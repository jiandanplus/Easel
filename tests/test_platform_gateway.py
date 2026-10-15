import asyncio,json
import httpx,pytest
from easel_platform import Settings,create_app

@pytest.fixture
def env(tmp_path):
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json={'id':'r','status':'completed','usage':{'input_tokens':7,'output_tokens':3}})
    app=create_app(Settings(tmp_path/'db','https://provider.test/v1','secret-key','real','alias'),transport=httpx.MockTransport(handler),clock=lambda:1000)
    s=app.state.store;u=s.register('a@example.test','long-password-123')
    s.create_plan('p','trial',100000,1,1,['alias']);g=s.grant('a@example.test','p','grant')['id']
    return app,u,g,calls

async def client(app):return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test')

def test_json_scoped_replay_and_account_boundary(env):
    async def go():
        app,u,g,calls=env
        async with await client(app) as c:
            h={'Authorization':'Bearer '+u['access_token']}
            r=await c.post('/v1/task-tokens',headers=h,json={'model':'alias'});assert r.status_code==200
            token=r.json();assert token['base_url_path']=='/v1'
            th={'Authorization':'Bearer '+token['token'],'Idempotency-Key':'one'}
            assert (await c.get('/v1/account',headers=th)).status_code==401
            r=await c.post('/v1/responses',headers=th,json={'model':'alias','input':'hi'});assert r.status_code==200
            assert (await c.post('/v1/responses',headers=th,json={'model':'alias','input':'hi'})).status_code==409
            assert len(calls)==1 and calls[0].headers['authorization']=='Bearer secret-key'
            assert json.loads(calls[0].content)['model']=='real'
            usage=(await c.get('/v1/usage',headers=h)).json();assert usage['used_tokens']==10
            assert (await c.get('/v1/entitlements',headers=h)).json()['entitlements'][0]['remaining_tokens']==99990
    asyncio.run(go())

def test_no_dispatch_without_authority(env):
    async def go():
        app,u,g,calls=env
        async with await client(app) as c:
            assert (await c.post('/v1/responses',json={'model':'alias'})).status_code==401
            h={'Authorization':'Bearer '+u['access_token']}
            th={'Authorization':'Bearer '+(await c.post('/v1/task-tokens',headers=h,json={'model':'alias'})).json()['token']}
            assert (await c.post('/v1/responses',headers=th,json={'model':'wrong'})).status_code==403
            app.state.store.revoke_grant(g)
            assert (await c.post('/v1/responses',headers=th,json={'model':'alias'})).status_code==403
            assert calls==[]
    asyncio.run(go())

def test_bad_upstream_port_safe(tmp_path):
    for url in ['https://provider.test:bad/v1','https://provider.test:99999/v1']:
        with pytest.raises(ValueError,match='Invalid upstream configuration'):Settings(tmp_path/'db',url)

class Bytes(httpx.AsyncByteStream):
    def __init__(self,chunks,fail=None):self.chunks=chunks;self.fail=fail;self.closed=False
    async def __aiter__(self):
        for chunk in self.chunks:yield chunk
        if self.fail:raise self.fail
    async def aclose(self):self.closed=True

def test_sse_utf8_tool_events_and_terminal_accounting(env):
    async def go():
        app,u,g,calls=env
        delta={'type':'response.output_text.delta','delta':'你好'}
        tool={'type':'response.function_call_arguments.delta','delta':'{"x":1}'}
        completed={'type':'response.completed','response':{'status':'completed','usage':{'input_tokens':9,'output_tokens':4}}}
        wire=b''.join(('data: '+json.dumps(x,ensure_ascii=False)+'\r\n\r\n').encode() for x in (delta,tool,completed))
        source=Bytes([wire[i:i+1] for i in range(len(wire))])
        app.state.gateway.transport=httpx.MockTransport(lambda r:httpx.Response(200,stream=source))
        async with await client(app) as c:
            h={'Authorization':'Bearer '+u['access_token']}
            t=(await c.post('/v1/task-tokens',headers=h,json={'model':'alias'})).json()['token']
            r=await c.post('/v1/responses',headers={'Authorization':'Bearer '+t},json={'model':'alias','stream':True})
            assert r.status_code==200 and '你好' in r.text and 'function_call_arguments' in r.text and 'response.completed' in r.text
            assert source.closed
            assert (await c.get('/v1/usage',headers=h)).json()['used_tokens']==13
    asyncio.run(go())

