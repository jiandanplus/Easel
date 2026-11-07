import secrets
from uuid import uuid4
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import pytest
from test_membership import seed


@pytest.mark.asyncio
async def test_runtime_rejects_migration_owner(db):
    from platform_service.errors import PlatformError

    with pytest.raises(PlatformError) as error:
        await db.require_runtime_role()
    assert error.value.code == "unsafe_runtime_role"


@pytest.mark.asyncio
@pytest.mark.parametrize("check", ["startup", "provision"])
@pytest.mark.parametrize(
    "table,column",
    [
        ("execution_models", "id"),
        ("execution_models", "definition"),
        ("execution_models", "operator_id"),
        ("execution_models", "created_at"),
        ("execution_model_states", "model_id"),
        ("execution_model_states", "enabled"),
        ("execution_model_states", "operator_id"),
        ("execution_model_states", "updated_at"),
    ],
)
async def test_runtime_rejects_or_removes_catalog_column_insert_grants(db, table, column, check):
    from platform_service.errors import PlatformError
    from platform_service.permissions import (
        assert_safe_runtime_role,
        grant_runtime_permissions,
    )

    role = "s3_column_insert_" + uuid4().hex
    async with db.connection() as c:
        await c.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
    try:
        async with db.connection() as c:
            await grant_runtime_permissions(c, role)
            await assert_safe_runtime_role(c, role)
            await c.execute(
                sql.SQL("GRANT INSERT ({}) ON {} TO {}").format(
                    sql.Identifier(column), sql.Identifier(table), sql.Identifier(role)
                )
            )
            privileges = await (
                await c.execute(
                    "SELECT has_table_privilege(%s,%s,'INSERT') AS table_insert, "
                    "has_any_column_privilege(%s,%s,'INSERT') AS column_insert",
                    (role, table, role, table),
                )
            ).fetchone()
            assert privileges == {"table_insert": False, "column_insert": True}
        if check == "startup":
            with pytest.raises(PlatformError) as error:
                async with db.connection() as c:
                    await assert_safe_runtime_role(c, role)
            assert error.value.code == "unsafe_runtime_role"
        else:
            # Reprovisioning may accept the role only after removing the grant.
            async with db.connection() as c:
                await grant_runtime_permissions(c, role)
                privileges = await (
                    await c.execute(
                        "SELECT has_any_column_privilege(%s,%s,'INSERT') AS unsafe",
                        (role, table),
                    )
                ).fetchone()
                assert privileges["unsafe"] is False
                await assert_safe_runtime_role(c, role)
    finally:
        async with db.connection() as c:
            await c.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            await c.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


@pytest.mark.asyncio
async def test_nonowner_runtime_works_and_cannot_destroy_history(db, test_dsn):
    from platform_service.db import Database
    from platform_service.permissions import grant_runtime_permissions
    from platform_service.membership import MembershipService
    from platform_service.credits import CreditLedger

    service, operator, plan, scope = await seed(db)
    role = "s1_runtime_" + uuid4().hex
    password = secrets.token_urlsafe(24)
    async with db.connection() as c:
        await c.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password)
            )
        )
    runtime = Database(
        make_conninfo(test_dsn, user=role, password=password), schema=db.schema
    )
    try:
        async with db.connection() as c:
            await grant_runtime_permissions(c, role)
        await runtime.open()
        await runtime.require_runtime_role()
        await MembershipService(runtime).issue_test(
            operator, "wallet", plan["id"], "low-role-issue"
        )
        ledger = CreditLedger(runtime)
        r = await ledger.reserve(
            scope,
            operation="fixture",
            key="one",
            body_hash="hash",
            amount=80,
            rate_version="v1",
        )
        await ledger.dispatch(scope, r["id"])
        await ledger.settle(scope, r["id"], actual=30, key="settle")
        assert (await ledger.summary(scope))["spent"] == "30"
        for command in [
            "TRUNCATE credit_entries",
            "ALTER TABLE credit_entries DISABLE TRIGGER ALL",
            "ALTER TABLE credit_entries ADD COLUMN malicious text",
            "DROP TABLE credit_entries",
            "UPDATE credit_entries SET delta=1",
            "DELETE FROM credit_entries",
            "UPDATE plans SET name='changed'",
        ]:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                async with runtime.connection() as c:
                    await c.execute(command)
        # Explicit extra privilege is detected before serving traffic.
        async with db.connection() as c:
            await c.execute(
                sql.SQL("GRANT TRUNCATE ON credit_entries TO {}").format(
                    sql.Identifier(role)
                )
            )
        from platform_service.errors import PlatformError

        with pytest.raises(PlatformError):
            await runtime.require_runtime_role()
    finally:
        await runtime.close()
        async with db.connection() as c:
            await c.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            await c.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


@pytest.mark.asyncio
async def test_runtime_rejects_owner_role_membership_even_noinherit(db, test_dsn):
    from platform_service.db import Database
    from platform_service.errors import PlatformError

    role = "s1_membership_" + uuid4().hex
    password = secrets.token_urlsafe(24)
    async with db.connection() as c:
        await c.execute(
            sql.SQL("CREATE ROLE {} LOGIN NOINHERIT PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password)
            )
        )
        owner = (await (await c.execute("SELECT current_user AS name")).fetchone())[
            "name"
        ]
        await c.execute(
            sql.SQL("GRANT {} TO {}").format(
                sql.Identifier(owner), sql.Identifier(role)
            )
        )
        await c.execute(
            sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                sql.Identifier(db.schema), sql.Identifier(role)
            )
        )
    runtime = Database(
        make_conninfo(test_dsn, user=role, password=password), schema=db.schema
    )
    try:
        await runtime.open()
        with pytest.raises(PlatformError):
            await runtime.require_runtime_role()
    finally:
        await runtime.close()
        async with db.connection() as c:
            await c.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            await c.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


@pytest.mark.asyncio
async def test_runtime_rejects_membership_in_non_superuser_table_owner(db, test_dsn):
    from platform_service.db import Database
    from platform_service.errors import PlatformError

    owner = "s1_owner_" + uuid4().hex
    role = "s1_child_" + uuid4().hex
    password = secrets.token_urlsafe(24)
    async with db.connection() as c:
        await c.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(owner)))
        await c.execute(
            sql.SQL("CREATE ROLE {} LOGIN NOINHERIT PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password)
            )
        )
        await c.execute(
            sql.SQL("GRANT {} TO {}").format(
                sql.Identifier(owner), sql.Identifier(role)
            )
        )
        await c.execute(
            sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                sql.Identifier(db.schema), sql.Identifier(role)
            )
        )
        await c.execute("CREATE TABLE role_ownership_probe(id text)")
        await c.execute(
            sql.SQL("ALTER TABLE role_ownership_probe OWNER TO {}").format(
                sql.Identifier(owner)
            )
        )
    runtime = Database(
        make_conninfo(test_dsn, user=role, password=password), schema=db.schema
    )
    try:
        await runtime.open()
        with pytest.raises(PlatformError):
            await runtime.require_runtime_role()
    finally:
        await runtime.close()
        async with db.connection() as c:
            await c.execute(
                sql.SQL("DROP OWNED BY {},{}").format(
                    sql.Identifier(role), sql.Identifier(owner)
                )
            )
            await c.execute(
                sql.SQL("DROP ROLE {},{}").format(
                    sql.Identifier(role), sql.Identifier(owner)
                )
            )
