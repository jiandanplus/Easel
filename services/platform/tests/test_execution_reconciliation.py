"""Real PostgreSQL checks for audited, explicit platform write-offs."""
import asyncio
from copy import deepcopy

import psycopg
import pytest

from platform_service.contracts import OperatorIdentity
from platform_service.errors import PlatformError
from test_execution_core import admit, setup

WRITEOFF = OperatorIdentity('host-writeoff-fixture', frozenset({'execution:writeoff'}))


def test_reconciliation_contract_is_independent():
    from platform_service.execution.reconciliation import ExecutionReconciliation
    assert callable(ExecutionReconciliation)


async def pending(db, *, count=1):
    from platform_service.execution.reconciliation import ExecutionReconciliation
    service, ledger, scope, context = await setup(db)
    jobs = []
    for number in range(count):
        job = await admit(service, context, 'writeoff-' + str(number))
        await service.dispatch(job['id'], job['attempt_id'])
        await service.pending(job['id'], job['attempt_id'], 'upstream_uncertain')
        jobs.append(job)
    async with db.connection() as conn:
        await conn.execute("UPDATE execution_jobs SET lease_until=now()-interval '1 second'")
    return ExecutionReconciliation(service), service, ledger, scope, context, jobs


async def plan(reconciler, jobs, **kwargs):
    return await reconciler.plan_waivers(WRITEOFF, [job['id'] for job in jobs],
        kwargs.get('reason', 'Historical usage cannot be established; platform absorbs the cost.'),
        kwargs.get('approval_reference', 'explicit-user-approval-fixture'))


async def snapshot(db):
    tables = ['credit_lots','credit_reservations','credit_holds','credit_journals',
              'credit_entries','execution_jobs','execution_invocations','execution_receipts',
              'execution_results','execution_resolutions','execution_resolution_operations']
    async with db.connection() as conn:
        return {table: await (await conn.execute('SELECT to_jsonb(t) row FROM ' + table + ' t ORDER BY to_jsonb(t)::text')).fetchall() for table in tables}


@pytest.mark.asyncio
async def test_plan_is_read_only_and_apply_preserves_unknown_actual_without_fake_reply(db):
    r, service, ledger, scope, ctx, jobs = await pending(db, count=3)
    before = await snapshot(db)
    p = await plan(r, jobs)
    assert await snapshot(db) == before
    result = await r.apply_waivers(WRITEOFF, p, p['plan_sha256'], 'writeoff-three')
    assert result['released_microcredits'] == str(sum(j['amount'] for j in jobs))
    assert all(i['status'] == 'waived' and i['actual'] is None and i['customer_charge'] == '0'
               and i['provider_usage_known'] is False for i in result['items'])
    wallet = await ledger.summary(scope)
    assert wallet['reserved'] == '0' and wallet['spent'] == '0' and wallet['pending_reconciliation'] == 0
    async with db.connection() as conn:
        assert not await (await conn.execute('SELECT 1 FROM execution_receipts')).fetchall()
        assert not await (await conn.execute('SELECT 1 FROM execution_results')).fetchall()
        rows = await (await conn.execute('SELECT status,actual FROM credit_reservations')).fetchall()
        assert all(x['status'] == 'waived' and x['actual'] is None for x in rows)
    frozen = await snapshot(db)
    assert await r.apply_waivers(WRITEOFF, p, p['plan_sha256'], 'writeoff-three') == result
    assert await r.operation(WRITEOFF, 'writeoff-three') == result
    assert await snapshot(db) == frozen
    assert (await service.lookup_operation(scope, ctx['invocation_id'], 'writeoff-0'))['result_state'] == 'not_available'
    assert (await admit(service, ctx, 'next-after-waiver'))['status'] == 'reserved'


@pytest.mark.asyncio
async def test_writeoff_capability_is_not_implied_by_publish_or_review(db):
    r, _, _, _, _, jobs = await pending(db)
    for caps in [frozenset(),frozenset({'execution:publish'}),frozenset({'execution:review'})]:
        actor = OperatorIdentity('wrong-capability', caps)
        with pytest.raises(PlatformError) as error:
            await r.plan_waivers(actor, [jobs[0]['id']], 'reason', 'approval')
        assert error.value.status == 403
    with pytest.raises(PlatformError) as error:
        await r.operation(WRITEOFF, 'missing')
    assert error.value.status == 404


