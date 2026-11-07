"""Direct SQL cannot bypass an actual verified capability."""
import importlib,sys
from pathlib import Path
import pytest,psycopg
sys.path.insert(0,str(Path(__file__).parents[1]/"src"))
from b2_owned_pg import b2_pg
@pytest.mark.asyncio
async def test_all_profile_media_tables_reject_direct_insert_and_truncate(b2_pg):
    try:lib=importlib.import_module("platform_service.media_maintenance.schema")
    except ModuleNotFoundError:pytest.fail("B2 low-level DML guards are missing")
    profile=lib.SchemaProfile("local_001_020","public",b2_pg.roles["runtime"],b2_pg.roles["controller"],tuple(b2_pg.migrations))
    async with b2_pg.connection("owner") as c:
        await c.execute("DROP SCHEMA IF EXISTS easel_backup_control CASCADE");await c.commit();await lib.install_control_schema(c,profile);await c.commit()
    for table in lib.profile_tables(profile.name):
        for operation in [psycopg.sql.SQL("INSERT INTO public.{} DEFAULT VALUES").format(psycopg.sql.Identifier(table)),psycopg.sql.SQL("TRUNCATE public.{} CASCADE").format(psycopg.sql.Identifier(table))]:
            async with b2_pg.connection("runtime") as c:
                await c.execute("SELECT set_config('easel_backup_control.paused','false',true)")
                with pytest.raises(psycopg.errors.CheckViolation,match="media maintenance permit required"):await c.execute(operation)

@pytest.mark.asyncio
async def test_media_wallet_transaction_needs_permit_through_commit_but_text_continues(tmp_path):
    from contextlib import asynccontextmanager
    from psycopg.rows import dict_row
    from b2_owned_media import owned_media,admitted
    from platform_service.media_maintenance.control import DatabaseControl
    from platform_service.media_maintenance.contracts import MaintenanceError
    from platform_service.media_maintenance.permits import assert_transaction_permit
    from platform_service.media.core import MediaService
    from platform_service.identity import IdentityService
    from test_execution_core import admit as text_admit
    async with owned_media(tmp_path) as value:
        env=value['env'];generation=value['generation'];runtime=generation.control
        first,permit1=await admitted(value,key='first-owned');second,permit2=await admitted(value,key='second-owned')
        class DB:
            def __init__(self,permit=None,clear=False):self.permit,self.clear=permit,clear
            @asynccontextmanager
            async def connection(self):
                async with env.connection('runtime') as c:
                    c.row_factory=dict_row
                    async with c.transaction():
                        if self.permit is not None:await assert_transaction_permit(c,self.permit,self.permit.binding)
                        yield c
                        if self.clear:await c.execute("SELECT set_config('easel_backup_control.permit_token','',true)")
        controller=DatabaseControl(env.factory('controller'),locks=value['locks'])
        worker=generation.identity
        await controller.begin_window(dict(host_id=worker.host_id,boot_id=worker.boot_id,process_start=worker.process_start,control_dev=value['locks'].inode[0],control_ino=value['locks'].inode[1],source_dev=value['source'].inode[0],source_ino=value['source'].inode[1],generation_digest=await controller.generation_digest(),configuration_state={}))
        scope=value['scope'];ledger=value['ledger']
        with pytest.raises(MaintenanceError):await runtime.finish_permit(permit1,'d'*64)
        before=await ledger.summary(scope)
        unguarded=MediaService(DB(),IdentityService(DB(),None))
        with pytest.raises(MaintenanceError):await unguarded.cancel(scope,first['id'])
        assert(await ledger.summary(scope))['reserved']==before['reserved']
        normal=DB(permit1);service=MediaService(normal,IdentityService(normal,None))
        await service.cancel(scope,first['id'],permit=permit1);await generation.finish(permit1,{'released':first['id']})
        assert int((await ledger.summary(scope))['reserved'])==int(before['reserved'])-first['amount']
        text_job=await text_admit(value['text'],value['text_context']);assert text_job['status']=='reserved'
        unchanged=await ledger.summary(scope)
        with pytest.raises(psycopg.errors.CheckViolation):
            async with DB().connection() as c:await c.execute('UPDATE credit_lots SET held=held-1,available=available+1 WHERE held>0')
        assert(await ledger.summary(scope))['reserved']==unchanged['reserved']
        bad=DB(permit2,clear=True);bad_service=MediaService(bad,IdentityService(bad,None));held=await ledger.summary(scope)
        with pytest.raises(psycopg.errors.CheckViolation,match='media maintenance permit required'):await bad_service.cancel(scope,second['id'],permit=permit2)
        assert(await ledger.summary(scope))['reserved']==held['reserved']
        good=DB(permit2);good_service=MediaService(good,IdentityService(good,None))
        await good_service.cancel(scope,second['id'],permit=permit2);await generation.finish(permit2,{'released':second['id']})


@pytest.mark.asyncio
async def test_voice_observation_uses_owned_step_job_and_attempt(tmp_path):
    import hashlib
    from b2_owned_media import owned_media
    from test_media_maintenance_voice import voice_job
    from platform_service.media.voices import VoiceStages
    async with owned_media(tmp_path) as value:
        job,permit,refs,payload=await voice_job(value)
        voice=VoiceStages(value['service'].db,value['service'].identity,value['service'])
        step=await voice.begin(job['id'],job['attempt_id'],key='upload',protocol='newapi_minimax_voice_v1',stage='upload',wire_hash=hashlib.sha256(payload).hexdigest(),refs=refs,permit=permit)
        await voice.record_headers(step['id'],http_status=200,http_request_id='synthetic-header',permit=permit)
        async with value['owner'].connection() as c:
            assert(await(await c.execute('SELECT count(*) n FROM media_voice_step_observations WHERE step_id=%s',(step['id'],))).fetchone())['n']==1



@pytest.mark.asyncio
async def test_job_permit_cannot_forge_usage_policy_block_without_overbudget_evidence(tmp_path):
    from b2_owned_media import owned_media,admitted
    from platform_service.media_maintenance.runtime import operation
    async with owned_media(tmp_path) as value:
        service=value['service'];job,permit=await admitted(value)
        with operation(permit):
            await service.record_provider_attempt(job['id'],job['attempt_id'],provider_request_hash='b'*64,provider_scope_hash='c'*64,upstream_model='synthetic',request_id='request-'+job['id'])
            await service.dispatch(job['id'],job['attempt_id'])
            await service.record_provider_association(job['id'],job['attempt_id'],http_status=200,http_request_id='http-'+job['id'])
            await service.record_provider_association(job['id'],job['attempt_id'],provider_response_id='response-'+job['id'])
            await service.register_artifact(job['id'],job['attempt_id'],storage_key='d'*64,sha256='e'*64,size=10,mime='image/png',properties={'width':1,'height':1,'format':'PNG'})
            await service.record_terminal_evidence(job['id'],job['attempt_id'],terminal_status='completed',provider_response_id='response-'+job['id'],quantities={'image':1},basis_evidence={'verified_delivery_v1':'d'*64},evidence_hash='e'*64)
            await service.pending(job['id'],job['attempt_id'],'usage_exceeds_reservation')
            with pytest.raises(psycopg.errors.CheckViolation):
                async with service.db.connection() as c:await c.execute("INSERT INTO media_blocked_bindings(binding_hash,reason)VALUES(%s,'usage_exceeds_reservation')",(job['binding_hash'],))
        async with value['owner'].connection() as c:
            assert(await(await c.execute('SELECT count(*) n FROM media_blocked_bindings')).fetchone())['n']==0
