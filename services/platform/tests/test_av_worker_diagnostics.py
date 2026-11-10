"""Safe failure output only; no Linux process or public source is accessed."""
import importlib
import json
from pathlib import Path
import sys

import pytest


SCRIPTS = Path(__file__).parents[1] / 'scripts'


def modules():
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    return (importlib.import_module('av_sources.archive_worker'),
            importlib.import_module('av_sources.linux_sandbox'))


ARGS = ['--expected-unit-name', 'easel-av-src-' + 'a' * 16 + '.service',
        '--expected-root-dev', '1', '--expected-root-ino', '2',
        '--expected-host-netns-ino', '3']


def test_failure_receipt_uses_fixed_kind_and_never_exception_text():
    worker, _ = modules()
    unsafe = type('private_token_SENTINEL', (ValueError,), {})
    receipt = worker._failure_receipt(unsafe('/root/private-key SENTINEL'))
    assert receipt == {'schema_version': 1, 'status': 'failed',
                       'failure_kind': 'ValueError', 'build_inputs_adopted': False,
                       'production_changed': False}
    assert 'SENTINEL' not in json.dumps(receipt)


def test_failure_receipt_adds_only_approved_kernel_diagnostic(monkeypatch):
    worker, sandbox = modules()
    diagnostic = {'version': 1, 'stage': 'kernel_collect', 'gate': 'status',
                  'reason': 'read_failed', 'facts': {}}
    monkeypatch.setattr(sandbox, 'sandbox_failure_diagnostic', lambda error: diagnostic, raising=False)
    receipt = worker._failure_receipt(sandbox.SandboxError('private content'))
    assert receipt['failure_kind'] == 'SandboxError'
    assert receipt['sandbox_diagnostic'] == diagnostic
    assert 'private content' not in json.dumps(receipt)


@pytest.mark.parametrize('failure', [ValueError, OSError, RuntimeError])
def test_diagnostic_exception_falls_back_without_leaking(monkeypatch, failure):
    worker, sandbox = modules()
    def fail(error):
        raise failure('SENTINEL /host/private')
    monkeypatch.setattr(sandbox, 'sandbox_failure_diagnostic', fail, raising=False)
    receipt = worker._failure_receipt(sandbox.SandboxError('SENTINEL'))
    assert 'sandbox_diagnostic' not in receipt
    assert 'SENTINEL' not in json.dumps(receipt)


def test_oversize_or_unserializable_diagnostic_is_not_emitted(monkeypatch):
    worker, sandbox = modules()
    for value in ({'content': 'SENTINEL' * 1000}, {'content': object()}):
        monkeypatch.setattr(sandbox, 'sandbox_failure_diagnostic', lambda error: value, raising=False)
        receipt = worker._failure_receipt(sandbox.SandboxError('SENTINEL'))
        assert 'sandbox_diagnostic' not in receipt
        assert len(json.dumps(receipt).encode()) <= 4096
        assert 'SENTINEL' not in json.dumps(receipt)


def test_cli_is_single_bounded_line_and_failure_cannot_be_success(monkeypatch, capsys):
    worker, _ = modules()
    unsafe = type('secret_CLASS_SENTINEL', (ValueError,), {})
    def fail(expected):
        raise unsafe('private error SENTINEL')
    monkeypatch.setattr(worker, 'run_worker', fail)
    assert worker.main(ARGS) == 1
    output = capsys.readouterr()
    assert output.err == '' and output.out.count('\n') == 1
    assert len(output.out.encode()) <= 4096 and 'SENTINEL' not in output.out
    assert json.loads(output.out)['status'] == 'failed'


def test_diagnostic_path_does_not_inspect_source_or_create_output(tmp_path, monkeypatch):
    worker, sandbox = modules()
    def fail(expected):
        raise sandbox.SandboxError('kernel failure')
    monkeypatch.setattr(worker, 'verify_sandbox', fail)
    monkeypatch.setattr(worker, 'verify_pinned_inputs', lambda *args: pytest.fail('source read before isolation'))
    with pytest.raises(sandbox.SandboxError) as caught:
        worker.run_worker({}, inputs=tmp_path / 'absent-input', work=tmp_path / 'absent-work')
    assert worker._failure_receipt(caught.value)['status'] == 'failed'
    assert list(tmp_path.iterdir()) == []


def test_actual_validator_first_failure_survives_worker_cli(monkeypatch, capsys):
    worker, sandbox = modules()
    # Use the actual validator and worker chain, replacing only the Linux
    # collector. Missing later fields must neither run nor appear as passed.
    monkeypatch.setattr(sandbox, 'collect_runtime_evidence', lambda: {'platform': 'linux', 'uid': 7})
    monkeypatch.setattr(worker, 'verify_pinned_inputs', lambda *args: pytest.fail('unexpected source read'))
    assert worker.main(ARGS) == 1
    output = capsys.readouterr()
    receipt = json.loads(output.out)
    assert receipt['sandbox_diagnostic'] == {
        'version': 1, 'stage': 'kernel_validate', 'gate': 'root_identity',
        'reason': 'mismatch', 'facts': {'field': 'uid', 'observed': 7, 'expected': 0}}
    assert set(receipt) == {'schema_version', 'status', 'failure_kind',
                            'build_inputs_adopted', 'production_changed', 'sandbox_diagnostic'}
    assert output.out.count('\n') == 1 and len(output.out.encode()) <= 4096
    assert output.err == ''


@pytest.mark.parametrize('changes', [
    {'stage': 'SENTINEL'}, {'gate': '/private/SENTINEL'}, {'reason': 'SENTINEL'},
    {'facts': {'body': 'SENTINEL'}}, {'facts': {'value': 'SENTINEL' * 10000}},
    {'facts': {'value': True}}, {'facts': {'field': 'SENTINEL'}},
])
def test_real_exporter_rejects_untrusted_structure_before_cli(changes, monkeypatch, capsys):
    worker, sandbox = modules()
    fields = {'stage': 'kernel_collect', 'gate': 'status', 'reason': 'invalid', 'facts': {}}
    fields.update(changes)
    error = sandbox.SandboxError('SENTINEL /host/private', **fields)
    def fail(expected):
        raise error
    monkeypatch.setattr(worker, 'run_worker', fail)
    assert worker.main(ARGS) == 1
    output = capsys.readouterr()
    receipt = json.loads(output.out)
    assert receipt['status'] == 'failed' and receipt['failure_kind'] == 'SandboxError'
    assert 'sandbox_diagnostic' not in receipt
    assert 'SENTINEL' not in output.out and output.err == ''
