"""Import-only local file closure. No encryption, DB execution or scheduler.

Trusted caller supplies a complete captured plan and excludes concurrent source
and destination mutation. Streams must have bounded, finite IO. The receipts
verify files only; they never authenticate a snapshot window or restored DB.
"""
from contextlib import closing
import hashlib
import io
import os
import re
import stat

from .contract import ClosureError, Limits, decode_manifest, encode_manifest, member_records, physical_size, validate_plan
from .files import FileError, Sources
from .ustar import TarError, TarReader, TarWriter


def _require(value):
    if not value:
        raise ClosureError()


def _signature(info):
    return tuple(getattr(info, field) for field in (
        'st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns',
        'st_uid', 'st_gid', 'st_mode', 'st_nlink'))


def _private_directory(fd):
    info = os.fstat(fd)
    _require(stat.S_ISDIR(info.st_mode) and (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) ==
             (os.geteuid(), os.getegid(), 0o700))
    return info


def _identity(info):
    return info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode


class _HashSink:
    def __init__(self, sink):
        self.sink = sink; self.digest = hashlib.sha256()

    def write(self, raw):
        count = self.sink.write(raw)
        if type(count) is int and 0 < count <= len(raw):
            self.digest.update(memoryview(raw)[:count])
        return count


class _HashSource:
    def __init__(self, source):
        self.source = source; self.digest = hashlib.sha256()

    def read(self, size):
        raw = self.source.read(size)
        if isinstance(raw, (bytes, bytearray, memoryview)):
            view = memoryview(raw).cast('B')
            _require(len(view) <= size)
            self.digest.update(view)
        return raw


class _FDSink:
    def __init__(self, fd):
        self.fd = fd

    def write(self, raw):
        return os.write(self.fd, raw)


def _receipt(status, plan, archive_bytes, archive_sha, manifest):
    return dict(status=status, file_count=len(member_records(plan)),
                blob_count=sum(row['present'] for row in plan['blobs']), archive_bytes=archive_bytes,
                archive_sha256=archive_sha, manifest_sha256=hashlib.sha256(manifest).hexdigest(),
                registry_sha256=plan['registry_sha256'])


def write_archive(plan, media_fd, file_fds, sink, *, owner, limits=Limits()):
    """Stream a caller-controlled capture; a partial sink is never success."""
    try:
        plan = validate_plan(plan, limits)
        manifest = encode_manifest(plan, limits)
        output = _HashSink(sink)
        writer = TarWriter(output, physical_size(plan, limits), limits.max_archive_bytes, limits.chunk_bytes)
        with Sources(plan, media_fd, file_fds, owner, limits.chunk_bytes) as sources:
            writer.add('manifest.json', len(manifest),
                       (manifest[pos:pos + limits.chunk_bytes] for pos in range(0, len(manifest), limits.chunk_bytes)))
            for row in member_records(plan):
                with closing(sources.chunks(row)) as chunks:
                    _require(writer.add(row['name'], row['size'], chunks) == row['sha256'])
            sources.verify()
            count = writer.finish()
            sources.verify()
        return _receipt('closure_files_verified', plan, count, output.digest.hexdigest(), manifest)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
        raise ClosureError() from None


def _regular_source(source, total):
    try:
        fd = source.fileno()
    except (AttributeError, io.UnsupportedOperation):
        return None
    info = os.fstat(fd)
    _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid()
             and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size == total and source.tell() == 0)
    return fd, _signature(info)


def _stored_file(fd, row, chunk_bytes):
    before = os.fstat(fd)
    _require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size == row['size']
             and (before.st_uid, before.st_gid, stat.S_IMODE(before.st_mode)) == (os.geteuid(), os.getegid(), 0o600))
    digest = hashlib.sha256(); offset = 0
    while offset < row['size']:
        raw = os.pread(fd, min(chunk_bytes, row['size'] - offset), offset)
        _require(0 < len(raw) <= min(chunk_bytes, row['size'] - offset))
        digest.update(raw); offset += len(raw)
    _require(not os.pread(fd, 1, offset) and digest.hexdigest() == row['sha256'])
    _require(_signature(os.fstat(fd)) == _signature(before))
    return _signature(before)


