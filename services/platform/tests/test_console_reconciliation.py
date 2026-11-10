"""R1 progress metadata against real PG; financial fixtures are never mocked."""
import asyncio
import hashlib
import json
import secrets
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb
import pytest
import pytest_asyncio

from platform_service.console_auth import ConsoleAuth, AdminPrincipal, ROLES
from platform_service.console_config import ConsoleSettings
from platform_service.errors import PlatformError
from platform_service.identity.oidc import digest
from test_execution_core import setup, admit


async def admin(db, role='operator', subject=None):
    actor, token, csrf = uuid4().hex, secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    async with db.connection() as c:
        await c.execute("INSERT INTO console_admins(id,issuer,subject,role,created_by) VALUES (%s,'fixture',%s,%s,'fixture')", (actor, subject or actor, role))
        await c.execute("INSERT INTO console_sessions(session_hash,admin_id,csrf_token,authorization_epoch,created_at,expires_at) VALUES (%s,%s,%s,0,now(),now()+interval '1 hour')", (digest(token), actor, csrf))
    settings = ConsoleSettings(database_url='postgresql://unused@127.0.0.1/unused', public_url='https://console.example', issuer='https://identity.example/oidc', client_id='fixture', client_secret='fixture', db_schema=db.schema)
    return ConsoleAuth(db, settings), AdminPrincipal(actor, role, ROLES[role], csrf, digest(token)), token


async def financial_snapshot(db):
    # Owner-only test inspection includes all raw fields, detecting ANY money/job write.
    result = {}
    async with db.connection() as c:
        for table in ('billing_accounts', 'memberships', 'credit_lots', 'credit_holds', 'credit_reservations', 'credit_journals', 'credit_entries', 'execution_jobs', 'execution_receipts', 'execution_results', 'execution_invocations'):
            rows = await (await c.execute(f'SELECT row_to_json(t)::text AS row FROM {table} t ORDER BY row_to_json(t)::text')).fetchall()
            result[table] = [row['row'] for row in rows]
    return result


async def counts(db):
    async with db.connection() as c:
        return [(await (await c.execute('SELECT count(*) AS n FROM ' + table)).fetchone())['n'] for table in ('console_reconciliation_cases', 'console_operations', 'console_audit_events')]


@pytest_asyncio.fixture
async def env(db):
    from platform_service.console_reconciliation import ConsoleReconciliation
    execution, _, _, ctx = await setup(db)
    job = await admit(execution, ctx)
    await execution.dispatch(job['id'], job['attempt_id'])
    await execution.pending(job['id'], job['attempt_id'], 'PRIVATE_RAW_REASON')
    auth, principal, token = await admin(db)
    return ConsoleReconciliation(db, auth), auth, principal, token, execution, ctx, job


def body(detail, *, revision=0, state='investigating', reason='检查现有事实，等待精确凭据'):
    return {'state': state, 'reason': reason, 'expected_revision': revision, 'snapshot_hash': detail['snapshot_hash']}


async def live_pending(db, env):
    """Synthetic verified payment facts and a real live-mode reservation; no I/O."""
    from platform_service.billing import BillingService
    from platform_service.contracts import Scope, OperatorIdentity
    from platform_service.membership import MembershipService
    from test_billing import event
    from test_execution_core import definition, BINDINGS
    execution = env[4]
    operator = OperatorIdentity('funding-filter-fixture', frozenset({'plans:publish', 'billing:publish', 'execution:publish'}))
    async with db.connection() as c:
        plan = (await (await c.execute("SELECT definition FROM plans WHERE id='fixture-plan-v1'")).fetchone())['definition']
    await MembershipService(db).publish_plan(operator, {**plan, 'id': 'fixture-live-plan', 'name': 'Synthetic live funding', 'test_only': False})
    billing = BillingService(db, mode='live', channels=({'id': 'wechat', 'merchant_id': 'm', 'app_id': 'a', 'enabled': True},))
    await billing.publish_offer(operator, {'id': 'funding-filter-live-offer', 'plan_id': 'fixture-live-plan', 'version': '1', 'amount_minor': '1', 'currency': 'CNY', 'test_only': False, 'terms': {'refund_policy': 'unused_full_only', 'description': 'Synthetic test facts only'}})
    scope = Scope('u', 'easel', 't', 'wallet', 'session', 0)
    order = await billing.create_order(scope, offer_id='funding-filter-live-offer', channel='wechat', idempotency_key='funding-filter-payment')
    await billing.apply_verified_event({**event(order), 'mode': 'live'})
    model = {**definition(), 'id': 'fixture-live-text', 'test_only': False, 'verification': 'live', 'verification_ref': 'synthetic-funding-filter-only'}
    await execution.publish(model, operator)
    await execution.set_enabled(model['id'], True, operator)
    bindings = {model['id']: BINDINGS['fixture-text']}
    minted = await execution.mint(scope, model=model['id'], invocation_key='live-filter', mode='live', bindings=bindings)
    ctx = await execution.authenticate_task(minted['task_token'])
    job = await execution.admit(ctx, key='live-filter-job', body_hash='live-filter-hash', output_limit=100, bindings=bindings)
    await execution.dispatch(job['id'], job['attempt_id'])
    await execution.pending(job['id'], job['attempt_id'], 'synthetic-live-pending')
    return job


