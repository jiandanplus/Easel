"""Prerequisite tests: temporary files only; no subprocess, Docker or SQL."""
import dataclasses
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1] / "src" / "platform_service"
for name, path in (("platform_service", ROOT), ("platform_service.media_backup_b3", ROOT / "media_backup_b3")):
    if name not in sys.modules:
        package = types.ModuleType(name); package.__path__ = [str(path)]; sys.modules[name] = package
contract = importlib.import_module("platform_service.media_backup_b3.target_contract")
try:
    m = importlib.import_module("platform_service.media_backup_b3.target_lifecycle")
except ModuleNotFoundError:
    m = None

PUBLIC = ("PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/lib/postgresql/16/bin", "GOSU_VERSION=1.19", "LANG=en_US.utf8", "PG_MAJOR=16", "PG_VERSION=16.15-1.pgdg13+2", "PGDATA=/var/lib/postgresql/data")
PASSWORD = "synthetic-temp-password"
def sha(b): return hashlib.sha256(b).hexdigest()
def env_bytes(role):
    return ("POSTGRES_USER="+role+"\nPOSTGRES_PASSWORD="+PASSWORD+"\nPOSTGRES_DB=postgres\nPOSTGRES_HOST_AUTH_METHOD=scram-sha-256\nPOSTGRES_INITDB_ARGS=--encoding=UTF8 --locale=C --auth-host=scram-sha-256\n").encode()
def spec(root, kind, nonce, role):
    raw=env_bytes(role)
    return contract.TargetSpec(restore_id="toy_job", kind=kind, operation_nonce=nonce*32, started_at=100., deadline=1900., image_id=contract.IMAGE_ID, source_container_id="1"*64, source_system_id_sha256="2"*64, source_volume_names=("source-data",), source_admin_name="source_admin", restored_role_names=("app_c","app_i"), bootstrap_role=role, docker_path=str(root/"docker"), docker_socket="unix:///var/run/docker.sock", docker_config_path=str(root/"config"), env_file_path=str(root/(kind+".env")), env_file_sha256=sha(raw), expected_env_sha256=sha(contract.canonical(sorted(PUBLIC+tuple(raw.decode().splitlines())))), admission_bytes=8*1024**3, running_budget_bytes=2*1024**3)

class PrerequisiteTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(m, "target lifecycle prerequisites not implemented")
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve(); self.root.chmod(0o700)
        (self.root/"config").mkdir(mode=0o700)
        (self.root/"docker").write_bytes(b"synthetic tool fixture never executed"); (self.root/"docker").chmod(0o700)
        for kind,role in (("central","boot_c"),("identity","boot_i")):
            (self.root/(kind+".env")).write_bytes(env_bytes(role)); (self.root/(kind+".env")).chmod(0o600)
        self.fd=os.open(self.root,os.O_RDONLY|os.O_DIRECTORY); self.addCleanup(os.close,self.fd)
        self.event=threading.Event()
        self.specs=(spec(self.root,"central","a","boot_c"),spec(self.root,"identity","b","boot_i"))
        self.source=m.RootSourceProof("toy_job",100.,1900.,"1"*64,"2"*64,("source-data",),"source_admin","3"*64)
        self.capacity=m.RootCapacityProof("toy_job",100.,1900.,110.,os.fstat(self.fd).st_dev,1024**3,11*1024**3,"4"*64)
        self.policy=m.RootFactoryPolicy(self.specs,self.source,self.capacity,self.event,str(self.root),self.fd,os.geteuid(),os.getegid(),sha((self.root/"docker").read_bytes()),(("pg_isready","/usr/lib/postgresql/16/bin/pg_isready","5"*64),("psql","/usr/lib/postgresql/16/bin/psql","6"*64)),PUBLIC)
        self.clock=patch.object(m.time,"monotonic",return_value=120.);self.clock.start();self.addCleanup(self.clock.stop)
        self.space=patch.object(m.os,"fstatvfs",return_value=types.SimpleNamespace(f_bavail=16*1024**2,f_frsize=4096))
        self.space.start();self.addCleanup(self.space.stop)
    def files(self):
        f=m._PrivateJobFiles(self.policy);self.addCleanup(f.close);return f
    def test_each_guard_validates_policy_once_and_rejects_same_json_list(self):
        f=self.files()
        real=m.RootFactoryPolicy.__post_init__;validations=[]
        def validate(policy):
            if policy is self.policy:validations.append(policy)
            return real(policy)
        original=self.policy._specs
        projection=self.policy._projection()
        with patch.object(m.RootFactoryPolicy,"__post_init__",validate):
            f._guard();f._guard()
            with self.subTest(check="two_guards_validation_cost"):
                self.assertEqual(len(validations),2)
            valid_count=len(validations)
            try:
                object.__setattr__(self.policy,"_specs",list(original))
                # The serialized projection collides; typed validation must still run.
                changed=dict(projection,specs=[dataclasses.asdict(s) for s in self.policy._specs])
                self.assertEqual(m.digest(projection),m.digest(changed))
                with self.assertRaises(m.B3Error):f._guard()
                self.assertEqual(len(validations),valid_count+1)
            finally:object.__setattr__(self.policy,"_specs",original)

    def test_review_all_exceptional_ledger_exits_retain_original(self):
        # Independent jobs keep the failure matrix isolated; no registry resets.
        for phase in ("claim", "append"):
            for point in ("file", "directory"):
                for error_type in (KeyboardInterrupt, SystemExit, RuntimeError, BaseException):
                    with self.subTest(phase=phase,point=point,error=error_type.__name__):
                        case=PrerequisiteTests("test_constructor_is_pure_and_policy_is_immutable")
                        case.setUp()
                        try:
                            f=case.files();j=m._TargetLedger(f)
                            if phase=="append":j.claim("central","8"*64)
                            original=error_type("synthetic interruption")
                            real=m.os.fsync
                            def fail(fd):
                                if (fd==f._fd)==(point=="directory"):raise original
                                return real(fd)
                            with patch.object(m.os,"fsync",side_effect=fail):
                                with self.assertRaises(BaseException) as caught:
                                    if phase=="claim":j.claim("central","8"*64)
                                    else:j.append("central","PREPARED")
                            if error_type in (KeyboardInterrupt,SystemExit,BaseException):self.assertIs(caught.exception,original)
                            self.assertTrue(j._unknown)
                            self.assertEqual(j._states["central"],"UNKNOWN")
                            self.assertIn(j,m._UNKNOWN_JOURNALS)
                            self.assertIs(j._files,f);self.assertTrue(j._retained_fds)
                            for fd in j._retained_fds:os.fstat(fd)
                            f.close();self.assertFalse(f._closed);os.fstat(f._fd)
                        finally:case.doCleanups()

    def test_review_missing_record_blocks_replacement_custodies_and_hashes(self):
        f=self.files();j=m._TargetLedger(f);real=m.os.open
        def failed_open(path,*args,**kwargs):
            if path=="b3-central-000.json":raise OSError("synthetic precreation failure")
            return real(path,*args,**kwargs)
        with patch.object(m.os,"open",side_effect=failed_open):
            with self.assertRaises(m.B3Error):j.claim("central","8"*64)
        self.assertFalse((self.root/"b3-central-000.json").exists())
        alternate=os.open(self.root,os.O_RDONLY|os.O_DIRECTORY);self.addCleanup(os.close,alternate)
        changed=dataclasses.replace(self.policy,_job_fd=alternate,_docker_sha256=self.policy._docker_sha256,
                                    _source=dataclasses.replace(self.source,_record_sha256="f"*64))
        other=m._PrivateJobFiles(changed);self.addCleanup(other.close)
        for custody in (f,self.files(),other):
            replacement=m._TargetLedger(custody)
            with self.subTest(custody=id(custody)),self.assertRaises(m.B3Error):replacement.claim("central","9"*64)
        independent=PrerequisiteTests("test_constructor_is_pure_and_policy_is_immutable")
        independent.setUp()
        try:m._TargetLedger(independent.files()).claim("central","8"*64)
        finally:independent.doCleanups()

    def test_review_interruption_survives_secondary_retention_failure(self):
        class BrokenRetention(list):
            def append(self,value):raise OSError("secondary retention failure")
        f=self.files();j=m._TargetLedger(f);original=KeyboardInterrupt("original")
        with patch.object(m,"_UNKNOWN_JOURNALS",BrokenRetention()),patch.object(m.os,"write",side_effect=original):
            with self.assertRaises(KeyboardInterrupt) as caught:j.claim("central","8"*64)
            self.assertIs(caught.exception,original)
            self.assertTrue(j._unknown);self.assertEqual(j._states["central"],"UNKNOWN")
            f.close();self.assertFalse(f._closed)
            self.assertIn(j,m._ATTEMPT_OWNERS.values())
        with self.assertRaises(m.B3Error):m._TargetLedger(self.files()).claim("central","9"*64)

    def test_review_interruption_immediately_after_ownership_retains_attempt(self):
        f=self.files();j=m._TargetLedger(f);original=KeyboardInterrupt("ownership boundary")
        lock=m._ATTEMPT_LOCK
        class InterruptedExit:
            def __enter__(self):return lock.__enter__()
            def __exit__(self,*args):
                lock.__exit__(*args)
                raise original
        with patch.object(m,"_ATTEMPT_LOCK",InterruptedExit()):
            with self.assertRaises(KeyboardInterrupt) as caught:j.claim("central","8"*64)
        self.assertIs(caught.exception,original)
        self.assertTrue(j._unknown);self.assertEqual(j._states["central"],"UNKNOWN")
        self.assertIn(j,m._UNKNOWN_JOURNALS)
        self.assertFalse((self.root/"b3-central-000.json").exists())
        f.close();self.assertFalse(f._closed)
        with self.assertRaises(m.B3Error):m._TargetLedger(self.files()).claim("central","9"*64)

    def test_review_eleven_environment_entries_required(self):
        raw=env_bytes("boot_c")
        candidate=dataclasses.replace(self.specs[0],env_file_sha256=sha(raw),expected_env_sha256=sha(contract.canonical(sorted(PUBLIC+tuple(raw.decode().splitlines())))))
        try:parsed=m._parse_env(raw,candidate,PUBLIC)
        except m.B3Error:self.fail("fixed fifth initialization override must be accepted")
        self.assertEqual(parsed["POSTGRES_INITDB_ARGS"],"--encoding=UTF8 --locale=C --auth-host=scram-sha-256")

    def test_constructor_is_pure_and_policy_is_immutable(self):
        with patch.object(m.os,"open",side_effect=AssertionError("IO forbidden")),patch.object(m.os,"fstat",side_effect=AssertionError("IO forbidden")):
            dataclasses.replace(self.policy)
        with self.assertRaises(dataclasses.FrozenInstanceError): self.policy._uid=0
        self.assertNotIn(str(self.root),repr(self.policy));self.assertNotIn("source_admin",repr(self.source))
        self.assertFalse(hasattr(m,"RootTargetFactory"));self.assertFalse(hasattr(m,"TargetHandle"))
    def test_policy_tampering_after_custody_and_event_replacement_reject(self):
        f=self.files()
        object.__setattr__(self.policy,"_cancellation",threading.Event())
        with self.assertRaises(m.B3Error): f.verify()

    def test_source_proof_closed_types_do_not_leak_raw_errors(self):
        for value in (["source-data"],({},), (True,), ("source-data","source-data")):
            with self.subTest(value=value),self.assertRaises(m.B3Error):dataclasses.replace(self.source,_volume_names=value)

    def test_ledger_rechecks_named_record_after_write(self):
        f=self.files();j=m._TargetLedger(f);real=m.os.fsync;replaced=[]
        def replace_after_sync(fd):
            real(fd)
            if not replaced:
                path=self.root/"b3-central-000.json"
                path.rename(self.root/"detached.json")
                path.write_bytes(b"{}");path.chmod(0o600);replaced.append(True)
        with patch.object(m.os,"fsync",side_effect=replace_after_sync):
            with self.assertRaises(m.B3Error): j.claim("central","8"*64)
        self.assertEqual(j._states["central"],"UNKNOWN")

    def test_policy_rejects_mutable_or_inconsistent_authority(self):
        mutations=({"_specs":list(self.specs)},{"_specs":self.specs[::-1]},{"_uid":True},{"_job_fd":True},{"_docker_sha256":"x"*64},{"_pg_tools":list(self.policy._pg_tools)},{"_public_image_env":PUBLIC+("HTTP_PROXY=bad",)},{"_public_image_env":PUBLIC[::-1]},{"_source":{}},{"_cancellation":object()})
        for kw in mutations:
            with self.subTest(kw=kw),self.assertRaises(m.B3Error): dataclasses.replace(self.policy,**kw)
        for kw in ({"deadline":1800.},{"restored_role_names":("app_c",)},{"source_container_id":"7"*64},{"bootstrap_role":"boot_c"},{"env_file_path":self.specs[0].env_file_path}):
            with self.subTest(spec=kw),self.assertRaises(m.B3Error):
                dataclasses.replace(self.policy,_specs=(self.specs[0],dataclasses.replace(self.specs[1],**kw)))
    def test_capacity_proof_rejects_bool_nonfinite_and_unfunded_peak(self):
        for kw in ({"_measured_at":float("nan")},{"_peak_bytes":True},{"_reserved_bytes":10*1024**3-1},{"_measured_at":1901.},{"_measurement_sha256":"bad"}):
            with self.subTest(kw=kw),self.assertRaises(m.B3Error): dataclasses.replace(self.capacity,**kw)
    def test_private_custody_clones_fd_and_safe_receipt_is_prepare_only(self):
        f=self.files();self.assertNotEqual(f._fd,self.fd);f.verify()
        r=f.safe_receipt();self.assertEqual(r["status"],"target_prerequisites_validated")
        for key in ("actual_resources_observed","target_instance_bound","isolated_restore_verified","production","provider","timer"):
            self.assertIs(r[key],False)
        self.assertNotIn(PASSWORD,json.dumps(r)); self.assertNotIn("boot_c",json.dumps(r))
        self.assertEqual(r["capacity_measurement_type"],"host_statvfs_plus_root_reserved_peak")
    def test_owner_mode_and_fd_path_mismatch_reject(self):
        for mode in (0o755,0o770):
            self.root.chmod(mode)
            with self.assertRaises(m.B3Error): self.files()
        self.root.chmod(0o700)
        with self.assertRaises(m.B3Error): m._PrivateJobFiles(dataclasses.replace(self.policy,_uid=os.geteuid()+1))
        other=self.root/"other";other.mkdir(mode=0o700)
        with self.assertRaises(m.B3Error): m._PrivateJobFiles(dataclasses.replace(self.policy,_job_path=str(other)))
    def test_directory_replacement_and_symlink_lineage_reject(self):
        f=self.files();original=self.root/"config";original.rename(self.root/"config-old");original.mkdir(mode=0o700)
        with self.assertRaises(m.B3Error): f.verify()
        original.rmdir();original.symlink_to(self.root/"config-old",target_is_directory=True)
        with self.assertRaises(m.B3Error): self.files()
    def test_config_must_stay_empty(self):
        f=self.files();(self.root/"config"/"config.json").write_text("{}")
        with self.assertRaises(m.B3Error): f.verify()
    def test_env_mode_hardlink_symlink_hash_and_content(self):
        path=self.root/"central.env"; original=path.read_bytes()
        for mode in (0o644,0o400):
            path.chmod(mode)
            with self.assertRaises(m.B3Error): self.files()
        path.chmod(0o600);os.link(path,self.root/"alias")
        with self.assertRaises(m.B3Error): self.files()
        (self.root/"alias").unlink()
        path.unlink();path.symlink_to(self.root/"identity.env")
        with self.assertRaises(m.B3Error): self.files()
        path.unlink();path.write_bytes(original);path.chmod(0o600)
        f=self.files();path.write_bytes(original.replace(PASSWORD.encode(),b"changed"))
        with self.assertRaises(m.B3Error): f.verify()
    def test_env_parser_is_closed_and_complete_hash_checked(self):
        base=env_bytes("boot_c")
        bads=(base+b"POSTGRES_USER=boot_c\n",base+b"HTTP_PROXY=x\n",base.replace(b"scram-sha-256",b"trust"),base.replace(PASSWORD.encode(),b""),base.replace(PASSWORD.encode(),b"nul\x00"),base.replace(b"POSTGRES_DB=postgres",b"POSTGRES_DB=other"),base.replace(b"boot_c",b"wrong"),base+b"\n",base[:-1]+b"\r\n")
        init=b"POSTGRES_INITDB_ARGS=--encoding=UTF8 --locale=C --auth-host=scram-sha-256\n"
        bads+=(base.replace(init,b""),base+init)
        bads+=tuple(base.replace(init,init[:-1]+tail+b"\n") for tail in (
            b" --auth-local=trust",b";echo bad",b"$(echo bad)",b"\r",b"\x00"))
        bads+=(base.replace(init,init.replace(b"--auth-host=scram-sha-256",b"--auth-host=trust")),)
        for raw in bads:
            with self.subTest(raw_sha=sha(raw)),self.assertRaises(m.B3Error):
                candidate=dataclasses.replace(self.specs[0],env_file_sha256=sha(raw),expected_env_sha256=sha(contract.canonical(sorted(PUBLIC+tuple(raw.decode().splitlines())))))
                m._parse_env(raw,candidate,PUBLIC)
        with self.assertRaises(m.B3Error): m._parse_env(base,dataclasses.replace(self.specs[0],expected_env_sha256="f"*64),PUBLIC)
    def test_tool_stamp_hash_and_execution_permission_rechecked(self):
        f=self.files();path=self.root/"docker";path.chmod(0o600)
        with self.assertRaises(m.B3Error): f.verify()
        path.chmod(0o700);path.write_bytes(b"replacement")
        with self.assertRaises(m.B3Error): self.files()
    def test_cancellation_original_deadline_and_capacity_checked(self):
        f=self.files();self.event.set()
        with self.assertRaises(m.B3Error): f.verify()
        self.event.clear()
        with patch.object(m.time,"monotonic",return_value=1900.):
            with self.assertRaises(m.B3Error): f.verify()
        with patch.object(m.os,"fstatvfs",return_value=types.SimpleNamespace(f_bavail=0,f_frsize=4096)):
            with self.assertRaises(m.B3Error): f.verify()
    def test_per_kind_exclusive_claim_canonical_chain_and_replay(self):
        f=self.files();j=m._TargetLedger(f)
        j.claim("central","8"*64);j.append("central","PREPARED")
        rows=[json.loads(p.read_bytes()) for p in sorted(self.root.glob("b3-central-*.json"))]
        self.assertEqual([r["state"] for r in rows],["CLAIMED","PREPARED"])
        self.assertEqual(rows[1]["previous_sha256"],sha((self.root/"b3-central-000.json").read_bytes()))
        for p in self.root.glob("b3-*.json"):
            self.assertEqual(p.stat().st_mode&0o777,0o600);self.assertNotIn(PASSWORD,p.read_text())
        for ledger in (j,m._TargetLedger(f)):
            with self.assertRaises(m.B3Error):ledger.claim("central","8"*64)
        j.claim("identity","9"*64)
        with self.assertRaises(m.B3Error): j.append("identity","START_CLAIMED")
    def test_fsync_unknown_is_permanent_and_keeps_evidence(self):
        f=self.files();j=m._TargetLedger(f)
        with patch.object(m.os,"fsync",side_effect=OSError("synthetic failure")):
            with self.assertRaises(m.B3Error):j.claim("central","8"*64)
        self.assertEqual(j._states["central"],"UNKNOWN")
        self.assertTrue((self.root/"b3-central-000.json").exists())
        self.assertTrue(j._retained_fds)
        self.assertTrue(hasattr(m,"_UNKNOWN_JOURNALS"),"unknown journal needs module-owned strong retention")
        self.assertIn(j,m._UNKNOWN_JOURNALS)
        with self.assertRaises(m.B3Error):j.claim("central","8"*64)
        with self.assertRaises(m.B3Error):j.append("central","PREPARED")
    def test_concurrent_claims_one_winner_and_no_overwrite(self):
        custodies=(self.files(),self.files());barrier=threading.Barrier(2); results=[]
        ledgers=[m._TargetLedger(f) for f in custodies]
        def work(ledger):
            barrier.wait()
            try:ledger.claim("central","8"*64);results.append("ok")
            except m.B3Error:results.append("rejected")
        threads=[threading.Thread(target=work,args=(ledger,)) for ledger in ledgers]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(sorted(results),["ok","rejected"])
        self.assertEqual(sum("central" in ledger._states for ledger in ledgers),1)
        self.assertFalse(any(ledger._unknown for ledger in ledgers))
    def test_ledger_tamper_and_closed_custody_fail_before_more_io(self):
        f=self.files();j=m._TargetLedger(f);j.claim("central","8"*64)
        (self.root/"b3-central-000.json").write_text("{}")
        with self.assertRaises(m.B3Error):j.append("central","PREPARED")
        f.close();f.close()
        self.assertIsNotNone(f._fd) # unknown keeps private evidence custody
        other=self.files();other.close();other.close()
        with self.assertRaises(m.B3Error):other.verify()

if __name__ == "__main__":unittest.main()
