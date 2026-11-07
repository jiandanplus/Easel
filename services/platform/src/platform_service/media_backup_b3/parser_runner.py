"""Root-owned, read-only pg_restore parser, never a database restore authority.

Only existing Root-selected no-network resources are inspected/exec'd. This
module never creates, starts, stops or deletes containers. Root must dispose of
UNKNOWN remote jobs separately. Popen termination does not prove exec ended.

inspect_projection retains full Config, HostConfig, Mounts, Id, Image and
RestartCount; State retains PID/start identity and seven baseline fields;
NetworkSettings retains Ports and Networks (including full per-network data).
Only volatile State diagnostics, ExecIDs and network sandbox diagnostics are
excluded. Expected canonical bytes are independently frozen AFTER Root start.
The one 1MiB parser tmpfs only masks the image VOLUME: no database is initialized.
It is NOT a Task3 target PGDATA substitute (new named volume/768MiB required).

File growth polling/reservation is cooperative observation, not an OS quota or
atomic reservation. Root supervises blocking OS calls under the same original
deadline. Private seals provide API discipline, not same-process isolation.
"""
from dataclasses import dataclass, field
import hashlib
import json
import math
import os
import re
import signal
import stat
import subprocess
import sys
import threading
import time
import uuid

from .contracts import B3Error, canonical, require

_PG = "/usr/lib/postgresql/16/bin/pg_restore"
_SHA = "/usr/bin/sha256sum"
_TMP = "/var/lib/postgresql/data"
_TMPOPTS = "rw,noexec,nosuid,nodev,size=1m,uid=999,gid=999,mode=0700"
_MODES = {"toc": (("--list",), 1024**2),
    "schema": (("--schema-only", "--file=-", "--restrict-key=easelB2SchemaComparison20261030"), 8*1024**2),
    "data": (("--data-only", "--file=-", "--restrict-key=easelB2SchemaComparison20261030"), 16*1024**2)}
_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
_HASH = re.compile(r"[a-f0-9]{64}\Z", re.ASCII)
_STATE = ("Pid", "StartedAt", "Running", "Paused", "Restarting", "OOMKilled", "Dead")
_TOP = ("Id", "Image", "Config", "HostConfig", "Mounts", "NetworkSettings", "RestartCount", "State")
_popen = subprocess.Popen


def _pairs(pairs):
    result={}
    for k,v in pairs:
        require(k not in result); result[k]=v
    return result


def _json(raw):
    require(type(raw) is bytes and len(raw)<=1024**2)
    try:
        return json.loads(raw,object_pairs_hook=_pairs,parse_constant=lambda _: require(False))
    except (ValueError,TypeError,UnicodeError,RecursionError):
        raise B3Error("invalid parser evidence") from None


def inspect_projection(value):
    """Pure projection of ONE Docker inspect object, before authority validation."""
    try:
        require(type(value) is dict)
        result={key:value[key] for key in _TOP}
        result["State"]={key:value["State"][key] for key in _STATE}
        result["NetworkSettings"]={key:value["NetworkSettings"][key] for key in ("Ports","Networks")}
        raw=canonical(result); require(len(raw)<=1024**2)
        return raw
    except (KeyError,TypeError,ValueError,RecursionError):
        raise B3Error("invalid parser evidence") from None


