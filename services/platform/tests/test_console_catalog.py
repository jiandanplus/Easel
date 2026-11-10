"""Atomic catalog mutation, same-actor idempotence and append-only audit."""
import asyncio
import importlib.util
from types import SimpleNamespace
from datetime import datetime, timezone
import psycopg
import pytest
from platform_service.errors import PlatformError
from test_membership import seed
from test_execution_core import definition

PERMISSIONS = frozenset({'console:read','plans:publish','billing:publish','execution:publish'})
REGISTRY = ({'id':'fixture','version':'1','models':{'fixture-text':{'upstream_model':'safe-upstream','verification':'fixture','verification_ref':'local-fixture'}}},)


class CatalogAuth:
    async def validate(self, conn, principal, permission):
        row = await (await conn.execute('SELECT role,active FROM console_admins WHERE id=%s', (principal.id,))).fetchone()
        if not row or not row['active'] or row['role'] == 'viewer' or permission not in principal.permissions:
            raise PlatformError(403,'console_permission_denied')
        return principal


@pytest.fixture
async def catalog_env(db):
    _, _, plan, _ = await seed(db)
    async with db.connection() as c:
        await c.execute("INSERT INTO console_admins(id,issuer,subject,role,created_by) VALUES (%s,'fixture-issuer','fixture-subject','operator','fixture-owner')", ('a'*32,))
    principal = SimpleNamespace(id='a'*32, role='operator', permissions=PERMISSIONS, session_hash='not-exported')
    from platform_service.console_catalog import ConsoleCatalog
    return ConsoleCatalog(db, CatalogAuth(), provider_ids=REGISTRY), principal, plan


def test_console_catalog_contract_exists():
    assert importlib.util.find_spec('platform_service.console_catalog') is not None


@pytest.mark.asyncio
async def test_plan_publish_atomic_audit_replay_and_conflict(db,catalog_env):
    api,p,plan = catalog_env
    new = {**plan,'id':'console-plan','name':'Console Plan'}
    first = await api.publish_plan(p,new,'Publish reviewed plan','one')
    assert first == await api.publish_plan(p,new,'Publish reviewed plan','one')
    with pytest.raises(PlatformError) as error:
        await api.publish_plan(p,{**new,'credits':'101'},'Publish reviewed plan','one')
    assert error.value.status == 409
    async with db.connection() as c:
        assert (await (await c.execute('SELECT count(*) AS n FROM console_operations')).fetchone())['n'] == 1
        assert (await (await c.execute('SELECT count(*) AS n FROM console_audit_events')).fetchone())['n'] == 1
    assert 'session_hash' not in str(first)


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_business_and_operation(db,catalog_env):
    api,p,plan = catalog_env
    async with db.connection() as c:
        await c.execute("CREATE FUNCTION reject_console_audit() RETURNS trigger LANGUAGE plpgsql AS $$BEGIN RAISE EXCEPTION 'fixture-rejected'; END$$")
        await c.execute('CREATE TRIGGER reject_audit BEFORE INSERT ON console_audit_events FOR EACH ROW EXECUTE FUNCTION reject_console_audit()')
    with pytest.raises(psycopg.Error):
        await api.publish_plan(p,{**plan,'id':'rolled-back','name':'Rollback'},'Reviewed','rollback')
    async with db.connection() as c:
        assert not await (await c.execute("SELECT 1 FROM plans WHERE id='rolled-back'")).fetchone()
        assert not await (await c.execute('SELECT 1 FROM console_operations')).fetchone()


@pytest.mark.asyncio
async def test_concurrent_idempotency_once(db,catalog_env):
    api,p,plan = catalog_env
    new={**plan,'id':'concurrent','name':'Concurrent'}
    results=await asyncio.gather(*(api.publish_plan(p,new,'Concurrent reviewed','same') for _ in range(4)))
    assert all(row == results[0] for row in results)
    async with db.connection() as c:
        assert (await (await c.execute('SELECT count(*) AS n FROM console_audit_events')).fetchone())['n'] == 1


@pytest.mark.asyncio
async def test_roles_rechecked_even_for_idempotent_replay(db,catalog_env):
    api,p,plan=catalog_env
    new={**plan,'id':'revoked','name':'Revoked'}
    await api.publish_plan(p,new,'Reviewed','one')
    async with db.connection() as c:await c.execute("UPDATE console_admins SET active=false WHERE id=%s",(p.id,))
    with pytest.raises(PlatformError):await api.publish_plan(p,new,'Reviewed','one')


