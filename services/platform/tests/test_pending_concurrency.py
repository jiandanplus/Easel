"""Real PostgreSQL coverage for separate active and unknown-cost wallet limits.

All grants and payment facts are synthetic rows in this test's owned schema.
No provider, payment network or production database is contacted.
"""

import asyncio
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from platform_service.credits import CreditLedger
from platform_service.db import Database
from platform_service.errors import PlatformError
from test_membership import seed


async def prepare_wallet(db, *, concurrency=1, credits="100000"):
    membership, operator, original, scope = await seed(db, credits)
    plan = {**original, "id": "concurrency-fixture-v1", "name": "Concurrency fixture",
            "concurrency": concurrency}
    await membership.publish_plan(operator, plan)
    await membership.issue_test(operator, "wallet", plan["id"], "primary-grant")
    return CreditLedger(db), scope, membership, operator, plan


async def reserve(ledger, scope, key, *, amount=10, funding_mode="test", operation="text", rate="v1"):
    return await ledger.reserve(scope, operation=operation, key=key, body_hash="fixture-hash",
                                amount=amount, rate_version=rate, funding_mode=funding_mode)


async def pending(ledger, scope, key, **kwargs):
    row = await reserve(ledger, scope, key, **kwargs)
    await ledger.dispatch(scope, row["id"])
    return await ledger.pending(scope, row["id"])


async def snapshot(db):
    async with db.connection() as c:
        return {table: await (await c.execute("SELECT to_jsonb(t) AS value FROM " + table + " t ORDER BY to_jsonb(t)::text")).fetchall()
                for table in ("credit_lots", "credit_reservations", "credit_holds", "credit_journals", "credit_entries")}


async def original_hold(db, reservation):
    async with db.connection() as c:
        return {
            "reservation": await (await c.execute("SELECT to_jsonb(t) AS value FROM credit_reservations t WHERE id=%s", (reservation,))).fetchone(),
            "holds": await (await c.execute("SELECT to_jsonb(t) AS value FROM credit_holds t WHERE reservation_id=%s ORDER BY lot_id", (reservation,))).fetchall(),
            "journals": await (await c.execute("SELECT to_jsonb(t) AS value FROM credit_journals t WHERE source=%s ORDER BY id", (reservation,))).fetchall(),
            "entries": await (await c.execute("SELECT to_jsonb(t) AS value FROM credit_entries t JOIN credit_journals j ON j.id=t.journal_id WHERE j.source=%s ORDER BY t.id", (reservation,))).fetchall(),
        }


async def assert_conservation(db):
    async with db.connection() as c:
        lots = await (await c.execute("SELECT * FROM credit_lots ORDER BY id")).fetchall()
        accounts = {lot["billing_account_id"] for lot in lots}
        for lot in lots:
            assert lot["issued"] == sum(lot[name] for name in ("available", "held", "spent", "expired", "refunded"))
            holds = await (await c.execute("""SELECT coalesce(sum(h.amount),0) AS amount
                FROM credit_holds h JOIN credit_reservations r ON r.id=h.reservation_id
                WHERE h.lot_id=%s AND r.status IN ('reserved','dispatched','pending_reconciliation')""", (lot["id"],))).fetchone()
            assert holds["amount"] == lot["held"]
        for account in accounts:
            entries = {row["bucket"]: int(row["total"]) for row in await (await c.execute(
                "SELECT bucket,sum(delta::numeric) AS total FROM credit_entries WHERE billing_account_id=%s GROUP BY bucket", (account,))).fetchall()}
            assert -entries.get("issuance", 0) == sum(lot["issued"] for lot in lots if lot["billing_account_id"] == account)
            for bucket in ("available", "held", "spent", "expired", "refunded"):
                assert entries.get(bucket, 0) == sum(lot[bucket] for lot in lots if lot["billing_account_id"] == account)
        assert not await (await c.execute("SELECT journal_id FROM credit_entries GROUP BY journal_id HAVING sum(delta::numeric)<>0")).fetchall()


