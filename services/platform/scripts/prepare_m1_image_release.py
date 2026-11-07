#!/usr/bin/env python3
"""Fixed 154-host M1.1 preparation. Secrets and exact private plans stay there.

No service stop/start, unit edit, payment, credit issue, provider POST, or rollback.
Applying and enabling are separate, explicit exact-plan operations.
"""
import argparse
import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import stat
import subprocess
import traceback
from urllib.parse import urlsplit
from uuid import uuid4
import zipfile

SOURCE=Path('/srv/easel/releases/20261026-reconciliation-r2-v1')
TARGET=Path('/srv/easel/releases/20261027-images-m1-1-size-v2')
PRIVATE=Path('/root/easel-image-release-20261027/size-v2')
EVIDENCE=Path('/root/easel-image-model-sync-20261027')
WHEEL=PRIVATE/'easel_central_platform-0.1.0-py3-none-any.whl'
WHEEL_SHA='f50e52511e941d3bea8e724eddbd68330e20eaa6339c5e2437b3880ca9705abf'
CENTRAL_ENV=Path('/etc/easel-central/central.env')
CONSOLE_ENV=Path('/etc/easel-central/console.env')
EXECUTION=Path('/etc/easel-central/execution.json')
MEDIA=Path('/etc/easel-central/media.json')
STORAGE=Path('/var/lib/easel-central/media')
OWNER_FILES=[Path('/root/easel-migration-20261024/central-connections-private.json'),
             Path('/root/easel-migration-20261024/central-operator-private.json')]
UNITS={'api':'easel-central-production.service','console':'easel-console-production.service'}
PROVIDER_KEY='workbench_deepseek_20261026'
PROVIDER_VERSION='newapi-rc45-images-20261027-v1'
MODELS={
 'gpt-image-2.5-flare':'07bad59f73f221315f4263c6f036136cf5c5e49ced296bfa09c84b16230bd1ff',
 'gpt-image-2.5-sunburst':'7c5f8d098314306da44bac17458e34417929b36d079c3ba49ac63abd22a6c973',
}
ACTIONS=['migrate_014_016','grant_api_runtime_permissions','append_image_right_on_original_grant',
         'publish_two_image_models_disabled','write_private_media_configuration','prepare_owned_storage']


def sha(raw):return hashlib.sha256(raw).hexdigest()


def unique(pairs):
    value={}
    for key,item in pairs:
        if key in value:raise ValueError('duplicate_json_key')
        value[key]=item
    return value


def private_bytes(path, maximum=4*1024*1024, *, owners=None):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode)!=0o600
                or info.st_uid not in (owners or {os.getuid()}) or info.st_size>maximum):
            raise ValueError('private_file_required')
        raw=stream.read(maximum+1)
        if len(raw)>maximum:raise ValueError('private_file_too_large')
        return raw


def read_private(path):return json.loads(private_bytes(path),object_pairs_hook=unique)


def save_private(path,value):
    raw=(json.dumps(value,sort_keys=True,ensure_ascii=False,indent=2,default=str)+'\n').encode()
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
    return sha(raw)


def approved_plan(path,expected):
    raw=private_bytes(path)
    if not re.fullmatch('[a-f0-9]{64}',expected) or sha(raw)!=expected:raise ValueError('approved_plan_hash_mismatch')
    return json.loads(raw,object_pairs_hook=unique)


def env_values(path):
    result={}
    for line in private_bytes(path,65536).decode().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):continue
        key,separator,raw=line.partition('=')
        if not separator or not re.fullmatch('[A-Z][A-Z0-9_]*',key):raise ValueError('invalid_private_environment')
        items=shlex.split(raw,comments=False)
        if len(items)>1:raise ValueError('invalid_private_environment')
        result[key]=items[0] if items else ''
    return result


