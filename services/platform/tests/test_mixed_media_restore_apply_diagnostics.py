"""Offline full mixed.apply orchestration; no PostgreSQL, Docker or provider calls.

The real apply, guards, canonicalization, member/hash validation, schema binding,
model-state validation and tar reading execute. Filesystem ownership, service,
PostgreSQL and crypto boundaries are in-memory doubles. This is control-flow and
failure-observability coverage, not a fresh database or crypto restore proof.
"""
import ast
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import stat
import tarfile
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[3]
SOURCE = REPO / 'services/platform/scripts/verify_mixed_media_restore_drill.py'


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def setup(monkeypatch, fault=None, source=SOURCE):
    m = load(source, 'offline_apply_only')
    fixture = load(REPO / 'services/platform/tests/test_mixed_media_restore_drill.py', 'offline_state_only')
    real_core = load(REPO / 'services/platform/scripts/verify_encrypted_restore_drill.py', 'offline_real_core')
    state = fixture.mixed_state()
    missing = m.missing_staged(state)
    rows = [dict(storage_key=str(n) * 64, sha256='a' * 64, size=1, state='attached') for n in (3, 4)]
    rows.append({k: missing[k] for k in ('storage_key', 'sha256', 'size', 'state')})
    files = {'media/blobs/' + str(n) * 64: bytes([n]) for n in (3, 4)}
    media_inventory = dict(missing_staged=missing, mixed_window_sha256=m.digest(m.canonical(state)),
                           registry_sha256=m.digest(m.canonical(rows)), synthetic=True)
    configs = {'configs' + name: b'synthetic-only' for name in m.SOURCE_FILES}
    configs['configs/etc/easel-central/central.env'] = b'CENTRAL_DB_SCHEMA=synthetic_schema\n'
    configs['configs' + str(m.MEDIA)] = m.canonical({'storage_root': str(m.STORAGE)})
    for kind in m.UNITS:
        main = fixture.main_for(kind, m)
        configs['configs' + str(m.MAIN_UNITS[kind])] = main
        configs['configs' + str(m.DROPINS[kind])] = m.expected_dropin(main, kind)
    metadata = [dict(name=name, synthetic=True) for name in sorted(configs)]
    migrations = [dict(version='synthetic-016', checksum='d' * 64)]
    processes = {k: dict(pid=n, mountinfo_sha256=str(n) * 64) for k, n in [('api', 3), ('console', 4)]}
    targets = [dict(kind=k, name='synthetic-' + k, user='synthetic-owner') for k in ('central', 'identity')]
    plan = dict(work_dir='/synthetic-private', dest='/synthetic-cipher', targets=targets,
                nonce='a' * 32, release=m.RELEASE, candidate_sha256=m.WHEEL_SHA,
                config_hashes={k: m.digest(v) for k, v in configs.items()},
                config_metadata_sha256=m.digest(m.canonical(metadata)), active_release_processes=processes,
                expected_migrations=migrations, media_root_inventory_sha256=m.digest(m.canonical(media_inventory)),
                pending_exception=state['pending_exception'], missing_staged=missing, missing_staged_sha256=m.digest(m.canonical(missing)),
                media_window_sha256=m.digest(m.canonical(state)), media_registry_sha256=m.digest(m.canonical(rows)),
                central_schema_sha256=m.digest(b'synthetic_schema'),
                central_config_sha256=m.digest(configs['configs/etc/easel-central/central.env']), consistency='synthetic only')
    events, storage, counts = [], {}, {}

    class MemoryPath:
        def __init__(self, path): self.path = str(path)
        def __truediv__(self, value): return MemoryPath(self.path + '/' + str(value))
        def __str__(self): return self.path
        @property
        def name(self): return self.path.rsplit('/', 1)[-1]
        def mkdir(self, mode): events.append(('mkdir', self.path))
        def lstat(self): return SimpleNamespace(st_uid=0, st_mode=stat.S_IFDIR | 0o700)
        def read_bytes(self): return storage[self.path]

    def write_private(path, raw):
        key = str(path)
        if key in storage: raise FileExistsError('synthetic exclusive write')
        storage[key] = raw

    def phase(_root, value):
        assert value in allowed_phases
        events.append(('phase', value))
        if fault == 'phase_write' and value == 'verify_original_services': raise OSError('synthetic-secret-message')

    def configurations(*_):
        counts['config'] = counts.get('config', 0) + 1
        value = copy.deepcopy(configs)
        if fault == 'config' and counts['config'] == 3: value[next(iter(value))] = b'changed'
        return value, 'synthetic_schema', 997, 997, copy.deepcopy(metadata)

    def process(*_):
        counts['process'] = counts.get('process', 0) + 1
        value = copy.deepcopy(processes)
        if fault == 'process' and counts['process'] == 2: value['api']['mountinfo_sha256'] = 'changed'
        return value

    def central_read(_core, _backup, statement, *_):
        value = copy.deepcopy(state if statement == 'window' else rows)
        if statement == 'window' and fault == 'window': value['pending_jobs'] = 0
        if statement == 'rows' and fault == 'registry': value[0]['sha256'] = '0' * 64
        return value

    def capture_blobs(*_):
        value = dict(files)
        if fault == 'files': value[next(iter(value))] = b'changed'
        return value, copy.deepcopy({k: v for k, v in media_inventory.items() if k != 'mixed_window_sha256'})

    def archive(entries, manifest):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w') as out:
            for name, raw in dict(entries, **{'manifest.json': m.canonical(manifest)}).items():
                entry = tarfile.TarInfo(name); entry.size = len(raw); out.addfile(entry, io.BytesIO(raw))
        return stream.getvalue()

    def save(envelope, _identifier, verify):
        verify(envelope); storage['/synthetic-cipher/saved'] = envelope; return '/synthetic-cipher/saved'

    core = SimpleNamespace(
        SOURCES=[('central', 'synthetic-source', 'synthetic-owner', 'synthetic-db'),
                 ('identity', 'synthetic-identity', 'synthetic-owner', 'synthetic-id-db')],
        TARGET='synthetic-target', reviewed=real_core.reviewed, write_private=write_private, write_phase=phase,
        service_state=lambda *_: {'synthetic_service': True}, production_containers=lambda *_: {'synthetic_container': True},
        backup_definition_hashes=lambda *_: {'synthetic_timer': 'unchanged'}, resource_gate=lambda: None,
        utc=lambda: 'synthetic-time', verify_members=real_core.verify_members, bound_central_schema=real_core.bound_central_schema,
        capture_database=lambda *_: (b'PGDMP-id', b'synthetic-role', dict(database=dict(datname='synthetic-id-db')), {}),
        match_inventories=real_core.match_inventories,
        restore_one=lambda _b, item, *_a, **_k: dict(kind=item['kind'], matched=True),
        stop_target=lambda _b, item: events.append(('stop', item['kind'])),
        psql=lambda *_: copy.deepcopy(migrations if _[-1] == 'migrations' else state if _[-1] == 'window' else rows),
    )
    load_core_ast = next(n for n in ast.parse(source.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == 'load_core')
    phase_assignment = next(n for n in load_core_ast.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Attribute) and t.attr == 'PHASES' for t in n.targets))
    allowed_phases = eval(compile(ast.Expression(phase_assignment.value), '<trusted-phase-contract>', 'eval'),
                          {'core': SimpleNamespace(PHASES=real_core.PHASES)})
    backup = SimpleNamespace(
        ROOT=MemoryPath('/synthetic-key-material'), command=lambda *_a, **_k: None,
        archive=archive, crypto=lambda: SimpleNamespace(seal=lambda _k, payload, _m: payload,
            unseal=lambda _k, value, *_: value), verify_payload=lambda *_: None, save=save,
        read_fixed=lambda path, *_a, **_k: storage.get(str(path), b'synthetic-key'),
    )
    monkeypatch.setattr(m, 'Path', MemoryPath)
    monkeypatch.setattr(m, 'configurations', configurations)
    monkeypatch.setattr(m, 'active_release_processes', process)
    monkeypatch.setattr(m, 'capture_media_database', lambda *_: (b'PGDMP-central', b'synthetic-role',
        dict(database=dict(datname='synthetic-db')), {}, files, copy.deepcopy(media_inventory)))
    monkeypatch.setattr(m, 'central_read', central_read)
    monkeypatch.setattr(m, 'window_sql', lambda *_: 'window')
    monkeypatch.setattr(m, 'media_rows_sql', lambda *_: 'rows')
    monkeypatch.setattr(m, 'migrations_sql', lambda *_: 'migrations')
    monkeypatch.setattr(m, 'capture_blobs', capture_blobs)
    monkeypatch.setattr(m, 'release_migrations', lambda *_: [] if fault == 'migrations' else migrations)
    monkeypatch.setattr(m, 'restore_source_configurations', lambda *_: dict(source_configuration_files_restored_verified=True))
    monkeypatch.setattr(m, 'restore_private_media', lambda *_: dict(
        media_blobs_covered=True, media_config_restored_verified=True, permissions_restored_verified=True,
        blob_count=2, nonempty_media_blobs_restore_verified=True,
        known_missing_staged_absence_restored_verified=True, missing_staged_count=1,
        missing_staged_sha256=plan['missing_staged_sha256']))
    monkeypatch.setattr(m, 'cleanup_owned', lambda _c, _b, ts: [dict(kind=t['kind'], not_running_verified=True) for t in ts])
    return m, core, backup, plan, events, storage



