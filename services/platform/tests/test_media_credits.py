"""Real PostgreSQL integration; media competes for the existing shared wallet."""
import asyncio
import copy
from dataclasses import replace
import pytest
from b2_legacy_media_fixtures import legacy_media, legacy_db as db, legacy_dsn as test_dsn
from platform_service.errors import PlatformError
from platform_service.contracts import OperatorIdentity
from test_execution_core import setup as text_setup, admit as text_admit
from test_media_contracts import definition

OPERATOR=OperatorIdentity('media-operator',frozenset({'media:publish'}))
BINDINGS={'fixture-image':{'provider_id':'fixture','provider_version':'1','adapter':'openai_images_v1','protocol_version':'1','binding_hash':'a'*64}}
REQUEST={'model':'fixture-image','capability':'image.generate','prompt':'Synthetic image','assets':[],'parameters':{'n':1}}


async def setup(db, concurrency=10, media_definition=None, *, maintenance=None):
    from platform_service.media.core import MediaService
    from platform_service.membership import MembershipService
    from test_membership import seed
    from test_execution_core import definition as text_definition, BINDINGS as TEXT_BINDINGS, OPERATOR as TEXT_OPERATOR
    from platform_service.execution.core import ExecutionService
    from platform_service.identity import IdentityService
    from platform_service.credits import CreditLedger
    membership,operator,original,scope=await seed(db,'300000000')
    await membership.publish_plan(operator,{**original,'id':'media-plan','name':'Media fixture','features':['text','media'],'concurrency':concurrency})
    await membership.issue_test(operator,'wallet','media-plan','media-grant')
    ledger=CreditLedger(db)
    async with db.connection() as c:
        await c.execute("INSERT INTO product_clients(product_id,client_id) VALUES ('easel','desktop')")
        await c.execute("INSERT INTO external_identities(issuer,subject,user_id) VALUES ('fixture','subject','u')")
        await c.execute("INSERT INTO identity_sessions(id,user_id,tenant_id,billing_account_id,product_id,client_id,authorization_epoch,issuer,subject,expires_at) VALUES ('session','u','t','wallet','easel','desktop',0,'fixture','subject',now()+interval '1 hour')")
    text=ExecutionService(db,IdentityService(db,None))
    await text.publish(text_definition(),TEXT_OPERATOR);await text.set_enabled('fixture-text',True,TEXT_OPERATOR)
    text_token=await text.mint(scope,model='fixture-text',invocation_key='text-run',mode='test',bindings=TEXT_BINDINGS)
    text_context=await text.authenticate_task(text_token['task_token'])
    service=MediaService(db,text.identity)
    # Callers explicitly supply a real trusted generation from owned fixtures.
    # No None/default flag can mint a product capability.
    from platform_service.media_maintenance.runtime import RuntimeGeneration
    from platform_service.media_maintenance.contracts import require
    from uuid import uuid4
    require(type(maintenance) is RuntimeGeneration)
    service.maintenance=maintenance
    async with maintenance.use('catalog',(media_definition or definition())['id'],transient=True):
        await service.publish(media_definition or definition(),OPERATOR)
        await service.set_enabled('fixture-image',True,OPERATOR)
    async with maintenance.use('mint',str(uuid4()),transient=True):
        token=await service.mint(scope,model='fixture-image',capability=(media_definition or definition())['capability'],invocation_key='media-run',mode='test',bindings=BINDINGS)
    context=await service.authenticate_task(token['task_token'])
    return service,ledger,scope,context,text,text_context


async def admit(service,context,key='operation',request=None):
    return await service.admit(context,key=key,request=copy.deepcopy(request or REQUEST),bindings=BINDINGS)


async def dispatch(service,job):
    await service.record_provider_attempt(job['id'],job['attempt_id'],provider_request_hash='b'*64,provider_scope_hash='c'*64,upstream_model='fixture-upstream',request_id='request-'+job['id'])
    await service.dispatch(job['id'],job['attempt_id'])
    await service.record_provider_association(job['id'],job['attempt_id'],http_status=200,http_request_id='http-'+job['id'])
    await service.record_provider_association(job['id'],job['attempt_id'],provider_response_id='response-'+job['id'])


async def artifact(service,job):
    return await service.register_artifact(job['id'],job['attempt_id'],storage_key='e'*32,sha256='f'*64,size=10,mime='image/png',properties={'width':1,'height':1,'format':'png'})


