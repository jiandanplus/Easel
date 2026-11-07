#!/usr/bin/env python3
"""154-only, reviewed same-snapshot encrypted backup and isolated restore drill.

No production writes, public ports, application startup, plaintext files, or raw
exception output. Existing encrypted backup/timer are not modified. Fresh
containers and volumes are stopped and retained, including on failure.
"""
import argparse
from contextlib import contextmanager
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import re
import resource
import select
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import time
import types
import uuid

TARGET = 'ser362540173134'
IMAGE = 'postgres@sha256:9e2cf6287f25c3151f4c9421ccce2b17f3e60061c111a29165e0d2cdd9ff0a97'
BASE = Path('/root/easel-migration-20261024/easel_encrypted_backup.py')
BASE_SHA = 'd330bccf0c71f9c4d808411ba48d87c217048dde0763023e4e9ca0cb45bb14a1'
RELEASE = '/srv/easel/releases/20261026-reconciliation-r2-v1'
MAXIMUM = 32 * 1024 * 1024
MAX_ROWS = 100000
MAX_DATA_BYTES = 128 * 1024 * 1024
POSTGRES_ARGUMENTS = ['postgres','-c','listen_addresses=','-c','max_connections=10','-c','shared_buffers=64MB',
                      '-c','log_statement=none','-c','log_min_error_statement=panic','-c','log_min_messages=panic']
SOURCES = (
    ('central', 'easel-central-pg-20261024', 'easel_restore_admin', 'easel_workbench_preview'),
    ('identity', 'easel-identity-pg-v2-20261024', 'preview_owner', 'easel_logto_preview'),
)

class Rejected(Exception):
    pass

def require(condition):
    if not condition:
        raise Rejected('restore_drill_rejected')

def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()

def digest(value):
    return hashlib.sha256(value).hexdigest()

def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')

def reviewed(plan, approved):
    require(isinstance(approved, str) and approved == digest(canonical(plan)))

def ident(value):
    require(isinstance(value, str) and 0 < len(value) <= 255 and not any(ord(c) < 32 for c in value))
    return '"' + value.replace('"', '""') + '"'

def literal(value):
    require(isinstance(value, str) and not any(ord(c) < 32 for c in value))
    return "'" + value.replace("'", "''") + "'"

def checked_central_schema(value):
    require(isinstance(value,str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,62}',value) is not None
            and not value.lower().startswith('pg_') and value.lower()!='information_schema')
    return value

def configured_central_schema(data):
    require(isinstance(data,bytes) and len(data)<=65536)
    try:lines=data.decode('utf-8').splitlines()
    except UnicodeDecodeError:raise Rejected('restore_drill_rejected')
    values=[line.strip().split('=',1)[1].strip() for line in lines if line.strip().startswith('CENTRAL_DB_SCHEMA=')]
    require(len(values)==1);value=values[0]
    if len(value)>=2 and value[0] in {'"',"'"} and value[-1]==value[0]:value=value[1:-1]
    return checked_central_schema(value)

def bound_central_schema(data,schema_sha,config_sha):
    value=configured_central_schema(data)
    require(digest(value.encode())==schema_sha and digest(data)==config_sha)
    return value

def schema_digest(value):
    text = value.decode('utf-8')
    text = '\n'.join(line for line in text.splitlines() if not re.fullmatch(r'\\(?:un)?restrict [A-Za-z0-9]+', line))
    return digest(text.encode())

def container_identity(nonce, kind):
    require(isinstance(nonce, str) and re.fullmatch('[0-9a-f]{32}', nonce) is not None and kind in {'central', 'identity'})
    name = 'easel-restore-drill-' + nonce + '-' + kind
    return {'nonce': nonce, 'kind': kind, 'name': name, 'volume': name + '-data', 'user': 'drill_' + nonce[:16]}

def container_create(item):
    return ['/usr/bin/docker', 'create', '--name=' + item['name'], '--label=easel.restore-drill=' + item['nonce'],
            '--label=easel.restore-kind=' + item['kind'], '--network=none', '--memory=512m', '--memory-swap=512m',
            '--cpus=0.75', '--pids-limit=128', '--restart=no', '--mount=type=volume,src=' + item['volume'] + ',dst=/var/lib/postgresql/data',
            '--env=POSTGRES_USER=' + item['user'], '--env=POSTGRES_HOST_AUTH_METHOD=trust', IMAGE,
            *POSTGRES_ARGUMENTS]

def verify_container(row, item):
    require(row['Name'] == '/' + item['name'] and row['Config']['Image'] == IMAGE)
    require(row['Config']['Labels'].get('easel.restore-drill') == item['nonce']
            and row['Config']['Labels'].get('easel.restore-kind') == item['kind'])
    require(row['Config'].get('Cmd') == POSTGRES_ARGUMENTS)
    env=row['Config'].get('Env') or []
    require([value.split('=',1)[1] for value in env if value.startswith('POSTGRES_USER=')]==[item['user']]
            and [value.split('=',1)[1] for value in env if value.startswith('POSTGRES_HOST_AUTH_METHOD=')]==['trust'])
    host = row['HostConfig']
    require(host['NetworkMode'] == 'none' and host['Memory'] == 512 * 1024 * 1024
            and host['MemorySwap'] == 512 * 1024 * 1024 and host['NanoCpus'] == 750000000
            and host['PidsLimit'] == 128 and host['RestartPolicy']['Name'] == 'no'
            and not host.get('PortBindings') and not any(row['NetworkSettings'].get('Ports', {}).values()))
    mounts = row['Mounts']
    require(len(mounts) == 1 and mounts[0]['Type'] == 'volume' and mounts[0]['Name'] == item['volume']
            and mounts[0]['Destination'] == '/var/lib/postgresql/data' and mounts[0]['RW'] is True)

