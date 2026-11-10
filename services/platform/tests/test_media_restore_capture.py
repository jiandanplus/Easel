"""F2 synthetic source/transport/clock tests; no daemon, PG or private env reads."""
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime,timezone
import importlib.util
import inspect
import json
import os
import ctypes
from functools import lru_cache
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SCRIPT=Path(__file__).parents[1]/'scripts/media_restore_capture.py'


@lru_cache(maxsize=1)
def _darwin_observer_bridge():
    # Test-only native bridge: SDK siginfo_t and macros, not a handwritten ABI.
    directory=Path(tempfile.mkdtemp(prefix='easel-f2-test-waitid-',dir='/private/tmp'))
    source=directory/'observe.c';library=directory/'observe.dylib'
    source.write_text("""#include <sys/types.h>
#include <sys/wait.h>
#include <errno.h>
#include <string.h>
#include <signal.h>
int observe_child(int pid, int *exitcode) {
    siginfo_t info;
    memset(&info, 0, sizeof(info));
    if (waitid(P_PID, (id_t)pid, &info, WEXITED | WNOHANG | WNOWAIT) != 0) return -errno;
    if (info.si_pid == 0) return 0;
    if (info.si_pid != pid || info.si_signo != SIGCHLD) return -EINVAL;
    if (info.si_code == CLD_EXITED) *exitcode = info.si_status;
    else if (info.si_code == CLD_KILLED || info.si_code == CLD_DUMPED) *exitcode = -info.si_status;
    else return -EINVAL;
    return 1;
}
""")
    result=subprocess.run(['/usr/bin/clang','-dynamiclib','-Wall','-Werror',str(source),'-o',str(library)],
                          env={'PATH':'/usr/bin:/bin','LANG':'C','TMPDIR':str(directory)},capture_output=True,timeout=30)
    assert result.returncode==0,'installed SDK waitid bridge must compile; never silently skip'
    native=ctypes.CDLL(str(library),use_errno=True);call=native.observe_child
    call.argtypes=[ctypes.c_int,ctypes.POINTER(ctypes.c_int)];call.restype=ctypes.c_int
    return call


def load():
    assert SCRIPT.exists(),'F2 producer implementation is missing'
    spec=importlib.util.spec_from_file_location('capture_f2_test',SCRIPT)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module._production_observer=module._observe_child_exit
    if sys.platform=='darwin':
        call=_darwin_observer_bridge()
        def observe(proc):
            module.require(proc.returncode is None)
            code=ctypes.c_int();result=call(proc.pid,ctypes.byref(code))
            module.require(result in (0,1));return code.value if result else None
        module._observe_child_exit=observe
    return module


@pytest.fixture
def lib():return load()


def fixture_finance():
    scope=dict(user_id='u',tenant_id='t',billing_account_id='b',product_id='easel',session_id='s',authorization_epoch=0)
    r=dict(id='r',status='pending_reconciliation',amount=9728,actual=None,user_id='u',tenant_id='t',billing_account_id='b',product_id='easel',funding_mode='live',operation='execution')
    j=dict(id='j',reservation_id='r',invocation_id='i',status=r['status'],amount=9728,actual=None,
           scope=scope,user_id='u',billing_account_id='b',product_id='easel',funding_mode='live',model='deepseek',attempt_id='a',binding_hash='a'*64)
    i=dict(id='i',scope=deepcopy(scope),session_id='s',user_id='u',billing_account_id='b',product_id='easel',funding_mode='live',model='deepseek',operation_count=1,held=9728,spent=0)
    return {'credit_lots':[dict(id='l',billing_account_id='b',test_only=False,issued=10000,available=272,held=9728,spent=0,expired=0,refunded=0)],
            'credit_reservations':[r],'credit_holds':[dict(reservation_id='r',lot_id='l',billing_account_id='b',amount=9728)],
            'execution_jobs':[j],'execution_invocations':[i],'execution_resolutions':[],
            'execution_resolution_operations':[],'execution_receipts':[],'execution_terminal_evidence':[],
            'execution_provider_attempts':[],'execution_provider_associations':[],
            'media_jobs':[],'media_invocations':[],'execution_scope_hashes':[dict(id='j',scope_hash='b'*64)]}


def test_complete_projection_preserves_all_raw_lots_and_pending():
    m=load();data=fixture_finance();data['credit_lots'].append(dict(id='independent',billing_account_id='b',test_only=True,frozen=True,expires_at='2000-01-01',issued=500,available=0,held=0,spent=0,expired=500,refunded=0))
    before=deepcopy(data);wallet,pending,evidence=m.project_finance(data)
    assert wallet==dict(issued=10500,available=272,held=9728,spent=0,expired=500,refunded=0)
    assert pending[0]['id']=='j' and pending[0]['actual']is None and pending[0]['source_rows']['reservation']==data['credit_reservations'][0]
    assert data==before and evidence['active']==0


@pytest.mark.parametrize('case',['orphan','missing_invocation','duplicate','cross_domain','status','scope','amount','actual','active','hold','float','overflow'])
def test_financial_graph_rejects_incomplete_or_inconsistent_state(lib,case):
    d=fixture_finance()
    if case=='orphan':d['execution_jobs']=[]
    elif case=='missing_invocation':d['execution_invocations']=[]
    elif case=='duplicate':d['credit_reservations']*=2
    elif case=='cross_domain':d['media_jobs']=[deepcopy(d['execution_jobs'][0])]
    elif case=='status':d['credit_reservations'][0]['status']='waived'
    elif case=='scope':d['execution_jobs'][0]['scope']['billing_account_id']='foreign'
    elif case=='amount':d['execution_jobs'][0]['amount']=1
    elif case=='actual':d['execution_jobs'][0]['actual']=0
    elif case=='active':
        d['credit_reservations'][0]['status']=d['execution_jobs'][0]['status']='dispatched'
    elif case=='hold':d['credit_holds'][0]['amount']=1
    elif case=='float':d['credit_lots'][0]['held']=9728.0
    else:d['credit_lots'][0]['issued']=2**63
    with pytest.raises(lib.Rejected):lib.project_finance(d)


def waived_data():
    d=fixture_finance();j=d['execution_jobs'][0];r=d['credit_reservations'][0]
    j.update(status='waived',reason='platform_absorbed',dispatched_at='2026-10-27T00:00:00+00:00',lease_until='2026-10-27T00:00:00+00:00')
    r.update(status='waived',completion_key='op')
    d['credit_lots'][0].update(available=10000,held=0);d['execution_invocations'][0]['held']=0
    item=dict(id='j',attempt_id='a',binding_hash='a'*64,scope_hash='b'*64,reservation_id='r',amount='9728',snapshot_hash='c'*64)
    plan=dict(version=1,kind='platform_absorbed',actor='operator',plan_sha256='d'*64,items=[item],reason='approved',approval_reference='approval')
    result=dict(operation_id='op',kind='platform_absorbed',plan_sha256='d'*64,items=[dict(id='j',status='waived')])
    d['execution_resolution_operations']=[dict(id='op',actor_id='operator',plan_hash='d'*64,plan=plan,result=result,created_at='2026-10-27T00:01:00+00:00')]
    d['execution_resolutions']=[dict(job_id='j',operation_id='op',kind='platform_absorbed',attempt_id='a',binding_hash='a'*64,scope_hash='b'*64,reservation_id='r',original_amount=9728,customer_charge=0,provider_usage_known=False,actor_id='operator',reason='approved',approval_reference='approval',plan_hash='d'*64,snapshot_hash='c'*64)]
    return d


