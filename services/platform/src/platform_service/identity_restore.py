"""Bounded, read-only same-Logto restore evidence, never proof of an OIDC login.

The trust root is the operator's protected policy, approved backup and selected
databases. This does not defend against a malicious database owner, attest the
HTTPS deployment, or fence writers. Both databases must be quiescent for cutover.
Only hashes and candidate IDs leave PostgreSQL; raw identity rows never do.
"""
from contextlib import AsyncExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import stat

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from .identity_migration import MigrationPolicy, VerifiedRestoreEvidence
from .errors import PlatformError


MAX_OBJECTS = 2048
MAX_ROWS = 100_000
MAX_DATA_BYTES = 128 * 1024 * 1024
MAX_SCHEMA_BYTES = 8 * 1024 * 1024
MAX_BACKUP_BYTES = 1024 * 1024 * 1024
MAX_CONFIG_BYTES = 32 * 1024
_SCHEMA = re.compile(r'[a-z_][a-z0-9_]{0,62}\Z')


class RestoreEvidenceError(ValueError):
    """Fixed diagnostic code only: no DSN, row content or underlying SQL errors."""


def _fail(code):
    raise RestoreEvidenceError('restore_' + code)


@dataclass(frozen=True)
class RestoreConfig:
    policy: MigrationPolicy
    source_dsn: str = field(repr=False)
    target_dsn: str = field(repr=False)
    source_schema: str
    target_schema: str
    backup_path: str = field(repr=False)


@contextmanager
def _private_file(path, limit):
    """Open every component without symlink traversal; validate the opened inode."""
    try:
        path = os.fspath(path)
        if not isinstance(path, str) or not path.startswith('/') or '\x00' in path:
            _fail('private_file_invalid')
        parts = path.split('/')[1:]
        if not parts or any(p in ('', '.', '..') for p in parts):
            _fail('private_file_invalid')
        directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = child
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        finally:
            os.close(directory)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if (not stat.S_ISREG(before.st_mode) or stat.S_IMODE(before.st_mode) != 0o600
                    or before.st_uid != os.geteuid() or before.st_nlink != 1
                    or not 0 < before.st_size <= limit):
                _fail('private_file_invalid')
            yield stream
            after = os.fstat(stream.fileno())
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                _fail('private_file_changed')
    except RestoreEvidenceError:
        raise
    except (OSError, TypeError, ValueError):
        raise RestoreEvidenceError('restore_private_file_invalid') from None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail('config_invalid')
        result[key] = value
    return result


def _dsn(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 8192:
        _fail('config_invalid')
    try:
        options = conninfo_to_dict(value)
    except psycopg.Error:
        raise RestoreEvidenceError('restore_config_invalid') from None
    if (any(key in options for key in ('service', 'servicefile', 'passfile', 'options'))
            or any(not options.get(key) for key in ('host', 'dbname', 'user'))
            or ',' in options['host'] or options['host'].startswith('/')
            or any(',' in options.get(key, '') for key in ('hostaddr', 'port'))):
        _fail('config_invalid')


def _schemas(source, target):
    if (not isinstance(source, str) or not isinstance(target, str)
            or not _SCHEMA.fullmatch(source) or source.startswith('pg_')
            or source == 'information_schema' or source != target):
        _fail('schema_invalid')


def read_private_restore_config(path):
    """Parse exact private operator configuration; do not connect or read backup."""
    try:
        with _private_file(path, MAX_CONFIG_BYTES) as stream:
            raw = stream.read(MAX_CONFIG_BYTES + 1)
        value = json.loads(raw, object_pairs_hook=_unique_object)
        expected = {'policy', 'source_dsn', 'target_dsn', 'source_schema', 'target_schema', 'backup_path'}
        policy_keys = {'environment', 'source_issuer', 'target_issuer', 'tenant_id', 'client_ids', 'approved_backup_sha256'}
        if (not isinstance(value, dict) or set(value) != expected
                or not isinstance(value['policy'], dict) or set(value['policy']) != policy_keys
                or not isinstance(value['policy']['client_ids'], list)):
            _fail('config_invalid')
        _dsn(value['source_dsn'])
        _dsn(value['target_dsn'])
        _schemas(value['source_schema'], value['target_schema'])
        backup = value['backup_path']
        if not isinstance(backup, str) or not Path(backup).is_absolute():
            _fail('config_invalid')
        policy = MigrationPolicy(**{**value['policy'], 'client_ids': tuple(value['policy']['client_ids'])})
        return RestoreConfig(**{**value, 'policy': policy})
    except RestoreEvidenceError:
        raise
    except (ValueError, TypeError, KeyError, UnicodeError, PlatformError):
        raise RestoreEvidenceError('restore_config_invalid') from None


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, separators=(',', ':'), sort_keys=True).encode()).hexdigest()


