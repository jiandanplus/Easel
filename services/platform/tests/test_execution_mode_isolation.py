"""Runtime mode transitions with real ledger state and intercepted provider I/O."""

import httpx
import pytest

from platform_service.billing import BillingService
from platform_service.contracts import OperatorIdentity
from platform_service.errors import PlatformError
from platform_service.execution_config import ExecutionProvider, ExecutionSettings
from platform_service.execution_runtime import ExecutionRuntime
from platform_service.membership import MembershipService
from test_billing import event
from test_execution_core import OPERATOR, definition, setup


async def mode_fixture(db, mode):
    service, ledger, scope, _ = await setup(db)
    model = "fixture-text"
    if mode == "live":
        operator = OperatorIdentity(
            "fixture-billing", frozenset({"plans:publish", "billing:publish"})
        )
        async with db.connection() as conn:
            plan = (
                await (await conn.execute("SELECT definition FROM plans")).fetchone()
            )["definition"]
        await MembershipService(db).publish_plan(
            operator,
            {
                **plan,
                "id": "live-plan",
                "name": "Synthetic live fixture",
                "test_only": False,
            },
        )
        billing = BillingService(
            db,
            mode="live",
            channels=(
                {"id": "wechat", "merchant_id": "m", "app_id": "a", "enabled": True},
            ),
        )
        await billing.publish_offer(
            operator,
            {
                "id": "live-offer",
                "plan_id": "live-plan",
                "version": "1",
                "currency": "CNY",
                "amount_minor": "1",
                "test_only": False,
                "terms": {
                    "refund_policy": "unused_full_only",
                    "description": "Synthetic mode-isolation fixture",
                },
            },
        )
        order = await billing.create_order(
            scope,
            offer_id="live-offer",
            channel="wechat",
            idempotency_key="fixture-live",
        )
        await billing.apply_verified_event({**event(order), "mode": "live"})
        model = "live-fixture-text"
        await service.publish(
            {
                **definition(),
                "id": model,
                "test_only": False,
                "verification": "live",
                "verification_ref": "synthetic-fixture-only",
            },
            OPERATOR,
        )
        await service.set_enabled(model, True, OPERATOR)
    return service, ledger, scope, model


def settings(mode, model):
    if mode == "disabled":
        return ExecutionSettings()
    return ExecutionSettings(
        mode=mode,
        providers={
            "fixture": ExecutionProvider(
                "1",
                "http://127.0.0.1:9999/responses"
                if mode == "test"
                else "https://provider.invalid/responses",
                "synthetic-" + mode + "-secret",
                {model: mode + "-upstream"},
            ),
        },
    )


def runtime(db, service, mode, model, calls):
    async def provider(request):
        calls.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "id": "synthetic-response",
                "status": "completed",
                "usage": {"input_tokens": 3, "output_tokens": 2},
            },
        )

    return ExecutionRuntime(
        db,
        service.identity,
        settings(mode, model),
        service=service,
        transport=httpx.MockTransport(provider),
    )


async def assert_no_execution(db, ledger, scope, before, calls):
    assert calls == []
    assert await ledger.summary(scope) == before
    async with db.connection() as conn:
        for table in ("execution_jobs", "credit_reservations", "execution_receipts"):
            assert (
                await (
                    await conn.execute(f"SELECT count(*) AS n FROM {table}")
                ).fetchone()
            )["n"] == 0
        assert (
            await (
                await conn.execute(
                    "SELECT sum(operation_count) AS n FROM execution_invocations"
                )
            ).fetchone()
        )["n"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("old_mode,new_mode", [("test", "live"), ("live", "test")])
async def test_old_task_bearer_cannot_cross_runtime_funding_mode(
    db, old_mode, new_mode
):
    service, ledger, scope, model = await mode_fixture(db, old_mode)
    calls = []
    original = runtime(db, service, old_mode, model, calls)
    switched = runtime(db, service, new_mode, model, calls)
    try:
        token = (
            await original.mint(
                scope, {"model": model, "invocation_key": "before-switch"}
            )
        )["task_token"]
        assert (await switched.catalog(scope))["models"] == []
        capabilities = await switched.capabilities("easel")
        assert not capabilities["subscription_execution_enabled"]
        assert not capabilities["test_execution_enabled"]
        before = await ledger.summary(scope)
        with pytest.raises(PlatformError, match="execution_mode_mismatch") as error:
            await switched.respond(
                token, "after-switch", {"model": model, "input": "synthetic probe"}
            )
        assert error.value.status == 403
        await assert_no_execution(db, ledger, scope, before, calls)
    finally:
        await original.close()
        await switched.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("old_mode", ["test", "live"])
async def test_default_disabled_runtime_rejects_existing_task_bearer(db, old_mode):
    service, ledger, scope, model = await mode_fixture(db, old_mode)
    calls = []
    original = runtime(db, service, old_mode, model, calls)
    disabled = runtime(db, service, "disabled", model, calls)
    try:
        token = (
            await original.mint(
                scope, {"model": model, "invocation_key": "before-disable"}
            )
        )["task_token"]
        before = await ledger.summary(scope)
        with pytest.raises(PlatformError, match="execution_disabled") as error:
            await disabled.respond(
                token, "after-disable", {"model": model, "input": "synthetic probe"}
            )
        assert error.value.status == 503
        await assert_no_execution(db, ledger, scope, before, calls)
    finally:
        await original.close()
        await disabled.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "old_mode,new_mode",
    [("test", "live"), ("live", "test"), ("test", "disabled"), ("live", "disabled")],
)
async def test_trusted_dispatched_receipt_keeps_original_mode_after_switch_and_revoke(
    db, old_mode, new_mode
):
    service, ledger, scope, model = await mode_fixture(db, old_mode)
    calls = []
    original = runtime(db, service, old_mode, model, calls)
    switched = runtime(db, service, new_mode, model, calls)
    try:
        token = (
            await original.mint(scope, {"model": model, "invocation_key": "dispatched"})
        )["task_token"]
        context = await service.authenticate_task(token)
        job = await service.admit(
            context,
            key="incurred",
            body_hash="synthetic-hash",
            output_limit=100,
            bindings=original.bindings,
        )
        await service.dispatch(job["id"], job["attempt_id"])
        before = await ledger.summary(scope)
        async with db.connection() as conn:
            await conn.execute("UPDATE identity_sessions SET revoked_at=now()")
        # Internal receipts settle incurred usage with the frozen job, independently
        # of the current public admission mode and revoked task authorization.
        await switched.service.finish(
            job["id"],
            job["attempt_id"],
            input_tokens=3,
            output_tokens=2,
            terminal_status="completed",
            provider_response_id="synthetic-response",
            evidence_hash="synthetic-receipt",
            payload=b"{}",
            content_type="application/json",
        )
        after = await ledger.summary(scope)
        assert after["spent"] == "700"
        assert after["reserved"] == "0"
        opposite = "live" if old_mode == "test" else "test"
        assert after[opposite + "_available"] == before[opposite + "_available"]
        async with db.connection() as conn:
            row = await (
                await conn.execute(
                    "SELECT funding_mode,status FROM execution_jobs WHERE id=%s",
                    (job["id"],),
                )
            ).fetchone()
        assert (row["funding_mode"], row["status"]) == (old_mode, "settled")
        assert calls == []
    finally:
        await original.close()
        await switched.close()
