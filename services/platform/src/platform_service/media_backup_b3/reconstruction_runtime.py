"""Original admission's finite phase custody and separate secret-safe journal.

This is private host custody. Hashes and deserialized results cannot mint a
consumer. The phase journal never records a private request, SQL, or verifier.
"""
from dataclasses import dataclass, field
from .contracts import require, digest, B3Error
from .execution import _ExecutionLedger, _redacted
from .reconstruction_contract import Operation

_MUTATING=(Operation.H_CREATE,Operation.HANDOVER,Operation.H_DROP,Operation.ROLES_APPLY,
    Operation.DB_CREATE,Operation.DB_FINALIZE,Operation.LOGIN_APPLY)

@dataclass(frozen=True,repr=False)
class RootReconstructionPolicy:
    native: object
    client_sha256: str
    program_sha256: str
    executable_sha256: str
    __repr__=_redacted
    def pin(self):
        from .execution import RootExecutionPolicy, _data, _hash
        require(type(self.native) is RootExecutionPolicy);self.native.__post_init__()
        _hash(self.client_sha256);_hash(self.program_sha256);_hash(self.executable_sha256)
        return digest(dict(native=_data(self.native),client=self.client_sha256,program=self.program_sha256,executable=self.executable_sha256))

@dataclass(repr=False,eq=False)
class _Phase:
    runtime: object
    kind: str
    operation: Operation
    ordinal: int
    target_slot: object
    plan: object
    state: str='CLAIMED'
    operations: list=field(default_factory=list)
    intent: object=None
    result: object=None
    main_start_anchor: object=None
    acceptance_deadline: object=None
    worker_slot: object=None
    baseline: object=None
    request_bytes: object=None
    final_record: object=None
    __repr__=_redacted

class _PhaseLedger(_ExecutionLedger):
    """Reuse only durable exclusive append/verify, never old operation rows."""
    def append(self, phase, state):
        require(state in ('CLAIMED','COMMITTED'))
        o=self.owner;r=phase.runtime;t=phase.target_slot
        row=dict(format='easel.b3.reconstruction-journal.v1',state=state,
            kind=phase.kind,operation=phase.operation.value,ordinal=phase.ordinal,
            restore_id=o._restore_id,job_device=o._job[0],job_inode=o._job[1],
            target_cid=t.cid,target_identity_sha256=t.identity_pin,
            input_sha256=o._input_pin,pair_sha256=r.admission.plan_pin,
            plan_sha256=r._plan_projection(phase.plan),
            policy_sha256=o._policy_pin,operation_id=t.original_attempt._spec.operation_nonce,
            rendering_sha256=r._plan_projection(r.admission.renderings[('central','identity').index(phase.kind)]),
            source_sha256=r.admission.source_pin,
            started_at_ns=o._started_at_ns,deadline_ns=o._deadline_ns,
            previous_sha256=self.records[-1].sha256 if self.records else None,
            native_count=len(phase.operations),
            result_sha256=r._plan_projection(phase.result) if state=='COMMITTED' else None,
            baseline_sha256=r._plan_projection(phase.baseline) if state=='COMMITTED' else None,
            reconstruction_policy_sha256=r.policy_pin)
        return self._append('reconstruction-'+str(phase.ordinal).zfill(3)+'-'+state.lower()+'.json',row)

