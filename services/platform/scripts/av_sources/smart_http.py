"""Bounded Git v0 framing for the two fixed AV source repositories.

This module has no network or subprocess entry points. A parsed pack receipt
verifies its wire container, not its objects, source identity or signature.
Protocol: https://git-scm.com/docs/gitprotocol-pack
HTTP framing: https://git-scm.com/docs/gitprotocol-http
"""
import hashlib
import re
import struct


class SourceTransportError(ValueError):
    pass


def _positive(value):
    if type(value) is not int or value < 1:
        raise SourceTransportError('invalid source limit')
    return value


def _oid(value):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{40}', value) is None:
        raise SourceTransportError('invalid source object id')
    return value


def pkt_line(body):
    if not isinstance(body, bytes) or not body or len(body) + 4 > 65520:
        raise SourceTransportError('invalid packet payload')
    return f'{len(body) + 4:04x}'.encode('ascii') + body


class _Reader:
    def __init__(self, source, limit, max_packets):
        self.source = source
        self.limit = _positive(limit)
        self.max_packets = _positive(max_packets)
        self.used = 0
        self.packets = 0

    def exact(self, size):
        if size > self.limit - self.used:
            raise SourceTransportError('response body limit exceeded')
        chunks = []
        remaining = size
        while remaining:
            try:
                part = self.source.read(remaining)
            except (OSError, ValueError):
                raise SourceTransportError('source read failed') from None
            if not isinstance(part, bytes) or not part or len(part) > remaining:
                raise SourceTransportError('truncated or invalid response')
            self.used += len(part)
            remaining -= len(part)
            chunks.append(part)
        return b''.join(chunks)

    def packet(self):
        self.packets += 1
        if self.packets > self.max_packets:
            raise SourceTransportError('packet count limit exceeded')
        header = self.exact(4)
        if re.fullmatch(b'[0-9a-f]{4}', header) is None:
            raise SourceTransportError('invalid packet header')
        length = int(header, 16)
        if length == 0:
            return None
        if length < 5 or length > 65520:
            raise SourceTransportError('unsupported packet length')
        return self.exact(length - 4)

    def eof(self):
        # Reserve a byte for an EOF probe. Exhausting the budget cannot prove
        # there is no trailing data, so the exact-cap case fails closed.
        if self.used >= self.limit:
            raise SourceTransportError('response EOF not proven within budget')
        try:
            extra = self.source.read(1)
        except (OSError, ValueError):
            raise SourceTransportError('source EOF check failed') from None
        if extra != b'':
            raise SourceTransportError('unexpected trailing response data')


def parse_advertisement(body, *, target, max_bytes=4 * 1024 * 1024):
    """Parse a complete v0 smart advertisement; require the fixed target."""
    import io
    _oid(target)
    if not isinstance(body, bytes) or len(body) >= _positive(max_bytes):
        raise SourceTransportError('advertisement body limit exceeded')
    reader = _Reader(io.BytesIO(body), max_bytes, 100000)
    first = reader.packet()
    if first is None or first.rstrip(b'\n') != b'# service=git-upload-pack':
        raise SourceTransportError('wrong advertised service')
    if reader.packet() is not None:
        raise SourceTransportError('missing service flush')
    refs = {}
    capabilities = None
    while True:
        packet = reader.packet()
        if packet is None:
            break
        packet = packet.removesuffix(b'\n')
        if capabilities is None:
            if packet.count(b'\0') != 1:
                raise SourceTransportError('missing first-ref capabilities')
            packet, caps = packet.split(b'\0')
            try:
                capabilities = caps.decode('ascii').split(' ')
            except UnicodeError:
                raise SourceTransportError('invalid capabilities') from None
            if any(not cap or any(ord(c) < 33 or ord(c) > 126 for c in cap)
                   for cap in capabilities):
                raise SourceTransportError('invalid capabilities')
        if b'\0' in packet:
            raise SourceTransportError('unexpected capability record')
        try:
            value, name = packet.decode('ascii').split(' ', 1)
        except (UnicodeError, ValueError):
            raise SourceTransportError('invalid advertised ref') from None
        _oid(value)
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]*(?:\^\{\})?', name):
            raise SourceTransportError('unsupported ref name')
        if name in refs:
            raise SourceTransportError('duplicate advertised ref')
        refs[name] = value
    reader.eof()
    required = {'side-band-64k', 'ofs-delta', 'shallow'}
    if capabilities is None or not required.issubset(capabilities):
        raise SourceTransportError('required capability unavailable')
    formats = [cap for cap in capabilities if cap.startswith('object-format=')]
    if formats and formats != ['object-format=sha1']:
        raise SourceTransportError('unsupported object format')
    if target not in refs.values():
        raise SourceTransportError('fixed target not advertised')
    return {'refs': refs, 'capabilities': capabilities, 'target_advertised': True}


