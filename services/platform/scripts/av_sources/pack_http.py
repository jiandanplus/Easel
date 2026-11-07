"""Bounded single-response HTTP/1.1 parsing, with no ambient transport.

Dependencies are supplied by the caller. A guard is only a callable here; this
module cannot authenticate it or establish a deadline. JSON and mux admission,
transport identity, cancellation and publication belong to the caller.
"""
from dataclasses import dataclass, replace
import re


class HTTPError(ValueError):
    """Fixed safe failure; never includes peer or dependency content."""


_SAFE = "invalid HTTP response"
_PRESETS = {
    "metadata": (131072, 163840),
    "stdout": (65536, 98304),
    "artifact": (16809985, 16842752),
}
_DEFAULT = object()
_COMMON = dict(overhead_bytes=32767, header_bytes=16384, header_fields=64,
               line_bytes=8192, status_bytes=1024, chunks=8192,
               chunk_size_digits=16, read_bytes=65536)


@dataclass(frozen=True)
class ResponseLimits:
    """A selected preset, optionally tightened. All byte caps include CRLF.

    Omitted caps use the selected preset, never a different preset's maxima.
    tighten() preserves that selection and may only reduce existing caps.
    """
    preset: str = "metadata"
    body_bytes: int = _DEFAULT
    wire_bytes: int = _DEFAULT
    overhead_bytes: int = _DEFAULT
    header_bytes: int = _DEFAULT
    header_fields: int = _DEFAULT
    line_bytes: int = _DEFAULT
    status_bytes: int = _DEFAULT
    chunks: int = _DEFAULT
    chunk_size_digits: int = _DEFAULT
    read_bytes: int = _DEFAULT

    def __post_init__(self):
        try:
            if type(self.preset) is not str or self.preset not in _PRESETS:
                raise HTTPError(_SAFE)
            body, wire = _PRESETS[self.preset]
            maxima = dict(_COMMON, body_bytes=body, wire_bytes=wire)
            for name, maximum in maxima.items():
                value = getattr(self, name)
                if value is _DEFAULT:
                    value = maximum
                if type(value) is not int or not 0 < value <= maximum:
                    raise HTTPError(_SAFE)
                object.__setattr__(self, name, value)
            if not (self.body_bytes <= self.wire_bytes and
                    self.overhead_bytes <= self.wire_bytes and
                    self.status_bytes <= self.line_bytes <= self.header_bytes <= self.overhead_bytes):
                raise HTTPError(_SAFE)
        except BaseException:
            pass
        else:
            return
        raise HTTPError(_SAFE)

    def tighten(self, **caps):
        try:
            if any(name == "preset" or name not in _COMMON and name not in ("body_bytes", "wire_bytes")
                   for name in caps):
                raise HTTPError(_SAFE)
            for name, value in caps.items():
                if type(value) is not int or not 0 < value <= getattr(self, name):
                    raise HTTPError(_SAFE)
            return replace(self, **caps)
        except BaseException:
            pass
        raise HTTPError(_SAFE)


METADATA_LIMITS = ResponseLimits()
STDOUT_LIMITS = ResponseLimits(preset="stdout")
ARTIFACT_LIMITS = ResponseLimits(preset="artifact")


@dataclass(frozen=True)
class Response:
    """Body and sanitized numeric/framing facts only; no acceptance marker."""
    body: bytes
    status: int
    framing: str
    wire_bytes: int
    overhead_bytes: int
    header_bytes: int
    header_fields: int
    chunks: int


_MEDIA = frozenset(("application/json", "application/vnd.docker.raw-stream",
                    "application/vnd.docker.multiplexed-stream"))
