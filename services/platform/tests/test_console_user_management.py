"""Console user management (migration 025, decision 0012) on a real PostgreSQL with the low Console role."""
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest

from platform_service.contracts import OperatorIdentity
from platform_service.credits import CreditLedger
from platform_service.errors import PlatformError
from platform_service.membership import MembershipService
from platform_service.permissions import CONSOLE_FUNCTIONS, assert_safe_runtime_role, grant_runtime_permissions
from test_console_auth import console  # noqa: F401 (fixture)
from test_console_owner_allowance import low_catalog
from test_console_permissions import console_role  # noqa: F401 (fixture)
from test_membership import seed

PUBLISHER = OperatorIdentity('fixture-publisher', frozenset({'plans:publish', 'billing:publish'}))


def live_plan(id, credits='50000000', days=30):
    return dict(id=id, name='试用 ' + id, version='1', included_products=['easel'], features=['text'],
                credits=credits, days=days, concurrency=1, test_only=False)


async def epoch(db, user='u'):
    async with db.connection() as c:
        return await (await c.execute('SELECT active,authorization_epoch FROM users WHERE id=%s', (user,))).fetchone()


@pytest.mark.asyncio
async def test_operator_disables_enables_and_signs_out_with_audit(db, console, console_role):
    await seed(db)
    async with db.connection() as c:
        await c.execute("INSERT INTO product_clients(product_id,client_id) VALUES ('easel','desktop')")
        await c.execute("INSERT INTO external_identities(issuer,subject,user_id) VALUES ('fixture','subject','u')")
        await c.execute("""INSERT INTO identity_sessions(id,user_id,tenant_id,billing_account_id,product_id,client_id,
            authorization_epoch,issuer,subject,expires_at) VALUES ('s1','u','t','wallet','easel','desktop',0,'fixture','subject',now()+interval '1 day')""")
    catalog, queries, principal, _ = await low_catalog(console, console_role, 'operator')
    before = await epoch(db)
    assert await catalog.set_user_active(principal, 'u', False, '异常使用，先停用', 'disable-1') == {'id': 'u', 'active': False}
    after = await epoch(db)
    assert after['active'] is False and after['authorization_epoch'] == before['authorization_epoch'] + 1
    assert [r['id'] for r in (await queries.users(principal, status='disabled'))['items']] == ['u']
    assert (await catalog.set_user_active(principal, 'u', True, '核实后恢复', 'enable-1'))['active'] is True
    out = await catalog.force_logout(principal, 'u', '换设备，统一下线', 'logout-1')
    assert out == {'id': 'u', 'sessions_ended': 1}
    assert (await epoch(db))['authorization_epoch'] == before['authorization_epoch'] + 2
    with pytest.raises(PlatformError) as error:
        await catalog.set_user_active(principal, 'missing', False, 'x', 'disable-missing')
    assert error.value.status == 404
    actions = [(a['action'], a['target_id']) for a in (await queries.user_audits(principal, 'u'))['items']]
    assert sorted(actions) == [('user.logout', 'u'), ('user.status', 'u'), ('user.status', 'u')]


@pytest.mark.asyncio
async def test_force_logout_revokes_live_sessions(db, console, console_role):
    await seed(db)
    async with db.connection() as c:
        await c.execute("INSERT INTO product_clients(product_id,client_id) VALUES ('easel','desktop')")
        await c.execute("INSERT INTO external_identities(issuer,subject,user_id) VALUES ('fixture','subject','u')")
        await c.execute("""INSERT INTO identity_sessions(id,user_id,tenant_id,billing_account_id,product_id,client_id,
            authorization_epoch,issuer,subject,expires_at) VALUES ('s1','u','t','wallet','easel','desktop',0,'fixture','subject',now()+interval '1 day')""")
    catalog, queries, principal, _ = await low_catalog(console, console_role, 'admin')
    assert (await catalog.force_logout(principal, 'u', '换设备', 'logout-2'))['sessions_ended'] == 1
    assert [s['active'] for s in (await queries.user_sessions(principal, 'u'))['items']] == [False]