def build_fetch_request(target, advertisement):
    _oid(target)
    if target not in advertisement.get('refs', {}).values():
        raise SourceTransportError('fixed target not advertised')
    if not {'side-band-64k', 'ofs-delta', 'shallow'}.issubset(
            advertisement.get('capabilities', [])):
        raise SourceTransportError('required capability unavailable')
    return (pkt_line(f'want {target} side-band-64k ofs-delta\n'.encode())
            + pkt_line(b'deepen 1\n') + b'0000' + pkt_line(b'done\n'))


def decode_pack_response(source, sink, *, target,
                         max_response_bytes=64 * 1024 * 1024,
                         max_pack_bytes=64 * 1024 * 1024,
                         max_packets=2000000, max_objects=60001):
    """Stream one depth-1 response into a caller-owned quarantine sink.

    The caller must retain a partial sink as failed, never as verified input.
    A separate bounded offline index/tree verifier is required before use.
    """
    _oid(target)
    _positive(max_pack_bytes)
    _positive(max_objects)
    reader = _Reader(source, max_response_bytes, max_packets)
    shallow = []
    while True:
        data = reader.packet()
        if data is None:
            break
        if data.removesuffix(b'\n') != f'shallow {target}'.encode() or shallow:
            raise SourceTransportError('unexpected shallow update')
        shallow.append(target)
    if reader.packet() not in (b'NAK', b'NAK\n'):
        raise SourceTransportError('unexpected negotiation result')
    sha256 = hashlib.sha256()
    checksum = hashlib.sha1()
    tail = b''
    header = b''
    total = progress = 0
    count = None
    while True:
        data = reader.packet()
        if data is None:
            break
        channel, chunk = data[0], data[1:]
        if channel == 2:
            progress += len(chunk)
            continue
        if channel != 1 or not chunk:
            raise SourceTransportError('upstream pack stream failed')
        if total + len(chunk) > max_pack_bytes:
            raise SourceTransportError('pack byte limit exceeded')
        if len(header) < 12:
            header = (header + chunk)[:12]
            if len(header) == 12:
                magic, version, count = struct.unpack('>4sII', header)
                if magic != b'PACK' or version not in (2, 3) or not 1 <= count <= max_objects:
                    raise SourceTransportError('invalid pack header')
        total += len(chunk)
        sha256.update(chunk)
        combined = tail + chunk
        if len(combined) > 20:
            checksum.update(combined[:-20])
            tail = combined[-20:]
        else:
            tail = combined
        try:
            written = sink.write(chunk)
        except (OSError, ValueError):
            raise SourceTransportError('pack sink write failed') from None
        if written != len(chunk):
            raise SourceTransportError('short pack sink write')
    reader.eof()
    if total < 32 or count is None or len(tail) != 20 or checksum.digest() != tail:
        raise SourceTransportError('pack checksum mismatch or truncation')
    return {'response_body_bytes': reader.used, 'pack_bytes': total,
            'pack_sha256': sha256.hexdigest(), 'pack_checksum_sha1_verified': True,
            'objects_declared': count, 'objects_verified': False,
            'progress_bytes': progress, 'shallow': shallow,
            'packets': reader.packets}
