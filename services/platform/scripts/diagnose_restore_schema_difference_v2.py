#!/usr/bin/env python3
"""One fixed failed drill: SQL remains in host memory; output only safe counts.

This diagnoses text differences. It never accepts a difference, changes the
restore verifier, or asserts semantic equality from a line multiset.
"""
from collections import Counter
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import resource
import signal
import socket
import sys
import tarfile
import time

NONCE = '946efa02f3dc46e2b0dfd5f6db7e4934'
PLAN_SHA = '742d06e7a6aaf0d6d3da49a4ed43620ed0381785cb7126dfc2e3186e2593e7e7'
TOOL = Path('/root/easel-restore-drill-review-20261027/verify_encrypted_restore_drill_v4.py')
TOOL_SHA = '55bde7568c29211798641e593b3906049c6013be6b8ae8635d1d5dab23db8542'
CIPHER = Path('/var/backups/easel-encrypted-v2-drill/20261026T234908Z-4a7b858a-9dae-4854-bb3a-bc284c803322.sealed')
CIPHER_SHA = '71e3410f7bcccf0fdc25e74dd1333b5dabb991822dfc6753bf891aee13ece9a2'
BACKUP_ID = '4a7b858a-9dae-4854-bb3a-bc284c803322'
ROOT = Path('/root/easel-restore-drill-20261027-' + NONCE)
MAXIMUM = 32 * 1024 * 1024
SOURCE_NAME = 'easel-central-pg-20261024'
SOURCE_ID = '39f20fd62e58c251cf8af9238139bbc85a67e9bce78bcd01a3c4abad482864a7'


def schema_lines(value, maximum=MAXIMUM):
    if not isinstance(value, bytes) or len(value) > maximum:
        raise ValueError('schema_diagnosis_rejected')
    lines = value.decode('utf-8').splitlines()
    if len(lines) > 100000:
        raise ValueError('schema_diagnosis_rejected')
    # Exactly the existing verifier's reviewed random-token exclusion.
    return [line for line in lines if not re.fullmatch(r'\\(?:un)?restrict [A-Za-z0-9]+', line)]


def line_category(line):
    text = line.strip().upper()
    if not text:
        return 'blank'
    if text.startswith('--'):
        return 'comment'
    if text.startswith('\\'):
        return 'meta_command'
    for action in ('CREATE', 'ALTER', 'DROP'):
        for kind in ('EXTENSION', 'SCHEMA', 'TABLE', 'SEQUENCE', 'FUNCTION', 'PROCEDURE',
                     'TYPE', 'DOMAIN', 'VIEW', 'INDEX', 'TRIGGER', 'POLICY', 'ROLE', 'USER',
                     'DATABASE', 'PUBLICATION', 'SUBSCRIPTION'):
            if re.match(r'^' + action + r'\s+(?:OR\s+REPLACE\s+)?' + kind + r'\b', text):
                return action.lower() + '_' + kind.lower()
        if re.match(r'^' + action + r'\b', text):
            return action.lower() + '_other'
    for action in ('GRANT', 'REVOKE', 'COMMENT', 'SET', 'RESET', 'SELECT', 'SECURITY LABEL'):
        if re.match(r'^' + action + r'\b', text):
            return action.lower().replace(' ', '_')
    return 'body_or_continuation'


def schema_summary(source, target, maximum=MAXIMUM):
    left, right = schema_lines(source, maximum), schema_lines(target, maximum)
    left_counter, right_counter = Counter(left), Counter(right)
    left_only, right_only = left_counter - right_counter, right_counter - left_counter
    def categories(values):
        result = Counter()
        for line, count in values.items():
            result[line_category(line)] += count
        return dict(sorted(result.items()))
    digest = lambda lines: hashlib.sha256('\n'.join(lines).encode()).hexdigest()
    return {
        'source_schema_sha256': digest(left), 'target_schema_sha256': digest(right),
        'exact_schema_hash_match': left == right,
        'source_line_count': len(left), 'target_line_count': len(right),
        'line_multiset_equal': left_counter == right_counter,
        'line_order_only': left != right and left_counter == right_counter,
        'source_only_line_count': sum(left_only.values()),
        'target_only_line_count': sum(right_only.values()),
        'source_only_categories': categories(left_only),
        'target_only_categories': categories(right_only),
        'classification_basis': 'line_prefix_only',
    }


