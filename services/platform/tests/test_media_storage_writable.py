"""Offline contract tests; never read server credentials or invoke systemd."""
import importlib.util
import json
import errno
import os
from pathlib import Path
import sys
import subprocess
from types import SimpleNamespace

import pytest


def module():
    source = Path(__file__).parents[1] / 'scripts/prepare_media_storage_writable.py'
    assert source.is_file(), 'independent reviewed storage repair helper required'
    spec = importlib.util.spec_from_file_location('media_storage_writable', source)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


OLD = (b'[Service]\nWorkingDirectory=/srv/easel/releases/20261027-images-m1-1-size-v2\n'
       b'ExecStart=\nExecStart=/srv/easel/releases/20261027-images-m1-1-size-v2/.venv/bin/python -m uvicorn app\n'
       b'Environment=CENTRAL_MEDIA_CONFIG_FILE=/etc/easel-central/media.json\n')


def test_only_appends_one_fixed_media_directive_to_original_bytes():
    value = module()
    assert value.append_writable(OLD) == OLD + b'ReadWritePaths=/var/lib/easel-central/media\n'


@pytest.mark.parametrize('raw', [OLD.rstrip(), OLD + b'ReadWritePaths=/other\n',
                              OLD + b'ReadWritePaths=\n', b'[Unit]\nDescription=wrong\n'])
def test_ambiguous_existing_write_directive_or_noncanonical_dropin_is_rejected(raw):
    with pytest.raises(ValueError):
        module().append_writable(raw)


def test_active_precondition_keeps_pending_rows_and_rejects_both_active_kinds():
    value = module()
    counts = {'text_active': 0, 'media_active': 0, 'text_pending': 2, 'media_pending': 1}
    assert value.require_idle(counts) == counts
    for key in ('text_active', 'media_active'):
        with pytest.raises(ValueError, match='active_jobs_require_review'):
            value.require_idle(counts | {key: 1})


def test_exact_script_and_raw_plan_hash_are_required_before_inspection(tmp_path, monkeypatch):
    value = module()
    plan = tmp_path / 'approved.json'
    plan.write_bytes(b'{}\n')
    plan.chmod(0o600)
    monkeypatch.setattr(value, 'inspect_current', lambda: pytest.fail('must reject before host inspection'))
    with pytest.raises(ValueError, match='script_hash_mismatch'):
        value.apply(plan, value.digest(plan.read_bytes()), '0' * 64)
    with pytest.raises(ValueError, match='approved_plan_hash_mismatch'):
        value.apply(plan, '0' * 64, value.script_digest())


def test_private_plan_rejects_symlink_and_nonprivate_mode(tmp_path):
    value = module()
    plan = tmp_path / 'plan.json'
    plan.write_bytes(b'{}\n')
    with pytest.raises(ValueError, match='private_file_required'):
        value.read_private(plan)
    plan.chmod(0o600)
    link = tmp_path / 'link.json'
    link.symlink_to(plan)
    with pytest.raises((ValueError, OSError)):
        value.read_private(link)


def test_plan_is_readonly_and_contains_no_raw_configuration_or_process_command(monkeypatch):
    value = module()
    current = {'version': 1, 'script_sha256': value.script_digest(),
               'files': {'/fixed/unit': {'sha256': 'a' * 64, 'metadata_sha256': 'b' * 64}},
               'service_state_sha256': 'c' * 64,
               'jobs': {'text_active': 0, 'media_active': 0, 'text_pending': 0, 'media_pending': 1}}
    monkeypatch.setattr(value, 'inspect_current', lambda: current)
    monkeypatch.setattr(value, 'write_private', lambda *args: pytest.fail('plan must not write'))
    assert value.make_plan() == current


def test_plan_and_current_file_drift_rejects_before_backup_or_mutation(tmp_path, monkeypatch):
    value = module()
    plan = {'version': 1, 'script_sha256': value.script_digest(), 'files': {'x': 'old'}}
    path = tmp_path / 'plan.json'
    raw = value.canonical(plan)
    path.write_bytes(raw)
    path.chmod(0o600)
    monkeypatch.setattr(value, 'inspect_current', lambda: plan | {'files': {'x': 'new'}})
    monkeypatch.setattr(value, 'backup_original', lambda *args: pytest.fail('no backup before drift gate'))
    with pytest.raises(ValueError, match='approved_state_changed'):
        value.apply(path, value.digest(raw), value.script_digest())


