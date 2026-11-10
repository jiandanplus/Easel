"""Stdlib-only caller tests: never import product or execute prepare/execute/actual."""
import ast
import copy
import gc
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('actual_preflight_once', HERE.parent / 'scripts/b3_target_once.py')
caller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(caller)

class Query:

    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0]

class Connection:

    def __init__(self):
        self.queries = []

    def execute(self, sql):
        self.queries.append(sql)
        if sql == 'SHOW transaction_isolation':
            return Query([('repeatable read',)])
        if sql == 'SHOW transaction_read_only':
            return Query([('on',)])
        return Query([('sample', ['second', 'first'])])

def fixture():
    col = lambda name, number: ['public', 'sample', 'r', 'owned', None, number, name, 'text', True, '', None, False, False, None, None, None, '']
    catalog = dict(columns=[col('first', 1), col('second', 2)], enums=[], database=[['synthetic', 'owned', None, -1, True, False]], constraints=[['all', 'full', 'catalog', 'retained']])
    rows = {'sample': [{'first': 'z', 'second': '1'}, {'first': 'a', 'second': '2'}]}
    api = dict(catalog=lambda *_: copy.deepcopy(catalog), raw_rows=lambda *_: copy.deepcopy(rows), parse=lambda raw: SimpleNamespace(**json.loads(raw)), canonical=caller.canonical, digest=lambda value: caller.sha(caller.canonical(value)), context=lambda **kw: SimpleNamespace(**kw))
    return (catalog, rows, api)

class CallerPure(unittest.TestCase):

    def context(self, kind='central', api=None):
        conn = Connection()
        env = SimpleNamespace(name='synthetic', roles={'b': 'role_z', 'a': 'role_a'})
        profile = SimpleNamespace(name='public_001_016', tables=('sample',))
        return (caller.context_from_source(conn, env, profile, kind, api or fixture()[2]), conn)

    def test_internal_role_admission_retains_graph_before_entry(self):
        graph={};events=[];pair=object();owners=(object(),object());auths=(object(),object())
        class Owner:
            def _admit_role_plan(self,p,*,source_owners,authorizations):
                self_test.assertIs(graph['role_admission']['owner'],self)
                self_test.assertIs(graph['role_admission']['pair'],pair)
                events.append('admit')
                return {'data_only':True}
        self_test=self;owner=Owner()
        result=caller.admit_owned_role_plan(graph,owner,owners,pair,auths)
        self.assertEqual(events,['admit']);self.assertTrue(result['data_only'])
        with self.assertRaises(ValueError):caller.admit_owned_role_plan(graph,owner,owners,pair,auths)

    def test_optin_role_cleanup_consumer_publication_then_existing_owners(self):
        from unittest.mock import patch
        events=[];pair=SimpleNamespace(snapshot=lambda:{'publication_fds':0})
        role={'unknown':False,'observation_error':False,'closed':False,'consumer_held':True}
        def consumer():events.append('consumer');role.update(closed=True,consumer_held=False)
        owner=SimpleNamespace(role_admission_snapshot=lambda:dict(role),_close_role_plan_consumer=consumer,
            close=lambda:events.append('execution'),_fd_slots=[],_native_close_attempted=set(),_native_closed=set())
        owner.snapshot=lambda:{k:False for k in ('execution_unknown','execution_observation_error',
            'execution_local_live_or_unobserved','execution_remote_live_or_unobserved','execution_active_work')}
        inputs=SimpleNamespace(companion=pair,close=lambda:events.append('inputs'))
        final=SimpleNamespace(close=lambda:events.append('final'))
        source=SimpleNamespace(cleanup=lambda g:events.append('source'))
        graph=dict(execution_owner=owner,inputs=inputs,finalobj=final,source_owners=[source],
            role_admission=dict(owner=owner,pair=pair))
        state=dict(unknown=False,execution_unknown=False,source_publication_fds=2)
        keys=('target_local_live_or_unobserved','source_active_work','source_observation_error',
            'b2_local_children','b2_observation_error','parser_local_live_or_unobserved')
        state.update({k:0 for k in keys})
        with patch.object(caller,'graph_state',return_value=state),patch.object(caller,'release_owned_source_capture',side_effect=lambda p:events.append('publication')):
            outcome={};caller.cleanup_known(graph,outcome)
        self.assertEqual(events,['consumer','publication','execution','inputs','final','source'])
        self.assertTrue(outcome['synthetic_cleanup_complete'])

    def test_independent_context_raw_order_and_immutable_pins(self):
        (ctx, catalog), conn = self.context()
        self.assertEqual(ctx.profile.tables[0]['primary_key'], ['second', 'first'])
        self.assertEqual([c['name'] for c in ctx.profile.tables[0]['columns']], ['first', 'second'])
        rows = fixture()[1]['sample']
        self.assertEqual(ctx.row_order_pins, (('sample', tuple((caller.sha(caller.canonical(r)) for r in rows))),))
        self.assertIsInstance(ctx.row_order_pins[0][1], tuple)
        self.assertEqual(ctx.role_names, ('role_a', 'role_z'))
        self.assertEqual(ctx.allowed_grantors, ('pg_database_owner',))
        self.assertIn('READ ONLY', conn.queries[0])
        self.assertIn('WITH ORDINALITY', conn.queries[3])

    def test_catalog_normalizes_only_database_name(self):
        catalog, _, api = fixture()
        (ctx, observed), _ = self.context(api=api)
        normalized = copy.deepcopy(catalog)
        normalized['database'][0][0] = '<trusted-database>'
        self.assertEqual(ctx.profile.catalog_sha256, api['digest'](normalized))
        self.assertEqual(observed, catalog)
        api['catalog'] = lambda *_: {**copy.deepcopy(catalog), 'constraints': []}
        (other, _), _ = self.context(api=api)
        self.assertNotEqual(other.profile.catalog_sha256, ctx.profile.catalog_sha256)

    def test_transaction_mode_is_verified_before_catalog(self):
        connection = Connection()
        original = connection.execute
        connection.execute = lambda sql: Query([('read committed',)]) if sql == 'SHOW transaction_isolation' else original(sql)
        env = SimpleNamespace(name='synthetic', roles={'owner': 'owned'})
        profile = SimpleNamespace(name='public_001_016', tables=('sample',))
        api = fixture()[2]
        api['catalog'] = lambda *_: self.fail('catalog must not be read after bad isolation')
        with self.assertRaises(ValueError):
            caller.context_from_source(connection, env, profile, 'central', api)

    def test_identity_has_no_raw_read_or_pins(self):
        api = fixture()[2]

        def forbidden(*args):
            raise AssertionError('identity raw read forbidden')
        api['raw_rows'] = forbidden
        (ctx, _), _ = self.context('identity', api)
        self.assertEqual(ctx.evidence_mode, 'copy_only')
        self.assertEqual(ctx.row_order_pins, ())

    def test_enum_labels_retained(self):
        catalog, _, api = fixture()
        catalog['columns'][0][7] = 'role_type'
        catalog['enums'] = [['role_type', ['User', 'MachineToMachine']]]
        api['catalog'] = lambda *_: catalog
        (ctx, _), _ = self.context(api=api)
        self.assertEqual(ctx.profile.tables[0]['columns'][0]['enum_labels'], ['User', 'MachineToMachine'])

    def test_stored_generated_column_preserves_raw_type_and_full_catalog(self):
        catalog, _, api = fixture()
        catalog['columns'][0][16] = 's'
        catalog['columns'][0][10] = "(evidence ->> 'target_issuer'::text)"
        api['catalog'] = lambda *_: copy.deepcopy(catalog)
        (ctx, observed), _ = self.context(api=api)
        self.assertEqual(ctx.profile.tables[0]['columns'][0], dict(name='first', type='text', nullable=False))
        self.assertEqual(observed, catalog)
        self.assertEqual(observed['columns'][0][16], 's')
        self.assertEqual(observed['columns'][0][10], catalog['columns'][0][10])
        normalized = copy.deepcopy(catalog)
        normalized['database'][0][0] = '<trusted-database>'
        self.assertEqual(ctx.profile.catalog_sha256, api['digest'](normalized))
        rows = fixture()[1]['sample']
        self.assertEqual(ctx.row_order_pins[0][1], tuple((api['digest'](row) for row in rows)))
        for index, value in ((16, ''), (10, 'different_generated_expression')):
            changed = copy.deepcopy(catalog)
            changed['columns'][0][index] = value
            api['catalog'] = lambda *_, changed=changed: changed
            (other, _), _ = self.context(api=api)
            self.assertNotEqual(ctx.profile.catalog_sha256, other.profile.catalog_sha256)

    def test_unknown_or_virtual_generated_modes_rejected(self):
        for mode in ('v', 'unknown', None):
            with self.subTest(mode=mode):
                catalog, _, api = fixture()
                catalog['columns'][0][16] = mode
                api['catalog'] = lambda *_: catalog
                with self.assertRaises(ValueError):
                    self.context(api=api)

    def test_context_signature_has_no_archive_or_stage_input(self):
        tree = ast.parse((HERE.parent / 'scripts/b3_target_once.py').read_text())
        fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'context_from_source'))
        self.assertEqual([a.arg for a in fn.args.args], ['connection', 'env', 'profile', 'kind', 'api'])
        self.assertFalse(any((isinstance(n, ast.Constant) and isinstance(n.value, str) and ('source_facts' in n.value) for n in ast.walk(fn))))

    def test_manifest_deadline_and_scope_frozen(self):
        m = dict(scope=copy.deepcopy(caller.SCOPE), uid=os.getuid(), gid=os.getgid(), started=100.0, deadline=1000.0)
        caller.check_manifest(m, 999.999)
        for now in (99, 1000, 1001):
            with self.assertRaises(ValueError):
                caller.check_manifest(m, now)
        m['scope']['retries'] = 1
        with self.assertRaises(ValueError):
            caller.check_manifest(m, 101)

    def test_exclusive_file_and_custody_replacement(self):
        with tempfile.TemporaryDirectory(dir=HERE) as tmp:
            root = Path(tmp)
            os.chmod(root, 448)
            os.chown(root, os.getuid(), os.getgid())
            path = root / 'test.private'
            caller.write_new(path, b'one')
            raw, custody = caller.private_read(path)
            self.assertEqual(raw, b'one')
            self.assertEqual(custody['file'][3:5], [os.getuid(), os.getgid()])
            with self.assertRaises(FileExistsError):
                caller.write_new(path, b'two')
            replacement = root / 'replacement'
            caller.write_new(replacement, b'one')
            replacement.replace(path)
            self.assertNotEqual(caller.private_read(path)[1], custody)

    def test_symlink_file_rejected(self):
        with tempfile.TemporaryDirectory(dir=HERE) as tmp:
            root = Path(tmp)
            os.chmod(root, 448)
            caller.write_new(root / 'target', b'one')
            (root / 'alias').symlink_to(root / 'target')
            with self.assertRaises(OSError):
                caller.private_read(root / 'alias')

    def test_source_sha_cid_image_nonce_binding(self):
        with tempfile.TemporaryDirectory(dir=HERE) as tmp:
            root = Path(tmp)
            os.chmod(root, 448)
            data = dict(container_id='a' * 64, image='postgres@sha256:' + 'b' * 64, nonce='c' * 32, database='easel_s1_test_b2_' + 'c' * 32)
            raw = caller.canonical(data)
            path = root / 'authority.private.json'
            caller.write_new(path, raw)
            expected = {k: data[k] for k in ('container_id', 'image', 'nonce')}
            expected['sha256'] = caller.sha(raw)
            caller.source_binding(path, expected)
            for key in expected:
                other = dict(expected)
                other[key] = 'bad'
                with self.assertRaises(ValueError):
                    caller.source_binding(path, other)

    def test_safe_receipt_exact_bounded_surface(self):
        receipt = valid_receipt()
        self.assertEqual(caller.safe_receipt(receipt), receipt)
        for other in ({**receipt, 'password': 'secret'}, {**receipt, 'dump_preflight_complete': False}, {**receipt, 'archive_sha256': 'invalid'}):
            with self.assertRaises(ValueError):
                caller.safe_receipt(other)

