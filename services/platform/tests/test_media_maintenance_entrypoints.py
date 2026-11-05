"""Real generation/transaction and physical ownership seams on isolated PG."""
import asyncio,importlib,os,sys,threading
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/"src"))
from b2_owned_pg import b2_pg
from test_media_maintenance_pg import installed,local_locks


def api():
    try:return importlib.import_module("platform_service.media_maintenance.runtime")
    except ModuleNotFoundError:pytest.fail("Trusted runtime operation registry missing")


@pytest.mark.asyncio
async def test_real_generation_capability_drains_exact_continuation_and_transaction(b2_pg,tmp_path):
    from platform_service.media_maintenance.contracts import MaintenanceError
    from platform_service.media_maintenance.control import DatabaseControl
    lib=api();await installed(b2_pg);fd,locks=local_locks(tmp_path)
    class DB:
        @asynccontextmanager
        async def connection(self):
            async with b2_pg.connection("runtime") as c:
                async with c.transaction():yield c
    from test_media_maintenance_domain import source as open_source
    source_path,source=open_source(tmp_path)
    generation=lib.RuntimeGeneration(DB(),locks,source=source);await generation.start()
    model="registry-"+uuid4().hex;permit=await generation.acquire("catalog",model)
    owner=DatabaseControl(b2_pg.factory("controller"),locks=locks)
    original=dict(host_id=generation.identity.host_id,boot_id=generation.identity.boot_id,process_start=generation.identity.process_start,control_dev=locks.inode[0],control_ino=locks.inode[1],source_dev=os.fstat(fd).st_dev,source_ino=os.fstat(fd).st_ino,generation_digest=await owner.generation_digest(),configuration_state={})
    try:
        await owner.begin_window(original)
        # Only possession of the already issued object permits continuation;
        # acquire() is a new transient request even for the same model.
        with pytest.raises(MaintenanceError):await generation.acquire("catalog",model)
        await lib.validate(permit)
        with pytest.raises(MaintenanceError):await generation.acquire("catalog","new-"+uuid4().hex)
        with lib.operation(permit):
            async with lib.GuardedDatabase(DB()).connection() as c:
                await c.execute("INSERT INTO media_models(id,definition,operator_id)VALUES(%s,'{}','synthetic')",(model,))
                await c.execute("INSERT INTO media_model_states(model_id,operator_id)VALUES(%s,'synthetic')",(model,))
        with pytest.raises(MaintenanceError):await generation.close()
        await generation.finish(permit,{"model":model});await generation.close()
        assert not generation.permits
    finally:
        for item in generation.permits:
            await generation.unknown(item)
            if not item.physical_count:item.release_failed_physical()
        source.close();locks.close();os.close(fd)


@pytest.mark.asyncio
async def test_physical_call_retains_actual_thread_after_parent_cancellation(b2_pg,tmp_path):
    from platform_service.media_maintenance.contracts import MaintenanceError
    lib=api();await installed(b2_pg);fd,locks=local_locks(tmp_path)
    class DB:
        @asynccontextmanager
        async def connection(self):
            async with b2_pg.connection("runtime") as c:
                async with c.transaction():yield c
    from test_media_maintenance_domain import source as open_source
    source_path,source=open_source(tmp_path)
    generation=lib.RuntimeGeneration(DB(),locks,source=source);await generation.start();permit=await generation.acquire("upload",str(uuid4()))
    entered=threading.Event();release=threading.Event();exited=threading.Event();path=source_path/"owned-file"
    @lib.physical_guard
    def write():
        try:entered.set();release.wait(3);path.write_bytes(b"synthetic")
        finally:exited.set()
    task=asyncio.create_task(lib.physical_call(write,permit=permit))
    try:
        await asyncio.to_thread(entered.wait,1);assert entered.is_set();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        with pytest.raises(MaintenanceError):locks.acquire_exclusive()
        release.set();await asyncio.to_thread(exited.wait,2)
        for _ in range(20):
            if permit.physical_count==0:break
            await asyncio.sleep(.01)
        assert permit.physical_count==0 and path.read_bytes()==b"synthetic"
        with pytest.raises(MaintenanceError):write()
    finally:
        release.set()
        if not permit._closed:
            await generation.unknown(permit)
            if not permit.physical_count:permit.release_failed_physical()
        source.close();locks.close();os.close(fd)



