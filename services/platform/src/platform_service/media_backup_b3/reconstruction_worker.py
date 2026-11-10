"""Private fixed installed session program; native qualification remains separate.

No source-selected SQL/callback/connection target. Installed origin, host native
custody and phase result acceptance are independent requirements of the host.
"""
import time
_ENTRY_ANCHOR=time.monotonic()
import hashlib
from dataclasses import dataclass

_clock=time.monotonic
_READY_BODY=b'{"format":"easel.b3.reconstruction-ready.v1"}'
READY_FRAME=len(_READY_BODY).to_bytes(4,'big')+_READY_BODY

class WorkerError(ValueError):
    def __init__(self):super().__init__('RECONSTRUCTION_WORKER_REFUSED')

def _need(value):
    if not value:raise WorkerError()

class _Budget:
    """Local elapsed budget anchored BEFORE READY, never host monotonic time."""
    def __init__(self):self.anchor=None;self.deadline=_ENTRY_ANCHOR+60;self.request=None;self.started=False
    def ready(self):
        _need(self.anchor is None);self.anchor=_clock()
        return READY_FRAME
    def accept(self, request):
        _need(self.anchor is not None and self.request is None)
        self.request=request
        self.deadline=min(self.deadline,self.anchor+request.remaining_ns/10**9)
        self.guard()
    def begin(self,request):
        _need(self.request is request and not self.started);self.started=True;self.guard()
    def guard(self):
        _need(self.anchor is not None and self.deadline is not None and _clock()<self.deadline)

_SESSIONS=[]  # strong original client custody, including uncertain constructors

@dataclass(repr=False,eq=False)
class _SessionSlot:
    request: object
    connection: object=None
    cursor: object=None
    state: str='ALLOCATING'
    commit_attempted: bool=False
    commit_acknowledged: bool=False
    close_attempted: bool=False

def _connect(**kwargs):
    import psycopg
    return psycopg.connect(**kwargs)

# Fixed PG16 logging guard. Read back every setting before any secret utility.
_LOG_GUARD=(('log_statement','none'),('log_min_error_statement','panic'),
    ('log_parameter_max_length','0'),('log_parameter_max_length_on_error','0'),
    ('log_duration','off'),('log_min_duration_statement','-1'),
    ('log_min_duration_sample','-1'),('log_statement_sample_rate','0'),
    ('log_transaction_sample_rate','0'),('debug_print_parse','off'),
    ('debug_print_rewritten','off'),('debug_print_plan','off'))
_SESSION="""SELECT pg_catalog.pg_backend_pid(), backend_start::text,
 (SELECT oid FROM pg_catalog.pg_roles WHERE rolname=session_user),
 (SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user),
 (SELECT system_identifier::text FROM pg_catalog.pg_control_system()),
 pg_catalog.current_setting('server_version_num')::int
 FROM pg_catalog.pg_stat_activity WHERE pid=pg_catalog.pg_backend_pid()"""
_ROLES="""SELECT a.rolname,a.oid,a.rolsuper,a.rolinherit,a.rolcreaterole,
 a.rolcreatedb,a.rolcanlogin,a.rolreplication,a.rolbypassrls,a.rolconnlimit,
 a.rolvaliduntil::text,a.rolpassword,
 (SELECT setconfig FROM pg_catalog.pg_db_role_setting s WHERE s.setdatabase=0 AND s.setrole=a.oid)
 FROM pg_catalog.pg_authid a ORDER BY a.oid"""
_MEMBERS="""SELECT r.rolname,m.rolname,g.rolname,a.admin_option,a.inherit_option,a.set_option
 FROM pg_catalog.pg_auth_members a JOIN pg_catalog.pg_roles r ON r.oid=a.roleid
 JOIN pg_catalog.pg_roles m ON m.oid=a.member JOIN pg_catalog.pg_roles g ON g.oid=a.grantor
 ORDER BY r.rolname,m.rolname,g.rolname"""

@dataclass(frozen=True,repr=False)
class _SessionCommit:
    before: tuple
    after: tuple
    role_pin: str
    membership_pin: str
    commit_acknowledged: bool
    def __repr__(self):return '<private reconstruction session commit DATA>'

def _session(cursor,request,budget):
    budget.guard();cursor.execute(_SESSION);rows=cursor.fetchall()
    _need(type(rows) is list and len(rows)==1 and len(rows[0])==6)
    row=tuple(rows[0]);pid,started,session,current,system,version=row
    _need(type(pid) is int and pid>0 and type(started) is str and bool(started))
    _need(type(session) is int and type(current) is int and session==current==request.identity_before['operational_oid'])
    _need(type(version) is int and 160000<=version<170000)
    _need(type(system) is str and system.isascii() and system.isdecimal())
    _need(hashlib.sha256(system.encode('ascii')).hexdigest()==request.binding['system_id_sha256'])
    budget.guard();return row