@pytest.mark.asyncio
async def test_empty_queue_and_absent_default_review_are_read_only(db):
    from platform_service.console_reconciliation import ConsoleReconciliation
    auth, principal, _ = await admin(db)
    api = ConsoleReconciliation(db, auth)
    before = await financial_snapshot(db)
    assert await api.list(principal) == {'items': [], 'limit': 25, 'offset': 0, 'has_more': False}
    assert await counts(db) == [0, 0, 0]
    assert await financial_snapshot(db) == before


@pytest.mark.asyncio
async def test_detail_unknown_usage_and_versioned_stable_safe_snapshot(db, env):
    api, _, p, _, _, _, job = env
    before = await financial_snapshot(db)
    first, second = await api.detail(p, job['id']), await api.detail(p, job['id'])
    assert first['snapshot_hash'] == second['snapshot_hash'] and first['observed_at'] != second['observed_at']
    assert first['job']['actual'] is None and first['job']['amount'] == '6573600'
    assert first['job']['review_state'] == 'unreviewed' and first['job']['review_revision'] == 0
    assert first['review'] is None and first['history'] == {'items': [], 'has_more': False}
    assert first['usage'] == {'present': False, 'input_tokens': None, 'output_tokens': None, 'diagnostic': 'central_usage_missing'}
    raw = json.dumps(first)
    assert not any(s in raw for s in ('PRIVATE_RAW_REASON', 'attempt_id', 'provider_response_id', 'scope', 'body_hash', 'payload'))
    assert len(first['snapshot_hash']) == 64
    assert await counts(db) == [0, 0, 0] and await financial_snapshot(db) == before


@pytest.mark.asyncio
async def test_review_is_atomic_metadata_only_and_hash_ignores_review_history(db, env):
    api, _, p, _, _, _, job = env
    detail = await api.detail(p, job['id'])
    # Expiry and freezing cannot cause opportunistic posting through these commands.
    async with db.connection() as c:
        await c.execute("UPDATE credit_lots SET expires_at=now()-interval '1 day',frozen=true")
    before = await financial_snapshot(db)
    result = await api.review(p, job['id'], body(detail), 'review-metadata-001')
    assert set(result) == {'operation_id', 'job_id', 'review'}
    assert result['review']['revision'] == 1
    assert result['review']['state'] == 'investigating' and result['review']['actor_id'] == p.id
    assert detail['job']['rate'] == {'version': '1', 'input': {'numerator': '100', 'denominator': '1'}, 'output': {'numerator': '200', 'denominator': '1'}}
    assert set(result['review']) == {'state', 'revision', 'actor_id', 'reason', 'snapshot_hash', 'updated_at'}
    after = await api.detail(p, job['id'])
    assert after['snapshot_hash'] == detail['snapshot_hash']
    assert after['review'] == result['review'] and after['job']['review_revision'] == 1
    assert after['history']['items'][0]['id'] == result['operation_id']
    assert after['history']['items'][0]['reason'] == body(detail)['reason']
    assert await counts(db) == [1, 1, 1] and await financial_snapshot(db) == before