def restore_command(item, database):
    return ['/usr/bin/docker', 'exec', '-i', item['name'], 'pg_restore', '--exit-on-error', '--single-transaction',
            '--no-password', '--username=' + item['user'], '--dbname=' + database]

def stop_owned(item, command, guard):
    guard(item)
    command(['/usr/bin/docker', 'stop', '--time=20', item['name']], timeout=35)

def verify_members(entries, manifest):
    expected = {value['name']: value for value in manifest['files']}
    require(len(expected) == len(manifest['files']) and set(entries) == set(expected))
    for name, value in entries.items():
        require(not name.startswith('/') and not any(part in ('', '.', '..') for part in name.split('/')))
        require(len(value) == expected[name]['length'] and digest(value) == expected[name]['sha256'])
        if name.startswith('databases/'):
            require(value.startswith(b'PGDMP'))

def match_inventories(source, restored):
    require(canonical(source) == canonical(restored))

def stable_sequences(before, after):
    require(canonical(before) == canonical(after))

def base_module():
    info = BASE.lstat()
    require(info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1 and BASE.is_file())
    require(stat.S_ISREG(info.st_mode))
    for parent in BASE.parents:
        meta=parent.lstat();require(stat.S_ISDIR(meta.st_mode) and meta.st_uid==0 and not stat.S_IMODE(meta.st_mode)&0o022)
    source = BASE.read_bytes()
    require(digest(source) == BASE_SHA)
    module = types.ModuleType('fixed_easel_backup')
    exec(compile(source, str(BASE), 'exec'), module.__dict__)
    return module

def psql(command, name, user, database, statement, snapshot=None):
    setup = "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\n"
    if snapshot is not None:
        require(re.fullmatch(r'[0-9A-Fa-f]+-[0-9A-Fa-f]+-[0-9]+', snapshot) is not None)
        setup += 'SET TRANSACTION SNAPSHOT ' + literal(snapshot) + ';\n'
    setup += "SET LOCAL statement_timeout='30s'; SET LOCAL lock_timeout='1s'; SET LOCAL row_security=off; SET LOCAL timezone='UTC'; SET LOCAL datestyle='ISO, YMD'; SET LOCAL intervalstyle='postgres'; SET LOCAL extra_float_digits=3;\n"
    result = command(['/usr/bin/docker', 'exec', '-i', name, 'psql', '-X', '-qAt', '--no-password', '--set=ON_ERROR_STOP=1',
                      '--username=' + user, '--dbname=' + database], data=(setup + statement + ';\nROLLBACK;\n').encode(), maximum=MAXIMUM, timeout=45)
    return json.loads(result)

def write_sql(command, item, database, statement):
    return command(['/usr/bin/docker', 'exec', '-i', item['name'], 'psql', '-X', '-q', '--no-password', '--set=ON_ERROR_STOP=1',
                    '--username=' + item['user'], '--dbname=' + database], data=statement.encode(), maximum=1024*1024, timeout=90)

@contextmanager
def exported_snapshot(name, user, database):
    args = ['/usr/bin/docker', 'exec', '-i', name, 'psql', '-X', '-qAt', '--no-password', '--set=ON_ERROR_STOP=1',
            '--username=' + user, '--dbname=' + database]
    proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C','HOME':'/root'}, close_fds=True, start_new_session=True)
    try:
        proc.stdin.write(b"BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; SET LOCAL statement_timeout='30s'; SET LOCAL lock_timeout='1s'; SET LOCAL idle_in_transaction_session_timeout='180s'; SELECT pg_export_snapshot();\n")
        proc.stdin.flush()
        deadline = time.monotonic() + 15
        line = bytearray()
        while not line.endswith(b'\n'):
            require(time.monotonic() < deadline and proc.poll() is None and len(line) < 128)
            if select.select([proc.stdout], [], [], 0.2)[0]:
                block = os.read(proc.stdout.fileno(), 128 - len(line))
                require(block)
                line.extend(block)
        token = line.decode().strip()
        require(re.fullmatch(r'[0-9A-Fa-f]+-[0-9A-Fa-f]+-[0-9]+', token) is not None)
        yield token
        proc.stdin.write(b'ROLLBACK;\n\\q\n'); proc.stdin.flush(); proc.stdin.close()
        require(proc.wait(timeout=10) == 0)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=10)
        for stream in (proc.stdin, proc.stdout):
            if stream and not stream.closed:
                stream.close()

RELATIONS = """SELECT coalesce(json_agg(t ORDER BY nspname,relname),'[]') FROM
 (SELECT n.nspname,c.relname,c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname NOT LIKE 'pg\\_%' ESCAPE '\\' AND n.nspname<>'information_schema'
 AND c.relkind IN ('r','p','m','S','f')) t"""
ROLES = """SELECT coalesce(json_agg(t ORDER BY rolname),'[]') FROM
 (SELECT rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,rolreplication,rolconnlimit,
 rolvaliduntil::text,rolbypassrls,rolpassword FROM pg_authid WHERE rolname NOT LIKE 'pg\\_%' ESCAPE '\\' AND rolname<>{ignore}) t"""
