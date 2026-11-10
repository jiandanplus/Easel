"""Identity integration against a real owned PostgreSQL schema and signed OIDC fixture."""

import asyncio
import base64
import hashlib
import json
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric import rsa

from platform_service.errors import PlatformError
from platform_service.identity import IdentityService


def challenge(value):
    return (
        base64.urlsafe_b64encode(hashlib.sha256(value.encode()).digest())
        .rstrip(b"=")
        .decode()
    )


class Provider:
    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.issuer = "https://identity.example/oidc"
        self.params = {}
        self.overrides = {}
        self.algorithm = "RS256"

    def handler(self, request):
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": self.issuer,
                    "authorization_endpoint": self.issuer + "/auth",
                    "token_endpoint": self.issuer + "/token",
                    "jwks_uri": self.issuer + "/jwks",
                },
            )
        if request.url.path.endswith("jwks"):
            jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
            jwk.update(kid="fixture", alg="RS256", use="sig")
            return httpx.Response(200, json={"keys": [jwk]})
        data = parse_qs(request.content.decode())
        assert data["redirect_uri"] == [
            "https://central.example/oidc/" + self.params["product"] + "/callback"
        ]
        assert challenge(data["code_verifier"][0]) == self.params["code_challenge"][0]
        claims = dict(
            iss=self.issuer,
            sub="subject-1",
            aud="oidc-client",
            exp=int(time.time()) + 120,
            iat=int(time.time()),
            nonce=self.params["nonce"][0],
            email="same@example.test",
            email_verified=True,
        )
        claims.update(self.overrides)
        key = (
            self.key
            if self.algorithm == "RS256"
            else "fixture-signing-secret-which-is-long"
        )
        return httpx.Response(
            200,
            json={
                "id_token": jwt.encode(
                    claims, key, algorithm=self.algorithm, headers={"kid": "fixture"}
                )
            },
        )


@pytest_asyncio.fixture
async def identity(db):
    async with db.connection() as conn:
        await conn.execute("INSERT INTO products(id) VALUES ('easel'), ('second')")
        await conn.execute(
            "INSERT INTO product_clients(product_id,client_id) VALUES ('easel','desktop'),('second','desktop')"
        )
    provider = Provider()
    settings = SimpleNamespace(
        public_url="https://central.example",
        issuer=provider.issuer,
        oidc_clients={
            p: SimpleNamespace(
                client_id="oidc-client", client_secret="fixture-client-secret"
            )
            for p in ("easel", "second")
        },
        allow_loopback_http=False,
        auth_ttl=300,
        handoff_ttl=60,
        access_ttl=900,
        refresh_ttl=2592000,
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(provider.handler)
    ) as client:
        yield IdentityService(db, settings, http_client=client), provider


async def handoff(identity, product="easel", **claims):
    service, provider = identity
    verifier = "fixture-verifier-" + "A" * 48
    tx = await service.begin_login(
        product,
        "desktop",
        "http://127.0.0.1:37689/platform-auth/callback",
        challenge(verifier),
        "local-state",
    )
    auth = await service.authorize(tx["transaction_id"])
    provider.params = parse_qs(urlsplit(auth["url"]).query)
    provider.params["product"] = product
    provider.overrides = claims
    callback = await service.callback(
        product, "provider-code", provider.params["state"][0], auth["cookie"]
    )
    query = parse_qs(urlsplit(callback["redirect_uri"]).query)
    assert query["state"] == ["local-state"]
    return dict(
        code=query["code"][0],
        state=query["state"][0],
        code_verifier=verifier,
        product_id=product,
        client_id="desktop",
        callback_uri="http://127.0.0.1:37689/platform-auth/callback",
    )


async def login(identity, product="easel", **claims):
    return await identity[0].redeem(await handoff(identity, product, **claims))


