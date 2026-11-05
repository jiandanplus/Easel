"""Task2B2 pure protocol tests. No real sockets, timers, threads or resources."""
import importlib.util
import json
import sys
from pathlib import Path
from dataclasses import FrozenInstanceError
import pytest

BASE = Path(__file__).parents[1] / "scripts/av_sources"
NONCE = "a" * 32
CID = "b" * 64
SECRET = "DO-NOT-EXPOSE-PEER-SECRET"


def load(name):
    path = BASE / (name + ".py")
    assert path.exists(), "finite API implementation is missing"
    spec = importlib.util.spec_from_file_location("test_target_" + name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


def wire(body=b"{}", status=200, media=b"application/json"):
    if status == 204:
        return b"HTTP/1.1 204 No Content\r\n\r\n"
    return (b"HTTP/1.1 " + str(status).encode() + b" OK\r\nContent-Type: " + media +
            b"\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def terminal(**changes):
    s = dict(Status="exited", Running=False, Paused=False, Restarting=False,
             Dead=False, OOMKilled=False, ExitCode=0, Pid=0, Error="")
    s.update(changes)
    return dict(Id=CID, RestartCount=0, State=s)


class Clock:
    def __init__(self): self.now = 10.0
    def __call__(self): return self.now


class Socket:
    def __init__(self, data, hook=None, split=7):
        self.data, self.hook, self.split = data, hook, split
        self.sent = []
        self.events = []
        self.close_count = self.shutdown_count = 0
        self.timeouts = []
    def event(self, name):
        self.events.append(name)
        if self.hook: self.hook(name, self)
    def settimeout(self, value):
        assert 0 < value <= 5
        self.timeouts.append(value)
        self.event("timeout")
    def connect(self, path):
        self.path = path
        self.event("connect")
    def sendall(self, data):
        self.sent.append(data)
        self.event("send")
    def recv(self, n):
        self.event("recv" if self.data else "eof")
        part = self.data[:min(n, self.split)]
        self.data = self.data[len(part):]
        return part
    def shutdown(self, how):
        self.shutdown_count += 1
        self.event("shutdown")
    def close(self):
        self.close_count += 1
        self.event("close")


class Timer:
    def __init__(self, seconds, callback, hook=None):
        assert 0 < seconds <= 5
        self.seconds, self.callback, self.hook = seconds, callback, hook
        self.events = []
        self.alive = False
    def event(self, name):
        self.events.append(name)
        if self.hook: self.hook(name, self)
    def start(self): self.event("start")
    def cancel(self): self.event("cancel")
    def join(self, timeout):
        assert 0 <= timeout <= self.seconds
        self.event("join")
    def is_alive(self):
        self.event("is_alive")
        return self.alive


class Rig:
    def __init__(self, m, responses=(), socket_hook=None, timer_hook=None, socket_split=7, **kwargs):
        self.m, self.clock = m, Clock()
        self.responses = list(responses)
        self.sockets, self.timers = [], []
        def sf(*args):
            assert args == (m.socket.AF_UNIX, m.socket.SOCK_STREAM)
            s = Socket(self.responses.pop(0), socket_hook, split=socket_split)
            self.sockets.append(s)
            return s
        def tf(seconds, callback):
            t = Timer(seconds, callback, timer_hook)
            self.timers.append(t)
            return t
        self.api = m.APIClient(NONCE, b"{}", http_module=load("pack_http"),
                               socket_factory=sf, timer_factory=tf, clock=self.clock, **kwargs)
    def call(self, operation): return self.api.call(self.m.Operation(operation))
    def preflight(self):
        self.call("info"); self.call("image"); self.call("create")
        self.api.bind_created(CID)
    def started(self):
        self.preflight(); self.api.bind_deadline(14.0); self.call("start")
    def ready(self):
        self.started(); self.call("inspect")


def prefix(): return [wire(), wire(), wire(encoded(dict(Id=CID)), 201)]
def full(): return prefix() + [wire(status=204), wire(encoded(terminal())),
                              wire(b"stdout", media=b"application/vnd.docker.raw-stream"),
                              wire(b"artifact", media=b"application/vnd.docker.multiplexed-stream")]


def rejects(m, fn):
    with pytest.raises(m.APIError, match="^invalid AV API operation$") as e: fn()
    assert e.value.__context__ is None
    assert SECRET not in str(e.value)


def test_exact_routes_body_frozen_result_and_cleanup():
    m = load("pack_api"); r = Rig(m, full())
    r.ready()
    stdout = r.call("stdout")
    assert stdout.value == b"stdout" and stdout.injected_for_tests is True
    r.api.admit_stdout()
    assert r.call("artifact").value == b"artifact"
    expected = [("GET", "/info"), ("GET", "/images/"+m.IMAGE+"/json"),
        ("POST", "/containers/create?name=run-av-pack-"+NONCE+"&platform=linux%2Farm64%2Fv8"),
        ("POST", "/containers/"+CID+"/start"), ("GET", "/containers/"+CID+"/json"),
        ("GET", "/containers/"+CID+"/logs?stdout=1&stderr=0&timestamps=0&follow=0&details=0&tail=all"),
        ("GET", "/containers/"+CID+"/logs?stdout=0&stderr=1&timestamps=0&follow=0&details=0&tail=all")]
    for s, (method, path), t in zip(r.sockets, expected, r.timers):
        assert s.path == "/Users/jiandan/.docker/run/docker.sock"
        assert s.sent[0].startswith((method+" /v1.49"+path+" HTTP/1.1\r\n").encode())
        assert b"Host: localhost\r\nConnection: close\r\n" in s.sent[0]
        assert s.close_count == 1 and s.shutdown_count == 1
        assert t.events == ["start", "cancel", "join", "is_alive"]
    assert r.sockets[2].sent[0].endswith(b"\r\n\r\n{}")
    with pytest.raises(FrozenInstanceError): stdout.value = b"changed"
    assert stdout.counters.wire_bytes > len(stdout.value)
    assert "body" not in vars(stdout.counters)


@pytest.mark.parametrize("body", [None, {}, bytearray(b"{}"), b"{ }", b'{"a":1,"a":2}',
    b"[]", b'{"a":NaN}', b'{"a":1e999}', b'{"b":0,"a":0}', b"{}"+b" "*131071])
def test_constructor_rejects_noncanonical_or_mutable_body(body):
    m = load("pack_api")
    rejects(m, lambda: m.APIClient(NONCE, body, http_module=load("pack_http")))


@pytest.mark.parametrize("nonce", ["a"*31, "A"*32, "g"*32, "a"*33, None, 32])
def test_nonce(nonce):
    m = load("pack_api")
    rejects(m, lambda: m.APIClient(nonce, b"{}", http_module=load("pack_http")))


def test_http_dependency_mandatory_and_preset_validation():
    m = load("pack_api")
    for dependency in (None, object()):
        rejects(m, lambda: m.APIClient(NONCE, b"{}", http_module=dependency))
    h = load("pack_http"); object.__setattr__(h.STDOUT_LIMITS, "body_bytes", 65537)
    rejects(m, lambda: m.APIClient(NONCE, b"{}", http_module=h))


@pytest.mark.parametrize("op", ["image", "create", "start", "inspect", "stdout", "artifact", "GET", "/info", "delete", None])
def test_invalid_initial_transition_rejects_without_io_and_poisons(op):
    m = load("pack_api"); r = Rig(m, [wire()])
    rejects(m, lambda: r.api.call(op if op not in [x.value for x in m.Operation] else m.Operation(op)))
    rejects(m, lambda: r.call("info"))
    assert not r.sockets


@pytest.mark.parametrize("op", ["info", "image", "create", "start", "stdout", "artifact"])
def test_once_attempts_consumed_even_on_io_failure(op):
    m = load("pack_api"); r = Rig(m, full())
    if op != "info": r.call("info")
    if op not in ("info", "image"): r.call("image")
    if op not in ("info", "image", "create"):
        r.call("create"); r.api.bind_created(CID); r.api.bind_deadline(14)
    if op in ("stdout", "artifact"):
        r.call("start"); r.call("inspect")
    if op == "artifact": r.call("stdout"); r.api.admit_stdout()
    r.responses[0] = b"HTTP/1.1 500 "+SECRET.encode()+b"\r\n\r\n"
    rejects(m, lambda: r.call(op))
    assert m.Operation(op) in r.api.attempted
    count = len(r.sockets)
    rejects(m, lambda: r.call(op)); rejects(m, lambda: r.call("inspect"))
    assert len(r.sockets) == count


@pytest.mark.parametrize("field,value", [("Running", True), ("Paused", True), ("Restarting", True),
    ("Dead", True), ("OOMKilled", True), ("ExitCode", 1), ("ExitCode", False), ("Pid", True),
    ("Error", "secret"), ("Status", "running")])
def test_logs_require_exact_terminal_state(field, value):
    m = load("pack_api"); data = prefix()+[wire(status=204), wire(encoded(terminal(**{field:value})))]
    r = Rig(m, data); r.ready(); rejects(m, lambda: r.call("stdout")); assert len(r.sockets)==5


@pytest.mark.parametrize("bad", [dict(RestartCount=True), dict(RestartCount=1), dict(Id="c"*64)])
def test_same_cid_and_restart_gate(bad):
    m = load("pack_api"); info = terminal(); info.update(bad)
    r = Rig(m, prefix()+[wire(status=204),wire(encoded(info))]); r.started()
    if "Id" in bad: rejects(m, lambda: r.call("inspect"))
    else: r.call("inspect"); rejects(m, lambda: r.call("stdout"))


@pytest.mark.parametrize("phase", ["before_start", "before_inspect", "before_stdout", "before_admission", "duplicate_admission", "duplicate_artifact"])
def test_artifact_gate(phase):
    m=load("pack_api"); r=Rig(m,full()); r.preflight(); r.api.bind_deadline(14)
    if phase != "before_start": r.call("start")
    if phase not in ("before_start","before_inspect"): r.call("inspect")
    if phase not in ("before_start","before_inspect","before_stdout"): r.call("stdout")
    if phase in ("duplicate_admission","duplicate_artifact"): r.api.admit_stdout()
    if phase == "duplicate_artifact": r.call("artifact")
    count=len(r.sockets)
    rejects(m, r.api.admit_stdout if phase=="duplicate_admission" else lambda:r.call("artifact"))
    assert len(r.sockets)==count


@pytest.mark.parametrize("deadline", [0, -1, True, float("nan"), float("inf"), "14"])
def test_deadline_invalid(deadline):
    m=load("pack_api"); r=Rig(m,prefix()); r.preflight()
    rejects(m,lambda:r.api.bind_deadline(deadline)); assert len(r.sockets)==3


def test_bind_once_and_cid_cannot_swap():
    m=load("pack_api"); r=Rig(m,prefix()); r.preflight()
    rejects(m,lambda:r.api.bind_created("c"*64)); assert len(r.sockets)==3
    r=Rig(m,prefix()); r.preflight(); r.api.bind_deadline(14)
    rejects(m,lambda:r.api.bind_deadline(15))
    r=Rig(m,[]); rejects(m,lambda:r.api.bind_created(CID)); assert not r.sockets


@pytest.mark.parametrize("phase", ["connect","send","recv","eof"])
@pytest.mark.parametrize("cause", ["cancel","deadline","baseexception"])
def test_interruption_owns_exact_socket_and_poison(phase,cause):
    m=load("pack_api")
    def hook(event,s):
        if event==phase:
            if cause=="cancel": r.timers[-1].callback()
            elif cause=="deadline": r.clock.now=15
            else: raise KeyboardInterrupt(SECRET)
    r=Rig(m,[wire()],socket_hook=hook)
    rejects(m,lambda:r.call("info")); assert len(r.sockets)==1
    assert r.sockets[0].close_count==1
    rejects(m,lambda:r.call("info")); assert len(r.sockets)==1
    assert "cancel" in r.timers[0].events and "join" in r.timers[0].events


def test_timer_before_connect_and_expired_before_socket():
    m=load("pack_api")
    r=Rig(m,[wire()],timer_hook=lambda e,t: t.callback() if e=="start" else None)
    rejects(m,lambda:r.call("info")); assert "connect" not in r.sockets[0].events
    r=Rig(m,prefix()); r.preflight(); r.api.bind_deadline(9)
    rejects(m,lambda:r.call("start")); assert len(r.sockets)==3


def test_original_deadline_clips_reads_without_reset():
    m=load("pack_api"); r=Rig(m,full()); r.started()
    r.clock.now=13.0
    def hook(e,s):
        if e=="recv": r.clock.now+=.01
    # Metadata header is byte-segmented by actual HTTP parser; after 100 reads fail.
    old_factory=r.api._socket_factory
    def factory(*a):
        s=old_factory(*a); s.hook=hook; return s
    r.api._socket_factory=factory
    rejects(m,lambda:r.call("inspect")); assert r.timers[-1].seconds==1
    assert r.sockets[-1].timeouts[-1]<r.sockets[-1].timeouts[0]


@pytest.mark.parametrize("phase", ["shutdown","close","cancel","join","is_alive","alive"])
def test_cleanup_faults_never_return_success_or_print_raw(phase,capsys):
    m=load("pack_api")
    def sh(e,s):
        if e==phase: raise RuntimeError(SECRET)
    def th(e,t):
        if phase=="alive": t.alive=True
        if e==phase: raise SystemExit(SECRET)
    r=Rig(m,[wire()],socket_hook=sh,timer_hook=th)
    rejects(m,lambda:r.call("info")); assert r.sockets[0].close_count==1
    assert "cancel" in r.timers[0].events and "join" in r.timers[0].events
    assert SECRET not in capsys.readouterr().err


def test_shutdown_oserror_expected_but_timer_callback_cannot_throw():
    m=load("pack_api")
    def sh(e,s):
        if e=="shutdown": raise OSError(SECRET)
    r=Rig(m,[wire()],socket_hook=sh); r.call("info")
    def th(e,t):
        if e=="start": t.callback()
    def broken(e,s):
        if e in ("shutdown","close"): raise KeyboardInterrupt(SECRET)
    r=Rig(m,[wire()],socket_hook=broken,timer_hook=th)
    rejects(m,lambda:r.call("info")); assert r.sockets[0].close_count==1


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}',b'{"x":NaN}',b'{"x":1e999}',b'[]',
    b'{"x":123456789012345678901}', b'{"x":'+b'['*32+b'0'+b']'*32+b'}',
    b'{"x":"bad\\uZZZZ"}', b'{"x":'+b'[0],'*8192+b'0}', b'{}'*65537])
