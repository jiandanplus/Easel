"""M3 real PostgreSQL + fully decoded original AV, synthetic HTTP only."""
import copy
import hashlib
import json
from dataclasses import replace

import httpx
import pytest
from b2_legacy_media_fixtures import legacy_media, legacy_db as db, legacy_dsn as test_dsn
from platform_service.errors import PlatformError
from platform_service.media_maintenance.contracts import MaintenanceError
from platform_service.media_config import MediaProvider, MediaSettings
from platform_service.media_runtime import MediaRuntime
from platform_service.media.av_artifacts import AVTools
from test_media_av_artifacts import files
from test_media_contracts import definition
from test_media_credits import setup


def resource(mimes):
    return {'mime_types':mimes,'max_files':1,'max_duration_ms':20000,'max_channels':2,
            'max_sample_rate':48000,'require_audio':True,**({'max_pixels':1024}if 'video/mp4'in mimes else {})}


async def build_audio(db,legacy_media,tmp_path,files,monkeypatch,handler,*,transcribe=False,usage='none',video=False,tools=True):
    import test_media_credits as fixture
    adapter='openai_transcriptions_v1'if transcribe else'openai_speech_v1'
    cap='audio.transcribe'if transcribe else'audio.speech'
    value=definition(cap);value['adapter']=adapter
    unit,basis=('input_token','provider_usage_v1')if usage=='tokens'else('audio_ms','provider_usage_v1')if usage=='duration'else('audio_ms','verified_input_v1')if transcribe else('unicode_scalar','verified_input_v1')
    value['rate']['meters']=[{'unit':unit,'basis':basis,'numerator':'1','denominator':'1000'if unit=='audio_ms'else'1'}]
    value['limits'].update(max_units={unit:20000 if unit=='audio_ms'else 100},max_assets=1 if transcribe else 0,max_input_bytes=1048576)
    value['limits']['input_media'if transcribe else'output_media']=resource(['video/mp4']if video else['audio/wav'])
    value['parameters']={'type':'object','properties':{'response_format':{'type':'string','enum':['json']if transcribe else['wav']},**({}if transcribe else{'voice':{'type':'string','enum':['alloy']}})},'required':['response_format'],'additionalProperties':False}
    bindings=copy.deepcopy(fixture.BINDINGS);bindings['fixture-image']['adapter']=adapter
    monkeypatch.setattr(fixture,'BINDINGS',bindings)
    service,ledger,scope,*_=await legacy_media.setup(db,media_definition=value)
    # The initial RED is an actual closed configuration, not a missing import.
    provider=MediaProvider('1','http://127.0.0.1:19991/v1','synthetic-private',{'fixture-image':'explicit-audio'},adapter=adapter,capabilities=(cap,))
    object.__setattr__(provider,'audio_profile',{'voices':['alloy'],'transcription_usage':usage,'transcription_video':video})
    try:settings=MediaSettings(mode='test',storage_root=legacy_media.source_path,providers={'fixture':provider},av_tools=AVTools(*files[1])if tools else None)
    except ValueError:pytest.fail('explicit standard audio profile is still rejected by central settings')
    runtime=MediaRuntime(db,service.identity,settings,service=service.raw,maintenance=legacy_media.generation,transport=httpx.MockTransport(handler))
    await legacy_media.start_runtime(runtime)
    token=await service.mint(scope,model='fixture-image',capability=cap,invocation_key='audio-runtime',mode='test',bindings=settings.bindings)
    request={'model':'fixture-image','capability':cap,'prompt':''if transcribe else'A😀e\u0301','assets':[],
             'parameters':{'response_format':'json'if transcribe else'wav',**({}if transcribe else{'voice':'alloy'})}}
    return runtime,ledger,scope,token['task_token'],request


