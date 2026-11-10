"""M3 voice stages and GET recovery, real PG and decoded local audio."""
import copy
import io
import json
import time
import wave
from dataclasses import replace

import httpx
import pytest
from b2_legacy_media_fixtures import legacy_media, legacy_db as db, legacy_dsn as test_dsn
from platform_service.errors import PlatformError
from platform_service.media_config import MediaProvider,MediaSettings
from platform_service.media_runtime import MediaRuntime
from platform_service.media.av_artifacts import AVTools
from test_media_av_artifacts import files
from test_media_audio_runtime import resource
from test_media_contracts import definition
from test_media_credits import setup,OPERATOR

A='task_'+'a'*32
B='task_'+'b'*32
C='task_'+'c'*32
TARGET='easel-minimax-speech-2.8-hd'


def sample():
    stream=io.BytesIO()
    with wave.open(stream,'wb')as f:f.setnchannels(1);f.setsampwidth(2);f.setframerate(8000);f.writeframes(b'\0\0'*80000)
    return stream.getvalue()


async def build_voice(db,legacy_media,tmp_path,files,monkeypatch,handler,*,fish=False):
    import test_media_credits as fixture
    adapter='newapi_fish_voice_v1'if fish else'newapi_minimax_voice_v1'
    target='easel-fish-s2.1-pro'if fish else TARGET
    cap='audio.speech'if fish else'voice.enroll'
    value=definition(cap);value['adapter']=adapter
    value['rate']['meters']=[{'unit':'audio_ms'if fish else'registration','basis':'verified_delivery_v1','numerator':'1','denominator':'1000'if fish else'1'}]
    value['limits'].update(max_units={'audio_ms':20000}if fish else{'registration':1},max_assets=0 if fish else 1,max_input_bytes=1048576)
    value['limits']['output_media'if fish else'input_media']=resource(['audio/wav'])
    value['parameters']={'type':'object','properties':{'voice':{'type':'string','enum':['narrator']},'response_format':{'type':'string','enum':['wav']}}if fish else{'target_model':{'type':'string','enum':[target]}},'required':['voice','response_format']if fish else['target_model'],'additionalProperties':False}
    bindings=copy.deepcopy(fixture.BINDINGS);bindings['fixture-image']['adapter']=adapter
    monkeypatch.setattr(fixture,'BINDINGS',bindings)
    service,ledger,scope,*_=await legacy_media.setup(db,media_definition=value)
    provider=MediaProvider('1','http://127.0.0.1:19991','synthetic-private',{'fixture-image':target,**({}if fish else{'fixture-synth':target})},adapter=adapter,capabilities=('audio.speech',)if fish else('voice.enroll','voice.synthesize'))
    object.__setattr__(provider,'voice_profile',{'target_models':[target],**({'presets':[['narrator','private-admin-reference']]}if fish else{})})
    try:settings=MediaSettings(mode='test',storage_root=legacy_media.source_path,providers={'fixture':provider},av_tools=AVTools(*files[1]))
    except ValueError:pytest.fail('explicit private voice profile is still rejected by central settings')
    runtime=MediaRuntime(db,service.identity,settings,service=service.raw,maintenance=legacy_media.generation,transport=httpx.MockTransport(handler))
    await legacy_media.start_runtime(runtime)
    token=await service.mint(scope,model='fixture-image',capability=cap,invocation_key='voice-runtime',mode='test',bindings=settings.bindings)
    request={'model':'fixture-image','capability':cap,'prompt':'hello'if fish else'','assets':[],'parameters':{'voice':'narrator','response_format':'wav'}if fish else{'target_model':target}}
    if not fish:
        async def chunks():yield sample()
        request['assets']=[(await runtime.upload_asset(token['task_token'],chunks(),mime='audio/wav'))['id']]
        synth=copy.deepcopy(value);synth.update(id='fixture-synth',capability='voice.synthesize')
        synth['rate']['meters']=[{'unit':'unicode_scalar','basis':'verified_input_v1','numerator':'1','denominator':'1'}]
        synth['limits'].update(max_units={'unicode_scalar':500},max_assets=0,output_media=resource(['audio/mpeg']))
        synth['limits'].pop('input_media')
        synth['parameters']={'type':'object','properties':{},'required':[],'additionalProperties':False}
        await service.publish(synth,OPERATOR);await service.set_enabled('fixture-synth',True,OPERATOR)
    return runtime,ledger,scope,token['task_token'],request


