"""Storage admission and recovery use owned PostgreSQL schemas and exact blobs."""
import asyncio
import hashlib
import io
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from b2_legacy_media_fixtures import legacy_media, legacy_db as db, legacy_dsn as test_dsn, legacy_bare_db as bare_db, legacy_014
from PIL import Image

from platform_service.errors import PlatformError
from platform_service.media_maintenance.contracts import MaintenanceError
from test_media_contracts import definition
from test_media_credits import BINDINGS, setup, admit, dispatch


def png():
    output = io.BytesIO()
    Image.new("RGB", (3, 2), (17, 39, 61)).save(output, format="PNG")
    return output.getvalue()


def metadata(*, size=10, storage_key=None):
    return {"storage_key": storage_key or uuid4().hex + uuid4().hex,
            "sha256": hashlib.sha256(b"synthetic" + str(size).encode()).hexdigest(),
            "size": size, "mime": "image/png", "properties": {"width": 3, "height": 2, "format": "PNG"}}


async def policy(db,legacy_media, **values):
    assert set(values) <= {"owner_max_bytes", "owner_max_files", "global_max_bytes", "global_max_files", "ttl_seconds"}
    async with legacy_media.policy() as c:
        await c.execute("UPDATE media_storage_policy SET " + ",".join(key + "=%s" for key in values) + " WHERE id=1", tuple(values.values()))


async def context(service, scope, key):
    token = await service.mint(scope, model="fixture-image", capability="image.generate", invocation_key=key, mode="test", bindings=BINDINGS)
    return await service.authenticate_task(token["task_token"])


def test_storage_lifecycle_contract_exists():
    from platform_service.media.core import MediaService
    assert callable(getattr(MediaService, "stage_blob", None))


@pytest.mark.asyncio
async def test_stage_replay_is_exact_and_does_not_double_count_storage(db,legacy_media):
    service, ledger, scope, ctx, *_ = await legacy_media.setup(db)
    item = metadata()
    staged = await service.stage_blob(ctx, item)
    repeated = await service.stage_blob(ctx, item)
    assert staged["storage_key"] == repeated["storage_key"] == item["storage_key"]
    with pytest.raises(PlatformError):
        await service.stage_blob(ctx, {**item, "sha256": "f" * 64})
    async with db.connection() as c:
        row = await (await c.execute("SELECT count(*) n,sum(size) size FROM media_blobs")).fetchone()
        assert row["n"] == 1 and row["size"] == 10
    assert (await ledger.summary(scope))["reserved"] == "0"


@pytest.mark.asyncio
async def test_owner_quota_crosses_invocations_and_concurrent_registration_has_one_winner(db,legacy_media):
    service, ledger, scope, ctx, *_ = await legacy_media.setup(db)
    other = await context(service, scope, "second-invocation")
    await policy(db,legacy_media, owner_max_bytes=10, owner_max_files=1, global_max_bytes=1000, global_max_files=100)
    results = await asyncio.gather(service.stage_blob(ctx, metadata()), service.stage_blob(other, metadata()), return_exceptions=True)
    assert sum(isinstance(value, dict) for value in results) == 1
    errors = [value for value in results if isinstance(value, BaseException)]
    assert len(errors) == 1 and isinstance(errors[0], PlatformError)
    async with db.connection() as c:
        row = await (await c.execute("SELECT count(*) n,sum(size) size FROM media_blobs WHERE state!='deleted'")).fetchone()
        assert row["n"] == 1 and row["size"] == 10
    assert (await ledger.summary(scope))["reserved"] == "0"


@pytest.mark.asyncio
async def test_stage_attach_counts_once_and_exact_registration_replays(db,legacy_media):
    service, ledger, scope, ctx, *_ = await legacy_media.setup(db)
    await policy(db,legacy_media, owner_max_bytes=10, owner_max_files=1, global_max_bytes=1000, global_max_files=100)
    item = metadata()
    await service.stage_blob(ctx, item)
    attached = await service.register_asset(ctx, **item)
    assert (await service.register_asset(ctx, id=attached["id"], **item))["id"] == attached["id"]
    async with db.connection() as c:
        row = await (await c.execute("SELECT state,attached_id FROM media_blobs WHERE storage_key=%s", (item["storage_key"],))).fetchone()
        assert row["state"] == "attached" and row["attached_id"] == attached["id"]
    with pytest.raises(PlatformError): await service.stage_blob(ctx, metadata())


