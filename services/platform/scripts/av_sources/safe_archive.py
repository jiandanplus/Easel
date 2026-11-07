"""Pinned, bounded, offline ordinary tar extraction. No member is executed.

PAX, GNU extensions, links and sparse files are deliberately unsupported.
GNU ordinary headers are supported. Extraction requires a caller-controlled,
effective-UID-owned 0700 destination parent. The caller must exclude concurrent
same-UID mutation; POSIX mkdir/open cannot provide that isolation. Directory
identity checks detect observed replacement, not arbitrary same-UID attacks.

Only single compression streams are supported. gzip/xz structure shares the
tar metadata budget; bzip2 conservatively charges ALL compressed bytes as
metadata because its bit-level coding tables are not exposed by the stdlib.
"""
from dataclasses import dataclass
import bz2
import hashlib
import lzma
import os
import re
import stat
import tempfile
import unicodedata
import zlib


class ArchiveError(ValueError):
    """Invalid archive, unsafe filesystem or exhausted budget."""


@dataclass(frozen=True)
class ArchiveLimits:
    max_archive_bytes: int = 128 * 1024 * 1024
    max_expanded_bytes: int = 544 * 1024 * 1024
    max_metadata_bytes: int = 8 * 1024 * 1024
    max_file_bytes: int = 16 * 1024 * 1024
    max_total_file_bytes: int = 512 * 1024 * 1024
    max_members: int = 30000
    max_depth: int = 64


def _open_parent(path):
    # Inspect original spelling before any normalization can erase symlink/...
    raw = os.fspath(path)
    if not isinstance(raw, str) or not raw or "\0" in raw or any(p in (".", "..") for p in raw.split("/")):
        raise ArchiveError("noncanonical filesystem path")
    if not raw.startswith("/"):
        raw = os.getcwd() + "/" + raw
    parts = raw.split("/")[1:]
    if any(not p for p in parts): raise ArchiveError("noncanonical filesystem path")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = child
        return fd, parts[-1]
    except BaseException:
        os.close(fd)
        raise


def _snapshot(s):
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns


def _source_fd(path):
    parent, name = _open_parent(path)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    finally:
        os.close(parent)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ArchiveError("source must be regular")
    return fd


def _validate_source(path):
    try:
        fd = _source_fd(path)
        try: return _snapshot(os.fstat(fd))
        finally: os.close(fd)
    except OSError as exc:
        raise ArchiveError(str(exc)) from exc


class _Metadata:
    def __init__(self, limit): self.remaining = limit

    def charge(self, size):
        if size > self.remaining: raise ArchiveError("metadata budget")
        self.remaining -= size

    def read(self, stream, size):
        self.charge(size)
        data = stream.read(size)
        if len(data) != size: raise ArchiveError("truncated compression metadata")
        return data


def _xz_metadata(stream, size, metadata):
    """Boundedly locate single-stream xz block structures using its index."""
    header = metadata.read(stream, 12)
    if size < 32: raise ArchiveError("truncated xz")
    stream.seek(size - 12)
    footer = metadata.read(stream, 12)
    if footer[10:] != b"YZ" or footer[8:10] != header[6:8]:
        raise ArchiveError("unsupported xz stream layout")
    index_size = (int.from_bytes(footer[4:8], "little") + 1) * 4
    index_start = size - 12 - index_size
    if index_start < 12: raise ArchiveError("invalid xz index size")
    stream.seek(index_start)
    index = metadata.read(stream, index_size)
    if not index or index[0] != 0 or zlib.crc32(index[:-4]) != int.from_bytes(index[-4:], "little"):
        raise ArchiveError("invalid xz index")
    pos = 1
    def vli():
        nonlocal pos
        value = 0
        for shift in range(0, 63, 7):
            if pos >= len(index) - 4: raise ArchiveError("truncated xz index integer")
            b = index[pos]; pos += 1; value |= (b & 127) << shift
            if not b & 128:
                if shift and b == 0: raise ArchiveError("noncanonical xz index integer")
                return value
        raise ArchiveError("oversized xz index integer")
    blocks = vli(); offset = 12
    # At least two one-byte integers per block; never trust an index loop count.
    if blocks > (len(index) - pos - 4) // 2: raise ArchiveError("invalid xz block count")
    check_size = {0: 0, 1: 4, 4: 8, 10: 32}.get(header[7])
    if header[6] or check_size is None: raise ArchiveError("unsupported xz flags")
    for _ in range(blocks):
        unpadded = vli(); vli()  # uncompressed size is enforced by the tar stream
        if offset >= index_start: raise ArchiveError("invalid xz block offset")
        stream.seek(offset)
        first = metadata.read(stream, 1)
        header_size = (first[0] + 1) * 4
        if first == b"\0" or unpadded < header_size + check_size or offset + unpadded > index_start:
            raise ArchiveError("invalid xz block size")
        metadata.read(stream, header_size - 1)
        metadata.charge(check_size + (-unpadded) % 4)
        offset += (unpadded + 3) // 4 * 4
    if offset != index_start or index[pos:-4].strip(b"\0"):
        raise ArchiveError("unsupported xz stream layout")
    stream.seek(0)


