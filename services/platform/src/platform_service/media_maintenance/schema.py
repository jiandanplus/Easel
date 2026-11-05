"""Owner-only explicit installer; never scans/applies public migrations."""
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from psycopg import sql
from .contracts import MaintenanceError,digest,require

_BASE=("media_models","media_model_states","media_blocked_bindings","media_invocations","media_task_tokens","media_jobs","media_provider_attempts","media_provider_associations","media_terminal_evidence","media_receipts","media_assets","media_artifacts","media_voices","media_storage_policy","media_storage_budgets","media_blobs","media_http_response_references")
_EXTRA=("media_voice_registration_proofs","media_voice_steps","media_voice_step_observations","media_work","media_observations","media_sources","media_protocol_facts")
_FINANCE=("credit_reservations","credit_holds","credit_journals","credit_entries")
_SQL=Path(__file__).with_name("sql")/"001_media_backup_control.sql"

def profile_tables(name):
    require(name in {"public_001_016","local_001_020"})
    return _BASE+(_EXTRA if name=="local_001_020" else ())

def _name(value):require(type(value) is str and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}",value,re.ASCII) is not None)

@dataclass(frozen=True)
class SchemaProfile:
    name:str
    application_schema:str
    runtime_role:str
    controller_role:str
    migrations:tuple
    def __post_init__(self):
        profile_tables(self.name)
        for value in (self.application_schema,self.runtime_role,self.controller_role):_name(value)
        require(self.runtime_role!=self.controller_role and type(self.migrations) is tuple)
        count=16 if self.name=="public_001_016" else 20
        require(len(self.migrations)==count)
        for index,item in enumerate(self.migrations,1):
            require(type(item) is tuple and len(item)==2 and item[0].startswith(f"{index:03}_") and item[0].endswith(".sql"));digest(item[1])

async def install_control_schema(owner_connection,expected_profile):
    require(type(expected_profile) is SchemaProfile);p=expected_profile
    raw=_SQL.read_bytes();checksum=hashlib.sha256(raw).hexdigest()
    try:
        async with owner_connection.transaction():
            await owner_connection.execute("SELECT pg_advisory_xact_lock(hashtextextended('easel-backup-control-schema-v1',0))")
            migrations=await(await owner_connection.execute(sql.SQL("SELECT version,checksum FROM {}.schema_migrations ORDER BY version").format(sql.Identifier(p.application_schema)))).fetchall()
            require(tuple(migrations)==p.migrations)
            tables=await(await owner_connection.execute("SELECT tablename FROM pg_tables WHERE schemaname=%s AND tablename LIKE 'media_%%'",(p.application_schema,))).fetchall()
            require({r[0] for r in tables}==set(profile_tables(p.name)))
            present=(await(await owner_connection.execute("SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname='easel_backup_control')")).fetchone())[0]
            if present:
                stored=await(await owner_connection.execute("SELECT version,checksum FROM easel_backup_control.schema_migrations")).fetchall()
                require(stored==[(_SQL.name,checksum)])
                config=(await(await owner_connection.execute("SELECT application_schema,profile FROM easel_backup_control.settings WHERE id=1")).fetchone())
                require(config==(p.application_schema,p.name));return checksum
            await owner_connection.execute(raw.decode(),prepare=False)
            await owner_connection.execute("INSERT INTO easel_backup_control.settings VALUES(1,%s,%s)",(p.application_schema,p.name))
            await owner_connection.execute("INSERT INTO easel_backup_control.schema_migrations VALUES(%s,%s)",(_SQL.name,checksum))
            for table in (*profile_tables(p.name),*_FINANCE):
                for suffix,events,level in (("rows","INSERT OR UPDATE OR DELETE","ROW"),("truncate","TRUNCATE","STATEMENT")):
                    command=sql.SQL("CREATE TRIGGER {} BEFORE "+events+" ON {}.{} FOR EACH "+level+" EXECUTE FUNCTION easel_backup_control.guard_media_mutation()")
                    await owner_connection.execute(command.format(sql.Identifier("a_b2_"+suffix),sql.Identifier(p.application_schema),sql.Identifier(table)))
            guarded=await(await owner_connection.execute("SELECT tgname FROM pg_trigger WHERE tgrelid=%s::regclass AND NOT tgisinternal",(p.application_schema+".credit_lots",))).fetchall()
            require({"lots_reconcile","lots_holds_reconcile","lot_funding_guard"}<={row[0] for row in guarded})
            await owner_connection.execute(sql.SQL("CREATE CONSTRAINT TRIGGER b2_media_lot_commit AFTER INSERT OR UPDATE OR DELETE ON {}.credit_lots DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION easel_backup_control.guard_media_lot_commit()").format(sql.Identifier(p.application_schema)))
            for role in (p.runtime_role,p.controller_role):
                await owner_connection.execute(sql.SQL("GRANT USAGE ON SCHEMA easel_backup_control TO {}").format(sql.Identifier(role)))
                await owner_connection.execute(sql.SQL("GRANT SELECT ON easel_backup_control.runtime_status,easel_backup_control.worker_status,easel_backup_control.worker_domains TO {}").format(sql.Identifier(role)))
            runtime=("register_worker(jsonb,bytea)","attest_worker(jsonb,bytea,jsonb)","unregister_worker(jsonb,bytea)","issue_permit(jsonb,bytea,text,text,text,text,text)","validate_permit(bytea,bigint,text,text,text,uuid,uuid)","finish_permit(bytea,bigint,text)","permit_result(bytea,bigint)","mark_unknown(bytea,bigint)","gc_writer_idle(text)","blob_writer_owned(bytea,bigint,text)")
            controller=("generation_digest()","begin_window(bytea,uuid,jsonb,jsonb)","observe_window(bytea,uuid)","verify_window(bytea,uuid,bigint,text)","transition(bytea,uuid,bigint,text,text,jsonb)")
            for role,functions in ((p.runtime_role,runtime),(p.controller_role,controller)):
                for signature in functions:
                    await owner_connection.execute(sql.SQL("GRANT EXECUTE ON FUNCTION easel_backup_control."+signature+" TO {}").format(sql.Identifier(role)))
            return checksum
    except MaintenanceError:raise
    except Exception:raise MaintenanceError() from None
