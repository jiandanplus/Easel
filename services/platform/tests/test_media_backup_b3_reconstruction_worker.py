"""Pure fixed-session fake; never connect, spawn, or load installed worker."""
import importlib
import pytest
from test_media_backup_b3_reconstruction_contract import api,document

def worker():return importlib.import_module('platform_service.media_backup_b3.reconstruction_worker')

def test_ready_anchor_counts_payload_and_import_time(monkeypatch):
    w=worker();clock=[100.0];monkeypatch.setattr(w,'_clock',lambda:clock[0])
    budget=w._Budget();assert budget.ready()==w.READY_FRAME
    clock[0]=102.0
    request=api().decode_request(api().encode_request(document()))
    with pytest.raises(w.WorkerError):budget.accept(request)
    assert budget.anchor==100.0

def test_ready_budget_never_renews_after_accept(monkeypatch):
    w=worker();clock=[100.0];monkeypatch.setattr(w,'_clock',lambda:clock[0])
    budget=w._Budget();budget.ready();d=document();d['remaining_ns']=1000
    request=api().decode_request(api().encode_request(d));budget.accept(request)
    clock[0]=100.001
    with pytest.raises(w.WorkerError):budget.guard()
    with pytest.raises(w.WorkerError):budget.accept(request)

def test_expired_request_never_allocates_client(monkeypatch):
    w=worker();clock=[100.0];monkeypatch.setattr(w,'_clock',lambda:clock[0])
    budget=w._Budget();budget.ready();clock[0]=102
    request=api().decode_request(api().encode_request(document()))
    def forbidden(**kwargs):raise AssertionError('connection before expiry guard')
    monkeypatch.setattr(w,'_connect',forbidden)
    with pytest.raises(w.WorkerError):w._roles_apply(request,budget)

def _session_fixture(w):
    from platform_service.media_backup_b3 import roles
    import hashlib
    d=document();d['remaining_ns']=1000
    d['binding']['system_id_sha256']=hashlib.sha256(b'123').hexdigest()
    d['identity_before'].update(operational_oid=100,source_oid=10)
    a=api().decode_plan(d['plan']).handover.expectation
    initial=[('A',10,a.superuser,a.inherit,a.createrole,a.createdb,False,a.replication,a.bypassrls,
        a.connlimit,a.validuntil,a.verifier,a.config),
        ('B',100,True,True,True,True,True,True,True,-1,None,'SCRAM-SENTINEL',None)]
    b=initial[1];d['identity_before']['operational_attributes_sha256']=roles._hash(b[2:11]+(b[12],))
    d['identity_before']['operational_verifier_sha256']=hashlib.sha256(b[11].encode()).hexdigest()
    d['identity_before']['initdb_sha256']=roles._hash((('synthetic initial inventory',),))
    request=api().decode_request(api().encode_request(d))
    final=list(initial)
    for i,definition in enumerate(request.plan.roles):
        e=definition.expectation
        final.append((e.name,201+i,e.superuser,e.inherit,e.createrole,e.createdb,False,
            e.replication,e.bypassrls,e.connlimit,e.validuntil,e.verifier,e.config))
    class Connection:
        def __init__(self):
            self.queries=[];self.closed=False;self.result=[];self.rows=initial;self.edges=[];self.commits=0
            self.lost=False;self.bad_session=False;self.bad_logging=False;self.grants=0;self.gucs={};self.parameters=[]
        def cursor(self):return self
        def execute(self,query,params=None):
            text=query if type(query) is str else query.as_string(None)
            self.queries.append(text);self.parameters.append(params)
            if query==w._SESSION:self.result=[(200,'2026-01-01 UTC',999 if self.bad_session else 100,100,'123',160015)]
            elif query==w._ROLES:self.result=list(self.rows)
            elif query==w._MEMBERS:self.result=sorted(self.edges)
            elif query==w._INITDB:self.result=[('synthetic initial inventory',)]
            elif text=='SELECT extname FROM pg_catalog.pg_extension ORDER BY extname':self.result=[('plpgsql',)]
            elif text=='SELECT pg_catalog.set_config(%s,%s,false)':
                self.gucs[params[0]]=params[1];self.result=[(params[1],)]
            elif text=='SELECT pg_catalog.current_setting(%s)':
                values=dict(w._LOG_GUARD,TimeZone='UTC',DateStyle='ISO, MDY',search_path='original, \"session path\"');values.update(self.gucs)
                self.result=[('all' if self.bad_logging else values.get(params[0],''),)]
            elif text.startswith('SELECT context FROM pg_catalog.pg_settings'):self.result=[('user',)]
            elif text.startswith('CREATE ROLE '):self.rows=final
            elif text.startswith('GRANT '):
                g=request.plan.memberships[self.grants];self.grants+=1
                self.edges.append((g.role_name,g.member_name,g.grantor_name,g.admin_option,g.inherit_option,g.set_option))
            elif text=='COMMIT':
                self.commits+=1
                if self.lost:raise OSError('private SQL must never escape')
        def fetchone(self):return self.result[0]
        def fetchall(self):return self.result
        def close(self):self.closed=True
    return request,Connection()

