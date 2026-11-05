"""Pure tiny synthetic capacity cases. No native resource execution."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import pytest
SOURCE=Path(__file__).resolve().parents[1]/'scripts/av_sources'
def load(path,name):
    s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
def test_observer_present():
    assert (SOURCE/"capacity_probe.py").exists(), "complete capacity observer missing"
@pytest.fixture
def m():
    assert (SOURCE/'capacity_probe.py').exists(), 'capacity observer missing'
    return load(SOURCE/'capacity_probe.py','capacity_test')
@pytest.fixture(autouse=True)
def tripwires(monkeypatch):
    import socket,threading,subprocess,ctypes,signal,resource
    calls=[]
    def no(*a,**kw): calls.append(1);raise AssertionError('native resource reached')
    for mod,names in ((socket,['socket']),(threading,['Timer','Thread']),(subprocess,['Popen']),
        (ctypes,['CDLL']),(os,['fork','statvfs']),(signal,['signal','alarm','setitimer','pthread_sigmask']),
        (resource,['setrlimit','getrlimit'])):
        for name in names:
            if hasattr(mod,name):monkeypatch.setattr(mod,name,no)
    yield
    assert not calls, 'native tripwire hidden by exception normalization'
def config(kind='foundation',proof=None):
    return dict(format='av-capacity-config-v1',kind=kind,nonce='1'*32,uid=os.geteuid(),gid=os.getegid(),foundation=proof)
def stats(low=False):
    return dict(bsize=4096,frsize=4096,blocks=1000000,bfree=900000,bavail=1 if low else 800000,fsid=1,flag=1)
def payload(kind='foundation',low=False):
    x=dict(format='av-capacity-measurement-v1',kind=kind,root=stats(low))
    if kind=='exact-log':
        ident=dict(dev=1,ino=2,mode=33152,uid=0,gid=0,nlink=1,size=100,mtime_ns=1,ctime_ns=1)
        x.update(log=stats(low),before=ident,after=copy.deepcopy(ident))
    return x
def mux(m,obj):
    b=m.canonical(obj)+b'\n';return b'\x01\0\0\0'+len(b).to_bytes(4,'big')+b
def test_fixed_recipe(m):
    r=m.build_recipe(config());b=r.body
    assert r.name=='run-av-pack-'+'1'*32 and r.create_bytes==m.canonical(b)
    assert b['HostConfig']['Mounts']==[] and b['HostConfig']['Memory']==67108864
    assert b['Entrypoint'][:4]==['/usr/local/bin/python3','-I','-B','-c']
    assert 'signal.alarm(5)' in b['Entrypoint'][4]
@pytest.mark.parametrize('bad',[{'extra':1},{'uid':True},{'nonce':'../x'},{'kind':'worker'},{'foundation':{}}])
def test_config_reject(m,bad):
    with pytest.raises(ValueError):m.build_recipe(dict(config(),**bad))
@pytest.mark.parametrize('kind',['foundation','exact-log'])
def test_measurement_low_is_valid(m,kind):
    v=m.decode_measurement(mux(m,payload(kind,True)),kind)
    assert v['capacity_ok'] is False and v['root']['available_bytes']==4096
@pytest.mark.parametrize('fault',['stream','reserved','truncated','extra','bool','overflow','duplicate','nan','noncanonical','identity'])
def test_measurement_reject(m,fault):
    p=payload('exact-log');b=mux(m,p)
    if fault=='stream': b=b'\x02'+b[1:]
    if fault=='reserved': b=b[:1]+b'\x01'+b[2:]
    if fault=='truncated':b=b[:-1]
    if fault=='extra':b+=b
    if fault=='bool':p['root']['bavail']=True;b=mux(m,p)
    if fault=='overflow':p['root']['blocks']=2**63;b=mux(m,p)
    if fault=='identity':p['after']['ino']=3;b=mux(m,p)
    if fault in ('duplicate','nan','noncanonical'):
        raw={'duplicate':b'{"kind":1,"kind":2}\n','nan':b'{"x":NaN}\n','noncanonical':b'{ "x":1}\n'}[fault]
        b=b'\x01\0\0\0'+len(raw).to_bytes(4,'big')+raw
    with pytest.raises(ValueError):m.decode_measurement(b,'exact-log')
def rig(m,tmp_path,fault=None,kind='foundation'):
    e=load(SOURCE/'pack_executor.py','capacity_executor')
    h=load(Path(__file__).with_name('test_av_pack_profile.py'),'capacity_fixture')
    root,pin=h.stage(tmp_path);p=e.bootstrap_stage(str(root),pin);pm=e.profile_module()
    c=config();events=[];now=[1.0];boot=['b'*64]
    foundation_c=config();foundation_c['nonce']='3'*32
    fr=m.build_recipe(foundation_c);fi=h.fake(pm,p,fr);fi['Id']='4'*64
    fi['LogPath']=m.log_path(fi['Id']);fi['State'].update(Status='exited',StartedAt='2026-11-03T00:00:00Z',FinishedAt='2026-11-03T00:00:01Z')
    ff=m.validate_inspect(fi,foundation_c,fr,fi['Id'],'terminal')
    proof=dict(format='av-capacity-foundation-v1',kind='foundation',nonce=foundation_c['nonce'],cid=fi['Id'],uid=c['uid'],gid=c['gid'],image=m.IMAGE,
        recipe_sha256=fr.sha256,config_sha256=m.sha(m.canonical(foundation_c)),configuration_sha256=ff['configuration_sha256'],state_sha256=ff['state_sha256'],boot='b'*64,injected_for_tests=False)
    if kind=='exact-log':c=config(kind,proof)
    r=m.build_recipe(c);state=h.fake(pm,p,r);state['LogPath']=''
    for mount in state['Mounts']:
        mount['Propagation']=next(x['BindOptions']['Propagation'] for x in r.body['HostConfig']['Mounts'] if x['Target']==mount['Destination'])
    class Timer:
        def __init__(self,seconds,cb):self.cb=cb;self.cancelled=False;events.append(('timer',seconds))
        def start(self):events.append('timer-start');self.cb() if fault=='early-cancel' else None
        def cancel(self):events.append('timer-cancel');self.cancelled=True
        def join(self,timeout):events.append(('join',timeout))
        def is_alive(self):return fault=='timer-live'
    class API:
        injected_for_tests=False
        cancelled=False
        def cancel(self):events.append('cancel');self.cancelled=True
        def bind_created(self,cid):events.append('bind');assert (root/'results/handle.json').exists()
        def bind_deadline(self,d):events.append(('deadline',d));assert d==31
        def call(self,op):
            name=op.value;events.append(name)
            if self.cancelled:raise ValueError('cancelled')
            if fault=='expiry' and name=='info':now[0]=31
            if fault=='boot' and name=='info':boot[0]='f'*64
            if fault=='unknown-create' and name=='create':raise ValueError('unknown')
            if name=='info':v=dict(OSType='linux',Architecture='arm64',DefaultRuntime='runc',CgroupVersion='2',DockerRootDir='/var/lib/docker')
            elif name=='image':v=json.loads(h.EVIDENCE.read_bytes())['current']
            elif name=='create':v=dict(Id=h.CID,Warnings=['bad'] if fault=='warnings' else None)
            elif name=='start':
                assert (root/'results/start.intent').exists()
                if fault=='unknown-start':raise ValueError('unknown')
                state['LogPath']=m.log_path(h.CID);state['State'].update(Status='exited',StartedAt='2026-11-03T00:00:00Z',FinishedAt='2026-11-03T00:00:01Z');v=None
            elif name=='inspect':v=copy.deepcopy(state)
            elif name=='stdout':v=b'bad' if fault=='stdout' else mux(m,payload(kind))
            else:raise AssertionError(name)
            return NS(value=v,injected_for_tests=False)
    class Guardian:
        injected_for_tests=False
        def __init__(self,p,cid,d):self.cid=cid;events.append(('guardian',cid))
        def inspect(self):
            events.append('borrow-inspect');v=copy.deepcopy(fi)
            if fault=='borrow-after' and events.count('borrow-inspect')==2:v['LogPath']='/bad'
            return NS(value=v,injected_for_tests=False)
        def kill(self):
            assert self.cid==h.CID;events.append('kill');assert (root/'results/kill.intent').exists()
            if fault=='unknown-start':raise ValueError('unknown-kill')
            return NS(value=None,injected_for_tests=False)
        def inspect_after_stop(self):events.append('post-stop');return NS(value={},injected_for_tests=False)
    api=API();deps=dict(clock=lambda:now[0],boot=lambda:boot[0],sleep=lambda n:now.__setitem__(0,now[0]+n),api=lambda *a:api,timer=Timer,guardian=Guardian)
    return p,e,c,str(root/'results'),deps,events,state
@pytest.mark.parametrize('kind',['foundation','exact-log'])
def test_complete_once(m,tmp_path,kind):
    p,e,c,path,d,events,state=rig(m,tmp_path,kind=kind)
    raw=m.run_case(p,e,c,path,_deps=d,_allow_injected=True);v=json.loads(raw)
    assert v['injected_for_tests'] is True and v['measurement']['capacity_ok'] is True
    assert events.count('create')==events.count('start')==events.count('stdout')==1
    assert events.count('borrow-inspect')==(2 if kind=='exact-log' else 0)
    assert events.index('timer-cancel')>events.index('stdout') and 'kill' not in events
    before=list(events)
    with pytest.raises(ValueError):m.run_case(p,e,c,path,_deps=d,_allow_injected=True)
    assert before==events
@pytest.mark.parametrize('fault',['warnings','unknown-create','unknown-start','stdout','expiry','boot','timer-live','early-cancel','borrow-after'])
def test_lifecycle_failures(m,tmp_path,fault):
    p,e,c,path,d,events,state=rig(m,tmp_path,fault,kind='exact-log')
    with pytest.raises(ValueError):m.run_case(p,e,c,path,_deps=d,_allow_injected=True)
    assert events.count('create')<=1 and events.count('start')<=1 and events.count('kill')<=1
    if fault=='warnings':assert Path(path,'handle.json').exists() and 'start' not in events
    if fault in ('unknown-start','stdout','timer-live','borrow-after'):assert events.count('kill')==1
    before=list(events)
    with pytest.raises(ValueError):m.run_case(p,e,c,path,_deps=d,_allow_injected=True)
    assert before==events
@pytest.mark.parametrize('fault',['path','mount','memory','network','oom'])
def test_inspect_reject(m,tmp_path,fault):
    p,e,c,path,d,events,state=rig(m,tmp_path)
    state['LogPath']=m.log_path(state['Id'])
    if fault=='path':state['LogPath']='/var/lib/docker/containers/other'
    if fault=='mount':state['Mounts']=[dict(Type='bind',Source='/var/lib/docker',Destination='/x',RW=False)]
    if fault=='memory':state['HostConfig']['Memory']=True
    if fault=='network':state['NetworkSettings']['IPAddress']='1.2.3.4'
    if fault=='oom':state['State']['OOMKilled']=True
    with pytest.raises(ValueError):m.validate_inspect(state,c,m.build_recipe(c),state['Id'],'created')
def test_injection_requires_explicit_allow(m,tmp_path):
    p,e,c,path,d,events,state=rig(m,tmp_path)
    with pytest.raises(ValueError):m.run_case(p,e,c,path,_deps=d)
    assert not events

@pytest.mark.parametrize('kind',['foundation','exact-log'])
def test_exact_retained_program_with_fake_imports(m,kind):
    import builtins,stat
    calls=[];printed=[]
    ident=dict(dev=1,ino=2,mode=33152,uid=0,gid=0,nlink=1,size=100,mtime_ns=1,ctime_ns=1)
    class OS:
        def statvfs(self,path):calls.append(('statvfs',path));return NS(**{'f_'+k:v for k,v in stats().items()})
        def lstat(self,path):calls.append(('lstat',path));return NS(**{'st_'+k:v for k,v in ident.items()})
    class Signal:
        SIGALRM=14;SIG_DFL=0;SIG_UNBLOCK=1
        def signal(self,*a):calls.append(('signal',a))
        def pthread_sigmask(self,*a):calls.append(('unblock',a))
        def alarm(self,n):calls.append(('alarm',n))
    modules={'os':OS(),'signal':Signal(),'json':json,'stat':stat}
    def imports(name,*a,**kw):assert name in modules;return modules[name]
    env=dict(vars(builtins),__import__=imports,print=lambda v:printed.append(v))
    program=m.FOUNDATION_PROGRAM if kind=='foundation' else m.LOG_PROGRAM
    exec(compile(program,'<fixed-public-probe>','exec'),{'__builtins__':env})
    assert calls[0][0]=='signal' and calls[0][1][0]==14
    handler=calls[0][1][1]
    assert callable(handler), 'namespace PID 1 requires an explicit Python alarm handler'
    assert calls[1:3]==[('unblock',(1,{14})),('alarm',5)]
    assert sum(event[0]=='alarm' for event in calls)==1
    with pytest.raises(SystemExit) as alarm_exit:handler(14,None)
    assert alarm_exit.value.code==124
    assert len(printed)==1
    raw=(printed[0]+'\n').encode();framed=b'\x01\0\0\0'+len(raw).to_bytes(4,'big')+raw
    assert m.decode_measurement(framed,kind)['capacity_ok'] is True
    expected=[('statvfs','/')]
    if kind=='exact-log':expected += [('lstat','/probe-log'),('statvfs','/probe-log'),('lstat','/probe-log')]
    assert calls[3:]==expected
    if kind=='exact-log':
        before=len(printed)
        def changed(path):
            ident['ino']+=1;return NS(**{'st_'+k:v for k,v in ident.items()})
        modules['os'].lstat=changed
        with pytest.raises(AssertionError):exec(compile(program,'<fixed-public-probe>','exec'),{'__builtins__':env})
        assert len(printed)==before

@pytest.mark.parametrize('mode',['before','during-factory','published'])
def test_original_global_cancel_poisoning_and_socket_publication(m,tmp_path,mode):
    # Real pinned APIClient and transport, synthetic in-memory socket and timer.
    p,e,c,path,d,events,state=rig(m,tmp_path)
    t=load(Path(__file__).with_name('test_av_pack_api.py'),'capacity_api_fixture')
    sockets=[];timers=[]
    def sf(*a):
        s=t.Socket(t.wire(t.encoded({'ok':True})));sockets.append(s)
        if mode=='during-factory':api.cancel()
        if mode=='published':s.connect=lambda path:api.cancel()
        return s
    def tf(*a):timer=t.Timer(*a);timers.append(timer);return timer
    api=p.api.APIClient(c['nonce'],m.build_recipe(c).create_bytes,http_module=p.http,
        socket_factory=sf,timer_factory=tf,clock=lambda:1.0)
    if mode=='before':api.cancel()
    with pytest.raises(ValueError):api.call(p.api.Operation.INFO)
    with pytest.raises(ValueError):api.call(p.api.Operation.INFO)
    assert len(sockets)==(0 if mode=='before' else 1)
    assert all(s.close_count==1 for s in sockets)

@pytest.mark.parametrize('fault',['unwritable','uncertain'])
def test_stop_claim_failure_never_sends(m,tmp_path,monkeypatch,fault):
    p,e,c,path,d,events,state=rig(m,tmp_path,'unknown-start')
    write=e.Records.write
    def broken(self,name,value):
        if name=='kill.intent':
            if fault=='uncertain':write(self,name,value)
            raise OSError('claim not acknowledged')
        return write(self,name,value)
    monkeypatch.setattr(e.Records,'write',broken)
    with pytest.raises(ValueError):m.run_case(p,e,c,path,_deps=d,_allow_injected=True)
    assert 'kill' not in events
    before=list(events)
    with pytest.raises(ValueError):m.run_case(p,e,c,path,_deps=d,_allow_injected=True)
    assert events==before

@pytest.mark.parametrize('fault',['expiry','boot','stage'])
def test_acknowledged_create_retained_before_post_fence(m,tmp_path,monkeypatch,fault):
    p,e,c,path,d,events,state=rig(m,tmp_path)
    api=d['api'](None,None);original=api.call;returned=[False]
    boot=d['boot'];check=e.profile_module().recheck_stage
    def create_then_drift(op):
        result=original(op)
        if op.value=='create':
            returned[0]=True
            if fault=='expiry':d['sleep'](30)
        return result
    api.call=create_then_drift
    if fault=='boot':d['boot']=lambda:'f'*64 if returned[0] else boot()
    if fault=='stage':
        def stage_check(*a,**kw):
            if returned[0]:raise ValueError('stage drift')
            return check(*a,**kw)
        monkeypatch.setattr(e.profile_module(),'recheck_stage',stage_check)
    with pytest.raises(ValueError):m.run_case(p,e,c,path,_deps=d,_allow_injected=True)
    record=e.Records(path).read('handle.json')[0]
    assert record['cid']=='2'*64 and record['name']=='run-av-pack-'+c['nonce']
    assert events.count('create')==1 and 'start' not in events and 'kill' not in events
    before=list(events)
    with pytest.raises(ValueError):m.run_case(p,e,c,path,_deps=d,_allow_injected=True)
    assert events==before

def test_acknowledged_create_cannot_write_replacement_records_directory(m,tmp_path):
    p,e,c,path,d,events,state=rig(m,tmp_path)
    api=d['api'](None,None);original=api.call;directory=Path(path);retained=directory.with_name('retained-results')
    def replace(op):
        result=original(op)
        if op.value=='create':directory.rename(retained);directory.mkdir(mode=0o700)
        return result
    api.call=replace
    with pytest.raises(ValueError):m.run_case(p,e,c,path,_deps=d,_allow_injected=True)
    assert list(directory.iterdir())==[] and not (retained/'handle.json').exists()
    assert events.count('create')==1 and 'start' not in events and 'kill' not in events

@pytest.mark.parametrize('value',[2**63,2**64-1])
@pytest.mark.parametrize('kind',['foundation','exact-log'])
def test_fsid_unsigned_identity_not_signed_capacity(m,value,kind):
    data=payload(kind);data['root']['fsid']=value
    if kind=='exact-log':data['log']['fsid']=value
    result=m.decode_measurement(mux(m,data),kind)
    assert result['root']['fsid']==value and result['root']['available_bytes']==800000*4096
    assert result['capacity_ok'] is True
@pytest.mark.parametrize('value',[2**64,-1,True])
def test_fsid_invalid_domain_rejected(m,value):
    data=payload();data['root']['fsid']=value
    with pytest.raises(ValueError):m.decode_measurement(mux(m,data),'foundation')

def test_cleanup_join_and_kill_share_ten_seconds_optional_read_skipped(m,tmp_path):
    p,e,c,path,d,events,state=rig(m,tmp_path,'stdout')
    timer=d['timer'];guardian=d['guardian'];start=d['clock']()
    class SlowTimer(timer):
        def join(self,timeout):super().join(timeout);assert timeout<=10;d['sleep'](1)
    class SlowGuardian(guardian):
        def kill(self):result=super().kill();d['sleep'](5);return result
        def inspect_after_stop(self):result=super().inspect_after_stop();d['sleep'](5);return result
    d.update(timer=SlowTimer,guardian=SlowGuardian)
    with pytest.raises(ValueError):m.run_case(p,e,c,path,_deps=d,_allow_injected=True)
    assert events.count('kill')==1 and 'post-stop' not in events
    assert d['clock']()-start==6 and Path(path,'kill.intent').exists()

@pytest.mark.parametrize('where',['cancel','join','claim'])
def test_cleanup_exhaustion_fences_claim_and_send(m,tmp_path,monkeypatch,where):
    p,e,c,path,d,events,state=rig(m,tmp_path,'stdout')
    if where=='cancel':
        api=d['api'](None,None);cancel=api.cancel
        def slow_cancel():cancel();d['sleep'](10)
        api.cancel=slow_cancel
    elif where=='join':
        timer=d['timer']
        class SlowTimer(timer):
            def join(self,timeout):super().join(timeout);d['sleep'](10)
        d['timer']=SlowTimer
    else:
        write=e.Records.write
        def slow_claim(self,name,value):
            result=write(self,name,value)
            if name=='kill.intent':d['sleep'](6)
            return result
        monkeypatch.setattr(e.Records,'write',slow_claim)
    with pytest.raises(ValueError):m.run_case(p,e,c,path,_deps=d,_allow_injected=True)
    assert 'kill' not in events and 'post-stop' not in events
    assert Path(path,'kill.intent').exists() is (where=='claim')

@pytest.mark.parametrize('fault',['unwritable','uncertain'])
def test_failed_create_handle_retention_is_not_retried(m,tmp_path,monkeypatch,fault):
    p,e,c,path,d,events,state=rig(m,tmp_path)
    api=d['api'](None,None);original=api.call;write=e.Records.write;attempts=[]
    def expire(op):
        result=original(op)
        if op.value=='create':d['sleep'](30)
        return result
    def fail_handle(self,name,value):
        if name=='handle.json':
            attempts.append(name)
            if fault=='uncertain':write(self,name,value)
            raise OSError('handle write not acknowledged')
        return write(self,name,value)
    api.call=expire;monkeypatch.setattr(e.Records,'write',fail_handle)
    with pytest.raises(ValueError):m.run_case(p,e,c,path,_deps=d,_allow_injected=True)
    assert attempts==['handle.json'] and 'start' not in events and 'kill' not in events
    assert Path(path,'handle.json').exists() is (fault=='uncertain')
    before=list(events)
    with pytest.raises(ValueError):m.run_case(p,e,c,path,_deps=d,_allow_injected=True)
    assert attempts==['handle.json'] and events==before


def test_observed_fixed_descriptor_platform_accepted_and_hash_retained(m,tmp_path):
    p,e,c,path,d,events,state=rig(m,tmp_path)
    recipe=m.build_recipe(c)
    baseline=m.validate_inspect(state,c,recipe,state['Id'],'created')
    state['ImageManifestDescriptor']['platform']={'architecture':'arm64','os':'linux','variant':'v8'}
    observed=m.validate_inspect(state,c,recipe,state['Id'],'created')
    assert observed['configuration_sha256']!=baseline['configuration_sha256']
    assert observed['state_sha256']==baseline['state_sha256']
    # Full raw descriptor stays in the existing observed-configuration hash.
    retained={k:state[k] for k in ('Id','Image','Name','Platform','ImageManifestDescriptor','Path','Args','Config','HostConfig')}
    retained['Mounts']=sorted(state['Mounts'],key=lambda row:row['Destination'])
    assert observed['configuration_sha256']==m.sha(m.canonical(retained))

@pytest.mark.parametrize('fault',['architecture','variant','os','top-key','platform-key','annotations','digest','size-bool'])
def test_descriptor_platform_has_no_additional_admission(m,tmp_path,fault):
    p,e,c,path,d,events,state=rig(m,tmp_path)
    descriptor=state['ImageManifestDescriptor']
    descriptor['platform']={'architecture':'arm64','os':'linux','variant':'v8'}
    if fault in ('architecture','variant','os'):descriptor['platform'][fault]='wrong'
    elif fault=='top-key':descriptor['extra']='unexpected'
    elif fault=='platform-key':descriptor['platform']['features']=[]
    elif fault=='annotations':descriptor['annotations']={}
    elif fault=='digest':descriptor['digest']='sha256:'+'0'*64
    elif fault=='size-bool':descriptor['size']=True
    with pytest.raises(ValueError):m.validate_inspect(state,c,m.build_recipe(c),state['Id'],'created')


def test_oom_null_same_policy_retains_unmodified_raw_configuration(m,tmp_path):
    p,e,c,path,d,events,state=rig(m,tmp_path)
    recipe=m.build_recipe(c)
    assert recipe.body['HostConfig']['OomKillDisable'] is False
    state['ImageManifestDescriptor']['platform']={'architecture':'arm64','os':'linux','variant':'v8'}
    baseline=m.validate_inspect(state,c,recipe,state['Id'],'created')
    state['HostConfig']['OomKillDisable']=None
    before=copy.deepcopy(state)
    observed=m.validate_inspect(state,c,recipe,state['Id'],'created')
    assert state==before, 'validator must not mutate raw inspect'
    assert observed['configuration_sha256']==baseline['configuration_sha256']
    assert observed['configuration_raw_sha256']!=baseline['configuration_raw_sha256']
    retained={k:state[k] for k in ('Id','Image','Name','Platform','ImageManifestDescriptor','Path','Args','Config','HostConfig')}
    retained['Mounts']=sorted(state['Mounts'],key=lambda row:row['Destination'])
    assert observed['configuration_raw_sha256']==m.sha(m.canonical(retained))
    policy=copy.deepcopy(retained);policy['HostConfig']['OomKillDisable']=False
    assert observed['configuration_sha256']==m.sha(m.canonical(policy))
    assert baseline['configuration_raw_sha256']==baseline['configuration_sha256']

@pytest.mark.parametrize('value',[True,0,1,'false',[],{}])
def test_oom_null_normalization_rejects_other_types(m,tmp_path,value):
    p,e,c,path,d,events,state=rig(m,tmp_path)
    state['HostConfig']['OomKillDisable']=value
    with pytest.raises(ValueError):m.validate_inspect(state,c,m.build_recipe(c),state['Id'],'created')

def test_oom_key_cannot_be_missing(m,tmp_path):
    p,e,c,path,d,events,state=rig(m,tmp_path)
    del state['HostConfig']['OomKillDisable']
    with pytest.raises(ValueError):m.validate_inspect(state,c,m.build_recipe(c),state['Id'],'created')

@pytest.mark.parametrize('kind',['foundation','exact-log'])
def test_complete_lifecycle_created_false_then_started_null(m,tmp_path,kind):
    p,e,c,path,d,events,state=rig(m,tmp_path,kind=kind)
    api=d['api'](None,None);original=api.call
    def normalize_after_start(op):
        result=original(op)
        if op.value=='start':state['HostConfig']['OomKillDisable']=None
        return result
    api.call=normalize_after_start
    receipt=json.loads(m.run_case(p,e,c,path,_deps=d,_allow_injected=True))
    created=e.Records(path).read('created.json')[0];terminal=receipt['terminal']
    assert terminal['configuration_sha256']==created['configuration_sha256']
    assert terminal['configuration_raw_sha256']!=created['configuration_raw_sha256']
    assert receipt['measurement']['capacity_ok'] is True and receipt['injected_for_tests'] is True
    assert events.count('create')==events.count('start')==events.count('stdout')==1
    assert 'kill' not in events


def test_diagnostic_unknown_create_record_without_receipt(m,tmp_path):
    p,e,c,path,d,events,state=rig(m,tmp_path,'unknown-create')
    with pytest.raises(ValueError):m.run_case(p,e,c,path,_deps=d,_allow_injected=True)
    fact=e.Records(path).read('failure.json')[0]
    assert fact['phase']=='create' and fact['cid'] is None and fact['start_attempted'] is False
    assert fact['api_failure'] is None and fact['injected_for_tests'] is True
    assert not Path(path,'done.json').exists() and events.count('create')==1


@pytest.mark.parametrize('op,phase,status',[('info','info',400),('create','create',400),('start','start',400)])
def test_diagnostic_exact_api_failure_and_stop_before_write(m,tmp_path,monkeypatch,op,phase,status):
    p,e,c,path,d,events,state=rig(m,tmp_path)
    api=d['api'](None,None);original=api.call
    api.last_failure=None
    def fail(operation):
        if operation.value==op:
            events.append(op)
            api.last_failure=p.api.FailureDiagnostic(op,'transport',status,True)
            raise RuntimeError('SECRET operation=forged http_status=599')
        return original(operation)
    api.call=fail;write=e.Records.write
    def track(self,name,value):
        if name=='failure.json':events.append('diagnostic')
        return write(self,name,value)
    monkeypatch.setattr(e.Records,'write',track)
    with pytest.raises(ValueError):m.run_case(p,e,c,path,_deps=d,_allow_injected=True)
    fact=e.Records(path).read('failure.json')[0]
    assert set(fact)=={'format','case','cid','phase','start_attempted','api_failure','injected_for_tests'}
    assert fact['format']=='av-capacity-failure-v1' and fact['phase']==phase
    assert fact['api_failure']==dict(operation=op,phase='transport',http_status=400,injected_for_tests=True)
    assert fact['cid']==('2'*64 if op=='start' else None)
    assert fact['start_attempted'] is (op=='start') and fact['injected_for_tests'] is True
    assert events.count(op)==1 and not Path(path,'done.json').exists()
    assert events.index('cancel')<events.index('diagnostic')
    if op=='start':assert events.count('kill')==1 and events.index('kill')<events.index('diagnostic')
    assert 'SECRET' not in Path(path,'failure.json').read_text()

@pytest.mark.parametrize('fault',['unwritable','swap','exhausted'])
def test_diagnostic_failure_preserves_stop_and_original_cleanup_budget(m,tmp_path,monkeypatch,fault):
    p,e,c,path,d,events,state=rig(m,tmp_path,'stdout');write=e.Records.write;attempts=[]
    def changed(self,name,value):
        if name=='failure.json':
            attempts.append(name)
            if fault=='unwritable':raise OSError('SECRET')
        return write(self,name,value)
    monkeypatch.setattr(e.Records,'write',changed)
    guardian=d['guardian'];retained=Path(path).with_name('retained-diagnostic')
    class Guardian(guardian):
        def kill(self):
            result=super().kill()
            if fault=='swap':Path(path).rename(retained);Path(path).mkdir(mode=0o700)
            if fault=='exhausted':d['sleep'](10)
            return result
    d['guardian']=Guardian
    with pytest.raises(ValueError):m.run_case(p,e,c,path,_deps=d,_allow_injected=True)
    assert events.count('kill')==1 and not Path(path,'failure.json').exists()
    assert attempts==(['failure.json'] if fault=='unwritable' else [])
    if fault=='swap':assert list(Path(path).iterdir())==[] and not (retained/'failure.json').exists()
    if fault=='exhausted':assert d['clock']()==11 and 'post-stop' not in events


def test_diagnostic_prior_record_refuses_without_calls_or_overwrite(m,tmp_path):
    p,e,c,path,d,events,state=rig(m,tmp_path);r=e.Records(path)
    r.write('failure.json',{'prior':True});before=Path(path,'failure.json').read_bytes()
    with pytest.raises(ValueError):m.run_case(p,e,c,path,_deps=d,_allow_injected=True)
    assert events==[] and Path(path,'failure.json').read_bytes()==before
    assert not Path(path,'create.intent').exists()


def test_diagnostic_success_never_writes_failure(m,tmp_path):
    p,e,c,path,d,events,state=rig(m,tmp_path)
    m.run_case(p,e,c,path,_deps=d,_allow_injected=True)
    assert not Path(path,'failure.json').exists()

@pytest.mark.parametrize('fault',['lookalike','mutated-status','getter'])
def test_diagnostic_untrusted_facts_do_not_escape_or_skip_stop(m,tmp_path,fault):
    p,e,c,path,d,events,state=rig(m,tmp_path,'stdout');api=d['api'](None,None)
    if fault=='lookalike':api.last_failure=NS(operation='create',phase='transport',http_status=400,injected_for_tests=False)
    elif fault=='mutated-status':
        api.last_failure=p.api.FailureDiagnostic('stdout','transport',200,False)
        object.__setattr__(api.last_failure,'http_status',True)
    else:
        class Broken(type(api)):
            @property
            def last_failure(self):raise RuntimeError('SECRET')
        api.__class__=Broken
    with pytest.raises(ValueError):m.run_case(p,e,c,path,_deps=d,_allow_injected=True)
    assert events.count('kill')==1 and not Path(path,'failure.json').exists()



def test_diagnostic_new_marker_in_success_path_forces_failure_without_overwrite(m,tmp_path):
    p,e,c,path,d,events,state=rig(m,tmp_path);api=d['api'](None,None);original=api.call
    def mark(op):
        result=original(op)
        if op.value=='info':e.Records(path).write('failure.json',{'prior':True})
        return result
    api.call=mark
    with pytest.raises(ValueError):m.run_case(p,e,c,path,_deps=d,_allow_injected=True)
    assert events.count('info')==1 and 'create' not in events
    assert e.Records(path).read('failure.json')[0]=={'prior':True}
    assert not Path(path,'done.json').exists()


def test_diagnostic_api_provenance_cannot_erase_existing_injection(m,tmp_path):
    p,e,c,path,d,events,state=rig(m,tmp_path,'unknown-create');api=d['api'](None,None)
    api.last_failure=p.api.FailureDiagnostic('create','transport',400,False)
    with pytest.raises(ValueError):m.run_case(p,e,c,path,_deps=d,_allow_injected=True)
    record=e.Records(path).read('failure.json')[0]
    assert record['injected_for_tests'] is True and record['api_failure']['injected_for_tests'] is False


def test_exact_log_recipe_requires_rslave(m,tmp_path):
    p,e,c,path,d,events,state=rig(m,tmp_path,kind='exact-log')
    recipe=m.build_recipe(c);body=recipe.body;host=body['HostConfig']
    expected=dict(Type='bind',Source=m.log_path(c['foundation']['cid']),Target='/probe-log',ReadOnly=True,
        BindOptions=dict(Propagation='rslave',NonRecursive=True,CreateMountpoint=False))
    assert host['Mounts']==[expected]
    assert host['Memory']==host['MemorySwap']==67108864 and host['NanoCpus']==1000000000
    assert host['PidsLimit']==8 and host['ReadonlyRootfs'] is True and host['NetworkMode']=='none'
    assert host['Binds']==host['VolumesFrom']==[] and host['Tmpfs']=={}
    assert body['Entrypoint']==['/usr/local/bin/python3','-I','-B','-c',m.LOG_PROGRAM]
    assert state['Mounts']==[dict(Type='bind',Source=expected['Source'],Destination='/probe-log',RW=False,Propagation='rslave')]
    m.validate_inspect(state,c,recipe,state['Id'],'created')


@pytest.mark.parametrize('section',['HostConfig','Mounts'])
@pytest.mark.parametrize('mode',['rprivate','rshared','slave','private',''])
def test_exact_log_wrong_propagation_rejected_independently(m,tmp_path,section,mode):
    p,e,c,path,d,events,state=rig(m,tmp_path,kind='exact-log')
    recipe=m.build_recipe(c)
    m.validate_inspect(state,c,recipe,state['Id'],'created')
    before=copy.deepcopy(state)
    if section=='HostConfig':state['HostConfig']['Mounts'][0]['BindOptions']['Propagation']=mode
    else:state['Mounts'][0]['Propagation']=mode
    with pytest.raises(ValueError):m.validate_inspect(state,c,recipe,state['Id'],'created')
    assert (state['Mounts']==before['Mounts']) if section=='HostConfig' else (state['HostConfig']==before['HostConfig'])
