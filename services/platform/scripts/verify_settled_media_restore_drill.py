#!/usr/bin/env python3
"""Independent settled-image recovery drill; reviewed execution is server only."""
import hashlib
import argparse
import io
import json
import os
from pathlib import Path
import re
import shlex
import signal
import stat
import resource
import sys
import tarfile
import types
import uuid
import zipfile

MAXIMUM=32*1024*1024
MAX_BLOB=64*1024*1024
STORAGE=Path('/var/lib/easel-central/media')
MEDIA=Path('/etc/easel-central/media.json')
MODELS=('gpt-image-2.5-flare','gpt-image-2.5-sunburst')
RELEASE='/srv/easel/releases/20261027-images-m1-1-size-v2'
SOURCE_RELEASE='/srv/easel/releases/20261026-reconciliation-r2-v1'
WHEEL=Path('/root/easel-image-release-20261027/size-v2/easel_central_platform-0.1.0-py3-none-any.whl')
WHEEL_SHA='f50e52511e941d3bea8e724eddbd68330e20eaa6339c5e2437b3880ca9705abf'
MIGRATION_016_SHA='a5a1dcfdb90299ebd063b18377a900ad68e1338bb2137cba9a7010f9694d6cba'
CORE=Path('/root/easel-restore-drill-review-20261027/verify_encrypted_restore_drill_v6.py')
CORE_SHA='4d3c4210469d6630dd4cbc71d8d5327e62aa972eedb7084685bb0df85a2c88ea'
UNITS={'api':'easel-central-production.service','console':'easel-console-production.service'}
MAIN_UNITS={kind:Path('/etc/systemd/system')/unit for kind,unit in UNITS.items()}
DROPINS={kind:Path(str(path)+'.d')/'90-m1-images.conf' for kind,path in MAIN_UNITS.items()}
SOURCE_FILES=('/etc/easel-central/central.env','/etc/easel-central/console.env','/etc/easel-central/execution.json',
    '/root/easel-migration-20261024/central-connections-private.json','/root/easel-migration-20261024/identity-connections-private.json',
    '/root/easel-migration-20261024/logto-runtime.env',str(MEDIA),*(str(path) for path in MAIN_UNITS.values()),*(str(path) for path in DROPINS.values()))

class Rejected(Exception):
    pass

def require(value):
    if not value:raise Rejected('media_restore_rejected')

def digest(raw):return hashlib.sha256(raw).hexdigest()

def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()

def unique(pairs):
    result={}
    for key,value in pairs:
        require(key not in result);result[key]=value
    return result

def media_configuration(raw):
    require(isinstance(raw,bytes) and len(raw)<=65536)
    try:value=json.loads(raw,object_pairs_hook=unique)
    except (ValueError,UnicodeDecodeError):raise Rejected('media_restore_rejected') from None
    require(isinstance(value,dict) and value.get('storage_root')==str(STORAGE))
    return value

def media_environment(raw,allow_absent=False):
    require(isinstance(raw,bytes) and len(raw)<=65536)
    values=[]
    try:
        for line in raw.decode().splitlines():
            key,separator,value=line.strip().partition('=')
            if separator and key.strip()=='CENTRAL_MEDIA_CONFIG_FILE':
                parts=shlex.split(value,comments=False)
                require(len(parts)==1);values.extend(parts)
    except (ValueError,UnicodeDecodeError):raise Rejected('media_restore_rejected') from None
    require(values==[str(MEDIA)] or (allow_absent and not values))

def verify_release_content(wheel,read_installed):
    require(isinstance(wheel,bytes) and digest(wheel)==WHEEL_SHA)
    migrations=[]
    with zipfile.ZipFile(io.BytesIO(wheel)) as archive:
        names=archive.namelist();require(len(names)==len(set(names))==83 and archive.testzip() is None)
        code=[name for name in names if name.endswith(('.py','.sql'))];require(len(code)==76)
        for name in code:
            require(name.startswith('platform_service/') and not any(part in ('','.','..') for part in name.split('/')))
            raw=archive.read(name);require(read_installed(name)==raw)
            if name.startswith('platform_service/migrations/') and name.endswith('.sql'):
                migrations.append({'version':name.rsplit('/',1)[1],'checksum':digest(raw)})
    migrations.sort(key=lambda row:row['version'])
    require(len(migrations)==16 and migrations[-1]=={'version':'016_m1_image_enablement.sql','checksum':MIGRATION_016_SHA})
    return migrations

def match_migrations(expected,actual):require(canonical(expected)==canonical(actual))

