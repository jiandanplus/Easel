import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tracemalloc
from types import SimpleNamespace

import pytest

from media_backup_fixture import capture
from media_backup.contract import encode_manifest
from media_backup_b2_fixture import BACKUP_ID, TARGET
from media_backup_b2_durable_fixture import roots, authenticated_objects
from test_media_backup_b2_anchor import intent_for
from platform_service.media_backup_b2.contracts import B2Error
from platform_service.media_backup_b2 import durable, anchor, reader


def published(r):
    with capture(r.base / "capture") as source:
        with durable.create_private_job(r.backup_fd, BACKUP_ID) as job:
            result = authenticated_objects(job, source)
            with intent_for(r, result.commit) as intent:
                anchor.publish_anchor(r.anchor_fd, intent, result.commit)
                job.bind_anchor(intent)
                durable.publish_commit_no_clobber(job, result.commit)
    return result


def job_fd(r):
    return os.open(BACKUP_ID, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=r.backup_fd)


def consume(value):
    digest = hashlib.sha256()
    while True:
        raw = value.read(1048576)
        if not raw: return digest.hexdigest()
        digest.update(raw)


def test_committed_reader_authenticates_context_before_returning_plan_and_owned_archive(tmp_path):
    with roots(tmp_path / "roots") as r:
        result = published(r)
        fd = job_fd(r)
        try:
            with intent_for(r, result.commit) as intent:
                plan, archive = reader.read_committed(intent, fd, result.private)
            # Reader retains its own exact trust/FD references, not caller lifetime.
            os.fstat(fd)
            with archive:
                assert plan == result.plan
                with pytest.raises(io.UnsupportedOperation): archive.fileno()
                assert not archive.verified
                assert consume(archive) == result.receipts["archive"]["plaintext_sha256"]
                assert archive.verified and archive.receipt == result.receipts["archive"]
        finally: os.close(fd)


@pytest.mark.parametrize("name", ["COMMITTED.json", "context.easelbs2", "archive.easelbs2"])
def test_partial_group_cannot_supply_a_trusted_plan(tmp_path, name):
    with roots(tmp_path / "roots") as r:
        result = published(r)
        (r.backup / BACKUP_ID / name).unlink()
        fd = job_fd(r)
        try:
            with intent_for(r, result.commit) as intent:
                with pytest.raises(B2Error): reader.read_committed(intent, fd, result.private)
        finally: os.close(fd)


@pytest.mark.parametrize("name", ["COMMITTED.json", "context.easelbs2", "archive.easelbs2"])
@pytest.mark.parametrize("fault", ["tamper", "trailing", "hardlink", "mode", "symlink", "fifo"])
def test_untrusted_object_or_commit_changes_fail_before_acceptance(tmp_path, name, fault):
    with roots(tmp_path / "roots") as r:
        result = published(r)
        path = r.backup / BACKUP_ID / name
        if fault == "tamper":
            raw = path.read_bytes()
            path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
        if fault == "trailing":
            with path.open("ab") as output: output.write(b"\0")
        if fault == "hardlink": os.link(path, r.base / "other")
        if fault == "mode": path.chmod(0o644)
        if fault == "symlink":
            path.rename(r.base / "other")
            path.symlink_to(r.base / "other")
        if fault == "fifo":
            path.unlink()
            os.mkfifo(path, 0o600)
        fd = job_fd(r)
        try:
            with intent_for(r, result.commit) as intent:
                with pytest.raises(B2Error): reader.read_committed(intent, fd, result.private)
        finally: os.close(fd)


def test_missing_or_wrong_independent_root_anchor_never_bootstraps_from_package(tmp_path):
    with roots(tmp_path / "roots") as r:
        result = published(r)
        (r.anchor / (BACKUP_ID + ".json")).unlink()
        fd = job_fd(r)
        try:
            with intent_for(r, result.commit) as intent:
                with pytest.raises(B2Error): reader.read_committed(intent, fd, result.private)
            with pytest.raises(B2Error): reader.read_committed(json.loads(result.commit), fd, result.private)
            with anchor.RootPolicy.from_operator(r.backup_fd, owner=r.owner) as policy:
                with pytest.raises(B2Error):
                    anchor.trusted_anchor_intent(fd, policy=policy, backup_id=BACKUP_ID,
                        target=TARGET, commit_sha256=hashlib.sha256(result.commit).hexdigest())
        finally: os.close(fd)


