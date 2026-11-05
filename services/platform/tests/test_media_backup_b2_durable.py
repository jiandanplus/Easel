import os
import stat

import pytest

from media_backup_b2_fixture import BACKUP_ID
from media_backup_b2_durable_fixture import roots, cipher_receipt
from platform_service.media_backup_b2.contracts import B2Error
from platform_service.media_backup_b2 import durable


def test_new_job_and_cipher_are_private_reopened_and_borrowed_roots_survive(tmp_path):
    with roots(tmp_path / "roots") as r:
        with durable.create_private_job(r.backup_fd, BACKUP_ID) as job:
            assert stat.S_IMODE((r.backup / BACKUP_ID).stat().st_mode) == 0o700
            sink = job.create_object("archive")
            assert sink.write(b"cipher-candidate") == 16
            source = sink.finish_reopen(cipher_receipt(b"cipher-candidate"))
            with source:
                assert source.read(100) == b"cipher-candidate"
                assert source.read(1) == b""
            path = r.backup / BACKUP_ID / "archive.easelbs2"
            assert stat.S_IMODE(path.stat().st_mode) == 0o600 and path.stat().st_nlink == 1
            assert set(p.name for p in path.parent.iterdir()) == {"archive.easelbs2"}
        os.fstat(r.backup_fd)


@pytest.mark.parametrize("bad", ["../escape", "UPPER-ID", "12345678-1234-1234-9234-123456789abc", True])
def test_job_name_never_comes_from_untrusted_paths(tmp_path, bad):
    with roots(tmp_path / "roots") as r:
        with pytest.raises(B2Error): durable.create_private_job(r.backup_fd, bad)


def test_job_collision_never_overwrites_or_adopts_prior_private_stage(tmp_path):
    with roots(tmp_path / "roots") as r:
        original = r.backup / BACKUP_ID
        original.mkdir(mode=0o700)
        marker = original / "kept"
        marker.write_bytes(b"previous private stage")
        with pytest.raises(B2Error): durable.create_private_job(r.backup_fd, BACKUP_ID)
        assert marker.read_bytes() == b"previous private stage"


def test_nonprivate_root_is_rejected_before_new_entries(tmp_path):
    with roots(tmp_path / "roots") as r:
        r.backup.chmod(0o755)
        with pytest.raises(B2Error): durable.create_private_job(r.backup_fd, BACKUP_ID)
        assert list(r.backup.iterdir()) == []


def test_replaced_job_or_ancestor_cannot_write_through_old_fd(tmp_path):
    with roots(tmp_path / "roots") as r:
        with durable.create_private_job(r.backup_fd, BACKUP_ID) as job:
            sink = job.create_object("archive")
            path = r.backup / BACKUP_ID
            path.rename(r.backup / "moved")
            path.mkdir(mode=0o700)
            with pytest.raises(B2Error): sink.write(b"must not be written")
            sink.close()
        assert list(path.iterdir()) == []


def test_hardlinked_cipher_cannot_be_finalized_or_auto_removed(tmp_path):
    with roots(tmp_path / "roots") as r:
        with durable.create_private_job(r.backup_fd, BACKUP_ID) as job:
            sink = job.create_object("archive")
            sink.write(b"candidate")
            partial = next((r.backup / BACKUP_ID).iterdir())
            os.link(partial, r.base / "other-link")
            with pytest.raises(B2Error): sink.finish_reopen(cipher_receipt(b"candidate"))
            sink.close()
            assert partial.exists() and (r.base / "other-link").exists()


def test_swapped_writable_fd_is_detected_before_touching_the_replacement(tmp_path):
    with roots(tmp_path / "roots") as r:
        with durable.create_private_job(r.backup_fd, BACKUP_ID) as job:
            sink = job.create_object("archive")
            untouched = r.base / "untouched"
            untouched.write_bytes(b"keep")
            other = os.open(untouched, os.O_RDWR)
            replaced_fd = sink._fd
            try: os.dup2(other, replaced_fd)
            finally: os.close(other)
            with pytest.raises(B2Error): sink.write(b"bad")
            sink.close()
            os.fstat(replaced_fd)  # Failed ownership must not close a foreign FD.
            os.close(replaced_fd)
            assert untouched.read_bytes() == b"keep"