@pytest.mark.asyncio
async def test_stale_changed_or_tampered_plan_cannot_write(db):
    r, _, _, _, _, jobs = await pending(db)
    p = await plan(r, jobs)
    before = await snapshot(db)
    tampered = deepcopy(p);tampered['items'][0]['amount'] = '1'
    for value, reviewed in [(tampered,p['plan_sha256']),(p,'0'*64)]:
        with pytest.raises(PlatformError):
            await r.apply_waivers(WRITEOFF, value, reviewed, 'tampered')
        assert await snapshot(db) == before
    async with db.connection() as conn:
        await conn.execute("UPDATE execution_jobs SET reason='another_internal_reason'")
    before = await snapshot(db)
    with pytest.raises(PlatformError) as error:
        await r.apply_waivers(WRITEOFF, p, p['plan_sha256'], 'stale')
    assert error.value.status == 409
    assert await snapshot(db) == before


@pytest.mark.asyncio
async def test_same_key_concurrent_apply_runs_once_and_different_plan_conflicts(db):
    r, _, ledger, scope, _, jobs = await pending(db)
    p = await plan(r, jobs)
    results = await asyncio.gather(*[r.apply_waivers(WRITEOFF,p,p['plan_sha256'],'same-key') for _ in range(4)])
    assert all(x == results[0] for x in results)
    assert (await ledger.summary(scope))['reserved'] == '0'
    changed = deepcopy(p);changed['reason'] = 'Different operator decision'
    from platform_service.execution.reconciliation import plan_digest
    changed['plan_sha256'] = plan_digest(changed)
    before = await snapshot(db)
    with pytest.raises(PlatformError) as error:
        await r.apply_waivers(WRITEOFF,changed,changed['plan_sha256'],'same-key')
    assert error.value.code == 'idempotency_conflict'
    assert await snapshot(db) == before


@pytest.mark.asyncio
async def test_expired_held_lot_does_not_return_to_available(db):
    r, _, ledger, scope, _, jobs = await pending(db)
    async with db.connection() as conn:
        await conn.execute("UPDATE credit_lots SET expires_at=now()-interval '1 day'")
    p = await plan(r, jobs)
    await r.apply_waivers(WRITEOFF,p,p['plan_sha256'],'expired')
    value = await ledger.summary(scope)
    assert value['available'] == '0' and value['reserved'] == '0' and value['spent'] == '0'
    assert value['expired'] == '300000000'


@pytest.mark.asyncio
async def test_unexpired_pending_lease_is_not_eligible(db):
    r, _, _, _, _, jobs = await pending(db)
    async with db.connection() as conn:
        await conn.execute("UPDATE execution_jobs SET lease_until=now()+interval '1 hour'")
    before = await snapshot(db)
    with pytest.raises(PlatformError):
        await plan(r, jobs)
    assert await snapshot(db) == before


@pytest.mark.asyncio
async def test_late_finish_cannot_charge_waived_job(db):
    r, service, _, _, _, jobs = await pending(db)
    p = await plan(r, jobs);await r.apply_waivers(WRITEOFF,p,p['plan_sha256'],'before-late-finish')
    before = await snapshot(db)
    with pytest.raises(PlatformError):
        await service.finish(jobs[0]['id'], jobs[0]['attempt_id'], input_tokens=10,
            output_tokens=2,terminal_status='completed',provider_response_id='late',
            evidence_hash='late-evidence',payload=b'{}',content_type='application/json')
    assert await snapshot(db) == before


@pytest.mark.asyncio
async def test_direct_unaudited_waiver_and_audit_tampering_are_rejected(db):
    r, service, _, _, _, jobs = await pending(db)
    before = await snapshot(db)
    with pytest.raises(psycopg.Error) as error:
        async with db.connection() as conn:
            row,scope,reservation = await service._job(conn,jobs[0]['id'],jobs[0]['attempt_id'])
            await service.ledger._complete(conn,scope,reservation,0,'fake','waived')
            await conn.execute("UPDATE execution_invocations SET held=held-%s WHERE id=%s",(row['amount'],row['invocation_id']))
            await conn.execute("UPDATE execution_jobs SET status='waived',actual=NULL WHERE id=%s",(row['id'],))
    assert 'unaudited write-off forbidden' in str(error.value)
    assert await snapshot(db) == before
    p = await plan(r,jobs);await r.apply_waivers(WRITEOFF,p,p['plan_sha256'],'valid-audit')
    for statement in ["UPDATE execution_resolutions SET customer_charge=1","DELETE FROM execution_resolutions","TRUNCATE execution_resolution_operations","UPDATE execution_jobs SET actual=0 WHERE status='waived'","UPDATE credit_reservations SET actual=0 WHERE status='waived'"]:
        with pytest.raises(psycopg.Error):
            async with db.connection() as conn:await conn.execute(statement)


