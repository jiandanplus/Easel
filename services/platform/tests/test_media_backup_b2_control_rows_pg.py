"""Bounded wire fetches for potentially large historical maintenance rows."""

import time

import psycopg
from psycopg.types.json import Jsonb
import pytest

from b2_owned_pg import _owned_pg


@pytest.mark.asyncio
async def test_control_row_budget_never_prefetches_a_batch_of_large_audit_rows():
    from platform_service.media_backup_b2.capture import _BudgetConnection
    from platform_service.media_backup_b2.contracts import B2Error
    from platform_service.media_backup_b2.maintenance_snapshot import read_control_rows
    from platform_service.media_maintenance.schema import (
        SchemaProfile,
        install_control_schema,
    )

    owned = _owned_pg(16)
    env = next(owned)
    try:
        profile = SchemaProfile(
            "public_001_016",
            "public",
            env.roles["runtime"],
            env.roles["controller"],
            tuple(env.migrations),
        )
        async with env.connection("owner") as connection:
            await install_control_schema(connection, profile)
            await connection.commit()
        with psycopg.connect(env.dsn("owner"), autocommit=True) as raw:
            padding = Jsonb({"padding": "x" * (2 * 1024**2)})
            for _ in range(8):
                raw.execute(
                    "INSERT INTO easel_backup_control.audit(action,epoch,details) VALUES('synthetic_history',0,%s)",
                    (padding,),
                )
            raw.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            connection = _BudgetConnection(raw, time.monotonic_ns() + 20_000_000_000)
            original = connection.cursor
            observed = []

            class Cursor:
                def __init__(self, cursor):
                    self.cursor = cursor

                @property
                def itersize(self):
                    return self.cursor.itersize

                @itersize.setter
                def itersize(self, value):
                    self.cursor.itersize = value

                def __enter__(self):
                    self.cursor.__enter__()
                    return self

                def __exit__(self, *args):
                    return self.cursor.__exit__(*args)

                def execute(self, *args, **kwargs):
                    return self.cursor.execute(*args, **kwargs)

                def __iter__(self):
                    observed.append((type(self.cursor).__name__, self.cursor.itersize))
                    return iter(self.cursor)

            connection.cursor = lambda *args, **kwargs: Cursor(
                original(*args, **kwargs)
            )
            with pytest.raises(B2Error):
                read_control_rows(connection)
            assert len(observed) == 7
            assert all(
                kind == "ServerCursor" and max_rows == 1 for kind, max_rows in observed
            )
    finally:
        owned.close()
