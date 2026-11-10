"""Web owner allowance (migration 024, decision 0011) on a real PostgreSQL with the low Console role."""
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest

from platform_service.console_auth import ConsoleAuth
from platform_service.console_catalog import ConsoleCatalog
from platform_service.console_service import ConsoleQueries
from platform_service.credits import CreditLedger
from platform_service.errors import PlatformError
from platform_service.permissions import assert_safe_runtime_role, grant_runtime_permissions
from test_console_auth import authenticated, console  # noqa: F401 (fixture)
from test_console_permissions import console_role  # noqa: F401 (fixture)
from test_membership import seed

REASON = '付款关闭期间给朋友试用。'


async def low_catalog(console, console_role, role):
    session, _ = await authenticated(console, role)
    owner_auth, _ = console
    low, _ = console_role
    auth = ConsoleAuth(low, owner_auth.settings)
    principal = await auth.authenticate(session['session_token'])
    return ConsoleCatalog(low, auth), ConsoleQueries(low, auth), principal, low


@pytest.mark.asyncio
async def test_admin_issues_fixed_allowance_once_with_audit(db, console, console_role):
    _, _, _, scope = await seed(db)
    catalog, queries, principal, _ = await low_catalog(console, console_role, 'admin')
    first = await catalog.issue_allowance(principal, 'wallet', REASON, 'web-allowance-1')
    assert first['credits'] == '100000000' and first['funding_source'] == 'owner_allowance'
    assert await catalog.issue_allowance(principal, 'wallet', REASON, 'web-allowance-1') == first
    assert (await CreditLedger(db).summary(scope))['live_available'] == '100000000'
    with pytest.raises(PlatformError) as error:
        await catalog.issue_allowance(principal, 'wallet', REASON, 'web-allowance-2')
    assert (error.value.status, error.value.code) == (409, 'owner_grant_active')
    with pytest.raises(PlatformError) as error:
        await catalog.issue_allowance(principal, 'missing-wallet', REASON, 'web-allowance-3')
    assert error.value.status == 404
    async with db.connection() as c:
        grant = await (await c.execute('SELECT operator_id,approval_reference,reason FROM owner_grants')).fetchone()
    assert grant == {'operator_id': 'console:' + principal.id, 'approval_reference': 'console-web', 'reason': REASON}
    audits = (await queries.audits(principal))['items']
    assert [(a['action'], a['target_id'], a['reason']) for a in audits] == [('allowance.issue', 'wallet', REASON)]


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['operator', 'viewer'])
async def test_only_admins_may_issue(db, console, console_role, role):
    await seed(db)
    catalog, _, principal, low = await low_catalog(console, console_role, role)
    with pytest.raises(PlatformError) as error:
        await catalog.issue_allowance(principal, 'wallet', REASON, 'web-allowance-denied')
    assert error.value.status == 403
    # Calling the function directly with the same identity is refused by the database too.
    with pytest.raises(psycopg.Error) as raw:
        async with low.connection() as c:
            await c.execute('SELECT console_issue_owner_allowance(%s,%s,%s,%s)', (principal.id, 'wallet', 'a' * 64, REASON))
    assert raw.value.sqlstate == 'EA403'
    async with db.connection() as c:
        assert (await (await c.execute('SELECT count(*) AS n FROM owner_grants')).fetchone())['n'] == 0


@pytest.mark.asyncio
async def test_product_runtime_cannot_reach_the_allowance_function(db):
    role = 'product_allowance_' + uuid4().hex
    async with db.connection() as c:
        await c.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
    try:
        async with db.connection() as c:
            await grant_runtime_permissions(c, role)
            row = await (await c.execute("SELECT has_function_privilege(%s,'console_issue_owner_allowance(text,text,text,text)','EXECUTE') AS allowed", (role,))).fetchone()
            assert row['allowed'] is False
            await c.execute(sql.SQL('GRANT EXECUTE ON FUNCTION console_issue_owner_allowance(text,text,text,text) TO {}').format(sql.Identifier(role)))
        with pytest.raises(PlatformError) as error:
            async with db.connection() as c:
                await assert_safe_runtime_role(c, role)
        assert error.value.code == 'unsafe_runtime_role'
    finally:
        async with db.connection() as c:
            await c.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
            await c.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
