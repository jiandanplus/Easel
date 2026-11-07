#!/usr/bin/env python3
"""Fixed-host two-model availability window. No restore or provider execution.

Run with the reviewed deployed Python using -B. plan is database read-only;
pause/resume only call the deployed MediaService.set_enabled domain method.
Every intent/result is private, exclusive and durable. Repeating an operation
only observes state; an interrupted operation is never automatically replayed.
"""
import argparse
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import resource
import socket
import stat
import subprocess
import sys

ROOT=Path('/root/easel-mixed-media-restore-review-20261027')
PREP=Path('/root/easel-image-release-20261027/size-v2/prepare_m1_image_release.py')
PREP_SHA='ff9d019ef275798b24e12adcc25c8018c9f8f3ff244dc003133f2df4912c02ef'
RELEASE='/srv/easel/releases/20261027-images-m1-1-size-v2'
MODELS=('gpt-image-2.5-flare','gpt-image-2.5-sunburst')
STORAGE=Path('/var/lib/easel-central/media')
EXPECTED_COUNTS={'jobs':3,'settled_jobs':2,'pending_jobs':1,'active_jobs':0,'artifacts':2,'blobs':3,
    'attached_blobs':2,'staged_blobs':1,'missing_staged':1,'gc_claims':0,'pending_actual_null':1,
    'pending_hold':1000000,'budgets':3,'invocations':3}
EXPECTED_WALLET={'issued':10000000,'available':6952153,'held':1000000,'spent':2047847,'expired':0,'refunded':0}

class Rejected(Exception):pass

def require(value):
    if not value:raise Rejected('media_window_rejected')

def digest(raw):return hashlib.sha256(raw).hexdigest()

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()

def tool_sha():return digest(Path(__file__).read_bytes())

def utc():return datetime.now(timezone.utc).isoformat()

def signature(info):
    return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns,info.st_uid,info.st_gid,info.st_mode,info.st_nlink)

def unique(pairs):
    value={}
    for key,item in pairs:require(key not in value);value[key]=item
    return value

def private_directory(path):
    info=Path(path).lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid==os.geteuid() and stat.S_IMODE(info.st_mode)==0o700)

def private_bytes(path,maximum=4*1024*1024):
    try:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as stream:
            info=os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_uid==os.geteuid() and stat.S_IMODE(info.st_mode)==0o600
                and info.st_nlink==1 and info.st_size<=maximum)
            raw=stream.read(maximum+1);require(len(raw)<=maximum);return raw
    except OSError:raise Rejected('media_window_rejected') from None

def read_private(path):
    try:return json.loads(private_bytes(path),object_pairs_hook=unique)
    except (ValueError,UnicodeError):raise Rejected('media_window_rejected') from None

def write_private(path,value):
    path=Path(path);private_directory(path.parent);raw=canonical(value)
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        try:
            view=memoryview(raw)
            while view:
                count=os.write(fd,view);require(count>0);view=view[count:]
            os.fchmod(fd,0o600);os.fsync(fd)
        finally:os.close(fd)
        parent=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(parent)
        finally:os.close(parent)
    except OSError:raise Rejected('media_window_rejected') from None
    return digest(raw)

def nonce_check(nonce):require(isinstance(nonce,str) and re.fullmatch('[a-f0-9]{32}',nonce) is not None)

def model_identity(snapshot):
    rows=snapshot['models']
    require(type(rows) is list and [row['model_id'] for row in rows]==list(MODELS))
    for row in rows:
        require(set(row)=={'model_id','enabled','definition_sha256'} and type(row['enabled']) is bool
            and re.fullmatch('[a-f0-9]{64}',row['definition_sha256']) is not None)
    return [{key:row[key] for key in ('model_id','definition_sha256')} for row in rows]

def baseline(snapshot):
    model_identity(snapshot)
    require(all(row['enabled'] for row in snapshot['models']) and snapshot['counts']==EXPECTED_COUNTS
        and snapshot['wallet']==EXPECTED_WALLET)

