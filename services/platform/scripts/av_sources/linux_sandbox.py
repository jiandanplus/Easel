"""Build an offline systemd job and check facts read inside its Linux process.

No function launches a command. Evidence from a synthetic caller is useful for
testing this validator, never proof that a real service passed these checks.
"""
from __future__ import annotations

import datetime
import errno
import json
import os
import re
import resource
import socket
import stat
import sys


class SandboxError(ValueError):
    """The requested configuration or observed isolation cannot be accepted."""

    def __init__(self, message="sandbox rejected", *, stage=None, gate=None, reason=None, facts=None):
        super().__init__(message)
        self.stage, self.gate, self.reason = stage, gate, reason
        self.facts = {} if facts is None else facts


_READ_STATES = frozenset(("ABSENT", "DENIED", "EMPTY", "UNLIMITED", "INTEGER", "INVALID"))
_TARGETS = {"/": "ROOT", "/code": "CODE", "/inputs": "INPUTS", "/work": "WORK", "/sys/fs/cgroup": "CGROUP"}
_MOUNT_MODEL_CODES = ("invalid_input", "size_limit", "record_limit", "malformed_record", "invalid_id",
                      "duplicate_id", "invalid_path", "invalid_flags", "invalid_graph", "ambiguous_stack",
                      "invalid_root", "invalid_observed", "observed_mismatch")
_WRITABLE_CLASSES = ("WORK", "TMP", "VAR_TMP", "DEV", "PROC", "CODE", "INPUTS", "USR", "SYS", "RUN", "HOME", "ROOT_HOME", "VAR_OTHER", "OTHER")
_WRITABLE_SUMMARY_KEYS = ("total", "allowed", "forbidden") + _WRITABLE_CLASSES
_DIAGNOSTIC_FIELDS = {
    "expected": {}, "platform": {},
    "status": {"field": ("NoNewPrivs", "CapEff", "CapPrm", "CapBnd"), "state": _READ_STATES, "value": int},
    "root_identity": {"field": ("root_dev", "root_ino", "uid", "euid"), "observed": int, "expected": int},
    "network_namespace": {"observed": int, "expected": int, "state": _READ_STATES},
    "cgroup_path": {"state": _READ_STATES},
    "cgroup_limits": {"field": ("memory.max", "memory.swap.max", "pids.max", "cpu.max"), "state": _READ_STATES, "value": int, "value2": int},
    "rlimits": {"field": ("AS", "CPU", "CORE"), "state": _READ_STATES, "soft": int, "hard": int},
    "mountinfo": {"target": tuple(_TARGETS.values()) + ("OTHER",), "count": "count", "state": _READ_STATES,
                  "check": ("fd_open", "fd_type", "fdinfo", "snapshot", "model"), "model_code": _MOUNT_MODEL_CODES},
    "mounts": {"target": tuple(_TARGETS.values()) + ("OTHER",), "read_only": bool, "own_mount": bool, "writable_count": "count",
               "writable_summary": "writable_summary"},
    "work": {"field": ("uid", "mode", "is_directory", "writable"), "value": "scalar"},
    "inet_socket": {"errno": "errno", "denied": bool},
    "proc1_root": {"errno": "errno", "denied": bool},
    "evidence": {"section": ("rlimits", "cgroup", "mounts", "work")},
}
_DIAGNOSTIC_REASONS = frozenset(("read_failed", "missing", "invalid", "mismatch", "duplicate", "unsupported", "not_denied", "unexpected"))


