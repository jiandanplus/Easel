"""Console user administration (migration 038, decision 0020) on a real PostgreSQL with the low Console role."""
import json

import httpx
import pytest

from platform_service.credits import CreditLedger
from platform_service.errors import PlatformError
from platform_service.logto_admin import LogtoAdmin, clean_phone
from test_console_auth import console  # noqa: F401 (fixture)
from test_console_owner_allowance import low_catalog
from test_console_permissions import console_role  # noqa: F401 (fixture)
from test_membership import seed

PRO = dict(id='pro-v1', name='专业版', version='1', included_products=['easel'], features=['text', 'image'],
           credits='10000000000', days=30, concurrency=2, test_only=False)


async def person(db, user, subject, issuer):
    async with db.connection() as c:
        if not await (await c.execute('SELECT 1 FROM users WHERE id=%s', (user,))).fetchone():
            await c.execute('INSERT INTO users(id) VALUES (%s)', (user,))
        await c.execute('INSERT INTO external_identities(issuer,subject,user_id,username) VALUES (%s,%s,%s,%s)',
                        (issuer, subject, user, user + '_name'))


async def admin_subject(db):
    async with db.connection() as c:
        return (await (await c.execute("SELECT issuer,subject FROM console_admins WHERE role='admin'")).fetchone())


class FakeLogto:
    """Stands in for LogtoAdmin; records calls, never sees the network."""
    def __init__(self):
        self.calls, self.users = [], {}

    async def create_user(self, *, username, phone=None, email=None, password=None):
        self.calls.append(('create', username, phone, email, bool(password)))
        if any(u['username'] == username for u in self.users.values()):
            raise PlatformError(409, 'console_username_taken')
        subject = 'logto-' + username
        self.users[subject] = {'id': subject, 'username': username, 'primaryPhone': phone, 'primaryEmail': email}
        return self.users[subject]

    async def update_user(self, subject, *, username, phone, email):
        self.calls.append(('update', subject, username, phone, email))
        self.users.setdefault(subject, {}).update(username=username, primaryPhone=phone, primaryEmail=email)

    async def set_password(self, subject, password):
        self.calls.append(('password', subject, len(password)))

    async def set_suspended(self, subject, suspended):
        self.calls.append(('suspend', subject, suspended))

    async def get_user(self, subject):
        return {**self.users.get(subject, {'username': 'x'}), 'isSuspended': False}


@pytest.mark.asyncio
async def test_admin_role_is_granted_and_removed_with_guards(db, console, console_role):
    await seed(db)
    catalog, queries, principal, _ = await low_catalog(console, console_role, 'admin')
    issuer, own_subject = (await admin_subject(db)).values()
    await person(db, 'u', 'subject-u', issuer)
    await person(db, 'me', own_subject, issuer)
    rows = {r['id']: r for r in (await queries.users(principal))['items']}
    assert rows['u']['role'] == 'user' and rows['me']['role'] == 'admin'
    assert await catalog.set_user_role(principal, 'u', 'admin', '一起管理后台', 'role-1') == {'id': 'u', 'role': 'admin'}
    assert sorted(r['id'] for r in (await queries.users(principal, status='admins'))['items']) == ['me', 'u']
    assert (await queries.user(principal, 'u'))['role'] == 'admin'
    with pytest.raises(PlatformError) as error:
        await catalog.set_user_role(principal, 'me', 'user', '试试取消自己', 'role-2')
    assert error.value.code == 'console_cannot_demote_self'
    assert (await catalog.set_user_role(principal, 'u', 'user', '不再管理', 'role-3'))['role'] == 'user'
    assert (await queries.user(principal, 'u'))['role'] == 'user'
    assert (await catalog.set_user_role(principal, 'u', 'admin', '恢复管理', 'role-4'))['role'] == 'admin'
    async with db.connection() as c:
        events = [r['action'] for r in await (await c.execute('SELECT action FROM console_acl_events ORDER BY created_at')).fetchall()]
    assert events[-3:] == ['approve', 'disable', 'approve']
    with pytest.raises(PlatformError):
        await catalog.set_user_role(principal, 'u', 'operator', 'x', 'role-5')


@pytest.mark.asyncio
async def test_operators_cannot_change_roles_or_delete(db, console, console_role):
    await seed(db)
    catalog, _, principal, _ = await low_catalog(console, console_role, 'operator')
    for call in (lambda: catalog.set_user_role(principal, 'u', 'admin', 'x', 'op-role'),
                 lambda: catalog.set_user_deleted(principal, 'u', True, 'x', 'op-delete'),
                 lambda: catalog.adjust_credits(principal, 'wallet', '5', 'x', 'op-credits')):
        with pytest.raises(PlatformError) as error:
            await call()
        assert error.value.status == 403


