"""Append-only image entitlement on an existing internal grant, without funds."""
import asyncio
from dataclasses import replace
from datetime import timedelta
import importlib.util
import secrets
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
from psycopg.conninfo import make_conninfo
import pytest

from platform_service.contracts import OperatorIdentity
from platform_service.errors import PlatformError
from platform_service.credits import active_plans, CreditLedger
from platform_service.membership import MembershipService
from test_internal_grants import internal, issue

OPERATOR=OperatorIdentity('feature-operator',frozenset({'internal:extend_features'}))


async def snapshot(db):
    async with db.connection() as c:
        return {table:await (await c.execute(sql.SQL('SELECT * FROM {} ORDER BY 1').format(sql.Identifier(table)))).fetchall()
                for table in ['plans','internal_grants','memberships','credit_lots','credit_journals','credit_entries']}


async def local_maintenance(db, root):
    """Media mutations run under a maintenance permit; local mode is the production default."""
    from platform_service.media_maintenance.runtime import RuntimeGeneration
    generation = RuntimeGeneration.local(db, storage_root=root)
    await generation.start()
    return generation


def feature_service(db):
    assert importlib.util.find_spec('platform_service.internal_features') is not None
    from platform_service.internal_features import InternalFeatureService
    return InternalFeatureService(db)


async def extend(service,internal,grant,**patch):
    return await service.extend(OPERATOR,**(dict(environment=internal[3]['environment_id'],grant_id=grant['id'],
        product='easel',feature='image',key='approved-image-once',reason='User approved existing experience image access')|patch))


@pytest.mark.asyncio
async def test_extension_is_audited_idempotent_and_preserves_all_old_funds_plan_expiry_and_concurrency(internal,db):
    grant=await issue(internal);scope=internal[2];service=feature_service(db)
    before=await snapshot(db);wallet=await CreditLedger(db).summary(scope)
    first=await extend(service,internal,grant)
    assert await extend(service,internal,grant)==first
    assert first['grant_id']==grant['id'] and first['product_id']=='easel' and first['feature']=='image'
    assert first['operator_id']==OPERATOR.id and first['reason']=='User approved existing experience image access'
    assert first['expires_at']==grant['expires_at'] and first['starts_at']==grant['starts_at']
    assert await snapshot(db)==before and await CreditLedger(db).summary(scope)==wallet
    entitlement=await MembershipService(db).entitlements(scope)
    assert entitlement['features']==['image','text'] and entitlement['concurrency']==1
    assert entitlement['experience_active'] and len(entitlement['memberships'])==1
    from platform_service.internal_features import feature_memberships
    async with db.connection() as c:
        plans=await active_plans(c,scope,'live')
        selected=await feature_memberships(c,scope,'live','image')
        assert selected==plans and selected[0]['definition']['features']==['text']
        assert await feature_memberships(c,scope,'test','image')==[]
        assert await feature_memberships(c,replace(scope,user_id='other'),'live','image')==[]
        assert await feature_memberships(c,replace(scope,product_id='second'),'live','image')==[]
        assert (await (await c.execute('SELECT count(*) n FROM internal_feature_extensions')).fetchone())['n']==1


@pytest.mark.asyncio
async def test_extension_requires_owner_capability_exact_grant_environment_feature_and_source_key(internal,db):
    grant=await issue(internal);service=feature_service(db)
    for patch in [{'environment':'different-env'},{'grant_id':'missing'},{'product':'second'},{'feature':'video'}]:
        with pytest.raises(PlatformError):await extend(service,internal,grant,**patch)
    with pytest.raises(PlatformError):
        await service.extend(OperatorIdentity('unprivileged',frozenset()),environment=internal[3]['environment_id'],grant_id=grant['id'],product='easel',feature='image',key='x',reason='approved')
    first=await extend(service,internal,grant)
    for patch in [{'key':'new-key'},{'reason':'different-reason'}]:
        with pytest.raises(PlatformError):await extend(service,internal,grant,**patch)
    assert await extend(service,internal,grant)==first


@pytest.mark.asyncio
async def test_concurrent_feature_extension_has_one_history_row_without_new_financial_rows(internal,db):
    grant=await issue(internal);service=feature_service(db);before=await snapshot(db)
    outcomes=await asyncio.gather(*(extend(service,internal,grant) for _ in range(6)))
    assert all(row==outcomes[0] for row in outcomes)
    assert await snapshot(db)==before