async def _rows(conn, statement, args=()):
    # Every catalog inventory is bounded before it reaches the collector process.
    query = sql.SQL("""SELECT CASE WHEN octet_length(row_to_json(b)::text)<=%s
        THEN row_to_json(b)::text ELSE NULL END FROM
        (SELECT * FROM ({}) inventory_rows LIMIT %s) b""").format(sql.SQL(statement))
    result, size = [], 0
    async with conn.cursor(name='restore_catalog') as cursor:
        cursor.itersize = 1
        await cursor.execute(query, (MAX_SCHEMA_BYTES, *args, MAX_OBJECTS + 1))
        async for (encoded,) in cursor:
            if encoded is None:
                _fail('schema_limit')
            size += len(encoded.encode())
            if size > MAX_SCHEMA_BYTES or len(result) >= MAX_OBJECTS:
                _fail('schema_limit')
            result.append(tuple(json.loads(encoded).values()))
    return result



def _acl_sql(kind):
    """Canonical object grant entries, including PostgreSQL's NULL defaults.

    Empty ACLs stay empty. Role names replace local OIDs without conflating the
    PUBLIC pseudo-role with a quoted role name. Owners remain separate fields.
    Only fixed catalog aliases are accepted; no caller SQL is interpolated.
    """
    if kind == 'relation':
        acl = 'c.relacl'
        default = "CASE WHEN c.relkind='S' THEN acldefault('s',c.relowner) ELSE acldefault('r',c.relowner) END"
    elif kind == 'function':
        acl, default = 'p.proacl', "acldefault('f',p.proowner)"
    elif kind == 'enum':
        acl, default = 't.typacl', "acldefault('T',t.typowner)"
    else:
        _fail('acl_kind_invalid')
    # Aggregate within the object row: aclexplode(empty) must not discard it.
    canonical = f"""(SELECT COALESCE(jsonb_agg(jsonb_build_array(
        CASE WHEN a.grantor=0 THEN 'PUBLIC' ELSE 'ROLE' END,
        CASE WHEN a.grantor=0 THEN NULL ELSE pg_get_userbyid(a.grantor) END,
        CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE 'ROLE' END,
        CASE WHEN a.grantee=0 THEN NULL ELSE pg_get_userbyid(a.grantee) END,
        a.privilege_type,a.is_grantable)
        ORDER BY (a.grantor=0),
        (CASE WHEN a.grantor=0 THEN NULL ELSE pg_get_userbyid(a.grantor) END) COLLATE "C",
        (a.grantee=0),
        (CASE WHEN a.grantee=0 THEN NULL ELSE pg_get_userbyid(a.grantee) END) COLLATE "C",
        a.privilege_type COLLATE "C",a.is_grantable),'[]'::jsonb)
        FROM aclexplode(COALESCE({acl},{default})) a)"""
    if kind == 'relation':
        # Index ACLs are not independently grantable. Preserve any anomalous raw
        # value rather than inventing table defaults or silently discarding it.
        return f"CASE WHEN c.relkind IN ('r','v','S') THEN {canonical} ELSE to_jsonb(c.relacl::text) END"
    return canonical