async def finish(service,job,quantity=1):
    return await service.finish(job['id'],job['attempt_id'],terminal_status='completed',provider_response_id='response-'+job['id'],quantities={'image':quantity},basis_evidence={'verified_delivery_v1':'d'*64},evidence_hash='e'*64)


@pytest.mark.asyncio
async def test_media_reserve_replay_exact_definition_and_round_once_settlement(db,legacy_media):
    service,ledger,scope,ctx,*_=await legacy_media.setup(db)
    before=await ledger.summary(scope);job=await admit(service,ctx)
    assert job['amount']==6 and job['replay'] is False
    assert (await admit(service,ctx))['id']==job['id']
    assert int((await ledger.summary(scope))['reserved'])==6
    await dispatch(service,job);await artifact(service,job)
    assert (await finish(service,job))['actual']=='2'
    assert (await finish(service,job))['actual']=='2'
    summary=await ledger.summary(scope)
    assert int(summary['spent'])-int(before['spent'])==2 and summary['reserved']=='0'
    with pytest.raises(PlatformError):await admit(service,ctx,request={**REQUEST,'prompt':'changed'})
    result=await service.get(scope,job['id'])
    assert result['execution_state']=='completed' and result['financial_state']=='settled'
    assert result['artifacts'][0]['sha256']=='f'*64 and 'storage_key' not in result['artifacts'][0]


@pytest.mark.asyncio
async def test_same_key_concurrent_media_admissions_have_one_reservation(db,legacy_media):
    service,ledger,scope,ctx,*_=await legacy_media.setup(db)
    jobs=await asyncio.gather(*(admit(service,ctx) for _ in range(5)))
    assert len({job['id'] for job in jobs})==1
    async with db.connection() as c:
        assert (await (await c.execute("SELECT count(*) n FROM credit_reservations WHERE operation='media'")).fetchone())['n']==1
    assert (await ledger.summary(scope))['reserved']=='6'


@pytest.mark.asyncio
async def test_media_and_text_share_wallet_concurrency_and_pending_ceiling(db,legacy_media):
    service,ledger,scope,ctx,text,text_ctx=await legacy_media.setup(db,concurrency=1)
    media=await admit(service,ctx)
    with pytest.raises(PlatformError) as e:await text_admit(text,text_ctx)
    assert e.value.code=='concurrency_limit'
    await service.cancel(scope,media['id'])
    text_job=await text_admit(text,text_ctx)
    with pytest.raises(PlatformError) as e:await admit(service,ctx,'second')
    assert e.value.code=='concurrency_limit'
    await text.dispatch(text_job['id'],text_job['attempt_id']);await text.pending(text_job['id'],text_job['attempt_id'],'unknown')
    for index in range(2):
        job=await admit(service,ctx,'pending-'+str(index));await dispatch(service,job);await service.pending(job['id'],job['attempt_id'],'unknown')
    with pytest.raises(PlatformError) as e:await admit(service,ctx,'blocked')
    assert e.value.code=='pending_reconciliation_limit'
    assert (await ledger.summary(scope))['pending_reconciliation']==3


@pytest.mark.asyncio
async def test_dispatched_unknown_cannot_release_and_expired_hold_settles_to_original_lot(db,legacy_media):
    service,ledger,scope,ctx,*_=await legacy_media.setup(db)
    job=await admit(service,ctx);await dispatch(service,job)
    cancelled=await service.cancel(scope,job['id'])
    assert cancelled['financial_state']=='pending_reconciliation' and cancelled['actual'] is None
    with pytest.raises(PlatformError):await ledger.release(scope,job['reservation_id'],key='not-safe')
    async with db.connection() as c:await c.execute("UPDATE credit_lots SET expires_at=clock_timestamp()-interval '1 second'")
    await artifact(service,job);await finish(service,job)
    after=await ledger.summary(scope)
    assert after['available']=='0' and after['reserved']=='0' and after['spent']=='2'


