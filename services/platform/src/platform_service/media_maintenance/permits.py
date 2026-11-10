"""Opaque operation ownership; thread references outlive parent cancellation."""
import copy
import hashlib
import os
import secrets
import threading
import time
from .contracts import MaintenanceError,OperationBinding,digest,require

_MINT=object()
class _Opaque:
    def __reduce__(self):raise TypeError("internal capability")
    def __repr__(self):return "<"+type(self).__name__+">"

class MediaPermit(_Opaque):
    def __init__(self,control,lease,binding,raw_token,epoch,*,_mint=None):
        require(_mint is _MINT and type(binding) is OperationBinding)
        self._control=control;self._lease=lease;self.binding=binding;self.epoch=epoch
        self._raw_token=raw_token;self._lock=threading.RLock();self._refs=0;self._pid=os.getpid()
        self._closing=False;self._closed=False;self._valid=True
    @property
    def token_digest(self):return hashlib.sha256(self._raw_token).hexdigest()
    def _local_lock(self):
        # Never wait on an RLock inherited from a different mint process.
        require(os.getpid()==self._pid);return self._lock
    @property
    def physical_count(self):
        with self._local_lock():return self._refs
    def _check(self,binding):require(os.getpid()==self._pid and self._valid and not self._closed and binding==self.binding)
    def retain_physical(self,binding):
        require(binding.kind in {'job','upload','gc'})
        if hasattr(self._control,'_verify_physical_domain'):self._control._verify_physical_domain()
        with self._local_lock():
            self._check(binding);require(not self._closing)
            lease=self._lease.retain();self._refs+=1
            return PhysicalRef(self,lease,_mint=_MINT)
    def invalidate(self):
        with self._local_lock():self._valid=False;self._closing=True
    def release_failed_physical(self):
        """Release only local FD after failure; NEVER removes the DB permit."""
        with self._local_lock():
            require(not self._valid and not self._refs)
            if not self._closed:self._closed=True;self._lease.close()

class PhysicalRef(_Opaque):
    def __init__(self,permit,lease,*,_mint=None):
        require(_mint is _MINT);self._permit=permit;self._lease=lease;self._closed=False;self._started=False;self._pid=os.getpid()
    def start(self):
        require(os.getpid()==self._pid)
        with self._permit._local_lock():
            require(not self._closed)
            if not self._started:
                self._permit._check(self._permit.binding);require(not self._permit._closing)
                self._started=True
            return self
    def close(self):
        require(os.getpid()==self._pid)
        with self._permit._local_lock():
            if not self._closed:
                self._closed=True;self._permit._refs-=1;self._lease.close()
    def __enter__(self):return self.start()
    def __exit__(self,*args):self.close();return False

class WriteFence(_Opaque):
    """Actual staged blob identity checked under this operation before writing."""
    def __init__(self,permit,metadata,*,action,_mint=None):
        require(_mint is _MINT and type(permit) is MediaPermit and action in {'write','discard'})
        self._permit=permit;self._metadata=copy.deepcopy(metadata);self._action=action;self._pid=os.getpid();self._valid=True
    @property
    def storage_key(self):require(os.getpid()==self._pid);return self._metadata['storage_key']
    def verify(self,metadata,*,action='write'):
        require(os.getpid()==self._pid and self._valid and action==self._action)
        from .runtime import require_physical
        ref=require_physical();require(ref._permit is self._permit)
        require(all(metadata.get(k)==self._metadata[k] for k in ('storage_key','sha256','size','mime','properties')))
    def close(self):require(os.getpid()==self._pid);self._valid=False


def _mint_write_fence(permit,metadata,*,action):return WriteFence(permit,metadata,action=action,_mint=_MINT)


class GcFence(_Opaque):
    """Transaction/claim/DB-clock bounded authority for the actual unlink."""
    def __init__(self,connection,permit,metadata,deadline_ns,*,_mint=None):
        require(_mint is _MINT and type(permit) is MediaPermit)
        self._connection=connection;self._permit=permit;self._metadata=dict(metadata)
        self._deadline_ns=deadline_ns;self._pid=os.getpid();self._valid=True
    def verify(self,metadata):
        require(os.getpid()==self._pid and self._valid and time.monotonic_ns()<self._deadline_ns)
        from psycopg.pq import TransactionStatus
        require(self._connection.info.transaction_status==TransactionStatus.INTRANS)
        require(all(metadata.get(k)==self._metadata[k] for k in ('storage_key','sha256','size','mime','properties','gc_token')))
        from .runtime import require_physical
        reference=require_physical();require(reference._permit is self._permit)
        with self._permit._local_lock():
            self._permit._check(self._permit.binding)
            require(self._permit.binding.kind=='gc' and self._permit.binding.subject_id==metadata['storage_key'] and self._permit.binding.attempt_id==metadata['gc_token'])
    def close(self):require(os.getpid()==self._pid);self._valid=False


def _mint_gc_fence(connection,permit,metadata,deadline_ns):
    require(type(deadline_ns) is int and deadline_ns>time.monotonic_ns())
    return GcFence(connection,permit,metadata,deadline_ns,_mint=_MINT)

async def admit_operation(control,locks,binding):
    require(type(binding) is OperationBinding)
    lease=locks.acquire_shared();raw=secrets.token_bytes(32)
    try:
        epoch=await control.issue_permit(binding,hashlib.sha256(raw).hexdigest())
        require(type(epoch) is int and epoch>=0)
        permit=MediaPermit(control,lease,binding,raw,epoch,_mint=_MINT)
        if hasattr(control,"_track_permit"):control._track_permit(permit)
        return permit
    except BaseException:
        lease.close();raise

async def assert_transaction_permit(connection,permit,binding):
    require(type(permit) is MediaPermit)
    with permit._local_lock():permit._check(binding);require(not permit._closing)
    try:await permit._control.verify_permit(connection,permit,binding)
    except BaseException:
        permit.invalidate();raise

async def close_permit(control,permit,*,stable_business_digest):
    require(type(permit) is MediaPermit and control is permit._control);digest(stable_business_digest)
    with permit._local_lock():
        permit._closing=True;permit._check(permit.binding);require(not permit._refs)
    try:await control.finish_permit(permit,stable_business_digest)
    except BaseException:
        permit.invalidate();raise
    with permit._local_lock():
        require(not permit._refs);permit._closed=True;permit._lease.close()
        if hasattr(control,"_forget_permit"):control._forget_permit(permit)

async def observe_permit(control,permit):
    """Only read the exact original durable result; never issue/finish a permit."""
    require(type(permit) is MediaPermit and control is permit._control and os.getpid()==permit._pid)
    result=await control.observe_permit(permit)
    require(type(result) is dict and result.get('state') in {'active','unknown','completed'})
    if result['state']=='completed':
        digest(result.get('business_sha256'))
        with permit._local_lock():
            require(not permit._refs)
            if not permit._closed:
                permit._valid=False;permit._closing=True;permit._closed=True;permit._lease.close()
        if hasattr(control,"_forget_permit"):control._forget_permit(permit)
    return result