class _Decoded:
    """Incremental decoder: never materialize more output than requested."""
    def __init__(self, stream, size, metadata):
        self.stream = stream; self.pending = b""; self.done = False
        self.crc = 0; self.size = 0
        magic = stream.read(6); stream.seek(0)
        if magic.startswith(b"\x1f\x8b"):
            self.kind = "gzip"
            header = metadata.read(stream, 10)
            if header[2] != 8 or header[3] & 224: raise ArchiveError("unsupported gzip header")
            crc = zlib.crc32(header)
            if header[3] & 4:
                length = metadata.read(stream, 2); crc = zlib.crc32(length, crc)
                crc = zlib.crc32(metadata.read(stream, int.from_bytes(length, "little")), crc)
            for flag in (8, 16):
                if header[3] & flag:
                    while True:
                        byte = metadata.read(stream, 1); crc = zlib.crc32(byte, crc)
                        if byte == b"\0": break
            if header[3] & 2 and int.from_bytes(metadata.read(stream, 2), "little") != crc & 65535:
                raise ArchiveError("bad gzip header CRC")
            metadata.charge(8)  # fixed trailer, validated after DEFLATE EOF
            self.decoder = zlib.decompressobj(-15)
        elif magic.startswith(b"BZh"):
            self.kind = "bzip2"; metadata.charge(size)
            self.decoder = bz2.BZ2Decompressor()
        elif magic.startswith(b"\xfd7zXZ\0"):
            self.kind = "xz"; _xz_metadata(stream, size, metadata)
            self.decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ)
        else:
            self.kind = "tar"; self.decoder = None

    def read(self, size):
        if self.done: return b""
        if self.decoder is None: return self.stream.read(size)
        result = bytearray()
        while len(result) < size and not self.done:
            if self.kind == "gzip":
                data = self.pending or self.stream.read(65536)
                chunk = self.decoder.decompress(data, size - len(result))
                self.pending = self.decoder.unconsumed_tail
                self.crc = zlib.crc32(chunk, self.crc); self.size += len(chunk)
            else:
                data = self.stream.read(65536) if self.decoder.needs_input else b""
                chunk = self.decoder.decompress(data, max_length=size - len(result))
            result.extend(chunk)
            if self.decoder.eof:
                tail = self.decoder.unused_data
                if self.kind == "gzip":
                    tail += self.stream.read(max(0, 8 - len(tail)))
                    if len(tail) != 8 or int.from_bytes(tail[:4], "little") != self.crc or int.from_bytes(tail[4:], "little") != self.size & 0xffffffff:
                        raise ArchiveError("bad gzip trailer or unsupported concatenation")
                elif tail:
                    raise ArchiveError("unsupported concatenated compression stream")
                if self.stream.read(1): raise ArchiveError("trailing compressed data")
                self.done = True
            elif not data and not chunk:
                raise ArchiveError("truncated compression stream")
        return bytes(result)


class _TarStream:
    def __init__(self, decoded, spool, limit):
        self.decoded = decoded; self.spool = spool; self.limit = limit; self.total = 0

    def read(self, size):
        data = self.decoded.read(min(size, self.limit - self.total + 1))
        self.total += len(data)
        if self.total > self.limit: raise ArchiveError("expanded budget")
        self.spool.write(data)
        return data


def _number(raw):
    if raw[0] & 128:
        raise ArchiveError("base256 numeric extension")
    v = raw.rstrip(b"\0 ").lstrip(b" ")
    if v and any(c not in b"01234567" for c in v):
        raise ArchiveError("invalid octal field")
    return int(v or b"0", 8)


def _string(raw):
    head, _, tail = raw.partition(b"\0")
    if tail.strip(b"\0"):
        raise ArchiveError("embedded NUL")
    try:
        return head.decode("utf-8", "strict")
    except UnicodeError as exc:
        raise ArchiveError("non UTF8 name") from exc


