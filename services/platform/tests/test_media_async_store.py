"""Real-PG recovery queue and lease fencing; no supplier traffic."""
import asyncio
import copy
from importlib import import_module

import psycopg
import pytest
from b2_legacy_media_fixtures import legacy_media, legacy_db as db, legacy_dsn as test_dsn
from platform_service.errors import PlatformError
from platform_service.media.async_contracts import ArtifactSource,Observation


async def setup_async(db,legacy_media,monkeypatch,*,associate=True):
    import test_media_credits as fixture
    from test_media_contracts import definition
    value=definition('video.generate');value['adapter']='newapi_videos_v1'
    value['rate']['meters']=[{'unit':'video_ms','basis':'verified_delivery_v1','numerator':'1','denominator':'1000'}]
    value['limits']['max_units']={'video_ms':5000}
    value['limits']['max_assets']=0
    value['limits']['output_media']={'mime_types':['video/mp4'],'max_files':1,'max_duration_ms':1000,'max_pixels':1024,'max_channels':2,'max_sample_rate':48000,'require_audio':False}
    value['parameters']={'type':'object','properties':{},'required':[],'additionalProperties':False}
    bindings=copy.deepcopy(fixture.BINDINGS);bindings['fixture-image']['adapter']='newapi_videos_v1'
    monkeypatch.setattr(fixture,'BINDINGS',bindings)
    service,ledger,scope,ctx,*_=await legacy_media.setup(db,media_definition=value)
    request={'model':'fixture-image','capability':'video.generate','prompt':'Synthetic video','assets':[],'parameters':{}}
    job=await service.admit(ctx,key='first',request=request,bindings=bindings)
    await service.record_provider_attempt(job['id'],job['attempt_id'],provider_request_hash='b'*64,provider_scope_hash='c'*64,upstream_model='explicit-fixture',request_id='local-'+job['id'])
    await service.dispatch(job['id'],job['attempt_id'])
    await service.record_provider_association(job['id'],job['attempt_id'],http_status=200,http_request_id='http-'+job['id'])
    if associate:await service.record_provider_association(job['id'],job['attempt_id'],provider_job_id='video_fixture')
    store=legacy_media.queue(service)
    return store,service,ledger,scope,ctx,job


def completed():
    return Observation('video_fixture','completed',(ArtifactSource('content','content','http://127.0.0.1:19991/v1/videos/video_fixture/content','video/mp4'),),{},None,'d'*64)


@pytest.mark.asyncio
async def test_known_association_recovers_one_queue_and_exclusive_lease(db,monkeypatch,legacy_media):
    store,service,ledger,scope,ctx,job=await setup_async(db,legacy_media,monkeypatch)
    assert await store.recover_associations()==1
    assert await store.recover_associations()==0
    first,second=await asyncio.gather(store.claim(limit=1),store.claim(limit=1))
    claimed=first+second
    assert len(claimed)==1 and claimed[0]['job_id']==job['id']
    assert claimed[0]['provider_job_id']=='video_fixture'
    assert(await ledger.summary(scope))['reserved']=='5'


@pytest.mark.asyncio
async def test_no_provider_id_remains_unknown_and_is_never_queued_for_regeneration(db,monkeypatch,legacy_media):
    store,service,ledger,scope,ctx,job=await setup_async(db,legacy_media,monkeypatch,associate=False)
    assert await store.recover_associations()==0 and await store.claim()==[]
    with pytest.raises(PlatformError):await store.enqueue(job['id'],job['attempt_id'])
    assert(await ledger.summary(scope))['reserved']=='5'
    async with legacy_media.sql(service.job_permit(job['id']))as c:assert(await(await c.execute('SELECT count(*) n FROM media_work')).fetchone())['n']==0