def source_argv(raw,kind):
    require(kind in UNITS and isinstance(raw,bytes))
    values={};section=None
    for line in raw.decode().splitlines():
        line=line.strip()
        if not line or line.startswith(('#',';')):continue
        if line.startswith('['):section=line;continue
        if section!='[Service]':continue
        require(not line.endswith('\\'))
        key,separator,value=line.partition('=');require(separator)
        values.setdefault(key,[]).append(value)
    user='easel-central' if kind=='api' else 'easel-console'
    environment='/etc/easel-central/central.env' if kind=='api' else '/etc/easel-central/console.env'
    for key,value in [('User',user),('Group',user),('WorkingDirectory',SOURCE_RELEASE),('EnvironmentFile',environment)]:
        require(values.get(key)==[value])
    require(len(values.get('ExecStart',[]))==1)
    argv=shlex.split(values['ExecStart'][0])
    require(argv and argv[0]==SOURCE_RELEASE+'/.venv/bin/python' and all(re.fullmatch(r'[A-Za-z0-9_./:@=\-\[\]]+',item) for item in argv))
    for flag,wanted in [('--host','127.0.0.1'),('--port','19874' if kind=='api' else '19875')]:
        matches=[]
        for index,item in enumerate(argv):
            if item==flag:require(index+1<len(argv));matches.append(argv[index+1])
            elif item.startswith(flag+'='):matches.append(item[len(flag)+1:])
        require(matches==[wanted])
    return argv

def expected_dropin(main,kind):
    argv=source_argv(main,kind);argv[0]=RELEASE+'/.venv/bin/python'
    raw='[Service]\nWorkingDirectory='+RELEASE+'\nExecStart=\nExecStart='+' '.join(argv)+'\n'
    if kind=='api':raw+='Environment=CENTRAL_MEDIA_CONFIG_FILE='+str(MEDIA)+'\n'
    return raw.encode()

def verify_dropins(dropins,mains):
    require(set(dropins)==set(mains)==set(UNITS))
    for kind in UNITS:require(dropins[kind]==expected_dropin(mains[kind],kind))

def verify_process_record(record,main,kind):
    argv=source_argv(main,kind);argv[0]=RELEASE+'/.venv/bin/python'
    require(type(record['pid']) is int and record['pid']>1 and record['active'] is True
            and record['cwd']==RELEASE and record['argv']==argv and record['exe']==record['venv_exe']
            and record['media_path']==(str(MEDIA) if kind=='api' else None))
    return {'pid':record['pid'],'command_sha256':digest(canonical(argv)),'cwd_match':True,
            'executable_match':True,'media_environment_match':True}

def proc_bytes(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        raw=bytearray()
        while True:
            block=os.read(fd,min(65536,65537-len(raw)))
            if not block:break
            raw.extend(block);require(len(raw)<=65536)
        return bytes(raw)
    finally:os.close(fd)

def active_release_processes(backup,configs):
    result={}
    for kind,unit in UNITS.items():
        raw=backup.command(['/usr/bin/systemctl','show','--property=MainPID,ActiveState,WorkingDirectory,DropInPaths',unit],maximum=8192).decode()
        values=dict(line.split('=',1) for line in raw.splitlines())
        require(values['ActiveState']=='active' and values['WorkingDirectory']==RELEASE and values['DropInPaths'].split()==[str(DROPINS[kind])])
        pid=int(values['MainPID']);require(pid>1)
        path=Path('/proc')/str(pid)
        args=proc_bytes(path/'cmdline').split(b'\x00');require(args[-1:]==[b''])
        environment=proc_bytes(path/'environ').split(b'\x00')
        media=[value.split(b'=',1)[1].decode() for value in environment if value.startswith(b'CENTRAL_MEDIA_CONFIG_FILE=')]
        require(len(media)<=1)
        record={'pid':pid,'active':True,'cwd':os.readlink(path/'cwd'),'argv':[value.decode() for value in args[:-1]],
            'exe':str((path/'exe').resolve(strict=True)),'venv_exe':str(Path(RELEASE+'/.venv/bin/python').resolve(strict=True)),
            'media_path':media[0] if media else None}
        result[kind]=verify_process_record(record,configs['configs'+str(MAIN_UNITS[kind])],kind)
    return result

def blob_index(rows):
    require(type(rows) is list and len(rows)<=4096)
    result={}
    for row in rows:
        require(type(row) is dict and set(row)=={'storage_key','sha256','size','state'})
        key=row['storage_key'];sha=row['sha256'];size=row['size']
        require(isinstance(key,str) and re.fullmatch(r'(?:[a-f0-9]{32}|[a-f0-9]{64})',key) is not None
                and key not in result and isinstance(sha,str) and re.fullmatch('[a-f0-9]{64}',sha) is not None
                and type(size) is int and 1<=size<=MAX_BLOB and row['state'] in {'attached','deleted'})
        result[key]=row
    return result

def signature(info):
    return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns,info.st_uid,info.st_gid,info.st_mode,info.st_nlink)

def checked_dir(info,uid,gid):
    require(stat.S_ISDIR(info.st_mode) and (info.st_uid,info.st_gid,stat.S_IMODE(info.st_mode))==(uid,gid,0o700))

def private_metadata(path,uid,gid):
    info=Path(path).lstat()
    require(stat.S_ISREG(info.st_mode) and (info.st_uid,info.st_gid,stat.S_IMODE(info.st_mode),info.st_nlink)==(uid,gid,0o600,1))

