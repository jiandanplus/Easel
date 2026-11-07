import asyncio
import secrets
from uuid import uuid4
import pytest
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from test_billing import setup_billing, order_new, paid, event
from platform_service.credits import CreditLedger
from platform_service.membership import MembershipService
from platform_service.errors import PlatformError


@pytest.mark.asyncio
async def test_live_and_test_funds_remain_separate(db):
    from platform_service.billing import BillingService

    s, op, scope, _ = await setup_billing(db)
    async with db.connection() as c:
        plan = (await (await c.execute("SELECT definition FROM plans")).fetchone())[
            "definition"
        ]
    live_plan = {**plan, "id": "live-plan", "name": "Live", "test_only": False}
    membership = MembershipService(db)
    await membership.publish_plan(op, live_plan)
    with pytest.raises(PlatformError):
        await membership.issue_test(op, "wallet", "live-plan", "forbidden")
    await membership.issue_test(op, "wallet", plan["id"], "test-grant")
    live = BillingService(
        db,
        mode="live",
        channels=(
            {"id": "wechat", "merchant_id": "m", "app_id": "a", "enabled": True},
        ),
    )
    await live.publish_offer(
        op,
        dict(
            id="live-offer",
            plan_id="live-plan",
            version="1",
            currency="CNY",
            amount_minor="99",
            test_only=False,
            terms={
                "refund_policy": "unused_full_only",
                "description": "Explicit fixture live simulation",
            },
        ),
    )
    order = await live.create_order(
        scope, offer_id="live-offer", channel="wechat", idempotency_key="live"
    )
    await live.apply_verified_event(
        {**event(order), "mode": "live", "amount_minor": 99}
    )
    ledger = CreditLedger(db)
    summary = await ledger.summary(scope)
    assert (
        summary["funding_mode"],
        summary["test_available"],
        summary["live_available"],
    ) == ("mixed", "100", "100")
    await ledger.reserve(
        scope,
        operation="mode",
        key="live-only",
        body_hash="h",
        amount=100,
        rate_version="v",
        funding_mode="live",
    )
    with pytest.raises(PlatformError):
        await ledger.reserve(
            scope,
            operation="mode",
            key="live-only",
            body_hash="h",
            amount=100,
            rate_version="v",
            funding_mode="test",
        )
    with pytest.raises(PlatformError):
        await ledger.reserve(
            scope,
            operation="mode",
            key="too-much",
            body_hash="h",
            amount=1,
            rate_version="v",
            funding_mode="live",
        )
    assert (await ledger.summary(scope))["test_available"] == "100"


@pytest.mark.asyncio
async def test_billing_history_and_order_bindings_are_database_protected(db):
    s, op, scope, order, fact = await paid(db)
    for query in (
        "UPDATE billing_offers SET version='2'",
        "DELETE FROM payment_events",
        "TRUNCATE payment_event_audits",
        "UPDATE billing_orders SET amount_minor=2",
        "UPDATE billing_orders SET billing_account_id='foreign-wallet'",
        "UPDATE billing_orders SET membership_id=NULL,paid_at=NULL",
        "UPDATE credit_lots SET refunded=1",
    ):
        with pytest.raises(psycopg.Error):
            async with db.connection() as c:
                await c.execute(query)


@pytest.mark.asyncio
async def test_database_rejects_fabricated_paid_state_and_funding_rewrite(db):
    s, _, scope, _ = await setup_billing(db)
    order = await order_new(s, scope)
    with pytest.raises(psycopg.Error):
        async with db.connection() as c:
            await c.execute(
                "UPDATE billing_orders SET status='paid',credit_state='granted' WHERE id=%s",
                (order["id"],),
            )
    await s.apply_verified_event(event(order))
    with pytest.raises(psycopg.Error):
        async with db.connection() as c:
            await c.execute("UPDATE credit_lots SET test_only=false")
    with pytest.raises(psycopg.Error):
        async with db.connection() as c:
            await c.execute("UPDATE billing_orders SET status='pending'")


