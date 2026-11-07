"""Private AV operator. Import is inert; native authority requires approved bootstrap.

All injected paths are pure evidence. Private Python object surgery is outside
this bootstrap trust boundary. No record itself authorizes Root adoption.
"""
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import time
import types

SAFE = 'invalid AV executor operation'
PROFILE_SHA = 'e04523a67281dd24fe66e4e2d168505fe5e5b314c4f3438dba69588e25cde3c9'
API_SHA = '501b68832e88a5b0eb21b480420ea6de70135238d0253fb98aded1ecf91d26fb'
NAMES = frozenset(('create.intent','handle.json','created.json','wall.json',
    'ready.json','start.intent','terminal.json','done.json','abort.intent','kill.intent','failure.json'))

class ExecutorError(ValueError): pass

def need(ok):
    if not ok: raise ExecutorError(SAFE)

def safe(fn):
    def call(*a,**kw):
        try: return fn(*a,**kw)
        except BaseException: pass
        raise ExecutorError(SAFE)
    return call

def sha(raw): return hashlib.sha256(raw).hexdigest()
def canonical(obj):
    return json.dumps(obj,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')
def hex64(v): return type(v) is str and re.fullmatch('[0-9a-f]{64}',v) is not None

def strict(raw,cap=16384):
    need(type(raw) is bytes and 0<len(raw)<=cap)
    def pairs(rows):
        out={}
        for k,v in rows: need(k not in out);out[k]=v
        return out
    def reject(_): raise ExecutorError(SAFE)
    obj=json.loads(raw,object_pairs_hook=pairs,parse_constant=reject)
    need(type(obj) is dict and canonical(obj)==raw)
    return obj

def identity(s):
    return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def close_owned(*descriptors):
    """Attempt each independent release once; an uncertain close is never retried."""
    failed=False
    for fd in descriptors:
        if fd is not None:
            try: os.close(fd)
            except BaseException: failed=True
    need(not failed)

def directory(path):
    need(type(path) is str and path.startswith('/') and all(x not in ('','.','..') for x in path.split('/')[1:]))
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for part in path.split('/')[1:]:
            nxt=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            old,fd=fd,nxt
            os.close(old)
        s=os.fstat(fd)
        need(stat.S_IMODE(s.st_mode)==0o700 and s.st_uid==os.geteuid() and s.st_gid==os.getegid())
        return fd
    except BaseException: close_owned(fd);raise

def read_file(parent,name,cap,expected=None,guard=lambda:None):
    fd=None;d=None
    try:
        guard();d=directory(parent);ds=identity(os.fstat(d))[:5]
        s=os.stat(name,dir_fd=d,follow_symlinks=False)
        need(stat.S_ISREG(s.st_mode) and stat.S_IMODE(s.st_mode)==0o600 and s.st_uid==os.geteuid()
            and s.st_gid==os.getegid() and s.st_nlink==1 and 0<s.st_size<=cap)
        fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=d)
        need(identity(os.fstat(fd))==identity(s));parts=[];size=0
        while True:
            guard();part=os.read(fd,min(65536,cap-size+1));guard()
            if not part: break
            size+=len(part);need(size<=cap);parts.append(part)
        raw=b''.join(parts)
        need(size==s.st_size and identity(os.fstat(fd))==identity(s)
            and identity(os.stat(name,dir_fd=d,follow_symlinks=False))==identity(s))
        current=directory(parent)
        try: need(identity(os.fstat(current))[:5]==ds)
        finally: os.close(current)
        need(expected is None or sha(raw)==expected);guard()
        return raw,identity(s)
    finally:
        close_owned(fd,d)

