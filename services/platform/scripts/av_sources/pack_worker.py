"""Public offline AV source gate. Native effects occur only in explicit main().

Pure injected evidence is never admissible as an actual host report. This worker
proves content integrity, not signed provenance or adoption.
"""
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import signal
import stat
import sys
import time
import types

IMAGE = 'sha256:3f9c7f75b916e8279e24136d7a57769aba9e193715be52570417698c52d6eed0'
CODE_NAMES = ('pack_worker.py','pack_runtime.py','pack_store.py','git_objects.py')
PROTECTED = {'pack_store.py':'30fa4928e766dfc4137f0b367d5d134c1523a06bc7f7eed4827fbc77d15ca3a6',
             'git_objects.py':'bbcd07d1968fbd41553e35a84e8b60a7f7534b4808b215b8fa84b37bfb76fad2'}
LIMITS = {'AS':[536870912,536870912], 'CPU':[30,30], 'CORE':[0,0],
          'FSIZE':[16777216,16777216], 'NOFILE':[64,64]}
MODES = ('preflight','decode','control-as','control-cpu','control-wall')
FALSE_FLAGS = ('signed_provenance_verified','source_signatures_verified','build_inputs_adopted',
               'production_verified','original_remote_gate_verified','amd64_toolchain_verified')
MANIFEST_CAP = 16777216
_PARENT = None
_ARMS = 0
_RUNTIMES = {}
_SUPERVISOR_SIGNALS_OK = False


class WorkerError(ValueError):
    """Only fixed safe diagnostics are emitted by the CLI."""


def _need(condition, message='worker contract rejected'):
    if not condition: raise WorkerError(message)


def _hex(value, size):
    return type(value) is str and re.fullmatch('[0-9a-f]{'+str(size)+'}',value) is not None


def _oid(value): return _hex(value,40) and value != '0'*40


def validate_profile(p):
    _need(type(p) is dict and set(p) == {'format','nonce','image','platform','uid','gid','mode','code_sha256','pack'})
    _need(p['format'] == 'av-pack-worker-profile-v1' and _hex(p['nonce'],32))
    _need(p['image'] == IMAGE and p['platform'] == 'linux/arm64/v8')
    _need(all(type(p[k]) is int and 0 < p[k] < 2**31 for k in ('uid','gid')))
    _need(type(p['mode']) is str and p['mode'] in MODES)
    pins = p['code_sha256']
    _need(type(pins) is dict and set(pins) == set(CODE_NAMES) and all(_hex(x,64) for x in pins.values()))
    _need(all(pins[n] == h for n,h in PROTECTED.items()))
    q = p['pack']
    if p['mode'] != 'decode': _need(q is None); return p
    _need(type(q) is dict and set(q) == {'file','bytes','sha256','commit_oid','expected_tree_oid','required_gitlink_oid','required_entry'})
    _need(q['file'] == 'source.pack' and type(q['bytes']) is int and 32 <= q['bytes'] <= 67108864)
    _need(_hex(q['sha256'],64) and _oid(q['commit_oid']))
    for k in ('expected_tree_oid','required_gitlink_oid'): _need(q[k] is None or _oid(q[k]))
    e = q['required_entry']
    if e is not None:
        _need(type(e) is dict and set(e) == {'path','oid','bytes'})
        path = e['path']
        _need(type(path) is str and 0 < len(path.encode('utf-8')) <= 4096 and not path.startswith('/')
              and all(x not in ('','.','..') for x in path.split('/')) and '\\' not in path and '\0' not in path)
        _need(_oid(e['oid']) and type(e['bytes']) is int and 0 <= e['bytes'] <= 16777216)
    return p


def parse_profile(raw):
    _need(type(raw) is bytes and 0 < len(raw) <= 16384)
    def pairs(items):
        out = {}
        for k,v in items:
            _need(k not in out, 'duplicate JSON key')
            out[k] = v
        return out
    def constant(_): raise WorkerError('nonfinite JSON')
    try: return validate_profile(json.loads(raw, object_pairs_hook=pairs, parse_constant=constant))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc: raise WorkerError('invalid profile JSON') from exc


