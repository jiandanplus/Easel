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

NONCE='b547406d5dbb48a980c14c3d97e0fbd5'
PLAN_SHA='4b491b8c0cd4fff13da377c3fd73f3b146afe0e32166bdb22e07051cb5c0aff3'
TOOL=Path('/root/easel-restore-drill-review-20261027/verify_encrypted_restore_drill_v3.py')
TOOL_SHA='f47ee363d2c5ac3c876d1114225175728d1b11c023e387fc9f3223514f630bcc'
CIPHER=Path('/var/backups/easel-encrypted-v2-drill/20261026T191624Z-9d5c0caa-043a-4fe5-abb8-535fe47e2568.sealed')
CIPHER_SHA='f9268acee8c171fbee7ac710a8986f539c2568e8a916a495eb68b7579c1db7f2'
BACKUP_ID='9d5c0caa-043a-4fe5-abb8-535fe47e2568'
ROOT=Path('/root/easel-restore-drill-20261027-'+NONCE)

def database_exists_statement(lib,name):
    return 'SELECT to_json(EXISTS(SELECT 1 FROM pg_database WHERE datname='+lib.literal(name)+'))'

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
    lib.require(not (ROOT/'restore-safe.json').exists() and not (ROOT/'diagnosis-v3-safe.json').exists())
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
        expected_roles={row['rolname']:row for row in baseline['roles']};actual_roles={row['rolname']:row for row in roles}
        report['role_presence_match']=set(expected_roles)==set(actual_roles)
        report['role_count_match']=len(expected_roles)==len(actual_roles)
        report['role_attribute_differences']=sorted({key for name in set(expected_roles)&set(actual_roles) for key in expected_roles[name] if expected_roles[name][key]!=actual_roles[name].get(key)})
        report['roles_match']=lib.canonical(roles)==lib.canonical(baseline['roles'])
        report['roles_sha256']=lib.digest(lib.canonical(roles))
        report['expected_roles_sha256']=lib.digest(lib.canonical(baseline['roles']))
        phase='target_database_check'
        exists=read(database_exists_statement(lib,baseline['database']['datname']))
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
    lib.write_private(ROOT/'diagnosis-v3-safe.json',lib.canonical(report))
    print(json.dumps(report,sort_keys=True))

if __name__=='__main__':
    try:main()
    except BaseException:
        print('{"status":"diagnosis_rejected"}');sys.exit(1)