def capture_blobs(root,rows,uid,gid):
    index=blob_index(rows);expected={key for key,row in index.items() if row['state']=='attached'}
    fd=None
    try:
        before_path=Path(root).lstat();checked_dir(before_path,uid,gid)
        fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        before=os.fstat(fd);require(signature(before_path)==signature(before))
        require(set(os.listdir(fd))==expected)
        entries={};files=[];total=0
        for key in sorted(expected):
            item=os.open(key,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
            try:
                meta=os.fstat(item);row=index[key]
                require(stat.S_ISREG(meta.st_mode) and meta.st_nlink==1
                        and (meta.st_uid,meta.st_gid,stat.S_IMODE(meta.st_mode))==(uid,gid,0o600)
                        and meta.st_size==row['size'] and total+meta.st_size<=MAXIMUM)
                raw=bytearray()
                while True:
                    block=os.read(item,min(65536,row['size']+1-len(raw)))
                    if not block:break
                    raw.extend(block);require(len(raw)<=row['size'])
                require(signature(meta)==signature(os.fstat(item)) and len(raw)==row['size'] and digest(raw)==row['sha256'])
                require(signature(meta)==signature(os.stat(key,dir_fd=fd,follow_symlinks=False)))
                entries['media/blobs/'+key]=bytes(raw);total+=len(raw)
                files.append({'storage_key':key,'size':row['size'],'sha256':row['sha256'],'uid':uid,'gid':gid,'mode':0o600})
            finally:os.close(item)
        require(set(os.listdir(fd))==expected and signature(before)==signature(os.fstat(fd))
                and signature(before)==signature(Path(root).lstat()))
        return entries,{'version':1,'uid':uid,'gid':gid,'mode':0o700,'files':files,'blob_bytes':total,
                        'deleted_count':sum(row['state']=='deleted' for row in rows),'registry_sha256':digest(canonical(rows))}
    except OSError:raise Rejected('media_restore_rejected') from None
    finally:
        if fd is not None:os.close(fd)

def write_owned(path,raw,uid,gid,mode=0o600):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        view=memoryview(raw)
        while view:
            count=os.write(fd,view);require(count>0);view=view[count:]
        if (os.fstat(fd).st_uid,os.fstat(fd).st_gid)!=(uid,gid):os.fchown(fd,uid,gid)
        os.fchmod(fd,mode);os.fsync(fd)
    finally:os.close(fd)

def restore_source_configurations(destination,entries,metadata,expected_names=None):
    expected_names=expected_names if expected_names is not None else {'configs'+path for path in SOURCE_FILES}
    require(type(metadata) is list and len(metadata)==len(expected_names) and set(entries)==expected_names)
    index={row['name']:row for row in metadata};require(len(index)==len(metadata) and set(index)==expected_names)
    for name,row in index.items():
        require(isinstance(name,str) and name.startswith('configs/') and not any(part in ('','.','..') for part in name.split('/'))
                and set(row)=={'name','uid','gid','mode','size','sha256'} and all(type(row[key]) is int and row[key]>=0 for key in ('uid','gid','size'))
                and row['mode'] in {0o600,0o644} and isinstance(entries[name],bytes)
                and len(entries[name])==row['size'] and digest(entries[name])==row['sha256'])
    require(sum(map(len,entries.values()))<=MAXIMUM)
    destination=Path(destination);destination.mkdir(mode=0o700)
    for name,row in index.items():
        parent=destination
        for part in name.split('/')[:-1]:
            parent=parent/part
            try:parent.mkdir(mode=0o700)
            except FileExistsError:pass
            info=parent.lstat();require(stat.S_ISDIR(info.st_mode) and info.st_uid==os.geteuid() and stat.S_IMODE(info.st_mode)==0o700)
        path=destination/name;write_owned(path,entries[name],row['uid'],row['gid'],row['mode'])
        info=path.lstat()
        require((info.st_uid,info.st_gid,stat.S_IMODE(info.st_mode),info.st_nlink)==(row['uid'],row['gid'],row['mode'],1)
                and path.read_bytes()==entries[name])
    return {'source_configuration_files_restored_verified':True,'source_configuration_count':len(index),
            'source_configuration_inventory_sha256':digest(canonical(metadata))}

def restore_private_media(destination,entries,inventory,media_config,uid,gid):
    require(type(inventory) is dict and inventory.get('version')==1
            and (inventory.get('uid'),inventory.get('gid'),inventory.get('mode'))==(uid,gid,0o700))
    files=inventory.get('files');require(type(files) is list)
    rows=[{'storage_key':row['storage_key'],'sha256':row['sha256'],'size':row['size'],'state':'attached'} for row in files]
    index=blob_index(rows)
    require(set(entries)=={'media/blobs/'+key for key in index})
    for row in files:
        require(set(row)=={'storage_key','sha256','size','uid','gid','mode'}
                and (row['uid'],row['gid'],row['mode'])==(uid,gid,0o600))
        value=entries['media/blobs/'+row['storage_key']]
        require(isinstance(value,bytes) and len(value)==row['size'] and digest(value)==row['sha256'])
    require(sum(map(len,entries.values()))==inventory['blob_bytes'] and len(media_config)<=65536)
    destination=Path(destination);destination.mkdir(mode=0o700)
    require(destination.lstat().st_uid==os.geteuid() and stat.S_IMODE(destination.lstat().st_mode)==0o700)
    configdir=destination/'configs';configdir.mkdir(mode=0o700)
    write_owned(configdir/'media.json',media_config,uid,gid)
    root=destination/'media';root.mkdir(mode=0o700)
    if (root.stat().st_uid,root.stat().st_gid)!=(uid,gid):os.chown(root,uid,gid)
    for key in index:write_owned(root/key,entries['media/blobs/'+key],uid,gid)
    actual,captured=capture_blobs(root,rows,uid,gid)
    require(actual==entries and captured['files']==files and captured['blob_bytes']==inventory['blob_bytes'])
    path=configdir/'media.json';info=path.lstat()
    require((info.st_uid,info.st_gid,stat.S_IMODE(info.st_mode),info.st_nlink)==(uid,gid,0o600,1)
            and path.read_bytes()==media_config)
    return {'media_blobs_covered':True,'blob_count':len(files),'blob_bytes':inventory['blob_bytes'],
            'blob_inventory_sha256':digest(canonical(inventory)),'media_config_sha256':digest(media_config),
            'media_config_restored_verified':True,'permissions_restored_verified':True,
            'nonempty_media_blobs_restore_verified':bool(files),
            'coverage_basis':'owned_registry_files_restored' if files else 'empty_registry_and_empty_owned_directory'}

def settled_window(state):
    expected={'invocations':2,'jobs':2,'artifacts':2,'assets':0,'blobs':2,'budgets':2,
        'settled_completed_jobs':2,'valid_invocations':2,'released_budgets':2,'valid_png_artifacts':2,
        'valid_attached_blobs':2,'typed_terminal_evidence':2,'typed_receipts':2,'http_references':2,
        'attempts':2,'http_associations':2,'owners':1,'active_jobs':0,'pinned_blobs':0,'gc_claims':0}
    require(type(state) is dict and set(state)==set(expected)|{'models','model_jobs'}
        and state['models']==[{'model_id':name,'enabled':False} for name in MODELS]
        and state['model_jobs']==[{'model':name,'jobs':1} for name in MODELS]
        and all(type(state[key]) is int and state[key]==value for key,value in expected.items()))

def match_settled_window(before,after):
    settled_window(before);settled_window(after);require(canonical(before)==canonical(after))

def enable_proof(databases,media,plan,plan_sha,migration_verified):
    require([row.get('kind') for row in databases]==['central','identity'] and all(row.get('matched') is True for row in databases))
    require(migration_verified is True and all(media.get(key) is True for key in
            ('media_blobs_covered','media_config_restored_verified','permissions_restored_verified','source_configuration_files_restored_verified')))
    require(plan['release']==RELEASE and plan['candidate_sha256']==WHEEL_SHA)
    require(media.get('blob_count')==2 and media.get('nonempty_media_blobs_restore_verified') is True
            and media.get('settled_jobs_restore_verified') is True and media.get('typed_receipts_restore_verified') is True)
    return {'success':True,'candidate_sha256':WHEEL_SHA,'source_release':RELEASE,'schema_016_restore_verified':True,
            'plan_sha256':plan_sha,'nonce':plan['nonce'],**media}

def cleanup_owned(core,backup,targets):
    result=[]
    for item in targets:
        try:
            core.guard_target(backup,item)
            row=json.loads(backup.command(['/usr/bin/docker','inspect',item['name']],maximum=1024*1024))[0]
            if row['State']['Running']:core.stop_target(backup,item)
            result.append({'kind':item['kind'],'not_running_verified':True})
        except BaseException:result.append({'kind':item['kind'],'not_running_verified':False})
    return result

def load_core():
    for parent in CORE.parents:
        info=parent.lstat();require(stat.S_ISDIR(info.st_mode) and info.st_uid==0 and not stat.S_IMODE(info.st_mode)&0o022)
    info=CORE.lstat()
    require(stat.S_ISREG(info.st_mode) and (info.st_uid,stat.S_IMODE(info.st_mode),info.st_nlink)==(0,0o600,1))
    raw=CORE.read_bytes();require(digest(raw)==CORE_SHA)
    core=types.ModuleType('reviewed_restore_v6');core.__file__=str(CORE)
    exec(compile(raw,str(CORE),'exec'),core.__dict__)
    core.RELEASE=RELEASE
    core.PHASES=core.PHASES|{'capture_media','restore_media_files','verify_media_window'}
    return core

def release_migrations(backup):
    purelib=backup.command([RELEASE+'/.venv/bin/python','-I','-B','-c',
        'import sysconfig; print(sysconfig.get_paths()["purelib"])'],maximum=4096,timeout=15).decode().strip()
    require(purelib==RELEASE+'/.venv/lib/python3.12/site-packages')
    return verify_release_content(backup.read_fixed(WHEEL,8*1024*1024),
        lambda name:backup.read_fixed(Path(purelib)/name,1024*1024,private=False))

def migrations_sql(core,schema):
    return "SELECT coalesce(json_agg(t ORDER BY version),'[]') FROM (SELECT version,checksum FROM "+core.ident(schema)+'.schema_migrations) t'

def media_rows_sql(core,schema):
    return "SELECT coalesce(json_agg(t ORDER BY storage_key),'[]') FROM (SELECT storage_key,sha256,size,state FROM "+core.ident(schema)+'.media_blobs) t'

def window_sql(core,schema):
    prefix=core.ident(schema)+'.'
    count=lambda table,where='true':'(SELECT count(*) FROM '+prefix+table+' WHERE '+where+')'
    checks={key:count(table) for key,table in (('invocations','media_invocations'),('jobs','media_jobs'),('artifacts','media_artifacts'),
        ('assets','media_assets'),('blobs','media_blobs'),('budgets','media_storage_budgets'),('http_references','media_http_response_references'),('attempts','media_provider_attempts'))}
    checks.update({
        'settled_completed_jobs':count('media_jobs',"status='settled' AND execution_state='completed' AND delivery_state='available' AND amount=1000000 AND actual=1000000 AND funding_mode='live' AND capability='image.generate' AND model_definition->'test_only'='false'::jsonb AND model_definition->'limits'->'max_assets'='0'::jsonb AND request->'assets'='[]'::jsonb AND request->'parameters'='{\"n\":1,\"size\":\"1024x1024\"}'::jsonb"),
        'valid_invocations':count('media_invocations',"operation_count=1 AND held=0 AND spent=1000000 AND funding_mode='live'"),
        'released_budgets':count('media_storage_budgets','released_at IS NOT NULL AND remaining_bytes=0 AND remaining_files=0'),
        'valid_png_artifacts':count('media_artifacts',"mime='image/png' AND properties='{\"width\":1024,\"height\":1024,\"format\":\"PNG\"}'::jsonb AND purged_at IS NULL AND expires_at>clock_timestamp()+interval '30 minutes'"),
        'valid_attached_blobs':count('media_blobs',"kind='artifact' AND state='attached' AND attached_id IS NOT NULL AND mime='image/png' AND properties='{\"width\":1024,\"height\":1024,\"format\":\"PNG\"}'::jsonb AND purged_at IS NULL AND deleted_at IS NULL AND gc_token IS NULL AND gc_lease_until IS NULL AND expires_at>clock_timestamp()+interval '30 minutes'"),
        'typed_terminal_evidence':count('media_terminal_evidence',"terminal_status='completed' AND provider_response_id IS NULL AND reference_kind='http_request_id' AND http_reference_namespace='newapi_x_oneapi_request_id_v1' AND quantities='{\"image\":1}'::jsonb"),
        'typed_receipts':count('media_receipts',"terminal_status='completed' AND provider_response_id IS NULL AND reference_kind='http_request_id' AND http_reference_namespace='newapi_x_oneapi_request_id_v1' AND actual=1000000 AND quantities='{\"image\":1}'::jsonb"),
        'http_associations':count('media_provider_associations',"kind='http' AND http_status=200 AND http_request_id IS NOT NULL AND provider_response_id IS NULL AND provider_job_id IS NULL"),
        'owners':'(SELECT count(*) FROM (SELECT DISTINCT user_id,tenant_id,billing_account_id FROM '+prefix+'media_jobs) t)',
        'active_jobs':count('media_jobs',"status IN ('reserved','dispatched','pending_reconciliation')"),
        'pinned_blobs':count('media_blobs b',prefix+'media_storage_pinned(b)'),
        'gc_claims':count('media_blobs','gc_token IS NOT NULL OR gc_lease_until IS NOT NULL')})
    models="(SELECT coalesce(json_agg(t ORDER BY model_id),'[]') FROM (SELECT model_id,enabled FROM "+prefix+'media_model_states) t)'
    jobs="(SELECT coalesce(json_agg(t ORDER BY model),'[]') FROM (SELECT model,count(*) jobs FROM "+prefix+'media_jobs GROUP BY model) t)'
    return 'SET LOCAL search_path TO '+core.ident(schema)+',pg_catalog; '+"SELECT json_build_object('models',"+models+",'model_jobs',"+jobs+','+','.join(core.literal(key)+','+value for key,value in checks.items())+')'

def central_read(core,backup,statement,snapshot=None):
    _,name,user,database=core.SOURCES[0]
    return core.psql(backup.command,name,user,database,statement,snapshot)

def source_parent_gate():
    for parent in STORAGE.parents:
        info=parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid in {0,997} and not stat.S_IMODE(info.st_mode)&0o022)

