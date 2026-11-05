"""Pure tests: no credentials, Docker, database, or decryption."""
import importlib.util
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import uuid
from types import SimpleNamespace
import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/verify_encrypted_restore_drill.py"

def module():
    spec = importlib.util.spec_from_file_location("restore_drill", SCRIPT)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value

@pytest.fixture
def lib():
    return module()

def test_reviewed_plan_requires_exact_digest(lib):
    plan={"nonce":"a"*32,"kind":"isolated-restore-v2"}
    lib.reviewed(plan, lib.digest(lib.canonical(plan)))
    with pytest.raises(lib.Rejected):lib.reviewed(plan, "0"*64)

def test_schema_normalization_removes_only_random_pg_restrict_tokens(lib):
    a="-- Dumped from database version 16.11\n\\restrict abc\nCREATE TABLE a (id text);\n\\unrestrict abc\n"
    b=a.replace("abc", "different")
    assert lib.schema_digest(a.encode()) == lib.schema_digest(b.encode())
    assert lib.schema_digest(a.encode()) != lib.schema_digest(a.replace("16.11", "16.12").encode())
    assert lib.schema_digest(a.encode()) != lib.schema_digest(a.replace("id text", "id integer").encode())

def test_sql_quotes_preserve_names_and_reject_controls(lib):
    assert lib.ident('a"b') == '"a""b"'
    assert lib.literal("a'b") == "'a''b'"
    for value in ["", "x\x00y", "x\ny", None]:
        with pytest.raises(lib.Rejected):lib.ident(value)

@pytest.mark.parametrize("nonce", ["", "../escape", "A"*32, "a"*31])
def test_container_names_are_restricted(lib,nonce):
    with pytest.raises(lib.Rejected):lib.container_identity(nonce,"central")

def test_container_only_uses_isolated_network_and_bounded_resources(lib):
    identity=lib.container_identity("a"*32,"central")
    command=lib.container_create(identity)
    assert "--network=none" in command
    assert "--memory=512m" in command and "--memory-swap=512m" in command
    assert "--cpus=0.75" in command and "--pids-limit=128" in command
    assert "--restart=no" in command
    assert not any(value in command for value in ["-p", "--publish", "--privileged", "--network=host"])
    assert "listen_addresses=" in command
    for value in ('log_statement=none','log_min_error_statement=panic','log_min_messages=panic'):assert value in command
    assert identity["volume"] in " ".join(command)

def fixture_container(lib):
    value=lib.container_identity("a"*32,"central")
    row={"Name":"/"+value["name"],"State":{"Running":True},"Config":{"Image":lib.IMAGE,"Cmd":lib.POSTGRES_ARGUMENTS,"Env":["POSTGRES_USER="+value["user"],"POSTGRES_HOST_AUTH_METHOD=trust"],"Labels":{"easel.restore-drill":value["nonce"],"easel.restore-kind":"central"}},"HostConfig":{"NetworkMode":"none","Memory":512*1024*1024,"MemorySwap":512*1024*1024,"NanoCpus":750000000,"PidsLimit":128,"RestartPolicy":{"Name":"no"},"PortBindings":{}},"NetworkSettings":{"Ports":{"5432/tcp":None}},"Mounts":[{"Type":"volume","Name":value["volume"],"Destination":"/var/lib/postgresql/data","RW":True}]}
    return value,row

def test_container_guard_requires_exact_owner_label_mount_and_limits(lib):
    value,row=fixture_container(lib);lib.verify_container(row,value)
    for change in [lambda r:r["Config"]["Labels"].update({"easel.restore-drill":"other"}),lambda r:r["Config"].update({"Cmd":["postgres"]}),lambda r:r["HostConfig"].update({"NetworkMode":"bridge"}),lambda r:r["HostConfig"].update({"Memory":0}),lambda r:r["HostConfig"].update({"PortBindings":{"5432/tcp":[{}]}}),lambda r:r["Mounts"][0].update({"Name":"production-data"})]:
        bad=json.loads(json.dumps(row));change(bad)
        with pytest.raises(lib.Rejected):lib.verify_container(bad,value)