@pytest.mark.asyncio
async def test_gc_is_durable_before_unlink_and_quota_released_only_after_ack(db,legacy_media):
    service, ledger, scope, ctx, *_ = await legacy_media.setup(db)
    await policy(db,legacy_media, owner_max_bytes=10, owner_max_files=1, global_max_bytes=1000, global_max_files=100, ttl_seconds=1)
    item = metadata()
    await service.stage_blob(ctx, item)
    await service.register_asset(ctx,**item)
    await asyncio.sleep(1.05)
    await service.prepare_gc()
    async with db.connection() as c:
        row = await (await c.execute("SELECT state FROM media_blobs WHERE storage_key=%s", (item["storage_key"],))).fetchone()
        assert row["state"] == "gc_pending"
    with pytest.raises(PlatformError): await service.stage_blob(ctx, metadata())
    claims = await service.claim_gc()
    assert len(claims) == 1 and claims[0]["storage_key"] == item["storage_key"]
    assert not await service.claim_gc()
    from platform_service.media_maintenance.contracts import MaintenanceError
    permit=legacy_media.generation.owned('gc',claims[0]['storage_key'],claims[0]['gc_token'])
    with pytest.raises(MaintenanceError,match='invalid_backup'):
        await service.raw.finish_gc(item['storage_key'],'wrong-lease-token',permit=permit)
    async with db.connection() as c:
        row = await (await c.execute("SELECT state FROM media_blobs WHERE storage_key=%s", (item["storage_key"],))).fetchone()
        assert row["state"] == "gc_pending"
    with pytest.raises(PlatformError): await service.stage_blob(ctx, metadata())
    permit=legacy_media.generation.owned('gc',claims[0]['storage_key'],claims[0]['gc_token'])
    await legacy_media.delete_owned(service,__import__('platform_service.media.artifacts',fromlist=['ArtifactStore']).ArtifactStore(legacy_media.source_path),claims[0],permit)
    await service.raw.finish_gc(item['storage_key'],claims[0]['gc_token'],permit=permit)
    await service.raw.finish_gc(item['storage_key'],claims[0]['gc_token'],permit=permit)
    await service.stage_blob(ctx, metadata())
    async with db.connection() as c:
        rows = await (await c.execute("SELECT state,count(*) n FROM media_blobs GROUP BY state")).fetchall()
        assert {row["state"]: row["n"] for row in rows} == {"deleted": 1, "staged": 1}
    assert (await ledger.summary(scope))["spent"] == "0"


async def owned_scope(db, original, *, foreign=False):
    from platform_service.contracts import OperatorIdentity
    from platform_service.membership import MembershipService
    product = "easel" if foreign else "second"
    user, tenant, wallet, subject = ("other", "foreign", "foreign-wallet", "foreign-subject") if foreign else ("u", "t", "wallet", "subject")
    session = "storage-" + uuid4().hex
    async with db.connection() as c:
        await c.execute("INSERT INTO product_clients(product_id,client_id) VALUES (%s,'desktop') ON CONFLICT DO NOTHING", (product,))
        if foreign:
            await c.execute("INSERT INTO external_identities(issuer,subject,user_id) VALUES ('fixture',%s,%s)", (subject, user))
        await c.execute("INSERT INTO identity_sessions(id,user_id,tenant_id,billing_account_id,product_id,client_id,authorization_epoch,issuer,subject,expires_at) VALUES (%s,%s,%s,%s,%s,'desktop',0,'fixture',%s,now()+interval '1 hour')", (session, user, tenant, wallet, product, subject))
    if foreign:
        await MembershipService(db).issue_test(OperatorIdentity("storage-fixture", frozenset({"test:issue"})), wallet, "media-plan", "foreign-storage")
    return replace(original, user_id=user, product_id=product, tenant_id=tenant, billing_account_id=wallet, session_id=session)


@pytest.mark.asyncio
async def test_same_owner_quota_is_shared_across_products_not_just_invocations(db,legacy_media):
    service, ledger, scope, ctx, *_ = await legacy_media.setup(db)
    second_scope = await owned_scope(db, scope)
    second = await context(service, second_scope, "second-product")
    await policy(db,legacy_media, owner_max_bytes=10, owner_max_files=1, global_max_bytes=1000, global_max_files=100)
    await service.stage_blob(ctx, metadata())
    with pytest.raises(PlatformError): await service.stage_blob(second, metadata())
    assert (await ledger.summary(scope))["reserved"] == "0"


