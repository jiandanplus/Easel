"""Private descriptor durability, not capture/authentication/self-verify proof.

The controller must finish whole-object authentication, source-facts and source
postconditions before issuing an independent anchor. No HTTP/CLI or cleanup policy.
"""
import hashlib
import os
import stat
import sys
import threading
import uuid

from .contracts import B2Error, require, validate_hash, validate_int, validate_uuid
from . import wire

_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_READ = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
_WRITE = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
_CHUNK = 1048576
_OBJECTS = {"archive": "archive.easelbs2", "context": "context.easelbs2"}


def _identity(info):
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid


def _stamp(info):
    return (*_identity(info), info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _close(fd, identity=None):
    if fd is None:
        return
    try:
        if identity is None or _identity(os.fstat(fd))[:2] == identity[:2]:
            os.close(fd)
    except OSError:
        pass


def _fd_path(fd):
    if sys.platform == "darwin":
        import fcntl
        raw = fcntl.fcntl(fd, 50, bytes(1024)).split(b"\0", 1)[0]
        path = os.fsdecode(raw)
    elif sys.platform.startswith("linux"):
        path = os.readlink("/proc/self/fd/" + str(fd))
    else:
        raise B2Error()
    require(type(path) is str and path.startswith("/") and len(os.fsencode(path)) <= 16384)
    require(not path.endswith(" (deleted)"))
    return path


class _Directory:
    """Held nofollow parent-entry chain rooted in the explicit operator FD."""
    def __init__(self, fd, owner):
        self.fds, self.entries, self.closed = [], [], False
        self.owner = owner
        try:
            require(type(fd) is int and fd >= 0 and type(owner) is tuple and len(owner) == 2)
            original = os.fstat(fd)
            require(stat.S_ISDIR(original.st_mode) and stat.S_IMODE(original.st_mode) == 0o700
                    and (original.st_uid, original.st_gid) == owner and original.st_nlink > 0)
            parts = [p for p in _fd_path(fd).split("/") if p]
            require(len(parts) <= 128 and all(p not in (".", "..") for p in parts))
            parent = os.open("/", _DIR)
            self.fds.append((parent, _identity(os.fstat(parent))))
            for name in parts:
                child = os.open(name, _DIR, dir_fd=parent)
                info = os.fstat(child)
                self.fds.append((child, _identity(info)))
                self.entries.append((parent, name, child, _identity(info)))
                parent = child
            self.fd = parent
            require(_identity(os.fstat(self.fd)) == _identity(original))
            self.verify()
        except BaseException:
            self.close()
            raise

    @property
    def identity(self):
        return self.fds[-1][1]

    @property
    def lineage(self):
        return frozenset(identity[:2] for _, identity in self.fds)

    @property
    def parent_identity(self):
        require(self.entries)
        return _identity(os.fstat(self.entries[-1][0]))

    @property
    def name(self):
        require(self.entries)
        return self.entries[-1][1]

    def verify(self):
        require(not self.closed)
        for fd, identity in self.fds:
            info = os.fstat(fd)
            require(stat.S_ISDIR(info.st_mode) and info.st_nlink > 0 and _identity(info) == identity)
        for parent, name, child, identity in self.entries:
            require(_identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) == identity
                    and _identity(os.fstat(child)) == identity)
        terminal = os.fstat(self.fd)
        require(stat.S_IMODE(terminal.st_mode) == 0o700 and (terminal.st_uid, terminal.st_gid) == self.owner)

    def sync(self):
        self.verify()
        # Includes newly created root/ancestor entries, not just jobdir.
        for fd, _ in reversed(self.fds):
            os.fsync(fd)
        self.verify()

    def clone(self):
        self.verify()
        clone = object.__new__(_Directory)
        clone.fds, clone.entries, clone.closed, clone.owner = [], [], False, self.owner
        mapping = {}
        try:
            for fd, identity in self.fds:
                copied = os.dup(fd)
                clone.fds.append((copied, identity))
                mapping[fd] = copied
            clone.entries = [(mapping[p], name, mapping[c], identity) for p, name, c, identity in self.entries]
            clone.fd = mapping[self.fd]
            self.verify()
            clone.verify()
            return clone
        except BaseException:
            clone.close()
            raise

    def close(self):
        if self.closed:
            return
        self.closed = True
        for fd, identity in reversed(self.fds):
            _close(fd, identity)