@pytest.mark.asyncio
async def test_committed_media_evidence_recovers_finances_without_second_attempt(db,monkeypatch,legacy_media):
    service,ledger,scope,ctx,*_=await legacy_media.setup(db)
    job=await admit(service,ctx);await dispatch(service,job);await artifact(service,job)
    complete=service.ledger._complete
    async def unavailable(*args,**kwargs):raise RuntimeError('synthetic financial interruption')
    monkeypatch.setattr(service.ledger,'_complete',unavailable)
    with pytest.raises(RuntimeError):await finish(service,job)
    async with db.connection() as c:
        assert (await (await c.execute('SELECT count(*) n FROM media_terminal_evidence')).fetchone())['n']==1
    monkeypatch.setattr(service.ledger,'_complete',complete)
    await service.finish_from_evidence(job['id'],job['attempt_id'])
    await service.finish_from_evidence(job['id'],job['attempt_id'])
    assert (await ledger.summary(scope))['spent']=='2'
    async with db.connection() as c:
        assert (await (await c.execute('SELECT count(*) n FROM media_provider_attempts')).fetchone())['n']==1


@pytest.mark.asyncio
async def test_media_overbudget_usage_retains_hold_and_no_fake_receipt(db,legacy_media):
    d=definition()
    d['rate']={'version':'provider-1','meters':[{'unit':'input_token','basis':'provider_usage_v1','numerator':'3','denominator':'2'}]}
    d['limits']['max_units']={'input_token':4}
    service,ledger,scope,ctx,*_=await legacy_media.setup(db,media_definition=d)
    job=await admit(service,ctx);await dispatch(service,job)
    with pytest.raises(PlatformError):
        await service.finish(job['id'],job['attempt_id'],terminal_status='completed',provider_response_id='response-'+job['id'],quantities={'input_token':5},basis_evidence={'provider_usage_v1':'d'*64},evidence_hash='e'*64)
    result=await service.get(scope,job['id'])
    assert result['financial_state']=='pending_reconciliation' and result['actual'] is None
    assert (await ledger.summary(scope))['reserved']=='6'
    async with db.connection() as c:
        assert (await (await c.execute('SELECT count(*) n FROM media_receipts')).fetchone())['n']==0
        assert (await (await c.execute('SELECT count(*) n FROM media_terminal_evidence')).fetchone())['n']==1
        assert (await (await c.execute('SELECT count(*) n FROM media_blocked_bindings')).fetchone())['n']==1


@pytest.mark.asyncio
async def test_database_rejects_media_scope_amount_receipt_and_counter_tampering(db,legacy_media):
    import psycopg
    service,ledger,scope,ctx,*_=await legacy_media.setup(db)
    job=await admit(service,ctx)
    for sql,params in [('UPDATE media_invocations SET held=held+1 WHERE id=%s',(ctx['invocation_id'],)),
                       ('UPDATE media_jobs SET amount=amount+1 WHERE id=%s',(job['id'],)),
                       ("UPDATE credit_reservations SET status='dispatched' WHERE id=%s",(job['reservation_id'],))]:
        with pytest.raises(psycopg.Error) as error:
            async with legacy_media.sql(service.job_permit(job['id'])) as c:await c.execute(sql,params)
        assert 'media maintenance permit required' not in str(error.value)
    await dispatch(service,job);await artifact(service,job);await finish(service,job)
    with pytest.raises(psycopg.Error) as error:
        async with legacy_media.sql(service.job_permit(job['id'])) as c:await c.execute("UPDATE media_terminal_evidence SET quantities='{}'")
    assert 'media maintenance permit required' not in str(error.value)


@pytest.mark.asyncio
async def test_trusted_execution_terminal_survives_financial_interruption(db,monkeypatch,legacy_media):
    service,ledger,scope,ctx,*_=await legacy_media.setup(db)
    job=await admit(service,ctx);await dispatch(service,job);await artifact(service,job)
    async def broken(*args,**kwargs):raise RuntimeError('synthetic interruption')
    monkeypatch.setattr(service.ledger,'_complete',broken)
    with pytest.raises(RuntimeError):await finish(service,job)
    before=await service.get(scope,job['id'])
    assert before['execution_state']=='completed' and before['financial_state']=='dispatched'
    await service.pending(job['id'],job['attempt_id'],'worker_interruption')
    after=await service.get(scope,job['id'])
    assert after['execution_state']=='completed' and after['actual'] is None


