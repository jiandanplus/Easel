"""Offline SHA-1 pack loader with cooperative byte/work/time bounds.

Content integrity only: no provenance, signature, OS resource or egress proof.
Input path checks assume one trusted writer, not a hostile same-UID process.
"""
from collections import defaultdict, deque
from dataclasses import dataclass, fields
import hashlib
import math
import os
import re
import stat
import struct
import time
from types import MappingProxyType
import zlib


class PackError(ValueError):
    """Malformed, unavailable, changed or over-budget pack."""


@dataclass(frozen=True)
class PackLimits:
    max_pack_bytes: int = 64 * 1024 * 1024
    max_inflated_bytes: int = 128 * 1024 * 1024
    max_resolved_bytes: int = 128 * 1024 * 1024
    max_blob_bytes: int = 16 * 1024 * 1024
    max_delta_bytes: int = 16 * 1024 * 1024
    max_metadata_bytes: int = 4 * 1024 * 1024
    max_objects: int = 60001
    max_delta_depth: int = 64
    max_seconds: int = 600


_OID = re.compile(r'[0-9a-f]{40}\Z')
_TYPES = {1: 'commit', 2: 'tree', 3: 'blob', 4: 'tag'}


def _oid(value):
    if type(value) is not str or not _OID.fullmatch(value):
        raise PackError('expected canonical SHA-1 object ID')
    return value


@dataclass(frozen=True, slots=True)
class PackStore:
    _objects: object
    _summary: object

    def read_object(self, oid):
        _oid(oid)
        try:
            return self._objects[oid]
        except KeyError:
            raise PackError('object unavailable') from None

    @property
    def summary(self):
        return dict(self._summary)


class _Clock:
    def __init__(self, clock, seconds):
        self.clock = clock
        self.seconds = seconds
        self.start = self.last = None
        self.check()

    def check(self):
        value = self.clock()
        try:
            valid = type(value) in (int, float) and math.isfinite(value)
        except OverflowError:
            valid = False
        if not valid:
            raise PackError('nonfinite cooperative clock')
        if self.start is None:
            self.start = value
        elif value < self.last or value - self.start >= self.seconds:
            raise PackError('decreasing or expired cooperative clock')
        self.last = value


def _identity(s):
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns,
            s.st_mode, s.st_uid, s.st_nlink)


def _private_source(path, expected_size):
    # Walk every component using directory descriptors and O_NOFOLLOW.
    path = os.fspath(path)
    if type(path) is not str or not path or '\0' in path or '..' in path.split('/'):
        raise PackError('unsafe source path')
    parts = os.path.abspath(path).split('/')[1:]
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    parent = os.open('/', flags)
    try:
        for component in parts[:-1]:
            nxt = os.open(component, flags, dir_fd=parent)
            os.close(parent)
            parent = nxt
        info = os.fstat(parent)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise PackError('source parent must be owned 0700')
        before = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                or stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1
                or before.st_size != expected_size):
            raise PackError('source must be owned single-link 0600 regular pinned file')
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            if _identity(os.fstat(fd)) != _identity(before):
                raise PackError('source changed during open')
        except BaseException:
            os.close(fd)
            raise
        return fd, parent, parts[-1], before
    except BaseException:
        os.close(parent)
        raise


class _Reader:
    def __init__(self, fd, size, clock):
        self.fd, self.size, self.clock = fd, size, clock
        self.pos = self.fetched = 0
        self.buffer = b''
        self.sha256 = hashlib.sha256()
        self.sha1 = hashlib.sha1()

    def peek(self):
        self.clock.check()
        if not self.buffer and self.fetched < self.size:
            amount = min(65536, self.size - self.fetched)
            chunk = os.read(self.fd, amount)
            self.clock.check()
            if not chunk or len(chunk) > amount:
                raise PackError('truncated source read')
            self.sha256.update(chunk)
            body = min(len(chunk), max(0, self.size - 20 - self.fetched))
            self.sha1.update(chunk[:body])
            self.fetched += len(chunk)
            self.buffer = chunk
        return self.buffer

    def advance(self, count):
        self.pos += count
        self.buffer = self.buffer[count:]

    def exact(self, count):
        if count < 0 or count > self.size - self.pos:
            raise PackError('truncated pack')
        out = bytearray()
        while count:
            chunk = self.peek()
            if not chunk:
                raise PackError('truncated pack')
            n = min(count, len(chunk))
            out.extend(chunk[:n])
            self.advance(n)
            count -= n
        return bytes(out)

    def byte(self):
        if self.pos >= self.size - 20:
            raise PackError('truncated object header')
        return self.exact(1)[0]

    def inflate(self, size):
        obj = zlib.decompressobj()
        out = bytearray()
        while not obj.eof:
            self.clock.check()
            chunk = self.peek()[:max(0, self.size - 20 - self.pos)]
            if not chunk:
                raise PackError('truncated zlib stream')
            limit = min(65536, size - len(out) + 1)
            data = obj.decompress(chunk, limit)
            consumed = len(chunk) - len(obj.unused_data if obj.eof else obj.unconsumed_tail)
            self.advance(consumed)
            if len(data) > size - len(out):
                raise PackError('inflated size exceeded')
            out.extend(data)
            if not data and not consumed:
                raise PackError('zlib made no progress')
        if len(out) != size:
            raise PackError('inflated size mismatch')
        return bytes(out)



