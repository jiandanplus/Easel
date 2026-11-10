"""Fixed internal allowance: private policy and real PostgreSQL ledger invariants."""
import asyncio
from dataclasses import replace
from datetime import timedelta
import json
import os
from pathlib import Path
import secrets
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
import pytest

from platform_service.config import Settings, OIDCClient
from platform_service.contracts import OperatorIdentity, Scope
from platform_service.errors import PlatformError


def policy_document(schema="central", **changes):
    return dict(schema_version=1, enabled=True, environment_id="internal-" + "a" * 32,
                public_url="http://127.0.0.1:18874", database_host="127.0.0.1",
                database_port=18776, database_name="easel_s1_test_internal", db_schema=schema,
                issuer="http://127.0.0.1:3011/oidc", product_id="easel",
                credits="10000000", days=7, concurrency=1, features=["text"], **changes)


def settings_for(document):
    return Settings(database_url="postgresql://ignored@127.0.0.1/unused",
                    public_url=document["public_url"], issuer=document["issuer"],
                    oidc_clients={"easel": OIDCClient("owned-logto-client", "TEST-ONLY-SECRET")},
                    allow_loopback_http=True, db_schema=document["db_schema"])


def load_policy(tmp_path, document):
    from platform_service.internal_grants import InternalGrantPolicy
    path = tmp_path / "internal-policy.json"
    path.write_text(json.dumps(document)); path.chmod(0o600)
    return InternalGrantPolicy.from_env(settings_for(document), {"CENTRAL_INTERNAL_GRANT_POLICY_FILE": str(path)})


def test_internal_policy_is_opt_in_private_and_exact(tmp_path):
    from platform_service.internal_grants import InternalGrantPolicy
    document = policy_document()
    settings = settings_for(document)
    with pytest.raises(PlatformError) as error:
        InternalGrantPolicy.from_env(settings, {})
    assert error.value.code == "internal_grants_disabled"
    policy = load_policy(tmp_path, document)
    assert len(policy.sha256) == 64
    path = tmp_path / "internal-policy.json"
    path.chmod(0o644)
    with pytest.raises(PlatformError):
        InternalGrantPolicy.from_env(settings, {"CENTRAL_INTERNAL_GRANT_POLICY_FILE": str(path)})
    path.chmod(0o600)
    alias = tmp_path / "alias.json"; alias.symlink_to(path)
    with pytest.raises(PlatformError):
        InternalGrantPolicy.from_env(settings, {"CENTRAL_INTERNAL_GRANT_POLICY_FILE": str(alias)})


@pytest.mark.parametrize("field,value", [
    ("credits", "10000001"), ("days", 8), ("days", True), ("concurrency", 2),
    ("features", ["text", "image"]), ("product_id", "second"), ("enabled", False),
    ("public_url", "https://public.example.invalid"), ("database_host", "localhost"),
    ("extra", "anything"),
])
def test_internal_policy_rejects_expansion_and_nonlocal_environment(tmp_path, field, value):
    with pytest.raises(PlatformError):
        load_policy(tmp_path, {**policy_document(), field: value})


def test_internal_policy_binds_issuer_public_origin_and_schema(tmp_path):
    from platform_service.internal_grants import InternalGrantPolicy
    document = policy_document(); load_policy(tmp_path, document)
    for field, value in [("issuer", "http://127.0.0.1:3012/oidc"), ("public_url", "http://127.0.0.1:18875"), ("db_schema", "other")]:
        with pytest.raises(PlatformError):
            InternalGrantPolicy.from_env(replace(settings_for(document), **{field: value}),
                {"CENTRAL_INTERNAL_GRANT_POLICY_FILE": str(tmp_path / "internal-policy.json")})


