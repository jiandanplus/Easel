"""Explicit migration-owner/runtime-role boundary for the central service."""

from psycopg import sql
from .errors import PlatformError

# INSERT is needed for controlled CLI provisioning and identity creation. Mutations
# of existing immutable history are deliberately absent, even for runtime operators.
INSERT_TABLES = (
    "media_protocol_facts",
    "media_voice_steps", "media_voice_step_observations", "media_voice_registration_proofs",
    "media_blobs", "media_storage_budgets", "media_work", "media_observations", "media_sources",
    "media_invocations", "media_task_tokens", "media_jobs", "media_provider_attempts",
    "media_provider_associations", "media_terminal_evidence", "media_receipts", "media_http_response_references",
    "media_assets", "media_artifacts", "media_voices", "media_blocked_bindings",
    "execution_provider_attempts",
    "execution_provider_associations",
    "execution_terminal_evidence",
    "execution_invocations",
    "execution_task_tokens",
    "execution_jobs",
    "execution_receipts",
    "execution_results",
    "execution_blocked_bindings",
    "users",
    "external_identities",
    "tenants",
    "tenant_members",
    "billing_accounts",
    "plans",
    "memberships",
    "credit_lots",
    "credit_reservations",
    "credit_holds",
    "credit_journals",
    "credit_entries",
    "identity_transactions",
    "identity_handoffs",
    "identity_sessions",
    "identity_tokens",
    "billing_offers",
    "billing_offer_withdrawals",
    "billing_orders",
    "payment_events",
    "payment_event_audits",
    "payment_transactions",
    "billing_refunds",
    "membership_revocations",
    "billing_outbox",
    "payment_orphan_events",
    "payment_refund_transactions",
)
UPDATE_COLUMNS = {
    "media_work": ("phase","due_at","lease_token","lease_until","polls","failures","last_safe_code","updated_at"),
    "media_sources": ("location","metadata","artifact_id","updated_at"),
    "media_blobs": ("state", "attached_id", "expires_at", "purged_at", "deleted_at", "gc_token", "gc_lease_until"),
    "media_storage_budgets": ("remaining_bytes", "remaining_files", "released_at"),
    "media_invocations": ("operation_count", "held", "spent"),
    "media_jobs": ("status", "actual", "execution_state", "delivery_state", "reason", "lease_token", "lease_until", "dispatched_at", "cancel_requested_at", "updated_at"),
    "media_assets": ("purged_at",), "media_artifacts": ("purged_at",),
    "execution_invocations": ("operation_count", "held", "spent"),
    "execution_jobs": (
        "status",
        "actual",
        "reason",
        "lease_until",
        "dispatched_at",
        "updated_at",
    ),
    "execution_results": ("payload",),
    "billing_accounts": ("id",),  # SELECT ... FOR UPDATE requires UPDATE privilege.
    "external_identities": ("email", "email_verified", "username"),
    "credit_lots": ("available", "held", "spent", "expired", "refunded", "frozen"),
    "credit_reservations": ("status", "actual", "completion_key", "updated_at"),
    "identity_transactions": ("state_hash", "nonce_hash", "cookie_hash", "consumed"),
    "identity_handoffs": ("consumed",),
    "identity_sessions": ("revoked_at",),
    "identity_tokens": ("consumed",),
    "billing_orders": (
        "status",
        "credit_state",
        "checkout",
        "membership_id",
        "provider_transaction_id",
        "reconciliation_reason",
        "paid_at",
        "updated_at",
    ),
    "billing_refunds": (
        "status",
        "provider_refund_id",
        "reconciliation_reason",
        "updated_at",
    ),
    "billing_outbox": (
        "status",
        "attempts",
        "due_at",
        "lease_token",
        "lease_until",
        "last_error",
    ),
}
IMMUTABLE_TABLES = (
    "media_protocol_facts",
    "media_voice_steps", "media_voice_step_observations", "media_voice_registration_proofs",
    "media_observations",
    "internal_feature_extensions",
    "media_storage_policy",
    "media_models", "media_model_states", "media_task_tokens", "media_provider_attempts",
    "media_provider_associations", "media_terminal_evidence", "media_receipts", "media_voices", "media_blocked_bindings", "media_http_response_references",
    "execution_provider_attempts",
    "execution_provider_associations",
    "execution_terminal_evidence",
    "internal_grants",
    "execution_models",
    "execution_model_states",
    "execution_receipts",
    "execution_task_tokens",
    "execution_blocked_bindings",
    "plans",
    "memberships",
    "credit_holds",
    "credit_journals",
    "credit_entries",
    "billing_offers",
    "billing_offer_withdrawals",
    "payment_events",
    "payment_event_audits",
    "payment_transactions",
    "membership_revocations",
    "payment_orphan_events",
    "payment_refund_transactions",
)


