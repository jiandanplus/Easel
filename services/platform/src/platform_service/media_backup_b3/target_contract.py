"""Pure B3 target policy. This module performs NO operations and mints NO handles.

All spec fields are independently supplied by Root, never by archive metadata.
Docker rendering profile complete-v1 is deliberately closed: the full Root
reference top-level keyset, Config, HostConfig, mount requests, State and network
metadata must match the explicit policy below. Unknown Docker-version fields
fail closed. The old prototype keyset is unsupported. No partial projection
silently discards a hook.
Only capability array order/one CAP_ prefix and two NNP spellings normalize.
Env hashing uses canonical JSON of the SORTED complete string list, including
image defaults and bootstrap password. The hash is private Root input; this code
never reads an env file. PGDATA is a named volume; tmpfs is socket/temp only.

Validated observations are immutable private bytes with hashes-only summaries.
They prove neither CLI execution, freshness, process UID/caps, nor capacity. 3B
must bind real VerifiedRestoreInput, absence/create/start ledger, clock, SQL
identity and private authority; these ordinary dataclasses are NOT that authority.
"""
from dataclasses import dataclass
from datetime import date
import copy
import hashlib
import json
import math
from pathlib import PurePosixPath
import re
from .contracts import B3Error, require, canonical, digest, _bounded

IMAGE_ID = "sha256:9e2cf6287f25c3151f4c9421ccce2b17f3e60061c111a29165e0d2cdd9ff0a97"
CAPS = ("CHOWN", "DAC_OVERRIDE", "KILL", "SETGID", "SETUID")
PGDATA = "/var/lib/postgresql/data"
TMPFS = (("/var/run/postgresql", "rw,nosuid,nodev,size=16777216"),
         ("/tmp", "rw,nosuid,nodev,size=16777216"))
COMMAND = ("postgres", "-c", "max_wal_size=256MB", "-c", "min_wal_size=80MB",
           "-c", "checkpoint_timeout=30s", "-c", "shared_buffers=128MB",
           "-c", "fsync=on", "-c", "full_page_writes=on", "-c", "synchronous_commit=on")
ZERO_TIME = "0001-01-01T00:00:00Z"