@pytest.fixture
async def internal(db, test_dsn, tmp_path):
    from platform_service.internal_grants import InternalGrantService
    # Only this owned PostgreSQL schema gets a synthetic equivalent of a consumed
    # OIDC proof. No real identity, provider, or external request occurs in tests.
    parsed = conninfo_to_dict(test_dsn)
    document = {**policy_document(db.schema), "database_host": parsed["host"],
                "database_port": int(parsed.get("port", 5432)), "database_name": parsed["dbname"]}
    policy = load_policy(tmp_path, document)
    sid = secrets.token_urlsafe(32)
    async with db.connection() as c:
        await c.execute("INSERT INTO users(id) VALUES ('u'),('other')")
        await c.execute("INSERT INTO tenants(id,owner_user_id) VALUES ('t','u'),('other-t','other')")
        await c.execute("INSERT INTO tenant_members(tenant_id,user_id,role) VALUES ('t','u','owner'),('other-t','other','owner')")
        await c.execute("INSERT INTO billing_accounts(id,tenant_id) VALUES ('wallet','t'),('other-wallet','other-t')")
        await c.execute("INSERT INTO products(id) VALUES ('easel')")
        await c.execute("INSERT INTO product_clients(product_id,client_id) VALUES ('easel','easel-desktop')")
        await c.execute("INSERT INTO external_identities(issuer,subject,user_id,email_verified) VALUES (%s,'real-subject','u',true)", (document["issuer"],))
        await c.execute("INSERT INTO identity_transactions(id,product_id,client_id,callback_uri,handoff_challenge,local_state,expires_at,state_hash,cookie_hash,nonce_hash,consumed) VALUES ('oidc-proof','easel','easel-desktop','http://127.0.0.1:37689/platform-auth/callback',%s,'state',clock_timestamp()+interval '5 minutes',%s,%s,%s,true)", ('a'*43, 'b'*64, 'c'*64, 'd'*64))
        await c.execute("INSERT INTO identity_handoffs(code_hash,transaction_id,user_id,tenant_id,billing_account_id,issuer,subject,expires_at,consumed) VALUES (%s,'oidc-proof','u','t','wallet',%s,'real-subject',clock_timestamp()+interval '1 minute',true)", ('e'*64, document['issuer']))
        await c.execute("INSERT INTO identity_sessions(id,user_id,tenant_id,billing_account_id,product_id,client_id,authorization_epoch,issuer,subject,expires_at,source_transaction_id) VALUES (%s,'u','t','wallet','easel','easel-desktop',0,%s,'real-subject',clock_timestamp()+interval '7 days','oidc-proof')", (sid, document['issuer']))
        await c.execute("INSERT INTO identity_tokens(token_hash,session_id,kind,expires_at) VALUES (%s,%s,'refresh',clock_timestamp()+interval '7 days')", ('f'*64,sid))
    return InternalGrantService(db, settings_for(document), policy), OperatorIdentity("internal-operator", frozenset({"internal:issue"})), Scope("u","easel","t","wallet",sid,0), document


async def issue(internal, **changes):
    service, operator, scope, document = internal
    return await service.issue(operator, environment=document['environment_id'], wallet=scope.billing_account_id,
        session=scope.session_id, key='approved-once', reason='User approved internal experience', **changes)


@pytest.mark.asyncio
async def test_internal_issue_is_atomic_balanced_distinct_from_payments_and_cross_operator_idempotent(internal, db):
    from platform_service.credits import CreditLedger
    from platform_service.membership import MembershipService
    service, operator, scope, document = internal
    first = await issue(internal)
    second = await issue((service, replace(operator,id="second-operator"), scope, document))
    assert first == second
    assert first['funding_source'] == 'internal_experience' and first['test_only'] is False
    summary = await CreditLedger(db).summary(scope)
    assert summary['available'] == summary['live_available'] == summary['experience_available'] == '10000000'
    assert summary['experience_issued'] == '10000000' and summary['test_available'] == '0'
    entitlements = await MembershipService(db).entitlements(scope)
    assert entitlements['experience_active'] is True
    assert entitlements['features'] == ['text'] and entitlements['concurrency'] == 1
    assert entitlements['memberships'][0]['funding_source'] == 'internal_experience'
    async with db.connection() as c:
        for table in ['internal_grants','memberships','credit_lots','credit_journals']:
            assert (await (await c.execute(sql.SQL('SELECT count(*) AS n FROM {}').format(sql.Identifier(table)))).fetchone())['n'] == 1
        assert (await (await c.execute('SELECT sum(delta) AS n FROM credit_entries')).fetchone())['n'] == 0
        for table in ['billing_orders','payment_events','payment_transactions']:
            assert (await (await c.execute(sql.SQL('SELECT count(*) AS n FROM {}').format(sql.Identifier(table)))).fetchone())['n'] == 0
        row = await (await c.execute('SELECT * FROM internal_grants')).fetchone()
        assert row['policy_sha256'] == service.policy.sha256
        assert row['expires_at'] - row['starts_at'] == timedelta(days=7)
        assert row['reason'] == 'User approved internal experience'


