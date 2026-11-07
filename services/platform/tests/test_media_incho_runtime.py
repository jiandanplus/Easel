"""Owned PostgreSQL, one Incho submit, real decoded audio, and exact wallet debit."""
import asyncio
import copy
from dataclasses import replace

import httpx
import pytest
from b2_legacy_media_fixtures import legacy_media, legacy_db as db, legacy_dsn as test_dsn

from platform_service.errors import PlatformError
from platform_service.media.av_artifacts import AVTools
from platform_service.media_config import MediaProvider, MediaSettings
from platform_service.media_runtime import MediaRuntime
from test_media_av_artifacts import files
from test_media_contracts import definition
from test_media_credits import setup
from test_media_incho_protocol import envelope, song


async def build(db,legacy_media,tmp_path,files,handler,monkeypatch,*,paths=True,origins=True,tools=True,n=2):
    value=definition('music.generate');value['adapter']='newapi_incho_v1'
    value['rate']['meters']=[{'unit':'audio_clip','basis':'verified_delivery_v1','numerator':'3','denominator':'2'}]
    value['limits']['max_units']={'audio_clip':2};value['limits']['max_assets']=0
    value['limits']['output_media']={'mime_types':['audio/mpeg'],'max_files':2,
        'max_duration_ms':1000,'max_channels':2,'max_sample_rate':48000,'require_audio':True}
    value['parameters']={'type':'object','properties':{'n':{'type':'integer','minimum':1,'maximum':2},
        'lyric':{'type':'string','maxLength':3000}},'required':[],'additionalProperties':False}
    import test_media_credits as fixture
    bindings=copy.deepcopy(fixture.BINDINGS);bindings['fixture-image']['adapter']='newapi_incho_v1'
    monkeypatch.setattr(fixture,'BINDINGS',bindings)
    service,ledger,scope,*_=await legacy_media.setup(db,media_definition=value)
    provider=MediaProvider('1','http://127.0.0.1:19991','synthetic-only',{'fixture-image':'v3.5'},
        adapter='newapi_incho_v1',capabilities=('music.generate',),
        download_origins=('http://127.0.0.1:19992',)if origins else(),
        download_path_prefixes=('/audio/',)if paths else())
    settings=MediaSettings(mode='test',storage_root=legacy_media.source_path,providers={'fixture':provider},
        av_tools=AVTools(*files[1])if tools else None)
    runtime=MediaRuntime(db,service.identity,settings,service=service.raw,maintenance=legacy_media.generation,transport=httpx.MockTransport(handler))
    await legacy_media.start_runtime(runtime)
    token=await service.mint(scope,model='fixture-image',capability='music.generate',
                             invocation_key='incho-runtime',mode='test',bindings=settings.bindings)
    request={'model':'fixture-image','capability':'music.generate','prompt':'Synthetic music only',
             'assets':[],'parameters':{'n':n}}
    return runtime,ledger,scope,token['task_token'],request


async def due(db,legacy_media,runtime):
    await legacy_media.due_work()


@pytest.mark.asyncio
async def test_incho_start_requires_020_adapter_constraint_without_running_ddl(db,tmp_path,files,monkeypatch,legacy_media):
    runtime,*_=await build(db,legacy_media,tmp_path,files,lambda req:pytest.fail('no HTTP permitted'),monkeypatch)
    try:
        async with db.connection()as c:
            await c.execute('ALTER TABLE media_work DROP CONSTRAINT media_work_adapter_check')
            await c.execute("ALTER TABLE media_work ADD CONSTRAINT media_work_adapter_check CHECK(adapter IN ('newapi_videos_v1','newapi_suno_v1','newapi_dashscope_music_v1','newapi_minimax_voice_v1','newapi_dashscope_voice_v1','newapi_fish_voice_v1'))")
        with pytest.raises(PlatformError)as error:await runtime.start()
        assert error.value.code=='media_incho_migration_required'
        async with db.connection()as c:
            row=await(await c.execute("SELECT pg_get_constraintdef(oid) definition FROM pg_constraint WHERE conrelid='media_work'::regclass AND conname='media_work_adapter_check'")).fetchone()
            assert 'newapi_incho_v1'not in row['definition']
    finally:await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('done_tracks',[1,2])