@pytest.mark.asyncio
@pytest.mark.parametrize("global_limit,foreign_allowed", [(10, False), (20, True)])
async def test_foreign_owner_has_separate_quota_but_shares_global_limit(db, global_limit, foreign_allowed,legacy_media):
    service, ledger, scope, ctx, *_ = await legacy_media.setup(db)
    foreign_scope = await owned_scope(db, scope, foreign=True)
    foreign = await context(service, foreign_scope, "foreign-product")
    await policy(db,legacy_media, owner_max_bytes=10, owner_max_files=1, global_max_bytes=global_limit, global_max_files=2)
    await service.stage_blob(ctx, metadata())
    if foreign_allowed:
        await service.stage_blob(foreign, metadata())
    else:
        with pytest.raises(PlatformError): await service.stage_blob(foreign, metadata())
    async with db.connection() as c:
        result = await (await c.execute("SELECT user_id,sum(size) size FROM media_blobs WHERE state!='deleted' GROUP BY user_id")).fetchall()
        assert {row["user_id"]: row["size"] for row in result} == ({"u": 10, "other": 10} if foreign_allowed else {"u": 10})


async def runtime_fixture(db,legacy_media, service, scope, tmp_path, handler, capability="image.generate"):
    from platform_service.media_config import MediaSettings, MediaProvider
    from platform_service.media_runtime import MediaRuntime
    settings = MediaSettings(mode="test", storage_root=legacy_media.source_path, providers={"fixture": MediaProvider("1", "http://127.0.0.1:9999/v1", "private-fixture", {"fixture-image": "upstream"})})
    minted = await service.mint(scope, model="fixture-image", capability=capability, invocation_key="storage-runtime", mode="test", bindings=settings.bindings)
    runtime = MediaRuntime(db, service.identity, settings, service=service.raw, maintenance=legacy_media.generation, transport=httpx.MockTransport(handler))
    await legacy_media.start_runtime(runtime)
    return runtime, minted


@pytest.mark.asyncio
async def test_upload_full_owner_quota_rejects_before_blob_write_or_financial_change(db, tmp_path,legacy_media):
    service, ledger, scope, ctx, *_ = await legacy_media.setup(db)
    await policy(db,legacy_media, owner_max_bytes=len(png())-1, owner_max_files=1, global_max_bytes=1000, global_max_files=10)
    seen = []
    runtime, token = await runtime_fixture(db,legacy_media, service, scope, tmp_path, lambda request: seen.append(request))
    async def chunks(): yield png()
    try:
        with pytest.raises(PlatformError): await runtime.upload_asset(token["task_token"], chunks(), mime="image/png")
        assert not runtime.store.root.exists() or not list(runtime.store.root.iterdir())
        assert not seen and (await ledger.summary(scope))["reserved"] == "0"
    finally: await runtime.close()


@pytest.mark.asyncio
async def test_full_storage_blocks_paid_job_before_wallet_reserve_and_provider_post(db, tmp_path,legacy_media):
    configured = definition(); configured["limits"]["max_artifact_bytes"] = 20
    service, ledger, scope, ctx, *_ = await legacy_media.setup(db, media_definition=configured)
    await policy(db,legacy_media, owner_max_bytes=100, owner_max_files=10, global_max_bytes=1000, global_max_files=100)
    await service.stage_blob(ctx, metadata(size=100))
    seen = []
    runtime, token = await runtime_fixture(db,legacy_media, service, scope, tmp_path, lambda request: seen.append(request))
    try:
        request = {"model": "fixture-image", "capability": "image.generate", "prompt": "Controlled storage admission", "assets": [], "parameters": {"n": 1}}
        with pytest.raises(PlatformError): await runtime.submit(token["task_token"], "storage-full", request)
        assert not seen and (await ledger.summary(scope))["reserved"] == "0"
        async with db.connection() as c:
            assert (await (await c.execute("SELECT count(*) n FROM media_jobs")).fetchone())["n"] == 0
    finally: await runtime.close()


