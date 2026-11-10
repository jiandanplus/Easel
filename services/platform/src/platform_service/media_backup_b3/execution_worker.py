"""Fixed Linux ATTEST endpoint. Private self-report is never host isolation proof.

No import-time discovery. Root must independently bind the exact worker image,
container, both network namespaces and resource configuration to original handles.
Only main() invokes native IO. Private helpers are implementation/test seams, not
configurable runners. No database work or resource creation occurs here.
"""
import errno
import hashlib
import json
import os
import platform
import re
import select
import stat
import sys
import time

from .contracts import B3Error
from . import execution_contract as contract
from .network_reader import observe_worker_network
from .network_profile import validate_network_profile
from .network_wire import encode_network_observation

_INTERPRETER = "/opt/easel/bin/python"
_BUILD = "/opt/easel/worker-build.json"
_ARTIFACT = "/opt/easel/artifact.whl"
_PG = "/usr/lib/postgresql/16/bin/pg_restore"
_INITIAL_NS = 60 * 10**9
_BUILD_LIMIT = 8 * 1024**2


def _require(ok):
    if not ok:
        raise B3Error("B3 worker rejected")


def _check(deadline):
    _require(time.monotonic_ns() < deadline)


def _wait(fd, writing, deadline):
    while True:
        _check(deadline)
        remaining = deadline-time.monotonic_ns()
        _require(remaining > 0)
        try:
            ready = select.select([] if writing else [fd], [fd] if writing else [], [], remaining/10**9)
        except InterruptedError:
            continue  # No delivered bytes; retain original offset and deadline.
        _check(deadline)
        _require(bool(ready[1] if writing else ready[0]))
        return


def _read_fd(fd, size, deadline):
    while True:
        _wait(fd, False, deadline)
        try:
            data = os.read(fd, size)
        except InterruptedError:
            continue
        _check(deadline)
        _require(type(data) is bytes and len(data) <= size)
        return data


def _read_exact(size, deadline):
    chunks = bytearray()
    while len(chunks) < size:
        data = _read_fd(0, min(16384, size-len(chunks)), deadline)
        _require(bool(data))
        chunks.extend(data)
    return bytes(chunks)


def _read_frame(deadline):
    header = _read_exact(4, deadline)
    size = int.from_bytes(header, "big")
    _require(0 < size <= 65536)
    payload = _read_exact(size, deadline)
    _require(_read_fd(0, 1, deadline) == b"")
    return header + payload


def _write_result(data, limit, deadline):
    _require(type(data) is bytes and len(data) <= limit+4 and len(data) >= 4
             and int.from_bytes(data[:4], "big") == len(data)-4 and len(data)-4 <= limit)
    _check(deadline)
    os.set_blocking(1, False)
    _check(deadline)
    _require(os.get_blocking(1) is False)
    _check(deadline)
    offset = 0
    while offset < len(data):
        _wait(1, True, deadline)
        _check(deadline)
        try:
            n = os.write(1, data[offset:offset+16384])
        except (InterruptedError, BlockingIOError) as exc:
            _check(deadline)
            progress = getattr(exc, "characters_written", 0)
            _require(type(progress) is int and progress == 0)
            if isinstance(exc, BlockingIOError):
                _require(exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK))
            continue
        _require(type(n) is int and 0 < n <= min(16384,len(data)-offset))
        offset += n
        _check(deadline)
    _check(deadline)


def _path(path):
    _require(type(path) is str and len(path) <= 4096 and
             re.fullmatch(r"/[A-Za-z0-9_./+:-]+", path, re.ASCII) is not None)
    _require(all(part not in ("", ".", "..") for part in path[1:].split("/")))
    _require(path.startswith(("/opt/easel/", "/usr/local/", "/usr/lib/")))
    return path


def _stamp(s):
    return tuple(getattr(s, key) for key in ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid",
                 "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns"))


def _regular(s):
    _require(stat.S_ISREG(s.st_mode) and s.st_uid == s.st_gid == 0 and
             s.st_mode & 0o22 == 0 and s.st_nlink > 0)


def _directory(s):
    _require(stat.S_ISDIR(s.st_mode) and s.st_uid == s.st_gid == 0 and s.st_mode & 0o22 == 0)


def _open_parent(path, deadline):
    """Walk using anchored nofollow directory FDs; never resolve a symlink."""
    _path(path)
    _check(deadline)
    parent = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        _directory(os.fstat(parent))
        parts = path[1:].split("/")
        for part in parts[:-1]:
            _check(deadline)
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
            try:
                _directory(os.fstat(child))
            except BaseException:
                os.close(child)
                raise
            os.close(parent)
            parent = child
        return parent,parts[-1]
    except BaseException:
        os.close(parent)
        raise


