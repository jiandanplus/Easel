"""Private owned source READ capture. No import-time IO or replay authority.

Only the fixed B2 sites drive this companion. Python identity seals describe
internal ownership discipline, not a sandbox against hostile Python callers.
"""
import copy
from dataclasses import asdict, is_dataclass
import hashlib
import json
import os
import stat
import threading
import time

from .contracts import B2Error, require
from .reconstruction_context import SourceReadScope, SourcePhase, parse_source_sample

_SEAL = object()
_RENDER = "SELECT current_setting('TimeZone'), current_setting('DateStyle');"
_IDENTITY = """SELECT current_database(),(SELECT system_identifier::text FROM pg_catalog.pg_control_system()),
 current_setting('server_version_num'),pg_backend_pid(),pg_current_snapshot()::text,
 current_setting('transaction_read_only'),current_setting('transaction_isolation'),
 current_setting('TimeZone'),current_user,session_user,
 (SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user),
 (SELECT oid FROM pg_catalog.pg_roles WHERE rolname=session_user);"""
_ROLES = """SELECT a.oid,a.rolname,a.rolsuper,a.rolinherit,a.rolcreaterole,a.rolcreatedb,
 a.rolcanlogin,a.rolreplication,a.rolconnlimit,a.rolvaliduntil::text,
 a.rolbypassrls,r.rolconfig,a.rolpassword,a.xmin::text
FROM pg_catalog.pg_authid a JOIN pg_catalog.pg_roles r ON r.oid=a.oid
WHERE a.rolname=ANY(%s) OR a.oid=10 ORDER BY a.rolname LIMIT 1025;"""
_DATABASE = """SELECT d.oid,d.datname,d.datdba,r.rolname,d.encoding,pg_encoding_to_char(d.encoding),
 d.datcollate,d.datctype,d.datlocprovider,d.daticulocale,d.daticurules,
 d.datcollversion,pg_database_collation_actual_version(d.oid),d.datacl::text,
 d.datconnlimit,d.datistemplate,d.datallowconn,t.spcname,d.xmin::text
FROM pg_catalog.pg_database d JOIN pg_catalog.pg_roles r ON r.oid=d.datdba
JOIN pg_catalog.pg_tablespace t ON t.oid=d.dattablespace
WHERE d.datname=current_database() LIMIT 2;"""
_SETTINGS = """SELECT s.setdatabase,s.setrole,r.rolname,s.setconfig,s.xmin::text
FROM pg_catalog.pg_db_role_setting s LEFT JOIN pg_catalog.pg_roles r ON r.oid=s.setrole
WHERE s.setdatabase=(SELECT oid FROM pg_catalog.pg_database WHERE datname=current_database())
 OR (s.setdatabase=0 AND (s.setrole=0 OR s.setrole IN
  (SELECT oid FROM pg_catalog.pg_roles WHERE rolname=ANY(%s) OR oid=10)))
ORDER BY s.setdatabase,s.setrole LIMIT 4097;"""


_GRANTS = """SELECT m.roleid,r.rolname,m.member,u.rolname,m.grantor,g.rolname,
 m.admin_option,m.inherit_option,m.set_option,m.xmin::text
FROM pg_catalog.pg_auth_members m
JOIN pg_catalog.pg_authid r ON r.oid=m.roleid
JOIN pg_catalog.pg_authid u ON u.oid=m.member
JOIN pg_catalog.pg_authid g ON g.oid=m.grantor
WHERE r.rolname=ANY(%s) OR u.rolname=ANY(%s)
ORDER BY r.rolname,u.rolname,g.rolname LIMIT 16385;"""


