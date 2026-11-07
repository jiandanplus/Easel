"""Source-only observer regressions. All native IO is replaced with private fakes."""
import copy
import errno
import importlib
import json
from pathlib import Path
import stat
import sys
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1] / 'src' / 'platform_service'
for name, path in (('platform_service', ROOT), ('platform_service.media_backup_b3', ROOT / 'media_backup_b3')):
    if name not in sys.modules:
        package = types.ModuleType(name)
        package.__path__ = [str(path)]
        sys.modules[name] = package
from test_media_backup_b3_network_fixtures import observation, wire_fixture

NAME = 'platform_service.media_backup_b3.execution_observer'
SOURCE = ROOT / 'media_backup_b3' / 'execution_observer.py'
NS = dict(device=4, inode=5)
STATUS = ('Name:\tpython\nPid:\t8\nUid:\t10001 10001 10001 10001\n'
          'Gid:\t10001 10001 10001 10001\nGroups:\t10001\n'
          'CapInh:\t0000000000000000\nCapPrm:\t0000000000000000\n'
          'CapEff:\t0000000000000000\nCapBnd:\t0000000000000000\n'
          'CapAmb:\t0000000000000000\nNoNewPrivs:\t1\n')
STAT = '1 (python (main)) S ' + ' '.join(['0'] * 18 + ['123'] + ['0'] * 30) + '\n'


def payload(remaining=6_000_000_000):
    return json.dumps(dict(format='easel.b3.execution-baseline-request.v1', remaining_ns=remaining),
                      sort_keys=True, separators=(',', ':')).encode()


