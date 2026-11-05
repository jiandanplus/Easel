"""Import-inert exact own-log capacity observer; Root authenticates these bytes.

Receipts are private instantaneous facts, never reservations or heavy-run admission.
Native execution trusts the already authenticated prepared/executor objects; Python
object replacement is outside that loader boundary. Supplied seams are test evidence.
"""
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
import os
import re
import stat
import time

SAFE='invalid AV capacity operation'
class CapacityError(ValueError): pass
def _need(v):
    if not v: raise CapacityError(SAFE)
def _safe(fn):
    def wrapped(*a,**kw):
        try:return fn(*a,**kw)
        except BaseException:pass
        raise CapacityError(SAFE)
    return wrapped
def canonical(x):return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')
def sha(x):return hashlib.sha256(x).hexdigest()
_canonical=canonical
_sha=sha
def _equal(a,b):return canonical(a)==canonical(b)
def _hex(x,n=64):return type(x) is str and re.fullmatch('[0-9a-f]{'+str(n)+'}',x) is not None
_ZERO='0001-01-01T00:00:00Z'
IMAGE="sha256:3f9c7f75b916e8279e24136d7a57769aba9e193715be52570417698c52d6eed0"
DESCRIPTOR={"mediaType":"application/vnd.oci.image.manifest.v1+json","digest":IMAGE,"size":1754}
IMAGE_ENV=("PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin","LANG=C.UTF-8",
    "GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305","PYTHON_VERSION=3.12.13",
    "PYTHON_SHA256=c08bc65a81971c1dd5783182826503369466c7e67374d1646519adf05207b684")
THRESHOLD=2415919104
PROGRAM_PREFIX = """import os,json,signal,stat
def alarm_exit(signum,frame):
 raise SystemExit(124)
signal.signal(signal.SIGALRM,alarm_exit)
signal.pthread_sigmask(signal.SIG_UNBLOCK,{signal.SIGALRM})
signal.alarm(5)
def fs(path):
 s=os.statvfs(path)
 return {k:getattr(s,'f_'+k) for k in ('bsize','frsize','blocks','bfree','bavail','fsid','flag')}
def ident():
 s=os.lstat('/probe-log')
 assert stat.S_ISREG(s.st_mode) and s.st_nlink==1
 return {k:getattr(s,'st_'+k) for k in ('dev','ino','mode','uid','gid','nlink','size','mtime_ns','ctime_ns')}
"""
FOUNDATION_PROGRAM=PROGRAM_PREFIX+"v={'format':'av-capacity-measurement-v1','kind':'foundation','root':fs('/')}\nprint(json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False))\n"
LOG_PROGRAM=PROGRAM_PREFIX+"v={'format':'av-capacity-measurement-v1','kind':'exact-log','root':fs('/')}\nv['before']=ident()\nv['log']=fs('/probe-log')\nv['after']=ident()\nassert v['before']==v['after']\nprint(json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False))\n"
@dataclass(frozen=True)
class Recipe:
    name:str
    create_bytes:bytes
    @property
    def body(self):return json.loads(self.create_bytes)
    @property
    def sha256(self):return sha(self.create_bytes)
def log_path(cid):
    _need(_hex(cid));return '/var/lib/docker/containers/'+cid+'/'+cid+'-json.log'
def _config(c):
    _need(type(c) is dict and set(c)=={'format','kind','nonce','uid','gid','foundation'})
    _need(c['format']=='av-capacity-config-v1' and c['kind'] in ('foundation','exact-log') and _hex(c['nonce'],32))
    _need(type(c['uid']) is int and c['uid']==os.geteuid() and c['uid']>0)
    _need(type(c['gid']) is int and c['gid']==os.getegid() and c['gid']>0)
    if c['kind']=='foundation':_need(c['foundation'] is None)
    else:
        p=c['foundation'];_need(type(p) is dict and set(p)=={'format','kind','nonce','cid','uid','gid','image','recipe_sha256','config_sha256','configuration_sha256','state_sha256','boot','injected_for_tests'})
        _need(p['format']=='av-capacity-foundation-v1' and p['kind']=='foundation' and p['image']==IMAGE and p['injected_for_tests'] is False)
        _need(_hex(p['nonce'],32) and p['nonce']!=c['nonce'] and _hex(p['cid']))
        _need(type(p['uid']) is int and type(p['gid']) is int and (p['uid'],p['gid'])==(c['uid'],c['gid']))
        for k in ('recipe_sha256','config_sha256','configuration_sha256','state_sha256','boot'):_need(_hex(p[k]))
        f=dict(format='av-capacity-config-v1',kind='foundation',nonce=p['nonce'],uid=p['uid'],gid=p['gid'],foundation=None)
        _need(p['config_sha256']==sha(canonical(f)) and p['recipe_sha256']==build_recipe(f).sha256)