def test_waived_history_does_not_count_immutable_old_hold_as_current(lib):
    d=waived_data();before=deepcopy(d);wallet,pending,e=lib.project_finance(d)
    assert wallet['held']==0 and pending==[] and e['waived']==1 and d==before


@pytest.mark.parametrize('field,value',[('customer_charge',1),('provider_usage_known',True),('scope_hash','0'*64),('reservation_id','foreign')])
def test_waived_audit_is_not_just_a_status_flag(lib,field,value):
    d=waived_data();d['execution_resolutions'][0][field]=value
    with pytest.raises(lib.Rejected):lib.project_finance(d)


def sample(wall='2026-10-28T00:00:00+00:00',mono=1000000000,db=None,boot='a'*32):
    return dict(utc=wall,monotonic_ns=mono,db_utc=db or wall,boot_id=boot)


def test_clock_uses_bounded_actual_samples_and_ttl(lib):
    a=sample();b=sample('2026-10-28T00:00:10+00:00',11000000000)
    assert lib.clock_window(a,b,['2026-10-28T01:00:00+00:00'])==('2026-10-28T00:00:00+00:00','2026-10-28T00:15:00+00:00')


@pytest.mark.parametrize('case',['skew','backwards','jump','boot','timeout','ttl','expired','missing_ttl'])
def test_clock_rejects_skew_discontinuity_and_old_assets(lib,case):
    a=sample();b=sample('2026-10-28T00:00:10+00:00',11000000000);ttl=['2026-10-28T01:00:00+00:00']
    if case=='skew':b['db_utc']='2026-10-28T00:00:16+00:00'
    elif case=='backwards':b['utc']='2026-10-27T23:59:59+00:00'
    elif case=='jump':b['monotonic_ns']=2000000000
    elif case=='boot':b['boot_id']='b'*32
    elif case=='timeout':b=sample('2026-10-28T00:15:01+00:00',902000000000)
    elif case=='ttl':ttl=['2026-10-28T00:30:10+00:00']  # exact horizon is not greater
    elif case=='expired':ttl=['2026-10-27T00:00:00+00:00']
    else:ttl=[]
    with pytest.raises(lib.Rejected):lib.clock_window(a,b,ttl)


@pytest.mark.parametrize('raw',[b'A=one\nA=two\n',b'A="bad\n',b'A=one\x00two',b'A=\xff'])
def test_private_env_is_strict_and_never_shell_evaluated(lib,raw):
    with pytest.raises(lib.Rejected):lib.parse_environment(raw)


def test_environment_substitution_is_not_executed(lib):
    assert lib.parse_environment(b'A="$(private-command)"\n')['A']=='$(private-command)'


def test_production_signatures_have_no_adapter_sql_owner_or_clock_injection(lib):
    assert set(inspect.signature(lib.capture_server).parameters)=={'window_id','expected_producer_sha256'}
    assert 'adapter' not in inspect.signature(lib.validate_capture_freshness).parameters


def test_query_catalog_is_schema_qualified_and_clock_is_pg_catalog(lib):
    q=lib.capture_queries('private_scope')
    assert 'pg_catalog.clock_timestamp()' in q['clock']
    assert '"private_scope"."credit_lots"' in q['wallet']
    assert 'expire(' not in '\n'.join(q.values()) and 'FOR UPDATE' not in '\n'.join(q.values())
    with pytest.raises(lib.Rejected):lib.capture_queries('public; DELETE FROM credit_lots')


def test_docker_argv_and_environment_pin_local_daemon(lib):
    argv,env=lib.docker_invocation(['inspect','--type=container','source'])
    assert argv[:5]==['/usr/bin/docker','--host','unix:///run/docker.sock','--config',str(lib.DOCKER_CONFIG)]
    assert not any(k.startswith('DOCKER_') for k in env)
    assert not set(env)&{'HOME','XDG_CONFIG_HOME','DOCKER_HOST','DOCKER_CONTEXT','DOCKER_CONFIG'}


@pytest.mark.parametrize('statement',['UPDATE a SET b=1','SELECT 1; COMMIT; UPDATE a SET b=1','WITH x AS (DELETE FROM a RETURNING *) SELECT * FROM x','SET TRANSACTION READ WRITE'])
def test_unknown_or_mutating_sql_is_not_dispatched(lib,statement):
    policy=lib.ReadPolicy(lib.contracts(), 'private_scope')
    policy.set_relations([{'nspname':'private_scope','relname':'credit_lots','relkind':'r'}])
    with pytest.raises(lib.Rejected):policy.check(statement)


def test_real_pinned_core_read_templates_are_accepted_and_suffix_injection_is_not(lib):
    c=lib.contracts();p=lib.ReadPolicy(c,'private_scope')
    p.set_relations([{'nspname':'private_scope','relname':'credit_lots','relkind':'r'}])
    p.check(c.core.RELATIONS);p.check(lib.capture_queries('private_scope')['wallet'])
    with pytest.raises(lib.Rejected):p.check(lib.capture_queries('private_scope')['wallet']+'; DELETE FROM users')


class FakeLocal:
    def __init__(self,lib):
        self.lib=lib;self.calls=[];self.drift=False;self.closed=[];self.records={}
        for n,name in enumerate(lib.CONTAINERS,1):
            self.records[name]={'Name':'/'+name,'Id':str(n)*64,'Image':lib.contracts().core.IMAGE.split('@')[1]if n<3 else 'sha256:'+'9'*64,
                'State':{'Running':True,'StartedAt':'fixed'}}
    def docker(self,args,**kwargs):
        self.calls.append((list(args),kwargs))
        if args[:2]==['info','--format']:return b'"local-daemon"'
        if args[:2]==['inspect','--type=container']:
            result=deepcopy(self.records[args[2]])
            if self.drift:result['Id']='7'*64
            return json.dumps([result]).encode()
        assert args[:2]==['exec','-i'] and args[2]in [r['Id']for r in self.records.values()]
        raw=kwargs['data'].decode()
        if "'database'"in raw:
            src=next(s for s in self.lib.contracts().core.SOURCES if self.records[s[1]]['Id']==args[2])
            return json.dumps(dict(database=src[3],user=src[2],session=src[2],bootstrap=src[2])).encode()
        if 'pg_export_snapshot'in raw:raise AssertionError('keeper is separate')
        if 'pg_class c'in raw:return b'[{"nspname":"private_scope","relname":"credit_lots","relkind":"r"}]'
        if 'pg_catalog.clock_timestamp()'in raw:return b'{"db_utc":"2026-10-28T00:00:00+00:00","transaction_utc":"2026-10-28T00:00:00+00:00"}'
        raise AssertionError('unexpected synthetic SQL')
    @contextmanager
    def keeper(self,args):
        self.calls.append((list(args),{'keeper':True}))
        try:yield 'A-B-1'
        finally:self.closed.append(args[2])


def test_source_broker_executes_only_full_container_ids_and_registered_snapshot(lib):
    io=FakeLocal(lib);b=lib.SourceBroker(io)
    with b.snapshot('central','private_scope',catalog=True)as token:
        b.sql('central',token,lib.capture_queries('private_scope')['clock'])
    executions=[args for args,_ in io.calls if args[0]=='exec']
    assert executions and all(args[2]=='1'*64 for args in executions)
    assert not b.active and io.closed==['1'*64]