def image_definition(model,evidence_hash):
    if model not in MODELS or not re.fullmatch('[a-f0-9]{64}',evidence_hash):raise ValueError('unapproved_model')
    return dict(id=model,version='20261027-internal-image-v1',name=model+' · 内部体验',capability='image.generate',
        feature='image',products=['easel'],test_only=False,provider_id='newapi_images',provider_version=PROVIDER_VERSION,
        adapter='openai_images_v1',protocol_version='1',verification='live',verification_ref='server-image-evidence:'+evidence_hash,
        rate={'version':'internal-one-point-per-image-20261027-v1','meters':[{'unit':'image','basis':'verified_delivery_v1','numerator':'1000000','denominator':'1'}]},
        limits={'max_input_bytes':65536,'max_assets':0,'max_artifact_bytes':20971520,'max_units':{'image':1}},
        parameters={'type':'object','properties':{'n':{'type':'integer','minimum':1,'maximum':1},
            'size':{'type':'string','enum':['1024x1024']}},'required':['n','size'],'additionalProperties':False})


def safe_plan_receipt(plan,digest,path):
    return {'plan_path':str(path),'plan_sha256':digest,'planned_actions':ACTIONS,
        'financial_snapshot':plan['financial_snapshot'],'model_definition_count':len(plan['definitions']),
        'task_states_before':plan.get('task_states_before',[]),
        'preparation_script_sha256':plan.get('preparation_script_sha256'),
        'original_grant_count':1,'source_scope_match':True,'planned_money_delta':'0','applied':False}


def approve_preconditions(expected,actual,active):
    if expected!=actual:raise ValueError('financial_snapshot_changed')
    if any(active.values()):raise ValueError('original_units_must_be_stopped_by_operator')


def units_active():
    return {key:subprocess.run(['systemctl','is-active','--quiet',unit],capture_output=True).returncode==0
            for key,unit in UNITS.items()}


def verify_staged():
    if sha(private_bytes(WHEEL,8*1024*1024))!=WHEEL_SHA:raise ValueError('candidate_hash_mismatch')
    import platform_service
    import PIL
    if not Path(platform_service.__file__).resolve().is_relative_to(TARGET) or PIL.__version__!='12.3.0':
        raise ValueError('run_with_staged_python')
    package=Path(platform_service.__file__).parent.parent
    with zipfile.ZipFile(WHEEL) as archive:
        if archive.testzip() is not None or len(archive.namelist())!=83:raise ValueError('candidate_archive_mismatch')
        for name in archive.namelist():
            if name.endswith(('.py','.sql')) and (package/name).read_bytes()!=archive.read(name):raise ValueError('installed_source_mismatch')


def walk_strings(value,path=()):
    if isinstance(value,dict):
        for key,item in value.items():yield from walk_strings(item,path+(key,))
    elif isinstance(value,str):yield path,value


def choose_owner(env,locator=None):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict
    from psycopg.rows import dict_row
    runtime=conninfo_to_dict(env['CENTRAL_DATABASE_URL']);schema=env['CENTRAL_DB_SCHEMA']
    candidates=[]
    sources=[Path(locator['path'])] if locator else OWNER_FILES
    for source in sources:
        value=read_private(source)
        entries=list(walk_strings(value))
        if locator:entries=[(keys,item) for keys,item in entries if list(keys)==locator['keys']]
        for keys,item in entries:
            if not item.startswith(('postgresql://','postgres://')):continue
            try:
                parsed=conninfo_to_dict(item)
                if any(parsed.get(k,'5432' if k=='port' else '')!=runtime.get(k,'5432' if k=='port' else '') for k in ('host','port','dbname')):continue
                with psycopg.connect(item,row_factory=dict_row) as c:
                    c.execute('SET TRANSACTION READ ONLY')
                    c.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(schema)))
                    row=c.execute("SELECT current_user=session_user AND n.nspowner=r.oid owner FROM pg_namespace n JOIN pg_roles r ON r.rolname=current_user WHERE n.nspname=current_schema()").fetchone()
                    if row and row['owner']:candidates.append((item,{'path':str(source),'keys':list(keys),'file_sha256':sha(private_bytes(source))}))
            except Exception:continue
    if not candidates:raise ValueError('owner_configuration_not_verified')
    return candidates[0]


def connect_owner(dsn,schema,*,readonly=True):
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
    c=psycopg.connect(dsn,row_factory=dict_row)
    c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ'+(', READ ONLY' if readonly else ''))
    c.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(schema)))
    return c


