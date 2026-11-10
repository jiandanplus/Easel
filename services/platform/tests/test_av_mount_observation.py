"""Synthetic FD/mount observations only; never probes this host's namespace."""
import errno
import importlib
import io
import json
import stat
from types import SimpleNamespace

import pytest


def subject():
    return importlib.import_module('services.platform.scripts.av_sources.linux_sandbox')


PATHS = ('/', '/code', '/inputs', '/work', '/sys/fs/cgroup')
IDS = dict(zip(PATHS, (1, 2, 1, 3, 4)))
RAW = ('1 999 8:1 / / ro - ext4 /dev/x rw\n'
       '2 1 8:1 /code /code ro - ext4 /dev/x rw\n'
       '3 1 8:1 /work /work rw - ext4 /dev/x rw\n'
       '4 1 0:1 / /sys/fs/cgroup ro - cgroup2 cgroup rw\n')


def test_fdinfo_reads_only_own_numeric_fd_with_bounded_ascii_reader(monkeypatch):
    module = subject(); calls = []
    class Reader(io.StringIO):
        def read(self, bound):
            calls.append(('bound', bound)); return super().read(bound)
    def opened(path, mode, **kwargs):
        calls.append((path, mode, kwargs))
        return Reader('pos:\t0\nflags:\t010000000\nmnt_id:\t23\nino:\t1234\n')
    monkeypatch.setattr(module, 'open', opened, raising=False)
    assert module._read_fd_mount_id(41) == 23
    assert calls == [('/proc/self/fdinfo/41', 'r', {'encoding': 'ascii', 'errors': 'strict'}), ('bound', 4097)]


@pytest.mark.parametrize('fd', [-1, True, '1', None, 2**31])
def test_invalid_fd_never_becomes_a_path(fd, monkeypatch):
    module = subject()
    monkeypatch.setattr(module, 'open', lambda *a, **k: pytest.fail('invalid FD caused file access'), raising=False)
    with pytest.raises(ValueError): module._read_fd_mount_id(fd)


@pytest.mark.parametrize('raw', [
    '', 'pos: 0\n', 'mnt_id: 1\nmnt_id: 2\n', ' mnt_id: 1\n',
    'mnt_id: 0\n', 'mnt_id: -1\n', 'mnt_id: 0x12\n', 'mnt_id: 2 SECRET\n',
    'mnt_id: 9223372036854775808\n', 'mnt_id: 1\x00\n', 'mnt_id: 1\r\n',
    'mnt_id: 1\n' + 'x' * 4096,
])
def test_bad_or_ambiguous_fdinfo_is_rejected(raw, monkeypatch):
    module = subject()
    monkeypatch.setattr(module, 'open', lambda *a, **k: io.StringIO(raw), raising=False)
    with pytest.raises(ValueError): module._read_fd_mount_id(4)


def fake_os(*, failing_target=None, fail_kind='open'):
    calls = []; handles = {}
    def opened(path, flags):
        calls.append(('open', path, flags))
        if path == failing_target and fail_kind == 'open':
            raise PermissionError(errno.EACCES, 'SECRET host path')
        fd = len(handles) + 10; handles[fd] = path; return fd
    def fstat(fd):
        calls.append(('stat', fd))
        bad = handles[fd] == failing_target and fail_kind == 'type'
        return SimpleNamespace(st_mode=stat.S_IFREG if bad else stat.S_IFDIR)
    os = SimpleNamespace(O_PATH=0x100, O_DIRECTORY=0x200, O_NOFOLLOW=0x400, O_CLOEXEC=0x800,
                         open=opened, fstat=fstat, close=lambda fd: calls.append(('close', fd)))
    return os, calls, handles