@pytest.mark.asyncio
async def test_idempotency_replays_original_even_after_later_revision(db, env):
    api, _, p, _, _, _, job = env
    detail = await api.detail(p, job['id']); first_body = body(detail)
    first = await api.review(p, job['id'], first_body, 'original-key-001')
    await api.review(p, job['id'], body(detail, revision=1, state='awaiting_evidence'), 'second-key-001')
    assert await api.review(p, job['id'], first_body, 'original-key-001') == first
    assert await api.operation(p, job['id'], 'original-key-001') == {'found': True, 'result': first}
    with pytest.raises(PlatformError) as error:
        await api.review(p, job['id'], {**first_body, 'reason': 'changed'}, 'original-key-001')
    assert error.value.status == 409 and await counts(db) == [1, 2, 2]


@pytest.mark.asyncio
async def test_original_key_lookup_does_not_cross_actor_job_or_create_rows(db, env):
    api, _, p, _, execution, ctx, job = env
    detail = await api.detail(p, job['id'])
    await api.review(p, job['id'], body(detail), 'lookup-key-001')
    _, other, _ = await admin(db)
    second = await admit(execution, ctx, 'second-job')
    before = await counts(db)
    for actor, id, key in ((other, job['id'], 'lookup-key-001'), (p, second['id'], 'lookup-key-001'), (p, job['id'], 'unknown-key-001')):
        assert await api.operation(actor, id, key) == {'found': False, 'result': None}
    assert await counts(db) == before


@pytest.mark.asyncio
async def test_same_key_concurrent_replay_and_first_registration_cas(db, env):
    api, _, p, _, _, _, job = env
    detail = await api.detail(p, job['id'])
    results = await asyncio.gather(*(api.review(p, job['id'], body(detail), 'same-key-001') for _ in range(4)))
    assert all(row == results[0] for row in results) and await counts(db) == [1, 1, 1]
    results = await asyncio.gather(*(api.review(p, job['id'], body(detail, revision=1), key) for key in ('cas-first-001', 'cas-second-001')), return_exceptions=True)
    assert sum(isinstance(row, dict) for row in results) == 1
    assert sum(isinstance(row, PlatformError) and row.status == 409 for row in results) == 1
    assert await counts(db) == [1, 2, 2]


@pytest.mark.asyncio
async def test_two_first_registrations_have_one_winner_without_orphan_audit(db, env):
    api, _, p, _, _, _, job = env
    detail = await api.detail(p, job['id'])
    results = await asyncio.gather(*(api.review(p, job['id'], body(detail), key) for key in ('first-case-001', 'first-case-002')), return_exceptions=True)
    assert sum(isinstance(row, dict) for row in results) == 1
    assert sum(isinstance(row, PlatformError) and row.status == 409 for row in results) == 1
    assert await counts(db) == [1, 1, 1]


@pytest.mark.asyncio
async def test_stale_snapshot_and_nonpending_job_refuse_new_review(db, env):
    api, _, p, _, execution, _, job = env
    detail = await api.detail(p, job['id'])
    with pytest.raises(PlatformError) as error:
        await api.review(p, job['id'], {**body(detail), 'snapshot_hash': '0' * 64}, 'stale-snapshot-001')
    assert error.value.status == 409
    await execution.finish(job['id'], job['attempt_id'], input_tokens=1, output_tokens=0, terminal_status='completed', provider_response_id='private-response', evidence_hash='private-evidence', payload=b'PRIVATE_REPLY', content_type='application/json')
    latest = await api.detail(p, job['id'])
    assert latest['snapshot_hash'] != detail['snapshot_hash'] and latest['job']['actual'] == '100'
    assert latest['usage'] == {'present': True, 'input_tokens': '1', 'output_tokens': '0', 'diagnostic': 'settled_usage_present'}
    with pytest.raises(PlatformError) as error:
        await api.review(p, job['id'], body(latest), 'finished-review-001')
    assert error.value.status == 409 and await counts(db) == [0, 0, 0]


