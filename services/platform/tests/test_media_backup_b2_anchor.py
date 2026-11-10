import hashlib
import json
import os
import pickle

import pytest

from media_backup_b2_fixture import BACKUP_ID, TARGET
from media_backup_b2_durable_fixture import roots, candidate_objects, cipher_receipt
from platform_service.media_backup_b2.contracts import B2Error, CommitRef
from platform_service.media_backup_b2 import durable, anchor


def intent_for(r, commit):
    policy = anchor.RootPolicy.from_operator(r.backup_fd, owner=r.owner)
    try:
        return anchor.trusted_anchor_intent(r.anchor_fd, policy=policy, backup_id=BACKUP_ID,
            target=TARGET, commit_sha256=hashlib.sha256(commit).hexdigest())
    finally: policy.close()


def test_independent_durable_anchor_precedes_atomic_commit_and_fixed_group(tmp_path):
    with roots(tmp_path / "roots") as r:
        with durable.create_private_job(r.backup_fd, BACKUP_ID) as job:
            raw = candidate_objects(job)
            with intent_for(r, raw) as intent:
                reference = anchor.publish_anchor(r.anchor_fd, intent, raw)
                assert isinstance(reference, CommitRef)
                assert (r.anchor / (BACKUP_ID + ".json")).stat().st_nlink == 1
                assert not (r.backup / BACKUP_ID / "COMMITTED.json").exists()
                job.bind_anchor(intent)
                assert durable.publish_commit_no_clobber(job, raw) == reference
                assert (r.backup / BACKUP_ID / "COMMITTED.json").read_bytes() == raw
                assert {p.name for p in (r.backup / BACKUP_ID).iterdir()} == {
                    "archive.easelbs2", "context.easelbs2", "COMMITTED.json"}
                assert (r.backup / BACKUP_ID / "COMMITTED.json").stat().st_nlink == 1
        os.fstat(r.anchor_fd)
        os.fstat(r.backup_fd)


def test_missing_real_anchor_cannot_be_replaced_with_json_bool_or_commitref(tmp_path):
    with roots(tmp_path / "roots") as r:
        for index, forged in enumerate((True, {}, CommitRef(BACKUP_ID, TARGET, "a" * 64, "b" * 64, "c" * 64))):
            # A failed admission poisons its own private job, never a prior job.
            identifier = f"12345678-1234-4234-9234-{index+1:012x}"
            with durable.create_private_job(r.backup_fd, identifier) as job:
                with pytest.raises(B2Error): job.bind_anchor(forged)
        with durable.create_private_job(r.backup_fd, BACKUP_ID) as job:
            raw = candidate_objects(job)
            with intent_for(r, raw) as intent:
                with pytest.raises(B2Error): job.bind_anchor(intent)
                assert not (r.backup / BACKUP_ID / "COMMITTED.json").exists()


def test_anchor_intent_cannot_be_json_or_pickle_authority(tmp_path):
    with roots(tmp_path / "roots") as r:
        with durable.create_private_job(r.backup_fd, BACKUP_ID) as job:
            raw = candidate_objects(job)
            with intent_for(r, raw) as intent:
                with pytest.raises((TypeError, B2Error)): json.dumps(intent)
                with pytest.raises((TypeError, B2Error)): pickle.dumps(intent)
            with pytest.raises((TypeError, B2Error)): anchor.RootPolicy({"owner": r.owner})


def test_trusted_restore_selector_cannot_be_rebound_after_issuance(tmp_path):
    with roots(tmp_path / "roots") as r:
        with durable.create_private_job(r.backup_fd, BACKUP_ID) as job:
            raw = candidate_objects(job)
            with intent_for(r, raw) as intent:
                for field, value in (("backup_id", "22345678-1234-4234-9234-123456789abc"),
                                     ("target", "other"), ("commit_sha256", "a" * 64)):
                    with pytest.raises(AttributeError): setattr(intent, field, value)


def test_anchor_root_cannot_be_the_backup_package_or_its_subtree(tmp_path):
    with roots(tmp_path / "roots") as r:
        policy = anchor.RootPolicy.from_operator(r.backup_fd, owner=r.owner)
        try:
            for fd in (r.backup_fd,):
                with pytest.raises(B2Error): anchor.trusted_anchor_intent(fd, policy=policy,
                    backup_id=BACKUP_ID, target=TARGET, commit_sha256="a" * 64)
            with durable.create_private_job(r.backup_fd, BACKUP_ID) as job:
                with pytest.raises(B2Error): anchor.trusted_anchor_intent(job._directory.fd, policy=policy,
                    backup_id=BACKUP_ID, target=TARGET, commit_sha256="a" * 64)
        finally: policy.close()


