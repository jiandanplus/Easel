"""Inert, private AV stage preparation and inspect admission.

The trusted bootstrap must authenticate this callable against its approved stage
hash. No execution permission, worker proof, provenance or adoption is returned.
Only already hash-bound public helper source may be compiled here.
"""
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
import re
import stat
import sys
import types
from types import MappingProxyType

IMAGE = "sha256:3f9c7f75b916e8279e24136d7a57769aba9e193715be52570417698c52d6eed0"
DESCRIPTOR = {"mediaType":"application/vnd.oci.image.manifest.v1+json","digest":IMAGE,"size":1754}
_IMAGE_ENV = ("PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG=C.UTF-8","GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305","PYTHON_VERSION=3.12.13",
    "PYTHON_SHA256=c08bc65a81971c1dd5783182826503369466c7e67374d1646519adf05207b684")
_LAYERS = ("709e0d49f62206f760d5392af4f1dab26c0017f4242f3975ffe0b2ce705f42e6",
 "cad8f50db2681fa9333c7d0cb4ebd4e43b69110ded2d0320bdd3978d33dda11f",
 "231860cc1cacd279d6b16ea6ab514a372bbb25577128c45e30287a8b60013b60",
 "0caa967c2360bb62f5ba2997de46d6afc20c13d24fb58588c9ca4c9136bf2b1f")
_CODE = ("pack_worker.py","pack_runtime.py","pack_store.py","git_objects.py")
_HOST = ("pack_profile.py","pack_artifact.py","pack_http.py","pack_api.py")
_SAFE = "invalid AV stage operation"
_ZERO = "0001-01-01T00:00:00Z"
_ABSENT = object()

class StageError(ValueError):
    """A fixed diagnostic with no host paths or dependency text."""

def _need(v):
    if not v: raise StageError(_SAFE)

def _safe(fn):
    def call(*a, **kw):
        try: return fn(*a, **kw)
        except BaseException: pass
        raise StageError(_SAFE) from None
    call.__name__ = fn.__name__
    call.__doc__ = fn.__doc__
    return call

def _canonical(v):
    return json.dumps(v,sort_keys=True,separators=(",",":"),ensure_ascii=True,allow_nan=False).encode("ascii")

def _sha(raw): return hashlib.sha256(raw).hexdigest()

def _hex(v,n=64):
    return type(v) is str and re.fullmatch("[0-9a-f]{%d}"%n,v) is not None and (n!=64 or v != "0"*n)

def _equal(a,b): return _canonical(a)==_canonical(b)

def _json(raw):
    _need(type(raw) is bytes and 0<len(raw)<=16384)
    # Before recursive JSON decoding, bound nesting outside string literals.
    depth=0; quoted=False; escape=False
    for c in raw:
        if quoted:
            if escape: escape=False
            elif c==92: escape=True
            elif c==34: quoted=False
        elif c==34: quoted=True
        elif c in (91,123):
            depth+=1;_need(depth<=16)
        elif c in (93,125): depth-=1;_need(depth>=0)
    _need(depth==0 and not quoted)
    def pairs(rows):
        out={}
        for k,v in rows: _need(k not in out);out[k]=v
        return out
    def constant(_): raise StageError(_SAFE)
    value=json.loads(raw,object_pairs_hook=pairs,parse_constant=constant)
    _need(type(value) is dict and _canonical(value)==raw)
    return value

def _fid(s):
    return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)

def _did(s): return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid)

def _close(fd): os.close(fd)

def _directory(path):
    _need(type(path) is str and path.startswith("/") and all(x not in ("",".","..") for x in path.split("/")[1:]))
    fd=os.open("/",os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for part in path.split("/")[1:]:
            nxt=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            old,fd=fd,nxt
            _close(old)
        return fd
    except BaseException:
        _close(fd);raise

def _read(fd,name,cap,guard,expected,*,size=None,retain=True):
    guard();before=os.stat(name,dir_fd=fd,follow_symlinks=False);guard()
    _need(stat.S_ISREG(before.st_mode) and stat.S_IMODE(before.st_mode)==0o600 and before.st_nlink==1
          and before.st_uid==os.geteuid() and before.st_gid==os.getegid()
          and 0<before.st_size<=cap and (size is None or before.st_size==size))
    owned=None
    try:
        guard();owned=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd);guard()
        _need(_fid(os.fstat(owned))==_fid(before))
        h=hashlib.sha256();chunks=[];total=0
        while True:
            guard();chunk=os.read(owned,min(65536,cap-total+1));guard()
            if not chunk:break
            total+=len(chunk);_need(total<=cap);h.update(chunk)
            if retain:chunks.append(chunk)
        guard();_need(total==before.st_size and _fid(os.fstat(owned))==_fid(before)
             and _fid(os.stat(name,dir_fd=fd,follow_symlinks=False))==_fid(before));guard()
        _need(h.hexdigest()==expected)
        return (b"".join(chunks) if retain else None),_fid(before)
    finally:
        if owned is not None:_close(owned)