@pytest.mark.parametrize("fault", ["no_progress", "short", "write_error", "fsync_error"])
def test_short_io_and_fsync_failure_cannot_publish_success(tmp_path, monkeypatch, fault):
    with roots(tmp_path / "roots") as r:
        with durable.create_private_job(r.backup_fd, BACKUP_ID) as job:
            sink = job.create_object("archive")
            original = os.write
            if fault == "no_progress": monkeypatch.setattr(durable.os, "write", lambda fd, raw: 0)
            if fault == "short": monkeypatch.setattr(durable.os, "write", lambda fd, raw: original(fd, raw[:3]))
            if fault == "write_error":
                def fail(*args): raise OSError("PRIVATE WRITE DIAGNOSTIC")
                monkeypatch.setattr(durable.os, "write", fail)
            if fault in ("no_progress", "write_error"):
                with pytest.raises(B2Error, match="^invalid_backup$"): sink.write(b"candidate")
            else:
                assert sink.write(b"candidate") == 9
                if fault == "fsync_error":
                    def fail(*args): raise OSError("PRIVATE FSYNC DIAGNOSTIC")
                    monkeypatch.setattr(durable.os, "fsync", fail)
                    with pytest.raises(B2Error): sink.finish_reopen(cipher_receipt(b"candidate"))
                else:
                    with sink.finish_reopen(cipher_receipt(b"candidate")) as source:
                        assert source.read(100) == b"candidate"
            sink.close()
            assert not (r.backup / BACKUP_ID / "COMMITTED.json").exists()


def test_all_new_ancestor_directory_entries_are_fsynced(tmp_path, monkeypatch):
    with roots(tmp_path / "roots") as r:
        events = []
        original = os.fsync
        def observed(fd):
            info = os.fstat(fd)
            if stat.S_ISDIR(info.st_mode): events.append((info.st_dev, info.st_ino))
            return original(fd)
        monkeypatch.setattr(durable.os, "fsync", observed)
        with durable.create_private_job(r.backup_fd, BACKUP_ID): pass
        for path in (r.base, r.backup, r.backup / BACKUP_ID):
            info = path.stat()
            assert (info.st_dev, info.st_ino) in events


def test_ancestor_rename_and_same_mode_replacement_invalidates_the_held_job(tmp_path):
    with roots(tmp_path / "roots") as r:
        with durable.create_private_job(r.backup_fd, BACKUP_ID) as job:
            sink = job.create_object("archive")
            r.base.rename(tmp_path / "old-roots")
            r.base.mkdir(mode=0o700)
            with pytest.raises(B2Error): sink.write(b"must stay private and unwritten")
            sink.close()
            assert list(r.base.iterdir()) == []


def test_close_reopen_cannot_adopt_a_different_inode(tmp_path, monkeypatch):
    with roots(tmp_path / "roots") as r:
        with durable.create_private_job(r.backup_fd, BACKUP_ID) as job:
            sink = job.create_object("archive")
            sink.write(b"candidate")
            foreign = r.base / "foreign"
            foreign.write_bytes(b"candidate")
            foreign.chmod(0o600)
            original = os.open
            def swapped(name, flags, *args, **kwargs):
                if isinstance(name, str) and name.endswith(".partial") and flags == durable._READ:
                    return original(foreign, flags)
                return original(name, flags, *args, **kwargs)
            monkeypatch.setattr(durable.os, "open", swapped)
            with pytest.raises(B2Error): sink.finish_reopen(cipher_receipt(b"candidate"))
            assert not (r.backup / BACKUP_ID / "archive.easelbs2").exists()
            assert foreign.read_bytes() == b"candidate"