class LifecyclePure(unittest.TestCase):

    def graph(self, broken=False):
        marks = []

        def owned():
            try:
                yield object()
            finally:
                marks.append('fixture-finally')
                if broken:
                    raise RuntimeError('synthetic cleanup failed')
        gen = owned()
        next(gen)

        class Ref:

            def close(self):
                marks.append('remote-ref-close')
        runner = SimpleNamespace(children=set(), unknown_refs=[Ref()])
        return (dict(generators=[gen], closers=[], fds=[], runner=runner, stageobj=None), marks)

    def test_normal_cleanup_runs_once_and_errors_retained(self):
        graph, marks = self.graph(broken=True)
        graph['runner'].unknown_refs = []
        outcome = {}
        caller.cleanup_known(graph, outcome)
        caller.cleanup_known(graph, outcome)
        self.assertEqual(marks, ['fixture-finally'])
        self.assertEqual(outcome['cleanup_error_classes'], ['RuntimeError'])
        self.assertFalse(outcome['synthetic_cleanup_complete'])
        self.assertIn('synthetic cleanup failed', graph['cleanup_traces'][0])

    def test_unknown_holds_generators_across_gc_until_explicit_disposition(self):
        graph, marks = self.graph()
        outcome = {}
        m = dict(operation_nonce='a' * 64)
        published = []
        checks = []
        manifest_hash = 'b' * 64

        def observe():
            gc.collect()
            self.assertEqual(marks, [])
            self.assertIn(graph, caller._QUARANTINED)
            checks.append(True)
            if len(checks) == 1:
                return None
            return dict(action='cleanup-owned-synthetic', operation_nonce=m['operation_nonce'], manifest_sha256=manifest_hash, remote_terminal_confirmed=True)
        caller.quarantine_unknown(graph, m, outcome, publish=published.append, observe=observe, pause=lambda: None, manifest_hash=manifest_hash)
        self.assertEqual(marks, ['remote-ref-close', 'fixture-finally'])
        self.assertFalse(outcome['success'])
        self.assertTrue(outcome['quarantine_entered'])
        self.assertTrue(outcome['root_disposition_applied'])
        self.assertFalse(published[0]['synthetic_cleanup_complete'])
        self.assertTrue(published[0]['remote_status_unknown'])
        self.assertEqual(len(checks), 2)
        self.assertNotIn(graph, caller._QUARANTINED)

    def test_disposition_cannot_authorize_by_timeout_or_wrong_nonce(self):
        for wrong in (None, {}, dict(action='cleanup-owned-synthetic', operation_nonce='wrong', manifest_sha256='b' * 64, remote_terminal_confirmed=True), dict(action='cleanup-owned-synthetic', operation_nonce='a' * 64, manifest_sha256='b' * 64, remote_terminal_confirmed=False)):
            self.assertFalse(caller.disposition_permits(wrong, dict(operation_nonce='a' * 64), 'b' * 64))

    def test_live_original_child_prevents_even_valid_disposition(self):
        graph, marks = self.graph()
        graph['runner'].children.add('fake-original-child')
        calls = []

        def observe():
            calls.append(True)
            if len(calls) == 2:
                self.assertEqual(marks, [])
                graph['runner'].children.clear()
            return dict(action='cleanup-owned-synthetic', operation_nonce='a' * 64, manifest_sha256='b' * 64, remote_terminal_confirmed=True)
        caller.quarantine_unknown(graph, dict(operation_nonce='a' * 64), {}, publish=lambda value: None, observe=observe, pause=lambda: None, manifest_hash='b' * 64)
        self.assertEqual(len(calls), 2)
        self.assertEqual(marks, ['remote-ref-close', 'fixture-finally'])

    def test_unknown_terminal_hooks_cannot_release_ownership(self):
        graph, marks = self.graph()
        outcome = {}
        calls = []

        def observe():
            calls.append(True)
            if len(calls) == 1:
                raise KeyboardInterrupt()
            gc.collect()
            self.assertEqual(marks, [])
            return dict(action='cleanup-owned-synthetic', operation_nonce='a' * 64, manifest_sha256='b' * 64, remote_terminal_confirmed=True)
        caller.quarantine_unknown(graph, dict(operation_nonce='a' * 64), outcome, publish=lambda value: None, observe=observe, pause=lambda: None, manifest_hash='b' * 64)
        self.assertEqual(marks, ['remote-ref-close', 'fixture-finally'])
        self.assertEqual(outcome['observation_error_classes'], ['KeyboardInterrupt'])
