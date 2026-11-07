"""Source-only bounded pipe tests; no subprocess is started."""
import unittest
try:
    from platform_service.media_backup_b3 import execution_io as io
except ImportError:
    io=None
from platform_service.media_backup_b3.contracts import B3Error

class FrameTests(unittest.TestCase):
    def setUp(self):self.assertIsNotNone(io,"bounded original pipe capture is required")
    def test_payload_cap_includes_only_body(self):
        frame=io._Capture(5,True)
        for b in b"\0\0\0\5abcde":frame.feed(bytes([b]))
        self.assertEqual(frame.finish(),b"\0\0\0\5abcde")
    def test_header_rejected_before_body(self):
        for header in (b"\0\0\0\0",b"\0\0\0\6"):
            capture=io._Capture(5,True)
            with self.assertRaises(B3Error):capture.feed(header)
            self.assertLessEqual(len(capture.raw),4)
    def test_truncation_and_any_trailing_byte(self):
        for raw in (b"",b"\0\0",b"\0\0\0\2x",b"\0\0\0\1xy"):
            with self.subTest(raw=raw), self.assertRaises(B3Error):
                capture=io._Capture(5,True);capture.feed(raw);capture.finish()
    def test_raw_diagnostic_has_no_header_allowance(self):
        capture=io._Capture(5,False);capture.feed(b"abcde")
        self.assertEqual(capture.finish(),b"abcde")
        with self.assertRaises(B3Error):capture.feed(b"x")

class PumpTests(unittest.TestCase):
    def fixture(self,write_steps=(),out=(b'\0\0\0\1x',b''),err=(b'',)):
        import types
        slot=io._NativeSlot('worker-start',1,(), 'a'*64)
        slot.parent={'in':10,'out':11,'err':12};slot.constructed=True
        slot.captures={'out':io._Capture(5,True),'err':io._Capture(5,False)}
        slot.input=b'abcdef';steps=list(write_steps);sent=bytearray();closed=[]
        streams={11:list(out),12:list(err)}
        class Selector:
            def __init__(self):self.keys=[]
            def register(self,fd,mask,data):self.keys.append(types.SimpleNamespace(fd=fd,data=data))
            def select(self,timeout):return [(key,0) for key in self.keys]
            def close(self):pass
        def write(fd,raw):
            value=steps.pop(0) if steps else min(2,len(raw))
            if isinstance(value,BaseException):raise value
            sent.extend(raw[:value]);return value
        def read(fd,n):
            self.assertLessEqual(n,65536)
            return streams[fd].pop(0)
        owner=types.SimpleNamespace(_selector_slots=[],_guard_live=lambda:None,_selector_type=lambda:Selector,
            _validate_native_fd=lambda s,fd:None,_close_native_fd=lambda s,fd:closed.append(fd),
            _io_write=write,_io_read=read)
        slot.process=types.SimpleNamespace(poll=lambda:0 if not streams[11] and not streams[12] else None)
        return owner,slot,sent,closed
    def test_partial_eintr_eagain_never_replay_whole_frame(self):
        owner,slot,sent,closed=self.fixture((InterruptedError(),BlockingIOError(),2,1))
        for _ in range(12):
            io._step(owner,[slot])
            if slot.terminal:break
        self.assertTrue(slot.terminal);self.assertEqual(slot.offset,6);self.assertEqual(sent,b'abcdef')
        self.assertEqual(closed,[10]);self.assertEqual(slot.eof,{'out','err'})
    def test_ambiguous_write_progress_is_not_retried(self):
        import errno
        owner,slot,sent,closed=self.fixture((BlockingIOError(errno.EAGAIN,'uncertain',1),))
        with self.assertRaises(B3Error):io._step(owner,[slot])
        self.assertEqual(slot.offset,0)
    def test_stderr_overflow_and_nonzero_exit_fail(self):
        owner,slot,_,_=self.fixture(err=(b'abcdef',))
        with self.assertRaises(B3Error):io._step(owner,[slot])
        owner,slot,_,_=self.fixture();slot.process.poll=lambda:9
        with self.assertRaises(B3Error):io._step(owner,[slot])
    def test_held_main_drained_concurrently_with_other_slot(self):
        owner,main,sent,closed=self.fixture();main.input=None
        other=io._NativeSlot('probe',2,(), 'b'*64)
        # Identity stays separate even when read readiness interleaves.
        other.parent={'in':20,'out':21,'err':22};other.constructed=True
        other.input=b'';other.captures={'out':io._Capture(5,False),'err':io._Capture(5,False)}
        other.process=type('P',(),{'poll':lambda self:None})()
        realread=owner._io_read;owner._io_read=lambda fd,n:b'' if fd in (21,22) else realread(fd,n)
        io._step(owner,[main,other]);self.assertTrue(main.captures['out'].raw)
        self.assertEqual(main.offset,0);self.assertFalse(main.input_closed);self.assertEqual(other.eof,{'out','err'})

    def test_selector_constructor_interruption_is_originally_anchored(self):
        owner,slot,_,_=self.fixture()
        class Interrupted:
            def __init__(self):
                assert owner._selector_slots[-1]['object'] is self
                raise KeyboardInterrupt('synthetic selector constructor')
        owner._selector_type=lambda:Interrupted
        with self.assertRaises(KeyboardInterrupt):io._step(owner,[slot])
        self.assertFalse(owner._selector_slots[-1]['closed'])
        self.assertIsNotNone(owner._selector_slots[-1]['object'])
    def test_selector_close_failure_retains_original_object(self):
        owner,slot,_,_=self.fixture()
        original=owner._selector_type()
        class CloseFailure(original):
            def close(self):raise OSError('synthetic selector close')
        owner._selector_type=lambda:CloseFailure
        with self.assertRaises(OSError):io._step(owner,[slot])
        self.assertFalse(owner._selector_slots[-1]['closed'])
