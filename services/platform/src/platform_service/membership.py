"""Immutable test plan editions and permissioned, atomic test issuance."""

import json
from datetime import timedelta
from uuid import uuid4
from psycopg.types.json import Jsonb
from .errors import PlatformError
from .credits import authorize, active_plans, bounded, integer, journal, MAX_CREDITS


def require(operator, capability):
    if capability not in operator.capabilities:
        raise PlatformError(403, "operator_capability_required")


def validate_plan(plan, *, allow_live=False):
    required = {
        "id",
        "name",
        "version",
        "included_products",
        "features",
        "credits",
        "days",
        "concurrency",
        "test_only",
    }
    if (
        not isinstance(plan, dict)
        or set(plan) - {"models", "pricing"} != required
        or type(plan["test_only"]) is not bool
        or (not allow_live and plan["test_only"] is not True)
    ):
        raise PlatformError(422, "invalid_test_plan")
    for field in ("id", "name", "version"):
        bounded(plan[field])
    for field in ("included_products", "features"):
        if (
            not isinstance(plan[field], list)
            or len(plan[field]) > 100
            or len(set(map(str, plan[field]))) != len(plan[field])
        ):
            raise PlatformError(422, "invalid_test_plan")
        for value in plan[field]:
            bounded(value)
    if not plan["included_products"]:
        raise PlatformError(422, "invalid_test_plan")
    from .pricing import validate_plan_access

    validate_plan_access(plan)
    credits = plan["credits"]
    if (
        not isinstance(credits, str)
        or not credits.isascii()
        or not credits.isdecimal()
        or len(credits) > 19
    ):
        raise PlatformError(422, "invalid_microcredits")
    integer(int(credits), positive=True)
    if str(int(credits)) != credits:
        raise PlatformError(422, "invalid_microcredits")
    integer(plan["days"], positive=True)
    integer(plan["concurrency"], positive=True)
    if plan["days"] > 3650 or plan["concurrency"] > 10000:
        raise PlatformError(422, "invalid_test_plan")
    return json.loads(json.dumps(plan))


def issuance_view(row):
    return {
        "id": row["id"],
        "billing_account_id": row["billing_account_id"],
        "plan_id": row["plan_id"],
        "starts_at": row["starts_at"].isoformat(),
        "expires_at": row["expires_at"].isoformat(),
        "test_only": True,
    }


