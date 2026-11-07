import httpx
import pytest

from platform_service.app import create_app
from platform_service.config import Settings
from platform_service.errors import PlatformError
from test_identity import identity, challenge  # noqa: F401 - shared pytest fixture
from platform_service.config import OIDCClient


class NoIdentity:
    async def authenticate(self, token, product_id=None):
        raise PlatformError(401, "invalid_authentication")

    async def me(self, scope):
        raise AssertionError("unauthenticated request reached me")

    async def sessions(self, scope):
        raise AssertionError("unauthenticated request reached sessions")


@pytest.mark.asyncio
async def test_capabilities_are_product_scoped_and_do_not_advertise_execution_or_payment(db):
    async with db.connection() as conn:
        await conn.execute("INSERT INTO products(id) VALUES('easel')")
    app = create_app(Settings("postgresql://localhost/unused", "https://accounts.example"), database=db, identity=NoIdentity())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://accounts.example") as client:
        result = await client.get("/v2/capabilities", params={"product_id": "easel"})
        assert result.status_code == 200
        assert result.json()["ledger_mode"] == "central_credit"
        assert result.json()["payments_enabled"] is False
        assert result.json()["subscription_execution_enabled"] is False
        assert result.json()["identity_ready"] is False
        assert (await client.get("/v2/capabilities", params={"product_id": "other"})).status_code == 404


@pytest.mark.asyncio
async def test_private_routes_reject_unauthenticated_and_cross_origin_requests(db):
    app = create_app(Settings("postgresql://localhost/unused", "https://accounts.example"), database=db, identity=NoIdentity())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://accounts.example") as client:
        for path in ("/v2/me", "/v2/wallet", "/v2/usage", "/v2/sessions", "/v2/products/easel/entitlements"):
            assert (await client.get(path)).status_code == 401
        blocked = await client.post("/v2/auth/refresh", json={"refresh_token": "private-fixture"}, headers={"Origin": "https://hostile.example"})
        assert blocked.status_code == 403
        assert "private-fixture" not in blocked.text
        assert (await client.post("/v2/wallet/issue", json={"amount": "999"})).status_code == 404


@pytest.mark.asyncio
async def test_auth_payloads_are_bounded_and_provider_unavailable_is_explicit(db):
    app = create_app(Settings("postgresql://localhost/unused", "https://accounts.example"), database=db, identity=NoIdentity())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://accounts.example") as client:
        result = await client.post("/v2/auth/transactions", json={"product_id": "easel"})
        assert result.status_code == 503
        assert result.json()["error"]["code"] == "identity_not_configured"
        huge = await client.post("/v2/auth/refresh", content=b"x" * 70000)
        assert huge.status_code == 413


@pytest.mark.asyncio
async def test_bearer_validation_does_not_echo_invalid_token(db):
    app = create_app(Settings("postgresql://localhost/unused", "https://accounts.example"), database=db, identity=NoIdentity())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://accounts.example") as client:
        response = await client.get("/v2/wallet", headers={"Authorization": "Bearer private-fixture-token"})
        assert response.status_code == 401
        assert "private-fixture-token" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("status,retryable", [(502, True), (503, False)])
async def test_api_preserves_explicit_retryability(db, status, retryable):
    class FailingIdentity(NoIdentity):
        async def authenticate(self, token, product_id=None):
            raise PlatformError(status, "fixture_provider_failure", retryable=retryable)
    app = create_app(Settings("postgresql://localhost/unused", "https://accounts.example"), database=db, identity=FailingIdentity())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://accounts.example") as client:
        response = await client.get("/v2/me", headers={"Authorization": "Bearer private-fixture-token"})
        assert response.json()["error"]["retryable"] is retryable


@pytest.mark.asyncio
async def test_wrong_product_authorize_does_not_destroy_correct_transaction(db, identity):  # noqa: F811
    service, provider = identity
    settings = Settings("postgresql://localhost/unused", "https://central.example", issuer=provider.issuer,
                        oidc_clients={key: OIDCClient("oidc-client", "fixture-secret") for key in ("easel", "second")})
    flow = await service.begin_login("easel", "desktop", "http://127.0.0.1:18888/platform-auth/callback", challenge("x" * 48), "local-" + "x" * 32)
    app = create_app(settings, database=db, identity=service)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=settings.public_url) as client:
        assert (await client.get("/oidc/second/authorize", params={"transaction_id": flow["transaction_id"]})).status_code == 400
        result = await client.get("/oidc/easel/authorize", params={"transaction_id": flow["transaction_id"]})
        assert result.status_code == 302
