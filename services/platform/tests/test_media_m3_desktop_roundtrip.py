"""Actual desktop host → ASGI central → isolated PG → original decoded media.

Accounts and provider HTTP responses are synthetic; no external network or key.
"""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
import hashlib
import json
import time
from types import SimpleNamespace

import httpx
import pytest
from b2_legacy_media_fixtures import legacy_media, legacy_db as db, legacy_dsn as test_dsn
from easel.harnesses.base import RunRequest
from easel.platform.central import CentralClient
from easel.platform.media import MediaClient, MediaHost
from platform_service.app import create_app
from platform_service.config import Settings
from platform_service.media_runtime import MediaRuntime
from test_media_av_artifacts import files
from test_media_audio_runtime import build_audio
from test_media_voice_runtime import build_voice, sample, A, B, C
from test_media_async_runtime import build as build_async


@asynccontextmanager
async def desktop(db, tmp_path, runtime, scope):
    token='synthetic-desktop-m3-access'
    async with db.connection() as c:
        await c.execute("INSERT INTO identity_tokens(token_hash,session_id,kind,expires_at) VALUES (%s,%s,'access',now()+interval '1 hour')",
            (hashlib.sha256(token.encode()).hexdigest(),scope.session_id))
    app=create_app(Settings('postgresql://localhost/unused','https://accounts.example'),
        database=db,identity=runtime.service.identity,media_runtime=runtime)
    loop=asyncio.get_running_loop()
    state=SimpleNamespace(calls=[],responses=[],after_response=None)
    workspace=tmp_path/'desktop';workspace.mkdir()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://accounts.example') as server:
            async def forward(req):
                state.calls.append((req.method,req.url.path))
                response=await server.request(req.method,req.url.path+('?' + req.url.query.decode() if req.url.query else ''),
                    headers=dict(req.headers),content=req.content)
                if req.url.path.startswith('/v2/media') and 'json' in response.headers.get('content-type',''):
                    state.responses.append((req.method,req.url.path,response.status_code,response.json()))
                if state.after_response:state.after_response(req,response)
                return httpx.Response(response.status_code,headers=response.headers,content=response.content,request=req)
            def bridge(req):return asyncio.run_coroutine_threadsafe(forward(req),loop).result(30)
            central=CentralClient(workspace,transport=httpx.MockTransport(bridge),url='https://accounts.example')
            auth={'access_token':token,'refresh_token':'synthetic-refresh','expires_at':time.time()+600,
                'refresh_expires_at':time.time()+1200,'session_id':scope.session_id,'scope':asdict(scope),
                'account':{'id':scope.user_id,'email':None,'email_verified':False}}
            central.save_auth(auth)
            client=MediaClient(workspace,central=central)
            request=RunRequest('Synthetic M3 acceptance',workspace,{},model='fixture-text',service_source='subscription',
                subscription_backend='central',account_id=scope.user_id,platform_url='https://accounts.example',
                central_scope=asdict(scope),invocation_key='accepted-turn',session_key='desktop-session',turn_id='desktop-turn')
            state.host=MediaHost(request,client=client);state.client=client;state.central=central
            state.workspace=workspace;state.server=server;state.auth=auth;state.request=request
            yield state
    finally:
        await runtime.close()
        await app.state.execution_runtime.close()


async def execute(state,name,args,tool='tool'):
    return await asyncio.to_thread(state.host.execute,name,args,tool)


def arguments(body):return {key:value for key,value in body.items() if key!='assets'}


async def download(state,job,path,raw):
    result=await execute(state,'download_media_artifact',{'job_id':job['id'],'artifact_id':job['artifacts'][0]['id'],'path':path},'download')
    assert result['ok'],(result,state.responses)
    assert (state.workspace/result['path']).read_bytes()==raw
    assert result['artifact']['sha256']==hashlib.sha256(raw).hexdigest()
    return result


async def counts(db,*,jobs,receipts):
    async with db.connection() as c:
        assert (await(await c.execute('SELECT count(*) n FROM media_jobs')).fetchone())['n']==jobs
        assert (await(await c.execute('SELECT count(*) n FROM media_receipts')).fetchone())['n']==receipts
        assert (await(await c.execute("SELECT count(*) n FROM credit_reservations WHERE operation='media'")).fetchone())['n']==jobs


