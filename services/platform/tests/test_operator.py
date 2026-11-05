import argparse
import json

import pytest

from platform_service.__main__ import main, operate


@pytest.fixture
def operator_env(db, test_dsn, monkeypatch):
    monkeypatch.setenv("CENTRAL_DATABASE_URL", test_dsn)
    monkeypatch.setenv("CENTRAL_PUBLIC_URL", "https://accounts.example")
    monkeypatch.setenv("CENTRAL_DB_SCHEMA", db.schema)
    monkeypatch.delenv("CENTRAL_MIGRATION_DATABASE_URL", raising=False)


@pytest.mark.asyncio
async def test_accounts_operator_command_does_not_require_issuance_identity(operator_env):
    result = await operate(argparse.Namespace(command="accounts"))
    assert result == {"accounts": []}


@pytest.mark.asyncio
async def test_register_and_publish_are_idempotent_operator_actions(operator_env, tmp_path):
    args = argparse.Namespace(command="register-product", product="easel", client="desktop")
    assert await operate(args) == await operate(args)
    plan = {"id": "fixture-v1", "name": "Fixture", "version": "1", "included_products": ["easel"], "features": ["text"], "credits": "1000000", "days": 1, "concurrency": 1, "test_only": True}
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    publish = argparse.Namespace(command="publish-plan", operator="fixture-operator", file=str(path))
    assert await operate(publish) == plan
    assert await operate(publish) == plan


def test_cli_configuration_error_does_not_echo_secret(monkeypatch, capsys):
    monkeypatch.delenv("CENTRAL_PUBLIC_URL", raising=False)
    monkeypatch.setenv("CENTRAL_DATABASE_URL", "postgresql://user:private-fixture-password@localhost/db")
    assert main(["accounts"]) == 1
    assert "private-fixture-password" not in capsys.readouterr().err


@pytest.mark.parametrize("error_kind", ["database", "pool"])
def test_cli_connection_errors_are_safe(monkeypatch, capsys, error_kind):
    import platform_service.__main__ as command
    import psycopg
    from psycopg_pool import PoolTimeout

    async def unavailable(args):
        error = psycopg.OperationalError if error_kind == "database" else PoolTimeout
        raise error("fixture-secret-must-not-escape")

    monkeypatch.setattr(command, "operate", unavailable)
    assert command.main(["accounts"]) == 1
    output = capsys.readouterr()
    assert "fixture-secret-must-not-escape" not in output.err
    assert "Central command failed" in output.err
