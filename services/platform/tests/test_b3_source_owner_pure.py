"""Private fake source lifecycle regressions; no runtime or native imports."""
import ast
import asyncio
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'scripts'))
import b3_target_once as caller

class SQL(str):
    def format(self,*args):return SQL(str(self).format(*args))
class Connection:
    def __init__(self,driver):self.driver=driver;self.info=NS(backend_pid=42)
    def execute(self,query,*args,**kwargs):
        self.driver.events.append(str(query))
        if self.driver.fail and self.driver.fail in str(query):raise self.driver.error
        return self
    def fetchall(self):return []
    def commit(self):self.execute('COMMIT')
    def close(self):self.execute('CLOSE')
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
class Driver:
    def __init__(self,fail=None):self.fail=fail;self.error=KeyboardInterrupt('private interruption');self.events=[];self.connections=[]
    def connect(self,*args,**kwargs):
        self.events.append('CONNECT')
        if self.fail=='CONNECT':raise self.error
        c=Connection(self);self.connections.append(c);return c

def authority():return NS(host='127.0.0.1',port=51430,database='synthetic',user='admin',password='private',container_id='a'*64)
def load_owner(driver,path):
    fake=NS(connect=driver.connect,sql=NS(SQL=SQL,Identifier=SQL,Literal=SQL),AsyncConnection=NS(connect=driver.aconnect))
    conninfo=NS(make_conninfo=lambda *args,**kw:'private-dsn')
    with patch.dict(sys.modules,{'psycopg':fake,'psycopg.conninfo':conninfo}):
        import b3_source_owner
        return b3_source_owner.SourceOwner(authority(),path,lambda:None,NS(is_set=lambda:False))
async def aconnect(self,*args,**kw):
    sync=self.connect(*args,**kw)
    class Async:
        info=sync.info
        def transaction(self):
            class Tx:
                async def __aenter__(self):sync.execute('TX ENTER');return self
                async def __aexit__(self,*args):sync.execute('TX EXIT')
            return Tx()
        async def execute(self,*a,**k):return sync.execute(*a,**k)
        async def commit(self):return sync.commit()
        async def close(self):return sync.close()
    return Async()
Driver.aconnect=aconnect

class Lifecycle(unittest.TestCase):
    def test_interrupted_setup_never_finalizes(self):
        driver=Driver('CREATE DATABASE')
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)
            owner=load_owner(driver,path)
            owner.bind_journal(path/'journal',dict(operation_nonce='a'*64))
            with self.assertRaises(KeyboardInterrupt):owner.prepare()
            self.assertTrue(owner.unknown)
            self.assertIs(owner.attempts[-1]['error'],driver.error)
            self.assertFalse(any('DROP' in x or 'terminate' in x or x=='CLOSE' for x in driver.events),driver.events)

    def test_cleanup_stops_retains_original_and_never_retries(self):
        events=[];error=KeyboardInterrupt('original')
        def fail():events.append('first');raise error
        graph=dict(generators=[NS(close=lambda:events.append('second')),NS(close=fail)],fds=[123])
        out={}
        with patch.object(caller.os,'close',lambda fd:events.append('fd')):
            caller.cleanup_known(graph,out)
            caller.cleanup_known(graph,out)
        self.assertEqual(events,['first'])
        self.assertIs(graph['cleanup_attempts'][-1]['error'],error)
        self.assertTrue(out['cleanup_refused_live_work'])
        self.assertEqual(out['status'],'FAILED')

