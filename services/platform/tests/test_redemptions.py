"""Redemption codes and web operator approval (migration 026, decision 0013) on a real PostgreSQL."""
import hashlib
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest

from platform_service.contracts import OperatorIdentity, Scope
from platform_service.credits import CreditLedger
from platform_service.errors import PlatformError
from platform_service.membership import MembershipService
from platform_service.permissions import RUNTIME_FUNCTIONS, grant_runtime_permissions
from platform_service.redemptions import RedemptionService, code_hash, new_code, normalize
from test_console_auth import console  # noqa: F401 (fixture)
from test_console_owner_allowance import low_catalog
from test_console_permissions import console_role  # noqa: F401 (fixture)
from test_membership import seed

PUBLISHER = OperatorIdentity('fixture-publisher', frozenset({'plans:publish', 'billing:publish'}))
OWNER = Scope('u', 'easel', 't', 'wallet', 'session', 0)
OTHER = Scope('other', 'easel', 'foreign', 'foreign-wallet', 'session-2', 0)


async def published(db):
    await MembershipService(db).publish_plan(PUBLISHER, dict(id='gift-v1', name='礼品卡', version='1', included_products=['easel'],
        features=['text'], credits='30000000', days=30, concurrency=1, test_only=False))


def test_codes_are_long_unambiguous_and_tolerant_of_typing():
    code = new_code()
    assert len(code) == 19 and code.count('-') == 3
    assert normalize(code.lower().replace('-', ' ')) == code.replace('-', '')
    assert code_hash('O1IL-0000-0000-0000') == code_hash('0111-0000-0000-0000')
    for bad in ('short', 'U' * 16, 'x' * 70, None):
        with pytest.raises(PlatformError):
            normalize(bad)


@pytest.mark.asyncio
async def test_admin_creates_codes_once_and_users_redeem_them_once(db, console, console_role):
    await seed(db)
    await published(db)
    catalog, queries, principal, _ = await low_catalog(console, console_role, 'admin')
    batch = await catalog.create_redemptions(principal, 'gift-v1', '十一月朋友', 3, 30, '给朋友试用', 'batch-1')
    codes = batch['codes']
    assert len(codes) == 3 and batch['credits'] == '30000000'
    replay = await catalog.create_redemptions(principal, 'gift-v1', '十一月朋友', 3, 30, '给朋友试用', 'batch-1')
    assert 'codes' not in replay and replay['id'] == batch['id']
    async with db.connection() as c:
        stored = str(await (await c.execute('SELECT * FROM console_operations')).fetchall())
    assert all(code not in stored and code.replace('-', '') not in stored for code in codes)
    service = RedemptionService(db)
    first = await service.redeem(OWNER, codes[0].lower())
    assert first['credits'] == '30000000' and first['replayed'] is False
    assert (await service.redeem(OWNER, codes[0]))['replayed'] is True
    assert (await CreditLedger(db).summary(OWNER))['live_available'] == '30000000'
    for scope, code, expected in ((OTHER, codes[0], 'redemption_code_used'), (OWNER, codes[1], 'redemption_batch_already_used'),
                                  (OWNER, new_code(), 'redemption_code_invalid')):
        with pytest.raises(PlatformError) as error:
            await service.redeem(scope, code)
        assert error.value.code == expected
    await catalog.disable_redemptions(principal, batch['id'], '停止发放', 'batch-off')
    with pytest.raises(PlatformError) as error:
        await service.redeem(OTHER, codes[1])
    assert error.value.code == 'redemption_code_disabled'
    listed = (await queries.redemptions(principal))['items'][0]
    assert (listed['redeemed'], listed['state']) == (1, 'disabled')
    detail = await queries.redemption(principal, batch['id'])
    assert len(detail['codes']) == 3 and [c['user_id'] for c in detail['codes'] if c['redeemed_at']] == ['u']
    assert all(len(c['code_ref']) == 8 for c in detail['codes'])


