"""Offline synthetic tests; these do not attest a running Linux sandbox."""
import errno
import importlib
import importlib.util
import json

import pytest

MODULE = "services.platform.scripts.av_sources.linux_sandbox"
STAGE = "/root/easel-av-source-run-20261029-0123456789abcdef"
UNIT = "easel-av-src-0123456789abcdef.service"
EXPECTED = dict(unit_name=UNIT, root_dev=2049, root_ino=1234, host_netns_ino=900)


def api():
    assert importlib.util.find_spec(MODULE) is not None, "Linux sandbox command builder is missing"
    return importlib.import_module(MODULE)


def evidence():
    return dict(platform="linux", uid=0, euid=0, root_dev=2049, root_ino=1234,
                netns_ino=901, NoNewPrivs=1, CapEff=0, CapPrm=0, CapBnd=0,
                rlimits={"AS": [536870912, 536870912], "CPU": [30, 30], "CORE": [0, 0]},
                cgroup_path="/system.slice/" + UNIT,
                cgroup={"memory.max": 536870912, "memory.swap.max": 0,
                        "pids.max": 16, "cpu.max": [200000, 100000]},
                mounts={p: {"mount_point": p, "read_only": p != "/work",
                            "writable_descendants": [], "fs_type": "cgroup2" if p == "/sys/fs/cgroup" else "ext4"}
                        for p in ["/", "/code", "/inputs", "/work", "/sys/fs/cgroup"]},
                work={"uid": 0, "mode": 0o700, "is_directory": True, "writable": True},
                inet_socket_errno=errno.EAFNOSUPPORT, proc1_root_errno=errno.EACCES)


def test_builder_generates_isolated_service_without_execution():
    command = api().build_command(STAGE, UNIT, root_dev=2049, root_ino=1234, host_netns_ino=900)
    assert isinstance(command, list) and all(type(s) is str for s in command)
    assert command[:5] == ["systemd-run", "--wait", "--pipe", "--collect", "--service-type=exec"]
    props = {a.split("=", 2)[1]: a.split("=", 2)[2] for a in command if a.startswith("--property=")}
    assert props["RootDirectory"] == STAGE + "/jail"
    assert props["WorkingDirectory"] == "/work"
    assert props["BindPaths"] == STAGE + "/work:/work"
    assert props["ReadWritePaths"] == "+/work"
    binds = props["BindReadOnlyPaths"].split()
    assert STAGE + "/code:/code" in binds
    for source, dest in [("ffmpeg_9.0.2.orig.tar.xz", "archive.xz"),
                         ("ffmpeg_9.0.2.orig.tar.xz.asc", "archive.asc"),
                         ("official-rsa-public-key.asc", "release-key.asc")]:
        assert "/root/easel-av-rsa-orig-source-20261028/" + source + ":/inputs/" + dest in binds
    assert "/usr/bin:/usr/bin" in binds and "/usr/lib:/usr/lib" in binds
    assert "-/usr/lib64:/usr/lib64" in binds
    assert "/sys/fs/cgroup:/sys/fs/cgroup" in binds
    for key, value in {"ProtectSystem": "strict", "ProtectHome": "yes", "PrivateNetwork": "yes",
                       "MountAPIVFS": "yes", "PrivateTmp": "yes", "PrivateDevices": "yes",
                       "ProtectKernelTunables": "yes", "ProtectKernelModules": "yes",
                       "ProtectKernelLogs": "yes", "ProtectControlGroups": "yes", "ProtectClock": "yes",
                       "CapabilityBoundingSet": "", "AmbientCapabilities": "", "NoNewPrivileges": "yes",
                       "RestrictAddressFamilies": "AF_UNIX", "RestrictSUIDSGID": "yes",
                       "RestrictRealtime": "yes", "LockPersonality": "yes", "UMask": "0077",
                       "MemoryMax": "536870912", "MemorySwapMax": "0", "TasksMax": "16",
                       "CPUQuota": "200%", "LimitAS": "536870912", "LimitCPU": "30",
                       "LimitCORE": "0", "RuntimeMaxSec": "120s"}.items():
        assert props[key] == value
    assert all(group in props["SystemCallFilter"].split() for group in ["~@mount", "@reboot", "@swap"])
    assert "PrivatePIDs" not in props
    py = command.index("/usr/bin/python3")
    assert command[py + 1:py + 4] == ["-I", "-B", "-c"]
    assert "'/code'" in command[py + 4] and "av_sources.archive_worker" in command[py + 4]
    assert command[py + 5:] == ["--expected-unit-name", UNIT, "--expected-root-dev", "2049",
                                "--expected-root-ino", "1234", "--expected-host-netns-ino", "900"]


