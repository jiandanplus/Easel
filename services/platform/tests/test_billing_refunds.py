import asyncio
import pytest
from test_billing import paid, event, setup_billing, order_new
from test_credits import reserve
from platform_service.credits import CreditLedger
from platform_service.membership import MembershipService
from platform_service.errors import PlatformError


def refund_fact(fact, rid, **changes):
    return {
        **fact,
        "event_id": "refund-event",
        "status": "refunded",
        "refund_id": rid,
        "provider_refund_id": "provider-refund",
        "refund_amount_minor": 1,
        **changes,
    }


@pytest.mark.asyncio
async def test_refund_freezes_then_compensates_once(db):
    s, op, scope, order, fact = await paid(db)
    ledger = CreditLedger(db)
    refund = await s.request_refund(op, order["id"], idempotency_key="refund")
    assert refund == await s.request_refund(op, order["id"], idempotency_key="refund")
    summary = await ledger.summary(scope)
    assert (summary["available"], summary["frozen"]) == ("0", "100")
    with pytest.raises(PlatformError):
        await reserve(ledger, scope)
    rf = refund_fact(fact, refund["id"])
    await s.apply_verified_event(rf)
    await s.apply_verified_event(rf)
    await s.apply_verified_event(
        {**rf, "event_id": "late-failure", "status": "refund_failed"}
    )
    summary = await ledger.summary(scope)
    assert (summary["issued"], summary["refunded"], summary["available"]) == (
        "100",
        "100",
        "0",
    )
    assert (await MembershipService(db).entitlements(scope))["active"] is False
    assert (await s.get_refund(op, refund["id"]))["status"] == "succeeded"


@pytest.mark.asyncio
async def test_refund_timeout_remains_frozen(db):
    s, op, scope, order, fact = await paid(db)
    refund = await s.request_refund(op, order["id"], idempotency_key="refund")
    jobs = await s.claim_outbox(worker_id="worker")
    job = next(x for x in jobs if x["kind"] == "request_refund")
    await s.finish_outbox(job["id"], job["lease_token"], error="provider_timeout")
    assert (await s.get_refund(op, refund["id"]))["status"] == "pending"
    assert (await CreditLedger(db).summary(scope))["frozen"] == "100"


@pytest.mark.asyncio
async def test_unexpected_refund_with_hold_is_manual_and_release_stays_frozen(db):
    s, op, scope, order, fact = await paid(db)
    ledger = CreditLedger(db)
    r = await reserve(ledger, scope, amount=60)
    with pytest.raises(PlatformError):
        await s.request_refund(op, order["id"], idempotency_key="no")
    await s.apply_verified_event(refund_fact(fact, "external"))
    assert (await s.get_order(scope, order["id"]))["credit_state"] == "manual_review"
    await ledger.release(scope, r["id"], key="release")
    summary = await ledger.summary(scope)
    assert (summary["available"], summary["frozen"], summary["spent"]) == (
        "0",
        "100",
        "0",
    )


@pytest.mark.asyncio
async def test_refund_and_consume_race_has_one_winner(db):
    s, op, scope, order, fact = await paid(db)
    results = await asyncio.gather(
        s.request_refund(op, order["id"], idempotency_key="race"),
        reserve(CreditLedger(db), scope),
        return_exceptions=True,
    )
    assert sum(isinstance(r, dict) for r in results) == 1


@pytest.mark.asyncio
async def test_refund_observed_before_payment_never_issues(db):
    from test_billing import setup_billing, order_new

    s, _, scope, _ = await setup_billing(db)
    order = await order_new(s, scope)
    await s.apply_verified_event({**event(order), "status": "refund_observed"})
    await s.apply_verified_event({**event(order), "event_id": "late-paid"})
    assert (await CreditLedger(db).summary(scope))["issued"] == "0"


@pytest.mark.asyncio
async def test_partial_refund_is_manual_and_never_claims_full_success(db):
    # Verified partial refund is an observed external anomaly, not a local offer.
    from test_billing import setup_billing

    s, op, scope, offer = await setup_billing(db)
    await s.publish_offer(
        op, {**offer, "id": "two-cent", "version": "2", "amount_minor": "2"}
    )
    order = await s.create_order(
        scope, offer_id="two-cent", channel="wechat", idempotency_key="partial"
    )
    fact = {**event(order), "amount_minor": 2}
    await s.apply_verified_event(fact)
    await s.apply_verified_event(refund_fact(fact, "external", refund_amount_minor=1))
    detail = await s.get_order(scope, order["id"])
    assert detail["refund"]["status"] == "manual_reconciliation"
    assert detail["credit_state"] == "manual_review"
    summary = await CreditLedger(db).summary(scope)
    assert (summary["refunded"], summary["frozen"]) == ("0", "100")


@pytest.mark.asyncio
async def test_paid_old_closed_observation_does_not_revoke(db):
    s, op, scope, order, fact = await paid(db)
    await s.apply_verified_event(
        {**fact, "event_id": "closed-after-paid", "status": "closed"}
    )
    assert (await s.get_order(scope, order["id"]))["status"] == "paid"
    assert (await CreditLedger(db).summary(scope))["available"] == "100"
    assert (await MembershipService(db).entitlements(scope))["active"] is True