def _read(cursor,query,budget):
    budget.guard();cursor.execute(query);rows=cursor.fetchall();budget.guard()
    _need(type(rows) is list);return tuple(tuple(x) for x in rows)

def _logging(cursor,budget):
    for key,value in _LOG_GUARD:
        budget.guard();cursor.execute('SELECT pg_catalog.set_config(%s,%s,false)',(key,value))
        _need(cursor.fetchone()==(value,))
        cursor.execute('SELECT pg_catalog.current_setting(%s)',(key,));_need(cursor.fetchone()==(value,))
    # Fresh target supports only its fixed builtin extension; no logging hook
    # may hide in session/shared preload or an arbitrary installed extension.
    for key in ('shared_preload_libraries','session_preload_libraries','local_preload_libraries'):
        budget.guard();cursor.execute('SELECT pg_catalog.current_setting(%s)',(key,));_need(cursor.fetchone()==('',))
    _need(_read(cursor,'SELECT extname FROM pg_catalog.pg_extension ORDER BY extname',budget)==(('plpgsql',),))

def _setting(cursor,step,request,budget):
    budget.guard()
    cursor.execute('SELECT pg_catalog.current_setting(%s)',(step.key,));row=cursor.fetchone()
    _need(type(row) is tuple and len(row)==1 and type(row[0]) is str)
    previous=row[0]
    cursor.execute('SELECT pg_catalog.set_config(%s,%s,false)',(step.key,step.value))
    _need(cursor.fetchone()==(step.value,));budget.guard()
    cursor.execute(step.statement)
    cursor.execute('SELECT pg_catalog.set_config(%s,%s,false)',(step.key,previous))
    _need(cursor.fetchone()==(previous,));budget.guard()
    cursor.execute('SELECT pg_catalog.current_setting(%s)',(step.key,));_need(cursor.fetchone()==(previous,))
    _logging(cursor,budget);_session(cursor,request,budget)