# Rendering profile: Root public parser created SHA 9c84563b...100eb9 and
# running SHA 20b86ccb...fe45, plus public local named-volume metadata.
# These bytes pin platform defaults ONLY; target policy is independently above.
# All fields are retained. No volatile field is discarded or freshness inferred.
# The old nine-key prototype is no longer an accepted rendering profile.
# StopTimeout pins the observed current-daemon/image rendering value 1. Root
# confirms no planned argv change is needed. Any future actual mismatch requires
# independent review; no general Docker default is inferred. Unknown schema rejects.
# Known representation alternatives: absent/None Healthcheck, omitted/False mount
# ReadOnly, None/{} volume Options, None/[] Dns, None/False OomKillDisable.
# All alternatives preserve their original canonical representation.
HOST_DEFAULTS_JSON = r'''
{
  "AutoRemove": false,
  "BlkioDeviceReadBps": [],
  "BlkioDeviceReadIOps": [],
  "BlkioDeviceWriteBps": [],
  "BlkioDeviceWriteIOps": [],
  "BlkioWeight": 0,
  "BlkioWeightDevice": [],
  "Cgroup": "",
  "CgroupParent": "",
  "CgroupnsMode": "private",
  "ConsoleSize": [
    0,
    0
  ],
  "ContainerIDFile": "",
  "CpuCount": 0,
  "CpuPercent": 0,
  "CpuPeriod": 0,
  "CpuQuota": 0,
  "CpuRealtimePeriod": 0,
  "CpuRealtimeRuntime": 0,
  "CpuShares": 0,
  "CpusetCpus": "",
  "CpusetMems": "",
  "DeviceCgroupRules": null,
  "IOMaximumBandwidth": 0,
  "IOMaximumIOps": 0,
  "Isolation": "",
  "MaskedPaths": [
    "/proc/acpi",
    "/proc/asound",
    "/proc/interrupts",
    "/proc/kcore",
    "/proc/keys",
    "/proc/latency_stats",
    "/proc/sched_debug",
    "/proc/scsi",
    "/proc/timer_list",
    "/proc/timer_stats",
    "/sys/devices/virtual/powercap",
    "/sys/firmware"
  ],
  "MemoryReservation": 0,
  "MemorySwappiness": null,
  "OomKillDisable": false,
  "OomScoreAdj": 0,
  "ReadonlyPaths": [
    "/proc/bus",
    "/proc/fs",
    "/proc/irq",
    "/proc/sys",
    "/proc/sysrq-trigger"
  ],
  "ShmSize": 67108864,
  "Ulimits": [],
  "VolumeDriver": ""
}
'''
MANIFEST_JSON = r'''
{
  "annotations": {
    "com.docker.official-images.bashbrew.arch": "arm64v8",
    "org.opencontainers.image.base.digest": "sha256:a66b19aa552f595b4ab276c6b4f9b24fa4546632c5ecd88cccdced4a6866323a",
    "org.opencontainers.image.base.name": "debian:trixie-slim",
    "org.opencontainers.image.created": "2026-10-21T00:40:27Z",
    "org.opencontainers.image.revision": "9d15534160ade17f2b6c455a39ee967c49b1937d",
    "org.opencontainers.image.source": "https://github.com/docker-library/postgres.git#9d15534160ade17f2b6c455a39ee967c49b1937d:16/trixie",
    "org.opencontainers.image.url": "https://hub.docker.com/_/postgres",
    "org.opencontainers.image.version": "16.15"
  },
  "digest": "sha256:d735d89e0f050dd5efa611ad633e44e06b526da77b04a7501cece311cd032375",
  "mediaType": "application/vnd.oci.image.manifest.v1+json",
  "platform": {
    "architecture": "arm64",
    "os": "linux",
    "variant": "v8"
  },
  "size": 3630
}
'''
NONE_ENDPOINT_JSON = r'''
{
  "Aliases": null,
  "DNSNames": null,
  "DriverOpts": null,
  "EndpointID": "",
  "Gateway": "",
  "GlobalIPv6Address": "",
  "GlobalIPv6PrefixLen": 0,
  "GwPriority": 0,
  "IPAMConfig": null,
  "IPAddress": "",
  "IPPrefixLen": 0,
  "IPv6Gateway": "",
  "Links": null,
  "MacAddress": "",
  "NetworkID": ""
}
'''
CONTAINER_KEYS = ('AppArmorProfile', 'Args', 'Config', 'Created', 'Driver', 'ExecIDs', 'HostConfig', 'HostnamePath', 'HostsPath', 'Id', 'Image', 'ImageManifestDescriptor', 'LogPath', 'MountLabel', 'Mounts', 'Name', 'NetworkSettings', 'Path', 'Platform', 'ProcessLabel', 'ResolvConfPath', 'RestartCount', 'State', 'Storage')



def _private_repr(self):
    return "<B3 private pure contract>"


def _match(value, pattern):
    require(type(value) is str and re.fullmatch(pattern, value, re.ASCII) is not None)


def _hash(value):
    _match(value, r"[a-f0-9]{64}")


def _ident(value):
    _match(value, r"[A-Za-z_][A-Za-z0-9_]{0,62}")


def _path(value):
    require(type(value) is str and 1 < len(value) <= 4096)
    _match(value, r"/[A-Za-z0-9_./-]+")
    require(str(PurePosixPath(value)) == value and all(p not in (".", "..", "") for p in value.split("/")[1:]))