def test_default_root_policy_does_not_treat_unprivileged_test_uid_as_root(tmp_path):
    if os.geteuid() == 0: pytest.skip("root host already satisfies real root ownership")
    with roots(tmp_path / "roots") as r:
        with pytest.raises(B2Error): anchor.RootPolicy.from_operator(r.backup_fd)


def test_existing_anchor_and_commit_never_overwrite(tmp_path):
    with roots(tmp_path / "roots") as r:
        with durable.create_private_job(r.backup_fd, BACKUP_ID) as job:
            raw = candidate_objects(job)
            path = r.anchor / (BACKUP_ID + ".json")
            path.write_bytes(b"prior immutable anchor")
            path.chmod(0o600)
            with intent_for(r, raw) as intent:
                with pytest.raises(B2Error): anchor.publish_anchor(r.anchor_fd, intent, raw)
            assert path.read_bytes() == b"prior immutable anchor"


@pytest.mark.parametrize("fault", ["file_fsync", "link", "dir_fsync", "unlink"])
def test_anchor_publication_failure_never_authorizes_commit_and_keeps_private_stage(tmp_path, monkeypatch, fault):
    with roots(tmp_path / "roots") as r:
        with durable.create_private_job(r.backup_fd, BACKUP_ID) as job:
            raw = candidate_objects(job)
            with intent_for(r, raw) as intent:
                fsync = os.fsync
                calls = []
                def sync(fd):
                    calls.append(fd)
                    if fault == "file_fsync" or (fault == "dir_fsync" and len(calls) == 2):
                        raise OSError("PRIVATE SYNC")
                    return fsync(fd)
                def fail(*args, **kwargs): raise OSError("PRIVATE NAMING")
                with monkeypatch.context() as patch:
                    if "fsync" in fault: patch.setattr(durable.os, "fsync", sync)
                    if fault == "link": patch.setattr(durable.os, "link", fail)
                    if fault == "unlink": patch.setattr(durable.os, "unlink", fail)
                    with pytest.raises(B2Error): anchor.publish_anchor(r.anchor_fd, intent, raw)
                with pytest.raises(B2Error): job.bind_anchor(intent)
                assert list(r.anchor.iterdir()), "failed private stage must not be auto-removed"
                assert not (r.backup / BACKUP_ID / "COMMITTED.json").exists()


def test_anchor_path_replacement_is_rejected_using_held_parent_entries(tmp_path):
    with roots(tmp_path / "roots") as r:
        with durable.create_private_job(r.backup_fd, BACKUP_ID) as job:
            raw = candidate_objects(job)
            with intent_for(r, raw) as intent:
                r.anchor.rename(r.base / "anchor-moved")
                r.anchor.mkdir(mode=0o700)
                with pytest.raises(B2Error): anchor.publish_anchor(r.anchor_fd, intent, raw)
                assert list(r.anchor.iterdir()) == []