@pytest.mark.asyncio
async def test_minimax_every_post_has_durable_stage_then_owned_ready_and_cross_public_model_synthesis(db,tmp_path,files,monkeypatch,legacy_media):
    seen=[]
    async def handler(req):
        seen.append((req.method,req.url.path))
        if req.method=='POST'and not req.url.path.endswith('/t2a_v2'):
            async with db.connection()as c:
                stage=await(await c.execute('SELECT * FROM media_voice_steps ORDER BY ordinal DESC LIMIT 1')).fetchone()
                assert stage and stage['request_id']==req.headers['x-client-request-id']
            if req.url.path.endswith('/upload'):return httpx.Response(200,json={'task_id':A,'file_task_id':A,'status':'SUCCESS'})
            assert json.loads(req.content)=={'file_task_id':A}
            return httpx.Response(200,json={'task_id':B,'voice_task_id':B,'status':'SUCCESS'})
        return httpx.Response(200,json={'task_id':C,'status':'SUCCESS','data':{'status':2,'audio':files[0]['mp3'].hex()},'extra_info':{'audio_format':'mp3','usage_characters':5}})
    runtime,ledger,scope,token,body=await build_voice(db,legacy_media,tmp_path,files,monkeypatch,handler)
    try:
        enrolled=await runtime.submit(token,'register',body)
        assert enrolled['actual']=='1'and enrolled['voice']['state']=='ready'
        assert B not in json.dumps(enrolled)and len(seen)==2
        assert(await runtime.submit(token,'register',body))['id']==enrolled['id']and len(seen)==2
        mint=await runtime.mint(scope,{'model':'fixture-synth','capability':'voice.synthesize','invocation_key':'speak'})
        speech={'model':'fixture-synth','capability':'voice.synthesize','prompt':'hello','assets':[],'voice_id':enrolled['voice']['id'],'parameters':{}}
        generated=await runtime.submit(mint['task_token'],'speak',speech)
        await runtime.run_once()
        done=await runtime._view(scope,generated['id'])
        assert done['actual']=='5'and(await runtime.artifact(scope,done['artifacts'][0]['id'],content=True))[0]==files[0]['mp3']
        assert sum(method=='POST'for method,_ in seen)==3
        assert(await ledger.summary(scope))['spent']=='6'
        async with db.connection()as c:
            assert (await(await c.execute("SELECT to_regclass('media_protocol_facts') name")).fetchone())['name'] is not None, 'private bridge facts are not persisted'
            facts=await(await c.execute('SELECT facts FROM media_protocol_facts WHERE job_id=%s',(done['id'],))).fetchone()
            assert facts['facts']=={'usage_characters':5}
        with pytest.raises(PlatformError):await runtime.voices.public(replace(scope,product_id='second'),enrolled['voice']['id'])
    finally:await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('lost_stage',['upload','register'])
async def test_voice_unknown_stage_never_reposts_and_keeps_original_hold(db,tmp_path,files,monkeypatch,lost_stage,legacy_media):
    seen=[]
    def handler(req):
        seen.append(req)
        if req.url.path.endswith('/upload')and lost_stage!='upload':return httpx.Response(200,json={'task_id':A,'file_task_id':A,'status':'SUCCESS'})
        raise httpx.ReadTimeout('synthetic stage response lost',request=req)
    runtime,ledger,scope,token,body=await build_voice(db,legacy_media,tmp_path,files,monkeypatch,handler)
    try:
        with pytest.raises(PlatformError):await runtime.submit(token,'register',body)
        original=await runtime.submit(token,'register',body)
        await runtime.run_once()
        assert original['actual']is None and original['financial_state']=='pending_reconciliation'
        assert len(seen)==(1 if lost_stage=='upload'else 2)
        assert(await ledger.summary(scope))['reserved']=='1'and(await ledger.summary(scope))['spent']=='0'
        async with db.connection()as c:assert(await(await c.execute('SELECT count(*) n FROM media_voices')).fetchone())['n']==0
    finally:await runtime.close()