async def test_incho_double_track_delivery_once_rounding_partial_success_and_owner_boundary(db,tmp_path,files,monkeypatch,done_tracks,legacy_media):
    seen=[];raw=files[0]['mp3']
    def handler(req):
        seen.append((req.method,req.url.path))
        if req.method=='POST':
            assert req.url.path=='/incho/submit/music'
            return httpx.Response(200,json={'code':'success','data':'gateway-task'})
        if req.url.path.startswith('/incho/fetch/'):
            return httpx.Response(200,json=envelope([song(),song('song-two',
                status='done'if done_tracks==2 else'fail',url='http://127.0.0.1:19992/audio/two.mp3')]))
        assert 'authorization'not in req.headers
        return httpx.Response(200,content=raw,headers={'content-type':'audio/mpeg'})
    runtime,ledger,scope,token,request=await build(db,legacy_media,tmp_path,files,handler,monkeypatch)
    try:
        await runtime.start()
        first=await runtime.submit(token,'same-operation',request)
        assert first['actual']is None
        assert(await runtime.submit(token,'same-operation',request))['id']==first['id']
        await runtime.run_once();await runtime.run_once()
        result=await runtime._view(scope,first['id'])
        expected='3'if done_tracks==2 else'2'
        assert result['actual']==expected and result['financial_state']=='settled'
        assert len(result['artifacts'])==done_tracks
        summary=await ledger.summary(scope)
        assert summary['reserved']=='0'and summary['spent']==expected
        for artifact in result['artifacts']:
            assert(await runtime.artifact(scope,artifact['id'],content=True))[0]==raw
            with pytest.raises(PlatformError):
                await runtime.artifact(replace(scope,user_id='foreign'),artifact['id'],content=True)
        with pytest.raises(PlatformError):await runtime._view(replace(scope,product_id='foreign'),first['id'])
        assert sum(method=='POST'for method,_ in seen)==1
        async with db.connection()as c:
            assert(await(await c.execute('SELECT count(*) n FROM media_receipts')).fetchone())['n']==1
            assert(await(await c.execute('SELECT count(*) n FROM media_artifacts')).fetchone())['n']==done_tracks
            assert(await(await c.execute('SELECT count(*) n FROM media_provider_attempts')).fetchone())['n']==1
    finally:await runtime.close()


@pytest.mark.asyncio
async def test_incho_lost_second_download_restart_gets_original_job_and_never_regenerates(db,tmp_path,files,monkeypatch,legacy_media):
    seen=[];second_downloads=0
    def handler(req):
        nonlocal second_downloads
        seen.append((req.method,req.url.path))
        if req.method=='POST':return httpx.Response(200,json={'code':'success','data':'gateway-task'})
        if req.url.path.startswith('/incho/fetch/'):
            return httpx.Response(200,json=envelope([song(),song('song-two',url='http://127.0.0.1:19992/audio/two.mp3')]))
        if req.url.path.endswith('/two.mp3'):
            second_downloads+=1
            if second_downloads==1:raise httpx.ReadTimeout('synthetic download uncertainty',request=req)
        return httpx.Response(200,content=files[0]['mp3'],headers={'content-type':'audio/mpeg'})
    runtime,ledger,scope,token,request=await build(db,legacy_media,tmp_path,files,handler,monkeypatch)
    try:
        first=await runtime.submit(token,'same-operation',request)
        await runtime.run_once()
        assert (await ledger.summary(scope))['spent']=='0'
        settings=runtime.settings;service=legacy_media.client(runtime.service)
        legacy_media.retain_runtime(runtime);await due(db,legacy_media,runtime)
        runtime=MediaRuntime(db,service.identity,settings,service=service.raw,maintenance=legacy_media.generation,transport=httpx.MockTransport(handler))
        await legacy_media.start_runtime(runtime)
        await runtime.run_once();await runtime.run_once()
        result=await runtime._view(scope,first['id'])
        assert result['financial_state']=='settled'and result['actual']=='3'
        assert len(result['artifacts'])==2 and second_downloads==2
        assert seen.count(('POST','/incho/submit/music'))==1
        assert seen.count(('GET','/audio/one.mp3'))==1
        assert (await ledger.summary(scope))['reserved']=='0'
    finally:await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('case',['unknown_post','failed_task','wrong_count','corrupt_audio'])
