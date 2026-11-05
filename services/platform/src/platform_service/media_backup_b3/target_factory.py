"""Owned B3 target actor. Construction is pure; create is explicit actual IO.

Only fixed Docker commands and a readonly bootstrap query exist. No restore or
arbitrary SQL API exists. Private Popen seam is for source tests only. Readiness
uses strict pg_isready terminal output AND same-CID/no-ExecIDs inspection before
another observation. CLI ambiguity is sticky UNKNOWN, never retry permission.

Output growth and clock/capacity checks are cooperative, not kernel quotas. Root
must supervise hung native/daemon calls and freeze an external once manifest;
process-local retention cannot prove no replay after a pre-record crash. Root
alone disposes retained resources. No destructor, stop/remove/restart or signals.
Private seals are API discipline, not protection from hostile Python code.
"""
from dataclasses import dataclass
import hashlib
import json
import os
import re
import subprocess
import threading
import time
from .contracts import B3Error, require, canonical, digest
from .dump_preflight import VerifiedRestoreInput
from .target_lifecycle import (RootFactoryPolicy, _PrivateJobFiles, _TargetLedger,
    _hash, _job, _regular, _stamp, _parse_env, _open_path, _redacted)
from .target_contract import (IMAGE_ID, build_target_plan, validate_volume_observation,
    validate_created_container, validate_running_container, bind_target_identity,
    verify_distinct_pair)

_popen = subprocess.Popen
_SEAL = object()
_OWNED = []  # Strong original input/custody/process/resource anchors, no GC cleanup.
_RUNTIME_OWNERS = {}
_JOB_OWNERS = {}  # Original device/inode -> immutable factory/input owner; never released.
_RUNTIME_LOCK = threading.RLock()
_STATES = ("CLAIMED", "ABSENT", "VOLUME_CREATED", "CID_FROZEN", "START_CLAIMED",
           "RUNNING", "METADATA_BOUND", "READY")
_OPTIONS = "-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000 -c idle_in_transaction_session_timeout=5000"
_SQL = """SELECT json_build_object(
'system_identifier',(SELECT system_identifier::text FROM pg_control_system()),
'server_version_num',current_setting('server_version_num'),
'fsync',current_setting('fsync'),'full_page_writes',current_setting('full_page_writes'),
'synchronous_commit',current_setting('synchronous_commit'),
'password_encryption',current_setting('password_encryption'),
'max_connections',current_setting('max_connections'),
'database',current_database(),'user',current_user,
'public_relations',(SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'),
'client_addr',inet_client_addr()::text,
'hba',(SELECT json_agg(json_build_object('type',type,'database',database,'user',user_name,
'address',address,'netmask',netmask,'auth_method',auth_method,'error',error) ORDER BY rule_number)
FROM pg_hba_file_rules WHERE type LIKE 'host%' OR error IS NOT NULL))::text;"""

@dataclass(frozen=True, repr=False)
class RuntimeToolPolicy:
    """Independent Root pins missing from frozen two-PG-tool prerequisites."""
    restore_id: str
    started_at: float
    deadline: float
    image_id: str
    tools: tuple
    __repr__ = _redacted
    def __post_init__(self):
        _job(self.restore_id,self.started_at,self.deadline)
        require(self.image_id==IMAGE_ID and type(self.image_id) is str)
        require(type(self.tools) is tuple and len(self.tools)==2)
        for row,name in zip(self.tools,("cat","sha256sum")):
            require(type(row) is tuple and len(row)==3 and row[:2]==(name,"/usr/bin/"+name));_hash(row[2])


def _json(raw):
    def pairs(items):
        value={}
        for k,v in items:require(k not in value);value[k]=v
        return value
    def bad(_):raise B3Error("invalid target observation")
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=bad)


def _oom_default_comparison(raw):
    """Compare fully validated observations, preserving their original records.

    Only the already accepted OOM-killer-enabled default has two spellings in
    this daemon's created/running observations. Every other byte stays exact.
    """
    host=raw["HostConfig"]
    require(host["OomKillDisable"] is None or host["OomKillDisable"] is False)
    return dict(raw,HostConfig=dict(host,OomKillDisable=False))