def test_object_replacement_after_read_open_invalidates_later_plaintext_and_eof(tmp_path):
    with roots(tmp_path / "roots") as r:
        result = published(r)
        fd = job_fd(r)
        try:
            with intent_for(r, result.commit) as intent:
                _, value = reader.read_committed(intent, fd, result.private)
            with value:
                path = r.backup / BACKUP_ID / "context.easelbs2"
                raw = path.read_bytes()
                path.rename(r.base / "old-context")
                path.write_bytes(raw)
                path.chmod(0o600)
                with pytest.raises(B2Error): value.read(1)
                assert not value.verified
        finally: os.close(fd)


def test_fresh_process_has_only_trusted_anchor_ciphers_and_synthetic_restore_key(tmp_path):
    with roots(tmp_path / "roots") as r:
        result = published(r)
        key = r.base / "synthetic-test-key.pem"
        key.write_bytes(result.private)
        key.chmod(0o600)
        clean = r.base / "fresh-process"
        clean.mkdir(mode=0o700)
        package = Path(__file__).resolve().parents[1]
        code = """import hashlib,json,os,sys
from pathlib import Path
from media_backup.contract import encode_manifest
from platform_service.media_backup_b2.anchor import RootPolicy,trusted_anchor_intent
from platform_service.media_backup_b2.reader import read_committed
flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
bfd=os.open(sys.argv[1],flags);afd=os.open(sys.argv[2],flags);jfd=os.open(sys.argv[4],flags,dir_fd=bfd)
try:
 with RootPolicy.from_operator(bfd,owner=(os.geteuid(),os.getegid())) as policy:
  with trusted_anchor_intent(afd,policy=policy,backup_id=sys.argv[4],target=sys.argv[5],commit_sha256=sys.argv[6]) as intent:
   plan,source=read_committed(intent,jfd,Path(sys.argv[3]).read_bytes())
 with source:
  digest=hashlib.sha256()
  while True:
   raw=source.read(1048576)
   if not raw:break
   digest.update(raw)
  print(json.dumps({'plan_sha256':hashlib.sha256(encode_manifest(plan)).hexdigest(),'archive_sha256':digest.hexdigest(),'verified':source.verified}))
finally:
 os.close(jfd);os.close(afd);os.close(bfd)
"""
        completed = subprocess.run([sys.executable, "-B", "-c", code, str(r.backup), str(r.anchor), str(key),
            BACKUP_ID, TARGET, hashlib.sha256(result.commit).hexdigest()], cwd=clean,
            env={"PATH": os.defpath, "LANG": "C", "PYTHONPATH": str(package / "src") + os.pathsep + str(package / "scripts")},
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        assert completed.returncode == 0, completed.stderr.decode()[-1000:]
        safe = json.loads(completed.stdout)
        assert safe == dict(plan_sha256=hashlib.sha256(encode_manifest(result.plan)).hexdigest(),
                            archive_sha256=result.receipts["archive"]["plaintext_sha256"], verified=True)
        assert {p.name for p in (r.backup / BACKUP_ID).iterdir()} == {
            "archive.easelbs2", "context.easelbs2", "COMMITTED.json"}


@pytest.mark.parametrize("field,bad", [("backup_id", "22345678-1234-4234-9234-123456789abc"),
    ("target", "another-target"), ("commit_sha256", "a" * 64)])
def test_wrong_external_restore_selector_does_not_accept_self_reported_commit(tmp_path, field, bad):
    with roots(tmp_path / "roots") as r:
        result = published(r)
        fd = job_fd(r)
        values = dict(backup_id=BACKUP_ID, target=TARGET, commit_sha256=hashlib.sha256(result.commit).hexdigest())
        values[field] = bad
        try:
            with anchor.RootPolicy.from_operator(r.backup_fd, owner=r.owner) as policy:
                with anchor.trusted_anchor_intent(r.anchor_fd, policy=policy, **values) as intent:
                    with pytest.raises(B2Error): reader.read_committed(intent, fd, result.private)
        finally: os.close(fd)


def _adversarial_authenticated_group(job, *, bad_plan=False, wrong_final=False,
                                     bad_registry=False, wrong_archive_length=False):
    """Trusted test deliberately anchors bad input to exercise the reader boundary.

    Durability receipts cannot stand in for T11 authenticated content self-verify.
    """
    from media_backup_b2_fixture import small_plan, synthetic_keys
    from test_media_backup_b2_stream import forged, sealed
    from media_backup.contract import physical_size, member_records, Limits
    from media_backup.ustar import TarWriter
    from platform_service.media_backup_b2.contracts import ExpectedV2
    from platform_service.media_backup_b2 import wire
    plan, raw_plan = small_plan()
    context = (b"{}" if bad_plan else json.dumps({**plan, "registry_sha256": "f" * 64},
        sort_keys=True, separators=(",", ":")).encode() if bad_registry else raw_plan)
    plan_sha = hashlib.sha256(context).hexdigest()
    registry = plan["registry_sha256"]
    value = ExpectedV2(BACKUP_ID, "central-context", TARGET, synthetic_keys()[2], len(context), plan_sha, registry)
    changes = {"plan_sha256": "d" * 64} if wrong_final else None
    context_cipher, _ = forged(context, value=value, final_changes=changes)
    # The archive's independently valid B1 manifest cannot rescue a bad context.
    raw_archive = io.BytesIO()
    writer = TarWriter(raw_archive, physical_size(plan), Limits().max_archive_bytes, 1048576)
    writer.add("manifest.json", len(raw_plan), (raw_plan,))
    for row in member_records(plan): writer.add(row["name"], 0, ())
    writer.finish()
    archive_plain = b"x" if wrong_archive_length else raw_archive.getvalue()
    archive_value = ExpectedV2(BACKUP_ID, "central-archive", TARGET, synthetic_keys()[2], len(archive_plain), plan_sha, registry)
    archive_cipher, _, archive_receipt = sealed(archive_plain, value=archive_value)
    rows = {}
    for kind, cipher in (("archive", archive_cipher), ("context", context_cipher)):
        meta = (archive_receipt if kind == "archive" else dict(plaintext_length=len(context),
            plaintext_sha256=plan_sha, plan_sha256=plan_sha, registry_sha256=registry,
            data_records=1, ciphertext_bytes=len(cipher), ciphertext_sha256=hashlib.sha256(cipher).hexdigest()))
        sink = job.create_object(kind)
        sink.write(cipher)
        with sink.finish_reopen(meta): pass
        rows[kind] = dict(name=kind + ".easelbs2", bytes=len(cipher), sha256=hashlib.sha256(cipher).hexdigest())
    commit = wire.encode_commit(dict(format="easel-backup-commit-v2", backup_id=BACKUP_ID,
        target=TARGET, window_id=BACKUP_ID, epoch=1, plan_sha256=plan_sha, registry_sha256=registry, objects=rows))
    return SimpleNamespace(commit=commit, private=synthetic_keys()[1])


@pytest.mark.parametrize("bad_plan,wrong_final", [(True, False), (False, True)])
def test_context_requires_complete_final_then_actual_strict_b1_plan_before_archive(tmp_path, monkeypatch, bad_plan, wrong_final):
    with roots(tmp_path / "roots") as r:
        with durable.create_private_job(r.backup_fd, BACKUP_ID) as job:
            result = _adversarial_authenticated_group(job, bad_plan=bad_plan, wrong_final=wrong_final)
            with intent_for(r, result.commit) as intent:
                anchor.publish_anchor(r.anchor_fd, intent, result.commit)
                job.bind_anchor(intent)
                durable.publish_commit_no_clobber(job, result.commit)
        created, original = [], reader.V2Decoder
        def observed(private, source, expected):
            created.append(expected.kind)
            return original(private, source, expected)
        monkeypatch.setattr(reader, "V2Decoder", observed)
        fd = job_fd(r)
        try:
            with intent_for(r, result.commit) as intent:
                with pytest.raises(B2Error): reader.read_committed(intent, fd, result.private)
            assert created == ["central-context"]
        finally: os.close(fd)


@pytest.mark.parametrize("fault", ["registry", "physical_size"])
def test_authenticated_self_reports_do_not_override_b1_registry_or_archive_physical_size(tmp_path, monkeypatch, fault):
    with roots(tmp_path / "roots") as r:
        with durable.create_private_job(r.backup_fd, BACKUP_ID) as job:
            result = _adversarial_authenticated_group(job, bad_registry=fault == "registry",
                                                      wrong_archive_length=fault == "physical_size")
            with intent_for(r, result.commit) as intent:
                anchor.publish_anchor(r.anchor_fd, intent, result.commit)
                job.bind_anchor(intent)
                durable.publish_commit_no_clobber(job, result.commit)
        fd = job_fd(r)
        try:
            with intent_for(r, result.commit) as intent:
                with pytest.raises(B2Error): reader.read_committed(intent, fd, result.private)
        finally: os.close(fd)


def test_wrong_synthetic_private_key_never_returns_a_plan(tmp_path):
    from media_backup_b2_fixture import KNOWN_V2_PRIVATE_PEM
    with roots(tmp_path / "roots") as r:
        result = published(r)
        fd = job_fd(r)
        try:
            with intent_for(r, result.commit) as intent:
                with pytest.raises(B2Error): reader.read_committed(intent, fd, KNOWN_V2_PRIVATE_PEM)
        finally: os.close(fd)


def test_lost_publish_receipt_only_observes_the_original_exact_group(tmp_path):
    with roots(tmp_path / "roots") as r:
        with capture(r.base / "capture") as source:
            with durable.create_private_job(r.backup_fd, BACKUP_ID) as job:
                result = authenticated_objects(job, source)
                with intent_for(r, result.commit) as intent:
                    reference = anchor.publish_anchor(r.anchor_fd, intent, result.commit)
                    job.bind_anchor(intent)
                    def lost_receipt():
                        durable.publish_commit_no_clobber(job, result.commit)
                        raise OSError("synthetic response lost after exact publication")
                    with pytest.raises(OSError): lost_receipt()
                    assert intent.observe() == reference
                    before = {p.name: p.read_bytes() for p in (r.backup / BACKUP_ID).iterdir()}
                    fd = job_fd(r)
                    try:
                        plan, value = reader.read_committed(intent, fd, result.private)
                        with value: consume(value)
                        assert plan == result.plan and value.verified
                    finally: os.close(fd)
                    assert {p.name: p.read_bytes() for p in (r.backup / BACKUP_ID).iterdir()} == before
                    with pytest.raises(B2Error): durable.publish_commit_no_clobber(job, result.commit)


def test_actual_large_durable_group_and_authenticated_reader_remain_streaming(tmp_path, record_property):
    from media_backup.contract import registry_digest
    length = 33 * 1048576
    with roots(tmp_path / "roots") as r:
        with capture(r.base / "capture") as source:
            key, block, digest = "1" * 64, b"m" * 1048576, hashlib.sha256()
            path = source.media / key
            with path.open("wb", buffering=0) as output:
                for _ in range(33):
                    output.write(block)
                    digest.update(block)
            path.chmod(0o600)
            row = dict(key=key, state="attached", present=True, size=length, sha256=digest.hexdigest(),
                       uid=r.owner[0], gid=r.owner[1], mode=0o600, tenant_id="synthetic-tenant",
                       product_id="easel", operation_id="job:synthetic", missing_evidence_sha256=None)
            refs = [{name: row[name] for name in ("key", "tenant_id", "product_id", "operation_id")}]
            source.plan.update(blobs=[row], references=refs, registry_sha256=registry_digest([row], refs))
            tracemalloc.start()
            try:
                with durable.create_private_job(r.backup_fd, BACKUP_ID) as job:
                    result = authenticated_objects(job, source)
                    with intent_for(r, result.commit) as intent:
                        anchor.publish_anchor(r.anchor_fd, intent, result.commit)
                        job.bind_anchor(intent)
                        durable.publish_commit_no_clobber(job, result.commit)
                fd = job_fd(r)
                try:
                    with intent_for(r, result.commit) as intent:
                        plan, value = reader.read_committed(intent, fd, result.private)
                    with value:
                        actual = consume(value)
                        assert actual == result.receipts["archive"]["plaintext_sha256"] and value.verified
                finally: os.close(fd)
                _, peak = tracemalloc.get_traced_memory()
                assert plan["blobs"][0]["size"] == length
                assert peak < 20 * 1048576
                record_property("blob_bytes", length)
                record_property("ciphertext_bytes", result.receipts["archive"]["ciphertext_bytes"])
                record_property("python_peak_bytes", peak)
            finally:
                tracemalloc.stop()