class Records:
    def __init__(self,path,guard=lambda:None):
        self.path,self.guard=path,guard
        fd=directory(path)
        try: self.directory_identity=identity(os.fstat(fd))[:5]
        finally: os.close(fd)
    def check(self):
        self.guard();fd=directory(self.path)
        try: need(identity(os.fstat(fd))[:5]==self.directory_identity)
        finally: os.close(fd)
    @safe
    def read(self,name,pin=None):
        need(name in NAMES);self.check()
        raw,ident=read_file(self.path,name,16384,pin,self.guard)
        self.check();return strict(raw),sha(raw),ident
    @safe
    def exists(self,name):
        need(name in NAMES);self.check();fd=directory(self.path)
        try:
            try: os.stat(name,dir_fd=fd,follow_symlinks=False);return True
            except FileNotFoundError: return False
        finally: os.close(fd)
    @safe
    def write(self,name,value):
        need(name in NAMES);raw=canonical(value);need(0<len(raw)<=16384);self.check()
        d=directory(self.path);fd=None
        try:
            fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=d)
            view=memoryview(raw)
            while view:
                self.guard();n=os.write(fd,view);need(type(n) is int and 0<n<=len(view));view=view[n:]
            os.fsync(fd);written_identity=identity(os.fstat(fd));os.fsync(d);self.guard()
        finally:
            close_owned(fd,d)
        need(self.read(name,sha(raw))[2]==written_identity);return sha(raw)

@safe
def bootstrap_stage(stage,descriptor_sha):
    need(hex64(descriptor_sha))
    raw,_=read_file(stage,'stage.json',16384,descriptor_sha)
    desc=strict(raw)
    need(set(desc)=={'format','nonce','profile_sha256','code_sha256','host_sha256'}
         and desc['format']=='av-pack-host-stage-v1'
         and desc['host_sha256']['pack_profile.py']==PROFILE_SHA)
    src,_=read_file(stage+'/host','pack_profile.py',262144,PROFILE_SHA)
    key='_av_executor_profile_'+PROFILE_SHA
    old=dict(sys.modules)
    try:
        mod=types.ModuleType(key);sys.modules[key]=mod
        exec(compile(src,'<approved-profile>','exec',dont_inherit=True),mod.__dict__)
        return mod.prepare_stage(stage,expected_descriptor_sha256=descriptor_sha)
    except BaseException:
        for k in list(sys.modules):
            if k.startswith(('_av_host_','_av_verified_pack_runtime_','_av_executor_profile_')):
                if k in old: sys.modules[k]=old[k]
                else: sys.modules.pop(k,None)
        raise

def profile_module(): return sys.modules['_av_executor_profile_'+PROFILE_SHA]

@safe
def boot_identity(*,_platform=None,_sysctl=None):
    platform=sys.platform if _platform is None else _platform
    if platform=='darwin':
        import ctypes as c
        class Timeval(c.Structure): _fields_=[('tv_sec',c.c_long),('tv_usec',c.c_int32)]
        need(c.sizeof(c.c_long)==8 and c.sizeof(c.c_int32)==4 and c.sizeof(Timeval)==16)
        f=c.CDLL(None,use_errno=True).sysctlbyname if _sysctl is None else _sysctl
        f.argtypes=[c.c_char_p,c.c_void_p,c.POINTER(c.c_size_t),c.c_void_p,c.c_size_t];f.restype=c.c_int
        value=Timeval();size=c.c_size_t(16)
        need(f(b'kern.boottime',c.byref(value),c.byref(size),None,0)==0 and size.value==16
            and value.tv_sec>0 and 0<=value.tv_usec<=999999)
        return sha(canonical(dict(seconds=value.tv_sec,microseconds=value.tv_usec)))
    need(platform=='linux' and _sysctl is None)
    with open('/proc/sys/kernel/random/boot_id','rb') as f: raw=f.read(129)
    need(re.fullmatch(b'[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}\n?',raw) is not None)
    return sha(raw.rstrip(b'\n'))

@safe
def authenticate_operator(path,pin,guard=lambda:None):
    need(hex64(pin) and type(path) is str and Path(path).name=='pack_executor.py')
    parent=str(Path(path).parent);fd=directory(parent)
    try: need(set(os.listdir(fd))<= {'pack_executor.py','pins.json'})
    finally: os.close(fd)
    return read_file(parent,'pack_executor.py',262144,pin,guard)[1]

