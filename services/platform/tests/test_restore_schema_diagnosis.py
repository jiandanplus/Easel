"""Only synthetic SQL is used; actual schemas stay on the deployment host."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import json
import hashlib
import io
import tarfile
from types import SimpleNamespace

import pytest


@pytest.fixture
def lib():
    path = Path(__file__).parents[1] / 'scripts' / 'diagnose_restore_schema_difference_v3.py'
    spec = spec_from_file_location('schema_diagnosis', path)
    value = module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_order_difference_is_observed_without_claiming_schema_equality(lib):
    result = lib.schema_summary(b'CREATE TABLE "synthetic_a" ();\nCREATE TABLE "synthetic_b" ();\n',
                                b'CREATE TABLE "synthetic_b" ();\nCREATE TABLE "synthetic_a" ();\n')
    assert result['exact_schema_hash_match'] is False
    assert result['line_multiset_equal'] is True
    assert result['line_order_only'] is True
    assert result['source_only_line_count'] == result['target_only_line_count'] == 0
    assert 'semantic_equal' not in result


def test_changed_acl_and_owner_lines_are_counted_without_exporting_sql(lib):
    source = b'ALTER SCHEMA "private_fixture" OWNER TO "source_fixture";\nGRANT USAGE ON SCHEMA "private_fixture" TO "recipient_fixture";\n'
    target = b'ALTER SCHEMA "private_fixture" OWNER TO "target_fixture";\nREVOKE USAGE ON SCHEMA "private_fixture" FROM "recipient_fixture";\n'
    result = lib.schema_summary(source, target)
    assert result['source_only_categories'] == {'alter_schema': 1, 'grant': 1}
    assert result['target_only_categories'] == {'alter_schema': 1, 'revoke': 1}
    assert result['line_order_only'] is False
    rendered = json.dumps(result)
    for private in ['private_fixture', 'source_fixture', 'target_fixture', 'recipient_fixture', 'ALTER SCHEMA']:
        assert private not in rendered


def test_function_bodies_defaults_and_passwords_are_never_returned(lib):
    source = b"-- Name: customer_private; Type: FUNCTION; Schema: private; Owner: private\nCREATE FUNCTION private.customer_private() RETURNS text AS $$\nBEGIN\nRETURN 'secret_default_fixture';\nEND; $$ LANGUAGE plpgsql;\nALTER ROLE private PASSWORD 'synthetic_password_fixture';\n"
    target = source.replace(b'secret_default_fixture', b'new_default_fixture').replace(b'synthetic_password_fixture', b'other_password_fixture')
    result = lib.schema_summary(source, target)
    assert result['source_only_line_count'] == result['target_only_line_count'] == 2
    rendered = json.dumps(result)
    for private in ['customer_private', 'secret_default_fixture', 'new_default_fixture', 'synthetic_password_fixture', 'other_password_fixture', 'RETURN', 'PASSWORD']:
        assert private not in rendered


def test_only_already_reviewed_random_restrict_lines_are_ignored(lib):
    source = b'\\restrict AbC123\nCREATE TABLE "fixture" ();\n\\unrestrict AbC123\n'
    target = b'\\restrict XYZ987\nCREATE TABLE "fixture" ();\n\\unrestrict XYZ987\n'
    result = lib.schema_summary(source, target)
    assert result['exact_schema_hash_match'] is True
    assert result['line_multiset_equal'] is True
    assert result['line_order_only'] is False
    changed = lib.schema_summary(source, target.replace(b'();', b'(value int);'))
    assert changed['exact_schema_hash_match'] is False
    assert changed['source_only_categories'] == {'create_table': 1}


def test_size_and_line_bounds_fail_closed(lib):
    with pytest.raises(ValueError):
        lib.schema_summary(b'012345', b'', maximum=5)
    with pytest.raises(ValueError):
        lib.schema_summary(b'\n' * 100001, b'')
    with pytest.raises(UnicodeDecodeError):
        lib.schema_summary(b'\xff', b'')


def test_headers_and_unknown_content_only_return_fixed_category_names(lib):
    result = lib.schema_summary(b'-- private_ddl_fixture\nSECRET_UNKNOWN_CONTENT\n\n', b'')
    assert result['source_only_categories'] == {'blank': 1, 'body_or_continuation': 1, 'comment': 1}
    assert result['source_only_line_count'] == 3
    assert 'private_ddl_fixture' not in json.dumps(result)


def test_matching_archive_render_is_used_without_reading_current_source(lib):
    value = b'CREATE TABLE "private_fixture" ();\n'
    expected = lib.schema_summary(value, value)['source_schema_sha256']
    def forbidden():
        raise AssertionError('current source must not be read')
    selected, receipt = lib.verified_schema_source(value, expected, forbidden)
    assert selected == value
    assert receipt['comparison_source'] == 'authenticated_custom_dump_render'
    assert receipt['archive_render_matches_captured_baseline'] is True


def test_current_source_is_accepted_only_when_it_matches_archive_baseline_hash(lib):
    value = b'CREATE TABLE "private_fixture" ();\n'
    expected = lib.schema_summary(value, value)['source_schema_sha256']
    calls = []
    def read():
        calls.append('read_only_schema')
        return value
    selected, receipt = lib.verified_schema_source(b'-- archive rendering differs\n', expected, read)
    assert selected == value and calls == ['read_only_schema']
    assert receipt['comparison_source'] == 'current_source_schema_verified_against_archived_hash'
    assert receipt['archive_render_matches_captured_baseline'] is False
    assert receipt['source_matches_captured_baseline'] is True


def test_changed_current_source_is_rejected_without_approximate_comparison(lib):
    original = b'CREATE TABLE "private_fixture" ();\n'
    expected = lib.schema_summary(original, original)['source_schema_sha256']
    with pytest.raises(ValueError, match='schema_diagnosis_rejected'):
        lib.verified_schema_source(b'-- different rendering\n', expected,
                                   lambda: b'CREATE TABLE "private_fixture" (changed int);\n')


def test_finer_table_default_context_only_exports_fixed_hints(lib):
    header = b'-- Name: private_table_fixture; Type: TABLE; Schema: private_schema; Owner: private_owner\n'
    source = header + b'    "private_column" text DEFAULT private_function() NOT NULL,\n'
    target = header + b'    "private_column" text DEFAULT other_function() NOT NULL,\n'
    result = lib.schema_detail_summary(source, target)
    assert result['source_context_counts'] == result['target_context_counts'] == {'table': 1}
    assert result['source_prefix_counts'] == result['target_prefix_counts'] == {'quoted_identifier_definition': 1}
    assert result['source_keyword_hint_counts'] == result['target_keyword_hint_counts'] == {'default': 1, 'not_null': 1}
    assert result['detail_basis'] == 'pg_dump_header_and_line_prefix_hints_only'
    for private in ['private_table_fixture', 'private_schema', 'private_owner', 'private_column', 'private_function', 'other_function']:
        assert private not in json.dumps(result)


def test_finer_constraints_and_function_body_do_not_export_sql(lib):
    source = (b'-- Name: private_constraint; Type: CONSTRAINT; Schema: private; Owner: private\n'
              b'    ADD CONSTRAINT "private_constraint" CHECK (private_value > 0);\n'
              b'-- Name: private_fn(); Type: FUNCTION; Schema: private; Owner: private\n'
              b"RETURN 'private_default_fixture';\n")
    target = source.replace(b'> 0', b'> 1').replace(b'private_default_fixture', b'other_default_fixture')
    result = lib.schema_detail_summary(source, target)
    assert result['source_context_counts'] == {'constraint': 1, 'function': 1}
    assert result['source_prefix_counts'] == {'add_constraint': 1, 'return': 1}
    assert result['source_keyword_hint_counts'] == {'check': 1}
    assert 'private' not in json.dumps(result) and 'other_default' not in json.dumps(result)


@pytest.mark.parametrize('fail_target_dump,mismatch_prior', [(False, False), (True, False), (False, True)])
def test_fixed_diagnosis_stops_owned_target_even_when_dump_fails(lib, monkeypatch, tmp_path, capsys, fail_target_dump, mismatch_prior):
    source = b'CREATE TABLE "synthetic_private_source" ();\n'
    target = b'CREATE TABLE "synthetic_private_source" (value int);\n'
    baseline = {'schema': lib.schema_summary(source, source)['source_schema_sha256'],
                'database': {'datname': 'synthetic_private_db'}}
    prior = {'nonce': lib.NONCE, 'plan_sha256': lib.PLAN_SHA, 'phase': 'diagnosis_complete',
             'differing_fields': ['schema'], 'summaries': {'schema': {
                 # Existing inventory diagnosis hashes canonical JSON of the field.
                 'actual_sha256': hashlib.sha256(json.dumps(
                     lib.schema_summary(target, target)['source_schema_sha256'],
                     sort_keys=True).encode()).hexdigest()}}}
    if mismatch_prior:
        # Earlier target had no column; the read now contains a new column.
        prior['summaries']['schema']['actual_sha256'] = hashlib.sha256(json.dumps(
            lib.schema_summary(source, source)['source_schema_sha256'], sort_keys=True).encode()).hexdigest()
    root = tmp_path / 'owned_root'
    root.mkdir()
    (root / 'intent.json').write_text(json.dumps({'plan_sha256': lib.PLAN_SHA}))
    (root / 'diagnosis-v4-safe.json').write_text(json.dumps(prior))
    (root / 'schema-diagnosis-safe.json').write_text(json.dumps({
        'nonce': lib.NONCE, 'plan_sha256': lib.PLAN_SHA, 'diagnosis_error': True,
        'phase': 'reading_owned_target_schema'}))
    (root / 'schema-diagnosis-v2-safe.json').write_text(json.dumps({
        'nonce': lib.NONCE, 'plan_sha256': lib.PLAN_SHA, 'phase': 'schema_diagnosis_complete',
        'source_matches_captured_baseline': True, 'target_matches_prior_inventory': True}))
    tool, cipher = tmp_path / 'synthetic_tool.py', tmp_path / 'synthetic.sealed'
    tool.write_bytes(b'# synthetic code\n')
    cipher.write_bytes(b'synthetic ciphertext')
    (root / 'transfer-private.pem').write_bytes(b'synthetic key only')
    monkeypatch.setattr(lib, 'ROOT', root)
    monkeypatch.setattr(lib, 'TOOL', tool)
    monkeypatch.setattr(lib, 'TOOL_SHA', hashlib.sha256(tool.read_bytes()).hexdigest())
    monkeypatch.setattr(lib, 'CIPHER', cipher)
    monkeypatch.setattr(lib, 'CIPHER_SHA', hashlib.sha256(cipher.read_bytes()).hexdigest())
    payload = io.BytesIO()
    manifest = {'format': 'easel-encrypted-backup-v2', 'plan_sha256': lib.PLAN_SHA}
    with tarfile.open(fileobj=payload, mode='w') as archive:
        for name, data in [('manifest.json', json.dumps(manifest).encode()),
                           ('inventories/central.json', json.dumps(baseline).encode()),
                           ('databases/central.dump', b'PGDMPsynthetic')]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    events = []
    def command(args, **kwargs):
        if args[1] == 'inspect':
            return json.dumps([{'State': {'Running': False, 'Status': 'exited'}}]).encode()
        if args[1] == 'start':
            events.append('start_owned')
            return b'owned'
        if 'pg_restore' in args:
            assert '--schema-only' in args and '--file=-' in args and kwargs['data'].startswith(b'PGDMP')
            return source
        if 'pg_dump' in args:
            if fail_target_dump:
                raise RuntimeError('synthetic private SQL must not escape')
            return target
        raise AssertionError('unexpected command')
    def require(value):
        if not value:
            raise ValueError('safe rejection')
    backup = SimpleNamespace(
        ROOT=root, read_fixed=lambda path, *_: path.read_bytes(), command=command,
        crypto=lambda: SimpleNamespace(unseal=lambda *_: payload.getvalue()),
        verify_payload=lambda *_: events.append('verified_payload'),
        container_gate=lambda *_: pytest.fail('matching archive must avoid production read'),
    )
    fake = SimpleNamespace(
        base_module=lambda: backup, resource_gate=lambda: None, require=require,
        container_identity=lambda *_: {'name': 'synthetic_owned_target'},
        guard_target=lambda *_: events.append('guard_owned'), target_ready=lambda *_: True,
        stop_target=lambda *_: events.append('stop_owned'), MAXIMUM=lib.MAXIMUM, TARGET='synthetic_target',
        digest=lambda data: hashlib.sha256(data).hexdigest(),
        canonical=lambda data: json.dumps(data, sort_keys=True).encode(),
        schema_digest=lambda data: lib.schema_summary(data, data)['source_schema_sha256'],
        write_private=lambda path, data: path.write_bytes(data),
    )
    monkeypatch.setattr(lib.importlib.util, 'spec_from_file_location', lambda *_: SimpleNamespace(loader=SimpleNamespace(exec_module=lambda _: None)))
    monkeypatch.setattr(lib.importlib.util, 'module_from_spec', lambda _: fake)
    monkeypatch.setattr(lib.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(lib.os, 'umask', lambda _: None)
    monkeypatch.setattr(lib.resource, 'setrlimit', lambda *_: None)
    monkeypatch.setattr(lib.signal, 'signal', lambda *_: None)
    monkeypatch.setattr(lib.socket, 'gethostname', lambda: 'ser362540173134')
    lib.main()
    assert events.count('start_owned') == events.count('stop_owned') == 1
    assert events[-1] == 'stop_owned'
    result = json.loads(capsys.readouterr().out)
    assert result['reapply'] is False and result['production_writes'] == 0
    if fail_target_dump:
        assert result['diagnosis_error'] is True and result['phase'] == 'reading_owned_target_schema'
    elif mismatch_prior:
        assert result['diagnosis_error'] is True and result['phase'] == 'verifying_owned_target_schema_hash'
        assert result['target_dump_completed'] is True
        assert result['observed_target_inventory_summary_sha256'] != result['prior_inventory_target_summary_sha256']
        assert result['source_matches_captured_baseline'] is True
        assert result['owned_target_repeat_summary']['exact_schema_hash_match'] is True
        assert 'source_only_categories' not in result
    else:
        assert result['phase'] == 'schema_diagnosis_complete'
        assert result['archive_render_matches_captured_baseline'] is True
        assert result['observed_target_schema_sha256'] != result['observed_target_inventory_summary_sha256']
        assert result['observed_target_inventory_summary_sha256'] == result['prior_inventory_target_summary_sha256']
    assert 'synthetic_private' not in json.dumps(result)
