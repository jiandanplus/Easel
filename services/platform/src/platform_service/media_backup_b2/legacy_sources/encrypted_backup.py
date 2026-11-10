#!/usr/bin/env python3
"""Fixed deployment backup. No plaintext files; stdout is success metadata only."""
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import pwd
import resource
import selectors
import socket
import stat
import subprocess
import sys
import tarfile
import time
import uuid

TARGET = 'ser362540173134'
OPERATION = 'fd13efa319487866926acf525e389dbd'
IMAGE = 'postgres@sha256:9e2cf6287f25c3151f4c9421ccce2b17f3e60061c111a29165e0d2cdd9ff0a97'
ROOT = Path('/root/easel-migration-20261024')
DEST = Path('/var/backups/easel-encrypted')
LIMIT = 32 * 1024 * 1024
HELPER_SHA256 = '7dd81fbc97d60c782fe2d2c43d81e37fc0b4ea926504e66309a43abc144ad7d6'
DATABASES = (
    ('central', 'easel-central-pg-20261024', 'easel_restore_admin', 'easel_workbench_preview'),
    ('identity', 'easel-identity-pg-v2-20261024', 'preview_owner', 'easel_logto_preview'),
)
REQUIRED = (
    '/etc/easel-central/central.env', '/etc/easel-central/console.env',
    '/etc/easel-central/execution.json',
    str(ROOT / 'central-connections-private.json'),
    str(ROOT / 'identity-connections-private.json'), str(ROOT / 'logto-runtime.env'),
)
OPTIONAL = (
    '/srv/easel/releases/20261024-split-v2/deployment-report.json',
    '/srv/easel/releases/20261024-split-v2/public-config.json',
    '/etc/nginx/sites-available/easel-central-public',
)


class Rejected(Exception):
    pass


def check(condition):
    if not condition:
        raise Rejected('backup rejected')