def _open_immutable(path, deadline):
    parent,leaf = _open_parent(path,deadline)
    try:
        _check(deadline)
        fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=parent)
        return fd,parent,leaf
    except BaseException:
        os.close(parent)
        raise


def _immutable_read(path, cap, deadline, expected=None, collect=False):
    fd, parent, leaf = _open_immutable(path, deadline)
    try:
        _check(deadline)
        before = os.fstat(fd)
        _regular(before)
        _require(0 <= before.st_size <= cap)
        if expected is not None:
            _require(before.st_size == expected["size"] and stat.S_IMODE(before.st_mode) == expected["mode"])
        chunks = bytearray() if collect else None
        total = 0
        digest = hashlib.sha256()
        while True:
            _check(deadline)
            try:
                data = os.read(fd, min(65536, before.st_size-total+1))
            except InterruptedError:
                continue
            _check(deadline)
            if not data:
                break
            total += len(data)
            _require(total <= before.st_size)
            digest.update(data)
            if collect:
                chunks.extend(data)
        _require(total == before.st_size)
        _check(deadline)
        _require(_stamp(os.fstat(fd)) == _stamp(before))
        _require(_stamp(os.stat(leaf, dir_fd=parent, follow_symlinks=False)) == _stamp(before))
        observed = digest.hexdigest()
        if expected is not None:
            _require(observed == expected["sha256"])
        _check(deadline)
        return bytes(chunks) if collect else observed
    finally:
        os.close(fd)
        os.close(parent)


def _immutable_bytes(path, cap, deadline, expected=None):
    return _immutable_read(path, cap, deadline, expected, True)


def _json(raw):
    try:
        # Reuse v2 strict duplicate/number/depth/string policy, not another JSON dialect.
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=contract._pairs,
                           parse_float=contract._bad_number, parse_constant=contract._bad_number)
        contract._tree(value)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise B3Error("B3 worker rejected") from None