async def _enum_inventory(conn, schema):
    # OIDs and numeric sort positions are local catalog details. A logical restore
    # preserves label order, type identity and privileges, but may reassign both.
    definitions = await _rows(conn, f"""SELECT n.nspname AS enum_schema,t.typname AS enum_name,
        pg_get_userbyid(t.typowner) AS enum_owner,{_acl_sql('enum')} AS enum_acl,
        an.nspname AS array_schema,a.typname AS array_name,
        pg_get_userbyid(a.typowner) AS array_owner,a.typacl::text AS array_acl,
        (a.typtype='b' AND a.typcategory='A' AND a.typelem=t.oid) AS generated_array
        FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
        LEFT JOIN pg_type a ON a.oid=t.typarray LEFT JOIN pg_namespace an ON an.oid=a.typnamespace
        WHERE n.nspname=%s AND t.typtype='e' ORDER BY n.nspname,t.typname""", (schema,))
    if any(not row[-1] or row[4] != schema for row in definitions):
        _fail('unsupported_type')
    labels = await _rows(conn, """SELECT n.nspname,t.typname,e.enumlabel
        FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace JOIN pg_enum e ON e.enumtypid=t.oid
        WHERE n.nspname=%s AND t.typtype='e' ORDER BY n.nspname,t.typname,e.enumsortorder""", (schema,))
    return {'definitions': [row[:-1] for row in definitions], 'ordered_labels': labels}