@pytest.mark.asyncio
async def test_model_registry_publish_disabled_and_optimistic_state(db,catalog_env):
    api,p,_=catalog_env
    await api.publish_model(p,definition(),'Reviewed model','model')
    async with db.connection() as c:
        state=await (await c.execute("SELECT enabled,updated_at FROM execution_model_states WHERE model_id='fixture-text'")).fetchone()
    assert state['enabled'] is False
    result=await api.set_model_state(p,'fixture-text',True,'Enable verified model','enable',state['updated_at'].isoformat())
    assert result['enabled'] is True
    assert await api.set_model_state(p,'fixture-text',True,'Enable verified model','enable',state['updated_at'].isoformat()) == result
    with pytest.raises(PlatformError) as error:
        await api.set_model_state(p,'fixture-text',False,'Stale disable','stale',state['updated_at'].isoformat())
    assert error.value.status == 409


@pytest.mark.asyncio
async def test_registry_verification_cannot_be_browser_claimed(db,catalog_env):
    api,p,_=catalog_env
    for changes in ({'provider_version':'unknown'},{'verification_ref':'claimed-by-browser'}, {'verification':'live','test_only':False}):
        with pytest.raises(PlatformError):
            await api.publish_model(p,{**definition(),**changes},'Claimed','different-'+str(len(changes)))


@pytest.mark.asyncio
async def test_internal_plan_and_offer_never_publish_from_console(db,catalog_env):
    api,p,plan=catalog_env
    with pytest.raises(PlatformError):
        await api.publish_plan(p,{**plan,'id':'easel-internal-experience-v1'},'Forbidden','internal')
    offer={'id':'internal-sale','plan_id':'easel-internal-experience-v1','version':'1','amount_minor':'1','currency':'CNY','test_only':False,'terms':{'refund_policy':'unused_full_only','description':'Forbidden'}}
    with pytest.raises(PlatformError):await api.publish_offer(p,offer,'Forbidden','offer')


@pytest.mark.asyncio
async def test_offer_publish_withdraw_and_immutable_history(db,catalog_env):
    api,p,plan=catalog_env
    offer={'id':'console-offer','plan_id':plan['id'],'version':'1','amount_minor':'1','currency':'CNY','test_only':True,'terms':{'refund_policy':'unused_full_only','description':'Fixture'}}
    await api.publish_offer(p,offer,'Publish','offer')
    assert (await api.withdraw_offer(p,offer['id'],'Withdraw','withdraw'))['withdrawn'] is True
    for table in ('console_operations','console_audit_events'):
        for statement in (f'DELETE FROM {table}',f'TRUNCATE {table}'):
            with pytest.raises(psycopg.Error):
                async with db.connection() as c:await c.execute(statement)


@pytest.mark.asyncio
@pytest.mark.parametrize('reason,key',[('', 'key'),('Reason',''),('x'*513,'key'),('Reason','x'*129)])
async def test_reason_and_key_bounded(db,catalog_env,reason,key):
    api,p,plan=catalog_env
    with pytest.raises(PlatformError) as error:await api.publish_plan(p,plan,reason,key)
    assert error.value.status == 422


@pytest.mark.asyncio
async def test_idempotency_scoped_to_actor_and_no_unadvertised_mutations(db,catalog_env):
    api,p,plan=catalog_env
    second=SimpleNamespace(id='b'*32,role='operator',permissions=PERMISSIONS,session_hash='not-exported-either')
    async with db.connection() as c:
        await c.execute("INSERT INTO console_admins(id,issuer,subject,role,created_by) VALUES (%s,'fixture-issuer','second-subject','operator','fixture')",(second.id,))
    await api.publish_plan(p,{**plan,'id':'first-actor','name':'First'},'First actor','shared-key')
    await api.publish_plan(second,{**plan,'id':'second-actor','name':'Second'},'Second actor','shared-key')
    async with db.connection() as c:
        assert (await (await c.execute('SELECT count(*) AS n FROM console_operations')).fetchone())['n']==2
    for forbidden in ('issue','issue_test','grant','refund','settle','recover','set_balance'):
        assert not hasattr(api,forbidden)


@pytest.mark.asyncio
async def test_concurrent_model_state_compare_and_swap_has_one_winner(db,catalog_env):
    api,p,_=catalog_env
    published=await api.publish_model(p,definition(),'Reviewed','publish')
    results=await asyncio.gather(*(api.set_model_state(p,'fixture-text',True,'Enable',key,published['updated_at'])
                                   for key in ('first','second')),return_exceptions=True)
    assert sum(isinstance(row,PlatformError) and row.status==409 for row in results)==1
    assert sum(isinstance(row,dict) and row['enabled'] is True for row in results)==1