def _scan(stage,expected,guard):
    _need(_hex(expected) and callable(guard));guard()
    _need(type(stage) is str and re.fullmatch("run-av-pack-[0-9a-f]{32}",stage.rsplit("/",1)[-1]) is not None)
    fds={};dirs={};files={};raw={}
    try:
        for key in ("","code","host","inputs","results"):
            guard();fd=_directory(stage+("/"+key if key else ""));fds[key]=fd
            s=os.fstat(fd);guard()
            _need(stat.S_IMODE(s.st_mode)==0o700 and s.st_uid==os.geteuid() and s.st_gid==os.getegid())
            dirs[key]=_did(s)
        guard();_need(set(os.listdir(fds[""]))=={"stage.json","code","host","inputs","results"});guard()
        raw["stage.json"],files["stage.json"]=_read(fds[""],"stage.json",16384,guard,expected)
        d=_json(raw["stage.json"])
        _need(set(d)=={"format","nonce","profile_sha256","code_sha256","host_sha256"}
            and d["format"]=="av-pack-host-stage-v1" and _hex(d["nonce"],32)
            and stage.rsplit("/",1)[-1]=="run-av-pack-"+d["nonce"] and _hex(d["profile_sha256"]))
        for key,names in (("code",_CODE),("host",_HOST)):
            pins=d[key+"_sha256"]
            _need(type(pins) is dict and set(pins)==set(names) and all(_hex(v) for v in pins.values()))
            guard();_need(set(os.listdir(fds[key]))==set(names));guard()
            for n in names:
                raw[key+"/"+n],files[key+"/"+n]=_read(fds[key],n,262144,guard,pins[n])
        raw["inputs/profile.json"],files["inputs/profile.json"]=_read(fds["inputs"],"profile.json",16384,guard,d["profile_sha256"])
        p=_json(raw["inputs/profile.json"])
        _need(type(p.get("mode")) is str and p["mode"] in ("decode","preflight","control-as","control-cpu","control-wall"))
        inputs={"profile.json"}
        if p["mode"]=="decode":
            q=p.get("pack");_need(type(q) is dict and q.get("file")=="source.pack"
                and type(q.get("bytes")) is int and 32<=q["bytes"]<=67108864 and _hex(q.get("sha256")))
            _,files["inputs/source.pack"]=_read(fds["inputs"],"source.pack",67108864,guard,q["sha256"],size=q["bytes"],retain=False)
            inputs.add("source.pack")
        else:_need(p.get("pack") is None)
        listings={"":{"stage.json","code","host","inputs","results"},"code":set(_CODE),"host":set(_HOST),"inputs":inputs}
        for key in dirs:
            guard()
            if key in listings:_need(set(os.listdir(fds[key]))==listings[key])
            _need(_did(os.fstat(fds[key]))==dirs[key])
            current=None
            try:
                guard();current=_directory(stage+("/"+key if key else ""));guard()
                _need(_did(os.fstat(current))==dirs[key]);guard()
                for path,identity in files.items():
                    parent,_,name=path.rpartition("/")
                    if parent==key:
                        guard();_need(_fid(os.stat(name,dir_fd=current,follow_symlinks=False))==identity);guard()
            finally:
                if current is not None:_close(current)
        guard()
        return raw,tuple(sorted(dirs.items())),tuple(sorted(files.items()))
    finally:
        failed=False
        for fd in fds.values():
            try:_close(fd)
            except BaseException:failed=True
        _need(not failed)

def _load(name,raw):
    key="_av_host_"+name[:-3]+"_"+_sha(raw)
    previous=sys.modules.get(key,_ABSENT)
    mod=types.ModuleType(key);sys.modules[key]=mod
    try:exec(compile(raw,"<verified-public-"+name+">","exec",dont_inherit=True),mod.__dict__)
    except BaseException:
        if previous is _ABSENT:sys.modules.pop(key,None)
        else:sys.modules[key]=previous
        raise
    return mod

