import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from psycopg.types.json import Jsonb
from ..credits import authorize, bounded, integer, journal, MAX_CREDITS, expire
from ..errors import PlatformError
from ..membership import require


def decimal_amount(value):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,18}", value):
        raise PlatformError(422, "invalid_amount_minor")
    return integer(int(value), positive=True)


def aware(value):
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError()
        return parsed
    except (ValueError, TypeError):
        raise PlatformError(422, "invalid_payment_timestamp") from None


def refund_view(row):
    if not row:
        return None
    return {
        k: str(v)
        if k == "amount_minor"
        else v.isoformat()
        if isinstance(v, datetime)
        else v
        for k, v in row.items()
        if k
        in (
            "id",
            "order_id",
            "amount_minor",
            "status",
            "created_at",
            "updated_at",
            "reconciliation_reason",
        )
    }


class BillingService:
    def __init__(self, db, mode="disabled", channels=()):
        if mode not in ("disabled", "test", "live"):
            raise ValueError("invalid payments mode")
        self.db, self.mode = db, mode
        self.channels = {c["id"]: dict(c) for c in channels if c.get("enabled", True)}
        if any(k not in ("wechat", "alipay") for k in self.channels):
            raise ValueError("invalid payment channel")

    async def publish_offer(self, operator, offer):
        require(operator, "billing:publish")
        if not isinstance(offer, dict) or set(offer) != {
            "id",
            "plan_id",
            "version",
            "amount_minor",
            "currency",
            "test_only",
            "terms",
        }:
            raise PlatformError(422, "invalid_offer")
        for key in ("id", "plan_id", "version"):
            bounded(offer[key])
        decimal_amount(offer["amount_minor"])
        terms = offer["terms"]
        if (
            offer["currency"] != "CNY"
            or type(offer["test_only"]) is not bool
            or not isinstance(terms, dict)
            or set(terms) != {"refund_policy", "description"}
            or terms["refund_policy"] != "unused_full_only"
        ):
            raise PlatformError(422, "invalid_offer")
        bounded(terms["description"])
        async with self.db.connection() as c:
            await c.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('central:offer-publication',0))"
            )
            plan = await (
                await c.execute(
                    "SELECT definition FROM plans WHERE id=%s", (offer["plan_id"],)
                )
            ).fetchone()
            if not plan or plan["definition"]["test_only"] != offer["test_only"]:
                raise PlatformError(422, "offer_plan_mode_mismatch")
            p = plan["definition"]
            snapshot = {
                **offer,
                **{
                    k: p[k]
                    for k in (
                        "name",
                        "credits",
                        "days",
                        "concurrency",
                        "included_products",
                        "features",
                    )
                },
            }
            old = await (
                await c.execute(
                    "SELECT definition FROM billing_offers WHERE id=%s OR (plan_id=%s AND version=%s)",
                    (offer["id"], offer["plan_id"], offer["version"]),
                )
            ).fetchone()
            if old:
                if old["definition"] != snapshot:
                    raise PlatformError(409, "offer_version_immutable")
                return old["definition"]
            await c.execute(
                "INSERT INTO billing_offers(id,plan_id,version,definition,created_by) VALUES (%s,%s,%s,%s,%s)",
                (
                    offer["id"],
                    offer["plan_id"],
                    offer["version"],
                    Jsonb(snapshot),
                    operator.id,
                ),
            )
            return snapshot

    async def withdraw_offer(self, operator, offer_id, reason):
        require(operator, "billing:publish")
        bounded(offer_id)
        bounded(reason)
        async with self.db.connection() as c:
            if not await (
                await c.execute(
                    "SELECT id FROM billing_offers WHERE id=%s", (offer_id,)
                )
            ).fetchone():
                raise PlatformError(404, "offer_not_found")
            await c.execute(
                "INSERT INTO billing_offer_withdrawals(offer_id,reason,operator_id) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                (offer_id, reason, operator.id),
            )
        return {"id": offer_id, "withdrawn": True}

    async def catalog(self, product_id):
        bounded(product_id)
        async with self.db.connection() as c:
            if not await (
                await c.execute(
                    "SELECT id FROM products WHERE id=%s AND enabled", (product_id,)
                )
            ).fetchone():
                raise PlatformError(404, "product_not_found")
            rows = await (
                await c.execute(
                    "SELECT definition FROM billing_offers o WHERE NOT EXISTS (SELECT 1 FROM billing_offer_withdrawals w WHERE w.offer_id=o.id) ORDER BY o.created_at,o.id"
                )
            ).fetchall()
        offers = (
            [
                r["definition"]
                for r in rows
                if product_id in r["definition"]["included_products"]
                and r["definition"]["test_only"] == (self.mode == "test")
            ]
            if self.mode != "disabled"
            else []
        )
        channels = (
            [
                {"id": k, "label": v.get("label", k), "enabled": True}
                for k, v in self.channels.items()
            ]
            if self.mode != "disabled"
            else []
        )
        return {
            "mode": self.mode,
            "channels": channels,
            "offers": offers if channels else [],
        }

    async def _view(self, c, row):
        result = {
            k: str(v)
            if k in ("amount_minor", "revision")
            else v.isoformat()
            if isinstance(v, datetime)
            else v
            for k, v in row.items()
            if k
            in (
                "id",
                "status",
                "channel",
                "mode",
                "currency",
                "amount_minor",
                "revision",
                "offer",
                "created_at",
                "expires_at",
                "paid_at",
                "credit_state",
                "checkout",
            )
        }
        refund = await (
            await c.execute(
                "SELECT * FROM billing_refunds WHERE order_id=%s ORDER BY created_at DESC,id DESC LIMIT 1",
                (row["id"],),
            )
        ).fetchone()
        result["refund"] = refund_view(refund)
        return result

    async def _scope_order(self, c, scope, order_id):
        bounded(order_id)
        await authorize(c, scope)
        row = await (
            await c.execute(
                """SELECT * FROM billing_orders WHERE id=%s AND user_id=%s
            AND product_id=%s AND tenant_id=%s AND billing_account_id=%s FOR UPDATE""",
                (
                    order_id,
                    scope.user_id,
                    scope.product_id,
                    scope.tenant_id,
                    scope.billing_account_id,
                ),
            )
        ).fetchone()
        if not row:
            raise PlatformError(404, "order_not_found")
        return row

    async def _lock_order(self, c, order_id):
        row = await (
            await c.execute(
                "SELECT billing_account_id FROM billing_orders WHERE id=%s", (order_id,)
            )
        ).fetchone()
        if not row:
            raise PlatformError(404, "order_not_found")
        await c.execute(
            "SELECT id FROM billing_accounts WHERE id=%s FOR UPDATE",
            (row["billing_account_id"],),
        )
        return await (
            await c.execute(
                "SELECT * FROM billing_orders WHERE id=%s FOR UPDATE", (order_id,)
            )
        ).fetchone()

    async def get_order(self, scope, order_id):
        async with self.db.connection() as c:
            return await self._view(c, await self._scope_order(c, scope, order_id))

    async def orders(self, scope):
        async with self.db.connection() as c:
            await authorize(c, scope)
            rows = await (
                await c.execute(
                    """SELECT * FROM billing_orders WHERE user_id=%s AND product_id=%s
                AND tenant_id=%s AND billing_account_id=%s ORDER BY created_at DESC,id DESC LIMIT 200""",
                    (
                        scope.user_id,
                        scope.product_id,
                        scope.tenant_id,
                        scope.billing_account_id,
                    ),
                )
            ).fetchall()
            return [await self._view(c, r) for r in rows]

    async def create_order(self, scope, *, offer_id, channel, idempotency_key):
        for value in (offer_id, channel, idempotency_key):
            bounded(value)
        if self.mode == "disabled" or channel not in self.channels:
            raise PlatformError(503, "payments_unavailable")
        body_hash = hashlib.sha256(
            json.dumps([offer_id, channel, self.mode]).encode()
        ).hexdigest()
        async with self.db.connection() as c:
            await authorize(c, scope)
            old = await (
                await c.execute(
                    """SELECT * FROM billing_orders WHERE user_id=%s AND product_id=%s
                AND tenant_id=%s AND billing_account_id=%s AND idempotency_key=%s""",
                    (
                        scope.user_id,
                        scope.product_id,
                        scope.tenant_id,
                        scope.billing_account_id,
                        idempotency_key,
                    ),
                )
            ).fetchone()
            if old:
                if old["body_hash"] != body_hash:
                    raise PlatformError(409, "idempotency_conflict")
                return await self._view(c, old)
            row = await (
                await c.execute(
                    """SELECT definition FROM billing_offers o WHERE id=%s
                AND NOT EXISTS(SELECT 1 FROM billing_offer_withdrawals w WHERE w.offer_id=o.id)""",
                    (offer_id,),
                )
            ).fetchone()
            if not row:
                raise PlatformError(404, "offer_not_found")
            offer = row["definition"]
            if scope.product_id not in offer["included_products"] or offer[
                "test_only"
            ] != (self.mode == "test"):
                raise PlatformError(403, "offer_scope_denied")
            config = self.channels[channel]
            row = await (
                await c.execute(
                    """INSERT INTO billing_orders(id,user_id,product_id,tenant_id,billing_account_id,
                offer_id,offer,idempotency_key,body_hash,channel,mode,merchant_id,app_id,amount_minor,currency,expires_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'CNY',clock_timestamp()+interval '15 minutes') RETURNING *""",
                    (
                        uuid4().hex,
                        scope.user_id,
                        scope.product_id,
                        scope.tenant_id,
                        scope.billing_account_id,
                        offer_id,
                        Jsonb(offer),
                        idempotency_key,
                        body_hash,
                        channel,
                        self.mode,
                        config["merchant_id"],
                        config["app_id"],
                        int(offer["amount_minor"]),
                    ),
                )
            ).fetchone()
            await self._enqueue(c, row, "create_checkout")
            return await self._view(c, row)

    async def _enqueue(self, c, order, kind, refund=None, delay=0):
        payload = dict(
            order_id=order["id"],
            channel=order["channel"],
            mode=order["mode"],
            merchant_id=order["merchant_id"],
            app_id=order["app_id"],
            amount_minor=str(order["amount_minor"]),
            currency=order["currency"],
            description=order["offer"]["name"][:100],
            expires_at=order["expires_at"].isoformat(),
        )
        if refund:
            payload.update(
                refund_id=refund["id"],
                provider_transaction_id=order["provider_transaction_id"],
                provider_refund_id=refund["provider_refund_id"],
                refund_amount_minor=str(refund["amount_minor"]),
                reason=refund["reason"],
            )
        key = ":".join([kind, order["id"], refund["id"] if refund else ""])
        await c.execute(
            """INSERT INTO billing_outbox(id,kind,order_id,refund_id,dedupe_key,payload,due_at)
            VALUES (%s,%s,%s,%s,%s,%s,clock_timestamp()+%s*interval '1 second') ON CONFLICT(dedupe_key) DO NOTHING""",
            (
                uuid4().hex,
                kind,
                order["id"],
                refund["id"] if refund else None,
                key,
                Jsonb(payload),
                delay,
            ),
        )
        # Repeated manual refresh can reactivate a finished poll; leased work is untouched.
        if kind in ("query_order", "close_order", "query_refund"):
            await c.execute(
                "UPDATE billing_outbox SET status='pending',due_at=clock_timestamp()+%s*interval '1 second' WHERE dedupe_key=%s AND status='done'",
                (delay, key),
            )
            if delay == 0:
                await c.execute(
                    "UPDATE billing_outbox SET due_at=least(due_at,clock_timestamp()) WHERE dedupe_key=%s AND status='pending'",
                    (key,),
                )

    async def request_refresh(self, scope, order_id):
        async with self.db.connection() as c:
            row = await self._scope_order(c, scope, order_id)
            if row["status"] in ("creating", "pending", "closed"):
                await self._enqueue(c, row, "query_order")
            return await self._view(c, row)

    async def request_close(self, scope, order_id):
        async with self.db.connection() as c:
            row = await self._scope_order(c, scope, order_id)
            if row["status"] in ("creating", "pending"):
                await self._enqueue(c, row, "close_order")
            return await self._view(c, row)

    async def _audit(self, c, event_id, order_id, outcome, digest):
        await c.execute(
            "INSERT INTO payment_event_audits(event_id,order_id,outcome,digest) VALUES (%s,%s,%s,%s)",
            (event_id, order_id, outcome, digest),
        )

    async def apply_verified_event(self, event):
        async with self.db.connection() as c:
            return await self._apply_event(c, event)

    async def _apply_event(self, c, event):
        if not isinstance(event, dict):
            raise PlatformError(422, "invalid_payment_fact")
        required = {
            "channel",
            "mode",
            "event_id",
            "order_id",
            "provider_transaction_id",
            "status",
            "amount_minor",
            "currency",
            "merchant_id",
            "app_id",
            "occurred_at",
            "raw_digest",
        }
        if not required.issubset(event):
            raise PlatformError(422, "invalid_payment_fact")
        if (
            set(event)
            - required
            - {"refund_id", "provider_refund_id", "refund_amount_minor", "raw_status"}
        ):
            raise PlatformError(422, "invalid_payment_fact")
        for key in (
            "channel",
            "mode",
            "event_id",
            "order_id",
            "merchant_id",
            "app_id",
            "raw_digest",
        ):
            bounded(event[key])
        status = event["status"]
        if status not in (
            "paid",
            "pending",
            "closed",
            "refunded",
            "refund_pending",
            "refund_failed",
            "refund_observed",
        ):
            raise PlatformError(422, "invalid_payment_status")
        if status in ("pending", "closed", "refund_observed"):
            if event["amount_minor"] is not None:
                integer(event["amount_minor"], positive=True)
        else:
            integer(event["amount_minor"], positive=True)
        if status == "paid":
            bounded(event["provider_transaction_id"])
            aware(event["occurred_at"])
        if event["provider_transaction_id"] is not None:
            bounded(event["provider_transaction_id"])
        if event["occurred_at"] is not None:
            aware(event["occurred_at"])
        exists = await (
            await c.execute(
                "SELECT id FROM billing_orders WHERE id=%s", (event["order_id"],)
            )
        ).fetchone()
        if not exists:
            channel = self.channels.get(event["channel"])
            if (
                event["mode"] != self.mode
                or not channel
                or channel["merchant_id"] != event["merchant_id"]
                or channel["app_id"] != event["app_id"]
            ):
                raise PlatformError(409, "payment_binding_mismatch")
            serialized = json.dumps(event, sort_keys=True, separators=(",", ":"))
            if len(serialized) > 16384:
                raise PlatformError(422, "invalid_payment_fact")
            fingerprint = hashlib.sha256(serialized.encode()).hexdigest()
            await c.execute(
                """INSERT INTO payment_orphan_events(id,channel,mode,merchant_id,event_id,order_id,fingerprint,fact)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                (
                    uuid4().hex,
                    event["channel"],
                    event["mode"],
                    event["merchant_id"],
                    event["event_id"],
                    event["order_id"],
                    fingerprint,
                    Jsonb(event),
                ),
            )
            return {
                "id": event["order_id"],
                "status": "reconciliation_required",
                "credit_state": "not_granted",
                "reason": "unknown_order",
            }
        order = await self._lock_order(c, event["order_id"])
        if (
            any(
                event[k] != order[k]
                for k in ("channel", "mode", "merchant_id", "app_id")
            )
            or any(
                event[k] is not None and event[k] != order[k]
                for k in ("amount_minor", "currency")
            )
            or (
                status in ("paid", "refunded", "refund_pending", "refund_failed")
                and event["currency"] != order["currency"]
            )
        ):
            raise PlatformError(409, "payment_binding_mismatch")
        # Provider facts are immutable. Distinct replay deliveries receive audit rows.
        old = await (
            await c.execute(
                "SELECT * FROM payment_events WHERE channel=%s AND mode=%s AND merchant_id=%s AND event_id=%s",
                (
                    event["channel"],
                    event["mode"],
                    event["merchant_id"],
                    event["event_id"],
                ),
            )
        ).fetchone()
        if old:
            outcome = "duplicate" if old["fact"] == event else "event_key_conflict"
            await self._audit(c, old["id"], order["id"], outcome, event["raw_digest"])
            if outcome == "event_key_conflict":
                await self._mark_review(c, order, "event_key_conflict", freeze=False)
            return await self._current_view(c, order["id"])
        eid = uuid4().hex
        inserted = await (
            await c.execute(
                """INSERT INTO payment_events(id,channel,mode,merchant_id,event_id,order_id,fact)
            VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id""",
                (
                    eid,
                    event["channel"],
                    event["mode"],
                    event["merchant_id"],
                    event["event_id"],
                    order["id"],
                    Jsonb(event),
                ),
            )
        ).fetchone()
        if not inserted:
            # Concurrent use of a global event key on another wallet: recurse after its commit.
            return await self._apply_event(c, event)
        outcome = status
        if status == "paid":
            trade = await (
                await c.execute(
                    """INSERT INTO payment_transactions(channel,mode,merchant_id,provider_transaction_id,order_id,event_id)
                VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING order_id""",
                    (
                        order["channel"],
                        order["mode"],
                        order["merchant_id"],
                        event["provider_transaction_id"],
                        order["id"],
                        eid,
                    ),
                )
            ).fetchone()
            if not trade:
                trade = await (
                    await c.execute(
                        "SELECT order_id FROM payment_transactions WHERE channel=%s AND mode=%s AND merchant_id=%s AND provider_transaction_id=%s",
                        (
                            order["channel"],
                            order["mode"],
                            order["merchant_id"],
                            event["provider_transaction_id"],
                        ),
                    )
                ).fetchone()
            if trade["order_id"] != order["id"]:
                outcome = "transaction_order_conflict"
                await self._mark_review(c, order, outcome, freeze=False)
            elif order["membership_id"]:
                if order["provider_transaction_id"] != event["provider_transaction_id"]:
                    outcome = "additional_payment"
                    await self._mark_review(c, order, outcome, freeze=False)
                else:
                    outcome = "payment_replay"
            elif (
                order["status"] in ("refunded", "reconciliation_required")
                or order["credit_state"] == "manual_review"
            ):
                outcome = "paid_after_refund_or_review"
                await self._mark_review(c, order, outcome, freeze=False)
            else:
                outcome = "paid_after_closed" if order["status"] == "closed" else "paid"
                await self._grant(c, order, event)
        elif status == "closed":
            if (
                order["membership_id"]
                and order["status"] != "refunded"
                and order["channel"] == "alipay"
                and event.get("raw_status") == "TRADE_CLOSED"
            ):
                outcome = "refund_suspected"
                await self._refund_observed(c, order, outcome)
            elif order["membership_id"]:
                outcome = "stale_closed_after_payment"
            elif order["status"] in ("creating", "pending"):
                await c.execute(
                    "UPDATE billing_orders SET status='closed',checkout=NULL,updated_at=clock_timestamp() WHERE id=%s",
                    (order["id"],),
                )
        elif status == "refund_observed":
            await self._refund_observed(c, order, "refund_observed")
        elif status in ("refunded", "refund_pending", "refund_failed"):
            outcome = await self._refund_event(c, order, event)
        elif status == "pending" and order["status"] == "creating":
            await c.execute(
                "UPDATE billing_orders SET status='pending',updated_at=clock_timestamp() WHERE id=%s",
                (order["id"],),
            )
        await self._audit(c, eid, order["id"], outcome, event["raw_digest"])
        return await self._current_view(c, order["id"])

    async def _current_view(self, c, order_id):
        return await self._view(
            c,
            await (
                await c.execute("SELECT * FROM billing_orders WHERE id=%s", (order_id,))
            ).fetchone(),
        )

    async def _mark_review(self, c, order, reason, *, freeze):
        if freeze and order["membership_id"]:
            await c.execute(
                "UPDATE credit_lots SET frozen=true WHERE membership_id=%s",
                (order["membership_id"],),
            )
            await c.execute(
                "INSERT INTO membership_revocations(membership_id,reason,order_id) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                (order["membership_id"], reason, order["id"]),
            )
        await c.execute(
            "UPDATE billing_orders SET status='reconciliation_required',reconciliation_reason=%s,credit_state=CASE WHEN %s THEN 'manual_review' ELSE credit_state END,checkout=NULL,updated_at=clock_timestamp() WHERE id=%s",
            (reason, freeze, order["id"]),
        )

    async def _grant(self, c, order, event):
        offer = order["offer"]
        credits = int(offer["credits"])
        total = await (
            await c.execute(
                "SELECT coalesce(sum(issued),0) AS n FROM credit_lots WHERE billing_account_id=%s",
                (order["billing_account_id"],),
            )
        ).fetchone()
        if total["n"] + credits > MAX_CREDITS:
            await self._mark_review(c, order, "wallet_capacity_exceeded", freeze=False)
            return
        now = (await (await c.execute("SELECT clock_timestamp() AS now")).fetchone())[
            "now"
        ]
        end = now + timedelta(days=offer["days"])
        mid = uuid4().hex
        await c.execute(
            """INSERT INTO memberships(id,billing_account_id,plan_id,operator_id,issuance_key,starts_at,expires_at)
            VALUES (%s,%s,%s,'billing:verified',%s,%s,%s)""",
            (mid, order["billing_account_id"], offer["plan_id"], order["id"], now, end),
        )
        # A pending refund may precede its paid observation. Preserve its protection
        # from the instant the delayed grant becomes visible to the wallet.
        await c.execute(
            """INSERT INTO credit_lots(id,billing_account_id,membership_id,issued,available,expires_at,test_only,frozen)
            VALUES (%s,%s,%s,%s,%s,%s,%s,EXISTS(
                SELECT 1 FROM billing_refunds WHERE order_id=%s
                AND status IN ('pending','manual_reconciliation','succeeded')))""",
            (
                uuid4().hex,
                order["billing_account_id"],
                mid,
                credits,
                credits,
                end,
                offer["test_only"],
                order["id"],
            ),
        )
        await journal(
            c,
            order["billing_account_id"],
            "issue",
            mid,
            {"issuance": -credits, "available": credits},
            "billing:verified",
        )
        updated = await (
            await c.execute(
                "UPDATE billing_orders SET status='paid',credit_state='granted',membership_id=%s,provider_transaction_id=%s,paid_at=%s,checkout=NULL,updated_at=clock_timestamp() WHERE id=%s RETURNING *",
                (mid, event["provider_transaction_id"], now, order["id"]),
            )
        ).fetchone()
        await self._enqueue(c, updated, "order_paid")

    async def request_refund(self, operator, order_id, *, idempotency_key):
        require(operator, "billing:refund")
        bounded(order_id)
        bounded(idempotency_key)
        async with self.db.connection() as c:
            await c.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (json.dumps(["refund", operator.id, idempotency_key]),),
            )
            old = await (
                await c.execute(
                    "SELECT * FROM billing_refunds WHERE operator_id=%s AND idempotency_key=%s",
                    (operator.id, idempotency_key),
                )
            ).fetchone()
            if old:
                if old["order_id"] != order_id:
                    raise PlatformError(409, "idempotency_conflict")
                return refund_view(old)
            order = await self._lock_order(c, order_id)
            if (
                order["status"] != "paid"
                or order["offer"]["terms"]["refund_policy"] != "unused_full_only"
            ):
                raise PlatformError(409, "refund_not_eligible")
            await expire(c, order["billing_account_id"])
            lot = await (
                await c.execute(
                    "SELECT * FROM credit_lots WHERE membership_id=%s FOR UPDATE",
                    (order["membership_id"],),
                )
            ).fetchone()
            if (
                not lot
                or lot["frozen"]
                or lot["available"] != lot["issued"]
                or any(lot[k] for k in ("held", "spent", "expired", "refunded"))
            ):
                raise PlatformError(409, "refund_not_unused")
            await c.execute(
                "UPDATE credit_lots SET frozen=true WHERE id=%s", (lot["id"],)
            )
            refund = await (
                await c.execute(
                    "INSERT INTO billing_refunds(id,order_id,operator_id,idempotency_key,amount_minor,reason,status) VALUES (%s,%s,%s,%s,%s,'unused_full_only','pending') RETURNING *",
                    (
                        uuid4().hex,
                        order_id,
                        operator.id,
                        idempotency_key,
                        order["amount_minor"],
                    ),
                )
            ).fetchone()
            await self._enqueue(c, order, "request_refund", refund)
            return refund_view(refund)

    async def get_refund(self, operator, refund_id):
        require(operator, "billing:refund")
        bounded(refund_id)
        async with self.db.connection() as c:
            row = await (
                await c.execute(
                    "SELECT * FROM billing_refunds WHERE id=%s", (refund_id,)
                )
            ).fetchone()
            if not row:
                raise PlatformError(404, "refund_not_found")
            return refund_view(row)

    async def _refund_observed(self, c, order, reason):
        if order["status"] == "refunded":
            return
        refund = await (
            await c.execute(
                "SELECT * FROM billing_refunds WHERE order_id=%s AND status IN ('pending','succeeded','manual_reconciliation')",
                (order["id"],),
            )
        ).fetchone()
        if refund and refund["status"] == "succeeded":
            return
        if not refund:
            await c.execute(
                "INSERT INTO billing_refunds(id,order_id,operator_id,idempotency_key,amount_minor,reason,status,reconciliation_reason) VALUES (%s,%s,'billing:observed',%s,%s,%s,'manual_reconciliation',%s)",
                (
                    uuid4().hex,
                    order["id"],
                    order["id"],
                    order["amount_minor"],
                    reason,
                    reason,
                ),
            )
        elif refund["status"] == "pending":
            # Known in-progress refund is resolved by its signed query. Freeze now.
            await self._enqueue(c, order, "query_refund", refund, delay=5)
        await self._mark_review(c, order, reason, freeze=True)

    async def _refund_event(self, c, order, event):
        for field in ("refund_id", "provider_refund_id"):
            bounded(event.get(field))
        amount = integer(event.get("refund_amount_minor"), positive=True)
        rid = event["refund_id"]
        refund = await (
            await c.execute(
                "SELECT * FROM billing_refunds WHERE id=%s FOR UPDATE", (rid,)
            )
        ).fetchone()
        if refund and refund["order_id"] != order["id"]:
            await self._mark_review(c, order, "refund_order_conflict", freeze=True)
            return "refund_order_conflict"
        if not refund:
            existing = await (
                await c.execute(
                    "SELECT * FROM billing_refunds WHERE order_id=%s AND status IN ('pending','succeeded','manual_reconciliation')",
                    (order["id"],),
                )
            ).fetchone()
            if existing:
                if existing["status"] == "succeeded":
                    return "refund_replay"
                await c.execute(
                    "UPDATE billing_refunds SET status='manual_reconciliation',reconciliation_reason='unexpected_refund_identity',updated_at=clock_timestamp() WHERE id=%s",
                    (existing["id"],),
                )
                await self._mark_review(
                    c, order, "unexpected_refund_identity", freeze=True
                )
                return "unexpected_refund_identity"
            refund = await (
                await c.execute(
                    "INSERT INTO billing_refunds(id,order_id,operator_id,idempotency_key,amount_minor,reason,status,provider_refund_id) VALUES (%s,%s,'billing:external',%s,%s,'verified_external','pending',%s) RETURNING *",
                    (rid, order["id"], rid, amount, event["provider_refund_id"]),
                )
            ).fetchone()
        if refund["status"] == "succeeded":
            return "refund_replay"
        # A distinct delivery of the same terminal failure is still an audit fact,
        # but cannot mutate this refund or release a later refund's shared freeze.
        if refund["status"] == "failed" and event["status"] == "refund_failed":
            return "refund_failure_replay"
        if (
            order["provider_transaction_id"]
            and event["provider_transaction_id"] != order["provider_transaction_id"]
        ):
            await c.execute(
                "UPDATE billing_refunds SET status='manual_reconciliation',reconciliation_reason='refund_payment_mismatch' WHERE id=%s",
                (rid,),
            )
            await self._mark_review(c, order, "refund_payment_mismatch", freeze=True)
            return "refund_payment_mismatch"
        claimed = await (
            await c.execute(
                """INSERT INTO payment_refund_transactions(channel,mode,merchant_id,provider_refund_id,order_id,refund_id)
            VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING order_id,refund_id""",
                (
                    order["channel"],
                    order["mode"],
                    order["merchant_id"],
                    event["provider_refund_id"],
                    order["id"],
                    rid,
                ),
            )
        ).fetchone()
        if not claimed:
            claimed = await (
                await c.execute(
                    """SELECT order_id,refund_id FROM payment_refund_transactions
                WHERE channel=%s AND mode=%s AND merchant_id=%s AND provider_refund_id=%s""",
                    (
                        order["channel"],
                        order["mode"],
                        order["merchant_id"],
                        event["provider_refund_id"],
                    ),
                )
            ).fetchone()
        if (claimed["order_id"], claimed["refund_id"]) != (order["id"], rid):
            await c.execute(
                "UPDATE billing_refunds SET status='manual_reconciliation',reconciliation_reason='provider_refund_reused' WHERE id=%s",
                (rid,),
            )
            await self._mark_review(c, order, "provider_refund_reused", freeze=True)
            return "provider_refund_reused"
        if (
            refund["provider_refund_id"]
            and refund["provider_refund_id"] != event["provider_refund_id"]
        ):
            await self._mark_review(
                c, order, "refund_transaction_conflict", freeze=True
            )
            return "refund_transaction_conflict"
        await c.execute(
            "UPDATE billing_refunds SET provider_refund_id=%s,updated_at=clock_timestamp() WHERE id=%s",
            (event["provider_refund_id"], rid),
        )
        if amount != order["amount_minor"] or refund["amount_minor"] != amount:
            await c.execute(
                "UPDATE billing_refunds SET status='manual_reconciliation',reconciliation_reason='partial_or_wrong_refund' WHERE id=%s",
                (rid,),
            )
            await self._mark_review(c, order, "partial_or_wrong_refund", freeze=True)
            return "partial_or_wrong_refund"
        lot = await (
            await c.execute(
                "SELECT * FROM credit_lots WHERE membership_id=%s FOR UPDATE",
                (order["membership_id"],),
            )
        ).fetchone()
        if event["status"] == "refund_pending":
            if refund["status"] == "failed":
                return "stale_refund_pending"
            if lot:
                await c.execute(
                    "UPDATE credit_lots SET frozen=true WHERE id=%s", (lot["id"],)
                )
            await self._enqueue(c, order, "query_refund", refund, delay=10)
            return "refund_pending"
        if event["status"] == "refund_failed":
            if (
                refund["status"] == "manual_reconciliation"
                or order["credit_state"] == "manual_review"
            ):
                return "refund_failure_requires_review"
            await c.execute(
                "UPDATE billing_refunds SET status='failed',updated_at=clock_timestamp() WHERE id=%s",
                (rid,),
            )
            if lot and order["status"] != "reconciliation_required":
                await c.execute(
                    """UPDATE credit_lots SET frozen=false WHERE id=%s
                    AND NOT EXISTS(SELECT 1 FROM billing_refunds WHERE order_id=%s
                        AND status IN ('pending','manual_reconciliation','succeeded'))
                    AND NOT EXISTS(SELECT 1 FROM membership_revocations WHERE membership_id=%s)""",
                    (lot["id"], order["id"], order["membership_id"]),
                )
            return "refund_failed"
        # A late success for an earlier failed request supersedes any newer pending
        # request. Keep the newer row for manual reconciliation; never dispatch it.
        await c.execute(
            "UPDATE billing_refunds SET status='manual_reconciliation',reconciliation_reason='superseded_by_verified_refund',updated_at=clock_timestamp() WHERE order_id=%s AND id<>%s AND status='pending'",
            (order["id"], rid),
        )
        if not lot or lot["spent"] or lot["held"] or lot["refunded"]:
            await c.execute(
                "UPDATE billing_refunds SET status='manual_reconciliation',reconciliation_reason='refund_requires_accounting',updated_at=clock_timestamp() WHERE id=%s",
                (rid,),
            )
            await self._mark_review(c, order, "refund_requires_accounting", freeze=True)
            return "refund_requires_accounting"
        reclaim = lot["available"] + lot["expired"]
        await c.execute(
            "UPDATE credit_lots SET available=0,expired=0,refunded=refunded+%s,frozen=true WHERE id=%s",
            (reclaim, lot["id"]),
        )
        await journal(
            c,
            order["billing_account_id"],
            "refund",
            rid,
            {
                "available": -lot["available"],
                "expired": -lot["expired"],
                "refunded": reclaim,
            },
            "billing:verified",
        )
        await c.execute(
            "INSERT INTO membership_revocations(membership_id,reason,order_id) VALUES (%s,'refund',%s) ON CONFLICT DO NOTHING",
            (order["membership_id"], order["id"]),
        )
        await c.execute(
            "UPDATE billing_refunds SET status='succeeded',updated_at=clock_timestamp() WHERE id=%s",
            (rid,),
        )
        updated = await (
            await c.execute(
                "UPDATE billing_orders SET status='refunded',credit_state='revoked',checkout=NULL,updated_at=clock_timestamp() WHERE id=%s RETURNING *",
                (order["id"],),
            )
        ).fetchone()
        await self._enqueue(c, updated, "order_refunded", refund)
        return "refunded"

    async def claim_outbox(self, *, worker_id, limit=20, lease_seconds=60):
        bounded(worker_id)
        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or type(lease_seconds) is not int
            or not 1 <= lease_seconds <= 600
        ):
            raise PlatformError(422, "invalid_outbox_lease")
        if self.mode == "disabled" or not self.channels:
            return []
        async with self.db.connection() as c:
            # A mode switch never dispatches historical jobs into another environment.
            # Obsolete create/close/refund work is retained as completed history.
            await c.execute(
                """UPDATE billing_outbox j SET status='done',lease_token=NULL,lease_until=NULL
                FROM billing_orders o WHERE o.id=j.order_id AND o.mode=%s AND o.channel=ANY(%s)
                AND (j.status='pending' OR (j.status='leased' AND j.lease_until<=clock_timestamp()))
                AND ((j.kind IN ('create_checkout','close_order') AND o.status NOT IN ('creating','pending'))
                  OR (j.kind='query_order' AND o.status NOT IN ('creating','pending','closed'))
                  OR (j.kind IN ('request_refund','query_refund') AND EXISTS(
                    SELECT 1 FROM billing_refunds r WHERE r.id=j.refund_id AND r.status<>'pending')))""",
                (self.mode, list(self.channels)),
            )
            rows = await (
                await c.execute(
                    """SELECT j.* FROM billing_outbox j JOIN billing_orders o ON o.id=j.order_id
                WHERE o.mode=%s AND o.channel=ANY(%s) AND ((j.status='pending' AND j.due_at<=clock_timestamp())
                OR (j.status='leased' AND j.lease_until<=clock_timestamp()))
                ORDER BY j.due_at,j.created_at,j.id LIMIT %s FOR UPDATE OF j SKIP LOCKED""",
                    (self.mode, list(self.channels), limit),
                )
            ).fetchall()
            jobs = []
            for row in rows:
                token = uuid4().hex
                await c.execute(
                    "UPDATE billing_outbox SET status='leased',attempts=attempts+1,lease_token=%s,lease_until=clock_timestamp()+%s*interval '1 second' WHERE id=%s",
                    (token, lease_seconds, row["id"]),
                )
                jobs.append(
                    {
                        k: row[k]
                        for k in ("id", "kind", "order_id", "refund_id", "payload")
                    }
                    | {"lease_token": token, "attempts": row["attempts"] + 1}
                )
            return jobs

    async def finish_outbox(
        self, job_id, lease_token, *, result=None, error=None, retry_seconds=30
    ):
        bounded(job_id)
        bounded(lease_token)
        if type(retry_seconds) is not int or not 1 <= retry_seconds <= 3600:
            raise PlatformError(422, "invalid_retry_delay")
        async with self.db.connection() as c:
            hint = await (
                await c.execute(
                    "SELECT order_id FROM billing_outbox WHERE id=%s", (job_id,)
                )
            ).fetchone()
            if not hint:
                raise PlatformError(404, "outbox_not_found")
            order = await self._lock_order(c, hint["order_id"])
            job = await (
                await c.execute(
                    "SELECT *,lease_until>clock_timestamp() AS lease_valid FROM billing_outbox WHERE id=%s FOR UPDATE",
                    (job_id,),
                )
            ).fetchone()
            if (
                job["status"] != "leased"
                or job["lease_token"] != lease_token
                or not job["lease_valid"]
            ):
                raise PlatformError(409, "outbox_lease_lost")
            retry = False
            if error is not None:
                bounded(error)
                retry = True
                if job["kind"] in ("request_refund", "create_checkout", "close_order"):
                    refund = (
                        await (
                            await c.execute(
                                "SELECT * FROM billing_refunds WHERE id=%s",
                                (job["refund_id"],),
                            )
                        ).fetchone()
                        if job["refund_id"]
                        else None
                    )
                    await self._enqueue(
                        c,
                        order,
                        "query_refund" if refund else "query_order",
                        refund,
                        delay=5,
                    )
            else:
                if not isinstance(result, dict):
                    raise PlatformError(422, "invalid_outbox_result")
                if result.get("status") == "observed":
                    fact = result.get("event", {})
                    if fact.get("order_id") != order["id"]:
                        raise PlatformError(409, "outbox_event_order_mismatch")
                    if job["refund_id"] and fact.get("refund_id") != job["refund_id"]:
                        raise PlatformError(409, "outbox_event_refund_mismatch")
                    await self._apply_event(c, fact)
                elif (
                    job["kind"] == "create_checkout"
                    and result.get("status") == "pending"
                ):
                    qr = result.get("qr_code")
                    if (
                        not isinstance(qr, str)
                        or not qr.strip()
                        or len(qr.encode("utf-8")) > 2048
                        or any(ord(ch) < 32 or ord(ch) == 127 for ch in qr)
                    ):
                        raise PlatformError(422, "invalid_checkout_code")
                    expiry = aware(result.get("expires_at"))
                    if order["status"] in ("creating", "pending"):
                        await c.execute(
                            "UPDATE billing_orders SET status='pending',checkout=%s,updated_at=clock_timestamp() WHERE id=%s",
                            (
                                Jsonb(
                                    {
                                        "qr_code": result["qr_code"],
                                        "expires_at": min(
                                            expiry, order["expires_at"]
                                        ).isoformat(),
                                    }
                                ),
                                order["id"],
                            ),
                        )
                    await self._enqueue(c, order, "query_order", delay=5)
                elif (
                    job["kind"] in ("request_refund", "query_refund")
                    and result.get("status") == "pending"
                ):
                    refund = await (
                        await c.execute(
                            "SELECT * FROM billing_refunds WHERE id=%s",
                            (job["refund_id"],),
                        )
                    ).fetchone()
                    if result.get("provider_refund_id"):
                        bounded(result["provider_refund_id"])
                        await c.execute(
                            "UPDATE billing_refunds SET provider_refund_id=coalesce(provider_refund_id,%s) WHERE id=%s",
                            (result["provider_refund_id"], refund["id"]),
                        )
                    await self._enqueue(c, order, "query_refund", refund, delay=10)
                elif result.get("status") not in ("unknown", "delivered", "closed"):
                    raise PlatformError(422, "invalid_outbox_result")
                current = await (
                    await c.execute(
                        "SELECT * FROM billing_orders WHERE id=%s", (order["id"],)
                    )
                ).fetchone()
                if job["kind"] == "query_order" and current["status"] in (
                    "creating",
                    "pending",
                ):
                    retry = True
                    if current["expires_at"] <= datetime.now(timezone.utc):
                        await self._enqueue(c, current, "close_order")
                if job["kind"] == "close_order":
                    await self._enqueue(c, current, "query_order", delay=5)
                if job["kind"] == "query_refund":
                    refund = await (
                        await c.execute(
                            "SELECT status FROM billing_refunds WHERE id=%s",
                            (job["refund_id"],),
                        )
                    ).fetchone()
                    retry = refund["status"] == "pending"
            await c.execute(
                "UPDATE billing_outbox SET status=%s,due_at=clock_timestamp()+%s*interval '1 second',lease_token=NULL,lease_until=NULL,last_error=%s WHERE id=%s",
                (
                    "pending" if retry else "done",
                    retry_seconds if error else 10,
                    error,
                    job_id,
                ),
            )
            return {"id": job_id, "status": "pending" if retry else "done"}
