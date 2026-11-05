"""B3 Task3B2a: Root-owned preparation prerequisites, no resource execution.

These records express independently supplied Root policy, not archive authority.
No factory, target handle, SQL adapter, Docker invocation, signal, cleanup actor,
or runtime-success stub exists in this subset. Private custody is explicit IO;
policy construction/import is pure. A prerequisite receipt cannot authorize SQL.

3B2b must bind EXACT live VerifiedRestoreInput/its original authority/event and
role union before the first durable claim, then own closed bounded transport,
absence/create/CID/start/metadata state machine and sealed live handle. This
journal deliberately accepts ONLY CLAIMED -> PREPARED. Neither is runtime proof.
Root must retain unknown journals/custody until external disposition. No __del__.
"""
from dataclasses import dataclass, asdict
import hashlib
import math
import os
from pathlib import PurePosixPath
import stat
import threading
import time
from .contracts import B3Error, require, canonical, digest
from .target_contract import TargetSpec, build_target_plan, _hash, _path, _ident, _name

_GIB = 1024**3
_PUBLIC_ENV = (
    "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/lib/postgresql/16/bin",
    "GOSU_VERSION=1.19", "LANG=en_US.utf8", "PG_MAJOR=16",
    "PG_VERSION=16.15-1.pgdg13+2", "PGDATA=/var/lib/postgresql/data")
_TOOL_PATH = "/usr/lib/postgresql/16/bin/"
_MAX_ENV = 16384
_MAX_TOOL = 128*1024**2
# Strong module ownership survives local caller losing a failed journal reference.
# Root explicitly disposes unknown evidence externally; this slice has no release.
_UNKNOWN_JOURNALS = []
# Atomic process-local ownership keyed ONLY by the pinned directory inode/kind.
# Neither alternate FDs/path aliases nor changed policy/input hashes reset it.
# Each owner pins an extra directory FD for the registry lifetime, preventing
# inode reuse after successful custody close. No automatic reset/release exists.
# This cannot survive process death if exclusive record creation never succeeded:
# Root's external once manifest/ledger MUST forbid replay across processes/crash.
_ATTEMPT_OWNERS = {}
_ATTEMPT_LOCK = threading.RLock()

def _redacted(self): return "<"+type(self).__name__+": private Root prerequisites>"
def _integer(value, maximum=2**63-1):
    require(type(value) is int and 0 <= value <= maximum)
def _number(value):
    require(type(value) in (int,float) and math.isfinite(value) and value >= 0)
def _job(restore_id, started, deadline):
    _name(restore_id);_number(started);_number(deadline)
    require(0 < deadline-started <= 1800)
def _job_values(record): return (record._restore_id,record._started_at,record._deadline)

@dataclass(frozen=True, repr=False)
class RootSourceProof:
    """Root's independent source record; not an archive-derived/live capability."""
    _restore_id: str
    _started_at: float
    _deadline: float
    _container_id: str
    _system_id_sha256: str
    _volume_names: tuple
    _admin_name: str
    _record_sha256: str
    __repr__ = _redacted
    def __post_init__(self):
        _job(*_job_values(self));_hash(self._container_id);_hash(self._system_id_sha256)
        _hash(self._record_sha256);_ident(self._admin_name)
        require(type(self._volume_names) is tuple and 0<len(self._volume_names)<=16)
        for name in self._volume_names:_name(name)
        require(len(set(self._volume_names))==len(self._volume_names))

@dataclass(frozen=True, repr=False)
class RootCapacityProof:
    """Root-held reservation assertion, plus independent measured peak receipt.

Not a Docker VM quota/global atomic reservation. _measurement_sha256 binds the
external Root measurement artifact; this module cannot validate its provenance.
Statvfs is actual host-filesystem availability, rechecked by explicit custody IO.
"""
    _restore_id: str
    _started_at: float
    _deadline: float
    _measured_at: float
    _device: int
    _peak_bytes: int
    _reserved_bytes: int
    _measurement_sha256: str
    __repr__ = _redacted
    def __post_init__(self):
        _job(*_job_values(self));_number(self._measured_at)
        require(self._started_at<=self._measured_at<self._deadline)
        _integer(self._device);_integer(self._peak_bytes,64*_GIB)
        _integer(self._reserved_bytes,64*_GIB);_hash(self._measurement_sha256)
        require(self._reserved_bytes>=10*_GIB+self._peak_bytes)

