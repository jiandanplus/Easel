"""Exclusive-window producer. No import-time IO, restore, migration or service action."""
from dataclasses import dataclass,field
import hashlib
import os
import re
import signal
import stat
import subprocess
import tempfile
import time
import threading
import io
from functools import wraps
from media_backup.contract import canonical,validate_plan,registry_digest,physical_size,encode_manifest
from .contracts import B2Error,CapturedSource,require
from .profiles import SourceProfile,IdentityProfile,validate_inventory,validate_identity_inventory,validate_profile_catalog
from .bundles import (ConfigInputs,encode_bundle,item_metadata,strict_json,canonical_bytes,
                      MAX_BUNDLES_BYTES,_stamp)
from .source_facts import encode_source_facts,derive_registry,verify_archived_bundles
from .maintenance_snapshot import make_snapshot, read_control_rows, verify_progress

_IDENT=re.compile(r'[a-z_][a-z0-9_]{0,62}\Z',re.ASCII)
_SETVAL=re.compile(rb"^SELECT pg_catalog\.setval\('([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)', (-?[0-9]+), (true|false)\);$",re.M)
MAX_SCHEMA=8*1024**2
MAX_DUMP_DATA=16*1024**2
_SCHEMA_RESTRICT_KEY='easelB2SchemaComparison20261030'

def _identifier(value):require(type(value) is str and _IDENT.fullmatch(value));return value

def _sanitized(function):
    @wraps(function)
    def run(*args,**kwargs):
        try:return function(*args,**kwargs)
        except B2Error:raise
        except Exception:raise B2Error() from None
    return run

def _remaining(deadline):
    value=(deadline-time.monotonic_ns())/1e9;require(value>0);return value

def parse_dump_sequence_values(raw):
    require(type(raw) is bytes and len(raw)<=MAX_SCHEMA)
    matches=_SETVAL.findall(raw)
    require(raw.count(b'pg_catalog.setval(')==len(matches))
    result=[{'schema':s.decode(),'name':n.decode(),'last_value':int(v),'is_called':b==b'true'} for s,n,v,b in matches]
    require(len({(r['schema'],r['name']) for r in result})==len(result))
    return sorted(result,key=lambda row:(row['schema'],row['name']))

def compare_sequences(before,dump,after):
    require(canonical_bytes(before)==canonical_bytes(dump)==canonical_bytes(after))

def verify_dump_schema(actual,expected):
    require(type(actual) is bytes and type(expected) is bytes and 0<len(actual)<=MAX_SCHEMA and len(expected)<=MAX_SCHEMA)
    require(actual==expected)
    return {'sha256':hashlib.sha256(actual).hexdigest(),'size':len(actual)}

def _copy_digest(columns,lines):
    # COPY text escapes newlines within values; each physical LF ends one row.
    hashes=[]
    for line in lines:
        require(len(hashes)<100000);hashes.append(hashlib.sha256(line).hexdigest())
    hashes.sort()
    return {'columns':columns,'count':len(hashes),'rows_sha256':hashlib.sha256(canonical_bytes(hashes)).hexdigest()}

def parse_dump_copy_inventory(raw):
    require(type(raw) is bytes and len(raw)<=MAX_DUMP_DATA)
    result={};active=None;lines=[];columns=None
    for line in io.BytesIO(raw):
        if active is not None:
            if line==b'\\.\n':result[active]=_copy_digest(columns,lines);active=None;lines=[]
            else:require(line.endswith(b'\n') and len(lines)<100000);lines.append(line)
            continue
        if line.startswith(b'COPY '):
            match=re.fullmatch(rb'COPY ([a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*) \(([a-z_][a-z0-9_]*(?:, [a-z_][a-z0-9_]*)*)\) FROM stdin;\n',line)
            require(match is not None);active=match[1].decode();require(active not in result)
            columns=match[2].decode().split(', ')
    require(active is None)
    return result

def snapshot_copy_inventory(connection):
    tables=_fetch(connection,"SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r' ORDER BY 1,2")
    result={};total=0
    for schema,table in tables:
        _identifier(schema);_identifier(table)
        columns=[r[0] for r in _fetch(connection,"SELECT attname FROM pg_attribute WHERE attrelid=%s::regclass AND attnum>0 AND NOT attisdropped AND attgenerated='' ORDER BY attnum",(schema+'.'+table,))]
        for name in columns:_identifier(name)
        command='COPY "'+schema+'"."'+table+'" ('+', '.join('"'+n+'"' for n in columns)+') TO STDOUT'
        data=bytearray()
        with connection.cursor().copy(command) as copy:
            for block in copy:
                total+=len(block);require(total<=MAX_DUMP_DATA);data.extend(block)
        result[schema+'.'+table]=_copy_digest(columns,io.BytesIO(data))
    return result

class ChildRunner:
    """Pinned executable prefixes only; private libpq environment, no shell.

    The optional prefixes support the authorized disposable-container harness.
    Caller supplies trusted executable paths, never archived command values.
    All child sessions have a retained *started* reference until wait completes.
    """
    def __init__(self,pg_dump,pg_restore):
        self.tools={'pg_dump':tuple(pg_dump),'pg_restore':tuple(pg_restore)}
        require(all(v and all(type(s) is str and s and '\x00' not in s for s in v) for v in self.tools.values()))
        self.children=set()
        self.unknown_refs=[]
    def run(self,tool,args,*,exclusive,deadline,environment=None,input_fd=None,output_fd=None,maximum=MAX_SCHEMA):
        require(tool in self.tools and type(args) is list and len(args)<64)
        require(all(type(a) is str and '\x00' not in a and len(a)<1024 for a in args))
        exclusive.verify(exclusive.binding)
        ref=exclusive.retain();child=None;remote_ref=None;terminal=False
        with ref.start():
            try:
                if len(self.tools[tool])>1:
                    # Killing a container/remote CLI does not prove its actual
                    # remote tool died. Keep EX physically retained on unknown.
                    remote_ref=exclusive.retain();remote_ref.start()
                env={'PATH':'/usr/bin:/bin:/usr/local/bin','LC_ALL':'C','LANG':'C'}
                if environment is not None:
                    require(type(environment) is dict and environment.keys()<={'PGHOST','PGPORT','PGDATABASE','PGUSER','PGPASSWORD','PGSSLMODE','PGCONNECT_TIMEOUT'})
                    env.update(environment)
                with tempfile.TemporaryFile() as err,tempfile.TemporaryFile() as out:
                    if input_fd is not None:os.lseek(input_fd,0,os.SEEK_SET)
                    child=subprocess.Popen([*self.tools[tool],*args],stdin=input_fd if input_fd is not None else subprocess.DEVNULL,
                        stdout=output_fd if output_fd is not None else out,stderr=err,env=env,start_new_session=True,close_fds=True)
                    self.children.add(child)
                    try:
                        while child.poll() is None:
                            _remaining(deadline)
                            require(os.fstat(err.fileno()).st_size<=65536)
                            require(os.fstat(output_fd if output_fd is not None else out.fileno()).st_size<=maximum)
                            time.sleep(min(.01,_remaining(deadline)))
                        # A transport's nonzero exit does not prove remote work
                        # ended. Retain its real started EX ref on uncertainty.
                        terminal=child.returncode==0
                        require(child.returncode==0 and os.fstat(err.fileno()).st_size<=65536)
                        target=output_fd if output_fd is not None else out.fileno()
                        require(os.fstat(target).st_size<=maximum)
                        exclusive.verify(exclusive.binding);_remaining(deadline)
                        return None if output_fd is not None else os.pread(target,maximum+1,0)
                    finally:
                        if child.poll() is None:
                            os.killpg(child.pid,signal.SIGTERM)
                            try:child.wait(timeout=.25)
                            except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait(timeout=2)
                        self.children.discard(child)
            except (OSError,ValueError,TypeError,subprocess.SubprocessError):raise B2Error() from None
            finally:
                if remote_ref is not None:
                    if terminal or child is None:remote_ref.close()
                    else:self.unknown_refs.append(remote_ref)

