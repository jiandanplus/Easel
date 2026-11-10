"""Signed OIDC claim transitions through the real central ASGI API, not email delivery."""

from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from platform_service.app import create_app
from platform_service.config import OIDCClient, Settings
from platform_service.contracts import OperatorIdentity, Scope
from platform_service.credits import CreditLedger
from platform_service.execution_config import ExecutionSettings
from platform_service.execution_runtime import ExecutionRuntime
from platform_service.internal_grants import InternalGrantService
from platform_service.media_config import MediaSettings
from platform_service.media_runtime import MediaRuntime
from platform_service.membership import MembershipService
from platform_service.payment_config import PaymentSettings
from platform_service.payment_runtime import PaymentRuntime
from test_identity import challenge, identity  # noqa: F401 - shared owned-schema fixture
from test_internal_grants import load_policy, policy_document, settings_for


FINANCIAL_TABLES = (
    "plans",
    "memberships",
    "membership_revocations",
    "internal_grants",
    "credit_lots",
    "credit_reservations",
    "credit_holds",
    "credit_journals",
    "credit_entries",
)
OWNER_TABLES = ("users", "tenants", "tenant_members", "billing_accounts")


@pytest_asyncio.fixture
async def email_api(db, identity):  # noqa: F811
    service, provider = identity
    settings = Settings(
        "postgresql://unused@127.0.0.1/unused",
        "https://central.example",
        issuer=provider.issuer,
        oidc_clients={
            product: OIDCClient("oidc-client", "fixture-client-secret")
            for product in ("easel", "second")
        },
    )

    def reject_external_request(request):
        raise AssertionError("email continuity must not contact a runtime provider")

    # Explicit disabled runtime objects avoid loading any operator configuration
    # from the test process environment. Their HTTP transports also fail closed.
    blocked = httpx.MockTransport(reject_external_request)
    payments = PaymentRuntime(db, PaymentSettings(), providers={})
    executions = ExecutionRuntime(db, service, ExecutionSettings(), transport=blocked)
    media = MediaRuntime(db, service, MediaSettings(), transport=blocked)
    app = create_app(
        settings,
        database=db,
        identity=service,
        payment_runtime=payments,
        execution_runtime=executions,
        media_runtime=media,
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=settings.public_url,
            trust_env=False,
        ) as client:
            yield client, provider
    finally:
        await media.close()
        await executions.close()


async def api_login(client, provider, *, callback_status=302, **claims):
    """Use every public broker/handoff route; only the signed IdP is synthetic."""
    verifier = "email-fixture-" + uuid4().hex + "A" * 32
    local_state = "email-state-" + uuid4().hex
    callback_uri = "http://127.0.0.1:37689/platform-auth/callback"
    started = await client.post(
        "/v2/auth/transactions",
        json={
            "product_id": "easel",
            "client_id": "desktop",
            "callback_uri": callback_uri,
            "handoff_challenge": challenge(verifier),
            "local_state": local_state,
        },
    )
    assert started.status_code == 200
    authorize_url = urlsplit(started.json()["authorize_url"])
    authorized = await client.get(authorize_url.path + "?" + authorize_url.query)
    assert authorized.status_code == 302
    provider.params = parse_qs(urlsplit(authorized.headers["location"]).query)
    provider.params["product"] = "easel"
    provider.overrides = claims
    callback = await client.get(
        "/oidc/easel/callback",
        params={"code": "signed-fixture-code", "state": provider.params["state"][0]},
    )
    assert callback.status_code == callback_status
    if callback_status != 302:
        return callback.json()
    handoff = urlsplit(callback.headers["location"])
    assert handoff.scheme + "://" + handoff.netloc + handoff.path == callback_uri
    proof = parse_qs(handoff.query)
    assert proof["state"] == [local_state]
    redeemed = await client.post(
        "/v2/auth/redeem",
        json={
            "code": proof["code"][0],
            "state": local_state,
            "code_verifier": verifier,
            "product_id": "easel",
            "client_id": "desktop",
            "callback_uri": callback_uri,
        },
    )
    assert redeemed.status_code == 200
    return redeemed.json()


