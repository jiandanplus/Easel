import asyncio
import hashlib
import base64
import json
import time
from urllib.parse import urlencode

from fastapi import FastAPI
import httpx
import pytest

from easel.harnesses.base import HarnessError
from tests.test_central_platform_client import auth


async def setup(tmp_path, *, timeout=300):
    from easel.platform.central_web import create_router

    state = {
        "active": False,
        "begin": None,
        "redeems": 0,
        "guard_threads": [],
        "revocations": [],
    }

    def handle(r):
        if r.url.path.endswith("transactions"):
            state["begin"] = json.loads(r.content)
            return httpx.Response(
                200,
                json={
                    "transaction_id": "transaction",
                    "authorize_url": "http://127.0.0.1:8791/oidc/easel/authorize?transaction_id=transaction",
                    "expires_at": time.time() + 300,
                },
            )
        if r.url.path.endswith("redeem"):
            state["redeems"] += 1
            if state.get("entered"):
                state["entered"].set()
                assert state["release"].wait(3)
            body = json.loads(r.content)
            challenge = (
                base64.urlsafe_b64encode(
                    hashlib.sha256(body["code_verifier"].encode()).digest()
                )
                .rstrip(b"=")
                .decode()
            )
            assert challenge == state["begin"]["handoff_challenge"]
            assert body["state"] == state["begin"]["local_state"]
            assert body["callback_uri"] == state["begin"]["callback_uri"]
            return httpx.Response(200, json=auth())
        if r.method == "DELETE" and r.url.path == "/v2/sessions/device":
            state["auth_error"] = "session_invalid"
        if r.url.path.endswith("logout"):
            state["revocations"].append(r.headers.get("authorization"))
        if r.url.path.endswith(("me", "refresh")) and state.get("auth_error"):
            return httpx.Response(401, json={"error": {"code": state["auth_error"]}})
        if r.url.path.endswith("me"):
            return httpx.Response(
                200, json={"account": auth()["account"], "scope": auth()["scope"]}
            )
        if r.url.path.endswith("capabilities"):
            return httpx.Response(
                200,
                json={"auth_mode": "logto", "subscription_execution_enabled": False},
            )
        return httpx.Response(200, json={"ok": True})

    def guard():
        import threading

        state["guard_threads"].append(threading.get_ident())
        if state["active"]:
            raise HarnessError("tasks active")

    router = create_router(
        tmp_path,
        guard,
        transport=httpx.MockTransport(handle),
        url="http://127.0.0.1:8791",
        login_timeout=timeout,
    )
    app = FastAPI()
    app.include_router(router)
    c = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
        base_url="http://local",
    )
    return router, c, state


async def callback(fixture, **changes):
    params = {
        "code": "handoff-secret",
        "state": fixture["begin"]["local_state"],
        **changes,
    }
    async with httpx.AsyncClient(trust_env=False, timeout=3) as browser:
        return await browser.get(
            fixture["begin"]["callback_uri"] + "?" + urlencode(params)
        )


@pytest.mark.asyncio
async def test_owned_callback_state_privacy_once_and_shutdown(tmp_path):
    import threading

    router, c, state = await setup(tmp_path)
    try:
        start = await c.post("/api/platform/central/auth/start")
        assert start.status_code == 200, start.text
        assert set(start.json()) == {"authorization_url", "expires_at"}
        assert "SECRET" not in start.text
        wrong = await callback(state, state="wrong")
        assert wrong.status_code == 400 and state["redeems"] == 0
        response = await callback(state)
        assert response.status_code == 200 and "SECRET" not in response.text
        status = (await c.get("/api/platform/central/status")).json()
        assert status["authenticated"] and not status["login"]["pending"]
        assert "SECRET" not in json.dumps(status)
        assert state["redeems"] == 1
        assert set(state["guard_threads"]) == {threading.get_ident()}
        with pytest.raises(httpx.HTTPError):
            await callback(state)
        assert (await c.post("/api/platform/central/logout")).json()[
            "authenticated"
        ] is False
    finally:
        await router.shutdown()
        await c.aclose()


@pytest.mark.asyncio
async def test_cancel_timeout_and_guard_before_redeem(tmp_path):
    router, c, state = await setup(tmp_path, timeout=0.05)
    try:
        await c.post("/api/platform/central/auth/start")
        cancelled = await c.post("/api/platform/central/auth/cancel")
        assert cancelled.status_code == 200 and not cancelled.json()["login"]["pending"]
        with pytest.raises(httpx.HTTPError):
            await callback(state)
        await c.post("/api/platform/central/auth/start")
        await asyncio.sleep(0.1)
        assert (await c.get("/api/platform/central/status")).json()["login"]["error"][
            "code"
        ] == "login_expired"
        await c.post("/api/platform/central/auth/start")
        state["active"] = True
        assert (await callback(state)).status_code == 409
        assert state["redeems"] == 0
        assert not (await c.get("/api/platform/central/status")).json()["authenticated"]
    finally:
        await router.shutdown()
        await c.aclose()


