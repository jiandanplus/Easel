import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import pytest
from test_membership import seed
from platform_service.errors import PlatformError


async def setup_billing(db):
    from platform_service.billing import BillingService
    from platform_service.contracts import OperatorIdentity

    _, _, plan, scope = await seed(db)
    operator = OperatorIdentity(
        "billing-operator",
        frozenset({"billing:publish", "billing:refund", "plans:publish", "test:issue"}),
    )
    service = BillingService(
        db,
        mode="test",
        channels=(
            {"id": "wechat", "merchant_id": "m", "app_id": "a", "enabled": True},
        ),
    )
    offer = dict(
        id="offer",
        plan_id=plan["id"],
        version="1",
        amount_minor="1",
        currency="CNY",
        test_only=True,
        terms={"refund_policy": "unused_full_only", "description": "Isolated fixture"},
    )
    await service.publish_offer(operator, offer)
    return service, operator, scope, offer


def event(order, **changes):
    return dict(
        channel="wechat",
        mode="test",
        event_id="event-1",
        order_id=order["id"],
        provider_transaction_id="trade-1",
        status="paid",
        amount_minor=1,
        currency="CNY",
        merchant_id="m",
        app_id="a",
        occurred_at=datetime.now(timezone.utc).isoformat(),
        raw_digest="a" * 64,
        **changes,
    )


async def order_new(service, scope, key="one"):
    return await service.create_order(
        scope, offer_id="offer", channel="wechat", idempotency_key=key
    )


async def paid(db):
    s, op, scope, offer = await setup_billing(db)
    order = await order_new(s, scope)
    fact = event(order)
    await s.apply_verified_event(fact)
    return s, op, scope, order, fact


@pytest.mark.asyncio
async def test_order_idempotency_scope_and_frozen_offer(db):
    s, op, scope, offer = await setup_billing(db)
    order = await order_new(s, scope)
    assert order == await order_new(s, scope)
    assert len(order["id"]) == 32 and order["amount_minor"] == "1"
    with pytest.raises(PlatformError):
        await s.create_order(
            scope, offer_id="other", channel="wechat", idempotency_key="one"
        )
    for foreign in (
        replace(scope, product_id="second"),
        replace(scope, user_id="other"),
        replace(scope, tenant_id="foreign"),
    ):
        with pytest.raises(PlatformError):
            await s.get_order(foreign, order["id"])
    await s.withdraw_offer(op, "offer", "withdrawn")
    assert (await s.catalog("easel"))["offers"] == []
    await s.apply_verified_event(event(order))
    assert (await s.get_order(scope, order["id"]))["credit_state"] == "granted"


@pytest.mark.asyncio
async def test_duplicate_verified_payment_is_atomic_once(db):
    s, op, scope, order, fact = await paid(db)
    await asyncio.gather(*(s.apply_verified_event(fact) for _ in range(4)))
    async with db.connection() as c:
        for table in ("memberships", "credit_lots", "payment_transactions"):
            assert (
                await (await c.execute(f"SELECT count(*) AS n FROM {table}")).fetchone()
            )["n"] == 1
        assert (
            await (
                await c.execute("SELECT sum(delta) AS n FROM credit_entries")
            ).fetchone()
        )["n"] == 0
    assert (await s.get_order(scope, order["id"]))["credit_state"] == "granted"


@pytest.mark.parametrize(
    "field,value",
    [
        ("amount_minor", 2),
        ("merchant_id", "wrong"),
        ("app_id", "wrong"),
        ("currency", "USD"),
        ("mode", "live"),
    ],
)
@pytest.mark.asyncio
async def test_invalid_verified_binding_never_grants(db, field, value):
    s, _, scope, _ = await setup_billing(db)
    order = await order_new(s, scope)
    with pytest.raises(PlatformError):
        await s.apply_verified_event({**event(order), field: value})
    assert (await s.get_order(scope, order["id"]))["credit_state"] == "not_granted"


