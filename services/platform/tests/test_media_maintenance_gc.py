"""Actual exact-claim GC row ownership before physical unlink on owned PG."""
import asyncio,hashlib,os
from contextlib import asynccontextmanager
from uuid import uuid4
import pytest
from b2_owned_media import owned_media
from platform_service.media_maintenance.runtime import operation,physical_call,GuardedDatabase
from platform_service.media_maintenance.contracts import MaintenanceError


async def expired_asset(value,tmp_path,*,retain=False):
    from platform_service.media.artifacts import ArtifactStore,inspect_image
    from test_media_artifacts import image_bytes
    raw=image_bytes();info=inspect_image(raw);key=uuid4().hex+uuid4().hex;metadata={'storage_key':key,**info}
    store=ArtifactStore(tmp_path/'media')
    async with value['generation'].use('policy','global',transient=True):
        async with value['service'].db.connection() as c:await c.execute('UPDATE media_storage_policy SET ttl_seconds=1 WHERE id=1')
    permit=await value['generation'].acquire('upload',value['context']['invocation_id'])
    with operation(permit):
        await value['service'].stage_blob(value['context'],metadata)
        await physical_call(store._put_verified,raw,info,storage_key=key)
        await value['service'].register_asset(value['context'],**metadata)
    if not retain:await value['generation'].finish(permit,{'attached':key})
    await asyncio.sleep(1.1)
    return (store,metadata,permit) if retain else (store,metadata)


async def claimed(value):
    async with value['generation'].use('maintenance','gc-scan-'+str(uuid4()),transient=True):
        await value['service'].prepare_gc()
        rows=await value['service'].claim_gc()
    assert len(rows)==1
    claim=rows[0];permit=value['generation'].owned('gc',claim['storage_key'],claim['gc_token'])
    return claim,permit


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',[None,'lease','token','metadata'])
async def test_gc_checks_exact_claim_before_unlink_and_only_fence_can_delete(tmp_path,failure):
    async with owned_media(tmp_path) as value:
        store,metadata=await expired_asset(value,tmp_path)
        assert hasattr(value['service'],'gc_delete_owned'),'transaction-owned GC before-unlink operation missing'
        claim,permit=await claimed(value)
        with operation(permit):
            with pytest.raises(MaintenanceError):store.delete_exact(claim)
            if failure=='lease':
                async with value['service'].db.connection() as c:
                    await c.execute("UPDATE media_blobs SET gc_lease_until=clock_timestamp()-interval '1 second' WHERE storage_key=%s",(claim['storage_key'],))
            supplied={**claim}
            if failure=='token':supplied['gc_token']=str(uuid4())
            if failure=='metadata':supplied['sha256']='0'*64
            if failure is not None:
                with pytest.raises(MaintenanceError):await value['service'].gc_delete_owned(store,supplied,permit=permit)
                assert (store.root/metadata['storage_key']).is_file()
            else:
                result=await value['service'].gc_delete_owned(store,claim,permit=permit)
                assert result['state']=='deleted' and result['gc_token']==claim['gc_token']
                assert not (store.root/metadata['storage_key']).exists()
        if failure is None:
            await value['generation'].finish(permit,{'deleted':claim['storage_key']})
            assert not value['generation'].permits


@pytest.mark.asyncio
async def test_unknown_gc_capability_prevents_other_generation_reclaim(tmp_path):
    from platform_service.media_maintenance.runtime import RuntimeGeneration
    async with owned_media(tmp_path) as value:
        store,metadata=await expired_asset(value,tmp_path)
        assert hasattr(value['service'],'gc_delete_owned'),'transaction-owned GC operation missing'
        claim,permit=await claimed(value)
        await value['generation'].unknown(permit);permit.release_failed_physical()
        other=RuntimeGeneration(value['db'],value['locks'],source=value['source']);await other.start()
        try:
            with pytest.raises(MaintenanceError):await other.acquire('gc',claim['storage_key'],str(uuid4()))
            assert (store.root/metadata['storage_key']).is_file()
        finally:await other.close()


@pytest.mark.asyncio
async def test_unlink_commit_receipt_unknown_retains_unknown_permit_and_history(tmp_path):
    async with owned_media(tmp_path) as value:
        store,metadata=await expired_asset(value,tmp_path)
        assert hasattr(value['service'],'gc_delete_owned'),'transaction-owned GC operation missing'
        claim,permit=await claimed(value);original=value['service'].db
        class LoseReceipt:
            @asynccontextmanager
            async def connection(self):
                async with original.connection() as c:yield c
                raise TimeoutError('synthetic commit receipt loss')
        value['service'].db=LoseReceipt()
        with pytest.raises(TimeoutError):await value['service'].gc_delete_owned(store,claim,permit=permit)
        value['service'].db=original
        assert not (store.root/metadata['storage_key']).exists()
        async with value['owner'].connection() as c:
            row=await(await c.execute('SELECT state,gc_token FROM media_blobs WHERE storage_key=%s',(claim['storage_key'],))).fetchone()
            residual=await(await c.execute('SELECT status FROM easel_backup_control.permits WHERE subject_id=%s',(claim['storage_key'],))).fetchone()
            assert row=={'state':'deleted','gc_token':claim['gc_token']} and residual['status']=='unknown'



