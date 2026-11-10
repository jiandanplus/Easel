"""Task1 source custody tests: synthetic FDs and the concrete factory fake only."""
import dataclasses
import hashlib
import importlib
import os
import pickle
import unittest
from unittest.mock import patch
import test_media_backup_b3_target_factory as factory_helpers

try:
    m = importlib.import_module("platform_service.media_backup_b3.execution")
except ModuleNotFoundError:
    m = None

class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(m, "original-producer execution custody foundation is required")
        self.case = factory_helpers.FactoryTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.close_synthetic_execution_fds)
        self.policy = m.RootExecutionPolicy(
            m.WorkerPins("sha256:"+"a"*64, "arm64", *("b"*64 for _ in range(6)),
                         ("PATH=/usr/local/bin:/usr/bin:/bin", "LANG=C.UTF-8", "GPG_KEY="+"A"*40,
                          "PYTHON_VERSION=3.12.13", "PYTHON_SHA256="+"b"*64)),
            m.TargetProbePins(factory_helpers.m.IMAGE_ID, "c"*64, "d"*64,
                tuple((r,"/usr/bin/"+r,"e"*64) for r in ("sha256sum","stat","readlink","cat")),
                "f"*64, "1"*64),
            m.ExecutionLimits(10**9, 256*1024**2, 256*1024**2, 64, 16*1024**2, 65536, 1024**2, 65536))
        self.now = [self.case.policy._source._started_at+1]
        self.clock = patch.object(m,"_monotonic",lambda: self.now[0])
        self.clock.start(); self.addCleanup(self.clock.stop)
    def close_synthetic_execution_fds(self):
        # Explicit test teardown only: never product UNKNOWN release evidence.
        if m is None:return
        for owner in m._OWNERS.values():
            if getattr(owner,"_input",None) is self.case.input:
                for slot in owner._fd_slots:
                    if slot.fd is not None and not slot.closed:
                        try:os.close(slot.fd)
                        except OSError:pass
                        slot.closed=True
    def owner(self):
        return m.RootExecutionOwner(self.case.factory(),self.case.input,self.policy)
    def test_original_producer_return_is_retained_with_separate_ledger(self):
        owner=self.owner(); handle=owner.create_target("central")
        self.assertIs(owner._targets["central"].original_handle,handle)
        self.assertIs(owner._targets["central"].original_attempt,owner._factory._attempts["central"])
        self.assertEqual(len(handle._attempt._records),8)
        self.assertEqual(handle._attempt._sequence,8)
        self.assertTrue((self.case.case.root/"execution-owner.once").is_file())
        self.assertFalse((self.case.case.root/"execution-central.once").exists())
        self.assertTrue(callable(getattr(owner,"attest",None)),"public bounded ATTEST is required")

    def pair(self,owner,order=("central","identity")):
        return [owner.create_target(kind) for kind in order]

    def test_role_admission_snapshot_separate_and_default_has_no_claim(self):
        owner=self.owner();self.pair(owner)
        state=owner.role_admission_snapshot()
        self.assertEqual(state['claims'],0)
        self.assertFalse(state['consumer_held'])
        self.assertFalse(state['admitted'])
        self.assertTrue(state['data_only'])
        self.assertFalse(state['native_verified'])
        self.assertEqual(owner.snapshot()['execution_phase_claims'],0)

    def admission_pair(self):
        from platform_service.media_backup_b2 import reconstruction_capture as capture
        pair=capture.OwnedSourceCapture(capture._SEAL)
        pair._lock=__import__('threading').RLock();pair._consumers=0
        pair._consumer=None;pair._consumer_ever=False;pair._publication_closed=False
        pair._inputs=__import__('types').SimpleNamespace(companion=pair)
        pair._verify_bindings=lambda:None;pair._verify_publication=lambda:None;pair._validate_terminal_success=lambda **kw:None
        return pair

    def test_role_admission_original_owner_retains_one_consumer_and_expired_cleanup(self):
        from types import SimpleNamespace as NS
        from dataclasses import dataclass
        @dataclass(frozen=True)
        class Plan: value:int=1
        owner=self.owner();self.pair(owner);pair=self.admission_pair()
        owner._role_custody=lambda slot:None
        calls=len(self.case.calls);phase_claims=owner.snapshot()['execution_phase_claims']
        from platform_service.media_backup_b3.roles import _hash
        module=NS(build_role_database_pair=lambda **kw:(Plan(),Plan()),_hash=_hash)
        with patch.dict(__import__('sys').modules,{'platform_service.media_backup_b3.roles':module}),patch.object(owner,'_role_join_data',return_value={}):
            summary=owner._admit_role_plan(pair,source_owners=(),authorizations=())
        self.assertTrue(summary['admitted']);self.assertTrue(summary['consumer_held'])
        self.assertIs(pair._consumer[0],owner)
        with self.assertRaises(m.B3Error):owner._admit_role_plan(pair,source_owners=(),authorizations=())
        self.assertTrue(owner.role_admission_snapshot()['admitted'])
        self.assertEqual(phase_claims,owner.snapshot()['execution_phase_claims'])
        self.assertEqual(calls,len(self.case.calls))
        self.now[0]=owner._deadline
        owner._close_role_plan_consumer()
        self.assertEqual(pair._consumers,0);self.assertIsNotNone(owner._role_slot.plans)
        self.assertTrue(owner.role_admission_snapshot()['closed'])
        owner.close()

    def test_role_admission_data_rejection_explicit_close_and_custody_unknown_retention(self):
        from types import SimpleNamespace as NS
        owner=self.owner();self.pair(owner);pair=self.admission_pair()
        owner._role_custody=lambda slot:None
        module=NS(build_role_database_pair=lambda **kw:None)
        with patch.dict(__import__('sys').modules,{'platform_service.media_backup_b3.roles':module}),patch.object(owner,'_role_join_data',side_effect=m.B3Error()):
            with self.assertRaises(m.B3Error):owner._admit_role_plan(pair,source_owners=(),authorizations=())
        self.assertEqual(owner._role_slot.state,'REJECTED');self.assertIsNone(owner._role_slot.plans)
        self.assertEqual(pair._consumers,1)
        owner._close_role_plan_consumer();self.assertEqual(pair._consumers,0)

    def test_complete_matching_capture_preflight_two_plan_admission_without_native(self):
        from media_backup_b3_replay_fixture import matching_graph
        graph=matching_graph(self)
        owner=self.owner();self.pair(owner)
        before=len(self.case.calls)
        # Original capture phase expires; Root job/event remain valid.
        self.now[0]=graph.phase_expired_at
        result=owner._admit_role_plan(graph.pair,source_owners=graph.owners,authorizations=graph.authorizations)
        self.assertTrue(result['admitted']);self.assertTrue(result['consumer_held'])
        self.assertEqual(len(owner._role_slot.plans),2)
        self.assertEqual([p.kind for p in owner._role_slot.plans],['central','identity'])
        self.assertEqual(before,len(self.case.calls))
        self.assertIs(owner._role_slot.origins,graph.stage._origin)
        self.assertIsNot(graph.pair._intent,graph.stage._origin[0]._supplied)
        self.assertEqual(owner._role_slot.renderings,(('UTC','ISO, MDY'),('UTC','ISO, MDY')))
        owner._close_role_plan_consumer()
        self.assertEqual(graph.pair._consumers,0)
        owner.close()

    def test_complete_optional_graph_external_ordinary_grantor_closure_admits(self):
        from media_backup_b3_replay_fixture import matching_graph
        graph=matching_graph(self,with_graph=True);owner=self.owner();self.pair(owner)
        before=len(self.case.calls)
        result=owner._admit_role_plan(graph.pair,source_owners=graph.owners,authorizations=graph.authorizations)
        self.assertTrue(result['admitted'])
        for plan in owner._role_slot.plans:
            self.assertEqual(len(plan.memberships),2)
            self.assertIsNotNone(plan.handover)
            self.assertEqual(len(plan.source_graph_edges),2)
        self.assertEqual(before,len(self.case.calls))
        self.assertEqual([o.roles for o in graph.owners],[{'owner':'owner'},{'owner':'owner'}])
        owner._close_role_plan_consumer();owner.close()

    def test_original_ready_record_replacement_is_refused_without_native_observation(self):
        owner=self.owner();self.pair(owner)
        attempt=owner._targets['central'].original_attempt
        row=attempt._records[0];attempt._records[0]=tuple(list(row))
        calls=len(self.case.calls)
        with self.assertRaises(m.B3Error):owner._check_original_bindings()
        self.assertEqual(calls,len(self.case.calls))

    def test_complete_graph_changed_reference_retains_unknown_consumer(self):
        from media_backup_b3_replay_fixture import matching_graph
        from uuid import uuid4
        graph=matching_graph(self);owner=self.owner();self.pair(owner)
        graph.pair._reference=dataclasses.replace(graph.pair._reference,backup_id=str(uuid4()))
        calls=len(self.case.calls)
        with self.assertRaises(m.B3Error):owner._admit_role_plan(graph.pair,source_owners=graph.owners,authorizations=graph.authorizations)
        self.assertEqual(owner._role_slot.state,'UNKNOWN');self.assertIsNone(owner._role_slot.plans)
        self.assertEqual(graph.pair._consumers,1)
        self.assertEqual(calls,len(self.case.calls))
        with self.assertRaises(m.B3Error):owner._close_role_plan_consumer()
        self.assertEqual(graph.pair._consumers,1)

    def test_complete_graph_custody_uncertainty_retains_consumer_and_blocks_disposition(self):
        from media_backup_b3_replay_fixture import matching_graph
        graph=matching_graph(self);owner=self.owner();self.pair(owner)
        with patch.object(graph.pair,'_verify_publication',side_effect=OSError('synthetic custody ambiguity')):
            with self.assertRaises(OSError):owner._admit_role_plan(graph.pair,source_owners=graph.owners,authorizations=graph.authorizations)
        self.assertEqual(owner._role_slot.state,'UNKNOWN');self.assertEqual(graph.pair._consumers,1)
        with self.assertRaises(m.B3Error):owner._close_role_plan_consumer()
        with self.assertRaises(m.B3Error):owner._retain_disposition('a'*64,'b'*64)
        with self.assertRaises(m.B3Error):owner.close()
        self.assertEqual(graph.pair._consumers,1)

    def test_fix_entry_original_sample_drift_and_unknown_keep_consumer(self):
        from media_backup_b3_replay_fixture import matching_graph
        from platform_service.media_backup_b2.reconstruction_context import SourcePhase
        for change in ('copy','backend','snapshot','source_unknown','graph_copy','inplace_backend'):
            case=ExecutionTests();case.setUp()
            try:
                graph=matching_graph(case,with_graph=change=='graph_copy');owner=case.owner();case.pair(owner)
                producer=graph.pair._producers[0];sample=producer._samples[SourcePhase.BEFORE]
                if change=='source_unknown':graph.owners[0].unknown=True
                elif change in ('copy','graph_copy'):producer._samples[SourcePhase.BEFORE]=dataclasses.replace(sample)
                elif change=='inplace_backend':object.__setattr__(sample,'identity',dataclasses.replace(sample.identity,backend_pid=sample.identity.backend_pid+1))
                else:
                    identity=dataclasses.replace(sample.identity,**({'backend_pid':sample.identity.backend_pid+1} if change=='backend' else {'snapshot_text':'10:21:11,15'}))
                    producer._samples[SourcePhase.BEFORE]=dataclasses.replace(sample,identity=identity)
                with case.assertRaises(Exception):owner._admit_role_plan(graph.pair,source_owners=graph.owners,authorizations=graph.authorizations)
                case.assertEqual(owner._role_slot.state,'UNKNOWN',change)
                case.assertTrue(owner._role_slot.consumer_held);case.assertEqual(graph.pair._consumers,1)
                with case.assertRaises(m.B3Error):owner._close_role_plan_consumer()
                case.assertEqual(graph.pair._consumers,1)
            finally:case.doCleanups()

    def test_fix_close_rechecks_original_reference_and_source_unknown(self):
        from media_backup_b3_replay_fixture import matching_graph
        for change in ('reference','source_unknown','sample_copy'):
            case=ExecutionTests();case.setUp()
            try:
                graph=matching_graph(case);owner=case.owner();case.pair(owner)
                owner._admit_role_plan(graph.pair,source_owners=graph.owners,authorizations=graph.authorizations)
                if change=='reference':graph.pair._reference=dataclasses.replace(graph.pair._reference)
                elif change=='source_unknown':graph.owners[0].unknown=True
                else:
                    producer=graph.pair._producers[0];phase=next(iter(producer._samples))
                    producer._samples[phase]=dataclasses.replace(producer._samples[phase])
                with case.assertRaises(Exception):owner._close_role_plan_consumer()
                case.assertEqual(owner._role_slot.state,'UNKNOWN');case.assertEqual(graph.pair._consumers,1)
            finally:case.doCleanups()

    def test_fix_expired_entry_known_rejection_explicitly_closes(self):
        from media_backup_b3_replay_fixture import matching_graph
        graph=matching_graph(self);owner=self.owner();self.pair(owner)
        self.now[0]=owner._deadline
        with self.assertRaises(m.B3Error):owner._admit_role_plan(graph.pair,source_owners=graph.owners,authorizations=graph.authorizations)
        self.assertEqual(owner._role_slot.state,'REJECTED');self.assertEqual(graph.pair._consumers,1)
        owner._close_role_plan_consumer();self.assertEqual(graph.pair._consumers,0)

    def test_fix_cancelled_known_consumer_closes_after_deadline(self):
        from media_backup_b3_replay_fixture import matching_graph
        graph=matching_graph(self);owner=self.owner();self.pair(owner)
        owner._admit_role_plan(graph.pair,source_owners=graph.owners,authorizations=graph.authorizations)
        self.now[0]=owner._deadline;owner._event.set()
        owner._close_role_plan_consumer()
        self.assertEqual(graph.pair._consumers,0);self.assertEqual(owner._role_slot.state,'CLOSED')

    def test_fix_hash_interval_expiry_and_cancellation_refuse_atomic_install(self):
        from media_backup_b3_replay_fixture import matching_graph
        from platform_service.media_backup_b3 import roles
        for change in ('expiry','cancel'):
            case=ExecutionTests();case.setUp()
            try:
                graph=matching_graph(case);owner=case.owner();case.pair(owner)
                original=roles._hash
                def hash_and_expire(value):
                    pin=original(value)
                    if type(value) is tuple and len(value)==2 and type(value[0]) is roles.RoleDatabasePlan:
                        if change=='expiry':case.now[0]=owner._deadline
                        else:owner._event.set()
                    return pin
                with patch.object(roles,'_hash',side_effect=hash_and_expire):
                    with case.assertRaises(m.B3Error):owner._admit_role_plan(graph.pair,source_owners=graph.owners,authorizations=graph.authorizations)
                case.assertEqual(owner._role_slot.state,'REJECTED');case.assertIsNone(owner._role_slot.plans)
                case.assertEqual(graph.pair._consumers,1)
                owner._close_role_plan_consumer();case.assertEqual(graph.pair._consumers,0)
            finally:case.doCleanups()

    def test_fix_paired_plan_hash_streams_repeated_witness_on_admit_and_close(self):
        from media_backup_b3_replay_fixture import matching_graph
        from platform_service.media_backup_b3 import roles
        graph=matching_graph(self,with_graph=True);owner=self.owner();self.pair(owner)
        original=roles.build_role_database_pair
        plans=[]
        def build(**kwargs):
            result=original(**kwargs)
            result=tuple(dataclasses.replace(p,memberships=tuple(dataclasses.replace(g,witness=g.witness if g.witness==('BOOTSTRAP_OID10',) else g.witness*3) for g in p.memberships)) for p in result)
            plans.extend(result)
            return result
        with patch.object(roles,'build_role_database_pair',side_effect=build),patch.object(roles,'_hash',wraps=roles._hash) as hashed:
            original_asdict=dataclasses.asdict
            def plan_spy(value,*args,**kwargs):
                if type(value) is roles.RoleDatabasePlan:raise AssertionError('whole plan tree')
                return original_asdict(value,*args,**kwargs)
            with patch.object(dataclasses,'asdict',side_effect=plan_spy):
                owner._admit_role_plan(graph.pair,source_owners=graph.owners,authorizations=graph.authorizations)
                owner._close_role_plan_consumer()
            self.assertGreaterEqual(hashed.call_count,2)
        self.assertEqual(owner._role_slot.plan_pin,m.digest(tuple(dataclasses.asdict(p) for p in plans)))

    def test_policy_exact_types_closed_tools_and_environment(self):
        for record in (self.policy,self.policy.worker,self.policy.target_probe,self.policy.limits):
            self.assertNotIn("sha256:",repr(record))
            with self.assertRaises(dataclasses.FrozenInstanceError):record.extra=True
        invalid=(
            (self.policy.limits,"cpu_nanos",True),
            (self.policy.limits,"memory_swap_bytes",1),
            (self.policy.worker,"architecture",object()),
            (self.policy.worker,"artifact_sha256","A"*64),
            (self.policy.worker,"public_env",("PATH=/bin",)),
            (self.policy.worker,"public_env",self.policy.worker.public_env+("HOME=/tmp",)),
            (self.policy.worker,"public_env",tuple(reversed(self.policy.worker.public_env))),
            (self.policy.target_probe,"tools",tuple(reversed(self.policy.target_probe.tools))),
            (self.policy.target_probe,"tools",((object(),"/usr/bin/sha256sum","a"*64),)+self.policy.target_probe.tools[1:]),
            (self.policy.target_probe,"image_id","sha256:"+"0"*64),
        )
        for record,field,value in invalid:
            with self.subTest(field=field,value=type(value).__name__),self.assertRaises(m.B3Error):
                dataclasses.replace(record,**{field:value})

    def test_import_has_no_io_and_no_adoption(self):
        with patch.object(m.os,"open",side_effect=AssertionError("import IO")),patch.object(m.os,"dup",side_effect=AssertionError("import dup")):
            # Execute without replacing live module/class registry identities.
            namespace=dict(__name__=m.__name__,__package__=m.__package__)
            exec(compile(__import__("pathlib").Path(m.__file__).read_text(),m.__file__,"exec"),namespace)
        owner=self.owner()
        for action in (lambda:pickle.dumps(owner),lambda:__import__("copy").copy(owner),lambda:__import__("copy").deepcopy(owner)):
            with self.assertRaises(TypeError):action()

    def test_reverse_production_order_and_clean_close_preserves_originals(self):
        owner=self.owner(); handles=self.pair(owner,("identity","central"))
        self.assertEqual(owner.snapshot()["execution_target_count"],2)
        originals=[self.case.policy._job_fd]+[h._attempt._job_pin for h in handles]
        owner.close();owner.close()
        self.assertTrue(owner._closed)
        self.assertFalse(owner.snapshot()["execution_unknown"])
        for fd in originals:os.fstat(fd)
        self.assertFalse(self.case.input._closed)
        self.assertTrue(all(not h._closed for h in handles))
        with self.assertRaises(m.B3Error):m.RootExecutionOwner(self.case.factory(),self.case.input,self.policy)

    def test_used_factory_and_foreign_input_rejected_without_commands(self):
        factory=self.case.factory();factory.create(self.case.input,"central");n=len(self.case.calls)
        with self.assertRaises(m.B3Error):m.RootExecutionOwner(factory,self.case.input,self.policy)
        self.assertEqual(n,len(self.case.calls))
        with self.assertRaises(m.B3Error):m.RootExecutionOwner(factory,object(),self.policy)

    def test_alias_contender_does_not_poison_original(self):
        owner=self.owner(); alias=os.dup(self.case.policy._job_fd);self.addCleanup(os.close,alias)
        other_policy=dataclasses.replace(self.case.policy,_job_fd=alias)
        other=factory_helpers.m.RootTargetFactory(other_policy,self.case.tools)
        with self.assertRaises(m.B3Error):m.RootExecutionOwner(other,self.case.input,self.policy)
        self.assertFalse(owner._unknown)
        owner.create_target("central")
        self.assertIs(m._OWNERS[owner._job],owner)

    def test_original_handle_replacement_on_same_attempt_is_rejected(self):
        owner=self.owner();h=owner.create_target("central")
        replacement=factory_helpers.m.TargetHandle(factory_helpers.m._SEAL,h._attempt)
        owner._targets["central"].original_handle=replacement
        n=len(self.case.calls)
        with self.assertRaises(m.B3Error):owner.create_target("identity")
        self.assertEqual(n,len(self.case.calls));self.assertTrue(owner._unknown)

    def test_replaced_original_bindings_fail_before_second_production(self):
        # Independent original jobs: one changed binding per case.
        for field in ("stage","stage_authority","parser_authority","contexts","event","policy","tools"):
            case=ExecutionTests("test_original_producer_return_is_retained_with_separate_ledger");case.setUp()
            try:
                owner=case.owner();owner.create_target("central");v=case.case.input
                if field=="stage":v._stage=object()
                elif field=="stage_authority":v._stage_authority=dataclasses.replace(v._stage_authority)
                elif field=="parser_authority":v._parser_authority=dataclasses.replace(v._parser_authority)
                elif field=="contexts":object.__setattr__(owner._authority,"contexts",tuple((k,dataclasses.replace(c)) for k,c in owner._contexts))
                elif field=="event":object.__setattr__(owner._authority,"cancellation",__import__("threading").Event())
                elif field=="policy":owner._policy=dataclasses.replace(owner._policy)
                else:owner._factory._tools=dataclasses.replace(owner._factory._tools)
                n=len(case.case.calls)
                with case.assertRaises(m.B3Error):owner.create_target("identity")
                case.assertEqual(n,len(case.case.calls));case.assertTrue(owner._unknown)
            finally:case.doCleanups()

    def test_producer_interruption_retains_original_attempt_no_handle_reconstruction(self):
        owner=self.owner()
        def stop(argv,result):raise KeyboardInterrupt("synthetic producer")
        self.case.fault=stop
        with self.assertRaises(KeyboardInterrupt):owner.create_target("central")
        slot=owner._targets["central"]
        self.assertIs(slot.original_attempt,owner._factory._attempts["central"])
        self.assertIsNone(slot.original_handle);self.assertTrue(owner._unknown)
        n=len(self.case.calls)
        with self.assertRaises(m.B3Error):owner.create_target("identity")
        self.assertEqual(n,len(self.case.calls))

    def test_fd_constructor_interruption_is_anchored_before_acquisition(self):
        factory=self.case.factory();job=os.fstat(self.case.policy._job_fd);key=(job.st_dev,job.st_ino)
        def interrupted(fd):
            original=m._OWNERS[key]
            self.assertIs(original._factory,factory)
            self.assertEqual(original._fd_slots[-1].operation,"job-duplicate")
            self.assertIsNone(original._fd_slots[-1].fd)
            raise KeyboardInterrupt("unobserved acquisition")
        with patch.object(m,"_dup",interrupted):
            with self.assertRaises(KeyboardInterrupt):m.RootExecutionOwner(factory,self.case.input,self.policy)
        owner=m._OWNERS[key];self.assertTrue(owner._unknown)
        self.assertTrue(owner.snapshot()["execution_observation_error"])
        with self.assertRaises(m.B3Error):owner.close()
        with self.assertRaises(m.B3Error):m.RootExecutionOwner(factory,self.case.input,self.policy)

    def test_phase_claim_burns_only_begin_and_keeps_factory_accounting(self):
        owner=self.owner();self.pair(owner);before=len(self.case.calls)
        attempt=owner._begin_attestation("central")
        self.assertIs(attempt,owner._attempts["central"])
        self.assertEqual(attempt.state,"CLAIMED")
        self.assertEqual(len(self.case.calls),before)
        self.assertTrue((self.case.case.root/"execution-central.once").exists())
        self.assertTrue(all(a._sequence==8 and len(a._records)==8 for a in owner._factory._attempts.values()))
        graph=owner.snapshot();self.assertEqual(graph["execution_phase_claims"],1)
        self.assertEqual(graph["execution_committed_count"],0);self.assertTrue(graph["execution_unknown"])
        with self.assertRaises(m.B3Error):owner._begin_attestation("central")
        with self.assertRaises(m.B3Error):owner.close()

    def test_phase_fsync_expiry_burns_claim_no_native_entry(self):
        owner=self.owner();self.pair(owner);before=len(self.case.calls)
        now=self.now;real=m._fsync
        def advance(fd):real(fd);now[0]=owner._deadline
        with patch.object(m,"_monotonic",lambda:now[0]),patch.object(m,"_fsync",advance):
            with self.assertRaises(m.B3Error):owner._begin_attestation("central")
        self.assertEqual(len(self.case.calls),before)
        self.assertTrue((self.case.case.root/"execution-central.once").exists())
        self.assertTrue(owner._unknown);self.assertIn("central",owner._attempts)

    def test_partial_writes_and_eintr_preserve_one_hash_chain(self):
        real=m._write;count=[0]
        def short(fd,raw):
            count[0]+=1
            if count[0]==1:raise InterruptedError()
            return real(fd,raw[:7])
        with patch.object(m,"_write",short):owner=self.owner()
        self.pair(owner)
        with patch.object(m,"_write",short):owner._begin_attestation("central")
        self.assertGreater(count[0],10)
        owner._ledger.verify()
        rows=owner._ledger.records
        self.assertEqual(len(rows),2);self.assertEqual(rows[1].previous,rows[0].sha256)
        for row in rows:
            self.assertLessEqual(len(row.raw),8192)
            self.assertEqual(hashlib.sha256(os.pread(row.slot.fd,8193,0)).hexdigest(),row.sha256)

    def test_exclusive_owner_file_replay_is_unknown_and_keeps_open_intent(self):
        (self.case.case.root/"execution-owner.once").write_bytes(b"preexisting")
        os.chmod(self.case.case.root/"execution-owner.once",0o600)
        with self.assertRaises(FileExistsError):self.owner()
        info=os.fstat(self.case.policy._job_fd);owner=m._OWNERS[(info.st_dev,info.st_ino)]
        self.assertTrue(owner._unknown);self.assertIsNone(owner._ledger.records[0].slot.fd)
        self.assertTrue(owner.snapshot()["execution_unknown"])

    def test_journal_tamper_and_fsync_failures_are_absorbing(self):
        for fault in ("hash","mode","file_fsync","directory_fsync"):
            case=ExecutionTests("test_original_producer_return_is_retained_with_separate_ledger");case.setUp()
            try:
                owner=case.owner();before=len(case.case.calls)
                row=owner._ledger.records[0]
                if fault=="hash":os.pwrite(row.slot.fd,b"X",0)
                elif fault=="mode":os.fchmod(row.slot.fd,0o644)
                if fault in ("hash","mode"):
                    with case.assertRaises(m.B3Error):owner.create_target("central")
                else:
                    case.pair(owner);before=len(case.case.calls);real=m._fsync
                    def fail(fd):
                        if (fault=="directory_fsync") == (fd==owner._job_slot.fd):raise OSError("synthetic fsync")
                        return real(fd)
                    with patch.object(m,"_fsync",fail),case.assertRaises(OSError):owner._begin_attestation("central")
                case.assertTrue(owner._unknown);case.assertEqual(before,len(case.case.calls))
                with case.assertRaises(m.B3Error):owner.close()
            finally:case.doCleanups()

    def test_cancel_and_original_clock_forbid_production(self):
        owner=self.owner();self.case.policy._cancellation.set()
        with self.assertRaises(m.B3Error):owner.create_target("central")
        self.assertTrue(owner._unknown);self.assertFalse(self.case.calls)

    def test_expired_snapshot_no_verify_or_commands_clean_close_still_attempts_fds(self):
        owner=self.owner();self.pair(owner);n=len(self.case.calls)
        with patch.object(m,"_monotonic",lambda:owner._deadline),patch.object(m.VerifiedRestoreInput,"verify",side_effect=AssertionError("snapshot verify")),patch.object(m.TargetHandle,"verify",side_effect=AssertionError("target verify")):
            snapshot=owner.snapshot();owner.close();closed=owner.snapshot()
        self.assertEqual(n,len(self.case.calls));self.assertFalse(snapshot["execution_unknown"])
        self.assertFalse(closed["execution_unknown"]);self.assertTrue(owner._closed)
        self.assertTrue(all(s.closed for s in owner._fd_slots))

    def test_snapshot_and_close_detect_replaced_original_without_verifying(self):
        owner=self.owner();self.pair(owner);before=len(self.case.calls)
        object.__setattr__(owner._authority,"contexts",tuple((k,dataclasses.replace(c)) for k,c in owner._contexts))
        with patch.object(m.VerifiedRestoreInput,"verify",side_effect=AssertionError("observation verify")):
            graph=owner.snapshot()
            self.assertTrue(graph["execution_observation_error"])
            self.assertTrue(graph["execution_unknown"])
            with self.assertRaises(m.B3Error):owner.close()
        self.assertFalse(any(s.close_attempted for s in owner._fd_slots))
        self.assertEqual(before,len(self.case.calls))

    def test_snapshot_detects_original_target_unknown(self):
        owner=self.owner();self.pair(owner)
        owner._targets["central"].original_attempt._unknown=True
        self.assertTrue(owner.snapshot()["execution_unknown"])
        with self.assertRaises(m.B3Error):owner.close()
        self.assertFalse(any(s.close_attempted for s in owner._fd_slots))

    def test_journal_named_replacement_and_phase_exclusive_replay(self):
        owner=self.owner();self.pair(owner)
        path=self.case.case.root/"execution-central.once"
        path.write_bytes(b"original other claim");path.chmod(0o600)
        before=len(self.case.calls)
        with self.assertRaises(FileExistsError):owner._begin_attestation("central")
        self.assertTrue(owner._unknown);self.assertIn("central",owner._attempts)
        self.assertEqual(before,len(self.case.calls))

    def test_interrupted_record_write_does_not_continue_after_original_expiry(self):
        calls=[];real=m._write
        def interrupted(fd,raw):
            calls.append(fd)
            if len(calls)==1:
                self.now[0]=self.case.policy._source._deadline
                raise InterruptedError()
            return real(fd,raw)
        with patch.object(m,"_write",interrupted),self.assertRaises(m.B3Error):self.owner()
        self.assertEqual(len(calls),1)
        info=os.fstat(self.case.policy._job_fd);owner=m._OWNERS[(info.st_dev,info.st_ino)]
        self.assertTrue(owner._unknown);self.assertIsNotNone(owner._ledger.records[0].slot.fd)

    def test_nofollow_lineage_open_interruption_retains_provisional_slot(self):
        real=m._open;seen=[]
        def interrupted(name,flags,*args,**kwargs):
            info=os.fstat(self.case.policy._job_fd);owner=m._OWNERS[(info.st_dev,info.st_ino)]
            self.assertTrue(flags & os.O_NOFOLLOW)
            self.assertTrue(owner._fd_slots[-1].operation.startswith("lineage:"))
            self.assertIsNone(owner._fd_slots[-1].fd)
            seen.append(name)
            if len(seen)==2:raise KeyboardInterrupt("synthetic lineage constructor")
            return real(name,flags,*args,**kwargs)
        with patch.object(m,"_open",interrupted),self.assertRaises(KeyboardInterrupt):self.owner()
        info=os.fstat(self.case.policy._job_fd);owner=m._OWNERS[(info.st_dev,info.st_ino)]
        self.assertTrue(owner._unknown);self.assertIsNotNone(owner._lineage_fds[0].fd)
        self.assertIsNone(owner._lineage_fds[1].fd)
        self.assertFalse(any(s.close_attempted for s in owner._fd_slots))

    def test_named_record_replacement_detected_before_production(self):
        owner=self.owner();path=self.case.case.root/"execution-owner.once"
        path.rename(self.case.case.root/"saved-owner.once")
        path.write_bytes(owner._ledger.records[0].raw);path.chmod(0o600)
        with self.assertRaises(m.B3Error):owner.create_target("central")
        self.assertTrue(owner._unknown);self.assertFalse(self.case.calls)

    def test_original_clock_cannot_be_renewed_by_replacing_module_seam(self):
        owner=self.owner();self.now[0]=owner._deadline
        with patch.object(m,"_monotonic",lambda:owner._started_at+1):
            with self.assertRaises(m.B3Error):owner.create_target("central")
        self.assertTrue(owner._unknown);self.assertFalse(self.case.calls)

    def test_directory_fsync_expiry_is_checked_after_durability(self):
        owner=self.owner();self.pair(owner);now=self.now;real=m._fsync;before=len(self.case.calls)
        def expire_directory(fd):
            real(fd)
            if fd==owner._job_slot.fd:now[0]=owner._deadline
        with patch.object(m,"_monotonic",lambda:now[0]),patch.object(m,"_fsync",expire_directory):
            with self.assertRaises(m.B3Error):owner._begin_attestation("central")
        self.assertEqual(before,len(self.case.calls));self.assertTrue(owner._unknown)
        self.assertTrue((self.case.case.root/"execution-central.once").exists())

    def test_close_ambiguity_stays_unknown_and_never_retries(self):
        owner=self.owner();fd=owner._job_slot.fd;calls=[]
        def ambiguous(value):calls.append(value);raise OSError("unknown close")
        with patch.object(m,"_close",ambiguous),self.assertRaises(OSError):owner.close()
        self.assertTrue(owner._unknown);self.assertFalse(owner._closed)
        self.assertTrue(owner._fd_slots[-1].close_attempted)
        self.assertFalse(owner._fd_slots[-1].closed);os.fstat(fd)
        with patch.object(m,"_close",ambiguous),self.assertRaises(m.B3Error):owner.close()
        self.assertEqual(len(calls),1)
        graph=owner.snapshot();self.assertTrue(graph["execution_unknown"])
        self.assertFalse(graph["execution_disposition_retained"]);self.assertEqual(graph["execution_retained_owner_count"],0)

    def test_review_original_bindings_snapshot_and_direct_close_retain_custody(self):
        fields=("factory_event", "policy_event", "factory_closed", "factory_pin", "tool_pin",
                "factory_projection", "tool_projection", "source_identity", "source_job",
                "authority_job", "job_fd", "input_policy", "input_receipt", "input_closed", "stage_closed")
        for field in fields:
            for route in ("snapshot", "close"):
                with self.subTest(field=field,route=route):
                    case=ExecutionTests("test_original_producer_return_is_retained_with_separate_ledger");case.setUp()
                    try:
                        owner=case.owner();f=owner._factory
                        if field=="factory_event":f._event=__import__("threading").Event()
                        elif field=="policy_event":object.__setattr__(f._policy,"_cancellation",__import__("threading").Event())
                        elif field=="factory_closed":f.close()
                        elif field=="factory_pin":f._pin="0"*64
                        elif field=="tool_pin":f._tool_pin="0"*64
                        elif field=="factory_projection":object.__setattr__(f._policy,"_docker_sha256","0"*64)
                        elif field=="tool_projection":object.__setattr__(f._tools,"tools",tuple((r,p,"0"*64) for r,p,h in f._tools.tools))
                        elif field=="source_identity":object.__setattr__(f._policy,"_source",dataclasses.replace(f._policy._source))
                        elif field=="source_job":object.__setattr__(owner._source,"_restore_id","other_job")
                        elif field=="authority_job":object.__setattr__(owner._authority,"restore_id","other_job")
                        elif field=="job_fd":
                            alias=os.dup(owner._job_fd);case.addCleanup(os.close,alias)
                            object.__setattr__(f._policy,"_job_fd",alias)
                        elif field=="input_policy":owner._input._policy=dataclasses.replace(owner._input._policy)
                        elif field=="input_receipt":owner._input._receipt["changed"]=True
                        elif field=="input_closed":owner._input._closed=True
                        else:owner._stage._closed=True
                        before=len(case.case.calls)
                        def forbidden(*a,**kw):raise AssertionError("observation performed active work")
                        with patch.object(owner,"_clock",forbidden),patch.object(m.VerifiedRestoreInput,"verify",forbidden),patch.object(m.TargetHandle,"verify",forbidden),patch.object(factory_helpers.m,"_popen",forbidden),patch.object(m,"_open",forbidden):
                            if route=="snapshot":
                                graph=owner.snapshot()
                                case.assertTrue(graph["execution_unknown"])
                                case.assertTrue(graph["execution_observation_error"])
                            with case.assertRaises(m.B3Error):owner.close()
                        case.assertTrue(owner._unknown)
                        case.assertFalse(owner._closed)
                        case.assertFalse(any(slot.close_attempted for slot in owner._fd_slots))
                        case.assertEqual(before,len(case.case.calls))
                    finally:case.doCleanups()

    def test_review_known_closed_owner_audit_survives_normal_caller_cleanup(self):
        owner=self.owner();handles=self.pair(owner);owner.close()
        before=len(self.case.calls)
        self.case.input.close()
        for handle in handles:handle.close()
        owner._factory.close()
        self.now[0]=owner._deadline
        def forbidden(*args,**kwargs):raise AssertionError("closed audit performed active work")
        with patch.object(owner,"_clock",forbidden),patch.object(m.VerifiedRestoreInput,"verify",forbidden),patch.object(m.TargetHandle,"verify",forbidden),patch.object(factory_helpers.m,"_popen",forbidden),patch.object(m,"_open",forbidden),patch.object(m.os,"fstat",forbidden),patch.object(m.os,"stat",forbidden):
            graph=owner.snapshot();owner.close()
        self.assertFalse(graph["execution_unknown"])
        self.assertFalse(graph["execution_observation_error"])
        self.assertEqual(graph["execution_target_count"],2)
        self.assertTrue(owner._closed);self.assertEqual(before,len(self.case.calls))

    def test_review_worker_path_accepts_canonical_dotted_components(self):
        env=("PATH=/opt/python3.12/bin:/usr/bin",)+self.policy.worker.public_env[1:]
        try:worker=dataclasses.replace(self.policy.worker,public_env=env)
        except m.B3Error:self.fail("canonical dotted directory components must be accepted")
        self.assertEqual(worker.public_env,env)
        for path in ("relative/bin", "/bin::/usr/bin", "/opt/./bin", "/opt/../bin", "/bin/", "/bin\n"):
            with self.subTest(path=path),self.assertRaises(m.B3Error):
                dataclasses.replace(worker,public_env=("PATH="+path,)+env[1:])


    def test_attest_public_shape_rejects_unproduced_pair(self):
        import inspect
        self.assertTrue(hasattr(m.RootExecutionOwner,'attest'),'public original-target ATTEST is required')
        self.assertEqual(tuple(inspect.signature(m.RootExecutionOwner.attest).parameters),('self','kind'))
        owner=self.owner()
        with self.assertRaises(m.B3Error):owner.attest('central')
        self.assertTrue(owner._unknown)

    def test_attest_pipe_interruption_retains_original_intent(self):
        self.assertTrue(hasattr(m.RootExecutionOwner,'attest'),'public original-target ATTEST is required')
        owner=self.owner();self.pair(owner)
        def interrupted():
            attempt=owner._attempts['central']
            self.assertEqual(attempt.operations[-1].operation,'target-inspect')
            self.assertEqual(attempt.operations[-1].fds,[[]])
            raise KeyboardInterrupt('synthetic pipe interruption')
        with patch.object(m,'_pipe',interrupted),self.assertRaises(KeyboardInterrupt):owner.attest('central')
        self.assertTrue(owner._unknown)
        self.assertFalse(owner._attempts['central'].operations[0].constructed)

    def test_attest_rejects_rewrapped_producer_before_native_transport(self):
        owner=self.owner();handles=self.pair(owner)
        slot=owner._targets['central'];slot.original_handle=factory_helpers.m.TargetHandle(factory_helpers.m._SEAL,handles[0]._attempt)
        with patch.object(m,'_popen',side_effect=AssertionError('rewrapped native entry')):
            with self.assertRaises(m.B3Error):owner.attest('central')
        self.assertTrue(owner._unknown);self.assertEqual(len(owner._native_originals),0)


