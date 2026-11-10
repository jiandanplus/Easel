"""Private bridge tests: all transport and workspaces are synthetic."""

import json
from concurrent.futures import ThreadPoolExecutor
import httpx
import pytest
from easel.harnesses.base import HarnessError
from easel.platform.client import PlatformClient


def auth(access="access-SECRET", refresh="refresh-SECRET"):
    return dict(
        access_token=access,
        refresh_token=refresh,
        expires_at=9999999999,
        refresh_expires_at=99999999999,
        account={"id": "owner", "email": "a@example.test", "email_verified": False},
        session_id="device",
    )


def client(tmp_path, handler):
    return PlatformClient(tmp_path, transport=httpx.MockTransport(handler))


def test_private_session_and_url_change_preserve_bytes(tmp_path):
    c = client(tmp_path, lambda r: httpx.Response(200, json=auth()))
    c.configure("http://127.0.0.1:8790")
    c.login("a@example.test", "synthetic password")
    path = tmp_path / "outputs/_harness/platform-session.json"
    original = path.read_bytes()
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert "SECRET" not in json.dumps(c.status())
    with pytest.raises(HarnessError):
        c.configure("http://localhost:9000")
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    "url",
    [
        "https://x.test/?q=a",
        "https://u:p@x.test",
        "http://x.test",
        "https://x.test/#x",
        "https://x.test:bad",
    ],
)
def test_reject_unsafe_urls(tmp_path, url):
    with pytest.raises(HarnessError):
        client(tmp_path, lambda r: None).configure(url)


def test_malformed_and_symlink_preserved(tmp_path):
    path = tmp_path / "outputs/_harness/platform-session.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"{invalid")
    c = client(tmp_path, lambda r: None)
    with pytest.raises(HarnessError):
        c.configure("http://localhost:8790")
    assert path.read_bytes() == b"{invalid"
    path.unlink()
    outside = tmp_path / "secret"
    outside.write_text("preserve")
    path.symlink_to(outside)
    with pytest.raises(HarnessError):
        c.configure("http://localhost:8790")
    assert outside.read_text() == "preserve"


def test_refresh_serialized_across_clients(tmp_path):
    calls = []

    def handle(r):
        calls.append(r.url.path)
        if r.url.path.endswith("login"):
            return httpx.Response(200, json={**auth(), "expires_at": 0})
        if r.url.path.endswith("refresh"):
            return httpx.Response(200, json=auth("new-access", "new-refresh"))
        assert r.headers["authorization"] == "Bearer new-access"
        return httpx.Response(
            200, json={"account": auth()["account"], "session_id": "device"}
        )

    a = client(tmp_path, handle)
    a.configure("http://localhost:8790")
    a.login("x", "y")
    b = client(tmp_path, handle)
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda c: c.account(), [a, b]))
    assert calls.count("/v1/auth/refresh") == 1


def test_network_failure_preserves_session(tmp_path):
    def handle(r):
        if r.url.path.endswith("login"):
            return httpx.Response(200, json={**auth(), "expires_at": 0})
        raise httpx.ConnectError("access-SECRET", request=r)

    c = client(tmp_path, handle)
    c.configure("http://localhost:8790")
    c.login("x", "y")
    path = tmp_path / "outputs/_harness/platform-session.json"
    before = path.read_bytes()
    with pytest.raises(HarnessError, match="平台") as exc:
        c.account()
    assert "SECRET" not in str(exc.value)
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_local_router_guard_and_safe_http_shapes(tmp_path):
    from fastapi import FastAPI
    from easel.platform.web import create_router

    calls = []
    active = [False]

    def handle(r):
        calls.append(r.url.path)
        if r.url.path.endswith("login") or r.url.path.endswith("register"):
            return httpx.Response(200, json=auth())
        return httpx.Response(200, json={"plans": [], "sessions": [], "ok": True})

    app = FastAPI()

    def guard():
        if active[0]:
            raise HarnessError("请等待任务完成")

    app.include_router(
        create_router(lambda: tmp_path, guard, transport=httpx.MockTransport(handle))
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
        base_url="http://local",
    ) as c:
        assert (
            await c.post(
                "/api/platform/configure", json={"url": "http://localhost:8790"}
            )
        ).status_code == 200
        response = await c.post(
            "/api/platform/login",
            json={"email": "a@example.test", "password": "synthetic password"},
        )
        assert response.status_code == 200 and "SECRET" not in response.text
        assert (await c.get("/api/platform/devices")).status_code == 200
        active[0] = True
        assert (await c.post("/api/platform/logout")).status_code == 409
        assert not any(x.endswith("logout") for x in calls)
        active[0] = False
        assert (await c.post("/api/platform/logout")).status_code == 200