@pytest.mark.asyncio
async def test_gc_holds_row_and_real_physical_reference_through_unlink_and_drain(tmp_path):
    import threading,psycopg
    from platform_service.media_maintenance.runtime import physical_guard
    from platform_service.media_maintenance.control import DatabaseControl
    async with owned_media(tmp_path) as value:
        store,metadata=await expired_asset(value,tmp_path);claim,permit=await claimed(value)
        entered=threading.Event();release=threading.Event()
        class Block:
            root=store.root
            @physical_guard
            def delete_exact(self,row,*,fence):
                entered.set();release.wait(3);return store.delete_exact(row,fence=fence)
        task=asyncio.create_task(value['service'].gc_delete_owned(Block(),claim,permit=permit));drain=None;holder=None
        try:
            await asyncio.to_thread(entered.wait,1);assert entered.is_set() and permit.physical_count==1
            async with value['owner'].connection() as c:
                with pytest.raises(psycopg.errors.LockNotAvailable):await c.execute('SELECT * FROM media_blobs WHERE storage_key=%s FOR UPDATE NOWAIT',(claim['storage_key'],))
            with pytest.raises(MaintenanceError):value['locks'].acquire_exclusive()
            controller=DatabaseControl(value['env'].factory('controller'),locks=value['locks'])
            original=dict(host_id=value['generation'].identity.host_id,boot_id=value['generation'].identity.boot_id,process_start=value['generation'].identity.process_start,control_dev=value['locks'].inode[0],control_ino=value['locks'].inode[1],source_dev=value['source'].inode[0],source_ino=value['source'].inode[1],generation_digest=await controller.generation_digest(),configuration_state={})
            drain=asyncio.create_task(controller.begin_window(original));await asyncio.sleep(.03);assert not drain.done()
            release.set();assert(await asyncio.wait_for(task,2))['state']=='deleted'
            window=await asyncio.wait_for(drain,2)
            with pytest.raises(MaintenanceError):await controller.acquire_window_exclusive(window)
            await value['generation'].finish(permit,{'deleted':claim['storage_key']})
            holder=await controller.acquire_window_exclusive(window)
            assert not (store.root/metadata['storage_key']).exists()
        finally:
            release.set()
            if not task.done():
                try:await task
                except Exception:pass
            if drain is not None and not drain.done():drain.cancel();await asyncio.gather(drain,return_exceptions=True)
            if holder is not None:holder.close()


@pytest.mark.asyncio
async def test_observed_database_loss_prevents_next_unlink_and_keeps_unknown(tmp_path):
    import threading,psycopg
    from platform_service.media_maintenance.runtime import physical_guard
    from platform_service.media_maintenance.permits import assert_transaction_permit
    async with owned_media(tmp_path) as value:
        store,metadata=await expired_asset(value,tmp_path);claim,permit=await claimed(value)
        entered=threading.Event();release=threading.Event();original=value['service'].db
        class Record:
            connection_value=None
            @asynccontextmanager
            async def connection(self):
                async with original.connection() as c:
                    self.connection_value=c;yield c
        record=Record();value['service'].db=record
        class Block:
            root=store.root
            @physical_guard
            def delete_exact(self,row,*,fence):
                entered.set();release.wait(3);return store.delete_exact(row,fence=fence)
        task=asyncio.create_task(value['service'].gc_delete_owned(Block(),claim,permit=permit))
        try:
            await asyncio.to_thread(entered.wait,1);assert entered.is_set()
            async with value['db'].connection() as c:
                row=await(await c.execute('SELECT pg_terminate_backend(%s)',(record.connection_value.info.backend_pid,))).fetchone()
                assert row['pg_terminate_backend'] is True
            with pytest.raises(MaintenanceError):await assert_transaction_permit(record.connection_value,permit,permit.binding)
            with pytest.raises(MaintenanceError):value['locks'].acquire_exclusive()
            release.set()
            with pytest.raises((MaintenanceError,psycopg.Error)):await asyncio.wait_for(task,2)
            assert (store.root/metadata['storage_key']).is_file() and permit.physical_count==0
            async with value['owner'].connection() as c:
                row=await(await c.execute('SELECT status FROM easel_backup_control.permits WHERE subject_id=%s',(claim['storage_key'],))).fetchone()
                assert row['status']=='unknown'
        finally:
            release.set();value['service'].db=original
            if not task.done():
                try:await task
                except Exception:pass



@pytest.mark.asyncio
async def test_gc_cannot_prepare_or_unlink_blob_while_original_upload_still_owns_it(tmp_path):
    async with owned_media(tmp_path) as value:
        store,metadata,upload=await expired_asset(value,tmp_path,retain=True)
        async with value['generation'].use('maintenance','first-scan-'+str(uuid4()),transient=True):
            assert await value['service'].prepare_gc()==[]
            assert await value['service'].claim_gc()==[]
        assert(store.root/metadata['storage_key']).is_file() and upload._valid
        await value['generation'].finish(upload,{'attached':metadata['storage_key']})
        claim,permit=await claimed(value)
        await value['service'].gc_delete_owned(store,claim,permit=permit)
        await value['generation'].finish(permit,{'deleted':claim['storage_key']})
        assert not(store.root/metadata['storage_key']).exists()



@pytest.mark.asyncio
async def test_new_upload_without_intent_finishes_despite_old_same_invocation_gc_pending(tmp_path):
    async with owned_media(tmp_path) as value:
        store,metadata=await expired_asset(value,tmp_path);claim,gc=await claimed(value)
        fresh=await value['generation'].acquire('upload',value['context']['invocation_id'])
        await value['generation'].finish(fresh,{'rejected_before_stage':True})
        assert fresh._closed
        await value['service'].gc_delete_owned(store,claim,permit=gc)
        await value['generation'].finish(gc,{'deleted':claim['storage_key']})
        assert not(store.root/metadata['storage_key']).exists()
