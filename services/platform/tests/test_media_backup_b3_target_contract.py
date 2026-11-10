"""Independent toy Docker rendering; no actors, credentials, or actual resources."""
import copy
import dataclasses
import hashlib
import importlib
import json
from pathlib import Path
import sys
import types
import unittest

# Do not import application __init__ or its runtime dependencies.
ROOT = Path(__file__).resolve().parents[1] / "src" / "platform_service"
for name, path in (("platform_service", ROOT), ("platform_service.media_backup_b3", ROOT / "media_backup_b3")):
    if name not in sys.modules:
        package = types.ModuleType(name)
        package.__path__ = [str(path)]
        sys.modules[name] = package
m = importlib.import_module("platform_service.media_backup_b3.target_contract")

ENV = ["PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/lib/postgresql/16/bin", "GOSU_VERSION=1.19", "LANG=en_US.utf8", "PG_MAJOR=16", "PG_VERSION=16.15-1.pgdg13+2", "PGDATA=/var/lib/postgresql/data", "POSTGRES_USER=boot_c", "POSTGRES_DB=postgres", "POSTGRES_HOST_AUTH_METHOD=scram-sha-256", "POSTGRES_PASSWORD=synthetic-only-password", "POSTGRES_INITDB_ARGS=--encoding=UTF8 --locale=C --auth-host=scram-sha-256"]
def envhash(env):
    return hashlib.sha256(json.dumps(sorted(env), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
def spec(**kw):
    fields = dict(restore_id="toy_job", kind="central", operation_nonce="a"*32, started_at=100., deadline=1900., image_id="sha256:9e2cf6287f25c3151f4c9421ccce2b17f3e60061c111a29165e0d2cdd9ff0a97", source_container_id="1"*64, source_system_id_sha256="2"*64, source_volume_names=("original-data",), source_admin_name="source_admin", restored_role_names=("application","identity_application"), bootstrap_role="boot_c", docker_path="/usr/local/bin/docker", docker_socket="unix:///var/run/docker.sock", docker_config_path="/private/root/toy/config", env_file_path="/private/root/toy/env", env_file_sha256="3"*64, expected_env_sha256=envhash(ENV), admission_bytes=8589934592, running_budget_bytes=2147483648)
    fields.update(kw)
    return m.TargetSpec(**fields)
def observations(s, running=False):
    labels = dict(purpose="b3-isolated-restore", restore_id=s.restore_id, kind=s.kind, operation_nonce=s.operation_nonce)
    volume = dict(Name=s.volume_name, Driver="local", Scope="local", Labels=labels.copy(), Options={}, Mountpoint="/var/lib/docker/volumes/"+s.volume_name+"/_data")
    host = dict(NetworkMode="none", ReadonlyRootfs=True, Privileged=False, NanoCpus=1000000000, Memory=805306368, MemorySwap=805306368, PidsLimit=128, RestartPolicy={"Name":"no", "MaximumRetryCount":0}, CapDrop=["ALL"], CapAdd=["CHOWN","DAC_OVERRIDE","KILL","SETGID","SETUID"], SecurityOpt=["no-new-privileges"], IpcMode="private", PidMode="", UTSMode="", UsernsMode="", Binds=None, VolumesFrom=None, Devices=[], DeviceRequests=None, PortBindings={}, PublishAllPorts=False, Runtime="runc", ExtraHosts=None, Links=None, GroupAdd=None, Dns=[], DnsOptions=[], DnsSearch=[], Mounts=[dict(Type="volume", Source=s.volume_name, Target="/var/lib/postgresql/data", ReadOnly=False)], Tmpfs={"/tmp":"rw,nosuid,nodev,size=16777216", "/var/run/postgresql":"rw,nosuid,nodev,size=16777216"}, LogConfig={"Type":"json-file", "Config":{"max-size":"4m", "max-file":"1"}})
    config = dict(Image=s.image_id, Labels=labels.copy(), User="0:0", Entrypoint=["docker-entrypoint.sh"], Cmd=["postgres","-c","max_wal_size=256MB","-c","min_wal_size=80MB","-c","checkpoint_timeout=30s","-c","shared_buffers=128MB","-c","fsync=on","-c","full_page_writes=on","-c","synchronous_commit=on"], Env=ENV.copy(), ExposedPorts={"5432/tcp":{}}, Volumes={"/var/lib/postgresql/data":{}}, Healthcheck=None, StopSignal="SIGINT")
    state = dict(Status="running" if running else "created", Running=running, Paused=False, Restarting=False, OOMKilled=False, Dead=False, Pid=123 if running else 0, StartedAt="2026-11-03T00:00:00Z" if running else "0001-01-01T00:00:00Z")
    raw = dict(Id="4"*64, Image=s.image_id, Name="/"+s.container_name, Config=config, HostConfig=host, Mounts=[dict(Type="volume", Name=s.volume_name, Source=volume["Mountpoint"], Destination="/var/lib/postgresql/data", Driver="local", Mode="z", RW=True, Propagation="")], NetworkSettings={"Networks":{"none":{}}, "Ports":{"5432/tcp":None}}, State=state, RestartCount=0)
    # Keep every complete Root reference field. Independently override the parser
    # security/identity/command with the literal target policy above.
    fixtures = Path(__file__).parent / "fixtures/media_backup_b3/docker_rendering_v1"
    template = json.loads((fixtures / (("running" if running else "created") + "-parser-secret-free.synthetic-reference.json")).read_text())
    complete = copy.deepcopy(template)
    for key in ("Config", "HostConfig", "State"):
        complete[key].update(raw[key])
    for key in ("Id", "Image", "Name", "Mounts", "RestartCount"):
        complete[key] = raw[key]
    complete["Config"].update(Hostname=raw["Id"][:12], StopTimeout=1)
    complete["Config"].pop("Healthcheck")  # actual known absence
    complete["HostConfig"]["Mounts"][0].pop("ReadOnly")
    complete["Path"] = "docker-entrypoint.sh"
    complete["Args"] = config["Cmd"].copy()
    for key, leaf in (("HostnamePath", "hostname"), ("HostsPath", "hosts"), ("ResolvConfPath", "resolv.conf"), ("LogPath", raw["Id"] + "-json.log")):
        complete[key] = "/var/lib/docker/containers/" + raw["Id"] + "/" + leaf if running else ""
    # Retain full none endpoint and sandbox, not prototype's empty projection.
    complete["NetworkSettings"]["Ports"] = raw["NetworkSettings"]["Ports"]
    volume["CreatedAt"] = "2026-11-03T00:00:00.123456789Z"
    volume["Options"] = None
    return complete, volume

def rebind_id(raw):
    raw["Config"]["Hostname"] = raw["Id"][:12]
    for key, leaf in (("HostnamePath", "hostname"), ("HostsPath", "hosts"), ("ResolvConfPath", "resolv.conf"), ("LogPath", raw["Id"] + "-json.log")):
        raw[key] = "/var/lib/docker/containers/" + raw["Id"] + "/" + leaf

class TargetContractTests(unittest.TestCase):
    def test_full_reference_fixture_identity(self):
        fixture = Path(__file__).parent / "fixtures/media_backup_b3/docker_rendering_v1"
        expected = {
            "created-parser-secret-free.synthetic-reference.json": "9c84563bc28ea658c584adc89138a8493edeb3d1e1b53f60876ee3f484100eb9",
            "running-parser-secret-free.synthetic-reference.json": "20b86ccb010585c4622c63f993b2ac658f011dba8b2342ede585afc6d832fe45",
        }
        manifest = json.loads((fixture / "identity-manifest.json").read_text())
        for name, digest in manifest.items():
            self.assertEqual(hashlib.sha256((fixture/name).read_bytes()).hexdigest(),digest)
        for name, digest in expected.items(): self.assertEqual(manifest[name],digest)
        for running in (False, True):
            raw, volume = observations(spec(),running)
            reference = json.loads((fixture / (("running" if running else "created") + "-parser-secret-free.synthetic-reference.json")).read_text())
            self.assertEqual(set(raw),set(reference))
            self.assertEqual(set(raw["Config"]),set(reference["Config"]))
            self.assertEqual(set(raw["HostConfig"]),set(reference["HostConfig"]) | {"Mounts"})
            validator = m.validate_running_container if running else m.validate_created_container
            self.assertEqual(json.loads(validator(spec(),raw,volume).canonical_projection),raw)

    def test_every_rendered_leaf_and_unknown_nested_fields(self):
        # Exercise every full-profile scalar/default/list/map leaf. This covers
        # all independently enumerated CPU/io/cgroup/memory/security defaults,
        # image/platform descriptors, no-network fields and mount/state fields.
        def leaves(value, path=()):
            if type(value) is dict and value:
                for k,v in value.items(): yield from leaves(v,path+(k,))
            elif type(value) is list and value:
                for k,v in enumerate(value): yield from leaves(v,path+(k,))
            else: yield path,value
        def dictionaries(value,path=()):
            if type(value) is dict:
                yield path
                for k,v in value.items(): yield from dictionaries(v,path+(k,))
            elif type(value) is list:
                for k,v in enumerate(value): yield from dictionaries(v,path+(k,))
        count=0
        for running in (False,True):
            validator=m.validate_running_container if running else m.validate_created_container
            raw,volume=observations(spec(),running)
            for original,is_volume in ((raw,False),(volume,True)):
                for path,value in leaves(original):
                    # Mutations are invalid by construction, including bool/int
                    # aliases and list/default shape changes, not just extras.
                    bad=copy.deepcopy(original); target=bad
                    for key in path[:-1]: target=target[key]
                    if type(value) is bool: mutation=not value
                    elif type(value) is int: mutation=True
                    elif type(value) is str: mutation=value+".unsafe"
                    elif value is None: mutation={"unsafe":True}
                    elif type(value) is list: mutation=["unsafe"]
                    else: mutation={"unsafe":True}
                    target[path[-1]]=mutation
                    with self.subTest(running=running,volume=is_volume,path=path),self.assertRaises(m.B3Error):
                        if is_volume: m.validate_volume_observation(spec(),bad)
                        else: validator(spec(),bad,volume)
                    count+=1
                for path in dictionaries(original):
                    bad=copy.deepcopy(original); target=bad
                    for key in path: target=target[key]
                    target["FutureUnsafeHook"]={"enabled":True}
                    with self.subTest(unknown=path,running=running,volume=is_volume),self.assertRaises(m.B3Error):
                        if is_volume: m.validate_volume_observation(spec(),bad)
                        else: validator(spec(),bad,volume)
                    count+=1
        self.assertGreater(count,400)

    def test_narrow_defaults_preserve_representation(self):
        s=spec()
        for running in (False,True):
            validator=m.validate_running_container if running else m.validate_created_container
            for health in ("absent",None):
                for dns in (None,[]):
                    for oom in (None,False):
                        for options in (None,{}):
                            for readonly in ("absent",False):
                                raw,volume=observations(s,running)
                                if health is None: raw["Config"]["Healthcheck"]=None
                                raw["HostConfig"]["Dns"]=dns
                                raw["HostConfig"]["OomKillDisable"]=oom
                                if readonly is False: raw["HostConfig"]["Mounts"][0]["ReadOnly"]=False
                                volume["Options"]=options
                                before=copy.deepcopy((raw,volume))
                                result=validator(s,raw,volume)
                                self.assertEqual(json.loads(result.canonical_projection),raw)
                                self.assertEqual(json.loads(m.validate_volume_observation(s,volume).canonical_projection),volume)
                                self.assertEqual(before,(raw,volume))
        for obj,path in (("raw",("HostConfig","Mounts",0,"ReadOnly")),("raw",("HostConfig","RestartPolicy","MaximumRetryCount")),("raw",("NetworkSettings","Networks","none","GwPriority")),("raw",("State","ExitCode")),("raw",("ImageManifestDescriptor","size"))):
            raw,volume=observations(s)
            target=raw
            for k in path[:-1]: target=target[k]
            target[path[-1]]=True
            with self.subTest(bool_alias=path),self.assertRaises(m.B3Error): m.validate_created_container(s,raw,volume)

    def test_review_fixed_initdb_environment_required_independently_of_hash(self):
        init="POSTGRES_INITDB_ARGS=--encoding=UTF8 --locale=C --auth-host=scram-sha-256"
        complete=[x for x in ENV if not x.startswith("POSTGRES_INITDB_ARGS=")]+[init]
        s=spec(expected_env_sha256=envhash(complete));raw,v=observations(s)
        raw["Config"]["Env"]=complete
        try:m.validate_created_container(s,raw,v)
        except m.B3Error:self.fail("fixed eleven-entry environment must be accepted")
        bads=([x for x in complete if x!=init],complete+[init])
        bads+=tuple([x if x!=init else value for x in complete] for value in (
            init+" --auth-local=trust",init.replace("scram-sha-256","trust"),
            init+";echo bad",init+"$(echo bad)",init+"\r",init+"\x00"))
        for env in bads:
            candidate=dataclasses.replace(s,expected_env_sha256=envhash(env))
            raw["Config"]["Env"]=env
            with self.subTest(env_hash=envhash(env)),self.assertRaises(m.B3Error):m.validate_created_container(candidate,raw,v)

    def test_positive_nonmutation_and_private_repr(self):
        s=spec(); raw,v=observations(s); before=copy.deepcopy((raw,v))
        result=m.validate_created_container(s,raw,v)
        self.assertEqual((raw,v),before)
        self.assertFalse(result.safe_summary()["actual_resources_observed"])
        self.assertFalse(result.safe_summary()["new_instance_proven"])
        for value in (s,m.build_target_plan(s),result):
            self.assertNotIn("synthetic",repr(value)); self.assertNotIn("/private",repr(value)); self.assertNotIn("boot_c",repr(value))
        with self.assertRaises(dataclasses.FrozenInstanceError): s.deadline=2000
        raw["Config"]["Env"].append("after-validation=mutation")
        self.assertNotIn(b"after-validation",result.canonical_projection)
    def test_plan_exact_policy(self):
        s=spec(); plan=m.build_target_plan(s)
        self.assertEqual(plan.volume_argv, (s.docker_path,"-H",s.docker_socket,"--config",s.docker_config_path,"volume","create","--driver","local","--label","purpose=b3-isolated-restore","--label","restore_id=toy_job","--label","kind=central","--label","operation_nonce="+"a"*32,s.volume_name))
        a=plan.container_argv
        expected=(
            "/usr/local/bin/docker", "-H", "unix:///var/run/docker.sock", "--config", "/private/root/toy/config",
            "create", "--pull", "never", "--name", "b3-central-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-pg",
            "--label", "purpose=b3-isolated-restore", "--label", "restore_id=toy_job", "--label", "kind=central",
            "--label", "operation_nonce=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "--network", "none", "--read-only",
            "--cap-drop", "ALL", "--cap-add", "CHOWN", "--cap-add", "DAC_OVERRIDE", "--cap-add", "KILL",
            "--cap-add", "SETGID", "--cap-add", "SETUID", "--security-opt", "no-new-privileges",
            "--cpus", "1", "--memory", "768m", "--memory-swap", "768m", "--pids-limit", "128",
            "--restart", "no", "--ipc", "private", "--user", "0:0", "--env-file", "/private/root/toy/env",
            "--mount", "type=volume,src=b3-central-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-pgdata,dst=/var/lib/postgresql/data",
            "--tmpfs", "/var/run/postgresql:rw,nosuid,nodev,size=16777216", "--tmpfs", "/tmp:rw,nosuid,nodev,size=16777216",
            "--log-driver", "json-file", "--log-opt", "max-size=4m", "--log-opt", "max-file=1",
            "sha256:9e2cf6287f25c3151f4c9421ccce2b17f3e60061c111a29165e0d2cdd9ff0a97",
            "postgres", "-c", "max_wal_size=256MB", "-c", "min_wal_size=80MB", "-c", "checkpoint_timeout=30s",
            "-c", "shared_buffers=128MB", "-c", "fsync=on", "-c", "full_page_writes=on", "-c", "synchronous_commit=on")
        self.assertEqual(a,expected)
        for flag,val in (("--network","none"),("--memory","768m"),("--memory-swap","768m"),("--cpus","1"),("--pids-limit","128"),("--user","0:0")):
            self.assertEqual(a[a.index(flag)+1],val)
        self.assertEqual(a.count("--cap-add"),5)
        self.assertNotIn("--publish",a); self.assertNotIn("synthetic-only-password",repr(a))
        self.assertEqual(a[-16:],tuple([s.image_id]+observations(s)[0]["Config"]["Cmd"]))
        with self.assertRaises(m.B3Error): dataclasses.replace(plan,container_argv=a+("--privileged",))
    def test_spec_mutations(self):
        for kw in ({"started_at":True},{"deadline":float("nan")},{"deadline":1901},{"operation_nonce":"x"*32},{"source_container_id":"short"},{"image_id":"postgres@sha256:"+"a"*64},{"source_system_id_sha256":"g"*64},{"bootstrap_role":"application"},{"bootstrap_role":"identity_application"},{"bootstrap_role":"source_admin"},{"restored_role_names":["application"]},{"source_volume_names":("x","x")},{"docker_path":"relative"},{"docker_socket":"tcp://localhost"},{"env_file_path":"/a/../b"},{"admission_bytes":True},{"kind":"other"},{"restore_id":"bad/job"}):
            with self.subTest(kw=kw),self.assertRaises(m.B3Error): spec(**kw)
    def test_volume_mutations(self):
        s=spec(); _,v=observations(s)
        for key,val in (("Name","original-data"),("Driver","nfs"),("Scope","global"),("Options",{"device":"/host"}),("Labels",{}),("Mountpoint","relative"),("Mountpoint","/var/lib/docker/volumes/original-data/_data")):
            bad=copy.deepcopy(v); bad[key]=val
            with self.subTest(key=key),self.assertRaises(m.B3Error): m.validate_volume_observation(s,bad)
    def test_container_mutations(self):
        s=spec(); raw,v=observations(s)
        cases=[("Id", "1"*64),("Image","sha256:"+"5"*64),("RestartCount",True),("Config.StopTimeout",10),("Config.User","999:999"),("Config.Healthcheck",{"Test":["CMD","hook"]}),("Config.Image","postgres:16"),("Config.Env",ENV+["HTTP_PROXY=bad"]),("Config.Env",[x for x in ENV if not x.startswith("POSTGRES_PASSWORD=")]),("HostConfig.NetworkMode","bridge"),("HostConfig.PortBindings",{"5432/tcp":[{"HostPort":"5432"}]}),("HostConfig.Devices",[{"PathOnHost":"/dev/anything"}]),("HostConfig.CapAdd",["ALL"]),("HostConfig.CapAdd",["CAP_CAP_CHOWN","DAC_OVERRIDE","KILL","SETGID","SETUID"]),("HostConfig.CapDrop",["ALL","ALL"]),("HostConfig.ReadonlyRootfs",False),("HostConfig.SecurityOpt",[]),("HostConfig.MemorySwap",1610612736),("HostConfig.Memory",True),("HostConfig.NanoCpus",999999999),("HostConfig.PidsLimit",129),("HostConfig.Runtime","custom"),("HostConfig.Tmpfs",{"/var/lib/postgresql/data":"rw"}),("HostConfig.Privileged",True),("HostConfig.Mounts",[]),("HostConfig.Binds",["/host:/data"]),("Mounts",[]),("NetworkSettings.Networks",{"bridge":{}}),("NetworkSettings.Ports",{"5432/tcp":[{"HostPort":"1"}]}),("State.StartedAt","2026-01-01T00:00:00Z"),("State.Pid",1),("State.Running",True)]
        for path,val in cases:
            bad=copy.deepcopy(raw); target=bad; bits=path.split(".")
            for k in bits[:-1]: target=target[k]
            target[bits[-1]]=val
            with self.subTest(path=path),self.assertRaises(m.B3Error): m.validate_created_container(s,bad,v)
        bad=copy.deepcopy(raw); bad["HostConfig"]["FutureUnsafeHook"]="yes"
        with self.assertRaises(m.B3Error): m.validate_created_container(s,bad,v)
    def test_cap_and_tmpfs_boundary(self):
        s=spec(); raw,v=observations(s)
        raw["HostConfig"]["CapAdd"]=["CAP_"+x for x in reversed(raw["HostConfig"]["CapAdd"])]
        raw["HostConfig"]["CapDrop"]=["CAP_ALL"]
        raw["HostConfig"]["SecurityOpt"]=["no-new-privileges=true"]
        raw["Mounts"].append(dict(Type="tmpfs",Source="",Destination="/tmp",Mode="",RW=True,Propagation=""))
        m.validate_created_container(s,raw,v)
        raw["Mounts"].append(copy.deepcopy(raw["Mounts"][-1]))
        with self.assertRaises(m.B3Error): m.validate_created_container(s,raw,v)
    def test_running_timestamp_semantics(self):
        s=spec(); raw,v=observations(s,True)
        invalid=("0001-01-01T00:00:00.0Z", "0001-01-01T00:00:00.000000000Z",
                 "0000-01-01T00:00:00Z", "2026-02-29T00:00:00Z", "2026-04-31T00:00:00Z",
                 "2026-13-01T00:00:00Z", "2026-01-00T00:00:00Z", "2026-01-01T24:00:00Z",
                 "2026-01-01T00:60:00Z", "2026-01-01T00:00:60Z", "2026-01-01T00:00:00.0000000001Z")
        for started in invalid:
            rr=copy.deepcopy(raw); rr["State"]["StartedAt"]=started
            with self.subTest(started=started),self.assertRaises(m.B3Error): m.validate_running_container(s,rr,v)
        # A 1 ns offset from the Go zero instant is nonzero, even though Python's
        # datetime would truncate its microseconds. Preserve the accepted bytes.
        for started in ("0001-01-01T00:00:00.000000001Z", "2024-02-29T23:59:59.123456789Z", "2026-11-03T00:00:00.000Z"):
            rr=copy.deepcopy(raw); rr["State"]["StartedAt"]=started
            result=m.validate_running_container(s,rr,v)
            self.assertEqual(json.loads(result.canonical_projection)["State"]["StartedAt"],started)

    def test_system_ids_and_pair(self):
        s=spec(); raw,v=observations(s,True)
        central=m.bind_target_identity(s,raw,v,b"123456789\n")
        env=[x.replace("POSTGRES_USER=boot_c","POSTGRES_USER=boot_i") for x in ENV]
        peer=spec(env_file_path="/private/root/toy/identity-env",kind="identity",operation_nonce="b"*32,bootstrap_role="boot_i",expected_env_sha256=envhash(env))
        r2,v2=observations(peer,True); r2["Id"]="6"*64; rebind_id(r2); r2["Config"]["Env"]=env
        identity=m.bind_target_identity(peer,r2,v2,b"987654321\n")
        self.assertTrue(m.verify_distinct_pair(central,identity)["distinct_pair"])
        for field,value in (("container_id",central.container_id),("system_id_sha256",central.system_id_sha256)):
            # Fully valid components can still describe peer resource reuse.
            if field == "container_id":
                rr=copy.deepcopy(r2); rr["Id"]=central.container_id; rebind_id(rr)
                other=m.bind_target_identity(peer,rr,v2,b"987654321")
            else: other=m.bind_target_identity(peer,r2,v2,b"123456789")
            with self.subTest(peer_field=field),self.assertRaises(m.B3Error): m.verify_distinct_pair(central,other)
        for field,value in (("deadline",1800.),("source_container_id","8"*64),("bootstrap_role","boot_c"),("restored_role_names",("application",))):
            pp=dataclasses.replace(peer,**{field:value})
            rr,vv=observations(pp,True); rr["Id"]="6"*64; rebind_id(rr)
            ee=[x.replace("POSTGRES_USER=boot_c","POSTGRES_USER="+pp.bootstrap_role) for x in ENV]
            pp=dataclasses.replace(pp,expected_env_sha256=envhash(ee)); rr["Config"]["Env"]=ee
            other=m.bind_target_identity(pp,rr,vv,b"987654321")
            with self.subTest(peer_spec=field),self.assertRaises(m.B3Error): m.verify_distinct_pair(central,other)
        for pair in ((identity,central),(central,central),(central,{})):
            with self.assertRaises(m.B3Error): m.verify_distinct_pair(*pair)
        for bad in (b"",b"0",b"01",b"1\n2",b"1\x00",b"18446744073709551616",b"+1",b" 1",b"1\n\n"):
            with self.subTest(bad=bad),self.assertRaises(m.B3Error): m.bind_target_identity(s,raw,v,bad)
        source=dataclasses.replace(s,source_system_id_sha256=hashlib.sha256(b"123456789").hexdigest())
        with self.assertRaises(m.B3Error): m.bind_target_identity(source,raw,v,b"123456789")
        with self.assertRaises(m.B3Error): m.validate_running_container(s,*observations(s))

if __name__ == "__main__": unittest.main()