@pytest.mark.asyncio
async def test_deleted_users_leave_the_list_sign_out_and_can_be_restored(db, console, console_role):
    await seed(db)
    catalog, queries, principal, _ = await low_catalog(console, console_role, 'admin')
    issuer, _ = (await admin_subject(db)).values()
    await person(db, 'u', 'subject-u', issuer)
    async with db.connection() as c:
        await c.execute("INSERT INTO product_clients(product_id,client_id) VALUES ('easel','desktop')")
        await c.execute("""INSERT INTO identity_sessions(id,user_id,tenant_id,billing_account_id,product_id,client_id,
            authorization_epoch,issuer,subject,expires_at) VALUES ('s1','u','t','wallet','easel','desktop',0,%s,'subject-u',now()+interval '1 day')""", (issuer,))
    fake = FakeLogto()
    catalog.directory = fake
    assert await catalog.set_user_deleted(principal, 'u', True, '用户要求注销', 'del-1') == {'id': 'u', 'active': False, 'deleted': True}
    assert fake.calls == [('suspend', 'subject-u', True)]
    assert 'u' not in [r['id'] for r in (await queries.users(principal))['items']]
    assert [r['id'] for r in (await queries.users(principal, status='deleted'))['items']] == ['u']
    async with db.connection() as c:
        assert (await (await c.execute("SELECT revoked_at IS NOT NULL AS r FROM identity_sessions WHERE id='s1'")).fetchone())['r']
    assert (await catalog.set_user_deleted(principal, 'u', False, '误删，恢复', 'del-2'))['active'] is True
    assert fake.calls[-1] == ('suspend', 'subject-u', False)
    assert 'u' in [r['id'] for r in (await queries.users(principal))['items']]
    await catalog.set_user_role(principal, 'u', 'admin', '管理', 'del-role')
    with pytest.raises(PlatformError) as error:
        await catalog.set_user_deleted(principal, 'u', True, '删管理员', 'del-3')
    assert error.value.code == 'console_user_is_admin'


@pytest.mark.asyncio
async def test_accounts_are_made_edited_and_reset_through_logto(db, console, console_role):
    await seed(db)
    catalog, queries, principal, _ = await low_catalog(console, console_role, 'admin')
    with pytest.raises(PlatformError) as error:
        await catalog.create_user(principal, 'new_user', '', '', '', '新同事', 'mk-0')
    assert (error.value.status, error.value.code) == (503, 'console_logto_not_configured')
    fake = FakeLogto()
    catalog.directory = queries.directory = fake
    made = await catalog.create_user(principal, 'new_user', '138 0013 8000', 'New@Example.com', 'secret-pass-1', '新同事', 'mk-1')
    assert made['created'] is True
    assert fake.calls[-1] == ('create', 'new_user', '8613800138000', 'new@example.com', True)
    async with db.connection() as c:
        operation = await (await c.execute("SELECT * FROM console_operations WHERE action='user.create'")).fetchone()
        identity = await (await c.execute('SELECT issuer,subject,username,email FROM external_identities WHERE user_id=%s', (made['id'],))).fetchone()
        wallets = await (await c.execute('SELECT count(*) AS n FROM billing_accounts b JOIN tenants t ON t.id=b.tenant_id WHERE t.owner_user_id=%s', (made['id'],))).fetchone()
    assert 'secret-pass-1' not in json.dumps(operation, default=str)
    assert identity == {'issuer': catalog.product_issuer, 'subject': 'logto-new_user', 'username': 'new_user', 'email': 'new@example.com'}
    assert wallets['n'] == 1
    assert await catalog.create_user(principal, 'new_user', '138 0013 8000', 'New@Example.com', 'secret-pass-1', '新同事', 'mk-1') == made
    with pytest.raises(PlatformError) as error:
        await catalog.create_user(principal, 'new_user', '', '', '', '重名', 'mk-2')
    assert error.value.code == 'console_username_taken'
    for bad in (('中文名', '', ''), ('ok_name', '12', ''), ('ok_name', '', 'not-an-email')):
        with pytest.raises(PlatformError):
            await catalog.create_user(principal, *bad, '', 'x', 'mk-bad')
    await catalog.update_profile(principal, made['id'], 'renamed', '', 'a@b.cn', '改名', 'ed-1')
    assert fake.calls[-1] == ('update', 'logto-new_user', 'renamed', None, 'a@b.cn')
    rows = {r['id']: r for r in (await queries.users(principal, q='renamed'))['items']}
    assert rows[made['id']]['username'] == 'renamed' and rows[made['id']]['email'] == 'a@b.cn'
    await catalog.set_password(principal, made['id'], 'another-pass-2', '忘记密码', 'pw-1')
    assert fake.calls[-1] == ('password', 'logto-new_user', 14)
    with pytest.raises(PlatformError):
        await catalog.set_password(principal, made['id'], 'short', '太短', 'pw-2')
    detail = await queries.user(principal, made['id'])
    assert detail['directory'] is True and detail['profile']['username'] == 'renamed'