async def add_synthetic_live_credit(db, membership, scope, test_plan):
    """Use existing verified-fact boundary; this is not a live payment test."""
    from platform_service.billing import BillingService
    from platform_service.contracts import OperatorIdentity

    operator = OperatorIdentity("live-fixture-operator", frozenset({"plans:publish", "billing:publish"}))
    plan = {**test_plan, "id": "live-concurrency-fixture-v1", "name": "Live concurrency fixture", "test_only": False}
    await membership.publish_plan(operator, plan)
    billing = BillingService(db, mode="live", channels=({"id": "wechat", "merchant_id": "fixture-merchant", "app_id": "fixture-app", "enabled": True},))
    await billing.publish_offer(operator, {"id": "live-fixture-offer", "plan_id": plan["id"], "version": "1",
        "amount_minor": "1", "currency": "CNY", "test_only": False,
        "terms": {"refund_policy": "unused_full_only", "description": "Synthetic owned-schema fixture only"}})
    order = await billing.create_order(scope, offer_id="live-fixture-offer", channel="wechat", idempotency_key="live-fixture-order")
    await billing.apply_verified_event({"channel": "wechat", "mode": "live", "event_id": "live-fixture-event",
        "order_id": order["id"], "provider_transaction_id": "live-fixture-trade", "status": "paid", "amount_minor": 1,
        "currency": "CNY", "merchant_id": "fixture-merchant", "app_id": "fixture-app",
        "occurred_at": datetime.now(timezone.utc).isoformat(), "raw_digest": "a" * 64})


@pytest.mark.asyncio
async def test_one_unknown_cost_keeps_its_hold_but_allows_an_independent_request(db):
    ledger, scope, _, _, plan = await prepare_wallet(db)
    unknown = await pending(ledger, scope, "unknown")
    before = await original_hold(db, unknown["id"])
    fresh = await reserve(ledger, scope, "fresh")
    assert fresh["id"] != unknown["id"] and fresh["status"] == "reserved"
    assert await original_hold(db, unknown["id"]) == before
    summary = await ledger.summary(scope)
    assert (summary["active_requests"], summary["pending_reconciliation"], summary["pending_reconciliation_limit"]) == (1, 1, 3)
    assert (summary["reserved"], summary["spent"], summary["available"]) == ("20", "0", "99980")
    async with db.connection() as c:
        assert (await (await c.execute("SELECT definition FROM plans WHERE id=%s", (plan["id"],))).fetchone())["definition"] == plan
    await assert_conservation(db)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["reserved", "dispatched"])
async def test_actual_active_requests_still_consume_the_original_one_slot(db, status):
    ledger, scope, *_ = await prepare_wallet(db)
    active = await reserve(ledger, scope, "active")
    if status == "dispatched":
        await ledger.dispatch(scope, active["id"])
    before = await snapshot(db)
    with pytest.raises(PlatformError) as denied:
        await reserve(ledger, replace(scope, product_id="second", session_id="another-session"), "independent")
    assert (denied.value.status, denied.value.code) == (409, "concurrency_limit")
    assert await snapshot(db) == before
    summary = await ledger.summary(scope)
    assert (summary["active_requests"], summary["pending_reconciliation"]) == (1, 0)
    await assert_conservation(db)


@pytest.mark.asyncio
@pytest.mark.parametrize("product", ["easel", "second"])
async def test_three_unknown_costs_block_new_requests_across_product_model_and_session(db, product):
    ledger, scope, *_ = await prepare_wallet(db, concurrency=10)
    for i in range(3):
        target = replace(scope, product_id="second" if i % 2 else "easel", session_id=f"old-session-{i}")
        await pending(ledger, target, f"unknown-{i}", operation=f"model-{i}", rate=f"rate-{i}")
    before = await snapshot(db)
    with pytest.raises(PlatformError) as denied:
        await reserve(ledger, replace(scope, product_id=product, session_id="fresh-session"), "fresh", operation="different-model", rate="different-rate")
    assert (denied.value.status, denied.value.code) == (409, "pending_reconciliation_limit")
    assert await snapshot(db) == before
    summary = await ledger.summary(scope)
    assert (summary["active_requests"], summary["pending_reconciliation"], summary["pending_reconciliation_limit"]) == (0, 3, 3)
    await assert_conservation(db)


