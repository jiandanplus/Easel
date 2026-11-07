"""Optional M3 permission interface; not imported by the frozen M1 runtime.

Provision after the existing central grants, and check when enabling the voice
worker. No UPDATE/DELETE/TRUNCATE/REFERENCES/DDL authority is granted here.
"""
from psycopg import sql

from ..errors import PlatformError
from ..permissions import assert_safe_runtime_role

VOICE_TABLES=('media_voice_steps','media_voice_step_observations','media_voice_registration_proofs')


async def assert_voice_runtime_permissions(conn,role=None):
    await assert_safe_runtime_role(conn,role)
    context=await (await conn.execute('SELECT current_user current_role,session_user session_role,current_schema() namespace')).fetchone()
    names=[role] if role is not None else list({context['current_role'],context['session_role']})
    rows=await (await conn.execute("SELECT c.oid,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relkind='r' AND c.relname=ANY(%s)",(context['namespace'],list(VOICE_TABLES)))).fetchall()
    if {row['relname'] for row in rows}!=set(VOICE_TABLES):raise PlatformError(503,'voice_runtime_unavailable')
    roles=await (await conn.execute('WITH RECURSIVE reachable(oid) AS (SELECT oid FROM pg_roles WHERE rolname=ANY(%s) UNION SELECT m.roleid FROM pg_auth_members m JOIN reachable r ON m.member=r.oid) SELECT rolname FROM pg_roles WHERE oid IN (SELECT oid FROM reachable)',(names,))).fetchall()
    for candidate in roles:
        for table in rows:
            unsafe=await (await conn.execute("SELECT has_table_privilege(%s,%s,'UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES') OR has_any_column_privilege(%s,%s,'UPDATE,REFERENCES') unsafe",(candidate['rolname'],table['oid'],candidate['rolname'],table['oid']))).fetchone()
            if unsafe['unsafe']:raise PlatformError(503,'unsafe_voice_runtime_role')
    for name in names:
        for table in rows:
            valid=await (await conn.execute("SELECT has_table_privilege(%s,%s,'SELECT') AND has_table_privilege(%s,%s,'INSERT') valid",(name,table['oid'],name,table['oid']))).fetchone()
            if not valid['valid']:raise PlatformError(503,'voice_runtime_unavailable')


async def grant_voice_permissions(conn,role):
    if not isinstance(role,str) or not 1<=len(role)<=63 or any(ord(c)<32 for c in role):raise PlatformError(422,'invalid_runtime_role')
    await assert_safe_runtime_role(conn,role,check_grants=False)
    namespace=(await (await conn.execute('SELECT current_schema() namespace')).fetchone())['namespace']
    for table in VOICE_TABLES:
        await conn.execute(sql.SQL('REVOKE ALL ON {}.{} FROM {}').format(sql.Identifier(namespace),sql.Identifier(table),sql.Identifier(role)))
        await conn.execute(sql.SQL('GRANT SELECT,INSERT ON {}.{} TO {}').format(sql.Identifier(namespace),sql.Identifier(table),sql.Identifier(role)))
    await assert_voice_runtime_permissions(conn,role)