def test_fixed_program_exact_readback_and_one_commit(monkeypatch):
    w=worker();request,connection=_session_fixture(w)
    monkeypatch.setattr(w,'_clock',lambda:100.)
    monkeypatch.setattr(w,'_connect',lambda **kwargs:connection)
    budget=w._Budget();frame=budget.ready()
    assert int.from_bytes(frame[:4],'big')==len(frame)-4
    result=w._roles_apply(request,budget)
    assert result.commit_acknowledged and connection.commits==1 and connection.closed
    assert connection.queries.index('BEGIN')<next(i for i,q in enumerate(connection.queries) if q.startswith('CREATE ROLE'))
    assert 'SELECT pg_catalog.current_setting(%s)' in connection.queries[:connection.queries.index('BEGIN')]
    assert 'SCRAM-SENTINEL' not in repr(result)

@pytest.mark.parametrize('failure',['lost','bad_session','bad_logging'])
def test_commit_loss_and_guard_failure_do_not_retry_or_cleanup(monkeypatch,failure):
    w=worker();request,connection=_session_fixture(w);setattr(connection,failure,True)
    monkeypatch.setattr(w,'_clock',lambda:100.)
    monkeypatch.setattr(w,'_connect',lambda **kwargs:connection)
    budget=w._Budget();budget.ready()
    with pytest.raises(w.WorkerError,match='^RECONSTRUCTION_WORKER_REFUSED$'):w._roles_apply(request,budget)
    assert connection.commits==(1 if failure=='lost' else 0)
    assert not connection.closed
    assert w._SESSIONS[-1].connection is connection and w._SESSIONS[-1].state=='UNKNOWN'
    if failure!='lost':assert not any(q.startswith('CREATE ROLE') for q in connection.queries)

@pytest.mark.parametrize('value',['public, pg_catalog','"one,two", "three four", public','"a""b", "$user"'])
def test_worker_sets_raw_list_then_from_current_and_restores_session(monkeypatch,value):
    w=worker();request,connection=_session_fixture(w)
    from platform_service.media_backup_b3 import reconstruction_sql as sql
    step=sql.SessionSetting('search_path',value,sql.SQL('ALTER ROLE "R" SET "search_path" FROM CURRENT'))
    monkeypatch.setattr(w,'_clock',lambda:100.)
    budget=w._Budget();budget.ready();budget.accept(request)
    w._setting(connection,step,request,budget)
    assert connection.gucs['search_path']=='original, "session path"'
    assert ('search_path',value) in connection.parameters
    assert 'ALTER ROLE "R" SET "search_path" FROM CURRENT' in connection.queries


def test_same_worker_request_cannot_be_replayed_after_commit_loss(monkeypatch):
    w=worker();request,connection=_session_fixture(w);connection.lost=True
    calls=[]
    def connect(**kwargs):calls.append(kwargs);return connection
    monkeypatch.setattr(w,'_clock',lambda:100.)
    monkeypatch.setattr(w,'_connect',connect)
    budget=w._Budget();budget.ready()
    for _ in range(2):
        with pytest.raises(w.WorkerError):w._roles_apply(request,budget)
    assert len(calls)==1 and connection.commits==1


def test_slow_program_import_cannot_extend_ready_budget(monkeypatch):
    import builtins
    w=worker();clock=[100.];request,connection=_session_fixture(w)
    monkeypatch.setattr(w,'_clock',lambda:clock[0])
    original=builtins.__import__;calls=[]
    def importing(name,globals=None,locals=None,fromlist=(),level=0):
        if 'reconstruction_sql' in (fromlist or ()):clock[0]+=1
        return original(name,globals,locals,fromlist,level)
    monkeypatch.setattr(builtins,'__import__',importing)
    monkeypatch.setattr(w,'_connect',lambda **kwargs:calls.append(kwargs))
    budget=w._Budget();budget.ready()
    with pytest.raises(w.WorkerError):w._roles_apply(request,budget)
    assert calls==[] and budget.anchor==100.


def test_initial_observe_needs_no_unknown_verifier_hash(monkeypatch):
    w=worker();c=api();d=document();d['operation']='INITIAL_OBSERVE'
    d['identity_before'].update(operational_oid=10,source_oid=None,
        operational_verifier_sha256=None,initdb_sha256=None)
    request,connection=_session_fixture(w)
    connection.rows=[('B',10,True,True,True,True,True,True,True,-1,None,'SCRAM-SENTINEL',None)]
    from platform_service.media_backup_b3.roles import _hash
    d['identity_before']['operational_attributes_sha256']=_hash(connection.rows[0][2:11]+(None,))
    d['binding']['system_id_sha256']=request.binding['system_id_sha256']
    original=connection.execute
    def execute(q,params=None):
        original(q,params)
        if q==w._SESSION:connection.result=[(200,'start',10,10,'123',160015)]
        elif q==w._INITDB:connection.result=[('synthetic fixed inventory',)]
    connection.execute=execute
    monkeypatch.setattr(w,'_clock',lambda:100.)
    monkeypatch.setattr(w,'_connect',lambda **kw:connection)
    request=c.decode_request(c.encode_request(d));budget=w._Budget();budget.ready()
    result=w._initial_observe(request,budget)
    assert result['operational_oid']==10 and result['initdb_sha256']
    assert result['operational_verifier_sha256']
    assert connection.commits==0 and connection.closed
    assert not any(q.startswith(('CREATE','ALTER','DROP','GRANT')) for q in connection.queries)