@pytest.mark.asyncio
@pytest.mark.parametrize("funding_mode", ["test", "live"])
async def test_unknown_limit_counts_both_funding_modes_of_the_shared_wallet(db, funding_mode):
    ledger, scope, membership, _, plan = await prepare_wallet(db, concurrency=10)
    await add_synthetic_live_credit(db, membership, scope, plan)
    await pending(ledger, scope, "test-1")
    await pending(ledger, replace(scope, product_id="second"), "live-1", funding_mode="live")
    await pending(ledger, scope, "test-2")
    before = await snapshot(db)
    with pytest.raises(PlatformError) as denied:
        await reserve(ledger, scope, "new-mode", funding_mode=funding_mode)
    assert (denied.value.status, denied.value.code) == (409, "pending_reconciliation_limit")
    assert await snapshot(db) == before
    assert (await ledger.summary(scope))["pending_reconciliation"] == 3
    await assert_conservation(db)


@pytest.mark.asyncio
@pytest.mark.parametrize("active_mode", ["test", "live"])
async def test_active_slot_cannot_be_bypassed_by_switching_funding_mode(db, active_mode):
    ledger, scope, membership, _, plan = await prepare_wallet(db)
    await add_synthetic_live_credit(db, membership, scope, plan)
    row = await reserve(ledger, scope, "active", funding_mode=active_mode)
    await ledger.dispatch(scope, row["id"])
    before = await snapshot(db)
    with pytest.raises(PlatformError) as denied:
        await reserve(ledger, replace(scope, product_id="second", session_id="other-session"), "other-mode",
                      funding_mode="live" if active_mode == "test" else "test", operation="other-model", rate="other-rate")
    assert (denied.value.status, denied.value.code) == (409, "concurrency_limit")
    assert await snapshot(db) == before
    summary = await ledger.summary(scope)
    assert (summary["active_requests"], summary["pending_reconciliation"]) == (1, 0)
    await assert_conservation(db)


@pytest.mark.asyncio
async def test_pending_limit_isolated_between_users_wallets_but_shared_by_wallet_members(db):
    ledger, scope, membership, operator, plan = await prepare_wallet(db, concurrency=10)
    for i in range(3):
        await pending(ledger, scope, f"unknown-{i}")
    await membership.issue_test(operator, "foreign-wallet", plan["id"], "foreign-grant")
    foreign = replace(scope, user_id="other", tenant_id="foreign", billing_account_id="foreign-wallet", session_id="foreign-session")
    fresh = await reserve(ledger, foreign, "foreign-fresh")
    assert fresh["status"] == "reserved"
    assert (await ledger.summary(foreign))["active_requests"] == 1
    assert (await ledger.summary(foreign))["pending_reconciliation"] == 0
    async with db.connection() as c:
        await c.execute("INSERT INTO tenant_members(tenant_id,user_id,role) VALUES ('t','other','member')")
    with pytest.raises(PlatformError) as denied:
        await reserve(ledger, replace(scope, user_id="other", session_id="member-session"), "member-fresh")
    assert denied.value.code == "pending_reconciliation_limit"
    assert (await ledger.summary(scope))["pending_reconciliation"] == 3
    await assert_conservation(db)


@pytest.mark.asyncio
async def test_old_reservation_replay_precedes_both_limits_and_does_not_write(db):
    ledger, scope, *_ = await prepare_wallet(db, concurrency=10)
    original = await pending(ledger, scope, "unknown-0")
    await pending(ledger, scope, "unknown-1")
    await pending(ledger, scope, "unknown-2")
    before = await snapshot(db)
    replay = await reserve(ledger, scope, "unknown-0")
    assert replay == original
    with pytest.raises(PlatformError) as conflict:
        await reserve(ledger, scope, "unknown-0", amount=11)
    assert conflict.value.code == "idempotency_conflict"
    assert await snapshot(db) == before
    await assert_conservation(db)


