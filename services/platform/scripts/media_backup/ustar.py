"""A bounded, canonical USTAR stream codec with no extension processing.

Only ordinary files are supported. The caller supplies the trusted member set
and expected physical length; this layer validates raw headers and byte framing.
IO objects must be finite blocking streams returning explicit progress counts.
"""

from contextlib import contextmanager
import hashlib
import re


class TarError(ValueError):
    """Invalid archive framing, resource limits, state, or IO progress."""


_MAX_FILE = 4 * 1024**3
_MAX_ARCHIVE = 32 * 1024**3
_MAX_CHUNK = 1024**2
_COMPONENT = re.compile(r"[A-Za-z0-9._-]+", re.ASCII)


def _integer(value, maximum, *, zero=False):
    if type(value) is not int or value < (0 if zero else 1) or value > maximum:
        raise TarError("invalid integer limit")
    return value


def _view(raw):
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        raise TarError("invalid bytes-like IO value")
    try:
        return memoryview(raw).cast("B")
    except (TypeError, ValueError) as exc:
        raise TarError("invalid bytes-like IO layout") from exc


def encode_header(name, size):
    """Encode one ordinary file header, using only the canonical wire form."""
    _integer(size, _MAX_FILE, zero=True)
    if (not isinstance(name, str) or not 1 <= len(name) <= 100
            or any(part in ("", ".", "..") or not _COMPONENT.fullmatch(part)
                   for part in name.split("/"))):
        raise TarError("invalid archive member name")
    raw = bytearray(512)
    encoded = name.encode("ascii")
    raw[:len(encoded)] = encoded
    raw[100:108] = b"0000600\0"
    raw[108:124] = b"0000000\0" * 2
    raw[124:136] = f"{size:011o}\0".encode("ascii")
    raw[136:148] = b"00000000000\0"
    raw[148:156] = b" " * 8
    raw[156:157] = b"0"
    raw[257:265] = b"ustar\x0000"
    raw[148:156] = f"{sum(raw):06o}\0 ".encode("ascii")
    return bytes(raw)


def decode_header(raw):
    """Validate every raw field before exposing the member's size or name."""
    view = _view(raw)
    if len(view) != 512:
        raise TarError("invalid raw header length")
    raw = bytes(view)
    size_raw = raw[124:135]
    if not all(48 <= byte <= 55 for byte in size_raw) or raw[135] != 0:
        raise TarError("invalid header size")
    try:
        name = raw[:100].split(b"\0", 1)[0].decode("ascii")
        size = int(size_raw, 8)
    except (ValueError, UnicodeError) as exc:
        raise TarError("invalid header encoding") from exc
    if raw != encode_header(name, size):
        raise TarError("noncanonical archive header")
    return name, size


def _write_all(sink, raw, chunk_bytes):
    """Perform bounded writes, including to sinks that accept short writes."""
    view = _view(raw)
    offset = 0
    while offset < len(view):
        part = view[offset:offset + chunk_bytes]
        written = sink.write(part)
        if type(written) is not int or not 1 <= written <= len(part):
            raise TarError("invalid sink progress")
        offset += written


class _Stream:
    def __init__(self, max_bytes, chunk_bytes):
        self.max_bytes = _integer(max_bytes, _MAX_ARCHIVE)
        self.chunk_bytes = _integer(chunk_bytes, _MAX_CHUNK)
        self.count = 0
        self._done = False
        self._failed = False

    def _budget(self, size):
        if size > self.max_bytes - self.count:
            raise TarError("archive byte budget exceeded")

    @contextmanager
    def _operation(self):
        if self._done or self._failed:
            raise TarError("archive stream is closed or failed")
        try:
            yield
        except BaseException:
            self._failed = True
            raise


