"""No actors: stdlib tempfiles and a private Popen seam only."""
import dataclasses
import errno
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

SRC = Path(__file__).resolve().parents[1] / "src" / "platform_service"
for name, path in (("platform_service", SRC), ("platform_service.media_backup_b3", SRC / "media_backup_b3")):
    if name not in sys.modules:
        m = types.ModuleType(name); m.__path__ = [str(path)]; sys.modules[name] = m
try:
    pr = importlib.import_module("platform_service.media_backup_b3.parser_runner")
except ModuleNotFoundError as e:
    if e.name != "platform_service.media_backup_b3.parser_runner": raise
    pr = None

CID = "a" * 64
IMAGE = "sha256:" + "b" * 64
TOOL = "c" * 64

def canonical(v):
    return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()

def inspect_fixture():
    return {"Id": CID, "Image": IMAGE, "RestartCount": 0,
        "Config": {"Labels": {"purpose": "b3-dump-parser", "operation_nonce": "toy_nonce"},
            "User": "999:999", "Entrypoint": ["/bin/sleep"], "Cmd": ["100"],
            "Env": ["PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "LANG=C", "PGDATA=/var/lib/postgresql/data"],
            "Volumes": {"/var/lib/postgresql/data": {}}, "ExposedPorts": {"5432/tcp": {}}},
        "HostConfig": {"NetworkMode": "none", "ReadonlyRootfs": True, "Privileged": False,
            "CapDrop": ["ALL"], "CapAdd": None, "SecurityOpt": ["no-new-privileges=true"],
            "NanoCpus": 1000000000, "Memory": 536870912, "MemorySwap": 536870912,
            "PidsLimit": 32, "Binds": None, "Devices": [], "DeviceRequests": None,
            "PortBindings": {}, "PublishAllPorts": False, "PidMode": "", "IpcMode": "private",
            "VolumesFrom": None, "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
            "Tmpfs": {"/var/lib/postgresql/data": "rw,noexec,nosuid,nodev,size=1m,uid=999,gid=999,mode=0700"}},
        "Mounts": [], "NetworkSettings": {"Ports": {"5432/tcp": None}, "Networks": {"none": {}}},
        "State": {"Pid": 123, "StartedAt": "2026-11-03T00:00:00Z", "Running": True,
            "Paused": False, "Restarting": False, "OOMKilled": False, "Dead": False}}

class FakeProcess:
    def __init__(self, rc=0): self.rc=rc; self.pid=12345
    def poll(self): return self.rc
    def wait(self, timeout=None):
        if self.rc is None: raise TimeoutError()
        return self.rc

class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(pr, "concrete parser runner must exist")
        self.assertIn("docker_config_dir",pr.ParserAuthority.__dataclass_fields__,"independent empty Docker config authority is required")
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name).resolve(); os.chmod(self.base, 0o700)
        self.dirfd=os.open(self.base, os.O_RDONLY|os.O_DIRECTORY); self.addCleanup(os.close,self.dirfd)
        self.config=self.base/"docker-config"; self.config.mkdir(mode=0o700)
        self.docker=self.base/"docker"; self.docker.write_bytes(b"toy never executed"); self.docker.chmod(0o700)
        self.inputs=[]
        for name in ("central", "identity"):
            path=self.base/name; path.write_bytes(b"toy archive"); path.chmod(0o600)
            fd=os.open(path,os.O_RDONLY); self.inputs.append(fd); self.addCleanup(os.close,fd)
        self.snapshot=inspect_fixture(); self.calls=[]; self.behavior=None
        self.term=patch.object(pr,"_terminate_local"); self.term.start(); self.addCleanup(self.term.stop)
        self.spawn=patch.object(pr,"_popen",self.fake_spawn); self.spawn.start(); self.addCleanup(self.spawn.stop)
        self.authority=self.auth(); self.runner=pr.NetworkNoneParserRunner(self.authority)

    def auth(self, **kw):
        now=time.monotonic()
        values=dict(cid=CID, nonce="toy_nonce", image_id=IMAGE, docker_path=str(self.docker),
            docker_sha256=hashlib.sha256(self.docker.read_bytes()).hexdigest(),
            socket="unix:///private/tmp/toy-docker.sock", docker_config_dir=str(self.config), pg_restore_sha256=TOOL,
            expected_inspect=canonical(self.snapshot), expected_inspect_sha256=hashlib.sha256(canonical(self.snapshot)).hexdigest(),
            work_dir_fd=self.dirfd, uid=os.geteuid(), gid=os.getegid(), started_at=now-1, deadline=now+100,
            reserved_bytes=128*1024**2, minimum_free_bytes=0)
        values.update(kw); return pr.ParserAuthority(**values)

    def fake_spawn(self, argv, **kw):
        self.calls.append((argv,kw))
        if self.behavior:
            custom=self.behavior(argv,kw)
            if custom is not None: return custom
        if "inspect" in argv: payload=canonical([self.snapshot])
        elif "/usr/bin/sha256sum" in argv: payload=(TOOL+"  /usr/lib/postgresql/16/bin/pg_restore\n").encode()
        else: payload=b"toy output"
        os.write(kw["stdout"],payload)
        return FakeProcess()

    def test_constructor_has_no_operations_and_immutable_authority(self):
        self.assertEqual(self.calls,[])
        with self.assertRaises(dataclasses.FrozenInstanceError): self.authority.deadline+=1
        for changes in ({"expected_inspect": self.snapshot}, {"cid": "a"*12}, {"image_id": "postgres:16"},
                        {"deadline":float("inf")}, {"uid":True}, {"socket":"tcp://host"}, {"nonce":[]},
                        {"expected_inspect": b'{"Id":1,"Id":2}'}):
            with self.subTest(changes=changes), self.assertRaises(pr.B3Error): self.auth(**changes)

    def test_six_slots_fixed_commands_clean_env_opaque_proof(self):
        for kind,fd in zip(("central","identity"), self.inputs):
            for mode in ("toc","schema","data"):
                out=self.runner.run(kind,mode,fd)
                self.assertEqual(self.runner.read(out),b"toy output")
                self.assertTrue(out.terminal_observed); self.assertEqual(out.mode,mode)
                self.assertNotIn("toy output",repr(out))
                with self.assertRaises(pr.B3Error): self.runner.read({"mode":mode})
        dumps=[(a,k) for a,k in self.calls if "/usr/lib/postgresql/16/bin/pg_restore" in a and "/usr/bin/sha256sum" not in a]
        self.assertEqual(len(dumps),6)
        for argv,kw in self.calls:
            self.assertEqual(argv[:5],[str(self.docker),"-H",self.authority.socket,"--config",str(self.config)])
            self.assertEqual(kw["env"],{"PATH":"/usr/bin:/bin","LANG":"C","LC_ALL":"C","HOME":str(self.config)})
            self.assertTrue(kw["start_new_session"]); self.assertTrue(kw["close_fds"])
            self.assertNotIn("--dbname",argv)
            if "exec" in argv:
                self.assertIn("LC_ALL=C",argv); self.assertIn("LANG=C",argv)
        self.assertIn("--list",dumps[0][0]); self.assertIn("--schema-only",dumps[1][0]); self.assertIn("--data-only",dumps[2][0])
        self.assertEqual(os.lseek(self.inputs[0],0,os.SEEK_CUR),0)
        with self.assertRaises(pr.B3Error): self.runner.run("central","toc",self.inputs[0])

    def test_input_identity_slot_and_permissions(self):
        self.runner.run("central","toc",self.inputs[0])
        with self.assertRaises(pr.B3Error): self.runner.run("identity","toc",self.inputs[0])
        self.assertEqual(len([a for a,k in self.calls if "exec" in a]),2)
        for case in ("badmode","badkind","badperm","badinput"):
            r=pr.NetworkNoneParserRunner(self.auth())
            if case=="badperm": os.chmod(self.base/"central",0o644)
            with self.assertRaises(pr.B3Error): r.run("wrong" if case=="badkind" else "central", "other" if case=="badmode" else "toc", -1 if case=="badinput" else self.inputs[0])
            os.chmod(self.base/"central",0o600)

    def test_inspect_closed_baseline_and_exact_drift(self):
        mutations=[("HostConfig","NetworkMode","bridge"),("HostConfig","Privileged",True),
                   ("HostConfig","Memory",1),("HostConfig","CapAdd",["SYS_ADMIN"]),
                   ("HostConfig","Binds",["/tmp:/tmp"]),("Config","User","0"),
                   ("NetworkSettings","Ports",{"5432/tcp":[{"HostPort":"123"}]}),
                   ("NetworkSettings","Networks",{"none":{},"host":{}}),
                   ("Config","Labels",{"purpose":"source","operation_nonce":"toy_nonce"})]
        for section,key,value in mutations:
            old=self.snapshot[section][key]; self.snapshot[section][key]=value
            with self.subTest(key=key), self.assertRaises(pr.B3Error): self.auth()
            self.snapshot[section][key]=old
        self.snapshot["State"]["Pid"]=124
        with self.assertRaises(pr.B3Error): self.runner.run("central","toc",self.inputs[0])
        self.assertFalse(any("exec" in a for a,k in self.calls))

    def test_tool_hash_mismatch_and_nonzero_keep_remote_unknown(self):
        def badhash(a,k):
            if "/usr/bin/sha256sum" in a: os.write(k["stdout"],b"wrong"); return FakeProcess()
        self.behavior=badhash
        with self.assertRaises(pr.B3Error): self.runner.run("central","toc",self.inputs[0])
        self.assertEqual(self.runner.remote_state,"TERMINAL")
        r=pr.NetworkNoneParserRunner(self.auth())
        def nonzero(a,k): return FakeProcess(2) if "exec" in a else None
        self.behavior=nonzero
        with self.assertRaises(pr.B3Error): r.run("central","toc",self.inputs[0])
        self.assertEqual(r.remote_state,"UNKNOWN"); self.assertIsNotNone(r._active_process)
        with self.assertRaises(pr.B3Error): r.run("central","schema",self.inputs[0])

    def test_caps_cancel_deadline_unknown_and_evidence(self):
        for stream,amount in (("stdout",1024**2+1),("stderr",65537)):
            r=pr.NetworkNoneParserRunner(self.auth())
            def over(a,k):
                if "/usr/lib/postgresql/16/bin/pg_restore" in a and "/usr/bin/sha256sum" not in a:
                    os.write(k[stream],b"x"*amount); return FakeProcess()
            self.behavior=over
            with self.assertRaises(pr.B3Error): r.run("central","toc",self.inputs[0])
            self.assertTrue(list(self.base.glob("parser-*")))
        ev=threading.Event(); ev.set()
        r=pr.NetworkNoneParserRunner(self.auth(cancellation=ev)); before=len(self.calls)
        with self.assertRaises(pr.B3Error): r.run("central","toc",self.inputs[0])
        self.assertEqual(before,len(self.calls))
        r=pr.NetworkNoneParserRunner(self.auth(started_at=time.monotonic()-10,deadline=time.monotonic()-1))
        with self.assertRaises(pr.B3Error): r.run("central","toc",self.inputs[0])
        r=pr.NetworkNoneParserRunner(self.auth())
        def unknown(a,k):
            if "exec" in a: r._authority.cancellation.set(); return FakeProcess(None)
        self.behavior=unknown
        with patch.object(pr,"_terminate_local"):
            with self.assertRaises(pr.B3Error): r.run("central","toc",self.inputs[0])
        self.assertEqual(r.remote_state,"UNKNOWN"); self.assertIsNotNone(r._active_process)
        self.assertEqual(os.fstat(self.inputs[0]).st_size,11)

    def test_host_namespace_and_device_drift_rejected(self):
        for key,value in (("UTSMode","host"),("UsernsMode","host"),("DeviceCgroupRules",["a *:* rwm"]),
                          ("ExtraHosts",["host:1.2.3.4"]),("Links",["source"]),("Runtime","nvidia")):
            self.snapshot["HostConfig"][key]=value
            with self.subTest(key=key), self.assertRaises(pr.B3Error): self.auth()
            del self.snapshot["HostConfig"][key]

    def test_symlink_hardlink_tool_and_wrong_uid(self):
        alias=self.base/"alias"; alias.symlink_to(self.docker)
        r=pr.NetworkNoneParserRunner(self.auth(docker_path=str(alias)))
        with self.assertRaises(pr.B3Error): r.run("central","toc",self.inputs[0])
        os.link(self.base/"central",self.base/"hardlink")
        with self.assertRaises(pr.B3Error): self.runner.run("central","toc",self.inputs[0])
        self.assertEqual(self.calls,[])
        r=pr.NetworkNoneParserRunner(self.auth(uid=os.geteuid()+1))
        with self.assertRaises(pr.B3Error): r.run("identity","toc",self.inputs[1])

    def test_failure_before_spawn_and_interrupt_after_spawn(self):
        def before(a,k): raise OSError("secret path")
        self.behavior=before
        with self.assertRaisesRegex(pr.B3Error,"^parser operation failed; evidence retained$"):
            self.runner.run("central","toc",self.inputs[0])
        self.assertEqual(self.runner.remote_state,"NOT_STARTED")
        class Interrupted(FakeProcess):
            def poll(self): raise KeyboardInterrupt("private")
        r=pr.NetworkNoneParserRunner(self.auth())
        self.behavior=lambda a,k: Interrupted() if "exec" in a else None
        with self.assertRaises(pr.B3Error): r.run("central","toc",self.inputs[0])
        self.assertEqual(r.remote_state,"UNKNOWN"); self.assertIsNotNone(r._active_process)
        self.assertIsNotNone(r._active_command)

    def test_concurrent_admission_fails_closed(self):
        self.runner._lock.acquire()
        try:
            with self.assertRaises(pr.B3Error): self.runner.run("central","toc",self.inputs[0])
        finally: self.runner._lock.release()
        with self.assertRaises(pr.B3Error): self.runner.run("identity","schema",self.inputs[1])
        self.assertEqual(self.calls,[])

    def test_output_proof_nonpickle_and_identity_bound(self):
        import pickle
        out=self.runner.run("central","toc",self.inputs[0])
        with self.assertRaises(pr.B3Error): pickle.dumps(out)
        forged=dataclasses.replace(out)
        with self.assertRaises(pr.B3Error): self.runner.read(forged)
        with self.assertRaises(pr.B3Error): pr.NetworkNoneParserRunner(self.auth()).read(out)

    def test_original_deadline_during_live_child_and_no_extension(self):
        authority=self.auth(); r=pr.NetworkNoneParserRunner(authority)
        clock=[authority.started_at+1]
        class Live(FakeProcess):
            def poll(self):
                clock[0]=authority.deadline+1
                return None
        self.behavior=lambda a,k: Live(None) if "exec" in a else None
        with patch.object(pr.time,"monotonic",side_effect=lambda:clock[0]), patch.object(pr.time,"sleep"):
            with self.assertRaises(pr.B3Error): r.run("central","toc",self.inputs[0])
        self.assertEqual(r._authority.deadline,authority.deadline)
        self.assertEqual(r.remote_state,"UNKNOWN")
        before=len(self.calls)
        with self.assertRaises(pr.B3Error): r.run("identity","toc",self.inputs[1])
        self.assertEqual(len(self.calls),before)

    def test_same_kind_different_input_and_post_inspect_drift(self):
        self.runner.run("central","toc",self.inputs[0])
        with self.assertRaises(pr.B3Error): self.runner.run("central","schema",self.inputs[1])
        r=pr.NetworkNoneParserRunner(self.auth())
        def change_after(a,k):
            if "/usr/lib/postgresql/16/bin/pg_restore" in a and "/usr/bin/sha256sum" not in a:
                self.snapshot["State"]["Pid"]+=1
        self.behavior=change_after
        with self.assertRaises(pr.B3Error): r.run("identity","data",self.inputs[1])
        self.assertEqual(r._outputs,{})

    def test_capacity_rejected_and_authority_constructor_no_file_operations(self):
        with patch.object(pr.os,"open",side_effect=AssertionError("unexpected IO")), patch.object(pr.os,"fstat",side_effect=AssertionError("unexpected IO")):
            pr.NetworkNoneParserRunner(self.authority)
        class NoSpace:
            f_bavail=0; f_frsize=4096
        with patch.object(pr.os,"fstatvfs",return_value=NoSpace()):
            with self.assertRaises(pr.B3Error): self.runner.run("central","toc",self.inputs[0])
        self.assertEqual(self.calls,[])

    def test_private_explicit_docker_config_must_stay_empty(self):
        (self.config/"config.json").write_bytes(b"private")
        with self.assertRaises(pr.B3Error): self.runner.run("central","toc",self.inputs[0])
        self.assertEqual(self.calls,[])
        (self.config/"config.json").unlink()
        alias=self.base/"config-link"; alias.symlink_to(self.config)
        r=pr.NetworkNoneParserRunner(self.auth(docker_config_dir=str(alias)))
        with self.assertRaises(pr.B3Error): r.run("central","toc",self.inputs[0])
        r=pr.NetworkNoneParserRunner(self.auth(docker_config_dir=str(self.base)))
        with self.assertRaises(pr.B3Error): r.run("central","toc",self.inputs[0])
        r=pr.NetworkNoneParserRunner(self.auth())
        def client_write(a,k): (self.config/"unexpected").write_bytes(b"x")
        self.behavior=client_write
        with self.assertRaises(pr.B3Error): r.run("central","toc",self.inputs[0])
        self.assertTrue((self.config/"unexpected").exists())

    def test_full_capture_survives_short_regular_file_reads(self):
        read=os.read
        with patch.object(pr.os,"read",side_effect=lambda fd,n:read(fd,min(n,3))):
            try: out=self.runner.run("central","data",self.inputs[0])
            except pr.B3Error: self.fail("full bounded capture must tolerate short regular-file reads")
        self.assertEqual(self.runner.read(out),b"toy output")

    def test_scratch_validation_failure_closes_owned_fds_and_retains_evidence(self):
        # Exercise stdout-first and stderr-second failures through public admission.
        # Both metadata acquisition and failed metadata validation own a real OS FD.
        real_open, real_fstat = os.open, os.fstat
        for allocation in (1, 2):
            for failure in ("fstat", "mode"):
                with self.subTest(allocation=allocation, failure=failure):
                    runner = pr.NetworkNoneParserRunner(self.auth())
                    opened = []
                    def tracked_open(path, *args, **kwargs):
                        fd = real_open(path, *args, **kwargs)
                        if isinstance(path, str) and path.startswith("parser-"):
                            opened.append((fd, path))
                        return fd
                    def rejected_metadata(fd):
                        if len(opened) == allocation and fd == opened[-1][0]:
                            if failure == "fstat":
                                raise OSError("injected scratch metadata failure")
                            fields = list(real_fstat(fd))
                            fields[0] = (fields[0] & ~0o777) | 0o644
                            return os.stat_result(fields)
                        return real_fstat(fd)
                    before = len(self.calls)
                    try:
                        with patch.object(pr.os, "open", side_effect=tracked_open), patch.object(pr.os, "fstat", side_effect=rejected_metadata):
                            with self.assertRaises(pr.B3Error):
                                runner.run("central", "toc", self.inputs[0])
                        self.assertEqual(len(opened), allocation)
                        self.assertEqual(len(self.calls), before)
                        self.assertEqual(runner.remote_state, "NOT_STARTED")
                        self.assertEqual(runner._scratch, [name for _, name in opened])
                        for _, name in opened:
                            self.assertTrue((self.base / name).is_file())
                        with self.assertRaises(pr.B3Error):
                            runner.run("identity", "schema", self.inputs[1])
                        self.assertEqual(len(self.calls), before)
                        self.assertEqual(real_fstat(self.inputs[0]).st_size, 11)
                        self.assertEqual(real_fstat(self.inputs[1]).st_size, 11)
                        for fd, _ in opened:
                            with self.assertRaises(OSError) as closed:
                                real_fstat(fd)
                            self.assertEqual(closed.exception.errno, errno.EBADF)
                    finally:
                        # RED execution must not itself leak a deliberately exposed FD.
                        for fd, _ in opened:
                            try: real_fstat(fd)
                            except OSError: pass
                            else: os.close(fd)

    def test_before_popen_failure_and_retained_evidence(self):
        self.docker.write_bytes(b"changed")
        with self.assertRaises(pr.B3Error): self.runner.run("central","toc",self.inputs[0])
        self.assertEqual(self.calls,[])
        self.assertEqual(self.runner.remote_state,"NOT_STARTED")
        self.assertFalse(hasattr(self.runner,"close"))

if __name__=="__main__": unittest.main()