@pytest.mark.parametrize('mode',['missing','invalid','oversize','timeout','redirect','json_missing','json_oversize'])
def test_uncertain_upstream_keeps_reservation_and_sanitizes(env,mode):
    async def go():
        app,u,g,calls=env
        secret='secret-key raw-private-upstream-body'
        if mode=='redirect':response=httpx.Response(302,headers={'location':'https://other.test/'+secret})
        elif mode=='json_missing':response=httpx.Response(200,json={'error':secret})
        elif mode=='json_oversize':response=httpx.Response(200,stream=Bytes([b'x'*(2*1024*1024+1)]))
        else:
            event={'type':'response.completed','response':{'usage':{'input_tokens':True,'output_tokens':1}}} if mode=='invalid' else {'type':'response.output_text.delta','delta':'hi'}
            chunks=[('data: '+json.dumps(event)+'\n\n').encode()]
            if mode=='oversize':chunks=[b'x'*(256*1024+1)]
            response=httpx.Response(200,stream=Bytes(chunks,httpx.ReadTimeout(secret) if mode=='timeout' else None))
        seen=[]
        def handler(r):seen.append(r);return response
        app.state.gateway.transport=httpx.MockTransport(handler)
        async with await client(app) as c:
            h={'Authorization':'Bearer '+u['access_token']}
            t=(await c.post('/v1/task-tokens',headers=h,json={'model':'alias'})).json()['token']
            r=await c.post('/v1/responses',headers={'Authorization':'Bearer '+t},json={'model':'alias','stream':mode not in ('json_missing','json_oversize','redirect')})
            assert secret not in r.text and 'response.completed' not in r.text
            assert len(seen)==1
            usage=(await c.get('/v1/usage',headers=h)).json()
            assert usage['used_tokens']==0 and usage['reserved_tokens']>0 and usage['pending_reconciliation']==1
    asyncio.run(go())

@pytest.mark.parametrize('reason',['no_grant','session_revoke','session_expire','task_expire','quota','concurrency','body_bound','output_bound','stream_mode'])
def test_admission_rejections_never_dispatch(env,reason):
    async def go():
        app,u,g,calls=env
        async with await client(app) as c:
            h={'Authorization':'Bearer '+u['access_token']}
            if reason=='no_grant':
                app.state.store.revoke_grant(g)
                assert (await c.post('/v1/task-tokens',headers=h,json={'model':'alias'})).status_code==403
                return
            t=(await c.post('/v1/task-tokens',headers=h,json={'model':'alias'})).json()['token']
            body={'model':'alias'}
            if reason=='session_revoke':app.state.store.revoke_session(u['account']['id'],u['session_id'])
            if reason in ('session_expire','task_expire'):app.state.store.clock=lambda:4000000
            if reason=='quota':
                with app.state.store.transaction() as db:db.execute('UPDATE grants SET token_quota=1')
            if reason=='concurrency':app.state.ledger.reserve(owner=u['account']['id'],grant=g,key='busy',body_hash='h',tokens=10)
            if reason=='body_bound':body['input']='x'*(1024*1024)
            if reason=='output_bound':body['max_output_tokens']=4097
            if reason=='stream_mode':body['stream']='yes'
            r=await c.post('/v1/responses',headers={'Authorization':'Bearer '+t},json=body)
            assert r.status_code in (400,401,403,429)
            assert calls==[]
    asyncio.run(go())

def test_auth_body_bound_and_refresh_invalidates_live_access(env):
    async def go():
        app,u,g,calls=env
        async with await client(app) as c:
            r=await c.post('/v1/auth/login',content=b'x'*16385);assert r.status_code==400
            new=(await c.post('/v1/auth/refresh',json={'refresh_token':u['refresh_token']})).json()
            assert (await c.get('/v1/account',headers={'Authorization':'Bearer '+u['access_token']})).status_code==401
            assert (await c.get('/v1/account',headers={'Authorization':'Bearer '+new['access_token']})).status_code==200
    asyncio.run(go())