def digest(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def safe_parents(path):
    for parent in reversed(Path(path).parents):
        info = parent.lstat()
        check(stat.S_ISDIR(info.st_mode) and info.st_uid == 0)
        check(not (stat.S_IMODE(info.st_mode) & 0o022))


def read_fixed(path, maximum, owner=0, private=True):
    """Only callers' fixed allowlist paths; reject symlinks, links and unsafe owners."""
    safe_parents(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        check(stat.S_ISREG(info.st_mode) and info.st_uid == owner and info.st_nlink == 1)
        mode = stat.S_IMODE(info.st_mode)
        check(mode == 0o600 if private else not (mode & 0o022))
        check(0 <= info.st_size <= maximum)
        result = bytearray()
        while True:
            block = os.read(fd, min(65536, maximum + 1 - len(result)))
            if not block:
                break
            result.extend(block)
            check(len(result) <= maximum)
        after = os.fstat(fd)
        check((info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) ==
              (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns))
        check(len(result) == info.st_size)
        return bytes(result)
    finally:
        os.close(fd)


def command(args, maximum=LIMIT, data=None, timeout=180):
    """Bound stdout and stderr independently without temp files or exception output."""
    # Inherited PG variables, DOCKER_HOST, loader paths, etc. never influence execution.
    env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C', 'HOME': '/root'}
    proc = subprocess.Popen(args, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                            close_fds=True, start_new_session=True)
    selector = selectors.DefaultSelector()
    output = bytearray()
    error_length = 0
    remaining = memoryview(data) if data is not None else None
    try:
        for stream in (proc.stdout, proc.stderr):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ)
        if remaining is not None:
            os.set_blocking(proc.stdin.fileno(), False)
            selector.register(proc.stdin, selectors.EVENT_WRITE)
        deadline = time.monotonic() + timeout
        while selector.get_map():
            check(time.monotonic() < deadline)
            for key, mask in selector.select(min(0.2, max(0, deadline - time.monotonic()))):
                if mask & selectors.EVENT_WRITE:
                    if not remaining:
                        selector.unregister(proc.stdin)
                        proc.stdin.close()
                    else:
                        try:
                            written = os.write(proc.stdin.fileno(), remaining[:65536])
                            remaining = remaining[written:]
                        except BlockingIOError:
                            pass
                    continue
                try:
                    block = os.read(key.fileobj.fileno(), 65536)
                except BlockingIOError:
                    continue
                if not block:
                    selector.unregister(key.fileobj)
                    continue
                if key.fileobj is proc.stdout:
                    output.extend(block)
                    check(len(output) <= maximum)
                else:
                    # Diagnostics are deliberately never returned, written or logged.
                    error_length += len(block)
                    check(error_length <= 1024 * 1024)
        check(proc.wait(timeout=max(0.01, deadline - time.monotonic())) == 0)
        return bytes(output)
    except BaseException:
        if proc.poll() is None:
            import signal
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        raise Rejected('backup rejected') from None
    finally:
        selector.close()
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()


def container_gate(name):
    values = json.loads(command(['/usr/bin/docker', 'inspect', name], maximum=1024 * 1024))
    check(type(values) is list and len(values) == 1)
    item = values[0]
    check(item['Name'] == '/' + name and item['State']['Running'] is True)
    check(item['Config']['Image'] == IMAGE)
    check(item['Config']['Labels'].get('easel.deployment') == OPERATION)
    check(not item['HostConfig'].get('PortBindings'))
    check(not any(item['NetworkSettings'].get('Ports', {}).values()))
    network_mode = item['HostConfig'].get('NetworkMode', '')
    check(network_mode != 'host' and not network_mode.startswith('container:'))
    env = item['Config'].get('Env') or []
    pgdata = [x.split('=', 1)[1] for x in env if x.startswith('PGDATA=')]
    check(len(pgdata) <= 1)
    pgdata = pgdata[0] if pgdata else '/var/lib/postgresql/data'
    check(pgdata.startswith('/') and '..' not in Path(pgdata).parts)
    mounts = item.get('Mounts') or []
    check(any(m.get('Type') in ('volume', 'bind') and m.get('RW') is True and
              (pgdata == m.get('Destination') or pgdata.startswith(m.get('Destination', '') + '/'))
              and m.get('Source', '').startswith('/') and m.get('Destination') != '/'
              for m in mounts))
    image = json.loads(command(['/usr/bin/docker', 'image', 'inspect', item['Image']], maximum=65536))
    check(len(image) == 1 and image[0]['Id'] == item['Image'])
    check(any(x.split('@')[-1] == IMAGE.split('@')[-1] for x in image[0].get('RepoDigests', [])))


def collect():
    entries = {}
    snapshots = []
    started = utc()
    for kind, name, user, database in DATABASES:
        container_gate(name)
        snapshot_start = utc()
        remaining = LIMIT - sum(map(len, entries.values()))
        dump = command(['/usr/bin/docker', 'exec', name, 'pg_dump', '--format=custom',
                        '--no-password', '--username=' + user, '--dbname=' + database],
                       maximum=remaining)
        check(dump.startswith(b'PGDMP'))
        command(['/usr/bin/docker', 'exec', '-i', name, 'pg_restore', '--list'],
                maximum=1024 * 1024, data=dump)
        entries['databases/' + kind + '.dump'] = dump
        entries['roles/' + kind + '.sql'] = command(
            ['/usr/bin/docker', 'exec', name, 'pg_dumpall', '--roles-only', '--no-password',
             '--username=' + user, '--database=' + database],
            maximum=LIMIT - sum(map(len, entries.values())))
        check(entries['roles/' + kind + '.sql'])
        snapshots.append({'database': database, 'container': name, 'started_utc': snapshot_start,
                          'finished_utc': utc(), 'pg_restore_list': 'passed'})
        check(sum(map(len, entries.values())) <= LIMIT)
    app_uid = pwd.getpwnam('easel-central').pw_uid
    for path in REQUIRED:
        owner = app_uid if path == '/etc/easel-central/execution.json' else 0
        entries['configs' + path] = read_fixed(path, LIMIT - sum(map(len, entries.values())), owner=owner)
        check(sum(map(len, entries.values())) <= LIMIT)
    omitted = []
    for path in OPTIONAL:
        try:
            value = read_fixed(path, LIMIT - sum(map(len, entries.values())), private=False)
        except FileNotFoundError:
            omitted.append(path)
            continue
        entries['public' + path] = value
        check(sum(map(len, entries.values())) <= LIMIT)
    manifest = {'format': 'easel-encrypted-backup-v1', 'operation_id': OPERATION,
                'target': TARGET, 'started_utc': started, 'finished_utc': utc(),
                'consistency': 'Each pg_dump database snapshot is consistent individually; roles and files are collected separately. No cross-database atomicity or PITR.',
                'validation': 'Authenticated decrypt, all member lengths and SHA256, PGDMP signatures, pg_restore --list; no fresh restore.',
                'snapshots': snapshots, 'optional_missing': omitted,
                'files': [{'name': name, 'length': len(value), 'sha256': digest(value)}
                          for name, value in sorted(entries.items())]}
    return entries, manifest


def archive(entries, manifest):
    content = dict(entries, **{'manifest.json': canonical(manifest)})
    check(sum(len(value) + 1024 for value in content.values()) + 10240 <= LIMIT)
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w', format=tarfile.USTAR_FORMAT) as out:
        for name, value in sorted(content.items()):
            check(not name.startswith('/') and '..' not in Path(name).parts)
            item = tarfile.TarInfo(name)
            item.size, item.mode, item.uid, item.gid, item.mtime = len(value), 0o600, 0, 0, 0
            out.addfile(item, io.BytesIO(value))
    payload = stream.getvalue()
    check(len(payload) <= LIMIT)
    return payload


def verify_payload(payload, expected_manifest):
    check(len(payload) <= LIMIT)
    expected = {x['name']: x for x in expected_manifest['files']}
    with tarfile.open(fileobj=io.BytesIO(payload), mode='r:') as source:
        members = source.getmembers()
        check(len(members) == len(expected) + 1)
        check(len({x.name for x in members}) == len(members))
        check({x.name for x in members} == set(expected) | {'manifest.json'})
        for item in members:
            check(item.isfile() and 0 <= item.size <= LIMIT)
            value = source.extractfile(item).read(LIMIT + 1)
            if item.name == 'manifest.json':
                check(value == canonical(expected_manifest))
            else:
                check(len(value) == expected[item.name]['length'])
                check(digest(value) == expected[item.name]['sha256'])
                if item.name.startswith('databases/'):
                    check(value.startswith(b'PGDMP'))


def crypto():
    path = ROOT / 'sealed_transfer.py'
    source = read_fixed(path, 32768)
    check(digest(source) == HELPER_SHA256)
    # Compile the exact verified bytes instead of rereading a potentially changed file.
    import types
    module = types.ModuleType('easel_backup_sealed_transfer')
    exec(compile(source, str(path), 'exec'), module.__dict__)
    return module


def save(envelope, backup_id, verify):
    safe_parents(DEST)
    try:
        DEST.mkdir(mode=0o700)
    except FileExistsError:
        pass
    info = DEST.lstat()
    check(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700)
    directory = os.open(DEST, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    name = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + backup_id + '.sealed'
    fd = None
    try:
        fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=directory)
        remaining = memoryview(envelope)
        while remaining:
            written = os.write(fd, remaining)
            check(written > 0)
            remaining = remaining[written:]
        os.fsync(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        saved = bytearray()
        while len(saved) <= len(envelope):
            block = os.read(fd, min(65536, len(envelope) + 1 - len(saved)))
            if not block:
                break
            saved.extend(block)
        check(bytes(saved) == envelope)
        verify(bytes(saved))
        os.fsync(directory)
        return str(DEST / name)
    finally:
        if fd is not None:
            os.close(fd)
        os.close(directory)


def main():
    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    check(os.geteuid() == 0 and socket.gethostname() == TARGET)
    check(len(sys.argv) == 1)
    entries, manifest = collect()
    payload = archive(entries, manifest)
    public = read_fixed(ROOT / 'transfer-public.pem', 32768, private=False)
    private = read_fixed(ROOT / 'transfer-private.pem', 32768)
    helper = crypto()
    backup_id = str(uuid.uuid4())
    metadata = {'transfer_id': backup_id, 'kind': 'central', 'target': TARGET,
                'expected_sha256': digest(payload), 'expected_length': len(payload)}
    envelope = helper.seal(public, payload, metadata)
    def verify(value):
        result = helper.unseal(private, value, backup_id, 'central', TARGET)
        check(result == payload)
        verify_payload(result, manifest)
    verify(envelope)
    path = save(envelope, backup_id, verify)
    print(json.dumps({'status': 'selfverified', 'backup_id': backup_id,
                      'path': path, 'ciphertext_bytes': len(envelope),
                      'ciphertext_sha256': digest(envelope), 'payload_bytes': len(payload),
                      'payload_sha256': digest(payload), 'file_count': len(manifest['files']),
                      'manifest_sha256': digest(canonical(manifest)),
                      'fresh_restore': False}, sort_keys=True))


def entrypoint():
    try:
        main()
    except BaseException:
        # No traceback, command diagnostics, secret paths/content, or partial manifest.
        print('easel backup rejected', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    entrypoint()
