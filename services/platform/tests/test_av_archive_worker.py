"""Offline worker contract checks. Kernel and GPG integrations are explicit seams."""
import dataclasses
import hashlib
import importlib
import io
from pathlib import Path
import sys
import tarfile
import time

import pytest


SCRIPTS = Path(__file__).parents[1] / 'scripts'
MODULE = SCRIPTS / 'av_sources/archive_worker.py'
EVIDENCE = Path(__file__).parents[3] / 'outputs/_run/central-av-upstream-rsa-source-20261028/server-receipts'
FP = 'FCF986EA15E6E293A5644F10B4322F04D67658D8'


def subject():
    assert MODULE.is_file(), 'offline archive worker is missing'
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    return importlib.import_module('av_sources.archive_worker')


def valid_status(timestamp=1789699562):
    return (f'[GNUPG:] NEWSIG\n[GNUPG:] GOODSIG {FP[-16:]} release\n'
            f'[GNUPG:] VALIDSIG {FP} 2026-09-18 {timestamp} 0 4 0 1 10 00 {FP}\n').encode()


def fixture_inputs(m, tmp_path, monkeypatch):
    inputs = tmp_path.resolve() / 'inputs'; inputs.mkdir(mode=0o700)
    work = tmp_path.resolve() / 'work'; work.mkdir(mode=0o700)
    body = b'9.0.2\n'
    with tarfile.open(inputs / 'archive.xz', 'w:xz', format=tarfile.GNU_FORMAT) as archive:
        directory = tarfile.TarInfo('ffmpeg-9.0.2/'); directory.type = tarfile.DIRTYPE
        directory.mode = 0o755; archive.addfile(directory)
        version = tarfile.TarInfo('ffmpeg-9.0.2/VERSION'); version.size = len(body)
        version.mode = 0o644; archive.addfile(version, io.BytesIO(body))
    for source, destination in [('ffmpeg_9.0.2.orig.tar.xz.asc', 'archive.asc'),
                                ('official-rsa-public-key.asc', 'release-key.asc')]:
        (inputs / destination).write_bytes((EVIDENCE / source).read_bytes())
    archive = (inputs / 'archive.xz').read_bytes()
    spec = dataclasses.replace(m.RELEASE, archive_size=len(archive),
                               archive_sha256=hashlib.sha256(archive).hexdigest(),
                               file_count=1, directory_count=1, content_bytes=len(body))
    monkeypatch.setattr(m, 'RELEASE', spec)
    return inputs, work, spec


def test_fixed_release_contract_is_not_configurable_from_cli():
    m = subject()
    assert m.RELEASE.archive_size == 12040788
    assert m.RELEASE.archive_sha256 == '8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e'
    assert (m.RELEASE.file_count, m.RELEASE.directory_count, m.RELEASE.content_bytes) == (10399, 126, 90358343)
    assert m.RELEASE.fingerprint == FP


def test_exact_good_signature_status_is_accepted():
    m = subject()
    receipt = m.check_signature_status(valid_status(), 0, now=1793232000)
    assert receipt['fingerprint'] == receipt['primary_fingerprint'] == FP
    assert receipt['signature_verified'] is True


@pytest.mark.parametrize('bad,code', [
    (b'', 0), (valid_status(), 1), (valid_status() + valid_status(), 0),
    (valid_status().replace(FP.encode(), b'A' * 40), 0),
    (valid_status().replace(b' 0 4 0 1 10 00 ', b' 0 4 0 1 2 00 '), 0),
    (valid_status(9999999999), 0), (b'x' * 16385, 0),
    *[(valid_status() + b'[GNUPG:] ' + tag + b' bad\n', 0) for tag in
      [b'BADSIG', b'ERRSIG', b'EXPKEYSIG', b'REVKEYSIG', b'KEYEXPIRED',
       b'KEYREVOKED', b'EXPSIG', b'SIGEXPIRED', b'NO_PUBKEY', b'FAILURE']],
])
def test_signature_failure_never_yields_success(bad, code):
    m = subject()
    with pytest.raises(m.WorkerError):
        m.check_signature_status(bad, code, now=1793232000)


