"""Pure synthetic mount graphs; no operating-system reads or real kernel proof."""
import importlib
import importlib.util

import pytest

MODULE = 'services.platform.scripts.av_sources.mount_table'
TARGETS = ('/', '/code', '/inputs', '/work', '/sys/fs/cgroup')


def subject():
    assert importlib.util.find_spec(MODULE) is not None, 'pure visible-mount resolver missing'
    return importlib.import_module(MODULE)


def row(identity, parent, point, opts='ro', super_opts='rw', fs='ext4'):
    return f'{identity} {parent} 8:1 / {point} {opts} - {fs} source {super_opts}\n'


def baseline():
    return row(70, 900, '/') + row(9, 70, '/code') + row(41, 70, '/work', 'rw') + row(3, 70, '/sys/fs/cgroup', fs='cgroup2')


def observed(**changes):
    value = {'/': 70, '/code': 9, '/inputs': 70, '/work': 41, '/sys/fs/cgroup': 3}
    value.update(changes); return value


def test_baseline_preserves_original_facts_and_inherited_inputs():
    value = subject().resolve_mountinfo(baseline(), observed())
    assert set(value) == set(TARGETS)
    assert value['/inputs'] == {'mount_point': '/', 'read_only': True, 'fs_type': 'ext4', 'writable_descendants': []}
    assert value['/']['writable_descendants'] == ['/work']
    assert value['/work']['read_only'] is False
    assert value['/sys/fs/cgroup']['fs_type'] == 'cgroup2'


def test_root_only_graph_is_valid_but_does_not_invent_required_bind_mounts():
    result = subject().resolve_mountinfo(row(19, 800, '/'), dict.fromkeys(TARGETS, 19))
    assert all(item['mount_point'] == '/' for item in result.values())


@pytest.mark.parametrize('under,over,expected', [('ro', 'ro', True), ('ro', 'rw', False), ('rw', 'ro', True)])
def test_same_point_stack_uses_parent_chain_not_numeric_or_record_order(under, over, expected):
    raw = baseline() + row(2, 9, '/code', over) + row(1, 70, '/other', 'rw')
    raw = raw.replace(row(9, 70, '/code'), row(9, 70, '/code', under))
    for text in (raw, ''.join(reversed(raw.splitlines(keepends=True)))):
        result = subject().resolve_mountinfo(text, observed(**{'/code': 2}))
        assert result['/code']['read_only'] is expected
        assert '/other' in result['/']['writable_descendants']


def test_ancestor_overmount_hides_old_child_branch_but_keeps_new_branch():
    raw = (row(50, 900, '/') + row(49, 50, '/code') + row(48, 49, '/code/leak', 'rw')
           + row(4, 50, '/') + row(3, 4, '/code') + row(2, 3, '/code/new', 'rw'))
    ids = dict.fromkeys(TARGETS, 4); ids['/code'] = 3
    result = subject().resolve_mountinfo(raw, ids)
    assert result['/code']['writable_descendants'] == ['/code/new']
    assert result['/']['writable_descendants'] == ['/code/new']


def test_same_parent_shallower_mount_hides_old_deeper_mount_and_its_children():
    raw = baseline() + row(52, 70, '/other/deep', 'rw') + row(53, 52, '/other/deep/child', 'rw') + row(6, 70, '/other')
    result = subject().resolve_mountinfo(raw, observed())
    assert result['/']['writable_descendants'] == ['/work']


def test_two_other_paths_are_not_merged_and_visible_code_child_stays_writable():
    raw = baseline() + row(88, 70, '/other-a') + row(89, 70, '/other-b', 'rw') + row(90, 9, '/code/visible', 'rw')
    result = subject().resolve_mountinfo(raw, observed())
    assert result['/']['writable_descendants'] == ['/code/visible', '/other-b', '/work']
    assert result['/code']['writable_descendants'] == ['/code/visible']


@pytest.mark.parametrize('opts,super_opts,readonly', [('ro', 'rw', True), ('rw', 'ro', True), ('rw', 'rw', False)])
def test_mount_and_superblock_access_are_independent(opts, super_opts, readonly):
    result = subject().resolve_mountinfo(row(1, 900, '/', opts, super_opts), dict.fromkeys(TARGETS, 1))
    assert result['/']['read_only'] is readonly


@pytest.mark.parametrize('raw', [
    '', 'SECRET invalid', row(0, 900, '/'), row(True, 900, '/'), row(1, -1, '/'), row(2**64, 900, '/'),
    row(1, 900, '/') + row(1, 900, '/code'), row(1, 900, '/') + row(2, 2, '/code'),
    row(1, 900, '/') + row(2, 3, '/code') + row(3, 2, '/code'),
    row(1, 900, '/') + row(2, 901, '/code'), row(1, 900, '/code'),
    row(1, 900, '/') + row(2, 1, '/') + row(3, 1, '/'),
    row(1, 900, '/') + row(2, 1, '/code') + row(3, 1, '/code'),
    row(1, 900, '/') + row(2, 1, '/code') + row(3, 2, '/elsewhere'),
    row(1, 900, '/a/../SECRET'), row(1, 900, '/a//b'), row(1, 900, '/a\\SECRET'),
    row(1, 900, '/', 'ro,rw'), row(1, 900, '/', 'rw,ro'),
    row(1, 900, '/', 'ro', 'ro,rw'), row(1, 900, '/', 'relatime'),
    row(1, 900, '/', fs='/private/SECRET'), row(1, 900, '/').replace(' - ext4 source rw', ' - ext4 source'),
])
def test_malformed_or_unprovable_graphs_fail_without_echoing_paths(raw):
    module = subject()
    with pytest.raises(module.MountTableError) as caught: module.resolve_mountinfo(raw, dict.fromkeys(TARGETS, 1))
    assert caught.value.code in module.ERROR_CODES
    assert str(caught.value) == caught.value.code
    assert 'SECRET' not in str(caught.value) and '/' not in str(caught.value)