@dataclass(frozen=True)
class PreparedStage:
    stage: str
    descriptor_sha256: str
    descriptor_bytes: bytes
    profile_bytes: bytes
    _raw: tuple
    _directories: tuple
    _files: tuple
    worker: object
    runtime: object
    artifact: object
    http: object
    api: object
    @property
    def profile(self):return json.loads(self.profile_bytes)
    @property
    def descriptor(self):return json.loads(self.descriptor_bytes)
    @property
    def source_bytes(self):return MappingProxyType({k.rsplit("/",1)[-1]:v for k,v in self._raw if k.startswith(("code/","host/"))})
    @property
    def validate_report(self):return self.worker.validate_report

@_safe
def prepare_stage(stage,*,expected_descriptor_sha256):
    """Authenticate bounded files; then load only pinned public helpers."""
    stage=os.fspath(stage)
    raw,dirs,files=_scan(stage,expected_descriptor_sha256,lambda:None)
    # These are the only registrations made by the fixed verified helper paths.
    # Include the worker-owned runtime loader key in the same transaction.
    keys=["_av_host_"+n[:-3]+"_"+_sha(raw[d+"/"+n]) for d,n in
          (("code","pack_worker.py"),("host","pack_artifact.py"),
           ("host","pack_http.py"),("host","pack_api.py"))]
    keys.append("_av_verified_pack_runtime_"+_sha(raw["code/pack_runtime.py"]))
    registered={k:sys.modules.get(k,_ABSENT) for k in keys}
    try:
        w=_load("pack_worker.py",raw["code/pack_worker.py"])
        profile=w.parse_profile(raw["inputs/profile.json"])
        d=_json(raw["stage.json"])
        _need(profile["code_sha256"]==d["code_sha256"] and profile["nonce"]==d["nonce"]
            and profile["uid"]==os.geteuid() and profile["gid"]==os.getegid())
        reread=w.prepare_runtime(profile,code_dir=stage+"/code")
        _need(reread=={n:raw["code/"+n] for n in _CODE})
        a=_load("pack_artifact.py",raw["host/pack_artifact.py"])
        h=_load("pack_http.py",raw["host/pack_http.py"])
        api=_load("pack_api.py",raw["host/pack_api.py"])
        _need(_scan(stage,expected_descriptor_sha256,lambda:None)==(raw,dirs,files))
        return PreparedStage(stage,expected_descriptor_sha256,raw["stage.json"],raw["inputs/profile.json"],
                             tuple(sorted(raw.items())),dirs,files,w,w._runtime_module(profile),a,h,api)
    except BaseException:
        # Restore each touched binding, including worker-owned runtime preparation;
        # remove only names absent before this attempt, leaving others untouched.
        for key,previous in registered.items():
            if previous is _ABSENT:sys.modules.pop(key,None)
            else:sys.modules[key]=previous
        raise

@_safe
def recheck_stage(prepared,*,guard):
    _need(type(prepared) is PreparedStage and callable(guard))
    raw,dirs,files=_scan(prepared.stage,prepared.descriptor_sha256,guard)
    _need(tuple(sorted(raw.items()))==prepared._raw and dirs==prepared._directories and files==prepared._files)
    guard()
    return {"descriptor_sha256":prepared.descriptor_sha256,"profile_sha256":_sha(prepared.profile_bytes),
            "identities_sha256":_sha(_canonical([dirs,files]))}

@dataclass(frozen=True)
class Recipe:
    name: str
    create_bytes: bytes
    @property
    def body(self):return json.loads(self.create_bytes)
    @property
    def sha256(self):return _sha(self.create_bytes)