@pytest.mark.asyncio
async def test_fish_restart_download_failure_only_get_original_task_and_single_decoded_artifact(db,tmp_path,files,monkeypatch,legacy_media):
    seen=[];downloads=0
    def handler(req):
        nonlocal downloads
        seen.append((req.method,req.url.path))
        if req.method=='POST':return httpx.Response(202,json={'task_id':A,'status':'IN_PROGRESS'})
        if req.url.path.endswith('/content'):
            downloads+=1
            if downloads==1:raise httpx.ReadTimeout('synthetic download loss',request=req)
            return httpx.Response(200,content=files[0]['wav'],headers={'content-type':'audio/wav'})
        assert req.url.path.endswith('/tasks/'+A)
        return httpx.Response(200,json={'task_id':A,'status':'SUCCESS','content_url':'/v1/tasks/'+A+'/artifacts/audio/content','mime_type':'audio/wav','size':len(files[0]['wav']),'expires_at':int(time.time())+1000})
    runtime,ledger,scope,token,body=await build_voice(db,legacy_media,tmp_path,files,monkeypatch,handler,fish=True)
    try:
        job=await runtime.submit(token,'fish',body)
        await runtime.run_once()
        assert(await ledger.summary(scope))['spent']=='0'
        await legacy_media.due_work()
        settings,service=runtime.settings,legacy_media.client(runtime.service)
        legacy_media.retain_runtime(runtime)
        runtime=MediaRuntime(db,service.identity,settings,service=service.raw,maintenance=legacy_media.generation,transport=httpx.MockTransport(handler))
        await legacy_media.start_runtime(runtime)
        await runtime.run_once();await runtime.run_once()
        done=await runtime._view(scope,job['id'])
        assert done['actual']=='1'and len(done['artifacts'])==1
        assert sum(method=='POST'for method,_ in seen)==1 and downloads==2
        assert(await runtime.artifact(scope,done['artifacts'][0]['id'],content=True))[0]==files[0]['wav']
    finally:await runtime.close()


@pytest.mark.asyncio
async def test_known_unknown_registration_recovers_only_get_and_settles_once(db,tmp_path,files,monkeypatch,legacy_media):
    seen=[]
    def handler(req):
        seen.append(req)
        if req.url.path.endswith('/upload'):return httpx.Response(200,json={'task_id':A,'file_task_id':A,'status':'SUCCESS'})
        if req.method=='POST':return httpx.Response(200,json={'task_id':B,'status':'unexpected'})
        assert req.url.path.endswith('/tasks/'+B)
        return httpx.Response(200,json={'task_id':B,'voice_task_id':B,'status':'SUCCESS'})
    runtime,ledger,scope,token,body=await build_voice(db,legacy_media,tmp_path,files,monkeypatch,handler)
    try:
        with pytest.raises(PlatformError):await runtime.submit(token,'register',body)
        job=await runtime.submit(token,'register',body)
        await runtime.run_once();await runtime.run_once()
        done=await runtime._view(scope,job['id'])
        assert done['actual']=='1'and done['voice']['state']=='ready'
        assert sum(req.method=='POST'for req in seen)==2 and sum(req.method=='GET'for req in seen)==1
    finally:await runtime.close()