TAIL_FAULTS = [
    ('window', 'verify_media_state'), ('registry', 'verify_media_registry'),
    ('files', 'verify_media_files'), ('config', 'verify_media_config'),
    ('process', 'verify_media_processes'), ('migrations', 'verify_media_migrations'),
    ('phase_write', 'verify_original_services'),
]


def test_reviewed_123_require_guards_remain_identical_and_ordered():
    guards = [ast.dump(node, include_attributes=False) for node in ast.walk(ast.parse(SOURCE.read_text()))
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'require']
    assert len(guards) == 123
    assert hashlib.sha256(json.dumps(guards, separators=(',', ':')).encode()).hexdigest() == \
        '55579de0a3c14c97b70f1af0c8fc822ec1fd7bccf792d99cc72aaf02146bd7d8'


def test_complete_apply_runs_both_database_branches_and_all_tail_guards(monkeypatch, capsys):
    m, core, backup, plan, events, storage = setup(monkeypatch)
    m.apply(core, backup, plan, m.digest(m.canonical(plan)))
    receipt = json.loads(storage['/synthetic-private/restore-safe.json'])
    assert receipt['success'] is True
    assert [row['kind'] for row in receipt['databases']] == ['central', 'identity']
    assert [('stop', 'central'), ('stop', 'identity')] == [e for e in events if e[0] == 'stop']
    phases = [e[1] for e in events if e[0] == 'phase']
    assert set(phase for _, phase in TAIL_FAULTS) <= set(phases)
    assert '/synthetic-private/failure-location-private.json' not in storage
    assert receipt['pending_actual_null_preserved'] is True and receipt['pending_hold_preserved'] == 1000000
    capsys.readouterr()