def _load(source, *, archive_size, sha256, limits):
    if not isinstance(limits, ArchiveLimits) or any(type(v) is not int or v < 0 for v in vars(limits).values()):
        raise ArchiveError("explicit valid limits required")
    if type(archive_size) is not int or archive_size < 0 or archive_size > limits.max_archive_bytes:
        raise ArchiveError("archive size budget")
    if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ArchiveError("invalid SHA256 pin")
    snapshot = _validate_source(source)
    if snapshot[2] != archive_size:
        raise ArchiveError("size mismatch")
    compressed = expanded = None
    success = False
    try:
        compressed = tempfile.TemporaryFile()
        expanded = tempfile.TemporaryFile()
        fd = _source_fd(source)
        with os.fdopen(fd, "rb") as f:
            s = os.fstat(f.fileno())
            if _snapshot(s) != snapshot:
                raise ArchiveError("source changed")
            h = hashlib.sha256(); count = 0
            while True:
                chunk = f.read(min(65536, archive_size - count + 1))
                if not chunk: break
                count += len(chunk)
                if count > archive_size: raise ArchiveError("archive grew")
                h.update(chunk); compressed.write(chunk)
            if count != archive_size or h.hexdigest() != sha256:
                raise ArchiveError("size or SHA256 mismatch")
        compressed.seek(0)
        metadata = _Metadata(limits.max_metadata_bytes)
        tar = _TarStream(_Decoded(compressed, archive_size, metadata), expanded, limits.max_expanded_bytes)
        entries = []; seen = {}; required_dirs = set(); file_bytes = 0; files = 0; dirs = 0
        while True:
            first = tar.read(1)
            if not first: raise ArchiveError("truncated tar")
            if first != b"\0":
                # One detection byte distinguishes a member from zero end padding;
                # reserve its complete header before consuming the other 511 bytes.
                metadata.charge(512)
                if len(entries) >= limits.max_members: raise ArchiveError("member budget")
            block = first + tar.read(511)
            if len(block) != 512: raise ArchiveError("truncated tar")
            if block == bytes(512):
                if tar.read(512) != bytes(512): raise ArchiveError("missing tar end")
                while True:
                    padding = tar.read(65536)
                    if not padding: break
                    if padding.strip(b"\0"): raise ArchiveError("trailing archive data")
                break
            if first == b"\0": raise ArchiveError("invalid empty member name")
            if _number(block[148:156]) != sum(block[:148]) + 256 + sum(block[156:]):
                raise ArchiveError("bad header checksum")
            gnu = block[257:265] == b"ustar  \0"
            if block[257:263] not in (b"ustar\0", bytes(6)) and not gnu:
                raise ArchiveError("unsupported tar format")
            if gnu:
                # GNU ordinary layout has atime/ctime here, NOT USTAR prefix.
                _number(block[345:357]); _number(block[357:369])
                if block[369:].strip(b"\0"):
                    raise ArchiveError("unsupported GNU extended/sparse layout")
            kind = block[156:157]
            if kind not in (b"0", b"\0", b"5"): raise ArchiveError("unsupported member type")
            name = _string(block[:100]); prefix = "" if gnu else _string(block[345:500])
            if prefix: name = prefix + "/" + name
            if kind == b"5" and name.endswith("/"): name = name[:-1]
            parts = name.split("/")
            if not name or any(p in ("", ".", "..") for p in parts) or "\\" in name or len(parts) > limits.max_depth:
                raise ArchiveError("unsafe path or depth")
            key = unicodedata.normalize("NFC", name).casefold()
            if key in seen: raise ArchiveError("duplicate or normalized collision")
            ancestors = ["/".join(key.split("/")[:i]) for i in range(1, len(parts))]
            if any(seen.get(parent) == "file" for parent in ancestors) or key in required_dirs and kind != b"5":
                raise ArchiveError("file directory conflict")
            required_dirs.update(ancestors)
            mode = _number(block[100:108]); size = _number(block[124:136])
            if mode & ~0o1777 or mode & 0o6000: raise ArchiveError("privileged or unsupported mode")
            directory = kind == b"5"
            if directory and size: raise ArchiveError("directory data")
            if size > limits.max_file_bytes: raise ArchiveError("file budget")
            file_bytes += size
            if file_bytes > limits.max_total_file_bytes: raise ArchiveError("total file budget")
            offset = expanded.tell(); digest = hashlib.sha256(); remaining = size
            while remaining:
                chunk = tar.read(min(65536, remaining))
                if not chunk: raise ArchiveError("truncated file")
                remaining -= len(chunk); digest.update(chunk)
            pad = (-size) % 512
            if len(tar.read(pad)) != pad: raise ArchiveError("truncated padding")
            entry = dict(path=name, type="directory" if directory else "file", size=size, mode=mode,
                         sha256=None if directory else digest.hexdigest())
            entries.append((entry, offset)); seen[key] = entry["type"]
            dirs += directory; files += not directory
        if _validate_source(source) != snapshot: raise ArchiveError("source changed")
        manifest = dict(entries=[e for e, _ in entries], file_count=files, directory_count=dirs,
                        archive_size=archive_size, archive_sha256=sha256)
        expanded.seek(0)
        success = True
        return expanded, entries, manifest, snapshot
    except (OSError, EOFError, ValueError, lzma.LZMAError, zlib.error) as exc:
        if isinstance(exc, ArchiveError): raise
        raise ArchiveError(str(exc)) from exc
    finally:
        if compressed is not None: compressed.close()
        if not success and expanded is not None: expanded.close()


