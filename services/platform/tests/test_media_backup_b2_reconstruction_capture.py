"""Offline owned read/custody tests. All native transport is replaced privately."""
import importlib.util
import sys
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests'),str(ROOT/'scripts')]
NAME='platform_service.media_backup_b2.reconstruction_capture'
class Availability(unittest.TestCase):
    def test_fixed_owned_capture_exists(self):
        self.assertIsNotNone(importlib.util.find_spec(NAME), 'fixed owned capture producer missing')

    def test_producer_has_no_caller_driver_clock_sql_or_phase_api(self):
        if importlib.util.find_spec(NAME) is None:
            self.fail('producer fixed native factory missing')
        import inspect
        from platform_service.media_backup_b2.reconstruction_capture import OwnedSourceProducer,OwnedSourceCapture
        self.assertEqual(tuple(inspect.signature(OwnedSourceProducer._from_source_owner).parameters),
          ('original_owner','scope','original_read_context','original_comparison','source_connection','started_at_ns','deadline_ns','cancellation','grant_graph_scope'))
        self.assertTrue(all(hasattr(OwnedSourceCapture,n) for n in ('_before','_during','_after','_finish_pipeline','close_publication')))

import copy
import hashlib
import threading
import time
from types import SimpleNamespace as NS
from unittest.mock import patch
from test_media_backup_b2_reconstruction_context import fixtures
from platform_service.media_backup_b2 import reconstruction_capture as c
from platform_service.media_backup_b2.contracts import B2Error
from platform_service.media_maintenance import filesystem as fs

class Cursor:
    def __init__(self,rows=(),status=None):self.rows=list(rows);self.statusmessage=status
    def fetchmany(self,n):result=self.rows[:n];del self.rows[:n];return result

class Native:
    def __init__(self,parts,fail=None):
        self.parts=parts;self.fail=fail;self.events=[];self.closed=False;self.info=NS(backend_pid=123)
    def execute(self,q,params=None):
        self.events.append(str(q))
        if self.fail and self.fail in str(q):raise OSError('private native ambiguity')
        if q==c._GRANTS:
            self.graph_params=params
            return Cursor([(43,'owner',44,'runtime',10,'postgres',False,True,True,'000123')])
        if q in (c._IDENTITY,c._ROLES,c._DATABASE,c._SETTINGS):
            index=(c._IDENTITY,c._ROLES,c._DATABASE,c._SETTINGS).index(q)+1
            rows=[self.parts[index]] if index==1 else self.parts[index]
            return Cursor(rows)
        return Cursor(status='BEGIN' if str(q).startswith('BEGIN') else 'ROLLBACK' if q=='ROLLBACK' else 'SET')
    def close(self):
        self.events.append('CLOSE')
        if self.fail=='CLOSE':raise OSError('private close ambiguity')
        self.closed=True

class Keeper:
    def __init__(self,timezone='Asia/Shanghai',datestyle='ISO, MDY'):self.rows=[(timezone,datestyle)];self.events=[]
    def execute(self,q):self.events.append(q);return Cursor(self.rows)