def _file_record(name,fd):
    before=os.fstat(fd);require(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and stat.S_IMODE(before.st_mode)==0o600)
    require(before.st_size<=4*1024**3);digest=hashlib.sha256();offset=0
    while offset<before.st_size:
        raw=os.pread(fd,min(1024**2,before.st_size-offset),offset);require(raw);digest.update(raw);offset+=len(raw)
    require(not os.pread(fd,1,offset) and _stamp(os.fstat(fd))==_stamp(before))
    return {'name':name,'size':offset,'sha256':digest.hexdigest(),'uid':before.st_uid,'gid':before.st_gid,'mode':0o600}

def _physical(media_fd,owner,deadline):
    before=_stamp(os.fstat(media_fd));info=os.fstat(media_fd)
    require(stat.S_ISDIR(info.st_mode) and (info.st_uid,info.st_gid,stat.S_IMODE(info.st_mode))==(*owner,0o700))
    result=[]
    for name in _source_names(media_fd):
        _remaining(deadline);require(re.fullmatch('[a-f0-9]{32}|[a-f0-9]{64}',name,re.ASCII))
        require(len(result)<100000)
        fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=media_fd)
        try:
            stamp=_stamp(os.fstat(fd));row=_file_record(name,fd)
            require(_stamp(os.stat(name,dir_fd=media_fd,follow_symlinks=False))==stamp)
            require((row['uid'],row['gid'])==owner)
            result.append({'key':row.pop('name'),**row})
        finally:os.close(fd)
    require(_stamp(os.fstat(media_fd))==before)
    return result

def _source_names(media_fd):
    names=[]
    with os.scandir(media_fd) as entries:
        for entry in entries:
            require(len(names)<100000 and re.fullmatch('[a-f0-9]{32}|[a-f0-9]{64}',entry.name,re.ASCII))
            names.append(entry.name)
    return sorted(names)

def _physical_stamps(media_fd):
    return {'directory':list(_stamp(os.fstat(media_fd))),
            'files':{name:list(_stamp(os.stat(name,dir_fd=media_fd,follow_symlinks=False))) for name in _source_names(media_fd)}}

_JOB_NAMES={'db/central.dump':'central.dump','db/identity.dump':'identity.dump',
    'db/central.roles':'central.roles','db/identity.roles':'identity.roles',
    'config/central':'central.config','config/identity':'identity.config','config/media':'media.config'}

@_sanitized
def fixed_identity_inventory(job_fd,fds):
    require(type(fds) is dict and fds.keys()<=_JOB_NAMES.keys())
    result={}
    for name,fd in fds.items():
        before=_stamp(os.fstat(fd));require(_stamp(os.stat(_JOB_NAMES[name],dir_fd=job_fd,follow_symlinks=False))==before)
        result[name]=list(before)
    return result

def verify_fixed_identities(job_fd,fds,expected):require(fixed_identity_inventory(job_fd,fds)==expected)

def _fetch(connection,query,params=None):return connection.execute(query,params).fetchall()

class _BudgetConnection:
    """Remaining-budget timeout before every blocking libpq statement."""
    def __init__(self,connection,deadline):self.raw=connection;self.deadline=deadline
    def _budget(self):
        milliseconds=int(_remaining(self.deadline)*1000)-10;require(milliseconds>0)
        self.raw.execute("SELECT set_config('statement_timeout',%s,false)",(str(min(milliseconds,5000)),))
        self.raw.execute("SELECT set_config('lock_timeout',%s,false)",(str(min(milliseconds,1000)),))
    def execute(self,*args,**kwargs):self._budget();return self.raw.execute(*args,**kwargs)
    def cursor(self,*args,**kwargs):self._budget();return self.raw.cursor(*args,**kwargs)
    def close(self):self.raw.close()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()