def verified_schema_source(rendered, expected_digest, read_current):
    rendered_digest = hashlib.sha256('\n'.join(schema_lines(rendered)).encode()).hexdigest()
    matched = rendered_digest == expected_digest
    source = rendered if matched else read_current()
    if hashlib.sha256('\n'.join(schema_lines(source)).encode()).hexdigest() != expected_digest:
        raise ValueError('schema_diagnosis_rejected')
    return source, {
        'archive_render_schema_sha256': rendered_digest,
        'archive_render_matches_captured_baseline': matched,
        'source_matches_captured_baseline': True,
        'comparison_source': ('authenticated_custom_dump_render' if matched else
                              'current_source_schema_verified_against_archived_hash'),
    }


def main():
    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    def interrupted(*_):
        raise RuntimeError('schema_diagnosis_interrupted')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, interrupted)
    assert os.geteuid() == 0 and socket.gethostname() == 'ser362540173134'
    assert hashlib.sha256(TOOL.read_bytes()).hexdigest() == TOOL_SHA
    spec = importlib.util.spec_from_file_location('reviewed_restore_drill', TOOL)
    lib = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lib)
    backup = lib.base_module()
    lib.resource_gate()
    intent = json.loads(backup.read_fixed(ROOT / 'intent.json', 65536))
    lib.require(intent['plan_sha256'] == PLAN_SHA)
    lib.require(not (ROOT / 'restore-safe.json').exists() and not (ROOT / 'schema-diagnosis-v2-safe.json').exists())
    prior_schema = json.loads(backup.read_fixed(ROOT / 'schema-diagnosis-safe.json', 65536))
    lib.require(prior_schema['nonce'] == NONCE and prior_schema['plan_sha256'] == PLAN_SHA
                and prior_schema['diagnosis_error'] is True and prior_schema['phase'] == 'reading_owned_target_schema')
    previous = json.loads(backup.read_fixed(ROOT / 'diagnosis-v4-safe.json', 65536))
    lib.require(previous['nonce'] == NONCE and previous['plan_sha256'] == PLAN_SHA
                and previous['phase'] == 'diagnosis_complete' and previous['differing_fields'] == ['schema'])
    item = lib.container_identity(NONCE, 'central')
    item['user'] = 'easel_restore_admin'
    lib.guard_target(backup, item)
    state = json.loads(backup.command(['/usr/bin/docker', 'inspect', item['name']], maximum=1024*1024))[0]['State']
    lib.require(state['Running'] is False and state['Status'] == 'exited')
    ciphertext = backup.read_fixed(CIPHER, lib.MAXIMUM + 1024*1024)
    lib.require(lib.digest(ciphertext) == CIPHER_SHA)
    private = backup.read_fixed(backup.ROOT / 'transfer-private.pem', 32768)
    payload = backup.crypto().unseal(private, ciphertext, BACKUP_ID, 'central', lib.TARGET)
    with tarfile.open(fileobj=io.BytesIO(payload), mode='r:') as archive:
        manifest = json.loads(archive.extractfile('manifest.json').read(lib.MAXIMUM + 1))
        backup.verify_payload(payload, manifest)
        baseline = json.loads(archive.extractfile('inventories/central.json').read(lib.MAXIMUM + 1))
        archived_dump = archive.extractfile('databases/central.dump').read(lib.MAXIMUM + 1)
    lib.require(manifest['format'] == 'easel-encrypted-backup-v2' and manifest['plan_sha256'] == PLAN_SHA)
    phase = 'starting_owned_target'
    started = False
    report = {}
    try:
        started = True
        backup.command(['/usr/bin/docker', 'start', item['name']], maximum=65536)
        deadline = time.monotonic() + 60
        while not lib.target_ready(backup, item):
            lib.require(time.monotonic() < deadline)
            time.sleep(.5)
        lib.guard_target(backup, item)
        phase = 'rendering_archived_schema'
        source = backup.command(['/usr/bin/docker', 'exec', '-i', item['name'], 'pg_restore',
                                 '--schema-only', '--file=-'], data=archived_dump, maximum=MAXIMUM, timeout=90)
        phase = 'verifying_original_schema_hash'
        def read_current_source():
            backup.container_gate(SOURCE_NAME)
            row = json.loads(backup.command(['/usr/bin/docker', 'inspect', SOURCE_NAME], maximum=1024*1024))[0]
            lib.require(row['Id'] == SOURCE_ID and row['Image'] == 'sha256:9e2cf6287f25c3151f4c9421ccce2b17f3e60061c111a29165e0d2cdd9ff0a97'
                        and row['State']['Running'] is True)
            return backup.command(['/usr/bin/docker', 'exec', '--env',
                                   'PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=30000 -c lock_timeout=1000',
                                   SOURCE_NAME, 'pg_dump', '--schema-only', '--quote-all-identifiers', '--lock-wait-timeout=1s',
                                   '--no-password', '--username=easel_restore_admin', '--dbname=' + baseline['database']['datname']],
                                  maximum=MAXIMUM, timeout=90)
        source, source_receipt = verified_schema_source(source, baseline['schema'], read_current_source)
        report.update(source_receipt)
        report['captured_baseline_schema_sha256'] = baseline['schema']
        phase = 'reading_owned_target_schema'
        target_dump = ['/usr/bin/docker', 'exec', item['name'], 'pg_dump', '--schema-only',
                       '--quote-all-identifiers', '--no-password', '--username=' + item['user'],
                       '--dbname=' + baseline['database']['datname']]
        actual = backup.command(target_dump, maximum=MAXIMUM, timeout=90)
        report['target_dump_completed'] = True
        report['observed_target_schema_sha256'] = lib.schema_digest(actual)
        # The existing diagnosis summarizes a field by hashing canonical JSON
        # of its value; schema's value is itself a raw schema digest string.
        report['observed_target_inventory_summary_sha256'] = lib.digest(lib.canonical(report['observed_target_schema_sha256']))
        report['prior_inventory_target_summary_sha256'] = previous['summaries']['schema']['actual_sha256']
        matched = report['observed_target_inventory_summary_sha256'] == report['prior_inventory_target_summary_sha256']
        report['target_matches_prior_inventory'] = matched
        if not matched:
            phase = 'reading_owned_target_schema_repeat'
            repeated = backup.command(target_dump, maximum=MAXIMUM, timeout=90)
            report['owned_target_repeat_summary'] = schema_summary(actual, repeated)
        phase = 'verifying_owned_target_schema_hash'
        lib.require(matched)
        phase = 'classifying_schema_text_difference'
        report.update(schema_summary(source, actual))
        phase = 'schema_diagnosis_complete'
    except BaseException:
        report['diagnosis_error'] = True
    finally:
        if started:
            lib.stop_target(backup, item)
    report.update({'status': 'read_only_schema_diagnosis', 'phase': phase, 'nonce': NONCE,
                   'plan_sha256': PLAN_SHA, 'production_writes': 0, 'reapply': False,
                   'owned_container_stopped_retained': True, 'restore_comparison_changed': False})
    lib.write_private(ROOT / 'schema-diagnosis-v2-safe.json', lib.canonical(report))
    print(json.dumps(report, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except BaseException:
        print('{"status":"schema_diagnosis_rejected"}')
        sys.exit(1)
