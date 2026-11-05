"""Real PostgreSQL phase history; no network and no additional wallet charges."""
import asyncio
from dataclasses import replace
import pytest
from b2_legacy_media_fixtures import legacy_media, legacy_db as db, legacy_dsn as test_dsn
import psycopg
from platform_service.errors import PlatformError
from test_media_credits import setup,OPERATOR
from test_media_contracts import definition

PROTOCOL='newapi_minimax_voice_v1'
TARGET='easel-minimax-speech-2.8-hd'


async def voice_job(db,legacy_media,protocol=PROTOCOL):
    from platform_service.media.voices import VoiceStages
    service,ledger,scope,ctx,*_=await legacy_media.setup(db)
    d=definition('voice.enroll');d['id']='voice-enrollment';d['adapter']=protocol
    d['rate']={'version':'registration-v1','meters':[{'unit':'registration','basis':'verified_delivery_v1','numerator':'3','denominator':'2'}]}
    d['limits']['max_units']={'registration':1}
    target=TARGET if protocol==PROTOCOL else 'cosyvoice-v2'
    d['parameters']={'type':'object','properties':{'target_model':{'type':'string','enum':[target]}},'required':['target_model'],'additionalProperties':False}
    binding={'voice-enrollment':{'provider_id':'fixture','provider_version':'1','adapter':protocol,'protocol_version':'1','binding_hash':'a'*64}}
    await service.publish(d,OPERATOR);await service.set_enabled(d['id'],True,OPERATOR)
    token=await service.mint(scope,model=d['id'],capability='voice.enroll',invocation_key='enroll',mode='test',bindings=binding)
    context=await service.authenticate_task(token['task_token'])
    sample=await service.register_asset(context,storage_key='1'*32,sha256='2'*64,size=10,mime='audio/wav',properties={'format':'WAV','duration_ms':1000,'channels':1,'sample_rate':24000})
    verified={sample['id']:{k:sample[k] for k in ('sha256','size','mime','properties')}}
    job=await service.admit(context,key='enroll',request={'model':d['id'],'capability':'voice.enroll','prompt':'','assets':[sample['id']],'parameters':{'target_model':target}},bindings=binding,verified_inputs=verified)
    await service.record_provider_attempt(job['id'],job['attempt_id'],provider_request_hash='b'*64,provider_scope_hash='c'*64,upstream_model='easel-minimax-upload',request_id='logical-'+job['id'])
    await service.dispatch(job['id'],job['attempt_id'])
    stages=legacy_media.stages(service)
    initial={'asset_id':sample['id'],'asset_sha256':sample['sha256'],'target_model':target}
    return service,ledger,scope,job,stages,initial


async def start(stages,job,refs,*,key='upload',stage='upload',wire_hash='b'*64,previous=None,protocol=PROTOCOL):
    return await stages.begin(job['id'],job['attempt_id'],key=key,protocol=protocol,stage=stage,
                              wire_hash=wire_hash,refs=refs,previous_step_id=previous)


async def uploaded(stages,step,*,key='upload-complete',task='task_'+'a'*32):
    await stages.record_headers(step['id'],http_status=200,http_request_id='req_upload')
    return await stages.observe(step['id'],key=key,state='completed',http_status=200,http_request_id='req_upload',
                                provider_task_id=task,response_hash='d'*64,facts={'operation':'upload','file_task_id':task})


async def observe_complete(stages,step_id,**values):
    await stages.record_headers(step_id,http_status=values['http_status'],http_request_id=values.get('http_request_id'))
    return await stages.observe(step_id,**values)


