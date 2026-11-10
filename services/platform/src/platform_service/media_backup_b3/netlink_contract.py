"""Bounded little-endian Linux netlink DATA grammar, without I/O or policy.

Completion here is framing only. Sender identity, target binding, permitted
interfaces and isolation require independent transport/profile validation.
Unknown attributes and flag bits in typed headers remain uninterpreted data.
"""
from dataclasses import dataclass
import struct

from .contracts import B3Error


@dataclass(frozen=True, repr=False)
class NetlinkFrame:
    type: int
    flags: int
    sequence: int
    local_portid: int
    payload: bytes


@dataclass(frozen=True, repr=False)
class NetlinkMessage:
    type: int
    payload: bytes


@dataclass(frozen=True, repr=False)
class NetlinkAttribute:
    id: int
    flags: int
    payload: bytes


@dataclass(frozen=True, repr=False)
class Link:
    family: int
    pad: int
    type: int
    index: int
    flags: int
    change: int
    attributes: tuple[NetlinkAttribute, ...]


@dataclass(frozen=True, repr=False)
class Address:
    family: int
    prefixlen: int
    flags: int
    scope: int
    index: int
    attributes: tuple[NetlinkAttribute, ...]


@dataclass(frozen=True, repr=False)
class Route:
    family: int
    dst_len: int
    src_len: int
    tos: int
    table: int
    protocol: int
    scope: int
    route_type: int
    flags: int
    attributes: tuple[NetlinkAttribute, ...]


@dataclass(frozen=True, repr=False)
class Rule:
    family: int
    dst_len: int
    src_len: int
    tos: int
    table: int
    res1: int
    res2: int
    action: int
    flags: int
    attributes: tuple[NetlinkAttribute, ...]


def _require(condition):
    if not condition:
        raise B3Error("invalid B3 netlink data")


def parse_attributes(raw: bytes) -> tuple[NetlinkAttribute, ...]:
    """Parse one nonrecursive TLV region; nested/network-order flags are data."""
    _require(type(raw) is bytes and len(raw) <= 1024**2)
    result = []
    seen = set()
    offset = 0
    while offset < len(raw):
        _require(len(result) < 256 and len(raw) - offset >= 4)
        length, kind = struct.unpack_from("<HH", raw, offset)
        aligned = (length + 3) & ~3
        _require(length >= 4 and offset + aligned <= len(raw))
        _require(not any(raw[offset + length:offset + aligned]))
        base = kind & 0x3fff
        _require(base != 0 and base not in seen)
        seen.add(base)
        result.append(NetlinkAttribute(base, kind & 0xc000, raw[offset + 4:offset + length]))
        offset += aligned
    return tuple(result)


def parse_message(message: NetlinkMessage) -> Link | Address | Route | Rule:
    """Validate fixed header and TLVs even for a manually constructed message."""
    _require(type(message) is NetlinkMessage)
    _require(type(message.type) is int and message.type in (16, 20, 24, 32))
    _require(type(message.payload) is bytes and len(message.payload) <= 65520)
    kind, raw = message.type, message.payload
    size = 16 if kind == 16 else (8 if kind == 20 else 12)
    _require(len(raw) >= size)
    fmt = "<BBHiII" if kind == 16 else ("<BBBBI" if kind == 20 else "<BBBBBBBBI")
    fields = struct.unpack_from(fmt, raw)
    family = fields[0]
    if kind == 16:
        _require(family == 0 and fields[1] == 0 and fields[3] > 0)
        record = Link
    else:
        _require(family in (2, 10))
        prefix_limit = 32 if family == 2 else 128
        _require(fields[1] <= prefix_limit)
        if kind == 20:
            _require(fields[4] > 0)
            record = Address
        else:
            _require(fields[2] <= prefix_limit)
            if kind == 32:
                _require(fields[5] == 0 and fields[6] == 0)
            record = Route if kind == 24 else Rule
    return record(*fields, parse_attributes(raw[size:]))


def parse_datagram(packet: bytes) -> tuple[NetlinkFrame, ...]:
    """Validate one packet's framing only; control fields remain opaque data."""
    _require(type(packet) is bytes and 1 <= len(packet) <= 65536)
    result = []
    offset = 0
    while offset < len(packet):
        _require(len(result) < 4096 and len(packet) - offset >= 16)
        length, kind, flags, seq, pid = struct.unpack_from("<IHHII", packet, offset)
        aligned = (length + 3) & ~3
        _require(length >= 16 and offset + aligned <= len(packet))
        _require(not any(packet[offset + length:offset + aligned]))
        result.append(NetlinkFrame(kind, flags, seq, pid, packet[offset + 16:offset + length]))
        offset += aligned
    return tuple(result)


def parse_dump(request_type: int, family: int, sequence: int, local_portid: int,
               datagrams: tuple[bytes, ...]) -> tuple[NetlinkMessage, ...]:
    """Parse the six fixed dump selectors, preserving data without accepting policy.

    nlmsg_pid binds to the receiver's local port ID. This is distinct from the
    kernel sender address, which only a separate transport can establish.
    """
    _require(all(type(x) is int and 0 <= x < 2**32
                 for x in (request_type, family, sequence, local_portid)))
    _require(sequence > 0 and local_portid > 0)
    _require((request_type, family) in ((18, 0), (22, 0), (26, 2), (26, 10), (34, 2), (34, 10)))
    _require(type(datagrams) is tuple and 1 <= len(datagrams) <= 256)
    _require(all(type(p) is bytes and 1 <= len(p) <= 65536 for p in datagrams))
    _require(sum(map(len, datagrams)) <= 8 * 1024**2)
    result = []
    terminal = False
    for packet_index, packet in enumerate(datagrams):
        frames = parse_datagram(packet)
        for frame_index, frame in enumerate(frames):
            _require(not terminal)
            _require(frame.flags == 2 and frame.sequence == sequence and frame.local_portid == local_portid)
            if frame.type == 3:
                _require(frame.payload == b"\x00\x00\x00\x00")
                _require(packet_index == len(datagrams) - 1 and frame_index == len(frames) - 1)
                terminal = True
            else:
                _require(frame.type == request_type - 2 and len(result) < 4096)
                message = NetlinkMessage(frame.type, frame.payload)
                typed = parse_message(message)
                _require(request_type == 22 or typed.family == family)
                result.append(message)
    _require(terminal)
    return tuple(result)
