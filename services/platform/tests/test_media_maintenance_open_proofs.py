"""Raw granted-controller OPEN must retain canonical digest value types."""

from contextlib import asynccontextmanager
import os

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
import pytest

from b2_owned_pg import b2_pg
from test_media_maintenance_pg import installed, local_locks


FIELDS = (
    "source_identity_sha256",
    "stable_side_effects_sha256",
    "original_state_sha256",
)
TABLES = (
    "schema_migrations",
    "settings",
    "control",
    "workers",
    "worker_attestations",
    "permits",
    "audit",
)


async def snapshot(env):
    result = {}
    async with env.connection("owner") as connection:
        for name in TABLES:
            query = sql.SQL("SELECT to_jsonb(t) FROM {}.{} t ORDER BY {}").format(
                sql.Identifier("easel_backup_control"),
                sql.Identifier(name),
                sql.SQL("id" if name == "audit" else "to_jsonb(t)::text"),
            )
            result[name] = await (await connection.execute(query)).fetchall()
        result["audit_sequence"] = await (
            await connection.execute(
                "SELECT last_value,is_called FROM easel_backup_control.audit_id_seq"
            )
        ).fetchone()
    return result


@asynccontextmanager
async def recovering(env, tmp_path):
    control = await installed(env)
    fd, locks = local_locks(tmp_path)
    owner = control.DatabaseControl(env.factory("controller"), locks=locks)
    identity = {
        "host_id": "synthetic-host",
        "boot_id": "synthetic-boot",
        "process_start": "1:2",
        "control_dev": locks.inode[0],
        "control_ino": locks.inode[1],
        "source_dev": os.fstat(fd).st_dev,
        "source_ino": os.fstat(fd).st_ino,
        "generation_digest": await owner.generation_digest(),
        "configuration_state": {},
    }
    try:
        window = await owner.begin_window(identity)
        await owner.acquire_window_exclusive(window)
        await owner.transition(window, expected_state="CAPTURING", next_state="VERIFYING", result={})
        await owner.transition(window, expected_state="VERIFYING", next_state="RECOVERING", result={})
        yield owner, window
    finally:
        for holder in owner._holders.values():
            holder.close()
        locks.close()
        os.close(fd)


async def raw_open(env, window, proof):
    async with env.connection("controller") as connection:
        await connection.execute(
            "SELECT easel_backup_control.transition(%s,%s,%s,'RECOVERING','OPEN',%s)",
            (window.owner_nonce, window.window_id, window.epoch, Jsonb(proof)),
        )
        await connection.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", FIELDS)
async def test_raw_open_rejects_numeric_digest_without_control_or_audit_change(b2_pg, tmp_path, field):
    async with recovering(b2_pg, tmp_path) as (owner, window):
        proof = {name: "1" * 64 for name in FIELDS}
        proof[field] = int("1" * 64)
        before = await snapshot(b2_pg)
        rejected = False
        try:
            await raw_open(b2_pg, window, proof)
        except psycopg.errors.CheckViolation:
            rejected = True
        after = await snapshot(b2_pg)
        state = (await owner.status())["state"]
        assert rejected and after == before and state == "RECOVERING", {
            "numeric_field": field,
            "rejected_with_23514": rejected,
            "state_after_raw_call": state,
            "audit_sequence_before": before["audit_sequence"],
            "audit_sequence_after": after["audit_sequence"],
            "last_audit_action": after["audit"][-1][0]["action"],
            "last_audit_digest_type": type(after["audit"][-1][0]["details"].get(field)).__name__,
        }


@pytest.mark.asyncio
async def test_raw_open_accepts_hex_digit_strings_and_exact_retry_is_immutable(b2_pg, tmp_path):
    async with recovering(b2_pg, tmp_path) as (owner, window):
        proof = {name: "1" * 64 for name in FIELDS}
        await raw_open(b2_pg, window, proof)
        first = await snapshot(b2_pg)
        assert (await owner.status())["state"] == "OPEN"
        assert first["audit"][-1][0]["details"] == proof
        assert all(type(value) is str for value in first["audit"][-1][0]["details"].values())
        await raw_open(b2_pg, window, proof)
        assert await snapshot(b2_pg) == first
