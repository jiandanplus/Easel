"""One-shot fixed public-image installer. No import-time IO or live restore authority.

Root independently pins these source bytes and intent.json before invoking main.
Private _ROOT/_OWNER are synthetic-test seams, never command-line configuration.
A failed claim and partial outputs are deliberately retained for observation.
"""
import hashlib
import json
import os
import platform
import posixpath
import re
import selectors
import stat
import subprocess
import sys
import tarfile
import time

_ROOT = "/"
_OWNER = (0, 0)
_INPUT = "/opt/easel-build-input"
_BUILD = "/opt/easel/worker-build.json"
_ARTIFACT = "/opt/easel/artifact.whl"
_ENTRY = "/usr/local/lib/python3.12/site-packages/platform_service/media_backup_b3/execution_worker.py"
_EXE = "/usr/local/bin/python3.12"
_PG = "/usr/lib/postgresql/16/bin/pg_restore"
_LIB = "opt/easel/pg16/lib"
_MIB = 1024**2


def _require(ok):
    if not ok:
        raise ValueError("b3 installer rejected")


def _hash(value):
    _require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None)


def _relative(value):
    _require(type(value) is str and 0 < len(value) <= 4096 and
             re.fullmatch(r"[A-Za-z0-9_./+:-]+", value, re.ASCII) is not None and
             all(p not in ("", ".", "..") for p in value.split("/")))
    return value


def _stamp(s):
    return tuple(getattr(s, k) for k in ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid",
        "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns"))


def _owned(s, directory=False, single=False):
    _require((stat.S_ISDIR(s.st_mode) if directory else stat.S_ISREG(s.st_mode)) and
             (s.st_uid, s.st_gid) == _OWNER and s.st_mode & 0o22 == 0 and
             (not single or s.st_nlink == 1))


def _parent(path, create=False):
    _require(path.startswith("/"))
    parts = _relative(path[1:]).split("/")
    fd = os.open(_ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            if create:
                try:
                    os.mkdir(part, 0o755, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                _owned(os.fstat(child), directory=True)
            except BaseException:
                os.close(child)
                raise
            os.close(fd)
            fd = child
        return fd, parts[-1]
    except BaseException:
        os.close(fd)
        raise


def _open(path):
    parent, name = _parent(path)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        return fd, parent, name
    except BaseException:
        os.close(parent)
        raise


def _read(fd, parent, name, cap, expected=None, collect=False, single=False):
    before = os.fstat(fd)
    _owned(before, single=single)
    _require(0 <= before.st_size <= cap)
    if expected is not None:
        _require(before.st_size == expected["size"])
    os.lseek(fd, 0, 0)
    h = hashlib.sha256()
    chunks = bytearray() if collect else None
    total = 0
    while True:
        data = os.read(fd, min(65536, before.st_size - total + 1))
        if not data:
            break
        total += len(data)
        _require(total <= before.st_size)
        h.update(data)
        if collect:
            chunks.extend(data)
    _require(total == before.st_size and _stamp(before) == _stamp(os.fstat(fd)) and
             _stamp(before) == _stamp(os.stat(name, dir_fd=parent, follow_symlinks=False)))
    if expected is not None:
        _require(h.hexdigest() == expected["sha256"])
    os.lseek(fd, 0, 0)
    return bytes(chunks) if collect else dict(sha256=h.hexdigest(), size=total, mode=stat.S_IMODE(before.st_mode))


def _pairs(pairs):
    obj = {}
    for k, v in pairs:
        _require(k not in obj)
        obj[k] = v
    return obj


def _bad(value):
    raise ValueError("b3 installer rejected")


def _json(raw):
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_float=_bad, parse_constant=_bad)


def _intent(raw):
    _require(len(raw) <= 65536)
    intent = _json(raw)
    _require(type(intent) is dict and intent.keys() == {"format", "architecture", "artifact", "wheels",
        "runtime", "pg_restore_sha256", "runtime_layout", "notices"})
    _require(intent["format"] == "easel.b3.worker-install.v1" and intent["architecture"] == "arm64")
    _hash(intent["pg_restore_sha256"])
    _require(type(intent["wheels"]) is list and 1 <= len(intent["wheels"]) <= 64)
    names = {"intent.json", ".install-claim", ".requirements.txt"}
    total = 0
    for item in [intent[k] for k in ("artifact", "runtime", "runtime_layout", "notices")] + intent["wheels"]:
        _require(type(item) is dict and item.keys() == {"filename", "sha256", "size"})
        name = item["filename"]
        _require(type(name) is str and len(name) <= 200 and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]*", name, re.ASCII) is not None and name not in names)
        names.add(name)
        _hash(item["sha256"])
        _require(type(item["size"]) is int and 0 < item["size"] <= 512*_MIB)
        total += item["size"]
    _require(total <= 1024*_MIB and intent["runtime_layout"]["size"] <= 8*_MIB and intent["notices"]["size"] <= 32*_MIB)
    _require(all(i["filename"].endswith(".whl") for i in [intent["artifact"]] + intent["wheels"]))
    return intent


def _write(path, data, mode=0o644):
    parent, name = _parent(path, create=True)
    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=parent)
        try:
            os.fchmod(fd, mode)
            offset = 0
            while offset < len(data):
                n = os.write(fd, data[offset:offset+65536])
                _require(n > 0)
                offset += n
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(parent)
    finally:
        os.close(parent)