@pytest.mark.asyncio
async def test_alipay_trade_closed_after_paid_requires_refund_review(db):
    from test_billing import setup_billing, event
    from platform_service.billing import BillingService

    _, op, scope, _ = await setup_billing(db)
    s = BillingService(
        db, mode="test", channels=({"id": "alipay", "merchant_id": "m", "app_id": "a"},)
    )
    order = await s.create_order(
        scope, offer_id="offer", channel="alipay", idempotency_key="a"
    )
    fact = {**event(order), "channel": "alipay"}
    await s.apply_verified_event(fact)
    await s.apply_verified_event(
        {
            **fact,
            "event_id": "refund-close",
            "status": "closed",
            "raw_status": "TRADE_CLOSED",
        }
    )
    assert (await s.get_order(scope, order["id"]))["refund"][
        "status"
    ] == "manual_reconciliation"
    assert (await CreditLedger(db).summary(scope))["frozen"] == "100"


@pytest.mark.asyncio
async def test_spent_unexpected_refund_preserves_spending(db):
    s, op, scope, order, fact = await paid(db)
    ledger = CreditLedger(db)
    hold = await reserve(ledger, scope, amount=40)
    await ledger.dispatch(scope, hold["id"])
    await ledger.settle(scope, hold["id"], actual=30, key="spent")
    await s.apply_verified_event(refund_fact(fact, "spent-refund"))
    summary = await ledger.summary(scope)
    assert (summary["spent"], summary["frozen"], summary["available"]) == (
        "30",
        "70",
        "0",
    )


@pytest.mark.asyncio
async def test_verified_refund_failure_unfreezes_and_late_success_compensates(db):
    s, op, scope, order, fact = await paid(db)
    refund = await s.request_refund(op, order["id"], idempotency_key="refund")
    await s.apply_verified_event(
        refund_fact(fact, refund["id"], event_id="failed", status="refund_failed")
    )
    assert (await CreditLedger(db).summary(scope))["available"] == "100"
    await s.apply_verified_event(refund_fact(fact, refund["id"], event_id="success"))
    assert (await CreditLedger(db).summary(scope))["refunded"] == "100"


@pytest.mark.asyncio
async def test_refund_wrong_original_trade_never_compensates(db):
    s, op, scope, order, fact = await paid(db)
    await s.apply_verified_event(
        refund_fact(fact, "wrong-trade-refund", provider_transaction_id="another-trade")
    )
    summary = await CreditLedger(db).summary(scope)
    assert summary["refunded"] == "0"
    assert (await s.get_order(scope, order["id"]))["credit_state"] == "manual_review"


@pytest.mark.asyncio
async def test_provider_refund_transaction_cannot_refund_two_orders(db):
    from test_billing import order_new

    s, op, scope, first, fact = await paid(db)
    second = await order_new(s, scope, "second")
    second_fact = {
        **event(second),
        "event_id": "pay-second",
        "provider_transaction_id": "trade-second",
    }
    await s.apply_verified_event(second_fact)
    await s.apply_verified_event(refund_fact(fact, "refund-first"))
    await s.apply_verified_event(
        refund_fact(second_fact, "refund-second", event_id="refund-second-event")
    )
    assert (await CreditLedger(db).summary(scope))["refunded"] == "100"
    assert (await s.get_order(scope, second["id"]))["credit_state"] == "manual_review"


@pytest.mark.asyncio
async def test_late_refund_success_after_new_request_is_recorded_once(db):
    s, op, scope, order, fact = await paid(db)
    first = await s.request_refund(op, order["id"], idempotency_key="first")
    await s.apply_verified_event(
        refund_fact(fact, first["id"], event_id="failed-first", status="refund_failed")
    )
    second = await s.request_refund(op, order["id"], idempotency_key="second")
    await s.apply_verified_event(
        refund_fact(fact, first["id"], event_id="late-success")
    )
    assert (await CreditLedger(db).summary(scope))["refunded"] == "100"
    assert (await s.get_refund(op, first["id"]))["status"] == "succeeded"
    assert (await s.get_refund(op, second["id"]))["status"] == "manual_reconciliation"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "newer_status", ["pending", "manual_reconciliation", "succeeded"]
)
async def test_stale_failed_refund_preserves_newer_refund_protection(db, newer_status):
    s, op, scope, order, fact = await paid(db)
    first = await s.request_refund(op, order["id"], idempotency_key="first")
    failure = refund_fact(
        fact, first["id"], event_id="failed-first", status="refund_failed"
    )
    await s.apply_verified_event(failure)
    second = await s.request_refund(op, order["id"], idempotency_key="second")
    if newer_status == "manual_reconciliation":
        await s.apply_verified_event(
            {**fact, "event_id": "manual-observation", "status": "refund_observed"}
        )
        # A signed identity conflict makes the newer refund itself require review.
        await s.apply_verified_event(
            refund_fact(fact, "unexpected-refund", event_id="identity-conflict")
        )
    elif newer_status == "succeeded":
        await s.apply_verified_event(
            refund_fact(
                fact,
                second["id"],
                event_id="second-success",
                provider_refund_id="provider-second",
            )
        )
    before = await s.get_refund(op, first["id"])
    await s.apply_verified_event({**failure, "event_id": "late-failed-first"})
    assert await s.get_refund(op, first["id"]) == before
    assert (await s.get_refund(op, second["id"]))["status"] == newer_status
    summary = await CreditLedger(db).summary(scope)
    assert summary["available"] == "0"
    if newer_status != "succeeded":
        assert summary["frozen"] == "100"
    async with db.connection() as c:
        lot = await (await c.execute("SELECT frozen FROM credit_lots")).fetchone()
    assert lot["frozen"] is True
    with pytest.raises(PlatformError):
        await reserve(CreditLedger(db), scope)


