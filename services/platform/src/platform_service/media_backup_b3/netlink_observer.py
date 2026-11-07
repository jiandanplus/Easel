"""Fixed Linux route-netlink observation, without policy or runtime authority.

Only the original owned socket is used and closed. Returned immutable bytes
record transport observations; they do not establish isolation or allowance.
The worker process lifecycle remains responsible for unresolved native cleanup.
"""
from dataclasses import dataclass
from socket import socket as _Socket
from select import select as _select
from time import monotonic_ns as _monotonic_ns
from sys import platform as _platform, byteorder as _byteorder
import struct

from .contracts import B3Error
from .netlink_contract import NetlinkMessage, parse_datagram, parse_dump


@dataclass(frozen=True, repr=False)
class NetworkDump:
    request_type: int
    family: int
    sequence: int
    local_portid: int
    datagrams: tuple[bytes, ...]
    parsedmessages: tuple[NetlinkMessage, ...]


def _require(condition):
    if not condition:
        raise B3Error("invalid B3 network observation")


def _remaining(deadline_ns):
    try:
        remaining = deadline_ns - _monotonic_ns()
    except Exception:
        raise B3Error("invalid B3 network observation") from None
    _require(remaining > 0)
    return remaining


def _wait(owned, deadline_ns, *, writing):
    remaining = _remaining(deadline_ns)
    read, write, errors = _select([] if writing else [owned],
                                  [owned] if writing else [], [], remaining / 1_000_000_000)
    _remaining(deadline_ns)
    _require(errors == [] and (write == [owned] and read == [] if writing
                              else read == [owned] and write == []))


def observe_linux_network(deadline_ns: int) -> tuple[NetworkDump, ...]:
    """Read six fixed complete dumps within the caller's original deadline.

    There is no retry, deadline renewal, caller selector or socket injection.
    Failures are generic and never yield a partially completed observation.
    """
    owned = None
    try:
        _require(type(deadline_ns) is int and 0 < deadline_ns < 2**63)
        _require(_platform == "linux" and _byteorder == "little")
        _require(_remaining(deadline_ns) <= 60_000_000_000)
        # Retain the original object before constructor initialization can fail.
        # Linux UAPI AF_NETLINK=16, SOCK_RAW=3, NETLINK_ROUTE=0.
        original_type = _Socket
        _remaining(deadline_ns)
        owned = original_type.__new__(original_type)
        _remaining(deadline_ns)
        original_type.__init__(owned, 16, 3, 0)
        _remaining(deadline_ns)
        owned.setblocking(False)
        _remaining(deadline_ns)
        owned.bind((0, 0))
        _remaining(deadline_ns)
        address = owned.getsockname()
        _remaining(deadline_ns)
        _require(type(address) is tuple and len(address) == 2)
        portid, groups = address
        _require(type(portid) is int and 0 < portid < 2**32 and type(groups) is int and groups == 0)
        records = []
        total_bytes = 0
        for sequence, (request_type, family) in enumerate(
                ((18, 0), (22, 0), (26, 2), (26, 10), (34, 2), (34, 10)), 11):
            size = 16 if request_type == 18 else (8 if request_type == 22 else 12)
            request = struct.pack("<IHHII", 16 + size, request_type, 0x301, sequence, portid)
            request += bytes([family]) + bytes(size - 1)
            _wait(owned, deadline_ns, writing=True)
            _remaining(deadline_ns)
            sent = owned.sendto(request, (0, 0))
            _remaining(deadline_ns)
            _require(type(sent) is int and sent == len(request))
            packets = []
            while True:
                _wait(owned, deadline_ns, writing=False)
                _remaining(deadline_ns)
                packet, ancillary, flags, sender = owned.recvmsg(65536, 0)
                _remaining(deadline_ns)
                _require(type(packet) is bytes and 1 <= len(packet) <= 65536)
                _require(ancillary == [] and type(flags) is int and flags == 0)
                _require(type(sender) is tuple and len(sender) == 2 and
                         all(type(x) is int and x == 0 for x in sender))
                _require(len(packets) < 256 and total_bytes + len(packet) <= 8 * 1024**2)
                frames = parse_datagram(packet)
                packets.append(packet)
                total_bytes += len(packet)
                if any(frame.type == 3 for frame in frames):
                    break
            datagrams = tuple(packets)
            messages = parse_dump(request_type, family, sequence, portid, datagrams)
            records.append(NetworkDump(request_type, family, sequence, portid, datagrams, messages))
        _remaining(deadline_ns)
    except Exception:
        raise B3Error("invalid B3 network observation") from None
    finally:
        if owned is not None:
            # An expired deadline must not skip local owned-descriptor cleanup.
            # Any close/check failure still forbids a positive return.
            invalid_cleanup = False
            try:
                try:
                    _remaining(deadline_ns)
                except Exception:
                    invalid_cleanup = True
            finally:
                # Even a BaseException in the pre-close check must attempt close.
                try:
                    try:
                        owned.close()
                    except Exception:
                        invalid_cleanup = True
                finally:
                    try:
                        _remaining(deadline_ns)
                    except Exception:
                        invalid_cleanup = True
            if invalid_cleanup:
                raise B3Error("invalid B3 network observation") from None
    _remaining(deadline_ns)
    return tuple(records)