@pytest.mark.asyncio
async def test_queue_finished_all_filters_only_include_reviewed_terminal(db, env):
    api, _, p, _, execution, ctx, job = env
    detail = await api.detail(p, job['id'])
    await api.review(p, job['id'], body(detail), 'before-finish-001')
    await execution.finish(job['id'], job['attempt_id'], input_tokens=1, output_tokens=0, terminal_status='completed', provider_response_id='r', evidence_hash='e', payload=b'{}', content_type='application/json')
    unreviewed = await admit(execution, ctx, 'unreviewed-terminal')
    await execution.dispatch(unreviewed['id'], unreviewed['attempt_id'])
    await execution.finish(unreviewed['id'], unreviewed['attempt_id'], input_tokens=1, output_tokens=0, terminal_status='completed', provider_response_id='r2', evidence_hash='e2', payload=b'{}', content_type='application/json')
    await admit(execution, ctx, 'unreviewed-active')
    assert (await api.list(p))['items'] == []
    assert [row['id'] for row in (await api.list(p, financial_status='finished'))['items']] == [job['id']]
    assert [row['id'] for row in (await api.list(p, financial_status='all'))['items']] == [job['id']]
    assert (await api.list(p, financial_status='all', review_state='unreviewed'))['items'] == []
    assert (await api.list(p, financial_status='all', product_id='other'))['items'] == []
    assert (await api.list(p, financial_status='all', q="%' OR true--"))['items'] == []


@pytest.mark.asyncio
async def test_waived_without_review_case_is_visible_as_terminal_and_queries_never_change_money(db, env):
    from platform_service.contracts import OperatorIdentity
    from platform_service.execution.reconciliation import ExecutionReconciliation

    api, _, principal, _, execution, _, job = env
    async with db.connection() as conn:
        await conn.execute("UPDATE execution_jobs SET lease_until=now()-interval '1 minute' WHERE id=%s", (job['id'],))
    operator = OperatorIdentity('synthetic-writeoff-operator', frozenset({'execution:writeoff'}))
    resolution = ExecutionReconciliation(execution)
    plan = await resolution.plan_waivers(operator, [job['id']], 'Synthetic explicitly approved platform absorption', 'synthetic-approval-only')
    await resolution.apply_waivers(operator, plan, plan['plan_sha256'], 'console-waived-fixture-001')

    before = await financial_snapshot(db)
    assert (await api.list(principal))['items'] == []
    for financial_status in ('finished', 'all'):
        rows = (await api.list(principal, financial_status=financial_status))['items']
        assert [row['id'] for row in rows] == [job['id']]
        assert rows[0]['review_state'] == 'unreviewed' and rows[0]['review_revision'] == 0
        assert rows[0]['status'] == 'waived' and rows[0]['actual'] is None
        assert rows[0]['customer_charge'] == '0' and rows[0]['resolution'] == 'platform_absorbed'
        assert rows[0]['provider_usage_known'] is False
    detail = await api.detail(principal, job['id'])
    assert detail['usage'] == {'present': False, 'input_tokens': None, 'output_tokens': None, 'diagnostic': 'waived_usage_unknown'}
    assert detail['review'] is None and detail['history'] == {'items': [], 'has_more': False}
    with pytest.raises(PlatformError) as invalid:
        await api.review(principal, job['id'], body(detail), 'no-review-after-waiver')
    assert invalid.value.status == 409
    assert await counts(db) == [0, 0, 0]
    assert await financial_snapshot(db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('kwargs', [{'limit': 0}, {'limit': True}, {'offset': -1}, {'q': 'x'*129}, {'financial_status': 'settled'}, {'review_state': 'closed'}, {'product_id': 'x'*256}, {'funding_mode': 'production'}, {'funding_mode': ''}, {'funding_mode': None}])
async def test_queue_bounds_and_fixed_filters(db, env, kwargs):
    api, _, p, *_ = env
    with pytest.raises(PlatformError) as error:
        await api.list(p, **kwargs)
    assert error.value.status == 422


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [{'state': 'closed'}, {'reason': ''}, {'reason': ' leading'}, {'reason': 'trailing '}, {'reason': 'x'*501}, {'reason': 'bad\x7f'}, {'reason': 'bad\nline'}, {'expected_revision': True}, {'expected_revision': -1}, {'snapshot_hash': 'X'*64}, {'actor_id': 'injected'}])
async def test_review_strict_body_validation(db, env, changes):
    api, _, p, _, _, _, job = env
    value = {**body(await api.detail(p, job['id'])), **changes}
    with pytest.raises(PlatformError) as error:
        await api.review(p, job['id'], value, 'invalid-body-001')
    assert error.value.status == 422 and await counts(db) == [0, 0, 0]


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ["UPDATE console_admins SET role='viewer'", "UPDATE console_admins SET active=false", "UPDATE console_admins SET authorization_epoch=authorization_epoch+1", "UPDATE console_sessions SET expires_at=now()-interval '1 second'"])
async def test_review_revalidates_current_session_acl_and_epoch(db, env, mutation):
    api, _, p, _, _, _, job = env
    value = body(await api.detail(p, job['id']))
    async with db.connection() as c:
        await c.execute(mutation)
    with pytest.raises(PlatformError) as error:
        await api.review(p, job['id'], value, 'revoked-review-001')
    assert error.value.status in (401, 403) and await counts(db) == [0, 0, 0]