def _regular(info, owner, maximum):
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            and stat.S_IMODE(info.st_mode) == 0o600 and (info.st_uid, info.st_gid) == owner
            and 0 <= info.st_size <= maximum)


class _File:
    def __init__(self, directory, name, maximum):
        self.directory, self.name, self._fd = directory, name, None
        self._offset = 0
        try:
            directory.verify()
            require(type(name) is str and name not in (".", "..") and "/" not in name and len(name) <= 128)
            self._fd = os.open(name, _READ, dir_fd=directory.fd)
            info = os.fstat(self._fd)
            _regular(info, directory.owner, maximum)
            self.stamp = _stamp(info)
            self.verify()
        except BaseException:
            self.close()
            raise

    @property
    def size(self):
        return self.stamp[6]

    def verify(self):
        require(self._fd is not None)
        self.directory.verify()
        require(_stamp(os.fstat(self._fd)) == self.stamp
                and _stamp(os.stat(self.name, dir_fd=self.directory.fd, follow_symlinks=False)) == self.stamp)

    def hash_all(self, expected_bytes, expected_sha256, *, check=None):
        require(check is None or callable(check))
        guard = check if check is not None else lambda: None
        guard()
        validate_int(expected_bytes, 32 * 1024**3 + 2 * 1024**2 + 8192, 1)
        validate_hash(expected_sha256)
        self.verify()
        require(self.size == expected_bytes)
        digest, offset = hashlib.sha256(), 0
        while offset < expected_bytes:
            guard()
            self.verify()
            size = min(_CHUNK, expected_bytes - offset)
            raw = os.pread(self._fd, size, offset)
            guard()
            require(type(raw) is bytes and 0 < len(raw) <= size)
            digest.update(raw)
            offset += len(raw)
        guard()
        eof = os.pread(self._fd, 1, offset)
        guard()
        require(eof == b"" and digest.hexdigest() == expected_sha256)
        self.verify()
        guard()

    def read(self, size):
        try:
            require(type(size) is int and size > 0)
            self.verify()
            require(os.lseek(self._fd, 0, os.SEEK_CUR) == self._offset)
            raw = os.read(self._fd, min(size, _CHUNK))
            require(type(raw) is bytes and len(raw) <= min(size, _CHUNK))
            require(raw or self._offset == self.size)
            self._offset += len(raw)
            require(self._offset <= self.size)
            self.verify()
            return raw
        except Exception:
            raise B2Error() from None

    def read_all(self, maximum):
        require(self.size <= maximum)
        result = bytearray()
        while True:
            raw = self.read(min(_CHUNK, maximum + 1 - len(result)))
            if not raw:
                return bytes(result)
            result.extend(raw)
            require(len(result) <= maximum)

    def close(self):
        fd, self._fd = self._fd, None
        if fd is not None:
            _close(fd, tuple(self.stamp[:5]) if hasattr(self, "stamp") else None)

    def __enter__(self): return self
    def __exit__(self, *args): self.close()


def _write_all(fd, raw):
    view = memoryview(raw)
    while view:
        count = os.write(fd, view)
        require(type(count) is int and 0 < count <= len(view))
        view = view[count:]


def _publish_record(directory, name, raw, maximum):
    require(type(raw) is bytes and 1 <= len(raw) <= maximum)
    directory.verify()
    temporary, fd = ".record-" + uuid.uuid4().hex + ".tmp", None
    try:
        fd = os.open(temporary, _WRITE, 0o600, dir_fd=directory.fd)
        os.fchmod(fd, 0o600)
        _regular(os.fstat(fd), directory.owner, maximum)
        _write_all(fd, raw)
        os.fsync(fd)
        stamp = _stamp(os.fstat(fd))
        os.close(fd)
        fd = None
        with _File(directory, temporary, maximum) as source:
            require(source.stamp == stamp)
            source.hash_all(len(raw), hashlib.sha256(raw).hexdigest())
        directory.verify()
        # Atomic no-clobber; never exists-then-overwriting rename.
        os.link(temporary, name, src_dir_fd=directory.fd, dst_dir_fd=directory.fd, follow_symlinks=False)
        os.unlink(temporary, dir_fd=directory.fd)
        directory.sync()
        with _File(directory, name, maximum) as source:
            require(source.stamp[:5] == stamp[:5])
            source.hash_all(len(raw), hashlib.sha256(raw).hexdigest())
    finally:
        _close(fd)
        # Unknown/partial names are deliberately not auto-deleted.


