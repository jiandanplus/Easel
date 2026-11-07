"""Offline Task C contracts; simulated systemd is not Linux acceptance."""
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).parents[1] / 'scripts'
MODULE = SCRIPTS / 'av_sources/remote_operator.py'
STAGE = '/root/easel-av-source-run-20261029-0123456789abcdef'
UNIT = 'easel-av-src-0123456789abcdef.service'
INVOCATION = '1234567890abcdef1234567890abcdef'


def subject():
    assert MODULE.is_file(), 'fixed Linux operator is missing'
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    return importlib.import_module('av_sources.remote_operator')


def payload(m, tmp_path):
    stage = tmp_path / 'stage'; stage.mkdir(mode=0o700)
    (stage / 'code').mkdir(mode=0o700)
    (stage / 'code/av_sources').mkdir(mode=0o700)
    entries = {}
    for name in m.PAYLOAD_FILES:
        path = stage / name; path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        data = ('# synthetic ' + name + '\n').encode(); path.write_bytes(data); path.chmod(0o600)
        entries[name] = {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    raw = (json.dumps({'schema_version': 1, 'files': entries}, sort_keys=True) + '\n').encode()
    manifest = stage / 'payload-manifest.json'; manifest.write_bytes(raw); manifest.chmod(0o600)
    return stage, hashlib.sha256(raw).hexdigest()


def unit_fact(**changes):
    value = {'Id': UNIT, 'LoadState': 'loaded', 'Transient': 'yes', 'RootDirectory': STAGE + '/jail',
             'InvocationID': INVOCATION, 'ActiveState': 'active', 'SubState': 'running',
             'MainPID': '123', 'ControlPID': '0'}
    value.update(changes); return value


def test_identity_accepts_only_exact_matching_calendar_stage():
    m = subject()
    assert m.validate_identity(STAGE, UNIT) == (Path(STAGE), UNIT)


@pytest.mark.parametrize('stage,unit', [
    (STAGE + '/x', UNIT), (STAGE.replace('20261029', '20260230'), UNIT),
    (STAGE.replace('/root/', '/tmp/'), UNIT), (STAGE + '\n', UNIT),
    (STAGE, UNIT.replace('0123456789abcdef', '1123456789abcdef')),
    (STAGE, UNIT + '\n'), (None, UNIT), (STAGE, None),
])
def test_bad_identity_is_rejected(stage, unit):
    m = subject()
    with pytest.raises(m.OperatorError): m.validate_identity(stage, unit)


def test_payload_verifies_exact_manifest_and_code(tmp_path):
    m = subject(); stage, sha = payload(m, tmp_path)
    value = m.verify_payload(stage, sha)
    assert set(value['files']) == set(m.PAYLOAD_FILES)
    assert not (stage / 'work').exists()


@pytest.mark.parametrize('kind', ['digest', 'changed', 'symlink', 'extra', 'mode', 'manifest_extra', 'manifest_missing'])
def test_bad_payload_is_rejected_without_output(tmp_path, kind):
    m = subject(); stage, sha = payload(m, tmp_path); path = stage / m.PAYLOAD_FILES[0]
    if kind == 'digest': sha = '0' * 64
    elif kind == 'changed': path.write_bytes(b'changed')
    elif kind == 'symlink': path.unlink(); path.symlink_to(stage / m.PAYLOAD_FILES[1])
    elif kind == 'extra': (path.parent / 'unexpected.py').write_text('bad')
    elif kind == 'mode': path.chmod(0o644)
    else:
        p = stage / 'payload-manifest.json'; value = json.loads(p.read_bytes())
        if kind == 'manifest_extra': value['files']['code/extra.py'] = value['files'][m.PAYLOAD_FILES[0]]
        else: value['files'].pop(m.PAYLOAD_FILES[0])
        raw = json.dumps(value).encode(); p.write_bytes(raw); sha = hashlib.sha256(raw).hexdigest()
    with pytest.raises(m.OperatorError): m.verify_payload(stage, sha)
    assert not (stage / 'jail').exists()


def test_layout_materializes_only_explicit_checked_links_and_placeholders(tmp_path):
    m = subject(); stage, _ = payload(m, tmp_path)
    m.prepare_layout(stage, {'lib': 'usr/lib', 'lib64': 'usr/lib64', 'bin': 'usr/bin'})
    assert os.readlink(stage / 'jail/lib') == 'usr/lib'
    assert (stage / 'work').stat().st_mode & 0o777 == 0o700
    assert (stage / 'jail/inputs/archive.xz').is_file()
    assert not (stage / 'jail/etc').exists()
    with pytest.raises(m.OperatorError): m.prepare_layout(stage, {'lib': 'usr/lib'})


@pytest.mark.parametrize('links', [{'lib': '../../etc'}, {'lib': 'usr/lib', 'bad': 'usr/lib'}, {}, {'lib': '/usr/lib'}])
def test_unknown_layout_fails_before_directory_creation(tmp_path, links):
    m = subject(); stage, _ = payload(m, tmp_path)
    with pytest.raises(m.OperatorError): m.prepare_layout(stage, links)
    assert not (stage / 'jail').exists()


def test_owned_unit_requires_identity_and_root():
    m = subject()
    assert m.validate_owned_unit(unit_fact(), Path(STAGE), UNIT) == INVOCATION


@pytest.mark.parametrize('change', [
    {'Id': 'other.service'}, {'Transient': 'no'}, {'RootDirectory': '/other'},
    {'InvocationID': ''}, {'InvocationID': '0' * 32}, {'LoadState': 'not-found'},
])
def test_foreign_or_ambiguous_unit_cannot_be_adopted(change):
    m = subject()
    with pytest.raises(m.OperatorError): m.validate_owned_unit(unit_fact(**change), Path(STAGE), UNIT)


def test_invocation_change_is_rejected():
    m = subject()
    with pytest.raises(m.OperatorError):
        m.validate_owned_unit(unit_fact(), Path(STAGE), UNIT, invocation='a' * 32)


def test_command_capture_is_bounded_and_no_shell(tmp_path, monkeypatch):
    m = subject(); observed = {}; original = m.subprocess.Popen
    def child(argv, **kwargs):
        observed.update(argv=argv, kwargs=kwargs)
        return original(argv, **kwargs)
    monkeypatch.setattr(m.subprocess, 'Popen', child)
    value = m.capture_command([sys.executable, '-I', '-B', '-c', 'print("bounded")'], tmp_path / 'capture', timeout=2)
    assert value == {'exit_code': 0, 'stdout': b'bounded\n', 'stderr': b''}
    assert observed['kwargs']['stdin'] == subprocess.DEVNULL
    assert observed['kwargs']['env'] == {'PATH': '/usr/bin:/bin', 'LANG': 'C'}
    assert observed['kwargs']['preexec_fn'] is m.output_limits
    assert observed['kwargs']['stdout'] == observed['kwargs']['stderr'] == subprocess.PIPE
    assert 'shell' not in observed['kwargs']


def test_capture_timeout_is_uncertain_and_reaps_only_owned_observer(tmp_path, monkeypatch):
    m = subject(); children = []; original = m.subprocess.Popen
    def child(argv, **kwargs):
        process = original(argv, **kwargs); children.append(process); return process
    monkeypatch.setattr(m.subprocess, 'Popen', child)
    with pytest.raises(m.RunUncertain):
        m.capture_command([sys.executable, '-I', '-B', '-c', 'import time; time.sleep(30)'],
                          tmp_path / 'capture', timeout=0.1)
    assert len(children) == 1 and children[0].poll() is not None
    assert children[0].stdout.closed and children[0].stderr.closed


def test_capture_preserves_existing_logs(tmp_path, monkeypatch):
    m = subject(); base = tmp_path / 'capture'; base.with_suffix('.stdout').write_bytes(b'original')
    monkeypatch.setattr(m.subprocess, 'Popen', lambda *a, **k: pytest.fail('must not launch'))
    with pytest.raises(m.OperatorError): m.capture_command(['/usr/bin/true'], base, timeout=1)
    assert base.with_suffix('.stdout').read_bytes() == b'original'


def test_host_guard_fails_on_this_non_linux_machine_before_processes(monkeypatch):
    m = subject(); monkeypatch.setattr(m.sys, 'platform', 'darwin')
    monkeypatch.setattr(m.subprocess, 'Popen', lambda *a, **k: pytest.fail('must not launch'))
    with pytest.raises(m.OperatorError): m.host_preflight()


def test_run_gates_host_before_stage_reads(monkeypatch):
    m = subject(); seen = []
    monkeypatch.setattr(m, 'host_preflight', lambda: (_ for _ in ()).throw(m.OperatorError('host')))
    monkeypatch.setattr(m, 'verify_payload', lambda *a: seen.append('payload'))
    with pytest.raises(m.OperatorError): m.run(STAGE, UNIT, 'a' * 64)
    assert seen == []


def test_single_run_records_intent_before_launch_and_checks_same_unit(tmp_path, monkeypatch):
    m = subject(); stage, sha = payload(m, tmp_path); calls = []
    monkeypatch.setattr(m, 'validate_identity', lambda s, u: (stage, UNIT))
    monkeypatch.setattr(m, 'host_preflight', lambda: {'links': {'lib': 'usr/lib'}, 'host_netns_ino': 222})
    monkeypatch.setattr(m, 'verify_public_inputs', lambda: calls.append('pins') or {'ok': True})
    state = iter([{'LoadState': 'not-found'}, {'LoadState': 'not-found'}])
    monkeypatch.setattr(m, 'show_unit', lambda unit: next(state))
    monkeypatch.setattr(m, 'build_command', lambda *a, **k: ['/usr/bin/systemd-run', UNIT])
    def capture(argv, base, *, timeout):
        assert json.loads((stage / 'run-intent.json').read_bytes())['unit'] == UNIT
        result = {'archive_materialized': True, 'source_manifest_sha256': 'a' * 64}
        directory = stage / 'work/result'; directory.mkdir(mode=0o700)
        receipt = directory / 'archive.safe.json'; receipt.write_text(json.dumps(result)); receipt.chmod(0o600)
        calls.append('launch'); return {'exit_code': 0, 'stdout': json.dumps(result).encode(), 'stderr': b''}
    monkeypatch.setattr(m, 'capture_command', capture)
    monkeypatch.setattr(m, 'read_worker_result', lambda *a: {'archive_materialized': True, 'source_manifest_sha256': 'a' * 64})
    value = m.run(STAGE, UNIT, sha)
    assert value['status'] == 'archive_materialized'
    assert calls == ['pins', 'launch', 'pins']
    assert value['production_changed'] is False and value['build_executed'] is False
    assert (stage / 'operator.safe.json').is_file()
    with pytest.raises(m.OperatorError): m.run(STAGE, UNIT, sha)
    assert calls.count('launch') == 1


def test_existing_unit_blocks_layout_and_launch(tmp_path, monkeypatch):
    m = subject(); stage, sha = payload(m, tmp_path)
    monkeypatch.setattr(m, 'validate_identity', lambda s, u: (stage, UNIT))
    monkeypatch.setattr(m, 'host_preflight', lambda: {'links': {'lib': 'usr/lib'}, 'host_netns_ino': 222})
    monkeypatch.setattr(m, 'verify_public_inputs', lambda: {})
    monkeypatch.setattr(m, 'show_unit', lambda u: unit_fact())
    monkeypatch.setattr(m, 'capture_command', lambda *a, **k: pytest.fail('must not launch'))
    with pytest.raises(m.OperatorError): m.run(STAGE, UNIT, sha)
    assert not (stage / 'jail').exists()


def test_uncertain_launch_preserves_intent_and_has_no_safe_success(tmp_path, monkeypatch):
    m = subject(); stage, sha = payload(m, tmp_path)
    monkeypatch.setattr(m, 'validate_identity', lambda s, u: (stage, UNIT))
    monkeypatch.setattr(m, 'host_preflight', lambda: {'links': {'lib': 'usr/lib'}, 'host_netns_ino': 222})
    monkeypatch.setattr(m, 'verify_public_inputs', lambda: {})
    monkeypatch.setattr(m, 'show_unit', lambda u: {'LoadState': 'not-found'})
    monkeypatch.setattr(m, 'build_command', lambda *a, **k: ['/usr/bin/systemd-run'])
    monkeypatch.setattr(m, 'capture_command', lambda *a, **k: (_ for _ in ()).throw(m.RunUncertain('timeout')))
    with pytest.raises(m.RunUncertain): m.run(STAGE, UNIT, sha)
    assert (stage / 'run-intent.json').is_file()
    assert not (stage / 'operator.safe.json').exists()


def test_status_is_read_only_and_does_not_require_execution_headroom(tmp_path, monkeypatch):
    m = subject(); stage, sha = payload(m, tmp_path); observed = []
    monkeypatch.setattr(m, 'validate_identity', lambda s, u: (stage, UNIT))
    monkeypatch.setattr(m, 'host_preflight', lambda **kw: observed.append(kw) or {})
    monkeypatch.setattr(m, 'show_unit', lambda u: {'LoadState': 'not-found'})
    m._exclusive_json(stage / 'run-intent.json', {'unit': UNIT, 'stage': str(stage), 'manifest_sha256': sha})
    before = {str(p.relative_to(stage)): p.read_bytes() for p in stage.rglob('*') if p.is_file()}
    value = m.status(STAGE, UNIT, sha)
    after = {str(p.relative_to(stage)): p.read_bytes() for p in stage.rglob('*') if p.is_file()}
    assert value['operator_success_receipt_present'] is False
    assert observed == [{'require_resources': False}]
    assert before == after


def result_fixture(m, tmp_path, monkeypatch):
    stage = tmp_path / 'stage'; (stage / 'work/result').mkdir(mode=0o700, parents=True)
    manifest = b'{"entries": []}\n'
    value = {'status': 'verified_release_archive_materialized', 'archive_materialized': True,
             'source_members_executed': False, 'build_inputs_adopted': False, 'build_executed': False,
             'production_changed': False, 'archive_size': m.RELEASE.archive_size,
             'archive_sha256': m.RELEASE.archive_sha256, 'signature_sha256': m.RELEASE.signature_sha256,
             'public_key_sha256': m.RELEASE.key_sha256, 'file_count': m.RELEASE.file_count,
             'directory_count': m.RELEASE.directory_count, 'content_bytes': m.RELEASE.content_bytes,
             'source_manifest_sha256': hashlib.sha256(manifest).hexdigest(), 'runtime': {'synthetic': True},
             'signature': {'signature_verified': True, 'fingerprint': m.RELEASE.fingerprint,
                           'primary_fingerprint': m.RELEASE.fingerprint}}
    m._exclusive_json(stage / 'work/result/archive.safe.json', value)
    p = stage / 'work/result/source-manifest.json'; p.write_bytes(manifest); p.chmod(0o600)
    calls = []
    monkeypatch.setattr(m, 'validate_runtime_evidence', lambda evidence, **expected: calls.append((evidence, expected)))
    return stage, value, calls


def test_worker_result_hash_and_runtime_are_independently_rechecked(tmp_path, monkeypatch):
    m = subject(); stage, value, calls = result_fixture(m, tmp_path, monkeypatch)
    assert m.read_worker_result(stage, {'synthetic_expected': True}) == value
    assert calls == [({'synthetic': True}, {'synthetic_expected': True})]


@pytest.mark.parametrize('field,bad', [('archive_materialized', 1), ('production_changed', True),
                                    ('build_executed', True), ('archive_sha256', '0' * 64),
                                    ('file_count', True), ('source_manifest_sha256', '0' * 64)])
def test_worker_bad_result_never_counts_as_materialized(tmp_path, monkeypatch, field, bad):
    m = subject(); stage, value, _ = result_fixture(m, tmp_path, monkeypatch)
    value[field] = bad; p = stage / 'work/result/archive.safe.json'; p.write_text(json.dumps(value))
    with pytest.raises(m.OperatorError): m.read_worker_result(stage, {})


def test_kernel_rejection_propagates_before_result_success(tmp_path, monkeypatch):
    m = subject(); stage, _, _ = result_fixture(m, tmp_path, monkeypatch)
    def reject(*args, **kwargs): raise ValueError('synthetic kernel mismatch')
    monkeypatch.setattr(m, 'validate_runtime_evidence', reject)
    with pytest.raises(ValueError, match='kernel mismatch'): m.read_worker_result(stage, {})


def test_duplicate_manifest_json_keys_are_rejected(tmp_path):
    m = subject(); stage, _ = payload(m, tmp_path)
    p = stage / 'payload-manifest.json'; raw = b'{"schema_version":1,"schema_version":1,"files":{}}'
    p.write_bytes(raw)
    with pytest.raises(m.OperatorError): m.verify_payload(stage, hashlib.sha256(raw).hexdigest())


def test_read_only_query_captures_real_native_child_with_small_output():
    m = subject()
    code, out, error = m._query([sys.executable, '-I', '-B', '-c', 'import sys; print("QUERY_OK"); sys.stderr.write("bounded\\n")'])
    assert (code, out, error) == (0, b'QUERY_OK\n', b'bounded\n')


def test_capture_pipe_limit_does_not_change_parent_resource_limits(tmp_path):
    m = subject(); before = m.resource.getrlimit(m.resource.RLIMIT_FSIZE)
    with pytest.raises(m.RunUncertain):
        m.capture_command([sys.executable, '-I', '-B', '-c',
                           'import os; os.write(1, b"x" * 65536); os.write(1, b"y")'],
                          tmp_path / 'kernel-limit', timeout=3)
    assert (tmp_path / 'kernel-limit.stdout').stat().st_size == 65536
    assert m.resource.getrlimit(m.resource.RLIMIT_FSIZE) == before


@pytest.mark.parametrize('streams', ['stdout', 'stderr', 'both'])
def test_capture_bounds_writer_without_inherited_fsize_limit(tmp_path, monkeypatch, streams):
    m = subject(); original = m.subprocess.Popen; children = []
    def independent_writer(argv, **kwargs):
        # Simulate the PID1-created service writer: observer preexec limits do
        # not apply to this writer receiving the output descriptors.
        kwargs.pop('preexec_fn', None)
        child = original(argv, **kwargs); children.append(child); return child
    monkeypatch.setattr(m.subprocess, 'Popen', independent_writer)
    script = 'import os; ' + {
        'stdout': 'os.write(1, b"x" * 2000000)',
        'stderr': 'os.write(2, b"y" * 2000000)',
        'both': 'os.write(1, b"x" * 40000); os.write(2, b"y" * 40000)',
    }[streams]
    base = tmp_path / 'independent-writer'
    with pytest.raises(m.RunUncertain):
        m.capture_command([sys.executable, '-I', '-B', '-c', script], base, timeout=3)
    paths = [base.with_suffix('.stdout'), base.with_suffix('.stderr')]
    assert sum(path.stat().st_size for path in paths) <= 65536
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in paths)
    assert len(children) == 1 and children[0].poll() is not None
    assert children[0].stdout.closed and children[0].stderr.closed


