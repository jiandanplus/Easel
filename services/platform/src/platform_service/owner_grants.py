"""Owner-only fixed allowance while payments stay off (decision 0010). No HTTP surface."""
import hashlib
import json
from datetime import timedelta
from uuid import uuid4

from psycopg.types.json import Jsonb

from .credits import MAX_CREDITS, bounded, journal
from .errors import PlatformError
from .membership import require

CREDITS = 100000000
DAYS = 365
PLAN = {
    "id": "easel-owner-allowance-v1", "name": "所有者额度，非付款", "version": "1",
    "included_products": ["easel"], "features": ["text", "image"], "credits": str(CREDITS),
    "days": DAYS, "concurrency": 1, "test_only": False,
}


def grant_view(row):
    return {"id": row["id"], "membership_id": row["membership_id"],
            "billing_account_id": row["billing_account_id"], "plan_id": PLAN["id"],
            "credits": PLAN["credits"], "features": PLAN["features"],
            "starts_at": row["starts_at"].isoformat(), "expires_at": row["expires_at"].isoformat(),
            "test_only": False, "funding_source": "owner_allowance", "notice": PLAN["name"]}


class OwnerGrantService:
    def __init__(self, db):
        self.db = db

    async def issue(self, operator, *, wallet, key, reason, approval_reference):
        require(operator, "owner:grant")
        for value in (wallet, key, reason, approval_reference, operator.id):
            bounded(value)
            if any(ord(char) < 32 or ord(char) == 127 for char in value):
                raise PlatformError(422, "invalid_owner_grant_parameter")
        async with self.db.connection() as c:
            await c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                            (json.dumps(["owner-grant", key]),))
            if not await (await c.execute("SELECT id FROM billing_accounts WHERE id=%s FOR UPDATE", (wallet,))).fetchone():
                raise PlatformError(404, "billing_account_not_found")
            old = await (await c.execute("SELECT * FROM owner_grants WHERE issuance_key=%s", (key,))).fetchone()
            if old:
                if (old["billing_account_id"], old["reason"], old["approval_reference"], old["operator_id"]) != (wallet, reason, approval_reference, operator.id):
                    raise PlatformError(409, "idempotency_conflict")
                return grant_view(old)
            active = await (await c.execute("""SELECT 1 FROM owner_grants g WHERE g.billing_account_id=%s
                AND g.expires_at>clock_timestamp() AND NOT EXISTS(
                    SELECT 1 FROM membership_revocations r WHERE r.membership_id=g.membership_id)""", (wallet,))).fetchone()
            if active:
                raise PlatformError(409, "owner_grant_active")
            await c.execute("INSERT INTO plans(id,name,version,definition,created_by) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                            (PLAN["id"], PLAN["name"], PLAN["version"], Jsonb(PLAN), operator.id))
            plan = await (await c.execute("SELECT definition FROM plans WHERE id=%s", (PLAN["id"],))).fetchone()
            if not plan or plan["definition"] != PLAN:
                raise PlatformError(409, "plan_version_immutable")
            total = await (await c.execute("SELECT coalesce(sum(issued),0) AS n FROM credit_lots WHERE billing_account_id=%s", (wallet,))).fetchone()
            if total["n"] + CREDITS > MAX_CREDITS:
                raise PlatformError(409, "wallet_capacity_exceeded")
            now = (await (await c.execute("SELECT clock_timestamp() AS now")).fetchone())["now"]
            end = now + timedelta(days=DAYS)
            mid, lid, gid = str(uuid4()), str(uuid4()), str(uuid4())
            source = "owner-allowance:" + hashlib.sha256(key.encode()).hexdigest()
            await c.execute("INSERT INTO memberships(id,billing_account_id,plan_id,operator_id,issuance_key,starts_at,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                            (mid, wallet, PLAN["id"], operator.id, source, now, end))
            await c.execute("INSERT INTO credit_lots(id,billing_account_id,membership_id,issued,available,expires_at,test_only) VALUES (%s,%s,%s,%s,%s,%s,false)",
                            (lid, wallet, mid, CREDITS, CREDITS, end))
            await journal(c, wallet, "issue", mid, {"issuance": -CREDITS, "available": CREDITS}, operator.id)
            row = await (await c.execute("""INSERT INTO owner_grants(id,billing_account_id,membership_id,lot_id,operator_id,
                issuance_key,reason,approval_reference,source,starts_at,expires_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (gid, wallet, mid, lid, operator.id, key, reason, approval_reference, source, now, end))).fetchone()
            return grant_view(row)