@pytest.mark.asyncio
async def test_recovery_never_dispatches_reserved_or_unknown_jobs(db,legacy_media):
    service,ledger,scope,ctx,*_=await legacy_media.setup(db)
    unsent=await admit(service,ctx,'unsent')
    unknown=await admit(service,ctx,'unknown');await dispatch(service,unknown)
    for original in (unsent,unknown):
        async with legacy_media.sql(service.job_permit(original['id'])) as c:
            await c.execute("UPDATE media_jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s",(original['id'],))
    await service.recover_financial();await service.recover_financial()
    assert (await service.get(scope,unsent['id']))['financial_state']=='released'
    assert (await service.get(scope,unknown['id']))['financial_state']=='pending_reconciliation'
    assert (await ledger.summary(scope))['reserved']=='6'
    async with db.connection() as c:
        assert (await (await c.execute('SELECT count(*) n FROM media_provider_attempts')).fetchone())['n']==1


@pytest.mark.asyncio
async def test_late_duplicate_or_changed_evidence_never_charges_twice(db,legacy_media):
    service,ledger,scope,ctx,*_=await legacy_media.setup(db)
    job=await admit(service,ctx);await dispatch(service,job);await artifact(service,job)
    await finish(service,job)
    before=await ledger.summary(scope)
    with pytest.raises(PlatformError):
        await service.finish(job['id'],job['attempt_id'],terminal_status='failed',provider_response_id='response-'+job['id'],quantities={'image':1},basis_evidence={'verified_delivery_v1':'d'*64},evidence_hash='e'*64)
    assert await ledger.summary(scope)==before
    with pytest.raises(PlatformError):await dispatch(service,job)


@pytest.mark.asyncio
async def test_asset_revalidation_uses_registered_identity_and_real_byte_limit(db,legacy_media):
    d=definition('image.edit');d['limits']['max_input_bytes']=1024
    service,ledger,scope,ctx,*_=await legacy_media.setup(db,media_definition=d)
    # Context capability is minted from the definition, never client asserted.
    asset=await service.register_asset(ctx,storage_key='1'*32,sha256='2'*64,size=950,mime='image/png',properties={'width':2,'height':2,'format':'png'})
    request={**REQUEST,'capability':'image.edit','assets':[asset['id']]}
    verified={asset['id']:{k:asset[k] for k in ('sha256','size','mime','properties')}}
    for values in [None,{asset['id']:{**verified[asset['id']],'size':1}},verified]:
        with pytest.raises(PlatformError):await service.admit(ctx,key='invalid',request=request,bindings=BINDINGS,verified_inputs=values)
    assert (await ledger.summary(scope))['reserved']=='0'


@pytest.mark.asyncio
async def test_verified_input_counts_unicode_scalars_not_client_metadata(db,legacy_media):
    d=definition('audio.speech')
    d['rate']={'version':'chars','meters':[{'unit':'unicode_scalar','basis':'verified_input_v1','numerator':'1','denominator':'1'}]}
    d['limits']['max_units']={'unicode_scalar':20}
    service,ledger,scope,ctx,*_=await legacy_media.setup(db,media_definition=d)
    job=await service.admit(ctx,key='speech',request={**REQUEST,'capability':'audio.speech','prompt':'A😀e\u0301'},bindings=BINDINGS)
    assert job['input_units']['unicode_scalar']==4 and job['amount']==20
    await dispatch(service,job)
    with pytest.raises(PlatformError):
        await service.record_terminal_evidence(job['id'],job['attempt_id'],terminal_status='completed',provider_response_id='response-'+job['id'],quantities={'unicode_scalar':8},basis_evidence={'verified_input_v1':'d'*64},evidence_hash='e'*64)
    await service.finish(job['id'],job['attempt_id'],terminal_status='completed',provider_response_id='response-'+job['id'],quantities={'unicode_scalar':4},basis_evidence={'verified_input_v1':'d'*64},evidence_hash='e'*64)
    assert (await ledger.summary(scope))['spent']=='4'


@pytest.mark.asyncio
async def test_independent_text_and_media_pools_share_one_execution_slot(db,test_dsn,legacy_media):
    from platform_service.db import Database
    from platform_service.media.core import MediaService
    from platform_service.identity import IdentityService
    service,ledger,scope,ctx,text,text_ctx=await legacy_media.setup(db,concurrency=1)
    second=replace(scope,product_id='second',session_id='second-session')
    async with db.connection() as c:
        await c.execute("INSERT INTO product_clients(product_id,client_id) VALUES ('second','desktop')")
        await c.execute("INSERT INTO identity_sessions(id,user_id,tenant_id,billing_account_id,product_id,client_id,authorization_epoch,issuer,subject,expires_at) VALUES ('second-session','u','t','wallet','second','desktop',0,'fixture','subject',now()+interval '1 hour')")
    token=await service.mint(second,model='fixture-image',capability='image.generate',invocation_key='cross-product',mode='test',bindings=BINDINGS)
    ctx=await service.authenticate_task(token['task_token'])
    other=Database(test_dsn,schema=db.schema,min_size=1,max_size=1);await other.open()
    try:
        media=legacy_media.client(MediaService(other,IdentityService(other,None)))
        result=await asyncio.gather(admit(media,ctx),text_admit(text,text_ctx),return_exceptions=True)
        assert sum(isinstance(value,dict) for value in result)==1
        failure=next(value for value in result if isinstance(value,PlatformError))
        assert failure.code=='concurrency_limit'
        async with db.connection() as c:
            assert (await (await c.execute("SELECT count(*) n FROM credit_reservations WHERE status='reserved'")).fetchone())['n']==1
    finally:await other.close()