def financial_snapshot(c):
    from psycopg import sql
    tables=[row['tablename'] for row in c.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema() ORDER BY tablename").fetchall()
            if re.match(r'^(credit_|billing_|payment_|execution_)',row['tablename']) or row['tablename'] in {'plans','memberships','membership_revocations','internal_grants'}]
    result={}
    for table in tables:
        digest=hashlib.sha256();count=0
        for row in c.execute(sql.SQL('SELECT to_jsonb(t)::text body FROM {} t ORDER BY 1').format(sql.Identifier(table))):
            digest.update(row['body'].encode()+b'\n');count+=1
            if count>100000:raise ValueError('snapshot_limit_requires_review')
        result[table]={'rows':count,'sha256':digest.hexdigest()}
    return result


def original_grant(c):
    rows=c.execute("""SELECT g.* FROM internal_grants g
        JOIN tenants t ON t.id=g.tenant_id AND t.owner_user_id=g.user_id
        JOIN users u ON u.id=g.user_id AND u.active
        JOIN tenant_members tm ON tm.user_id=g.user_id AND tm.tenant_id=g.tenant_id AND tm.active
        JOIN memberships m ON m.id=g.membership_id AND m.billing_account_id=g.billing_account_id
        JOIN credit_lots l ON l.id=g.lot_id AND l.membership_id=m.id AND l.billing_account_id=g.billing_account_id
        WHERE g.starts_at<=clock_timestamp() AND g.expires_at>clock_timestamp()
          AND m.plan_id='easel-internal-experience-v1' AND NOT l.test_only AND l.issued=10000000
          AND (m.starts_at,m.expires_at,l.expires_at)=(g.starts_at,g.expires_at,g.expires_at)
          AND NOT EXISTS(SELECT 1 FROM membership_revocations r WHERE r.membership_id=m.id)""").fetchall()
    if len(rows)!=1:raise ValueError('exactly_one_original_owned_grant_required')
    return json.loads(json.dumps(rows[0],default=str))


def evidence_definitions():
    from PIL import Image
    definitions=[]
    for model,image_sha in MODELS.items():
        short=model.rsplit('-',1)[1]
        body_path=EVIDENCE/(short+'-protocol-01-response-private.json')
        header_path=EVIDENCE/(short+'-protocol-01-headers-private.json')
        raw=private_bytes(body_path);body=json.loads(raw,object_pairs_hook=unique);headers=read_private(header_path)
        normalized={key.lower():value for key,value in headers.items()}
        reference=normalized.get('x-oneapi-request-id')
        if not isinstance(reference,str) or not re.fullmatch('[0-9]{23}[A-Za-z0-9]{16}',reference):raise ValueError('gateway_reference_unverified')
        if set(body)!={'created','data'} or type(body['created']) is not int or body['created']<=0 or len(body['data'])!=1 or set(body['data'][0])!={'url'}:raise ValueError('body_profile_unverified')
        parsed=urlsplit(body['data'][0]['url'])
        if (parsed.scheme,parsed.hostname,parsed.port or 443)!=('https','api.image2pro.top',443):raise ValueError('download_origin_unverified')
        found=False
        for path in EVIDENCE.glob('*.png'):
            image=private_bytes(path,20971520)
            if sha(image)!=image_sha:continue
            with Image.open(io.BytesIO(image)) as decoded:decoded.verify()
            with Image.open(io.BytesIO(image)) as decoded:
                decoded.load()
                if decoded.format!='PNG' or decoded.size!=(1024,1024) or getattr(decoded,'n_frames',1)!=1:raise ValueError('decoded_image_mismatch')
            found=True;break
        if not found:raise ValueError('verified_image_file_required')
        evidence_hash=sha(json.dumps({'model':model,'body_sha256':sha(raw),'image_sha256':image_sha,'namespace':'newapi_x_oneapi_request_id_v1','reference':reference},sort_keys=True).encode())
        definitions.append(image_definition(model,evidence_hash))
    return definitions


