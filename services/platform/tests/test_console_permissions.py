import secrets
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import pytest

from platform_service.errors import PlatformError
from platform_service.db import Database
from platform_service.permissions import grant_runtime_permissions
from test_console_auth import console


@pytest.fixture
async def console_role(db,test_dsn):
    from platform_service.console_permissions import grant_console_permissions
    role='console_role_'+uuid4().hex;password=secrets.token_urlsafe(24)
    async with db.connection() as c:
        await c.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(role),sql.Literal(password)))
        await grant_console_permissions(c,role)
    low=Database(make_conninfo(test_dsn,user=role,password=password),schema=db.schema)
    try:
        await low.open();yield low,role
    finally:
        await low.close()
        async with db.connection() as c:
            await c.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
            await c.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


@pytest.mark.asyncio
async def test_console_role_is_not_owner_and_has_only_listed_catalog_and_auth_rights(db,console_role):
    from platform_service.console_permissions import assert_safe_console_role
    low,role=console_role
    async with low.connection() as c:await assert_safe_console_role(c)
    with pytest.raises(PlatformError):
        async with db.connection() as c:await assert_safe_console_role(c)
    async with low.connection() as c:
        checks=await (await c.execute("SELECT has_table_privilege(current_user,'console_admins','SELECT') AS acl_read,has_any_column_privilege(current_user,'console_admins','INSERT,UPDATE') AS acl_write,has_table_privilege(current_user,'execution_models','INSERT') AS catalog_write")).fetchone()
        assert checks=={'acl_read':True,'acl_write':False,'catalog_write':True}
    for statement in ['SELECT * FROM identity_tokens','SELECT * FROM identity_transactions','SELECT * FROM execution_task_tokens','SELECT payload FROM execution_results',"UPDATE console_admins SET role='admin'","INSERT INTO internal_grants(id) VALUES ('x')",'TRUNCATE console_sessions','DELETE FROM console_admins','CREATE TABLE unauthorized(id text)']:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with low.connection() as c:await c.execute(statement)
    with pytest.raises(PlatformError):await low.require_runtime_role()


@pytest.mark.asyncio
async def test_product_runtime_has_no_console_select_even_after_reprovisioning(db):
    role='product_role_'+uuid4().hex
    async with db.connection() as c:await c.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
    try:
        async with db.connection() as c:
            await grant_runtime_permissions(c,role)
            rows=await (await c.execute("SELECT c.relname,has_any_column_privilege(%s,c.oid,'SELECT') AS readable FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND c.relkind='r' AND c.relname LIKE 'console_%%'",(role,))).fetchall()
            assert rows and not any(row['readable'] for row in rows)
    finally:
        async with db.connection() as c:
            await c.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
            await c.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


@pytest.mark.asyncio
@pytest.mark.parametrize('extra',["GRANT INSERT (id) ON console_admins TO {}","GRANT UPDATE (role) ON console_admins TO {}","GRANT SELECT ON identity_tokens TO {}","GRANT UPDATE (delta) ON credit_entries TO {}"])
async def test_console_startup_detects_accidental_column_or_secret_grants(db,console_role,extra):
    from platform_service.console_permissions import assert_safe_console_role
    low,role=console_role
    async with db.connection() as c:await c.execute(sql.SQL(extra).format(sql.Identifier(role)))
    with pytest.raises(PlatformError):
        async with low.connection() as c:await assert_safe_console_role(c)


@pytest.mark.asyncio
async def test_console_runtime_cannot_bootstrap_admin_even_with_python_capability(db,console_role,console):
    from platform_service.console_auth import ConsoleAuth
    from platform_service.contracts import OperatorIdentity
    from test_console_auth import signed_login
    owner_auth,_=console;pending=await signed_login(console)
    low,_=console_role
    auth=ConsoleAuth(low,owner_auth.settings)
    with pytest.raises(PlatformError) as error:
        await auth.approve_request(pending['request_id'],'admin',OperatorIdentity('forged',frozenset({'console:bootstrap'})))
    assert error.value.code=='console_owner_required'


