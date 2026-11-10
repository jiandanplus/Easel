"""Plan-level model access and prices that follow the points spent (migration 027) on a real PostgreSQL."""
from uuid import uuid4

import pytest

from platform_service.contracts import OperatorIdentity
from platform_service.credits import CreditLedger
from platform_service.errors import PlatformError
from platform_service.membership import MembershipService
from platform_service.pricing import scale_text, validate_plan_access
from fractions import Fraction
from test_execution_core import BINDINGS, OPERATOR, admit, definition, setup

ADMIN = OperatorIdentity("fixture-admin", frozenset({"plans:publish", "test:issue"}))
FULL = 65536 * 100 + 100 * 200  # worst-case hold for the fixture model at its base price


def plan(id, credits="10000000", days=7, **extra):
    return dict(id=id, name=id, version="1", included_products=["easel", "second"], features=["text"],
                credits=credits, days=days, concurrency=10, test_only=True, **extra)


def half():
    return {"numerator": "1", "denominator": "2"}


async def issue(db, value):
    members = MembershipService(db)
    await members.publish_plan(ADMIN, value)
    await members.issue_test(ADMIN, "wallet", value["id"], "grant-" + value["id"])


async def job_row(db, job):
    async with db.connection() as c:
        return await (await c.execute("SELECT price,amount FROM execution_jobs WHERE id=%s", (job["id"],))).fetchone()


async def only_lot_with(db, plan_id):
    async with db.connection() as c:
        return await (await c.execute(
            """SELECT l.held FROM credit_lots l JOIN memberships m ON m.id=l.membership_id WHERE m.plan_id=%s""",
            (plan_id,))).fetchone()


@pytest.mark.parametrize("bad", [{"models": []}, {"models": ["a", "a"]}, {"models": "a"},
                                 {"pricing": {"default": {"numerator": "0", "denominator": "1"}}},
                                 {"pricing": {"extra": {}}}, {"pricing": {"default": {"numerator": 1, "denominator": 2}}}])
def test_invalid_access_settings_are_refused(bad):
    with pytest.raises(PlatformError):
        validate_plan_access(bad)


def test_scaling_stays_exact():
    rate = {"version": "1", "input": {"numerator": "3", "denominator": "7"}, "output": {"numerator": "1", "denominator": "2"}}
    scaled = scale_text(rate, Fraction(4, 5))
    assert scaled["input"] == {"numerator": "12", "denominator": "35"} and scaled["output"] == {"numerator": "2", "denominator": "5"}


@pytest.mark.asyncio
async def test_price_multiplier_applies_only_to_points_from_that_plan(db):
    service, _, scope, context = await setup(db)
    await issue(db, plan("cheap", pricing={"default": half()}))
    job = await admit(service, context, "cheap-call")
    row = await job_row(db, job)
    assert row["amount"] == FULL // 2 and row["price"]["multiplier"] == half()
    assert (await only_lot_with(db, "cheap"))["held"] == FULL // 2


@pytest.mark.asyncio
async def test_small_cheap_plan_cannot_discount_other_points(db):
    service, _, scope, context = await setup(db)
    await issue(db, plan("tiny-cheap", credits="1000000", pricing={"default": half()}))
    job = await admit(service, context, "full-price")
    assert (await job_row(db, job))["amount"] == FULL
    assert (await only_lot_with(db, "tiny-cheap"))["held"] == 0


@pytest.mark.asyncio
async def test_settlement_uses_the_price_the_call_was_admitted_at(db):
    service, _, scope, context = await setup(db)
    await issue(db, plan("cheap", pricing={"default": half()}))
    job = await admit(service, context, "snapshot")
    async with db.connection() as c:
        await c.execute("""INSERT INTO model_prices(id,kind,model_id,rate,reason,operator_id)
            VALUES (%s,'execution','fixture-text',%s,'raise','op')""", (uuid4().hex, '{"version":"2","input":{"numerator":"1000","denominator":"1"},"output":{"numerator":"1000","denominator":"1"}}'))
    await service.dispatch(job["id"], job["attempt_id"])
    await service.finish(job["id"], job["attempt_id"], input_tokens=10, output_tokens=10, terminal_status="completed", provider_response_id="r-1", evidence_hash="receipt", payload=b"{}", content_type="application/json")
    assert (await service.get(scope, job["id"]))["actual"] in ("1500", 1500)


