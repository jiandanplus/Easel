"""Access failures cannot prove refresh failure; no real credentials or APIs."""
import json
import time

import httpx
import pytest

from easel.platform.central import CentralClient
from easel.platform.client import PlatformError
from easel.platform.media import MediaClient
from tests.test_central_platform_client import auth
from tests.test_central_media_host import ORIGIN,definition


def clients(root,handler,*,expired=False):
    central=CentralClient(root,transport=httpx.MockTransport(handler),url=ORIGIN)
    value=auth()
    if expired:value['expires_at']=0
    central.save_auth(value)
    return central,MediaClient(root,central=central)


def fresh():
    value=auth();value.update(access_token='FRESH_ACCESS_SYNTHETIC_SECRET',refresh_token='FRESH_REFRESH_SYNTHETIC_SECRET')
    return value


@pytest.mark.parametrize('caller',['central','media'])
@pytest.mark.parametrize('code',['session_invalid','invalid_authentication'])
def test_get_access_401_renews_once_and_retries_only_original_get(tmp_path,caller,code):
    calls=[]
    def handler(request):
        calls.append((request.method,request.url.path,request.headers.get('authorization')))
        if request.url.path=='/v2/auth/refresh':return httpx.Response(200,json=fresh())
        if request.headers.get('authorization')=='Bearer ACCESS-CENTRAL-SECRET':return httpx.Response(401,json={'error':{'code':code}})
        if request.url.path=='/v2/me':return httpx.Response(200,json={'scope':auth()['scope'],'account':auth()['account']})
        return httpx.Response(200,json={'models':[definition()]})
    central,media=clients(tmp_path,handler)
    result=central.status()if caller=='central'else media.catalog()
    assert result.get('authenticated',True)and central.store.read()['auth']['refresh_token']==fresh()['refresh_token']
    assert len([x for x in calls if x[0]=='POST'])==1
    assert calls[0][1]==calls[2][1]=='/v2/me'
    assert all(path in{'/v2/auth/refresh','/v2/me','/v2/products/easel/media-models'}for _,path,_ in calls)


def test_access_401_on_non_get_never_reposts_or_refreshes_and_preserves_refresh(tmp_path):
    calls=[]
    def handler(request):
        calls.append((request.method,request.url.path))
        return httpx.Response(401,json={'error':{'code':'session_invalid'}})
    central,_=clients(tmp_path,handler)
    with pytest.raises(PlatformError):central.call('POST','/v2/orders',{'offer_id':'fixture','channel':'wechat','idempotency_key':'one'})
    assert calls==[('POST','/v2/orders')]
    assert central.store.read()['auth']['refresh_token']=='REFRESH-CENTRAL-SECRET'


def test_second_get_401_is_not_a_second_refresh_or_proof_to_delete_refresh(tmp_path):
    calls=[]
    def handler(request):
        calls.append((request.method,request.url.path))
        if request.url.path.endswith('/refresh'):return httpx.Response(200,json=fresh())
        return httpx.Response(401,json={'error':{'code':'session_invalid'}})
    central,_=clients(tmp_path,handler)
    result=central.status()
    assert not result['authenticated']and result['scope']==auth()['scope']
    assert calls==[('GET','/v2/me'),('POST','/v2/auth/refresh'),('GET','/v2/me')]
    assert central.store.read()['auth']['refresh_token']==fresh()['refresh_token']


@pytest.mark.parametrize('caller',['central','media'])
def test_refresh_explicit_401_still_clears_auth_and_guard(tmp_path,caller):
    calls=[]
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(401,json={'error':{'code':'session_invalid'}})
    central,media=clients(tmp_path,handler,expired=True)
    if caller=='central':assert not central.status()['authenticated']
    else:
        with pytest.raises(PlatformError):media.catalog()
    state=central.store.read()
    assert'auth'not in state and'refresh_guard'not in state
    assert calls==['/v2/auth/refresh']


@pytest.mark.parametrize('fault',['timeout','truncated','bad_scope'])
def test_unknown_rotation_is_durable_and_restart_never_replays_old_refresh(tmp_path,fault):
    calls=[]
    def handler(request):
        calls.append(request.url.path)
        if fault=='timeout':raise httpx.ReadTimeout('synthetic committed but response lost',request=request)
        if fault=='truncated':return httpx.Response(200,content=b'{"access_token":',headers={'content-type':'application/json'})
        value=fresh();value['scope']['authorization_epoch']+=1
        return httpx.Response(200,json=value)
    central,media=clients(tmp_path,handler,expired=True)
    before=central.store.read()['auth']
    with pytest.raises(PlatformError):media.catalog()
    saved=central.store.read()
    assert saved['auth']==before and saved['refresh_guard']['state']in{'sending','uncertain'}
    assert saved['refresh_guard']['scope_hash']and saved['refresh_guard']['refresh_hash']
    # New instance models process restart; status without network remains honest.
    restarted=CentralClient(tmp_path,transport=httpx.MockTransport(handler),url=ORIGIN)
    status=restarted.status(verify=False)
    assert not status['authenticated']and status['scope']==before['scope']and status['error']['code']=='refresh_uncertain'
    assert not restarted.status()['authenticated']
    with pytest.raises(PlatformError):MediaClient(tmp_path,central=restarted).catalog()
    assert calls==['/v2/auth/refresh']


