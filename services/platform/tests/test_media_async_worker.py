"""GET-only leased recovery with real owned AV and PostgreSQL ledger evidence."""
import httpx
import pytest
from b2_legacy_media_fixtures import legacy_media, legacy_db as db, legacy_dsn as test_dsn
from platform_service.errors import PlatformError
from platform_service.media.artifacts import ArtifactStore
from platform_service.media.av_artifacts import AVTools
from test_media_async_store import setup_async, completed
from test_media_av_artifacts import files

@pytest.mark.asyncio
@pytest.mark.parametrize("media",["video","av"])
async def test_get_recovery_decodes_original_video_settles_once_after_logout(db,monkeypatch,tmp_path,files,media,legacy_media):
    from platform_service.media.worker import MediaWorker
    from platform_service.media_config import MediaProvider,MediaSettings
    store,service,ledger,scope,ctx,job=await setup_async(db,legacy_media,monkeypatch)
    raw,tools=files;requests=[]
    provider=MediaProvider(version='1',base_url='http://127.0.0.1:19991/v1',secret='fixture',models={'fixture-image':'explicit-fixture'},adapter='newapi_videos_v1',capabilities=('video.generate',))
    settings=MediaSettings(mode='test',providers={'fixture':provider},storage_root=legacy_media.source_path,av_tools=AVTools(*tools))
    async def binding(_job):return provider
    async def request(request):
        requests.append((request.method,request.url.path))
        assert request.method=='GET'
        if request.url.path.endswith('/content'):return httpx.Response(200,content=raw[media],headers={'content-type':'video/mp4'})
        return httpx.Response(200,json={'id':'video_fixture','status':'completed'})
    async with db.connection()as c:await c.execute("UPDATE identity_sessions SET revoked_at=clock_timestamp() WHERE id='session'")
    async with httpx.AsyncClient(transport=httpx.MockTransport(request))as client:
        worker=MediaWorker(store.raw,ArtifactStore(settings.storage_root),client,settings,binding)
        assert await legacy_media.run_worker(worker)==1
        assert await legacy_media.run_worker(worker)==0
    async with db.connection()as c:
        rows=await(await c.execute('SELECT * FROM media_artifacts')).fetchall()
        work=await(await c.execute('SELECT * FROM media_work')).fetchone()
        assert len(rows)==1 and rows[0]['properties']['duration_ms']==500,work['last_safe_code']
        assert ArtifactStore(settings.storage_root).read(rows[0])==raw[media]
        assert(await(await c.execute('SELECT count(*) n FROM media_receipts')).fetchone())['n']==1
        assert(await(await c.execute('SELECT phase FROM media_work')).fetchone())['phase']=='done'
    assert (await ledger.summary(scope))['reserved']=='0'
    assert requests==[('GET','/v1/videos/video_fixture'),('GET','/v1/videos/video_fixture/content')]

@pytest.mark.asyncio
async def test_expired_queue_is_manual_and_keeps_financial_and_storage_holds(db,monkeypatch,legacy_media):
    store,service,ledger,scope,ctx,job=await setup_async(db,legacy_media,monkeypatch)
    await store.enqueue(job['id'],job['attempt_id'])
    # Time passage is injected through clock only; immutable deadline stays intact.
    monkeypatch.setattr('platform_service.media.async_store.now',lambda:__import__('datetime').datetime.max.replace(tzinfo=__import__('datetime').timezone.utc))
    assert await store.expire()==1
    assert await store.claim()==[]
    assert (await ledger.summary(scope))['reserved']=='5'
    async with db.connection()as c:
        assert(await(await c.execute('SELECT phase FROM media_work')).fetchone())['phase']=='manual'
        assert(await(await c.execute('SELECT released_at FROM media_storage_budgets')).fetchone())['released_at']is None

@pytest.mark.asyncio
async def test_stale_lease_cannot_write_terminal_or_settle(db,monkeypatch,legacy_media):
    store,service,ledger,scope,ctx,job=await setup_async(db,legacy_media,monkeypatch)
    await store.enqueue(job['id'],job['attempt_id']);claim=(await store.claim())[0]
    await store.observe(claim,completed())
    metadata={'sha256':'e'*64,'size':20,'mime':'video/mp4','properties':{'width':32,'height':32,'duration_ms':500,'format':'MP4'}}
    await store.stage_source(claim,'content',metadata);await store.attach_source(claim,'content')
    await store.prepare_settlement(claim)
    async with legacy_media.sql(service.job_permit(job['id']))as c:await c.execute("UPDATE media_work SET lease_until=clock_timestamp()-interval '1 second'")
    replacement=(await store.claim())[0]
    with pytest.raises(PlatformError):await service.record_terminal_evidence(job['id'],job['attempt_id'],terminal_status='completed',provider_response_id='video_fixture',quantities={'video_ms':500},basis_evidence={'verified_delivery_v1':'f'*64},evidence_hash='d'*64,work_claim=claim)
    assert (await ledger.summary(scope))['reserved']=='5'
    assert replacement['lease_token']!=claim['lease_token']

