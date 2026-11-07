"""Synthetic private stages and fake inspect only; never Engine/runtime evidence."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import pytest

SOURCE = Path(__file__).resolve().parents[1] / "scripts/av_sources"
EVIDENCE = Path(__file__).resolve().parents[3] / "outputs/_run/av-pack-controller-20261102/ROOT-IMAGE-READINESS.safe.json"
CODE = ("pack_worker.py", "pack_runtime.py", "pack_store.py", "git_objects.py")
HOST = ("pack_profile.py", "pack_artifact.py", "pack_http.py", "pack_api.py")
NONCE = "1" * 32
CID = "2" * 64

def canon(v):
    return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()

def sha(v):
    return hashlib.sha256(v).hexdigest()

@pytest.fixture
def m():
    path = SOURCE / "pack_profile.py"
    if not path.exists(): pytest.skip("Task2B3A module missing")
    spec = importlib.util.spec_from_file_location("_test_av_profile", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod

def put(p, raw):
    p.write_bytes(raw)
    p.chmod(0o600)

def stage(tmp_path, decode=False):
    root = tmp_path.resolve() / ("run-av-pack-" + NONCE)
    root.mkdir(mode=0o700)
    for d in ("code", "host", "inputs", "results"):
        (root/d).mkdir(mode=0o700)
    pins = {}
    for d, names in (("code", CODE), ("host", HOST)):
        pins[d] = {}
        for n in names:
            raw = (SOURCE/n).read_bytes()
            put(root/d/n, raw)
            pins[d][n] = sha(raw)
    profile = dict(format="av-pack-worker-profile-v1", nonce=NONCE,
                   image="sha256:3f9c7f75b916e8279e24136d7a57769aba9e193715be52570417698c52d6eed0",
                   platform="linux/arm64/v8", uid=os.geteuid(), gid=os.getegid(),
                   mode="decode" if decode else "preflight", code_sha256=pins["code"], pack=None)
    if decode:
        raw = b"tiny synthetic pack bytes only...."
        profile["pack"] = dict(file="source.pack", bytes=len(raw), sha256=sha(raw), commit_oid="3"*40,
                               expected_tree_oid=None, required_gitlink_oid=None, required_entry=None)
        put(root/"inputs/source.pack", raw)
    put(root/"inputs/profile.json", canon(profile))
    desc = dict(format="av-pack-host-stage-v1", nonce=NONCE, profile_sha256=sha(canon(profile)),
                code_sha256=pins["code"], host_sha256=pins["host"])
    put(root/"stage.json", canon(desc))
    return root, sha(canon(desc))

def repin(root):
    d=json.loads((root/"stage.json").read_bytes())
    d["profile_sha256"]=sha((root/"inputs/profile.json").read_bytes())
    put(root/"stage.json",canon(d))
    return sha(canon(d))

def fake(m, p, r):
    b=r.body
    c={k:copy.deepcopy(v) for k,v in b.items() if k != "HostConfig"}
    h=b["HostConfig"]
    h["MaskedPaths"]=["/proc/kcore","/proc/keys"]
    h["ReadonlyPaths"]=["/proc/bus","/proc/fs","/proc/irq","/proc/sys","/proc/sysrq-trigger"]
    return dict(Id=CID,Image=m.IMAGE,Name="/"+r.name,Platform="linux",Config=c,HostConfig=h,
        Path=c["Entrypoint"][0],Args=c["Entrypoint"][1:]+c["Cmd"],
        ImageManifestDescriptor=copy.deepcopy(m.DESCRIPTOR),
        Mounts=[dict(Type="bind",Source=x["Source"],Destination=x["Target"],RW=False,Propagation="rprivate") for x in h["Mounts"]],
        NetworkSettings=dict(Networks={},Ports={},IPAddress="",GlobalIPv6Address="",LinkLocalIPv6Address=""),
        RestartCount=0,State=dict(Status="created",Running=False,OOMKilled=False,Paused=False,Restarting=False,
          Dead=False,Pid=0,ExitCode=0,Error="",StartedAt="0001-01-01T00:00:00Z",FinishedAt="0001-01-01T00:00:00Z"))

def test_prepare_runtime_recipe_recheck(m,tmp_path,monkeypatch):
    root,d=stage(tmp_path,True)
    import socket, threading, signal
    def forbidden(*a,**kw): raise AssertionError("native path reached")
    monkeypatch.setattr(socket,"socket",forbidden)
    monkeypatch.setattr(threading,"Timer",forbidden)
    monkeypatch.setattr(signal,"setitimer",forbidden)
    p=m.prepare_stage(str(root),expected_descriptor_sha256=d)
    assert p.worker.validate_report is p.validate_report
    assert p.runtime is p.worker._runtime_module(p.profile)
    p.worker.validate_runtime(p.worker.synthetic_runtime(p.profile),p.profile)
    assert set(p.source_bytes)==set(CODE+HOST)
    profile=p.profile; profile["nonce"]="f"*32
    assert p.profile["nonce"]==NONCE
    r=m.make_recipe(p)
    b=r.body; b["HostConfig"]["Memory"]=1
    assert r.body["HostConfig"]["Memory"]==536870912
    assert r.body["HostConfig"]["MemorySwap"]==536870912
    assert [u["Name"] for u in r.body["HostConfig"]["Ulimits"]]==["core","cpu","fsize","nofile"]
    assert r.create_bytes==canon(r.body)
    monkeypatch.setattr(m,"_load",forbidden)
    calls=[]
    facts=m.recheck_stage(p,guard=lambda:calls.append(1))
    assert len(calls)>20 and facts["descriptor_sha256"]==d
    put(root/"results/later",b"legitimate result")
    m.recheck_stage(p,guard=lambda:None)
    with pytest.raises(m.StageError,match="^invalid AV stage operation$"):
        m.recheck_stage(p,guard=forbidden)

@pytest.mark.parametrize("fault",["codehash","hosthash","profilehash","descriptorhash","mode","hardlink","symlink","extra","dirmode","noncanonical","duplicate","nonfinite","packhash","packsize","traversal","owner","fifo"])
def test_precompile_rejection(m,tmp_path,monkeypatch,fault):
    root,d=stage(tmp_path,True)
    if fault=="codehash": put(root/"code/pack_worker.py",b"raise RuntimeError('secret')")
    elif fault=="hosthash": put(root/"host/pack_api.py",b"bad")
    elif fault=="profilehash": put(root/"inputs/profile.json",b"{}")
    elif fault=="descriptorhash": d="f"*64
    elif fault=="mode": (root/"code/pack_worker.py").chmod(0o644)
    elif fault=="hardlink": os.link(root/"code/pack_worker.py",root/"results/link")
    elif fault=="symlink":
        (root/"host/pack_api.py").unlink(); (root/"host/pack_api.py").symlink_to(root/"code/pack_worker.py")
    elif fault=="extra": put(root/"inputs/extra",b"x")
    elif fault=="dirmode": (root/"host").chmod(0o755)
    elif fault in ("noncanonical","duplicate","nonfinite"):
        raw={"noncanonical":b"{ }","duplicate":b'{"x":1,"x":2}',"nonfinite":b'{"x":NaN}'}[fault]
        put(root/"inputs/profile.json",raw);d=repin(root)
    elif fault=="packhash": put(root/"inputs/source.pack",b"x"*33)
    elif fault=="packsize":
        v=json.loads((root/"inputs/profile.json").read_bytes());v["pack"]["bytes"]=True
        put(root/"inputs/profile.json",canon(v));d=repin(root)
    elif fault=="traversal": root=root/".."/root.name
    elif fault=="owner": monkeypatch.setattr(m.os,"getegid",lambda:os.getgid()+1)
    elif fault=="fifo":
        (root/"inputs/source.pack").unlink();os.mkfifo(root/"inputs/source.pack",0o600)
    calls=[]
    def forbidden(*a,**kw):
        calls.append((a,kw))
        raise RuntimeError("helper load tripwire")
    monkeypatch.setattr(m,"_load",forbidden)
    with pytest.raises(m.StageError,match="^invalid AV stage operation$"):
        m.prepare_stage(str(root),expected_descriptor_sha256=d)
    assert calls==[], "helper load reached before all input/source pins"

def test_authoritative_worker_schema_and_reload_binding(m,tmp_path,monkeypatch):
    root,d=stage(tmp_path)
    v=json.loads((root/"inputs/profile.json").read_bytes());v["unexpected"]=1
    put(root/"inputs/profile.json",canon(v));d=repin(root)
    loaded=[];original=m._load
    def load(*a): loaded.append(a[0]);return original(*a)
    monkeypatch.setattr(m,"_load",load)
    with pytest.raises(m.StageError):m.prepare_stage(str(root),expected_descriptor_sha256=d)
    assert loaded==["pack_worker.py"]

def test_recheck_drift_and_safe_errors(m,tmp_path,monkeypatch):
    root,d=stage(tmp_path);p=m.prepare_stage(str(root),expected_descriptor_sha256=d)
    put(root/"code/pack_worker.py",(root/"code/pack_worker.py").read_bytes())
    with pytest.raises(m.StageError): m.recheck_stage(p,guard=lambda:None)
    with pytest.raises(m.StageError): m.recheck_stage(p,guard=None)

def test_image_daemon_and_empty_policies(m):
    current=json.loads(EVIDENCE.read_bytes())
    m.validate_daemon(current["daemon"]);m.validate_image(current["current"])
    for key,values in {"Architecture":["amd64",None],"DefaultRuntime":["other",None],"CgroupVersion":[1,2],"OSType":["darwin"]}.items():
        for value in values:
            v=copy.deepcopy(current["daemon"]);v[key]=value
            with pytest.raises(m.StageError):m.validate_daemon(v)
    for key in ("User","Entrypoint","Volumes","ExposedPorts","Labels","Healthcheck","OnBuild","Shell","WorkingDir","StopSignal"):
        v=copy.deepcopy(current["current"]);v["Config"][key]=False
        with pytest.raises(m.StageError):m.validate_image(v)
    for key in ("Id","Os","Architecture","Variant","Descriptor","RootFS"):
        v=copy.deepcopy(current["current"]);v[key]=None
        with pytest.raises(m.StageError):m.validate_image(v)

@pytest.mark.parametrize("section",["Config","HostConfig"])
def test_every_recipe_critical_field(m,tmp_path,section):
    root,d=stage(tmp_path);p=m.prepare_stage(str(root),expected_descriptor_sha256=d);r=m.make_recipe(p)
    f=fake(m,p,r)
    result=m.validate_container(f,CID,p,r,phase="created")
    assert result["phase"]=="created"
    for key in r.body if section=="Config" else r.body["HostConfig"]:
        if key=="HostConfig":continue
        v=copy.deepcopy(f);v[section][key]="unsafe"
        with pytest.raises(m.StageError):m.validate_container(v,CID,p,r,phase="created")
    with pytest.raises(m.StageError):m.validate_container(f,CID,p,object(),phase="created")

@pytest.mark.parametrize("key",["Id","Image","Name","Platform","Path","Args","ImageManifestDescriptor","Mounts","NetworkSettings","RestartCount","State"])
def test_top_identity_fields(m,tmp_path,key):
    root,d=stage(tmp_path);p=m.prepare_stage(str(root),expected_descriptor_sha256=d);r=m.make_recipe(p);f=fake(m,p,r)
    f[key]=None
    with pytest.raises(m.StageError):m.validate_container(f,CID,p,r,phase="created")

def test_states_hashes_and_explicit_normalizations(m,tmp_path):
    root,d=stage(tmp_path);p=m.prepare_stage(str(root),expected_descriptor_sha256=d);r=m.make_recipe(p);f=fake(m,p,r)
    a=m.validate_container(f,CID,p,r,phase="created")
    f["State"].update(Status="running",Running=True,Pid=99,StartedAt="2026-11-03T00:00:00Z")
    b=m.validate_container(f,CID,p,r)
    assert a["state_sha256"]!=b["state_sha256"] and a["configuration_sha256"]==b["configuration_sha256"]
    f["State"].update(Status="exited",Running=False,Pid=0,FinishedAt="2026-11-03T00:00:01.123Z")
    m.validate_container(f,CID,p,r,phase="terminal")
    for key in f["State"]:
        bad=copy.deepcopy(f);bad["State"][key]=None
        with pytest.raises(m.StageError):m.validate_container(bad,CID,p,r)
    for sec,key,val in [("HostConfig","Memory",True),("HostConfig","Init",0),("Config","Tty",0),("HostConfig","OomScoreAdj",False)]:
        bad=copy.deepcopy(f);bad[sec][key]=val
        with pytest.raises(m.StageError):m.validate_container(bad,CID,p,r)
    bad=copy.deepcopy(f);bad["Config"]["Env"]*=2
    with pytest.raises(m.StageError):m.validate_container(bad,CID,p,r)
    bad=copy.deepcopy(f);bad["Mounts"]*=2
    with pytest.raises(m.StageError):m.validate_container(bad,CID,p,r)
    f["HostConfig"]["SecurityOpt"]=["no-new-privileges"]
    f["HostConfig"]["Binds"]=None
    m.validate_container(f,CID,p,r)
    before=m.validate_container(f,CID,p,r)["configuration_sha256"]
    f["HostConfig"]["MaskedPaths"].append("/proc/extra")
    assert before!=m.validate_container(f,CID,p,r)["configuration_sha256"]


def test_module_exists():
    assert (SOURCE / "pack_profile.py").exists(), "Task2B3A preparation implementation missing"


def test_created_is_not_observe(m,tmp_path):
    root,d=stage(tmp_path);p=m.prepare_stage(str(root),expected_descriptor_sha256=d);r=m.make_recipe(p)
    with pytest.raises(m.StageError):m.validate_container(fake(m,p,r),CID,p,r)

@pytest.mark.parametrize("key,value",[("Sysctls",{"kernel.domainname":"unsafe"}),("MemoryReservation",1),
    ("CpuShares",False),("CpuQuota",1),("CpusetCpus","0"),("ContainerIDFile","/tmp/unsafe"),
    ("Annotations",{"unsafe":"override"}),("BlkioWeight",1)])
def test_unrequested_host_overrides_rejected(m,tmp_path,key,value):
    root,d=stage(tmp_path);p=m.prepare_stage(str(root),expected_descriptor_sha256=d);r=m.make_recipe(p);f=fake(m,p,r)
    f["HostConfig"][key]=value
    with pytest.raises(m.StageError):m.validate_container(f,CID,p,r,phase="created")

def test_explicit_bind_optional_false_fields(m,tmp_path):
    root,d=stage(tmp_path);p=m.prepare_stage(str(root),expected_descriptor_sha256=d);r=m.make_recipe(p);f=fake(m,p,r)
    for x in f["HostConfig"]["Mounts"]:
        x["BindOptions"].pop("CreateMountpoint")
        x["BindOptions"].update(ReadOnlyNonRecursive=False,ReadOnlyForceRecursive=False)
    m.validate_container(f,CID,p,r,phase="created")
    f["HostConfig"]["Mounts"][0]["BindOptions"]["ReadOnlyNonRecursive"]=0
    with pytest.raises(m.StageError):m.validate_container(f,CID,p,r,phase="created")

@pytest.mark.parametrize("field",["SecondaryIPAddresses","SecondaryIPv6Addresses","Gateway","IPv6Gateway"])
def test_no_secondary_network_address(m,tmp_path,field):
    root,d=stage(tmp_path);p=m.prepare_stage(str(root),expected_descriptor_sha256=d);r=m.make_recipe(p);f=fake(m,p,r)
    f["NetworkSettings"][field]=["unsafe"]
    with pytest.raises(m.StageError):m.validate_container(f,CID,p,r,phase="created")

def test_runtime_reread_binding_and_safe_module_failure(m,tmp_path,monkeypatch):
    root,d=stage(tmp_path);original=m._load
    def load(name,raw):
        mod=original(name,raw)
        if name=="pack_worker.py":
            def reread(*a,**kw):return {"wrong":"retained bytes mismatch"}
            mod.prepare_runtime=reread
        return mod
    monkeypatch.setattr(m,"_load",load)
    with pytest.raises(m.StageError,match="^invalid AV stage operation$"):
        m.prepare_stage(str(root),expected_descriptor_sha256=d)
    def broken(*a):raise RuntimeError("SECRET path /not/exported")
    monkeypatch.setattr(m,"_load",broken)
    with pytest.raises(m.StageError) as e:m.prepare_stage(str(root),expected_descriptor_sha256=d)
    assert str(e.value)=="invalid AV stage operation" and e.value.__suppress_context__

def test_directory_file_replacement_and_guard_eof(m,tmp_path,monkeypatch):
    root,d=stage(tmp_path);p=m.prepare_stage(str(root),expected_descriptor_sha256=d)
    original=m.os.read;reads=[]
    def read(fd,n):
        assert n<=65536
        data=original(fd,n);reads.append(data)
        return data
    monkeypatch.setattr(m.os,"read",read)
    calls=[]
    def guard():
        calls.append(len(reads))
        if reads and reads[-1]==b"":raise TimeoutError("secret deadline")
    with pytest.raises(m.StageError):m.recheck_stage(p,guard=guard)
    assert reads[-1]==b"" and len(calls)>1
    m.recheck_stage(p,guard=lambda:None)
    (root/"results").rename(tmp_path/"oldresults");(root/"results").mkdir(mode=0o700)
    assert set(x.name for x in root.iterdir())=={"stage.json","code","host","inputs","results"}
    assert (root/"results").stat().st_mode & 0o777 == 0o700
    with pytest.raises(m.StageError):m.recheck_stage(p,guard=lambda:None)


def test_zero_nonce_is_valid_lower_hex(m,tmp_path,monkeypatch):
    monkeypatch.setattr(sys.modules[__name__],"NONCE","0"*32)
    root,d=stage(tmp_path)
    p=m.prepare_stage(str(root),expected_descriptor_sha256=d)
    assert p.profile["nonce"]=="0"*32

@pytest.mark.parametrize("fault",["codecap","profilecap","packempty","inputlink","directorylink","descriptor_extra","profile_nonce","profile_codepins","wrong_input_name"])
def test_remaining_stage_bounds(m,tmp_path,monkeypatch,fault):
    root,d=stage(tmp_path,True)
    if fault=="codecap":put(root/"code/pack_worker.py",b"x"*262145)
    elif fault=="profilecap":put(root/"inputs/profile.json",b"x"*16385)
    elif fault=="packempty":put(root/"inputs/source.pack",b"")
    elif fault=="inputlink":os.link(root/"inputs/source.pack",root/"results/link")
    elif fault=="directorylink":
        (root/"results").rmdir();(root/"results").symlink_to(root/"code",target_is_directory=True)
    elif fault=="descriptor_extra":
        v=json.loads((root/"stage.json").read_bytes());v["extra"]=1;put(root/"stage.json",canon(v));d=sha(canon(v))
    else:
        v=json.loads((root/"inputs/profile.json").read_bytes())
        if fault=="profile_nonce":v["nonce"]="f"*32
        elif fault=="profile_codepins":v["code_sha256"]["pack_worker.py"]="f"*64
        else:v["pack"]["file"]="../source.pack"
        put(root/"inputs/profile.json",canon(v));d=repin(root)
    with pytest.raises(m.StageError):m.prepare_stage(str(root),expected_descriptor_sha256=d)

def test_forged_recipe_and_ordered_empty_maps(m,tmp_path):
    root,d=stage(tmp_path);p=m.prepare_stage(str(root),expected_descriptor_sha256=d);r=m.make_recipe(p);f=fake(m,p,r)
    forged=m.Recipe(r.name,r.create_bytes+b" ")
    with pytest.raises(m.StageError):m.validate_container(f,CID,p,forged,phase="created")
    f["Config"]["Env"].reverse();m.validate_container(f,CID,p,r,phase="created")
    for key,zero in [("Binds",[]),("PortBindings",{}),("CapAdd",[]),("StorageOpt",{})]:
        f["HostConfig"].pop(key,None);m.validate_container(f,CID,p,r,phase="created")
        f["HostConfig"][key]=None;m.validate_container(f,CID,p,r,phase="created")
        f["HostConfig"][key]=0
        with pytest.raises(m.StageError):m.validate_container(f,CID,p,r,phase="created")
        f["HostConfig"][key]=zero
    for key in ("Memory","Init","Runtime","Ulimits","Tmpfs","Mounts","NetworkMode","RestartPolicy"):
        bad=copy.deepcopy(f);bad["HostConfig"].pop(key)
        with pytest.raises(m.StageError):m.validate_container(bad,CID,p,r,phase="created")


def test_configuration_hash_excludes_runtime_network_metadata_and_mount_order(m,tmp_path):
    root,d=stage(tmp_path);p=m.prepare_stage(str(root),expected_descriptor_sha256=d);r=m.make_recipe(p);f=fake(m,p,r)
    a=m.validate_container(f,CID,p,r,phase="created")["configuration_sha256"]
    f["NetworkSettings"].update(SandboxID="synthetic-initialized",SandboxKey="synthetic-runtime-key")
    f["NetworkSettings"]["Networks"]={"none":{"EndpointID":"synthetic-endpoint","NetworkID":"synthetic-network"}}
    f["Mounts"].reverse()
    assert a==m.validate_container(f,CID,p,r,phase="created")["configuration_sha256"]
    f["NetworkSettings"]["Networks"]["none"]["IPAddress"]="192.0.2.1"
    with pytest.raises(m.StageError):m.validate_container(f,CID,p,r,phase="created")
    f["NetworkSettings"]["Networks"]["none"].pop("IPAddress")
    f["NetworkSettings"]["Ports"]={"80/tcp":[{"HostPort":"8080"}]}
    with pytest.raises(m.StageError):m.validate_container(f,CID,p,r,phase="created")


def private_bindings():
    return {k:v for k,v in sys.modules.items() if k.startswith(("_av_host_","_av_verified_"))}

def test_failed_repeat_preparation_restores_existing_private_bindings(m,tmp_path):
    root,d=stage(tmp_path);p=m.prepare_stage(str(root),expected_descriptor_sha256=d)
    before=private_bindings()
    parent=tmp_path/"second";parent.mkdir(mode=0o700)
    second,_=stage(parent)
    profile=json.loads((second/"inputs/profile.json").read_bytes());profile["extra"]=True
    put(second/"inputs/profile.json",canon(profile));bad=repin(second)
    with pytest.raises(m.StageError):m.prepare_stage(str(second),expected_descriptor_sha256=bad)
    after=private_bindings()
    assert after.keys()==before.keys()
    assert all(after[k] is value for k,value in before.items()), "failed attempt replaced an earlier binding"
    assert sys.modules[p.worker.__name__] is p.worker
    assert sys.modules[p.runtime.__name__] is p.runtime
    assert p.worker._runtime_module(p.profile) is p.runtime
    p.worker.validate_runtime(p.worker.synthetic_runtime(p.profile),p.profile)

def test_failed_preparation_removes_only_new_private_bindings(m,tmp_path,monkeypatch):
    root,d=stage(tmp_path)
    worker_key="_av_host_pack_worker_"+sha((root/"code/pack_worker.py").read_bytes())
    monkeypatch.delitem(sys.modules,worker_key,raising=False)
    before=private_bindings()
    profile=json.loads((root/"inputs/profile.json").read_bytes());profile["extra"]=True
    put(root/"inputs/profile.json",canon(profile));bad=repin(root)
    with pytest.raises(m.StageError):m.prepare_stage(str(root),expected_descriptor_sha256=bad)
    assert worker_key not in sys.modules
    after=private_bindings()
    assert after.keys()==before.keys() and all(after[k] is v for k,v in before.items())


def test_failed_late_preparation_restores_all_touched_helpers(m,tmp_path,monkeypatch):
    root,d=stage(tmp_path);p=m.prepare_stage(str(root),expected_descriptor_sha256=d)
    before=private_bindings();original=m._load
    def load(name,raw):
        mod=original(name,raw)
        if name=="pack_api.py":raise RuntimeError("inert late failure after registrations")
        return mod
    monkeypatch.setattr(m,"_load",load)
    with pytest.raises(m.StageError):m.prepare_stage(str(root),expected_descriptor_sha256=d)
    after=private_bindings()
    assert after.keys()==before.keys() and all(after[k] is v for k,v in before.items())
    assert all(sys.modules[x.__name__] is x for x in (p.worker,p.runtime,p.artifact,p.http,p.api))
    p.worker.validate_runtime(p.worker.synthetic_runtime(p.profile),p.profile)

def test_failed_late_preparation_removes_new_worker_and_runtime(m,tmp_path,monkeypatch):
    root,d=stage(tmp_path)
    worker_key="_av_host_pack_worker_"+sha((root/"code/pack_worker.py").read_bytes())
    runtime_key="_av_verified_pack_runtime_"+sha((root/"code/pack_runtime.py").read_bytes())
    monkeypatch.delitem(sys.modules,worker_key,raising=False)
    monkeypatch.delitem(sys.modules,runtime_key,raising=False)
    before=private_bindings();original=m._load
    def load(name,raw):
        if name=="pack_artifact.py":raise RuntimeError("inert failure after runtime")
        return original(name,raw)
    monkeypatch.setattr(m,"_load",load)
    with pytest.raises(m.StageError):m.prepare_stage(str(root),expected_descriptor_sha256=d)
    assert worker_key not in sys.modules and runtime_key not in sys.modules
    after=private_bindings()
    assert after.keys()==before.keys() and all(after[k] is v for k,v in before.items())


@pytest.fixture(autouse=True)
def no_native_resources(monkeypatch):
    import socket, threading, subprocess, ctypes, signal, resource
    calls=[]
    def forbidden(*a,**kw): calls.append(True);raise RuntimeError("native tripwire")
    for module,name in ((socket,"socket"),(threading,"Timer"),(subprocess,"Popen"),(ctypes,"CDLL"),
                        (os,"fork"),(signal,"setitimer"),(resource,"setrlimit"),(os,"statvfs")):
        monkeypatch.setattr(module,name,forbidden)
    yield
    assert calls==[], "a native dependency was reached, even if normalized"


PLATFORM = {"architecture":"arm64","os":"linux","variant":"v8"}


def test_exact_descriptor_forms_retained_in_hashes(m,tmp_path):
    root,d=stage(tmp_path);p=m.prepare_stage(str(root),expected_descriptor_sha256=d);r=m.make_recipe(p)
    f=fake(m,p,r);before=copy.deepcopy(f)
    simple=m.validate_container(f,CID,p,r,phase="created")
    assert f==before
    f["ImageManifestDescriptor"]["platform"]=copy.deepcopy(PLATFORM);before=copy.deepcopy(f)
    extended=m.validate_container(f,CID,p,r,phase="created")
    assert f==before
    for key in ("configuration_sha256","configuration_raw_sha256"):
        assert simple[key]!=extended[key], "descriptor platform must remain a configuration change"


@pytest.mark.parametrize("fault",["extra","missing-mediaType","missing-digest","missing-size","mediaType","digest","size","size-bool","size-string","size-float",
    "platform-null","platform-list","platform-string","platform-extra","platform-missing-architecture","platform-missing-os","platform-missing-variant",
    "architecture","os","variant","architecture-type","os-type","variant-type"])
def test_descriptor_exact_schema_rejects_other_forms(m,tmp_path,fault):
    root,d=stage(tmp_path);p=m.prepare_stage(str(root),expected_descriptor_sha256=d);r=m.make_recipe(p);f=fake(m,p,r)
    desc=f["ImageManifestDescriptor"]
    desc["platform"]=copy.deepcopy(PLATFORM)
    if fault.startswith("missing-"):del desc[fault[8:]]
    elif fault=="extra":desc["extra"]=False
    elif fault=="mediaType":desc["mediaType"]="application/json"
    elif fault=="digest":desc["digest"]="sha256:"+"f"*64
    elif fault.startswith("size"):desc["size"]={"size":1755,"size-bool":True,"size-string":"1754","size-float":1754.0}[fault]
    elif fault.startswith("platform-missing-"):del desc["platform"][fault[17:]]
    elif fault=="platform-extra":desc["platform"]["extra"]=False
    elif fault.startswith("platform-"):desc["platform"]={"platform-null":None,"platform-list":[],"platform-string":"linux/arm64/v8"}[fault]
    elif fault.endswith("-type"):desc["platform"][fault[:-5]]=False
    else:desc["platform"][fault]="wrong"
    with pytest.raises(m.StageError):m.validate_container(f,CID,p,r,phase="created")


@pytest.mark.parametrize("platform",[False,True])
def test_oom_false_created_null_started_policy_and_raw_hashes(m,tmp_path,platform):
    root,d=stage(tmp_path);p=m.prepare_stage(str(root),expected_descriptor_sha256=d);r=m.make_recipe(p);f=fake(m,p,r)
    assert r.body["HostConfig"]["OomKillDisable"] is False
    if platform:f["ImageManifestDescriptor"]["platform"]=copy.deepcopy(PLATFORM)
    before=copy.deepcopy(f);created=m.validate_container(f,CID,p,r,phase="created")
    assert f==before
    f["HostConfig"]["OomKillDisable"]=None
    f["State"].update(Status="running",Running=True,Pid=9,StartedAt="2026-11-03T00:00:00Z")
    before=copy.deepcopy(f);observed=m.validate_container(f,CID,p,r)
    assert f==before, "validator must preserve complete raw inspect"
    assert created["configuration_sha256"]==observed["configuration_sha256"]
    assert created["configuration_raw_sha256"]!=observed["configuration_raw_sha256"]
    assert created["configuration_sha256"]==created["configuration_raw_sha256"]
    raw={k:f[k] for k in ("Id","Image","Name","Platform","ImageManifestDescriptor","Path","Args","Config","HostConfig")}
    raw["Mounts"]=sorted(f["Mounts"],key=lambda x:x["Destination"])
    assert observed["configuration_raw_sha256"]==sha(canon(raw))
    policy=copy.deepcopy(raw);policy["HostConfig"]["OomKillDisable"]=False
    assert observed["configuration_sha256"]==sha(canon(policy))


@pytest.mark.parametrize("value",[True,0,1,"false",[],{},"missing"])
def test_oom_representation_rejects_other_types_and_missing(m,tmp_path,value):
    root,d=stage(tmp_path);p=m.prepare_stage(str(root),expected_descriptor_sha256=d);r=m.make_recipe(p);f=fake(m,p,r)
    if value=="missing":del f["HostConfig"]["OomKillDisable"]
    else:f["HostConfig"]["OomKillDisable"]=value
    with pytest.raises(m.StageError):m.validate_container(f,CID,p,r,phase="created")
