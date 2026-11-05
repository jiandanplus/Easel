#!/usr/bin/env python3
"""Read only diagnosis of one stopped, owned failed restore; no re-apply."""
import hashlib
import importlib.util
import io
import json
import os
import signal
from pathlib import Path
import resource
import socket
import sys
import tarfile
import time

NONCE='083b087376364b99961b3126c364bce9'
PLAN_SHA='97efa75e06fc55b31a3ec8f4c944a6afd9921d44a13d7f6d0a2d0da76a8e72cc'
TOOL=Path('/root/easel-restore-drill-review-20261027/verify_encrypted_restore_drill.py')
TOOL_SHA='cd463029cfb7463705031f086f49c0f62ad71576838fad56f64d91bbd3c5872b'
CIPHER=Path('/var/backups/easel-encrypted-v2-drill/20261026T185149Z-6080aefc-35ac-4da7-96fd-7dfef21edad7.sealed')
CIPHER_SHA='e6102315fe5862fcf7096201c28a267a54d893ef7969a230c9521dc7c6fcdca3'
BACKUP_ID='6080aefc-35ac-4da7-96fd-7dfef21edad7'
ROOT=Path('/root/easel-restore-drill-20261027-'+NONCE)

def main():
    os.umask(0o077);resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    def interrupted(*_):raise RuntimeError('interrupted')
    signal.signal(signal.SIGTERM,interrupted);signal.signal(signal.SIGHUP,interrupted)
    assert os.geteuid()==0
    assert socket.gethostname()=='ser362540173134'
    assert hashlib.sha256(TOOL.read_bytes()).hexdigest()==TOOL_SHA
    spec=importlib.util.spec_from_file_location('reviewed_restore_drill',TOOL);lib=importlib.util.module_from_spec(spec);spec.loader.exec_module(lib)
    backup=lib.base_module();lib.resource_gate()
    intent=json.loads(backup.read_fixed(ROOT/'intent.json',65536));lib.require(intent['plan_sha256']==PLAN_SHA)
    lib.require(not (ROOT/'restore-safe.json').exists() and not (ROOT/'diagnosis-safe.json').exists())
    item=lib.container_identity(NONCE,'central');item['user']='easel_restore_admin'
    lib.guard_target(backup,item)
    state=json.loads(backup.command(['/usr/bin/docker','inspect',item['name']],maximum=1024*1024))[0]['State']
    lib.require(state['Running'] is False and state['Status']=='exited')
    ciphertext=backup.read_fixed(CIPHER,lib.MAXIMUM+1024*1024);lib.require(lib.digest(ciphertext)==CIPHER_SHA)
    private=backup.read_fixed(backup.ROOT/'transfer-private.pem',32768)
    payload=backup.crypto().unseal(private,ciphertext,BACKUP_ID,'central',lib.TARGET)
    with tarfile.open(fileobj=io.BytesIO(payload),mode='r:') as archive:
        manifest=json.loads(archive.extractfile('manifest.json').read(lib.MAXIMUM+1))
        backup.verify_payload(payload,manifest)
        baseline=json.loads(archive.extractfile('inventories/central.json').read(lib.MAXIMUM+1))
    lib.require(manifest['format']=='easel-encrypted-backup-v2' and manifest['plan_sha256']==PLAN_SHA)
    phase='starting_owned_target';started=False;report={}
    try:
        started=True
        backup.command(['/usr/bin/docker','start',item['name']],maximum=65536)
        deadline=time.monotonic()+60
        while True:
            try:
                backup.command(['/usr/bin/docker','exec',item['name'],'pg_isready','--username='+item['user'],'--dbname=postgres'],maximum=65536,timeout=5)
                break
            except backup.Rejected:
                lib.require(time.monotonic()<deadline);time.sleep(.5)
        lib.guard_target(backup,item)
        read=lambda sql:lib.psql(backup.command,item['name'],item['user'],'postgres',sql)
        phase='target_role_check'
        roles=read(lib.ROLES.format(ignore=lib.literal('')))
        report['roles_match']=lib.canonical(roles)==lib.canonical(baseline['roles'])
        report['roles_sha256']=lib.digest(lib.canonical(roles))
        report['expected_roles_sha256']=lib.digest(lib.canonical(baseline['roles']))
        phase='target_database_check'
        exists=read('SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname='+lib.literal(baseline['database']['datname'])+')')
        report['database_exists']=exists
        if exists:
            phase='target_inventory_check'
            actual=lib.collect_inventory(backup.command,item['name'],item['user'],baseline['database']['datname'])
            changed=[key for key in baseline if lib.canonical(baseline[key])!=lib.canonical(actual.get(key))]
            report['differing_fields']=changed
            report['summaries']={key:{'expected_sha256':lib.digest(lib.canonical(baseline[key])),'actual_sha256':lib.digest(lib.canonical(actual.get(key)))} for key in changed}
            report['table_counts_match']=len(actual['tables'])==len(baseline['tables'])
            report['money_checks_match']=actual['money']==baseline['money']
            if 'extensions' in changed:
                report['extension_differing_fields']=sorted({key for expected in baseline['extensions'] for current in actual['extensions'] if expected['extname']==current['extname'] for key in expected if expected[key]!=current.get(key)})
        phase='diagnosis_complete'
    except BaseException:
        report['diagnosis_error']=True
    finally:
        if started:lib.stop_target(backup,item)
    report.update({'status':'read_only_diagnosis','phase':phase,'nonce':NONCE,'plan_sha256':PLAN_SHA,'production_writes':0,'reapply':False,'owned_container_stopped_retained':True})
    lib.write_private(ROOT/'diagnosis-safe.json',lib.canonical(report))
    print(json.dumps(report,sort_keys=True))

if __name__=='__main__':
    try:main()
    except BaseException:
        print('{"status":"diagnosis_rejected"}');sys.exit(1)