class _Runtime:
    __repr__=_redacted
    def __init__(self, owner, admission):
        # Constructor is only called after the original has retained this object.
        self.owner=owner;self.admission=admission;self.phases=[]
        self.journal=_PhaseLedger(owner);self.unknown=False
        self.original_plans=admission.plans;self.original_targets=admission.targets
        self.closed=False;self.stopped=False;self.stop_record=None;self._committed=[];self._proofs=[];self._preflights=[]
        owner._check_original_bindings()
        self.policy=owner._original_reconstruction_policy
        self.policy_pin=owner._reconstruction_policy_pin
    def __copy__(self):raise TypeError('original reconstruction custody only')
    def __deepcopy__(self,memo):raise TypeError('original reconstruction custody only')
    def __reduce_ex__(self,p):raise TypeError('original reconstruction custody only')
    @staticmethod
    def _plan_projection(plan):
        from .roles import _hash
        return _hash(plan)
    def verify(self):
        o=self.owner;s=self.admission
        require(s is o._role_original_slot and s is o._role_slot and s.runtime is self)
        o._role_originals(s)
        require(s.plans is self.original_plans and s.targets is self.original_targets)
        require(s.consumer_held and not self.closed)
        for phase in self.phases:
            require(phase.runtime is self and phase.target_slot is o._targets[phase.kind])
            require(phase.plan is self.original_plans[('central','identity').index(phase.kind)])
        o._check_original_bindings()
        require(self.policy is o._original_reconstruction_policy and self.policy_pin==o._reconstruction_policy_pin)
        self.journal.verify()
    def fail(self, phase):
        self.unknown=True;self.admission.state='UNKNOWN';self.admission.reason='RECONSTRUCTION_UNKNOWN'
        phase.state='UNKNOWN';self.owner._fail()
    def guard_time(self, now):
        for phase in self.phases:
            if not self.committed(phase) and phase.acceptance_deadline is not None:
                require(now < phase.acceptance_deadline)
    def committed(self, phase):
        rows=[row for row in self._committed if row[0] is phase]
        if len(rows)!=1:return False
        _,record,result_pin=rows[0]
        return (phase.state=='COMMITTED' and phase.final_record is record and record.durable
            and any(r is record for r in self.journal.records)
            and self._plan_projection(phase.result)==result_pin)
    def next_operation(self, kind):
        plan=self.original_plans[('central','identity').index(kind)]
        chain=(Operation.INITIAL_OBSERVE,)+((Operation.H_CREATE,Operation.HANDOVER,Operation.H_DROP) if plan.handover else ())
        chain+=(Operation.ROLES_APPLY,Operation.DB_CREATE,Operation.DB_FINALIZE,Operation.LOGIN_APPLY)
        prior=[p for p in self.phases if p.kind==kind]
        require(all(self.committed(p) for p in prior) and len(prior)<len(chain))
        return chain[len(prior)]
    def claim(self, kind, operation):
        """Burn before native allocation/connection; no retry after append ambiguity."""
        o=self.owner
        with o._exclusive():
            self.verify();o._guard_live();o._role_custody(self.admission);self.admission.pair._verify_publication()
            require(not self.unknown and not self.stopped and type(kind) is str and kind in ('central','identity'))
            require(type(operation) is Operation and operation is self.next_operation(kind))
            # Final convergence needs genuine same-owner Task5/6 proofs. Their
            # registration is deliberately absent until those owners implement it.
            require(operation not in (Operation.DB_FINALIZE,Operation.LOGIN_APPLY))
            if kind=='identity':
                require(any(p.kind=='central' and p.operation is Operation.DB_CREATE and p.state=='COMMITTED' for p in self.phases))
            require(all(self.committed(p) for p in self.phases))
            phase=_Phase(self,kind,operation,len(self.phases)+1,o._targets[kind],self.original_plans[('central','identity').index(kind)])
            self.phases.append(phase);o._active=True
            try:
                phase.intent=self.journal.append(phase,'CLAIMED')
                self.verify();o._guard_live()
                return phase
            except BaseException:
                self.fail(phase);raise B3Error() from None
            finally:o._active=False
    def start_anchor(self, phase):
        """Called immediately before original Popen constructor; never renew."""
        require(any(phase is p for p in self.phases) and phase.state=='CLAIMED' and phase.main_start_anchor is None)
        o=self.owner;o._guard_live();now=o._clock()
        self.guard_time(now);phase.main_start_anchor=now;phase.acceptance_deadline=min(o._deadline,now+60)
    def _target_observe(self,phase):
        from . import execution_baseline as b
        from .target_factory import _oom_default_comparison
        from .contracts import canonical
        v=b._one(self._run_operation(phase,'target-inspect'))
        require(canonical(_oom_default_comparison(v))==canonical(_oom_default_comparison(phase.target_slot.original_attempt._running)))
        require(v['Id']==phase.target_slot.cid and v['ExecIDs'] is None)
        for slot in phase.operations:
            if slot.remote_exec and slot.remote_cid==phase.target_slot.cid and slot.terminal and slot.durable:slot.remote_terminal=True
        return v
    def _target_baseline(self,phase):
        # Fixed original physical-only probes; never reconnect old bootstrap SQL.
        import re
        from . import execution_baseline as b
        self._target_observe(phase)
        expected=''.join(r[2]+'  '+r[1]+'\n' for r in self.policy.native.target_probe.tools).encode('ascii')
        require(self._run_operation(phase,'probe-hashes')==expected)
        rounds=[]
        for _ in range(2):
            ns=b._namespace(self._run_operation(phase,'probe-ns'),self._run_operation(phase,'probe-link'))
            require(ns==b._namespace(self._run_operation(phase,'probe-self-ns'),self._run_operation(phase,'probe-self-link')))
            start=b._start(self._run_operation(phase,'probe-stat'));exe=self._run_operation(phase,'probe-exe')
            require(re.fullmatch(rb'/[^\x00-\x20\x7f-\xff]+\n',exe) is not None and not exe.endswith(b' (deleted)\n'))
            rounds.append(dict(namespace=ns,start=start,exe=exe.decode('ascii')))
        require(rounds[0]==rounds[1]);self._target_observe(phase);return rounds[0]
    def _worker_observe(self,phase,state):
        from . import execution_baseline as b
        value=b._reconstruction_worker(self._run_operation(phase,'worker-inspect'),self.owner,phase,state)
        for slot in phase.operations:
            if slot.remote_exec and slot.remote_cid==phase.worker_slot.cid and slot.terminal and slot.durable:slot.remote_terminal=True
        return value
    def _dispatch(self,kind):
        import re
        from .execution import _WorkerSlot
        from . import execution_baseline as b
        o=self.owner
        with o._exclusive():
            self.verify();o._guard_live();o._role_custody(self.admission);self.admission.pair._verify_publication()
            require(type(kind) is str and kind in ('central','identity'))
            operation=self.next_operation(kind)
            require(self.policy is not None and operation in (Operation.INITIAL_OBSERVE,Operation.ROLES_APPLY))
            # Pure host preflight precedes even the durable claim and first native
            # slot. Reserved hashes are finite-width DATA, never prerequisite authority.
            from types import SimpleNamespace
            pending=_Phase(self,kind,operation,len(self.phases)+1,o._targets[kind],self.original_plans[('central','identity').index(kind)])
            pending.intent=SimpleNamespace(sha256='0'*64)
            reserved=min(60*10**9,o._deadline_ns-o._started_at_ns)
            checked=self._request(pending,reserved)
            phase=self.claim(kind,operation)
            self._preflights.append((phase,checked))
            o._active=True
            try:
                require(self.policy is not None)
                require(phase.operation in (Operation.INITIAL_OBSERVE,Operation.ROLES_APPLY))
                original=self._target_baseline(phase)
                b._reconstruction_image(self._run_operation(phase,'image-inspect'),self.policy.native.worker)
                spec=phase.target_slot.original_attempt._spec
                phase.worker_slot=_WorkerSlot('easel-b3-reconstruct-'+spec.operation_nonce+'-'+str(phase.ordinal),
                    {'com.drawbookai.easel.purpose':'b3-reconstruct','com.drawbookai.easel.operation':spec.operation_nonce})
                raw=self._run_operation(phase,'worker-create');require(re.fullmatch(rb'[a-f0-9]{64}\n',raw) is not None)
                phase.worker_slot.cid=raw[:-1].decode('ascii')
                require(phase.worker_slot.cid not in tuple(t.cid for t in o._targets.values())+(spec.source_container_id,))
                require(all(p is phase or p.worker_slot is None or p.worker_slot.cid!=phase.worker_slot.cid for p in self.phases))
                self._worker_observe(phase,'created')
                main=self._new_operation(phase,'worker-start',held=True);phase.worker_slot.main=main
                for _ in range(128):
                    raw=self._run_operation(phase,'worker-inspect');v=b._one(raw)
                    if v['State']['Status']=='running':b._reconstruction_worker(raw,o,phase,'running');break
                    b._reconstruction_worker(raw,o,phase,'created')
                else:require(False)
                baseline=b._observer(self._run_operation(phase,'observer'),original['namespace']);phase.baseline=baseline
                self._worker_observe(phase,'running');require(self._target_baseline(phase)==original)
                phase.result=self._handshake(phase,main)
                self._installed_join(phase)
                self._worker_observe(phase,'exited');phase.worker_slot.terminal=True;main.remote_terminal=True
                require(self._target_baseline(phase)==original)
                require(all(s.terminal and s.durable and (not s.remote_exec or getattr(s,'remote_terminal',False)) for s in phase.operations))
                self.verify();o._guard_live();o._audit_execution()
                phase.final_record=self.journal.append(phase,'COMMITTED')
                o._guard_live()  # fsync duration cannot turn an expired phase into a commit
                self._committed.append((phase,phase.final_record,self._plan_projection(phase.result)))
                phase.state='COMMITTED';self.verify();return phase
            except BaseException:self.fail(phase);raise B3Error() from None
            finally:o._active=False
    def _request(self,phase,remaining):
        import os,base64
        from . import reconstruction_contract as c
        from .target_factory import _open_path, _parse_env
        self.verify();o=self.owner;t=phase.target_slot;a=t.original_attempt;spec=a._spec
        require(self.policy is not None)
        fd=_open_path(spec.env_file_path)
        try:values=_parse_env(a._files._read(fd,16384),spec,o._factory_policy._public_image_env)
        finally:os.close(fd)
        identity=dict(operational_name=spec.bootstrap_role,operational_oid=10,
            source_name=phase.plan.handover.expectation.name if phase.plan.handover else None,
            source_oid=None,helper_name=None,helper_oid=None,
            operational_attributes_sha256=self._plan_projection((True,True,True,True,True,True,True,-1,None,None)),
            operational_verifier_sha256=None,initdb_sha256=None)
        if phase.operation is not Operation.INITIAL_OBSERVE:
            require(phase.operation is Operation.ROLES_APPLY and phase.plan.handover is None)
            observed=[p for p in self.phases if p.kind==phase.kind and p.operation is Operation.INITIAL_OBSERVE]
            require(len(observed)==1 and self.committed(observed[0]))
            identity.update(observed[0].result['observation'])
        native=self.policy.native;w=native.worker;l=native.limits
        build=dict(artifact_sha256=w.artifact_sha256,manifest_sha256=w.dependency_manifest_sha256,
            entrypoint_sha256=w.entrypoint_sha256,executable_sha256=self.policy.executable_sha256,client_sha256=self.policy.client_sha256,program_sha256=self.policy.program_sha256,
            image_id=w.image_id,cpu_nanos=l.cpu_nanos,memory_bytes=l.memory_bytes,pids_limit=l.pids_limit,tmpfs_bytes=l.tmpfs_bytes)
        before=self.phases[phase.ordinal-2] if phase.ordinal>1 else None
        prerequisite=dict(ordinal=phase.ordinal-1,head_sha256=phase.intent.sha256,
            record_sha256=before.final_record.sha256 if before is not None else phase.intent.sha256)
        rendering=self.admission.renderings[('central','identity').index(phase.kind)]
        return c.encode_request(dict(format=c.REQUEST_FORMAT,operation=phase.operation.value,
            binding=dict(restore_id=o._restore_id,job_dev=o._job[0],job_ino=o._job[1],kind=phase.kind,
                operation_id=spec.operation_nonce,ordinal=phase.ordinal,source_cid=spec.source_container_id,target_cid=t.cid,
                system_id_sha256=t.identity.system_id_sha256,input_sha256=o._input_pin,
                context_sha256=o._context_pins[('central','identity').index(phase.kind)],pair_sha256=self.admission.plan_pin,
                plan_sha256=self._plan_projection(phase.plan),rendering_sha256=self._plan_projection(rendering),
                started_ns=o._started_at_ns,deadline_ns=o._deadline_ns),policy=dict(c._POLICY),
            plan=c.project_plan(phase.plan),identity_before=identity,prerequisite=prerequisite,
            private_auth=dict(operational_password=base64.b64encode(values['POSTGRES_PASSWORD'].encode('utf-8')).decode('ascii')),
            remaining_ns=remaining,source_rendering=dict(zip(('timezone','datestyle'),rendering)),build=build))
    def _command(self,phase,operation):
        from .execution import RootExecutionOwner
        from types import SimpleNamespace
        require(self.policy is not None)
        require(operation in ('target-inspect','image-inspect','worker-inspect','worker-create','worker-start','observer',
            'probe-hashes','probe-ns','probe-self-ns','probe-link','probe-self-link','probe-stat','probe-exe'))
        proxy=SimpleNamespace(_policy=self.policy.native)
        argv,cap,framed,remote=RootExecutionOwner._command(proxy,phase,operation)
        if operation=='worker-create':
            argv=tuple('com.drawbookai.easel.purpose=b3-reconstruct' if x=='com.drawbookai.easel.purpose=b3-attest'
                else 'reconstruct_worker' if x=='attest_worker' else x for x in argv)
        if operation=='worker-start':
            from .reconstruction_contract import RESULT_LIMIT
            from .reconstruction_worker import READY_FRAME
            cap=RESULT_LIMIT+4+len(READY_FRAME);framed=False
        return argv,cap,framed,remote
    def _native_record(self,phase,slot,state):
        import hashlib
        require(state in ('intent','result','request'))
        row=dict(format='easel.b3.reconstruction-native.v1',state=state,
            phase_ordinal=phase.ordinal,ordinal=slot.ordinal,operation=slot.operation,
            argv_sha256=slot.argv_pin,request_sha256=slot.request_pin,
            terminal_code=slot.terminal_code,
            stdout_sha256=hashlib.sha256(slot.captures['out'].finish()).hexdigest() if state=='result' else None,
            stderr_sha256=hashlib.sha256(slot.captures['err'].finish()).hexdigest() if state=='result' else None,
            previous_sha256=self.journal.records[-1].sha256)
        return self.journal._append('reconstruction-%03d-native-%03d-%s.json'%(phase.ordinal,slot.ordinal,state),row)
    def _new_operation(self,phase,operation,held=False):
        from . import execution_io as io
        require(len(phase.operations)<512)
        argv,cap,framed,remote=self._command(phase,operation)
        slot=io._NativeSlot(operation,len(phase.operations)+1,argv,digest(list(argv)),remote_cid=remote,
            remote_exec=operation.startswith('probe-') or operation=='observer')
        phase.operations.append(slot)
        slot.intent_record=self._native_record(phase,slot,'intent')
        io._launch(self.owner,phase,slot,cap,framed,held=held)
        return slot
    def _finish_operation(self,phase,slot):
        require(slot.terminal and not slot.durable)
        slot.result_record=self._native_record(phase,slot,'result');slot.durable=True
        return slot.captures['out'].finish()
    def _run_operation(self,phase,operation):
        from . import execution_io as io
        from .contracts import canonical
        slot=self._new_operation(phase,operation)
        if operation=='observer':
            payload=canonical(dict(format='easel.b3.execution-baseline-request.v1',remaining_ns=self.owner._remaining(phase)))
            slot.input=len(payload).to_bytes(4,'big')+payload
        io._drain(self.owner,phase,slot)
        return self._finish_operation(phase,slot)
    def _handshake(self,phase,main):
        """Exactly READY then guarded original request then one RESULT and EOF."""
        import hashlib
        from . import execution_io as io, reconstruction_contract as c
        from .reconstruction_worker import READY_FRAME
        while len(main.captures['out'].raw)<len(READY_FRAME):
            require(not main.eof and main.process.poll() is None)
            io._step(self.owner,[main])
        require(bytes(main.captures['out'].raw)==READY_FRAME and not main.captures['err'].raw)
        require(main.input is None and main.offset==0 and main.process.poll() is None)
        # Remaining is sampled only after READY. Re-read original custody and
        # compare every static field against immutable preflight bytes.
        raw=self._request(phase,self.owner._remaining(phase))
        checked=[raw0 for original,raw0 in self._preflights if original is phase]
        require(len(checked)==1 and len(raw)<=len(checked[0]))
        require(self._static_request(raw)==self._static_request(checked[0]))
        phase.request_bytes=raw
        main.request_pin=hashlib.sha256(raw).hexdigest()
        main.dispatch_record=self._native_record(phase,main,'request')
        self.owner._guard_live();main.input=raw
        io._drain(self.owner,phase,main)
        output=self._finish_operation(phase,main)
        require(output.startswith(READY_FRAME) and not main.captures['err'].finish())
        return c.decode_result(output[len(READY_FRAME):],raw)
    def _installed_join(self,phase):
        from .network_wire import decode_network_observation
        from .network_profile import validate_network_profile
        evidence=phase.result['installed_evidence']
        # Join independently observed namespace/profile/security; no result-derived allowlist.
        for side in ('before','after'):
            security=evidence[side]['security']
            network=validate_network_profile(decode_network_observation(security['network']))
            require(security['netns']==phase.baseline['namespace'])
            require(network.profile_sha256==phase.baseline['profile_sha256'])
            require(network.security_sha256==phase.baseline['security_sha256'])
    @staticmethod
    def _static_request(raw):
        import json
        from .contracts import canonical
        value=json.loads(raw[4:])
        value.pop('remaining_ns')
        value['prerequisite']['head_sha256']='0'*64
        if value['binding']['ordinal']==1:value['prerequisite']['record_sha256']='0'*64
        return canonical(value)
    def _role_proof(self,phase):
        from .reconstruction import RoleCreationProof
        require(phase.operation is Operation.ROLES_APPLY and self.committed(phase))
        self.verify();self.owner._guard_live();self.owner._audit_execution()
        require(phase.worker_slot.terminal)
        require(not any(p._phase is phase for p in self._proofs))
        proof=object.__new__(RoleCreationProof)
        object.__setattr__(proof,'_runtime',self);object.__setattr__(proof,'_phase',phase)
        self._proofs.append(proof);return proof
    def snapshot(self):
        return dict(reconstruction_phase_claims=len(self.phases),
            reconstruction_committed_count=sum(self.committed(p) for p in self.phases),
            reconstruction_unknown=bool(self.unknown or self.stopped or any(not self.committed(p) for p in self.phases)),
            reconstruction_consumer_held=self.admission.consumer_held)
    def audit_slots(self):
        slots=[s for p in self.phases for s in p.operations]
        require(len({id(s) for s in slots})==len(slots))
        return slots
    def _stop_known(self):
        """Durable cleanup-only terminal state; never grants work or success."""
        import hashlib,os
        from . import execution as e
        from .contracts import canonical
        o=self.owner
        require(not self.stopped and not self.unknown and not o._unknown and not o._active)
        require(o._event.is_set() or o._clock()>=o._deadline)
        self.verify();o._check_original_bindings();o._verify_lineage();o._audit_execution()
        row=dict(format='easel.b3.reconstruction-stop.v1',state='STOPPED',
            reason='FAILED_NEEDS_REVIEW',phase_count=len(self.phases),
            result_chain_sha256=self._plan_projection(tuple(result for _,_,result in self._committed)),
            job_device=o._job[0],job_inode=o._job[1],input_sha256=o._input_pin,
            previous_sha256=self.journal.records[-1].sha256 if self.journal.records else None)
        raw=canonical(row);require(len(raw)<=8192)
        slot=o._allocation('journal:reconstruction-stopped.json')
        record=e._Record('reconstruction-stopped.json',slot,raw,hashlib.sha256(raw).hexdigest(),row['previous_sha256'])
        self.journal.records.append(record);self.stop_record=record
        try:
            slot.fd=e._open(record.name,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=o._job_slot.fd)
            e._regular(os.fstat(slot.fd),o._factory_policy)
            offset=0
            while offset<len(raw):
                try:n=e._write(slot.fd,raw[offset:])
                except InterruptedError as exc:require(not getattr(exc,'characters_written',0));continue
                require(type(n) is int and 0<n<=len(raw)-offset);offset+=n
            e._fsync(slot.fd);o._check_original_bindings();o._verify_lineage()
            e._fsync(o._job_slot.fd);o._check_original_bindings();o._verify_lineage()
            slot.stamp=e._stamp(os.fstat(slot.fd));record.durable=True;self.journal.verify()
            self.stopped=True
        except BaseException:
            self.unknown=True;self.admission.state='UNKNOWN';o._fail();raise B3Error() from None
    def may_close(self):
        self.verify()
        require(not self.unknown and all(self.committed(p) for p in self.phases))
        require(all(s.terminal and s.durable and (not s.remote_exec or getattr(s,'remote_terminal',False)) for s in self.audit_slots()))
        complete=all(any(p.kind==kind and p.operation is Operation.LOGIN_APPLY and self.committed(p)
            for p in self.phases) for kind in ('central','identity'))
        if not complete:self._stop_known()


def begin(owner, target, verified, kind):
    from .execution import RootExecutionOwner
    from .target_factory import TargetHandle
    require(type(owner) is RootExecutionOwner and type(target) is TargetHandle)
    require(type(kind) is str and kind in ('central','identity'))
    require(verified is owner._input and owner._targets[kind].original_handle is target)
    with owner._exclusive():
        owner._guard_live();s=owner._role_slot
        require(s is not None and s.state=='ADMITTED' and s.plans is not None)
        owner._role_originals(s);owner._role_custody(s);s.pair._verify_publication()
        require(s.runtime is None)
        r=object.__new__(_Runtime);s.runtime=r  # retain before constructor
        try:_Runtime.__init__(r,owner,s);r.verify();return r
        except BaseException:
            s.state='UNKNOWN';s.reason='RUNTIME_UNKNOWN';owner._fail();raise B3Error() from None