def _baseline(p,a):
    try:
        require(set(p)==set(_TOP) and p["Id"]==a.cid and p["Image"]==a.image_id)
        require(type(p["RestartCount"]) is int and p["RestartCount"]==0)
        s=p["State"]; require(set(s)==set(_STATE))
        require(type(s["Pid"]) is int and s["Pid"]>0 and type(s["StartedAt"]) is str and bool(s["StartedAt"]))
        require(s["Running"] is True)
        require(all(s[k] is False for k in ("Paused","Restarting","OOMKilled","Dead")))
        c=p["Config"]; h=p["HostConfig"]; n=p["NetworkSettings"]
        require(c["Labels"]=={"purpose":"b3-dump-parser","operation_nonce":a.nonce})
        require(c["User"]=="999:999" and c["Entrypoint"]==["/bin/sleep"])
        require(type(c["Cmd"]) is list and len(c["Cmd"])==1 and type(c["Cmd"][0]) is str)
        require(re.fullmatch(r"[1-9][0-9]{0,3}",c["Cmd"][0]) is not None and int(c["Cmd"][0])<=1800)
        require(c.get("Volumes") in (None,{}, {_TMP:{}}))
        require(c.get("ExposedPorts") in (None,{}, {"5432/tcp":{}}))
        env=c["Env"]; require(type(env) is list and all(type(x) is str and "=" in x for x in env))
        keys=[x.split("=",1)[0] for x in env]
        require(len(set(keys))==len(keys) and set(keys)<={"PATH","LANG","LC_ALL","GOSU_VERSION","PG_MAJOR","PG_VERSION","PGDATA"})
        vals=dict(x.split("=",1) for x in env)
        require(vals.get("PGDATA",_TMP)==_TMP)
        require(h["NetworkMode"]=="none" and h["ReadonlyRootfs"] is True and h["Privileged"] is False)
        require(h["CapDrop"]==["ALL"] and h["CapAdd"] in (None,[]))
        require(h["SecurityOpt"] in (["no-new-privileges=true"],["no-new-privileges"]))
        for key,value in (("NanoCpus",1000000000),("Memory",536870912),("MemorySwap",536870912),("PidsLimit",32)):
            require(type(h[key]) is int and h[key]==value)
        for key in ("Binds","Devices","DeviceRequests","VolumesFrom"):
            require(h.get(key) in (None,[]))
        require(h["PortBindings"] in (None,{}) and h["PublishAllPorts"] is False)
        require(h["PidMode"]=="" and h["IpcMode"]=="private")
        for key in ("UTSMode","UsernsMode","CgroupnsMode"):
            require(h.get(key,"") in ("","private"))
        for key in ("DeviceCgroupRules","ExtraHosts","Links"):
            require(h.get(key) in (None,[]))
        require(h.get("Runtime","runc")=="runc")
        require(h["RestartPolicy"]=={"Name":"no","MaximumRetryCount":0})
        require(h["Tmpfs"]=={_TMP:_TMPOPTS})
        # No bind/volume host source is accepted. Docker versions that include
        # tmpfs in Mounts use this exact closed representation.
        require(p["Mounts"] in ([],[{"Type":"tmpfs","Source":"","Destination":_TMP,
            "Mode":"","RW":True,"Propagation":""}]))
        require(set(n)=={"Ports","Networks"} and n["Ports"] in (None,{}, {"5432/tcp":None}, {"5432/tcp":[]}))
        require(type(n["Networks"]) is dict and set(n["Networks"])=={"none"})
    except (KeyError,TypeError,ValueError,RecursionError):
        raise B3Error("invalid parser authority") from None


