import hashlib
import io
import os
from pathlib import Path

import pytest
from b2_legacy_media_fixtures import legacy_media, legacy_db as db
from PIL import Image
from platform_service.errors import PlatformError
from platform_service.media.artifacts import ArtifactStore


class EntryObservedStore(ArtifactStore):
    """Record entry, then forward to the actual guarded implementation."""
    entry_calls = 0

    def put_image(self, raw, **options):
        from platform_service.media_maintenance.permits import WriteFence
        from platform_service.media_maintenance.runtime import current_permit
        self.entry_calls += 1
        self.entry_permit = current_permit()
        self.entry_fence = options['fence']
        assert type(self.entry_fence) is WriteFence
        return super().put_image(raw, **options)


async def assert_rejected_image_intent(db,legacy_media,service,store,context,record_property):
    permit=store.entry_permit
    assert permit is service._upload_permits[store.entry_fence.storage_key]
    assert permit.binding.kind=='upload' and permit.binding.subject_id==context['invocation_id']
    state=(await legacy_media.generation.observe(permit))['state']
    assert state=='unknown'
    record_property('original_upload_permit_state',state)
    async with db.connection() as c:
        blob=await(await c.execute('SELECT state,attached_id,kind,invocation_id FROM media_blobs WHERE storage_key=%s',(store.entry_fence.storage_key,))).fetchone()
        assert blob=={'state':'staged','attached_id':None,'kind':'asset','invocation_id':context['invocation_id']}
        assert (await(await c.execute('SELECT count(*) n FROM media_assets')).fetchone())['n']==0
        assert (await(await c.execute('SELECT count(*) n FROM media_artifacts')).fetchone())['n']==0
    assert not list(legacy_media.source_path.iterdir())


def image_bytes(fmt="PNG", size=(3, 2)):
    output = io.BytesIO()
    Image.new("RGB", size, (13, 27, 49)).save(output, format=fmt)
    return output.getvalue()


@pytest.mark.asyncio
async def test_private_artifact_store_decodes_and_retains_exact_original_bytes(tmp_path,db,legacy_media):
    from platform_service.media.artifacts import ArtifactStore
    service,ledger,scope,ctx,*_=await legacy_media.setup(db)
    store = ArtifactStore(legacy_media.source_path, max_bytes=1024 * 1024, max_pixels=100)
    raw = image_bytes()
    value = await legacy_media.put_image(service,ctx,store,raw)
    assert value["sha256"] == hashlib.sha256(raw).hexdigest() and value["size"] == len(raw)
    assert value["mime"] == "image/png" and value["properties"] == {"width": 3, "height": 2, "format": "PNG"}
    assert store.read(value) == raw
    assert (legacy_media.source_path).stat().st_mode & 0o777 == 0o700
    assert (legacy_media.source_path / value["storage_key"]).stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("raw", [b"<html>not an image</html>", b"\x89PNG\r\n\x1a\n", b"{}"])
@pytest.mark.asyncio
async def test_actual_decode_rejects_fake_content_without_leaving_an_artifact(tmp_path, raw,db,legacy_media,record_property):
    service,ledger,scope,ctx,*_=await legacy_media.setup(db)
    before=await ledger.summary(scope)
    store = EntryObservedStore(legacy_media.source_path)
    with pytest.raises(PlatformError) as rejected:
        await legacy_media.put_image_for_rejection(service,ctx,store,raw,reference_raw=image_bytes())
    assert rejected.value.code=='invalid_media_artifact'
    record_property('actual_store_entry_calls',store.entry_calls)
    assert store.entry_calls == 1
    assert not (legacy_media.source_path).exists() or not list((legacy_media.source_path).iterdir())
    await assert_rejected_image_intent(db,legacy_media,service,store,ctx,record_property)
    assert await ledger.summary(scope)==before


@pytest.mark.asyncio
@pytest.mark.parametrize('case',['pixels','bytes','mime'])
async def test_pixel_and_byte_limits_apply_before_full_decode_or_write(tmp_path,db,legacy_media,case,record_property):
    service,ledger,scope,ctx,*_=await legacy_media.setup(db)
    before=await ledger.summary(scope)
    limits={'max_pixels':2} if case=='pixels' else {'max_bytes':8} if case=='bytes' else {}
    store=EntryObservedStore(legacy_media.source_path,**limits)
    raw=image_bytes('JPEG') if case=='mime' else image_bytes()
    options={'mime':'image/png'} if case=='mime' else {}
    with pytest.raises(PlatformError) as rejected:
        await legacy_media.put_image_for_rejection(service,ctx,store,raw,reference_raw=raw,**options)
    assert rejected.value.code=='invalid_media_artifact'
    record_property('actual_store_entry_calls',store.entry_calls)
    assert store.entry_calls == 1
    assert not list(legacy_media.source_path.iterdir())
    await assert_rejected_image_intent(db,legacy_media,service,store,ctx,record_property)
    assert await ledger.summary(scope)==before


