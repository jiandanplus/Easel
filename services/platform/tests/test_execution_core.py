import asyncio
from dataclasses import replace
import pytest
from test_credits import wallet
from platform_service.errors import PlatformError
from platform_service.contracts import OperatorIdentity

OPERATOR = OperatorIdentity("fixture-operator", frozenset({"execution:publish"}))


def definition():
    return dict(
        id="fixture-text",
        name="Fixture text",
        version="1",
        capability="text",
        feature="text",
        products=["easel"],
        test_only=True,
        provider_id="fixture",
        provider_version="1",
        verification="fixture",
        verification_ref="local-fixture",
        rate=dict(
            version="1",
            input=dict(numerator="100", denominator="1"),
            output=dict(numerator="200", denominator="1"),
        ),
        limits=dict(
            max_input_bytes=1048576, max_input_tokens=65536, max_output_tokens=4096
        ),
    )


BINDINGS = {
    "fixture-text": dict(
        provider_id="fixture", provider_version="1", binding_hash="fixture-binding"
    )
}


async def setup(db):
    from platform_service.execution.core import ExecutionService
    from platform_service.identity import IdentityService

    ledger, scope = await wallet(db, "300000000")
    async with db.connection() as c:
        await c.execute(
            "INSERT INTO product_clients(product_id,client_id) VALUES ('easel','desktop')"
        )
        await c.execute(
            "INSERT INTO external_identities(issuer,subject,user_id) VALUES ('fixture','subject','u')"
        )
        await c.execute(
            "INSERT INTO identity_sessions(id,user_id,tenant_id,billing_account_id,product_id,client_id,authorization_epoch,issuer,subject,expires_at) VALUES ('session','u','t','wallet','easel','desktop',0,'fixture','subject',now()+interval '1 hour')"
        )
    service = ExecutionService(db, IdentityService(db, None))
    await service.publish(definition(), OPERATOR)
    await service.set_enabled("fixture-text", True, OPERATOR)
    minted = await service.mint(
        scope,
        model="fixture-text",
        invocation_key="run",
        mode="test",
        bindings=BINDINGS,
    )
    return service, ledger, scope, await service.authenticate_task(minted["task_token"])


async def admit(service, context, key="op"):
    return await service.admit(
        context, key=key, body_hash="hash", output_limit=100, bindings=BINDINGS
    )


def test_round_once():
    from platform_service.execution.rates import cost

    assert (
        cost(
            dict(
                input=dict(numerator="1", denominator="2"),
                output=dict(numerator="1", denominator="2"),
            ),
            1,
            1,
        )
        == 1
    )


@pytest.mark.asyncio
async def test_reserve_dispatch_settle_replay(db):
    service, ledger, scope, ctx = await setup(db)
    job = await admit(service, ctx)
    assert (await ledger.summary(scope))["reserved"] == "6573600"
    assert (await admit(service, ctx))["replay"] is True
    await service.dispatch(job["id"], job["attempt_id"])
    with pytest.raises(PlatformError):
        await service.dispatch(job["id"], job["attempt_id"])
    kwargs = dict(
        input_tokens=10,
        output_tokens=5,
        terminal_status="completed",
        provider_response_id="response",
        evidence_hash="receipt",
        payload=b"{}",
        content_type="application/json",
    )
    await service.finish(job["id"], job["attempt_id"], **kwargs)
    await service.finish(job["id"], job["attempt_id"], **kwargs)
    assert (await ledger.summary(scope))["spent"] == "2000"
    assert (await service.result(ctx, "op"))["payload"] == b"{}"
    with pytest.raises(PlatformError):
        await service.admit(
            ctx, key="op", body_hash="changed", output_limit=100, bindings=BINDINGS
        )


