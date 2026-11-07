"""Fixed-release offline worker. Never compiles or executes archive members."""
import argparse
import base64
from dataclasses import dataclass
import datetime
import hashlib
import json
import os
from pathlib import Path
import resource
import stat
import subprocess
import tempfile
import time

from av_sources.safe_archive import (ArchiveError, ArchiveLimits, _source_fd,
                                    extract_archive, inspect_archive)


class WorkerError(ValueError):
    pass


@dataclass(frozen=True)
class ReleaseSpec:
    prefix: str = 'ffmpeg-9.0.2'
    version: bytes = b'9.0.2\n'
    archive_size: int = 12040788
    archive_sha256: str = '8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e'
    signature_size: int = 520
    signature_sha256: str = 'd617fd94ea354dadd2a8bb16243d37c44e1e9729b06b6ad58fe30bfb0fa943f2'
    key_size: int = 1709
    key_sha256: str = '397b3becedcd5a98769967ff1ff8501ddc89f8368b8f766e4701377d7dbaabe5'
    fingerprint: str = 'FCF986EA15E6E293A5644F10B4322F04D67658D8'
    file_count: int = 10399
    directory_count: int = 126
    content_bytes: int = 90358343


RELEASE = ReleaseSpec()


def _pinned_file(path, size, digest, *, read=False):
    try:
        fd = _source_fd(path)
        with os.fdopen(fd, 'rb') as source:
            before = os.fstat(source.fileno())
            if before.st_size != size:
                raise WorkerError('public input size mismatch')
            h = hashlib.sha256(); count = 0; pieces = []
            while True:
                piece = source.read(min(65536, size - count + 1))
                if not piece:
                    break
                count += len(piece)
                if count > size:
                    raise WorkerError('public input grew')
                h.update(piece)
                if read:
                    pieces.append(piece)
            after = os.fstat(source.fileno())
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise WorkerError('public input identity changed')
        if count != size or h.hexdigest() != digest:
            raise WorkerError('public input SHA mismatch')
        return b''.join(pieces) if read else h.hexdigest()
    except (OSError, ArchiveError) as error:
        raise WorkerError('public input rejected') from error


def verify_pinned_inputs(inputs, spec):
    inputs = Path(inputs)
    archive = _pinned_file(inputs / 'archive.xz', spec.archive_size, spec.archive_sha256)
    _pinned_file(inputs / 'archive.asc', spec.signature_size, spec.signature_sha256)
    _pinned_file(inputs / 'release-key.asc', spec.key_size, spec.key_sha256)
    return {'archive_size': spec.archive_size, 'archive_sha256': archive,
            'signature_sha256': spec.signature_sha256, 'public_key_sha256': spec.key_sha256}


def check_signature_status(status, exit_code, *, now=None):
    """Accept only the fixed RSA release signature's complete status result."""
    now = time.time() if now is None else now
    if type(exit_code) is not int or exit_code != 0 or not isinstance(status, bytes) or len(status) > 16384:
        raise WorkerError('signature verification failed')
    try:
        lines = status.decode('ascii').splitlines()
    except UnicodeError:
        raise WorkerError('invalid signature status') from None
    allowed = {'NEWSIG', 'KEY_CONSIDERED', 'SIG_ID', 'GOODSIG', 'VALIDSIG'}
    records = []
    for line in lines:
        fields = line.split()
        if len(fields) < 2 or fields[0] != '[GNUPG:]' or fields[1] not in allowed:
            raise WorkerError('signature rejection status')
        records.append(fields)
    valid = [r for r in records if r[1] == 'VALIDSIG']
    if len(valid) != 1 or len(valid[0]) != 12:
        raise WorkerError('missing or ambiguous release signature')
    value = valid[0]
    if value[2] != RELEASE.fingerprint or value[11] != RELEASE.fingerprint:
        raise WorkerError('wrong release signer')
    try:
        timestamp, expiry = int(value[4]), int(value[5])
        date = datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc).date().isoformat()
    except (ValueError, OverflowError, OSError):
        raise WorkerError('invalid signature timestamp') from None
    if timestamp <= 0 or timestamp > now + 300 or date != value[3] or (expiry and expiry <= now):
        raise WorkerError('expired or invalid release signature time')
    if value[6:11] != ['4', '0', '1', '10', '00']:
        raise WorkerError('unexpected release signature algorithms')
    return {'signature_verified': True, 'fingerprint': value[2],
            'primary_fingerprint': value[11], 'signature_timestamp': timestamp}


