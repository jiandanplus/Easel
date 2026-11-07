"""Pure host parsers for the fixed target and installed observer transcripts."""
import hashlib
import re
import stat
from .contracts import require,canonical,digest
from .execution_contract import _unframe,_exact,_closed
from .execution_observer import _status,_start,_RESPONSE_FORMAT
from .network_wire import decode_network_observation
from .network_profile import validate_network_profile

_HOST_DEFAULTS = {'Binds': None, 'ContainerIDFile': '', 'LogConfig': {'Type': 'json-file', 'Config': {}}, 'PortBindings': {}, 'RestartPolicy': {'Name': 'no', 'MaximumRetryCount': 0}, 'AutoRemove': False, 'VolumeDriver': '', 'VolumesFrom': None, 'ConsoleSize': [0, 0], 'CapAdd': None, 'CapDrop': ['ALL'], 'CgroupnsMode': 'private', 'Dns': None, 'DnsOptions': [], 'DnsSearch': [], 'ExtraHosts': None, 'GroupAdd': None, 'IpcMode': 'private', 'Cgroup': '', 'Links': None, 'OomScoreAdj': 0, 'PidMode': '', 'Privileged': False, 'PublishAllPorts': False, 'ReadonlyRootfs': True, 'SecurityOpt': ['no-new-privileges:true'], 'UTSMode': '', 'UsernsMode': '', 'ShmSize': 67108864, 'Runtime': 'runc', 'Isolation': '', 'CpuShares': 0, 'CgroupParent': '', 'BlkioWeight': 0, 'BlkioWeightDevice': [], 'BlkioDeviceReadBps': [], 'BlkioDeviceWriteBps': [], 'BlkioDeviceReadIOps': [], 'BlkioDeviceWriteIOps': [], 'CpuPeriod': 0, 'CpuQuota': 0, 'CpuRealtimePeriod': 0, 'CpuRealtimeRuntime': 0, 'CpusetCpus': '', 'CpusetMems': '', 'Devices': [], 'DeviceCgroupRules': None, 'DeviceRequests': None, 'MemoryReservation': 0, 'MemorySwappiness': None, 'Ulimits': [], 'CpuCount': 0, 'CpuPercent': 0, 'IOMaximumIOps': 0, 'IOMaximumBandwidth': 0, 'MaskedPaths': ['/proc/acpi', '/proc/asound', '/proc/interrupts', '/proc/kcore', '/proc/keys', '/proc/latency_stats', '/proc/sched_debug', '/proc/scsi', '/proc/timer_list', '/proc/timer_stats', '/sys/devices/virtual/powercap', '/sys/firmware'], 'ReadonlyPaths': ['/proc/bus', '/proc/fs', '/proc/irq', '/proc/sys', '/proc/sysrq-trigger']}
_ENTRY=['/opt/easel/bin/python','-I','-B','-m','platform_service.media_backup_b3.execution_worker']

def _one(raw):
    from .target_factory import _json
    v=_json(raw);require(type(v) is list and len(v)==1 and type(v[0]) is dict);return v[0]

def _observer(raw,namespace):
    v=_unframe(raw,1024**2)
    _closed(v,('format','before','after','raw_network'))
    _exact(v['format'],_RESPONSE_FORMAT)
    stable=[]
    for s in (v['before'],v['after']):
        _closed(s,('own_pid','own_netns','pid1_netns','pid1_start_time','pid1_stat_raw','own_exe','pid1_exe','own_status','pid1_status'))
        require(type(s['own_pid']) is int and 1<s['own_pid']<2**32)
        _exact(s['own_netns'],namespace);_exact(s['pid1_netns'],namespace)
        _exact(s['pid1_start_time'],_start(s['pid1_stat_raw'].encode('ascii')))
        for key,pid in (('own_status',s['own_pid']),('pid1_status',1)):
            r=s[key];require(type(r) is dict and type(r.get('raw')) is str)
            _exact(r,_status(r['raw'].encode('ascii'),pid))
        for key in ('own_exe','pid1_exe'):
            e=s[key]
            _closed(e,('device','inode','mode','uid','gid','size','mtime_ns','ctime_ns','target'))
            require(all(type(e[k]) is int and 0<=e[k]<2**64 for k in e if k!='target'))
            require(e['device']>0 and e['inode']>0 and e['size']>0)
            require(stat.S_ISREG(e['mode']) and e['mode']&0o111 and not e['mode']&0o022 and e['uid']==e['gid']==0)
            t=e['target'];require(type(t) is str and t.startswith('/') and 0<len(t)<=4096 and all(32<ord(c)<127 for c in t) and not t.endswith(' (deleted)'))
        _exact(s['own_exe'],s['pid1_exe'])
        stable.append({k:({x:y for x,y in value.items() if x!='raw'} if k.endswith('_status') else value)
            for k,value in s.items() if k!='pid1_stat_raw'})
    _exact(stable[0],stable[1])
    network=validate_network_profile(decode_network_observation(v['raw_network']))
    import json
    _exact(json.loads(network.canonical_security_json)['namespace'],namespace)
    return dict(raw=v,namespace=namespace,profile_sha256=network.profile_sha256,security_sha256=network.security_sha256,
        pid1_start_time=v['before']['pid1_start_time'],exe=v['before']['pid1_exe'])

