"""Private bounded transport for the closed execution owner only.

Slots precede allocation. A raised allocator or constructor remains unobserved;
this module never discovers, restarts, signals, or releases unknown children.
"""
from dataclasses import dataclass, field
import os
import selectors
from .contracts import require

class _Capture:
    def __init__(self, cap, framed):
        require(type(cap) is int and cap > 0 and type(framed) is bool)
        self.cap=cap; self.framed=framed; self.raw=bytearray(); self.size=None
    def feed(self, raw):
        require(type(raw) is bytes)
        if self.framed and self.size is None:
            take=min(4-len(self.raw),len(raw)); self.raw.extend(raw[:take]); raw=raw[take:]
            if len(self.raw)==4:
                self.size=int.from_bytes(self.raw,'big'); require(0 < self.size <= self.cap)
        ceiling=self.cap if not self.framed else (4 if self.size is None else self.size+4)
        require(len(self.raw)+len(raw)<=ceiling); self.raw.extend(raw)
    def finish(self):
        if self.framed: require(self.size is not None and len(self.raw)==self.size+4)
        return bytes(self.raw)

@dataclass(repr=False)
class _NativeSlot:
    operation: str
    ordinal: int
    argv: tuple
    argv_pin: str
    process: object = None
    fds: list = field(default_factory=list)
    parent: dict = field(default_factory=dict)
    captures: dict = field(default_factory=dict)
    eof: set = field(default_factory=set)
    input: object = None
    offset: int = 0
    input_closed: bool = False
    constructed: bool = False
    terminal_code: object = None
    terminal: bool = False
    durable: bool = False
    remote_cid: object = None
    remote_exec: bool = False
    request_pin: object = None


def _launch(o,a,slot,cap,framed,held=False):
    # Native slot is already retained by the owner BEFORE any allocation.
    slot.captures={'out':_Capture(cap,framed),'err':_Capture(o._policy.limits.diagnostic_limit if slot.operation=='worker-start' else 65536,False)}
    children={}
    for channel in ('in','out','err'):
        pair=[];slot.fds.append(pair)
        result=o._io_pipe()  # pair remains present even on interrupted allocation
        require(type(result) is tuple and len(result)==2)
        pair.extend(result)  # immediately retain both returned originals
        for fd in result:
            require(type(fd) is int and fd>=0)
            info=os.fstat(fd)
            o._native_stamps[(id(slot),fd)]=(info.st_dev,info.st_ino,info.st_mode)
        read,write=result
        parent,child=(write,read) if channel=='in' else (read,write)
        slot.parent[channel]=parent;children[channel]=child
        o._io_nonblocking(parent,False)
    child=object.__new__(o._popen_type());slot.process=child
    o._native_originals.append((slot,child,slot.argv,slot.ordinal))
    o._guard_live()
    if held:
        require(a.main_start_anchor is None)
        a.main_start_anchor=o._clock()
        a.acceptance_deadline=min(o._deadline,a.main_start_anchor+60)
    o._guard_live() # AFTER intent fsync and immediately before constructor
    o._popen_type().__init__(child,slot.argv,stdin=children['in'],stdout=children['out'],
        stderr=children['err'],env=o._execution_env(),close_fds=True,cwd=o._path)
    slot.constructed=True
    for fd in children.values():o._close_native_fd(slot,fd)
    if not held:
        slot.input=b''


def _step(o,slots):
    """One bounded multiplexed turn; also drains the held original main."""
    o._guard_live()
    record={'object':None,'closed':False};o._selector_slots.append(record)
    selector_type=o._selector_type()
    selector=object.__new__(selector_type);record['object']=selector
    selector_type.__init__(selector)
    try:
        for s in slots:
            require(s.constructed)
            for ch in ('out','err'):
                if ch not in s.eof:
                    o._validate_native_fd(s,s.parent[ch]);selector.register(s.parent[ch],selectors.EVENT_READ,(s,ch))
            if s.input is not None and not s.input_closed:
                if s.offset==len(s.input):
                    o._close_native_fd(s,s.parent['in']);s.input_closed=True
                else:
                    o._validate_native_fd(s,s.parent['in']);selector.register(s.parent['in'],selectors.EVENT_WRITE,(s,'in'))
        for key,mask in selector.select(.01):
            o._guard_live();s,ch=key.data
            try:
                if ch=='in':
                    n=o._io_write(key.fd,s.input[s.offset:s.offset+65536])
                    require(type(n) is int and 0<n<=min(65536,len(s.input)-s.offset));s.offset+=n
                else:
                    raw=o._io_read(key.fd,65536);require(type(raw) is bytes and len(raw)<=65536)
                    if raw:s.captures[ch].feed(raw)
                    else:s.eof.add(ch)
            except (InterruptedError,BlockingIOError) as exc:
                require(not getattr(exc,'characters_written',0));continue
        for s in slots:
            code=s.process.poll()
            if code is not None:
                require(type(code) is int and code==0)
                if s.terminal_code is not None:require(s.terminal_code==code)
                s.terminal_code=code
                if s.eof=={'out','err'} and s.input_closed:
                    for c in s.captures.values():c.finish()
                    require(s.offset==len(s.input));s.terminal=True
    finally:
        selector.close();record['closed']=True
    o._guard_live()


def _drain(o,a,slot):
    while not slot.terminal:
        active=[s for s in a.operations if s.constructed and not s.terminal]
        _step(o,active)
    return slot.captures['out'].finish()
