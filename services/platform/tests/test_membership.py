import pytest
from dataclasses import replace


def test_membership_contract_exists():
    from platform_service.membership import MembershipService

    assert callable(MembershipService)


async def seed(db, credits="100", products=None):
    from platform_service.contracts import Scope, OperatorIdentity
    from platform_service.membership import MembershipService

    async with db.connection() as c:
        await c.execute("INSERT INTO users(id) VALUES ('u'),('other')")
        await c.execute(
            "INSERT INTO tenants(id,owner_user_id) VALUES ('t','u'),('foreign','other')"
        )
        await c.execute(
            "INSERT INTO tenant_members(tenant_id,user_id,role) VALUES ('t','u','owner'),('foreign','other','owner')"
        )
        await c.execute(
            "INSERT INTO billing_accounts(id,tenant_id) VALUES ('wallet','t'),('foreign-wallet','foreign')"
        )
        await c.execute("INSERT INTO products(id) VALUES ('easel'),('second')")
    operator = OperatorIdentity(
        "fixture-operator", frozenset({"plans:publish", "test:issue"})
    )
    service = MembershipService(db)
    plan = dict(
        id="fixture-plan-v1",
        name="Fixture",
        version="1",
        included_products=products or ["easel", "second"],
        features=["text"],
        credits=credits,
        days=30,
        concurrency=10,
        test_only=True,
    )
    await service.publish_plan(operator, plan)
    scope = Scope("u", "easel", "t", "wallet", "session", 0)
    return service, operator, plan, scope


@pytest.mark.asyncio
async def test_issuance_is_atomic_and_idempotent(db):
    service, operator, plan, scope = await seed(db)
    first = await service.issue_test(operator, "wallet", plan["id"], "grant-1")
    assert first == await service.issue_test(operator, "wallet", plan["id"], "grant-1")
    from platform_service.credits import CreditLedger

    summary = await CreditLedger(db).summary(scope)
    assert summary["issued"] == summary["available"] == "100"
    assert summary["test_only"] is True
    assert (await service.entitlements(scope))["active"] is True
    async with db.connection() as c:
        assert (
            await (await c.execute("SELECT count(*) AS n FROM memberships")).fetchone()
        )["n"] == 1
        assert (
            await (
                await c.execute("SELECT sum(delta) AS n FROM credit_entries")
            ).fetchone()
        )["n"] == 0


@pytest.mark.asyncio
async def test_issuance_key_cannot_change_wallet_or_plan(db):
    from platform_service.errors import PlatformError

    service, operator, plan, scope = await seed(db)
    await service.issue_test(operator, "wallet", plan["id"], "same")
    for wallet, plan_id in [("foreign-wallet", plan["id"]), ("wallet", "different")]:
        with pytest.raises(PlatformError) as e:
            await service.issue_test(operator, wallet, plan_id, "same")
        assert e.value.status == 409


@pytest.mark.asyncio
async def test_plan_version_immutable_and_operator_capabilities_required(db):
    from platform_service.errors import PlatformError
    from platform_service.contracts import OperatorIdentity

    service, operator, plan, _ = await seed(db)
    assert (await service.publish_plan(operator, plan))["id"] == plan["id"]
    with pytest.raises(PlatformError):
        await service.publish_plan(operator, {**plan, "credits": "101"})
    denied = OperatorIdentity("not-operator", frozenset())
    with pytest.raises(PlatformError):
        await service.publish_plan(denied, plan)
    with pytest.raises(PlatformError):
        await service.issue_test(denied, "wallet", plan["id"], "x")
    with pytest.raises(PlatformError):
        await service.publish_plan(operator, {**plan, "id": "paid", "test_only": False})


@pytest.mark.asyncio
async def test_membership_product_scope_and_revocation(db):
    from platform_service.errors import PlatformError
    from platform_service.credits import CreditLedger

    service, operator, plan, scope = await seed(db, products=["easel"])
    ledger = CreditLedger(db)
    with pytest.raises(PlatformError):
        await ledger.reserve(
            scope,
            operation="fixture",
            key="a",
            body_hash="hash",
            amount=1,
            rate_version="v1",
        )
    await service.issue_test(operator, "wallet", plan["id"], "one")
    with pytest.raises(PlatformError):
        await ledger.reserve(
            replace(scope, product_id="second"),
            operation="fixture",
            key="a",
            body_hash="hash",
            amount=1,
            rate_version="v1",
        )
    for invalid in [
        replace(scope, billing_account_id="foreign-wallet"),
        replace(scope, tenant_id="foreign"),
        replace(scope, user_id="other"),
        replace(scope, authorization_epoch=1),
    ]:
        with pytest.raises(PlatformError):
            await ledger.summary(invalid)
    async with db.connection() as c:
        await c.execute("UPDATE tenant_members SET active=false WHERE user_id='u'")
    with pytest.raises(PlatformError):
        await service.entitlements(scope)