def sandbox_failure_diagnostic(error) -> dict | None:
    """Export only an exact, bounded allowlisted structure; never exception text."""
    if type(error) is not SandboxError:
        return None
    try:
        values = vars(error)
        if (type(values) is not dict or len(values) != 4 or
                any(type(key) is not str or key not in ("stage", "gate", "reason", "facts") for key in values)):
            return None
        stage, gate, reason, facts = (values[name] for name in ("stage", "gate", "reason", "facts"))
        if (type(stage) is not str or stage not in ("kernel_collect", "kernel_validate")
                or type(gate) is not str or gate not in _DIAGNOSTIC_FIELDS
                or type(reason) is not str or reason not in _DIAGNOSTIC_REASONS or type(facts) is not dict):
            return None
        rules = _DIAGNOSTIC_FIELDS[gate]
        if len(facts) > len(rules): return None
        safe = {}
        for key, value in facts.items():
            if type(key) is not str or key not in rules: return None
            rule = rules[key]
            if rule is int:
                valid = type(value) is int and 0 <= value <= 2**63 - 1
            elif rule is bool:
                valid = type(value) is bool
            elif rule == "writable_summary":
                target = facts.get("target")
                if stage != "kernel_validate" or gate != "mounts" or type(target) is not str or target != "ROOT": return None
                value = _safe_writable_summary(value, facts.get("writable_count"))
                valid = value is not None
            elif rule in ("count", "errno", "scalar"):
                valid = ((rule == "scalar" and type(value) is bool) or
                         type(value) is int and 0 <= value <= {"count": 65535, "errno": 4095, "scalar": 2**63 - 1}[rule])
            else:
                valid = type(value) is str and len(value) <= 32 and value in rule
            if not valid: return None
            safe[key] = value
        result = {"version": 1, "stage": stage, "gate": gate, "reason": reason, "facts": safe}
        return result if len(json.dumps(result, ensure_ascii=True, separators=(",", ":")).encode("ascii")) <= 3072 else None
    except Exception:
        # Diagnostics are optional. Even an encoder failure must only omit
        # them, never escape as a replacement error or relax the failed gate.
        return None


def _safe_writable_summary(value, total):
    """Validate and detach the entire fixed nested count schema, or omit it."""
    if (type(value) is not dict or len(value) != len(_WRITABLE_SUMMARY_KEYS) or
            any(type(key) is not str or key not in _WRITABLE_SUMMARY_KEYS for key in value) or
            any(type(item) is not int or not 0 <= item <= 65535 for item in value.values()) or
            type(total) is not int or not 0 <= total <= 65535):
        return None
    if (value["total"] != total or value["total"] != sum(value[key] for key in _WRITABLE_CLASSES) or
            value["allowed"] != sum(value[key] for key in _WRITABLE_CLASSES[:5]) or
            value["forbidden"] != sum(value[key] for key in _WRITABLE_CLASSES[5:]) or
            value["total"] != value["allowed"] + value["forbidden"]):
        return None
    return {key: value[key] for key in _WRITABLE_SUMMARY_KEYS}


def _root_writable_summary(children):
    """Classify validated inventory entries without exporting or merging paths."""
    if (type(children) is not list or len(children) > 65535 or
            any(not _absolute_path(child) for child in children)):
        return None
    counts = dict.fromkeys(_WRITABLE_CLASSES, 0)
    roots = (("/work", "WORK"), ("/tmp", "TMP"), ("/var/tmp", "VAR_TMP"), ("/dev", "DEV"), ("/proc", "PROC"),
             ("/code", "CODE"), ("/inputs", "INPUTS"), ("/usr", "USR"), ("/sys", "SYS"), ("/run", "RUN"),
             ("/home", "HOME"), ("/root", "ROOT_HOME"), ("/var", "VAR_OTHER"))
    for child in children:
        category = next((label for root, label in roots if _beneath(child, root)), "OTHER")
        counts[category] += 1
    allowed = sum(counts[key] for key in _WRITABLE_CLASSES[:5])
    return {"total": len(children), "allowed": allowed, "forbidden": len(children) - allowed, **counts}


def _tag_failure(error, stage, gate, reason, facts):
    # Preserve the first structured failure, including an invalid structure
    # that the exporter must reject rather than silently replacing it.
    if error.stage is None:
        error.stage, error.gate, error.reason, error.facts = stage, gate, reason, facts
    return error