def _delta(program, base, cap, clock):
    cursor = 0

    def number():
        nonlocal cursor
        value = shift = 0
        while True:
            clock.check()
            if cursor >= len(program) or shift >= 64:
                raise PackError('truncated or overflowing delta varint')
            byte = program[cursor]
            cursor += 1
            value |= (byte & 127) << shift
            if value.bit_length() > 64:
                raise PackError('overflowing delta varint')
            if not byte & 128:
                return value
            shift += 7

    if number() != len(base):
        raise PackError('delta source size mismatch')
    target = number()
    if target > cap:
        raise PackError('delta target byte budget exceeded')
    out = bytearray()
    base_view = memoryview(base)
    while cursor < len(program):
        clock.check()
        opcode = program[cursor]
        cursor += 1
        if opcode & 128:
            offset = length = 0
            for bit in range(7):
                if opcode & (1 << bit):
                    if cursor >= len(program):
                        raise PackError('truncated delta copy')
                    value = program[cursor]
                    cursor += 1
                    if bit < 4:
                        offset |= value << (8 * bit)
                    else:
                        length |= value << (8 * (bit - 4))
            length = length or 65536
            if offset > len(base) or length > len(base) - offset:
                raise PackError('delta copy outside base')
            if length > target - len(out):
                raise PackError('delta output exceeds target')
            out.extend(base_view[offset:offset + length])
        elif opcode:
            if opcode > len(program) - cursor or opcode > target - len(out):
                raise PackError('delta insert exceeds input or target')
            out.extend(memoryview(program)[cursor:cursor + opcode])
            cursor += opcode
        else:
            raise PackError('reserved delta opcode zero')
    if len(out) != target:
        raise PackError('delta target size mismatch')
    return bytes(out)


def _resolve(entries, limits, clock):
    # Each entry is inflated once and scheduled/resolved at most once. REF bases
    # may be forward, including objects produced by another delta. Queue order
    # starts with all full objects, so identical REF bases use minimum depth.
    ready = deque()
    by_offset = defaultdict(list)
    by_oid = defaultdict(list)
    for index, (kind, _raw, base) in enumerate(entries):
        clock.check()
        if kind in _TYPES:
            ready.append((index, None))
        elif kind == 6:
            by_offset[base].append(index)
        else:
            by_oid[base].append(index)
    objects = {}
    results = {}
    total = max_depth = 0
    scheduled = set()
    while ready:
        clock.check()
        index, dependency = ready.popleft()
        kind, raw, _base = entries[index]
        if kind in _TYPES:
            name, depth = _TYPES[kind], 0
        else:
            name, base_raw, depth = dependency
            depth += 1
            if depth > limits.max_delta_depth:
                raise PackError('delta depth budget exceeded')
            cap = limits.max_blob_bytes if name == 'blob' else limits.max_metadata_bytes
            raw = _delta(raw, base_raw, min(cap, limits.max_resolved_bytes - total), clock)
        if len(raw) > limits.max_resolved_bytes - total:
            raise PackError('resolved byte budget exceeded')
        total += len(raw)
        max_depth = max(max_depth, depth)
        digest = hashlib.sha1(name.encode() + b' ' + str(len(raw)).encode() + b'\0')
        digest.update(raw)
        oid = digest.hexdigest()
        value = (name, raw)
        if oid in objects and objects[oid] != value:
            raise PackError('object identity collision')
        objects[oid] = value
        results[index] = (name, raw, depth)
        for child in by_offset.pop(index, ()):
            ready.append((child, results[index]))
        for child in by_oid.pop(oid, ()):
            if child not in scheduled:
                scheduled.add(child)
                ready.append((child, results[index]))
    if len(results) != len(entries):
        raise PackError('missing, thin, cyclic or unresolvable delta bases')
    return objects, total, max_depth

