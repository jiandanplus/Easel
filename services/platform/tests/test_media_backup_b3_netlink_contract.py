"""Pure netlink grammar tests; the public fixture conveys no runtime authority."""
import ast
import base64
from dataclasses import FrozenInstanceError
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import struct

import pytest

from platform_service.media_backup_b3.contracts import B3Error

MODULE = "platform_service.media_backup_b3.netlink_contract"
FIXTURE = Path(__file__).parent / "fixtures/media_backup_b3/network/linuxkit-6.12.76-none-v1.public.json"


def parametrize(names, values):
    # Keep large malformed byte buffers out of test IDs and failure summaries.
    return pytest.mark.parametrize(names, values, ids=lambda value: type(value).__name__)


def api():
    assert importlib.util.find_spec(MODULE) is not None, "pure netlink parser is not implemented"
    return importlib.import_module(MODULE)


def test_shared_datagram_grammar_is_framing_only_and_redacted():
    n = api()
    assert hasattr(n, "parse_datagram"), "shared packet grammar missing"
    frames = n.parse_datagram(msg(1,b"secret",flags=65535,seq=0,pid=0)+done())
    assert (frames[0].type,frames[0].flags,frames[0].sequence,frames[0].local_portid,frames[0].payload) == (1,65535,0,0,b"secret")
    assert frames[1].type == 3
    assert "secret" not in repr(frames[0])
    with pytest.raises(FrozenInstanceError):
        frames[0].payload = b"changed"
    assert len(n.parse_datagram(msg(1,b"")*4096)) == 4096


@parametrize("raw", [None, bytearray(16), b"", b"x", bytes(16), struct.pack("<IHHII",17,1,0,0,0)+b"x", struct.pack("<IHHII",17,1,0,0,0)+b"x\0\0x", bytes(65537)])
def test_shared_datagram_rejects_invalid_framing(raw):
    n = api()
    assert hasattr(n, "parse_datagram"), "shared packet grammar missing"
    with pytest.raises(B3Error):
        n.parse_datagram(raw)


def msg(kind=16, payload=None, flags=2, seq=7, pid=9):
    if payload is None:
        payload = struct.pack("<BBHiII", 0, 0, 772, 1, 0x10049, 0)
    raw = struct.pack("<IHHII", 16 + len(payload), kind, flags, seq, pid) + payload
    return raw + bytes(-len(raw) % 4)


def done(**kw):
    return msg(3, struct.pack("<i", 0), **kw)


def attr(kind=1, payload=b"x"):
    raw = struct.pack("<HH", 4 + len(payload), kind) + payload
    return raw + bytes(-len(raw) % 4)


def test_empty_completed_dump_and_immutable_redacted_values():
    n = api()
    assert n.parse_dump(18, 0, 7, 9, (done(),)) == ()
    data = n.parse_dump(18, 0, 7, 9, (msg() + done(),))
    assert data == (n.NetlinkMessage(16, msg()[16:32]),)
    parsed = n.parse_message(data[0])
    assert (parsed.family, parsed.index, parsed.flags) == (0, 1, 0x10049)
    for value in (data[0], parsed, n.parse_attributes(attr())[0]):
        with pytest.raises(FrozenInstanceError):
            value.extra = "changed"
        assert "10049" not in repr(value) and "payload=" not in repr(value)


def test_public_fixture_all_six_dumps_preserve_headers_and_nested_bytes():
    n = api()
    raw = FIXTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == "269cea55bbe3139f77a6629462f0a11559b3e8efe3a5cd1171ab65b962b74584"
    dumps = json.loads(raw)["dumps"]
    expected = {(18, 0): 10, (22, 0): 2, (26, 2): 3, (26, 10): 1, (34, 2): 3, (34, 10): 2}
    kinds = []
    for d in dumps:
        packets = tuple(base64.b64decode(p, validate=True) for p in d["packets"])
        selector = (d["request_type"], d["family"])
        values = n.parse_dump(*selector, d["sequence"], d["local_portid"], packets)
        assert len(values) == expected[selector]
        typed = tuple(n.parse_message(v) for v in values)
        if selector == (18, 0):
            assert typed[0].flags == 0x10049
            for value in typed:
                for a in value.attributes:
                    if a.id == 18:
                        nested = n.parse_attributes(a.payload)
                        kinds.extend(x.payload for x in nested if x.id == 1)
                        assert b"".join(attr(x.id | x.flags, x.payload) for x in nested) == a.payload
    assert b"gretap\x00" in kinds and b"erspan\x00" in kinds