def _namespace(raw,link):
    require(type(raw) is bytes and re.fullmatch(rb'[1-9][0-9]* [1-9][0-9]*\n',raw) is not None)
    d,i=(int(x) for x in raw.split());require(d<2**64 and i<2**64)
    require(link==('net:['+str(i)+']\n').encode('ascii'))
    return dict(device=d,inode=i)

def _image(raw,pins):
    image=_one(raw)
    require(digest(image)==pins.image_inspect_sha256)
    require(image['Id']==pins.image_id and image['Architecture']==pins.architecture and image['Os']=='linux')
    c=image['Config'];require(c.get('Volumes') is None and c.get('ExposedPorts') is None)
    _exact(c['Env'],list(pins.public_env));_exact(c['Entrypoint'],_ENTRY);_exact(c['Cmd'],['attest_worker'])
    require(c['User']=='10001:10001' and c['WorkingDir']=='/opt/easel')
    require(image['RootFS']['Type']=='layers' and len(image['RootFS']['Layers'])>0)
    return image

def _worker_lifecycle(v,cid,phase,target_cid=None,target=None):
    """Validate raw initialized metadata before projecting stable phase values.

    Shared-network target is only the owner's already pinned original snapshot.
    No returned worker metadata or arbitrary filesystem path is authority.
    """
    require(type(cid) is str and re.fullmatch('[a-f0-9]{64}',cid) is not None)
    require(phase in ('created','running','exited'))
    root='/var/lib/docker/containers/'
    paths={field:root+cid+'/'+leaf for field,leaf in (('ResolvConfPath','resolv.conf'),
        ('HostnamePath','hostname'),('HostsPath','hosts'),('LogPath',cid+'-json.log'))}
    hostname,domain=cid[:12],''
    if target is not None:
        require(type(target_cid) is str and re.fullmatch('[a-f0-9]{64}',target_cid) is not None and target_cid!=cid)
        require(type(target) is dict and target['Id']==target_cid)
        for field,leaf in (('ResolvConfPath','resolv.conf'),('HostnamePath','hostname'),('HostsPath','hosts')):
            expected=root+target_cid+'/'+leaf;_exact(target[field],expected);paths[field]=expected
        hostname,domain=target['Config']['Hostname'],target['Config']['Domainname']
        require(type(hostname) is str and type(domain) is str)
    else:require(target_cid is None)
    for field,expected in paths.items():_exact(v[field],'' if phase=='created' else expected)
    _exact(v['Config']['Hostname'],cid[:12] if phase=='created' else hostname)
    _exact(v['Config']['Domainname'],'' if phase=='created' else domain)
    return dict(paths,Config=dict(v['Config'],Hostname=hostname,Domainname=domain))

