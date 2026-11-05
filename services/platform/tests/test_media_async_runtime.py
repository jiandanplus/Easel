"""Real PG plus controlled New API protocols and original local AV bytes."""
import copy
import httpx
import pytest
from b2_legacy_media_fixtures import legacy_media, legacy_db as db, legacy_dsn as test_dsn, legacy_bare_db as bare_db
from platform_service.errors import PlatformError
from platform_service.media_config import MediaProvider,MediaSettings
from platform_service.media_runtime import MediaRuntime
from platform_service.media.av_artifacts import AVTools
from test_media_contracts import definition
from test_media_credits import setup
from test_media_av_artifacts import files


async def build(db,legacy_media,tmp_path,files,adapter,handler,monkeypatch,*,tools=True,paths=True):
    raw,tool_paths=files
    video=adapter=='newapi_videos_v1';cap='video.generate'if video else'music.generate'
    value=definition(cap);value['adapter']=adapter
    unit,basis=('video_ms','verified_delivery_v1')if video else ('audio_clip','verified_delivery_v1')if adapter=='newapi_suno_v1'else('audio_ms','provider_usage_v1')
    value['rate']['meters']=[{'unit':unit,'basis':basis,'numerator':'1','denominator':'1'if unit=='audio_clip'else'1000'}]
    value['limits']['max_units']={unit:2 if unit=='audio_clip'else 5000}
    value['limits']['max_assets']=0
    value['limits']['output_media']={'mime_types':['video/mp4']if video else['audio/wav'],'max_files':1,'max_duration_ms':1000,'max_channels':2,'max_sample_rate':48000,'require_audio':not video,**({'max_pixels':1024}if video else{})}
    value['parameters']={'type':'object','properties':{'seconds':{'type':'integer','minimum':1,'maximum':5},'size':{'type':'string','enum':['32x32']}},'required':['seconds','size'],'additionalProperties':False}if video else{'type':'object','properties':{},'required':[],'additionalProperties':False}
    import test_media_credits as fixture
    bindings=copy.deepcopy(fixture.BINDINGS);bindings["fixture-image"]["adapter"]=adapter
    monkeypatch.setattr(fixture,"BINDINGS",bindings)
    service,ledger,scope,*_=await legacy_media.setup(db,media_definition=value)
    provider=MediaProvider('1','http://127.0.0.1:19991/v1','synthetic-fixture',{'fixture-image':'explicit-fixture'},adapter=adapter,capabilities=(cap,),download_origins=('http://127.0.0.1:19992',),download_path_prefixes=('/audio/',)if paths else())
    settings=MediaSettings(mode='test',storage_root=legacy_media.source_path,providers={'fixture':provider},av_tools=AVTools(*tool_paths)if tools else None)
    runtime=MediaRuntime(db,service.identity,settings,service=service.raw,maintenance=legacy_media.generation,transport=httpx.MockTransport(handler))
    await legacy_media.start_runtime(runtime)
    token=await service.mint(scope,model='fixture-image',capability=cap,invocation_key='runtime',mode='test',bindings=settings.bindings)
    request={'model':'fixture-image','capability':cap,'prompt':'Synthetic controlled media','assets':[],'parameters':{'seconds':1,'size':'32x32'}if video else{}}
    return runtime,ledger,scope,token['task_token'],request