def _roles_apply(request,budget):
    """Fixed one-transaction role/member program, with exact before/post reads.

    This emits DATA only. Host acceptance must independently join original
    native process/EOF/exit/netns and durable phase records before owned proof.
    """
    connection=None;slot=None
    try:
        if budget.request is None:budget.accept(request)
        budget.begin(request)
        # Imports occur after READY; their elapsed time consumes the same budget.
        from . import reconstruction_sql as sql, roles as r
        from .reconstruction_contract import Operation
        _need(request.operation is Operation.ROLES_APPLY);budget.guard()
        password=request.private_auth['operational_password'].decode('utf-8','strict')
        slot=_SessionSlot(request);_SESSIONS.append(slot)
        connection=_connect(host='127.0.0.1',port=5432,dbname='postgres',
            user=request.identity_before['operational_name'],password=password,
            connect_timeout=1,sslmode='disable',application_name='easel_b3_reconstruct_v1',
            options='-c statement_timeout=1000 -c lock_timeout=1000 -c idle_in_transaction_session_timeout=1000',
            autocommit=True)
        slot.connection=connection;slot.state='CONNECTED'
        budget.guard();cursor=connection.cursor();slot.cursor=cursor
        _logging(cursor,budget)
        for key,value in zip(('TimeZone','DateStyle'),request.source_rendering):
            budget.guard();cursor.execute('SELECT pg_catalog.set_config(%s,%s,false)',(key,value));_need(cursor.fetchone()==(value,))
            cursor.execute('SELECT pg_catalog.current_setting(%s)',(key,));_need(cursor.fetchone()==(value,))
        before=_session(cursor,request,budget)
        initial=_read(cursor,_ROLES,budget);members=_read(cursor,_MEMBERS,budget)
        inventory=_inventory(cursor,budget)
        _need(r._hash(inventory)==request.identity_before['initdb_sha256'])
        _need(all(len(row)==13 for row in initial))
        names={row[0]:row for row in initial};_need(len(names)==len(initial))
        operational=names.get(request.identity_before['operational_name']);_need(operational is not None)
        _need(operational[1]==request.identity_before['operational_oid'])
        attributes=operational[2:11]+(operational[12],)
        _need(r._hash(attributes)==request.identity_before['operational_attributes_sha256'])
        verifier=operational[11];_need(type(verifier) is str)
        _need(hashlib.sha256(verifier.encode('utf-8')).hexdigest()==request.identity_before['operational_verifier_sha256'])
        if request.plan.handover is not None:
            source=request.plan.handover.expectation.name
            _need(request.identity_before['source_name']==source and request.identity_before['source_oid']==10)
            _need(source in names and names[source][1]==10 and operational[1]!=10)
            e=request.plan.handover.expectation
            _need(e.config!=())
            actual=names[source];config=None if actual[12] is None else tuple(actual[12])
            _need(actual[:12]+(config,)==(e.name,10,e.superuser,e.inherit,e.createrole,e.createdb,False,
                e.replication,e.bypassrls,e.connlimit,e.validuntil,e.verifier,config) and config==e.config)
        else:_need(operational[1]==10)
        expected={}
        for definition in request.plan.roles:
            e=definition.expectation;_need(e.name not in names)
            # No native operation may conflate NULL and an unrepresentable [] config.
            _need(e.config!=())
            expected[e.name]=(e.name,None,e.superuser,e.inherit,e.createrole,e.createdb,False,
                e.replication,e.bypassrls,e.connlimit,e.validuntil,e.verifier,e.config)
        statements=sql.roles_apply(request.plan)
        # Refuse session-unsettable native settings before role DDL. No key or
        # raw value is silently rewritten into a representable approximation.
        for step in statements:
            if type(step) is sql.SessionSetting:
                budget.guard();cursor.execute('SELECT context FROM pg_catalog.pg_settings WHERE pg_catalog.lower(name)=pg_catalog.lower(%s)',(step.key,))
                _need(cursor.fetchall() in ([('user',)],[('superuser',)]))
        _need(_inventory(cursor,budget)==inventory)
        budget.guard();cursor.execute('BEGIN')
        for statement in statements:
            budget.guard()
            if type(statement) is sql.SessionSetting:_setting(cursor,statement,request,budget)
            else:cursor.execute(statement)
        observed=_read(cursor,_ROLES,budget)
        _need(len(observed)==len(initial)+len(expected))
        for row in observed:
            if row[0] in names:_need(row==names[row[0]])
            else:
                _need(row[0] in expected and type(row[1]) is int and row[1]!=10)
                want=expected[row[0]]
                config=None if row[12] is None else tuple(row[12])
                _need(row[:1]+(None,)+row[2:12]+(config,)==want)
        prefix=list(members)
        for grant in request.plan.memberships:
            budget.guard();cursor.execute(sql.membership(grant))
            prefix.append((grant.role_name,grant.member_name,grant.grantor_name,
                grant.admin_option,grant.inherit_option,grant.set_option))
            _need(_read(cursor,_MEMBERS,budget)==tuple(sorted(prefix)))
            _session(cursor,request,budget)
        _need(_read(cursor,_MEMBERS,budget)==tuple(sorted(prefix)))
        # Read all attrs again after memberships; session remains operational B.
        _need(_read(cursor,_ROLES,budget)==observed)
        _session(cursor,request,budget);budget.guard();slot.commit_attempted=True;cursor.execute('COMMIT')
        slot.commit_acknowledged=True
        budget.guard();after=_session(cursor,request,budget)
        _need(after==before and _read(cursor,_ROLES,budget)==observed)
        _need(_read(cursor,_MEMBERS,budget)==tuple(sorted(prefix)))
        result=_SessionCommit(before,after,r._hash(observed),r._hash(tuple(sorted(prefix))),True)
        slot.close_attempted=True;connection.close();budget.guard();slot.state='COMMITTED';return result
    except BaseException:
        # Never retry COMMIT, issue compensation, print query, or auto-close an
        # uncertain session as successful cleanup. Installed process custody owns it.
        if slot is not None:slot.state='UNKNOWN'
        raise WorkerError() from None


# Fixed initial cluster inventory, observed before any role DDL. The operational
# password comes from original target custody; catalog values authorize nothing.
_INITDB="""SELECT pg_catalog.jsonb_build_object(
 'roles',(SELECT pg_catalog.jsonb_agg(pg_catalog.to_jsonb(r) ORDER BY r.oid) FROM pg_catalog.pg_roles r),
 'databases',(SELECT pg_catalog.jsonb_agg(pg_catalog.to_jsonb(d) ORDER BY d.oid) FROM pg_catalog.pg_database d),
 'members',(SELECT pg_catalog.jsonb_agg(pg_catalog.to_jsonb(m) ORDER BY m.oid) FROM pg_catalog.pg_auth_members m),
 'dependencies',(SELECT pg_catalog.jsonb_agg(pg_catalog.to_jsonb(s) ORDER BY s.dbid,s.classid,s.objid,s.objsubid,s.refclassid,s.refobjid,s.deptype) FROM pg_catalog.pg_shdepend s))::text"""

def _inventory(cursor,budget):
    inventory=_read(cursor,_INITDB,budget)
    _need(len(inventory)==1 and len(inventory[0])==1 and type(inventory[0][0]) is str)
    _need(len(inventory[0][0].encode('utf-8'))<=4*1024*1024)
    return inventory


