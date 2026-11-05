"""Original producer custody and closed bounded ATTEST host execution.

Pins are DATA; only original producer objects supply custody.
Strong registries and burned files never reset, including after host-only close.
Same-owner hostile Python mutation/writers remain outside the ownership model.
"""
from dataclasses import dataclass, fields, field
from contextlib import contextmanager
import hashlib
import copy
import math
import os
from pathlib import PurePosixPath
import re
import stat
import threading
import time
import subprocess
import selectors

from . import execution_io as _transport, execution_baseline as _baseline
from . import execution_contract as _contract

from .contracts import B3Error, require, canonical, digest
from .dump_preflight import VerifiedRestoreInput
from .target_factory import RootTargetFactory, TargetHandle, _binding
from .target_contract import IMAGE_ID
from .target_lifecycle import _directory, _regular, _stamp

_popen = subprocess.Popen
_pipe = os.pipe
_read = os.read
_set_blocking = os.set_blocking
_selector = selectors.DefaultSelector
_monotonic = time.monotonic
_open = os.open
_dup = os.dup
_write = os.write
_fsync = os.fsync
_close = os.close
_REGISTRY_LOCK = threading.RLock()
_OWNERS = {}  # job inode -> original provisional/complete owner, never removed
_FACTORIES = {}  # factory identity -> same strong owner
_KINDS = ("central", "identity")


def _redacted(self):
    return "<" + type(self).__name__ + ": private execution custody>"


def _hash(value):
    require(type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value, re.ASCII) is not None)


def _image(value):
    require(type(value) is str and value.startswith("sha256:")); _hash(value[7:])


def _data(value):
    """Closed policy projection, validating exact types before any comparison."""
    if type(value) in (WorkerPins, TargetProbePins, ExecutionLimits, RootExecutionPolicy):
        value.__post_init__()
        return {f.name: _data(getattr(value, f.name)) for f in fields(value)}
    if type(value) is tuple:
        return tuple(_data(v) for v in value)
    require(type(value) in (str, int))
    return value


@dataclass(frozen=True, repr=False)
class WorkerPins:
    image_id: str
    architecture: str
    artifact_sha256: str
    dependency_manifest_sha256: str
    entrypoint_sha256: str
    observer_sha256: str
    pg_restore_sha256: str
    image_inspect_sha256: str
    public_env: tuple
    __repr__ = _redacted

    def __post_init__(self):
        _image(self.image_id)
        require(type(self.architecture) is str and self.architecture in ("arm64", "amd64"))
        for name in ("artifact_sha256", "dependency_manifest_sha256", "entrypoint_sha256",
                     "observer_sha256", "pg_restore_sha256", "image_inspect_sha256"):
            _hash(getattr(self, name))
        require(type(self.public_env) is tuple and len(self.public_env) == 5)
        for item, expected in zip(self.public_env, ("PATH", "LANG", "GPG_KEY", "PYTHON_VERSION", "PYTHON_SHA256")):
            require(type(item) is str and len(item) <= 4128 and "=" in item)
            require(all(32 <= ord(c) < 127 for c in item))
            key, value = item.split("=", 1)
            require(key == expected and 0 < len(value) <= 4096)
            if key == "PATH":
                for path in value.split(":"):
                    require(path.startswith("/") and all(p not in ("", ".", "..") for p in path[1:].split("/")))
            elif key == "LANG": require(value in ("C", "C.UTF-8", "en_US.utf8"))
            elif key == "GPG_KEY": require(re.fullmatch(r"[A-F0-9]{40}", value, re.ASCII) is not None)
            elif key == "PYTHON_VERSION": require(re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value, re.ASCII) is not None)
            else: _hash(value)


@dataclass(frozen=True, repr=False)
class TargetProbePins:
    image_id: str
    image_manifest_sha256: str
    image_config_sha256: str
    tools: tuple
    dependency_closure_sha256: str
    probe_schema_sha256: str
    __repr__ = _redacted

    def __post_init__(self):
        require(type(self.image_id) is str and self.image_id == IMAGE_ID)
        for name in ("image_manifest_sha256", "image_config_sha256", "dependency_closure_sha256", "probe_schema_sha256"):
            _hash(getattr(self, name))
        require(type(self.tools) is tuple and len(self.tools) == 4)
        for row, role in zip(self.tools, ("sha256sum", "stat", "readlink", "cat")):
            require(type(row) is tuple and len(row) == 3)
            require(type(row[0]) is str and type(row[1]) is str)
            require(row[:2] == (role, "/usr/bin/" + role)); _hash(row[2])


@dataclass(frozen=True, repr=False)
class ExecutionLimits:
    cpu_nanos: int
    memory_bytes: int
    memory_swap_bytes: int
    pids_limit: int
    tmpfs_bytes: int
    envelope_limit: int
    private_result_limit: int
    diagnostic_limit: int
    __repr__ = _redacted

    def __post_init__(self):
        for f, maximum in zip(fields(self), (2*10**9, 2*1024**3, 2*1024**3, 128,
                                             256*1024**2, 65536, 1024**2, 65536)):
            value = getattr(self, f.name)
            require(type(value) is int and 0 < value <= maximum)
        require(self.memory_swap_bytes == self.memory_bytes)


@dataclass(frozen=True, repr=False)
class RootExecutionPolicy:
    worker: WorkerPins
    target_probe: TargetProbePins
    limits: ExecutionLimits
    __repr__ = _redacted

    def __post_init__(self):
        for value, expected in ((self.worker, WorkerPins), (self.target_probe, TargetProbePins),
                                (self.limits, ExecutionLimits)):
            require(type(value) is expected); value.__post_init__()


@dataclass(repr=False)
class _FDSlot:
    operation: str
    fd: object = None
    stamp: object = None
    closed: bool = False
    close_attempted: bool = False


@dataclass(repr=False)
class _TargetSlot:
    kind: str
    original_attempt: object = None
    original_handle: object = None
    state: str = "PRODUCING"
    identity: object = None
    identity_pin: object = None
    cid: object = None
    running_pin: object = None
    ready_records: object = None
    ready_records_pin: object = None


@dataclass(repr=False)
class _ExecutionAttempt:
    kind: str
    target_slot: _TargetSlot
    worker_slot: object = None
    state: str = "BOUND"
    unknown: bool = False
    operations: list = field(default_factory=list)
    main_start_anchor: object = None
    acceptance_deadline: object = None
    request_bytes: object = None
    baseline: object = None
    response: object = None
    execution_policy: object = None
    final_record: object = None
    baseline_hash: object = None


@dataclass(repr=False)
class _WorkerSlot:
    name: str
    labels: dict
    cid: object = None
    running: object = None
    immutable: object = None
    main: object = None
    terminal: bool = False


@dataclass(repr=False)
class _Record:
    name: str
    slot: _FDSlot
    raw: bytes
    sha256: str
    previous: object
    durable: bool = False