@pytest.mark.asyncio
async def test_remote_peer_denied_even_with_forwarded_headers(tmp_path):
    from easel.platform.central_web import create_router

    app = FastAPI()
    router = create_router(tmp_path, lambda: None)
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("198.51.100.2", 1234)),
        base_url="http://localhost",
    ) as c:
        for method, path in [
            ("GET", "status"),
            ("POST", "auth/start"),
            ("POST", "auth/cancel"),
            ("POST", "logout"),
            ("GET", "wallet"),
            ("DELETE", "devices/device"),
        ]:
            response = await c.request(
                method,
                "/api/platform/central/" + path,
                headers={"X-Forwarded-For": "127.0.0.1"},
            )
            assert response.status_code == 403
    await router.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["cancel", "task"])
async def test_guard_and_cancellation_during_redemption_never_save_late_session(
    tmp_path, action
):
    import threading

    router, c, state = await setup(tmp_path)
    state["entered"], state["release"] = threading.Event(), threading.Event()
    try:
        await c.post("/api/platform/central/auth/start")
        response = asyncio.create_task(callback(state))
        assert await asyncio.to_thread(state["entered"].wait, 2)
        assert router.platform_changes == 1
        assert (await callback(state)).status_code == 409
        if action == "cancel":
            cancelled = await c.post("/api/platform/central/auth/cancel")
            assert cancelled.status_code == 200
        else:
            state["active"] = True
        state["release"].set()
        assert (await response).status_code == 409
        assert not (await c.get("/api/platform/central/status")).json()["authenticated"]
        assert router.platform_changes == 0
        assert state["revocations"] == ["Bearer ACCESS-CENTRAL-SECRET"]
        assert not (
            tmp_path / "outputs/_harness/central-platform-session.json"
        ).exists()
    finally:
        state["release"].set()
        await router.shutdown()
        await c.aclose()


@pytest.mark.asyncio
async def test_app_admission_observes_central_identity_changes(tmp_path, monkeypatch):
    from fastapi import HTTPException
    import web.app as web

    monkeypatch.setattr(web, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(web._central_platform_router, "platform_changes", 1)
    with pytest.raises(HTTPException) as error:
        await web._accept_task_context(
            web.ChatRequest(message="synthetic"), "codex", {}
        )
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_local_revocation_status_logout_and_fresh_browser_login_recover(tmp_path):
    router, c, state = await setup(tmp_path)
    try:
        assert (await c.post("/api/platform/central/auth/start")).status_code == 200
        assert (await callback(state)).status_code == 200
        assert (
            await c.delete("/api/platform/central/devices/device")
        ).status_code == 200
        status = (await c.get("/api/platform/central/status")).json()
        assert (
            not status["authenticated"] and status["error"]["code"] == "session_invalid"
        )
        assert status["account"] is None and status["scope"] is None
        assert "SECRET" not in json.dumps(status)
        assert (await c.post("/api/platform/central/logout")).status_code == 200
        assert (await c.post("/api/platform/central/auth/start")).status_code == 200
        state.pop("auth_error")
        assert (await callback(state)).status_code == 200
        assert (await c.get("/api/platform/central/status")).json()["authenticated"]
        assert state["redeems"] == 2
    finally:
        await router.shutdown()
        await c.aclose()


@pytest.mark.asyncio
async def test_redeem_route_accepts_only_a_local_code_request(tmp_path):
    router, c, state = await setup(tmp_path)
    from easel.platform.central import CentralClient
    CentralClient(tmp_path, url="http://127.0.0.1:8791").save_auth(auth())
    base = router.prefix if getattr(router, "prefix", "") else ""
    ok = await c.post(base + "/redemptions", json={"code": " ABCD-EFGH-JKMN-PQRS "})
    assert ok.status_code == 200 and ok.json() == {"ok": True}
    foreign = await c.post(base + "/redemptions", json={"code": "ABCD-EFGH-JKMN-PQRS"}, headers={"origin": "https://evil.example"})
    assert foreign.status_code == 403
    for body in ({"code": "x", "extra": 1}, ["code"], {"code": 7}):
        assert (await c.post(base + "/redemptions", json=body)).status_code == 400
    assert (await c.post(base + "/redemptions", content=b"{" * 600, headers={"content-type": "application/json"})).status_code == 400
    await c.aclose()