def _capacity(stage):
    s=os.statvfs(stage);return s.f_bavail*s.f_frsize

def native_dependencies(prepared):
    native_interpreter()
    return dict(clock=time.monotonic,boot=boot_identity,sleep=time.sleep,capacity=_capacity,
        api=lambda p,r:p.api.APIClient(p.profile['nonce'],r.create_bytes,http_module=p.http),
        launch=launch_guardian)

def stdout_report(raw,p,allow):
    need(type(raw) is bytes and len(raw)<=65536);offset=frames=total=0;parts=[]
    while offset<len(raw):
        need(len(raw)-offset>=8);header=raw[offset:offset+8];offset+=8;frames+=1
        need(frames<=4096 and header[:4]==b'\x01\0\0\0')
        size=int.from_bytes(header[4:],'big');total+=size
        need(total<=32768 and size<=len(raw)-offset)
        parts.append(raw[offset:offset+size]);offset+=size
    payload=b''.join(parts);need(frames>0 and payload.endswith(b'\n'))
    report=strict(payload[:-1],32768)
    p.validate_report(report,p.profile,_allow_injected=allow)
    return report

@safe
def admit_guardian_record(record,expected,*,prior_injected=False,allow_injected=False):
    """Exact bound evidence plus monotonic provenance; absence is not native."""
    need(type(record) is dict and type(prior_injected) is bool and type(allow_injected) is bool)
    flag=record.get('injected_for_tests')
    need(type(flag) is bool and canonical(record)==canonical(dict(expected,injected_for_tests=flag)))
    need(not prior_injected or flag)
    need(not flag or allow_injected)
    return flag