async def foreign_identity(db,scope):
    foreign=replace(scope,user_id='other',tenant_id='foreign',billing_account_id='foreign-wallet',session_id='foreign-session')
    foreign_token='synthetic-foreign-access'
    async with db.connection() as c:
        await c.execute("INSERT INTO external_identities(issuer,subject,user_id) VALUES ('fixture','foreign-subject','other')")
        await c.execute("INSERT INTO identity_sessions(id,user_id,tenant_id,billing_account_id,product_id,client_id,authorization_epoch,issuer,subject,expires_at) VALUES ('foreign-session','other','foreign','foreign-wallet','easel','desktop',0,'fixture','foreign-subject',now()+interval '1 hour')")
        await c.execute("INSERT INTO identity_tokens(token_hash,session_id,kind,expires_at) VALUES (%s,'foreign-session','access',now()+interval '1 hour')",(hashlib.sha256(foreign_token.encode()).hexdigest(),))
    return foreign,foreign_token


@pytest.mark.asyncio
@pytest.mark.parametrize('transcribe',[False,True])
async def test_speech_or_empty_transcript_uploaded_downloaded_and_charged_once(db,tmp_path,files,monkeypatch,transcribe,legacy_media):
    seen=[];raw=files[0]['wav']
    def provider(req):
        seen.append(req)
        if transcribe:
            assert 'multipart/form-data' in req.headers['content-type'] and raw in req.content
            return httpx.Response(200,json={'text':''},headers={'x-request-id':'req_empty'})
        return httpx.Response(200,content=raw,headers={'content-type':'audio/wav','x-request-id':'req_speech'})
    runtime,ledger,scope,_,body=await build_audio(db,legacy_media,tmp_path,files,monkeypatch,provider,transcribe=transcribe)
    async with desktop(db,tmp_path,runtime,scope) as d:
        args=arguments(body)
        if transcribe:
            (d.workspace/'sample.wav').write_bytes(raw);args['assets']=['sample.wav']
        first=await execute(d,'create_media',args)
        assert first['ok'],(first,d.responses)
        job=first['job'];assert job['actual']==('1' if transcribe else '4')
        artifact_raw=(await runtime.artifact(scope,job['artifacts'][0]['id'],content=True))[0]
        if transcribe:assert json.loads(artifact_raw)=={'text':''}
        else:assert artifact_raw==raw
        await download(d,job,'outputs/transcript.json' if transcribe else 'outputs/speech.wav',artifact_raw)
        repeated=await asyncio.to_thread(MediaHost(d.request,client=d.client).execute,'create_media',args,'tool')
        assert repeated['ok'] and repeated['job']['id']==job['id']
        assert len(seen)==1 and d.calls.count(('POST','/v2/media/jobs'))==1
        assert d.calls.count(('POST','/v2/media/assets'))==int(transcribe)
        assert (await ledger.summary(scope))['spent']==job['actual']
        await counts(db,jobs=1,receipts=1)