def _worker(raw,o,a,phase):
    v=_one(raw);w=a.worker_slot;p=o._policy;l=p.limits
    _closed(v,('Id','Created','Path','Args','State','Image','ResolvConfPath','HostnamePath','HostsPath','LogPath','Name',
        'RestartCount','Driver','Platform','MountLabel','ProcessLabel','AppArmorProfile','ExecIDs','HostConfig','Storage',
        'Mounts','Config','NetworkSettings','ImageManifestDescriptor'))
    require(v['Driver']=='overlayfs' and v['MountLabel']==v['ProcessLabel']==v['AppArmorProfile']=='')
    _exact(v['Storage'],{'RootFS':{'Snapshot':{'Name':'overlayfs'}}})
    desc=v['ImageManifestDescriptor'];_closed(desc,('mediaType','digest','size','platform'))
    require(desc['mediaType']=='application/vnd.oci.image.manifest.v1+json' and desc['digest']==p.worker.image_id)
    require(type(desc['size']) is int and 0<desc['size']<=1024**2)
    _exact(desc['platform'],{'architecture':p.worker.architecture,'os':'linux'})
    require(v['Id']==w.cid and v['Image']==p.worker.image_id and v['Name']=='/'+w.name)
    require(v['Platform']=='linux' and v['RestartCount']==0 and v['ExecIDs'] is None and v['Mounts']==[])
    require(v['Path']==_ENTRY[0]);_exact(v['Args'],_ENTRY[1:]+['attest_worker'])
    h=dict(_HOST_DEFAULTS,NetworkMode='container:'+a.target_slot.cid,Memory=l.memory_bytes,MemorySwap=l.memory_swap_bytes,
        NanoCpus=l.cpu_nanos,PidsLimit=l.pids_limit,Tmpfs={'/run/easel':'rw,noexec,nosuid,nodev,size='+str(l.tmpfs_bytes)},OomKillDisable=None)
    actual=dict(v['HostConfig']);require('OomKillDisable' in actual);require(type(actual['OomKillDisable']) in (bool,type(None)) and actual['OomKillDisable'] in (False,None));actual['OomKillDisable']=None
    _exact(actual,h)
    target=a.target_slot.original_attempt._running
    require(digest(target)==a.target_slot.running_pin)
    lifecycle=_worker_lifecycle(v,w.cid,phase,a.target_slot.cid,target)
    c=dict(Hostname=w.cid[:12] if phase=='created' else target['Config']['Hostname'],
        Domainname='' if phase=='created' else target['Config']['Domainname'],User='10001:10001',AttachStdin=True,AttachStdout=True,AttachStderr=True,
        Tty=False,OpenStdin=True,StdinOnce=True,Env=list(p.worker.public_env),Cmd=['attest_worker'],Image=p.worker.image_id,
        Volumes=None,WorkingDir='/opt/easel',Entrypoint=_ENTRY,Labels=w.labels,StopTimeout=1)
    _exact(v['Config'],c)
    n=v['NetworkSettings'];_exact(n,{'SandboxID':'','SandboxKey':'','Ports':{},'Networks':{}})
    s=v['State'];_closed(s,('Status','Running','Paused','Restarting','OOMKilled','Dead','Pid','ExitCode','Error','StartedAt','FinishedAt'))
    require(type(s['StartedAt']) is str and type(s['FinishedAt']) is str)
    require(s['Error']=='' and s['Paused'] is False and s['Restarting'] is False and s['OOMKilled'] is False and s['Dead'] is False)
    require(type(s['Pid']) is int and type(s['ExitCode']) is int and s['ExitCode']==0)
    if phase=='created':require(s['Status']=='created' and s['Running'] is False and s['Pid']==0)
    elif phase=='running':
        require(s['Status']=='running' and s['Running'] is True and s['Pid']>0)
        identity=(s['Pid'],s['StartedAt']);require(type(identity[1]) is str and identity[1] not in ('','0001-01-01T00:00:00Z'))
        if w.running is None:w.running=identity
        else:require(w.running==identity)
    else:
        require(phase=='exited' and s['Status']=='exited' and s['Running'] is False and s['Pid']==0 and w.running is not None)
        require(s['StartedAt']==w.running[1] and s['FinishedAt'] not in ('','0001-01-01T00:00:00Z'))
    # Validate lifecycle changes first; all other original fields remain exact.
    immutable={k:v[k] for k in v if k not in ('State','ExecIDs','NetworkSettings','HostConfig')};immutable['HostConfig']=actual;immutable.update(lifecycle)
    if w.immutable is None:w.immutable=immutable
    else:_exact(immutable,w.immutable)
    return v


_RECONSTRUCTION_ENTRY=["/opt/easel/bin/python","-I","-B","-m","platform_service.media_backup_b3.reconstruction_worker"]