@pytest.mark.asyncio
async def test_low_privilege_role_with_real_auth_queries_and_catalog(db,test_dsn):
    import secrets
    from uuid import uuid4
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from platform_service.db import Database
    from platform_service.console_permissions import grant_console_permissions,assert_safe_console_role
    from platform_service.console_auth import ConsoleAuth,AdminPrincipal
    from platform_service.console_config import ConsoleSettings
    from platform_service.identity.oidc import digest
    from platform_service.console_service import ConsoleQueries
    from platform_service.console_catalog import ConsoleCatalog
    from platform_service.billing.service import BillingService
    from platform_service.contracts import OperatorIdentity
    _,_,plan,scope=await seed(db)
    billing=BillingService(db,mode='test',channels=({'id':'wechat','enabled':True,'merchant_id':'fixture-merchant','app_id':'fixture-app'},))
    base_offer={'id':'owned-fixture-offer','plan_id':plan['id'],'version':'1','amount_minor':'1','currency':'CNY','test_only':True,
                'terms':{'refund_policy':'unused_full_only','description':'Owned fixture'}}
    await billing.publish_offer(OperatorIdentity('fixture',frozenset({'billing:publish'})),base_offer)
    order=await billing.create_order(scope,offer_id=base_offer['id'],channel='wechat',idempotency_key='owned-fixture')
    actor='c'*32;token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
    role='console_catalog_'+uuid4().hex;password=secrets.token_urlsafe(32)
    async with db.connection() as c:
        await c.execute("INSERT INTO console_admins(id,issuer,subject,role,created_by) VALUES (%s,'fixture-issuer','real-auth-subject','operator','fixture')",(actor,))
        await c.execute("INSERT INTO console_sessions(session_hash,admin_id,csrf_token,authorization_epoch,created_at,expires_at) VALUES (%s,%s,%s,0,now(),now()+interval '1 hour')",(digest(token),actor,csrf))
        await c.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(role),sql.Literal(password)))
        await grant_console_permissions(c,role)
    low=Database(make_conninfo(test_dsn,user=role,password=password),schema=db.schema)
    settings=ConsoleSettings(database_url='postgresql://unused@127.0.0.1/unused',public_url='https://console.example',
                             issuer='https://identity.example/oidc',client_id='fixture',client_secret='not-real',db_schema=db.schema)
    try:
        await low.open()
        async with low.connection() as c:await assert_safe_console_role(c)
        auth=ConsoleAuth(low,settings)
        principal=AdminPrincipal(actor,'operator',PERMISSIONS,csrf,digest(token))
        queries=ConsoleQueries(low,auth,provider_ids=REGISTRY)
        assert len((await queries.users(principal))['items'])==2
        assert (await queries.wallet(principal,'wallet'))['issued']=='0'
        catalog=ConsoleCatalog(low,auth,provider_ids=REGISTRY)
        await catalog.publish_plan(principal,{**plan,'id':'low-role-plan','name':'Low role'},'Reviewed','plan')
        await catalog.publish_offer(principal,{**base_offer,'id':'console-role-offer','plan_id':'low-role-plan'},'Reviewed','offer')
        await catalog.withdraw_offer(principal,'console-role-offer','Withdraw reviewed offer','withdraw')
        published=await catalog.publish_model(principal,definition(),'Reviewed','model')
        assert (await catalog.set_model_state(principal,'fixture-text',True,'Enable','enable',published['updated_at']))['enabled']
        assert len((await queries.audits(principal))['items'])==5
        assert (await queries.overview(principal))['orders']==1
        assert (await queries.user(principal,'u'))['id']=='u'
        assert (await queries.order(principal,order['id']))['amount_minor']=='1'
        for name in ('users','products','plans','wallets','orders','offers','models','providers','executions','audits'):
            result=await getattr(queries,name)(principal,limit=1)
            assert set(result)=={'items','limit','offset','has_more'}
        for statement in ('SELECT token_hash FROM identity_tokens','SELECT payload FROM execution_results',
                          'SELECT checkout FROM billing_orders','SELECT scope FROM execution_jobs',
                          'SELECT provider_response_id FROM execution_receipts',
                          "INSERT INTO internal_grants(id) VALUES ('forbidden')"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                async with low.connection() as c:await c.execute(statement)
    finally:
        await low.close()
        async with db.connection() as c:
            await c.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
            await c.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
