"""F2 fixed-host read-only producer. No pause, money, restore or provider actions.

Production entry points construct their own local transport, facts and clock.
All source/configuration reads below are future server-only operations; tests
use synthetic seams and do not certify actual PG/Linux/root acceptance.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from functools import lru_cache
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import pwd
import re
import resource
import select
import shlex
import signal
import socket
import stat
import subprocess
import sys
import time
from urllib.parse import urlsplit,unquote,parse_qsl
import uuid

F1_SHA='3fff42e415989cf8c6d8004b34ac0ff2990a6472247ce581ece33dee99541f6e'
ROOT=Path('/root/easel-media-capture-v2-20261028')
DOCKER_CONFIG=ROOT/'docker-client-empty'
SOCKET=Path('/run/docker.sock')
MAXIMUM=32*1024*1024
NONTERMINAL={'reserved','dispatched','pending_reconciliation'}
SERVICES=('easel-central-production','easel-console-production','easel-gateway-tunnel','nginx')
CONTAINERS=('easel-central-pg-20261024','easel-identity-pg-v2-20261024','easel-logto-v2-20261024')
GRAPH_KEYS={
 'credit_lots':('id',),'credit_reservations':('id',),'credit_holds':('reservation_id','lot_id'),
 'execution_jobs':('id',),'execution_invocations':('id',),'execution_resolutions':('job_id',),
 'execution_resolution_operations':('id',),'execution_receipts':('job_id',),'execution_terminal_evidence':('job_id',),
 'execution_provider_attempts':('job_id',),'execution_provider_associations':('job_id','kind'),
 'media_jobs':('id',),'media_invocations':('id',),'execution_scope_hashes':('id',),
}


class Rejected(Exception):pass


def require(value):
    if not value:raise Rejected('capture_rejected')


def sha(raw):return hashlib.sha256(raw).hexdigest()


@lru_cache(maxsize=1)
def f1():
    path=Path(__file__).with_name('media_restore_freeze.py')
    raw=path.read_bytes();require(sha(raw)==F1_SHA)
    module=type(sys)('fixed_capture_f1');module.__file__=str(path)
    exec(compile(raw,str(path),'exec'),module.__dict__)
    return module


def contracts():return f1().reviewed_contracts()


def _integer(value,minimum=0):require(type(value)is int and minimum<=value<=9223372036854775807)


def _utc(value):
    require(type(value)is str)
    try:
        result=datetime.fromisoformat(value)
        require(result.utcoffset()==timedelta(0));return result
    except (ValueError,TypeError,OverflowError):raise Rejected('capture_rejected') from None


def parse_environment(raw):
    try:
        require(type(raw)is bytes and len(raw)<=65536 and b'\0'not in raw)
        result={}
        for line in raw.decode('utf-8').splitlines():
            if not line.strip()or line.lstrip().startswith('#'):continue
            key,separator,value=line.partition('=')
            require(separator and re.fullmatch('[A-Z][A-Z0-9_]*',key)is not None and key not in result)
            parts=shlex.split(value,comments=False);require(len(parts)<=1)
            result[key]=parts[0]if parts else ''
        return result
    except Rejected:raise
    except Exception:raise Rejected('capture_rejected') from None


def _index(rows,keys):
    require(type(rows)is list and len(rows)<=100000);result={}
    for row in rows:
        require(type(row)is dict and all(type(row.get(k))is str and row[k]for k in keys))
        key=tuple(row[k]for k in keys);require(key not in result);result[key]=row
    return result


def project_finance(bundle):
    """Whole raw graph. No domain calls, expiry, filters or historical hold sums."""
    try:
        require(type(bundle)is dict and set(bundle)==set(GRAPH_KEYS));f1().canonical(bundle)
        indexes={t:_index(bundle[t],keys)for t,keys in GRAPH_KEYS.items()}
        require(sum(len(v)for v in indexes.values())<=100000)
        lots=indexes['credit_lots'];reservations=indexes['credit_reservations']
        for r in reservations.values():
            _integer(r['amount'],1)
            require(r['funding_mode']in {'test','live'}and (r['actual']is None or type(r['actual'])is int and 0<=r['actual']<=r['amount']))
        wallet={k:0 for k in ('issued','available','held','spent','expired','refunded')}
        for lot in lots.values():
            require(type(lot['test_only'])is bool)
            for k in wallet:_integer(lot[k]);wallet[k]+=lot[k];_integer(wallet[k])
            require(lot['issued']==sum(lot[k]for k in ('available','held','spent','expired','refunded')))
        claimed={};pending=[];waived=0
        for domain in ('execution','media'):
            invocations=indexes[domain+'_invocations'];aggregates={k:[0,0,0]for k in invocations}
            for j in indexes[domain+'_jobs'].values():
                r=reservations.get((j['reservation_id'],));i=invocations.get((j['invocation_id'],))
                require(r is not None and i is not None and j['reservation_id']not in claimed)
                claimed[j['reservation_id']]=(domain,j)
                require(j['status']==r['status'] and j['status']in NONTERMINAL|{'settled','released','waived'}
                        and j['amount']==r['amount'] and j['actual']==r['actual'] and r['operation']==domain)
                _integer(j['amount'],1)
                require(j['actual']is None or type(j['actual'])is int and 0<=j['actual']<=j['amount'])
                scope=j['scope'];require(type(scope)is dict and set(scope)=={'user_id','tenant_id','billing_account_id','product_id','session_id','authorization_epoch'}and i['scope']==scope)
                for key in ('user_id','tenant_id','billing_account_id','product_id','session_id'):f1()._identifier(scope[key])
                _integer(scope['authorization_epoch'])
                for key in ('user_id','billing_account_id','product_id','funding_mode'):
                    require(j[key]==r[key]==i[key])
                for key in ('user_id','billing_account_id','product_id'):require(scope[key]==j[key])
                require(scope['tenant_id']==r['tenant_id'] and scope['session_id']==i['session_id'] and j['model']==i['model'])
                require(j['status']not in {'reserved','dispatched'})
                amount=aggregates[(j['invocation_id'],)];amount[0]+=1
                if j['status']in NONTERMINAL:amount[1]+=j['amount'];require(j['actual']is None)
                if j['status']=='settled':require(j['actual']is not None);amount[2]+=j['actual']
                if domain=='execution':
                    receipt=indexes['execution_receipts'].get((j['id'],))
                    require((receipt is not None)==(j['status']=='settled'))
                    if receipt:require(receipt['actual']==j['actual'] and receipt['attempt_id']==j['attempt_id'])
                    resolution=indexes['execution_resolutions'].get((j['id'],))
                    require((resolution is not None)==(j['status']=='waived'))
                    if resolution:
                        _waived(j,r,resolution,indexes);waived+=1
                    if j['status']=='pending_reconciliation':
                        pending.append({'id':j['id'],'status':j['status'],'amount':j['amount'],'actual':None,
                            'source_rows':{'job':j,'reservation':r,'invocation':i,
                                'attempt':indexes['execution_provider_attempts'].get((j['id'],)),
                                'associations':[v for v in indexes['execution_provider_associations'].values()if v['job_id']==j['id']]}})
            for key,i in invocations.items():
                for k in ('operation_count','held','spent'):_integer(i[k])
                require([i['operation_count'],i['held'],i['spent']]==aggregates[key])
        held={key:0 for key in lots};original_allocations={key:0 for key in reservations}
        for hold in indexes['credit_holds'].values():
            r=reservations.get((hold['reservation_id'],));lot=lots.get((hold['lot_id'],))
            require(r is not None and lot is not None and hold['billing_account_id']==r['billing_account_id']==lot['billing_account_id']
                    and lot['test_only']==(r['funding_mode']=='test'));_integer(hold['amount'],1)
            original_allocations[(r['id'],)]+=hold['amount']
            if r['status']in NONTERMINAL:held[(hold['lot_id'],)]+=hold['amount']
        for key,lot in lots.items():require(held[key]==lot['held'])
        for r in reservations.values():
            require(r['status']in NONTERMINAL|{'settled','released','waived'} and r['status']not in {'reserved','dispatched'})
            require(original_allocations[(r['id'],)]==r['amount'])
            if r['status']in NONTERMINAL:require(r['id']in claimed)
            if r['status']=='waived':require(r['id']in claimed and claimed[r['id']][0]=='execution'and claimed[r['id']][1]['status']=='waived')
        for table,key in (('execution_resolutions','job_id'),('execution_receipts','job_id'),('execution_terminal_evidence','job_id'),('execution_provider_attempts','job_id'),('execution_provider_associations','job_id')):
            require(all((v[key],)in indexes['execution_jobs']for v in indexes[table].values()))
        require(set(indexes['execution_scope_hashes'])==set(indexes['execution_jobs']))
        for op in indexes['execution_resolution_operations'].values():
            actual={r['job_id']for r in indexes['execution_resolutions'].values()if r['operation_id']==op['id']}
            require(actual and actual=={v['id']for v in op['plan']['items']}=={v['id']for v in op['result']['items']}
                    and len(actual)==len(op['plan']['items'])==len(op['result']['items'])<=32)
        return wallet,sorted(pending,key=lambda v:v['id']),{'active':0,'waived':waived,'graph_sha256':sha(f1().canonical(bundle))}
    except Rejected:raise
    except Exception:raise Rejected('capture_rejected') from None


def _waived(j,r,a,indexes):
    op=indexes['execution_resolution_operations'].get((a['operation_id'],));require(op is not None)
    plan,result=op['plan'],op['result'];scope=indexes['execution_scope_hashes'][(j['id'],)]['scope_hash']
    require(j['reason']=='platform_absorbed' and j['actual']is None and r['actual']is None and r['completion_key']==op['id']
            and a['kind']=='platform_absorbed' and type(a['customer_charge'])is int and a['customer_charge']==0
            and a['provider_usage_known']is False and a['scope_hash']==scope
            and a['actor_id']==op['actor_id'] and a['plan_hash']==op['plan_hash']
            and a['reservation_id']==r['id'] and a['original_amount']==j['amount']
            and a['attempt_id']==j['attempt_id'] and a['binding_hash']==j['binding_hash'])
    require(str(plan['version'])=='1' and plan['kind']==result['kind']=='platform_absorbed'
            and plan['actor']==op['actor_id'] and plan['plan_sha256']==result['plan_sha256']==op['plan_hash']
            and result['operation_id']==op['id'] and a['reason']==plan['reason'] and a['approval_reference']==plan['approval_reference'])
    require(_utc(j['lease_until'])<=_utc(op['created_at']) and _utc(j['dispatched_at'])<=_utc(op['created_at']))
    items=[x for x in plan['items']if x['id']==j['id']];out=[x for x in result['items']if x['id']==j['id']]
    require(len(items)==len(out)==1 and out[0]['status']=='waived')
    for k in ('attempt_id','binding_hash','scope_hash','reservation_id','snapshot_hash'):require(items[0][k]==a[k])
    require(items[0]['amount']==str(a['original_amount']))


def clock_window(first,last,expiries):
    try:
        for s in (first,last):
            _integer(s['monotonic_ns']);require(re.fullmatch('[a-f0-9]{32}',s['boot_id'])is not None)
            require(abs((_utc(s['utc'])-_utc(s['db_utc'])).total_seconds())<=5)
        elapsed=(last['monotonic_ns']-first['monotonic_ns'])/1e9
        require(first['boot_id']==last['boot_id'] and 0<=elapsed<=900)
        for k in ('utc','db_utc'):
            delta=(_utc(last[k])-_utc(first[k])).total_seconds();require(0<=delta<=900 and abs(delta-elapsed)<=5)
        require(type(expiries)is list and bool(expiries) and all((_utc(v)-_utc(last['db_utc'])).total_seconds()>1800 for v in expiries))
        created=_utc(first['utc']);return created.isoformat(),(created+timedelta(seconds=900)).isoformat()
    except Rejected:raise
    except Exception:raise Rejected('capture_rejected') from None


def capture_queries(schema):
    c=contracts().core
    try:c.checked_central_schema(schema)
    except Exception:raise Rejected('capture_rejected') from None
    prefix=c.ident(schema)+'.'
    wallet='SELECT pg_catalog.json_build_object('+','.join(c.literal(k)+',coalesce(sum('+c.ident(k)+'::numeric),0)'for k in ('issued','available','held','spent','expired','refunded'))+') FROM '+prefix+'"credit_lots"'
    tables={k:v for k,v in GRAPH_KEYS.items()if k!='execution_scope_hashes'}
    parts=[c.literal(k)+",(SELECT coalesce(pg_catalog.json_agg(t ORDER BY "+','.join(c.ident(x)for x in keys)+"),'[]') FROM "+prefix+c.ident(k)+' t)'for k,keys in tables.items()]
    parts.append("'execution_scope_hashes',(SELECT coalesce(pg_catalog.json_agg(t ORDER BY id),'[]') FROM (SELECT id,pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(scope::text,'UTF8')),'hex') scope_hash FROM "+prefix+'execution_jobs) t)')
    return {'wallet':wallet,'graph':'SELECT pg_catalog.json_build_object('+','.join(parts)+')',
        'clock':"SELECT pg_catalog.json_build_object('db_utc',pg_catalog.clock_timestamp()::text,'transaction_utc',pg_catalog.transaction_timestamp()::text)",
        'identity':"SELECT pg_catalog.json_build_object('database',pg_catalog.current_database(),'user',current_user,'session',session_user,'bootstrap',(SELECT rolname FROM pg_catalog.pg_authid WHERE oid=10))",
        'expires':"SELECT coalesce(pg_catalog.json_agg(expires_at::text ORDER BY expires_at),'[]') FROM (SELECT expires_at FROM "+prefix+'media_blobs UNION ALL SELECT expires_at FROM '+prefix+'media_artifacts) t'}


def docker_invocation(args):
    return ['/usr/bin/docker','--host','unix:///run/docker.sock','--config',str(DOCKER_CONFIG),*args],{'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C'}


class ReadPolicy:
    def __init__(self,c,schema):
        self.c,self.schema=c,schema
        self.exact=set(capture_queries(schema).values())|{c.core.RELATIONS,c.core.ROLES.format(ignore=c.core.literal('')),
            c.core.MEMBERS.format(ignore=c.core.literal('')),c.core.DATABASE,c.core.EXTENSIONS,c.core.CONSTRAINTS,
            c.core.SETTINGS.format(ignore=c.core.literal('')),'SELECT count(*) FROM pg_largeobject_metadata',
            'SELECT to_json(rolname) FROM pg_authid WHERE oid=10',c.mixed.window_sql(c.core,schema),
            c.mixed.media_rows_sql(c.core,schema),c.mixed.migrations_sql(c.core,schema)}
        self.rows=[]
    def set_relations(self,relations):
        require(type(relations)is list and len(relations)<=2048)
        for row in relations:
            require(type(row)is dict and set(row)=={'nspname','relname','relkind'} and row['relkind']in {'r','p','m','S'})
            qualified=self.c.core.ident(row['nspname'])+'.'+self.c.core.ident(row['relname'])
            if row['relkind']=='S':self.exact.add('SELECT json_build_array(last_value,is_called) FROM '+qualified)
            else:
                self.rows.append("SELECT json_build_object('n',count(*),'bytes',coalesce(sum(row_bytes),0),'sha256',encode(sha256(convert_to(coalesce(string_agg(row_hash,'' ORDER BY row_hash),''),'UTF8')),'hex')) FROM (SELECT encode(sha256(convert_to(row_to_json(t)::text,'UTF8')),'hex') row_hash,octet_length(row_to_json(t)::text) row_bytes FROM "+qualified+' t LIMIT ')
        credits=[r for r in relations if r['relname']=='credit_lots'];require(len(credits)==1)
        schema=self.c.core.ident(credits[0]['nspname'])
        self.exact.add("SELECT json_build_object('lots_balanced',NOT EXISTS(SELECT 1 FROM "+schema+".credit_lots WHERE issued<>available+held+spent+expired+refunded),'journals_balanced',NOT EXISTS(SELECT 1 FROM "+schema+".credit_entries GROUP BY journal_id HAVING sum(delta)<>0))")
    def check(self,statement):
        require(type(statement)is str)
        if statement in self.exact:return
        for prefix in self.rows:
            if statement.startswith(prefix)and statement.endswith(') q'):
                number=statement[len(prefix):-3]
                if re.fullmatch('[0-9]{1,6}',number)and 1<=int(number)<=100001:return
        raise Rejected('capture_rejected')


def _signature(info):
    return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns,info.st_uid,info.st_gid,info.st_mode,info.st_nlink)


def _read_file(path,maximum,*,owner=0,private=True):
    path=Path(path)
    for parent in path.parents:
        s=parent.lstat();require(stat.S_ISDIR(s.st_mode)and s.st_uid in {0,997}and not s.st_mode&0o022)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        before=os.fstat(fd);require(stat.S_ISREG(before.st_mode)and before.st_uid==owner and before.st_nlink==1
            and before.st_size<=maximum and not before.st_mode&0o022 and (not private or stat.S_IMODE(before.st_mode)==0o600))
        raw=bytearray()
        while True:
            block=os.read(fd,65536)
            if not block:break
            raw.extend(block);require(len(raw)<=maximum)
        require(_signature(before)==_signature(os.fstat(fd))==_signature(path.lstat()))
        return bytes(raw)
    finally:os.close(fd)


def _platform_gate(expected):
    require(os.getuid()==os.geteuid()==0 and sys.platform=='linux' and socket.gethostname()==f1().TARGET
            and sys.flags.isolated and sys.dont_write_bytecode)
    require(type(expected)is str and re.fullmatch('[a-f0-9]{64}',expected)is not None
            and sha(_read_file(Path(__file__),1024*1024))==expected)
    require(sha(_read_file(Path(__file__).with_name('media_restore_freeze.py'),1024*1024))==F1_SHA)
    require(Path(sys.executable).resolve()==Path(contracts().mixed.RELEASE+'/.venv/bin/python').resolve())


def _private_directory(path,*,create=False):
    path=Path(path)
    if create and not path.exists():path.mkdir(mode=0o700)
    fd=f1()._private_directory(path);os.close(fd)


def _observe_child_exit(proc):
    # WNOWAIT preserves this child's PID until its owned group is signalled.
    # The production factory is Linux-only. No reaping/poll fallback exists.
    require(proc.returncode is None and callable(getattr(os,'waitid',None)))
    result=os.waitid(os.P_PID,proc.pid,os.WEXITED|os.WNOHANG|os.WNOWAIT)
    if result is None:return None
    require(result.si_pid==proc.pid and result.si_signo==signal.SIGCHLD)
    if result.si_code==os.CLD_EXITED:return result.si_status
    require(result.si_code in {os.CLD_KILLED,os.CLD_DUMPED});return -result.si_status


def _wait_child_exit(proc,timeout):
    deadline=time.monotonic()+timeout
    while True:
        result=_observe_child_exit(proc)
        if result is not None:return result
        remaining=deadline-time.monotonic();require(remaining>0)
        select.select([],[],[],min(.05,remaining))


def _wait_owned_group_gone(pgid,*,cleanup_deadline):
    # After reaping, only existence checks are allowed. PID reuse can cause a
    # conservative timeout, never another nonzero signal to a new group.
    deadline=min(time.monotonic()+2,cleanup_deadline)
    while True:
        remaining=deadline-time.monotonic();require(remaining>0)
        try:os.killpg(pgid,0)
        except ProcessLookupError:return
        except PermissionError:pass  # Not evidence that the group is gone.
        select.select([],[],[],min(.01,remaining))


def _cleanup_process(proc,*,failed):
    rejected=False;owned=False;deadline=None
    try:
        now=time.monotonic();deadline=getattr(proc,'_easel_cleanup_deadline',None)
        if deadline is None:
            deadline=now+12;proc._easel_cleanup_deadline=deadline
        require(type(deadline)in (int,float)and now<deadline)
        # No poll/wait has reaped the leader before this proof and signal.
        # Even an exited leader retains ownership of its original PID/PGID.
        _observe_child_exit(proc);owned=True
        if failed:
            require(time.monotonic()<deadline)
            try:os.killpg(proc.pid,signal.SIGKILL)
            except ProcessLookupError:pass
    except BaseException:rejected=True
    finally:
        try:
            remaining=max(0,deadline-time.monotonic())if type(deadline)in (int,float)else 0
            proc.wait(timeout=min(10,remaining))
        except BaseException:rejected=True
        finally:
            if failed and owned and proc.returncode is not None:
                try:_wait_owned_group_gone(proc.pid,cleanup_deadline=deadline)
                except BaseException:rejected=True
            for stream in (proc.stdin,proc.stdout,proc.stderr):
                try:
                    if stream and not stream.closed:stream.close()
                except BaseException:rejected=True
    if rejected:raise Rejected('capture_rejected')


def _run_bounded(argv,*,env,data=None,maximum=MAXIMUM,timeout=45):
    """Bound allocations while draining both pipes, including before exit/timeout."""
    proc=None;completed=False
    try:
        require(type(maximum)is int and 0<maximum<=MAXIMUM and 0<timeout<=180
                and (data is None or type(data)is bytes and len(data)<=MAXIMUM))
        proc=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                              env=env,close_fds=True,start_new_session=True)
        for stream in (proc.stdin,proc.stdout,proc.stderr):os.set_blocking(stream.fileno(),False)
        inputs=memoryview(data or b'');readers={proc.stdout:bytearray(),proc.stderr:bytearray()}
        output=readers[proc.stdout];deadline=time.monotonic()+timeout
        while readers or inputs or _observe_child_exit(proc)is None:
            remaining=deadline-time.monotonic();require(remaining>0)
            if not inputs and not proc.stdin.closed:proc.stdin.close()
            ready,writable,_=select.select(list(readers),[proc.stdin]if inputs else [],[],min(0.2,remaining))
            if writable:
                try:count=os.write(proc.stdin.fileno(),inputs[:65536])
                except BlockingIOError:count=0
                require(count>=0);inputs=inputs[count:]
            for stream in ready:
                buffer=readers[stream];limit=maximum if stream is proc.stdout else min(maximum,8192)
                try:chunk=os.read(stream.fileno(),min(65536,limit-len(buffer)+1))
                except BlockingIOError:continue
                if not chunk:del readers[stream];continue
                require(len(buffer)+len(chunk)<=limit);buffer.extend(chunk)
        require(_observe_child_exit(proc)==0);completed=True
        return bytes(output)
    except BaseException:raise Rejected('capture_rejected') from None
    finally:
        if proc is not None:_cleanup_process(proc,failed=not completed)


class LocalIO:
    def __init__(self):
        _private_directory(ROOT,create=True);_private_directory(DOCKER_CONFIG,create=True)
        self.socket_signature=None
        self.guard()
    def guard(self):
        require(os.geteuid()==0)
        cli=Path('/usr/bin/docker').lstat();sock=SOCKET.lstat()
        require(stat.S_ISREG(cli.st_mode)and cli.st_uid==0 and not cli.st_mode&0o022 and cli.st_nlink==1
                and stat.S_ISSOCK(sock.st_mode)and sock.st_uid==0 and stat.S_IMODE(sock.st_mode)in {0o600,0o660}
                and not list(DOCKER_CONFIG.iterdir()))
        current=(sock.st_dev,sock.st_ino,sock.st_ctime_ns)
        require(self.socket_signature is None or self.socket_signature==current);self.socket_signature=current
    def docker(self,args,*,data=None,maximum=MAXIMUM,timeout=45):
        self.guard();argv,env=docker_invocation(args)
        value=_run_bounded(argv,env=env,data=data,maximum=maximum,timeout=timeout)
        self.guard();return value
    def systemctl(self,args):
        require(args and args[0]=='show'and args[-1].removesuffix('.service')in SERVICES)
        allowed=[['show','--property=MainPID,ActiveState,WorkingDirectory,DropInPaths'],
                 ['show','--property=MainPID,ActiveState,ActiveEnterTimestampMonotonic'],
                 ['show','--property=MainPID,ActiveState,InvocationID'],['show','-p','MainPID','--value']]
        require(args[:-1]in allowed)
        value=_run_bounded(['/usr/bin/systemctl',*args],env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C'},maximum=8192,timeout=10)
        if args[1]!='-p':
            wanted=set(args[1].split('=',1)[1].split(','));_properties(value,wanted)
        return value
    @contextmanager
    def keeper(self,args):
        self.guard();argv,env=docker_invocation(args);completed=False
        proc=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                              env=env,close_fds=True,start_new_session=True)
        try:
            proc.stdin.write(b"BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; SET LOCAL statement_timeout='30s'; SET LOCAL lock_timeout='1s'; SET LOCAL idle_in_transaction_session_timeout='180s'; SELECT pg_catalog.pg_export_snapshot();\n")
            proc.stdin.flush();deadline=time.monotonic()+15;line=bytearray()
            while not line.endswith(b'\n'):
                require(time.monotonic()<deadline and _observe_child_exit(proc)is None and len(line)<128)
                if select.select([proc.stdout],[],[],0.2)[0]:
                    part=os.read(proc.stdout.fileno(),128-len(line));require(part);line.extend(part)
            token=line.decode().strip();require(re.fullmatch('[0-9A-Fa-f]+-[0-9A-Fa-f]+-[0-9]+',token)is not None)
            yield token
            proc.stdin.write(b'ROLLBACK;\n\\q\n');proc.stdin.flush();proc.stdin.close()
            require(_wait_child_exit(proc,10)==0);completed=True
        finally:
            _cleanup_process(proc,failed=not completed)
            self.guard()


def _properties(raw,wanted):
    result={}
    for line in raw.decode().splitlines():
        key,separator,value=line.partition('=');require(separator and key not in result);result[key]=value
    require(set(result)==set(wanted));return result


def _json(raw):
    require(type(raw)is bytes and len(raw)<=MAXIMUM)
    def unique(items):
        value={}
        for k,v in items:require(k not in value);value[k]=v
        return value
    value=json.loads(raw,object_pairs_hook=unique,parse_constant=lambda _:require(False))
    f1()._json_value(value);return value


class SourceBroker:
    def __init__(self,local=None):
        self.local=local if local is not None else LocalIO();self.c=contracts();self.active={}
        self.daemon=self.local.docker(['info','--format','{{json .ID}}'],maximum=4096).strip()
        require(0<len(self.daemon)<=1024)
        self.records={name:self.inspect(name)for name in CONTAINERS}
    def inspect(self,name):
        require(name in CONTAINERS)
        rows=_json(self.local.docker(['inspect','--type=container',name],maximum=1024*1024))
        require(type(rows)is list and len(rows)==1 and rows[0]['Name']=='/'+name and rows[0]['State']['Running']is True)
        r=rows[0];require(re.fullmatch('[a-f0-9]{64}',r['Id'])is not None and re.fullmatch('sha256:[a-f0-9]{64}',r['Image'])is not None)
        if name in {s[1]for s in self.c.core.SOURCES}:require(r['Image']==self.c.core.IMAGE.split('@',1)[1])
        return r
    def guard(self,name):
        require(self.local.docker(['info','--format','{{json .ID}}'],maximum=4096).strip()==self.daemon)
        actual=self.inspect(name);before=self.records[name]
        require((actual['Id'],actual['Image'],actual['State']['StartedAt'])==
                (before['Id'],before['Image'],before['State']['StartedAt']))
    @contextmanager
    def snapshot(self,kind,schema,*,catalog=False):
        source=next(s for s in self.c.core.SOURCES if s[0]==kind);_,name,user,db=source
        self.guard(name);args=['exec','-i',self.records[name]['Id'],'psql','-X','-qAt','--no-password','--set=ON_ERROR_STOP=1','--username='+user,'--dbname='+db]
        with self.local.keeper(args)as token:
            key=(name,token);require(key not in self.active)
            policy=ReadPolicy(self.c,schema)
            if not catalog:policy.exact={capture_queries(schema)[k]for k in ('clock','identity')}
            self.active[key]=policy
            try:
                identity=self.sql(kind,token,capture_queries(schema)['identity'])
                require(identity=={'database':db,'user':user,'session':user,'bootstrap':user})
                if catalog:
                    require(kind=='central');policy.set_relations(self.sql(kind,token,self.c.core.RELATIONS))
                yield token
            finally:
                del self.active[key];self.guard(name)
    def sql(self,kind,token,statement):
        _,name,user,db=next(s for s in self.c.core.SOURCES if s[0]==kind)
        return self.c.core.psql(self.command,name,user,db,statement,token)
    def command(self,argv,**kwargs):
        try:
            require(type(argv)is list and bool(argv)and all(type(v)is str for v in argv)
                    and set(kwargs)<={'data','maximum','timeout'})
            return self._command(argv,**kwargs)
        except BaseException:raise Rejected('capture_rejected') from None
    def _command(self,argv,**kwargs):
        if argv[0]=='/usr/bin/systemctl':return self.local.systemctl(argv[1:])
        require(argv[:2]==['/usr/bin/docker','exec'])
        if argv[2]=='-i':
            name=argv[3];source=next(s for s in self.c.core.SOURCES if s[1]==name);_,_,user,db=source
            require(argv[4:]==['psql','-X','-qAt','--no-password','--set=ON_ERROR_STOP=1','--username='+user,'--dbname='+db])
            data=kwargs.get('data');require(type(data)is bytes)
            raw=data.decode();match=re.match("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\nSET TRANSACTION SNAPSHOT '([0-9A-Fa-f]+-[0-9A-Fa-f]+-[0-9]+)';\n",raw)
            require(match is not None);token=match.group(1);policy=self.active.get((name,token));require(policy is not None)
            setup="SET LOCAL statement_timeout='30s'; SET LOCAL lock_timeout='1s'; SET LOCAL row_security=off; SET LOCAL timezone='UTC'; SET LOCAL datestyle='ISO, YMD'; SET LOCAL intervalstyle='postgres'; SET LOCAL extra_float_digits=3;\n"
            body=raw[match.end():];require(body.startswith(setup)and body.endswith(';\nROLLBACK;\n'))
            policy.check(body[len(setup):-len(';\nROLLBACK;\n')]);self.guard(name)
            return self.local.docker(['exec','-i',self.records[name]['Id'],*argv[4:]],data=data,maximum=kwargs.get('maximum',MAXIMUM),timeout=kwargs.get('timeout',45))
        name=argv[2];source=next(s for s in self.c.core.SOURCES if s[1]==name);_,_,user,db=source
        require(len(argv)==10 and argv[3:9]==['pg_dump','--schema-only','--quote-all-identifiers','--no-password','--username='+user,'--dbname='+db]
                and argv[9].startswith('--snapshot=')and source[0]=='central')
        token=argv[9].split('=',1)[1];policy=self.active.get((name,token));require(policy is not None and bool(policy.rows))
        self.guard(name)
        return self.local.docker(['exec',self.records[name]['Id'],*argv[3:]],maximum=kwargs.get('maximum',MAXIMUM),timeout=kwargs.get('timeout',90))


def _endpoint(url,db_name,record,*,caller=None):
    p=urlsplit(url);options=parse_qsl(p.query,keep_blank_values=True,strict_parsing=True)
    require(p.scheme in {'postgres','postgresql'}and not p.fragment and unquote(p.path.removeprefix('/'))==db_name
            and p.username and len(options)==len(dict(options))and set(dict(options))<={'sslmode','connect_timeout','application_name'})
    host=p.hostname;port=p.port or 5432
    require(host!='localhost')  # No unbound resolver/hosts-file inference.
    if host in {'127.0.0.1','::1'}:
        bindings=record['NetworkSettings']['Ports'].get('5432/tcp')or []
        require(sum(v['HostIp']==host and v['HostPort']==str(port)for v in bindings)==1)
    else:
        require(caller is not None and port==5432)
        matched=[]
        for network,details in record['NetworkSettings']['Networks'].items():
            aliases={record['Name'].removeprefix('/'),*(details.get('Aliases')or [])}
            if host in aliases and network in caller['NetworkSettings']['Networks']:matched.append(network)
        require(len(matched)==1)


def _process_environment(raw):
    require(raw.endswith(b'\0'));result={}
    for item in raw[:-1].split(b'\0'):
        key,sep,value=item.decode().partition('=');require(sep and key and key not in result);result[key]=value
    return result


def _runtime_database(kind,private,running,schema):
    require(kind in {'api','console'})
    prefix='CENTRAL_'if kind=='api'else 'CENTRAL_CONSOLE_'
    keys=(prefix+'DATABASE_URL',prefix+'DB_SCHEMA')
    require(all(k in private and running.get(k)==private[k]for k in keys)and private[keys[1]]==schema)
    return private[keys[0]]


class SystemClock:
    def sample(self,db):
        boot=_read_file('/proc/sys/kernel/random/boot_id',128,private=False).decode().strip()
        return {'utc':datetime.now(timezone.utc).isoformat(),'monotonic_ns':time.monotonic_ns(),
                'db_utc':_utc(db['db_utc']).isoformat(),'transaction_utc':_utc(db['transaction_utc']).isoformat(),
                'boot_id':uuid.UUID(boot).hex,'raw_boot_id':boot}


class SourceFacts:
    """Fixed reviewed filesystem/process source. No caller-provided facts object."""
    def __init__(self,broker):
        self.broker=broker;self.c=contracts();self.m=self.c.mixed;self.pwd=pwd
        self.REQUIRED=self.m.SOURCE_FILES[:6];self.command=broker.command
        self.schema=None;self.configuration_sha=None
    def read_fixed(self,path,maximum,*,owner=0,private=True):
        allowed=set(self.m.SOURCE_FILES)|{str(self.c.core.BASE),'/etc/systemd/system/easel-encrypted-backup.service',
            '/etc/systemd/system/easel-encrypted-backup.timer',str(self.m.WHEEL)}
        require(str(path)in allowed)
        return _read_file(path,maximum,owner=owner,private=private)
    def binding(self):
        m,c=self.m,self.c
        configs,schema,uid,gid,metadata=m.configurations(c.core,self)
        require((uid,gid)==(997,997))
        env={name:parse_environment(configs['configs'+name])for name in
             ('/etc/easel-central/central.env','/etc/easel-central/console.env','/root/easel-migration-20261024/logto-runtime.env')}
        require(schema==env['/etc/easel-central/central.env']['CENTRAL_DB_SCHEMA'])
        configuration_sha=sha(configs['configs/etc/easel-central/central.env'])
        require(self.schema is None or (self.schema,self.configuration_sha)==(schema,configuration_sha))
        self.schema,self.configuration_sha=schema,configuration_sha
        wheel=self.read_fixed(m.WHEEL,8*1024*1024)
        installed=Path(m.RELEASE)/'.venv/lib/python3.12/site-packages'
        migrations=m.verify_release_content(wheel,lambda name:_read_file(installed/name,8*1024*1024,private=False))
        processes=m.active_release_processes(self,configs);enriched={}
        for kind,row in processes.items():
            pid=row['pid'];base=Path('/proc')/str(pid)
            raw=m.proc_bytes(base/'stat');require(raw.split(b' ',1)[0]==str(pid).encode())
            fields=raw.rsplit(b')',1)[1].split();starttime=int(fields[19]);require(starttime>0)
            values=_properties(self.command(['/usr/bin/systemctl','show','--property=MainPID,ActiveState,InvocationID',m.UNITS[kind]]),{'MainPID','ActiveState','InvocationID'})
            require(values['MainPID']==str(pid)and values['ActiveState']=='active')
            running=_process_environment(m.proc_bytes(base/'environ'))
            private=env['/etc/easel-central/'+('central'if kind=='api'else 'console')+'.env']
            database_url=_runtime_database(kind,private,running,schema)
            _endpoint(database_url,c.core.SOURCES[0][3],self.broker.records[c.core.SOURCES[0][1]])
            namespace=os.readlink(base/'ns/mnt');mountinfo=m.proc_bytes(base/'mountinfo',512*1024)
            m.verify_mount_namespace(mountinfo,namespace,kind)
            require(sha(mountinfo)==row['mountinfo_sha256']and sha(namespace.encode())==row['mount_namespace_sha256'])
            exe=(base/'exe').resolve(strict=True)
            enriched[kind]={'pid':pid,'starttime':starttime,'invocation_id':values['InvocationID'],'cwd':m.RELEASE,
                'command_sha256':row['command_sha256'],'executable_sha256':sha(_read_file(exe,64*1024*1024,private=False)),
                'media_path':str(m.MEDIA)if kind=='api'else None,'mount_namespace':namespace,'mountinfo':mountinfo.decode()}
            require(m.proc_bytes(base/'stat').rsplit(b')',1)[1].split()[19]==str(starttime).encode())
        logto=self.broker.inspect('easel-logto-v2-20261024');runtime={}
        for item in logto['Config']['Env']:
            key,sep,value=item.partition('=');require(sep and key not in runtime);runtime[key]=value
        require(runtime.get('DB_URL')==env['/root/easel-migration-20261024/logto-runtime.env'].get('DB_URL')and runtime.get('DB_URL'))
        _endpoint(runtime['DB_URL'],c.core.SOURCES[1][3],self.broker.records[c.core.SOURCES[1][1]],caller=logto)
        production={}
        for name in CONTAINERS:
            self.broker.guard(name);r=self.broker.records[name]
            production[name]={'id':r['Id'],'image':r['Image'],'started_at':r['State']['StartedAt']}
        boot=uuid.UUID(_read_file('/proc/sys/kernel/random/boot_id',128,private=False).decode().strip()).hex
        return {'target':f1().TARGET,'boot_id':boot,'release':m.RELEASE,'candidate_sha256':m.WHEEL_SHA,
            'source_pins':dict(f1().SOURCE_PINS),'migrations':migrations,'central_schema_sha256':sha(schema.encode()),
            'central_config_sha256':configuration_sha,'configurations':{r['name'][len('configs'):]:{k:r[k]for k in ('sha256','uid','gid','mode','size')}for r in metadata},
            'source_processes':enriched,'host_surface':{'mount_namespace':os.readlink('/proc/self/ns/mnt'),
                'mountinfo':m.proc_bytes(Path('/proc/self/mountinfo'),512*1024).decode()},
            'production_containers':production,'original_services':c.core.service_state(self),
            'backup_definitions':c.core.backup_definition_hashes(self)}
    def blobs(self,state,registry):
        e=state['pending_exception'];b=e['blob']
        missing={k:b[k]for k in ('storage_key','sha256','size','state','job_id','attempt_id')}
        missing.update(binding_hash=e['job']['binding_hash'],http_reference_sha256=sha(f1().canonical(e['reference'])),staged_row_sha256=sha(f1().canonical(b)))
        self.m.validate_missing(missing)
        entries,inventory=self.m.capture_blobs(self.m.STORAGE,registry,997,997,missing)
        require(all(name.startswith('media/blobs/')and name.count('/')==2 for name in entries))
        return {name.split('/')[-1]:raw for name,raw in entries.items()},inventory,missing


class PinnedReadOnlyProducer:
    def __init__(self):
        self.broker=SourceBroker();self.c=contracts();self.facts=SourceFacts(self.broker);self.clock=SystemClock()
        self.initial_binding=self.facts.binding();self.schema=self.facts.schema;self.samples=[];self.expiries=[];self.graph_evidence=[]
        for kind in ('central','identity'):
            with self.broker.snapshot(kind,self.schema):pass
    def binding(self):return self.facts.binding()
    def command(self,argv,**kwargs):return self.broker.command(argv,**kwargs)
    @contextmanager
    def exported_snapshot(self):
        with self.broker.snapshot('central',self.schema,catalog=True)as token:yield token
    def sql(self,token,statement):return self.broker.sql('central',token,statement)
    def sample(self,token):
        sample=self.clock.sample(self.sql(token,capture_queries(self.schema)['clock']))
        require(sample['boot_id']==self.initial_binding['boot_id']);self.samples.append(sample);return sample
    def observations(self,token):
        q=capture_queries(self.schema);self.sample(token)
        graph=self.sql(token,q['graph']);wallet,pending,evidence=project_finance(graph)
        require(wallet==self.sql(token,q['wallet']));self.graph_evidence.append(evidence)
        state=self.sql(token,self.c.mixed.window_sql(self.c.core,self.schema))
        f1().validate_enabled_media_state(state,self.c)
        history=sorted([{'id':j['id'],'model':j['model'],'status':j['status']}for j in graph['media_jobs']],key=lambda row:row['id'])
        require(history==state['history'])
        media_pending=state['pending_exception']
        require(media_pending['job']==_index(graph['media_jobs'],('id',))[(media_pending['job']['id'],)]
                and media_pending['reservation']==_index(graph['credit_reservations'],('id',))[(media_pending['job']['reservation_id'],)]
                and media_pending['invocation']==_index(graph['media_invocations'],('id',))[(media_pending['job']['invocation_id'],)])
        registry=self.sql(token,self.c.mixed.media_rows_sql(self.c.core,self.schema))
        require(self.sql(token,self.c.mixed.migrations_sql(self.c.core,self.schema))==self.initial_binding['migrations'])
        files,inventory,missing=self.facts.blobs(state,registry)
        expiries=self.sql(token,q['expires']);require(type(expiries)is list and len(expiries)==5)
        self.expiries.extend(expiries);self.sample(token)
        return {'wallet':wallet,'text_pending':pending,'media_state':state,'registry':registry,
                'files':files,'media_inventory':inventory,'missing':missing}
    def start_clock(self):
        with self.exported_snapshot()as token:return self.sample(token)
    def end_clock(self):
        with self.exported_snapshot()as token:
            expiries=self.sql(token,capture_queries(self.schema)['expires']);require(type(expiries)is list and len(expiries)==5)
            self.expiries.extend(expiries);return self.sample(token)
    def final_clock(self):
        # Final DB/expiry reads share one read-only snapshot. All keeper and
        # source/fact closing I/O precedes the actual local clock observation.
        with self.exported_snapshot()as token:
            q=capture_queries(self.schema)
            expiries=self.sql(token,q['expires']);require(type(expiries)is list and len(expiries)==5)
            db=self.sql(token,q['clock'])
        require(self.binding()==self.initial_binding)
        final=self.clock.sample(db);require(final['boot_id']==self.initial_binding['boot_id'])
        return final,expiries  # Never append to the persisted receipt's samples.


def _final_admission(first,previous,current,expires,expiries):
    require(type(current)is dict and set(current)=={'utc','monotonic_ns','db_utc','transaction_utc','boot_id','raw_boot_id'}
            and uuid.UUID(current['raw_boot_id']).hex==current['boot_id']
            and _utc(current['transaction_utc'])<=_utc(current['db_utc']))
    clock_window(first,current,expiries);clock_window(previous,current,expiries)
    require(_utc(current['utc'])<_utc(expires))
    # Derived completion upper bound, not a new observed database timestamp.
    upper=max(_utc(current['utc']),_utc(current['db_utc']))+timedelta(seconds=5)
    require(all((_utc(value)-upper).total_seconds()>1800 for value in expiries))


def _save_document(path,value):
    raw=f1().canonical(value);parent=f1()._private_directory(path.parent)
    try:
        fd=os.open(path.name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=parent)
        try:
            view=memoryview(raw)
            while view:
                count=os.write(fd,view);require(count>0);view=view[count:]
            os.fchmod(fd,0o600);os.fsync(fd)
        finally:os.close(fd)
        os.fsync(parent)
    finally:os.close(parent)
    require(_read_file(path,4*1024*1024)==raw)
    return sha(raw)


def _load_document(path,expected):
    require(path.parent.parent==ROOT and re.fullmatch('[a-f0-9]{32}',path.parent.name)is not None)
    raw=_read_file(path,4*1024*1024);require(sha(raw)==expected);return f1().decode_canonical(raw)


def _receipt(receipt,freeze,expected,expiries):
    require(type(receipt)is dict and set(receipt)=={'format','producer_sha256','f1_sha256','window_id','freeze_sha256',
        'binding_sha256','created_utc','expires_utc','clock_samples','graph_evidence','approved','actions','source_pins'})
    require(receipt['format']=='easel-media-capture-receipt-v1'and receipt['producer_sha256']==expected
            and receipt['f1_sha256']==F1_SHA and receipt['source_pins']==f1().SOURCE_PINS
            and receipt['approved']is False and receipt['actions']==[]
            and receipt['freeze_sha256']==sha(f1().canonical(freeze))and receipt['window_id']==freeze['window_id']
            and receipt['binding_sha256']==sha(f1().canonical(freeze['binding']))
            and receipt['created_utc']==freeze['created_utc']and receipt['expires_utc']==freeze['expires_utc'])
    samples=receipt['clock_samples'];require(type(samples)is list and 5<=len(samples)<=16)
    for index,s in enumerate(samples):
        require(type(s)is dict and set(s)=={'utc','monotonic_ns','db_utc','transaction_utc','boot_id','raw_boot_id'}
                and uuid.UUID(s['raw_boot_id']).hex==s['boot_id']==freeze['binding']['boot_id'])
        require(_utc(s['transaction_utc'])<=_utc(s['db_utc']))
        clock_window(samples[0],s,expiries)
        if index:clock_window(samples[index-1],s,expiries)
    require((receipt['created_utc'],receipt['expires_utc'])==clock_window(samples[0],samples[-1],expiries))
    evidence=receipt['graph_evidence'];require(type(evidence)is list and len(evidence)==2)
    for row in evidence:
        require(type(row)is dict and set(row)=={'active','waived','graph_sha256'}and type(row['active'])is int and row['active']==0)
        _integer(row['waived']);require(re.fullmatch('[a-f0-9]{64}',row['graph_sha256'])is not None)


def capture_server(*,window_id,expected_producer_sha256):
    try:
        require(type(window_id)is str and re.fullmatch('[a-f0-9]{32}',window_id)is not None)
        _platform_gate(expected_producer_sha256);producer=PinnedReadOnlyProducer()
        directory=ROOT/window_id;require(not directory.exists()and not directory.is_symlink());directory.mkdir(mode=0o700)
        first=producer.start_clock();created=_utc(first['utc']);expires=created+timedelta(seconds=900)
        freeze=f1().capture_freeze(producer,contracts(),window_id=window_id,created_utc=created.isoformat(),expires_utc=expires.isoformat())
        require(producer.binding()==freeze['binding'])
        saved=f1().save_private_freeze(directory,freeze)
        last=producer.end_clock();require(_utc(last['utc'])<expires)
        require(clock_window(first,last,producer.expiries)==(created.isoformat(),expires.isoformat()))
        for index,sample in enumerate(producer.samples):
            clock_window(first,sample,producer.expiries)
            if index:clock_window(producer.samples[index-1],sample,producer.expiries)
        receipt={'format':'easel-media-capture-receipt-v1','producer_sha256':expected_producer_sha256,'f1_sha256':F1_SHA,
            'window_id':window_id,'freeze_sha256':saved['sha256'],'binding_sha256':sha(f1().canonical(freeze['binding'])),
            'created_utc':freeze['created_utc'],'expires_utc':freeze['expires_utc'],'clock_samples':producer.samples,
            'graph_evidence':producer.graph_evidence,'approved':False,'actions':[],'source_pins':dict(f1().SOURCE_PINS)}
        _receipt(receipt,freeze,expected_producer_sha256,producer.expiries)
        path=directory/'producer-private.json';digest=_save_document(path,receipt)
        f1().load_private_freeze(saved['path'],saved['sha256'],contracts())
        final,expiries=producer.final_clock()
        _final_admission(first,producer.samples[-1],final,freeze['expires_utc'],expiries)
        return {'status':'captured_unapproved','freeze_path':saved['path'],'freeze_sha256':saved['sha256'],
                'producer_receipt_path':str(path),'producer_receipt_sha256':digest,'safe':saved['safe'],'approved':False,'actions':[]}
    except BaseException:raise Rejected('capture_rejected') from None


def validate_capture_freshness(freeze_path,freeze_sha256,receipt_path,receipt_sha256,*,expected_producer_sha256):
    try:
        _platform_gate(expected_producer_sha256);path=Path(receipt_path);receipt=_load_document(path,receipt_sha256)
        require(Path(freeze_path).parent==path.parent)
        freeze=f1().load_private_freeze(freeze_path,freeze_sha256,contracts())
        require(freeze['window_id']==path.parent.name)
        producer=PinnedReadOnlyProducer();require(producer.binding()==freeze['binding'])
        now,expiries=producer.final_clock()
        _receipt(receipt,freeze,expected_producer_sha256,expiries)
        _final_admission(receipt['clock_samples'][0],receipt['clock_samples'][-1],now,freeze['expires_utc'],expiries)
        return {'status':'fresh_capture_unapproved','approved':False,'actions':[],'freeze_sha256':freeze_sha256}
    except BaseException:raise Rejected('capture_rejected') from None


def main():
    import argparse
    os.umask(0o077);resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    def interrupted(*_):raise Rejected('capture_rejected')
    signal.signal(signal.SIGTERM,interrupted);signal.signal(signal.SIGHUP,interrupted)
    parser=argparse.ArgumentParser();parser.add_argument('--window-id',required=True);parser.add_argument('--expected-producer-sha256',required=True)
    args=parser.parse_args();print(json.dumps(capture_server(window_id=args.window_id,expected_producer_sha256=args.expected_producer_sha256),sort_keys=True))


if __name__=='__main__':
    try:main()
    except BaseException:
        print('easel read-only capture rejected; inspect server-private records',file=sys.stderr);sys.exit(1)
