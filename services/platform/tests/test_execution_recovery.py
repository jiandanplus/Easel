"""Authoritative tuple lookup is a current-owner, read-only history read."""
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest

from platform_service.errors import PlatformError
from test_execution_core import setup, admit


async def snapshot(db):
    async with db.connection() as conn:
        return {table: await (await conn.execute("SELECT to_jsonb(t) AS value FROM " + table + " t ORDER BY to_jsonb(t)::text")).fetchall()
                for table in ("execution_jobs", "execution_invocations", "execution_task_tokens", "execution_receipts", "execution_results", "credit_reservations", "credit_journals", "credit_entries", "credit_lots")}


async def finish(service, job):
    await service.dispatch(job["id"], job["attempt_id"])
    await service.finish(job["id"], job["attempt_id"], input_tokens=3, output_tokens=2,
                         terminal_status="completed", provider_response_id="fixture",
                         evidence_hash="fixture", payload=b"PRIVATE-CACHED-RESULT", content_type="application/json")


@pytest.mark.asyncio
async def test_tuple_lookup_each_financial_and_cache_state_is_read_only(db):
    service, ledger, scope, ctx = await setup(db)
    await admit(service, ctx, "reserved")
    dispatched = await admit(service, ctx, "dispatched")
    await service.dispatch(dispatched["id"], dispatched["attempt_id"])
    pending = await admit(service, ctx, "pending")
    await service.dispatch(pending["id"], pending["attempt_id"])
    await service.pending(pending["id"], pending["attempt_id"], "network_error")
    released = await admit(service, ctx, "released")
    await service.cancel(scope, released["id"])
    settled = await admit(service, ctx, "settled")
    await finish(service, settled)
    # An owned isolated test schema supplies expired metadata on INSERT, respecting
    # immutable history triggers. Status lookup must never expire bytes itself.
    async with db.connection() as conn:
        await conn.execute("ALTER TABLE execution_results ALTER COLUMN expires_at SET DEFAULT (clock_timestamp()-interval '1 second')")
    expired = await admit(service, ctx, "expired")
    await finish(service, expired)
    before = await snapshot(db)
    for key, status, result_state in (("reserved", "reserved", "not_available"),
                                    ("dispatched", "dispatched", "not_available"),
                                    ("pending", "pending_reconciliation", "not_available"),
                                    ("released", "released", "not_available"),
                                    ("settled", "settled", "available"),
                                    ("expired", "settled", "expired")):
        row = await service.lookup_operation(scope, ctx["invocation_id"], key)
        assert row["status"] == status and row["result_state"] == result_state
        assert row["actual"] == ("700" if status == "settled" else "0" if status == "released" else None)
        assert not {"payload", "content_type", "scope", "body_hash", "binding_hash", "attempt_id", "reservation_id", "invocation_id", "operation_key"} & row.keys()
    with pytest.raises(PlatformError) as missing:
        await service.lookup_operation(scope, ctx["invocation_id"], "not-yet-found")
    assert (missing.value.status, missing.value.code) == (404, "execution_not_found")
    assert await snapshot(db) == before
    assert await service.expire_results() == 1
    before = await snapshot(db)
    assert (await service.lookup_operation(scope, ctx["invocation_id"], "expired"))["result_state"] == "expired"
    assert (await service.lookup_operation(scope, ctx["invocation_id"], "settled"))["result_state"] == "available"
    assert await snapshot(db) == before


async def new_session(db, scope, name, **changes):
    current = replace(scope, session_id=name, **changes)
    async with db.connection() as conn:
        await conn.execute("INSERT INTO product_clients(product_id,client_id) VALUES (%s,'desktop') ON CONFLICT DO NOTHING", (current.product_id,))
        await conn.execute("INSERT INTO external_identities(issuer,subject,user_id) VALUES ('fixture',%s,%s) ON CONFLICT DO NOTHING", (current.user_id, current.user_id))
        await conn.execute("INSERT INTO identity_sessions(id,user_id,tenant_id,billing_account_id,product_id,client_id,authorization_epoch,issuer,subject,expires_at) VALUES (%s,%s,%s,%s,%s,'desktop',%s,'fixture',%s,now()+interval '1 hour')", (name, current.user_id, current.tenant_id, current.billing_account_id, current.product_id, current.authorization_epoch, current.user_id))
    return current