@pytest.mark.asyncio
async def test_unknown_keeps_hold_settlement_after_revoke(db):
    service, ledger, scope, ctx = await setup(db)
    job = await admit(service, ctx)
    await service.dispatch(job["id"], job["attempt_id"])
    await service.pending(job["id"], job["attempt_id"], "network_error")
    async with db.connection() as c:
        await c.execute("UPDATE identity_sessions SET revoked_at=now()")
    await service.finish(
        job["id"],
        job["attempt_id"],
        input_tokens=10,
        output_tokens=0,
        terminal_status="completed",
        provider_response_id="r",
        evidence_hash="e",
        payload=b"{}",
        content_type="application/json",
    )
    assert (await ledger.summary(scope))["spent"] == "1000"
    with pytest.raises(PlatformError):
        await admit(service, ctx, "new")


@pytest.mark.asyncio
async def test_recovery_releases_only_undispatched(db):
    service, ledger, scope, ctx = await setup(db)
    a, b = await admit(service, ctx, "a"), await admit(service, ctx, "b")
    await service.dispatch(b["id"], b["attempt_id"])
    async with db.connection() as c:
        await c.execute(
            "UPDATE execution_jobs SET lease_until=now()-interval '1 second'"
        )
    await service.recover()
    assert (await service.get(scope, a["id"]))["status"] == "released"
    assert (await service.get(scope, b["id"]))["status"] == "pending_reconciliation"
    assert (await ledger.summary(scope))["reserved"] == "6573600"


@pytest.mark.asyncio
async def test_overbudget_pending_and_binding_disabled(db):
    service, ledger, scope, ctx = await setup(db)
    job = await admit(service, ctx)
    await service.dispatch(job["id"], job["attempt_id"])
    with pytest.raises(PlatformError):
        await service.finish(
            job["id"],
            job["attempt_id"],
            input_tokens=999999,
            output_tokens=0,
            terminal_status="completed",
            provider_response_id="r",
            evidence_hash="e",
            payload=b"{}",
            content_type="application/json",
        )
    assert (await service.get(scope, job["id"]))["status"] == "pending_reconciliation"
    with pytest.raises(PlatformError):
        await admit(service, ctx, "next")
    assert (await ledger.summary(scope))["reserved"] == "6573600"


@pytest.mark.asyncio
async def test_scope_mode_catalog_and_revoked_dispatch(db):
    service, ledger, scope, ctx = await setup(db)
    assert not (await service.catalog(scope, mode="live", bindings=BINDINGS))["models"]
    with pytest.raises(PlatformError):
        await service.mint(
            scope,
            model="fixture-text",
            invocation_key="live",
            mode="live",
            bindings=BINDINGS,
        )
    job = await admit(service, ctx)
    with pytest.raises(PlatformError):
        await service.get(replace(scope, user_id="other"), job["id"])
    async with db.connection() as c:
        await c.execute("UPDATE identity_sessions SET revoked_at=now()")
    with pytest.raises(PlatformError):
        await service.dispatch(job["id"], job["attempt_id"])


@pytest.mark.asyncio
async def test_concurrent_admission_same_key_and_budget(db):
    service, ledger, scope, ctx = await setup(db)
    jobs = await asyncio.gather(*[admit(service, ctx) for _ in range(5)])
    assert len({j["id"] for j in jobs}) == 1
    await service.cancel(scope, jobs[0]["id"])
    for i in range(31):
        j = await admit(service, ctx, str(i))
        await service.cancel(scope, j["id"])
    with pytest.raises(PlatformError):
        await admit(service, ctx, "budget")


@pytest.mark.asyncio
async def test_database_rejects_execution_counter_and_history_tampering(db):
    import psycopg

    service, ledger, scope, ctx = await setup(db)
    job = await admit(service, ctx)
    for statement in (
        "UPDATE execution_invocations SET held=0",
        "UPDATE execution_invocations SET operation_count=0",
        "UPDATE execution_invocations SET scope='{}'",
        "UPDATE execution_models SET definition='{}'",
        "UPDATE execution_jobs SET model_definition='{}'",
        "DELETE FROM execution_invocations",
        "TRUNCATE execution_jobs CASCADE",
    ):
        with pytest.raises(psycopg.Error):
            async with db.connection() as c:
                await c.execute(statement)
    await service.dispatch(job["id"], job["attempt_id"])
    await service.finish(
        job["id"],
        job["attempt_id"],
        input_tokens=1,
        output_tokens=0,
        terminal_status="completed",
        provider_response_id="r",
        evidence_hash="e",
        payload=b"{}",
        content_type="application/json",
    )
    for statement in (
        "UPDATE execution_receipts SET input_tokens=2",
        "DELETE FROM execution_receipts",
        "UPDATE execution_results SET payload=NULL",
        "UPDATE execution_jobs SET status='dispatched'",
    ):
        with pytest.raises(psycopg.Error):
            async with db.connection() as c:
                await c.execute(statement)