class TarWriter(_Stream):
    """Write bounded chunks into an archive of an exact trusted physical size."""

    def __init__(self, sink, total_size, max_bytes, chunk_bytes):
        super().__init__(max_bytes, chunk_bytes)
        self.total_size = _integer(total_size, self.max_bytes)
        if self.total_size < 10240 or self.total_size % 10240:
            raise TarError("invalid physical archive size")
        self.sink = sink

    def _write(self, raw):
        view = _view(raw)
        self._budget(len(view))
        if len(view) > self.total_size - self.count:
            raise TarError("physical archive size exceeded")
        offset = 0
        while offset < len(view):
            part = view[offset:offset + self.chunk_bytes]
            written = self.sink.write(part)
            if type(written) is not int or not 1 <= written <= len(part):
                raise TarError("invalid sink progress")
            self.count += written
            offset += written

    def _zeros(self, size):
        block = bytes(min(size, self.chunk_bytes))
        while size:
            n = min(size, len(block))
            self._write(memoryview(block)[:n])
            size -= n

    def add(self, name, size, chunks):
        """Stream one member and return its lowercase SHA256 digest."""
        with self._operation():
            header = encode_header(name, size)
            end = self.count + 512 + size + (-size) % 512 + 1024
            if end + (-end) % 10240 > self.total_size:
                raise TarError("member exceeds physical archive size")
            self._write(header)
            digest = hashlib.sha256()
            consumed = 0
            for chunk in chunks:
                view = _view(chunk)
                if not 1 <= len(view) <= self.chunk_bytes or len(view) > size - consumed:
                    raise TarError("invalid member chunk size")
                self._write(view)
                digest.update(view)
                consumed += len(view)
            if consumed != size:
                raise TarError("member body size mismatch")
            self._zeros((-size) % 512)
            return digest.hexdigest()

    def finish(self):
        """Write exactly two zero blocks and the required record padding."""
        with self._operation():
            footer = 1024 + (-(self.count + 1024)) % 10240
            if self.count + footer != self.total_size:
                raise TarError("physical archive size mismatch")
            self._zeros(footer)
            self._done = True
            return self.count


class TarReader(_Stream):
    """Read one raw canonical header/body at a time; never expand extensions."""

    def __init__(self, source, max_bytes, chunk_bytes):
        super().__init__(max_bytes, chunk_bytes)
        self.source = source
        self._pending = None

    def _read_exact(self, size):
        self._budget(size)
        data = bytearray()
        while len(data) < size:
            requested = min(size - len(data), self.chunk_bytes)
            view = _view(self.source.read(requested))
            if not 1 <= len(view) <= requested:
                raise TarError("truncated archive or invalid source progress")
            data.extend(view)
            self.count += len(view)
        return data

    def _zeroes(self, size):
        self._budget(size)
        while size:
            amount = min(size, self.chunk_bytes)
            if any(self._read_exact(amount)):
                raise TarError("nonzero archive padding")
            size -= amount

    def header(self):
        """Return a validated header; the previous body must be consumed first."""
        with self._operation():
            if self._pending is not None:
                raise TarError("unconsumed member body")
            name, size = decode_header(self._read_exact(512))
            self._pending = size
            return name, size

    def body(self, size, sink):
        """Copy the exact pending member to a sink and check its zero padding."""
        with self._operation():
            _integer(size, _MAX_FILE, zero=True)
            if self._pending is None or size != self._pending:
                raise TarError("pending member size mismatch")
            padding = (-size) % 512
            self._budget(size + padding)
            digest = hashlib.sha256()
            remaining = size
            while remaining:
                raw = self._read_exact(min(remaining, self.chunk_bytes))
                _write_all(sink, raw, self.chunk_bytes)
                digest.update(raw)
                remaining -= len(raw)
            self._zeroes(padding)
            self._pending = None
            return digest.hexdigest()

    def finish(self, expected_total):
        """Check the exact canonical footer, record padding and a one-byte EOF."""
        with self._operation():
            _integer(expected_total, self.max_bytes)
            if self._pending is not None:
                raise TarError("unconsumed member body")
            footer = 1024 + (-(self.count + 1024)) % 10240
            if self.count + footer != expected_total:
                raise TarError("physical archive size mismatch")
            self._zeroes(footer)
            extra = _view(self.source.read(1))
            if len(extra):
                raise TarError("unexpected archive trailing bytes")
            self._done = True
            return self.count
