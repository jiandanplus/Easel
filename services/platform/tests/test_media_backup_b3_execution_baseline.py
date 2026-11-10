"""Synthetic raw observer parsing; no Linux proc calls."""
import unittest
import copy
import stat
import json
import types
from unittest.mock import patch
from test_media_backup_b3_execution_observer import STATUS,STAT
from test_media_backup_b3_network_fixtures import wire_fixture,PROFILE,SECURITY
from platform_service.media_backup_b3 import execution_observer as observer
from platform_service.media_backup_b3.contracts import B3Error,canonical
try:
    from platform_service.media_backup_b3 import execution_baseline as baseline
except ImportError:baseline=None

def fixture():
    ns=wire_fixture()['namespace_before']
    exe=dict(device=2,inode=3,mode=stat.S_IFREG|0o755,uid=0,gid=0,size=100,mtime_ns=1,ctime_ns=1,target='/usr/local/bin/python3.12')
    snap=dict(own_pid=8,own_netns=ns,pid1_netns=ns,pid1_start_time=123,pid1_stat_raw=STAT,
        own_exe=exe,pid1_exe=exe,own_status=observer._status(STATUS.encode(),8),
        pid1_status=observer._status(STATUS.replace('Pid:\t8','Pid:\t1').encode(),1))
    return dict(format=observer._RESPONSE_FORMAT,before=snap,after=copy.deepcopy(snap),raw_network=wire_fixture())

def production_frame():
    """Real producer/serializer/framer; only kernel acquisition and stdout are fake."""
    from platform_service.media_backup_b3.network_wire import decode_network_observation
    v=fixture();out=bytearray();deadline=7_000_000_000
    def write(fd,data):
        assert fd==1
        size=min(127,len(data));out.extend(data[:size]);return size
    fake_os=types.SimpleNamespace(**vars(observer.os))
    with patch.object(observer,'_monotonic_ns',return_value=1_000_000_000), \
         patch.object(observer,'_snapshot',side_effect=[v['before'],v['after']]), \
         patch.object(observer,'observe_worker_network',return_value=decode_network_observation(v['raw_network'])), \
         patch.object(observer,'os',fake_os), \
         patch.object(fake_os,'set_blocking',return_value=None), \
         patch.object(fake_os,'get_blocking',return_value=False), \
         patch.object(fake_os,'write',side_effect=write), \
         patch.object(observer,'_select',return_value=([],[1],[])):
        value=observer._observe(deadline)
        observer._write_frame(observer._contract._canonical(value),deadline)
    return bytes(out)

def worker_case():
    from test_media_backup_b3_execution import _SYNTHETIC_WORKER
    from platform_service.media_backup_b3 import execution
    from platform_service.media_backup_b3.contracts import digest
    ns=types.SimpleNamespace;running=copy.deepcopy(_SYNTHETIC_WORKER);cid=running['Id'];target_cid='4'*64
    target=dict(Id=target_cid,Config=dict(Hostname='trusted-target',Domainname='synthetic.test'))
    for field,leaf in [('ResolvConfPath','resolv.conf'),('HostnamePath','hostname'),('HostsPath','hosts')]:
        target[field]='/var/lib/docker/containers/'+target_cid+'/'+leaf;running[field]=target[field]
    running['LogPath']='/var/lib/docker/containers/'+cid+'/'+cid+'-json.log'
    running['HostConfig']['NetworkMode']='container:'+target_cid
    running['Config'].update(Hostname=target['Config']['Hostname'],Domainname=target['Config']['Domainname'],Image=running['Image'])
    created=copy.deepcopy(running);created['State'].update(Status='created',Running=False,Pid=0,StartedAt='0001-01-01T00:00:00Z')
    created['HostConfig']['OomKillDisable']=False;created['Config'].update(Hostname=cid[:12],Domainname='')
    for field in ('ResolvConfPath','HostnamePath','HostsPath','LogPath'):created[field]=''
    exited=copy.deepcopy(running);exited['State'].update(Status='exited',Running=False,Pid=0,FinishedAt='2026-11-04T10:42:13Z')
    limits=execution.ExecutionLimits(10**9,512*1024**2,512*1024**2,64,16*1024**2,65536,1024**2,65536)
    o=ns(_policy=ns(limits=limits,worker=ns(image_id=running['Image'],architecture='arm64',public_env=tuple(running['Config']['Env']))))
    w=ns(cid=cid,name='synthetic',labels=running['Config']['Labels'],running=None,immutable=None)
    t=ns(cid=target_cid,original_attempt=ns(_running=target),running_pin=digest(target))
    return o,ns(worker_slot=w,target_slot=t),created,running,exited