@pytest.mark.asyncio
async def test_database_rejects_rebound_owner_expiry_product_and_mutable_feature_history(internal,db):
    grant=await issue(internal);service=feature_service(db)
    row=await extend(service,internal,grant)
    async with db.connection() as c:stored=await (await c.execute('SELECT * FROM internal_feature_extensions')).fetchone()
    for patch in [{'user_id':'other'},{'billing_account_id':'other-wallet'},{'tenant_id':'other-t'},
                  {'environment_id':'other-environment'},{'expires_at':stored['expires_at']+timedelta(days=1)},
                  {'starts_at':stored['starts_at']+timedelta(seconds=1)},{'product_id':'second'},{'feature':'video'}]:
        values=stored|{'id':'candidate-'+str(len(str(patch))),'issuance_key':'candidate-'+str(len(str(patch)))}|patch
        with pytest.raises(psycopg.Error):
            async with db.connection() as c:
                await c.execute('INSERT INTO internal_feature_extensions(id,grant_id,environment_id,user_id,tenant_id,billing_account_id,product_id,feature,starts_at,expires_at,operator_id,issuance_key,reason) VALUES (%(id)s,%(grant_id)s,%(environment_id)s,%(user_id)s,%(tenant_id)s,%(billing_account_id)s,%(product_id)s,%(feature)s,%(starts_at)s,%(expires_at)s,%(operator_id)s,%(issuance_key)s,%(reason)s)',values)
    for command in ['UPDATE internal_feature_extensions SET reason=\'changed\'','DELETE FROM internal_feature_extensions','TRUNCATE internal_feature_extensions']:
        with pytest.raises(psycopg.Error):
            async with db.connection() as c:await c.execute(command)


@pytest.mark.asyncio
async def test_inactive_owner_blocks_product_entitlements_without_rewriting_feature_history(internal,db):
    grant=await issue(internal);scope=internal[2];service=feature_service(db)
    await extend(service,internal,grant)
    async with db.connection() as c:
        await c.execute("UPDATE users SET active=false WHERE id=%s",(scope.user_id,))
    with pytest.raises(PlatformError):await MembershipService(db).entitlements(scope)
    from platform_service.internal_features import feature_memberships
    async with db.connection() as c:
        assert await feature_memberships(c,scope,'live','image',memberships=[])==[]
        assert (await (await c.execute('SELECT count(*) n FROM internal_feature_extensions')).fetchone())['n']==1


@pytest.mark.asyncio
async def test_extension_uses_real_image_feature_gate_on_original_wallet_and_no_new_grant(internal,db,tmp_path):
    from platform_service.media.core import MediaService
    from platform_service.identity import IdentityService
    from test_media_contracts import definition
    grant=await issue(internal);scope=internal[2]
    media=MediaService(db,IdentityService(db,None))
    spec=definition();spec.update(feature='image',test_only=False,verification='live',verification_ref='synthetic-local-protocol-evidence')
    spec['rate']={'version':'approved-internal-image-20261027-v1','meters':[{'unit':'image','basis':'verified_delivery_v1','numerator':'1000000','denominator':'1'}]}
    spec['limits']['max_units']={'image':1}
    binding={'fixture-image':{'provider_id':'fixture','provider_version':'1','adapter':'openai_images_v1','protocol_version':'1','binding_hash':'a'*64}}
    operator=OperatorIdentity('publisher',frozenset({'media:publish'}))
    maintenance=await local_maintenance(db,tmp_path);media.maintenance=maintenance
    async with maintenance.use('catalog',spec['id'],transient=True):
        await media.publish(spec,operator);await media.set_enabled('fixture-image',True,operator)
    with pytest.raises(PlatformError) as caught:
        async with maintenance.use('mint',str(uuid4()),transient=True):
            await media.mint(scope,model='fixture-image',capability='image.generate',invocation_key='before-extension',mode='live',bindings=binding)
    assert caught.value.code=='media_entitlement_required'
    await extend(feature_service(db),internal,grant)
    async with maintenance.use('mint',str(uuid4()),transient=True):
        token=await media.mint(scope,model='fixture-image',capability='image.generate',invocation_key='after-extension',mode='live',bindings=binding)
    assert token['model']=='fixture-image'
    assert (await CreditLedger(db).summary(scope))['experience_issued']=='10000000'


def test_runtime_has_select_only_feature_authority_and_preserves_text_rules():
    from platform_service.permissions import INSERT_TABLES, UPDATE_COLUMNS, SELECT_TABLES, IMMUTABLE_TABLES
    assert 'internal_feature_extensions' in SELECT_TABLES and 'internal_feature_extensions' in IMMUTABLE_TABLES
    assert 'internal_feature_extensions' not in INSERT_TABLES and 'internal_feature_extensions' not in UPDATE_COLUMNS


