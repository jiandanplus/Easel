#!/usr/bin/env python3
"""Reviewable, fixed-scope API media mount repair; never executes provider calls.

``plan`` only prints JSON. The operator saves it as a root-owned 0600 file,
reviews its exact bytes, then supplies its SHA and this script's SHA to apply.
Apply checks idle, stops only the named API unit, confirms inactivity and a
fresh unchanged financial/job summary, then appends, reloads and starts API.
The before/after summaries detect stop-window races; they are not an atomic
ingress lock. Pending reconciliation is preserved. A stop-window race restores
only the unchanged old API. Failures after replacement leave a private receipt
and the original bytes; this helper never rolls back a changed file blindly.
"""
import sys
sys.dont_write_bytecode = True

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import stat
import subprocess
from uuid import uuid4


RELEASE = Path('/srv/easel/releases/20261027-images-m1-1-size-v2')
STORAGE = Path('/var/lib/easel-central/media')
API_UNIT = 'easel-central-production.service'
CONSOLE_UNIT = 'easel-console-production.service'
SYSTEMD = Path('/etc/systemd/system')
API_DROPIN = SYSTEMD / (API_UNIT + '.d') / '90-m1-images.conf'
CONSOLE_DROPIN = SYSTEMD / (CONSOLE_UNIT + '.d') / '90-m1-images.conf'
PRIVATE_ROOT = Path('/root/easel-image-release-20261027')
HELPERS = {
    'prepare_m1_image_release.py': 'ff9d019ef275798b24e12adcc25c8018c9f8f3ff244dc003133f2df4912c02ef',
    'prepare_m1_image_cutover.py': '32ba883a5362af05edf3a53dc070c05f80ec8275a947a4bb7063078ae80aba36',
}
DIRECTIVE = b'ReadWritePaths=/var/lib/easel-central/media\n'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def script_digest():
    return digest(Path(__file__).read_bytes())


def canonical(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False,
                       separators=(',', ':')) + '\n').encode()


def unique(pairs):
    result = {}
    for key, item in pairs:
        if key in result:
            raise ValueError('duplicate_json_key')
        result[key] = item
    return result


def read_file(path):
    path = Path(path)
    if path.resolve() != path:
        raise ValueError('symlink_source_rejected')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 1048576:
            raise ValueError('regular_bounded_file_required')
        raw = stream.read(1048577)
    metadata = {'mode': stat.S_IMODE(info.st_mode), 'uid': info.st_uid,
                'gid': info.st_gid, 'size': info.st_size, 'nlink': info.st_nlink}
    return raw, {'sha256': digest(raw), 'metadata_sha256': digest(canonical(metadata))}


def read_private(path):
    raw, _ = read_file(path)
    info = Path(path).stat()
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError('private_file_required')
    return raw


def write_private(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fchmod(stream.fileno(), 0o600)
        os.fsync(stream.fileno())


def append_writable(original):
    text = original.decode('utf-8')
    if (not original.endswith(b'\n') or not text.startswith('[Service]\n')
            or text.count('[Service]') != 1 or re.search(r'^\s*ReadWritePaths\s*=', text, re.M)):
        raise ValueError('original_dropin_requires_review')
    return original + DIRECTIVE


def require_idle(counts):
    if any(counts.get(key) != 0 for key in ('text_active', 'media_active')):
        raise ValueError('active_jobs_require_review')
    return counts


def require_same_window(before, after):
    require_idle(after)
    if after != before:
        raise ValueError('job_window_changed')
    return after


def helpers():
    directory = Path(__file__).resolve().parent
    for name, expected in HELPERS.items():
        raw, _ = read_file(directory / name)
        if digest(raw) != expected:
            raise ValueError('dependency_hash_mismatch')
    # Both scripts have no import-time host operations. Pin their bytes first.
    sys.path.insert(0, str(directory))
    spec = importlib.util.spec_from_file_location('storage_repair_cutover', directory / 'prepare_m1_image_cutover.py')
    cutover = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cutover)
    if digest(Path(cutover.release.__file__).read_bytes()) != HELPERS['prepare_m1_image_release.py']:
        raise ValueError('dependency_module_mismatch')
    return cutover, cutover.release