# Independently enumerated from the approved T4/T5 task inventory. This invokes
# every actual public/low-level seam, rather than counting registry declarations.
REQUIRED_SEAMS={
 "platform_service.media.core:MediaService":("publish","set_enabled","mint","admit","dispatch","heartbeat","record_provider_attempt","record_provider_association","record_protocol_facts","record_http_response_reference","record_terminal_evidence","pending","finish_from_evidence","cancel","recover_financial","register_asset","register_artifact","register_voice","_reserve_storage_budget","_release_storage_budget","_stage_storage","stage_blob","_attach_storage","prepare_gc","claim_gc","finish_gc"),
 "platform_service.media.async_store:MediaAsyncStore":("enqueue","recover_associations","claim","seed","heartbeat","owned","defer","observe","stage_source","attach_source","expire","prepare_settlement","done"),
 "platform_service.media.worker:MediaWorker":("_write_source","_promote_partial","run_once","execute","_execute_claim"),
 "platform_service.media.audio_execution:":("store_input","finish_audio"),
 "platform_service.media.voice_execution:VoiceExecution":("stage","enroll","verify","_bounded_verify","finish","_bounded_finish","recover","_recover_job","_bounded__recover_job"),
 "platform_service.media.voices:VoiceStages":("begin","observe","record_headers"),
 "platform_service.media.voices:OwnedVoices":("register_ready",),
 "platform_service.media.artifacts:ArtifactStore":("check_capacity","put_image","put_image_async","_put_verified","discard","delete_exact"),
 "platform_service.media.av_artifacts:":("inspect_av",),
}
SEAMS=[(owner,name)for owner,names in REQUIRED_SEAMS.items()for name in names]


@pytest.mark.asyncio
@pytest.mark.parametrize("owner,name",SEAMS)
async def test_all_required_low_level_seams_reject_absent_capability(owner,name):
    import inspect
    from platform_service.media_maintenance.contracts import MaintenanceError
    module,class_name=owner.split(":");target=importlib.import_module(module)
    if class_name:target=getattr(target,class_name)
    function=getattr(target,name);signature=inspect.signature(function);args=[];kwargs={}
    for parameter in signature.parameters.values():
        if parameter.kind in (parameter.VAR_POSITIONAL,parameter.VAR_KEYWORD):continue
        if parameter.default is not parameter.empty:continue
        if parameter.kind==parameter.KEYWORD_ONLY:kwargs[parameter.name]=None
        else:args.append(None)
    with pytest.raises(MaintenanceError):
        value=function(*args,**kwargs)
        if hasattr(value,"__aenter__"):
            async with value:pass
        elif inspect.isawaitable(value):await value
        elif hasattr(value,"__enter__"):
            with value:pass



@pytest.mark.asyncio
async def test_actual_core_uses_exact_reserved_capability_and_keeps_text_independent(tmp_path):
    from b2_owned_media import owned_media,admitted
    from platform_service.media_maintenance.runtime import operation
    from platform_service.media_maintenance.contracts import MaintenanceError
    from test_execution_core import admit as text_admit
    async with owned_media(tmp_path) as value:
        service=value['service'];job,permit=await admitted(value);before=await value['ledger'].summary(value['scope'])
        assert job['id']==permit.binding.subject_id and job['attempt_id']==permit.binding.attempt_id
        wrong=await value['generation'].acquire('job',str(uuid4()),str(uuid4()))
        with pytest.raises(MaintenanceError):await service.cancel(value['scope'],job['id'],permit=wrong)
        assert (await value['ledger'].summary(value['scope']))['reserved']==before['reserved']
        with operation(permit):await service.cancel(value['scope'],job['id'])
        assert await value['generation'].finish_job_if_stable(permit)
        await value['generation'].finish(wrong,{'no_job':True})
        text_job=await text_admit(value['text'],value['text_context'])
        assert text_job['status']=='reserved'
        assert (await value['ledger'].summary(value['scope']))['pending_reconciliation']==0



