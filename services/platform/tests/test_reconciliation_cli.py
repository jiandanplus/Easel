"""Owner-only, explicit plan/apply CLI. No production credentials or providers."""
import argparse
import json
import pytest


def test_writeoff_plan_cli_has_no_implicit_apply(monkeypatch,capsys):
    import platform_service.__main__ as command
    async def operation(args):
        assert args.command=='execution-writeoff-plan'
        assert args.jobs==['690ef707-da7f-4f8c-be18-1fdbf7645e40']
        assert args.operator=='synthetic-owner'
        assert args.approval_reference=='pending-human-review'
        assert not getattr(args,'reviewed_plan_hash',None)
        return {'kind':'platform_absorbed','plan_only':True}
    monkeypatch.setattr(command,'operate',operation)
    assert command.main(['execution-writeoff-plan','--job','690ef707-da7f-4f8c-be18-1fdbf7645e40','--reason','Synthetic unknown-cost review','--approval-reference','pending-human-review','--operator','synthetic-owner'])==0
    assert json.loads(capsys.readouterr().out)['plan_only'] is True


def test_writeoff_apply_requires_exact_review_hash_and_original_key(monkeypatch,capsys):
    import platform_service.__main__ as command
    async def operation(args):
        assert args.command=='execution-writeoff-apply'
        assert args.plan=='/private/controlled-plan.json'
        assert args.reviewed_plan_hash=='a'*64 and args.key=='single-operation'
        return {'operation_id':'synthetic-op'}
    monkeypatch.setattr(command,'operate',operation)
    assert command.main(['execution-writeoff-apply','--plan','/private/controlled-plan.json','--reviewed-plan-hash','a'*64,'--key','single-operation','--operator','synthetic-owner'])==0
    assert json.loads(capsys.readouterr().out)['operation_id']=='synthetic-op'
    with pytest.raises(SystemExit):
        command.main(['execution-writeoff-apply','--plan','/private/controlled-plan.json','--key','single-operation','--operator','synthetic-owner'])



def test_media_writeoff_commands_route_to_the_same_owner_only_entry(monkeypatch,capsys):
    import platform_service.__main__ as command
    seen=[]
    async def reconcile(args):
        seen.append(args.command);return {'subject':'media'}
    monkeypatch.setattr('platform_service.reconciliation_cli.operate_reconciliation',reconcile)
    assert command.main(['media-writeoff-plan','--job','81a6155d-c7dd-4b98-bd3f-5e909a4d3ba9','--reason','Synthetic undelivered image','--approval-reference','pending-human-review','--operator','synthetic-owner'])==0
    assert command.main(['media-writeoff-apply','--plan','/private/controlled-plan.json','--reviewed-plan-hash','a'*64,'--key','single-operation','--operator','synthetic-owner'])==0
    assert command.main(['media-writeoff-operation','--key','single-operation','--operator','synthetic-owner'])==0
    assert seen==['media-writeoff-plan','media-writeoff-apply','media-writeoff-operation']
    with pytest.raises(SystemExit):
        command.main(['media-writeoff-apply','--plan','/private/controlled-plan.json','--key','single-operation','--operator','synthetic-owner'])


@pytest.mark.asyncio
async def test_media_writeoff_cli_requires_owner_before_other_configuration(monkeypatch):
    from platform_service.reconciliation_cli import operate_reconciliation
    from platform_service.errors import PlatformError
    monkeypatch.delenv('CENTRAL_MIGRATION_DATABASE_URL',raising=False)
    with pytest.raises(PlatformError) as failure:
        await operate_reconciliation(argparse.Namespace(command='media-writeoff-plan'))
    assert failure.value.code=='reconciliation_owner_required'

@pytest.mark.asyncio
async def test_reconciliation_cli_requires_owner_before_other_configuration(monkeypatch):
    from platform_service.reconciliation_cli import operate_reconciliation
    from platform_service.errors import PlatformError
    monkeypatch.delenv('CENTRAL_MIGRATION_DATABASE_URL',raising=False)
    with pytest.raises(PlatformError) as failure:
        await operate_reconciliation(argparse.Namespace(command='execution-writeoff-plan'))
    assert failure.value.code=='reconciliation_owner_required'


@pytest.mark.asyncio
async def test_reconciliation_cli_rejects_other_database_before_connect(monkeypatch):
    from platform_service import reconciliation_cli as cli
    from platform_service.config import Settings
    from platform_service.errors import PlatformError
    monkeypatch.setenv('CENTRAL_MIGRATION_DATABASE_URL','postgresql://owner:synthetic-private@127.0.0.1:59999/wrong_database')
    monkeypatch.setattr(cli.Settings,'from_env',lambda:Settings(database_url='postgresql://runtime:synthetic@127.0.0.1:59999/intended_database',public_url='https://central.example.invalid'))
    def forbidden(*args,**kwargs):raise AssertionError('must_not_connect')
    monkeypatch.setattr(cli,'Database',forbidden)
    with pytest.raises(PlatformError) as failure:
        await cli.operate_reconciliation(argparse.Namespace(command='execution-writeoff-plan'))
    assert failure.value.code=='reconciliation_owner_target_mismatch'


def test_private_plan_reader_refuses_links_and_world_readable_files(tmp_path):
    from platform_service.reconciliation_cli import read_plan
    target=tmp_path/'plan.json';target.write_text('{"safe":"synthetic"}');target.chmod(0o644)
    with pytest.raises(ValueError,match='private_reconciliation_plan_required'):read_plan(target)
    target.chmod(0o600);assert read_plan(target)=={'safe':'synthetic'}
    link=tmp_path/'plan-link.json';link.symlink_to(target)
    with pytest.raises(ValueError,match='private_reconciliation_plan_required'):read_plan(link)


def test_private_plan_reader_bounds_data_and_hides_parse_content(tmp_path):
    from platform_service.reconciliation_cli import read_plan
    target=tmp_path/'plan.json';target.write_text('PRIVATE_SYNTHETIC_MARKER');target.chmod(0o600)
    with pytest.raises(ValueError) as failure:read_plan(target)
    assert 'PRIVATE_SYNTHETIC_MARKER' not in str(failure.value)
    target.write_bytes(b' '*65537)
    with pytest.raises(ValueError,match='invalid_reconciliation_plan_file'):read_plan(target)