@pytest.mark.parametrize("stage,unit", [(STAGE + "/", UNIT), (STAGE + "\n", UNIT),
    (STAGE.replace("20261029", "20260230"), UNIT), (STAGE + "/../x", UNIT),
    (STAGE.replace("root", "home"), UNIT), (STAGE, UNIT.replace("0123", "1123")),
    (STAGE, UNIT + "\n"), (STAGE, "--help"), (None, UNIT), (STAGE, True)])
def test_builder_rejects_unsafe_identity(stage, unit):
    with pytest.raises(api().SandboxError):
        api().build_command(stage, unit, root_dev=2049, root_ino=1234, host_netns_ino=900)


@pytest.mark.parametrize("name,value", [("root_dev", True), ("root_dev", -1), ("root_ino", 0),
    ("host_netns_ino", "900"), ("host_netns_ino", 0), ("root_ino", 1.0), ("root_dev", 2**64)])
def test_builder_rejects_bad_integer_types(name, value):
    args = dict(root_dev=2049, root_ino=1234, host_netns_ino=900); args[name] = value
    with pytest.raises(api().SandboxError): api().build_command(STAGE, UNIT, **args)


def test_synthetic_evidence_summary_is_serializable_and_detached():
    raw = evidence(); summary = api().validate_runtime_evidence(raw, **EXPECTED)
    assert summary["cgroup_path"] == "/system.slice/" + UNIT
    assert summary["root_ino"] == 1234
    assert json.loads(json.dumps(summary)) == summary
    summary["rlimits"]["AS"][0] = 1
    assert raw["rlimits"]["AS"][0] == 536870912


@pytest.mark.parametrize("field", list(evidence()))
def test_missing_top_level_fact_rejected(field):
    raw = evidence(); del raw[field]
    with pytest.raises(api().SandboxError): api().validate_runtime_evidence(raw, **EXPECTED)


BAD_FACTS = [("platform", "darwin"), ("uid", 1000), ("euid", True), ("root_dev", 1),
             ("root_ino", 2), ("netns_ino", 900), ("netns_ino", True), ("NoNewPrivs", False),
             ("CapEff", 1), ("CapPrm", 1), ("CapBnd", 1), ("CapBnd", "0"),
             ("cgroup_path", "/wrong/" + UNIT), ("cgroup_path", "/system.slice/../" + UNIT),
             ("inet_socket_errno", None), ("inet_socket_errno", errno.EMFILE),
             ("proc1_root_errno", None), ("proc1_root_errno", errno.ENOENT)]


@pytest.mark.parametrize("field,value", BAD_FACTS)
def test_independent_bad_kernel_fact_rejected(field, value):
    raw = evidence(); raw[field] = value
    with pytest.raises(api().SandboxError): api().validate_runtime_evidence(raw, **EXPECTED)


@pytest.mark.parametrize("kind,key,value", [
    ("rlimits", "AS", [536870912, -1]), ("rlimits", "CPU", [31, 30]),
    ("rlimits", "CORE", [False, 0]), ("rlimits", "CPU", [30]),
    ("cgroup", "memory.max", "max"), ("cgroup", "memory.swap.max", 1),
    ("cgroup", "pids.max", 17), ("cgroup", "cpu.max", [200001, 100000]),
    ("cgroup", "cpu.max", [200000, True]), ("work", "mode", 0o755),
    ("work", "uid", 1000), ("work", "writable", False), ("work", "is_directory", False)])
def test_nested_limits_and_directory_facts_rejected(kind, key, value):
    raw = evidence(); raw[kind][key] = value
    with pytest.raises(api().SandboxError): api().validate_runtime_evidence(raw, **EXPECTED)


@pytest.mark.parametrize("path", ["/", "/code", "/inputs", "/work"])
def test_each_mount_must_have_expected_access(path):
    raw = evidence(); raw["mounts"][path]["read_only"] = path == "/work"
    with pytest.raises(api().SandboxError): api().validate_runtime_evidence(raw, **EXPECTED)
    raw = evidence(); del raw["mounts"][path]
    with pytest.raises(api().SandboxError): api().validate_runtime_evidence(raw, **EXPECTED)