def _read_state(value):
    if value is None: return "ABSENT"
    if type(value) is int:
        return "UNLIMITED" if value == -1 else "INTEGER" if 0 <= value <= 2**63 - 1 else "INVALID"
    if type(value) is str and len(value) <= 128:
        text = value.strip()
        if not text: return "EMPTY"
        if any(part in ("max", "-1") for part in text.split()): return "UNLIMITED"
        if re.fullmatch(r"[0-9]+", text) and len(text) <= 19: return "INTEGER"
    return "INVALID"


def _number_facts(value, key="value"):
    return {key: value} if type(value) is int and 0 <= value <= 2**63 - 1 else {}


_STAGE = re.compile(r"/root/easel-av-source-run-(\d{8})-([0-9a-f]{16})", re.ASCII)
_UNIT = re.compile(r"easel-av-src-([0-9a-f]{16})\.service", re.ASCII)
_INPUTS = "/root/easel-av-rsa-orig-source-20261028"
_MEMORY = 512 * 1024 * 1024
_PATHS = ("/", "/code", "/inputs", "/work", "/sys/fs/cgroup")


def _integer(value, name, *, minimum=0):
    if type(value) is not int or not minimum <= value <= 2**63 - 1:
        raise SandboxError(f"invalid integer: {name}")
    return value


def _expected(unit_name, root_dev, root_ino, host_netns_ino):
    if type(unit_name) is not str or not _UNIT.fullmatch(unit_name):
        raise SandboxError("invalid unit name")
    _integer(root_dev, "root_dev")
    _integer(root_ino, "root_ino", minimum=1)
    _integer(host_netns_ino, "host_netns_ino", minimum=1)


def build_command(stage, unit_name, *, root_dev, root_ino, host_netns_ino) -> list[str]:
    """Return an argv only; stage layout and inode pins are operator inputs."""
    _expected(unit_name, root_dev, root_ino, host_netns_ino)
    match = _STAGE.fullmatch(stage) if type(stage) is str else None
    if match is None or _UNIT.fullmatch(unit_name).group(1) != match.group(2):
        raise SandboxError("stage and unit must have the same fixed identity")
    try:
        datetime.datetime.strptime(match.group(1), "%Y%m%d")
    except ValueError as exc:
        raise SandboxError("invalid stage date") from exc
    readonly = ["/usr/bin:/usr/bin", "/usr/lib:/usr/lib", "-/usr/lib64:/usr/lib64",
                stage + "/code:/code", "/sys/fs/cgroup:/sys/fs/cgroup",
                _INPUTS + "/ffmpeg_9.0.2.orig.tar.xz:/inputs/archive.xz",
                _INPUTS + "/ffmpeg_9.0.2.orig.tar.xz.asc:/inputs/archive.asc",
                _INPUTS + "/official-rsa-public-key.asc:/inputs/release-key.asc"]
    properties = {
        "User": "root", "Group": "root", "Slice": "system.slice",
        "RootDirectory": stage + "/jail", "WorkingDirectory": "/work",
        "MountAPIVFS": "yes", "BindReadOnlyPaths": " ".join(readonly),
        "BindPaths": stage + "/work:/work", "ProtectSystem": "strict",
        "ReadWritePaths": "+/work", "ProtectHome": "yes", "PrivateNetwork": "yes",
        "PrivateTmp": "yes", "PrivateDevices": "yes", "ProtectKernelTunables": "yes",
        "ProtectKernelModules": "yes", "ProtectKernelLogs": "yes",
        "ProtectControlGroups": "yes", "ProtectClock": "yes",
        "CapabilityBoundingSet": "", "AmbientCapabilities": "", "NoNewPrivileges": "yes",
        "RestrictAddressFamilies": "AF_UNIX", "RestrictSUIDSGID": "yes",
        "RestrictRealtime": "yes", "LockPersonality": "yes", "UMask": "0077",
        "SystemCallFilter": "~@mount @reboot @swap @module @raw-io",
        "MemoryMax": str(_MEMORY), "MemorySwapMax": "0", "TasksMax": "16",
        "CPUQuota": "200%", "CPUQuotaPeriodSec": "100ms", "LimitAS": str(_MEMORY),
        "LimitCPU": "30", "LimitCORE": "0", "RuntimeMaxSec": "120s",
        "KillMode": "control-group", "SendSIGKILL": "yes", "TimeoutStopSec": "5s",
    }
    launcher = "import sys,runpy;sys.path.insert(0,'/code');runpy.run_module('av_sources.archive_worker',run_name='__main__')"
    return ["systemd-run", "--wait", "--pipe", "--collect", "--service-type=exec",
            "--unit=" + unit_name, *[f"--property={key}={value}" for key, value in properties.items()],
            "--", "/usr/bin/python3", "-I", "-B", "-c", launcher,
            "--expected-unit-name", unit_name, "--expected-root-dev", str(root_dev),
            "--expected-root-ino", str(root_ino), "--expected-host-netns-ino", str(host_netns_ino)]