@pytest.mark.parametrize('fault',[httpx.ConnectError,httpx.ConnectTimeout])
@pytest.mark.parametrize('caller',['central','bounded','media'])
def test_refresh_that_never_connected_keeps_the_login_and_retries(tmp_path,caller,fault):
    calls=[];down=[True]
    def handler(request):
        calls.append(request.url.path)
        if request.url.path=='/v2/auth/refresh':
            if down[0]:raise fault('synthetic connection never opened',request=request)
            return httpx.Response(200,json=fresh())
        if request.url.path=='/v2/me':return httpx.Response(200,json={'scope':auth()['scope'],'account':auth()['account']})
        return httpx.Response(200,json={'models':[definition()]})
    central,media=clients(tmp_path,handler,expired=True)
    if caller=='bounded':central.absolute_deadline=time.monotonic()+5
    before=central.store.read()['auth']
    with pytest.raises(PlatformError)as error:
        if caller=='media':media.catalog()
        else:
            with central.store.locked():central._access(central.store.read())
    assert error.value.code!='refresh_uncertain'
    saved=central.store.read()
    assert saved['auth']==before and'refresh_guard'not in saved
    down[0]=False
    if caller=='media':media.catalog()
    else:assert central.status()['authenticated']
    assert central.store.read()['auth']['refresh_token']==fresh()['refresh_token']
    assert calls.count('/v2/auth/refresh')==2


def test_guard_write_failure_happens_before_any_post(tmp_path,monkeypatch):
    calls=[]
    central,media=clients(tmp_path,lambda r:calls.append(r)or httpx.Response(200,json=fresh()),expired=True)
    before=central.store.path.read_bytes()
    def fail(value):raise OSError('synthetic marker disk failure')
    monkeypatch.setattr(central.store,'write',fail)
    with pytest.raises(PlatformError):media.catalog()
    assert calls==[]and central.store.path.read_bytes()==before


def test_final_auth_save_failure_keeps_prepost_guard_and_restart_does_not_replay(tmp_path,monkeypatch):
    calls=[]
    central,media=clients(tmp_path,lambda r:calls.append(r.url.path)or httpx.Response(200,json=fresh()),expired=True)
    write=central.store.write
    def fail_final(value):
        if'refresh_guard'not in value:raise OSError('synthetic final disk failure')
        return write(value)
    monkeypatch.setattr(central.store,'write',fail_final)
    with pytest.raises(PlatformError):media.catalog()
    state=central.store.read()
    assert state['auth']['refresh_token']=='REFRESH-CENTRAL-SECRET'and state['refresh_guard']['state']in{'sending','uncertain'}
    restarted=CentralClient(tmp_path,transport=central.transport,url=ORIGIN)
    assert not restarted.status()['authenticated']and calls==['/v2/auth/refresh']


@pytest.mark.parametrize('action',['begin','logout'])
def test_explicit_user_reauthentication_or_local_logout_can_clear_uncertain_guard_without_refresh(tmp_path,action):
    calls=[];lost=[True]
    def handler(request):
        calls.append(request.url.path)
        if lost[0]:raise httpx.ReadTimeout('synthetic unknown',request=request)
        assert request.url.path=='/v2/auth/transactions'
        return httpx.Response(200,json={'authorize_url':ORIGIN+'/oidc/easel/authorize?fixture=1','expires_at':time.time()+300})
    central,media=clients(tmp_path,handler,expired=True)
    with pytest.raises(PlatformError):media.catalog()
    lost[0]=False
    if action=='begin':assert central.begin('http://127.0.0.1:40000/callback','challenge','state')['authorize_url']
    else:assert not central.logout()['authenticated']
    assert'auth'not in central.store.read()and'refresh_guard'not in central.store.read()
    assert calls.count('/v2/auth/refresh')==1


def test_diagnostic_records_have_only_five_safe_fields_and_never_credentials(tmp_path,caplog):
    def handler(request):raise httpx.ReadTimeout('body SECRET_PROVIDER_BODY',request=request)
    _,media=clients(tmp_path,handler,expired=True)
    with pytest.raises(PlatformError):media.catalog()
    rows=[json.loads(r.message)for r in caplog.records if r.name=='easel.auth']
    assert rows
    assert all(set(row)=={'code','status','path','scope_hash','time'}for row in rows)
    assert'CENTRAL-SECRET'not in caplog.text and'SECRET_PROVIDER_BODY'not in caplog.text


