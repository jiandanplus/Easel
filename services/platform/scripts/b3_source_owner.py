"""Explicit source ownership for the Root once caller. No automatic finalizers.

Import is stdlib-only. Root supplies the already validated exact source authority.
All connection/setup/cleanup attempts remain private, including exceptions.
"""
from contextlib import contextmanager, asynccontextmanager
import os
import json
import stat
from pathlib import Path
import hashlib
import secrets
import sys
import traceback
from uuid import uuid4


def _replay_pin(value):
    from dataclasses import asdict
    return hashlib.sha256(json.dumps(asdict(value),sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


class _OwnedReplayAuthorization:
    """Original source-owned independent choice; DATA alone grants nothing."""
    def __init__(self,owner,data,comparison,root,material,selection):
        self.owner=owner;self.data=data;self.original_comparison=comparison
        self.original_root_authorization=root;self.authentication_material=dict(material)
        self.selection=selection;self.grant_graph_scope=data.grant_graph_scope
        self._data_pin=_replay_pin(data);self._comparison_pin=_replay_pin(comparison)
        self._material_pin=tuple(sorted((k,hashlib.sha256(v).hexdigest()) for k,v in material.items()))
        self._selection_pin=_replay_pin(selection)
        self._event=owner.cancellation;self._clock=owner.check_budget
        self._root=owner._source_authority
    def __repr__(self):return '<OwnedReplayAuthorization redacted>'
    def __copy__(self):raise TypeError('original replay authorization')
    def __deepcopy__(self,memo):raise TypeError('original replay authorization')
    def __reduce_ex__(self,p):raise TypeError('original replay authorization')
    def _verify(self):
        if (getattr(self.owner,'_replay_authorization',None) is not self
                or self.owner._source_authority is not self._root or self.original_root_authorization is not self._root
                or self.owner.cancellation is not self._event or self.owner.check_budget is not self._clock
                or getattr(self.owner,'_replay_selection',None) is not self.selection
                or _replay_pin(self.data)!=self._data_pin or _replay_pin(self.original_comparison)!=self._comparison_pin
                or _replay_pin(self.selection)!=self._selection_pin or self.data.grant_graph_scope is not self.grant_graph_scope
                or tuple(sorted((k,hashlib.sha256(v).hexdigest()) for k,v in self.authentication_material.items()))!=self._material_pin):
            raise RuntimeError('original replay authorization refused')


class SourceOwner:
    def __init__(self, authority, migrations_dir, check_budget, cancellation):
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import make_conninfo
        self._source_authority=authority
        self.driver=psycopg;self.sql=sql;self.make_conninfo=make_conninfo
        self.check_budget=check_budget;self.cancellation=cancellation
        self.migrations_dir=migrations_dir
        self.base=make_conninfo(host=authority.host,hostaddr=authority.host,
            port=str(authority.port),dbname=authority.database,user=authority.user,
            password=authority.password,connect_timeout="5",sslmode="disable",
            application_name="easel_b2_owned_tests")
        suffix=uuid4().hex[:16];self.name="easel_s1_test_b2r_"+suffix
        self.roles={k:"b2r_"+k+"_"+suffix for k in ("owner","runtime","controller")}
        self.passwords={k:secrets.token_urlsafe(32) for k in self.roles}
        self.container_id=authority.container_id;self.migrations=[]
        self.attempts=[];self.connections=[];self.resources=[]
        self.active=0;self.unknown=False;self.phase='NEW';self.cleanup_attempted=False
        self.journal=None;self.journal_sequence=0;self.journal_fds=[];self.journal_attempts=[]

    def bind_replay_selection(self,selection,*,original_root_authorization):
        from platform_service.media_backup_b3.roles import ReplaySelection
        if (type(selection) is not ReplaySelection or original_root_authorization is not self._source_authority
                or getattr(self,'_replay_selection',None) is not None or getattr(self,'_reconstruction_producer',None) is not None
                or self.phase!='READY' or self.active or self.unknown or self.cleanup_attempted):
            raise RuntimeError('replay selection refused')
        if (selection.source_container_id!=self.container_id or selection.database_name!=self.name
                or tuple(sorted(self.roles.values()))!=selection.business_role_names):
            raise RuntimeError('replay selection binding refused')
        selection.__post_init__()
        self._replay_selection=selection;self._replay_selection_pin=_replay_pin(selection)
        return selection

    def bind_replay_authorization(self,data,*,original_comparison,original_root_authorization,authentication_material):
        from platform_service.media_backup_b3.roles import ReplayAuthorizationData
        from platform_service.media_backup_b3.contracts import ComparisonContext
        selection=getattr(self,'_replay_selection',None)
        if (type(data) is not ReplayAuthorizationData or type(original_comparison) is not ComparisonContext
                or original_root_authorization is not self._source_authority or selection is None
                or getattr(self,'_replay_authorization',None) is not None or getattr(self,'_reconstruction_producer',None) is not None
                or self.phase!='READY' or self.active or self.unknown or self.cleanup_attempted):
            raise RuntimeError('replay authorization refused')
        data.__post_init__()
        if (data.source_container_id!=self.container_id or data.database_name!=self.name
                or data.kind!=original_comparison.source_kind or data.kind!=selection.kind
                or data.system_identifier_sha256!=selection.system_identifier_sha256
                or data.policy_record_sha256!=selection.policy_record_sha256
                or original_comparison.role_names!=selection.replay_role_names
                or original_comparison.allowed_grantors!=selection.allowed_grantors
                or data.started_at!=self.binding['started'] or data.deadline!=self.binding['deadline']
                or _replay_pin(selection)!=self._replay_selection_pin
                or type(authentication_material) is not dict
                or set(authentication_material)!={a.credential_id for a in data.authentication}):
            raise RuntimeError('replay authorization binding refused')
        for a in data.authentication:
            secret=authentication_material[a.credential_id]
            if type(secret) is not bytes or not 1<=len(secret)<=4096 or hashlib.sha256(secret).hexdigest()!=a.credential_sha256:
                raise RuntimeError('private authentication binding refused')
        auth=_OwnedReplayAuthorization(self,data,original_comparison,original_root_authorization,authentication_material,selection)
        self._replay_authorization=auth
        return auth

    def begin_reconstruction(self,scope,*,original_read_context,original_comparison,
            started_at_ns,deadline_ns,cancellation):
        from platform_service.media_backup_b2.reconstruction_capture import OwnedSourceProducer
        from platform_service.media_backup_b2.reconstruction_context import SourceReadScope
        from psycopg.conninfo import conninfo_to_dict
        self.guard()
        if (self.phase!='READY' or self.cleanup_attempted or self.active or self.unknown
                or getattr(self,'_reconstruction_producer',None) is not None
                or type(scope) is not SourceReadScope or original_read_context is not scope or cancellation is not self.cancellation):
            raise RuntimeError('source reconstruction refused')
        if (int(self.binding['started']*1e9)!=started_at_ns or int(self.binding['deadline']*1e9)!=deadline_ns
                or scope.database_name!=self.name or scope.container_id!=self.container_id):
            raise RuntimeError('source reconstruction binding refused')
        ci=conninfo_to_dict(self.base)
        if (ci.get('host')!='127.0.0.1' or ci.get('hostaddr')!='127.0.0.1'
                or ci.get('user')!=scope.connection_admin
                or not {scope.connection_admin,self.roles['owner']}<=set(scope.selected_role_names)):
            raise RuntimeError('source reconstruction principal refused')
        # Intent is durable before any B2 read. A failed factory retains it.
        from dataclasses import asdict
        auth=getattr(self,'_replay_authorization',None)
        if auth is not None:
            auth._verify()
            if auth.original_comparison is not original_comparison:raise RuntimeError('replay comparison refused')
        self._reconstruction_comparison=original_comparison
        self._reconstruction_comparison_pin=hashlib.sha256(json.dumps(asdict(original_comparison),sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
        self._reconstruction_attempt=self.begin('reconstruction_capture')
        try:
            producer=OwnedSourceProducer._from_source_owner(self,scope,
                original_read_context=original_read_context,original_comparison=original_comparison,
                source_connection=self.make_conninfo(self.base,dbname=self.name),
                started_at_ns=started_at_ns,deadline_ns=deadline_ns,cancellation=cancellation,
                grant_graph_scope=None if auth is None else auth.grant_graph_scope)
            self._reconstruction_producer=producer
            return producer
        except BaseException as error:
            self.failed(self._reconstruction_attempt,error);raise

    def _finish_reconstruction(self,producer,expected):
        from platform_service.media_backup_b2.reconstruction_capture import OwnedSourceProducer
        import time
        if (type(producer) is not OwnedSourceProducer or getattr(self,'_reconstruction_producer',None) is not producer
                or producer._owner is not self or self.phase!='READY' or self.cleanup_attempted or self.unknown
                or self.cancellation.is_set() or time.monotonic_ns()>=producer._deadline):
            raise RuntimeError('source reconstruction finish refused')
        from dataclasses import asdict
        pin=hashlib.sha256(json.dumps(asdict(producer._comparison),sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
        if producer._comparison is not self._reconstruction_comparison or pin!=self._reconstruction_comparison_pin:
            raise RuntimeError('source reconstruction comparison refused')
        attempt=self._reconstruction_attempt;pair=producer._pair
        if attempt['state']!='ACTIVE' or self.active!=1 or pair is None:
            raise RuntimeError('source reconstruction finish refused')
        pair._verify_bindings();pair._require_read_terminal()
        with pair._lock, pair._inputs._work_lock:
            if (pair._terminal_state!=expected or pair._inputs._active_work or pair._inputs._work_unknown
                    or pair._controller.pending_window_ids or pair._custody_unknown or pair._cleanup_unknown
                    or pair._phase_deadline is None or time.monotonic_ns()>=pair._phase_deadline
                    or any(not p._terminal() for p in pair._producers)):
                raise RuntimeError('source reconstruction finish refused')
            if expected=='TERMINAL_SUCCESS' and (producer._rejected or pair._state['cancelled'].is_set()):
                raise RuntimeError('source reconstruction finish refused')
            if expected=='FAILED_TERMINAL' and not any(p._rejected for p in pair._producers):
                raise RuntimeError('source reconstruction failure refused')
            # Journal ambiguity retains legacy UNKNOWN/active semantics.
            try:self.record(dict(operation='reconstruction_capture',state=expected,
                source_read_terminal=True,publication_retained=bool(pair._slots)))
            except BaseException as error:self.failed(attempt,error);raise
            self.done(attempt)
            attempt['capture_outcome']=expected

    def finish_reconstruction(self,exact_producer):
        self._finish_reconstruction(exact_producer,'TERMINAL_SUCCESS')

    def finish_known_failed_reconstruction(self,exact_producer):
        self._finish_reconstruction(exact_producer,'FAILED_TERMINAL')

    def __repr__(self):return '<ExplicitSourceOwner>'

    def bind_journal(self,path,binding):
        if self.journal is not None:raise RuntimeError('ownership already bound')
        self.journal=Path(path);self.binding=dict(binding)
        try:
            self.journal.mkdir(mode=0o700,exist_ok=True)
            st=self.journal.lstat()
            if not stat.S_ISDIR(st.st_mode) or st.st_uid!=os.getuid() or stat.S_IMODE(st.st_mode)!=0o700:
                raise RuntimeError('private ownership directory required')
            self.journal_fd=os.open(self.journal,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            self.journal_fds.append(self.journal_fd)
            self.journal_parent_fd=os.open(self.journal.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            self.journal_fds.append(self.journal_parent_fd)
            parent_attempt=dict(operation='journal_parent_fsync',fd=self.journal_parent_fd,state='ACTIVE')
            self.journal_attempts.append(parent_attempt)
            try:os.fsync(self.journal_parent_fd)
            except BaseException as error:
                parent_attempt.update(state='UNKNOWN',error=error,exc_info=sys.exc_info());raise
            parent_attempt['state']='TERMINAL'
            self.record(dict(operation='ownership',database=self.name,roles=self.roles))
        except BaseException:
            self.unknown=True;raise

    def record(self,value):
        if self.journal is None:raise RuntimeError('durable ownership required')
        record=dict(binding=self.binding,source_database=self.name,**value)
        raw=json.dumps(record,sort_keys=True,separators=(',',':')).encode()
        if len(raw)>8192 or self.journal_sequence>=10000:raise RuntimeError('ownership journal bound')
        name=self.name+'-'+str(self.journal_sequence)+'.json';self.journal_sequence+=1
        attempt=dict(path=name,state='ACTIVE');self.journal_attempts.append(attempt)
        try:
            fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=self.journal_fd)
            attempt['fd']=fd;self.journal_fds.append(fd)
            if os.write(fd,raw)!=len(raw):raise RuntimeError('ownership journal short write')
            os.fsync(fd);os.fsync(self.journal_fd)
            # Record release BEFORE close and never retry an ambiguous close.
            attempt['state']='CLOSING';os.close(fd)
            self.journal_fds.remove(fd);attempt['state']='TERMINAL'
        except BaseException as error:
            attempt.update(state='UNKNOWN',error=error,exc_info=sys.exc_info())
            self.unknown=True;raise

    def guard(self):
        if self.unknown or self.cancellation.is_set():raise RuntimeError('source work refused')
        self.check_budget()

    def begin(self,operation,connection=None,resource=None):
        self.guard()
        attempt=dict(operation=operation,connection=connection,resource=resource,state='ACTIVE')
        self.attempts.append(attempt);self.active+=1
        try:self.record(dict(operation=operation,state='ACTIVE',attempt=len(self.attempts)-1,resource=resource if isinstance(resource,dict) else None))
        except BaseException as error:self.failed(attempt,error);raise
        return attempt

    def failed(self,attempt,error):
        attempt.update(state='UNKNOWN',error=error,exc_info=sys.exc_info(),trace=traceback.format_exc())
        self.unknown=True
        # active remains nonzero: interrupt is not proof of remote termination.

    def done(self,attempt):
        try:self.record(dict(operation=attempt['operation'],state='TERMINAL',attempt=self.attempts.index(attempt),backend_pid=attempt.get('backend_pid')))
        except BaseException as error:self.failed(attempt,error);raise
        attempt['state']='TERMINAL';self.active-=1

    def call(self,operation,fn,connection=None,resource=None):
        attempt=self.begin(operation,connection,resource)
        try:
            value=fn();attempt['result']=value
        except BaseException as error:
            self.failed(attempt,error);raise
        self.done(attempt);return value

    async def acall(self,operation,fn,connection=None):
        attempt=self.begin(operation,connection)
        try:
            value=await fn();attempt['result']=value
        except BaseException as error:
            self.failed(attempt,error);raise
        self.done(attempt);return value

    def dsn(self,role):
        return self.make_conninfo(self.base,dbname=self.name,user=self.roles[role],password=self.passwords[role])

    def connect(self,dsn,autocommit=False):
        # Retain attempt before native connection entry, including missing handle.
        a=self.begin('connect')
        try:
            c=self.driver.connect(dsn,autocommit=autocommit)
            a['connection']=c;self.connections.append(c)
            a['backend_pid']=c.info.backend_pid
        except BaseException as error:
            self.failed(a,error);raise
        self.done(a)
        return c

    def execute(self,c,query,*args,resource=None,**kw):
        return self.call('execute',lambda:c.execute(query,*args,**kw),c,resource)

    def limits(self,c):
        self.execute(c,"SET statement_timeout='5000ms'")
        self.execute(c,"SET lock_timeout='1000ms'")

    def close_connection(self,c):
        if any(a['operation']=='close' and a['connection'] is c for a in self.attempts):
            raise RuntimeError('source close already attempted')
        self.call('close',c.close,c)

    @contextmanager
    def sync_connection(self,role):
        c=self.connect(self.dsn(role),autocommit=True)
        a=self.begin('sync_context',c)
        try:
            self.limits(c)
            yield c
        except BaseException as error:
            self.failed(a,error);raise
        self.done(a)
        self.close_connection(c)

    @asynccontextmanager
    async def connection(self,role):
        a=self.begin('async_connect')
        try:
            c=await self.driver.AsyncConnection.connect(self.dsn(role),autocommit=False,options='-c statement_timeout=5000 -c lock_timeout=1000')
            a['connection']=c;self.connections.append(c);a['backend_pid']=c.info.backend_pid
        except BaseException as error:
            self.failed(a,error);raise
        self.done(a)
        context=self.begin('async_context',c)
        tracked=AsyncTracked(self,c)
        try:
            yield tracked
        except BaseException as error:
            self.failed(context,error);raise
        self.done(context)
        await self.acall('close',c.close,c)

    def factory(self,role):return lambda:self.connection(role)

    def prepare(self):
        if self.phase!='NEW':raise RuntimeError('source preparation already attempted')
        self.phase='PREPARING'
        # Exact frozen PUBLIC016 setup recipe, with explicit commit and ownership.
        c=self.connect(self.base,autocommit=True);self.limits(c)
        for kind,role in self.roles.items():
            resource=dict(kind='role',name=role);self.resources.append(resource)
            self.execute(c,self.sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                self.sql.Identifier(role),self.sql.Literal(self.passwords[kind])),resource=resource)
        resource=dict(kind='database',name=self.name);self.resources.append(resource)
        self.execute(c,self.sql.SQL("CREATE DATABASE {} OWNER {}").format(
            self.sql.Identifier(self.name),self.sql.Identifier(self.roles['owner'])),resource=resource)
        # Keep setup connections until ALL setup succeeds: no close on partial setup.
        owner=self.connect(self.dsn('owner'));self.limits(owner)
        self.execute(owner,"CREATE TABLE public.schema_migrations(version text PRIMARY KEY,checksum text NOT NULL)")
        paths=sorted(self.migrations_dir.glob('*.sql'))[:16]
        if len(paths)!=16:raise RuntimeError('PUBLIC016 migration recipe missing')
        for path in paths:
            raw=path.read_bytes();self.execute(owner,raw.decode(),prepare=False)
            digest=hashlib.sha256(raw).hexdigest()
            self.execute(owner,"INSERT INTO public.schema_migrations VALUES(%s,%s)",(path.name,digest))
            self.migrations.append((path.name,digest))
        I=self.sql.Identifier;S=self.sql.SQL
        self.execute(owner,S("GRANT USAGE ON SCHEMA public TO {},{}").format(I(self.roles['runtime']),I(self.roles['controller'])))
        self.execute(owner,S("GRANT SELECT,INSERT,UPDATE,DELETE,TRUNCATE ON ALL TABLES IN SCHEMA public TO {}").format(I(self.roles['runtime'])))
        self.execute(owner,S("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(I(self.roles['runtime'])))
        self.call('commit',owner.commit,owner)
        self.close_connection(owner);self.close_connection(c)
        self.phase='READY'
        return self

    def cleanup(self,graph):
        # No foreign, duplicate, partial, uncertain, concurrent, or implicit cleanup.
        if (self.cleanup_attempted or self.phase!='READY' or self.active or self.unknown
                or not any(x is self for x in graph.get('source_owners',[]))
                or graph.get('cleanup_phase')!='SOURCE'
                or graph.get('cleanup_current') is not self):
            raise RuntimeError('source cleanup refused')
        producer=getattr(self,'_reconstruction_producer',None)
        if producer is not None and producer._pair._consumers:raise RuntimeError('source consumer retained')
        self.cleanup_attempted=True;self.phase='CLEANING'
        c=self.connect(self.base,autocommit=True);self.limits(c)
        cursor=self.execute(c,"SELECT pid FROM pg_stat_activity WHERE datname=%s",(self.name,))
        rows=self.call('fetchall',cursor.fetchall,c)
        self.live_backends=tuple(rows)
        if rows:
            self.record(dict(operation='unexpected_source_backends',backend_pids=[row[0] for row in rows]))
            self.unknown=True
            raise RuntimeError('source backends remain')
        self.execute(c,self.sql.SQL('DROP DATABASE {}').format(self.sql.Identifier(self.name)))
        for role in reversed(tuple(self.roles.values())):
            self.execute(c,self.sql.SQL('DROP ROLE {}').format(self.sql.Identifier(role)))
        self.close_connection(c)
        self.record(dict(operation='cleanup',state='TERMINAL'))
        for fd in (self.journal_fd,self.journal_parent_fd):
            a=dict(operation='journal_directory_close',fd=fd,state='ACTIVE');self.journal_attempts.append(a)
            try:os.close(fd)
            except BaseException as error:
                a.update(state='UNKNOWN',error=error,exc_info=sys.exc_info());self.unknown=True;raise
            self.journal_fds.remove(fd);a['state']='TERMINAL'
        self.phase='CLOSED'


class AsyncTracked:
    """Keep exact native operation/cursor and original connection in the owner."""
    def __init__(self,owner,connection):self.owner=owner;self.connection=connection
    def __getattr__(self,name):return getattr(self.connection,name)
    async def execute(self,*args,**kw):
        return await self.owner.acall('execute',lambda:self.connection.execute(*args,**kw),self.connection)
    async def commit(self):return await self.owner.acall('commit',self.connection.commit,self.connection)
    async def rollback(self):return await self.owner.acall('rollback',self.connection.rollback,self.connection)

    @asynccontextmanager
    async def transaction(self,*args,**kw):
        # Never invoke native rollback/exit while the body result is uncertain.
        tx=self.connection.transaction(*args,**kw)
        context=self.owner.begin('transaction_context',self.connection,tx)
        try:
            result=await self.owner.acall('transaction_enter',tx.__aenter__,self.connection)
            yield result
        except BaseException as error:
            self.owner.failed(context,error);raise
        await self.owner.acall('transaction_exit',lambda:tx.__aexit__(None,None,None),self.connection)
        self.owner.done(context)