def safe_snapshot(snapshot):
    return {'finance_sha256':digest(canonical(snapshot['finance'])),'financial_table_count':len(snapshot['finance']),
        'financial_row_count':sum(row['rows'] for row in snapshot['finance'].values()),
        'counts':snapshot['counts'],'models_enabled':sum(row['enabled'] for row in snapshot['models'])}

def make_plan(backend,directory,nonce):
    nonce_check(nonce);directory=Path(directory);private_directory(directory)
    path=directory/('window-'+nonce+'-plan-private.json')
    require(not path.exists() and not path.is_symlink())
    binding=backend.verify();snapshot=backend.snapshot();baseline(snapshot)
    plan={'format':'easel-media-restore-window-v1','nonce':nonce,'tool_sha256':tool_sha(),
        'binding':binding,'original':snapshot,'actions':['pause_exact_two_models','resume_original_enabled_states'],
        'provider_requests':0,'money_mutations':0,'created_utc':utc()}
    sha=write_private(path,plan)
    return {'plan_path':str(path),'plan_sha256':sha,'actions':plan['actions'],**safe_snapshot(snapshot)}

def approved(path,expected):
    path=Path(path);private_directory(path.parent);raw=private_bytes(path)
    require(isinstance(expected,str) and re.fullmatch('[a-f0-9]{64}',expected) is not None and digest(raw)==expected)
    try:plan=json.loads(raw,object_pairs_hook=unique)
    except (ValueError,UnicodeError):raise Rejected('media_window_rejected') from None
    require(plan['format']=='easel-media-restore-window-v1' and plan['tool_sha256']==tool_sha())
    nonce_check(plan['nonce']);require(path.name=='window-'+plan['nonce']+'-plan-private.json')
    baseline(plan['original']);return plan

def record_path(path,plan,phase,kind):return Path(path).parent/('window-'+plan['nonce']+'-'+phase+'-'+kind+'-private.json')

def existing_record(path,plan,sha,phase,kind):
    target=record_path(path,plan,phase,kind)
    if not target.exists() and not target.is_symlink():return None
    value=read_private(target)
    require(value['phase']==phase and value['plan_sha256']==sha and value['nonce']==plan['nonce'] and value['tool_sha256']==plan['tool_sha256'])
    return value

@contextmanager
def operation_lock(path):
    # Every nonce controls the same two models. Serialize on their shared private
    # directory, since separate plan inodes cannot exclude competing windows.
    directory=Path(path).parent;private_directory(directory)
    fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:raise Rejected('media_window_rejected') from None
        yield
    finally:os.close(fd)

def observe(path,plan,sha,snapshot,phase='status',repeated=False):
    pause=existing_record(path,plan,sha,'pause','intent');resume=existing_record(path,plan,sha,'resume','intent')
    last='resume' if resume else 'pause' if pause else None
    result=existing_record(path,plan,sha,last,'result') if last else None
    desired=all(row['enabled'] is (last=='resume') for row in snapshot['models']) if last else False
    current_window=(last!='pause' or {**snapshot,'models':plan['original']['models']}==plan['original'])
    success=bool(result and result['success'] and desired and current_window)
    return {'phase':phase,'state':(last+('_complete' if success else '_incomplete')) if last else 'planned',
        'success':success,'repeated':repeated,'plan_sha256':sha,
        'financial_unchanged':snapshot['finance']==plan['original']['finance'],**safe_snapshot(snapshot)}

