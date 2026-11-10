"""Owned-schema fixtures. An explicit local PostgreSQL test DSN is mandatory."""
import ipaddress
import json
import os
from pathlib import Path
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
import pytest
import pytest_asyncio


@pytest.fixture
def test_dsn():
    value = os.environ.get("EASEL_CENTRAL_TEST_DSN")
    if not value and os.environ.get("EASEL_CENTRAL_TEST_DSN_FILE"):
        source = Path(os.environ["EASEL_CENTRAL_TEST_DSN_FILE"])
        value = json.loads(source.read_text())["dsn"] if source.suffix == ".json" else source.read_text().strip()
    if not value:
        pytest.fail("EASEL_CENTRAL_TEST_DSN or EASEL_CENTRAL_TEST_DSN_FILE is required; real PostgreSQL tests cannot skip")
    parsed = conninfo_to_dict(value)
    host = parsed.get("host", "")
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if not loopback or parsed.get("hostaddr", host) != host or not parsed.get("dbname", "").startswith("easel_s1_test"):
        pytest.fail("Test DSN must use a literal loopback host and easel_s1_test database prefix")
    if parsed.get("service"):
        pytest.fail("Test DSN must not use an indirect service definition")
    return value


@pytest_asyncio.fixture
async def bare_db(test_dsn):
    from platform_service.db import Database

    schema = "s1_test_" + uuid.uuid4().hex
    async with await psycopg.AsyncConnection.connect(test_dsn, autocommit=True) as control:
        await control.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    database = Database(test_dsn, schema=schema)
    try:
        await database.open()
        yield database
    finally:
        await database.close()
        async with await psycopg.AsyncConnection.connect(test_dsn, autocommit=True) as control:
            await control.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest_asyncio.fixture
async def db(bare_db):
    await bare_db.migrate()
    return bare_db