@pytest.mark.parametrize('fault,phase', TAIL_FAULTS)
def test_original_failed_style_reports_exact_tail_location_without_success(monkeypatch, fault, phase):
    m, core, backup, plan, events, storage = setup(monkeypatch, fault)
    with pytest.raises(Exception): m.apply(core, backup, plan, m.digest(m.canonical(plan)))
    assert '/synthetic-private/restore-safe.json' not in storage
    assert '/synthetic-private/enable-proof.json' not in storage
    assert '/synthetic-private/failure-location-private.json' in storage, 'old failed-style phase must gain a private location'
    location = json.loads(storage['/synthetic-private/failure-location-private.json'])
    assert location['phase_attempted'] == phase
    durable = 'verify_media_migrations' if fault == 'phase_write' else phase
    assert location['phase_last_durable'] == durable
    assert json.loads(storage['/synthetic-private/failure-safe.json'])['phase'] == durable
    assert location['exception']['frames']
    assert any(frame['source'] == 'mixed' and frame['function'] == 'apply' for frame in location['exception']['frames'])
    assert all(set(frame) == {'source', 'function', 'line'} for frame in location['exception']['frames'])
    assert 'synthetic-secret-message' not in json.dumps(location)
    assert [e for e in events if e[0] == 'stop'] == [('stop', 'central'), ('stop', 'identity')]