def _identity(s):
    return (s.st_dev,s.st_ino,s.st_mode,s.st_size,s.st_uid,s.st_gid,s.st_nlink,s.st_mtime_ns,s.st_ctime_ns)


def _directory(path, uid=None, gid=None):
    path = os.fspath(path)
    _need(type(path) is str and path.startswith('/') and '\0' not in path
          and all(x not in ('','.','..') for x in path.split('/')[1:]))
    fd = os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for name in path.split('/')[1:]:
            nxt = os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd); fd=nxt
        s=os.fstat(fd)
        _need(stat.S_IMODE(s.st_mode)==0o700 and s.st_uid==(os.geteuid() if uid is None else uid)
              and (gid is None or s.st_gid==gid),'stage directory identity rejected')
        return fd
    except BaseException: os.close(fd); raise


def read_private(directory, name, *, cap, expected_sha=None, expected_size=None, uid=None, gid=None):
    _need(type(name) is str and name in (*CODE_NAMES,'profile.json','source.pack','manifest.json'))
    parent = _directory(directory,uid,gid)
    fd = None
    try:
        before=os.stat(name,dir_fd=parent,follow_symlinks=False)
        _need(stat.S_ISREG(before.st_mode) and stat.S_IMODE(before.st_mode)==0o600 and before.st_nlink==1
              and before.st_uid==(os.geteuid() if uid is None else uid) and (gid is None or before.st_gid==gid)
              and 0 <= before.st_size <= cap and (expected_size is None or before.st_size==expected_size))
        fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
        _need(_identity(os.fstat(fd))==_identity(before))
        data=bytearray()
        while True:
            chunk=os.read(fd,min(65536,cap+1-len(data)))
            if not chunk: break
            data.extend(chunk)
            _need(len(data)<=cap)
        _need(len(data)==before.st_size and _identity(os.fstat(fd))==_identity(before)
              and _identity(os.stat(name,dir_fd=parent,follow_symlinks=False))==_identity(before),'stage drift')
        data=bytes(data)
        _need(expected_sha is None or hashlib.sha256(data).hexdigest()==expected_sha,'code/input pin mismatch')
        return data
    finally:
        if fd is not None: os.close(fd)
        os.close(parent)


def _load(name, raw):
    # These are pinned public helpers, never object payloads. Dataclass resolves
    # the module in sys.modules; no sys.path mutation or application fallback.
    key='_av_verified_'+name[:-3]+'_'+hashlib.sha256(raw).hexdigest()
    mod=types.ModuleType(key)
    sys.modules[key]=mod
    try: exec(compile(raw,'/code/'+name,'exec',dont_inherit=True),mod.__dict__)
    except BaseException: sys.modules.pop(key,None); raise
    return mod


def _codes(profile, directory):
    raw={n:read_private(directory,n,cap=262144,expected_sha=profile['code_sha256'][n],
                        uid=profile['uid'],gid=profile['gid']) for n in CODE_NAMES}
    # Verify all bytes before executing even the first helper.
    return raw


def prepare_runtime(profile, *, code_dir):
    """Host contract: prepare from a bounded, pinned, private code stage first.

    Native main always uses /code. No implicit source-tree/path fallback exists.
    Retained module bytes are the same bytes checked by _codes, even if a later
    filesystem path changes; execute_decode separately rechecks stage pins.
    """
    validate_profile(profile)
    raw=_codes(profile,code_dir)
    runtime=_load('pack_runtime.py',raw['pack_runtime.py'])
    _RUNTIMES[profile['code_sha256']['pack_runtime.py']]=runtime
    return raw


def _runtime_module(profile):
    key=profile['code_sha256']['pack_runtime.py']
    _need(key in _RUNTIMES,'runtime preparation required')
    return _RUNTIMES[key]


def validate_runtime(proof, profile):
    try: return _runtime_module(profile).validate_runtime(proof,profile)
    except ValueError as exc: raise WorkerError('runtime proof rejected') from exc