def test_non_linux_collector_refuses_before_any_probe(monkeypatch):
    av = api(); monkeypatch.setattr(av.sys, "platform", "darwin")
    monkeypatch.setattr(av.os, "getuid", lambda: pytest.fail("must not probe on non Linux"))
    with pytest.raises(av.SandboxError, match="Linux"): av.collect_runtime_evidence()


def test_synthetic_mount_parser_resolves_inherited_readonly_and_writable_descendants():
    raw = ("1 0 8:1 / / ro,relatime - ext4 /dev/x rw\n"
           "2 1 8:1 /stage/code /code ro - ext4 /dev/x rw\n"
           "3 1 8:1 /stage/work /work rw - ext4 /dev/x rw\n"
           "4 1 0:1 / /sys/fs/cgroup ro - cgroup2 cgroup rw\n"
           "5 1 8:1 /input /inputs/archive.xz ro - ext4 /dev/x rw\n")
    facts = api()._mount_facts(raw)
    assert facts["/inputs"]["read_only"] is True
    assert facts["/inputs"]["mount_point"] == "/"
    assert facts["/work"]["read_only"] is False
    bad = raw + "6 2 8:1 /extra /code/extra rw - ext4 /dev/x rw\n"
    assert api()._mount_facts(bad)["/code"]["writable_descendants"] == ["/code/extra"]


@pytest.mark.parametrize("raw", ["", "nonsense", "1 0 8:1 / / rw - ext4", 
    "1 0 8:1 / / ro - ext4 /dev/x rw\n2 1 8:1 / / ro - ext4 /dev/x rw\n"])
def test_malformed_or_ambiguous_mountinfo_rejected(raw):
    with pytest.raises(api().SandboxError): api()._mount_facts(raw)


@pytest.mark.parametrize("opts,super_opts", [
    ("ro,rw", "rw"), ("rw,ro", "rw"),
    ("ro", "ro,rw"), ("ro", "rw,ro"),
])
def test_mountinfo_rejects_conflicting_access_flags_within_each_option_list(opts, super_opts):
    raw = f"1 0 8:1 / / {opts} - ext4 /dev/x {super_opts}\n"
    with pytest.raises(api().SandboxError): api()._mount_facts(raw)


def test_readonly_bind_on_writable_superblock_is_accepted():
    raw = ("1 0 8:1 / / ro - ext4 /dev/x rw\n"
           "2 1 8:1 /stage/code /code ro,relatime - ext4 /dev/x rw,errors=remount-ro\n")
    facts = api()._mount_facts(raw)
    assert facts["/code"]["mount_point"] == "/code"
    assert facts["/code"]["read_only"] is True


def test_writable_code_child_mount_rejected():
    raw = evidence(); raw["mounts"]["/code"]["writable_descendants"] = ["/code/leak"]
    with pytest.raises(api().SandboxError): api().validate_runtime_evidence(raw, **EXPECTED)


def test_synthetic_collector_reads_kernel_paths_and_only_creates_probe_handles(monkeypatch):
    from types import SimpleNamespace
    av = api(); monkeypatch.setattr(av.sys, "platform", "linux")
    texts = {"/proc/self/status": "NoNewPrivs:\t1\nCapEff:\t00000000\nCapPrm:\t00000000\nCapBnd:\t00000000\n",
             "/proc/self/cgroup": "0::/system.slice/" + UNIT + "\n",
             "/proc/self/mountinfo": "1 999 8:1 / / ro - ext4 /dev/x rw\n2 1 8:1 /code /code ro - ext4 /dev/x rw\n3 1 8:1 /work /work rw - ext4 /dev/x rw\n4 1 0:1 / /sys/fs/cgroup ro - cgroup2 cgroup rw\n"}
    for name, value in {"memory.max": "536870912", "memory.swap.max": "0", "pids.max": "16", "cpu.max": "200000 100000"}.items():
        texts["/sys/fs/cgroup/system.slice/" + UNIT + "/" + name] = value
    reads = []
    def read(path): reads.append(path); return texts[path]
    monkeypatch.setattr(av, "_read_text", read)
    monkeypatch.setattr(av, "_directory_mount_ids", lambda: {"/": 1, "/code": 2, "/inputs": 1, "/work": 3, "/sys/fs/cgroup": 4})
    monkeypatch.setattr(av.os, "getuid", lambda: 0)
    monkeypatch.setattr(av.os, "geteuid", lambda: 0)
    def stat(path):
        assert path in ["/", "/proc/self/ns/net"]
        return SimpleNamespace(st_dev=2049, st_ino=1234 if path == "/" else 901)
    monkeypatch.setattr(av.os, "stat", stat)
    monkeypatch.setattr(av, "_work_fact", lambda: evidence()["work"])
    values = {av.resource.RLIMIT_AS: (536870912, 536870912), av.resource.RLIMIT_CPU: (30, 30), av.resource.RLIMIT_CORE: (0, 0)}
    monkeypatch.setattr(av.resource, "getrlimit", lambda kind: values[kind])
    def socket(family, kind):
        assert family == av.socket.AF_INET and kind == av.socket.SOCK_STREAM
        raise OSError(errno.EAFNOSUPPORT, "synthetic seccomp denial")
    monkeypatch.setattr(av.socket, "socket", socket)
    def open_dir(path, flags):
        assert path == "/proc/1/root" and flags == av.os.O_RDONLY | av.os.O_DIRECTORY | av.os.O_CLOEXEC
        raise PermissionError(errno.EACCES, "synthetic proc denial")
    monkeypatch.setattr(av.os, "open", open_dir)
    result = av.collect_runtime_evidence()
    av.validate_runtime_evidence(result, **EXPECTED)
    assert set(reads) == set(texts)


