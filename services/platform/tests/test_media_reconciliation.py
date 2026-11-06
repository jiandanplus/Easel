"""Real PostgreSQL checks for audited media write-offs (migration 021)."""
import asyncio
from copy import deepcopy
from uuid import uuid4

import psycopg
import pytest

from platform_service.contracts import OperatorIdentity
from platform_service.errors import PlatformError
from test_internal_features import local_maintenance
from test_media_credits import admit, dispatch, setup

WRITEOFF = OperatorIdentity('host-media-writeoff-fixture', frozenset({'execution:writeoff'}))
REASON = 'Image generated upstream but never delivered; platform absorbs the cost.'


async def pending(db, root, *, count=1):
    from platform_service.media.reconciliation import MediaReconciliation
    maintenance = await local_maintenance(db, root)
    service, ledger, scope, context, *_ = await setup(db, maintenance=maintenance)
    jobs = []
    for number in range(count):
        async with maintenance.use('job', str(uuid4()), str(uuid4()), transient=True):
            job = await admit(service, context, 'writeoff-' + str(number))
        async with maintenance.use('job', job['id'], job['attempt_id'], transient=True):
            await dispatch(service, job)
            await service.pending(job['id'], job['attempt_id'], 'upstream_uncertain')
        jobs.append(job)
    async with db.connection() as conn:
        await conn.execute("UPDATE media_jobs SET lease_until=now()-interval '1 second'")
    return MediaReconciliation(db), service, ledger, scope, jobs


async def plan(reconciler, jobs, **kwargs):
    return await reconciler.plan_waivers(WRITEOFF, [job['id'] for job in jobs],
        kwargs.get('reason', REASON), kwargs.get('approval_reference', 'explicit-user-approval-fixture'))


async def snapshot(db):
    tables = ['credit_lots','credit_reservations','credit_holds','credit_journals','credit_entries',
              'media_jobs','media_invocations','media_receipts','media_storage_budgets',
              'media_resolutions','media_resolution_operations']
    async with db.connection() as conn:
        return {table: await (await conn.execute('SELECT to_jsonb(t) row FROM ' + table + ' t ORDER BY to_jsonb(t)::text')).fetchall() for table in tables}


@pytest.mark.asyncio
async def test_plan_is_read_only_and_apply_releases_hold_and_storage_without_receipt(db, tmp_path):
    r, service, ledger, scope, jobs = await pending(db, tmp_path, count=2)
    assert (await ledger.summary(scope))['pending_reconciliation'] == 2
    before = await snapshot(db)
    p = await plan(r, jobs)
    assert p['subject'] == 'media' and len(p['items']) == 2
    assert await snapshot(db) == before
    result = await r.apply_waivers(WRITEOFF, p, p['plan_sha256'], 'media-writeoff-two')
    assert result['released_microcredits'] == str(sum(j['amount'] for j in jobs))
    assert all(i['status'] == 'waived' and i['actual'] is None and i['customer_charge'] == '0'
               and i['provider_usage_known'] is False for i in result['items'])
    wallet = await ledger.summary(scope)
    assert wallet['reserved'] == '0' and wallet['spent'] == '0' and wallet['pending_reconciliation'] == 0
    async with db.connection() as conn:
        assert not await (await conn.execute('SELECT 1 FROM media_receipts')).fetchall()
        jobs_after = await (await conn.execute('SELECT status,actual,reason,execution_state,delivery_state FROM media_jobs')).fetchall()
        assert all(j == {'status': 'waived', 'actual': None, 'reason': 'platform_absorbed',
                         'execution_state': 'unknown', 'delivery_state': 'unavailable'} for j in jobs_after)
        budgets = await (await conn.execute('SELECT remaining_bytes,remaining_files,released_at FROM media_storage_budgets')).fetchall()
        assert all(b['remaining_bytes'] == 0 and b['remaining_files'] == 0 and b['released_at'] for b in budgets)
        invocation = await (await conn.execute('SELECT held,spent FROM media_invocations')).fetchone()
        assert invocation == {'held': 0, 'spent': 0}
    frozen = await snapshot(db)
    assert await r.apply_waivers(WRITEOFF, p, p['plan_sha256'], 'media-writeoff-two') == result
    assert await r.operation(WRITEOFF, 'media-writeoff-two') == result
    assert await snapshot(db) == frozen


@pytest.mark.asyncio
async def test_writeoff_needs_the_operator_capability(db, tmp_path):
    r, *_, jobs = await pending(db, tmp_path)
    for caps in [frozenset(), frozenset({'media:publish'}), frozenset({'execution:review'})]:
        with pytest.raises(PlatformError) as error:
            await r.plan_waivers(OperatorIdentity('wrong-capability', caps), [jobs[0]['id']], 'reason', 'approval')
        assert error.value.status == 403
    with pytest.raises(PlatformError) as error:
        await r.operation(WRITEOFF, 'missing')
    assert error.value.status == 404


@pytest.mark.asyncio
async def test_only_expired_unreceipted_pending_jobs_are_eligible(db, tmp_path):
    r, service, ledger, scope, jobs = await pending(db, tmp_path)
    async with db.connection() as conn:
        await conn.execute("UPDATE media_jobs SET lease_until=now()+interval '1 hour'")
    with pytest.raises(PlatformError) as error:
        await plan(r, jobs)
    assert error.value.code == 'media_writeoff_ineligible'
    with pytest.raises(PlatformError) as error:
        await r.plan_waivers(WRITEOFF, ['no-such-job'], REASON, 'approval')
    assert error.value.status == 404