def collect_runtime(profile):
    _need(_PARENT is not None and _ARMS==1,'child setup required')
    return _runtime_module(profile).collect_runtime(profile,parent_pid=_PARENT,timer_arms=_ARMS,
                                                   supervisor_signals_ok=_SUPERVISOR_SIGNALS_OK)


def synthetic_runtime(profile):
    """Explicit pure fixture; only execute_decode(_runtime=...) accepts this seam."""
    return dict(uid=profile['uid'],gid=profile['gid'],groups=[],pid=2,ppid=1,supervisor_pid=1,
                system='Linux',machine='aarch64',limits={k:list(v) for k,v in LIMITS.items()},
                timer_remaining=119.9,timer_interval=0,timer_arms=1,caps=['0000000000000000']*5,
                no_new_privs=1,seccomp=2,interfaces=['lo'],routes=[],cgroup_path='/synthetic',
                cgroup_scope='namespace-relative;host-inspect-required',cgroup_namespace='cgroup:[1234]',
                supervisor_cgroup_namespace='cgroup:[1234]',
                alarm_default=True,alarm_blocked=False,alarm_pending=False,
                supervisor_term_int_unblocked=True,
                cgroup_readonly=True,memory_max=536870912,memory_swap_max=0,pids_max=16,
                cpu_max=[200000,100000],mounts_validated=True)


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')


