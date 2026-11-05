"""Static OIDC browser recovery pages preserve API errors and auth boundaries."""
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from platform_service.app import create_app
from platform_service.config import OIDCClient, Settings
from platform_service.errors import PlatformError
from test_identity import identity, challenge


def app_for(db, fixture):
    service, provider=fixture
    settings=Settings("postgresql://localhost/unused", "https://central.example",issuer=provider.issuer,
        oidc_clients={key:OIDCClient("oidc-client","fixture-secret") for key in ("easel","second")})
    return create_app(settings,database=db,identity=service),settings


def assert_static_page(response, *, status=400, forbidden=()):
    assert response.status_code==status
    assert response.headers['content-type'].startswith('text/html')
    assert '登录未完成' in response.text
    assert '回到 Easel' in response.text and '左下角' in response.text and '重新登录' in response.text
    assert response.headers['cache-control']=='no-store'
    assert response.headers['referrer-policy']=='no-referrer'
    assert response.headers['x-content-type-options']=='nosniff'
    policy=response.headers['content-security-policy']
    for required in ["default-src 'none'", "base-uri 'none'", "form-action 'none'", "frame-ancestors 'none'"]:
        assert required in policy
    assert '<script' not in response.text and '<form' not in response.text and 'http-equiv="refresh"' not in response.text
    for value in forbidden:
        assert value not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['missing_cookie','expired','wrong_state','missing_code'])
async def test_browser_callback_recovers_without_exchange_or_identity_side_effects(db,identity,monkeypatch,failure):
    service,_=identity
    flow=await service.begin_login('easel','desktop','http://127.0.0.1:37689/platform-auth/callback',challenge('x'*48),'LOCAL-STATE-CANARY')
    app,settings=app_for(db,identity)
    async def forbidden_exchange(*args,**kwargs):
        raise AssertionError('invalid callback must not exchange its authorization code')
    monkeypatch.setattr(service.oidc,'exchange',forbidden_exchange)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=settings.public_url) as client:
        authorized=await client.get('/oidc/easel/authorize',params={'transaction_id':flow['transaction_id']},headers={'accept':'text/html'})
        assert authorized.status_code==302
        state=parse_qs(urlsplit(authorized.headers['location']).query)['state'][0]
        cookie=client.cookies.get('central_oidc_easel')
        if failure=='missing_cookie':client.cookies.clear()
        if failure=='expired':
            async with db.connection() as c:
                await c.execute("UPDATE identity_transactions SET expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",(flow['transaction_id'],))
        response=await client.get('/oidc/easel/callback',params={
            'code':'' if failure=='missing_code' else 'RAW-CODE-CANARY',
            'state':'WRONG-STATE-CANARY' if failure=='wrong_state' else state,
            'error_description':'PRIVATE-ERROR-DETAIL-CANARY',
        },headers={'accept':'text/html,application/xhtml+xml;q=0.9,*/*;q=0.8'})
        assert_static_page(response,forbidden=('RAW-CODE-CANARY','WRONG-STATE-CANARY','PRIVATE-ERROR-DETAIL-CANARY',state,cookie,flow['transaction_id']))
    async with db.connection() as c:
        row=await (await c.execute('SELECT consumed FROM identity_transactions WHERE id=%s',(flow['transaction_id'],))).fetchone()
        assert row['consumed'] is False
        for table in ('identity_handoffs','identity_sessions'):
            assert (await (await c.execute('SELECT count(*) AS n FROM '+table)).fetchone())['n']==0


@pytest.mark.asyncio
@pytest.mark.parametrize('path,parameters',[
    ('/oidc/easel/authorize',{'transaction_id':'UNKNOWN-TX-CANARY'}),
    ('/oidc/easel/callback',{'state':'STATE-CANARY','error':'unknown_provider_error','error_description':'<script>PRIVATE-CANARY</script>','redirect_uri':'https://hostile.invalid/PRIVATE-CANARY'}),
    ('/oidc/evil<script>PRIVATE-CANARY/authorize',{'transaction_id':'UNKNOWN-TX-CANARY'}),
])
async def test_unknown_transaction_provider_error_and_injected_product_are_static(db,identity,path,parameters):
    app,settings=app_for(db,identity)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=settings.public_url) as client:
        response=await client.get(path,params=parameters,headers={'accept':'text/html'})
    assert_static_page(response,forbidden=('PRIVATE-CANARY','UNKNOWN-TX-CANARY','STATE-CANARY','unknown_provider_error','hostile.invalid'))


@pytest.mark.asyncio
@pytest.mark.parametrize('route',['authorize','callback'])
async def test_unknown_platform_failure_keeps_status_without_exposing_code(db,identity,monkeypatch,route):
    service,_=identity
    async def fail(*args,**kwargs):raise PlatformError(503,'private_upstream_detail_canary',retryable=True)
    monkeypatch.setattr(service,route,fail)
    app,settings=app_for(db,identity)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=settings.public_url) as client:
        response=await client.get('/oidc/easel/'+route,params={'transaction_id':'TX','code':'CODE','state':'STATE'},headers={'accept':'text/html'})
    assert_static_page(response,status=503,forbidden=('private_upstream_detail_canary',))


@pytest.mark.asyncio
@pytest.mark.parametrize('accept',['application/json','*/*','text/html;q=0,application/json'])
async def test_api_oidc_requests_keep_original_json_error_contract(db,identity,accept):
    app,settings=app_for(db,identity)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=settings.public_url) as client:
        response=await client.get('/oidc/easel/callback',params={'code':'RAW-CODE-CANARY','state':'STATE-CANARY'},headers={'accept':accept})
    assert response.status_code==400
    assert response.headers['content-type'].startswith('application/json')
    error=response.json()['error']
    assert set(error)=={'code','retryable','correlation_id'}
    assert error['code']=='invalid_request' and error['retryable'] is False
    assert len(error['correlation_id'])==16
    assert 'RAW-CODE-CANARY' not in response.text and 'STATE-CANARY' not in response.text


@pytest.mark.asyncio
async def test_html_accept_does_not_change_other_routes_or_methods(db,identity):
    app,settings=app_for(db,identity)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=settings.public_url) as client:
        for method,path in [('GET','/v2/me'),('POST','/v2/auth/refresh'),('POST','/oidc/easel/callback'),('GET','/oidc/easel/other')]:
            response=await client.request(method,path,headers={'accept':'text/html'},json={})
            assert response.status_code in (400,401,404,405)
            assert response.headers['content-type'].startswith('application/json')