@pytest.mark.asyncio
@pytest.mark.parametrize("resolution", ["refunded", "refund_failed"])
async def test_refund_pending_before_paid_grants_frozen_until_resolution(
    db, resolution
):
    s, op, scope, _ = await setup_billing(db)
    order = await order_new(s, scope)
    fact = event(order)
    pending = refund_fact(fact, "external", event_id="early", status="refund_pending")
    await s.apply_verified_event(pending)
    await s.apply_verified_event(fact)
    ledger = CreditLedger(db)
    summary = await ledger.summary(scope)
    assert (summary["issued"], summary["available"], summary["frozen"]) == (
        "100",
        "0",
        "100",
    )
    with pytest.raises(PlatformError):
        await reserve(ledger, scope)
    final = {**pending, "event_id": "resolution", "status": resolution}
    await s.apply_verified_event(final)
    # Independent late observations preserve final accounting and issuance count.
    await s.apply_verified_event({**fact, "event_id": "paid-again"})
    await s.apply_verified_event({**final, "event_id": "resolution-again"})
    await s.apply_verified_event({**pending, "event_id": "late-pending"})
    summary = await ledger.summary(scope)
    assert summary["issued"] == "100"
    if resolution == "refunded":
        assert (summary["available"], summary["refunded"]) == ("0", "100")
        assert (await MembershipService(db).entitlements(scope))["active"] is False
    else:
        assert (summary["available"], summary["refunded"], summary["frozen"]) == (
            "100",
            "0",
            "0",
        )
        await reserve(ledger, scope)


@pytest.mark.asyncio
@pytest.mark.parametrize("resolution", ["refunded", "refund_failed"])
async def test_refund_resolved_before_paid_preserves_facts_and_late_success(
    db, resolution
):
    s, op, scope, _ = await setup_billing(db)
    order = await order_new(s, scope)
    fact = event(order)
    pending = refund_fact(fact, "external", event_id="early", status="refund_pending")
    await s.apply_verified_event(pending)
    await s.apply_verified_event(
        {**pending, "event_id": "resolved", "status": resolution}
    )
    await s.apply_verified_event(fact)
    if resolution == "refund_failed":
        assert (await CreditLedger(db).summary(scope))["available"] == "100"
        await s.apply_verified_event(
            {**pending, "event_id": "late-success", "status": "refunded"}
        )
        assert (await CreditLedger(db).summary(scope))["refunded"] == "100"
        assert (await s.get_refund(op, "external"))["status"] == "succeeded"
    else:
        # Success before issuance has no lot to compensate; retain manual facts.
        assert (await CreditLedger(db).summary(scope))["issued"] == "0"
        assert (await s.get_refund(op, "external"))["status"] == "manual_reconciliation"
    await s.apply_verified_event(
        {**pending, "event_id": "late-failure", "status": "refund_failed"}
    )
    await s.apply_verified_event({**fact, "event_id": "paid-again"})
    summary = await CreditLedger(db).summary(scope)
    assert summary["available"] == "0"
    assert summary["issued"] == ("100" if resolution == "refund_failed" else "0")
    async with db.connection() as c:
        facts = await (
            await c.execute("SELECT count(*) AS n FROM payment_events")
        ).fetchone()
        grants = await (
            await c.execute("SELECT count(*) AS n FROM memberships")
        ).fetchone()
    assert facts["n"] == (6 if resolution == "refund_failed" else 5)
    assert grants["n"] == (1 if resolution == "refund_failed" else 0)


@pytest.mark.asyncio
async def test_refund_failure_preserves_unresolved_order_accounting_freeze(db):
    s, op, scope, order, fact = await paid(db)
    refund = await s.request_refund(op, order["id"], idempotency_key="refund")
    await s.apply_verified_event(
        {**fact, "event_id": "extra-payment", "provider_transaction_id": "extra-trade"}
    )
    assert (await s.get_order(scope, order["id"]))[
        "status"
    ] == "reconciliation_required"
    await s.apply_verified_event(
        refund_fact(fact, refund["id"], event_id="failed", status="refund_failed")
    )
    summary = await CreditLedger(db).summary(scope)
    assert (summary["available"], summary["frozen"]) == ("0", "100")
    with pytest.raises(PlatformError):
        await reserve(CreditLedger(db), scope)
