"""Trusted in-process operation lineage; no HTTP fields or implicit admission."""
import asyncio
from contextlib import asynccontextmanager,contextmanager
from contextvars import ContextVar
import functools
import hashlib
import inspect
import json
import os
from .contracts import MaintenanceError,OperationBinding,require
from .control import DatabaseControl
from .filesystem import ControlLock,SourceRoot
from .identity import worker_identity
from .permits import MediaPermit,PhysicalRef,WriteFence,_mint_write_fence,admit_operation,assert_transaction_permit,close_permit,observe_permit

_CURRENT=ContextVar("easel_media_permit",default=None)
_PHYSICAL=ContextVar("easel_media_physical",default=None)


def current_permit(explicit=None,*,kinds=None,subject=None,attempt=None):
    permit=explicit if explicit is not None else _CURRENT.get()
    require(type(permit) is MediaPermit)
    with permit._local_lock():permit._check(permit.binding);require(not permit._closing)
    if kinds is not None:require(permit.binding.kind in kinds)
    if subject is not None:require(type(subject) is str and permit.binding.subject_id==subject)
    if attempt is not None:require(type(attempt) is str and permit.binding.attempt_id==attempt)
    return permit


def optional_permit():return _CURRENT.get()


@contextmanager
def operation(permit):
    current_permit(permit);old=_CURRENT.get()
    require(old is None or old is permit or old.binding.kind=='maintenance')
    token=_CURRENT.set(permit)
    try:yield permit
    finally:_CURRENT.reset(token)


async def validate(permit):
    current_permit(permit);permit._control._verify_physical_domain()
    async with permit._control.connection_factory() as connection:
        async with connection.transaction():await assert_transaction_permit(connection,permit,permit.binding)


class GuardedDatabase:
    """Only media's DB view; ordinary unscoped reads/text retain original DB API."""
    def __init__(self,database):self.database=database
    def __getattr__(self,name):return getattr(self.database,name)
    @asynccontextmanager
    async def connection(self):
        permit=_CURRENT.get()
        if permit is not None:permit._control._verify_physical_domain()
        async with self.database.connection() as connection:
            if permit is not None:await assert_transaction_permit(connection,current_permit(permit),permit.binding)
            yield connection


def mutation(kinds,*,subject=None,attempt=None):
    """Guard a concrete mutation before parsing inputs or taking business locks."""
    kinds=frozenset([kinds] if type(kinds) is str else kinds)
    def decorate(function):
        signature=inspect.signature(function)
        @functools.wraps(function)
        async def guarded(*args,**kwargs):
            explicit=kwargs.pop('permit',None);permit=current_permit(explicit,kinds=kinds)
            bound=signature.bind(*args,**kwargs);bound.apply_defaults();values=bound.arguments
            def value(selector):return selector(values) if callable(selector) else values.get(selector)
            current_permit(permit,kinds=kinds,subject=value(subject) if subject is not None else None,attempt=value(attempt) if attempt is not None else None)
            if values.get('c') is not None:await assert_transaction_permit(values['c'],permit,permit.binding)
            else:await validate(permit)
            with operation(permit):return await function(*args,**kwargs)
        guarded.__media_guard__=(tuple(sorted(kinds)),subject if type(subject) is str else bool(subject),attempt if type(attempt) is str else bool(attempt))
        return guarded
    return decorate


def mutation_context(kinds,*,subject=None,attempt=None):
    kinds=frozenset([kinds] if type(kinds) is str else kinds)
    def decorate(factory):
        signature=inspect.signature(factory)
        @asynccontextmanager
        @functools.wraps(factory)
        async def guarded(*args,**kwargs):
            permit=current_permit(kwargs.pop('permit',None),kinds=kinds)
            bound=signature.bind(*args,**kwargs);bound.apply_defaults();values=bound.arguments
            def value(selector):return selector(values) if callable(selector) else values.get(selector)
            current_permit(permit,subject=value(subject) if subject is not None else None,attempt=value(attempt) if attempt is not None else None)
            await validate(permit)
            with operation(permit):
                async with factory(*args,**kwargs) as result:yield result
        guarded.__media_guard__=(tuple(sorted(kinds)),subject if type(subject) is str else bool(subject),attempt if type(attempt) is str else bool(attempt))
        guarded.__media_context_guard__=True
        return guarded
    return decorate