def _members(fd, limit=20000, byte_limit=512*_MIB):
    """Parse only literal standard USTAR headers, never hidden PAX/GNU extensions."""
    size = os.fstat(fd).st_size
    _require(size % 512 == 0)
    offset = 0
    result = {}
    total = 0
    while offset + 512 <= size:
        block = os.pread(fd, 512, offset)
        _require(len(block) == 512)
        if block == bytes(512):
            _require(size-offset >= 1024)
            while offset < size:
                chunk = os.pread(fd, min(65536, size-offset), offset)
                _require(chunk and not any(chunk))
                offset += len(chunk)
            return result
        _require(block[257:263] == b"ustar\0" and block[263:265] == b"00")
        item = tarfile.TarInfo.frombuf(block, "utf-8", "strict")
        name = _relative(item.name.rstrip("/") if item.isdir() else item.name)
        _require(name not in result and item.uid == item.gid == 0 and item.type in (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE, tarfile.SYMTYPE))
        _require(type(item.size) is int and 0 <= item.size <= 512*_MIB and item.mode <= 0o777)
        _require(item.isreg() or item.size == 0)
        kind = "directory" if item.isdir() else "symlink" if item.issym() else "file"
        row = dict(kind=kind, mode=item.mode, offset=offset+512, size=item.size)
        if item.issym():
            row["relative_target"] = item.linkname
        if item.isreg():
            h = hashlib.sha256()
            read = 0
            while read < item.size:
                data = os.pread(fd, min(65536, item.size-read), offset+512+read)
                _require(bool(data))
                h.update(data)
                read += len(data)
            row["sha256"] = h.hexdigest()
            total += item.size
        result[name] = row
        _require(len(result) <= limit and total <= byte_limit)
        offset += 512 + ((item.size+511)//512)*512
        _require(offset <= size)
    raise ValueError("b3 installer rejected")


def _runtime(fd, rows, pg_hash):
    _require(type(rows) is list and 1 <= len(rows) <= 20000)
    expected = {}
    for row in rows:
        _require(type(row) is dict)
        kind = row.get("kind")
        fields = {"destination_path", "kind", "mode", "layer_sha256", "source_path"}
        fields |= {"size", "sha256"} if kind == "file" else {"relative_target"} if kind == "symlink" else set()
        _require(kind in ("file", "directory", "symlink") and row.keys() == fields)
        _hash(row["layer_sha256"])
        _relative(row["source_path"])
        dest = row["destination_path"]
        _require(type(dest) is str and dest.startswith("rootfs/"))
        name = _relative(dest[7:])
        _require(name == _LIB or name.startswith(_LIB+"/") or name == _PG[1:])
        _require(name not in expected and type(row["mode"]) is int)
        _require(row["mode"] == 0o755 if kind == "directory" else row["mode"] == 0o777 if kind == "symlink" else 0 <= row["mode"] <= 0o777 and row["mode"] & 0o22 == 0 and row["mode"] & 0o444 == 0o444)
        meta = {k:row[k] for k in ("kind", "mode")}
        if kind == "file":
            _hash(row["sha256"])
            _require(type(row["size"]) is int and 0 <= row["size"] <= 512*_MIB)
            meta.update(size=row["size"], sha256=row["sha256"])
        if kind == "symlink":
            target = row["relative_target"]
            _require(type(target) is str and len(target) <= 4096 and not target.startswith("/") and
                     re.fullmatch(r"[A-Za-z0-9_./+:-]+", target, re.ASCII) is not None)
            meta["relative_target"] = target
        expected[name] = meta
    for name in list(expected):
        parent = posixpath.dirname(name)
        while parent:
            if parent in expected:
                _require(expected[parent]["kind"] == "directory")
            else:
                expected[parent] = dict(kind="directory", mode=0o755)
            parent = posixpath.dirname(parent)
    _require(_LIB in expected and expected[_LIB]["kind"] == "directory")
    _require(_PG[1:] in expected and expected[_PG[1:]]["kind"] == "file" and expected[_PG[1:]]["sha256"] == pg_hash)
    # Resolve every component, including directory aliases; reject cycles and escapes.
    for name, meta in expected.items():
        if meta["kind"] != "symlink":
            continue
        pending = (posixpath.dirname(name)+"/"+meta["relative_target"]).split("/")
        stack = []
        hops = 0
        while pending:
            part = pending.pop(0)
            if part in ("", "."):
                continue
            if part == "..":
                _require(bool(stack));stack.pop()
                _require("/".join(stack) == _LIB or "/".join(stack).startswith(_LIB+"/"))
                continue
            stack.append(part)
            current = "/".join(stack)
            _require(current in expected)
            node = expected[current]
            if node["kind"] == "symlink":
                hops += 1
                _require(hops <= len(expected))
                stack.pop()
                pending = node["relative_target"].split("/") + pending
            elif pending:
                _require(node["kind"] == "directory")
        _require("/".join(stack) == _LIB or "/".join(stack).startswith(_LIB+"/"))
    actual = _members(fd)
    _require(actual.keys() == expected.keys())
    for name, meta in expected.items():
        _require(all(actual[name].get(k) == v for k, v in meta.items()))
    return actual


def _notices(fd):
    nodes = _members(fd, 4096, 64*_MIB)
    _require("licenses/provenance.json" in nodes and nodes["licenses/provenance.json"]["kind"] == "file")
    for name, meta in nodes.items():
        _require(name == "licenses" or name.startswith("licenses/"))
        _require(meta["kind"] in ("directory", "file") and meta["mode"] == (0o755 if meta["kind"] == "directory" else 0o644))
        parent = posixpath.dirname(name)
        _require(not parent or parent in nodes and nodes[parent]["kind"] == "directory")
    return nodes


def _extract(fd, nodes, prefix=""):
    for name in sorted(nodes, key=lambda n:(n.count("/"),n)):
        meta = nodes[name]
        path = prefix+"/"+name
        if meta["kind"] == "directory":
            parent, leaf = _parent(path, create=True)
            try:
                try:
                    os.mkdir(leaf, 0o755, dir_fd=parent)
                    os.fsync(parent)
                except FileExistsError:
                    child = os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                    try:
                        s = os.fstat(child);_owned(s, directory=True)
                        _require(stat.S_IMODE(s.st_mode) == 0o755)
                    finally:
                        os.close(child)
            finally:
                os.close(parent)
        elif meta["kind"] == "file":
            parent, leaf = _parent(path)
            try:
                out = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, meta["mode"], dir_fd=parent)
                try:
                    os.fchmod(out, meta["mode"])
                    read = 0
                    while read < meta["size"]:
                        data = os.pread(fd, min(65536, meta["size"]-read), meta["offset"]+read)
                        _require(bool(data))
                        offset = 0
                        while offset < len(data):
                            n = os.write(out, data[offset:]);_require(n > 0);offset += n
                        read += len(data)
                    os.fsync(out)
                finally:
                    os.close(out)
                os.fsync(parent)
            finally:
                os.close(parent)
    for name, meta in sorted(nodes.items()):
        if meta["kind"] == "symlink":
            parent, leaf = _parent(prefix+"/"+name)
            try:
                os.symlink(meta["relative_target"], leaf, dir_fd=parent)
                os.fsync(parent)
            finally:
                os.close(parent)
    for name, meta in nodes.items():
        path = prefix+"/"+name
        parent, leaf = _parent(path)
        try:
            s = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
            _require((s.st_uid, s.st_gid) == _OWNER and stat.S_IMODE(s.st_mode) == meta["mode"])
            if meta["kind"] == "symlink":
                _require(stat.S_ISLNK(s.st_mode) and os.readlink(leaf, dir_fd=parent) == meta["relative_target"])
            elif meta["kind"] == "directory":
                _owned(s, directory=True)
            else:
                file = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
                try:
                    _read(file, parent, leaf, 512*_MIB, meta, single=True)
                finally:
                    os.close(file)
        finally:
            os.close(parent)