def _schema(connection,profile):
    tables=[r[0] for r in _fetch(connection,"SELECT c.relname FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p','f','m') ORDER BY c.relname")]
    enums=[[r[0],r[1]] for r in _fetch(connection,"SELECT t.typname,array_agg(e.enumlabel ORDER BY e.enumsortorder) FROM pg_type t JOIN pg_enum e ON e.enumtypid=t.oid JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname='public' GROUP BY t.typname ORDER BY t.typname")]
    if type(profile) is IdentityProfile:validate_identity_inventory(profile,tables,enums)
    else:
        migrations=[tuple(r) for r in _fetch(connection,'SELECT version,checksum FROM public.schema_migrations ORDER BY version')]
        validate_inventory(profile,migrations,tables)
    # Bind every actual column, default, constraint, function and ACL in approved
    # schemas; unknown application schemas are rejected rather than omitted.
    schemas=[r[0] for r in _fetch(connection,"SELECT nspname FROM pg_catalog.pg_namespace WHERE nspname NOT LIKE 'pg_%' AND nspname<>'information_schema' ORDER BY nspname")]
    require(set(schemas)<={'public','easel_backup_control'})
    rows=_fetch(connection,"""SELECT n.nspname,c.relname,c.relkind,c.relowner::regrole::text,c.relacl::text,
       a.attnum,a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod),a.attnotnull,a.attidentity,
       pg_catalog.pg_get_expr(d.adbin,d.adrelid),c.relrowsecurity,c.relforcerowsecurity,
       a.attcollation::regcollation::text,a.attstorage,a.attcompression,a.attgenerated
       FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
       LEFT JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
       LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
       WHERE n.nspname IN ('public','easel_backup_control') AND c.relkind IN ('r','p','S','v','m','f')
       ORDER BY n.nspname,c.relname,a.attnum""")
    constraints=_fetch(connection,"SELECT n.nspname,c.relname,x.conname,pg_get_constraintdef(x.oid) FROM pg_constraint x JOIN pg_class c ON c.oid=x.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('public','easel_backup_control') ORDER BY 1,2,3")
    functions=_fetch(connection,"SELECT n.nspname,p.proname,pg_get_function_identity_arguments(p.oid),pg_get_functiondef(p.oid),p.proowner::regrole::text,p.proacl::text FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname IN ('public','easel_backup_control') ORDER BY 1,2,3")
    triggers=_fetch(connection,"SELECT n.nspname,c.relname,t.tgname,pg_get_triggerdef(t.oid),t.tgenabled FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('public','easel_backup_control') AND NOT t.tgisinternal ORDER BY 1,2,3")
    indexes=_fetch(connection,"SELECT schemaname,tablename,indexname,indexdef FROM pg_indexes WHERE schemaname IN ('public','easel_backup_control') ORDER BY 1,2,3")
    policies=_fetch(connection,"SELECT schemaname,tablename,policyname,permissive,roles,cmd,qual,with_check FROM pg_policies WHERE schemaname IN ('public','easel_backup_control') ORDER BY 1,2,3")
    namespaces=_fetch(connection,"SELECT nspname,pg_get_userbyid(nspowner),nspacl::text FROM pg_namespace WHERE nspname IN ('public','easel_backup_control') ORDER BY 1")
    defaults=_fetch(connection,"SELECT coalesce(n.nspname,''),pg_get_userbyid(a.defaclrole),a.defaclobjtype,a.defaclacl::text FROM pg_default_acl a LEFT JOIN pg_namespace n ON n.oid=a.defaclnamespace ORDER BY 1,2,3")
    database=_fetch(connection,"SELECT datname,pg_get_userbyid(datdba),datacl::text,datconnlimit,datistemplate,datallowconn FROM pg_database WHERE datname=current_database()")
    types=_fetch(connection,"SELECT n.nspname,t.typname,t.typtype,pg_get_userbyid(t.typowner),t.typacl::text,t.typdefault FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname IN ('public','easel_backup_control') ORDER BY 1,2")
    relation_options=_fetch(connection,"SELECT n.nspname,c.relname,c.relpersistence,c.relreplident,c.relispartition,a.amname,t.spcname,c.reloptions,pg_get_partkeydef(c.oid),pg_get_expr(c.relpartbound,c.oid) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_am a ON a.oid=c.relam LEFT JOIN pg_tablespace t ON t.oid=c.reltablespace WHERE n.nspname IN ('public','easel_backup_control') AND c.relkind IN ('r','p','S','v','m','f') ORDER BY 1,2")
    sequences=_fetch(connection,"SELECT n.nspname,c.relname,format_type(s.seqtypid,NULL),s.seqstart,s.seqincrement,s.seqmin,s.seqmax,s.seqcache,s.seqcycle,nt.nspname,t.relname,a.attname,d.deptype FROM pg_sequence s JOIN pg_class c ON c.oid=s.seqrelid JOIN pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_depend d ON d.objid=c.oid AND d.classid='pg_class'::regclass AND d.deptype IN ('a','i') LEFT JOIN pg_class t ON t.oid=d.refobjid LEFT JOIN pg_namespace nt ON nt.oid=t.relnamespace LEFT JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=d.refobjsubid WHERE n.nspname IN ('public','easel_backup_control') ORDER BY 1,2")
    rules=_fetch(connection,"SELECT schemaname,tablename,rulename,definition FROM pg_rules WHERE schemaname IN ('public','easel_backup_control') ORDER BY 1,2,3")
    operators=_fetch(connection,"SELECT n.nspname,o.oprname,o.oprleft::regtype::text,o.oprright::regtype::text,o.oprcode::regprocedure::text FROM pg_operator o JOIN pg_namespace n ON n.oid=o.oprnamespace WHERE n.nspname IN ('public','easel_backup_control') ORDER BY 1,2,3,4")
    collations=_fetch(connection,"SELECT n.nspname,c.collname,c.collprovider,c.collisdeterministic,c.collencoding,c.collcollate,c.collctype,c.colliculocale,c.collversion FROM pg_collation c JOIN pg_namespace n ON n.oid=c.collnamespace WHERE n.nspname IN ('public','easel_backup_control') ORDER BY 1,2")
    casts=_fetch(connection,"SELECT s.typnamespace::regnamespace::text,s.typname,t.typnamespace::regnamespace::text,t.typname,c.castfunc::regprocedure::text,c.castcontext,c.castmethod FROM pg_cast c JOIN pg_type s ON s.oid=c.castsource JOIN pg_type t ON t.oid=c.casttarget LEFT JOIN pg_proc p ON p.oid=c.castfunc WHERE s.typnamespace IN (SELECT oid FROM pg_namespace WHERE nspname IN ('public','easel_backup_control')) OR t.typnamespace IN (SELECT oid FROM pg_namespace WHERE nspname IN ('public','easel_backup_control')) OR p.pronamespace IN (SELECT oid FROM pg_namespace WHERE nspname IN ('public','easel_backup_control')) ORDER BY 1,2,3,4")
    extensions=_fetch(connection,"SELECT e.extname,e.extversion,n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace ORDER BY 1")
    events=_fetch(connection,"SELECT evtname,evtevent,evtenabled,evtfoid::regprocedure::text,evttags FROM pg_event_trigger ORDER BY 1")
    return {'columns':[list(r) for r in rows],'constraints':[list(r) for r in constraints],'functions':[list(r) for r in functions],
            'enums':enums,'triggers':[list(r) for r in triggers],'indexes':[list(r) for r in indexes],'policies':[list(r) for r in policies],
            'namespaces':[list(r) for r in namespaces],'default_acls':[list(r) for r in defaults],
            'database':[list(r) for r in database],'types':[list(r) for r in types],'relation_options':[list(r) for r in relation_options],
            'sequence_definitions':[list(r) for r in sequences],'rules':[list(r) for r in rules],'operators':[list(r) for r in operators],
            'collations':[list(r) for r in collations],'casts':[list(r) for r in casts],'extensions':[list(r) for r in extensions],'event_triggers':[list(r) for r in events]}

read_source_catalog=_schema

def _raw_rows(connection,profile):
    result={};total=2+sum(len(name)+8 for name in profile.tables)
    raw_connection=getattr(connection,'raw',connection)
    if raw_connection.info.transaction_status==0:
        connection.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
    for table in profile.tables:
        _identifier(table)
        # Server-side JSON preserves database timestamp/numeric representation;
        # reject floats and bound each row before keeping it in memory.
        items=[]
        with connection.cursor(name='b2raw_'+hashlib.sha256(table.encode()).hexdigest()[:16]) as cursor:
            cursor.itersize=1
            cursor.execute('SELECT CASE WHEN octet_length(row_to_json(t)::text)<=%s THEN row_to_json(t)::text END FROM public."'+table+'" t ORDER BY row_to_json(t)::text',(8*1024**2,))
            for row in cursor:
                require(type(row[0]) is str)
                raw=row[0].encode();total+=len(raw)+1;require(total<=8*1024**2 and len(items)<100000)
                # row_to_json has spacing; parse then canonicalize strict types.
                import json
                value=json.loads(raw,parse_float=lambda _:(_ for _ in ()).throw(B2Error()),parse_constant=lambda _:(_ for _ in ()).throw(B2Error()))
                canonical_bytes(value);items.append(value)
        result[table]=items
    require(len(canonical_bytes(result))<=8*1024**2)
    return result

def sequence_inventory(connection,*,schemas=('public',)):
    require(type(schemas) is tuple and schemas and len(set(schemas))==len(schemas) and set(schemas)<={'public','easel_backup_control'})
    rows=_fetch(connection,"""SELECT n.nspname,c.relname,pg_get_userbyid(c.relowner),
       format_type(s.seqtypid,NULL),s.seqstart,s.seqincrement,s.seqmax,s.seqmin,s.seqcache,s.seqcycle,
       nt.nspname,t.relname,a.attname,d.deptype
       FROM pg_sequence s JOIN pg_class c ON c.oid=s.seqrelid JOIN pg_namespace n ON n.oid=c.relnamespace
       LEFT JOIN pg_depend d ON d.objid=c.oid AND d.classid='pg_class'::regclass AND d.deptype IN ('a','i')
       LEFT JOIN pg_class t ON t.oid=d.refobjid LEFT JOIN pg_namespace nt ON nt.oid=t.relnamespace
       LEFT JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=d.refobjsubid
       WHERE n.nspname=ANY(%s) ORDER BY 1,2""",(list(schemas),))
    result=[]
    names=('schema','name','owner','data_type','start','increment','max','min','cache','cycle','owned_schema','owned_table','owned_column','dependency')
    for row in rows:
        item=dict(zip(names,row));schema=_identifier(item['schema']);name=_identifier(item['name'])
        value=connection.execute('SELECT last_value,is_called FROM "'+schema+'"."'+name+'"').fetchone()
        item.update(last_value=value[0],is_called=value[1]);result.append(item)
    return result