@pytest.mark.asyncio
async def test_concurrent_paid_jobs_reserve_output_budget_before_either_post(db, tmp_path,legacy_media):
    import base64
    configured = definition(); configured["limits"]["max_artifact_bytes"] = 100
    service, ledger, scope, ctx, *_ = await legacy_media.setup(db, media_definition=configured)
    await policy(db,legacy_media, owner_max_bytes=100, owner_max_files=4, global_max_bytes=1000, global_max_files=100)
    seen = []
    async def handler(request):
        seen.append(request)
        await asyncio.sleep(0.05)
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png()).decode()}]}, headers={"x-request-id": "storage-budget-" + str(len(seen))})
    runtime, token = await runtime_fixture(db,legacy_media, service, scope, tmp_path, handler)
    request = {"model": "fixture-image", "capability": "image.generate", "prompt": "Controlled output budget", "assets": [], "parameters": {"n": 1}}
    try:
        results = await asyncio.gather(*(runtime.submit(token["task_token"], "budget-"+str(n), request) for n in range(2)), return_exceptions=True)
        assert len([value for value in results if isinstance(value, dict)]) == 1
        assert len([value for value in results if isinstance(value, PlatformError)]) == 1
        assert len(seen) == 1
        assert (await ledger.summary(scope))["spent"] == "2"
        assert (await ledger.summary(scope))["reserved"] == "0"
        # Completed delivery releases only the unused output budget, leaving
        # the actual blob charged exactly once to storage quota.
        await service.stage_blob(ctx, metadata(size=100-len(png())))
    finally: await runtime.close()


@pytest.mark.asyncio
async def test_unknown_task_pins_expired_input_and_partial_output_without_releasing_funds(db,legacy_media):
    configured = definition("image.edit"); configured["limits"]["max_artifact_bytes"] = 20
    service, ledger, scope, ctx, *_ = await legacy_media.setup(db, media_definition=configured)
    await policy(db,legacy_media, owner_max_bytes=100, owner_max_files=10, global_max_bytes=1000, global_max_files=100, ttl_seconds=1)
    source = metadata()
    await service.stage_blob(ctx, source)
    asset = await service.register_asset(ctx, **source)
    request = {"model": "fixture-image", "capability": "image.edit", "prompt": "Controlled unknown result", "assets": [asset["id"]], "parameters": {"n": 1}}
    verified = {asset["id"]: {key: asset[key] for key in ("sha256", "size", "mime", "properties")}}
    job = await service.admit(ctx, key="unknown-pin", request=request, bindings=BINDINGS, verified_inputs=verified)
    await dispatch(service, job)
    partial = metadata()
    await service.stage_blob(ctx, partial, kind="artifact", job_id=job["id"], attempt_id=job["attempt_id"])
    await service.pending(job["id"], job["attempt_id"], "synthetic_timeout")
    await asyncio.sleep(1.05)
    await service.prepare_gc()
    assert not await service.claim_gc()
    # Two ten-byte blobs plus ten bytes of unknown output budget remain held.
    with pytest.raises(PlatformError): await service.stage_blob(ctx, metadata(size=80))
    async with db.connection() as c:
        rows = await (await c.execute("SELECT storage_key,state FROM media_blobs ORDER BY storage_key")).fetchall()
        assert {row["storage_key"]: row["state"] for row in rows} == {source["storage_key"]: "attached", partial["storage_key"]: "staged"}
        original = await (await c.execute("SELECT purged_at FROM media_assets WHERE id=%s", (asset["id"],))).fetchone()
        assert original["purged_at"] is None
    result = await service.get(scope, job["id"])
    assert result["financial_state"] == "pending_reconciliation" and result["actual"] is None
    assert (await ledger.summary(scope))["reserved"] == "6"


