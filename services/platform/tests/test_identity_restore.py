"""Actual read-only inventories of fresh synthetic IdP schemas; no real identities."""
from dataclasses import replace
import hashlib
import importlib
import importlib.util
import json
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import pytest
import pytest_asyncio


def module():
    assert importlib.util.find_spec('platform_service.identity_restore') is not None, 'restore collector is not implemented'
    return importlib.import_module('platform_service.identity_restore')


def config_value(backup):
    return {'policy': {'environment': 'synthetic-restore', 'source_issuer': 'http://127.0.0.1:18763/oidc',
            'target_issuer': 'https://identity.example/oidc', 'tenant_id': 'default',
            'client_ids': ['console-client', 'product-client'],
            'approved_backup_sha256': hashlib.sha256(backup.read_bytes()).hexdigest()},
        'source_dsn': 'host=127.0.0.1 dbname=source user=synthetic password=PRIVATE-CANARY',
        'target_dsn': 'host=127.0.0.1 dbname=target user=synthetic password=PRIVATE-CANARY',
        'source_schema': 'logto', 'target_schema': 'logto', 'backup_path': str(backup)}


@pytest.fixture
def private_files(tmp_path):
    backup = tmp_path / 'approved.dump'
    backup.write_bytes(b'SYNTHETIC APPROVED BACKUP ONLY')
    backup.chmod(0o600)
    path = tmp_path / 'restore.json'
    path.write_text(json.dumps(config_value(backup)))
    path.chmod(0o600)
    return path, backup


def test_private_parser_has_exact_contract_and_secret_safe_repr(private_files):
    path, backup = private_files
    value = module().read_private_restore_config(path)
    assert value.policy.client_ids == ('console-client', 'product-client')
    assert value.backup_path == str(backup)
    assert 'PRIVATE-CANARY' not in repr(value)
    assert str(backup) not in repr(value)


@pytest.mark.parametrize('mutation', ['mode', 'symlink', 'extra', 'duplicate', 'relative_backup', 'schema', 'schema_mismatch', 'dsn_service', 'dsn_options'])
def test_private_parser_rejects_untrusted_input(private_files, mutation):
    path, backup = private_files
    if mutation == 'mode': path.chmod(0o644)
    elif mutation == 'symlink':
        link = path.with_name('link.json'); link.symlink_to(path); path = link
    elif mutation == 'duplicate':
        path.write_text('{"policy":{},"policy":{}}')
    else:
        value = config_value(backup)
        if mutation == 'extra': value['proof'] = {'verified': True}
        if mutation == 'relative_backup': value['backup_path'] = 'approved.dump'
        if mutation == 'schema': value['source_schema'] = 'public; DROP SCHEMA public'
        if mutation == 'schema_mismatch': value['target_schema'] = 'other'
        if mutation == 'dsn_service': value['source_dsn'] = 'service=unexpected'
        if mutation == 'dsn_options': value['source_dsn'] += ' options=-crow_security=on'
        path.write_text(json.dumps(value))
    with pytest.raises(Exception) as caught:
        module().read_private_restore_config(path)
    assert 'PRIVATE-CANARY' not in str(caught.value)
    assert 'restore_' in str(caught.value)


@pytest_asyncio.fixture
async def inventories(test_dsn, private_files):
    names = ['easel_s1_test_idp_' + uuid4().hex for _ in range(2)]
    path, backup = private_files
    opened, created = [], []
    async with await psycopg.AsyncConnection.connect(test_dsn, autocommit=True) as control:
        try:
            for name in names:
                await control.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
                created.append(name)
                dsn = make_conninfo(test_dsn, dbname=name)
                conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
                opened.append(conn)
                await conn.execute('CREATE SCHEMA logto')
                for statement in [
                    'CREATE TABLE logto.users (tenant_id text NOT NULL,id text NOT NULL,password_hash text,PRIMARY KEY(tenant_id,id))',
                    'CREATE TABLE logto.applications (tenant_id text NOT NULL,id text NOT NULL,secret text,PRIMARY KEY(tenant_id,id))',
                    'CREATE TABLE logto.settings (key text PRIMARY KEY,value jsonb NOT NULL)',
                ]:
                    await conn.execute(statement)
                await conn.execute("INSERT INTO logto.users VALUES ('default','user-one','PASSWORD-ROW-CANARY'),('other','not-a-candidate','PRIVATE')")
                await conn.execute("INSERT INTO logto.applications VALUES ('default','console-client','SECRET-ROW-CANARY'),('default','product-client','PRIVATE')")
                await conn.execute("INSERT INTO logto.settings VALUES ('version','{\"v\":1}')")
            value = config_value(backup)
            value.update(source_dsn=make_conninfo(test_dsn,dbname=names[0]),target_dsn=make_conninfo(test_dsn,dbname=names[1]))
            path.write_text(json.dumps(value))
            yield opened, path, backup, 'logto', 'logto', value
        finally:
            for conn in opened: await conn.close()
            for name in created:
                await control.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))