@safe
def execute_once(prepared,*,operator_path,operator_sha256,_deps=None,_allow_injected=False):
    injected=_deps is not None
    need(type(_allow_injected) is bool and (not injected or _allow_injected))
    deps=native_dependencies(prepared) if _deps is None else dict(_deps)
    need(set(deps)=={'clock','boot','sleep','capacity','api','launch'} and all(callable(v) for v in deps.values()))
    clock,boot,sleep=deps['clock'],deps['boot'],deps['sleep']
    pm=profile_module();pm.recheck_stage(prepared,guard=lambda:None)
    operator_identity=authenticate_operator(operator_path,operator_sha256)
    recipe=pm.make_recipe(prepared);records=Records(prepared.stage+'/results')
    need(not records.exists('create.intent'))
    need(deps['capacity'](prepared.stage)>=2415919104)
    api=deps['api'](prepared,recipe)
    injected=injected or api.injected_for_tests
    need(not injected or _allow_injected)
    counters={};pins={};identities={};deadline=None;boot_pin=None;proc=None;started=False;bindings=None
    def guard():
        if deadline is not None:
            now=clock();need(type(now) in (int,float) and math.isfinite(now) and now<deadline)
            need(boot()==boot_pin)
    def put(name,value):
        pins[name]=records.write(name,value);identities[name]=records.read(name,pins[name])[2]
        return pins[name]
    def get(name,pin=None):
        value,digest,ident=records.read(name,pin)
        pins[name]=digest;identities[name]=ident;return value
    def fence():
        guard()
        need(not records.exists('abort.intent') and not records.exists('kill.intent'))
        for name,pin in pins.items(): need(records.read(name,pin)[2]==identities[name])
        need(authenticate_operator(operator_path,operator_sha256,guard)==operator_identity)
        pm.recheck_stage(prepared,guard=guard);guard()
    def call(name):
        nonlocal injected
        guard();result=api.call(getattr(prepared.api.Operation,name));guard()
        injected=injected or result.injected_for_tests;need(not injected or _allow_injected)
        row=counters.setdefault(name.lower(),dict(calls=0,wire_bytes=0,overhead_bytes=0,header_bytes=0,header_fields=0,chunks=0))
        row['calls']+=1
        for field in ('wire_bytes','overhead_bytes','header_bytes','header_fields','chunks'):
            value=vars(result.counters).get(field,0)
            need(type(value) is int and value>=0);row[field]+=value
        return result.value
    case=dict(stage=prepared.descriptor_sha256,profile=sha(prepared.profile_bytes),recipe=recipe.sha256,
              operator=operator_sha256,sources=sha(canonical(prepared.descriptor)),nonce=prepared.profile['nonce'],image=prepared.profile['image'])
    try:
        pm.recheck_stage(prepared,guard=guard)
        pm.validate_daemon(call('INFO'));pm.validate_image(call('IMAGE'))
        put('create.intent',case)
        response=call('CREATE');cid=response.get('Id');need(hex64(cid))
        put('handle.json',dict(case,cid=cid,name=recipe.name))
        need(set(response)=={'Id','Warnings'} and (response['Warnings'] is None or response['Warnings']==[]))
        api.bind_created(cid)
        created=pm.validate_container(call('INSPECT'),cid,prepared,recipe,phase='created')
        put('created.json',created)
        boot_pin=boot();need(hex64(boot_pin));now=clock()
        need(type(now) in (int,float) and math.isfinite(now) and now>=0)
        deadline=now+180.0;records.guard=guard
        put('wall.json',dict(case,cid=cid,seconds=180,deadline=deadline,boot=boot_pin,
                             handle=pins['handle.json'],created=pins['created.json']))
        bindings=dict(case,cid=cid,deadline=deadline,boot=boot_pin,wall=pins['wall.json'],
                      handle=pins['handle.json'],created=pins['created.json'])
        api.bind_deadline(deadline)
        proc=deps['launch'](bindings,records,prepared=prepared,operator_path=operator_path,operator_sha256=operator_sha256)
        need(type(proc.pid) is int and proc.pid>0)
        for _ in range(181):
            guard();need(proc.poll() is None)
            if records.exists('ready.json'): break
            sleep(min(1.0,max(0.0,deadline-clock())))
        ready=get('ready.json')
        guardian_injected=admit_guardian_record(ready,dict(bindings,pid=proc.pid),allow_injected=_allow_injected)
        injected=injected or guardian_injected
        need(proc.poll() is None);fence()
        put('start.intent',dict(bindings,pid=proc.pid));started=True
        call('START')
        terminal=None
        for _ in range(181):
            facts=pm.validate_container(call('INSPECT'),cid,prepared,recipe,phase='observe')
            need(facts['configuration_sha256']==created['configuration_sha256'] and not facts['state']['OOMKilled'])
            if facts['state']['Status']=='exited':
                need(facts['state']['ExitCode']==0 and facts['state']['OOMKilled'] is False);terminal=facts;break
            sleep(min(1.0,max(0.0,deadline-clock())))
        need(terminal is not None)
        put('terminal.json',dict(bindings,facts=terminal,sample=clock()))
        for _ in range(181):
            guard()
            if proc.poll() is not None: break
            sleep(min(1.0,max(0.0,deadline-clock())))
        need(proc.poll()==0);need(proc.wait(timeout=max(0.0,deadline-clock()))==0);guard()
        done=get('done.json')
        guardian_injected=admit_guardian_record(done,dict(bindings,pid=proc.pid,outcome='normal',terminal=terminal),
            prior_injected=guardian_injected,allow_injected=_allow_injected)
        injected=injected or guardian_injected;fence()
        report=stdout_report(call('STDOUT'),prepared,_allow_injected)
        injected=injected or report['injected_for_tests'];api.admit_stdout();guard()
        artifact=prepared.artifact.decode_manifest_logs(call('ARTIFACT'),profile=prepared.profile,report=report,
                    validator=prepared.validate_report,_allow_injected=_allow_injected)
        fence()
        stored=prepared.artifact.store_manifest(artifact.canonical_bytes,expected_size=report['manifest']['bytes'],
                expected_sha256=report['manifest']['sha256'],destination=Path(prepared.stage)/'results/manifest.json')
        guard()
        final=pm.validate_container(call('INSPECT'),cid,prepared,recipe,phase='observe');need(final==terminal)
        raw,ident=read_file(prepared.stage+'/results','manifest.json',16777216,stored['sha256'],guard)
        # Artifact helper uses size before uid/gid in its identity schema.
        need((ident[0],ident[1],ident[2],ident[6],ident[3],ident[4],ident[5],ident[7],ident[8])==tuple(stored['identity'])
             and len(raw)==stored['bytes'] and raw==artifact.canonical_bytes)
        fence()
        receipt=canonical(dict(scope='av-single-case-private-facts-v1',injected_for_tests=injected,cid=cid,
            mode=prepared.profile['mode'],case=case,operator_identity=operator_identity,guardian_injected_for_tests=guardian_injected,
            artifact=stored,report=report,counters=counters,
            transport=artifact.transport,deadline=deadline,boot=boot_pin,sample=clock(),records=pins,record_identities=identities))
        need(len(receipt)<=16384);guard();return receipt
    except BaseException:
        if (started or proc is not None) and bindings is not None:
            try:
                cleanup=Records(prepared.stage+'/results')
                if not cleanup.exists('abort.intent'): cleanup.write('abort.intent',dict(bindings,reason='controller-failed'))
            except BaseException: pass
            if proc is not None:
                try: proc.wait(timeout=10.0)
                except BaseException: pass
        raise