def roles_inventory(connection,role_names,administrative_grantors=()):
    require(type(role_names) is tuple and role_names and len(set(role_names))==len(role_names))
    for name in role_names:_identifier(name)
    rows=_fetch(connection,"SELECT rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,rolreplication,rolconnlimit,rolvaliduntil::text,rolbypassrls,rolconfig FROM pg_roles WHERE rolname=ANY(%s) ORDER BY rolname",(list(role_names),))
    require({r[0] for r in rows}==set(role_names))
    members=_fetch(connection,"SELECT r.rolname,m.rolname,a.admin_option,g.rolname,a.inherit_option,a.set_option,a.xmin::text FROM pg_auth_members a JOIN pg_roles r ON r.oid=a.roleid JOIN pg_roles m ON m.oid=a.member JOIN pg_roles g ON g.oid=a.grantor WHERE r.rolname=ANY(%s) OR m.rolname=ANY(%s) ORDER BY 1,2,4",(list(role_names),list(role_names)))
    require(type(administrative_grantors) is tuple)
    for name in administrative_grantors:_identifier(name)
    require(all(r[0] in role_names and r[1] in role_names and r[3] in (*role_names,*administrative_grantors) for r in members))
    ownership=_fetch(connection,"SELECT n.nspname,c.relname,pg_get_userbyid(c.relowner),c.relacl::text FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('public','easel_backup_control') ORDER BY 1,2")
    require(all(r[2] in role_names for r in ownership))
    require(connection.execute('SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()').fetchone()[0] in role_names)
    acl_source="""SELECT n.nspname AS schema,c.relname AS name,'relation'::text AS kind,c.relacl AS acl FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('public','easel_backup_control')
      UNION ALL SELECT n.nspname,c.relname||'.'||a.attname,'column',a.attacl FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('public','easel_backup_control') AND a.attnum>0 AND NOT a.attisdropped
      UNION ALL SELECT n.nspname,p.proname||'('||pg_get_function_identity_arguments(p.oid)||')','function',p.proacl FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname IN ('public','easel_backup_control')
      UNION ALL SELECT n.nspname,t.typname,'type',t.typacl FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname IN ('public','easel_backup_control')
      UNION ALL SELECT nspname,nspname,'namespace',nspacl FROM pg_namespace WHERE nspname IN ('public','easel_backup_control')
      UNION ALL SELECT coalesce(n.nspname,''),pg_get_userbyid(a.defaclrole)||':'||a.defaclobjtype::text,'default',a.defaclacl FROM pg_default_acl a LEFT JOIN pg_namespace n ON n.oid=a.defaclnamespace
      UNION ALL SELECT '',datname,'database',datacl FROM pg_database WHERE datname=current_database()"""
    acl_rows=_fetch(connection,"SELECT a.schema,a.name,a.kind,pg_get_userbyid(x.grantor),CASE WHEN x.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(x.grantee) END,x.privilege_type,x.is_grantable FROM ("+acl_source+") a CROSS JOIN LATERAL aclexplode(a.acl) x ORDER BY 1,2,3,4,5,6")
    known=set(role_names)|{'PUBLIC','pg_database_owner'}
    for row in acl_rows:
        require(row[4] in known and row[3] in known|set(administrative_grantors))
    return {'roles':[list(r) for r in rows],'memberships':[list(r) for r in members],
            'ownership':[list(r) for r in ownership],'acls':[list(r) for r in acl_rows]}

def roles_export(inventory):
    """Versioned, non-executable complete trusted-role export including ACL facts."""
    return canonical_bytes({'format':'easel-roles-v1','inventory':inventory})

@_sanitized
def role_credential_inventory(connection,role_names):
    require(type(role_names) is tuple and role_names and len(set(role_names))==len(role_names))
    for name in role_names:_identifier(name)
    credentials=_fetch(connection,'SELECT rolname,rolpassword,xmin::text FROM pg_catalog.pg_authid WHERE rolname=ANY(%s) ORDER BY rolname',(list(role_names),))
    require({r[0] for r in credentials}==set(role_names))
    for _,password,version in credentials:
        require(password is None or (type(password) is str and len(password)<=1024 and password.startswith(('SCRAM-SHA-256$','md5'))))
        require(type(version) is str and version.isdigit())
    return [list(row) for row in credentials]

def keeper_identity(connection):
    return tuple(connection.execute("SELECT pg_backend_pid(),pg_current_snapshot()::text,current_setting('transaction_read_only'),current_setting('transaction_isolation'),transaction_timestamp()::text").fetchone())

def _dump_sequences(runner,fd,before,exclusive,deadline):
    ddl=runner.run('pg_restore',['--schema-only','--file=-'],exclusive=exclusive,deadline=deadline,input_fd=fd)
    text=ddl.decode('utf-8');result=[]
    for item in before:
        name=item['name'];schema=item['schema'];qualified=schema+'.'+name
        # Names are fixed safe identifiers, so dumped identifiers are unquoted.
        if item['dependency']=='i':
            marker='SEQUENCE NAME '+qualified+'\n'
            require(marker in text)
            start=text.index(marker);end=text.index(');',start);block=text[start:end]
            require('ALTER TABLE '+item['owned_schema']+'.'+item['owned_table']+' ALTER COLUMN '+item['owned_column']+' ADD GENERATED' in text[:start])
            # Identity owner follows the owning table's owner in pg_dump SQL.
            require('ALTER TABLE '+item['owned_schema']+'.'+item['owned_table']+' OWNER TO '+item['owner']+';' in text)
        else:
            marker='CREATE SEQUENCE '+qualified+'\n';require(marker in text)
            start=text.index(marker);end=text.index(';',start);block=text[start:end]
            require('ALTER SEQUENCE '+qualified+' OWNER TO '+item['owner']+';' in text)
            if item['owned_table'] is not None:
                require('ALTER SEQUENCE '+qualified+' OWNED BY '+item['owned_schema']+'.'+item['owned_table']+'.'+item['owned_column']+';' in text)
        for label,key in (('START WITH','start'),('INCREMENT BY','increment'),('CACHE','cache')):
            require(re.search(r'\b'+label+r' '+str(item[key])+r'\b',block))
        for key,label,default in (('min','MINVALUE',1 if item['increment']>0 else (-9223372036854775808 if item['data_type']=='bigint' else -2147483648)),
                                   ('max','MAXVALUE',9223372036854775807 if item['data_type']=='bigint' and item['increment']>0 else (2147483647 if item['increment']>0 else -1))):
            require((item[key]==default and 'NO '+label in block) or re.search(r'(?<!NO )\b'+label+r' '+str(item[key])+r'\b',block))
        require(('CYCLE' in block.replace('NO CYCLE',''))==item['cycle'])
        values=runner.run('pg_restore',['--data-only','--table='+name,'--file=-'],exclusive=exclusive,deadline=deadline,input_fd=fd)
        parsed=parse_dump_sequence_values(values);require(len(parsed)==1 and parsed[0]['schema']==schema and parsed[0]['name']==name)
        result.append({**item,'last_value':parsed[0]['last_value'],'is_called':parsed[0]['is_called']})
    # Every sequence in the full public business dump must be accounted for.
    require(text.count('CREATE SEQUENCE ')+text.count('    SEQUENCE NAME ')==len(before))
    return result