def test_observes_only_fixed_directories_and_closes_every_fd(monkeypatch):
    module = subject(); os, calls, handles = fake_os()
    monkeypatch.setattr(module, 'os', os)
    monkeypatch.setattr(module, '_read_fd_mount_id', lambda fd: IDS[handles[fd]])
    assert module._directory_mount_ids() == IDS
    assert [row[1] for row in calls if row[0] == 'open'] == list(PATHS)
    assert all(row[2] == 0xF00 for row in calls if row[0] == 'open')
    assert [row[1] for row in calls if row[0] == 'close'] == list(handles)
    assert not any('/inputs/' in row[1] for row in calls if row[0] == 'open')


@pytest.mark.parametrize('kind', ['open', 'type', 'fdinfo'])
def test_directory_failure_stops_and_closes_owned_fds(kind, monkeypatch):
    module = subject(); os, calls, handles = fake_os(failing_target='/code', fail_kind=kind)
    monkeypatch.setattr(module, 'os', os)
    def observed(fd):
        if kind == 'fdinfo' and handles[fd] == '/code': raise ValueError('SECRET raw fdinfo')
        return IDS[handles[fd]]
    monkeypatch.setattr(module, '_read_fd_mount_id', observed)
    with pytest.raises(module.SandboxError) as caught: module._directory_mount_ids()
    value = module.sandbox_failure_diagnostic(caught.value)
    assert value['stage'] == 'kernel_collect' and value['gate'] == 'mountinfo'
    assert value['facts']['target'] == 'CODE'
    assert [row[1] for row in calls if row[0] == 'open'] == ['/', '/code']
    assert [row[1] for row in calls if row[0] == 'close'] == list(handles)
    assert 'SECRET' not in json.dumps(value)


def test_missing_o_path_does_not_fall_back_to_other_open_mode(monkeypatch):
    module = subject()
    monkeypatch.setattr(module, 'os', SimpleNamespace(open=lambda *a: pytest.fail('unsupported probe executed')))
    with pytest.raises(module.SandboxError) as caught: module._directory_mount_ids()
    assert module.sandbox_failure_diagnostic(caught.value)['reason'] == 'unsupported'


def test_changed_mount_snapshot_is_rejected_before_resolving(monkeypatch):
    module = subject(); values = iter([RAW, RAW + '\n'])
    monkeypatch.setattr(module, '_read_text', lambda path: next(values))
    monkeypatch.setattr(module, '_directory_mount_ids', lambda: IDS)
    with pytest.raises(module.SandboxError) as caught: module._verified_mount_facts()
    value = module.sandbox_failure_diagnostic(caught.value)
    assert value['facts']['check'] == 'snapshot' and value['reason'] == 'mismatch'


def test_stable_snapshot_is_bound_to_fixed_directory_ids(monkeypatch):
    module = subject(); reads = []
    def read(path): reads.append(path); return RAW
    monkeypatch.setattr(module, '_read_text', read)
    monkeypatch.setattr(module, '_directory_mount_ids', lambda: IDS)
    facts = module._verified_mount_facts()
    assert facts['/code']['mount_point'] == '/code' and facts['/code']['read_only'] is True
    assert facts['/work']['read_only'] is False
    assert reads == ['/proc/self/mountinfo', '/proc/self/mountinfo']


def test_fd_mapping_disagreement_is_safe_failure(monkeypatch):
    module = subject()
    monkeypatch.setattr(module, '_read_text', lambda path: RAW)
    monkeypatch.setattr(module, '_directory_mount_ids', lambda: {**IDS, '/code': 3})
    with pytest.raises(module.SandboxError) as caught: module._verified_mount_facts()
    value = module.sandbox_failure_diagnostic(caught.value)
    assert value['facts']['model_code'] == 'observed_mismatch'
    assert value['facts']['check'] == 'model'
    assert value['stage'] == 'kernel_collect' and value['gate'] == 'mountinfo'


def test_model_codes_outside_allowlist_cannot_be_exported():
    module = subject()
    for value in ('SECRET /mount/path', 'x' * 5000, True, None):
        error = module.SandboxError(stage='kernel_collect', gate='mountinfo', reason='invalid',
                                    facts={'check': 'model', 'model_code': value})
        assert module.sandbox_failure_diagnostic(error) is None
