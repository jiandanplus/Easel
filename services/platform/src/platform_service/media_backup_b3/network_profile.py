"""Pure, closed Linuxkit N3 DATA validation; never restore/target authority.

Tables are offline-compiled source literals. There is no runtime policy loader.
Raw bytes are reparsed by N1; cached records confer no trust.
"""
from dataclasses import dataclass, fields
import hashlib
import json

from .contracts import B3Error
from .netlink_contract import NetlinkMessage, parse_dump, parse_message, parse_attributes
from .netlink_observer import NetworkDump
from . import _network_policy as P


@dataclass(frozen=True, repr=False)
class NamespaceIdentity:
    device: int
    inode: int


@dataclass(frozen=True, repr=False)
class InterfaceEntry:
    name: str
    index: int
    iflink: int
    arphrd: int
    flags: int
    operstate: str
    address_bytes: bytes


@dataclass(frozen=True, repr=False)
class ControlEntry:
    name: str
    uid: int
    gid: int
    mode: int
    file_type: str = "regular"


@dataclass(frozen=True, repr=False)
class ProcViews:
    dev: bytes
    addresses6: bytes
    route4: bytes
    route6: bytes
    fib_trie: bytes


@dataclass(frozen=True, repr=False)
class NetworkObservation:
    kernel_release: str
    byteorder: str
    namespace_before: NamespaceIdentity
    namespace_after: NamespaceIdentity
    sysfs_before: tuple[InterfaceEntry | ControlEntry, ...]
    sysfs_after: tuple[InterfaceEntry | ControlEntry, ...]
    proc_before: ProcViews
    proc_after: ProcViews
    dumps: tuple[NetworkDump, ...]


@dataclass(frozen=True, repr=False)
class NetworkProfileResult:
    profile_id: str
    profile_sha256: str
    security_sha256: str
    canonical_security_json: bytes
    observed_interfaces: tuple[str, ...]
    active_interfaces: tuple[str, ...]
    sysfs_controls: tuple[ControlEntry, ...]
    raw_observation_sha256: str
    telemetry_json: bytes


_PROFILE_ID = "b3-linuxkit-6.12.76-inactive-v1"
_SLOTS = ((18, 0, 11), (22, 0, 12), (26, 2, 13), (26, 10, 14), (34, 2, 15), (34, 10, 16))
_MAXIMA = (10, 2, 3, 1, 3, 2)
_PROC_FIELDS = ("dev", "addresses6", "route4", "route6", "fib_trie")


def _require(ok):
    if not ok:
        raise B3Error("invalid B3 network profile")


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def _uint(value, bits, minimum=0):
    _require(type(value) is int and minimum <= value < 1 << bits)


def _namespace(value):
    _require(type(value) is NamespaceIdentity)
    _uint(value.device, 64, 1)
    _uint(value.inode, 64, 1)
    return {"device": value.device, "inode": value.inode}


def _attrs(actual, expected, telemetry, slot, record, path=()):
    # Only the four reviewed rule forms, over private immutable compiled tuples.
    _require(len(actual) == len(expected))
    rules = {x[0]: x for x in expected}
    result = []
    seen = set()
    for a in actual:
        _require(a.id in rules and a.id not in seen)
        seen.add(a.id)
        ident, flags, length, tag, data = rules[a.id]
        _require(a.flags == flags and len(a.payload) == length)
        here = path + (ident,)
        if tag == "fixed":
            _require(a.payload == data)
            value = {"tag": "hex", "hex": a.payload.hex()}
        elif tag == "children":
            value = {"tag": "children", "attributes": _attrs(parse_attributes(a.payload), data, telemetry, slot, record, here)}
        elif tag == "words":
            bits, fixed = data
            width = bits // 8
            words = [int.from_bytes(a.payload[i:i+width], "little") for i in range(0, length, width)]
            _require(len(words) == len(fixed) and all(f is None or w == f for w, f in zip(words, fixed)))
            value = {"tag": "words", "bits": bits, "values": list(fixed)}
            telemetry.append({"slot": slot, "record": record, "path": list(here), "bits": bits, "values": words})
        elif tag == "permanent_identity":
            _require(length == 16 and a.payload[0] & 3 == 2 and a.payload[6:] == bytes(10))
            value = {"tag": "hex", "hex": a.payload.hex()}
        else:
            _require(False)
        result.append({"id": ident, "flags": flags, "length": length, "value": value})
    return sorted(result, key=lambda x: x["id"])