def test_catalog_get_repair_updates_host_snapshot_before_single_mint_and_paid_post(tmp_path):
    from tests.test_central_media_host import Fixture,setup,MODEL
    fixture=Fixture();calls=[]
    def handler(request):
        calls.append((request.method,request.url.path,request.headers.get('authorization')))
        if request.url.path=='/v2/auth/refresh':return httpx.Response(200,json=fresh())
        if request.url.path.endswith('/media-models')and request.headers['authorization']=='Bearer ACCESS-CENTRAL-SECRET':
            return httpx.Response(401,json={'error':{'code':'session_invalid'}})
        if request.url.path=='/v2/media/task-tokens':assert request.headers['authorization']=='Bearer '+fresh()['access_token']
        return fixture(request)
    central_client,host,_=setup(tmp_path,handler)
    result=host.execute('create_media',{'model':MODEL,'prompt':'synthetic'},'one')
    assert result['ok']
    assert len([r for r in calls if r[:2]==('POST','/v2/auth/refresh')])==1
    assert len([r for r in calls if r[:2]==('POST','/v2/media/task-tokens')])==1
    paid=[r for r in calls if r[:2]==('POST','/v2/media/jobs')]
    assert len(paid)==1 and paid[0][2]=='Bearer MEDIA-TASK-TOKEN-SECRET'


def test_task_token_401_never_renews_product_access_or_discards_product_auth(tmp_path):
    from tests.test_central_media_host import Fixture,setup,MODEL
    fixture=Fixture();calls=[]
    def handler(request):
        calls.append((request.method,request.url.path))
        if request.url.path=='/v2/media/jobs':return httpx.Response(401,json={'error':{'code':'session_invalid'}})
        return fixture(request)
    client,host,_=setup(tmp_path,handler)
    assert not host.execute('create_media',{'model':MODEL,'prompt':'synthetic'},'one')['ok']
    assert client.central.store.read()['auth']['refresh_token']=='REFRESH-CENTRAL-SECRET'
    assert calls.count(('POST','/v2/media/jobs'))==1
    assert('POST','/v2/auth/refresh')not in calls


def test_late_old_catalog_401_cannot_renew_or_mint_for_new_account(tmp_path):
    from tests.test_central_media_host import Fixture,setup,MODEL
    fixture=Fixture();holder={};calls=[]
    def handler(request):
        calls.append(request.method)
        if request.url.path.endswith('/media-models'):
            central=holder['client'].central;new=fresh();new['account']['id']='other';new['scope']['user_id']='other'
            with central.store.locked():central.store.write({'version':2,'url':ORIGIN,'auth':new})
            return httpx.Response(401,json={'error':{'code':'session_invalid'}})
        return fixture(request)
    client,host,_=setup(tmp_path,handler);holder['client']=client
    assert not host.execute('create_media',{'model':MODEL,'prompt':'synthetic'},'one')['ok']
    assert client.central.store.read()['auth']['account']['id']=='other'
    assert'POST'not in calls


def test_stale_guard_cannot_make_begin_discard_a_different_new_login(tmp_path):
    def lost(request):raise httpx.ReadTimeout('synthetic',request=request)
    central,media=clients(tmp_path,lost,expired=True)
    with pytest.raises(PlatformError):media.catalog()
    with central.store.locked():
        state=central._state();new=fresh();new['account']['id']='other';new['scope']['user_id']='other'
        state['auth']=new;central.store.write(state)
    before=central.store.path.read_bytes()
    from easel.harnesses.base import HarnessError
    with pytest.raises(HarnessError):central.begin('http://127.0.0.1:40000/callback','challenge','state')
    assert central.store.path.read_bytes()==before


def test_get_repair_does_not_extend_absolute_deadline_for_refresh_or_retry(tmp_path):
    import asyncio
    calls=[]
    class Stalled(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(.2)
            yield json.dumps(fresh()).encode()
    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith('/refresh'):return httpx.Response(200,stream=Stalled())
        return httpx.Response(401,json={'error':{'code':'session_invalid'}})
    central,media=clients(tmp_path,handler)
    started=time.monotonic()
    with pytest.raises(PlatformError)as error:media._snapshot(deadline=time.monotonic()+.02)
    assert error.value.code=='refresh_uncertain'and time.monotonic()-started<.08
    assert calls==['/v2/me','/v2/auth/refresh']
    assert central.store.read()['auth']['scope']==auth()['scope']