def _exact_names(fd, expected):
    found = set()
    with os.scandir(fd) as entries:
        for entry in entries:
            _require(entry.name in expected and entry.name not in found)
            found.add(entry.name)
    _require(found == expected)


def restore_archive(source, parent_fd, name, expected_plan, *, limits=Limits()):
    """Restore into an exclusive private child; leave a failed stage isolated."""
    stage_fd = None; directories = {}
    try:
        plan = validate_plan(expected_plan, limits)
        manifest = encode_manifest(plan, limits); total = physical_size(plan, limits)
        _require(type(parent_fd) is int and parent_fd >= 0 and type(name) is str
                 and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', name) is not None)
        parent_before = _private_directory(parent_fd)
        bound_source = _regular_source(source, total)
        hashed = _HashSource(source)
        reader = TarReader(hashed, limits.max_archive_bytes, limits.chunk_bytes)
        _require(reader.header() == ('manifest.json', len(manifest)))
        raw_manifest = io.BytesIO()
        _require(reader.body(len(manifest), raw_manifest) == hashlib.sha256(manifest).hexdigest())
        _require(raw_manifest.getvalue() == manifest and decode_manifest(raw_manifest.getvalue(), limits) == plan)
        os.mkdir(name, 0o700, dir_fd=parent_fd)
        stage_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        os.fchmod(stage_fd, 0o700)
        stage_identity = _identity(_private_directory(stage_fd))
        for part in ('config', 'db', 'media'):
            os.mkdir(part, 0o700, dir_fd=stage_fd)
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=stage_fd)
            directories[part] = fd; os.fchmod(fd, 0o700); _private_directory(fd)
        identities = {part: _identity(os.fstat(fd)) for part, fd in directories.items()}
        stored = {}; expected_names = {part: set() for part in directories}
        for row in member_records(plan):
            _require(reader.header() == (row['name'], row['size']))
            part, basename = row['name'].split('/', 1)
            fd = os.open(basename, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK,
                         0o600, dir_fd=directories[part])
            try:
                os.fchmod(fd, 0o600)
                _require(reader.body(row['size'], _FDSink(fd)) == row['sha256'])
                os.fsync(fd)
                stored[row['name']] = _stored_file(fd, row, limits.chunk_bytes)
            finally:
                os.close(fd)
            expected_names[part].add(basename)
        count = reader.finish(total)
        if bound_source is not None:
            _require(_signature(os.fstat(bound_source[0])) == bound_source[1])
        _require(_identity(_private_directory(parent_fd)) == _identity(parent_before))
        _require(_identity(_private_directory(stage_fd)) == stage_identity)
        _require(_identity(os.stat(name, dir_fd=parent_fd, follow_symlinks=False)) == stage_identity)
        _exact_names(stage_fd, set(directories))
        for part, fd in directories.items():
            _require(_identity(_private_directory(fd)) == identities[part])
            _require(_identity(os.stat(part, dir_fd=stage_fd, follow_symlinks=False)) == identities[part])
            _exact_names(fd, expected_names[part]); os.fsync(fd)
        for member, stamp in stored.items():
            part, basename = member.split('/', 1)
            _require(_signature(os.stat(basename, dir_fd=directories[part], follow_symlinks=False)) == stamp)
        os.fsync(stage_fd); os.fsync(parent_fd)
        return _receipt('private_stage_restored_verified', plan, count, hashed.digest.hexdigest(), manifest)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
        raise ClosureError() from None
    finally:
        for fd in directories.values():
            os.close(fd)
        if stage_fd is not None:
            os.close(stage_fd)