@pytest.mark.asyncio
async def test_new_price_applies_to_later_calls_and_catalog(db):
    service, _, scope, context = await setup(db)
    async with db.connection() as c:
        await c.execute("""INSERT INTO model_prices(id,kind,model_id,rate,reason,operator_id)
            VALUES (%s,'execution','fixture-text',%s,'lower','op')""", (uuid4().hex, '{"version":"2","input":{"numerator":"1","denominator":"1"},"output":{"numerator":"2","denominator":"1"}}'))
    job = await admit(service, context, "new-price")
    assert (await job_row(db, job))["amount"] == 65536 + 200
    rate = (await service.catalog(scope, mode="test", bindings=BINDINGS))["models"][0]["rate"]
    assert rate["input"] == {"numerator": "1", "denominator": "1"}


@pytest.mark.asyncio
async def test_plan_model_list_limits_access_and_points(db):
    service, _, scope, _ = await setup(db)
    await service.publish({**definition(), "id": "other-text", "name": "Other"}, OPERATOR)
    await service.set_enabled("other-text", True, OPERATOR)
    bindings = {**BINDINGS, "other-text": BINDINGS["fixture-text"]}
    await issue(db, plan("only-other", models=["other-text"], credits="20000000"))
    ids = {m["id"] for m in (await service.catalog(scope, mode="test", bindings=bindings))["models"]}
    assert ids == {"fixture-text", "other-text"}
    # The fixture plan allows every text model, so spending on fixture-text must skip only-other's points.
    minted = await service.mint(scope, model="fixture-text", invocation_key="fixture-only", mode="test", bindings=bindings)
    context = await service.authenticate_task(minted["task_token"])
    await service.admit(context, key="k", body_hash="hash", output_limit=100, bindings=bindings)
    assert (await only_lot_with(db, "only-other"))["held"] == 0


@pytest.mark.asyncio
async def test_model_outside_every_plan_is_hidden_and_refused(db):
    service, _, scope, _ = await setup(db)
    for model in ("premium-a", "premium-b"):
        await service.publish({**definition(), "id": model, "name": model, "feature": "premium"}, OPERATOR)
        await service.set_enabled(model, True, OPERATOR)
    bindings = {**BINDINGS, "premium-a": BINDINGS["fixture-text"], "premium-b": BINDINGS["fixture-text"]}
    await issue(db, {**plan("limited", models=["premium-a"]), "features": ["premium"]})
    ids = {m["id"] for m in (await service.catalog(scope, mode="test", bindings=bindings))["models"]}
    assert ids == {"fixture-text", "premium-a"}
    with pytest.raises(PlatformError) as error:
        await service.mint(scope, model="premium-b", invocation_key="denied", mode="test", bindings=bindings)
    assert error.value.status == 403


@pytest.mark.asyncio
async def test_disabled_model_is_available_only_to_trial_wallets(db):
    service, _, scope, _ = await setup(db)
    await service.set_enabled("fixture-text", False, OPERATOR)
    assert (await service.catalog(scope, mode="test", bindings=BINDINGS))["models"] == []
    async with db.connection() as c:
        await c.execute("INSERT INTO model_trial_access(kind,model_id,billing_account_id,operator_id) VALUES ('execution','fixture-text','wallet','op')")
    assert [m["id"] for m in (await service.catalog(scope, mode="test", bindings=BINDINGS))["models"]] == ["fixture-text"]
    async with db.connection() as c:
        await c.execute("UPDATE model_trial_access SET active=false")
    assert (await service.catalog(scope, mode="test", bindings=BINDINGS))["models"] == []