@pytest.mark.asyncio
async def test_same_mode_entitlement_feature_and_immutable_replay(db):
    service, ledger, scope, ctx = await setup(db)
    job = await admit(service, ctx)
    await service.set_enabled("fixture-text", False, OPERATOR)
    assert (await admit(service, ctx))["id"] == job["id"]
    with pytest.raises(PlatformError):
        await admit(service, ctx, "another")
    from copy import deepcopy

    d = deepcopy(definition())
    d["id"] = "feature-missing"
    d["feature"] = "advanced"
    await service.publish(d, OPERATOR)
    await service.set_enabled(d["id"], True, OPERATOR)
    with pytest.raises(PlatformError):
        await service.mint(
            scope,
            model=d["id"],
            invocation_key="other",
            mode="test",
            bindings={d["id"]: BINDINGS["fixture-text"]},
        )
    changed = deepcopy(definition())
    changed["rate"]["input"]["numerator"] = "101"
    with pytest.raises(PlatformError):
        await service.publish(changed, OPERATOR)
    # A token row holds only its irreversible hash; product access credentials
    # have no row in the task-token namespace.
    with pytest.raises(PlatformError):
        await service.authenticate_task("product-access-token")


@pytest.mark.asyncio
async def test_invocation_cumulative_spend_budget_not_reset_by_renewal(db):
    service, ledger, scope, ctx = await setup(db)
    for i in range(15):
        job = await admit(service, ctx, str(i))
        await service.dispatch(job["id"], job["attempt_id"])
        await service.finish(
            job["id"],
            job["attempt_id"],
            input_tokens=65536,
            output_tokens=100,
            terminal_status="incomplete",
            provider_response_id=str(i),
            evidence_hash=str(i),
            payload=b"{}",
            content_type="application/json",
        )
    renewed = await service.mint(
        scope,
        model="fixture-text",
        invocation_key="run",
        mode="test",
        bindings=BINDINGS,
    )
    renewed_ctx = await service.authenticate_task(renewed["task_token"])
    assert renewed_ctx["invocation_id"] == ctx["invocation_id"]
    with pytest.raises(PlatformError):
        await admit(service, renewed_ctx, "over")
    assert (await ledger.summary(scope))["spent"] == str(15 * 6573600)


@pytest.mark.asyncio
async def test_revoke_serializes_with_dispatch_and_no_hold_is_lost(db):
    service, ledger, scope, ctx = await setup(db)
    job = await admit(service, ctx)
    async with db.connection() as c:
        await c.execute(
            "SELECT id FROM identity_sessions WHERE id='session' FOR UPDATE"
        )
        dispatch = asyncio.create_task(service.dispatch(job["id"], job["attempt_id"]))
        await asyncio.sleep(0.03)
        assert not dispatch.done()
        await c.execute("UPDATE identity_sessions SET revoked_at=now()")
    with pytest.raises(PlatformError):
        await dispatch
    assert (await ledger.summary(scope))["reserved"] == "6573600"
    async with db.connection() as c:
        await c.execute(
            "UPDATE execution_jobs SET lease_until=now()-interval '1 second'"
        )
    assert (await service.recover())["released"] == 1