@pytest.mark.asyncio
async def test_batch_stale_member_rolls_back_every_job(db):
    r, _, _, _, _, jobs = await pending(db,count=2)
    p = await plan(r,jobs)
    async with db.connection() as conn:
        await conn.execute("UPDATE execution_jobs SET reason='changed' WHERE id=%s",(jobs[1]['id'],))
    before = await snapshot(db)
    with pytest.raises(PlatformError):await r.apply_waivers(WRITEOFF,p,p['plan_sha256'],'stale-batch')
    assert await snapshot(db) == before


@pytest.mark.asyncio
async def test_failure_after_financial_write_rolls_back_audit_and_batch(db,monkeypatch):
    r, service, _, _, _, jobs = await pending(db,count=2)
    p = await plan(r,jobs);before=await snapshot(db)
    complete=service.ledger._complete
    async def fail_after_complete(*args,**kwargs):
        await complete(*args,**kwargs)
        raise RuntimeError('fixture failure after wallet mutation')
    monkeypatch.setattr(service.ledger,'_complete',fail_after_complete)
    with pytest.raises(RuntimeError):await r.apply_waivers(WRITEOFF,p,p['plan_sha256'],'atomic-failure')
    assert await snapshot(db)==before
    with pytest.raises(PlatformError) as error:await r.operation(WRITEOFF,'atomic-failure')
    assert error.value.status==404


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value',[('attempt_id','wrong-attempt'),('binding_hash','foreign-binding'),('scope_hash','f'*64),('reservation_id','foreign-reservation')])
async def test_reviewed_plan_cannot_rebind_attempt_scope_or_reservation(db,field,value):
    from platform_service.execution.reconciliation import plan_digest
    r, _, _, _, _, jobs = await pending(db)
    p=await plan(r,jobs);p['items'][0][field]=value;p['plan_sha256']=plan_digest(p)
    before=await snapshot(db)
    with pytest.raises(PlatformError):await r.apply_waivers(WRITEOFF,p,p['plan_sha256'],'foreign-binding')
    assert await snapshot(db)==before


@pytest.mark.asyncio
async def test_runtime_can_settle_same_wallet_after_owner_waiver_without_audit_privileges(db,test_dsn):
    import secrets
    from uuid import uuid4
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from platform_service.db import Database
    from platform_service.execution.core import ExecutionService
    from platform_service.identity import IdentityService
    from platform_service.permissions import grant_runtime_permissions
    r, _, ledger, scope, context, jobs=await pending(db)
    p=await plan(r,jobs);await r.apply_waivers(WRITEOFF,p,p['plan_sha256'],'owner-writeoff')
    role='writeoff_runtime_'+uuid4().hex;password=secrets.token_urlsafe(24)
    async with db.connection() as conn:
        await conn.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(role),sql.Literal(password)))
        await grant_runtime_permissions(conn,role)
    runtime=Database(make_conninfo(test_dsn,user=role,password=password),schema=db.schema)
    try:
        await runtime.open();await runtime.require_runtime_role()
        service=ExecutionService(runtime,IdentityService(runtime,None))
        job=await admit(service,context,'new-runtime-after-owner-writeoff')
        await service.dispatch(job['id'],job['attempt_id'])
        await service.finish(job['id'],job['attempt_id'],input_tokens=10,output_tokens=2,
            terminal_status='completed',provider_response_id='runtime-after-waiver',evidence_hash='evidence',
            payload=b'{}',content_type='application/json')
        assert (await ledger.summary(scope))['spent']=='1400'
        pending_job=await admit(service,context,'runtime-unaudited-waiver')
        await service.dispatch(pending_job['id'],pending_job['attempt_id'])
        await service.pending(pending_job['id'],pending_job['attempt_id'],'upstream_uncertain')
        async with db.connection() as conn:
            await conn.execute("UPDATE execution_jobs SET lease_until=now()-interval '1 second' WHERE id=%s",(pending_job['id'],))
        before=await snapshot(db)
        with pytest.raises(psycopg.Error):
            async with runtime.connection() as conn:
                row,frozen,reservation=await service._job(conn,pending_job['id'],pending_job['attempt_id'])
                await service.ledger._complete(conn,frozen,reservation,0,'unaudited','waived')
                await conn.execute('UPDATE execution_invocations SET held=held-%s WHERE id=%s',(row['amount'],row['invocation_id']))
                await conn.execute("UPDATE execution_jobs SET status='waived',actual=NULL WHERE id=%s",(row['id'],))
        assert await snapshot(db)==before
    finally:
        await runtime.close()
        async with db.connection() as conn:
            await conn.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
            await conn.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