def _pin(value):
    return hashlib.sha256(json.dumps(asdict(value) if is_dataclass(value) else value,
        sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _connect_native(dsn, remaining):
    # Lazy import, raw autocommit connection: no validation SELECT before import.
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    ci=conninfo_to_dict(dsn)
    require(ci.get('host')=='127.0.0.1' and ci.get('hostaddr')=='127.0.0.1')
    require(not any(k in ci for k in ('service','servicefile','passfile','options')))
    require(all(type(ci.get(k)) is str and ci[k] for k in ('dbname','user','password','port')))
    require(remaining>=1)
    ms=int(remaining*1000)-10;require(ms>0)
    return psycopg.connect(dsn, autocommit=True, connect_timeout=min(5,int(remaining)),
        options='-c statement_timeout='+str(min(ms,5000))+' -c lock_timeout='+str(min(ms,1000)))


def _literal_command(shape, value):
    from psycopg import sql
    return sql.SQL(shape).format(sql.Literal(value))


def _command(con, query):
    cursor=con.execute(query)
    expected=str(query).split()[0] if type(query) is str else 'SET'
    require(cursor.statusmessage in ({'BEGIN'} if expected=='BEGIN' else {'ROLLBACK'} if expected=='ROLLBACK' else {'SET'}))
    return cursor.statusmessage


def _bounded(cursor, maximum, widths, budget):
    rows=[]
    while True:
        batch=cursor.fetchmany(min(64, maximum+1-len(rows)))
        require(type(batch) in (list,tuple))
        if not batch:break
        require(len(batch)<=min(64,maximum+1-len(rows)))
        for row in batch:
            require(type(row) in (list,tuple) and len(row)==len(widths))
            converted=[]
            for value,limit in zip(row,widths):
                if type(value) is list:
                    require(len(value)<=256)
                    value=tuple(value)
                values=value if type(value) is tuple else (value,)
                for item in values:
                    require(item is None or type(item) in (str,int,bool))
                    if type(item) is str:
                        require(len(item)<=limit and '\x00' not in item)
                        try:size=len(item.encode('utf-8'))
                        except UnicodeError:raise B2Error() from None
                        require(size<=limit)
                    else:size=16
                    budget[0]+=size;require(budget[0]<=8*1024**2)
                converted.append(value)
            rows.append(tuple(converted));require(len(rows)<=maximum)
    return tuple(rows)


class _Opaque:
    def __repr__(self):return '<'+type(self).__name__+' redacted>'
    def __copy__(self):raise TypeError('original ownership required')
    def __deepcopy__(self,memo):raise TypeError('original ownership required')
    def __reduce_ex__(self,p):raise TypeError('original ownership required')


class OwnedSourceProducer(_Opaque):
    def __init__(self, seal):require(seal is _SEAL);self._seal=seal

    @classmethod
    def _from_source_owner(cls, original_owner, scope, *, original_read_context,
            original_comparison, source_connection, started_at_ns, deadline_ns, cancellation, grant_graph_scope=None):
        require(cls is OwnedSourceProducer and type(scope) is SourceReadScope and original_read_context is scope)
        require(type(source_connection) is str and source_connection)
        require(type(started_at_ns) is int and type(deadline_ns) is int
                and started_at_ns<=time.monotonic_ns()<deadline_ns)
        require(scope.database_name==original_owner.name and scope.container_id==original_owner.container_id)
        authorization=getattr(original_owner,'_replay_authorization',None)
        if authorization is not None:
            authorization._verify()
            require(authorization.original_comparison is original_comparison)
            require(grant_graph_scope is authorization.grant_graph_scope)
        else:require(grant_graph_scope is None)
        if grant_graph_scope is not None:
            from .reconstruction_grants import GrantGraphScope
            require(type(grant_graph_scope) is GrantGraphScope and grant_graph_scope.kind==scope.kind)
            require(set(grant_graph_scope.role_names)<=set(scope.selected_role_names))
        result=cls(_SEAL)
        result._authorization=authorization;result._grant_scope=grant_graph_scope
        result._grant_scope_pin=None if grant_graph_scope is None else _pin(grant_graph_scope)
        result._grant_samples={}
        result._owner=original_owner;result._scope=scope;result._dsn=source_connection
        result._read_context=original_read_context;result._comparison=original_comparison
        result._read_pin=_pin(scope)
        result._started=started_at_ns;result._deadline=deadline_ns;result._cancel=cancellation
        result._lock=threading.RLock();result._samples={};result._attempts=[]
        result._pair=None;result._rendering=None;result._keeper=None
        result._unknown=False;result._rejected=False;result._error=None
        return result

    def _verify(self):
        require(type(self) is OwnedSourceProducer and self._seal is _SEAL)
        require(getattr(self._owner,'_reconstruction_producer',None) is self)
        require(self._read_context is self._scope and _pin(self._scope)==self._read_pin)
        require(getattr(self._owner,'_replay_authorization',None) is self._authorization)
        if self._authorization is not None:
            self._authorization._verify();require(self._authorization.grant_graph_scope is self._grant_scope)
        require(self._grant_scope_pin==(None if self._grant_scope is None else _pin(self._grant_scope)))
        require(not self._unknown and not self._rejected and not self._cancel.is_set())
        require(self._started<=time.monotonic_ns()<self._deadline)

    def _read(self, phase, observer):
        with self._lock:
            self._verify();pair=self._pair;pair._guard()
            require(tuple(self._samples)==tuple((SourcePhase.BEFORE,SourcePhase.DURING,SourcePhase.AFTER)[:len(self._samples)]))
            require(len(self._samples)<3)
            require(phase is (SourcePhase.BEFORE,SourcePhase.DURING,SourcePhase.AFTER)[len(self._samples)])
            require(len(self._attempts)==len(self._samples))
            a=dict(phase=phase,state='INTENT',ref=None,connection=None,backend_pid=None,acks=[],rows=None,limits=[],commands=[])
            self._attempts.append(a)
            try:
                a['ref']=pair._exclusive.retain();a['state']='STARTING';a['ref'].start();a['state']='ACTIVE'
                if phase is SourcePhase.BEFORE:
                    require(self._keeper is None);self._keeper=observer
                elif phase is SourcePhase.DURING:
                    require(observer is self._keeper and pair._inputs._keepers[self._scope.kind] is observer)
                else:
                    require(observer is not self._keeper)
                    # Borrowed from the actual fresh capture site; the capture
                    # owner alone closes it and reports that observation.
                    a['observer']=observer;a['observer_close']='BORROWED'
                budget=[0]
                rendering=_bounded(observer.execute(_RENDER),1,(1024,128),budget)
                require(len(rendering)==1 and all(type(v) is str and v for v in rendering[0]))
                a['rendering']=rendering[0]
                if self._rendering is None:self._rendering=rendering[0]
                else:require(rendering[0]==self._rendering)
                pair._guard()
                a['state']='CONNECTING'
                con=_connect_native(self._dsn,(min(self._deadline,pair._inputs._deadline)-time.monotonic_ns())/1e9)
                a['connection']=con;a['backend_pid']=con.info.backend_pid;a['state']='ACTIVE'
                a['acks'].append(_command(con,'BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY'))
                pair._guard()
                def refresh_limits():
                    pair._guard()
                    for kind,cap in (('statement_timeout',5000),('lock_timeout',1000)):
                        ms=min(cap,(min(self._deadline,pair._inputs._deadline)-time.monotonic_ns())//1_000_000-1)
                        require(ms>0)
                        command=dict(kind=kind,milliseconds=ms,state='ATTEMPTED')
                        a['limits'].append(command)
                        a['acks'].append(_command(con,"SET LOCAL "+kind+" TO '"+str(ms)+"ms'"))
                        command['state']='ACKNOWLEDGED';pair._guard()
                for shape,value in zip(('SET LOCAL TimeZone TO {}','SET LOCAL DateStyle TO {}'),self._rendering):
                    refresh_limits()
                    command=dict(shape=shape,value=value,parameter_sha256=hashlib.sha256(value.encode()).hexdigest(),state='ATTEMPTED')
                    a['commands'].append(command)
                    a['acks'].append(_command(con,_literal_command(shape,value)))
                    command['state']='ACKNOWLEDGED';pair._guard()
                kw={}
                if phase is SourcePhase.DURING:
                    kind=self._scope.kind;require(pair._inputs._keepers[kind] is observer)
                    kw=dict(keeper_snapshot_token=pair._inputs._keeper_snapshots[kind],
                            keeper_snapshot_text=pair._inputs._keeper_stamps[kind][1])
                    refresh_limits()
                    command=dict(shape='SET TRANSACTION SNAPSHOT {}',value=kw['keeper_snapshot_token'],
                        parameter_sha256=hashlib.sha256(kw['keeper_snapshot_token'].encode()).hexdigest(),state='ATTEMPTED')
                    a['commands'].append(command)
                    a['acks'].append(_command(con,_literal_command(command['shape'],command['value'])))
                    command['state']='ACKNOWLEDGED';pair._guard()
                rows=[]
                for query,params,maximum,widths in (
                    (_IDENTITY,None,1,(1024,1024,1024,1024,1024,1024,1024,1024,63,63,1024,1024)),
                    (_ROLES,(list(self._scope.selected_role_names),),1024,(1024,63,1024,1024,1024,1024,1024,1024,1024,1024,1024,8192,1024,1024)),
                    (_DATABASE,None,1,(1024,63,1024,63,1024,1024,1024,1024,1024,1024,65536,1024,1024,1024,1024,1024,1024,63,1024)),
                    (_SETTINGS,(list(self._scope.selected_role_names),),4096,(1024,1024,63,8192,1024))):
                    refresh_limits();rows.append(_bounded(con.execute(query,params),maximum,widths,budget));pair._guard()
                graph_rows=None
                if self._grant_scope is not None:
                    refresh_limits()
                    selected=list(self._grant_scope.role_names)
                    graph_rows=_bounded(con.execute(_GRANTS,(selected,selected)),16384,
                        (1024,63,1024,63,1024,63,1024,1024,1024,1024),budget)
                    pair._guard()
                a['rows']=tuple(rows);a['grant_rows']=graph_rows
                refresh_limits();a['acks'].append(_command(con,'ROLLBACK'));pair._guard();a['state']='CLOSING';con.close()
                require(con.closed);a['state']='NATIVE_TERMINAL'
                if phase is not SourcePhase.AFTER:
                    a['ref'].close();a['state']='TERMINAL'
            except BaseException as error:
                a.update(state='UNKNOWN',error=error);self._unknown=True;self._error=error
                raise B2Error() from None
            # Native end is known before DATA/equality rejection. Do not turn
            # bounded validation errors into an invented live native session.
            try:
                require(len(rows[0])==1)
                sample=parse_source_sample(self._scope,phase,rows[0][0],*rows[1:],**kw)
                require(sample.identity.timezone==self._rendering[0])
                if self._samples:require(sample.stable_private_value()==self._samples[SourcePhase.BEFORE].stable_private_value())
                graph=None
                if self._grant_scope is not None:
                    from .reconstruction_grants import parse_grant_graph
                    graph=parse_grant_graph(self._grant_scope,sample,graph_rows)
                    require(graph.scope is self._grant_scope)
                    if self._grant_samples:
                        require(graph.stable_private_value()==self._grant_samples[SourcePhase.BEFORE].stable_private_value())
            except Exception as error:
                self._rejected=True;self._error=error;a['validation']='REJECTED'
                raise B2Error() from None
            self._samples[phase]=sample
            if graph is not None:self._grant_samples[phase]=graph
            a['validation']='ACCEPTED'
            return sample

    def _observe_after_close(self, observer, error, read_error=None):
        with self._lock:
            require(self._attempts)
            a=self._attempts[-1]
            require(a['phase'] is SourcePhase.AFTER and a.get('observer') is observer
                and a.get('observer_close')=='BORROWED')
            if read_error is not None:
                a['observer_read_error']=read_error
                if not (a['state']=='NATIVE_TERMINAL' and a.get('validation')=='REJECTED'):
                    a['state']='UNKNOWN';self._unknown=True;self._error=read_error
            if error is not None:
                a.update(observer_close='UNKNOWN',observer_error=error,state='UNKNOWN')
                self._unknown=True;self._error=error
                return
            a['observer_close']='CLOSED'
            # A known close cannot repair an earlier ambiguous rendering/read.
            if a['state']=='NATIVE_TERMINAL':
                try:a['ref'].close();a['state']='TERMINAL'
                except BaseException as error:
                    a.update(state='UNKNOWN',error=error);self._unknown=True;self._error=error
                    raise B2Error() from None

    def _terminal(self):
        return not self._unknown and all(a['state']=='TERMINAL' for a in self._attempts)


class OwnedSourceCapture(_Opaque):
    def __init__(self, seal):require(seal is _SEAL);self._seal=seal

    @classmethod
    def _for_inputs(cls, original_inputs, *, original_contexts, producers):
        from .capture import OwnedInputs
        require(cls is OwnedSourceCapture and type(original_inputs) is OwnedInputs)
        require(type(producers) is tuple and len(producers)==2 and type(original_contexts) is tuple and len(original_contexts)==2)
        require(original_inputs._preflight is None and original_inputs._exclusive is None and original_inputs._capture is None)
        for kind,p,context in zip(('central','identity'),producers,original_contexts):
            require(type(p) is OwnedSourceProducer);p._verify()
            require(p._scope.kind==kind and p._pair is None and not p._attempts and p._comparison is context)
        require(producers[0]._cancel is producers[1]._cancel and producers[0]._started==producers[1]._started and producers[0]._deadline==producers[1]._deadline)
        pair=cls(_SEAL);pair._inputs=original_inputs;pair._producers=producers;pair._contexts=original_contexts
        pair._lock=threading.RLock();pair._phase_deadline=None;pair._exclusive=None;pair._runner=None;pair._controller=None;pair._policy=None
        pair._state=None;pair._backup_id=None;pair._binding=None;pair._captured=None;pair._plan_pin=None
        pair._job=None;pair._slots=[];pair._custody_unknown=False;pair._publication_closed=False
        pair._stage='NEW';pair._terminal_state=None;pair._cleanup_unknown=False;pair._consumers=0;pair._consumer=None;pair._consumer_ever=False
        pair._authenticated=None;pair._commit=None;pair._reference=None;pair._intent=None
        pair._grant_objects=None;pair._grant_pins=None
        pair._sample_objects=None;pair._sample_pins=None
        pair._grant_scopes=tuple(p._grant_scope for p in producers)
        pair._grant_scope_pins=tuple(p._grant_scope_pin for p in producers)
        pair._cancel=producers[0]._cancel
        for p in producers:p._pair=pair
        return pair

    def _verify_bindings(self):
        require(type(self) is OwnedSourceCapture and self._seal is _SEAL and self._inputs.companion is self)
        require(tuple(p._grant_scope_pin for p in self._producers)==self._grant_scope_pins)
        require(all(p._grant_scope is original for p,original in zip(self._producers,self._grant_scopes)))
        if self._sample_objects is not None:
            for p,originals,pins in zip(self._producers,self._sample_objects,self._sample_pins):
                require(tuple(p._samples)==tuple(phase for phase,_ in originals))
                require(all(p._samples[phase] is sample for phase,sample in originals))
                require(tuple((sample.safe_summary()['full_sha256'],sample.safe_summary()['stable_sha256'])
                    for _,sample in originals)==pins)
        if self._grant_objects is not None:
            for p,originals,pins in zip(self._producers,self._grant_objects,self._grant_pins):
                require(tuple(p._grant_samples)==tuple(phase for phase,_ in originals))
                require(all(p._grant_samples[phase] is g for phase,g in originals))
                require(tuple(g.safe_summary() for _,g in originals)==pins)
        for p,context in zip(self._producers,self._contexts):
            require(p._pair is self and p._comparison is context and getattr(p._owner,'_reconstruction_producer',None) is p)
            require(p._read_context is p._scope and _pin(p._scope)==p._read_pin)
            require(p._grant_scope_pin==(None if p._grant_scope is None else _pin(p._grant_scope)))
            require(getattr(p._owner,'_replay_authorization',None) is p._authorization)
            if p._authorization is not None:
                p._authorization._verify();require(p._authorization.grant_graph_scope is p._grant_scope)

    def _guard(self):
        self._verify_bindings()
        require(self._terminal_state is None and not self._custody_unknown and not self._cleanup_unknown)
        require(not self._cancel.is_set() and self._state is not None and not self._state['cancelled'].is_set())
        require(not self._inputs._work_unknown)
        require(all(p._started<=time.monotonic_ns()<p._deadline for p in self._producers))
        if self._exclusive is not None:
            require(self._inputs._exclusive is self._exclusive and self._inputs.binding is self._binding)
            if self._state['phase']=='OPEN':
                require(self._exclusive._closed and not self._exclusive.active_refs
                    and self._controller._holders.get(self._binding.window_id) is not self._exclusive)
            else:self._exclusive.verify(self._binding)

    def _bind_backup_attempt(self, controller, state, policy, backup_id, runner):
        from .anchor import RootPolicy
        from .capture import ChildRunner
        from ..media_maintenance.control import DatabaseControl
        with self._lock:
            self._verify_bindings();require(self._state is None)
            require(type(controller) is DatabaseControl and type(policy) is RootPolicy and type(runner) is ChildRunner)
            require(type(state) is dict and type(state['cancelled']) is threading.Event)
            policy._check();self._controller=controller;self._state=state;self._policy=policy;self._backup_id=backup_id;self._runner=runner
            self._guard()

    def _bind_window(self, exclusive):
        from ..media_maintenance.filesystem import ExclusiveWindow
        with self._lock:
            self._guard();require(self._exclusive is None and type(exclusive) is ExclusiveWindow)
            require(self._inputs._exclusive is exclusive and self._inputs.binding is exclusive.binding)
            require(type(self._inputs._deadline) is int and time.monotonic_ns()<self._inputs._deadline)
            self._phase_deadline=self._inputs._deadline
            self._exclusive=exclusive;self._binding=exclusive.binding;self._guard();self._stage='WINDOW'

    def _producer(self,kind):
        require(kind in ('central','identity'))
        return self._producers[0 if kind=='central' else 1]

    def _before(self,kind,keeper):return self._producer(kind)._read(SourcePhase.BEFORE,keeper)
    def _during(self,kind,keeper):return self._producer(kind)._read(SourcePhase.DURING,keeper)
    def _after(self,kind,observer):return self._producer(kind)._read(SourcePhase.AFTER,observer)

    def _after_observer_closed(self, kind, observer, error, read_error=None):
        # Fixed capture-owner observation, never a producer close or caller receipt.
        self._producer(kind)._observe_after_close(observer,error,read_error)

    def _bind_captured(self, actual_captured):
        from .contracts import CapturedSource
        with self._lock:
            self._guard();require(self._captured is None and type(actual_captured) is CapturedSource and self._inputs._capture is actual_captured)
            require(all(tuple(p._samples)==(SourcePhase.BEFORE,SourcePhase.DURING,SourcePhase.AFTER) and p._terminal() for p in self._producers))
            for p in self._producers:
                if p._grant_scope is not None:
                    require(tuple(p._grant_samples)==(SourcePhase.BEFORE,SourcePhase.DURING,SourcePhase.AFTER))
                    require(all(g.scope is p._grant_scope and g.source_sample is p._samples[phase] and g.source_sample_pin==p._samples[phase].safe_summary()['full_sha256']
                        for phase,g in p._grant_samples.items()))
                    require(all(g.stable_private_value()==p._grant_samples[SourcePhase.DURING].stable_private_value()
                        for g in p._grant_samples.values()))
            # Freeze the actual successful three-phase return objects, including
            # default four-query capture without the optional graph sidecar.
            self._sample_objects=tuple(tuple(p._samples.items()) for p in self._producers)
            self._sample_pins=tuple(tuple((sample.safe_summary()['full_sha256'],sample.safe_summary()['stable_sha256'])
                for sample in p._samples.values()) for p in self._producers)
            self._grant_objects=tuple(tuple(p._grant_samples.items()) for p in self._producers)
            self._grant_pins=tuple(tuple(g.safe_summary() for g in p._grant_samples.values()) for p in self._producers)
            self._captured=actual_captured;self._plan_pin=_pin(actual_captured.plan);self._stage='CAPTURED'

    def _verify_publication(self):
        self._verify_bindings();self._policy._check()
        require(self._captured is self._inputs._capture and _pin(self._captured.plan)==self._plan_pin)
        require(not self._custody_unknown and len(self._slots)==2)
        for slot in self._slots:
            require(slot['state']=='HELD' and _directory_identity(os.fstat(slot['fd']))==slot['identity'])
            require(not os.get_inheritable(slot['fd']))
        require(self._slots[1]['identity'][:2]==self._policy._backup.identity[:2])
        require(_directory_identity(os.stat(self._backup_id,dir_fd=self._slots[1]['fd'],follow_symlinks=False))==self._slots[0]['identity'])
        if self._authenticated is not None:
            from .durable import _stamp
            for kind,stamp in self._authenticated['stamps'].items():
                require(_stamp(os.stat(kind+'.easelbs2',dir_fd=self._slots[0]['fd'],follow_symlinks=False))==stamp)
            require(self._authenticated['original_receipts']==self._authenticated['receipts'])
            require(self._authenticated['original_decoders']==self._authenticated['decoders'])
            require(self._authenticated['original_archive']==self._authenticated['archive'])
        if self._commit is not None:
            from .durable import _stamp
            require(_stamp(os.stat('COMMITTED.json',dir_fd=self._slots[0]['fd'],follow_symlinks=False))==self._commit_stamp)

    def _retain_publication_job(self,actual_job):
        from .durable import PrivateJob
        with self._lock:
            self._guard();require(self._stage=='CAPTURED' and self._job is None and type(actual_job) is PrivateJob)
            actual_job.verify();require(actual_job.backup_id==self._backup_id and actual_job.owner==self._policy.owner)
            require(actual_job._root.identity[:2]==self._policy._backup.identity[:2])
            self._job=actual_job
            for original in (actual_job._directory.fd,actual_job._root.fd):
                slot=dict(original_fd=original,fd=None,state='ALLOCATING');self._slots.append(slot)
                try:
                    slot['fd']=os.dup(original);os.set_inheritable(slot['fd'],False)
                    info=os.fstat(slot['fd']);slot['identity']=_directory_identity(info);slot['nlink_at_dup']=info.st_nlink;slot['state']='HELD'
                except BaseException as error:
                    slot.update(state='UNKNOWN',error=error);self._custody_unknown=True;raise B2Error() from None
            self._verify_publication();self._stage='JOB'

    def _record_authenticated_objects(self, receipts, decoder_receipts, archive_receipt, reopened):
        with self._lock:
            self._guard();self._verify_publication();require(self._stage=='JOB')
            require(receipts is self._state['owned_receipts'] and decoder_receipts is self._state['owned_decoder_receipts']
                and reopened is self._state['owned_reopened'] and archive_receipt is self._state['owned_archive_receipt'])
            from .stream import V2Encoder,V2Decoder
            require(all(type(self._state['owned_encoders'][k]) is V2Encoder and self._state['owned_encoders'][k]._finished
                and type(self._state['owned_decoders'][k]) is V2Decoder and self._state['owned_decoders'][k].verified
                and self._state['owned_decoders'][k].receipt==decoder_receipts[k] for k in ('context','archive')))
            require(set(receipts)==set(decoder_receipts)==set(reopened)=={'context','archive'})
            require(decoder_receipts==receipts and self._state['post'] is not None)
            require(all(self._job._finished[k][0]==receipts[k] for k in receipts))
            stamps={k:tuple(reopened[k].stamp) for k in reopened}
            require(all(stamps[k]==self._job._finished[k][1] for k in stamps))
            self._authenticated=dict(receipts=copy.deepcopy(receipts),decoders=copy.deepcopy(decoder_receipts),
                archive=copy.deepcopy(archive_receipt),stamps=stamps,original_receipts=receipts,
                original_decoders=decoder_receipts,original_archive=archive_receipt)
            self._authenticated_pin=_pin({k:self._authenticated[k] for k in ('receipts','decoders','archive','stamps')})
            self._stage='AUTHENTICATED'

    def _record_committed(self, reference, commit, intent):
        from .contracts import CommitRef
        with self._lock:
            self._guard();self._verify_publication();require(self._stage=='AUTHENTICATED')
            require(reference is self._state['owned_commit_ref'] and commit is self._state['owned_commit_bytes']
                and intent is self._state['owned_intent'])
            require(type(reference) is CommitRef and self._job._committed and self._state['persisted'])
            require(self._job._anchor is not None and intent.backup_id==self._backup_id)
            require(reference==intent._validate_commit(commit) and reference==intent._read_anchor())
            require(hashlib.sha256(commit).hexdigest()==self._state['commit_sha256'])
            from .durable import _stamp
            self._commit_stamp=_stamp(os.stat('COMMITTED.json',dir_fd=self._slots[0]['fd'],follow_symlinks=False))
            require(self._commit_stamp[6]==len(commit))
            self._reference=reference;self._commit=bytes(commit);self._intent=intent
            self._writer_policy=intent._policy;self._writer_issuer=intent._policy._issuer
            require(self._writer_issuer is self._policy._issuer)
            self._writer_selector=intent._directory
            self._writer_pin=(intent.backup_id,intent.target,intent.commit_sha256,
                intent._directory.identity,intent._policy._backup.identity,reference,hashlib.sha256(commit).hexdigest())
            self._stage='COMMITTED'

    def _record_open(self,binding,controller):
        with self._lock:
            self._guard();self._verify_publication();require(self._stage=='COMMITTED')
            require(binding is self._binding and controller is self._controller and self._state['phase']=='OPEN')
            require(binding.window_id not in controller.pending_window_ids)
            self._stage='OPEN'

    def _require_read_terminal(self):
        self._verify_bindings()
        require(all(p._terminal() for p in self._producers))
        require(not self._inputs._work_unknown and not self._runner.children and not self._runner.unknown_refs)
        require(not self._cleanup_unknown)

    def _finish_pipeline(self, *, inputs_closed, exclusive_accounted, cleanup_known):
        # Called only from build_backup's actual finally, after real worker and
        # keeper/holder disposition. The original state is retained, not a receipt.
        with self._lock:
            self._verify_bindings();require(self._terminal_state is None)
            try:
                require(inputs_closed and exclusive_accounted and cleanup_known)
                self._require_read_terminal()
                with self._inputs._work_lock:require(self._inputs._active_work==0 and not self._inputs._work_unknown)
                require(not self._inputs._keepers and not self._cancel.is_set() and not self._custody_unknown)
                require(not self._controller.pending_window_ids)
                require(all(time.monotonic_ns()<p._deadline for p in self._producers))
                if self._phase_deadline is not None and time.monotonic_ns()>=self._phase_deadline:
                    self._terminal_state='EXPIRED'
                    return self._terminal_state
                if self._stage=='OPEN' and not self._state['cancelled'].is_set():
                    self._verify_publication();require(self._state['persisted'] and self._state['phase']=='OPEN')
                    require(_pin({k:self._authenticated[k] for k in ('receipts','decoders','archive','stamps')})==self._authenticated_pin)
                    self._terminal_state='TERMINAL_SUCCESS'
                else:
                    require(self._state['cancelled'].is_set() and any(p._rejected for p in self._producers))
                    require(self._state['phase'] in (None,'NEEDS_REVIEW') and not self._state['commit_attempted'])
                    self._terminal_state='FAILED_TERMINAL'
            except BaseException:
                self._terminal_state='UNKNOWN'
            return self._terminal_state

    def snapshot(self):
        # Nonblocking, bounded observations: a lock owner may be inside libpq.
        result=dict(active=0,unknown=False,retained_refs=0,publication_fds=0,observation_error=False)
        if not self._lock.acquire(blocking=False):result.update(unknown=True,observation_error=True);return result
        try:
            result['unknown']=self._custody_unknown or self._cleanup_unknown or self._terminal_state=='UNKNOWN'
            result['publication_fds']=sum(s['state']!='CLOSED' for s in self._slots)
            for p in self._producers:
                if not p._lock.acquire(blocking=False):result.update(unknown=True,observation_error=True);continue
                try:
                    result['unknown']|=p._unknown
                    result['active']+=sum(a['state'] not in ('TERMINAL',) for a in p._attempts)
                    result['retained_refs']+=sum(a['ref'] is not None and not a['ref']._closed for a in p._attempts)
                finally:p._lock.release()
        except BaseException:result.update(unknown=True,observation_error=True)
        finally:self._lock.release()
        return result

    def _validate_terminal_success(self, *, require_live=True):
        """Passive original-success validation; capture clock is historical."""
        self._verify_bindings();self._require_read_terminal()
        require(self._terminal_state=='TERMINAL_SUCCESS' and self._stage=='OPEN')
        require(not self._publication_closed)
        require(self._state['phase']=='OPEN' and self._state['persisted'])
        if require_live:require(not self._cancel.is_set() and not self._state['cancelled'].is_set())
        require(self._captured is self._inputs._capture and self._captured.window is self._binding)
        require(_pin(self._captured.plan)==self._plan_pin)
        require(not self._controller.pending_window_ids)
        require(not self._inputs._active_work and not self._inputs._work_unknown and not self._inputs._keepers)
        require(_pin({k:self._authenticated[k] for k in ('receipts','decoders','archive','stamps')})==self._authenticated_pin)
        for p in self._producers:
            owner=p._owner;attempt=owner._reconstruction_attempt
            require(owner.phase=='READY' and not owner.cleanup_attempted and not owner.unknown and owner.active==0)
            require(owner.cancellation is self._cancel and attempt['state']=='TERMINAL')
            require(attempt.get('capture_outcome')=='TERMINAL_SUCCESS')
            require(tuple(p._samples)==(SourcePhase.BEFORE,SourcePhase.DURING,SourcePhase.AFTER))
            require(not p._rejected and not p._unknown)
            require(all(a['validation']=='ACCEPTED' and a['state']=='TERMINAL' for a in p._attempts))
            require(all(v.stable_private_value()==p._samples[SourcePhase.DURING].stable_private_value() for v in p._samples.values()))

    def _claim_consumer(self,owner,slot):
        with self._lock:
            # Establish the original pair claim before checking mutable custody.
            # A duplicate must fail without touching an existing winner.
            require(type(self) is OwnedSourceCapture and self._seal is _SEAL and self._inputs.companion is self)
            require(not self._consumer_ever and self._consumer is None and self._consumers==0)
            self._consumer_ever=True;self._consumer=(owner,slot);self._consumers=1

    def _release_consumer(self,owner,slot):
        with self._lock:
            self._verify_bindings()
            require(self._consumer is not None and self._consumer[0] is owner and self._consumer[1] is slot)
            require(self._consumers==1)
            self._consumer=None;self._consumers=0

    def close_publication(self):
        with self._lock:
            self._verify_bindings();require(not self._publication_closed and not self._consumers)
            self._require_read_terminal();require(not self._custody_unknown)
            for slot in self._slots:
                require(slot['state']=='HELD')
                # Release needs only allocation identity, not mutable content.
                # Refuse to close a foreign/reused or missing descriptor.
                try:
                    info=os.fstat(slot['fd'])
                    require((info.st_dev,info.st_ino)==slot['identity'][:2])
                except BaseException as error:
                    slot.update(state='UNKNOWN',error=error);self._custody_unknown=True;raise B2Error() from None
                slot['state']='CLOSING'
                try:os.close(slot['fd'])
                except BaseException as error:
                    slot.update(state='UNKNOWN',error=error);self._custody_unknown=True;raise B2Error() from None
                slot['state']='CLOSED'
            self._publication_closed=True


def _directory_identity(info):
    require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode)==0o700 and info.st_nlink>=1)
    return info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid
