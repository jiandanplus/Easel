#!/usr/bin/env python3
"""Explicit, hash-bound candidate and cutover operations; no financial writes.

Official semantics: freedesktop.org systemd.service ExecStart empty assignment
resets the old command; systemd.exec EnvironmentFile overrides Environment.
Raw unit/env text and receipts remain in the root-only server directory.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import subprocess
import time
import traceback
from urllib.parse import urlsplit
from uuid import uuid4

import prepare_m1_image_release as release

CANDIDATE='easel-central-images-candidate.service'
CANDIDATE_PATH=Path('/etc/systemd/system')/CANDIDATE
DROPIN_NAME='90-m1-images.conf'
PREPARE_SHA='ff9d019ef275798b24e12adcc25c8018c9f8f3ff244dc003133f2df4912c02ef'
SMOKE_KEYS=('health','runtime_role','configuration','text_models_unchanged','image_models_disabled',
            'financial_snapshot_unchanged','catalog_auth_required')


def parse_service(text,kind):
    if kind not in {'api','console'}:raise ValueError('invalid_unit_kind')
    values={};section=None
    for line in text.splitlines():
        stripped=line.strip()
        if not stripped or stripped.startswith(('#',';')):continue
        if stripped.startswith('['):section=stripped;continue
        if section!='[Service]':continue
        if stripped.endswith('\\'):raise ValueError('continued_service_line_requires_review')
        key,separator,value=stripped.partition('=')
        if not separator:raise ValueError('invalid_service_line')
        values.setdefault(key,[]).append(value)
    user='easel-central' if kind=='api' else 'easel-console'
    env=str(release.CENTRAL_ENV if kind=='api' else release.CONSOLE_ENV)
    for key,expected in [('User',user),('Group',user),('WorkingDirectory',str(release.SOURCE)),('EnvironmentFile',env)]:
        if values.get(key)!=[expected]:raise ValueError('source_service_scope_mismatch')
    if len(values.get('ExecStart',[]))!=1:raise ValueError('source_execstart_requires_review')
    argv=shlex.split(values['ExecStart'][0])
    if (not argv or argv[0]!=str(release.SOURCE/'.venv/bin/python')
            or any(not re.fullmatch(r'[A-Za-z0-9_./:@=\-\[\]]+',item) for item in argv)):
        raise ValueError('source_execstart_requires_review')
    if flag(argv,'--host')!='127.0.0.1' or flag(argv,'--port')!=('19874' if kind=='api' else '19875'):
        raise ValueError('source_listener_mismatch')
    return values,argv


def flag(argv,name,replacement=None):
    matches=[]
    for index,item in enumerate(argv):
        if item==name:
            if index+1>=len(argv):raise ValueError('incomplete_listener_flag')
            matches.append((index,argv[index+1],False))
        elif item.startswith(name+'='):matches.append((index,item[len(name)+1:],True))
    if len(matches)!=1:raise ValueError('ambiguous_listener_flag')
    index,value,inline=matches[0]
    if replacement is not None:
        argv[index if inline else index+1]=name+'='+replacement if inline else replacement
    return value


def command(text,kind,*,port=None):
    _,argv=parse_service(text,kind);argv[0]=str(release.TARGET/'.venv/bin/python')
    if port is not None:flag(argv,'--port',str(port))
    return ' '.join(argv)


def render_candidate(text):
    values,_=parse_service(text,'api')
    result=text.replace('WorkingDirectory='+values['WorkingDirectory'][0], 'WorkingDirectory='+str(release.TARGET),1)
    result=result.replace('ExecStart='+values['ExecStart'][0],'ExecStart='+command(text,'api',port=19876),1)
    return result.rstrip()+'\n\n[Service]\nEnvironment=CENTRAL_MEDIA_CONFIG_FILE='+str(release.MEDIA)+'\n'


def render_dropin(text,kind):
    result='[Service]\nWorkingDirectory='+str(release.TARGET)+'\nExecStart=\nExecStart='+command(text,kind)+'\n'
    if kind=='api':result+='Environment=CENTRAL_MEDIA_CONFIG_FILE='+str(release.MEDIA)+'\n'
    return result


def smoke_summary(value):
    return {key:value.get(key) is True for key in SMOKE_KEYS}


def systemctl(*args):
    result=subprocess.run(['systemctl',*args],capture_output=True,text=True)
    if result.returncode:raise ValueError('systemd_operation_failed')
    return result.stdout.strip()


def check_helper():
    if release.sha(Path(release.__file__).read_bytes())!=PREPARE_SHA:raise ValueError('release_helper_hash_mismatch')
    release.verify_staged()


def no_unknown_dropins():
    for unit in release.UNITS.values():
        if systemctl('show',unit,'-p','DropInPaths','--value'):raise ValueError('existing_dropin_requires_review')


def installed_media_hash():
    account=pwd.getpwnam('easel-central')
    return release.sha(release.private_bytes(release.MEDIA,65536,owners={account.pw_uid}))


def plan(release_plan_path,release_plan_sha):
    check_helper();no_unknown_dropins()
    original=release.approved_plan(release_plan_path,release_plan_sha)
    if original.get('candidate_sha256')!=release.WHEEL_SHA:raise ValueError('release_plan_mismatch')
    env,media=release.configurations()
    if env.get('CENTRAL_MEDIA_CONFIG_FILE') not in (None,str(release.MEDIA)):raise ValueError('environment_media_override_requires_review')
    units={};dropins={}
    for kind,unit in release.UNITS.items():
        p=Path('/etc/systemd/system')/unit
        text=p.read_text();parse_service(text,kind)
        units[kind]={'path':str(p),'sha256':release.sha(p.read_bytes()),'mode':p.stat().st_mode&0o777,
                     'uid':p.stat().st_uid,'gid':p.stat().st_gid,'original_text':text}
        dropins[kind]=render_dropin(text,kind)
    candidate=render_candidate(units['api']['original_text'])
    media_raw=(json.dumps(media,sort_keys=True,ensure_ascii=False,indent=2,default=str)+'\n').encode()
    value={'version':1,'candidate_sha256':release.WHEEL_SHA,'script_sha256':release.sha(Path(__file__).read_bytes()),
        'release_plan_path':str(release_plan_path),'release_plan_sha256':release_plan_sha,'source_hashes':release.current_hashes(),
        'units':units,'dropins':dropins,'candidate_unit':candidate,'expected_media_sha256':release.sha(media_raw),
        'no_financial_write':True,'no_model_enable':True,'old_binary_restart_compatibility':'not_verified_not_a_database_rollback'}
    path=release.PRIVATE/('cutover-plan-'+uuid4().hex+'-private.json');digest=release.save_private(path,value)
    return {'plan_path':str(path),'plan_sha256':digest,'candidate_port':19876,'production_ports':[19874,19875],
        'candidate_unit_sha256':release.sha(candidate.encode()),'dropin_count':2,'financial_writes':0,'models_enabled':False}


def load_plan(path,digest):
    check_helper();value=release.approved_plan(path,digest)
    if value.get('candidate_sha256')!=release.WHEEL_SHA or value.get('script_sha256')!=release.sha(Path(__file__).read_bytes()):
        raise ValueError('cutover_input_changed')
    return value


def sources_unchanged(value):
    if release.current_hashes()!=value['source_hashes']:raise ValueError('original_unit_or_environment_changed')
    if installed_media_hash()!=value['expected_media_sha256']:raise ValueError('installed_media_changed')


def write_exact(path,text):
    raw=text.encode()
    if path.is_symlink() or path.parent.resolve()!=path.parent:raise ValueError('unsafe_unit_path')
    if path.exists():
        if path.read_bytes()!=raw or path.stat().st_uid!=0 or path.stat().st_mode&0o777!=0o600:raise ValueError('existing_unit_requires_review')
        return
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())


def receipt(prefix,value):
    path=release.PRIVATE/(prefix+'-'+uuid4().hex+'-private.json');digest=release.save_private(path,value)
    return {'receipt_path':str(path),'receipt_sha256':digest,**{key:item for key,item in value.items() if key in {'success','phase','checks','provider_posts_by_smoke_script','financial_writes'}}}


def candidate_start(value,digest,applied_path,applied_sha):
    sources_unchanged(value);no_unknown_dropins()
    if any(release.units_active().values()):raise ValueError('original_units_must_remain_inactive')
    if subprocess.run(['systemctl','is-active','--quiet',CANDIDATE],capture_output=True).returncode==0:
        raise ValueError('existing_candidate_process_requires_review')
    applied=release.approved_plan(applied_path,applied_sha)
    if (applied.get('success') is not True or applied.get('phase')!='applied-disabled'
            or applied.get('plan_sha256')!=value['release_plan_sha256'] or applied.get('candidate_sha256')!=release.WHEEL_SHA
            or applied.get('models_enabled') is not False):raise ValueError('applied_disabled_receipt_required')
    write_exact(CANDIDATE_PATH,value['candidate_unit'])
    systemctl('daemon-reload');systemctl('start',CANDIDATE)
    return receipt('candidate-started',{'success':True,'phase':'candidate-started','cutover_plan_sha256':digest,
        'applied_receipt_sha256':applied_sha,'candidate_sha256':release.WHEEL_SHA,'provider_posts_by_smoke_script':0,'financial_writes':0})


async def runtime_readonly():
    from platform_service.db import Database
    from psycopg.conninfo import make_conninfo
    env=release.env_values(release.CENTRAL_ENV)
    dsn=make_conninfo(env['CENTRAL_DATABASE_URL'],options='-c default_transaction_read_only=on')
    db=Database(dsn,schema=env['CENTRAL_DB_SCHEMA']);await db.open()
    try:await db.require_runtime_role()
    finally:await db.close()


def get_json(path,port):
    import httpx
    env=release.env_values(release.CENTRAL_ENV);host=urlsplit(env['CENTRAL_PUBLIC_URL']).hostname
    with httpx.Client(trust_env=False,follow_redirects=False,timeout=5) as client:
        response=client.get('http://127.0.0.1:'+str(port)+path,headers={'Host':host,'Accept':'application/json'})
    if len(response.content)>65536:raise ValueError('unexpected_smoke_body')
    return response.status_code,response.json()


def process_identity_matches(cwd,executable,argv,target,expected):
    return Path(cwd).resolve()==Path(target).resolve() and Path(executable).resolve()==Path(expected[0]).resolve() and argv==expected


def verify_process(unit,value,port):
    pid=int(systemctl('show',unit,'-p','MainPID','--value'))
    if pid<=1:raise ValueError('candidate_process_not_running')
    root=Path('/proc')/str(pid)
    argv=[item.decode() for item in (root/'cmdline').read_bytes().rstrip(b'\0').split(b'\0')]
    expected=command(value['units']['api']['original_text'],'api',port=port).split(' ')
    if not process_identity_matches(os.readlink(root/'cwd'),os.readlink(root/'exe'),argv,release.TARGET,expected):
        raise ValueError('actual_process_does_not_match_reviewed_unit')


def smoke(value,digest,*,port=19876):
    sources_unchanged(value)
    unit=CANDIDATE if port==19876 else release.UNITS['api']
    if systemctl('show',unit,'-p','WorkingDirectory','--value')!=str(release.TARGET):raise ValueError('candidate_release_not_active')
    verify_process(unit,value,port)
    env,_=release.configurations();dsn,_=release.choose_owner(env)
    original=release.approved_plan(Path(value['release_plan_path']),value['release_plan_sha256'])
    with release.connect_owner(dsn,env['CENTRAL_DB_SCHEMA']) as c:before=release.financial_snapshot(c)
    checks={};status,health=get_json('/health',port);checks['health']=status==200 and health.get('ok') is True
    status,capabilities=get_json('/v2/capabilities?product_id=easel',port)
    checks['image_models_disabled']=status==200 and capabilities.get('media_execution_enabled') is False and capabilities.get('test_media_execution_enabled') is False
    checks['text_models_unchanged']=status==200 and capabilities.get('subscription_execution_enabled') is True
    checks['catalog_auth_required']=all(get_json(path,port)[0]==401 for path in ['/v2/products/easel/models','/v2/products/easel/media-models'])
    asyncio.run(runtime_readonly());checks['runtime_role']=True
    account=pwd.getpwnam('easel-central')
    checks['configuration']=(release.MEDIA.stat().st_uid==account.pw_uid and release.MEDIA.stat().st_mode&0o777==0o600
        and release.STORAGE.stat().st_uid==account.pw_uid and release.STORAGE.stat().st_mode&0o777==0o700)
    with release.connect_owner(dsn,env['CENTRAL_DB_SCHEMA']) as c:
        after=release.financial_snapshot(c)
        models=c.execute('SELECT m.id,m.definition,s.enabled FROM media_models m JOIN media_model_states s ON s.model_id=m.id ORDER BY m.id').fetchall()
        checks['image_models_disabled']=checks['image_models_disabled'] and len(models)==2 and all(not item['enabled'] for item in models)
        checks['text_models_unchanged']=checks['text_models_unchanged'] and all(after[name]==original['financial_snapshot'][name] for name in ['execution_models','execution_model_states'])
    checks['financial_snapshot_unchanged']=before==after
    public=smoke_summary(checks)
    if not all(public.values()):raise ValueError('read_only_smoke_failed')
    return receipt('candidate-smoke' if port==19876 else 'production-smoke',{'success':True,'phase':'candidate-smoke' if port==19876 else 'production-smoke',
        'cutover_plan_sha256':digest,'candidate_sha256':release.WHEEL_SHA,'checks':public,'provider_posts_by_smoke_script':0,
        'financial_writes':0,'private_financial_snapshot':after,'authenticated_catalog_get':'not_performed_no_login_token_created_or_extracted'})


def switch(value,digest,smoke_path,smoke_sha):
    sources_unchanged(value);no_unknown_dropins()
    if any(release.units_active().values()):raise ValueError('original_units_must_remain_inactive')
    proof=release.approved_plan(smoke_path,smoke_sha)
    if (proof.get('success') is not True or proof.get('phase')!='candidate-smoke'
            or proof.get('cutover_plan_sha256')!=digest or proof.get('candidate_sha256')!=release.WHEEL_SHA
            or not all(smoke_summary(proof.get('checks',{})).values())):raise ValueError('exact_candidate_smoke_receipt_required')
    # Preserve the original main unit and all private environment bytes. These
    # exact drop-ins are required inputs of the subsequent backup/restore proof.
    for kind,unit in release.UNITS.items():
        directory=Path('/etc/systemd/system')/(unit+'.d')
        if directory.is_symlink():raise ValueError('unsafe_dropin_directory')
        directory.mkdir(mode=0o755,exist_ok=True)
        if any(directory.iterdir()):raise ValueError('existing_dropin_requires_review')
    systemctl('stop',CANDIDATE)
    for kind,unit in release.UNITS.items():write_exact(Path('/etc/systemd/system')/(unit+'.d')/DROPIN_NAME,value['dropins'][kind])
    systemctl('daemon-reload')
    systemctl('start',*release.UNITS.values())
    return receipt('cutover-started',{'success':True,'phase':'cutover-started','cutover_plan_sha256':digest,
        'candidate_smoke_receipt_sha256':smoke_sha,'candidate_sha256':release.WHEEL_SHA,'financial_writes':0,
        'original_main_units_and_environment_unchanged':True,'dropins_require_backup_capture':True,'old_binary_rollback_not_verified':True})


def main():
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['plan','candidate-start','candidate-smoke','cutover','production-smoke'])
    parser.add_argument('--release-plan',type=Path);parser.add_argument('--expected-release-plan-sha256')
    parser.add_argument('--plan',type=Path);parser.add_argument('--expected-plan-sha256')
    parser.add_argument('--receipt',type=Path);parser.add_argument('--expected-receipt-sha256')
    args=parser.parse_args()
    if os.getuid()!=0:raise ValueError('root_host_operator_required')
    if args.phase=='plan':
        if not args.release_plan or not args.expected_release_plan_sha256:raise ValueError('exact_release_plan_required')
        result=plan(args.release_plan,args.expected_release_plan_sha256)
    else:
        if not args.plan or not args.expected_plan_sha256:raise ValueError('exact_cutover_plan_required')
        value=load_plan(args.plan,args.expected_plan_sha256)
        if args.phase=='candidate-start':
            if not args.receipt or not args.expected_receipt_sha256:raise ValueError('applied_receipt_required')
            result=candidate_start(value,args.expected_plan_sha256,args.receipt,args.expected_receipt_sha256)
        elif args.phase in {'candidate-smoke','production-smoke'}:result=smoke(value,args.expected_plan_sha256,port=19876 if args.phase=='candidate-smoke' else 19874)
        else:
            if not args.receipt or not args.expected_receipt_sha256:raise ValueError('smoke_receipt_required')
            result=switch(value,args.expected_plan_sha256,args.receipt,args.expected_receipt_sha256)
    print(json.dumps(result,sort_keys=True))


if __name__=='__main__':
    try:main()
    except Exception as error:
        path=release.PRIVATE/('cutover-error-'+uuid4().hex+'-private.log')
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as stream:stream.write(traceback.format_exc())
        code=str(error) if isinstance(error,ValueError) and re.fullmatch('[a-z_]{1,100}',str(error)) else 'cutover_operation_failed'
        print(json.dumps({'success':False,'error':code,'private_log_path':str(path)}));raise SystemExit(1)