def _binding(policy,verified,authority=None):
    require(type(verified) is VerifiedRestoreInput)
    verified.verify();a=verified._stage._authority
    require(a is verified._stage_authority and (authority is None or a is authority))
    a.check();require(a.cancellation is policy._cancellation)
    require((a.restore_id,a.started_at,a.deadline)==
        (policy._source._restore_id,policy._source._started_at,policy._source._deadline))
    require(tuple(k for k,_ in a.contexts)==("central","identity"))
    roles=tuple(sorted(set().union(*(set(c.role_names) for _,c in a.contexts))))
    for spec in policy._specs:require(spec.restored_role_names==roles)
    return a


class _Attempt:
    __repr__ = _redacted
    def __init__(self,factory,verified,spec):
        self._factory=factory;self._verified=verified;self._authority=verified._stage._authority
        self._stage=verified._stage;self._input_pin=digest(verified.safe_receipt)
        self._spec=spec;self._files=None;self._ledger=None;self._unknown=False
        self._processes=[];self._outputs=[];self._records=[];self._retained_fds=[]
        self._cid=None;self._volume=None;self._created=None;self._running=None;self._identity=None
        self._sequence=0;self._state=None;self._closed=False;self._job_pin=None
    def _guard(self):
        require(not self._unknown and not self._closed)
        # A failed paired actor forbids transport before any fresh observation.
        require(all(not peer._unknown for peer in self._factory._attempts.values()))
        self._factory._check_policy()
        _binding(self._factory._policy,self._verified,self._authority)
        require(self._verified._stage is self._stage and digest(self._verified.safe_receipt)==self._input_pin)
        if self._files is not None:self._files.verify()
    def _fail(self):
        self._unknown=True
        if self._ledger is not None:self._ledger._retain_unknown(self._spec.kind)
    def _records_check(self):
        f=self._files
        if self._ledger is not None:self._ledger._verify_records(self._spec.kind)
        for name,stamp,sha in self._records:
            fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=f._fd)
            try:
                _regular(os.fstat(fd),f._policy);require(_stamp(os.fstat(fd))==stamp)
                require(hashlib.sha256(f._read(fd,8192)).hexdigest()==sha)
            finally:os.close(fd)
    def _advance(self,state):
        self._guard();self._records_check()
        require(self._sequence<len(_STATES) and state==_STATES[self._sequence])
        row=dict(format="b3-target-runtime-v1",sequence=self._sequence,state=state,
            policy_sha256=self._factory._pin,tools_sha256=self._factory._tool_pin,
            input_sha256=self._input_pin,
            cid=self._cid,previous_sha256=self._records[-1][2] if self._records else None,
            volume_sha256=digest(self._volume) if self._volume is not None else None,
            created_sha256=digest(self._created) if self._created is not None else None,
            running_sha256=digest(self._running) if self._running is not None else None,
            system_id_sha256=self._identity.system_id_sha256 if self._identity is not None else None,
            transcript_sha256=digest([dict(operation=e["operation"],argv_sha256=digest(e["argv"]),
                code=e.get("terminal_code"),stdout_sha256=e.get("stdout_sha256"),
                stderr_sha256=e.get("stderr_sha256")) for e in self._outputs]))
        raw=canonical(row);require(len(raw)<=8192)
        name="runtime-"+self._spec.kind+"-"+str(self._sequence).zfill(3)+".json"
        fd=os.open(name,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=self._files._fd)
        self._retained_fds.append(fd);_regular(os.fstat(fd),self._files._policy)
        offset=0
        while offset<len(raw):
            self._guard();n=os.write(fd,raw[offset:]);require(type(n) is int and n>0);offset+=n
        os.fsync(fd);self._guard();os.fsync(self._files._fd);self._guard()
        self._records.append((name,_stamp(os.fstat(fd)),hashlib.sha256(raw).hexdigest()))
        self._records_check();self._state=state;self._sequence+=1
    def _command(self,operation):
        s=self._spec;p=self._factory._policy
        prefix=(s.docker_path,"-H",s.docker_socket,"--config",s.docker_config_path)
        plan=build_target_plan(s)
        env=dict(PATH="/usr/bin:/bin",LANG="C",LC_ALL="C",HOME=p._job_path)
        if operation=="volume_absence" or operation=="volume_inspect":return prefix+("volume","inspect",s.volume_name),env,1024**2
        if operation=="container_absence":return prefix+("inspect","--type","container",s.container_name),env,1024**2
        if operation=="volume_create":return plan.volume_argv,env,65536
        if operation=="container_create":return plan.container_argv,env,65536
        _hash(self._cid)
        if operation=="inspect":return prefix+("inspect","--type","container",self._cid),env,1024**2
        if operation=="start":return prefix+("start",self._cid),env,65536
        base=prefix+("exec","--user","999:999","--env","LC_ALL=C","--env","LANG=C")
        if operation=="tools":
            paths=tuple(row[1] for row in p._pg_tools+self._factory._tools.tools)
            return base+(self._cid,"/usr/bin/sha256sum")+paths,env,65536
        if operation=="pid1":return base+(self._cid,"/usr/bin/cat","/proc/1/status"),env,65536
        if operation=="ready":return base+(self._cid,p._pg_tools[0][1],"-h","127.0.0.1","-p","5432","-U",s.bootstrap_role,"-d","postgres","-t","1"),env,65536
        require(operation in ("metadata","deny_password"))
        fd=_open_path(s.env_file_path)
        try:values=_parse_env(self._files._read(fd,16384),s,p._public_image_env)
        finally:os.close(fd)
        env["PGPASSWORD"]=values["POSTGRES_PASSWORD"]+("-b3-denied" if operation=="deny_password" else "")
        return base+("--env","PGPASSWORD","--env","PGSSLMODE=disable","--env","PGGSSENCMODE=disable","--env","PGCONNECT_TIMEOUT=2","--env","PGPASSFILE=/dev/null","--env","PGOPTIONS="+_OPTIONS,self._cid,p._pg_tools[1][1],"-X","-w","-A","-t","-v","ON_ERROR_STOP=1","-h","127.0.0.1","-p","5432","-U",s.bootstrap_role,"-d","postgres","-c",_SQL),env,65536
    def _run(self,operation):
        # No caller-supplied argv/SQL/environment enters this transport.
        self._guard();self._records_check();argv,env,cap=self._command(operation)
        streams=[];names=[]
        for suffix in ("out","err"):
            name="transport-"+self._spec.kind+"-"+str(len(self._outputs)).zfill(4)+"-"+suffix
            fd=os.open(name,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=self._files._fd)
            self._retained_fds.append(fd);_regular(os.fstat(fd),self._files._policy)
            streams.append(fd);names.append(name)
        entry=dict(operation=operation,argv=argv,streams=tuple(streams),process=None)
        self._outputs.append(entry);self._guard()
        # Anchor the original Popen object BEFORE its constructor can spawn or
        # raise an interruption. Even a partially initialized child stays owned.
        child=object.__new__(_popen)
        entry["process"]=child;self._processes.append(child)
        _popen.__init__(child,argv,stdin=subprocess.DEVNULL,stdout=streams[0],stderr=streams[1],
                        env=env,close_fds=True,cwd=self._factory._policy._job_path)
        while True:
            self._guard()
            for name,fd in zip(names,streams):
                _regular(os.fstat(fd),self._files._policy)
                named=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=self._files._fd)
                try:require(_stamp(os.fstat(named))==_stamp(os.fstat(fd)))
                finally:os.close(named)
            require(os.fstat(streams[0]).st_size<=cap and os.fstat(streams[1]).st_size<=65536)
            code=child.poll()
            if code is not None:break
            time.sleep(.02)
        require(type(code) is int)
        self._guard()
        out=self._files._read(streams[0],cap);err=self._files._read(streams[1],65536)
        entry["terminal_code"]=code;entry["stdout_sha256"]=hashlib.sha256(out).hexdigest()
        entry["stderr_sha256"]=hashlib.sha256(err).hexdigest()
        return code,out,err
    def _ok(self,operation):
        code,out,err=self._run(operation);require(code==0 and err==b"");return out
    def _inspect(self,operation):
        raw=_json(self._ok(operation));require(type(raw) is list and len(raw)==1 and type(raw[0]) is dict)
        return raw[0]
    def _absent(self,operation):
        code,out,err=self._run(operation);s=self._spec
        expected=("Error response from daemon: get "+s.volume_name+": no such volume\n" if operation=="volume_absence" else "Error response from daemon: No such container: "+s.container_name+"\n").encode()
        require(code==1 and out==b"[]\n" and err==expected)
    def _observe(self,created=False):
        volume=self._inspect("volume_inspect");vo=validate_volume_observation(self._spec,volume)
        if self._volume is not None:require(vo==validate_volume_observation(self._spec,self._volume))
        raw=self._inspect("inspect");require(raw["Id"]==self._cid)
        validator=validate_created_container if created else validate_running_container
        validator(self._spec,raw,volume)
        if self._created is not None:
            validate_created_container(self._spec,self._created,self._volume)
            current,original=_oom_default_comparison(raw),_oom_default_comparison(self._created)
            for key in ("Id","Name","Image","Created","Config","HostConfig","Mounts"):
                require(canonical(current[key])==canonical(original[key]))
        if self._running is not None:
            validate_running_container(self._spec,self._running,self._volume)
            require(canonical(_oom_default_comparison(raw))==canonical(_oom_default_comparison(self._running)))
        return raw,volume
    def _tools_check(self):
        pins=self._factory._policy._pg_tools+self._factory._tools.tools
        expected="".join(row[2]+"  "+row[1]+"\n" for row in pins).encode()
        require(self._ok("tools")==expected);self._observe()
    def _pid(self):
        raw=self._ok("pid1");fields={}
        for line in raw.decode("ascii").splitlines():
            require(":" in line);key,value=line.split(":",1);require(key not in fields);fields[key]=value.split()
        require(fields.get("Name")==["postgres"])
        require(fields.get("Uid")==["999"]*4 and fields.get("Gid")==["999"]*4)
        require(fields.get("CapEff")==["0000000000000000"] and fields.get("NoNewPrivs")==["1"])
        self._observe()
    def _deny_password(self):
        code,out,err=self._run("deny_password")
        expected=('psql: error: connection to server at "127.0.0.1", port 5432 failed: FATAL:  password authentication failed for user "'+self._spec.bootstrap_role+'"\n').encode()
        require(code==2 and out==b"" and err==expected)
        self._observe()  # no active remote exec, same original start/CID
    def _metadata(self):
        row=_json(self._ok("metadata"));s=self._spec
        expected=dict(server_version_num="160015",fsync="on",full_page_writes="on",synchronous_commit="on",password_encryption="scram-sha-256",max_connections="100",database="postgres",user=s.bootstrap_role,public_relations=0,client_addr="127.0.0.1/32")
        require(type(row) is dict and set(row)==set(expected)|{"hba","system_identifier"})
        for key,value in expected.items():require(type(row[key]) is type(value) and row[key]==value)
        rules=row["hba"];require(type(rules) is list and 0<len(rules)<=64);matches=False
        for rule in rules:
            require(type(rule) is dict and set(rule)=={"type","database","user","address","netmask","auth_method","error"})
            require(rule["type"] in ("host","hostssl","hostnossl") and rule["auth_method"]=="scram-sha-256" and rule["error"] is None)
            for key in ("database","user"):require(type(rule[key]) is list and all(type(v) is str for v in rule[key]))
            if rule["type"] in ("host","hostnossl") and rule["address"] in ("127.0.0.1","0.0.0.0","all"):
                require((rule["address"],rule["netmask"]) in (("127.0.0.1","255.255.255.255"),("0.0.0.0","0.0.0.0"),("all",None)))
                if ("all" in rule["database"] or "postgres" in rule["database"]) and ("all" in rule["user"] or s.bootstrap_role in rule["user"]):matches=True
        require(matches and type(row["system_identifier"]) is str)
        raw,volume=self._observe()
        identity=bind_target_identity(s,raw,volume,row["system_identifier"].encode("ascii"))
        if self._identity is not None:
            original=self._identity;original.__post_init__()
            require((identity.spec,identity.container_id,identity.system_id_sha256,identity.volume_observation)==
                    (original.spec,original.container_id,original.system_id_sha256,original.volume_observation))
            require(canonical(_oom_default_comparison(_json(identity.container_observation.canonical_projection)))==
                    canonical(_oom_default_comparison(_json(original.container_observation.canonical_projection))))
        return identity
    def _create(self):
        self._guard()
        # Anchor partially initialized custody even if its constructor is interrupted.
        self._files=_PrivateJobFiles.__new__(_PrivateJobFiles)
        _PrivateJobFiles.__init__(self._files,self._factory._policy)
        self._ledger=_TargetLedger(self._files)
        self._ledger.claim(self._spec.kind,digest(self._verified.safe_receipt))
        self._ledger.append(self._spec.kind,"PREPARED");self._advance("CLAIMED")
        self._absent("volume_absence");self._absent("container_absence");self._advance("ABSENT")
        require(self._ok("volume_create")== (self._spec.volume_name+"\n").encode())
        self._volume=self._inspect("volume_inspect");validate_volume_observation(self._spec,self._volume)
        self._advance("VOLUME_CREATED")
        cid=self._ok("container_create");require(re.fullmatch(rb"[a-f0-9]{64}\n",cid) is not None)
        self._cid=cid[:-1].decode("ascii");require(self._cid!=self._spec.source_container_id)
        self._advance("CID_FROZEN")
        self._created,_=self._observe(created=True)
        self._advance("START_CLAIMED")
        require(self._ok("start")== (self._cid+"\n").encode())
        self._running,_=self._observe();self._advance("RUNNING")
        self._tools_check()
        while True:
            code,out,err=self._run("ready")
            require(type(code) is int and code in (0,1,2) and err==b"")
            expected={0:b"127.0.0.1:5432 - accepting connections\n",1:b"127.0.0.1:5432 - rejecting connections\n",2:b"127.0.0.1:5432 - no response\n"}
            require(out==expected[code]);self._observe()
            if code==0:break
            self._guard();time.sleep(.05)
        self._pid();self._deny_password();self._identity=self._metadata();self._factory._pair(self)
        self._advance("METADATA_BOUND");self._advance("READY")
        return TargetHandle(_SEAL,self)
    def _verify(self):
        self._guard();require(self._state=="READY");self._records_check()
        self._observe();self._tools_check();self._pid();self._deny_password();self._metadata();self._factory._pair(self)