@_safe
def make_recipe(prepared):
    _need(type(prepared) is PreparedStage)
    p=_json(prepared.profile_bytes);uid=p["uid"];gid=p["gid"];nonce=p["nonce"]
    h=dict(Init=False,ReadonlyRootfs=True,Memory=536870912,MemorySwap=536870912,NanoCpus=2000000000,
      PidsLimit=16,ShmSize=1048576,Runtime="runc",NetworkMode="none",IpcMode="private",CgroupnsMode="private",
      PidMode="",UTSMode="",UsernsMode="",CgroupParent="",Privileged=False,CapDrop=["ALL"],CapAdd=[],
      SecurityOpt=["no-new-privileges:true"],GroupAdd=[],OomKillDisable=False,OomScoreAdj=0,
      AutoRemove=False,RestartPolicy={"Name":"no","MaximumRetryCount":0},PublishAllPorts=False,
      PortBindings={},Binds=[],VolumesFrom=[],Links=[],ExtraHosts=[],Devices=[],DeviceRequests=[],
      DeviceCgroupRules=[],Dns=[],DnsSearch=[],DnsOptions=[],StorageOpt={},
      Ulimits=[dict(Name=n,Soft=v,Hard=v) for n,v in (("core",0),("cpu",30),("fsize",16777216),("nofile",64))],
      Mounts=[dict(Type="bind",Source=prepared.stage+"/"+n,Target="/"+n,ReadOnly=True,
        BindOptions=dict(Propagation="rprivate",NonRecursive=True,CreateMountpoint=False)) for n in ("code","inputs")],
      Tmpfs={"/work":f"rw,noexec,nosuid,nodev,size=33554432,mode=700,uid={uid},gid={gid}"},
      LogConfig={"Type":"json-file","Config":{"max-size":"64m","max-file":"1","mode":"blocking"}})
    b=dict(Image=IMAGE,User=f"{uid}:{gid}",Hostname="av-pack-"+nonce[:12],Domainname="",
      Entrypoint=["/usr/local/bin/python3","-I","-B","/code/pack_worker.py"],
      Cmd=["/inputs/profile.json",_sha(prepared.profile_bytes)],WorkingDir="/work",StopSignal="SIGTERM",
      AttachStdin=False,OpenStdin=False,StdinOnce=False,Tty=False,AttachStdout=True,AttachStderr=True,
      Env=sorted((*_IMAGE_ENV,"HOME=/work","TMPDIR=/work","PYTHONDONTWRITEBYTECODE=1","PYTHONUNBUFFERED=1")),
      Labels={"easel.av.nonce":nonce,"easel.av.profile-sha256":_sha(prepared.profile_bytes),
              "easel.av.stage-sha256":prepared.descriptor_sha256},HostConfig=h)
    raw=_canonical(b);_need(len(raw)<=131072)
    return Recipe("run-av-pack-"+nonce,raw)

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

@_safe
def validate_daemon(info):
    _bounded(info)
    _need(info.get("OSType")=="linux" and info.get("Architecture") in ("arm64","aarch64")
        and info.get("DefaultRuntime")=="runc" and info.get("CgroupVersion")=="2")
    return {"configuration_sha256":_sha(_canonical({k:info[k] for k in ("OSType","Architecture","DefaultRuntime","CgroupVersion")}))}

@_safe
def validate_image(info):
    _bounded(info)
    for k,v in dict(Id=IMAGE,Os="linux",Architecture="arm64",Variant="v8",Descriptor=DESCRIPTOR,
        RootFS={"Type":"layers","Layers":["sha256:"+v for v in _LAYERS]}).items():_need(_equal(info[k],v))
    c=info["Config"];_need(type(c) is dict and _equal(c.get("Cmd"),["python3"]) and _env(c.get("Env"))==_env(list(_IMAGE_ENV)))
    for k,z in dict(User="",Entrypoint=[],Volumes={},ExposedPorts={},Labels={},Healthcheck={},OnBuild=[],Shell=[],WorkingDir="",StopSignal="").items():_empty(c,k,z)
    return {"image":IMAGE,"configuration_sha256":_sha(_canonical(info))}

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
def validate_container(info,cid,prepared,recipe,*,phase="observe"):
    _bounded(info);_need(type(cid) is str and re.fullmatch("[0-9a-f]{64}",cid))
    expected=make_recipe(prepared)
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
            _need(type(actual) is list and len(actual)==2)
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
    mounts=info["Mounts"];_need(type(mounts) is list and 2<=len(mounts)<=4);seen=set()
    for x in mounts:
        _need(type(x) is dict);target=x["Destination"];_need(type(target) is str and target not in seen);seen.add(target)
        if target in ("/code","/inputs"):
            _need(x.get("Type")=="bind" and x.get("Source")==prepared.stage+target and x.get("RW") is False and x.get("Propagation")=="rprivate")
        else:_need(target in ("/work","/dev/shm") and x.get("Type")=="tmpfs" and x.get("Source")=="" and x.get("RW") is True)
    _need({"/code","/inputs"}<=seen)
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
    config={k:info[k] for k in ("Id","Image","Name","Platform","ImageManifestDescriptor","Path","Args","Config","HostConfig")}
    config["Mounts"]=sorted(mounts,key=lambda x:x["Destination"])
    raw_hash=_sha(_canonical(config))
    # Retain the complete observation; normalize only this unsupported flag.
    policy=config if h["OomKillDisable"] is False else dict(config,HostConfig=dict(h,OomKillDisable=False))
    return dict(identity=cid,phase=phase,state=state,state_sha256=_sha(_canonical({"State":info["State"],"RestartCount":info["RestartCount"]})),
                configuration_sha256=_sha(_canonical(policy)),configuration_raw_sha256=raw_hash)
