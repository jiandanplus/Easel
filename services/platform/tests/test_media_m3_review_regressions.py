"""Independent-review repros; real isolated PostgreSQL, synthetic provider only."""
import asyncio
from datetime import timedelta
from types import SimpleNamespace
import httpx
import pytest
from b2_legacy_media_fixtures import legacy_media, legacy_db as db, legacy_dsn as test_dsn
from psycopg.types.json import Jsonb
from test_media_av_artifacts import files
from test_media_audio_runtime import build_audio
from test_media_voice_stages import voice_job, start, observe_complete
from platform_service.errors import PlatformError

@pytest.mark.asyncio
async def test_sql_protocol_facts_enforce_same_strict_numbers_as_python(db,tmp_path,files,monkeypatch,legacy_media):
    from platform_service.media.usage_facts import validate_protocol_facts
    runtime,_,scope,token,body=await build_audio(db,legacy_media,tmp_path,files,monkeypatch,lambda req:None,transcribe=True,usage='tokens')
    try:
        async def chunks():yield files[0]['wav']
        body['assets']=[(await runtime.upload_asset(token,chunks(),mime='audio/wav'))['id']]
        context=await runtime.service.authenticate_task(token)
        asset=await runtime.service.get_asset(context,body['assets'][0])
        job=await legacy_media.client(runtime.service).admit(context,key='independent',request=body,bindings=runtime.bindings,verified_inputs={asset['id']:{k:asset[k]for k in ('sha256','size','mime','properties')}})
        await legacy_media.client(runtime.service).record_provider_attempt(job['id'],job['attempt_id'],provider_request_hash='b'*64,provider_scope_hash='c'*64,upstream_model='fixture',request_id='independent')
        await legacy_media.client(runtime.service).dispatch(job['id'],job['attempt_id'])
        invalid=[{'type':None,'input_tokens':1,'output_tokens':2,'total_tokens':3},
                 {'type':'tokens','input_tokens':1,'output_tokens':2,'total_tokens':999},
                 {'type':'tokens','input_tokens':9223372036854775808,'output_tokens':0,'total_tokens':9223372036854775808}]
        from uuid import uuid4
        import secrets
        from psycopg import sql
        from psycopg.conninfo import make_conninfo
        from platform_service.db import Database
        from platform_service.permissions import grant_runtime_permissions
        import os,json
        from pathlib import Path
        role='review_'+uuid4().hex;password=secrets.token_urlsafe(24)
        await legacy_media.create_runtime_role(role,password)
        dsn=legacy_media.env.dsn('owner')
        low=Database(make_conninfo(dsn,user=role,password=password),schema=db.schema)
        await low.open();await low.require_runtime_role()
        accepted=[]
        from psycopg.errors import CheckViolation
        for facts in invalid:
            with pytest.raises(PlatformError):validate_protocol_facts('openai_transcriptions_v1',facts)
            try:
              from platform_service.media_maintenance.runtime import GuardedDatabase,operation
              with operation(legacy_media.client(runtime.service).job_permit(job['id'])):
               async with GuardedDatabase(low).connection()as c:
                 async with c.transaction(force_rollback=True):
                    await c.execute('INSERT INTO media_protocol_facts(job_id,attempt_id,binding_hash,namespace,response_hash,facts) VALUES (%s,%s,%s,%s,%s,%s)',
                        (job['id'],job['attempt_id'],job['binding_hash'],'openai_transcriptions_v1','d'*64,Jsonb(facts)))
                    await c.execute('SET CONSTRAINTS ALL IMMEDIATE')
                    accepted.append(facts)
            except CheckViolation as error:
                assert 'media maintenance permit required' not in str(error)
        await low.close()
        await legacy_media.drop_runtime_role(role)
        print('NONOWNER_RAW_SQL_ACCEPTED_INVALID',accepted)
        assert accepted==[], 'SQL accepted facts rejected by strict Python validator'
    finally:await runtime.close()