@pytest.mark.asyncio
async def test_audit_insert_failure_rolls_back_case_and_operation(db, env):
    api, _, p, _, _, _, job = env
    value = body(await api.detail(p, job['id']))
    async with db.connection() as c:
        await c.execute("CREATE FUNCTION reject_review_audit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic audit rejection'; END $$")
        await c.execute('CREATE TRIGGER reject_review_audit BEFORE INSERT ON console_audit_events FOR EACH ROW EXECUTE FUNCTION reject_review_audit()')
    before = await financial_snapshot(db)
    with pytest.raises(psycopg.Error):
        await api.review(p, job['id'], value, 'failed-audit-001')
    assert await counts(db) == [0, 0, 0] and await financial_snapshot(db) == before
    assert await api.operation(p, job['id'], 'failed-audit-001') == {'found': False, 'result': None}


@pytest.mark.asyncio
async def test_review_does_not_request_update_lock_on_financial_job(db, env):
    api, _, p, _, _, _, job = env
    value = body(await api.detail(p, job['id']))
    async with db.connection() as c:
        # A foreign key protects the referenced key with KEY SHARE. That is
        # compatible with real non-key financial updates (NO KEY UPDATE), while
        # an application SELECT FOR UPDATE would wait here and fail this test.
        await c.execute('SELECT id FROM execution_jobs WHERE id=%s FOR NO KEY UPDATE', (job['id'],))
        result = await asyncio.wait_for(api.review(p, job['id'], value, 'no-financial-lock-001'), timeout=1.5)
    assert result['review']['revision'] == 1


@pytest.mark.asyncio
async def test_missing_and_invalid_job_ids_have_fixed_errors(db, env):
    api, _, p, *_ = env
    for value, status in [('not-a-uuid', 422), (str(uuid4()), 404)]:
        with pytest.raises(PlatformError) as error:
            await api.detail(p, value)
        assert error.value.status == status


@pytest.mark.asyncio
async def test_fk_key_protection_conflict_rolls_back_entire_review(db, env):
    api, _, p, _, _, _, job = env
    value = body(await api.detail(p, job['id']))
    before = await financial_snapshot(db)
    # The actual execution service explicitly locks jobs FOR UPDATE. The FK's
    # natural KEY SHARE can contend; it does not guarantee a locked snapshot.
    async with db.connection() as c:
        await c.execute('SELECT id FROM execution_jobs WHERE id=%s FOR UPDATE', (job['id'],))
        with pytest.raises(psycopg.errors.LockNotAvailable):
            await api.review(p, job['id'], value, 'fk-contention-001')
    assert await counts(db) == [0, 0, 0] and await financial_snapshot(db) == before


@pytest.mark.asyncio
async def test_financial_finish_after_observation_keeps_review_metadata_only(db, env):
    api, _, p, _, execution, _, job = env
    detail = await api.detail(p, job['id'])
    observed, resume = asyncio.Event(), asyncio.Event()
    original = api._read_job

    async def paused_read(conn, id):
        row = await original(conn, id)
        observed.set()
        await resume.wait()
        return row

    api._read_job = paused_read
    pending = asyncio.create_task(api.review(p, job['id'], body(detail), 'observed-pending-001'))
    await asyncio.wait_for(observed.wait(), 1)
    try:
        await execution.finish(job['id'], job['attempt_id'], input_tokens=1, output_tokens=0, terminal_status='completed', provider_response_id='race-response', evidence_hash='race-evidence', payload=b'{}', content_type='application/json')
        finished = await financial_snapshot(db)
    finally:
        resume.set()
    result = await pending
    api._read_job = original
    latest = await api.detail(p, job['id'])
    assert result['review']['snapshot_hash'] == detail['snapshot_hash']
    assert latest['job']['status'] == 'settled' and latest['snapshot_hash'] != detail['snapshot_hash']
    assert latest['review'] == result['review'] and await financial_snapshot(db) == finished
    assert (await api.list(p))['items'] == []
    assert (await api.list(p, financial_status='finished'))['items'][0]['id'] == job['id']