@pytest.mark.asyncio
async def test_concurrent_same_stage_grants_one_post_permit_and_exact_replay(db,legacy_media):
    service,ledger,scope,job,stages,refs=await voice_job(db,legacy_media)
    before=await ledger.summary(scope)
    values=await asyncio.gather(*(start(stages,job,refs) for _ in range(5)))
    assert sum(value['dispatch'] for value in values)==1 and len({value['id'] for value in values})==1
    assert len({value['request_id'] for value in values})==1
    with pytest.raises(PlatformError):await start(stages,job,refs,wire_hash='e'*64)
    assert await ledger.summary(scope)==before
    async with db.connection() as c:
        assert (await (await c.execute("SELECT count(*) n FROM credit_reservations WHERE operation='media'")).fetchone())['n']==1


@pytest.mark.asyncio
async def test_crash_after_durable_begin_is_unknown_and_never_reposts_or_advances(db,legacy_media):
    service,ledger,scope,job,stages,refs=await voice_job(db,legacy_media)
    step=await start(stages,job,refs)
    assert step['dispatch'] is True
    assert (await start(stages,job,refs))['dispatch'] is False
    snapshot=await stages.snapshot(scope,job['id'])
    assert snapshot['state']=='unknown'
    with pytest.raises(PlatformError):
        await start(stages,job,{'file_task_id':'task_'+'a'*32,'target_model':TARGET},key='register',stage='register',wire_hash='e'*64,previous=step['id'])
    assert (await ledger.summary(scope))['reserved']=='2'


@pytest.mark.asyncio
async def test_completed_upload_freezes_owned_task_reference_and_registration_keeps_one_hold(db,legacy_media):
    service,ledger,scope,job,stages,refs=await voice_job(db,legacy_media)
    step=await start(stages,job,refs);result=await uploaded(stages,step)
    assert (await uploaded(stages,step))['id']==result['id']
    expected={'file_task_id':'task_'+'a'*32,'target_model':TARGET}
    for invalid in [{**expected,'file_task_id':'task_'+'b'*32},{**expected,'target_model':'other'},dict(expected,provider_voice_id='raw-supplier')]:
        with pytest.raises(PlatformError):await start(stages,job,invalid,key='register',stage='register',wire_hash='e'*64,previous=step['id'])
    next_step=await start(stages,job,expected,key='register',stage='register',wire_hash='e'*64,previous=step['id'])
    assert next_step['dispatch'] and next_step['ordinal']==1 and next_step['previous_step_id']==step['id']
    assert (await ledger.summary(scope))['reserved']=='2'
    public=await stages.snapshot(scope,job['id'])
    assert 'task_' not in str(public) and 'file_task_id' not in str(public) and 'refs' not in public


@pytest.mark.asyncio
async def test_unknown_or_rejected_stage_does_not_release_or_create_next_post(db,legacy_media):
    service,ledger,scope,job,stages,refs=await voice_job(db,legacy_media)
    step=await start(stages,job,refs)
    await stages.observe(step['id'],key='timeout',state='unknown')
    with pytest.raises(PlatformError):
        await start(stages,job,{'file_task_id':'task_'+'a'*32,'target_model':TARGET},key='register',stage='register',wire_hash='e'*64,previous=step['id'])
    assert (await start(stages,job,refs))['dispatch'] is False
    assert (await ledger.summary(scope))['reserved']=='2'
    async with db.connection() as c:
        assert (await (await c.execute('SELECT count(*) n FROM media_voice_steps')).fetchone())['n']==1


