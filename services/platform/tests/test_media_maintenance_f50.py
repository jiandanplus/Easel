"""Opt-in exact f50 installed guard delta with actual isolated 001-016 PG."""
import asyncio
import base64
from contextlib import asynccontextmanager
import io
import os
from pathlib import Path
import sys
from uuid import uuid4

import pytest

PREFIX=os.environ.get('EASEL_B2_F50_INSTALLED')
if PREFIX is None:
    pytest.skip('explicit installed f50 guard candidate required',allow_module_level=True)
PREFIX=Path(PREFIX).resolve()
from b2_owned_pg import _owned_pg
# The fixture imports no application code, but adds the source directory for
# ordinary tests. Remove it before importing this installed-only candidate.
sys.path[:]=[value for value in sys.path if not value.endswith('/services/platform/src')]
sys.path.insert(0,str(PREFIX))


def image():
    from PIL import Image
    output=io.BytesIO();Image.new('RGB',(3,2),(17,29,41)).save(output,format='PNG')
    return output.getvalue()


@asynccontextmanager
async def candidate(tmp_path):
    from psycopg.rows import dict_row
    from platform_service.media_maintenance.filesystem import ControlLock,SourceRoot
    from platform_service.media_maintenance.schema import SchemaProfile,install_control_schema
    from platform_service.media_runtime import MediaRuntime
    from platform_service.media_config import MediaSettings,MediaProvider
    from platform_service.media.core import MediaService
    from platform_service.identity import IdentityService
    import httpx
    owned=_owned_pg(16);env=next(owned);fd=None;locks=None;source=None;runtime=None
    class DB:
        def __init__(self,role):self.role=role
        @asynccontextmanager
        async def connection(self):
            async with env.connection(self.role)as c:
                c.row_factory=dict_row
                async with c.transaction():yield c
    try:
        async with env.connection('owner')as c:
            await install_control_schema(c,SchemaProfile('public_001_016','public',env.roles['runtime'],env.roles['controller'],tuple(env.migrations)));await c.commit()
        root=tmp_path/'control';root.mkdir(mode=0o700);(root/'media.lock').write_bytes(b'');(root/'media.lock').chmod(0o600)
        fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY);locks=ControlLock(fd,'media.lock',owner=(os.getuid(),os.getgid()))
        media=tmp_path/'media';media.mkdir(mode=0o700);anchor=os.open(tmp_path,os.O_RDONLY|os.O_DIRECTORY)
        try:source=SourceRoot.from_anchor(anchor,'media')
        finally:os.close(anchor)
        requests=[]
        async def handler(request):
            requests.append(request)
            return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(image()).decode()}]},headers={'x-request-id':'synthetic-f50-response'})
        settings=MediaSettings(mode='test',storage_root=media,providers={'fixture':MediaProvider('1','http://127.0.0.1:19991/v1','synthetic-private',{'fixture-image':'synthetic-upstream'})})
        # This exercises actual startup/OS collection/registration/attestation;
        # no RuntimeGeneration is injected and no boolean authorizes anything.
        runtime=MediaRuntime(DB('runtime'),IdentityService(DB('runtime'),None),settings,control_lock=locks,source_root=source,transport=httpx.MockTransport(handler))
        await runtime.start();assert runtime.maintenance._started
        from test_media_credits import setup
        seeded=await setup(DB('owner'),maintenance=runtime.maintenance)
        _,ledger,scope,context,text,text_context=seeded
        yield dict(env=env,db=DB('runtime'),owner=DB('owner'),runtime=runtime,service=runtime.service,generation=runtime.maintenance,locks=locks,source=source,scope=scope,context=context,text=text,text_context=text_context,requests=requests,ledger=ledger)
    finally:
        if runtime is not None:await runtime.close()
        if source is not None:source.close()
        if locks is not None:locks.close()
        if fd is not None:os.close(fd)
        try:next(owned)
        except StopIteration:pass


def test_installed_f50_bindings_and_no_m2_m3_expansion():
    from platform_service.media_runtime import MediaRuntime
    from platform_service.media_maintenance.coverage import require_capture_coverage
    assert callable(getattr(MediaRuntime,'start',None)),'f50 runtime has no actual maintenance startup'
    assert require_capture_coverage('public_001_016')['method_count']==53
    package=PREFIX/'platform_service'
    assert len(list((package/'migrations').glob('*.sql')))==16
    assert not(package/'media/worker.py').exists()
    assert not(package/'media/async_store.py').exists()
    assert not hasattr(__import__('platform_service.media_config',fromlist=['MediaSettings']).MediaSettings,'control_root')
    from platform_service.media.core import MediaService
    assert not hasattr(MediaService,'record_protocol_facts')
    for name,module in tuple(sys.modules.items()):
        if name=='platform_service' or name.startswith('platform_service.'):
            path=getattr(module,'__file__',None)
            if path is not None:assert Path(path).resolve().is_relative_to(PREFIX),name