class ProducerTests(unittest.TestCase):
    def setup_read(self,parts=None):
        parts=parts or fixtures();scope=parts[0];cancel=threading.Event();now=time.monotonic_ns()
        owner=NS(name=scope.database_name,container_id=scope.container_id)
        p=c.OwnedSourceProducer._from_source_owner(owner,scope,original_read_context=scope,
            original_comparison={'original':'comparison'},source_connection='private-dsn',started_at_ns=now-1,
            deadline_ns=now+60_000_000_000,cancellation=cancel)
        owner._reconstruction_producer=p
        self.addCleanup(patch.stopall)
        patch.object(fs,'_duplicate_exclusive',side_effect=lambda fd:fd+1).start()
        patch.object(fs,'_close_exclusive',return_value=None).start()
        window=fs.ExclusiveWindow(NS(verify=lambda:None),-10,object(),_mint=fs._MINT)
        keeper=Keeper();inputs=NS(_deadline=p._deadline,_keepers={'central':keeper},
            _keeper_snapshots={'central':'00000003-0000001B-1'},_keeper_stamps={'central':(123,'10:20:11,15')})
        pair=NS(_guard=lambda:None,_exclusive=window,_inputs=inputs)
        p._pair=pair
        native=[]
        def connect(dsn,remaining):
            value=Native(copy.deepcopy(parts));native.append(value);return value
        patch.object(c,'_connect_native',side_effect=connect).start()
        patch.object(c,'_literal_command',side_effect=lambda shape,value:shape.format(repr(value))).start()
        return p,keeper,window,native

    def test_fix_success_boundary_pins_default_original_samples_full_and_stable(self):
        import dataclasses
        from platform_service.media_backup_b2.contracts import CapturedSource
        p,k,w,n=self.setup_read()
        for phase in (c.SourcePhase.BEFORE,c.SourcePhase.DURING,c.SourcePhase.AFTER):
            observer=Keeper() if phase is c.SourcePhase.AFTER else k
            p._read(phase,observer)
            if phase is c.SourcePhase.AFTER:p._observe_after_close(observer,None)
        pair=c.OwnedSourceCapture(c._SEAL);p._pair=pair
        pair._inputs=NS(companion=pair);pair._producers=(p,p);pair._contexts=(p._comparison,p._comparison)
        pair._grant_scopes=(None,None);pair._grant_scope_pins=(None,None)
        pair._grant_objects=None;pair._grant_pins=None;pair._captured=None
        pair._sample_objects=None;pair._sample_pins=None
        pair._lock=threading.RLock();pair._guard=lambda:None
        captured=object.__new__(CapturedSource);object.__setattr__(captured,'plan',{})
        pair._inputs._capture=captured
        pair._bind_captured(captured)
        phase=c.SourcePhase.BEFORE;sample=p._samples[phase]
        pair._verify_bindings()
        p._samples[phase]=dataclasses.replace(sample)
        with self.assertRaises(B2Error):pair._verify_bindings()
        p._samples[phase]=sample
        object.__setattr__(sample,'identity',dataclasses.replace(sample.identity,backend_pid=sample.identity.backend_pid+1))
        with self.assertRaises(B2Error):pair._verify_bindings()

    def test_optional_graph_input_requires_original_precapture_authorization(self):
        import inspect
        self.assertIn('grant_graph_scope',inspect.signature(c.OwnedSourceProducer._from_source_owner).parameters)
        parts=fixtures();scope=parts[0];owner=NS(name=scope.database_name,container_id=scope.container_id)
        now=time.monotonic_ns()
        with self.assertRaises(B2Error):
            c.OwnedSourceProducer._from_source_owner(owner,scope,original_read_context=scope,
                original_comparison=object(),source_connection='private',started_at_ns=now-1,
                deadline_ns=now+60_000_000_000,cancellation=threading.Event(),grant_graph_scope=object())

    def graph_read(self):
        from platform_service.media_backup_b2.reconstruction_grants import GrantGraphScope
        previous,k,w,n=self.setup_read();scope=previous._scope
        graph=GrantGraphScope(scope.kind,scope.selected_role_names)
        owner=previous._owner
        authorization=NS(_verify=lambda:None,original_comparison=previous._comparison,grant_graph_scope=graph)
        owner._replay_authorization=authorization
        p=c.OwnedSourceProducer._from_source_owner(owner,scope,original_read_context=scope,
            original_comparison=previous._comparison,source_connection='private',started_at_ns=previous._started,
            deadline_ns=previous._deadline,cancellation=previous._cancel,grant_graph_scope=graph)
        owner._reconstruction_producer=p;p._pair=previous._pair
        return p,k,w,n

    def test_optional_graph_fifth_same_transaction_shared_sample_and_after_close(self):
        p,k,w,n=self.graph_read()
        for phase,observer in ((c.SourcePhase.BEFORE,k),(c.SourcePhase.DURING,k),(c.SourcePhase.AFTER,Keeper())):
            sample=p._read(phase,observer);graph=p._grant_samples[phase]
            self.assertIs(graph.source_sample,sample);self.assertIs(graph.scope,p._grant_scope)
            self.assertEqual(graph.source_sample_pin,sample.safe_summary()['full_sha256'])
            self.assertEqual(n[-1].graph_params,(list(p._grant_scope.role_names),list(p._grant_scope.role_names)))
            selected=[q for q in n[-1].events if q.startswith('SELECT')]
            self.assertEqual(selected,[c._IDENTITY,c._ROLES,c._DATABASE,c._SETTINGS,c._GRANTS])
            if phase is c.SourcePhase.AFTER:
                self.assertEqual(w.active_refs,1);p._observe_after_close(observer,None)
        self.assertEqual(w.active_refs,0)
        self.assertTrue(p._terminal())
        self.assertEqual(len({str(g.stable_private_value()) for g in p._grant_samples.values()}),1)
        p._authorization.grant_graph_scope=object()
        with self.assertRaises(B2Error):p._verify()

    def test_optional_graph_query_uncertainty_keeps_original_started_ref(self):
        p,k,w,n=self.graph_read()
        with patch.object(c,'_connect_native',return_value=Native(fixtures(),fail=c._GRANTS)):
            with self.assertRaises(B2Error):p._read(c.SourcePhase.BEFORE,k)
        self.assertTrue(p._unknown);self.assertFalse(p._grant_samples)
        self.assertEqual(w.active_refs,1)

    def test_non10_admin_bootstrap_and_complete_data_are_preserved(self):
        p,k,w,native=self.setup_read();sample=p._read(c.SourcePhase.BEFORE,k)
        self.assertEqual(sample.identity.current_user_oid,42)
        self.assertEqual(sample.roles[0].oid,10)
        self.assertEqual(sample.database.tablespace_name,'custom_space')
        self.assertEqual(sample.settings[1].role_name,'external_role')
        self.assertEqual(sample.roles[0].validuntil,'infinity')
        self.assertEqual(native[0].events[-2:],['ROLLBACK','CLOSE'])
        self.assertEqual(w.active_refs,0)
        self.assertTrue(p._terminal())

    def test_fixed_setters_then_snapshot_before_any_child_select(self):
        p,k,w,n=self.setup_read();p._read(c.SourcePhase.BEFORE,k);sample=p._read(c.SourcePhase.DURING,k)
        primary=[q for q in n[1].events if not q.startswith(("SET LOCAL statement_timeout", "SET LOCAL lock_timeout"))]
        self.assertEqual(primary[:4],[
            'BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY',
            "SET LOCAL TimeZone TO 'Asia/Shanghai'", "SET LOCAL DateStyle TO 'ISO, MDY'",
            "SET TRANSACTION SNAPSHOT '00000003-0000001B-1'"])
        self.assertEqual(primary[4],c._IDENTITY)
        self.assertEqual([x for x in p._attempts[1]['acks'] if x!='SET'],['BEGIN','ROLLBACK'])
        self.assertGreater(len(p._attempts[1]['acks']),5)
        self.assertEqual(sample.keeper_snapshot_text,'10:20:11,15')
        observer=Keeper();p._read(c.SourcePhase.AFTER,observer)
        p._observe_after_close(observer,None)
        self.assertEqual(len(n),3);self.assertEqual(w.active_refs,0)
        with self.assertRaises((B2Error,IndexError)):p._read(c.SourcePhase.AFTER,Keeper())
        self.assertEqual(len(n),3)

    def test_identity_rejection_is_after_known_native_end(self):
        parts=fixtures();parts[1][0]='wrong_database'
        p,k,w,n=self.setup_read(parts)
        with self.assertRaises(B2Error):p._read(c.SourcePhase.BEFORE,k)
        self.assertTrue(p._rejected);self.assertFalse(p._unknown)
        self.assertTrue(n[0].closed);self.assertEqual(w.active_refs,0)
        self.assertEqual(p._attempts[0]['state'],'TERMINAL')

    def test_native_set_import_query_rollback_close_ambiguity_retains_started_ref(self):
        for fail in ('TimeZone','DateStyle','SET TRANSACTION SNAPSHOT',c._IDENTITY,'ROLLBACK','CLOSE'):
            with self.subTest(fail=fail):
                p,k,w,n=self.setup_read()
                if fail=='SET TRANSACTION SNAPSHOT':p._read(c.SourcePhase.BEFORE,k);phase=c.SourcePhase.DURING
                else:phase=c.SourcePhase.BEFORE
                with patch.object(c,'_connect_native',return_value=Native(fixtures(),fail=fail)):
                    with self.assertRaises(B2Error):p._read(phase,k)
                self.assertTrue(p._unknown);self.assertEqual(w.active_refs,1)
                ref=p._attempts[-1]['ref'];self.assertTrue(ref._started)
                w.close();self.assertEqual(w.active_refs,1);self.assertFalse(ref._closed)
                ref.close()  # synthetic teardown only, never production recovery
                patch.stopall()

    def test_connect_uncertainty_retains_intent_without_handle(self):
        p,k,w,n=self.setup_read()
        with patch.object(c,'_connect_native',side_effect=OSError('private')):
            with self.assertRaises(B2Error):p._read(c.SourcePhase.BEFORE,k)
        self.assertIsNone(p._attempts[0]['connection']);self.assertEqual(w.active_refs,1)
        self.assertTrue(p._unknown)

    def test_rendering_changed_or_bad_bounds_refused_before_child_connect(self):
        for rows in ([('UTC','ISO, MDY')],[('Asia/Shanghai','SQL, DMY')],[('', 'ISO')],
                     [('x'*1025,'ISO')],[('Asia/Shanghai','x'*129)],[('Asia/Shanghai','ISO','extra')],[], [('Asia/Shanghai','ISO'),('UTC','ISO')]):
            p,k,w,n=self.setup_read();p._read(c.SourcePhase.BEFORE,k);k.rows=rows
            with self.assertRaises(B2Error):p._read(c.SourcePhase.DURING,k)
            self.assertEqual(len(n),1)
            patch.stopall()

    def test_stable_xmin_timezone_and_order_drift_refused(self):
        for mutate in (lambda parts:parts[2][0].__setitem__(13,'124'),
                       lambda parts:parts[1].__setitem__(7,'UTC'),
                       lambda parts:parts[4][0][3].reverse()):
            p,k,w,n=self.setup_read();p._read(c.SourcePhase.BEFORE,k)
            changed=fixtures();mutate(changed)
            with patch.object(c,'_connect_native',return_value=Native(changed)):
                with self.assertRaises(B2Error):p._read(c.SourcePhase.DURING,k)
            self.assertTrue(p._rejected);self.assertEqual(w.active_refs,0)
            patch.stopall()

    def test_oversize_fetch_and_field_budget_rejected_without_truncation(self):
        for rows,widths in (([(1,)]*3,(1024,)),([('x'*1025,)],(1024,)),([(['a=1']*257,)],(8192,))):
            with self.assertRaises(B2Error):c._bounded(Cursor(rows),2,widths,[0])
        with self.assertRaises(B2Error):c._bounded(Cursor([('x',)]),2,(1024,),[8*1024**2])

    def test_copies_wrong_keeper_repeat_and_mutated_context_denied(self):
        p,k,w,n=self.setup_read()
        with self.assertRaises(TypeError):copy.copy(p)
        p._read(c.SourcePhase.BEFORE,k)
        with self.assertRaises(B2Error):p._read(c.SourcePhase.BEFORE,k)
        p._owner._reconstruction_producer=object()
        with self.assertRaises(B2Error):p._read(c.SourcePhase.DURING,k)
        self.assertEqual(len(n),1)

    def test_timeout_refresh_is_fixed_bounded_and_precedes_each_primary_operation(self):
        p,k,w,n=self.setup_read();p._read(c.SourcePhase.BEFORE,k)
        events=n[0].events
        for i,q in enumerate(events):
            if q.startswith(('SELECT','SET LOCAL TimeZone','SET LOCAL DateStyle','ROLLBACK')):
                self.assertTrue(events[i-2].startswith('SET LOCAL statement_timeout'))
                self.assertTrue(events[i-1].startswith('SET LOCAL lock_timeout'))
        self.assertTrue(any(q.startswith('SET LOCAL statement_timeout') for q in events))
        self.assertTrue(all(0<r['milliseconds']<=5000 for r in p._attempts[0]['limits'] if r['kind']=='statement_timeout'))


    def test_child_started_ref_survives_real_owned_thread_finally_and_window_close(self):
        import asyncio
        from platform_service.media_backup_b2.capture import OwnedInputs,_owned_thread
        p,k,w,n=self.setup_read()
        o=OwnedInputs.__new__(OwnedInputs)
        o._exclusive=w;o._work_lock=threading.RLock();o._active_work=0;o._work_unknown=False
        async def exercise():
            with patch.object(c,'_connect_native',side_effect=OSError('synthetic connect lost')):
                with self.assertRaises(B2Error):await _owned_thread(o,lambda:p._read(c.SourcePhase.BEFORE,k))
        asyncio.run(exercise())
        self.assertEqual(o._active_work,0)
        self.assertEqual(w.active_refs,1)
        w.close();self.assertEqual(w.active_refs,1)
        self.assertTrue(p._attempts[0]['ref']._started)

    def test_limit_refresh_shrinks_against_same_original_deadline(self):
        p,k,w,n=self.setup_read();original_deadline=p._deadline
        current=time.monotonic_ns()
        p._deadline=current+900_000_000;p._pair._inputs._deadline=p._deadline
        ticks=iter(range(current,current+800_000_000,5_000_000))
        with patch.object(c.time,'monotonic_ns',side_effect=lambda:next(ticks)):
            p._read(c.SourcePhase.BEFORE,k)
        statement=[r['milliseconds'] for r in p._attempts[0]['limits'] if r['kind']=='statement_timeout']
        self.assertTrue(all(a>b for a,b in zip(statement,statement[1:])))
        self.assertTrue(all(0<x<900 for x in statement))
        self.assertLess(p._deadline,original_deadline)