async def collect(inventories, **overrides):
    _, path, _, _, _, _ = inventories
    config = module().read_private_restore_config(path)
    kwargs = {name: getattr(config, name) for name in ('source_dsn','target_dsn','source_schema','target_schema','backup_path')}
    kwargs.update(overrides)
    return await module().collect_restore_evidence(config.policy, **kwargs)


@pytest.mark.asyncio
async def test_real_complete_readonly_inventory_and_tenant_scoped_candidates(inventories):
    connections, path, _, source, target, _ = inventories
    before = []
    for conn, schema in zip(connections, (source, target)):
        before.append(await (await conn.execute(sql.SQL('SELECT md5(json_agg(t ORDER BY id)::text) FROM {}.users t').format(sql.Identifier(schema)))).fetchone())
    evidence = await collect(inventories)
    assert evidence.user_ids == ('user-one',)
    assert evidence.client_ids == ('console-client','product-client')
    assert evidence.source_context_sha256 != evidence.target_context_sha256
    assert evidence.policy_sha256 == module().read_private_restore_config(path).policy.sha256
    assert len(evidence.schema_sha256) == len(evidence.data_sha256) == 64
    assert evidence.verified_at.utcoffset().total_seconds() == 0
    assert 'user-one' not in repr(evidence)
    after = []
    for conn, schema in zip(connections, (source, target)):
        after.append(await (await conn.execute(sql.SQL('SELECT md5(json_agg(t ORDER BY id)::text) FROM {}.users t').format(sql.Identifier(schema)))).fetchone())
    assert before == after


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['same_context','wrong_backup','backup_mode','backup_symlink','row','other_tenant','missing_table','missing_column','extra_table','missing_client','empty_users','partition','type','oversize_rows','oversize_bytes'])
async def test_real_inventory_rejects_incomplete_or_unsupported_evidence(inventories, mutation, monkeypatch):
    connections, _, backup, source, target, value = inventories
    conn = connections[1]
    overrides = {}
    if mutation == 'same_context': overrides['target_dsn'] = value['source_dsn']
    elif mutation == 'wrong_backup': backup.write_bytes(b'CHANGED')
    elif mutation == 'backup_mode': backup.chmod(0o644)
    elif mutation == 'backup_symlink':
        link = backup.with_name('link.dump'); link.symlink_to(backup); overrides['backup_path'] = str(link)
    elif mutation == 'row':
        await conn.execute(sql.SQL("UPDATE {}.users SET password_hash='CHANGED' WHERE id='user-one'").format(sql.Identifier(target)))
    elif mutation == 'other_tenant':
        await conn.execute(sql.SQL("UPDATE {}.users SET password_hash='CHANGED' WHERE tenant_id='other'").format(sql.Identifier(target)))
    elif mutation in ('missing_client','empty_users'):
        for conn, schema in zip(connections, (source,target)):
            statement = "DELETE FROM {}.applications WHERE id='product-client'" if mutation == 'missing_client' else "DELETE FROM {}.users"
            await conn.execute(sql.SQL(statement).format(sql.Identifier(schema)))
    elif mutation == 'oversize_rows': monkeypatch.setattr(module(), 'MAX_ROWS', 2)
    elif mutation == 'oversize_bytes': monkeypatch.setattr(module(), 'MAX_DATA_BYTES', 8)
    else:
        statements = {
            'missing_table': 'DROP TABLE {}.settings',
            'missing_column': 'ALTER TABLE {}.users DROP COLUMN password_hash',
            'extra_table': 'CREATE TABLE {}.extra (value text)',
            'view': 'CREATE VIEW {}.extra AS SELECT 1 AS value',
            'sequence': 'CREATE SEQUENCE {}.extra',
            'function': "CREATE FUNCTION {}.extra() RETURNS int LANGUAGE sql AS 'SELECT 1'",
            'trigger': "CREATE FUNCTION {}.extra() RETURNS trigger LANGUAGE plpgsql AS 'BEGIN RETURN NEW; END'",
            'partition': 'CREATE TABLE {}.extra(value int) PARTITION BY RANGE(value)',
            'type': "CREATE DOMAIN {}.extra AS text CHECK(length(VALUE)>0)",
        }
        for conn, schema in zip(connections, (source,target)):
            await conn.execute(sql.SQL(statements[mutation]).format(sql.Identifier(schema)))
            if mutation == 'trigger':
                await conn.execute(sql.SQL('CREATE TRIGGER extra BEFORE INSERT ON {}.users FOR EACH ROW EXECUTE FUNCTION {}.extra()').format(sql.Identifier(schema),sql.Identifier(schema)))
            if mutation in ('missing_table','missing_column','extra_table'): break
    with pytest.raises(Exception) as caught:
        await collect(inventories, **overrides)
    assert 'restore_' in str(caught.value)
    assert 'CANARY' not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize('denial', ['rls','select','control_permission','none'])