@pytest.mark.asyncio
async def test_installed_f50_runtime_decode_settle_replay_and_text(tmp_path):
    from test_execution_core import admit as text_admit
    async with candidate(tmp_path)as value:
        runtime=value['runtime'];token=await runtime.mint(value['scope'],{'model':'fixture-image','capability':'image.generate','invocation_key':'f50-runtime'})
        body={'model':'fixture-image','capability':'image.generate','prompt':'synthetic','assets':[],'parameters':{'n':1}}
        first=await runtime.submit(token['task_token'],'f50-operation',body)
        assert first['financial_state']=='settled' and len(value['requests'])==1
        assert(await runtime.submit(token['task_token'],'f50-operation',body))['id']==first['id']
        assert len(value['requests'])==1 and not value['generation'].permits
        actual=await runtime.artifact(value['scope'],first['artifacts'][0]['id'],content=True)
        assert actual[0]==image()
        assert(await text_admit(value['text'],value['text_context']))['status']=='reserved'


@pytest.mark.asyncio
async def test_installed_f50_actual_sql_no_cap_and_default_missing_control_fail_closed(tmp_path):
    import psycopg
    from platform_service.media_runtime import MediaRuntime
    from platform_service.media_maintenance.contracts import MaintenanceError
    from platform_service.errors import PlatformError
    async with candidate(tmp_path)as value:
        async with value['db'].connection()as c:
            with pytest.raises(psycopg.errors.CheckViolation):
                await c.execute("INSERT INTO media_models(id,definition,operator_id)VALUES('missing-cap','{}','synthetic')")
        # Old MediaSettings has no control_root. Exercise the real old-class
        # fallback and actual default from_path factory; on this isolated Mac
        # /var/lib/easel-central-control is absent. No real config is read.
        probe=MediaRuntime(value['db'],value['service'].identity,value['runtime'].settings)
        try:
            await probe.start();assert probe.maintenance is None
            with pytest.raises((MaintenanceError,PlatformError)):
                await probe.mint(value['scope'],{'model':'fixture-image','capability':'image.generate','invocation_key':'must-deny'})
            assert(await value['ledger'].summary(value['scope']))['reserved']=='0'
        finally:await probe.close()


@pytest.mark.asyncio
async def test_installed_f50_original_nonce_gc_fence_and_unknown_no_reclaim(tmp_path):
    from platform_service.media.artifacts import inspect_image
    from platform_service.media_maintenance.runtime import operation,physical_call,RuntimeGeneration
    from platform_service.media_maintenance.contracts import MaintenanceError
    async with candidate(tmp_path)as value:
        generation=value['generation'];service=value['service'];store=value['runtime'].store
        async with generation.use('policy','global',transient=True):
            async with service.db.connection()as c:await c.execute('UPDATE media_storage_policy SET ttl_seconds=1 WHERE id=1')
        raw=image();info=inspect_image(raw);key=uuid4().hex+uuid4().hex;metadata={'storage_key':key,**info}
        original=await generation.acquire('upload',value['context']['invocation_id'])
        with operation(original):await service.stage_blob(value['context'],metadata)
        foreign=await generation.acquire('upload',value['context']['invocation_id'])
        with operation(foreign):
            with pytest.raises(MaintenanceError):await physical_call(store._put_verified,raw,info,storage_key=key)
        await generation.finish(foreign,{'no_intent':True})
        with operation(original):
            await physical_call(store._put_verified,raw,info,storage_key=key)
            await service.register_asset(value['context'],**metadata)
        await generation.finish(original,{'attached':key});await asyncio.sleep(1.1)
        async with generation.use('maintenance','scan-'+str(uuid4()),transient=True):
            await service.prepare_gc();claim=(await service.claim_gc())[0]
        permit=generation.owned('gc',key,claim['gc_token'])
        with operation(permit):
            with pytest.raises(MaintenanceError):store.delete_exact(claim)
        assert(await service.gc_delete_owned(store,claim,permit=permit))['state']=='deleted'
        await generation.finish(permit,{'deleted':key});assert not(store.root/key).exists()
        # Separate known claim proves residual unknown survives lease time.
        second_key=uuid4().hex+uuid4().hex;second={'storage_key':second_key,**info};p=await generation.acquire('upload',value['context']['invocation_id'])
        with operation(p):
            await service.stage_blob(value['context'],second);await physical_call(store._put_verified,raw,info,storage_key=second_key)
            await service.register_asset(value['context'],**second)
        await generation.finish(p,{'attached':second_key});await asyncio.sleep(1.1)
        async with generation.use('maintenance','scan-'+str(uuid4()),transient=True):
            await service.prepare_gc();claim=(await service.claim_gc())[0]
        unknown=generation.owned('gc',second_key,claim['gc_token']);await generation.unknown(unknown);unknown.release_failed_physical()
        other=RuntimeGeneration(value['db'],value['locks'],source=value['source']);await other.start()
        try:
            with pytest.raises(MaintenanceError):await other.acquire('gc',second_key,str(uuid4()))
            assert(store.root/second_key).exists()
        finally:await other.close()
