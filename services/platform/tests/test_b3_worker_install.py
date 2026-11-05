"""Synthetic-only installer regression tests: no installations or runtime resources."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tarfile
import unittest
from unittest.mock import patch, Mock
import tempfile

SOURCE = Path(__file__).resolve().parents[1] / "scripts/b3_worker_install.py"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def load():
    spec = importlib.util.spec_from_file_location("b3_installer_test", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SOURCE.exists(), "fixed offline installer has not been implemented")
        self.m = load()
        previous_umask = os.umask(0o022)
        self.addCleanup(os.umask, previous_umask)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input = self.root / "opt/easel-build-input"
        self.input.mkdir(parents=True)
        self.patches = [patch.object(self.m, "_ROOT", str(self.root)),
                        patch.object(self.m, "_OWNER", (os.getuid(), os.getgid()))]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.rows = [dict(destination_path="rootfs/opt/easel/pg16/lib",kind="directory",mode=493),
                     dict(destination_path="rootfs/opt/easel/pg16/lib/lib.so",kind="file",mode=420,size=3,sha256=digest(b"lib")),
                     dict(destination_path="rootfs/opt/easel/pg16/lib/alias.so",kind="symlink",mode=511,relative_target="lib.so"),
                     dict(destination_path="rootfs/usr/lib/postgresql/16/bin/pg_restore",kind="file",mode=493,size=2,sha256=digest(b"pg"))]
        for row in self.rows:
            row.update(layer_sha256="a"*64, source_path="usr/lib/source")
        self.runtime_entries = {}
        for row in self.rows:
            name=row["destination_path"].removeprefix("rootfs/")
            self.runtime_entries[name]=(row["kind"],row["mode"],row.get("relative_target", b"lib" if name.endswith("lib.so") else b"pg"))
            for parent in Path(name).parents:
                if str(parent) != ".":
                    self.runtime_entries.setdefault(str(parent),("directory",493,b""))
        self.intent = dict(format="easel.b3.worker-install.v1", architecture="arm64", pg_restore_sha256=digest(b"pg"))
        self.intent["artifact"] = self.put("platform-1-py3-none-any.whl",b"wheel")
        self.intent["wheels"] = [self.put("dependency-1-py3-none-any.whl",b"dependency")]
        self.refresh_archives()
        self.intent["notices"]=self.put("notices.tar",self.tar({"licenses":("directory",493,b""),"licenses/provenance.json":("file",420,b"{}"),"licenses/COPYRIGHT":("file",420,b"legal")}))
        self.write_intent()
        for path,data in [("usr/local/bin/python3.12",b"python"),("usr/local/lib/python3.12/site-packages/platform_service/media_backup_b3/execution_worker.py",b"worker"),("usr/local/lib/python3.12/__pycache__/x.pyc",b"pyc")]:
            dest=self.root/path;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(data);dest.chmod(0o644)

    def put(self,name,data):
        (self.input/name).write_bytes(data)
        return dict(filename=name,size=len(data),sha256=digest(data))

    def tar(self,entries):
        out=io.BytesIO()
        with tarfile.open(fileobj=out,mode="w",format=tarfile.USTAR_FORMAT) as tf:
            for name,(kind,mode,data) in entries.items():
                info=tarfile.TarInfo(name);info.mode=mode
                if kind=="directory":info.type=tarfile.DIRTYPE
                elif kind=="symlink":info.type=tarfile.SYMTYPE;info.linkname=data
                else:info.size=len(data)
                tf.addfile(info,io.BytesIO(data) if kind=="file" else None)
        return out.getvalue()

    def refresh_archives(self):
        self.intent["runtime"]=self.put("runtime.tar",self.tar(self.runtime_entries))
        self.intent["runtime_layout"]=self.put("layout.json",json.dumps(dict(format="b3-pg-runtime-layout.v1",nodes=self.rows)).encode())

    def write_intent(self):
        (self.input/"intent.json").write_text(json.dumps(self.intent))

    def run_install(self,side_effect=None):
        with patch.object(self.m,"_pip",side_effect=side_effect) as pip:
            previous_umask = os.umask(0)
            try:
                result=self.m._install()
            finally:
                os.umask(previous_umask)
        return result,pip

    def test_complete_pipeline_manifest_and_fixed_pip(self):
        receipt,pip=self.run_install()
        build=json.loads((self.root/"opt/easel/worker-build.json").read_bytes())
        self.assertEqual(build["format"],"easel.b3.worker-build.v1")
        self.assertEqual(build["allowed_fileless_modules"],[])
        self.assertEqual(len(pip.call_args_list),2)
        self.assertEqual(build["artifact_sha256"],digest(b"wheel"))
        self.assertEqual(build["pg_restore_sha256"],digest(b"pg"))
        self.assertIn("/usr/local/lib/python3.12/__pycache__/x.pyc",build["files"])
        self.assertNotIn("/opt/easel/bin/python",build["files"])
        self.assertNotIn("/opt/easel/worker-build.json",build["files"])
        for path,meta in build["files"].items():
            real=self.root/path.lstrip("/");self.assertEqual(meta["sha256"],digest(real.read_bytes()))
        self.assertEqual(os.readlink(self.root/"opt/easel/bin/python"),"/usr/local/bin/python3.12")
        self.assertEqual(stat.S_IMODE((self.root/"opt/easel/artifact.whl").stat().st_mode),0o444)
        self.assertEqual(receipt["manifest_sha256"],digest((self.root/"opt/easel/worker-build.json").read_bytes()))
        self.assertNotIn("success",receipt)
        req=(self.input/".requirements.txt").read_text()
        self.assertIn("--hash=sha256:"+self.intent["artifact"]["sha256"],req)

    def test_failure_consumes_claim_retains_input_and_no_retry(self):
        with self.assertRaises(RuntimeError):self.run_install(RuntimeError("fake pip failure"))
        self.assertTrue((self.input/".install-claim").exists())
        self.assertTrue((self.input/self.intent["runtime"]["filename"]).exists())
        with self.assertRaises(Exception):self.run_install()

    def test_rejects_policy_changes(self):
        for change in [dict(architecture="amd64"),dict(extra=True),dict(wheels=[]),dict(pg_restore_sha256="A"*64)]:
            with self.subTest(change=change):
                intent=dict(self.intent);intent.update(change)
                with self.assertRaises(Exception):self.m._intent(json.dumps(intent).encode())

    def test_rejects_duplicate_json_keys(self):
        with self.assertRaises(Exception):self.m._intent(b'{"format":"x","format":"y"}')

    def test_tampered_input_rejected_before_pip(self):
        (self.input/self.intent["artifact"]["filename"]).write_bytes(b"wrong")
        with self.assertRaises(Exception):self.run_install()

    def test_input_symlink_rejected(self):
        name=self.input/self.intent["artifact"]["filename"];name.unlink();name.symlink_to("intent.json")
        with self.assertRaises(Exception):self.run_install()

    def test_no_overwrite_existing_immutable(self):
        dest=self.root/"opt/easel/artifact.whl";dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(b"old")
        with self.assertRaises(Exception):self.run_install()
        self.assertEqual(dest.read_bytes(),b"old")

    def test_archive_rejects_extra_traversal_and_truncation(self):
        for name in ["opt/easel/pg16/lib/extra","../escape","usr/lib/global.so"]:
            with self.subTest(name=name):
                entries=dict(self.runtime_entries);entries[name]=("file",420,b"bad")
                fd=os.open(self.input/"runtime.tar",os.O_RDWR)
                try:
                    data=self.tar(entries);os.ftruncate(fd,0);os.write(fd,data);os.lseek(fd,0,0)
                    with self.assertRaises(Exception):self.m._runtime(fd,self.rows,self.intent["pg_restore_sha256"])
                finally:os.close(fd)
        (self.input/"runtime.tar").write_bytes(b"truncated")
        fd=os.open(self.input/"runtime.tar",os.O_RDONLY)
        try:
            with self.assertRaises(Exception):self.m._runtime(fd,self.rows,self.intent["pg_restore_sha256"])
        finally:os.close(fd)

    def test_symlink_cannot_escape_private_tree(self):
        self.rows[2]["relative_target"]="../../../../usr/lib/global.so"
        self.runtime_entries["opt/easel/pg16/lib/alias.so"]=("symlink",511,self.rows[2]["relative_target"])
        self.refresh_archives();self.write_intent()
        with self.assertRaises(Exception):self.run_install()

    def test_import_has_no_io(self):
        with patch("os.open",side_effect=AssertionError("import IO")),patch("subprocess.Popen",side_effect=AssertionError("process")):
            load()

    def test_unlisted_input_rejected(self):
        (self.input/"unlisted").write_bytes(b"unknown")
        with self.assertRaises(Exception):self.run_install()

    def test_existing_private_tree_is_rejected_before_any_copy(self):
        tree=self.root/"opt/easel/pg16/lib";tree.mkdir(parents=True);(tree/"extra").write_bytes(b"old")
        with self.assertRaises(Exception):self.run_install()
        self.assertFalse((self.root/"usr/lib/postgresql/16/bin/pg_restore").exists())

    def test_rejects_reserved_duplicate_and_noninteger_metadata(self):
        for value in [True,-1,513*1024**2,"5"]:
            intent=json.loads(json.dumps(self.intent));intent["artifact"]["size"]=value
            with self.subTest(value=value),self.assertRaises(Exception):self.m._intent(json.dumps(intent).encode())
        for name in ["intent.json","../bad.whl","space name.whl","ümlaut.whl",self.intent["wheels"][0]["filename"]]:
            intent=json.loads(json.dumps(self.intent));intent["artifact"]["filename"]=name
            with self.subTest(name=name),self.assertRaises(Exception):self.m._intent(json.dumps(intent).encode())

    def test_input_hardlink_and_writable_mode_rejected(self):
        name=self.input/self.intent["artifact"]["filename"]
        name.chmod(0o666)
        with self.assertRaises(Exception):self.run_install()
        name.chmod(0o644)
        (self.input/".install-claim").unlink()
        os.link(name,self.input/"hardlink")
        with self.assertRaises(Exception):self.run_install()

    def test_runtime_duplicate_hidden_extension_and_bad_hash_rejected(self):
        original=self.tar(self.runtime_entries)
        # Duplicating first header is a duplicate USTAR name, even if valid checksum.
        for data in [original[:512]+original,original[:257]+b"BAD!!!"+original[263:]]:
            (self.input/"runtime.tar").write_bytes(data)
            fd=os.open(self.input/"runtime.tar",os.O_RDONLY)
            try:
                with self.assertRaises(Exception):self.m._runtime(fd,self.rows,self.intent["pg_restore_sha256"])
            finally:os.close(fd)
        self.rows[1]["sha256"]="f"*64
        self.refresh_archives();self.write_intent()
        with self.assertRaises(Exception):self.run_install()

    def test_notice_links_and_escape_rejected(self):
        for name,kind,data in [("licenses/bad","symlink","COPYRIGHT"),("escape","file",b"bad")]:
            entries={"licenses":("directory",493,b""),"licenses/provenance.json":("file",420,b"{}"),name:(kind,511 if kind=="symlink" else 420,data)}
            self.intent["notices"]=self.put("notices.tar",self.tar(entries));self.write_intent()
            with self.assertRaises(Exception):self.run_install()
            (self.input/".install-claim").unlink()

    def test_final_json_has_endpoint_exact_schema(self):
        self.run_install()
        build=json.loads((self.root/"opt/easel/worker-build.json").read_bytes())
        expected={"format","architecture","files","allowed_fileless_modules"}
        for role in ["artifact","entrypoint","executable","pg_restore"]:expected.update({role+"_path",role+"_sha256"})
        self.assertEqual(set(build),expected)
        for path,meta in build["files"].items():
            self.assertTrue(path.startswith(("/usr/local/","/usr/lib/","/opt/easel/")))
            self.assertEqual(set(meta),{"sha256","size","mode"})
            self.assertEqual(meta["mode"]&0o22,0)
        raw=(self.root/"opt/easel/worker-build.json").read_bytes()
        self.assertEqual(raw,json.dumps(build,sort_keys=True,separators=(",",":")).encode())
        self.assertEqual(stat.S_IMODE((self.root/"opt/easel/worker-build.json").stat().st_mode),0o444)

    def test_pip_arguments_and_failure_custody(self):
        process=Mock();process.stdout.fileno.return_value=101;process.stderr.fileno.return_value=102
        process.wait.return_value=1;process.poll.return_value=None
        selector=Mock();selector.get_map.return_value={}
        with patch.object(self.m.subprocess,"Popen",return_value=process) as start,patch.object(self.m.selectors,"DefaultSelector",return_value=selector),patch.object(self.m.os,"set_blocking"):
            with self.assertRaises(ValueError):self.m._pip()
        args=start.call_args.args[0]
        self.assertEqual(args[:6],[self.m.sys.executable,"-I","-B","-m","pip","install"])
        for flag in ["--isolated","--no-index","--no-deps","--no-compile","--require-hashes","--no-cache-dir"]:self.assertIn(flag,args)
        self.assertEqual(start.call_args.kwargs["env"]["PIP_CONFIG_FILE"],"/dev/null")
        self.assertNotIn("LD_LIBRARY_PATH",start.call_args.kwargs["env"])
        self.assertEqual(start.call_args.kwargs["stdin"],self.m.subprocess.DEVNULL)
        process.kill.assert_called_once();self.assertEqual(process.wait.call_count,2)

    def test_pip_timeout_consumes_claim_and_kills_original(self):
        process=Mock();process.stdout.fileno.return_value=101;process.stderr.fileno.return_value=102;process.poll.return_value=None
        selector=Mock();selector.get_map.return_value={101:True}
        with patch.object(self.m.subprocess,"Popen",return_value=process),patch.object(self.m.selectors,"DefaultSelector",return_value=selector),patch.object(self.m.os,"set_blocking"),patch.object(self.m.time,"monotonic",side_effect=[0,301]):
            with self.assertRaises(ValueError):self.m._pip()
        process.kill.assert_called_once();process.wait.assert_called_once_with(timeout=10)
        with self.assertRaises(ValueError):self.run_install(ValueError("timeout"))
        self.assertTrue((self.input/".install-claim").exists())

    def test_main_rejects_wrong_architecture_without_io(self):
        with patch.object(self.m.platform,"machine",return_value="x86_64"),patch.object(self.m,"_install") as install,patch.object(self.m.os,"write"):
            self.assertEqual(self.m.main(),1)
        install.assert_not_called()

    def test_relative_directory_alias_is_preserved_without_inventory_alias(self):
        for name,kind,mode,extra in [("dir","directory",493,{}),("dir/file","file",420,dict(size=4,sha256=digest(b"data"))),("dir-alias","symlink",511,dict(relative_target="dir"))]:
            self.rows.append(dict(destination_path="rootfs/opt/easel/pg16/lib/"+name,kind=kind,mode=mode,layer_sha256="a"*64,source_path="usr/lib/source",**extra))
        self.runtime_entries.update({"opt/easel/pg16/lib/dir":("directory",493,b""),"opt/easel/pg16/lib/dir/file":("file",420,b"data"),"opt/easel/pg16/lib/dir-alias":("symlink",511,"dir")})
        self.refresh_archives();self.write_intent();self.run_install()
        files=json.loads((self.root/"opt/easel/worker-build.json").read_bytes())["files"]
        self.assertIn("/opt/easel/pg16/lib/dir/file",files)
        self.assertNotIn("/opt/easel/pg16/lib/dir-alias/file",files)

    def test_pip_output_cap_kills_original(self):
        process=Mock();process.stdout.fileno.return_value=101;process.stderr.fileno.return_value=102;process.poll.return_value=None
        selector=Mock();selector.get_map.return_value={101:True};key=Mock();key.fileobj=process.stdout
        selector.select.return_value=[(key,1)]
        with patch.object(self.m.subprocess,"Popen",return_value=process),patch.object(self.m.selectors,"DefaultSelector",return_value=selector),patch.object(self.m.os,"set_blocking"),patch.object(self.m.os,"read",return_value=b"x"*16384):
            with self.assertRaises(ValueError):self.m._pip()
        process.kill.assert_called_once();process.wait.assert_called_once_with(timeout=10)

    def test_invalid_intent_still_consumes_exclusive_claim(self):
        self.intent["architecture"]="amd64";self.write_intent()
        with self.assertRaises(Exception):self.run_install()
        self.assertTrue((self.input/".install-claim").exists())


if __name__=="__main__":unittest.main()