@pytest.mark.asyncio
async def test_completed_stage_cannot_change_provider_ref_and_history_is_immutable(db,legacy_media,record_property):
    service,ledger,scope,job,stages,refs=await voice_job(db,legacy_media)
    step=await start(stages,job,refs);await uploaded(stages,step)
    with pytest.raises(PlatformError):await uploaded(stages,step,key='other',task='task_'+'b'*32)
    before=await ledger.summary(scope)
    async with db.connection() as c:
        history=await(await c.execute('SELECT * FROM media_voice_steps ORDER BY id')).fetchall()
        observations=await(await c.execute('SELECT * FROM media_voice_step_observations ORDER BY id')).fetchall()
    for index,command in enumerate(["UPDATE media_voice_steps SET wire_hash=repeat('0',64)",
                    "DELETE FROM media_voice_step_observations"]):
        with pytest.raises(psycopg.Error) as rejected:
            async with legacy_media.sql(service.job_permit(job['id'])) as c:await c.execute(command)
        error=rejected.value
        record_property('history_rejection_'+str(index),error.diag.message_primary+'; '+error.diag.context)
        assert error.sqlstate=='23514'
        assert 'media maintenance permit required' not in str(error)
        assert error.diag.message_primary=='immutable ledger or plan history'
        assert 'deny_immutable_change()' in error.diag.context
    async with db.connection() as c:
        assert await(await c.execute('SELECT * FROM media_voice_steps ORDER BY id')).fetchall()==history
        assert await(await c.execute('SELECT * FROM media_voice_step_observations ORDER BY id')).fetchall()==observations
    assert await ledger.summary(scope)==before
    assert before['reserved']=='2' and before['spent']=='0'


@pytest.mark.asyncio
async def test_current_b2_contract_forbids_truncate_with_original_job_permit(db,legacy_media,record_property):
    service,ledger,scope,job,stages,refs=await voice_job(db,legacy_media)
    step=await start(stages,job,refs);await uploaded(stages,step)
    before=await ledger.summary(scope)
    async with db.connection() as c:
        history=await(await c.execute('SELECT * FROM media_voice_steps ORDER BY id')).fetchall()
        observations=await(await c.execute('SELECT * FROM media_voice_step_observations ORDER BY id')).fetchall()
    with pytest.raises(psycopg.Error) as rejected:
        async with legacy_media.sql(service.job_permit(job['id'])) as c:await c.execute('TRUNCATE media_voice_steps')
    error=rejected.value
    record_property('original_truncate_rejection',str(error))
    assert error.sqlstate=='0A000'
    assert error.diag.message_primary=='cannot truncate a table referenced in a foreign key constraint'
    # PostgreSQL's FK precheck masks every BEFORE TRUNCATE trigger for the
    # original command. CASCADE reaches the current B2 no-TRUNCATE contract.
    with pytest.raises(psycopg.Error) as rejected:
        async with legacy_media.sql(service.job_permit(job['id'])) as c:await c.execute('TRUNCATE media_voice_steps CASCADE')
    error=rejected.value
    record_property('truncate_rejection',str(error))
    assert (error.sqlstate,error.diag.message_primary)==('23514','media maintenance permit required')
    assert 'guard_media_mutation()' in error.diag.context
    assert 'assert_current(' not in error.diag.context
    async with db.connection() as c:
        assert await(await c.execute('SELECT * FROM media_voice_steps ORDER BY id')).fetchall()==history
        assert await(await c.execute('SELECT * FROM media_voice_step_observations ORDER BY id')).fetchall()==observations
    assert await ledger.summary(scope)==before


@pytest.mark.asyncio
async def test_revoked_owner_cannot_advance_but_original_hold_survives(db,legacy_media):
    service,ledger,scope,job,stages,refs=await voice_job(db,legacy_media)
    step=await start(stages,job,refs);await uploaded(stages,step)
    with pytest.raises(PlatformError):await stages.snapshot(replace(scope,product_id='second'),job['id'])
    async with db.connection() as c:await c.execute("UPDATE identity_sessions SET revoked_at=clock_timestamp() WHERE id='session'")
    with pytest.raises(PlatformError):
        await start(stages,job,{'file_task_id':'task_'+'a'*32,'target_model':TARGET},key='register',stage='register',wire_hash='e'*64,previous=step['id'])
    assert (await ledger.summary(scope))['reserved']=='2'