@pytest.mark.asyncio
async def test_concurrent_internal_grants_issue_exactly_once(internal, db):
    outcomes = await asyncio.gather(*(issue(internal) for _ in range(8)))
    assert all(value == outcomes[0] for value in outcomes)
    async with db.connection() as c:
        assert (await (await c.execute('SELECT sum(issued) AS n FROM credit_lots')).fetchone())['n'] == 10000000


@pytest.mark.asyncio
async def test_internal_grant_source_and_wallet_cannot_be_rebound(internal):
    service, operator, scope, document = internal
    await issue(internal)
    for changes in [dict(key='different'),dict(reason='different'),dict(wallet='other-wallet'),dict(session='acceptance-session')]:
        params = dict(environment=document['environment_id'],wallet='wallet',session=scope.session_id,key='approved-once',reason='User approved internal experience')
        params.update(changes)
        with pytest.raises(PlatformError):
            await service.issue(operator, **params)


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', [
    "UPDATE identity_sessions SET revoked_at=clock_timestamp()",
    "UPDATE identity_sessions SET expires_at=clock_timestamp()-interval '1 second'",
    "UPDATE users SET active=false WHERE id='u'",
    "UPDATE users SET authorization_epoch=1 WHERE id='u'",
    "UPDATE tenant_members SET active=false WHERE user_id='u'",
    "UPDATE products SET enabled=false",
    "UPDATE product_clients SET enabled=false",
    "UPDATE identity_tokens SET consumed=true",
    "UPDATE identity_handoffs SET consumed=false",
    "UPDATE identity_sessions SET source_transaction_id=NULL",
])
async def test_internal_grant_requires_current_real_oidc_identity(internal, db, mutation):
    async with db.connection() as c:
        await c.execute(mutation)
    with pytest.raises(PlatformError):
        await issue(internal)
    async with db.connection() as c:
        assert (await (await c.execute('SELECT count(*) AS n FROM internal_grants')).fetchone())['n'] == 0


@pytest.mark.asyncio
async def test_internal_grant_denies_capability_environment_and_wrong_issuer(internal):
    service, operator, scope, document = internal
    params = dict(environment=document['environment_id'],wallet='wallet',session=scope.session_id,key='approved-once',reason='approved')
    with pytest.raises(PlatformError):
        await service.issue(replace(operator,capabilities=frozenset({'test:issue','billing:publish'})), **params)
    with pytest.raises(PlatformError):
        await service.issue(operator, **{**params,'environment':'different'})
    wrong = replace(service.policy, issuer='http://127.0.0.1:3012/oidc')
    from platform_service.internal_grants import InternalGrantService
    with pytest.raises(PlatformError):
        await InternalGrantService(service.db, service.settings, wrong).issue(operator, **params)


@pytest.mark.asyncio
async def test_injected_audit_failure_rolls_back_every_issue_row(internal, db):
    async with db.connection() as c:
        await c.execute("CREATE FUNCTION reject_internal_audit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected failure' USING ERRCODE='23514'; END $$")
        await c.execute('CREATE TRIGGER reject_internal_audit BEFORE INSERT ON internal_grants FOR EACH ROW EXECUTE FUNCTION reject_internal_audit()')
    with pytest.raises(psycopg.errors.CheckViolation):
        await issue(internal)
    async with db.connection() as c:
        for table in ['internal_grants','plans','memberships','credit_lots','credit_journals','credit_entries']:
            assert (await (await c.execute(sql.SQL('SELECT count(*) AS n FROM {}').format(sql.Identifier(table)))).fetchone())['n'] == 0


@pytest.mark.asyncio
async def test_experience_expiry_and_test_live_separation(internal, db):
    from platform_service.credits import CreditLedger
    from platform_service.membership import MembershipService
    _, _, scope, _ = internal
    await issue(internal)
    ledger = CreditLedger(db)
    with pytest.raises(PlatformError) as error:
        await ledger.reserve(scope,operation='text',key='test-mode',body_hash='hash',amount=1,rate_version='v1',funding_mode='test')
    assert error.value.code == 'membership_required'
    reserve = await ledger.reserve(scope,operation='text',key='live-mode',body_hash='hash',amount=10,rate_version='v1',funding_mode='live')
    await ledger.release(scope,reserve['id'],key='release')
    # Same controlled lot-expiry rehearsal used by the ledger's existing PG
    # tests. Immutable membership terms remain seven days.
    async with db.connection() as c:
        await c.execute("UPDATE credit_lots SET expires_at=clock_timestamp()-interval '1 second'")
    summary=await ledger.summary(scope)
    assert summary['experience_available']=='0' and summary['expired']=='10000000'
    assert summary['experience_issued']=='10000000'
    assert summary['experience_expires_at'] is not None