def test_commit_cannot_bind_inconsistent_object_final_metadata_even_when_file_hashes_match(tmp_path):
    from platform_service.media_backup_b2 import wire
    with roots(tmp_path / "roots") as r:
        with durable.create_private_job(r.backup_fd, BACKUP_ID) as job:
            rows = {}
            for kind in ("archive", "context"):
                payload = ("candidate " + kind).encode()
                receipt = cipher_receipt(payload)
                if kind == "archive": receipt["plan_sha256"] = "d" * 64
                sink = job.create_object(kind)
                sink.write(payload)
                with sink.finish_reopen(receipt): pass
                rows[kind] = dict(name=kind + ".easelbs2", bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
            raw = wire.encode_commit(dict(format="easel-backup-commit-v2", backup_id=BACKUP_ID,
                target=TARGET, window_id=BACKUP_ID, epoch=1, plan_sha256="b" * 64, registry_sha256="c" * 64, objects=rows))
            with intent_for(r, raw) as intent:
                anchor.publish_anchor(r.anchor_fd, intent, raw)
                job.bind_anchor(intent)
                with pytest.raises(B2Error): durable.publish_commit_no_clobber(job, raw)
                assert not (r.backup / BACKUP_ID / "COMMITTED.json").exists()


@pytest.mark.parametrize("fault", ["file_fsync", "link", "dir_fsync", "unlink"])
def test_commit_fault_has_no_success_receipt_and_never_deletes_objects_or_anchor(tmp_path, monkeypatch, fault):
    with roots(tmp_path / "roots") as r:
        with durable.create_private_job(r.backup_fd, BACKUP_ID) as job:
            raw = candidate_objects(job)
            with intent_for(r, raw) as intent:
                reference = anchor.publish_anchor(r.anchor_fd, intent, raw)
                job.bind_anchor(intent)
                calls, fsync = [], os.fsync
                def sync(fd):
                    calls.append(fd)
                    if fault == "file_fsync" or (fault == "dir_fsync" and len(calls) == 2):
                        raise OSError("PRIVATE COMMIT SYNC")
                    return fsync(fd)
                def fail(*args, **kwargs): raise OSError("PRIVATE COMMIT NAMING")
                with monkeypatch.context() as patch:
                    if "fsync" in fault: patch.setattr(durable.os, "fsync", sync)
                    if fault == "link": patch.setattr(durable.os, "link", fail)
                    if fault == "unlink": patch.setattr(durable.os, "unlink", fail)
                    with pytest.raises(B2Error): durable.publish_commit_no_clobber(job, raw)
                assert intent.observe() == reference
                assert (r.backup / BACKUP_ID / "archive.easelbs2").exists()
                assert (r.backup / BACKUP_ID / "context.easelbs2").exists()
                assert any(p.name.startswith(".record-") for p in (r.backup / BACKUP_ID).iterdir()) or fault == "dir_fsync"


def test_commit_collision_keeps_the_original_record_and_no_replace_fallback(tmp_path, monkeypatch):
    with roots(tmp_path / "roots") as r:
        with durable.create_private_job(r.backup_fd, BACKUP_ID) as job:
            raw = candidate_objects(job)
            with intent_for(r, raw) as intent:
                anchor.publish_anchor(r.anchor_fd, intent, raw)
                job.bind_anchor(intent)
                prior = r.backup / BACKUP_ID / "COMMITTED.json"
                prior.write_bytes(b"old commit record")
                prior.chmod(0o600)
                monkeypatch.setattr(durable.os, "replace", lambda *a, **k: pytest.fail("overwriting rename fallback"))
                with pytest.raises(B2Error): durable.publish_commit_no_clobber(job, raw)
                assert prior.read_bytes() == b"old commit record"


def test_policy_issuer_survives_exact_clones_and_nested_intent_clones(tmp_path):
    with roots(tmp_path / "roots") as r:
        with anchor.RootPolicy.from_operator(r.backup_fd, owner=r.owner) as policy:
            with policy._clone() as clone:
                with clone._clone() as nested:
                    assert clone is not policy and nested is not clone
                    assert clone._issuer is policy._issuer is nested._issuer
            with anchor.trusted_anchor_intent(r.anchor_fd, policy=policy,
                    backup_id=BACKUP_ID, target=TARGET, commit_sha256="a" * 64) as intent:
                with intent._clone() as clone:
                    with clone._clone() as nested:
                        assert intent is not clone and clone is not nested
                        assert intent._policy is not policy
                        assert nested._policy is not clone._policy
                        assert intent._policy._issuer is policy._issuer
                        assert clone._policy._issuer is nested._policy._issuer is policy._issuer
                        nested._check()


def test_fresh_policies_on_same_fd_have_distinct_issuers(tmp_path):
    with roots(tmp_path / "roots") as r:
        with anchor.RootPolicy.from_operator(r.backup_fd, owner=r.owner) as first:
            with anchor.RootPolicy.from_operator(r.backup_fd, owner=r.owner) as second:
                assert first._backup.identity == second._backup.identity
                assert first._issuer is not second._issuer
                with first._clone() as clone:
                    assert clone._issuer is first._issuer
                    assert clone._issuer is not second._issuer


def test_distinct_writer_reader_intents_share_issuer_without_reviving_closed_intent(tmp_path):
    with roots(tmp_path / "roots") as r:
        with anchor.RootPolicy.from_operator(r.backup_fd, owner=r.owner) as policy:
            with anchor.trusted_anchor_intent(r.anchor_fd, policy=policy,
                    backup_id=BACKUP_ID, target=TARGET, commit_sha256="a" * 64) as writer:
                with anchor.trusted_anchor_intent(r.anchor_fd, policy=policy,
                        backup_id=BACKUP_ID, target=TARGET, commit_sha256="a" * 64) as reader:
                    assert writer is not reader
                    assert writer._policy is not reader._policy
                    assert writer._policy._issuer is reader._policy._issuer is policy._issuer
                    writer.close()
                    assert writer._closed and writer._policy._closed
                    with pytest.raises(B2Error): writer._check()
                    with pytest.raises(B2Error): writer._clone()
                    with pytest.raises(B2Error): writer._policy._check()
                    reader._check()
                    with reader._clone() as clone:
                        assert clone._policy._issuer is policy._issuer
                        clone._check()
                    with pytest.raises(B2Error): writer._check()
                    reader.close()
                    with pytest.raises(B2Error): reader._check()
                    with pytest.raises(B2Error): reader._clone()
                    policy._check()