@pytest.mark.parametrize('case',['unregistered','cross_source','role','write','appended','unknown_command','replacement'])
def test_broker_rejects_scope_or_mutation_before_exec(lib,case):
    io=FakeLocal(lib);b=lib.SourceBroker(io);c=lib.contracts().core
    with b.snapshot('central','private_scope',catalog=True)as token:
        io.calls.clear()
        if case=='replacement':
            io.drift=True
            with pytest.raises(lib.Rejected):b.sql('central',token,lib.capture_queries('private_scope')['clock'])
        elif case=='unknown_command':
            with pytest.raises(lib.Rejected):b.command(['/usr/bin/docker','stop','source'])
        else:
            src=c.SOURCES[1]if case=='cross_source'else c.SOURCES[0]
            tok='A-B-9'if case=='unregistered'else token
            statement=lib.capture_queries('private_scope')['clock']
            if case=='write':statement='UPDATE a SET b=1'
            if case=='appended':statement+='; COMMIT; DELETE FROM a'
            user='other'if case=='role'else src[2]
            with pytest.raises(lib.Rejected):c.psql(b.command,src[1],user,src[3],statement,tok)
        assert not any(args[0]=='exec'for args,_ in io.calls)
        io.drift=False


def test_identity_snapshot_cannot_query_central_tables(lib):
    b=lib.SourceBroker(FakeLocal(lib))
    with b.snapshot('identity','private_scope')as token:
        with pytest.raises(lib.Rejected):b.sql('identity',token,lib.capture_queries('private_scope')['wallet'])


def test_host_docker_environment_is_never_inherited(lib,monkeypatch):
    import subprocess
    calls=[]
    monkeypatch.setenv('DOCKER_HOST','tcp://untrusted.invalid:2375');monkeypatch.setenv('DOCKER_CONTEXT','elsewhere')
    monkeypatch.setattr(lib.LocalIO,'guard',lambda self:None)
    monkeypatch.setattr(lib,'_private_directory',lambda *a,**k:None)
    def run(argv,**kwargs):
        calls.append((argv,kwargs));return b'ok'
    monkeypatch.setattr(lib,'_run_bounded',run)
    lib.LocalIO().docker(['inspect','--type=container','source'])
    argv,kwargs=calls[0]
    assert argv[:5]==lib.docker_invocation([])[0]
    assert not set(kwargs['env'])&{'HOME','XDG_CONFIG_HOME','DOCKER_HOST','DOCKER_CONTEXT','DOCKER_CONFIG'}


@pytest.mark.parametrize('raw',[b'X=1\nX=2\n',b'X=1\nY=2\n'])
def test_systemd_properties_reject_duplicate_or_extra_keys(lib,raw):
    with pytest.raises(lib.Rejected):lib._properties(raw,{'X'})


@pytest.mark.parametrize('suffix',['?host=elsewhere','?options=write','?sslmode=disable&sslmode=require','#ignored'])
def test_runtime_endpoint_cannot_be_overridden_by_query_or_fragment(lib,suffix):
    r={'NetworkSettings':{'Ports':{'5432/tcp':[{'HostIp':'127.0.0.1','HostPort':'5432'}]}}}
    with pytest.raises(lib.Rejected):lib._endpoint('postgres://user:synthetic@127.0.0.1/db'+suffix,'db',r)


def test_endpoint_and_proc_environment_match_exact_source_without_password_argv(lib):
    r={'NetworkSettings':{'Ports':{'5432/tcp':[{'HostIp':'127.0.0.1','HostPort':'15432'}]}}}
    lib._endpoint('postgres://user:PRIVATE_SENTINEL@127.0.0.1:15432/db?sslmode=disable','db',r)
    assert lib._process_environment(b'A=PRIVATE_SENTINEL\0')=={'A':'PRIVATE_SENTINEL'}
    with pytest.raises(lib.Rejected):lib._process_environment(b'A=1\0A=2\0')


@pytest.mark.parametrize('case',['orphan_waived','reservation_bool','scope_epoch','unknown_funding'])
def test_graph_closed_primitive_and_waived_ownership_contract(lib,case):
    d=fixture_finance()
    if case=='orphan_waived':
        d=waived_data()
        for key in ('execution_jobs','execution_invocations','execution_resolutions','execution_resolution_operations','execution_scope_hashes'):d[key]=[]
    elif case=='reservation_bool':
        for row in (d['credit_reservations'][0],d['execution_jobs'][0]):row['amount']=1
        d['credit_reservations'][0]['amount']=True
        d['credit_holds'][0]['amount']=1;d['credit_lots'][0].update(issued=1,available=0,held=1);d['execution_invocations'][0]['held']=1
    elif case=='scope_epoch':
        d['execution_jobs'][0]['scope']['authorization_epoch']=True;d['execution_invocations'][0]['scope']['authorization_epoch']=True
    else:
        for table in ('credit_reservations','execution_jobs','execution_invocations'):d[table][0]['funding_mode']='unapproved'
    with pytest.raises(lib.Rejected):lib.project_finance(d)


