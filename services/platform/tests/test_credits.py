import asyncio
from dataclasses import replace
import pytest
from test_membership import seed


def test_credit_contract_exists():
    from platform_service.credits import CreditLedger, price_microcredits

    assert callable(CreditLedger)
    assert price_microcredits(3, 2, 4) == 2


async def wallet(db, credits="100"):
    from platform_service.credits import CreditLedger

    service, operator, plan, scope = await seed(db, credits)
    await service.issue_test(operator, "wallet", plan["id"], "grant")
    return CreditLedger(db), scope


async def reserve(ledger, scope, key="one", amount=80):
    return await ledger.reserve(
        scope,
        operation="fixture",
        key=key,
        body_hash="hash",
        amount=amount,
        rate_version="fixture-v1",
    )


@pytest.mark.asyncio
async def test_shared_wallet_cannot_overspend_across_independent_connections(
    db, test_dsn
):
    ledger, scope = await wallet(db)
    from platform_service.db import Database
    from platform_service.credits import CreditLedger

    # Separate pool, separately acquired PostgreSQL backend, same schema/wallet.
    other = Database(test_dsn, schema=db.schema, min_size=1, max_size=1)
    await other.open()
    try:
        async with db.connection() as a, other.connection() as b:
            pa = await (await a.execute("SELECT pg_backend_pid() AS pid")).fetchone()
            pb = await (await b.execute("SELECT pg_backend_pid() AS pid")).fetchone()
            assert pa["pid"] != pb["pid"]
        results = await asyncio.gather(
            reserve(ledger, scope),
            reserve(CreditLedger(other), replace(scope, product_id="second")),
            return_exceptions=True,
        )
        assert sum(isinstance(result, dict) for result in results) == 1
        assert (await ledger.summary(scope))["available"] == "20"
    finally:
        await other.close()


@pytest.mark.asyncio
async def test_reserve_idempotency_and_settlement_no_double_charge(db):
    from platform_service.errors import PlatformError

    ledger, scope = await wallet(db)
    r = await reserve(ledger, scope)
    assert r == await reserve(ledger, scope)
    with pytest.raises(PlatformError) as e:
        await reserve(ledger, scope, amount=81)
    assert e.value.status == 409
    await ledger.dispatch(scope, r["id"])
    settled = await ledger.settle(scope, r["id"], actual=30, key="settle")
    assert settled == await ledger.settle(scope, r["id"], actual=30, key="settle")
    with pytest.raises(PlatformError):
        await ledger.settle(scope, r["id"], actual=31, key="settle")
    s = await ledger.summary(scope)
    assert (s["available"], s["reserved"], s["spent"]) == ("70", "0", "30")
    assert (await ledger.usage(scope))[0]["actual"] == "30"
    with pytest.raises(PlatformError):
        await ledger.release(scope, r["id"], key="release")


@pytest.mark.asyncio
async def test_pending_unknown_cost_cannot_release_and_over_budget_preserves_hold(db):
    from platform_service.errors import PlatformError

    ledger, scope = await wallet(db)
    r = await reserve(ledger, scope)
    await ledger.dispatch(scope, r["id"])
    with pytest.raises(PlatformError):
        await ledger.settle(scope, r["id"], actual=81, key="over")
    assert (await ledger.summary(scope))["pending_reconciliation"] == 1
    with pytest.raises(PlatformError):
        await ledger.release(scope, r["id"], key="unknown")
    assert (await ledger.pending(scope, r["id"]))["status"] == "pending_reconciliation"
    assert (await ledger.summary(scope))["reserved"] == "80"
    await ledger.settle(scope, r["id"], actual=50, key="reconcile")
    assert (await ledger.summary(scope))["spent"] == "50"


@pytest.mark.asyncio
async def test_unstarted_release_and_expired_hold_return_to_original_lot(db):
    ledger, scope = await wallet(db)
    r = await reserve(ledger, scope)
    async with db.connection() as c:
        await c.execute(
            "UPDATE credit_lots SET expires_at=clock_timestamp()-interval '1 second'"
        )
    s = await ledger.summary(scope)
    assert (s["expired"], s["reserved"], s["available"]) == ("20", "80", "0")
    released = await ledger.release(scope, r["id"], key="cancel")
    assert released == await ledger.release(scope, r["id"], key="cancel")
    s = await ledger.summary(scope)
    assert (s["expired"], s["reserved"]) == ("100", "0")


@pytest.mark.asyncio
async def test_expired_hold_can_settle_and_remainder_expires(db):
    ledger, scope = await wallet(db)
    r = await reserve(ledger, scope)
    await ledger.dispatch(scope, r["id"])
    async with db.connection() as c:
        await c.execute(
            "UPDATE credit_lots SET expires_at=clock_timestamp()-interval '1 second'"
        )
    await ledger.settle(scope, r["id"], actual=30, key="settle")
    s = await ledger.summary(scope)
    assert (s["expired"], s["spent"], s["reserved"]) == ("70", "30", "0")


