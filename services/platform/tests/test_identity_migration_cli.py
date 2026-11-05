"""Owner CLI routing, target binding and privacy; no real credentials."""
import argparse
import json
from types import SimpleNamespace
from contextlib import asynccontextmanager

import pytest


@pytest.mark.parametrize('command,extra',[
    ('identity-restore-proof',[]),
    ('identity-migration-inspect',['--proof-id='+'a'*32]),
    ('identity-migration-apply',['--proof-id='+'a'*32,'--key=fixture-key','--reason=Fixture migration'])])
def test_private_migration_commands_parse_and_only_print_safe_result(monkeypatch,capsys,command,extra):
    from platform_service import identity_migration_cli as cli
    from platform_service import __main__ as main
    seen=[]
    async def operate(args):
        seen.append(vars(args))
        return {'proof_id':'a'*32,'product_count':1,'console_count':1}
    monkeypatch.setattr(cli,'operate_identity_migration',operate)
    assert main.main([command,'--config-file=/private/tmp/fixture-config.json','--operator=fixture-owner',*extra])==0
    assert seen[0]['command']==command and seen[0]['operator']=='fixture-owner'
    assert json.loads(capsys.readouterr().out)=={'proof_id':'a'*32,'product_count':1,'console_count':1}


@pytest.mark.parametrize('arguments',[
    ['identity-restore-proof','--config-file=CANARY','--operator=fixture','--subject=PRIVATE-CANARY'],
    ['identity-migration-apply','--config-file=CANARY','--operator=fixture'],
    ['identity-restore-proof','--config-file=CANARY'],
])
def test_migration_argument_errors_do_not_echo_values_or_reach_operations(monkeypatch,capsys,arguments):
    from platform_service import identity_migration_cli as cli
    from platform_service import __main__ as main
    async def forbidden(args):raise AssertionError('must not open database')
    monkeypatch.setattr(cli,'operate_identity_migration',forbidden)
    assert main.main(arguments)==1
    output=capsys.readouterr()
    assert 'CANARY' not in output.out+output.err and 'Traceback' not in output.err


@pytest.mark.asyncio
async def test_migration_requires_explicit_owner_before_reading_config(monkeypatch):
    from platform_service.identity_migration_cli import operate_identity_migration
    monkeypatch.delenv('CENTRAL_MIGRATION_DATABASE_URL',raising=False)
    with pytest.raises(ValueError,match='identity_migration_owner_required'):
        await operate_identity_migration(argparse.Namespace(command='identity-restore-proof',config_file='/never/read',operator='fixture-owner'))


@pytest.mark.parametrize('owner,runtime,valid',[
    ('postgresql://owner:CANARY@127.0.0.1:5432/test','postgresql://runtime:PRIVATE@127.0.0.1/test',True),
    ('postgres://owner:CANARY@db.example:5432/test?sslmode=verify-full','postgresql://runtime:PRIVATE@db.example/test?sslmode=verify-full',True),
    ('postgresql://owner:CANARY@127.0.0.1/other','postgresql://runtime:PRIVATE@127.0.0.1/test',False),
    ('postgresql://owner:CANARY@127.0.0.2/test','postgresql://runtime:PRIVATE@127.0.0.1/test',False),
    ('postgresql://owner:CANARY@127.0.0.1/test?hostaddr=127.0.0.2','postgresql://runtime:PRIVATE@127.0.0.1/test',False),
    ('postgresql://owner:CANARY@127.0.0.1/test?options=unsafe','postgresql://runtime:PRIVATE@127.0.0.1/test',False),
])
def test_owner_target_is_same_database_without_indirect_routing(owner,runtime,valid):
    from platform_service.identity_migration_cli import require_same_database
    if valid:require_same_database(owner,runtime)
    else:
        with pytest.raises(ValueError,match='identity_migration_owner_target_mismatch'):
            require_same_database(owner,runtime)


@pytest.mark.parametrize('kind',['value','os','database','pool'])
def test_migration_runtime_errors_never_expose_credentials(monkeypatch,capsys,kind):
    from platform_service import identity_migration_cli as cli
    from platform_service import __main__ as main
    import psycopg
    from psycopg_pool import PoolTimeout
    async def fail(args):
        error={'value':ValueError,'os':OSError,'database':psycopg.OperationalError,'pool':PoolTimeout}[kind]
        raise error('PRIVATE-CANARY password and subject')
    monkeypatch.setattr(cli,'operate_identity_migration',fail)
    assert main.main(['identity-restore-proof','--config-file=/private/tmp/fixture','--operator=fixture'])==1
    output=capsys.readouterr()
    assert 'CANARY' not in output.out+output.err and 'Traceback' not in output.err
    assert 'Identity migration command failed' in output.err