async def test_actual_low_role_cannot_prove_hidden_or_unreadable_tables(inventories, denial):
    connections, _, _, source, target, value = inventories
    role = 'restore_role_' + uuid4().hex
    password = uuid4().hex
    await connections[0].execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(role),sql.Literal(password)))
    try:
        for conn, schema in zip(connections,(source,target)):
            await conn.execute('REVOKE EXECUTE ON FUNCTION pg_control_system() FROM PUBLIC')
            if denial != 'control_permission':
                await conn.execute(sql.SQL('GRANT EXECUTE ON FUNCTION pg_control_system() TO {}').format(sql.Identifier(role)))
            await conn.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(schema),sql.Identifier(role)))
            await conn.execute(sql.SQL('GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}').format(sql.Identifier(schema),sql.Identifier(role)))
            if denial == 'rls':
                await conn.execute(sql.SQL('ALTER TABLE {}.users ENABLE ROW LEVEL SECURITY').format(sql.Identifier(schema)))
            elif denial == 'select':
                await conn.execute(sql.SQL('REVOKE SELECT ON {}.settings FROM {}').format(sql.Identifier(schema),sql.Identifier(role)))
        kwargs = {'source_dsn': make_conninfo(value['source_dsn'],user=role,password=password),
                  'target_dsn': make_conninfo(value['target_dsn'],user=role,password=password)}
        if denial == 'none':
            result = await collect(inventories, **kwargs)
            assert result.user_ids == ('user-one',)
        else:
            with pytest.raises(Exception) as caught:
                await collect(inventories, **kwargs)
            assert str(caught.value) == ('restore_context_unavailable' if denial == 'control_permission' else 'restore_visibility_incomplete')
            assert password not in str(caught.value)
    finally:
        for conn in connections:
            await conn.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
        await connections[0].execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