async def rows_snapshot(db, tables):
    """Compare every stored field, including immutable audit provenance."""
    result = {}
    async with db.connection() as conn:
        for table in tables:
            query = sql.SQL(
                "SELECT to_jsonb(t) AS row FROM {} AS t ORDER BY to_jsonb(t)::text"
            ).format(sql.Identifier(table))
            result[table] = [row["row"] for row in await (await conn.execute(query)).fetchall()]
    return result


async def seed_existing_funds(db, test_dsn, tmp_path, session, issuer):
    """Nonempty test membership, internal grant, settled usage and unknown hold."""
    memberships = MembershipService(db)
    operator = OperatorIdentity(
        "email-continuity-operator", frozenset({"plans:publish", "test:issue"})
    )
    plan = {
        "id": "email-continuity-existing-v1",
        "name": "Email continuity existing fixture",
        "version": "1",
        "included_products": ["easel"],
        "features": ["text"],
        "credits": "1000",
        "days": 30,
        "concurrency": 3,
        "test_only": True,
    }
    await memberships.publish_plan(operator, plan)
    scope = Scope(**session["scope"])
    await memberships.issue_test(
        operator, scope.billing_account_id, plan["id"], "existing-test-grant"
    )

    # Reuse the existing strict policy helper, with a newly written synthetic
    # policy bound to this exact owned test database/schema and proven session.
    parsed = conninfo_to_dict(test_dsn)
    document = {
        **policy_document(db.schema),
        "database_host": parsed["host"],
        "database_port": int(parsed.get("port", 5432)),
        "database_name": parsed["dbname"],
        "issuer": issuer,
    }
    policy = load_policy(tmp_path, document)
    internal = InternalGrantService(db, settings_for(document), policy)
    await internal.issue(
        OperatorIdentity("email-internal-operator", frozenset({"internal:issue"})),
        environment=document["environment_id"],
        wallet=scope.billing_account_id,
        session=scope.session_id,
        key="existing-internal-grant",
        reason="Synthetic preexisting email continuity allowance",
    )

    ledger = CreditLedger(db)
    for key, amount, actual in (("existing-settled", 60, 17), ("existing-pending", 23, None)):
        reserved = await ledger.reserve(
            scope,
            operation="email-continuity-fixture",
            key=key,
            body_hash="email-continuity-fixture-hash",
            amount=amount,
            rate_version="fixture-v1",
            funding_mode="test",
        )
        await ledger.dispatch(scope, reserved["id"])
        if actual is None:
            await ledger.pending(scope, reserved["id"])
        else:
            await ledger.settle(scope, reserved["id"], actual=actual, key="existing-settlement")


async def api_account_state(client, session):
    headers = {"Authorization": "Bearer " + session["access_token"]}
    result = {}
    for name, path in (
        ("me", "/v2/me"),
        ("wallet", "/v2/wallet"),
        ("entitlements", "/v2/products/easel/entitlements"),
        ("usage", "/v2/usage"),
    ):
        response = await client.get(path, headers=headers)
        assert response.status_code == 200
        result[name] = response.json()
    return result


def assert_nonempty_existing_facts(state, financial):
    assert state["wallet"]["issued"] == "10001000"
    assert state["wallet"]["spent"] == "17"
    assert state["wallet"]["reserved"] == "23"
    assert state["wallet"]["pending_reconciliation"] == 1
    assert state["wallet"]["experience_issued"] == "10000000"
    assert len(state["entitlements"]["memberships"]) == 2
    assert {row["status"] for row in state["usage"]["usage"]} == {
        "settled", "pending_reconciliation"
    }
    assert len(financial["memberships"]) == 2
    assert len(financial["internal_grants"]) == 1
    for table in ("plans", "credit_lots", "credit_reservations", "credit_holds", "credit_journals", "credit_entries"):
        assert financial[table], table


