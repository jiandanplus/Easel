"""Actual voice POST-stage/header/ready provenance uses the original job permit."""
import hashlib
from types import SimpleNamespace
from uuid import uuid4
import pytest
from platform_service.media_maintenance.runtime import operation


async def voice_job(value):
    from test_media_contracts import definition
    from test_media_credits import OPERATOR
    service=value['service'];generation=value['generation'];target='easel-minimax-speech-2.8-hd'
    definition=definition('voice.enroll');definition['id']='voice-enrollment';definition['adapter']='newapi_minimax_voice_v1'
    definition['rate']={'version':'registration-v1','meters':[{'unit':'registration','basis':'verified_delivery_v1','numerator':'3','denominator':'2'}]}
    definition['limits']['max_units']={'registration':1}
    definition['parameters']={'type':'object','properties':{'target_model':{'type':'string','enum':[target]}},'required':['target_model'],'additionalProperties':False}
    bindings={'voice-enrollment':{'provider_id':'fixture','provider_version':'1','adapter':'newapi_minimax_voice_v1','protocol_version':'1','binding_hash':'a'*64}}
    async with generation.use('catalog',definition['id'],transient=True):
        await service.publish(definition,OPERATOR);await service.set_enabled(definition['id'],True,OPERATOR)
    async with generation.use('mint',str(uuid4()),transient=True):
        token=await service.mint(value['scope'],model=definition['id'],capability='voice.enroll',invocation_key='enroll',mode='test',bindings=bindings)
    context=await service.authenticate_task(token['task_token'])
    async with generation.use('upload',context['invocation_id'],transient=True):
        sample=await service.register_asset(context,storage_key='1'*64,sha256='2'*64,size=10,mime='audio/wav',properties={'format':'WAV','duration_ms':1000,'channels':1,'sample_rate':24000})
    permit=await generation.acquire('job',str(uuid4()),str(uuid4()));payload=b'synthetic-voice-upload'
    with operation(permit):
        job=await service.admit(context,key='enroll',request={'model':definition['id'],'capability':'voice.enroll','prompt':'','assets':[sample['id']],'parameters':{'target_model':target}},bindings=bindings,verified_inputs={sample['id']:{k:sample[k]for k in ('sha256','size','mime','properties')}})
        await service.record_provider_attempt(job['id'],job['attempt_id'],provider_request_hash=hashlib.sha256(payload).hexdigest(),provider_scope_hash='c'*64,upstream_model='synthetic-upload',request_id='logical-'+job['id'])
        await service.dispatch(job['id'],job['attempt_id'])
    refs={'asset_id':sample['id'],'asset_sha256':sample['sha256'],'target_model':target}
    return job,permit,refs,payload


@pytest.mark.asyncio
async def test_actual_stage_callbacks_and_ready_proof_share_same_job_capability(tmp_path):
    from b2_owned_media import owned_media
    from platform_service.media.voice_execution import VoiceExecution
    from platform_service.media.voices import OwnedVoices
    from platform_service.media_maintenance.contracts import MaintenanceError
    async with owned_media(tmp_path) as value:
        job,permit,refs,payload=await voice_job(value)
        runtime=SimpleNamespace(service=value['service'],_client=None,settings=SimpleNamespace(worker_wall_seconds=120));execution=VoiceExecution(runtime)
        seen=[]
        class Adapter:
            adapter_id='newapi_minimax_voice_v1'
            async def submit(self,client,prepared,**callbacks):
                seen.append(prepared.stage);task='task_'+('a' if prepared.stage=='upload' else 'b')*32
                await callbacks['on_headers'](http_status=200,http_request_id='synthetic-'+prepared.stage)
                await callbacks['on_task'](task,'d'*64)
                facts={'operation':'upload','file_task_id':task} if prepared.stage=='upload' else {'operation':'clone','voice_task_id':task}
                return SimpleNamespace(state='completed',reference=SimpleNamespace(task_id=task),evidence_hash='d'*64,facts=facts)
        first,result,_=await execution.stage(job,Adapter(),SimpleNamespace(stage='upload',payload=payload),refs,permit=permit)
        register_refs={'file_task_id':result.reference.task_id,'target_model':refs['target_model']}
        last,result,_=await execution.stage(job,Adapter(),SimpleNamespace(stage='register',payload=b'synthetic-register'),register_refs,first['id'],permit=permit)
        ready=await OwnedVoices(value['service'].db,value['service'].identity,value['service']).register_ready(job['id'],job['attempt_id'],permit=permit)
        assert ready.id and seen==['upload','register']
        wrong=await value['generation'].acquire('job',str(uuid4()),str(uuid4()))
        with pytest.raises(MaintenanceError):await execution.stages.record_headers(last['id'],http_status=200,permit=wrong)
        async with value['owner'].connection() as c:
            assert(await(await c.execute('SELECT count(*) n FROM media_voice_registration_proofs WHERE job_id=%s',(job['id'],))).fetchone())['n']==1
        await value['generation'].finish(wrong,{'unused':True})