class _ExecutionLedger:
    """Exclusive durable closed claim, operation, and dispatch-commit records."""
    def __init__(self, owner):
        self.owner = owner
        self.records = []

    def _claim(self, attempt=None):
        o = self.owner
        kind = None if attempt is None else attempt.kind
        name = "execution-owner.once" if attempt is None else "execution-"+kind+".once"
        target = None if attempt is None else attempt.target_slot
        previous = self.records[-1].sha256 if self.records else None
        row = dict(format="easel.b3.execution-journal.v1", kind=kind, ordinal=0,
            phase="BOUND" if attempt is None else "CLAIMED", restore_id=o._restore_id,
            operation_id=None if target is None else target.original_attempt._spec.operation_nonce,
            input_sha256=o._input_pin,
            context_sha256=None if kind is None else o._context_pins[_KINDS.index(kind)],
            target_identity_sha256=None if target is None else target.identity_pin,
            target_cid=None if target is None else target.cid, worker_cid=None,
            policy_sha256=o._policy_pin, job_device=o._job[0], job_inode=o._job[1],
            started_at_ns=o._started_at_ns, deadline_ns=o._deadline_ns,
            argv_sha256=None, request_sha256=None, previous_sha256=previous,
            terminal_code=None, stdout_sha256=None, stderr_sha256=None)
        self._append(name,row)

    def operation(self, attempt, slot, phase, *, request_frame=None, baseline_sha256=None, validated_response_sha256=None):
        require(phase in ('intent','result'))
        o=self.owner;t=attempt.target_slot
        row=dict(format='easel.b3.execution-journal.v1',kind=attempt.kind,ordinal=slot.ordinal,
            phase=phase,restore_id=o._restore_id,operation_id=t.original_attempt._spec.operation_nonce,
            input_sha256=o._input_pin,context_sha256=o._context_pins[_KINDS.index(attempt.kind)],
            target_identity_sha256=t.identity_pin,target_cid=t.cid,worker_cid=attempt.worker_slot.cid if attempt.worker_slot else None,
            policy_sha256=o._policy_pin,job_device=o._job[0],job_inode=o._job[1],started_at_ns=o._started_at_ns,
            deadline_ns=o._deadline_ns,argv_sha256=slot.argv_pin,request_sha256=slot.request_pin,
            previous_sha256=self.records[-1].sha256,terminal_code=slot.terminal_code,
            stdout_sha256=None if phase=='intent' else hashlib.sha256(slot.captures['out'].finish()).hexdigest(),
            stderr_sha256=None if phase=='intent' else hashlib.sha256(slot.captures['err'].finish()).hexdigest())
        if slot.operation=='dispatch' and phase=='intent':
            require(type(request_frame) is bytes and request_frame is attempt.request_bytes)
            require(hashlib.sha256(request_frame).hexdigest()==slot.request_pin)
            require(baseline_sha256 is None and validated_response_sha256 is None)
            row['phase']='DISPATCH_CLAIMED'
            # Exact punctuation cost plus hex length, before allocating hex.
            require(len(canonical(row))+len(',"request_frame_hex":""')+2*len(request_frame)<=8192)
            row['request_frame_hex']=request_frame.hex()
        elif slot.operation=='dispatch' and phase=='result':
            require(request_frame is None);_hash(baseline_sha256);_hash(validated_response_sha256)
            row.update(phase='COMMITTED',baseline_sha256=baseline_sha256,validated_response_sha256=validated_response_sha256)
        else:require(request_frame is baseline_sha256 is validated_response_sha256 is None)
        return self._append('execution-'+attempt.kind+'-'+str(slot.ordinal).zfill(3)+'-'+phase+'.json',row)

    def _append(self,name,row):
        o=self.owner;previous=self.records[-1].sha256 if self.records else None
        require(row['previous_sha256']==previous)
        raw = canonical(row); require(len(raw) <= 8192)
        slot = o._allocation("journal:"+name)
        record = _Record(name, slot, raw, hashlib.sha256(raw).hexdigest(), previous)
        self.records.append(record)  # retain intent before open can acquire/raise
        slot.fd = _open(name, os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600, dir_fd=o._job_slot.fd)
        _regular(os.fstat(slot.fd), o._factory_policy)
        offset = 0
        while offset < len(raw):
            o._guard_time()
            try: n = _write(slot.fd, raw[offset:])
            except InterruptedError as exc:
                require(not getattr(exc,"characters_written",0));continue
            require(type(n) is int and 0 < n <= len(raw)-offset); offset += n
        _fsync(slot.fd)
        o._guard_bindings()
        _fsync(o._job_slot.fd)
        o._guard_bindings()
        slot.stamp = _stamp(os.fstat(slot.fd)); record.durable = True
        self.verify()
        return record

    def verify(self):
        previous = None
        for record in self.records:
            require(record.durable and record.previous == previous)
            slot = record.slot
            require(slot.fd is not None and not slot.closed)
            info = os.fstat(slot.fd); _regular(info, self.owner._factory_policy)
            named = os.stat(record.name, dir_fd=self.owner._job_slot.fd, follow_symlinks=False)
            require(_stamp(info) == slot.stamp == _stamp(named))
            raw = os.pread(slot.fd, 8193, 0)
            require(len(raw) <= 8192 and raw == record.raw and hashlib.sha256(raw).hexdigest() == record.sha256)
            previous = record.sha256


class _RoleAdmissionSlot:
    __repr__=_redacted
    def __init__(self,owner,pair,source_owners,authorizations):
        self.owner=owner;self.pair=pair;self.source_owners=source_owners
        self.authorizations=authorizations;self.state='CLAIMED';self.consumer_held=False
        self.plans=None;self.plan_pin=None;self.reason=None;self.runtime=None
        self.targets=tuple(owner._targets.values());self.input=owner._input
        self.stage=owner._stage;self.contexts=owner._context_objects
        self.target_records=tuple(t.ready_records for t in self.targets)
        self.target_record_pins=tuple(t.ready_records_pin for t in self.targets)
    def __copy__(self):raise TypeError('original admission only')
    def __deepcopy__(self,memo):raise TypeError('original admission only')
    def __reduce_ex__(self,p):raise TypeError('original admission only')


