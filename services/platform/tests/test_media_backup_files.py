"""FD and exact-inventory safety of B1, only synthetic tmp_path content."""
import importlib
import os
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
from media_backup_fixture import capture

def module():
    return importlib.import_module('media_backup.files')

def records(plan):
    return sorted(plan['files'] + [dict(name='media/' + row['key'], **{k: row[k] for k in ('size', 'sha256', 'uid', 'gid', 'mode')})
                                 for row in plan['blobs'] if row['present']], key=lambda row: row['name'])

def test_borrowed_fds_stream_exact_bytes_and_remain_open(tmp_path):
    lib = module()
    with capture(tmp_path / 'source', [(b'abcde', 'attached', True), (b'unknown', 'staged', False), (b'gone', 'purged', False)]) as item:
        with lib.Sources(item.plan, item.media_fd, item.fds, item.owner, 2) as source:
            for row in records(item.plan):
                chunks = list(source.chunks(row))
                assert all(0 < len(chunk) <= 2 for chunk in chunks)
                assert sum(map(len, chunks)) == row['size']
            source.verify()
        for fd in (*item.fds.values(), item.media_fd):
            assert os.fstat(fd)

@pytest.mark.parametrize('change', ['missing', 'extra', 'part', 'staged_exists', 'deleted_exists', 'symlink', 'hardlink', 'fifo', 'mode', 'dir_mode', 'size', 'hash', 'fd_extra'])
def test_unsafe_or_unaccounted_sources_fail_closed(tmp_path, change):
    lib = module()
    with capture(tmp_path / 'source', [(b'bytes', 'attached', True), (b'missing', 'staged', False), (b'gone', 'deleted', False)]) as item:
        key = item.plan['blobs'][0]['key']; path = item.media / key
        if change == 'missing': path.unlink()
        elif change == 'extra': (item.media / ('f' * 64)).write_bytes(b'other')
        elif change == 'part': (item.media / (key + '.part')).write_bytes(b'partial')
        elif change == 'staged_exists': (item.media / item.plan['blobs'][1]['key']).write_bytes(b'unexpected')
        elif change == 'deleted_exists': (item.media / item.plan['blobs'][2]['key']).write_bytes(b'gone')
        elif change == 'symlink': path.unlink(); path.symlink_to(item.fixed / '0')
        elif change == 'hardlink': os.link(path, item.fixed / 'link')
        elif change == 'fifo': path.unlink(); os.mkfifo(path, 0o600)
        elif change == 'mode': path.chmod(0o644)
        elif change == 'dir_mode': item.media.chmod(0o755)
        elif change == 'size': path.write_bytes(b'oversized')
        elif change == 'hash': path.write_bytes(b'wrong')
        else: item.fds['db/extra'] = os.open(item.fixed / '0', os.O_RDONLY)
        with pytest.raises(lib.FileError, match='invalid_source'):
            with lib.Sources(item.plan, item.media_fd, item.fds, item.owner, 3) as source:
                for row in records(item.plan): list(source.chunks(row))
                source.verify()

@pytest.mark.parametrize('change', ['new_entry', 'same_size_mutation', 'replace_blob', 'fixed_change', 'partial_consumption', 'duplicate_read', 'wrong_owner'])
def test_final_verification_refuses_drift_or_incomplete_consumption(tmp_path, change):
    lib = module()
    with capture(tmp_path / 'source', [(b'bytes', 'attached', True)]) as item:
        owner = (item.owner[0] + 1, item.owner[1]) if change == 'wrong_owner' else item.owner
        with pytest.raises(lib.FileError, match='invalid_source'):
            with lib.Sources(item.plan, item.media_fd, item.fds, owner, 3) as source:
                rows = records(item.plan)
                for row in rows:
                    if change == 'partial_consumption' and row['name'].startswith('media/'):
                        stream = source.chunks(row); next(stream); stream.close()
                    else: list(source.chunks(row))
                key = item.plan['blobs'][0]['key']; path = item.media / key
                if change == 'new_entry': (item.media / 'unexpected').write_bytes(b'x')
                elif change == 'same_size_mutation': path.write_bytes(b'other')
                elif change == 'replace_blob': path.unlink(); path.write_bytes(b'bytes'); path.chmod(0o600)
                elif change == 'fixed_change': (item.fixed / '0').write_bytes(b'changed')
                elif change == 'duplicate_read': list(source.chunks(rows[0]))
                source.verify()

def test_context_exit_closes_retained_blob_generator_and_prevents_further_reads(tmp_path, monkeypatch):
    lib = module()
    with capture(tmp_path / 'source', [(b'longer bytes', 'attached', True)]) as item:
        original_open = os.open; opened = []
        def tracked_open(name, flags, *args, **kwargs):
            fd = original_open(name, flags, *args, **kwargs)
            if name == item.plan['blobs'][0]['key']: opened.append(fd)
            return fd
        monkeypatch.setattr(os, 'open', tracked_open)
        with lib.Sources(item.plan, item.media_fd, item.fds, item.owner, 2) as source:
            row = next(row for row in records(item.plan) if row['name'].startswith('media/'))
            iterator = source.chunks(row)
            assert next(iterator) == b'lo'
        assert len(opened) == 1
        with pytest.raises(OSError): os.fstat(opened[0])
        with pytest.raises(lib.FileError, match='invalid_source'): next(iterator)
        for fd in (*item.fds.values(), item.media_fd): assert os.fstat(fd)