@pytest.mark.asyncio
async def test_points_are_added_on_the_current_plan_and_deducted_soonest_first(db, console, console_role):
    _, _, _, scope = await seed(db)
    catalog, queries, principal, _ = await low_catalog(console, console_role, 'admin')
    await catalog.publish_plan(principal, PRO, '发布专业版', 'publish-pro')
    ledger = CreditLedger(db)
    before = int((await ledger.summary(scope))['live_available'])
    with pytest.raises(PlatformError) as error:
        await catalog.adjust_credits(principal, 'wallet', '5', '补偿', 'adj-0')
    assert error.value.code == 'console_no_live_plan'
    granted = await catalog.issue_plan(principal, 'wallet', 'pro-v1', '开通专业版', 'grant-1')
    assert granted['credits'] == '10000000000'
    added = await catalog.adjust_credits(principal, 'wallet', '+12.5', '补偿故障', 'adj-1')
    assert added['delta'] == '12500000' and added['expires_at'] is not None
    assert int((await ledger.summary(scope))['live_available']) == before + 10000000000 + 12500000
    taken = await catalog.adjust_credits(principal, 'wallet', '-2', '多发了', 'adj-2')
    assert taken['delta'] == '-2000000'
    assert int((await ledger.summary(scope))['live_available']) == before + 10000000000 + 10500000
    assert await catalog.adjust_credits(principal, 'wallet', '-2', '多发了', 'adj-2') == taken
    with pytest.raises(PlatformError) as error:
        await catalog.adjust_credits(principal, 'wallet', '-99999', '扣太多', 'adj-3')
    assert error.value.code == 'console_points_insufficient'
    for bad in ('0', 'abc', '1.1234567', '+2000000'):
        with pytest.raises(PlatformError):
            await catalog.adjust_credits(principal, 'wallet', bad, 'x', 'adj-bad-' + str(len(bad)))
    detail = await queries.user(principal, 'u')
    sources = {p['source'] for p in detail['plans']}
    assert {'console', 'points'} <= sources
    current = [r for r in (await queries.users(principal))['items'] if r['id'] == 'u'][0]
    assert current['plan_name'] == '专业版'
    async with db.connection() as c:
        kinds = [r['kind'] for r in await (await c.execute(
            "SELECT kind FROM credit_journals WHERE billing_account_id='wallet' ORDER BY created_at")).fetchall()]
    assert kinds[-1] == 'adjust'


@pytest.mark.asyncio
async def test_a_plan_is_ended_early_and_its_points_forfeited(db, console, console_role):
    _, _, _, scope = await seed(db)
    catalog, queries, principal, _ = await low_catalog(console, console_role, 'admin')
    await catalog.publish_plan(principal, PRO, '发布专业版', 'publish-pro')
    granted = await catalog.issue_plan(principal, 'wallet', 'pro-v1', '开通专业版', 'grant-1')
    before = int((await CreditLedger(db).summary(scope))['live_available'])
    ended = await catalog.end_plan(principal, 'wallet', granted['membership_id'], '换成标准版', 'end-1')
    assert ended == {'membership_id': granted['membership_id'], 'billing_account_id': 'wallet', 'forfeited': '10000000000'}
    assert int((await CreditLedger(db).summary(scope))['live_available']) == before - 10000000000
    plan = [p for p in (await queries.user(principal, 'u'))['plans'] if p['id'] == granted['membership_id']][0]
    assert plan['ended'] is True and plan['available'] == '0'
    with pytest.raises(PlatformError) as error:
        await catalog.end_plan(principal, 'wallet', granted['membership_id'], '再结束一次', 'end-2')
    assert error.value.code == 'console_plan_not_active'
    # Ended, so the same plan can be granted again right away.
    assert (await catalog.issue_plan(principal, 'wallet', 'pro-v1', '重新开通', 'grant-2'))['plan_id'] == 'pro-v1'


def test_phone_numbers_are_stored_with_the_country_code():
    assert clean_phone('13800138000') == '8613800138000'
    assert clean_phone('+1 (415) 555-0100') == '14155550100'
    assert clean_phone('') is None
    with pytest.raises(PlatformError):
        clean_phone('12')


@pytest.mark.asyncio
async def test_logto_client_gets_a_token_and_maps_errors():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        if request.url.path == '/oidc/token':
            assert b'client_credentials' in request.content and b'scope=all' in request.content
            return httpx.Response(200, json={'access_token': 'token-1', 'expires_in': 3600})
        assert request.headers['authorization'] == 'Bearer token-1'
        if request.method == 'POST':
            body = json.loads(request.content)
            if body['username'] == 'taken':
                return httpx.Response(422, json={'code': 'user.username_already_in_use'})
            if body.get('password') == 'weak-password':
                return httpx.Response(422, json={'code': 'password.rejected'})
            return httpx.Response(200, json={'id': 'abc', 'username': body['username']})
        return httpx.Response(200, json={})

    admin = LogtoAdmin('http://logto.test', 'id', 'secret', transport=httpx.MockTransport(handler))
    assert (await admin.create_user(username='ok'))['id'] == 'abc'
    with pytest.raises(PlatformError) as error:
        await admin.create_user(username='taken')
    assert error.value.code == 'console_username_taken'
    with pytest.raises(PlatformError) as error:
        await admin.create_user(username='weak', password='weak-password')
    assert error.value.code == 'console_password_rejected'
    await admin.set_suspended('abc', True)
    assert seen.count(('POST', '/oidc/token')) == 1 and ('PATCH', '/api/users/abc/is-suspended') in seen
    await admin.aclose()
