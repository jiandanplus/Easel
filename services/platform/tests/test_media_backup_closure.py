"""Synthetic end-to-end archive closure; no real DB or configuration reads."""
import copy
import hashlib
import importlib
import io
import os
import stat
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
from media_backup_fixture import capture

def module():
    return importlib.import_module('media_backup.closure')

def archived(lib, item):
    sink = io.BytesIO()
    receipt = lib.write_archive(item.plan, item.media_fd, item.fds, sink, owner=item.owner)
    assert receipt['status'] == 'closure_files_verified'
    assert receipt['archive_sha256'] == hashlib.sha256(sink.getvalue()).hexdigest()
    return sink.getvalue()

@pytest.mark.parametrize('specs', [[], [(b'one', 'attached', True)],
    [(b'a', 'attached', True), (b'pending', 'staged', False), (b'deleted', 'deleted', False),
     (b'purged', 'purged', False), (b'still staged', 'staged', True)]])
def test_exact_roundtrip_preserves_absence_and_restricts_receipts(tmp_path, specs):
    lib = module()
    with capture(tmp_path / 'source', specs) as item:
        raw = archived(lib, item)
        result = lib.restore_archive(io.BytesIO(raw), item.parent_fd, 'stage', item.plan)
        assert result['status'] == 'private_stage_restored_verified'
        assert result['archive_sha256'] == hashlib.sha256(raw).hexdigest()
        assert result['archive_bytes'] == len(raw)
        stage = item.parent / 'stage'
        expected = {row['name'] for row in item.plan['files']} | {'media/' + row['key'] for row in item.plan['blobs'] if row['present']}
        actual = {path.relative_to(stage).as_posix() for path in stage.rglob('*') if path.is_file()}
        assert actual == expected
        for path in (stage, *stage.rglob('*')):
            info = path.lstat()
            assert (info.st_uid, info.st_gid) == item.owner
            assert stat.S_IMODE(info.st_mode) == (0o700 if path.is_dir() else 0o600)
        for row in item.plan['blobs']:
            path = stage / 'media' / row['key']
            assert path.exists() is row['present']
        assert set(result) == {'status', 'file_count', 'blob_count', 'archive_bytes', 'archive_sha256', 'manifest_sha256', 'registry_sha256'}

@pytest.mark.parametrize('change', ['manifest_body', 'file_body', 'header', 'truncate', 'extra_zero', 'extra_nonzero', 'wrong_plan', 'different_scope'])
def test_archive_corruption_and_wrong_trusted_plan_never_succeed(tmp_path, change):
    lib = module()
    from media_backup.contract import ClosureError
    with capture(tmp_path / 'source', [(b'artifact', 'attached', True)]) as item:
        raw = bytearray(archived(lib, item)); expected = copy.deepcopy(item.plan)
        if change == 'manifest_body': raw[512] ^= 1
        elif change == 'file_body':
            length = int(raw[124:135], 8); offset = 512 + length + (-length) % 512 + 512
            raw[offset] ^= 1
        elif change == 'header': raw[156] = ord('x')
        elif change == 'truncate': del raw[-1]
        elif change == 'extra_zero': raw.extend(b'\0')
        elif change == 'extra_nonzero': raw.extend(b'x')
        elif change == 'wrong_plan': expected['snapshots']['central'] = 'different'
        else: expected['references'][0]['tenant_id'] = 'foreign'
        with pytest.raises(ClosureError, match='invalid_closure'):
            lib.restore_archive(io.BytesIO(raw), item.parent_fd, 'stage', expected)
        if (item.parent / 'stage').exists():
            assert stat.S_IMODE((item.parent / 'stage').lstat().st_mode) == 0o700

@pytest.mark.parametrize('name', ['../outside', '/absolute', '.', '..', '', 'nested/name', 'stage\x00name'])
def test_restore_name_never_escapes_private_parent(tmp_path, name):
    lib = module()
    from media_backup.contract import ClosureError
    with capture(tmp_path / 'source') as item:
        raw = archived(lib, item)
        with pytest.raises(ClosureError, match='invalid_closure'):
            lib.restore_archive(io.BytesIO(raw), item.parent_fd, name, item.plan)
        assert list(item.parent.iterdir()) == []

def test_existing_target_and_untrusted_parent_refused_without_overwrite(tmp_path):
    lib = module()
    from media_backup.contract import ClosureError
    with capture(tmp_path / 'source') as item:
        raw = archived(lib, item)
        target = item.parent / 'stage'; target.write_bytes(b'keep')
        with pytest.raises(ClosureError): lib.restore_archive(io.BytesIO(raw), item.parent_fd, 'stage', item.plan)
        assert target.read_bytes() == b'keep'
        target.unlink(); target.symlink_to(item.fixed)
        with pytest.raises(ClosureError): lib.restore_archive(io.BytesIO(raw), item.parent_fd, 'stage', item.plan)
        target.unlink(); item.parent.chmod(0o755)
        with pytest.raises(ClosureError): lib.restore_archive(io.BytesIO(raw), item.parent_fd, 'stage', item.plan)
        assert not target.exists()

def test_source_drift_and_sink_failure_do_not_produce_success(tmp_path):
    lib = module()
    from media_backup.contract import ClosureError
    with capture(tmp_path / 'source', [(b'bytes', 'attached', True)]) as item:
        class MutationSink(io.BytesIO):
            def write(self, value):
                result = super().write(value)
                if b'bytes' in bytes(value):
                    (item.media / item.plan['blobs'][0]['key']).write_bytes(b'other')
                return result
        with pytest.raises(ClosureError): lib.write_archive(item.plan, item.media_fd, item.fds, MutationSink(), owner=item.owner)
    with capture(tmp_path / 'another') as item:
        class BrokenSink:
            def write(self, value): return 0
        with pytest.raises(ClosureError): lib.write_archive(item.plan, item.media_fd, item.fds, BrokenSink(), owner=item.owner)