@pytest.mark.asyncio
async def test_expired_attached_asset_soft_expires_before_exact_physical_gc(db, tmp_path,legacy_media):
    from platform_service.media.artifacts import ArtifactStore
    service, ledger, scope, ctx, *_ = await legacy_media.setup(db)
    await policy(db,legacy_media, ttl_seconds=1)
    store = ArtifactStore(legacy_media.source_path)
    item = await legacy_media.put_image(service,ctx,store,png())
    # put_image completed the actual original stage/write/attach under its nonce.
    asset = await service.get_asset(ctx,item['asset_id'])
    await asyncio.sleep(1.05)
    with pytest.raises(PlatformError): await service.get_asset(ctx, asset["id"])
    assert (store.root / item["storage_key"]).exists()
    await service.prepare_gc()
    async with db.connection() as c:
        row = await (await c.execute("SELECT purged_at FROM media_assets WHERE id=%s", (asset["id"],))).fetchone()
        assert row["purged_at"] is not None
    claims = await service.claim_gc()
    claim = next(row for row in claims if row["storage_key"] == item["storage_key"])
    permit=legacy_media.generation.owned('gc',claim['storage_key'],claim['gc_token'])
    await legacy_media.delete_owned(service,store,claim,permit)
    assert not (store.root / item["storage_key"]).exists()
    # A crash after unlink but before database ACK replays the same exact key.
    assert (await service.raw.finish_gc(item['storage_key'],claim['gc_token'],permit=permit))['state']=='deleted'
    await service.raw.finish_gc(item['storage_key'],claim['gc_token'],permit=permit)
    async with db.connection() as c:
        assert (await (await c.execute("SELECT state FROM media_blobs WHERE storage_key=%s", (item["storage_key"],))).fetchone())["state"] == "deleted"
    assert (await ledger.summary(scope))["spent"] == "0"


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['changed','symlink','untrusted_key'])
async def test_physical_gc_never_deletes_changed_symlink_or_unowned_files(db,tmp_path,legacy_media,kind):
    from platform_service.media.artifacts import ArtifactStore
    from platform_service.media_maintenance.contracts import MaintenanceError
    service,ledger,scope,ctx,*_=await legacy_media.setup(db)
    await policy(db,legacy_media,ttl_seconds=1)
    store=ArtifactStore(legacy_media.source_path)
    item=await legacy_media.put_image(service,ctx,store,png())
    path=store.root/item['storage_key']
    outside=tmp_path/'outside';outside.write_bytes(png())
    claim,permit=await legacy_media.gc_claim(service,item)
    if kind=='changed':
        changed=bytes([png()[0]^1])+png()[1:]
        path.write_bytes(changed)
        with pytest.raises(PlatformError):await legacy_media.delete_owned(service,store,claim,permit)
        assert path.read_bytes()==changed
    elif kind=='symlink':
        path.unlink();path.symlink_to(outside)
        with pytest.raises(PlatformError):await legacy_media.delete_owned(service,store,claim,permit)
        assert path.is_symlink()
    else:
        with pytest.raises(MaintenanceError,match='invalid_backup'):
            await legacy_media.delete_owned(service,store,{**claim,'storage_key':'../outside'},permit)
        assert path.read_bytes()==png()
    assert outside.read_bytes()==png()
    async with db.connection()as c:
        row=await(await c.execute('SELECT state,deleted_at FROM media_blobs WHERE storage_key=%s',(item['storage_key'],))).fetchone()
    assert row['state']=='gc_pending'and row['deleted_at']is None



@pytest.mark.asyncio
async def test_artifact_file_reader_holds_sql_share_lock_until_snapshot_completes(db, test_dsn, tmp_path,legacy_media):
    import psycopg
    from psycopg import sql
    from platform_service.media.artifacts import ArtifactStore
    configured = definition(); configured["limits"]["max_artifact_bytes"] = 1000
    service, ledger, scope, ctx, *_ = await legacy_media.setup(db, media_definition=configured)
    store = ArtifactStore(legacy_media.source_path)
    job = await admit(service, ctx)
    await dispatch(service, job)
    item = await legacy_media.put_image(service,ctx,store,png(),job=job)
    await service.stage_blob(ctx, item, kind="artifact", job_id=job["id"], attempt_id=job["attempt_id"])
    artifact = await service.register_artifact(job["id"], job["attempt_id"], **item)
    def snapshot(row):
        with psycopg.connect(test_dsn, connect_timeout=2) as concurrent:
            with pytest.raises(psycopg.errors.LockNotAvailable):
                concurrent.execute(sql.SQL("SELECT id FROM {}.media_artifacts WHERE id=%s FOR UPDATE NOWAIT").format(sql.Identifier(db.schema)), (artifact["id"],))
        return store.read(row)
    assert await service.read_artifact(scope, artifact["id"], snapshot) == png()


@pytest.mark.asyncio
async def test_staging_record_cannot_attach_to_another_invocation_or_owner(db,legacy_media):
    service, ledger, scope, ctx, *_ = await legacy_media.setup(db)
    other_invocation = await context(service, scope, "unrelated-invocation")
    foreign_scope = await owned_scope(db, scope, foreign=True)
    foreign = await context(service, foreign_scope, "foreign-invocation")
    item = metadata()
    await service.stage_blob(ctx, item)
    with pytest.raises(MaintenanceError,match='invalid_backup'): await service.register_asset(other_invocation, **item)
    with pytest.raises(MaintenanceError,match='invalid_backup'): await service.stage_blob(foreign, item)
    attached = await service.register_asset(ctx, **item)
    assert attached["invocation_id"] == ctx["invocation_id"]