@dataclass(frozen=True)
class _GuardianRequest:
    method: str
    path: str
    status: int
    content_types: tuple
    limits: object
    body: bytes = b''

class GuardianAPI:
    """Pinned internal _OwnedCall interface; never an APIClient bypass.

    Consumes only _http/_clock/_socket_factory/_timer_factory/_poisoned.
    Independent ownership state allows a poisoned inspect to enter stop-only.
    """
    def __init__(self,prepared,cid,deadline,*,socket_factory=None,timer_factory=None,clock=None):
        need(hex64(cid) and sha(prepared.source_bytes['pack_api.py'])==API_SHA)
        self._cid=cid;self._deadline=deadline;self._api=prepared.api;self._http=prepared.http
        self._api._http(self._http)
        self._clock=time.monotonic if clock is None else clock
        self._socket_factory=self._api.socket.socket if socket_factory is None else socket_factory
        self._timer_factory=self._api.threading.Timer if timer_factory is None else timer_factory
        self._injected=any(x is not None for x in (socket_factory,timer_factory,clock))
        self._read_failed=False;self._killed=False;self._post=False;self._poisoned=False
    @property
    def injected_for_tests(self): return self._injected
    @safe
    def _exchange(self,stop=False,post=False):
        if stop:
            need(not self._killed);self._killed=True
        elif post:
            need(self._killed and not self._post);self._post=True
        else:
            need(not self._read_failed and not self._killed);self._read_failed=True
        now=self._clock();end=now+5 if stop or post else min(now+5,self._deadline)
        need(math.isfinite(now) and now<end)
        request=_GuardianRequest('POST' if stop else 'GET','/containers/'+self._cid+
            ('/kill?signal=KILL' if stop else '/json'),204 if stop else 200,('application/json',),self._http.METADATA_LIMITS)
        active=self._api._OwnedCall(self,end)
        try:
            response=active.run(request)
            need(type(response.body) is bytes and len(response.body)<=request.limits.body_bytes
                 and response.status==request.status)
            names=('wire_bytes','overhead_bytes','header_bytes','header_fields','chunks')
            for n in names: need(type(getattr(response,n)) is int and 0<=getattr(response,n)<=getattr(request.limits,n))
            need(response.wire_bytes==response.overhead_bytes+len(response.body)
                and response.header_bytes<=response.overhead_bytes and response.framing in ('none','content-length','chunked'))
            if stop: need(response.body==b'');value=None
            else:
                value=self._api._json(response.body,self._api._JSON_MAX);need(value.get('Id')==self._cid)
            active.guard()
            if not stop and not post: self._read_failed=False
            return self._api.Result(self._api.Operation.INSPECT,value,
                self._api.WireCounters(*(getattr(response,n) for n in names),response.framing),self.injected_for_tests)
        except BaseException:
            active.cancel()
            if not stop and not post: self._read_failed=True
            raise
    def inspect(self): return self._exchange()
    def kill(self): return self._exchange(stop=True)
    def inspect_after_stop(self): return self._exchange(post=True)