@pytest.mark.asyncio
async def test_settlement_failure_recovers_only_frozen_evidence_under_new_lease(db,monkeypatch,legacy_media):
    store,service,ledger,scope,ctx,job=await setup_async(db,legacy_media,monkeypatch)
    await store.enqueue(job['id'],job['attempt_id']);old=(await store.claim())[0]
    await store.observe(old,completed())
    metadata={'sha256':'e'*64,'size':20,'mime':'video/mp4','properties':{'width':32,'height':32,'duration_ms':500,'format':'MP4'}}
    await store.stage_source(old,'content',metadata);await store.attach_source(old,'content')
    await store.prepare_settlement(old)
    await service.record_provider_association(job['id'],job['attempt_id'],provider_response_id='video_fixture')
    await service.record_terminal_evidence(job['id'],job['attempt_id'],terminal_status='completed',provider_response_id='video_fixture',quantities={'video_ms':500},basis_evidence={'verified_delivery_v1':'f'*64},evidence_hash='d'*64,work_claim=old)
    async with legacy_media.sql(service.job_permit(job['id']))as c:await c.execute("UPDATE media_work SET lease_until=clock_timestamp()-interval '1 second'")
    replacement=(await store.claim())[0]
    with pytest.raises(PlatformError):await service.finish_from_evidence(job['id'],job['attempt_id'],work_claim=old)
    # Generic finance recovery cannot bypass the async lease fence.
    await service.recover_financial()
    assert (await ledger.summary(scope))['reserved']=='5'
    await service.finish_from_evidence(job['id'],job['attempt_id'],work_claim=replacement)
    await service.finish_from_evidence(job['id'],job['attempt_id'],work_claim=replacement)
    await store.done(replacement)
    async with db.connection()as c:assert(await(await c.execute('SELECT count(*) n FROM media_receipts')).fetchone())['n']==1

@pytest.mark.asyncio
async def test_cancelled_supplier_status_keeps_hold_without_cost_evidence(db,monkeypatch,tmp_path,files,legacy_media):
    from platform_service.media.worker import MediaWorker
    from platform_service.media_config import MediaProvider,MediaSettings
    store,service,ledger,scope,ctx,job=await setup_async(db,legacy_media,monkeypatch)
    await service.cancel(scope,job['id'])
    provider=MediaProvider('1','http://127.0.0.1:19991/v1','fixture',{'fixture-image':'explicit-fixture'},adapter='newapi_videos_v1',capabilities=('video.generate',))
    settings=MediaSettings(mode='test',providers={'fixture':provider},storage_root=legacy_media.source_path,av_tools=AVTools(*files[1]))
    async def binding(job):return provider
    def handler(request):
        assert request.method=='GET'
        return httpx.Response(200,json={'id':'video_fixture','status':'failed'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler))as client:
        await legacy_media.run_worker(MediaWorker(store.raw,ArtifactStore(settings.storage_root),client,settings,binding))
    assert(await ledger.summary(scope))['reserved']=='5'
    async with db.connection()as c:
        assert(await(await c.execute('SELECT count(*) n FROM media_receipts')).fetchone())['n']==0
        assert(await(await c.execute('SELECT phase FROM media_work')).fetchone())['phase']=='manual'

@pytest.mark.asyncio
async def test_nonowner_async_role_can_deliver_but_cannot_rebind_or_rewrite_history(db,test_dsn,monkeypatch,legacy_media):
    from uuid import uuid4
    import secrets
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from platform_service.db import Database
    from platform_service.media.core import MediaService
    from platform_service.media.async_store import MediaAsyncStore
    from platform_service.permissions import grant_runtime_permissions
    store,service,ledger,scope,ctx,job=await setup_async(db,legacy_media,monkeypatch)
    role='async_runtime_'+uuid4().hex;password=secrets.token_urlsafe(24)
    await legacy_media.create_runtime_role(role,password)
    lowdb=Database(make_conninfo(test_dsn,user=role,password=password),schema=db.schema)
    try:
        await lowdb.open();await lowdb.require_runtime_role()
        low=legacy_media.client(MediaService(lowdb,service.identity));queue=legacy_media.queue(low)
        await queue.enqueue(job['id'],job['attempt_id']);claim=(await queue.claim())[0]
        await queue.observe(claim,completed())
        metadata={'sha256':'e'*64,'size':20,'mime':'video/mp4','properties':{'width':32,'height':32,'duration_ms':500,'format':'MP4'}}
        await queue.stage_source(claim,'content',metadata);await queue.attach_source(claim,'content')
        await queue.prepare_settlement(claim)
        await low.record_provider_association(job['id'],job['attempt_id'],provider_response_id='video_fixture')
        await low.record_terminal_evidence(job['id'],job['attempt_id'],terminal_status='completed',provider_response_id='video_fixture',quantities={'video_ms':500},basis_evidence={'verified_delivery_v1':'f'*64},evidence_hash='d'*64,work_claim=claim)
        await low.finish_from_evidence(job['id'],job['attempt_id'],work_claim=claim);await queue.done(claim)
        for statement in ["UPDATE media_work SET provider_job_id='forged'","UPDATE media_observations SET response_hash=repeat('0',64)","UPDATE media_sources SET storage_key=repeat('a',64)",'DELETE FROM media_sources','TRUNCATE media_work']:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                async with lowdb.connection()as c:await c.execute(statement)
    finally:
        await lowdb.close()
        await legacy_media.drop_runtime_role(role)