@pytest.mark.asyncio
async def test_null_unknown_actual_cannot_hide_a_nonzero_release_charge(db,monkeypatch):
    from platform_service.credits import journal
    r, service, _, _, _, jobs=await pending(db)
    p=await plan(r,jobs);before=await snapshot(db)
    async def malformed_release(conn,scope,reservation,actual,key,status):
        # Deliberately simulate an incorrect low-level implementation. A
        # balanced wallet journal alone must not let unknown SQL NULL bypass
        # the waived customer's explicit charge of zero.
        hold=await (await conn.execute('SELECT lot_id,amount FROM credit_holds WHERE reservation_id=%s',(reservation['id'],))).fetchone()
        amount=hold['amount']
        await conn.execute('UPDATE credit_lots SET held=held-%s,spent=spent+1,available=available+%s WHERE id=%s',
                           (amount,amount-1,hold['lot_id']))
        await journal(conn,scope.billing_account_id,'release',reservation['id'],{'held':-amount,'spent':1,'available':amount-1})
        await conn.execute("UPDATE credit_reservations SET status='waived',actual=NULL,completion_key=%s WHERE id=%s",(key,reservation['id']))
    monkeypatch.setattr(service.ledger,'_complete',malformed_release)
    with pytest.raises(psycopg.errors.CheckViolation) as error:
        await r.apply_waivers(WRITEOFF,p,p['plan_sha256'],'malformed-null-release')
    assert 'terminal journal amount divergence' in str(error.value)
    assert await snapshot(db)==before


@pytest.mark.asyncio
async def test_terminal_evidence_arriving_after_plan_prevents_writeoff(db):
    from platform_service.execution.reconciliation import ExecutionReconciliation
    service,_,_,context=await setup(db)
    job=await admit(service,context,'late-credible-terminal')
    await service.record_provider_attempt(job['id'],job['attempt_id'],provider_scope_hash='c'*64,
        provider_request_hash='a'*64,upstream_model='fixture-upstream',request_id='req-late-terminal')
    await service.dispatch(job['id'],job['attempt_id'])
    await service.pending(job['id'],job['attempt_id'],'upstream_uncertain')
    async with db.connection() as conn:
        await conn.execute("UPDATE execution_jobs SET lease_until=now()-interval '1 second'")
    r=ExecutionReconciliation(service);p=await plan(r,[job])
    await service.record_provider_association(job['id'],job['attempt_id'],http_status=200,http_request_id='http-late')
    await service.record_terminal_evidence(job['id'],job['attempt_id'],terminal={
        'id':'resp-late','status':'completed','usage':{'input_tokens':10,'output_tokens':2}},evidence_hash='b'*64)
    before=await snapshot(db)
    with pytest.raises(PlatformError) as error:await r.apply_waivers(WRITEOFF,p,p['plan_sha256'],'has-terminal')
    assert error.value.code=='execution_evidence_available'
    assert await snapshot(db)==before
    with pytest.raises(PlatformError):await plan(r,[job])


@pytest.mark.asyncio
async def test_database_refuses_inserting_a_new_waived_job_or_reservation(db):
    _,_,_,_,_,jobs=await pending(db)
    before=await snapshot(db)
    for table in ['execution_jobs','credit_reservations']:
        with pytest.raises(psycopg.errors.CheckViolation) as error:
            async with db.connection() as conn:
                await conn.execute('INSERT INTO '+table+' SELECT (jsonb_populate_record(NULL::'+table+",to_jsonb(t)||jsonb_build_object('status','waived','actual',NULL,'id','forged-new-row'))).* FROM "+table+' t LIMIT 1')
        assert 'write-off requires an existing pending record' in str(error.value)
    assert await snapshot(db)==before


@pytest.mark.asyncio
async def test_plan_transaction_rejects_an_accidental_write(db,monkeypatch):
    r,_,_,_,_,jobs=await pending(db)
    original=r._item
    async def accidental_write(conn,job_id,**kwargs):
        await conn.execute('UPDATE execution_jobs SET reason=reason WHERE id=%s',(job_id,))
        return await original(conn,job_id,**kwargs)
    monkeypatch.setattr(r,'_item',accidental_write)
    before=await snapshot(db)
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):await plan(r,jobs)
    assert await snapshot(db)==before