@_safe
def build_recipe(config):
    _config(config);c=config;nonce=c['nonce']
    h=dict(Init=False,ReadonlyRootfs=True,Memory=67108864,MemorySwap=67108864,NanoCpus=1000000000,
      PidsLimit=8,ShmSize=1048576,Runtime="runc",NetworkMode="none",IpcMode="private",CgroupnsMode="private",
      PidMode="",UTSMode="",UsernsMode="",CgroupParent="",Privileged=False,CapDrop=["ALL"],CapAdd=[],
      SecurityOpt=["no-new-privileges:true"],GroupAdd=[],OomKillDisable=False,OomScoreAdj=0,
      AutoRemove=False,RestartPolicy={"Name":"no","MaximumRetryCount":0},PublishAllPorts=False,
      PortBindings={},Binds=[],VolumesFrom=[],Links=[],ExtraHosts=[],Devices=[],DeviceRequests=[],
      DeviceCgroupRules=[],Dns=[],DnsSearch=[],DnsOptions=[],StorageOpt={},Tmpfs={},
      Ulimits=[dict(Name=n,Soft=v,Hard=v) for n,v in (("core",0),("cpu",2),("fsize",1048576),("nofile",32))],
      Mounts=[],LogConfig={"Type":"json-file","Config":{"max-size":"1m","max-file":"1","mode":"blocking"}})
    if c['kind']=='exact-log':h['Mounts']=[dict(Type='bind',Source=log_path(c['foundation']['cid']),Target='/probe-log',ReadOnly=True,
        BindOptions=dict(Propagation='rslave',NonRecursive=True,CreateMountpoint=False))]
    b=dict(Image=IMAGE,User=f"{c['uid']}:{c['gid']}",Hostname='av-pack-'+nonce[:12],Domainname='',
        Entrypoint=['/usr/local/bin/python3','-I','-B','-c',FOUNDATION_PROGRAM if c['kind']=='foundation' else LOG_PROGRAM],Cmd=[],WorkingDir='/',StopSignal='SIGTERM',
        AttachStdin=False,OpenStdin=False,StdinOnce=False,Tty=False,AttachStdout=True,AttachStderr=True,
        Env=sorted((*IMAGE_ENV,'HOME=/nonexistent','TMPDIR=/nonexistent','PYTHONDONTWRITEBYTECODE=1','PYTHONUNBUFFERED=1')),
        Labels={'easel.av.nonce':nonce,'easel.av.capacity-kind':c['kind'],'easel.av.capacity-config-sha256':sha(canonical(c))},HostConfig=h)
    return Recipe('run-av-pack-'+nonce,canonical(b))
def _bounded(info):
    _need(type(info) is dict and len(_canonical(info))<=131072)

def _env(rows):
    _need(type(rows) is list and len(rows)<=128);out={}
    for row in rows:
        _need(type(row) is str and "=" in row and len(row)<=4096)
        k,v=row.split("=",1);_need(k and k not in out);out[k]=v
    return out

def _empty(config,key,zero):
    v=config.get(key)
    _need(v is None or (type(v) is type(zero) and v==zero))