def test_apply_stops_only_api_then_checks_idle_and_stable_window_before_write_and_start(tmp_path, monkeypatch):
    value = module()
    dropin = tmp_path / '90-m1-images.conf'
    dropin.write_bytes(OLD)
    dropin.chmod(0o600)
    monkeypatch.setattr(value, 'API_DROPIN', dropin)
    window = {'text_active': 0, 'media_active': 0, 'text_pending': 0, 'media_pending': 1,
              'nonterminal_sha256': 'a' * 64, 'financial_sha256': 'b' * 64}
    plan = {'version': 1, 'script_sha256': value.script_digest(), 'files': {'x': 'stable'}, 'jobs': window}
    raw = value.canonical(plan)
    approved = tmp_path / 'approved.json'
    approved.write_bytes(raw)
    approved.chmod(0o600)
    calls = []
    monkeypatch.setattr(value, 'inspect_current', lambda: calls.append('inspect') or plan)
    monkeypatch.setattr(value, 'backup_original', lambda *args: calls.append('backup') or tmp_path / 'backup')
    monkeypatch.setattr(value, 'write_receipt', lambda *args: None)
    monkeypatch.setattr(value, 'replace_dropin', lambda *args: calls.append('write'))
    monkeypatch.setattr(value, 'active_counts', lambda: calls.append('idle') or window)
    monkeypatch.setattr(value, 'verify_inactive', lambda: calls.append('inactive'))
    monkeypatch.setattr(value, 'verify_files', lambda *args: calls.append('files'))
    monkeypatch.setattr(value, 'verify_console', lambda *args: calls.append('console'))
    monkeypatch.setattr(value, 'systemctl', lambda *args: calls.append(args) or '')
    monkeypatch.setattr(value, 'verify_repaired', lambda: calls.append('probe') or {'success': True})
    result = value.apply(approved, value.digest(raw), value.script_digest())
    assert result['success'] is True
    assert calls.index('backup') < calls.index('write')
    assert calls[calls.index('write') - 1] == 'idle'
    stop = ('stop', value.API_UNIT)
    start = ('start', value.API_UNIT)
    assert calls.index(stop) < calls.index('inactive') < calls.index('write')
    assert calls[calls.index(start) - 1] == 'idle'
    assert ('daemon-reload',) in calls and start in calls
    assert calls.index('write') < calls.index(('daemon-reload',)) < calls.index(start)
    assert not any(isinstance(item, tuple) and value.CONSOLE_UNIT in item for item in calls)
    assert calls[-1] == 'console'


@pytest.mark.parametrize('race', [
    {'text_active': 0, 'media_active': 1, 'media_pending': 1, 'nonterminal_sha256': 'old', 'financial_sha256': 'same'},
    {'text_active': 0, 'media_active': 0, 'media_pending': 2, 'nonterminal_sha256': 'new', 'financial_sha256': 'same'},
    {'text_active': 0, 'media_active': 0, 'media_pending': 1, 'nonterminal_sha256': 'old', 'financial_sha256': 'changed'},
])
def test_stop_race_preserves_old_bytes_restores_only_old_api_and_records_failure(tmp_path, monkeypatch, race):
    value = module()
    dropin = tmp_path / '90.conf'
    dropin.write_bytes(OLD)
    dropin.chmod(0o600)
    monkeypatch.setattr(value, 'API_DROPIN', dropin)
    window = {'text_active': 0, 'media_active': 0, 'media_pending': 1,
              'nonterminal_sha256': 'old', 'financial_sha256': 'same'}
    plan = {'version': 1, 'script_sha256': value.script_digest(), 'jobs': window}
    raw = value.canonical(plan)
    approved = tmp_path / 'approved.json'
    approved.write_bytes(raw)
    approved.chmod(0o600)
    monkeypatch.setattr(value, 'inspect_current', lambda: plan)
    monkeypatch.setattr(value, 'backup_original', lambda *args: tmp_path / 'backup')
    monkeypatch.setattr(value, 'replace_dropin', lambda *args: pytest.fail('race must preserve original file'))
    counts = iter([window, race])
    monkeypatch.setattr(value, 'active_counts', lambda: next(counts))
    monkeypatch.setattr(value, 'verify_inactive', lambda: None)
    calls = []
    monkeypatch.setattr(value, 'systemctl', lambda *args: calls.append(args) or '')
    monkeypatch.setattr(value, 'restore_original_api', lambda *args: calls.append('restore_original_api') or True)
    receipts = []
    monkeypatch.setattr(value, 'write_receipt', lambda directory, receipt: receipts.append(receipt.copy()))
    with pytest.raises(ValueError, match='active_jobs_require_review|job_window_changed'):
        value.apply(approved, value.digest(raw), value.script_digest())
    assert calls == [('stop', value.API_UNIT), 'restore_original_api']
    assert receipts[-1]['success'] is False and receipts[-1]['phase'] == 'api_stopped'
    assert receipts[-1]['original_api_restored'] is True
    assert dropin.read_bytes() == OLD