async def transition(backend,path,sha,phase):
    require(phase in {'pause','resume','status'});plan=approved(path,sha)
    with operation_lock(path):
        require(backend.verify(plan['binding']['owner_locator'])==plan['binding'])
        before=backend.snapshot();require(model_identity(before)==model_identity(plan['original']))
        if phase=='status':return observe(path,plan,sha,before)
        if existing_record(path,plan,sha,phase,'intent'):
            return observe(path,plan,sha,before,phase,True)
        if phase=='pause':require(before==plan['original'])
        else:require(existing_record(path,plan,sha,'pause','intent') is not None)
        record={'phase':phase,'plan_sha256':sha,'tool_sha256':plan['tool_sha256'],'nonce':plan['nonce'],'started_utc':utc()}
        write_private(record_path(path,plan,phase,'intent'),record)
        success=False;after=None
        try:
            desired=phase=='resume'
            for row in before['models']:
                if row['enabled'] is not desired:
                    await backend.set_enabled(row['model_id'],desired,'media-restore-window-'+plan['nonce'])
            await backend.close()
            # Use a fresh read-only transaction after domain transactions commit.
            require(backend.verify(plan['binding']['owner_locator'])==plan['binding'])
            after=backend.snapshot();require(model_identity(after)==model_identity(plan['original']))
            require(all(row['enabled'] is desired for row in after['models']))
            if phase=='pause':
                require(after['counts']==EXPECTED_COUNTS and after['media_sha256']==before['media_sha256']
                    and after['files_sha256']==before['files_sha256'] and after['migrations_sha256']==before['migrations_sha256']
                    and after['finance']==before['finance'])
            success=True
        except BaseException:
            # Never print exception text: connection errors may contain credentials.
            try:await backend.close()
            except BaseException:pass
            try:after=backend.snapshot()
            except BaseException:after=None
        result={**record,'finished_utc':utc(),'success':success,'error':None if success else 'media_window_operation_failed',
            'financial_unchanged':after['finance']==plan['original']['finance'] if after else None,
            'observed':safe_snapshot(after) if after else None,'provider_requests':0,'money_mutations':0}
        write_private(record_path(path,plan,phase,'result'),result)
        return {key:value for key,value in result.items() if key not in {'nonce','tool_sha256'}}


def trusted_source(path,*,private=False):
    path=Path(path)
    for parent in path.parents:
        info=parent.lstat();require(stat.S_ISDIR(info.st_mode) and info.st_uid==0 and not stat.S_IMODE(info.st_mode)&0o022)
    info=path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and not stat.S_IMODE(info.st_mode)&0o022)
    if private:require(info.st_uid==0 and stat.S_IMODE(info.st_mode)==0o600)
    return path.read_bytes()