class RootTargetFactory:
    __repr__ = _redacted
    def __init__(self,policy,runtime_tools):
        require(type(policy) is RootFactoryPolicy and type(runtime_tools) is RuntimeToolPolicy)
        self._policy=policy;self._tools=runtime_tools;self._event=policy._cancellation
        policy.__post_init__();runtime_tools.__post_init__()
        self._pin=digest(policy._projection());self._tool_pin=digest(runtime_tools.__dict__)
        self._attempts={};self._lock=threading.RLock();self._closed=False;self._check_policy()
    def _check_policy(self):
        require(not self._closed);p=self._policy;t=self._tools
        t.__post_init__()
        require(digest(p._projection())==self._pin and digest(t.__dict__)==self._tool_pin and p._cancellation is self._event)
        require((t.restore_id,t.started_at,t.deadline,t.image_id)==(p._source._restore_id,p._source._started_at,p._source._deadline,p._specs[0].image_id))
    def _pair(self,attempt):
        peer=self._attempts.get("identity" if attempt._spec.kind=="central" else "central")
        if peer is not None:
            try:
                require(not peer._unknown)
                if peer._identity is not None:
                    peer._guard();peer._records_check();peer._observe()
                    peer._tools_check();peer._pid();peer._deny_password();peer._metadata()
                    identities=sorted((attempt._identity,peer._identity),key=lambda x:x.spec.kind)
                    verify_distinct_pair(*identities)
            except BaseException:
                # Under the original factory lock: retain the actual transport
                # owner before the current actor's outer handler runs. Never
                # replace an interruption or release original child/FD custody.
                peer._fail()
                raise
    def create(self,verified_input,kind):
        with self._lock:
            self._check_policy();require(type(kind) is str and kind in ("central","identity") and kind not in self._attempts)
            # Read only the supplied FD identity before rejecting a duplicate
            # owner; do not open custody, re-read input or run resource commands.
            info=os.fstat(self._policy._job_fd);job=(info.st_dev,info.st_ino)
            with _RUNTIME_LOCK:
                owner=_JOB_OWNERS.get(job)
                require(owner is None or (owner[0] is self and owner[1] is verified_input))
            _binding(self._policy,verified_input)
            if self._attempts:require(all(a._verified is verified_input and not a._unknown for a in self._attempts.values()))
            s=self._policy._specs[("central","identity").index(kind)]
            a=_Attempt(self,verified_input,s)
            _OWNED.append(a);self._attempts[kind]=a
            try:
                info=os.fstat(self._policy._job_fd);job=(info.st_dev,info.st_ino);key=(*job,kind)
                with _RUNTIME_LOCK:
                    # Claim the WHOLE original job before custody/resource IO.
                    # Alias FDs, changed hashes and opposite creation order do
                    # not permit another factory to split pair coordination.
                    owner=_JOB_OWNERS.get(job)
                    require(owner is None or (owner[0] is self and owner[1] is verified_input))
                    require(key not in _RUNTIME_OWNERS)
                    if owner is None:_JOB_OWNERS[job]=(self,verified_input)
                    _RUNTIME_OWNERS[key]=a
                a._job_pin=os.dup(self._policy._job_fd)
                return a._create()
            except BaseException as error:
                a._fail()
                if not isinstance(error,Exception):raise
                raise B3Error("target lifecycle unknown; retain original evidence") from None
    def close(self):
        # All resource-owning attempts stay anchored, even successful/closed ones.
        # Host custody is retained for explicit Root disposition with the resources.
        with self._lock:
            if any(a._unknown for a in self._attempts.values()):return
            self._closed=True