@pytest.mark.asyncio
async def test_database_rejects_refund_success_without_compensation(db):
    s, op, scope, order, fact = await paid(db)
    refund = await s.request_refund(op, order["id"], idempotency_key="r")
    with pytest.raises(psycopg.Error):
        async with db.connection() as c:
            await c.execute(
                "UPDATE billing_refunds SET status='succeeded' WHERE id=%s",
                (refund["id"],),
            )
            await c.execute(
                "UPDATE billing_orders SET status='refunded',credit_state='revoked' WHERE id=%s",
                (order["id"],),
            )


@pytest.mark.asyncio
async def test_low_privilege_role_full_payment_and_refund(db, test_dsn):
    from platform_service.db import Database
    from platform_service.billing import BillingService
    from platform_service.permissions import grant_runtime_permissions

    s, op, scope, _ = await setup_billing(db)
    role = "s2_runtime_" + uuid4().hex
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
        service = BillingService(runtime, mode="test", channels=s.channels.values())
        order = await order_new(service, scope)
        fact = event(order)
        await service.apply_verified_event(fact)
        refund = await service.request_refund(
            op, order["id"], idempotency_key="low-refund"
        )
        from test_billing_refunds import refund_fact

        await service.apply_verified_event(refund_fact(fact, refund["id"]))
        assert (await CreditLedger(runtime).summary(scope))["refunded"] == "100"
        jobs = await service.claim_outbox(worker_id="low-role")
        job = next(j for j in jobs if j["kind"] == "order_refunded")
        await service.finish_outbox(
            job["id"], job["lease_token"], result={"status": "delivered"}
        )
        for query in (
            "DELETE FROM billing_orders",
            "UPDATE payment_events SET fact='{}'",
            "TRUNCATE billing_refunds",
            "UPDATE billing_orders SET revision=0",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                async with runtime.connection() as c:
                    await c.execute(query)
    finally:
        await runtime.close()
        async with db.connection() as c:
            await c.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            await c.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


@pytest.mark.asyncio
@pytest.mark.parametrize("read_list", [True, False])
async def test_order_projection_serializes_refund_commit(
    db, test_dsn, monkeypatch, read_list
):
    from platform_service.billing import BillingService
    from platform_service.db import Database

    s, op, scope, order, _ = await paid(db)
    before = await s.get_order(scope, order["id"])
    other = Database(test_dsn, schema=db.schema, min_size=1, max_size=1)
    await other.open()
    entered, release = asyncio.Event(), asyncio.Event()
    view = s._view

    async def paused_view(c, row):
        # Pause after the order/revision SELECT, before its separate refund SELECT.
        entered.set()
        await release.wait()
        return await view(c, row)

    monkeypatch.setattr(s, "_view", paused_view)
    reader = writer = None
    try:
        async with other.connection() as c:
            pid = (
                await (await c.execute("SELECT pg_backend_pid() AS pid")).fetchone()
            )["pid"]
        reader = asyncio.create_task(
            s.orders(scope) if read_list else s.get_order(scope, order["id"])
        )
        await asyncio.wait_for(entered.wait(), timeout=5)
        service = BillingService(other, mode="test", channels=s.channels.values())
        writer = asyncio.create_task(
            service.request_refund(op, order["id"], idempotency_key="interleaved")
        )
        for _ in range(100):
            async with db.connection() as c:
                blocked = await (
                    await c.execute(
                        "SELECT cardinality(pg_blocking_pids(%s)) AS n", (pid,)
                    )
                ).fetchone()
            if blocked["n"]:
                break
            await asyncio.sleep(0.01)
        assert blocked["n"] > 0, "refund must wait for the canonical wallet lock"
        assert not writer.done()
        release.set()
        projection = await asyncio.wait_for(reader, timeout=5)
        if read_list:
            projection = projection[0]
        assert projection["revision"] == before["revision"]
        assert projection["refund"] is None
        await asyncio.wait_for(writer, timeout=5)
        after = await s.get_order(scope, order["id"])
        assert int(after["revision"]) > int(projection["revision"])
        assert after["refund"]["status"] == "pending"
    finally:
        release.set()
        for task in (reader, writer):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (reader, writer) if task is not None),
            return_exceptions=True,
        )
        await other.close()