@pytest.mark.asyncio
async def test_actual_runtime_image_lifecycle_upload_submit_replay_and_generation_close(tmp_path):
    import base64,httpx
    from b2_owned_media import owned_media
    from platform_service.media_runtime import MediaRuntime
    from platform_service.media_config import MediaSettings,MediaProvider
    from test_media_artifacts import image_bytes
    async with owned_media(tmp_path) as value:
        requests=[]
        async def handler(request):
            requests.append(request)
            return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(image_bytes()).decode()}]},headers={'x-request-id':'synthetic-runtime-response'})
        settings=MediaSettings(mode='test',storage_root=tmp_path/'media',providers={'fixture':MediaProvider('1','http://127.0.0.1:19991/v1','synthetic-private',{'fixture-image':'synthetic-upstream'})})
        runtime=MediaRuntime(value['db'],value['service'].identity,settings,service=value['service'],maintenance=value['generation'],transport=httpx.MockTransport(handler))
        try:
            await runtime.start()
            token=await runtime.mint(value['scope'],{'model':'fixture-image','capability':'image.generate','invocation_key':'runtime-owned'})
            body={'model':'fixture-image','capability':'image.generate','prompt':'synthetic image','assets':[],'parameters':{'n':1}}
            result=await runtime.submit(token['task_token'],'runtime-operation',body)
            assert result['financial_state']=='settled' and len(requests)==1
            repeated=await runtime.submit(token['task_token'],'runtime-operation',body)
            assert repeated['id']==result['id'] and len(requests)==1
            assert result['artifacts'] and 'storage_key' not in result['artifacts'][0]
            assert not value['generation'].permits
        finally:await runtime.close()


@pytest.mark.asyncio
async def test_missing_control_schema_rejects_media_write_and_keeps_text_read_path(tmp_path):
    from platform_service.media_runtime import MediaRuntime
    from platform_service.media_config import MediaSettings,MediaProvider
    from platform_service.errors import PlatformError
    from b2_owned_media import owned_media
    async with owned_media(tmp_path) as value:
        async with value['owner'].connection() as c:await c.execute('DROP SCHEMA easel_backup_control CASCADE')
        settings=MediaSettings(mode='test',storage_root=tmp_path/'media',providers={'fixture':MediaProvider('1','http://127.0.0.1:19991/v1','synthetic-private',{'fixture-image':'synthetic-upstream'})})
        runtime=MediaRuntime(value['db'],value['service'].identity,settings,service=value['service'],control_lock=value['locks'],source_root=value['source'])
        try:
            await runtime.start()
            with pytest.raises(PlatformError) as error:await runtime.mint(value['scope'],{'model':'fixture-image','capability':'image.generate','invocation_key':'must-close'})
            assert error.value.status==503
            assert (await value['ledger'].summary(value['scope']))['reserved']=='0'
        finally:await runtime.close()



@pytest.mark.asyncio
async def test_same_key_concurrent_admission_replays_readonly_and_closes_unused_new_capability(tmp_path):
    from b2_owned_media import owned_media
    from test_media_credits import REQUEST,BINDINGS
    async with owned_media(tmp_path) as value:
        generation=value['generation'];service=value['service'];permits=[]
        async def call():
            async with generation.use('job',str(uuid4()),str(uuid4())) as permit:
                permits.append(permit)
                return await service.admit(value['context'],key='same-key',request=REQUEST,bindings=BINDINGS)
        jobs=await asyncio.gather(call(),call())
        assert len({j['id']for j in jobs})==1 and sum(j['replay']for j in jobs)==1
        active=[p for p in permits if not p._closed];assert len(active)==1 and active[0].binding.subject_id==jobs[0]['id']
        async with value['owner'].connection() as c:
            assert(await(await c.execute("SELECT count(*) n FROM credit_reservations WHERE operation='media'")).fetchone())['n']==1
        await service.cancel(value['scope'],jobs[0]['id'],permit=active[0]);await generation.finish(active[0],{'released':jobs[0]['id']})



@pytest.mark.asyncio
async def test_each_upload_request_has_distinct_admission_and_same_subject_is_not_new_drain_authority(tmp_path):
    from b2_owned_media import owned_media
    from platform_service.media_maintenance.runtime import validate
    from platform_service.media_maintenance.control import DatabaseControl
    from platform_service.media_maintenance.contracts import MaintenanceError
    async with owned_media(tmp_path) as value:
        generation=value['generation'];subject=value['context']['invocation_id']
        first=await generation.acquire('upload',subject);second=await generation.acquire('upload',subject)
        assert first is not second and first.token_digest!=second.token_digest
        await generation.finish(first,{'no_blob':True});await validate(second)
        owner=DatabaseControl(value['env'].factory('controller'),locks=value['locks']);worker=generation.identity
        original=dict(host_id=worker.host_id,boot_id=worker.boot_id,process_start=worker.process_start,control_dev=value['locks'].inode[0],control_ino=value['locks'].inode[1],source_dev=value['source'].inode[0],source_ino=value['source'].inode[1],generation_digest=await owner.generation_digest(),configuration_state={})
        await owner.begin_window(original)
        with pytest.raises(MaintenanceError):await generation.acquire('upload',subject)
        await validate(second)
        await generation.finish(second,{'no_blob':True})



