"""Evidence append authority must not become operator write-off authority."""
from uuid import uuid4
import pytest
from psycopg import sql

from platform_service.errors import PlatformError
from platform_service.permissions import grant_runtime_permissions, assert_safe_runtime_role


@pytest.fixture
async def runtime_role(db):
    role='r2_runtime_'+uuid4().hex
    async with db.connection() as conn:
        await conn.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
        await grant_runtime_permissions(conn,role)
    try:yield role
    finally:
        async with db.connection() as conn:
            await conn.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
            await conn.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


@pytest.mark.asyncio
async def test_runtime_can_append_provider_evidence_but_not_operational_waivers(db,runtime_role):
    async with db.connection() as conn:
        for table in ('execution_provider_attempts','execution_provider_associations','execution_terminal_evidence'):
            row=await (await conn.execute("SELECT has_table_privilege(%s,%s,'SELECT') r,has_table_privilege(%s,%s,'INSERT') i,has_any_column_privilege(%s,%s,'UPDATE') u",(runtime_role,table,runtime_role,table,runtime_role,table))).fetchone()
            assert row=={'r':True,'i':True,'u':False}
        for table in ('execution_resolutions','execution_resolution_operations','media_resolutions','media_resolution_operations','owner_grants'):
            assert not (await (await conn.execute("SELECT has_any_column_privilege(%s,%s,'SELECT,INSERT,UPDATE,REFERENCES') unsafe",(runtime_role,table))).fetchone())['unsafe']
        await assert_safe_runtime_role(conn,runtime_role)


@pytest.mark.asyncio
@pytest.mark.parametrize('table,privilege',[('owner_grants','SELECT'),('execution_resolutions','SELECT'),('execution_resolutions','INSERT'),('execution_resolution_operations','SELECT'),('execution_resolution_operations','INSERT'),('media_resolutions','SELECT'),('media_resolution_operations','INSERT'),('execution_terminal_evidence','UPDATE')])
async def test_startup_rejects_extra_financial_or_evidence_mutation_authority(db,runtime_role,table,privilege):
    async with db.connection() as conn:
        await conn.execute(sql.SQL('GRANT '+privilege+' ON {} TO {}').format(sql.Identifier(table),sql.Identifier(runtime_role)))
    with pytest.raises(PlatformError) as failure:
        async with db.connection() as conn:await assert_safe_runtime_role(conn,runtime_role)
    assert failure.value.code=='unsafe_runtime_role'