def _name(value):
    _match(value, r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


def _number(value):
    require(type(value) in (int, float) and math.isfinite(value) and value > 0)


def _closed(value, keys):
    require(type(value) is dict and set(value) == set(keys))


def _equal(value, expected):
    # Python bool/int equality must never weaken typed Docker policy.
    require(type(value) is type(expected) and value == expected)
    if type(expected) is dict:
        for k in expected: _equal(value[k], expected[k])
    elif type(expected) in (list, tuple):
        for v, e in zip(value, expected): _equal(v, e)


def _tuple(value, validator, nonempty=False):
    require(type(value) is tuple and (not nonempty or bool(value)))
    for item in value: validator(item)
    require(len(value) == len(set(value)))


@dataclass(frozen=True, repr=False)
class TargetSpec:
    restore_id: str
    kind: str
    operation_nonce: str
    started_at: float
    deadline: float
    image_id: str
    source_container_id: str
    source_system_id_sha256: str
    source_volume_names: tuple
    source_admin_name: str
    # Shared GLOBAL union of restored roles across both kind contexts, supplied
    # independently by Root. Both specs must carry the identical union so each
    # bootstrap role is checked against every restored role, including its peer.
    restored_role_names: tuple
    bootstrap_role: str
    docker_path: str
    docker_socket: str
    docker_config_path: str
    env_file_path: str
    env_file_sha256: str
    expected_env_sha256: str
    admission_bytes: int
    running_budget_bytes: int

    __repr__ = _private_repr

    def __post_init__(self):
        try:
            _name(self.restore_id); require(type(self.kind) is str and self.kind in ("central", "identity"))
            _match(self.operation_nonce, r"[a-f0-9]{32}")  # exactly 128 bits
            _number(self.started_at); _number(self.deadline)
            require(0 < self.deadline-self.started_at <= 1800)
            _equal(self.image_id, IMAGE_ID)
            _hash(self.source_container_id); _hash(self.source_system_id_sha256)
            _tuple(self.source_volume_names, _name, True)
            for v in (self.source_admin_name, self.bootstrap_role): _ident(v)
            _tuple(self.restored_role_names, _ident, True)
            require(self.bootstrap_role not in self.restored_role_names and self.bootstrap_role != self.source_admin_name)
            for v in (self.docker_path, self.docker_config_path, self.env_file_path): _path(v)
            require(len({self.docker_path, self.docker_config_path, self.env_file_path}) == 3)
            require(type(self.docker_socket) is str and self.docker_socket.startswith("unix://"))
            _path(self.docker_socket[7:])
            _hash(self.env_file_sha256); _hash(self.expected_env_sha256)
            _equal(self.admission_bytes, 8*1024**3); _equal(self.running_budget_bytes, 2*1024**3)
            require(self.volume_name not in self.source_volume_names)
        except (TypeError, ValueError, AttributeError, OverflowError):
            raise B3Error("invalid B3 pure contract") from None

    @property
    def volume_name(self):
        return "b3-"+self.kind+"-"+self.operation_nonce+"-pgdata"

    @property
    def container_name(self):
        return "b3-"+self.kind+"-"+self.operation_nonce+"-pg"


def _spec(s):
    require(type(s) is TargetSpec)
    # Revalidate even a caller that bypassed frozen dataclass assignment.
    s.__post_init__()


def _labels(s):
    return {"purpose":"b3-isolated-restore", "restore_id":s.restore_id,
            "kind":s.kind, "operation_nonce":s.operation_nonce}


@dataclass(frozen=True, repr=False)
class TargetResourcePlan:
    spec: TargetSpec
    volume_argv: tuple
    container_argv: tuple
    __repr__ = _private_repr

    def __post_init__(self):
        _spec(self.spec)
        for argv in (self.volume_argv, self.container_argv):
            require(type(argv) is tuple and all(type(x) is str and "\x00" not in x for x in argv))
        require((self.volume_argv, self.container_argv) == _plan_argv(self.spec))

    def safe_summary(self):
        return {"volume_plan_sha256":digest(self.volume_argv), "container_plan_sha256":digest(self.container_argv),
                "actual_resources_observed":False, "new_instance_proven":False}


def _plan_argv(spec):
    _spec(spec)
    prefix = (spec.docker_path, "-H", spec.docker_socket, "--config", spec.docker_config_path)
    labels = tuple(v for k,val in _labels(spec).items() for v in ("--label", k+"="+val))
    volume = prefix+("volume", "create", "--driver", "local")+labels+(spec.volume_name,)
    container = prefix+("create", "--pull", "never", "--name", spec.container_name)+labels+(
        "--network", "none", "--read-only", "--cap-drop", "ALL")
    container += tuple(v for cap in CAPS for v in ("--cap-add", cap))
    container += ("--security-opt", "no-new-privileges", "--cpus", "1", "--memory", "768m",
                  "--memory-swap", "768m", "--pids-limit", "128", "--restart", "no", "--ipc", "private",
                  "--user", "0:0", "--env-file", spec.env_file_path, "--mount",
                  "type=volume,src="+spec.volume_name+",dst="+PGDATA)
    container += tuple(v for path, options in TMPFS for v in ("--tmpfs", path+":"+options))
    container += ("--log-driver", "json-file", "--log-opt", "max-size=4m", "--log-opt", "max-file=1", spec.image_id)+COMMAND
    return volume, container


def build_target_plan(spec):
    volume, container = _plan_argv(spec)
    return TargetResourcePlan(spec, volume, container)


@dataclass(frozen=True, repr=False)
class ValidatedObservation:
    """Private canonical bytes retain full supplied accepted rendering, not authority."""
    canonical_projection: bytes
    __repr__ = _private_repr

    def __post_init__(self):
        require(type(self.canonical_projection) is bytes)

    @property
    def sha256(self):
        return hashlib.sha256(self.canonical_projection).hexdigest()

    def safe_summary(self):
        return {"observation_sha256":self.sha256, "actual_resources_observed":False, "new_instance_proven":False}


def validate_volume_observation(spec, rawdict):
    try:
        _spec(spec); _bounded(rawdict)
        _closed(rawdict, ("Name", "Driver", "Scope", "Labels", "Options", "Mountpoint", "CreatedAt"))
        for key,value in (("Name",spec.volume_name),("Driver","local"),("Scope","local"),("Labels",_labels(spec))):
            _equal(rawdict[key], value)
        require(rawdict["Options"] is None or type(rawdict["Options"]) is dict and not rawdict["Options"])
        _running_started_at(rawdict["CreatedAt"])
        _path(rawdict["Mountpoint"])
        # With local driver/no options, the mount must resolve to this named
        # volume's standard data directory, never a source volume's path.
        require(rawdict["Mountpoint"].endswith("/"+spec.volume_name+"/_data"))
        return ValidatedObservation(canonical(copy.deepcopy(rawdict)))
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        raise B3Error("invalid B3 pure contract") from None


def _caps(value, expected):
    require(type(value) is list)
    result=[]
    for cap in value:
        require(type(cap) is str)
        cap = cap[4:] if cap.startswith("CAP_") else cap
        require(cap in expected and cap not in result); result.append(cap)
    require(set(result) == set(expected))
    return sorted(result)


def _env(spec, value):
    require(type(value) is list and len(value) == 11)
    env={}
    for item in value:
        require(type(item) is str and "=" in item)
        key, val = item.split("=",1)
        require(key not in env); env[key]=val
    _closed(env, ("PATH", "GOSU_VERSION", "LANG", "PG_MAJOR", "PG_VERSION", "PGDATA", "POSTGRES_USER", "POSTGRES_DB", "POSTGRES_HOST_AUTH_METHOD", "POSTGRES_PASSWORD", "POSTGRES_INITDB_ARGS"))
    for key, val in (("PG_MAJOR","16"),("PG_VERSION","16.15-1.pgdg13+2"),("PGDATA",PGDATA),
                     ("POSTGRES_USER",spec.bootstrap_role),("POSTGRES_DB","postgres"),("POSTGRES_HOST_AUTH_METHOD","scram-sha-256"),
                     ("POSTGRES_INITDB_ARGS","--encoding=UTF8 --locale=C --auth-host=scram-sha-256")):
        _equal(env[key],val)
    # Public image defaults are independently hash pinned by Root; no overrides.
    require(all(type(env[k]) is str and 0 < len(env[k]) <= 4096 for k in ("PATH","GOSU_VERSION","LANG")))
    _match(env["POSTGRES_PASSWORD"], r"[!-~]{1,1024}")
    require(digest(sorted(value)) == spec.expected_env_sha256)


def _running_started_at(value):
    require(type(value) is str)
    match=re.fullmatch(r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:[.]([0-9]{1,9}))?Z",value,re.ASCII)
    require(match is not None)
    year,month,day,hour,minute,second=map(int,match.group(1,2,3,4,5,6))
    date(year,month,day)  # Calendar semantics; ValueError becomes generic B3Error.
    require(hour < 24 and minute < 60 and second < 60)
    # Preserve nanosecond precision: datetime microsecond parsing would truncate
    # a valid year-1 +1ns timestamp to zero. Only all-zero fractions equal zero.
    fraction=match.group(7) or "0"
    require((year,month,day,hour,minute,second) != (1,1,1,0,0,0) or int(fraction) != 0)


def _container(spec, raw, volume, running):
    _spec(spec); validate_volume_observation(spec,volume); _bounded(raw)
    _closed(raw, CONTAINER_KEYS)
    _equal(raw["Path"], "docker-entrypoint.sh"); _equal(raw["Args"], list(COMMAND))
    for key, value in (("Platform","linux"),("Driver","overlayfs"),("AppArmorProfile",""),
                       ("MountLabel",""),("ProcessLabel",""),("ExecIDs",None),
                       ("Storage",{"RootFS":{"Snapshot":{"Name":"overlayfs"}}}),
                       ("ImageManifestDescriptor",json.loads(MANIFEST_JSON))):
        _equal(raw[key],value)
    _running_started_at(raw["Created"])
    # Docker-generated paths must bind to this container, never arbitrary host
    # hooks/files. This platform's root is pinned, not inferred from user input.
    for key, leaf in (("HostnamePath","hostname"),("HostsPath","hosts"),
                      ("ResolvConfPath","resolv.conf"),("LogPath",raw["Id"]+"-json.log")):
        _equal(raw[key], "/var/lib/docker/containers/"+raw["Id"]+"/"+leaf if running else "")
    _hash(raw["Id"]); require(raw["Id"] != spec.source_container_id)
    for key, value in (("Image",spec.image_id),("Name","/"+spec.container_name),("RestartCount",0)):
        _equal(raw[key],value)
    config=raw["Config"]
    _closed(config, set(("Image","Labels","User","Entrypoint","Cmd","Env","ExposedPorts","Volumes","StopSignal",
                         "AttachStderr","AttachStdin","AttachStdout","Domainname","Hostname","OpenStdin",
                         "StdinOnce","StopTimeout","Tty","WorkingDir")) | ({"Healthcheck"} if "Healthcheck" in config else set()))
    for key,value in (("Image",spec.image_id),("Labels",_labels(spec)),("User","0:0"),
                      ("Entrypoint",["docker-entrypoint.sh"]),("Cmd",list(COMMAND)),("Volumes",{PGDATA:{}}),
                      ("StopSignal","SIGINT"),("AttachStderr",True),("AttachStdin",False),("AttachStdout",True),
                      ("Domainname",""),("Hostname",raw["Id"][:12]),("OpenStdin",False),("StdinOnce",False),
                      ("StopTimeout",1),("Tty",False),("WorkingDir","")):
        _equal(config[key],value)
    if "Healthcheck" in config: _equal(config["Healthcheck"],None)
    require(config["ExposedPorts"] in ({},{"5432/tcp":{}}))
    _env(spec,config["Env"])
    host=raw["HostConfig"]
    expected = dict(NetworkMode="none", ReadonlyRootfs=True, Privileged=False, NanoCpus=1000000000,
        Memory=805306368, MemorySwap=805306368, PidsLimit=128,
        RestartPolicy={"Name":"no","MaximumRetryCount":0}, IpcMode="private", PidMode="", UTSMode="", UsernsMode="",
        Binds=None, VolumesFrom=None, Devices=[], DeviceRequests=None, PortBindings={}, PublishAllPorts=False,
        Runtime="runc", ExtraHosts=None, Links=None, GroupAdd=None, Dns=[], DnsOptions=[], DnsSearch=[],
        Mounts=[dict(Type="volume",Source=spec.volume_name,Target=PGDATA,ReadOnly=False)], Tmpfs=dict(TMPFS),
        LogConfig={"Type":"json-file","Config":{"max-size":"4m","max-file":"1"}})
    expected.update(json.loads(HOST_DEFAULTS_JSON))
    # Two observed daemon defaults and the named-mount omitted false spelling.
    if host.get("Dns") is None: expected["Dns"] = None
    if host.get("OomKillDisable") is None: expected["OomKillDisable"] = None
    request = host.get("Mounts")
    require(type(request) is list and len(request) == 1 and type(request[0]) is dict)
    if "ReadOnly" not in request[0]: expected["Mounts"][0].pop("ReadOnly")
    _closed(host, set(expected)|{"CapAdd","CapDrop","SecurityOpt"})
    for key,value in expected.items():
        _equal(host[key],value)
    # Explicitly validate nested bool/int fields too (dict equality alone coerces).
    _equal(host["RestartPolicy"]["MaximumRetryCount"],0)
    if "ReadOnly" in host["Mounts"][0]: _equal(host["Mounts"][0]["ReadOnly"],False)
    caps=_caps(host["CapAdd"],CAPS); drops=_caps(host["CapDrop"],("ALL",))
    require(host["SecurityOpt"] in (["no-new-privileges"],["no-new-privileges=true"]))
    mounts=raw["Mounts"]; require(type(mounts) is list and 1 <= len(mounts) <= 3)
    seen=set()
    for mount in mounts:
        require(type(mount) is dict)
        destination=mount.get("Destination")
        require(type(destination) is str and destination not in seen); seen.add(destination)
        if destination == PGDATA:
            _closed(mount,("Type","Name","Source","Destination","Driver","Mode","RW","Propagation"))
            for key,value in (("Type","volume"),("Name",spec.volume_name),("Source",volume["Mountpoint"]),
                              ("Driver","local"),("RW",True),("Propagation","")):
                _equal(mount[key],value)
            # Docker local-volume SELinux mode rendering frozen in this profile.
            _equal(mount["Mode"],"z")
        else:
            require(destination in dict(TMPFS))
            _closed(mount,("Type","Source","Destination","Mode","RW","Propagation"))
            for key,value in (("Type","tmpfs"),("Source",""),("Mode",""),("RW",True),("Propagation","")):
                _equal(mount[key],value)
    require(PGDATA in seen)
    net=raw["NetworkSettings"]; _closed(net,("Networks","Ports","SandboxID","SandboxKey"))
    _closed(net["Networks"], ("none",))
    endpoint = json.loads(NONE_ENDPOINT_JSON)
    if running:
        # Network ID is an observed 64hex identifier, NOT kernel isolation proof.
        _hash(net["Networks"]["none"]["NetworkID"])
        _hash(net["Networks"]["none"]["EndpointID"])
        endpoint["NetworkID"] = net["Networks"]["none"]["NetworkID"]
        endpoint["EndpointID"] = net["Networks"]["none"]["EndpointID"]
        _hash(net["SandboxID"])
        _equal(net["SandboxKey"], "/var/run/docker/netns/"+net["SandboxID"][:12])
    else:
        _equal(net["SandboxID"], ""); _equal(net["SandboxKey"], "")
    _equal(net["Networks"]["none"],endpoint)
    require(type(net["Ports"]) is dict and set(net["Ports"]) <= {"5432/tcp"})
    if net["Ports"]: require(net["Ports"]["5432/tcp"] is None or net["Ports"]["5432/tcp"] == [])
    state=raw["State"]
    _closed(state,("Status","Running","Paused","Restarting","OOMKilled","Dead","Pid","StartedAt","ExitCode","Error","FinishedAt"))
    for key,value in (("Status","running" if running else "created"),("Running",running),
                      ("Paused",False),("Restarting",False),("OOMKilled",False),("Dead",False),("ExitCode",0),("Error",""),("FinishedAt",ZERO_TIME)):
        _equal(state[key],value)
    require(type(state["Pid"]) is int)
    if running:
        require(0 < state["Pid"] < 2**31)
        _running_started_at(state["StartedAt"])
    else:
        _equal(state["Pid"],0); _equal(state["StartedAt"],ZERO_TIME)
    projection=copy.deepcopy(raw)
    projection["HostConfig"]["CapAdd"]=caps; projection["HostConfig"]["CapDrop"]=drops
    projection["HostConfig"]["SecurityOpt"]=["no-new-privileges"]
    # Preserve Env order and the entire approved raw rendering, not only hashes.
    return ValidatedObservation(canonical(projection))


def _validate(spec, raw, volume, running):
    try:
        return _container(spec,raw,volume,running)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        raise B3Error("invalid B3 pure contract") from None


def validate_created_container(spec, rawdict, volumedict):
    return _validate(spec,rawdict,volumedict,False)


def validate_running_container(spec, rawdict, volumedict):
    return _validate(spec,rawdict,volumedict,True)


@dataclass(frozen=True, repr=False)
class TargetIdentity:
    """Caller-constructible pure observation; deliberately no live authority token."""
    spec: TargetSpec
    container_id: str
    system_id_sha256: str
    volume_observation: ValidatedObservation
    container_observation: ValidatedObservation
    __repr__ = _private_repr

    def __post_init__(self):
        _spec(self.spec); _hash(self.container_id); _hash(self.system_id_sha256)
        require(self.container_id != self.spec.source_container_id and self.system_id_sha256 != self.spec.source_system_id_sha256)
        require(type(self.volume_observation) is ValidatedObservation and type(self.container_observation) is ValidatedObservation)
        try:
            volume=json.loads(self.volume_observation.canonical_projection)
            container=json.loads(self.container_observation.canonical_projection)
            require(container["Id"] == self.container_id)
            require(validate_volume_observation(self.spec,volume) == self.volume_observation)
            require(validate_running_container(self.spec,container,volume) == self.container_observation)
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError):
            raise B3Error("invalid B3 pure contract") from None

    def safe_summary(self):
        return {"container_id_sha256":digest(self.container_id), "system_id_sha256":self.system_id_sha256,
                "container_observation_sha256":self.container_observation.sha256,
                "volume_observation_sha256":self.volume_observation.sha256,
                "actual_resources_observed":False, "new_instance_proven":False}