@pytest.mark.asyncio
async def test_closed_then_paid_and_duplicate_trade_conflicts(db):
    s, _, scope, _ = await setup_billing(db)
    first = await order_new(s, scope)
    await s.apply_verified_event(
        {
            **event(first),
            "event_id": "close",
            "status": "closed",
            "provider_transaction_id": None,
        }
    )
    assert (await s.get_order(scope, first["id"]))["status"] == "closed"
    await s.apply_verified_event(event(first))
    await s.apply_verified_event(
        {**event(first), "event_id": "extra", "provider_transaction_id": "trade-2"}
    )
    assert (await s.get_order(scope, first["id"]))[
        "status"
    ] == "reconciliation_required"
    second = await order_new(s, scope, "two")
    await s.apply_verified_event({**event(second), "event_id": "collision"})
    assert (await s.get_order(scope, second["id"]))[
        "status"
    ] == "reconciliation_required"
    async with db.connection() as c:
        assert (
            await (await c.execute("SELECT count(*) AS n FROM memberships")).fetchone()
        )["n"] == 1


@pytest.mark.asyncio
async def test_outbox_fencing_checkout_and_refresh(db):
    s, _, scope, _ = await setup_billing(db)
    order = await order_new(s, scope)
    jobs = await s.claim_outbox(worker_id="worker")
    job = jobs[0]
    assert job["kind"] == "create_checkout"
    with pytest.raises(PlatformError):
        await s.finish_outbox(
            job["id"],
            "wrong",
            result={
                "status": "pending",
                "qr_code": "weixin://fixture",
                "expires_at": order["expires_at"],
            },
        )
    await s.finish_outbox(
        job["id"],
        job["lease_token"],
        result={
            "status": "pending",
            "qr_code": "weixin://fixture",
            "expires_at": order["expires_at"],
        },
    )
    assert (await s.get_order(scope, order["id"]))["checkout"][
        "qr_code"
    ] == "weixin://fixture"
    await s.request_refresh(scope, order["id"])
    jobs = await s.claim_outbox(worker_id="worker")
    job = next(x for x in jobs if x["kind"] == "query_order")
    await s.finish_outbox(
        job["id"],
        job["lease_token"],
        result={"status": "observed", "event": event(order)},
    )
    assert (await s.get_order(scope, order["id"]))["credit_state"] == "granted"


@pytest.mark.asyncio
async def test_disabled_billing_cannot_create(db):
    from platform_service.billing import BillingService

    _, _, scope, _ = await setup_billing(db)
    with pytest.raises(PlatformError):
        await order_new(BillingService(db), scope)


@pytest.mark.asyncio
async def test_old_outbox_lease_cannot_finish_after_reclaim(db):
    s, _, scope, _ = await setup_billing(db)
    order = await order_new(s, scope)
    old = (await s.claim_outbox(worker_id="old"))[0]
    async with db.connection() as c:
        await c.execute(
            "UPDATE billing_outbox SET lease_until=clock_timestamp()-interval '1 second'"
        )
    new = (await s.claim_outbox(worker_id="new"))[0]
    assert old["lease_token"] != new["lease_token"]
    with pytest.raises(PlatformError):
        await s.finish_outbox(
            old["id"],
            old["lease_token"],
            result={"status": "observed", "event": event(order)},
        )
    assert (await s.get_order(scope, order["id"]))["credit_state"] == "not_granted"


@pytest.mark.asyncio
async def test_payment_grant_rolls_back_when_atomic_outbox_fails(db):
    import psycopg

    s, _, scope, _ = await setup_billing(db)
    order = await order_new(s, scope)
    async with db.connection() as c:
        await c.execute(
            "CREATE FUNCTION reject_paid() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.kind='order_paid' THEN RAISE EXCEPTION 'forced'; END IF; RETURN NEW; END $$"
        )
        await c.execute(
            "CREATE TRIGGER reject_paid BEFORE INSERT ON billing_outbox FOR EACH ROW EXECUTE FUNCTION reject_paid()"
        )
    with pytest.raises(psycopg.Error):
        await s.apply_verified_event(event(order))
    async with db.connection() as c:
        for table in (
            "memberships",
            "credit_lots",
            "payment_events",
            "payment_transactions",
            "credit_journals",
        ):
            assert (
                await (await c.execute(f"SELECT count(*) AS n FROM {table}")).fetchone()
            )["n"] == 0
    assert (await s.get_order(scope, order["id"]))["credit_state"] == "not_granted"