"""Pure fake lifecycle/descriptor/template/composition controls; no actual imports."""
import ast, copy, gc, importlib.util, json, os, threading, unittest
from pathlib import Path
from types import SimpleNamespace as NS
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('caller', HERE.parent / 'scripts/b3_target_once.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)

def valid_receipt():
    parser = dict(cid='c' * 64, image_id=c.PARSER_IMAGE, tool_sha256=c.PARSER_TOOL_SHA, docker_sha256=c.DOCKER_SHA, inspect_sha256='e' * 64, authority_sha256='f' * 64)
    inspections = {}
    for kind in ('central', 'identity'):
        inspections[kind] = dict(toc_entries=c.TEMPLATE_PINS[kind]['selected'], tables=1, sequences=0, rows=0, toc_equal=True, schema_equal=True, data_equal=True, copy_equal=True, sequences_equal=True, **{mode: dict(size=1, sha256='a' * 64) for mode in ('toc', 'schema', 'data')})
    return dict(authenticated_stage=True, dump_preflight_complete=True, parser=parser, dump_inspection=inspections, **{key: 'a' * 64 for key in ('plan_sha256', 'registry_sha256', 'source_facts_sha256', 'archive_sha256', 'ciphertext_sha256')})

class Controls(unittest.TestCase):

    def graph(self, active=0, unknown=False, state='TERMINAL', rc=0):
        marks = []

        def gen():
            try:
                yield None
            finally:
                marks.append('drop')
        g = gen()
        next(g)
        inputs = NS(_work_lock=threading.Lock(), _active_work=active, _work_unknown=unknown, close=lambda: None)
        return (dict(inputs=inputs, runner=NS(children=set(), unknown_refs=[]), parser_runner=NS(remote_state=state, _retained_processes=[NS(poll=lambda: rc)]), generators=[g], closers=[], fds=[], stageobj=None), marks)

    def test_source_active_and_sticky_are_unknown_before_cleanup(self):
        for active, unknown in ((1, False), (0, True)):
            g, marks = self.graph(active, unknown)
            self.assertTrue(c.graph_state(g)['unknown'])
            c.cleanup_known(g, {})
            gc.collect()
            self.assertEqual(marks, [])
            g['inputs']._active_work = 0
            g['release_unknown_refs'] = True
            c.cleanup_known(g, {})

    def test_parser_unknown_live_and_unobserved_are_unknown(self):
        for state, rc in (('UNKNOWN', 0), ('TERMINAL', None), ('TERMINAL', 1)):
            g, marks = self.graph(state=state, rc=rc)
            self.assertTrue(c.graph_state(g)['unknown'])
            c.cleanup_known(g, {})
            self.assertEqual(marks, [])
            g['parser_runner'] = None
            g['release_unknown_refs'] = True
            c.cleanup_known(g, {})

    def test_normal_early_and_terminal_cleanup(self):
        self.assertFalse(c.graph_state({})['unknown'])
        g, marks = self.graph()
        self.assertFalse(c.graph_state(g)['unknown'])
        c.cleanup_known(g, {})
        self.assertEqual(marks, ['drop'])

    def test_manual_disposition_waits_for_source_zero_keeps_failed_sticky_deadline(self):
        g, marks = self.graph(1, True, 'UNKNOWN')
        m = dict(operation_nonce='a' * 64, deadline=901.0)
        out = {}
        calls = []

        def observe():
            gc.collect()
            self.assertEqual(marks, [])
            calls.append(1)
            if len(calls) == 2:
                g['inputs']._active_work = 0
            return dict(action='cleanup-owned-synthetic', operation_nonce='a' * 64, manifest_sha256='b' * 64, remote_terminal_confirmed=True)
        c.quarantine_unknown(g, m, out, publish=lambda _: None, observe=observe, pause=lambda: None, manifest_hash='b' * 64)
        self.assertEqual(len(calls), 2)
        self.assertEqual(marks, ['drop'])
        self.assertFalse(out['success'])
        self.assertTrue(g['inputs']._work_unknown)
        self.assertEqual(m['deadline'], 901.0)

    def test_fixed_plan_has_sleep720_no_execution(self):
        m = dict(directory='/private/tmp/fake-case', operation_nonce='a' * 64)
        plan = c.parser_plan(m)
        self.assertEqual(plan[-2:], [c.PARSER_IMAGE, '720'])
        self.assertIn('--pull', plan)
        self.assertIn('never', plan)
        self.assertEqual(plan[0], c.DOCKER_PATH)
        self.assertIn('no-new-privileges', plan)

    def test_module_import_stdlib_only_and_actual_no_container_lifecycle(self):
        tree = ast.parse((HERE.parent / 'scripts/b3_target_once.py').read_text())
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                self.assertNotIn('platform', node.module or '')
        actual = next((n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'actual'))
        s = ast.unparse(actual)
        self.assertIn('inspect_authenticated_stage', s)
        self.assertIn('VerifiedRestoreInput', s)
        self.assertIn('graph_state(graph)', s)
        self.assertIn('parser_descriptor', s)
        for bad in ('docker create', 'docker start', 'docker stop', 'parse_toc('):
            self.assertNotIn(bad, s)

    def test_template_only_exact_owner_field_changes(self):
        data = dict(format='easel-root-known-fixture-toc-policy-v1', header_entries=759, logical_records=[['TABLE', 'public', 'sample', 'fixture_role']], toc_sha256='a' * 64)
        pin = dict(kind='identity', sha256=c.sha(c.canonical(data)), selected=1, header=759)
        factory = lambda records, header: NS(expected_records=records, expected_header_entries=header)
        result = c.template_policy(c.canonical(data), pin, 'fresh_owner', factory)
        self.assertEqual(result.expected_records, (('TABLE', 'public', 'sample', 'fresh_owner'),))
        for change in ('hash', 'tag', 'old-role-tag', 'owner', 'count'):
            d = copy.deepcopy(data)
            p = dict(pin)
            if change == 'hash':
                p['sha256'] = 'b' * 64
            if change == 'tag':
                d['logical_records'][0][2] = 'sample_fixture_role'
            if change == 'old-role-tag':
                d['logical_records'][0][2] = 'sample_easel_s1_test_b2r_oldrole'
            if change == 'owner':
                d['logical_records'][0][3] = 'arbitrary'
            if change == 'count':
                p['selected'] = 2
            if change != 'hash':
                p['sha256'] = c.sha(c.canonical(d))
            with self.assertRaises(ValueError):
                c.template_policy(c.canonical(d), p, 'fresh_owner', factory)

    def test_descriptor_exact_binding_and_canonical_inspect(self):
        m = dict(operation_nonce='a' * 64, directory='/private/tmp/fake-case', source=dict(container_id='b' * 64), started=1.0, deadline=901.0)
        rid = 'b3-' + m['operation_nonce'][:32]
        inspect = dict(Id='c' * 64, Image=c.PARSER_IMAGE, Config=dict(Labels=dict(purpose='b3-dump-parser', operation_nonce=rid), Cmd=['720']))
        import base64
        d = dict(started=1.0,deadline=901.0,format='easel-b3-root-parser-descriptor-v1', manifest_sha256='d' * 64, operation_nonce=m['operation_nonce'], restore_id=rid, cid='c' * 64, image_id=c.PARSER_IMAGE, tool_sha256=c.PARSER_TOOL_SHA, inspect_base64=base64.b64encode(c.canonical(inspect)).decode(), inspect_sha256=c.sha(c.canonical(inspect)))
        raw = c.canonical(d)
        self.assertEqual(c.validate_descriptor(raw, m, 'd' * 64)['cid'], 'c' * 64)
        for key, value in (('cid', 'b' * 64), ('manifest_sha256', 'f' * 64), ('operation_nonce', 'e' * 64), ('restore_id', 'wrong'), ('image_id', 'sha256:' + 'e' * 64), ('tool_sha256', 'f' * 64), ('inspect_sha256', 'e' * 64), ('extra', True)):
            bad = {**d, key: value}
            with self.assertRaises(ValueError):
                c.validate_descriptor(c.canonical(bad), m, 'd' * 64)

    def test_receipt_rejects_missing_proofs_flags_unknown_fields(self):
        r = valid_receipt()
        self.assertEqual(c.safe_receipt(r), r)
        for mode in ('toc', 'schema', 'data'):
            d = copy.deepcopy(r)
            del d['dump_inspection']['identity'][mode]
            with self.assertRaises(ValueError):
                c.safe_receipt(d)
        for key in ('toc_equal', 'schema_equal', 'data_equal', 'copy_equal', 'sequences_equal'):
            d = copy.deepcopy(r)
            d['dump_inspection']['central'][key] = False
            with self.assertRaises(ValueError):
                c.safe_receipt(d)
        d = copy.deepcopy(r)
        d['parser']['secret'] = 'no'
        with self.assertRaises(ValueError):
            c.safe_receipt(d)

    def test_real_public_templates_distinct_exact_counts(self):
        for kind in ('central', 'identity'):
            p = c.TEMPLATE_PINS[kind]
            result = c.template_policy(Path(p['path']).read_bytes(), p, 'fresh_owner', lambda records, header: NS(expected_records=records, header=header))
            self.assertEqual(len(result.expected_records), p['selected'])
            self.assertEqual(result.header, p['header'])

    def test_source_close_failure_does_not_drop_generators_or_close_other_handles(self):
        g, marks = self.graph()

        class Inputs:
            _work_lock = threading.Lock()
            _active_work = 0
            _work_unknown = False

            def close(self):
                raise RuntimeError('source close failed')
        g['inputs'] = Inputs()
        g['closers'] = [g['inputs']]
        out = {}
        c.cleanup_known(g, out)
        self.assertEqual(marks, [])
        self.assertTrue(out['cleanup_refused_live_work'])
        g['inputs'] = None
        g['closers'] = []
        g.pop('cleanup_attempted', None)
        c.cleanup_known(g, {})

    def test_original_parser_start_budget_no_extension(self):
        m = dict(started=100.0, deadline=1000.0)
        c.check_parser_start_budget(m, 280.0)
        with self.assertRaises(ValueError):
            c.check_parser_start_budget(m, 280.001)
        self.assertEqual(m['deadline'], 1000.0)

    def test_busy_source_lock_and_probe_failure_are_nonblocking_failclosed(self):
        g, marks = self.graph()
        g['inputs']._work_lock.acquire()
        try:
            state = c.graph_state(g)
            self.assertTrue(state['unknown'])
            self.assertFalse(c.disposition_work_terminal(state))
            c.cleanup_known(g, {})
            self.assertEqual(marks, [])
        finally:
            g['inputs']._work_lock.release()

        class Broken:

            @property
            def children(self):
                raise KeyboardInterrupt()
        g['runner'] = Broken()
        self.assertTrue(c.graph_state(g)['unknown'])
        c.cleanup_known(g, {})
        self.assertEqual(marks, [])
        g['runner'] = None
        c.cleanup_known(g, {})

    def test_source_close_error_not_retried_and_manual_disposition_stays_failed(self):
        g, marks = self.graph()
        calls = []

        class Inputs:
            _work_lock = threading.Lock()
            _active_work = 0
            _work_unknown = False

            def close(self):
                calls.append(1)
                raise RuntimeError('fake-close')
        g['inputs'] = Inputs()
        g['closers'] = [g['inputs']]
        out = {}
        c.cleanup_known(g, out)
        c.cleanup_known(g, out)
        self.assertEqual(len(calls), 1)
        self.assertEqual(marks, [])

        # A disposition cannot resume an already uncertain cleanup phase.
        g['release_unknown_refs']=True
        c.cleanup_known(g,out)
        self.assertEqual(len(calls),1)
        self.assertEqual(marks,[])
        self.assertTrue(out['cleanup_refused_live_work'])
        self.assertFalse(out['success'])
        self.assertFalse(out['synthetic_cleanup_complete'])
        self.assertIn('RuntimeError', out['cleanup_error_classes'])

    def test_descriptor_label_canonical_and_duplicate_controls(self):
        import base64
        m = dict(operation_nonce='a' * 64, source=dict(container_id='b' * 64))
        rid = 'b3-' + 'a' * 32
        projection = dict(Id='c' * 64, Image=c.PARSER_IMAGE, Config=dict(Labels=dict(purpose='b3-dump-parser', operation_nonce=rid), Cmd=['720']))

        def document(raw):
            return c.canonical(dict(format='easel-b3-root-parser-descriptor-v1', manifest_sha256='d' * 64, operation_nonce='a' * 64, restore_id=rid, cid='c' * 64, image_id=c.PARSER_IMAGE, tool_sha256=c.PARSER_TOOL_SHA, inspect_base64=base64.b64encode(raw).decode(), inspect_sha256=c.sha(raw)))
        for raw in (json.dumps(projection).encode(), b'{"Id":1,"Id":2}'):
            with self.assertRaises(ValueError):
                c.validate_descriptor(document(raw), m, 'd' * 64)
        for field in ('purpose', 'operation_nonce'):
            d = copy.deepcopy(projection)
            d['Config']['Labels'][field] = 'wrong'
            with self.assertRaises(ValueError):
                c.validate_descriptor(document(c.canonical(d)), m, 'd' * 64)

    def test_poll_error_and_invalid_source_flags_are_safe_unknown(self):
        g, marks = self.graph()

        def fail():
            raise KeyboardInterrupt()
        g['parser_runner']._retained_processes = [NS(poll=fail)]
        self.assertTrue(c.graph_state(g)['unknown'])
        g['inputs']._active_work = 'private-must-never-serialize'
        state = c.graph_state(g)
        self.assertTrue(state['source_observation_error'])
        self.assertNotIn('private-must-never-serialize', json.dumps(state))
        g['inputs']._active_work = 0
        g['parser_runner'] = None
        c.cleanup_known(g, {})


from unittest.mock import patch
class TargetQualification(unittest.TestCase):
    def test_fd_regular_private_bounded_and_owner(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'input';c.write_new(path,b'{}')
            fd=os.open(path,os.O_RDONLY)
            try:
                self.assertEqual(c.fd_read(fd),b'{}')
                with self.assertRaises(ValueError):c.fd_read(fd,1)
                os.chmod(path,0o644)
                with self.assertRaises(ValueError):c.fd_read(fd)
            finally:os.close(fd)
    def test_fd_hardlink_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'input';c.write_new(path,b'{}');os.link(path,Path(d)/'alias')
            fd=os.open(path,os.O_RDONLY)
            try:
                with self.assertRaises(ValueError):c.fd_read(fd)
            finally:os.close(fd)
    def test_fd_aliases_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'input';c.write_new(path,b'{}')
            fds=[os.open(path,os.O_RDONLY) for _ in range(3)]
            try:
                with self.assertRaises(ValueError):c.validate_fd_set(*fds)
            finally:
                for fd in fds:os.close(fd)
    def test_report_cannot_overwrite_prior_or_uncertain_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'report';c.write_new(path,b'')
            fd=os.open(path,os.O_RDWR)
            try:
                c.report_write(fd,{'success':False})
                with self.assertRaises(ValueError):c.report_write(fd,{'success':True})
                self.assertEqual(c.closed_json(c.fd_read(fd)),{'success':False})
            finally:os.close(fd)
    def test_exclusive_claim_fsyncs_file_and_directory(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'execute.once';seen=[];original=os.fsync
            def sync(fd):seen.append(os.fstat(fd).st_mode);original(fd)
            with patch.object(c.os,'fsync',sync):c.write_new(path,b'original')
            self.assertEqual(len(seen),2)
            with self.assertRaises(FileExistsError):c.write_new(path,b'replacement')
            self.assertEqual(path.read_bytes(),b'original')
    def test_partial_claim_is_never_removed(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'execute.once'
            with patch.object(c.os,'fsync',side_effect=OSError('synthetic')):
                with self.assertRaises(OSError):c.write_new(path,b'original')
            self.assertTrue(path.exists())
            with self.assertRaises(FileExistsError):c.write_new(path,b'retry')
    def launch(self):
        m=dict(operation_nonce='a'*64,started=1.0,deadline=901.0)
        value=dict(format='b3-root-external-launch-v1',manifest_sha256='b'*64,**m,
            caller_pid=123,caller_ppid=321,supervisor_pid=321,supervisor_nonce='e'*64,tool_session='original-session',launched_at=2.0)
        return m,value
    def test_external_launch_all_bindings(self):
        m,value=self.launch();self.assertEqual(c.launch_record(c.canonical(value),m,'b'*64,pid=123),value)
        for key,bad in [('caller_pid',124),('supervisor_pid',123),('manifest_sha256','c'*64),
            ('operation_nonce','c'*64),('deadline',902.0),('started',2.0),('tool_session',''),('launched_at',902.0),('extra',True)]:
            with self.subTest(key=key),self.assertRaises(ValueError):
                c.launch_record(c.canonical({**value,key:bad}),m,'b'*64,pid=123)
    def graph(self,unknown=False,rc=0):
        process=NS(poll=lambda:rc)
        attempt=NS(_unknown=unknown,_state='READY',_sequence=8,_records=[None]*8,_processes=[process],
            _outputs=[dict(process=process,terminal_code=rc,operation='deny_password' if rc==2 else 'metadata',
                stdout_sha256='a'*64,stderr_sha256='b'*64)])
        return dict(factory=NS(_attempts={'central':attempt}),generators=[],fds=[],closers=[])
    def test_target_unknown_and_unobserved_prevent_cleanup(self):
        for unknown,rc in ((True,0),(False,None)):
            graph=self.graph(unknown,rc);out={}
            self.assertTrue(c.graph_state(graph)['unknown']);c.cleanup_known(graph,out)
            self.assertTrue(out['cleanup_refused_live_work'])
    def test_expected_nonzero_target_probe_is_not_unknown(self):
        self.assertFalse(c.graph_state(self.graph(False,2))['unknown'])
    def test_unvalidated_target_terminal_or_forged_transcript_stays_unknown(self):
        for change in ('notready','missingrecord','missingentry','wrongprocess','wrongcode','wrongoperation','missinghash'):
            graph=self.graph(False,2);attempt=graph['factory']._attempts['central'];entry=attempt._outputs[0]
            if change=='notready':attempt._state='RUNNING'
            if change=='missingrecord':attempt._records.pop()
            if change=='missingentry':attempt._outputs=[]
            if change=='wrongprocess':entry['process']=NS(poll=lambda:2)
            if change=='wrongcode':entry['terminal_code']=0
            if change=='wrongoperation':entry['operation']='metadata'
            if change=='missinghash':entry.pop('stdout_sha256')
            with self.subTest(change=change):self.assertTrue(c.graph_state(graph)['unknown'])
    def test_validated_absence_and_readiness_nonzeros_are_known(self):
        for operation,rc in (('volume_absence',1),('container_absence',1),('ready',1),('ready',2),('deny_password',2)):
            graph=self.graph(False,rc);graph['factory']._attempts['central']._outputs[0]['operation']=operation
            with self.subTest(operation=operation,rc=rc):self.assertFalse(c.graph_state(graph)['unknown'])
    def test_original_target_process_blocks_disposition(self):
        state=c.graph_state(self.graph(True,None));self.assertFalse(c.disposition_work_terminal(state))
        state=c.graph_state(self.graph(True,0));self.assertTrue(c.disposition_work_terminal(state))
        self.assertTrue(state['unknown'])
    def test_source_observer_unknown_retains_graph(self):
        graph=dict(observer=NS(_unknown=True,_active=False,_processes=[]))
        self.assertTrue(c.graph_state(graph)['unknown'])
        out={};c.cleanup_known(graph,out);self.assertTrue(out['cleanup_refused_live_work'])
    def test_terminal_graph_strongly_retained_even_after_close(self):
        graph=self.graph();out={};c.cleanup_known(graph,out)
        self.assertTrue(any(item is graph for item in c._RETAINED))
    def test_target_pair_uses_same_final_input_then_postcheck_before_close(self):
        tree=ast.parse((HERE.parent/'scripts/b3_target_once.py').read_text())
        actual=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='actual')
        block=next(n.body for n in ast.walk(actual) if isinstance(n,ast.With) and 'trusted_anchor_intent' in ast.unparse(n.items[0]))
        start=next(i for i,n in enumerate(block) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='factory' for t in n.targets))
        end=next(i for i,n in enumerate(block) if isinstance(n,ast.If) and ast.unparse(n.test)=='execution_owner is None')
        part=ast.Module(body=block[start:end+1],type_ignores=[]);events=[];final=NS(close=lambda:events.append('close'))
        factory=NS(create=lambda obj,kind:(events.append((obj,kind)) or NS(safe_receipt=lambda:{'status':'bound'})))
        observer=NS(snapshot=lambda *a:(events.append('post') or {'baseline':True}))
        env=dict(make_factory=lambda *a:factory,m={},envelope={'config':{'format':'b3-target-root-config-v1'}},execution_owner=None,stage_authority=object(),contexts=[],directory=Path('/synthetic'),fds=[],
            handles=[],check_budget=lambda:None,finalobj=final,central=None,identity=None,psycopg=None,PUBLIC_016=None,observer=observer,
            graph_state=lambda _:dict(unknown=False),source_baseline={'baseline':True},write_new=lambda *a:None,
            canonical=c.canonical,sha=c.sha,require=c.require,outcome={},parser_runner=NS(remote_state='TERMINAL'))
        exec(compile(part,'<pure extracted target composition>','exec'),env)
        self.assertEqual(events,[(final,'central'),(final,'identity'),'post','close'])
    def test_no_source_success_stub_and_source_reads_complete(self):
        tree=ast.parse((HERE.parent/'scripts/b3_target_once.py').read_text())
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='SourceObserver')
        src=ast.unparse(cls)
        for name in ('read_source_catalog','_raw_rows','roles_inventory','role_credential_inventory','snapshot_copy_inventory','sequence_inventory','read_control_rows'):
            self.assertIn(name+'(',src)
        self.assertIn('REPEATABLE READ READ ONLY',src)
        self.assertIn('self._unknown = True',src)
    def test_source_and_raw_image_identifiers_remain_exact(self):
        c.check_source_image('postgres@'+c.PARSER_IMAGE)
        with self.assertRaises(ValueError):c.check_source_image(c.PARSER_IMAGE)
    def test_identity_fixture_byte_pin(self):
        pin=c.TEMPLATE_PINS['identity']
        self.assertEqual(c.sha(Path(pin['path']).read_bytes()),'0f7a2de46f95d77e4029792380da821e3fffac9f156d0dc15d131560619d2052')



class SourceSnapshotControls(unittest.TestCase):
    def test_real_helper_fake_connections_exact_preservation_and_drift(self):
        import types,sys
        calls=[];version=[1]
        cap=types.ModuleType('platform_service.media_backup_b2.capture')
        for name in ('read_source_catalog','_raw_rows','roles_inventory','role_credential_inventory','snapshot_copy_inventory','sequence_inventory'):
            def read(*args,_name=name,**kw):calls.append((_name,kw));return {'value':version[0]}
            setattr(cap,name,read)
        maintenance=types.ModuleType('platform_service.media_backup_b2.maintenance_snapshot')
        maintenance.read_control_rows=lambda _:dict(all_seven=['rows'])
        conninfo=types.ModuleType('psycopg.conninfo');conninfo.make_conninfo=lambda base,**kw:kw['dbname']
        class Conn:
            def __init__(self,name):self.name=name;self.closed=False
            def execute(self,sql):
                calls.append(sql)
                if sql=='SHOW transaction_read_only':return Query([('on',)])
                if sql=='SHOW transaction_isolation':return Query([('repeatable read',)])
                if 'pg_control_system' in sql:return Query([('123','source_admin')])
                if 'SELECT nspname' in sql:return Query([('easel_backup_control',)] if self.name=='central' else [])
                return Query([])
            def close(self):self.closed=True
        modules={cap.__name__:cap,maintenance.__name__:maintenance,conninfo.__name__:conninfo}
        with tempfile.TemporaryDirectory() as d,patch.dict(sys.modules,modules):
            root=Path(d).resolve();(root/'config').mkdir(mode=0o700);(root/'media').mkdir(mode=0o700)
            c.write_new(root/'config/value',b'fixed')
            m=dict(uid=os.getuid(),gid=os.getgid(),scope=c.SCOPE,started=1.0,deadline=901.0,
                source_proof=dict(system_id_sha256=c.sha(b'123'),admin_name='source_admin'))
            observer=c.SourceObserver(m,root);observer.inspect=lambda:{'full':'source-inspect'}
            env=lambda name:NS(base='private',name=name,roles={'owner':'own','runtime':'run','controller':'control'})
            db=NS(connect=lambda name,**kw:Conn(name))
            with patch.object(c.time,'monotonic',return_value=2.0):
                baseline=observer.snapshot(env('central'),env('identity'),db,object())
                post=observer.snapshot(env('central'),env('identity'),db,object())
                self.assertEqual(baseline,post)
                version[0]=2
                changed=observer.snapshot(env('central'),env('identity'),db,object())
            self.assertNotEqual(baseline,changed)
            self.assertTrue(all(conn.closed for conn in observer.connections))
            self.assertFalse(observer._unknown);self.assertFalse(observer._active)
            self.assertIn('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY',calls)
            sequences=[kw for name,kw in (x for x in calls if isinstance(x,tuple)) if name=='sequence_inventory']
            self.assertIn({'schemas':('public','easel_backup_control')},sequences)
            self.assertIn({'schemas':('public',)},sequences)



class ParkedGateControls(unittest.TestCase):
    def module(self):
        import sys
        with patch.dict(sys.modules,{'b3_target_once':c}):
            spec=importlib.util.spec_from_file_location('gate',HERE.parent/'scripts/b3_target_launch.py')
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
    def fixture(self,root):
        m,binding=TargetQualification().launch()
        launch={k:v for k,v in binding.items() if k not in ('caller_pid','caller_ppid','tool_session')}
        launch['format']='b3-root-external-launch-intent-v1'
        raw=c.canonical(launch);c.write_new(root/'launch.once',raw)
        caller=dict(launch_sha256=c.sha(raw),binding=binding)
        return caller
    def test_gate_accepts_only_bound_original_child_and_exec_not_called(self):
        gate=self.module()
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve();caller=self.fixture(root);c.write_new(root/'caller.once',c.canonical(caller))
            fd=os.open(root/'launch.once',os.O_RDONLY)
            try:
                with patch.object(c.os,'getpid',return_value=123),patch.object(c.os,'getppid',return_value=321),patch.object(c.time,'monotonic',return_value=2.0):
                    gate.gate(root,fd,pause=lambda _:self.fail('must not wait after exact gate'))
                self.assertTrue((root/'parked.private.json').exists())
                self.assertFalse((root/'execute.once').exists())
            finally:os.close(fd)
    def test_missing_gate_expires_without_retry_or_cleanup(self):
        gate=self.module()
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve();self.fixture(root);fd=os.open(root/'launch.once',os.O_RDONLY)
            try:
                with patch.object(c.time,'monotonic',side_effect=[2.0,3.0,902.0]):
                    with self.assertRaises(ValueError):gate.gate(root,fd,pause=lambda _:None)
                self.assertTrue((root/'parked.private.json').exists());self.assertTrue((root/'launch.once').exists())
                self.assertFalse((root/'execute.once').exists())
            finally:os.close(fd)



class FullEnvelopeControls(unittest.TestCase):
    def test_fake_prepare_park_finalize_execute_same_core_and_once(self):
        import sys,types,base64
        gate=ParkedGateControls().module()
        module=types.ModuleType('platform_service.media_backup_b3.target_contract')
        module.build_target_plan=lambda spec:NS(volume_argv=('fixed-volume',spec),container_argv=('fixed-container',spec))
        events=[];fds=[]
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();job=root/'job'
            def new(name,value):
                path=root/name;raw=value if type(value) is bytes else c.canonical(value);c.write_new(path,raw)
                return dict(path=str(path),sha256=c.sha(raw))
            original=new('original',dict(format='b3-root-original-job-v1',operation_nonce='a'*64,
                started=1.0,deadline=901.0,supervisor_pid=321,supervisor_nonce='e'*64))
            authority=new('source',b'synthetic-source-placeholder')
            sourcepin=new('source-proof',b'{}');capacitypin=new('capacity-proof',b'{}')
            tool=new('docker',b'fake-tool-no-execution')
            config=dict(format='b3-target-root-config-v1',directory=str(job),started=1.0,deadline=901.0,
                operation_nonce='a'*64,source=dict(container_id=c.SOURCE_CID,image=c.SOURCE_IMAGE_REF,nonce='b'*32,sha256=authority['sha256']),
                authority=authority,runtime=dict(prefix=sys.prefix,testtools=str(root),dependencies={tool['path']:tool['sha256']}),
                source_proof=sourcepin,capacity_proof=capacitypin,original_job=original)
            source=dict(admin_name='fixture_admin');capacity=dict(device=os.stat(root).st_dev,reserved_bytes=12*1024**3)
            def openfile(name,value):
                pin=new(name,value);fd=os.open(pin['path'],os.O_RDWR);fds.append(fd);return fd
            request=openfile('request',dict(format='b3-target-prepare-request-v1',config_sha256=c.sha(c.canonical(config))))
            prep_auth=openfile('prep-authority',dict(config=config));prep_report=openfile('prep-report',b'')
            async def fake_actual(*args):
                self.assertTrue((job/'execute.once').exists());events.append('actual-fake')
                args[2].update(synthetic_cleanup_complete=True)
            fake_free=NS(f_bavail=64*1024**3,f_frsize=1)
            with patch.dict(sys.modules,{module.__name__:module}),patch.object(c.time,'monotonic',return_value=2.0),                 patch.object(c,'dependency_hashes',return_value={'fixture':'c'*64}),                 patch.object(c,'source_document',return_value={'user':'fixture_admin'}),                 patch.object(c,'proof_documents',return_value=(source,capacity)),                 patch.object(c,'target_specs',return_value=('central','identity')),                 patch.object(c.os,'statvfs',return_value=fake_free),                 patch.object(c,'DOCKER_PATH',tool['path']),patch.object(c,'DOCKER_SHA',tool['sha256']),                 patch.object(c.os,'getpid',return_value=123),patch.object(c.os,'getppid',return_value=321),                 patch.object(c.signal,'signal',return_value=None),patch.object(c.signal,'setitimer'),                 patch.object(c,'actual',fake_actual):
                result=c.prepare_fd(request,prep_auth,prep_report);self.assertTrue(result['prepared'])
                m=c.closed_json((job/'manifest.private.json').read_bytes());manifest_hash=result['manifest_sha256']
                launch=dict(format='b3-root-external-launch-intent-v1',manifest_sha256=manifest_hash,
                    operation_nonce=config['operation_nonce'],started=1.0,deadline=901.0,
                    supervisor_pid=321,supervisor_nonce='e'*64,launched_at=2.0)
                c.write_new(job/'launch.once',c.canonical(launch))
                restore_id='b3-'+config['operation_nonce'][:32]
                inspection=dict(Id='c'*64,Image=c.PARSER_IMAGE,Config=dict(Labels=dict(purpose='b3-dump-parser',operation_nonce=restore_id),Cmd=['720']))
                raw=c.canonical(inspection)
                descriptor=new('parser',dict(format='easel-b3-root-parser-descriptor-v1',manifest_sha256=manifest_hash,
                    operation_nonce=config['operation_nonce'],restore_id=restore_id,cid='c'*64,image_id=c.PARSER_IMAGE,
                    tool_sha256=c.PARSER_TOOL_SHA,inspect_base64=base64.b64encode(raw).decode(),inspect_sha256=c.sha(raw),started=1.0,deadline=901.0))
                execute_auth=openfile('execute-authority',b'');execute_report=openfile('execute-report',b'')
                manifest_fd=os.open(job/'manifest.private.json',os.O_RDONLY);fds.append(manifest_fd)
                launch_fd=os.open(job/'launch.once',os.O_RDONLY);fds.append(launch_fd)
                binding=dict(launch,format='b3-root-external-launch-v1',caller_pid=123,caller_ppid=321,tool_session='actual-fake-session')
                caller=dict(launch_sha256=c.sha(c.canonical(launch)),binding=binding)
                envelope=dict(config=config,parser_descriptor=descriptor,
                    external_launch=dict(path=str(job/'launch.once'),sha256=caller['launch_sha256']),
                    external_caller=dict(path=str(job/'caller.once'),sha256=c.sha(c.canonical(caller))))
                def release(_):
                    self.assertEqual(c.fd_read(execute_auth),b'');events.append('root-postrecord')
                    os.pwrite(execute_auth,c.canonical(envelope),0);os.fsync(execute_auth)
                    c.write_new(job/'caller.once',c.canonical(caller))
                gate.gate(job,launch_fd,pause=release)
                out=c.execute_fd(manifest_fd,execute_auth,execute_report)
                self.assertTrue(out['success']);self.assertEqual(events,['root-postrecord','actual-fake'])
                self.assertEqual(m['config_sha256'],c.sha(c.canonical(envelope['config'])))
                # Fresh report cannot authorize replay of the original job.
                report2=openfile('report2',b'')
                with self.assertRaises(FileExistsError):c.execute_fd(manifest_fd,execute_auth,report2)
                self.assertEqual(events,['root-postrecord','actual-fake'])
                # Altering core while keeping postrecords cannot replace clock/source.
                changed=dict(envelope,config=dict(config,deadline=902.0))
                bad_auth=openfile('bad-authority',changed);bad_report=openfile('bad-report',b'')
                with self.assertRaises(ValueError):c.execute_fd(manifest_fd,bad_auth,bad_report)
            for fd in fds:os.close(fd)

if __name__ == '__main__':
    unittest.main(verbosity=2)


class ExecutionGraphIntegration(unittest.TestCase):
    def concrete(self):
        from test_media_backup_b3_execution import ExecutionTests
        fixture=ExecutionTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        owner=fixture.owner();fixture.pair(owner)
        graph=dict(factory=owner._factory,finalobj=owner._input,execution_owner=owner,
                   fds=[],closers=[],generators=[])
        return fixture,owner,graph

    def test_original_owner_snapshot_and_mismatch_fail_closed(self):
        fixture,owner,graph=self.concrete()
        state=caller.graph_state(graph)
        self.assertEqual(state.get('execution_owner_count'),1)
        self.assertEqual(state.get('execution_target_count'),2)
        self.assertFalse(state['execution_observation_error'])
        graph['factory']=object()
        state=caller.graph_state(graph)
        self.assertTrue(state['execution_observation_error']);self.assertTrue(state['unknown'])

    def test_partial_owner_never_absent_and_no_release(self):
        events=[];graph=dict(execution_owner=object(),closers=[SimpleNamespace(close=lambda:events.append('released'))])
        state=caller.graph_state(graph)
        self.assertTrue(state.get('execution_observation_error'))
        caller.cleanup_known(graph,{})
        self.assertEqual(events,[])

    def test_known_owner_closes_before_final_with_original_audit(self):
        fixture,owner,graph=self.concrete();events=[]
        original=owner.close
        def close():events.append('owner');original()
        # Patch only close IO boundary; graph authority remains concrete original.
        graph['closers']=[SimpleNamespace(close=lambda:events.append('peer'))]
        with patch.object(owner,'close',close):caller.cleanup_known(graph,{})
        self.assertEqual(events,['owner','peer'])
        self.assertEqual(graph['cleanup_attempts'][0]['phase'],'EXECUTION_CLOSE')
        self.assertIs(graph['cleanup_attempts'][0]['resource'],owner)
        self.assertTrue(owner._closed)
        self.assertFalse(caller.graph_state(graph)['execution_disposition_retained'])

    def test_close_failure_absorbs_later_release(self):
        fixture,owner,graph=self.concrete();events=[]
        graph['closers']=[SimpleNamespace(close=lambda:events.append('peer'))]
        with patch.object(owner,'close',side_effect=OSError('synthetic close ambiguity')) as close:
            outcome={};caller.cleanup_known(graph,outcome)
            graph['release_unknown_refs']=True;caller.cleanup_known(graph,outcome)
            self.assertEqual(close.call_count,1)
        self.assertEqual(events,[]);self.assertTrue(graph['cleanup_failed'])


class AttestConfigControls(unittest.TestCase):
    def test_closed_execution_policy_shape_and_legacy_rejects_extra(self):
        import dataclasses
        from test_media_backup_b3_execution import ExecutionTests
        fixture=ExecutionTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        policy=json.loads(json.dumps(dataclasses.asdict(fixture.policy)))
        config=dict(format='b3-target-root-config-attest-v1',execution_policy=policy,
            directory='/private/tmp/synthetic-task3',started=1.,deadline=901.,operation_nonce='a'*64,
            source=dict(container_id=caller.SOURCE_CID,image=caller.SOURCE_IMAGE_REF,nonce='b'*32,sha256='c'*64),
            authority=dict(path='/private/tmp/synthetic-authority',sha256='c'*64),
            runtime=dict(prefix='/private/tmp/runtime',testtools='/private/tmp/tools',dependencies={'/private/tmp/fake':'d'*64}),
            source_proof=dict(path='/private/tmp/source',sha256='e'*64),capacity_proof=dict(path='/private/tmp/capacity',sha256='f'*64),
            original_job=dict(path='/private/tmp/job',sha256='1'*64))
        self.assertEqual(caller.validate_config(config,now=2.),config)
        for section,key,value in [('limits','cpu_nanos',True),('worker','extra','x'),('target_probe','tools',[] )]:
            changed=copy.deepcopy(config);changed['execution_policy'][section][key]=value
            with self.subTest(key=key),self.assertRaises((ValueError,Exception)):caller.validate_config(changed,now=2.)
        with self.assertRaises(ValueError):caller.validate_config({**config,'format':'b3-target-root-config-v1'},now=2.)
        for i,value in enumerate(('PATH=relative/bin','LANG=private','GPG_KEY=ABC','PYTHON_VERSION=evil','PYTHON_SHA256='+'B'*64)):
            changed=copy.deepcopy(config);changed['execution_policy']['worker']['public_env'][i]=value
            with self.subTest(env=i),self.assertRaises(ValueError):caller.validate_config(changed,now=2.)


class RetainedExecutionIntegration(unittest.TestCase):
    @staticmethod
    def fd_open(fd):
        try:os.fstat(fd);return True
        except OSError:return False
    def concrete(self):
        from test_media_backup_b3_execution import FullExecutionTests
        fixture=FullExecutionTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        owner=fixture.full_owner()
        def fault(a,slot,out):
            if slot.operation=='probe-self-ns':raise OSError('synthetic original exit17')
            return out
        fixture.transport(owner,fault)
        with self.assertRaises(Exception):owner.attest('central')
        # Fake transport terminality is asynchronous; joining only test worker threads.
        for _ in range(100):
            if all(sl.process is not None and type(sl.process.poll()) is int for a in owner._attempts.values() for sl in a.operations):break
            __import__('time').sleep(.001)
        state=owner.snapshot()
        self.assertEqual(state['execution_local_live_or_unobserved'],0)
        self.assertFalse(state['execution_observation_error']);self.assertTrue(state['execution_unknown'])
        self.assertEqual(state['execution_committed_count'],0)
        failed=[sl for a in owner._attempts.values() for sl in a.operations if not sl.terminal]
        self.assertTrue(failed);self.assertTrue(any(getattr(sl,'disposition_exit',None)==17 for sl in failed))
        events=[]
        graph=dict(factory=owner._factory,finalobj=owner._input,execution_owner=owner,
            closers=[SimpleNamespace(close=lambda:events.append('closer'))],generators=[],fds=[])
        return fixture,owner,graph,events

    def document(self,owner):
        return dict(action='cleanup-owned-synthetic',operation_nonce='a'*64,manifest_sha256='b'*64,
            remote_terminal_confirmed=True,execution_owner_sha256=owner._retention_binding())

    def test_unknown_original_disposition_retains_owner_after_peer_cleanup(self):
        fixture,owner,graph,events=self.concrete();outcome={};document=self.document(owner)
        original_fds=tuple(slot.fd for slot in owner._fd_slots)
        graph['fds']=[owner._job_slot.fd]  # Alias must stay owned and open after unrelated cleanup.
        path=fixture.case.case.root/'unrelated-synthetic-fd';caller.write_new(path,b'unrelated')
        unrelated=os.open(path,os.O_RDONLY)
        closed_numbers={fd for _,fd in owner._native_closed}
        if unrelated not in closed_numbers:
            available=[]
            for fd in closed_numbers:
                try:os.fstat(fd)
                except OSError:available.append(fd)
            self.assertTrue(available)
            reused=available[0];os.dup2(unrelated,reused);os.close(unrelated);unrelated=reused
        self.assertIn(unrelated,closed_numbers)
        self.addCleanup(lambda:os.close(unrelated) if self.fd_open(unrelated) else None)
        graph['fds'].append(unrelated)
        with patch.object(owner,'close',side_effect=AssertionError('retained owner close')) as close:
            caller.quarantine_unknown(graph,dict(operation_nonce='a'*64),outcome,publish=lambda x:None,
                observe=lambda:document,pause=lambda:(_ for _ in ()).throw(AssertionError('unexpected quarantine')),manifest_hash='b'*64)
            close.assert_not_called()
        self.assertFalse(self.fd_open(unrelated))
        self.assertEqual(events,['closer']);self.assertFalse(outcome['success'])
        self.assertEqual(outcome['status'],'FAILED');self.assertTrue(outcome['synthetic_cleanup_complete'])
        self.assertTrue(any(g is graph for g in caller._RETAINED))
        self.assertFalse(any(g is graph for g in caller._QUARANTINED))
        state=caller.graph_state(graph)
        self.assertTrue(state['execution_unknown']);self.assertTrue(state['execution_disposition_retained'])
        self.assertGreater(state['execution_remote_live_or_unobserved'],0);self.assertTrue(outcome['remote_status_unknown'])
        self.assertEqual(state['execution_retained_owner_count'],1)
        self.assertFalse(state['execution_observation_error']);self.assertFalse(owner._closed)
        self.assertEqual(outcome['execution_disposition_state'],'RETAINED')
        self.assertEqual(graph['disposition_audit'][0]['phase'],'EXECUTION_RETAIN')
        for fd in original_fds:os.fstat(fd)
        self.assertTrue(owner._input._closed)
        caller.cleanup_known(graph,outcome);self.assertEqual(events,['closer'])
        import dataclasses
        original=owner._native_originals[0];clone=dataclasses.replace(original[0])
        attempt=next(a for a in owner._attempts.values() if any(sl is original[0] for sl in a.operations))
        ordinal=next(i for i,sl in enumerate(attempt.operations) if sl is original[0])
        owner._native_originals[0]=(clone,*original[1:]);attempt.operations[ordinal]=clone
        self.assertTrue(caller.graph_state(graph)['execution_observation_error'])
        owner._native_originals[0]=original;attempt.operations[ordinal]=original[0]
        owner._input._closed=False  # Authorized closure cannot be reversed into a replacement live peer.
        self.assertTrue(caller.graph_state(graph)['execution_observation_error'])

    def test_missing_wrong_binding_and_live_local_release_nothing(self):
        fixture,owner,graph,events=self.concrete();document=self.document(owner)
        for bad in ({k:v for k,v in document.items() if k!='execution_owner_sha256'},
                    {**document,'execution_owner_sha256':'0'*64}):
            self.assertFalse(caller.disposition_permits(bad,dict(operation_nonce='a'*64),'b'*64,graph=graph))
            graph.update(release_unknown_refs=True,matched_disposition=(bad,dict(operation_nonce='a'*64),'b'*64))
            outcome={};caller.cleanup_known(graph,outcome)
            self.assertTrue(outcome['cleanup_refused_live_work']);self.assertEqual(events,[])
        graph['matched_disposition']=(document,dict(operation_nonce='a'*64),'b'*64)
        slot=next(sl for a in owner._attempts.values() for sl in a.operations if not sl.terminal)
        with patch.object(slot.process,'poll',return_value=None):
            self.assertFalse(caller.disposition_work_terminal(caller.graph_state(graph)))
            caller.cleanup_known(graph,{})
        self.assertEqual(events,[]);self.assertFalse(owner.snapshot()['execution_disposition_retained'])

    def test_snapshot_and_retention_uncertainty_release_nothing(self):
        fixture,owner,graph,events=self.concrete();document=self.document(owner)
        graph['release_unknown_refs']=True
        graph['matched_disposition']=(document,dict(operation_nonce='a'*64),'b'*64)
        with patch.object(owner,'_retain_disposition',side_effect=OSError('synthetic audit uncertain')):
            outcome={};caller.cleanup_known(graph,outcome)
        self.assertTrue(outcome['cleanup_refused_live_work']);self.assertEqual(events,[])
        process=owner._native_originals[0][1]
        with patch.object(process,'poll',side_effect=OSError('synthetic poll uncertainty')):
            self.assertTrue(caller.graph_state(graph)['execution_observation_error'])
            caller.cleanup_known(graph,{})
        self.assertEqual(events,[])

    def test_real_failed_owner_close_then_matched_disposition_never_retries(self):
        from test_media_backup_b3_execution import ExecutionTests,m
        fixture=ExecutionTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        owner=fixture.owner();fixture.pair(owner);events=[]
        graph=dict(factory=owner._factory,execution_owner=owner,finalobj=owner._input,
            closers=[SimpleNamespace(close=lambda:events.append('released'))],fds=[],generators=[])
        with patch.object(m,'_close',side_effect=OSError('synthetic owned FD close failed')) as close:
            outcome={};caller.cleanup_known(graph,outcome)
            self.assertTrue(owner._unknown);self.assertTrue(graph['cleanup_failed'])
            document=self.document(owner)
            self.assertFalse(caller.disposition_permits(document,dict(operation_nonce='a'*64),'b'*64,graph=graph))
            graph.update(release_unknown_refs=True,matched_disposition=(document,dict(operation_nonce='a'*64),'b'*64))
            caller.cleanup_known(graph,outcome);self.assertEqual(close.call_count,1)
        self.assertEqual(events,[]);self.assertFalse(owner._closed)
        self.assertFalse(owner.snapshot()['execution_disposition_retained'])


class FullCallerExecutionComposition(unittest.TestCase):
    def test_original_factory_pair_two_commits_snapshot_then_owner_close_before_peers(self):
        import dataclasses
        from test_media_backup_b3_execution import FullExecutionTests,m
        fixture=FullExecutionTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        image=dict(Id=fixture.policy.worker.image_id,Architecture='arm64',Os='linux',
            Config=dict(Volumes=None,ExposedPorts=None,Env=list(fixture.policy.worker.public_env),
                Entrypoint=m._baseline._ENTRY,Cmd=['attest_worker'],User='10001:10001',WorkingDir='/opt/easel'),
            RootFS=dict(Type='layers',Layers=['sha256:'+'f'*64]))
        fixture.policy=dataclasses.replace(fixture.policy,worker=dataclasses.replace(fixture.policy.worker,image_inspect_sha256=m.digest(image)))
        factory=fixture.case.factory();events=[];final=fixture.case.input
        tree=ast.parse((HERE.parent/'scripts/b3_target_once.py').read_text())
        actual=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='actual')
        block=next(n.body for n in ast.walk(actual) if isinstance(n,ast.With) and 'trusted_anchor_intent' in ast.unparse(n.items[0]))
        start=next(i for i,n in enumerate(block) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='factory' for t in n.targets))
        end=next(i for i,n in enumerate(block) if isinstance(n,ast.If) and ast.unparse(n.test)=='execution_owner is None')
        part=ast.Module(body=block[start:end+1],type_ignores=[])
        observer=NS(_unknown=False,_active=False,_processes=[],snapshot=lambda *a:(events.append('post') or {'baseline':True}))
        env=dict(make_factory=lambda *a:factory,m={},envelope={'config':{'format':'b3-target-root-config-attest-v1',
            'execution_policy':json.loads(json.dumps(dataclasses.asdict(fixture.policy)))}},execution_owner=None,
            stage_authority=object(),contexts=[],directory=Path('/synthetic'),fds=[],closers=[],generators=[],
            handles=[],check_budget=lambda:None,finalobj=final,central=None,identity=None,psycopg=None,PUBLIC_016=None,observer=observer,
            graph_state=caller.graph_state,source_baseline={'baseline':True},write_new=lambda *a:None,
            canonical=caller.canonical,sha=caller.sha,require=caller.require,outcome={},parser_runner=NS(remote_state='TERMINAL',_retained_processes=[]))
        # Run the real pre-capture context digest producer too; starting only at
        # make_factory would miss an incompatible earlier top-level projection.
        from platform_service.media_backup_b3.contracts import ComparisonContext
        env['contexts']=list(final._stage_authority.contexts)
        self.assertTrue(all(type(ctx) is ComparisonContext for _,ctx in env['contexts']))
        context_assignment=next(node for node in ast.walk(actual) if isinstance(node,ast.Assign)
            and any(ast.unparse(target)=="outcome['context_sha256']" for target in node.targets))
        exec(compile(ast.Module(body=[context_assignment],type_ignores=[]),'<real pre-capture context producer>','exec'),env)
        legacy_contexts=list(env['outcome']['context_sha256'])
        independently_full=[m.digest(dataclasses.asdict(ctx)) for _,ctx in env['contexts']]
        self.assertNotEqual(legacy_contexts,independently_full)
        self.assertEqual(legacy_contexts,[caller.sha(caller.canonical(dict(kind=kind,
            catalog=ctx.profile.catalog_sha256,row_pins=ctx.row_order_pins))) for kind,ctx in env['contexts']])
        # These inputs are held before constructing the execution owner, like
        # workflow's independent original Root job/spec/manifest context.
        source=factory._policy._source;original_job=os.fstat(factory._policy._job_fd)
        import math
        held_root_context=dict(restore_id=source._restore_id,source_container_id=source._container_id,
            operation_ids={spec.kind:spec.operation_nonce for spec in factory._policy._specs},
            job_device=original_job.st_dev,job_inode=original_job.st_ino,
            started_at_ns=math.ceil(source._started_at*10**9),deadline_ns=math.floor(source._deadline*10**9))
        original_init=m.RootExecutionOwner.__init__
        def initialize(owner,*args):original_init(owner,*args);fixture.transport(owner)
        with patch.object(m.RootExecutionOwner,'__init__',initialize):exec(compile(part,'<synthetic caller composition>','exec'),env)
        owner=env['execution_owner'];self.assertIs(owner._factory,factory);self.assertIs(owner._input,final)
        self.assertEqual(events,['post']);self.assertFalse(final._closed)
        self.assertEqual(env['outcome']['normal_work_state']['execution_committed_count'],2)
        self.assertEqual([s['kind'] for s in env['outcome']['execution']['summaries']],['central','identity'])
        env['closers']=[NS(close=lambda:events.append('peer'))]
        caller.cleanup_known(env,env['outcome'])
        self.assertEqual(events,['post','peer']);self.assertTrue(owner._closed);self.assertTrue(final._closed)
        self.assertEqual([row['phase'] for row in env['cleanup_attempts']],['EXECUTION_CLOSE','FINAL','CLOSER'])
        self.assertFalse(env['outcome']['final_work_state']['execution_unknown'])
        # Use a closed complete terminal scaffold for the unrelated preflight
        # evidence; context/targets/execution/graphs come from the real producer
        # composition above, never copied to make two different pins agree.
        from test_b3_target_supervisor_pure import WorkflowControls
        scaffold=WorkflowControls();scaffold.exercise()
        supervisor,legacy_report=scaffold.terminal_fixture
        legacy_report['context_sha256']=legacy_contexts
        self.assertTrue(supervisor.validate_terminal(legacy_report,legacy_report['manifest_sha256'],901.))
        terminal=copy.deepcopy(legacy_report);terminal.update(env['outcome'])
        terminal['status']='target_worker_attestation_verified'
        self.assertTrue(supervisor.validate_terminal(terminal,terminal['manifest_sha256'],source._deadline,
            expected_root_context=held_root_context))
        self.assertEqual(terminal['context_sha256'],independently_full)
        self.assertEqual(terminal['execution']['context_sha256'],dict(zip(('central','identity'),independently_full)))
        changed_full=m.digest(dataclasses.asdict(dataclasses.replace(env['contexts'][0][1],database_name='changed_source')))
        self.assertNotEqual(changed_full,independently_full[0])
        for key in ('top-level-legacy','top-level-full-tamper','execution-map-full-tamper','summary-full-tamper'):
            bad=copy.deepcopy(terminal)
            if key=='top-level-legacy':bad['context_sha256']=legacy_contexts
            elif key=='top-level-full-tamper':bad['context_sha256'][0]=changed_full
            elif key=='execution-map-full-tamper':bad['execution']['context_sha256']['central']=changed_full
            else:bad['execution']['summaries'][0]['binding_data']['context_sha256']=changed_full
            with self.subTest(tamper=key),self.assertRaises(ValueError):
                supervisor.validate_terminal(bad,bad['manifest_sha256'],source._deadline,
                    expected_root_context=held_root_context)