@pytest.mark.asyncio
async def test_current_authorization_all_business_dimensions_and_fresh_login(db):
    service, ledger, scope, ctx = await setup(db)
    job = await admit(service, ctx, "key/with?query")
    await finish(service, job)
    async with db.connection() as conn:
        await conn.execute("INSERT INTO tenant_members(tenant_id,user_id,role) VALUES ('t','other','member'),('foreign','u','member')")
        await conn.execute("INSERT INTO billing_accounts(id,tenant_id) VALUES ('same-tenant-wallet','t')")
    foreign_scopes = [
        await new_session(db, scope, "other-user", user_id="other"),
        await new_session(db, scope, "other-product", product_id="second"),
        await new_session(db, scope, "other-wallet", billing_account_id="same-tenant-wallet"),
        await new_session(db, scope, "other-tenant", tenant_id="foreign", billing_account_id="foreign-wallet"),
    ]
    before = await snapshot(db)
    for foreign in foreign_scopes:
        with pytest.raises(PlatformError) as hidden:
            await service.lookup_operation(foreign, ctx["invocation_id"], "key/with?query")
        assert (hidden.value.status, hidden.value.code) == (404, "execution_not_found")
    assert await snapshot(db) == before
    async with db.connection() as conn:
        await conn.execute("UPDATE identity_sessions SET revoked_at=now() WHERE id='session'")
        await conn.execute("UPDATE users SET authorization_epoch=1 WHERE id='u'")
    fresh = await new_session(db, scope, "fresh-login", authorization_epoch=1)
    before = await snapshot(db)
    assert (await service.lookup_operation(fresh, ctx["invocation_id"], "key/with?query"))["status"] == "settled"
    with pytest.raises(PlatformError):
        await service.lookup_operation(scope, ctx["invocation_id"], "key/with?query")
    with pytest.raises(PlatformError):
        await service.result(ctx, "key/with?query")
    assert await snapshot(db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("invocation,key", [("../bad", "key"), (str(uuid4()), ""), (str(uuid4()), "a"*129), (str(uuid4()), "a\n"), (str(uuid4()), "中文")])
async def test_tuple_shape_validation(db, invocation, key):
    service, ledger, scope, ctx = await setup(db)
    with pytest.raises(PlatformError) as invalid:
        await service.lookup_operation(scope, invocation, key)
    assert invalid.value.status == 422


@pytest.mark.asyncio
async def test_operation_http_route_product_bearer_and_no_execution_actions(db):
    from platform_service.app import create_app
    from platform_service.config import Settings
    from test_execution_api import Identity, Runtime
    runtime = Runtime()
    calls = []
    async def lookup(scope, invocation_id, operation_key):
        calls.append((invocation_id, operation_key))
        return {"id": "job", "status": "settled", "result_state": "expired"}
    runtime.lookup_operation = lookup
    async with db.connection() as conn:
        await conn.execute("INSERT INTO products(id) VALUES('easel')")
    app = create_app(Settings("postgresql://localhost/unused", "https://accounts.example"), database=db, identity=Identity(), execution_runtime=runtime)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://accounts.example") as client:
        params = {"invocation_id": str(uuid4()), "operation_key": "key/with?query"}
        response = await client.get("/v2/execution-operations", params=params, headers={"Authorization": "Bearer product-private-token"})
        assert response.status_code == 200 and response.json()["result_state"] == "expired"
        assert calls == [(params["invocation_id"], params["operation_key"])]
        assert (await client.get("/v2/execution-operations", params=params, headers={"Authorization": "Bearer task-private-token"})).status_code == 401
        assert runtime.received == []


@pytest.mark.asyncio
async def test_historical_tenant_predicate_is_independent_of_other_owner_fields(db, monkeypatch):
    """Exercise the tenant SQL predicate separately from current authorization.

    Schema foreign keys couple current tenant/wallet. Bypass those already-tested
    guards here only, retaining real PostgreSQL history and all other owner fields,
    so removing the historical tenant predicate causes this test to fail.
    """
    import platform_service.execution.core as core
    service, ledger, scope, ctx = await setup(db)
    await admit(service, ctx)
    async def validated(conn, requested):
        assert requested.tenant_id == "foreign"
    async def authorized(conn, requested):
        assert requested.billing_account_id == scope.billing_account_id
    monkeypatch.setattr(service.identity, "validate_scope", validated)
    monkeypatch.setattr(core, "authorize", authorized)
    before = await snapshot(db)
    with pytest.raises(PlatformError) as hidden:
        await service.lookup_operation(replace(scope, tenant_id="foreign"), ctx["invocation_id"], "op")
    assert (hidden.value.status, hidden.value.code) == (404, "execution_not_found")
    assert await snapshot(db) == before
