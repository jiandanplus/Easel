"""Independent Console role: read business metadata and narrowly mutate catalogs."""
from psycopg import sql

from .errors import PlatformError
from .permissions import CONSOLE_FUNCTIONS, assert_safe_runtime_role

SELECT_TABLES = frozenset({
    "console_admins", "console_sessions", "console_login_transactions",
    "console_operations", "console_audit_events", "console_user_notes",
    "users", "tenants", "tenant_members", "billing_accounts", "products", "product_clients",
    "plans", "memberships", "membership_revocations", "credit_lots",
    "credit_holds", "credit_journals", "credit_entries",
    "billing_offers", "billing_offer_withdrawals",
    "execution_models", "execution_model_states",
})
SELECT_COLUMNS = {
    "console_reconciliation_cases": frozenset({"job_id","state","revision","actor_id","reason","snapshot_hash","last_operation_id","created_at","updated_at"}),
    "console_identity_aliases": frozenset({"issuer","subject","admin_id"}),
    "console_plan_grants": frozenset({"id","billing_account_id","plan_id","membership_id","operator_id","reason","credits","starts_at","expires_at","created_at"}),
    "external_identities": frozenset({"user_id","email","email_verified","username"}),
    "identity_sessions": frozenset({"user_id","product_id","created_at","expires_at","revoked_at"}),
    "internal_grants": frozenset({"id","lot_id","membership_id"}),
    "billing_orders": frozenset({"id","product_id","billing_account_id","channel","mode","amount_minor",
        "currency","status","credit_state","revision","created_at","updated_at","paid_at","expires_at","offer"}),
    "billing_refunds": frozenset({"id","order_id","amount_minor","status","created_at","updated_at"}),
    "execution_jobs": frozenset({"id","model","product_id","billing_account_id","status","amount","actual",
        "funding_mode","created_at","updated_at","model_definition","reservation_id"}),
    "media_jobs": frozenset({"reservation_id","model"}),
    "execution_receipts": frozenset({"job_id","input_tokens","output_tokens"}),
    "credit_reservations": frozenset({"id","billing_account_id","product_id","operation","rate_version",
        "amount","actual","status","funding_mode","created_at","updated_at"}),
}
INSERT_TABLES = frozenset({"console_login_transactions","console_login_requests","console_sessions",
    "console_operations","console_audit_events","console_user_notes","plans","billing_offers","billing_offer_withdrawals",
    "execution_models","execution_model_states"})
INSERT_COLUMNS = {
    "console_reconciliation_cases": frozenset({"job_id","state","revision","actor_id","reason","snapshot_hash","last_operation_id","created_at","updated_at"}),
}
UPDATE_COLUMNS = {
    "console_reconciliation_cases": frozenset({"state","revision","actor_id","reason","snapshot_hash","last_operation_id","updated_at"}),
    "console_login_transactions": frozenset({"consumed"}),
    "console_sessions": frozenset({"revoked_at"}),
    "console_user_notes": frozenset({"note","tags","updated_by","updated_at"}),
    "execution_model_states": frozenset({"enabled","operator_id","updated_at"}),
}
# SELECT FOR UPDATE inside existing immutable-plan publication requires an UPDATE
# privilege only when used; catalog publication does not lock/write old plans.


async def _context(conn,role):
    try:
        await assert_safe_runtime_role(conn,role,check_grants=False)
    except PlatformError:
        raise PlatformError(503,"unsafe_console_role") from None
    row=await (await conn.execute('SELECT current_user AS role,session_user AS session_role,current_schema() AS schema')).fetchone()
    names=[role] if role is not None else [row['role'],row['session_role']]
    roles=await (await conn.execute("""WITH RECURSIVE reachable(oid) AS (
        SELECT oid FROM pg_roles WHERE rolname=ANY(%s)
        UNION SELECT m.roleid FROM pg_auth_members m JOIN reachable r ON m.member=r.oid)
        SELECT rolname FROM pg_roles WHERE oid IN (SELECT oid FROM reachable)""",(names,))).fetchall()
    return row['schema'],[r['rolname'] for r in roles]


