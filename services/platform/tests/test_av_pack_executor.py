"""Pure executor fixtures; no native resources."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import pytest
SOURCE = Path(__file__).resolve().parents[1] / 'scripts/av_sources'
def load(path, name):
    spec=importlib.util.spec_from_file_location(name,path)
    m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m
@pytest.fixture
def m():
    if not (SOURCE/'pack_executor.py').exists(): pytest.skip('missing executor')
    return load(SOURCE/'pack_executor.py','executor_test')
def test_executor_exists():
    assert (SOURCE/'pack_executor.py').exists(), 'complete executor missing'
def test_boot_darwin_abi(m):
    import ctypes as c
    class Fake:
        def __call__(self,name,value,size,new,newlen):
            assert name==b'kern.boottime' and new is None and newlen==0
            assert self.argtypes==[c.c_char_p,c.c_void_p,c.POINTER(c.c_size_t),c.c_void_p,c.c_size_t]
            assert self.restype is c.c_int
            value._obj.tv_sec=42;value._obj.tv_usec=999999
            assert size._obj.value==16
            return 0
    assert m.boot_identity(_platform='darwin',_sysctl=Fake())==m.sha(m.canonical({'seconds':42,'microseconds':999999}))
@pytest.mark.parametrize('fault',['length','sec','usec','return'])
def test_boot_darwin_rejects(m,fault):
    class Fake:
        def __call__(self,n,v,s,new,nl):
            v._obj.tv_sec=0 if fault=='sec' else 42;v._obj.tv_usec=1000000 if fault=='usec' else 0
            s._obj.value=8 if fault=='length' else 16
            return -1 if fault=='return' else 0
    with pytest.raises(m.ExecutorError): m.boot_identity(_platform='darwin',_sysctl=Fake())
def test_records_exclusive_and_identity(m,tmp_path):
    r=m.Records(str(tmp_path.resolve()))
    pin=r.write('create.intent',{'x':1})
    assert r.read('create.intent',pin)[0]=={'x':1}
    with pytest.raises(m.ExecutorError): r.write('create.intent',{'x':2})
    with pytest.raises(m.ExecutorError): r.read('create.intent','f'*64)
    with pytest.raises(m.ExecutorError): r.write('../bad',{})
def test_bootstrap_actual_stage_and_rollback(m,tmp_path):
    h=load(Path(__file__).with_name('test_av_pack_profile.py'),'executor_profile_fixtures')
    root,pin=h.stage(tmp_path)
    p=m.bootstrap_stage(str(root),pin)
    assert p.descriptor_sha256==pin
    assert p.validate_report is p.worker.validate_report
    with pytest.raises(m.ExecutorError): m.bootstrap_stage(str(root),'f'*64)

def rig(m,tmp_path,mode='preflight',fault=None,guardian_flags=(True,True),inspect_compat=False):
    h=load(Path(__file__).with_name('test_av_pack_profile.py'),'executor_profile_fixtures')
    wtest=load(Path(__file__).with_name('test_av_pack_worker.py'),'executor_worker_fixtures')
    producer=tmp_path/'producer';producer.mkdir(mode=0o700)
    w,profile,code,inputs,work=wtest.stage(producer)
    profile['mode']=mode
    if mode!='decode': profile['pack']=None
    captured={};actual=w.main
    def capture(*a,**kw):
        rc=actual(*a,**kw);ops=kw['_ops'];captured.update(stdout=bytes(ops.stdout),artifact=bytes(ops.stderr));return rc
    w.main=capture
    wtest.stage=lambda _: (w,profile,code,inputs,work)
    wtest.test_actual_main_protocol_with_fake_fork_controls_and_short_writes(producer,mode,None)
    root,pin=h.stage(tmp_path)
    profile['nonce']=h.NONCE
    # Produce again with exactly the stage nonce and profile, retaining genuine main.
    work.joinpath('manifest.json').unlink();producer.joinpath('accepted-synthetic/manifest.json').unlink();producer.joinpath('accepted-synthetic').rmdir()
    wtest.test_actual_main_protocol_with_fake_fork_controls_and_short_writes(producer,mode,None)
    h.put(root/'inputs/profile.json',h.canon(profile))
    if mode=='decode': h.put(root/'inputs/source.pack',(inputs/'source.pack').read_bytes())
    pin=h.repin(root);prepared=m.bootstrap_stage(str(root),pin);pm=m.profile_module();recipe=pm.make_recipe(prepared)
    op=tmp_path/'operator';op.mkdir(mode=0o700);h.put(op/'pack_executor.py',(SOURCE/'pack_executor.py').read_bytes())
    clock=[1.0];events=[];state=h.fake(pm,prepared,recipe)
    if inspect_compat:state['ImageManifestDescriptor']['platform']=dict(h.PLATFORM)
    class Process:
        pid=456
        def __init__(self,bindings,records):
            self.bindings=bindings;self.records=records;self.dead=False;self.reaped=False;self.wait_timeouts=[]
            events.append('launch')
            if fault!='ready': records.write('ready.json',dict(bindings,pid=self.pid,injected_for_tests=guardian_flags[0]))
        def poll(self): return 0 if self.dead else None
        def wait(self,timeout):
            self.wait_timeouts.append(timeout);events.append('reap');assert self.dead;self.reaped=True;return 0
    proc=[]
    def launch(bindings,records,**kw):
        p=Process(bindings,records);proc.append(p);return p
    class API:
        injected_for_tests=True
        def bind_created(self,cid): events.append('bind');assert cid==h.CID
        def bind_deadline(self,d): events.append('deadline');assert d==181
        def admit_stdout(self): events.append('admit')
        def call(self,operation):
            name=operation.value;events.append(name)
            if name=='info': value={'OSType':'linux','Architecture':'aarch64','DefaultRuntime':'runc','CgroupVersion':'2'}
            elif name=='image': value=json.loads(h.EVIDENCE.read_bytes())['current']
            elif name=='create': value={'Id':h.CID,'Warnings':['bad'] if fault=='warnings' else None}
            elif name=='start':
                if inspect_compat:state['HostConfig']['OomKillDisable']=None
                assert 'launch' in events and (root/'results/ready.json').exists()
                state['State'].update(Status='running',Running=True,Pid=9,StartedAt='2026-11-03T00:00:00Z');value=None
            elif name=='inspect':
                if 'start' in events:
                    state['State'].update(Status='exited',Running=False,Pid=0,FinishedAt='2026-11-03T00:00:01Z')
                    if fault=='exit': state['State']['ExitCode']=1
                    facts=pm.validate_container(state,h.CID,prepared,recipe,phase='observe')
                    if not proc[0].dead:
                        proc[0].records.write('done.json',dict(proc[0].bindings,pid=456,outcome='normal',terminal=facts,injected_for_tests=guardian_flags[1]))
                        proc[0].dead=True
                value=json.loads(json.dumps(state))
            else:
                data=captured[name]
                if fault=='stdout' and name=='stdout': data=b'{}\n'
                stream=1 if name=='stdout' else 2
                value=bytes([stream,0,0,0])+len(data).to_bytes(4,'big')+data
            return type('Result',(),{'value':value,'injected_for_tests':True,'counters':type('Counters',(),{})()})()
    api=API()
    deps=dict(clock=lambda:clock[0],boot=lambda:'b'*64,sleep=lambda n:clock.__setitem__(0,clock[0]+n),
              capacity=lambda _:3*1024**3,api=lambda *_:api,launch=launch)
    return prepared,op/'pack_executor.py',m.sha((op/'pack_executor.py').read_bytes()),deps,events,proc

@pytest.mark.parametrize('mode',['preflight','decode','control-as','control-cpu','control-wall'])
def test_complete_genuine_producer_pipeline(m,tmp_path,mode):
    p,path,pin,deps,events,proc=rig(m,tmp_path,mode)
    result=m.execute_once(p,operator_path=str(path),operator_sha256=pin,_deps=deps,_allow_injected=True)
    dto=json.loads(result)
    assert dto['injected_for_tests'] is True and dto['guardian_injected_for_tests'] is True and dto['mode']==mode
    assert proc[0].reaped and events.index('reap')<events.index('stdout')<events.index('admit')<events.index('artifact')
    assert events.count('create')==events.count('start')==events.count('stdout')==events.count('artifact')==1
    before=list(events)
    with pytest.raises(m.ExecutorError): m.execute_once(p,operator_path=str(path),operator_sha256=pin,_deps=deps,_allow_injected=True)
    assert events==before
@pytest.mark.parametrize('fault',['warnings','ready','stdout','exit'])
def test_controller_rejects_causal_failure(m,tmp_path,fault):
    p,path,pin,deps,events,proc=rig(m,tmp_path,fault=fault)
    with pytest.raises(m.ExecutorError): m.execute_once(p,operator_path=str(path),operator_sha256=pin,_deps=deps,_allow_injected=True)
    assert (Path(p.stage)/'results/handle.json').exists()
    assert 'artifact' not in events
    if fault in ('warnings','ready'): assert 'start' not in events
    if fault=='warnings': assert 'bind' not in events
    if fault in ('stdout','exit'): assert (Path(p.stage)/'results/abort.intent').exists()
def test_injection_cannot_be_hidden(m,tmp_path):
    p,path,pin,deps,events,_=rig(m,tmp_path)
    with pytest.raises(m.ExecutorError): m.execute_once(p,operator_path=str(path),operator_sha256=pin,_deps=deps)
    assert not events

@pytest.mark.parametrize('poison',[False,True])
def test_guardian_transport_exact_cid_once_and_real_http(m,tmp_path,poison):
    h=load(Path(__file__).with_name('test_av_pack_profile.py'),'executor_profile_fixtures')
    a=load(Path(__file__).with_name('test_av_pack_api.py'),'executor_api_fixtures')
    root,pin=h.stage(tmp_path);p=m.bootstrap_stage(str(root),pin)
    responses=[b'broken' if poison else a.wire(a.encoded({'Id':h.CID})),a.wire(status=204),a.wire(a.encoded({'Id':h.CID}))]
    sockets=[]
    def sf(*_):
        s=a.Socket(responses.pop(0));sockets.append(s);return s
    g=m.GuardianAPI(p,h.CID,100.0,socket_factory=sf,timer_factory=a.Timer,clock=lambda:1.0)
    if poison:
        with pytest.raises(m.ExecutorError):g.inspect()
        with pytest.raises(m.ExecutorError):g.inspect()
    else: assert g.inspect().value=={'Id':h.CID}
    g.kill()
    with pytest.raises(m.ExecutorError):g.kill()
    assert g.inspect_after_stop().value=={'Id':h.CID}
    with pytest.raises(m.ExecutorError):g.inspect_after_stop()
    assert len(sockets)==3 and b'/'+h.CID.encode()+b'/kill?signal=KILL ' in sockets[1].sent[0]
    assert all(s.close_count==s.shutdown_count==1 for s in sockets)

def test_launcher_is_direct_pinned_minimal_and_inert(m,tmp_path):
    h=load(Path(__file__).with_name('test_av_pack_profile.py'),'executor_profile_fixtures')
    root,pin=h.stage(tmp_path);p=m.bootstrap_stage(str(root),pin)
    op=tmp_path/'operator';op.mkdir(mode=0o700);h.put(op/'pack_executor.py',(SOURCE/'pack_executor.py').read_bytes())
    calls=[]
    def popen(argv,**kw): calls.append((argv,kw));return type('P',(),{'pid':456})()
    bindings=dict(handle='a'*64,wall='b'*64,created='c'*64)
    m.launch_guardian(bindings,m.Records(str(root/'results')),prepared=p,operator_path=str(op/'pack_executor.py'),
         operator_sha256=m.sha((op/'pack_executor.py').read_bytes()),_popen=popen)
    argv,kw=calls[0]
    assert argv[0]==sys.executable and argv[0]==m.PYTHON
    assert argv[5:]==[str(op/'pack_executor.py'),m.sha((op/'pack_executor.py').read_bytes()),'guardian',p.stage,p.descriptor_sha256,'a'*64,'b'*64,'c'*64]
    import subprocess
    assert kw['stdin']==kw['stdout']==kw['stderr']==subprocess.DEVNULL
    assert kw['cwd']==str(op)
    assert argv[1:3]==['-I','-B'] and argv[3]=='-c' and argv[4]==m.BOOTSTRAP
    assert kw['shell'] is False and kw['start_new_session'] is True and kw['close_fds'] is True
    assert kw['env']=={'LANG':'C','LC_ALL':'C','PATH':'/usr/bin:/bin'}

@pytest.mark.parametrize('fault',['deadline','boot','config','state','artifact','source','record','abort','kill'])
def test_post_store_fences_reject_quarantine(m,tmp_path,fault):
    p,path,pin,deps,events,proc=rig(m,tmp_path)
    actual=p.artifact.store_manifest;stored=[];boot=['b'*64]
    deps['boot']=lambda:boot[0]
    def altered(*a,**kw):
        result=actual(*a,**kw);stored.append(result)
        if fault=='deadline': deps['sleep'](180)
        elif fault=='boot': boot[0]='c'*64
        elif fault=='artifact': Path(kw['destination']).write_bytes(b'{}')
        elif fault=='source': (Path(p.stage)/'code/pack_worker.py').chmod(0o644)
        elif fault=='record': (Path(p.stage)/'results/wall.json').chmod(0o644)
        elif fault in ('abort','kill'): m.Records(p.stage+'/results').write(fault+'.intent',{})
        else:
            api=deps['api'](None,None);call=api.call
            def changed(op):
                value=call(op)
                if op.value=='inspect':
                    if fault=='state': value.value['State']['FinishedAt']='2026-11-03T00:00:02Z'
                    else: value.value['HostConfig']['Memory']=1
                return value
            api.call=changed
        return result
    p.artifact.store_manifest=altered
    with pytest.raises(m.ExecutorError): m.execute_once(p,operator_path=str(path),operator_sha256=pin,_deps=deps,_allow_injected=True)
    assert (Path(p.stage)/'results/abort.intent').exists()
    assert stored, 'post-store mutation was never reached'
    assert (Path(p.stage)/'results/manifest.json').exists()
    assert not (Path(p.stage)/'results/accepted.json').exists()

@pytest.mark.parametrize('fault',['normal','created','abnormal','read','boot','swap','late'])
def test_guardian_loop_original_binding_and_stop(m,tmp_path,fault):
    p,path,pin,deps,events,proc=rig(m,tmp_path)
    # Obtain a genuinely controller-authored record set; remove only the fake
    # child's own ready/done to run an independent pure guardian against it.
    m.execute_once(p,operator_path=str(path),operator_sha256=pin,_deps=deps,_allow_injected=True)
    records=m.Records(p.stage+'/results');handle=records.read('handle.json')[1];wall=records.read('wall.json')[1];created=records.read('created.json')[1]
    for name in ('ready.json','done.json'): (Path(p.stage)/'results'/name).unlink()
    h=load(Path(__file__).with_name('test_av_pack_profile.py'),'guardian_profile_fixtures')
    state=h.fake(m.profile_module(),p,m.profile_module().make_recipe(p))
    now=[1.0];calls=[];boot=['b'*64]
    class API:
        injected_for_tests=True
        def inspect(self):
            calls.append('inspect')
            if fault=='read': raise ValueError('synthetic')
            if fault=='boot': boot[0]='c'*64
            if fault=='swap': (Path(p.stage)/'results/wall.json').chmod(0o644)
            if fault=='late': now[0]=181
            if fault=='created' and len(calls)==1: return type('R',(),{'value':state,'injected_for_tests':True})()
            state['State'].update(Status='exited',Running=False,Pid=0,StartedAt='2026-11-03T00:00:00Z',FinishedAt='2026-11-03T00:00:01Z',ExitCode=1 if fault=='abnormal' else 0)
            return type('R',(),{'value':state,'injected_for_tests':True})()
        def kill(self):
            calls.append('kill');return type('R',(),{'value':None,'injected_for_tests':True})()
        def inspect_after_stop(self):
            calls.append('post');return type('R',(),{'value':{},'injected_for_tests':True})()
    gd=dict(clock=lambda:now[0],boot=lambda:boot[0],sleep=lambda n:now.__setitem__(0,now[0]+n),pid=lambda:456,api=lambda *_:API())
    rc=m.guardian_main(p.stage,p.descriptor_sha256,str(path),pin,handle,wall,created,_deps=gd,_allow_injected=True)
    if fault in ('normal','created'):
        assert rc==0 and 'kill' not in calls
    else:
        assert rc==70 and calls.count('kill')==calls.count('post')==1
        if fault=='read': assert calls.count('inspect')==1

def test_bootstrap_argv_count_and_checked_publication(m,tmp_path):
    assert "sys.argv[3]=='execute'" in m.BOOTSTRAP
    p,path,pin,deps,events,proc=rig(m,tmp_path)
    chunks=[]
    def writer(fd,raw): chunks.append(bytes(raw[:3]));return min(3,len(raw))
    rc=m.main(['execute',p.stage,p.descriptor_sha256,str(path),pin],_deps=deps,_allow_injected=True,_write=writer)
    assert rc==0 and json.loads(b''.join(chunks))['injected_for_tests'] is True

def test_partial_publication_is_nonzero_without_second_json(m,tmp_path):
    p,path,pin,deps,events,proc=rig(m,tmp_path);chunks=[]
    def writer(fd,raw):
        if chunks: raise OSError('private')
        chunks.append(bytes(raw[:3]));return 3
    assert m.main(['execute',p.stage,p.descriptor_sha256,str(path),pin],_deps=deps,_allow_injected=True,_write=writer)==70
    assert len(chunks)==1 and len(chunks[0])==3

@pytest.mark.parametrize('mode',['execute','guardian'])
def test_authenticating_bootstrap_executes_only_pinned_bytes(m,tmp_path,monkeypatch,mode):
    op=tmp_path/'op';op.mkdir(mode=0o700)
    raw=b"def main(argv):\n    assert argv[0] in ('execute','guardian')\n    return 0\n"
    path=op/'pack_executor.py';path.write_bytes(raw);path.chmod(0o600)
    argv=['-c',str(path),m.sha(raw),mode,'/owned-stage','a'*64]+(['b'*64,'c'*64,'d'*64] if mode=='guardian' else [])
    monkeypatch.setattr(sys,'argv',argv)
    with pytest.raises(SystemExit) as result: exec(m.BOOTSTRAP,{})
    assert result.value.code==0
    argv[2]='f'*64
    with pytest.raises(SystemExit) as result: exec(m.BOOTSTRAP,{})
    assert result.value.code==70

@pytest.mark.parametrize('fault',['unknown-create','unknown-start','running-oom','slow-inspect'])
def test_unknown_outcomes_never_retry(m,tmp_path,fault):
    p,path,pin,deps,events,proc=rig(m,tmp_path);api=deps['api'](None,None);original=api.call
    def altered(op):
        if op.value==('create' if fault=='unknown-create' else 'start' if fault=='unknown-start' else ''):
            events.append(op.value);raise OSError('unknown sent length')
        result=original(op)
        if op.value=='inspect' and 'start' in events:
            if fault=='running-oom':
                result.value['State'].update(Status='running',Running=True,Pid=9,OOMKilled=True,FinishedAt='0001-01-01T00:00:00Z')
            if fault=='slow-inspect':deps['sleep'](180)
        return result
    api.call=altered
    with pytest.raises(m.ExecutorError):m.execute_once(p,operator_path=str(path),operator_sha256=pin,_deps=deps,_allow_injected=True)
    assert events.count('create')==1 and events.count('start')<=1 and 'stdout' not in events
    assert (Path(p.stage)/'results/create.intent').exists()
    before=list(events)
    with pytest.raises(m.ExecutorError):m.execute_once(p,operator_path=str(path),operator_sha256=pin,_deps=deps,_allow_injected=True)
    assert events==before

def test_private_writer_rejects_same_bytes_replacement(m,tmp_path,monkeypatch):
    r=m.Records(str(tmp_path.resolve()));fsync=m.os.fsync;done=[]
    def swap(fd):
        fsync(fd)
        if m.stat.S_ISDIR(m.os.fstat(fd).st_mode) and not done:
            done.append(True);target=tmp_path/'create.intent';target.rename(tmp_path/'retained')
            target.write_bytes((tmp_path/'retained').read_bytes());target.chmod(0o600)
    monkeypatch.setattr(m.os,'fsync',swap)
    with pytest.raises(m.ExecutorError):r.write('create.intent',{'x':1})

@pytest.fixture(autouse=True)
def no_native_resources(monkeypatch):
    import socket,threading,subprocess,ctypes,signal,resource
    calls=[]
    def forbidden(*a,**kw): calls.append(True);raise RuntimeError('native tripwire')
    for module,name in ((socket,'socket'),(threading,'Timer'),(subprocess,'Popen'),(ctypes,'CDLL'),
                        (os,'fork'),(signal,'setitimer'),(resource,'setrlimit'),(os,'statvfs')):
        monkeypatch.setattr(module,name,forbidden)
    yield
    assert calls==[], 'a native dependency was reached, even if normalized'

def test_default_wiring_is_import_and_constructor_inert(m,tmp_path):
    h=load(Path(__file__).with_name('test_av_pack_profile.py'),'executor_profile_fixtures')
    root,pin=h.stage(tmp_path);p=m.bootstrap_stage(str(root),pin)
    d=m.native_dependencies(p)
    api=d['api'](p,m.profile_module().make_recipe(p))
    assert api.injected_for_tests is False
    assert m.GuardianAPI(p,h.CID,200).injected_for_tests is False

def test_bootstrap_ignores_read_access_time_only(m,tmp_path,monkeypatch):
    op=tmp_path/'op';op.mkdir(mode=0o700);path=op/'pack_executor.py'
    raw=b'def main(argv): return 0\n';path.write_bytes(raw);path.chmod(0o600)
    monkeypatch.setattr(sys,'argv',['-c',str(path),m.sha(raw),'execute','/owned-stage','a'*64])
    original=os.fstat
    def access_changed(fd):
        s=original(fd)
        if m.stat.S_ISREG(s.st_mode):
            from types import SimpleNamespace
            return SimpleNamespace(**{name:(getattr(s,name)+100 if name=='st_atime' else getattr(s,name))
                                      for name in dir(s) if name.startswith('st_')})
        return s
    monkeypatch.setattr(os,'fstat',access_changed)
    with pytest.raises(SystemExit) as result: exec(m.BOOTSTRAP,{})
    assert result.value.code==0

def test_long_running_observations_keep_receipt_bounded(m,tmp_path):
    p,path,pin,deps,events,proc=rig(m,tmp_path);api=deps['api'](None,None);call=api.call;count=[0]
    h=load(Path(__file__).with_name('test_av_pack_profile.py'),'executor_profile_fixtures')
    state=h.fake(m.profile_module(),p,m.profile_module().make_recipe(p))
    state['State'].update(Status='running',Running=True,Pid=9,StartedAt='2026-11-03T00:00:00Z')
    def running(op):
        if op.value=='inspect' and 'start' in events and count[0]<120:
            count[0]+=1;events.append('inspect')
            return type('R',(),{'value':state,'injected_for_tests':True,
                'counters':type('C',(),{'__init__':lambda self:self.__dict__.update(wire_bytes=3000,overhead_bytes=100,
                    header_bytes=100,header_fields=2,chunks=0,framing='content-length')})()})()
        return call(op)
    api.call=running
    receipt=m.execute_once(p,operator_path=str(path),operator_sha256=pin,_deps=deps,_allow_injected=True)
    assert len(receipt)<=16384 and count[0]==120


def guardian_case(m,tmp_path,sequence,*,api_label=False,result_labels=None,kill_error=False,inspect_compat=False):
    p,path,pin,deps,events,proc=rig(m,tmp_path,inspect_compat=inspect_compat)
    m.execute_once(p,operator_path=str(path),operator_sha256=pin,_deps=deps,_allow_injected=True)
    r=m.Records(p.stage+'/results')
    hashes=[r.read(name)[1] for name in ('handle.json','wall.json','created.json')]
    for name in ('ready.json','done.json'): (Path(p.stage)/'results'/name).unlink()
    h=load(Path(__file__).with_name('test_av_pack_profile.py'),'guardian_review_fixtures')
    base=h.fake(m.profile_module(),p,m.profile_module().make_recipe(p))
    if inspect_compat:
        base['ImageManifestDescriptor']['platform']=dict(h.PLATFORM)
        base['HostConfig']['OomKillDisable']=None
    calls=[];now=[1.0];observations=[0];labels=list(result_labels or [False]*len(sequence))
    class API:
        injected_for_tests=api_label
        def inspect(self):
            i=observations[0];observations[0]+=1;calls.append(('inspect',h.CID))
            status=sequence[min(i,len(sequence)-1)]
            if status=='error':raise OSError('synthetic read error')
            state=json.loads(json.dumps(base))
            if status!='created':state['State'].update(Status=status,Running=status=='running',Pid=9 if status=='running' else 0,
                StartedAt='2026-11-03T00:00:00Z',FinishedAt='0001-01-01T00:00:00Z' if status=='running' else '2026-11-03T00:00:01Z')
            return type('R',(),{'value':state,'injected_for_tests':labels[min(i,len(labels)-1)]})()
        def kill(self):
            calls.append(('kill',h.CID))
            if kill_error: raise OSError('unknown stop outcome')
            return type('R',(),{'value':None,'injected_for_tests':False})()
        def inspect_after_stop(self):
            calls.append(('post',h.CID));return type('R',(),{'value':{},'injected_for_tests':False})()
    gd=dict(clock=lambda:now[0],boot=lambda:'b'*64,sleep=lambda n:now.__setitem__(0,now[0]+n),pid=lambda:456,api=lambda *_:API())
    def run():return m.guardian_main(p.stage,p.descriptor_sha256,str(path),pin,*hashes,_deps=gd,_allow_injected=True)
    return run,r,calls,now,p


def test_publication_retains_original_operator_inode(m,tmp_path):
    p,path,pin,deps,events,proc=rig(m,tmp_path);chunks=[];ident=m.authenticate_operator(str(path),pin)
    def write(fd,raw):
        if not chunks:
            retained=tmp_path/'retained-operator.py';path.rename(retained)
            path.write_bytes(retained.read_bytes());path.chmod(0o600)
            assert m.sha(path.read_bytes())==pin and m.authenticate_operator(str(path),pin)!=ident
            chunks.append(bytes(raw[:3]));return 3
        chunks.append(bytes(raw));return len(raw)
    assert m.main(['execute',p.stage,p.descriptor_sha256,str(path),pin],_deps=deps,_allow_injected=True,_write=write)==70
    assert len(chunks)==1 and len(chunks[0])==3


@pytest.mark.parametrize('sequence,success',[(('created','running','exited'),True),(('running','created','exited'),False)])
def test_guardian_observation_transition_is_monotonic(m,tmp_path,sequence,success):
    run,r,calls,now,p=guardian_case(m,tmp_path,sequence)
    rc=run()
    if success:
        assert rc==0 and [c[0] for c in calls]==['inspect']*3
        assert r.read('done.json')[0]['outcome']=='normal'
    else:
        assert rc==70 and [c[0] for c in calls]==['inspect','inspect','kill','post']
        assert now[0]>=181 and r.exists('kill.intent')
        assert all(cid=='2'*64 for _,cid in calls)


def test_guardian_dependency_injection_cannot_be_erased(m,tmp_path):
    run,r,calls,now,p=guardian_case(m,tmp_path,['exited'],api_label=False,result_labels=[False])
    assert run()==0
    assert r.read('ready.json')[0]['injected_for_tests'] is True
    assert r.read('done.json')[0]['injected_for_tests'] is True


def test_guardian_record_provenance_admission(m):
    assert callable(getattr(m,'admit_guardian_record',None)), 'guardian evidence needs provenance admission'
    expected={'pid':456,'cid':'2'*64}
    for record in (dict(expected,injected_for_tests=True),expected,dict(expected,injected_for_tests=0)):
        with pytest.raises(m.ExecutorError):m.admit_guardian_record(record,expected)
    assert m.admit_guardian_record(dict(expected,injected_for_tests=True),expected,allow_injected=True) is True
    with pytest.raises(m.ExecutorError):
        m.admit_guardian_record(dict(expected,injected_for_tests=False),expected,prior_injected=True,allow_injected=True)


@pytest.mark.parametrize('unknown',[False,True])
def test_guardian_durable_kill_claim_survives_reentry(m,tmp_path,unknown):
    run,r,calls,now,p=guardian_case(m,tmp_path,['error'],kill_error=unknown)
    assert run()==70
    pin=r.read('kill.intent')[1];before=list(calls)
    assert [c[0] for c in calls].count('kill')==1
    assert run()==70
    assert calls==before and r.read('kill.intent')[1]==pin


def test_guardian_unwritable_kill_claim_never_sends(m,tmp_path,monkeypatch):
    run,r,calls,now,p=guardian_case(m,tmp_path,['error'])
    write=m.Records.write;attempts=[]
    def fail(self,name,value):
        if name=='kill.intent':attempts.append(name);raise OSError('cannot persist stop claim')
        return write(self,name,value)
    monkeypatch.setattr(m.Records,'write',fail)
    assert run()==70
    assert attempts==['kill.intent'] and [c[0] for c in calls]==['inspect']
    assert r.read('done.json')[0]['outcome']=='kill-unclaimed'


def test_owned_prestart_guardian_receives_abort_and_bounded_reap(m,tmp_path):
    p,path,pin,deps,events,proc=rig(m,tmp_path,fault='ready')
    with pytest.raises(m.ExecutorError):m.execute_once(p,operator_path=str(path),operator_sha256=pin,_deps=deps,_allow_injected=True)
    assert 'start' not in events and 'reap' in events
    assert proc[0].wait_timeouts==[10.0] and not proc[0].dead and not proc[0].reaped
    assert (Path(p.stage)/'results/abort.intent').exists()


@pytest.mark.parametrize('target',['directory','read','write','bootstrap-directory','bootstrap-file'])
def test_fd_finalization_attempts_each_independent_owner_once(m,tmp_path,monkeypatch,target):
    parent=tmp_path.resolve()/'private';parent.mkdir(mode=0o700)
    operator=parent/'pack_executor.py';raw=b'def main(argv): return 0\n';operator.write_bytes(raw);operator.chmod(0o600)
    record=parent/'create.intent';record.write_bytes(b'{"x":1}');record.chmod(0o600)
    records=m.Records(str(parent))
    # Bootstrap exact OPDIR listing excludes this test record.
    if target.startswith('bootstrap'):record.unlink()
    open0,close0,fstat0=os.open,os.close,os.fstat
    owners={};opened=[];attempts=[];failed=[]
    def tracked_open(*a,**kw):
        fd=open0(*a,**kw);owner=len(opened);opened.append(owner);owners[fd]=owner;return fd
    def uncertain_close(fd):
        owner=owners.pop(fd,None);attempts.append(owner)
        isfile=m.stat.S_ISREG(fstat0(fd).st_mode)
        close0(fd)  # close may have succeeded before the interruption is raised
        if not failed and (isfile if target in ('read','write','bootstrap-file') else True):
            failed.append(owner);raise KeyboardInterrupt('private close outcome uncertain')
    try:
        with monkeypatch.context() as patch:
            patch.setattr(os,'open',tracked_open);patch.setattr(os,'close',uncertain_close)
            if target.startswith('bootstrap'):
                patch.setattr(sys,'argv',['-c',str(operator),m.sha(raw),'execute','/owned-stage','a'*64])
                with pytest.raises(SystemExit) as rc:exec(m.BOOTSTRAP,{})
                assert rc.value.code==70
            elif target=='directory':
                with pytest.raises(m.ExecutorError):m.safe(m.directory)(str(parent))
            elif target=='read':
                with pytest.raises(m.ExecutorError):records.read('create.intent')
            else:
                with pytest.raises(m.ExecutorError):records.write('start.intent',{'x':1})
        assert failed, 'close injection did not execute'
        assert not owners, 'independently owned descriptor skipped'
        assert None not in attempts and sorted(attempts)==opened, 'uncertain close was retried'
    finally:
        # Test harness owns only descriptors whose release was never attempted.
        # Do not retry any descriptor with an uncertain close outcome.
        for fd in list(owners):close0(fd)


@pytest.mark.parametrize('flags',[(False,False),(False,True),(True,True),(True,False)])
def test_controller_guardian_provenance_never_rolls_back(m,tmp_path,flags):
    p,path,pin,deps,events,proc=rig(m,tmp_path,guardian_flags=flags)
    if flags==(True,False):
        with pytest.raises(m.ExecutorError):m.execute_once(p,operator_path=str(path),operator_sha256=pin,_deps=deps,_allow_injected=True)
        assert 'stdout' not in events
    else:
        receipt=json.loads(m.execute_once(p,operator_path=str(path),operator_sha256=pin,_deps=deps,_allow_injected=True))
        assert receipt['injected_for_tests'] is True
        assert receipt['guardian_injected_for_tests'] is flags[1]


def test_scoped_guardian_api_injection_label_is_readonly(m,tmp_path):
    h=load(Path(__file__).with_name('test_av_pack_profile.py'),'guardian_label_fixtures')
    root,pin=h.stage(tmp_path);p=m.bootstrap_stage(str(root),pin)
    g=m.GuardianAPI(p,h.CID,200,clock=lambda:1.0)
    assert g.injected_for_tests is True
    with pytest.raises(AttributeError):g.injected_for_tests=False
    assert g.injected_for_tests is True


def test_kill_claim_persisted_but_unacknowledged_never_sends(m,tmp_path,monkeypatch):
    run,r,calls,now,p=guardian_case(m,tmp_path,['error'])
    write=m.Records.write;claimed=[]
    def interrupted(self,name,value):
        result=write(self,name,value)
        if name=='kill.intent':claimed.append(result);raise KeyboardInterrupt('claim completion unknown')
        return result
    monkeypatch.setattr(m.Records,'write',interrupted)
    assert run()==70 and claimed
    assert r.read('kill.intent')[1]==claimed[0]
    before=list(calls);assert [c[0] for c in calls]==['inspect']
    assert run()==70 and calls==before
    assert r.read('done.json')[0]['outcome']=='kill-unclaimed'


# Compatibility/drift checks are pure. Root separately authenticates the binary.
def interpreter_state(m,monkeypatch,*,captured='/trusted/python3.12',current='/trusted/python3.12',
                      version=(3,12,7),implementation='cpython',isolated=1,bytecode=1):
    from types import SimpleNamespace
    monkeypatch.setattr(m,'PYTHON',captured)
    monkeypatch.setattr(m,'sys',SimpleNamespace(executable=current,version_info=version,
        implementation=SimpleNamespace(name=implementation),
        flags=SimpleNamespace(isolated=isolated,dont_write_bytecode=bytecode)))


@pytest.mark.parametrize('changes',[{'version':(3,9,6)},{'captured':''},{'captured':None},
    {'captured':'relative/python'},{'current':'/other/python3.12'},{'implementation':'pypy'},
    {'isolated':0},{'bytecode':0},{'captured':b'/trusted/python3.12'}])
def test_native_interpreter_rejects_compatibility_and_drift(m,monkeypatch,changes):
    interpreter_state(m,monkeypatch,**changes)
    assert callable(getattr(m,'native_interpreter',None)), 'missing pure interpreter guard'
    with pytest.raises(m.ExecutorError,match='^'+m.SAFE+'$'): m.native_interpreter()


def test_native_interpreter_accepts_captured_modern_unchanged_path(m,monkeypatch):
    interpreter_state(m,monkeypatch)
    assert callable(getattr(m,'native_interpreter',None)), 'missing pure interpreter guard'
    assert m.native_interpreter()=='/trusted/python3.12'
    assert set(m.native_dependencies(object()))=={'clock','boot','sleep','capacity','api','launch'}


@pytest.mark.parametrize('entry',['dependencies','controller','launcher','guardian'])
def test_old_interpreter_rejected_before_native_factories(m,monkeypatch,entry):
    interpreter_state(m,monkeypatch,version=(3,9,6))
    calls=[]
    def reached(name):
        def fail(*a,**kw):
            calls.append(name)
            raise AssertionError('must reject interpreter before '+name)
        return fail
    monkeypatch.setattr(m,'profile_module',reached('profile'))
    monkeypatch.setattr(m,'bootstrap_stage',reached('bootstrap'))
    monkeypatch.setattr(m,'authenticate_operator',reached('authenticate'))
    monkeypatch.setattr(m,'GuardianAPI',reached('guardian-api'))
    if entry=='launcher': monkeypatch.setattr(m,'authenticate_operator',lambda *a:())
    with pytest.raises(m.ExecutorError):
        if entry=='dependencies': m.native_dependencies(object())
        elif entry=='controller': m.execute_once(object(),operator_path='/owned/pack_executor.py',operator_sha256='a'*64)
        elif entry=='launcher':
            from types import SimpleNamespace
            m.launch_guardian(dict(handle='c'*64,wall='d'*64,created='e'*64),None,
                prepared=SimpleNamespace(stage='/owned-stage',descriptor_sha256='f'*64),operator_path='/owned/pack_executor.py',
                operator_sha256='a'*64,_popen=reached('popen'))
        else: m.guardian_main('/owned','a'*64,'/owned/pack_executor.py','b'*64,'c'*64,'d'*64,'e'*64)
    assert calls==[], 'safe normalization must not hide native factory reachability'



def test_old_interpreter_stops_before_default_api_factory(m,tmp_path,monkeypatch):
    p,path,pin,deps,events,proc=rig(m,tmp_path)
    interpreter_state(m,monkeypatch,version=(3,9,6))
    calls=[]
    def api_factory(*a,**kw):
        calls.append('api');raise AssertionError('old interpreter reached native API factory')
    monkeypatch.setattr(m,'_capacity',lambda _:3*1024**3)
    monkeypatch.setattr(p.api,'APIClient',api_factory)
    # profile_module uses the real module registry; no native operation is needed.
    monkeypatch.setattr(m,'profile_module',lambda:sys.modules['_av_executor_profile_'+m.PROFILE_SHA])
    with pytest.raises(m.ExecutorError):m.execute_once(p,operator_path=str(path),operator_sha256=pin)
    assert calls==[] and events==[] and not proc
    assert not (Path(p.stage)/'results/create.intent').exists()


@pytest.mark.parametrize('terminal_drift',[False,True])
def test_complete_lifecycle_exact_platform_created_false_then_started_null(m,tmp_path,terminal_drift):
    p,path,pin,deps,events,proc=rig(m,tmp_path,inspect_compat=True)
    records=m.Records(p.stage+'/results')
    if terminal_drift:
        store=p.artifact.store_manifest
        def drift(*a,**kw):
            result=store(*a,**kw);api=deps['api'](None,None);call=api.call
            def changed(op):
                value=call(op)
                if op.value=='inspect':value.value['HostConfig']['OomKillDisable']=False
                return value
            api.call=changed
            return result
        p.artifact.store_manifest=drift
        with pytest.raises(m.ExecutorError):m.execute_once(p,operator_path=str(path),operator_sha256=pin,_deps=deps,_allow_injected=True)
        assert records.exists('abort.intent')
    else:
        dto=json.loads(m.execute_once(p,operator_path=str(path),operator_sha256=pin,_deps=deps,_allow_injected=True))
        created=records.read('created.json')[0];terminal=records.read('terminal.json')[0]['facts']
        assert created['configuration_sha256']==terminal['configuration_sha256']
        assert created['configuration_raw_sha256']!=terminal['configuration_raw_sha256']
        assert records.read('done.json')[0]['terminal']==terminal
        assert dto['injected_for_tests'] is True and dto['guardian_injected_for_tests'] is True
        assert proc[0].reaped and not records.exists('abort.intent') and not records.exists('kill.intent')
    assert events.count('create')==events.count('start')==events.count('stdout')==events.count('artifact')==1
    before=list(events)
    with pytest.raises(m.ExecutorError):m.execute_once(p,operator_path=str(path),operator_sha256=pin,_deps=deps,_allow_injected=True)
    assert events==before, 'a consumed lifecycle must not retry'



def test_real_guardian_accepts_only_policy_stable_started_null_facts(m,tmp_path):
    run,records,calls,now,p=guardian_case(m,tmp_path,['running','exited'],inspect_compat=True)
    assert run()==0
    created=records.read('created.json')[0];done=records.read('done.json')[0]
    assert done['outcome']=='normal' and done['injected_for_tests'] is True
    assert done['terminal']['configuration_sha256']==created['configuration_sha256']
    assert done['terminal']['configuration_raw_sha256']!=created['configuration_raw_sha256']
    assert [op for op,cid in calls]==['inspect','inspect'] and not records.exists('kill.intent')


@pytest.fixture(autouse=True)
def diagnostic_native_tripwires(monkeypatch):
    import socket, threading, subprocess, ctypes, os, signal, resource
    calls = []
    def forbidden(*a, **kw):
        calls.append(True)
        raise AssertionError("native resource reached")
    for mod, names in ((socket, ('socket',)), (threading, ('Thread', 'Timer')),
                       (subprocess, ('Popen',)), (ctypes, ('CDLL',)),
                       (os, ('fork', 'statvfs')), (signal, ('signal', 'alarm', 'setitimer', 'pthread_sigmask')),
                       (resource, ('getrlimit', 'setrlimit'))):
        for name in names:
            if hasattr(mod, name):
                monkeypatch.setattr(mod, name, forbidden)
    yield
    assert not calls, "native tripwire swallowed by normalized exception"



def test_diagnostic_record_exact_name_exclusive_and_current_pin(m,tmp_path):
    import hashlib
    assert m.API_SHA==hashlib.sha256((SOURCE/'pack_api.py').read_bytes()).hexdigest()
    assert m.PROFILE_SHA=='e04523a67281dd24fe66e4e2d168505fe5e5b314c4f3438dba69588e25cde3c9'
    r=m.Records(str(tmp_path));r.write('failure.json',{'format':'synthetic'})
    with pytest.raises(m.ExecutorError):r.write('failure.json',{'replace':True})
    for name in ('failure','Failure.json','failure.json/','../failure.json'):
        with pytest.raises(m.ExecutorError):r.write(name,{})