class BaselineTests(unittest.TestCase):
    def setUp(self):self.assertIsNotNone(baseline,'host must independently reparse original raw baseline')
    def test_real_observer_frame_is_reparsed_by_host(self):
        raw=production_frame();ns=wire_fixture()['namespace_before']
        try:result=baseline._observer(raw,ns)
        except B3Error as exc:
            self.fail('host rejected real producer frame format '+json.loads(raw[4:])['format']+': '+str(exc))
        self.assertEqual(result['namespace'],ns)
        self.assertEqual(result['profile_sha256'],PROFILE)
        self.assertEqual(result['security_sha256'],SECURITY)
        self.assertEqual(result['raw']['raw_network'],wire_fixture())
        self.assertEqual(result['pid1_start_time'],123)
        self.assertEqual(result['exe'],fixture()['before']['pid1_exe'])

    def test_real_observer_frame_closed_format_and_raw_guards(self):
        raw=production_frame();v=json.loads(raw[4:]);ns=v['before']['own_netns']
        def frame(value):
            body=canonical(value);return len(body).to_bytes(4,'big')+body
        changes=[('old_wrong_format',lambda x:x.update(format='easel.b3.execution-baseline-observation.v1')),
            ('unknown_format',lambda x:x.update(format='unknown')),
            ('extra_field',lambda x:x.update(security_sha256=SECURITY)),
            ('missing_field',lambda x:x.pop('raw_network')),
            ('derived_status',lambda x:x['after']['own_status'].update(cap_effective=1)),
            ('raw_stat',lambda x:x['after'].update(pid1_stat_raw=STAT.replace('123','124'))),
            ('network_namespace',lambda x:x['raw_network']['namespace_after'].update(inode=999)),
            ('raw_network',lambda x:x['raw_network']['proc_after'].update(dev='00'))]
        for name,change in changes:
            value=copy.deepcopy(v);change(value)
            with self.subTest(case=name),self.assertRaises(B3Error):baseline._observer(frame(value),ns)
        malformed=[b'',b'\x00'*4,b'\x00\x00\x00\x01{',raw[:-1],raw+b'\x00',
            (1024**2+1).to_bytes(4,'big')+raw[4:]]
        for index,value in enumerate(malformed):
            with self.subTest(frame=index),self.assertRaises(B3Error):baseline._observer(value,ns)
        with self.assertRaises(B3Error):baseline._observer(raw,dict(device=999,inode=998))

    def test_raw_baseline_reparsed(self):
        v=fixture();raw=canonical(v);ns=v['before']['own_netns']
        result=baseline._observer(len(raw).to_bytes(4,'big')+raw,ns)
        self.assertEqual(result['namespace'],ns)
        self.assertEqual(len(result['security_sha256']),64)
    def test_derived_fields_and_raw_drift_rejected(self):
        for key in ('own_netns','pid1_start_time','pid1_exe','own_status','pid1_stat_raw'):
            v=fixture();s=v['after']
            if key=='own_netns':s[key]=dict(device=999,inode=998)
            elif key=='pid1_start_time':s[key]+=1
            elif key=='pid1_exe':s[key]=dict(s[key],inode=9)
            elif key=='own_status':s[key]=dict(s[key],cap_effective=1)
            else:s[key]=s[key].replace('123','124')
            raw=canonical(v)
            with self.subTest(key=key),self.assertRaises(B3Error):baseline._observer(len(raw).to_bytes(4,'big')+raw,v['before']['own_netns'])

    def test_worker_closed_defaults_reject_privilege_mount_env_and_unknown_fields(self):
        o,a,created,template,exited=worker_case();slot=a.worker_slot
        baseline._worker(canonical([template]),o,a,'running')
        for path,value in ((('HostConfig','Privileged'),True),(('HostConfig','PidMode'),'host'),
            (('Mounts',),[{'Source':'/socket'}]),(('Config','Env'),['EVIL=1']),
            (('ImageManifestDescriptor','platform','architecture'),'amd64'),(('unexpected',),1)):
            v=copy.deepcopy(template);node=v
            for key in path[:-1]:node=node[key]
            node[path[-1]]=value
            slot.running=None;slot.immutable=None
            with self.subTest(path=path),self.assertRaises(B3Error):baseline._worker(canonical([v]),o,a,'running')

    def test_worker_phase_projection_accepts_trusted_target_and_keeps_raw(self):
        o,a,created,running,exited=worker_case();raws=copy.deepcopy((created,running,exited))
        for phase,value in [('created',created),('running',running),('running',copy.deepcopy(running)),('exited',exited)]:
            self.assertEqual(baseline._worker(canonical([value]),o,a,phase),value)
        self.assertEqual((created,running,exited),raws)
        stable=a.worker_slot.immutable
        self.assertEqual(stable['HostnamePath'],a.target_slot.original_attempt._running['HostnamePath'])
        self.assertEqual(stable['Config']['Hostname'],'trusted-target')
        self.assertEqual(stable['HostConfig']['OomKillDisable'],None)

    def test_worker_paths_and_hostnames_reject_independent_mutants(self):
        cases=[(('ResolvConfPath',),'/var/lib/docker/containers/'+'5'*64+'/resolv.conf'),
            (('HostnamePath',),'/var/lib/docker/containers/'+'4'*64+'/hosts'),
            (('HostsPath',),'/other/containers/'+'4'*64+'/hosts'),
            (('HostsPath',),'/var/lib/docker/containers/'+'4'*64+'/../hosts'),
            (('LogPath',),'/var/lib/docker/containers/'+'4'*64+'/'+'4'*64+'-json.log'),
            (('Config','Hostname'),'worker-self-selected'),(('Config','Domainname'),'worker-selected.test'),
            (('HostConfig','OomKillDisable'),True),(('HostConfig','OomKillDisable'),0),
            (('HostConfig','OomKillDisable'),'false'),(('HostConfig','Memory'),1),
            (('ImageManifestDescriptor','digest'),'sha256:'+'f'*64)]
        for path,value in cases:
            o,a,created,running,exited=worker_case();baseline._worker(canonical([created]),o,a,'created')
            node=running
            for key in path[:-1]:node=node[key]
            node[path[-1]]=value
            with self.subTest(path=path,value=value),self.assertRaises(B3Error):baseline._worker(canonical([running]),o,a,'running')
        for missing in ('HostsPath','LogPath'):
            o,a,created,running,exited=worker_case();running.pop(missing)
            with self.subTest(missing=missing),self.assertRaises(B3Error):baseline._worker(canonical([running]),o,a,'running')
        o,a,created,running,exited=worker_case();running['UnexpectedPath']=''
        with self.assertRaises(B3Error):baseline._worker(canonical([running]),o,a,'running')

    def test_worker_created_paths_and_hostname_must_not_be_initialized(self):
        for field in ('ResolvConfPath','HostnamePath','HostsPath','LogPath'):
            o,a,created,running,exited=worker_case();created[field]=running[field]
            with self.subTest(field=field),self.assertRaises(B3Error):baseline._worker(canonical([created]),o,a,'created')
        o,a,created,running,exited=worker_case();created['Config']['Hostname']='trusted-target'
        with self.assertRaises(B3Error):baseline._worker(canonical([created]),o,a,'created')

    def test_worker_original_target_snapshot_only_and_reference_drift_rejected(self):
        for mode in ('cid','path','hostname','replacement'):
            o,a,created,running,exited=worker_case()
            if mode=='cid':a.target_slot.original_attempt._running['Id']='5'*64
            elif mode=='path':a.target_slot.original_attempt._running['HostsPath']='/wrong'
            elif mode=='hostname':a.target_slot.original_attempt._running['Config']['Hostname']='changed'
            else:a.target_slot.original_attempt=types.SimpleNamespace(_running=dict(running))
            with self.subTest(mode=mode),self.assertRaises(B3Error):baseline._worker(canonical([created]),o,a,'created')
        o,a,created,running,exited=worker_case()
        a.target_slot._running={'Id':'5'*64,'Config':{'Hostname':'not-authority'}}
        baseline._worker(canonical([created]),o,a,'created');baseline._worker(canonical([running]),o,a,'running')

    def test_worker_later_path_changes_never_renormalize(self):
        o,a,created,running,exited=worker_case()
        baseline._worker(canonical([created]),o,a,'created');baseline._worker(canonical([running]),o,a,'running')
        exited['LogPath']+='.rotated'
        with self.assertRaises(B3Error):baseline._worker(canonical([exited]),o,a,'exited')

    def test_worker_pinned_target_metadata_still_requires_known_cid_root(self):
        from platform_service.media_backup_b3.contracts import digest
        for key,value in [('HostsPath','/tmp/containers/'+'4'*64+'/hosts'),('HostnamePath','/var/lib/docker/containers/'+'5'*64+'/hostname'),('Id','5'*64)]:
            o,a,created,running,exited=worker_case();target=a.target_slot.original_attempt._running
            target[key]=value;a.target_slot.running_pin=digest(target)
            with self.subTest(key=key),self.assertRaises(B3Error):baseline._worker(canonical([created]),o,a,'created')

    def test_worker_oom_missing_is_not_a_valid_null_default(self):
        o,a,created,running,exited=worker_case();baseline._worker(canonical([created]),o,a,'created')
        running['HostConfig'].pop('OomKillDisable')
        with self.assertRaises(B3Error):baseline._worker(canonical([running]),o,a,'running')