async def assert_safe_console_role(conn,role=None,check_grants=True):
    schema,names=await _context(conn,role)
    if not check_grants:return
    audit_sequence=(await (await conn.execute("SELECT pg_get_serial_sequence(%s,'id')::regclass::oid AS oid",
        (schema+'.console_audit_events',))).fetchone())['oid']
    for name in names:
        if (await (await conn.execute("SELECT has_schema_privilege(%s,%s,'CREATE') AS unsafe",(name,schema))).fetchone())['unsafe']:
            raise PlatformError(503,'unsafe_console_role')
        sequence_access=await (await conn.execute("""SELECT EXISTS(
            SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=%s AND c.relkind='S' AND (
                has_sequence_privilege(%s,c.oid,'SELECT,UPDATE') OR
                (c.oid<>%s AND has_sequence_privilege(%s,c.oid,'USAGE')))) AS unsafe""",
            (schema,name,audit_sequence,name))).fetchone()
        if sequence_access['unsafe']:raise PlatformError(503,'unsafe_console_role')
        # Column checks detect inherited and column-only accidental privileges.
        privileges=await (await conn.execute("""SELECT c.relname,a.attname,
            has_column_privilege(%s,c.oid,a.attnum,'SELECT') AS can_select,
            has_column_privilege(%s,c.oid,a.attnum,'INSERT') AS can_insert,
            has_column_privilege(%s,c.oid,a.attnum,'UPDATE') AS can_update,
            has_column_privilege(%s,c.oid,a.attnum,'REFERENCES') AS can_reference,
             has_table_privilege(%s,c.oid,'SELECT') AS table_select,
             has_table_privilege(%s,c.oid,'INSERT') AS table_insert,
            has_table_privilege(%s,c.oid,'DELETE,TRUNCATE,TRIGGER') AS destructive
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid
            WHERE n.nspname=%s AND c.relkind IN ('r','p') AND a.attnum>0 AND NOT a.attisdropped""",
            (name,name,name,name,name,name,name,schema))).fetchall()
        for row in privileges:
            table,column=row['relname'],row['attname']
            if (row['destructive'] or row['can_reference']
                    or (row['table_select'] and table not in SELECT_TABLES)
                    or (row['table_insert'] and table not in INSERT_TABLES)
                    or (row['can_select'] and table not in SELECT_TABLES and column not in SELECT_COLUMNS.get(table,()))
                    or (row['can_insert'] and table not in INSERT_TABLES and column not in INSERT_COLUMNS.get(table,()))
                    or (row['can_update'] and column not in UPDATE_COLUMNS.get(table,()))):
                raise PlatformError(503,'unsafe_console_role')


async def grant_console_permissions(conn,role):
    if not isinstance(role,str) or not 1<=len(role)<=63:raise PlatformError(422,'invalid_console_role')
    await assert_safe_console_role(conn,role,check_grants=False)
    schema=(await (await conn.execute('SELECT current_schema() AS schema')).fetchone())['schema']
    ident,namespace=sql.Identifier(role),sql.Identifier(schema)
    await conn.execute(sql.SQL('REVOKE ALL ON SCHEMA {} FROM {}').format(namespace,ident))
    await conn.execute(sql.SQL('REVOKE ALL ON ALL TABLES IN SCHEMA {} FROM {}').format(namespace,ident))
    await conn.execute(sql.SQL('REVOKE ALL ON ALL SEQUENCES IN SCHEMA {} FROM {}').format(namespace,ident))
    # REVOKE table ALL does not remove independent column grants.
    columns=await (await conn.execute("""SELECT c.relname,array_agg(a.attname ORDER BY a.attnum) AS columns
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid
        WHERE n.nspname=%s AND c.relkind IN ('r','p') AND a.attnum>0 AND NOT a.attisdropped GROUP BY c.relname""",(schema,))).fetchall()
    for row in columns:
        await conn.execute(sql.SQL('REVOKE ALL ({}) ON {}.{} FROM {}').format(
            sql.SQL(',').join(map(sql.Identifier,row['columns'])),namespace,sql.Identifier(row['relname']),ident))
    await conn.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(namespace,ident))
    existing={row['relname'] for row in columns}
    for table in sorted(SELECT_TABLES & existing):
        await conn.execute(sql.SQL('GRANT SELECT ON {}.{} TO {}').format(namespace,sql.Identifier(table),ident))
    for table,allowed in SELECT_COLUMNS.items():
        if table in existing:
            await conn.execute(sql.SQL('GRANT SELECT ({}) ON {}.{} TO {}').format(sql.SQL(',').join(map(sql.Identifier,sorted(allowed))),namespace,sql.Identifier(table),ident))
    for table in sorted(INSERT_TABLES & existing):
        await conn.execute(sql.SQL('GRANT INSERT ON {}.{} TO {}').format(namespace,sql.Identifier(table),ident))
    for table,allowed in INSERT_COLUMNS.items():
        if table in existing:
            await conn.execute(sql.SQL('GRANT INSERT ({}) ON {}.{} TO {}').format(
                sql.SQL(',').join(map(sql.Identifier,sorted(allowed))),namespace,sql.Identifier(table),ident))
    for table,allowed in UPDATE_COLUMNS.items():
        if table in existing:
            await conn.execute(sql.SQL('GRANT UPDATE ({}) ON {}.{} TO {}').format(sql.SQL(',').join(map(sql.Identifier,sorted(allowed))),namespace,sql.Identifier(table),ident))
    # Only the operations audit sequence is needed, never arbitrary sequences.
    seq=await (await conn.execute("SELECT pg_get_serial_sequence(%s,'id') AS name",(schema+'.console_audit_events',))).fetchone()
    if seq and seq['name']:
        await conn.execute(sql.SQL('GRANT USAGE ON SEQUENCE {} TO {}').format(sql.Identifier(*seq['name'].split('.')),ident))
    # The web owner allowance goes through one audited SECURITY DEFINER function (024).
    for function in CONSOLE_FUNCTIONS:
        if (await (await conn.execute('SELECT to_regprocedure(%s) IS NOT NULL AS present',(schema+'.'+function,))).fetchone())['present']:
            await conn.execute(sql.SQL('GRANT EXECUTE ON FUNCTION {}.{} TO {}').format(namespace,sql.SQL(function),ident))
    await assert_safe_console_role(conn,role)