def test_status_checks_revocation_without_exposing_bearers(tmp_path):
    def handle(r):
        if r.url.path.endswith("login"):
            return httpx.Response(200, json=auth())
        return httpx.Response(401, json={"error": {"code": "invalid_authentication"}})

    c = client(tmp_path, handle)
    c.configure("http://localhost:8790")
    c.login("x", "y")
    status = c.status()
    assert (
        not status["authenticated"]
        and status["error"]["code"] == "invalid_authentication"
    )
    assert "SECRET" not in json.dumps(status)


@pytest.mark.asyncio
async def test_local_router_uses_actual_backend_contract(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from easel.platform.web import create_router
    from easel_platform import create_app
    from easel_platform import Settings

    backend = create_app(Settings(database=tmp_path / "platform.sqlite"))
    with TestClient(backend) as server:

        def dispatch(request):
            response = server.request(
                request.method,
                request.url.path,
                content=request.content,
                headers={
                    k: v
                    for k, v in request.headers.items()
                    if k in ("authorization", "content-type")
                },
            )
            return httpx.Response(response.status_code, json=response.json())

        local = FastAPI()
        local.include_router(
            create_router(
                lambda: tmp_path, lambda: None, transport=httpx.MockTransport(dispatch)
            )
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=local, client=("127.0.0.1", 1234)),
            base_url="http://local",
        ) as c:
            await c.post(
                "/api/platform/configure", json={"url": "http://127.0.0.1:8790"}
            )
            response = await c.post(
                "/api/platform/register",
                json={
                    "email": "synthetic@example.test",
                    "password": "synthetic-password-123",
                },
            )
            assert response.status_code == 200
            assert not any(
                key in response.text for key in ("access_token", "refresh_token")
            )
            status = (await c.get("/api/platform/status")).json()
            assert status["authenticated"] and not status["account"]["email_verified"]
            assert (await c.get("/api/platform/entitlements")).json() == {
                "entitlements": []
            }
            devices = (await c.get("/api/platform/devices")).json()["sessions"]
            assert len(devices) == 1
            assert (
                await c.delete("/api/platform/devices/" + devices[0]["id"])
            ).status_code == 200
            assert not (await c.get("/api/platform/status")).json()["authenticated"]
            assert (await c.post("/api/platform/logout")).status_code == 200
            assert (
                await c.post(
                    "/api/platform/login",
                    json={
                        "email": "synthetic@example.test",
                        "password": "synthetic-password-123",
                    },
                )
            ).status_code == 200
            assert (await c.post("/api/platform/logout")).status_code == 200


def test_missing_posix_lock_fails_subscription_safely_without_import_breakage(
    tmp_path, monkeypatch
):
    from easel.platform import session

    monkeypatch.setattr(session, "fcntl", None)
    with pytest.raises(HarnessError, match="当前系统"):
        with session.SessionStore(tmp_path).locked():
            pytest.fail("unsupported lock entered")
    assert not (tmp_path / "outputs").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "peer", ["198.51.100.23", "testclient", "::ffff:198.51.100.23"]
)
async def test_platform_router_denies_remote_before_client(tmp_path, monkeypatch, peer):
    from fastapi import FastAPI
    import easel.platform.web as bridge

    app = FastAPI()
    app.include_router(bridge.create_router(tmp_path, lambda: None))
    monkeypatch.setattr(
        bridge, "PlatformClient", lambda *a, **k: pytest.fail("remote platform call")
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(peer, 1234)),
        base_url="http://localhost",
    ) as c:
        for method, path in [
            ("GET", "status"),
            ("GET", "devices"),
            ("GET", "plans"),
            ("GET", "entitlements"),
            ("GET", "usage"),
            ("GET", "models"),
            ("POST", "configure"),
            ("POST", "login"),
            ("POST", "register"),
            ("POST", "logout"),
            ("DELETE", "devices/fake"),
        ]:
            response = await c.request(
                method,
                "/api/platform/" + path,
                json={},
                headers={"Origin": "http://localhost", "X-Forwarded-For": "127.0.0.1"},
            )
            assert response.status_code == 403, (path, response.text)


@pytest.mark.parametrize("name", ["platform-session.json", "platform-session.lock"])
def test_special_session_files_fail_promptly(tmp_path, name):
    import os
    import signal
    from easel.platform.session import SessionStore

    store = SessionStore(tmp_path)
    store.directory.mkdir(parents=True)
    os.mkfifo(store.directory / name)

    def timed_out(*args):
        raise AssertionError("special session file blocked")

    previous = signal.signal(signal.SIGALRM, timed_out)
    signal.setitimer(signal.ITIMER_REAL, 0.5)
    try:
        with pytest.raises(HarnessError):
            with store.locked():
                store.read()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