def configurations():
    env=env_values(CENTRAL_ENV)
    if Path(env['CENTRAL_EXECUTION_CONFIG_FILE'])!=EXECUTION:raise ValueError('execution_source_mismatch')
    source=json.loads(private_bytes(EXECUTION,65536,owners={0,pwd.getpwnam('easel-central').pw_uid}),object_pairs_hook=unique)
    existing=source['providers'][PROVIDER_KEY]
    if existing['endpoint']!='https://api.drawbookai.cn:19443/v1/responses':raise ValueError('verified_gateway_endpoint_changed')
    media={'mode':'live','storage_root':str(STORAGE),'concurrency':1,'timeout_seconds':180,'providers':{'newapi_images':{
        'version':PROVIDER_VERSION,'base_url':'https://api.drawbookai.cn:19443/v1','secret':existing['secret'],
        'models':{model:model for model in MODELS},'adapter':'openai_images_v1','protocol_version':'1',
        'capabilities':['image.generate'],'download_origins':['https://api.image2pro.top'],'response_profile':'newapi_images_v1'}}}
    from platform_service.media_config import MediaSettings,MediaProvider
    MediaSettings(**(media|{'providers':{key:MediaProvider(**item) for key,item in media['providers'].items()}}))
    return env,media


def current_hashes():
    paths=[CENTRAL_ENV,CONSOLE_ENV,EXECUTION]+[Path('/etc/systemd/system')/unit for unit in UNITS.values()]+[Path('/etc/systemd/system/easel-encrypted-backup.service'),Path('/etc/systemd/system/easel-encrypted-backup.timer')]
    return {str(path):sha(path.read_bytes()) for path in paths}


def make_plan(backup_path,backup_sha):
    verify_staged();env,_=configurations();dsn,locator=choose_owner(env)
    if sha(private_bytes(backup_path))!=backup_sha:raise ValueError('root_reviewed_backup_receipt_changed')
    with connect_owner(dsn,env['CENTRAL_DB_SCHEMA']) as c:
        snapshot=financial_snapshot(c);grant=original_grant(c)
        migrations=c.execute('SELECT version,checksum FROM schema_migrations ORDER BY version').fetchall()
        tasks=c.execute("SELECT status,count(*) n FROM execution_jobs WHERE status IN ('reserved','dispatched','pending_reconciliation') GROUP BY status ORDER BY status").fetchall()
    from platform_service.db import MIGRATIONS_DIR
    expected=[{'version':p.name,'checksum':sha(p.read_bytes())} for p in sorted(MIGRATIONS_DIR.glob('*.sql')) if p.name<'014']
    if migrations!=expected:raise ValueError('exact_r2_migration_baseline_required')
    plan={'version':1,'phase':'review-required','source_release':str(SOURCE),'target_release':str(TARGET),'candidate_sha256':WHEEL_SHA,
        'preparation_script_sha256':sha(Path(__file__).read_bytes()),
        'owner_locator':locator,'source_hashes':current_hashes(),'financial_snapshot':snapshot,'original_grant':grant,
        'migrations_before':migrations,'task_states_before':tasks,'definitions':evidence_definitions(),'planned_actions':ACTIONS,'planned_money_delta':'0',
        'pre_migration_backup':{'path':str(backup_path),'sha256':backup_sha,'basis':'root_reviewed_existing_R2_v6_backup_restore_receipt'},
        'recovery_boundary':{'old_cli_migrate_rejects_extra_migrations':True,'old_runtime_restart_not_verified':True,
            'rollback_is_not_just_unit_switch':True,'forward_repair_or_explicit_verified_backup_restore_required':True},
        'post_migration_readiness':{'loopback_candidate_port':19876,'original_unit_port':19874,'models_disabled':True,'provider_posts':0}}
    path=PRIVATE/('plan-'+uuid4().hex+'-private.json');digest=save_private(path,plan)
    return safe_plan_receipt(plan,digest,path)