class OwnedSourceCallerHelpers(unittest.TestCase):
    def test_internal_helpers_are_not_selected_by_main_or_actual(self):
        tree=ast.parse((HERE.parent/'scripts/b3_target_once.py').read_text())
        helpers={'begin_owned_source_capture','finish_owned_source_capture','finish_known_failed_owned_source_capture','release_owned_source_capture'}
        self.assertTrue(helpers<={n.name for n in tree.body if isinstance(n,ast.FunctionDef)})
        for method in (n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in ('actual','main')):
            called={n.func.id for n in ast.walk(method) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)}
            self.assertFalse(helpers&called)

    def test_real_terminal_pair_finishes_owners_before_graph_and_explicit_release(self):
        import asyncio
        import pytest
        from test_media_backup_b2_pipeline import structural_flow,attach_owned_companion,run_flow
        async def exercise(path,monkeypatch):
            with structural_flow(path,monkeypatch) as env:
                pair,_=attach_owned_companion(env,monkeypatch)
                await run_flow(env)
                owners=tuple(p._owner for p in pair._producers)
                graph=dict(inputs=env.inputs,runner=env.runner,source_owners=list(owners))
                self.assertTrue(caller.graph_state(graph)['unknown'])
                caller.finish_owned_source_capture(owners,pair)
                state=caller.graph_state(graph)
                self.assertFalse(state['unknown']);self.assertEqual(state['source_publication_fds'],2)
                out={};caller.cleanup_known(graph,out)
                self.assertTrue(out['cleanup_refused_live_work'])
                caller.release_owned_source_capture(pair)
                self.assertEqual(caller.graph_state(graph)['source_publication_fds'],0)
        with tempfile.TemporaryDirectory() as tmp,pytest.MonkeyPatch.context() as monkeypatch:
            asyncio.run(exercise(Path(tmp),monkeypatch))
