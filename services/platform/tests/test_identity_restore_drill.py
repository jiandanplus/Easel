"""Pure boundary tests; this file never starts a database or Docker."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/verify_identity_restore_drill.py'


def load():
    assert SCRIPT.is_file(), 'owned restore drill harness must exist'
    spec = importlib.util.spec_from_file_location('restore_drill', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def parameters(**changes):
    return {'host':'127.0.0.1','port':'18777','dbname':'easel_s1_test_console_cbabf5af7060',
            'user':'fixture','password':'synthetic-not-a-secret',**changes}


def metadata(**changes):
    return {'name':'/easel-console-tests-cbabf5af7060','running':True,
            'image':'postgres@sha256:9e2cf6287f25c3151f4c9421ccce2b17f3e60061c111a29165e0d2cdd9ff0a97',
            'ports':{'5432/tcp':[{'HostIp':'127.0.0.1','HostPort':'18777'}]},
            'memory':805306368,'tmpfs':{'/var/lib/postgresql/data':'rw,noexec,nosuid,size=536870912'},**changes}


def test_harness_contract_exists():
    assert SCRIPT.is_file(), 'owned restore drill harness must exist'


def test_default_is_plan_without_reading_configuration_or_running_tools(monkeypatch,capsys):
    module=load()
    monkeypatch.setattr(module,'execute_drill',lambda *args:pytest.fail('must not execute'))
    assert module.main([])==0
    result=json.loads(capsys.readouterr().out)
    assert result['executed'] is False and result['verification']=='not_run'


@pytest.mark.parametrize('changes',[{'host':'localhost'},{'host':'115.190.211.112'},
    {'port':'18776'},{'dbname':'easel_workbench_preview'},{'service':'other'},
    {'options':'-c search_path=other'},{'hostaddr':'127.0.0.2'},{'user':''}])
def test_dsn_cannot_select_other_environment(changes):
    with pytest.raises(ValueError):load().validate_connection_parameters(parameters(**changes))


def test_approved_connection_and_owned_container():
    module=load()
    module.validate_connection_parameters(parameters())
    module.validate_container(metadata())


@pytest.mark.parametrize('changes',[{'name':'/other'},{'running':False},{'image':'postgres:latest'},
    {'ports':{'5432/tcp':[{'HostIp':'0.0.0.0','HostPort':'18777'}]}},
    {'memory':0},{'tmpfs':{}},{'tmpfs':{'/var/lib/postgresql/data':'size=1g'}}])
def test_wrong_container_binding_is_rejected(changes):
    with pytest.raises(ValueError):load().validate_container(metadata(**changes))


def test_names_are_nonce_bound_unique_and_postgres_safe():
    module=load();names=module.owned_names('a'*32)
    assert len(set(names.values()))==len(names)
    assert all(len(value)<=63 and value.replace('_','').isalnum() for value in names.values())
    assert names['restore_database'].startswith('easel_s1_test_restore_')
    with pytest.raises(ValueError):module.owned_names('a;DROP')


def test_dump_restore_commands_are_exact_and_have_no_password_or_cleanup():
    module=load();names=module.owned_names('b'*32)
    dump=module.dump_command('easel_s1_test_console_cbabf5af7060',names['schema'])
    restore=module.restore_command(names['restore_database'],'fixture')
    assert '--schema='+names['schema'] in dump
    assert '--format=custom' in dump and '--no-password' in dump
    assert '--single-transaction' in restore and '--exit-on-error' in restore
    assert '--no-owner' in restore and '--no-acl' in restore
    assert all('--clean' not in part and '--create' not in part and 'synthetic-not-a-secret' not in part for part in dump+restore)
    with pytest.raises(ValueError):module.restore_command('easel_workbench_preview','fixture')


def test_canonical_data_hash_is_order_independent_and_detects_corruption():
    module=load()
    rows={'accounts':[{'id':'a','amount':'9007199254740993'},{'id':'b','amount':'0'}]}
    reversed_rows={'accounts':list(reversed(rows['accounts']))}
    assert module.data_fingerprint(rows)==module.data_fingerprint(reversed_rows)
    assert module.data_fingerprint(rows)!=module.data_fingerprint({'accounts':[{'id':'a','amount':'9007199254740992'},{'id':'b','amount':'0'}]})


@pytest.mark.parametrize('field',['data_sha256','schema_sha256','sequence_sha256'])
def test_restore_comparison_cannot_hide_partial_restore(field):
    module=load();snapshot={'data_sha256':'a'*64,'schema_sha256':'b'*64,'sequence_sha256':'c'*64,'table_counts':{'x':1}}
    assert module.compare_snapshots(snapshot,snapshot)['all_equal'] is True
    with pytest.raises(ValueError):module.compare_snapshots(snapshot,{**snapshot,field:'d'*64})


def test_bad_cli_does_not_echo_sensitive_values(capsys):
    assert load().main(['--dsn-file','SYNTHETIC_CANARY','--execute-owned-drill'])==1
    value=capsys.readouterr()
    assert 'CANARY' not in value.out+value.err and 'Traceback' not in value.out


def semantic():
    return {'financial_balanced':True,'journal_balanced':True,'wallet_balanced':True,
            'settled_count':1,'pending_count':1,'pending_held_positive':True,
            'membership_count':1,'active_admin_match':True,'disabled_admin_match':True,
            'product_alias_same_user':True,'console_alias_count':2,'migration_count':1,
            'entry_count':3,'proof_count':1}


@pytest.mark.parametrize('field,value',[('financial_balanced',False),('journal_balanced',False),
    ('wallet_balanced',False),('pending_count',0),('pending_held_positive',False),
    ('membership_count',0),('active_admin_match',False),('disabled_admin_match',False),
    ('product_alias_same_user',False),('console_alias_count',1),('entry_count',0)])
def test_semantic_snapshot_requires_financial_and_identity_fixture(field,value):
    module=load()
    module.validate_semantics(semantic())
    with pytest.raises(ValueError):module.validate_semantics({**semantic(),field:value})


@pytest.mark.parametrize('mode',[0o644,0o400])
def test_private_connection_file_rejects_wrong_mode(tmp_path,monkeypatch,mode):
    module=load();path=tmp_path/'test-dsn.json'
    path.write_text(json.dumps({'dsn':'postgresql://fixture:synthetic@127.0.0.1:18777/'+parameters()['dbname']}))
    path.chmod(mode);monkeypatch.setattr(module,'APPROVED_DSN_FILE',path)
    with pytest.raises(ValueError):module.read_private_dsn(path)


def test_private_connection_file_symlink_is_never_followed(tmp_path,monkeypatch):
    module=load();target=tmp_path/'private';target.write_text('SYNTHETIC_CANARY');target.chmod(0o600)
    link=tmp_path/'test-dsn.json';link.symlink_to(target)
    monkeypatch.setattr(module,'APPROVED_DSN_FILE',link)
    with pytest.raises(OSError):module.read_private_dsn(link)


def test_cleanup_rejects_any_object_outside_nonce_before_connecting(monkeypatch):
    module=load();names=module.owned_names('c'*32)
    import psycopg
    monkeypatch.setattr(psycopg,'connect',lambda *a,**k:pytest.fail('must refuse before connection'))
    with pytest.raises(ValueError):module.cleanup_owned('unused',names,
        {'databases':['easel_workbench_preview'],'roles':[],'schema':False},'fixture')


def test_actual_snapshot_detects_changed_sequence_owned_by_without_database(monkeypatch):
    module=load()
    import psycopg
    owner={'column':'id'}

    class Rows:
        def __init__(self,rows):self.rows=rows
        def __iter__(self):return iter(self.rows)
        def fetchone(self):return self.rows[0] if self.rows else None
        def fetchall(self):return self.rows

    class Connection:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,statement,parameters=None):
            query=str(statement)
            if 'SELECT current_user=session_user' in query:return Rows([(True,)])
            if 'SELECT relname FROM pg_class' in query:return Rows([('fixture',)])
            if 'SELECT to_jsonb(t)' in query:return Rows([({'id':'synthetic'},)])
            if 'SELECT c.relname,s.seqstart' in query:return Rows([('fixture_seq',1,1,9223372036854775807,1,1,False)])
            if 'SELECT last_value,is_called' in query:return Rows([(1,True)])
            if 'pg_depend' in query:return Rows([('fixture_seq','s1_test_fixture','fixture',owner['column'],'a')])
            return Rows([])

    monkeypatch.setattr(psycopg,'connect',lambda *args,**kwargs:Connection())
    source=module.snapshot('synthetic','s1_test_fixture')
    owner['column']='different_column'
    target=module.snapshot('synthetic','s1_test_fixture')
    assert source['data_sha256']==target['data_sha256']
    assert source['sequence_sha256']!=target['sequence_sha256']
    with pytest.raises(ValueError):module.compare_snapshots(source,target)


@pytest.mark.parametrize('names,allowed',[
    (['pg_catalog','pg_toast','public','information_schema'],True),
    (['pg_catalog','public','information_schema','unexpected_namespace'],False),
])
def test_empty_database_allows_only_builtin_namespaces(names,allowed):
    module=load()
    if allowed:module.validate_empty_namespaces(names)
    else:
        with pytest.raises(ValueError):module.validate_empty_namespaces(names)


def test_restored_console_auth_uses_injected_database_without_keyword_dsn_settings(monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    import psycopg
    from platform_service import db,permissions,console_permissions,console_auth,console_service
    from platform_service.console_config import ConsoleSettings
    module=load();captured={}
    settings=ConsoleSettings(database_url='postgresql://unused@127.0.0.1/unused',
        public_url='https://central.example',issuer='https://identity.example/oidc',
        client_id='oidc-client',client_secret='synthetic-only',db_schema='synthetic')
    class Connection:
        async def execute(self,query,*args):
            if query=='SELECT token_hash FROM identity_tokens':
                raise psycopg.errors.InsufficientPrivilege('synthetic denied')
    class Database:
        def __init__(self,dsn,**kwargs):self.dsn=dsn
        async def open(self):pass
        async def close(self):pass
        async def require_runtime_role(self):pass
        @asynccontextmanager
        async def connection(self):yield Connection()
    async def grant(*args,**kwargs):pass
    class Auth:
        def __init__(self,database,actual_settings):
            captured.update(database=database,settings=actual_settings)
        async def authenticate(self,token):return SimpleNamespace(id='active-synthetic')
    class Queries:
        def __init__(self,database,auth):assert database is captured['database']
        async def wallet(self,*args):return {'memberships':[{'id':'synthetic'}]}
    monkeypatch.setattr(db,'Database',Database)
    monkeypatch.setattr(permissions,'grant_runtime_permissions',grant)
    monkeypatch.setattr(console_permissions,'grant_console_permissions',grant)
    monkeypatch.setattr(console_permissions,'assert_safe_console_role',grant)
    monkeypatch.setattr(console_auth,'ConsoleAuth',Auth)
    monkeypatch.setattr(console_service,'ConsoleQueries',Queries)
    seed={'settings':settings,'session_token':'synthetic-token','active_admin_id':'active-synthetic'}
    result=asyncio.run(module.check_low_roles('postgresql://synthetic:synthetic@127.0.0.1:18777/synthetic',
        'synthetic',module.owned_names('c'*32),seed,{'roles':[]}))
    assert captured['settings'] is settings
    assert captured['database'].dsn.startswith('user=')
    assert 's5a2_console_' in captured['database'].dsn
    assert result['actual_console_read'] is True
