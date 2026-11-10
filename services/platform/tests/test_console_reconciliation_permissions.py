"""Real PostgreSQL constraints for Console-only review metadata; never financial writes."""
import hashlib
from uuid import uuid4
from datetime import datetime, timezone
import pytest
import psycopg
from psycopg.types.json import Jsonb
from test_console_auth import console, authenticated
from test_console_permissions import console_role
from test_execution_core import setup, admit


def test_review_capability_is_metadata_only():
    from platform_service.console_auth import ROLES
    for role in ('admin','operator'):
        assert 'execution:review' in ROLES[role]
        assert 'execution:reconcile' not in ROLES[role]
    assert 'execution:review' not in ROLES['viewer']


@pytest.mark.asyncio
async def test_review_table_and_console_role_have_only_metadata_authority(db, console_role):
    from platform_service.console_permissions import assert_safe_console_role
    low,_=console_role
    async with low.connection() as c:
        await assert_safe_console_role(c)
        row=await (await c.execute("SELECT has_any_column_privilege(current_user,'console_reconciliation_cases','SELECT') r,has_any_column_privilege(current_user,'console_reconciliation_cases','INSERT') i,has_table_privilege(current_user,'console_reconciliation_cases','INSERT') table_insert,has_column_privilege(current_user,'console_reconciliation_cases','state','UPDATE') w,has_column_privilege(current_user,'console_reconciliation_cases','job_id','UPDATE') j,has_column_privilege(current_user,'console_reconciliation_cases','created_at','UPDATE') t,has_table_privilege(current_user,'console_reconciliation_cases','DELETE,TRUNCATE') d")).fetchone()
        assert row==dict(r=True,i=True,table_insert=False,w=True,j=False,t=False,d=False)
        for table in ('credit_lots','credit_reservations','execution_jobs','execution_receipts','credit_entries'):
            assert not (await (await c.execute("SELECT has_any_column_privilege(current_user,%s,'INSERT,UPDATE') w",(table,))).fetchone())['w']


async def seeded(db, console):
    _,principal=await authenticated(console)
    service,ledger,scope,context=await setup(db)
    admitted=await admit(service,context)
    return admitted['id'],principal.id


async def insert_case(db, job, actor, *, matching=True, audited=True):
    op=uuid4().hex;stamp=datetime.now(timezone.utc);key=hashlib.sha256(op.encode()).hexdigest()
    review=dict(state='investigating',revision=1,actor_id=actor,reason='Synthetic metadata review',snapshot_hash='a'*64,updated_at=stamp.isoformat())
    result=dict(operation_id=op,job_id=job,review={**review,'state':'disputed'} if not matching else review)
    async with db.connection() as c:
        await c.execute("INSERT INTO console_reconciliation_cases(job_id,state,revision,actor_id,reason,snapshot_hash,created_at,updated_at,last_operation_id) VALUES (%s,'investigating',1,%s,%s,%s,%s,%s,%s)",(job,actor,review['reason'],review['snapshot_hash'],stamp,stamp,op))
        await c.execute("INSERT INTO console_operations(id,actor_id,key_hash,request_hash,action,target_type,target_id,result) VALUES (%s,%s,%s,%s,'execution.review','execution',%s,%s)",(op,actor,key,key,job,Jsonb(result)))
        if audited:
            await c.execute("INSERT INTO console_audit_events(operation_id,actor_id,action,target_type,target_id,reason,key_hash,request_hash) VALUES (%s,%s,'execution.review','execution',%s,%s,%s,%s)",(op,actor,job,review['reason'],key,key))
    return op


@pytest.mark.asyncio
async def test_review_metadata_and_immutable_audit_are_bound(db,console):
    job,actor=await seeded(db,console)
    op=await insert_case(db,job,actor)
    async with db.connection() as c:
        assert (await (await c.execute('SELECT last_operation_id FROM console_reconciliation_cases')).fetchone())['last_operation_id']==op