@pytest.mark.asyncio
async def test_database_rejects_done_without_settlement(db,monkeypatch,legacy_media):
    import psycopg
    store,service,ledger,scope,ctx,job=await setup_async(db,legacy_media,monkeypatch)
    await store.enqueue(job['id'],job['attempt_id'])
    with pytest.raises(psycopg.errors.CheckViolation)as error:
        async with legacy_media.sql(service.job_permit(job['id']))as c:await c.execute("UPDATE media_work SET phase='done'")
    assert 'media maintenance permit required' not in str(error.value)

@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['incomplete','fresh','promote'])
async def test_stable_source_write_preserves_existing_partial_and_validates_original_bytes(db,legacy_media,monkeypatch,kind):
    from platform_service.media.worker import MediaWorker
    import hashlib
    queue,service,ledger,scope,ctx,job=await setup_async(db,legacy_media,monkeypatch)
    store=ArtifactStore(legacy_media.source_path)
    worker=MediaWorker(queue.raw,store,None,None,None)
    metadata={'storage_key':'a'*64,'sha256':hashlib.sha256(b'original').hexdigest(),'size':8,'mime':'text/plain','properties':{}}
    metadata=await service.stage_blob(ctx,metadata,kind='artifact',job_id=job['id'],attempt_id=job['attempt_id'])
    permit=service.job_permit(job['id'])
    partial=store.root/(metadata['storage_key']+'.part')
    if kind=='incomplete':
        partial.write_bytes(b'incomplete');partial.chmod(0o600)
        with pytest.raises(PlatformError):await legacy_media.physical(worker._write_source,b'original',metadata,permit=permit)
        assert partial.read_bytes()==b'incomplete'
        assert not (store.root/metadata['storage_key']).exists()
        assert (await ledger.summary(scope))['reserved']=='5'
    elif kind=='fresh':
        await legacy_media.physical(worker._write_source,b'original',metadata,permit=permit)
        assert store.read(metadata)==b'original'and not partial.exists()
    else:
        partial.write_bytes(b'original');partial.chmod(0o600)
        await legacy_media.physical(worker._promote_partial,metadata,permit=permit)
        assert store.read(metadata)==b'original'and not partial.exists()


def test_download_path_is_explicit_and_canonical():
    from platform_service.media.worker import check_download_path
    from types import SimpleNamespace
    provider=SimpleNamespace(download_path_prefixes=('/audio/',))
    check_download_path(provider,'https://cdn.example/audio/song.wav?signature=fixture')
    for value in ['https://cdn.example/private/song.wav','https://cdn.example/audio/../private.wav','https://cdn.example/audio/%2e%2e/private.wav','https://cdn.example/audio//song.wav']:
        with pytest.raises(PlatformError):check_download_path(provider,value)
    with pytest.raises(PlatformError):check_download_path(SimpleNamespace(download_path_prefixes=()),'https://cdn.example/audio/song.wav')

@pytest.mark.asyncio
async def test_database_binds_terminal_to_first_immutable_observation(db,monkeypatch,legacy_media):
    import psycopg
    store,service,ledger,scope,ctx,job=await setup_async(db,legacy_media,monkeypatch)
    await store.enqueue(job['id'],job['attempt_id']);claim=(await store.claim())[0]
    await store.observe(claim,completed())
    metadata={'sha256':'e'*64,'size':20,'mime':'video/mp4','properties':{'width':32,'height':32,'duration_ms':500,'format':'MP4'}}
    await store.stage_source(claim,'content',metadata);await store.attach_source(claim,'content');await store.prepare_settlement(claim)
    await service.record_provider_association(job['id'],job['attempt_id'],provider_response_id='video_fixture')
    with pytest.raises(psycopg.errors.CheckViolation):
        await service.record_terminal_evidence(job['id'],job['attempt_id'],terminal_status='completed',provider_response_id='video_fixture',quantities={'video_ms':500},basis_evidence={'verified_delivery_v1':'f'*64},evidence_hash='a'*64,work_claim=claim)
    assert(await ledger.summary(scope))['reserved']=='5'