class SyntheticProducer:
    def __init__(self,lib):
        self.lib=lib
        path=Path(__file__).with_name('test_media_restore_freeze.py');s=importlib.util.spec_from_file_location('f1_fixture_f2',path)
        t=importlib.util.module_from_spec(s);s.loader.exec_module(t)
        self.a=t.Adapter(lib.f1());self.c=lib.contracts();self.samples=[];self.expiries=[];self.graph_evidence=[];self.ticks=0
        self.schema='private_scope';self.initial_binding=self.a.value;self.late=False
    def binding(self):return self.a.binding()
    def command(self,*a,**k):return self.a.command(*a,**k)
    def exported_snapshot(self):return self.a.exported_snapshot()
    def _tick(self,record=True):
        self.ticks+=1
        seconds=901 if self.late else self.ticks
        utc='2026-10-28T00:'+str(seconds//60).zfill(2)+':'+str(seconds%60).zfill(2)+'+00:00'
        value=sample(utc,seconds*1000000000)
        value.update(transaction_utc=utc,raw_boot_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
        if record:self.samples.append(value)
        return value
    def start_clock(self):return self._tick()
    def end_clock(self):
        self.expiries.extend(['2026-10-28T01:00:00+00:00']*5);return self._tick()
    def observations(self,token):
        self._tick();self._tick();self.expiries.extend(['2026-10-28T01:00:00+00:00']*5)
        self.graph_evidence.append(dict(active=0,waived=0,graph_sha256='a'*64))
        return self.a.observations(token)
    def sql(self,token,statement):return ['2026-10-28T01:00:00+00:00']*5
    def final_clock(self):
        with self.exported_snapshot()as token:expiries=self.sql(token,'synthetic-expiries')
        return self._tick(record=False),expiries


@pytest.fixture
def publisher(lib,tmp_path,monkeypatch):
    root=tmp_path/'capture';root.mkdir(mode=0o700);root.chmod(0o700)
    monkeypatch.setattr(lib,'ROOT',root);monkeypatch.setattr(lib.f1(),'ROOT_UID',os.geteuid())
    monkeypatch.setattr(lib,'_platform_gate',lambda expected:None)
    producer=SyntheticProducer(lib)
    monkeypatch.setattr(lib,'PinnedReadOnlyProducer',lambda:producer)
    def read(path,maximum,**kwargs):
        p=Path(path);assert p.is_relative_to(root),'test must not read any owner/source environment'
        return p.read_bytes()
    monkeypatch.setattr(lib,'_read_file',read)
    return producer,root


def test_factory_publisher_emits_only_unapproved_pair_and_freshness_is_not_permission(lib,publisher):
    p,root=publisher;r=lib.capture_server(window_id='a'*32,expected_producer_sha256='b'*64)
    assert r['status']=='captured_unapproved'and r['approved']is False and r['actions']==[]
    assert 'PRIVATE-SYNTHETIC-SENTINEL'not in json.dumps(r)
    check=lib.validate_capture_freshness(r['freeze_path'],r['freeze_sha256'],r['producer_receipt_path'],r['producer_receipt_sha256'],expected_producer_sha256='b'*64)
    assert check['approved']is False and check['actions']==[]
    with pytest.raises(lib.Rejected):lib.capture_server(window_id='a'*32,expected_producer_sha256='b'*64)


def test_partial_receipt_failure_does_not_complete_or_overwrite(lib,publisher,monkeypatch):
    p,root=publisher
    monkeypatch.setattr(lib,'_save_document',lambda *a:(_ for _ in ()).throw(OSError('PRIVATE_SENTINEL')))
    with pytest.raises(lib.Rejected,match='^capture_rejected$'):lib.capture_server(window_id='a'*32,expected_producer_sha256='b'*64)
    assert list((root/('a'*32)).glob('freeze-*-private.json'))
    with pytest.raises(lib.Rejected):lib.capture_server(window_id='a'*32,expected_producer_sha256='b'*64)


def test_final_clock_after_f_capture_is_part_of_publication_admission(lib,publisher,monkeypatch):
    p,root=publisher
    old=lib.f1().save_private_freeze
    def late(*args):
        result=old(*args);p.late=True;return result
    monkeypatch.setattr(lib.f1(),'save_private_freeze',late)
    with pytest.raises(lib.Rejected):lib.capture_server(window_id='a'*32,expected_producer_sha256='b'*64)
    assert not(root/('a'*32)/'producer-private.json').exists()


@pytest.mark.parametrize('case',['unknown_key','empty_samples','clock_jump','wrong_window','approved'])
def test_receipt_is_closed_and_every_clock_sample_is_revalidated(lib,publisher,case):
    p,root=publisher;r=lib.capture_server(window_id='a'*32,expected_producer_sha256='b'*64)
    path=Path(r['producer_receipt_path']);doc=json.loads(path.read_bytes())
    if case=='unknown_key':doc['ignored']=True
    elif case=='empty_samples':doc['clock_samples']=[]
    elif case=='clock_jump':doc['clock_samples'][1]['db_utc']='2026-10-28T01:00:00+00:00'
    elif case=='wrong_window':doc['window_id']='c'*32
    else:doc['approved']=True
    raw=lib.f1().canonical(doc);path.write_bytes(raw)
    with pytest.raises(lib.Rejected):lib.validate_capture_freshness(r['freeze_path'],r['freeze_sha256'],str(path),lib.sha(raw),expected_producer_sha256='b'*64)


def test_non_root_platform_gate_rejects_before_any_private_read(lib,monkeypatch):
    monkeypatch.setattr(lib.os,'getuid',lambda:1);monkeypatch.setattr(lib.os,'geteuid',lambda:1)
    monkeypatch.setattr(lib,'_read_file',lambda *a,**k:pytest.fail('must reject before reading'))
    with pytest.raises(lib.Rejected):lib._platform_gate('b'*64)


class IntegratedLocal(FakeLocal):
    def __init__(self,lib,adapter):
        super().__init__(lib);self.a=adapter;self.schema='fixture';self.seconds=0
        self.graph=fixture_finance()
        for rows in self.graph.values():
            for row in rows:
                if row.get('billing_account_id')=='b':row['billing_account_id']='w'
                if type(row.get('scope'))is dict:row['scope']['billing_account_id']='w'
        self.graph['credit_lots'][0].update(adapter.view['wallet'])
        e=adapter.view['media_state']['pending_exception']
        self.graph['media_jobs']=[deepcopy(e['job'])];self.graph['media_invocations']=[deepcopy(e['invocation'])]
        self.graph['credit_reservations'].append(deepcopy(e['reservation']))
        self.graph['credit_holds'].append(dict(reservation_id=e['reservation']['id'],lot_id='l',billing_account_id='w',amount=1000000))
        for n,(job,model)in enumerate(lib.contracts().mixed.SETTLED_JOBS.items()):
            j=deepcopy(e['job']);j.update(id=job,model=model,status='settled',actual=1000000,invocation_id='im'+str(n),reservation_id='rm'+str(n))
            i=deepcopy(e['invocation']);i.update(id=j['invocation_id'],model=model,held=0,spent=1000000)
            r=deepcopy(e['reservation']);r.update(id=j['reservation_id'],status='settled',actual=1000000)
            self.graph['media_jobs'].append(j);self.graph['media_invocations'].append(i);self.graph['credit_reservations'].append(r)
            self.graph['credit_holds'].append(dict(reservation_id=r['id'],lot_id='l',billing_account_id='w',amount=1000000))
        j=deepcopy(self.graph['execution_jobs'][0]);j.update(id='historical',reservation_id='rh',invocation_id='ih',status='settled',amount=47847,actual=47847,attempt_id='ah')
        r=deepcopy(self.graph['credit_reservations'][0]);r.update(id='rh',status='settled',amount=47847,actual=47847)
        i=deepcopy(self.graph['execution_invocations'][0]);i.update(id='ih',held=0,spent=47847)
        self.graph['execution_jobs'].append(j);self.graph['credit_reservations'].append(r);self.graph['execution_invocations'].append(i)
        self.graph['execution_receipts'].append(dict(job_id='historical',actual=47847,attempt_id='ah'))
        self.graph['execution_scope_hashes'].append(dict(id='historical',scope_hash='b'*64))
        self.graph['credit_holds'].append(dict(reservation_id='rh',lot_id='l',billing_account_id='w',amount=47847))
    def docker(self,args,**kwargs):
        if args[0]!='exec':return super().docker(args,**kwargs)
        self.calls.append((list(args),kwargs));name=next(k for k,v in self.records.items()if v['Id']==args[2 if args[1]=='-i'else 1])
        if args[1]!='-i':return self.a.command(['/usr/bin/docker','exec',name,*args[2:]],**kwargs)
        raw=kwargs['data'].decode();statement=raw.split("SET LOCAL extra_float_digits=3;\n",1)[1][:-len(';\nROLLBACK;\n')]
        q=self.lib.capture_queries(self.schema);m=self.lib.contracts().mixed;c=self.lib.contracts().core
        if statement==q['identity']:
            source=next(s for s in c.SOURCES if s[1]==name);value=dict(database=source[3],user=source[2],session=source[2],bootstrap=source[2])
        elif statement==q['clock']:
            self.seconds+=1;utc='2026-10-28T00:00:'+str(self.seconds).zfill(2)+'+00:00';value=dict(db_utc=utc,transaction_utc=utc)
        elif statement==q['graph']:value=self.graph
        elif statement==q['wallet']:value=self.a.view['wallet']
        elif statement==q['expires']:value=['2026-10-28T01:00:00+00:00']*5
        elif statement==m.window_sql(c,self.schema):value=self.a.view['media_state']
        elif statement==m.media_rows_sql(c,self.schema):value=self.a.view['registry']
        elif statement==m.migrations_sql(c,self.schema):value=self.a.value['migrations']
        else:return self.a.command(['/usr/bin/docker','exec','-i',name,*args[3:]],**kwargs)
        return json.dumps(value).encode()


def integrated_producer(lib):
    fixture=SyntheticProducer(lib);a=fixture.a;local=IntegratedLocal(lib,a)
    producer=object.__new__(lib.PinnedReadOnlyProducer);producer.broker=lib.SourceBroker(local);producer.c=lib.contracts()
    producer.schema='fixture';producer.initial_binding=a.value;producer.samples=[];producer.expiries=[];producer.graph_evidence=[]
    class Facts:
        def binding(self):return a.binding()
        def blobs(self,state,registry):return a.view['files'],a.view['media_inventory'],a.view['missing']
    class Clock:
        def sample(self,db):
            result=sample(db['db_utc'],local.seconds*1000000000)
            result.update(transaction_utc=db['transaction_utc'],raw_boot_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa');return result
    producer.facts=Facts();producer.clock=Clock()
    return producer,local


def test_actual_producer_methods_and_pinned_collector_cross_local_broker(lib):
    producer,local=integrated_producer(lib);first=producer.start_clock()
    created,expires=lib.clock_window(first,first,['2026-10-28T01:00:00+00:00'])
    f=lib.f1().capture_freeze(producer,lib.contracts(),window_id='a'*32,created_utc=created,expires_utc=expires)
    producer.end_clock()
    assert f['observations']['wallet']['held']==1009728 and len(producer.graph_evidence)==2
    assert not producer.broker.active and len(local.closed)==4


def test_full_graph_and_media_observation_must_describe_the_same_pending_rows(lib):
    producer,local=integrated_producer(lib)
    local.graph['media_jobs'][0]['scope']['authorization_epoch']=2
    local.graph['media_invocations'][0]['scope']['authorization_epoch']=2
    # Both graph sides are internally coherent; the separately returned media
    # window still has the original epoch. The producer must catch the divergence.
    with producer.exported_snapshot()as token:
        with pytest.raises(lib.Rejected):producer.observations(token)


@pytest.mark.parametrize('kind,prefix',[('api','CENTRAL_'),('console','CENTRAL_CONSOLE_')])
def test_runtime_database_uses_actual_product_specific_keys(lib,kind,prefix):
    private={prefix+'DATABASE_URL':'postgres://u:PRIVATE_SENTINEL@127.0.0.1:15432/db',prefix+'DB_SCHEMA':'private_scope'}
    assert lib._runtime_database(kind,private,dict(private),'private_scope')==private[prefix+'DATABASE_URL']
    for wrong in ({'CENTRAL_DATABASE_URL':private[prefix+'DATABASE_URL'],'CENTRAL_DB_SCHEMA':'private_scope'}if kind=='console'else {},dict(private,**{prefix+'DB_SCHEMA':'foreign'})):
        with pytest.raises(lib.Rejected):lib._runtime_database(kind,private,wrong,'private_scope')


def test_keeper_read_only_lifetime_and_own_process_cleanup(lib,monkeypatch):
    import io
    calls=[]
    class Process:
        pid=424242
        def __init__(self):
            rd,wr=os.pipe();os.write(wr,b'A-B-1\n');os.close(wr)
            self.stdout=os.fdopen(rd,'rb');self.stdin=io.BytesIO();self.stderr=None;self.done=False;self.returncode=None;self.writes=[]
            original=self.stdin.write
            def write(raw):
                self.writes.append(raw)
                if raw.startswith(b'ROLLBACK'):self.done=True
                return original(raw)
            self.stdin.write=write
        def poll(self):return 0 if self.done else None
        def wait(self,timeout):self.done=True;self.returncode=0;return 0
    procs=[]
    def popen(argv,**kwargs):
        calls.append((argv,kwargs));p=Process();procs.append(p);return p
    monkeypatch.setattr(lib.LocalIO,'guard',lambda self:None)
    monkeypatch.setattr(lib,'_private_directory',lambda *a,**k:None)
    monkeypatch.setattr(lib.subprocess,'Popen',popen)
    monkeypatch.setattr(lib,'_observe_child_exit',lambda p:0 if p.done else None)
    kills=[]
    def killed(pid,sig):
        if sig==0:raise ProcessLookupError()
        kills.append((pid,sig))
    monkeypatch.setattr(lib.os,'killpg',killed)
    local=lib.LocalIO()
    with local.keeper(['exec','-i','1'*64,'psql'])as token:assert token=='A-B-1'
    assert b'REPEATABLE READ READ ONLY'in procs[0].writes[0]and procs[0].writes[-1]==b'ROLLBACK;\n\\q\n'
    assert calls[0][1]['start_new_session']is True and kills==[]
    with pytest.raises(RuntimeError,match='synthetic'):
        with local.keeper(['exec','-i','1'*64,'psql']):raise RuntimeError('synthetic')
    assert kills==[(424242,lib.signal.SIGKILL)]and all(p.stdin.closed and p.stdout.closed for p in procs)
    assert all('stop'not in argv and 'kill'not in argv for argv,_ in calls)


@pytest.mark.parametrize('stream',['stdout','stderr'])
def test_bounded_runner_rejects_real_synthetic_excess_without_leaking(lib,monkeypatch,stream):
    import sys
    processes=[];real=lib.subprocess.Popen
    def popen(*a,**k):
        p=real(*a,**k);processes.append(p);return p
    monkeypatch.setattr(lib.subprocess,'Popen',popen)
    command=[sys.executable,'-I','-B','-c',"import os,time;os.write("+('1'if stream=='stdout'else '2')+",b'PRIVATE_SENTINEL'*65536);time.sleep(30)"]
    with pytest.raises(lib.Rejected,match='^capture_rejected$'):
        lib._run_bounded(command,env={'PATH':'/usr/bin:/bin'},maximum=4096,timeout=3)
    assert len(processes)==1 and processes[0].poll()is not None
    assert all(v.closed for v in (processes[0].stdin,processes[0].stdout,processes[0].stderr))


def test_bounded_runner_real_synthetic_success_and_input(lib):
    import sys
    command=[sys.executable,'-I','-B','-c',"import os;data=b''\nwhile True:\n p=os.read(0,1024)\n if not p:break\n data+=p\nos.write(1,data);os.write(2,b'private diagnostic')"]
    assert lib._run_bounded(command,env={'PATH':'/usr/bin:/bin'},data=b'a'*4096,maximum=4096,timeout=3)==b'a'*4096


@pytest.mark.parametrize('case',['timeout','cancel'])
def test_bounded_runner_real_synthetic_child_reaped_on_timeout_or_cancel(lib,monkeypatch,case):
    import sys
    processes=[];real=lib.subprocess.Popen
    def popen(*a,**k):
        process=real(*a,**k);processes.append(process);return process
    monkeypatch.setattr(lib.subprocess,'Popen',popen)
    if case=='cancel':
        monkeypatch.setattr(lib.select,'select',lambda *a,**k:(_ for _ in ()).throw(KeyboardInterrupt()))
    command=[sys.executable,'-I','-B','-c','import time;time.sleep(30)']
    with pytest.raises(lib.Rejected,match='^capture_rejected$'):
        lib._run_bounded(command,env={'PATH':'/usr/bin:/bin'},maximum=1024,timeout=0.05)
    assert len(processes)==1 and processes[0].poll()is not None
    assert all(stream.closed for stream in (processes[0].stdin,processes[0].stdout,processes[0].stderr))


@pytest.mark.parametrize('status',['pending_reconciliation','settled','released','waived'])
@pytest.mark.parametrize('case',['hold_account','lot_funding','lot_bool'])
def test_original_hold_allocation_requires_account_and_immutable_funding_match(lib,status,case):
    d=waived_data()if status=='waived'else fixture_finance()
    if status in {'settled','released'}:
        d['execution_jobs'][0]['status']=d['credit_reservations'][0]['status']=status
        actual=9728 if status=='settled'else 0
        d['execution_jobs'][0]['actual']=d['credit_reservations'][0]['actual']=actual
        d['execution_invocations'][0].update(held=0,spent=actual)
        d['credit_lots'][0].update(held=0,available=272 if actual else 10000,spent=actual)
        if actual:d['execution_receipts']=[dict(job_id='j',actual=actual,attempt_id='a')]
    if case=='hold_account':d['credit_holds'][0]['billing_account_id']='foreign'
    elif case=='lot_funding':d['credit_lots'][0]['test_only']=True
    else:d['credit_lots'][0]['test_only']=0
    with pytest.raises(lib.Rejected):lib.project_finance(d)


def test_current_mutable_frozen_or_expiry_flags_do_not_reject_original_valid_hold(lib):
    d=waived_data();d['credit_lots'][0].update(frozen=True,expires_at='2000-01-01')
    assert lib.project_finance(d)[2]['waived']==1


@pytest.mark.parametrize('argv',[[],['/usr/bin/docker'],['/usr/bin/docker','exec'],['/usr/bin/docker','exec','-i','unknown']])
def test_broker_malformed_command_is_sanitized_and_never_dispatched(lib,argv):
    local=FakeLocal(lib);broker=lib.SourceBroker(local);local.calls.clear()
    with pytest.raises(lib.Rejected,match='^capture_rejected$'):broker.command(argv)
    assert local.calls==[]


def test_broker_does_not_accept_caller_environment_or_unknown_keyword(lib):
    local=FakeLocal(lib);broker=lib.SourceBroker(local)
    with broker.snapshot('central','private_scope')as token:
        local.calls.clear();core=lib.contracts().core
        def injecting(argv,**kwargs):return broker.command(argv,env={'PGHOST':'remote.invalid'},**kwargs)
        with pytest.raises(lib.Rejected):core.psql(injecting,*core.SOURCES[0][1:],lib.capture_queries('private_scope')['clock'],token)
        assert local.calls==[]


@pytest.mark.parametrize('case',['amount','missing'])
def test_historical_waived_hold_population_stays_complete(lib,case):
    d=waived_data()
    if case=='amount':d['credit_holds'][0]['amount']=1
    else:d['credit_holds']=[]
    with pytest.raises(lib.Rejected):lib.project_finance(d)


@pytest.mark.parametrize('requested,bound,accepted',[
 ('127.0.0.1','127.0.0.1',True),('::1','::1',True),
 ('127.0.0.1','::1',False),('::1','127.0.0.1',False),
 ('localhost','127.0.0.1',False),('localhost','::1',False)])
def test_endpoint_literal_family_and_unresolved_localhost_contract(lib,requested,bound,accepted):
    record={'NetworkSettings':{'Ports':{'5432/tcp':[{'HostIp':bound,'HostPort':'15432'}]}}}
    host='['+requested+']'if ':'in requested else requested
    if accepted:lib._endpoint('postgres://u:synthetic@'+host+':15432/db','db',record)
    else:
        with pytest.raises(lib.Rejected):lib._endpoint('postgres://u:synthetic@'+host+':15432/db','db',record)


@pytest.mark.parametrize('field',['utc','db_utc','monotonic_ns'])
def test_receipt_intermediate_clock_retreat_is_rejected(lib,publisher,field):
    producer,_=publisher;result=lib.capture_server(window_id='a'*32,expected_producer_sha256='b'*64)
    freeze=lib.f1().load_private_freeze(result['freeze_path'],result['freeze_sha256'],lib.contracts())
    doc=lib.f1().decode_canonical(Path(result['producer_receipt_path']).read_bytes())
    doc['clock_samples'][2][field]=doc['clock_samples'][0][field]
    if field=='db_utc':doc['clock_samples'][2]['transaction_utc']=doc['clock_samples'][0]['transaction_utc']
    with pytest.raises(lib.Rejected):lib._receipt(doc,freeze,'b'*64,producer.expiries)


@pytest.mark.parametrize('case',['receipt_io','final_query','final_boot'])
def test_final_admission_rejects_late_durability_query_or_boot(lib,publisher,monkeypatch,case):
    p,root=publisher
    if case=='receipt_io':
        original=lib._save_document
        def slow(*a):
            result=original(*a);p.late=True;return result
        monkeypatch.setattr(lib,'_save_document',slow)
        with pytest.raises(lib.Rejected):lib.capture_server(window_id='a'*32,expected_producer_sha256='b'*64)
        assert (root/('a'*32)/'producer-private.json').exists()
        return
    result=lib.capture_server(window_id='a'*32,expected_producer_sha256='b'*64)
    if case=='final_query':
        original=p.sql
        def slow(*a):
            result=original(*a);p.late=True;return result
        monkeypatch.setattr(p,'sql',slow)
    else:
        original=p.final_clock
        def changed_boot():
            s,expiries=original();s['boot_id']='b'*32;s['raw_boot_id']='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb';return s,expiries
        monkeypatch.setattr(p,'final_clock',changed_boot)
    with pytest.raises(lib.Rejected):lib.validate_capture_freshness(result['freeze_path'],result['freeze_sha256'],result['producer_receipt_path'],result['producer_receipt_sha256'],expected_producer_sha256='b'*64)


def test_final_sample_follows_closing_guards_and_does_not_mutate_receipt_samples(lib,monkeypatch):
    p,local=integrated_producer(lib);p.start_clock();before=deepcopy(p.samples)
    original=local.keeper;closed=[]
    @contextmanager
    def closing(*a,**k):
        with original(*a,**k)as token:yield token
        closed.append(True)
    monkeypatch.setattr(local,'keeper',closing)
    real_sample=p.clock.sample
    def observed(db):
        assert closed and not p.broker.active
        return real_sample(db)
    monkeypatch.setattr(p.clock,'sample',observed)
    final,expiries=p.final_clock()
    assert p.samples==before and len(expiries)==5 and final['boot_id']==p.initial_binding['boot_id']


def test_final_closing_delay_is_measured_after_source_guards(lib,monkeypatch):
    p,local=integrated_producer(lib);first=p.start_clock();original=local.keeper;closed=[]
    @contextmanager
    def closing(*a,**k):
        with original(*a,**k)as token:yield token
        closed.append(True)
    monkeypatch.setattr(local,'keeper',closing)
    def late(db):
        assert closed and not p.broker.active
        s=sample('2026-10-28T00:15:01+00:00',901_000_000_000)
        s.update(db_utc=db['db_utc'],transaction_utc=db['transaction_utc'],raw_boot_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa');return s
    monkeypatch.setattr(p.clock,'sample',late)
    final,expiries=p.final_clock()
    with pytest.raises(lib.Rejected):lib._final_admission(first,first,final,'2026-10-28T00:15:00+00:00',expiries)


def test_successful_final_admission_leaves_persisted_receipt_bytes_unchanged(lib,publisher,monkeypatch):
    p,_=publisher;writes=[];original=lib._save_document
    def saved(path,value):
        digest=original(path,value);writes.append((path,path.read_bytes(),len(value['clock_samples'])));return digest
    monkeypatch.setattr(lib,'_save_document',saved)
    result=lib.capture_server(window_id='a'*32,expected_producer_sha256='b'*64)
    path,raw,count=writes[0]
    assert path.read_bytes()==raw and lib.sha(raw)==result['producer_receipt_sha256']
    assert len(p.samples)==count==len(lib.f1().decode_canonical(raw)['clock_samples'])


def test_nonreaping_observer_retains_own_exited_leader_until_cleanup(lib):
    process=subprocess.Popen([sys.executable,'-I','-B','-c','pass'],stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
    try:
        deadline=lib.time.monotonic()+3
        while lib._observe_child_exit(process)is None:
            assert lib.time.monotonic()<deadline;lib.time.sleep(.01)
        assert process.returncode is None
        assert lib._observe_child_exit(process)==0
        assert process.wait(timeout=3)==0
    finally:
        if process.returncode is None:
            os.killpg(process.pid,lib.signal.SIGKILL);process.wait(timeout=3)
        for stream in (process.stdin,process.stdout,process.stderr):stream.close()


def test_bounded_runner_reaps_group_with_exited_leader_and_living_descendant(lib,tmp_path,monkeypatch):
    processes=[];real=lib.subprocess.Popen
    def popen(*a,**k):
        p=real(*a,**k);processes.append(p);return p
    monkeypatch.setattr(lib.subprocess,'Popen',popen)
    pidfile=tmp_path/'own-child-pid'
    code='import os,time\npid=os.fork()\nif pid:\n open('+repr(str(pidfile))+',"w").write(str(pid))\n os._exit(0)\ntime.sleep(10)\n'
    child=None
    try:
        with pytest.raises(lib.Rejected):lib._run_bounded([sys.executable,'-I','-B','-c',code],env={'PATH':'/usr/bin:/bin'},maximum=1024,timeout=.3)
        assert len(processes)==1 and processes[0].returncode==0
        child=int(pidfile.read_text())
        with pytest.raises(ProcessLookupError):os.getpgid(child)
        assert all(s.closed for s in (processes[0].stdin,processes[0].stdout,processes[0].stderr))
    finally:
        if pidfile.exists():child=int(pidfile.read_text())
        if child:
            try:
                if os.getpgid(child)==processes[0].pid:os.killpg(processes[0].pid,lib.signal.SIGKILL)
            except ProcessLookupError:pass


def test_keeper_cleans_own_living_descendant_after_group_leader_exits(lib,tmp_path,monkeypatch):
    pidfile=tmp_path/'own-keeper-child'
    code='import os,time\npid=os.fork()\nif pid:\n open('+repr(str(pidfile))+',"w").write(str(pid))\n os.write(1,b"A-B-1\\n")\n os._exit(0)\ntime.sleep(10)\n'
    monkeypatch.setattr(lib.LocalIO,'guard',lambda self:None);monkeypatch.setattr(lib,'_private_directory',lambda *a,**k:None)
    monkeypatch.setattr(lib,'docker_invocation',lambda args:([sys.executable,'-I','-B','-c',code],{'PATH':'/usr/bin:/bin'}))
    processes=[];real=lib.subprocess.Popen
    def popen(*a,**k):
        p=real(*a,**k);processes.append(p);return p
    monkeypatch.setattr(lib.subprocess,'Popen',popen)
    try:
        with pytest.raises(RuntimeError,match='synthetic keeper failure'):
            with lib.LocalIO().keeper(['exec','-i','1'*64,'psql']):raise RuntimeError('synthetic keeper failure')
        assert len(processes)==1 and processes[0].returncode==0
        with pytest.raises(ProcessLookupError):os.getpgid(int(pidfile.read_text()))
        assert processes[0].stdin.closed and processes[0].stdout.closed
    finally:
        if pidfile.exists():
            try:
                if os.getpgid(int(pidfile.read_text()))==processes[0].pid:os.killpg(processes[0].pid,lib.signal.SIGKILL)
            except ProcessLookupError:pass


def test_cleanup_refuses_lost_child_identity_before_any_group_signal(lib,monkeypatch):
    calls=[]
    class Process:
        pid=1234;returncode=None;stdin=None;stdout=None;stderr=None
        def wait(self,timeout):calls.append('reap');return 0
    monkeypatch.setattr(lib,'_observe_child_exit',lambda p:(_ for _ in ()).throw(ChildProcessError('synthetic ownership lost')))
    monkeypatch.setattr(lib.os,'killpg',lambda *a:calls.append('signal'))
    with pytest.raises(lib.Rejected,match='^capture_rejected$'):lib._cleanup_process(Process(),failed=True)
    assert calls==['reap']


def test_failed_group_signal_still_reaps_closes_and_sanitizes(lib,monkeypatch):
    import io
    calls=[]
    class Process:
        pid=1234;returncode=None
        stdin=io.BytesIO();stdout=io.BytesIO();stderr=io.BytesIO()
        def wait(self,timeout):calls.append('reap');self.returncode=7;return 7
    monkeypatch.setattr(lib,'_observe_child_exit',lambda p:7)
    def denied(pid,sig):
        if sig==0:raise ProcessLookupError()
        calls.append('signal');raise PermissionError('PRIVATE_SENTINEL')
    monkeypatch.setattr(lib.os,'killpg',denied);p=Process()
    with pytest.raises(lib.Rejected,match='^capture_rejected$'):lib._cleanup_process(p,failed=True)
    assert calls==['signal','reap']and all(s.closed for s in (p.stdin,p.stdout,p.stderr))


def test_real_nonzero_child_exit_never_leaks_private_stderr_or_open_pipes(lib,monkeypatch):
    processes=[];real=lib.subprocess.Popen
    def popen(*a,**k):
        p=real(*a,**k);processes.append(p);return p
    monkeypatch.setattr(lib.subprocess,'Popen',popen)
    with pytest.raises(lib.Rejected,match='^capture_rejected$'):
        lib._run_bounded([sys.executable,'-I','-B','-c','import os;os.write(2,b"PRIVATE_SENTINEL");raise SystemExit(7)'],env={'PATH':'/usr/bin:/bin'},maximum=1024,timeout=3)
    assert len(processes)==1 and processes[0].returncode==7
    assert all(s.closed for s in (processes[0].stdin,processes[0].stdout,processes[0].stderr))


@pytest.mark.parametrize('case',['alive','exit','signal','foreign','bad_signal','unsupported','already_reaped'])
def test_production_waitid_observer_preserves_nonreaping_flags_and_rejects_wrong_identity(lib,monkeypatch,case):
    from types import SimpleNamespace
    class Process:pid=1234;returncode=None
    for key,value in {'P_PID':1,'WEXITED':4,'WNOHANG':1,'WNOWAIT':32,'CLD_EXITED':1,'CLD_KILLED':2,'CLD_DUMPED':3}.items():
        monkeypatch.setattr(lib.os,key,value,raising=False)
    result=None if case=='alive'else SimpleNamespace(si_pid=1234,si_signo=lib.signal.SIGCHLD,si_code=1,si_status=7)
    if case=='signal':result.si_code=2;result.si_status=9
    if case=='foreign':result.si_pid=4321
    if case=='bad_signal':result.si_signo=lib.signal.SIGTERM
    calls=[]
    def waitid(*args):calls.append(args);return result
    monkeypatch.setattr(lib.os,'waitid',waitid,raising=False)
    if case=='unsupported':monkeypatch.setattr(lib.os,'waitid',None)
    p=Process()
    if case=='already_reaped':p.returncode=0
    if case in {'foreign','bad_signal','unsupported','already_reaped'}:
        with pytest.raises(lib.Rejected):lib._production_observer(p)
    else:assert lib._production_observer(p)==(None if case=='alive'else -9 if case=='signal'else 7)
    if calls:assert calls==[(1,1234,4|1|32)]
    assert p.returncode==(0 if case=='already_reaped'else None)


def test_failed_cleanup_waits_for_group_disappearance_without_resignalling(lib,monkeypatch):
    events=[]
    class Process:
        pid=1234;returncode=None;stdin=None;stdout=None;stderr=None
        def wait(self,timeout):events.append('reap');self.returncode=0;return 0
    monkeypatch.setattr(lib,'_observe_child_exit',lambda p:0)
    checks=[]
    def group(pid,sig):
        events.append(sig)
        if sig==0:
            checks.append(True)
            if len(checks)==1:return
            if len(checks)==2:raise PermissionError('synthetic transition')
            raise ProcessLookupError()
    monkeypatch.setattr(lib.os,'killpg',group)
    lib._cleanup_process(Process(),failed=True)
    assert events==[lib.signal.SIGKILL,'reap',0,0,0]


def test_failed_cleanup_group_deadline_is_bounded_and_already_reaped_never_resignals(lib,monkeypatch):
    import io
    events=[]
    class Process:
        pid=1234;returncode=None
        stdin=io.BytesIO();stdout=io.BytesIO();stderr=io.BytesIO()
        def wait(self,timeout):self.returncode=0;return 0
    monkeypatch.setattr(lib,'_observe_child_exit',lambda p:lib.require(p.returncode is None))
    monkeypatch.setattr(lib.os,'killpg',lambda pid,sig:events.append(sig))
    clock={'now':0};monkeypatch.setattr(lib.time,'monotonic',lambda:clock['now'])
    def advance(*a):clock['now']+=3;return [],[],[]
    monkeypatch.setattr(lib.select,'select',advance)
    p=Process()
    with pytest.raises(lib.Rejected):lib._cleanup_process(p,failed=True)
    assert events==[lib.signal.SIGKILL,0]and all(s.closed for s in (p.stdin,p.stdout,p.stderr))
    with pytest.raises(lib.Rejected):lib._cleanup_process(p,failed=True)
    assert events==[lib.signal.SIGKILL,0]  # No signal or group probe after identity was reaped.


@pytest.mark.parametrize('status',['settled','released'])
def test_generic_terminal_reservation_history_does_not_require_a_model_job(lib,status):
    d=fixture_finance();r=d['credit_reservations'][0];actual=9728 if status=='settled'else 0
    r.update(status=status,actual=actual)
    d['credit_lots'][0].update(held=0,spent=actual,available=10000-actual)
    d['execution_jobs']=[];d['execution_invocations']=[];d['execution_scope_hashes']=[]
    wallet,pending,_=lib.project_finance(d)
    assert wallet['held']==0 and pending==[]


@pytest.mark.parametrize('wall,db',[('2026-10-28T00:00:04+00:00','2026-10-28T00:00:01+00:00'),('2026-10-28T00:00:02+00:00','2026-10-28T00:00:05+00:00')])
def test_final_ttl_uses_explicit_conservative_completion_upper_bound(lib,wall,db):
    from datetime import timedelta
    first=sample('2026-10-28T00:00:00+00:00',0)
    current=sample(wall,int((lib._utc(wall)-lib._utc(first['utc'])).total_seconds())*1_000_000_000,db)
    current.update(transaction_utc=db,raw_boot_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
    upper=max(lib._utc(wall),lib._utc(db))+timedelta(seconds=5)
    with pytest.raises(lib.Rejected):lib._final_admission(first,first,current,'2026-10-28T00:15:00+00:00',[(upper+timedelta(seconds=1800)).isoformat()]*5)
    lib._final_admission(first,first,current,'2026-10-28T00:15:00+00:00',[(upper+timedelta(seconds=1800,microseconds=1)).isoformat()]*5)


def test_cleanup_keyboard_interrupt_reentry_keeps_first_total_deadline_and_owned_identity(lib,monkeypatch):
    import io
    clock={'now':10};signals=[];waits=[]
    class Process:
        pid=424242;returncode=None;attempts=0
        stdin=io.BytesIO();stdout=io.BytesIO();stderr=io.BytesIO()
        def wait(self,timeout):
            waits.append(timeout);self.attempts+=1
            if self.attempts==1:raise KeyboardInterrupt()
            self.returncode=0;return 0
    def observe(p):lib.require(p.returncode is None);return 0
    monkeypatch.setattr(lib,'_observe_child_exit',observe)
    monkeypatch.setattr(lib.time,'monotonic',lambda:clock['now'])
    def signal(pid,sig):
        signals.append(sig)
        if sig==0:raise ProcessLookupError()
    monkeypatch.setattr(lib.os,'killpg',signal);p=Process()
    with pytest.raises(lib.Rejected):lib._cleanup_process(p,failed=True)
    assert p.returncode is None and p._easel_cleanup_deadline==22
    clock['now']=15;lib._cleanup_process(p,failed=True)
    assert p._easel_cleanup_deadline==22 and waits==[10,7]
    assert signals==[lib.signal.SIGKILL,lib.signal.SIGKILL,0]
    with pytest.raises(lib.Rejected):lib._cleanup_process(p,failed=True)
    assert signals==[lib.signal.SIGKILL,lib.signal.SIGKILL,0]


def test_cleanup_exhausted_shared_budget_never_signals_or_waits_blockingly(lib,monkeypatch):
    import io
    calls=[]
    class Process:
        pid=1234;returncode=None;_easel_cleanup_deadline=22
        stdin=io.BytesIO();stdout=io.BytesIO();stderr=io.BytesIO()
        def wait(self,timeout):calls.append(('wait',timeout));raise lib.subprocess.TimeoutExpired('synthetic',timeout)
    monkeypatch.setattr(lib.time,'monotonic',lambda:23)
    monkeypatch.setattr(lib,'_observe_child_exit',lambda p:calls.append('observe'))
    monkeypatch.setattr(lib.os,'killpg',lambda *a:calls.append('signal'));p=Process()
    with pytest.raises(lib.Rejected):lib._cleanup_process(p,failed=True)
    assert calls==[('wait',0)]and all(s.closed for s in (p.stdin,p.stdout,p.stderr))
