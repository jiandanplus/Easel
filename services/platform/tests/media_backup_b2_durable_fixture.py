"""Owned temporary directories/FDs for durable B2 tests, never deployment roots."""
from contextlib import contextmanager
import hashlib
import os
from types import SimpleNamespace

from media_backup_b2_fixture import BACKUP_ID, TARGET


@contextmanager
def roots(path):
    path.mkdir(mode=0o700)
    backup, anchor = path / "backup", path / "anchor"
    backup.mkdir(mode=0o700)
    anchor.mkdir(mode=0o700)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    bfd, afd = os.open(backup, flags), os.open(anchor, flags)
    try:
        yield SimpleNamespace(base=path, backup=backup, anchor=anchor, backup_fd=bfd, anchor_fd=afd,
                              owner=(os.geteuid(), os.getegid()), backup_id=BACKUP_ID, target=TARGET)
    finally:
        os.close(bfd)
        os.close(afd)


def cipher_receipt(raw):
    return dict(plaintext_length=1, plaintext_sha256=hashlib.sha256(b"x").hexdigest(),
                plan_sha256="b" * 64, registry_sha256="c" * 64, data_records=1,
                ciphertext_bytes=len(raw), ciphertext_sha256=hashlib.sha256(raw).hexdigest())


def candidate_objects(job):
    """Durability-only opaque bytes; not authentication/self-verify evidence."""
    from platform_service.media_backup_b2 import wire
    rows = {}
    for kind in ("archive", "context"):
        raw = ("synthetic cipher candidate " + kind).encode()
        sink = job.create_object(kind)
        sink.write(raw)
        receipt = cipher_receipt(raw)
        with sink.finish_reopen(receipt): pass
        rows[kind] = dict(name=kind + ".easelbs2", bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    value = dict(format="easel-backup-commit-v2", backup_id=job.backup_id, target=TARGET,
                 window_id=BACKUP_ID, epoch=1, plan_sha256="b" * 64, registry_sha256="c" * 64, objects=rows)
    return wire.encode_commit(value)


def authenticated_objects(job, source):
    from media_backup.contract import encode_manifest, physical_size
    from media_backup.closure import write_archive
    from media_backup_b2_fixture import synthetic_keys
    from platform_service.media_backup_b2.contracts import ExpectedV2
    from platform_service.media_backup_b2.stream import V2Encoder
    from platform_service.media_backup_b2 import wire
    public, private, recipient = synthetic_keys()
    context = encode_manifest(source.plan)
    plan_sha = hashlib.sha256(context).hexdigest()
    rows, receipts = {}, {}
    for kind in ("archive", "context"):
        sink = job.create_object(kind)
        length = physical_size(source.plan) if kind == "archive" else len(context)
        expected = ExpectedV2(job.backup_id, "central-" + kind, TARGET, recipient,
                              length, plan_sha, source.plan["registry_sha256"])
        encoder = V2Encoder(public, expected, sink)
        if kind == "archive":
            receipt = write_archive(source.plan, source.media_fd, source.fds, encoder, owner=source.owner)
        else:
            encoder.write(context)
            receipt = dict(status="context_plan_verified", plaintext_length=len(context),
                plaintext_sha256=plan_sha, plan_sha256=plan_sha, registry_sha256=source.plan["registry_sha256"])
        receipts[kind] = encoder.finish(receipt)
        with sink.finish_reopen(receipts[kind]): pass
        rows[kind] = dict(name=kind + ".easelbs2", bytes=receipts[kind]["ciphertext_bytes"],
                          sha256=receipts[kind]["ciphertext_sha256"])
    value = dict(format="easel-backup-commit-v2", backup_id=job.backup_id, target=TARGET,
                 window_id=BACKUP_ID, epoch=1, plan_sha256=plan_sha,
                 registry_sha256=source.plan["registry_sha256"], objects=rows)
    return SimpleNamespace(commit=wire.encode_commit(value), private=private,
                           plan=source.plan, receipts=receipts)