def _private_directory(path):
    info = Path(path).lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise WorkerError('worker directory must be owned and private')


def _exclusive(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as output:
        output.write(data)


def _json(path, value):
    data = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()
    if len(data) > 16 * 1024 * 1024:
        raise WorkerError('worker receipt too large')
    _exclusive(path, data)
    return hashlib.sha256(data).hexdigest()


def _decode_public_key(armor):
    try:
        lines = armor.decode('ascii').splitlines()
        if lines[0] != '-----BEGIN PGP PUBLIC KEY BLOCK-----' or lines[-1] != '-----END PGP PUBLIC KEY BLOCK-----':
            raise WorkerError('invalid release public key armor')
        start = lines.index('') + 1
        encoded = ''.join(line for line in lines[start:-1] if line and not line.startswith('='))
        key = base64.b64decode(encoded, validate=True)
        if not key or len(key) > 8192:
            raise WorkerError('invalid release public key size')
        return key
    except (UnicodeError, ValueError, IndexError) as error:
        raise WorkerError('release public key decode failed') from error


def _gpg_output_limits():
    # Each output file is kernel-limited before exec; no unbounded pipe capture.
    resource.setrlimit(resource.RLIMIT_FSIZE, (16384, 16384))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def verify_signature(inputs, result, spec):
    armor = _pinned_file(inputs / 'release-key.asc', spec.key_size, spec.key_sha256, read=True)
    home = result / 'gpg'; home.mkdir(mode=0o700)
    ring = result / 'release-key.gpg'; _exclusive(ring, _decode_public_key(armor))
    stdout = result / 'gpgv.stdout'; stderr = result / 'gpgv.stderr'
    out_fd = os.open(stdout, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        err_fd = os.open(stderr, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except BaseException:
        os.close(out_fd); raise
    with os.fdopen(out_fd, 'wb') as out, os.fdopen(err_fd, 'wb') as err:
        command = ['/usr/bin/gpgv', '--homedir', str(home), '--keyring', str(ring),
                   '--status-fd', '1', str(inputs / 'archive.asc'), str(inputs / 'archive.xz')]
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                   env={'PATH': '/usr/bin:/bin', 'LANG': 'C'}, cwd=home,
                                   preexec_fn=_gpg_output_limits)
        try:
            code = process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                raise WorkerError('signature verifier did not stop') from None
            raise WorkerError('signature verifier timed out') from None
    if stdout.stat().st_size > 16384 or stderr.stat().st_size > 16384:
        raise WorkerError('signature output limit exceeded')
    return check_signature_status(stdout.read_bytes(), code)


def verify_sandbox(expected):
    from av_sources.linux_sandbox import collect_runtime_evidence, validate_runtime_evidence
    return validate_runtime_evidence(collect_runtime_evidence(), **expected)


def _validate_manifest(manifest, spec):
    entries = manifest.get('entries', [])
    if (manifest.get('file_count') != spec.file_count or manifest.get('directory_count') != spec.directory_count
            or sum(e['size'] for e in entries if e['type'] == 'file') != spec.content_bytes):
        raise WorkerError('release member counts mismatch')
    if any(e['path'] != spec.prefix and not e['path'].startswith(spec.prefix + '/') for e in entries):
        raise WorkerError('release root mismatch')
    return entries


def _verify_tree(source, entries, spec):
    wanted = set()
    for entry in entries:
        relative = Path(entry['path']); wanted.add(str(relative))
        wanted.update(str(p) for p in relative.parents if str(p) != '.')
        path = source / relative; info = path.lstat()
        if entry['type'] == 'directory':
            _private_directory(path)
        elif (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
              or info.st_uid != os.geteuid()):
            raise WorkerError('materialized file type or mode mismatch')
        else:
            _pinned_file(path, entry['size'], entry['sha256'])
    actual = {str(p.relative_to(source)) for p in source.rglob('*')}
    if actual != wanted:
        raise WorkerError('materialized member set mismatch')
    version = source / spec.prefix / 'VERSION'
    if version.read_bytes() != spec.version:
        raise WorkerError('release version mismatch')


def run_worker(expected, *, inputs=Path('/inputs'), work=Path('/work')):
    runtime = verify_sandbox(expected)  # Must precede any source or output access.
    inputs, work = Path(inputs), Path(work)
    _private_directory(work)
    spec = RELEASE
    pins = verify_pinned_inputs(inputs, spec)
    result = work / 'result'
    try:
        result.mkdir(mode=0o700)
    except OSError as error:
        raise WorkerError('worker result already exists or is unavailable') from error
    _private_directory(result)
    signature = verify_signature(inputs, result, spec)
    if verify_pinned_inputs(inputs, spec) != pins:
        raise WorkerError('public inputs changed during signature verification')
    spool = result / 'spool'; spool.mkdir(mode=0o700)
    previous_tempdir = tempfile.tempdir
    tempfile.tempdir = str(spool)
    try:
        limits = ArchiveLimits()
        arguments = {'archive_size': spec.archive_size, 'sha256': spec.archive_sha256, 'limits': limits}
        manifest = inspect_archive(inputs / 'archive.xz', **arguments)
        entries = _validate_manifest(manifest, spec)
        extracted = extract_archive(inputs / 'archive.xz', result / 'source', **arguments)
        if extracted != manifest:
            raise WorkerError('archive changed between check and materialization')
        _verify_tree(result / 'source', entries, spec)
    except (ArchiveError, OSError) as error:
        raise WorkerError('archive check or materialization failed') from error
    finally:
        tempfile.tempdir = previous_tempdir
    if verify_pinned_inputs(inputs, spec) != pins:
        raise WorkerError('public inputs changed during materialization')
    manifest_sha = _json(result / 'source-manifest.json', manifest)
    summary = {'schema_version': 1, 'status': 'verified_release_archive_materialized',
               **pins, 'signature': signature, 'runtime': runtime,
               'file_count': manifest['file_count'], 'directory_count': manifest['directory_count'],
               'content_bytes': spec.content_bytes, 'source_manifest_sha256': manifest_sha,
               'archive_materialized': True, 'source_members_executed': False,
               'build_inputs_adopted': False, 'build_executed': False, 'production_changed': False}
    _json(result / 'archive.safe.json', summary)
    return summary


def _failure_receipt(error):
    """Return bounded public failure facts, never exception text or class names."""
    from av_sources import linux_sandbox

    kind = 'ValueError'
    if type(error) is linux_sandbox.SandboxError:
        kind = 'SandboxError'
    elif type(error) is WorkerError:
        kind = 'WorkerError'
    elif type(error) is ArchiveError:
        kind = 'ArchiveError'
    elif isinstance(error, OSError):
        kind = 'OSError'
    elif isinstance(error, subprocess.SubprocessError):
        kind = 'SubprocessError'
    receipt = {'schema_version': 1, 'status': 'failed', 'failure_kind': kind,
               'build_inputs_adopted': False, 'production_changed': False}
    if type(error) is linux_sandbox.SandboxError:
        try:
            diagnostic = linux_sandbox.sandbox_failure_diagnostic(error)
            if diagnostic is not None:
                candidate = dict(receipt, sandbox_diagnostic=diagnostic)
                # The helper enforces its gate-specific schema; this final
                # bound covers the complete wire record plus its newline.
                raw = json.dumps(candidate, ensure_ascii=True, sort_keys=True)
                if len(raw.encode('ascii')) + 1 <= 4096:
                    return candidate
        except Exception:
            # Diagnostics must not replace the original fail-closed result.
            pass
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description='Verify a fixed AV source in the prepared Linux sandbox')
    parser.add_argument('--expected-unit-name', required=True)
    parser.add_argument('--expected-root-dev', type=int, required=True)
    parser.add_argument('--expected-root-ino', type=int, required=True)
    parser.add_argument('--expected-host-netns-ino', type=int, required=True)
    args = parser.parse_args(argv)
    expected = {'unit_name': args.expected_unit_name, 'root_dev': args.expected_root_dev,
                'root_ino': args.expected_root_ino, 'host_netns_ino': args.expected_host_netns_ino}
    try:
        value = run_worker(expected)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps(_failure_receipt(error), ensure_ascii=True, sort_keys=True))
        return 1
    print(json.dumps(value))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
