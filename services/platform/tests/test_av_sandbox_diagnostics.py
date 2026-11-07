"""Offline diagnostics only: no Linux runtime probes or source processing."""
import errno
import importlib
import json
from types import SimpleNamespace

import pytest

from services.platform.tests.test_av_source_linux_sandbox import evidence, EXPECTED


def subject():
    return importlib.import_module('services.platform.scripts.av_sources.linux_sandbox')


def diagnostic(error):
    module = subject()
    assert hasattr(module, 'sandbox_failure_diagnostic'), 'safe sandbox diagnostic interface missing'
    return module.sandbox_failure_diagnostic(error)


def test_exact_safe_schema_never_uses_error_text_or_traceback():
    module = subject()
    error = module.SandboxError('SECRET /home/private arbitrary traceback', stage='kernel_validate',
                                gate='root_identity', reason='mismatch', facts={'field': 'root_ino', 'observed': 12, 'expected': 13})
    value = diagnostic(error)
    assert value == {'version': 1, 'stage': 'kernel_validate', 'gate': 'root_identity', 'reason': 'mismatch',
                     'facts': {'field': 'root_ino', 'observed': 12, 'expected': 13}}
    raw = json.dumps(value).encode()
    assert len(raw) <= 3072 and b'SECRET' not in raw and b'/home' not in raw
    value['facts']['observed'] = 99
    assert diagnostic(error)['facts']['observed'] == 12


@pytest.mark.parametrize('patch', [
    {'stage': 'SECRET'}, {'gate': '/private/path'}, {'reason': 'x' * 4000},
    {'facts': {'field': 'root_ino', 'observed': True}}, {'facts': {'observed': -1}},
    {'facts': {'observed': 2**64}}, {'facts': {'arbitrary': 'SECRET'}},
    {'facts': {'field': '/secret'}}, {'facts': {'observed': object()}},
    {'facts': []}, {'facts': {'observed': '9'}}, {'facts': {'state': 'INTEGER'}},
])
def test_unknown_or_malicious_diagnostics_rejected_entirely(patch):
    module = subject()
    options = dict(stage='kernel_validate', gate='root_identity', reason='mismatch', facts={})
    options.update(patch)
    assert diagnostic(module.SandboxError('never expose', **options)) is None


def test_unstructured_subclass_foreign_exception_and_extra_attributes_rejected():
    module = subject()
    class Child(module.SandboxError): pass
    for error in [ValueError('private'), module.SandboxError('old message'), Child('private')]:
        assert diagnostic(error) is None
    error = module.SandboxError(stage='kernel_collect', gate='status', reason='invalid', facts={'state': 'INVALID'})
    error.unknown = 'SECRET'
    assert diagnostic(error) is None


@pytest.mark.parametrize('field,gate', [('root_ino', 'root_identity'), ('NoNewPrivs', 'status'),
    ('CapEff', 'status'), ('netns_ino', 'network_namespace'), ('inet_socket_errno', 'inet_socket')])
def test_validator_reports_first_original_guard_without_acceptance(field, gate):
    module = subject(); raw = evidence()
    raw[field] = {'root_ino': 99, 'NoNewPrivs': 0, 'CapEff': 1, 'netns_ino': 900, 'inet_socket_errno': None}[field]
    if field == 'inet_socket_errno': raw['proc1_root_errno'] = None
    else: raw['work']['mode'] = 0o777  # A later failure cannot overwrite the first.
    with pytest.raises(module.SandboxError) as caught:
        module.validate_runtime_evidence(raw, **EXPECTED)
    value = diagnostic(caught.value)
    assert value['stage'] == 'kernel_validate' and value['gate'] == gate
    assert len(json.dumps(value).encode()) <= 3072


def test_missing_and_unlimited_facts_are_classified_without_raw_values():
    module = subject(); raw = evidence(); raw['cgroup']['memory.max'] = 'max'
    with pytest.raises(module.SandboxError) as caught: module.validate_runtime_evidence(raw, **EXPECTED)
    assert diagnostic(caught.value)['facts'] == {'field': 'memory.max', 'state': 'UNLIMITED'}
    raw = evidence(); del raw['NoNewPrivs']
    with pytest.raises(module.SandboxError) as caught: module.validate_runtime_evidence(raw, **EXPECTED)
    assert diagnostic(caught.value)['facts']['state'] == 'ABSENT'


@pytest.mark.parametrize('code,state', [(errno.ENOENT, 'ABSENT'), (errno.EACCES, 'DENIED')])
def test_collector_first_read_failure_has_fixed_classification_and_no_probe(monkeypatch, code, state):
    module = subject(); monkeypatch.setattr(module.sys, 'platform', 'linux')
    def fail(path): raise OSError(code, 'SECRET /arbitrary/private/path')
    monkeypatch.setattr(module, '_read_text', fail)
    monkeypatch.setattr(module, 'os', SimpleNamespace(stat=lambda *args: pytest.fail('must stop at first collection failure')))
    monkeypatch.setattr(module, '_inet_probe', lambda: pytest.fail('no live probe'))
    with pytest.raises(module.SandboxError) as caught: module.collect_runtime_evidence()
    assert diagnostic(caught.value) == {'version': 1, 'stage': 'kernel_collect', 'gate': 'status',
                                       'reason': 'read_failed', 'facts': {'state': state}}