@pytest.mark.asyncio
async def test_batches_follow_plan_limits_and_admin_only(db, console, console_role):
    await seed(db)
    await published(db)
    catalog, _, principal, _ = await low_catalog(console, console_role, 'admin')
    for plan, count, days, code in (('easel-owner-allowance-v1', 1, 30, 'plan_not_found'), ('gift-v1', 101, 30, 'invalid_console_command'),
                                    ('gift-v1', 1, 400, 'invalid_console_command')):
        with pytest.raises(PlatformError) as error:
            await catalog.create_redemptions(principal, plan, 'x', count, days, 'r', 'bad-' + str(count) + str(days))
        assert error.value.code == code


@pytest.mark.asyncio
async def test_operators_cannot_create_codes(db, console, console_role):
    await seed(db)
    await published(db)
    catalog, _, principal, low = await low_catalog(console, console_role, 'operator')
    with pytest.raises(PlatformError) as error:
        await catalog.create_redemptions(principal, 'gift-v1', 'x', 1, 30, 'r', 'op-batch')
    assert error.value.status == 403
    with pytest.raises(psycopg.Error) as raw:
        async with low.connection() as c:
            await c.execute('SELECT console_create_redemption_batch(%s,%s,%s,%s,%s,%s)', (principal.id, 'gift-v1', 'x', 'r', 30, ['a' * 64]))
    assert raw.value.sqlstate == 'EA403'


@pytest.mark.asyncio
async def test_only_runtime_may_redeem(db, console_role):
    low, console_name = console_role
    async with low.connection() as c:
        assert (await (await c.execute("SELECT has_function_privilege(current_user,'redeem_code(text,text,text)','EXECUTE') AS ok")).fetchone())['ok'] is False
    role = 'product_redeem_' + uuid4().hex
    async with db.connection() as c:
        await c.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
    try:
        async with db.connection() as c:
            await grant_runtime_permissions(c, role)
            assert (await (await c.execute("SELECT has_function_privilege(%s,%s,'EXECUTE') AS ok", (role, RUNTIME_FUNCTIONS[0]))).fetchone())['ok'] is True
    finally:
        async with db.connection() as c:
            await c.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
            await c.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
    from platform_service.console_permissions import assert_safe_console_role
    async with db.connection() as c:
        await c.execute(sql.SQL('GRANT EXECUTE ON FUNCTION redeem_code(text,text,text) TO {}').format(sql.Identifier(console_name)))
    with pytest.raises(PlatformError):
        async with low.connection() as c:
            await assert_safe_console_role(c)


@pytest.mark.asyncio
async def test_admin_approves_operators_on_the_web_but_never_admins(db, console, console_role):
    catalog, queries, principal, _ = await low_catalog(console, console_role, 'admin')
    auth, _ = console
    request_id = uuid4().hex
    async with db.connection() as c:
        await c.execute("""INSERT INTO console_login_requests(id,issuer,subject,subject_hash,expires_at)
            VALUES (%s,%s,'new-person',%s,now()+interval '1 day')""", (request_id, auth.settings.issuer, hashlib.sha256(b'new-person').hexdigest()))
    assert [r['id'] for r in (await queries.admins(principal))['requests']] == [request_id]
    with pytest.raises(PlatformError):
        await catalog.approve_admin(principal, request_id, 'admin', '加管理员', 'approve-admin')
    approved = await catalog.approve_admin(principal, request_id, 'operator', '新同事', 'approve-1')
    assert approved['role'] == 'operator'
    listing = await queries.admins(principal)
    assert listing['requests'] == [] and {a['role'] for a in listing['admins']} == {'admin', 'operator'}
    assert (await catalog.disable_admin(principal, approved['id'], '离职', 'disable-1'))['active'] is False
    for target in (principal.id,):
        with pytest.raises(PlatformError) as error:
            await catalog.disable_admin(principal, target, '误操作', 'disable-self')
        assert error.value.status == 403
    async with db.connection() as c:
        events = await (await c.execute('SELECT action,role,operator_id FROM console_acl_events WHERE admin_id=%s ORDER BY created_at', (approved['id'],))).fetchall()
    assert [(e['action'], e['role']) for e in events] == [('approve', 'operator'), ('disable', 'operator')]
    assert all(e['operator_id'] == 'console:' + principal.id for e in events)