def write_manifest(directory, manifest, *, _budget=None):
    cap=MANIFEST_CAP if _budget is None else _budget
    _need(type(cap) is int and 0 < cap <= MANIFEST_CAP)
    parent=_directory(directory)
    fd=None; created=False; total=0; digest=hashlib.sha256()
    try:
        fd=os.open('manifest.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=parent)
        created=True
        for text in json.JSONEncoder(sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).iterencode(manifest):
            # Bound each write even if a single JSON token is long.
            for start in range(0,len(text),4096):
                chunk=text[start:start+4096].encode('ascii')
                _need(len(chunk)<=cap-total,'manifest byte budget')
                view=memoryview(chunk)
                while view:
                    n=os.write(fd,view)
                    _need(type(n) is int and n>0,'manifest write failure')
                    view=view[n:]
                total+=len(chunk);digest.update(chunk)
        os.fsync(fd);os.close(fd);fd=None
        os.fsync(parent)
        data=read_private(directory,'manifest.json',cap=MANIFEST_CAP,expected_size=total,expected_sha=digest.hexdigest())
        return {'file':'manifest.json','bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
    except BaseException:
        if created: os.unlink('manifest.json',dir_fd=parent)
        raise
    finally:
        if fd is not None: os.close(fd)
        os.close(parent)


def _base_report(profile,proof,injected):
    return dict(format='av-pack-worker-report-v1',nonce=profile['nonce'],image=IMAGE,
                platform=profile['platform'],mode=profile['mode'],code_sha256=profile['code_sha256'],
                pack=profile['pack'],runtime_proof=proof,runtime_sha256=hashlib.sha256(canonical(proof)).hexdigest(),
                injected_for_tests=injected, outcome='passed',decode_success=False,manifest=None,
                counts=None,tree_oid=None,matched_entry=None,matched_gitlinks=[],terminal=None,
                **{k:False for k in FALSE_FLAGS})


def execute_decode(profile, *, code_dir, inputs_dir, work_dir, _runtime=None):
    validate_profile(profile)
    raw=prepare_runtime(profile,code_dir=code_dir)
    runtime=_runtime_module(profile)
    proof=collect_runtime(profile) if _runtime is None else _runtime
    try: runtime.validate_runtime(proof,profile)
    except ValueError as exc: raise WorkerError('runtime proof rejected') from exc
    _directory_fd=_directory(inputs_dir,profile['uid'],profile['gid']);os.close(_directory_fd)
    _directory_fd=_directory(work_dir,profile['uid'],profile['gid']);os.close(_directory_fd)
    report=_base_report(profile,proof,_runtime is not None)
    _need(profile['mode'] in ('preflight','decode'))
    if profile['mode']=='preflight':
        report['manifest']=write_manifest(work_dir,{'format':'av-pack-preflight-v1','nonce':profile['nonce']})
        return report
    store_module=_load('pack_store.py',raw['pack_store.py'])
    objects=_load('git_objects.py',raw['git_objects.py'])
    q=profile['pack']
    # Descriptor metadata check before the reviewed loader's own bounded read.
    parent=_directory(inputs_dir,profile['uid'],profile['gid'])
    try:
        info=os.stat('source.pack',dir_fd=parent,follow_symlinks=False)
        _need(info.st_gid==profile['gid'],'input group mismatch')
    finally: os.close(parent)
    store=store_module.load_pack(Path(inputs_dir)/'source.pack',expected_size=q['bytes'],expected_sha256=q['sha256'])
    manifest=objects.verify_commit_tree(q['commit_oid'],store.read_object)
    _need(q['expected_tree_oid'] is None or manifest['tree_oid']==q['expected_tree_oid'],'tree mismatch')
    matched=[e for e in manifest['gitlinks'] if e['oid']==q['required_gitlink_oid']]
    _need(q['required_gitlink_oid'] is None or len(matched)>0,'gitlink mismatch')
    entry=None
    if q['required_entry'] is not None:
        e=q['required_entry']; found=[x for x in manifest['entries'] if x['path']==e['path']]
        _need(len(found)==1 and found[0]['oid']==e['oid'] and found[0]['size']==e['bytes'] and found[0]['kind']=='blob','entry mismatch')
        entry=found[0]
    # Revalidate every code pin after the loader/verifier, before publication.
    _need(_codes(profile,code_dir)==raw,'code drift')
    report.update(decode_success=True,manifest=write_manifest(work_dir,manifest),counts=manifest['counts'],
                  tree_oid=manifest['tree_oid'],matched_entry=entry,matched_gitlinks=matched)
    return report


class NativeOps:
    def getpid(self): return os.getpid()
    def getppid(self): return os.getppid()
    def limits(self):
        import resource
        for key,pair in LIMITS.items():
            ident=getattr(resource,'RLIMIT_'+key)
            actual=resource.getrlimit(ident)
            _need(all(x == resource.RLIM_INFINITY or x >= wanted for x,wanted in zip(actual,pair)),
                  'inherited limit below fixed policy')
            if list(actual)!=pair: resource.setrlimit(ident,tuple(pair))
        return {k:list(resource.getrlimit(getattr(resource,'RLIMIT_'+k))) for k in LIMITS}
    def default_alarm(self): signal.signal(signal.SIGALRM,signal.SIG_DFL)
    def unblock_alarm(self): signal.pthread_sigmask(signal.SIG_UNBLOCK,{signal.SIGALRM})
    def alarm_state(self):
        mask=signal.pthread_sigmask(signal.SIG_BLOCK,set())
        return ('default' if signal.getsignal(signal.SIGALRM)==signal.SIG_DFL else 'handler',
                signal.SIGALRM in mask,signal.SIGALRM in signal.sigpending())
    def arm(self,seconds,interval): signal.setitimer(signal.ITIMER_REAL,seconds,interval)
    def timer(self): return signal.getitimer(signal.ITIMER_REAL)


def setup_child(parent_pid, ops):
    _need(ops.getpid()!=1 and ops.getppid()==parent_pid and parent_pid==1,'dedicated PID1 supervisor required')
    _need(ops.limits()==LIMITS,'resource limits rejected')
    ops.default_alarm()
    ops.unblock_alarm()
    _need(ops.alarm_state()==('default',False,False),'alarm signal state rejected')
    ops.arm(120,0)
    remaining,interval=ops.timer()
    _need(type(remaining) in (int,float) and 0 < remaining <=120 and interval==0,'timer rejected')
    return remaining,interval


def expected_status(mode,status):
    return (os.WIFEXITED(status) and os.WEXITSTATUS(status)==0 if mode in ('preflight','decode','control-as')
            else os.WIFSIGNALED(status) and os.WTERMSIG(status)==(signal.SIGKILL if mode=='control-cpu' else signal.SIGALRM))


def validate_report(report, profile, *, _allow_injected=False):
    """Shared host consumer. The default rejects every injected/offline report.

    Host separately retrieves and validates the complete manifest, its digest,
    same container/config identity and original nonrenewable deadline fences.
    """
    validate_profile(profile)
    _need(type(report) is dict and set(report)==set(_base_report(profile,{},False)))
    for key in ('format','nonce','image','platform','mode','code_sha256','pack'):
        expected='av-pack-worker-report-v1' if key=='format' else profile[key]
        _need(report[key]==expected,'report identity rejected')
    _need(all(report[k] is False for k in FALSE_FLAGS))
    _need(type(report['injected_for_tests']) is bool and (not report['injected_for_tests'] or _allow_injected is True))
    proof=report['runtime_proof'];validate_runtime(proof,profile)
    _need(report['runtime_sha256']==hashlib.sha256(canonical(proof)).hexdigest())
    _need(report['outcome']=='passed' and type(report['decode_success']) is bool)
    artifact=report['manifest']
    _need(type(artifact) is dict and set(artifact)=={'file','bytes','sha256'} and artifact['file']=='manifest.json'
          and type(artifact['bytes']) is int and 0 < artifact['bytes'] <= MANIFEST_CAP and _hex(artifact['sha256'],64))
    if profile['mode']=='decode':
        q=profile['pack'];counts=report['counts']
        _need(report['decode_success'] is True and _oid(report['tree_oid'])
              and (q['expected_tree_oid'] is None or report['tree_oid']==q['expected_tree_oid']))
        _need(type(counts) is dict and set(counts)=={'members','content_bytes','loaded_bytes'}
              and all(type(x) is int and x>=0 for x in counts.values())
              and counts['members']<=30000 and counts['content_bytes']<=536870912 and counts['loaded_bytes']<=134217728)
        entry=report['matched_entry']
        if q['required_entry'] is None: _need(entry is None)
        else:
            e=q['required_entry']
            _need(type(entry) is dict and set(entry)=={'path','mode','kind','oid','size','sha256'}
                  and entry['path']==e['path'] and entry['oid']==e['oid'] and type(entry['size']) is int
                  and entry['size']==e['bytes'] and entry['kind']=='blob' and entry['mode'] in ('100644','100755')
                  and _hex(entry['sha256'],64))
        links=report['matched_gitlinks']
        _need(type(links) is list and len(links)<=30000)
        _need((q['required_gitlink_oid'] is None and links==[]) or (q['required_gitlink_oid'] is not None and bool(links)))
        for link in links:
            _need(type(link) is dict and set(link)=={'path','mode','oid'} and link['mode']=='160000'
                  and link['oid']==q['required_gitlink_oid'] and type(link['path']) is str and 0<len(link['path'].encode())<=4096)
    else:
        _need(report['decode_success'] is False and report['counts'] is None and report['tree_oid'] is None
              and report['matched_entry'] is None and report['matched_gitlinks']==[])
    term=report['terminal']
    if report['injected_for_tests'] and term is None:
        _need(_allow_injected is True)
    else:
        _need(type(term) is dict and set(term)=={'pid','exit_code','signal','elapsed','child_cpu'})
        _need(type(term['pid']) is int and term['pid']==proof['pid'] and term['pid']>1)
        for key in ('elapsed','child_cpu'):
            _need(type(term[key]) in (int,float) and math.isfinite(term[key]) and term[key]>=0)
        _need(term['elapsed']<180)
        mode=profile['mode']
        if mode in ('control-cpu','control-wall'):
            _need(term['exit_code'] is None and type(term['signal']) is int
                  and term['signal']==(9 if mode=='control-cpu' else 14))
            if mode=='control-cpu': _need(29 <= term['child_cpu'] <= 32)
            else: _need(term['elapsed'] >= 119)
        else: _need(type(term['exit_code']) is int and term['exit_code']==0 and term['signal'] is None)
    return report


def _line(value):
    data=canonical(value)+b'\n'
    _need(len(data)<=32768,'stdout budget')
    return data


class NativeSupervisor:
    def install(self,s,h): return signal.signal(s,h)
    def restore(self,s,h): signal.signal(s,h)
    def kill(self,pid): os.kill(pid,signal.SIGKILL)
    def read(self,fd,size): return os.read(fd,size)
    def wait(self,pid):
        child,status,usage=os.wait4(pid,0)
        return child,status,usage.ru_utime+usage.ru_stime
    def close(self,fd): os.close(fd)


ERROR_PHASES = frozenset({'entrypoint','profile','code','handoff','setup','runtime',
                          'control','decode','manifest','pipe','child_terminal','report','cleanup'})


def _child_error_phase(frame):
    if type(frame) is dict and set(frame)=={'error'}:
        value=frame['error']
        if type(value) is str and value in ERROR_PHASES: return value
    return None


def write_all(ops, fd, data):
    view=memoryview(data)
    while view:
        n=ops.write(fd,view)
        _need(type(n) is int and 0 < n <= len(view),'bounded write failure')
        view=view[n:]


class NativeExecution(NativeOps, NativeSupervisor):
    """The only actual syscall/control adapter; never instantiated in pure tests."""
    injected=False
    code_dir='/code';inputs_dir='/inputs';work_dir='/work'
    def environment(self):
        return platform.system(),platform.machine(),os.getpid(),os.geteuid(),os.getegid()
    def profile_bytes(self,pin):
        return read_private('/inputs','profile.json',cap=16384,expected_sha=pin,
                            uid=os.geteuid(),gid=os.getegid())
    def pipe(self): return os.pipe()
    def fork(self): return os.fork()
    def write(self,fd,data): return os.write(fd,data)
    def clock(self): return time.monotonic()
    def block_term_int(self): return signal.pthread_sigmask(signal.SIG_BLOCK,{signal.SIGTERM,signal.SIGINT})
    def set_mask(self,mask): signal.pthread_sigmask(signal.SIG_SETMASK,mask)
    def term_int_unblocked(self):
        return not signal.pthread_sigmask(signal.SIG_BLOCK,set()) & {signal.SIGTERM,signal.SIGINT}
    def child_signals(self,mask):
        for s in (signal.SIGTERM,signal.SIGINT): signal.signal(s,signal.SIG_DFL)
        self.set_mask(set(mask)-{signal.SIGTERM,signal.SIGINT})
    def runtime(self,profile): return collect_runtime(profile)
    def decode(self,profile,proof):
        return execute_decode(profile,code_dir=self.code_dir,inputs_dir=self.inputs_dir,
                              work_dir=self.work_dir,_runtime=proof if self.injected else None)
    def control_as(self):
        import mmap
        try: region=mmap.mmap(-1,536870912+4096)
        except OSError as exc: _need(exc.errno==errno.ENOMEM,'AS control errno')
        else: region.close();raise WorkerError('AS control unexpectedly allocated')
    def control_cpu(self):
        while True: pass
    def control_wall(self):
        while True: signal.pause()
    def exit_child(self,status): os._exit(status)


class OwnedChild:
    """Ownership begins before fork and covers every parent fd/handler/wait path."""
    def __init__(self,ops):
        self.ops=ops;self.child=None;self.reaped=False;self.intervened=False
        self.fds=set();self.handlers={};self.mask=None;self.status=None;self.failed=False
    def stop(self,signum=None,frame=None):
        if self.reaped: return
        self.intervened=True
        if self.child is not None and not getattr(self,'stopped',False):
            self.stopped=True
            try: self.ops.kill(self.child)
            except ProcessLookupError: pass
    def begin(self):
        self.mask=self.ops.block_term_int()
        for s in (signal.SIGTERM,signal.SIGINT): self.handlers[s]=self.ops.install(s,self.stop)
    def pipe(self):
        ends=self.ops.pipe();self.fds.update(ends);return ends
    def close(self,fd):
        self.ops.close(fd);self.fds.discard(fd)
    def handoff(self):
        self.ops.set_mask(set(self.mask)-{signal.SIGTERM,signal.SIGINT})
        _need(self.ops.term_int_unblocked() and not self.intervened,'supervisor signal handoff rejected')
    def _wait(self):
        while True:
            try: return self.ops.wait(self.child)
            except InterruptedError: continue
    def reap(self):
        try:
            result=self._wait()
            _need(result[0]==self.child,'owned child mismatch')
            self.reaped=True;self.status=result
            return result
        except BaseException:
            self.failed=True;self.stop();raise
    def receive(self,fd):
        data=bytearray()
        while True:
            part=self.ops.read(fd,4096)
            if not part: return bytes(data)
            _need(len(part)<=4096 and len(data)+len(part)<=65536,'pipe budget')
            data.extend(part)
    def cleanup(self):
        errors=[]
        if self.child is not None and not self.reaped:
            try: self.stop()
            except BaseException: errors.append(True)
            try: self.reap()
            except BaseException: errors.append(True)
        # Block cancellation while restoring handlers; restore every field even
        # when one fd close or one handler restoration fails.
        try: self.ops.block_term_int()
        except BaseException: errors.append(True)
        for fd in tuple(self.fds):
            try: self.close(fd)
            except BaseException: errors.append(True)
        for s,h in self.handlers.items():
            try: self.ops.restore(s,h)
            except BaseException: errors.append(True)
        if self.mask is not None:
            try: self.ops.set_mask(self.mask)
            except BaseException: errors.append(True)
        _need(not errors,'owned cleanup rejected')


def child_protocol(profile,ops,parent,read_fd,write_fd,gate_read,gate_write,mask,handlers):
    global _PARENT,_ARMS,_SUPERVISOR_SIGNALS_OK
    phase='handoff'
    try:
        ops.close(read_fd);ops.close(gate_write)
        ops.child_signals(mask)
        # Parent publishes this byte only after owned handlers and actual
        # unblocked TERM/INT mask have been checked. No input parsing precedes it.
        _need(ops.read(gate_read,1)==b'G','parent handoff missing')
        ops.close(gate_read)
        _PARENT=parent;_SUPERVISOR_SIGNALS_OK=True
        phase='setup';setup_child(parent,ops);_ARMS=1
        phase='runtime';proof=ops.runtime(profile);validate_runtime(proof,profile)
        write_all(ops,write_fd,_line({'proof':proof}))
        mode=profile['mode']
        if mode in ('preflight','decode'):
            phase='decode';report=ops.decode(profile,proof)
        else:
            phase='control';report=_base_report(profile,proof,ops.injected)
            if mode=='control-as':
                ops.control_as()
                phase='manifest'
                report['manifest']=write_manifest(ops.work_dir,{'format':'av-pack-control-v1','mode':mode,'nonce':profile['nonce']})
            elif mode=='control-cpu': ops.control_cpu();raise WorkerError('CPU control returned')
            else: ops.control_wall();raise WorkerError('wall control returned')
        phase='pipe';write_all(ops,write_fd,_line({'report':report}))
    except Exception:
        try: write_all(ops,write_fd,_line({'error':phase if phase in ERROR_PHASES else 'runtime'}))
        except Exception: pass
        ops.exit_child(70)
        return
    ops.exit_child(0)


def publish_manifest(ops, profile, report, *, work_dir):
    """After mandatory owned cleanup, publish only the pinned private artifact.

    The caller owns final failure handling and success stdout ordering. Completion
    here means accepted writes, not Engine retention or host adoption.
    """
    meta=report['manifest']
    _need(type(meta) is dict and set(meta)=={'file','bytes','sha256'}
          and meta['file']=='manifest.json' and type(meta['bytes']) is int
          and 0 < meta['bytes'] <= MANIFEST_CAP and _hex(meta['sha256'],64))
    raw=read_private(work_dir,'manifest.json',cap=MANIFEST_CAP,
                     expected_size=meta['bytes'],expected_sha=meta['sha256'],
                     uid=profile['uid'],gid=profile['gid'])
    for offset in range(0,len(raw),65536): write_all(ops,2,raw[offset:offset+65536])
    write_all(ops,2,b'\n')


def main(argv=None, *, _ops=None):
    """Actual CLI cannot inject operations. Explicit pure calls are labeled."""
    argv=sys.argv[1:] if argv is None else argv
    ops=NativeExecution() if _ops is None else _ops
    if _ops is not None: ops.injected=True
    owner=None;phase='entrypoint';result=None
    try:
        _need(len(argv)==2 and argv[0]=='/inputs/profile.json' and _hex(argv[1],64),'fixed entrypoint required')
        system,machine,parent,uid,gid=ops.environment()
        _need((system,machine,parent)==('Linux','aarch64',1),'native Linux PID1 required')
        phase='profile';raw=ops.profile_bytes(argv[1])
        _need(len(raw)<=16384 and hashlib.sha256(raw).hexdigest()==argv[1])
        profile=parse_profile(raw);_need(uid==profile['uid'] and gid==profile['gid'])
        phase='code';prepare_runtime(profile,code_dir=ops.code_dir)
        phase='handoff';owner=OwnedChild(ops);owner.begin()
        read_fd,write_fd=owner.pipe();gate_read,gate_write=owner.pipe()
        started=ops.clock();child=ops.fork()
        if child==0:
            child_protocol(profile,ops,parent,read_fd,write_fd,gate_read,gate_write,owner.mask,owner.handlers)
            raise WorkerError('child exit returned')
        _need(type(child) is int and child>1,'invalid owned child')
        owner.child=child
        owner.close(write_fd);owner.close(gate_read)
        owner.handoff();write_all(ops,gate_write,b'G');owner.close(gate_write)
        phase='pipe';data=owner.receive(read_fd);owner.close(read_fd)
        phase='child_terminal';pid,status,cpu=owner.reap()
        elapsed=ops.clock()-started
        phase='pipe'
        lines=[json.loads(x) for x in data.splitlines()]
        # Before runtime admission, a sole safe error is failure evidence only.
        # Unknown/non-string/extra/malformed frames retain the pipe failure phase.
        preproof_error=_child_error_phase(lines[0]) if len(lines)==1 else None
        if preproof_error is not None:
            phase=preproof_error
            raise WorkerError('child pre-proof phase failure')
        _need(lines and set(lines[0])=={'proof'})
        proof=lines[0]['proof'];validate_runtime(proof,profile)
        if len(lines)>1 and set(lines[1])=={'error'}:
            postproof_error=_child_error_phase(lines[1]) if len(lines)==2 else None
            _need(postproof_error is not None,'invalid child error frame')
            phase=postproof_error
            raise WorkerError('child phase failure')
        if profile['mode'] in ('control-cpu','control-wall'):
            _need(len(lines)==1,'unexpected child frames')
        else:
            _need(len(lines)==2 and type(lines[1]) is dict and set(lines[1])=={'report'},'invalid child report frame')
        phase='child_terminal'
        _need(not owner.intervened and expected_status(profile['mode'],status),'child terminal rejected')
        if profile['mode'] in ('control-cpu','control-wall'):
            report=_base_report(profile,proof,ops.injected)
            phase='manifest'
            report['manifest']=write_manifest(ops.work_dir,{'format':'av-pack-control-v1','mode':profile['mode'],'nonce':profile['nonce']})
        else:
            report=lines[1]['report']
        report['injected_for_tests']=ops.injected
        report['terminal']={'pid':pid,'exit_code':os.WEXITSTATUS(status) if os.WIFEXITED(status) else None,
                            'signal':os.WTERMSIG(status) if os.WIFSIGNALED(status) else None,
                            'elapsed':elapsed,'child_cpu':cpu}
        phase='report';validate_report(report,profile,_allow_injected=ops.injected)
        result=_line(report)
    except BaseException:
        result=None
    finally:
        if owner is not None:
            try: owner.cleanup()
            except BaseException: phase='cleanup';result=None
    if result is not None:
        try:
            phase='manifest'
            publish_manifest(ops,profile,report,work_dir=ops.work_dir)
        except BaseException:
            result=None
    if result is None:
        phase=phase if phase in ERROR_PHASES else 'entrypoint'
        try:
            write_all(ops,1,_line({'format':'av-pack-worker-error-v2','outcome':'failed','phase':phase,
                                 'injected_for_tests':ops.injected}))
        except BaseException: pass
        return 70
    try: write_all(ops,1,result)
    except BaseException: return 70
    return 0


if __name__=='__main__':
    raise SystemExit(main())