def inspect_archive(source, *, archive_size, sha256, limits):
    """Return a JSON serializable manifest without creating a destination."""
    spool, _, manifest, _ = _load(source, archive_size=archive_size, sha256=sha256, limits=limits)
    spool.close()
    return manifest


def _private_directory(s):
    if not stat.S_ISDIR(s.st_mode) or s.st_uid != os.geteuid() or stat.S_IMODE(s.st_mode) != 0o700:
        raise ArchiveError("directory must be effective-UID-owned private 0700")


def _directory_identity(s):
    return s.st_dev, s.st_ino


def _checked_directory(parent_fd, name, expected):
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    try:
        current = os.fstat(fd)
        _private_directory(current)
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if _directory_identity(current) != expected or _directory_identity(named) != expected:
            raise ArchiveError("directory identity changed")
        return fd
    except BaseException:
        os.close(fd)
        raise


def extract_archive(source, destination, *, archive_size, sha256, limits):
    """Validate all bytes first, then exclusively materialize a new 0700 directory.

    Parent must be caller-controlled, effective-UID-owned 0700, with no concurrent
    same-UID mutator. Partial output is retained on failure. Executable bits are
    manifest only. The trust premise cannot be proven from POSIX permission bits.
    """
    spool, entries, manifest, snapshot = _load(source, archive_size=archive_size, sha256=sha256, limits=limits)
    descriptors = []
    try:
        parent_fd, destination_name = _open_parent(destination)
        descriptors.append(parent_fd)
        _private_directory(os.fstat(parent_fd))
        if _validate_source(source) != snapshot: raise ArchiveError("source changed")
        os.mkdir(destination_name, 0o700, dir_fd=parent_fd)
        root_identity = _directory_identity(os.stat(destination_name, dir_fd=parent_fd, follow_symlinks=False))
        root_fd = _checked_directory(parent_fd, destination_name, root_identity)
        descriptors.append(root_fd)
        owned_dirs = {(): root_identity}
        for entry, offset in entries:
            parts = entry["path"].split("/"); current = root_fd
            directories = parts if entry["type"] == "directory" else parts[:-1]
            local_fds = []
            try:
                for index, part in enumerate(directories):
                    key = tuple(parts[:index + 1])
                    if key not in owned_dirs:
                        os.mkdir(part, 0o700, dir_fd=current)
                        owned_dirs[key] = _directory_identity(os.stat(part, dir_fd=current, follow_symlinks=False))
                    current = _checked_directory(current, part, owned_dirs[key])
                    local_fds.append(current)
                if entry["type"] == "file":
                    fd = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=current)
                    with os.fdopen(fd, "wb") as out:
                        spool.seek(offset); remaining = entry["size"]
                        while remaining:
                            chunk = spool.read(min(65536, remaining))
                            if not chunk: raise ArchiveError("validated spool truncated")
                            out.write(chunk); remaining -= len(chunk)
            finally:
                for fd in reversed(local_fds): os.close(fd)
        if _validate_source(source) != snapshot: raise ArchiveError("source changed during extraction")
        _private_directory(os.fstat(parent_fd))
        named = os.stat(destination_name, dir_fd=parent_fd, follow_symlinks=False)
        if _directory_identity(named) != root_identity: raise ArchiveError("destination identity changed")
        _private_directory(named)
        return manifest
    except OSError as exc:
        raise ArchiveError(str(exc)) from exc
    finally:
        for fd in reversed(descriptors): os.close(fd)
        spool.close()