def configurations(core,backup):
    account=backup.pwd.getpwnam('easel-central');require(account.pw_uid==997 and account.pw_gid==997)
    require(tuple(backup.REQUIRED)==SOURCE_FILES[:6])
    entries={};metadata=[]
    for path in sorted(SOURCE_FILES):
        app_owned=path in {'/etc/easel-central/execution.json',str(MEDIA)};owner=account.pw_uid if app_owned else 0
        before=Path(path).lstat();mode=stat.S_IMODE(before.st_mode)
        require(stat.S_ISREG(before.st_mode) and before.st_uid==owner and before.st_gid in ({0,997} if app_owned else {0})
                and mode in ({0o600,0o644} if Path(path) in MAIN_UNITS.values() else {0o600}) and before.st_nlink==1)
        raw=backup.read_fixed(path,65536 if path in {str(MEDIA),'/etc/easel-central/central.env'} else MAXIMUM,
            owner=owner,private=Path(path) not in MAIN_UNITS.values())
        require(signature(before)==signature(Path(path).lstat()))
        name='configs'+path;entries[name]=raw
        metadata.append({'name':name,'uid':before.st_uid,'gid':before.st_gid,'mode':mode,'size':len(raw),'sha256':digest(raw)})
    env=entries['configs/etc/easel-central/central.env'];media_environment(env,allow_absent=True)
    verify_dropins({kind:entries['configs'+str(path)] for kind,path in DROPINS.items()},
                   {kind:entries['configs'+str(path)] for kind,path in MAIN_UNITS.items()})
    schema=core.configured_central_schema(env);media_configuration(entries['configs'+str(MEDIA)])
    private_metadata(MEDIA,account.pw_uid,account.pw_gid)
    source_parent_gate()
    return entries,schema,account.pw_uid,account.pw_gid,metadata

