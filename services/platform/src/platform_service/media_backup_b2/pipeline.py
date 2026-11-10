"""Import-only controller pipeline; explicit trusted inputs, never a scheduler.

The verification private key is supplied by the trusted caller. Local acceptance
uses generated synthetic keys only; this module never discovers a key or DSN.
Committed bytes and window restoration are distinct outcomes on receipt loss.
"""

import asyncio
from contextlib import closing
import hashlib
import os
import threading
import time

from media_backup.closure import write_archive
from media_backup.contract import canonical, encode_manifest, physical_size

from ..media_maintenance.control import DatabaseControl
from ..media_maintenance.contracts import MaintenanceError
from ..media_maintenance.identity import host_identity
from .anchor import RootPolicy, publish_anchor, trusted_anchor_intent
from .capture import (
    ChildRunner,
    OwnedInputs,
    capture_source,
    preflight_async,
    run_owned_io,
    verify_after_async,
)
from .contracts import B2Error, ExpectedV2, require, validate_id, validate_uuid
from .durable import create_private_job, publish_commit_no_clobber
from .profiles import SourceProfile
from .reader import read_committed
from .self_verify import verify_archive_content
from .stream import V2Decoder, V2Encoder, _pem
from . import wire


def recipient_binding(public_pem, private_pem):
    """Reject mismatched verification credentials before any maintenance intent."""
    try:
        public, recipient = _pem(public_pem)
        private, verification_recipient = _pem(private_pem, private=True)
        require(
            recipient == verification_recipient
            and private.public_key().public_numbers() == public.public_numbers()
        )
        return recipient
    except Exception:
        raise B2Error() from None


def _require_coverage(profile):
    # A fixed code registry, not a boolean or caller-supplied validation hook.
    from ..media_maintenance.coverage import require_capture_coverage

    require_capture_coverage(profile.name)


def observe_backup(root_anchor_intent, backup_job_fd, private_pem):
    """Read only the original exact group; do not invent a durability witness.

    This can establish that the committed, authenticated bytes currently exist.
    It neither repeats publication nor repairs an uncertain maintenance window.
    """
    try:
        plan, source = read_committed(root_anchor_intent, backup_job_fd, private_pem)
        with source:
            receipt = verify_archive_content(source, plan)
        return dict(
            status="committed_bytes_verified",
            backup_id=root_anchor_intent.backup_id,
            commit_sha256=root_anchor_intent.commit_sha256,
            archive_sha256=receipt["archive_sha256"],
            publication_durability_confirmed=False,
            maintenance_window_restored=False,
        )
    except Exception:
        raise B2Error() from None


class _CheckedSink:
    def __init__(self, sink, check):
        self.sink, self.check = sink, check

    def write(self, raw):
        self.check()
        count = self.sink.write(raw)
        self.check()
        return count


def _owner_call(loop, factory, deadline_ns):
    remaining = (deadline_ns - time.monotonic_ns()) / 1e9
    require(remaining > 0)
    future = asyncio.run_coroutine_threadsafe(factory(), loop)
    try:
        result = future.result(timeout=remaining)
        require(time.monotonic_ns() < deadline_ns)
        return result
    except BaseException:
        future.cancel()
        raise


async def _bounded_owner(factory, deadline_ns):
    remaining = (deadline_ns - time.monotonic_ns()) / 1e9
    require(remaining > 0)
    result = await asyncio.wait_for(factory(), timeout=remaining)
    require(time.monotonic_ns() < deadline_ns)
    return result


