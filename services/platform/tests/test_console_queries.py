"""Real PostgreSQL projections; no network or credential-bearing fixtures."""
from types import SimpleNamespace
import importlib.util
import json
from datetime import datetime, timezone
import pytest
from platform_service.errors import PlatformError
from test_membership import seed


class ReadAuth:
    async def validate(self, conn, principal, permission):
        assert permission == 'console:read'
        row = await (await conn.execute('SHOW transaction_read_only')).fetchone()
        assert row['transaction_read_only'] == 'on'
        if permission not in principal.permissions:
            raise PlatformError(403, 'console_permission_denied')
        return principal


PRINCIPAL = SimpleNamespace(id='a' * 32, role='viewer', permissions=frozenset({'console:read'}), session_hash='never-export-session')


def service(db):
    from platform_service.console_service import ConsoleQueries
    return ConsoleQueries(db, ReadAuth(), provider_ids=('fixture',), payments_mode='disabled')


def test_console_queries_contract_exists():
    assert importlib.util.find_spec('platform_service.console_service') is not None


def test_nested_model_metadata_whitelisted_without_database():
    from platform_service.console_service import ConsoleQueries
    from test_execution_core import definition
    model=definition()
    model['rate']['private_extra']='not-for-console'
    model['limits']['private_extra']='not-for-console'
    row={'definition':model,'enabled':False,'updated_at':datetime.now(timezone.utc),'operator_id':'fixture'}
    assert 'private_extra' not in json.dumps(ConsoleQueries._model(row))


def test_audit_summary_whitelist_without_database():
    from platform_service.console_service import ConsoleQueries
    row={'id':1,'operation_id':'operation','actor_id':'actor','action':'model.state','target_type':'model','target_id':'model',
         'reason':'Enable','key_hash':'a'*64,'request_hash':'b'*64,'created_at':datetime.now(timezone.utc),
         'result_enabled':True,'result_name':'Fixture','result_version':'1','private_result':{'private':'not-for-console'}}
    result=ConsoleQueries._audit(row)
    assert result['result_summary']=={'enabled':True,'name':'Fixture','version':'1'}
    assert 'private_result' not in json.dumps(result)


@pytest.mark.asyncio
async def test_read_pages_real_snapshot_no_expiry_mutation(db):
    members, operator, plan, scope = await seed(db, credits='9007199254740993')
    await members.issue_test(operator, 'wallet', plan['id'], 'one')
    # An expired available lot would be changed by CreditLedger.summary().
    async with db.connection() as c:
        await c.execute("UPDATE credit_lots SET expires_at=now()-interval '1 day'")
        before = await (await c.execute('SELECT sum(available) AS available FROM credit_lots')).fetchone()
    api = service(db)
    overview = await api.overview(PRINCIPAL)
    assert overview['users'] == 2 and overview['products'] == 2
    assert overview['payments_mode'] == 'disabled'
    assert any(row['available'] == '9007199254740993' for row in overview['wallets'])
    result = await api.wallet(PRINCIPAL, 'wallet')
    assert result['issued'] == result['available'] == '9007199254740993'
    assert result['lots'][0]['test_only'] is True
    assert result['memberships'][0]['test_only'] is True
    async with db.connection() as c:
        assert await (await c.execute('SELECT sum(available) AS available FROM credit_lots')).fetchone() == before
        assert (await (await c.execute("SELECT count(*) AS n FROM credit_journals WHERE kind='expire'")).fetchone())['n'] == 0


@pytest.mark.asyncio
async def test_all_pages_paginate_and_fixed_safe_projection(db):
    await seed(db)
    async with db.connection() as c:
        await c.execute("INSERT INTO external_identities(issuer,subject,user_id,email) VALUES ('private-issuer','private-subject','u','private-email@example.invalid')")
    api = service(db)
    first = await api.users(PRINCIPAL, limit=1)
    second = await api.users(PRINCIPAL, limit=1, offset=1)
    assert first['has_more'] is True and second['has_more'] is False
    assert first['items'][0]['id'] != second['items'][0]['id']
    for name in ('users','products','plans','wallets','orders','offers','models','providers','executions','audits'):
        result = await getattr(api, name)(PRINCIPAL, limit=1, offset=0, q='')
        assert set(result) == {'items','limit','offset','has_more'}
        serialized = json.dumps(result)
        assert all(secret not in serialized for secret in ('private-subject','private-email','never-export-session'))
    detail = await api.user(PRINCIPAL, 'u')
    assert detail['id'] == 'u' and detail['wallets'][0]['id'] == 'wallet'
    assert 'subject' not in json.dumps(detail) and 'email' not in detail