# The initial bootstrap is intentionally self-contained: it authenticates the
# outer operator before compiling it. Initial profile utility authentication is
# separate and occurs only inside the now authenticated outer operator.
BOOTSTRAP = r"""
import os,sys,stat,hashlib,types,re
def fid(s):
    return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
try:
    if not (len(sys.argv)==6 and sys.argv[3]=='execute' or len(sys.argv)==9 and sys.argv[3]=='guardian'): raise ValueError()
    path,pin=sys.argv[1:3]
    if not re.fullmatch('[0-9a-f]{64}',pin) or not path.startswith('/') or path.split('/')[-1]!='pack_executor.py': raise ValueError()
    parts=path.split('/')[1:]
    if any(x in ('','.','..') for x in parts): raise ValueError()
    d=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            n=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=d);old,d=d,n;os.close(old)
        s=os.fstat(d)
        if stat.S_IMODE(s.st_mode)!=0o700 or s.st_uid!=os.geteuid() or s.st_gid!=os.getegid(): raise ValueError()
        if not set(os.listdir(d))<={'pack_executor.py','pins.json'}: raise ValueError()
        f=os.open('pack_executor.py',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=d)
        try:
            s=os.fstat(f)
            if not stat.S_ISREG(s.st_mode) or stat.S_IMODE(s.st_mode)!=0o600 or s.st_nlink!=1 or s.st_uid!=os.geteuid() or s.st_gid!=os.getegid() or not 0<s.st_size<=262144: raise ValueError()
            raw=b''
            while len(raw)<=262144:
                part=os.read(f,min(65536,262145-len(raw)))
                if not part: break
                raw+=part
            if len(raw)!=s.st_size or hashlib.sha256(raw).hexdigest()!=pin or fid(os.fstat(f))!=fid(s) or fid(os.stat('pack_executor.py',dir_fd=d,follow_symlinks=False))!=fid(s): raise ValueError()
        finally: os.close(f)
    finally: os.close(d)
    key='_av_executor_'+pin;m=types.ModuleType(key);sys.modules[key]=m
    exec(compile(raw,'<approved-operator>','exec',dont_inherit=True),m.__dict__)
    result=m.main([sys.argv[3],*sys.argv[4:6],path,pin,*sys.argv[6:]])
except BaseException:
    result=70
sys.exit(result)
"""
# Captured while the authenticated initial bootstrap compiles this operator.
# Root must preflight and pin this path and its binary before native execution.
# These pure compatibility/drift checks do not authenticate an executable.
PYTHON = sys.executable

@safe
def native_interpreter():
    need(sys.implementation.name=='cpython' and sys.version_info>=(3,12))
    need(type(PYTHON) is str and bool(PYTHON) and os.path.isabs(PYTHON)
        and type(sys.executable) is str and PYTHON==sys.executable)
    need(sys.flags.isolated==1 and sys.flags.dont_write_bytecode==1)
    return PYTHON

@safe
def launch_guardian(bindings,records,*,prepared,operator_path,operator_sha256,_popen=None):
    python=native_interpreter()
    import subprocess
    authenticate_operator(operator_path,operator_sha256)
    popen=subprocess.Popen if _popen is None else _popen
    argv=[python,'-I','-B','-c',BOOTSTRAP,operator_path,operator_sha256,'guardian',prepared.stage,
          prepared.descriptor_sha256,bindings['handle'],bindings['wall'],bindings['created']]
    return popen(argv,shell=False,start_new_session=True,close_fds=True,stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,cwd=str(Path(operator_path).parent),env={'LANG':'C','LC_ALL':'C','PATH':'/usr/bin:/bin'})