@pytest.mark.asyncio
async def test_voice_http_routes_expose_owned_ready_reference_not_private_provider_id(db,tmp_path,files,monkeypatch,legacy_media):
    import hashlib
    from platform_service.app import create_app
    from platform_service.config import Settings
    def handler(req):return httpx.Response(200,json={'task_id':A,'file_task_id':A,'status':'SUCCESS'}if req.url.path.endswith('/upload')else{'task_id':B,'voice_task_id':B,'status':'SUCCESS'})
    runtime,ledger,scope,token,body=await build_voice(db,legacy_media,tmp_path,files,monkeypatch,handler)
    access='synthetic-product-token'
    async with db.connection()as c:await c.execute("INSERT INTO identity_tokens(token_hash,session_id,kind,expires_at) VALUES (%s,%s,'access',now()+interval '1 hour')",(hashlib.sha256(access.encode()).hexdigest(),scope.session_id))
    app=create_app(Settings('postgresql://localhost/unused','https://accounts.example'),database=db,identity=runtime.service.identity,media_runtime=runtime)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://accounts.example')as client:
            headers={'Authorization':'Bearer '+access}
            enrollment=await runtime.submit(token,'register',body)
            result=await client.get('/v2/media/voices/'+enrollment['voice']['id'],headers=headers)
            assert result.status_code==200 and result.json()['state']=='ready'and B not in result.text
            another=await runtime.mint(scope,{'model':'fixture-image','capability':'voice.enroll','invocation_key':'http-upload'})
            upload=await client.post('/v2/media/assets',content=sample(),headers={'Authorization':'Bearer '+another['task_token'],'Content-Type':'audio/wav'})
            assert upload.status_code==200 and upload.json()['properties']['duration_ms']==10000
            assert(await client.get('/v2/media/voices/'+enrollment['voice']['id'],headers={'Authorization':'Bearer '+token})).status_code==401
    finally:
        await runtime.close();await app.state.execution_runtime.close()


@pytest.mark.asyncio
async def test_voice_tables_are_provisioned_by_common_runtime_grants_and_immutable(db,legacy_media):
    from uuid import uuid4
    from psycopg import sql
    from platform_service.permissions import grant_runtime_permissions
    from platform_service.media.voice_permissions import assert_voice_runtime_permissions
    role='m3_runtime_'+uuid4().hex
    await legacy_media.create_runtime_role(role)
    try:
        async with db.connection()as c:
            await grant_runtime_permissions(c,role)
            for table in ('media_voice_steps','media_voice_step_observations','media_voice_registration_proofs'):
                row=await(await c.execute("SELECT has_table_privilege(%s,%s,'INSERT') allowed, has_table_privilege(%s,%s,'UPDATE,DELETE,TRUNCATE') unsafe",(role,table,role,table))).fetchone()
                assert row=={'allowed':True,'unsafe':False}
            await assert_voice_runtime_permissions(c,role)
    finally:
        await legacy_media.drop_runtime_role(role)


@pytest.mark.asyncio
@pytest.mark.parametrize('crash_after',['upload','register'])
async def test_completed_stage_crash_resumes_next_unique_stage_or_finishes_without_reposting(db,tmp_path,files,monkeypatch,crash_after,legacy_media):
    seen=[]
    def handler(req):
        seen.append(req)
        return httpx.Response(200,json={'task_id':A,'file_task_id':A,'status':'SUCCESS'}if req.url.path.endswith('/upload')else{'task_id':B,'voice_task_id':B,'status':'SUCCESS'})
    runtime,ledger,scope,token,body=await build_voice(db,legacy_media,tmp_path,files,monkeypatch,handler)
    original_stage=runtime.voice_execution.stage
    async def interrupt(*args,**kwargs):
        result=await original_stage(*args,**kwargs)
        if args[2].stage==crash_after:raise RuntimeError('synthetic crash after committed observation')
        return result
    monkeypatch.setattr(runtime.voice_execution,'stage',interrupt)
    try:
        with pytest.raises(PlatformError):await runtime.submit(token,'register',body)
        job=await runtime.submit(token,'register',body)
        monkeypatch.setattr(runtime.voice_execution,'stage',original_stage)
        await runtime.run_once();await runtime.run_once()
        done=await runtime._view(scope,job['id'])
        assert done['actual']=='1'and done['voice']['state']=='ready'
        assert len(seen)==2 and all(req.method=='POST'for req in seen)
    finally:await runtime.close()


@pytest.mark.asyncio
async def test_audio_voice_runtime_start_requires_019_and_stage_tables_before_dispatch(db,tmp_path,files,monkeypatch,legacy_media):
    runtime,*_=await build_voice(db,legacy_media,tmp_path,files,monkeypatch,lambda req:None,fish=True)
    try:
        async with db.connection()as c:await c.execute('ALTER TABLE media_voice_steps RENAME TO missing_voice_steps')
        with pytest.raises(PlatformError):await runtime.start()
    finally:await runtime.close()