def test_cancellation_closes_before_slot_release(env):
    async def go():
        app,u,g,calls=env
        l=app.state.ledger
        r=l.reserve(owner=u['account']['id'],grant=g,key='cancel',body_hash='h',tokens=100);l.dispatch(r['id'])
        entered=asyncio.Event();closed=[]
        class Blocking(httpx.AsyncByteStream):
            async def __aiter__(self):
                entered.set();await asyncio.Event().wait()
                yield b''
            async def aclose(self):
                with app.state.store.transaction() as db:active=db.execute('SELECT active FROM requests WHERE id=?',(r['id'],)).fetchone()[0]
                closed.append(active)
        hc=httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(200,stream=Blocking())))
        upstream=await hc.send(hc.build_request('POST','https://fake.test'),stream=True)
        async def consume():
            async for _ in app.state.gateway.stream(upstream,hc,r['id']):pass
        task=asyncio.create_task(consume());await entered.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert closed==[1] and l.snapshot(u['account']['id'])['pending_reconciliation']==1
    asyncio.run(go())


def test_event_name_cannot_smuggle_clean_terminal_without_accounting(env):
    async def go():
        app,u,g,calls=env
        wire=b'event: response.completed\ndata: {"type":"response.output_text.delta","delta":"secret-key"}\n\n'
        app.state.gateway.transport=httpx.MockTransport(lambda r:httpx.Response(200,stream=Bytes([wire])))
        async with await client(app) as c:
            t=(await c.post('/v1/task-tokens',headers={'Authorization':'Bearer '+u['access_token']},json={'model':'alias'})).json()['token']
            r=await c.post('/v1/responses',headers={'Authorization':'Bearer '+t},json={'model':'alias','stream':True})
            assert 'response.completed' not in r.text and 'secret-key' not in r.text
    asyncio.run(go())

def test_no_ambient_proxy_redirect_or_client_authorization_forwarded(env,monkeypatch):
    async def go():
        app,u,g,calls=env
        original=httpx.AsyncClient;options=[]
        def factory(*args,**kwargs):options.append(kwargs);return original(*args,**kwargs)
        async with await client(app) as c:
            monkeypatch.setattr('easel_platform.gateway.httpx.AsyncClient',factory)
            h={'Authorization':'Bearer '+u['access_token']}
            t=(await c.post('/v1/task-tokens',headers=h,json={'model':'alias'})).json()['token']
            await c.post('/v1/responses',headers={'Authorization':'Bearer '+t},json={'model':'alias'})
        assert options[0]['trust_env'] is False and options[0]['follow_redirects'] is False
        assert calls[0].headers['authorization']=='Bearer secret-key'
    asyncio.run(go())

@pytest.mark.parametrize('extra',[{'input':[{'role':'user','content':[{'type':'input_image','image_url':'https://image.test/x'}]}]},{'input':[{'type':'item_reference','id':'stored'}]},{'input':[{'type':'input_file','file_id':'file-x'}]},{'tools':[{'type':'web_search'}]},{'tools':[{'type':'file_search','vector_store_ids':['vs']}]},{'previous_response_id':'r'},{'conversation':'c'},{'prompt':{'id':'p'}},{'background':True}])
def test_unbounded_context_or_hosted_tools_rejected_before_dispatch(env,extra):
    async def go():
        app,u,g,calls=env
        async with await client(app) as c:
            t=(await c.post('/v1/task-tokens',headers={'Authorization':'Bearer '+u['access_token']},json={'model':'alias'})).json()['token']
            r=await c.post('/v1/responses',headers={'Authorization':'Bearer '+t},json={'model':'alias','input':'hi',**extra})
            assert r.status_code==400 and r.json()['error']['code']=='unsupported_request_shape'
            assert calls==[]
    asyncio.run(go())