class TargetHandle:
    __repr__ = _redacted
    def __init__(self,seal=None,attempt=None):
        if seal is not _SEAL:raise TypeError("owned target handle")
        require(type(attempt) is _Attempt and attempt._state=="READY" and not attempt._unknown)
        self._attempt=attempt;self._closed=False
    def __reduce_ex__(self,protocol):raise TypeError("owned target handle")
    def verify(self):
        a=self._attempt
        with a._factory._lock:
            require(not self._closed)
            try:a._verify()
            except BaseException as error:
                a._fail()
                if not isinstance(error,Exception):raise
                raise B3Error("target verification unknown; retain original evidence") from None
    def safe_receipt(self):
        self.verify();a=self._attempt
        return dict(status="target_instance_bound",identity_sha256=digest(a._identity.safe_summary()),
            runtime_journal_sha256=a._records[-1][2],policy_sha256=a._factory._pin,
            original_deadline=a._spec.deadline,readiness_observed=True,tcp_scram_hba_observed=True,
            durability_observed=True,wrong_bootstrap_password_denied=True,pid1_identity_observed=True,docker_network_none_configured=True,
            kernel_network_isolation_proven=False,isolated_restore_verified=False,
            production=False,provider=False,timer=False)
    def close(self):self._closed=True  # never stop/remove or discard ownership