MEMBERS = """SELECT coalesce(json_agg(t ORDER BY role,member,grantor),'[]') FROM
 (SELECT pg_get_userbyid(roleid) role,pg_get_userbyid(member) member,pg_get_userbyid(grantor) grantor,admin_option,inherit_option,set_option
 FROM pg_auth_members WHERE pg_get_userbyid(member)<>{ignore} AND pg_get_userbyid(roleid)<>{ignore}) t"""
DATABASE = """SELECT row_to_json(t) FROM (SELECT datname,pg_get_userbyid(datdba) owner,pg_encoding_to_char(encoding) encoding,
 datcollate,datctype,datlocprovider,daticulocale,datcollversion,datconnlimit,datallowconn,datistemplate,
 (SELECT coalesce(json_agg(a ORDER BY grantor,grantee,privilege_type,is_grantable),'[]') FROM
 (SELECT pg_get_userbyid(x.grantor) grantor,CASE WHEN x.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(x.grantee) END grantee,
 x.privilege_type,x.is_grantable FROM aclexplode(coalesce(datacl,acldefault('d',datdba))) x) a) acl
 FROM pg_database WHERE datname=current_database()) t"""
EXTENSIONS = """SELECT coalesce(json_agg(t ORDER BY extname),'[]') FROM
 (SELECT e.extname,e.extversion,pg_get_userbyid(e.extowner) owner,n.nspname schema,e.extrelocatable,
 ARRAY(SELECT quote_ident(rn.nspname)||'.'||quote_ident(c.relname) FROM unnest(e.extconfig) WITH ORDINALITY a(oid,ord)
 JOIN pg_class c ON c.oid=a.oid JOIN pg_namespace rn ON rn.oid=c.relnamespace ORDER BY a.ord) config_tables,e.extcondition
 FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace) t"""
CONSTRAINTS = """SELECT coalesce(json_agg(t ORDER BY schema,relation,domain,name),'[]') FROM
 (SELECT n.nspname schema,c.relname relation,typ.typname domain,k.conname name,k.contype kind,
 k.convalidated,k.conislocal,k.coninhcount,k.connoinherit,k.condeferrable,k.condeferred,
 pg_get_constraintdef(k.oid,false) definition FROM pg_constraint k
 JOIN pg_namespace n ON n.oid=k.connamespace LEFT JOIN pg_class c ON c.oid=k.conrelid
 LEFT JOIN pg_type typ ON typ.oid=k.contypid
 WHERE n.nspname NOT LIKE 'pg\\_%' ESCAPE '\\' AND n.nspname<>'information_schema') t"""
SETTINGS = """SELECT coalesce(json_agg(t ORDER BY database,role),'[]') FROM
 (SELECT CASE WHEN setdatabase=0 THEN '' ELSE (SELECT datname FROM pg_database WHERE oid=setdatabase) END database,
 CASE WHEN setrole=0 THEN '' ELSE pg_get_userbyid(setrole) END role,setconfig
 FROM pg_db_role_setting WHERE (setdatabase=0 OR setdatabase=(SELECT oid FROM pg_database WHERE datname=current_database()))
 AND (setrole=0 OR pg_get_userbyid(setrole)<>{ignore})) t"""

def collect_inventory(command, name, user, database, snapshot=None, ignore=''):
    read = lambda statement: psql(command, name, user, database, statement, snapshot)
    require(read('SELECT count(*) FROM pg_largeobject_metadata') == 0)
    relations = read(RELATIONS)
    require(len(relations) <= 2048 and all(row['relkind'] != 'f' for row in relations))
    tables, sequences, remaining, bytes_left = {}, {}, MAX_ROWS, MAX_DATA_BYTES
    for row in relations:
        qualified = ident(row['nspname']) + '.' + ident(row['relname'])
        if row['relkind'] == 'S':
            sequences[qualified] = read('SELECT json_build_array(last_value,is_called) FROM ' + qualified)
            continue
        value = read("SELECT json_build_object('n',count(*),'bytes',coalesce(sum(row_bytes),0),'sha256',encode(sha256(convert_to(coalesce(string_agg(row_hash,'' ORDER BY row_hash),''),'UTF8')),'hex')) FROM (SELECT encode(sha256(convert_to(row_to_json(t)::text,'UTF8')),'hex') row_hash,octet_length(row_to_json(t)::text) row_bytes FROM " + qualified + ' t LIMIT ' + str(remaining + 1) + ') q')
        remaining -= value['n']; bytes_left -= value['bytes']
        require(remaining >= 0 and bytes_left >= 0)
        tables[qualified] = value
    dump = ['/usr/bin/docker','exec',name,'pg_dump','--schema-only','--quote-all-identifiers','--no-password','--username='+user,'--dbname='+database]
    if snapshot is not None:
        dump.append('--snapshot=' + snapshot)
    structure = command(dump, maximum=MAXIMUM, timeout=90)
    money = None
    credit = [row for row in relations if row['relname'] == 'credit_lots']
    if credit:
        require(len(credit) == 1)
        schema = ident(credit[0]['nspname'])
        money = read("SELECT json_build_object('lots_balanced',NOT EXISTS(SELECT 1 FROM " + schema + ".credit_lots WHERE issued<>available+held+spent+expired+refunded),'journals_balanced',NOT EXISTS(SELECT 1 FROM " + schema + ".credit_entries GROUP BY journal_id HAVING sum(delta)<>0))")
        require(money == {'lots_balanced': True, 'journals_balanced': True})
    settings=read(SETTINGS.format(ignore=literal(ignore)))
    require(not any(value.split('=',1)[0].startswith('log_') or value.split('=',1)[0]=='logging_collector'
                    for row in settings for value in row['setconfig']))
    return {'bootstrap_role':read('SELECT to_json(rolname) FROM pg_authid WHERE oid=10'),'schema':schema_digest(structure),'tables':tables,'sequences':sequences,
            'roles':read(ROLES.format(ignore=literal(ignore))), 'memberships':read(MEMBERS.format(ignore=literal(ignore))),
            'database':read(DATABASE),'extensions':read(EXTENSIONS),'constraints':read(CONSTRAINTS),'settings':settings, 'money':money}