async def test_incho_uncertainty_has_no_extra_submit_no_fake_receipt_and_no_zero_cost(db,tmp_path,files,monkeypatch,case,legacy_media):
    seen=[]
    def handler(req):
        seen.append(req.method)
        if req.method=='POST':
            if case=='unknown_post':raise httpx.ReadTimeout('synthetic lost submit',request=req)
            return httpx.Response(200,json={'code':'success','data':'gateway-task'})
        if req.url.path.startswith('/incho/fetch/'):
            choices=[song(status='fail')]if case=='failed_task'else[song(),song('song-two')]
            return httpx.Response(200,json=envelope(choices,state='FAILURE'if case=='failed_task'else'SUCCESS'))
        return httpx.Response(200,content=b'not-an-mp3',headers={'content-type':'audio/mpeg'})
    runtime,ledger,scope,token,request=await build(db,legacy_media,tmp_path,files,handler,monkeypatch,n=1 if case=='wrong_count'else 2)
    try:
        if case=='unknown_post':
            with pytest.raises(PlatformError):await runtime.submit(token,'same-operation',request)
            # Original accepted operation returns its existing hold, never POST.
            await runtime.submit(token,'same-operation',request)
        else:await runtime.submit(token,'same-operation',request)
        await runtime.run_once();await due(db,legacy_media,runtime);await runtime.run_once()
        summary=await ledger.summary(scope)
        assert summary['reserved']=='3'and summary['spent']=='0'
        assert seen.count('POST')==1
        if case=='unknown_post':assert seen==['POST']
        async with db.connection()as c:
            row=await(await c.execute('SELECT actual,status FROM media_jobs')).fetchone()
            assert row['actual']is None
            assert(await(await c.execute('SELECT count(*) n FROM media_receipts')).fetchone())['n']==0
    finally:await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('missing',['paths','origins','tools'])
async def test_incho_missing_delivery_preconditions_is_closed_before_reserve_or_post(db,tmp_path,files,monkeypatch,missing,legacy_media):
    runtime,ledger,scope,token,request=await build(db,legacy_media,tmp_path,files,
        lambda req:pytest.fail('closed capability made HTTP'),monkeypatch,**{missing:False})
    try:
        assert(await runtime.catalog(scope))['models']==[]
        with pytest.raises(PlatformError):await runtime.submit(token,'closed',request)
        assert(await ledger.summary(scope))['reserved']=='0'
    finally:await runtime.close()


@pytest.mark.asyncio
async def test_incho_cancel_after_dispatch_keeps_hold_until_original_trusted_delivery(db,tmp_path,files,monkeypatch,legacy_media):
    seen=[]
    def handler(req):
        seen.append(req.method)
        if req.method=='POST':return httpx.Response(200,json={'code':'success','data':'gateway-task'})
        if req.url.path.startswith('/incho/fetch/'):
            return httpx.Response(200,json=envelope([song()]))
        return httpx.Response(200,content=files[0]['mp3'],headers={'content-type':'audio/mpeg'})
    runtime,ledger,scope,token,request=await build(db,legacy_media,tmp_path,files,handler,monkeypatch)
    try:
        first=await runtime.submit(token,'cancelled',request)
        await runtime.cancel(scope,first['id'])
        pending=await runtime._view(scope,first['id'])
        assert pending['actual']is None and not pending['artifacts']
        assert(await ledger.summary(scope))['reserved']=='3'
        await runtime.run_once()
        result=await runtime._view(scope,first['id'])
        assert result['actual']=='2'and len(result['artifacts'])==1
        assert(await ledger.summary(scope))['spent']=='2'
        assert seen==['POST','GET','GET']
    finally:await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('case',['download','stop','other_owner'])