def _read_text(path):
    # Kernel pseudo-files only, with a bound even for an unexpected mount layout.
    with open(path, "r", encoding="ascii", errors="strict") as stream:
        data = stream.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise SandboxError("kernel evidence exceeded size bound")
    return data


def _mount_decode(value):
    return re.sub(r"\\(040|011|012|134)", lambda m: chr(int(m.group(1), 8)), value)


def _beneath(path, parent):
    return parent == "/" or path == parent or path.startswith(parent + "/")


def _absolute_path(value):
    return (type(value) is str and value.startswith("/") and
            not re.search(r"[\x00-\x20\x7f]", value) and
            (value == "/" or all(part not in ("", ".", "..") for part in value[1:].split("/"))))


def _mount_facts(raw):
    mounts = {}
    for count, line in enumerate(raw.splitlines(), 1):
        fields = line.split()
        try:
            split = fields.index("-")
            if split < 6 or len(fields) != split + 4:
                raise ValueError("malformed mountinfo")
            point = _mount_decode(fields[4])
            if not _absolute_path(point):
                raise ValueError("ambiguous mount point")
            if point in mounts:
                raise SandboxError("malformed or ambiguous mountinfo", stage="kernel_collect", gate="mountinfo",
                                   reason="duplicate", facts={"target": _TARGETS.get(point, "OTHER"), "count": count})
            opts = fields[5].split(",")
            super_opts = fields[split + 3].split(",")
            if not ({"ro", "rw"} & set(opts)):
                raise ValueError("missing mount access flags")
            if any({"ro", "rw"} <= set(options) for options in (opts, super_opts)):
                raise ValueError("conflicting mount access flags")
            mounts[point] = {"mount_point": point, "read_only": "ro" in opts or "ro" in super_opts,
                             "fs_type": fields[split + 1]}
        except SandboxError:
            raise
        except (ValueError, IndexError) as exc:
            raise SandboxError("malformed or ambiguous mountinfo", stage="kernel_collect", gate="mountinfo",
                               reason="invalid", facts={"state": "INVALID"}) from exc
    if "/" not in mounts:
        raise SandboxError("root mount missing", stage="kernel_collect", gate="mountinfo", reason="missing", facts={"target": "ROOT"})
    result = {}
    for path in _PATHS:
        nearest = max((point for point in mounts if _beneath(path, point)), key=len)
        result[path] = dict(mounts[nearest])
        result[path]["writable_descendants"] = sorted(
            point for point, fact in mounts.items() if point != path and
            _beneath(point, path) and not fact["read_only"])
    return result


