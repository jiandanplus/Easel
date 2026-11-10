"""Root-count diagnostics through the real worker CLI, with synthetic evidence."""
import importlib
import json
from pathlib import Path
import sys

import pytest

from services.platform.tests.test_av_source_linux_sandbox import EXPECTED, evidence


def modules():
    scripts = str(Path(__file__).parents[1] / 'scripts')
    if scripts not in sys.path: sys.path.insert(0, scripts)
    return (importlib.import_module('av_sources.archive_worker'),
            importlib.import_module('av_sources.linux_sandbox'))


def args():
    return ['--expected-unit-name', EXPECTED['unit_name'], '--expected-root-dev', str(EXPECTED['root_dev']),
            '--expected-root-ino', str(EXPECTED['root_ino']),
            '--expected-host-netns-ino', str(EXPECTED['host_netns_ino'])]


def test_real_validator_to_cli_counts_allowed_and_forbidden_without_source_processing(monkeypatch, capsys):
    worker, sandbox = modules(); raw = evidence()
    raw['mounts']['/']['writable_descendants'] = [
        '/work', '/tmp', '/var/tmp', '/dev', '/dev/pts', '/proc', '/proc/extra',
        '/sys/SYNTHETIC_PRIVATE_MARKER', '/run/SYNTHETIC_PRIVATE_MARKER']
    monkeypatch.setattr(worker, 'verify_sandbox', lambda expected: sandbox.validate_runtime_evidence(raw, **expected))
    monkeypatch.setattr(worker, 'verify_pinned_inputs', lambda *a: pytest.fail('source processing after failed root policy'))
    assert worker.main(args()) == 1
    captured = capsys.readouterr(); receipt = json.loads(captured.out)
    diagnostic = receipt['sandbox_diagnostic']
    summary = diagnostic['facts']['writable_summary']
    assert diagnostic['stage'] == 'kernel_validate' and diagnostic['gate'] == 'mounts'
    assert diagnostic['facts']['writable_count'] == summary['total'] == 9
    assert summary['allowed'] == 7 and summary['forbidden'] == 2
    assert summary['SYS'] == summary['RUN'] == 1 and summary['DEV'] == summary['PROC'] == 2
    assert receipt['status'] == 'failed' and receipt['build_inputs_adopted'] is False
    assert len(captured.out.encode('ascii')) <= 4096 and captured.out.count('\n') == 1
    assert 'SYNTHETIC_PRIVATE_MARKER' not in captured.out and captured.err == ''


def test_bad_nested_summary_is_omitted_by_actual_cli_exporter(monkeypatch, capsys):
    worker, sandbox = modules()
    error = sandbox.SandboxError('PRIVATE_MARKER', stage='kernel_validate', gate='mounts', reason='mismatch',
        facts={'target': 'ROOT', 'writable_count': 1, 'writable_summary': {'UNKNOWN_PRIVATE_MARKER': 1}})
    def failed(expected): raise error
    monkeypatch.setattr(worker, 'verify_sandbox', failed)
    assert worker.main(args()) == 1
    captured = capsys.readouterr(); receipt = json.loads(captured.out)
    assert receipt['status'] == 'failed' and receipt['failure_kind'] == 'SandboxError'
    assert 'sandbox_diagnostic' not in receipt and 'PRIVATE_MARKER' not in captured.out
    assert captured.err == '' and len(captured.out.encode('ascii')) <= 4096