def _validate_build(build, policy, deadline):
    _check(deadline)
    fields = {"format", "architecture", "artifact_path", "artifact_sha256", "entrypoint_path",
              "entrypoint_sha256", "executable_path", "executable_sha256", "pg_restore_path",
              "pg_restore_sha256", "files", "allowed_fileless_modules"}
    _require(type(build) is dict and build.keys() == fields)
    _require(build["format"] == "easel.b3.worker-build.v1" and
             build["architecture"] in ("amd64", "arm64") and build["architecture"] == policy.architecture)
    _require(build["artifact_path"] == _ARTIFACT and build["pg_restore_path"] == _PG)
    for role in ("artifact", "entrypoint", "executable", "pg_restore"):
        _path(build[role+"_path"])
        contract._hash(build[role+"_sha256"])
    files = build["files"]
    _require(type(files) is dict and 1 <= len(files) <= 20000 and _BUILD not in files)
    total = 0
    for path, meta in files.items():
        _check(deadline)
        _path(path)
        _require(type(meta) is dict and meta.keys() == {"sha256", "size", "mode"})
        contract._hash(meta["sha256"])
        _require(type(meta["size"]) is int and 0 <= meta["size"] <= 512*1024**2)
        _require(type(meta["mode"]) is int and 0 <= meta["mode"] <= 0o777 and meta["mode"] & 0o22 == 0)
        total += meta["size"]
        _require(total <= 2*1024**3)
    for role in ("artifact", "entrypoint", "executable", "pg_restore"):
        path = build[role+"_path"]
        _require(path in files and files[path]["sha256"] == build[role+"_sha256"])
    for role, pin in (("artifact",policy.worker_artifact_sha256), ("entrypoint",policy.entrypoint_sha256),
                      ("pg_restore",policy.pg_restore_sha256)):
        _require(build[role+"_sha256"] == pin)
    allowed = build["allowed_fileless_modules"]
    _require(type(allowed) is list and len(allowed) <= 2048)
    _require(all(type(n) is str and len(n) <= 4096 and re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_]*(?:[.][A-Za-z_][A-Za-z0-9_]*)*",n,re.ASCII) for n in allowed))
    _require(allowed == sorted(set(allowed)))
    return build


def _load_build(policy, deadline):
    raw = _immutable_bytes(_BUILD, _BUILD_LIMIT, deadline)
    _require(hashlib.sha256(raw).hexdigest() == policy.dependency_manifest_sha256)
    build = _validate_build(_json(raw), policy, deadline)
    # The alias is the only permitted symlink, never a general realpath fallback.
    _require(sys.executable == _INTERPRETER and build["entrypoint_path"] == __file__)
    _check(deadline)
    parent,leaf = _open_parent(_INTERPRETER,deadline)
    try:
        _require(os.readlink(leaf,dir_fd=parent) == build["executable_path"])
    finally:
        os.close(parent)
    _check(deadline)
    arch = {"aarch64":"arm64", "arm64":"arm64", "x86_64":"amd64", "amd64":"amd64"}.get(platform.machine())
    _require(arch == policy.architecture)
    hashes = {}
    for path, meta in build["files"].items():
        hashes[path] = _immutable_read(path, 512*1024**2, deadline, meta)
    return build, hashes, hashlib.sha256(raw).hexdigest(), arch


def _kernel_bytes(path, cap, deadline):
    _check(deadline)
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        data = bytearray()
        while True:
            _check(deadline)
            try:
                part = os.read(fd, min(16384,cap-len(data)+1))
            except InterruptedError:
                continue
            _check(deadline)
            if not part:
                return bytes(data)
            data.extend(part)
            _require(len(data) <= cap)
    finally:
        os.close(fd)


def _text(path, cap, deadline):
    return _kernel_bytes(path,cap,deadline).decode("ascii", "strict")


def _number(text):
    _require(re.fullmatch(r"(?:0|[1-9][0-9]*)", text, re.ASCII) is not None)
    return int(text)


def _observe_security(policy, deadline):
    _check(deadline)
    _require(os.getuid() == os.geteuid() == os.getgid() == os.getegid() == 10001)
    _check(deadline)
    groups = sorted(os.getgroups())
    _require(set(groups) <= {10001})
    status = {}
    for line in _text("/proc/self/status",65536,deadline).splitlines():
        _require(":" in line)
        key,value = line.split(":",1)
        _require(key and key not in status)
        status[key] = value.strip()
    for key in ("Uid", "Gid"):
        _require(status.get(key, "").split() == ["10001"]*4)
    _require(set(status.get("Groups", "INVALID").split()) <= {"10001"})
    for key in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"):
        value = status.get(key, "")
        _require(re.fullmatch(r"[0-9a-fA-F]{1,16}",value,re.ASCII) is not None and int(value,16) == 0)
    _require(status.get("NoNewPrivs") == "1")
    _check(deadline)
    ns = os.stat("/proc/self/ns/net")  # Sole explicit trusted kernel symlink exception.
    _require(ns.st_dev > 0 and ns.st_ino > 0)
    _check(deadline)
    observation = observe_worker_network(deadline)
    network = validate_network_profile(observation)
    _check(deadline)
    _require(network.profile_sha256 == policy.network_profile_sha256 and network.security_sha256 == policy.network_security_sha256)
    namespace = json.loads(network.canonical_security_json)["namespace"]
    _require(namespace == dict(device=ns.st_dev,inode=ns.st_ino))
    _require(_text("/proc/self/cgroup",128,deadline) == "0::/\n")
    resources = {}
    for file, expected in (("memory.max",policy.memory_bytes),("memory.swap.max",0),("pids.max",policy.pids_limit)):
        resources[file] = _text("/sys/fs/cgroup/"+file,128,deadline).removesuffix("\n")
        _require(_number(resources[file]) == expected)
    resources["cpu.max"] = _text("/sys/fs/cgroup/cpu.max",128,deadline).removesuffix("\n")
    cpu = resources["cpu.max"].split(" ")
    _require(len(cpu) == 2)
    quota,period = map(_number,cpu)
    _require(quota > 0 and period > 0 and quota*10**9 == policy.cpu_nanos*period)
    _check(deadline)
    relevant = {key:status[key] for key in ("Uid","Gid","Groups","CapInh","CapPrm","CapEff","CapBnd","CapAmb","NoNewPrivs")}
    metadata = contract._digest(dict(status=relevant,groups=groups,resources=resources))
    return dict(_metadata_sha256=metadata,_network_observation=observation,worker_netns=dict(device=ns.st_dev,inode=ns.st_ino),uid=10001,gid=10001,
                cap_effective=0,cap_permitted=0,cap_inheritable=0,
                cap_ambient=0,no_new_privileges=True)


def _namespace_directory(path, hashes, deadline):
    _path(path)
    _require(any(file.startswith(path+"/") for file in hashes))
    # Walk directory itself using the same nofollow anchored traversal.
    fd,parent,leaf = _open_immutable(path,deadline)
    try:
        _directory(os.fstat(fd))
        _check(deadline)
        _require(_stamp(os.stat(leaf,dir_fd=parent,follow_symlinks=False)) == _stamp(os.fstat(fd)))
    finally:
        os.close(fd)
        os.close(parent)


def _origins(hashes, allowed, executable_hash, deadline):
    snapshot = dict(sys.modules)
    records = []
    used = set()
    def file_record(path):
        _path(path)
        _require(path in hashes)
        return [path,hashes[path]]
    for name,module in sorted(snapshot.items()):
        _check(deadline)
        _require(type(name) is str and module is not None)
        spec = getattr(module,"__spec__",None)
        origin = getattr(spec,"origin",None)
        file = getattr(module,"__file__",None)
        paths = []
        if origin not in (None,"built-in","frozen"):
            paths.append(file_record(origin))
        if file is not None:
            paths.append(file_record(file))
        cached = getattr(module,"__cached__",None)
        if cached is not None:
            # A declared cache may not exist with -B. Only ENOENT permits absence;
            # any existing bytecode must itself belong to the verified inventory.
            _path(cached)
            if cached in hashes:
                paths.append(file_record(cached))
            else:
                try:
                    _check(deadline)
                    os.lstat(cached)
                except FileNotFoundError:
                    pass
                else:
                    _require(False)
        if origin in ("built-in","frozen"):
            records.append(["interpreter",name,origin,executable_hash])
        if paths:
            records.append(["module",name,paths])
        elif origin in ("built-in","frozen"):
            pass
        elif spec is not None and getattr(spec,"submodule_search_locations",None) is not None:
            locations = list(spec.submodule_search_locations)
            _require(bool(locations))
            for path in locations:
                _namespace_directory(path,hashes,deadline)
            records.append(["namespace",name,sorted(locations)])
        else:
            _require(name in allowed)
            used.add(name)
            records.append(["generated",name])
    _require(used == set(allowed))
    for line in _text("/proc/self/maps",8*1024**2,deadline).splitlines():
        _check(deadline)
        match = re.fullmatch(r"([0-9a-f]+)-([0-9a-f]+) ([r-][w-][x-][ps]) ([0-9a-f]+) ([0-9a-f]+):([0-9a-f]+) ([0-9]+)(?: +(.*))?",line,re.ASCII)
        _require(match is not None)
        start,end,perms,offset,major,minor,inode,path = match.groups()
        _require(int(start,16) < int(end,16))
        if perms[2] != "x":
            continue
        if path and path.startswith("/"):
            records.append(["native",file_record(path)])
        else:
            _require(int(inode) == 0 and path in (None,"","[vdso]","[vsyscall]"))
            records.append(["anonymous-kernel",path or "anonymous"])
    _check(deadline)
    current = dict(sys.modules)
    _require(snapshot.keys() == current.keys() and all(current[name] is obj for name,obj in snapshot.items()))
    # Same approved canonical hashing as request/attestation binding.
    return contract._digest(sorted(records,key=contract._canonical))


def _attest(parsed, deadline):
    before = _observe_security(parsed.policy,deadline)
    build,hashes,dependency_hash,architecture = _load_build(parsed.policy,deadline)
    origins = _origins(hashes,build["allowed_fileless_modules"],hashes[build["executable_path"]],deadline)
    after = _observe_security(parsed.policy,deadline)
    stable = lambda v: {key:value for key,value in v.items() if key != "_network_observation"}
    _require(stable(before) == stable(after))
    # Raw observations include permitted volatile counters; compare full N3 security.
    before_wire = encode_network_observation(before["_network_observation"])
    after_wire = encode_network_observation(after["_network_observation"])
    network = contract._network_evidence(before_wire, after_wire)
    observed = {key:value for key,value in after.items() if not key.startswith("_")}
    observed.update(network)
    p = parsed.policy
    # Private v3 canonical helpers deliberately avoid an inconsistent serializer.
    evidence = dict(binding_sha256=contract._digest(contract._binding_data(p.binding)),
        policy_sha256=contract._digest(contract._policy_data(p)),request_sha256=parsed.request_sha256,
        manifest_sha256=contract._digest(contract.build_worker_manifest(p)),architecture=architecture,
        artifact_sha256=hashes[build["artifact_path"]],dependency_manifest_sha256=dependency_hash,
        entrypoint_sha256=hashes[build["entrypoint_path"]],pg_restore_sha256=hashes[build["pg_restore_path"]],
        executable_sha256=hashes[build["executable_path"]],package_origins_sha256=origins,**observed)
    _check(deadline)
    return contract.encode_attestation(parsed,evidence)


def _invocation():
    _require(sys.argv[1:] == ["attest_worker"] and sys.platform == "linux" and
             sys.executable == _INTERPRETER and not os.isatty(0))


def main():
    """Fixed module entrypoint. No arguments/callbacks or arbitrary selectors."""
    try:
        _invocation()
        local_start = time.monotonic_ns()  # Exactly one origin before reading.
        initial_deadline = local_start + _INITIAL_NS
        parsed = contract.decode_worker_request(_read_frame(initial_deadline))
        deadline = min(initial_deadline,local_start+parsed.remaining_ns)
        _check(deadline)
        response = _attest(parsed,deadline)
        _write_result(response,parsed.policy.private_result_limit,deadline)
        return 0
    except BaseException:
        # Best effort only: never wait/retry, including before local_start exists.
        # Omit diagnostic if nonblocking setup fails; never expose exception text.
        try:
            os.set_blocking(2,False)
            os.write(2,b"b3_worker_rejected\n")
        except BaseException:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