def frame(raw):
    return len(raw).to_bytes(4, 'big') + raw


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SOURCE.is_file(), 'fixed installed baseline observer is missing')
        self.o = importlib.import_module(NAME)
        self.now = 1_000_000_000
        self.patches = []
        self.patch(self.o, '_monotonic_ns', side_effect=lambda: self.now)
        # Copy os so no patch leaks to unittest/pathlib or existing N4 fixture code.
        self.patch(self.o, 'os', types.SimpleNamespace(**vars(self.o.os)))
        for name in ('open', 'read', 'write', 'close', 'stat', 'fstat', 'readlink', 'getpid',
                     'set_blocking', 'get_blocking', 'isatty'):
            self.patch(self.o.os, name, side_effect=AssertionError('unpatched native seam: ' + name))
        self.patch(self.o.os, 'set_blocking', return_value=None)
        self.patch(self.o.os, 'get_blocking', return_value=False)
        self.patch(self.o, '_select', return_value=([0, 9], [1], []))

    def patch(self, obj, name, *args, **kw):
        p = mock.patch.object(obj, name, *args, **kw)
        value = p.start()
        self.addCleanup(p.stop)
        return value

    def reject(self, fn, *args):
        with self.assertRaises(self.o.B3Error):
            fn(*args)

    def io(self, data, read_steps=(), write_steps=()):
        incoming = bytearray(data)
        outgoing = bytearray()
        reads, writes = list(read_steps), list(write_steps)
        calls = []
        def read(fd, n):
            calls.append(n)
            step = reads.pop(0) if reads else n
            if isinstance(step, BaseException):
                raise step
            size = min(n, step, len(incoming))
            result = bytes(incoming[:size]); del incoming[:size]
            return result
        def write(fd, data):
            step = writes.pop(0) if writes else len(data)
            if isinstance(step, BaseException):
                raise step
            outgoing.extend(data[:step])
            return step
        self.patch(self.o.os, 'read', side_effect=read)
        self.patch(self.o.os, 'write', side_effect=write)
        return incoming, outgoing, calls

    def proc(self):
        o = self.o
        ns = observation().namespace_before
        self.patch(o.os, 'getpid', return_value=8)
        self.patch(o.os, 'stat', side_effect=lambda path, **kw: types.SimpleNamespace(
            st_dev=ns.device if '/ns/' in path else 2,
            st_ino=ns.inode if '/ns/' in path else 3,
            st_mode=stat.S_IFREG | 0o755, st_uid=0, st_gid=0,
            st_size=100, st_mtime_ns=1, st_ctime_ns=1))
        self.patch(o.os, 'readlink', return_value='/usr/local/bin/python3.13')
        data = {'/proc/self/status': STATUS.encode(), '/proc/1/status': STATUS.replace('Pid:\t8', 'Pid:\t1').encode(),
                '/proc/1/stat': STAT.encode()}
        self.patch(o, '_proc_bytes', side_effect=lambda path, deadline: data[path])
        return data

    def test_fixed_positive_main_preserves_full_network_and_both_snapshots(self):
        o = self.o
        self.proc()
        _, out, _ = self.io(frame(payload()))
        self.patch(o, '_invocation')
        self.patch(o.os, 'close', return_value=None)
        n4 = self.patch(o, 'observe_worker_network', return_value=observation())
        self.assertEqual(o.main(), 0)
        n4.assert_called_once_with(7_000_000_000)
        result = json.loads(out[4:])
        self.assertEqual(int.from_bytes(out[:4], 'big'), len(out)-4)
        self.assertLessEqual(len(out), 1048576+4)
        self.assertEqual(set(result), {'format', 'before', 'after', 'raw_network'})
        self.assertEqual(result['raw_network'], wire_fixture())
        self.assertEqual(result['before'], result['after'])
        self.assertEqual(result['before']['pid1_start_time'], 123)
        self.assertEqual(result['before']['own_pid'], 8)

    def test_request_closed_strict_types_and_json(self):
        self.assertEqual(self.o._request(payload()), 6_000_000_000)
        for value in (True, 0, -1, 60_000_000_001, 1.5, None, [], {}):
            self.reject(self.o._request, payload(value))
        for raw in (b'{}', payload()[:-1]+b',"extra":0}',
                    payload()[:-1]+b',"remaining_ns":1}',
                    b'{"format":NaN}', b'{"format":Infinity}',
                    b'['*100+b']'*100, b'{"remaining_ns":1.0}'):
            self.reject(self.o._request, raw)

    def test_direct_hostile_keys_never_invoke_callbacks(self):
        class Hostile(str):
            def __eq__(self, other): raise AssertionError('equality invoked')
            __hash__ = str.__hash__
        self.reject(self.o._validate_request, {Hostile('format'): 'x', 'remaining_ns': 1})
        self.reject(self.o._validate_request, dict(format=Hostile('x'), remaining_ns=1))

    def test_frame_exact_cap_and_preallocation_cap_plus_one(self):
        for raw in (b'x', b'x'*65536):
            self.io(frame(raw))
            self.assertEqual(self.o._read_frame(self.now+1), raw)
        _, _, reads = self.io((65537).to_bytes(4, 'big') + b'not read')
        self.reject(self.o._read_frame, self.now+1)
        self.assertEqual(reads, [4])

    def test_frame_truncation_zero_and_any_trailing(self):
        for data in (b'', b'\0', b'\0\0\0', b'\0'*4, frame(b'xx')[:-1], frame(b'x')+b'y'):
            self.io(data)
            self.reject(self.o._read_frame, self.now+1)

    def test_offsets_survive_partial_eintr_eagain(self):
        raw = payload()
        _, out, _ = self.io(frame(raw), (1, InterruptedError(), BlockingIOError(errno.EAGAIN, 'wait'), 2),
                           (7, InterruptedError(), BlockingIOError(errno.EAGAIN, 'wait'), 13))
        self.assertEqual(self.o._read_frame(self.now+1), raw)
        self.o._write_frame(b'x'*1048576, self.now+1)
        self.assertEqual(out, frame(b'x'*1048576))

    def test_bad_write_and_output_overflow_fail(self):
        for step in (0, -1, 100, True, BrokenPipeError(), BlockingIOError(errno.EAGAIN, 'uncertain', 1)):
            self.io(b'', write_steps=(step,))
            self.reject(self.o._write_frame, b'x', self.now+1)
        for data in (b'', b'x'*1048577):
            self.reject(self.o._write_frame, data, self.now+1)

    def test_nonblocking_checked_before_io(self):
        for value in (True, 0):
            self.patch(self.o.os, 'get_blocking', return_value=value)
            self.reject(self.o._read_frame, self.now+1)
            self.reject(self.o._write_frame, b'x', self.now+1)

    def test_input_clock_never_renews(self):
        o = self.o
        for elapsed, remaining, success, deadline in ((4, 6, True, 7), (4, 60, True, 16),
                (15, 60, False, 16), (6, 6, False, 7), (1, 600, False, 0)):
            self.now = 1_000_000_000
            def read(deadline):
                self.now += elapsed*10**9
                return payload(remaining*10**9)
            with mock.patch.object(o, '_invocation'), mock.patch.object(o, '_read_frame', side_effect=read), \
                 mock.patch.object(o, '_observe', return_value={}) as observe, \
                 mock.patch.object(o, '_write_frame'), mock.patch.object(o, '_close', return_value=None), \
                 mock.patch.object(o, '_diagnostic'):
                self.assertEqual(o.main(), 0 if success else 1)
                if success: observe.assert_called_once_with(deadline*10**9)
                else: observe.assert_not_called()

    def test_observation_drift_privilege_failures(self):
        o = self.o
        data = self.proc()
        self.patch(o, 'observe_worker_network', return_value=observation())
        base = data['/proc/1/status']
        for old, new in ((b'10001 10001 10001 10001', b'0 10001 10001 10001'),
                         (b'CapEff:\t0000000000000000', b'CapEff:\t0000000000000001'),
                         (b'NoNewPrivs:\t1', b'NoNewPrivs:\t0')):
            data['/proc/1/status'] = base.replace(old, new)
            self.reject(o._observe, self.now+1)
        data['/proc/1/status'] = base
        for field, value in (('pid1_start_time', 124), ('own_pid', 1), ('pid1_exe', {}),
                             ('pid1_netns', dict(device=1, inode=2)), ('own_netns', dict(device=1, inode=2))):
            before = o._snapshot(self.now+1)
            after = copy.deepcopy(before); after[field] = value
            with mock.patch.object(o, '_snapshot', side_effect=[before, after]):
                self.reject(o._observe, self.now+1)

    def test_status_duplicates_lists_caps_and_stat_parentheses(self):
        o = self.o
        parsed = o._status(STATUS.encode(), 8)
        self.assertEqual(parsed['uid'], [10001]*4)
        for raw in (STATUS+'CapEff: 0\n', STATUS.replace('10001 10001 10001 10001', '10001 10001'),
                    STATUS.replace('Groups:\t10001', 'Groups:\t0'), STATUS.replace('CapBnd:\t0000000000000000', 'CapBnd:\txyz'),
                    STATUS.replace('Pid:\t8', 'Pid:\t1'), STATUS+'Name: duplicate\n'):
            self.reject(o._status, raw.encode(), 8)
        self.assertEqual(o._start(STAT.encode()), 123)
        for raw in (b'2 (main) S 0\n', STAT.replace('123', '-1').encode(), b'1 missing S 0\n', b'1 (x) S '+b'1 '*100):
            self.reject(o._start, raw)

    def test_main_no_success_after_close_expiry_or_partial_output(self):
        o = self.o
        for phase in ('close', 'write', 'expire'):
            with mock.patch.object(o, '_invocation'), mock.patch.object(o, '_read_frame', return_value=payload()), \
                 mock.patch.object(o, '_observe', return_value={}), \
                 mock.patch.object(o, '_write_frame', side_effect=OSError('secret') if phase=='write' else None), \
                 mock.patch.object(o, '_close', side_effect=OSError('secret') if phase=='close' else None), \
                 mock.patch.object(o, '_diagnostic') as diagnostic:
                if phase=='expire': self.now += 20_000_000_000
                if phase=='expire':
                    with mock.patch.object(o, '_check', side_effect=o.B3Error('expired')):
                        self.assertEqual(o.main(), 1)
                else: self.assertEqual(o.main(), 1)
                diagnostic.assert_called_once_with()

    def test_import_and_fixed_invocation_have_no_native_discovery(self):
        o = self.o
        # Execute module body; setUp's native seams would fail on IO/discovery.
        namespace = dict(__name__=NAME, __package__='platform_service.media_backup_b3')
        code = compile(SOURCE.read_bytes(), str(SOURCE), 'exec')
        with mock.patch.dict(sys.modules, {'os': o.os}):
            exec(code, namespace)
        self.assertTrue(callable(namespace['main']))
        for argv, platform, exe, tty in ((['observer', 'mode'], 'linux', '/opt/easel/bin/python', False),
                (['observer'], 'darwin', '/opt/easel/bin/python', False),
                (['observer'], 'linux', '/usr/bin/python', False),
                (['observer'], 'linux', '/opt/easel/bin/python', True)):
            with mock.patch.object(o.sys, 'argv', argv), mock.patch.object(o.sys, 'platform', platform), \
                 mock.patch.object(o.sys, 'executable', exe), mock.patch.object(o.os, 'isatty', return_value=tty):
                self.reject(o._invocation)
        with mock.patch.object(o.sys, 'argv', ['observer']), mock.patch.object(o.sys, 'platform', 'linux'), \
             mock.patch.object(o.sys, 'executable', '/opt/easel/bin/python'), mock.patch.object(o.os, 'isatty', return_value=False):
            o._invocation()

    def test_full_main_partial_response_is_failure_and_static_diagnostic(self):
        o = self.o
        self.proc()
        self.patch(o, '_invocation')
        self.patch(o.os, 'close', return_value=None)
        self.patch(o, 'observe_worker_network', return_value=observation())
        self.io(frame(payload()))
        output = []
        def write(fd, data):
            output.append((fd, data))
            if fd == 1 and sum(x[0] == 1 for x in output) > 1:
                raise BrokenPipeError('private native error')
            return min(7, len(data)) if fd == 1 else len(data)
        self.patch(o.os, 'write', side_effect=write)
        self.assertEqual(o.main(), 1)
        self.assertEqual(output[-1], (2, b'b3_baseline_observer_rejected\n'))
        self.assertEqual(sum(fd == 1 for fd, _ in output), 2)

    def test_full_response_then_stdout_or_stderr_close_failure_returns_one(self):
        o = self.o
        self.proc()
        self.patch(o, '_invocation')
        self.patch(o, 'observe_worker_network', return_value=observation())
        for failed_fd in (1, 2):
            with self.subTest(failed_fd=failed_fd):
                _, output, _ = self.io(frame(payload()))
                transport_write = o.os.write.side_effect
                closed, diagnostics = [], []
                def valid_response():
                    self.assertGreaterEqual(len(output), 5)
                    declared = int.from_bytes(output[:4], 'big')
                    self.assertTrue(0 < declared <= 1048576)
                    self.assertEqual(len(output), declared+4)
                    result = json.loads(output[4:])
                    self.assertEqual(set(result), {'format', 'before', 'after', 'raw_network'})
                    self.assertEqual(result['format'], 'easel.b3.execution-baseline-response.v1')
                    self.assertEqual(result['before'], result['after'])
                    self.assertEqual(result['before']['own_pid'], 8)
                    self.assertEqual(result['before']['pid1_start_time'], 123)
                    self.assertEqual(result['raw_network'], wire_fixture())
                def close(fd):
                    closed.append(fd)
                    if fd == 0:
                        self.assertEqual(output, b'')
                    else:
                        valid_response()  # Complete valid frame precedes late close.
                    if fd == failed_fd:
                        raise OSError('private late-close error')
                def write(fd, data):
                    if fd == 1:
                        return transport_write(fd, data)
                    self.assertEqual(fd, 2)
                    diagnostics.append(data)
                    return len(data)
                with mock.patch.object(o.os, 'close', side_effect=close), \
                     mock.patch.object(o.os, 'write', side_effect=write):
                    self.assertEqual(o.main(), 1)
                self.assertEqual(closed, [0, 1] if failed_fd == 1 else [0, 1, 2])
                valid_response()
                self.assertEqual(diagnostics, [b'b3_baseline_observer_rejected\n'])

    def test_all_status_privileges_and_self_pid1_rejected(self):
        o = self.o
        data = self.proc()
        self.patch(o, 'observe_worker_network', return_value=observation())
        original = data['/proc/self/status']
        for field in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb'):
            data['/proc/self/status'] = original.replace(field.encode()+b':\t0000000000000000', field.encode()+b':\t0000000000000001')
            self.reject(o._observe, self.now+1)
        for field in ('Uid', 'Gid'):
            data['/proc/self/status'] = original.replace(field.encode()+b':\t10001 10001 10001 10001', field.encode()+b':\t10001 0 10001 10001')
            self.reject(o._observe, self.now+1)
        data['/proc/self/status'] = original
        self.patch(o.os, 'getpid', return_value=1)
        self.reject(o._observe, self.now+1)

    def test_namespace_mismatch_and_exe_drift_from_real_snapshot_guards(self):
        o = self.o
        self.proc()
        self.patch(o, 'observe_worker_network', return_value=observation())
        original = o.os.stat.side_effect
        def mismatch(path, **kw):
            value = original(path, **kw)
            if path == '/proc/1/ns/net': value.st_ino += 1
            return value
        self.patch(o.os, 'stat', side_effect=mismatch)
        self.reject(o._observe, self.now+1)
        self.patch(o.os, 'stat', side_effect=original)
        for mode in ('start', 'exe', 'uid'):
            def n4(deadline):
                if mode == 'start':
                    o._proc_bytes.side_effect = lambda path, deadline: STAT.replace('123', '124').encode() if path.endswith('/stat') else (
                        STATUS if path == '/proc/self/status' else STATUS.replace('Pid:\t8', 'Pid:\t1')).encode()
                elif mode == 'exe':
                    o.os.readlink.return_value = '/usr/local/bin/python3.14'
                else:
                    o._proc_bytes.side_effect = lambda path, deadline: STAT.encode() if path.endswith('/stat') else (
                        STATUS.replace('10001', '10002') if path == '/proc/self/status' else STATUS.replace('Pid:\t8', 'Pid:\t1')).encode()
                return observation()
            with mock.patch.object(o, 'observe_worker_network', side_effect=n4):
                self.reject(o._observe, self.now+1)
            o._proc_bytes.side_effect = lambda path, deadline: STAT.encode() if path.endswith('/stat') else (
                STATUS if path == '/proc/self/status' else STATUS.replace('Pid:\t8', 'Pid:\t1')).encode()
            o.os.readlink.return_value = '/usr/local/bin/python3.13'

    def test_fixed_proc_reader_bounds_closes_checks_nonblocking_and_identity(self):
        o = self.o
        s = types.SimpleNamespace(st_dev=1, st_ino=2, st_mode=stat.S_IFREG|0o444,
                                 st_uid=10001, st_gid=10001, st_size=0, st_mtime_ns=0, st_ctime_ns=0)
        self.patch(o.os, 'open', return_value=9)
        self.patch(o.os, 'fstat', return_value=s)
        self.patch(o.os, 'stat', return_value=s)
        close = self.patch(o.os, 'close', return_value=None)
        for raw in (b'x', b'x'*65536):
            self.io(raw, (InterruptedError(), BlockingIOError(errno.EAGAIN, 'retry'), 7))
            self.assertEqual(o._proc_bytes('/proc/1/stat', self.now+1), raw)
            close.assert_called_with(9)
        for raw in (b'x'*65537, b'', b'\xff', b'\0', b'a\r\n'):
            self.io(raw)
            self.reject(o._proc_bytes, '/proc/1/stat', self.now+1)
        self.io(b'valid')
        with mock.patch.object(o.os, 'close', side_effect=OSError('uncertain close')):
            with self.assertRaises(OSError): o._proc_bytes('/proc/1/stat', self.now+1)
        changed = copy.copy(s); changed.st_ino += 1
        self.io(b'valid')
        with mock.patch.object(o.os, 'stat', return_value=changed):
            self.reject(o._proc_bytes, '/proc/1/stat', self.now+1)
        for path in ('/tmp/file', '/proc/2/stat', '/proc/self/exe'):
            self.reject(o._proc_bytes, path, self.now+1)

    def test_select_expiry_eof_wait_and_spurious_eagain_never_renew(self):
        o = self.o
        self.io(frame(payload()))
        def select(*args):
            self.now += 1_000_000_000
            return ([0], [1], [])
        self.patch(o, '_select', side_effect=select)
        self.reject(o._read_frame, self.now+1_000_000_000)
        # Header and payload readable, EOF is delayed past the same input cap.
        self.now = 1
        calls = []
        def read(fd, size):
            calls.append(size)
            if len(calls) == 1: return len(payload()).to_bytes(4, 'big')
            if len(calls) == 2: return payload()
            self.now = 15_000_000_001
            return b''
        self.patch(o, '_select', return_value=([0], [1], []))
        self.patch(o.os, 'read', side_effect=read)
        self.reject(o._read_frame, 15_000_000_001)
        self.assertEqual(len(calls), 3)

    def test_main_n4_invalid_encode_close_or_expiry_never_success(self):
        o = self.o
        self.proc()
        self.patch(o, '_invocation')
        self.patch(o.os, 'close', return_value=None)
        self.patch(o, 'observe_worker_network', return_value=observation())
        for phase in ('n4', 'encode', 'close', 'expiry'):
            self.now = 1_000_000_000
            self.io(frame(payload()))
            def n4(deadline):
                if phase == 'n4': raise OSError('private-canary')
                if phase == 'encode': return object()
                if phase == 'expiry': self.now = deadline
                return observation()
            with mock.patch.object(o, 'observe_worker_network', side_effect=n4), \
                 mock.patch.object(o.os, 'close', side_effect=OSError('close') if phase == 'close' else None), \
                 mock.patch.object(o, '_diagnostic'):
                self.assertEqual(o.main(), 1)

    def test_main_small_native_proc_reads_do_not_bypass_reader(self):
        o = self.o
        self.patch(o, '_invocation')
        ns = observation().namespace_before
        regular = types.SimpleNamespace(st_dev=2, st_ino=3, st_mode=stat.S_IFREG|0o555,
            st_uid=0, st_gid=0, st_size=100, st_mtime_ns=1, st_ctime_ns=1)
        self.patch(o.os, 'stat', side_effect=lambda path, **kw: types.SimpleNamespace(st_dev=ns.device, st_ino=ns.inode)
                   if '/ns/' in path else regular)
        self.patch(o.os, 'fstat', return_value=regular)
        self.patch(o.os, 'readlink', return_value='/usr/local/bin/python3.13')
        self.patch(o.os, 'getpid', return_value=8)
        self.patch(o.os, 'close', return_value=None)
        self.patch(o, 'observe_worker_network', return_value=observation())
        incoming, outgoing, _ = self.io(frame(payload()))
        transport_read = o.os.read.side_effect
        filedata = bytearray()
        paths = []
        def op(path, flags):
            paths.append(path)
            filedata[:] = (STAT if path == '/proc/1/stat' else STATUS if path == '/proc/self/status' else STATUS.replace('Pid:\t8', 'Pid:\t1')).encode()
            return 9
        def read(fd, size):
            if fd == 0: return transport_read(fd, size)
            result = bytes(filedata[:size]); del filedata[:size]
            return result
        self.patch(o.os, 'open', side_effect=op)
        self.patch(o.os, 'read', side_effect=read)
        self.assertEqual(o.main(), 0)
        self.assertEqual(paths, ['/proc/1/stat', '/proc/self/status', '/proc/1/status']*2)
        self.assertTrue(outgoing)

    def test_oversize_and_malformed_native_data_never_encode_output(self):
        o = self.o
        self.proc()
        self.io(frame(payload()))
        self.patch(o, '_invocation')
        self.patch(o.os, 'close', return_value=None)
        self.patch(o, 'observe_worker_network', return_value=object())
        with mock.patch.object(o, '_write_frame') as output, mock.patch.object(o, '_diagnostic'):
            self.assertEqual(o.main(), 1)
            output.assert_not_called()
        for raw in (b'1 (x) S '+b'1'*65000+b'x\n', b'\xff', b'x'*65537):
            self.reject(o._start, raw)


if __name__ == '__main__':
    unittest.main()