@safe
def guardian_main(stage,descriptor_sha,operator_path,operator_sha256,handle_sha,wall_sha,created_sha,
                  *,_deps=None,_allow_injected=False):
    injected=_deps is not None
    need(type(_allow_injected) is bool and (not injected or _allow_injected))
    if _deps is None: native_interpreter()
    prepared=bootstrap_stage(stage,descriptor_sha);pm=profile_module()
    operator_identity=authenticate_operator(operator_path,operator_sha256)
    records=Records(stage+'/results')
    handle,_,hi=records.read('handle.json',handle_sha);wall,_,wi=records.read('wall.json',wall_sha)
    created,_,ci=records.read('created.json',created_sha)
    recipe=pm.make_recipe(prepared);cid=handle['cid'];need(hex64(cid))
    case=dict(stage=descriptor_sha,profile=sha(prepared.profile_bytes),recipe=recipe.sha256,
        operator=operator_sha256,sources=sha(canonical(prepared.descriptor)),nonce=prepared.profile['nonce'],image=prepared.profile['image'])
    need(handle==dict(case,cid=cid,name=recipe.name))
    deadline=wall['deadline'];boot_pin=wall['boot']
    need(type(deadline) in (int,float) and math.isfinite(deadline) and deadline>0 and hex64(boot_pin)
        and wall==dict(case,cid=cid,seconds=180,deadline=deadline,boot=boot_pin,handle=handle_sha,created=created_sha))
    bindings=dict(case,cid=cid,deadline=deadline,boot=boot_pin,wall=wall_sha,handle=handle_sha,created=created_sha)
    deps=dict(clock=time.monotonic,boot=boot_identity,sleep=time.sleep,pid=os.getpid,
        api=lambda p,c,d:GuardianAPI(p,c,d)) if _deps is None else dict(_deps)
    need(set(deps)=={'clock','boot','sleep','pid','api'})
    clock,boot,sleep=deps['clock'],deps['boot'],deps['sleep'];pid=deps['pid']()
    need(type(pid) is int and pid>0)
    api=deps['api'](prepared,cid,deadline)
    need(type(api.injected_for_tests) is bool)
    injected=injected or api.injected_for_tests
    need(not injected or _allow_injected)
    def admit_result(result):
        nonlocal injected
        need(type(result.injected_for_tests) is bool)
        injected=injected or result.injected_for_tests
        need(not injected or _allow_injected)
        return result.value
    def guard():
        need(clock()<deadline and boot()==boot_pin)
        need(records.read('handle.json',handle_sha)[2]==hi and records.read('wall.json',wall_sha)[2]==wi
            and records.read('created.json',created_sha)[2]==ci)
        need(authenticate_operator(operator_path,operator_sha256)==operator_identity)
        pm.recheck_stage(prepared,guard=lambda:need(clock()<deadline and boot()==boot_pin))
    poisoned=False;observed_running=False
    try:
        guard();records.write('ready.json',dict(bindings,pid=pid,injected_for_tests=injected));guard()
        for _ in range(181):
            guard()
            if records.exists('abort.intent'):
                need(records.read('abort.intent')[0]==dict(bindings,reason='controller-failed'))
                break
            if records.exists('kill.intent'): break
            if not poisoned:
                try:
                    raw=admit_result(api.inspect())
                    phase='created' if raw.get('State',{}).get('Status')=='created' else 'observe'
                    facts=pm.validate_container(raw,cid,prepared,recipe,phase=phase)
                    need(facts['configuration_sha256']==created['configuration_sha256'] and not facts['state']['OOMKilled'])
                    if phase=='created': need(not observed_running)
                    elif facts['state']['Status']=='running': observed_running=True
                    elif facts['state']['Status']=='exited':
                        need(records.exists('start.intent') and facts['state']['ExitCode']==0 and not facts['state']['OOMKilled'])
                        need(records.read('start.intent')[0]==dict(bindings,pid=pid));guard()
                        need(not records.exists('abort.intent') and not records.exists('kill.intent'))
                        records.write('done.json',dict(bindings,pid=pid,outcome='normal',terminal=facts,injected_for_tests=injected));guard()
                        return 0
                except BaseException: poisoned=True
            sleep(min(1.0,max(0.0,deadline-clock())))
    except BaseException: pass
    # Durable authority is consumed before transport, across guardian instances.
    # If this write/flush/reread fails, a partial or existing claim is quarantine,
    # never permission for a new send. An unwritable record means no stop claim.
    claimed=False;outcome='kill-unclaimed'
    try:
        records.write('kill.intent',dict(bindings,pid=pid,reason='guardian-stop',injected_for_tests=injected))
        claimed=True
    except BaseException: pass
    if claimed:
        outcome='kill-unknown'
        try:
            admit_result(api.kill());outcome='kill-known'
            try: admit_result(api.inspect_after_stop())
            except BaseException: pass
        except BaseException: pass
    try: records.write('done.json',dict(bindings,pid=pid,outcome=outcome,injected_for_tests=injected))
    except BaseException: pass
    return 70