@pytest.mark.parametrize('ids', [None, {}, {'/': 70}, dict.fromkeys(TARGETS, True),
    dict.fromkeys(TARGETS, 0), dict.fromkeys(TARGETS, 2**64), {**observed(), '/unknown': 70},
    {**observed(), '/code': 70}, {**observed(), '/inputs': 9}, {**observed(), '/work': 999}])
def test_missing_foreign_or_wrong_visible_observed_ids_fail_closed(ids):
    module = subject()
    with pytest.raises(module.MountTableError): module.resolve_mountinfo(baseline(), ids)


def test_limits_are_bounded_and_deep_graph_does_not_recurse():
    module = subject()
    with pytest.raises(module.MountTableError): module.resolve_mountinfo('x' * (1024 * 1024 + 1), observed())
    with pytest.raises(module.MountTableError): module.resolve_mountinfo(row(1, 900, '/') * 4097, observed())
    raw = row(5000, 99999, '/') + ''.join(row(5000-i, 5001-i, '/') for i in range(1, 1500))
    result = module.resolve_mountinfo(raw, dict.fromkeys(TARGETS, 3501))
    assert all(item['read_only'] for item in result.values())


def test_invalid_input_types_and_error_codes_cannot_leak_objects():
    module = subject()
    class Secret:
        def __str__(self): pytest.fail('no object string conversion')
        def __repr__(self): pytest.fail('no object repr conversion')
    with pytest.raises(module.MountTableError): module.resolve_mountinfo(Secret(), observed())
    assert module.MountTableError(Secret()).code == 'invalid_input'


def test_4096_record_boundary_and_utf8_byte_bound_are_independent():
    module = subject()
    raw = row(9000, 99999, '/') + ''.join(row(9000-i, 9001-i, '/') for i in range(1, 4096))
    result = module.resolve_mountinfo(raw, dict.fromkeys(TARGETS, 4905))
    assert all(item['mount_point'] == '/' for item in result.values())
    with pytest.raises(module.MountTableError) as caught:
        module.resolve_mountinfo('é' * (1024 * 512 + 1), observed())
    assert caught.value.code == 'size_limit'


@pytest.mark.parametrize('child_parent,allowed', [(9, True), (2, False)])
def test_stack_visibility_preserves_original_validator_child_write_guard(child_parent, allowed):
    from services.platform.tests.test_av_source_linux_sandbox import evidence, EXPECTED
    sandbox = importlib.import_module('services.platform.scripts.av_sources.linux_sandbox')
    raw = baseline() + row(2, 9, '/code') + row(56, child_parent, '/code/cache', 'rw')
    facts = subject().resolve_mountinfo(raw, observed(**{'/code': 2}))
    data = evidence(); data['mounts'] = facts
    if allowed:
        assert sandbox.validate_runtime_evidence(data, **EXPECTED)['mounts']['/code']['writable_descendants'] == []
    else:
        with pytest.raises(sandbox.SandboxError): sandbox.validate_runtime_evidence(data, **EXPECTED)


def test_ancestor_covering_cgroup_returns_real_effective_mount_then_old_bind_guard_rejects():
    from services.platform.tests.test_av_source_linux_sandbox import evidence, EXPECTED
    sandbox = importlib.import_module('services.platform.scripts.av_sources.linux_sandbox')
    facts = subject().resolve_mountinfo(baseline() + row(2, 70, '/sys'), observed(**{'/sys/fs/cgroup': 2}))
    assert facts['/sys/fs/cgroup']['mount_point'] == '/sys'
    data = evidence(); data['mounts'] = facts
    with pytest.raises(sandbox.SandboxError): sandbox.validate_runtime_evidence(data, **EXPECTED)


def test_unknown_visible_write_is_not_excused_by_matching_all_five_fd_ids():
    from services.platform.tests.test_av_source_linux_sandbox import evidence, EXPECTED
    sandbox = importlib.import_module('services.platform.scripts.av_sources.linux_sandbox')
    facts = subject().resolve_mountinfo(baseline() + row(2, 70, '/other-visible', 'rw'), observed())
    assert '/other-visible' in facts['/']['writable_descendants']
    data = evidence(); data['mounts'] = facts
    with pytest.raises(sandbox.SandboxError): sandbox.validate_runtime_evidence(data, **EXPECTED)


@pytest.mark.parametrize('shape', ['root_only', 'with_binds', 'overmounted'])
def test_only_unique_self_parent_root_anchor_is_supported(shape):
    raw, ids = row(70, 70, '/'), dict.fromkeys(TARGETS, 70)
    if shape == 'with_binds': raw, ids = baseline().replace(row(70, 900, '/'), row(70, 70, '/')), observed()
    if shape == 'overmounted':
        raw += row(2, 70, '/') + row(1, 2, '/code')
        ids = dict.fromkeys(TARGETS, 2); ids['/code'] = 1
    result = subject().resolve_mountinfo(raw, ids)
    assert set(result) == set(TARGETS)
    assert result['/']['read_only'] is True


@pytest.mark.parametrize('extra', [row(2, 2, '/'), row(2, 2, '/code'), row(2, 3, '/') + row(3, 2, '/')])
def test_self_parent_exception_never_allows_second_root_or_hidden_cycle(extra):
    module = subject()
    with pytest.raises(module.MountTableError): module.resolve_mountinfo(row(1, 1, '/') + extra, dict.fromkeys(TARGETS, 1))