def _work_fact():
    fd = os.open("/work", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        current = os.fstat(fd)
        return {"uid": current.st_uid, "mode": stat.S_IMODE(current.st_mode),
                "is_directory": stat.S_ISDIR(current.st_mode),
                "writable": os.access("/work", os.W_OK | os.X_OK, effective_ids=True)}
    finally:
        os.close(fd)


def _read_fd_mount_id(fd):
    """Read metadata for a descriptor opened by this collector, never its data."""
    if type(fd) is not int or not 0 <= fd < 2**31:
        raise ValueError("invalid owned descriptor")
    with open("/proc/self/fdinfo/" + str(fd), "r", encoding="ascii", errors="strict") as stream:
        raw = stream.read(4097)
    if len(raw) > 4096 or any(ord(char) < 32 and char not in "\n\t" for char in raw):
        raise ValueError("invalid descriptor metadata")
    values = []
    for line in raw.split("\n"):
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        if name.strip() == "mnt_id":
            if name != "mnt_id":
                raise ValueError("ambiguous descriptor mount id")
            values.append(value.strip(" \t"))
    if len(values) != 1 or not re.fullmatch(r"[1-9][0-9]{0,18}", values[0], re.ASCII):
        raise ValueError("missing or invalid descriptor mount id")
    value = int(values[0])
    if value > 2**63 - 1:
        raise ValueError("descriptor mount id exceeds bound")
    return value


def _directory_mount_ids():
    """Observe only the five fixed directories using Linux O_PATH handles."""
    if not hasattr(os, "O_PATH"):
        raise SandboxError("directory mount observation unsupported", stage="kernel_collect",
                           gate="mountinfo", reason="unsupported", facts={"check": "fd_open"})
    result = {}
    for path in _PATHS:
        fd = None
        check = "fd_open"
        try:
            fd = os.open(path, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            check = "fd_type"
            if not stat.S_ISDIR(os.fstat(fd).st_mode):
                raise ValueError("fixed mount target is not a directory")
            check = "fdinfo"
            result[path] = _read_fd_mount_id(fd)
        except (OSError, ValueError) as exc:
            state = "INVALID"
            if isinstance(exc, OSError):
                state = "ABSENT" if exc.errno == errno.ENOENT else "DENIED" if exc.errno in (errno.EACCES, errno.EPERM) else "INVALID"
            raise SandboxError("fixed directory mount observation failed", stage="kernel_collect", gate="mountinfo",
                               reason="read_failed" if isinstance(exc, OSError) else "invalid",
                               facts={"target": _TARGETS[path], "check": check, "state": state}) from exc
        finally:
            if fd is not None:
                os.close(fd)
    return result


def _verified_mount_facts():
    """Bind the pure visible graph to fixed FDs within a stable mount snapshot."""
    from .mount_table import MountTableError, resolve_mountinfo

    before = _read_text("/proc/self/mountinfo")
    observed = _directory_mount_ids()
    after = _read_text("/proc/self/mountinfo")
    if before != after:
        raise SandboxError("mount snapshot changed", stage="kernel_collect", gate="mountinfo",
                           reason="mismatch", facts={"check": "snapshot"})
    try:
        return resolve_mountinfo(before, observed)
    except MountTableError as exc:
        code = exc.code if type(exc.code) is str and exc.code in _MOUNT_MODEL_CODES else "invalid_input"
        raise SandboxError("visible mount graph rejected", stage="kernel_collect", gate="mountinfo",
                           reason="mismatch" if code == "observed_mismatch" else "duplicate" if code == "duplicate_id" else "invalid",
                           facts={"check": "model", "model_code": code}) from exc


def _inet_probe():
    # Create/close only. Never connect, bind, send, resolve names, or emit data.
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    except OSError as exc:
        return exc.errno
    probe.close()
    return None


def _proc_probe():
    # Follow this proc symlink deliberately: O_NOFOLLOW would give a false pass.
    try:
        fd = os.open("/proc/1/root", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError as exc:
        return exc.errno
    os.close(fd)
    return None


def _cgroup_path(raw):
    lines = raw.splitlines()
    if len(lines) != 1 or not lines[0].startswith("0::/"):
        raise SandboxError("a single unified cgroup v2 path is required")
    path = lines[0][3:]
    if path == "/" or any(part in ("", ".", "..") for part in path[1:].split("/")):
        raise SandboxError("invalid cgroup path")
    if re.search(r"[\x00-\x20\x7f]", path):
        raise SandboxError("invalid cgroup path")
    return path


def collect_runtime_evidence() -> dict:
    """Read this Linux process's facts; never trust environment success flags."""
    if sys.platform != "linux":
        raise SandboxError("Linux runtime evidence is required", stage="kernel_collect", gate="platform", reason="unsupported")
    gate, facts = "status", {}
    try:
        status = {}
        for line in _read_text("/proc/self/status").splitlines():
            if ":" in line:
                name, value = line.split(":", 1)
                if name in ("NoNewPrivs", "CapEff", "CapPrm", "CapBnd"):
                    facts = {"field": name, "state": _read_state(value)}
                    if name in status:
                        raise SandboxError("duplicate kernel status", stage="kernel_collect", gate=gate, reason="duplicate", facts={"field": name})
                    status[name] = int(value.strip(), 10 if name == "NoNewPrivs" else 16)
        gate, facts = "root_identity", {}
        root = os.stat("/")
        gate, facts = "network_namespace", {}
        net = os.stat("/proc/self/ns/net")
        gate, facts = "cgroup_path", {}
        path = _cgroup_path(_read_text("/proc/self/cgroup"))
        cgroup = {}
        for name in ("memory.max", "memory.swap.max", "pids.max", "cpu.max"):
            gate, facts = "cgroup_limits", {"field": name}
            content = _read_text("/sys/fs/cgroup" + path + "/" + name).strip()
            facts = {"field": name, "state": _read_state(content)}
            cgroup[name] = [int(part) for part in content.split()] if name == "cpu.max" else int(content)
        gate, facts = "root_identity", {}
        uid, euid = os.getuid(), os.geteuid()
        limits = {}
        for name, kind in (("AS", resource.RLIMIT_AS), ("CPU", resource.RLIMIT_CPU), ("CORE", resource.RLIMIT_CORE)):
            gate, facts = "rlimits", {"field": name}
            limits[name] = list(resource.getrlimit(kind))
        gate, facts = "mountinfo", {}
        mounts = _verified_mount_facts()
        gate, facts = "work", {}
        work = _work_fact()
        gate, facts = "inet_socket", {}
        inet = _inet_probe()
        gate, facts = "proc1_root", {}
        proc = _proc_probe()
        return {"platform": sys.platform, "uid": uid, "euid": euid,
                "root_dev": root.st_dev, "root_ino": root.st_ino, "netns_ino": net.st_ino,
                **status, "rlimits": limits,
                "cgroup_path": path, "cgroup": cgroup,
                "mounts": mounts, "work": work,
                "inet_socket_errno": inet, "proc1_root_errno": proc}
    except SandboxError as exc:
        raise _tag_failure(exc, "kernel_collect", gate, "invalid", facts)
    except (OSError, ValueError, KeyError, UnicodeError) as exc:
        if isinstance(exc, OSError):
            if "state" in _DIAGNOSTIC_FIELDS[gate]:
                facts = {**facts, "state": "ABSENT" if exc.errno == errno.ENOENT else "DENIED" if exc.errno in (errno.EPERM, errno.EACCES) else "INVALID"}
        raise SandboxError("cannot collect complete Linux kernel evidence", stage="kernel_collect", gate=gate,
                           reason="read_failed" if isinstance(exc, OSError) else "invalid", facts=facts) from exc


def _require_equal(value, expected, name):
    if type(value) is not type(expected) or value != expected:
        raise SandboxError(f"runtime mismatch: {name}")


def _require_pair(value, expected, name):
    if type(value) not in (list, tuple) or len(value) != 2:
        raise SandboxError(f"invalid limit pair: {name}")
    for index, expected_value in enumerate(expected):
        _require_equal(value[index], expected_value, name)


def validate_runtime_evidence(evidence, *, unit_name, root_dev, root_ino, host_netns_ino) -> dict:
    """Fail closed on every missing, mismatched or mistyped required fact."""
    try:
        _expected(unit_name, root_dev, root_ino, host_netns_ino)
    except SandboxError as exc:
        raise _tag_failure(exc, "kernel_validate", "expected", "invalid", {})
    if type(evidence) is not dict:
        raise SandboxError("evidence must be a dictionary", stage="kernel_validate", gate="evidence", reason="invalid")
    gate, facts = "evidence", {}
    try:
        for name, expected in {"platform": "linux", "uid": 0, "euid": 0, "root_dev": root_dev,
                               "root_ino": root_ino, "NoNewPrivs": 1,
                               "CapEff": 0, "CapPrm": 0, "CapBnd": 0}.items():
            value = evidence.get(name)
            if name == "platform":
                gate, facts = "platform", {}
            elif name in ("uid", "euid", "root_dev", "root_ino"):
                gate, facts = "root_identity", {"field": name, **_number_facts(value, "observed"), **_number_facts(expected, "expected")}
            else:
                gate, facts = "status", {"field": name, "state": _read_state(value), **_number_facts(value)}
            _require_equal(evidence[name], expected, name)
        for section in ("rlimits", "cgroup", "mounts", "work"):
            gate, facts = "evidence", {"section": section}
            if type(evidence[section]) is not dict:
                raise SandboxError("invalid evidence section: " + section)
        gate, facts = "network_namespace", {"state": _read_state(evidence.get("netns_ino")),
            **_number_facts(evidence.get("netns_ino"), "observed"), **_number_facts(host_netns_ino, "expected")}
        net = _integer(evidence["netns_ino"], "netns_ino", minimum=1)
        if net == host_netns_ino:
            raise SandboxError("network namespace is still the host namespace")
        gate, facts = "cgroup_path", {"state": _read_state(evidence.get("cgroup_path"))}
        _require_equal(evidence["cgroup_path"], "/system.slice/" + unit_name, "cgroup_path")
        for name, pair in {"AS": [_MEMORY, _MEMORY], "CPU": [30, 30], "CORE": [0, 0]}.items():
            gate, facts = "rlimits", {"field": name, "state": "ABSENT" if name not in evidence["rlimits"] else "INVALID"}
            value = evidence["rlimits"].get(name)
            if type(value) in (list, tuple) and len(value) == 2:
                states = [_read_state(item) for item in value]
                facts.update(state="UNLIMITED" if "UNLIMITED" in states else "INTEGER" if states == ["INTEGER", "INTEGER"] else "INVALID")
                facts.update(_number_facts(value[0], "soft"), **_number_facts(value[1], "hard"))
            _require_pair(evidence["rlimits"][name], pair, name)
        for name, expected in {"memory.max": _MEMORY, "memory.swap.max": 0, "pids.max": 16}.items():
            value = evidence["cgroup"].get(name)
            gate, facts = "cgroup_limits", {"field": name, "state": _read_state(value), **_number_facts(value)}
            _require_equal(evidence["cgroup"][name], expected, name)
        gate, facts = "cgroup_limits", {"field": "cpu.max", "state": "ABSENT" if "cpu.max" not in evidence["cgroup"] else "INVALID"}
        value = evidence["cgroup"].get("cpu.max")
        if type(value) in (list, tuple) and len(value) == 2:
            states = [_read_state(item) for item in value]
            facts.update(state="UNLIMITED" if "UNLIMITED" in states else "INTEGER" if states == ["INTEGER", "INTEGER"] else "INVALID")
            facts.update(_number_facts(value[0]), **_number_facts(value[1], "value2"))
        _require_pair(evidence["cgroup"]["cpu.max"], [200000, 100000], "cpu.max")
        for path in _PATHS:
            gate, facts = "mounts", {"target": _TARGETS[path]}
            mount = evidence["mounts"][path]
            if type(mount) is not dict:
                raise SandboxError("invalid mount evidence")
            if type(mount.get("read_only")) is bool: facts["read_only"] = mount["read_only"]
            _require_equal(mount["read_only"], path != "/work", path + " mount mode")
            point = mount["mount_point"]
            facts["own_mount"] = type(point) is str and point == path
            if not _absolute_path(point) or not _beneath(path, point):
                raise SandboxError("invalid effective mount path")
            if path in ("/code", "/work", "/sys/fs/cgroup") and point != path:
                raise SandboxError("required bind mount missing: " + path)
            if type(mount["fs_type"]) is not str or not mount["fs_type"]:
                raise SandboxError("mount filesystem type missing")
            children = mount["writable_descendants"]
            if type(children) is list: facts["writable_count"] = len(children)
            if type(children) is not list or any(not _absolute_path(child) for child in children):
                raise SandboxError("invalid writable mount inventory")
            if path == "/" and any(not any(_beneath(child, allowed) for allowed in
                    ("/work", "/tmp", "/var/tmp", "/dev", "/proc")) for child in children):
                # Metadata only, after the original guard has already decided
                # to reject. A diagnostic failure cannot replace that decision.
                try:
                    summary = _root_writable_summary(children)
                    if summary is not None: facts["writable_summary"] = summary
                except Exception:
                    pass
                raise SandboxError("unexpected writable mount outside work and private API filesystems")
            if path != "/":
                _require_equal(mount["writable_descendants"], [], path + " writable child mounts")
        gate, facts = "mounts", {"target": "CGROUP"}
        _require_equal(evidence["mounts"]["/sys/fs/cgroup"]["fs_type"], "cgroup2", "cgroup filesystem")
        for name, expected in {"uid": 0, "mode": 0o700, "is_directory": True, "writable": True}.items():
            value = evidence["work"].get(name)
            gate, facts = "work", {"field": name, **_number_facts(value)}
            if type(value) is bool: facts["value"] = value
            _require_equal(evidence["work"][name], expected, "/work " + name)
        for name, allowed in (("inet_socket_errno", (errno.EPERM, errno.EACCES, errno.EAFNOSUPPORT)),
                              ("proc1_root_errno", (errno.EPERM, errno.EACCES))):
            value = evidence.get(name)
            gate, facts = "inet_socket" if name == "inet_socket_errno" else "proc1_root", {
                "denied": type(value) is int and value in allowed, **_number_facts(value, "errno")}
            value = evidence[name]
            if type(value) is not int or value not in allowed:
                raise SandboxError("permission probe was not denied: " + name)
        # Select known fields, so unrelated/unserializable caller keys cannot be
        # mistaken for evidence. Return a detached snapshot.
        names = ("platform", "uid", "euid", "root_dev", "root_ino", "netns_ino", "NoNewPrivs",
                 "CapEff", "CapPrm", "CapBnd", "cgroup_path",
                 "inet_socket_errno", "proc1_root_errno")
        result = {name: evidence[name] for name in names}
        # Pair tuples supplied to the pure validator become JSON arrays too.
        result["rlimits"] = {name: list(evidence["rlimits"][name]) for name in ("AS", "CPU", "CORE")}
        result["cgroup"] = {name: evidence["cgroup"][name] for name in ("memory.max", "memory.swap.max", "pids.max")}
        result["cgroup"]["cpu.max"] = list(evidence["cgroup"]["cpu.max"])
        result["work"] = {name: evidence["work"][name] for name in ("uid", "mode", "is_directory", "writable")}
        result["mounts"] = {path: {name: evidence["mounts"][path][name] for name in
            ("mount_point", "read_only", "fs_type")} for path in _PATHS}
        for path in _PATHS:
            result["mounts"][path]["writable_descendants"] = list(evidence["mounts"][path]["writable_descendants"])
        return result
    except SandboxError as exc:
        raise _tag_failure(exc, "kernel_validate", gate, "mismatch", facts)
    except (KeyError, TypeError, IndexError) as exc:
        raise SandboxError("missing or invalid runtime evidence", stage="kernel_validate", gate=gate,
                           reason="missing" if type(exc) is KeyError else "invalid", facts=facts) from exc