@pytest.mark.asyncio
async def test_dashscope_slow_stream_respects_remaining_chain_budget(db,monkeypatch,legacy_media):
    import platform_service.media.voices as module
    from platform_service.media.voice_execution import VoiceExecution
    from platform_service.media.adapters.newapi_voice import DashScopeVoiceAdapter,BridgeVoiceProfile
    protocol='newapi_dashscope_voice_v1'
    service,ledger,scope,job,stages,refs=await voice_job(db,legacy_media,protocol)
    create=await start(stages,job,refs,key='create',stage='create',protocol=protocol)
    await observe_complete(stages,create['id'],key='created',state='completed',http_status=200,provider_task_id='task_'+'a'*32,response_hash='d'*64,
        facts={'operation':'create_voice','voice_task_id':'task_'+'a'*32,'target_model':'cosyvoice-v2','voice_status':'UNVERIFIED'})
    async with db.connection()as c:
        row=await(await c.execute('SELECT dispatched_at FROM media_jobs WHERE id=%s',(job['id'],))).fetchone()
    real_now=module.now; offset=row['dispatched_at']+timedelta(seconds=299.90)-real_now()
    monkeypatch.setattr(module,'now',lambda:real_now()+offset)
    class Slow(httpx.AsyncByteStream):
        async def __aiter__(self):
            import json
            payload=json.dumps({'output':{'task_id':'task_'+'b'*32,'task_status':'SUCCEEDED','voice_task_id':'task_'+'b'*32,'target_model':'cosyvoice-v2','voice_status':'OK'},'usage':{'count':1}}).encode()
            for index in range(0,len(payload),30):
                await asyncio.sleep(.03)
                yield payload[index:index+30]
    calls=[]
    def handler(req):calls.append(req.method);return httpx.Response(200,headers={'content-type':'application/json'},stream=Slow())
    provider=SimpleNamespace(adapter=protocol,base_url='http://127.0.0.1:9999',secret='synthetic-only',version='1',models={'voice-enrollment':'cosyvoice-v2'},capabilities=('voice.enroll',))
    adapter=DashScopeVoiceAdapter(provider,'test',BridgeVoiceProfile(target_models=('cosyvoice-v2',)),binding_hash='a'*64,provider_scope_hash='c'*64)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler))as client:
        runner=VoiceExecution(SimpleNamespace(service=service,_client=client))
        with pytest.raises(TimeoutError):
            await runner.verify(job,adapter,permit=service.job_permit(job['id']))
        with pytest.raises(PlatformError):
            await runner.verify(job,adapter,permit=service.job_permit(job['id']))
    assert calls==['POST']
    async with db.connection() as c:
        last=await stages._latest(c,(await stages._steps(c,job['id']))[-1]['id'])
        assert last['state']=='unknown'
    assert (await ledger.summary(scope))['spent']=='0'


@pytest.mark.asyncio
async def test_facts_sql_closed_schema_and_numeric_boundaries(db,legacy_media):
    from platform_service.media.usage_facts import validate_protocol_facts
    token={'type':'tokens','input_tokens':1,'output_tokens':2,'total_tokens':3}
    duration={'type':'duration','seconds':'0.001','rounding':'ceil_milliseconds_v1'}
    cases=[('openai_transcriptions_v1',token,True),
        ('openai_transcriptions_v1',duration,True),
        ('newapi_minimax_voice_v1',{'usage_characters':9223372036854775807,'audio_length_ms':'900000'},True),
        ('newapi_dashscope_voice_v1',{'characters':0},True)]
    for field in token:
        cases.append(('openai_transcriptions_v1',{**token,field:None},False))
    for field in ('input_tokens','output_tokens','total_tokens'):
        for value in (True,-1,1.5,'1',9223372036854775808):
            cases.append(('openai_transcriptions_v1',{**token,field:value},False))
    for value in (None,1,'NaN','Infinity','-1','9223372036854775.808','1e999999','x'*65):
        cases.append(('openai_transcriptions_v1',{**duration,'seconds':value},False))
    for value in (None,'floor_milliseconds_v1',True):
        cases.append(('openai_transcriptions_v1',{**duration,'rounding':value},False))
    for ns,field in [('newapi_minimax_voice_v1','usage_characters'),('newapi_dashscope_voice_v1','characters')]:
        for value in (None,True,-1,1.5,'1',9223372036854775808):cases.append((ns,{field:value},False))
        cases.append((ns,{field:1,'private_id':'should-not-store'},False))
    for value in (None,1,'900000.0001','-1','NaN','Infinity','1e999999'):
        cases.append(('newapi_minimax_voice_v1',{'usage_characters':1,'audio_length_ms':value},False))
    async with db.connection() as c:
        for ns,facts,valid in cases:
            if valid:validate_protocol_facts(ns,facts)
            else:
                with pytest.raises(PlatformError):validate_protocol_facts(ns,facts)
            row=await(await c.execute('SELECT valid_media_protocol_facts(%s,%s) valid',(ns,Jsonb(facts)))).fetchone()
            assert row['valid'] is valid,(ns,facts)


@pytest.mark.asyncio
@pytest.mark.parametrize('budget',['remaining','worker_wall'])
async def test_recovered_voice_get_shares_original_deadline_and_keeps_hold(db,tmp_path,files,monkeypatch,budget,legacy_media):
    import json
    import platform_service.media.voices as clock
    from test_media_voice_runtime import build_voice,A,B
    calls=[]
    class Slow(httpx.AsyncByteStream):
        async def __aiter__(self):
            raw=json.dumps({'task_id':B,'voice_task_id':B,'status':'SUCCESS'}).encode()
            for index in range(0,len(raw),10):
                await asyncio.sleep(.2)
                yield raw[index:index+10]
    def handler(req):
        calls.append((req.method,req.url.path))
        if req.method=='GET':return httpx.Response(200,headers={'content-type':'application/json'},stream=Slow())
        if req.url.path.endswith('/upload'):return httpx.Response(200,json={'task_id':A,'file_task_id':A,'status':'SUCCESS'})
        return httpx.Response(200,json={'task_id':B,'status':'unexpected'})
    runtime,ledger,scope,token,body=await build_voice(db,legacy_media,tmp_path,files,monkeypatch,handler)
    try:
        with pytest.raises(PlatformError):await runtime.submit(token,'lost',body)
        async with db.connection() as c:row=await(await c.execute('SELECT dispatched_at FROM media_jobs')).fetchone()
        real_now=clock.now;offset=row['dispatched_at']+timedelta(seconds=86399.0 if budget=='remaining' else 301)-real_now()
        if budget=='worker_wall':object.__setattr__(runtime.settings,'worker_wall_seconds',0.1)
        monkeypatch.setattr(clock,'now',lambda:real_now()+offset)
        await runtime.run_once()
        offset=row['dispatched_at']+timedelta(hours=24,seconds=1)-real_now()
        await runtime.run_once()
        assert [method for method,path in calls]==['POST','POST','GET']
        assert calls[-1][1].endswith('/tasks/'+B)
        summary=await ledger.summary(scope)
        assert summary['spent']=='0' and summary['reserved']=='1'
        async with db.connection() as c:
            assert (await(await c.execute('SELECT count(*) n FROM media_voices')).fetchone())['n']==0
            assert (await(await c.execute('SELECT count(*) n FROM media_receipts')).fetchone())['n']==0
    finally:await runtime.close()