@pytest.mark.asyncio
async def test_minimax_ready_voice_same_turn_has_independent_synthesis_budget(db,tmp_path,files,monkeypatch,legacy_media):
    seen=[]
    def provider(req):
        seen.append((req.method,req.url.path))
        if req.url.path.endswith('/upload'):return httpx.Response(200,json={'task_id':A,'file_task_id':A,'status':'SUCCESS'})
        if req.url.path.endswith('/voice_clone'):return httpx.Response(200,json={'task_id':B,'voice_task_id':B,'status':'SUCCESS'})
        return httpx.Response(200,json={'task_id':C,'status':'SUCCESS','data':{'status':2,'audio':files[0]['mp3'].hex()},'extra_info':{'audio_format':'mp3','usage_characters':5}})
    runtime,ledger,scope,_,body=await build_voice(db,legacy_media,tmp_path,files,monkeypatch,provider)
    async with desktop(db,tmp_path,runtime,scope) as d:
        (d.workspace/'voice.wav').write_bytes(sample())
        enrollment=await execute(d,'create_media',{**arguments(body),'assets':['voice.wav']},'enroll')
        assert enrollment['ok'],(enrollment,d.responses)
        voice=enrollment['job']['voice'];assert voice['state']=='ready'
        assert set(voice)=={'id','name','language','expires_at','state'}
        assert enrollment['job']['actual']=='1'
        _,foreign_token=await foreign_identity(db,scope)
        forbidden=await d.server.get('/v2/media/voices/'+voice['id'],headers={'authorization':'Bearer '+foreign_token})
        assert forbidden.status_code==404,forbidden.text
        args={'model':'fixture-synth','capability':'voice.synthesize','prompt':'hello','parameters':{},'voice_id':voice['id']}
        speech=await execute(d,'create_media',args,'speak')
        assert speech['ok'],(speech,d.responses)
        assert speech['job']['id']!=enrollment['job']['id']
        await runtime.run_once()
        done=await execute(d,'get_media_job',{'job_id':speech['job']['id']})
        assert done['ok'] and done['job']['actual']=='5',done
        await download(d,done['job'],'outputs/cloned.mp3',files[0]['mp3'])
        repeated=await execute(d,'create_media',args,'speak')
        assert repeated['ok'] and repeated['job']['id']==speech['job']['id']
        assert d.calls.count(('POST','/v2/media/task-tokens'))==2
        assert d.calls.count(('POST','/v2/media/jobs'))==2
        assert sum(method=='POST' for method,path in seen)==3
        assert (await ledger.summary(scope))['spent']=='6'
        await counts(db,jobs=2,receipts=2)
        public=json.dumps(d.responses)
        assert A not in public and B not in public and C not in public and 'synthetic-private' not in public


@pytest.mark.asyncio
async def test_fish_original_get_recovery_survives_runtime_restart_then_download(db,tmp_path,files,monkeypatch,legacy_media):
    seen=[];downloads=0
    def provider(req):
        nonlocal downloads
        seen.append((req.method,req.url.path))
        if req.method=='POST':return httpx.Response(202,json={'task_id':A,'status':'IN_PROGRESS'})
        if req.url.path.endswith('/content'):
            downloads+=1
            if downloads==1:raise httpx.ReadTimeout('synthetic content loss',request=req)
            return httpx.Response(200,content=files[0]['wav'],headers={'content-type':'audio/wav'})
        assert req.url.path.endswith('/tasks/'+A)
        return httpx.Response(200,json={'task_id':A,'status':'SUCCESS','content_url':'/v1/tasks/'+A+'/artifacts/audio/content','mime_type':'audio/wav','size':len(files[0]['wav']),'expires_at':int(time.time())+1000})
    runtime,ledger,scope,_,body=await build_voice(db,legacy_media,tmp_path,files,monkeypatch,provider,fish=True)
    async with desktop(db,tmp_path,runtime,scope) as d:
        result=await execute(d,'create_media',arguments(body));assert result['ok'],(result,d.responses)
        await runtime.run_once()
        assert (await ledger.summary(scope))['spent']=='0'
        await legacy_media.due_work()
        restarted=MediaRuntime(db,runtime.service.identity,runtime.settings,service=runtime.service,maintenance=legacy_media.generation,transport=httpx.MockTransport(provider))
        await legacy_media.start_runtime(restarted)
        try:
            await restarted.run_once();await restarted.run_once()
        finally:await restarted.close()
        done=await execute(d,'get_media_job',{'job_id':result['job']['id']})
        assert done['ok'] and done['job']['actual']=='1',done
        await download(d,done['job'],'outputs/fish.wav',files[0]['wav'])
        assert sum(method=='POST' for method,path in seen)==1 and downloads==2
        await counts(db,jobs=1,receipts=1)


