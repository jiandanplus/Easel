"""Transient raw-credential output boundary, not an OS/same-user sandbox.

No credential history is persisted. Rotation/logout history lasts this process;
unknown token-only copies after restart and encoded/transformed data are outside
this boundary. Saturation fails closed instead of evicting still-sensitive values.
"""

import os
from pathlib import Path
import re
import stat
import tempfile
import threading

from easel.harnesses.base import HarnessError

_CHUNK = 64 * 1024
_MAX_VALUE = 65536
_MAX_VALUES = 256
_MAX_ROOTS = 512
_MAX_TOTAL = 8 * 1024 * 1024
_lock = threading.RLock()
_values = {}
_failed = set()
_total = 0
_saturated = False
# Platform raw JSON copies remain recognizable after restart. A bare key or
# redacted documentation example is harmless. Platform tokens use urlsafe text.
_AUTH_VALUE = re.compile(
    rb'"(?:access_token|refresh_token)"\s*:\s*"[A-Za-z0-9_.-]{16,65536}"'
)


def remember(root, values):
    global _total, _saturated
    key = str(Path(root).resolve())
    with _lock:
        if _saturated or key in _failed:
            raise HarnessError("敏感输出保护已达上限，请重启应用后重试")
        if key not in _values:
            if len(_values) >= _MAX_ROOTS:
                _saturated = True
                raise HarnessError("敏感输出保护已达上限，请重启应用后重试")
            _values[key] = set()
        current = _values[key]
        for value in values:
            if not isinstance(value, str) or not value or value in current:
                continue
            size = len(value.encode("utf-8"))
            if (
                size > _MAX_VALUE
                or len(current) >= _MAX_VALUES
                or _total + size > _MAX_TOTAL
            ):
                _failed.add(key)
                raise HarnessError("敏感输出保护已达上限，请重启应用后重试")
            current.add(value)
            _total += size


def sensitive_values(root):
    # Atomic bounded read only, no platform HTTP, no file creation or POSIX lock.
    from .session import SessionStore

    SessionStore(root).read()
    key = str(Path(root).resolve())
    with _lock:
        if _saturated or key in _failed:
            raise HarnessError("敏感输出保护不可用，请重启应用后重试")
        return tuple(_values.get(key, ()))


def remember_auth(root, state):
    auth = state.get("auth") or {}
    remember(root, (auth.get("access_token"), auth.get("refresh_token")))


def _directory_open_supported():
    return (
        os.open in os.supports_dir_fd
        and hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
    )


def _portable_stat(path):
    return path.lstat()