def publication_guard(prepared,path,pin,receipt,deps):
    dto=strict(receipt)
    need(deps['clock']()<dto['deadline'] and deps['boot']()==dto['boot'])
    def guard(): need(deps['clock']()<dto['deadline'] and deps['boot']()==dto['boot'])
    records=Records(prepared.stage+'/results',guard)
    need(not records.exists('abort.intent') and not records.exists('kill.intent'))
    for name,digest in dto['records'].items():
        need(list(records.read(name,digest)[2])==dto['record_identities'][name])
    need(list(authenticate_operator(path,pin,guard))==dto['operator_identity'])
    profile_module().recheck_stage(prepared,guard=guard)
    meta=dto['artifact'];raw,ident=read_file(prepared.stage+'/results','manifest.json',16777216,meta['sha256'],guard)
    need(len(raw)==meta['bytes'] and [ident[0],ident[1],ident[2],ident[6],ident[3],ident[4],ident[5],ident[7],ident[8]]==meta['identity'])
    guard()

def main(argv,*,_deps=None,_allow_injected=False,_write=None):
    """Bounded CLI. Parent collector also requires exit zero after publication."""
    prepared=None;receipt=None
    try:
        need(type(argv) is list and all(type(x) is str for x in argv))
        if len(argv)==8 and argv[0]=='guardian':
            _,stage,descriptor,path,pin,handle,wall,created=argv
            need(all(hex64(x) for x in (descriptor,pin,handle,wall,created)))
            return guardian_main(stage,descriptor,path,pin,handle,wall,created,_deps=_deps,_allow_injected=_allow_injected)
        need(len(argv)==5 and argv[0]=='execute')
        _,stage,descriptor,path,pin=argv
        need(_write is None or _allow_injected is True)
        prepared=bootstrap_stage(stage,descriptor)
        deps=native_dependencies(prepared) if _deps is None else dict(_deps)
        # A supplied writer is injection even when all execution factories default.
        execution_deps=deps if _deps is not None or _write is not None else None
        receipt=execute_once(prepared,operator_path=path,operator_sha256=pin,_deps=execution_deps,_allow_injected=_allow_injected)
        writer=os.write if _write is None else _write
        view=memoryview(receipt+b'\n')
        while view:
            publication_guard(prepared,path,pin,receipt,deps)
            count=writer(1,view);need(type(count) is int and 0<count<=len(view));view=view[count:]
            publication_guard(prepared,path,pin,receipt,deps)
        return 0
    except BaseException:
        if prepared is not None and receipt is not None:
            try:
                r=Records(prepared.stage+'/results');wall=r.read('wall.json')[0]
                binding={k:v for k,v in wall.items() if k!='seconds'}
                binding['wall']=r.read('wall.json')[1]
                r.write('abort.intent',dict(binding,reason='controller-failed'))
            except BaseException: pass
        return 70
