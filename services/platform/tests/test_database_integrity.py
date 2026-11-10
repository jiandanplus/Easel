import pytest
import psycopg
from test_credits import wallet, reserve


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "table",
    ["credit_entries", "credit_journals", "plans", "memberships", "credit_holds"],
)
async def test_history_truncate_is_guarded_even_for_migration_owner(db, table):
    from psycopg import sql

    await wallet(db)
    with pytest.raises(psycopg.Error):
        async with db.connection() as c:
            await c.execute(
                sql.SQL("TRUNCATE {} CASCADE").format(sql.Identifier(table))
            )


@pytest.mark.asyncio
async def test_terminal_corruption_rolls_back_and_legitimate_settlement_remains_possible(
    db,
):
    ledger, scope = await wallet(db)
    r = await reserve(ledger, scope)
    with pytest.raises(psycopg.Error):
        async with db.connection() as c:
            await c.execute(
                "UPDATE credit_reservations SET status='settled',actual=0,completion_key='corrupt' WHERE id=%s",
                (r["id"],),
            )
    assert (await ledger.summary(scope))["reserved"] == "80"
    await ledger.dispatch(scope, r["id"])
    await ledger.settle(scope, r["id"], actual=30, key="legitimate")
    assert (await ledger.summary(scope))["spent"] == "30"


@pytest.mark.asyncio
async def test_committed_hold_cannot_be_extended(db):
    from platform_service.membership import MembershipService
    from platform_service.contracts import OperatorIdentity

    ledger, scope = await wallet(db)
    r = await reserve(ledger, scope)
    await MembershipService(db).issue_test(
        OperatorIdentity("op", frozenset({"test:issue"})),
        "wallet",
        "fixture-plan-v1",
        "second",
    )
    with pytest.raises(psycopg.Error):
        async with db.connection() as c:
            await c.execute(
                """INSERT INTO credit_holds(reservation_id,lot_id,billing_account_id,amount)
            SELECT %s,id,'wallet',1 FROM credit_lots WHERE id NOT IN
            (SELECT lot_id FROM credit_holds WHERE reservation_id=%s) LIMIT 1""",
                (r["id"], r["id"]),
            )
    await ledger.release(scope, r["id"], key="legitimate")
    assert (await ledger.summary(scope))["available"] == "200"


@pytest.mark.asyncio
async def test_budget_bindings_and_terminal_rows_cannot_be_rewritten(db):
    ledger, scope = await wallet(db)
    r = await reserve(ledger, scope)
    for assignment in [
        "amount=79",
        "product_id='second'",
        "body_hash='changed'",
        "idempotency_key='changed'",
    ]:
        with pytest.raises(psycopg.Error):
            async with db.connection() as c:
                await c.execute(
                    "UPDATE credit_reservations SET " + assignment + " WHERE id=%s",
                    (r["id"],),
                )
    await ledger.release(scope, r["id"], key="legitimate")
    with pytest.raises(psycopg.Error):
        async with db.connection() as c:
            await c.execute(
                "UPDATE credit_reservations SET status='reserved',actual=NULL,completion_key=NULL WHERE id=%s",
                (r["id"],),
            )


@pytest.mark.asyncio
async def test_reservation_requires_full_hold_allocation_at_commit(db):
    await wallet(db)
    with pytest.raises(psycopg.Error):
        async with db.connection() as c:
            await c.execute("""INSERT INTO credit_reservations(id,billing_account_id,tenant_id,user_id,product_id,operation,idempotency_key,body_hash,rate_version,amount,status)
            VALUES ('fake','wallet','t','u','easel','fake','fake','fake','fake',1,'reserved')""")


@pytest.mark.asyncio
async def test_active_lot_holds_cannot_diverge_even_with_balanced_wallet_journal(db):
    ledger, scope = await wallet(db)
    r = await reserve(ledger, scope)
    with pytest.raises(psycopg.Error):
        async with db.connection() as c:
            await c.execute("UPDATE credit_lots SET held=held-1,expired=expired+1")
            await c.execute(
                "INSERT INTO credit_journals(id,billing_account_id,kind,source) VALUES ('corrupt-expiry','wallet','expire','corrupt')"
            )
            await c.execute(
                "INSERT INTO credit_entries(journal_id,billing_account_id,bucket,delta) VALUES ('corrupt-expiry','wallet','held',-1),('corrupt-expiry','wallet','expired',1)"
            )
    await ledger.release(scope, r["id"], key="legitimate")
    assert (await ledger.summary(scope))["available"] == "100"


@pytest.mark.asyncio
async def test_terminal_journal_must_match_declared_actual(db):
    ledger, scope = await wallet(db)
    r = await reserve(ledger, scope)
    await ledger.dispatch(scope, r["id"])
    with pytest.raises(psycopg.Error):
        async with db.connection() as c:
            await c.execute(
                "UPDATE credit_lots SET held=held-80,spent=spent+30,available=available+50"
            )
            await c.execute(
                "INSERT INTO credit_journals(id,billing_account_id,kind,source) VALUES ('corrupt-terminal','wallet','settle',%s)",
                (r["id"],),
            )
            await c.execute(
                "INSERT INTO credit_entries(journal_id,billing_account_id,bucket,delta) VALUES ('corrupt-terminal','wallet','held',-80),('corrupt-terminal','wallet','spent',30),('corrupt-terminal','wallet','available',50)"
            )
            await c.execute(
                "UPDATE credit_reservations SET status='settled',actual=0,completion_key='corrupt' WHERE id=%s",
                (r["id"],),
            )
    await ledger.settle(scope, r["id"], actual=30, key="legitimate")
    assert (await ledger.summary(scope))["spent"] == "30"