_SYNTHETIC_WORKER={'Id': '9999999999999999999999999999999999999999999999999999999999999999', 'Created': 'synthetic-Created', 'Path': '/opt/easel/bin/python', 'Args': ['-I', '-B', '-m', 'platform_service.media_backup_b3.execution_worker', 'attest_worker'], 'State': {'Status': 'running', 'Running': True, 'Paused': False, 'Restarting': False, 'OOMKilled': False, 'Dead': False, 'Pid': 51982, 'ExitCode': 0, 'Error': '', 'StartedAt': '2026-11-04T10:42:12.413588755Z', 'FinishedAt': '0001-01-01T00:00:00Z'}, 'Image': 'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'ResolvConfPath': 'synthetic-ResolvConfPath', 'HostnamePath': 'synthetic-HostnamePath', 'HostsPath': 'synthetic-HostsPath', 'LogPath': 'synthetic-LogPath', 'Name': '/synthetic', 'RestartCount': 0, 'Driver': 'overlayfs', 'Platform': 'linux', 'MountLabel': '', 'ProcessLabel': '', 'AppArmorProfile': '', 'ExecIDs': None, 'HostConfig': {'Binds': None, 'ContainerIDFile': '', 'LogConfig': {'Type': 'json-file', 'Config': {}}, 'NetworkMode': 'none', 'PortBindings': {}, 'RestartPolicy': {'Name': 'no', 'MaximumRetryCount': 0}, 'AutoRemove': False, 'VolumeDriver': '', 'VolumesFrom': None, 'ConsoleSize': [0, 0], 'CapAdd': None, 'CapDrop': ['ALL'], 'CgroupnsMode': 'private', 'Dns': None, 'DnsOptions': [], 'DnsSearch': [], 'ExtraHosts': None, 'GroupAdd': None, 'IpcMode': 'private', 'Cgroup': '', 'Links': None, 'OomScoreAdj': 0, 'PidMode': '', 'Privileged': False, 'PublishAllPorts': False, 'ReadonlyRootfs': True, 'SecurityOpt': ['no-new-privileges:true'], 'Tmpfs': {'/run/easel': 'rw,noexec,nosuid,nodev,size=16777216'}, 'UTSMode': '', 'UsernsMode': '', 'ShmSize': 67108864, 'Runtime': 'runc', 'Isolation': '', 'CpuShares': 0, 'Memory': 536870912, 'NanoCpus': 1000000000, 'CgroupParent': '', 'BlkioWeight': 0, 'BlkioWeightDevice': [], 'BlkioDeviceReadBps': [], 'BlkioDeviceWriteBps': [], 'BlkioDeviceReadIOps': [], 'BlkioDeviceWriteIOps': [], 'CpuPeriod': 0, 'CpuQuota': 0, 'CpuRealtimePeriod': 0, 'CpuRealtimeRuntime': 0, 'CpusetCpus': '', 'CpusetMems': '', 'Devices': [], 'DeviceCgroupRules': None, 'DeviceRequests': None, 'MemoryReservation': 0, 'MemorySwap': 536870912, 'MemorySwappiness': None, 'OomKillDisable': None, 'PidsLimit': 64, 'Ulimits': [], 'CpuCount': 0, 'CpuPercent': 0, 'IOMaximumIOps': 0, 'IOMaximumBandwidth': 0, 'MaskedPaths': ['/proc/acpi', '/proc/asound', '/proc/interrupts', '/proc/kcore', '/proc/keys', '/proc/latency_stats', '/proc/sched_debug', '/proc/scsi', '/proc/timer_list', '/proc/timer_stats', '/sys/devices/virtual/powercap', '/sys/firmware'], 'ReadonlyPaths': ['/proc/bus', '/proc/fs', '/proc/irq', '/proc/sys', '/proc/sysrq-trigger']}, 'Storage': {'RootFS': {'Snapshot': {'Name': 'overlayfs'}}}, 'Mounts': [], 'Config': {'Hostname': 'c92224b89ec5', 'Domainname': '', 'User': '10001:10001', 'AttachStdin': True, 'AttachStdout': True, 'AttachStderr': True, 'Tty': False, 'OpenStdin': True, 'StdinOnce': True, 'Env': ['PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', 'LANG=C.UTF-8', 'GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305', 'PYTHON_VERSION=3.12.13', 'PYTHON_SHA256=c08bc65a81971c1dd5783182826503369466c7e67374d1646519adf05207b684'], 'Cmd': ['attest_worker'], 'Image': 'sha256:81e6305be6381a0d12c0d7a7bc38f2d0fb2b922af306691aafdcba36045b16b4', 'Volumes': None, 'WorkingDir': '/opt/easel', 'Entrypoint': ['/opt/easel/bin/python', '-I', '-B', '-m', 'platform_service.media_backup_b3.execution_worker'], 'Labels': {'com.drawbookai.easel.purpose': 'b3-public-cold-worker'}, 'StopTimeout': 1}, 'NetworkSettings': {'Ports': {}, 'Networks': {}, 'SandboxID': '', 'SandboxKey': ''}, 'ImageManifestDescriptor': {'mediaType': 'application/vnd.oci.image.manifest.v1+json', 'digest': 'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'size': 1403, 'platform': {'architecture': 'arm64', 'os': 'linux'}}}

