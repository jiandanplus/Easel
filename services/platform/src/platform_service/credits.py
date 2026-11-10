"""Trusted internal wallet operations. No public price or balance write surface."""

from uuid import uuid4
from .errors import PlatformError

MAX_CREDITS = 2**63 - 1
PENDING_RECONCILIATION_LIMIT = 3


def integer(value, *, positive=False):
    if type(value) is not int or value < int(positive) or value > MAX_CREDITS:
        raise PlatformError(422, "invalid_microcredits")
    return value


def bounded(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise PlatformError(422, "invalid_identifier")
    return value


def price_microcredits(units, numerator, denominator=1):
    """Apply a rational integer rate, rounding the complete operation up once."""
    integer(units)
    integer(numerator)
    integer(denominator, positive=True)
    return integer((units * numerator + denominator - 1) // denominator)


async def authorize(c, scope, *, lock=True):
    # One canonical wallet lock serializes all products and all lot transitions.
    query = """SELECT b.id FROM billing_accounts b
        JOIN tenant_members m ON m.tenant_id=b.tenant_id AND m.user_id=%s
        JOIN users u ON u.id=m.user_id
        JOIN products p ON p.id=%s
        WHERE b.id=%s AND b.tenant_id=%s AND m.active AND u.active AND p.enabled
        AND u.authorization_epoch=%s"""
    if lock:
        query += " FOR UPDATE OF b"
    row = await (
        await c.execute(
            query,
            (
                scope.user_id,
                scope.product_id,
                scope.billing_account_id,
                scope.tenant_id,
                scope.authorization_epoch,
            ),
        )
    ).fetchone()
    if not row:
        raise PlatformError(403, "scope_denied")


async def active_plans(c, scope, funding_mode=None):
    rows = await (
        await c.execute(
            """SELECT m.id,m.expires_at,p.definition FROM memberships m
        JOIN plans p ON p.id=m.plan_id WHERE m.billing_account_id=%s
        AND m.starts_at<=clock_timestamp() AND m.expires_at>clock_timestamp()
        AND NOT EXISTS(SELECT 1 FROM membership_revocations r WHERE r.membership_id=m.id)
        ORDER BY m.created_at,m.id""",
            (scope.billing_account_id,),
        )
    ).fetchall()
    return [
        r
        for r in rows
        if scope.product_id in r["definition"]["included_products"]
        and (
            funding_mode is None
            or r["definition"]["test_only"] == (funding_mode == "test")
        )
    ]


async def journal(c, account, kind, source, changes, operator_id=None):
    changes = {k: v for k, v in changes.items() if v}
    if not changes:
        return
    jid = str(uuid4())
    await c.execute(
        "INSERT INTO credit_journals(id,billing_account_id,kind,source,operator_id) VALUES (%s,%s,%s,%s,%s)",
        (jid, account, kind, source, operator_id),
    )
    for bucket, delta in changes.items():
        await c.execute(
            "INSERT INTO credit_entries(journal_id,billing_account_id,bucket,delta) VALUES (%s,%s,%s,%s)",
            (jid, account, bucket, delta),
        )


async def expire(c, account):
    rows = await (
        await c.execute(
            """SELECT id,available FROM credit_lots WHERE billing_account_id=%s
        AND expires_at<=clock_timestamp() AND available>0 ORDER BY expires_at,id FOR UPDATE""",
            (account,),
        )
    ).fetchall()
    for row in rows:
        await c.execute(
            "UPDATE credit_lots SET available=0,expired=expired+%s WHERE id=%s",
            (row["available"], row["id"]),
        )
        await journal(
            c,
            account,
            "expire",
            str(uuid4()),
            {"available": -row["available"], "expired": row["available"]},
        )


def reservation_view(row):
    return {
        k: (
            str(v)
            if k in ("amount", "actual") and v is not None
            else v.isoformat()
            if hasattr(v, "isoformat")
            else v
        )
        for k, v in row.items()
        if k
        in (
            "id",
            "operation",
            "product_id",
            "amount",
            "actual",
            "status",
            "rate_version",
            "created_at",
            "updated_at",
        )
    }


def grant_source(lot):
    """Where a grant of points came from, in words the desktop can show."""
    operator = lot.get("operator_id") or ""
    if lot.get("internal"):
        return "internal"
    if lot.get("plan_id") == "easel-owner-allowance-v1":
        return "owner"
    if operator == "billing:verified":
        return "purchase"
    if operator.startswith("redemption:"):
        return "redemption"
    if operator.startswith("console"):
        return "console"
    return "test" if lot.get("test_only") else "other"


def grant_view(lot):
    return {
        "id": lot["id"],
        "source": grant_source(lot),
        "plan_id": lot.get("plan_id"),
        "plan_name": lot.get("plan_name"),
        **{k: str(lot[k]) for k in ("issued", "available", "spent", "expired", "refunded")},
        "frozen": lot["frozen"],
        "revoked": lot["revoked"],
        "starts_at": lot["starts_at"].isoformat() if lot.get("starts_at") else None,
        "expires_at": lot["expires_at"].isoformat(),
    }


class CreditLedger:
    def __init__(self, db):
        self.db = db

    async def summary(self, scope):
        async with self.db.connection() as c:
            await authorize(c, scope)
            await expire(c, scope.billing_account_id)
            row = await (
                await c.execute(
                    """SELECT coalesce(sum(available) FILTER(WHERE NOT frozen),0) AS available,
                coalesce(sum(held),0) AS reserved,coalesce(sum(spent),0) AS spent,
                coalesce(sum(expired),0) AS expired,coalesce(sum(issued),0) AS issued,
                coalesce(sum(available) FILTER(WHERE frozen),0) AS frozen,
                coalesce(sum(refunded),0) AS refunded,
                coalesce(sum(available) FILTER(WHERE NOT frozen AND test_only),0) AS test_available,
                coalesce(sum(available) FILTER(WHERE NOT frozen AND NOT test_only),0) AS live_available,
                coalesce(bool_or(test_only),false) AS has_test,
                coalesce(bool_or(NOT test_only),false) AS has_live
                FROM credit_lots WHERE billing_account_id=%s""",
                    (scope.billing_account_id,),
                )
            ).fetchone()
            n = await (
                await c.execute(
                    """SELECT count(*) FILTER(WHERE status='pending_reconciliation') AS pending,
                    count(*) FILTER(WHERE status IN ('reserved','dispatched')) AS active
                    FROM credit_reservations WHERE billing_account_id=%s
                    AND status IN ('reserved','dispatched','pending_reconciliation')""",
                    (scope.billing_account_id,),
                )
            ).fetchone()
            experience = await (await c.execute("""SELECT
                coalesce(sum(l.available) FILTER(WHERE NOT l.frozen AND NOT EXISTS(
                    SELECT 1 FROM membership_revocations r WHERE r.membership_id=g.membership_id)),0) AS available,
                coalesce(sum(l.issued),0) AS issued,
                max(g.expires_at) AS expires_at
                FROM internal_grants g JOIN credit_lots l ON l.id=g.lot_id
                WHERE g.billing_account_id=%s""", (scope.billing_account_id,))).fetchone()
            has_test, has_live = row.pop("has_test"), row.pop("has_live")
            # Every grant of points, newest first, for the desktop 余额 page ("我的充值").
            lots = await (await c.execute("""SELECT l.id,l.issued,l.available,l.spent,l.expired,l.refunded,l.frozen,l.test_only,l.expires_at,
                m.plan_id,m.operator_id,m.starts_at,p.name AS plan_name,
                EXISTS(SELECT 1 FROM membership_revocations r WHERE r.membership_id=l.membership_id) AS revoked,
                EXISTS(SELECT 1 FROM internal_grants g WHERE g.lot_id=l.id) AS internal
                FROM credit_lots l LEFT JOIN memberships m ON m.id=l.membership_id LEFT JOIN plans p ON p.id=m.plan_id
                WHERE l.billing_account_id=%s ORDER BY coalesce(m.starts_at,l.expires_at) DESC,l.id DESC LIMIT 100""",
                (scope.billing_account_id,))).fetchall()
            return {
                "unit": "microcredit",
                "microcredits_per_point": "1000000",
                **{k: str(v) for k, v in row.items()},
                "experience_available": str(experience["available"]),
                "experience_issued": str(experience["issued"]),
                "experience_expires_at": experience["expires_at"].isoformat() if experience["expires_at"] else None,
                "pending_reconciliation": n["pending"],
                "active_requests": n["active"],
                "pending_reconciliation_limit": PENDING_RECONCILIATION_LIMIT,
                "test_only": not has_live,
                "grants": [grant_view(lot) for lot in lots],
                "funding_mode": "mixed"
                if has_test and has_live
                else "test"
                if has_test
                else "live"
                if has_live
                else "empty",
            }

    async def usage(self, scope):
        async with self.db.connection() as c:
            await authorize(c, scope)
            rows = await (
                await c.execute(
                    "SELECT * FROM credit_reservations WHERE billing_account_id=%s AND product_id=%s ORDER BY created_at DESC,id DESC LIMIT 200",
                    (scope.billing_account_id, scope.product_id),
                )
            ).fetchall()
            views = [reservation_view(r) for r in rows]
            # The desktop usage page shows which model each charge was for, and tokens for text.
            details = {}
            ids = [r["id"] for r in rows]
            if ids and (await (await c.execute("SELECT to_regclass('execution_jobs') IS NOT NULL AS ok")).fetchone())["ok"]:
                for row in await (await c.execute(
                        "SELECT j.reservation_id,j.model,r.input_tokens,r.output_tokens FROM execution_jobs j"
                        " LEFT JOIN execution_receipts r ON r.job_id=j.id WHERE j.reservation_id=ANY(%s)", (ids,))).fetchall():
                    details[row["reservation_id"]] = {"model": row["model"], "input_tokens": row["input_tokens"], "output_tokens": row["output_tokens"]}
            if ids and (await (await c.execute("SELECT to_regclass('media_jobs') IS NOT NULL AS ok")).fetchone())["ok"]:
                for row in await (await c.execute(
                        "SELECT reservation_id,model,capability FROM media_jobs WHERE reservation_id=ANY(%s)", (ids,))).fetchall():
                    details[row["reservation_id"]] = {"model": row["model"], "capability": row["capability"]}
            return [{**view, **details.get(view["id"], {})} for view in views]

    async def reserve(
        self,
        scope,
        *,
        operation,
        key,
        body_hash,
        amount,
        rate_version,
        funding_mode="test",
    ):
        async with self.db.connection() as c:
            return await self.reserve_in_transaction(
                c,
                scope,
                operation=operation,
                key=key,
                body_hash=body_hash,
                amount=amount,
                rate_version=rate_version,
                funding_mode=funding_mode,
            )

    async def reserve_in_transaction(
        self,
        c,
        scope,
        *,
        operation,
        key,
        body_hash,
        amount,
        rate_version,
        funding_mode="test",
        authorize_scope=True,
        membership_ids=None,
    ):
        """Caller owns transaction; execution callers already hold canonical wallet."""
        if funding_mode not in ("test", "live"):
            raise PlatformError(422, "invalid_funding_mode")
        integer(amount, positive=True)
        for value in (operation, key, body_hash, rate_version):
            bounded(value)
        if authorize_scope:
            await authorize(c, scope)
        old = await (
            await c.execute(
                """SELECT * FROM credit_reservations WHERE billing_account_id=%s
            AND product_id=%s AND operation=%s AND idempotency_key=%s""",
                (scope.billing_account_id, scope.product_id, operation, key),
            )
        ).fetchone()
        if old:
            if (
                old["user_id"],
                old["body_hash"],
                old["amount"],
                old["rate_version"],
                old["funding_mode"],
            ) != (scope.user_id, body_hash, amount, rate_version, funding_mode):
                raise PlatformError(409, "idempotency_conflict")
            return reservation_view(old)
        plans = await active_plans(c, scope, funding_mode)
        if not plans:
            raise PlatformError(403, "membership_required")
        count = await (
            await c.execute(
                """SELECT count(*) FILTER(WHERE status IN ('reserved','dispatched')) AS active,
                count(*) FILTER(WHERE status='pending_reconciliation') AS pending
                FROM credit_reservations WHERE billing_account_id=%s
                AND status IN ('reserved','dispatched','pending_reconciliation')""",
                (scope.billing_account_id,),
            )
        ).fetchone()
        # Unknown costs retain their holds but have a separate, wallet-wide
        # admission ceiling. The canonical wallet lock covers both counts.
        if count["pending"] >= PENDING_RECONCILIATION_LIMIT:
            raise PlatformError(409, "pending_reconciliation_limit")
        if count["active"] >= max(p["definition"]["concurrency"] for p in plans):
            raise PlatformError(409, "concurrency_limit")
        await expire(c, scope.billing_account_id)
        lots = await (
            await c.execute(
                """SELECT * FROM credit_lots WHERE billing_account_id=%s AND available>0
            AND NOT frozen AND test_only=%s AND (%s::text[] IS NULL OR membership_id=ANY(%s::text[]))
            AND expires_at>clock_timestamp() ORDER BY expires_at,id FOR UPDATE""",
                (scope.billing_account_id, funding_mode == "test", membership_ids, membership_ids),
            )
        ).fetchall()
        if sum(lot["available"] for lot in lots) < amount:
            raise PlatformError(409, "insufficient_credits")
        rid = str(uuid4())
        row = await (
            await c.execute(
                """INSERT INTO credit_reservations(id,billing_account_id,tenant_id,user_id,product_id,operation,idempotency_key,body_hash,rate_version,amount,status,funding_mode)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'reserved',%s) RETURNING *""",
                (
                    rid,
                    scope.billing_account_id,
                    scope.tenant_id,
                    scope.user_id,
                    scope.product_id,
                    operation,
                    key,
                    body_hash,
                    rate_version,
                    amount,
                    funding_mode,
                ),
            )
        ).fetchone()
        remaining = amount
        for lot in lots:
            take = min(remaining, lot["available"])
            if not take:
                break
            await c.execute(
                "UPDATE credit_lots SET available=available-%s,held=held+%s WHERE id=%s",
                (take, take, lot["id"]),
            )
            await c.execute(
                "INSERT INTO credit_holds(reservation_id,lot_id,billing_account_id,amount) VALUES (%s,%s,%s,%s)",
                (rid, lot["id"], scope.billing_account_id, take),
            )
            remaining -= take
        await journal(
            c,
            scope.billing_account_id,
            "reserve",
            rid,
            {"available": -amount, "held": amount},
        )
        return reservation_view(row)

    async def _get(self, c, scope, rid):
        bounded(rid)
        await authorize(c, scope)
        row = await (
            await c.execute(
                """SELECT * FROM credit_reservations WHERE id=%s AND billing_account_id=%s
            AND tenant_id=%s AND user_id=%s AND product_id=%s FOR UPDATE""",
                (
                    rid,
                    scope.billing_account_id,
                    scope.tenant_id,
                    scope.user_id,
                    scope.product_id,
                ),
            )
        ).fetchone()
        if not row:
            raise PlatformError(404, "reservation_not_found")
        return row

    async def _status(self, scope, rid, target, allowed):
        async with self.db.connection() as c:
            row = await self._get(c, scope, rid)
            if row["status"] == target:
                return reservation_view(row)
            if row["status"] not in allowed:
                raise PlatformError(409, "reservation_state_conflict")
            row = await (
                await c.execute(
                    "UPDATE credit_reservations SET status=%s,updated_at=clock_timestamp() WHERE id=%s RETURNING *",
                    (target, rid),
                )
            ).fetchone()
            return reservation_view(row)

    async def dispatch(self, scope, reservation_id):
        return await self._status(scope, reservation_id, "dispatched", {"reserved"})

    async def pending(self, scope, reservation_id):
        return await self._status(
            scope, reservation_id, "pending_reconciliation", {"dispatched"}
        )

    async def settle(self, scope, reservation_id, *, actual, key):
        integer(actual)
        bounded(key)
        over_budget = False
        async with self.db.connection() as c:
            row = await self._get(c, scope, reservation_id)
            if row["status"] == "settled":
                if row["actual"] != actual or row["completion_key"] != key:
                    raise PlatformError(409, "idempotency_conflict")
                return reservation_view(row)
            if row["status"] not in ("dispatched", "pending_reconciliation"):
                raise PlatformError(409, "reservation_state_conflict")
            if actual > row["amount"]:
                await c.execute(
                    "UPDATE credit_reservations SET status='pending_reconciliation',updated_at=clock_timestamp() WHERE id=%s",
                    (reservation_id,),
                )
                over_budget = True
            else:
                result = await self._complete(c, scope, row, actual, key, "settled")
        # Preserve hold and pending state even when the caller receives a rejection.
        if over_budget:
            raise PlatformError(409, "settlement_exceeds_reservation")
        return result

    async def release(self, scope, reservation_id, *, key):
        bounded(key)
        async with self.db.connection() as c:
            row = await self._get(c, scope, reservation_id)
            if row["status"] == "released":
                if row["completion_key"] != key:
                    raise PlatformError(409, "idempotency_conflict")
                return reservation_view(row)
            if row["status"] != "reserved":
                raise PlatformError(409, "unknown_cost_cannot_release")
            return await self._complete(c, scope, row, 0, key, "released")

    async def _complete(self, c, scope, row, actual, key, status):
        integer(actual)
        if status not in ('settled', 'released', 'waived') or (status != 'settled' and actual != 0):
            raise PlatformError(422, 'invalid_reservation_completion')
        await expire(c, scope.billing_account_id)
        holds = await (
            await c.execute(
                """SELECT h.amount,l.id,l.expires_at<=clock_timestamp() AS expired
            FROM credit_holds h JOIN credit_lots l ON l.id=h.lot_id WHERE h.reservation_id=%s
            ORDER BY l.expires_at,l.id FOR UPDATE OF l""",
                (row["id"],),
            )
        ).fetchall()
        remaining = actual
        changes = {
            "held": -row["amount"],
            "spent": actual,
            "available": 0,
            "expired": 0,
        }
        for hold in holds:
            spend = min(remaining, hold["amount"])
            remaining -= spend
            released = hold["amount"] - spend
            available = 0 if hold["expired"] else released
            expired = released if hold["expired"] else 0
            await c.execute(
                """UPDATE credit_lots SET held=held-%s,spent=spent+%s,available=available+%s,
                expired=expired+%s WHERE id=%s""",
                (hold["amount"], spend, available, expired, hold["id"]),
            )
            changes["available"] += available
            changes["expired"] += expired
        await journal(
            c,
            scope.billing_account_id,
            "settle" if status == "settled" else "release",
            row["id"],
            changes,
        )
        result = await (
            await c.execute(
                """UPDATE credit_reservations SET status=%s,actual=%s,completion_key=%s,
            updated_at=clock_timestamp() WHERE id=%s RETURNING *""",
                (status, None if status == 'waived' else actual, key, row["id"]),
            )
        ).fetchone()
        return reservation_view(result)