@pytest.mark.asyncio
async def test_known_registration_task_still_get_recovers_after_five_minutes(db,tmp_path,files,monkeypatch,legacy_media):
    import platform_service.media.voices as clock
    from test_media_voice_runtime import build_voice,A,B
    calls=[]
    def handler(req):
        calls.append(req.method)
        if req.url.path.endswith('/upload'):return httpx.Response(200,json={'task_id':A,'file_task_id':A,'status':'SUCCESS'})
        if req.method=='POST':return httpx.Response(200,json={'task_id':B,'status':'unexpected'})
        assert req.url.path.endswith('/tasks/'+B)
        return httpx.Response(200,json={'task_id':B,'voice_task_id':B,'status':'SUCCESS'})
    runtime,ledger,scope,token,body=await build_voice(db,legacy_media,tmp_path,files,monkeypatch,handler)
    try:
        with pytest.raises(PlatformError):await runtime.submit(token,'late-get',body)
        job=await runtime.submit(token,'late-get',body)
        async with db.connection()as c:row=await(await c.execute('SELECT dispatched_at FROM media_jobs')).fetchone()
        real_now=clock.now; offset=row['dispatched_at']+timedelta(seconds=301)-real_now()
        monkeypatch.setattr(clock,'now',lambda:real_now()+offset)
        await runtime.run_once();await runtime.run_once()
        done=await runtime._view(scope,job['id'])
        print('LATE_RECOVERY',{'calls':calls,'actual':done['actual'],'financial_state':done['financial_state'],'reserved':(await ledger.summary(scope))['reserved']})
        assert calls==['POST','POST','GET'], 'known task should retain 24h GET recovery, without another POST'
        assert done['actual']=='1' and done['voice']['state']=='ready'
    finally:await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('crash_after',['upload','register'])
async def test_completed_stage_after_post_deadline_only_finishes_existing_registration(db,tmp_path,files,monkeypatch,crash_after,legacy_media):
    import platform_service.media.voices as clock
    from test_media_voice_runtime import build_voice,A,B
    calls=[]
    def handler(req):
        calls.append(req.method)
        return httpx.Response(200,json={'task_id':A,'file_task_id':A,'status':'SUCCESS'} if req.url.path.endswith('/upload') else {'task_id':B,'voice_task_id':B,'status':'SUCCESS'})
    runtime,ledger,scope,token,body=await build_voice(db,legacy_media,tmp_path,files,monkeypatch,handler)
    stage=runtime.voice_execution.stage
    async def interrupted(*args,**kwargs):
        result=await stage(*args,**kwargs)
        if args[2].stage==crash_after:raise RuntimeError('synthetic crash after committed stage')
        return result
    monkeypatch.setattr(runtime.voice_execution,'stage',interrupted)
    try:
        with pytest.raises(PlatformError):await runtime.submit(token,'late-completed',body)
        job=await runtime.submit(token,'late-completed',body)
        monkeypatch.setattr(runtime.voice_execution,'stage',stage)
        async with db.connection() as c:row=await(await c.execute('SELECT dispatched_at FROM media_jobs')).fetchone()
        real_now=clock.now;offset=row['dispatched_at']+timedelta(seconds=301)-real_now()
        monkeypatch.setattr(clock,'now',lambda:real_now()+offset)
        await runtime.run_once();await runtime.run_once()
        done=await runtime._view(scope,job['id'])
        summary=await ledger.summary(scope)
        if crash_after=='register':
            assert calls==['POST','POST'] and done['actual']=='1' and done['voice']['state']=='ready'
            assert summary['spent']=='1' and summary['reserved']=='0'
        else:
            assert calls==['POST'] and done['actual'] is None and done['financial_state']=='pending_reconciliation'
            assert summary['spent']=='0' and summary['reserved']=='1'
        async with db.connection() as c:
            assert (await(await c.execute('SELECT count(*) n FROM media_receipts')).fetchone())['n']==int(crash_after=='register')
    finally:await runtime.close()
