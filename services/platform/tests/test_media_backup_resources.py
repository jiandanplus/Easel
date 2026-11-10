"""Actual >32MiB disk archive and restore, with bounded IO/memory observations."""
import hashlib
import os
import shutil
import sys
import tracemalloc
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
from media_backup_fixture import capture, canonical


@pytest.mark.parametrize('sizes', [(33 * 1024**2,), (17 * 1024**2, 17 * 1024**2)], ids=['single-over-32mib', 'aggregate-over-32mib'])
def test_real_disk_stream_roundtrip_above_old_limit_with_bounded_memory(tmp_path, monkeypatch, record_property, sizes):
    from media_backup.closure import write_archive, restore_archive
    from media_backup.contract import Limits
    limits = Limits()
    required = sum(sizes) * 3 + 64 * 1024**2
    assert shutil.disk_usage(tmp_path).free >= required, 'insufficient headroom for actual B1 resource test'
    with capture(tmp_path / 'source', [(b'', 'attached', True) for size in sizes]) as item:
        zeros = b'\0' * limits.chunk_bytes
        for row, size in zip(item.plan['blobs'], sizes):
            path = item.media / row['key']
            with path.open('r+b') as stream: stream.truncate(size)
            digest = hashlib.sha256(); remaining = size
            while remaining:
                amount = min(remaining, len(zeros)); digest.update(memoryview(zeros)[:amount]); remaining -= amount
            row['size'] = size; row['sha256'] = digest.hexdigest()
        item.plan['registry_sha256'] = hashlib.sha256(canonical(dict(blobs=item.plan['blobs'], references=item.plan['references']))).hexdigest()
        max_pread = max_read = max_write = 0
        original_pread = os.pread
        def checked_pread(fd, amount, offset):
            nonlocal max_pread
            assert 0 < amount <= limits.chunk_bytes
            max_pread = max(max_pread, amount)
            return original_pread(fd, amount, offset)
        monkeypatch.setattr(os, 'pread', checked_pread)
        path = tmp_path / 'archive.tar'
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w+b') as stream:
            class Counted:
                def write(self, raw):
                    nonlocal max_write
                    assert 0 < len(raw) <= limits.chunk_bytes
                    max_write = max(max_write, len(raw)); return stream.write(raw)
                def read(self, amount):
                    nonlocal max_read
                    assert 0 < amount <= limits.chunk_bytes
                    max_read = max(max_read, amount); return stream.read(amount)
                def fileno(self): return stream.fileno()
                def tell(self): return stream.tell()
            counted = Counted()
            tracemalloc.start()
            try:
                written = write_archive(item.plan, item.media_fd, item.fds, counted, owner=item.owner, limits=limits)
                stream.flush(); os.fsync(stream.fileno()); stream.seek(0)
                restored = restore_archive(counted, item.parent_fd, 'restored', item.plan, limits=limits)
                current_memory, peak_memory = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
        assert written['archive_sha256'] == restored['archive_sha256']
        assert written['archive_bytes'] == restored['archive_bytes'] == path.stat().st_size > 32 * 1024**2
        assert peak_memory < 14 * 1024**2
        assert max_pread == max_read == max_write == limits.chunk_bytes
        for row in item.plan['blobs']:
            restored_path = item.parent / 'restored' / 'media' / row['key']
            assert restored_path.stat().st_size == row['size']
            digest = hashlib.sha256()
            with restored_path.open('rb') as stream:
                while raw := stream.read(limits.chunk_bytes): digest.update(raw)
            assert digest.hexdigest() == row['sha256']
        for key, value in {'logical_blob_bytes': sum(sizes), 'archive_bytes': path.stat().st_size,
                           'restored_blob_bytes': sum(sizes), 'peak_traced_bytes': peak_memory,
                           'max_pread_bytes': max_pread, 'max_archive_read_bytes': max_read,
                           'max_archive_write_bytes': max_write, 'required_initial_free_bytes': required}.items():
            record_property(key, value)

