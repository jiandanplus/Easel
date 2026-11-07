"""Signed synthetic OIDC, real owned PG, separate admin authorization."""
from dataclasses import replace
from urllib.parse import parse_qs,urlsplit
import hashlib
import json
import time
import asyncio
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

from platform_service.contracts import OperatorIdentity
from platform_service.errors import PlatformError
from test_identity import Provider


@pytest_asyncio.fixture
async def console(db):
    from platform_service.console_auth import ConsoleAuth
    from platform_service.console_config import ConsoleSettings
    provider=Provider()
    provider.overrides={'auth_time':int(time.time())}
    settings=ConsoleSettings(database_url='postgresql://unused@localhost/unused',public_url='https://central.example',
        issuer=provider.issuer,client_id='oidc-client',client_secret='fixture-client-secret',db_schema=db.schema)
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider.handler)) as client:
        yield ConsoleAuth(db,settings,http_client=client),provider


async def signed_login(console):
    auth,provider=console
    provider.overrides={'auth_time':int(time.time()),**provider.overrides}
    begin=await auth.begin_login()
    provider.params={**parse_qs(urlsplit(begin['url']).query),'product':'console'}
    assert provider.params['redirect_uri']==['https://central.example/oidc/console/callback']
    assert provider.params['prompt']==['login']
    return await auth.callback('synthetic-code',provider.params['state'][0],begin['cookie'])


def bootstrap():return OperatorIdentity('fixture-owner',frozenset({'console:bootstrap'}))


async def authenticated(console,role='admin'):
    auth,_=console
    pending=await signed_login(console)
    assert pending['status']=='pending'
    await auth.approve_request(pending['request_id'],role,bootstrap())
    result=await signed_login(console)
    assert result['status']=='authenticated'
    return result,await auth.authenticate(result['session_token'])


@pytest.mark.asyncio
async def test_signed_user_without_acl_only_requests_and_never_becomes_product_or_admin(console,db):
    auth,_=console
    result=await signed_login(console)
    assert set(result)=={'status','request_id'} and result['status']=='pending' and len(result['request_id'])==32
    requests=await auth.list_requests(bootstrap())
    assert len(requests)==1 and requests[0]['id']==result['request_id']
    assert set(requests[0]) <= {'id','status','subject_hash','verified_at','expires_at'}
    assert 'subject-1' not in json.dumps(requests)
    async with db.connection() as c:
        for table in ['console_admins','console_sessions','users','identity_sessions']:
            assert (await (await c.execute('SELECT count(*) AS n FROM '+table)).fetchone())['n']==0
        transactions=await (await c.execute('SELECT * FROM console_login_transactions')).fetchall()
        assert len(transactions)==1 and transactions[0]['consumed'] is True
        assert 'synthetic-code' not in json.dumps(transactions,default=str)


@pytest.mark.asyncio
@pytest.mark.parametrize('role,permissions',[('admin',{'console:read','plans:publish','billing:publish','execution:publish','execution:review','users:manage','owner:allowance','plans:issue'}),('operator',{'console:read','plans:publish','billing:publish','execution:publish','execution:review','users:manage'}),('viewer',{'console:read'})])
async def test_session_has_current_role_csrf_and_only_cookie_hash_persisted(console,db,role,permissions):
    auth,_=console
    result,principal=await authenticated(console,role)
    assert principal.role==role and principal.permissions==frozenset(permissions)
    assert principal.csrf_token==result['csrf_token']
    assert principal.session_hash==hashlib.sha256(result['session_token'].encode()).hexdigest()
    assert result['session_token'] not in repr(principal) and principal.session_hash not in repr(principal)
    async with db.connection() as c:
        row=await (await c.execute('SELECT * FROM console_sessions')).fetchone()
        assert 'session_token' not in row and result['session_token'] not in json.dumps(row,default=str)
        assert (row['expires_at']-row['created_at']).total_seconds()==28800
        assert (await auth.validate(c,principal,'console:read')).id==principal.id
        if role=='viewer':
            with pytest.raises(PlatformError) as error:await auth.validate(c,principal,'plans:publish')
            assert error.value.status==403


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['state','cookie','expired','replay','nonce','issuer','audience'])
async def test_console_login_rejects_correlation_expiry_replay_and_bad_signed_claims(console,db,failure):
    auth,provider=console
    start=await auth.begin_login();provider.params={**parse_qs(urlsplit(start['url']).query),'product':'console'}
    state=provider.params['state'][0];cookie=start['cookie']
    if failure=='expired':
        async with db.connection() as c:await c.execute("UPDATE console_login_transactions SET expires_at=clock_timestamp()-interval '1 second'")
    if failure=='nonce':provider.overrides={'nonce':'wrong'}
    if failure=='issuer':provider.overrides={'iss':'https://wrong.example'}
    if failure=='audience':provider.overrides={'aud':'wrong'}
    if failure=='replay':await auth.callback('synthetic-code',state,cookie)
    with pytest.raises(PlatformError):
        await auth.callback('synthetic-code','wrong' if failure=='state' else state,'wrong' if failure=='cookie' else cookie)
    async with db.connection() as c:
        assert (await (await c.execute('SELECT count(*) AS n FROM console_sessions')).fetchone())['n']==0