@pytest.mark.asyncio
async def test_internal_audit_is_immutable_and_runtime_cannot_issue(internal, db, test_dsn):
    from platform_service.db import Database
    from platform_service.permissions import grant_runtime_permissions
    from platform_service.internal_grants import InternalGrantService
    from platform_service.credits import CreditLedger
    await issue(internal)
    service, operator, scope, document = internal
    role='internal_runtime_'+uuid4().hex; password=secrets.token_urlsafe(24)
    async with db.connection() as c:
        await c.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(role),sql.Literal(password)))
        await grant_runtime_permissions(c,role)
    runtime=Database(make_conninfo(test_dsn,user=role,password=password),schema=db.schema)
    try:
        await runtime.open(); await runtime.require_runtime_role()
        assert (await CreditLedger(runtime).summary(scope))['experience_available']=='10000000'
        with pytest.raises(PlatformError) as error:
            await InternalGrantService(runtime,service.settings,service.policy).issue(operator,environment=document['environment_id'],wallet='wallet',session=scope.session_id,key='approved-once',reason='User approved internal experience')
        assert error.value.code=='internal_owner_required'
        for command in ["INSERT INTO internal_grants(id) VALUES ('bad')", "UPDATE internal_grants SET reason='bad'", 'DELETE FROM internal_grants', 'TRUNCATE internal_grants']:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                async with runtime.connection() as c:
                    await c.execute(command)
        async with db.connection() as c:
            await c.execute(sql.SQL('GRANT INSERT (id) ON internal_grants TO {}').format(sql.Identifier(role)))
        with pytest.raises(PlatformError):
            await runtime.require_runtime_role()
    finally:
        await runtime.close()
        async with db.connection() as c:
            await c.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
            await c.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
    for command in ["UPDATE internal_grants SET reason='bad'",'DELETE FROM internal_grants','TRUNCATE internal_grants CASCADE']:
        with pytest.raises(psycopg.errors.CheckViolation):
            async with db.connection() as c:
                await c.execute(command)


