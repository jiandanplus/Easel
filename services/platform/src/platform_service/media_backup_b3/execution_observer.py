"""Fixed installed baseline DATA observer, never independent host authority.

Entrypoint: /opt/easel/bin/python -I -B -m
platform_service.media_backup_b3.execution_observer (no arguments).
No import-time IO. All native seams are private and receive one local deadline.
The host must reparse raw_network, raw status/stat and all recorded identities,
then bind them to its original target/worker and pinned executable manifest.

Closed response v1: format, before, after, raw_network. Each snapshot contains
own_pid, own_netns, pid1_netns, pid1_start_time, pid1_stat_raw, own_exe,
pid1_exe, own_status, pid1_status. Namespace records: device,inode. Executable
records: device,inode,mode,uid,gid,size,mtime_ns,ctime_ns,target (explicit fixed
proc symlink follow). Status records: raw,pid,uid,gid,groups,cap_inheritable,
cap_permitted,cap_effective,cap_bounding,cap_ambient,no_new_privs.
raw_network is the complete existing network_wire DATA encoding, without a
helper-selected security hash/pin. No returned data grants execution authority.
"""
import errno
import json
import os
import re
from select import select as _select
import stat
import sys
from time import monotonic_ns as _monotonic_ns

from .contracts import B3Error
from . import execution_contract as _contract
from .network_reader import observe_worker_network
from .network_wire import encode_network_observation

_REQUEST_FORMAT = 'easel.b3.execution-baseline-request.v1'
_RESPONSE_FORMAT = 'easel.b3.execution-baseline-response.v1'
_INPUT_CAP = 65536
_OUTPUT_CAP = 1048576
_PROC_CAP = 65536
_INITIAL_NS = 15_000_000_000
_MAX_REMAINING_NS = 60_000_000_000
_PROC_FILES = ('/proc/self/status', '/proc/1/status', '/proc/1/stat')
_NS_PATHS = ('/proc/self/ns/net', '/proc/1/ns/net')
_EXE_PATHS = ('/proc/self/exe', '/proc/1/exe')
_CAPS = (('CapInh', 'cap_inheritable'), ('CapPrm', 'cap_permitted'),
         ('CapEff', 'cap_effective'), ('CapBnd', 'cap_bounding'), ('CapAmb', 'cap_ambient'))


def _require(ok):
    if not ok:
        raise B3Error('B3 baseline observer rejected')


def _check(deadline):
    _require(type(deadline) is int and 0 < deadline < 2**63)
    remaining = deadline - _monotonic_ns()
    _require(0 < remaining <= _MAX_REMAINING_NS)


def _call(deadline, fn, *args, **kwargs):
    _check(deadline)
    result = fn(*args, **kwargs)
    _check(deadline)
    return result


def _nonblocking(fd, deadline):
    _call(deadline, os.set_blocking, fd, False)
    _require(_call(deadline, os.get_blocking, fd) is False)


def _retryable(exc):
    # Ambiguous progress is fatal; never resubmit bytes with unknown offsets.
    progress = getattr(exc, 'characters_written', 0)
    _require(type(progress) is int and progress == 0)
    if isinstance(exc, BlockingIOError):
        _require(exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK))


def _io(fd, data_or_size, writing, deadline):
    while True:
        _check(deadline)
        left = deadline - _monotonic_ns()
        _require(left > 0)
        try:
            ready = _select([] if writing else [fd], [fd] if writing else [], [], left/10**9)
            _check(deadline)
            _require(fd in ready[1 if writing else 0])
            value = os.write(fd, data_or_size) if writing else os.read(fd, data_or_size)
        except (InterruptedError, BlockingIOError) as exc:
            _retryable(exc)
            _check(deadline)
            continue
        except OSError:
            raise B3Error('B3 baseline observer rejected') from None
        _check(deadline)
        if writing:
            _require(type(value) is int and 0 < value <= len(data_or_size))
        else:
            _require(type(value) is bytes and len(value) <= data_or_size)
        return value


def _read_exact(size, deadline):
    result = bytearray()
    while len(result) < size:
        part = _io(0, min(16384, size-len(result)), False, deadline)
        _require(bool(part))
        result.extend(part)
    return bytes(result)


def _read_frame(deadline):
    _nonblocking(0, deadline)
    size = int.from_bytes(_read_exact(4, deadline), 'big')
    _require(0 < size <= _INPUT_CAP)  # Before body allocation/read.
    body = _read_exact(size, deadline)
    _require(_io(0, 1, False, deadline) == b'')  # Exact EOF shares deadline.
    return body