# Explicit product SELECT authority: future Console/auth tables are never inherited.
SELECT_TABLES = tuple(dict.fromkeys((*INSERT_TABLES, *IMMUTABLE_TABLES, "products", "product_clients")))
SEQUENCE_NAMES = ("credit_entries_id_seq", "payment_event_audits_id_seq")


ALLOWANCE_FUNCTION = "console_issue_owner_allowance(text,text,text,text)"
# Console-only SECURITY DEFINER entry points (migrations 024, 025).
CONSOLE_FUNCTIONS = (
    ALLOWANCE_FUNCTION,
    "console_set_user_active(text,text,boolean)",
    "console_force_logout(text,text)",
    "console_issue_plan(text,text,text,text,text)",
)


async def can_issue_allowance(conn, role, schema):
    """True when the role may call any Console entry point; absent ones count as no."""
    for function in CONSOLE_FUNCTIONS + ("console_require_admin(text,text[])",):
        row = await (
            await conn.execute(
                """SELECT CASE WHEN to_regprocedure(%s) IS NULL THEN false
                ELSE has_function_privilege(%s,to_regprocedure(%s),'EXECUTE') END AS allowed""",
                (schema + "." + function, role, schema + "." + function),
            )
        ).fetchone()
        if row["allowed"]:
            return True
    return False


async def assert_safe_runtime_role(conn, role=None, *, check_grants=True):
    context = await (
        await conn.execute(
            "SELECT current_user AS current_role, session_user AS session_role, current_schema() AS schema"
        )
    ).fetchone()
    if not context["schema"]:
        raise PlatformError(503, "unsafe_runtime_role")
    names = (
        [role]
        if role is not None
        else [context["current_role"], context["session_role"]]
    )
    roles = await (
        await conn.execute(
            """WITH RECURSIVE reachable(oid) AS (
        SELECT oid FROM pg_roles WHERE rolname=ANY(%s)
        UNION SELECT m.roleid FROM pg_auth_members m JOIN reachable r ON m.member=r.oid
    ) SELECT p.oid,p.rolname,p.rolsuper,p.rolcreaterole,p.rolcreatedb,p.rolreplication,p.rolbypassrls
      FROM pg_roles p JOIN reachable r ON p.oid=r.oid""",
            (names,),
        )
    ).fetchall()
    if not roles or not all(any(r["rolname"] == n for r in roles) for n in names):
        raise PlatformError(503, "unsafe_runtime_role")
    sensitive = {
        "pg_execute_server_program",
        "pg_read_server_files",
        "pg_write_server_files",
        "pg_signal_backend",
        "pg_checkpoint",
    }
    if any(
        any(
            r[k]
            for k in (
                "rolsuper",
                "rolcreaterole",
                "rolcreatedb",
                "rolreplication",
                "rolbypassrls",
            )
        )
        or r["rolname"] in sensitive
        for r in roles
    ):
        raise PlatformError(503, "unsafe_runtime_role")
    oids = [r["oid"] for r in roles]
    owned = await (
        await conn.execute(
            """SELECT EXISTS(
        SELECT 1 FROM pg_namespace WHERE nspname=%s AND nspowner=ANY(%s::oid[])
        UNION ALL SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=%s AND c.relowner=ANY(%s::oid[])
        UNION ALL SELECT 1 FROM pg_database WHERE datname=current_database() AND datdba=ANY(%s::oid[])
    ) AS unsafe""",
            (context["schema"], oids, context["schema"], oids, oids),
        )
    ).fetchone()
    if owned["unsafe"]:
        raise PlatformError(503, "unsafe_runtime_role")
    if check_grants:
        # NOINHERIT memberships may still SET ROLE into every reachable parent.
        for name in {r["rolname"] for r in roles}:
            unsafe = await (
                await conn.execute(
                    """SELECT
                has_schema_privilege(%s,%s,'CREATE') OR EXISTS(
                  SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                  WHERE n.nspname=%s AND c.relkind IN ('r','p') AND (
                    has_table_privilege(%s,c.oid,'TRUNCATE,TRIGGER') OR
                    has_table_privilege(%s,c.oid,'DELETE') OR
                    (c.relname=ANY(%s) AND has_any_column_privilege(%s,c.oid,'UPDATE,REFERENCES'))
                  )) AS unsafe""",
                    (
                        name,
                        context["schema"],
                        context["schema"],
                        name,
                        name,
                        list(IMMUTABLE_TABLES),
                        name,
                    ),
                )
            ).fetchone()
            if unsafe["unsafe"]:
                raise PlatformError(503, "unsafe_runtime_role")
            # Only the Console may issue the web owner allowance (migration 024).
            if await can_issue_allowance(conn, name, context["schema"]):
                raise PlatformError(503, "unsafe_runtime_role")
            catalog_write = await (
                await conn.execute(
                    "SELECT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relname IN ('execution_models','execution_model_states','media_models','media_model_states','media_storage_policy','internal_grants','internal_feature_extensions') AND has_any_column_privilege(%s,c.oid,'INSERT')) AS unsafe",
                    (context["schema"], name),
                )
            ).fetchone()
            if catalog_write["unsafe"]:
                raise PlatformError(503, "unsafe_runtime_role")
            # Operational loss decisions belong to a separately verified host
            # owner. Product runtime may append provider evidence, but cannot
            # read private approval notes or insert a waiver operation.
            resolution_access = await (await conn.execute(
                "SELECT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=%s AND c.relname IN ('execution_resolutions','execution_resolution_operations','media_resolutions','media_resolution_operations','owner_grants') "
                "AND has_any_column_privilege(%s,c.oid,'SELECT,INSERT,UPDATE,REFERENCES')) AS unsafe",
                (context["schema"], name),
            )).fetchone()
            if resolution_access["unsafe"]:
                raise PlatformError(503, "unsafe_runtime_role")
            console_access = await (await conn.execute(
                "SELECT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=%s AND starts_with(c.relname,'console_') AND c.relkind IN ('r','p') "
                "AND has_any_column_privilege(%s,c.oid,'SELECT,INSERT,UPDATE,REFERENCES')) AS unsafe",
                (context["schema"], name),
            )).fetchone()
            if console_access["unsafe"]:
                raise PlatformError(503, "unsafe_runtime_role")
            continuity_access = await (await conn.execute(
                "SELECT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=%s AND c.relname IN ('identity_restore_proofs','identity_restore_candidates','identity_migrations','identity_migration_entries') "
                "AND has_any_column_privilege(%s,c.oid,'SELECT,INSERT,UPDATE,REFERENCES')) AS unsafe",
                (context["schema"],name),
            )).fetchone()
            if continuity_access['unsafe']:
                raise PlatformError(503,'unsafe_runtime_role')
            sequence_access = await (await conn.execute(
                "SELECT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=%s AND c.relkind='S' AND ("
                "has_sequence_privilege(%s,c.oid,'SELECT,UPDATE') OR "
                "(NOT (c.relname=ANY(%s)) AND has_sequence_privilege(%s,c.oid,'USAGE')))) AS unsafe",
                (context["schema"],name,list(SEQUENCE_NAMES),name),
            )).fetchone()
            if sequence_access["unsafe"]:
                raise PlatformError(503, "unsafe_runtime_role")