@pytest.mark.asyncio
async def test_inspect_does_not_contact_identity_databases(monkeypatch):
    from platform_service import identity_migration_cli as cli
    from platform_service import identity_restore
    monkeypatch.setenv('CENTRAL_MIGRATION_DATABASE_URL','postgresql://owner:CANARY@127.0.0.1/test')
    monkeypatch.setenv('CENTRAL_DATABASE_URL','postgresql://runtime:PRIVATE@127.0.0.1/test')
    monkeypatch.setenv('CENTRAL_PUBLIC_URL','https://accounts.example')
    monkeypatch.setenv('CENTRAL_DB_SCHEMA','fixture_schema')
    policy=object();events=[]
    monkeypatch.setattr(identity_restore,'read_private_restore_config',lambda path:SimpleNamespace(policy=policy))
    async def forbidden(*args,**kwargs):raise AssertionError('inspect must not contact IdP')
    monkeypatch.setattr(identity_restore,'collect_restore_evidence',forbidden)
    class FakeDatabase:
        def __init__(self,*args,**kwargs):assert kwargs['schema']=='fixture_schema'
        async def open(self):events.append('open')
        async def close(self):events.append('close')
        @asynccontextmanager
        async def connection(self):
            class Connection:
                async def execute(self,query):return self
                async def fetchone(self):return {'owned':True}
            yield Connection()
    class FakeService:
        def __init__(self,db,p):assert p is policy
        async def inspect(self,proof_id,operator):
            assert proof_id=='a'*32 and operator.capabilities==frozenset({'identity:migrate'})
            return {'proof_id':proof_id,'conflict_count':0}
    monkeypatch.setattr(cli,'Database',FakeDatabase)
    monkeypatch.setattr(cli,'IdentityMigrationService',FakeService)
    result=await cli.operate_identity_migration(argparse.Namespace(command='identity-migration-inspect',config_file='/fixture',operator='fixture-owner',proof_id='a'*32))
    assert result['conflict_count']==0 and events==['open','close']


@pytest.mark.parametrize('value',[
    {'proof_id':'PRIVATE-SUBJECT-CANARY'},
    {'candidate_sha256':'PRIVATE-CANARY'},
    {'product_count':True},
    {'console_count':-1},
    {'expires_at':'PRIVATE-CANARY'},
    {'replayed':'PRIVATE-CANARY'},
])
def test_safe_summary_rejects_malformed_values_in_allowed_fields(value):
    from platform_service.identity_migration_cli import _safe_summary
    with pytest.raises(ValueError,match='invalid_identity_migration_result'):_safe_summary(value)


@pytest.mark.asyncio
async def test_actual_owner_check_precedes_collector_and_always_closes_pool(monkeypatch):
    from platform_service import identity_migration_cli as cli
    from platform_service import identity_restore
    from platform_service.errors import PlatformError
    monkeypatch.setenv('CENTRAL_MIGRATION_DATABASE_URL','postgresql://owner:CANARY@127.0.0.1/test')
    monkeypatch.setenv('CENTRAL_DATABASE_URL','postgresql://runtime:PRIVATE@127.0.0.1/test')
    monkeypatch.setenv('CENTRAL_PUBLIC_URL','https://accounts.example')
    monkeypatch.setenv('CENTRAL_DB_SCHEMA','fixture_schema')
    monkeypatch.setattr(identity_restore,'read_private_restore_config',lambda _:SimpleNamespace(policy=object()))
    async def forbidden(*args,**kwargs):
        raise AssertionError('non-owner must never read identity databases')
    monkeypatch.setattr(identity_restore,'collect_restore_evidence',forbidden)
    events=[]
    class FakeDatabase:
        def __init__(self,*args,**kwargs):pass
        async def open(self):events.append('open')
        async def close(self):events.append('close')
        @asynccontextmanager
        async def connection(self):
            class Connection:
                async def execute(self,_):return self
                async def fetchone(self):return {'owned':False}
            yield Connection()
    monkeypatch.setattr(cli,'Database',FakeDatabase)
    with pytest.raises(PlatformError,match='identity_migration_owner_required'):
        await cli.operate_identity_migration(argparse.Namespace(command='identity-restore-proof',
            config_file='/fixture',operator='fixture-owner'))
    assert events==['open','close']


@pytest.mark.asyncio
async def test_pool_open_error_cannot_log_private_driver_diagnostics_and_closes(monkeypatch,caplog):
    import logging
    import psycopg
    from platform_service import identity_migration_cli as cli
    from platform_service import identity_restore
    monkeypatch.setenv('CENTRAL_MIGRATION_DATABASE_URL','postgresql://owner:CANARY@127.0.0.1/test')
    monkeypatch.setenv('CENTRAL_DATABASE_URL','postgresql://runtime:PRIVATE@127.0.0.1/test')
    monkeypatch.setenv('CENTRAL_PUBLIC_URL','https://accounts.example')
    monkeypatch.setenv('CENTRAL_DB_SCHEMA','fixture_schema')
    monkeypatch.setattr(identity_restore,'read_private_restore_config',lambda _:SimpleNamespace(policy=object()))
    events=[]
    loggers=[logging.getLogger(name) for name in ('psycopg','psycopg.pool')]
    before=[logger.disabled for logger in loggers]
    class FailedDatabase:
        def __init__(self,*args,**kwargs):pass
        async def open(self):
            events.append('open')
            for logger in loggers:logger.warning('PRIVATE-DRIVER-CANARY user/database/certificate')
            raise psycopg.OperationalError('PRIVATE-DRIVER-CANARY')
        async def close(self):events.append('close')
    monkeypatch.setattr(cli,'Database',FailedDatabase)
    with caplog.at_level(logging.WARNING),pytest.raises(psycopg.OperationalError):
        await cli.operate_identity_migration(argparse.Namespace(command='identity-restore-proof',
            config_file='/fixture',operator='fixture-owner'))
    assert 'PRIVATE-DRIVER-CANARY' not in caplog.text
    assert events==['open','close']
    assert [logger.disabled for logger in loggers]==before