def test_input_pins_use_real_public_metadata_and_reject_mutation(tmp_path, monkeypatch):
    m = subject(); inputs, work, spec = fixture_inputs(m, tmp_path, monkeypatch)
    result = m.verify_pinned_inputs(inputs, spec)
    assert result['archive_sha256'] == spec.archive_sha256
    for name in ['archive.asc', 'release-key.asc', 'archive.xz']:
        path = inputs / name; original = path.read_bytes(); path.write_bytes(original + b'x')
        with pytest.raises(m.WorkerError):
            m.verify_pinned_inputs(inputs, spec)
        path.write_bytes(original)
    assert list(work.iterdir()) == []


def test_symlink_input_is_rejected(tmp_path, monkeypatch):
    m = subject(); inputs, _, spec = fixture_inputs(m, tmp_path, monkeypatch)
    path = inputs / 'archive.asc'; path.rename(inputs / 'original.asc')
    path.symlink_to(inputs / 'original.asc')
    with pytest.raises(m.WorkerError):
        m.verify_pinned_inputs(inputs, spec)


def test_sandbox_failure_occurs_before_any_work_or_source_processing(tmp_path, monkeypatch):
    m = subject(); calls = []
    def fail(expected):
        calls.append('sandbox'); raise m.WorkerError('not isolated')
    monkeypatch.setattr(m, 'verify_sandbox', fail)
    monkeypatch.setattr(m, 'verify_pinned_inputs', lambda *args: pytest.fail('pins must not run'))
    with pytest.raises(m.WorkerError):
        m.run_worker({}, inputs=tmp_path / 'absent', work=tmp_path / 'not-created')
    assert calls == ['sandbox'] and not (tmp_path / 'not-created').exists()


def test_pipeline_checks_real_synthetic_archive_and_retains_unexecuted_modes(tmp_path, monkeypatch):
    m = subject(); inputs, work, spec = fixture_inputs(m, tmp_path, monkeypatch)
    events = []
    monkeypatch.setattr(m, 'verify_sandbox', lambda expected: events.append('sandbox') or {'scope': 'synthetic-test'})
    def signature(source, destination, release):
        assert release == spec; events.append('signature')
        return m.check_signature_status(valid_status(), 0, now=1793232000)
    monkeypatch.setattr(m, 'verify_signature', signature)
    result = m.run_worker({}, inputs=inputs, work=work)
    assert events == ['sandbox', 'signature']
    assert result['archive_materialized'] is True
    assert result['build_inputs_adopted'] is False and result['build_executed'] is False
    assert result['production_changed'] is False
    assert result['file_count'] == 1 and result['directory_count'] == 1
    version = work / 'result/source/ffmpeg-9.0.2/VERSION'
    assert version.read_bytes() == b'9.0.2\n' and version.stat().st_mode & 0o777 == 0o600
    assert (work / 'result/source-manifest.json').is_file()
    assert (work / 'result/archive.safe.json').is_file()
    original = version.read_bytes()
    with pytest.raises(m.WorkerError):
        m.run_worker({}, inputs=inputs, work=work)
    assert version.read_bytes() == original


def test_manifest_mismatch_stops_before_materialization(tmp_path, monkeypatch):
    m = subject(); inputs, work, spec = fixture_inputs(m, tmp_path, monkeypatch)
    monkeypatch.setattr(m, 'RELEASE', dataclasses.replace(spec, file_count=2))
    monkeypatch.setattr(m, 'verify_sandbox', lambda expected: {'scope': 'synthetic-test'})
    monkeypatch.setattr(m, 'verify_signature', lambda *args: {'signature_verified': True})
    with pytest.raises(m.WorkerError):
        m.run_worker({}, inputs=inputs, work=work)
    assert not (work / 'result/source').exists()


def test_signature_rejection_cannot_create_source_tree(tmp_path, monkeypatch):
    m = subject(); inputs, work, _ = fixture_inputs(m, tmp_path, monkeypatch)
    monkeypatch.setattr(m, 'verify_sandbox', lambda expected: {'scope': 'synthetic-test'})
    def fail(*args): raise m.WorkerError('signature refused')
    monkeypatch.setattr(m, 'verify_signature', fail)
    with pytest.raises(m.WorkerError):
        m.run_worker({}, inputs=inputs, work=work)
    assert not (work / 'result/source').exists()


