import copy
"""Private fake/tempFS supervisor controls; no native launches or real sources."""
import importlib.util
import os
import fcntl
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))


class ModuleLoader(unittest.TestCase):
    def module(self):
        path = SCRIPTS / 'b3_target_supervisor.py'
        self.assertTrue(path.exists(), 'external supervisor source does not exist')
        spec = importlib.util.spec_from_file_location('supervisor_under_test', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

class BootstrapControls(ModuleLoader):
    def test_binding_requires_actual_session_exact_nonce_pid_and_closed_shape(self):
        m = self.module()
        ready = dict(format='b3-supervisor-bootstrap-v1', supervisor_pid=731,
                     supervisor_nonce='a'*64, code_sha256='b'*64,
                     interpreter_sha256='c'*64)
        good = dict(format='b3-supervisor-session-binding-v1',
                    supervisor_pid=731, supervisor_nonce='a'*64,
                    bootstrap_sha256=m.sha(m.canonical(ready)), tool_session='actual-tool-317')
        self.assertEqual(m.validate_session_binding(good, ready), 'actual-tool-317')
        for changes in ({'supervisor_pid':732}, {'supervisor_nonce':'d'*64},
                        {'tool_session':''}, {'future_session':True},
                        {'bootstrap_sha256':'e'*64}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                m.validate_session_binding(dict(good, **changes), ready)

    def test_original_clock_starts_after_binding_and_record_precedes_collector(self):
        m = self.module()
        with tempfile.TemporaryDirectory(dir='/private/tmp') as temp:
            os.chown(temp,os.getuid(),os.getgid())
            root = Path(temp)
            os.chmod(root, 0o700)
            os.chown(root, os.getuid(), os.getgid())
            ready = dict(format='b3-supervisor-bootstrap-v1', supervisor_pid=731,
                         supervisor_nonce='a'*64, code_sha256='b'*64,
                         interpreter_sha256='c'*64)
            binding = dict(format='b3-supervisor-session-binding-v1', supervisor_pid=731,
                           supervisor_nonce='a'*64, bootstrap_sha256=m.sha(m.canonical(ready)),
                           tool_session='actual-tool-317')
            with patch.object(m.time, 'monotonic', return_value=102.5):
                job = m.bind_original_job(root, ready, binding, 'f'*64)
            self.assertEqual(job['started'], 102.5)
            self.assertEqual(job['deadline'], 1002.5)
            self.assertEqual(m.closed_json(m.private_read(root/'original_job.private.json')[0]),job)
            with self.assertRaises(FileExistsError):
                m.bind_original_job(root, ready, binding, 'f'*64)

    def test_invalid_binding_does_not_burn_original_clock(self):
        m = self.module()
        with tempfile.TemporaryDirectory(dir='/private/tmp') as temp:
            os.chown(temp,os.getuid(),os.getgid())
            with patch.object(m.time, 'monotonic', side_effect=AssertionError('clock touched')):
                with self.assertRaises(ValueError):
                    m.bind_original_job(Path(temp), {}, {}, 'f'*64)
            self.assertFalse((Path(temp)/'original_job.private.json').exists())



class OwnershipControls(ModuleLoader):
    def test_constructor_interruption_keeps_original_object_and_never_replays(self):
        m=self.module()
        class Fake:
            def __init__(self,*a,**kw): raise KeyboardInterrupt()
        with tempfile.TemporaryDirectory(dir='/private/tmp') as temp:
            os.chown(temp,os.getuid(),os.getgid())
            actor=m.ProcessGraph(Path(temp),dict(started=1.,deadline=901.))
            with patch.object(m.subprocess,'Popen',Fake), patch.object(m.time,'monotonic',return_value=2.):
                with self.assertRaises(KeyboardInterrupt): actor._spawn(['fixed'],())
                self.assertEqual(len(actor.attempts),1)
                self.assertIsInstance(actor.attempts[0]['process'],Fake)
                self.assertTrue(actor.unknown)
                with self.assertRaises(ValueError): actor._spawn(['fixed'],())
                self.assertEqual(len(actor.attempts),1)

    def test_capacity_never_fabricates_future_measurement_and_keeps_reservation(self):
        m=self.module()
        result=m.capacity_measurement(dict(operation_nonce='a'*64,started=1.,deadline=901.),7,2.)
        self.assertEqual(len(result['components']),6)
        self.assertTrue(all(x['observation']=='not-created' and x['measured_bytes']==0 for x in result['components']))
        self.assertEqual(result['peak_bytes'],sum(x['peak_allowance_bytes'] for x in result['components']))
        self.assertEqual(result['reserved_bytes'],10*1024**3+result['peak_bytes'])

    def test_closed_terminal_exit_zero_alone_rejected(self):
        m=self.module()
        for outcome in ({},dict(success=True,status='target_lifecycle_verified'),dict(prepared=True)):
            with self.assertRaises(ValueError):m.validate_terminal(outcome,'a'*64)

    def test_source_inspect_rejects_wrong_source_port_running_and_execs(self):
        m=self.module()
        import copy
        d=dict(Id=m.SOURCE_CID,Image=m.PARSER_IMAGE,State=dict(Running=True,Pid=62835,StartedAt='2026-11-03T09:24:06.729872208Z',Restarting=False,Paused=False,Dead=False),
               RestartCount=0,Config=dict(User='',Image=m.PARSER_IMAGE),ExecIDs=None,Mounts=[dict(Type='volume',Name='easel-b2-disk-e419567e242e68b72e0a7b3a26d4385f-data',Destination='/var/lib/postgresql/data',RW=True)],
               NetworkSettings=dict(Ports={'5432/tcp':[dict(HostIp='127.0.0.1',HostPort='51430')]}))
        self.assertEqual(m.validate_source_inspect(d),['easel-b2-disk-e419567e242e68b72e0a7b3a26d4385f-data'])
        for alter in (lambda v:v.update(Id='a'*64),lambda v:v['State'].update(Running=False),
                      lambda v:v.update(ExecIDs=['x']),lambda v:v['NetworkSettings']['Ports']['5432/tcp'][0].update(HostPort='9999')):
            v=copy.deepcopy(d);alter(v)
            with self.assertRaises(ValueError):m.validate_source_inspect(v)

class AdditionalControls(ModuleLoader):
    def test_pth_hook_is_exact_reviewed_bytes_never_generic_import(self):
        m=self.module()
        with tempfile.TemporaryDirectory(dir='/private/tmp') as temp:
            os.chown(temp,os.getuid(),os.getgid())
            root=Path(temp);pth=root/'_virtualenv.pth'
            pth.write_bytes(b'import _virtualenv')
            hook=root/'_virtualenv.py';hook.write_bytes(b'# fixed fake hook')
            spec=dict(runtime_roots=[str(root)],pth_files=[str(pth)],
                runtime=dict(dependencies={str(pth):m.sha(pth.read_bytes()),str(hook):m.sha(hook.read_bytes())}),repository_dependencies={})
            with patch.object(m,'STARTUP_ROOTS',(str(root),)),patch.object(m,'dependency_hashes',return_value=spec['runtime']['dependencies']),patch.object(m,'VIRTUALENV_HOOK_SHA',m.sha(hook.read_bytes())):
                m.verify_inventory(spec)
                for raw in (b'import os\n',b'import _virtualenv; evil()\n',b'import _virtualenv\n# extra\n'):
                    pth.write_bytes(raw);spec['runtime']['dependencies'][str(pth)]=m.sha(raw)
                    with self.assertRaises(ValueError):m.verify_inventory(spec)

    def test_bootstrap_validates_alias_hash_without_venv_sys_prefix(self):
        m=self.module()
        with tempfile.TemporaryDirectory(dir='/private/tmp') as temp:
            os.chown(temp,os.getuid(),os.getgid())
            exe=Path(temp)/'python';exe.write_bytes(b'fake-executable')
            spec=dict(interpreter=dict(path=str(exe),sha256=m.sha(exe.read_bytes())),runtime=dict(prefix='/approved/venv'))
            with patch.object(m.sys,'executable',str(exe)),patch.object(m.sys,'prefix','/opt/anaconda3'):
                m.validate_bootstrap_interpreter(spec)
                with self.assertRaises(ValueError):m.validate_bootstrap_interpreter(dict(spec,interpreter=dict(path='/other/python',sha256=spec['interpreter']['sha256'])))
                exe.write_bytes(b'drift')
                with self.assertRaises(ValueError):m.validate_bootstrap_interpreter(spec)

    def test_unknown_hold_keeps_original_graph_and_writes_safe_status(self):
        m=self.module()
        class Stop(BaseException):pass
        with tempfile.TemporaryDirectory(dir='/private/tmp') as temp:
            os.chown(temp,os.getuid(),os.getgid())
            graph=m.ProcessGraph(Path(temp),dict(started=1.,deadline=901.))
            graph.error=(RuntimeError,RuntimeError('secret'),None)
            # Test only the durable transition: passive loop remains production-only.
            graph.mark_unknown()
            self.assertIn(graph,m.RETAINED)
            raw=(Path(temp)/'unknown.safe.json').read_bytes()
            self.assertNotIn(b'secret',raw)
            self.assertEqual(m.closed_json(raw)['status'],'UNKNOWN')
            self.assertFalse(m.closed_json(raw)['cleanup_performed'])

    def test_new_file_rejects_nonprivate_parent_before_creation(self):
        m=self.module()
        with tempfile.TemporaryDirectory(dir='/private/tmp') as temp:
            os.chown(temp,os.getuid(),os.getgid())
            os.chmod(temp,0o755)
            graph=m.ProcessGraph(temp,dict(started=1.,deadline=901.))
            with self.assertRaises(ValueError):graph.new('must-not-exist')
            self.assertFalse((Path(temp)/'must-not-exist').exists())

    def test_source_inspect_requires_stable_started_restart_and_root_auth(self):
        m=self.module();import copy
        d=dict(Id=m.SOURCE_CID,Image=m.PARSER_IMAGE,State=dict(Running=True,Pid=62835,StartedAt='2026-11-03T09:24:06.729872208Z',Restarting=False,Paused=False,Dead=False),
            RestartCount=0,ExecIDs=None,Mounts=[dict(Type='volume',Name='easel-b2-disk-e419567e242e68b72e0a7b3a26d4385f-data',Destination='/var/lib/postgresql/data',RW=True)],
            Config=dict(User='',Image=m.PARSER_IMAGE,Env=['POSTGRES_USER=admin','POSTGRES_DB=postgres','POSTGRES_PASSWORD=secret']),
            NetworkSettings=dict(Ports={'5432/tcp':[dict(HostIp='127.0.0.1',HostPort='51430')]}))
        self.assertEqual(m.validate_source_inspect(d),['easel-b2-disk-e419567e242e68b72e0a7b3a26d4385f-data'])
        for fn in (lambda v:v.update(RestartCount=1),lambda v:v['State'].update(Paused=True),lambda v:v['Config'].update(User='999')):
            value=copy.deepcopy(d);fn(value)
            with self.assertRaises(ValueError):m.validate_source_inspect(value)

class WorkflowControls(ModuleLoader):
    def exercise(self, fail=None):
        import base64,types,contextlib
        m=self.module();import b3_target_once as c
        module=types.ModuleType('platform_service.media_backup_b3.target_contract')
        module.build_target_plan=lambda v:types.SimpleNamespace(volume_argv=('fixed-volume',v),container_argv=('fixed-container',v))
        with tempfile.TemporaryDirectory(dir='/private/tmp') as temp:
            os.chown(temp,os.getuid(),os.getgid());root=Path(temp);boot=root/'bootstrap';m.mkdir_private(boot)
            interpreter=root/'python';interpreter.write_bytes(b'fake-python')
            auth=root/'authority';m.write_new(auth,b'fake-source-no-credentials')
            job=dict(format='b3-root-original-job-v1',operation_nonce='a'*64,started=1.,deadline=901.,supervisor_pid=os.getpid(),supervisor_nonce='e'*64)
            m.write_new(boot/'original_job.private.json',m.canonical(job))
            spec=dict(bootstrap=str(boot),directory=str(root/'job'),source=dict(container_id=m.SOURCE_CID,image=m.SOURCE_IMAGE_REF,nonce='b'*32,sha256=m.sha(auth.read_bytes())),
                authority=dict(path=str(auth),sha256=m.sha(auth.read_bytes())),runtime=dict(prefix=sys.prefix,testtools=str(root),dependencies={str(interpreter):m.sha(interpreter.read_bytes())}),
                interpreter=dict(path=str(interpreter),sha256=m.sha(interpreter.read_bytes())))
            if fail=='attest-target-only':
                import dataclasses,json
                from test_media_backup_b3_execution import ExecutionTests
                fixture=ExecutionTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
                spec.update(format='b3-external-supervisor-spec-attest-v1',execution_policy=json.loads(json.dumps(dataclasses.asdict(fixture.policy))))
            inspect=dict(Id=m.SOURCE_CID,Image=m.PARSER_IMAGE,State=dict(Running=True,Pid=62835,StartedAt='2026-11-03T09:24:06.729872208Z',Restarting=False,Paused=False,Dead=False),
                RestartCount=0,Config=dict(User='',Image=m.PARSER_IMAGE),ExecIDs=None,Mounts=[dict(Type='volume',Name='easel-b2-disk-e419567e242e68b72e0a7b3a26d4385f-data',Destination='/var/lib/postgresql/data',RW=True)],
                NetworkSettings=dict(Ports={'5432/tcp':[dict(HostIp='127.0.0.1',HostPort='51430')]}))
            raw=m.canonical(inspect)
            proof=dict(format='b3-independent-source-proof-v1',**{k:job[k] for k in ('operation_nonce','started','deadline')},observed_at=2.,container_id=m.SOURCE_CID,image_id=m.PARSER_IMAGE,
                system_id_sha256='c'*64,volume_names=[inspect['Mounts'][0]['Name']],admin_name='admin',inspect_sha256=m.sha(raw),inspect_base64=base64.b64encode(raw).decode(),tools_sha256='d'*64)
            graph=m.ProcessGraph(boot,job);calls=[];actors=[];inventory_checks=[]
            def spawn(actor,argv,pass_fds):
                self.assertTrue((boot/'original_job.private.json').exists());calls.append(tuple(argv))
                if '--prepare' in argv:
                    self.assertEqual([fcntl.fcntl(fd,fcntl.F_GETFL)&os.O_ACCMODE for fd in pass_fds],[os.O_RDONLY,os.O_RDONLY,os.O_RDWR])
                    self.assertEqual(len({(os.fstat(fd).st_dev,os.fstat(fd).st_ino) for fd in pass_fds}),3)
                if str(m.LAUNCHER) in argv:
                    self.assertEqual([fcntl.fcntl(fd,fcntl.F_GETFL)&os.O_ACCMODE for fd in pass_fds],[os.O_RDONLY,os.O_RDONLY,os.O_RDWR,os.O_RDWR])
                    self.assertEqual(len({(os.fstat(fd).st_dev,os.fstat(fd).st_ino) for fd in pass_fds}),4)
                    launch_stamp=m.stamp(os.stat(root/'job/launch.once'))
                    self.assertEqual(m.stamp(os.fstat(pass_fds[0])),launch_stamp)
                if '--collect-source' in argv:
                    self.assertFalse((root/'job').exists())
                    m.report_write(pass_fds[2],proof)
                    kind='collector'
                elif '--prepare' in argv:
                    kind='prepare'
                    if fail=='prepare':raise KeyboardInterrupt()
                    c.prepare_fd(*pass_fds)
                elif str(m.LAUNCHER) in argv:
                    kind='caller';self.assertTrue((root/'job/launch.once').exists())
                    self.assertEqual(m.fd_read(pass_fds[2]),b'')
                    hint=dict(pid=987,ppid=os.getpid(),executable=str(interpreter),launch_sha256=m.sha(m.fd_read(pass_fds[0])),independent_observation=False)
                    if fail=='pid':hint['pid']=988
                    m.write_new(root/'job/parked.private.json',m.canonical(hint))
                else:
                    kind='parser';self.assertEqual(list(argv),m.parser_plan(m.closed_json((root/'job/manifest.private.json').read_bytes())))
                    self.assertTrue((root/'job/launch.once').exists());self.assertFalse((root/'job/caller.once').exists())
                attempt=dict(kind=kind,pid=987,process=types.SimpleNamespace(pid=987,poll=lambda:None),fds=pass_fds,streams=[],terminal=None)
                actor.attempts.append(attempt);actors.append(actor)
                return attempt
            def wait(actor,attempt):
                kind=attempt['kind']
                if fail==kind:raise KeyboardInterrupt()
                if kind=='parser':return ('c'*64+'\n').encode()
                if kind=='caller':
                    self.assertTrue((root/'job/caller.once').exists())
                    envelope=m.closed_json(m.fd_read(attempt['fds'][2]))
                    manifest=m.closed_json(m.fd_read(attempt['fds'][1]));mh=m.sha(m.fd_read(attempt['fds'][1]))
                    self.assertEqual(envelope['config']['deadline'],901.)
                    self.assertEqual(envelope['external_caller']['sha256'],m.sha((root/'job/caller.once').read_bytes()))
                    binding=m.closed_json((root/'job/caller.once').read_bytes())['binding']
                    self.assertEqual((binding['caller_pid'],binding['caller_ppid'],binding['tool_session']),(987,os.getpid(),'actual-session'))
                    result=dict(manifest_sha256=mh,actual_executed=True,success=True,status='target_lifecycle_verified',isolated_restore_verified=False,production=False,provider=False,timer=False,
                        origins={'fake':'e'*64},independent_contexts_before_capture=True,context_sha256=['a'*64,'b'*64],capture_committed=True,targets=[dict(status='target_instance_bound',identity_sha256='a'*64,runtime_journal_sha256='b'*64,policy_sha256='c'*64,original_deadline=901.,readiness_observed=True,tcp_scram_hba_observed=True,
                            durability_observed=True,wrong_bootstrap_password_denied=True,pid1_identity_observed=True,docker_network_none_configured=True,kernel_network_isolation_proven=False,isolated_restore_verified=False,production=False,provider=False,timer=False) for _ in range(2)],
                        source_preservation=dict(scope='post-capture-to-post-target-before-own-source-cleanup',baseline_sha256='f'*64,post_sha256='f'*64,equal=True),normal_work_state=c.graph_state({}),receipt={'fixture':True},files={},
                        stage_retained_after_close=True,caller_fds_alive=True,dump_preflight_complete=True,SQL_restore=False,final_work_state=c.graph_state({}),child_or_remote_unknown=False,cleanup_error_classes=[],synthetic_cleanup_complete=True)
                    if fail=='terminal':result['targets'][0]['readiness_observed']=False
                    if fail=='counters':result['normal_work_state']['source_active_work']=1
                    if fail=='missing-state':result['normal_work_state'].pop('source_work_unknown')
                    if fail=='extra-state':result['final_work_state']['new_flag']=False
                    if fail=='wrong-type':result['final_work_state']['source_owner_active']=False
                    if fail=='negative-history':result['normal_work_state']['parser_local_terminal_count']=-1
                    if fail=='wrong-deadline':result['targets'][0]['original_deadline']=902.
                    result['origins']={'fake':dict(path_sha256='e'*64,sha256='e'*64)}
                    result['files']={name:dict(size=1,sha256='a'*64) for name in c.FIXED}
                    result['receipt']=dict(authenticated_stage=True,dump_preflight_complete=True,plan_sha256='a'*64,registry_sha256='b'*64,source_facts_sha256='c'*64,archive_sha256='d'*64,ciphertext_sha256='e'*64,
                        parser=dict(cid='c'*64,image_id=m.PARSER_IMAGE,tool_sha256=m.PARSER_TOOL_SHA,docker_sha256=m.DOCKER_SHA,inspect_sha256='f'*64,authority_sha256='a'*64),
                        dump_inspection={kind:dict(toc_entries=c.TEMPLATE_PINS[kind]['selected'],toc=dict(size=1,sha256='a'*64),schema=dict(size=1,sha256='a'*64),data=dict(size=1,sha256='a'*64),tables=1,sequences=1,rows=1,
                            toc_equal=True,schema_equal=True,data_equal=True,copy_equal=True,sequences_equal=True) for kind in ('central','identity')})
                    m.report_write(attempt['fds'][3],result)
                return b''
            def docker(actor,*args):
                calls.append(args)
                self.assertTrue((root/'job/launch.once').exists())
                if fail==args[0]:raise KeyboardInterrupt()
                if args[0]=='start':return ('c'*64+'\n').encode()
                if args[0]=='exec':return (m.PARSER_TOOL_SHA+'  /usr/lib/postgresql/16/bin/pg_restore\n').encode()
                if args[0]=='inspect':return m.canonical([dict(Id='c'*64,Image=m.PARSER_IMAGE,Config=dict(Labels=dict(purpose='b3-dump-parser',operation_nonce='b3-'+job['operation_nonce'][:32]),Cmd=['720']))])
                raise AssertionError(args)
            dependencies={str(interpreter):spec['interpreter']['sha256']}
            free=types.SimpleNamespace(f_bavail=64*1024**3,f_frsize=1)
            try:
                with contextlib.ExitStack() as stack:
                    for obj,name,value in ((m,'verify_inventory',lambda s:inventory_checks.append('current')),(m.ProcessGraph,'_spawn',spawn),(m.ProcessGraph,'wait',wait),(m.ProcessGraph,'docker',docker),(m.ProcessGraph,'observe',lambda a,b:None),
                        (m,'dependency_hashes',lambda r:dependencies),(c,'dependency_hashes',lambda r:dependencies),(c,'source_document',lambda cfg:{'user':'admin'}),(c,'target_specs',lambda *a:('central','identity')),
                        (m.time,'monotonic',lambda:2.),(m.os,'statvfs',lambda p:free)):
                        stack.enter_context(patch.object(obj,name,value))
                    stack.enter_context(patch.dict(sys.modules,{module.__name__:module}))
                    if fail:
                        with self.assertRaises((ValueError,KeyboardInterrupt)):m.workflow(spec,job,'actual-session',graph)
                        self.assertFalse((boot/'qualification.safe.json').exists())
                    else:
                        result=m.workflow(spec,job,'actual-session',graph)
                        self.assertTrue(result['success']);self.assertTrue((boot/'qualification.safe.json').exists())
                        self.assertEqual(len(inventory_checks),3)
                        self.terminal_fixture=(m,copy.deepcopy(result))
                    self.assertEqual(sum(str(m.LAUNCHER) in call for call in calls),int(fail not in ('collector','prepare','parser','start','exec','inspect')))
                    self.assertLessEqual(sum('create' in call for call in calls),1)
                    self.assertFalse(any(any(x in call for x in ('stop','rm','kill','restart','pull')) for call in calls))
            finally:
                for actor in {id(a):a for a in [graph,*actors]}.values():
                    for fd in actor.fds:
                        try:os.close(fd)
                        except OSError:pass

    def test_fixed_collector_prepare_oneparser_park_finalize_monitor(self):self.exercise()

    def test_each_uncertain_stage_stops_without_retry_cleanup_or_safe_success(self):
        for phase in ('collector','prepare','parser','start','exec','inspect','pid','caller','terminal','counters','missing-state','extra-state','wrong-type','negative-history','wrong-deadline'):
            with self.subTest(phase=phase):self.exercise(phase)

class CollectorControls(ModuleLoader):
    def test_fixed_fresh_collector_retains_attempts_and_never_leaks_password(self):
        import types,contextlib,copy
        m=self.module();events=[]
        source=dict(database='postgres',user='admin',password='only-private-credential')
        inspection=dict(Id=m.SOURCE_CID,Image=m.PARSER_IMAGE,State=dict(Running=True,Pid=62835,StartedAt='2026-11-03T09:24:06.729872208Z',Restarting=False,Paused=False,Dead=False),RestartCount=0,
            Config=dict(User='',Image=m.PARSER_IMAGE),ExecIDs=None,Mounts=[dict(Type='volume',Name='easel-b2-disk-e419567e242e68b72e0a7b3a26d4385f-data',Destination='/var/lib/postgresql/data',RW=True)],
            NetworkSettings=dict(Ports={'5432/tcp':[dict(HostIp='127.0.0.1',HostPort='51430')]}))
        tools=''.join(digest+'  '+path+'\n' for _,path,digest in m.TOOLS)+(m.PARSER_TOOL_SHA+'  /usr/lib/postgresql/16/bin/pg_restore\n')
        with tempfile.TemporaryDirectory(dir='/private/tmp') as temp:
            os.chown(temp,os.getuid(),os.getgid());g=m.ProcessGraph(temp,dict(started=1.,deadline=901.))
            def docker(*args):
                events.append(args)
                return m.canonical([inspection]) if args[0]=='inspect' else tools.encode()
            def connect(**kw):
                self.assertEqual(g.operations[-1]['operation'],'readonly-connect')
                self.assertIsNone(g.operations[-1]['connection'])
                self.assertEqual(kw['password'],source['password']);self.assertEqual(kw['port'],51430)
                self.assertIn('default_transaction_read_only=on',kw['options'])
                return connection
            def execute(sql):
                self.assertEqual(g.operations[-1]['operation'],'readonly-system-query')
                self.assertEqual(sql,"SELECT system_identifier::text,current_user,current_setting('server_version_num') FROM pg_control_system()")
                return types.SimpleNamespace(fetchone=lambda:('123456789','admin','160006'))
            connection=types.SimpleNamespace(execute=execute,close=lambda:events.append('close'))
            psycopg=types.ModuleType('psycopg');psycopg.connect=connect
            with patch.dict(sys.modules,psycopg=psycopg),patch.object(m,'verify_inventory'),patch.object(m,'check_loaded_origins'),patch.object(m,'source_document',return_value=source),patch.object(g,'docker',docker),patch.object(m.time,'monotonic',return_value=2.):
                result=m.source_collect(dict(authority={},source={}),dict(operation_nonce='a'*64,started=1.,deadline=901.),g)
                self.assertNotIn(source['password'],m.canonical(result).decode())
                self.assertEqual(result['system_id_sha256'],m.sha(b'123456789'))
                self.assertEqual(len([x for x in events if type(x) is tuple and x[0]=='inspect']),2)
                self.assertEqual(events[1],('exec',m.SOURCE_CID,'/usr/bin/sha256sum',*[v[1] for v in m.TOOLS],'/usr/lib/postgresql/16/bin/pg_restore'))
                self.assertIn(connection,g.connections)
            for fd in g.fds:os.close(fd)

    def test_inventory_rejects_unpinned_file_alias_drift_and_missing_loaded_origin(self):
        m=self.module()
        with tempfile.TemporaryDirectory(dir='/private/tmp') as temp:
            root=Path(temp);real=root/'real';real.write_bytes(b'fixed');alias=root/'python';alias.symlink_to(real)
            pth=root/'locked-runtime-dependencies.pth';pth.write_text(str(root)+'\n')
            dependencies={str(x):m.sha(x.read_bytes()) for x in (real,alias,pth)}
            spec=dict(runtime_roots=[str(root)],pth_files=[str(pth)],symlink_targets={str(alias):str(real)},runtime=dict(dependencies=dependencies),repository_dependencies={})
            with patch.object(m,'STARTUP_ROOTS',(str(root),)),patch.object(m,'dependency_hashes',return_value=dependencies):
                m.verify_inventory(spec)
                extra=root/'unexpected';extra.write_bytes(b'new')
                with self.assertRaises(ValueError):m.verify_inventory(spec)
                extra.unlink();alias.unlink();alias.symlink_to(pth)
                with self.assertRaises(ValueError):m.verify_inventory(spec)
            with patch.object(m.sys,'modules',{'unknown':type('Module',(),{'__file__':str(real)})()}):
                with self.assertRaises(ValueError):m.check_loaded_origins(dict(runtime=dict(dependencies={}),repository_dependencies={}))

    def test_observe_deadline_and_pipe_cap_are_sticky_without_kill(self):
        import types
        m=self.module()
        with tempfile.TemporaryDirectory(dir='/private/tmp') as temp:
            g=m.ProcessGraph(temp,dict(started=1.,deadline=901.))
            a=dict(streams=[dict(eof=False,count=4,limit=4)],process=types.SimpleNamespace(poll=lambda:None))
            with patch.object(m.time,'monotonic',return_value=2.):
                with self.assertRaises(ValueError):g.observe(a)
            self.assertTrue(g.unknown)
            g2=m.ProcessGraph(temp,dict(started=1.,deadline=901.))
            with patch.object(m.time,'monotonic',return_value=901.):
                with self.assertRaises(ValueError):g2.observe(a)
            self.assertTrue(g2.unknown)

class SpecControls(ModuleLoader):
    def test_closed_spec_fixed_source_runtime_and_alias_map(self):
        import copy
        m=self.module();prefix='/private/tmp/easel-central-catalog-candidate-20261103-r1/runtime';exe=prefix+'/bin/python3'
        spec=dict(format='b3-external-supervisor-spec-v1',bootstrap='/private/tmp/new-bootstrap',directory='/private/tmp/new-job',supervisor_nonce='a'*64,operation_nonce='b'*64,
            source=dict(container_id=m.SOURCE_CID,image=m.SOURCE_IMAGE_REF,nonce='e419567e242e68b72e0a7b3a26d4385f',sha256='25ad6293eedc068d8b805353c5d966cf285a308a429b7db50e38868238231ee4'),
            authority=dict(path='/private/tmp/easel-b2-owned-pg-e419567e242e68b72e0a7b3a26d4385f/authority.private.json',sha256='25ad6293eedc068d8b805353c5d966cf285a308a429b7db50e38868238231ee4'),
            runtime=dict(prefix=prefix,testtools=m.STARTUP_ROOTS[2],dependencies={exe:'c'*64,'/base/python':'c'*64}),interpreter=dict(path=exe,sha256='c'*64),
            runtime_roots=list(m.STARTUP_ROOTS),pth_files=[prefix+'/locked.pth'],symlink_targets={exe:'/base/python'},repository_dependencies={'/repo/script':'d'*64})
        self.assertEqual(m.validate_spec(spec),spec)
        for fn in (lambda v:v.update(hook='arbitrary'),lambda v:v['source'].update(nonce='f'*32),lambda v:v['interpreter'].update(path='/foreign/python'),lambda v:v['symlink_targets'].update({exe:'/unpinned'})):
            bad=copy.deepcopy(spec);fn(bad)
            with self.assertRaises(ValueError):m.validate_spec(bad)

    def test_passive_bootstrap_timeout_never_creates_job_or_collects(self):
        m=self.module()
        with tempfile.TemporaryDirectory(dir='/private/tmp') as temp:
            os.chown(temp,os.getuid(),os.getgid());root=Path(temp);exe=root/'python';exe.write_bytes(b'fake')
            spec=dict(bootstrap=str(root/'boot'),directory=str(root/'job'),supervisor_nonce='a'*64,operation_nonce='b'*64,interpreter=dict(path=str(exe),sha256=m.sha(exe.read_bytes())))
            m.write_new(root/'spec',m.canonical(spec))
            with patch.object(m,'validate_spec',side_effect=lambda x:x),patch.object(m.sys,'executable',str(exe)),patch.object(m.time,'monotonic',side_effect=[1.,122.]),patch.object(m,'workflow',side_effect=AssertionError('resource path entered')),patch('builtins.print'):
                with self.assertRaises(ValueError):m.run(root/'spec')
            self.assertTrue((root/'boot/bootstrap.private.json').exists())
            self.assertFalse((root/'boot/original_job.private.json').exists());self.assertFalse((root/'job').exists())

class ReviewFixControls(ModuleLoader):
    def test_all_inventoried_pth_must_have_complete_policy_coverage(self):
        m=self.module()
        with tempfile.TemporaryDirectory(dir='/private/tmp') as temp:
            root=Path(temp);pth=root/'locked-runtime-dependencies.pth';pth.write_text(str(root)+'\n')
            deps={str(pth):m.sha(pth.read_bytes())}
            spec=dict(runtime_roots=[str(root)],pth_files=[str(pth)],symlink_targets={},runtime=dict(dependencies=deps),repository_dependencies={})
            # This is only private tempFS; production startup roots remain fixed.
            with patch.object(m,'STARTUP_ROOTS',(str(root),),create=True),patch.object(m,'dependency_hashes',return_value=deps):
                m.verify_inventory(spec)
                hidden=root/'unlisted.pth';hidden.write_bytes(b'import arbitrary_hook')
                deps[str(hidden)]=m.sha(hidden.read_bytes())
                with self.assertRaises(ValueError):m.verify_inventory(spec)
                spec['pth_files'].append(str(hidden))
                with self.assertRaises(ValueError):m.verify_inventory(spec)
                hidden.unlink();deps.pop(str(hidden));spec['pth_files'].remove(str(hidden))
                extra=root/'unlisted-new-file';extra.write_bytes(b'unpinned')
                with self.assertRaises(ValueError):m.verify_inventory(spec)
                extra.unlink();spec['pth_files'].append(str(root/'nonexistent.pth'))
                with self.assertRaises(ValueError):m.verify_inventory(spec)

    def test_readonly_reopen_matches_original_inode_and_rejects_replacement(self):
        m=self.module()
        with tempfile.TemporaryDirectory(dir='/private/tmp') as temp:
            os.chown(temp,os.getuid(),os.getgid());root=Path(temp);g=m.ProcessGraph(root,{})
            original=g.new('input',b'fixed')
            reopened=m._fd(g,root/'input',original)
            self.assertEqual(fcntl.fcntl(reopened,fcntl.F_GETFL)&os.O_ACCMODE,os.O_RDONLY)
            self.assertEqual(m.stamp(os.fstat(original)),m.stamp(os.fstat(reopened)))
            realread=m.private_read
            def replaced(path):
                value=realread(path);(root/'input').unlink();m.write_new(root/'input',b'fixed');return value
            with patch.object(m,'private_read',replaced):
                with self.assertRaises(ValueError):m._fd(g,root/'input',original)
            for fd in g.fds:os.close(fd)


class ExecutionTerminalControls(ModuleLoader):
    exercise=WorkflowControls.exercise
    def valid(self):
        self.exercise();module,outcome=self.terminal_fixture
        outcome['status']='target_worker_attestation_verified'
        for key in ('normal_work_state','final_work_state'):
            outcome[key].update(execution_owner_count=1,execution_target_count=2,execution_phase_claims=2,
                execution_committed_count=2,execution_local_terminal_count=18)
        execution=dict(format='b3-target-worker-attestation-v1',target_bound=True,attestation_complete=True,
            SQL_restore=False,isolated_restore_verified=False,input_sha256='9'*64,context_sha256=dict(zip(('central','identity'),outcome['context_sha256'])),
            job_device=1,job_inode=2,started_at_ns=1000000000,deadline_ns=901000000000,summaries=[])
        for kind,ctx,target in zip(('central','identity'),outcome['context_sha256'],outcome['targets']):
            execution['summaries'].append(dict(kind=kind,format='easel.b3.worker-attestation-summary.v3',operation='attest_worker',
                binding_sha256=('3' if kind=='central' else '4')*64,manifest_sha256='5'*64,request_sha256=('6' if kind=='central' else '7')*64,
                private_attestation_sha256='8'*64,input_sha256=execution['input_sha256'],context_sha256=ctx,
                target_identity_sha256=target['identity_sha256'],job_device=1,job_inode=2,started_at_ns=1000000000,deadline_ns=901000000000))
        root=dict(restore_id='original-restore',source_container_id=module.SOURCE_CID,
            operation_ids=dict(central='original-central',identity='original-identity'),job_device=1,job_inode=2,
            started_at_ns=1000000000,deadline_ns=901000000000)
        for kind,summary,target in zip(('central','identity'),execution['summaries'],outcome['targets']):
            target['target_cid']=('1' if kind=='central' else '2')*64
            binding=dict(restore_id=root['restore_id'],operation_id=root['operation_ids'][kind],kind=kind,operation='attest_worker',
                source_container_id=root['source_container_id'],target_container_id=target['target_cid'],target_identity_sha256=target['identity_sha256'],
                verified_input_sha256=execution['input_sha256'],context_sha256=execution['context_sha256'][kind],
                **{k:root[k] for k in ('job_device','job_inode','started_at_ns','deadline_ns')})
            summary['binding_data']=binding;summary['binding_sha256']=module.sha(module.canonical(binding))
        outcome['execution']=execution
        return module,outcome,root

    def test_exact_attest_positive_and_forged_counters_closed(self):
        module,outcome,root=self.valid()
        self.assertTrue(module.validate_terminal(outcome,outcome['manifest_sha256'],901.,expected_root_context=root))
        for field,value in [('execution_owner_count',0),('execution_target_count',1),('execution_phase_claims',True),
            ('execution_committed_count',1),('execution_local_terminal_count',0),('execution_remote_live_or_unobserved',1),
            ('execution_active_work',2),('execution_unknown',True),('execution_disposition_retained',True),('extra',False)]:
            bad=copy.deepcopy(outcome);bad['final_work_state'][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):module.validate_terminal(bad,bad['manifest_sha256'],901.,expected_root_context=root)
        bad=copy.deepcopy(outcome);bad['normal_work_state'].pop('execution_owner_count')
        with self.assertRaises(ValueError):module.validate_terminal(bad,bad['manifest_sha256'],901.,expected_root_context=root)
        for field,value in [('input_sha256','0'*64),('context_sha256','0'*64),('deadline_ns',902000000000),('job_inode',3),('kind','identity')]:
            bad=copy.deepcopy(outcome);bad['execution']['summaries'][0][field]=value
            with self.subTest(binding=field),self.assertRaises(ValueError):module.validate_terminal(bad,bad['manifest_sha256'],901.,expected_root_context=root)

    def test_attest_requires_root_context_and_recomputed_wrong_fixed_binding_fails(self):
        module,outcome,root=self.valid()
        with self.assertRaises(ValueError):module.validate_terminal(outcome,outcome['manifest_sha256'],901.)
        for key,value in [('restore_id','foreign'),('operation_id','foreign'),('source_container_id','0'*64)]:
            bad=copy.deepcopy(outcome);binding=bad['execution']['summaries'][0]['binding_data'];binding[key]=value
            bad['execution']['summaries'][0]['binding_sha256']=module.sha(module.canonical(binding))
            with self.subTest(key=key),self.assertRaises(ValueError):
                module.validate_terminal(bad,bad['manifest_sha256'],901.,expected_root_context=root)

    def test_legacy_graph_versions_exact_and_never_mixed(self):
        self.exercise();module,outcome=self.terminal_fixture
        for key in ('normal_work_state','final_work_state'):
            outcome[key]={k:v for k,v in outcome[key].items() if not k.startswith('execution_')}
        self.assertTrue(module.validate_terminal(outcome,outcome['manifest_sha256'],901.))
        import b3_target_once
        outcome['normal_work_state']=b3_target_once.graph_state({})
        with self.assertRaises(ValueError):module.validate_terminal(outcome,outcome['manifest_sha256'],901.)

    def test_requested_attest_rejects_target_only_child_status(self):
        self.exercise('attest-target-only')