def _write_frame(payload, deadline):
    _require(type(payload) is bytes and 0 < len(payload) <= _OUTPUT_CAP)
    _nonblocking(1, deadline)
    data = len(payload).to_bytes(4, 'big') + payload
    offset = 0
    while offset < len(data):
        offset += _io(1, data[offset:offset+16384], True, deadline)
    _check(deadline)


def _validate_request(value):
    # Validate direct Python keys before equality, membership, or lookup.
    _require(type(value) is dict and all(type(k) is str for k in value))
    _require(value.keys() == {'format', 'remaining_ns'})
    _require(type(value['format']) is str and value['format'] == _REQUEST_FORMAT)
    remaining = value['remaining_ns']
    _require(type(remaining) is int and 0 < remaining <= _MAX_REMAINING_NS)
    return remaining


def _request(raw):
    try:
        _require(type(raw) is bytes and 0 < len(raw) <= _INPUT_CAP)
        value = json.loads(raw.decode('ascii'), object_pairs_hook=_contract._pairs,
                           parse_float=_contract._bad_number, parse_constant=_contract._bad_number)
        _contract._tree(value)
        remaining = _validate_request(value)
        _require(raw == _contract._canonical(value))
        return remaining
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise B3Error('B3 baseline observer rejected') from None


def _close(fd, deadline):
    # Still attempt cleanup if expired; any pre/post/close error prevents success.
    try:
        _check(deadline)
    finally:
        os.close(fd)
    _check(deadline)


def _proc_bytes(path, deadline):
    _require(type(path) is str and path in _PROC_FILES)
    _check(deadline)
    # Only fixed proc regular leaves; unlike ns/exe these are NOT symlink follows.
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        _check(deadline)
        _nonblocking(fd, deadline)
        before = _call(deadline, os.fstat, fd)
        _require(stat.S_ISREG(before.st_mode))
        result = bytearray()
        while True:
            part = _io(fd, min(16384, _PROC_CAP+1-len(result)), False, deadline)
            if not part:
                break
            result.extend(part)
            _require(len(result) <= _PROC_CAP)
        after = _call(deadline, os.fstat, fd)
        named = _call(deadline, os.stat, path, follow_symlinks=False)
        _require(_stamp(before) == _stamp(after) == _stamp(named))
        _ascii(bytes(result))
    finally:
        _close(fd, deadline)
    return bytes(result)


def _ascii(raw):
    _require(type(raw) is bytes and 0 < len(raw) <= _PROC_CAP)
    _require(all(x in (9, 10) or 32 <= x <= 126 for x in raw))
    return raw.decode('ascii')


def _uint(text, bits=32, positive=False):
    _require(type(text) is str and re.fullmatch(r'0|[1-9][0-9]*', text) is not None)
    _require(len(text) <= 20)
    value = int(text)
    _require(int(positive) <= value < 2**bits)
    return value