def test_strict_metadata_prescan_and_json(raw):
    m=load("pack_api"); r=Rig(m,[wire(raw)])
    rejects(m,lambda:r.call("info")); rejects(m,lambda:r.call("info"))


def test_prescan_strings_escapes_and_small_tightening():
    m=load("pack_api")
    raw=encoded({"x":'\\"[]{},:'*10})
    r=Rig(m,[wire(raw)]); assert r.call("info").value==json.loads(raw)
    for caps in (dict(depth=1),dict(openings=1),dict(string_bytes=2),dict(number_chars=1),dict(commas=1),dict(colons=1)):
        raw=encoded({"a":[1,22,3],"b":0})
        r=Rig(m,[wire(raw)],json_caps=caps); rejects(m,lambda:r.call("info"))
    rejects(m,lambda:Rig(m,[],json_caps=dict(depth=33)))


@pytest.mark.parametrize("raw", [wire().replace(b"200 OK",b"302 Secret"),wire().replace(b"application/json",b"text/plain"),
    wire(b"{}").replace(b"Content-Length: 2",b"Content-Length: 3"),
    wire()+b"x", b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\nZ\r\n"])
def test_actual_http_failures_poison(raw):
    m=load("pack_api"); r=Rig(m,[raw]); rejects(m,lambda:r.call("info")); rejects(m,lambda:r.call("info"))


def test_injection_label_cannot_be_erased_by_public_assignment():
    m=load("pack_api"); r=Rig(m,[wire()])
    with pytest.raises(AttributeError): r.api.injected_for_tests=False
    assert r.call("info").injected_for_tests is True


def test_default_construction_and_import_are_inert(monkeypatch):
    import socket
    import threading
    h=load("pack_http")
    def forbidden(*a,**k): raise AssertionError("real resource attempted")
    monkeypatch.setattr(socket,"socket",forbidden)
    monkeypatch.setattr(threading,"Timer",forbidden)
    m=load("pack_api")
    api=m.APIClient(NONCE,b"{}",http_module=h)
    assert api.injected_for_tests is False


@pytest.mark.parametrize("op", ["info", "image", "create", "start", "stdout", "artifact"])
def test_successful_once_operations_cannot_repeat(op):
    m=load("pack_api"); r=Rig(m,full())
    for current in ("info","image","create","start","inspect","stdout","artifact"):
        if current=="start": r.api.bind_created(CID); r.api.bind_deadline(14)
        if current=="artifact": r.api.admit_stdout()
        r.call(current)
        if current==op: break
    count=len(r.sockets)
    rejects(m,lambda:r.call(op)); assert len(r.sockets)==count


@pytest.mark.parametrize("cid", [CID[:12],CID.upper(),"c"*64,None,True])
def test_binding_matches_returned_full_id(cid):
    m=load("pack_api"); r=Rig(m,prefix())
    r.call("info"); r.call("image"); response=r.call("create")
    response.value["Id"]=cid
    rejects(m,lambda:r.api.bind_created(cid)); assert len(r.sockets)==3


def test_mutable_create_response_cannot_swap_internal_binding():
    m=load("pack_api"); r=Rig(m,prefix()+[wire(encoded(dict(Id=CID)))])
    r.call("info"); r.call("image"); response=r.call("create")
    response.value.clear(); response.value.update(Id="c"*64,Warnings=["untrusted"])
    r.api.bind_created(CID)
    assert r.call("inspect").value["Id"]==CID
    assert r.sockets[-1].sent[0].startswith(("GET /v1.49/containers/"+CID+"/json").encode())


def test_start_without_deadline_and_unbound_inspect_reject_before_io():
    m=load("pack_api"); r=Rig(m,prefix()); r.preflight()
    rejects(m,lambda:r.call("start")); assert len(r.sockets)==3
    r=Rig(m,prefix()); r.call("info"); r.call("image"); r.call("create")
    rejects(m,lambda:r.call("inspect")); assert len(r.sockets)==3


def test_inspect_gate_cannot_be_forged_by_mutating_returned_dict():
    m=load("pack_api"); r=Rig(m,prefix()+[wire(status=204),wire(encoded(terminal(Running=True)))])
    r.started(); result=r.call("inspect"); result.value.update(terminal())
    rejects(m,lambda:r.call("stdout")); assert len(r.sockets)==5


def test_later_inspect_revokes_terminal_gate():
    m=load("pack_api"); r=Rig(m,full()[:5]+[wire(encoded(terminal(Status="running")))])
    r.ready(); r.call("inspect"); rejects(m,lambda:r.call("stdout")); assert len(r.sockets)==6


@pytest.mark.parametrize("split",[1,2,11,65536])
def test_actual_http_chunked_segments_all_operations(split):
    m=load("pack_api")
    def chunked(raw):
        head,body=raw.split(b"\r\n\r\n",1)
        if b" 204 " in head: return raw
        head=head[:head.index(b"Content-Length:")]+b"Transfer-Encoding: chunked"
        pieces=[body[i:i+3] for i in range(0,len(body),3)]
        return head+b"\r\n\r\n"+b"".join(format(len(p),"x").encode()+b"\r\n"+p+b"\r\n" for p in pieces)+b"0\r\n\r\n"
    r=Rig(m,[chunked(raw) for raw in full()],socket_split=split)
    r.ready(); out=r.call("stdout"); r.api.admit_stdout(); artifact=r.call("artifact")
    assert out.value==b"stdout" and artifact.value==b"artifact"
    assert out.counters.framing=="chunked" and out.counters.chunks==3


@pytest.mark.parametrize("fault",["close","join","is_alive"])
def test_timeout_callback_cleanup_failure_is_nonthrowing_and_fixed(fault,capsys):
    m=load("pack_api")
    def sh(e,s):
        if e==fault: raise KeyboardInterrupt(SECRET)
    def th(e,t):
        if e=="start": t.callback(); t.callback()
        if e==fault: raise KeyboardInterrupt(SECRET)
    r=Rig(m,[wire()],socket_hook=sh,timer_hook=th)
    rejects(m,lambda:r.call("info")); assert r.sockets[0].close_count==1
    assert SECRET not in capsys.readouterr().err


def test_cleanup_cannot_extend_deadline_or_return_success():
    m=load("pack_api")
    def th(e,t):
        if e=="cancel": r.clock.now=15.0
    r=Rig(m,[wire()],timer_hook=th)
    rejects(m,lambda:r.call("info"))
    assert "join" in r.timers[0].events and r.sockets[0].close_count==1


def test_cancel_before_call_opens_nothing_and_cannot_be_reset():
    m=load("pack_api"); r=Rig(m,[wire()]); r.api.cancel(); r.api.cancel()
    rejects(m,lambda:r.call("info")); assert not r.sockets and not r.timers


def test_cancel_inside_factory_closes_late_published_socket_once():
    m=load("pack_api"); r=Rig(m,[wire()]); old=r.api._socket_factory
    def sf(*args):
        s=old(*args); r.api.cancel(); return s
    r.api._socket_factory=sf
    rejects(m,lambda:r.call("info")); assert r.sockets[0].close_count==1
    assert "connect" not in r.sockets[0].events and not r.timers


@pytest.mark.parametrize("which",["socket","timer","start","timeout"])
def test_dependency_faults_are_safe_and_poisoned(which):
    m=load("pack_api"); r=Rig(m,[wire()])
    def fail(*a,**k): raise SystemExit(SECRET)
    if which=="socket": r.api._socket_factory=fail
    elif which=="timer": r.api._timer_factory=fail
    elif which=="start":
        old=r.api._timer_factory
        def tf(*a):
            t=old(*a); t.start=fail; return t
        r.api._timer_factory=tf
    else:
        old=r.api._socket_factory
        def sf(*a):
            s=old(*a); s.settimeout=fail; return s
        r.api._socket_factory=sf
    rejects(m,lambda:r.call("info")); rejects(m,lambda:r.call("info"))
    if r.sockets: assert r.sockets[0].close_count==1
    if r.timers: assert "cancel" in r.timers[0].events and "join" in r.timers[0].events


def test_mutated_http_presets_reject_before_factory():
    m=load("pack_api"); r=Rig(m,[wire()])
    object.__setattr__(r.api._http.METADATA_LIMITS,"body_bytes",131073)
    rejects(m,lambda:r.call("info")); assert not r.sockets


def test_response_counters_cannot_escape_as_unchecked_safe_facts():
    m=load("pack_api"); r=Rig(m,[wire()]); real=r.api._http.read_response
    def bad(*a,**kw):
        result=real(*a,**kw); object.__setattr__(result,"wire_bytes",-1); return result
    r.api._http.read_response=bad
    rejects(m,lambda:r.call("info"))


@pytest.mark.parametrize("caps,raw",[
    (dict(depth=2),b'{"x":[0]}'),
    (dict(openings=2),b'{"x":[]}'),
    (dict(string_bytes=3),b'{"x":"a"}'),
    (dict(number_chars=2),b'{"x":10}'),
    (dict(commas=1),b'{"x":[0,1]}'),
    (dict(colons=1),b'{"x":0}'),
])
def test_prescan_tight_caps_exact_boundaries(caps,raw):
    m=load("pack_api"); r=Rig(m,[wire(raw)],json_caps=caps)
    assert r.call("info").value==json.loads(raw)


def test_prescan_fixed_openings_before_loads_without_large_body(monkeypatch):
    m=load("pack_api"); r=Rig(m,[wire(b'{"x":['+b'[],'*8191+b'[]]}')])
    calls=[]
    def forbidden(*a,**k):
        calls.append(True)
        raise AssertionError("json.loads reached after structural cap")
    monkeypatch.setattr(m.json,"loads",forbidden)
    rejects(m,lambda:r.call("info"))
    assert not calls


def test_prestart_inspect_uses_five_seconds_and_binding_clips_later_inspect():
    m=load("pack_api"); r=Rig(m,prefix()+[wire(encoded(dict(Id=CID))),wire(encoded(dict(Id=CID)))])
    r.preflight(); r.call("inspect"); assert r.timers[-1].seconds==5
    r.api.bind_deadline(10.5); r.call("inspect"); assert r.timers[-1].seconds==.5


def test_public_cancel_normalizes_catchable_baseexception():
    m=load("pack_api"); r=Rig(m,[])
    class BrokenLock:
        def __enter__(self): raise KeyboardInterrupt(SECRET)
        def __exit__(self,*a): return False
    r.api._lock=BrokenLock()
    rejects(m,r.api.cancel)


@pytest.mark.parametrize("fault", ["enter", "exit"])
def test_owned_release_lock_interrupt_preserves_all_cleanup(fault, capsys):
    m=load("pack_api")
    owners=[]
    class InterruptedLock:
        def __init__(self):
            self.failed=False
            self.events=[]
        def __enter__(self):
            self.events.append("enter")
            if fault=="enter" and not self.failed:
                self.failed=True
                raise KeyboardInterrupt(SECRET)
            return self
        def __exit__(self,*args):
            self.events.append("exit")
            if fault=="exit" and not self.failed:
                self.failed=True
                raise KeyboardInterrupt(SECRET)
            return False
    lock=InterruptedLock()
    def timer_hook(event, timer):
        if event=="cancel":
            # The real reviewed HTTP receiver has already consumed final EOF.
            assert r.sockets[0].events[-1]=="eof"
            assert r.sockets[0].data==b""
            owner=r.api._active
            owners.append(owner)
            assert owner.owned is r.sockets[0]
            owner.lock=lock
    r=Rig(m,[wire()],timer_hook=timer_hook)
    rejects(m,lambda:r.call("info"))
    assert r.sockets[0].shutdown_count==r.sockets[0].close_count==1
    assert r.timers[0].events==["start","cancel","join","is_alive"]
    assert owners[0].owned is None and owners[0].cleanup_failed is True
    assert lock.failed is True
    rejects(m,lambda:r.call("info"))
    assert len(r.sockets)==len(r.timers)==1
    assert r.sockets[0].shutdown_count==r.sockets[0].close_count==1
    assert SECRET not in capsys.readouterr().err


@pytest.fixture(autouse=True)
def diagnostic_native_tripwires(monkeypatch):
    import socket, threading, subprocess, ctypes, os, signal, resource
    calls = []
    def forbidden(*a, **kw):
        calls.append(True)
        raise AssertionError("native resource reached")
    for mod, names in ((socket, ('socket',)), (threading, ('Thread', 'Timer')),
                       (subprocess, ('Popen',)), (ctypes, ('CDLL',)),
                       (os, ('fork', 'statvfs')), (signal, ('signal', 'alarm', 'setitimer', 'pthread_sigmask')),
                       (resource, ('getrlimit', 'setrlimit'))):
        for name in names:
            if hasattr(mod, name):
                monkeypatch.setattr(mod, name, forbidden)
    yield
    assert not calls, "native tripwire swallowed by normalized exception"


def test_diagnostic_first_failure_snapshot_is_immutable_and_retained():
    m = load("pack_api"); r = Rig(m, [wire(b"SECRET", 400)])
    assert r.api.last_failure is None
    rejects(m, lambda: r.call("info"))
    fact = r.api.last_failure
    assert vars(fact) == dict(operation="info", phase="transport", http_status=400, injected_for_tests=True)
    with pytest.raises(FrozenInstanceError): fact.phase = "publish"
    rejects(m, lambda: r.call("image")); r.api.cancel()
    assert r.api.last_failure is fact
    assert r.sockets[0].close_count == r.sockets[0].shutdown_count == 1
    assert r.timers[0].events == ["start", "cancel", "join", "is_alive"]


@pytest.mark.parametrize('fault,phase,status', [('request','request',None),('transport','transport',None),
    ('decode','decode',200),('publish','publish',200),('framing','transport',200)])
def test_diagnostic_stages_and_status_cannot_be_forged(monkeypatch,fault,phase,status):
    m=load('pack_api');r=Rig(m,[wire(b'bad' if fault=='decode' else b'{}') if fault!='framing' else b'HTTP/1.1 200 OK\r\nBAD\r\n'])
    if fault=='transport':
        def forged(*a):raise RuntimeError('operation=create http_status=400 SECRET')
        r.api._socket_factory=forged
    if fault=='publish':
        class BrokenSet(set):
            def add(self,value):raise RuntimeError('SECRET')
        r.api._success=BrokenSet()
    op=object() if fault=='request' else m.Operation.INFO
    rejects(m,lambda:r.api.call(op))
    fact=r.api.last_failure
    assert (fact.operation,fact.phase,fact.http_status)==(None if fault=='request' else 'info',phase,status)
    with pytest.raises(AttributeError):r.api.last_failure=None
    for s in r.sockets:assert s.close_count==s.shutdown_count==1

@pytest.mark.parametrize('field,value', [('operation',object()),('operation','made-up'),('phase',object()),
    ('phase','response_accepted'),('http_status',True),('http_status',99),('http_status',600),
    ('http_status','400'),('injected_for_tests',1)])
def test_diagnostic_strict_fields(field,value):
    m=load('pack_api');fields=dict(operation='create',phase='transport',http_status=400,injected_for_tests=False)
    fields[field]=value
    rejects(m,lambda:m.FailureDiagnostic(**fields))


def test_diagnostic_snapshot_failure_does_not_bypass_cleanup(monkeypatch):
    m=load('pack_api');r=Rig(m,[wire(b'SECRET',400)])
    def broken(*a):raise RuntimeError('SECRET')
    monkeypatch.setattr(m,'FailureDiagnostic',broken)
    rejects(m,lambda:r.call('info'))
    assert r.api.last_failure is None and r.api._poisoned
    assert r.sockets[0].close_count==r.sockets[0].shutdown_count==1
    assert r.timers[0].events==['start','cancel','join','is_alive']


def test_diagnostic_unavailable_first_snapshot_cannot_be_relabelled(monkeypatch):
    m=load('pack_api');r=Rig(m,[wire(b'SECRET',400)])
    constructor=m.FailureDiagnostic
    def broken(*a):raise RuntimeError('SECRET')
    monkeypatch.setattr(m,'FailureDiagnostic',broken)
    rejects(m,lambda:r.call('info'))
    assert r.api.last_failure is None
    before=(len(r.sockets),len(r.timers))
    monkeypatch.setattr(m,'FailureDiagnostic',constructor)
    rejects(m,lambda:r.call('image'))
    assert r.api.last_failure is None
    assert (len(r.sockets),len(r.timers))==before


def test_diagnostic_success_keeps_normal_result_and_no_failure():
    m=load('pack_api');r=Rig(m,[wire()]);out=r.call('info')
    assert out.value=={} and out.injected_for_tests is True and r.api.last_failure is None