@pytest.mark.asyncio
async def test_real_logto_objects_are_compared_without_executing_view_or_trigger(inventories):
    connections, _, _, _, _, _ = inventories
    for conn in connections:
        await conn.execute('CREATE SEQUENCE logto.counter START 4')
        await conn.execute("CREATE FUNCTION logto.touch() RETURNS trigger LANGUAGE plpgsql AS 'BEGIN RETURN NEW; END'")
        await conn.execute('CREATE TRIGGER touched BEFORE UPDATE ON logto.users FOR EACH ROW EXECUTE FUNCTION logto.touch()')
        await conn.execute('ALTER TABLE logto.users ENABLE ROW LEVEL SECURITY')
        await conn.execute("CREATE POLICY tenant_policy ON logto.users USING(tenant_id=current_setting('app.tenant_id',true))")
        await conn.execute("CREATE VIEW logto.only_default AS SELECT id,nextval('logto.counter') FROM logto.users WHERE tenant_id='default'")
    before = await collect(inventories)
    assert before.user_ids == ('user-one',)
    for conn in connections:
        assert await (await conn.execute('SELECT last_value,is_called FROM logto.counter')).fetchone() == (4,False)
    await connections[1].execute("SELECT nextval('logto.counter')")
    with pytest.raises(Exception,match='restore_'):
        await collect(inventories)


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['function','view','policy','index','sequence_definition','sequence_ownership','trigger_state'])
async def test_real_schema_definition_changes_rejected(inventories, change):
    connections, _, _, _, _, _ = inventories
    statements = {
        'function': "CREATE FUNCTION logto.f() RETURNS int LANGUAGE sql AS 'SELECT 1'",
        'view': 'CREATE VIEW logto.v AS SELECT 1 AS value',
        'policy': 'CREATE POLICY p ON logto.users USING(true)',
        'index': 'CREATE INDEX additional ON logto.users(id)',
        'sequence_definition': 'ALTER SEQUENCE logto.s INCREMENT BY 2',
        'sequence_ownership': 'ALTER SEQUENCE logto.s OWNED BY logto.users.id',
        'trigger_state': 'ALTER TABLE logto.users DISABLE TRIGGER t',
    }
    for conn in connections:
        if change in ('sequence_definition','sequence_ownership'): await conn.execute('CREATE SEQUENCE logto.s')
        if change == 'trigger_state':
            await conn.execute("CREATE FUNCTION logto.f() RETURNS trigger LANGUAGE plpgsql AS 'BEGIN RETURN NEW; END'")
            await conn.execute('CREATE TRIGGER t BEFORE UPDATE ON logto.users FOR EACH ROW EXECUTE FUNCTION logto.f()')
    await connections[1].execute(statements[change])
    with pytest.raises(Exception,match='restore_'):
        await collect(inventories)


@pytest.mark.asyncio
async def test_catalog_inventory_is_bounded(inventories, monkeypatch):
    monkeypatch.setattr(module(), 'MAX_OBJECTS', 2)
    with pytest.raises(Exception, match='restore_schema_limit'):
        await collect(inventories)


def test_invalid_policy_error_is_sanitized(private_files):
    path, _ = private_files
    value = json.loads(path.read_text())
    value['policy']['target_issuer'] = 'http://UNTRUSTED-CANARY/oidc'
    path.write_text(json.dumps(value))
    with pytest.raises(Exception) as caught:
        module().read_private_restore_config(path)
    assert str(caught.value) == 'restore_config_invalid'


@pytest.mark.parametrize('same_context', [True,False])
def test_context_identity_not_database_name_decides_distinctness(same_context):
    source = {'database': 'logto', 'context': 'a'*64, 'schema': 'c'*64, 'data': 'd'*64,
              'users': ('u',), 'clients': ('c',)}
    target = {**source, 'context': 'a'*64 if same_context else 'b'*64}
    if same_context:
        with pytest.raises(module().RestoreEvidenceError, match='restore_context_not_distinct'):
            module()._matching_inventories(source, target)
    else:
        module()._matching_inventories(source, target)


@pytest_asyncio.fixture
async def enum_inventories(inventories):
    connections,*_=inventories
    for conn in connections:
        await conn.execute("CREATE TYPE logto.sign_in_mode AS ENUM ('SignIn','Register','Combined')")
        await conn.execute("CREATE TYPE logto.unused_marker AS ENUM ('First','Last')")
        await conn.execute('CREATE TYPE logto.empty_unused AS ENUM ()')
        await conn.execute("ALTER TABLE logto.users ADD COLUMN mode logto.sign_in_mode NOT NULL DEFAULT 'SignIn'")
        await conn.execute("ALTER TABLE logto.users ADD COLUMN modes logto.sign_in_mode[] NOT NULL DEFAULT ARRAY['SignIn','Register']::logto.sign_in_mode[]")
    return inventories


@pytest.mark.asyncio
async def test_native_enums_and_generated_arrays_compare_across_distinct_oids(enum_inventories):
    connections,*_=enum_inventories
    oids=[]
    for conn in connections:
        oids.append((await (await conn.execute("SELECT 'logto.sign_in_mode'::regtype::oid")).fetchone())[0])
    assert oids[0]!=oids[1]
    result=await collect(enum_inventories)
    assert result.user_ids==('user-one',) and len(result.schema_sha256)==64