@pytest.mark.asyncio
async def test_reservation_scope_is_bound_to_user_product_and_wallet(db):
    from platform_service.errors import PlatformError

    ledger, scope = await wallet(db)
    r = await reserve(ledger, scope)
    for other in [
        replace(scope, product_id="second"),
        replace(scope, billing_account_id="foreign-wallet"),
        replace(scope, user_id="other"),
    ]:
        with pytest.raises(PlatformError):
            await ledger.dispatch(other, r["id"])


@pytest.mark.asyncio
async def test_postgres_rejects_unbalanced_and_mutated_journal(db):
    import psycopg

    ledger, scope = await wallet(db)
    with pytest.raises(psycopg.Error):
        async with db.connection() as c:
            await c.execute(
                "INSERT INTO credit_journals(id,billing_account_id,kind,source) VALUES ('unbalanced','wallet','issue','bad')"
            )
            await c.execute(
                "INSERT INTO credit_entries(journal_id,billing_account_id,bucket,delta) VALUES ('unbalanced','wallet','available',1)"
            )
    for sql in [
        "UPDATE credit_entries SET delta=delta+1",
        "DELETE FROM credit_entries",
        "UPDATE credit_journals SET source='tampered'",
        "DELETE FROM credit_journals",
    ]:
        with pytest.raises(psycopg.Error):
            async with db.connection() as c:
                await c.execute(sql)
    with pytest.raises(psycopg.Error):
        async with db.connection() as c:
            await c.execute("UPDATE credit_lots SET available=-1")


@pytest.mark.parametrize("value", [True, -1, 1.5, "1", 2**63])
@pytest.mark.asyncio
async def test_reserve_rejects_non_integer_or_out_of_range_amount(db, value):
    from platform_service.errors import PlatformError

    ledger, scope = await wallet(db)
    with pytest.raises(PlatformError):
        await reserve(ledger, scope, amount=value)


def test_pricing_rounds_only_once_and_rejects_invalid_inputs():
    from platform_service.credits import price_microcredits
    from platform_service.errors import PlatformError

    assert price_microcredits(5, 3, 2) == 8
    assert price_microcredits(0, 3, 2) == 0
    for args in [(True, 1, 1), (1, 1, 0), (-1, 1, 1), (2**62, 4, 1)]:
        with pytest.raises(PlatformError):
            price_microcredits(*args)


@pytest.mark.asyncio
async def test_database_rejects_lot_or_journal_balance_divergence(db):
    import psycopg

    ledger, scope = await wallet(db)
    with pytest.raises(psycopg.Error):
        async with db.connection() as c:
            await c.execute(
                "UPDATE credit_lots SET available=available-1,spent=spent+1"
            )
    with pytest.raises(psycopg.Error):
        async with db.connection() as c:
            await c.execute(
                "INSERT INTO credit_journals(id,billing_account_id,kind,source) VALUES ('fabricated','wallet','settle','fabricated')"
            )
            await c.execute(
                "INSERT INTO credit_entries(journal_id,billing_account_id,bucket,delta) VALUES ('fabricated','wallet','available',-1),('fabricated','wallet','spent',1)"
            )
    assert (await ledger.summary(scope))["available"] == "100"


@pytest.mark.asyncio
async def test_committed_journal_cannot_be_extended(db):
    import psycopg

    await wallet(db)
    with pytest.raises(psycopg.Error):
        async with db.connection() as c:
            row = await (
                await c.execute("SELECT id FROM credit_journals LIMIT 1")
            ).fetchone()
            await c.execute(
                "INSERT INTO credit_entries(journal_id,billing_account_id,bucket,delta) VALUES (%s,'wallet','available',1),(%s,'wallet','available',-1)",
                (row["id"], row["id"]),
            )


@pytest.mark.asyncio
async def test_concurrent_settlement_and_release_are_exactly_once(db):
    ledger, scope = await wallet(db)
    r = await reserve(ledger, scope)
    await ledger.dispatch(scope, r["id"])
    results = await asyncio.gather(
        *[ledger.settle(scope, r["id"], actual=30, key="same") for _ in range(4)]
    )
    assert all(result == results[0] for result in results)
    summary = await ledger.summary(scope)
    assert (summary["available"], summary["spent"]) == ("70", "30")
    r = await reserve(ledger, scope, key="cancel", amount=50)
    results = await asyncio.gather(
        *[ledger.release(scope, r["id"], key="cancel") for _ in range(4)]
    )
    assert all(result == results[0] for result in results)
    assert (await ledger.summary(scope))["available"] == "70"