@dataclass(repr=False)
class OwnedInputs:
    central_dsn:str=field(repr=False)
    identity_dsn:str=field(repr=False)
    identity_profile:SourceProfile|IdentityProfile
    central_roles:tuple
    identity_roles:tuple
    media_fd:int
    job_fd:int
    configs:ConfigInputs
    owner:tuple
    binding:object
    expected_schema:dict
    reserve_bytes:int
    administrative_grantors:tuple=()
    role_admin_dsns:dict=field(default_factory=dict,repr=False)
    source_root:object=field(default=None,repr=False)
    private_owner:tuple=field(default_factory=lambda:(os.getuid(),os.getgid()))
    companion:object=field(default=None,init=False,repr=False)
    _capture:object=field(default=None,init=False,repr=False)
    _exclusive:object=field(default=None,init=False,repr=False)
    _before:object=field(default=None,init=False,repr=False)
    _fds:dict=field(default_factory=dict,init=False,repr=False)
    _keepers:dict=field(default_factory=dict,init=False,repr=False)
    _keeper_stamps:dict=field(default_factory=dict,init=False,repr=False)
    _keeper_snapshots:dict=field(default_factory=dict,init=False,repr=False)
    _deadline:int=field(default=0,init=False,repr=False)
    _preflight:object=field(default=None,init=False,repr=False)
    _work_lock:object=field(default_factory=threading.RLock,init=False,repr=False)
    _active_work:int=field(default=0,init=False,repr=False)
    _work_unknown:bool=field(default=False,init=False,repr=False)
    _owns_source:bool=field(default=False,init=False,repr=False)
    _locks:object=field(default=None,init=False,repr=False)
    _domain:dict=field(default_factory=dict,init=False,repr=False)
    _worker_registry:list=field(default_factory=list,init=False,repr=False)
    def bind_reconstruction(self,pair):
        from .reconstruction_capture import OwnedSourceCapture
        require(type(pair) is OwnedSourceCapture and pair._inputs is self and self.companion is None)
        require(self._preflight is None and self._exclusive is None and self._capture is None and self._active_work==0)
        self.companion=pair
        pair._verify_bindings()
    def _connect(self,kind):
        return self._connect_dsn(self.central_dsn if kind=='central' else self.identity_dsn)
    def _connect_dsn(self,dsn):
        require(not self._work_unknown)
        import psycopg
        from psycopg.conninfo import conninfo_to_dict
        ci=conninfo_to_dict(dsn)
        require(ci.get('host')=='127.0.0.1' and ci.get('hostaddr','127.0.0.1')=='127.0.0.1' and not any(k in ci for k in ('service','servicefile','passfile','options')))
        require(all(type(ci.get(key)) is str and ci[key] for key in ('dbname','port','user','password')))
        deadline=self._deadline or time.monotonic_ns()+30_000_000_000
        remaining=_remaining(deadline);require(remaining>=1)
        milliseconds=int(remaining*1000)-10;require(milliseconds>0)
        connection=psycopg.connect(dsn,
            hostaddr='127.0.0.1',connect_timeout=min(5,int(remaining)),
            options='-c statement_timeout='+str(min(milliseconds,5000))+' -c lock_timeout='+str(min(milliseconds,1000)),autocommit=True)
        return _BudgetConnection(connection,deadline)
    def _roles(self,kind,connection):
        """Explicit privileged owned-source input; never inspect other roles."""
        from psycopg.conninfo import conninfo_to_dict
        require(type(self.role_admin_dsns) is dict and self.role_admin_dsns.keys()=={'central','identity'})
        names=self.central_roles if kind=='central' else self.identity_roles
        actual=conninfo_to_dict(self.role_admin_dsns[kind]);source=conninfo_to_dict(self.central_dsn if kind=='central' else self.identity_dsn)
        require(all(actual.get(k)==source.get(k) for k in ('dbname','host','port')))
        result=roles_inventory(connection,names,self.administrative_grantors)
        with self._connect_dsn(self.role_admin_dsns[kind]) as admin:
            result['credentials']=role_credential_inventory(admin,names)
        return result
    def _workers(self,connection):
        from ..media_maintenance.filesystem import verify_domain_rows,domain_attestation
        require(self.source_root is not None and self._locks is not None)
        rows=[r[0] for r in _fetch(connection,"SELECT jsonb_build_object('worker_id',worker_id,'generation',generation,'identity',identity,'active',active,'domain',domain) FROM easel_backup_control.worker_domains WHERE active ORDER BY worker_id")]
        verify_domain_rows(rows,self._locks,self.source_root)
        require(domain_attestation(self._locks,self.source_root)==self._domain)
        return rows
    @_sanitized
    def preflight(self,profile,locks,*,host_identity):
        """Read-only pre-window definition; no fabricated WindowBinding needed."""
        from ..media_maintenance.filesystem import ControlLock,SourceRoot,filesystem_identity,domain_attestation
        from ..media_maintenance.identity import host_identity as actual_host_identity
        require(self.binding is None and self._capture is None and type(locks) is ControlLock)
        require(type(host_identity) is tuple and len(host_identity)==3 and all(type(v) is str and v for v in host_identity))
        require(host_identity==actual_host_identity())
        locks.verify();self.configs.verify()
        if self.source_root is None:self.source_root=SourceRoot(self.media_fd,owner=self.owner);self._owns_source=True
        require(type(self.source_root) is SourceRoot)
        self.source_root.verify();require(self.source_root.inode==(os.fstat(self.media_fd).st_dev,os.fstat(self.media_fd).st_ino))
        self._locks=locks;self._domain=domain_attestation(locks,self.source_root)
        for fd in (self.media_fd,self.job_fd,self.configs._root):
            identity=filesystem_identity(fd)
            require(identity[0]==locks.identity[0] and identity[3]==locks.identity[3] and identity[1] in {'apfs','ext4','xfs','btrfs'})
        require(type(self.private_owner) is tuple and len(self.private_owner)==2 and all(type(v) is int and v>=0 for v in self.private_owner))
        info=os.fstat(self.job_fd);require(stat.S_ISDIR(info.st_mode) and (info.st_uid,info.st_gid,stat.S_IMODE(info.st_mode))==(*self.private_owner,0o700))
        require(type(self.reserve_bytes) is int and self.reserve_bytes>0)
        capacity=os.fstatvfs(self.job_fd);require(capacity.f_bavail*capacity.f_frsize>=self.reserve_bytes)
        schemas={};database_bytes=0
        for kind,p in (('central',profile),('identity',self.identity_profile)):
            with self._connect(kind) as con:
                database_bytes+=con.execute('SELECT pg_database_size(current_database())').fetchone()[0]
                schemas[kind]=_schema(con,p);require(schemas[kind]==self.expected_schema[kind])
                validate_profile_catalog(p,schemas[kind])
                self._roles(kind,con)
                if kind=='central':
                    state=con.execute('SELECT state FROM easel_backup_control.control WHERE id=1').fetchone()
                    require(state==('OPEN',) and con.execute('SELECT count(*) FROM easel_backup_control.permits').fetchone()==(0,))
                    generation=con.execute('SELECT easel_backup_control.generation_digest()').fetchone()[0]
                    workers=self._workers(con)
                    require(all(r['identity']['host_id']==host_identity[0] and r['identity']['boot_id']==host_identity[1] for r in workers))
                    self._worker_registry=workers
                    rows=_raw_rows(con,profile)
        physical=_physical(self.media_fd,self.owner,time.monotonic_ns()+30_000_000_000);derive_registry(rows,physical,source_owner=self.owner)
        # Coexisting source dumps, encrypted archive/context, verification IO /
        # potential private stage, bounded facts/config copies and a fixed margin.
        config_bytes=sum(row['size'] for kind in ('central','identity','media') for row in self.configs.items(kind))
        required=3*database_bytes+3*sum(row['size'] for row in physical)+4*config_bytes+128*1024**2
        require(self.reserve_bytes>=required)
        self._preflight={'schemas':schemas,'media':_stamp(os.fstat(self.media_fd)),'job':_stamp(os.fstat(self.job_fd))}
        return dict(zip(('host_id','boot_id','process_start'),host_identity),control_dev=locks.inode[0],control_ino=locks.inode[1],
            source_dev=os.fstat(self.media_fd).st_dev,source_ino=os.fstat(self.media_fd).st_ino,generation_digest=generation,configuration_state={})
    @_sanitized
    def bind_window(self,exclusive,*,phase_deadline_ns):
        from ..media_maintenance.filesystem import ExclusiveWindow
        require(type(exclusive) is ExclusiveWindow and self.binding is None and self._exclusive is None and self._preflight is not None)
        binding=exclusive.binding;require(binding is not None)
        exclusive.verify(binding)
        require(type(phase_deadline_ns) is int and time.monotonic_ns()<phase_deadline_ns<=binding.deadline_ns)
        require(phase_deadline_ns-time.monotonic_ns()<=120_000_000_000)
        self.binding=binding;self._exclusive=exclusive;self._deadline=phase_deadline_ns
        return None
    @_sanitized
    def checkpoint(self):
        """Same live keeper transactions, actual EX, original phase deadline."""
        require(not self._work_unknown and self._exclusive is not None and self.binding is not None and self._keepers.keys()=={'central','identity'})
        self._exclusive.verify(self.binding);_remaining(self._deadline)
        self.source_root.verify()
        for kind,con in self._keepers.items():
            row=keeper_identity(con)
            require(row==self._keeper_stamps[kind] and row[2:4] == ('on','repeatable read'))
            # Original exported token must still import in a real fresh backend.
            # PID/snapshot/RO/isolation (even a timestamp) alone is not authority
            # for transaction identity after a keeper rollback/restart.
            from psycopg import sql
            with self._connect(kind) as observer:
                observer.raw.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
                observer.raw.execute(sql.SQL('SET TRANSACTION SNAPSHOT {}').format(sql.Literal(self._keeper_snapshots[kind])))
                require(observer.raw.execute('SELECT pg_current_snapshot()::text').fetchone()[0]==row[1])
        require(not self._work_unknown)
        self.source_root.verify()
        return {'keepers':2}
    def _control(self,connection,allowed):
        b=self.binding
        row=connection.execute('SELECT state,epoch,window_id::text,owner_hash,binding FROM easel_backup_control.control WHERE id=1').fetchone()
        require(row is not None and row[0] in allowed and row[1]==b.epoch and row[2]==b.window_id and row[3]==hashlib.sha256(b.owner_nonce).hexdigest())
        expected={k:getattr(b,k) for k in ('host_id','boot_id','process_start','control_dev','control_ino','source_dev','source_ino','generation_digest','deadline_ns')}
        require(row[4]==expected)
        require(connection.execute('SELECT easel_backup_control.generation_digest()').fetchone()[0]==b.generation_digest)
        require(connection.execute('SELECT count(*) FROM easel_backup_control.permits').fetchone()[0]==0)
        workers=self._workers(connection)
        require(workers==self._worker_registry)
        require(all(r['identity']['host_id']==b.host_id and r['identity']['boot_id']==b.boot_id for r in workers))
        require(os.getpid()>0 and time.monotonic_ns()<(self._deadline or b.deadline_ns))
    @_sanitized
    def verify_after(self,captured):
        require(captured is self._capture and self._exclusive is not None)
        require(captured.fixed_fds==self._fds and canonical_bytes(captured.plan)==self._before['plan'])
        require(captured.window==self.binding and captured.media_fd==self.media_fd and captured.owner==self.owner)
        self.checkpoint()
        self.configs.verify()
        verify_fixed_identities(self.job_fd,captured.fixed_fds,self._before['fixed_stamps'])
        require(_physical(self.media_fd,self.owner,self._deadline)==self._before['physical'])
        require(_physical_stamps(self.media_fd)==self._before['physical_stamps'])
        for kind in ('central','identity'):
            with self._connect(kind) as con:
                if kind=='central':
                    con.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
                    self._control(con,{'CAPTURING','VERIFYING'})
                    control_sequences=sequence_inventory(con,schemas=('easel_backup_control',))
                    control_rows=read_control_rows(con)
                    require(sequence_inventory(con,schemas=('easel_backup_control',))==control_sequences)
                    current={**self._before['maintenance_snapshot'],'tables':control_rows,'sequences':control_sequences}
                    verify_progress(self._before['maintenance_snapshot'],current)
                require(sequence_inventory(con)==self._before['sequences'][kind])
                require(self._roles(kind,con)==self._before['roles'][kind])
                profile=self._before['profiles'][kind]
                require(_schema(con,profile)==self._before['schemas'][kind])
                if kind=='central':require(_protected(_raw_rows(con,profile))==self._before['protected'])
        require([_file_record(n,fd) for n,fd in sorted(captured.fixed_fds.items())]==captured.plan['files'])
        self.checkpoint()
        return {'source_identity_sha256':hashlib.sha256(canonical_bytes(self._before['schemas'])).hexdigest(),
                'stable_side_effects_sha256':hashlib.sha256(canonical_bytes(self._before['protected'])).hexdigest()}
    def close(self):
        with self._work_lock:require(self._active_work==0)
        if self.companion is not None:self.companion._require_read_terminal()
        for con in self._keepers.values():con.close()
        self._keepers={};self._keeper_stamps={};self._keeper_snapshots={}
        if self._owns_source and self.source_root is not None:self.source_root.close();self.source_root=None;self._owns_source=False
        for fd in self._fds.values():os.close(fd)
        self._fds={}