@pytest.mark.asyncio
async def test_enum_logical_order_survives_restore_sort_number_reassignment(enum_inventories):
    connections,*_=enum_inventories
    # A logical pg_dump restore recreates this order with sequential sort numbers.
    # Do not compare enumsortorder floats or label OIDs as portable identity.
    await connections[0].execute("ALTER TYPE logto.unused_marker ADD VALUE 'Middle' AFTER 'First'")
    await connections[1].execute('DROP TYPE logto.unused_marker')
    await connections[1].execute("CREATE TYPE logto.unused_marker AS ENUM ('First','Middle','Last')")
    before=[]
    for conn in connections:
        before.append(await (await conn.execute("SELECT e.enumsortorder FROM pg_enum e WHERE e.enumtypid='logto.unused_marker'::regtype ORDER BY e.enumsortorder")).fetchall())
    assert before[0]!=before[1]
    assert (await collect(enum_inventories)).client_ids==('console-client','product-client')


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation',['add_label','rename_unused_label','reorder_unused','extra_unused',
    'missing_unused','rename_type','different_schema','array_element','foreign_same_name','owner','enum_acl'])
async def test_enum_definition_only_differences_and_scope_are_rejected(enum_inventories,mutation):
    connections,*_=enum_inventories;target=connections[1]
    role=None
    try:
        if mutation=='add_label':await target.execute("ALTER TYPE logto.unused_marker ADD VALUE 'Extra'")
        elif mutation=='rename_unused_label':await target.execute("ALTER TYPE logto.sign_in_mode RENAME VALUE 'Combined' TO 'Changed'")
        elif mutation=='reorder_unused':
            await target.execute('DROP TYPE logto.unused_marker')
            await target.execute("CREATE TYPE logto.unused_marker AS ENUM ('Last','First')")
        elif mutation=='extra_unused':await target.execute("CREATE TYPE logto.additional_unused AS ENUM ('Extra')")
        elif mutation=='missing_unused':await target.execute('DROP TYPE logto.empty_unused')
        elif mutation=='rename_type':await target.execute('ALTER TYPE logto.unused_marker RENAME TO another_name')
        elif mutation=='different_schema':
            await target.execute('CREATE SCHEMA foreign_types')
            await target.execute('ALTER TYPE logto.unused_marker SET SCHEMA foreign_types')
        elif mutation=='array_element':
            await target.execute("CREATE TYPE logto.other_mode AS ENUM ('SignIn','Register','Combined')")
            await target.execute('ALTER TABLE logto.users ALTER COLUMN modes DROP DEFAULT')
            await target.execute('ALTER TABLE logto.users ALTER COLUMN modes TYPE logto.other_mode[] USING modes::text::logto.other_mode[]')
        elif mutation=='foreign_same_name':
            await target.execute('CREATE SCHEMA foreign_types')
            await target.execute("CREATE TYPE foreign_types.sign_in_mode AS ENUM ('SignIn','Register','Combined')")
            await target.execute('ALTER TABLE logto.users ALTER COLUMN mode DROP DEFAULT')
            await target.execute('ALTER TABLE logto.users ALTER COLUMN mode TYPE foreign_types.sign_in_mode USING mode::text::foreign_types.sign_in_mode')
        else:
            role='enum_fixture_'+uuid4().hex
            await target.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
            if mutation=='owner':await target.execute(sql.SQL('ALTER TYPE logto.unused_marker OWNER TO {}').format(sql.Identifier(role)))
            else:
                type_name='sign_in_mode'
                await target.execute(sql.SQL('GRANT USAGE ON TYPE {}.{} TO {}').format(sql.Identifier('logto'),sql.Identifier(type_name),sql.Identifier(role)))
        with pytest.raises(module().RestoreEvidenceError) as caught:await collect(enum_inventories)
        assert str(caught.value) in ('restore_inventory_mismatch','restore_unsupported_type')
    finally:
        if role:
            for conn in connections:await conn.execute(sql.SQL('DROP OWNED BY {} CASCADE').format(sql.Identifier(role)))
            await target.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


@pytest.mark.asyncio
@pytest.mark.parametrize('definition',[
    'CREATE DOMAIN logto.unsupported AS text CHECK(length(VALUE)>0)',
    'CREATE TYPE logto.unsupported AS RANGE (subtype=integer)',
    'CREATE TYPE logto.unsupported AS (value text)',
])
async def test_enum_support_does_not_admit_domains_ranges_or_composites(enum_inventories,definition):
    connections,*_=enum_inventories
    for conn in connections:await conn.execute(definition)
    with pytest.raises(module().RestoreEvidenceError,match='restore_unsupported_'):
        await collect(enum_inventories)