@pytest.mark.asyncio
@pytest.mark.parametrize('matching,audited',[(False,True),(True,False)])
async def test_mismatched_or_missing_review_audit_rolls_back_case(db,console,matching,audited):
    job,actor=await seeded(db,console)
    with pytest.raises(psycopg.errors.CheckViolation):
        await insert_case(db,job,actor,matching=matching,audited=audited)
    async with db.connection() as c:
        for table in ('console_reconciliation_cases','console_operations','console_audit_events'):
            assert (await (await c.execute('SELECT count(*) n FROM '+table)).fetchone())['n']==0


@pytest.mark.asyncio
async def test_case_update_without_new_operation_or_revision_is_rejected(db,console):
    job,actor=await seeded(db,console);await insert_case(db,job,actor)
    for clause in ["state='disputed'",'revision=revision+1',"snapshot_hash=repeat('b',64)"]:
        with pytest.raises(psycopg.errors.CheckViolation):
            async with db.connection() as c:await c.execute('UPDATE console_reconciliation_cases SET '+clause)


@pytest.mark.asyncio
async def test_low_console_case_write_cannot_bypass_audit(db,console,console_role):
    job,actor=await seeded(db,console);low,_=console_role
    with pytest.raises((psycopg.errors.CheckViolation,psycopg.errors.ForeignKeyViolation)):
        async with low.connection() as c:
            await c.execute("INSERT INTO console_reconciliation_cases(job_id,state,revision,actor_id,reason,snapshot_hash,last_operation_id) VALUES (%s,'investigating',1,%s,'Synthetic reason',%s,%s)",(job,actor,'a'*64,uuid4().hex))


@pytest.mark.asyncio
async def test_review_operation_cannot_commit_without_matching_case(db,console):
    job,actor=await seeded(db,console);key='b'*64
    with pytest.raises(psycopg.errors.CheckViolation):
        async with db.connection() as c:
            op=uuid4().hex
            await c.execute("INSERT INTO console_operations(id,actor_id,key_hash,request_hash,action,target_type,target_id,result) VALUES (%s,%s,%s,%s,'execution.review','execution',%s,%s)",(op,actor,key,key,job,Jsonb(dict(operation_id=op,job_id=job,review={}))))
            await c.execute("INSERT INTO console_audit_events(operation_id,actor_id,action,target_type,target_id,reason,key_hash,request_hash) VALUES (%s,%s,'execution.review','execution',%s,'Synthetic reason',%s,%s)",(op,actor,job,key,key))


@pytest.mark.asyncio
async def test_case_owner_cannot_delete_or_truncate_review_history(db,console):
    job,actor=await seeded(db,console);await insert_case(db,job,actor)
    for statement in ['DELETE FROM console_reconciliation_cases','TRUNCATE console_reconciliation_cases']:
        with pytest.raises(psycopg.errors.CheckViolation):
            async with db.connection() as c:await c.execute(statement)


@pytest.mark.asyncio
async def test_console_guard_rejects_grant_to_case_identity_columns(db,console_role):
    from platform_service.console_permissions import assert_safe_console_role
    from psycopg import sql
    low,role=console_role
    async with db.connection() as c:
        await c.execute(sql.SQL('GRANT UPDATE(job_id) ON console_reconciliation_cases TO {}').format(sql.Identifier(role)))
    with pytest.raises(Exception) as failure:
        async with low.connection() as c:await assert_safe_console_role(c)
    assert getattr(failure.value,'code',None)=='unsafe_console_role'


@pytest.mark.asyncio
@pytest.mark.parametrize('privilege',['SELECT','INSERT'])
async def test_console_guard_rejects_accidental_table_grant_for_column_only_case(db,console_role,privilege):
    from platform_service.console_permissions import assert_safe_console_role
    from platform_service.errors import PlatformError
    from psycopg import sql
    low,role=console_role
    async with db.connection() as c:
        await c.execute(sql.SQL('GRANT '+privilege+' ON console_reconciliation_cases TO {}').format(sql.Identifier(role)))
    with pytest.raises(PlatformError) as failure:
        async with low.connection() as c:await assert_safe_console_role(c)
    assert failure.value.code=='unsafe_console_role'