@pytest.mark.asyncio
async def test_same_email_or_wrong_issuer_subject_does_not_gain_acl(console):
    auth,provider=console
    _,principal=await authenticated(console)
    provider.overrides={'sub':'different-subject'}
    assert (await signed_login(console))['status']=='pending'
    provider.overrides={}
    assert (await signed_login(console))['status']=='authenticated'


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation',[
    "UPDATE console_admins SET active=false",
    "UPDATE console_admins SET authorization_epoch=authorization_epoch+1",
    "UPDATE console_sessions SET revoked_at=clock_timestamp()",
    "UPDATE console_sessions SET expires_at=clock_timestamp()-interval '1 second'",
])
async def test_session_revocation_and_expiry_are_checked_on_every_read_and_write(console,db,mutation):
    auth,_=console
    result,principal=await authenticated(console)
    async with db.connection() as c:await c.execute(mutation)
    with pytest.raises(PlatformError):await auth.authenticate(result['session_token'])
    async with db.connection() as c:
        with pytest.raises(PlatformError):await auth.validate(c,principal,'plans:publish')


@pytest.mark.asyncio
async def test_validate_is_readonly_and_rechecks_role_and_csrf(console,db):
    auth,_=console
    _,principal=await authenticated(console)
    async with db.connection() as c:
        await c.execute('SET TRANSACTION READ ONLY')
        assert (await auth.validate(c,principal,'console:read')).id==principal.id
    async with db.connection() as c:
        with pytest.raises(PlatformError):await auth.validate(c,replace(principal,csrf_token='forged'),'plans:publish')
    async with db.connection() as c:await c.execute("UPDATE console_admins SET role='viewer'")
    async with db.connection() as c:
        current=await auth.validate(c,principal,'console:read')
        assert current.role=='viewer' and current.permissions==frozenset({'console:read'})
        with pytest.raises(PlatformError):await auth.validate(c,principal,'execution:publish')


@pytest.mark.asyncio
async def test_logout_and_arbitrary_product_bearer_are_not_console_auth(console):
    auth,_=console
    for cookie in ['',None,'Bearer PRODUCT-TOKEN-CANARY','PRODUCT-TOKEN-CANARY']:
        with pytest.raises(PlatformError):await auth.authenticate(cookie)
    result,_=await authenticated(console)
    await auth.logout(result['session_token'])
    with pytest.raises(PlatformError):await auth.authenticate(result['session_token'])


@pytest.mark.asyncio
async def test_bootstrap_requires_verified_unexpired_request_capability_and_disabled_admin_stays_disabled(console,db):
    auth,_=console
    request=await signed_login(console)
    with pytest.raises(PlatformError):await auth.approve_request(request['request_id'],'admin',OperatorIdentity('ordinary',frozenset()))
    with pytest.raises(PlatformError):await auth.approve_request('0'*32,'admin',bootstrap())
    with pytest.raises(PlatformError):await auth.approve_request(request['request_id'],'superuser',bootstrap())
    approved=await auth.approve_request(request['request_id'],'admin',bootstrap())
    assert approved==await auth.approve_request(request['request_id'],'admin',bootstrap())
    await auth.disable_admin(approved['id'],bootstrap())
    assert (await signed_login(console))['status']=='pending'
    with pytest.raises(PlatformError):await auth.approve_request(request['request_id'],'admin',bootstrap())


@pytest.mark.asyncio
@pytest.mark.parametrize('offset',[-361,61])
async def test_console_requires_fresh_signed_authentication_time(console,db,offset):
    auth,provider=console
    provider.overrides={'auth_time':int(time.time())+offset}
    with pytest.raises(PlatformError) as error:
        await signed_login(console)
    assert error.value.code=='console_authentication_stale'
    async with db.connection() as c:
        assert (await (await c.execute('SELECT count(*) AS n FROM console_login_requests')).fetchone())['n']==0
        assert (await (await c.execute('SELECT count(*) AS n FROM console_sessions')).fetchone())['n']==0


