"""Pure simulated worker checks; never invoke native entrypoint or kernel IO."""
from test_media_backup_b3_network_fixtures import PROFILE, SECURITY, observation, network_evidence
import copy
import errno
import dataclasses
import hashlib
import importlib
import json
import os as native_os
from pathlib import Path
import stat
import sys
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1] / "src" / "platform_service"
for name, path in (("platform_service", ROOT), ("platform_service.media_backup_b3", ROOT / "media_backup_b3")):
    if name not in sys.modules:
        package = types.ModuleType(name)
        package.__path__ = [str(path)]
        sys.modules[name] = package
c = importlib.import_module("platform_service.media_backup_b3.execution_contract")
NAME = "platform_service.media_backup_b3.execution_worker"
SOURCE = ROOT / "media_backup_b3" / "execution_worker.py"


def _forbid_external_calls(event, args):
    if event.startswith(("socket.", "subprocess.")) or event in ("os.system", "os.fork", "os.posix_spawn", "os.exec"):
        raise AssertionError("external calls prohibited in simulated worker tests")


def policy(**kw):
    b = c.ExecutionBinding("job", "op", "central", c.Operation.ATTEST_WORKER,
        "1"*64, "2"*64, "3"*64, "4"*64, "5"*64, 0, 1800*10**9, 0, 1)
    d = dict(binding=b, worker_image_id="sha256:"+"6"*64,
        worker_artifact_sha256="7"*64, dependency_manifest_sha256="8"*64,
        entrypoint_sha256="9"*64, pg_restore_sha256="a"*64, architecture="arm64",
        network_profile_sha256=PROFILE,network_security_sha256=SECURITY,
        cpu_nanos=10**9, memory_bytes=512*1024**2, memory_swap_bytes=512*1024**2,
        pids_limit=64, tmpfs_bytes=1024**2, envelope_limit=65536,
        private_result_limit=1024**2, diagnostic_limit=65536)
    d.update(kw)
    return c.ExecutionPolicy(**d)