def load_pack(path, *, expected_size, expected_sha256, limits=PackLimits(), _clock=None):
    """Load a pinned private pack, with no writes or external base fallback.

    Limits only tighten positive exact integer ceilings; _clock is an explicit
    offline seam. The clock/decompression caps are cooperative, not isolation.
    """
    if type(limits) is not PackLimits:
        raise PackError('invalid limits')
    ceilings = PackLimits()
    for field in fields(limits):
        value = getattr(limits, field.name)
        if type(value) is not int or not 0 < value <= getattr(ceilings, field.name):
            raise PackError('invalid limit')
    if type(expected_size) is not int or not 0 < expected_size <= limits.max_pack_bytes:
        raise PackError('invalid pinned size')
    if type(expected_sha256) is not str or not re.fullmatch('[0-9a-f]{64}', expected_sha256):
        raise PackError('invalid pinned SHA256')
    clock = _Clock(time.monotonic if _clock is None else _clock, limits.max_seconds)
    fd = parent = None
    try:
        fd, parent, name, before = _private_source(path, expected_size)
        reader = _Reader(fd, expected_size, clock)
        magic, version, count = struct.unpack('>4sII', reader.exact(12))
        if magic != b'PACK' or version not in (2, 3) or not 1 <= count <= limits.max_objects:
            raise PackError('invalid pack header')
        entries = []
        boundaries = {}
        inflated = 0
        for _ in range(count):
            clock.check()
            offset = reader.pos
            first = reader.byte()
            kind = (first >> 4) & 7
            size, shift, byte = first & 15, 4, first
            while byte & 128:
                if shift >= 64:
                    raise PackError('object header overflow')
                byte = reader.byte()
                size |= (byte & 127) << shift
                shift += 7
            if size.bit_length() > 64 or kind not in (*_TYPES, 6, 7):
                raise PackError('invalid object type or size')
            base = None
            if kind == 6:
                byte = reader.byte()
                distance = byte & 127
                digits = 1
                while byte & 128:
                    if digits >= 10:
                        raise PackError('OFS reference overflow')
                    byte = reader.byte()
                    distance = ((distance + 1) << 7) | (byte & 127)
                    digits += 1
                base = boundaries.get(offset - distance)
                if distance == 0 or base is None:
                    raise PackError('OFS reference is not a prior entry boundary')
            elif kind == 7:
                if reader.pos + 20 > expected_size - 20:
                    raise PackError('truncated REF reference')
                base = reader.exact(20).hex()
            cap = (limits.max_delta_bytes if kind in (6, 7) else
                   limits.max_blob_bytes if kind == 3 else limits.max_metadata_bytes)
            if size > cap or size > limits.max_inflated_bytes - inflated:
                raise PackError('inflated byte budget exceeded')
            inflated += size
            raw = reader.inflate(size)
            boundaries[offset] = len(entries)
            entries.append((kind, raw, base))
        if reader.pos != expected_size - 20:
            raise PackError('pack has extra or unconsumed bytes')
        trailer = reader.exact(20)
        if reader.sha1.digest() != trailer or reader.sha256.hexdigest() != expected_sha256:
            raise PackError('pack checksum or pin mismatch')
        if (_identity(os.fstat(fd)) != _identity(before)
                or _identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) != _identity(before)):
            raise PackError('source changed during parse')
        objects, resolved, max_depth = _resolve(entries, limits, clock)
        if (_identity(os.fstat(fd)) != _identity(before)
                or _identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) != _identity(before)):
            raise PackError('source changed during resolution')
        clock.check()
        return PackStore(MappingProxyType(objects), MappingProxyType({
            'pack_bytes': expected_size, 'pack_sha256': expected_sha256,
            'pack_checksum_sha1': trailer.hex(), 'objects_declared': count,
            'objects_resolved': count, 'unique_objects': len(objects),
            'inflated_bytes': inflated, 'resolved_bytes': resolved, 'max_delta_depth': max_depth,
            'clock_injected_for_tests': _clock is not None,
            'source_identity_verified': False, 'build_inputs_adopted': False,
            'os_resource_limits_verified': False, 'dns_ip_egress_verified': False}))
    except (OSError, zlib.error, struct.error, TypeError) as exc:
        raise PackError('source or pack decoding failed') from exc
    finally:
        if fd is not None:
            os.close(fd)
        if parent is not None:
            os.close(parent)