class MembershipService:
    def __init__(self, db):
        self.db = db

    async def publish_plan(self, operator, plan: dict):
        require(operator, "plans:publish")
        plan = validate_plan(
            plan, allow_live="billing:publish" in operator.capabilities
        )
        async with self.db.connection() as c:
            # Serialize concurrent publication across both immutable identity keys.
            await c.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('central:plan-publication',0))"
            )
            existing = await (
                await c.execute(
                    "SELECT definition FROM plans WHERE id=%s OR (name=%s AND version=%s)",
                    (plan["id"], plan["name"], plan["version"]),
                )
            ).fetchone()
            if existing:
                if existing["definition"] != plan:
                    raise PlatformError(409, "plan_version_immutable")
                return existing["definition"]
            found = await (
                await c.execute(
                    "SELECT id FROM products WHERE id=ANY(%s) AND enabled",
                    (plan["included_products"],),
                )
            ).fetchall()
            if len(found) != len(plan["included_products"]):
                raise PlatformError(422, "unknown_plan_product")
            await c.execute(
                "INSERT INTO plans(id,name,version,definition,created_by) VALUES (%s,%s,%s,%s,%s)",
                (plan["id"], plan["name"], plan["version"], Jsonb(plan), operator.id),
            )
            return plan

    async def plans(self, product_id):
        bounded(product_id)
        async with self.db.connection() as c:
            exists = await (
                await c.execute(
                    "SELECT id FROM products WHERE id=%s AND enabled", (product_id,)
                )
            ).fetchone()
            if not exists:
                raise PlatformError(404, "product_not_found")
            rows = await (
                # A retired plan (migration 036) is no longer offered; its members keep it.
                await c.execute("""SELECT p.definition FROM plans p WHERE NOT coalesce((SELECT s.retired FROM plan_states s
                    WHERE s.plan_id=p.id ORDER BY s.created_at DESC,s.id DESC LIMIT 1),false) ORDER BY p.created_at,p.id""")
            ).fetchall()
            return [
                r["definition"]
                for r in rows
                if product_id in r["definition"]["included_products"]
            ]

    async def issue_test(self, operator, billing_account_id, plan_id, issuance_key):
        require(operator, "test:issue")
        for value in (billing_account_id, plan_id, issuance_key):
            bounded(value)
        async with self.db.connection() as c:
            # An issuance key cannot be rebound to a different target wallet.
            await c.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (json.dumps(["test-issuance", operator.id, issuance_key]),),
            )
            old = await (
                await c.execute(
                    "SELECT * FROM memberships WHERE operator_id=%s AND issuance_key=%s",
                    (operator.id, issuance_key),
                )
            ).fetchone()
            if old:
                if (old["billing_account_id"], old["plan_id"]) != (
                    billing_account_id,
                    plan_id,
                ):
                    raise PlatformError(409, "idempotency_conflict")
                return issuance_view(old)
            target = await (
                await c.execute(
                    "SELECT id FROM billing_accounts WHERE id=%s FOR UPDATE",
                    (billing_account_id,),
                )
            ).fetchone()
            if not target:
                raise PlatformError(404, "billing_account_not_found")
            row = await (
                await c.execute("SELECT definition FROM plans WHERE id=%s", (plan_id,))
            ).fetchone()
            if not row:
                raise PlatformError(404, "plan_not_found")
            plan = row["definition"]
            if plan["test_only"] is not True:
                raise PlatformError(403, "test_issuance_requires_test_plan")
            credits = int(plan["credits"])
            total = await (
                await c.execute(
                    "SELECT coalesce(sum(issued),0) AS n FROM credit_lots WHERE billing_account_id=%s",
                    (billing_account_id,),
                )
            ).fetchone()
            if total["n"] + credits > MAX_CREDITS:
                raise PlatformError(409, "wallet_capacity_exceeded")
            now = (
                await (await c.execute("SELECT clock_timestamp() AS now")).fetchone()
            )["now"]
            mid = str(uuid4())
            end = now + timedelta(days=plan["days"])
            membership = await (
                await c.execute(
                    """INSERT INTO memberships(id,billing_account_id,plan_id,operator_id,issuance_key,starts_at,expires_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                    (
                        mid,
                        billing_account_id,
                        plan_id,
                        operator.id,
                        issuance_key,
                        now,
                        end,
                    ),
                )
            ).fetchone()
            await c.execute(
                """INSERT INTO credit_lots(id,billing_account_id,membership_id,issued,available,expires_at)
                VALUES (%s,%s,%s,%s,%s,%s)""",
                (str(uuid4()), billing_account_id, mid, credits, credits, end),
            )
            await journal(
                c,
                billing_account_id,
                "issue",
                mid,
                {"issuance": -credits, "available": credits},
                operator.id,
            )
            return issuance_view(membership)

    async def entitlements(self, scope):
        from .internal_features import entitled_features
        async with self.db.connection() as c:
            await authorize(c, scope)
            memberships = await active_plans(c, scope)
            internal_ids = {r["membership_id"] for r in await (await c.execute(
                "SELECT membership_id FROM internal_grants WHERE billing_account_id=%s",
                (scope.billing_account_id,),
            )).fetchall()}
            return {
                "experience_active": any(m["id"] in internal_ids for m in memberships),
                "product_id": scope.product_id,
                "active": bool(memberships),
                "test_only": all(m["definition"]["test_only"] for m in memberships),
                "features": await entitled_features(c,scope,memberships),
                "concurrency": max(
                    (m["definition"]["concurrency"] for m in memberships), default=0
                ),
                "memberships": [
                    {
                        "id": m["id"],
                        "plan_id": m["definition"]["id"],
                        "expires_at": m["expires_at"].isoformat(),
                        "test_only": m["definition"]["test_only"],
                        "funding_source": "internal_experience" if m["id"] in internal_ids else "test" if m["definition"]["test_only"] else "live",
                    }
                    for m in memberships
                ],
            }
