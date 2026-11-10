"""Media tokens and immutable evidence cannot cross identities or capabilities."""
from dataclasses import replace
import pytest
from b2_legacy_media_fixtures import legacy_media, legacy_db as db, legacy_dsn as test_dsn
from platform_service.errors import PlatformError
from platform_service.media_maintenance.contracts import MaintenanceError
from test_media_credits import setup, admit, dispatch, artifact, finish, BINDINGS, OPERATOR


@pytest.mark.asyncio
async def test_closed_catalog_mode_binding_capability_and_operator_required(db,legacy_media):
    service,ledger,scope,ctx,*_=await legacy_media.setup(db)
    await service.set_enabled('fixture-image',False,OPERATOR)
    assert (await service.catalog(scope,mode='test',bindings=BINDINGS))['models']==[]
    for mode,capability,bindings in [('live','image.generate',BINDINGS),('test','video.generate',BINDINGS),('test','image.generate',{})]:
        with pytest.raises(PlatformError):await service.mint(scope,model='fixture-image',capability=capability,invocation_key='wrong',mode=mode,bindings=bindings)
    with pytest.raises(PlatformError):await service.set_enabled('fixture-image',True,'untrusted-operator')


@pytest.mark.asyncio
async def test_media_scope_revocation_and_foreign_owner_are_checked_before_money(db,legacy_media):
    service,ledger,scope,ctx,*_=await legacy_media.setup(db)
    before=await ledger.summary(scope)
    for patch in [{'scope':replace(scope,user_id='other')},{'capability':'video.generate'},{'funding_mode':'live'}]:
        with pytest.raises(PlatformError):await admit(service,{**ctx,**patch})
    assert await ledger.summary(scope)==before
    job=await admit(service,ctx)
    with pytest.raises(PlatformError):await service.get(replace(scope,product_id='second'),job['id'])
    async with db.connection() as c:await c.execute("UPDATE identity_sessions SET revoked_at=clock_timestamp() WHERE id='session'")
    with pytest.raises(PlatformError):await service.dispatch(job['id'],job['attempt_id'])


@pytest.mark.asyncio
async def test_unknown_cannot_forge_trusted_delivery_evidence(db,legacy_media):
    service,ledger,scope,ctx,*_=await legacy_media.setup(db)
    job=await admit(service,ctx);await dispatch(service,job)
    # No verified artifact exists, even though an internal caller supplies count.
    with pytest.raises(PlatformError):await finish(service,job)
    assert (await ledger.summary(scope))['reserved']=='6'
    await artifact(service,job)
    with pytest.raises(MaintenanceError,match='invalid_backup'):await service.record_terminal_evidence(job['id'],'foreign-attempt',terminal_status='completed',provider_response_id='response-'+job['id'],quantities={'image':1},basis_evidence={'verified_delivery_v1':'d'*64},evidence_hash='e'*64)


@pytest.mark.asyncio
async def test_runtime_permissions_cover_media_without_model_publish_or_history_mutation(db,legacy_media):
    from platform_service.permissions import INSERT_TABLES, IMMUTABLE_TABLES, SELECT_TABLES, UPDATE_COLUMNS
    for name in ['media_jobs','media_provider_attempts','media_provider_associations','media_terminal_evidence','media_receipts','media_assets','media_artifacts']:
        assert name in INSERT_TABLES and name in SELECT_TABLES
    assert 'media_models' not in INSERT_TABLES and 'media_model_states' not in INSERT_TABLES
    assert 'media_models' in IMMUTABLE_TABLES and 'media_terminal_evidence' in IMMUTABLE_TABLES
    assert 'amount' not in UPDATE_COLUMNS['media_jobs'] and 'request' not in UPDATE_COLUMNS['media_jobs']