def runtime_boundary(function):
    @functools.wraps(function)
    async def guarded(self,*args,**kwargs):
        self._maintenance()._check()
        return await function(self,*args,**kwargs)
    guarded.__media_runtime_boundary__=True
    return guarded


def runtime_lifecycle(stage):
    require(stage in {'start','close'})
    def decorate(function):
        @functools.wraps(function)
        async def guarded(self,*args,**kwargs):
            require(self.maintenance is None or type(self.maintenance) is RuntimeGeneration)
            result=await function(self,*args,**kwargs)
            if stage=='start' and self.maintenance is not None and self.maintenance._started:self.maintenance._check()
            if stage=='close':require(all(task.done()for task in self.tasks))
            return result
        guarded.__media_lifecycle_guard__=stage
        return guarded
    return decorate


def physical_directory_guard(factory):
    signature=inspect.signature(factory)
    @functools.wraps(factory)
    def guarded(*args,**kwargs):
        bound=signature.bind(*args,**kwargs);bound.apply_defaults()
        if bound.arguments['create']:
            ref=require_physical();ref._permit._control._source.verify_path(bound.arguments['self'].root)
        return factory(*args,**kwargs)
    guarded.__media_directory_guard__=True
    return guarded


def require_physical():
    permit=current_permit(kinds={'job','upload','gc'});permit._control._verify_physical_domain()
    reference=_PHYSICAL.get();require(type(reference) is PhysicalRef and reference._permit is permit)
    reference.start();return reference


def physical_guard(function):
    receiver=next(iter(inspect.signature(function).parameters),None)=='self'
    if inspect.iscoroutinefunction(function):
        @functools.wraps(function)
        async def guarded(*args,**kwargs):
            reference=require_physical()
            if receiver and args:
                root=getattr(args[0],'root',getattr(getattr(args[0],'store',None),'root',None))
                if root is not None:reference._permit._control._source.verify_path(root)
            return await function(*args,**kwargs)
    else:
        @functools.wraps(function)
        def guarded(*args,**kwargs):
            reference=require_physical()
            if receiver and args:
                root=getattr(args[0],'root',getattr(getattr(args[0],'store',None),'root',None))
                if root is not None:reference._permit._control._source.verify_path(root)
            return function(*args,**kwargs)
    guarded.__media_physical_guard__=True
    return guarded


async def authorize_write(permit,metadata,*,action='write',connection=None):
    current_permit(permit,kinds={'job','upload'});permit._control._verify_physical_domain()
    require(type(metadata) is dict and action in {'write','discard'})
    required=('storage_key','sha256','size','mime','properties');require(all(k in metadata for k in required))
    async def check(c):
        await assert_transaction_permit(c,permit,permit.binding)
        value=await(await c.execute("SELECT jsonb_build_object('storage_key',storage_key,'sha256',sha256,'size',size,'mime',mime,'properties',properties,'kind',kind,'invocation_id',invocation_id,'job_id',job_id,'attempt_id',attempt_id,'state',state,'attached_id',attached_id) AS binding FROM media_blobs WHERE storage_key=%s",(metadata['storage_key'],))).fetchone()
        row=(value['binding'] if isinstance(value,dict) else value[0]) if value is not None else None
        require(type(row) is dict and all(row[k]==metadata[k]for k in required))
        if permit.binding.kind=='upload':require(row['kind']=='asset' and row['invocation_id']==permit.binding.subject_id)
        else:require(row['kind']=='artifact' and row['job_id']==permit.binding.subject_id and row['attempt_id']==permit.binding.attempt_id)
        require(row['state'] in {'staged','attached'})
        require(await permit._control.blob_writer_owned(c,permit,metadata['storage_key']) is True)
        if action=='discard':require(row['state']=='staged' and row['attached_id'] is None)
        return _mint_write_fence(permit,{k:row[k]for k in required},action=action)
    if connection is not None:return await check(connection)
    async with permit._control.connection_factory() as c:
        async with c.transaction():return await check(c)