@pytest.mark.asyncio
async def test_unknown_hold_cannot_be_reused_as_available_credit(db):
    ledger, scope, *_ = await prepare_wallet(db, credits="100")
    unknown = await pending(ledger, scope, "unknown", amount=80)
    before = await snapshot(db)
    with pytest.raises(PlatformError) as denied:
        await reserve(ledger, scope, "over-remaining", amount=21)
    assert denied.value.code == "insufficient_credits"
    assert await snapshot(db) == before
    await reserve(ledger, scope, "remaining", amount=20)
    summary = await ledger.summary(scope)
    assert (summary["available"], summary["reserved"], summary["spent"]) == ("0", "100", "0")
    assert (await original_hold(db, unknown["id"]))["reservation"]["value"]["actual"] is None
    await assert_conservation(db)


@pytest.mark.asyncio
async def test_execution_admit_replay_stays_idempotent_at_the_pending_limit(db):
    from test_execution_core import setup, admit

    service, ledger, scope, context = await setup(db)
    jobs = []
    for i in range(3):
        job = await admit(service, context, f"op-{i}")
        await service.dispatch(job["id"], job["attempt_id"])
        await service.pending(job["id"], job["attempt_id"], "upstream_timeout")
        jobs.append(job)
    before = await snapshot(db)
    replay = await admit(service, context, "op-0")
    assert replay["replay"] is True and replay["id"] == jobs[0]["id"]
    with pytest.raises(PlatformError) as dispatched:
        await service.dispatch(replay["id"], replay["attempt_id"])
    assert dispatched.value.code == "execution_already_dispatched"
    with pytest.raises(PlatformError) as denied:
        await admit(service, context, "new-op")
    assert (denied.value.status, denied.value.code) == (409, "pending_reconciliation_limit")
    assert await snapshot(db) == before
    async with db.connection() as c:
        row = await (await c.execute("SELECT count(*) AS jobs, count(*) FILTER(WHERE actual IS NOT NULL) AS priced FROM execution_jobs")).fetchone()
        invocation = await (await c.execute("SELECT operation_count,held,spent FROM execution_invocations WHERE id=%s", (context["invocation_id"],))).fetchone()
        assert row == {"jobs": 3, "priced": 0}
        assert invocation == {"operation_count": 3, "held": sum(job["amount"] for job in jobs), "spent": 0}
        assert not await (await c.execute("SELECT * FROM execution_receipts")).fetchall()
    await assert_conservation(db)


@pytest.mark.asyncio
async def test_two_real_database_pools_compete_for_one_slot_after_a_pending_cost(db, test_dsn):
    ledger, scope, *_ = await prepare_wallet(db)
    unknown = await pending(ledger, scope, "unknown")
    preserved = await original_hold(db, unknown["id"])
    pools = [Database(test_dsn, schema=db.schema, min_size=1, max_size=1) for _ in range(2)]
    tasks = []
    try:
        for pool in pools:
            await pool.open()
        pids = []
        for pool in pools:
            async with pool.connection() as c:
                pids.append((await (await c.execute("SELECT pg_backend_pid() AS pid")).fetchone())["pid"])
        assert pids[0] != pids[1]
        async with db.connection() as blocker:
            await blocker.execute("SELECT id FROM billing_accounts WHERE id='wallet' FOR UPDATE")
            tasks = [asyncio.create_task(reserve(CreditLedger(pool), replace(scope, product_id="easel" if i == 0 else "second", session_id=f"competing-{i}"), f"new-{i}")) for i, pool in enumerate(pools)]
            # Prove both independent PostgreSQL backends actually wait on the wallet.
            async with asyncio.timeout(5):
                while True:
                    async with db.connection() as observer:
                        blocking = [(await (await observer.execute("SELECT pg_blocking_pids(%s) AS pids", (pid,))).fetchone())["pids"] for pid in pids]
                    if all(blocking):
                        break
                    await asyncio.sleep(.01)
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert sum(isinstance(value, dict) for value in results) == 1
        denied = next(value for value in results if isinstance(value, PlatformError))
        assert (denied.status, denied.code) == (409, "concurrency_limit")
        summary = await ledger.summary(scope)
        assert (summary["active_requests"], summary["pending_reconciliation"], summary["reserved"]) == (1, 1, "20")
        assert await original_hold(db, unknown["id"]) == preserved
        await assert_conservation(db)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for pool in pools:
            await pool.close()