@pytest.mark.asyncio
async def test_real_nonowner_role_executes_media_but_cannot_publish_or_mutate_evidence(db,test_dsn,legacy_media):
    from uuid import uuid4
    import secrets
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from platform_service.db import Database
    from platform_service.identity import IdentityService
    from platform_service.media.core import MediaService
    from platform_service.permissions import grant_runtime_permissions,assert_safe_runtime_role
    service,ledger,scope,ctx,*_=await legacy_media.setup(db)
    role='media_runtime_'+uuid4().hex;password=secrets.token_urlsafe(24)
    await legacy_media.create_runtime_role(role,password)
    runtime=Database(make_conninfo(test_dsn,user=role,password=password),schema=db.schema)
    try:
        await runtime.open();await runtime.require_runtime_role()
        low=legacy_media.client(MediaService(runtime,IdentityService(runtime,None)))
        job=await admit(low,ctx);await dispatch(low,job);await artifact(low,job);await finish(low,job)
        assert (await ledger.summary(scope))['spent']=='2'
        for command in ["UPDATE media_terminal_evidence SET evidence_hash=repeat('0',64)",
                        "UPDATE media_jobs SET amount=1","DELETE FROM media_jobs",'TRUNCATE media_receipts',
                        "INSERT INTO media_models(id,definition,operator_id) VALUES ('forged','{}','evil')",
                        "UPDATE media_model_states SET enabled=false"]:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                async with runtime.connection() as c:await c.execute(command)
        async with db.connection() as c:
            await c.execute(sql.SQL('GRANT INSERT (definition) ON media_models TO {}').format(sql.Identifier(role)))
        with pytest.raises(PlatformError):
            async with db.connection() as c:await assert_safe_runtime_role(c,role)
    finally:
        await runtime.close()
        await legacy_media.drop_runtime_role(role)


@pytest.mark.asyncio
async def test_cross_product_asset_and_artifact_ids_never_authorize_access(db,legacy_media):
    service,ledger,scope,ctx,*_=await legacy_media.setup(db)
    asset=await service.register_asset(ctx,storage_key='1'*32,sha256='2'*64,size=10,mime='image/png',properties={'width':1,'height':1,'format':'png'})
    foreign={**ctx,'scope':replace(scope,product_id='second')}
    with pytest.raises(PlatformError):await service.get_asset(foreign,asset['id'])
    job=await admit(service,ctx);await dispatch(service,job);output=await artifact(service,job)
    for changed in [replace(scope,product_id='second'),replace(scope,user_id='other'),replace(scope,billing_account_id='foreign-wallet',tenant_id='foreign')]:
        with pytest.raises(PlatformError):await service.get_artifact(changed,output['id'])
    with pytest.raises(PlatformError):
        await service.admit(foreign,key='stolen',request={'model':'fixture-image','capability':'image.generate','prompt':'x'},bindings=BINDINGS)
    assert (await ledger.summary(scope))['reserved']=='6'


@pytest.mark.asyncio
async def test_response_id_reuse_and_unassociated_terminal_are_rejected(db,legacy_media):
    service,ledger,scope,ctx,*_=await legacy_media.setup(db)
    first=await admit(service,ctx,'first');await dispatch(service,first)
    second=await admit(service,ctx,'second')
    await service.record_provider_attempt(second['id'],second['attempt_id'],provider_request_hash='b'*64,provider_scope_hash='c'*64,upstream_model='fixture-upstream',request_id='request-second')
    await service.dispatch(second['id'],second['attempt_id'])
    await service.record_provider_association(second['id'],second['attempt_id'],http_status=200)
    with pytest.raises(PlatformError):
        await service.record_provider_association(second['id'],second['attempt_id'],provider_response_id='response-'+first['id'])
    await artifact(service,second)
    with pytest.raises(PlatformError):
        await service.record_terminal_evidence(second['id'],second['attempt_id'],terminal_status='completed',provider_response_id='response-'+first['id'],quantities={'image':1},basis_evidence={'verified_delivery_v1':'d'*64},evidence_hash='e'*64)
    assert (await ledger.summary(scope))['reserved']=='12'


