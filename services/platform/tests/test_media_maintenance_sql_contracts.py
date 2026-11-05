"""Raw restricted-role SQL must reject incomplete maintenance authority.

Each case owns a new synthetic database and uses the actual OS-attested worker.
No Python controller validation or mocked database stands in for the SQL API.
"""

import copy
import hashlib
import secrets
import time
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from b2_owned_media import owned_media
from platform_service.media_maintenance.control import DatabaseControl


async def _snapshot(env):
    async with env.connection("owner") as connection:
        state = await (
            await connection.execute(
                "SELECT state,epoch,window_id,owner_hash,binding,original_state "
                "FROM easel_backup_control.control WHERE id=1"
            )
        ).fetchone()
        audit = await (
            await connection.execute(
                "SELECT id,action,window_id,epoch,details "
                "FROM easel_backup_control.audit ORDER BY id"
            )
        ).fetchall()
        permits = await (
            await connection.execute(
                "SELECT token_hash,epoch,status FROM easel_backup_control.permits "
                "ORDER BY token_hash"
            )
        ).fetchall()
        return state, audit, permits


async def _raw_call(env, role, statement, parameters):
    """Commit accepted inputs so an unintended acceptance cannot hide in rollback."""
    async with env.connection(role) as connection:
        try:
            row = await (await connection.execute(statement, parameters)).fetchone()
            await connection.commit()
            return None, row
        except psycopg.Error as error:
            await connection.rollback()
            return error.sqlstate, None


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_epoch", [None, -1, 0, 9223372036854775807])
async def test_raw_finish_rejects_wrong_epoch_without_completion_audit(
    tmp_path, bad_epoch
):
    async with owned_media(tmp_path) as value:
        env, generation = value["env"], value["generation"]
        permit = await generation.acquire("catalog", "unused-" + uuid4().hex)
        assert bad_epoch != permit.epoch
        before = await _snapshot(env)
        code, _ = await _raw_call(
            env,
            "runtime",
            "SELECT easel_backup_control.finish_permit(%s,%s,%s)",
            (permit._raw_token, bad_epoch, "a" * 64),
        )
        after = await _snapshot(env)
        assert code == "23514", "incomplete authority was accepted by raw SQL"
        assert after == before, "rejected authority changed control, audit or permits"
        assert any(
            row[0] == hashlib.sha256(permit._raw_token).hexdigest()
            and row[1] == permit.epoch
            and row[2] == "active"
            for row in after[2]
        )
        await generation.finish(permit, {"synthetic": "exact-original-epoch"})
        final = await _snapshot(env)
        assert len(final[2]) == len(before[2]) - 1
        assert len(final[1]) == len(before[1]) + 1
        assert final[1][-1][1] == "permit_completed"


async def _window_inputs(value):
    generation = value["generation"]
    controller = DatabaseControl(
        value["env"].factory("controller"), locks=value["locks"]
    )
    identity = generation.identity
    domain = generation.control._domain
    binding = {
        key: getattr(identity, key) for key in ("host_id", "boot_id", "process_start")
    }
    binding.update(
        {
            key: domain[key]
            for key in ("control_dev", "control_ino", "source_dev", "source_ino")
        }
    )
    binding["generation_digest"] = await controller.generation_digest()
    original = {**binding, "configuration_state": {}}
    binding["deadline_ns"] = time.monotonic_ns() + 150_000_000_000
    return secrets.token_bytes(32), str(uuid4()), binding, original


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    [
        "null_window",
        "non_v4_window",
        "null_nonce",
        "short_nonce",
        "null_binding",
        "json_null_binding",
        "generation_only_binding",
        "extra_binding_field",
        "missing_source_inode",
        "boolean_source_inode",
        "fractional_source_inode",
        "negative_source_inode",
        "oversized_source_inode",
        "null_original",
        "json_null_original",
        "array_original",
        "missing_configuration",
        "array_configuration",
        "mismatched_original",
        "extra_original_field",
    ],
)
async def test_raw_begin_rejects_incomplete_context_before_durable_drain(
    tmp_path, invalid
):
    async with owned_media(tmp_path) as value:
        nonce, window, binding, original = await _window_inputs(value)
        if invalid == "null_window":
            window = None
        elif invalid == "non_v4_window":
            window = "00000000-0000-0000-0000-000000000001"
        elif invalid == "null_nonce":
            nonce = None
        elif invalid == "short_nonce":
            nonce = b"short"
        elif invalid == "null_binding":
            binding = None
        elif invalid == "json_null_binding":
            binding = Jsonb(None)
        elif invalid == "generation_only_binding":
            binding = {"generation_digest": binding["generation_digest"]}
        elif invalid == "extra_binding_field":
            binding["unexpected"] = True
        elif invalid == "missing_source_inode":
            del binding["source_ino"]
        elif invalid == "boolean_source_inode":
            binding["source_ino"] = True
        elif invalid == "fractional_source_inode":
            binding["source_ino"] = 1.5
        elif invalid == "negative_source_inode":
            binding["source_ino"] = -1
        elif invalid == "oversized_source_inode":
            binding["source_ino"] = 2**63
        elif invalid == "null_original":
            original = None
        elif invalid == "json_null_original":
            original = Jsonb(None)
        elif invalid == "array_original":
            original = []
        elif invalid == "missing_configuration":
            del original["configuration_state"]
        elif invalid == "array_configuration":
            original["configuration_state"] = []
        elif invalid == "mismatched_original":
            original["host_id"] = "different-actual-context"
        elif invalid == "extra_original_field":
            original["unexpected"] = True
        else:
            raise AssertionError("unknown raw SQL counterexample")
        before = await _snapshot(value["env"])
        code, _ = await _raw_call(
            value["env"],
            "controller",
            "SELECT easel_backup_control.begin_window(%s,%s,%s,%s)",
            (
                nonce,
                window,
                binding
                if binding is None or isinstance(binding, Jsonb)
                else Jsonb(binding),
                original
                if original is None or isinstance(original, Jsonb)
                else Jsonb(original),
            ),
        )
        after = await _snapshot(value["env"])
        assert code == "23514", "incomplete recovery context durably entered DRAINING"
        assert after == before, "rejected context changed control, audit or permits"


@pytest.mark.asyncio
async def test_raw_begin_replays_only_exact_original_recovery_context(tmp_path):
    async with owned_media(tmp_path) as value:
        nonce, window, binding, original = await _window_inputs(value)
        statement = "SELECT easel_backup_control.begin_window(%s,%s,%s,%s)"
        arguments = (nonce, window, Jsonb(binding), Jsonb(original))
        code, row = await _raw_call(value["env"], "controller", statement, arguments)
        assert code is None and row == (1,)
        accepted = await _snapshot(value["env"])
        assert accepted[0][0] == "DRAINING"
        code, row = await _raw_call(value["env"], "controller", statement, arguments)
        assert code is None and row == (1,)
        assert await _snapshot(value["env"]) == accepted
        altered = copy.deepcopy(original)
        altered["configuration_state"] = {"different": "context"}
        code, _ = await _raw_call(
            value["env"],
            "controller",
            statement,
            (nonce, window, Jsonb(binding), Jsonb(altered)),
        )
        assert code == "23514", "idempotent retry accepted a different original context"
        assert await _snapshot(value["env"]) == accepted