class ConsumerTests(unittest.TestCase):
    def pair(self):
        pair=c.OwnedSourceCapture(c._SEAL)
        pair._lock=threading.RLock();pair._consumers=0
        pair._consumer=None;pair._consumer_ever=False;pair._publication_closed=False
        pair._inputs=NS(companion=pair)
        pair._verify_bindings=lambda:None
        pair._validate_terminal_success=lambda:None
        return pair

    def test_exact_consumer_reserved_once_and_publication_blocks_until_explicit_release(self):
        pair=self.pair();owner=object();slot=object()
        pair._claim_consumer(owner,slot)
        self.assertEqual(pair._consumers,1)
        with self.assertRaises(B2Error):pair.close_publication()
        with self.assertRaises(B2Error):pair._claim_consumer(owner,object())
        with self.assertRaises(B2Error):pair._release_consumer(object(),slot)
        pair._release_consumer(owner,slot)
        self.assertEqual(pair._consumers,0)
        with self.assertRaises(B2Error):pair._claim_consumer(owner,slot)
        with self.assertRaises(B2Error):pair._release_consumer(owner,slot)

class CaptureSites(unittest.TestCase):
    def test_actual_capture_sites_follow_original_per_database_lifetimes(self):
        self.exercise_capture_sites()

    def test_actual_after_observer_retained_on_rendering_ambiguity(self):
        self.exercise_capture_sites('render')

    def test_actual_after_observer_retained_on_close_ambiguity(self):
        self.exercise_capture_sites('close')

    def test_actual_after_observer_retained_on_business_read_ambiguity(self):
        self.exercise_capture_sites('roles')

    def test_actual_after_validation_rejection_is_known_after_observer_close(self):
        self.exercise_capture_sites('data')

    def exercise_capture_sites(self, fault=None):
        import tempfile,os
        from contextlib import ExitStack
        from platform_service.media_backup_b2 import capture as cap
        from platform_service.media_backup_b2.profiles import PUBLIC_016
        class Config:
            def verify(self):pass
            def items(self,kind):return [{'name':'maintenance'}]
        class Row:
            def __init__(self,value):self.value=value
            def fetchone(self):return (self.value,)
        class Con:
            def __init__(self,kind):self.kind=kind;self.closed=False
            def execute(self,q,*args):
                if q==c._RENDER:
                    if self is not producer._keeper:assert producer._attempts[-1]['observer'] is self
                    if fault=='render' and self is not producer._keeper:raise OSError('render lost')
                    return Cursor([('Asia/Shanghai','ISO, MDY')])
                if q=='SHOW server_version_num':return Row('160004')
                if 'pg_export_snapshot' in str(q):return Row('00000003-0000001B-1')
                return Row(True)
            def close(self):
                if fault=='close' and self is observed[0]:raise OSError('observer close lost')
                self.closed=True
            def __enter__(self):return self
            def __exit__(self,*args):self.close()
        class StopFlow(Exception):pass
        observed=[];producer,_,_,_=ProducerTests.setup_read(self)
        events=[];companion=NS(_cleanup_unknown=False,
            _bind_window=lambda ex:events.append('window'),
            _before=lambda kind,k:events.append(kind+':before'),
            _during=lambda kind,k:events.append(kind+':during'),
            _after=lambda kind,k:events.append(kind+':after'),
            _bind_captured=lambda actual:events.append('CAPTURED'))
        with tempfile.TemporaryDirectory() as tmp,ExitStack() as stack:
            media=os.open(tmp,os.O_RDONLY|os.O_DIRECTORY);stack.callback(os.close,media)
            os.mkdir(tmp+'/job',0o700);job=os.open(tmp+'/job',os.O_RDONLY|os.O_DIRECTORY);stack.callback(os.close,job)
            st=os.fstat(media);binding=NS(source_dev=st.st_dev,source_ino=st.st_ino)
            stack.enter_context(patch.object(fs,'_duplicate_exclusive',side_effect=lambda fd:fd+1))
            stack.enter_context(patch.object(fs,'_close_exclusive',return_value=None))
            window=fs.ExclusiveWindow(NS(verify=lambda:None),-10,binding,_mint=fs._MINT)
            o=cap.OwnedInputs('central','identity',PUBLIC_016,(),(),media,job,Config(),
                (os.getuid(),os.getgid()),binding,{'central':{},'identity':{}},1)
            o._exclusive=window;o._deadline=time.monotonic_ns()+60_000_000_000
            o._preflight={'schemas':{'central':{},'identity':{}}};o.companion=companion
            producer._pair._exclusive=window;producer._pair._inputs=o
            def before(kind,con):
                events.append(kind+':before')
                if kind=='central':producer._read(c.SourcePhase.BEFORE,con)
            def during(kind,con):
                events.append(kind+':during')
                if kind=='central':producer._read(c.SourcePhase.DURING,con)
            def after(kind,con):
                events.append(kind+':after')
                if kind=='central':
                    observed.append(con)
                    if fault=='data':
                        parts=fixtures();parts[1][0]='wrong_database'
                        stack.enter_context(patch.object(c,'_connect_native',return_value=Native(parts)))
                    producer._read(c.SourcePhase.AFTER,con)
            def closed(kind,con,error=None,read_error=None):
                if kind=='central':
                    if read_error is None:producer._observe_after_close(con,error)
                    else:producer._observe_after_close(con,error,read_error)
            companion._before=before;companion._during=during;companion._after=after
            companion._after_observer_closed=closed
            def roles(self,kind,con):
                events.append(kind+':roles')
                if fault=='roles' and observed and con is observed[0]:raise OSError('business read lost')
                return {}
            def runner(self,tool,args,**kw):
                if args==['--version']:return (tool+' (PostgreSQL) 16.4\n').encode()
                if kw.get('output_fd') is not None:os.write(kw['output_fd'],b'PGDMP');return None
                return b'synthetic'
            compared=[]
            def compare(*args):
                compared.append(True)
                if len(compared)==2:raise StopFlow()
            patches=[(cap,'ConfigInputs',Config),(cap.OwnedInputs,'_connect',lambda self,kind:Con(kind)),
              (cap.OwnedInputs,'_control',lambda *a:None),(cap.OwnedInputs,'_roles',roles),
              (cap.ChildRunner,'run',runner),(cap,'_schema',lambda *a:{}),
              (cap,'validate_profile_catalog',lambda *a:None),(cap,'sequence_inventory',lambda *a,**kw:[]),
              (cap,'snapshot_copy_inventory',lambda *a:{}),(cap,'_raw_rows',lambda *a:{}),
              (cap,'read_control_rows',lambda *a:{}),(cap,'keeper_identity',lambda *a:(123,'10:20:11,15','on','repeatable read')),
              (cap,'_physical',lambda *a:[]),(cap,'_physical_stamps',lambda *a:{}),
              (cap,'derive_registry',lambda *a,**kw:([],[])),
              (cap,'make_snapshot',lambda *a,**kw:{'tables':{'control':[{'state':'CAPTURING'}]}}),
              (cap,'verify_dump_schema',lambda *a:{}),(cap,'parse_dump_copy_inventory',lambda *a:{}),
              (cap,'_dump_sequences',lambda *a:[]),(cap,'roles_export',lambda *a:b'{}'),(cap,'compare_sequences',compare)]
            for owner,name,value in patches:stack.enter_context(patch.object(owner,name,value))
            fake_conninfo=NS(conninfo_to_dict=lambda dsn:{'host':'127.0.0.1','port':'1','dbname':dsn,'user':'owner','password':'synthetic'})
            stack.enter_context(patch.dict(sys.modules,{'psycopg.conninfo':fake_conninfo}))
            with self.assertRaises(B2Error):cap._capture_source_sync(PUBLIC_016,window,o,cap.ChildRunner(('fake',),('fake',)))
            attempt=producer._attempts[-1]
            self.assertIs(attempt['observer'],observed[0])
            if fault=='data':
                self.assertTrue(producer._rejected)
                self.assertFalse(producer._unknown)
                self.assertTrue(producer._terminal())
                self.assertTrue(attempt['ref']._closed)
                self.assertEqual(attempt['observer_close'],'CLOSED')
                return
            if fault:
                self.assertTrue(producer._unknown)
                self.assertEqual(attempt['state'],'UNKNOWN')
                self.assertFalse(attempt['ref']._closed)
                self.assertEqual(attempt['observer_close'],'UNKNOWN' if fault=='close' else 'CLOSED')
                return
            self.assertTrue(observed[0].closed)
            self.assertEqual(attempt['observer_close'],'CLOSED')
            self.assertIsNot(attempt['connection'],observed[0])
            self.assertTrue(producer._terminal())
            self.assertEqual(events,['window','central:before','central:roles','identity:before','identity:roles',
                'central:during','central:roles','central:after','central:roles',
                'identity:during','identity:roles','identity:after','identity:roles'])
            self.assertFalse(o._keepers)

    def test_preflight_and_repeated_postcheck_cannot_fill_sample_slots(self):
        import ast
        from platform_service.media_backup_b2 import capture as cap
        tree=ast.parse(Path(cap.__file__).read_text())
        owned=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='OwnedInputs')
        for name in ('preflight','verify_after','_roles'):
            method=next(n for n in owned.body if isinstance(n,ast.FunctionDef) and n.name==name)
            calls=[n.func.attr for n in ast.walk(method) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)]
            self.assertFalse({'_before','_during','_after','_bind_captured'}&set(calls))