@pytest.mark.asyncio
async def test_real_console_auth_runs_with_low_role_but_bootstrap_stays_owner_only(db,console_role,console):
    from platform_service.console_auth import ConsoleAuth
    from test_console_auth import signed_login,bootstrap
    owner_auth,provider=console
    low,_=console_role
    auth=ConsoleAuth(low,owner_auth.settings,http_client=owner_auth.oidc.client)
    pending=await signed_login((auth,provider))
    assert pending['status']=='pending'
    await owner_auth.approve_request(pending['request_id'],'operator',bootstrap())
    session=await signed_login((auth,provider))
    principal=await auth.authenticate(session['session_token'])
    async with low.connection() as c:
        assert (await auth.validate(c,principal,'plans:publish')).role=='operator'
    async with low.connection() as c:
        await c.execute('SET TRANSACTION READ ONLY')
        assert (await auth.validate(c,principal,'console:read')).id==principal.id
    for action in [auth.list_requests(bootstrap()),auth.disable_admin(principal.id,bootstrap())]:
        with pytest.raises(PlatformError) as error:await action
        assert error.value.code=='console_owner_required'
    await auth.logout(session['session_token'])
    with pytest.raises(PlatformError):await auth.authenticate(session['session_token'])


@pytest.mark.asyncio
async def test_console_role_reprovision_removes_stale_column_privileges(db,console_role):
    from platform_service.console_permissions import grant_console_permissions,assert_safe_console_role
    low,role=console_role
    async with db.connection() as c:
        await c.execute(sql.SQL('GRANT INSERT(id) ON console_admins TO {}').format(sql.Identifier(role)))
        await c.execute(sql.SQL('GRANT SELECT(token_hash) ON identity_tokens TO {}').format(sql.Identifier(role)))
        await grant_console_permissions(c,role)
    async with low.connection() as c:await assert_safe_console_role(c)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        async with low.connection() as c:await c.execute('SELECT token_hash FROM identity_tokens')


@pytest.mark.asyncio
async def test_product_role_cannot_hide_console_authority_in_noinherit_membership(db,test_dsn,console_role):
    _,parent=console_role
    child='console_child_'+uuid4().hex;password=secrets.token_urlsafe(24)
    async with db.connection() as c:
        await c.execute(sql.SQL('CREATE ROLE {} LOGIN NOINHERIT PASSWORD {}').format(sql.Identifier(child),sql.Literal(password)))
        await c.execute(sql.SQL('GRANT {} TO {}').format(sql.Identifier(parent),sql.Identifier(child)))
        await c.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(db.schema),sql.Identifier(child)))
    low=Database(make_conninfo(test_dsn,user=child,password=password),schema=db.schema)
    try:
        await low.open()
        with pytest.raises(PlatformError) as error:await low.require_runtime_role()
        assert error.value.code=='unsafe_runtime_role'
    finally:
        await low.close()
        async with db.connection() as c:
            await c.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(child)))
            await c.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(child)))


@pytest.mark.asyncio
@pytest.mark.parametrize('statement',[
    'SELECT checkout FROM billing_orders','SELECT provider_transaction_id FROM billing_orders',
    'SELECT merchant_id FROM billing_orders','SELECT provider_refund_id FROM billing_refunds',
    'SELECT scope FROM execution_jobs','SELECT reason FROM execution_jobs',
    'SELECT provider_response_id FROM execution_receipts','SELECT attempt_id FROM execution_receipts',
    'SELECT session_id FROM internal_grants','SELECT source_transaction_id FROM internal_grants',
    'SELECT id FROM identity_sessions','SELECT idempotency_key FROM credit_reservations',
    'SELECT body_hash FROM credit_reservations','SELECT completion_key FROM credit_reservations'])
async def test_console_database_role_cannot_read_private_business_columns(console_role,statement):
    low,_=console_role
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        async with low.connection() as c:await c.execute(statement)


@pytest.mark.asyncio
async def test_product_cannot_use_console_audit_sequence(db):
    role='product_sequence_'+uuid4().hex
    async with db.connection() as c:await c.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
    try:
        async with db.connection() as c:
            await grant_runtime_permissions(c,role)
            row=await (await c.execute("SELECT has_sequence_privilege(%s,pg_get_serial_sequence(current_schema()||'.console_audit_events','id'),'USAGE,SELECT,UPDATE') AS leaked",(role,))).fetchone()
            assert row['leaked'] is False
            for table in ('credit_entries','payment_event_audits'):
                row=await (await c.execute("SELECT has_sequence_privilege(%s,pg_get_serial_sequence(current_schema()||'.'||%s,'id'),'USAGE') AS allowed",(role,table))).fetchone()
                assert row['allowed'] is True
    finally:
        async with db.connection() as c:
            await c.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
            await c.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