@pytest.mark.asyncio
async def test_foreign_enum_array_is_rejected_even_with_identical_names_and_rows(enum_inventories):
    connections,*_=enum_inventories
    for conn in connections:
        await conn.execute('CREATE SCHEMA foreign_types')
        await conn.execute("CREATE TYPE foreign_types.sign_in_mode AS ENUM ('SignIn','Register','Combined')")
        await conn.execute('ALTER TABLE logto.users ALTER COLUMN modes DROP DEFAULT')
        await conn.execute('ALTER TABLE logto.users ALTER COLUMN modes TYPE foreign_types.sign_in_mode[] USING modes::text::foreign_types.sign_in_mode[]')
    with pytest.raises(module().RestoreEvidenceError,match='restore_unsupported_type'):
        await collect(enum_inventories)


@pytest.mark.asyncio
async def test_generated_array_acl_cannot_be_granted_independently_of_enum(enum_inventories):
    connections,*_=enum_inventories;target=connections[1]
    role='enum_array_fixture_'+uuid4().hex
    await target.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
    try:
        with pytest.raises(psycopg.errors.InvalidGrantOperation):
            await target.execute(sql.SQL('GRANT USAGE ON TYPE logto._sign_in_mode TO {}').format(sql.Identifier(role)))
        # PostgreSQL enforces array privileges through the element type. No raw
        # pg_catalog mutation is used to fabricate an impossible array ACL.
        before=await collect(enum_inventories)
        await target.execute(sql.SQL('GRANT USAGE ON TYPE logto.sign_in_mode TO {}').format(sql.Identifier(role)))
        with pytest.raises(module().RestoreEvidenceError,match='restore_inventory_mismatch'):
            await collect(enum_inventories)
        assert len(before.schema_sha256)==64
    finally:
        for conn in connections:await conn.execute(sql.SQL('DROP OWNED BY {} CASCADE').format(sql.Identifier(role)))
        await target.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


@pytest.mark.parametrize('kind,default_code', [('relation', "acldefault('r'"), ('function', "acldefault('f'"), ('enum', "acldefault('T'")])
def test_acl_sql_contract_preserves_complete_grants_and_empty_acl(kind, default_code):
    collector=module()
    assert callable(getattr(collector,'_acl_sql',None)), 'canonical ACL collector expression is missing'
    expression=collector._acl_sql(kind)
    assert 'aclexplode(COALESCE(' in expression and default_code in expression
    assert 'jsonb_agg(' in expression and "'[]'::jsonb" in expression
    assert 'pg_get_userbyid(a.grantor)' in expression
    assert "WHEN a.grantee=0 THEN 'PUBLIC' ELSE 'ROLE'" in expression
    assert 'a.privilege_type' in expression and 'a.is_grantable' in expression
    assert 'NULLIF' not in expression and 'has_table_privilege' not in expression
    if kind=='relation':assert "acldefault('s'" in expression


def test_acl_sql_contract_rejects_unknown_catalog_kind():
    collector=module()
    assert callable(getattr(collector,'_acl_sql',None)), 'canonical ACL collector expression is missing'
    with pytest.raises(collector.RestoreEvidenceError,match='restore_'):
        collector._acl_sql('untrusted catalog expression')


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['table','view','sequence','function','enum'])
async def test_null_acl_and_explicit_default_owner_acl_are_equivalent(inventories,kind):
    connections,*_=inventories
    statements={
        'table': ('CREATE TABLE logto.acl_object(value int)', 'GRANT ALL PRIVILEGES ON TABLE logto.acl_object TO CURRENT_USER', "SELECT relacl IS NULL FROM pg_class WHERE oid='logto.acl_object'::regclass"),
        'view': ('CREATE VIEW logto.acl_object AS SELECT 1 AS value', 'GRANT ALL PRIVILEGES ON TABLE logto.acl_object TO CURRENT_USER', "SELECT relacl IS NULL FROM pg_class WHERE oid='logto.acl_object'::regclass"),
        'sequence': ('CREATE SEQUENCE logto.acl_object', 'GRANT ALL PRIVILEGES ON SEQUENCE logto.acl_object TO CURRENT_USER', "SELECT relacl IS NULL FROM pg_class WHERE oid='logto.acl_object'::regclass"),
        'function': ("CREATE FUNCTION logto.acl_object() RETURNS int LANGUAGE sql AS 'SELECT 1'", 'GRANT ALL PRIVILEGES ON FUNCTION logto.acl_object() TO CURRENT_USER', "SELECT proacl IS NULL FROM pg_proc WHERE oid='logto.acl_object()'::regprocedure"),
        'enum': ("CREATE TYPE logto.acl_object AS ENUM ('one','two')", 'GRANT ALL PRIVILEGES ON TYPE logto.acl_object TO CURRENT_USER', "SELECT typacl IS NULL FROM pg_type WHERE oid='logto.acl_object'::regtype"),
    }
    create,grant,probe=statements[kind]
    for c in connections:await c.execute(create)
    await connections[0].execute(grant)
    states=[(await (await c.execute(probe)).fetchone())[0] for c in connections]
    assert states==[False,True], 'fixture must reproduce explicit/default catalog representations'
    assert len((await collect(inventories)).schema_sha256)==64


