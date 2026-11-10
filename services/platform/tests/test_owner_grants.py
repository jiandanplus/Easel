"""Real PostgreSQL checks for the owner allowance (migration 022)."""
import asyncio

import psycopg
import pytest

from platform_service.contracts import OperatorIdentity
from platform_service.credits import CreditLedger
from platform_service.errors import PlatformError
from platform_service.membership import MembershipService
from platform_service.owner_grants import CREDITS, OwnerGrantService, PLAN
from test_membership import seed

OWNER = OperatorIdentity('easel-host-owner', frozenset({'owner:grant'}))
ARGS = dict(wallet='wallet', key='restore-1', reason='Payments are off; owner restores access.',
            approval_reference='explicit-user-approval-fixture')


async def issue(db, **changes):
    return await OwnerGrantService(db).issue(OWNER, **{**ARGS, **changes})


@pytest.mark.asyncio
async def test_grant_opens_text_and_image_with_live_credit_and_replays(db):
    _, _, _, scope = await seed(db)
    first = await issue(db)
    assert first['credits'] == str(CREDITS) == '100000000' and first['test_only'] is False
    assert await issue(db) == first
    wallet = await CreditLedger(db).summary(scope)
    assert wallet['live_available'] == str(CREDITS) and wallet['experience_available'] == '0'
    rights = await MembershipService(db).entitlements(scope)
    assert rights['active'] and rights['test_only'] is False
    assert rights['features'] == ['image', 'text'] and rights['concurrency'] == 1
    async with db.connection() as c:
        lots = await (await c.execute('SELECT issued,available,test_only,expires_at-created_at>interval \'364 days\' long FROM credit_lots')).fetchall()
        assert lots == [{'issued': CREDITS, 'available': CREDITS, 'test_only': False, 'long': True}]
        entries = await (await c.execute("SELECT e.bucket,e.delta FROM credit_entries e JOIN credit_journals j ON j.id=e.journal_id WHERE j.kind='issue' ORDER BY e.bucket")).fetchall()
        assert entries == [{'bucket': 'available', 'delta': CREDITS}, {'bucket': 'issuance', 'delta': -CREDITS}]


@pytest.mark.asyncio
async def test_needs_capability_known_wallet_and_stable_parameters(db):
    await seed(db)
    for caps in [frozenset(), frozenset({'test:issue'}), frozenset({'execution:writeoff'}), frozenset({'internal:issue'})]:
        with pytest.raises(PlatformError) as error:
            await OwnerGrantService(db).issue(OperatorIdentity('wrong', caps), **ARGS)
        assert error.value.status == 403
    with pytest.raises(PlatformError) as error:
        await issue(db, wallet='missing')
    assert error.value.status == 404
    with pytest.raises(PlatformError) as error:
        await issue(db, reason='bad\nreason')
    assert error.value.code == 'invalid_owner_grant_parameter'
    await issue(db)
    with pytest.raises(PlatformError) as error:
        await issue(db, reason='Another decision')
    assert error.value.code == 'idempotency_conflict'
    with pytest.raises(PlatformError) as error:
        await issue(db, key='restore-2')
    assert error.value.code == 'owner_grant_active'


@pytest.mark.asyncio
async def test_concurrent_requests_issue_once(db):
    _, _, _, scope = await seed(db)
    results = await asyncio.gather(*[issue(db) for _ in range(4)])
    assert all(result == results[0] for result in results)
    assert (await CreditLedger(db).summary(scope))['live_available'] == str(CREDITS)


@pytest.mark.asyncio
async def test_database_rejects_unaudited_or_changed_allowance(db):
    await seed(db)
    await issue(db)
    for statement in ["UPDATE owner_grants SET reason='changed'", 'DELETE FROM owner_grants', 'TRUNCATE owner_grants']:
        with pytest.raises(psycopg.errors.Error):
            async with db.connection() as c:
                await c.execute(statement)
    with pytest.raises(psycopg.errors.Error) as error:
        async with db.connection() as c:
            await c.execute("""INSERT INTO memberships(id,billing_account_id,plan_id,operator_id,issuance_key,starts_at,expires_at)
                VALUES ('forged','foreign-wallet',%s,'forger','forged-key',now(),now()+interval '1 day')""", (PLAN['id'],))
    assert 'missing owner grant audit' in str(error.value)