@pytest.mark.asyncio
async def test_original_post_inflight_lookup_is_nonblocking_unknown_until_commit(db, env):
    api, _, p, _, _, _, job = env
    detail = await api.detail(p, job['id'])
    key = 'inflight-operation-001'
    async with db.connection() as c:
        await c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('console-command:' + p.id + ':' + hashlib.sha256(key.encode()).hexdigest(),))
        pending = asyncio.create_task(api.review(p, job['id'], body(detail), key))
        await asyncio.sleep(0.03)
        assert not pending.done()
        assert await asyncio.wait_for(api.operation(p, job['id'], key), 1) == {'found': False, 'result': None}
    result = await pending
    assert await api.operation(p, job['id'], key) == {'found': True, 'result': result}


@pytest.mark.asyncio
async def test_detail_history_is_bounded_and_old_operation_remains_queryable(db, env):
    api, _, p, _, _, _, job = env
    detail = await api.detail(p, job['id'])
    first = None
    for revision in range(51):
        result = await api.review(p, job['id'], body(detail, revision=revision), 'history-review-' + str(revision))
        first = first or result
    latest = await api.detail(p, job['id'])
    assert latest['history']['has_more'] is True and len(latest['history']['items']) == 50
    assert [row['revision'] for row in latest['history']['items']] == list(range(51, 1, -1))
    assert await api.operation(p, job['id'], 'history-review-0') == {'found': True, 'result': first}


@pytest.mark.asyncio
async def test_queue_pagination_and_readonly_transactions(db, env):
    api, auth, p, _, execution, ctx, job = env
    second = await admit(execution, ctx, 'second-pending')
    await execution.dispatch(second['id'], second['attempt_id'])
    await execution.pending(second['id'], second['attempt_id'], 'private-second')
    original = auth.validate

    async def readonly_validate(conn, principal, permission):
        assert (await (await conn.execute('SHOW transaction_read_only')).fetchone())['transaction_read_only'] == 'on'
        return await original(conn, principal, permission)

    auth.validate = readonly_validate
    before = await financial_snapshot(db)
    first_page = await api.list(p, limit=1)
    second_page = await api.list(p, limit=1, offset=1)
    assert first_page['has_more'] is True and second_page['has_more'] is False
    assert first_page['items'][0]['id'] == second['id'] and second_page['items'][0]['id'] == job['id']
    await api.detail(p, job['id'])
    await api.operation(p, job['id'], 'not-found-key-001')
    assert await financial_snapshot(db) == before and await counts(db) == [0, 0, 0]


@pytest.mark.asyncio
async def test_real_restricted_console_role_queries_reviews_without_financial_grants(db, env, test_dsn):
    from platform_service.console_reconciliation import ConsoleReconciliation
    from platform_service.console_permissions import grant_console_permissions, assert_safe_console_role
    from platform_service.db import Database
    _, auth, p, _, _, _, job = env
    role, password = 'console_review_' + uuid4().hex, secrets.token_urlsafe(24)
    async with db.connection() as c:
        await c.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(role), sql.Literal(password)))
        await grant_console_permissions(c, role)
    low = Database(make_conninfo(test_dsn, user=role, password=password), schema=db.schema)
    try:
        await low.open()
        async with low.connection() as c:
            await assert_safe_console_role(c)
        service = ConsoleReconciliation(low, ConsoleAuth(low, auth.settings))
        detail = await service.detail(p, job['id'])
        before = await financial_snapshot(db)
        first = await service.review(p, job['id'], body(detail), 'low-role-review-001')
        second = await service.review(p, job['id'], body(detail, revision=1, state='disputed'), 'low-role-review-002')
        assert second['review']['revision'] == 2
        assert (await service.list(p))['items'][0]['review_state'] == 'disputed'
        assert await service.operation(p, job['id'], 'low-role-review-001') == {'found': True, 'result': first}
        for statement in ('SELECT reason FROM execution_jobs', 'SELECT scope FROM execution_jobs', 'SELECT attempt_id FROM execution_receipts', 'SELECT provider_response_id FROM execution_receipts', 'SELECT payload FROM execution_results', 'UPDATE execution_jobs SET actual=0', 'UPDATE credit_lots SET available=0', 'DELETE FROM console_reconciliation_cases'):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                async with low.connection() as c:
                    await c.execute(statement)
        assert await financial_snapshot(db) == before
    finally:
        await low.close()
        async with db.connection() as c:
            await c.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
            await c.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