@pytest.mark.parametrize('path,target', [('/', 'ROOT'), ('/code', 'CODE'), ('/private/SECRET', 'OTHER')])
def test_duplicate_mount_reports_only_fixed_target_and_count(path, target):
    module = subject()
    raw = f'1 0 8:1 / {path} ro - ext4 /dev/SECRET rw\n2 1 8:1 / {path} ro - ext4 /dev/SECRET rw\n'
    with pytest.raises(module.SandboxError) as caught: module._mount_facts(raw)
    assert diagnostic(caught.value) == {'version': 1, 'stage': 'kernel_collect', 'gate': 'mountinfo',
                                       'reason': 'duplicate', 'facts': {'target': target, 'count': 2}}


def test_diagnostic_failure_does_not_turn_guard_failure_into_success(monkeypatch):
    module = subject(); raw = evidence(); raw['root_ino'] = object()
    with pytest.raises(module.SandboxError) as caught: module.validate_runtime_evidence(raw, **EXPECTED)
    value = diagnostic(caught.value)
    assert value is None or value['gate'] == 'root_identity'
    assert 'object at' not in json.dumps(value)


def test_export_failure_is_omitted_without_emitting_or_raising(monkeypatch):
    module = subject()
    error = module.SandboxError(stage='kernel_validate', gate='expected', reason='invalid')
    def failed(*args, **kwargs): raise RuntimeError('SECRET encoder detail')
    monkeypatch.setattr(module, 'json', SimpleNamespace(dumps=failed))
    assert diagnostic(error) is None


def test_hostile_attribute_mapping_and_fact_objects_are_not_evaluated():
    module = subject()
    class Hostile(dict):
        def __iter__(self): pytest.fail('must not iterate hostile mapping')
        def items(self): pytest.fail('must not evaluate hostile facts')
    class Secret:
        def __str__(self): pytest.fail('must not stringify message')
        def __repr__(self): pytest.fail('must not repr message')
    error = module.SandboxError(Secret(), stage='kernel_collect', gate='status', reason='invalid', facts=Hostile())
    assert diagnostic(error) is None
    error.__dict__ = Hostile(error.__dict__)
    assert diagnostic(error) is None


@pytest.mark.parametrize('failure,gate,state', [
    ('status_invalid', 'status', 'INVALID'), ('cgroup_absent', 'cgroup_path', 'ABSENT'),
    ('limit_empty', 'cgroup_limits', 'EMPTY'), ('limit_unlimited', 'cgroup_limits', 'UNLIMITED'),
    ('mount_duplicate', 'mountinfo', None), ('work_denied', 'work', None),
])
def test_synthetic_collector_marks_first_midstream_failure_without_real_probes(monkeypatch, failure, gate, state):
    module = subject(); monkeypatch.setattr(module.sys, 'platform', 'linux')
    texts = {'/proc/self/status': 'NoNewPrivs: 1\nCapEff: 0\nCapPrm: 0\nCapBnd: 0\n',
             '/proc/self/cgroup': '0::/system.slice/unit\n',
             '/proc/self/mountinfo': '1 999 8:1 / / ro - ext4 /dev/x rw\n'}
    for name, value in {'memory.max': '536870912', 'memory.swap.max': '0', 'pids.max': '16', 'cpu.max': '200000 100000'}.items():
        texts['/sys/fs/cgroup/system.slice/unit/' + name] = value
    if failure == 'status_invalid': texts['/proc/self/status'] = 'CapEff: SECRET\n'
    if failure == 'limit_empty': texts['/sys/fs/cgroup/system.slice/unit/memory.max'] = ''
    if failure == 'limit_unlimited': texts['/sys/fs/cgroup/system.slice/unit/memory.max'] = 'max'
    if failure == 'mount_duplicate': texts['/proc/self/mountinfo'] *= 2
    def read(path):
        if failure == 'cgroup_absent' and path == '/proc/self/cgroup': raise FileNotFoundError(errno.ENOENT, 'SECRET')
        return texts[path]
    monkeypatch.setattr(module, '_read_text', read)
    monkeypatch.setattr(module, '_directory_mount_ids', lambda: {path: 1 for path in ('/', '/code', '/inputs', '/work', '/sys/fs/cgroup')})
    monkeypatch.setattr(module, 'os', SimpleNamespace(stat=lambda path: SimpleNamespace(st_dev=2049, st_ino=1234), getuid=lambda: 0, geteuid=lambda: 0))
    monkeypatch.setattr(module, 'resource', SimpleNamespace(RLIMIT_AS=1, RLIMIT_CPU=2, RLIMIT_CORE=3, getrlimit=lambda kind: (0, 0)))
    def work():
        if failure == 'work_denied': raise PermissionError(errno.EACCES, 'SECRET')
        return evidence()['work']
    monkeypatch.setattr(module, '_work_fact', work)
    monkeypatch.setattr(module, '_inet_probe', lambda: pytest.fail('first failure must precede probes'))
    with pytest.raises(module.SandboxError) as caught: module.collect_runtime_evidence()
    value = diagnostic(caught.value)
    assert value['stage'] == 'kernel_collect' and value['gate'] == gate
    if state is not None: assert value['facts']['state'] == state
    assert 'SECRET' not in json.dumps(value)
    if failure == 'mount_duplicate': assert value['reason'] == 'duplicate'