@pytest.mark.asyncio
async def test_product_guard_rejects_console_sequence_privilege(db):
    from platform_service.permissions import assert_safe_runtime_role
    role='product_sequence_guard_'+uuid4().hex
    async with db.connection() as c:await c.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
    try:
        async with db.connection() as c:
            await c.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(db.schema),sql.Identifier(role)))
            await c.execute(sql.SQL('GRANT USAGE ON SEQUENCE console_audit_events_id_seq TO {}').format(sql.Identifier(role)))
        with pytest.raises(PlatformError):
            async with db.connection() as c:await assert_safe_runtime_role(c,role)
    finally:
        async with db.connection() as c:
            await c.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
            await c.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


@pytest.mark.asyncio
@pytest.mark.parametrize('extra',[
    'GRANT USAGE ON SEQUENCE credit_entries_id_seq TO {}',
    'GRANT SELECT ON SEQUENCE console_audit_events_id_seq TO {}',
    'GRANT UPDATE ON SEQUENCE console_audit_events_id_seq TO {}'])
async def test_console_guard_rejects_unneeded_sequence_authority(db,console_role,extra):
    from platform_service.console_permissions import assert_safe_console_role
    low,role=console_role
    async with db.connection() as c:await c.execute(sql.SQL(extra).format(sql.Identifier(role)))
    with pytest.raises(PlatformError):
        async with low.connection() as c:await assert_safe_console_role(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('scenario',['wallet','billing','execution'])
async def test_every_query_and_catalog_runs_with_actual_low_role_and_auth(db,console_role,console,scenario):
    from platform_service.console_auth import ConsoleAuth
    from platform_service.console_service import ConsoleQueries
    from platform_service.console_catalog import ConsoleCatalog
    from test_console_auth import authenticated
    from test_console_catalog import REGISTRY
    from test_execution_core import definition
    if scenario=='billing':
        from test_billing import setup_billing
        billing,_,scope,plan=await setup_billing(db)
        order=await billing.create_order(scope,offer_id='offer',channel='wechat',idempotency_key='low-role-order')
    elif scenario=='execution':
        from test_execution_core import setup,admit
        execution,_,_,context=await setup(db)
        await admit(execution,context)
        plan=None
    else:
        from test_membership import seed
        members,operator,plan,_=await seed(db)
        await members.issue_test(operator,'wallet',plan['id'],'low-role-grant')
    session,_=await authenticated(console,'operator')
    owner_auth,_=console;low,_=console_role
    auth=ConsoleAuth(low,owner_auth.settings)
    principal=await auth.authenticate(session['session_token'])
    queries=ConsoleQueries(low,auth,provider_ids=REGISTRY)
    assert 'wallets' in await queries.overview(principal)
    assert all('spendable_available' in row for row in (await queries.users(principal))['items'])
    for name in ('users','products','plans','wallets','orders','offers','models','providers','executions','audits'):
        result=await getattr(queries,name)(principal,limit=2)
        assert set(result)=={'items','limit','offset','has_more'}
    users=(await queries.users(principal))['items']
    assert users and (await queries.user(principal,users[0]['id']))['id']==users[0]['id']
    # Every user page and the stats page run with the low role's actual grants.
    for status in ('','funded','empty','expiring','disabled'):
        await queries.users(principal,status=status,sort='last_used')
    for row in users:
        await queries.user_activity(principal,row['id'],30)
        await queries.user_usage(principal,row['id'])
        await queries.user_ledger(principal,row['id'])
        await queries.user_sessions(principal,row['id'])
        await queries.user_audits(principal,row['id'])
    assert len((await queries.stats(principal,7))['daily'])==7
    wallets=(await queries.wallets(principal))['items']
    assert wallets and (await queries.wallet(principal,wallets[0]['id']))['id']==wallets[0]['id']
    if scenario=='billing':
        assert (await queries.order(principal,order['id']))['id']==order['id']
    if scenario=='wallet':
        catalog=ConsoleCatalog(low,auth,provider_ids=REGISTRY)
        await catalog.publish_plan(principal,{**plan,'id':'minimum-role-plan','name':'Minimum role'},'Reviewed','low-plan')
        published=await catalog.publish_model(principal,definition(),'Reviewed','low-model')
        assert (await catalog.set_model_state(principal,'fixture-text',True,'Enable','low-enable',published['updated_at']))['enabled']
        offer={'id':'low-role-offer','plan_id':'minimum-role-plan','version':'1','amount_minor':'1','currency':'CNY',
               'test_only':True,'terms':{'refund_policy':'unused_full_only','description':'Reviewed test offer'}}
        await catalog.publish_offer(principal,offer,'Reviewed','low-offer')
        assert (await catalog.withdraw_offer(principal,offer['id'],'Withdraw','low-withdraw'))['withdrawn'] is True
        assert len((await queries.audits(principal))['items'])==5