def _initial_observe(request,budget):
    slot=None
    try:
        from .reconstruction_contract import Operation
        from .roles import _hash
        _need(request.operation is Operation.INITIAL_OBSERVE)
        if budget.request is None:budget.accept(request)
        budget.begin(request)
        slot=_SessionSlot(request);_SESSIONS.append(slot)
        connection=_connect(host='127.0.0.1',port=5432,dbname='postgres',
            user=request.identity_before['operational_name'],
            password=request.private_auth['operational_password'].decode('utf-8','strict'),
            connect_timeout=1,sslmode='disable',application_name='easel_b3_reconstruct_v1',
            options='-c statement_timeout=1000 -c lock_timeout=1000 -c idle_in_transaction_session_timeout=1000',
            autocommit=True)
        slot.connection=connection;slot.state='CONNECTED';budget.guard()
        cursor=connection.cursor();slot.cursor=cursor;_logging(cursor,budget)
        before=_session(cursor,request,budget)
        rows=_read(cursor,_ROLES,budget)
        operational=[row for row in rows if row[0]==request.identity_before['operational_name']]
        _need(len(operational)==1 and len(operational[0])==13)
        row=operational[0];_need(row[1]==10 and type(row[11]) is str)
        _need(_hash(row[2:11]+(row[12],))==request.identity_before['operational_attributes_sha256'])
        inventory=_inventory(cursor,budget)
        _need(_read(cursor,_ROLES,budget)==rows and _inventory(cursor,budget)==inventory)
        _need(_session(cursor,request,budget)==before)
        result=dict(before=list(before),after=list(before),operational_oid=10,
            operational_attributes_sha256=request.identity_before['operational_attributes_sha256'],
            operational_verifier_sha256=hashlib.sha256(row[11].encode('utf-8')).hexdigest(),
            initdb_sha256=_hash(inventory))
        slot.close_attempted=True;connection.close();budget.guard();slot.state='OBSERVED'
        return result
    except BaseException:
        if slot is not None:slot.state='UNKNOWN'
        raise WorkerError() from None


def _installed_origin(request,budget):
    from . import reconstruction_origin as origin
    return origin._load(request,budget)


def main():
    """Closed actual installed entry. Every operation consumes the entry budget."""
    try:
        import os,sys,platform
        _need(sys.argv[1:]==['reconstruct_worker'] and sys.platform=='linux'
            and platform.machine() in ('aarch64','arm64') and sys.executable=='/opt/easel/bin/python'
            and sys.flags.isolated==1 and sys.flags.dont_write_bytecode==1 and not os.isatty(0))
        budget=_Budget()
        from . import reconstruction_contract as c, reconstruction_origin as origin
        from .execution_worker import _read_exact, _read_fd, _write_result
        initial=int(budget.deadline*10**9)
        _write_result(budget.ready(),4096,initial)
        header=_read_exact(4,initial);size=int.from_bytes(header,'big')
        _need(0<size<=c.REQUEST_LIMIT)
        raw=header+_read_exact(size,initial);_need(_read_fd(0,1,initial)==b'')
        request=c.decode_request(raw);budget.accept(request)
        state=_installed_origin(request,budget)
        before=origin._observe(state,request,budget)
        if request.operation is c.Operation.INITIAL_OBSERVE:
            observed=_initial_observe(request,budget)
            session=dict(before=observed.pop('before'),after=observed.pop('after'),commit_acknowledged=False,
                role_sha256=None,membership_sha256=None)
        else:
            _need(request.operation is c.Operation.ROLES_APPLY)
            result=_roles_apply(request,budget);observed=None
            session=dict(before=list(result.before),after=list(result.after),commit_acknowledged=result.commit_acknowledged,
                role_sha256=result.role_pin,membership_sha256=result.membership_pin)
        # Warm the actual encoder before final origins, then measure all lazy imports.
        c.bounded_frame(session,c.RESULT_LIMIT)
        after=origin._observe(state,request,budget)
        evidence=origin._evidence(state,before,after)
        frame=c.bounded_frame(dict(format=c.RESULT_FORMAT,request_sha256=hashlib.sha256(raw).hexdigest(),binding=request.binding,
            operation=request.operation.value,build=request.build,session=session,observation=observed,installed_evidence=evidence),c.RESULT_LIMIT)
        c.decode_result(frame,raw);budget.guard()
        _write_result(frame,c.RESULT_LIMIT,int(budget.deadline*10**9));budget.guard()
        return 0
    except BaseException:
        for slot in _SESSIONS:
            slot.state='UNKNOWN'
        return 1

_FIXED_CALLABLES=(main,_initial_observe,_roles_apply,_connect,_installed_origin)

if __name__=='__main__':
    import sys
    sys.exit(main())
