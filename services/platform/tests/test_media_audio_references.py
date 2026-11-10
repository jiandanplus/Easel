"""Real-PG constraints for binary response references; no provider requests."""
import pytest
from b2_legacy_media_fixtures import legacy_media, legacy_db as db, legacy_dsn as test_dsn
import psycopg
from platform_service.errors import PlatformError
from test_media_credits import setup,admit


async def binary_job(db,legacy_media,key='binary',http_id='req_binary_fixture'):
    service,ledger,scope,ctx,*_=await legacy_media.setup(db)
    job=await admit(service,ctx,key)
    await service.record_provider_attempt(job['id'],job['attempt_id'],provider_request_hash='b'*64,
        provider_scope_hash='c'*64,upstream_model='fixture-upstream',request_id='local-'+job['id'])
    await service.dispatch(job['id'],job['attempt_id'])
    await service.record_provider_association(job['id'],job['attempt_id'],http_status=200,http_request_id=http_id)
    return service,ledger,scope,ctx,job


async def insert_reference(c,job,**patch):
    values=dict(job_id=job['id'],attempt_id=job['attempt_id'],binding_hash=job['binding_hash'],provider_scope_hash='c'*64,
                namespace='openai_x_request_id_v1',http_request_id='req_binary_fixture',response_body_sha256='d'*64)
    values.update(patch)
    return await (await c.execute('INSERT INTO media_http_response_references(job_id,attempt_id,binding_hash,provider_scope_hash,namespace,http_request_id,response_body_sha256) VALUES (%(job_id)s,%(attempt_id)s,%(binding_hash)s,%(provider_scope_hash)s,%(namespace)s,%(http_request_id)s,%(response_body_sha256)s) RETURNING *',values)).fetchone()


@pytest.mark.asyncio
async def test_binary_reference_is_distinct_from_object_id_and_binds_authentic_http_header(db,legacy_media):
    service,ledger,scope,ctx,job=await binary_job(db,legacy_media)
    async with legacy_media.sql(service.job_permit(job['id'])) as c:
        ref=await insert_reference(c,job)
        assert ref['namespace']=='openai_x_request_id_v1' and ref['response_body_sha256']=='d'*64
    for index,patch in enumerate([{'namespace':'guessed'},{'http_request_id':'local-only'}, {'http_request_id':'req_other'},
                  {'attempt_id':'other'},{'binding_hash':'e'*64},{'provider_scope_hash':'e'*64}]):
        candidate=await admit(service,ctx,'candidate-'+str(index))
        await service.record_provider_attempt(candidate['id'],candidate['attempt_id'],provider_request_hash='b'*64,
            provider_scope_hash='c'*64,upstream_model='fixture-upstream',request_id='local-'+candidate['id'])
        await service.dispatch(candidate['id'],candidate['attempt_id'])
        await service.record_provider_association(candidate['id'],candidate['attempt_id'],http_status=200,http_request_id='req_candidate_'+str(index))
        with pytest.raises(psycopg.Error) as error:
            async with legacy_media.sql(service.job_permit(candidate['id'])) as c:
                await insert_reference(c,candidate,**{'http_request_id':'req_candidate_'+str(index),**patch})
        # Foreign attempt is rejected by exact capability binding; other cases
        # must still reach the original reference invariant under a valid cap.
        if 'attempt_id' not in patch:
            assert 'media maintenance permit required' not in str(error.value)


@pytest.mark.asyncio
async def test_typed_binary_terminal_requires_equal_body_hash_and_keeps_provider_id_null(db,legacy_media):
    from psycopg.types.json import Jsonb
    service,ledger,scope,ctx,job=await binary_job(db,legacy_media)
    async with legacy_media.sql(service.job_permit(job['id'])) as c:await insert_reference(c,job)
    async def terminal(evidence_hash='d'*64,provider_id=None,reference_kind='http_request_id'):
        async with legacy_media.sql(service.job_permit(job['id'])) as c:
            await c.execute('INSERT INTO media_terminal_evidence(job_id,attempt_id,binding_hash,terminal_status,provider_response_id,quantities,basis_evidence,evidence_hash,reference_kind,http_reference_namespace,http_reference_value) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (job['id'],job['attempt_id'],job['binding_hash'],'completed',provider_id,Jsonb({'image':0}),Jsonb({'verified_delivery_v1':'e'*64}),evidence_hash,reference_kind,'openai_x_request_id_v1','req_binary_fixture'))
            await c.execute("UPDATE media_jobs SET execution_state='completed' WHERE id=%s",(job['id'],))
            await service._release_storage_budget(c,job['id'])
    for args in [dict(evidence_hash='e'*64),dict(provider_id='fabricated-object'),dict(reference_kind='object_id')]:
        with pytest.raises(psycopg.Error) as error:await terminal(**args)
        assert 'media maintenance permit required' not in str(error.value)
    await terminal()
    async with legacy_media.sql(service.job_permit(job['id'])) as c:
        evidence=await (await c.execute('SELECT * FROM media_terminal_evidence WHERE job_id=%s',(job['id'],))).fetchone()
        assert evidence['provider_response_id'] is None and evidence['reference_kind']=='http_request_id'
        with pytest.raises(psycopg.Error) as error:await c.execute("UPDATE media_http_response_references SET response_body_sha256=repeat('0',64)")
        assert 'media maintenance permit required' not in str(error.value)


@pytest.mark.asyncio
async def test_same_authenticated_http_response_cannot_be_attached_to_another_job(db,legacy_media):
    service,ledger,scope,ctx,first=await binary_job(db,legacy_media)
    async with legacy_media.sql(service.job_permit(first['id'])) as c:await insert_reference(c,first)
    second=await admit(service,ctx,'other')
    await service.record_provider_attempt(second['id'],second['attempt_id'],provider_request_hash='e'*64,
        provider_scope_hash='c'*64,upstream_model='fixture-upstream',request_id='local-second')
    await service.dispatch(second['id'],second['attempt_id'])
    await service.record_provider_association(second['id'],second['attempt_id'],http_status=200,http_request_id='req_binary_fixture')
    with pytest.raises(psycopg.Error) as error:
        async with legacy_media.sql(service.job_permit(second['id'])) as c:await insert_reference(c,second)
    assert 'media maintenance permit required' not in str(error.value)
    assert (await ledger.summary(scope))['reserved']=='12'
