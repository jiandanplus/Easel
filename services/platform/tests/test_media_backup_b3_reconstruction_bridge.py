"""Original Root, native FD transport and real fixed program with fake PG only."""
import os,dataclasses,unittest
from unittest.mock import patch
import test_media_backup_b3_execution as h
from media_backup_b3_replay_fixture import matching_graph
from platform_service.media_backup_b3 import reconstruction_worker as worker,reconstruction_contract as c,reconstruction_runtime as r
m=h.m
_SYNTHETIC_WORKER=h._SYNTHETIC_WORKER

class BridgeTests(unittest.TestCase):
    setUp=h.ExecutionTests.setUp
    pair=h.ExecutionTests.pair
    close_synthetic_execution_fds=h.ExecutionTests.close_synthetic_execution_fds
    def transport(self,owner,fault=None):
        import copy,json,threading
        from test_media_backup_b3_execution_baseline import fixture
        from test_media_backup_b3_network_fixtures import network_evidence
        baseline=fixture();ns=baseline['before']['own_netns'];outer=self
        image=dict(Id=self.native.worker.image_id,Architecture='arm64',Os='linux',
            Config=dict(Volumes=None,ExposedPorts=None,Env=list(self.native.worker.public_env),
                Entrypoint=m._baseline._RECONSTRUCTION_ENTRY,Cmd=['reconstruct_worker'],User='10001:10001',WorkingDir='/opt/easel'),
            RootFS=dict(Type='layers',Layers=['sha256:'+'f'*64]))
        # Independent synthetic full image inspect pin, established before owner.
        self.assertEqual(owner._reconstruction_policy.native.worker.image_inspect_sha256,m.digest(image))
        workers={};sent=[];threads=[];calls=[]
        class Process:
            def __init__(self,argv,**kw):
                a=next(a for a in owner._role_slot.runtime.phases if not owner._role_slot.runtime.committed(a))
                slot=a.operations[-1];op=slot.operation;calls.append((a.kind,op))
                self.code=None;self.slot=slot
                outer.assertIs(slot.process,self)
                fds=[os.dup(kw[k]) for k in ('stdin','stdout','stderr')]
                def work():
                    try:
                        if op=='worker-start':
                            installed=outer.installed;installed.fds=tuple(fds)
                            captured=bytearray();request=bytearray()
                            def read(fd,size):
                                data=os.read(fds[0] if fd==0 else fd,size)
                                if fd==0:request.extend(data)
                                return data
                            def write(fd,data):
                                if fd==1 and data==worker.READY_FRAME:
                                    outer.now[0]+=getattr(outer,'ready_delay',0)
                                    return os.write(fds[1],data)
                                if fd==1:captured.extend(data);return len(data)
                                return os.write(fds[fd] if fd in (0,1,2) else fd,data)
                            installed.os.read=read;installed.os.write=write
                            code=installed.worker.main()
                            if request:sent.append(bytes(request))
                            if code:self.code=code;return
                            out=bytes(captured)
                        elif op=='target-inspect':out=json.dumps([outer.case.fixture[a.kind][1]]).encode()
                        elif op=='image-inspect':out=json.dumps([image]).encode()
                        elif op=='worker-create':workers[a.kind]=format(a.ordinal,'064x');out=(workers[a.kind]+'\n').encode()
                        elif op=='worker-inspect':
                            w=copy.deepcopy(_SYNTHETIC_WORKER);w['Id']=workers[a.kind];w['Name']='/'+a.worker_slot.name
                            w['Args']=m._baseline._RECONSTRUCTION_ENTRY[1:]+['reconstruct_worker']
                            w['Config'].update(Entrypoint=m._baseline._RECONSTRUCTION_ENTRY,Cmd=['reconstruct_worker'],Hostname=w['Id'][:12],Image=owner._reconstruction_policy.native.worker.image_id,
                                Env=list(owner._reconstruction_policy.native.worker.public_env),Labels=a.worker_slot.labels)
                            l=owner._reconstruction_policy.native.limits;w['HostConfig'].update(NetworkMode='container:'+a.target_slot.cid,
                                Memory=l.memory_bytes,MemorySwap=l.memory_swap_bytes)
                            main=a.worker_slot.main
                            if main is None:w['State'].update(Status='created',Running=False,Pid=0,StartedAt='0001-01-01T00:00:00Z')
                            elif main.process.code is not None:w['State'].update(Status='exited',Running=False,Pid=0,FinishedAt='2026-11-04T10:42:13Z')
                            if w['State']['Status']=='created':
                                for field in ('ResolvConfPath','HostnamePath','HostsPath','LogPath'):w[field]=''
                                w['HostConfig']['OomKillDisable']=False
                            else:
                                target=outer.case.fixture[a.kind][1]
                                for field in ('ResolvConfPath','HostnamePath','HostsPath'):w[field]=target[field]
                                w['LogPath']='/var/lib/docker/containers/'+w['Id']+'/'+w['Id']+'-json.log'
                                w['Config'].update(Hostname=target['Config']['Hostname'],Domainname=target['Config']['Domainname'])
                            out=json.dumps([w]).encode()
                        elif op=='probe-hashes':out=''.join(r[2]+'  '+r[1]+'\n' for r in owner._reconstruction_policy.native.target_probe.tools).encode()
                        elif op in ('probe-ns','probe-self-ns'):out=(str(ns['device'])+' '+str(ns['inode'])+'\n').encode()
                        elif op in ('probe-link','probe-self-link'):out=('net:['+str(ns['inode'])+']\n').encode()
                        elif op=='probe-stat':out=baseline['before']['pid1_stat_raw'].encode()
                        elif op=='probe-exe':out=b'/usr/lib/postgresql/16/bin/postgres\n'
                        elif op=='observer':
                            data=bytearray()
                            while True:
                                b=os.read(fds[0],65536)
                                if not b:break
                                data.extend(b)
                            outer.assertGreater(len(data),4)
                            raw=m.canonical(baseline);out=len(raw).to_bytes(4,'big')+raw
                        else:raise AssertionError(op)
                        if fault:out=fault(a,slot,out)
                        os.set_blocking(fds[1],True)
                        offset=0
                        while offset<len(out):offset+=os.write(fds[1],out[offset:offset+8192])
                        self.code=0
                    except BaseException as exc:self.error=exc;self.code=17
                    finally:
                        for fd in fds:os.close(fd)
                t=threading.Thread(target=work,daemon=True);threads.append(t);t.start()
            def poll(self):return self.code
        self.addCleanup(lambda:[t.join(.2) for t in threads])
        self.addCleanup(lambda:[os.close(fd) for a in owner._role_slot.runtime.phases for sl in a.operations for pair in sl.fds for fd in pair if (id(sl),fd) not in owner._native_closed])
        patcher=patch.object(m,'_popen',Process);patcher.start();self.addCleanup(patcher.stop)
        return sent,calls


    def prepare(self,policy_change=None):
        graph=matching_graph(self)
        import tempfile
        from pathlib import Path
        from reconstruction_installed_fixture import Installed
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.installed=Installed(Path(temp.name)/'installed',lambda **kw:PG(**kw))
        self.installed.worker._SESSIONS=worker._SESSIONS
        self.installed.put('/sys/fs/cgroup/memory.max',(str(self.policy.limits.memory_bytes)+'\n').encode(),False)
        pins=self.installed.build()
        image=dict(Id=self.policy.worker.image_id,Architecture='arm64',Os='linux',
            Config=dict(Volumes=None,ExposedPorts=None,Env=list(self.policy.worker.public_env),
                Entrypoint=m._baseline._RECONSTRUCTION_ENTRY,Cmd=['reconstruct_worker'],User='10001:10001',WorkingDir='/opt/easel'),
            RootFS=dict(Type='layers',Layers=['sha256:'+'f'*64]))
        self.native=dataclasses.replace(self.policy,worker=dataclasses.replace(self.policy.worker,image_inspect_sha256=m.digest(image),
            artifact_sha256=pins['artifact_sha256'],dependency_manifest_sha256=pins['manifest_sha256'],entrypoint_sha256=pins['entrypoint_sha256']))
        owner=m.RootExecutionOwner(self.case.factory(),self.case.input,self.policy,
            reconstruction_policy=r.RootReconstructionPolicy(self.native,pins['client_sha256'],pins['program_sha256'],pins['executable_sha256']))
        if policy_change:policy_change(owner)
        self.pair(owner);owner._admit_role_plan(graph.pair,source_owners=graph.owners,authorizations=graph.authorizations)
        runtime=owner._begin_reconstruction(owner._targets['central'].original_handle,owner._input,'central')
        self.connections=[];outer=self
        self.catalog=dict(rows=None,members=[],inventory='original',statements=[])
        class PG:
            def __init__(self,**kw):
                self.q=outer.installed.worker._SESSIONS[-1].request;self.settings={};self.result=[];self.commits=0;self.closed=False
                if outer.catalog['rows'] is None:
                    outer.catalog['rows']=[(kw['user'],10,True,True,True,True,True,True,True,-1,None,'SCRAM-SYNTHETIC',None)]
                self.final=list(outer.catalog['rows'])
                for i,d in enumerate(self.q.plan.roles):
                    e=d.expectation;self.final.append((e.name,200+i,e.superuser,e.inherit,e.createrole,e.createdb,
                        False,e.replication,e.bypassrls,e.connlimit,e.validuntil,e.verifier,e.config))
                outer.connections.append(self)
            def cursor(self):return self
            def execute(self,q,params=None):
                text=q if type(q) is str else q.as_string(None)
                outer.catalog['statements'].append(text)
                if q==worker._SESSION:self.result=[(123,'synthetic',10,10,'10001',160015)]
                elif q==worker._ROLES:self.result=list(outer.catalog['rows'])
                elif q==worker._MEMBERS:self.result=list(outer.catalog['members'])
                elif q==worker._INITDB:self.result=[(m.canonical(outer.catalog['rows']).decode()+m.canonical(outer.catalog['members']).decode()+outer.catalog['inventory'],)]
                elif text=='SELECT extname FROM pg_catalog.pg_extension ORDER BY extname':self.result=[('plpgsql',)]
                elif text=='SELECT pg_catalog.set_config(%s,%s,false)':self.settings[params[0]]=params[1];self.result=[(params[1],)]
                elif text=='SELECT pg_catalog.current_setting(%s)':self.result=[(self.settings.get(params[0],''),)]
                elif text.startswith('CREATE ROLE '):outer.catalog['rows']=self.final
                elif text=='COMMIT':
                    self.commits+=1
                    if getattr(outer,'lost_commit',False):raise OSError('synthetic lost commit')
            def fetchone(self):return self.result[0]
            def fetchall(self):return self.result
            def close(self):
                if getattr(outer,'lost_close',False):raise OSError('synthetic close')
                self.closed=True
        patcher=patch.object(worker,'_connect',PG);patcher.start();self.addCleanup(patcher.stop)
        patcher=patch.object(worker,'_clock',lambda:100.);patcher.start();self.addCleanup(patcher.stop)
        return owner,runtime,graph

    def test_measured_namespace_rejected_by_original_host_even_when_decoder_accepts(self):
        import json
        owner,runtime,graph=self.prepare()
        def fault(a,slot,out):
            if slot.operation!='worker-start':return out
            d=json.loads(out[4:])
            for side in ('before','after'):
                security=d['installed_evidence'][side]['security']
                security['netns']['inode']+=1
                for key in ('namespace_before','namespace_after'):security['network'][key]['inode']+=1
            changed=c.bounded_frame(d,c.RESULT_LIMIT)
            c.decode_result(changed,a.request_bytes)
            return changed
        self.transport(owner,fault)
        with self.assertRaises(m.B3Error):runtime._dispatch('central')
        self.assertTrue(runtime.unknown);self.assertEqual(graph.pair._consumers,1)
        self.assertFalse(any(runtime.committed(p) for p in runtime.phases))

    def test_echoed_build_cannot_replace_measured_hash(self):
        import json
        owner,runtime,graph=self.prepare()
        def fault(a,slot,out):
            if slot.operation!='worker-start':return out
            d=json.loads(out[4:]);d['installed_evidence']['entrypoint_sha256']='0'*64
            return c.bounded_frame(d,c.RESULT_LIMIT)
        self.transport(owner,fault)
        with self.assertRaises(m.B3Error):runtime._dispatch('central')
        self.assertTrue(runtime.unknown)

    def _inventory_drift(self,kind):
        owner,runtime,graph=self.prepare();self.transport(owner)
        runtime._dispatch('central')
        if kind=='role':self.catalog['rows'].append(('extra',99,False,True,False,False,False,False,False,-1,None,None,None))
        elif kind=='member':self.catalog['members'].append(('pg_read_all_data','postgres','postgres',False,True,True))
        else:self.catalog['inventory']='changed database/dependency inventory'
        self.catalog['statements'].clear()
        with self.assertRaises(m.B3Error):runtime._dispatch('central')
        self.assertFalse(any(q=='BEGIN' or q=='COMMIT' or q.startswith('CREATE ROLE ') for q in self.catalog['statements']))
        self.assertEqual(self.connections[-1].commits,0)
        self.assertEqual(worker._SESSIONS[-1].state,'UNKNOWN')
        self.assertTrue(runtime.unknown);self.assertEqual(graph.pair._consumers,1)
        with self.assertRaises(m.B3Error):runtime._role_proof(runtime.phases[-1])

    def test_original_inventory_rejects_extra_role(self):self._inventory_drift('role')
    def test_original_inventory_rejects_extra_membership(self):self._inventory_drift('member')
    def test_original_inventory_rejects_catalog_drift(self):self._inventory_drift('inventory')

    def test_policy_replaced_before_begin_is_refused(self):
        with patch.object(m,'_popen') as native:
            with self.assertRaises(m.B3Error):
                self.prepare(lambda owner:setattr(owner,'_reconstruction_policy',dataclasses.replace(owner._reconstruction_policy)))
            native.assert_not_called()

    def test_changed_policy_pins_before_begin_is_refused(self):
        with patch.object(m,'_popen') as native:
            with self.assertRaises(m.B3Error):
                self.prepare(lambda owner:setattr(owner,'_reconstruction_policy',dataclasses.replace(owner._reconstruction_policy,program_sha256='e'*64)))
            native.assert_not_called()

    def test_policy_removed_before_begin_is_refused(self):
        with patch.object(m,'_popen') as native:
            with self.assertRaises(m.B3Error):self.prepare(lambda owner:setattr(owner,'_reconstruction_policy',None))
            native.assert_not_called()

    def test_request_plan_overflow_precedes_first_native(self):self._preflight_overflow('PLAN_LIMIT')
    def test_request_frame_overflow_precedes_first_native(self):self._preflight_overflow('REQUEST_LIMIT')

    def _preflight_overflow(self,limit):
        owner,runtime,graph=self.prepare()
        with patch.object(c,limit,1),patch.object(m,'_popen',side_effect=OSError('forbidden native')) as native:
            with self.assertRaises((m.B3Error,c.ProtocolError)):runtime._dispatch('central')
            native.assert_not_called()
        self.assertFalse(runtime.unknown);self.assertFalse(owner._unknown)
        self.assertEqual(owner._native_originals,[])
        self.assertEqual(runtime.phases,[]);self.assertEqual(graph.pair._consumers,1)
        owner._event.set();owner._close_role_plan_consumer();owner.close()
        self.assertEqual(graph.pair._consumers,0);self.assertTrue(owner._closed)

    def test_private_auth_overflow_precedes_first_native(self):
        from platform_service.media_backup_b3 import target_factory as factory
        owner,runtime,graph=self.prepare();original=factory._parse_env
        def oversized(*args,**kwargs):
            values=original(*args,**kwargs);values['POSTGRES_PASSWORD']='s'*4097;return values
        with patch.object(factory,'_parse_env',oversized),patch.object(m,'_popen',side_effect=OSError('forbidden native')) as native:
            with self.assertRaises((m.B3Error,c.ProtocolError)):runtime._dispatch('central')
            native.assert_not_called()
        self.assertFalse(runtime.unknown);self.assertEqual(runtime.phases,[])
        self.assertEqual(owner._native_originals,[])

    def test_maximum_private_auth_is_preflighted_and_remaining_sampled_after_ready(self):
        from platform_service.media_backup_b3 import target_factory as factory
        owner,runtime,graph=self.prepare();sent,calls=self.transport(owner);self.ready_delay=7
        original=factory._parse_env
        def maximum(*args,**kwargs):
            values=original(*args,**kwargs);values['POSTGRES_PASSWORD']='s'*4096;return values
        with patch.object(factory,'_parse_env',maximum):phase=runtime._dispatch('central')
        request=c.decode_request(sent[0]);self.assertEqual(len(request.private_auth['operational_password']),4096)
        self.assertEqual(request.remaining_ns,53*10**9)
        self.assertEqual(phase.acceptance_deadline-phase.main_start_anchor,60)
        self.assertLessEqual(len(sent[0]),len(runtime._preflights[0][1]))

    def test_original_private_projection_cannot_change_after_preflight(self):
        from platform_service.media_backup_b3 import target_factory as factory
        owner,runtime,graph=self.prepare();sent,calls=self.transport(owner);original=factory._parse_env
        def changed(*args,**kwargs):
            values=original(*args,**kwargs)
            if runtime.phases:values['POSTGRES_PASSWORD']='changed-synthetic'
            return values
        with patch.object(factory,'_parse_env',changed):
            with self.assertRaises(m.B3Error):runtime._dispatch('central')
        self.assertEqual(sent,[]);self.assertTrue(runtime.unknown)
        self.assertEqual(graph.pair._consumers,1)

    def test_policy_replaced_after_begin_is_refused_before_claim(self):
        owner,runtime,graph=self.prepare()
        owner._reconstruction_policy=dataclasses.replace(owner._reconstruction_policy)
        with patch.object(m,'_popen') as native:
            with self.assertRaises(m.B3Error):runtime.claim('central',c.Operation.INITIAL_OBSERVE)
            native.assert_not_called()
        self.assertEqual(runtime.phases,[])

    def test_original_policy_pin_mutation_is_refused_before_claim(self):
        owner,runtime,graph=self.prepare()
        object.__setattr__(owner._reconstruction_policy,'program_sha256','e'*64)
        with patch.object(m,'_popen') as native:
            with self.assertRaises(m.B3Error):runtime.claim('central',c.Operation.INITIAL_OBSERVE)
            native.assert_not_called()
        self.assertEqual(runtime.phases,[])

    def test_original_owner_initial_observe_to_actual_roles_commit(self):
        owner,runtime,graph=self.prepare();sent,calls=self.transport(owner)
        observed=runtime._dispatch('central')
        self.assertTrue(runtime.committed(observed))
        roles=runtime._dispatch('central')
        self.assertTrue(runtime.committed(roles))
        self.assertEqual([p.operation for p in runtime.phases],[c.Operation.INITIAL_OBSERVE,c.Operation.ROLES_APPLY])
        self.assertEqual([pg.commits for pg in self.connections],[0,1])
        self.assertTrue(all(pg.closed for pg in self.connections))
        self.assertEqual(len(sent),2)
        proof=runtime._role_proof(roles);proof.verify()
        owner._audit_execution()
        self.assertEqual(graph.pair._consumers,1)
        self.assertNotIn(b'request_frame_hex',b''.join(x.raw for x in runtime.journal.records))

    def test_facade_resolves_same_original_owner(self):
        from platform_service.media_backup_b3.reconstruction import restore_roles
        owner,runtime,graph=self.prepare();self.transport(owner)
        proof=restore_roles(owner._targets['central'].original_handle,owner._input,'central');proof.verify()
        self.assertIs(proof._runtime,runtime)
        with self.assertRaises(m.B3Error):restore_roles(owner._targets['central'].original_handle,owner._input,'central')

    def test_mutable_committed_label_never_advances_phase(self):
        owner,runtime,graph=self.prepare()
        phase=runtime.claim('central',c.Operation.INITIAL_OBSERVE);phase.state='COMMITTED'
        with self.assertRaises(m.B3Error):runtime.next_operation('central')
        with self.assertRaises(m.B3Error):owner._close_role_plan_consumer()
        self.assertEqual(graph.pair._consumers,1)

    def test_partial_stdin_preserves_single_request(self):
        owner,runtime,graph=self.prepare();sent,calls=self.transport(owner)
        write=owner._io_write;owner._io_write=lambda fd,raw:write(fd,raw[:2048])
        phase=runtime._dispatch('central')
        self.assertEqual(sent,[phase.request_bytes]);self.assertTrue(runtime.committed(phase))

    def test_commit_ack_loss_is_absorbing(self):
        owner,runtime,graph=self.prepare();self.transport(owner)
        runtime._dispatch('central');self.lost_commit=True
        with self.assertRaises(m.B3Error):runtime._dispatch('central')
        self.assertEqual(self.connections[-1].commits,1)
        self.assertFalse(self.connections[-1].closed)
        with self.assertRaises(m.B3Error):runtime._dispatch('central')
        self.assertEqual(len(self.connections),2)
        with self.assertRaises(m.B3Error):owner._close_role_plan_consumer()
        self.assertEqual(graph.pair._consumers,1)

    def test_result_tail_fails_without_resend(self):
        owner,runtime,graph=self.prepare()
        sent,calls=self.transport(owner,lambda a,s,out:out+b'x' if s.operation=='worker-start' else out)
        with self.assertRaises(m.B3Error):runtime._dispatch('central')
        self.assertEqual(len(sent),1)
        self.assertTrue(runtime.unknown)
        with self.assertRaises(m.B3Error):runtime._dispatch('central')
        self.assertEqual(graph.pair._consumers,1)

    def test_constructor_failure_retains_original_native_slot(self):
        owner,runtime,graph=self.prepare()
        class Failed:
            def __init__(self,*args,**kwargs):raise OSError('synthetic constructor')
            def poll(self):return None
        with patch.object(m,'_popen',Failed):
            with self.assertRaises(m.B3Error):runtime._dispatch('central')
        phase=runtime.phases[0];slot=phase.operations[0]
        self.assertIs(owner._native_originals[-1][1],slot.process)
        self.assertEqual(phase.state,'UNKNOWN');self.assertEqual(graph.pair._consumers,1)
        # Synthetic teardown only, not product release evidence.
        for pair in slot.fds:
            for fd in pair:
                if (id(slot),fd) not in owner._native_closed:os.close(fd)

    def test_result_eof_truncation_fails_closed(self):
        owner,runtime,graph=self.prepare()
        sent,calls=self.transport(owner,lambda a,s,out:out[:-1] if s.operation=='worker-start' else out)
        with self.assertRaises(m.B3Error):runtime._dispatch('central')
        self.assertTrue(runtime.unknown);self.assertEqual(len(sent),1)
        with self.assertRaises(m.B3Error):owner._close_role_plan_consumer()
        self.assertEqual(graph.pair._consumers,1)

    def test_result_identity_mismatch_fails_closed(self):
        import json
        owner,runtime,graph=self.prepare()
        def fault(a,slot,out):
            if slot.operation=='worker-start':
                value=json.loads(out[4:]);value['session']['after'][2]=11
                out=c.bounded_frame(value,c.RESULT_LIMIT)
            return out
        sent,calls=self.transport(owner,fault)
        with self.assertRaises(m.B3Error):runtime._dispatch('central')
        self.assertTrue(runtime.unknown);self.assertEqual(len(sent),1)
        self.assertEqual(graph.pair._consumers,1)

    def test_final_fsync_unknown_never_mints_proof(self):
        owner,runtime,graph=self.prepare();self.transport(owner)
        runtime._dispatch('central')
        original=m._fsync
        def fsync(fd):
            if any(record.slot.fd==fd and record.name=='reconstruction-002-committed.json' for record in runtime.journal.records):
                raise OSError('synthetic fsync unknown')
            return original(fd)
        with patch.object(m,'_fsync',fsync):
            with self.assertRaises(m.B3Error):runtime._dispatch('central')
        phase=runtime.phases[-1]
        self.assertEqual(self.connections[-1].commits,1)
        with self.assertRaises(m.B3Error):runtime._role_proof(phase)
        with self.assertRaises(m.B3Error):runtime._dispatch('central')
        self.assertEqual(graph.pair._consumers,1)

    def test_session_close_unknown_never_replays_commit(self):
        owner,runtime,graph=self.prepare();self.transport(owner)
        runtime._dispatch('central');self.lost_close=True
        with self.assertRaises(m.B3Error):runtime._dispatch('central')
        self.assertEqual(self.connections[-1].commits,1)
        self.assertEqual(worker._SESSIONS[-1].state,'UNKNOWN')
        with self.assertRaises(m.B3Error):runtime._dispatch('central')
        self.assertEqual(len(self.connections),2);self.assertEqual(graph.pair._consumers,1)

    def test_new_phase_gets_separate_original_sixty_second_anchor(self):
        owner,runtime,graph=self.prepare();self.transport(owner)
        phase=runtime._dispatch('central');first=phase.main_start_anchor
        self.now[0]=first+61
        second=runtime._dispatch('central')
        self.assertEqual(second.main_start_anchor,first+61)
        self.assertEqual(second.acceptance_deadline,min(owner._deadline,first+121))
        with self.assertRaises(m.B3Error):runtime.start_anchor(second)

    def test_final_fsync_elapsed_time_cannot_commit_expired_phase(self):
        owner,runtime,graph=self.prepare();self.transport(owner)
        original=m._fsync
        def fsync(fd):
            original(fd)
            if any(record.slot.fd==fd and record.name=='reconstruction-001-committed.json' for record in runtime.journal.records):
                self.now[0]=runtime.phases[0].acceptance_deadline
        with patch.object(m,'_fsync',fsync):
            with self.assertRaises(m.B3Error):runtime._dispatch('central')
        self.assertFalse(runtime.committed(runtime.phases[0]));self.assertTrue(runtime.unknown)
        self.assertEqual(graph.pair._consumers,1)

    def test_native_fd_close_unknown_is_not_retried(self):
        owner,runtime,graph=self.prepare();self.transport(owner)
        attempted=[]
        def close(fd):attempted.append(fd);raise OSError('synthetic native close unknown')
        with patch.object(m,'_close',close):
            with self.assertRaises(m.B3Error):runtime._dispatch('central')
            with self.assertRaises(m.B3Error):runtime._dispatch('central')
        self.assertEqual(len(attempted),1)
        self.assertTrue(owner._native_close_attempted)
        self.assertTrue(runtime.unknown);self.assertEqual(graph.pair._consumers,1)

    def test_known_cancel_after_roles_closes_without_success_or_replay(self):
        owner,runtime,graph=self.prepare();self.transport(owner)
        runtime._dispatch('central');phase=runtime._dispatch('central')
        proof=runtime._role_proof(phase)
        owner._event.set();owner._close_role_plan_consumer()
        self.assertTrue(runtime.stopped and runtime.closed and runtime.stop_record.durable)
        self.assertEqual(graph.pair._consumers,0)
        with self.assertRaises(m.B3Error):proof.verify()
        with self.assertRaises(m.B3Error):runtime._dispatch('central')
        owner.close();self.assertTrue(owner._closed)
        self.assertTrue(owner.snapshot()['execution_unknown'])

    def test_known_expiry_after_roles_closes_original_host_refs(self):
        owner,runtime,graph=self.prepare();self.transport(owner)
        runtime._dispatch('central');runtime._dispatch('central')
        self.now[0]=owner._deadline
        owner._close_role_plan_consumer();owner.close()
        self.assertTrue(runtime.stopped and runtime.stop_record.durable and owner._closed)
        self.assertEqual(graph.pair._consumers,0)