def test_idle_window_compares_hashes_even_when_pending_count_is_unchanged():
    value = module()
    window = {'text_active': 0, 'media_active': 0, 'media_pending': 1,
              'nonterminal_sha256': 'a', 'financial_sha256': 'b'}
    assert value.require_same_window(window, window.copy()) == window
    with pytest.raises(ValueError, match='job_window_changed'):
        value.require_same_window(window, window | {'nonterminal_sha256': 'c'})


def test_backup_preserves_exact_original_bytes_and_is_private(tmp_path, monkeypatch):
    value = module()
    monkeypatch.setattr(value, 'PRIVATE_ROOT', tmp_path)
    directory = value.backup_original(OLD, {'script_sha256': value.script_digest()})
    assert directory.stat().st_mode & 0o777 == 0o700
    original = directory / '90-m1-images.conf.original'
    assert original.read_bytes() == OLD and original.stat().st_mode & 0o777 == 0o600


def test_atomic_replacement_rejects_current_byte_drift_and_preserves_source(tmp_path, monkeypatch):
    value = module()
    path = tmp_path / '90.conf'
    path.write_bytes(OLD)
    path.chmod(0o600)
    monkeypatch.setattr(value, 'API_DROPIN', path)
    with pytest.raises(ValueError, match='dropin_changed_before_replace'):
        value.replace_dropin(OLD + b'changed', value.append_writable(OLD))
    assert path.read_bytes() == OLD
    value.replace_dropin(OLD, value.append_writable(OLD))
    assert path.read_bytes() == value.append_writable(OLD)
    assert path.stat().st_mode & 0o777 == 0o600


def test_namespace_command_targets_actual_pid_and_drops_credentials_before_probe():
    value = module()
    command = value.probe_command(1234, 997, 997)
    assert command[:3] == ['/usr/bin/nsenter', '--mount=/proc/1234/ns/mnt', '--']
    assert command[3] == str(value.RELEASE / '.venv/bin/python')
    assert command[-2:] == ['997', '997']
    code = command[command.index('-c') + 1]
    assert code.index('os.setuid') < code.index('os.open')
    assert 'os.O_EXCL' in code and 'os.O_NOFOLLOW' in code and '0o600' in code
    assert 'os.fsync' in code and 'os.unlink' in code and 'os.statvfs' in code
    assert '/var/lib/easel-central/media' in code and "'/etc'" in code and str(value.RELEASE) in code


def test_probe_result_requires_media_write_and_both_readonly_negative_results():
    value = module()
    good = {'media': {'writable': True, 'readonly': False, 'removed': True},
            'etc': {'writable': False, 'readonly': True, 'removed': True},
            'release': {'writable': False, 'readonly': True, 'removed': True}}
    assert value.validate_probe(good) == good
    for key in ('media', 'etc', 'release'):
        bad = json.loads(json.dumps(good))
        bad[key]['writable'] = key != 'media'
        with pytest.raises(ValueError, match='namespace_probe_failed'):
            value.validate_probe(bad)
    good['release']['readonly'] = False
    with pytest.raises(ValueError, match='namespace_probe_failed'):
        value.validate_probe(good)


@pytest.mark.parametrize('state,pid', [('active', '0'), ('inactive', '42'), ('failed', '0')])
def test_stop_requires_named_api_to_be_inactive_with_zero_mainpid(monkeypatch, state, pid):
    value = module()
    calls = []
    def show(*args):
        calls.append(args)
        return state if 'ActiveState' in args else pid
    monkeypatch.setattr(value, 'systemctl', show)
    with pytest.raises(ValueError, match='api_must_be_inactive'):
        value.verify_inactive()
    assert all(item[1] == value.API_UNIT for item in calls)


def test_restore_refuses_to_start_old_api_after_any_approved_file_changed(monkeypatch):
    value = module()
    def changed(plan):
        raise ValueError('approved_files_changed')
    monkeypatch.setattr(value, 'verify_files', changed)
    monkeypatch.setattr(value, 'systemctl', lambda *args: pytest.fail('must not restore changed inputs'))
    with pytest.raises(ValueError, match='approved_files_changed'):
        value.restore_original_api({}, OLD)


def test_dependency_hashes_close_before_loading_unreviewed_helper(tmp_path, monkeypatch):
    value = module()
    monkeypatch.setattr(value, '__file__', str(tmp_path / 'repair.py'))
    (tmp_path / 'prepare_m1_image_release.py').write_bytes(b'raise RuntimeError("must not execute")')
    with pytest.raises(ValueError, match='dependency_hash_mismatch'):
        value.helpers()