def _status(raw, pid):
    text = _ascii(raw)
    _require(text.endswith('\n'))
    fields = {}
    for line in text.splitlines():
        key, sep, value = line.partition(':')
        _require(bool(sep) and re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', key) is not None)
        _require(key not in fields)
        fields[key] = value.strip(' \t')
    needed = {'Pid', 'Uid', 'Gid', 'Groups', 'NoNewPrivs'} | {k for k, _ in _CAPS}
    _require(needed <= fields.keys())
    result = dict(raw=text, pid=_uint(fields['Pid'], positive=True))
    _require(result['pid'] == pid)
    for source, dest in (('Uid', 'uid'), ('Gid', 'gid'), ('Groups', 'groups')):
        tokens = fields[source].split()
        _require(len(tokens) == (1 if source == 'Groups' else 4))
        result[dest] = [_uint(token) for token in tokens]
        _require(result[dest] == ([10001] if source == 'Groups' else [10001]*4))
    for source, dest in _CAPS:
        _require(re.fullmatch(r'[0-9a-f]{16}', fields[source]) is not None)
        result[dest] = int(fields[source], 16)
        _require(result[dest] == 0)
    result['no_new_privs'] = _uint(fields['NoNewPrivs'])
    _require(result['no_new_privs'] == 1)
    return result


def _start(raw):
    text = _ascii(raw)
    # comm may itself contain spaces and parentheses; delimit by final ') '.
    match = re.fullmatch(r'1 \(([^\n]*)\) ([RSDZTtXxKWPI]) ([^\n]*)\n', text)
    _require(match is not None)
    numbers = match[3].split(' ')
    _require(len(numbers) == 49)  # fields 4..52, state is field 3.
    for number in numbers:
        _require(re.fullmatch(r'-?(?:0|[1-9][0-9]*)', number) is not None and len(number) <= 21)
        _require(-2**63 <= int(number) < 2**64)
    return _uint(numbers[18], 64, True)  # starttime is field 22.


def _stamp(s):
    return (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid,
            s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def _namespace(path, deadline):
    _require(type(path) is str and path in _NS_PATHS)
    # Intentional follow of this exact proc namespace symlink, never caller data.
    s = _call(deadline, os.stat, path, follow_symlinks=True)
    _require(type(s.st_dev) is int and 0 < s.st_dev < 2**64 and
             type(s.st_ino) is int and 0 < s.st_ino < 2**64)
    return dict(device=s.st_dev, inode=s.st_ino)


def _exe(path, deadline):
    _require(type(path) is str and path in _EXE_PATHS)
    # Fixed intentional exe follow, with stat/readlink/stat/readlink stability.
    before = _call(deadline, os.stat, path, follow_symlinks=True)
    target = _call(deadline, os.readlink, path)
    _require(type(target) is str and 0 < len(target) <= 4096 and target.isascii())
    _require(target.startswith('/') and all(32 < ord(c) < 127 for c in target) and
             not target.endswith(' (deleted)'))
    _require(stat.S_ISREG(before.st_mode) and before.st_mode & 0o111 != 0 and
             before.st_uid == 0 and before.st_gid == 0 and before.st_mode & 0o022 == 0)
    after = _call(deadline, os.stat, path, follow_symlinks=True)
    _require(_stamp(before) == _stamp(after) and target == _call(deadline, os.readlink, path))
    keys = ('device', 'inode', 'mode', 'uid', 'gid', 'size', 'mtime_ns', 'ctime_ns')
    result = dict(zip(keys, _stamp(before)))
    _require(all(type(v) is int and 0 <= v < 2**64 for v in result.values()))
    _require(result['device'] > 0 and result['inode'] > 0 and result['size'] > 0)
    result['target'] = target
    return result


def _snapshot(deadline):
    pid = _call(deadline, os.getpid)
    _require(type(pid) is int and 1 < pid < 2**32)
    own_ns = _namespace('/proc/self/ns/net', deadline)
    pid1_ns = _namespace('/proc/1/ns/net', deadline)
    _require(own_ns == pid1_ns)
    raw = _proc_bytes('/proc/1/stat', deadline)
    return dict(own_pid=pid, own_netns=own_ns, pid1_netns=pid1_ns,
                pid1_start_time=_start(raw), pid1_stat_raw=_ascii(raw),
                own_exe=_exe('/proc/self/exe', deadline), pid1_exe=_exe('/proc/1/exe', deadline),
                own_status=_status(_proc_bytes('/proc/self/status', deadline), pid),
                pid1_status=_status(_proc_bytes('/proc/1/status', deadline), 1))


def _observe(deadline):
    before = _snapshot(deadline)
    network = _call(deadline, observe_worker_network, deadline)
    after = _snapshot(deadline)
    # Raw stat/status contain normal CPU/memory/scheduling counter changes.
    def stable(snapshot):
        return {k: ({x: y for x, y in v.items() if x != 'raw'} if k.endswith('_status') else v)
                for k, v in snapshot.items() if k != 'pid1_stat_raw'}
    _require(stable(before) == stable(after))
    raw_network = _call(deadline, encode_network_observation, network)
    _require(before['own_netns'] == after['own_netns'] == before['pid1_netns'] == after['pid1_netns'] ==
             raw_network['namespace_before'] == raw_network['namespace_after'])
    _check(deadline)
    return dict(format=_RESPONSE_FORMAT, before=before, after=after, raw_network=raw_network)


def _invocation():
    _require(sys.argv[1:] == [] and sys.platform == 'linux' and
             sys.executable == '/opt/easel/bin/python' and not os.isatty(0))


def _diagnostic():
    # One bounded nonblocking best effort, no fresh deadline or raw exception.
    try:
        os.set_blocking(2, False)
        if os.get_blocking(2) is False:
            os.write(2, b'b3_baseline_observer_rejected\n')
    except BaseException:
        pass


def main():
    """Single fixed entrypoint; exit0 requires all output and close checks."""
    try:
        origin = _monotonic_ns()  # Before any input byte, never renewed.
        initial_deadline = origin + _INITIAL_NS
        _invocation()
        _nonblocking(2, initial_deadline)
        remaining = _request(_read_frame(initial_deadline))
        deadline = min(initial_deadline, origin+remaining)
        _check(deadline)
        _close(0, deadline)
        result = _observe(deadline)
        _check(deadline)
        raw = _contract._canonical(result)
        _check(deadline)
        _write_frame(raw, deadline)
        _close(1, deadline)
        _close(2, deadline)
        _check(deadline)
        return 0
    except BaseException:
        _diagnostic()
        return 1


if __name__ == '__main__':
    sys.exit(main())