@pytest.mark.asyncio
async def test_real_pg_expired_slow_get_is_cancelled_manual_and_heartbeat_cannot_extend(db,monkeypatch,tmp_path,legacy_media):
    import asyncio
    from platform_service.media.worker import MediaWorker
    from platform_service.media_config import MediaProvider,MediaSettings
    store,service,ledger,scope,ctx,job=await setup_async(db,legacy_media,monkeypatch)
    async with legacy_media.sql(service.job_permit(job['id']))as c:
        await c.execute("INSERT INTO media_work(job_id,attempt_id,binding_hash,provider_scope_hash,provider_job_id,adapter,deadline) SELECT job_id,attempt_id,binding_hash,provider_scope_hash,provider_job_id,'newapi_videos_v1',clock_timestamp()+interval '200 milliseconds' FROM media_provider_associations WHERE job_id=%s AND kind='job'",(job['id'],))
    provider=MediaProvider('1','http://127.0.0.1:19991/v1','fixture',{'fixture-image':'explicit-fixture'},adapter='newapi_videos_v1',capabilities=('video.generate',))
    settings=MediaSettings(mode='test',providers={'fixture':provider},storage_root=legacy_media.source_path)
    cancelled=[];requests=[]
    class Slow(httpx.AsyncByteStream):
        async def __aiter__(self):
            try:
                for byte in b'{"id":"video_fixture","status":"in_progress"}':
                    await asyncio.sleep(.02);yield bytes([byte])
            finally:cancelled.append(True)
    def handler(request):
        requests.append(request.method)
        return httpx.Response(200,stream=Slow(),headers={'content-type':'application/json'})
    async def binding(job):return provider
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler),timeout=.025)as client:
        worker=MediaWorker(store.raw,ArtifactStore(settings.storage_root),client,settings,binding)
        async with asyncio.timeout(1):await legacy_media.run_worker(worker)
    async with db.connection()as c:
        work=await(await c.execute('SELECT * FROM media_work')).fetchone()
        assert work['phase']=='manual'and work['lease_token']is None
        assert(await(await c.execute('SELECT released_at FROM media_storage_budgets')).fetchone())['released_at']is None
    assert requests==['GET']and cancelled
    assert(await ledger.summary(scope))['reserved']=='5'
    with pytest.raises(PlatformError):await store.heartbeat(work)

@pytest.mark.asyncio
async def test_known_query_retains_active_admission_but_get_failure_stays_pending(db,monkeypatch,legacy_media):
    import test_media_credits as fixture
    from platform_service.media.async_contracts import Observation
    from platform_service.identity import now
    original=fixture.setup
    async def one(*args,**kwargs):return await original(*args,concurrency=1,**kwargs)
    monkeypatch.setattr(fixture,'setup',one)
    queue,service,ledger,scope,ctx,job=await setup_async(db,legacy_media,monkeypatch)
    await queue.enqueue(job['id'],job['attempt_id'])
    async with legacy_media.sql(service.job_permit(job['id']))as c:await c.execute("UPDATE media_jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s",(job['id'],))
    # A valid durable query is still admitted while waiting for its GET worker.
    await service.recover_financial()
    assert(await ledger.summary(scope))['active_requests']==1
    claim=(await queue.claim())[0]
    await queue.heartbeat(claim)
    async with db.connection()as c:
        row=await(await c.execute('SELECT lease_until FROM media_jobs WHERE id=%s',(job['id'],))).fetchone()
        assert now()<row['lease_until']<=claim['deadline']
    running=Observation('video_fixture','running',(),{},None,'d'*64)
    await queue.observe(claim,running);await service.recover_financial()
    assert(await ledger.summary(scope))['active_requests']==1
    with pytest.raises(PlatformError)as error:await service.admit(ctx,key='second',request=job['request'],bindings=fixture.BINDINGS)
    assert error.value.code=='concurrency_limit'
    await legacy_media.due_work()
    claim=(await queue.claim())[0];await queue.defer(claim,'media_get_timeout')
    assert(await ledger.summary(scope))['active_requests']==0
    assert(await ledger.summary(scope))['pending_reconciliation']==1
    await legacy_media.due_work()
    claim=(await queue.claim())[0];await queue.observe(claim,running)
    await service.recover_financial()
    assert(await ledger.summary(scope))['active_requests']==0
    assert(await ledger.summary(scope))['reserved']=='5'