def roles_dump(command, name, user, database):
    return command(['/usr/bin/docker','exec',name,'pg_dumpall','--roles-only','--quote-all-identifiers','--no-password','--username='+user,'--database='+database], maximum=MAXIMUM, timeout=60)

def capture_database(backup, source):
    kind,name,user,database = source
    backup.container_gate(name)
    role_sql_before = roles_dump(backup.command,name,user,database)
    started = utc()
    with exported_snapshot(name,user,database) as snapshot:
        inventory_before = collect_inventory(backup.command,name,user,database,snapshot)
        data = backup.command(['/usr/bin/docker','exec',name,'pg_dump','--format=custom','--quote-all-identifiers','--lock-wait-timeout=1s',
                               '--no-password','--username='+user,'--dbname='+database,'--snapshot='+snapshot],maximum=MAXIMUM,timeout=120)
        require(data.startswith(b'PGDMP'))
        inventory_after = collect_inventory(backup.command,name,user,database,snapshot)
        stable_sequences(inventory_before['sequences'],inventory_after['sequences'])
        match_inventories(inventory_before,inventory_after)
    require(schema_digest(role_sql_before) == schema_digest(roles_dump(backup.command,name,user,database)))
    return data, role_sql_before, inventory_before, {'started_utc':started,'finished_utc':utc()}

def bootstrap_roles_sql(statement, bootstrap):
    line='CREATE ROLE ' + ident(bootstrap) + ';'
    lines=statement.splitlines()
    require(lines.count(line)==1)
    return '\n'.join(value for value in lines if value!=line)+'\n'

def database_create_sql(meta):
    require(meta['encoding'] == 'UTF8' and meta['datallowconn'] is True and meta['datistemplate'] is False
            and meta['datlocprovider'] in ('c','i'))
    result = 'CREATE DATABASE ' + ident(meta['datname']) + ' WITH OWNER ' + ident(meta['owner']) + ' TEMPLATE template0 ENCODING ' + literal(meta['encoding'])
    result += ' LC_COLLATE ' + literal(meta['datcollate']) + ' LC_CTYPE ' + literal(meta['datctype'])
    result += ' LOCALE_PROVIDER ' + ('libc' if meta['datlocprovider']=='c' else 'icu')
    if meta['datlocprovider']=='i':
        result += ' ICU_LOCALE ' + literal(meta['daticulocale'])
    require(type(meta['datconnlimit']) is int and meta['datconnlimit'] >= -1)
    return result + ' CONNECTION LIMIT ' + str(meta['datconnlimit']) + ';\n'

def database_permissions_sql(meta, settings):
    database = ident(meta['datname'])
    roles = {row['grantee'] for row in meta['acl']} | {meta['owner'],'PUBLIC'}
    statement = ''.join('REVOKE ALL ON DATABASE ' + database + ' FROM ' + ('PUBLIC' if role=='PUBLIC' else ident(role)) + ';\n' for role in sorted(roles))
    for row in meta['acl']:
        require(row['privilege_type'] in ('CONNECT','CREATE','TEMPORARY') and type(row['is_grantable']) is bool)
        role = 'PUBLIC' if row['grantee']=='PUBLIC' else ident(row['grantee'])
        statement += 'SET ROLE ' + ident(row['grantor']) + '; GRANT ' + row['privilege_type'] + ' ON DATABASE ' + database + ' TO ' + role
        statement += (' WITH GRANT OPTION' if row['is_grantable'] else '') + '; RESET ROLE;\n'
    for row in settings:
        if row['database'] != meta['datname']:
            continue
        for config in row['setconfig']:
            key,value = config.split('=',1)
            require(re.fullmatch('[a-zA-Z_][a-zA-Z0-9_.]*',key) is not None)
            statement += 'SELECT pg_catalog.set_config(' + literal(key) + ',' + literal(value) + ',false);\n'
            statement += ('ALTER ROLE ' + ident(row['role']) + ' IN DATABASE ' + database if row['role'] else 'ALTER DATABASE ' + database)
            statement += ' SET ' + ident(key) + ' FROM CURRENT; RESET ' + ident(key) + ';\n'
    return statement

def write_private(path, value):
    fd = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        view=memoryview(value)
        while view:
            count=os.write(fd,view);require(count>0);view=view[count:]
        os.fsync(fd)
    finally:
        os.close(fd)

def resource_gate():
    disk=os.statvfs('/var/lib/docker')
    require(disk.f_bavail*disk.f_frsize >= 1024*1024*1024)
    mem={}
    for line in Path('/proc/meminfo').read_text().splitlines():
        key,value=line.split(':',1);mem[key]=int(value.strip().split()[0])
    require(mem.get('MemAvailable',0) >= 1024*1024)