@pytest.mark.asyncio
async def test_dashscope_verify_keeps_original_create_reference_and_stops_when_ready(db,legacy_media):
    protocol='newapi_dashscope_voice_v1'
    service,ledger,scope,job,stages,refs=await voice_job(db,legacy_media,protocol)
    create=await start(stages,job,refs,key='create',stage='create',protocol=protocol)
    create_id='task_'+'a'*32;query_id='task_'+'b'*32
    await observe_complete(stages,create['id'],key='created',state='completed',http_status=200,provider_task_id=create_id,response_hash='d'*64,
        facts={'operation':'create_voice','voice_task_id':create_id,'target_model':refs['target_model'],'voice_status':'UNVERIFIED'})
    expected={'voice_task_id':create_id,'target_model':refs['target_model']}
    verify=await start(stages,job,expected,key='verify-1',stage='verify',wire_hash='e'*64,previous=create['id'],protocol=protocol)
    await observe_complete(stages,verify['id'],key='deploying',state='completed',http_status=200,provider_task_id=query_id,response_hash='f'*64,
        facts={'operation':'query_voice','voice_task_id':query_id,'target_model':refs['target_model'],'voice_status':'DEPLOYING'})
    with pytest.raises(PlatformError):
        await start(stages,job,{**expected,'voice_task_id':query_id},key='verify-2',stage='verify',wire_hash='e'*64,previous=verify['id'],protocol=protocol)
    second=await start(stages,job,expected,key='verify-2',stage='verify',wire_hash='e'*64,previous=verify['id'],protocol=protocol)
    ready_id='task_'+'c'*32
    await observe_complete(stages,second['id'],key='ready',state='completed',http_status=200,provider_task_id=ready_id,response_hash='a'*64,
        facts={'operation':'query_voice','voice_task_id':ready_id,'target_model':refs['target_model'],'voice_status':'OK'})
    with pytest.raises(PlatformError):
        await start(stages,job,expected,key='verify-3',stage='verify',wire_hash='e'*64,previous=second['id'],protocol=protocol)
    assert (await ledger.summary(scope))['reserved']=='2'


