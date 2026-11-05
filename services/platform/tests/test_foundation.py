import asyncio
from dataclasses import FrozenInstanceError
import subprocess
import sys

import psycopg
import pytest

from platform_service.contracts import OperatorIdentity, Scope, VerifiedIdentity
from platform_service.db import Database
from platform_service.errors import PlatformError
import platform_service.db as db_module


def scope(**overrides):
    return Scope(**(dict(user_id="u", product_id="easel", tenant_id="t", billing_account_id="b", session_id="s", authorization_epoch=0) | overrides))


@pytest.mark.parametrize("overrides", [{"product_id": ""}, {"tenant_id": " "}, {"user_id": "x" * 256}, {"session_id": 123}, {"authorization_epoch": -1}, {"authorization_epoch": True}])
def test_scope_rejects_invalid_ids_and_epoch(overrides):
    with pytest.raises(ValueError):
        scope(**overrides)


def test_contracts_are_immutable_and_validate_identity():
    with pytest.raises(FrozenInstanceError):
        scope().product_id = "other"
    assert OperatorIdentity("operator", frozenset({"plans:publish"})).capabilities == frozenset({"plans:publish"})
    assert VerifiedIdentity("https://issuer.test/oidc", "subject", None, False, 1).email is None
    with pytest.raises(ValueError):
        VerifiedIdentity("", "subject", None, False, 1)
    with pytest.raises(ValueError):
        OperatorIdentity("operator", {"mutable"})


def test_error_contains_only_stable_code():
    error = PlatformError(409, "idempotency_conflict")
    assert (error.status, error.code, error.retryable, str(error)) == (409, "idempotency_conflict", False, "idempotency_conflict")


async def test_first_migrations_apply_then_noop(bare_db):
    versions = await bare_db.migrate()
    assert "001_foundation.sql" in versions
    assert versions == sorted(versions)
    assert await bare_db.migrate() == []
    async with bare_db.connection() as conn:
        rows = await (await conn.execute("SELECT version, checksum FROM schema_migrations ORDER BY version")).fetchall()
    assert [row["version"] for row in rows] == versions
    assert all(len(row["checksum"]) == 64 for row in rows)


async def test_changed_migration_checksum_is_rejected(db, tmp_path, monkeypatch):
    for path in db_module.MIGRATIONS_DIR.glob("*.sql"):
        (tmp_path / path.name).write_bytes(path.read_bytes())
    foundation = tmp_path / "001_foundation.sql"
    foundation.write_bytes(foundation.read_bytes() + b"\n-- altered\n")
    monkeypatch.setattr(db_module, "MIGRATIONS_DIR", tmp_path)
    with pytest.raises(PlatformError) as failure:
        await db.migrate()
    assert failure.value.code == "migration_checksum_mismatch"


async def test_concurrent_migrations_are_serialized(bare_db, test_dsn):
    second = Database(test_dsn, schema=bare_db.schema)
    await second.open()
    try:
        results = await asyncio.gather(bare_db.migrate(), second.migrate())
    finally:
        await second.close()
    assert sum("001_foundation.sql" in versions for versions in results) == 1


async def test_connection_context_commits_and_rolls_back(db):
    async with db.connection() as conn:
        await conn.execute("INSERT INTO users(id) VALUES ('kept')")
    with pytest.raises(RuntimeError):
        async with db.connection() as conn:
            await conn.execute("INSERT INTO users(id) VALUES ('rolled_back')")
            raise RuntimeError("rollback")
    async with db.connection() as conn:
        rows = await (await conn.execute("SELECT id FROM users ORDER BY id")).fetchall()
    assert rows == [{"id": "kept"}]


async def test_composite_wallet_ownership_rejects_cross_tenant(db):
    async with db.connection() as conn:
        await conn.execute("INSERT INTO users(id) VALUES ('u')")
        await conn.execute("INSERT INTO tenants(id,owner_user_id) VALUES ('t1','u'),('t2','u')")
        await conn.execute("INSERT INTO billing_accounts(id,tenant_id) VALUES ('b','t1')")
        await conn.execute("CREATE TABLE ownership_probe (billing_account_id text, tenant_id text, FOREIGN KEY(billing_account_id,tenant_id) REFERENCES billing_accounts(id,tenant_id))")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        async with db.connection() as conn:
            await conn.execute("INSERT INTO ownership_probe VALUES ('b','t2')")


def test_package_is_independent_from_desktop_modules():
    result = subprocess.run([sys.executable, "-I", "-c", "import platform_service, platform_service.db; import sys; assert 'easel' not in sys.modules; assert 'web.app' not in sys.modules"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("schema", ["public;DROP SCHEMA public", "", "a" * 64])
def test_schema_must_be_controlled_identifier(schema):
    with pytest.raises(ValueError):
        Database("dbname=unused", schema=schema)