def test_probe_success_closes_socket_and_directory_and_is_rejected(monkeypatch):
    av = api(); closed = []
    class Probe:
        def close(self): closed.append("socket")
    monkeypatch.setattr(av.socket, "socket", lambda *a: Probe())
    monkeypatch.setattr(av.os, "open", lambda *a: 42)
    monkeypatch.setattr(av.os, "close", lambda fd: closed.append(fd))
    assert av._inet_probe() is None
    assert av._proc_probe() is None
    assert closed == ["socket", 42]


@pytest.mark.parametrize("kind,key", [("rlimits", "AS"), ("rlimits", "CPU"), ("rlimits", "CORE"),
    ("cgroup", "memory.max"), ("cgroup", "memory.swap.max"), ("cgroup", "pids.max"), ("cgroup", "cpu.max"),
    ("work", "uid"), ("work", "mode"), ("work", "is_directory"), ("work", "writable")])
def test_missing_nested_required_fact_rejected(kind, key):
    raw = evidence(); del raw[kind][key]
    with pytest.raises(api().SandboxError): api().validate_runtime_evidence(raw, **EXPECTED)


@pytest.mark.parametrize("kind", ["rlimits", "cgroup", "work", "mounts"])
def test_nested_sections_must_be_plain_dictionaries(kind):
    raw = evidence(); raw[kind] = []
    with pytest.raises(api().SandboxError): api().validate_runtime_evidence(raw, **EXPECTED)


def test_summary_drops_unknown_nested_objects():
    raw = evidence()
    for kind in ("rlimits", "cgroup", "work", "mounts"):
        raw[kind]["untrusted_extra"] = object()
    raw["mounts"]["/code"]["untrusted_extra"] = object()
    summary = api().validate_runtime_evidence(raw, **EXPECTED)
    assert json.loads(json.dumps(summary)) == summary


@pytest.mark.parametrize("value", [True, "none", [object()], ["/../host"], ["/host-business"]])
def test_root_writable_mount_inventory_must_be_well_formed(value):
    raw = evidence(); raw["mounts"]["/"]["writable_descendants"] = value
    with pytest.raises(api().SandboxError): api().validate_runtime_evidence(raw, **EXPECTED)


@pytest.mark.parametrize("point", ["/code/..", "/code/", "/code\n", "/code//x"])
def test_mount_point_must_be_canonical(point):
    raw = evidence(); raw["mounts"]["/code"]["mount_point"] = point
    with pytest.raises(api().SandboxError): api().validate_runtime_evidence(raw, **EXPECTED)


@pytest.mark.parametrize("raw", ["0::/\n", "0::/../host\n", "0::/x//y\n", "0::/x\n1:cpu:/x\n", "0::/x y\n"])
def test_cgroup_paths_reject_nonunified_or_escaping_inputs(raw):
    with pytest.raises(api().SandboxError): api()._cgroup_path(raw)


def test_collector_wraps_kernel_read_failure(monkeypatch):
    av = api(); monkeypatch.setattr(av.sys, "platform", "linux")
    def denied(path): raise PermissionError(errno.EACCES, "synthetic")
    monkeypatch.setattr(av, "_read_text", denied)
    with pytest.raises(av.SandboxError, match="cannot collect"): av.collect_runtime_evidence()