@pytest.mark.asyncio
async def test_capability_artifacts_cannot_substitute_another_media_type(db,legacy_media):
    service,ledger,scope,ctx,*_=await legacy_media.setup(db)
    job=await admit(service,ctx);await dispatch(service,job)
    with pytest.raises(PlatformError):
        await service.register_artifact(job['id'],job['attempt_id'],storage_key='1'*32,sha256='2'*64,size=10,mime='audio/wav',properties={'duration_ms':1000,'format':'wav'})
    assert (await service.get(scope,job['id']))['artifacts']==[]


@pytest.mark.asyncio
async def test_exact_asset_and_artifact_metadata_replay_is_idempotent(db,legacy_media):
    service,ledger,scope,ctx,*_=await legacy_media.setup(db)
    asset_values=dict(asset_id='owned-asset',storage_key='1'*32,sha256='2'*64,size=10,mime='image/png',properties={'width':1,'height':1,'format':'png'})
    first=await service.register_asset(ctx,**asset_values)
    assert (await service.register_asset(ctx,**asset_values))['id']==first['id']
    with pytest.raises(PlatformError):await service.register_asset(ctx,**{**asset_values,'sha256':'3'*64})
    job=await admit(service,ctx);await dispatch(service,job)
    values=dict(artifact_id='owned-artifact',storage_key='4'*32,sha256='5'*64,size=10,mime='image/png',properties={'width':1,'height':1,'format':'png'})
    output=await service.register_artifact(job['id'],job['attempt_id'],**values)
    await finish(service,job)
    assert (await service.register_artifact(job['id'],job['attempt_id'],**values))['id']==output['id']
    with pytest.raises(PlatformError):await service.register_artifact(job['id'],job['attempt_id'],**{**values,'size':20})


@pytest.mark.asyncio
async def test_voice_registration_is_real_owned_delivery_and_private_provider_id(db,legacy_media):
    from test_media_contracts import definition
    from test_media_credits import REQUEST
    d=definition('voice.enroll')
    d['rate']={'version':'voice-1','meters':[{'unit':'registration','basis':'verified_delivery_v1','numerator':'3','denominator':'2'}]}
    d['limits']['max_units']={'registration':1}
    service,ledger,scope,ctx,*_=await legacy_media.setup(db,media_definition=d)
    asset=await service.register_asset(ctx,storage_key='1'*32,sha256='2'*64,size=10,mime='audio/wav',properties={'duration_ms':1000,'format':'wav'})
    verified={asset['id']:{k:asset[k] for k in ('sha256','size','mime','properties')}}
    job=await service.admit(ctx,key='voice',request={**REQUEST,'capability':'voice.enroll','prompt':'','assets':[asset['id']]},bindings=BINDINGS,verified_inputs=verified)
    await dispatch(service,job)
    values=dict(terminal_status='completed',provider_response_id='response-'+job['id'],quantities={'registration':1},basis_evidence={'verified_delivery_v1':'d'*64},evidence_hash='e'*64)
    with pytest.raises(PlatformError):await service.record_terminal_evidence(job['id'],job['attempt_id'],**values)
    voice=await service.register_voice(job['id'],job['attempt_id'],voice_id='owned-voice',provider_voice_id='private-provider-voice',properties={'name':'我的音色'})
    assert (await service.register_voice(job['id'],job['attempt_id'],voice_id='owned-voice',provider_voice_id='private-provider-voice',properties={'name':'我的音色'}))['id']==voice['id']
    await service.finish(job['id'],job['attempt_id'],**values)
    result=await service.get(scope,job['id'])
    assert result['delivery_state']=='available' and result['voice']['id']=='owned-voice'
    assert 'provider_voice_id' not in result['voice'] and (await ledger.summary(scope))['spent']=='2'
    assert (await service.get_voice(scope,'owned-voice'))['provider_voice_id']=='private-provider-voice'
    with pytest.raises(PlatformError):await service.get_voice(replace(scope,product_id='second'),'owned-voice')
