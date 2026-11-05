"""Offline synthetic tests. Native syscall/resource operations are always faked."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import zlib

import pytest

ROOT = Path(__file__).resolve().parents[1] / 'scripts' / 'av_sources'


def module():
    path = ROOT / 'pack_worker.py'
    assert path.exists(), 'missing offline pack worker'
    spec = importlib.util.spec_from_file_location('av_pack_worker_test', path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    if hasattr(mod,'prepare_runtime'):
        with tempfile.TemporaryDirectory() as directory:
            for name in mod.CODE_NAMES:
                p=Path(directory)/name;p.write_bytes((ROOT/name).read_bytes());p.chmod(0o600)
            mod.prepare_runtime(profile(),code_dir=Path(directory).resolve())
    return mod


def oid(kind, raw):
    return hashlib.sha1(kind.encode() + b' ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()


def profile(mode='preflight'):
    return dict(format='av-pack-worker-profile-v1', nonce='a' * 32,
                image='sha256:3f9c7f75b916e8279e24136d7a57769aba9e193715be52570417698c52d6eed0',
                platform='linux/arm64/v8', uid=os.geteuid() or 1000,
                gid=os.getegid() or 1000, mode=mode,
                code_sha256={n: hashlib.sha256((ROOT / n).read_bytes()).hexdigest()
                             for n in ('pack_worker.py', 'pack_runtime.py', 'pack_store.py', 'git_objects.py')},
                pack=None)


def stage(tmp_path):
    w = module()
    dirs = [tmp_path / n for n in ('code', 'inputs', 'work')]
    for p in dirs:
        p.mkdir(mode=0o700)
    code, inputs, work = dirs
    for n in ('pack_worker.py', 'pack_runtime.py', 'pack_store.py', 'git_objects.py'):
        (code / n).write_bytes((ROOT / n).read_bytes())
        (code / n).chmod(0o600)
    blob = b'synthetic offline content\n'
    blob_oid = oid('blob', blob)
    link_oid = 'b' * 40
    tree = b'100644 file.txt\0' + bytes.fromhex(blob_oid) + b'160000 sub\0' + bytes.fromhex(link_oid)
    tree_oid = oid('tree', tree)
    commit = (f'tree {tree_oid}\nauthor A <a@b> 1 +0000\ncommitter A <a@b> 1 +0000\n\nsynthetic\n').encode()
    commit_oid = oid('commit', commit)
    payload = bytearray(struct.pack('>4sII', b'PACK', 2, 3))
    for kind, raw in ((1, commit), (2, tree), (3, blob)):
        size = len(raw)
        first = kind << 4 | (size & 15)
        size >>= 4
        payload.append(first | (128 if size else 0))
        while size:
            payload.append((size & 127) | (128 if size >> 7 else 0))
            size >>= 7
        payload.extend(zlib.compress(raw))
    payload.extend(hashlib.sha1(payload).digest())
    (inputs / 'source.pack').write_bytes(payload)
    (inputs / 'source.pack').chmod(0o600)
    p = profile('decode')
    p['pack'] = dict(file='source.pack', bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest(),
                     commit_oid=commit_oid, expected_tree_oid=tree_oid,
                     required_gitlink_oid=link_oid,
                     required_entry=dict(path='file.txt', oid=blob_oid, bytes=len(blob)))
    return w, p, code, inputs, work


def test_missing_worker_and_inert_import(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('native side effect during import')
    monkeypatch.setattr(os, 'fork', forbidden)
    w = module()
    assert callable(w.execute_decode)


def test_synthetic_complete_pipeline(tmp_path):
    w, p, code, inputs, work = stage(tmp_path)
    result = w.execute_decode(p, code_dir=code, inputs_dir=inputs, work_dir=work,
                              _runtime=w.synthetic_runtime(p))
    raw = (work / 'manifest.json').read_bytes()
    manifest = json.loads(raw)
    assert manifest['tree_oid'] == p['pack']['expected_tree_oid']
    assert len(manifest['entries']) == 1 and len(manifest['gitlinks']) == 1
    assert result['manifest'] == dict(file='manifest.json', bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    assert result['matched_entry']['sha256'] == hashlib.sha256(b'synthetic offline content\n').hexdigest()
    assert result['injected_for_tests'] is True
    assert all(result[k] is False for k in w.FALSE_FLAGS)


@pytest.mark.parametrize('field,value', [('mode','other'),('uid',True),('gid',0),('nonce','A'*32),('image','latest'),('platform','linux/amd64'),('format',1),('extra',1)])
def test_profile_rejects(tmp_path, field, value):
    w = module()
    p = profile()
    p[field] = value
    with pytest.raises(w.WorkerError):
        w.validate_profile(p)


@pytest.mark.parametrize('mutation', ['tree','entry','gitlink','code','pack','permissions','link','output'])
def test_pipeline_fail_closed(tmp_path, mutation):
    w, p, code, inputs, work = stage(tmp_path)
    if mutation == 'tree': p['pack']['expected_tree_oid'] = 'c' * 40
    if mutation == 'entry': p['pack']['required_entry']['bytes'] += 1
    if mutation == 'gitlink': p['pack']['required_gitlink_oid'] = 'c' * 40
    if mutation == 'code': (code / 'pack_store.py').write_bytes(b'raise Exception("must never exec")')
    if mutation == 'pack': (inputs / 'source.pack').write_bytes(b'invalid')
    if mutation == 'permissions': (inputs / 'source.pack').chmod(0o644)
    if mutation == 'link':
        (code / 'pack_store.py').unlink()
        (code / 'pack_store.py').symlink_to(ROOT / 'pack_store.py')
    if mutation == 'output': (work / 'manifest.json').write_text('preserve')
    with pytest.raises((w.WorkerError, ValueError, OSError)):
        w.execute_decode(p, code_dir=code, inputs_dir=inputs, work_dir=work, _runtime=w.synthetic_runtime(p))
    if mutation == 'output': assert (work / 'manifest.json').read_text() == 'preserve'
    else: assert not (work / 'manifest.json').exists()


def test_json_duplicate_nan_and_size():
    w = module()
    for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b' ' * 16385):
        with pytest.raises(w.WorkerError): w.parse_profile(raw)


@pytest.mark.parametrize('field,value', [('uid',0),('pid',1),('ppid',5),('system','Darwin'),('machine','x86_64'),('timer_remaining',0),('timer_interval',1),('timer_arms',2),('caps',['1']*5),('no_new_privs',0),('seccomp',0),('interfaces',['lo','eth0']),('routes',['bad']),('cgroup_path','/../bad'),('cgroup_readonly',False),('memory_max','max'),('memory_swap_max',1),('pids_max',17),('cpu_max',[300001,100000]),('mounts_validated',False),('groups',[0]),('cgroup_scope','host-proven'),('supervisor_cgroup_namespace','cgroup:[999]')])
def test_runtime_rejects_each_wrong_field(field, value):
    w = module()
    p = profile()
    proof = w.synthetic_runtime(p)
    proof[field] = value
    with pytest.raises(w.WorkerError): w.validate_runtime(proof, p)


def test_runtime_exact_keys_and_limits():
    w = module()
    p = profile()
    proof = w.synthetic_runtime(p)
    w.validate_runtime(proof, p)
    for key in proof:
        altered = copy.deepcopy(proof)
        del altered[key]
        with pytest.raises(w.WorkerError): w.validate_runtime(altered, p)
    for key in proof['limits']:
        altered = copy.deepcopy(proof)
        altered['limits'][key] = [-1,-1]
        with pytest.raises(w.WorkerError): w.validate_runtime(altered, p)


def test_child_setup_order_and_timer_no_renewal():
    w = module()
    class Fake:
        def __init__(self): self.calls=[]
        def getpid(self): return 2
        def getppid(self): return 1
        def limits(self): self.calls.append('limits'); return w.LIMITS
        def default_alarm(self): self.calls.append('default')
        def unblock_alarm(self): self.calls.append('unblock')
        def alarm_state(self): self.calls.append('state');return ('default',False,False)
        def arm(self, seconds, interval): self.calls.append(('arm', seconds, interval))
        def timer(self): self.calls.append('gettimer'); return (119.9,0)
    fake=Fake()
    assert w.setup_child(1, fake) == (119.9,0)
    assert fake.calls == ['limits','default','unblock','state',('arm',120,0),'gettimer']


def test_stream_failure_removes_partial(tmp_path, monkeypatch):
    w = module()
    tmp_path.chmod(0o700)
    def fail(fd): raise OSError('private raw error')
    monkeypatch.setattr(w.os, 'fsync', fail)
    with pytest.raises(OSError): w.write_manifest(tmp_path, {'x':1})
    assert not (tmp_path/'manifest.json').exists()
    with pytest.raises(w.WorkerError): w.write_manifest(tmp_path, {'x':'a'*100}, _budget=16)
    assert not (tmp_path/'manifest.json').exists()


def test_wait_status_modes():
    w = module()
    for mode, status, good in [('decode',0,True),('preflight',9,False),('control-as',0,True),('control-cpu',9,True),('control-wall',14,True),('decode',14,False),('control-cpu',24,False)]:
        assert w.expected_status(mode,status) is good


def test_shared_report_validator_rejects_injection_and_drift(tmp_path):
    w,p,code,inputs,work=stage(tmp_path)
    report=w.execute_decode(p,code_dir=code,inputs_dir=inputs,work_dir=work,_runtime=w.synthetic_runtime(p))
    w.validate_report(report,p,_allow_injected=True)
    with pytest.raises(w.WorkerError): w.validate_report(report,p)
    for field,value in [('nonce','b'*32),('mode','preflight'),('outcome','failed'),('counts',{}),('manifest',{'file':'manifest.json','bytes':1,'sha256':'x'}),('injected_for_tests',False),('signed_provenance_verified',True)]:
        altered=copy.deepcopy(report);altered[field]=value
        with pytest.raises(w.WorkerError): w.validate_report(altered,p)


def test_native_limits_never_raise_lower_inherited_limit(monkeypatch):
    w=module()
    import resource
    calls=[]
    monkeypatch.setattr(resource,'getrlimit',lambda key:(1,1))
    monkeypatch.setattr(resource,'setrlimit',lambda *args:calls.append(args))
    with pytest.raises(w.WorkerError): w.NativeOps().limits()
    assert calls==[]


@pytest.mark.parametrize('state',[('default',True,False),('handler',False,False),('default',False,True)])
def test_setup_rejects_blocked_handler_pending_alarm(state):
    w=module()
    class Fake:
        def getpid(self): return 2
        def getppid(self): return 1
        def limits(self): return w.LIMITS
        def default_alarm(self): pass
        def unblock_alarm(self): pass
        def alarm_state(self): return state
        def arm(self,*args): pytest.fail('must reject before arm')
    with pytest.raises(w.WorkerError): w.setup_child(1,Fake())


def test_runtime_validation_requires_bounded_preparation(tmp_path):
    w=module();p=profile()
    assert hasattr(w,'prepare_runtime'), 'missing bounded host/runtime preparation'
    w._RUNTIMES.clear()
    with pytest.raises(w.WorkerError): w.validate_runtime(w.synthetic_runtime(p),p)
    code=tmp_path/'code';code.mkdir(mode=0o700)
    for name in w.CODE_NAMES:
        target=code/name;target.write_bytes((ROOT/name).read_bytes());target.chmod(0o600)
    target=code/'pack_runtime.py';target.unlink();target.symlink_to(ROOT/'pack_runtime.py')
    with pytest.raises((w.WorkerError,OSError)): w.prepare_runtime(p,code_dir=code)
    target.unlink();target.write_bytes(b'x'*262145);target.chmod(0o600)
    with pytest.raises(w.WorkerError): w.prepare_runtime(p,code_dir=code)
    target.unlink()
    with pytest.raises(OSError): w.prepare_runtime(p,code_dir=code)


@pytest.mark.parametrize('key,value',[('alarm_default',False),('alarm_blocked',True),('alarm_pending',True),('supervisor_term_int_unblocked',False)])
def test_runtime_rejects_signal_state(key,value):
    w=module();p=profile();proof=w.synthetic_runtime(p);proof[key]=value
    with pytest.raises(w.WorkerError): w.validate_runtime(proof,p)


class PipeWouldBlock(RuntimeError):
    pass


class FakePipeRegistry:
    """Deterministic connected pipes, actor-owned endpoint tables and real EOF."""
    def __init__(self):
        self.next_fd=10;self.endpoints={};self.queues={};self.owned={'parent':set(),'child':set()}
        self.allocations=[];self.streams={1:bytearray(),2:bytearray()};self.standard_events=[]
    def standard_write(self,actor,fd,data):
        if actor!='parent' or fd not in (1,2): raise OSError('standard stream ownership rejected')
        assert 0<len(data)<=65536
        self.streams[fd].extend(data);self.standard_events.append((fd,bytes(data)));return len(data)
    def pipe(self):
        read,write=self.next_fd,self.next_fd+1;self.next_fd+=2
        self.endpoints[read]=(read,'read');self.endpoints[write]=(read,'write')
        self.queues[read]=bytearray();self.owned['parent'].update((read,write))
        self.allocations.append((read,write));return read,write
    def fork(self): self.owned['child']=self.owned['parent'].copy()
    def endpoint(self,actor,fd,direction=None):
        if fd not in self.owned[actor] or fd not in self.endpoints: raise OSError('invalid or closed fake fd')
        key,actual=self.endpoints[fd]
        if direction is not None and actual!=direction: raise OSError('wrong fake fd direction')
        return key
    def close(self,actor,fd): self.endpoint(actor,fd);self.owned[actor].remove(fd)
    def exit(self,actor): self.owned[actor].clear()
    def write(self,actor,fd,data):
        key=self.endpoint(actor,fd,'write')
        readers={f for f,e in self.endpoints.items() if e==(key,'read')}
        if not any(readers & owned for owned in self.owned.values()): raise BrokenPipeError('no fake reader')
        self.queues[key].extend(data);return len(data)
    def read(self,actor,fd,size):
        key=self.endpoint(actor,fd,'read');queue=self.queues[key]
        if queue:
            data=bytes(queue[:size]);del queue[:size];return data
        writers={f for f,e in self.endpoints.items() if e==(key,'write')}
        if any(writers & owned for owned in self.owned.values()): raise PipeWouldBlock('fake live writer without bytes')
        return b''


def test_fake_pipe_registry_exact_identity_direction_ownership_and_eof():
    pipes=FakePipeRegistry()
    for actor,fd in [('child',1),('child',2),('parent',0),('parent',3)]:
        with pytest.raises(OSError): pipes.standard_write(actor,fd,b'x')
    assert pipes.standard_write('parent',2,b'artifact')==8
    assert pipes.standard_write('parent',1,b'report')==6
    assert pipes.streams=={1:b'report',2:b'artifact'}
    r,w=pipes.pipe();pipes.fork()
    pipes.close('parent',w);pipes.close('child',r)
    for actor,fd,direction in [('parent',w,'write'),('child',r,'read'),('parent',r,'write'),('child',w,'read'),('child',999,'read')]:
        with pytest.raises(OSError): pipes.endpoint(actor,fd,direction)
    with pytest.raises(PipeWouldBlock): pipes.read('parent',r,10)
    assert pipes.write('child',w,b'connected')==9
    assert pipes.read('parent',r,4)==b'conn'
    assert pipes.read('parent',r,10)==b'ected'
    with pytest.raises(PipeWouldBlock): pipes.read('parent',r,10)
    pipes.close('child',w)
    assert pipes.read('parent',r,10)==b''
    pipes.close('parent',r)
    with pytest.raises(OSError): pipes.read('parent',r,1)


@pytest.mark.parametrize('mode,fault',[(m,None) for m in ['preflight','decode','control-as','control-cpu','control-wall']]+[
    ('preflight',f) for f in ['fork','pipe','install','read','wait','close','gatewrite','malformed','truncated','terminalpid','handoff-cancel','runtime','setup','childwrite','missing-G','wrong-G','wrong-endpoints','early-report-close','error-unknown','error-nonstring','error-extra','error-malformed','error-extra-key','post-error-unknown','post-error-nonstring','post-error-extra','post-error-extra-key','artifact-zero','artifact-invalid','artifact-partial','artifact-base','artifact-pin','artifact-missing','artifact-mode','artifact-symlink','stdout-partial','stdout-zero','stdout-base','errorwrite','cleanup-base']]+[('decode','decode'),('control-as','control-as')])
def test_actual_main_protocol_with_fake_fork_controls_and_short_writes(tmp_path,mode,fault):
    w,p,code,inputs,work=stage(tmp_path);p['mode']=mode
    if mode!='decode': p['pack']=None
    assert hasattr(w,'NativeExecution'),'missing cohesive main adapter'
    class ControlDeath(BaseException): pass
    class Fake(w.NativeExecution):
        injected=True
        code_dir=code;inputs_dir=inputs;work_dir=work
        def __init__(self):
            self.is_child=False;self.pipes=FakePipeRegistry();self.stdout=self.pipes.streams[1];self.stderr=self.pipes.streams[2];self.pending=False;self.dead=False;self.status=0;self.gate_received=False
            self.handlers={};self.mask={14,15,2};self.calls=[];self.now=0;self.next_fd=10
            self.failed=False;self.exitstatus=0
        def fail(self,name):
            if fault==name and not self.failed:
                self.failed=True;raise OSError('private must never escape')
        def environment(self): return ('Linux','aarch64',1,p['uid'],p['gid'])
        def profile_bytes(self,pin): return w.canonical(p)
        def pipe(self): self.fail('pipe');return self.pipes.pipe()
        def fork(self):
            self.fail('fork');self.calls.append('fork');self.pipes.fork();self.pending=True
            self.childmask=self.mask.copy();self.childhandlers=self.handlers.copy()
            self.child_args=tuple(fd for pair in self.pipes.allocations for fd in pair)
            assert len(self.child_args)==4
            assert not [c for c in self.calls if type(c) is tuple and c[0] in ('limits','arm','runtime','decode','control')]
            return 2
        def schedule_child(self):
            assert self.pending and not self.dead
            self.pending=False;parentmask=self.mask;parenthandlers=self.handlers
            self.mask=self.childmask;self.handlers=self.childhandlers;self.is_child=True
            args=self.child_args
            if fault=='wrong-endpoints': args=(args[1],args[0],args[3],args[2])
            try: w.child_protocol(p,self,1,*args,self.childmask,self.childhandlers)
            except ControlDeath: pass
            finally:
                self.pipes.exit('child');self.dead=True
                self.is_child=False;self.mask=parentmask;self.handlers=parenthandlers
            queue=self.pipes.queues[self.pipes.allocations[0][0]]
            if fault=='malformed': queue[:]=b'{bad}\n'
            if fault=='truncated': del queue[-4:]
            if fault=='error-unknown': queue[:]=w.canonical({'error':'unknown'})+b'\n'
            if fault=='error-nonstring': queue[:]=w.canonical({'error':['runtime']})+b'\n'
            if fault=='error-extra': queue[:]=w.canonical({'error':'runtime'})+b'\n'+w.canonical({'extra':True})+b'\n'
            if fault=='error-malformed': queue[:]=b'{"error":\n'
            if fault=='error-extra-key': queue[:]=w.canonical({'error':'runtime','extra':True})+b'\n'
            if fault in ('post-error-unknown','post-error-nonstring','post-error-extra','post-error-extra-key'):
                first=bytes(queue).splitlines()[0]+b'\n'
                frame={'error':'unknown' if fault=='post-error-unknown' else [] if fault=='post-error-nonstring' else 'runtime'}
                if fault=='post-error-extra-key': frame['extra']=True
                queue[:]=first+w.canonical(frame)+b'\n'
                if fault=='post-error-extra': queue.extend(w.canonical({'extra':True})+b'\n')
        def getpid(self): return 2 if self.is_child else 1
        def getppid(self): return 1
        def close(self,fd):
            actor='child' if self.is_child else 'parent'
            if not self.is_child: self.fail('close')
            self.pipes.close(actor,fd);self.calls.append(('close',actor,fd))
            if actor=='parent' and self.pending and fd==self.pipes.allocations[1][1]:
                # Missing G is delivered as actual gate EOF, never fabricated.
                self.schedule_child()
        def install(self,s,h): self.fail('install');old=self.handlers.get(s);self.handlers[s]=h;return old
        def restore(self,s,h):
            self.handlers[s]=h;self.calls.append(('restore',s))
            if s==2 and fault=='cleanup-base': raise KeyboardInterrupt('private')
            if s==2 and fault=='artifact-pin': (work/'manifest.json').write_bytes(b'{}')
            if s==2 and fault=='artifact-missing': (work/'manifest.json').unlink()
            if s==2 and fault=='artifact-mode': (work/'manifest.json').chmod(0o644)
            if s==2 and fault=='artifact-symlink':
                target=work/'moved';(work/'manifest.json').rename(target);(work/'manifest.json').symlink_to(target)
        def block_term_int(self): old=self.mask.copy();self.mask|={2,15};return old
        def set_mask(self,mask):
            self.mask=set(mask)
            if fault=='handoff-cancel' and not self.failed and not self.is_child:
                self.failed=True;self.handlers[15](15,None)
        def term_int_unblocked(self):
            valid=not self.mask & {2,15}
            if valid: self.calls.append('parent-unblocked')
            return valid
        def child_signals(self,mask): self.handlers={};self.mask=set(mask)-{2,15}
        def limits(self):
            assert self.gate_received,'child setup before delivered G'
            self.calls.append(('limits',self.is_child));return w.LIMITS
        def default_alarm(self): self.calls.append(('default',self.is_child))
        def unblock_alarm(self): self.mask.discard(14)
        def alarm_state(self): return ('default',14 in self.mask or fault=='setup',False)
        def arm(self,*args): self.calls.append(('arm',self.is_child,args))
        def timer(self): return 119.8,0
        def runtime(self,profile):
            assert self.gate_received;self.calls.append(('runtime',self.is_child))
            self.fail('runtime');return w.synthetic_runtime(profile)
        def decode(self,profile,proof):
            assert self.gate_received;self.calls.append(('decode',self.is_child))
            self.fail('decode');return super().decode(profile,proof)
        def read(self,fd,size):
            actor='child' if self.is_child else 'parent'
            if actor=='parent': self.fail('read')
            if fault=='early-report-close' and actor=='parent' and fd in self.pipes.owned['parent']:
                self.pipes.close('parent',fd)
            data=self.pipes.read(actor,fd,size)
            if actor=='child':
                assert fd==self.pipes.allocations[1][0]
                self.calls.append(('child-gate-read',data));self.gate_received=data==b'G'
            return data
        def write(self,fd,data):
            data=bytes(data[:3])
            if fd in (1,2):
                assert not self.is_child
                if fd==2:
                    if fault=='artifact-zero': return 0
                    if fault=='artifact-invalid': return len(data)+1
                    if fault=='artifact-base': raise KeyboardInterrupt('private')
                    if fault=='errorwrite': raise OSError('private')
                    if fault=='artifact-partial' and self.stderr: raise OSError('private')
                    assert ('wait',2) in self.calls and ('restore',2) in self.calls
                    assert self.pipes.owned=={'parent':set(),'child':set()}
                    self.pipes.standard_write('parent',2,data)
                else:
                    self.calls.append(('stdout-attempt',len(self.stdout)))
                    if fault in ('stdout-zero','errorwrite'): return 0
                    if fault=='stdout-base': raise KeyboardInterrupt('private')
                    if fault=='stdout-partial' and self.stdout: raise OSError('private')
                    if fault is None:
                        assert self.stderr== (work/'manifest.json').read_bytes()+b'\n'
                    self.pipes.standard_write('parent',1,data)
                return len(data)
            actor='child' if self.is_child else 'parent'
            if actor=='child': self.fail('childwrite')
            else: self.fail('gatewrite')
            if actor=='parent' and fault=='missing-G':
                self.pipes.endpoint(actor,fd,'write');return len(data)
            delivered=b'X' if actor=='parent' and fault=='wrong-G' else data
            count=self.pipes.write(actor,fd,delivered)
            if actor=='parent':
                assert fd==self.pipes.allocations[1][1]
                assert self.pipes.owned['parent']=={self.pipes.allocations[0][0],self.pipes.allocations[1][1]}
                assert 'parent-unblocked' in self.calls
                self.calls.append(('gate-queued',delivered))
                assert self.pending and not self.gate_received
                self.schedule_child()
            return count
        def wait(self,pid):
            self.calls.append(('wait',pid))
            self.fail('wait')
            if fault=='terminalpid' and not self.failed: self.failed=True;return 3,0,.01
            assert self.dead,'cannot wait a live deferred child'
            return pid,self.status,30.0 if mode=='control-cpu' else .01
        def kill(self,pid):
            assert pid==2
            self.calls.append(('kill',pid));self.pipes.exit('child');self.pending=False;self.dead=True;self.status=9
        def clock(self): self.now+=120 if mode=='control-wall' else 1;return self.now
        def control_as(self):
            assert self.gate_received;self.calls.append(('control',self.is_child))
            self.fail('control-as');self.calls.append('as')
        def control_cpu(self):
            assert self.gate_received;self.calls.append(('control',self.is_child))
            self.calls.append('cpu');self.status=9;raise ControlDeath
        def control_wall(self):
            assert self.gate_received;self.calls.append(('control',self.is_child))
            self.calls.append('wall');self.status=14;raise ControlDeath
        def exit_child(self,status):
            self.exitstatus=status;self.status=status<<8;raise ControlDeath
    fake=Fake()
    rc=w.main(['/inputs/profile.json',hashlib.sha256(w.canonical(p)).hexdigest()],_ops=fake)
    if fault in ('stdout-partial','stdout-zero','stdout-base','errorwrite'):
        assert rc==70 and b'worker-error' not in fake.stdout
        assert len(fake.stdout)==(3 if fault=='stdout-partial' else 0)
        assert len([c for c in fake.calls if type(c) is tuple and c[0]=='stdout-attempt'])==(2 if fault=='stdout-partial' else 1)
        return
    result=json.loads(fake.stdout)
    if fault is not None:
        assert rc==70 and result['format']=='av-pack-worker-error-v2'
        assert result['injected_for_tests'] is True and result['phase'] in w.ERROR_PHASES
        assert set(result)=={'format','outcome','phase','injected_for_tests'} and result['outcome']=='failed'
        exact={'runtime':'runtime','setup':'setup','missing-G':'handoff','wrong-G':'handoff',
               'decode':'decode','control-as':'control','error-unknown':'pipe','error-nonstring':'pipe',
               'error-extra':'pipe','error-malformed':'pipe','error-extra-key':'pipe','malformed':'pipe','truncated':'pipe',
               'post-error-unknown':'pipe','post-error-nonstring':'pipe','post-error-extra':'pipe','post-error-extra-key':'pipe'}
        if fault in exact: assert result['phase']==exact[fault]
        assert b'private' not in fake.stdout
        if fault not in ('fork','pipe','install'): assert ('wait',2) in fake.calls
        if fault in ('missing-G','wrong-G'):
            assert not [c for c in fake.calls if type(c) is tuple and c[0] in ('limits','arm','runtime','decode','control')]
        return
    assert rc==0
    assert result['mode']==mode and result['injected_for_tests'] is True
    assert result['terminal']['pid']==2
    assert fake.calls.count('fork')==1
    queued=fake.calls.index(('gate-queued',b'G'));received=fake.calls.index(('child-gate-read',b'G'))
    assert queued<received<fake.calls.index(('limits',True))
    assert fake.pipes.owned=={'parent':set(),'child':set()}
    assert [c for c in fake.calls if type(c) is tuple and c[0]=='arm']==[('arm',True,(120,0))]
    assert fake.calls.count(('wait',2))==1 and not [c for c in fake.calls if type(c) is tuple and c[0]=='kill']
    assert (work/'manifest.json').exists()
    assert fake.stderr==(work/'manifest.json').read_bytes()+b'\n'
    stream_ids=[fd for fd,data in fake.pipes.standard_events]
    first_stdout=stream_ids.index(1)
    assert stream_ids[:first_stdout] and set(stream_ids[:first_stdout])=={2} and set(stream_ids[first_stdout:])=={1}
    # Actual connected FakeMain producer bytes feed the strict artifact consumer.
    spec=importlib.util.spec_from_file_location('connected_artifact',ROOT/'pack_artifact.py')
    artifact=importlib.util.module_from_spec(spec);sys.modules[spec.name]=artifact;spec.loader.exec_module(artifact)
    framed=b'\x02\0\0\0'+len(fake.stderr).to_bytes(4,'big')+bytes(fake.stderr)
    decoded=artifact.decode_manifest_logs(framed,profile=p,report=result,validator=w.validate_report,_allow_injected=True)
    assert decoded.canonical_bytes==(work/'manifest.json').read_bytes() and decoded.transport['injected_for_tests'] is True
    target=tmp_path/'accepted-synthetic';target.mkdir(mode=0o700)
    artifact.store_manifest(decoded.canonical_bytes,expected_size=result['manifest']['bytes'],expected_sha256=result['manifest']['sha256'],destination=target/'manifest.json')
    w.validate_report(result,p,_allow_injected=True)
    with pytest.raises(w.WorkerError): w.validate_report(result,p)


@pytest.mark.parametrize('failure',['install','read','close','wait','interrupted-wait','handoff-cancel'])
def test_owned_boundary_reaps_even_when_operations_fail(failure):
    w=module()
    assert hasattr(w,'OwnedChild'),'missing whole ownership boundary'
    class Fake:
        def __init__(self): self.calls=[];self.failed=False;self.handler=None
        def block_term_int(self): return set()
        def set_mask(self,mask):
            if failure=='handoff-cancel' and self.handler: self.handler(15,None)
        def term_int_unblocked(self): return True
        def install(self,s,h):
            self.handler=h
            if failure=='install' and not self.failed: self.failed=True;raise OSError('private')
            return None
        def restore(self,s,h): self.calls.append(('restore',s))
        def kill(self,pid): self.calls.append(('kill',pid))
        def close(self,fd):
            self.calls.append(('close',fd))
            if failure=='close' and not self.failed: self.failed=True;raise OSError('private')
        def wait(self,pid):
            self.calls.append(('wait',pid))
            if failure in ('wait','interrupted-wait') and not self.failed:
                self.failed=True
                raise InterruptedError() if failure=='interrupted-wait' else OSError('private')
            return pid,9,30.
    ops=Fake();owner=w.OwnedChild(ops);owner.child=42;owner.fds={7,8}
    try:
        owner.begin();owner.handoff()
        if failure=='read': raise OSError('private')
        owner.reap()
    except (OSError,w.WorkerError): pass
    finally:
        try: owner.cleanup()
        except w.WorkerError:
            assert failure=='close'
    assert ('wait',42) in ops.calls
    assert owner.reaped is True
    if failure!='install':
        assert ('restore',15) in ops.calls and ('restore',2) in ops.calls
    if failure!='interrupted-wait': assert ('kill',42) in ops.calls or failure=='close'


def test_profile_first_snapshot_checks_actual_gid(monkeypatch):
    w=module()
    assert hasattr(w,'NativeExecution')
    seen={}
    def read(directory,name,**kwargs): seen.update(kwargs);return b'{}'
    monkeypatch.setattr(w,'read_private',read)
    w.NativeExecution().profile_bytes('a'*64)
    assert seen['uid']==os.geteuid() and seen['gid']==os.getegid()


@pytest.mark.parametrize('mode',['preflight','decode','control-as','control-cpu','control-wall'])
@pytest.mark.parametrize('wrong',['file','directory'])
def test_wrong_profile_or_inputs_gid_rejected_before_fork(tmp_path,monkeypatch,mode,wrong):
    w=module();p=profile(mode)
    inputs=tmp_path/'inputs';inputs.mkdir(mode=0o700)
    raw=w.canonical(p);(inputs/'profile.json').write_bytes(raw);(inputs/'profile.json').chmod(0o600)
    origstat=w.os.stat;origfstat=w.os.fstat
    def altered(info):
        fields=list(info);fields[5]=os.getegid()+1;return os.stat_result(fields)
    def fake_stat(path,*args,**kwargs):
        info=origstat(path,*args,**kwargs)
        return altered(info) if wrong=='file' and path=='profile.json' else info
    def fake_fstat(fd):
        info=origfstat(fd)
        return altered(info) if wrong=='directory' and w.stat.S_ISDIR(info.st_mode) else info
    monkeypatch.setattr(w.os,'stat',fake_stat);monkeypatch.setattr(w.os,'fstat',fake_fstat)
    monkeypatch.setattr(w,'parse_profile',lambda raw:pytest.fail('metadata mismatch reached parser'))
    class Fake(w.NativeExecution):
        injected=True
        def environment(self): return 'Linux','aarch64',1,p['uid'],p['gid']
        def profile_bytes(self,pin): return w.read_private(inputs,'profile.json',cap=16384,expected_sha=pin,uid=os.geteuid(),gid=os.getegid())
        def write(self,fd,data): return len(data)
        def fork(self): pytest.fail('GID mismatch reached fork')
    assert w.main(['/inputs/profile.json',hashlib.sha256(raw).hexdigest()],_ops=Fake())==70


def test_bounded_runtime_metadata_drift_rejected(tmp_path,monkeypatch):
    w=module();p=profile();tmp_path.chmod(0o700)
    for name in w.CODE_NAMES:
        target=tmp_path/name;target.write_bytes((ROOT/name).read_bytes());target.chmod(0o600)
    original=w.os.stat;calls=[0]
    def drift(path,*args,**kwargs):
        info=original(path,*args,**kwargs)
        if path=='pack_runtime.py':
            calls[0]+=1
            if calls[0]>1:
                values=list(info);values[9]+=1;return os.stat_result(values)
        return info
    monkeypatch.setattr(w.os,'stat',drift)
    with pytest.raises(w.WorkerError): w.prepare_runtime(p,code_dir=tmp_path)


@pytest.mark.parametrize('failure',['partial-write','zero-write','directory-fsync','rehash'])
def test_manifest_catchable_failure_never_leaves_accepted_partial(tmp_path,monkeypatch,failure):
    w=module();tmp_path.chmod(0o700)
    original_write=w.os.write;original_fsync=w.os.fsync;original_read=w.read_private;calls=[0]
    def write(fd,data):
        calls[0]+=1
        if failure=='zero-write': return 0
        if calls[0]==1: return original_write(fd,data[:1])
        raise OSError('private')
    def fsync(fd):
        if w.stat.S_ISDIR(w.os.fstat(fd).st_mode): raise OSError('private')
        return original_fsync(fd)
    def read(*args,**kwargs): raise w.WorkerError('private')
    if failure in ('partial-write','zero-write'): monkeypatch.setattr(w.os,'write',write)
    if failure=='directory-fsync': monkeypatch.setattr(w.os,'fsync',fsync)
    if failure=='rehash': monkeypatch.setattr(w,'read_private',read)
    with pytest.raises((w.WorkerError,OSError)): w.write_manifest(tmp_path,{'a':'synthetic'})
    assert not (tmp_path/'manifest.json').exists()


def test_all_native_functions_forbidden_during_fake_main_entrypoint_failure(monkeypatch):
    w=module()
    def forbidden(*args,**kwargs): pytest.fail('actual native operation escaped fake adapter')
    import resource
    for obj,names in ((w.os,('fork','pipe','kill','wait4')),(w.signal,('signal','pthread_sigmask','setitimer','getitimer','sigpending')),
                      (resource,('setrlimit','getrlimit'))):
        for name in names: monkeypatch.setattr(obj,name,forbidden)
    class Fake:
        injected=True
        def write(self,fd,data): return len(data)
    assert w.main(['wrong'],_ops=Fake())==70
