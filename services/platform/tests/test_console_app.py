"""HTTP boundaries of the independent management BFF (no product credentials)."""
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest

from platform_service.errors import PlatformError


ORIGIN = "http://127.0.0.1:18875"


def settings():
    return SimpleNamespace(database_url="postgresql://unused/easel_s1_test", public_url=ORIGIN,
                           issuer="http://127.0.0.1:18763/oidc", db_schema="test",
                           allow_loopback_http=True, pool_min_size=1, pool_max_size=2,
                           session_ttl=28800, auth_ttl=300)


class Auth:
    def __init__(self, *, read_only=False):
        self.role = "viewer" if read_only else "admin"
        self.permissions = {"console:read"} if read_only else {"console:read", "plans:publish", "billing:publish", "execution:publish"}
        self.logged_out = False

    async def authenticate(self, cookie):
        if cookie != "session-proof" or self.logged_out:
            raise PlatformError(401, "console_authentication_required")
        return SimpleNamespace(id="admin-proof", role=self.role, permissions=frozenset(self.permissions),
                               csrf_token="csrf-proof", session_hash="never-export-this")

    async def begin_login(self):
        return {"url": "http://127.0.0.1:18763/oidc/auth?state=provider-proof", "cookie": "login-proof", "expires_at": "2030-01-01T00:00:00+00:00"}

    async def callback(self, code, state, cookie):
        if (code, state, cookie) != ("code-proof", "state-proof", "login-proof"):
            raise PlatformError(400, "console_login_invalid")
        return {"status": "pending", "request_id": "pending-proof-1234567890"}

    async def logout(self, cookie):
        assert cookie == "session-proof"
        self.logged_out = True


class Queries:
    async def overview(self, principal):
        return {"observed_at": "2026-10-23T00:00:00+00:00", "users": 2}

    async def users(self, principal, **kwargs):
        return {"items": [{"id": "user-proof"}], "limit": kwargs["limit"], "offset": kwargs["offset"], "has_more": False}


class Catalog:
    def __init__(self):
        self.calls = []

    async def publish_plan(self, principal, definition, reason, key):
        self.calls.append((principal.id, definition, reason, key))
        return {"id": definition["id"], "published": True}


def app(tmp_path, *, auth=None):
    from platform_service.console_app import create_console_app
    (tmp_path / "assets").mkdir(exist_ok=True)
    (tmp_path / "index.html").write_text('<!doctype html><html><title>中央运营后台</title><script type="module" src="/assets/index-proof.js"></script></html>')
    (tmp_path / "assets" / "index-proof.js").write_text("document.title='Console';")
    catalog = Catalog()
    return create_console_app(settings(), database=object(), auth=auth or Auth(), queries=Queries(), catalog=catalog, static_dir=tmp_path), catalog


@pytest.mark.asyncio
async def test_console_does_not_accept_product_bearer_or_export_session_hash(tmp_path):
    server, _ = app(tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url=ORIGIN) as client:
        response = await client.get("/api/session", headers={"Authorization": "Bearer ordinary-product-proof"})
        assert response.status_code == 401
        client.cookies.set("easel_console_session", "session-proof")
        response = await client.get("/api/session")
        assert response.json() == {"admin": {"id": "admin-proof", "role": "admin", "permissions": ["billing:publish", "console:read", "execution:publish", "plans:publish"]}, "csrf_token": "csrf-proof"}
        assert "never-export" not in response.text
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
@pytest.mark.parametrize("headers,status", [({}, 403), ({"Origin": ORIGIN}, 403),
    ({"Origin": "http://evil.invalid", "X-Console-CSRF": "csrf-proof"}, 403),
    ({"Origin": ORIGIN, "X-Console-CSRF": "wrong"}, 403),
    ({"Origin": ORIGIN, "X-Console-CSRF": "csrf-proof", "Idempotency-Key": "key-proof-123"}, 200)])
async def test_catalog_requires_origin_csrf_and_idempotency(tmp_path, headers, status):
    server, catalog = app(tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url=ORIGIN, cookies={"easel_console_session": "session-proof"}) as client:
        response = await client.post("/api/plans", headers=headers, json={"definition": {"id": "plan-proof"}, "reason": "审批通过的版本"})
        assert response.status_code == status
        assert bool(catalog.calls) == (status == 200)


@pytest.mark.asyncio
async def test_read_only_role_cannot_publish(tmp_path):
    server, catalog = app(tmp_path, auth=Auth(read_only=True))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url=ORIGIN, cookies={"easel_console_session": "session-proof"}) as client:
        response = await client.post("/api/plans", headers={"Origin": ORIGIN, "X-Console-CSRF": "csrf-proof", "Idempotency-Key": "key-proof-123"}, json={"definition": {}, "reason": "viewer attempt"})
        assert response.status_code == 403 and not catalog.calls


@pytest.mark.asyncio
async def test_static_routes_are_bounded_and_never_swallow_api(tmp_path):
    server, _ = app(tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url=ORIGIN) as client:
        response = await client.get("/users")
        assert response.status_code == 200 and "中央运营后台" in response.text
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert response.headers["x-content-type-options"] == "nosniff"
        response = await client.get("/assets/index-proof.js")
        assert response.status_code == 200 and "immutable" in response.headers["cache-control"]
        for path in ("/api/not-a-route", "/assets/missing.js", "/assets/%2e%2e%2findex.html", "/unknown-page"):
            response = await client.get(path)
            assert response.status_code in (401, 404)
            assert '<html' not in response.text


@pytest.mark.asyncio
async def test_pending_registration_is_not_admin_and_callback_secrets_are_not_reflected(tmp_path):
    server, _ = app(tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url=ORIGIN) as client:
        response = await client.get("/auth/login", follow_redirects=False)
        assert response.status_code == 302
        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=lax" in cookie
        response = await client.get("/oidc/console/callback?code=code-proof&state=state-proof", headers={"Accept": "text/html"})
        assert response.status_code == 403 and "pending-proof-1234567890" in response.text
        assert "code-proof" not in response.text and "state-proof" not in response.text
        assert 'easel_console_session=""' in response.headers.get("set-cookie", "")
        assert "Max-Age=0" in response.headers.get("set-cookie", "")
        response = await client.get("/api/session")
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_logout_needs_csrf_and_invalidates_cookie(tmp_path):
    auth = Auth(); server, _ = app(tmp_path, auth=auth)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url=ORIGIN, cookies={"easel_console_session": "session-proof"}) as client:
        response = await client.post("/auth/logout", headers={"Origin": ORIGIN, "X-Console-CSRF": "csrf-proof"})
        assert response.status_code == 200 and auth.logged_out
        assert "Max-Age=0" in response.headers["set-cookie"]


@pytest.mark.asyncio
async def test_catalog_rejects_actor_injection_and_oversized_json(tmp_path):
    server, catalog = app(tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url=ORIGIN, cookies={"easel_console_session": "session-proof"}) as client:
        headers = {"Origin": ORIGIN, "X-Console-CSRF": "csrf-proof", "Idempotency-Key": "key-proof-123"}
        response = await client.post("/api/plans", headers=headers, json={"definition": {}, "reason": "test", "operator": "injected"})
        assert response.status_code == 400 and not catalog.calls
        response = await client.post("/api/plans", headers=headers, json={"definition": {"x": "a" * 65536}, "reason": "test"})
        assert response.status_code == 413 and not catalog.calls