def test_loading_pinned_helpers_does_not_create_bytecode_during_readonly_plan(tmp_path):
    value = module()
    for name in ('prepare_media_storage_writable.py', *value.HELPERS):
        (tmp_path / name).write_bytes((Path(value.__file__).parent / name).read_bytes())
    loader = ("from pathlib import Path; "
              "p=Path(__import__('sys').argv[1]); "
              "ns={'__file__':str(p),'__name__':'storage_readonly'}; "
              "exec(compile(p.read_bytes(),str(p),'exec'),ns); ns['helpers']()")
    completed = subprocess.run([sys.executable, '-I', '-c', loader,
                                str(tmp_path / 'prepare_media_storage_writable.py')], capture_output=True)
    assert completed.returncode == 0, completed.stderr.decode()
    assert not list(tmp_path.rglob('*.pyc')), 'read-only planning must not create dependency bytecode'


@pytest.mark.parametrize('fail_fsync', [False, True])
def test_probe_body_exclusively_writes_private_file_and_cleans_up_even_on_fsync_failure(tmp_path, monkeypatch, capsys, fail_fsync):
    value = module()
    media, etc, release = [tmp_path / name for name in ('media', 'etc', 'release')]
    for directory in (media, etc, release):
        directory.mkdir()
    actual_open, actual_fsync = os.open, os.fsync
    opens = []
    def bounded_open(path, flags, mode=0o777):
        opens.append((path, flags, mode))
        if Path(path).parent != media:
            raise OSError(errno.EROFS, 'synthetic readonly mount')
        return actual_open(path, flags, mode)
    def fsync(fd):
        assert os.fstat(fd).st_mode & 0o777 == 0o600
        if fail_fsync:
            raise OSError(errno.EIO, 'synthetic fsync failure')
        return actual_fsync(fd)
    monkeypatch.setattr(os, 'open', bounded_open)
    monkeypatch.setattr(os, 'fsync', fsync)
    monkeypatch.setattr(os, 'statvfs', lambda path: SimpleNamespace(f_flag=0 if Path(path) == media else os.ST_RDONLY))
    for name in ('setgroups', 'setgid', 'setuid'):
        monkeypatch.setattr(os, name, lambda *args: None)
    monkeypatch.setattr(sys, 'argv', ['-c', '997', '997'])
    code = value.PROBE_CODE.replace(str(value.STORAGE), str(media)).replace("'/etc'", repr(str(etc))).replace(str(value.RELEASE), str(release))
    if fail_fsync:
        with pytest.raises(OSError, match='synthetic fsync failure'):
            exec(compile(code, '<isolated-probe>', 'exec'), {})
    else:
        exec(compile(code, '<isolated-probe>', 'exec'), {})
        value.validate_probe(json.loads(capsys.readouterr().out))
    assert list(media.iterdir()) == [] and list(etc.iterdir()) == [] and list(release.iterdir()) == []
    assert all(flags & os.O_EXCL and flags & os.O_NOFOLLOW and mode == 0o600 for _, flags, mode in opens)


def test_readonly_window_hashes_private_ids_without_returning_them(monkeypatch):
    value = module()
    import psycopg
    statements = []
    class Cursor:
        def __init__(self, rows):
            self.rows = rows
        def fetchone(self):
            return self.rows[0]
        def fetchall(self):
            return self.rows
    class Connection:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def execute(self, statement):
            text = statement if isinstance(statement, str) else statement.as_string()
            statements.append(text)
            if 'count(*) FILTER' in text:
                return Cursor([(0, 1)])
            if 'j.id,j.status' in text:
                return Cursor([('private-job-id', 'pending_reconciliation', 1000000, None,
                                'private-reservation-id', 'dispatched', 1000000, None)])
            return Cursor([('{"id":"private-financial-id"}',)])
    monkeypatch.setattr(value, 'helpers', lambda: (None, SimpleNamespace(CENTRAL_ENV=Path('/fixed/env'),
        env_values=lambda path: {'CENTRAL_DATABASE_URL': 'postgresql://offline.invalid/db', 'CENTRAL_DB_SCHEMA': 'fixed_schema'})))
    monkeypatch.setattr(psycopg, 'connect', lambda *args, **kwargs: Connection())
    result = value.active_counts()
    assert result['text_active'] == result['media_active'] == 0
    assert result['text_pending'] == result['media_pending'] == 1
    assert len(result['financial_sha256']) == len(result['nonterminal_sha256']) == 64
    assert 'private-' not in json.dumps(result) and 'postgresql' not in json.dumps(result)
    assert statements[0] == 'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'
    assert all(text.startswith(('SET ', 'SELECT ')) for text in statements)
    assert not any('lease' in text for text in statements)