def systemctl(*args):
    result = subprocess.run(['/usr/bin/systemctl', *args], capture_output=True, text=True,
                            timeout=180, check=False)
    if result.returncode:
        raise ValueError('systemd_operation_failed')
    return result.stdout.strip()


def active_counts():
    _, release = helpers()
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    env = release.env_values(release.CENTRAL_ENV)
    dsn = make_conninfo(env['CENTRAL_DATABASE_URL'], connect_timeout=5,
                        options='-c default_transaction_read_only=on -c statement_timeout=10000')
    result = {}
    with psycopg.connect(dsn) as connection:
        connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        connection.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(env['CENTRAL_DB_SCHEMA'])))
        nonterminal = {}
        for label, table in [('text', 'execution_jobs'), ('media', 'media_jobs')]:
            row = connection.execute(sql.SQL("SELECT count(*) FILTER (WHERE status IN ('reserved','dispatched')), "
                "count(*) FILTER (WHERE status='pending_reconciliation') FROM {}").format(sql.Identifier(table))).fetchone()
            result[label + '_active'], result[label + '_pending'] = row
            # Hash identifiers only inside the host process. No lease/heartbeat
            # fields participate, so a stopped worker's lease cannot hide a race.
            rows = connection.execute(sql.SQL("SELECT j.id,j.status,j.amount,j.actual,j.reservation_id,"
                "r.status,r.amount,r.actual FROM {} j JOIN credit_reservations r ON r.id=j.reservation_id "
                "WHERE j.status IN ('reserved','dispatched','pending_reconciliation') ORDER BY j.id LIMIT 100001")
                .format(sql.Identifier(table))).fetchall()
            if len(rows) > 100000:
                raise ValueError('snapshot_limit_requires_review')
            nonterminal[label] = rows
        result['nonterminal_sha256'] = digest(canonical(nonterminal))
        financial = {}
        for table in ('credit_reservations', 'credit_holds', 'credit_journals', 'credit_entries'):
            rows = connection.execute(sql.SQL("SELECT (to_jsonb(t)-'updated_at')::text FROM {} t "
                "ORDER BY 1 LIMIT 100001").format(sql.Identifier(table))).fetchall()
            if len(rows) > 100000:
                raise ValueError('snapshot_limit_requires_review')
            financial[table] = rows
        result['financial_sha256'] = digest(canonical(financial))
    return result


def service_state(kind, cutover, original, *, repaired=False):
    unit = API_UNIT if kind == 'api' else CONSOLE_UNIT
    dropin = API_DROPIN if kind == 'api' else CONSOLE_DROPIN
    expected_user = 'easel-central' if kind == 'api' else 'easel-console'
    for name, expected in [('User', expected_user), ('Group', expected_user),
                           ('WorkingDirectory', str(RELEASE)), ('ProtectSystem', 'strict')]:
        if systemctl('show', unit, '-p', name, '--value') != expected:
            raise ValueError('effective_unit_scope_mismatch')
    if shlex.split(systemctl('show', unit, '-p', 'DropInPaths', '--value')) != [str(dropin)]:
        raise ValueError('unexpected_dropins')
    writable = shlex.split(systemctl('show', unit, '-p', 'ReadWritePaths', '--value'))
    if writable != ([str(STORAGE)] if repaired and kind == 'api' else []):
        raise ValueError('unexpected_writable_paths')
    if systemctl('is-active', unit) != 'active':
        raise ValueError('original_service_not_active')
    pid = int(systemctl('show', unit, '-p', 'MainPID', '--value'))
    if pid <= 1:
        raise ValueError('service_pid_required')
    root = Path('/proc') / str(pid)
    argv = [item.decode() for item in (root / 'cmdline').read_bytes().rstrip(b'\0').split(b'\0')]
    expected = cutover.command(original, kind).split(' ')
    if not cutover.process_identity_matches(os.readlink(root / 'cwd'), os.readlink(root / 'exe'), argv, RELEASE, expected):
        raise ValueError('actual_process_mismatch')
    account = pwd.getpwnam(expected_user)
    credentials = {line.split(':', 1)[0]: line.split(':', 1)[1].split()
                   for line in (root / 'status').read_text().splitlines() if line.startswith(('Uid:', 'Gid:'))}
    if (credentials.get('Uid') != [str(account.pw_uid)] * 4
            or credentials.get('Gid') != [str(account.pw_gid)] * 4):
        raise ValueError('actual_process_credentials_mismatch')
    state = {'pid': pid, 'start': (root / 'stat').read_text().rpartition(')')[2].split()[19],
             'mount_namespace': os.readlink(root / 'ns/mnt'), 'command_sha256': digest(canonical(argv))}
    return state