def test_archive_index_verifies_members_not_only_dump_magic(lib):
    entries={"databases/central.dump":b"PGDMPfixture","inventories/central.json":b"{}"}
    manifest={"files":[{"name":name,"length":len(data),"sha256":hashlib.sha256(data).hexdigest()} for name,data in sorted(entries.items())]}
    lib.verify_members(entries,manifest)
    for bad in [dict(entries, **{"../secret":b"x"}),{"databases/central.dump":b"PGDMPtampered","inventories/central.json":b"{}"}, {"databases/central.dump":b"PGDMPfixture"}]:
        with pytest.raises(lib.Rejected):lib.verify_members(bad,manifest)

def test_sequences_and_all_inventory_fields_must_match(lib):
    inventory={"schema":"a","tables":{"users":{"n":2,"sha256":"b"}},"sequences":{"s":[3,True]},"roles":{"r":["LOGIN"]},"database":{"owner":"r"},"money":{"lots_balanced":True,"journals_balanced":True}}
    lib.match_inventories(inventory,inventory)
    for key in inventory:
        bad=json.loads(json.dumps(inventory));bad[key]={"changed":True}
        with pytest.raises(lib.Rejected):lib.match_inventories(inventory,bad)
    with pytest.raises(lib.Rejected):lib.stable_sequences({"a":[1,True]}, {"a":[2,True]})

def test_restore_preserves_owner_and_acl_and_uses_exit_on_error(lib):
    identity=lib.container_identity("a"*32,"identity")
    command=lib.restore_command(identity,"easel_logto_preview")
    assert "--exit-on-error" in command and "--single-transaction" in command
    assert "--no-owner" not in command and "--no-acl" not in command
    assert identity["name"] in command

def test_failure_stops_only_verified_owned_container_and_never_removes_data(lib):
    seen=[];identity=lib.container_identity("a"*32,"identity")
    lib.stop_owned(identity,lambda args,**kw:seen.append(args),lambda item:None)
    assert seen==[["/usr/bin/docker","stop","--time=20",identity["name"]]]
    with pytest.raises(lib.Rejected):lib.stop_owned(identity,lambda args,**kw:seen.append(args),lambda item:(_ for _ in ()).throw(lib.Rejected("wrong target")))
    assert len(seen)==1