@pytest.mark.asyncio
async def test_idempotency_key_collision_with_other_action_never_parses_other_result(db, env):
    api, _, p, _, _, _, job = env
    key, operation_id = 'shared-catalog-key-001', uuid4().hex
    key_hash, request_hash = hashlib.sha256(key.encode()).hexdigest(), '0' * 64
    # Legacy/other catalog results are not reconciliation-shaped. A key collision
    # must yield 409 without attempting to coerce arbitrary result metadata.
    async with db.connection() as c:
        await c.execute("INSERT INTO console_operations(id,actor_id,key_hash,request_hash,action,target_type,target_id,result) VALUES (%s,%s,%s,%s,'plan.publish','plan','fixture-plan-v1',%s)", (operation_id, p.id, key_hash, request_hash, Jsonb({'review': {'revision': 'other-shape'}})))
        await c.execute("INSERT INTO console_audit_events(operation_id,actor_id,action,target_type,target_id,reason,key_hash,request_hash) VALUES (%s,%s,'plan.publish','plan','fixture-plan-v1','Fixture',%s,%s)", (operation_id, p.id, key_hash, request_hash))
    value = body(await api.detail(p, job['id']))
    with pytest.raises(PlatformError) as error:
        await api.review(p, job['id'], value, key)
    assert error.value.status == 409 and await counts(db) == [0, 1, 1]


@pytest.mark.asyncio
async def test_independent_detail_uses_the_job_wallet_and_product_not_list_state(db, env):
    from platform_service.contracts import Scope, OperatorIdentity
    from platform_service.membership import MembershipService
    from test_execution_core import BINDINGS
    api, _, p, _, execution, _, first = env
    await MembershipService(db).issue_test(OperatorIdentity('fixture', frozenset({'test:issue'})), 'foreign-wallet', 'fixture-plan-v1', 'foreign-grant')
    async with db.connection() as c:
        await c.execute("INSERT INTO external_identities(issuer,subject,user_id) VALUES ('fixture','other-subject','other')")
        await c.execute("INSERT INTO identity_sessions(id,user_id,tenant_id,billing_account_id,product_id,client_id,authorization_epoch,issuer,subject,expires_at) VALUES ('other-session','other','foreign','foreign-wallet','easel','desktop',0,'fixture','other-subject',now()+interval '1 hour')")
    scope = Scope('other', 'easel', 'foreign', 'foreign-wallet', 'other-session', 0)
    minted = await execution.mint(scope, model='fixture-text', invocation_key='foreign-run', mode='test', bindings=BINDINGS)
    ctx = await execution.authenticate_task(minted['task_token'])
    second = await admit(execution, ctx, 'foreign-operation')
    await execution.dispatch(second['id'], second['attempt_id'])
    await execution.pending(second['id'], second['attempt_id'], 'PRIVATE_FOREIGN_REASON')
    listed = await api.list(p)
    assert {row['billing_account_id'] for row in listed['items']} == {'wallet', 'foreign-wallet'}
    first_detail, second_detail = await api.detail(p, first['id']), await api.detail(p, second['id'])
    assert first_detail['job']['billing_account_id'] == 'wallet'
    assert second_detail['job']['billing_account_id'] == 'foreign-wallet' and second_detail['job']['product_id'] == 'easel'
    assert first_detail['snapshot_hash'] != second_detail['snapshot_hash']


@pytest.mark.asyncio
async def test_funding_filter_isolates_real_live_and_test_reservations_readonly(db, env):
    api, _, p, _, _, _, test_job = env
    live_job = await live_pending(db, env)
    before = await financial_snapshot(db)
    all_rows = (await api.list(p, funding_mode='all'))['items']
    assert {row['id'] for row in all_rows} == {test_job['id'], live_job['id']}
    assert {row['id'] for row in (await api.list(p))['items']} == {test_job['id'], live_job['id']}
    for mode, id in (('test', test_job['id']), ('live', live_job['id'])):
        result = await api.list(p, funding_mode=mode)
        assert [row['id'] for row in result['items']] == [id]
        assert result['items'][0]['funding_mode'] == mode and result['has_more'] is False
    assert await financial_snapshot(db) == before and await counts(db) == [0, 0, 0]