@pytest.mark.asyncio
async def test_direct_registration_cannot_bypass_quota_or_race_another_invocation(db,legacy_media):
    service, ledger, scope, ctx, *_ = await legacy_media.setup(db)
    other = await context(service, scope, "direct-registration")
    await policy(db,legacy_media, owner_max_bytes=10, owner_max_files=1, global_max_bytes=1000, global_max_files=100)
    results = await asyncio.gather(service.register_asset(ctx, **metadata()), service.register_asset(other, **metadata()), return_exceptions=True)
    assert sum(isinstance(value, dict) for value in results) == 1
    assert sum(isinstance(value, PlatformError) for value in results) == 1
    async with db.connection() as c:
        row = await (await c.execute("SELECT count(*) n,sum(size) size FROM media_blobs WHERE state!='deleted'")).fetchone()
        assert row["n"] == 1 and row["size"] == 10


@pytest.mark.asyncio
async def test_undispatched_cancel_releases_output_budget_without_deleting_inputs(db,legacy_media):
    configured = definition(); configured["limits"]["max_artifact_bytes"] = 100
    service, ledger, scope, ctx, *_ = await legacy_media.setup(db, media_definition=configured)
    await policy(db,legacy_media, owner_max_bytes=100, owner_max_files=2, global_max_bytes=1000, global_max_files=100)
    first = await admit(service, ctx, "first-budget")
    with pytest.raises(PlatformError): await admit(service, ctx, "blocked-budget")
    assert (await service.cancel(scope, first["id"]))["financial_state"] == "released"
    second = await admit(service, ctx, "next-budget")
    assert second["id"] != first["id"] and second["status"] == "reserved"
    assert (await ledger.summary(scope))["reserved"] == "6"


@pytest.mark.asyncio
async def test_status_label_without_immutable_terminal_evidence_never_unpins_or_releases_storage(db,legacy_media):
    import psycopg
    configured = definition("image.edit")
    service, ledger, scope, ctx, *_ = await legacy_media.setup(db, media_definition=configured)
    await policy(db,legacy_media, ttl_seconds=1)
    source = metadata()
    await service.stage_blob(ctx, source)
    asset = await service.register_asset(ctx, **source)
    request = {"model": "fixture-image", "capability": "image.edit", "prompt": "Controlled incomplete terminal label", "assets": [asset["id"]], "parameters": {"n": 1}}
    verified = {asset["id"]: {key: asset[key] for key in ("sha256", "size", "mime", "properties")}}
    job = await service.admit(ctx, key="untrusted-label", request=request, bindings=BINDINGS, verified_inputs=verified)
    await dispatch(service, job)
    partial = metadata()
    await service.stage_blob(ctx, partial, kind="artifact", job_id=job["id"], attempt_id=job["attempt_id"])
    await service.pending(job["id"], job["attempt_id"], "unknown")
    # Fault injection: an internal status-writing bug must not grant authority
    # equivalent to actual immutable provider evidence.
    async with legacy_media.sql(service.job_permit(job['id'])) as c:
        await c.execute("UPDATE media_jobs SET execution_state='failed' WHERE id=%s", (job["id"],))
    await asyncio.sleep(1.05)
    assert await service.prepare_gc() == []
    with pytest.raises(psycopg.errors.CheckViolation)as error:
        async with legacy_media.sql(service.job_permit(job['id'])) as c:
            await service._release_storage_budget(c, job['id'])
    assert 'media maintenance permit required' not in str(error.value)
    assert (await ledger.summary(scope))["reserved"] == "6"


@pytest.mark.asyncio
async def test_database_rejects_output_budget_reduction_without_matching_blob_allocation(db,legacy_media):
    import psycopg
    service, ledger, scope, ctx, *_=await legacy_media.setup(db)
    job=await admit(service,ctx)
    with pytest.raises(psycopg.errors.CheckViolation)as error:
        async with legacy_media.sql(service.job_permit(job['id'])) as c:
            await c.execute("UPDATE media_storage_budgets SET remaining_bytes=0,remaining_files=0 WHERE job_id=%s",(job['id'],))
    assert 'media maintenance permit required' not in str(error.value)
    async with db.connection() as c:
        budget=await (await c.execute("SELECT * FROM media_storage_budgets WHERE job_id=%s",(job["id"],))).fetchone()
        assert budget["remaining_bytes"]==budget["max_bytes"] and budget["remaining_files"]==budget["max_files"]
    assert (await ledger.summary(scope))["reserved"]=="6"