@pytest.mark.asyncio
async def test_live_image_cannot_claim_text_feature_even_as_legacy_misconfigured_catalog(internal,db,tmp_path):
    from platform_service.media.core import MediaService
    from platform_service.media.contracts import validate_definition
    from platform_service.identity import IdentityService
    from test_media_contracts import definition
    await issue(internal);scope=internal[2]
    bad=definition();bad.update(feature='text',test_only=False,verification='live')
    rejected=False
    try:validate_definition(bad)
    except PlatformError:rejected=True
    assert rejected, 'a live image model must require the image feature'
    async with db.connection() as c:
        await c.execute("INSERT INTO media_models(id,definition,operator_id) VALUES ('fixture-image',%s,'legacy-owner')",(Jsonb(bad),))
        await c.execute("INSERT INTO media_model_states(model_id,enabled,operator_id) VALUES ('fixture-image',true,'legacy-owner')")
    media=MediaService(db,IdentityService(db,None))
    bindings={'fixture-image':{'provider_id':'fixture','provider_version':'1','adapter':'openai_images_v1','protocol_version':'1','binding_hash':'a'*64}}
    assert await media.catalog(scope,mode='live',bindings=bindings)=={'models':[]}
    maintenance=await local_maintenance(db,tmp_path);media.maintenance=maintenance
    with pytest.raises(PlatformError):
        async with maintenance.use('mint',str(uuid4()),transient=True):
            await media.mint(scope,model='fixture-image',capability='image.generate',invocation_key='no-text-bypass',mode='live',bindings=bindings)
    assert (await CreditLedger(db).summary(scope))['reserved']=='0'


@pytest.mark.asyncio
async def test_actual_nonowner_runtime_can_read_image_right_but_cannot_extend_or_write_audit(internal,db,test_dsn):
    from platform_service.db import Database
    from platform_service.permissions import grant_runtime_permissions
    grant=await issue(internal);scope=internal[2]
    await extend(feature_service(db),internal,grant)
    role='image_runtime_'+uuid4().hex
    password=secrets.token_urlsafe(24)
    async with db.connection() as c:
        await c.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(role),sql.Literal(password)))
        await grant_runtime_permissions(c,role)
    runtime=Database(make_conninfo(test_dsn,user=role,password=password),schema=db.schema)
    try:
        await runtime.open();await runtime.require_runtime_role()
        rights=await MembershipService(runtime).entitlements(scope)
        assert rights['features']==['image','text'] and rights['concurrency']==1
        with pytest.raises(PlatformError) as caught:
            await extend(feature_service(runtime),internal,grant)
        assert caught.value.code=='internal_owner_required'
        for command in [
            'INSERT INTO internal_feature_extensions SELECT * FROM internal_feature_extensions',
            "UPDATE internal_feature_extensions SET reason='changed'",'DELETE FROM internal_feature_extensions',
            'TRUNCATE internal_feature_extensions','ALTER TABLE internal_feature_extensions DISABLE TRIGGER ALL',
        ]:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                async with runtime.connection() as c:await c.execute(command)
    finally:
        await runtime.close()
        async with db.connection() as c:
            await c.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
            await c.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


@pytest.mark.asyncio
@pytest.mark.parametrize('privilege',['INSERT','INSERT (id)'])
async def test_runtime_startup_rejects_accidental_feature_table_or_column_insert_and_reprovision_removes_it(db,privilege):
    from platform_service.permissions import assert_safe_runtime_role,grant_runtime_permissions
    role='feature_grant_'+uuid4().hex
    async with db.connection() as c:
        await c.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
    try:
        async with db.connection() as c:
            await grant_runtime_permissions(c,role)
            await c.execute(sql.SQL('GRANT '+privilege+' ON internal_feature_extensions TO {}').format(sql.Identifier(role)))
        with pytest.raises(PlatformError) as caught:
            async with db.connection() as c:await assert_safe_runtime_role(c,role)
        assert caught.value.code=='unsafe_runtime_role'
        async with db.connection() as c:
            await grant_runtime_permissions(c,role)
            permissions=await (await c.execute("SELECT has_any_column_privilege(%s,'internal_feature_extensions','INSERT') unsafe",(role,))).fetchone()
            assert permissions['unsafe'] is False
            await assert_safe_runtime_role(c,role)
    finally:
        async with db.connection() as c:
            await c.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
            await c.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