def _reconstruction_worker(raw,o,a,phase):
    v=_one(raw);w=a.worker_slot;p=o._reconstruction_policy.native;l=p.limits
    _closed(v,('Id','Created','Path','Args','State','Image','ResolvConfPath','HostnamePath','HostsPath','LogPath','Name',
        'RestartCount','Driver','Platform','MountLabel','ProcessLabel','AppArmorProfile','ExecIDs','HostConfig','Storage',
        'Mounts','Config','NetworkSettings','ImageManifestDescriptor'))
    require(v['Driver']=='overlayfs' and v['MountLabel']==v['ProcessLabel']==v['AppArmorProfile']=='')
    _exact(v['Storage'],{'RootFS':{'Snapshot':{'Name':'overlayfs'}}})
    desc=v['ImageManifestDescriptor'];_closed(desc,('mediaType','digest','size','platform'))
    require(desc['mediaType']=='application/vnd.oci.image.manifest.v1+json' and desc['digest']==p.worker.image_id)
    require(type(desc['size']) is int and 0<desc['size']<=1024**2)
    _exact(desc['platform'],{'architecture':p.worker.architecture,'os':'linux'})
    require(v['Id']==w.cid and v['Image']==p.worker.image_id and v['Name']=='/'+w.name)
    require(v['Platform']=='linux' and v['RestartCount']==0 and v['ExecIDs'] is None and v['Mounts']==[])
    require(v['Path']==_RECONSTRUCTION_ENTRY[0]);_exact(v['Args'],_RECONSTRUCTION_ENTRY[1:]+['reconstruct_worker'])
    h=dict(_HOST_DEFAULTS,NetworkMode='container:'+a.target_slot.cid,Memory=l.memory_bytes,MemorySwap=l.memory_swap_bytes,
        NanoCpus=l.cpu_nanos,PidsLimit=l.pids_limit,Tmpfs={'/run/easel':'rw,noexec,nosuid,nodev,size='+str(l.tmpfs_bytes)},OomKillDisable=None)
    actual=dict(v['HostConfig']);require('OomKillDisable' in actual);require(type(actual['OomKillDisable']) in (bool,type(None)) and actual['OomKillDisable'] in (False,None));actual['OomKillDisable']=None
    _exact(actual,h)
    target=a.target_slot.original_attempt._running
    require(digest(target)==a.target_slot.running_pin)
    lifecycle=_worker_lifecycle(v,w.cid,phase,a.target_slot.cid,target)
    c=dict(Hostname=w.cid[:12] if phase=='created' else target['Config']['Hostname'],
        Domainname='' if phase=='created' else target['Config']['Domainname'],User='10001:10001',AttachStdin=True,AttachStdout=True,AttachStderr=True,
        Tty=False,OpenStdin=True,StdinOnce=True,Env=list(p.worker.public_env),Cmd=['reconstruct_worker'],Image=p.worker.image_id,
        Volumes=None,WorkingDir='/opt/easel',Entrypoint=_RECONSTRUCTION_ENTRY,Labels=w.labels,StopTimeout=1)
    _exact(v['Config'],c)
    n=v['NetworkSettings'];_exact(n,{'SandboxID':'','SandboxKey':'','Ports':{},'Networks':{}})
    s=v['State'];_closed(s,('Status','Running','Paused','Restarting','OOMKilled','Dead','Pid','ExitCode','Error','StartedAt','FinishedAt'))
    require(type(s['StartedAt']) is str and type(s['FinishedAt']) is str)
    require(s['Error']=='' and s['Paused'] is False and s['Restarting'] is False and s['OOMKilled'] is False and s['Dead'] is False)
    require(type(s['Pid']) is int and type(s['ExitCode']) is int and s['ExitCode']==0)
    if phase=='created':require(s['Status']=='created' and s['Running'] is False and s['Pid']==0)
    elif phase=='running':
        require(s['Status']=='running' and s['Running'] is True and s['Pid']>0)
        identity=(s['Pid'],s['StartedAt']);require(type(identity[1]) is str and identity[1] not in ('','0001-01-01T00:00:00Z'))
        if w.running is None:w.running=identity
        else:require(w.running==identity)
    else:
        require(phase=='exited' and s['Status']=='exited' and s['Running'] is False and s['Pid']==0 and w.running is not None)
        require(s['StartedAt']==w.running[1] and s['FinishedAt'] not in ('','0001-01-01T00:00:00Z'))
    # Validate lifecycle changes first; all other original fields remain exact.
    immutable={k:v[k] for k in v if k not in ('State','ExecIDs','NetworkSettings','HostConfig')};immutable['HostConfig']=actual;immutable.update(lifecycle)
    if w.immutable is None:w.immutable=immutable
    else:_exact(immutable,w.immutable)
    return v


def _reconstruction_image(raw,pins):
    image=_one(raw)
    require(digest(image)==pins.image_inspect_sha256)
    require(image['Id']==pins.image_id and image['Architecture']==pins.architecture and image['Os']=='linux')
    c=image['Config'];require(c.get('Volumes') is None and c.get('ExposedPorts') is None)
    _exact(c['Env'],list(pins.public_env));_exact(c['Entrypoint'],_RECONSTRUCTION_ENTRY);_exact(c['Cmd'],['reconstruct_worker'])
    require(c['User']=='10001:10001' and c['WorkingDir']=='/opt/easel')
    require(image['RootFS']['Type']=='layers' and len(image['RootFS']['Layers'])>0)
    return image