@pytest.mark.asyncio
@pytest.mark.parametrize('adapter',['newapi_videos_v1','newapi_suno_v1','newapi_dashscope_music_v1'])
async def test_video_music_owned_original_download(db,tmp_path,files,monkeypatch,adapter,legacy_media):
    seen=[];video=adapter=='newapi_videos_v1';raw=files[0]['video' if video else 'wav']
    def provider(req):
        seen.append((req.method,req.url.path))
        if req.method=='POST':
            if video:return httpx.Response(200,json={'id':'known','status':'queued'})
            if adapter=='newapi_suno_v1':return httpx.Response(200,json={'code':'success','data':'known'})
        if req.url.path.endswith('/content') or req.url.path=='/audio/music.wav':
            return httpx.Response(200,content=raw,headers={'content-type':'video/mp4' if video else 'audio/wav'})
        if video:return httpx.Response(200,json={'id':'known','status':'completed'})
        if adapter=='newapi_suno_v1':return httpx.Response(200,json={'code':'success','data':{'task_id':'known','status':'SUCCESS','data':[{'id':'track','audio_url':'http://127.0.0.1:19992/audio/music.wav'}]}})
        return httpx.Response(200,json={'output':{'task_id':'known','task_status':'SUCCEEDED','finish_reason':'stop','audio':{'url':'http://127.0.0.1:19992/audio/music.wav'}},'usage':{'duration':0.5}})
    runtime,ledger,scope,_,body=await build_async(db,legacy_media,tmp_path,files,adapter,provider,monkeypatch)
    async with desktop(db,tmp_path,runtime,scope) as d:
        submitted=await execute(d,'create_media',arguments(body));assert submitted['ok'],(submitted,d.responses)
        await runtime.run_once();await runtime.run_once()
        done=await execute(d,'get_media_job',{'job_id':submitted['job']['id']})
        assert done['ok'] and done['job']['actual']=='1',done
        await download(d,done['job'],'outputs/video.mp4' if video else 'outputs/music.wav',raw)
        assert sum(method=='POST' for method,path in seen)==1
        await counts(db,jobs=1,receipts=1)


@pytest.mark.asyncio
async def test_unknown_speech_same_call_never_posts_again_and_keeps_hold(db,tmp_path,files,monkeypatch,legacy_media):
    seen=[]
    def provider(req):
        seen.append(req);raise httpx.ReadTimeout('synthetic unknown speech',request=req)
    runtime,ledger,scope,_,body=await build_audio(db,legacy_media,tmp_path,files,monkeypatch,provider)
    async with desktop(db,tmp_path,runtime,scope) as d:
        result=await execute(d,'create_media',arguments(body));assert not result['ok'] and result['local_status']=='unknown'
        replay=await asyncio.to_thread(MediaHost(d.request,client=d.client).execute,'create_media',arguments(body),'tool')
        assert replay['ok'] and replay['job']['actual'] is None
        assert replay['job']['financial_state']=='pending_reconciliation'
        await runtime.run_once()
        assert len(seen)==1 and d.calls.count(('POST','/v2/media/jobs'))==1
        assert d.calls.count(('POST','/v2/media/task-tokens'))==1
        summary=await ledger.summary(scope);assert summary['spent']=='0' and summary['reserved']=='100'
        assert not replay['job']['artifacts']
        await counts(db,jobs=1,receipts=0)


@pytest.mark.asyncio
@pytest.mark.parametrize('case',['stop','other_owner'])
async def test_late_download_cannot_publish_after_stop_or_owner_switch(db,tmp_path,files,monkeypatch,case,legacy_media):
    def provider(req):return httpx.Response(200,content=files[0]['wav'],headers={'content-type':'audio/wav','x-request-id':'req_late'})
    runtime,ledger,scope,_,body=await build_audio(db,legacy_media,tmp_path,files,monkeypatch,provider)
    async with desktop(db,tmp_path,runtime,scope) as d:
        result=await execute(d,'create_media',arguments(body));assert result['ok'],(result,d.responses)
        job=result['job'];artifact=job['artifacts'][0]
        if case=='other_owner':
            foreign,foreign_token=await foreign_identity(db,scope)
            for path in ('/v2/media/jobs/'+job['id'],'/v2/media/artifacts/'+artifact['id'],'/v2/media/artifacts/'+artifact['id']+'/content'):
                response=await d.server.get(path,headers={'authorization':'Bearer '+foreign_token})
                assert response.status_code==404,(path,response.status_code,response.text)
        def late(req,response):
            if req.url.path.endswith('/content'):
                if case=='stop':d.host.stop()
                else:d.central.save_auth({**d.auth,'access_token':foreign_token,'scope':asdict(foreign),'session_id':foreign.session_id,'account':{'id':'other','email':None,'email_verified':False}})
        d.after_response=late
        saved=await execute(d,'download_media_artifact',{'job_id':job['id'],'artifact_id':artifact['id'],'path':'outputs/late.wav'})
        assert not saved['ok'] and not (d.workspace/'outputs/late.wav').exists()
        assert (await ledger.summary(scope))['spent']=='4'
        await counts(db,jobs=1,receipts=1)