async def build_backup(
    controller,
    profile,
    owned_inputs,
    public_pem,
    backup_root_fd,
    anchor_root_fd,
    *,
    private_pem,
    backup_id,
    target,
    child_runner,
    root_policy,
):
    """Attempt exactly one explicit backup identity; no automatic nonce retry.

    A committed backup whose owner recovery fails returns a distinct uncertain
    window outcome. Earlier failures raise fixed invalid_backup and leave the
    original private job/control intent available for exact read-only diagnosis.
    Caller owns all supplied roots, policy, configs and inputs. Cancellation can
    leave actual IO alive; close() refuses those inputs until real work ends.
    """
    binding = exclusive = deadline = successful_outcome = None
    state = {
        "phase": None,
        "persisted": False,
        "commit_attempted": False,
        "commit_sha256": None,
        "post": None,
        "cancelled": threading.Event(),
    }
    inputs_closed = exclusive_accounted = cleanup_known = False
    companion = owned_inputs.companion if type(owned_inputs) is OwnedInputs else None
    try:
        require(
            type(controller) is DatabaseControl
            and type(profile) is SourceProfile
            and type(owned_inputs) is OwnedInputs
            and type(child_runner) is ChildRunner
            and type(root_policy) is RootPolicy
        )
        if companion is not None:
            require(companion._inputs is owned_inputs)
            companion._bind_backup_attempt(controller,state,root_policy,backup_id,child_runner)
        validate_uuid(backup_id)
        validate_id(target)
        recipient = recipient_binding(public_pem, private_pem)
        root_policy._check()
        require(
            root_policy.owner == owned_inputs.private_owner
            and (os.fstat(backup_root_fd).st_dev, os.fstat(backup_root_fd).st_ino)
            == root_policy._backup.identity[:2]
        )
        _require_coverage(profile)
        original = await preflight_async(
            owned_inputs, profile, controller.locks, host_identity=host_identity()
        )
        # The controller retains an original intent before RPC, so a lost begin
        # response is queried by its existing window ID, not a second begin.
        binding = await asyncio.wait_for(controller.begin_window(original), timeout=30)
        state["phase"] = "DRAINING"
        drain_deadline = controller.drain_deadline_ns(binding)
        while True:
            await _bounded_owner(
                lambda: controller.verify_window(binding, expected_state="DRAINING"),
                drain_deadline,
            )
            try:
                exclusive = await _bounded_owner(
                    lambda: controller.acquire_window_exclusive(binding), drain_deadline
                )
                break
            except MaintenanceError:
                # Only a known, unchanged drain intent can wait for a busy OFD.
                # RPC uncertainty remains closed and cannot be resubmitted here.
                require(binding.window_id not in controller.pending_window_ids)
                await _bounded_owner(
                    lambda: controller.verify_window(
                        binding, expected_state="DRAINING"
                    ),
                    drain_deadline,
                )
                await asyncio.sleep(0.01)
        state["phase"] = "CAPTURING"
        deadline = controller.phase_deadline_ns(binding)
        owned_inputs.bind_window(exclusive, phase_deadline_ns=deadline)
        captured = await _bounded_owner(
            lambda: capture_source(profile, exclusive, owned_inputs, child_runner),
            deadline,
        )
        await _bounded_owner(
            lambda: controller.verify_window(binding, expected_state="CAPTURING"),
            deadline,
        )
        loop = asyncio.get_running_loop()

        def guard():
            require(not state["cancelled"].is_set() and time.monotonic_ns() < deadline)
            exclusive.verify(binding)
            owned_inputs.checkpoint()
            _owner_call(
                loop,
                lambda: controller.verify_window(
                    binding, expected_state=state["phase"]
                ),
                deadline,
            )
            require(not state["cancelled"].is_set() and time.monotonic_ns() < deadline)

        def write_verify_publish():
            # All actual IO/resources below are owned by this retained thread,
            # including failure cleanup. Parent cancellation does not close them.
            guard()
            context = encode_manifest(captured.plan)
            plan_sha = hashlib.sha256(context).hexdigest()
            receipts, reopened, decoder_receipts = {}, {}, {}
            if companion is not None:
                state["owned_receipts"]=receipts
                state["owned_reopened"]=reopened
                state["owned_decoder_receipts"]=decoder_receipts
                state["owned_encoders"]={}
                state["owned_decoders"]={}
            with create_private_job(backup_root_fd, backup_id) as job:
                if companion is not None:companion._retain_publication_job(job)
                try:
                    for kind in ("archive", "context"):
                        guard()
                        expected = ExpectedV2(
                            backup_id,
                            "central-" + kind,
                            target,
                            recipient,
                            physical_size(captured.plan)
                            if kind == "archive"
                            else len(context),
                            plan_sha,
                            captured.plan["registry_sha256"],
                        )
                        sink = job.create_object(kind)
                        encoder = V2Encoder(
                            public_pem, expected, _CheckedSink(sink, guard)
                        )
                        if companion is not None:state["owned_encoders"][kind]=encoder
                        try:
                            if kind == "archive":
                                receipt = write_archive(
                                    captured.plan,
                                    captured.media_fd,
                                    captured.fixed_fds,
                                    _CheckedSink(encoder, guard),
                                    owner=captured.owner,
                                )
                            else:
                                encoder.write(context)
                                receipt = dict(
                                    status="context_plan_verified",
                                    plaintext_length=len(context),
                                    plaintext_sha256=plan_sha,
                                    plan_sha256=plan_sha,
                                    registry_sha256=captured.plan["registry_sha256"],
                                )
                            guard()
                            receipts[kind] = encoder.finish(receipt)
                            reopened[kind] = sink.finish_reopen(receipts[kind])
                            guard()
                        finally:
                            encoder.close()
                    _owner_call(
                        loop,
                        lambda: controller.transition(
                            binding,
                            expected_state="CAPTURING",
                            next_state="VERIFYING",
                            result={"objects": "durably_reopened"},
                        ),
                        deadline,
                    )
                    state["phase"] = "VERIFYING"
                    guard()
                    expected = ExpectedV2(
                        backup_id,
                        "central-context",
                        target,
                        recipient,
                        len(context),
                        plan_sha,
                        captured.plan["registry_sha256"],
                    )
                    with closing(
                        V2Decoder(private_pem, reopened["context"], expected)
                    ) as decoder:
                        offset = 0
                        while True:
                            guard()
                            raw = decoder.read(wire.CHUNK_BYTES)
                            if not raw:
                                break
                            require(raw == context[offset : offset + len(raw)])
                            offset += len(raw)
                        require(
                            offset == len(context)
                            and decoder.verified
                            and decoder.receipt == receipts["context"]
                        )
                        decoder_receipts["context"] = decoder.receipt
                        if companion is not None:state["owned_decoders"]["context"]=decoder
                    expected = ExpectedV2(
                        backup_id,
                        "central-archive",
                        target,
                        recipient,
                        physical_size(captured.plan),
                        plan_sha,
                        captured.plan["registry_sha256"],
                    )
                    with closing(
                        V2Decoder(private_pem, reopened["archive"], expected)
                    ) as decoder:
                        archive_receipt = verify_archive_content(decoder, captured.plan, check=guard)
                        require(decoder.verified and decoder.receipt == receipts["archive"])
                        decoder_receipts["archive"] = decoder.receipt
                        if companion is not None:
                            state["owned_decoders"]["archive"]=decoder
                            state["owned_archive_receipt"]=archive_receipt
                    state["post"] = owned_inputs.verify_after(captured)
                    guard()
                    if companion is not None:companion._record_authenticated_objects(receipts,decoder_receipts,archive_receipt,reopened)
                    commit = wire.encode_commit(
                        dict(
                            format="easel-backup-commit-v2",
                            backup_id=backup_id,
                            target=target,
                            window_id=binding.window_id,
                            epoch=binding.epoch,
                            plan_sha256=plan_sha,
                            registry_sha256=captured.plan["registry_sha256"],
                            objects={
                                kind: dict(
                                    name=kind + ".easelbs2",
                                    bytes=row["ciphertext_bytes"],
                                    sha256=row["ciphertext_sha256"],
                                )
                                for kind, row in receipts.items()
                            },
                        )
                    )
                    state["commit_sha256"] = hashlib.sha256(commit).hexdigest()
                    if companion is not None:state["owned_commit_bytes"]=commit
                    with trusted_anchor_intent(
                        anchor_root_fd,
                        policy=root_policy,
                        backup_id=backup_id,
                        target=target,
                        commit_sha256=state["commit_sha256"],
                    ) as intent:
                        guard()
                        publish_anchor(anchor_root_fd, intent, commit)
                        guard()
                        job.bind_anchor(intent)
                        state["commit_attempted"] = True
                        reference = publish_commit_no_clobber(job, commit)
                        if companion is not None:
                            state["owned_commit_ref"]=reference
                            state["owned_intent"]=intent
                        state["persisted"] = True
                        if companion is not None:companion._record_committed(reference,commit,intent)
                    guard()
                finally:
                    for source in reopened.values():
                        source.close()

        await _bounded_owner(
            lambda: run_owned_io(owned_inputs, write_verify_publish), deadline
        )
        require(state["persisted"] and not state["cancelled"].is_set())
        post = await _bounded_owner(
            lambda: verify_after_async(owned_inputs, captured), deadline
        )
        require(post == state["post"])
        await _bounded_owner(
            lambda: controller.transition(
                binding,
                expected_state="VERIFYING",
                next_state="RECOVERING",
                result={"backup": "committed"},
            ),
            deadline,
        )
        state["phase"] = "RECOVERING"
        result = dict(
            post, original_state_sha256=hashlib.sha256(canonical(original)).hexdigest()
        )
        await _bounded_owner(
            lambda: controller.transition(
                binding, expected_state="RECOVERING", next_state="OPEN", result=result
            ),
            deadline,
        )
        state["phase"] = "OPEN"
        if companion is not None:companion._record_open(binding,controller)
        successful_outcome = dict(
            status="backup_committed",
            backup_id=backup_id,
            target=target,
            commit_persisted=True,
            commit_sha256=state["commit_sha256"],
            window_state="OPEN",
            window_restored=True,
        )
        return successful_outcome
    except BaseException as error:
        state["cancelled"].set()
        # Never turn a failed/unknown/cancelled operation into finally OPEN.
        if binding is not None and state["phase"] not in (None, "OPEN"):
            try:
                await asyncio.wait_for(
                    controller.transition(
                        binding,
                        expected_state=state["phase"],
                        next_state="NEEDS_REVIEW",
                        result={"failure": "backup_incomplete_or_uncertain"},
                    ),
                    timeout=5,
                )
                state["phase"] = "NEEDS_REVIEW"
            except BaseException:
                pass
        if not isinstance(error, Exception):
            raise
        window_state = state["phase"] or "unknown"
        if binding is not None and binding.window_id in controller.pending_window_ids:
            window_state = "unknown"
        if state["persisted"]:
            return dict(
                status="backup_committed_window_uncertain",
                backup_id=backup_id,
                target=target,
                commit_persisted=True,
                commit_sha256=state["commit_sha256"],
                window_state=window_state,
                window_restored=False,
            )
        if state["commit_attempted"]:
            return dict(
                status="backup_commit_uncertain",
                backup_id=backup_id,
                target=target,
                commit_persisted=None,
                commit_sha256=state["commit_sha256"],
                window_state=window_state,
                window_restored=False,
            )
        raise B2Error() from None
    finally:
        # Actual in-flight capture/IO retains its own EX ref and inputs; close()
        # refuses to invalidate those descriptors. Failed private files remain.
        try:
            owned_inputs.close()
            inputs_closed = True
            cleanup_known = True
        except BaseException as cleanup:
            if not isinstance(cleanup, Exception):
                raise
            if successful_outcome is not None:
                successful_outcome["status"] = "backup_committed_cleanup_uncertain"
        # A holder retained by the controller is still needed by exact lost-
        # receipt observation/review. Closing it here would invalidate that
        # capability even when durable OPEN completed and its response was lost.
        if exclusive is not None and (
            binding is None
            or controller._holders.get(binding.window_id) is not exclusive
        ):
            try:
                exclusive.close()
                exclusive_accounted = True
            except BaseException as cleanup:
                cleanup_known = False
                if not isinstance(cleanup, Exception):
                    raise
                if successful_outcome is not None:
                    successful_outcome["status"] = "backup_committed_cleanup_uncertain"
        else:
            exclusive_accounted = True  # original controller retains known holder, or none acquired
        if successful_outcome is not None and time.monotonic_ns() >= deadline:
            successful_outcome["status"] = "backup_committed_receipt_expired"

        if companion is not None:
            seal=companion._finish_pipeline(inputs_closed=inputs_closed,exclusive_accounted=exclusive_accounted,cleanup_known=cleanup_known)
            if successful_outcome is not None and seal!='TERMINAL_SUCCESS' and successful_outcome['status']!='backup_committed_receipt_expired':
                successful_outcome['status']='backup_committed_cleanup_uncertain'