def test_pre_dispatch_client_failure_releases_reservation(env,monkeypatch):
    async def go():
        app,u,g,calls=env
        async with await client(app) as c:
            h={'Authorization':'Bearer '+u['access_token']}
            t=(await c.post('/v1/task-tokens',headers=h,json={'model':'alias'})).json()['token']
            def broken(*a,**k):raise ValueError('secret-key')
            monkeypatch.setattr('easel_platform.gateway.httpx.AsyncClient',broken)
            r=await c.post('/v1/responses',headers={'Authorization':'Bearer '+t},json={'model':'alias'})
            assert r.status_code==502 and 'secret-key' not in r.text
            assert app.state.ledger.snapshot(u['account']['id'])['reserved_tokens']==0
            assert calls==[]
    asyncio.run(go())


@pytest.mark.parametrize('change',['rotate','expire'])
def test_mint_revalidates_access_after_delayed_body(env,change):
    async def go():
        app,u,g,calls=env
        async with await client(app) as c:
            h={'Authorization':'Bearer '+u['access_token']}
            issued=(await c.post('/v1/task-tokens',headers=h,json={'model':'alias'})).json()['token']
            async def body():
                yield b'{"model":'
                if change=='rotate':app.state.store.refresh(u['refresh_token'])
                else:app.state.store.clock=lambda:1901
                yield b'"alias"}'
            r=await c.post('/v1/task-tokens',headers=h,content=body())
            assert r.status_code==401
            r=await c.post('/v1/responses',headers={'Authorization':'Bearer '+issued},json={'model':'alias'})
            assert r.status_code==200
    asyncio.run(go())

@pytest.mark.parametrize('old_state',['zero','exhausted','pending','busy'])
def test_mint_selects_available_replacement_grant(env,old_state):
    async def go():
        app,u,g,calls=env;s=app.state.store;l=app.state.ledger;owner=u['account']['id']
        if old_state=='zero':
            with s.transaction() as db:db.execute('UPDATE grants SET token_quota=0 WHERE id=?',(g,))
        else:
            r=l.reserve(owner=owner,grant=g,key='old',body_hash='h',tokens=100000 if old_state!='busy' else 1)
            if old_state=='exhausted':l.dispatch(r['id']);l.settle(r['id'],100000,0)
            if old_state=='pending':l.pending(r['id'])
        with s.transaction() as db:db.execute('UPDATE grants SET ends_at=2000 WHERE id=?',(g,))
        new=s.grant('a@example.test','p','new')['id']
        async with await client(app) as c:
            t=(await c.post('/v1/task-tokens',headers={'Authorization':'Bearer '+u['access_token']},json={'model':'alias'})).json()['token']
            assert app.state.gateway.authorize(t)['grant_id']==new
            assert (await c.post('/v1/responses',headers={'Authorization':'Bearer '+t},json={'model':'alias'})).status_code==200
    asyncio.run(go())

@pytest.mark.parametrize('stream',[False,True])
def test_encoded_upstream_rejected_without_read_or_decode(env,stream,monkeypatch):
    import gzip
    async def go():
        app,u,g,calls=env
        compressed=gzip.compress(b'x'*(3*1024*1024));assert len(compressed)<8192
        class Unread(Bytes):
            async def __aiter__(self):
                pytest.fail('encoded stream must not be consumed')
                yield b''
        source=Unread([compressed])
        original_decoder=httpx.Response._get_content_decoder
        def decoder(response,*a,**k):
            if response.headers.get('content-encoding')=='gzip':pytest.fail('decoder must not be called')
            return original_decoder(response,*a,**k)
        monkeypatch.setattr(httpx.Response,'_get_content_decoder',decoder)
        def handler(r):
            assert r.headers['accept-encoding']=='identity'
            return httpx.Response(200,headers={'Content-Encoding':'gzip'},stream=source)
        app.state.gateway.transport=httpx.MockTransport(handler)
        async with await client(app) as c:
            t=(await c.post('/v1/task-tokens',headers={'Authorization':'Bearer '+u['access_token']},json={'model':'alias'})).json()['token']
            r=await c.post('/v1/responses',headers={'Authorization':'Bearer '+t},json={'model':'alias','stream':stream})
            assert r.status_code==502 and r.json()['error']['code']=='invalid_upstream_response'
            assert source.closed and app.state.ledger.snapshot(u['account']['id'])['pending_reconciliation']==1
    asyncio.run(go())