@dataclass(frozen=True, repr=False)
class RootFactoryPolicy:
    """Pure immutable policy. Explicit custody clones FD, never this constructor.

UID/GID defaults are intentionally not implicit: Root supplies 0/0 in its root
stage or explicit local ownership for tests. Neither claims effective host root.
PG tool pins describe in-image tools; checking them requires 3B2b fixed exec.
"""
    _specs: tuple
    _source: RootSourceProof
    _capacity: RootCapacityProof
    _cancellation: threading.Event
    _job_path: str
    _job_fd: int
    _uid: int
    _gid: int
    _docker_sha256: str
    _pg_tools: tuple
    _public_image_env: tuple
    __repr__ = _redacted
    def __post_init__(self):
        require(type(self._specs) is tuple and len(self._specs)==2)
        require(all(type(s) is TargetSpec for s in self._specs))
        for s in self._specs:s.__post_init__()
        a,b=self._specs
        require((a.kind,b.kind)==("central","identity"))
        require(type(self._source) is RootSourceProof and type(self._capacity) is RootCapacityProof)
        self._source.__post_init__();self._capacity.__post_init__()
        for s in self._specs:
            require((s.restore_id,s.started_at,s.deadline)==_job_values(self._source)==_job_values(self._capacity))
            require((s.source_container_id,s.source_system_id_sha256,s.source_volume_names,s.source_admin_name)==(self._source._container_id,self._source._system_id_sha256,self._source._volume_names,self._source._admin_name))
        require(a.restored_role_names==b.restored_role_names==tuple(sorted(a.restored_role_names)))
        for field in ("docker_path","docker_socket","docker_config_path"):
            require(getattr(a,field)==getattr(b,field))
        for field in ("operation_nonce","volume_name","container_name","bootstrap_role","env_file_path","env_file_sha256","expected_env_sha256"):
            require(getattr(a,field)!=getattr(b,field))
        require(type(self._cancellation) is threading.Event)
        _path(self._job_path);_integer(self._job_fd)
        _integer(self._uid,2**32-1);_integer(self._gid,2**32-1);_hash(self._docker_sha256)
        require(type(self._pg_tools) is tuple and len(self._pg_tools)==2)
        for row,name in zip(self._pg_tools,("pg_isready","psql")):
            require(type(row) is tuple and len(row)==3 and row[:2]==(name,_TOOL_PATH+name));_hash(row[2])
        require(type(self._public_image_env) is tuple and self._public_image_env==_PUBLIC_ENV)
        # Constrain private material to direct children of the pinned job dir.
        private=(a.docker_config_path,a.env_file_path,b.env_file_path)
        require(all(str(PurePosixPath(p).parent)==self._job_path for p in private))
        require(len(set(private))==3 and a.docker_path not in private)
    def _projection(self):
        self.__post_init__()
        return dict(specs=[asdict(s) for s in self._specs], source=asdict(self._source),
                    capacity=asdict(self._capacity),job_path=self._job_path,job_fd=self._job_fd,
                    uid=self._uid,gid=self._gid,docker_sha256=self._docker_sha256,
                    pg_tools=self._pg_tools,public_image_env=self._public_image_env)