@pytest.mark.asyncio
@pytest.mark.parametrize('arguments', [{'limit':0},{'limit':101},{'limit':True},{'offset':-1},{'offset':100001},{'q':'x'*129}])
async def test_query_bounds_fail_closed(db, arguments):
    with pytest.raises(PlatformError) as error:
        await service(db).users(PRINCIPAL, **arguments)
    assert error.value.status == 422


@pytest.mark.asyncio
async def test_query_parameter_injection_is_data_and_read_requires_auth(db):
    await seed(db)
    assert (await service(db).users(PRINCIPAL, q="%' OR true; DROP TABLE users;--"))['items'] == []
    denied = SimpleNamespace(id='a'*32, role='viewer', permissions=frozenset(), session_hash='private')
    with pytest.raises(PlatformError):
        await service(db).overview(denied)
    async with db.connection() as c:
        assert (await (await c.execute('SELECT count(*) AS n FROM users')).fetchone())['n'] == 2


@pytest.mark.asyncio
async def test_order_details_exclude_checkout_and_raw_provider_fields(db):
    from test_billing import setup_billing
    billing, _, scope, _ = await setup_billing(db)
    order = await billing.create_order(scope, offer_id='offer', channel='wechat', idempotency_key='one')
    async with db.connection() as c:
        await c.execute("UPDATE billing_orders SET checkout=%s WHERE id=%s", ('{"payload":"private-checkout-secret"}',order['id']))
    result = await service(db).order(PRINCIPAL, order['id'])
    assert result['amount_minor'] == '1'
    raw = json.dumps(result)
    assert 'checkout' not in result and 'private-checkout-secret' not in raw
    assert 'merchant_id' not in result and 'provider_transaction_id' not in result


@pytest.mark.asyncio
async def test_execution_uses_safe_projection_without_result_payload(db):
    from test_execution_core import setup, admit
    execution, _, _, context = await setup(db)
    await admit(execution, context)
    result = await service(db).executions(PRINCIPAL)
    assert len(result['items']) == 1
    row = result['items'][0]
    assert row['actual'] is None and row['status'] == 'reserved'
    assert not {'scope','body_hash','attempt_id','binding_hash','payload','model_definition'} & set(row)


@pytest.mark.asyncio
async def test_console_real_session_can_read_without_last_seen_writes(db):
    import secrets
    from platform_service.console_auth import ConsoleAuth
    from platform_service.identity.oidc import digest
    from platform_service.console_config import ConsoleSettings
    # Constructor settings are public fixture values; no discovery/network occurs.
    token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
    async with db.connection() as c:
        await c.execute("INSERT INTO console_admins(id,issuer,subject,role,created_by) VALUES (%s,'fixture-issuer','fixture-subject','viewer','fixture')",('b'*32,))
        await c.execute("INSERT INTO console_sessions(session_hash,admin_id,csrf_token,authorization_epoch,created_at,expires_at) VALUES (%s,%s,%s,0,now(),now()+interval '1 hour')",(digest(token),'b'*32,csrf))
    settings=ConsoleSettings(database_url='postgresql://unused@127.0.0.1/unused',public_url='https://console.example',
                             issuer='https://identity.example/oidc',client_id='fixture',client_secret='not-real',db_schema=db.schema)
    auth=ConsoleAuth(db,settings)
    from platform_service.console_auth import AdminPrincipal
    principal=AdminPrincipal('b'*32,'viewer',frozenset({'console:read'}),csrf,digest(token))
    from platform_service.console_service import ConsoleQueries
    api=ConsoleQueries(db,auth)
    assert (await api.users(principal))['items']==[]