@pytest.mark.asyncio
async def test_speech_raw_owned_audio_typed_header_hash_single_settlement(db,tmp_path,files,monkeypatch,legacy_media):
    raw=files[0]['wav'];seen=[]
    def handler(req):
        seen.append(req);assert req.url.path=='/v1/audio/speech'
        assert json.loads(req.content)['input']=='A😀e\u0301'
        return httpx.Response(200,content=raw,headers={'content-type':'audio/wav','x-request-id':'req_audio_once'})
    runtime,ledger,scope,token,body=await build_audio(db,legacy_media,tmp_path,files,monkeypatch,handler)
    try:
        result=await runtime.submit(token,'once',body)
        assert result['financial_state']=='settled'and result['actual']=='4'
        artifact=result['artifacts'][0]
        assert(await runtime.artifact(scope,artifact['id'],content=True))[0]==raw
        assert artifact['properties']['duration_ms']==500
        assert(await runtime.submit(token,'once',body))['id']==result['id']and len(seen)==1
        with pytest.raises(PlatformError):await runtime.artifact(replace(scope,product_id='second'),artifact['id'],content=True)
        async with db.connection()as c:
            evidence=await(await c.execute('SELECT * FROM media_terminal_evidence WHERE job_id=%s',(result['id'],))).fetchone()
            assert evidence['provider_response_id']is None and evidence['reference_kind']=='http_request_id'
            assert evidence['http_reference_value']=='req_audio_once'and evidence['evidence_hash']==hashlib.sha256(raw).hexdigest()
            assert(await(await c.execute('SELECT count(*) n FROM media_receipts')).fetchone())['n']==1
        assert(await ledger.summary(scope))['spent']=='4'
    finally:await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('video',[False,True])
async def test_transcription_owned_multipart_empty_text_and_actual_input_audio_duration(db,tmp_path,files,monkeypatch,video,legacy_media):
    raw=files[0]['av'if video else'wav'];seen=[]
    def handler(req):
        seen.append(req)
        assert req.url.path=='/v1/audio/transcriptions'and'multipart/form-data'in req.headers['content-type']and raw in req.content
        assert b'filename="input.'in req.content
        return httpx.Response(200,json={'text':''},headers={'x-request-id':'req_transcription_empty'})
    runtime,ledger,scope,token,body=await build_audio(db,legacy_media,tmp_path,files,monkeypatch,handler,transcribe=True,video=video)
    try:
        async def chunks():yield raw
        asset=await runtime.upload_asset(token,chunks(),mime='video/mp4'if video else'audio/wav')
        body['assets']=[asset['id']]
        result=await runtime.submit(token,'once',body)
        assert result['actual']=='1'and result['financial_state']=='settled'
        assert json.loads((await runtime.artifact(scope,result['artifacts'][0]['id'],content=True))[0])=={'text':''}
        async with db.connection()as c:
            evidence=await(await c.execute('SELECT quantities FROM media_terminal_evidence WHERE job_id=%s',(result['id'],))).fetchone()
            expected=asset['properties']['audio_duration_ms'if video else'duration_ms']
            assert evidence['quantities']=={'audio_ms':expected}
        assert(await runtime.submit(token,'once',body))['id']==result['id']and len(seen)==1
    finally:await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['timeout','missing_header','corrupt','missing_tools'])
async def test_audio_unknown_holds_and_missing_decoder_rejects_before_admission(db,tmp_path,files,monkeypatch,failure,legacy_media):
    seen=[]
    def handler(req):
        seen.append(req)
        if failure=='timeout':raise httpx.ReadTimeout('synthetic uncertainty',request=req)
        return httpx.Response(200,content=b'broken'if failure=='corrupt'else files[0]['wav'],headers={'content-type':'audio/wav',**({}if failure=='missing_header'else{'x-request-id':'req_audio_broken'})})
    runtime,ledger,scope,token,body=await build_audio(db,legacy_media,tmp_path,files,monkeypatch,handler,tools=failure!='missing_tools')
    try:
        with pytest.raises(PlatformError):await runtime.submit(token,'once',body)
        if failure=='missing_tools':
            assert not seen and(await ledger.summary(scope))['reserved']=='0'and(await runtime.catalog(scope))['models']==[]
        else:
            original=await runtime.submit(token,'once',body)
            await runtime.run_once()
            assert len(seen)==1 and original['actual']is None and original['financial_state']=='pending_reconciliation'
            assert(await ledger.summary(scope))['reserved']=='100'and(await ledger.summary(scope))['spent']=='0'
    finally:await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('usage,payload,actual',[('tokens',{'type':'tokens','input_tokens':2,'output_tokens':1,'total_tokens':3},'2'),('duration',{'type':'duration','seconds':1.0001},'2'),('tokens',{'type':'tokens','input_tokens':True,'output_tokens':1,'total_tokens':2},None),('tokens',None,None)])