@pytest.mark.asyncio
async def test_frozen_request_hash_includes_binding_definition_and_asset_manifest(db,legacy_media):
    from platform_service.media.contracts import digest
    service,ledger,scope,ctx,*_=await legacy_media.setup(db)
    job=await admit(service,ctx)
    assert job['request_hash']==digest({'request':job['request'],'definition':job['model_definition'],'binding_hash':job['binding_hash'],'inputs':{}})
    altered={**BINDINGS,'fixture-image':{**BINDINGS['fixture-image'],'binding_hash':'b'*64}}
    replay=await service.admit(ctx,key='operation',request=REQUEST,bindings=altered)
    assert replay['id']==job['id'] and replay['binding_hash']=='a'*64
    fresh=await service.admit(ctx,key='second',request=REQUEST,bindings=altered)
    assert fresh['request_hash']!=job['request_hash'] and fresh['binding_hash']=='b'*64


@pytest.mark.asyncio
async def test_bearer_task_expiry_and_revocation_reject_before_reserving(db,monkeypatch,legacy_media):
    import platform_service.media.core as core
    from datetime import timedelta
    service,ledger,scope,ctx,*_=await legacy_media.setup(db)
    minted=await service.mint(scope,model='fixture-image',capability='image.generate',invocation_key='expiry',mode='test',bindings=BINDINGS)
    actual_now=core.now()
    with monkeypatch.context() as patch:
        patch.setattr(core,'now',lambda:actual_now+timedelta(minutes=6))
        with pytest.raises(PlatformError) as error:await service.authenticate_task(minted['task_token'])
        assert error.value.code=='task_token_invalid'
    async with db.connection() as c:await c.execute("UPDATE identity_sessions SET revoked_at=clock_timestamp() WHERE id='session'")
    with pytest.raises(PlatformError):await admit(service,ctx)
    async with db.connection() as c:assert (await (await c.execute('SELECT count(*) n FROM credit_reservations')).fetchone())['n']==0


@pytest.mark.asyncio
async def test_expired_input_is_pinned_until_queued_job_is_released(db,legacy_media):
    import psycopg
    d=definition('image.edit')
    service,ledger,scope,ctx,*_=await legacy_media.setup(db,media_definition=d)
    async with legacy_media.policy() as c:
        await c.execute('UPDATE media_storage_policy SET ttl_seconds=1 WHERE id=1')
    asset=await service.register_asset(ctx,asset_id='short-asset',storage_key='3'*32,sha256='2'*64,size=10,mime='image/png',properties={'width':1,'height':1,'format':'png'})
    request={**REQUEST,'capability':'image.edit','assets':[asset['id']]}
    verified={asset['id']:{k:asset[k] for k in ('sha256','size','mime','properties')}}
    job=await service.admit(ctx,key='pinned',request=request,bindings=BINDINGS,verified_inputs=verified)
    await asyncio.sleep(1.05)
    async with legacy_media.generation.use('gc',asset['storage_key'],'pinned-input-probe') as permit:
        with pytest.raises(psycopg.Error) as error:
            async with legacy_media.sql(permit) as c:await c.execute("UPDATE media_assets SET purged_at=clock_timestamp() WHERE id='short-asset'")
        assert 'media maintenance permit required' not in str(error.value)
    await service.cancel(scope,job['id'])
    assert asset['storage_key'] in await service.raw.prepare_gc(permit=permit)
    async with db.connection() as c:
        assert (await (await c.execute("SELECT purged_at FROM media_assets WHERE id='short-asset'")).fetchone())['purged_at'] is not None