def _plain_stat(info, directory=False):
    return (
        (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
        and not stat.S_ISLNK(info.st_mode)
        and not getattr(info, "st_file_attributes", 0) & 0x400
    )


def _open_portable_regular(path, outputs):
    """Portable plain-file path (including Windows), with pinned identity checks.

    Reparse points/links or filesystems without stable identities fail closed.
    This is a safe plain-file compatibility path, not a hostile same-user race
    sandbox. The same immutable credential scan applies after opening.
    """
    chain = [*reversed(outputs.parents), outputs]
    relative = path.relative_to(outputs)
    chain.extend(
        outputs.joinpath(*relative.parts[:i]) for i in range(1, len(relative.parts))
    )
    before = [_portable_stat(parent) for parent in chain]
    target_before = _portable_stat(path)
    if not all(_plain_stat(info, directory=True) for info in before) or not _plain_stat(
        target_before
    ):
        raise OSError("unsafe output path")
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        opened = os.fstat(descriptor)
        after = [_portable_stat(parent) for parent in chain]
        target_after = _portable_stat(path)
        if (
            not _plain_stat(opened)
            or not _plain_stat(target_after)
            or not opened.st_ino
            or not os.path.samestat(target_before, opened)
            or not os.path.samestat(opened, target_after)
            or not all(
                _plain_stat(info, directory=True) and os.path.samestat(prior, info)
                for prior, info in zip(before, after)
            )
        ):
            raise OSError("changed output path")
        stream = os.fdopen(descriptor, "rb")
        descriptor = None
        return stream
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _open_regular(path, outputs):
    """Open each directory without following links, then pin one regular inode."""
    if not _directory_open_supported():
        return _open_portable_regular(path, outputs)
    relative = path.relative_to(outputs)
    descriptor = os.open(outputs, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in relative.parts[:-1]:
            following = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = following
        target = os.open(
            relative.name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=descriptor,
        )
        if not stat.S_ISREG(os.fstat(target).st_mode):
            os.close(target)
            raise OSError("nonregular output")
        return os.fdopen(target, "rb")
    finally:
        os.close(descriptor)


def _scan_raw(stream, secrets, limit=None):
    from fastapi import HTTPException

    overlap = max([_MAX_VALUE + 128, *(len(value) for value in secrets)])
    tail = b""
    count = 0
    while block := stream.read(_CHUNK):
        count += len(block)
        if limit is not None and count > limit:
            raise HTTPException(403, "压缩产物超过安全检查上限")
        content = tail + block
        if any(value in content for value in secrets) or _AUTH_VALUE.search(content):
            raise HTTPException(403, "产物含敏感凭证，不可通过产物接口访问")
        tail = content[-overlap:]
    return count


def _scan_archive(spool, secrets):
    """Inspect ordinary ZIP/Office diagnostic copies without extraction.

    Before ZipFile allocates its directory, bound classic ZIP's footer/directory.
    No ZIP64/encrypted/nested archive acceptance claim; fail closed for those.
    """
    from fastapi import HTTPException
    import struct
    import zipfile

    spool.seek(0)
    if not zipfile.is_zipfile(spool):
        return
    try:
        spool.seek(0, 2)
        size = spool.tell()
        spool.seek(max(0, size - 65557))
        footer = spool.read(65557)
        offset = footer.rfind(b"PK\x05\x06")
        if offset < 0:
            raise ValueError
        (
            _,
            disk,
            central_disk,
            disk_count,
            count,
            central_size,
            central_offset,
            comment,
        ) = struct.unpack_from("<4s4H2LH", footer, offset)
        if (
            disk
            or central_disk
            or count != disk_count
            or count > 1000
            or central_size > 2 * 1024 * 1024
            or central_offset == 0xFFFFFFFF
            or offset + 22 + comment != len(footer)
        ):
            raise ValueError
        spool.seek(0)
        with zipfile.ZipFile(spool) as archive:
            budget = 64 * 1024 * 1024
            for member in archive.infolist():
                if member.is_dir():
                    continue
                if member.flag_bits & 1 or member.file_size > budget:
                    raise ValueError
                if any(value in member.filename.encode("utf-8") for value in secrets):
                    raise ValueError
                with archive.open(member) as stream:
                    # A nested ZIP is another transformation layer: reject rather
                    # than silently present it as an inspected diagnostic archive.
                    beginning = stream.read(4)
                    if beginning.startswith(b"PK\x03\x04"):
                        raise ValueError
                with archive.open(member) as stream:
                    budget -= _scan_raw(stream, secrets, budget)
    except (
        ValueError,
        OSError,
        RuntimeError,
        NotImplementedError,
        zipfile.BadZipFile,
        EOFError,
        struct.error,
    ):
        raise HTTPException(403, "压缩产物无法安全检查") from None


def safe_snapshot(path, outputs):
    """Read into an unlinked private spool, scan it, serve precisely these bytes.

    Memory is bounded even for large media. No public path re-open after scanning;
    a concurrent rewrite is either rejected by inode metadata checks or included
    in the immutable scanned snapshot. Errors never fall back to an unchecked read.
    """
    from fastapi import HTTPException

    spool = tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")
    try:
        outputs = Path(outputs).resolve()
        if Path(tempfile.gettempdir()).resolve().is_relative_to(outputs):
            raise HTTPException(403, "临时快照目录不能位于公开产物目录")
        with _open_regular(Path(path), outputs) as source:
            before = os.fstat(source.fileno())
            while block := source.read(_CHUNK):
                spool.write(block)
            after = os.fstat(source.fileno())
            if (
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise HTTPException(409, "产物正在修改，请稍后重试")
        secrets = tuple(
            value.encode("utf-8") for value in sensitive_values(outputs.parent)
        )
        spool.seek(0)
        _scan_raw(spool, secrets)
        _scan_archive(spool, secrets)
        spool.seek(0)
        return spool
    except HTTPException:
        spool.close()
        raise
    except (OSError, ValueError, HarnessError):
        spool.close()
        raise HTTPException(403, "无法安全读取产物") from None


def snapshot_response(snapshot, name, range_header=""):
    """Single/multipart byte ranges over already scanned immutable bytes."""
    from fastapi import HTTPException
    from mimetypes import guess_type
    from starlette.responses import StreamingResponse
    import secrets

    class OwnedResponse(StreamingResponse):
        async def __call__(self, scope, receive, send):
            try:
                await super().__call__(scope, receive, send)
            finally:
                snapshot.close()  # completion, send error, or disconnect/cancellation

    snapshot.seek(0, 2)
    size = snapshot.tell()
    media = guess_type(name)[0] or "application/octet-stream"
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
    }
    ranges = [(0, size - 1)]
    status = 200
    if range_header:
        try:
            unit, values = range_header.split("=", 1)
            parts = values.split(",")
            if unit != "bytes" or not 1 <= len(parts) <= 16:
                raise ValueError
            ranges = []
            for part in parts:
                first, last = part.strip().split("-", 1)
                if not first:
                    count = int(last)
                    if count <= 0:
                        raise ValueError
                    start, end = max(0, size - count), size - 1
                else:
                    start, end = (
                        int(first),
                        min(int(last) if last else size - 1, size - 1),
                    )
                if start < 0 or start >= size or end < start:
                    raise ValueError
                ranges.append((start, end))
            status = 206
        except (ValueError, TypeError):
            snapshot.close()
            raise HTTPException(
                416, "无效的产物范围", headers={"Content-Range": f"bytes */{size}"}
            ) from None
    boundary = secrets.token_hex(16)

    def prefix(start, end):
        return (
            f"--{boundary}\r\nContent-Type: {media}\r\nContent-Range: bytes {start}-{end}/{size}\r\n\r\n"
        ).encode()

    if len(ranges) == 1:
        start, end = ranges[0]
        headers["Content-Length"] = str(max(0, end - start + 1))
        if status == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    else:
        headers["Content-Length"] = str(
            sum(len(prefix(start, end)) + end - start + 3 for start, end in ranges)
            + len(f"--{boundary}--\r\n".encode())
        )

    def content():
        for start, end in ranges:
            if len(ranges) > 1:
                yield prefix(start, end)
            snapshot.seek(start)
            left = max(0, end - start + 1)
            while left:
                block = snapshot.read(min(_CHUNK, left))
                if not block:
                    return
                left -= len(block)
                yield block
            if len(ranges) > 1:
                yield b"\r\n"
        if len(ranges) > 1:
            yield f"--{boundary}--\r\n".encode()

    return OwnedResponse(
        content(),
        status_code=status,
        headers=headers,
        media_type=media
        if len(ranges) == 1
        else f"multipart/byteranges; boundary={boundary}",
    )