async def _inventory(conn, schema, tenant_id):
    await conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    await conn.execute("SET LOCAL row_security=off")
    await conn.execute("SET LOCAL timezone='UTC'")
    await conn.execute("SET LOCAL datestyle='ISO, YMD'")
    await conn.execute("SET LOCAL intervalstyle='postgres'")
    await conn.execute("SET LOCAL extra_float_digits=3")
    await conn.execute(sql.SQL('SET LOCAL search_path TO pg_catalog, {}').format(sql.Identifier(schema)))
    context = await (await conn.execute("SELECT current_database(),inet_server_addr()::text,inet_server_port(),current_setting('server_version_num'),current_setting('server_encoding'),current_setting('transaction_read_only'),current_setting('row_security')")).fetchone()
    if not (await (await conn.execute("SELECT has_function_privilege('pg_catalog.pg_control_system()','EXECUTE')")).fetchone())[0]:
        _fail('context_unavailable')
    identity = await (await conn.execute("SELECT (pg_control_system()).system_identifier::text,(SELECT oid::text FROM pg_database WHERE datname=current_database())")).fetchone()
    if int(context[3]) // 10000 != 16:
        _fail('postgres_version_unsupported')
    if context[5:] != ('on', 'off'):
        _fail('readonly_required')
    if not (await (await conn.execute('SELECT has_schema_privilege(%s,\'USAGE\')', (schema,))).fetchone())[0]:
        _fail('visibility_incomplete')
    relations = await _rows(conn, f"""SELECT c.oid,c.relname,c.relkind,c.relpersistence,c.relrowsecurity,c.relforcerowsecurity,
        c.reloptions,c.relispartition,pg_get_userbyid(c.relowner),{_acl_sql('relation')} FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=%s ORDER BY c.relname""", (schema,))
    relations = [(int(r[0]), *r[1:]) for r in relations]
    if not relations or any(r[2] not in ('r','i','S','v') or r[3] != 'p' or r[7] for r in relations):
        _fail('unsupported_schema')
    if await _rows(conn, """SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
        LEFT JOIN pg_type element ON element.oid=t.typelem
        WHERE n.nspname=%s AND t.typrelid=0
          AND NOT (t.typtype='e' AND t.typelem=0)
          AND NOT (t.typtype='b' AND t.typelem<>0 AND element.typarray=t.oid
              AND element.typnamespace=t.typnamespace AND (element.typtype='e' OR element.typrelid<>0))
        LIMIT 1""", (schema,)):
        _fail('unsupported_type')
    if await _rows(conn, """SELECT 1 FROM pg_inherits i JOIN pg_class c ON c.oid=i.inhrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s LIMIT 1""", (schema,)):
        _fail('unsupported_schema')
    # Version and all supported schema objects are part of the same comparison.
    structure = {'server_version': context[3:5], 'relations': [r[1:] for r in relations]}
    structure['enums'] = await _enum_inventory(conn, schema)
    structure['database_locale'] = await (await conn.execute("SELECT datcollate,datctype,datlocprovider,daticulocale,datcollversion FROM pg_database WHERE datname=current_database()")).fetchone()
    structure['extensions'] = await _rows(conn, 'SELECT e.extname,e.extversion,n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace ORDER BY e.extname')
    structure['functions'] = await _rows(conn, f"""SELECT p.proname,pg_get_function_identity_arguments(p.oid),pg_get_functiondef(p.oid),pg_get_userbyid(p.proowner),{_acl_sql('function')}
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=%s ORDER BY 1,2""", (schema,))
    structure['constraints'] = await _rows(conn, """SELECT c.relname,k.conname,k.contype,pg_get_constraintdef(k.oid,false),k.convalidated,k.condeferrable,k.condeferred
        FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=%s ORDER BY 1,2""", (schema,))
    structure['indexes'] = await _rows(conn, """SELECT c.relname,pg_get_indexdef(i.indexrelid),i.indisvalid,i.indisready
        FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=%s ORDER BY 1""", (schema,))
    structure['triggers'] = await _rows(conn, """SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid,false),t.tgenabled
        FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=%s AND NOT t.tgisinternal ORDER BY 1,2""", (schema,))
    structure['policies'] = await _rows(conn, """SELECT tablename,policyname,permissive,roles,cmd,qual,with_check
        FROM pg_policies WHERE schemaname=%s ORDER BY 1,2""", (schema,))
    structure['rules'] = await _rows(conn, """SELECT tablename,rulename,definition FROM pg_rules
        WHERE schemaname=%s ORDER BY 1,2""", (schema,))
    tables, sequences, views = {}, {}, {}
    remaining, bytes_left = MAX_ROWS, MAX_DATA_BYTES
    for oid, name, kind, *_ in relations:
        if kind == 'i':
            continue
        qualified = sql.Identifier(schema, name)
        if kind == 'v':
            views[name] = (await (await conn.execute('SELECT pg_get_viewdef(%s,false)', (oid,))).fetchone())[0]
            continue
        if kind == 'S':
            definition = await (await conn.execute('SELECT seqtypid::regtype::text,seqstart,seqincrement,seqmax,seqmin,seqcache,seqcycle FROM pg_sequence WHERE seqrelid=%s', (oid,))).fetchone()
            ownership = await _rows(conn, """SELECT n.nspname,c.relname,a.attname,d.deptype
                FROM pg_depend d JOIN pg_class c ON c.oid=d.refobjid
                JOIN pg_namespace n ON n.oid=c.relnamespace
                LEFT JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=d.refobjsubid
                WHERE d.classid='pg_class'::regclass AND d.refclassid='pg_class'::regclass
                AND d.objid=%s AND d.deptype IN ('a','i') ORDER BY 1,2,3,4""", (oid,))
            state = await (await conn.execute(sql.SQL('SELECT last_value,is_called FROM {}').format(qualified))).fetchone()
            sequences[name] = ((definition, ownership), state)
            continue
        visible = await (await conn.execute("SELECT has_table_privilege(%s,'SELECT'),row_security_active(%s)", (oid, oid))).fetchone()
        if visible != (True, False):
            _fail('visibility_incomplete')
        columns = await _rows(conn, """SELECT a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,a.attidentity,a.attgenerated,
            pg_get_expr(d.adbin,d.adrelid),co.collname,tn.nspname,
            (tn.nspname='pg_catalog' OR (tn.nspname=%s AND t.typtype='e') OR
              (tn.nspname=%s AND t.typtype='b' AND element.typtype='e' AND element.typarray=t.oid AND en.nspname=%s))
            FROM pg_attribute a JOIN pg_type t ON t.oid=a.atttypid JOIN pg_namespace tn ON tn.oid=t.typnamespace
            LEFT JOIN pg_type element ON element.oid=t.typelem LEFT JOIN pg_namespace en ON en.oid=element.typnamespace
            LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
            LEFT JOIN pg_collation co ON co.oid=a.attcollation
            WHERE a.attrelid=%s AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum""", (schema, schema, schema, oid))
        if not columns or any(not c[-1] for c in columns):
            _fail('unsupported_type')
        structure['columns:' + name] = columns
        # Hash rows on the server, including other tenants. Transfer no passwords/keys.
        query = sql.SQL("SELECT encode(sha256(convert_to(row_to_json(t)::text,'UTF8')),'hex'),octet_length(row_to_json(t)::text) FROM {} t LIMIT %s").format(qualified)
        async with conn.cursor(name='restore_rows') as cursor:
            await cursor.execute(query, (remaining + 1,))
            hashes = []
            async for row_hash, row_bytes in cursor:
                remaining -= 1
                bytes_left -= row_bytes
                if remaining < 0 or bytes_left < 0:
                    _fail('data_limit')
                hashes.append(row_hash)
        tables[name] = (len(hashes), _digest(sorted(hashes)))
    if not {'users', 'applications'} <= tables.keys():
        _fail('identity_tables_missing')
    candidates = {}
    for name in ('users', 'applications'):
        columns = structure['columns:' + name]
        types = {c[0]: c[1] for c in columns}
        if any(not (types.get(key) == 'text' or types.get(key, '').startswith('character varying')) for key in ('tenant_id','id')):
            _fail('identity_columns_invalid')
        cursor = await conn.execute(sql.SQL('SELECT id FROM {} WHERE tenant_id=%s ORDER BY id LIMIT %s').format(sql.Identifier(schema, name)), (tenant_id, MAX_ROWS + 1))
        ids = tuple(r[0] for r in await cursor.fetchall())
        if (not ids or len(ids) > MAX_ROWS or len(set(ids)) != len(ids)
                or any(not isinstance(v, str) or not 1 <= len(v) <= 255 or any(ord(c) < 32 for c in v) for v in ids)):
            _fail('identity_inventory_invalid')
        candidates[name] = ids
    structure['views'] = views
    structure['sequences'] = {k: v[0] for k, v in sequences.items()}
    serialized = json.dumps(structure, ensure_ascii=True, sort_keys=True)
    if len(serialized.encode()) > MAX_SCHEMA_BYTES:
        _fail('schema_limit')
    return {'database': context[0], 'context': _digest([*identity, context[0], schema]),
            'schema': _digest(structure), 'data': _digest({'tables': tables, 'sequences': {k: v[1] for k,v in sequences.items()}}),
            'users': candidates['users'], 'clients': candidates['applications']}


