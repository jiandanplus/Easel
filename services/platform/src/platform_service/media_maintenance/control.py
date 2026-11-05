"""Restricted SQL transport and explicit owner CAS; no runtime activation."""
from contextlib import asynccontextmanager
from dataclasses import asdict,replace
import secrets
import os
import time
from uuid import uuid4
from psycopg.types.json import Jsonb
from .contracts import MaintenanceError,OperationBinding,WindowBinding,WorkerIdentity,digest,require

class DatabaseControl:
    def __init__(self,connection_factory,*,locks=None,clock_ns=time.monotonic_ns):
        self.connection_factory=connection_factory;self.locks=locks;self.clock_ns=clock_ns
        self._source=None;self._domain=None;self._workers={};self._windows={};self._holders={};self._permits={};self._pid=os.getpid()
    def __repr__(self):return "<DatabaseControl>"
    async def _rpc(self,name,args=(),*,connection=None):
        allowed={"register_worker","attest_worker","unregister_worker","issue_permit","finish_permit","permit_result","mark_unknown","generation_digest","begin_window","observe_window","verify_window","transition"}
        require(os.getpid()==self._pid and name in allowed)
        async def run(c):
            row=await(await c.execute("SELECT easel_backup_control."+name+"("+",".join(["%s"]*len(args))+") AS value",args)).fetchone()
            return row['value'] if isinstance(row,dict) else row[0]
        try:
            if connection is not None:return await run(connection)
            async with self.connection_factory() as c:
                async with c.transaction():return await run(c)
        except MaintenanceError:raise
        except Exception:raise MaintenanceError() from None
    async def status(self):
        require(os.getpid()==self._pid)
        try:
            async with self.connection_factory() as c:
                row=await(await c.execute("SELECT jsonb_build_object('state',state,'epoch',epoch,'window_id',window_id) AS value FROM easel_backup_control.runtime_status")).fetchone()
                require(row is not None);return row['value'] if isinstance(row,dict) else row[0]
        except Exception:raise MaintenanceError() from None
    async def register_worker(self,identity):
        require(type(identity) is WorkerIdentity);key=(identity.worker_id,identity.generation)
        token=self._workers.setdefault(key,secrets.token_bytes(32))
        await self._rpc("register_worker",(Jsonb(asdict(identity)),token))
    async def attest_worker(self,identity,source):
        from .filesystem import SourceRoot,domain_attestation
        require(type(identity) is WorkerIdentity and type(source) is SourceRoot)
        token=self._workers.get((identity.worker_id,identity.generation));require(token is not None)
        domain=domain_attestation(self.locks,source)
        await self._rpc('attest_worker',(Jsonb(asdict(identity)),token,Jsonb(domain)))
        self._source=source;self._domain=domain
    def _verify_physical_domain(self):
        require(os.getpid()==self._pid and self._source is not None and self._domain is not None)
        from .filesystem import domain_attestation
        require(domain_attestation(self.locks,self._source)==self._domain)
    def _track_permit(self,permit):self._permits[permit.token_digest]=permit
    def _forget_permit(self,permit):self._permits.pop(permit.token_digest,None)
    async def unregister_worker(self,identity):
        require(type(identity) is WorkerIdentity)
        require(not any(p.binding.worker==identity and (not p._closed or p.physical_count) for p in self._permits.values()))
        token=self._workers.get((identity.worker_id,identity.generation));require(token is not None)
        await self._rpc("unregister_worker",(Jsonb(asdict(identity)),token))
    async def issue_permit(self,binding,token_digest):
        require(type(binding) is OperationBinding);digest(token_digest)
        token=self._workers.get((binding.worker.worker_id,binding.worker.generation));require(token is not None)
        return await self._rpc("issue_permit",(Jsonb(asdict(binding.worker)),token,token_digest,binding.kind,binding.subject_id,binding.attempt_id,binding.upstream_identity_sha256))
    async def verify_permit(self,connection,permit,binding):
        require(os.getpid()==self._pid and connection is not None and binding==permit.binding)
        try:
            await connection.execute("SELECT easel_backup_control.validate_permit(%s,%s,%s,%s,%s,%s,%s)",(permit._raw_token,permit.epoch,binding.kind,binding.subject_id,binding.attempt_id,binding.worker.worker_id,binding.worker.generation))
        except Exception:raise MaintenanceError() from None
    async def finish_permit(self,permit,business_digest):
        await self._rpc("finish_permit",(permit._raw_token,permit.epoch,business_digest))
    async def observe_permit(self,permit):
        return await self._rpc("permit_result",(permit._raw_token,permit.epoch))
    async def mark_unknown(self,permit):
        permit.invalidate();await self._rpc("mark_unknown",(permit._raw_token,permit.epoch))
    async def generation_digest(self):return await self._rpc("generation_digest")
    async def begin_window(self,original_state,*,drain_seconds=30,capture_verify_seconds=120):
        require(type(drain_seconds) is int and drain_seconds==30 and type(capture_verify_seconds) is int and capture_verify_seconds==120)
        require(type(original_state) is dict)
        names=("host_id","boot_id","process_start","control_dev","control_ino","source_dev","source_ino","generation_digest")
        require(set(original_state)==set(names)|{"configuration_state"})
        require(not any(v.get("phase_state")!="OPEN" for v in self._windows.values()))
        now=self.clock_ns();nonce=secrets.token_bytes(32);window=str(uuid4())
        value=WindowBinding(window,nonce,0,*(original_state[k] for k in names[:3]),*(original_state[k] for k in names[3:7]),original_state['generation_digest'],now+150_000_000_000)
        payload={k:getattr(value,k) for k in names};payload['deadline_ns']=value.deadline_ns
        self._windows[window]={"binding":value,"drain_until":now+30_000_000_000,"phase_until":None,"unknown":True,"phase_state":"INTENT"}
        epoch=await self._rpc("begin_window",(nonce,window,Jsonb(payload),Jsonb(original_state)))
        value=WindowBinding(window,nonce,epoch,*(getattr(value,k) for k in names),value.deadline_ns)
        self._windows[window].update(binding=value,unknown=False,phase_state='DRAINING')
        return value
    @property
    def pending_window_ids(self):return frozenset(k for k,v in self._windows.items() if v['unknown'])
    async def observe_window(self,window_id):
        entry=self._windows.get(window_id);require(entry is not None);binding=entry['binding']
        observed=await self._rpc("observe_window",(binding.owner_nonce,window_id))
        expected={k:getattr(binding,k) for k in ("host_id","boot_id","process_start","control_dev","control_ino","source_dev","source_ino","generation_digest","deadline_ns")}
        require(type(observed) is dict and observed.get('binding')==expected)
        require(type(observed.get('epoch')) is int and observed['epoch']>=1)
        require(binding.epoch==0 or observed['epoch']==binding.epoch)
        state=observed.get('state');require(state in {'DRAINING','CAPTURING','VERIFYING','RECOVERING','NEEDS_REVIEW','OPEN'})
        bound=replace(binding,epoch=observed['epoch'])
        if state=='OPEN':
            intent=entry.get('pending_transition')
            result=entry.get('completion_result') if entry['phase_state']=='OPEN' else None
            if intent is not None and intent['next_state']=='OPEN':result=intent['result']
            require(result is not None and observed.get('result')==result and binding.epoch==bound.epoch)
            holder=self._holders.get(window_id)
            if holder is not None:
                # A lost OPEN receipt is resolved only by the original durable
                # result. Retain unknown/EX when live refs or path drift remain.
                holder.close_if_idle(bound);self._holders.pop(window_id)
            entry['completion_result']=result
        entry.update(binding=bound,unknown=False,phase_state=state,pending_transition=None)
        return bound,state
    def _window(self,binding,*,allow_expired=False):
        require(os.getpid()==self._pid and type(binding) is WindowBinding)
        entry=self._windows.get(binding.window_id);require(entry is not None and entry['binding']==binding)
        require(not entry['unknown'])
        if not allow_expired:require(self.clock_ns()<=binding.deadline_ns)
        return entry
    def drain_deadline_ns(self,binding):
        entry=self._window(binding)
        require(entry['phase_state']=='DRAINING' and self.locks is not None)
        self.locks.verify();require(self.locks.inode==(binding.control_dev,binding.control_ino))
        deadline=min(binding.deadline_ns,entry['drain_until']);require(self.clock_ns()<=deadline)
        return deadline
    def phase_deadline_ns(self,binding):
        entry=self._window(binding)
        holder=self._holders.get(binding.window_id);require(holder is not None)
        holder.verify(binding);require(entry['phase_until'] is not None)
        deadline=min(binding.deadline_ns,entry['phase_until']);require(self.clock_ns()<=deadline)
        return deadline
    async def verify_window(self,binding,*,expected_state):
        entry=self._windows.get(getattr(binding,'window_id',None))
        try:
            checked=self._window(binding)
            require(type(expected_state) is str and expected_state in {'DRAINING','CAPTURING','VERIFYING','RECOVERING'})
            require(checked['phase_state']==expected_state and self.locks is not None)
            self.locks.verify();require(self.locks.inode==(binding.control_dev,binding.control_ino))
            if expected_state=='DRAINING':require(self.clock_ns()<=checked['drain_until'])
            else:self.phase_deadline_ns(binding)
            observed=await self._rpc('verify_window',(binding.owner_nonce,binding.window_id,binding.epoch,expected_state))
            expected={k:getattr(binding,k) for k in ('host_id','boot_id','process_start','control_dev','control_ino','source_dev','source_ino','generation_digest','deadline_ns')}
            require(type(observed) is dict and observed.get('binding')==expected and observed.get('state')==expected_state and type(observed.get('epoch')) is int and observed['epoch']==binding.epoch and observed.get('generation_digest')==binding.generation_digest)
            # The await cannot extend either budget or excuse an EX/path change.
            self._window(binding);self.locks.verify()
            if expected_state=='DRAINING':require(self.clock_ns()<=checked['drain_until'])
            else:self.phase_deadline_ns(binding)
        except BaseException:
            if entry is not None:entry['unknown']=True
            raise
    async def acquire_window_exclusive(self,binding):
        entry=self._window(binding);require(entry['phase_state']=='DRAINING' and self.locks is not None and self.clock_ns()<=entry['drain_until'])
        require(self.locks.inode==(binding.control_dev,binding.control_ino))
        holder=self.locks.acquire_exclusive(binding)
        entry['phase_until']=min(binding.deadline_ns,self.clock_ns()+120_000_000_000)
        try:
            entry["unknown"]=True
            await self._rpc("transition",(binding.owner_nonce,binding.window_id,binding.epoch,"DRAINING","CAPTURING",Jsonb({'barrier':'exclusive'})))
            entry.update(unknown=False,phase_state="CAPTURING")
            self._holders[binding.window_id]=holder;return holder
        except BaseException:holder.close();raise
    async def transition(self,binding,*,expected_state,next_state,result):
        entry=self._window(binding,allow_expired=next_state=="NEEDS_REVIEW");require(type(result) is dict)
        if next_state!="NEEDS_REVIEW":
            require(entry['phase_until'] is not None and self.clock_ns()<=entry['phase_until'])
            holder=self._holders.get(binding.window_id);require(holder is not None);holder.verify(binding)
        if next_state=="OPEN":
            require(set(result)=={'source_identity_sha256','stable_side_effects_sha256','original_state_sha256'})
            for value in result.values():digest(value)
            holder.seal(binding)
        entry.update(unknown=True,pending_transition={'expected_state':expected_state,'next_state':next_state,'result':dict(result)})
        await self._rpc("transition",(binding.owner_nonce,binding.window_id,binding.epoch,expected_state,next_state,Jsonb(result)))
        if next_state=="OPEN":
            holder.close_if_idle(binding);self._holders.pop(binding.window_id)
            entry['completion_result']=dict(result)
        entry.update(unknown=False,phase_state=next_state,pending_transition=None)

async def register_worker(control,identity):return await control.register_worker(identity)
async def unregister_worker(control,identity):return await control.unregister_worker(identity)
async def begin_window(controller,original_state,*,drain_seconds=30,capture_verify_seconds=120):
    return await controller.begin_window(original_state,drain_seconds=drain_seconds,capture_verify_seconds=capture_verify_seconds)
async def acquire_window_exclusive(controller,binding):return await controller.acquire_window_exclusive(binding)
async def transition(controller,binding,*,expected_state,next_state,result):
    return await controller.transition(binding,expected_state=expected_state,next_state=next_state,result=result)