def _header(record):
    result = {}
    for f in fields(record):
        if f.name != "attributes":
            key = "arphrd" if f.name == "type" else ("type" if f.name == "route_type" else f.name)
            result[key] = getattr(record, f.name)
    return result


def _netlink(dumps):
    _require(type(dumps) is tuple and len(dumps) == 6)
    total = 0
    metadata, groups, telemetry = [], [[], [], [], []], []
    link_rules = {x[0]: x for x in P.LINKS}
    names, indexes = set(), set()
    port = None
    for slot, d in enumerate(dumps):
        _require(type(d) is NetworkDump)
        for v in (d.request_type, d.family, d.sequence, d.local_portid):
            _uint(v, 32)
        _require((d.request_type, d.family, d.sequence) == _SLOTS[slot] and d.local_portid > 0)
        if port is None:
            port = d.local_portid
        _require(port == d.local_portid)
        _require(type(d.datagrams) is tuple and 1 <= len(d.datagrams) <= 256)
        for p in d.datagrams:
            _require(type(p) is bytes and 1 <= len(p) <= 65536)
            total += len(p)
            _require(total <= 8 * 1024**2)
        _require(type(d.parsedmessages) is tuple and len(d.parsedmessages) == _MAXIMA[slot])
        for m in d.parsedmessages:
            _require(type(m) is NetlinkMessage and type(m.type) is int and type(m.payload) is bytes and len(m.payload) <= 65520)
        messages = parse_dump(d.request_type, d.family, d.sequence, d.local_portid, d.datagrams)
        _require(messages == d.parsedmessages)
        metadata.append(dict(request_type=d.request_type, family=d.family, sequence=d.sequence, local_portid=d.local_portid, packet_count=len(d.datagrams)))
        seen = set()
        for ordinal, message in enumerate(messages):
            r = parse_message(message)
            header = _header(r)
            if slot == 0:
                name_attrs = [a for a in r.attributes if a.id == 3]
                _require(len(name_attrs) == 1)
                name = name_attrs[0].payload
                _require(2 <= len(name) <= 16 and name[-1:] == b"\0")
                name = name[:-1].decode("ascii")
                _require(name in link_rules and name not in names and r.index not in indexes)
                names.add(name)
                indexes.add(r.index)
                _, bounds, _, fixed, expected = link_rules[name]
                limits = dict(bounds)
                _require(limits["min"] <= r.index <= limits["max"])
                _require({k:v for k,v in header.items() if k != "index"} == dict(fixed))
                result = {"name": name, "header": header, "attributes": _attrs(r.attributes, expected, telemetry, slot, ordinal)}
                group = 0
            else:
                table = P.ADDRESSES if slot == 1 else (P.ROUTES if slot in (2, 3) else P.RULES)
                candidates = [x for x in table if dict(x[0]) == header]
                _require(len(candidates) == 1)
                # Equal route headers can only occur with different destinations;
                # the frozen profile headers already distinguish these records.
                fixed, expected = candidates[0]
                result = {"header": header, "attributes": _attrs(r.attributes, expected, telemetry, slot, ordinal)}
                group = 1 if slot == 1 else (2 if slot in (2,3) else 3)
            key = _json(result)
            _require(key not in seen)
            seen.add(key)
            groups[group].append(result)
    groups[0].sort(key=lambda x: x["name"])
    for group in groups[1:]:
        group.sort(key=_json)
    return groups, metadata, telemetry