async def grant_runtime_permissions(conn, role: str):
    """Grant only required DML to an existing non-owner role in current_schema().

    Invoke using the migration owner after migration; role creation and credentials
    are deployment responsibilities. The caller controls the transaction boundary.
    """
    if not isinstance(role, str) or not role or len(role) > 63:
        raise PlatformError(422, "invalid_runtime_role")
    await assert_safe_runtime_role(conn, role, check_grants=False)
    schema = (
        await (await conn.execute("SELECT current_schema() AS schema")).fetchone()
    )["schema"]
    ident = sql.Identifier(role)
    namespace = sql.Identifier(schema)
    await conn.execute(
        sql.SQL("REVOKE ALL ON SCHEMA {} FROM {}").format(namespace, ident)
    )
    await conn.execute(
        sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA {} FROM {}").format(
            namespace, ident
        )
    )
    await conn.execute(
        sql.SQL("REVOKE ALL ON ALL SEQUENCES IN SCHEMA {} FROM {}").format(
            namespace, ident
        )
    )
    await conn.execute(
        sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(namespace, ident)
    )
    for table in SELECT_TABLES:
        await conn.execute(sql.SQL("GRANT SELECT ON {}.{} TO {}").format(
            namespace, sql.Identifier(table), ident))
    for table in INSERT_TABLES:
        await conn.execute(
            sql.SQL("GRANT INSERT ON {}.{} TO {}").format(
                namespace, sql.Identifier(table), ident
            )
        )
    # A schema held at an older migration may lack later columns (username, 023).
    present = {
        (row["relname"], row["attname"])
        for row in await (
            await conn.execute(
                """SELECT c.relname,a.attname FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
                JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname=current_schema() AND a.attnum>0 AND NOT a.attisdropped"""
            )
        ).fetchall()
    }
    for table, wanted in UPDATE_COLUMNS.items():
        columns = [column for column in wanted if (table, column) in present]
        if not columns:
            continue
        await conn.execute(
            sql.SQL("GRANT UPDATE ({}) ON {}.{} TO {}").format(
                sql.SQL(",").join(map(sql.Identifier, columns)),
                namespace,
                sql.Identifier(table),
                ident,
            )
        )
    for sequence in SEQUENCE_NAMES:
        await conn.execute(sql.SQL("GRANT USAGE ON SEQUENCE {}.{} TO {}").format(
            namespace, sql.Identifier(sequence), ident))
    await assert_safe_runtime_role(conn, role)