def test_private_location_never_serializes_unknown_types_messages_paths_or_values():
    m = load(SOURCE, 'privacy_diagnostic')
    assert hasattr(m, 'private_exception_location'), 'private location diagnostic is required'
    sentinel = 'SYNTHETIC_PRIVATE_SENTINEL_TOKEN_SQL_USER'
    SecretType = type(sentinel, (Exception,), {})
    try:
        raise SecretType(sentinel)
    except Exception as error:
        value = m.private_exception_location(error)
    assert value['type'] == 'other'
    assert sentinel not in json.dumps(value)
    assert str(REPO) not in json.dumps(value)
    assert all(frame['source'] == 'other' and frame['function'] == 'other' for frame in value['frames'])


def test_failed_diagnostic_write_preserves_the_original_guard_exception(monkeypatch):
    m, core, backup, plan, events, storage = setup(monkeypatch, 'process')
    original_write = core.write_private
    def failing_diagnostic_write(path, raw):
        if str(path).endswith('/failure-location-private.json'):
            raise OSError('synthetic-diagnostic-write-secret')
        return original_write(path, raw)
    core.write_private = failing_diagnostic_write
    with pytest.raises(m.Rejected) as error: m.apply(core, backup, plan, m.digest(m.canonical(plan)))
    assert str(error.value) == 'media_restore_rejected'
    assert '/synthetic-private/restore-safe.json' not in storage
    assert '/synthetic-private/failure-safe.json' in storage
    assert [e for e in events if e[0] == 'stop'] == [('stop', 'central'), ('stop', 'identity')]


def test_failed_failure_receipt_write_preserves_original_guard_after_cleanup(monkeypatch):
    m, core, backup, plan, events, storage = setup(monkeypatch, 'process')
    original_write, original_cleanup, original_require = core.write_private, m.cleanup_owned, m.require
    guard_errors = []
    def record_guard(condition):
        try:
            return original_require(condition)
        except m.Rejected as error:
            guard_errors.append(error)
            raise
    def record_cleanup(*args):
        events.append(('cleanup', 'started'))
        result = original_cleanup(*args)
        events.append(('cleanup', 'finished'))
        return result
    def failing_failure_receipt(path, raw):
        if str(path).endswith('/failure-safe.json'):
            events.append(('failure_receipt', 'write_failed'))
            raise OSError('synthetic-failure-receipt-write-secret')
        return original_write(path, raw)
    m.require, m.cleanup_owned, core.write_private = record_guard, record_cleanup, failing_failure_receipt
    with pytest.raises(m.Rejected) as caught:
        m.apply(core, backup, plan, m.digest(m.canonical(plan)))
    assert len(guard_errors) == 1 and caught.value is guard_errors[0]
    assert events.index(('cleanup', 'finished')) < events.index(('failure_receipt', 'write_failed'))
    assert '/synthetic-private/restore-safe.json' not in storage
    assert '/synthetic-private/enable-proof.json' not in storage


@pytest.mark.parametrize('name', ['restore-safe.json', 'enable-proof.json'])
def test_success_publication_write_failure_is_not_swallowed(monkeypatch, capsys, name):
    m, core, backup, plan, events, storage = setup(monkeypatch)
    original_write = core.write_private
    publication_error = OSError('synthetic-proof-publication-write-secret')
    def failing_publication(path, raw):
        if str(path).endswith('/' + name):
            raise publication_error
        return original_write(path, raw)
    core.write_private = failing_publication
    with pytest.raises(OSError) as caught:
        m.apply(core, backup, plan, m.digest(m.canonical(plan)))
    assert caught.value is publication_error
    assert '/synthetic-private/' + name not in storage
    assert '/synthetic-private/failure-safe.json' in storage
    assert capsys.readouterr().out == ''


def test_success_cleanup_exception_is_not_swallowed(monkeypatch, capsys):
    m, core, backup, plan, events, storage = setup(monkeypatch)
    cleanup_error = OSError('synthetic-success-cleanup-secret')
    def failing_cleanup(*_):
        raise cleanup_error
    m.cleanup_owned = failing_cleanup
    with pytest.raises(OSError) as caught:
        m.apply(core, backup, plan, m.digest(m.canonical(plan)))
    assert caught.value is cleanup_error
    capsys.readouterr()