@parametrize("raw", [b"", b"x", bytes(16), msg()[:-1], msg() + b"x", msg() + done() + b"\0", done() + msg(), done() + done(), msg(), struct.pack("<IHHII", 15,16,2,7,9), struct.pack("<IHHII", 99,16,2,7,9) + bytes(16)])
def test_rejects_bad_framing_and_multipart_contract(raw):
    with pytest.raises(B3Error):
        api().parse_dump(18, 0, 7, 9, (raw,))


@parametrize("field,value", [("flags",0),("flags",0x12),("flags",0x22),("flags",0x202),("seq",8),("pid",0),("pid",10)])
@parametrize("terminal", [False, True])
def test_completed_dump_rejects_one_bad_header_field(field, value, terminal):
    n = api()
    assert len(n.parse_dump(18,0,7,9,(msg()+done(),))) == 1
    # The other message is valid and the final DONE is never missing/duplicated.
    packet = msg()+done(**{field:value}) if terminal else msg(**{field:value})+done()
    with pytest.raises(B3Error):
        n.parse_dump(18,0,7,9,(packet,))


@parametrize("kind,payload", [(1,b""),(2,b""),(4,b""),(20,struct.pack("<BBBBI",2,32,0,0,1)),(16,b"")])
def test_completed_dump_rejects_wrong_data_type_or_short_payload(kind,payload):
    with pytest.raises(B3Error):
        api().parse_dump(18,0,7,9,(msg(kind,payload)+done(),))


@parametrize("payload", [b"",struct.pack("<i",-1),bytes(8)])
def test_completed_dump_rejects_only_bad_done_payload(payload):
    with pytest.raises(B3Error):
        api().parse_dump(18,0,7,9,(msg()+msg(3,payload),))


def test_completed_dump_rejects_only_nonzero_attribute_padding():
    # Valid fixed link header and TLV; change only one alignment padding byte.
    payload = msg()[16:32] + attr(1,b"x")
    valid = msg(payload=payload)
    assert len(api().parse_dump(18,0,7,9,(valid+done(),))) == 1
    with pytest.raises(B3Error):
        api().parse_dump(18,0,7,9,(valid[:-1]+b"\1"+done(),))


def test_completed_dump_rejects_only_response_type():
    payload = struct.pack("<BBBBBBBBI",2,0,0,0,0,0,0,0,0)
    n = api()
    assert len(n.parse_dump(26,2,7,9,(msg(24,payload)+done(),))) == 1
    # The same bytes form a valid Rule with the same family: only the dump's
    # expected response type differs, so family/typed checks cannot mask it.
    with pytest.raises(B3Error):
        n.parse_dump(26,2,7,9,(msg(32,payload)+done(),))


@parametrize("args", [(True,0,7,9), (18,False,7,9), (18,0,True,9), (18,0,7,True), (18,0,0,9), (18,0,7,0), (18,0,-1,9), (18,0,2**32,9), (18,0,7,2**32), (18,2,7,9), (22,2,7,9), (26,0,7,9), (34,0,7,9), (19,0,7,9), ("18",0,7,9)])
def test_rejects_unapproved_selectors_and_non_u32_inputs(args):
    with pytest.raises(B3Error):
        api().parse_dump(*args, (done(),))


@parametrize("packets", [(), [], [done()], (bytearray(done()),), (memoryview(done()),), (None,), (b"",), (done(), done())])
def test_rejects_bad_packet_containers_and_limits(packets):
    with pytest.raises(B3Error):
        api().parse_dump(18, 0, 7, 9, packets)


def sized_link(size):
    """One valid framed message with an opaque TLV; size includes nlmsghdr."""
    return msg(payload=msg()[16:32] + attr(1,bytes(size-36)))


def test_completed_dump_packet_byte_limit_neighbors():
    n = api()
    prefix = sized_link(65504)
    inside = prefix + msg()
    outside = prefix + sized_link(36)
    assert len(inside) == 65536 and len(outside) == 65540
    assert len(n.parse_dump(18,0,7,9,(inside,done()))) == 2
    with pytest.raises(B3Error):
        n.parse_dump(18,0,7,9,(outside,done()))


def test_completed_dump_packet_count_limit_neighbors():
    n = api()
    assert len(n.parse_dump(18,0,7,9,(msg(),)*255+(done(),))) == 255
    with pytest.raises(B3Error):
        n.parse_dump(18,0,7,9,(msg(),)*256+(done(),))


def test_completed_dump_total_byte_limit_neighbors():
    n = api()
    prefix = (sized_link(65536),)*127
    inside = prefix + (sized_link(65516),done())
    outside = prefix + (sized_link(65520),done())
    assert sum(map(len,inside)) == 8*1024**2
    assert sum(map(len,outside)) == 8*1024**2+4
    assert len(n.parse_dump(18,0,7,9,inside)) == 128
    with pytest.raises(B3Error):
        n.parse_dump(18,0,7,9,outside)