@pytest.mark.asyncio
async def test_nested_legacy_catalog_metadata_is_projected_not_dumped(db):
    _,_,plan,_=await seed(db)
    snapshot={**plan,'plan_id':plan['id'],'id':'legacy-offer','amount_minor':'1','currency':'CNY',
              'terms':{'refund_policy':'unused_full_only','description':'Legacy fixture','private_extra':'not-for-console'}}
    async with db.connection() as c:
        await c.execute('INSERT INTO billing_offers(id,plan_id,version,definition,created_by) VALUES (%s,%s,%s,%s,%s)',
                        ('legacy-offer',plan['id'],'1',json.dumps(snapshot),'fixture'))
    result=await service(db).offers(PRINCIPAL)
    assert 'private_extra' not in json.dumps(result)
    assert result['items'][0]['terms']=={'refund_policy':'unused_full_only','description':'Legacy fixture'}


@pytest.mark.asyncio
@pytest.mark.parametrize('test_only',[True,False])
async def test_frozen_and_expired_available_are_not_advertised_spendable(db,test_only):
    if test_only:
        members,operator,plan,_=await seed(db,credits='100')
        lots=[await members.issue_test(operator,'wallet',plan['id'],key) for key in ('active','frozen','expired')]
    else:
        from test_billing import setup_billing,event
        from platform_service.billing import BillingService
        from platform_service.membership import MembershipService
        billing,operator,scope,_=await setup_billing(db)
        async with db.connection() as c:
            plan=(await (await c.execute('SELECT definition FROM plans')).fetchone())['definition']
        await MembershipService(db).publish_plan(operator,{**plan,'id':'live-plan','name':'Live','test_only':False})
        live=BillingService(db,mode='live',channels=billing.channels.values())
        await live.publish_offer(operator,{'id':'live-offer','plan_id':'live-plan','version':'1','amount_minor':'1',
            'currency':'CNY','test_only':False,'terms':{'refund_policy':'unused_full_only','description':'Local simulation'}})
        for key in ('active','frozen','expired'):
            order=await live.create_order(scope,offer_id='live-offer',channel='wechat',idempotency_key=key)
            await live.apply_verified_event({**event(order),'mode':'live','event_id':key,'provider_transaction_id':key})
        async with db.connection() as c:
            lots=await (await c.execute('SELECT membership_id AS id FROM credit_lots ORDER BY created_at,id')).fetchall()
    async with db.connection() as c:
        await c.execute('UPDATE credit_lots SET frozen=true WHERE membership_id=%s',(lots[1]['id'],))
        await c.execute("UPDATE credit_lots SET expires_at=now()-interval '1 day' WHERE membership_id=%s",(lots[2]['id'],))
        before=await (await c.execute('SELECT count(*) AS n FROM credit_journals')).fetchone()
    api=service(db)
    detail=await api.wallet(PRINCIPAL,'wallet')
    listed=next(row for row in (await api.wallets(PRINCIPAL))['items'] if row['id']=='wallet')
    funding=(await api.overview(PRINCIPAL))['wallets'][0]
    for row in (detail,listed,funding):
        assert row['available']=='300'
        assert row['spendable_available']=='100'
        assert row['frozen_available']=='100'
        assert row['unposted_expired_available']=='100'
    for row in (detail,listed):
        assert row['test_available']==('100' if test_only else '0')
        assert row['live_available']==('0' if test_only else '100')
    async with db.connection() as c:
        assert await (await c.execute('SELECT count(*) AS n FROM credit_journals')).fetchone()==before


@pytest.mark.asyncio
async def test_audit_projection_includes_safe_model_after_state(db):
    from test_console_catalog import CatalogAuth,PERMISSIONS,REGISTRY
    from test_execution_core import definition
    from platform_service.console_catalog import ConsoleCatalog
    async with db.connection() as c:
        await c.execute("INSERT INTO console_admins(id,issuer,subject,role,created_by) VALUES (%s,'fixture','subject','operator','fixture')",('a'*32,))
    principal=SimpleNamespace(id='a'*32,role='operator',permissions=PERMISSIONS,session_hash='not-exported')
    api=ConsoleCatalog(db,CatalogAuth(),provider_ids=REGISTRY)
    published=await api.publish_model(principal,definition(),'Reviewed','publish')
    await api.set_model_state(principal,'fixture-text',True,'Enable','enable',published['updated_at'])
    rows=(await service(db).audits(principal))['items']
    assert rows[0]['result_summary']['enabled'] is True
    assert rows[1]['result_summary']['name']=='Fixture text'
    assert 'result' not in rows[0] and 'limits' not in rows[1]['result_summary']

