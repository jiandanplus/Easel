"""Pure ROOT failure metadata; no kernel, files, service or provider probes."""
import importlib
import json

import pytest

from services.platform.tests.test_av_source_linux_sandbox import evidence, EXPECTED


CLASSES = ('WORK', 'TMP', 'VAR_TMP', 'DEV', 'PROC', 'CODE', 'INPUTS', 'USR', 'SYS', 'RUN', 'HOME', 'ROOT_HOME', 'VAR_OTHER', 'OTHER')
ALLOWED = CLASSES[:5]


def subject():
    return importlib.import_module('services.platform.scripts.av_sources.linux_sandbox')


def summary(**counts):
    result = dict.fromkeys(CLASSES, 0); result.update(counts)
    result.update(total=sum(result.values()), allowed=sum(result[key] for key in ALLOWED))
    result['forbidden'] = result['total'] - result['allowed']
    return result


def failure(children):
    module = subject(); raw = evidence(); raw['mounts']['/']['writable_descendants'] = children
    with pytest.raises(module.SandboxError) as caught: module.validate_runtime_evidence(raw, **EXPECTED)
    return caught.value


def export(value, **facts):
    module = subject()
    return module.sandbox_failure_diagnostic(module.SandboxError(stage='kernel_validate', gate='mounts', reason='mismatch',
        facts={'target': 'ROOT', 'writable_count': value.get('total', 0) if type(value) is dict else 0, 'writable_summary': value, **facts}))


def test_nine_total_writable_mounts_are_not_nine_violations_and_text_never_leaks():
    module = subject()
    children = ['/work', '/tmp', '/var/tmp', '/dev', '/proc', '/work/cache', '/tmp/cache', '/dev/pts', '/sys/SECRET']
    value = module.sandbox_failure_diagnostic(failure(children))
    assert value['facts']['writable_count'] == 9
    assert value['facts']['writable_summary'] == summary(WORK=2, TMP=2, VAR_TMP=1, DEV=2, PROC=1, SYS=1)
    raw = json.dumps(value).encode()
    assert b'SECRET' not in raw and b'/sys' not in raw and len(raw) <= 3072
    line = json.dumps({'schema_version': 1, 'status': 'failed', 'failure_kind': 'SandboxError', 'sandbox_diagnostic': value,
                       'build_inputs_adopted': False, 'production_changed': False}) + '\n'
    assert len(line.encode()) <= 4096


def test_component_boundaries_all_fixed_categories_and_other_paths_count_separately():
    module = subject()
    children = ['/work/x', '/tmp/x', '/var/tmp/x', '/dev/x', '/proc/x', '/code/x', '/inputs/x', '/usr/x', '/sys/x',
                '/run/x', '/home/x', '/root/x', '/var/x', '/work-other', '/tmp-other', '/var/tmp-other',
                '/dev-other', '/proc-other', '/code-other', '/inputs-other', '/usr-other', '/sys-other',
                '/run-other', '/home-other', '/root-other', '/different-one', '/different-two']
    value = module.sandbox_failure_diagnostic(failure(children))['facts']['writable_summary']
    assert value == summary(WORK=1, TMP=1, VAR_TMP=1, DEV=1, PROC=1, CODE=1, INPUTS=1, USR=1, SYS=1,
                            RUN=1, HOME=1, ROOT_HOME=1, VAR_OTHER=2, OTHER=13)
    assert value['total'] == len(children) and value['allowed'] == 5


def test_summary_schema_is_detached_and_accepts_bounded_maximum_counts():
    value = summary(OTHER=65534, WORK=1)
    exported = export(value)
    assert exported is not None
    exported['facts']['writable_summary']['OTHER'] = 0
    assert value['OTHER'] == 65534
    assert export(value)['facts']['writable_summary']['OTHER'] == 65534


@pytest.mark.parametrize('mutate', [
    lambda value: value.update(SECRET=1), lambda value: value.pop('WORK'),
    lambda value: value.update(OTHER=True), lambda value: value.update(OTHER=-1),
    lambda value: value.update(OTHER=65536), lambda value: value.update(OTHER='1'),
    lambda value: value.update(OTHER=object()), lambda value: value.update(OTHER=1.0),
    lambda value: value.update(total=8), lambda value: value.update(allowed=8),
    lambda value: value.update(forbidden=8), lambda value: value.update(OTHER=[1]),
])
def test_malicious_unknown_and_inconsistent_summary_rejected_whole(mutate):
    value = summary(OTHER=1); mutate(value)
    assert export(value) is None


def test_summary_cannot_attach_to_foreign_target_gate_or_count():
    module = subject(); value = summary(OTHER=1)
    assert export(value, target='CODE') is None
    assert export(value, writable_count=2) is None
    for stage, gate in [('kernel_collect', 'mounts'), ('kernel_validate', 'mountinfo')]:
        error = module.SandboxError(stage=stage, gate=gate, reason='mismatch',
            facts={'target': 'ROOT', 'writable_count': 1, 'writable_summary': value})
        assert module.sandbox_failure_diagnostic(error) is None


def test_hostile_nested_mapping_is_never_evaluated():
    class Hostile(dict):
        def items(self): pytest.fail('must reject dict subclass without evaluation')
        def __iter__(self): pytest.fail('must reject dict subclass without iteration')
    assert export(Hostile(summary(OTHER=1))) is None


def test_only_original_root_allowlist_failure_gets_summary_and_other_guards_still_reject():
    module = subject(); raw = evidence()
    raw['mounts']['/']['writable_descendants'] = ['/work', '/tmp', '/var/tmp', '/dev', '/proc']
    assert module.validate_runtime_evidence(raw, **EXPECTED)['mounts']['/']['writable_descendants'] == raw['mounts']['/']['writable_descendants']
    for change in ['root_ro', 'bad_path', 'code_child', 'wrong_id', 'work_mode']:
        raw = evidence()
        if change == 'root_ro': raw['mounts']['/']['read_only'] = False
        if change == 'bad_path': raw['mounts']['/']['writable_descendants'] = ['/a/../SECRET']
        if change == 'code_child': raw['mounts']['/code']['writable_descendants'] = ['/code/leak']
        if change == 'wrong_id': raw['root_ino'] = 22
        if change == 'work_mode': raw['work']['mode'] = 0o777
        with pytest.raises(module.SandboxError) as caught: module.validate_runtime_evidence(raw, **EXPECTED)
        diagnostic = module.sandbox_failure_diagnostic(caught.value)
        assert diagnostic is None or 'writable_summary' not in diagnostic['facts']


def test_classifier_failure_preserves_original_guard_failure(monkeypatch):
    module = subject()
    assert hasattr(module, '_root_writable_summary'), 'pure summary classifier missing'
    def fail(*args): raise RuntimeError('SECRET')
    monkeypatch.setattr(module, '_root_writable_summary', fail)
    error = failure(['/sys/SECRET'])
    value = module.sandbox_failure_diagnostic(error)
    assert value is None or ('writable_summary' not in value['facts'] and value['gate'] == 'mounts')


def test_summary_first_cannot_evaluate_hostile_target_comparison():
    module = subject()
    class Hostile:
        def __eq__(self, other): pytest.fail('must reject target type before comparison')
        def __ne__(self, other): pytest.fail('must reject target type before comparison')
    error = module.SandboxError(stage='kernel_validate', gate='mounts', reason='mismatch',
        facts={'writable_summary': summary(OTHER=1), 'writable_count': 1, 'target': Hostile()})
    assert module.sandbox_failure_diagnostic(error) is None
