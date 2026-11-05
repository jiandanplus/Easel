"""Real parser authority at the actual caller assignment; no backend/native imports.

Only the straight-line authority-construction statements are extracted from
actual(). They execute unchanged with real product ParserAuthority/projection;
the surrounding database/capture/container workflow is deliberately not entered.
"""
import ast
import base64
import copy
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest

HERE = Path(__file__).resolve().parent

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

# Reuse the existing approved public fixture and its stdlib-only package setup.
fixture_module = load('parser_fixture_boundary', HERE / 'test_media_backup_b3_parser_runner.py')
pr = fixture_module.pr
caller = load('caller_parser_boundary', HERE.parent / 'scripts/b3_target_once.py')

class ActualParserBoundary(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/private/tmp')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.work_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, self.work_fd)
        self.m = dict(operation_nonce='a'*64, source=dict(container_id='b'*64),
                      started=1.0, deadline=901.0)
        self.manifest_hash = 'd'*64
        self.restore_id = 'b3-' + self.m['operation_nonce'][:32]
        self.inspect = fixture_module.inspect_fixture()
        self.inspect.update(Image=caller.PARSER_IMAGE, Created='synthetic-created',
                            ExecIDs=None, Name='/synthetic-parser', GraphDriver={'Name':'overlay2'})
        self.inspect['Config']['Labels']['operation_nonce'] = self.restore_id
        self.inspect['Config']['Cmd'] = ['720']
        self.inspect['Config']['Image'] = 'synthetic-image-reference'
        self.inspect['HostConfig']['LogConfig'] = {'Type':'json-file','Config':{}}
        self.inspect['State'].update(Status='running', ExitCode=0, Error='', FinishedAt='synthetic-finished', Health={'Status':'none'})
        self.inspect['NetworkSettings'].update(SandboxID='synthetic-sandbox', SandboxKey='/synthetic-sandbox')
        self.inspect['NetworkSettings']['Networks']['none'] = {'NetworkID':'synthetic-network','EndpointID':'','IPAddress':'','Aliases':None}

    def descriptor(self, value=None):
        raw = caller.canonical(self.inspect if value is None else value)
        return dict(format='easel-b3-root-parser-descriptor-v1', manifest_sha256=self.manifest_hash,
            operation_nonce=self.m['operation_nonce'], restore_id=self.restore_id,
            cid=self.inspect['Id'], image_id=caller.PARSER_IMAGE, tool_sha256=caller.PARSER_TOOL_SHA,
            inspect_base64=base64.b64encode(raw).decode(), inspect_sha256=caller.sha(raw),
            started=self.m['started'], deadline=self.m['deadline'])

    def construct(self, descriptor):
        descriptor = caller.validate_descriptor(caller.canonical(descriptor), self.m, self.manifest_hash)
        tree = ast.parse((HERE.parent / 'scripts/b3_target_once.py').read_text())
        actual = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'actual')
        block = next(n.body for n in ast.walk(actual) if isinstance(n, ast.With)
                     and 'trusted_anchor_intent' in ast.unparse(n.items[0]))
        def assignment(node, name):
            return isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
        start = next(i for i,n in enumerate(block) if assignment(n, 'descriptor'))
        end = next(i for i,n in enumerate(block) if assignment(n, 'parserauthority'))
        parser_import = next(n for n in actual.body if isinstance(n, ast.ImportFrom)
                             and n.module == 'platform_service.media_backup_b3.parser_runner')
        construction = ast.Module(body=[parser_import, *block[start:end+1]], type_ignores=[])
        env = dict(vars(caller), parser_descriptor=descriptor,
            stage_authority=SimpleNamespace(restore_id=self.restore_id, cancellation=threading.Event()),
            directory=self.directory, parser_work=self.work_fd, owner=(os.geteuid(),os.getegid()), m=self.m)
        exec(compile(construction, '<real actual caller authority boundary>', 'exec'), env)
        return env['parserauthority']

    def test_full_raw_inspect_reaches_real_authority_as_exact_projection(self):
        descriptor = self.descriptor()
        original = copy.deepcopy(descriptor)
        try:
            authority = self.construct(descriptor)
        except pr.B3Error as error:
            self.fail('full raw inspect must be projected before real ParserAuthority: ' + str(error))
        self.assertIs(type(authority), pr.ParserAuthority)
        projected = pr.inspect_projection(caller.closed_json(base64.b64decode(descriptor['inspect_base64'])))
        self.assertEqual(authority.expected_inspect, projected)
        self.assertEqual(authority.expected_inspect_sha256, caller.sha(projected))
        self.assertNotEqual(authority.expected_inspect_sha256, descriptor['inspect_sha256'])
        self.assertEqual(descriptor, original)
        self.assertEqual(base64.b64decode(descriptor['inspect_base64']), caller.canonical(self.inspect))
        self.assertEqual(descriptor['inspect_sha256'], caller.sha(caller.canonical(self.inspect)))
        result = caller.closed_json(projected)
        self.assertEqual(set(result), {'Id','Image','Config','HostConfig','Mounts','NetworkSettings','RestartCount','State'})
        self.assertEqual(result['State'], {k:self.inspect['State'][k] for k in ('Pid','StartedAt','Running','Paused','Restarting','OOMKilled','Dead')})
        for key in ('Config','HostConfig','Mounts'):
            self.assertEqual(result[key], self.inspect[key])
        self.assertEqual(result['NetworkSettings'], {k:self.inspect['NetworkSettings'][k] for k in ('Ports','Networks')})
        for key, expected in dict(cid=descriptor['cid'], nonce=self.restore_id, image_id=caller.PARSER_IMAGE,
                started_at=self.m['started'], deadline=self.m['deadline']).items():
            self.assertEqual(getattr(authority,key), expected)

    def test_resource_policy_drift_still_rejected_after_projection(self):
        for key,value in [('Memory',1073741824),('NetworkMode','bridge'),('CapAdd',['SYS_ADMIN']),('CapDrop',[])]:
            changed = copy.deepcopy(self.inspect)
            changed['HostConfig'][key] = value
            with self.subTest(key=key), self.assertRaises(pr.B3Error):
                self.construct(self.descriptor(changed))

    def test_wrong_raw_pin_rejected_before_projection(self):
        descriptor = self.descriptor()
        descriptor['inspect_sha256'] = caller.sha(pr.inspect_projection(self.inspect))
        with self.assertRaises(ValueError):
            self.construct(descriptor)

    def test_raw_diagnostics_are_independently_pinned(self):
        descriptor = self.descriptor()
        for part in ('top','state','network'):
            changed = copy.deepcopy(self.inspect)
            if part == 'top': changed['ExecIDs'] = ['changed']
            if part == 'state': changed['State']['Error'] = 'changed'
            if part == 'network': changed['NetworkSettings']['SandboxID'] = 'changed'
            bad = dict(descriptor, inspect_base64=base64.b64encode(caller.canonical(changed)).decode())
            with self.subTest(part=part), self.assertRaises(ValueError):
                self.construct(bad)

if __name__ == '__main__':
    unittest.main()
