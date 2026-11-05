#!/usr/bin/env python3
"""Explicit synthetic PostgreSQL recovery drill for one already owned container.

Default mode reports not_run without loading credentials or running tools.
This repository validation harness is not a production recovery command.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
from uuid import uuid4

PLATFORM = Path(__file__).resolve().parents[1]
CONTAINER = 'easel-console-tests-cbabf5af7060'
IMAGE = 'postgres@sha256:9e2cf6287f25c3151f4c9421ccce2b17f3e60061c111a29165e0d2cdd9ff0a97'
DATABASE = 'easel_s1_test_console_cbabf5af7060'
APPROVED_DSN_FILE = Path('/private/tmp/easel-console-20261023/test-dsn-private.json')
MAX_BYTES = 32 * 1024 * 1024
PHASE = 'arguments'
CONNECT = {'connect_timeout':5,'options':'-c statement_timeout=30000 -c lock_timeout=2000 -c timezone=UTC'}


def require(value):
    if not value:
        raise ValueError('owned_drill_precondition_failed')


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()


def data_fingerprint(tables):
    return canonical_hash({name:sorted(rows,key=lambda row:json.dumps(row,sort_keys=True,separators=(',',':')))
                           for name,rows in sorted(tables.items())})


def compare_snapshots(source,target):
    for field in ('data_sha256','schema_sha256','sequence_sha256','table_counts'):
        require(source[field]==target[field])
    return {'all_equal':True,'data_equal':True,'schema_equal':True,'sequence_equal':True}


def validate_semantics(summary):
    for key in ('financial_balanced','journal_balanced','wallet_balanced','pending_held_positive',
                'active_admin_match','disabled_admin_match','product_alias_same_user'):
        require(summary.get(key) is True)
    for key,count in (('settled_count',1),('pending_count',1),('console_alias_count',2),
                      ('migration_count',1),('entry_count',3),('proof_count',1)):
        require(type(summary.get(key)) is int and summary[key]==count)
    require(type(summary.get('membership_count')) is int and summary['membership_count']>=1)


def semantic_snapshot(dsn,schema,seed):
    import psycopg
    from psycopg import sql
    with psycopg.connect(dsn,**CONNECT) as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        c.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(schema)))
        lots=c.execute('SELECT billing_account_id,sum(issued),sum(available),sum(held),sum(spent),sum(expired),sum(refunded) FROM credit_lots GROUP BY billing_account_id ORDER BY billing_account_id').fetchall()
        entries=c.execute("SELECT billing_account_id,-coalesce(sum(delta) FILTER(WHERE bucket='issuance'),0),coalesce(sum(delta) FILTER(WHERE bucket='available'),0),coalesce(sum(delta) FILTER(WHERE bucket='held'),0),coalesce(sum(delta) FILTER(WHERE bucket='spent'),0),coalesce(sum(delta) FILTER(WHERE bucket='expired'),0),coalesce(sum(delta) FILTER(WHERE bucket='refunded'),0) FROM credit_entries GROUP BY billing_account_id ORDER BY billing_account_id").fetchall()
        unknown=c.execute("SELECT count(*),coalesce(bool_and(actual IS NULL AND amount>0),false) FROM execution_jobs WHERE status='pending_reconciliation'").fetchone()
        pending=c.execute("SELECT count(*),coalesce(sum(amount),0) FROM credit_reservations WHERE status='pending_reconciliation' AND actual IS NULL").fetchone()
        active=c.execute('SELECT role,active FROM console_admins WHERE id=%s',(seed['active_admin_id'],)).fetchone()
        disabled=c.execute('SELECT role,active FROM console_admins WHERE id=%s',(seed['disabled_admin_id'],)).fetchone()
        summary={
            'financial_balanced':bool(lots) and all(row[1]==sum(row[2:]) for row in lots),
            'wallet_balanced':lots==entries,
            'journal_balanced':c.execute('SELECT count(*) FROM (SELECT journal_id FROM credit_entries GROUP BY journal_id HAVING sum(delta)<>0) x').fetchone()==(0,),
            'settled_count':c.execute("SELECT count(*) FROM execution_jobs WHERE status='settled' AND actual=2000").fetchone()[0],
            'pending_count':unknown[0],
            'pending_held_positive':unknown[1] and pending[0]==unknown[0] and pending[1]>0 and sum(row[3] for row in lots)==pending[1],
            'membership_count':c.execute('SELECT count(*) FROM memberships').fetchone()[0],
            'active_admin_match':active==('admin',True),'disabled_admin_match':disabled==('viewer',False),
            'product_alias_same_user':c.execute("SELECT user_id FROM external_identities WHERE issuer=%s AND subject='drill-product'",(seed['target_issuer'],)).fetchone()==('u',),
            'console_alias_count':c.execute('SELECT count(*) FROM console_identity_aliases WHERE issuer=%s AND admin_id=ANY(%s)',(seed['target_issuer'],[seed['active_admin_id'],seed['disabled_admin_id']])).fetchone()[0],
            'migration_count':c.execute('SELECT count(*) FROM identity_migrations').fetchone()[0],
            'entry_count':c.execute('SELECT count(*) FROM identity_migration_entries').fetchone()[0],
            'proof_count':c.execute('SELECT count(*) FROM identity_restore_proofs').fetchone()[0]}
        validate_semantics(summary)
        return summary


def validate_connection_parameters(value):
    require(set(value)<={'host','port','dbname','user','password','connect_timeout','sslmode','hostaddr'}
            and value.get('host')=='127.0.0.1' and value.get('hostaddr','127.0.0.1')=='127.0.0.1'
            and str(value.get('port'))=='18777' and value.get('dbname')==DATABASE
            and isinstance(value.get('user'),str) and bool(value['user']))


def validate_container(value):
    require(value.get('name')=='/'+CONTAINER and value.get('running') is True
            and value.get('image')==IMAGE and value.get('memory')==805306368
            and value.get('ports')=={'5432/tcp':[{'HostIp':'127.0.0.1','HostPort':'18777'}]})
    tmpfs=value.get('tmpfs',{})
    require(set(tmpfs)=={'/var/lib/postgresql/data'})
    tokens=tmpfs['/var/lib/postgresql/data'].split(',')
    require('size=536870912' in tokens or 'size=512m' in tokens)


def owned_names(nonce):
    require(isinstance(nonce,str) and re.fullmatch('[0-9a-f]{32}',nonce))
    return {'schema':'s1_test_drill_'+nonce,'idp_schema':'s5a2_idp_'+nonce,
            'restore_database':'easel_s1_test_restore_'+nonce,
            'idp_source_database':'easel_s1_test_idp_src_'+nonce,
            'idp_target_database':'easel_s1_test_idp_dst_'+nonce,
            'product_role':'s5a2_product_'+nonce,'console_role':'s5a2_console_'+nonce}


def validate_empty_namespaces(names):
    require(all(name.startswith('pg_') or name in ('public','information_schema') for name in names))


def dump_command(database,schema):
    require(database==DATABASE or re.fullmatch('easel_s1_test_idp_src_[0-9a-f]{32}',database))
    require(re.fullmatch('(s1_test_drill_|s5a2_idp_)[0-9a-f]{32}',schema))
    return ['docker','exec','--user','postgres',CONTAINER,'pg_dump','--no-password',
            '--username=postgres','--dbname='+database,'--schema='+schema,'--format=custom']


def restore_command(database,owner):
    require(re.fullmatch('easel_s1_test_(restore_|idp_dst_)[0-9a-f]{32}',database))
    require(re.fullmatch('[A-Za-z_][A-Za-z0-9_-]{0,62}',owner))
    return ['docker','exec','--user','postgres','-i',CONTAINER,'pg_restore','--no-password',
            '--username=postgres','--dbname='+database,'--role='+owner,'--no-owner','--no-acl',
            '--single-transaction','--exit-on-error']


def run_tool(argv,*,data=None):
    result=subprocess.run(argv,input=data,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90,check=False)
    require(result.returncode==0 and len(result.stdout)<=MAX_BYTES)
    return result.stdout


def read_private_dsn(path):
    from psycopg.conninfo import conninfo_to_dict
    require(path==APPROVED_DSN_FILE)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as handle:
        info=os.fstat(handle.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid()
                and stat.S_IMODE(info.st_mode)==0o600 and info.st_size<=16384)
        value=json.loads(handle.read(16385))
    require(set(value)=={'dsn'} and isinstance(value['dsn'],str))
    validate_connection_parameters(conninfo_to_dict(value['dsn']))
    return value['dsn']


def write_private(root,name,value):
    require(Path(name).name==name and root.is_dir() and not root.is_symlink())
    fd=os.open(root/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as handle:
        os.fchmod(handle.fileno(),0o600);handle.write(value);handle.flush();os.fsync(handle.fileno())


def inspect_container():
    template='{"name":{{json .Name}},"running":{{json .State.Running}},"image":{{json .Config.Image}},"ports":{{json .HostConfig.PortBindings}},"memory":{{json .HostConfig.Memory}},"tmpfs":{{json .HostConfig.Tmpfs}}}'
    validate_container(json.loads(run_tool(['docker','inspect','--format',template,CONTAINER])))


def snapshot(dsn,schema):
    import psycopg
    from psycopg import sql
    with psycopg.connect(dsn,**CONNECT) as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        c.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(schema)))
        c.execute('SET LOCAL row_security=off')
        require(c.execute("SELECT current_user=session_user AND current_user=pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname=current_schema()").fetchone()==(True,))
        tables=[r[0] for r in c.execute("SELECT relname FROM pg_class WHERE relnamespace=current_schema()::regnamespace AND relkind IN ('r','p') ORDER BY relname")]
        require(0<len(tables)<=150)
        data={}
        for table in tables:
            rows=c.execute(sql.SQL('SELECT to_jsonb(t) FROM {}.{} t LIMIT 10001').format(sql.Identifier(schema),sql.Identifier(table))).fetchall()
            require(len(rows)<=10000)
            data[table]=[r[0] for r in rows]
        require(len(json.dumps(data))<=MAX_BYTES)
        structural={
            'columns':c.execute("SELECT c.relname,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,a.attidentity,a.attgenerated,pg_get_expr(d.adbin,d.adrelid) FROM pg_class c JOIN pg_attribute a ON a.attrelid=c.oid LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum WHERE c.relnamespace=current_schema()::regnamespace AND c.relkind IN ('r','p') AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum").fetchall(),
            'constraints':c.execute("SELECT c.relname,k.conname,k.contype,k.condeferrable,k.condeferred,k.convalidated,pg_get_constraintdef(k.oid,true) FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid WHERE c.relnamespace=current_schema()::regnamespace ORDER BY c.relname,k.conname").fetchall(),
            'indexes':c.execute('SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname=current_schema() ORDER BY tablename,indexname').fetchall(),
            'functions':c.execute("SELECT p.proname,pg_get_function_identity_arguments(p.oid),pg_get_functiondef(p.oid) FROM pg_proc p WHERE p.pronamespace=current_schema()::regnamespace ORDER BY p.proname,pg_get_function_identity_arguments(p.oid)").fetchall(),
            'triggers':c.execute("SELECT c.relname,t.tgname,t.tgenabled,pg_get_triggerdef(t.oid,true) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid WHERE c.relnamespace=current_schema()::regnamespace AND NOT t.tgisinternal ORDER BY c.relname,t.tgname").fetchall(),
            'views':c.execute("SELECT relname,pg_get_viewdef(oid,true) FROM pg_class WHERE relnamespace=current_schema()::regnamespace AND relkind IN ('v','m') ORDER BY relname").fetchall(),
            'rls':c.execute("SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class WHERE relnamespace=current_schema()::regnamespace AND relkind IN ('r','p') ORDER BY relname").fetchall(),
            'policies':c.execute('SELECT tablename,policyname,permissive,roles,cmd,qual,with_check FROM pg_policies WHERE schemaname=current_schema() ORDER BY tablename,policyname').fetchall()}
        sequences={}
        for row in c.execute("SELECT c.relname,s.seqstart,s.seqincrement,s.seqmax,s.seqmin,s.seqcache,s.seqcycle FROM pg_sequence s JOIN pg_class c ON c.oid=s.seqrelid WHERE c.relnamespace=current_schema()::regnamespace ORDER BY c.relname").fetchall():
            state=c.execute(sql.SQL('SELECT last_value,is_called FROM {}.{}').format(sql.Identifier(schema),sql.Identifier(row[0]))).fetchone()
            sequences[row[0]]=[*row[1:],*state]
        # Serial and identity dependencies use semantic names; source/restore OIDs differ.
        ownership=c.execute("""SELECT s.relname,n.nspname,t.relname,a.attname,d.deptype
            FROM pg_class s
            LEFT JOIN pg_depend d ON d.classid='pg_class'::regclass AND d.objid=s.oid
                AND d.refclassid='pg_class'::regclass AND d.deptype IN ('a','i')
            LEFT JOIN pg_class t ON t.oid=d.refobjid
            LEFT JOIN pg_namespace n ON n.oid=t.relnamespace
            LEFT JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=d.refobjsubid
            WHERE s.relnamespace=current_schema()::regnamespace AND s.relkind='S'
            ORDER BY s.relname,n.nspname,t.relname,a.attname,d.deptype""").fetchall()
        return {'data_sha256':data_fingerprint(data),'schema_sha256':canonical_hash(structural),
                'sequence_sha256':canonical_hash({'definitions_state':sequences,'owned_by':ownership}),
                'table_counts':{k:len(v) for k,v in data.items()}}


async def seed_central(db):
    """Only synthetic fixtures and signed MockTransport; no provider execution."""
    import httpx
    import time
    from urllib.parse import parse_qs,urlsplit
    from test_credits import wallet
    from test_execution_core import definition,BINDINGS,OPERATOR
    from test_identity import Provider
    from platform_service.execution.core import ExecutionService
    from platform_service.identity import IdentityService
    from platform_service.console_auth import ConsoleAuth
    from platform_service.console_config import ConsoleSettings
    from platform_service.contracts import OperatorIdentity
    provider=Provider();source_issuer=provider.issuer
    ledger,scope=await wallet(db,'300000000')
    async with db.connection() as c:
        await c.execute("INSERT INTO product_clients(product_id,client_id) VALUES ('easel','desktop')")
        await c.execute("INSERT INTO external_identities(issuer,subject,user_id) VALUES (%s,'drill-product','u')",(source_issuer,))
        await c.execute("INSERT INTO identity_sessions(id,user_id,tenant_id,billing_account_id,product_id,client_id,authorization_epoch,issuer,subject,expires_at) VALUES ('session','u','t','wallet','easel','desktop',0,%s,'drill-product',now()+interval '1 hour')",(source_issuer,))
    execution=ExecutionService(db,IdentityService(db,None))
    await execution.publish(definition(),OPERATOR);await execution.set_enabled('fixture-text',True,OPERATOR)
    minted=await execution.mint(scope,model='fixture-text',invocation_key='drill',mode='test',bindings=BINDINGS)
    context=await execution.authenticate_task(minted['task_token'])
    settled=await execution.admit(context,key='settled',body_hash='fixture',output_limit=100,bindings=BINDINGS)
    await execution.dispatch(settled['id'],settled['attempt_id'])
    await execution.finish(settled['id'],settled['attempt_id'],input_tokens=10,output_tokens=5,
        terminal_status='completed',provider_response_id='synthetic',evidence_hash='synthetic',payload=b'{}',content_type='application/json')
    pending=await execution.admit(context,key='pending',body_hash='fixture',output_limit=100,bindings=BINDINGS)
    await execution.dispatch(pending['id'],pending['attempt_id']);await execution.pending(pending['id'],pending['attempt_id'],'synthetic_unknown')
    settings=ConsoleSettings(database_url='postgresql://unused@127.0.0.1/unused',public_url='https://central.example',
        issuer=source_issuer,client_id='oidc-client',client_secret='synthetic-only',db_schema=db.schema)
    operator=OperatorIdentity('synthetic-drill-owner',frozenset({'console:bootstrap'}))
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider.handler)) as client:
        auth=ConsoleAuth(db,settings,http_client=client)
        async def login(subject):
            provider.overrides={'auth_time':int(time.time()),'sub':subject}
            begin=await auth.begin_login();provider.params={**parse_qs(urlsplit(begin['url']).query),'product':'console'}
            return await auth.callback('synthetic-code',provider.params['state'][0],begin['cookie'])
        active=await login('drill-admin');approved=await auth.approve_request(active['request_id'],'admin',operator)
        session=await login('drill-admin')
        disabled=await login('drill-disabled');denied=await auth.approve_request(disabled['request_id'],'viewer',operator)
        await auth.disable_admin(denied['id'],operator)
    return {'source_issuer':source_issuer,'target_issuer':'https://restored-identity.example/oidc',
            'subjects':('drill-product','drill-admin','drill-disabled'),'settings':settings,
            'session_token':session['session_token'],'active_admin_id':approved['id'],'disabled_admin_id':denied['id']}


async def seed_migration(db,dsn,names,owner,root):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from platform_service.identity_migration import MigrationPolicy,IdentityMigrationService
    from platform_service.identity_restore import collect_restore_evidence
    from platform_service.contracts import OperatorIdentity
    seed=await seed_central(db)
    src=make_conninfo(dsn,dbname=names['idp_source_database'])
    dst=make_conninfo(dsn,dbname=names['idp_target_database'])
    with psycopg.connect(src,**CONNECT) as c:
        c.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(names['idp_schema'])))
        c.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(names['idp_schema'])))
        c.execute("CREATE TYPE fixture_identity_status AS ENUM ('active','locked')")
        c.execute("ALTER TYPE fixture_identity_status ADD VALUE 'pending' BEFORE 'locked'")
        c.execute("CREATE TYPE fixture_unused_status AS ENUM ('preserved')")
        c.execute("CREATE TABLE users(tenant_id text NOT NULL,id text NOT NULL,status fixture_identity_status NOT NULL DEFAULT 'active',history fixture_identity_status[] NOT NULL DEFAULT '{active}',PRIMARY KEY(tenant_id,id))")
        c.execute('CREATE TABLE applications(tenant_id text NOT NULL,id text NOT NULL,PRIMARY KEY(tenant_id,id))')
        for subject in seed['subjects']:c.execute('INSERT INTO users(tenant_id,id) VALUES (%s,%s)',('default',subject))
        c.execute("INSERT INTO applications VALUES ('default','oidc-client')")
    backup=run_tool(dump_command(names['idp_source_database'],names['idp_schema']))
    write_private(root,'identity.dump',backup)
    run_tool(restore_command(names['idp_target_database'],owner),data=backup)
    backup_hash=hashlib.sha256(backup).hexdigest()
    policy=MigrationPolicy(environment='synthetic-owned-drill',source_issuer=seed['source_issuer'],
        target_issuer=seed['target_issuer'],tenant_id='default',client_ids=('oidc-client',),approved_backup_sha256=backup_hash)
    evidence=await collect_restore_evidence(policy,source_dsn=src,target_dsn=dst,
        source_schema=names['idp_schema'],target_schema=names['idp_schema'],backup_path=root/'identity.dump')
    service=IdentityMigrationService(db,policy)
    operator=OperatorIdentity('synthetic-drill-owner',frozenset({'identity:collect','identity:migrate'}))
    proof=await service.record_proof(evidence,operator)
    result=await service.apply(proof['proof_id'],'owned-drill-migration','Synthetic recovery fixture',operator)
    require(result['product_count']>=1 and result['console_count']>=2)
    return seed,{'product_count':result['product_count'],'console_count':result['console_count'],
                 'collector_executed':True,'identity_backup_sha256':backup_hash,'native_enum_fixture':True}


async def check_low_roles(dsn,schema,names,seed,created):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from platform_service.db import Database
    from platform_service.permissions import grant_runtime_permissions
    from platform_service.console_permissions import grant_console_permissions,assert_safe_console_role
    from platform_service.console_auth import ConsoleAuth
    from platform_service.console_service import ConsoleQueries
    ownerdb=Database(dsn,schema=schema,min_size=1,max_size=1);await ownerdb.open()
    try:
        for kind,grant in (('product',grant_runtime_permissions),('console',grant_console_permissions)):
            role=names[kind+'_role'];password=secrets.token_urlsafe(32)
            async with ownerdb.connection() as c:
                await c.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(role),sql.Literal(password)))
            created['roles'].append(role)
            async with ownerdb.connection() as c:await grant(c,role)
            low=Database(make_conninfo(dsn,user=role,password=password),schema=schema,min_size=1,max_size=1)
            try:
                await low.open()
                if kind=='product':
                    await low.require_runtime_role()
                else:
                    async with low.connection() as c:await assert_safe_console_role(c)
                    auth=ConsoleAuth(low,seed['settings'])
                    actor=await auth.authenticate(seed['session_token'])
                    require(actor.id==seed['active_admin_id'])
                    require((await ConsoleQueries(low,auth).wallet(actor,'wallet'))['memberships'])
                    try:
                        async with low.connection() as c:await c.execute('SELECT token_hash FROM identity_tokens')
                    except psycopg.errors.InsufficientPrivilege:pass
                    else:raise ValueError('private_table_read_allowed')
            finally:await low.close()
    finally:await ownerdb.close()
    return {'actual_product_guard':True,'actual_console_guard':True,'actual_console_read':True,'private_read_denied':True}


def cleanup_owned(dsn,names,created,owner):
    import psycopg
    from psycopg import sql
    require(set(created['databases'])<=set(names[k] for k in ('restore_database','idp_source_database','idp_target_database')))
    require(set(created['roles'])<=set(names[k] for k in ('product_role','console_role')))
    with psycopg.connect(dsn,autocommit=True,**CONNECT) as c:
        for database in reversed(created['databases']):
            row=c.execute('SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=%s',(database,)).fetchone()
            require(row==(owner,))
            c.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(database)))
        if created['schema']:
            row=c.execute('SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname=%s',(names['schema'],)).fetchone()
            require(row==(owner,))
            c.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(names['schema'])))
        for role in reversed(created['roles']):c.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


async def perform_drill(dsn,root,names,cleanup):
    global PHASE
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from platform_service.db import Database
    created={'schema':False,'databases':[],'roles':[]}
    # A nonce-bound manifest is written before mutations; it carries no secret.
    write_private(root,'owned-objects.json',json.dumps(names,sort_keys=True).encode())
    PHASE='create_owned_objects'
    with psycopg.connect(dsn,autocommit=True,**CONNECT) as c:
        row=c.execute('SELECT current_user,session_user,current_database()').fetchone()
        require(row[0]==row[1] and row[2]==DATABASE);owner=row[0]
        for key in ('restore_database','idp_source_database','idp_target_database'):
            database=names[key]
            require(c.execute('SELECT 1 FROM pg_database WHERE datname=%s',(database,)).fetchone() is None)
            c.execute(sql.SQL('CREATE DATABASE {} TEMPLATE template0').format(sql.Identifier(database)))
            created['databases'].append(database)
        c.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(names['schema'])))
        created['schema']=True
    target=make_conninfo(dsn,dbname=names['restore_database'])
    # Only private connection file; never copy it to the public receipt.
    write_private(root,'connections-private.json',json.dumps({'source':dsn,'target':target}).encode())
    db=Database(dsn,schema=names['schema'],min_size=1,max_size=1)
    try:
        PHASE='migrate_and_seed_synthetic'
        await db.open();await db.migrate()
        seed,migration=await seed_migration(db,dsn,names,owner,root)
    finally:await db.close()
    PHASE='source_snapshot_and_dump'
    source=snapshot(dsn,names['schema'])
    semantics=semantic_snapshot(dsn,names['schema'],seed)
    archive=run_tool(dump_command(DATABASE,names['schema']))
    write_private(root,'central.dump',archive)
    require(compare_snapshots(source,snapshot(dsn,names['schema']))['all_equal'])
    PHASE='restore_to_new_empty_database'
    with psycopg.connect(target,**CONNECT) as c:
        validate_empty_namespaces(row[0] for row in c.execute('SELECT nspname FROM pg_namespace').fetchall())
    run_tool(restore_command(names['restore_database'],owner),data=archive)
    restored=snapshot(target,names['schema']);comparison=compare_snapshots(source,restored)
    require(semantic_snapshot(target,names['schema'],seed)==semantics)
    PHASE='actual_restored_low_privilege_checks'
    guards=await check_low_roles(target,names['schema'],names,seed,created)
    require(compare_snapshots(source,snapshot(target,names['schema']))['all_equal'])
    receipt={'success':True,'verification':'actual_synthetic_restore','production_ready':False,
        'verified_at':datetime.now(timezone.utc).isoformat(),
        'container':CONTAINER,'new_database':True,'same_schema_name':True,'schema_snapshot':source,
        'comparison':comparison,'semantics':semantics,'guards':guards,'migration':migration,'central_dump_sha256':hashlib.sha256(archive).hexdigest(),
        'central_dump_bytes':len(archive),'credentials_exported':False,'model_calls':0,'payment_calls':0,
        'cleanup_requested':cleanup,'container_stopped':False,'executed':True}
    PHASE='optional_owned_cleanup'
    if cleanup:cleanup_owned(dsn,names,created,owner)
    receipt['owned_cleanup_complete']=cleanup
    return receipt


def execute_drill(dsn_file,output,cleanup):
    global PHASE
    require(dsn_file==APPROVED_DSN_FILE and output is not None)
    require(not any(parent.is_symlink() for parent in output.parents))
    output.mkdir(mode=0o700);output.chmod(0o700)
    # Imports are restricted to current public repo source and synthetic fixtures.
    sys.path[:0]=[str(PLATFORM/'src'),str(PLATFORM/'tests')]
    try:
        PHASE='owned_container_preflight';inspect_container()
        PHASE='private_connection_preflight';dsn=read_private_dsn(dsn_file)
        names=owned_names(uuid4().hex)
        result=asyncio.run(perform_drill(dsn,output,names,cleanup))
        write_private(output,'restore-safe.json',(json.dumps(result,sort_keys=True,indent=2)+'\n').encode())
        return result
    except Exception:
        # Keep partial artifacts and database objects for scoped diagnosis.
        write_private(output,'restore-failure-safe.json',json.dumps({'success':False,'phase':PHASE,
            'partial_objects_may_remain':True,'container_stopped':False}).encode())
        raise


class Parser(argparse.ArgumentParser):
    def error(self,_message):raise ValueError('invalid_arguments')


def main(argv=None):
    try:
        parser=Parser(description=__doc__)
        parser.add_argument('--execute-owned-drill',action='store_true')
        parser.add_argument('--cleanup-owned',action='store_true')
        parser.add_argument('--dsn-file',type=Path,default=APPROVED_DSN_FILE)
        parser.add_argument('--output',type=Path)
        args=parser.parse_args(argv)
        require(args.dsn_file==APPROVED_DSN_FILE)
        if not args.execute_owned_drill:
            require(not args.cleanup_owned)
            print(json.dumps({'executed':False,'verification':'not_run','production_ready':False,
                              'container':CONTAINER,'requires_explicit_execution_flag':True}));return 0
        result=execute_drill(args.dsn_file,args.output,args.cleanup_owned)
        # Whole-table snapshots contain hashes/counts only, not rows or tokens.
        print(json.dumps(result,sort_keys=True));return 0
    except Exception:
        print(json.dumps({'success':False,'phase':PHASE,'code':'identity_restore_drill_failed'}));return 1


if __name__=='__main__':raise SystemExit(main())