@pytest.mark.asyncio
async def test_database_rejects_registry_owner_that_disagrees_with_full_invocation_scope(db,legacy_media):
    import psycopg
    service,ledger,scope,ctx,*_=await legacy_media.setup(db)
    permit=await legacy_media.generation.acquire('upload',ctx['invocation_id'])
    with pytest.raises(psycopg.errors.CheckViolation)as error:
        async with legacy_media.sql(permit) as c:
            await c.execute("""INSERT INTO media_blobs(storage_key,kind,scope,user_id,tenant_id,billing_account_id,invocation_id,sha256,size,mime,properties,state,expires_at)
              SELECT repeat('a',64),'asset',scope,'other-user',tenant_id,billing_account_id,id,repeat('b',64),10,'image/png','{}','staged',clock_timestamp()+interval '1 hour'
              FROM media_invocations WHERE id=%s""",(ctx["invocation_id"],))
    assert 'media maintenance permit required' not in str(error.value)
    async with db.connection() as c:
        assert (await (await c.execute("SELECT count(*) n FROM media_blobs")).fetchone())["n"]==0


@pytest.mark.asyncio
async def test_interrupted_unverifiable_part_keeps_gc_pending_and_quota_until_verified_private_recovery(db,tmp_path,legacy_media):
    service,ledger,scope,ctx,*_=await legacy_media.setup(db)
    await policy(db,legacy_media,owner_max_bytes=len(png()),owner_max_files=1,global_max_bytes=1000,global_max_files=10,ttl_seconds=1)
    runtime,_=await runtime_fixture(db,legacy_media,service,scope,tmp_path,lambda request:None)
    item={**metadata(size=len(png())),"sha256":hashlib.sha256(png()).hexdigest()}
    await service.stage_blob(ctx,item)
    assert runtime.store.root.stat().st_mode & 0o777 == 0o700
    partial=runtime.store.root/(item["storage_key"]+".part")
    partial.write_bytes(png()[:10]);partial.chmod(0o600)
    try:
        await asyncio.sleep(1.05)
        assert (await runtime.run_once())["gc"]==0 and partial.exists()
        async with db.connection() as c:
            row=await(await c.execute("SELECT state,deleted_at FROM media_blobs WHERE storage_key=%s",(item["storage_key"],))).fetchone()
        assert row["state"]=="staged" and row["deleted_at"] is None
        with pytest.raises(PlatformError):await service.stage_blob(ctx,metadata(size=1))
        # Synthetic private operator recovery only: the exact owned test partial
        # is removed after inspection; no production filesystem/records involved.
        partial.unlink()
        assert (await runtime.run_once())['gc']==0
        with pytest.raises(PlatformError):await service.stage_blob(ctx,metadata(size=1))
        async with db.connection()as c:
            row=await(await c.execute('SELECT state,deleted_at FROM media_blobs WHERE storage_key=%s',(item['storage_key'],))).fetchone()
        assert row['state']=='staged'and row['deleted_at']is None
        assert (await ledger.summary(scope))["reserved"]=="0"
    finally:await runtime.close()


@pytest.mark.asyncio
async def test_database_rejects_attaching_a_blob_to_different_existing_asset_metadata(db,legacy_media):
    import psycopg
    service,ledger,scope,ctx,*_=await legacy_media.setup(db)
    original=await service.register_asset(ctx,**metadata())
    other=metadata()
    await service.stage_blob(ctx,other)
    with pytest.raises(psycopg.errors.CheckViolation)as error:
        async with legacy_media.sql(service.upload_permit(other['storage_key'])) as c:
            await c.execute("UPDATE media_blobs SET state='attached',attached_id=%s,expires_at=%s WHERE storage_key=%s",(original['id'],original['expires_at'],other['storage_key']))

    assert 'media maintenance permit required' not in str(error.value)

@pytest.mark.asyncio
async def test_database_rejects_registry_foreign_attempt_even_with_balanced_budget_transfer(db,legacy_media):
    import psycopg
    service,ledger,scope,ctx,*_=await legacy_media.setup(db)
    job=await admit(service,ctx)
    await dispatch(service,job)
    with pytest.raises(psycopg.errors.CheckViolation)as error:
        async with legacy_media.sql(service.job_permit(job['id'])) as c:
            await c.execute("UPDATE media_storage_budgets SET remaining_bytes=remaining_bytes-10,remaining_files=remaining_files-1 WHERE job_id=%s",(job['id'],))
            await c.execute("""INSERT INTO media_blobs(storage_key,kind,scope,user_id,tenant_id,billing_account_id,invocation_id,job_id,attempt_id,sha256,size,mime,properties,state,expires_at)
              SELECT repeat('a',64),'artifact',scope,user_id,tenant_id,billing_account_id,invocation_id,id,'foreign-attempt',repeat('b',64),10,'image/png','{}','staged',clock_timestamp()+interval '1 hour' FROM media_jobs WHERE id=%s""",(job['id'],))

    # This exact foreign attempt must fail the real binding guard, not lack a cap.
    assert 'media maintenance permit required' in str(error.value)
    assert (await ledger.summary(scope))['reserved']=='6'
    async with db.connection()as c:
        assert(await(await c.execute('SELECT count(*) n FROM media_blobs')).fetchone())['n']==0