class _CipherSink:
    def __init__(self, job, kind):
        self.job, self.kind, self._fd = job, kind, None
        self._lock, self._written = threading.RLock(), 0
        self._name = "." + kind + "-" + uuid.uuid4().hex + ".partial"
        self._finished = self._failed = False
        try:
            job.verify()
            self._fd = os.open(self._name, _WRITE, 0o600, dir_fd=job._directory.fd)
            os.fchmod(self._fd, 0o600)
            self._identity = _identity(os.fstat(self._fd))
            self._last = _stamp(os.fstat(self._fd))
            self._verify()
            job._directory.sync()
        except BaseException:
            self.close()
            raise

    def _verify(self):
        self.job.verify()
        require(self._fd is not None and not self._finished and not self._failed)
        info = os.fstat(self._fd)
        _regular(info, self.job.owner, wire.cipher_budget("central-" + self.kind))
        require(_stamp(info) == self._last and _identity(info) == self._identity
                and _stamp(os.stat(self._name, dir_fd=self.job._directory.fd, follow_symlinks=False)) == self._last
                and os.lseek(self._fd, 0, os.SEEK_CUR) == self._written)

    def write(self, raw):
        with self._lock:
            try:
                self._verify()
                require(isinstance(raw, (bytes, bytearray, memoryview)))
                view = memoryview(raw).cast("B")
                count = len(view)
                require(self._written + count <= wire.cipher_budget("central-" + self.kind))
                while view:
                    self._verify()
                    accepted = os.write(self._fd, view)
                    require(type(accepted) is int and 0 < accepted <= len(view))
                    self._written += accepted
                    self._last = _stamp(os.fstat(self._fd))
                    view = view[accepted:]
                self._verify()
                return count
            except BaseException as error:
                self._failed = self.job._failed = True
                if not isinstance(error, Exception): raise
                raise B2Error() from None

    def finish_reopen(self, receipt):
        with self._lock:
            try:
                self._verify()
                require(type(receipt) is dict and receipt.keys() == {"plaintext_length", "plaintext_sha256", "plan_sha256",
                    "registry_sha256", "data_records", "ciphertext_bytes", "ciphertext_sha256"})
                wire.encode_final({key: value for key, value in receipt.items() if key not in {"ciphertext_bytes", "ciphertext_sha256"}})
                validate_int(receipt["plaintext_length"], wire.MAX_ARCHIVE if self.kind == "archive" else wire.MAX_CONTEXT, 1)
                validate_int(receipt["ciphertext_bytes"], wire.cipher_budget("central-" + self.kind), 1)
                validate_hash(receipt["ciphertext_sha256"])
                require(receipt["ciphertext_bytes"] == self._written)
                os.fsync(self._fd)
                stamp = _stamp(os.fstat(self._fd))
                self._verify()
                os.close(self._fd)
                self._fd = None
                with _File(self.job._directory, self._name, wire.cipher_budget("central-" + self.kind)) as reopened:
                    require(reopened.stamp == stamp)
                name = _OBJECTS[self.kind]
                self.job._directory.verify()
                os.link(self._name, name, src_dir_fd=self.job._directory.fd,
                        dst_dir_fd=self.job._directory.fd, follow_symlinks=False)
                os.unlink(self._name, dir_fd=self.job._directory.fd)
                self.job._directory.sync()
                source = _File(self.job._directory, name, wire.cipher_budget("central-" + self.kind))
                try:
                    require(source.stamp[:5] == stamp[:5])
                    source.hash_all(receipt["ciphertext_bytes"], receipt["ciphertext_sha256"])
                    self.job._finished[self.kind] = (dict(receipt), source.stamp)
                    self._finished = True
                    return source
                except BaseException:
                    source.close()
                    raise
            except BaseException as error:
                self._failed = self.job._failed = True
                if not isinstance(error, Exception): raise
                raise B2Error() from None

    def close(self):
        with self._lock:
            fd, self._fd = self._fd, None
            _close(fd, getattr(self, "_identity", None))