@pytest.mark.asyncio
async def test_store_rejects_symlinks_changed_bytes_and_untrusted_storage_keys(tmp_path,db,legacy_media):
    from platform_service.media.artifacts import ArtifactStore
    service,ledger,scope,ctx,*_=await legacy_media.setup(db)
    root = legacy_media.source_path
    store = ArtifactStore(root)
    value = await legacy_media.put_image(service,ctx,store,image_bytes())
    path = root / value["storage_key"]
    path.write_bytes(b"different")
    with pytest.raises(PlatformError): store.read(value)
    path.unlink(); outside = tmp_path / "outside"; outside.write_bytes(image_bytes()); path.symlink_to(outside)
    with pytest.raises(PlatformError): store.read(value)
    with pytest.raises(PlatformError): store.read({**value, "storage_key": "../outside"})
    linked = tmp_path / "linked"; linked.symlink_to(root, target_is_directory=True)
    from platform_service.media_maintenance.contracts import MaintenanceError
    with pytest.raises(MaintenanceError,match="invalid_backup"): await legacy_media.put_image(service,ctx,ArtifactStore(linked),image_bytes())



@pytest.mark.asyncio
async def test_gc_can_remove_only_a_complete_verified_interrupted_part(tmp_path,db,legacy_media):
    from platform_service.media.artifacts import ArtifactStore
    service,ledger,scope,ctx,*_=await legacy_media.setup(db)
    async with legacy_media.policy()as c:await c.execute('UPDATE media_storage_policy SET ttl_seconds=1 WHERE id=1')
    class ObservingStore(ArtifactStore):
        def delete_exact(self,metadata,*,fence=None):
            # Repeat the exact owned unlink before ACK while its actual row lock,
            # physical ref and freshly validated opaque fence remain held.
            first=super().delete_exact(metadata,fence=fence)
            second=super().delete_exact(metadata,fence=fence)
            self.removals=(first,second)
            return first
    store=ObservingStore(legacy_media.source_path)
    metadata=await legacy_media.put_image(service,ctx,store,image_bytes())
    path=store.root/metadata['storage_key'];partial=path.with_name(path.name+'.part')
    path.rename(partial)
    claim,permit=await legacy_media.gc_claim(service,metadata)
    result=await legacy_media.delete_owned(service,store,claim,permit)
    assert result['state']=='deleted'and store.removals==(True,False)
    assert not partial.exists()
    assert (await service.raw.finish_gc(metadata['storage_key'],claim['gc_token'],permit=permit))['state']=='deleted'


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['incomplete','symlink','complete_plus_incomplete'])
async def test_gc_unverifiable_part_never_reports_deleted_or_removes_other_data(tmp_path,kind,db,legacy_media):
    from platform_service.media.artifacts import ArtifactStore
    service,ledger,scope,ctx,*_=await legacy_media.setup(db)
    async with legacy_media.policy()as c:await c.execute('UPDATE media_storage_policy SET ttl_seconds=1 WHERE id=1')
    store=ArtifactStore(legacy_media.source_path)
    metadata=await legacy_media.put_image(service,ctx,store,image_bytes())
    path=store.root/metadata['storage_key'];partial=path.with_name(path.name+'.part')
    outside=tmp_path/'outside';outside.write_bytes(image_bytes())
    if kind!='complete_plus_incomplete':path.unlink()
    if kind=='symlink':partial.symlink_to(outside)
    else:partial.write_bytes(image_bytes()[:10]);partial.chmod(0o600)
    claim,permit=await legacy_media.gc_claim(service,metadata)
    with pytest.raises(PlatformError,match='media_gc_blob_unverifiable'):
        await legacy_media.delete_owned(service,store,claim,permit)
    assert partial.exists()and outside.read_bytes()==image_bytes()
    if kind=='complete_plus_incomplete':assert path.read_bytes()==image_bytes()
    async with db.connection()as c:
        row=await(await c.execute('SELECT state,deleted_at FROM media_blobs WHERE storage_key=%s',(metadata['storage_key'],))).fetchone()
    assert row['state']=='gc_pending'and row['deleted_at']is None
