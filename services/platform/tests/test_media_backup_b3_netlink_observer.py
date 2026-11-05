"""Fake primitives only: this file never opens a native socket."""
import ast
import base64
from dataclasses import FrozenInstanceError, fields
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import select
import socket
import struct
import time

import pytest

from platform_service.media_backup_b3.contracts import B3Error
from platform_service.media_backup_b3.netlink_contract import parse_datagram

MODULE = "platform_service.media_backup_b3.netlink_observer"
FIXTURE = Path(__file__).parent / "fixtures/media_backup_b3/network/linuxkit-6.12.76-none-v1.public.json"
SELECTORS = ((18,0),(22,0),(26,2),(26,10),(34,2),(34,10))


def api():
    assert importlib.util.find_spec(MODULE) is not None, "N2 observer missing"
    return importlib.import_module(MODULE)


def frame(kind=3, payload=bytes(4), seq=11, pid=1, flags=2):
    raw = struct.pack("<IHHII",16+len(payload),kind,flags,seq,pid)+payload
    return raw+bytes(-len(raw)%4)


def fixture_packets():
    raw = FIXTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == "269cea55bbe3139f77a6629462f0a11559b3e8efe3a5cd1171ab65b962b74584"
    result = []
    for i,d in enumerate(json.loads(raw)["dumps"]):
        packets = []
        for encoded in d["packets"]:
            packet = bytearray(base64.b64decode(encoded,validate=True))
            offset = 0
            for f in parse_datagram(bytes(packet)):
                struct.pack_into("<II",packet,offset+8,11+i,1)
                offset += (16+len(f.payload)+3)&~3
            packets.append(bytes(packet))
        result.append(packets)
    return result


class Harness:
    def __init__(self, monkeypatch, *, packets=None, fail=None, expire=None, peer=(0,0), ancillary=None, flags=0, name=(1,0)):
        self.n = api()
        self.now = 1_000_000_000
        self.deadline = self.now+1_000_000_000
        self.events = []
        self.objects = []
        self.sent = []
        self.waits = []
        self.packets = fixture_packets() if packets is None else packets
        self.slot = -1
        self.received = 0
        self.fail = fail
        self.expire = expire
        owner = self

        def event(label):
            owner.events.append(label)
            if owner.expire == label:
                owner.now = owner.deadline
            if owner.fail == label:
                raise OSError("sensitive-native-detail")

        class Socket:
            def __new__(cls):
                value = object.__new__(cls)
                owner.objects.append(value)
                event("new")
                return value

            def __init__(self,*args):
                owner.constructor = args
                event("init")

            def setblocking(self, value):
                assert value is False
                event("setblocking")

            def bind(self, value):
                assert value == (0,0)
                event("bind")

            def getsockname(self):
                event("getsockname")
                return name

            def sendto(self, request, peer):
                assert peer == (0,0)
                owner.sent.append(request)
                owner.slot += 1
                event("send")
                return len(request)-1 if fail == "short" else len(request)

            def recvmsg(self, *args):
                assert args == (65536,0)
                event("recv")
                owner.received += 1
                return owner.packets[owner.slot].pop(0), ([] if ancillary is None else ancillary), flags, peer

            def close(self):
                event("close")

        def select(read,write,error,timeout):
            assert error == []
            assert (read == owner.objects and write == []) or (write == owner.objects and read == [])
            assert 0 < timeout <= 1
            owner.waits.append(timeout)
            label = "select-read" if read else "select-write"
            event(label)
            if fail in ("timeout", "spurious"):
                return [],[],[]
            return read,write,[]

        def clock():
            owner.events.append("clock")
            value = owner.now
            owner.now += 1_000
            return value

        monkeypatch.setattr(self.n,"_Socket",Socket)
        monkeypatch.setattr(self.n,"_select",select)
        monkeypatch.setattr(self.n,"_monotonic_ns",clock)
        monkeypatch.setattr(self.n,"_platform","linux")
        monkeypatch.setattr(self.n,"_byteorder","little")

    def observe(self, deadline=None):
        return self.n.observe_linux_network(self.deadline if deadline is None else deadline)


def test_six_fixture_dumps_exact_requests_immutable_redacted_and_owned_cleanup(monkeypatch):
    h = Harness(monkeypatch)
    expected = fixture_packets()
    result = h.observe()
    assert type(result) is tuple and len(result) == 6
    assert len(h.objects) == 1 and h.constructor == (16,3,0)
    assert h.events.count("close") == 1
    assert h.waits == sorted(h.waits,reverse=True)
    for i,(record,(kind,family)) in enumerate(zip(result,SELECTORS)):
        assert tuple(f.name for f in fields(record)) == ("request_type","family","sequence","local_portid","datagrams","parsedmessages")
        assert (record.request_type,record.family,record.sequence,record.local_portid) == (kind,family,11+i,1)
        assert record.datagrams == tuple(expected[i])
        assert len(record.parsedmessages) == (10,2,3,1,3,2)[i]
        size = 16 if kind == 18 else (8 if kind == 22 else 12)
        assert h.sent[i] == struct.pack("<IHHII",16+size,kind,0x301,11+i,1)+bytes([family])+bytes(size-1)
        assert "datagrams" not in repr(record) and "payload" not in repr(record)
        with pytest.raises(FrozenInstanceError):
            record.family = 8
    for index,label in enumerate(h.events):
        if label not in ("clock","new"):
            assert h.events[index-1] == "clock" and h.events[index+1] == "clock"


