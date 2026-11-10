"""One fixed offline source job, with exclusive state and no deployment actions.

Bootstrap MUST verify this payload's manifest SHA before importing this module.
The checks below are a second gate, not a substitute for that outer bootstrap.
Timeouts preserve state; this operator never kills, stops, restarts or retries a
unit. Its read-only status command is the recovery path for uncertain observers.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import selectors
import socket
import stat
import subprocess
import sys
import time

from av_sources.archive_worker import RELEASE, _pinned_file
from av_sources.linux_sandbox import build_command, validate_runtime_evidence


class OperatorError(ValueError):
    pass


class RunUncertain(OperatorError):
    pass


PAYLOAD_FILES = tuple('code/av_sources/' + name for name in
                      ('safe_archive.py', 'linux_sandbox.py', 'archive_worker.py', 'remote_operator.py', 'mount_table.py'))
INPUT_ROOT = Path('/root/easel-av-rsa-orig-source-20261028')
_STAGE = re.compile(r'/root/easel-av-source-run-(\d{8})-([0-9a-f]{16})', re.ASCII)
_UNIT = re.compile(r'easel-av-src-([0-9a-f]{16})\.service', re.ASCII)
_SHA = re.compile(r'[0-9a-f]{64}', re.ASCII)
_ENV = {'PATH': '/usr/bin:/bin', 'LANG': 'C'}
_OUTPUT_LIMIT = 65536


def validate_identity(stage, unit):
    match = _STAGE.fullmatch(stage) if type(stage) is str else None
    other = _UNIT.fullmatch(unit) if type(unit) is str else None
    if not match or not other or match.group(2) != other.group(1):
        raise OperatorError('stage/unit identity rejected')
    try:
        datetime.datetime.strptime(match.group(1), '%Y%m%d')
    except ValueError as error:
        raise OperatorError('stage calendar date rejected') from error
    return Path(stage), unit


def _private_directory(path):
    try:
        info = Path(path).lstat()
    except OSError as error:
        raise OperatorError('private directory unavailable') from error
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise OperatorError('directory is not owned and private')


def _read_regular(path, limit):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as source:
            before = os.fstat(source.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                    or stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1
                    or before.st_size > limit):
                raise OperatorError('private regular file rejected')
            raw = source.read(limit + 1); after = os.fstat(source.fileno())
        if (len(raw) > limit or len(raw) != before.st_size or
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise OperatorError('private file changed during read')
        return raw
    except OSError as error:
        raise OperatorError('private file unavailable') from error


def _no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise OperatorError('duplicate JSON key')
        result[key] = value
    return result


def _json_load(raw):
    try:
        return json.loads(raw, object_pairs_hook=_no_duplicate_keys)
    except (ValueError, UnicodeError) as error:
        raise OperatorError('invalid bounded JSON') from error


def _exclusive_json(path, value):
    raw = (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()
    if len(raw) > _OUTPUT_LIMIT:
        raise OperatorError('operator receipt too large')
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as output:
            output.write(raw); output.flush(); os.fsync(output.fileno())
    except OSError as error:
        raise OperatorError('exclusive state already exists or unavailable') from error
    return hashlib.sha256(raw).hexdigest()


def verify_payload(stage, manifest_sha256):
    if type(manifest_sha256) is not str or not _SHA.fullmatch(manifest_sha256):
        raise OperatorError('exact outer manifest pin required')
    _private_directory(stage)
    for name in ('code', 'code/av_sources'):
        _private_directory(stage / name)
    raw = _read_regular(stage / 'payload-manifest.json', 16384)
    if hashlib.sha256(raw).hexdigest() != manifest_sha256:
        raise OperatorError('payload manifest SHA mismatch')
    value = _json_load(raw)
    if (type(value) is not dict or set(value) != {'schema_version', 'files'} or
            type(value['schema_version']) is not int or value['schema_version'] != 1 or
            type(value['files']) is not dict or set(value['files']) != set(PAYLOAD_FILES)):
        raise OperatorError('fixed payload member set required')
    actual = {str(path.relative_to(stage)) for path in (stage / 'code').rglob('*')}
    if actual != set(PAYLOAD_FILES) | {'code/av_sources'}:
        raise OperatorError('unexpected code member')
    for name, pin in value['files'].items():
        if (type(pin) is not dict or set(pin) != {'size', 'sha256'} or
                type(pin['size']) is not int or not 0 < pin['size'] <= 1024 * 1024 or
                type(pin['sha256']) is not str or not _SHA.fullmatch(pin['sha256'])):
            raise OperatorError('invalid payload pin')
        data = _read_regular(stage / name, 1024 * 1024)
        if len(data) != pin['size'] or hashlib.sha256(data).hexdigest() != pin['sha256']:
            raise OperatorError('payload file SHA mismatch')
    return value


def _pipe_output(process, *, timeout, limit, outputs=None):
    """Bound aggregate bytes before storing them, independent of writer limits."""
    deadline = time.monotonic() + timeout; data = {'stdout': bytearray(), 'stderr': bytearray()}
    total = 0
    try:
        with selectors.DefaultSelector() as selection:
            for name in data:
                stream = getattr(process, name); os.set_blocking(stream.fileno(), False)
                selection.register(stream, selectors.EVENT_READ, name)
            while selection.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RunUncertain('observer timed out; use status, never retry run')
                for key, _ in selection.select(min(remaining, 0.2)):
                    piece = os.read(key.fileobj.fileno(), min(4096, limit - total))
                    if not piece:
                        selection.unregister(key.fileobj); continue
                    total += len(piece)
                    if outputs is None:
                        data[key.data].extend(piece)
                    else:
                        outputs[key.data].write(piece)
                    if total >= limit:
                        raise RunUncertain('observer reached output limit; use status, never retry run')
            try:
                code = process.wait(timeout=max(0.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired as error:
                raise RunUncertain('observer timed out; use status, never retry run') from error
    finally:
        process.stdout.close(); process.stderr.close()
        # Only the direct Popen child is ours to reap. Never signal a unit,
        # process group, MainPID, or any PID recovered from saved state.
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired as error:
            raise RunUncertain('owned observer could not be reaped') from error
    return code, bytes(data['stdout']), bytes(data['stderr'])


def _query(argv, *, timeout=5, limit=16384):
    """Read fixed system commands through bounded pipes; no shell or files."""
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, env=_ENV)
    return _pipe_output(process, timeout=timeout, limit=limit)


def host_preflight(*, require_resources=True):
    if (sys.platform != 'linux' or os.getuid() != 0 or os.geteuid() != 0 or
            socket.gethostname() != 'ser362540173134' or sys.version_info[:2] != (3, 12)):
        raise OperatorError('fixed Linux host/Python identity required')
    code, output, _ = _query(['/usr/bin/systemctl', '--version'], limit=4096)
    if code != 0 or not output.splitlines() or not re.match(rb'systemd 255 \(255\.4(?:[ -]|\))', output.splitlines()[0]):
        raise OperatorError('systemd 255.4 required')
    links = {}
    for name, target in [('lib', 'usr/lib'), ('lib64', 'usr/lib64'), ('bin', 'usr/bin')]:
        path = Path('/') / name
        if not path.exists() and not path.is_symlink():
            if name == 'lib': raise OperatorError('host lib layout missing')
            continue
        info = path.lstat()
        if not stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or os.readlink(path) != target:
            raise OperatorError('host library link layout is unsupported')
        links[name] = target
    for path in ['/usr/bin', '/usr/lib', '/sys/fs/cgroup']:
        info = Path(path).lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise OperatorError('public host directory rejected')
    for path in ['/usr/bin/python3', '/usr/bin/gpgv', '/usr/bin/systemd-run', '/usr/bin/systemctl']:
        if not Path(path).is_file() or not os.access(path, os.X_OK):
            raise OperatorError('fixed system tool missing')
    if require_resources:
        usage = os.statvfs('/root')
        if usage.f_bavail * usage.f_frsize < 1024 ** 3:
            raise OperatorError('insufficient free disk for bounded source job')
        with open('/proc/meminfo', 'rb') as source:
            memory = source.read(65537)
        match = re.search(rb'^MemAvailable:\s+(\d+) kB$', memory, re.MULTILINE)
        if len(memory) > 65536 or not match or int(match.group(1)) * 1024 < 768 * 1024 ** 2:
            raise OperatorError('insufficient available memory')
    return {'links': links, 'host_netns_ino': os.stat('/proc/self/ns/net').st_ino,
            'hostname': 'ser362540173134', 'systemd': '255.4', 'python_minor': '3.12'}


def verify_public_inputs():
    sources = [('ffmpeg_9.0.2.orig.tar.xz', RELEASE.archive_size, RELEASE.archive_sha256),
               ('ffmpeg_9.0.2.orig.tar.xz.asc', RELEASE.signature_size, RELEASE.signature_sha256),
               ('official-rsa-public-key.asc', RELEASE.key_size, RELEASE.key_sha256)]
    try:
        return {name: _pinned_file(INPUT_ROOT / name, size, sha) for name, size, sha in sources}
    except ValueError as error:
        raise OperatorError('fixed public source pin rejected') from error


def prepare_layout(stage, links):
    allowed = {'lib': 'usr/lib', 'lib64': 'usr/lib64', 'bin': 'usr/bin'}
    if (type(links) is not dict or 'lib' not in links or
            any(name not in allowed or allowed[name] != target for name, target in links.items())):
        raise OperatorError('unsupported explicit root library links')
    _private_directory(stage)
    try:
        jail = stage / 'jail'; jail.mkdir(mode=0o700)
        (stage / 'work').mkdir(mode=0o700)
        for name in ['usr', 'usr/bin', 'usr/lib', 'usr/lib64', 'code', 'inputs', 'work',
                     'tmp', 'var', 'var/tmp', 'dev', 'proc', 'sys', 'sys/fs', 'sys/fs/cgroup']:
            (jail / name).mkdir(mode=0o700)
        for name, target in links.items():
            (jail / name).symlink_to(target)
        for name in ['archive.xz', 'archive.asc', 'release-key.asc']:
            fd = os.open(jail / 'inputs' / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
    except OSError as error:
        raise OperatorError('exclusive root layout already exists or is unavailable') from error


def show_unit(unit):
    if type(unit) is not str or not _UNIT.fullmatch(unit):
        raise OperatorError('unit query name rejected')
    properties = ['Id', 'LoadState', 'Transient', 'RootDirectory', 'InvocationID',
                  'ActiveState', 'SubState', 'MainPID', 'ControlPID']
    command = ['/usr/bin/systemctl', 'show', '--no-pager', '--property=' + ','.join(properties), unit]
    code, out, _ = _query(command)
    try:
        result = {}
        for line in out.decode('utf-8', errors='strict').splitlines():
            key, value = line.split('=', 1)
            if key not in properties or key in result: raise OperatorError('invalid unit observation')
            result[key] = value
    except (UnicodeError, ValueError) as error:
        raise OperatorError('malformed unit observation') from error
    if result.get('LoadState') == 'not-found' and code in (0, 1, 4):
        return {'LoadState': 'not-found'}
    if code != 0 or set(result) != set(properties):
        raise OperatorError('incomplete unit observation')
    return result


def validate_owned_unit(facts, stage, unit, *, invocation=None):
    if (type(facts) is not dict or facts.get('Id') != unit or facts.get('LoadState') != 'loaded' or
            facts.get('Transient') != 'yes' or facts.get('RootDirectory') != str(stage / 'jail')):
        raise OperatorError('unit ownership cannot be proven')
    value = facts.get('InvocationID')
    if (type(value) is not str or not re.fullmatch('[0-9a-f]{32}', value) or value == '0' * 32
            or invocation is not None and value != invocation):
        raise OperatorError('unit invocation identity cannot be proven')
    return value


def output_limits():
    resource.setrlimit(resource.RLIMIT_FSIZE, (_OUTPUT_LIMIT, _OUTPUT_LIMIT))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def capture_command(argv, base, *, timeout):
    """One observer; forward at most 64 KiB total into exclusive private logs."""
    handles = []
    try:
        for suffix in ('.stdout', '.stderr'):
            fd = os.open(Path(base).with_suffix(suffix), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            handles.append(os.fdopen(fd, 'wb'))
        with handles[0] as output, handles[1] as error:
            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       env=_ENV, preexec_fn=output_limits)
            code, _, _ = _pipe_output(process, timeout=timeout, limit=_OUTPUT_LIMIT,
                                      outputs={'stdout': output, 'stderr': error})
        result = {'exit_code': code}
        for name in ('stdout', 'stderr'):
            result[name] = _read_regular(Path(base).with_suffix('.' + name), _OUTPUT_LIMIT)
        return result
    except OSError as error:
        raise OperatorError('exclusive capture failed') from error
    finally:
        for handle in handles:
            if not handle.closed: handle.close()


def _terminal(facts, stage, unit):
    if facts.get('LoadState') != 'not-found':
        validate_owned_unit(facts, stage, unit)
        if facts.get('ActiveState') not in ('inactive', 'failed') or facts.get('MainPID') != '0' or facts.get('ControlPID') != '0':
            raise RunUncertain('unit still active or terminal state uncertain')
    path = Path('/sys/fs/cgroup/system.slice') / unit / 'cgroup.procs'
    if path.exists():
        with path.open('rb') as source: raw = source.read(4097)
        if len(raw) > 4096 or raw.strip():
            raise RunUncertain('unit cgroup still has processes')


def read_worker_result(stage, expected):
    path = stage / 'work/result'
    _private_directory(path)
    summary = _json_load(_read_regular(path / 'archive.safe.json', _OUTPUT_LIMIT))
    if type(summary) is not dict or summary.get('status') != 'verified_release_archive_materialized':
        raise OperatorError('worker terminal receipt rejected')
    for name, wanted in {'archive_materialized': True, 'source_members_executed': False,
                         'build_inputs_adopted': False, 'build_executed': False, 'production_changed': False}.items():
        if type(summary.get(name)) is not bool or summary[name] is not wanted:
            raise OperatorError('worker receipt scope flags rejected')
    for name, value in {'archive_size': RELEASE.archive_size, 'archive_sha256': RELEASE.archive_sha256,
                        'signature_sha256': RELEASE.signature_sha256, 'public_key_sha256': RELEASE.key_sha256,
                        'file_count': RELEASE.file_count, 'directory_count': RELEASE.directory_count,
                        'content_bytes': RELEASE.content_bytes}.items():
        if type(summary.get(name)) is not type(value) or summary[name] != value:
            raise OperatorError('worker release result mismatch')
    signature = summary.get('signature')
    if (type(signature) is not dict or signature.get('signature_verified') is not True or
            signature.get('fingerprint') != RELEASE.fingerprint or signature.get('primary_fingerprint') != RELEASE.fingerprint):
        raise OperatorError('worker signature receipt rejected')
    validate_runtime_evidence(summary.get('runtime'), **expected)
    manifest = _read_regular(path / 'source-manifest.json', 16 * 1024 * 1024)
    if hashlib.sha256(manifest).hexdigest() != summary.get('source_manifest_sha256'):
        raise OperatorError('source manifest SHA mismatch')
    return summary


def run(stage, unit, manifest_sha256):
    stage, unit = validate_identity(stage, unit)
    host = host_preflight()  # Reject wrong host before any stage access/write.
    verify_payload(stage, manifest_sha256)
    if any((stage / name).exists() or (stage / name).is_symlink() for name in ('run-intent.json', 'jail', 'work', 'operator.safe.json')):
        raise OperatorError('run already started or partial state retained; use status')
    pins = verify_public_inputs()
    if show_unit(unit).get('LoadState') != 'not-found':
        raise OperatorError('unit already exists; cannot launch or adopt')
    prepare_layout(stage, host['links'])
    info = (stage / 'jail').stat()
    expected = {'unit_name': unit, 'root_dev': info.st_dev, 'root_ino': info.st_ino,
                'host_netns_ino': host['host_netns_ino']}
    command = build_command(str(stage), unit, **{k: v for k, v in expected.items() if k != 'unit_name'})
    intent = {'schema_version': 1, 'unit': unit, 'stage': str(stage), 'manifest_sha256': manifest_sha256,
              'expected': expected, 'public_inputs': pins, 'host': host,
              'created_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'automatic_retry': False}
    _exclusive_json(stage / 'run-intent.json', intent)
    observed = capture_command(command, stage / 'systemd-run', timeout=140)
    _terminal(show_unit(unit), stage, unit)
    if observed['exit_code'] != 0:
        raise OperatorError('source unit failed; retained state must be inspected')
    result = read_worker_result(stage, expected)
    if _json_load(observed['stdout']) != result:
        raise OperatorError('observer output and worker result differ')
    if verify_public_inputs() != pins:
        raise OperatorError('public source changed during operation')
    verify_payload(stage, manifest_sha256)
    receipt = {'schema_version': 1, 'status': 'archive_materialized', 'unit': unit,
               'manifest_sha256': manifest_sha256, 'source_manifest_sha256': result['source_manifest_sha256'],
               'worker_result_sha256': hashlib.sha256(_read_regular(stage / 'work/result/archive.safe.json', _OUTPUT_LIMIT)).hexdigest(),
               'terminal_no_processes': True, 'archive_materialized': True,
               'build_inputs_adopted': False, 'build_executed': False, 'production_changed': False,
               'automatic_retry': False}
    _exclusive_json(stage / 'operator.safe.json', receipt)
    return receipt


def status(stage, unit, manifest_sha256):
    stage, unit = validate_identity(stage, unit); host_preflight(require_resources=False)
    verify_payload(stage, manifest_sha256)
    intent = _json_load(_read_regular(stage / 'run-intent.json', 16384))
    if (type(intent) is not dict or intent.get('unit') != unit or intent.get('stage') != str(stage) or
            intent.get('manifest_sha256') != manifest_sha256):
        raise OperatorError('existing intent identity mismatch')
    facts = show_unit(unit)
    if facts.get('LoadState') != 'not-found':
        validate_owned_unit(facts, stage, unit)
    complete = False
    if (stage / 'operator.safe.json').exists():
        value = _json_load(_read_regular(stage / 'operator.safe.json', _OUTPUT_LIMIT))
        complete = (value.get('unit') == unit and value.get('manifest_sha256') == manifest_sha256
                    and value.get('status') == 'archive_materialized')
    return {'schema_version': 1, 'status': 'read_only_observation', 'unit': unit,
            'load_state': facts['LoadState'], 'active_state': facts.get('ActiveState'),
            'operator_success_receipt_present': complete, 'automatic_retry': False,
            'build_executed': False, 'production_changed': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Fixed offline Linux source operator')
    parser.add_argument('mode', choices=('run', 'status'))
    parser.add_argument('--stage', required=True)
    parser.add_argument('--unit', required=True)
    parser.add_argument('--manifest-sha256', required=True)
    args = parser.parse_args(argv)
    try:
        result = (run if args.mode == 'run' else status)(args.stage, args.unit, args.manifest_sha256)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({'schema_version': 1, 'status': 'uncertain' if isinstance(error, RunUncertain) else 'failed',
                          'failure_kind': type(error).__name__, 'automatic_retry': False,
                          'build_executed': False, 'production_changed': False}))
        return 1
    print(json.dumps(result, sort_keys=True)); return 0


if __name__ == '__main__':
    raise SystemExit(main())