def _parse_env(raw,spec,public):
    """Bounded, literal env-file format; returned secret data stays private."""
    require(type(raw) is bytes and 0<len(raw)<=_MAX_ENV)
    require(hashlib.sha256(raw).hexdigest()==spec.env_file_sha256)
    try:text=raw.decode("utf-8",errors="strict")
    except UnicodeError:raise B3Error("invalid private Root material") from None
    require(text.endswith("\n") and "\r" not in text and "\x00" not in text)
    lines=text[:-1].split("\n");require(len(lines)==5)
    values={}
    for line in lines:
        require("=" in line);key,value=line.split("=",1)
        require(key not in values and value and len(value)<=4096);values[key]=value
    require(values.keys()=={"POSTGRES_USER","POSTGRES_PASSWORD","POSTGRES_DB","POSTGRES_HOST_AUTH_METHOD","POSTGRES_INITDB_ARGS"})
    require(values["POSTGRES_USER"]==spec.bootstrap_role and values["POSTGRES_DB"]=="postgres" and values["POSTGRES_HOST_AUTH_METHOD"]=="scram-sha-256")
    require(values["POSTGRES_INITDB_ARGS"]=="--encoding=UTF8 --locale=C --auth-host=scram-sha-256")
    require(type(public) is tuple and public==_PUBLIC_ENV)
    require(digest(sorted(public+tuple(lines)))==spec.expected_env_sha256)
    return values

def _stamp(info):
    return (info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid,
            info.st_nlink,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
def _directory(info,policy):
    require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode)==0o700)
    require((info.st_uid,info.st_gid)==(policy._uid,policy._gid))
def _regular(info,policy,tool=False):
    require(stat.S_ISREG(info.st_mode) and info.st_nlink==1)
    require((info.st_uid,info.st_gid)==(policy._uid,policy._gid) if not tool else info.st_uid in (0,policy._uid))
    if tool:require(info.st_mode&0o111 and not info.st_mode&0o022)
    else:require(stat.S_IMODE(info.st_mode)==0o600)