class ExplicitOwnerControls(unittest.TestCase):
    def fixture(self,fail=None):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        path=Path(tmp.name)
        for i in range(16):(path/('%03d.sql'%i)).write_text('MIGRATION %d'%i)
        driver=Driver(fail);owner=load_owner(driver,path)
        owner.bind_journal(path/'journal',dict(operation_nonce='a'*64,manifest_sha256='b'*64,
            source_container_id='c'*64,started=1,deadline=901))
        return owner,driver,path

    def test_original_source_authority_retained_for_independent_replay_binding(self):
        owner,driver,path=self.fixture()
        self.assertIsNotNone(owner._source_authority)
        self.assertTrue(callable(getattr(owner,'bind_replay_authorization',None)))
        self.assertTrue(callable(getattr(owner,'bind_replay_selection',None)))
        self.assertFalse(hasattr(owner,'_replay_authorization'))

    def test_precapture_authorization_exact_independent_selection_and_private_material(self):
        import copy,dataclasses,hashlib,pickle
        from test_media_backup_b3_roles import fixture
        from platform_service.media_backup_b3 import roles
        from b3_source_owner import SourceOwner
        kw=fixture();data=kw['authorization'];context=kw['context']
        material={a.credential_id:(a.role_name+a.mode.value).encode() for a in data.authentication}
        data=dataclasses.replace(data,authentication=tuple(dataclasses.replace(a,
            credential_sha256=hashlib.sha256(material[a.credential_id]).hexdigest()) for a in data.authentication))
        owner=SourceOwner.__new__(SourceOwner);authority=object()
        owner._source_authority=authority;owner.cancellation=NS(is_set=lambda:False);owner.check_budget=lambda:None
        owner.phase='READY';owner.active=0;owner.unknown=False;owner.cleanup_attempted=False
        owner.name=data.database_name;owner.container_id=data.source_container_id
        owner.binding={'started':data.started_at,'deadline':data.deadline}
        owner.roles={n:n for n in context.role_names}
        selection=roles.ReplaySelection('b3-replay-selection-v1',data.kind,context.role_names,context.role_names,
            context.allowed_grantors,data.policy_record_sha256,data.source_container_id,data.system_identifier_sha256,data.database_name)
        with self.assertRaises(RuntimeError):owner.bind_replay_selection(selection,original_root_authorization=object())
        owner.bind_replay_selection(selection,original_root_authorization=authority)
        with self.assertRaises(RuntimeError):owner.bind_replay_authorization(data,original_comparison=context,
            original_root_authorization=authority,authentication_material={})
        auth=owner.bind_replay_authorization(data,original_comparison=context,
            original_root_authorization=authority,authentication_material=material)
        auth._verify();self.assertIs(auth.data,data);self.assertIs(auth.original_comparison,context)
        for action in (lambda:copy.copy(auth),lambda:pickle.dumps(auth)):
            with self.assertRaises(TypeError):action()
        material.clear();auth._verify()  # caller cannot mutate retained material
        self.assertNotIn('source_db',repr(auth))
        owner._replay_selection=dataclasses.replace(selection)
        with self.assertRaises(RuntimeError):auth._verify()

    def test_intents_durable_before_first_native_entry(self):
        owner,driver,path=self.fixture()
        self.assertEqual(driver.events,[])
        records=list((path/'journal').glob('*'))
        self.assertEqual(len(records),1)
        raw=records[0].read_text()
        self.assertIn(owner.name,raw)
        self.assertTrue(all(role in raw for role in owner.roles.values()))
        self.assertNotIn('private-dsn',raw)
        self.assertEqual(records[0].stat().st_mode & 0o777,0o600)

    def test_all_uncertain_setup_operations_retain_no_finalizer(self):
        for fail in ('CONNECT','CREATE ROLE','CREATE DATABASE','MIGRATION 0','COMMIT','CLOSE'):
            with self.subTest(fail=fail):
                owner,driver,_=self.fixture(fail)
                with self.assertRaises(KeyboardInterrupt) as caught:owner.prepare()
                self.assertIs(caught.exception,driver.error)
                self.assertTrue(owner.unknown)
                self.assertTrue(owner.active)
                self.assertFalse(any('DROP' in x or 'terminate' in x for x in driver.events))
                self.assertEqual(driver.events.count('CLOSE'),int(fail=='CLOSE'))
                self.assertIs(owner.attempts[-1]['error'],driver.error)
                state=caller.graph_state(dict(source_owners=[owner]))
                self.assertTrue(state['unknown'])
                self.assertFalse(caller.disposition_work_terminal(state))

    def test_normal_recipe_and_original_cleanup(self):
        owner,driver,_=self.fixture();owner.prepare()
        self.assertEqual(len(owner.migrations),16)
        self.assertEqual(sum(x.startswith('GRANT') for x in driver.events),3)
        self.assertEqual(owner.phase,'READY')
        graph=dict(source_owners=[owner]);out={}
        caller.cleanup_known(graph,out)
        self.assertTrue(out['synthetic_cleanup_complete'])
        self.assertEqual(owner.phase,'CLOSED')
        self.assertEqual(sum(x.startswith('DROP') for x in driver.events),4)
        with self.assertRaises(RuntimeError):owner.cleanup(graph)

    def test_foreign_and_partial_cleanup_refused(self):
        owner,driver,_=self.fixture()
        with self.assertRaises(RuntimeError):owner.cleanup({})
        owner.prepare()
        with self.assertRaises(RuntimeError):owner.cleanup(dict(cleanup_phase='SOURCE',cleanup_current=owner,source_owners=[]))
        self.assertFalse(any(x.startswith('DROP') for x in driver.events))

    def test_async_execute_commit_close_and_body_interrupt_retained(self):
        for fail in ('CONNECT','ASYNC WORK','COMMIT','CLOSE','BODY'):
            with self.subTest(fail=fail):
                owner,driver,_=self.fixture();owner.prepare();driver.events.clear();driver.fail=fail
                async def run():
                    async with owner.connection('owner') as c:
                        if fail=='BODY':raise driver.error
                        await c.execute('ASYNC WORK')
                        await c.commit()
                with self.assertRaises(KeyboardInterrupt) as caught:asyncio.run(run())
                self.assertIs(caught.exception,driver.error)
                self.assertTrue(owner.unknown)
                self.assertGreater(owner.active,0)
                self.assertEqual(driver.events.count('CLOSE'),int(fail=='CLOSE'))
                self.assertTrue(any(a.get('error') is driver.error for a in owner.attempts))

    def test_async_transaction_failure_does_not_unwind_native_context(self):
        owner,driver,_=self.fixture();owner.prepare();driver.events.clear();driver.fail='ASYNC WORK'
        async def run():
            async with owner.connection('owner') as c:
                async with c.transaction():await c.execute('ASYNC WORK')
        with self.assertRaises(KeyboardInterrupt):asyncio.run(run())
        self.assertNotIn('TX EXIT',driver.events)
        self.assertNotIn('CLOSE',driver.events)
        self.assertTrue(owner.unknown)

    def test_async_transaction_normal_exit_commits_without_outer_implicit_transaction(self):
        owner,driver,_=self.fixture();owner.prepare();driver.events.clear()
        async def run():
            async with owner.connection('owner') as c:
                async with c.transaction():await c.execute('ASYNC WORK')
        asyncio.run(run())
        self.assertEqual(driver.events,['CONNECT','TX ENTER','ASYNC WORK','TX EXIT','CLOSE'])

    def test_ownership_fsync_uncertainty_prevents_sql(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);path=Path(tmp.name)
        driver=Driver();owner=load_owner(driver,path);error=KeyboardInterrupt('fsync')
        with patch('os.fsync',side_effect=error):
            with self.assertRaises(KeyboardInterrupt):owner.bind_journal(path/'journal',{})
        self.assertTrue(owner.unknown);self.assertEqual(driver.events,[])
        self.assertIs(owner.journal_attempts[-1]['error'],error)
        with self.assertRaises(RuntimeError):owner.prepare()
        self.assertEqual(driver.events,[])

    def test_live_source_backend_refuses_all_drops(self):
        owner,driver,_=self.fixture();owner.prepare()
        with patch.object(Connection,'fetchall',lambda _: [(1234,)]):
            out={};caller.cleanup_known(dict(source_owners=[owner]),out)
        self.assertTrue(out['cleanup_refused_live_work'])
        self.assertFalse(any(x.startswith('DROP') or 'terminate' in x for x in driver.events))

    def test_fd_close_uncertainty_stops_remaining_fds(self):
        out={};graph=dict(fds=[101,102]);calls=[];error=KeyboardInterrupt('fd')
        def close(fd):calls.append(fd);raise error
        with patch.object(caller.os,'close',close):
            caller.cleanup_known(graph,out);caller.cleanup_known(graph,out)
        self.assertEqual(calls,[102]);self.assertIs(graph['cleanup_attempts'][0]['error'],error)
        self.assertEqual(graph['fds'],[101,102])

    def test_actual_attaches_owner_before_setup_and_avoids_generator(self):
        source=(HERE.parent/'scripts/b3_target_once.py').read_text()
        tree=ast.parse(source);node=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='actual')
        body=ast.get_source_segment(source,node)
        self.assertNotIn('_owned_pg',body)
        self.assertLess(body.index('source_owners.append(env)'),body.index('env.bind_journal('))
        self.assertLess(body.index('env.bind_journal('),body.index('env.prepare()'))
        self.assertIn("with env.sync_connection('owner')",body)

    def test_cleanup_first_source_interrupt_keeps_second_and_fd(self):
        one,driver,_=self.fixture();two,other,_=self.fixture();one.prepare();two.prepare()
        driver.fail='DROP DATABASE';other.events.clear()
        graph=dict(source_owners=[two,one],fds=[234]);out={};closed=[]
        with patch.object(caller.os,'close',lambda fd:closed.append(fd)):
            caller.cleanup_known(graph,out);graph['release_unknown_refs']=True;caller.cleanup_known(graph,out)
        self.assertNotIn(234,closed);self.assertEqual(other.events,[])
        self.assertIs(graph['cleanup_attempts'][-1]['error'],driver.error)
        self.assertEqual(out['status'],'FAILED')

if __name__=='__main__':unittest.main()


class ReconstructionOwnerGuards(unittest.TestCase):
    def test_wrong_producer_cannot_finish_or_decrement_original_owner(self):
        import b3_source_owner
        owner=b3_source_owner.SourceOwner.__new__(b3_source_owner.SourceOwner)
        owner.active=1;owner.unknown=False
        for name in ('finish_reconstruction','finish_known_failed_reconstruction'):
            with self.assertRaises(RuntimeError):getattr(owner,name)(object())
            self.assertEqual(owner.active,1)
            self.assertFalse(owner.unknown)

    def test_legacy_failed_is_still_sticky_and_never_decrements(self):
        import b3_source_owner
        owner=b3_source_owner.SourceOwner.__new__(b3_source_owner.SourceOwner)
        owner.active=1;owner.unknown=False;attempt={'state':'ACTIVE'};error=OSError('synthetic')
        owner.failed(attempt,error)
        self.assertEqual(owner.active,1);self.assertTrue(owner.unknown)
        self.assertEqual(attempt['state'],'UNKNOWN');self.assertIs(attempt['error'],error)