@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['unstaged','wrong_job','wrong_metadata'])
async def test_valid_capability_cannot_write_unstaged_or_other_attempt_blob(tmp_path,failure):
    from b2_owned_media import owned_media,admitted
    from platform_service.media.artifacts import ArtifactStore,inspect_image
    from platform_service.media_maintenance.runtime import operation,physical_call
    from platform_service.media_maintenance.contracts import MaintenanceError
    from test_media_artifacts import image_bytes
    async with owned_media(tmp_path) as value:
        raw=image_bytes();info=inspect_image(raw);key=uuid4().hex+uuid4().hex;metadata={'storage_key':key,**info}
        store=ArtifactStore(tmp_path/'media')
        if failure=='wrong_job':
            job,original=await admitted(value,key='original');other,permit=await admitted(value,key='other')
            with operation(original):
                await value['service'].record_provider_attempt(job['id'],job['attempt_id'],provider_request_hash='b'*64,provider_scope_hash='c'*64,upstream_model='synthetic',request_id='request-'+job['id'])
                await value['service'].dispatch(job['id'],job['attempt_id'])
                await value['service'].stage_blob(value['context'],metadata,kind='artifact',job_id=job['id'],attempt_id=job['attempt_id'])
        else:
            permit=await value['generation'].acquire('upload',value['context']['invocation_id'])
            if failure=='wrong_metadata':
                with operation(permit):await value['service'].stage_blob(value['context'],metadata)
                info={**info,'sha256':'0'*64}
        with pytest.raises(MaintenanceError):await physical_call(store._put_verified,raw,info,storage_key=key,permit=permit)
        assert not (store.root/key).exists() and not (store.root/(key+'.part')).exists()



@pytest.mark.asyncio
async def test_same_invocation_fresh_nonce_cannot_replace_original_staged_blob_owner(tmp_path):
    from b2_owned_media import owned_media
    from platform_service.media.artifacts import ArtifactStore,inspect_image
    from platform_service.media_maintenance.runtime import operation,physical_call
    from platform_service.media_maintenance.contracts import MaintenanceError
    from test_media_artifacts import image_bytes
    async with owned_media(tmp_path) as value:
        raw=image_bytes();info=inspect_image(raw);key=uuid4().hex+uuid4().hex;metadata={'storage_key':key,**info}
        first=await value['generation'].acquire('upload',value['context']['invocation_id']);second=await value['generation'].acquire('upload',value['context']['invocation_id'])
        with operation(first):await value['service'].stage_blob(value['context'],metadata)
        with pytest.raises(MaintenanceError):await physical_call(ArtifactStore(tmp_path/'media')._put_verified,raw,info,storage_key=key,permit=second)
        assert not(tmp_path/'media'/key).exists()
        with operation(first):
            await physical_call(ArtifactStore(tmp_path/'media')._put_verified,raw,info,storage_key=key)
            await value['service'].register_asset(value['context'],**metadata)
        await value['generation'].finish(first,{'attached':key});await value['generation'].finish(second,{'no_intent':True})


@pytest.mark.asyncio
async def test_runtime_default_control_open_uses_trusted_effective_group(tmp_path,monkeypatch):
    from b2_owned_media import owned_media
    from platform_service.media_runtime import MediaRuntime
    from platform_service.media_config import MediaSettings,MediaProvider
    from platform_service.media_maintenance.filesystem import ControlLock
    async with owned_media(tmp_path) as value:
        seen=[]
        def actual_local_capability(path,*,owner=(0,0),**kwargs):
            seen.append((path,owner));value['locks'].verify();return value['locks']
        monkeypatch.setattr(ControlLock,'from_path',actual_local_capability)
        runtime=MediaRuntime(value['db'],value['service'].identity,MediaSettings(mode='test',storage_root=tmp_path/'media',providers={'fixture':MediaProvider('1','http://127.0.0.1:19991/v1','synthetic-private',{'fixture-image':'synthetic-upstream'})}),service=value['service'],source_root=value['source'])
        try:
            await runtime.start()
            assert seen==[('/var/lib/easel-central-control',(0,os.getegid()))]
            assert runtime.maintenance._started
            token=await runtime.mint(value['scope'],{'model':'fixture-image','capability':'image.generate','invocation_key':'actual-start-owned-control'})
            assert type(token['task_token'])is str and not runtime.maintenance.permits
            unused=await runtime.maintenance.acquire('job',str(uuid4()),str(uuid4()))
            with api().operation(unused):assert await runtime.maintenance.finish_job_if_stable(unused)
            assert not runtime.maintenance.permits
        finally:await runtime.close()