class PrivateJob:
    def __init__(self, root, directory, backup_id):
        self._root, self._directory, self.backup_id = root, directory, backup_id
        self.owner, self._sinks, self._finished = root.owner, {}, {}
        self._failed = self._closed = self._committed = False
        self._anchor = None

    def verify(self):
        require(not self._closed and not self._failed and not self._committed)
        self._root.verify()
        self._directory.verify()
        require(self._directory.parent_identity == self._root.identity and self._directory.name == self.backup_id)

    def create_object(self, kind):
        try:
            self.verify()
            require(type(kind) is str and kind in _OBJECTS and kind not in self._sinks)
            sink = _CipherSink(self, kind)
            self._sinks[kind] = sink
            return sink
        except Exception:
            self._failed = True
            raise B2Error() from None

    def bind_anchor(self, intent):
        from .anchor import RootAnchorIntent
        try:
            self.verify()
            require(type(intent) is RootAnchorIntent and self._anchor is None)
            intent._require_durable()
            intent._verify_for_job(self)
            intent._read_anchor()
            self._anchor = intent._clone()
        except Exception:
            self._failed = True
            raise B2Error() from None

    def close(self):
        if self._closed: return
        for sink in self._sinks.values(): sink.close()
        if self._anchor is not None: self._anchor.close()
        self._directory.close()
        self._root.close()
        self._closed = True

    def __enter__(self): return self
    def __exit__(self, *args): self.close()


def create_private_job(trusted_backup_root_fd, backup_id):
    root = directory = None
    try:
        validate_uuid(backup_id)
        info = os.fstat(trusted_backup_root_fd)
        require(info.st_uid == os.geteuid())
        root = _Directory(trusted_backup_root_fd, (info.st_uid, info.st_gid))
        os.mkdir(backup_id, 0o700, dir_fd=root.fd)
        fd = os.open(backup_id, _DIR, dir_fd=root.fd)
        try: directory = _Directory(fd, root.owner)
        finally: os.close(fd)
        directory.sync()
        root.verify()
        return PrivateJob(root, directory, backup_id)
    except BaseException as error:
        if directory is not None: directory.close()
        if root is not None: root.close()
        if not isinstance(error, Exception): raise
        raise B2Error() from None


def publish_commit_no_clobber(job, commit_bytes):
    try:
        require(type(job) is PrivateJob)
        job.verify()
        commit = wire.decode_commit(commit_bytes)
        require(commit["backup_id"] == job.backup_id and job._anchor is not None
                and set(job._finished) == set(_OBJECTS))
        job._anchor._verify_for_job(job)
        reference = job._anchor._validate_commit(commit_bytes)
        require(job._anchor._read_anchor() == reference)
        for kind, name in _OBJECTS.items():
            receipt, stamp = job._finished[kind]
            row = commit["objects"][kind]
            require(row["bytes"] == receipt["ciphertext_bytes"] and row["sha256"] == receipt["ciphertext_sha256"])
            require(receipt["plan_sha256"] == commit["plan_sha256"]
                    and receipt["registry_sha256"] == commit["registry_sha256"])
            with _File(job._directory, name, wire.cipher_budget("central-" + kind)) as source:
                require(source.stamp == stamp)
                source.hash_all(row["bytes"], row["sha256"])
        require(job._anchor._read_anchor() == reference)
        job.verify()
        _publish_record(job._directory, "COMMITTED.json", commit_bytes, wire.MAX_COMMIT)
        job._committed = True
        return reference
    except Exception:
        if type(job) is PrivateJob: job._failed = True
        raise B2Error() from None