@pytest.mark.parametrize('script,timeout', [
    ('import os; os.write(1, b"x" * 40000); os.write(2, b"y" * 40000)', 3),
    ('import time; time.sleep(30)', 0.1),
    ('import os,time; os.close(1); os.close(2); time.sleep(30)', 0.1),
])
def test_readonly_query_uncertainty_reaps_only_its_observer(monkeypatch, script, timeout):
    m = subject(); original = m.subprocess.Popen; children = []
    def child(argv, **kwargs):
        process = original(argv, **kwargs); children.append(process); return process
    monkeypatch.setattr(m.subprocess, 'Popen', child)
    with pytest.raises(m.RunUncertain):
        m._query([sys.executable, '-I', '-B', '-c', script], timeout=timeout, limit=65536)
    assert len(children) == 1 and children[0].poll() is not None
    assert children[0].stdout.closed and children[0].stderr.closed


@pytest.mark.parametrize('out,code', [(b'LoadState=not-found\n', 1), (b'LoadState=not-found\n', 0)])
def test_show_unit_accepts_explicit_not_found_only(monkeypatch, out, code):
    m = subject(); commands = []
    def query(argv): commands.append(argv); return code, out, b''
    monkeypatch.setattr(m, '_query', query)
    assert m.show_unit(UNIT) == {'LoadState': 'not-found'}
    assert commands[0][-1] == UNIT and commands[0][1] == 'show'