@pytest.mark.asyncio
async def test_internal_cli_requires_explicit_owner_dsn_and_uses_dedicated_capability(internal, db, test_dsn, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from platform_service.__main__ import operate
    service, operator, scope, document = internal
    env={"CENTRAL_DATABASE_URL":settings_for(document).database_url,"CENTRAL_PUBLIC_URL":document['public_url'],
         "CENTRAL_OIDC_ISSUER":document['issuer'],"CENTRAL_DB_SCHEMA":db.schema,
         "CENTRAL_OIDC_CLIENTS":json.dumps({"easel":{"client_id":"owned-logto-client","client_secret":"TEST-ONLY-SECRET"}}),
         "CENTRAL_ALLOW_LOOPBACK_HTTP":"1","CENTRAL_INTERNAL_GRANT_POLICY_FILE":str(tmp_path/'internal-policy.json')}
    for key,value in env.items(): monkeypatch.setenv(key,value)
    monkeypatch.delenv('CENTRAL_MIGRATION_DATABASE_URL',raising=False)
    args=SimpleNamespace(command='internal-grant',environment=document['environment_id'],wallet='wallet',
        session=scope.session_id,key='approved-once',reason='User approved internal experience',operator='operator')
    with pytest.raises(PlatformError) as error:
        await operate(args)
    assert error.value.code=='internal_owner_required'
    monkeypatch.setenv('CENTRAL_MIGRATION_DATABASE_URL',test_dsn)
    result=await operate(args)
    assert result['funding_source']=='internal_experience'
    assert result==await operate(args)


def test_internal_cli_parameters_are_explicit_and_no_credit_amount_knob(monkeypatch,capsys):
    import platform_service.__main__ as cli
    seen=[]
    async def capture(args):
        seen.append(vars(args)); return {'ok':True}
    monkeypatch.setattr(cli,'operate',capture)
    args=['internal-grant','--environment','owned-environment','--wallet','wallet','--session','session',
          '--key','key','--reason','approved','--operator','operator']
    assert cli.main(args)==0
    assert seen[0]['environment']=='owned-environment'
    with pytest.raises(SystemExit): cli.main(args+['--credits','999999999'])
    assert 'TEST-ONLY-SECRET' not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_internal_grant_preserves_old_test_lots_and_refuses_capacity_overflow(internal, db):
    from platform_service.membership import MembershipService
    from platform_service.credits import CreditLedger, MAX_CREDITS
    service, operator, scope, document=internal
    memberships=MembershipService(db)
    test_operator=OperatorIdentity('old-test-operator',frozenset({'plans:publish','test:issue'}))
    plan=dict(id='existing-test',name='Existing test',version='1',included_products=['easel'],features=['text'],credits=str(MAX_CREDITS),days=30,concurrency=1,test_only=True)
    await memberships.publish_plan(test_operator,plan)
    await memberships.issue_test(test_operator,'wallet',plan['id'],'old-test-only')
    async with db.connection() as c:
        before=await (await c.execute('SELECT * FROM credit_lots ORDER BY id')).fetchall()
    with pytest.raises(PlatformError) as error:
        await issue(internal)
    assert error.value.code=='wallet_capacity_exceeded'
    async with db.connection() as c:
        assert await (await c.execute('SELECT * FROM credit_lots ORDER BY id')).fetchall()==before
        assert (await (await c.execute('SELECT count(*) AS n FROM internal_grants')).fetchone())['n']==0
    assert (await CreditLedger(db).summary(scope))['test_available']==str(MAX_CREDITS)


@pytest.mark.asyncio
async def test_different_keys_racing_one_wallet_cannot_issue_twice(internal):
    service,operator,scope,document=internal
    async def attempt(key):
        return await service.issue(operator,environment=document['environment_id'],wallet='wallet',session=scope.session_id,key=key,reason='approved')
    values=await asyncio.gather(attempt('first'),attempt('second'),return_exceptions=True)
    assert sum(isinstance(value,dict) for value in values)==1
    assert sum(isinstance(value,PlatformError) and value.code=='idempotency_conflict' for value in values)==1


@pytest.mark.asyncio
async def test_internal_environment_must_match_actual_database_and_connection(internal):
    from platform_service.internal_grants import InternalGrantService
    service,operator,scope,document=internal
    for field,value in [('database_name','different'),('database_port',1),('database_host','127.0.0.2')]:
        other=InternalGrantService(service.db,service.settings,replace(service.policy,**{field:value}))
        with pytest.raises(PlatformError) as error:
            await other.issue(operator,environment=document['environment_id'],wallet='wallet',session=scope.session_id,key='key',reason='approved')
        assert error.value.code=='internal_environment_mismatch'


@pytest.mark.asyncio
async def test_consumed_other_transaction_cannot_prove_a_session(internal, db):
    async with db.connection() as c:
        await c.execute("INSERT INTO identity_transactions(id,product_id,client_id,callback_uri,handoff_challenge,local_state,expires_at,state_hash,cookie_hash,nonce_hash,consumed) SELECT 'unrelated',product_id,client_id,callback_uri,handoff_challenge,local_state,expires_at,'other-state',cookie_hash,nonce_hash,true FROM identity_transactions")
        await c.execute("UPDATE identity_sessions SET source_transaction_id='unrelated'")
    with pytest.raises(PlatformError) as error:
        await issue(internal)
    assert error.value.code=='internal_session_required'


@pytest.mark.asyncio
async def test_internal_plan_without_atomic_audit_is_rejected_and_cannot_use_test_command(internal, db):
    from platform_service.membership import MembershipService
    await issue(internal)
    with pytest.raises(PlatformError):
        await MembershipService(db).issue_test(OperatorIdentity('other',frozenset({'test:issue'})),'wallet','easel-internal-experience-v1','fake-test')
    with pytest.raises(psycopg.errors.CheckViolation):
        async with db.connection() as c:
            await c.execute("INSERT INTO memberships(id,billing_account_id,plan_id,operator_id,issuance_key,starts_at,expires_at) VALUES ('unaudited','wallet','easel-internal-experience-v1','other','another',clock_timestamp(),clock_timestamp()+interval '7 days')")


@pytest.mark.asyncio
async def test_internal_grant_has_no_http_issuance_surface(internal, db):
    import httpx
    from platform_service.app import create_app
    service,_,_,_=internal
    app=create_app(service.settings,database=db)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=service.settings.public_url) as client:
        for path in ['/v2/internal-grant','/v2/internal-grants','/internal-grant']:
            assert (await client.post(path,json={})).status_code==404