def test_dump_message_count_limits():
    n = api()
    packet = msg()*2048
    assert len(n.parse_dump(18, 0, 7, 9, (packet, packet, done()))) == 4096
    with pytest.raises(B3Error):
        n.parse_dump(18, 0, 7, 9, (packet, packet, msg()+done()))


def test_attributes_preserve_flags_unknown_data_and_exact_bounds():
    n = api()
    raw = attr(0xc001, b"secret\x00") + attr(0x3fff, b"\xff")
    a, b = n.parse_attributes(raw)
    assert (a.id, a.flags, a.payload) == (1, 0xc000, b"secret\x00")
    assert (b.id, b.flags, b.payload) == (0x3fff, 0, b"\xff")
    assert "secret" not in repr(a)
    assert n.parse_attributes(b"") == ()
    assert len(n.parse_attributes(b"".join(attr(i, b"") for i in range(1,257)))) == 256


@parametrize("raw", [None, bytearray(), b"x", bytes(4), struct.pack("<HH",3,1), struct.pack("<HH",8,1), struct.pack("<HH",5,1)+b"x", attr()[:-1]+b"\1", attr(0x8000), attr(1)+attr(0x8001), b"".join(attr(i,b"") for i in range(1,258)), bytes(1024**2+1)])
def test_rejects_bad_attributes(raw):
    with pytest.raises(B3Error):
        api().parse_attributes(raw)


@parametrize("kind,payload", [(16,struct.pack("<BBHiII",0,1,1,1,0,0)),(16,struct.pack("<BBHiII",2,0,1,1,0,0)),(16,struct.pack("<BBHiII",0,0,1,0,0,0)),(16,struct.pack("<BBHiII",0,0,1,-1,0,0)),(20,struct.pack("<BBBBI",0,0,0,0,1)),(20,struct.pack("<BBBBI",2,33,0,0,1)),(20,struct.pack("<BBBBI",10,129,0,0,1)),(20,struct.pack("<BBBBI",2,32,0,0,0)),(24,struct.pack("<BBBBBBBBI",2,33,0,0,0,0,0,0,0)),(24,struct.pack("<BBBBBBBBI",10,0,129,0,0,0,0,0,0)),(32,struct.pack("<BBBBBBBBI",2,0,0,0,0,1,0,0,0)),(32,struct.pack("<BBBBBBBBI",2,0,0,0,0,0,1,0,0)),(24,bytes(12)),(32,bytes(12)),(16,bytes(15)),(20,bytes(7)),(24,bytes(11)),(32,bytes(11)),(16,struct.pack("<BBHiII",0,0,1,1,0,0)+b"x")])
def test_rejects_invalid_typed_headers_and_tlvs(kind,payload):
    with pytest.raises(B3Error):
        api().parse_message(api().NetlinkMessage(kind,payload))


@parametrize("value", [None, (16,b""), True])
def test_rejects_forged_message_objects(value):
    with pytest.raises(B3Error):
        api().parse_message(value)


@parametrize("kind,payload", [(True,b""),("16",b""),(3,b""),(16,bytearray(16)),(16,bytes(65537))])
def test_rejects_forged_message_fields(kind,payload):
    with pytest.raises(B3Error):
        api().parse_message(api().NetlinkMessage(kind,payload))


def test_family_binding_and_valid_typed_values():
    n = api()
    for kind, request, payload, fields in [
        (20,22,struct.pack("<BBBBI",10,128,255,255,2),{"family":10,"prefixlen":128,"index":2}),
        (24,26,struct.pack("<BBBBBBBBI",2,32,31,255,255,255,255,255,0xffffffff),{"family":2,"dst_len":32,"src_len":31,"flags":0xffffffff}),
        (32,34,struct.pack("<BBBBBBBBI",10,128,127,255,255,0,0,255,0xffffffff),{"family":10,"action":255,"table":255}),
    ]:
        family = 0 if request == 22 else payload[0]
        data = n.parse_dump(request,family,7,9,(msg(kind,payload)+done(),))[0]
        typed = n.parse_message(data)
        assert all(getattr(typed,k) == v for k,v in fields.items())
        assert typed.attributes == ()
        if request != 22:
            with pytest.raises(B3Error):
                n.parse_dump(request,12-family,7,9,(msg(kind,payload)+done(),))


def test_module_import_surface_has_no_io_or_runtime_dependencies():
    n = api()
    tree = ast.parse(Path(n.__file__).read_text())
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imports |= {a.name for node in ast.walk(tree) if isinstance(node, ast.Import) for a in node.names}
    assert imports <= {"dataclasses", "struct", "contracts"}
    banned = {"open", "exec", "eval", "__import__", "socket", "subprocess"}
    assert not any(isinstance(node,ast.Name) and node.id in banned for node in ast.walk(tree))