@pytest.mark.parametrize('out', [b'', b'LoadState=loaded\n', b'LoadState=not-found\nLoadState=loaded\n',
                               b'Secret=anything\n', b'LoadState=not-found\n\xff'])
def test_show_unit_rejects_incomplete_or_ambiguous_output(monkeypatch, out):
    m = subject(); monkeypatch.setattr(m, '_query', lambda argv: (0, out, b''))
    with pytest.raises(m.OperatorError): m.show_unit(UNIT)


def test_public_input_check_only_uses_fixed_paths_and_pins(monkeypatch):
    m = subject(); calls = []
    def pin(path, size, sha): calls.append((str(path), size, sha)); return sha
    monkeypatch.setattr(m, '_pinned_file', pin)
    value = m.verify_public_inputs()
    assert set(value) == {'ffmpeg_9.0.2.orig.tar.xz', 'ffmpeg_9.0.2.orig.tar.xz.asc', 'official-rsa-public-key.asc'}
    assert all(p.startswith('/root/easel-av-rsa-orig-source-20261028/') for p, _, _ in calls)
    assert calls[0][1:] == (12040788, m.RELEASE.archive_sha256)


def test_cli_requires_exact_manifest_pin_and_known_mode(monkeypatch):
    m = subject(); monkeypatch.setattr(m, 'run', lambda *a: pytest.fail('must not run'))
    for argv in [['run', '--stage', STAGE, '--unit', UNIT], ['deploy'],
                 ['run', '--stage', STAGE, '--unit', UNIT, '--manifest-sha256', 'x', '--archive', '/other']]:
        with pytest.raises(SystemExit): m.main(argv)
