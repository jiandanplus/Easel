"""Actual descriptor cleanup when construction never hands ownership to caller."""
import asyncio
from contextlib import contextmanager
import os

import pytest

from media_backup_b2_durable_fixture import roots
from media_backup_b2_fixture import BACKUP_ID, TARGET
from platform_service.media_backup_b2 import anchor, durable


@contextmanager
def owned_fds(monkeypatch):
    opened, copied, created = os.open, os.dup, {}
    def record(fd):
        info = os.fstat(fd)
        created[fd] = (info.st_dev, info.st_ino)
        return fd
    def live():
        result = []
        for fd, identity in created.items():
            try:
                info = os.fstat(fd)
                if (info.st_dev, info.st_ino) == identity: result.append(fd)
            except OSError:
                pass
        return result
    with monkeypatch.context() as patch:
        patch.setattr(os, "open", lambda *a, **kw: record(opened(*a, **kw)))
        patch.setattr(os, "dup", lambda fd: record(copied(fd)))
        try:
            yield live
        finally:
            # Even the expected RED run may close only descriptors it opened.
            for fd in live(): os.close(fd)


@pytest.mark.parametrize("boundary", ["job_sync", "policy_construction", "intent_check"])
def test_cancelled_constructor_closes_owned_fds_and_preserves_cancel_and_inputs(tmp_path, monkeypatch, boundary):
    with roots(tmp_path / "roots") as r:
        with anchor.RootPolicy.from_operator(r.backup_fd, owner=r.owner) as policy:
            def cancel(*a, **kw): raise asyncio.CancelledError()
            with owned_fds(monkeypatch) as live:
                with monkeypatch.context() as patch:
                    if boundary == "job_sync":
                        patch.setattr(durable._Directory, "sync", cancel)
                        operation = lambda: durable.create_private_job(r.backup_fd, BACKUP_ID)
                    elif boundary == "policy_construction":
                        original = anchor.RootPolicy.__init__
                        def cancelled_policy(self, *a, **kw):
                            original(self, *a, **kw)
                            raise asyncio.CancelledError()
                        patch.setattr(anchor.RootPolicy, "__init__", cancelled_policy)
                        operation = lambda: anchor.RootPolicy.from_operator(r.backup_fd, owner=r.owner)
                    else:
                        patch.setattr(anchor.RootAnchorIntent, "_check", cancel)
                        operation = lambda: anchor.trusted_anchor_intent(r.anchor_fd,
                            policy=policy, backup_id=BACKUP_ID, target=TARGET, commit_sha256="a" * 64)
                    with pytest.raises(asyncio.CancelledError): operation()
                assert live() == []
            os.fstat(r.backup_fd)
            os.fstat(r.anchor_fd)
            policy._check()
            assert not (r.anchor / (BACKUP_ID + ".json")).exists()
            assert not (r.backup / BACKUP_ID / "COMMITTED.json").exists()