def _sysfs(entries, links):
    _require(type(entries) is tuple and len(entries) == 11)
    actual, controls, raw = {}, [], []
    by_name = {x["name"]: x for x in links}
    rules = {x[0]: dict(x[2]) for x in P.LINKS}
    for e in entries:
        _require(type(e) in (InterfaceEntry, ControlEntry))
        _require(type(e.name) is str and 1 <= len(e.name) <= 15 and e.name not in actual)
        _require(e.name.isascii() and all(32 < ord(c) < 127 and c != "/" for c in e.name))
        actual[e.name] = e
        if type(e) is ControlEntry:
            for x in (e.uid, e.gid, e.mode):
                _uint(x, 32)
            _require(type(e.file_type) is str and e == ControlEntry("bonding_masters", 0, 0, 420, "regular"))
            controls.append(e)
            raw.append(dict(tag="control", name=e.name, uid=e.uid, gid=e.gid, mode=e.mode, file_type=e.file_type))
        else:
            for x in (e.index, e.iflink, e.arphrd, e.flags):
                _uint(x, 32)
            _require(type(e.operstate) is str and type(e.address_bytes) is bytes and len(e.address_bytes) <= 16)
            _require(e.name in by_name)
            link = by_name[e.name]
            h, rule = link["header"], rules[e.name]
            attrs = {a["id"]: a for a in link["attributes"]}
            _require(e.index == h["index"] and e.arphrd == h["arphrd"] and e.flags == rule["flags"] and e.iflink == rule["iflink"] and e.operstate == rule["operstate"])
            _require(e.address_bytes.hex() == attrs[1]["value"]["hex"])
            raw.append(dict(tag="interface", name=e.name, index=e.index, iflink=e.iflink, arphrd=e.arphrd, flags=e.flags, operstate=e.operstate, address_hex=e.address_bytes.hex()))
    _require(set(actual) == set(by_name) | {"bonding_masters"} and len(controls) == 1)
    return actual, tuple(controls), {"schema": 1, "entries": raw}


def _lines(raw):
    _require(type(raw) is bytes and 0 < len(raw) <= 65536 and raw.endswith(b"\n"))
    _require(all(c in (9,10) or 32 <= c <= 126 for c in raw))
    lines = raw[:-1].decode("ascii").split("\n")
    _require(all(line.strip(" \t") for line in lines))
    return lines


def _hex(token, width):
    _require(len(token) == width and all(c in "0123456789abcdef" for c in token))
    return int(token, 16)


def _proc(view):
    _require(type(view) is ProcViews)
    data = {key: _lines(getattr(view, key)) for key in _PROC_FIELDS}
    dev = data["dev"]
    _require(tuple(dev[:2]) == P.DEV_HEADER and len(dev) == 12)
    rows, names = [], set()
    for line in dev[2:]:
        _require(line.count(":") == 1)
        name, values = line.split(":")
        name = name.strip(" \t")
        tokens = values.split()
        _require(name in {x[0] for x in P.LINKS} and name not in names and len(tokens) == 16)
        names.add(name)
        numbers = []
        for token in tokens:
            _require(1 <= len(token) <= 20 and token.isascii() and token.isdigit() and (token == "0" or token[0] != "0"))
            number = int(token)
            _uint(number,64)
            numbers.append(number)
        rows.append({"name": name, "values": numbers})
    _require(len(data["addresses6"]) == 1)
    address = data["addresses6"][0].split()
    _require(len(address) == 6)
    nums = [_hex(x,w) for x,w in zip(address[:5],(32,2,2,2,2))]
    _require(nums == [1,1,128,16,128] and address[5] == "lo")
    _require(len(data["route4"]) == 1 and tuple(data["route4"][0].split()) == P.ROUTE4_HEADER)
    _require(len(data["route6"]) == 3)
    sentinels, locals_ = 0, 0
    route_telemetry = []
    for row, line in enumerate(data["route6"]):
        tokens = line.split()
        _require(len(tokens) == 10 and tokens[9] == "lo")
        values = [_hex(t,w) for t,w in zip(tokens[:9],(32,2,32,2,32,8,8,8,8))]
        dst,prefix,src,srcprefix,hop,metric,ref,use,flags = values
        _require(src == srcprefix == hop == use == 0)
        if (dst,prefix,metric,flags) == (0,0,4294967295,2097664):
            sentinels += 1
        else:
            _require((dst,prefix,metric,flags) == (1,128,0,2149580801))
            locals_ += 1
        route_telemetry.append({"row": row, "reference_count": ref})
    _require(sentinels == 2 and locals_ == 1)
    trie = data["fib_trie"]
    _require(len(trie) == 18)
    content = ("+-- 127.0.0.0/8 2 0 2", "+-- 127.0.0.0/31 1 0 0", "|-- 127.0.0.0", "/8 host LOCAL", "|-- 127.0.0.1", "/32 host LOCAL", "|-- 127.255.255.255", "/32 link BROADCAST")
    for start, section in ((0,"Main:"),(9,"Local:")):
        for line, indent, tokens in zip(trie[start:start+9],(0,2,5,8,11,8,11,5,8),(section,)+content):
            _require(len(line)-len(line.lstrip(" ")) == indent and not line[indent:].startswith("\t"))
            _require(line[indent:].split() == tokens.split())
    return {"dev": rows, "route6": route_telemetry}