@pytest.mark.asyncio
async def test_direct_sql_cannot_insert_unassociated_first_wire_or_a_skipped_stage(db,legacy_media,record_property):
    from psycopg.types.json import Jsonb
    service,ledger,scope,job,stages,refs=await voice_job(db,legacy_media)
    before=await ledger.summary(scope)
    for ordinal,stage,wire in [(0,'upload','e'*64),(1,'register','b'*64)]:
        phases=[]
        with pytest.raises(psycopg.Error) as rejected:
            async with legacy_media.sql(service.job_permit(job['id'])) as c:
                await c.execute('INSERT INTO media_voice_steps(id,job_id,attempt_id,binding_hash,provider_scope_hash,operation_key,ordinal,protocol,stage,wire_hash,request_id,refs) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                    ('raw-'+str(ordinal),job['id'],job['attempt_id'],job['binding_hash'],'c'*64,'raw-'+str(ordinal),ordinal,PROTOCOL,stage,wire,'req_raw_'+str(ordinal),Jsonb(refs)))
                phases.append('execute returned')
        assert phases==['execute returned']  # Original017 is deferred until commit.
        record_property('insert_rejection_phase_'+str(ordinal),'connection commit after execute returned')
        error=rejected.value
        record_property('insert_rejection_'+str(ordinal),error.diag.message_primary+'; '+error.diag.context)
        assert error.sqlstate=='23514'
        assert 'media maintenance permit required' not in str(error)
        assert error.diag.message_primary==('invalid initial voice stage' if ordinal==0 else 'voice stage ordinal gap')
        assert 'check_media_voice_history()' in error.diag.context
    async with db.connection() as c:
        assert (await(await c.execute('SELECT count(*) n FROM media_voice_steps')).fetchone())['n']==0
        assert (await(await c.execute('SELECT count(*) n FROM media_voice_step_observations')).fetchone())['n']==0
    assert await ledger.summary(scope)==before
    assert before['reserved']=='2' and before['spent']=='0'


@pytest.mark.asyncio
async def test_ready_owned_voice_and_private_binding_proof_commit_before_single_settlement(db,legacy_media):
    from platform_service.media.voices import OwnedVoices
    service,ledger,scope,job,stages,refs=await voice_job(db,legacy_media)
    voices=legacy_media.voices(service)
    upload=await start(stages,job,refs);await uploaded(stages,upload)
    registration=await start(stages,job,{'file_task_id':'task_'+'a'*32,'target_model':TARGET},key='register',stage='register',wire_hash='e'*64,previous=upload['id'])
    with pytest.raises(PlatformError):await voices.register_ready(job['id'],job['attempt_id'],name='我的声音')
    task='task_'+'b'*32
    await observe_complete(stages,registration['id'],key='registered',state='completed',http_status=200,provider_task_id=task,response_hash='f'*64,facts={'operation':'clone','voice_task_id':task})
    voice=await voices.register_ready(job['id'],job['attempt_id'],name='我的声音')
    assert (await voices.register_ready(job['id'],job['attempt_id'],name='我的声音')).id==voice.id
    resolved=await voices.resolve(scope,voice.id,protocol=PROTOCOL,target_model=TARGET,binding_hash='a'*64,provider_scope_hash='c'*64)
    assert resolved.provider_reference==task and task not in repr(resolved)
    public=await voices.public(scope,voice.id)
    assert public['id']==voice.id and public['name']=='我的声音' and task not in str(public)
    for patch in [{'binding_hash':'f'*64},{'provider_scope_hash':'e'*64},{'target_model':'other'},{'protocol':'newapi_dashscope_voice_v1'}]:
        with pytest.raises(PlatformError):
            await voices.resolve(scope,voice.id,**{'protocol':PROTOCOL,'target_model':TARGET,'binding_hash':'a'*64,'provider_scope_hash':'c'*64,**patch})
    with pytest.raises(PlatformError):await voices.public(replace(scope,product_id='second'),voice.id)
    await service.record_provider_association(job['id'],job['attempt_id'],http_status=200,http_request_id='req_final_registration')
    await service.record_provider_association(job['id'],job['attempt_id'],provider_response_id=task)
    await service.finish(job['id'],job['attempt_id'],terminal_status='completed',provider_response_id=task,quantities={'registration':1},basis_evidence={'verified_delivery_v1':'e'*64},evidence_hash='f'*64)
    assert (await ledger.summary(scope))['spent']=='2' and (await ledger.summary(scope))['reserved']=='0'
    async with db.connection() as c:
        assert (await (await c.execute('SELECT count(*) n FROM media_voice_registration_proofs')).fetchone())['n']==1


@pytest.mark.asyncio
async def test_unknown_registration_cannot_create_owned_voice_or_claim_zero_cost(db,legacy_media):
    from platform_service.media.voices import OwnedVoices
    service,ledger,scope,job,stages,refs=await voice_job(db,legacy_media)
    upload=await start(stages,job,refs);await uploaded(stages,upload)
    registration=await start(stages,job,{'file_task_id':'task_'+'a'*32,'target_model':TARGET},key='register',stage='register',wire_hash='e'*64,previous=upload['id'])
    await stages.observe(registration['id'],key='uncertain',state='unknown')
    voices=legacy_media.voices(service)
    with pytest.raises(PlatformError):await voices.register_ready(job['id'],job['attempt_id'],name='未完成')
    summary=await ledger.summary(scope)
    assert summary['reserved']=='2' and summary['spent']=='0' and summary['pending_reconciliation']==1
    async with db.connection() as c:assert (await (await c.execute('SELECT count(*) n FROM media_voices')).fetchone())['n']==0


@pytest.mark.asyncio
async def test_phase_headers_are_durable_before_body_without_premature_pending(db,legacy_media):
    service,ledger,scope,job,stages,refs=await voice_job(db,legacy_media)
    step=await start(stages,job,refs)
    await stages.record_headers(step['id'],http_status=200,http_request_id='req_real_stage')
    assert (await ledger.summary(scope))['pending_reconciliation']==0
    assert (await stages.snapshot(scope,job['id']))['state']=='unknown'
    async with db.connection() as c:
        row=await (await c.execute('SELECT * FROM media_voice_step_observations WHERE step_id=%s',(step['id'],))).fetchone()
        assert row['state']=='headers' and row['http_request_id']=='req_real_stage' and row['response_hash'] is None
    with pytest.raises(PlatformError):
        await stages.observe(step['id'],key='false-header',state='completed',http_status=200,http_request_id='req_other',
            provider_task_id='task_'+'a'*32,response_hash='d'*64,facts={'operation':'upload','file_task_id':'task_'+'a'*32})


@pytest.mark.asyncio
async def test_nonowner_voice_runtime_has_only_append_authority_and_detects_extra_grants(db,test_dsn,legacy_media):
    from uuid import uuid4
    import secrets
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from platform_service.db import Database
    from platform_service.identity import IdentityService
    from platform_service.media.core import MediaService
    from platform_service.media.voices import VoiceStages
    from platform_service.media.voice_permissions import grant_voice_permissions,assert_voice_runtime_permissions
    from platform_service.permissions import grant_runtime_permissions
    service,ledger,scope,job,stages,refs=await voice_job(db,legacy_media)
    role='m3_voice_'+uuid4().hex;password=secrets.token_urlsafe(24)
    await legacy_media.create_runtime_role(role,password)
    async with db.connection() as c:
        await grant_voice_permissions(c,role)
    runtime=Database(make_conninfo(test_dsn,user=role,password=password),schema=db.schema)
    try:
        await runtime.open();await runtime.require_runtime_role()
        async with runtime.connection() as c:await assert_voice_runtime_permissions(c)
        identity=IdentityService(runtime,None);low=legacy_media.stages(legacy_media.client(MediaService(runtime,identity)))
        step=await start(low,job,refs);await uploaded(low,step)
        for command in ["UPDATE media_voice_steps SET wire_hash=repeat('0',64)",'DELETE FROM media_voice_step_observations','TRUNCATE media_voice_registration_proofs']:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                async with runtime.connection() as c:await c.execute(command)
        async with db.connection() as c:
            await c.execute(sql.SQL('GRANT UPDATE(provider_reference) ON media_voice_registration_proofs TO {}').format(sql.Identifier(role)))
            with pytest.raises(PlatformError):await assert_voice_runtime_permissions(c,role)
    finally:
        await runtime.close()
        await legacy_media.drop_runtime_role(role)


@pytest.mark.asyncio
async def test_dashscope_verify_runner_has_eight_post_budget_and_one_hold(db,legacy_media):
    import httpx
    from types import SimpleNamespace
    from platform_service.media.voice_execution import VoiceExecution
    from platform_service.media.adapters.newapi_voice import DashScopeVoiceAdapter,BridgeVoiceProfile
    protocol='newapi_dashscope_voice_v1'
    service,ledger,scope,job,stages,refs=await voice_job(db,legacy_media,protocol)
    create=await start(stages,job,refs,key='create',stage='create',protocol=protocol)
    await observe_complete(stages,create['id'],key='created',state='completed',http_status=200,provider_task_id='task_'+'a'*32,response_hash='d'*64,
        facts={'operation':'create_voice','voice_task_id':'task_'+'a'*32,'target_model':'cosyvoice-v2','voice_status':'UNVERIFIED'})
    calls=[]
    async def handler(req):
        calls.append(req)
        async with db.connection()as c:
            count=(await(await c.execute('SELECT count(*) n FROM media_voice_steps')).fetchone())['n']
            assert count==len(calls)+1
        return httpx.Response(200,json={'output':{'task_id':'task_'+str(len(calls))*32,'task_status':'SUCCEEDED','voice_task_id':'task_'+str(len(calls))*32,
            'target_model':'cosyvoice-v2','voice_status':'DEPLOYING'},'usage':{'count':1}})
    provider=SimpleNamespace(adapter=protocol,base_url='http://127.0.0.1:9999',secret='synthetic-only',version='1',models={'voice-enrollment':'cosyvoice-v2'},capabilities=('voice.enroll',))
    adapter=DashScopeVoiceAdapter(provider,'test',BridgeVoiceProfile(target_models=('cosyvoice-v2',)),binding_hash='a'*64,provider_scope_hash='c'*64)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler))as client:
        runner=VoiceExecution(SimpleNamespace(service=service,_client=client))
        assert hasattr(runner,'verify'), 'bounded DashScope verify stage runner is not connected'
        with pytest.raises(PlatformError)as error:await runner.verify(job,adapter,permit=service.job_permit(job['id']))
        assert error.value.code=='voice_stage_limit'
    assert len(calls)==8
    assert(await ledger.summary(scope))['reserved']=='2'and(await ledger.summary(scope))['spent']=='0'
    async with db.connection()as c:
        assert(await(await c.execute('SELECT count(*) n FROM credit_reservations WHERE operation=\'media\'')).fetchone())['n']==1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode",["unknown","rejected"])