def _protected(rows):
    result={name:value for name,value in rows.items() if name.startswith('media_')}
    reservations={j['reservation_id'] for j in rows['media_jobs']}
    result['credit_reservations']=[r for r in rows['credit_reservations'] if r['id'] in reservations or r['operation']=='media']
    result['credit_holds']=[r for r in rows['credit_holds'] if r['reservation_id'] in reservations]
    # Journal/entries are immutable; bind exact media reservation sources only,
    # not unrelated later text balance/ledger activity.
    result['credit_journals']=[r for r in rows['credit_journals'] if r['source'] in reservations]
    journal_ids={r['id'] for r in result['credit_journals']}
    result['credit_entries']=[r for r in rows['credit_entries'] if r['journal_id'] in journal_ids]
    return result

def _capture_source_sync(profile,exclusive,owned_inputs,child_runner):
    """Borrow caller FDs; keep actual independent read-only snapshot keepers live.

    The identity DB needs its own explicit profile. This implementation ships
    central profiles only; a deployed Logto catalog is a separate pinned gate.
    """
    from ..media_maintenance.filesystem import ExclusiveWindow
    require(type(exclusive) is ExclusiveWindow and type(owned_inputs) is OwnedInputs and type(child_runner) is ChildRunner)
    o=owned_inputs;keepers=[];fds={};success=False
    try:
        require(o._capture is None and exclusive.binding is not None and type(o.configs) is ConfigInputs)
        require(o.binding==exclusive.binding and o._exclusive is exclusive and o._preflight is not None)
        b=o.binding;deadline=o._deadline
        exclusive.verify(b);_remaining(deadline)
        if o.companion is not None:o.companion._bind_window(exclusive)
        require((os.fstat(o.media_fd).st_dev,os.fstat(o.media_fd).st_ino)==(b.source_dev,b.source_ino))
        info=os.fstat(o.job_fd);require(stat.S_ISDIR(info.st_mode) and (info.st_uid,info.st_gid,stat.S_IMODE(info.st_mode))==(*o.private_owner,0o700))
        require(type(o.reserve_bytes) is int and o.reserve_bytes>0)
        capacity=os.fstatvfs(o.job_fd);require(capacity.f_bavail*capacity.f_frsize>=o.reserve_bytes)
        o.configs.verify();require('maintenance' in {r['name'] for r in o.configs.items('central')})
        for tool in ('pg_dump','pg_restore'):
            version=child_runner.run(tool,['--version'],exclusive=exclusive,deadline=deadline)
            require(re.fullmatch(rb'pg_(?:dump|restore) \(PostgreSQL\) 16\.[0-9]+[^\n]*\n',version))
        profiles={'central':profile,'identity':o.identity_profile};snapshots={};schemas={};before_seq={};before_roles={};rows=None;projections={};dump_schema={};maintenance_rows=None;maintenance_sequences=None;maintenance_snapshot=None
        for kind,p in profiles.items():
            con=o._connect(kind);keepers.append(con)
            require(con.execute('SHOW server_version_num').fetchone()[0].startswith('16'))
            if kind=='central':o._control(con,{'CAPTURING'})
            schemas[kind]=_schema(con,p)
            require(schemas[kind]==o.expected_schema[kind])
            validate_profile_catalog(p,schemas[kind])
            if o._preflight is not None:require(schemas[kind]==o._preflight['schemas'][kind])
            before_seq[kind]=sequence_inventory(con)
            if o.companion is not None:o.companion._before(kind,con)
            before_roles[kind]=o._roles(kind,con)
            con.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
            snapshots[kind]=con.execute('SELECT pg_export_snapshot()').fetchone()[0]
            o._keepers[kind]=con
            o._keeper_snapshots[kind]=snapshots[kind]
            o._keeper_stamps[kind]=keeper_identity(con)
            # The authoritative catalog must be read inside that same exported
            # transaction. A pre-BEGIN sample is only preflight, not snapshot proof.
            schemas[kind]=_schema(con,p);require(schemas[kind]==o.expected_schema[kind]);validate_profile_catalog(p,schemas[kind])
            # Same keeper and MVCC snapshot as raw SELECT facts, independently
            # compare every actual restored COPY data stream with this view.
            projections[kind]=snapshot_copy_inventory(con)
            if kind=='central':
                rows=_raw_rows(con,p)
                maintenance_rows=read_control_rows(con)
                maintenance_sequences=sequence_inventory(con,schemas=('easel_backup_control',))
        physical=_physical(o.media_fd,o.owner,deadline);physical_stamps=_physical_stamps(o.media_fd);derive_registry(rows,physical,source_owner=o.owner)
        # Earliest source expiry must survive the entire fixed capture+verify
        # budget. No mutation of expires_at/pins to manufacture headroom.
        central=keepers[0]
        expiry=central.execute("SELECT coalesce(min(expires_at)>clock_timestamp()+interval '120 seconds',true) FROM public.media_blobs WHERE state<>'deleted'").fetchone()[0]
        require(expiry is True)
        dump_seq={};after_roles={};after_seq={}
        for kind,con in zip(('central','identity'),keepers):
            name='db/'+kind+'.dump';fd=os.open(kind+'.dump',os.O_CREAT|os.O_EXCL|os.O_RDWR|os.O_NOFOLLOW,0o600,dir_fd=o.job_fd);fds[name]=fd
            # Libpq credentials remain in a minimal environment, never argv.
            from psycopg.conninfo import conninfo_to_dict
            ci=conninfo_to_dict(o.central_dsn if kind=='central' else o.identity_dsn)
            env={key:ci[source] for key,source in (('PGHOST','host'),('PGPORT','port'),('PGDATABASE','dbname'),('PGUSER','user'),('PGPASSWORD','password')) if source in ci}
            env.update(PGCONNECT_TIMEOUT='5',PGSSLMODE='disable')
            if kind=='central':
                control_ddl=child_runner.run('pg_dump',['--schema-only','--schema=easel_backup_control','--format=plain','--no-password','--snapshot='+snapshots[kind],'--restrict-key='+_SCHEMA_RESTRICT_KEY],exclusive=exclusive,deadline=deadline,environment=env)
                maintenance_snapshot=make_snapshot(maintenance_rows,maintenance_sequences,snapshot=snapshots[kind],catalog=schemas[kind],ddl=control_ddl.decode('utf-8'))
                require(maintenance_snapshot['tables']['control'][0]['state']=='CAPTURING')
            child_runner.run('pg_dump',['--format=custom','--schema=public','--no-password','--snapshot='+snapshots[kind]],exclusive=exclusive,deadline=deadline,environment=env,output_fd=fd,maximum=4*1024**3)
            require(os.pread(fd,5,0)==b'PGDMP');os.fsync(fd)
            actual_schema=child_runner.run('pg_restore',['--schema-only','--file=-','--restrict-key='+_SCHEMA_RESTRICT_KEY],exclusive=exclusive,deadline=deadline,input_fd=fd)
            expected_schema=child_runner.run('pg_dump',['--schema-only','--schema=public','--format=plain','--no-password','--snapshot='+snapshots[kind],'--restrict-key='+_SCHEMA_RESTRICT_KEY],exclusive=exclusive,deadline=deadline,environment=env)
            dump_schema[kind]={'snapshot':snapshots[kind],**verify_dump_schema(actual_schema,expected_schema)}
            dump_data=child_runner.run('pg_restore',['--data-only','--file=-'],exclusive=exclusive,deadline=deadline,input_fd=fd,maximum=MAX_DUMP_DATA)
            require(parse_dump_copy_inventory(dump_data)==projections[kind])
            dump_seq[kind]=_dump_sequences(child_runner,fd,before_seq[kind],exclusive,deadline)
            if o.companion is not None:o.companion._during(kind,con)
            during=o._roles(kind,con)
            require(during==before_roles[kind])
            name='db/'+kind+'.roles';fd=os.open(kind+'.roles',os.O_CREAT|os.O_EXCL|os.O_RDWR|os.O_NOFOLLOW,0o600,dir_fd=o.job_fd);fds[name]=fd
            raw=roles_export(during);require(os.write(fd,raw)==len(raw));os.fsync(fd)
            # A separate live observer is essential: the keeper's MVCC view
            # cannot prove privileges or protected source rows stayed fixed.
            if o.companion is None:
                with o._connect(kind) as observer:
                    after_roles[kind]=o._roles(kind,observer)
                    after_seq[kind]=sequence_inventory(observer)
            else:
                observer=o._connect(kind);observer_read_error=None
                try:
                    o.companion._after(kind,observer)
                    after_roles[kind]=o._roles(kind,observer)
                    after_seq[kind]=sequence_inventory(observer)
                except BaseException as error:
                    observer_read_error=error
                    raise
                finally:
                    try:
                        observer.close()
                        require(observer.raw.closed if type(observer) is _BudgetConnection else observer.closed)
                    except BaseException as error:
                        o.companion._after_observer_closed(kind,observer,error,observer_read_error)
                        raise
                    else:o.companion._after_observer_closed(kind,observer,None,observer_read_error)
            compare_sequences(before_seq[kind],dump_seq[kind],after_seq[kind])
            require(before_roles[kind]==after_roles[kind])
        require(o.reserve_bytes>=2*sum(os.fstat(fd).st_size for fd in fds.values())+sum(r['size'] for r in physical)+64*1024**2)
        identity_source={'profile':o.identity_profile.name,'relations':schemas['identity'],
            'image_sha256':o.identity_profile.image_sha256 if type(o.identity_profile) is IdentityProfile else None,
            'public_sql':[list(r) for r in o.identity_profile.public_sql] if type(o.identity_profile) is IdentityProfile else []}
        schema={'migrations':[list(r) for r in profile.migrations],'relations':schemas['central'],'identity':identity_source}
        config_metadata={kind:item_metadata(o.configs.items(kind)) for kind in ('central','identity','media')}
        sequences={kind:{'before':before_seq[kind],'dump':dump_seq[kind],'after':after_seq[kind]} for kind in profiles}
        roles={kind:{'before':before_roles[kind],'during':before_roles[kind],'after':after_roles[kind]} for kind in profiles}
        workers={key:getattr(b,key) for key in ('generation_digest','window_id','host_id','boot_id','process_start')}
        workers.update(domain=o._domain,registry=o._worker_registry,registry_sha256=hashlib.sha256(canonical_bytes(o._worker_registry)).hexdigest())
        raw=encode_source_facts(rows,[_file_record(n,fd) for n,fd in sorted(fds.items())],config_metadata,
            {**snapshots,'cross_database_atomic':False},sequences,profile=profile,schema=schema,roles=roles,workers=workers,physical=physical,projections=projections,dump_schema=dump_schema,source_owner=o.owner,maintenance_snapshot=maintenance_snapshot)
        facts=strict_json(raw,8*1024**2);bundles={}
        for kind in profiles.keys()|{'media'}:
            raw_bundle=encode_bundle(kind,o.configs.items(kind),source_facts=facts if kind=='central' else None)
            bundles['config/'+kind]=raw_bundle
            fd=os.open(kind+'.config',os.O_CREAT|os.O_EXCL|os.O_RDWR|os.O_NOFOLLOW,0o600,dir_fd=o.job_fd);fds['config/'+kind]=fd
            require(os.write(fd,raw_bundle)==len(raw_bundle));os.fsync(fd)
        require(sum(map(len,bundles.values()))<=MAX_BUNDLES_BYTES)
        blobs,refs=derive_registry(rows,physical,source_owner=o.owner)
        plan=validate_plan({'format':'easel-media-closure-v1','snapshots':{**snapshots,'source_sha256':hashlib.sha256(raw).hexdigest(),'schema_sha256':facts['schema_sha256']},
            'registry_sha256':registry_digest(blobs,refs),'files':[_file_record(n,fd) for n,fd in sorted(fds.items())],'blobs':blobs,'references':refs})
        required=sum(row['size'] for row in plan['files'])+2*physical_size(plan)+len(encode_manifest(plan))+64*1024**2
        require(o.reserve_bytes>=required)
        current=os.fstatvfs(o.job_fd);require(current.f_bavail*current.f_frsize>=required)
        verify_archived_bundles(bundles,plan['files'],plan)
        source=CapturedSource(plan,o.media_fd,dict(fds),o.owner,hashlib.sha256(raw).hexdigest(),b)
        o._exclusive=exclusive;o._before={'physical':physical,'physical_stamps':physical_stamps,'sequences':before_seq,'roles':before_roles,'schemas':schemas,'profiles':profiles,'protected':_protected(rows),'plan':canonical_bytes(plan),'fixed_stamps':fixed_identity_inventory(o.job_fd,fds),'maintenance_snapshot':maintenance_snapshot}
        o._capture=source;o._fds=dict(fds)
        o.verify_after(source)
        os.fsync(o.job_fd)
        with o._work_lock:require(not o._work_unknown)
        if o.companion is not None:o.companion._bind_captured(source)
        success=True;return source
    except BaseException as exc:
        for fd in fds.values():
            try:os.close(fd)
            except BaseException:
                if o.companion is not None:o.companion._cleanup_unknown=True
                raise
        o._fds={}
        if isinstance(exc,(KeyboardInterrupt,SystemExit)):raise
        # Cancellation is propagated, never converted into success.
        import asyncio
        if isinstance(exc,asyncio.CancelledError):raise
        raise B2Error() from None
    finally:
        if not success:
            for con in keepers:
                try:con.close()
                except Exception:
                    if o.companion is not None:o.companion._cleanup_unknown=True
            o._keepers={};o._keeper_stamps={};o._keeper_snapshots={}