@pytest.mark.asyncio
async def test_fresh_signed_authentication_with_small_clock_skew_can_request_acl(console):
    _,provider=console
    provider.overrides={'auth_time':int(time.time())+30}
    assert (await signed_login(console))['status']=='pending'


@pytest.mark.asyncio
async def test_callback_concurrent_exchange_is_one_time_and_expired_request_cannot_be_approved(console,db):
    auth,provider=console
    start=await auth.begin_login();provider.params={**parse_qs(urlsplit(start['url']).query),'product':'console'}
    results=await asyncio.gather(*(auth.callback('synthetic-code',provider.params['state'][0],start['cookie']) for _ in range(2)),return_exceptions=True)
    assert sum(isinstance(result,dict) and result['status']=='pending' for result in results)==1
    assert sum(isinstance(result,PlatformError) for result in results)==1
    request=next(result for result in results if isinstance(result,dict))
    async with db.connection() as c:await c.execute("UPDATE console_login_requests SET expires_at=clock_timestamp()-interval '1 second'")
    with pytest.raises(PlatformError):await auth.approve_request(request['request_id'],'admin',bootstrap())


@pytest.mark.asyncio
@pytest.mark.parametrize('claims',[{}, {'auth_time':None}, {'auth_time':True}, {'auth_time':False},
    {'auth_time':'123'}, {'auth_time':123.5}])
async def test_console_exchange_requires_explicit_integral_auth_time_without_database(claims):
    from platform_service.console_config import ConsoleSettings
    from platform_service.identity.oidc import OIDC,digest
    from test_identity import challenge
    provider=Provider();provider.overrides=claims
    verifier='synthetic-verifier';nonce='synthetic-nonce'
    provider.params={'product':'console','nonce':[nonce],'code_challenge':[challenge(verifier)]}
    settings=ConsoleSettings(database_url='postgresql://unused@127.0.0.1/unused',public_url='https://central.example',
        issuer=provider.issuer,client_id='oidc-client',client_secret='fixture-client-secret',db_schema='central')
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider.handler)) as client:
        oidc=OIDC(settings.oidc_settings(),client)
        with pytest.raises(PlatformError) as error:
            await oidc.exchange('console','synthetic-code',verifier,digest(nonce),require_auth_time=True)
    assert error.value.code=='identity_token_invalid'


@pytest.mark.asyncio
async def test_product_exchange_preserves_missing_auth_time_compatibility_without_database():
    from platform_service.console_config import ConsoleSettings
    from platform_service.identity.oidc import OIDC,digest
    from test_identity import challenge
    provider=Provider();verifier='synthetic-verifier';nonce='synthetic-nonce'
    provider.params={'product':'console','nonce':[nonce],'code_challenge':[challenge(verifier)]}
    settings=ConsoleSettings(database_url='postgresql://unused@127.0.0.1/unused',public_url='https://central.example',
        issuer=provider.issuer,client_id='oidc-client',client_secret='fixture-client-secret',db_schema='central')
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider.handler)) as client:
        result=await OIDC(settings.oidc_settings(),client).exchange('console','synthetic-code',verifier,digest(nonce))
    assert abs(result.authenticated_at-time.time())<5


@pytest.mark.asyncio
async def test_console_authorization_requests_zero_max_age(console):
    auth,_=console
    start=await auth.begin_login()
    params=parse_qs(urlsplit(start['url']).query)
    assert params['prompt']==['login'] and params['max_age']==['0']


@pytest.mark.asyncio
@pytest.mark.parametrize('claims',[{}, {'auth_time':None}, {'auth_time':True}, {'auth_time':False},
    {'auth_time':'123'}, {'auth_time':123.5}])
async def test_console_callback_requires_explicit_integral_signed_auth_time(console,db,claims):
    auth,provider=console
    start=await auth.begin_login()
    provider.params={**parse_qs(urlsplit(start['url']).query),'product':'console'}
    provider.overrides=claims
    with pytest.raises(PlatformError) as error:
        await auth.callback('synthetic-code',provider.params['state'][0],start['cookie'])
    assert error.value.code=='identity_token_invalid'
    async with db.connection() as c:
        for table in ('console_login_requests','console_sessions'):
            assert (await (await c.execute('SELECT count(*) AS n FROM '+table)).fetchone())['n']==0