@pytest.mark.asyncio
async def test_stale_changed_or_tampered_plan_cannot_write(db, tmp_path):
    r, *_, jobs = await pending(db, tmp_path)
    p = await plan(r, jobs)
    before = await snapshot(db)
    tampered = deepcopy(p);tampered['items'][0]['amount'] = '1'
    wrong_subject = deepcopy(p);wrong_subject['subject'] = 'execution'
    for value, reviewed in [(tampered, p['plan_sha256']), (p, '0'*64), (wrong_subject, p['plan_sha256'])]:
        with pytest.raises(PlatformError):
            await r.apply_waivers(WRITEOFF, value, reviewed, 'tampered')
        assert await snapshot(db) == before
    async with db.connection() as conn:
        await conn.execute("UPDATE media_jobs SET reason='another_internal_reason'")
    before = await snapshot(db)
    with pytest.raises(PlatformError) as error:
        await r.apply_waivers(WRITEOFF, p, p['plan_sha256'], 'stale')
    assert error.value.status == 409
    assert await snapshot(db) == before


@pytest.mark.asyncio
async def test_same_key_concurrent_apply_runs_once_and_different_plan_conflicts(db, tmp_path):
    from platform_service.execution.reconciliation import plan_digest
    r, service, ledger, scope, jobs = await pending(db, tmp_path)
    p = await plan(r, jobs)
    results = await asyncio.gather(*[r.apply_waivers(WRITEOFF, p, p['plan_sha256'], 'same-key') for _ in range(4)])
    assert all(x == results[0] for x in results)
    assert (await ledger.summary(scope))['reserved'] == '0'
    changed = deepcopy(p);changed['reason'] = 'Different operator decision'
    changed['plan_sha256'] = plan_digest(changed)
    before = await snapshot(db)
    with pytest.raises(PlatformError) as error:
        await r.apply_waivers(WRITEOFF, changed, changed['plan_sha256'], 'same-key')
    assert error.value.code == 'idempotency_conflict'
    assert await snapshot(db) == before


@pytest.mark.asyncio
async def test_database_rejects_unaudited_or_replayed_media_waivers(db, tmp_path):
    r, *_, jobs = await pending(db, tmp_path)
    async with db.connection() as conn:
        job = await (await conn.execute('SELECT * FROM media_jobs')).fetchone()
    for statement in [
        "UPDATE media_jobs SET status='waived',actual=NULL",
        "UPDATE credit_reservations SET status='waived',completion_key='forged'",
    ]:
        with pytest.raises(psycopg.errors.Error):
            async with db.connection() as conn:
                await conn.execute(statement)
    p = await plan(r, jobs)
    await r.apply_waivers(WRITEOFF, p, p['plan_sha256'], 'audited')
    for statement in [
        "UPDATE media_resolutions SET customer_charge=1",
        "DELETE FROM media_resolutions",
        "TRUNCATE media_resolution_operations",
        "UPDATE media_jobs SET status='settled',actual=1",
        "UPDATE media_jobs SET delivery_state='available'",
    ]:
        with pytest.raises(psycopg.errors.Error):
            async with db.connection() as conn:
                await conn.execute(statement)
    with pytest.raises(psycopg.errors.Error):
        async with db.connection() as conn:
            await conn.execute("""INSERT INTO media_jobs SELECT (jsonb_populate_record(NULL::media_jobs,
                to_jsonb(j)||jsonb_build_object('id','copy','attempt_id','copy-attempt','operation_key','copy','status','waived'))).*
                FROM media_jobs j WHERE id=%s""", (job['id'],))


@pytest.mark.asyncio
async def test_waiver_unpins_a_staged_artifact_whose_delivery_never_finished(db, tmp_path):
    # Production 81a6155d: decoded upstream image staged, the write failed, no terminal evidence.
    from platform_service.media.reconciliation import MediaReconciliation
    maintenance = await local_maintenance(db, tmp_path)
    service, ledger, scope, context, *_ = await setup(db, maintenance=maintenance)
    async with maintenance.use('job', str(uuid4()), str(uuid4()), transient=True):
        job = await admit(service, context, 'staged')
    async with maintenance.use('job', job['id'], job['attempt_id'], transient=True):
        await dispatch(service, job)
        await service.stage_blob(context, {'storage_key': 'e'*32, 'sha256': 'f'*64, 'size': 10, 'mime': 'image/png',
                                           'properties': {'width': 1, 'height': 1, 'format': 'png'}},
                                 kind='artifact', job_id=job['id'], attempt_id=job['attempt_id'])
        await service.pending(job['id'], job['attempt_id'], 'upstream_uncertain')
    async with db.connection() as conn:
        await conn.execute("UPDATE media_jobs SET lease_until=now()-interval '1 second'")
        pinned = "SELECT media_storage_pinned(b) pinned, b.state FROM media_blobs b WHERE storage_key=%s"
        assert await (await conn.execute(pinned, ('e'*32,))).fetchone() == {'pinned': True, 'state': 'staged'}
    r = MediaReconciliation(db)
    p = await plan(r, [job])
    await r.apply_waivers(WRITEOFF, p, p['plan_sha256'], 'staged-writeoff')
    async with db.connection() as conn:
        assert await (await conn.execute(pinned, ('e'*32,))).fetchone() == {'pinned': False, 'state': 'staged'}
        budget = await (await conn.execute('SELECT remaining_bytes,remaining_files,released_at FROM media_storage_budgets WHERE job_id=%s', (job['id'],))).fetchone()
        assert budget['remaining_bytes'] == 0 and budget['remaining_files'] == 0 and budget['released_at']
    assert (await ledger.summary(scope))['reserved'] == '0'