@pytest.mark.asyncio
@pytest.mark.parametrize('adapter',['newapi_videos_v1','newapi_suno_v1','newapi_dashscope_music_v1'])
async def test_first_post_then_get_delivery_retries_and_restart_never_regenerate(db,tmp_path,files,adapter,monkeypatch,legacy_media):
    raw,_=files;seen=[];downloads=0
    video=adapter=='newapi_videos_v1'
    def handler(req):
        nonlocal downloads
        seen.append(req.method)
        if req.method=='POST':
            if video:return httpx.Response(200,json={'id':'known','status':'queued'})
            if adapter=='newapi_suno_v1':return httpx.Response(200,json={'code':'success','data':'known'})
            return httpx.Response(200,json={'output':{'task_id':'known','task_status':'SUCCEEDED','finish_reason':'stop','audio':{'url':'http://127.0.0.1:19992/audio/music.wav'}},'usage':{'duration':0.5}})
        if req.url.path.endswith('/content')or req.url.path=='/audio/music.wav':
            downloads+=1
            if downloads==1:raise httpx.ReadTimeout('synthetic download loss',request=req)
            if not video:assert 'authorization'not in req.headers
            return httpx.Response(200,content=raw['video'if video else'wav'],headers={'content-type':'video/mp4'if video else'audio/wav'})
        if video:return httpx.Response(200,json={'id':'known','status':'completed'})
        if adapter=='newapi_suno_v1':return httpx.Response(200,json={'code':'success','data':{'task_id':'known','status':'SUCCESS','data':[{'id':'track','audio_url':'http://127.0.0.1:19992/audio/music.wav'}]}})
        return httpx.Response(200,json={'output':{'task_id':'known','task_status':'SUCCEEDED','finish_reason':'stop','audio':{'url':'http://127.0.0.1:19992/audio/music.wav'}},'usage':{'duration':0.5}})
    runtime,ledger,scope,token,request=await build(db,legacy_media,tmp_path,files,adapter,handler,monkeypatch)
    try:
        first=await runtime.submit(token,'one',request)
        assert first['actual']is None
        assert(await runtime.submit(token,'one',request))['id']==first['id']
        await runtime.run_once()
        assert (await ledger.summary(scope))['spent']=='0'
        await legacy_media.due_work()
        # Recreate the facade while the original actual worker capability stays live.
        settings=runtime.settings;service=legacy_media.client(runtime.service)
        legacy_media.retain_runtime(runtime)
        runtime=MediaRuntime(db,service.identity,settings,service=service.raw,maintenance=legacy_media.generation,transport=httpx.MockTransport(handler))
        await legacy_media.start_runtime(runtime)
        await runtime.run_once();await runtime.run_once()
        value=await runtime._view(scope,first['id'])
        assert value['financial_state']=='settled'and value['actual']=='1'
        assert len(value['artifacts'])==1
        blob,mime,sha=await runtime.artifact(scope,value['artifacts'][0]['id'],content=True)
        assert blob==raw['video'if video else'wav']
        assert seen.count('POST')==1 and downloads==2
        async with db.connection()as c:
            assert(await(await c.execute('SELECT count(*) n FROM media_receipts')).fetchone())['n']==1
            assert(await(await c.execute('SELECT count(*) n FROM media_artifacts')).fetchone())['n']==1
    finally:await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('case',['timeout','known_bad_state','no_tools'])
async def test_unknown_post_is_never_repeated_and_missing_tools_stays_closed(db,tmp_path,files,case,monkeypatch,legacy_media):
    seen=[]
    def handler(req):
        seen.append(req.method)
        if case=='timeout':raise httpx.ReadTimeout('synthetic lost submit',request=req)
        return httpx.Response(200,json={'id':'known','status':'unrecognized'})
    runtime,ledger,scope,token,request=await build(db,legacy_media,tmp_path,files,'newapi_videos_v1',handler,monkeypatch,tools=case!='no_tools')
    try:
        with pytest.raises(PlatformError):await runtime.submit(token,'one',request)
        if case=='no_tools':
            assert (await runtime.catalog(scope))['models']==[] and not seen
            assert (await ledger.summary(scope))['reserved']=='0'
        else:
            await runtime.submit(token,'one',request)
            await runtime.run_once()
            assert seen.count('POST')==1 and (await ledger.summary(scope))['reserved']=='5'
            async with db.connection()as c:
                count=(await(await c.execute("SELECT count(*) n FROM media_provider_associations WHERE kind='job'")).fetchone())['n']
                assert count==(1 if case=='known_bad_state'else 0)
                assert(await(await c.execute('SELECT count(*) n FROM media_receipts')).fetchone())['n']==0
    finally:await runtime.close()

@pytest.mark.asyncio
async def test_async_startup_requires_owner_migration_and_never_runs_ddl(bare_db,tmp_path,files):
    from platform_service.media.core import MediaService
    provider=MediaProvider('1','http://127.0.0.1:19991/v1','synthetic-fixture',{'fixture-image':'explicit-fixture'},adapter='newapi_videos_v1',capabilities=('video.generate',))
    settings=MediaSettings(mode='test',storage_root=tmp_path/'owned',providers={'fixture':provider},av_tools=AVTools(*files[1]))
    runtime=MediaRuntime(bare_db,None,settings,service=MediaService(bare_db,None))
    try:
        await runtime.start()
        with pytest.raises(PlatformError)as error:await runtime.run_once()
        assert error.value.code=='media_maintenance_unavailable'
        async with bare_db.connection()as c:
            assert(await(await c.execute("SELECT to_regclass('media_work') name")).fetchone())['name']is None
        await bare_db.migrate()
        await runtime.start()
        # Owner business migrations do not invent control/domain authority.
        with pytest.raises(PlatformError,match='media_maintenance_unavailable'):await runtime.run_once()
    finally:await runtime.close()