@pytest.mark.asyncio
async def test_nonfinancial_closed_fact_may_omit_price(db):
    s, _, scope, _ = await setup_billing(db)
    order = await order_new(s, scope)
    await s.apply_verified_event(
        {
            **event(order),
            "status": "closed",
            "amount_minor": None,
            "currency": None,
            "occurred_at": None,
            "provider_transaction_id": None,
        }
    )
    assert (await s.get_order(scope, order["id"]))["status"] == "closed"


@pytest.mark.asyncio
async def test_worker_cannot_claim_other_funding_mode_or_completed_financial_work(db):
    from platform_service.billing import BillingService

    s, _, scope, _ = await setup_billing(db)
    order = await order_new(s, scope)
    live = BillingService(db, mode="live", channels=s.channels.values())
    assert await live.claim_outbox(worker_id="live") == []
    await s.apply_verified_event(event(order))
    jobs = await s.claim_outbox(worker_id="test")
    assert [j["kind"] for j in jobs] == ["order_paid"]


@pytest.mark.asyncio
async def test_checkout_qr_payload_can_exceed_identifier_size(db):
    s, _, scope, _ = await setup_billing(db)
    order = await order_new(s, scope)
    job = (await s.claim_outbox(worker_id="w"))[0]
    await s.finish_outbox(
        job["id"],
        job["lease_token"],
        result={
            "status": "pending",
            "qr_code": "https://fixture.invalid/" + ("x" * 400),
            "expires_at": order["expires_at"],
        },
    )
    assert len((await s.get_order(scope, order["id"]))["checkout"]["qr_code"]) > 256


@pytest.mark.asyncio
async def test_unknown_verified_order_is_durable_and_never_grants(db):
    s, _, _, _ = await setup_billing(db)
    fact = event({"id": "0" * 32})
    assert (await s.apply_verified_event(fact))["status"] == "reconciliation_required"
    await s.apply_verified_event(fact)
    async with db.connection() as c:
        assert (
            await (
                await c.execute("SELECT count(*) AS n FROM payment_orphan_events")
            ).fetchone()
        )["n"] == 1
        assert (
            await (await c.execute("SELECT count(*) AS n FROM memberships")).fetchone()
        )["n"] == 0


@pytest.mark.parametrize("qr", ["https://fixture.invalid/\nunsafe", "x" * 2049])
@pytest.mark.asyncio
async def test_checkout_rejects_control_or_oversized_qr(db, qr):
    s, _, scope, _ = await setup_billing(db)
    order = await order_new(s, scope)
    job = (await s.claim_outbox(worker_id="w"))[0]
    with pytest.raises(PlatformError):
        await s.finish_outbox(
            job["id"],
            job["lease_token"],
            result={
                "status": "pending",
                "qr_code": qr,
                "expires_at": order["expires_at"],
            },
        )


@pytest.mark.asyncio
async def test_order_revision_covers_refund_only_changes_and_manual_review(db):
    from test_billing_refunds import refund_fact

    s, op, scope, _ = await setup_billing(db)
    order = await order_new(s, scope)
    assert order["revision"] == "0"
    fact = event(order)
    await s.apply_verified_event(fact)
    paid_version = int((await s.get_order(scope, order["id"]))["revision"])
    refund = await s.request_refund(op, order["id"], idempotency_key="rev")
    pending_version = int((await s.get_order(scope, order["id"]))["revision"])
    assert pending_version > paid_version > 0
    await s.apply_verified_event(
        refund_fact(fact, refund["id"], status="refund_failed")
    )
    failed_version = int((await s.get_order(scope, order["id"]))["revision"])
    assert failed_version > pending_version
    await s.apply_verified_event(
        {**fact, "event_id": "observe", "status": "refund_observed"}
    )
    assert int((await s.get_order(scope, order["id"]))["revision"]) > failed_version
