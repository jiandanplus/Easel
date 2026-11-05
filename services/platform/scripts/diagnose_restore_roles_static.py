#!/usr/bin/env python3
"""Fixed server-only archive parser diagnosis, no SQL and no target startup."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import resource
import socket
import sys
import tarfile

TOOL=Path('/root/easel-restore-drill-review-20261027/verify_encrypted_restore_drill.py')
TOOL_SHA='cd463029cfb7463705031f086f49c0f62ad71576838fad56f64d91bbd3c5872b'
NONCE='083b087376364b99961b3126c364bce9'
PLAN_SHA='97efa75e06fc55b31a3ec8f4c944a6afd9921d44a13d7f6d0a2d0da76a8e72cc'
ROOT=Path('/root/easel-restore-drill-20261027-'+NONCE)
CIPHER=Path('/var/backups/easel-encrypted-v2-drill/20261026T185149Z-6080aefc-35ac-4da7-96fd-7dfef21edad7.sealed')
CIPHER_SHA='e6102315fe5862fcf7096201c28a267a54d893ef7969a230c9521dc7c6fcdca3'

def main():
    os.umask(0o077);resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    assert os.geteuid()==0 and socket.gethostname()=='ser362540173134'
    assert hashlib.sha256(TOOL.read_bytes()).hexdigest()==TOOL_SHA
    spec=importlib.util.spec_from_file_location('frozen_restore',TOOL);lib=importlib.util.module_from_spec(spec);spec.loader.exec_module(lib)
    backup=lib.base_module();intent=json.loads(backup.read_fixed(ROOT/'intent.json',65536));lib.require(intent['plan_sha256']==PLAN_SHA)
    cipher=backup.read_fixed(CIPHER,lib.MAXIMUM+1024*1024);lib.require(lib.digest(cipher)==CIPHER_SHA)
    private=backup.read_fixed(backup.ROOT/'transfer-private.pem',32768)
    payload=backup.crypto().unseal(private,cipher,'6080aefc-35ac-4da7-96fd-7dfef21edad7','central',lib.TARGET)
    with tarfile.open(fileobj=io.BytesIO(payload),mode='r:') as archive:
        manifest=json.loads(archive.extractfile('manifest.json').read(lib.MAXIMUM+1));backup.verify_payload(payload,manifest)
        roles=archive.extractfile('roles/central.sql').read(lib.MAXIMUM+1)
    lib.require(manifest['plan_sha256']==PLAN_SHA and manifest['format']=='easel-encrypted-backup-v2')
    statement=roles.decode();lines=statement.splitlines();bootstrap='easel_restore_admin'
    report={'status':'static_roles_diagnosis','nonce':NONCE,'plan_sha256':PLAN_SHA,
            'bootstrap_quoted_create_count':lines.count('CREATE ROLE '+lib.ident(bootstrap)+';'),
            'bootstrap_unquoted_create_count':lines.count('CREATE ROLE '+bootstrap+';'),
            'role_create_count':sum(row.startswith('CREATE ROLE ') for row in lines),
            'has_bootstrap_alter':any(row.startswith('ALTER ROLE '+lib.ident(bootstrap)+' ') for row in lines),
            'roles_sha256':lib.digest(roles),'roles_bytes':len(roles),'production_writes':0,'sql_executed':False,'target_started':False,'reapply':False}
    try:
        parsed=lib.bootstrap_roles_sql(statement,bootstrap)
        report.update(bootstrap_parse_ok=True,parsed_bytes=len(parsed.encode()),parsed_has_newlines=chr(10) in parsed)
    except BaseException:report['bootstrap_parse_ok']=False
    lib.write_private(ROOT/'static-diagnosis-safe.json',lib.canonical(report));print(json.dumps(report,sort_keys=True))

if __name__=='__main__':
    try:main()
    except BaseException:print('{"status":"static_diagnosis_rejected"}');sys.exit(1)