@pytest.mark.parametrize("deadline", [True,False,0,-1,2**63,1.0,"123",None,1_000_000_000,61_000_000_001])
def test_strict_original_deadline_rejects_before_construction(monkeypatch,deadline):
    h = Harness(monkeypatch)
    with pytest.raises(B3Error):
        h.n.observe_linux_network(deadline)
    assert h.objects == []


@pytest.mark.parametrize("phase", ["init","setblocking","bind","getsockname","select-write","send","select-read","recv","close","short","timeout","spurious"])
def test_any_native_failure_closes_original_once_no_retry_or_partial_result(monkeypatch,phase):
    h = Harness(monkeypatch,fail=phase)
    with pytest.raises(B3Error,match="^invalid B3 network observation$") as error:
        h.observe()
    assert "sensitive" not in str(error.value)
    assert len(h.objects) == 1 and h.events.count("close") == 1
    if phase != "close":
        assert len(h.sent) <= 1 and h.received <= 1
    else:
        assert len(h.sent) == 6


@pytest.mark.parametrize("phase", ["new","init","setblocking","bind","getsockname","select-write","send","select-read","recv","close"])
def test_expiry_at_every_native_phase_prevents_next_work_and_return(monkeypatch,phase):
    h = Harness(monkeypatch,expire=phase)
    with pytest.raises(B3Error):
        h.observe()
    assert h.events.count("close") == 1
    if phase != "close":
        assert len(h.sent) <= 1 and h.received <= 1
    else:
        assert len(h.sent) == 6


@pytest.mark.parametrize("name", [(0,0),(2**32,0),(True,0),(1,1),(1,False),[1,0],(1,0,0)])
def test_invalid_bound_address_rejected(monkeypatch,name):
    h = Harness(monkeypatch,name=name)
    with pytest.raises(B3Error):
        h.observe()
    assert not h.sent and h.events.count("close") == 1


@pytest.mark.parametrize("kwargs", [{"flags":1},{"flags":8},{"flags":32},{"ancillary":[(1,2,b"secret")]},{"peer":(1,0)},{"peer":(0,1)},{"peer":[0,0]},{"peer":(False,0)}])
def test_recv_transport_metadata_rejected(monkeypatch,kwargs):
    h = Harness(monkeypatch,**kwargs)
    with pytest.raises(B3Error):
        h.observe()
    assert h.events.count("close") == 1 and h.received == 1


@pytest.mark.parametrize("packet", [b"",bytearray(frame()),frame()[:-1],frame()+b"x",frame(3,b""),frame(3,struct.pack("<i",-1)),frame()+frame(16,bytes(16)),frame()+frame(),frame(2,b""),frame(seq=12),frame(pid=0),frame(flags=0),bytes(65537)],ids=lambda x:type(x).__name__)
def test_bad_packet_or_done_never_returns_prefix(monkeypatch,packet):
    h = Harness(monkeypatch,packets=[[packet]])
    with pytest.raises(B3Error):
        h.observe()
    assert h.events.count("close") == 1 and h.received <= 2 and len(h.sent) == 1


def link(payload=b"", seq=11):
    return frame(16,struct.pack("<BBHiII",0,0,772,1,0,0)+payload,seq=seq)


def test_terminal_header_bytes_inside_attribute_do_not_stop_receive(monkeypatch):
    opaque = frame()
    attribute = struct.pack("<HH",4+len(opaque),1)+opaque
    packets = fixture_packets()
    packets[0] = [link(attribute),frame()]
    h = Harness(monkeypatch,packets=packets)
    assert len(h.observe()[0].parsedmessages) == 1
    assert h.received >= 7


def test_failure_on_sixth_dump_returns_no_first_five(monkeypatch):
    packets = fixture_packets()
    packets[5] = [frame(seq=16,payload=b"bad")]
    h = Harness(monkeypatch,packets=packets)
    with pytest.raises(B3Error):
        h.observe()
    assert len(h.sent) == 6 and h.events.count("close") == 1


@pytest.mark.parametrize("count,accepted", [(255,True),(256,False)])
def test_complete_packet_count_neighbors(monkeypatch,count,accepted):
    packets = fixture_packets()
    packets[0] = [link()]*count+[frame()]
    h = Harness(monkeypatch,packets=packets)
    if accepted:
        assert len(h.observe()[0].parsedmessages) == count
    else:
        with pytest.raises(B3Error):
            h.observe()
        assert h.received == 257