class RootExecutionOwner:
    __repr__ = _redacted

    def __init__(self, factory, verified_input, policy, *, reconstruction_policy=None):
        require(type(factory) is RootTargetFactory and type(verified_input) is VerifiedRestoreInput)
        require(type(policy) is RootExecutionPolicy); policy.__post_init__()
        if reconstruction_policy is not None:
            from .reconstruction_runtime import RootReconstructionPolicy
            require(type(reconstruction_policy) is RootReconstructionPolicy);reconstruction_policy.pin()
        self._reconstruction_policy=reconstruction_policy
        self._original_reconstruction_policy=reconstruction_policy
        self._reconstruction_policy_pin=None if reconstruction_policy is None else reconstruction_policy.pin()
        self._lock = threading.RLock()
        self._factory = factory; self._input = verified_input; self._policy = policy
        self._clock = _monotonic
        self._original_policy = policy
        self._producer_returns = []
        self._fd_slots = []; self._targets = {}; self._attempts = {}
        self._native_originals=[];self._native_closed=set();self._native_close_attempted=set();self._native_stamps={};self._selector_slots=[]
        self._disposition = None; self._close_started = False
        self._unknown = False; self._closed = False; self._initialized = False; self._active = False
        self._job_slot = None; self._ledger = None; self._role_slot = None; self._role_original_slot = None
        with self._lock, factory._lock, _REGISTRY_LOCK:
            require(not factory._attempts)
            factory._check_policy()
            info = os.fstat(factory._policy._job_fd); job = (info.st_dev, info.st_ino)
            require(job not in _OWNERS and id(factory) not in _FACTORIES)
            # Winning original is anchored before binding validation or custody IO.
            self._job = job; _OWNERS[job] = self; _FACTORIES[id(factory)] = self
            try:
                self._factory_policy = factory._policy; self._tools = factory._tools
                self._factory_pin = factory._pin; self._tool_pin = factory._tool_pin
                self._policy_pin = digest(_data(policy))
                self._stage = verified_input._stage
                self._authority = verified_input._stage_authority
                self._parser_authority = verified_input._parser_authority
                self._runner = verified_input._runner
                self._input_policy = verified_input._policy
                self._input_policy_pin = digest(_context_data(self._input_policy))
                self._contexts = self._authority.contexts
                self._context_objects = tuple(ctx for _, ctx in self._contexts)
                self._context_pins = tuple(digest(_context_data(ctx)) for ctx in self._context_objects)
                self._event = self._authority.cancellation
                source = self._factory_policy._source
                self._source = source
                self._restore_id = source._restore_id; self._started_at = source._started_at; self._deadline = source._deadline
                self._started_at_ns = math.ceil(self._started_at*10**9)
                self._deadline_ns = math.floor(self._deadline*10**9)
                self._job_fd = self._factory_policy._job_fd
                self._path = self._factory_policy._job_path
                self._lineage = self._read_lineage()
                require(self._lineage[-1][1:3] == job)
                _binding(self._factory_policy, verified_input, self._authority)
                self._input_pin = digest(verified_input.safe_receipt)
                self._job_slot = self._allocation("job-duplicate")
                self._job_slot.fd = _dup(self._job_fd)
                _directory(os.fstat(self._job_slot.fd), self._factory_policy)
                self._job_slot.stamp = (info.st_dev, info.st_ino)
                self._lineage_fds = []
                self._pin_lineage()
                self._ledger = _ExecutionLedger(self)
                self._guard_bindings()
                self._ledger._claim()
                self._initialized = True
            except BaseException:
                self._fail(); raise

    def __reduce_ex__(self, protocol):
        raise TypeError("original execution owner cannot be adopted")

    def __copy__(self):
        raise TypeError("original execution owner cannot be copied")

    def __deepcopy__(self, memo):
        raise TypeError("original execution owner cannot be copied")

    def _allocation(self, operation):
        slot = _FDSlot(operation); self._fd_slots.append(slot); return slot

    def _read_lineage(self):
        result = []
        path = PurePosixPath("/")
        for part in ("",) + PurePosixPath(self._path).parts[1:]:
            if part: path = path / part
            info = os.stat(str(path), follow_symlinks=False)
            require(stat.S_ISDIR(info.st_mode))
            result.append((str(path), info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid))
        _directory(info, self._factory_policy)
        return tuple(result)

    def _pin_lineage(self):
        # Retain every nofollow path-component allocation before doing any check.
        for index, row in enumerate(self._lineage):
            slot = self._allocation("lineage:"+row[0]); self._lineage_fds.append(slot)
            parent = None if index == 0 else self._lineage_fds[index-1].fd
            name = "/" if index == 0 else PurePosixPath(row[0]).name
            slot.fd = _open(name, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW, dir_fd=parent)
            info = os.fstat(slot.fd)
            require((info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid) == row[1:])
        self._verify_lineage()

    def _verify_lineage(self):
        require(len(self._lineage_fds) == len(self._lineage))
        for index, (slot, row) in enumerate(zip(self._lineage_fds, self._lineage)):
            require(slot.fd is not None and not slot.closed)
            parent = None if index == 0 else self._lineage_fds[index-1].fd
            name = "/" if index == 0 else PurePosixPath(row[0]).name
            for info in (os.fstat(slot.fd), os.stat(name, dir_fd=parent, follow_symlinks=False)):
                require(stat.S_ISDIR(info.st_mode))
                require((info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid) == row[1:])

    def _check_original_bindings(self):
        """Shared pure-memory checks: no time, verification, reopening or native IO.

        Normal caller cleanup may close original peers only after this owner has
        successfully released its host custody. Identity/content pins remain
        auditable in that state; pre-close peer closure is never normal release.
        """
        require(_OWNERS.get(self._job) is self and _FACTORIES.get(id(self._factory)) is self)
        f = self._factory; v = self._input; p = self._factory_policy
        require(f._policy is p and f._tools is self._tools)
        require(f._pin == self._factory_pin and f._tool_pin == self._tool_pin)
        require(digest(p._projection()) == self._factory_pin)
        self._tools.__post_init__()
        require(digest(self._tools.__dict__) == self._tool_pin)
        require(self._policy is self._original_policy and digest(_data(self._policy)) == self._policy_pin)
        require(self._reconstruction_policy is self._original_reconstruction_policy)
        require((None if self._reconstruction_policy is None else self._reconstruction_policy.pin()) == self._reconstruction_policy_pin)
        require(self._policy.target_probe.image_id == p._specs[0].image_id)
        require(f._event is self._event and p._cancellation is self._event)
        require(v._stage is self._stage)
        require(v._stage_authority is self._authority and self._stage._authority is self._authority)
        require(v._parser_authority is self._parser_authority and v._runner is self._runner)
        require(self._runner._authority is self._parser_authority)
        require(v._policy is self._input_policy and digest(_context_data(v._policy)) == self._input_policy_pin)
        require(digest(v._receipt) == self._input_pin)
        require(self._authority.contexts is self._contexts and self._authority.cancellation is self._event)
        for (_, current), original, pin in zip(self._contexts, self._context_objects, self._context_pins):
            require(current is original and digest(_context_data(current)) == pin)
        require(p._source is self._source)
        job_values = (self._restore_id, self._started_at, self._deadline)
        require((self._source._restore_id, self._source._started_at, self._source._deadline) == job_values)
        require((self._authority.restore_id, self._authority.started_at, self._authority.deadline) == job_values)
        require(p._job_fd == self._job_fd and p._job_path == self._path)
        if not self._closed and self._disposition is None:
            require(f._closed is False and v._closed is False and self._stage._closed is False)
        require(all(type(k) is str for k in f._attempts))
        require(set(f._attempts) == set(self._targets))
        for kind, slot in self._targets.items():
            require(slot.state == "READY")
            a = slot.original_attempt; h = slot.original_handle
            require(any(k == kind and original is h and original_attempt is a
                        for k, original, original_attempt in self._producer_returns))
            require(type(h) is TargetHandle and h._attempt is a and f._attempts.get(kind) is a)
            require(a._unknown is False and a._state == "READY")
            if not self._closed and self._disposition is None: require(h._closed is False and a._closed is False)
            require(a._factory is f and a._verified is v and a._stage is self._stage and a._authority is self._authority)
            require(a._identity is slot.identity and digest(a._identity.safe_summary()) == slot.identity_pin)
            require(a._cid == slot.cid and digest(a._running) == slot.running_pin)
            require(a._sequence == 8 and len(a._records) == 8)
            require(slot.ready_records is not None and len(slot.ready_records)==8)
            require(all(current is original for current,original in zip(a._records,slot.ready_records)))
            require(digest(tuple(a._records))==slot.ready_records_pin)

    def _observe_original(self):
        require(self._initialized)
        self._check_original_bindings()

    def _fail(self):
        self._unknown = True
        for attempt in self._attempts.values():
            if attempt.state!='COMMITTED':
                attempt.unknown = True; attempt.state = "UNKNOWN"

    def _guard_time(self):
        require(not self._event.is_set())
        now = self._clock()
        require(type(now) in (int, float) and math.isfinite(now) and self._started_at <= now < self._deadline)
        runtime=self._reconstruction_runtime()
        if runtime is not None:runtime.guard_time(now)
        for a in self._attempts.values():
            if a.acceptance_deadline is not None and a.state!='COMMITTED':require(now<a.acceptance_deadline)

    def _guard_bindings(self):
        require(not self._unknown and not self._closed)
        self._check_original_bindings()
        self._guard_time()
        info = os.fstat(self._job_fd); _directory(info, self._factory_policy)
        require((info.st_dev, info.st_ino) == self._job)
        self._verify_lineage()
        if self._job_slot is not None and self._job_slot.fd is not None:
            info = os.fstat(self._job_slot.fd); _directory(info, self._factory_policy)
            require(not self._job_slot.closed and (info.st_dev, info.st_ino) == self._job)

    def _guard_live(self):
        self._guard_bindings()
        require(self._initialized)
        self._ledger.verify()

    @contextmanager
    def _exclusive(self):
        # A competing API call cannot wait and silently become a new operation.
        require(self._lock.acquire(blocking=False))
        try:
            with self._factory._lock:
                require(not self._active)
                yield
        finally:
            self._lock.release()

    def create_target(self, kind):
        require(type(kind) is str and kind in _KINDS)
        with self._exclusive():
            require(not self._active)
            try:
                self._guard_live(); require(kind not in self._targets and not self._attempts)
                slot = _TargetSlot(kind); self._targets[kind] = slot
                self._active = True
                try:
                    # Store the return itself: never reconstruct from an attempt.
                    slot.original_handle = self._factory.create(self._input, kind)
                    self._producer_returns.append((kind, slot.original_handle, slot.original_handle._attempt))
                finally:
                    slot.original_attempt = self._factory._attempts.get(kind)
                h = slot.original_handle; a = slot.original_attempt
                require(type(h) is TargetHandle and h._attempt is a)
                slot.identity = a._identity; slot.identity_pin = digest(a._identity.safe_summary())
                slot.cid = a._cid; slot.running_pin = digest(a._running)
                slot.ready_records=tuple(a._records);slot.ready_records_pin=digest(slot.ready_records)
                slot.state = "READY"
                self._guard_live(); return h
            except BaseException:
                self._fail(); raise
            finally:
                self._active = False

    def _begin_attestation(self, kind):
        """Burn original claim only. No child command, success or continuation API."""
        require(type(kind) is str and kind in _KINDS)
        with self._exclusive():
            require(not self._active)
            try:
                self._guard_live()
                require(set(self._targets) == set(_KINDS) and kind not in self._attempts)
                require(kind == "central" if not self._attempts else
                        (kind == "identity" and self._attempts["central"].state == "COMMITTED"))
                attempt = _ExecutionAttempt(kind, self._targets[kind])
                self._attempts[kind] = attempt; self._active = True
                self._ledger._claim(attempt)
                self._guard_live(); attempt.state = "CLAIMED"
                return attempt
            except BaseException:
                self._fail(); raise
            finally:
                self._active = False

    def role_admission_snapshot(self):
        held=self._lock.acquire(blocking=False)
        try:
            slot=self._role_slot
            mismatch=slot is not self._role_original_slot
            return dict(claims=int(slot is not None),admitted=bool(slot and slot.plans is not None),
                consumer_held=bool(slot and slot.consumer_held),closed=bool(slot and slot.state=='CLOSED'),
                unknown=bool(mismatch or (slot and slot.state=='UNKNOWN')),observation_error=bool(not held or mismatch),
                data_only=True,native_verified=False,SQL_restore=False)
        finally:
            if held:self._lock.release()

    def _begin_reconstruction(self,target,verified,kind):
        from .reconstruction_runtime import begin
        return begin(self,target,verified,kind)

    def _reconstruction_runtime(self):
        return None if self._role_slot is None else self._role_slot.runtime

    def _reconstruction_snapshot(self):
        runtime=self._reconstruction_runtime()
        return runtime.snapshot() if runtime is not None else dict(reconstruction_phase_claims=0,
            reconstruction_committed_count=0,reconstruction_unknown=False,reconstruction_consumer_held=False)

    def _all_native_slots(self):
        slots=[s for a in self._attempts.values() for s in a.operations if s.operation!='dispatch']
        runtime=self._reconstruction_runtime()
        return slots+(runtime.audit_slots() if runtime is not None else [])

    def _role_originals(self,slot):
        require(slot is self._role_slot and slot is self._role_original_slot and slot.owner is self and slot.input is self._input and slot.stage is self._stage)
        require(len(slot.targets)==len(self._targets) and all(a is b for a,b in zip(slot.targets,self._targets.values())))
        require(len(slot.contexts)==len(self._context_objects) and all(a is b for a,b in zip(slot.contexts,self._context_objects)))
        require(all(tuple(t.original_attempt._records)==records and digest(records)==pin
            for t,records,pin in zip(slot.targets,slot.target_records,slot.target_record_pins)))
        if slot.plans is not None:
            from .roles import _hash
            require(_hash(slot.plans)==slot.plan_pin)
        if slot.consumer_held:
            require(slot.pair._consumer is not None and slot.pair._consumer[0] is self and slot.pair._consumer[1] is slot)
            require(slot.pair._consumers==1)

    def _role_custody(self,slot):
        from ..media_backup_b2.reconstruction_capture import _pin
        from ..media_backup_b2.reconstruction_context import SourcePhase
        from media_backup.contract import encode_manifest
        pair=slot.pair;stage=self._stage
        pair._validate_terminal_success(require_live=False)
        require(type(slot.source_owners) is tuple and len(slot.source_owners)==2)
        require(type(slot.authorizations) is tuple and len(slot.authorizations)==2)
        require(pair._contexts is not None and len(pair._contexts)==2)
        require(all(a is b for a,b in zip(pair._contexts,self._context_objects)))
        require(pair._cancel is self._event)
        require(canonical(pair._captured.plan)==canonical(stage._plan))
        receipt=stage._receipt
        require(pair._captured.source_facts_sha256==receipt['source_facts_sha256']==stage._plan['snapshots']['source_sha256'])
        require(hashlib.sha256(encode_manifest(stage._plan)).hexdigest()==receipt['plan_sha256'])
        archive=pair._authenticated['archive'];stream=pair._authenticated['receipts']['archive']
        for key in ('plan_sha256','registry_sha256','source_facts_sha256','archive_sha256'):
            require(archive[key]==receipt[key])
        require(stream['plaintext_sha256']==receipt['archive_sha256'] and stream['ciphertext_sha256']==receipt['ciphertext_sha256'])
        require(stream['plaintext_length']==archive['archive_bytes'])
        intent=pair._intent
        require(intent._policy is pair._writer_policy and intent._directory is pair._writer_selector)
        require(intent._policy._issuer is pair._writer_issuer is pair._policy._issuer)
        require((intent.backup_id,intent.target,intent.commit_sha256,intent._directory.identity,
            intent._policy._backup.identity,pair._reference,hashlib.sha256(pair._commit).hexdigest())==pair._writer_pin)
        require(pair._state['owned_intent'] is intent and pair._state['owned_commit_ref'] is pair._reference)
        require(pair._state['owned_commit_bytes']==pair._commit)
        require(len(stage._origin)==2)
        from .preflight import _CommittedReadOrigin
        for origin in stage._origin:
            require(type(origin) is _CommittedReadOrigin)
            origin.verify()
            require(origin._issuer is pair._writer_issuer and origin._reference==pair._reference)
            require(origin._commit==pair._commit and origin._job_identity==pair._slots[0]['identity'][:5])
            require(origin._selector_identity==pair._writer_pin[3])
            require(origin._reader._receipt==stream)
        require(stage._origin[0]._supplied is stage._origin[1]._supplied)
        require(stage._origin[0]._reader is not stage._origin[1]._reader)
        for owner,producer,auth,context in zip(slot.source_owners,pair._producers,slot.authorizations,self._context_objects):
            require(owner is producer._owner and owner._reconstruction_producer is producer)
            require(getattr(owner,'_replay_authorization',None) is auth and producer._authorization is auth)
            auth._verify();require(auth.original_comparison is context)

    def _role_join_data(self,slot):
        from ..media_backup_b2.reconstruction_capture import _pin
        from ..media_backup_b2.reconstruction_context import SourcePhase
        pair=slot.pair;stage=self._stage;receipt=stage._receipt
        samples=[];authorizations=[];graphs=[];provenance=[]
        for kind,owner,producer,auth,context in zip(_KINDS,slot.source_owners,pair._producers,slot.authorizations,self._context_objects):
            require(owner is producer._owner and owner._reconstruction_producer is producer)
            require(getattr(owner,'_replay_authorization',None) is auth and producer._authorization is auth)
            auth._verify();require(auth.original_comparison is context)
            data=auth.data
            require(data.restore_id==self._restore_id and data.started_at==self._started_at and data.deadline==self._deadline)
            require(producer._started==int(self._started_at*1e9) and producer._deadline==int(self._deadline*1e9))
            require(owner.binding['started']==self._started_at and owner.binding['deadline']==self._deadline)
            samples.append(tuple(producer._samples[phase] for phase in (SourcePhase.BEFORE,SourcePhase.DURING,SourcePhase.AFTER)));authorizations.append(data)
            graphs.append(None if producer._grant_scope is None else tuple(producer._grant_samples[phase] for phase in (SourcePhase.BEFORE,SourcePhase.DURING,SourcePhase.AFTER)))
            provenance.append(dict(before_sample_sha256=producer._samples[SourcePhase.BEFORE].safe_summary()['full_sha256'],
                during_sample_sha256=producer._samples[SourcePhase.DURING].safe_summary()['full_sha256'],
                after_sample_sha256=producer._samples[SourcePhase.AFTER].safe_summary()['full_sha256'],
                rendering_sha256=_pin(producer._rendering),authorization_sha256=auth._data_pin,
                source_facts_sha256=receipt['source_facts_sha256'],plan_sha256=receipt['plan_sha256']))
        slot.renderings=tuple(p._rendering for p in pair._producers)
        slot.source_pin=_pin(tuple(provenance));slot.origins=stage._origin
        return dict(samples=tuple(samples),observations=tuple(copy.deepcopy(stage._observations[kind]) for kind in _KINDS),
            contexts=self._context_objects,authorizations=tuple(authorizations),
            target_specs=tuple(self._factory_policy._specs),provenance=tuple(provenance),grant_samples=tuple(graphs))

    def _admit_role_plan(self,pair,*,source_owners,authorizations):
        from ..media_backup_b2.reconstruction_capture import OwnedSourceCapture
        require(type(self) is RootExecutionOwner and type(pair) is OwnedSourceCapture)
        with self._exclusive():
            require(self._role_slot is None)
            slot=_RoleAdmissionSlot(self,pair,source_owners,authorizations);self._role_slot=slot;self._role_original_slot=slot
            try:
                pair._claim_consumer(self,slot);slot.consumer_held=True
            except Exception:
                slot.state='REJECTED';slot.reason='CLAIM_REFUSED';raise B3Error() from None
            self._active=True
            try:
                # Custody is checked separately from current authorization/time.
                # Failure here is sticky, including an original source UNKNOWN.
                self._check_original_bindings();self._role_originals(slot)
                self._role_custody(slot);pair._verify_publication()
                try:
                    self._guard_time()
                except Exception:
                    self._role_custody(slot);pair._verify_publication();self._role_originals(slot)
                    slot.state='REJECTED';slot.reason='AUTHORITY_REFUSED';raise B3Error() from None
                self._input.verify()
                try:
                    pair._validate_terminal_success()
                    require(not self._unknown and not self._closed and not self._close_started)
                    require(set(self._targets)==set(_KINDS))
                    require(all(a.state=='COMMITTED' and not a.unknown for a in self._attempts.values()))
                    from .roles import build_role_database_pair
                    arguments=self._role_join_data(slot)
                    plans=build_role_database_pair(**arguments)
                    from .roles import _hash
                    pin=_hash(plans)
                    self._guard_time()
                except Exception:
                    # Never label a changed peer during planning a known denial.
                    self._role_custody(slot);pair._verify_publication();self._role_originals(slot)
                    slot.state='REJECTED';slot.reason='DATA_REFUSED';raise B3Error() from None
                self._guard_live();pair._verify_publication();self._role_custody(slot);self._role_originals(slot)
                slot.plans=plans;slot.plan_pin=pin;slot.state='ADMITTED'
                return self.role_admission_snapshot()
            except BaseException:
                if slot.state=='CLAIMED':slot.state='UNKNOWN';slot.reason='CUSTODY_UNKNOWN'
                raise
            finally:self._active=False

    def _close_role_plan_consumer(self):
        with self._exclusive():
            slot=self._role_slot
            require(slot is not None and slot.state in ('ADMITTED','REJECTED','CLOSED'))
            if slot.state=='CLOSED':return
            require(not self._active)
            try:
                self._check_original_bindings();self._role_originals(slot)
                if slot.runtime is not None:slot.runtime.may_close()
                if slot.consumer_held:
                    # Historical custody remains mandatory after clock expiry or
                    # cancellation; releasing host references grants no new work.
                    self._role_custody(slot);slot.pair._verify_publication()
                    slot.pair._release_consumer(self,slot);slot.consumer_held=False
                slot.state='CLOSED'
                if slot.runtime is not None:slot.runtime.closed=True
            except BaseException:
                slot.state='UNKNOWN';slot.reason='CLOSE_UNKNOWN';raise

    def _popen_type(self):return _popen
    def _selector_type(self):return _selector
    def _io_pipe(self):return _pipe()
    def _io_nonblocking(self,fd,blocking):
        _set_blocking(fd,blocking);require(os.get_blocking(fd) is False)
    def _validate_native_fd(self,s,fd):
        require((id(s),fd) not in self._native_closed)
        info=os.fstat(fd);require(stat.S_ISFIFO(info.st_mode))
        require((info.st_dev,info.st_ino,info.st_mode)==self._native_stamps[(id(s),fd)])
    def _io_read(self,fd,size):return _read(fd,size)
    def _io_write(self,fd,raw):return _write(fd,raw)
    def _execution_env(self):return dict(PATH='/usr/bin:/bin',LANG='C',LC_ALL='C',HOME=self._path)
    def _close_native_fd(self,s,fd):
        require(any(fd in pair for pair in s.fds))
        self._validate_native_fd(s,fd)
        key=(id(s),fd);require(key not in self._native_close_attempted)
        self._native_close_attempted.add(key);_close(fd);self._native_closed.add(key)

    def _command(self,a,operation):
        """Closed source table; no external argv, paths, SQL or callbacks."""
        spec=a.target_slot.original_attempt._spec;l=self._policy.limits
        prefix=(spec.docker_path,'-H',spec.docker_socket,'--config',spec.docker_config_path)
        target=a.target_slot.cid;w=a.worker_slot
        if operation=='target-inspect':return prefix+('inspect','--type','container',target),1024**2,False,None
        if operation=='image-inspect':return prefix+('image','inspect',self._policy.worker.image_id),1024**2,False,None
        if operation=='worker-inspect':
            require(w is not None and w.cid is not None)
            return prefix+('inspect','--type','container',w.cid),1024**2,False,None
        probe={'probe-hashes':('/usr/bin/sha256sum',)+tuple(r[1] for r in self._policy.target_probe.tools),
            'probe-ns':('/usr/bin/stat','-L','-c','%d %i','/proc/1/ns/net'),
            'probe-self-ns':('/usr/bin/stat','-L','-c','%d %i','/proc/self/ns/net'),
            'probe-link':('/usr/bin/readlink','/proc/1/ns/net'),
            'probe-self-link':('/usr/bin/readlink','/proc/self/ns/net'),
            'probe-stat':('/usr/bin/cat','/proc/1/stat'),
            'probe-exe':('/usr/bin/readlink','/proc/1/exe')}
        if operation in probe:
            return prefix+('exec','--user','999:999','--env','LC_ALL=C','--env','LANG=C',target)+probe[operation],65536,False,target
        if operation=='worker-create':
            require(w is not None and w.cid is None)
            argv=prefix+('create','--name',w.name,'--label','com.drawbookai.easel.purpose=b3-attest',
                '--label','com.drawbookai.easel.operation='+spec.operation_nonce,'--network','container:'+target,
                '--user','10001:10001','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges:true',
                '--cpus',str(l.cpu_nanos//10**9)+'.'+str(l.cpu_nanos%10**9).zfill(9),'--memory',str(l.memory_bytes),'--memory-swap',str(l.memory_swap_bytes),
                '--pids-limit',str(l.pids_limit),'--tmpfs','/run/easel:rw,noexec,nosuid,nodev,size='+str(l.tmpfs_bytes),
                '--restart','no','--stop-timeout','1','--interactive',self._policy.worker.image_id,'attest_worker')
            return argv,65536,False,None
        require(w is not None and w.cid is not None)
        if operation=='worker-start':return prefix+('start','--attach','--interactive',w.cid),l.private_result_limit,True,w.cid
        require(operation=='observer')
        return prefix+('exec','--interactive','--user','10001:10001',w.cid,'/opt/easel/bin/python','-I','-B','-m',
            'platform_service.media_backup_b3.execution_observer'),1024**2,True,w.cid

    def _new_operation(self,a,operation,held=False):
        require(len(a.operations)<512)
        argv,cap,framed,remote=self._command(a,operation)
        slot=_transport._NativeSlot(operation,len(a.operations)+1,argv,digest(list(argv)),remote_cid=remote,
            remote_exec=operation.startswith('probe-') or operation=='observer')
        a.operations.append(slot)
        slot.intent_record=self._ledger.operation(a,slot,'intent')
        self._guard_live()
        _transport._launch(self,a,slot,cap,framed,held=held)
        return slot

    def _finish_operation(self,a,s):
        require(s.terminal and not s.durable)
        s.result_record=self._ledger.operation(a,s,'result');s.durable=True
        return s.captures['out'].finish()

    def _run_operation(self,a,operation):
        s=self._new_operation(a,operation)
        if operation=='observer':
            require(a.state=='WORKER_WAITING')
            payload=canonical(dict(format='easel.b3.execution-baseline-request.v1',remaining_ns=self._remaining(a)))
            s.input=len(payload).to_bytes(4,'big')+payload
            require(len(payload)<=65536)
        _transport._drain(self,a,s)
        return self._finish_operation(a,s)

    def _target_observe(self,a):
        raw=self._run_operation(a,'target-inspect');v=_baseline._one(raw)
        from .target_factory import _oom_default_comparison
        require(canonical(_oom_default_comparison(v))==canonical(_oom_default_comparison(a.target_slot.original_attempt._running)))
        require(v['Id']==a.target_slot.cid and v['ExecIDs'] is None)
        for s in a.operations:
            if s.remote_exec and s.remote_cid==a.target_slot.cid and s.terminal and s.durable:s.remote_terminal=True
        return v

    def _target_baseline(self,a):
        self._target_observe(a)
        expected=''.join(r[2]+'  '+r[1]+'\n' for r in self._policy.target_probe.tools).encode('ascii')
        require(self._run_operation(a,'probe-hashes')==expected)
        rounds=[]
        for _ in range(2):
            ns=self._run_operation(a,'probe-ns');link=self._run_operation(a,'probe-link')
            own=self._run_operation(a,'probe-self-ns');ownlink=self._run_operation(a,'probe-self-link')
            namespace=_baseline._namespace(ns,link);require(namespace==_baseline._namespace(own,ownlink))
            rawstat=self._run_operation(a,'probe-stat');start=_baseline._start(rawstat)
            exe=self._run_operation(a,'probe-exe')
            require(re.fullmatch(rb'/[^\x00-\x20\x7f-\xff]+\n',exe) is not None and not exe.endswith(b' (deleted)\n'))
            rounds.append(dict(namespace=namespace,start=start,exe=exe.decode('ascii')))
        require(rounds[0]==rounds[1]);self._target_observe(a)
        return rounds[0]

    def _worker_observe(self,a,phase):
        v=_baseline._worker(self._run_operation(a,'worker-inspect'),self,a,phase)
        for s in a.operations:
            if s.remote_exec and s.remote_cid==a.worker_slot.cid and s.terminal and s.durable:s.remote_terminal=True
        return v

    def _remaining(self,a):
        self._guard_live();require(a.acceptance_deadline is not None)
        remaining=min(60_000_000_000,math.floor((a.acceptance_deadline-self._clock())*10**9))
        require(remaining>0);return remaining

    def _contract_policy(self,a):
        t=a.target_slot;s=t.original_attempt._spec;p=self._policy;w=p.worker;l=p.limits;b=a.baseline
        binding=_contract.ExecutionBinding(self._restore_id,s.operation_nonce,a.kind,_contract.Operation.ATTEST_WORKER,
            s.source_container_id,t.cid,t.identity_pin,self._input_pin,self._context_pins[_KINDS.index(a.kind)],
            self._started_at_ns,self._deadline_ns,*self._job)
        return _contract.ExecutionPolicy(binding,w.image_id,w.artifact_sha256,w.dependency_manifest_sha256,
            w.entrypoint_sha256,w.pg_restore_sha256,w.architecture,b['profile_sha256'],b['security_sha256'],
            *(getattr(l,f.name) for f in fields(l)))

    def attest(self,kind):
        require(type(kind) is str and kind in _KINDS)
        with self._exclusive():
            try:
                a=self._begin_attestation(kind);self._active=True
                # The original factory handle verifies the complete target/peer pair.
                a.target_slot.original_handle.verify()
                original=self._target_baseline(a);a.state='TARGET_BASELINED'
                _baseline._image(self._run_operation(a,'image-inspect'),self._policy.worker)
                spec=a.target_slot.original_attempt._spec
                a.worker_slot=_WorkerSlot('easel-b3-attest-'+spec.operation_nonce,
                    {'com.drawbookai.easel.purpose':'b3-attest','com.drawbookai.easel.operation':spec.operation_nonce})
                raw=self._run_operation(a,'worker-create')
                require(re.fullmatch(rb'[a-f0-9]{64}\n',raw) is not None)
                a.worker_slot.cid=raw[:-1].decode('ascii');require(a.worker_slot.cid not in tuple(t.cid for t in self._targets.values())+(spec.source_container_id,))
                require(all(other is a or other.worker_slot is None or other.worker_slot.cid!=a.worker_slot.cid for other in self._attempts.values()))
                a.state='WORKER_CREATED';self._worker_observe(a,'created')
                main=self._new_operation(a,'worker-start',held=True);a.worker_slot.main=main
                # One original asynchronous start; only bounded read-only state waits.
                for _ in range(128):
                    raw=self._run_operation(a,'worker-inspect');v=_baseline._one(raw)
                    if v['State']['Status']=='running':_baseline._worker(raw,self,a,'running');break
                    _baseline._worker(raw,self,a,'created')
                else:require(False)
                a.state='WORKER_WAITING'
                require(main.offset==0 and main.input is None and main.process.poll() is None and not main.captures['out'].raw and not main.captures['err'].raw)
                baseline_raw=self._run_operation(a,'observer')
                a.baseline=_baseline._observer(baseline_raw,original['namespace'])
                self._worker_observe(a,'running');a.state='BASELINED'
                require(self._target_baseline(a)==original)
                self._worker_observe(a,'running')
                a.target_slot.original_handle.verify()
                self._guard_live();a.execution_policy=self._contract_policy(a)
                a.request_bytes=_contract.encode_attest_request(a.execution_policy,self._remaining(a))
                require(len(a.request_bytes)<=self._policy.limits.envelope_limit+4)
                main.request_pin=hashlib.sha256(a.request_bytes).hexdigest()
                # Closed dispatch claim binds the exact original bytes durably.
                dispatch=_transport._NativeSlot('dispatch',len(a.operations)+1,(),digest([]),request_pin=main.request_pin)
                a.operations.append(dispatch)
                self._ledger.operation(a,dispatch,'intent',request_frame=a.request_bytes)
                a.state='DISPATCH_CLAIMED';self._guard_live()
                require(main.input is None and main.offset==0 and main.process.poll() is None and not main.captures['out'].raw and not main.captures['err'].raw)
                main.input=a.request_bytes
                _transport._drain(self,a,main);a.response=self._finish_operation(a,main);a.state='DRAINED'
                self._worker_observe(a,'exited');a.worker_slot.terminal=True;main.remote_terminal=True
                self._target_observe(a)
                validated=_contract.validate_attestation(a.execution_policy,a.request_bytes,a.response)
                a.target_slot.original_handle.verify()
                a.state='VALIDATED';self._guard_live()
                a.baseline_hash=digest(dict(target=original,observer=a.baseline['raw'],transcripts=[
                    dict(ordinal=s.ordinal,argv_sha256=s.argv_pin,stdout=s.captures['out'].finish().hex(),stderr=s.captures['err'].finish().hex())
                    for s in a.operations if s.operation not in ('dispatch','worker-start')]))
                dispatch.captures={'out':_transport._Capture(len(a.response),False),'err':_transport._Capture(65536,False)}
                dispatch.captures['out'].feed(a.response);dispatch.terminal_code=0;dispatch.terminal=True
                a.final_record=self._ledger.operation(a,dispatch,'result',baseline_sha256=a.baseline_hash,
                    validated_response_sha256=hashlib.sha256(validated).hexdigest())
                dispatch.durable=True;self._guard_live()
                require(all(s.durable and s.terminal and (not s.remote_exec or getattr(s,'remote_terminal',False)) for s in a.operations))
                summary=_contract.attestation_summary(a.execution_policy,a.request_bytes,a.response)
                self._guard_live();a.state='COMMITTED'
                return summary
            except BaseException:self._fail();raise
            finally:self._active=False

    def _audit_execution(self):
        slots=self._all_native_slots()
        runtime=self._reconstruction_runtime()
        if runtime is not None and runtime.admission.consumer_held:runtime.verify()
        require(len({id(s) for s in slots})==len(slots))
        originals=self._native_originals
        require(all(r['closed'] and r['object'] is not None for r in self._selector_slots))
        require(len({id(p) for _,p,_,_ in originals})==len(originals))
        require(all(any(s is native for native in slots) for s,_,_,_ in originals))
        terminal=0;local=0;remote=0
        for s in slots:
            matches=[row for row in originals if row[0] is s]
            if s.process is None:require(not matches);local+=1
            else:
                require(len(matches)==1 and matches[0][1] is s.process and matches[0][2] is s.argv and matches[0][3]==s.ordinal)
                require(digest(list(s.argv))==s.argv_pin)
                code=s.process.poll() if not self._closed else s.terminal_code
                if s.terminal:
                    require(type(code) is int and code==s.terminal_code==0 and s.durable and s.eof=={'out','err'} and s.input_closed)
                    require(s.offset==len(s.input))
                    records=(runtime.journal.records if runtime is not None and any(s is n for n in runtime.audit_slots()) else self._ledger.records)
                    record=s.result_record;require(record.durable and any(r is record for r in records))
                    from .target_factory import _json
                    row=_json(record.raw)
                    require(row['argv_sha256']==s.argv_pin and row['ordinal']==s.ordinal and row['terminal_code']==code)
                    require(row['stdout_sha256']==hashlib.sha256(s.captures['out'].finish()).hexdigest())
                    require(row['stderr_sha256']==hashlib.sha256(s.captures['err'].finish()).hexdigest())
                    terminal+=1
                else:
                    # Terminal local observation grants no drain/result/remote proof.
                    if type(code) is int:
                        previous=getattr(s,'disposition_exit',None)
                        require(previous is None or previous==code)
                        s.disposition_exit=code;terminal+=1
                    else:local+=1
            if s.remote_cid is not None and not getattr(s,'remote_terminal',False):remote+=1
        committed=0
        for a in self._attempts.values():
            if a.state=='COMMITTED':
                require(not a.unknown and a.final_record is not None and a.final_record.durable and
                    any(r is a.final_record for r in self._ledger.records) and a.worker_slot.terminal)
                require(all(s.terminal and s.durable for s in a.operations))
                committed+=1
            elif a.worker_slot is not None:remote+=int(not a.worker_slot.terminal)
        return committed,terminal,local,remote

    def snapshot(self):
        """Observation only; retained originals and producer transcripts survive close."""
        acquired=self._lock.acquire(blocking=False);error=not acquired
        counts=(0,0,0,0)
        try:
            if acquired:
                try:
                    self._observe_original()
                    if self._disposition is not None:
                        require(self._unknown and not self._closed and not self._close_started)
                        for peer in self._disposition['closeable_peers']:
                            value=peer._closed;require(type(value) is bool)
                            was_closed=self._disposition['peer_closed'][id(peer)]
                            require(not was_closed or value)
                            self._disposition['peer_closed'][id(peer)]=value
                        self._ledger.verify()
                        require(self._retention_binding()==self._disposition['binding'])
                        for current,original in ((tuple(self._fd_slots),self._disposition['fd_slots']),
                            (tuple(self._ledger.records),self._disposition['records']),
                            (tuple(self._targets.values()),self._disposition['targets'])):
                            require(len(current)==len(original) and all(a is b for a,b in zip(current,original)))
                        require(tuple(self._native_originals)==self._disposition['native'])
                        require(tuple((id(slot),slot.fd,slot.stamp) for slot in self._fd_slots)==self._disposition['fd_pins'])
                        require(frozenset(self._native_closed)==self._disposition['native_closed'])
                        require(frozenset(self._native_close_attempted)==self._disposition['native_close_attempted'])
                        for native,fd in self._disposition['open_native_fds']:self._validate_native_fd(native,fd)
                        for slot in self._fd_slots:
                            require(slot.fd is not None and not slot.closed and not slot.close_attempted)
                            os.fstat(slot.fd)
                    counts=self._audit_execution()
                except BaseException:error=True
            # A contender observes uncertainty but does not poison the winner.
            committed,terminal,local,remote=counts
            incomplete=(bool(self._attempts) and committed!=len(self._attempts)) or self._reconstruction_snapshot()['reconstruction_unknown']
            if error:
                if acquired:self._fail()
                local=max(local,1);remote=max(remote,1)
            return dict(execution_owner_count=1,
                execution_target_count=sum(s.state=='READY' and s.original_handle is not None for s in self._targets.values()),
                execution_phase_claims=len(self._attempts),execution_committed_count=committed,
                execution_local_terminal_count=terminal,execution_local_live_or_unobserved=local,
                execution_remote_live_or_unobserved=remote,execution_active_work=int(self._active),
                execution_unknown=bool(self._unknown or error or incomplete or local or remote or self._active),
                execution_observation_error=bool(error),execution_disposition_retained=self._disposition is not None,execution_retained_owner_count=int(self._disposition is not None))
        finally:
            if acquired:self._lock.release()

    def _retention_binding(self):
        # Passive, original-memory + retained ledger binding; DATA cannot adopt it.
        with self._lock:
            self._observe_original();self._verify_lineage();self._ledger.verify()
            info=os.fstat(self._job_slot.fd)
            require((info.st_dev,info.st_ino)==self._job and not self._job_slot.closed)
            require(self._started_at_ns==math.ceil(self._started_at*10**9) and self._deadline_ns==math.floor(self._deadline*10**9))
            row=dict(job=list(self._job),input_sha256=self._input_pin,
                context_sha256=dict(zip(_KINDS,self._context_pins)),policy_sha256=self._policy_pin,
                started_at_ns=self._started_at_ns,deadline_ns=self._deadline_ns,
                journal_head=self._ledger.records[-1].sha256,restore_id=self._restore_id)
            runtime=self._reconstruction_runtime()
            if runtime is not None:
                row['reconstruction_head']=runtime.journal.records[-1].sha256 if runtime.journal.records else None
            return digest(row)

    def _retain_disposition(self, document_sha256, binding_sha256):
        with self._lock:
            _hash(document_sha256);_hash(binding_sha256)
            require(self._role_slot is None or not self._role_slot.consumer_held)
            require(self._disposition is None and self._unknown and not self._closed and not self._close_started)
            require(not self._active and self._retention_binding()==binding_sha256)
            state=self.snapshot()
            require(not state['execution_active_work'] and not state['execution_local_live_or_unobserved']
                and not state['execution_observation_error'])
            # Exact references are held permanently; no host close is attempted.
            closeable=(self._factory,self._input,self._stage)+tuple(
                peer for target in self._targets.values() for peer in (target.original_handle,target.original_attempt))
            require(all(type(peer._closed) is bool for peer in closeable))
            self._disposition=dict(closeable_peers=closeable,peer_closed={id(peer):peer._closed for peer in closeable},document_sha256=document_sha256,binding=binding_sha256,
                fd_slots=tuple(self._fd_slots),fd_pins=tuple((id(slot),slot.fd,slot.stamp) for slot in self._fd_slots),
                native=tuple(self._native_originals),native_closed=frozenset(self._native_closed),
                native_close_attempted=frozenset(self._native_close_attempted),
                open_native_fds=tuple((native,fd) for native in self._all_native_slots()
                    for pair in native.fds for fd in pair if (id(native),fd) not in self._native_closed),
                records=tuple(self._ledger.records),targets=tuple(self._targets.values()),
                peers=(self._factory,self._input,self._stage,self._authority,self._parser_authority))
            require(self.snapshot()['execution_observation_error'] is False)

    def close(self):
        # Budget expiry cannot suppress an attempt to close known owned FDs.
        with self._exclusive():
            if self._closed: return
            require(self._role_slot is None or (self._role_slot.state=='CLOSED' and not self._role_slot.consumer_held))
            require(not self._unknown and not self._active and all(a.state=='COMMITTED' for a in self._attempts.values()))
            try:
                require(not self._close_started and self._disposition is None)
                self._close_started=True
                require(self._initialized)
                self._observe_original()
                self._ledger.verify()
                committed,terminal,local,remote=self._audit_execution()
                require(local==remote==0 and committed==len(self._attempts))
                for native in self._all_native_slots():
                    for pair in native.fds:
                        require(len(pair)==2)
                        for fd in pair:
                            if (id(native),fd) not in self._native_closed:self._close_native_fd(native,fd)
                for slot in reversed(self._fd_slots):
                    require(slot.fd is not None and not slot.close_attempted)
                    slot.close_attempted = True
                    _close(slot.fd)
                    slot.closed = True
                self._closed = True
            except BaseException:
                self._fail(); raise


def _context_data(context):
    # Context trees are validated by the original VerifiedRestoreInput producer.
    # This projection adds a frozen original-content pin, never new authority.
    from dataclasses import asdict
    return asdict(context)
