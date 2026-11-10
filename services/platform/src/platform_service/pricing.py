"""Which plan may pay for a model, and at what price (decision 0014).

Prices follow the batch of points being spent: a call is charged by the plan that
issued the points it uses, so a cheap small plan cannot discount points from another.
"""
from fractions import Fraction

from .credits import expire
from .errors import PlatformError

ONE = Fraction(1)
MAX_FRACTION_PART = 10**12


def as_fraction(value):
    if (not isinstance(value, dict) or set(value) != {"numerator", "denominator"}
            or not all(isinstance(value[k], str) and value[k].isascii() and value[k].isdecimal() and len(value[k]) <= 13 for k in value)):
        raise PlatformError(422, "invalid_plan_pricing")
    numerator, denominator = int(value["numerator"]), int(value["denominator"])
    if not 0 < numerator <= MAX_FRACTION_PART or not 0 < denominator <= MAX_FRACTION_PART:
        raise PlatformError(422, "invalid_plan_pricing")
    return Fraction(numerator, denominator)


def validate_plan_access(plan):
    """Optional plan fields: `models` limits access; `pricing` sets multipliers."""
    models = plan.get("models")
    if models is not None and (not isinstance(models, list) or not models or len(models) > 200
                               or len(set(map(str, models))) != len(models)
                               or not all(isinstance(m, str) and 1 <= len(m) <= 128 for m in models)):
        raise PlatformError(422, "invalid_plan_models")
    pricing = plan.get("pricing")
    if pricing is None:
        return
    if not isinstance(pricing, dict) or not set(pricing) <= {"default", "models"}:
        raise PlatformError(422, "invalid_plan_pricing")
    if "default" in pricing:
        as_fraction(pricing["default"])
    overrides = pricing.get("models", {})
    if not isinstance(overrides, dict) or len(overrides) > 200:
        raise PlatformError(422, "invalid_plan_pricing")
    for model, value in overrides.items():
        if not isinstance(model, str) or not 1 <= len(model) <= 128:
            raise PlatformError(422, "invalid_plan_pricing")
        as_fraction(value)


def plan_allows(definition, model):
    models = definition.get("models")
    return models is None or model in models


def multiplier(definition, model):
    pricing = definition.get("pricing") or {}
    value = (pricing.get("models") or {}).get(model) or pricing.get("default")
    return as_fraction(value) if value else ONE


def _scaled(part, factor):
    value = Fraction(int(part["numerator"]), int(part["denominator"])) * factor
    return {"numerator": str(value.numerator), "denominator": str(value.denominator)}


def scale_text(rate, factor):
    return {"version": rate["version"], **{k: _scaled(rate[k], factor) for k in ("input", "output")}}


def scale_media(rate, factor):
    return {"version": rate["version"], "meters": [{**meter, **_scaled(meter, factor)} for meter in rate["meters"]]}


async def catalog_ready(c):
    """False only on frozen pre-027 schemas kept for legacy backup tests."""
    return (await (await c.execute("SELECT to_regclass('model_prices') IS NOT NULL AS ready")).fetchone())["ready"]


async def current_rate(c, kind, model, base):
    if not await catalog_ready(c):
        return base
    row = await (await c.execute(
        """SELECT rate FROM model_prices WHERE kind=%s AND model_id=%s AND effective_at<=clock_timestamp()
        ORDER BY effective_at DESC,created_at DESC LIMIT 1""", (kind, model))).fetchone()
    return row["rate"] if row else base


async def has_trial(c, kind, model, wallet):
    if wallet is None or not await catalog_ready(c):
        return False
    return bool(await (await c.execute(
        "SELECT 1 FROM model_trial_access WHERE kind=%s AND model_id=%s AND billing_account_id=%s AND active",
        (kind, model, wallet))).fetchone())


async def _tiers(c, account, funding_mode, plans, model):
    """Payable lots grouped by price, earliest-expiring group first."""
    by_membership = {p["id"]: multiplier(p["definition"], model) for p in plans}
    lots = await (await c.execute(
        """SELECT membership_id,available FROM credit_lots WHERE billing_account_id=%s AND available>0 AND NOT frozen
        AND test_only=%s AND expires_at>clock_timestamp() ORDER BY expires_at,id""",
        (account, funding_mode == "test"))).fetchall()
    tiers = {}
    for lot in lots:
        factor = by_membership.get(lot["membership_id"])
        if factor is not None:
            tier = tiers.setdefault(factor, {"factor": factor, "memberships": set(), "available": 0})
            tier["memberships"].add(lot["membership_id"])
            tier["available"] += lot["available"]
    return list(tiers.values())


async def select_price(c, scope, funding_mode, plans, model, amount_of):
    """Pick the first group of points that covers this call; one call is one price.

    With no group large enough this still returns a choice, so reservation reports
    the same limit and balance errors it always has, in the same order.
    """
    await expire(c, scope.billing_account_id)
    tiers = await _tiers(c, scope.billing_account_id, funding_mode, plans, model)
    for tier in tiers:
        amount = amount_of(tier["factor"])
        if tier["available"] >= amount:
            return tier["factor"], amount, sorted(tier["memberships"])
    if tiers:
        return tiers[0]["factor"], amount_of(tiers[0]["factor"]), sorted(tiers[0]["memberships"])
    factor = multiplier(plans[0]["definition"], model)
    return factor, amount_of(factor), [p["id"] for p in plans]


async def display_multiplier(c, scope, funding_mode, plans, model):
    tiers = await _tiers(c, scope.billing_account_id, funding_mode, plans, model)
    return tiers[0]["factor"] if tiers else multiplier(plans[0]["definition"], model)


def price_record(rate, factor):
    return {"rate": rate, "multiplier": {"numerator": str(factor.numerator), "denominator": str(factor.denominator)}}