@pytest.mark.asyncio
async def test_runtime_can_execute_but_cannot_publish_or_rewrite(db, test_dsn):
    import secrets
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from uuid import uuid4
    from platform_service.db import Database
    from platform_service.identity import IdentityService
    from platform_service.permissions import grant_runtime_permissions
    from platform_service.execution.core import ExecutionService

    service, ledger, scope, ctx = await setup(db)
    role = "s3_runtime_" + uuid4().hex
    password = secrets.token_urlsafe(24)
    async with db.connection() as c:
        await c.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password)
            )
        )
        await grant_runtime_permissions(c, role)
    runtime = Database(
        make_conninfo(test_dsn, user=role, password=password), schema=db.schema
    )
    try:
        await runtime.open()
        await runtime.require_runtime_role()
        service = ExecutionService(runtime, IdentityService(runtime, None))
        minted = await service.mint(
            scope,
            model="fixture-text",
            invocation_key="runtime",
            mode="test",
            bindings=BINDINGS,
        )
        ctx = await service.authenticate_task(minted["task_token"])
        job = await admit(service, ctx)
        await service.dispatch(job["id"], job["attempt_id"])
        await service.finish(
            job["id"],
            job["attempt_id"],
            input_tokens=10,
            output_tokens=2,
            terminal_status="completed",
            provider_response_id="r",
            evidence_hash="e",
            payload=b"{}",
            content_type="application/json",
        )
        assert (await ledger.summary(scope))["spent"] == "1400"
        for statement in (
            "UPDATE execution_models SET definition='{}'",
            "INSERT INTO execution_models(id,definition,operator_id) VALUES ('bad','{}','bad')",
            "UPDATE execution_model_states SET enabled=false",
            "UPDATE execution_receipts SET actual=0",
            "UPDATE execution_invocations SET scope='{}'",
            "DELETE FROM execution_jobs",
            "TRUNCATE execution_results",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                async with runtime.connection() as c:
                    await c.execute(statement)
    finally:
        await runtime.close()
        async with db.connection() as c:
            await c.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            await c.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


@pytest.mark.asyncio
@pytest.mark.parametrize("input_tokens,output_tokens", [(65537, 0), (0, 101)])
async def test_usage_dimension_overage_even_inside_money_reserve(
    db, input_tokens, output_tokens
):
    service, ledger, scope, ctx = await setup(db)
    job = await admit(service, ctx)
    await service.dispatch(job["id"], job["attempt_id"])
    with pytest.raises(PlatformError):
        await service.finish(
            job["id"],
            job["attempt_id"],
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            terminal_status="completed",
            provider_response_id="r",
            evidence_hash="e",
            payload=b"{}",
            content_type="application/json",
        )
    assert (await ledger.summary(scope))["reserved"] == "6573600"
    assert (await service.get(scope, job["id"]))["status"] == "pending_reconciliation"
    assert not await service.readiness("easel", mode="test", bindings=BINDINGS)


@pytest.mark.asyncio
async def test_concurrent_cancel_dispatch_no_unknown_release(db):
    service, ledger, scope, ctx = await setup(db)
    job = await admit(service, ctx)
    await asyncio.gather(
        service.dispatch(job["id"], job["attempt_id"]),
        service.cancel(scope, job["id"]),
        return_exceptions=True,
    )
    state = await service.get(scope, job["id"])
    assert state["status"] in ("released", "pending_reconciliation")
    async with db.connection() as c:
        row = await (
            await c.execute(
                "SELECT dispatched_at,status FROM execution_jobs WHERE id=%s",
                (job["id"],),
            )
        ).fetchone()
        assert (row["dispatched_at"] is None) == (row["status"] == "released")


@pytest.mark.asyncio
async def test_separate_database_pools_serialize_wallet_concurrency(db, test_dsn):
    from platform_service.db import Database
    from platform_service.identity import IdentityService
    from platform_service.execution.core import ExecutionService

    service, ledger, scope, ctx = await setup(db)
    other = Database(test_dsn, schema=db.schema, max_size=5)
    await other.open()
    try:
        other_service = ExecutionService(other, IdentityService(other, None))
        results = await asyncio.gather(
            *[
                admit(service if i % 2 else other_service, ctx, str(i))
                for i in range(12)
            ],
            return_exceptions=True,
        )
        assert sum(isinstance(r, dict) for r in results) == 10
        assert (await ledger.summary(scope))["reserved"] == str(6573600 * 10)
    finally:
        await other.close()