@pytest.mark.asyncio
async def test_voice_async_terminal_requires_worker_lease_fence(db,tmp_path,files,monkeypatch,legacy_media):
    def handler(req):return httpx.Response(202,json={'task_id':A,'status':'IN_PROGRESS'})
    runtime,_,scope,token,body=await build_voice(db,legacy_media,tmp_path,files,monkeypatch,handler,fish=True)
    try:
        public=await runtime.submit(token,'fish',body)
        async with db.connection()as c:
            job=await(await c.execute('SELECT * FROM media_jobs WHERE id=%s',(public['id'],))).fetchone()
            with pytest.raises(PlatformError)as error:await runtime.service._work_fence(c,job,None)
            assert error.value.code=='media_work_lease_lost'
    finally:await runtime.close()


@pytest.mark.asyncio
async def test_m3_cannot_register_unproved_voice_through_legacy_core(db,tmp_path,files,monkeypatch,legacy_media):
    import psycopg
    runtime,_,scope,token,body=await build_voice(db,legacy_media,tmp_path,files,monkeypatch,lambda req:None)
    try:
        context=await runtime.service.authenticate_task(token)
        asset=await runtime.service.get_asset(context,body['assets'][0])
        job=await legacy_media.client(runtime.service).admit(context,key='unproved',request=body,bindings=runtime.bindings,verified_inputs={asset['id']:{k:asset[k]for k in ('sha256','size','mime','properties')}})
        await legacy_media.client(runtime.service).record_provider_attempt(job['id'],job['attempt_id'],provider_request_hash='b'*64,provider_scope_hash='c'*64,upstream_model=TARGET,request_id='unproved')
        await legacy_media.client(runtime.service).dispatch(job['id'],job['attempt_id'])
        with pytest.raises((PlatformError,psycopg.Error)):
            await legacy_media.client(runtime.service).register_voice(job['id'],job['attempt_id'],provider_voice_id=B,properties={'name':'not-ready'})
    finally:await runtime.close()


@pytest.mark.asyncio
async def test_owned_voice_scope_provider_profile_target_and_credential_boundaries(db,tmp_path,files,monkeypatch,legacy_media):
    from platform_service.media_config import voice_boundary
    from platform_service.media.voice_execution import owned
    def handler(req):return httpx.Response(200,json={'task_id':A,'file_task_id':A,'status':'SUCCESS'}if req.url.path.endswith('/upload')else{'task_id':B,'voice_task_id':B,'status':'SUCCESS'})
    runtime,_,scope,token,body=await build_voice(db,legacy_media,tmp_path,files,monkeypatch,handler)
    try:
        job=await runtime.submit(token,'register',body)
        request={'model':'fixture-synth','capability':'voice.synthesize','voice_id':job['voice']['id']}
        provider=runtime.settings.providers['fixture']
        voice,boundary=await owned(runtime,scope,request,provider)
        assert voice.id==job['voice']['id']and voice.binding_hash!=runtime.bindings['fixture-synth']['binding_hash']
        for change in ({'user_id':'other'},{'product_id':'second'},{'tenant_id':'other'},{'billing_account_id':'other'}):
            with pytest.raises(PlatformError):await owned(runtime,replace(scope,**change),request,provider)
        for candidate in [replace(provider,secret='different-credential'),replace(provider,version='2'),
                replace(provider,base_url='http://127.0.0.1:19992'),replace(provider,voice_profile={**provider.voice_profile,'formats':['mp3']})]:
            with pytest.raises(PlatformError):await owned(runtime,scope,request,candidate)
        for patch in ({'target_model':'other'},{'protocol':'newapi_dashscope_voice_v1'},{'provider_id':'other'},{'provider_version':'2'},
                      {'voice_boundary_hash':voice_boundary(runtime.settings,'other',provider,TARGET)}):
            with pytest.raises(PlatformError):
                await runtime.voices.resolve(scope,voice.id,**{'protocol':provider.adapter,'target_model':TARGET,'provider_id':'fixture',
                    'provider_version':'1','binding_hash':runtime.bindings['fixture-synth']['binding_hash'],
                    'provider_scope_hash':voice.provider_scope_hash,'voice_boundary_hash':boundary,**patch})
    finally:await runtime.close()