def production_containers(backup):
    result={}
    for name in ('easel-central-pg-20261024','easel-identity-pg-v2-20261024','easel-logto-v2-20261024'):
        rows=json.loads(backup.command(['/usr/bin/docker','inspect',name],maximum=1024*1024))
        require(len(rows)==1 and rows[0]['Name']=='/'+name and rows[0]['State']['Running'] is True)
        row=rows[0];result[name]={'id':row['Id'],'image':row['Image'],'started_at':row['State']['StartedAt']}
    return result

def backup_definition_hashes(backup):
    return {str(path):digest(backup.read_fixed(path,65536,private=False)) for path in
            (BASE,Path('/etc/systemd/system/easel-encrypted-backup.service'),Path('/etc/systemd/system/easel-encrypted-backup.timer'))}

def service_state(backup):
    result={}
    for unit in ('easel-central-production','easel-console-production','easel-gateway-tunnel','nginx'):
        values=backup.command(['/usr/bin/systemctl','show','--property=MainPID,ActiveState,ActiveEnterTimestampMonotonic',unit],maximum=4096).decode().splitlines()
        value=dict(line.split('=',1) for line in values)
        require(value.get('ActiveState')=='active' and int(value.get('MainPID','0'))>1)
        result[unit]=value
    return result

PHASES={'seal_archive','archive_saved','verify_original_services','complete'} | {'capture_'+kind for kind in ('central','identity')} | {'restore_'+kind+'_'+step for kind in ('central','identity') for step in ('start_target','roles_parse','roles_write','create_database','extensions','pg_restore','known_checks','database_permissions','collect_inventory','compare','stopped')}