def build_plan(core,backup,nonce):
    plan=core.build_plan(backup,nonce)
    console=backup.command(['/usr/bin/systemctl','show','-p','WorkingDirectory','--value','easel-console-production'],maximum=4096).decode().strip()
    require(console==RELEASE)
    migrations=release_migrations(backup);configs,schema,uid,gid,config_metadata=configurations(core,backup)
    processes=active_release_processes(backup,configs)
    match_migrations(migrations,central_read(core,backup,migrations_sql(core,schema)))
    state=central_read(core,backup,window_sql(core,schema));settled_window(state)
    blobs=central_read(core,backup,media_rows_sql(core,schema));require(len(blobs)==2 and all(row['state']=='attached' for row in blobs))
    _,inventory=capture_blobs(STORAGE,blobs,uid,gid)
    plan.update({'format':'easel-settled-media-restore-plan-v1','script_sha256':digest(Path(__file__).read_bytes()),
        'core_sha256':CORE_SHA,'candidate_sha256':WHEEL_SHA,'migration_016_sha256':MIGRATION_016_SHA,
        'work_dir':'/root/easel-settled-media-restore-drill-20261027-'+nonce,'dest':'/var/backups/easel-encrypted-settled-media-drill',
        'config_hashes':{name:digest(raw) for name,raw in sorted(configs.items())},'expected_migrations':migrations,
        'config_metadata_sha256':digest(canonical(config_metadata)),
        'active_release_processes':processes,
        'media_uid':uid,'media_gid':gid,'media_root_inventory_sha256':digest(canonical(inventory)),
        'media_window_sha256':digest(canonical(state)),'media_registry_sha256':digest(canonical(blobs)),
        'initial_media_registry_empty':False,'expected_blob_count':2,'expected_settled_jobs':2,'gc_horizon_seconds':1800,
        'media_coverage':'Two settled owned PNG artifacts and exact registry; both image models disabled, all jobs terminal, held zero and storage budgets released. Reject active/pending/GC claims/partials/unknown files; original TTL preserved.',
        'consistency':'Independent per-database read-only exported snapshots. Settled media registry and owned files checked before/within/after central snapshot and at completion; image models disabled, no active work or GC claims, blobs remain unexpired for at least 30 minutes. No cross-database atomicity, ongoing media backup, or PITR.'})
    return plan

