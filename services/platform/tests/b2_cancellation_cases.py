"""Synthetic causal cancellation cases. Importing this module opens no resources."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx


class ParkedStream(httpx.AsyncByteStream):
    def __init__(self, deadline: datetime):
        self.deadline = deadline
        self.entered = asyncio.Event()
        self.entered_at = None
        self.saw_cancel = False
        self.finalized = False
        self.closed = False
        self.consumed = False

    async def __aiter__(self):
        if self.consumed:
            raise RuntimeError('stream already consumed')
        entered_at = datetime.now(timezone.utc)
        if entered_at >= self.deadline:
            raise RuntimeError('stream deadline expired')
        self.consumed = True
        self.entered_at = entered_at
        self.entered.set()
        try:
            yield b'{'
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.saw_cancel = True
            raise
        finally:
            self.finalized = True

    async def aclose(self):
        self.closed = True


MUTABLE_WORK_FIELDS = frozenset({
    'phase', 'due_at', 'lease_token', 'lease_until', 'polls', 'failures',
    'last_safe_code', 'updated_at',
})


def _assert_binding(initial, row):
    # Match the SQL trigger's complete immutable projection, including deadline
    # and created_at, instead of maintaining a weaker subset of binding fields.
    assert {k: v for k, v in row.items() if k not in MUTABLE_WORK_FIELDS} == {
        k: v for k, v in initial.items() if k not in MUTABLE_WORK_FIELDS
    }, 'immutable work binding changed'


@asynccontextmanager
async def _scoped_sql(legacy_media, permit, guarded_database, operation):
    with operation(permit):
        async with guarded_database(legacy_media.runtime_db).connection() as connection:
            yield connection


async def _read_work(sql, job_id):
    async with sql() as connection:
        row = await (await connection.execute(
            'SELECT * FROM media_work WHERE job_id=%s', (job_id,),
        )).fetchone()
    assert row is not None, 'expected original work row'
    return row


async def _insert_work(sql, job_id, *, expired):
    # Two fixed INSERT expressions; there is no deadline UPDATE or enqueue.
    delta = "-interval '1 second'" if expired else "+interval '20 seconds'"
    async with sql() as connection:
        row = await (await connection.execute(
            'INSERT INTO media_work(job_id,attempt_id,binding_hash,'
            'provider_scope_hash,provider_job_id,adapter,deadline) '
            'SELECT job_id,attempt_id,binding_hash,provider_scope_hash,'
            "provider_job_id,'newapi_videos_v1',clock_timestamp()" + delta +
            " FROM media_provider_associations WHERE job_id=%s AND kind='job' RETURNING *",
            (job_id,),
        )).fetchone()
    assert row is not None, 'expected exactly one associated work'
    return row


async def _assert_final(sql, initial, queue, ledger, scope, permit, error_type, claim, operation):
    work = await _read_work(sql, initial['job_id'])
    _assert_binding(initial, work)
    assert work['phase'] == 'manual', 'deadline must produce manual work'
    assert work['lease_token'] is None and work['lease_until'] is None
    assert work['last_safe_code'] == 'media_recovery_window_expired'
    async with sql() as connection:
        budget = await (await connection.execute(
            'SELECT * FROM media_storage_budgets WHERE job_id=%s', (initial['job_id'],),
        )).fetchone()
        assert budget is not None and budget['released_at'] is None
        assert budget['remaining_bytes'] == 8388608
        job = await (await connection.execute(
            'SELECT * FROM media_jobs WHERE id=%s', (initial['job_id'],),
        )).fetchone()
        assert job['status'] == 'pending_reconciliation' and job['actual'] is None
        for table in ('media_sources', 'media_observations', 'media_artifacts',
                      'media_terminal_evidence', 'media_receipts', 'media_blobs'):
            count = await (await connection.execute(
                f'SELECT count(*) n FROM {table} WHERE job_id=%s', (initial['job_id'],),
            )).fetchone()
            assert count['n'] == 0, 'unexpected delivery or settlement row'
        assert (await (await connection.execute(
            'SELECT count(*) n FROM media_work WHERE job_id=%s', (initial['job_id'],),
        )).fetchone())['n'] == 1
    with operation(permit):
        assert (await ledger.summary(scope))['reserved'] == '5'
    # The actual prior claim is used for C2; C1's original unclaimed row has
    # no lease. Neither can invent a heartbeat capability after expiry.
    try:
        await queue.heartbeat(claim)
    except error_type as error:
        assert error.code == 'media_work_lease_lost'
    else:
        raise AssertionError('expired heartbeat unexpectedly accepted')
    after_denial = await _read_work(sql, initial['job_id'])
    assert after_denial == work, 'rejected heartbeat changed work'
    assert permit.physical_count == 0
    assert permit._valid and not permit._closed, 'unknown hold must retain job permit'


async def expired_before_dispatch(db, legacy_media, monkeypatch) -> dict[str, bool | int]:
    """C1: expired INSERT becomes manual without dispatching any HTTP."""
    from test_media_async_store import setup_async
    from platform_service.errors import PlatformError
    from platform_service.identity import IdentityService
    from platform_service.media.core import MediaService
    from platform_service.media.artifacts import ArtifactStore
    from platform_service.media.worker import MediaWorker
    from platform_service.media_config import MediaProvider, MediaSettings
    from platform_service.media_maintenance.runtime import GuardedDatabase, operation

    _, _, _, scope, _, job = await setup_async(db, legacy_media, monkeypatch)
    service = legacy_media.client(MediaService(
        legacy_media.runtime_db, IdentityService(legacy_media.runtime_db, None),
    ))
    queue = legacy_media.queue(service)
    ledger = service.raw.ledger
    permit = service.job_permit(job['id'])
    def sql():
        return _scoped_sql(legacy_media, permit, GuardedDatabase, operation)
    provider = MediaProvider('1', 'http://127.0.0.1:19991/v1', 'fixture',
                             {'fixture-image': 'explicit-fixture'},
                             adapter='newapi_videos_v1', capabilities=('video.generate',))
    settings = MediaSettings(mode='test', providers={'fixture': provider},
                             storage_root=legacy_media.source_path)
    assert settings.worker_wall_seconds == 120
    requests = []
    def handler(request):
        requests.append(request.method)
        raise AssertionError('expired work must not issue HTTP')
    async def binding(_job):
        return provider
    async with asyncio.timeout(30):
        initial = await _insert_work(sql, job['id'], expired=True)
        assert initial['deadline'] < datetime.now(timezone.utc)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            worker = MediaWorker(queue.raw, ArtifactStore(settings.storage_root),
                                 client, settings, binding)
            count = await legacy_media.run_worker(worker)
        assert count == 0 and requests == []
        await _assert_final(sql, initial, queue, ledger, scope, permit, PlatformError, initial, operation)
        assert all(p.physical_count == 0 for p in legacy_media.generation.permits)
        assert not any(p.binding.kind == 'maintenance' for p in legacy_media.generation.permits)
    return {'worker_claims': count, 'http_gets': len(requests),
            'manual': True, 'heartbeat_denied': True, 'reserved_points': 5,
            'budget_unreleased': True, 'binding_unchanged': True, 'delivery_rows': 0}


async def inflight_deadline_cancellation(db, legacy_media, monkeypatch) -> dict[str, bool | int]:
    """C2: the original worker's own deadline cancels an entered GET body."""
    from test_media_async_store import setup_async
    from platform_service.errors import PlatformError
    from platform_service.identity import IdentityService
    from platform_service.media.core import MediaService
    from platform_service.media.artifacts import ArtifactStore
    from platform_service.media.worker import MediaWorker
    from platform_service.media_config import MediaProvider, MediaSettings
    from platform_service.media_maintenance.runtime import GuardedDatabase, operation

    _, _, _, scope, _, job = await setup_async(db, legacy_media, monkeypatch)
    service = legacy_media.client(MediaService(
        legacy_media.runtime_db, IdentityService(legacy_media.runtime_db, None),
    ))
    queue = legacy_media.queue(service)
    ledger = service.raw.ledger
    permit = service.job_permit(job['id'])
    def sql():
        return _scoped_sql(legacy_media, permit, GuardedDatabase, operation)
    provider = MediaProvider('1', 'http://127.0.0.1:19991/v1', 'fixture',
                             {'fixture-image': 'explicit-fixture'},
                             adapter='newapi_videos_v1', capabilities=('video.generate',))
    settings = MediaSettings(mode='test', providers={'fixture': provider},
                             storage_root=legacy_media.source_path)
    assert settings.worker_wall_seconds == 120
    requests = []
    async def binding(_job):
        return provider
    # The 30 seconds bounds failure only. It never changes the stored 20 second
    # deadline; expiry of this outer timeout raises and cannot return a pass.
    async with asyncio.timeout(30):
        initial = await _insert_work(sql, job['id'], expired=False)
        probe = ParkedStream(initial['deadline'])
        def handler(request):
            requests.append(request.method)
            assert requests == ['GET'], 'only one status GET is authorized'
            assert request.url.path == '/v1/videos/video_fixture'
            return httpx.Response(200, stream=probe,
                                  headers={'content-type': 'application/json'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            worker = MediaWorker(queue.raw, ArtifactStore(settings.storage_root),
                                 client, settings, binding)
            worker_task = asyncio.create_task(legacy_media.run_worker(worker))
            entered_task = asyncio.create_task(probe.entered.wait())
            try:
                done, _ = await asyncio.wait(
                    {entered_task, worker_task}, return_when=asyncio.FIRST_COMPLETED,
                )
                assert entered_task in done and probe.entered.is_set(), 'body did not enter first'
                assert not worker_task.done(), 'worker already ended before parked-body check'
                assert probe.entered_at is not None and probe.entered_at < initial['deadline']
                assert not (probe.saw_cancel or probe.finalized or probe.closed)
                claim = await _read_work(sql, job['id'])
                _assert_binding(initial, claim)
                assert claim['lease_token'] is not None and claim['lease_until'] is not None
                assert datetime.now(timezone.utc) < claim['lease_until'] <= initial['deadline']
                await queue.heartbeat(claim)
                after_heartbeat = await _read_work(sql, job['id'])
                _assert_binding(initial, after_heartbeat)
                assert after_heartbeat['lease_token'] == claim['lease_token']
                assert datetime.now(timezone.utc) < after_heartbeat['lease_until'] <= initial['deadline']
                assert not worker_task.done() and not probe.saw_cancel
                count = await worker_task
                assert count == 1 and not worker_task.cancelled()
                assert datetime.now(timezone.utc) >= initial['deadline']
                assert probe.saw_cancel and probe.finalized and probe.closed
                assert requests == ['GET']
                await _assert_final(sql, initial, queue, ledger, scope, permit, PlatformError, claim, operation)
                assert all(p.physical_count == 0 for p in legacy_media.generation.permits)
                assert not any(p.binding.kind == 'maintenance' for p in legacy_media.generation.permits)
            finally:
                # On a failed precondition or outer timeout, collect every owned
                # task without converting the original exception into a pass.
                for task in (entered_task, worker_task):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(entered_task, worker_task, return_exceptions=True)
    return {'worker_claims': count, 'http_gets': len(requests), 'body_entered': True,
            'actual_cancel': probe.saw_cancel, 'stream_finalized': probe.finalized,
            'stream_closed': probe.closed, 'heartbeat_checked': True,
            'heartbeat_denied': True, 'manual': True, 'reserved_points': 5,
            'budget_unreleased': True, 'binding_unchanged': True, 'delivery_rows': 0}