class ProductionBackend:
    def __init__(self):self.db=None;self.service=None;self.prep=None

    def verify(self,locator=None):
        require(os.geteuid()==0 and socket.gethostname()=='ser362540173134')
        private_directory(ROOT);trusted_source(Path(__file__),private=True)
        require(Path(__file__).parent==ROOT and digest(trusted_source(PREP,private=True))==PREP_SHA)
        if self.prep is None:
            sys.dont_write_bytecode=True
            spec=importlib.util.spec_from_file_location('reviewed_m1_preparation',PREP)
            self.prep=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.prep)
        self.prep.verify_staged()
        env=self.prep.env_values(self.prep.CENTRAL_ENV)
        self.dsn,owner=self.prep.choose_owner(env,locator);self.schema=env['CENTRAL_DB_SCHEMA']
        require(locator is None or owner==locator)
        paths=[self.prep.CENTRAL_ENV,self.prep.CONSOLE_ENV,self.prep.EXECUTION,self.prep.MEDIA,
            *[Path('/etc/systemd/system')/unit for unit in self.prep.UNITS.values()],
            *[Path('/etc/systemd/system')/(unit+'.d/90-m1-images.conf') for unit in self.prep.UNITS.values()]]
        secret_paths={self.prep.CENTRAL_ENV,self.prep.CONSOLE_ENV,self.prep.EXECUTION,self.prep.MEDIA}
        hashes={}
        for path in paths:
            raw=trusted_source(path)
            if path in secret_paths:
                info=path.lstat();require(stat.S_IMODE(info.st_mode)==0o600 and info.st_uid in {0,pwd.getpwnam('easel-central').pw_uid,pwd.getpwnam('easel-console').pw_uid})
            hashes[str(path)]=digest(raw)
        for unit in self.prep.UNITS.values():
            result=subprocess.run(['/usr/bin/systemctl','show','--property=ActiveState,WorkingDirectory',unit],
                capture_output=True,check=False,timeout=10)
            require(result.returncode==0 and len(result.stdout)<=8192)
            values=dict(line.split('=',1) for line in result.stdout.decode().splitlines())
            require(values=={'ActiveState':'active','WorkingDirectory':RELEASE})
        return {'release':RELEASE,'source_sha256':digest(canonical(hashes)),'owner_locator':owner}

    def snapshot(self):
        from psycopg import sql
        with self.prep.connect_owner(self.dsn,self.schema) as c:
            models=c.execute('SELECT m.id model_id,s.enabled,m.definition FROM media_models m LEFT JOIN media_model_states s ON s.model_id=m.id ORDER BY m.id').fetchall()
            models=[{'model_id':row['model_id'],'enabled':row['enabled'],'definition_sha256':digest(canonical(row['definition']))} for row in models]
            finance=self.prep.financial_snapshot(c)
            media={}
            tables=[row['tablename'] for row in c.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema() AND tablename LIKE 'media_%' AND tablename<>'media_model_states' ORDER BY tablename").fetchall()]
            for table in tables:
                hashed=hashlib.sha256();count=0
                for row in c.execute(sql.SQL('SELECT to_jsonb(t)::text body FROM {} t ORDER BY 1').format(sql.Identifier(table))):
                    hashed.update(row['body'].encode()+b'\n');count+=1;require(count<=100000)
                media[table]={'rows':count,'sha256':hashed.hexdigest()}
                if table in {'media_jobs','media_invocations','media_storage_budgets'}:finance[table]=media[table]
            wallet=c.execute('SELECT '+','.join('coalesce(sum('+name+'),0)::bigint '+name for name in EXPECTED_WALLET)+' FROM credit_lots').fetchone()
            counts=c.execute("""SELECT
                (SELECT count(*) FROM media_jobs) jobs,
                (SELECT count(*) FROM media_jobs WHERE status='settled') settled_jobs,
                (SELECT count(*) FROM media_jobs WHERE status='pending_reconciliation') pending_jobs,
                (SELECT count(*) FROM media_jobs WHERE status IN ('reserved','dispatched')) active_jobs,
                (SELECT count(*) FROM media_jobs WHERE status='pending_reconciliation' AND actual IS NULL AND amount=1000000) pending_actual_null,
                (SELECT coalesce(sum(r.amount),0)::bigint FROM credit_reservations r JOIN media_jobs j ON j.reservation_id=r.id WHERE j.status='pending_reconciliation' AND r.status='pending_reconciliation' AND r.actual IS NULL) pending_hold,
                (SELECT count(*) FROM media_artifacts) artifacts,
                (SELECT count(*) FROM media_blobs) blobs,
                (SELECT count(*) FROM media_blobs WHERE state='attached') attached_blobs,
                (SELECT count(*) FROM media_blobs WHERE state='staged') staged_blobs,
                (SELECT count(*) FROM media_blobs WHERE gc_token IS NOT NULL OR gc_lease_until IS NOT NULL) gc_claims,
                (SELECT count(*) FROM media_storage_budgets) budgets,
                (SELECT count(*) FROM media_invocations) invocations""").fetchone()
            rows=c.execute('SELECT storage_key,sha256,size,state FROM media_blobs ORDER BY storage_key').fetchall()
            files,missing=self.files_snapshot(rows);counts['missing_staged']=missing
            migrations=c.execute('SELECT version,checksum FROM schema_migrations ORDER BY version').fetchall()
            require(len(migrations)==16 and [row['version'][:3] for row in migrations]==[str(i).zfill(3) for i in range(1,17)])
            from platform_service.db import MIGRATIONS_DIR
            require(migrations==[{'version':p.name,'checksum':digest(p.read_bytes())} for p in sorted(MIGRATIONS_DIR.glob('*.sql'))])
        return {'models':models,'counts':counts,'wallet':wallet,'finance':finance,'media_sha256':digest(canonical(media)),
            'files_sha256':digest(canonical(files)),'migrations_sha256':digest(canonical(migrations))}

    def files_snapshot(self,rows):
        owner=pwd.getpwnam('easel-central');info=STORAGE.lstat()
        require(stat.S_ISDIR(info.st_mode) and (info.st_uid,info.st_gid,stat.S_IMODE(info.st_mode))==(owner.pw_uid,owner.pw_gid,0o700))
        fd=os.open(STORAGE,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            before=os.fstat(fd);files=[];missing=0;attached=set()
            for row in rows:
                key=row['storage_key'];require(re.fullmatch('(?:[a-f0-9]{32}|[a-f0-9]{64})',key) is not None)
                if row['state']=='staged':
                    try:os.stat(key,dir_fd=fd,follow_symlinks=False)
                    except FileNotFoundError:missing+=1;continue
                    raise Rejected('media_window_rejected')
                require(row['state']=='attached');attached.add(key)
                item=os.open(key,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
                with os.fdopen(item,'rb') as stream:
                    meta=os.fstat(stream.fileno())
                    require(stat.S_ISREG(meta.st_mode) and meta.st_nlink==1 and (meta.st_uid,meta.st_gid,stat.S_IMODE(meta.st_mode))==(owner.pw_uid,owner.pw_gid,0o600)
                        and meta.st_size==row['size'] and 0<meta.st_size<=20971520)
                    raw=stream.read(20971521)
                    require(signature(meta)==signature(os.fstat(stream.fileno())) and len(raw)==row['size'] and digest(raw)==row['sha256'])
                    require(signature(meta)==signature(os.stat(key,dir_fd=fd,follow_symlinks=False)))
                    files.append({'key':key,'size':row['size'],'sha256':row['sha256'],'metadata':signature(meta)})
            require(set(os.listdir(fd))==attached and signature(before)==signature(os.fstat(fd)) and signature(before)==signature(STORAGE.lstat()))
            return files,missing
        finally:os.close(fd)

    async def set_enabled(self,model,enabled,actor):
        require(model in MODELS and type(enabled) is bool and re.fullmatch('media-restore-window-[a-f0-9]{32}',actor) is not None)
        from platform_service.db import Database
        from platform_service.media.core import MediaService
        from platform_service.contracts import OperatorIdentity
        from platform_service.identity import IdentityService
        if self.db is None:
            self.db=Database(self.dsn,schema=self.schema);await self.db.open()
            self.service=MediaService(self.db,IdentityService(self.db,None))
        await self.service.set_enabled(model,enabled,OperatorIdentity(actor,frozenset({'media:publish'})))

    async def close(self):
        if self.db is not None:
            try:await self.db.close()
            finally:self.db=None;self.service=None


def main():
    os.umask(0o077);resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['plan','pause','resume','status'])
    parser.add_argument('--nonce');parser.add_argument('--plan',type=Path);parser.add_argument('--expected-plan-sha256')
    args=parser.parse_args();backend=ProductionBackend()
    if args.phase=='plan':
        require(args.plan is None and args.expected_plan_sha256 is None)
        result=make_plan(backend,ROOT,args.nonce)
    else:
        require(args.nonce is None and args.plan is not None and args.expected_plan_sha256 is not None and args.plan.parent==ROOT)
        result=asyncio.run(transition(backend,args.plan,args.expected_plan_sha256,args.phase))
    print(json.dumps(result,sort_keys=True))
    return 0 if result.get('success',True) or args.phase=='status' else 1

if __name__=='__main__':
    try:exit_code=main()
    except SystemExit:raise
    except BaseException:
        print('easel media availability window rejected; private records retained',file=sys.stderr);exit_code=1
    sys.exit(exit_code)