async def physical_call(function,*args,permit=None,connection=None,**kwargs):
    permit=current_permit(permit,kinds={'job','upload','gc'})
    if connection is None:await validate(permit)
    else:await assert_transaction_permit(connection,permit,permit.binding)
    name=getattr(function,'__qualname__','').split('.')[-1]
    metadata=None;action='write'
    if name=='_put_verified':
        require(len(args)>=2);metadata={'storage_key':kwargs.get('storage_key'),**args[1]}
    elif name=='_write_source':require(len(args)>=2);metadata=args[1]
    elif name in {'_promote_partial','discard'}:
        require(args);metadata=args[0];action='discard' if name=='discard' else 'write'
    if metadata is not None:
        require('fence' not in kwargs);kwargs['fence']=await authorize_write(permit,metadata,action=action,connection=connection)
    if name=='put_image':require(type(kwargs.get('fence')) is WriteFence)
    reference=permit.retain_physical(permit.binding)
    def enter():
        current_permit(permit);reference.start();return _PHYSICAL.set(reference)
    if inspect.iscoroutinefunction(function):
        try:
            with operation(permit):
                token=enter()
                try:return await function(*args,**kwargs)
                finally:_PHYSICAL.reset(token)
        finally:reference.close()
    def actual():
        try:
            with operation(permit):
                token=enter()
                try:return function(*args,**kwargs)
                finally:_PHYSICAL.reset(token)
        finally:reference.close()
    work=asyncio.create_task(asyncio.to_thread(actual))
    # Shield only the actual worker. Its ref is closed by actual(), never by a
    # cancelled parent, even if the OS syscall/thread has not returned yet.
    try:return await asyncio.shield(work)
    except BaseException:
        permit.invalidate()
        def consumed(task):
            if not task.cancelled():task.exception()
        work.add_done_callback(consumed);raise