async def _owned_thread(owned_inputs,operation):
    """Parent cancellation never closes a live keeper or releases live IO EX."""
    import asyncio
    o=owned_inputs
    require(type(o) is OwnedInputs and o._exclusive is not None)
    with o._work_lock:
        require(o._active_work==0 and not o._work_unknown)
        ref=o._exclusive.retain();o._active_work+=1
    def worker():
        try:
            with o._work_lock:
                require(not o._work_unknown);ref.start()
            return operation()
        finally:
            ref.close()
            with o._work_lock:o._active_work-=1
    task=asyncio.create_task(asyncio.to_thread(worker))
    def consume(future):
        if not future.cancelled():
            try:future.exception()
            except BaseException:pass
    task.add_done_callback(consume)
    try:return await asyncio.shield(task)
    except asyncio.CancelledError:
        with o._work_lock:o._work_unknown=True
        raise

async def capture_source(profile,exclusive,owned_inputs,child_runner):
    return await _owned_thread(owned_inputs,lambda:_capture_source_sync(profile,exclusive,owned_inputs,child_runner))

async def verify_after_async(owned_inputs,captured):
    """Final IO gate with actual retained thread lifetime; synchronous core is public too."""
    return await _owned_thread(owned_inputs,lambda:owned_inputs.verify_after(captured))

async def preflight_async(owned_inputs,profile,locks,*,host_identity):
    """Bounded actual pre-window read thread; closing live inputs is forbidden."""
    import asyncio
    o=owned_inputs;require(type(o) is OwnedInputs and o._exclusive is None)
    with o._work_lock:
        require(o._active_work==0 and not o._work_unknown);o._active_work+=1
    def worker():
        try:
            with o._work_lock:require(not o._work_unknown)
            result=o.preflight(profile,locks,host_identity=host_identity)
            with o._work_lock:require(not o._work_unknown)
            return result
        finally:
            with o._work_lock:o._active_work-=1
    task=asyncio.create_task(asyncio.to_thread(worker))
    def consume(future):
        if not future.cancelled():
            try:future.exception()
            except BaseException:pass
    task.add_done_callback(consume)
    try:return await asyncio.shield(task)
    except asyncio.CancelledError:
        with o._work_lock:o._work_unknown=True
        raise

async def run_owned_io(owned_inputs,trusted_internal_callable):
    """Internal pipeline IO lifetime only; never a substitute for source proof."""
    require(callable(trusted_internal_callable))
    return await _owned_thread(owned_inputs,trusted_internal_callable)