def bind_target_identity(spec, rawrunning, rawvolume, system_identifier_bytes):
    try:
        observation=validate_running_container(spec,rawrunning,rawvolume)
        require(type(system_identifier_bytes) is bytes and 0 < len(system_identifier_bytes) <= 21)
        text=system_identifier_bytes.decode("ascii")
        require(re.fullmatch(r"[1-9][0-9]{0,19}\n?",text,re.ASCII) is not None)
        decimal=text.removesuffix("\n"); require(int(decimal) < 2**64)
        system_hash=hashlib.sha256(decimal.encode("ascii")).hexdigest()
        return TargetIdentity(spec,rawrunning["Id"],system_hash,validate_volume_observation(spec,rawvolume),observation)
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError):
        raise B3Error("invalid B3 pure contract") from None


def verify_distinct_pair(central, identity):
    require(type(central) is TargetIdentity and type(identity) is TargetIdentity)
    central.__post_init__(); identity.__post_init__()
    a,b=central.spec,identity.spec
    require((a.kind,b.kind) == ("central","identity"))
    for field in ("restore_id","started_at","deadline","image_id","source_container_id","source_system_id_sha256",
                  "source_volume_names","source_admin_name","restored_role_names","docker_path","docker_socket","docker_config_path"):
        require(getattr(a,field) == getattr(b,field))
    for field in ("container_id","system_id_sha256"):
        require(getattr(central,field) != getattr(identity,field))
    for field in ("operation_nonce","volume_name","container_name","bootstrap_role","env_file_path"):
        require(getattr(a,field) != getattr(b,field))
    return {"distinct_pair":True,"pair_sha256":digest([central.safe_summary(),identity.safe_summary()]),
            "actual_resources_observed":False,"new_instance_proven":False}
