import argparse
import json

import pytest

from platform_service.__main__ import main, operate


@pytest.mark.parametrize('command,arguments', [('model-publish', ['--file', '/private/tmp/owned-fixture-model.json', '--operator', 'fixture-operator']), ('model-enable', ['--model', 'fixture-text-v1', '--operator', 'fixture-operator']), ('model-disable', ['--model', 'fixture-text-v1', '--operator', 'fixture-operator']), ('execution-work', [])])
def test_execution_commands_are_explicit_operator_surfaces(monkeypatch, capsys, command, arguments):
    import platform_service.__main__ as cli
    captured = []
    async def capture(args):
        captured.append(args)
        return {'command': args.command}
    monkeypatch.setattr(cli, 'operate', capture)
    assert main([command, *arguments]) == 0
    assert captured[0].command == command
    assert json.loads(capsys.readouterr().out) == {'command': command}


@pytest.mark.asyncio
async def test_model_publication_and_withdrawal_use_immutable_operator_catalog(db, test_dsn, monkeypatch, tmp_path):
    monkeypatch.setenv('CENTRAL_DATABASE_URL', test_dsn)
    monkeypatch.setenv('CENTRAL_PUBLIC_URL', 'https://central.example')
    monkeypatch.setenv('CENTRAL_DB_SCHEMA', db.schema)
    definition = {'id': 'fixture-text-v1', 'name': 'Fixture text', 'version': '1', 'capability': 'text', 'feature': 'text', 'products': ['easel'], 'test_only': True, 'provider_id': 'fixture', 'provider_version': '1', 'verification': 'fixture', 'verification_ref': 'owned-fixture-report', 'rate': {'version': 'fixture-rate-v1', 'input': {'numerator': '100', 'denominator': '1'}, 'output': {'numerator': '200', 'denominator': '1'}}, 'limits': {'max_input_bytes': 1048576, 'max_input_tokens': 65536, 'max_output_tokens': 4096}}
    file = tmp_path / 'model.json'
    file.write_text(json.dumps(definition))
    published = await operate(argparse.Namespace(command='model-publish', file=str(file), operator='fixture-operator'))
    assert published['id'] == 'fixture-text-v1'
    disabled = await operate(argparse.Namespace(command='model-disable', model='fixture-text-v1', operator='fixture-operator'))
    assert disabled['enabled'] is False
    enabled = await operate(argparse.Namespace(command='model-enable', model='fixture-text-v1', operator='fixture-operator'))
    assert enabled['enabled'] is True