def _matching_inventories(source, target):
    # Database names may legitimately match on distinct clusters. The context is
    # bound to system_identifier + actual database OID/name + same schema.
    if source['context'] == target['context']:
        _fail('context_not_distinct')
    if any(source[key] != target[key] for key in ('schema', 'data', 'users', 'clients')):
        _fail('inventory_mismatch')


async def collect_restore_evidence(policy, *, source_dsn, target_dsn, source_schema, target_schema, backup_path):
    """Collect real snapshots. Caller separately proves central schema-owner authority."""
    try:
        if not isinstance(policy, MigrationPolicy):
            _fail('policy_invalid')
        _dsn(source_dsn)
        _dsn(target_dsn)
        _schemas(source_schema, target_schema)
        backup_hash = hashlib.sha256()
        with _private_file(backup_path, MAX_BACKUP_BYTES) as stream:
            read_bytes = 0
            while block := stream.read(1024 * 1024):
                read_bytes += len(block)
                if read_bytes > MAX_BACKUP_BYTES:
                    _fail('private_file_invalid')
                backup_hash.update(block)
        if backup_hash.hexdigest() != policy.approved_backup_sha256:
            _fail('backup_mismatch')
        async with asyncio.timeout(180), AsyncExitStack() as stack:
            inventories = []
            for dsn, schema in ((source_dsn, source_schema), (target_dsn, target_schema)):
                conn = await stack.enter_async_context(await psycopg.AsyncConnection.connect(dsn,
                    connect_timeout=5, options='-c statement_timeout=15000 -c lock_timeout=2000',
                    application_name='easel_identity_restore_collector'))
                inventories.append(await _inventory(conn, schema, policy.tenant_id))
            source, target = inventories
            _matching_inventories(source, target)
            if not set(policy.client_ids) <= set(source['clients']):
                _fail('required_clients_missing')
            return VerifiedRestoreEvidence(policy_sha256=policy.sha256, backup_sha256=backup_hash.hexdigest(),
                schema_sha256=source['schema'], data_sha256=source['data'], source_context_sha256=source['context'],
                target_context_sha256=target['context'], tenant_id=policy.tenant_id, user_ids=source['users'],
                client_ids=source['clients'], verified_at=datetime.now(timezone.utc))
    except RestoreEvidenceError:
        raise
    except (psycopg.Error, OSError, ValueError, TypeError, KeyError, TimeoutError):
        raise RestoreEvidenceError('restore_collection_failed') from None