@pytest.mark.parametrize('name', ['archive.asc', 'release-key.asc'])
def test_changed_public_signature_input_cannot_be_reported_as_original_pin(tmp_path, monkeypatch, name):
    m = subject(); inputs, work, _ = fixture_inputs(m, tmp_path, monkeypatch)
    monkeypatch.setattr(m, 'verify_sandbox', lambda expected: {'scope': 'synthetic-test'})
    def change(*args):
        path = inputs / name; path.write_bytes(path.read_bytes() + b'x')
        return m.check_signature_status(valid_status(), 0, now=1793232000)
    monkeypatch.setattr(m, 'verify_signature', change)
    with pytest.raises(m.WorkerError):
        m.run_worker({}, inputs=inputs, work=work)
    assert not (work / 'result/source').exists()


def test_changed_input_at_end_prevents_success_receipt(tmp_path, monkeypatch):
    m = subject(); inputs, work, _ = fixture_inputs(m, tmp_path, monkeypatch)
    monkeypatch.setattr(m, 'verify_sandbox', lambda expected: {'scope': 'synthetic-test'})
    monkeypatch.setattr(m, 'verify_signature', lambda *args: {'signature_verified': True})
    verify_tree = m._verify_tree
    def change(*args):
        verify_tree(*args)
        path = inputs / 'archive.asc'; path.write_bytes(path.read_bytes() + b'x')
    monkeypatch.setattr(m, '_verify_tree', change)
    with pytest.raises(m.WorkerError):
        m.run_worker({}, inputs=inputs, work=work)
    assert not (work / 'result/archive.safe.json').exists()


def test_gpgv_uses_exclusive_logs_private_keyring_and_minimal_environment(tmp_path, monkeypatch):
    m = subject(); inputs, work, spec = fixture_inputs(m, tmp_path, monkeypatch)
    result = work / 'result'; result.mkdir(mode=0o700)
    calls = []
    class Process:
        def __init__(self, command, **kwargs):
            calls.append((command, kwargs))
            kwargs['stdout'].write(valid_status()); kwargs['stderr'].write(b'public diagnostic')
        def wait(self, timeout):
            assert timeout == 15
            return 0
    monkeypatch.setattr(m.subprocess, 'Popen', Process)
    receipt = m.verify_signature(inputs, result, spec)
    command, kwargs = calls[0]
    assert len(calls) == 1 and command[0] == '/usr/bin/gpgv'
    assert command[-2:] == [str(inputs / 'archive.asc'), str(inputs / 'archive.xz')]
    assert kwargs['env'] == {'PATH': '/usr/bin:/bin', 'LANG': 'C'}
    assert kwargs['preexec_fn'] is m._gpg_output_limits
    assert kwargs['cwd'] == result / 'gpg'
    assert receipt['signature_verified'] is True
    assert (result / 'release-key.gpg').stat().st_mode & 0o777 == 0o600
    assert (result / 'gpgv.stdout').stat().st_mode & 0o777 == 0o600


def test_gpgv_timeout_stops_same_child_without_retry(tmp_path, monkeypatch):
    m = subject(); inputs, work, spec = fixture_inputs(m, tmp_path, monkeypatch)
    result = work / 'result'; result.mkdir(mode=0o700)
    events = []
    class Process:
        def __init__(self, command, **kwargs): events.append('spawn')
        def wait(self, timeout):
            events.append(('wait', timeout))
            if timeout == 15:
                raise m.subprocess.TimeoutExpired('gpgv', timeout)
            return -9
        def kill(self): events.append('kill-owned-child')
    monkeypatch.setattr(m.subprocess, 'Popen', Process)
    with pytest.raises(m.WorkerError, match='timed out'):
        m.verify_signature(inputs, result, spec)
    assert events == ['spawn', ('wait', 15), 'kill-owned-child', ('wait', 2)]
    assert (result / 'gpgv.stdout').is_file() and (result / 'gpgv.stderr').is_file()


def test_cli_cannot_change_source_pins(monkeypatch):
    m = subject()
    monkeypatch.setattr(m, 'run_worker', lambda *args: pytest.fail('worker must not run'))
    args = ['--expected-unit-name', 'easel-av-src-' + 'a' * 16 + '.service',
            '--expected-root-dev', '1', '--expected-root-ino', '2',
            '--expected-host-netns-ino', '3', '--archive-sha256', '0' * 64]
    with pytest.raises(SystemExit) as caught:
        m.main(args)
    assert caught.value.code == 2
