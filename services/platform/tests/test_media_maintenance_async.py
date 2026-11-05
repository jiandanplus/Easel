"""DRAINING keeps the admitted exact async attempt; it never admits another."""
from uuid import uuid4
import pytest
from b2_owned_media import owned_media,admitted
from platform_service.media_maintenance.runtime import operation
from platform_service.media_maintenance.control import DatabaseControl
from platform_service.media_maintenance.contracts import MaintenanceError


@pytest.mark.asyncio
async def test_draining_worker_claim_continues_only_the_original_in_memory_capability(tmp_path,monkeypatch):
    import os,test_media_credits as fixture
    from test_media_contracts import definition
    from platform_service.media.async_store import MediaAsyncStore
    value=definition('video.generate');value['adapter']='newapi_videos_v1'
    value['rate']['meters']=[{'unit':'video_ms','basis':'verified_delivery_v1','numerator':'1','denominator':'1000'}]
    value['limits']['max_units']={'video_ms':5000};value['limits']['max_assets']=0
    value['limits']['output_media']={'mime_types':['video/mp4'],'max_files':1,'max_duration_ms':1000,'max_pixels':1024,'max_channels':2,'max_sample_rate':48000,'require_audio':False}
    value['parameters']={'type':'object','properties':{},'required':[],'additionalProperties':False}
    bindings={'fixture-image':{'provider_id':'fixture','provider_version':'1','adapter':'newapi_videos_v1','protocol_version':'1','binding_hash':'a'*64}}
    monkeypatch.setattr(fixture,'BINDINGS',bindings)
    async with owned_media(tmp_path,definition=value) as env:
        request={'model':'fixture-image','capability':'video.generate','prompt':'synthetic video','assets':[],'parameters':{}}
        job,permit=await admitted(env,request=request,bindings=bindings);queue=MediaAsyncStore(env['service'])
        with operation(permit):
            await env['service'].record_provider_attempt(job['id'],job['attempt_id'],provider_request_hash='b'*64,provider_scope_hash='c'*64,upstream_model='explicit-fixture',request_id='request-'+job['id'])
            await env['service'].dispatch(job['id'],job['attempt_id'])
            await env['service'].record_provider_association(job['id'],job['attempt_id'],http_status=200,http_request_id='http-'+job['id'])
            await env['service'].record_provider_association(job['id'],job['attempt_id'],provider_job_id='synthetic-provider-job')
            await queue.enqueue(job['id'],job['attempt_id'])
        controller=DatabaseControl(env['env'].factory('controller'),locks=env['locks'])
        original=dict(host_id=env['generation'].identity.host_id,boot_id=env['generation'].identity.boot_id,process_start=env['generation'].identity.process_start,control_dev=env['locks'].inode[0],control_ino=env['locks'].inode[1],source_dev=0,source_ino=0,generation_digest=await controller.generation_digest(),configuration_state={})
        await controller.begin_window(original)
        with pytest.raises(MaintenanceError):await env['generation'].acquire('maintenance','new-scan')
        with operation(permit):
            claims=await queue.claim(limit=1)
            assert len(claims)==1 and claims[0]['job_id']==job['id'] and claims[0]['attempt_id']==job['attempt_id']
            await queue.heartbeat(claims[0])
        with pytest.raises(MaintenanceError):await env['generation'].acquire('job',job['id'],str(uuid4()))
