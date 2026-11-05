"""Trusted-FD media capture, without path inputs or source mutation.

Caller must exclude concurrent same-UID mutation for the full operation.
Observed identity checks detect drift; they are not a filesystem snapshot.
"""
import hashlib
import os
import stat

from .contract import ClosureError, Limits, member_records, validate_plan


class FileError(ValueError):
    def __init__(self):
        super().__init__('invalid_source')


def _require(value):
    if not value:
        raise FileError()


def _signature(info):
    return tuple(getattr(info, field) for field in (
        'st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns',
        'st_uid', 'st_gid', 'st_mode', 'st_nlink'))


def _file(info, row):
    _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
    _require((info.st_size, info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) ==
             (row['size'], row['uid'], row['gid'], row['mode']))


class Sources:
    """Borrow fixed FDs and a private media directory; own only per-blob opens."""

    def __init__(self, plan, media_fd, file_fds, owner, chunk_bytes):
        try:
            self.plan = validate_plan(plan, Limits(chunk_bytes=chunk_bytes))
            self.records = {row['name']: row for row in member_records(self.plan)}
            _require(type(owner) is tuple and len(owner) == 2 and all(
                type(value) is int and 0 <= value < 2**32 for value in owner))
            _require(type(media_fd) is int and media_fd >= 0 and type(file_fds) is dict)
            _require(set(file_fds) == {row['name'] for row in self.plan['files']})
            _require(all(type(fd) is int and fd >= 0 for fd in file_fds.values()))
            self.media_fd, self.file_fds, self.owner = media_fd, dict(file_fds), owner
            self.chunk_bytes = chunk_bytes
            self.expected = {row['key'] for row in self.plan['blobs'] if row['present']}
            self.initial = {}; self.consumed = set(); self.started = set(); self.active = False
            self.owned_fds = set()
        except (OSError, TypeError, KeyError, ValueError):
            raise FileError() from None

    def _directory(self):
        info = os.fstat(self.media_fd)
        _require(stat.S_ISDIR(info.st_mode) and (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) ==
                 (*self.owner, 0o700))
        return _signature(info)

    def _inventory(self):
        names = set()
        with os.scandir(self.media_fd) as entries:
            for entry in entries:
                _require(entry.name in self.expected and entry.name not in names)
                names.add(entry.name)
                _require(len(names) <= len(self.expected))
        _require(names == self.expected)

    def __enter__(self):
        try:
            _require(not self.active and not self.started)
            self.directory_before = self._directory()
            self._inventory()
            for row in self.plan['files']:
                info = os.fstat(self.file_fds[row['name']]); _file(info, row)
                self.initial[row['name']] = _signature(info)
            self.active = True
            return self
        except (OSError, ValueError):
            raise FileError() from None

    def chunks(self, row):
        owned = None
        try:
            _require(self.active and type(row) is dict and row.get('name') in self.records)
            name = row['name']
            _require(row == self.records[name] and name not in self.started)
            self.started.add(name)
            if name.startswith('media/'):
                key = name.split('/', 1)[1]
                owned = os.open(key, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.media_fd)
                self.owned_fds.add(owned)
                fd = owned
            else:
                fd = self.file_fds[name]
            before = os.fstat(fd); _file(before, row)
            stamp = _signature(before)
            if name in self.initial:
                _require(stamp == self.initial[name])
            else:
                self.initial[name] = stamp
            offset = 0; digest = hashlib.sha256()
            while offset < row['size']:
                raw = os.pread(fd, min(self.chunk_bytes, row['size'] - offset), offset)
                _require(type(raw) is bytes and 0 < len(raw) <= min(self.chunk_bytes, row['size'] - offset))
                digest.update(raw); offset += len(raw)
                yield raw
                _require(self.active)
            _require(not os.pread(fd, 1, offset))
            _require(_signature(os.fstat(fd)) == stamp and digest.hexdigest() == row['sha256'])
            if owned is not None:
                _require(_signature(os.stat(key, dir_fd=self.media_fd, follow_symlinks=False)) == stamp)
            self.consumed.add(name)
        except (OSError, ValueError, KeyError, TypeError):
            raise FileError() from None
        finally:
            if owned is not None and owned in self.owned_fds:
                self.owned_fds.remove(owned)
                os.close(owned)

    def verify(self):
        try:
            _require(self.active and self.consumed == set(self.records))
            self._inventory()
            _require(self._directory() == self.directory_before)
            for name, before in self.initial.items():
                if name.startswith('media/'):
                    info = os.stat(name.split('/', 1)[1], dir_fd=self.media_fd, follow_symlinks=False)
                else:
                    info = os.fstat(self.file_fds[name])
                _require(_signature(info) == before)
        except (OSError, ValueError):
            raise FileError() from None

    def __exit__(self, exc_type, exc, traceback):
        self.active = False
        while self.owned_fds:
            os.close(self.owned_fds.pop())
        return False