@pytest.mark.asyncio
async def test_voice_reference_privilege_is_rejected_by_common_startup_role_check(db,legacy_media):
    from uuid import uuid4
    from psycopg import sql
    from platform_service.permissions import grant_runtime_permissions,assert_safe_runtime_role
    role='m3_refs_'+uuid4().hex
    await legacy_media.create_runtime_role(role)
    try:
        async with db.connection()as c:
            await grant_runtime_permissions(c,role)
            await c.execute(sql.SQL('GRANT REFERENCES ON media_voice_registration_proofs TO {}').format(sql.Identifier(role)))
        with pytest.raises(PlatformError):
            async with db.connection()as c:await assert_safe_runtime_role(c,role)
    finally:
        await legacy_media.drop_runtime_role(role)


@pytest.mark.asyncio
async def test_dashscope_legacy_enroll_is_closed_without_hiding_supported_synthesis(db,tmp_path,files,monkeypatch,legacy_media):
    import test_media_credits as fixture
    adapter='newapi_dashscope_voice_v1'
    value=definition('voice.synthesize');value['adapter']=adapter
    value['limits'].update(max_units={'unicode_scalar':100},max_assets=0,output_media=resource(['audio/wav']))
    value['rate']['meters']=[{'unit':'unicode_scalar','basis':'verified_input_v1','numerator':'1','denominator':'1'}]
    monkeypatch.setattr(fixture,'BINDINGS',{**fixture.BINDINGS,'fixture-image':{**fixture.BINDINGS['fixture-image'],'adapter':adapter}})
    service,ledger,scope,*_=await legacy_media.setup(db,media_definition=value)
    provider=MediaProvider('1','http://127.0.0.1:9999','synthetic-private',{'fixture-image':'cosyvoice-v2'},adapter=adapter,
        capabilities=('voice.enroll','voice.synthesize'),voice_profile={'target_models':['cosyvoice-v2']})
    runtime=MediaRuntime(db,service.identity,MediaSettings(mode='test',storage_root=legacy_media.source_path,providers={'fixture':provider},av_tools=AVTools(*files[1])),service=service)
    try:
        assert(await runtime.catalog(scope))['models']
        assert(await runtime.capabilities('easel'))['test_media_execution_enabled']
        with pytest.raises(PlatformError)as error:runtime._provider('fixture-image','voice.enroll')
        assert error.value.code=='voice_sample_publication_unavailable'
        assert(await ledger.summary(scope))['reserved']=='0'
    finally:await runtime.close()


@pytest.mark.asyncio
async def test_real_minimum_runtime_role_completes_voice_stages_without_owner_privileges(db,test_dsn,tmp_path,files,monkeypatch,legacy_media):
    import secrets
    from uuid import uuid4
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from platform_service.db import Database
    from platform_service.identity import IdentityService
    from platform_service.permissions import grant_runtime_permissions
    def handler(req):return httpx.Response(200,json={'task_id':A,'file_task_id':A,'status':'SUCCESS'}if req.url.path.endswith('/upload')else{'task_id':B,'voice_task_id':B,'status':'SUCCESS'})
    initial,_,scope,token,body=await build_voice(db,legacy_media,tmp_path,files,monkeypatch,handler)
    settings=initial.settings;await initial.close()
    role='m3_minimum_'+uuid4().hex;password=secrets.token_urlsafe(24)
    await legacy_media.create_runtime_role(role,password)
    limited=Database(make_conninfo(test_dsn,user=role,password=password),schema=db.schema)
    runtime=None
    try:
        await limited.open();await limited.require_runtime_role()
        generation=await legacy_media.restart_generation(limited)
        runtime=MediaRuntime(limited,IdentityService(limited,None),settings,maintenance=generation,transport=httpx.MockTransport(handler))
        await runtime.start()
        job=await runtime.submit(token,'minimum-role',body)
        assert job['actual']=='1'and job['voice']['state']=='ready'
    finally:
        if runtime is not None:await runtime.close()
        await limited.close()
        await legacy_media.drop_runtime_role(role)