def capture_media_database(core,backup,source,schema,uid,gid,migrations):
    kind,name,user,database=source;backup.container_gate(name)
    roles=core.roles_dump(backup.command,name,user,database);started=core.utc()
    state=central_read(core,backup,window_sql(core,schema));settled_window(state)
    live_rows=central_read(core,backup,media_rows_sql(core,schema))
    with core.exported_snapshot(name,user,database) as snapshot:
        before=core.collect_inventory(backup.command,name,user,database,snapshot)
        match_migrations(migrations,central_read(core,backup,migrations_sql(core,schema),snapshot))
        match_settled_window(state,central_read(core,backup,window_sql(core,schema),snapshot))
        rows=central_read(core,backup,media_rows_sql(core,schema),snapshot)
        require(len(rows)==2 and all(row['state']=='attached' for row in rows) and canonical(rows)==canonical(live_rows))
        files,media_inventory=capture_blobs(STORAGE,rows,uid,gid)
        data=backup.command(['/usr/bin/docker','exec',name,'pg_dump','--format=custom','--quote-all-identifiers','--lock-wait-timeout=1s',
            '--no-password','--username='+user,'--dbname='+database,'--snapshot='+snapshot],maximum=MAXIMUM,timeout=120)
        require(data.startswith(b'PGDMP'))
        after=core.collect_inventory(backup.command,name,user,database,snapshot)
        core.stable_sequences(before['sequences'],after['sequences']);core.match_inventories(before,after)
        reread,metadata=capture_blobs(STORAGE,rows,uid,gid)
        require(reread==files and metadata==media_inventory)
    require(core.schema_digest(roles)==core.schema_digest(core.roles_dump(backup.command,name,user,database)))
    match_settled_window(state,central_read(core,backup,window_sql(core,schema)))
    require(canonical(rows)==canonical(central_read(core,backup,media_rows_sql(core,schema))))
    return data,roles,before,{'started_utc':started,'finished_utc':core.utc()},files,media_inventory