async def apply_disabled(plan,digest):
    verify_staged();env,media=configurations()
    if plan.get('preparation_script_sha256')!=sha(Path(__file__).read_bytes()):raise ValueError('reviewed_preparation_script_changed')
    if plan.get('candidate_sha256')!=WHEEL_SHA or plan.get('source_hashes')!=current_hashes() or plan.get('planned_actions')!=ACTIONS:raise ValueError('reviewed_source_changed')
    dsn,locator=choose_owner(env,plan['owner_locator'])
    if locator!=plan['owner_locator']:raise ValueError('owner_configuration_changed')
    if (sha(private_bytes(Path(plan['pre_migration_backup']['path'])))!=plan['pre_migration_backup']['sha256']
            or plan['definitions']!=evidence_definitions()):raise ValueError('reviewed_evidence_or_backup_changed')
    if MEDIA.exists() or MEDIA.is_symlink() or STORAGE.resolve()!=STORAGE:raise ValueError('media_or_storage_requires_review')
    account=pwd.getpwnam('easel-central')
    if STORAGE.exists() and (STORAGE.stat().st_uid!=account.pw_uid or stat.S_IMODE(STORAGE.stat().st_mode)!=0o700):raise ValueError('storage_owner_requires_review')
    with connect_owner(dsn,env['CENTRAL_DB_SCHEMA']) as c:
        actual=financial_snapshot(c)
        approve_preconditions(plan['financial_snapshot'],actual,units_active())
        if original_grant(c)!=plan['original_grant']:raise ValueError('original_grant_changed')
        if c.execute("SELECT count(*) n FROM execution_jobs WHERE status IN ('reserved','dispatched')").fetchone()['n']:
            raise ValueError('active_original_task_requires_review')
        if c.execute('SELECT version,checksum FROM schema_migrations ORDER BY version').fetchall()!=plan['migrations_before']:
            raise ValueError('migration_baseline_changed')
    from platform_service.db import Database
    from platform_service.permissions import grant_runtime_permissions
    from platform_service.contracts import OperatorIdentity
    from platform_service.internal_features import InternalFeatureService
    from platform_service.media.core import MediaService
    from platform_service.identity import IdentityService
    from psycopg.conninfo import conninfo_to_dict
    database=Database(dsn,schema=env['CENTRAL_DB_SCHEMA']);await database.open()
    try:
        applied=await database.migrate()
        if applied!=['014_media_execution.sql','015_media_storage.sql','016_m1_image_enablement.sql']:raise ValueError('unexpected_migration_transition')
        async with database.connection() as c:await grant_runtime_permissions(c,conninfo_to_dict(env['CENTRAL_DATABASE_URL'])['user'])
        grant=plan['original_grant'];actor=OperatorIdentity('m1-image-release-20261027',frozenset({'internal:extend_features','media:publish'}))
        await InternalFeatureService(database).extend(actor,environment=grant['environment_id'],grant_id=grant['id'],product='easel',feature='image',key='approved-image-original-grant-20261027',reason='用户授权原体验账号增加图片功能，不增点、不延长；每张已验证交付图片1点')
        service=MediaService(database,IdentityService(database,None))
        for definition in plan['definitions']:await service.publish(definition,actor)
    finally:await database.close()
    if not STORAGE.parent.exists():
        STORAGE.parent.mkdir(mode=0o750);os.chown(STORAGE.parent,account.pw_uid,account.pw_gid)
    if not STORAGE.exists():STORAGE.mkdir(mode=0o700)
    if STORAGE.is_symlink():raise ValueError('unsafe_storage_path')
    os.chown(STORAGE,account.pw_uid,account.pw_gid);STORAGE.chmod(0o700)
    if MEDIA.exists():raise ValueError('media_configuration_already_exists_requires_review')
    save_private(MEDIA,media);os.chown(MEDIA,account.pw_uid,account.pw_gid)
    with connect_owner(dsn,env['CENTRAL_DB_SCHEMA']) as c:
        after=financial_snapshot(c)
        if after!=plan['financial_snapshot']:raise ValueError('original_financial_rows_changed')
        rows=c.execute('SELECT enabled FROM media_model_states ORDER BY model_id').fetchall()
        if len(rows)!=2 or any(row['enabled'] for row in rows):raise ValueError('models_must_remain_disabled')
    receipt={'success':True,'phase':'applied-disabled','plan_sha256':digest,'candidate_sha256':WHEEL_SHA,
        'migrations':applied,'original_financial_snapshot':after,'models_enabled':False,'original_units_untouched':True}
    path=PRIVATE/('applied-'+uuid4().hex+'-private.json');receipt_sha=save_private(path,receipt)
    return {'receipt_path':str(path),'receipt_sha256':receipt_sha,'success':True,'phase':'applied-disabled','original_financial_rows_unchanged':True,'models_enabled':False}