def inspect_current():
    cutover, release = helpers()
    release.verify_staged()
    account = pwd.getpwnam('easel-central')
    if STORAGE.resolve() != STORAGE:
        raise ValueError('unsafe_storage_path')
    info = STORAGE.stat()
    if not stat.S_ISDIR(info.st_mode) or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (account.pw_uid, account.pw_gid, 0o700):
        raise ValueError('storage_owner_or_mode_mismatch')
    paths = [SYSTEMD / API_UNIT, SYSTEMD / CONSOLE_UNIT, API_DROPIN, CONSOLE_DROPIN,
             release.CENTRAL_ENV, release.CONSOLE_ENV, release.EXECUTION, release.MEDIA]
    files = {}
    raw_files = {}
    for path in paths:
        raw_files[path], files[str(path)] = read_file(path)
    _, expected_media = release.configurations()
    if json.loads(raw_files[release.MEDIA], object_pairs_hook=unique) != expected_media:
        raise ValueError('media_configuration_mismatch')
    state = {}
    for kind, unit, dropin in [('api', API_UNIT, API_DROPIN), ('console', CONSOLE_UNIT, CONSOLE_DROPIN)]:
        original = raw_files[SYSTEMD / unit].decode()
        if raw_files[dropin] != cutover.render_dropin(original, kind).encode():
            raise ValueError('original_dropin_bytes_mismatch')
        state[kind] = service_state(kind, cutover, original)
    return {'version': 1, 'script_sha256': script_digest(), 'candidate_sha256': release.WHEEL_SHA,
            'dependency_sha256': HELPERS, 'files': files,
            'service_state_sha256': digest(canonical(state)), 'original_api_mainpid': state['api']['pid'],
            'console_process_sha256': digest(canonical(state['console'])), 'jobs': require_idle(active_counts()),
            'change': {'path': str(API_DROPIN), 'append_ascii': DIRECTIVE.decode(),
                       'console_unchanged': True, 'protect_system': 'strict'},
            'probe': {'actual_api_mainpid_mount_namespace': True, 'media': str(STORAGE),
                      'readonly': ['/etc', str(RELEASE)], 'exclusive_mode': '0600',
                      'fsync_then_remove': True, 'as_service_user': True},
            'maintenance': {'stop_only': API_UNIT, 'confirm_inactive_before_write': True,
                            'start_only': API_UNIT, 'stop_window_summary_must_match': True,
                            'restore_unchanged_old_api_on_stop_race': True},
            'idle_checks_are_not_lock': True,
            'pending_reconciliation_preserved': True, 'database_writes': 0, 'provider_posts': 0}


def make_plan():
    return inspect_current()


def verify_files(plan):
    for path, expected in plan['files'].items():
        if read_file(Path(path))[1] != expected:
            raise ValueError('approved_files_changed')


def verify_inactive():
    if (systemctl('show', API_UNIT, '-p', 'ActiveState', '--value') != 'inactive'
            or systemctl('show', API_UNIT, '-p', 'MainPID', '--value') != '0'):
        raise ValueError('api_must_be_inactive')


