"""Closed pure raw-observation DATA adapter. No discovery or live authority."""
from dataclasses import asdict
from .contracts import B3Error
from . import network_profile as profile
from .netlink_contract import parse_dump
from .netlink_observer import NetworkDump

_PROC = ("dev", "addresses6", "route4", "route6", "fib_trie")
_TOP = ("kernel_release", "byteorder", "namespace_before", "namespace_after",
        "sysfs_before", "sysfs_after", "proc_before", "proc_after", "dumps")
_CONTROL = ("tag", "name", "uid", "gid", "mode", "file_type")
_INTERFACE = ("tag", "name", "index", "iflink", "arphrd", "flags", "operstate", "address_hex")
_DUMP = ("request_type", "family", "sequence", "local_portid", "datagrams")


def _require(ok):
    if not ok:
        raise B3Error("invalid B3 network wire")


def _closed(value, keys):
    _require(type(value) is dict and all(type(k) is str for k in value) and value.keys() == set(keys))


def _uint(value, bits=32, minimum=0):
    _require(type(value) is int and minimum <= value < 1 << bits)


def _text(value, maximum):
    _require(type(value) is str and len(value) <= maximum and value.isascii())


def _hex(value, maximum, minimum=0):
    # Bound characters BEFORE scanning or allocating decoded bytes.
    _require(type(value) is str and minimum*2 <= len(value) <= maximum*2 and len(value)%2 == 0)
    _require(all(c in "0123456789abcdef" for c in value))


def _preflight(v):
    _closed(v, _TOP)
    _text(v["kernel_release"], 256)
    _text(v["byteorder"], 6)
    for side in ("namespace_before", "namespace_after"):
        _closed(v[side], ("device", "inode"))
        for x in v[side].values(): _uint(x, 64, 1)
    for side in ("sysfs_before", "sysfs_after"):
        rows = v[side]
        _require(type(rows) is list and len(rows) == 11)
        for e in rows:
            _require(type(e) is dict and all(type(k) is str for k in e) and type(e.get("tag")) is str)
            _require(e["tag"] in ("control", "interface"))
            _closed(e, _CONTROL if e["tag"] == "control" else _INTERFACE)
            _text(e["name"], 15)
            if e["tag"] == "control":
                for k in ("uid", "gid", "mode"): _uint(e[k])
                _text(e["file_type"], 16)
            else:
                for k in ("index", "iflink", "arphrd", "flags"): _uint(e[k])
                _text(e["operstate"], 16)
                _hex(e["address_hex"], 16)
    for side in ("proc_before", "proc_after"):
        _closed(v[side], _PROC)
        for x in v[side].values(): _hex(x, 65536, 1)
    _require(type(v["dumps"]) is list and len(v["dumps"]) == 6)
    total = 0
    for d in v["dumps"]:
        _closed(d, _DUMP)
        for k in _DUMP[:-1]: _uint(d[k])
        _require(type(d["datagrams"]) is list and 1 <= len(d["datagrams"]) <= 256)
        for p in d["datagrams"]:
            _hex(p, 65536, 1)
            total += len(p)//2
            _require(total <= 8*1024**2)


def _decode(v):
    _preflight(v)  # Every field checked before bytes or records are allocated.
    def sysfs(side):
        rows = []
        for e in v[side]:
            args = {k:x for k,x in e.items() if k != "tag"}
            if e["tag"] == "control": rows.append(profile.ControlEntry(**args))
            else:
                args["address_bytes"] = bytes.fromhex(args.pop("address_hex"))
                rows.append(profile.InterfaceEntry(**args))
        return tuple(rows)
    dumps = []
    for d in v["dumps"]:
        args = tuple(d[k] for k in _DUMP[:-1])
        packets = tuple(bytes.fromhex(p) for p in d["datagrams"])
        dumps.append(NetworkDump(*args, packets, parse_dump(*args, packets)))
    o = profile.NetworkObservation(v["kernel_release"], v["byteorder"],
        profile.NamespaceIdentity(**v["namespace_before"]), profile.NamespaceIdentity(**v["namespace_after"]),
        sysfs("sysfs_before"), sysfs("sysfs_after"),
        profile.ProcViews(**{k:bytes.fromhex(v["proc_before"][k]) for k in _PROC}),
        profile.ProcViews(**{k:bytes.fromhex(v["proc_after"][k]) for k in _PROC}), tuple(dumps))
    profile.validate_network_profile(o)
    return o


def decode_network_observation(value):
    """Regenerate N1 caches from original bytes and independently revalidate N3."""
    try:
        return _decode(value)
    except Exception:
        raise B3Error("invalid B3 network wire") from None


def encode_network_observation(observation):
    """Preserve original packet/entry order and raw bytes; never trust a cache."""
    try:
        profile.validate_network_profile(observation)
        o = observation
        def sysfs(rows):
            result = []
            for e in rows:
                d = asdict(e)
                if type(e) is profile.InterfaceEntry:
                    d["address_hex"] = d.pop("address_bytes").hex()
                    d["tag"] = "interface"
                else: d["tag"] = "control"
                result.append(d)
            return result
        return dict(kernel_release=o.kernel_release, byteorder=o.byteorder,
            namespace_before=asdict(o.namespace_before), namespace_after=asdict(o.namespace_after),
            sysfs_before=sysfs(o.sysfs_before), sysfs_after=sysfs(o.sysfs_after),
            proc_before={k:getattr(o.proc_before,k).hex() for k in _PROC},
            proc_after={k:getattr(o.proc_after,k).hex() for k in _PROC},
            dumps=[dict(request_type=d.request_type, family=d.family, sequence=d.sequence,
                        local_portid=d.local_portid, datagrams=[p.hex() for p in d.datagrams]) for d in o.dumps])
    except Exception:
        raise B3Error("invalid B3 network wire") from None