@pytest.mark.parametrize("extra,accepted", [(0,True),(4,False)])
def test_complete_six_dump_aggregate_byte_neighbors(monkeypatch,extra,accepted):
    packets = [[frame(seq=11+i)] for i in range(6)]
    # Five DONEs consume 100 bytes; first dump exactly fills remaining budget.
    def sized(size):
        return link(struct.pack("<HH",size-32,1)+bytes(size-36))
    packets[0] = [sized(65536)]*127+[sized(65416+extra),frame()]
    assert sum(len(p) for d in packets for p in d) == 8*1024**2+extra
    h = Harness(monkeypatch,packets=packets)
    if accepted:
        assert len(h.observe()) == 6
    else:
        with pytest.raises(B3Error):
            h.observe()
        assert len(h.sent) == 6


@pytest.mark.parametrize("attribute,value", [("_platform","darwin"),("_byteorder","big")])
def test_platform_guard_before_socket(monkeypatch,attribute,value):
    h = Harness(monkeypatch)
    monkeypatch.setattr(h.n,attribute,value)
    with pytest.raises(B3Error):
        h.observe()
    assert not h.objects


def test_import_has_only_definitions_and_local_primitive_copies():
    n = api()
    tree = ast.parse(Path(n.__file__).read_text())
    assert all(isinstance(node,(ast.Import,ast.ImportFrom,ast.ClassDef,ast.FunctionDef)) or
               isinstance(node,ast.Expr) and isinstance(node.value,ast.Constant)
               for node in tree.body)
    imports = {node.module for node in ast.walk(tree) if isinstance(node,ast.ImportFrom)}
    assert imports <= {"dataclasses","socket","select","time","sys","contracts","netlink_contract"}


def test_fake_patches_do_not_change_global_standard_library(monkeypatch):
    originals = socket.socket,select.select,time.monotonic_ns
    h = Harness(monkeypatch)
    assert h.observe()
    assert (socket.socket,select.select,time.monotonic_ns) == originals


@pytest.mark.parametrize("error_type", [InterruptedError,BlockingIOError])
@pytest.mark.parametrize("phase", ["select-write","send","select-read","recv"])
def test_interrupt_or_eagain_fails_without_repeating_operation(monkeypatch,error_type,phase):
    h = Harness(monkeypatch)
    original = h.n._select
    if phase.startswith("select"):
        def interrupted(read,write,error,timeout):
            if bool(write) == (phase == "select-write"):
                h.events.append(phase)
                raise error_type("sensitive-detail")
            return original(read,write,error,timeout)
        monkeypatch.setattr(h.n,"_select",interrupted)
    else:
        def interrupted(self,*args):
            h.events.append(phase)
            raise error_type("sensitive-detail")
        monkeypatch.setattr(h.n._Socket,"sendto" if phase == "send" else "recvmsg",interrupted)
    with pytest.raises(B3Error,match="^invalid B3 network observation$"):
        h.observe()
    assert h.events.count(phase) == 1 and h.events.count("close") == 1


def test_expiry_at_each_observed_clock_check_prevents_return(monkeypatch):
    baseline = Harness(monkeypatch)
    baseline.observe()
    checks = baseline.events.count("clock")
    for target in range(1,checks+1):
        h = Harness(monkeypatch)
        original = h.n._monotonic_ns
        count = 0
        def clock():
            nonlocal count
            count += 1
            if count == target:
                h.now = h.deadline
            return original()
        monkeypatch.setattr(h.n,"_monotonic_ns",clock)
        with pytest.raises(B3Error):
            h.observe()
        assert h.events.count("close") == int(bool(h.objects))


def test_final_clock_native_error_is_generic_after_original_close(monkeypatch):
    baseline = Harness(monkeypatch)
    baseline.observe()
    checks = baseline.events.count("clock")
    h = Harness(monkeypatch)
    original = h.n._monotonic_ns
    count = 0
    def clock():
        nonlocal count
        count += 1
        if count == checks:
            raise OSError("sensitive-clock-detail")
        return original()
    monkeypatch.setattr(h.n,"_monotonic_ns",clock)
    with pytest.raises(B3Error,match="^invalid B3 network observation$"):
        h.observe()
    assert h.events.count("close") == 1


@pytest.mark.parametrize("interruption", [KeyboardInterrupt,SystemExit])
@pytest.mark.parametrize("phase", ["preclose-clock","recv","close"])
def test_base_interruption_propagates_and_original_close_is_attempted(monkeypatch,interruption,phase):
    baseline = Harness(monkeypatch)
    baseline.observe()
    close_index = baseline.events.index("close")
    preclose_check = baseline.events[:close_index].count("clock")
    h = Harness(monkeypatch)
    if phase == "preclose-clock":
        original = h.n._monotonic_ns
        count = 0
        def clock():
            nonlocal count
            count += 1
            if count == preclose_check:
                raise interruption("bounded-test-interruption")
            return original()
        monkeypatch.setattr(h.n,"_monotonic_ns",clock)
    else:
        method = "recvmsg" if phase == "recv" else "close"
        original = getattr(h.n._Socket,method)
        def interrupted(self,*args):
            original(self,*args)
            raise interruption("bounded-test-interruption")
        monkeypatch.setattr(h.n._Socket,method,interrupted)
    with pytest.raises(interruption,match="^bounded-test-interruption$"):
        h.observe()
    assert len(h.objects) == 1
    assert h.events.count("close") == 1
    assert len(h.sent) == (1 if phase == "recv" else 6)
