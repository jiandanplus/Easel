#!/usr/bin/env python3
"""Capture a reviewed plan on 154; emit an explicit metadata-only allowlist."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import socket
import stat
import subprocess
import sys

ROOT=Path('/root/easel-settled-media-restore-review-20261027')
DR=ROOT/'verify_settled_media_restore_drill_v1.py'
DR_SHA='49df4b72db760f88022c0dd3e7b4d8e566463fb12ab1a6044a8a2b59bf7f857d'

class Rejected(Exception):pass

def require(value):
    if not value:raise Rejected('plan_capture_rejected')

def digest(raw):return hashlib.sha256(raw).hexdigest()

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()

def unique(pairs):
    result={}
    for key,value in pairs:require(key not in result);result[key]=value
    return result

def safe_plan(plan):
    keys=('format','nonce','target','script_sha256','core_sha256','candidate_sha256','migration_016_sha256',
        'work_dir','dest','release','image','estimated_disk_upper_bytes','inventory_version',
        'central_schema_sha256','central_config_sha256','config_metadata_sha256','media_uid','media_gid',
        'media_root_inventory_sha256','media_window_sha256','media_registry_sha256','initial_media_registry_empty',
        'expected_blob_count','expected_settled_jobs','gc_horizon_seconds','production_writes','stops_production')
    result={key:plan[key] for key in keys}
    result['sources']=[{key:row[key] for key in ('kind','id','image_id')} for row in plan['sources']]
    result['targets']=[{key:row[key] for key in ('kind','name','volume')} for row in plan['targets']]
    result['resource_limits']={key:plan['resource_limits'][key] for key in
        ('simultaneous_restore_containers','memory_bytes','nano_cpus','pids','network','published_ports')}
    result['active_release_processes']={kind:{key:plan['active_release_processes'][kind][key] for key in
        ('pid','command_sha256','cwd_match','executable_match','media_environment_match')} for kind in ('api','console') if kind in plan['active_release_processes']}
    result.update(configuration_count=len(plan['config_hashes']),configuration_hashes_sha256=digest(canonical(plan['config_hashes'])),
        migration_count=len(plan['expected_migrations']),migrations_sha256=digest(canonical(plan['expected_migrations'])))
    return result

def capture_plan(directory,nonce,run=subprocess.run):
    require(isinstance(nonce,str) and re.fullmatch('[0-9a-f]{32}',nonce) is not None)
    directory=Path(directory);info=directory.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid==os.geteuid() and stat.S_IMODE(info.st_mode)==0o700)
    target=directory/('plan-'+nonce+'-private.json')
    if target.exists() or target.is_symlink():raise FileExistsError('private plan already exists')
    result=run([sys.executable,'-B',str(DR),'--nonce',nonce],capture_output=True,timeout=90,
        env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C','HOME':'/root'},check=False)
    require(result.returncode==0 and len(result.stdout)<=65536 and len(result.stderr)<=65536)
    try:value=json.loads(result.stdout,object_pairs_hook=unique)
    except (ValueError,UnicodeDecodeError):raise Rejected('plan_capture_rejected') from None
    plan=value['plan'];raw=canonical(plan)
    require(value['plan_sha256']==digest(raw) and plan['nonce']==nonce and plan['script_sha256']==DR_SHA)
    require(plan['format']=='easel-settled-media-restore-plan-v1' and plan['initial_media_registry_empty'] is False
            and plan['expected_blob_count']==2 and plan['expected_settled_jobs']==2 and plan['gc_horizon_seconds']==1800)
    safe=safe_plan(plan)
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        view=memoryview(raw)
        while view:
            count=os.write(fd,view);require(count>0);view=view[count:]
        os.fchmod(fd,0o600);os.fsync(fd)
    finally:os.close(fd)
    parent=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(parent)
    finally:os.close(parent)
    return {'plan_path':str(target),'plan_sha256':digest(raw),'safe_plan':safe}

def main():
    os.umask(0o077);resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    require(os.geteuid()==0 and socket.gethostname()=='ser362540173134')
    for parent in DR.parents:
        info=parent.lstat();require(stat.S_ISDIR(info.st_mode) and info.st_uid==0 and not stat.S_IMODE(info.st_mode)&0o022)
    info=DR.lstat();require(stat.S_ISREG(info.st_mode) and (info.st_uid,stat.S_IMODE(info.st_mode),info.st_nlink)==(0,0o600,1))
    require(digest(DR.read_bytes())==DR_SHA)
    parser=argparse.ArgumentParser();parser.add_argument('--nonce',required=True);args=parser.parse_args()
    print(json.dumps(capture_plan(ROOT,args.nonce),sort_keys=True))

if __name__=='__main__':
    try:main()
    except BaseException:
        print('easel media restore plan capture rejected',file=sys.stderr);sys.exit(1)
