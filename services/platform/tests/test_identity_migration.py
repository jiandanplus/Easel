"""Real owned-PG identity-continuity contracts; no external identity or provider calls."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest
import pytest_asyncio

from platform_service.contracts import OperatorIdentity
from platform_service.errors import PlatformError
from platform_service.identity_migration import MigrationPolicy, VerifiedRestoreEvidence, IdentityMigrationService
from test_identity import identity, login
from test_console_auth import console, authenticated, signed_login, bootstrap
from test_console_permissions import console_role


def operator(capability='identity:migrate'):
    return OperatorIdentity('synthetic-maintainer', frozenset({capability}))


def evidence(policy, **changes):
    value=VerifiedRestoreEvidence(policy_sha256=policy.sha256, backup_sha256=policy.approved_backup_sha256,
        schema_sha256='b'*64,data_sha256='c'*64,source_context_sha256='d'*64,target_context_sha256='e'*64,
        tenant_id=policy.tenant_id,user_ids=('subject-1',),client_ids=policy.client_ids,
        verified_at=datetime.now(timezone.utc))
    return replace(value,**changes)


@pytest_asyncio.fixture
async def continuity(db,identity,console):
    product=await login(identity)
    session,principal=await authenticated(console,'operator')
    policy=MigrationPolicy('synthetic-continuity',identity[1].issuer,'https://new-identity.example/oidc',
        'default',('oidc-client',),'a'*64)
    return IdentityMigrationService(db,policy),policy,product,session,principal


async def proof(continuity):
    service,policy,*_=continuity
    return await service.record_proof(evidence(policy),operator('identity:collect'))


async def snapshot(db):
    tables=('users','tenants','tenant_members','billing_accounts','memberships','credit_lots','credit_reservations',
        'credit_journals','credit_entries','identity_sessions','identity_tokens','identity_handoffs',
        'console_admins','console_sessions','billing_orders','execution_jobs','internal_grants','plans',
        'credit_holds','execution_invocations','execution_receipts','execution_results','execution_task_tokens')
    async with db.connection() as c:
        return {t:await (await c.execute(sql.SQL('SELECT * FROM {} ORDER BY to_jsonb({})::text').format(sql.Identifier(t),sql.Identifier(t)))).fetchall() for t in tables}


def test_policy_is_exact_and_rejects_unsafe_or_unbounded_identity_configuration():
    p=MigrationPolicy('env-1','http://127.0.0.1:18763/oidc','https://login.example/oidc','default',('b','a'),'a'*64)
    assert len(p.sha256)==64
    assert p.sha256==replace(p,client_ids=('a','b')).sha256
    assert p.sha256!=replace(p,target_issuer='https://login.example:443/oidc').sha256
    for changes in ({'target_issuer':p.source_issuer},{'target_issuer':'https://0x7f.0.0.0x1/oidc'},
            {'source_issuer':'http://localhost:18763/oidc'},{'client_ids':()}, {'client_ids':('a','a')},
            {'approved_backup_sha256':'not-a-hash'},{'environment':'bad\nvalue'}):
        with pytest.raises(PlatformError):replace(p,**changes)


@pytest.mark.asyncio
async def test_proof_freezes_only_central_candidates_and_expires_in_one_hour(continuity,db):
    service,_,*_=continuity
    result=await proof(continuity)
    assert result['product_count']==result['console_count']==1
    assert 'subject-1' not in json.dumps(result)
    async with db.connection() as c:
        row=await (await c.execute('SELECT * FROM identity_restore_proofs WHERE id=%s',(result['proof_id'],))).fetchone()
        assert (row['expires_at']-row['created_at']).total_seconds()==3600
        assert len(row['candidates'])==2
    report=await service.inspect(result['proof_id'],operator())
    assert report['conflict_count']==0 and 'subject-1' not in json.dumps(report)


@pytest.mark.asyncio
@pytest.mark.parametrize('changes',[{'policy_sha256':'f'*64},{'backup_sha256':'f'*64},{'tenant_id':'different'},
    {'source_context_sha256':'e'*64},{'user_ids':()},{'user_ids':('unrelated',)}, {'client_ids':()},
    {'schema_sha256':'x'},{'verified_at':datetime(2000,1,1,tzinfo=timezone.utc)},
    {'verified_at':datetime.now(timezone.utc)+timedelta(minutes=5)}])
async def test_invalid_or_incomplete_evidence_never_records_proof(continuity,db,changes):
    service,policy,*_=continuity
    with pytest.raises(PlatformError):await service.record_proof(evidence(policy,**changes),operator('identity:collect'))
    async with db.connection() as c:
        assert (await (await c.execute('SELECT count(*) n FROM identity_restore_proofs')).fetchone())['n']==0


@pytest.mark.asyncio
async def test_capabilities_empty_candidates_and_ambiguous_wallet_fail_closed(db,continuity):
    service,policy,product,*_=continuity
    with pytest.raises(PlatformError):await service.record_proof(evidence(policy),operator())
    result=await proof(continuity)
    with pytest.raises(PlatformError):await service.apply(result['proof_id'],'key','reason',operator('identity:collect'))
    with pytest.raises(PlatformError):await service.inspect(result['proof_id'],operator('identity:collect'))
    async with db.connection() as c:
        await c.execute('INSERT INTO billing_accounts(id,tenant_id) VALUES (%s,%s)',('ambiguous-wallet',product['scope']['tenant_id']))
    with pytest.raises(PlatformError):await service.record_proof(evidence(policy),operator('identity:collect'))
    empty=IdentityMigrationService(db,replace(policy,source_issuer='https://absent.example/oidc'))
    with pytest.raises(PlatformError):await empty.record_proof(evidence(empty.policy),operator('identity:collect'))


@pytest.mark.asyncio
async def test_apply_preserves_all_existing_state_and_returns_stable_receipt(continuity,db,identity,console):
    service,policy,product,session,principal=continuity
    before=await snapshot(db);p=await proof(continuity)
    result=await service.apply(p['proof_id'],'stable-key','issuer relocation',operator())
    assert result['product_count']==result['console_count']==1 and result['replayed'] is False
    assert await snapshot(db)==before
    replay=await service.apply(p['proof_id'],'stable-key','issuer relocation',operator())
    assert replay=={**result,'replayed':True}
    assert await snapshot(db)==before
    assert 'subject-1' not in json.dumps(result)
    assert await identity[0].authenticate(product['access_token'])
    assert (await console[0].authenticate(session['session_token'])).id==principal.id
    for key,reason in [('new-key','issuer relocation'),('stable-key','different')]:
        with pytest.raises(PlatformError):await service.apply(p['proof_id'],key,reason,operator())
    async with db.connection() as c:
        assert (await (await c.execute('SELECT count(*) n FROM identity_migrations')).fetchone())['n']==1
        assert (await (await c.execute('SELECT count(*) n FROM identity_migration_entries')).fetchone())['n']==2
        assert (await (await c.execute('SELECT user_id FROM external_identities WHERE issuer=%s',(policy.target_issuer,))).fetchone())['user_id']==product['scope']['user_id']


@pytest.mark.asyncio
async def test_signed_target_logins_reuse_wallet_and_admin_without_merging_email(continuity,identity,console,db):
    service,policy,old_product,_,principal=continuity
    p=await proof(continuity);await service.apply(p['proof_id'],'key','reason',operator())
    identity[1].issuer=policy.target_issuer;identity[0].settings.issuer=policy.target_issuer
    new_product=await login(identity)
    assert new_product['scope']['user_id']==old_product['scope']['user_id']
    assert new_product['scope']['billing_account_id']==old_product['scope']['billing_account_id']
    unrelated=await login(identity,sub='same-email-new-subject')
    assert unrelated['scope']['user_id']!=old_product['scope']['user_id']
    auth,provider=console
    auth.settings=replace(auth.settings,issuer=policy.target_issuer)
    auth.oidc.settings=auth.settings.oidc_settings();provider.issuer=policy.target_issuer
    logged=await signed_login(console)
    assert logged['status']=='authenticated'
    assert (await auth.authenticate(logged['session_token'])).id==principal.id
    await auth.disable_admin(principal.id,bootstrap())
    pending=await signed_login(console)
    assert pending['status']=='pending'
    with pytest.raises(PlatformError):await auth.approve_request(pending['request_id'],'operator',bootstrap())
    async with db.connection() as c:
        assert (await (await c.execute('SELECT count(*) n FROM console_admins')).fetchone())['n']==1


@pytest.mark.asyncio
async def test_target_pending_approval_resolves_alias_without_creating_or_upgrading_admin(continuity,console,db):
    service,policy,_,_,principal=continuity
    auth,_=console
    async with db.connection() as c:
        await c.execute("INSERT INTO console_login_requests(id,issuer,subject,subject_hash,expires_at) VALUES (%s,%s,'subject-1',%s,clock_timestamp()+interval '1 day')",('f'*32,policy.target_issuer,'a'*64))
    p=await proof(continuity);await service.apply(p['proof_id'],'key','reason',operator())
    auth.settings=replace(auth.settings,issuer=policy.target_issuer)
    approved=await auth.approve_request('f'*32,'operator',bootstrap())
    assert approved['id']==principal.id
    with pytest.raises(PlatformError):await auth.approve_request('f'*32,'admin',bootstrap())


@pytest.mark.asyncio
@pytest.mark.parametrize('conflict',['product','canonical','source_changed'])
async def test_any_conflict_rolls_back_whole_batch(continuity,db,conflict):
    service,policy,*_=continuity;p=await proof(continuity)
    async with db.connection() as c:
        if conflict=='product':
            await c.execute("INSERT INTO users(id) VALUES ('other-user')")
            await c.execute("INSERT INTO external_identities(issuer,subject,user_id) VALUES (%s,'subject-1','other-user')",(policy.target_issuer,))
        elif conflict=='canonical':
            await c.execute("INSERT INTO console_admins(id,issuer,subject,role,created_by) VALUES (%s,%s,'subject-1','viewer','fixture')",('e'*32,policy.target_issuer))
        else:await c.execute("UPDATE console_admins SET role='viewer'")
    before=await snapshot(db)
    assert (await service.inspect(p['proof_id'],operator()))['conflict_count']>0
    with pytest.raises(PlatformError):await service.apply(p['proof_id'],'key','reason',operator())
    assert await snapshot(db)==before
    async with db.connection() as c:
        for t in ('identity_migrations','identity_migration_entries','console_identity_aliases'):
            assert (await (await c.execute('SELECT count(*) n FROM '+t)).fetchone())['n']==0


@pytest.mark.asyncio
async def test_concurrent_same_key_and_proof_consumption_are_atomic(continuity,db):
    service,*_=continuity;p=await proof(continuity)
    results=await asyncio.gather(*(service.apply(p['proof_id'],'same','reason',operator()) for _ in range(2)))
    assert len({r['migration_id'] for r in results})==1
    assert sorted(r['replayed'] for r in results)==[False,True]
    async with db.connection() as c:
        assert (await (await c.execute('SELECT count(*) n FROM identity_migrations')).fetchone())['n']==1


@pytest.mark.asyncio
async def test_expired_proof_is_not_consumed(continuity,db):
    service,policy,*_=continuity
    # The table is immutable: construct an already-expired synthetic proof as owner.
    p=await proof(continuity)
    async with db.connection() as c:
        # One statement clock keeps this synthetic proof's duration exactly one
        # hour; two volatile clocks can exceed the strict maximum by a microsecond.
        await c.execute("INSERT INTO identity_restore_proofs(id,policy_sha256,evidence,candidates,candidate_sha256,operator_id,created_at,expires_at) SELECT %s,policy_sha256,evidence,candidates,candidate_sha256,operator_id,statement_timestamp()-interval '2 hours',statement_timestamp()-interval '1 hour' FROM identity_restore_proofs WHERE id=%s",('a'*32,p['proof_id']))
    with pytest.raises(PlatformError):await service.apply('a'*32,'expired','reason',operator())


@pytest.mark.asyncio
async def test_new_audit_is_immutable_and_alias_requires_matching_entry(continuity,db):
    service,policy,*_=continuity;p=await proof(continuity)
    await service.apply(p['proof_id'],'key','reason',operator())
    for table in ('identity_restore_proofs','identity_restore_candidates','identity_migrations','identity_migration_entries','console_identity_aliases'):
        for statement in ('UPDATE '+table+' SET '+('issuer=issuer' if table=='console_identity_aliases' else 'proof_id=proof_id' if table=='identity_restore_candidates' else 'id=id'),'DELETE FROM '+table,'TRUNCATE '+table+' CASCADE'):
            with pytest.raises(psycopg.Error):
                async with db.connection() as c:await c.execute(statement)
    with pytest.raises(psycopg.Error):
        async with db.connection() as c:
            await c.execute('INSERT INTO console_identity_aliases(issuer,subject,admin_id,migration_entry_id) SELECT %s,subject,admin_id,%s FROM console_identity_aliases LIMIT 1',('https://unapproved.example/oidc','f'*32))
    with pytest.raises(psycopg.Error):
        async with db.connection() as c:
            await c.execute("INSERT INTO console_admins(id,issuer,subject,role,created_by) VALUES (%s,%s,'subject-1','viewer','fixture')",('b'*32,policy.target_issuer))


@pytest.mark.asyncio
async def test_web_roles_cannot_collect_or_apply_or_read_audit(continuity,console_role):
    low,_=console_role;_,policy,*_=continuity
    service=IdentityMigrationService(low,policy)
    for action in (service.record_proof(evidence(policy),operator('identity:collect')),service.inspect('a'*32,operator()),service.apply('a'*32,'key','reason',operator())):
        with pytest.raises(PlatformError) as error:await action
        assert error.value.code=='identity_owner_required'
    async with low.connection() as c:
        await c.execute('SELECT issuer,subject,admin_id FROM console_identity_aliases')
    for table in ('identity_restore_proofs','identity_restore_candidates','identity_migrations','identity_migration_entries'):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with low.connection() as c:await c.execute('SELECT * FROM '+table)


@pytest.mark.asyncio
@pytest.mark.parametrize('table,column',[('identity_restore_proofs','id'),('identity_restore_candidates','candidate')])
async def test_product_guard_rejects_accidental_new_proof_column_select(db,table,column):
    from platform_service.permissions import grant_runtime_permissions,assert_safe_runtime_role
    role='continuity_'+uuid4().hex
    async with db.connection() as c:await c.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
    try:
        async with db.connection() as c:
            await grant_runtime_permissions(c,role)
            await c.execute(sql.SQL('GRANT SELECT({}) ON {} TO {}').format(sql.Identifier(column),sql.Identifier(table),sql.Identifier(role)))
        with pytest.raises(PlatformError):
            async with db.connection() as c:await assert_safe_runtime_role(c,role)
    finally:
        async with db.connection() as c:
            await c.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
            await c.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


@pytest.mark.asyncio
async def test_mapping_keeps_nonempty_settled_pending_balances_and_disabled_acl(db):
    from test_execution_core import setup,admit
    execution,ledger,scope,context=await setup(db)
    settled=await admit(execution,context,'settled')
    await execution.dispatch(settled['id'],settled['attempt_id'])
    await execution.finish(settled['id'],settled['attempt_id'],input_tokens=10,output_tokens=5,
        terminal_status='completed',provider_response_id='synthetic-response',evidence_hash='synthetic-receipt',
        payload=b'{}',content_type='application/json')
    pending=await admit(execution,context,'pending')
    await execution.dispatch(pending['id'],pending['attempt_id'])
    await execution.pending(pending['id'],pending['attempt_id'],'synthetic-unknown')
    policy=MigrationPolicy('state-preservation','https://source.example/oidc','https://target.example/oidc','default',('fixture-app',),'a'*64)
    async with db.connection() as c:
        await c.execute("INSERT INTO external_identities(issuer,subject,user_id) VALUES (%s,'product-subject','u')",(policy.source_issuer,))
        for id,subject,active,role,epoch in [('a'*32,'active-admin',True,'viewer',2),('b'*32,'disabled-admin',False,'admin',5)]:
            await c.execute('INSERT INTO console_admins(id,issuer,subject,active,role,authorization_epoch,created_by) VALUES (%s,%s,%s,%s,%s,%s,%s)',
                (id,policy.source_issuer,subject,active,role,epoch,'synthetic-owner'))
    service=IdentityMigrationService(db,policy)
    ev=evidence(policy,user_ids=('product-subject','active-admin','disabled-admin'))
    proof=await service.record_proof(ev,operator('identity:collect'))
    before=await snapshot(db);balance=await ledger.summary(scope)
    assert balance['spent']=='2000' and balance['pending_reconciliation']==1 and int(balance['reserved'])>0
    result=await service.apply(proof['proof_id'],'state','preserve unknown held',operator())
    assert result['console_count']==2 and result['product_count']==1
    assert await snapshot(db)==before and await ledger.summary(scope)==balance


@pytest.mark.asyncio
async def test_concurrent_canonical_insert_and_alias_apply_never_double_occupy(continuity,db):
    service,policy,*_=continuity;p=await proof(continuity)
    async def canonical():
        try:
            async with db.connection() as c:
                await c.execute("INSERT INTO console_admins(id,issuer,subject,role,created_by) VALUES (%s,%s,'subject-1','viewer','fixture')",('c'*32,policy.target_issuer))
            return 'canonical'
        except psycopg.IntegrityError:return 'rejected'
    async def alias():
        try:
            await service.apply(p['proof_id'],'race','reason',operator())
            return 'alias'
        except PlatformError:return 'rejected'
    results=await asyncio.wait_for(asyncio.gather(canonical(),alias()),5)
    assert results.count('rejected')==1
    async with db.connection() as c:
        rows=await (await c.execute('SELECT id FROM console_admins WHERE issuer=%s AND subject=%s UNION ALL SELECT admin_id FROM console_identity_aliases WHERE issuer=%s AND subject=%s',
            (policy.target_issuer,'subject-1',policy.target_issuer,'subject-1'))).fetchall()
        assert len(rows)==1


@pytest.mark.asyncio
async def test_failure_after_first_mapping_insert_leaves_no_partial_identity_or_audit(continuity,db):
    service,policy,*_=continuity;p=await proof(continuity)
    async with db.connection() as c:
        await c.execute("""CREATE FUNCTION reject_synthetic_alias() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'synthetic insert failure' USING ERRCODE='23514'; END $$""")
        await c.execute('CREATE TRIGGER synthetic_failure BEFORE INSERT ON console_identity_aliases FOR EACH ROW EXECUTE FUNCTION reject_synthetic_alias()')
    with pytest.raises(PlatformError):await service.apply(p['proof_id'],'failure','reason',operator())
    async with db.connection() as c:
        assert (await (await c.execute('SELECT count(*) n FROM external_identities WHERE issuer=%s',(policy.target_issuer,))).fetchone())['n']==0
        for table in ('identity_migrations','identity_migration_entries','console_identity_aliases'):
            assert (await (await c.execute('SELECT count(*) n FROM '+table)).fetchone())['n']==0


@pytest.mark.asyncio
@pytest.mark.parametrize('grant',["SELECT(id) ON identity_restore_proofs","INSERT(id) ON identity_migrations","SELECT(candidate) ON identity_restore_candidates",
    "SELECT(candidate) ON identity_migration_entries","SELECT(migration_entry_id) ON console_identity_aliases",
    "INSERT(issuer) ON console_identity_aliases"])
async def test_console_guard_rejects_every_new_unneeded_column_authority(db,console_role,grant):
    from platform_service.console_permissions import assert_safe_console_role
    low,role=console_role
    async with db.connection() as c:await c.execute(sql.SQL('GRANT '+grant+' TO {}').format(sql.Identifier(role)))
    with pytest.raises(PlatformError):
        async with low.connection() as c:await assert_safe_console_role(c)


@pytest.mark.asyncio
async def test_alias_signed_login_uses_actual_low_console_role(continuity,console,console_role):
    from platform_service.console_auth import ConsoleAuth
    service,policy,_,_,principal=continuity;p=await proof(continuity)
    await service.apply(p['proof_id'],'low-role','reason',operator())
    original,provider=console;provider.issuer=policy.target_issuer
    settings=replace(original.settings,issuer=policy.target_issuer)
    auth=ConsoleAuth(console_role[0],settings,http_client=original.oidc.client)
    session=await signed_login((auth,provider))
    assert (await auth.authenticate(session['session_token'])).id==principal.id


def test_evidence_invalid_collection_type_is_a_fixed_platform_error():
    policy=MigrationPolicy('env','https://old.example/oidc','https://new.example/oidc','default',('app',),'a'*64)
    service=IdentityMigrationService(None,policy)
    for ids in ((['unhashable'],),(True,),('duplicate','duplicate')):
        with pytest.raises(PlatformError):service._evidence(evidence(policy,user_ids=ids))


@pytest.mark.asyncio
async def test_batch_coverage_validation_runs_once_instead_of_per_entry(continuity,db):
    service,*_=continuity;p=await proof(continuity)
    async with db.connection() as c:
        await c.execute('CREATE TABLE continuity_validation_calls(marker integer NOT NULL)')
        definition=(await (await c.execute("SELECT pg_get_functiondef('check_identity_continuity()'::regprocedure) AS definition")).fetchone())['definition']
        definition=definition.replace('\nBEGIN\n','\nBEGIN\n INSERT INTO continuity_validation_calls VALUES (1);\n',1)
        await c.execute(definition,prepare=False)
    await service.apply(p['proof_id'],'one-validation','reason',operator())
    async with db.connection() as c:
        calls=(await (await c.execute('SELECT count(*) n FROM continuity_validation_calls')).fetchone())['n']
    assert calls==1


@pytest.mark.asyncio
async def test_committed_batch_refuses_late_entry_before_deferred_commit(continuity,db):
    from psycopg.types.json import Jsonb
    service,*_=continuity;p=await proof(continuity)
    batch=await service.apply(p['proof_id'],'no-append','reason',operator())
    async with db.connection() as c:
        candidate={'kind':'product','subject':'late-subject','user_id':'unused'}
        with pytest.raises(psycopg.errors.CheckViolation):
            await c.execute('INSERT INTO identity_migration_entries(id,migration_id,kind,subject,candidate) VALUES (%s,%s,%s,%s,%s)',
                ('d'*32,batch['migration_id'],'product','late-subject',Jsonb(candidate)))


@pytest.mark.asyncio
async def test_all_owner_operations_set_transaction_local_statement_and_lock_timeouts(continuity,db,monkeypatch):
    service,*_=continuity;seen=[];original=service._candidates
    async def observed(c):
        statement=(await (await c.execute('SHOW statement_timeout')).fetchone())['statement_timeout']
        lock=(await (await c.execute('SHOW lock_timeout')).fetchone())['lock_timeout']
        seen.append((statement,lock))
        return await original(c)
    monkeypatch.setattr(service,'_candidates',observed)
    async with db.connection() as c:
        baseline=(await (await c.execute('SHOW statement_timeout')).fetchone(),await (await c.execute('SHOW lock_timeout')).fetchone())
    p=await proof(continuity)
    await service.inspect(p['proof_id'],operator())
    await service.apply(p['proof_id'],'timeouts','reason',operator())
    assert seen==[('30s','2s')]*3
    async with db.connection() as c:
        after=(await (await c.execute('SHOW statement_timeout')).fetchone(),await (await c.execute('SHOW lock_timeout')).fetchone())
    assert after==baseline


@pytest.mark.asyncio
async def test_early_constraint_validation_cannot_admit_nonmember_entry_or_normalized_candidate(continuity,db,monkeypatch):
    from contextlib import asynccontextmanager
    from psycopg.types.json import Jsonb
    service,*_=continuity;p=await proof(continuity)
    async with db.connection() as c:
        @asynccontextmanager
        async def same_transaction(**kwargs):yield c
        monkeypatch.setattr(service,'_transaction',same_transaction)
        batch=await service.apply(p['proof_id'],'early','reason',operator())
        await c.execute('SET CONSTRAINTS ALL IMMEDIATE')
        extra={'kind':'product','subject':'early-nonmember','user_id':'unused'}
        with pytest.raises(psycopg.errors.CheckViolation):
            await c.execute('INSERT INTO identity_restore_candidates(proof_id,kind,subject,candidate) VALUES (%s,%s,%s,%s)',
                (p['proof_id'],'product','early-nonmember',Jsonb(extra)))


@pytest.mark.asyncio
async def test_early_constraint_validation_cannot_append_unverified_audit_entry(continuity,db,monkeypatch):
    from contextlib import asynccontextmanager
    from psycopg.types.json import Jsonb
    service,*_=continuity;p=await proof(continuity)
    async with db.connection() as c:
        @asynccontextmanager
        async def same_transaction(**kwargs):yield c
        monkeypatch.setattr(service,'_transaction',same_transaction)
        batch=await service.apply(p['proof_id'],'early-entry','reason',operator())
        await c.execute('SET CONSTRAINTS ALL IMMEDIATE')
        extra={'kind':'product','subject':'early-nonmember','user_id':'unused'}
        with pytest.raises(psycopg.errors.CheckViolation):
            await c.execute('INSERT INTO identity_migration_entries(id,migration_id,kind,subject,candidate) VALUES (%s,%s,%s,%s,%s)',
                ('e'*32,batch['migration_id'],'product','early-nonmember',Jsonb(extra)))


@pytest.mark.asyncio
async def test_total_deadline_rolls_back_and_returns_only_fixed_error(continuity,db,monkeypatch):
    import platform_service.identity_migration as module
    service,policy,*_=continuity
    async def stuck(c):
        await c.execute("INSERT INTO users(id) VALUES ('DEADLINE-CANARY')")
        await asyncio.Event().wait()
    monkeypatch.setattr(module,'TRANSACTION_TIMEOUT_SECONDS',0.1)
    monkeypatch.setattr(service,'_candidates',stuck)
    with pytest.raises(PlatformError) as failure:await service.record_proof(evidence(policy),operator('identity:collect'))
    assert failure.value.code=='identity_migration_timeout' and 'CANARY' not in str(failure.value)
    async with db.connection() as c:
        assert not await (await c.execute("SELECT id FROM users WHERE id='DEADLINE-CANARY'")).fetchone()
        assert (await (await c.execute('SELECT count(*) n FROM identity_restore_proofs')).fetchone())['n']==0


@pytest.mark.asyncio
async def test_statement_and_lock_expiry_are_fixed_errors_without_partial_batch(continuity,db,monkeypatch):
    import platform_service.identity_migration as module
    service,policy,*_=continuity;p=await proof(continuity)
    async def slow(c):await c.execute('SELECT pg_sleep(0.2)')
    monkeypatch.setattr(module,'STATEMENT_TIMEOUT_MS',20)
    with monkeypatch.context() as scoped:
        scoped.setattr(service,'_candidates',slow)
        with pytest.raises(PlatformError) as failure:await service.record_proof(evidence(policy),operator('identity:collect'))
        assert failure.value.code=='identity_migration_timeout'
    monkeypatch.setattr(module,'STATEMENT_TIMEOUT_MS',30000)
    monkeypatch.setattr(module,'LOCK_TIMEOUT_MS',20)
    async with db.connection() as holder:
        await holder.execute("SELECT pg_advisory_xact_lock(hashtextextended('identity-migration-owner',0))")
        with pytest.raises(PlatformError) as failure:await service.apply(p['proof_id'],'locked','reason',operator())
        assert failure.value.code=='identity_migration_timeout'
    async with db.connection() as c:
        assert (await (await c.execute('SELECT count(*) n FROM identity_migrations')).fetchone())['n']==0


@pytest.mark.asyncio
async def test_proof_normalizer_is_atomic_with_constraints_already_immediate(continuity,db,monkeypatch):
    from contextlib import asynccontextmanager
    service,*_=continuity
    async with db.connection() as c:
        await c.execute('SET CONSTRAINTS ALL IMMEDIATE')
        @asynccontextmanager
        async def same_transaction(**kwargs):yield c
        monkeypatch.setattr(service,'_transaction',same_transaction)
        p=await proof(continuity)
        assert (await (await c.execute('SELECT count(*) n FROM identity_restore_candidates WHERE proof_id=%s',(p['proof_id'],))).fetchone())['n']==2


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['console','product'])
async def test_validation_at_10000_candidate_limit_is_one_batch_check(db,kind):
    from platform_service.identity_migration import MAX_CANDIDATES
    assert MAX_CANDIDATES==10000
    policy=MigrationPolicy('candidate-limit','https://old-limit.example/oidc','https://new-limit.example/oidc','default',('app',),'a'*64)
    async with db.connection() as c:
        if kind=='console':
            await c.execute("INSERT INTO console_admins(id,issuer,subject,role,created_by) SELECT md5('limit-'||i::text),%s,'limit-'||i::text,'viewer','synthetic-scale' FROM generate_series(1,10000) i",(policy.source_issuer,))
        else:
            await c.execute("INSERT INTO users(id) SELECT 'limit-user-'||i::text FROM generate_series(1,10000) i")
            await c.execute("INSERT INTO tenants(id,owner_user_id) SELECT 'limit-tenant-'||i::text,'limit-user-'||i::text FROM generate_series(1,10000) i")
            await c.execute("INSERT INTO billing_accounts(id,tenant_id) SELECT 'limit-wallet-'||i::text,'limit-tenant-'||i::text FROM generate_series(1,10000) i")
            await c.execute("INSERT INTO external_identities(issuer,subject,user_id) SELECT %s,'limit-'||i::text,'limit-user-'||i::text FROM generate_series(1,10000) i",(policy.source_issuer,))
        await c.execute('CREATE TABLE continuity_validation_calls(marker integer NOT NULL)')
        definition=(await (await c.execute("SELECT pg_get_functiondef('check_identity_continuity()'::regprocedure) AS definition")).fetchone())['definition']
        await c.execute(definition.replace('\nBEGIN\n','\nBEGIN\n INSERT INTO continuity_validation_calls VALUES (1);\n',1),prepare=False)
    service=IdentityMigrationService(db,policy)
    p=await service.record_proof(evidence(policy,user_ids=tuple('limit-'+str(i) for i in range(1,10001))),operator('identity:collect'))
    assert p[kind+'_count']==10000
    result=await service.apply(p['proof_id'],'limit','candidate-limit validation',operator())
    assert result[kind+'_count']==10000
    async with db.connection() as c:
        assert (await (await c.execute('SELECT count(*) n FROM continuity_validation_calls')).fetchone())['n']==1
        for table in ('identity_restore_candidates','identity_migration_entries'):
            assert (await (await c.execute('SELECT count(*) n FROM '+table)).fetchone())['n']==10000
        assert (await (await c.execute('SELECT count(*) n FROM console_identity_aliases')).fetchone())['n']==(10000 if kind=='console' else 0)


@pytest.mark.asyncio
async def test_candidate_owner_wallet_resolution_uses_two_set_queries(continuity,db):
    service,*_=continuity;calls=[]
    async with db.connection() as c:
        class Observed:
            async def execute(self,*args,**kwargs):
                calls.append(1)
                return await c.execute(*args,**kwargs)
        candidates=await service._candidates(Observed())
    assert len(candidates)==2 and len(calls)==2