@pytest.mark.asyncio
async def test_notes_and_tags_are_searchable_and_validated(db, console, console_role):
    await seed(db)
    catalog, queries, principal, _ = await low_catalog(console, console_role, 'operator')
    saved = await catalog.set_user_note(principal, 'u', '老朋友，\n先给文本额度', ['朋友', '内测'], '更新备注', 'note-1')
    assert saved['tags'] == ['朋友', '内测']
    assert (await queries.user(principal, 'u'))['note']['note'] == '老朋友，\n先给文本额度'
    assert [r['id'] for r in (await queries.users(principal, q='内测'))['items']] == ['u']
    assert (await queries.users(principal, q='u'))['items'][0]['tags'] in (['朋友', '内测'], None)
    for bad in (['a,b'], ['x' * 21], ['dup', 'dup'], ['t'] * 11):
        with pytest.raises(PlatformError) as error:
            await catalog.set_user_note(principal, 'u', 'n', bad, '更新备注', 'note-bad')
        assert error.value.status == 422


@pytest.mark.asyncio
async def test_admin_issues_published_plan_within_limits(db, console, console_role):
    _, _, _, scope = await seed(db)
    members = MembershipService(db)
    await members.publish_plan(PUBLISHER, live_plan('friend-trial-v1'))
    await members.publish_plan(PUBLISHER, live_plan('too-big-v1', credits='200000000'))
    await members.publish_plan(PUBLISHER, live_plan('too-long-v1', days=400))
    catalog, queries, principal, _ = await low_catalog(console, console_role, 'admin')
    first = await catalog.issue_plan(principal, 'wallet', 'friend-trial-v1', '朋友试用', 'plan-1')
    assert first['credits'] == '50000000' and first['test_only'] is False
    assert await catalog.issue_plan(principal, 'wallet', 'friend-trial-v1', '朋友试用', 'plan-1') == first
    assert (await CreditLedger(db).summary(scope))['live_available'] == '50000000'
    cases = [('friend-trial-v1', 'plan-2', 409, 'plan_grant_active'), ('too-big-v1', 'plan-3', 409, 'plan_above_console_limit'),
             ('too-long-v1', 'plan-4', 409, 'plan_above_console_limit'), ('easel-owner-allowance-v1', 'plan-5', 404, 'plan_not_found'),
             ('missing-v1', 'plan-6', 404, 'plan_not_found')]
    for plan, key, status, code in cases:
        with pytest.raises(PlatformError) as error:
            await catalog.issue_plan(principal, 'wallet', plan, '朋友试用', key)
        assert (error.value.status, error.value.code) == (status, code)
    async with db.connection() as c:
        grant = await (await c.execute('SELECT plan_id,operator_id,credits FROM console_plan_grants')).fetchone()
    assert grant == {'plan_id': 'friend-trial-v1', 'operator_id': 'console:' + principal.id, 'credits': 50000000}
    assert [a['action'] for a in (await queries.audits(principal))['items']] == ['plan.issue']


@pytest.mark.asyncio
async def test_operators_cannot_issue_plans_or_call_the_function(db, console, console_role):
    await seed(db)
    await MembershipService(db).publish_plan(PUBLISHER, live_plan('friend-trial-v1'))
    catalog, _, principal, low = await low_catalog(console, console_role, 'operator')
    with pytest.raises(PlatformError) as error:
        await catalog.issue_plan(principal, 'wallet', 'friend-trial-v1', '朋友试用', 'plan-op')
    assert error.value.status == 403
    with pytest.raises(psycopg.Error) as raw:
        async with low.connection() as c:
            await c.execute('SELECT console_issue_plan(%s,%s,%s,%s,%s)', (principal.id, 'wallet', 'friend-trial-v1', 'a' * 64, 'r'))
    assert raw.value.sqlstate == 'EA403'


@pytest.mark.asyncio
@pytest.mark.parametrize('function', CONSOLE_FUNCTIONS)
async def test_product_runtime_cannot_reach_console_functions(db, function):
    role = 'product_console_fn_' + uuid4().hex
    async with db.connection() as c:
        await c.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
    try:
        async with db.connection() as c:
            await grant_runtime_permissions(c, role)
            assert (await (await c.execute("SELECT has_function_privilege(%s,%s,'EXECUTE') AS ok", (role, function))).fetchone())['ok'] is False
            await c.execute(sql.SQL('GRANT EXECUTE ON FUNCTION {} TO {}').format(sql.SQL(function), sql.Identifier(role)))
        with pytest.raises(PlatformError):
            async with db.connection() as c:
                await assert_safe_runtime_role(c, role)
    finally:
        async with db.connection() as c:
            await c.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
            await c.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