class WorkerTests(unittest.TestCase):
    def test_stdout_nonblocking_setup(self):
        with mock.patch.object(self.w.os,"set_blocking") as setter, mock.patch.object(self.w.os,"get_blocking",return_value=False), mock.patch.object(self.w,"_wait"), mock.patch.object(self.w.os,"write",return_value=5):
            self.w._write_result(b"\x00\x00\x00\x01x",1,10**9)
        setter.assert_called_once_with(1,False)

    def setUp(self):
        self.assertTrue(SOURCE.is_file(), "actual fixed worker module has not been implemented")
        self.w = importlib.import_module(NAME)
        self.clock = mock.patch.object(self.w.time, "monotonic_ns", return_value=1)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        reader = mock.patch.object(self.w,"observe_worker_network",return_value=observation())
        reader.start(); self.addCleanup(reader.stop)
        os_copy = types.SimpleNamespace(**vars(self.w.os))
        patcher=mock.patch.object(self.w,"os",os_copy)
        patcher.start(); self.addCleanup(patcher.stop)
        for obj, names in ((self.w.os, ("open","stat","lstat","listdir","readlink","fstat","read","write","set_blocking",
                "getuid","geteuid","getgid","getegid","getgroups")), (self.w.platform,("machine",))):
            for name in names:
                patcher=mock.patch.object(obj,name,side_effect=AssertionError("unpatched native seam: "+name))
                patcher.start(); self.addCleanup(patcher.stop)
        self.w.os.set_blocking = mock.Mock()
        self.w.os.get_blocking = mock.Mock(return_value=False)

    def rejects(self, call, *args):
        with self.assertRaises(c.B3Error): call(*args)

    def test_fixed_interface_and_import_no_io(self):
        w = self.w
        for argv, platform, tty, executable in ((["worker"], "linux", False, w._INTERPRETER),
                (["worker", "attest_worker", "x"], "linux", False, w._INTERPRETER),
                (["worker", "attest_worker"], "darwin", False, w._INTERPRETER),
                (["worker", "attest_worker"], "linux", True, w._INTERPRETER),
                (["worker", "attest_worker"], "linux", False, "/usr/bin/python")):
            with mock.patch.object(sys, "argv", argv), mock.patch.object(sys, "platform", platform), \
                 mock.patch.object(sys, "executable", executable), mock.patch.object(w.os, "isatty", return_value=tty):
                self.rejects(w._invocation)
        code = compile(SOURCE.read_bytes(), str(SOURCE), "exec")
        with mock.patch.object(native_os, "open", side_effect=AssertionError("IO at import")), \
             mock.patch.object(native_os, "stat", side_effect=AssertionError("IO at import")), \
             mock.patch.object(native_os, "listdir", side_effect=AssertionError("IO at import")), \
             mock.patch.object(w.time, "monotonic_ns", side_effect=AssertionError("clock at import")):
            exec(code, {"__name__":NAME+"_probe", "__package__":"platform_service.media_backup_b3", "__file__":str(SOURCE)})

    def test_frame_fragmented_and_partial_output(self):
        w = self.w; raw = c.encode_attest_request(policy(), 10**9)
        chunks = [bytes([x]) for x in raw] + [b""]
        calls = []
        def read(fd, n):
            calls.append(n); return chunks.pop(0)
        with mock.patch.object(w.os, "read", side_effect=read), mock.patch.object(w.select, "select", return_value=([0],[1],[])):
            self.assertEqual(w._read_frame(10**9), raw)
        self.assertLessEqual(max(calls), 16384)
        written = bytearray()
        def write(fd, data):
            n = min(len(data), 3); written.extend(data[:n]); return n
        with mock.patch.object(w.os, "write", side_effect=write), mock.patch.object(w.select, "select", return_value=([],[1],[])):
            w._write_result((8).to_bytes(4,"big")+b"abcdefgh", 8, 10**9)
        self.assertEqual(written, (8).to_bytes(4,"big")+b"abcdefgh")

    def test_frame_rejections_and_deadline(self):
        w=self.w
        for chunks in ([b""], [b"\x00" , b""], [(65537).to_bytes(4,"big")], [b"\x00"*4],
                [(3).to_bytes(4,"big"), b"a", b""], [(1).to_bytes(4,"big"),b"a",b"x"]):
            with mock.patch.object(w.os,"read",side_effect=chunks), mock.patch.object(w.select,"select",return_value=([0],[],[])):
                self.rejects(w._read_frame, 10**9)
        with mock.patch.object(w.select,"select",return_value=([],[],[])):
            self.rejects(w._read_frame, 10**9)
        self.rejects(w._check,1)
        self.rejects(w._write_result,b"x",0,10**9)

    def test_manifest_shape_and_hash_before_json(self):
        w=self.w
        with mock.patch.object(w,"_immutable_bytes",return_value=b"private-canary"), \
             mock.patch.object(w.json,"loads",side_effect=AssertionError("parsed before hash")):
            self.rejects(w._load_build,policy(),10**9)
        fixture=self.build()
        w._validate_build(fixture,policy(),10**9)
        for key in fixture:
            bad=copy.deepcopy(fixture); del bad[key]
            self.rejects(w._validate_build,bad,policy(),10**9)
        for key,value in (("extra",1),("architecture","amd64"),("artifact_path","/tmp/a"),
                ("entrypoint_path","/opt/easel/../worker.py"),("allowed_fileless_modules",["x","x"]),
                ("files",{}),("files",{"/opt/easel/worker-build.json":dict(sha256="1"*64,size=1,mode=420)})):
            self.rejects(w._validate_build,{**fixture,key:value},policy(),10**9)
        for field,value in (("mode",True),("mode",438),("size",True),("size",512*1024**2+1),("sha256","Z"*64)):
            bad=copy.deepcopy(fixture); bad["files"][fixture["artifact_path"]][field]=value
            self.rejects(w._validate_build,bad,policy(),10**9)

    def build(self):
        w=self.w
        paths=[w._ARTIFACT,"/opt/easel/worker.py","/usr/local/bin/python3.13",w._PG]
        d=dict(format="easel.b3.worker-build.v1", architecture="arm64", artifact_path=paths[0],
            artifact_sha256="7"*64,entrypoint_path=paths[1],entrypoint_sha256="9"*64,
            executable_path=paths[2],executable_sha256="b"*64,pg_restore_path=paths[3],pg_restore_sha256="a"*64,
            files={},allowed_fileless_modules=[])
        for path,h in zip(paths,("7","9","b","a")):
            d["files"][path]=dict(sha256=h*64,size=1,mode=420)
        return d

    def test_paths_and_strict_json(self):
        w=self.w
        for path in ("/tmp/a","/opt/easel//a","/opt/easel/./a","/opt/easel/../a","/opt/easel/a b","/opt/easel/é","/opt/easel/a/", "/usr/library/a"):
            self.rejects(w._path,path)
        for raw in (b'{"x":1,"x":1}', b'{"x":1.1}', b'{"x":NaN}', b'{"x":"\\ud800"}',b'{"x":"\\u0000"}', b'['*100+b']'*100):
            self.rejects(w._json,raw)

    def test_immutable_stream_uses_actual_hash_and_detects_mutation(self):
        w=self.w
        s=types.SimpleNamespace(st_dev=1,st_ino=2,st_mode=stat.S_IFREG|420,st_uid=0,st_gid=0,
            st_nlink=2,st_size=3,st_mtime_ns=1,st_ctime_ns=1)
        def run(data, after=s, expected=None):
            with mock.patch.object(w,"_open_immutable",return_value=(8,9,"a")), \
                 mock.patch.object(w.os,"fstat",side_effect=[s,after]), mock.patch.object(w.os,"stat",return_value=after), \
                 mock.patch.object(w.os,"close"), mock.patch.object(w.os,"read",side_effect=data):
                return w._immutable_bytes("/opt/easel/a",4,10**9,expected)
        self.assertEqual(run([b"a",b"bc",b""]),b"abc")
        for data in ([b"ab",b""],[b"abcd",b""]): self.rejects(run,data)
        changed=copy.copy(s); changed.st_ino=3
        self.rejects(run,[b"abc",b""],changed)
        self.rejects(run,[b"abc",b""],s,dict(size=3,mode=420,sha256="0"*64))
        for field,value in (("st_uid",10001),("st_gid",1),("st_mode",stat.S_IFREG|438),("st_mode",stat.S_IFLNK|420),("st_nlink",0)):
            bad=copy.copy(s); setattr(bad,field,value)
            self.rejects(w._regular,bad)

    def kernel(self):
        return {
            "/proc/self/status": b"Uid: 10001 10001 10001 10001\nGid: 10001 10001 10001 10001\nGroups: 10001\nCapInh: 0000\nCapPrm: 0000\nCapEff: 0000\nCapBnd: 0000\nCapAmb: 0000\nNoNewPrivs: 1\n",
            "/proc/net/route":b"Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\n",
            "/proc/net/ipv6_route":b"",
            "/proc/self/cgroup":b"0::/\n",
            "/sys/fs/cgroup/memory.max":b"536870912\n",
            "/sys/fs/cgroup/memory.swap.max":b"0\n",
            "/sys/fs/cgroup/pids.max":b"64\n",
            "/sys/fs/cgroup/cpu.max":b"100000 100000\n"}

    def observe(self, data=None, ids=10001, names=None, ns=None, observe_call=None):
        w=self.w; data=data or self.kernel()
        with mock.patch.object(w,"_kernel_bytes",side_effect=lambda path,cap,deadline:data[path]), \
             mock.patch.object(w.os,"getuid",return_value=ids), mock.patch.object(w.os,"geteuid",return_value=ids), \
             mock.patch.object(w.os,"getgid",return_value=ids), mock.patch.object(w.os,"getegid",return_value=ids), \
             mock.patch.object(w.os,"getgroups",return_value=[10001]), \
             mock.patch.object(w.os,"stat",return_value=ns or types.SimpleNamespace(st_dev=observation().namespace_before.device,st_ino=observation().namespace_before.inode)), \
             mock.patch.object(w.os,"listdir",return_value=names or ["lo"]):
            return (observe_call or w._observe_security)(policy(),10**9)

    def test_kernel_complete_profile(self):
        self.assertEqual(self.observe()["worker_netns"],network_evidence()["network_security_snapshot"]["namespace"])
        with mock.patch.object(self.w,"observe_worker_network",return_value=dataclasses.replace(observation(),kernel_release="unsupported")):
            self.rejects(self.observe)

    def test_kernel_failure_table(self):
        base=self.kernel()
        changes={"/proc/self/status":[base["/proc/self/status"]+b"CapEff: 0\n",base["/proc/self/status"].replace(b"CapEff: 0000",b"CapEff: 0001"),base["/proc/self/status"].replace(b"NoNewPrivs: 1",b"NoNewPrivs: 0"),base["/proc/self/status"].replace(b"Groups: 10001",b"Groups: 0")],
            "/proc/self/cgroup":[b"0::/other\n",b"1:cpu:/\n"],
            "/sys/fs/cgroup/memory.max":[b"max",b"536870913"],
            "/sys/fs/cgroup/memory.swap.max":[b"1"], "/sys/fs/cgroup/pids.max":[b"065",b"65"],
            "/sys/fs/cgroup/cpu.max":[b"100001 100000",b"max 100000",b"0 1"]}
        for path,values in changes.items():
            for value in values:
                data=copy.copy(base); data[path]=value
                with self.subTest(path=path,value=value): self.rejects(self.observe,data)
        self.rejects(self.observe,None,0)
        self.rejects(self.observe,None,10001,None,types.SimpleNamespace(st_dev=0,st_ino=1))

    def test_origins_native_fileless_pyc_and_drift(self):
        w=self.w; path="/opt/easel/a.py"; hashes={path:"a"*64}
        module=types.SimpleNamespace(__file__=path,__spec__=types.SimpleNamespace(origin=path),__cached__=None)
        modules={"a":module}
        with mock.patch.object(w.sys,"modules",modules), mock.patch.object(w,"_kernel_bytes",return_value=b""), mock.patch.object(w.os,"lstat",return_value=object()):
            self.assertEqual(len(w._origins(hashes,[],"b"*64,10**9)),64)
            module.__cached__="/opt/easel/a.pyc"; self.rejects(w._origins,hashes,[],"b"*64,10**9)
            module.__cached__=None
            module.__file__="/opt/easel/missing.py"; self.rejects(w._origins,hashes,[],"b"*64,10**9)
        fileless=types.SimpleNamespace(__spec__=None)
        with mock.patch.object(w.sys,"modules",{"generated":fileless}),mock.patch.object(w,"_kernel_bytes",return_value=b""):
            self.rejects(w._origins,{},[],"b"*64,10**9)
            w._origins({},["generated"],"b"*64,10**9)
            self.rejects(w._origins,{},["generated","unused"],"b"*64,10**9)
        with mock.patch.object(w.sys,"modules",modules),mock.patch.object(w,"_kernel_bytes",return_value=b"1000-2000 r-xp 00000000 00:01 1 /opt/easel/missing.so\n"):
            self.rejects(w._origins,hashes,[],"b"*64,10**9)
        module.__file__=path
        def drift(*args): modules["a"]=fileless; return b""
        with mock.patch.object(w.sys,"modules",modules),mock.patch.object(w,"_kernel_bytes",side_effect=drift):
            self.rejects(w._origins,hashes,[],"b"*64,10**9)

    def test_open_walk_rejects_symlink_ancestry_and_nonroot_directories(self):
        w=self.w
        d=types.SimpleNamespace(st_mode=stat.S_IFDIR|493,st_uid=0,st_gid=0)
        bad=types.SimpleNamespace(st_mode=stat.S_IFDIR|511,st_uid=0,st_gid=0)
        with mock.patch.object(w.os,"open",side_effect=[10,11,OSError("symlink")]), \
             mock.patch.object(w.os,"fstat",return_value=d),mock.patch.object(w.os,"close") as close:
            with self.assertRaises(OSError): w._open_immutable("/opt/easel/a",10**9)
            self.assertEqual(close.call_args_list,[mock.call(10),mock.call(11)])
        with mock.patch.object(w.os,"open",return_value=10),mock.patch.object(w.os,"fstat",return_value=bad),mock.patch.object(w.os,"close"):
            self.rejects(w._open_immutable,"/opt/easel/a",10**9)

    def test_load_build_exact_hashes_alias_architecture_and_inventory(self):
        w=self.w; build=self.build(); raw=c._canonical(build)
        p=policy(dependency_manifest_sha256=hashlib.sha256(raw).hexdigest())
        def read(path,cap,deadline,meta): return meta["sha256"]
        with mock.patch.object(w,"_immutable_bytes",return_value=raw),mock.patch.object(w,"__file__",build["entrypoint_path"]), \
             mock.patch.object(sys,"executable",w._INTERPRETER),mock.patch.object(w.os,"readlink",return_value=build["executable_path"]), \
             mock.patch.object(w.platform,"machine",return_value="aarch64"),mock.patch.object(w,"_immutable_read",side_effect=read) as stream, \
             mock.patch.object(w,"_open_parent",return_value=(9,"python")),mock.patch.object(w.os,"close"):
            loaded,hashes,actual,arch=w._load_build(p,10**9)
            self.assertEqual(actual,hashlib.sha256(raw).hexdigest()); self.assertEqual(arch,"arm64")
            self.assertEqual(stream.call_count,4); self.assertEqual(set(hashes),set(build["files"]))
            with mock.patch.object(w.os,"readlink",return_value="../bin/python"):
                self.rejects(w._load_build,p,10**9)
            with mock.patch.object(w.platform,"machine",return_value="x86_64"):
                self.rejects(w._load_build,p,10**9)
            with mock.patch.object(w,"__file__","/opt/easel/other.py"):
                self.rejects(w._load_build,p,10**9)
            with mock.patch.object(sys,"executable","/usr/bin/python"):
                self.rejects(w._load_build,p,10**9)

    def test_kernel_stream_cap_short_chunks_and_interruption(self):
        w=self.w
        with mock.patch.object(w.os,"open",return_value=9),mock.patch.object(w.os,"close"), \
             mock.patch.object(w.os,"read",side_effect=[InterruptedError(),b"a",b"bc",b""]) as read:
            self.assertEqual(w._kernel_bytes("/proc/self/status",3,10**9),b"abc")
            self.assertLessEqual(max(call.args[1] for call in read.call_args_list),4)
        with mock.patch.object(w.os,"open",return_value=9),mock.patch.object(w.os,"close"),mock.patch.object(w.os,"read",return_value=b"abcd"):
            self.rejects(w._kernel_bytes,"/proc/self/status",3,10**9)

    def test_native_maps_valid_and_malformed_deleted_uninventoried(self):
        w=self.w; path="/usr/lib/a.so"; h={path:"a"*64}
        good="1000-2000 r-xp 00000000 00:01 1 "+path+"\n2000-3000 r-xp 00000000 00:00 0 [vdso]\n"
        with mock.patch.object(w.sys,"modules",{}),mock.patch.object(w,"_kernel_bytes",return_value=good.encode()):
            result=w._origins(h,[],"b"*64,10**9); self.assertEqual(len(result),64)
        for text in (good.replace(path,path+" (deleted)"),good.replace(path,"/usr/lib/b.so"),good.replace("1000-2000","3000-1000"),good.replace("[vdso]","[unexpected]"),"bad\n"):
            with mock.patch.object(w.sys,"modules",{}),mock.patch.object(w,"_kernel_bytes",return_value=text.encode()):
                self.rejects(w._origins,h,[],"b"*64,10**9)

    def test_builtin_frozen_namespace_and_absent_cached(self):
        w=self.w
        frozen=types.SimpleNamespace(__file__="/opt/easel/a.py",__spec__=types.SimpleNamespace(origin="frozen"))
        with mock.patch.object(w.sys,"modules",{"a":frozen}),mock.patch.object(w,"_kernel_bytes",return_value=b""),mock.patch.object(c,"_digest",wraps=c._digest) as digest:
            w._origins({"/opt/easel/a.py":"a"*64},[],"b"*64,10**9)
            self.assertIn(["interpreter","a","frozen","b"*64],digest.call_args.args[0])
        namespace=types.SimpleNamespace(__spec__=types.SimpleNamespace(origin=None,submodule_search_locations=["/opt/easel/pkg"]))
        with mock.patch.object(w.sys,"modules",{"pkg":namespace}),mock.patch.object(w,"_kernel_bytes",return_value=b""),mock.patch.object(w,"_namespace_directory") as directory:
            w._origins({"/opt/easel/pkg/a.py":"a"*64},[],"b"*64,10**9)
            directory.assert_called_once()
        with mock.patch.object(w,"_open_immutable",side_effect=AssertionError("must reject empty closure before IO")):
            self.rejects(w._namespace_directory,"/opt/easel/pkg",{},10**9)
        mod=types.SimpleNamespace(__file__="/opt/easel/a.py",__cached__="/opt/easel/a.pyc",__spec__=None)
        with mock.patch.object(w.sys,"modules",{"a":mod}),mock.patch.object(w,"_kernel_bytes",return_value=b""),mock.patch.object(w.os,"lstat",side_effect=FileNotFoundError):
            w._origins({"/opt/easel/a.py":"a"*64},[],"b"*64,10**9)

    def test_attest_exact_binding_and_postcheck_drift(self):
        w=self.w; build=self.build(); hashes={path:meta["sha256"] for path,meta in build["files"].items()}
        p=policy(); raw=c.encode_attest_request(p,10**9); parsed=c.decode_worker_request(raw)
        security=self.observe()
        with mock.patch.object(w,"_observe_security",return_value=security), \
             mock.patch.object(w,"_load_build",return_value=(build,hashes,p.dependency_manifest_sha256,p.architecture)), \
             mock.patch.object(w,"_origins",return_value="c"*64):
            response=w._attest(parsed,10**9)
            c.validate_attestation(p,raw,response)
            changed={**security,"worker_netns":dict(device=1,inode=3)}
            with mock.patch.object(w,"_observe_security",side_effect=[security,changed]):
                self.rejects(w._attest,parsed,10**9)
            altered={**hashes,build["artifact_path"]:"e"*64}
            with mock.patch.object(w,"_load_build",return_value=(build,altered,p.dependency_manifest_sha256,p.architecture)):
                self.rejects(w._attest,parsed,10**9)

    def test_actual_two_reader_passes_bracket_hashing_with_permitted_counters(self):
        from test_media_backup_b3_network_fixtures import varied_observation, RAW
        w=self.w;build=self.build();p=policy();raw=c.encode_attest_request(p,123);parsed=c.decode_worker_request(raw)
        hashes={path:meta["sha256"] for path,meta in build["files"].items()}
        order=[];observations=iter([observation(),varied_observation()]);actual_observe=w._observe_security
        def reader(deadline):order.append("reader");return next(observations)
        def load(*args):order.append("build");return build,hashes,p.dependency_manifest_sha256,p.architecture
        def origins(*args):order.append("origins");return "c"*64
        with mock.patch.object(w,"observe_worker_network",side_effect=reader),mock.patch.object(w,"_observe_security",side_effect=lambda p,d:self.observe(observe_call=actual_observe)),mock.patch.object(w,"_load_build",side_effect=load),mock.patch.object(w,"_origins",side_effect=origins):
            response=w._attest(parsed,10**9)
        self.assertEqual(order,["reader","build","origins","reader"])
        evidence=json.loads(c.validate_attestation(p,raw,response))["evidence"]
        self.assertEqual(evidence["network_before_raw_sha256"],RAW)
        self.assertNotEqual(evidence["network_after_raw_sha256"],RAW)
        self.assertEqual(evidence["network_security_sha256"],SECURITY)
        self.assertNotIn("interfaces",evidence);self.assertNotIn("routes",evidence)

    def test_main_uses_one_local_origin_and_conservative_deadline(self):
        w=self.w; raw=c.encode_attest_request(policy(),1000)
        parsed=c.decode_worker_request(raw)
        with mock.patch.object(w,"_invocation"),mock.patch.object(w,"_read_frame",return_value=raw) as read, \
             mock.patch.object(w,"_attest",return_value=b"response") as attest,mock.patch.object(w,"_write_result") as write, \
             mock.patch.object(w.time,"monotonic_ns",return_value=100):
            self.assertEqual(w.main(),0)
            read.assert_called_once_with(100+w._INITIAL_NS)
            self.assertEqual(attest.call_args.args[1],1100)
            self.assertEqual(write.call_args.args[2],1100)
        with mock.patch.object(w,"_invocation"),mock.patch.object(w,"_read_frame",return_value=raw), \
             mock.patch.object(w,"_attest",side_effect=AssertionError("deadline must reject before attest")), \
             mock.patch.object(w.os,"write",return_value=19) as write, \
             mock.patch.object(w.os,"set_blocking"), \
             mock.patch.object(w.time,"monotonic_ns",side_effect=[100,1100]):
            self.assertEqual(w.main(),1); write.assert_called_once_with(2,b"b3_worker_rejected\n")

    def test_security_snapshot_rejects_allowed_metadata_drift(self):
        first=self.observe(); data=self.kernel()
        data["/proc/self/status"]=data["/proc/self/status"].replace(b"CapEff: 0000",b"CapEff: 00000000")
        second=self.observe(data)
        self.assertNotEqual(first,second,"metadata drift must remain visible for postcheck")

    def test_io_interruption_progress_timeout_and_short_write(self):
        w=self.w
        with mock.patch.object(w.select,"select",side_effect=[InterruptedError(),([0],[],[]),([0],[],[])]), \
             mock.patch.object(w.os,"read",side_effect=[InterruptedError(),b"x"]) as read:
            self.assertEqual(w._read_fd(0,1,10**9),b"x")
            self.assertEqual(read.call_count,2)
        data=(3).to_bytes(4,"big")+b"abc"; seen=[]
        def write(fd,b):
            seen.append(b)
            if len(seen)==1: raise InterruptedError()
            return min(2,len(b))
        with mock.patch.object(w.select,"select",return_value=([],[1],[])),mock.patch.object(w.os,"write",side_effect=write):
            w._write_result(data,3,10**9)
        self.assertEqual(seen[0],seen[1]); self.assertEqual(seen[2],data[2:])
        for n in (0,-1,100):
            with mock.patch.object(w.select,"select",return_value=([],[1],[])),mock.patch.object(w.os,"write",return_value=n):
                self.rejects(w._write_result,data,3,10**9)
        with mock.patch.object(w.select,"select",return_value=([0],[],[])),mock.patch.object(w.os,"read",return_value=b"x"), \
             mock.patch.object(w.time,"monotonic_ns",side_effect=[1,1,1,100]):
            self.rejects(w._read_fd,0,1,100)

    def test_wait_expiry_before_select_never_passes_negative_timeout(self):
        with mock.patch.object(self.w.time,"monotonic_ns",side_effect=[1,100,100]), mock.patch.object(self.w.select,"select") as select:
            self.rejects(self.w._wait,1,True,100)
        select.assert_not_called()

    def test_stdout_large_partial_offset_eintr_eagain(self):
        w=self.w;payload=bytes(range(256))*256;data=len(payload).to_bytes(4,"big")+payload
        output=bytearray();calls=[];steps=[7,InterruptedError(),BlockingIOError(errno.EAGAIN,"full"),13]
        def write(fd, chunk):
            calls.append((len(output),chunk))
            step=steps.pop(0) if steps else min(4001,len(chunk))
            if isinstance(step,BaseException):raise step
            output.extend(chunk[:step]);return step
        with mock.patch.object(w.select,"select",return_value=([],[1],[])),mock.patch.object(w.os,"write",side_effect=write):
            w._write_result(data,len(payload),10**9)
        self.assertEqual(bytes(output),data)
        self.assertEqual(calls[1][0],7);self.assertEqual(calls[1],calls[2]);self.assertEqual(calls[2],calls[3])
        self.assertLessEqual(max(len(chunk) for _,chunk in calls),16384)
        w.os.set_blocking.assert_called_once_with(1,False)

    def test_stdout_eagain_unknown_progress_fatal_errors_and_setup(self):
        w=self.w;data=b"\x00\x00\x00\x01x"
        class Unknown(BlockingIOError):
            characters_written=None
        errors=[BlockingIOError(errno.EAGAIN,"private-canary",1),Unknown(errno.EAGAIN,"private-canary"),BlockingIOError(errno.EPERM,"private-canary"),BrokenPipeError("private-canary")]
        for error in errors:
            with mock.patch.object(w,"_wait"),mock.patch.object(w.os,"write",side_effect=error) as write:
                with self.assertRaises((c.B3Error,OSError)):w._write_result(data,1,100)
                self.assertEqual(write.call_count,1)
        for setter,blocking in ((OSError("private-canary"),False),(None,True),(None,0)):
            with mock.patch.object(w.os,"set_blocking",side_effect=setter),mock.patch.object(w.os,"get_blocking",return_value=blocking),mock.patch.object(w.os,"write") as write:
                with self.assertRaises((c.B3Error,OSError)):w._write_result(data,1,100)
                write.assert_not_called()

    def test_stdout_full_pipe_spurious_readiness_cannot_renew_deadline(self):
        w=self.w;data=b"\x00\x00\x00\x01x"
        for ready in (False,True):
            now=[1];writes=[]
            def select(r,out,x,timeout):
                now[0]+=15
                return ([],[1] if ready else [],[])
            def write(fd,chunk):
                writes.append(chunk)
                raise BlockingIOError(errno.EAGAIN,"full")
            with mock.patch.object(w.time,"monotonic_ns",side_effect=lambda:now[0]),mock.patch.object(w.select,"select",side_effect=select),mock.patch.object(w.os,"write",side_effect=write):
                self.rejects(w._write_result,data,1,40)
            self.assertLessEqual(now[0],46)
            self.assertTrue(all(chunk==data for chunk in writes))

    def test_stdout_deadline_after_setup_and_after_write(self):
        w=self.w;data=b"\x00\x00\x00\x01x";now=[1]
        def setter(fd,value):now[0]=100
        with mock.patch.object(w.time,"monotonic_ns",side_effect=lambda:now[0]),mock.patch.object(w.os,"set_blocking",side_effect=setter),mock.patch.object(w.os,"write") as write:
            self.rejects(w._write_result,data,1,100);write.assert_not_called()
        now[0]=1
        def write(fd,chunk):now[0]=100;return len(chunk)
        with mock.patch.object(w.time,"monotonic_ns",side_effect=lambda:now[0]),mock.patch.object(w,"_wait"),mock.patch.object(w.os,"write",side_effect=write):
            self.rejects(w._write_result,data,1,100)

    def test_build_all_scalar_pins_roots_and_types(self):
        w=self.w; base=self.build()
        for key in ("artifact_sha256","entrypoint_sha256","pg_restore_sha256","executable_sha256"):
            for value in (True,1,"A"*64,"0"*63):
                self.rejects(w._validate_build,{**base,key:value},policy(),10**9)
        for role in ("artifact","entrypoint","pg_restore","executable"):
            bad=copy.deepcopy(base); del bad["files"][bad[role+"_path"]]
            self.rejects(w._validate_build,bad,policy(),10**9)
        for value in (["x.y","a"],["a-b"],["é"],True):
            self.rejects(w._validate_build,{**base,"allowed_fileless_modules":value},policy(),10**9)
        for value in ("/home/a","/run/a","/proc/a","/usr/libx/a","/usr/local//a"):
            self.rejects(w._path,value)

    def test_each_security_restriction_and_equivalent_cpu_ratio(self):
        base=self.kernel()
        for key in (b"CapInh",b"CapPrm",b"CapEff",b"CapBnd",b"CapAmb"):
            for value in (b"0001",b"garbage"):
                data=copy.copy(base); data["/proc/self/status"]=base["/proc/self/status"].replace(key+b": 0000",key+b": "+value)
                self.rejects(self.observe,data)
        for key in (b"Uid",b"Gid"):
            data=copy.copy(base); data["/proc/self/status"]=base["/proc/self/status"].replace(key+b": 10001 10001 10001 10001",key+b": 10001 10001 0 10001")
            self.rejects(self.observe,data)
        data=copy.copy(base); data["/sys/fs/cgroup/cpu.max"]=b"25000 25000\n"
        self.observe(data)
        data["/sys/fs/cgroup/cpu.max"]=b"025000 25000\n"; self.rejects(self.observe,data)

    def test_directory_and_leaf_replacement_stamps_and_verified_pyc(self):
        w=self.w
        s=types.SimpleNamespace(st_dev=1,st_ino=2,st_mode=stat.S_IFREG|420,st_uid=0,st_gid=0,
            st_nlink=1,st_size=3,st_mtime_ns=1,st_ctime_ns=1)
        for key in ("st_dev","st_ino","st_mode","st_uid","st_gid","st_nlink","st_size","st_mtime_ns","st_ctime_ns"):
            changed=copy.copy(s); setattr(changed,key,getattr(s,key)+1)
            with mock.patch.object(w,"_open_immutable",return_value=(8,9,"a")),mock.patch.object(w.os,"close"), \
                 mock.patch.object(w.os,"fstat",return_value=s),mock.patch.object(w.os,"read",side_effect=[b"abc",b""]), \
                 mock.patch.object(w.os,"stat",return_value=changed):
                self.rejects(w._immutable_bytes,"/opt/easel/a",4,10**9)
        d=copy.copy(s); d.st_mode=stat.S_IFDIR|493
        with mock.patch.object(w,"_open_immutable",return_value=(8,9,"pkg")),mock.patch.object(w.os,"close"), \
             mock.patch.object(w.os,"fstat",return_value=d),mock.patch.object(w.os,"stat",return_value=d):
            w._namespace_directory("/opt/easel/pkg",{"/opt/easel/pkg/a.py":"a"*64},10**9)
        mod=types.SimpleNamespace(__file__="/opt/easel/a.py",__cached__="/opt/easel/a.pyc",__spec__=None)
        with mock.patch.object(w.sys,"modules",{"a":mod}),mock.patch.object(w,"_kernel_bytes",return_value=b""),mock.patch.object(c,"_digest",wraps=c._digest) as digest:
            w._origins({"/opt/easel/a.py":"a"*64,"/opt/easel/a.pyc":"c"*64},[],"b"*64,10**9)
            self.assertIn(["/opt/easel/a.pyc","c"*64],digest.call_args.args[0][0][2])

    def test_test_audit_guard_only_direct_entry(self):
        source=compile(Path(__file__).read_bytes(),__file__,"exec")
        with mock.patch.object(sys,"addaudithook") as register:
            exec(source,{"__name__":"worker_test_import_probe","__file__":__file__})
        register.assert_not_called()
        calls=[]
        with mock.patch.object(sys,"addaudithook",side_effect=lambda hook:calls.append("guard")), \
             mock.patch.object(unittest,"main",side_effect=lambda:calls.append("tests")):
            exec(source,{"__name__":"__main__","__file__":__file__})
        self.assertEqual(calls,["guard","tests"])

    def test_rejection_diagnostic_nonblocking_once_without_wait_or_new_clock(self):
        w=self.w; raw=c.encode_attest_request(policy(),1000)
        for phase in ("invocation","expired"):
            for behavior in ("ok","eagain","short","setter_error","write_error"):
                with self.subTest(phase=phase,behavior=behavior):
                    calls=[]
                    def blocking(fd,value):
                        calls.append(("blocking",fd,value))
                        if behavior=="setter_error": raise OSError("private-canary setter")
                    def write(fd,data):
                        calls.append(("write",fd,data))
                        if behavior=="eagain": raise BlockingIOError("private-canary full")
                        if behavior=="write_error": raise OSError("private-canary write")
                        return 1 if behavior=="short" else len(data)
                    def invocation():
                        if phase=="invocation": raise ValueError("private-canary invocation")
                    times=[] if phase=="invocation" else [100,1100]
                    with mock.patch.object(w,"_invocation",side_effect=invocation), \
                         mock.patch.object(w,"_read_frame",return_value=raw), \
                         mock.patch.object(w,"_attest",side_effect=AssertionError("attest after expiry")), \
                         mock.patch.object(w.os,"set_blocking",side_effect=blocking), \
                         mock.patch.object(w.os,"write",side_effect=write), \
                         mock.patch.object(w.select,"select",side_effect=AssertionError("rejection must never wait")) as wait, \
                         mock.patch.object(w.time,"monotonic_ns",side_effect=times) as clock:
                        self.assertEqual(w.main(),1)
                    expected=[("blocking",2,False)]
                    if behavior!="setter_error": expected.append(("write",2,b"b3_worker_rejected\n"))
                    self.assertEqual(calls,expected)
                    self.assertEqual(clock.call_count,0 if phase=="invocation" else 2)
                    wait.assert_not_called()

    def test_main_stdout_failure_remains_static_and_never_succeeds(self):
        w=self.w;raw=c.encode_attest_request(policy(),1000);output=[]
        def write(fd,data):
            output.append((fd,data))
            if fd==1:raise BrokenPipeError("private-canary")
            return len(data)
        with mock.patch.object(w,"_invocation"),mock.patch.object(w,"_read_frame",return_value=raw),mock.patch.object(w,"_attest",return_value=b"\x00\x00\x00\x01x"),mock.patch.object(w,"_wait"),mock.patch.object(w.os,"write",side_effect=write):
            self.assertEqual(w.main(),1)
        self.assertEqual(output[-1],(2,b"b3_worker_rejected\n"))
        self.assertEqual(len(output),2)

    def test_main_rejection_is_static_and_no_exception_chain(self):
        w=self.w; output=[]
        with mock.patch.object(w,"_invocation",side_effect=ValueError("private-canary")), \
             mock.patch.object(w.os,"set_blocking"), \
             mock.patch.object(w.os,"write",side_effect=lambda fd,b:output.append((fd,b)) or len(b)):
            self.assertEqual(w.main(),1)
        self.assertEqual(output,[(2,b"b3_worker_rejected\n")])


if __name__ == "__main__":
    sys.addaudithook(_forbid_external_calls)
    unittest.main()