def verify_console(plan):
    cutover, _ = helpers()
    original = read_file(SYSTEMD / CONSOLE_UNIT)[0].decode()
    if digest(canonical(service_state('console', cutover, original))) != plan['console_process_sha256']:
        raise ValueError('console_process_changed')


def restore_original_api(plan, original):
    # Restore availability only while every old input and the old drop-in are
    # still exactly approved. Never reverse other operators' changes.
    verify_files(plan)
    if read_file(API_DROPIN)[0] != original:
        raise ValueError('old_api_restore_requires_review')
    verify_inactive()
    systemctl('start', API_UNIT)
    cutover, _ = helpers()
    service_state('api', cutover, read_file(SYSTEMD / API_UNIT)[0].decode())
    return True


def backup_original(original, plan):
    if PRIVATE_ROOT.resolve() != PRIVATE_ROOT:
        raise ValueError('unsafe_private_directory')
    directory = PRIVATE_ROOT / ('media-storage-writable-' + uuid4().hex)
    directory.mkdir(mode=0o700)
    write_private(directory / '90-m1-images.conf.original', original)
    write_private(directory / 'approved-plan.json', canonical(plan))
    return directory


def replace_dropin(original, replacement):
    current, _ = read_file(API_DROPIN)
    if current != original:
        raise ValueError('dropin_changed_before_replace')
    temporary = API_DROPIN.parent / ('.media-storage-' + uuid4().hex + '.tmp')
    try:
        write_private(temporary, replacement)
        if read_file(API_DROPIN)[0] != original:
            raise ValueError('dropin_changed_before_replace')
        os.replace(temporary, API_DROPIN)
        directory = os.open(API_DROPIN.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


PROBE_CODE = r'''
import errno, json, os, stat, sys, uuid
os.setgroups([])
os.setgid(int(sys.argv[2]))
os.setuid(int(sys.argv[1]))
result = {}
for label, directory in [('media', '/var/lib/easel-central/media'), ('etc', '/etc'),
                         ('release', '/srv/easel/releases/20261027-images-m1-1-size-v2')]:
    path = directory + '/.easel-storage-probe-' + uuid.uuid4().hex
    record = {'writable': False, 'readonly': bool(os.statvfs(directory).f_flag & os.ST_RDONLY), 'removed': True}
    created = False
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        created = True
        try:
            os.fchmod(fd, 0o600)
            if os.write(fd, b'easel-storage-probe\n') != 20:
                raise RuntimeError('short_probe_write')
            os.fsync(fd)
            record['writable'] = stat.S_IMODE(os.fstat(fd).st_mode) == 0o600
        finally:
            os.close(fd)
    except OSError as error:
        if error.errno not in (errno.EROFS, errno.EACCES, errno.EPERM):
            raise
    finally:
        if created:
            os.unlink(path)
        record['removed'] = not os.path.lexists(path)
    result[label] = record
print(json.dumps(result, sort_keys=True))
'''


def probe_command(pid, uid, gid):
    if any(not isinstance(item, int) or item <= 0 for item in (pid, uid, gid)):
        raise ValueError('nonroot_service_identity_required')
    return ['/usr/bin/nsenter', '--mount=/proc/' + str(pid) + '/ns/mnt', '--',
            str(RELEASE / '.venv/bin/python'), '-I', '-B', '-c', PROBE_CODE, str(uid), str(gid)]


def validate_probe(value):
    for key in ('media', 'etc', 'release'):
        row = value.get(key, {})
        if (row.get('writable') is not (key == 'media') or row.get('readonly') is not (key != 'media')
                or row.get('removed') is not True):
            raise ValueError('namespace_probe_failed')
    return value


def verify_repaired():
    cutover, _ = helpers()
    original = read_file(SYSTEMD / API_UNIT)[0].decode()
    before = service_state('api', cutover, original, repaired=True)
    account = pwd.getpwnam('easel-central')
    completed = subprocess.run(probe_command(before['pid'], account.pw_uid, account.pw_gid),
                               capture_output=True, timeout=30, check=False)
    if completed.returncode or len(completed.stdout) > 4096:
        raise ValueError('namespace_probe_failed')
    probes = validate_probe(json.loads(completed.stdout, object_pairs_hook=unique))
    if service_state('api', cutover, original, repaired=True) != before:
        raise ValueError('api_process_changed_during_probe')
    return {'success': True, 'api_process_sha256': digest(canonical(before)), 'probes': probes}


def write_receipt(directory, value):
    write_private(directory / ('receipt-' + uuid4().hex + '.json'), canonical(value))


def apply(plan_path, expected_plan_sha256, expected_script_sha256):
    if not re.fullmatch('[0-9a-f]{64}', expected_script_sha256) or script_digest() != expected_script_sha256:
        raise ValueError('script_hash_mismatch')
    raw = read_private(plan_path)
    if not re.fullmatch('[0-9a-f]{64}', expected_plan_sha256) or digest(raw) != expected_plan_sha256:
        raise ValueError('approved_plan_hash_mismatch')
    approved = json.loads(raw, object_pairs_hook=unique)
    if approved.get('script_sha256') != expected_script_sha256 or inspect_current() != approved:
        raise ValueError('approved_state_changed')
    original, _ = read_file(API_DROPIN)
    replacement = append_writable(original)
    directory = backup_original(original, approved)
    receipt = {'success': False, 'phase': 'backed_up', 'backup_path': str(directory),
               'approved_plan_sha256': expected_plan_sha256, 'script_sha256': expected_script_sha256,
               'original_sha256': digest(original), 'replacement_sha256': digest(replacement),
               'database_writes': 0, 'provider_posts': 0, 'pending_reconciliation_preserved': True,
               'idle_checks_are_not_lock': True, 'original_api_restored': False}
    stopped = False
    try:
        if inspect_current() != approved:
            raise ValueError('approved_state_changed')
        before = require_same_window(approved['jobs'], active_counts())
        systemctl('stop', API_UNIT)
        stopped = True
        receipt['phase'] = 'api_stopped'
        verify_inactive()
        require_same_window(before, active_counts())
        verify_files(approved)
        verify_console(approved)
        verify_inactive()
        require_same_window(before, active_counts())
        receipt['phase'] = 'replacement_attempted'
        replace_dropin(original, replacement)
        receipt['phase'] = 'dropin_written'
        systemctl('daemon-reload')
        verify_inactive()
        require_same_window(before, active_counts())
        systemctl('start', API_UNIT)
        receipt['phase'] = 'api_started'
        receipt['verification'] = verify_repaired()
        verify_console(approved)
        receipt.update(success=True, phase='verified')
        return receipt
    except Exception:
        if stopped and read_file(API_DROPIN)[0] == original:
            try:
                receipt['original_api_restored'] = restore_original_api(approved, original)
            except Exception:
                receipt['old_api_restore_requires_operator_review'] = True
        raise
    finally:
        write_receipt(directory, receipt)


def main():
    os.umask(0o077)
    if os.geteuid() != 0:
        raise ValueError('root_host_operator_required')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['plan', 'apply'])
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--expected-plan-sha256')
    parser.add_argument('--expected-script-sha256')
    args = parser.parse_args()
    if args.phase == 'plan':
        result = make_plan()
    else:
        if not args.plan or not args.expected_plan_sha256 or not args.expected_script_sha256:
            raise ValueError('exact_approved_plan_and_script_required')
        result = apply(args.plan, args.expected_plan_sha256, args.expected_script_sha256)
    sys.stdout.buffer.write(canonical(result))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        code = str(error) if isinstance(error, ValueError) and re.fullmatch('[a-z_]{1,100}', str(error)) else 'storage_repair_failed'
        print(json.dumps({'success': False, 'error': code}), file=sys.stderr)
        raise SystemExit(1)