def apply(core,backup,plan,approved):
    core.reviewed(plan,approved)
    root=Path(plan['work_dir']);root.mkdir(mode=0o700)
    require(root.lstat().st_uid==0 and stat.S_IMODE(root.lstat().st_mode)==0o700)
    core.write_private(root/'intent.json',canonical({'plan_sha256':approved,'started_utc':core.utc(),'state':'started'}))
    original_services=core.service_state(backup);original_containers=core.production_containers(backup)
    original_definitions=core.backup_definition_hashes(backup)
    targets=[];entries={};inventories={};windows={};results=[];last=['initial'];failed=False
    def phase(value):core.write_phase(root,value);last[0]=value
    try:
        configs,schema,uid,gid,config_metadata=configurations(core,backup)
        require({name:digest(raw) for name,raw in sorted(configs.items())}==plan['config_hashes'])
        require(digest(canonical(config_metadata))==plan['config_metadata_sha256'])
        require(active_release_processes(backup,configs)==plan['active_release_processes'])
        for source in core.SOURCES:
            kind=source[0];phase('capture_'+kind)
            if kind=='central':
                data,roles,inventory,window,files,media_inventory=capture_media_database(core,backup,source,schema,uid,gid,plan['expected_migrations'])
                require(digest(canonical(media_inventory))==plan['media_root_inventory_sha256'])
                entries.update(files);entries['inventories/media.json']=canonical(media_inventory)
            else:data,roles,inventory,window=core.capture_database(backup,source)
            entries['databases/'+kind+'.dump']=data;entries['roles/'+kind+'.sql']=roles
            entries['inventories/'+kind+'.json']=canonical(inventory);inventories[kind]=inventory;windows[kind]=window
        entries.update(configs)
        entries['inventories/configurations.json']=canonical(config_metadata)
        current,_,_,_,metadata=configurations(core,backup);require(current==configs and metadata==config_metadata)
        require(sum(map(len,entries.values()))<=MAXIMUM)
        manifest={'format':'easel-encrypted-backup-v2','inventory_version':4,'coverage':'settled-owned-images-v1','target':core.TARGET,
            'plan_sha256':approved,'candidate_sha256':WHEEL_SHA,'source_release':RELEASE,
            'central_schema_sha256':plan['central_schema_sha256'],'central_config_sha256':plan['central_config_sha256'],
            'snapshots':windows,'consistency':plan['consistency'],
            'files':[{'name':name,'length':len(raw),'sha256':digest(raw)} for name,raw in sorted(entries.items())]}
        core.verify_members(entries,manifest);payload=backup.archive(entries,manifest);helper=backup.crypto()
        public=backup.read_fixed(backup.ROOT/'transfer-public.pem',32768,private=False)
        private=backup.read_fixed(backup.ROOT/'transfer-private.pem',32768);backup_id=str(uuid.uuid4())
        phase('seal_archive')
        envelope=helper.seal(public,payload,{'transfer_id':backup_id,'kind':'central','target':core.TARGET,
            'expected_sha256':digest(payload),'expected_length':len(payload)})
        def decrypt(value):
            clear=helper.unseal(private,value,backup_id,'central',core.TARGET)
            require(clear==payload);backup.verify_payload(clear,manifest);return clear
        backup.DEST=Path(plan['dest']);cipher=backup.save(envelope,backup_id,decrypt);phase('archive_saved')
        clear=decrypt(backup.read_fixed(Path(cipher),MAXIMUM+1024*1024))
        with tarfile.open(fileobj=io.BytesIO(clear),mode='r:') as archive:
            restored={item.name:archive.extractfile(item).read(MAXIMUM+1) for item in archive.getmembers() if item.name!='manifest.json'}
        core.verify_members(restored,manifest)
        restored_schema=core.bound_central_schema(restored['configs/etc/easel-central/central.env'],plan['central_schema_sha256'],plan['central_config_sha256'])
        media_environment(restored['configs/etc/easel-central/central.env'],allow_absent=True);media_configuration(restored['configs'+str(MEDIA)])
        verify_dropins({kind:restored['configs'+str(path)] for kind,path in DROPINS.items()},
                       {kind:restored['configs'+str(path)] for kind,path in MAIN_UNITS.items()})
        require({name:digest(restored[name]) for name in configs}==plan['config_hashes'])
        phase('restore_media_files')
        media=restore_private_media(root/'restored-private',{name:raw for name,raw in restored.items() if name.startswith('media/blobs/')},
            json.loads(restored['inventories/media.json'],object_pairs_hook=unique),restored['configs'+str(MEDIA)],uid,gid)
        restored_metadata=json.loads(restored['inventories/configurations.json'],object_pairs_hook=unique)
        require(digest(canonical(restored_metadata))==plan['config_metadata_sha256'])
        media.update(restore_source_configurations(root/'restored-configurations',{name:restored[name] for name in configs},restored_metadata))
        migration_verified=False
        for item in plan['targets']:
            core.resource_gate();targets.append(item)
            baseline=json.loads(restored['inventories/'+item['kind']+'.json'],object_pairs_hook=unique)
            core.match_inventories(inventories[item['kind']],baseline)
            result=core.restore_one(backup,item,restored,baseline,phase,r2_check_repair=item['kind']=='central',r2_schema=restored_schema)
            if item['kind']=='central':
                actual=core.psql(backup.command,item['name'],item['user'],baseline['database']['datname'],migrations_sql(core,restored_schema))
                match_migrations(plan['expected_migrations'],actual);migration_verified=True
                restored_state=core.psql(backup.command,item['name'],item['user'],baseline['database']['datname'],window_sql(core,restored_schema))
                settled_window(restored_state);require(digest(canonical(restored_state))==plan['media_window_sha256'])
                restored_rows=core.psql(backup.command,item['name'],item['user'],baseline['database']['datname'],media_rows_sql(core,restored_schema))
                require(digest(canonical(restored_rows))==plan['media_registry_sha256'])
                require(digest(canonical(restored_rows))==json.loads(restored['inventories/media.json'])['registry_sha256'])
                media.update(settled_jobs_restore_verified=True,typed_receipts_restore_verified=True)
            results.append(result);core.stop_target(backup,item);phase('restore_'+item['kind']+'_stopped')
        phase('verify_media_window')
        state=central_read(core,backup,window_sql(core,schema));settled_window(state)
        require(digest(canonical(state))==plan['media_window_sha256'])
        rows=central_read(core,backup,media_rows_sql(core,schema))
        require(digest(canonical(rows))==plan['media_registry_sha256'])
        files,inventory=capture_blobs(STORAGE,rows,uid,gid)
        require(len(files)==2 and files=={name:raw for name,raw in entries.items() if name.startswith('media/blobs/')}
                and digest(canonical(inventory))==plan['media_root_inventory_sha256'])
        current,_,_,_,metadata=configurations(core,backup);require(current==configs and metadata==config_metadata)
        require(active_release_processes(backup,current)==plan['active_release_processes'])
        require(release_migrations(backup)==plan['expected_migrations'])
        phase('verify_original_services')
        require(core.service_state(backup)==original_services and core.production_containers(backup)==original_containers
                and core.backup_definition_hashes(backup)==original_definitions)
        proof=enable_proof(results,media,plan,approved,migration_verified)
        receipt={**proof,'status':'fresh_settled_media_restore_verified','started_utc':json.loads((root/'intent.json').read_bytes())['started_utc'],
            'finished_utc':core.utc(),'ciphertext_path':cipher,'ciphertext_bytes':len(envelope),'ciphertext_sha256':digest(envelope),
            'manifest_sha256':digest(canonical(manifest)),'databases':results,'snapshots':windows,'consistency':plan['consistency'],
            'production_writes':0,'provider_requests':0,'public_ports':0,'containers_stopped_retained':True,'volumes_retained':True,
            'original_backup_timer_changed':False,'original_services_unchanged':True,'original_containers_unchanged':True}
        core.write_private(root/'restore-safe.json',canonical(receipt))
        core.write_private(root/'enable-proof.json',canonical(receipt));phase('complete')
        print(json.dumps(receipt,sort_keys=True))
    except BaseException:failed=True;raise
    finally:
        cleanup=cleanup_owned(core,backup,targets)
        if failed:core.write_private(root/'failure-safe.json',canonical({'status':'media_restore_failed','phase':last[0],
            'nonce':plan['nonce'],'plan_sha256':approved,'production_writes':0,'cleanup':cleanup}))

def main():
    os.umask(0o077);resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    def interrupted(*_):raise Rejected('media_restore_interrupted')
    signal.signal(signal.SIGTERM,interrupted);signal.signal(signal.SIGHUP,interrupted)
    parser=argparse.ArgumentParser();parser.add_argument('--nonce',required=True);parser.add_argument('--apply',action='store_true')
    parser.add_argument('--reviewed-plan-sha256');args=parser.parse_args()
    core=load_core();core.container_identity(args.nonce,'central');backup=core.base_module()
    plan=build_plan(core,backup,args.nonce)
    if not args.apply:
        require(args.reviewed_plan_sha256 is None)
        print(json.dumps({'plan':plan,'plan_sha256':digest(canonical(plan))},sort_keys=True));return
    apply(core,backup,plan,args.reviewed_plan_sha256)

if __name__=='__main__':
    try:main()
    except BaseException:
        print('easel media restore rejected; private intent and owned objects retained',file=sys.stderr);sys.exit(1)