class RuntimeGeneration:
    def __init__(self,database,locks,*,source):
        require(type(locks) is ControlLock and type(source) is SourceRoot)
        # Control RPCs validate their own original capability. Reusing media's
        # operation-scoped wrapper here would reject a deliberately sealed
        # permit before its finish/observe RPC can resolve that exact object.
        while type(database) is GuardedDatabase:database=database.database
        self.database=database;self.locks=locks;self.source=source;self.identity=worker_identity()
        self.control=DatabaseControl(database.connection,locks=locks)
        self._permits={};self._gc_claimed=set();self._gc_rows={};self._started=False;self._closing=False;self._pid=os.getpid();self._admission=asyncio.Lock()
    @classmethod
    def local(cls,database,*,storage_root):
        """Same generation API backed by in-process control (media maintenance="local")."""
        from .local_control import LocalControl,LocalLocks,LocalSource
        while type(database) is GuardedDatabase:database=database.database
        self=cls.__new__(cls)
        self.database=database;self.locks=LocalLocks();self.source=LocalSource(storage_root);self.identity=worker_identity()
        self.control=LocalControl(database.connection,self.source)
        self._permits={};self._gc_claimed=set();self._gc_rows={};self._started=False;self._closing=False;self._pid=os.getpid();self._admission=asyncio.Lock()
        return self
    @property
    def permits(self):require(os.getpid()==self._pid);return tuple(self._permits.values())
    def _check(self):
        require(os.getpid()==self._pid and self._started and not self._closing)
        self.control._verify_physical_domain()
    async def start(self):
        require(os.getpid()==self._pid and not self._started and not self._closing)
        self.locks.verify();self.source.verify()
        await self.control.register_worker(self.identity);await self.control.attest_worker(self.identity,self.source);self._started=True
    async def acquire(self,kind,subject,attempt=None,*,upstream_sha256=None):
        self._check();binding=OperationBinding(kind,subject,attempt,self.identity,upstream_sha256);key=(kind,subject,attempt)
        async with self._admission:
            self._check()
            # An exact job/GC attempt is the long-lived logical operation. Every
            # new upload/mint/catalog/policy/scan request needs a fresh nonce,
            # even if another request currently targets the same invocation.
            old=next((p for p in self._permits.values() if (p.binding.kind,p.binding.subject_id,p.binding.attempt_id)==key),None) if kind in {'job','gc'} else None
            if old is not None:
                require(old.binding==binding);await validate(old);return old
            permit=await admit_operation(self.control,self.locks,binding)
            self._permits[permit.token_digest]=permit;return permit
    def owned(self,kind,subject,attempt=None):
        self._check();require(kind in {'job','gc'})
        found=[p for p in self._permits.values() if (p.binding.kind,p.binding.subject_id,p.binding.attempt_id)==(kind,subject,attempt)]
        require(len(found)==1);permit=found[0]
        return current_permit(permit,kinds={kind},subject=subject,attempt=attempt)
    async def finish(self,permit,facts):
        self._check();require(permit in self._permits.values() and type(facts) is dict)
        raw=json.dumps(facts,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()
        await close_permit(self.control,permit,stable_business_digest=hashlib.sha256(raw).hexdigest())
        self._permits.pop(permit.token_digest)
    async def observe(self,permit):
        self._check();require(permit in self._permits.values());result=await observe_permit(self.control,permit)
        if result['state']=='completed':self._permits.pop(permit.token_digest)
        return result
    async def unknown(self,permit):
        require(os.getpid()==self._pid and permit in self._permits.values())
        await self.control.mark_unknown(permit)
    async def finish_job_if_stable(self,permit):
        self._check();require(permit.binding.kind=='job')
        if permit._closed or not permit._valid:return False
        with operation(permit):
            async with GuardedDatabase(self.database).connection() as c:
                row=await(await c.execute('SELECT status FROM media_jobs WHERE id=%s',(permit.binding.subject_id,))).fetchone()
                status=None if row is None else (row['status'] if isinstance(row,dict) else row[0])
                if status is not None and status not in {'settled','cancelled','released'}:return False
                exists=await(await c.execute("SELECT to_regclass(current_schema()||'.media_work')")).fetchone()
                has_work=(next(iter(exists.values())) if isinstance(exists,dict) else exists[0]) is not None
                if has_work:
                    work=await(await c.execute("SELECT 1 FROM media_work WHERE job_id=%s AND phase<>'done'",(permit.binding.subject_id,))).fetchone()
                    if work:return False
            await self.finish(permit,{'job':permit.binding.subject_id,'attempt':permit.binding.attempt_id,'status':status})
        return True

    @asynccontextmanager
    async def use(self,kind,subject,attempt=None,*,transient=False):
        permit=await self.acquire(kind,subject,attempt)
        try:
            with operation(permit):yield permit
        except BaseException:
            if kind=='maintenance' and not permit.physical_count:
                await asyncio.shield(self.finish(permit,{'scan':'finished'}))
            elif transient and permit in self._permits.values():await asyncio.shield(self.unknown(permit))
            elif kind=='job' and permit in self._permits.values() and permit._valid:
                await asyncio.shield(self.finish_job_if_stable(permit))
            raise
        else:
            if transient:await self.finish(permit,{'kind':kind,'subject':subject})
            elif kind=='job' and permit in self._permits.values() and permit._valid:
                await self.finish_job_if_stable(permit)
    async def close(self):
        require(os.getpid()==self._pid and self._started)
        require(not self._permits)
        self._closing=True;await self.control.unregister_worker(self.identity);self._started=False
