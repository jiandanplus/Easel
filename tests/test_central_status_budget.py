"""Synthetic status budgets: local file locks and in-memory HTTP only."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import gzip
import json
import socket
import ssl
import sys
import threading
import time
from types import SimpleNamespace

from fastapi import FastAPI
import httpx
import pytest

from easel.platform.central import CentralClient
from easel.platform.client import PlatformError
from tests.test_central_platform_client import auth

ORIGIN = "https://accounts.example.invalid"


def response(path):
    if path == "/v2/me":
        value = auth()
        return {"account": value["account"], "scope": value["scope"]}
    return {"auth_mode": "fixture", "subscription_execution_enabled": False}


class DelayedTransport(httpx.BaseTransport, httpx.AsyncBaseTransport):
    """Finite legacy delay; cancellable async headers/body for the owned path."""
    def __init__(self, delay=0, *, body=False):
        self.delay, self.body = delay, body
        self.calls = []
        self.closed = threading.Event()

    def record(self, request):
        self.calls.append((request.method, request.url.path,
                           request.headers.get("authorization"),
                           request.extensions["timeout"]["read"]))

    def handle_request(self, request):
        self.record(request)
        time.sleep(self.delay)
        return httpx.Response(200, json=response(request.url.path))

    async def handle_async_request(self, request):
        self.record(request)
        if self.body:
            delay = self.delay
            payload = json.dumps(response(request.url.path)).encode()

            class Body(httpx.AsyncByteStream):
                async def __aiter__(self):
                    yield payload[:1]
                    await asyncio.sleep(delay)
                    yield payload[1:]

            return httpx.Response(200, stream=Body())
        await asyncio.sleep(self.delay)
        return httpx.Response(200, json=response(request.url.path))

    async def aclose(self):
        self.closed.set()


def fixture(tmp_path, monkeypatch, transport, *, budget=.08):
    from easel.platform import central_web as bridge

    base = CentralClient(tmp_path, transport=transport, url=ORIGIN)
    base.save_auth(auth())
    before = base.store.path.read_bytes()
    created, reads = [], []

    def owned(*args, **kwargs):
        native = CentralClient(*args, **kwargs)
        state = native._state

        def observed():
            reads.append(native)
            return state()

        native._state = observed
        created.append(native)
        return native

    monkeypatch.setattr(bridge, "STATUS_BUDGET_SECONDS", budget, raising=False)
    monkeypatch.setattr(bridge, "CentralClient", owned)
    router = bridge.create_router(tmp_path, lambda: None,
                                  transport=transport, url=ORIGIN)
    app = FastAPI()
    app.include_router(router)
    browser = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
        base_url="http://local")
    return SimpleNamespace(base=base, before=before, created=created, reads=reads,
                           router=router, browser=browser)


async def close(value):
    await value.router.shutdown()
    await value.browser.aclose()


def assert_unavailable(result):
    assert result.status_code == 503
    assert result.json()["detail"]["code"] == "platform_unavailable"
    assert "SECRET" not in result.text


@pytest.mark.asyncio
async def test_status_times_out_while_real_session_lock_stays_held(tmp_path, monkeypatch):
    transport = DelayedTransport()
    value = fixture(tmp_path, monkeypatch, transport)
    holder = value.base.store.locked()
    holder.__enter__()
    task = asyncio.create_task(value.browser.get("/api/platform/central/status"))
    try:
        await asyncio.sleep(.2)
        returned_while_locked = task.done()
    finally:
        holder.__exit__(None, None, None)
    try:
        result = await asyncio.wait_for(task, 1)
        assert returned_while_locked, "status remained queued behind an owned lock"
        assert_unavailable(result)
        assert transport.calls == []
        assert value.reads == []
        assert value.base.store.path.read_bytes() == value.before
        # A timed-out contender must not damage subsequent lock ownership.
        with value.base.store.locked():
            assert value.base.store.read()["auth"]["scope"] == auth()["scope"]
    finally:
        await close(value)


def test_expired_owned_status_never_reads_auth_or_creates_refresh_marker(tmp_path, monkeypatch):
    transport = DelayedTransport()
    native = CentralClient(tmp_path, transport=transport, url=ORIGIN)
    expired = auth()
    expired["expires_at"] = 0
    native.save_auth(expired)
    before = native.store.path.read_bytes()
    native.absolute_deadline = time.monotonic() - 1

    def forbidden():
        pytest.fail("expired worker read auth before rejecting its deadline")

    monkeypatch.setattr(native.store, "read", forbidden)
    with pytest.raises(PlatformError) as error:
        native.status()
    assert error.value.status == 503
    assert transport.calls == []
    assert native.store.path.read_bytes() == before


@pytest.mark.asyncio
async def test_identity_and_public_capabilities_share_one_budget_and_client(tmp_path, monkeypatch):
    transport = DelayedTransport(.055)
    value = fixture(tmp_path, monkeypatch, transport, budget=.085)
    try:
        started = time.monotonic()
        result = await value.browser.get("/api/platform/central/status")
        elapsed = time.monotonic() - started
        assert_unavailable(result)
        assert elapsed < .25
        assert len(value.created) == 1
        assert [row[:2] for row in transport.calls] == [
            ("GET", "/v2/me"), ("GET", "/v2/capabilities")]
        assert transport.calls[0][2] == "Bearer ACCESS-CENTRAL-SECRET"
        assert transport.calls[1][2] is None
        assert 0 < transport.calls[1][3] < transport.calls[0][3]
        assert value.base.store.path.read_bytes() == value.before
    finally:
        await close(value)


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [False, True])
async def test_stalled_headers_or_body_close_transport_and_release_owned_lock(tmp_path, monkeypatch, body):
    transport = DelayedTransport(.6, body=body)
    value = fixture(tmp_path, monkeypatch, transport, budget=.2)
    try:
        started = time.monotonic()
        result = await value.browser.get("/api/platform/central/status")
        assert_unavailable(result)
        assert time.monotonic() - started < .5
        until = time.monotonic() + .3
        while not transport.closed.is_set() and time.monotonic() < until:
            await asyncio.sleep(.005)
        assert transport.closed.is_set(), "HTTP owner outlived its deadline"
        assert [row[:2] for row in transport.calls] == [("GET", "/v2/me")]
        native = CentralClient(tmp_path, transport=transport, url=ORIGIN)
        native.absolute_deadline = time.monotonic() + .1
        result = await asyncio.to_thread(native.status, verify=False)
        assert result["authenticated"]
        assert value.base.store.path.read_bytes() == value.before
    finally:
        await close(value)


@pytest.mark.asyncio
async def test_executor_queue_is_bounded_and_late_worker_is_an_auth_noop(tmp_path, monkeypatch):
    from easel.platform import central_web as bridge

    transport = DelayedTransport()
    value = fixture(tmp_path, monkeypatch, transport, budget=.06)
    gate, finished = threading.Event(), threading.Event()
    futures = []
    pool = ThreadPoolExecutor(1)

    async def queued(method, *args, **kwargs):
        def admitted():
            try:
                assert gate.wait(1), "synthetic gate was not released"
                return method(*args, **kwargs)
            finally:
                finished.set()
        future = asyncio.get_running_loop().run_in_executor(pool, admitted)
        futures.append(future)
        return await asyncio.shield(future)

    monkeypatch.setattr(bridge.asyncio, "to_thread", queued)
    task = asyncio.create_task(value.browser.get("/api/platform/central/status"))
    try:
        await asyncio.sleep(.2)
        returned_before_admission = task.done()
    finally:
        gate.set()
    try:
        result = await asyncio.wait_for(task, 1)
        await asyncio.gather(*futures, return_exceptions=True)
        assert returned_before_admission, "route waited indefinitely for its executor"
        assert_unavailable(result)
        assert finished.is_set()
        assert value.reads == [] and transport.calls == []
        assert value.base.store.path.read_bytes() == value.before
    finally:
        pool.shutdown(wait=True)
        await close(value)


@pytest.mark.asyncio
async def test_default_sync_status_still_works_inside_an_existing_event_loop(tmp_path):
    calls = []
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json=response(request.url.path))
    native = CentralClient(tmp_path, transport=httpx.MockTransport(handler), url=ORIGIN)
    native.save_auth(auth())
    assert native.status()["authenticated"]
    assert calls == ["/v2/me"]


def test_deadline_rechecked_after_successful_lock_and_descriptor_released(tmp_path, monkeypatch):
    from easel.platform import session

    store = session.SessionStore(tmp_path)
    clock = [0.0]
    original = session.fcntl.flock

    def late_acquisition(stream, flags):
        original(stream, flags)
        clock[0] = 2.0

    with monkeypatch.context() as patch:
        patch.setattr(session, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=time.sleep))
        patch.setattr(session.fcntl, "flock", late_acquisition)
        with pytest.raises(session.SessionLockTimeout):
            with store.locked(deadline=1):
                pytest.fail("expired lock acquisition admitted state access")
    with store.locked(deadline=time.monotonic() + .2):
        assert store.read() == {}


def test_timed_out_contender_cannot_mutate_or_duplicate_an_owned_refresh(tmp_path):
    entered, release = threading.Event(), threading.Event()
    calls = []
    fresh = auth()
    fresh.update(access_token="ROTATED_ACCESS_SYNTHETIC", refresh_token="ROTATED_REFRESH_SYNTHETIC")

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/v2/auth/refresh":
            entered.set()
            assert release.wait(1)
            return httpx.Response(200, json=fresh)
        return httpx.Response(200, json=response(request.url.path))

    transport = httpx.MockTransport(handler)
    first = CentralClient(tmp_path, transport=transport, url=ORIGIN)
    expired = auth()
    expired["expires_at"] = 0
    first.save_auth(expired)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(first.status)
        try:
            assert entered.wait(1)
            marked = first.store.path.read_bytes()
            second = CentralClient(tmp_path, transport=transport, url=ORIGIN,
                                   absolute_deadline=time.monotonic() + .05)
            with pytest.raises(PlatformError) as error:
                second.status()
            assert error.value.status == 503
            assert first.store.path.read_bytes() == marked
            assert calls == [("POST", "/v2/auth/refresh")]
        finally:
            release.set()
        assert future.result(timeout=1)["authenticated"]
    assert first.store.read()["auth"] == fresh
    assert "refresh_guard" not in first.store.read()
    assert calls == [("POST", "/v2/auth/refresh"), ("GET", "/v2/me")]


@pytest.mark.asyncio
async def test_owned_successful_refresh_persists_before_verification_and_public_caps(tmp_path, monkeypatch):
    calls = []
    fresh = auth()
    fresh.update(access_token="ROTATED_ACCESS_SYNTHETIC", refresh_token="ROTATED_REFRESH_SYNTHETIC")

    def handler(request):
        calls.append((request.method, request.url.path, request.headers.get("authorization")))
        if request.url.path == "/v2/auth/refresh":
            return httpx.Response(200, json=fresh)
        return httpx.Response(200, json=response(request.url.path))

    value = fixture(tmp_path, monkeypatch, httpx.MockTransport(handler), budget=.5)
    expired = auth()
    expired["expires_at"] = 0
    with value.base.store.locked():
        value.base.store.write({"version": 2, "url": ORIGIN, "auth": expired})
    try:
        result = await value.browser.get("/api/platform/central/status")
        assert result.status_code == 200 and result.json()["authenticated"]
        assert calls == [("POST", "/v2/auth/refresh", None),
                         ("GET", "/v2/me", "Bearer ROTATED_ACCESS_SYNTHETIC"),
                         ("GET", "/v2/capabilities", None)]
        assert value.base.store.read()["auth"] == fresh
        assert "refresh_guard" not in value.base.store.read()
    finally:
        await close(value)


@pytest.mark.asyncio
@pytest.mark.parametrize("second_401", [False, True])
async def test_owned_access_401_allows_one_refresh_and_only_original_get_repair(tmp_path, second_401):
    calls = []
    fresh = auth()
    fresh.update(access_token="ROTATED_ACCESS_SYNTHETIC", refresh_token="ROTATED_REFRESH_SYNTHETIC")

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/v2/auth/refresh":
            return httpx.Response(200, json=fresh)
        if request.headers.get("authorization") == "Bearer ACCESS-CENTRAL-SECRET" or second_401:
            return httpx.Response(401, json={"error": {"code": "session_invalid"}})
        return httpx.Response(200, json=response(request.url.path))

    native = CentralClient(tmp_path, transport=httpx.MockTransport(handler), url=ORIGIN)
    native.save_auth(auth())
    native.absolute_deadline = time.monotonic() + .5
    result = await asyncio.to_thread(native.status)
    assert result["authenticated"] is (not second_401)
    assert result["scope"] == auth()["scope"]
    assert calls == [("GET", "/v2/me"), ("POST", "/v2/auth/refresh"), ("GET", "/v2/me")]
    assert native.store.read()["auth"] == fresh
    assert "refresh_guard" not in native.store.read()


@pytest.mark.asyncio
@pytest.mark.parametrize("repair", [False, True])
async def test_owned_refresh_timeout_is_durable_and_never_replayed_on_restart(tmp_path, repair):
    calls = []

    async def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/v2/auth/refresh":
            await asyncio.sleep(.6)
            return httpx.Response(200, json=auth())
        return httpx.Response(401, json={"error": {"code": "session_invalid"}})

    native = CentralClient(tmp_path, transport=httpx.MockTransport(handler), url=ORIGIN)
    original = auth()
    if not repair:
        original["expires_at"] = 0
    native.save_auth(original)
    native.absolute_deadline = time.monotonic() + .15
    result = await asyncio.to_thread(native.status)
    assert result["authenticated"] is False
    assert result["error"]["code"] == "refresh_uncertain"
    saved = native.store.read()
    assert saved["auth"] == original
    assert saved["refresh_guard"]["state"] in {"sending", "uncertain"}
    assert calls == ([("GET", "/v2/me")] if repair else []) + [("POST", "/v2/auth/refresh")]
    restarted = CentralClient(tmp_path, transport=native.transport, url=ORIGIN)
    assert restarted.status()["error"]["code"] == "refresh_uncertain"
    assert len([row for row in calls if row[0] == "POST"]) == 1
    assert "SECRET" not in json.dumps(result)


@pytest.mark.asyncio
async def test_owned_public_caps_cannot_refresh_or_send_retained_bearer(tmp_path):
    calls = []
    def handler(request):
        calls.append((request.method, request.url.path, request.headers.get("authorization")))
        return httpx.Response(200, json=response(request.url.path))
    native = CentralClient(tmp_path, transport=httpx.MockTransport(handler), url=ORIGIN)
    native.save_auth(auth())
    with native.store.locked():
        state = native.store.read()
        state["refresh_guard"] = {"state": "uncertain"}
        native.store.write(state)
    before = native.store.path.read_bytes()
    native.absolute_deadline = time.monotonic() + .5
    result = await asyncio.to_thread(native.call, "GET", "/v2/capabilities?product_id=easel", public=True)
    assert result["auth_mode"] == "fixture"
    assert calls == [("GET", "/v2/capabilities", None)]
    assert native.store.path.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["secret", "compressed_large", "gzip_ok", "wrong_scope", "unknown_401"])
async def test_owned_transport_preserves_central_decoding_and_auth_error_rules(tmp_path, fault):
    def handler(request):
        if fault == "secret":
            return httpx.Response(200, json={"api_key": "PRIVATE_REMOTE_SECRET"})
        if fault.startswith("gzip") or fault == "compressed_large":
            raw = json.dumps(response(request.url.path)).encode() if fault == "gzip_ok" else b'{"padding":"' + b"x" * (2 * 1024 * 1024) + b'"}'
            return httpx.Response(200, content=gzip.compress(raw), headers={"content-encoding": "gzip"})
        if fault == "unknown_401":
            return httpx.Response(401, json={"error": {"code": "PRIVATE_REMOTE_SECRET"}})
        value = response(request.url.path)
        value["scope"] = {**value["scope"], "user_id": "other"}
        return httpx.Response(200, json=value)

    native = CentralClient(tmp_path, transport=httpx.MockTransport(handler), url=ORIGIN)
    native.save_auth(auth())
    before = native.store.path.read_bytes()
    native.absolute_deadline = time.monotonic() + .5
    result = await asyncio.to_thread(native.status)
    assert result["authenticated"] is (fault == "gzip_ok")
    if fault != "gzip_ok":
        assert result["error"]["code"] in {"platform_unavailable", "account_changed"}
    assert "PRIVATE_REMOTE_SECRET" not in json.dumps(result)
    assert native.store.path.read_bytes() == before


@pytest.mark.asyncio
async def test_route_timeout_does_not_claim_worker_stopped_and_refresh_fence_finishes(tmp_path, monkeypatch):
    calls, cancelled = [], threading.Event()
    async def handler(request):
        calls.append((request.method, request.url.path))
        assert request.url.path == "/v2/auth/refresh"
        try:
            await asyncio.sleep(.6)
            return httpx.Response(200, json=auth())
        finally:
            cancelled.set()

    value = fixture(tmp_path, monkeypatch, httpx.MockTransport(handler), budget=.2)
    original = auth()
    original["expires_at"] = 0
    with value.base.store.locked():
        value.base.store.write({"version": 2, "url": ORIGIN, "auth": original})
    try:
        result = await value.browser.get("/api/platform/central/status")
        assert_unavailable(result)
        until = time.monotonic() + .4
        while time.monotonic() < until:
            saved = value.base.store.read()
            if cancelled.is_set() and saved.get("refresh_guard", {}).get("state") == "uncertain":
                break
            await asyncio.sleep(.005)
        assert cancelled.is_set()
        assert saved["auth"] == original and saved["refresh_guard"]["state"] == "uncertain"
        with value.base.store.locked(deadline=time.monotonic() + .2):
            assert value.base.store.read()["auth"] == original
        restarted = CentralClient(tmp_path, transport=value.base.transport, url=ORIGIN)
        assert restarted.status()["error"]["code"] == "refresh_uncertain"
        assert calls == [("POST", "/v2/auth/refresh")]
    finally:
        await close(value)


@pytest.mark.asyncio
async def test_lock_wait_is_charged_before_identity_and_capabilities(tmp_path, monkeypatch):
    transport = DelayedTransport(.07)
    value = fixture(tmp_path, monkeypatch, transport, budget=.2)
    holder = value.base.store.locked()
    holder.__enter__()
    started = time.monotonic()
    task = asyncio.create_task(value.browser.get("/api/platform/central/status"))
    try:
        await asyncio.sleep(.09)
    finally:
        holder.__exit__(None, None, None)
    try:
        result = await asyncio.wait_for(task, 1)
        assert_unavailable(result)
        assert time.monotonic() - started < .5
        assert [row[1] for row in transport.calls] == ["/v2/me", "/v2/capabilities"]
        assert 0 < transport.calls[0][3] < .16
        assert 0 < transport.calls[1][3] < .08
        assert value.base.store.path.read_bytes() == value.before
    finally:
        await close(value)


@pytest.mark.asyncio
async def test_remote_peer_is_rejected_before_status_owned_client_exists(tmp_path, monkeypatch):
    from easel.platform import central_web as bridge
    def forbidden(*args, **kwargs):
        pytest.fail("remote peer constructed an account client")
    monkeypatch.setattr(bridge, "CentralClient", forbidden)
    app = FastAPI()
    app.include_router(bridge.create_router(tmp_path, lambda: None, url=ORIGIN))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("198.51.100.2", 1234)),
                                 base_url="http://local") as browser:
        result = await browser.get("/api/platform/central/status")
    assert result.status_code == 403
    assert not (tmp_path / "outputs").exists()


@pytest.mark.asyncio
async def test_dns_gate_remains_closed_when_worker_exits_and_session_lock_is_released(tmp_path, monkeypatch):
    """Default HTTP path, no DNS/network: both resolver mechanisms are gated."""
    from easel.platform import central_web as bridge

    value = fixture(tmp_path, monkeypatch, None, budget=.3)
    entered, release, worker_done = threading.Event(), threading.Event(), threading.Event()
    child_entered, child_release = tmp_path / "resolver-entered", tmp_path / "resolver-release"
    processes, commands, connections = [], [], []
    real_to_thread = asyncio.to_thread
    real_spawn = asyncio.create_subprocess_exec

    def gated_dns(*args, **kwargs):
        entered.set()
        assert release.wait(2), "synthetic parent resolver was not released"
        raise socket.gaierror("synthetic resolver gate")

    def forbidden_connect(*args, **kwargs):
        connections.append(True)
        raise AssertionError("DNS timeout must never start a network connection")

    async def synthetic_resolver(*args, **kwargs):
        commands.append((args, kwargs))
        code = ("import pathlib,sys,time; "
                "pathlib.Path(sys.argv[1]).touch(); "
                "gate=pathlib.Path(sys.argv[2]); "
                "exec('while not gate.exists(): time.sleep(.01)'); "
                "print('[\"127.0.0.1\"]',flush=True)")
        process = await real_spawn(sys.executable, "-I", "-B", "-c", code,
                                   str(child_entered), str(child_release), **kwargs)
        processes.append(process)
        return process

    async def observed_worker(method, *args, **kwargs):
        def run():
            try:
                return method(*args, **kwargs)
            finally:
                worker_done.set()
        return await real_to_thread(run)

    monkeypatch.setattr(socket, "getaddrinfo", gated_dns)
    monkeypatch.setattr(socket.socket, "connect", forbidden_connect)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", synthetic_resolver)
    monkeypatch.setattr(bridge.asyncio, "to_thread", observed_worker)
    try:
        result = await value.browser.get("/api/platform/central/status")
        assert_unavailable(result)
        until = time.monotonic() + .25
        while not worker_done.is_set() and time.monotonic() < until:
            await asyncio.sleep(.005)
        assert entered.is_set() or child_entered.exists(), "probe never entered a resolver"
        finished_before_gate_release = worker_done.is_set()
        try:
            with value.base.store.locked(deadline=time.monotonic() + .05):
                available_before_gate_release = True
        except TimeoutError:
            available_before_gate_release = False
        assert finished_before_gate_release, "resolver executor shutdown retained the worker"
        assert available_before_gate_release, "DNS work retained the session lock past deadline"
        assert not release.is_set() and not child_release.exists()
        assert processes and all(process.returncode is not None for process in processes)
        assert all(command[0][-2:] == ("accounts.example.invalid", "443") for command in commands)
        assert "ACCESS-CENTRAL-SECRET" not in repr(commands)
        assert "REFRESH-CENTRAL-SECRET" not in repr(commands)
        assert connections == []
        assert value.base.store.path.read_bytes() == value.before
    finally:
        release.set()
        child_release.touch()
        for process in processes:
            if process.returncode is None:
                process.kill()
        until = time.monotonic() + 1
        while not worker_done.is_set() and time.monotonic() < until:
            await asyncio.sleep(.005)
        await close(value)
    assert connections == []
    assert value.base.store.path.read_bytes() == value.before


def isolated_resolver(monkeypatch, addresses, *, admission_delay=0, process_delay=0):
    """Run a real owned child with synthetic stdout and no getaddrinfo call."""
    spawning = asyncio.create_subprocess_exec
    children, commands = [], []
    async def spawn(*args, **kwargs):
        commands.append((args, kwargs))
        assert kwargs["stdin"] == -3 and kwargs["stderr"] == -3
        assert kwargs["close_fds"] is True
        assert set(kwargs["env"]) == {"PATH", "LANG"}
        assert args[1:3] == ("-I", "-B")
        code = ("import sys,time;time.sleep(" + repr(process_delay) + ");sys.stdout.write("
                + repr(json.dumps(addresses)) + ");sys.stdout.flush()")
        process = await spawning(sys.executable, "-I", "-B", "-c", code, **kwargs)
        children.append(process)
        if admission_delay:
            await asyncio.sleep(admission_delay)
        return process
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    return children, commands


def recording_httpcore(monkeypatch, *, connect_failures=0, fault=None, payload=None):
    """The installed HTTPcore connection/TLS/HTTP path, with no real sockets."""
    import httpcore
    from httpcore._backends.auto import AutoBackend

    peers, tls, writes, closed = [], [], [], []
    raw = json.dumps(payload or response("/v2/me")).encode()
    wire = b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(raw)).encode() + b"\r\nConnection: close\r\n\r\n" + raw

    class Stream(httpcore.AsyncNetworkStream):
        async def read(self, max_bytes, timeout=None):
            if fault == "read":
                raise httpcore.ReadTimeout("synthetic read outcome unknown")
            if fault == "write":
                # HTTPcore may still accept an authoritative response after
                # WriteError. A lost response models an actually unknown POST.
                raise httpcore.ReadError("synthetic response lost after write")
            return wire

        async def write(self, buffer, timeout=None):
            writes.append(bytes(buffer))
            if fault == "write":
                raise httpcore.WriteError("synthetic write outcome unknown")

        async def aclose(self):
            closed.append(True)

        async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            tls.append((server_hostname, ssl_context.verify_mode, ssl_context.check_hostname))
            return self

        def get_extra_info(self, info):
            return None

    async def connect(self, host, port, **kwargs):
        peers.append((host, port))
        if len(peers) <= connect_failures:
            raise httpcore.ConnectError("synthetic pre-HTTP failure")
        return Stream()

    monkeypatch.setattr(AutoBackend, "connect_tcp", connect)
    return SimpleNamespace(peers=peers, tls=tls, writes=writes, closed=closed)


@pytest.mark.asyncio
@pytest.mark.parametrize("origin,host_header,tls_name", [
    ("https://accounts.example.invalid:9443", b"accounts.example.invalid:9443", "accounts.example.invalid"),
    ("https://账号.example.invalid", b"xn--rprr44k.example.invalid", "xn--rprr44k.example.invalid"),
    ("https://[::1]:9443", b"[::1]:9443", "::1"),
])
async def test_real_httpcore_preserves_original_host_sni_and_certificate_verification(tmp_path, monkeypatch, origin, host_header, tls_name):
    children, commands = isolated_resolver(monkeypatch, ["192.0.2.1"])
    trace = recording_httpcore(monkeypatch)
    native = CentralClient(tmp_path, url=origin)
    native.save_auth(auth())
    native.absolute_deadline = time.monotonic() + 1
    result = await asyncio.to_thread(native.status)
    assert result["authenticated"]
    assert trace.tls == [(tls_name, ssl.CERT_REQUIRED, True)]
    assert b"Host: " + host_header + b"\r\n" in b"".join(trace.writes)
    assert all(child.returncode == 0 for child in children)
    if tls_name == "::1":
        assert commands == [] and trace.peers == [("::1", 9443)]
    else:
        assert len(commands) == 1
        assert commands[0][0][-2] == tls_name
        assert trace.peers == [("192.0.2.1", 9443 if ":9443" in origin else 443)]
    assert "CENTRAL-SECRET" not in repr(commands)


@pytest.mark.asyncio
async def test_resolved_numeric_connection_never_reenters_parent_dns(tmp_path, monkeypatch):
    children, _ = isolated_resolver(monkeypatch, ["192.0.2.1"])
    lookups, peers = [], []
    def forbidden_dns(*args, **kwargs):
        lookups.append(args)
        raise AssertionError("parent DNS executor must not be used")
    def synthetic_connect(self, address):
        peers.append(address)
        raise ConnectionRefusedError("synthetic no-network connection")
    monkeypatch.setattr(socket, "getaddrinfo", forbidden_dns)
    monkeypatch.setattr(socket.socket, "connect", synthetic_connect)
    native = CentralClient(tmp_path, url=ORIGIN)
    native.save_auth(auth())
    before = native.store.path.read_bytes()
    native.absolute_deadline = time.monotonic() + 1
    result = await asyncio.to_thread(native.status)
    assert not result["authenticated"] and result["error"]["code"] == "platform_unavailable"
    assert lookups == [] and peers == [("192.0.2.1", 443)]
    assert all(child.returncode == 0 for child in children)
    assert native.store.path.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "read", "write"])
async def test_address_fallback_only_before_http_and_never_replays_refresh_post(tmp_path, monkeypatch, fault):
    children, _ = isolated_resolver(monkeypatch, ["192.0.2.1", "192.0.2.2"])
    fresh = auth()
    fresh.update(access_token="ROTATED_ACCESS_SYNTHETIC", refresh_token="ROTATED_REFRESH_SYNTHETIC")
    trace = recording_httpcore(monkeypatch, connect_failures=1 if fault is None else 0,
                               fault=fault, payload=fresh)
    native = CentralClient(tmp_path, url=ORIGIN)
    expired = auth()
    expired["expires_at"] = 0
    native.save_auth(expired)
    native.absolute_deadline = time.monotonic() + 1
    result = await asyncio.to_thread(native.status)
    posts = b"".join(trace.writes).count(b"POST /v2/auth/refresh HTTP/1.1\r\n")
    assert posts == 1
    if fault is None:
        # The subsequent identity request has its own fresh DNS result, but
        # failed only scope validation because this stream returns refresh JSON.
        assert trace.peers[:2] == [("192.0.2.1", 443), ("192.0.2.2", 443)]
        assert native.store.read()["auth"] == fresh
        assert "refresh_guard" not in native.store.read()
    else:
        assert trace.peers == [("192.0.2.1", 443)]
        assert result["error"]["code"] == "refresh_uncertain"
        assert native.store.read()["auth"] == expired
        assert native.store.read()["refresh_guard"]["state"] == "uncertain"
    assert all(child.returncode == 0 for child in children)


@pytest.mark.asyncio
async def test_cancelled_resolver_admission_retains_ownership_and_reaps_child(tmp_path, monkeypatch):
    from easel.platform import central
    children, _ = isolated_resolver(monkeypatch, ["192.0.2.1"], admission_delay=.15, process_delay=5)
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(.04):
            await central._status_addresses("accounts.example.invalid", 443)
    assert children and all(child.returncode is not None and child.returncode != 0 for child in children)


@pytest.mark.asyncio
@pytest.mark.parametrize("addresses", [[], ["hostname.invalid"], ["127.0.0.1"] * 17, "x" * 5000])
async def test_owned_resolver_rejects_invalid_or_excessive_output(tmp_path, monkeypatch, addresses):
    from easel.platform import central
    children, _ = isolated_resolver(monkeypatch, addresses)
    with pytest.raises((PlatformError, ValueError)):
        await central._status_addresses("accounts.example.invalid", 443)
    assert children and all(child.returncode == 0 for child in children)


@pytest.mark.asyncio
async def test_actual_isolated_resolver_recipe_bounds_and_deduplicates_synthetic_dns(monkeypatch):
    from easel.platform import central
    real_spawn = asyncio.create_subprocess_exec
    children = []
    async def stubbed_recipe(*args, **kwargs):
        assert args[4] == central._STATUS_RESOLVER
        prefix = ("import socket; "
                  "socket.getaddrinfo=lambda host,port,**options: "
                  "[(socket.AF_INET,socket.SOCK_STREAM,6,'',('192.0.2.'+str(i),port)) "
                  "for i in list(range(1,24))+[1,2]]; ")
        process = await real_spawn(*args[:4], prefix + args[4], *args[5:], **kwargs)
        children.append(process)
        return process
    monkeypatch.setattr(asyncio, "create_subprocess_exec", stubbed_recipe)
    async with asyncio.timeout(1):
        addresses = await central._status_addresses("accounts.example.invalid", 443)
    assert addresses == ["192.0.2." + str(i) for i in range(1, 17)]
    assert children and all(child.returncode == 0 for child in children)