@pytest.mark.asyncio
@pytest.mark.parametrize("verified_email", ["same@example.test", "verified@example.test"])
async def test_verified_email_reauthentication_keeps_existing_identity_and_every_financial_row(
    db, test_dsn, tmp_path, email_api, verified_email
):
    client, provider = email_api
    original = await api_login(client, provider, email_verified=False)
    assert original["account"]["email_verified"] is False
    await seed_existing_funds(db, test_dsn, tmp_path, original, provider.issuer)
    before = await api_account_state(client, original)
    financial = await rows_snapshot(db, FINANCIAL_TABLES)
    owners = await rows_snapshot(db, OWNER_TABLES)
    sessions = await rows_snapshot(db, ("identity_sessions",))
    assert_nonempty_existing_facts(before, financial)

    verified = await api_login(client, provider, email=verified_email, email_verified=True)
    after = await api_account_state(client, verified)
    for field in ("user_id", "tenant_id", "billing_account_id", "authorization_epoch", "product_id"):
        assert verified["scope"][field] == original["scope"][field]
    assert verified["account"]["id"] == original["account"]["id"]
    assert after["me"]["account"] == {
        **before["me"]["account"], "email": verified_email, "email_verified": True
    }
    assert verified["session_id"] != original["session_id"]
    assert verified["access_token"] != original["access_token"]
    for name in ("wallet", "entitlements", "usage"):
        assert after[name] == before[name], name
    assert await rows_snapshot(db, FINANCIAL_TABLES) == financial
    assert await rows_snapshot(db, OWNER_TABLES) == owners
    current_sessions = (await rows_snapshot(db, ("identity_sessions",)))["identity_sessions"]
    assert len(current_sessions) == len(sessions["identity_sessions"]) + 1
    assert all(row in current_sessions for row in sessions["identity_sessions"])


@pytest.mark.asyncio
async def test_same_email_different_subject_gets_empty_wallet_without_adopting_existing_funds(
    db, test_dsn, tmp_path, email_api
):
    client, provider = email_api
    original = await api_login(client, provider, email_verified=False)
    await seed_existing_funds(db, test_dsn, tmp_path, original, provider.issuer)
    before = await api_account_state(client, original)
    financial = await rows_snapshot(db, FINANCIAL_TABLES)
    assert_nonempty_existing_facts(before, financial)

    unrelated = await api_login(client, provider, sub="different-subject", email_verified=True)
    other = await api_account_state(client, unrelated)
    for field in ("user_id", "tenant_id", "billing_account_id"):
        assert unrelated["scope"][field] != original["scope"][field]
    assert other["me"]["account"]["email"] == before["me"]["account"]["email"]
    assert other["wallet"]["issued"] == other["wallet"]["available"] == "0"
    assert other["wallet"]["experience_issued"] == "0"
    assert other["entitlements"]["memberships"] == []
    assert other["usage"]["usage"] == []
    assert await api_account_state(client, original) == before
    assert await rows_snapshot(db, FINANCIAL_TABLES) == financial


@pytest.mark.asyncio
async def test_signed_same_subject_from_untrusted_issuer_cannot_adopt_or_mutate_existing_wallet(
    db, test_dsn, tmp_path, email_api
):
    client, provider = email_api
    original = await api_login(client, provider, email_verified=False)
    await seed_existing_funds(db, test_dsn, tmp_path, original, provider.issuer)
    before = await api_account_state(client, original)
    financial = await rows_snapshot(db, FINANCIAL_TABLES)
    owners = await rows_snapshot(db, OWNER_TABLES)
    sessions = await rows_snapshot(db, ("identity_sessions",))
    assert_nonempty_existing_facts(before, financial)

    rejected = await api_login(
        client,
        provider,
        callback_status=401,
        iss="https://untrusted-email-identity.example/oidc",
        email_verified=True,
    )
    assert rejected["error"]["code"] == "identity_token_invalid"
    assert await api_account_state(client, original) == before
    assert await rows_snapshot(db, FINANCIAL_TABLES) == financial
    assert await rows_snapshot(db, OWNER_TABLES) == owners
    assert await rows_snapshot(db, ("identity_sessions",)) == sessions