def _open_path(path,directory=False):
    """Open EVERY absolute path component without following a symlink."""
    _path(path);parts=PurePosixPath(path).parts[1:]
    fd=os.open("/",os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for i,part in enumerate(parts):
            flags=os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK
            if i<len(parts)-1 or directory:flags|=os.O_DIRECTORY
            child=os.open(part,flags,dir_fd=fd);os.close(fd);fd=child
        return fd
    except BaseException:
        os.close(fd);raise

class _PrivateJobFiles:
    """Explicit private file custody, never target/SQL authority.

No destructor cleanup. Holds cloned host descriptors only, no child processes.
Before/after IO guards reopen nonsymlink lineage and pin inode/owner/mode. Root
must exclude hostile same-owner writers; these checks are not an OS write lock.
"""
    __repr__ = _redacted
    def __init__(self,policy):
        require(type(policy) is RootFactoryPolicy);policy.__post_init__()
        self._policy=policy;self._frozen=digest(policy._projection());self._event=policy._cancellation
        self._fd=None;self._config=None;self._tool=None;self._closed=False
        self._file_pins={};self._lock=threading.RLock();self._journals=[]
        try:
            self._fd=os.dup(policy._job_fd)
            info=os.fstat(self._fd);_directory(info,policy)
            self._identity=(info.st_dev,info.st_ino)
            self._guard()
            self._config=_open_path(policy._specs[0].docker_config_path,True)
            _directory(os.fstat(self._config),policy)
            self._config_identity=(os.fstat(self._config).st_dev,os.fstat(self._config).st_ino)
            self._tool=_open_path(policy._specs[0].docker_path)
            self._tool_stamp=_stamp(os.fstat(self._tool))
            self.verify()
        except (OSError,ValueError,TypeError,AttributeError,OverflowError):
            self.close();raise B3Error("private Root prerequisite validation failed") from None
    def _guard(self):
        require(not self._closed);p=self._policy
        require(digest(p._projection())==self._frozen and p._cancellation is self._event)
        require((os.geteuid(),os.getegid())==(p._uid,p._gid))
        now=time.monotonic();require(type(now) in (int,float) and math.isfinite(now))
        require(not self._event.is_set() and p._source._started_at<=now<p._source._deadline)
        info=os.fstat(self._fd);_directory(info,p)
        require((info.st_dev,info.st_ino)==self._identity)
        other=_open_path(p._job_path,True)
        try:require((os.fstat(other).st_dev,os.fstat(other).st_ino)==self._identity)
        finally:os.close(other)
        capacity=p._capacity;require(info.st_dev==capacity._device and capacity._measured_at<=now)
        v=os.fstatvfs(self._fd)
        require(type(v.f_bavail) is int and type(v.f_frsize) is int and v.f_bavail>=0 and v.f_frsize>0)
        require(v.f_bavail*v.f_frsize>=capacity._reserved_bytes)
    def _check_config(self):
        p=self._policy;self._guard();info=os.fstat(self._config);_directory(info,p)
        require((info.st_dev,info.st_ino)==self._config_identity and not os.listdir(self._config))
        fd=_open_path(p._specs[0].docker_config_path,True)
        try:require(_stamp(os.fstat(fd))==_stamp(info))
        finally:os.close(fd)
        self._guard()
    def _read(self,fd,maximum):
        self._guard();before=_stamp(os.fstat(fd));require(before[6]<=maximum)
        chunks=[];offset=0
        while offset<before[6]:
            self._guard();chunk=os.pread(fd,min(65536,before[6]-offset),offset)
            require(type(chunk) is bytes and 0<len(chunk)<=65536);chunks.append(chunk);offset+=len(chunk)
            self._guard()
        require(_stamp(os.fstat(fd))==before);self._guard();return b"".join(chunks)
    def _check_tool(self):
        p=self._policy;self._guard();info=os.fstat(self._tool);_regular(info,p,True)
        require(_stamp(info)==self._tool_stamp)
        fd=_open_path(p._specs[0].docker_path)
        try:require(_stamp(os.fstat(fd))==self._tool_stamp)
        finally:os.close(fd)
        require(hashlib.sha256(self._read(self._tool,_MAX_TOOL)).hexdigest()==p._docker_sha256)
        self._guard()
    def _check_env(self,s):
        self._guard();fd=_open_path(s.env_file_path)
        try:
            info=os.fstat(fd);_regular(info,self._policy);before=_stamp(info)
            if s.kind in self._file_pins:require(self._file_pins[s.kind]==before)
            _parse_env(self._read(fd,_MAX_ENV),s,self._policy._public_image_env)
            after=_open_path(s.env_file_path)
            try:require(_stamp(os.fstat(after))==before)
            finally:os.close(after)
            self._file_pins[s.kind]=before
        finally:os.close(fd)
        self._guard()
    def verify(self):
        try:
            with self._lock:
                self._guard();self._check_config();self._check_tool()
                for s in self._policy._specs:self._check_env(s)
                self._guard()
        except (OSError,TypeError,ValueError,AttributeError,OverflowError):
            raise B3Error("private Root prerequisite validation failed") from None
    def safe_receipt(self):
        self.verify()
        return dict(status="target_prerequisites_validated",policy_sha256=self._frozen,
                    original_deadline=self._policy._source._deadline,
                    capacity_measurement_type="host_statvfs_plus_root_reserved_peak",
                    actual_resources_observed=False,target_instance_bound=False,
                    isolated_restore_verified=False,production=False,provider=False,timer=False)
    def close(self):
        # Serialize with attempts so custody cannot close mid-claim/append.
        with self._lock:
            if any(j._unknown for j in self._journals):return
            if self._closed:return
            self._closed=True
            for name in ("_tool","_config","_fd"):
                fd=getattr(self,name,None)
                if fd is not None:os.close(fd);setattr(self,name,None)

class _TargetLedger:
    """Private append-only preparation journal, NOT evidence of runtime states.

Process-local inode/kind ownership arbitrates all concurrent claims; exclusive
first records also reject record-present collisions across processes. Each bounded
canonical record is an exclusive 0600 file, fsynced before directory fsync. Any
IO uncertainty is sticky and retains descriptors and custody, with no retry or
rewrite. Restart requires Root external inspection/disposition, never replay.
"""
    __repr__ = _redacted
    def __init__(self,files):
        require(type(files) is _PrivateJobFiles)
        self._files=files;self._states={};self._records={};self._retained_fds=[]
        self._unknown=False;self._job_pin_fd=None;self._lock=threading.RLock();files._journals.append(self)
    def _retain_unknown(self,kind):
        # The original owner already has a strong module registry anchor BEFORE
        # any attempt IO. Secondary list failure must not mask the original exit.
        self._unknown=True;self._states[kind]="UNKNOWN"
        try:_UNKNOWN_JOURNALS.append(self)
        except BaseException:pass
    def claim(self,kind,verified_safe_input_sha256):
        with self._lock,self._files._lock:
            require(not self._unknown and type(kind) is str and kind in ("central","identity"))
            _hash(verified_safe_input_sha256);require(kind not in self._states)
            key=(*self._files._identity,kind)
            try:
                with _ATTEMPT_LOCK:
                    require(key not in _ATTEMPT_OWNERS)
                    _ATTEMPT_OWNERS[key]=self # strong ownership before any attempt IO
                self._states[kind]="ATTEMPTED"
                if self._job_pin_fd is None:self._job_pin_fd=os.dup(self._files._fd)
                self._files.verify()
                self._write(kind,"CLAIMED",verified_safe_input_sha256)
            except BaseException as error:
                # A rejected contender never owns/poisons the winner's attempt.
                # Catch also the boundary between registration and local state.
                if _ATTEMPT_OWNERS.get(key) is not self:raise
                self._retain_unknown(kind)
                if not isinstance(error,Exception):raise
                raise B3Error("Root preparation claim unknown; retain evidence") from None
    def append(self,kind,state):
        with self._lock,self._files._lock:
            require(not self._unknown and type(kind) is str and type(state) is str)
            require(self._states.get(kind)=="CLAIMED" and state=="PREPARED")
            try:
                self._files.verify();self._verify_records(kind)
                self._write(kind,state,self._records[kind][0][3])
            except BaseException as error:
                self._retain_unknown(kind)
                if not isinstance(error,Exception):raise
                raise B3Error("Root preparation journal unknown; retain evidence") from None
    def _verify_records(self,kind):
        for name,stamp,sha,_ in self._records.get(kind,()):
            self._files._guard();fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=self._files._fd)
            try:
                _regular(os.fstat(fd),self._files._policy)
                require(_stamp(os.fstat(fd))==stamp)
                require(hashlib.sha256(self._files._read(fd,8192)).hexdigest()==sha)
            finally:os.close(fd)
            self._files._guard()
    def _write(self,kind,state,input_hash):
        f=self._files;p=f._policy;s=p._specs[("central","identity").index(kind)]
        prior=self._records.get(kind,[]);sequence=len(prior)
        name="b3-"+kind+"-"+str(sequence).zfill(3)+".json"
        plan=build_target_plan(s)
        row=dict(format="b3-root-preparation-v1",kind=kind,sequence=sequence,state=state,
                 restore_id=s.restore_id,started_at=s.started_at,deadline=s.deadline,
                 policy_sha256=f._frozen,spec_sha256=digest(asdict(s)),
                 plan_sha256=digest([plan.volume_argv,plan.container_argv]),
                 env_file_sha256=s.env_file_sha256,docker_sha256=p._docker_sha256,
                 source_record_sha256=p._source._record_sha256,
                 verified_safe_input_sha256=input_hash,
                 previous_sha256=prior[-1][2] if prior else None)
        raw=canonical(row);require(len(raw)<=8192);f._guard()
        fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=f._fd)
        self._retained_fds.append(fd)
        _regular(os.fstat(fd),p);f._guard()
        offset=0
        while offset<len(raw):
            f._guard();written=os.write(fd,raw[offset:]);require(type(written) is int and written>0)
            offset+=written;f._guard()
        os.fsync(fd);f._guard();os.fsync(f._fd);f._guard()
        stamp=_stamp(os.fstat(fd));sha=hashlib.sha256(raw).hexdigest()
        self._records.setdefault(kind,[]).append((name,stamp,sha,input_hash))
        self._verify_records(kind)
        self._states[kind]=state
        os.close(fd);self._retained_fds.remove(fd)
