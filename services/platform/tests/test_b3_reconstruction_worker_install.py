"""Companion entry must exist separately from unchanged legacy installer."""
from pathlib import Path

def test_separate_fixed_installer_exists():
    source=Path(__file__).resolve().parents[1]/'scripts/b3_reconstruction_worker_install.py'
    assert source.is_file(), 'missing fixed offline companion installer'

import importlib.util
import json
import os
import io
import zipfile
from unittest.mock import patch
import unittest
import test_b3_worker_install as legacy
from platform_service.media_backup_b3 import reconstruction_origin as origin

class CompanionTests(unittest.TestCase):
    setUp=legacy.InstallerTests.setUp
    put=legacy.InstallerTests.put
    tar=legacy.InstallerTests.tar
    refresh_archives=legacy.InstallerTests.refresh_archives
    write_intent=legacy.InstallerTests.write_intent
    def companion(self):
        source=Path(__file__).resolve().parents[1]/'scripts/b3_reconstruction_worker_install.py'
        spec=importlib.util.spec_from_file_location('companion_test',source)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);module._base=self.m
        for path in origin._PROGRAM+origin._CLIENT:
            p=self.root/path[1:];p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'public synthetic module');p.chmod(0o444)
        payload=io.BytesIO()
        with zipfile.ZipFile(payload,'w') as wheel:
            for path in origin._PROGRAM:wheel.writestr(path.removeprefix(origin._BASE),b'public synthetic module')
        self.intent['artifact']=self.put('platform-1-py3-none-any.whl',payload.getvalue());self.write_intent()
        return module
    def test_companion_real_flow_dual_manifest(self):
        m=self.companion()
        old=os.umask(0)
        try:
            with patch.object(self.m,'_pip') as pip:receipt=m._install()
        finally:os.umask(old)
        self.assertEqual(pip.call_count,2)
        legacy=json.loads((self.root/origin._LEGACY[1:]).read_bytes())
        new=json.loads((self.root/origin._BUILD[1:]).read_bytes())
        self.assertEqual(legacy['format'],'easel.b3.worker-build.v1')
        self.assertNotIn(origin._LEGACY,legacy['files']);self.assertNotIn(origin._BUILD,new['files'])
        self.assertIn(origin._LEGACY,new['files']);self.assertIn(m._CLAIM,new['files'])
        self.assertEqual(origin._validate(new),new)
        self.assertEqual(receipt['files'],len(new['files']))
        self.assertEqual((self.root/origin._BUILD[1:]).stat().st_mode&0o777,0o444)
        with patch.object(self.m,'_pip') as pip:
            with self.assertRaises(Exception):m._install()
            pip.assert_not_called()
    def test_companion_failed_claim_retained(self):
        m=self.companion()
        with patch.object(self.m,'_pip',side_effect=RuntimeError('public failure')):
            with self.assertRaises(Exception):m._install()
        self.assertTrue((self.root/m._CLAIM[1:]).is_file())
        self.assertFalse((self.root/origin._BUILD[1:]).exists())
        with patch.object(self.m,'_pip') as pip:
            with self.assertRaises(Exception):m._install()
            pip.assert_not_called()

    def test_companion_rejects_wheel_without_program(self):
        m=self.companion()
        payload=io.BytesIO()
        with zipfile.ZipFile(payload,'w') as wheel:wheel.writestr('unrelated.py',b'public')
        self.intent['artifact']=self.put('platform-1-py3-none-any.whl',payload.getvalue());self.write_intent()
        old=os.umask(0)
        try:
            with patch.object(self.m,'_pip'):
                with self.assertRaises(Exception):m._install()
        finally:os.umask(old)
        self.assertTrue((self.root/m._CLAIM[1:]).exists())
        self.assertFalse((self.root/origin._BUILD[1:]).exists())


def test_companion_source_and_current_baseline_entry_agree():
    import ast
    from platform_service.media_backup_b3 import execution_baseline
    source=Path(__file__).resolve().parents[1]/'scripts/b3_reconstruction_worker_install.py'
    tree=ast.parse(source.read_text())
    assert execution_baseline._RECONSTRUCTION_ENTRY==['/opt/easel/bin/python','-I','-B','-m','platform_service.media_backup_b3.reconstruction_worker']
    assert origin._ENTRY==origin._BASE+execution_baseline._RECONSTRUCTION_ENTRY[-1].replace('.','/')+'.py'
    text=source.read_text()
    assert "with_name('b3_worker_install.py')" in text and "legacy=b._install()" in text
    assert "if __name__=='__main__':sys.exit(main())" in text
    project=(source.parents[1]/'pyproject.toml').read_text()
    assert 'src/platform_service' in project
    for installed in origin._PROGRAM:
        local=source.parents[1]/'src'/installed.removeprefix(origin._BASE)
        assert local.is_file()
    baseline=Path(execution_baseline.__file__).read_text()
    assert "_exact(c['Cmd'],['reconstruct_worker'])" in baseline