async def test_login_product_scope_and_same_identity_shared_wallet(identity, db):
    service, _ = identity
    one = await login(identity)
    two = await login(identity, "second")
    assert one["scope"]["user_id"] == two["scope"]["user_id"]
    assert one["scope"]["billing_account_id"] == two["scope"]["billing_account_id"]
    assert one["access_token"] != two["access_token"]
    scope = await service.authenticate(one["access_token"], "easel")
    assert (await service.me(scope))["account"]["email_verified"] is True
    with pytest.raises(PlatformError):
        await service.authenticate(one["access_token"], "second")
    async with db.connection() as conn:
        dumped = str(
            await (await conn.execute("SELECT * FROM identity_tokens")).fetchall()
        )
    assert one["access_token"] not in dumped and one["refresh_token"] not in dumped


async def test_login_keeps_display_username_and_drops_malformed_ones(identity, db):
    async def stored():
        async with db.connection() as conn:
            return (await (await conn.execute(
                "SELECT username FROM external_identities WHERE subject='subject-1'")).fetchone())["username"]
    session = await login(identity, username="alice")
    assert await stored() == "alice"
    scope = await identity[0].authenticate(session["access_token"], "easel")
    assert (await identity[0].me(scope))["account"]["username"] == "alice"  # Shown on the desktop profile page.
    await login(identity, username="alice-renamed")
    assert await stored() == "alice-renamed"
    for bad in ("", "   ", "x" * 129, "bad\nname", 42):
        await login(identity, username=bad)
        assert await stored() is None
    await login(identity)
    assert await stored() is None


async def test_email_never_merges_subjects(identity):
    first = await login(identity)
    second = await login(identity, sub="subject-2")
    assert first["account"]["id"] != second["account"]["id"]


async def test_refresh_rotation_replay_revokes_device(identity):
    service, _ = identity
    first = await login(identity)
    rotated = await service.refresh(first["refresh_token"])
    with pytest.raises(PlatformError):
        await service.authenticate(first["access_token"])
    assert await service.authenticate(rotated["access_token"])
    with pytest.raises(PlatformError):
        await service.refresh(first["refresh_token"])
    with pytest.raises(PlatformError):
        await service.authenticate(rotated["access_token"])


async def test_handoff_concurrent_exchange_is_single_use(identity):
    service, _ = identity
    payload = await handoff(identity)
    results = await asyncio.gather(
        service.redeem(payload), service.redeem(payload), return_exceptions=True
    )
    assert sum(isinstance(x, dict) for x in results) == 1
    assert sum(isinstance(x, PlatformError) for x in results) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("code_verifier", "wrong"),
        ("product_id", "second"),
        ("client_id", "other"),
        ("callback_uri", "http://127.0.0.1:37690/platform-auth/callback"),
        ("code", "wrong"),
        ("state", "wrong"),
    ],
)
async def test_wrong_handoff_proof(identity, field, value):
    payload = await handoff(identity)
    payload[field] = value
    with pytest.raises(PlatformError):
        await identity[0].redeem(payload)


@pytest.mark.parametrize(
    "uri",
    [
        "http://localhost:1234/platform-auth/callback",
        "https://evil.test/platform-auth/callback",
        "http://127.0.0.1:1234/other",
        "http://x@127.0.0.1:1234/platform-auth/callback",
        "http://127.0.0.1:1234/platform-auth/callback?q=1",
        "http://127.0.0.1:1234/platform-auth/callback#x",
    ],
)
async def test_callback_allowlist(identity, uri):
    with pytest.raises(PlatformError):
        await identity[0].begin_login(
            "easel", "desktop", uri, challenge("A" * 48), "local-state"
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE users SET active=false",
        "UPDATE users SET authorization_epoch=authorization_epoch+1",
        "UPDATE tenant_members SET active=false",
        "UPDATE products SET enabled=false",
        "UPDATE product_clients SET enabled=false",
    ],
)
async def test_revocation_is_immediate(identity, db, mutation):
    session = await login(identity)
    async with db.connection() as conn:
        await conn.execute(mutation)
    with pytest.raises(PlatformError):
        await identity[0].authenticate(session["access_token"])
    with pytest.raises(PlatformError):
        await identity[0].refresh(session["refresh_token"])


