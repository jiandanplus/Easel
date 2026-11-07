import pytest

from platform_service.contracts import OperatorIdentity
from platform_service.credits import CreditLedger
from platform_service.errors import PlatformError
from platform_service.payment_config import PaymentSettings
from test_membership import seed


async def setup_runtime(db, tmp_path, *, providers=None):
    from platform_service.payment_runtime import PaymentRuntime

    _, _, plan, scope = await seed(db, credits="100000000")
    config = PaymentSettings(
        mode="test",
        test_key=b"test-runtime-key" * 4,
        test_state_file=tmp_path / "provider.json",
    )
    runtime = PaymentRuntime(db, config, providers=providers)
    operator = OperatorIdentity(
        "runtime-operator", frozenset({"billing:publish", "billing:refund"})
    )
    await runtime.billing.publish_offer(
        operator,
        {
            "id": "runtime-offer",
            "plan_id": plan["id"],
            "version": "1",
            "amount_minor": "1",
            "currency": "CNY",
            "test_only": True,
            "terms": {
                "refund_policy": "unused_full_only",
                "description": "模拟测试条款",
            },
        },
    )
    order = await runtime.billing.create_order(
        scope, offer_id="runtime-offer", channel="wechat", idempotency_key="one"
    )
    return runtime, operator, scope, order


@pytest.mark.asyncio
async def test_worker_checkout_query_recovery_and_issuance_are_durable(db, tmp_path):
    runtime, _, scope, order = await setup_runtime(db, tmp_path)
    assert (await runtime.run_once())["completed"] >= 1
    current = await runtime.billing.get_order(scope, order["id"])
    assert current["status"] == "pending" and current["checkout"]["qr_code"].startswith(
        "easel-test-payment:"
    )
    assert (await CreditLedger(db).summary(scope))["available"] == "0"
    from platform_service.payment_runtime import payment_order

    provider = runtime.providers["wechat"]
    provider.record(
        payment_order(
            {
                "order_id": order["id"],
                "amount_minor": "1",
                "currency": "CNY",
                "description": "Fixture",
                "expires_at": order["expires_at"],
            }
        ),
        status="paid",
        event_id="query-recovery-fixture",
    )
    await runtime.billing.request_refresh(scope, order["id"])
    await runtime.run_once()
    assert (await runtime.billing.get_order(scope, order["id"]))[
        "credit_state"
    ] == "granted"
    assert (await CreditLedger(db).summary(scope))["available"] == "100000000"
    await runtime.run_once()
    assert (await CreditLedger(db).summary(scope))["issued"] == "100000000"


@pytest.mark.asyncio
async def test_provider_timeout_preserves_order_and_retry_instead_of_fake_success(
    db, tmp_path
):
    class Uncertain:
        async def create(self, order):
            raise PlatformError(502, "payment_transport_uncertain", retryable=True)

    runtime, _, scope, order = await setup_runtime(
        db, tmp_path, providers={"wechat": Uncertain()}
    )
    result = await runtime.run_once(limit=1)
    assert result["retried"] == 1
    assert (await runtime.billing.get_order(scope, order["id"]))[
        "credit_state"
    ] == "not_granted"
    async with db.connection() as conn:
        row = await (
            await conn.execute(
                "SELECT status,last_error FROM billing_outbox WHERE kind='create_checkout'"
            )
        ).fetchone()
    assert (
        row["status"] == "pending"
        and row["last_error"] == "payment_transport_uncertain"
    )


@pytest.mark.asyncio
async def test_worker_checks_frozen_merchant_before_any_network_call(db, tmp_path):
    class MustNotCall:
        async def create(self, order):
            raise AssertionError("wrong merchant must never be called")

    runtime, _, scope, order = await setup_runtime(
        db, tmp_path, providers={"wechat": MustNotCall()}
    )
    runtime.contexts["wechat"] = {
        **runtime.contexts["wechat"],
        "merchant_id": "different-merchant",
    }
    assert (await runtime.run_once(limit=1))["retried"] == 1
    assert (await runtime.billing.get_order(scope, order["id"]))[
        "credit_state"
    ] == "not_granted"


@pytest.mark.asyncio
async def test_worker_refund_pending_stays_frozen_until_verified_refund(db, tmp_path):
    from platform_service.payment_runtime import payment_order
    from platform_service.payments import RefundRequest

    runtime, operator, scope, order = await setup_runtime(db, tmp_path)
    await runtime.run_once()
    porder = payment_order(
        {
            "order_id": order["id"],
            "amount_minor": "1",
            "currency": "CNY",
            "description": "Fixture",
            "expires_at": order["expires_at"],
        }
    )
    provider = runtime.providers["wechat"]
    raw, headers = provider.record(porder, status="paid", event_id="payment-one")
    await runtime.billing.apply_verified_event(
        provider.verify_notification(raw, headers).to_event()
    )
    refund = await runtime.billing.request_refund(
        operator, order["id"], idempotency_key="refund-one"
    )
    await runtime.run_once()
    wallet = await CreditLedger(db).summary(scope)
    assert wallet["available"] == "0" and wallet["frozen"] == "100000000"
    request = RefundRequest(refund["id"], 1, "测试退款")
    provider.record(porder, status="refunded", event_id="refund-one", refund=request)
    async with db.connection() as conn:
        await conn.execute(
            "UPDATE billing_outbox SET due_at=clock_timestamp() WHERE kind='query_refund' AND status='pending'"
        )
    await runtime.run_once()
    assert (await CreditLedger(db).summary(scope))["refunded"] == "100000000"
    assert (await runtime.billing.get_refund(operator, refund["id"]))[
        "status"
    ] == "succeeded"
