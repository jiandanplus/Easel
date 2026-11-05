"""Source-only owned lifecycle tests. Private fake Popen writes retained temp FDs."""
import copy, dataclasses, hashlib, importlib, json, os, pickle, types, unittest
from unittest.mock import patch
import test_media_backup_b3_target_lifecycle as prereq
import test_media_backup_b3_target_contract as render
import test_media_backup_b3_dump_preflight as producer
try:
    m=importlib.import_module("platform_service.media_backup_b3.target_factory")
except ModuleNotFoundError:m=None

class FactoryTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(m,"complete concrete target factory missing")
        self.case=prereq.PrerequisiteTests("test_constructor_is_pure_and_policy_is_immutable");self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.input_case=producer.FinalInputTests("test_six_outputs_owned_receipt_and_reverification");self.input_case.setUp();self.addCleanup(self.input_case.doCleanups)
        self.input=self.input_case.inspect();a=self.input._stage._authority
        roles=tuple(sorted(set().union(*(set(c.role_names) for _,c in a.contexts))))
        specs=tuple(dataclasses.replace(s,restore_id=a.restore_id,started_at=a.started_at,deadline=a.deadline,restored_role_names=roles) for s in self.case.specs)
        source=dataclasses.replace(self.case.source,_restore_id=a.restore_id,_started_at=a.started_at,_deadline=a.deadline)
        capacity=dataclasses.replace(self.case.capacity,_restore_id=a.restore_id,_started_at=a.started_at,_deadline=a.deadline,_measured_at=a.started_at)
        self.policy=dataclasses.replace(self.case.policy,_specs=specs,_source=source,_capacity=capacity,_cancellation=a.cancellation)
        self.tools=m.RuntimeToolPolicy(a.restore_id,a.started_at,a.deadline,specs[0].image_id,(("cat","/usr/bin/cat","7"*64),("sha256sum","/usr/bin/sha256sum","8"*64)))
        self.calls=[];self.created=set();self.volumes=set();self.started=set();self.fault=None;self.polls=[]
        self.fixture={}
        for i,s in enumerate(specs):
            created,volume=render.observations(s,False);running,_=render.observations(s,True)
            cid=str(4+i)*64
            for raw in (created,running):
                raw["Id"]=cid;raw["Config"]["Hostname"]=cid[:12]
                raw["Config"]["Env"]=list(prereq.PUBLIC)+prereq.env_bytes(s.bootstrap_role).decode().splitlines()
            render.rebind_id(running)
            self.fixture[s.kind]=(created,running,volume)
        outer=self
        class Process:
            def __init__(self,argv,**kw):
                outer.calls.append(tuple(argv));self.returncode=None
                result=outer.respond(tuple(argv),kw)
                if outer.fault:result=outer.fault(tuple(argv),result)
                code,out,err=result;os.write(kw["stdout"],out);os.write(kw["stderr"],err);self.code=code
                self.stdin=kw["stdin"];self.env=kw["env"];outer.polls.append(self)
            def poll(self):self.returncode=self.code;return self.code
        self.patcher=patch.object(m,"_popen",Process);self.patcher.start();self.addCleanup(self.patcher.stop)
    def respond(self,argv,kw):
        args=argv[5:]
        s=next(s for s in self.policy._specs if any(x in argv for x in (s.volume_name,s.container_name,self.fixture[s.kind][0]["Id"])))
        created,running,volume=self.fixture[s.kind];cid=created["Id"]
        if args[:2]==("volume","inspect"):
            if s.kind not in self.volumes:return 1,b"[]\n",("Error response from daemon: get "+s.volume_name+": no such volume\n").encode()
            return 0,json.dumps([volume]).encode(),b""
        if args[0]=="inspect":
            if args[:3]!=("inspect","--type","container"):
                return 1,b"[]\n",("error: no such object: "+args[-1]+"\n").encode()
            self.assertEqual(len(args),4)
            if s.kind not in self.created:return 1,b"[]\n",("Error response from daemon: No such container: "+s.container_name+"\n").encode()
            return 0,json.dumps([running if s.kind in self.started else created]).encode(),b""
        if args[:2]==("volume","create"):self.volumes.add(s.kind);return 0,(s.volume_name+"\n").encode(),b""
        if args[0]=="create":self.created.add(s.kind);return 0,(cid+"\n").encode(),b""
        if args[0]=="start":self.started.add(s.kind);return 0,(cid+"\n").encode(),b""
        self.assertEqual(args[0],"exec")
        if "/usr/bin/sha256sum" in args:
            paths=args[args.index("/usr/bin/sha256sum")+1:]
            pins={row[1]:row[2] for row in self.policy._pg_tools+self.tools.tools}
            return 0,"".join(pins[path]+"  "+path+"\n" for path in paths).encode(),b""
        if "/usr/bin/cat" in args:return 0,b"Name:\tpostgres\nUid:\t999\t999\t999\t999\nGid:\t999\t999\t999\t999\nCapEff:\t0000000000000000\nNoNewPrivs:\t1\n",b""
        if "/usr/lib/postgresql/16/bin/pg_isready" in args:return 0,b"127.0.0.1:5432 - accepting connections\n",b""
        self.assertIn("/usr/lib/postgresql/16/bin/psql",args)
        if kw["env"]["PGPASSWORD"]!=prereq.PASSWORD:
            return 2,b"",('psql: error: connection to server at "127.0.0.1", port 5432 failed: FATAL:  password authentication failed for user "'+s.bootstrap_role+'"\n').encode()
        self.assertEqual(kw["env"]["PGPASSWORD"],prereq.PASSWORD)
        row=dict(system_identifier=str(10001+(s.kind=="identity")),server_version_num="160015",fsync="on",full_page_writes="on",synchronous_commit="on",password_encryption="scram-sha-256",max_connections="100",database="postgres",user=s.bootstrap_role,public_relations=0,client_addr="127.0.0.1/32",hba=[dict(type="host",database=["all"],user=["all"],address="127.0.0.1",netmask="255.255.255.255",auth_method="scram-sha-256",error=None)])
        return 0,json.dumps(row).encode()+b"\n",b""
    def factory(self):return m.RootTargetFactory(self.policy,self.tools)
    def test_each_guard_validates_policy_once_and_rejects_same_json_list(self):
        f=self.factory();self.addCleanup(f.close)
        real=prereq.m.RootFactoryPolicy.__post_init__;validations=[]
        def validate(policy):
            if policy is self.policy:validations.append(policy)
            return real(policy)
        original=self.policy._specs
        projection=self.policy._projection()
        with patch.object(prereq.m.RootFactoryPolicy,"__post_init__",validate):
            f._check_policy();f._check_policy()
            with self.subTest(check="two_guards_validation_cost"):
                self.assertEqual(len(validations),2)
            valid_count=len(validations)
            try:
                object.__setattr__(self.policy,"_specs",list(original))
                # The serialized projection collides; typed validation must still run.
                changed=dict(projection,specs=[dataclasses.asdict(s) for s in self.policy._specs])
                self.assertEqual(prereq.m.digest(projection),prereq.m.digest(changed))
                with self.assertRaises(prereq.m.B3Error):f._check_policy()
                self.assertEqual(len(validations),valid_count+1)
                self.assertEqual(self.calls,[])
            finally:object.__setattr__(self.policy,"_specs",original)

    def test_native_client_addr_pair_repeated_verify_preserves_raw_metadata(self):
        # Real Factory flow with only the private transport replaced: no metadata
        # comparison or lifecycle method is mocked.
        emitted={}
        def capture(argv,result):
            if result[0]==0 and "-c" in argv and "/usr/lib/postgresql/16/bin/psql" in argv:
                self.assertEqual(argv[-1],m._SQL)
                raw=b"  "+result[1]+b"\n"  # Preserve transport bytes, including whitespace.
                emitted[hashlib.sha256(raw).hexdigest()]=raw
                return 0,raw,result[2]
            return result
        self.fault=capture;f=self.factory()
        try:handles=[f.create(self.input,kind) for kind in ("central","identity")]
        except m.B3Error:
            self.assertEqual(f._attempts["central"]._state,"RUNNING")
            self.fail("native inet::text client_addr must reach READY from RUNNING metadata")
        counts={h._attempt._spec.kind:sum(e["operation"]=="metadata" for e in h._attempt._outputs) for h in handles}
        for _ in range(2):
            for h in handles:h.verify()
        for h in handles:
            a=h._attempt
            self.assertEqual(a._state,"READY");self.assertFalse(a._unknown)
            entries=[e for e in a._outputs if e["operation"]=="metadata"]
            # Each handle verify also checks its peer's metadata.
            self.assertEqual(len(entries),counts[a._spec.kind]+4)
            for entry in entries:
                raw=os.pread(entry["streams"][0],65536,0)
                self.assertEqual(raw,emitted[entry["stdout_sha256"]])
                self.assertEqual(json.loads(raw)["client_addr"],"127.0.0.1/32")
                self.assertEqual(hashlib.sha256(raw).hexdigest(),entry["stdout_sha256"])
                self.assertEqual(os.pread(entry["streams"][1],65536,0),b"")
                self.assertEqual(entry["stderr_sha256"],hashlib.sha256(b"").hexdigest())

    def test_client_addr_exact_native_format_rejection_retains_unknown(self):
        values=("127.0.0.1","127.0.0.2/32","127.0.0.1/0","127.0.0.1/24",
                "::1/128",None,True,0,1.0,[],{},["127.0.0.1/32"],
                " 127.0.0.1/32","127.0.0.1/32 ","127.0.0.1/32\n")
        for phase in ("create","verify","peer"):
            for value in values:
                with self.subTest(phase=phase,value=value):
                    case=FactoryTests();case.setUp()
                    try:
                        f=case.factory();h=f.create(case.input,"central") if phase!="create" else None
                        a=f._attempts.get("central")
                        original=tuple(a._processes) if a else ()
                        files=a._files if a else None
                        def fault(argv,result):
                            if result[0]==0 and "-c" in argv and "/usr/lib/postgresql/16/bin/psql" in argv:
                                if phase=="peer" and case.fixture["central"][0]["Id"] not in argv:return result
                                row=json.loads(result[1]);row["client_addr"]=value
                                return 0,json.dumps(row).encode()+b"\n",b""
                            return result
                        case.fault=fault
                        with case.assertRaises(m.B3Error):
                            if phase=="create":f.create(case.input,"central")
                            elif phase=="verify":h.verify()
                            else:f.create(case.input,"identity")
                        a=f._attempts["central"]
                        case.assertTrue(a._unknown)
                        case.assertEqual(a._state,"RUNNING" if phase=="create" else "READY")
                        if phase=="peer":case.assertTrue(f._attempts["identity"]._unknown)
                        if files is not None:case.assertIs(a._files,files)
                        case.assertEqual(tuple(a._processes[:len(original)]),original)
                        entries=[e for e in a._outputs if e["operation"]=="metadata"]
                        raw=os.pread(entries[-1]["streams"][0],65536,0)
                        case.assertEqual(json.loads(raw)["client_addr"],value)
                        case.assertEqual(hashlib.sha256(raw).hexdigest(),entries[-1]["stdout_sha256"])
                        for entry in a._outputs:
                            for fd in entry["streams"]:os.fstat(fd)
                        case.fault=None;n=len(case.calls)
                        with case.assertRaises(m.B3Error):f.create(case.input,"central")
                        if h is not None:
                            with case.assertRaises(m.B3Error):h.verify()
                        case.assertEqual(n,len(case.calls))
                    finally:case.doCleanups()

    def test_peer_ambiguous_metadata_marks_both_unknown_and_stops_transport(self):
        f=self.factory();h=f.create(self.input,"central");peer=h._attempt
        original_processes=tuple(peer._processes);original_files=peer._files
        def fault(argv,r):
            if peer._cid in argv and r[0]==0 and "-c" in argv and "/usr/lib/postgresql/16/bin/psql" in argv:
                return 1,b"",b"ambiguous peer metadata"
            return r
        self.fault=fault
        with self.assertRaises(m.B3Error):f.create(self.input,"identity")
        self.assertTrue(peer._unknown);self.assertTrue(f._attempts["identity"]._unknown)
        self.assertIs(peer._files,original_files);self.assertEqual(tuple(peer._processes[:len(original_processes)]),original_processes)
        self.fault=None;n=len(self.calls)
        with self.assertRaises(m.B3Error):h.verify()
        self.assertEqual(len(self.calls),n)

    def test_peer_each_check_baseexception_retains_owner(self):
        for method in ("_guard","_records_check","_observe","_tools_check","_pid","_deny_password","_metadata"):
            with self.subTest(method=method):
                case=FactoryTests();case.setUp()
                try:
                    f=case.factory();h=f.create(case.input,"central");peer=h._attempt
                    original=BaseException("peer interruption")
                    def interrupted():raise original
                    with patch.object(peer,method,interrupted):
                        with case.assertRaises(BaseException) as caught:f.create(case.input,"identity")
                    case.assertIs(caught.exception,original);case.assertTrue(peer._unknown)
                    case.assertTrue(f._attempts["identity"]._unknown)
                    n=len(case.calls)
                    with case.assertRaises(m.B3Error):h.verify()
                    case.assertEqual(n,len(case.calls))
                finally:case.doCleanups()

    def test_peer_partial_child_constructor_and_poll_retained(self):
        for where in ("constructor","poll"):
            with self.subTest(where=where):
                case=FactoryTests();case.setUp()
                try:
                    f=case.factory();h=f.create(case.input,"central");peer=h._attempt
                    original=KeyboardInterrupt("peer child interruption");normal=m._popen
                    class Interrupted(normal):
                        def __init__(self,argv,**kw):
                            self.is_peer=peer._cid in argv
                            if self.is_peer and where=="constructor":raise original
                            super().__init__(argv,**kw)
                        def poll(self):
                            if self.is_peer and where=="poll":raise original
                            return super().poll()
                    with patch.object(m,"_popen",Interrupted):
                        with case.assertRaises(KeyboardInterrupt) as caught:f.create(case.input,"identity")
                    case.assertIs(caught.exception,original);case.assertTrue(peer._unknown)
                    case.assertTrue(f._attempts["identity"]._unknown)
                    child=peer._processes[-1];case.assertIs(peer._outputs[-1]["process"],child)
                    for fd in peer._outputs[-1]["streams"]:os.fstat(fd)
                    n=len(case.calls)
                    with case.assertRaises(m.B3Error):h.verify()
                    case.assertEqual(n,len(case.calls));case.assertIs(peer._processes[-1],child)
                finally:case.doCleanups()

    def test_unknown_peer_blocks_existing_handle_before_any_transport(self):
        f=self.factory();h=f.create(self.input,"central");other=f.create(self.input,"identity")
        other._attempt._fail();n=len(self.calls)
        with self.assertRaises(m.B3Error):h.verify()
        self.assertEqual(n,len(self.calls));self.assertTrue(h._attempt._unknown)

    def test_split_factory_same_system_id_rejected_before_io(self):
        f=self.factory();h=f.create(self.input,"central");other=self.factory()
        def collision(argv,r):
            if r[0]==0 and "-c" in argv and "/usr/lib/postgresql/16/bin/psql" in argv:
                row=json.loads(r[1]);row["system_identifier"]="10001";return 0,json.dumps(row).encode(),b""
            return r
        self.fault=collision;n=len(self.calls)
        with self.assertRaises(m.B3Error):other.create(self.input,"identity")
        self.assertEqual(n,len(self.calls));self.assertFalse(h._attempt._unknown)
        self.fault=None;f.create(self.input,"identity");h.verify()

    def test_identity_first_and_closed_factory_cannot_release_job_owner(self):
        f=self.factory();h=f.create(self.input,"identity");n=len(self.calls)
        with self.assertRaises(m.B3Error):self.factory().create(self.input,"central")
        self.assertEqual(n,len(self.calls));h.verify();f.close();n=len(self.calls)
        with self.assertRaises(m.B3Error):self.factory().create(self.input,"central")
        self.assertEqual(n,len(self.calls))

    def test_job_owner_alias_fd_changed_policy_and_input_cannot_split(self):
        f=self.factory();h=f.create(self.input,"central");fd=os.dup(self.policy._job_fd);self.addCleanup(os.close,fd)
        altered=dataclasses.replace(self.policy,_job_fd=fd)
        policies=(altered,dataclasses.replace(altered,_source=dataclasses.replace(altered._source,_record_sha256="e"*64)))
        for policy in policies:
            with self.subTest(policy=policy):
                other=m.RootTargetFactory(policy,self.tools);n=len(self.calls)
                with patch.object(m.os,"open",side_effect=AssertionError("duplicate opened custody")):
                    with self.assertRaises(m.B3Error):other.create(self.input,"identity")
                self.assertEqual(n,len(self.calls));self.assertFalse(h._attempt._unknown)
        # Private adversarial exact-type alias: different instance, same original
        # stage/authority and fields. It must not split the owning input identity.
        alternate=object.__new__(type(self.input))
        for name in self.input.__slots__:setattr(alternate,name,getattr(self.input,name))
        n=len(self.calls)
        with self.assertRaises(m.B3Error):self.factory().create(alternate,"identity")
        with self.assertRaises(m.B3Error):f.create(alternate,"identity")
        self.assertEqual(n,len(self.calls));f.create(self.input,"identity")

    def test_concurrent_factories_claim_one_whole_job(self):
        import threading
        factories=(self.factory(),self.factory());barrier=threading.Barrier(2);results=[]
        def claim(f,kind):
            barrier.wait()
            try:results.append((kind,f.create(self.input,kind)))
            except m.B3Error:results.append((kind,None))
        threads=[threading.Thread(target=claim,args=(f,k)) for f,k in zip(factories,("central","identity"))]
        for t in threads:t.start()
        for t in threads:t.join(10);self.assertFalse(t.is_alive())
        self.assertEqual(len(results),2);self.assertEqual(sum(h is not None for _,h in results),1)
        self.assertEqual(len(self.created),1)
        winner=next(h for _,h in results if h is not None);winner.verify()

    def test_independent_job_owners_do_not_conflict(self):
        f=self.factory();h=f.create(self.input,"central")
        case=FactoryTests();case.setUp()
        try:
            other=case.factory();second=other.create(case.input,"identity");second.verify()
            self.assertFalse(h._attempt._unknown);self.assertFalse(second._attempt._unknown)
        finally:case.doCleanups()
        h.verify()

    def test_container_inspect_exact_typed_argv(self):
        f=self.factory();f.create(self.input,"central")
        s=self.policy._specs[0];prefix=(s.docker_path,"-H",s.docker_socket,"--config",s.docker_config_path)
        rows=[c for c in self.calls if c[5]=="inspect"]
        self.assertEqual(rows[0],prefix+("inspect","--type","container",s.container_name))
        self.assertGreater(len(rows),1)
        for c in rows[1:]:self.assertEqual(c,prefix+("inspect","--type","container","4"*64))

    def test_typed_absence_rejects_untyped_unsupported_and_daemon_errors(self):
        for error in (b"error: no such object: NAME\n",b"unknown flag: --type\n",b"daemon unavailable\n",b"Error response from daemon: No such container: WRONG\n"):
            with self.subTest(error=error):
                case=FactoryTests();case.setUp()
                try:
                    case.fault=lambda argv,r:(1,b"[]\n",error) if argv[5]=="inspect" else r
                    with case.assertRaises(m.B3Error):case.factory().create(case.input,"central")
                    case.assertFalse(case.created);case.assertFalse(case.volumes)
                finally:case.doCleanups()

    def test_complete_two_kind_graph_and_sealed_handle(self):
        factory=self.factory();self.assertFalse(self.calls)
        a=factory.create(self.input,"central");b=factory.create(self.input,"identity")
        for h in (a,b):
            h.verify();r=h.safe_receipt();self.assertEqual(r["status"],"target_instance_bound")
            self.assertTrue(r["wrong_bootstrap_password_denied"]);self.assertFalse(r["isolated_restore_verified"]);self.assertNotIn(prereq.PASSWORD,json.dumps(r))
            with self.assertRaises(TypeError):pickle.dumps(h)
        for s in self.policy._specs:
            rows=[json.loads(p.read_bytes()) for p in sorted(self.case.root.glob("runtime-"+s.kind+"-*.json"))]
            self.assertEqual([x["state"] for x in rows],["CLAIMED","ABSENT","VOLUME_CREATED","CID_FROZEN","START_CLAIMED","RUNNING","METADATA_BOUND","READY"])
            self.assertEqual(sum(c[5]=="start" and c[-1]==self.fixture[s.kind][0]["Id"] for c in self.calls),1)
        with self.assertRaises(m.B3Error):factory.create(self.input,"central")
        with self.assertRaises(TypeError):m.TargetHandle()
        for child,argv in zip(self.polls,self.calls):
            self.assertNotIn(prereq.PASSWORD," ".join(argv))
            self.assertLessEqual(set(child.env),{"PATH","LANG","LC_ALL","HOME","PGPASSWORD"})
        self.assertFalse(any(c[5] in ("stop","rm","restart","kill") for c in self.calls))
    def test_default_oom_rendering_pair_and_repeated_verify_preserve_raw_records(self):
        # Full template observations retain created False -> running None.
        for created_value,running_value in ((False,None),(None,False)):
            with self.subTest(created=created_value,running=running_value):
                case=FactoryTests();case.setUp()
                try:
                    for created,running,_ in case.fixture.values():
                        created["HostConfig"]["OomKillDisable"]=created_value
                        running["HostConfig"]["OomKillDisable"]=running_value
                    for spec in case.policy._specs:
                        created,running,volume=case.fixture[spec.kind]
                        m.validate_created_container(spec,created,volume)
                        m.validate_running_container(spec,running,volume)
                    f=case.factory()
                    try:handles=[f.create(case.input,kind) for kind in ("central","identity")]
                    except m.B3Error:
                        case.assertEqual(f._attempts["central"]._state,"START_CLAIMED")
                        case.fail("validated OOM default spellings must reach READY")
                    before={}
                    for h in handles:
                        a=h._attempt;kind=a._spec.kind
                        before[kind]=(copy.deepcopy(a._created),copy.deepcopy(a._running))
                        original_identity=a._identity;identity_summary=copy.deepcopy(a._identity.safe_summary())
                        for value in (created_value,running_value,created_value):
                            case.fixture[kind][1]["HostConfig"]["OomKillDisable"]=value
                            h.verify()
                        case.assertIs(a._identity,original_identity)
                        case.assertEqual(a._identity.safe_summary(),identity_summary)
                        case.assertEqual(a._created,before[kind][0])
                        case.assertEqual(a._running,before[kind][1])
                        row=json.loads((case.case.root/("runtime-"+kind+"-007.json")).read_bytes())
                        case.assertEqual(row["created_sha256"],m.digest(before[kind][0]))
                        case.assertEqual(row["running_sha256"],m.digest(before[kind][1]))
                        # Every captured inspect stream remains the raw child JSON.
                        values=[]
                        for entry in a._outputs:
                            if entry["operation"]=="inspect":
                                fd=entry["streams"][0];raw=os.pread(fd,1024**2,0)
                                case.assertEqual(hashlib.sha256(raw).hexdigest(),entry["stdout_sha256"])
                                values.append(json.loads(raw)[0]["HostConfig"]["OomKillDisable"])
                        case.assertIs(values[0],created_value)
                        case.assertIs(values[1],running_value)
                    for h in handles:h.verify()
                finally:case.doCleanups()

    def test_oom_rendering_does_not_hide_invalid_or_other_container_drift(self):
        for phase in ("created","running","verify"):
            for key,value in (("OomKillDisable",True),("OomKillDisable",0),
                              ("OomKillDisable","false"),("OomKillDisable","missing"),
                              ("Memory",805306369),("Privileged",True),
                              ("Dns",None)):
                with self.subTest(phase=phase,key=key,value=value):
                    case=FactoryTests();case.setUp()
                    try:
                        f=case.factory();h=f.create(case.input,"central") if phase=="verify" else None
                        row=case.fixture["central"][0 if phase=="created" else 1]
                        if value=="missing":row["HostConfig"].pop(key)
                        else:row["HostConfig"][key]=value
                        # DNS None is itself accepted, but must still compare exactly.
                        if phase=="created" and key=="Dns":
                            case.fixture["central"][1]["HostConfig"]["Dns"]=[]
                        with case.assertRaises(m.B3Error):
                            if h is None:f.create(case.input,"central")
                            else:h.verify()
                        case.assertTrue(f._attempts["central"]._unknown)
                        n=len(case.calls)
                        with case.assertRaises(m.B3Error):f.create(case.input,"central")
                        case.assertEqual(n,len(case.calls))
                    finally:case.doCleanups()

    def test_uncertain_start_retains_refs_and_forbids_replay(self):
        self.fault=lambda argv,r:(1,b"",b"daemon ambiguous") if argv[5]=="start" else r
        f=self.factory()
        with self.assertRaises(m.B3Error):f.create(self.input,"central")
        a=f._attempts["central"];self.assertTrue(a._unknown);self.assertTrue(a._processes)
        self.assertIs(a._verified,self.input);f.close();self.assertFalse(a._files._closed)
        n=len(self.calls)
        with self.assertRaises(m.B3Error):f.create(self.input,"central")
        self.assertEqual(n,len(self.calls))
    def test_invalid_input_and_wrong_original_event_before_io(self):
        f=self.factory()
        for candidate in ({},object(),None,object.__new__(type("DerivedInput",(m.VerifiedRestoreInput,),{}))):
            with self.assertRaises(m.B3Error):f.create(candidate,"central")
        wrong=dataclasses.replace(self.policy,_cancellation=__import__("threading").Event())
        with self.assertRaises(m.B3Error):m.RootTargetFactory(wrong,self.tools).create(self.input,"central")
        self.assertFalse(self.calls)
    def test_wrong_metadata_stops_without_ready(self):
        def fault(argv,r):
            if "/usr/lib/postgresql/16/bin/psql" in argv and "/usr/bin/sha256sum" not in argv:
                row=json.loads(r[1]);row["fsync"]="off";return 0,json.dumps(row).encode(),b""
            return r
        self.fault=fault;f=self.factory()
        with self.assertRaises(m.B3Error):f.create(self.input,"central")
        self.assertTrue(f._attempts["central"]._unknown)
    def test_existing_volume_is_never_reused(self):
        self.volumes.add("central");f=self.factory()
        with self.assertRaises(m.B3Error):f.create(self.input,"central")
        self.assertFalse(any(c[5] in ("create","start") or c[5:7]==("volume","create") for c in self.calls))

    def test_failed_custody_cannot_be_reclaimed_by_new_factory(self):
        f=self.factory()
        with patch.object(m._PrivateJobFiles,"verify",side_effect=KeyboardInterrupt("synthetic")):
            with self.assertRaises(KeyboardInterrupt):f.create(self.input,"central")
        self.assertIsNotNone(f._attempts["central"]._files)
        with self.assertRaises(m.B3Error):self.factory().create(self.input,"central")
        self.assertFalse(self.calls)
    def test_peer_process_drift_invalidates_existing_handle(self):
        f=self.factory();a=f.create(self.input,"central");f.create(self.input,"identity")
        self.fixture["identity"][1]["State"]["Pid"]+=1
        with self.assertRaises(m.B3Error):a.verify()
    def test_output_permissions_rechecked_after_child(self):
        def fault(argv,r):
            # Child changes its own output inode's permission through retained FD.
            a=f._attempts["central"]
            os.fchmod(a._outputs[-1]["streams"][0],0o644)
            return r
        f=self.factory();self.fault=fault
        with self.assertRaises(m.B3Error):f.create(self.input,"central")
        self.assertEqual(len(self.calls),1)
    def test_terminal_nonready_observation_uses_same_single_start(self):
        count=[0]
        def fault(argv,r):
            if "/usr/lib/postgresql/16/bin/pg_isready" in argv and "/usr/bin/sha256sum" not in argv:
                count[0]+=1
                if count[0]<3:return count[0],(b"127.0.0.1:5432 - rejecting connections\n" if count[0]==1 else b"127.0.0.1:5432 - no response\n"),b""
            return r
        self.fault=fault;f=self.factory();f.create(self.input,"central")
        self.assertEqual(count[0],3);self.assertEqual(sum(c[5]=="start" for c in self.calls),1)
    def test_ambiguous_nonready_never_polls_again(self):
        self.fault=lambda argv,r:(1,b"",b"daemon error") if "/usr/lib/postgresql/16/bin/pg_isready" in argv and "/usr/bin/sha256sum" not in argv else r
        f=self.factory()
        with self.assertRaises(m.B3Error):f.create(self.input,"central")
        self.assertEqual(sum("/usr/lib/postgresql/16/bin/pg_isready" in c and "/usr/bin/sha256sum" not in c for c in self.calls),1)
    def test_start_claim_fsync_failure_never_starts(self):
        f=self.factory();real=os.fsync
        def fail(fd):
            a=f._attempts["central"]
            if a._state=="CID_FROZEN" and any(p.name.endswith("004.json") for p in self.case.root.glob("runtime-*")):raise SystemExit("synthetic")
            return real(fd)
        with patch.object(m.os,"fsync",side_effect=fail):
            with self.assertRaises(SystemExit):f.create(self.input,"central")
        self.assertFalse(any(c[5]=="start" for c in self.calls));self.assertTrue(f._attempts["central"]._retained_fds)
    def test_handle_rechecks_input_custody_original_authority(self):
        f=self.factory();h=f.create(self.input,"central")
        self.input._stage._authority=dataclasses.replace(self.input._stage._authority)
        n=len(self.calls)
        with self.assertRaises(m.B3Error):h.verify()
        self.assertEqual(n,len(self.calls))
    def test_cancellation_after_container_creation_retains_cid_output_no_start(self):
        def fault(argv,r):
            if argv[5]=="create":self.policy._cancellation.set()
            return r
        self.fault=fault;f=self.factory()
        with self.assertRaises(m.B3Error):f.create(self.input,"central")
        a=f._attempts["central"];self.assertTrue(a._unknown);self.assertTrue(a._outputs)
        self.assertFalse(any(c[5]=="start" for c in self.calls))
    def test_stdio_bounds_fail_closed(self):
        self.fault=lambda argv,r:(0,b"x"*(1024**2+1),b"")
        f=self.factory()
        with self.assertRaises(m.B3Error):f.create(self.input,"central")
        self.assertEqual(len(self.calls),1);self.assertTrue(f._attempts["central"]._processes)
    def test_transport_interrupt_retains_original_child_and_input(self):
        f=self.factory();original=KeyboardInterrupt("synthetic")
        class Interrupted:
            def __init__(self,*a,**k):pass
            def poll(self):raise original
        with patch.object(m,"_popen",Interrupted):
            with self.assertRaises(KeyboardInterrupt) as caught:f.create(self.input,"central")
        self.assertIs(caught.exception,original)
        a=f._attempts["central"];self.assertTrue(a._processes);self.assertIs(a._verified,self.input)
    def test_source_system_reuse_rejected(self):
        source=dataclasses.replace(self.policy._source,_system_id_sha256=hashlib.sha256(b"10001").hexdigest())
        specs=tuple(dataclasses.replace(s,source_system_id_sha256=source._system_id_sha256) for s in self.policy._specs)
        self.policy=dataclasses.replace(self.policy,_source=source,_specs=specs)
        with self.assertRaises(m.B3Error):self.factory().create(self.input,"central")

    def test_wrong_password_probe_is_required_and_ambiguous_denial_fails(self):
        seen=[]
        def fault(argv,r):
            if r[0]==2 and b"password authentication failed" in r[2]:
                seen.append(True);return 2,b"",b"connection refused\n"
            return r
        self.fault=fault;f=self.factory()
        with self.assertRaises(m.B3Error):f.create(self.input,"central")
        self.assertEqual(seen,[True]);self.assertEqual(sum(c[5]=="start" for c in self.calls),1)

    def test_metadata_and_pid_and_tool_rejection_matrix(self):
        mutations=(
            ("server_version_num","160014"),("fsync","off"),("full_page_writes","off"),
            ("synchronous_commit","off"),("password_encryption","md5"),("max_connections","101"),
            ("database","other"),("user","source_admin"),("public_relations",True),
            ("public_relations",1),("client_addr",None),("system_identifier","0"),
            ("system_identifier","18446744073709551616"),("hba",[]),
            ("hba",[dict(type="host",database=["all"],user=["all"],address="127.0.0.1",netmask="255.255.255.255",auth_method="trust",error=None)]),
            ("raw",b'{"system_identifier":NaN}'),("raw",b'{"x":1,"x":2}'),
            ("tool",None),("uid",None),("caps",None),("nnp",None))
        for key,value in mutations:
            with self.subTest(field=key):
                case=FactoryTests();case.setUp()
                try:
                    def fault(argv,r):
                        if key=="tool" and "/usr/bin/sha256sum" in argv:return 0,b"0"+r[1][1:],b""
                        if key in ("uid","caps","nnp") and "/proc/1/status" in argv:
                            old,new={"uid":(b"999",b"0"),"caps":(b"0000000000000000",b"0000000000000001"),"nnp":(b"NoNewPrivs:\t1",b"NoNewPrivs:\t0")}[key]
                            return 0,r[1].replace(old,new),b""
                        if r[0]==0 and "-c" in argv and "/usr/lib/postgresql/16/bin/psql" in argv:
                            if key=="raw":return 0,value,b""
                            if key not in ("tool","uid","caps","nnp"):
                                row=json.loads(r[1]);row[key]=value;return 0,json.dumps(row).encode(),b""
                        return r
                    case.fault=fault;f=case.factory()
                    with case.assertRaises(m.B3Error):f.create(case.input,"central")
                    case.assertTrue(f._attempts["central"]._unknown)
                    case.assertFalse(any(json.loads(p.read_bytes())["state"]=="READY" for p in case.case.root.glob("runtime-*")))
                finally:case.doCleanups()
    def test_each_durable_phase_interrupt_keeps_once_ownership(self):
        for phase in ("CLAIMED","ABSENT","VOLUME_CREATED","CID_FROZEN","START_CLAIMED","RUNNING","METADATA_BOUND","READY"):
            with self.subTest(phase=phase):
                case=FactoryTests();case.setUp()
                try:
                    f=case.factory();real=m._Attempt._advance;original=BaseException("synthetic")
                    def interrupted(a,state):
                        real(a,state)
                        if state==phase:raise original
                    with patch.object(m._Attempt,"_advance",interrupted):
                        with case.assertRaises(BaseException) as caught:f.create(case.input,"central")
                    case.assertIs(caught.exception,original);a=f._attempts["central"]
                    case.assertTrue(a._unknown);case.assertIs(a._verified,case.input)
                    count=len(case.calls)
                    with case.assertRaises(m.B3Error):case.factory().create(case.input,"central")
                    case.assertEqual(count,len(case.calls))
                finally:case.doCleanups()
    def test_immutable_container_and_volume_drift_reject_on_verify(self):
        f=self.factory();h=f.create(self.input,"central")
        self.fixture["central"][2]["CreatedAt"]="2026-11-03T00:00:00.123456788Z"
        with self.assertRaises(m.B3Error):h.verify()
    def test_environment_drift_before_start_no_start(self):
        def fault(argv,r):
            if argv[5]=="create":
                path=self.case.root/"central.env";path.write_bytes(path.read_bytes()+b"EXTRA=x\n")
            return r
        self.fault=fault;f=self.factory()
        with self.assertRaises(m.B3Error):f.create(self.input,"central")
        self.assertFalse(any(c[5]=="start" for c in self.calls))
    def test_constructor_is_pure_and_runtime_tools_bound(self):
        with patch.object(m.os,"open",side_effect=AssertionError("IO")),patch.object(m.os,"fstat",side_effect=AssertionError("IO")):
            self.factory()
        wrong=dataclasses.replace(self.tools,restore_id="other")
        with self.assertRaises(m.B3Error):m.RootTargetFactory(self.policy,wrong)
        self.assertFalse(self.calls)
    def test_deadline_and_capacity_rechecked_mid_transport(self):
        f=self.factory()
        class Hung:
            def __init__(self,*a,**kw):pass
            def poll(self):
                self_time.return_value=self.policy._source._deadline
                return None
        self_time=prereq.m.time.monotonic;Hung.policy=self.policy
        with patch.object(m,"_popen",Hung):
            with self.assertRaises(m.B3Error):f.create(self.input,"central")
        self.assertTrue(f._attempts["central"]._processes)

    def test_role_union_mismatch_rejects_before_custody(self):
        specs=tuple(dataclasses.replace(s,restored_role_names=("wrong_role",)) for s in self.policy._specs)
        self.policy=dataclasses.replace(self.policy,_specs=specs)
        with self.assertRaises(m.B3Error):self.factory().create(self.input,"central")
        self.assertFalse(self.calls)
    def test_active_exec_after_terminal_nonready_halts_polling(self):
        def fault(argv,r):
            if "/usr/lib/postgresql/16/bin/pg_isready" in argv and "/usr/bin/sha256sum" not in argv:
                self.fixture["central"][1]["ExecIDs"]=["9"*64]
                return 1,b"127.0.0.1:5432 - rejecting connections\n",b""
            return r
        self.fault=fault
        with self.assertRaises(m.B3Error):self.factory().create(self.input,"central")
        self.assertEqual(sum("-t" in c and "/usr/lib/postgresql/16/bin/pg_isready" in c for c in self.calls),1)
    def test_peer_system_collision_rejects_second_handle(self):
        f=self.factory();f.create(self.input,"central")
        def fault(argv,r):
            if r[0]==0 and "-c" in argv and "/usr/lib/postgresql/16/bin/psql" in argv:
                row=json.loads(r[1]);row["system_identifier"]="10001";return 0,json.dumps(row).encode(),b""
            return r
        self.fault=fault
        with self.assertRaises(m.B3Error):f.create(self.input,"identity")
        self.assertTrue(f._attempts["identity"]._unknown)

    def test_verified_receipt_drift_rejects_before_mutation(self):
        def fault(argv,r):
            if argv[5:7]==("volume","inspect"):
                self.input._receipt["changed_after_claim"]=True
            return r
        self.fault=fault
        with self.assertRaises(m.B3Error):self.factory().create(self.input,"central")
        self.assertFalse(any(c[5] in ("create","start") or c[5:7]==("volume","create") for c in self.calls))

    def test_popen_constructor_interruption_keeps_original_process_object(self):
        f=self.factory();original=KeyboardInterrupt("synthetic constructor")
        class InterruptedInit:
            def __init__(self,*a,**kw):raise original
        with patch.object(m,"_popen",InterruptedInit):
            with self.assertRaises(KeyboardInterrupt) as caught:f.create(self.input,"central")
        self.assertIs(caught.exception,original)
        a=f._attempts["central"]
        self.assertEqual(len(a._processes),1)
        self.assertIs(a._outputs[-1]["process"],a._processes[0])

    def test_metadata_journal_binds_observed_system_and_transcript(self):
        f=self.factory();f.create(self.input,"central")
        row=json.loads((self.case.root/"runtime-central-007.json").read_bytes())
        self.assertIn("system_id_sha256",row)
        self.assertEqual(row["system_id_sha256"],hashlib.sha256(b"10001").hexdigest())
        self.assertEqual(row["volume_sha256"],prereq.contract.digest(self.fixture["central"][2]))
        self.assertEqual(len(row["transcript_sha256"]),64)

if __name__=="__main__":unittest.main()