@pytest.mark.asyncio
async def test_wrong_decoder_version_is_closed_before_any_reservation_or_post(db,tmp_path,files,monkeypatch,legacy_media):
    seen=[]
    runtime,ledger,scope,token,request=await build(db,legacy_media,tmp_path,files,'newapi_videos_v1',lambda request:seen.append(request),monkeypatch)
    object.__setattr__(runtime.settings.av_tools,'version','not-the-installed-version')
    try:
        assert(await runtime.catalog(scope))['models']==[]
        assert not(await runtime.capabilities(scope.product_id))['test_media_execution_enabled']
        with pytest.raises(PlatformError):await runtime.submit(token,'closed',request)
        assert not seen and(await ledger.summary(scope))['reserved']=='0'
    finally:await runtime.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('adapter',['newapi_suno_v1','newapi_dashscope_music_v1'])
async def test_music_missing_download_paths_is_closed_before_post(db,tmp_path,files,monkeypatch,adapter,legacy_media):
    seen=[]
    runtime,ledger,scope,token,request=await build(db,legacy_media,tmp_path,files,adapter,lambda request:seen.append(request),monkeypatch,paths=False)
    try:
        assert(await runtime.catalog(scope))['models']==[]
        assert not(await runtime.capabilities(scope.product_id))['test_media_execution_enabled']
        with pytest.raises(PlatformError):await runtime.submit(token,'closed',request)
        assert not seen and(await ledger.summary(scope))['reserved']=='0'
    finally:await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('adapter',['newapi_videos_v1','newapi_suno_v1','newapi_dashscope_music_v1'])
async def test_actual_worker_loss_keeps_original_unknown_hold_and_never_reclaims_by_lease(db,legacy_media,tmp_path,files,adapter,monkeypatch):
    from platform_service.media_maintenance.runtime import RuntimeGeneration
    seen=[]
    def handler(req):
        seen.append((req.method,req.url.path))
        if req.method=='POST':
            if adapter=='newapi_videos_v1':return httpx.Response(200,json={'id':'known','status':'queued'})
            if adapter=='newapi_suno_v1':return httpx.Response(200,json={'code':'success','data':'known'})
            return httpx.Response(200,json={'output':{'task_id':'known','task_status':'SUCCEEDED','finish_reason':'stop','audio':{'url':'http://127.0.0.1:19992/audio/music.wav'}},'usage':{'duration':0.5}})
        raise AssertionError('unknown original worker must not be replaced by a GET claimant')
    runtime,ledger,scope,token,request=await build(db,legacy_media,tmp_path,files,adapter,handler,monkeypatch)
    first=await runtime.submit(token,'lost-owner',request)
    before=await ledger.summary(scope)
    async with db.connection()as c:
        original=await(await c.execute("SELECT * FROM media_provider_associations WHERE job_id=%s AND kind='job'",(first['id'],))).fetchone()
    await legacy_media.due_work()
    old_generation=runtime.maintenance
    await runtime.close()
    assert any(p.binding.subject_id==first['id']for p in old_generation.permits)
    replacement=RuntimeGeneration(legacy_media.runtime_db,legacy_media.locks,source=legacy_media.source)
    await replacement.start()
    restarted=MediaRuntime(db,runtime.service.identity,runtime.settings,service=runtime.service,maintenance=replacement,transport=httpx.MockTransport(handler))
    try:
        await restarted.start()
        await restarted.run_once()
        after=await ledger.summary(scope)
        assert after['reserved']==before['reserved']and after['spent']==before['spent']=='0'
        assert [method for method,path in seen]==['POST']
        async with db.connection()as c:
            assert(await(await c.execute('SELECT count(*) n FROM media_receipts')).fetchone())['n']==0
            assert(await(await c.execute('SELECT count(*) n FROM media_artifacts')).fetchone())['n']==0
            observed=await(await c.execute("SELECT * FROM media_provider_associations WHERE job_id=%s AND kind='job'",(first['id'],))).fetchone()
            assert observed==original
        assert not list(legacy_media.source_path.iterdir())
    finally:
        await restarted.close()