@pytest.mark.asyncio
@pytest.mark.parametrize("terminal",[False,True])
async def test_migration015_backfills_legacy014_job_budgets_and_replays_idempotently(legacy_014,tmp_path,monkeypatch,terminal):
    import platform_service.db as database_module
    database=legacy_014.db
    ledger,scope,job=await legacy_014.bootstrap(terminal=terminal)
    before=await ledger.summary(scope)
    await legacy_014.reject_control_on_actual014()
    source=database_module.MIGRATIONS_DIR
    through016=tmp_path/'public_001_016';through016.mkdir()
    for path in source.glob('*.sql'):
        if path.name<'017':(through016/path.name).write_bytes(path.read_bytes())
    monkeypatch.setattr(database_module,'MIGRATIONS_DIR',through016)
    assert await database.migrate()==['015_media_storage.sql','016_m1_image_enablement.sql']
    assert await database.migrate()==[]
    async with database.connection()as c:
        await c.execute('UPDATE media_jobs SET updated_at=clock_timestamp() WHERE id=%s',(job['id'],))
        row=await(await c.execute('SELECT * FROM media_storage_budgets WHERE job_id=%s',(job['id'],))).fetchone()
        assert(await(await c.execute('SELECT count(*) n FROM schema_migrations')).fetchone())['n']==16
    assert row['max_bytes']==job['model_definition']['limits']['max_artifact_bytes']
    if terminal:
        assert row['remaining_bytes']==row['remaining_files']==0 and row['released_at']is not None
        assert before['spent']=='2'and before['reserved']=='0'
    else:
        assert row['remaining_bytes']==row['max_bytes']-10 and row['remaining_files']==0 and row['max_files']==1 and row['released_at']is None
        assert before['spent']=='0'and before['reserved']=='6'
    assert await ledger.summary(scope)==before


@pytest.mark.asyncio
async def test_unknown_gc_original_lease_expiry_cannot_reclaim_or_release_quota(db,legacy_media,tmp_path):
    from platform_service.media.artifacts import ArtifactStore
    service,ledger,scope,ctx,*_=await legacy_media.setup(db)
    raw=png()
    await policy(db,legacy_media,ttl_seconds=1,owner_max_bytes=len(raw),owner_max_files=1,global_max_bytes=1000,global_max_files=10)
    store=ArtifactStore(legacy_media.source_path)
    item=await legacy_media.put_image(service,ctx,store,raw)
    claim,permit=await legacy_media.gc_claim(service,item)
    # Mutable GC lease fault timing is scoped by its original still-valid cap;
    # immutable expires_at and original upload ownership are never rewritten.
    async with legacy_media.sql(permit)as c:
        await c.execute("UPDATE media_blobs SET gc_lease_until=clock_timestamp()+interval '1 second' WHERE storage_key=%s",(item['storage_key'],))
    path=store.root/item['storage_key']
    path.write_bytes(b'x'+raw[1:])
    with pytest.raises(PlatformError,match='media_gc_blob_unverifiable'):
        await legacy_media.delete_owned(service,store,claim,permit)
    assert not permit._valid
    await asyncio.sleep(1.05)
    assert await service.claim_gc()==[]
    assert path.read_bytes()==b'x'+raw[1:]
    with pytest.raises(PlatformError):await service.stage_blob(ctx,metadata(size=1))
    async with db.connection()as c:
        row=await(await c.execute('SELECT state,deleted_at,gc_lease_until<=clock_timestamp() expired FROM media_blobs WHERE storage_key=%s',(item['storage_key'],))).fetchone()
    assert row['state']=='gc_pending'and row['deleted_at']is None and row['expired']is True
    assert(await ledger.summary(scope))['spent']=='0'
    assert(await ledger.summary(scope))['reserved']=='0'