_TOKEN = re.compile(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_STATUS = re.compile(rb"HTTP/1[.]1 ([0-9]{3}) [\x20-\x7e\t]*")
_DECIMAL = re.compile(rb"(?:0|[1-9][0-9]*)")
_HEX = re.compile(rb"[0-9a-fA-F]+")


class _Receiver:
    def __init__(self, read, guard, limits):
        self.read, self.guard, self.limits = read, guard, limits
        self.wire = self.overhead = self.headers = self.fields = self.chunks = 0
        self.body = bytearray()

    def take(self, n, *, header=False, body=False, eof=False):
        caps = self.limits
        if type(n) is not int or not 0 < n <= caps.read_bytes:
            raise HTTPError(_SAFE)
        if not eof:
            if self.wire + n > caps.wire_bytes:
                raise HTTPError(_SAFE)
            if body:
                if len(self.body) + n > caps.body_bytes:
                    raise HTTPError(_SAFE)
            elif self.overhead + n > caps.overhead_bytes:
                raise HTTPError(_SAFE)
            if header and self.headers + n > caps.header_bytes:
                raise HTTPError(_SAFE)
        self.guard()
        try:
            data = self.read(n)
        finally:
            self.guard()
        if type(data) is not bytes or len(data) > n:
            raise HTTPError(_SAFE)
        self.wire += len(data)
        if not body:
            self.overhead += len(data)
        if header:
            self.headers += len(data)
        if eof:
            # A one-byte, unretained EOF probe is necessary even at an exact
            # wire cap. Any returned byte rejects; it never enters the body.
            if data:
                raise HTTPError(_SAFE)
        elif not data:
            raise HTTPError(_SAFE)
        return data

    def line(self, cap, *, header=False):
        line = bytearray()
        while True:
            if len(line) >= cap:
                raise HTTPError(_SAFE)
            value = self.take(1, header=header)
            if value == b"\n":
                if not line or line[-1] != 13:
                    raise HTTPError(_SAFE)
                return bytes(line[:-1])
            if line and line[-1] == 13:
                raise HTTPError(_SAFE)
            line.extend(value)

    def payload(self, length):
        if (length > self.limits.body_bytes - len(self.body) or
                length > self.limits.wire_bytes - self.wire):
            raise HTTPError(_SAFE)
        remaining = length
        while remaining:
            data = self.take(min(remaining, self.limits.read_bytes), body=True)
            self.body.extend(data)
            remaining -= len(data)

    def run(self, expected_status, content_types, on_status):
        caps = self.limits
        status_line = self.line(caps.status_bytes, header=True)
        match = _STATUS.fullmatch(status_line)
        if match is None:
            raise HTTPError(_SAFE)
        status = int(match[1])
        if not 100 <= status <= 599:
            raise HTTPError(_SAFE)
        if on_status is not None:
            self.guard()
            try:
                on_status(status)
            finally:
                self.guard()
        if status != expected_status:
            raise HTTPError(_SAFE)
        seen, selected = set(), {}
        while True:
            line = self.line(2 if self.fields >= caps.header_fields else caps.line_bytes, header=True)
            if not line:
                break
            self.fields += 1
            if self.fields > caps.header_fields:
                raise HTTPError(_SAFE)
            name, separator, value = line.partition(b":")
            if not separator or _TOKEN.fullmatch(name) is None:
                raise HTTPError(_SAFE)
            name = name.lower()
            if name in seen or any(c != 9 and not 32 <= c <= 126 for c in value):
                raise HTTPError(_SAFE)
            seen.add(name)
            if (name in (b"content-encoding", b"upgrade") or
                    name == b"connection" and b"upgrade" in
                    (part.strip(b" \t").lower() for part in value.split(b","))):
                raise HTTPError(_SAFE)
            if name in (b"content-type", b"content-length", b"transfer-encoding"):
                selected[name] = value.strip(b" \t")
        length = selected.get(b"content-length")
        transfer = selected.get(b"transfer-encoding")
        media = selected.get(b"content-type")
        if media is not None:
            media = media.split(b";", 1)[0].strip(b" \t").decode("ascii").lower()
            if media not in content_types:
                raise HTTPError(_SAFE)
        elif expected_status != 204:
            raise HTTPError(_SAFE)
        if length is not None and transfer is not None:
            raise HTTPError(_SAFE)
        if length is not None and _DECIMAL.fullmatch(length) is None:
            raise HTTPError(_SAFE)
        if expected_status == 204:
            if transfer is not None or length not in (None, b"0"):
                raise HTTPError(_SAFE)
            framing = "none" if length is None else "content-length"
        elif length is not None:
            framing = "content-length"
            # Bound decimal conversion too; huge syntactically valid lengths
            # reject before allocating or attempting a body read.
            if len(length) > len(str(caps.body_bytes)):
                raise HTTPError(_SAFE)
            self.payload(int(length))
        elif transfer is not None and transfer.lower() == b"chunked":
            framing = "chunked"
            while True:
                if self.chunks >= caps.chunks:
                    raise HTTPError(_SAFE)
                self.chunks += 1
                token = self.line(min(caps.line_bytes, caps.chunk_size_digits + 2))
                if not token or len(token) > caps.chunk_size_digits or _HEX.fullmatch(token) is None:
                    raise HTTPError(_SAFE)
                size = int(token, 16)
                if size == 0:
                    if self.line(caps.line_bytes):
                        raise HTTPError(_SAFE)
                    break
                # Reserve the current chunk's mandatory CRLF before payload.
                if (size + 2 > caps.wire_bytes - self.wire or
                        2 > caps.overhead_bytes - self.overhead):
                    raise HTTPError(_SAFE)
                self.payload(size)
                if self.take(1) != b"\r" or self.take(1) != b"\n":
                    raise HTTPError(_SAFE)
        else:
            raise HTTPError(_SAFE)
        self.take(1, eof=True)
        return Response(bytes(self.body), expected_status, framing, self.wire,
                        self.overhead, self.headers, self.fields, self.chunks)


def read_response(read, *, guard, expected_status, content_types, limits, on_status=None):
    """Read exactly one explicitly framed response, requiring a final EOF.

    Read/guard must be supplied. All failures, including catchable BaseException,
    become a new fixed HTTPError outside the handling context. Requests are
    positive and at most 65536 bytes. No headers or peer reason text escape.
    """
    try:
        if (not callable(read) or not callable(guard) or
                on_status is not None and not callable(on_status) or type(expected_status) is not int or
                expected_status not in (200, 201, 204) or type(content_types) is not tuple or
                not content_types or any(type(item) is not str or item not in _MEDIA
                                         for item in content_types) or
                type(limits) is not ResponseLimits):
            raise HTTPError(_SAFE)
        # Validate even a frozen instance supplied by an untrusted caller.
        checked = ResponseLimits(**vars(limits))
        return _Receiver(read, guard, checked).run(expected_status, content_types, on_status)
    except BaseException:
        pass
    raise HTTPError(_SAFE)