@pytest.mark.skipif(os.environ.get('EASEL_RESTORE_DRILL_DOCKER')!='1', reason='explicit synthetic Docker environment required')
def test_actual_synthetic_exported_snapshot_dump_and_fresh_restore(lib, monkeypatch):
    original_match=lib.match_inventories
    def match(source, restored):
        changed=[key for key in source if lib.canonical(source[key])!=lib.canonical(restored.get(key))]
        if changed: raise AssertionError('synthetic inventory mismatch fields: '+','.join(changed))
        return original_match(source,restored)
    monkeypatch.setattr(lib,'match_inventories',match)
    docker=shutil.which('docker')
    assert docker
    original_popen=subprocess.Popen
    def popen(args, **kwargs):
        args=[docker if value=='/usr/bin/docker' else value for value in args]
        return original_popen(args,**kwargs)
    monkeypatch.setattr(lib.subprocess,'Popen',popen)
    def run(args,maximum=lib.MAXIMUM,data=None,timeout=90):
        args=[docker if value=='/usr/bin/docker' else value for value in args]
        proc=original_popen(args,stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            out,err=proc.communicate(data,timeout=timeout)
        except BaseException:
            proc.kill();proc.wait();raise
        if proc.returncode!=0 or len(out)>maximum:
            raise lib.Rejected('synthetic_command_rejected')
        return out
    source=lib.container_identity(uuid.uuid4().hex,'central')
    target=lib.container_identity(uuid.uuid4().hex,'central')
    backup=SimpleNamespace(command=run,Rejected=lib.Rejected)
    backup.container_gate=lambda name:lib.guard_target(backup,source)
    started=[];completed=False
    try:
        started.append(source);lib.start_target(backup,source)
        statement='CREATE DATABASE fixture_wallet OWNER '+lib.ident(source['user'])+';'
        lib.write_sql(run,source,'postgres',statement)
        lib.write_sql(run,source,'postgres','ALTER ROLE '+lib.ident(source['user'])+" PASSWORD 'synthetic-bootstrap';")
        fixture_sql="""
CREATE ROLE fixture_runtime LOGIN PASSWORD 'synthetic-only';
CREATE ROLE fixture_operator LOGIN IN ROLE fixture_runtime;
CREATE ROLE fixture_plpgsql_owner SUPERUSER;
DROP EXTENSION plpgsql RESTRICT;
SET ROLE fixture_plpgsql_owner;
CREATE EXTENSION plpgsql;
RESET ROLE;
CREATE ROLE fixture_extension_owner;
GRANT CREATE ON DATABASE fixture_wallet TO fixture_extension_owner;
GRANT USAGE,CREATE ON SCHEMA public TO fixture_extension_owner;
SET ROLE fixture_extension_owner;
CREATE EXTENSION pgcrypto;
RESET ROLE;
CREATE SCHEMA fixture;
CREATE TYPE fixture.item_state AS ENUM ('active','expired');
CREATE TABLE fixture.credit_lots(id text primary key,issued bigint,available bigint,held bigint,spent bigint,expired bigint,refunded bigint,state fixture.item_state);
CREATE TABLE fixture.credit_entries(id bigserial primary key,journal_id text,delta bigint);
INSERT INTO fixture.credit_lots VALUES('synthetic-lot',100,90,0,10,0,0,'active');
INSERT INTO fixture.credit_entries(journal_id,delta) VALUES('synthetic-journal',-10),('synthetic-journal',10);
GRANT USAGE ON SCHEMA fixture TO fixture_runtime;
GRANT SELECT ON ALL TABLES IN SCHEMA fixture TO fixture_runtime;
ALTER DEFAULT PRIVILEGES IN SCHEMA fixture GRANT SELECT ON TABLES TO fixture_runtime;
GRANT CONNECT ON DATABASE fixture_wallet TO fixture_runtime;
ALTER DATABASE fixture_wallet SET timezone TO 'UTC';
"""
        lib.write_sql(run,source,'fixture_wallet',fixture_sql)
        diagnostic_path=SCRIPT.parent/'diagnose_encrypted_restore_drill_v2.py'
        diag_spec=importlib.util.spec_from_file_location('diagnostic_v2',diagnostic_path);diag=importlib.util.module_from_spec(diag_spec);diag_spec.loader.exec_module(diag)
        assert lib.psql(run,source['name'],source['user'],'postgres',diag.database_exists_statement(lib,'fixture_wallet')) is True
        assert lib.psql(run,source['name'],source['user'],'postgres',diag.database_exists_statement(lib,'missing_database')) is False
        dump,roles,inventory,window=lib.capture_database(backup,('central',source['name'],source['user'],'fixture_wallet'))
        assert dump.startswith(b'PGDMP')
        assert inventory['money']=={'lots_balanced':True,'journals_balanced':True}
        assert any(row['rolname']==source['user'] and row['rolpassword'] for row in inventory['roles'])
        assert len(inventory['tables'])==2 and len(inventory['sequences'])==1
        assert any(value['extname']=='pgcrypto' and value['extversion'] and value['owner']=='fixture_extension_owner' for value in inventory['extensions'])
        assert window['started_utc']<=window['finished_utc']
        entries={'databases/central.dump':dump,'roles/central.sql':roles}
        target['user']=inventory['bootstrap_role']
        started.append(target)
        steps=[]
        receipt=lib.restore_one(backup,target,entries,inventory,phase=steps.append)
        assert steps==['restore_central_'+step for step in ('start_target','roles_parse','roles_write','create_database','extensions','pg_restore','database_permissions','collect_inventory','compare')]
        assert receipt['matched'] is True and receipt['tables']==2 and receipt['rows']==3
        assert receipt['money']=={'lots_balanced':True,'journals_balanced':True}
        marker='SYNTHETIC_FAILURE_MARKER_'+uuid.uuid4().hex
        with pytest.raises(lib.Rejected):
            lib.write_sql(run,target,'postgres','ALTER ROLE fixture_runtime PASSWORD '+lib.literal(marker)+' INVALID_SYNTAX;')
        logs=original_popen([docker,'logs',target['name']],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        stdout,stderr=logs.communicate(timeout=10)
        assert marker.encode() not in stdout+stderr
        lib.match_inventories(inventory,lib.collect_inventory(run,target['name'],target['user'],'fixture_wallet'))
        lib.stop_target(backup,target)
        failed_target=lib.container_identity(uuid.uuid4().hex,'central');failed_target['user']=inventory['bootstrap_role'];started.append(failed_target)
        failed_steps=[]
        with pytest.raises(lib.Rejected):lib.restore_one(backup,failed_target,{**entries,'databases/central.dump':b'PGDMPinvalid-synthetic-fixture'},inventory,phase=failed_steps.append)
        assert failed_steps[-1]=='restore_central_pg_restore'
        assert lib.psql(run,failed_target['name'],failed_target['user'],'fixture_wallet',
            "SELECT to_json(count(*)) FROM information_schema.tables WHERE table_schema='fixture'")==0
        lib.stop_target(backup,failed_target)
        completed=True
    finally:
        for item in reversed(started):
            try:
                lib.stop_target(backup,item)
            except lib.Rejected:
                if completed:raise


def test_bootstrap_role_creation_only_is_skipped_while_attributes_are_restored(lib):
    original="""CREATE ROLE "initial_owner";\nALTER ROLE "initial_owner" WITH SUPERUSER LOGIN PASSWORD 'synthetic-only';\nCREATE ROLE "runtime";\n"""
    result=lib.bootstrap_roles_sql(original,'initial_owner')
    assert 'CREATE ROLE "initial_owner";' not in result
    assert 'ALTER ROLE "initial_owner" WITH SUPERUSER LOGIN PASSWORD' in result
    assert 'CREATE ROLE "runtime";' in result
    for bad in [original.replace('CREATE ROLE "initial_owner";',''),original+original]:
        with pytest.raises(lib.Rejected):lib.bootstrap_roles_sql(bad,'initial_owner')


def test_extensions_include_exact_version_owner_schema_and_config_tables(lib,monkeypatch):
    extension=[{'extname':'pgcrypto','extversion':'1.3','owner':'source_owner','schema':'public','config_tables':[]}]
    def read(command,name,user,database,statement,snapshot=None):
        if statement==lib.RELATIONS:return []
        if statement==lib.EXTENSIONS:return extension
        if 'pg_largeobject_metadata' in statement:return 0
        if 'WHERE oid=10' in statement:return 'source_owner'
        if statement==lib.DATABASE:return {'owner':'source_owner'}
        return []
    monkeypatch.setattr(lib,'psql',read)
    inventory=lib.collect_inventory(lambda *args,**kwargs:b'schema','source','owner','db')
    assert inventory['extensions']==extension
    bad=json.loads(json.dumps(inventory));bad['extensions'][0]['extversion']='older'
    with pytest.raises(lib.Rejected):lib.match_inventories(inventory,bad)


def test_temporary_entrypoint_postgres_is_not_ready_for_restore(lib):
    calls=[];comm=[b'bash\n',b'postgres\n']
    def command(args,**kwargs):
        calls.append(args)
        if args[-1]=='/proc/1/comm':return comm.pop(0)
        if 'pg_isready' in args:return b'ready'
        raise AssertionError('unexpected command')
    backup=SimpleNamespace(command=command,Rejected=lib.Rejected)
    item=lib.container_identity('a'*32,'central')
    assert lib.target_ready(backup,item) is False
    assert not any('pg_isready' in args for args in calls)
    assert lib.target_ready(backup,item) is True
    assert sum('pg_isready' in args for args in calls)==1


def test_phase_journal_is_append_only_private_and_rejects_freeform_data(lib,tmp_path):
    lib.write_phase(tmp_path,'capture_central')
    lib.write_phase(tmp_path,'restore_central_roles_write')
    path=tmp_path/'phases.jsonl';before=path.read_bytes();rows=[json.loads(row) for row in before.splitlines()]
    assert [row['phase'] for row in rows]==['capture_central','restore_central_roles_write']
    assert path.stat().st_mode&0o777==0o600
    for value in ['password=secret','bad\nphase','../escape']:
        with pytest.raises(lib.Rejected):lib.write_phase(tmp_path,value)
    assert path.read_bytes()==before


def test_extension_prepare_refuses_nonempty_target_before_sql(lib,monkeypatch):
    writes=[]
    monkeypatch.setattr(lib,'psql',lambda *args,**kwargs:[1,0,0])
    monkeypatch.setattr(lib,'write_sql',lambda *args,**kwargs:writes.append(args))
    with pytest.raises(lib.Rejected):lib.prepare_extensions(SimpleNamespace(command=None),lib.container_identity('a'*32,'central'),'db',[])
    assert writes==[]

def test_extension_seed_rebuild_is_restrict_transaction_and_restores_temp_privileges(lib,monkeypatch):
    expected={'extname':'plpgsql','extversion':'1.0','owner':'original_owner','schema':'pg_catalog'}
    current={**expected,'owner':'bootstrap'};state={'current':[current]};writes=[]
    def read(command,name,user,database,query,snapshot=None):
        if query==lib.EXTENSIONS:return state['current']
        if query.startswith('SELECT json_build_array'):return [0,0,0]
        if 'EXISTS' in query:return True
        if 'has_database_privilege' in query or 'has_schema_privilege' in query:return False
        raise AssertionError('unexpected query')
    def write(command,item,database,statement):writes.append(statement);state['current']=[expected]
    monkeypatch.setattr(lib,'psql',read);monkeypatch.setattr(lib,'write_sql',write)
    lib.prepare_extensions(SimpleNamespace(command=None),lib.container_identity('a'*32,'central'),'db',[expected])
    assert len(writes)==1
    sql=writes[0]
    assert 'DROP EXTENSION "plpgsql" RESTRICT' in sql and 'SET LOCAL ROLE "original_owner"' in sql
    assert "VERSION '1.0'" in sql and sql.startswith('BEGIN;') and sql.endswith('COMMIT;\n')
    assert sql.count('GRANT ')==3 and sql.count('REVOKE ')==3
    assert not any(value in sql for value in ('CASCADE','REASSIGN OWNED','SUPERUSER','ALTER ROLE'))

def test_extension_prepare_missing_schema_does_not_silently_create_or_execute(lib,monkeypatch):
    writes=[]
    def read(command,name,user,database,query,snapshot=None):
        if query==lib.EXTENSIONS:return []
        if query.startswith('SELECT json_build_array'):return [0,0,0]
        return False
    monkeypatch.setattr(lib,'psql',read);monkeypatch.setattr(lib,'write_sql',lambda *args:writes.append(args))
    with pytest.raises(lib.Rejected):lib.prepare_extensions(SimpleNamespace(command=None),lib.container_identity('a'*32,'central'),'db',[{'extname':'custom','owner':'original_owner','schema':'not_restored_yet','extversion':'1'}])


def r2_constraint_fixture(flat=False):
    result=[]
    specs=[('console_reconciliation_cases','reason',True),('execution_resolutions','reason',True),
           ('execution_resolutions','approval_reference',True),('execution_provider_attempts','request_id',False),
           ('execution_provider_associations','http_request_id',False),('execution_provider_associations','provider_response_id',False),
           ('execution_terminal_evidence','provider_response_id',False)]
    for table,column,trim in specs:
        length='length(btrim('+column+'))' if trim else 'length('+column+')'
        maximum=500 if trim else 256
        tail=('('+column+' = btrim('+column+")) AND ("+column+" !~ '[[:cntrl:]]'::text)") if trim else '('+column+" ~ '^[A-Za-z0-9._:-]+$'::text)"
        definition=('CHECK ((('+length+' >= 1) AND ('+length+' <= '+str(maximum)+') AND '+tail+'))') if flat else ('CHECK (((('+length+' >= 1) AND ('+length+' <= '+str(maximum)+')) AND '+tail+'))')
        result.append({'schema':'public','relation':table,'domain':None,'name':table+'_'+column+'_check','kind':'c',
                       'convalidated':True,'conislocal':True,'coninhcount':0,'connoinherit':False,
                       'condeferrable':False,'condeferred':False,'definition':definition})
    return result


def test_known_r2_checks_restore_original_between_in_one_restrict_transaction(lib):
    statement=lib.known_r2_check_plan(r2_constraint_fixture(),r2_constraint_fixture(True))
    assert statement.startswith('BEGIN;') and statement.endswith('COMMIT;')
    assert statement.count(' BETWEEN ')==7 and statement.count(' RESTRICT;')==7
    assert statement.count('ALTER TABLE ONLY ')==14
    assert 'CASCADE' not in statement and 'SUPERUSER' not in statement
    assert lib.known_r2_check_plan(r2_constraint_fixture(),r2_constraint_fixture())==''


@pytest.mark.parametrize('field,value', [('convalidated',False),('conislocal',False),('coninhcount',1),
                                       ('connoinherit',True),('condeferrable',True),('condeferred',True),
                                       ('name','private_variant'),('kind','u'),('domain','private_domain')])
@pytest.mark.parametrize('side',['source','target'])
def test_known_r2_checks_refuse_any_name_or_attribute_variant(lib,field,value,side):
    source,target=r2_constraint_fixture(),r2_constraint_fixture(True)
    (source if side=='source' else target)[-1][field]=value
    with pytest.raises(lib.Rejected):lib.known_r2_check_plan(source,target)


@pytest.mark.parametrize('side',['source','target'])
def test_known_r2_checks_refuse_changed_leaf_or_missing_constraint(lib,side):
    source,target=r2_constraint_fixture(),r2_constraint_fixture(True)
    (source if side=='source' else target)[0]['definition']=(source if side=='source' else target)[0]['definition'].replace('500','499')
    with pytest.raises(lib.Rejected):lib.known_r2_check_plan(source,target)
    source,target=r2_constraint_fixture(),r2_constraint_fixture(True)
    (source if side=='source' else target).pop()
    with pytest.raises(lib.Rejected):lib.known_r2_check_plan(source,target)


def test_known_r2_repair_validates_every_constraint_before_any_write(lib,monkeypatch):
    source,target=r2_constraint_fixture(),r2_constraint_fixture(True)
    target[-1]['definition']=target[-1]['definition'].replace('256','255')
    writes=[]
    monkeypatch.setattr(lib,'guard_target',lambda *_:None)
    monkeypatch.setattr(lib,'psql',lambda *_:target)
    monkeypatch.setattr(lib,'write_sql',lambda *args:writes.append(args))
    with pytest.raises(lib.Rejected):lib.restore_known_r2_checks(SimpleNamespace(command=None),lib.container_identity('a'*32,'central'),'db',source)
    assert writes==[]


def test_r2_schema_scope_is_explicit_and_never_selected_by_scanning(lib):
    schema='synthetic_private_'+uuid.uuid4().hex[:12]
    source=[{**row,'schema':schema} for row in r2_constraint_fixture()]
    target=[{**row,'schema':schema} for row in r2_constraint_fixture(True)]
    sql=lib.known_r2_check_plan(source,target,schema=schema)
    assert ('ALTER TABLE ONLY "'+schema+'".') in sql and '"public".' not in sql
    with pytest.raises(lib.Rejected):lib.known_r2_check_plan(source,target,schema='public')
    with pytest.raises(lib.Rejected):lib.known_r2_check_plan(source,target,schema='other_private_scope')


@pytest.mark.parametrize('data,expected', [(b'CENTRAL_DB_SCHEMA=public\n','public'),
    (b'PRIVATE_DSN=synthetic_only\nCENTRAL_DB_SCHEMA="scope_123"\n','scope_123'),
    (b"CENTRAL_DB_SCHEMA='Scope_123'\n",'Scope_123')])
def test_trusted_central_schema_only_reads_explicit_safe_identifier(lib,data,expected):
    assert lib.configured_central_schema(data)==expected


@pytest.mark.parametrize('data',[b'',b'OTHER=synthetic\n',b'CENTRAL_DB_SCHEMA=one\nCENTRAL_DB_SCHEMA=two\n',
    b'CENTRAL_DB_SCHEMA=unsafe;select\n',b'CENTRAL_DB_SCHEMA="with space"\n',
    b'CENTRAL_DB_SCHEMA=123invalid\n',b'CENTRAL_DB_SCHEMA='+b'a'*64+b'\n'])
def test_trusted_central_schema_refuses_missing_duplicate_or_unsafe_config(lib,data):
    with pytest.raises(lib.Rejected):lib.configured_central_schema(data)


def test_captured_schema_and_complete_config_must_match_plan(lib):
    data=b'PRIVATE_DSN=synthetic_a\nCENTRAL_DB_SCHEMA=scope_one\n'
    schema_sha=lib.digest(b'scope_one');config_sha=lib.digest(data)
    assert lib.bound_central_schema(data,schema_sha,config_sha)=='scope_one'
    with pytest.raises(lib.Rejected):lib.bound_central_schema(data.replace(b'scope_one',b'scope_two'),schema_sha,config_sha)
    with pytest.raises(lib.Rejected):lib.bound_central_schema(data.replace(b'synthetic_a',b'synthetic_b'),schema_sha,config_sha)
    with pytest.raises(lib.Rejected):lib.bound_central_schema(data,schema_sha,'0'*64)


@pytest.mark.skipif(os.environ.get('EASEL_RESTORE_DRILL_DOCKER')!='1', reason='explicit synthetic Docker environment required')
@pytest.mark.parametrize('schema',['public','synthetic_private_'+uuid.uuid4().hex[:12]],ids=['public','nonpublic'])
def test_public_r2_fresh_restore_keeps_raw_schema_and_all_constraint_attributes(lib,monkeypatch,schema):
    docker=shutil.which('docker');assert docker
    original_popen=subprocess.Popen
    monkeypatch.setattr(lib.subprocess,'Popen',lambda args,**kwargs:original_popen([docker if x=='/usr/bin/docker' else x for x in args],**kwargs))
    def run(args,maximum=lib.MAXIMUM,data=None,timeout=90):
        proc=original_popen([docker if x=='/usr/bin/docker' else x for x in args],stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:out,err=proc.communicate(data,timeout=timeout)
        except BaseException:proc.kill();proc.wait();raise
        if proc.returncode or len(out)>maximum:raise lib.Rejected('synthetic_command_rejected')
        return out
    source=lib.container_identity(uuid.uuid4().hex,'central');target=lib.container_identity(uuid.uuid4().hex,'central')
    backup=SimpleNamespace(command=run,Rejected=lib.Rejected)
    backup.container_gate=lambda _:lib.guard_target(backup,source)
    started=[];completed=False
    try:
        started.append(source);lib.start_target(backup,source)
        lib.write_sql(run,source,'postgres','CREATE DATABASE fixture_public_r2 OWNER '+lib.ident(source['user'])+';')
        if schema!='public':
            lib.write_sql(run,source,'fixture_public_r2','CREATE SCHEMA '+lib.ident(schema)+';')
            lib.write_sql(run,source,'postgres','ALTER DATABASE fixture_public_r2 SET search_path TO '+lib.ident(schema)+',pg_catalog;')
        migrations=sorted(path for path in (SCRIPT.parents[1]/'src/platform_service/migrations').glob('*.sql') if int(path.name[:3])<=13)
        assert len(migrations)==13
        for path in migrations:lib.write_sql(run,source,'fixture_public_r2','SET search_path TO '+lib.ident(schema)+',pg_catalog;\n'+path.read_text())
        assert len(lib.psql(run,source['name'],source['user'],'fixture_public_r2',lib.CONSTRAINTS))==377
        lib.write_sql(run,source,'fixture_public_r2',"""
CREATE TABLE public.synthetic_constraint_attributes(value integer);
ALTER TABLE public.synthetic_constraint_attributes ADD CONSTRAINT synthetic_not_valid CHECK(value>=0) NOT VALID;
ALTER TABLE public.synthetic_constraint_attributes ADD CONSTRAINT synthetic_no_inherit CHECK(value<100) NO INHERIT;
INSERT INTO public.synthetic_constraint_attributes VALUES(5);
CREATE FUNCTION public.synthetic_opaque_body() RETURNS text LANGUAGE sql AS $body$
SELECT $literal$
-- Name: fake; Type: TABLE; Schema: public; Owner: fake
CREATE TABLE fake (
    CONSTRAINT "console_reconciliation_cases_reason_check" CHECK (((a AND b) AND c))
);
DEFAULT 'AND'; "AND"; arithmetic (1 + (2 * 3)); BETWEEN 1 AND 2;
$literal$::text;
$body$;
""")
        dump,roles,inventory,_=lib.capture_database(backup,('central',source['name'],source['user'],'fixture_public_r2'))
        assert len(inventory['constraints'])==379
        assert any(row['convalidated'] is False for row in inventory['constraints'])
        assert any(row['connoinherit'] is True for row in inventory['constraints'])
        target['user']=inventory['bootstrap_role'];started.append(target);steps=[]
        receipt=lib.restore_one(backup,target,{'databases/central.dump':dump,'roles/central.sql':roles},inventory,steps.append,r2_check_repair=True,r2_schema=schema)
        assert receipt['matched'] is True and 'restore_central_known_checks' in steps
        restored=lib.collect_inventory(run,target['name'],target['user'],'fixture_public_r2')
        lib.match_inventories(inventory,restored)
        assert restored['schema']==inventory['schema'] and restored['constraints']==inventory['constraints']
        function="SELECT to_json(pg_get_functiondef('public.synthetic_opaque_body()'::regprocedure))"
        assert lib.psql(run,source['name'],source['user'],'fixture_public_r2',function)==lib.psql(run,target['name'],target['user'],'fixture_public_r2',function)
        completed=True
    finally:
        for item in reversed(started):
            try:lib.stop_target(backup,item)
            except lib.Rejected:
                if completed:raise