@pytest.mark.parametrize('terminal',[{'status':'failed'},{'status':'completed','error':{'message':'secret-key'}}])
def test_contradictory_completed_terminal_is_sanitized(env,terminal):
    async def go():
        app,u,g,calls=env
        wire=('data: '+json.dumps({'type':'response.completed','response':{**terminal,'usage':{'input_tokens':1,'output_tokens':1}}})+'\n\n').encode()
        app.state.gateway.transport=httpx.MockTransport(lambda r:httpx.Response(200,stream=Bytes([wire])))
        async with await client(app) as c:
            t=(await c.post('/v1/task-tokens',headers={'Authorization':'Bearer '+u['access_token']},json={'model':'alias'})).json()['token']
            r=await c.post('/v1/responses',headers={'Authorization':'Bearer '+t},json={'model':'alias','stream':True})
            assert 'response.completed' not in r.text and 'secret-key' not in r.text and 'upstream_error' in r.text
            assert app.state.ledger.snapshot(u['account']['id'])['pending_reconciliation']==1
    asyncio.run(go())


@pytest.mark.parametrize('condition,code',[('quota','quota_exceeded'),('busy','concurrency_exceeded')])
def test_mint_no_eligible_grant_cannot_use_foreign_budget(env,condition,code):
    async def go():
        app,u,g,calls=env;s=app.state.store
        s.register('foreign@example.test','long-password-123');s.grant('foreign@example.test','p','foreign')
        if condition=='quota':
            with s.transaction() as db:db.execute('UPDATE grants SET token_quota=0 WHERE id=?',(g,))
        else:app.state.ledger.reserve(owner=u['account']['id'],grant=g,key='busy',body_hash='h',tokens=1)
        async with await client(app) as c:
            r=await c.post('/v1/task-tokens',headers={'Authorization':'Bearer '+u['access_token']},json={'model':'alias'})
            assert r.status_code==429 and r.json()['error']['code']==code and calls==[]
    asyncio.run(go())


def test_mint_checks_actual_access_inside_its_transaction(env):
    from easel_platform.auth import PlatformError
    app,u,g,calls=env;s=app.state.store
    s.authenticate(u['access_token']);s.refresh(u['refresh_token'])
    with pytest.raises(PlatformError) as error:app.state.gateway.mint(u['access_token'],'alias')
    assert error.value.code=='invalid_authentication'


def test_native_namespaced_client_function_tools_are_bounded_text(env):
    async def go():
        app, user, grant, calls = env
        async with await client(app) as connection:
            header = {"Authorization": "Bearer " + user["access_token"]}
            token = (await connection.post("/v1/task-tokens", headers=header, json={"model": "alias"})).json()["token"]
            scoped = {"Authorization": "Bearer " + token}
            namespace = {"type": "namespace", "name": "functions", "description": "Local tools", "tools": [{"type": "function", "name": "shell", "parameters": {"type": "object"}}]}
            response = await connection.post("/v1/responses", headers=scoped, json={"model": "alias", "input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello"}]}], "tools": [namespace]})
            assert response.status_code == 200
            assert len(calls) == 1
            for invalid in ([{"type": "namespace", "name": "functions", "tools": [{"type": "web_search"}]}], [{"type": "namespace", "name": "functions", "tools": "invalid"}], [{"type": "web_search"}], [{"type": "namespace", "name": "functions", "tools": [{"type": "namespace", "name": "nested", "tools": [{"type": "function", "name": "shell"}]}]}]):
                rejected = await connection.post("/v1/responses", headers=scoped, json={"model": "alias", "input": "hello", "tools": invalid})
                assert rejected.status_code == 400
            assert len(calls) == 1
    asyncio.run(go())
