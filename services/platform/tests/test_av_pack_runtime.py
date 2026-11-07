"""Runtime collector tests use fake proc/sys/resource/signal operations only."""
import importlib.util
from pathlib import Path

import pytest

PATH=Path(__file__).resolve().parents[1]/'scripts/av_sources/pack_runtime.py'


def runtime():
    spec=importlib.util.spec_from_file_location('pure_av_runtime',PATH)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    return mod


def mountinfo():
    return '\n'.join([
        '1 0 0:1 / / ro - overlay overlay ro',
        '2 1 0:2 /stage/code /code ro - ext4 /dev/fake ro',
        '3 1 0:2 /stage/inputs /inputs ro - ext4 /dev/fake ro',
        '4 1 0:3 / /work rw,noexec,nosuid,nodev - tmpfs tmpfs rw,size=32768k,mode=700,uid=1000,gid=1000',
        '5 1 0:4 / /sys/fs/cgroup ro - cgroup2 cgroup ro',
        '6 1 0:5 / /dev/shm rw,noexec,nosuid,nodev - tmpfs shm rw,size=1024k',
        '7 1 0:6 / /proc rw,nosuid,nodev,noexec - proc proc rw',
        '8 1 0:7 / /dev rw,nosuid - tmpfs tmpfs rw,size=65536k',
    ])


def test_mount_policy_exact_and_unknown_writable():
    r=runtime();p={'uid':1000,'gid':1000}
    assert r._mounts(mountinfo(),p) is True
    for text in [mountinfo().replace('/code ro','/code rw'), mountinfo().replace('size=32768k','size=65536k'),
                 mountinfo().replace('size=1024k','size=65536k'),mountinfo().replace('uid=1000','uid=0'),
                 mountinfo().replace('/sys/fs/cgroup ro','/sys/fs/cgroup rw'),
                 mountinfo()+'\n9 1 0:9 / /data rw - ext4 /dev/fake rw']:
        with pytest.raises(ValueError): r._mounts(text,p)


def test_collector_only_reads_bounded_fixed_fake_paths(monkeypatch):
    r=runtime();p={'uid':1000,'gid':1000}
    status='\n'.join([f'{k}: 0000000000000000' for k in ('CapInh','CapPrm','CapEff','CapBnd','CapAmb')]+['NoNewPrivs: 1','Seccomp: 2'])
    files={'/proc/self/status':status,'/proc/1/status':'SigBlk: 0000000000000000', '/proc/self/cgroup':'0::/private/test',
           '/proc/self/mountinfo':mountinfo(),'/proc/net/route':'Iface Destination\n','/proc/net/ipv6_route':'',
           '/sys/fs/cgroup/private/test/memory.max':'536870912','/sys/fs/cgroup/private/test/memory.swap.max':'0',
           '/sys/fs/cgroup/private/test/pids.max':'16','/sys/fs/cgroup/private/test/cpu.max':'200000 100000'}
    seen=[]
    def read(path,cap=65536):
        seen.append((path,cap));return files[path]
    monkeypatch.setattr(r,'_read',read)
    monkeypatch.setattr(r.platform,'system',lambda:'Linux');monkeypatch.setattr(r.platform,'machine',lambda:'aarch64')
    for name,value in [('geteuid',1000),('getegid',1000),('getpid',2),('getppid',1),('getgroups',[])]:
        monkeypatch.setattr(r.os,name,lambda value=value:value)
    monkeypatch.setattr(r.os,'listdir',lambda path:['lo'] if path=='/sys/class/net' else pytest.fail('unexpected path'))
    by_id={getattr(r.resource,'RLIMIT_'+k):v for k,v in r.LIMITS.items()}
    monkeypatch.setattr(r.resource,'getrlimit',lambda key:by_id[key])
    monkeypatch.setattr(r.signal,'getitimer',lambda key:(119.8,0))
    monkeypatch.setattr(r.signal,'pthread_sigmask',lambda *args:set())
    monkeypatch.setattr(r.signal,'getsignal',lambda *args:r.signal.SIG_DFL)
    monkeypatch.setattr(r.signal,'sigpending',lambda:set())
    monkeypatch.setattr(r.os,'readlink',lambda path:'cgroup:[1234]' if path in ('/proc/self/ns/cgroup','/proc/1/ns/cgroup') else pytest.fail('unexpected ns'))
    proof=r.collect_runtime(p,parent_pid=1,timer_arms=1,supervisor_signals_ok=True)
    assert proof['cgroup_path']=='/private/test'
    assert all(0<cap<=65536 for _,cap in seen)
    files['/proc/self/cgroup']='0::/'
    for key in ('memory.max','memory.swap.max','pids.max','cpu.max'):
        files['/sys/fs/cgroup/'+key]=files['/sys/fs/cgroup/private/test/'+key]
    proof=r.collect_runtime(p,parent_pid=1,timer_arms=1,supervisor_signals_ok=True)
    assert proof['cgroup_path']=='/' and proof['cgroup_scope']=='namespace-relative;host-inspect-required'
    for key,value in [('/sys/fs/cgroup/memory.max','max'),('/sys/fs/cgroup/pids.max','max'),('/sys/fs/cgroup/cpu.max','max 100000'),('/proc/self/mountinfo',mountinfo().replace('0:4 / /sys/fs/cgroup','0:4 /other /sys/fs/cgroup'))]:
        before=files[key];files[key]=value
        with pytest.raises(ValueError): r.collect_runtime(p,parent_pid=1,timer_arms=1,supervisor_signals_ok=True)
        files[key]=before
    for kind in ('handler','blocked','pending','expired','supervisor-blocked','supervisor-missing'):
        with monkeypatch.context() as patch:
            if kind=='handler': patch.setattr(r.signal,'getsignal',lambda *args:object())
            if kind=='blocked': patch.setattr(r.signal,'pthread_sigmask',lambda *args:{r.signal.SIGALRM})
            if kind=='pending': patch.setattr(r.signal,'sigpending',lambda:{r.signal.SIGALRM})
            if kind=='expired': patch.setattr(r.signal,'getitimer',lambda key:(0,0))
            old=files['/proc/1/status']
            if kind=='supervisor-blocked': files['/proc/1/status']='SigBlk: 0000000000004000'
            if kind=='supervisor-missing': files['/proc/1/status']=''
            with pytest.raises(ValueError): r.collect_runtime(p,parent_pid=1,timer_arms=1,supervisor_signals_ok=True)
            files['/proc/1/status']=old
