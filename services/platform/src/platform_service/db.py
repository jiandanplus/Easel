"""Psycopg async pool and transactional, checksum-protected SQL migrations."""

from contextlib import asynccontextmanager
import hashlib
from pathlib import Path
import re

from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .errors import PlatformError

MIGRATIONS_DIR = Path(__file__).with_name("migrations")


class Database:
    def __init__(
        self,
        dsn: str,
        *,
        min_size: int = 1,
        max_size: int = 5,
        schema: str | None = None,
    ):
        if schema is not None and (
            not isinstance(schema, str)
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", schema)
        ):
            raise ValueError("invalid controlled database schema")
        self.schema = schema
        self._pool = AsyncConnectionPool(
            dsn,
            min_size=min_size,
            max_size=max_size,
            open=False,
            kwargs={"row_factory": dict_row},
            configure=self._configure,
        )

    async def _configure(self, connection):
        if self.schema is not None:
            await connection.execute(
                sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema))
            )
            await connection.commit()

    async def open(self):
        await self._pool.open(wait=True)

    async def close(self):
        await self._pool.close()

    @asynccontextmanager
    async def connection(self):
        async with self._pool.connection() as connection:
            yield connection

    async def require_runtime_role(self):
        """Fail closed if runtime can own, rewrite or disable ledger protections."""
        from .permissions import assert_safe_runtime_role

        async with self.connection() as connection:
            await assert_safe_runtime_role(connection)

    async def migrate(self) -> list[str]:
        paths = sorted(
            path
            for path in MIGRATIONS_DIR.glob("*.sql")
            if re.fullmatch(r"[0-9]{3}_[a-z0-9_]+\.sql", path.name)
        )
        migrations = {path.name: path.read_bytes() for path in paths}
        async with self.connection() as connection:
            # The transaction-scoped lock covers bootstrap as well as every DDL file.
            # PostgreSQL advisory locks are database-local; schema separates test runs.
            row = await (
                await connection.execute("SELECT current_schema() AS schema")
            ).fetchone()
            lock_key = int.from_bytes(
                hashlib.sha256(
                    f"easel-central-migrations:{row['schema']}".encode()
                ).digest()[:8],
                "big",
                signed=True,
            )
            await connection.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key,))
            await connection.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                version text PRIMARY KEY,
                checksum text NOT NULL CHECK (checksum ~ '^[a-f0-9]{64}$'),
                applied_at timestamptz NOT NULL DEFAULT now()
            )""")
            applied = await (
                await connection.execute(
                    "SELECT version, checksum FROM schema_migrations ORDER BY version"
                )
            ).fetchall()
            existing = {row["version"]: row["checksum"] for row in applied}
            for version, checksum in existing.items():
                if version not in migrations:
                    raise PlatformError(500, "migration_file_missing")
                if hashlib.sha256(migrations[version]).hexdigest() != checksum:
                    raise PlatformError(500, "migration_checksum_mismatch")
            result = []
            for version, content in migrations.items():
                if version in existing:
                    continue
                await connection.execute(content.decode("utf-8"), prepare=False)
                await connection.execute(
                    "INSERT INTO schema_migrations(version, checksum) VALUES (%s, %s)",
                    (version, hashlib.sha256(content).hexdigest()),
                )
                result.append(version)
        return result