async def test_transcription_strict_usage_profile_never_invents_missing_quantities(db,tmp_path,files,monkeypatch,usage,payload,actual,legacy_media):
    seen=[]
    def handler(req):
        seen.append(req)
        return httpx.Response(200,json={'text':'result',**({'usage':payload}if payload is not None else{})},headers={'x-request-id':'req_usage'})
    runtime,ledger,scope,token,body=await build_audio(db,legacy_media,tmp_path,files,monkeypatch,handler,transcribe=True,usage=usage)
    try:
        async def chunks():yield files[0]['wav']
        body['assets']=[(await runtime.upload_asset(token,chunks(),mime='audio/wav'))['id']]
        if actual is None:
            with pytest.raises(PlatformError):await runtime.submit(token,'usage',body)
            result=await runtime.submit(token,'usage',body)
            assert result['actual']is None and result['financial_state']=='pending_reconciliation'and(await ledger.summary(scope))['spent']=='0'
        else:
            result=await runtime.submit(token,'usage',body)
            assert result['actual']==actual
            async with db.connection()as c:
                assert (await(await c.execute("SELECT to_regclass('media_protocol_facts') name")).fetchone())['name'] is not None, 'private normalized usage facts are not persisted'
                facts=await(await c.execute('SELECT facts FROM media_protocol_facts WHERE job_id=%s',(result['id'],))).fetchone()
                assert facts['facts']['type']==usage
                if usage=='duration':assert facts['facts']['seconds']=='1.0001'
        assert len(seen)==1
    finally:await runtime.close()


@pytest.mark.asyncio
async def test_standard_audio_start_rejects_missing_protocol_evidence_storage(db,tmp_path,files,monkeypatch,legacy_media):
    runtime,*_=await build_audio(db,legacy_media,tmp_path,files,monkeypatch,lambda req:None)
    try:
        async with db.connection()as c:await c.execute('ALTER TABLE media_protocol_facts RENAME TO missing_protocol_facts')
        with pytest.raises(PlatformError):await runtime.start()
    finally:await runtime.close()


@pytest.mark.asyncio
async def test_private_protocol_facts_reject_arbitrary_body_fields_and_bind_exact_attempt(db,tmp_path,files,monkeypatch,legacy_media):
    import psycopg
    runtime,_,scope,token,body=await build_audio(db,legacy_media,tmp_path,files,monkeypatch,lambda req:None,transcribe=True,usage='tokens')
    try:
        async def chunks():yield files[0]['wav']
        body['assets']=[(await runtime.upload_asset(token,chunks(),mime='audio/wav'))['id']]
        context=await runtime.service.authenticate_task(token)
        asset=await runtime.service.get_asset(context,body['assets'][0])
        job=await legacy_media.client(runtime.service).admit(context,key='facts',request=body,bindings=runtime.bindings,verified_inputs={asset['id']:{k:asset[k]for k in ('sha256','size','mime','properties')}})
        await legacy_media.client(runtime.service).record_provider_attempt(job['id'],job['attempt_id'],provider_request_hash='b'*64,provider_scope_hash='c'*64,upstream_model='fixture',request_id='facts')
        await legacy_media.client(runtime.service).dispatch(job['id'],job['attempt_id'])
        for facts in [{'prompt':'private text'},{'voice_id':'private'},{'url':'https://signed.invalid/'},{'type':'tokens','input_tokens':True,'output_tokens':1,'total_tokens':2}]:
            with pytest.raises((PlatformError,psycopg.Error)):
                await legacy_media.client(runtime.service).record_protocol_facts(job['id'],job['attempt_id'],response_hash='d'*64,facts=facts)
        with pytest.raises(MaintenanceError,match='invalid_backup'):await legacy_media.client(runtime.service).record_protocol_facts(job['id'],'different-attempt',response_hash='d'*64,facts={'type':'tokens','input_tokens':1,'output_tokens':1,'total_tokens':2})
    finally:await runtime.close()


@pytest.mark.parametrize('adapter,cap,profile,upstream',[
    ('openai_speech_v1','audio.speech',{},'tts'),
    ('newapi_fish_voice_v1','audio.speech',{'target_models':[]},'easel-fish-s2.1-pro'),
    ('newapi_minimax_voice_v1','voice.enroll',{'target_models':['easel-minimax-speech-2.8-hd']},'unpublished-target')])
def test_empty_or_mismatched_audio_voice_profiles_cannot_advertise_available_models(tmp_path,adapter,cap,profile,upstream):
    provider=MediaProvider('1','http://127.0.0.1:19991','synthetic-only',{'public':upstream},adapter=adapter,capabilities=(cap,),
        **({'audio_profile':profile}if adapter.startswith('openai')else{'voice_profile':profile}))
    with pytest.raises(ValueError):MediaSettings(mode='test',storage_root=tmp_path/'owned',providers={'fixture':provider})