def _pip(check=False):
    args = [sys.executable, "-I", "-B", "-m", "pip"]
    args += ["check", "--isolated", "--disable-pip-version-check"] if check else ["install", "--isolated",
        "--no-index", "--no-deps", "--no-compile", "--disable-pip-version-check", "--no-cache-dir",
        "--require-hashes", "-r", _INPUT+"/.requirements.txt"]
    env = dict(PATH="/usr/local/bin:/usr/bin:/bin", HOME="/nonexistent", PIP_CONFIG_FILE="/dev/null",
               PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1", LANG="C.UTF-8")
    proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, cwd="/", close_fds=True)
    deadline = time.monotonic()+300
    selector = selectors.DefaultSelector()
    total = 0
    try:
        for stream in (proc.stdout, proc.stderr):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ)
        while selector.get_map():
            _require(time.monotonic() < deadline)
            for key, _ in selector.select(min(0.2, max(0, deadline-time.monotonic()))):
                data = os.read(key.fileobj.fileno(), 16384)
                if not data:
                    selector.unregister(key.fileobj)
                total += len(data)
                _require(total <= _MIB)
        _require(proc.wait(timeout=max(0.001, deadline-time.monotonic())) == 0)
    except BaseException:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)
        raise
    finally:
        selector.close()
        proc.stdout.close();proc.stderr.close()