async def test_sessions_revoke_logout_and_account_isolation(identity):
    service, _ = identity
    first = await login(identity)
    second = await login(identity)
    other = await login(identity, sub="other-user")
    scope = await service.authenticate(first["access_token"])
    sessions = await service.sessions(scope)
    assert len(sessions) == 2 and "token" not in json.dumps(sessions)
    with pytest.raises(PlatformError):
        await service.revoke(scope, other["session_id"])
    await service.revoke(scope, second["session_id"])
    with pytest.raises(PlatformError):
        await service.authenticate(second["access_token"])
    await service.logout(first["access_token"])
    with pytest.raises(PlatformError):
        await service.authenticate(first["access_token"])


@pytest.mark.parametrize(
    "uri",
    [
        "http://127.0.0.1:1234/platform-auth/callback\n",
        "http://127.0.0.1:1234/platform-auth/\tcallback",
    ],
)
async def test_callback_control_characters_rejected(identity, uri):
    with pytest.raises(PlatformError):
        await identity[0].begin_login(
            "easel", "desktop", uri, challenge("A" * 48), "state"
        )


@pytest.mark.parametrize("stage", ["transaction", "handoff", "access", "refresh"])
async def test_expiration_is_enforced(identity, db, stage):
    service, _ = identity
    if stage == "transaction":
        tx = await service.begin_login(
            "easel",
            "desktop",
            "http://127.0.0.1:37689/platform-auth/callback",
            challenge("A" * 48),
            "state",
        )
        async with db.connection() as conn:
            await conn.execute(
                "UPDATE identity_transactions SET expires_at=now()-interval '1 second'"
            )
        with pytest.raises(PlatformError):
            await service.authorize(tx["transaction_id"])
    elif stage == "handoff":
        payload = await handoff(identity)
        async with db.connection() as conn:
            await conn.execute(
                "UPDATE identity_handoffs SET expires_at=now()-interval '1 second'"
            )
        with pytest.raises(PlatformError):
            await service.redeem(payload)
    else:
        session = await login(identity)
        async with db.connection() as conn:
            await conn.execute(
                "UPDATE identity_tokens SET expires_at=now()-interval '1 second' WHERE kind=%s",
                (stage,),
            )
        with pytest.raises(PlatformError):
            if stage == "access":
                await service.authenticate(session["access_token"])
            else:
                await service.refresh(session["refresh_token"])


async def test_transaction_database_has_only_proof_hashes(identity, db):
    service, provider = identity
    tx = await service.begin_login(
        "easel",
        "desktop",
        "http://127.0.0.1:37689/platform-auth/callback",
        challenge("A" * 48),
        "state",
    )
    auth = await service.authorize(tx["transaction_id"])
    params = parse_qs(urlsplit(auth["url"]).query)
    async with db.connection() as conn:
        row = await (
            await conn.execute(
                "SELECT * FROM identity_transactions WHERE id=%s",
                (tx["transaction_id"],),
            )
        ).fetchone()
    text = str(row)
    assert all(
        value not in text
        for value in (
            auth["cookie"],
            params["state"][0],
            params["nonce"][0],
            "fixture-client-secret",
        )
    )


async def test_concurrent_refresh_reuse_revokes_the_issued_pair(identity):
    service, _ = identity
    initial = await login(identity)
    results = await asyncio.gather(
        service.refresh(initial["refresh_token"]),
        service.refresh(initial["refresh_token"]),
        return_exceptions=True,
    )
    pairs = [result for result in results if isinstance(result, dict)]
    assert len(pairs) == 1
    assert sum(isinstance(result, PlatformError) for result in results) == 1
    with pytest.raises(PlatformError):
        await service.authenticate(pairs[0]["access_token"])