@dataclass(frozen=True,repr=False)
class ParserAuthority:
    cid: str
    nonce: str
    image_id: str
    docker_path: str
    docker_sha256: str
    socket: str
    docker_config_dir: str
    pg_restore_sha256: str
    expected_inspect: bytes
    expected_inspect_sha256: str
    work_dir_fd: int
    uid: int
    gid: int
    started_at: float
    deadline: float
    reserved_bytes: int
    minimum_free_bytes: int
    cancellation: threading.Event = field(default_factory=threading.Event,compare=False,repr=False)

    def __post_init__(self):
        for v in (self.cid,self.docker_sha256,self.pg_restore_sha256,self.expected_inspect_sha256):
            require(type(v) is str and _HASH.fullmatch(v) is not None)
        require(type(self.image_id) is str and self.image_id.startswith("sha256:") and _HASH.fullmatch(self.image_id[7:]) is not None)
        require(type(self.nonce) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}",self.nonce,re.ASCII) is not None)
        require(type(self.docker_path) is str and self.docker_path.startswith("/") and os.path.normpath(self.docker_path)==self.docker_path and "\x00" not in self.docker_path)
        require(type(self.socket) is str and self.socket.startswith("unix:///") and "\x00" not in self.socket and os.path.normpath(self.socket[7:])==self.socket[7:])
        require(type(self.docker_config_dir) is str and self.docker_config_dir.startswith("/")
            and os.path.normpath(self.docker_config_dir)==self.docker_config_dir and "\x00" not in self.docker_config_dir)
        for v in (self.work_dir_fd,self.uid,self.gid,self.reserved_bytes,self.minimum_free_bytes): require(type(v) is int and v>=0)
        require(64*1024**2<=self.reserved_bytes<=1024**3 and self.minimum_free_bytes<=64*1024**3)
        for v in (self.started_at,self.deadline): require(type(v) in (int,float) and math.isfinite(v) and v>=0)
        require(0<self.deadline-self.started_at<=1800 and type(self.cancellation) is threading.Event)
        p=_json(self.expected_inspect)
        require(canonical(p)==self.expected_inspect and hashlib.sha256(self.expected_inspect).hexdigest()==self.expected_inspect_sha256)
        _baseline(p,self)

    def __repr__(self): return "<ParserAuthority>"


def _stamp(s):
    return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)


def _path(fd):
    if sys.platform=="darwin":
        import fcntl
        return os.fsdecode(fcntl.fcntl(fd,50,bytes(1024)).split(b"\0",1)[0])
    require(sys.platform.startswith("linux"))
    value=os.readlink("/proc/self/fd/"+str(fd)); require(not value.endswith(" (deleted)")); return value


def _terminate_local(process,remaining):
    """Local-only best effort; remote status remains UNKNOWN regardless."""
    try:
        os.killpg(process.pid,signal.SIGTERM)
        process.wait(timeout=max(0,min(0.2,remaining)))
    except (OSError,subprocess.TimeoutExpired,TimeoutError): pass


@dataclass(frozen=True,repr=False,eq=False)
class _InspectedOutput:
    mode: str
    size: int
    sha256: str
    cid: str
    image_id: str
    tool_sha256: str
    authority_sha256: str
    terminal_observed: bool

    def __repr__(self): return "<InspectedParserOutput>"
    def __reduce__(self): raise B3Error("opaque parser output")