def _inventory():
    files = {}
    total = 0
    def walk(fd, path):
        nonlocal total
        _owned(os.fstat(fd), directory=True)
        for name in sorted(os.listdir(fd)):
            _relative(name)
            childpath = path+"/"+name
            if childpath == _BUILD:
                continue
            s = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISLNK(s.st_mode):
                continue
            if stat.S_ISDIR(s.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    walk(child, childpath)
                finally:
                    os.close(child)
            else:
                file = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                try:
                    meta = _read(file, fd, name, 512*_MIB)
                finally:
                    os.close(file)
                _require(meta["mode"] <= 0o777)
                files[childpath] = meta
                total += meta["size"]
                _require(len(files) <= 20000 and total <= 2*1024**3)
    for path in ("/usr/local", "/usr/lib", "/opt/easel"):
        parent, leaf = _parent(path)
        try:
            fd = os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                walk(fd, path)
            finally:
                os.close(fd)
        finally:
            os.close(parent)
    return files


def _install():
    opened = []
    def verified(path, cap, expected=None, collect=False):
        fd, parent, name = _open(path)
        opened.extend([fd, parent])
        data = _read(fd, parent, name, cap, expected, collect, single=True)
        return data if collect else fd
    try:
        _write(_INPUT+"/.install-claim", b"easel.b3.worker-install.v1\n", 0o444)
        intent = _intent(verified(_INPUT+"/intent.json", 65536, collect=True))
        inputs = [intent[k] for k in ("artifact", "runtime", "runtime_layout", "notices")] + intent["wheels"]
        inputdir, _ = _parent(_INPUT+"/intent.json")
        try:
            _require(set(os.listdir(inputdir)) == {"intent.json", ".install-claim"} | {item["filename"] for item in inputs})
        finally:
            os.close(inputdir)
        pinned = {}
        for item in [intent[k] for k in ("artifact", "runtime", "runtime_layout", "notices")] + intent["wheels"]:
            pinned[item["filename"]] = verified(_INPUT+"/"+item["filename"], 512*_MIB, item)
        layoutfd = pinned[intent["runtime_layout"]["filename"]]
        layout = _json(os.pread(layoutfd, 8*_MIB+1, 0))
        _require(type(layout) is dict and layout.keys() == {"format", "nodes"} and layout["format"] == "b3-pg-runtime-layout.v1")
        runtimefd = pinned[intent["runtime"]["filename"]]
        runtime = _runtime(runtimefd, layout["nodes"], intent["pg_restore_sha256"])
        noticesfd = pinned[intent["notices"]["filename"]]
        notices = _notices(noticesfd)
        # Entire owned subtrees must be fresh; shared ancestor scaffolds may exist.
        for path in ("/"+_LIB, _PG, "/opt/easel/licenses", _ARTIFACT, _BUILD, "/opt/easel/bin/python"):
            parent, leaf = _parent(path, create=True)
            try:
                try:
                    os.stat(leaf, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise ValueError("b3 installer rejected")
            finally:
                os.close(parent)
        # Validate both complete archives before any immutable destination writes.
        _extract(runtimefd, runtime)
        _extract(noticesfd, notices, "/opt/easel")
        artifactfd = pinned[intent["artifact"]["filename"]]
        parent, leaf = _parent(_ARTIFACT, create=True)
        try:
            out = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444, dir_fd=parent)
            try:
                os.fchmod(out, 0o444)
                offset = 0
                while offset < intent["artifact"]["size"]:
                    data = os.pread(artifactfd, min(65536, intent["artifact"]["size"]-offset), offset)
                    _require(bool(data))
                    written = 0
                    while written < len(data):
                        n = os.write(out, data[written:]);_require(n > 0);written += n
                    offset += len(data)
                os.fsync(out)
            finally:
                os.close(out)
            os.fsync(parent)
        finally:
            os.close(parent)
        req = "".join("file://"+_INPUT+"/"+item["filename"]+" --hash=sha256:"+item["sha256"]+"\n"
                      for item in [intent["artifact"]]+intent["wheels"])
        _write(_INPUT+"/.requirements.txt", req.encode("ascii"), 0o444)
        _pip()
        _pip(check=True)
        parent, leaf = _parent("/opt/easel/bin/python", create=True)
        try:
            os.symlink(_EXE, leaf, dir_fd=parent)
            os.fsync(parent)
        finally:
            os.close(parent)
        files = _inventory()
        build = dict(format="easel.b3.worker-build.v1", architecture="arm64", files=files, allowed_fileless_modules=[])
        for role, path in (("artifact", _ARTIFACT), ("entrypoint", _ENTRY), ("executable", _EXE), ("pg_restore", _PG)):
            _require(path in files)
            build[role+"_path"] = path
            build[role+"_sha256"] = files[path]["sha256"]
        _require(build["artifact_sha256"] == intent["artifact"]["sha256"] and build["pg_restore_sha256"] == intent["pg_restore_sha256"])
        raw = json.dumps(build, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        _require(len(raw) <= 8*_MIB)
        _write(_BUILD, raw, 0o444)
        return dict(manifest_sha256=hashlib.sha256(raw).hexdigest(), files=len(files),
                    bytes=sum(meta["size"] for meta in files.values()), artifact_sha256=build["artifact_sha256"],
                    pg_restore_sha256=build["pg_restore_sha256"])
    finally:
        for fd in reversed(opened):
            os.close(fd)


def main():
    try:
        _require(sys.argv[1:] == [] and sys.platform == "linux" and platform.machine() in ("aarch64", "arm64") and
                 os.getuid() == os.geteuid() == os.getgid() == os.getegid() == 0 and _ROOT == "/" and _OWNER == (0, 0))
        os.umask(0o022)
        receipt = json.dumps(_install(), sort_keys=True, separators=(",", ":")).encode("ascii")+b"\n"
        _require(len(receipt) <= 1024)
        os.write(1, receipt)
        return 0
    except BaseException:
        try:
            os.write(2, b"b3_worker_install_rejected\n")
        except BaseException:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