async def test_incho_actual_asgi_host_download_and_late_publication_fences(db,tmp_path,files,monkeypatch,case,legacy_media):
    from dataclasses import asdict
    from test_media_m3_desktop_roundtrip import desktop,execute,arguments,foreign_identity,counts
    raw=files[0]['mp3'];seen=[]
    def handler(req):
        seen.append((req.method,req.url.path))
        if req.method=='POST':return httpx.Response(200,json={'code':'success','data':'gateway-task'})
        if req.url.path.startswith('/incho/fetch/'):
            return httpx.Response(200,json=envelope([song()]))
        return httpx.Response(200,content=raw,headers={'content-type':'audio/mpeg'})
    runtime,ledger,scope,_,body=await build(db,legacy_media,tmp_path,files,handler,monkeypatch,n=1)
    async with desktop(db,tmp_path,runtime,scope)as d:
        await asyncio.to_thread(d.host._accepted)
        submitted=await execute(d,'create_media',arguments(body))
        assert submitted['ok'],(submitted,d.responses)
        await runtime.run_once();await runtime.run_once()
        result=await execute(d,'get_media_job',{'job_id':submitted['job']['id']})
        assert result['ok']and result['job']['actual']=='2'
        job=result['job'];artifact=job['artifacts'][0]
        if case=='other_owner':
            foreign,foreign_token=await foreign_identity(db,scope)
            for path in ('/v2/media/jobs/'+job['id'],'/v2/media/artifacts/'+artifact['id'],
                         '/v2/media/artifacts/'+artifact['id']+'/content'):
                response=await d.server.get(path,headers={'authorization':'Bearer '+foreign_token})
                assert response.status_code==404
        def late(req,response):
            if req.url.path.endswith('/content'):
                if case=='stop':d.host.stop()
                elif case=='other_owner':d.central.save_auth({**d.auth,'access_token':foreign_token,
                    'scope':asdict(foreign),'session_id':foreign.session_id,
                    'account':{'id':'other','email':None,'email_verified':False}})
        d.after_response=late
        downloaded=await execute(d,'download_media_artifact',{'job_id':job['id'],
            'artifact_id':artifact['id'],'path':'outputs/music.mp3'},'download')
        target=d.workspace/'outputs/music.mp3'
        if case=='download':
            assert downloaded['ok'],(downloaded,d.responses)
            assert target.read_bytes()==raw
        else:assert not downloaded['ok']and not target.exists()
        assert(await ledger.summary(scope))['spent']=='2'
        assert sum(method=='POST'for method,_ in seen)==1
        await counts(db,jobs=1,receipts=1)


@pytest.mark.asyncio
@pytest.mark.parametrize('params',[{'n':True},{'n':'1'},{'n':0},{'n':3},
    {'lyric':'x'*3001},{'unknown':'value'}])
async def test_incho_actual_host_rejects_invalid_values_before_mint_or_supplier_post(db,tmp_path,files,monkeypatch,params,legacy_media):
    from test_media_m3_desktop_roundtrip import desktop,execute,arguments,counts
    runtime,ledger,scope,_,body=await build(db,legacy_media,tmp_path,files,
        lambda req:pytest.fail('invalid host arguments reached supplier'),monkeypatch,n=1)
    body['parameters']=params
    async with desktop(db,tmp_path,runtime,scope)as d:
        result=await execute(d,'create_media',arguments(body))
        assert not result['ok']
        assert all(method=='GET'for method,path in d.calls)
        assert(await ledger.summary(scope))['reserved']=='0'
        await counts(db,jobs=0,receipts=0)