async def enable(plan,digest,proof):
    verify_staged()
    if plan.get('preparation_script_sha256')!=sha(Path(__file__).read_bytes()):raise ValueError('reviewed_preparation_script_changed')
    if (proof.get('success') is not True or proof.get('candidate_sha256')!=WHEEL_SHA
            or proof.get('source_release')!=str(TARGET) or proof.get('media_blobs_covered') is not True
            or proof.get('schema_016_restore_verified') is not True):raise ValueError('post_migration_restore_proof_required')
    for unit in UNITS.values():
        directory=subprocess.check_output(['systemctl','show',unit,'-p','WorkingDirectory','--value'],text=True).strip()
        if directory!=str(TARGET):raise ValueError('reviewed_release_cutover_required')
    env,_=configurations();dsn,locator=choose_owner(env,plan['owner_locator'])
    if locator!=plan['owner_locator']:raise ValueError('owner_configuration_changed')
    from platform_service.db import Database
    from platform_service.contracts import OperatorIdentity
    from platform_service.media.core import MediaService
    from platform_service.identity import IdentityService
    db=Database(dsn,schema=env['CENTRAL_DB_SCHEMA']);await db.open()
    try:
        service=MediaService(db,IdentityService(db,None));actor=OperatorIdentity('m1-image-release-20261027',frozenset({'media:publish'}))
        for definition in plan['definitions']:
            await service.publish(definition,actor)
            await service.set_enabled(definition['id'],True,actor)
    finally:await db.close()
    return {'success':True,'phase':'enabled','plan_sha256':digest,'enabled_model_count':2,'provider_requests':0,'credits_issued':'0'}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['stage','plan','apply-disabled','enable'])
    parser.add_argument('--plan',type=Path);parser.add_argument('--expected-plan-sha256');parser.add_argument('--restore-proof',type=Path)
    parser.add_argument('--pre-backup-receipt',type=Path);parser.add_argument('--expected-backup-sha256')
    args=parser.parse_args()
    if os.getuid()!=0:raise ValueError('root_host_operator_required')
    if args.phase=='stage':verify_staged();result={'success':True,'phase':'staged-verified','candidate_sha256':WHEEL_SHA,'mutations':0}
    elif args.phase=='plan':
        if not args.pre_backup_receipt or not args.expected_backup_sha256:raise ValueError('explicit_R2_backup_receipt_required')
        result=make_plan(args.pre_backup_receipt,args.expected_backup_sha256)
    else:
        if not args.plan or not args.expected_plan_sha256:raise ValueError('explicit_approved_plan_required')
        plan=approved_plan(args.plan,args.expected_plan_sha256)
        if args.phase=='apply-disabled':result=asyncio.run(apply_disabled(plan,args.expected_plan_sha256))
        else:
            if not args.restore_proof:raise ValueError('restore_proof_required')
            result=asyncio.run(enable(plan,args.expected_plan_sha256,read_private(args.restore_proof)))
    print(json.dumps(result,sort_keys=True))


if __name__=='__main__':
    try:main()
    except Exception as error:
        # A DB/HTTP exception may embed a private connection. Never serialize it.
        log=PRIVATE/('preparation-error-'+uuid4().hex+'-private.log')
        try:
            fd=os.open(log,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'w') as stream:stream.write(traceback.format_exc())
            private_log=str(log)
        except Exception:private_log=None
        code=str(error) if isinstance(error,ValueError) and re.fullmatch('[a-z_]{1,100}',str(error)) else 'preparation_or_application_failed'
        print(json.dumps({'success':False,'error':code,'private_log_path':private_log}))
        raise SystemExit(1)