class FullExecutionTests(unittest.TestCase):
    setUp=ExecutionTests.setUp
    owner=ExecutionTests.owner
    pair=ExecutionTests.pair
    close_synthetic_execution_fds=ExecutionTests.close_synthetic_execution_fds
    # Inherited custody cases also run against the unchanged concrete factory.
    def transport(self,owner,fault=None):
        import copy,json,threading
        from test_media_backup_b3_execution_baseline import fixture
        from test_media_backup_b3_network_fixtures import network_evidence
        baseline=fixture();ns=baseline['before']['own_netns'];outer=self
        image=dict(Id=self.policy.worker.image_id,Architecture='arm64',Os='linux',
            Config=dict(Volumes=None,ExposedPorts=None,Env=list(self.policy.worker.public_env),
                Entrypoint=m._baseline._ENTRY,Cmd=['attest_worker'],User='10001:10001',WorkingDir='/opt/easel'),
            RootFS=dict(Type='layers',Layers=['sha256:'+'f'*64]))
        # Independent synthetic full image inspect pin, established before owner.
        self.assertEqual(owner._policy.worker.image_inspect_sha256,m.digest(image))
        workers={};sent=[];threads=[];calls=[]
        class Process:
            def __init__(self,argv,**kw):
                a=next(a for a in owner._attempts.values() if a.state!='COMMITTED')
                slot=a.operations[-1];op=slot.operation;calls.append((a.kind,op))
                self.code=None;self.slot=slot
                outer.assertIs(slot.process,self)
                fds=[os.dup(kw[k]) for k in ('stdin','stdout','stderr')]
                def work():
                    try:
                        if op=='worker-start':
                            request=bytearray()
                            while True:
                                chunk=os.read(fds[0],65536)
                                if not chunk:break
                                request.extend(chunk)
                            sent.append(bytes(request));parsed=m._contract.decode_worker_request(bytes(request));p=parsed.policy
                            e=dict(binding_sha256=m._contract._digest(m._contract._binding_data(p.binding)),
                                policy_sha256=m._contract._digest(m._contract._policy_data(p)),request_sha256=parsed.request_sha256,
                                manifest_sha256=m._contract._digest(m._contract.build_worker_manifest(p)),architecture=p.architecture,
                                artifact_sha256=p.worker_artifact_sha256,dependency_manifest_sha256=p.dependency_manifest_sha256,
                                entrypoint_sha256=p.entrypoint_sha256,pg_restore_sha256=p.pg_restore_sha256,
                                executable_sha256='c'*64,package_origins_sha256='d'*64,worker_netns=ns,
                                **network_evidence(),uid=10001,gid=10001,cap_effective=0,cap_permitted=0,
                                cap_inheritable=0,cap_ambient=0,no_new_privileges=True)
                            out=m._contract.encode_attestation(parsed,e)
                        elif op=='target-inspect':out=json.dumps([outer.case.fixture[a.kind][1]]).encode()
                        elif op=='image-inspect':out=json.dumps([image]).encode()
                        elif op=='worker-create':workers[a.kind]=str(8+(a.kind=='identity'))*64;out=(workers[a.kind]+'\n').encode()
                        elif op=='worker-inspect':
                            w=copy.deepcopy(_SYNTHETIC_WORKER);w['Id']=workers[a.kind];w['Name']='/'+a.worker_slot.name
                            w['Config'].update(Hostname=w['Id'][:12],Image=owner._policy.worker.image_id,
                                Env=list(owner._policy.worker.public_env),Labels=a.worker_slot.labels)
                            l=owner._policy.limits;w['HostConfig'].update(NetworkMode='container:'+a.target_slot.cid,
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
                        elif op=='probe-hashes':out=''.join(r[2]+'  '+r[1]+'\n' for r in owner._policy.target_probe.tools).encode()
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
                        offset=0
                        while offset<len(out):offset+=os.write(fds[1],out[offset:offset+8192])
                        self.code=0
                    except BaseException as exc:self.error=exc;self.code=17
                    finally:
                        for fd in fds:os.close(fd)
                t=threading.Thread(target=work,daemon=True);threads.append(t);t.start()
            def poll(self):return self.code
        self.addCleanup(lambda:[t.join(.2) for t in threads])
        self.addCleanup(lambda:[os.close(fd) for a in owner._attempts.values() for sl in a.operations for pair in sl.fds for fd in pair if (id(sl),fd) not in owner._native_closed])
        patcher=patch.object(m,'_popen',Process);patcher.start();self.addCleanup(patcher.stop)
        return sent,calls

    def full_owner(self):
        image=dict(Id=self.policy.worker.image_id,Architecture='arm64',Os='linux',
            Config=dict(Volumes=None,ExposedPorts=None,Env=list(self.policy.worker.public_env),
                Entrypoint=m._baseline._ENTRY,Cmd=['attest_worker'],User='10001:10001',WorkingDir='/opt/easel'),
            RootFS=dict(Type='layers',Layers=['sha256:'+'f'*64]))
        self.policy=dataclasses.replace(self.policy,worker=dataclasses.replace(self.policy.worker,image_inspect_sha256=m.digest(image)))
        owner=self.owner();self.pair(owner);return owner

    def test_complete_original_pair_custody_and_known_close(self):
        owner=self.full_owner();sent,calls=self.transport(owner)
        summaries=[owner.attest(kind) for kind in ('central','identity')]
        self.assertEqual(len(sent),2);self.assertEqual(len(summaries),2)
        self.assertEqual([k for k,op in calls if op=='worker-start'],['central','identity'])
        graph=owner.snapshot();self.assertFalse(graph['execution_unknown']);self.assertEqual(graph['execution_committed_count'],2)
        self.assertGreater(graph['execution_local_terminal_count'],0)
        self.assertEqual(graph['execution_remote_live_or_unobserved'],0)
        for a in owner._factory._attempts.values():self.assertEqual((a._sequence,len(a._records)),(8,8))
        owner.close();self.assertEqual(owner.snapshot(),graph)

    def test_namespace_drift_before_dispatch_burns_original(self):
        owner=self.full_owner()
        def fault(a,slot,out):
            if slot.operation=='probe-self-ns':return b'999 999\n'
            return out
        sent,calls=self.transport(owner,fault)
        with self.assertRaises(m.B3Error):owner.attest('central')
        self.assertFalse(sent);self.assertTrue(owner._unknown)
        self.assertNotIn(('central','worker-start'),calls)

    def assert_one_pair_pass_per_attest_boundary(self,kind):
        owner=self.full_owner();sent,calls=self.transport(owner)
        if kind=='identity':owner.attest('central')
        attempts=owner._factory._attempts
        before={k:sum(e['operation']=='metadata' for e in a._outputs) for k,a in attempts.items()}
        sent_before=len(sent)
        owner.attest(kind)
        self.assertEqual(owner._attempts[kind].state,'COMMITTED')
        self.assertFalse(owner._unknown);self.assertEqual(len(sent),sent_before+1)
        for k,a in attempts.items():
            with self.subTest(selected=kind,target=k):
                self.assertEqual(sum(e['operation']=='metadata' for e in a._outputs)-before[k],3)

    def test_central_attest_verifies_complete_pair_once_per_boundary(self):
        self.assert_one_pair_pass_per_attest_boundary('central')

    def test_identity_attest_verifies_complete_pair_once_per_boundary(self):
        self.assert_one_pair_pass_per_attest_boundary('identity')

    def assert_nonselected_peer_drift_blocks_dispatch(self,kind):
        import json
        owner=self.full_owner();peer='identity' if kind=='central' else 'central'
        peer_attempt=owner._factory._attempts[peer];drifted=[]
        def metadata_drift(argv,result):
            if peer_attempt._cid in argv and result[0]==0 and '-c' in argv and '/usr/lib/postgresql/16/bin/psql' in argv:
                row=json.loads(result[1]);row['system_identifier']='99999';drifted.append(peer)
                return result[0],json.dumps(row).encode(),result[2]
            return result
        def fault(a,slot,out):
            if a.kind==kind and slot.operation=='observer':self.case.fault=metadata_drift
            return out
        sent,calls=self.transport(owner,fault)
        if kind=='identity':owner.attest('central')
        sent_before=len(sent)
        with self.assertRaises(m.B3Error):owner.attest(kind)
        a=owner._attempts[kind]
        self.assertEqual(drifted,[peer]);self.assertEqual(len(sent),sent_before)
        self.assertEqual(a.worker_slot.main.offset,0)
        self.assertFalse(any(s.operation=='dispatch' for s in a.operations))
        self.assertTrue(owner._unknown);self.assertTrue(a.unknown);self.assertEqual(a.state,'UNKNOWN')
        self.assertTrue(peer_attempt._unknown)
        if kind=='identity':self.assertEqual(owner._attempts['central'].state,'COMMITTED')
        counts=(len(calls),len(self.case.calls))
        with self.assertRaises(m.B3Error):owner.attest(kind)
        self.assertEqual((len(calls),len(self.case.calls)),counts)

    def test_central_attest_rejects_identity_peer_drift_before_dispatch(self):
        self.assert_nonselected_peer_drift_blocks_dispatch('central')

    def test_identity_attest_rejects_central_peer_drift_before_dispatch(self):
        self.assert_nonselected_peer_drift_blocks_dispatch('identity')

    def test_original_main_59_seconds_leaves_at_most_one_second(self):
        owner=self.full_owner()
        def fault(a,s,out):
            if s.operation=='observer':self.now[0]=a.main_start_anchor+59
            return out
        sent,calls=self.transport(owner,fault);owner.attest('central')
        parsed=m._contract.decode_worker_request(sent[0]);self.assertLessEqual(parsed.remaining_ns,10**9)
        a=owner._attempts['central'];self.assertEqual(a.acceptance_deadline,a.main_start_anchor+60)

    def test_baseline_at_main_expiry_writes_zero_request_bytes(self):
        owner=self.full_owner()
        def fault(a,s,out):
            if s.operation=='observer':self.now[0]=a.main_start_anchor+60
            return out
        sent,calls=self.transport(owner,fault)
        with self.assertRaises(m.B3Error):owner.attest('central')
        self.assertEqual(owner._attempts['central'].worker_slot.main.offset,0)
        self.assertFalse(sent);self.assertTrue(owner._unknown)
        n=len(calls)
        with self.assertRaises(m.B3Error):owner.attest('central')
        self.assertEqual(len(calls),n)

    def test_early_job_deadline_wins(self):
        owner=self.full_owner();self.now[0]=owner._deadline-5
        sent,calls=self.transport(owner);owner.attest('central')
        a=owner._attempts['central'];self.assertEqual(a.acceptance_deadline,owner._deadline)
        self.assertLessEqual(m._contract.decode_worker_request(sent[0]).remaining_ns,5*10**9)

    def test_dispatch_fsync_expiry_prevents_any_main_write(self):
        owner=self.full_owner();sent,calls=self.transport(owner);real=m._fsync
        def fault(fd):
            real(fd)
            a=owner._attempts.get('central')
            if a and a.operations and a.operations[-1].operation=='dispatch':self.now[0]=a.acceptance_deadline
        with patch.object(m,'_fsync',fault),self.assertRaises(m.B3Error):owner.attest('central')
        a=owner._attempts['central'];self.assertEqual(a.worker_slot.main.offset,0)
        self.assertFalse(sent);self.assertTrue(owner._unknown)

    def test_late_final_fsync_failure_never_commits(self):
        owner=self.full_owner();sent,calls=self.transport(owner);real=m._fsync
        def fault(fd):
            a=owner._attempts.get('central')
            if a and a.state=='VALIDATED':raise OSError('synthetic final fsync')
            real(fd)
        with patch.object(m,'_fsync',fault),self.assertRaises(OSError):owner.attest('central')
        self.assertEqual(len(sent),1);self.assertTrue(owner._unknown)
        self.assertEqual(owner.snapshot()['execution_committed_count'],0)
        with self.assertRaises(m.B3Error):owner.close()

    def test_constructor_interruption_retains_preallocated_process(self):
        owner=self.full_owner();calls=[]
        class Interrupted:
            def __init__(child,argv,**kw):
                slot=owner._attempts['central'].operations[-1]
                self.assertIs(slot.process,child)
                self.assertTrue(any(p is child for _,p,_,_ in owner._native_originals))
                self.assertEqual(len(slot.fds),3)
                calls.append(child);raise KeyboardInterrupt('synthetic native constructor')
            def poll(child):return None
        with patch.object(m,'_popen',Interrupted),self.assertRaises(KeyboardInterrupt):owner.attest('central')
        self.assertEqual(len(calls),1);self.assertTrue(owner._unknown)
        self.assertGreater(owner.snapshot()['execution_local_live_or_unobserved'],0)
        for sl in owner._attempts['central'].operations:
            for pair in sl.fds:
                for fd in pair:os.close(fd)

    def test_original_worker_running_pid_drift_rejected(self):
        import json
        owner=self.full_owner();seen=[0]
        def fault(a,s,out):
            if s.operation=='worker-inspect':
                v=json.loads(out)
                if v[0]['State']['Status']=='running':
                    seen[0]+=1
                    if seen[0]>1:v[0]['State']['Pid']+=1
                return json.dumps(v).encode()
            return out
        sent,calls=self.transport(owner,fault)
        with self.assertRaises(m.B3Error):owner.attest('central')
        self.assertFalse(sent);self.assertTrue(owner._unknown)

    def test_response_any_trailing_byte_prevents_commit(self):
        owner=self.full_owner()
        sent,calls=self.transport(owner,lambda a,s,out:out+b'x' if s.operation=='worker-start' else out)
        with self.assertRaises(m.B3Error):owner.attest('central')
        self.assertEqual(len(sent),1);self.assertEqual(owner.snapshot()['execution_committed_count'],0)

    def test_dispatch_closed_exact_frame_and_maximum_generated_shape(self):
        import json
        from test_media_backup_b3_execution_contract import policy,binding
        p=policy(binding=binding(restore_id='r'*96,operation_id='o'*96,job_device=2**64-1,job_inode=2**64-1,
            started_at_ns=2**63-1-1800*10**9,deadline_ns=2**63-1),cpu_nanos=2*10**9,memory_bytes=2*1024**3,
            memory_swap_bytes=2*1024**3,pids_limit=128,tmpfs_bytes=256*1024**2)
        raw=m._contract.encode_attest_request(p,60*10**9)
        owner=self.full_owner();a=owner._begin_attestation('central');a.request_bytes=raw
        slot=m._transport._NativeSlot('dispatch',1,(),m.digest([]),request_pin=hashlib.sha256(raw).hexdigest())
        record=owner._ledger.operation(a,slot,'intent',request_frame=raw)
        row=json.loads(record.raw);self.assertLessEqual(len(record.raw),8192)
        self.assertEqual(bytes.fromhex(row['request_frame_hex']),raw)
        self.assertEqual(row['request_sha256'],hashlib.sha256(raw).hexdigest())
        maximum=dict(row,restore_id='r'*96,operation_id='o'*96,kind='identity',worker_cid='9'*64,
            job_device=2**64-1,job_inode=2**64-1,started_at_ns=p.binding.started_at_ns,deadline_ns=p.binding.deadline_ns)
        self.assertLessEqual(len(m.canonical(maximum)),8192)
        other=m._transport._NativeSlot('target-inspect',2,(),m.digest([]))
        with self.assertRaises(m.B3Error):owner._ledger.operation(a,other,'intent',request_frame=raw)

    def test_committed_native_wrapper_rejected_and_close_refused(self):
        owner=self.full_owner();self.transport(owner);owner.attest('central')
        original=owner._attempts['central'].operations[0].process
        class Wrapper:
            def poll(self):return original.poll()
        owner._attempts['central'].operations[0].process=Wrapper()
        graph=owner.snapshot();self.assertTrue(graph['execution_observation_error']);self.assertTrue(graph['execution_unknown'])
        with self.assertRaises(m.B3Error):owner.close()

    def test_second_failure_preserves_prior_durable_commit_counter(self):
        owner=self.full_owner()
        def fault(a,s,out):
            if a.kind=='identity' and s.operation=='target-inspect':return b'[]'
            return out
        self.transport(owner,fault);owner.attest('central')
        with self.assertRaises(m.B3Error):owner.attest('identity')
        graph=owner.snapshot();self.assertTrue(graph['execution_unknown'])
        self.assertEqual(graph['execution_committed_count'],1)

    def test_known_committed_close_failure_is_absorbing_and_never_retried(self):
        owner=self.full_owner();self.transport(owner);owner.attest('central')
        attempted=[]
        def fail(fd):attempted.append(fd);raise OSError('synthetic known host close')
        with patch.object(m,'_close',fail),self.assertRaises(OSError):owner.close()
        self.assertTrue(owner._unknown);self.assertFalse(owner._closed)
        with patch.object(m,'_close',fail),self.assertRaises(m.B3Error):owner.close()
        self.assertEqual(len(attempted),1)

    def test_committed_snapshot_after_expiry_forbids_verify_native_and_time(self):
        owner=self.full_owner();self.transport(owner);owner.attest('central');before=owner.snapshot()
        self.now[0]=owner._deadline
        def forbidden(*args,**kw):raise AssertionError('observation performed active work')
        with patch.object(owner,'_clock',forbidden),patch.object(m.TargetHandle,'verify',forbidden),patch.object(m,'_popen',forbidden),patch.object(m,'_open',forbidden):
            self.assertEqual(owner.snapshot(),before)
            owner.close();self.assertEqual(owner.snapshot(),before)

    def test_created_to_running_wait_never_restarts_original_main(self):
        import json
        owner=self.full_owner();waiting=[False]
        def fault(a,s,out):
            if s.operation=='worker-inspect' and a.worker_slot.main is not None and not waiting[0]:
                waiting[0]=True;v=json.loads(out)
                v[0]['State'].update(Status='created',Running=False,Pid=0,StartedAt='0001-01-01T00:00:00Z')
                for field in ('ResolvConfPath','HostnamePath','HostsPath','LogPath'):v[0][field]=''
                v[0]['Config'].update(Hostname=v[0]['Id'][:12],Domainname='');v[0]['HostConfig']['OomKillDisable']=False
                return json.dumps(v).encode()
            return out
        sent,calls=self.transport(owner,fault);owner.attest('central')
        self.assertTrue(waiting[0]);self.assertEqual(sum(op=='worker-start' for _,op in calls),1)
        self.assertEqual(len(sent),1);self.assertFalse(owner.snapshot()['execution_unknown'])