def validate_network_profile(observation: NetworkObservation) -> NetworkProfileResult:
    """Validate DATA only, without I/O, clocks, native probes or target authority."""
    try:
        _require(type(observation) is NetworkObservation)
        o = observation
        _require(type(o.kernel_release) is str and o.kernel_release == "6.12.76-linuxkit")
        _require(type(o.byteorder) is str and o.byteorder == "little")
        before, after = _namespace(o.namespace_before), _namespace(o.namespace_after)
        _require(before == after)
        groups, slots, net_telemetry = _netlink(o.dumps)
        links, addresses, routes, rules = groups
        sb, controls, raw_before = _sysfs(o.sysfs_before, links)
        sa, _, raw_after = _sysfs(o.sysfs_after, links)
        _require(sb == sa)
        pb, pa = _proc(o.proc_before), _proc(o.proc_after)
        profile_hash = hashlib.sha256(b"B3-N3-POLICY-v1\0" + P.POLICY_BYTES).hexdigest()
        security = dict(schema=1, profile_id=_PROFILE_ID, profile_sha256=profile_hash, kernel_release=o.kernel_release, byteorder=o.byteorder, namespace=before, links=links, addresses=addresses, routes=routes, rules=rules, sysfs_controls=[dict(tag="control",name=e.name,uid=e.uid,gid=e.gid,mode=e.mode,file_type=e.file_type) for e in controls], proc=P.proc_security())
        canonical = _json(security)
        metadata = dict(schema=1,kernel_release=o.kernel_release,byteorder=o.byteorder,namespace_before=before,namespace_after=after,slots=slots)
        raw = hashlib.sha256(b"B3-N3-RAW-v1\0")
        def frame(value):
            raw.update(len(value).to_bytes(8,"little"))
            raw.update(value)
        frame(_json(metadata))
        for dump in o.dumps:
            for packet in dump.datagrams:
                frame(packet)
        for view in (o.proc_before,o.proc_after):
            for key in _PROC_FIELDS:
                frame(getattr(view,key))
        frame(_json(raw_before))
        frame(_json(raw_after))
        telemetry = _json(dict(schema=1,origin=metadata,netlink=net_telemetry,proc_before=pb,proc_after=pa))
        return NetworkProfileResult(_PROFILE_ID,profile_hash,hashlib.sha256(b"B3-N3-SECURITY-v1\0"+canonical).hexdigest(),canonical,tuple(x["name"] for x in links),("lo",),controls,raw.hexdigest(),telemetry)
    except Exception:
        raise B3Error("invalid B3 network profile") from None
