"""No runtime control ownership, table reads or direct state mutation."""
import sys,importlib
from pathlib import Path
import pytest,psycopg
sys.path.insert(0,str(Path(__file__).parents[1]/"src"))
from b2_owned_pg import b2_pg
@pytest.mark.asyncio
async def test_runtime_cannot_open_control_or_read_permit_tokens(b2_pg):
    try:lib=importlib.import_module("platform_service.media_maintenance.schema")
    except ModuleNotFoundError:pytest.fail("B2 control privilege boundary is missing")
    profile=lib.SchemaProfile("local_001_020","public",b2_pg.roles["runtime"],b2_pg.roles["controller"],tuple(b2_pg.migrations))
    async with b2_pg.connection("owner") as c:
        await c.execute("DROP SCHEMA IF EXISTS easel_backup_control CASCADE");await c.commit();await lib.install_control_schema(c,profile);await c.commit()
    for statement in ["UPDATE easel_backup_control.control SET state='OPEN'", "DELETE FROM easel_backup_control.audit", "TRUNCATE easel_backup_control.audit", "SELECT token_hash FROM easel_backup_control.permits", "CREATE TABLE easel_backup_control.shadow(x int)"]:
        async with b2_pg.connection("runtime") as c:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):await c.execute(statement)
    async with b2_pg.connection("runtime") as c:
        columns=[x.name for x in (await c.execute("SELECT * FROM easel_backup_control.runtime_status")).description]
        assert not any("token" in x or "nonce" in x for x in columns)
