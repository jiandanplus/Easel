"""maintenance="local": media permits without the B2 control schema (the default)."""
import os
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio

from platform_service.media_config import MediaSettings
from platform_service.media_maintenance.contracts import MaintenanceError
from platform_service.media_maintenance.permits import MediaPermit
from platform_service.media_maintenance.runtime import RuntimeGeneration, validate


class _Transaction:
    async def __aenter__(self): return self
    async def __aexit__(self, *exc): return False


class _Connection:
    def transaction(self): return _Transaction()


class _NoSqlDatabase:
    """Permits must never need SQL in local mode."""
    @asynccontextmanager
    async def connection(self):
        yield _Connection()


def test_settings_default_to_local_and_reject_unknown_modes():
    assert MediaSettings().maintenance == "local"
    assert MediaSettings(maintenance="control").maintenance == "control"
    with pytest.raises(ValueError):
        MediaSettings(maintenance="off")


async def test_local_permit_lifecycle_without_control_schema(tmp_path):
    generation = RuntimeGeneration.local(_NoSqlDatabase(), storage_root=tmp_path)
    await generation.start()
    status = await generation.control.status()
    assert status["state"] == "OPEN"

    permit = await generation.acquire("upload", "invocation-1")
    assert type(permit) is MediaPermit
    await validate(permit)
    assert (await generation.observe(permit))["state"] == "active"

    with pytest.raises(MaintenanceError):
        await generation.close()  # an open permit blocks shutdown, as with the database control

    await generation.finish(permit, {"uploaded": "invocation-1"})
    assert permit._closed and not generation.permits
    with pytest.raises(MaintenanceError):
        await validate(permit)
    await generation.close()


async def test_local_job_permit_is_reused_and_unknown_is_sticky(tmp_path):
    generation = RuntimeGeneration.local(_NoSqlDatabase(), storage_root=tmp_path)
    await generation.start()
    first = await generation.acquire("job", "job-1", "attempt-1")
    assert await generation.acquire("job", "job-1", "attempt-1") is first
    await generation.unknown(first)
    assert not first._valid  # invalidated locally, as the database control does
    assert (await generation.control.observe_permit(first))["state"] == "unknown"
    with pytest.raises(MaintenanceError):
        await validate(first)


async def test_local_source_accepts_only_the_media_root_itself(tmp_path):
    root = tmp_path / "media"
    (root / "artifacts").mkdir(parents=True)
    (tmp_path / "link").symlink_to(root)
    generation = RuntimeGeneration.local(_NoSqlDatabase(), storage_root=root)
    generation.source.verify_path(root)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        generation.source.verify_fd(fd)
    finally:
        os.close(fd)
    for other in (root / "artifacts", tmp_path, tmp_path / "link", tmp_path / "missing"):
        with pytest.raises(MaintenanceError):
            generation.source.verify_path(other)


# ---- a real image job against PostgreSQL without the control schema ----

@pytest_asyncio.fixture
async def local_media(tmp_path):
    from b2_legacy_media_fixtures import DB, LegacyMediaEnvironment
    from b2_owned_pg import _owned_pg
    from platform_service.db import Database

    owned = _owned_pg(20)
    env = next(owned)
    database = Database(env.dsn('owner'), schema='public')
    environment = None
    try:
        await database.open()
        async with env.connection('owner') as connection:
            row = await (await connection.execute(
                "SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname='easel_backup_control')")).fetchone()
            assert row[0] is False  # nothing from B2 is installed
        source_path = tmp_path / 'local-media'
        source_path.mkdir(mode=0o700)
        runtime_db = DB(env, 'runtime')
        generation = RuntimeGeneration.local(runtime_db, storage_root=source_path)
        await generation.start()

        class LocalMediaEnvironment(LegacyMediaEnvironment):
            async def restart_generation(self, db):
                self.generation = RuntimeGeneration.local(db, storage_root=source_path)
                await self.generation.start()
                return self.generation

        environment = LocalMediaEnvironment(env, database, runtime_db, generation, None, generation.source)
        environment._source_path = source_path
        yield environment
    finally:
        if environment is not None:
            for runtime in reversed(environment.runtimes):
                await runtime.close()
        await database.close()
        if environment is not None:
            for role in tuple(environment.created_roles):
                await environment.drop_runtime_role(role)
        try:
            next(owned)
        except StopIteration:
            pass


async def test_image_job_settles_with_local_maintenance(tmp_path, local_media):
    import test_media_images_runtime as images
    await images.test_real_image_decode_hash_and_evidence_precede_settlement_without_repeat_post(tmp_path, local_media)


async def test_uncertain_upstream_keeps_hold_with_local_maintenance(tmp_path, local_media):
    import test_media_images_runtime as images
    await images.test_uncertain_and_invalid_upstream_never_resends_or_fakes_usage(tmp_path, 'no_response_id', local_media)