class NetworkNoneParserRunner:
    """Six exact-once slots; construction performs no IO or runtime operations."""
    def __init__(self,authority):
        require(type(authority) is ParserAuthority); authority.__post_init__()
        self._authority=authority; self._lock=threading.Lock(); self._failed=False
        self._slots=set(); self._inputs={}; self._outputs={}; self._scratch=[]
        self._active_process=None; self._active_command=None; self._remote_state="NOT_STARTED"
        self._retained_processes=[]; self._used=0; self._config_stamp=None

    @property
    def remote_state(self): return self._remote_state

    def __repr__(self): return "<NetworkNoneParserRunner>"

    def _check(self):
        a=self._authority; now=time.monotonic()
        require(not self._failed and not a.cancellation.is_set() and a.started_at<=now<a.deadline)
        d=os.fstat(a.work_dir_fd)
        require(stat.S_ISDIR(d.st_mode) and stat.S_IMODE(d.st_mode)==0o700 and (d.st_uid,d.st_gid)==(a.uid,a.gid)==(os.geteuid(),os.getegid()))
        v=os.fstatvfs(a.work_dir_fd)
        require(v.f_bavail*v.f_frsize>=a.minimum_free_bytes+(a.reserved_bytes-self._used))
        require(self._used<=a.reserved_bytes)

    def _file(self,fd):
        require(type(fd) is int and fd>=0)
        s=os.fstat(fd); a=self._authority
        require(stat.S_ISREG(s.st_mode) and stat.S_IMODE(s.st_mode)==0o600 and s.st_nlink==1
            and (s.st_uid,s.st_gid)==(a.uid,a.gid) and 0<s.st_size<=4*1024**3)
        path=_path(fd); require(os.path.isabs(path))
        require(_stamp(os.stat(path,follow_symlinks=False))==_stamp(s))
        return _stamp(s)

    def _config(self):
        """Open each component without symlink traversal; never read config files."""
        a=self._authority
        flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC
        fd=os.open("/",flags)
        try:
            for part in a.docker_config_dir.split("/")[1:]:
                require(part not in ("", ".", ".."))
                nxt=os.open(part,flags,dir_fd=fd); os.close(fd); fd=nxt
            s=os.fstat(fd); d=os.fstat(a.work_dir_fd)
            require((s.st_dev,s.st_ino)!=(d.st_dev,d.st_ino))
            require(stat.S_IMODE(s.st_mode)==0o700 and (s.st_uid,s.st_gid)==(a.uid,a.gid))
            require(os.listdir(fd)==[])
            stamp=_stamp(s)
            if self._config_stamp is None: self._config_stamp=stamp
            else: require(stamp==self._config_stamp)
        finally: os.close(fd)

    def _docker(self):
        self._check(); a=self._authority
        require(os.path.realpath(a.docker_path)==a.docker_path)
        fd=os.open(a.docker_path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:
            before=os.fstat(fd); require(stat.S_ISREG(before.st_mode) and before.st_mode&0o111 and before.st_size<=256*1024**2)
            h=hashlib.sha256()
            while True:
                self._check(); block=os.read(fd,1024**2)
                if not block: break
                h.update(block)
            require(h.hexdigest()==a.docker_sha256 and _stamp(before)==_stamp(os.fstat(fd)))
            require(_stamp(os.stat(a.docker_path,follow_symlinks=False))==_stamp(before))
        finally: os.close(fd)

    def _scratch_file(self):
        self._check(); a=self._authority
        name="parser-"+uuid.uuid4().hex
        fd=os.open(name,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=a.work_dir_fd)
        try:
            self._scratch.append(name)
            s=os.fstat(fd); require(stat.S_ISREG(s.st_mode) and stat.S_IMODE(s.st_mode)==0o600 and s.st_nlink==1 and (s.st_uid,s.st_gid)==(a.uid,a.gid))
            return fd,name,(s.st_dev,s.st_ino)
        except BaseException:
            # Ownership transfers only on return; retain the evidence file.
            os.close(fd)
            raise

    def _capture(self,args,cap,stdin=None,remote=False):
        self._docker(); a=self._authority
        out,oname,oid=self._scratch_file(); err=None
        process=None
        try:
            err,ename,eid=self._scratch_file()
            self._check(); self._config()
            argv=[a.docker_path,"-H",a.socket,"--config",a.docker_config_dir,*args]
            self._active_command=tuple(argv)
            process=_popen(argv,stdin=subprocess.DEVNULL if stdin is None else stdin,
                stdout=out,stderr=err,env={**_ENV,"HOME":a.docker_config_dir},start_new_session=True,close_fds=True)
            self._active_process=process; self._retained_processes.append(process)
            if remote: self._remote_state="UNKNOWN"
            while True:
                # Even terminal processes must pass size/deadline checks.
                self._check()
                require(os.fstat(out).st_size<=cap and os.fstat(err).st_size<=65536)
                rc=process.poll()
                if rc is not None: break
                time.sleep(min(0.02,max(0,a.deadline-time.monotonic())))
            require(rc==0)
            if remote: self._remote_state="TERMINAL"
            self._config(); self._check()
            for fd,name,identity,limit in ((out,oname,oid,cap),(err,ename,eid,65536)):
                s=os.fstat(fd); p=os.stat(name,dir_fd=a.work_dir_fd,follow_symlinks=False)
                require((s.st_dev,s.st_ino)==identity and _stamp(s)==_stamp(p) and stat.S_IMODE(s.st_mode)==0o600
                    and s.st_nlink==1 and (s.st_uid,s.st_gid)==(a.uid,a.gid) and s.st_size<=limit)
                self._used+=s.st_size
            before=_stamp(os.fstat(out)); os.lseek(out,0,os.SEEK_SET)
            chunks=[]; total=0
            while True:
                self._check(); chunk=os.read(out,min(1024**2,cap+1-total))
                if not chunk: break
                total+=len(chunk); require(total<=cap); chunks.append(chunk)
            result=b"".join(chunks)
            require(total==os.fstat(out).st_size and before==_stamp(os.fstat(out)))
            self._active_process=None; self._active_command=None
            return result
        except BaseException:
            if process is not None and remote and self._remote_state!="TERMINAL": self._remote_state="UNKNOWN"
            if process is not None:
                try: _terminate_local(process,a.deadline-time.monotonic())
                except BaseException: pass
            self._failed=True
            raise B3Error("parser operation failed; evidence retained") from None
        finally:
            os.close(out)
            if err is not None: os.close(err)

    def _exec(self,tool,args,stdin=None,cap=1024):
        return self._capture(["exec","--interactive","--user","999:999","--env","LC_ALL=C","--env","LANG=C",self._authority.cid,tool,*args],cap,stdin,True)

    def _inspect(self):
        a=self._authority
        raw=self._capture(["inspect","--type","container",a.cid],1024**2)
        obj=_json(raw); require(type(obj) is list and len(obj)==1)
        projection=inspect_projection(obj[0]); p=_json(projection); _baseline(p,a)
        require(projection==a.expected_inspect)

    def run(self,kind,mode,input_fd):
        acquired=self._lock.acquire(blocking=False)
        if not acquired:
            self._failed=True
            raise B3Error("parser admission failed")
        clone=None
        try:
            self._check()
            require(type(kind) is str and kind in ("central","identity") and type(mode) is str and mode in _MODES)
            require((kind,mode) not in self._slots)
            stamp=self._file(input_fd); identity=stamp[:2]
            if kind in self._inputs: require(self._inputs[kind]==stamp)
            else:
                require(all(s[:2]!=identity for s in self._inputs.values())); self._inputs[kind]=stamp
            self._slots.add((kind,mode))
            clone=os.open(_path(input_fd),os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
            require(self._file(clone)==stamp); os.lseek(clone,0,os.SEEK_SET)
            self._inspect()
            tool=self._exec(_SHA,[_PG])
            require(tool==(self._authority.pg_restore_sha256+"  "+_PG+"\n").encode("ascii"))
            args,cap=_MODES[mode]
            result=self._exec(_PG,list(args),clone,cap)
            require(self._file(input_fd)==stamp and self._file(clone)==stamp)
            self._inspect(); self._check()
            a=self._authority
            proof=_InspectedOutput(mode,len(result),hashlib.sha256(result).hexdigest(),a.cid,a.image_id,a.pg_restore_sha256,
                hashlib.sha256(canonical({"cid":a.cid,"inspect":a.expected_inspect_sha256,"docker":a.docker_sha256,
                    "tool":a.pg_restore_sha256,"started":a.started_at,"deadline":a.deadline,"nonce":a.nonce,
                    "image":a.image_id,"socket":a.socket,"docker_path":a.docker_path,"docker_config_dir":a.docker_config_dir,"uid":a.uid,"gid":a.gid,
                    "work_dir_fd":a.work_dir_fd,"reserved":a.reserved_bytes,"minimum_free":a.minimum_free_bytes})).hexdigest(),True)
            self._outputs[proof]=result
            return proof
        except BaseException:
            self._failed=True
            raise B3Error("parser operation failed; evidence retained") from None
        finally:
            if clone is not None: os.close(clone)
            self._lock.release()

    def read(self,output):
        require(type(output) is _InspectedOutput and output in self._outputs)
        return self._outputs[output]