@pytest.mark.asyncio
async def test_reclaimed_lease_rejects_late_observation_and_preserves_download_budget(db,monkeypatch,legacy_media):
    store,service,ledger,scope,ctx,job=await setup_async(db,legacy_media,monkeypatch)
    await store.enqueue(job['id'],job['attempt_id']);old=(await store.claim())[0]
    async with legacy_media.sql(service.job_permit(job['id']))as c:await c.execute("UPDATE media_work SET lease_until=clock_timestamp()-interval '1 second'")
    new=(await store.claim())[0]
    assert old['lease_token']!=new['lease_token']
    with pytest.raises(PlatformError):await store.observe(old,completed())
    await store.observe(new,completed())
    with pytest.raises(PlatformError):await store.defer(old,'late_response')
    async with legacy_media.sql(service.job_permit(job['id']))as c:
        budget=await(await c.execute('SELECT * FROM media_storage_budgets WHERE job_id=%s',(job['id'],))).fetchone()
        assert budget['released_at']is None and budget['remaining_bytes']==8388608
        assert(await(await c.execute('SELECT count(*) n FROM media_terminal_evidence')).fetchone())['n']==0
        assert(await(await c.execute('SELECT count(*) n FROM media_observations')).fetchone())['n']==1
    assert(await ledger.summary(scope))['reserved']=='5'
    await service.cancel(scope,job['id'])
    assert(await service.get(scope,job['id']))['actual']is None


@pytest.mark.asyncio
async def test_observation_scope_and_sources_are_immutable_and_source_refresh_cannot_change_identity(db,monkeypatch,legacy_media):
    store,service,ledger,scope,ctx,job=await setup_async(db,legacy_media,monkeypatch)
    await store.enqueue(job['id'],job['attempt_id']);claim=(await store.claim())[0]
    await store.observe(claim,completed())
    wrong=Observation('different','completed',completed().sources,{},None,'d'*64)
    with pytest.raises(PlatformError):await store.observe(claim,wrong)
    wrong=Observation('video_fixture','completed',(ArtifactSource('other','content',completed().sources[0].location,'video/mp4'),),{},None,'d'*64)
    with pytest.raises(PlatformError):await store.observe(claim,wrong)
    for command in ["UPDATE media_work SET binding_hash=repeat('e',64)","UPDATE media_observations SET response_hash=repeat('e',64)",'DELETE FROM media_sources']:
        with pytest.raises(psycopg.Error) as error:
            async with legacy_media.sql(service.job_permit(job['id']))as c:await c.execute(command)
        assert 'media maintenance permit required' not in str(error.value)


@pytest.mark.asyncio
async def test_source_stage_and_attachment_are_fenced_and_idempotent_after_logout(db,monkeypatch,legacy_media):
    store,service,ledger,scope,ctx,job=await setup_async(db,legacy_media,monkeypatch)
    await store.enqueue(job['id'],job['attempt_id']);claim=(await store.claim())[0];await store.observe(claim,completed())
    sources=await store.sources(claim);assert len(sources)==1
    metadata={'sha256':'e'*64,'size':20,'mime':'video/mp4','properties':{'width':32,'height':32,'duration_ms':500,'format':'MP4'}}
    async with legacy_media.sql(service.job_permit(job['id']))as c:await c.execute("UPDATE identity_sessions SET revoked_at=clock_timestamp() WHERE id='session'")
    staged=await store.stage_source(claim,'content',metadata)
    assert (await store.stage_source(claim,'content',metadata))['storage_key']==staged['storage_key']
    first=await store.attach_source(claim,'content');second=await store.attach_source(claim,'content')
    assert first['id']==second['id']
    with pytest.raises(PlatformError):await store.stage_source({**claim,'lease_token':'stale'},'content',metadata)
    async with legacy_media.sql(service.job_permit(job['id']))as c:
        assert(await(await c.execute('SELECT count(*) n FROM media_artifacts')).fetchone())['n']==1
        assert(await(await c.execute('SELECT remaining_bytes FROM media_storage_budgets WHERE job_id=%s',(job['id'],))).fetchone())['remaining_bytes']==8388608-20