def _timestamp(v):
    _need(type(v) is str and len(v)<=40 and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z",v) is not None)
    datetime.fromisoformat(v[:-1]+"+00:00")

def _state(info,phase):
    _need(phase in ("created","observe","terminal"))
    s=info["State"];_need(type(s) is dict)
    _need(type(info["RestartCount"]) is int and info["RestartCount"]==0)
    for k in ("Running","OOMKilled","Paused","Restarting","Dead"):_need(type(s[k]) is bool)
    for k in ("Paused","Restarting","Dead"):_need(s[k] is False)
    _need(s["Error"]=="" and type(s["Pid"]) is int and type(s["ExitCode"]) is int and 0<=s["ExitCode"]<=255)
    for k in ("StartedAt","FinishedAt"):_timestamp(s[k])
    if s["Status"]=="created":
        _need(phase=="created" and not s["Running"] and not s["OOMKilled"] and s["Pid"]==s["ExitCode"]==0
            and s["StartedAt"]==s["FinishedAt"]==_ZERO)
    elif s["Status"]=="running":
        _need(phase=="observe" and s["Running"] and s["Pid"]>0 and s["StartedAt"]!=_ZERO and s["FinishedAt"]==_ZERO)
    elif s["Status"]=="exited":
        _need(phase!="created" and not s["Running"] and s["Pid"]==0 and s["StartedAt"]!=_ZERO and s["FinishedAt"]!=_ZERO)
        if phase=="terminal":_need(s["ExitCode"]==0 and not s["OOMKilled"])
    else:_need(False)
    return {k:s[k] for k in ("Status","Running","OOMKilled","Pid","ExitCode","StartedAt","FinishedAt")}

@_safe
def validate_inspect(info,config,recipe,cid,phase="observe"):
    _bounded(info);_need(type(cid) is str and re.fullmatch("[0-9a-f]{64}",cid))
    expected=build_recipe(config)
    _need(type(recipe) is Recipe and recipe==expected)
    b=expected.body;c=info["Config"];h=info["HostConfig"]
    _need(type(c) is dict and type(h) is dict)
    for k,v in dict(Id=cid,Image=IMAGE,Name="/"+expected.name,Platform="linux",
                    Path=b["Entrypoint"][0],Args=b["Entrypoint"][1:]+b["Cmd"]).items():_need(_equal(info[k],v))
    descriptor=info["ImageManifestDescriptor"]
    _need(_equal(descriptor,DESCRIPTOR) or _equal(descriptor,dict(DESCRIPTOR,
        platform={"architecture":"arm64","os":"linux","variant":"v8"})))
    for k,v in b.items():
        if k=="HostConfig":continue
        if k=="Env":_need(_env(c[k])==_env(v))
        else:_need(_equal(c[k],v))
    for k,z in dict(Volumes={},ExposedPorts={},Healthcheck={},OnBuild=[],Shell=[]).items():_empty(c,k,z)
    for k,v in b["HostConfig"].items():
        actual=h.get(k)
        if k=="OomKillDisable":
            _need(k in h and (actual is False or actual is None));continue
        if type(v) in (list,dict) and not v:
            _empty(h,k,v);continue
        if k=="SecurityOpt":
            _need(actual in (["no-new-privileges"],["no-new-privileges:true"]));continue
        if k=="Mounts":
            _need(type(actual) is list and len(actual)==len(v))
            bytarget={}
            for x in actual:
                _need(type(x) is dict and x.get("Target") not in bytarget);bytarget[x.get("Target")]=x
            for mount in v:
                x=bytarget[mount["Target"]]
                _need(set(x)<=set(mount) and set(mount)-set(x)==set())
                opts=x["BindOptions"];_need(type(opts) is dict and set(opts)<=set(mount["BindOptions"])|{"ReadOnlyNonRecursive","ReadOnlyForceRecursive"})
                for optional in ("ReadOnlyNonRecursive","ReadOnlyForceRecursive"):
                    _need(optional not in opts or opts[optional] is False)
                normalized={k:z for k,z in opts.items() if k not in ("ReadOnlyNonRecursive","ReadOnlyForceRecursive")};normalized.setdefault("CreateMountpoint",False)
                _need(_equal(normalized,mount["BindOptions"]))
                _need(_equal({k:z for k,z in x.items() if k!="BindOptions"},{k:z for k,z in mount.items() if k!="BindOptions"}))
            continue
        _need(k in h and _equal(actual,v))
    # Named ancillary Engine defaults cannot add host paths, controls or resources.
    for k,z in dict(Sysctls={},Annotations={},CpusetCpus="",CpusetMems="",ContainerIDFile="",VolumeDriver="",
                    Isolation="",Cgroup="",BlkioDeviceReadBps=[],BlkioDeviceWriteBps=[],BlkioDeviceReadIOps=[],
                    BlkioDeviceWriteIOps=[],BlkioWeightDevice=[],ConsoleSize=[0,0]).items():
        if k in h and h[k] is not None:_need(_equal(h[k],z))
    for k in ("MemoryReservation","KernelMemory","KernelMemoryTCP","CpuShares","CpuPeriod","CpuQuota",
              "CpuRealtimePeriod","CpuRealtimeRuntime","CpuCount","CpuPercent","BlkioWeight"):
        if k in h:_need(type(h[k]) is int and h[k]==0)
    if "MemorySwappiness" in h:_need(h["MemorySwappiness"] is None)
    for k,minimum in {"MaskedPaths":{"/proc/kcore","/proc/keys"},"ReadonlyPaths":{"/proc/bus","/proc/fs","/proc/irq","/proc/sys","/proc/sysrq-trigger"}}.items():
        rows=h[k];_need(type(rows) is list and len(rows)<=128 and all(type(x) is str and x.startswith("/") and len(x)<=4096 for x in rows) and minimum<=set(rows))
    mounts=info["Mounts"];_need(type(mounts) is list and len(b["HostConfig"]["Mounts"])<=len(mounts)<=len(b["HostConfig"]["Mounts"])+1);seen=set()
    for x in mounts:
        _need(type(x) is dict);target=x["Destination"];_need(type(target) is str and target not in seen);seen.add(target)
        if target=="/probe-log" and config["kind"]=="exact-log":
            _need(x.get("Type")=="bind" and x.get("Source")==log_path(config["foundation"]["cid"]) and x.get("RW") is False and x.get("Propagation")=="rslave")
        else:_need(target=="/dev/shm" and x.get("Type")=="tmpfs" and x.get("Source")=="" and x.get("RW") is True)
    _need((set() if config["kind"]=="foundation" else {"/probe-log"})<=seen)
    net=info["NetworkSettings"];_need(type(net) is dict)
    networks=net.get("Networks");_need(type(networks) is dict and set(networks)<= {"none"})
    _empty(net,"Ports",{})
    for key in ("IPAddress","GlobalIPv6Address","LinkLocalIPv6Address","Gateway","IPv6Gateway"):_empty(net,key,"")
    for key in ("SecondaryIPAddresses","SecondaryIPv6Addresses"):_empty(net,key,[])
    for n in networks.values():
        _need(type(n) is dict)
        for key in ("IPAddress","GlobalIPv6Address","LinkLocalIPv6Address","Gateway","IPv6Gateway"):_empty(n,key,"")
        _empty(n,"IPAMConfig",{})
    state=_state(info,phase)
    _need(not state["OOMKilled"])
    _need(info.get("LogPath")==log_path(cid) or (phase=="created" and info.get("LogPath")==""))
    observed={k:info[k] for k in ("Id","Image","Name","Platform","ImageManifestDescriptor","Path","Args","Config","HostConfig")}
    observed["Mounts"]=sorted(mounts,key=lambda x:x["Destination"])
    raw_hash=_sha(_canonical(observed))
    # cgroup v2 discards this unsupported false setting as null after START.
    # Preserve the raw observation and normalize only this named policy field.
    policy=observed if h["OomKillDisable"] is False else dict(observed,HostConfig=dict(h,OomKillDisable=False))
    return dict(identity=cid,phase=phase,state=state,state_sha256=_sha(_canonical({"State":info["State"],"RestartCount":info["RestartCount"]})),
                configuration_sha256=_sha(_canonical(policy)),configuration_raw_sha256=raw_hash)

def _integer(v,maximum=2**63-1):_need(type(v) is int and 0<=v<=maximum)
def _stats(v):
    _need(type(v) is dict and set(v)=={'bsize','frsize','blocks','bfree','bavail','fsid','flag'})
    for k,x in v.items():_integer(x,2**64-1 if k=='fsid' else 2**63-1)
    _need(0<v['bsize']<=2**30 and 0<v['frsize']<=2**30 and v['bavail']<=v['bfree']<=v['blocks'])
    _need(v['blocks']*v['frsize']<=2**63-1)
    return dict(v,available_bytes=v['bavail']*v['frsize'])
@_safe
def decode_measurement(raw_mux,kind):
    _need(kind in ('foundation','exact-log') and type(raw_mux) is bytes and 0<len(raw_mux)<=4096)
    pos=0;frames=0;parts=[];total=0
    while pos<len(raw_mux):
        _need(len(raw_mux)-pos>=8);h=raw_mux[pos:pos+8];pos+=8;frames+=1
        _need(frames<=16 and h[:4]==b'\x01\0\0\0');n=int.from_bytes(h[4:],'big');total+=n
        _need(n<=len(raw_mux)-pos and total<=2048);parts.append(raw_mux[pos:pos+n]);pos+=n
    raw=b''.join(parts);_need(raw.endswith(b'\n'))
    def pairs(rows):
        v={}
        for k,x in rows:_need(k not in v);v[k]=x
        return v
    def reject(x):raise CapacityError(SAFE)
    v=json.loads(raw[:-1],object_pairs_hook=pairs,parse_constant=reject)
    keys={'format','kind','root'} | (set() if kind=='foundation' else {'log','before','after'})
    _need(type(v) is dict and set(v)==keys and canonical(v)+b'\n'==raw)
    _need(v['format']=='av-capacity-measurement-v1' and v['kind']==kind)
    out=dict(format=v['format'],kind=kind,root=_stats(v['root']))
    if kind=='exact-log':
        for k in ('before','after'):
            a=v[k];_need(type(a) is dict and set(a)=={'dev','ino','mode','uid','gid','nlink','size','mtime_ns','ctime_ns'})
            for x in a.values():_integer(x)
            _need(stat.S_ISREG(a['mode']) and a['nlink']==1 and a['size']<=1048576)
        _need(_equal(v['before'],v['after']));out.update(log=_stats(v['log']),identity=v['before'])
    out['capacity_ok']=all(out[k]['available_bytes']>=THRESHOLD for k in ('root','log') if k in out)
    return out

def _native(prepared,executor):
    executor.native_interpreter()
    import threading
    return dict(clock=time.monotonic,boot=executor.boot_identity,sleep=time.sleep,timer=threading.Timer,
        api=lambda p,r:p.api.APIClient(p.profile['nonce'],r.create_bytes,http_module=p.http),
        guardian=lambda p,c,d:executor.GuardianAPI(p,c,d))

# Each phase denotes the next locally attempted action, never remote completion.
FAILURE_PHASES = frozenset(('prepare','info','image','borrow-check','create','created',
    'start','poll','output','borrow-recheck','final-check','publish'))

def _failure_facts(api, module):
    """Copy exact native diagnostic fields; legacy/fake APIs may have none."""
    fact = getattr(api, 'last_failure', None)
    if fact is None:
        return None
    _need(type(fact) is module.FailureDiagnostic)
    _need(set(vars(fact)) == {'operation','phase','http_status','injected_for_tests'})
    checked = module.FailureDiagnostic(fact.operation, fact.phase, fact.http_status, fact.injected_for_tests)
    return dict(operation=checked.operation,phase=checked.phase,http_status=checked.http_status,
        injected_for_tests=checked.injected_for_tests)

@_safe
def run_case(prepared,executor,config,results_path,*,_deps=None,_allow_injected=False):
    """Run one fixed retained case; callers must authenticate helper bytes first.

    Foundation proof in the receipt is eligible only after Root accepts a native
    receipt. The exact-log input accepts that small proof, never arbitrary paths.
    """
    injected=_deps is not None
    _need(type(_allow_injected) is bool and (not injected or _allow_injected))
    recipe=build_recipe(config);config=json.loads(canonical(config))
    _need(prepared.profile['nonce']==config['nonce'])
    deps=_native(prepared,executor) if _deps is None else dict(_deps)
    _need(set(deps)=={'clock','boot','sleep','timer','api','guardian'} and all(callable(x) for x in deps.values()))
    clock,boot=deps['clock'],deps['boot'];pm=executor.profile_module()
    pm.recheck_stage(prepared,guard=lambda:None)
    records=executor.Records(results_path)
    _need(not records.exists('create.intent') and not records.exists('failure.json'))
    now=clock();boot_pin=boot()
    _need(type(now) in (int,float) and math.isfinite(now) and now>=0 and _hex(boot_pin))
    deadline=now+30.0;api=None;timer=None;timer_owned=False;started=False;cid=None;pins={};identities={};fired=[False]
    def guard():
        n=clock();_need(type(n) in (int,float) and math.isfinite(n) and n<deadline and boot()==boot_pin and not fired[0])
    records.guard=guard
    def fence():
        guard();pm.recheck_stage(prepared,guard=guard);records.check()
        _need(not records.exists('kill.intent') and not records.exists('abort.intent') and not records.exists('failure.json'))
        for name,pin in pins.items():_need(records.read(name,pin)[2]==identities[name])
        guard()
    def put(name,value):
        fence();pin=records.write(name,value);pins[name]=pin;identities[name]=records.read(name,pin)[2];fence()
    def provenance(flag):
        nonlocal injected
        _need(type(flag) is bool);injected=injected or flag;_need(not injected or _allow_injected)
    def admit(result):provenance(result.injected_for_tests);return result.value
    def call(name):
        fence();value=admit(api.call(getattr(prepared.api.Operation,name)));fence();return value
    def stop_timer(failure=False,cleanup_end=None):
        nonlocal timer_owned
        if not timer_owned:return
        timer_owned=False
        failed=False
        try:timer.cancel()
        except BaseException:failed=True
        try:
            end=cleanup_end if failure else deadline
            remaining=0.0 if end is None else max(0.0,end-clock())
            timer.join(timeout=min(1.0,remaining) if failure else remaining)
        except BaseException:failed=True
        try:failed=bool(timer.is_alive()) or failed
        except BaseException:failed=True
        _need(not failed)
    case=dict(format='av-capacity-case-v1',config_sha256=sha(canonical(config)),recipe_sha256=recipe.sha256,
        stage_sha256=prepared.descriptor_sha256,sources_sha256=sha(canonical(prepared.descriptor)),nonce=config['nonce'],kind=config['kind'],boot=boot_pin,deadline=deadline)
    def borrowed():
        proof=config['foundation'];_need(proof['boot']==boot_pin)
        fc=dict(format='av-capacity-config-v1',kind='foundation',nonce=proof['nonce'],uid=proof['uid'],gid=proof['gid'],foundation=None)
        fence();g=deps['guardian'](prepared,proof['cid'],deadline);provenance(g.injected_for_tests)
        raw=admit(g.inspect());fence();facts=validate_inspect(raw,fc,build_recipe(fc),proof['cid'],'terminal')
        _need(facts['configuration_sha256']==proof['configuration_sha256'] and facts['state_sha256']==proof['state_sha256'])
    phase='prepare'
    try:
        put('create.intent',case)
        api=deps['api'](prepared,recipe);provenance(api.injected_for_tests)
        def cancel():
            fired[0]=True
            try:api.cancel()
            except BaseException:pass
        guard();timer=deps['timer'](max(0.0,deadline-clock()),cancel);timer_owned=True;timer.start();guard()
        phase='info';info=call('INFO');pm.validate_daemon(info);_need(info.get('DockerRootDir')=='/var/lib/docker')
        phase='image';pm.validate_image(call('IMAGE'))
        if config['kind']=='exact-log':
            phase='borrow-check';borrowed()
        # CREATE transfers an acknowledged identity before any post-I/O fence.
        # Failed acceptance retains only this bounded identity, never permission.
        phase='create';fence();result=api.call(prepared.api.Operation.CREATE)
        response=result.value;_need(type(response) is dict);acknowledged=response.get('Id');_need(_hex(acknowledged))
        if config['kind']=='exact-log':_need(acknowledged!=config['foundation']['cid'])
        cid=acknowledged;provenance(result.injected_for_tests)
        put('handle.json',dict(case,cid=cid,name=recipe.name))
        _need(set(response)=={'Id','Warnings'} and response['Warnings'] in (None,[]))
        api.bind_created(cid);guard();api.bind_deadline(deadline);guard()
        phase='created'
        created=validate_inspect(call('INSPECT'),config,recipe,cid,'created');put('created.json',created)
        phase='start'
        put('start.intent',dict(case,cid=cid));started=True;call('START')
        phase='poll'
        terminal=None
        for _ in range(31):
            facts=validate_inspect(call('INSPECT'),config,recipe,cid,'observe')
            _need(facts['configuration_sha256']==created['configuration_sha256'])
            if facts['state']['Status']=='exited':
                _need(facts['state']['ExitCode']==0);terminal=facts;break
            guard();deps['sleep'](min(1.0,max(0.0,deadline-clock())));guard()
        _need(terminal is not None);put('terminal.json',terminal)
        phase='output'
        measurement=decode_measurement(call('STDOUT'),config['kind'])
        if config['kind']=='exact-log':
            phase='borrow-recheck';borrowed()
        phase='final-check'
        _need(validate_inspect(call('INSPECT'),config,recipe,cid,'observe')==terminal)
        stop_timer();fence()
        phase='publish'
        proof=None
        if config['kind']=='foundation':
            proof=dict(format='av-capacity-foundation-v1',kind='foundation',nonce=config['nonce'],cid=cid,uid=config['uid'],gid=config['gid'],image=IMAGE,
                recipe_sha256=recipe.sha256,config_sha256=case['config_sha256'],configuration_sha256=terminal['configuration_sha256'],state_sha256=terminal['state_sha256'],boot=boot_pin,injected_for_tests=injected)
        receipt=dict(format='av-capacity-receipt-v1',case=case,cid=cid,injected_for_tests=injected,measurement=measurement,foundation=proof,
            terminal=terminal,sample=clock(),records=dict(pins))
        put('done.json',receipt);fence();raw=canonical(receipt);_need(len(raw)<=16384);fence();return raw
    except BaseException:
        # One cleanup budget includes cancellation, timer join, evidence, claim,
        # and transport. It never changes the original acceptance deadline.
        cleanup_end=None
        try:
            n=clock();_need(type(n) in (int,float) and math.isfinite(n) and n>=0);cleanup_end=n+10.0
        except BaseException:pass
        def cleanup_guard(allowance=0.0):
            n=clock();_need(cleanup_end is not None and type(n) in (int,float) and math.isfinite(n)
                and n<cleanup_end and n+allowance<=cleanup_end)
        if api is not None:
            try:api.cancel()
            except BaseException:pass
        try:stop_timer(True,cleanup_end)
        except BaseException:pass
        # Keep the original Records instance and directory identity. Reopening a
        # replacement path must never confer authority to write failure evidence.
        records.guard=cleanup_guard
        if _hex(cid):
            try:
                cleanup_guard();records.check()
                if not records.exists('handle.json'):
                    records.write('handle.json',dict(case,cid=cid,name=recipe.name))
                cleanup_guard()
            except BaseException:pass
        if started and _hex(cid):
            claimed=False
            try:
                cleanup_guard(5.0)
                records.write('kill.intent',dict(case,cid=cid,reason='capacity-failed',injected_for_tests=injected))
                cleanup_guard(5.0);claimed=True
            except BaseException:pass
            if claimed:
                try:
                    g=deps['guardian'](prepared,cid,deadline);provenance(g.injected_for_tests)
                    cleanup_guard(5.0);admit(g.kill());cleanup_guard()
                    # Protected GuardianAPI allocates a fixed five seconds per
                    # exchange; skip an optional read unless all five fit.
                    try:
                        cleanup_guard(5.0);admit(g.inspect_after_stop());cleanup_guard()
                    except BaseException:pass
                except BaseException:pass
        try:
            cleanup_guard();records.check()
            facts=_failure_facts(api,prepared.api) if api is not None else None
            if facts is not None:provenance(facts['injected_for_tests'])
            _need(type(phase) is str and phase in FAILURE_PHASES)
            cleanup_guard()
            records.write('failure.json',dict(format='av-capacity-failure-v1',case=case,
                cid=cid,phase=phase,start_attempted=started,api_failure=facts,injected_for_tests=injected))
            cleanup_guard()
        except BaseException:pass
        raise