def write_phase(root,phase):
    require(phase in PHASES)
    fd=os.open(root/'phases.jsonl',os.O_WRONLY|os.O_APPEND|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        info=os.fstat(fd);require(stat.S_ISREG(info.st_mode) and info.st_uid==os.geteuid() and stat.S_IMODE(info.st_mode)==0o600 and info.st_nlink==1 and info.st_size<65536)
        value=canonical({'phase':phase,'at_utc':utc()})+b'\n'
        require(os.write(fd,value)==len(value));os.fsync(fd)
    finally:os.close(fd)

def build_plan(backup, nonce):
    require(os.geteuid()==0 and socket.gethostname()==TARGET)
    resource_gate()
    sources=[]
    for kind,name,user,database in SOURCES:
        backup.container_gate(name)
        row=json.loads(backup.command(['/usr/bin/docker','inspect',name],maximum=1024*1024))[0]
        require(psql(backup.command,name,user,database,'SELECT to_json(rolname) FROM pg_authid WHERE oid=10')==user)
        sources.append({'kind':kind,'container':name,'id':row['Id'],'image_id':row['Image'],'database':database})
    release=backup.command(['/usr/bin/systemctl','show','-p','WorkingDirectory','--value','easel-central-production'],maximum=4096).decode().strip()
    require(release==RELEASE)
    central_config=backup.read_fixed('/etc/easel-central/central.env',65536)
    central_schema=configured_central_schema(central_config)
    constraints=psql(backup.command,SOURCES[0][1],SOURCES[0][2],SOURCES[0][3],CONSTRAINTS)
    require(known_r2_check_plan(constraints,constraints,schema=central_schema)=='')
    return {'format':'easel-snapshot-restore-drill-plan-v4','nonce':nonce,'target':TARGET,'script_sha256':digest(Path(__file__).read_bytes()),
            'base_sha256':BASE_SHA,'sources':sources,'release':release,'image':IMAGE,
            'work_dir':'/root/easel-restore-drill-20261027-'+nonce,'dest':'/var/backups/easel-encrypted-v2-drill',
            'targets':[{**container_identity(nonce,kind),'user':user} for kind,name,user,database in SOURCES],
            'resource_limits':{'simultaneous_restore_containers':1,'memory_bytes':536870912,'nano_cpus':750000000,'pids':128,'network':'none','published_ports':0},
            'estimated_disk_upper_bytes':1024*1024*1024,'production_writes':False,'stops_production':False,
            'inventory_version':3,'central_schema_sha256':digest(central_schema.encode()),'central_config_sha256':digest(central_config),
            'check_restore_policy':'Central R2 fixed seven known CHECK definitions only; schema bound to captured private central.env, exact source/target definitions and flags; transactional original BETWEEN syntax; all other states rejected; raw schema hash unchanged.',
            'consistency':'Independent per-database exported read-only snapshots; roles and sequence state stability checked; no cross-database atomicity or PITR.'}

def guard_target(backup,item):
    rows=json.loads(backup.command(['/usr/bin/docker','inspect',item['name']],maximum=1024*1024))
    require(len(rows)==1);verify_container(rows[0],item)
    volume=json.loads(backup.command(['/usr/bin/docker','volume','inspect',item['volume']],maximum=65536))[0]
    require(volume['Name']==item['volume'] and volume['Driver']=='local' and volume['Labels'].get('easel.restore-drill')==item['nonce'])

def target_ready(backup,item):
    try:
        comm=backup.command(['/usr/bin/docker','exec',item['name'],'cat','/proc/1/comm'],maximum=128,timeout=5).decode().strip()
        if comm!='postgres':return False
        backup.command(['/usr/bin/docker','exec',item['name'],'pg_isready','--username='+item['user'],'--dbname=postgres'],maximum=65536,timeout=5)
        return True
    except backup.Rejected:
        return False

def start_target(backup,item):
    # Existing names are rejected before create: no reuse, reset, rm, or volume deletion.
    names=backup.command(['/usr/bin/docker','ps','-a','--format','{{.Names}}'],maximum=1024*1024).decode().splitlines()
    volumes=backup.command(['/usr/bin/docker','volume','ls','--format','{{.Name}}'],maximum=1024*1024).decode().splitlines()
    require(item['name'] not in names and item['volume'] not in volumes)
    backup.command(['/usr/bin/docker','volume','create','--label=easel.restore-drill='+item['nonce'],item['volume']],maximum=65536)
    backup.command(container_create(item),maximum=65536)
    guard_target(backup,item)
    backup.command(['/usr/bin/docker','start',item['name']],maximum=65536)
    deadline=time.monotonic()+60
    while not target_ready(backup,item):
        require(time.monotonic()<deadline);time.sleep(.5)
    guard_target(backup,item)

def stop_target(backup,item):
    stop_owned(item,backup.command,lambda value:guard_target(backup,value))
    row=json.loads(backup.command(['/usr/bin/docker','inspect',item['name']],maximum=1024*1024))[0]
    require(row['State']['Running'] is False and row['State']['Status']=='exited')

def prepare_extensions(backup,item,database,baseline):
    read=lambda statement:psql(backup.command,item['name'],item['user'],database,statement)
    empty=read("SELECT json_build_array((SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT LIKE 'pg\\_%' ESCAPE '\\' AND n.nspname<>'information_schema'),(SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname NOT LIKE 'pg\\_%' ESCAPE '\\' AND n.nspname<>'information_schema'),(SELECT count(*) FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname NOT LIKE 'pg\\_%' ESCAPE '\\' AND n.nspname<>'information_schema'))")
    require(empty==[0,0,0])
    existing={value['extname']:value for value in read(EXTENSIONS)}
    expected={value['extname']:value for value in baseline}
    require(len(expected)==len(baseline) and set(existing)<={'plpgsql'})
    def create(value,drop_seed=False):
        owner=value['owner'];schema=value['schema'];name=value['extname']
        require(read('SELECT to_json(EXISTS(SELECT 1 FROM pg_namespace WHERE nspname='+literal(schema)+'))') is True)
        privilege=[]
        if not read("SELECT to_json(has_database_privilege("+literal(owner)+",current_database(),'CREATE'))"):
            privilege.append(('CREATE','DATABASE',database))
        for key in ('CREATE','USAGE'):
            if not read('SELECT to_json(has_schema_privilege('+literal(owner)+','+literal(schema)+','+literal(key)+'))'):
                privilege.append((key,'SCHEMA',schema))
        statement='BEGIN;\n'
        if drop_seed:statement+='DROP EXTENSION '+ident(name)+' RESTRICT;\n'
        for key,kind,target in privilege:statement+='GRANT '+key+' ON '+kind+' '+ident(target)+' TO '+ident(owner)+';\n'
        statement+='SET LOCAL ROLE '+ident(owner)+'; CREATE EXTENSION '+ident(name)+' WITH SCHEMA '+ident(schema)+' VERSION '+literal(value['extversion'])+'; RESET ROLE;\n'
        for key,kind,target in reversed(privilege):statement+='REVOKE '+key+' ON '+kind+' '+ident(target)+' FROM '+ident(owner)+';\n'
        statement+='COMMIT;\n'
        write_sql(backup.command,item,database,statement)
    if 'plpgsql' in existing and ('plpgsql' not in expected or canonical(existing['plpgsql'])!=canonical(expected['plpgsql'])):
        if 'plpgsql' in expected:create(expected['plpgsql'],True)
        else:write_sql(backup.command,item,database,'BEGIN; DROP EXTENSION "plpgsql" RESTRICT; COMMIT;')
        existing.pop('plpgsql')
        if 'plpgsql' in expected:existing['plpgsql']=expected['plpgsql']
    for name in sorted(expected):
        if name not in existing:create(expected[name]);existing[name]=expected[name]
    actual=read(EXTENSIONS)
    require(canonical(sorted(actual,key=lambda value:value['extname']))==canonical(sorted(baseline,key=lambda value:value['extname'])))

def known_r2_check_plan(source,target,schema='public'):
    """Restore the original public R2 syntax; never normalize a fingerprint.

    BETWEEN creates a nested AND which a dump/reparse flattens. The seven
    version-pinned migrations are the sole approved reconstruction templates.
    Every source and target definition/attribute must match before any write.
    """
    def index(rows):
        require(isinstance(rows,list))
        result={(row['schema'],row['relation'],row['domain'],row['name']):row for row in rows}
        require(len(result)==len(rows));return result
    schema=checked_central_schema(schema)
    original,restored=index(source),index(target)
    specifications=(('console_reconciliation_cases','reason',True),('execution_resolutions','reason',True),
        ('execution_resolutions','approval_reference',True),('execution_provider_attempts','request_id',False),
        ('execution_provider_associations','http_request_id',False),('execution_provider_associations','provider_response_id',False),
        ('execution_terminal_evidence','provider_response_id',False))
    statements=[]
    for table,column,trim in specifications:
        name=table+'_'+column+'_check';key=(schema,table,None,name)
        require(key in original and key in restored)
        length='length(btrim('+column+'))' if trim else 'length('+column+')'
        maximum=500 if trim else 256
        tail=('('+column+' = btrim('+column+")) AND ("+column+" !~ '[[:cntrl:]]'::text)") if trim else '('+column+" ~ '^[A-Za-z0-9._:-]+$'::text)"
        nested='CHECK (((('+length+' >= 1) AND ('+length+' <= '+str(maximum)+')) AND '+tail+'))'
        flat='CHECK ((('+length+' >= 1) AND ('+length+' <= '+str(maximum)+') AND '+tail+'))'
        expected={'schema':schema,'relation':table,'domain':None,'name':name,'kind':'c',
            'convalidated':True,'conislocal':True,'coninhcount':0,'connoinherit':False,
            'condeferrable':False,'condeferred':False,'definition':nested}
        require(canonical(original[key])==canonical(expected))
        if canonical(restored[key])==canonical(expected):continue
        require(canonical(restored[key])==canonical({**expected,'definition':flat}))
        quoted=ident(column)
        expression=('length(btrim('+quoted+')) BETWEEN 1 AND 500 AND '+quoted+'=btrim('+quoted+") AND "+quoted+" !~ '[[:cntrl:]]'") if trim else 'length('+quoted+") BETWEEN 1 AND 256 AND "+quoted+" ~ '^[A-Za-z0-9._:-]+$'"
        qualified=ident(schema)+'.'+ident(table)
        statements.append('ALTER TABLE ONLY '+qualified+' DROP CONSTRAINT '+ident(name)+' RESTRICT;')
        statements.append('ALTER TABLE ONLY '+qualified+' ADD CONSTRAINT '+ident(name)+' CHECK('+expression+');')
    if not statements:return ''
    return "BEGIN;\nSET LOCAL statement_timeout='30s'; SET LOCAL lock_timeout='1s'; SET LOCAL search_path=pg_catalog,"+ident(schema)+";\n"+'\n'.join(statements)+'\nCOMMIT;'

def restore_known_r2_checks(backup,item,database,baseline,schema='public'):
    require(item['kind']=='central')
    guard_target(backup,item)
    current=psql(backup.command,item['name'],item['user'],database,CONSTRAINTS)
    statement=known_r2_check_plan(baseline,current,schema=schema)
    if statement:
        write_sql(backup.command,item,database,statement)
        current=psql(backup.command,item['name'],item['user'],database,CONSTRAINTS)
        require(known_r2_check_plan(baseline,current,schema=schema)=='')

def restore_one(backup,item,entries,inventory,phase=lambda _:None,r2_check_repair=False,r2_schema=None):
    kind=item['kind'];database=inventory['database']['datname']
    require(item['user']==inventory['bootstrap_role'])
    phase('restore_'+kind+'_start_target');start_target(backup,item)
    phase('restore_'+kind+'_roles_parse');roles=bootstrap_roles_sql(entries['roles/'+kind+'.sql'].decode(),item['user'])
    phase('restore_'+kind+'_roles_write');write_sql(backup.command,item,'postgres',roles)
    phase('restore_'+kind+'_create_database');write_sql(backup.command,item,'postgres',database_create_sql(inventory['database']))
    phase('restore_'+kind+'_extensions');prepare_extensions(backup,item,database,inventory['extensions'])
    phase('restore_'+kind+'_pg_restore')
    backup.command(restore_command(item,database),data=entries['databases/'+kind+'.dump'],maximum=1024*1024,timeout=180)
    if r2_check_repair:
        phase('restore_'+kind+'_known_checks');restore_known_r2_checks(backup,item,database,inventory['constraints'],schema=r2_schema)
    phase('restore_'+kind+'_database_permissions');write_sql(backup.command,item,'postgres',database_permissions_sql(inventory['database'],inventory['settings']))
    phase('restore_'+kind+'_collect_inventory');restored=collect_inventory(backup.command,item['name'],item['user'],database)
    phase('restore_'+kind+'_compare');match_inventories(inventory,restored)
    return {'kind':kind,'tables':len(restored['tables']),'rows':sum(row['n'] for row in restored['tables'].values()),
            'schema_sha256':restored['schema'],'data_sha256':digest(canonical(restored['tables'])),
            'sequence_sha256':digest(canonical(restored['sequences'])),'roles_sha256':digest(canonical([restored['roles'],restored['memberships']])),
            'database_metadata_sha256':digest(canonical([restored['database'],restored['settings']])),'constraints_sha256':digest(canonical(restored['constraints'])),'money':restored['money'],'matched':True}

def apply(backup,plan,approved):
    reviewed(plan,approved)
    root=Path(plan['work_dir']);root.mkdir(mode=0o700)
    require(root.lstat().st_uid==0 and stat.S_IMODE(root.lstat().st_mode)==0o700)
    write_private(root/'intent.json',canonical({'plan_sha256':approved,'started_utc':utc(),'state':'started'}))
    original_services=service_state(backup)
    original_containers=production_containers(backup)
    original_backup_definitions=backup_definition_hashes(backup)
    entries={};inventory={};windows={};targets=[];results=[];last_phase=['initial'];failed=False
    def phase(value):
        write_phase(root,value);last_phase[0]=value
    try:
        for source in SOURCES:
            kind=source[0];phase('capture_'+kind)
            data,roles,value,window=capture_database(backup,source)
            entries['databases/'+kind+'.dump']=data;entries['roles/'+kind+'.sql']=roles
            entries['inventories/'+kind+'.json']=canonical(value);inventory[kind]=value;windows[kind]=window
        app_uid=backup.pwd.getpwnam('easel-central').pw_uid
        for path in backup.REQUIRED:
            entries['configs'+path]=backup.read_fixed(path,MAXIMUM,owner=app_uid if path=='/etc/easel-central/execution.json' else 0)
        bound_central_schema(entries['configs/etc/easel-central/central.env'],plan['central_schema_sha256'],plan['central_config_sha256'])
        require(sum(map(len,entries.values()))<=MAXIMUM)
        manifest={'format':'easel-encrypted-backup-v2','inventory_version':3,'central_schema_sha256':plan['central_schema_sha256'],'central_config_sha256':plan['central_config_sha256'],'target':TARGET,'plan_sha256':approved,'snapshots':windows,'consistency':plan['consistency'],
                  'files':[{'name':name,'length':len(data),'sha256':digest(data)} for name,data in sorted(entries.items())]}
        verify_members(entries,manifest)
        phase('seal_archive');payload=backup.archive(entries,manifest);helper=backup.crypto()
        public=backup.read_fixed(backup.ROOT/'transfer-public.pem',32768,private=False)
        private=backup.read_fixed(backup.ROOT/'transfer-private.pem',32768)
        backup_id=str(uuid.uuid4())
        envelope=helper.seal(public,payload,{'transfer_id':backup_id,'kind':'central','target':TARGET,'expected_sha256':digest(payload),'expected_length':len(payload)})
        def decrypt(value):
            clear=helper.unseal(private,value,backup_id,'central',TARGET)
            require(clear==payload);backup.verify_payload(clear,manifest)
            return clear
        backup.DEST=Path(plan['dest'])
        path=backup.save(envelope,backup_id,decrypt);phase('archive_saved')
        # Restore only bytes re-read and authenticated from the durable ciphertext.
        clear=decrypt(backup.read_fixed(Path(path),MAXIMUM+1024*1024))
        with tarfile.open(fileobj=io.BytesIO(clear),mode='r:') as archive:
            restored_entries={member.name:archive.extractfile(member).read(MAXIMUM+1) for member in archive.getmembers() if member.name!='manifest.json'}
        verify_members(restored_entries,manifest)
        restored_schema=bound_central_schema(restored_entries['configs/etc/easel-central/central.env'],plan['central_schema_sha256'],plan['central_config_sha256'])
        for item in plan['targets']:
            resource_gate();targets.append(item)
            result=restore_one(backup,item,restored_entries,inventory[item['kind']],phase,r2_check_repair=item['kind']=='central',r2_schema=restored_schema);results.append(result)
            stop_target(backup,item);phase('restore_'+item['kind']+'_stopped')
        phase('verify_original_services')
        require(service_state(backup)==original_services and production_containers(backup)==original_containers
                and backup_definition_hashes(backup)==original_backup_definitions)
        receipt={'status':'fresh_restore_verified','plan_sha256':approved,'started_utc':json.loads((root/'intent.json').read_bytes())['started_utc'],'finished_utc':utc(),
                 'ciphertext_path':path,'ciphertext_bytes':len(envelope),'ciphertext_sha256':digest(envelope),'manifest_sha256':digest(canonical(manifest)),
                 'databases':results,'snapshots':windows,'consistency':plan['consistency'],'production_writes':0,'public_ports':0,
                 'containers_stopped_retained':True,'volumes_retained':True,'original_backup_timer_changed':False,'original_services_unchanged':True,'original_containers_unchanged':True}
        write_private(root/'restore-safe.json',canonical(receipt));phase('complete');print(json.dumps(receipt,sort_keys=True))
    except BaseException:
        failed=True;raise
    finally:
        cleanup=[]
        for item in targets:
            try:
                guard_target(backup,item)
                row=json.loads(backup.command(['/usr/bin/docker','inspect',item['name']],maximum=1024*1024))[0]
                if row['State']['Running']:
                    stop_target(backup,item)
                cleanup.append({'kind':item['kind'],'not_running_verified':True})
            except BaseException:
                cleanup.append({'kind':item['kind'],'not_running_verified':False})
                # Preserve every object; a failed guard never targets an unverified name.
                pass
        if failed:
            write_private(root/'failure-safe.json',canonical({'status':'restore_failed','phase':last_phase[0],'nonce':plan['nonce'],'plan_sha256':approved,'production_writes':0,'cleanup':cleanup}))

def main():
    os.umask(0o077);resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    def interrupted(*_): raise Rejected('restore_drill_interrupted')
    signal.signal(signal.SIGTERM,interrupted);signal.signal(signal.SIGHUP,interrupted)
    parser=argparse.ArgumentParser();parser.add_argument('--nonce',required=True);parser.add_argument('--apply',action='store_true');parser.add_argument('--reviewed-plan-sha256')
    args=parser.parse_args();container_identity(args.nonce,'central')
    backup=base_module();plan=build_plan(backup,args.nonce)
    if not args.apply:
        require(args.reviewed_plan_sha256 is None)
        print(json.dumps({'plan':plan,'plan_sha256':digest(canonical(plan))},sort_keys=True));return
    apply(backup,plan,args.reviewed_plan_sha256)

if __name__=='__main__':
    try:
        main()
    except BaseException:
        print('easel restore drill rejected; private intent and owned containers retained',file=sys.stderr)
        sys.exit(1)