@pytest.mark.asyncio
async def test_acl_grant_order_is_normalized_but_grant_option_is_not(inventories):
    connections,*_=inventories
    roles=['acl_order_'+uuid4().hex for _ in range(2)]
    try:
        for role in roles:await connections[0].execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
        for c,order in zip(connections,(roles,list(reversed(roles)))):
            for role in order:await c.execute(sql.SQL('GRANT SELECT ON logto.settings TO {}').format(sql.Identifier(role)))
        raw=[(await (await c.execute("SELECT relacl::text FROM pg_class WHERE oid='logto.settings'::regclass")).fetchone())[0] for c in connections]
        assert raw[0]!=raw[1]
        assert len((await collect(inventories)).schema_sha256)==64
        await connections[1].execute(sql.SQL('GRANT SELECT ON logto.settings TO {} WITH GRANT OPTION').format(sql.Identifier(roles[0])))
        with pytest.raises(module().RestoreEvidenceError,match='restore_inventory_mismatch'):await collect(inventories)
    finally:
        for role in roles:
            for c in connections:await c.execute(sql.SQL('DROP OWNED BY {} CASCADE').format(sql.Identifier(role)))
            await connections[0].execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['empty_owner_acl','public_grant','owner_select_revoked'])
async def test_canonical_acl_does_not_hide_changed_grants(inventories,change):
    connections,*_=inventories;target=connections[1]
    if change=='empty_owner_acl':
        await target.execute('REVOKE ALL PRIVILEGES ON logto.settings FROM CURRENT_USER')
        assert (await (await target.execute("SELECT relacl IS NOT NULL AND (SELECT count(*) FROM aclexplode(relacl))=0 FROM pg_class WHERE oid='logto.settings'::regclass")).fetchone())[0]
    elif change=='public_grant':await target.execute('GRANT SELECT ON logto.settings TO PUBLIC')
    else:await target.execute('REVOKE SELECT ON logto.settings FROM CURRENT_USER')
    with pytest.raises(module().RestoreEvidenceError,match='restore_inventory_mismatch'):await collect(inventories)


@pytest.mark.asyncio
async def test_canonical_acl_keeps_grantor_identity(inventories):
    connections,*_=inventories
    roles=['acl_grantor_'+uuid4().hex for _ in range(3)]
    try:
        for role in roles:await connections[0].execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
        for c in connections:
            for grantor in roles[:2]:
                await c.execute(sql.SQL('GRANT USAGE ON SCHEMA logto TO {}').format(sql.Identifier(grantor)))
                await c.execute(sql.SQL('GRANT SELECT ON logto.settings TO {} WITH GRANT OPTION').format(sql.Identifier(grantor)))
        for c,grantor in zip(connections,roles[:2]):
            try:
                await c.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(grantor)))
                await c.execute(sql.SQL('GRANT SELECT ON logto.settings TO {}').format(sql.Identifier(roles[2])))
            finally:await c.execute('RESET ROLE')
        with pytest.raises(module().RestoreEvidenceError,match='restore_inventory_mismatch'):await collect(inventories)
    finally:
        for role in roles:
            for c in connections:await c.execute(sql.SQL('DROP OWNED BY {} CASCADE').format(sql.Identifier(role)))
            await connections[0].execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