async def test_dashscope_verify_stops_after_unknown_or_rejected_post_without_retry(db,mode,legacy_media):
    import httpx
    from types import SimpleNamespace
    from platform_service.media.voice_execution import VoiceExecution
    from platform_service.media.adapters.newapi_voice import DashScopeVoiceAdapter,BridgeVoiceProfile
    protocol='newapi_dashscope_voice_v1'
    service,ledger,scope,job,stages,refs=await voice_job(db,legacy_media,protocol)
    create=await start(stages,job,refs,key='create',stage='create',protocol=protocol)
    await observe_complete(stages,create['id'],key='created',state='completed',http_status=200,provider_task_id='task_'+'a'*32,response_hash='d'*64,
        facts={'operation':'create_voice','voice_task_id':'task_'+'a'*32,'target_model':'cosyvoice-v2','voice_status':'UNVERIFIED'})
    calls=[]
    async def handler(req):
        calls.append(req)
        async with db.connection()as c:
            count=(await(await c.execute('SELECT count(*) n FROM media_voice_steps')).fetchone())['n']
            assert count==len(calls)+1
        if mode=='unknown':raise httpx.ReadTimeout('synthetic uncertain',request=req)
        return httpx.Response(200,json={'output':{'task_id':'task_'+str(len(calls))*32,'task_status':'SUCCEEDED','voice_task_id':'task_'+str(len(calls))*32,
            'target_model':'cosyvoice-v2','voice_status':'UNDEPLOYED'},'usage':{'count':1}})
    provider=SimpleNamespace(adapter=protocol,base_url='http://127.0.0.1:9999',secret='synthetic-only',version='1',models={'voice-enrollment':'cosyvoice-v2'},capabilities=('voice.enroll',))
    adapter=DashScopeVoiceAdapter(provider,'test',BridgeVoiceProfile(target_models=('cosyvoice-v2',)),binding_hash='a'*64,provider_scope_hash='c'*64)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler))as client:
        runner=VoiceExecution(SimpleNamespace(service=service,_client=client))
        assert hasattr(runner,'verify'), 'bounded DashScope verify stage runner is not connected'
        with pytest.raises(PlatformError)as error:await runner.verify(job,adapter,permit=service.job_permit(job['id']))
        with pytest.raises(PlatformError):await runner.verify(job,adapter,permit=service.job_permit(job['id']))
    assert len(calls)==1
    async with db.connection()as c:
        latest=await(await c.execute('SELECT state FROM media_voice_step_observations ORDER BY created_at DESC LIMIT 1')).fetchone()
        assert latest['state']==mode
    assert(await ledger.summary(scope))['reserved']=='2'and(await ledger.summary(scope))['spent']=='0'
    async with db.connection()as c:
        assert(await(await c.execute('SELECT count(*) n FROM credit_reservations WHERE operation=\'media\'')).fetchone())['n']==1
