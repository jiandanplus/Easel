"""Transaction boundaries for one-attempt, scoped text execution.

Provider I/O is owned by the runtime, never by this module. All financial writes
reuse CreditLedger's transaction internals under the canonical wallet lock.
"""

from dataclasses import asdict
from datetime import timedelta
from hashlib import sha256
from uuid import uuid4
import re

from psycopg.types.json import Jsonb
from ..contracts import Scope, OperatorIdentity
from ..credits import CreditLedger, authorize, active_plans, bounded, integer
from ..errors import PlatformError
from ..identity import now, opaque
from ..pricing import catalog_ready, current_rate, display_multiplier, has_trial, plan_allows, price_record, scale_text, select_price
from .rates import normalize, cost
from ..execution_protocol import model_reasoning

MAX_OPERATIONS = 32
MAX_BUDGET = 100_000_000


def scope_of(value):
    return value if isinstance(value, Scope) else Scope(**value)


def public(row):
    keys = (
        "id",
        "model",
        "status",
        "amount",
        "actual",
        "funding_mode",
        "created_at",
        "updated_at",
        "input_tokens",
        "output_tokens",
    )
    result = {
        k: (
            str(row[k])
            if k in ("amount", "actual") and row[k] is not None
            else row[k].isoformat()
            if hasattr(row[k], "isoformat")
            else row[k]
        )
        for k in keys
        if k in row
    }
    result["rate_version"] = row["model_definition"]["rate"]["version"]
    if row.get("status") == "waived":
        result.update(actual=None, input_tokens=None, output_tokens=None,
                      customer_charge="0", resolution="platform_absorbed", provider_usage_known=False)
    if row.get("status") == "pending_reconciliation" and isinstance(row.get("reason"), str):
        failure_code = {
            "upstream_timeout": "upstream_timeout",
            "upstream_unavailable": "upstream_unavailable",
            "upstream_uncertain": "upstream_unavailable",
            "lease_expired": "upstream_unavailable",
            "upstream_error": "upstream_error",
            "invalid_upstream_response": "upstream_error",
            "upstream_response_too_large": "upstream_error",
            "upstream_event_too_large": "upstream_error",
            "invalid_upstream_usage": "invalid_upstream_usage",
            "usage_exceeds_reservation": "invalid_upstream_usage",
            "cancelled_after_dispatch": "request_cancelled",
        }.get(row["reason"])
        if failure_code:
            result["failure_code"] = failure_code
    return result


def operator_id(operator):
    if (
        not isinstance(operator, OperatorIdentity)
        or "execution:publish" not in operator.capabilities
    ):
        raise PlatformError(403, "operator_capability_required")
    return operator.id


def validate_definition(value):
    required = {
        "id",
        "name",
        "version",
        "capability",
        "feature",
        "products",
        "test_only",
        "provider_id",
        "provider_version",
        "verification",
        "verification_ref",
        "rate",
        "limits",
    }
    if (not isinstance(value, dict) or not required.issubset(value)
            or set(value) - required - {"reasoning"}):
        raise PlatformError(422, "invalid_model_definition")
    d = dict(value)
    for k in required - {"products", "test_only", "rate", "limits"}:
        bounded(d[k])
    if (
        d["capability"] != "text"
        or type(d["test_only"]) is not bool
        or d["verification"] != ("fixture" if d["test_only"] else "live")
    ):
        raise PlatformError(422, "invalid_model_definition")
    if (
        not isinstance(d["products"], list)
        or not d["products"]
        or len(d["products"]) > 64
    ):
        raise PlatformError(422, "invalid_model_definition")
    for p in d["products"]:
        bounded(p)
    d["rate"] = normalize(d["rate"])
    if "reasoning" in d:
        d["reasoning"] = model_reasoning(d["reasoning"])
    limits = d["limits"]
    maximum = dict(
        max_input_bytes=1048576, max_input_tokens=65536, max_output_tokens=4096
    )
    if not isinstance(limits, dict) or set(limits) != set(maximum):
        raise PlatformError(422, "invalid_model_limits")
    for key, cap in maximum.items():
        if integer(limits[key], positive=True) > cap:
            raise PlatformError(422, "invalid_model_limits")
    cost(d["rate"], limits["max_input_tokens"], limits["max_output_tokens"])
    return d


class ExecutionService:
    def __init__(self, db, identity):
        self.db, self.identity, self.ledger = db, identity, CreditLedger(db)
        from ..execution_evidence import ExecutionEvidence
        self.evidence = ExecutionEvidence(db, self._job)

    async def record_provider_attempt(self, job_id, attempt_id, **values):
        return await self.evidence.start(job_id, attempt_id, **values)

    async def record_provider_association(self, job_id, attempt_id, **values):
        return await self.evidence.association(job_id, attempt_id, **values)

    async def record_terminal_evidence(self, job_id, attempt_id, **values):
        return await self.evidence.terminal(job_id, attempt_id, **values)

    async def publish(self, definition, operator_id):
        op = globals()["operator_id"](operator_id)
        d = validate_definition(definition)
        async with self.db.connection() as c:
            await c.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                ("execution-model:" + d["id"],),
            )
            old = await (
                await c.execute(
                    "SELECT definition FROM execution_models WHERE id=%s", (d["id"],)
                )
            ).fetchone()
            if old:
                if old["definition"] != d:
                    raise PlatformError(409, "model_definition_immutable")
                return d
            await c.execute(
                "INSERT INTO execution_models(id,definition,operator_id) VALUES (%s,%s,%s)",
                (d["id"], Jsonb(d), op),
            )
            await c.execute(
                "INSERT INTO execution_model_states(model_id,operator_id) VALUES (%s,%s)",
                (d["id"], op),
            )
        return d

    async def set_enabled(self, model_id, enabled, operator_id):
        op = globals()["operator_id"](operator_id)
        bounded(model_id)
        if type(enabled) is not bool:
            raise PlatformError(422, "invalid_enabled")
        async with self.db.connection() as c:
            row = await (
                await c.execute(
                    "UPDATE execution_model_states SET enabled=%s,operator_id=%s,updated_at=clock_timestamp() WHERE model_id=%s RETURNING model_id,enabled",
                    (enabled, op, model_id),
                )
            ).fetchone()
            if not row:
                raise PlatformError(404, "model_not_found")
            return row

    async def _definition(self, c, model, mode, bindings, product, wallet=None):
        row = await (
            await c.execute(
                "SELECT m.definition,s.enabled FROM execution_models m JOIN execution_model_states s ON s.model_id=m.id WHERE m.id=%s",
                (model,),
            )
        ).fetchone()
        if mode not in ("test", "live") or not row:
            raise PlatformError(503, "execution_unavailable")
        d = row["definition"]
        b = bindings.get(model)
        if (
            not (row["enabled"] or await has_trial(c, "execution", model, wallet))
            or d["test_only"] != (mode == "test")
            or product not in d["products"]
            or not b
            or (b.get("provider_id"), b.get("provider_version"))
            != (d["provider_id"], d["provider_version"])
        ):
            raise PlatformError(503, "execution_unavailable")
        bounded(b.get("binding_hash"))
        blocked = await (
            await c.execute(
                "SELECT 1 FROM execution_blocked_bindings WHERE binding_hash=%s",
                (b["binding_hash"],),
            )
        ).fetchone()
        if blocked:
            raise PlatformError(503, "execution_binding_blocked")
        return d, b

    async def _plans(self, c, scope, mode, definition):
        plans = [
            p
            for p in await active_plans(c, scope, mode)
            if definition["feature"] in p["definition"]["features"]
            and plan_allows(p["definition"], definition["id"])
        ]
        if not plans:
            raise PlatformError(403, "execution_entitlement_required")
        return plans

    async def _entitlement(self, c, scope, mode, definition):
        return max(p["expires_at"] for p in await self._plans(c, scope, mode, definition))

    async def readiness(self, product_id, *, mode, bindings):
        """Whether an enabled, verified definition matches this configured binding."""
        async with self.db.connection() as c:
            rows = await (
                await c.execute("SELECT id FROM execution_models ORDER BY id")
            ).fetchall()
            for row in rows:
                try:
                    await self._definition(c, row["id"], mode, bindings, product_id)
                    return True
                except PlatformError:
                    continue
        return False

    async def catalog(self, scope, *, mode, bindings):
        scope = scope_of(scope)
        async with self.db.connection() as c:
            await self.identity.validate_scope(c, scope)
            await authorize(c, scope)
            rows = await (
                await c.execute("SELECT id FROM execution_models ORDER BY id")
            ).fetchall()
            result = []
            for row in rows:
                try:
                    d, _ = await self._definition(
                        c, row["id"], mode, bindings, scope.product_id, scope.billing_account_id
                    )
                    plans = await self._plans(c, scope, mode, d)
                except PlatformError:
                    continue
                d = {**d, "rate": scale_text(
                    await current_rate(c, "execution", d["id"], d["rate"]),
                    await display_multiplier(c, scope, mode, plans, d["id"]))}
                result.append(
                    {
                        k: d[k]
                        for k in (
                            "id",
                            "name",
                            "version",
                            "capability",
                            "rate",
                            "limits",
                            "test_only",
                        )
                    }
                    | {"readiness": "test" if mode == "test" else "live"}
                    | ({"reasoning": model_reasoning(d["reasoning"])} if "reasoning" in d else {})
                )
            return {"models": result}

    async def mint(self, scope, *, model, invocation_key, mode, bindings):
        scope = scope_of(scope)
        bounded(model)
        bounded(invocation_key)
        async with self.db.connection() as c:
            session = await self.identity.validate_scope(c, scope)
            await authorize(c, scope)
            old = await (
                await c.execute(
                    "SELECT * FROM execution_invocations WHERE session_id=%s AND invocation_key=%s FOR UPDATE",
                    (scope.session_id, invocation_key),
                )
            ).fetchone()
            if old and (
                old["model"] != model
                or old["funding_mode"] != mode
                or old["scope"] != asdict(scope)
            ):
                raise PlatformError(409, "idempotency_conflict")
            d, _ = await self._definition(c, model, mode, bindings, scope.product_id, scope.billing_account_id)
            member_expiry = await self._entitlement(c, scope, mode, d)
            expiry = min(
                session["expires_at"], member_expiry, now() + timedelta(seconds=1800)
            )
            if old:
                if old["expires_at"] <= now():
                    raise PlatformError(410, "invocation_expired")
                invocation, expiry = old["id"], min(expiry, old["expires_at"])
            else:
                invocation = str(uuid4())
                await c.execute(
                    "INSERT INTO execution_invocations(id,scope,session_id,user_id,product_id,billing_account_id,invocation_key,model,funding_mode,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        invocation,
                        Jsonb(asdict(scope)),
                        scope.session_id,
                        scope.user_id,
                        scope.product_id,
                        scope.billing_account_id,
                        invocation_key,
                        model,
                        mode,
                        expiry,
                    ),
                )
            token = opaque()
            token_expiry = min(expiry, now() + timedelta(seconds=300))
            await c.execute(
                "INSERT INTO execution_task_tokens(token_hash,invocation_id,expires_at) VALUES (%s,%s,%s)",
                (sha256(token.encode()).hexdigest(), invocation, token_expiry),
            )
        return dict(
            task_token=token,
            expires_at=token_expiry.isoformat(),
            invocation_id=invocation,
            model=model,
            funding_mode=mode,
            scope=asdict(scope),
            base_url_path="/v2",
        )

    async def authenticate_task(self, token):
        bounded(token)
        async with self.db.connection() as c:
            row = await (
                await c.execute(
                    "SELECT i.*,t.expires_at token_expires,m.definition AS immutable_model FROM execution_task_tokens t JOIN execution_invocations i ON i.id=t.invocation_id JOIN execution_models m ON m.id=i.model WHERE t.token_hash=%s",
                    (sha256(token.encode()).hexdigest(),),
                )
            ).fetchone()
            if not row or min(row["expires_at"], row["token_expires"]) <= now():
                raise PlatformError(401, "task_token_invalid")
            scope = scope_of(row["scope"])
            await self.identity.validate_scope(c, scope)
            return dict(
                scope=scope,
                invocation_id=row["id"],
                model=row["model"],
                funding_mode=row["funding_mode"],
                model_limits=row["immutable_model"]["limits"],
                **({"model_reasoning": model_reasoning(row["immutable_model"]["reasoning"])}
                   if "reasoning" in row["immutable_model"] else {}),
            )

    async def _invocation(self, c, context):
        scope = scope_of(context["scope"])
        row = await (
            await c.execute(
                "SELECT * FROM execution_invocations WHERE id=%s FOR UPDATE",
                (context["invocation_id"],),
            )
        ).fetchone()
        if (
            not row
            or row["scope"] != asdict(scope)
            or row["model"] != context["model"]
            or row["funding_mode"] != context["funding_mode"]
        ):
            raise PlatformError(403, "execution_scope_mismatch")
        return row

    async def admit(self, context, *, key, body_hash, output_limit, bindings):
        bounded(key)
        bounded(body_hash)
        integer(output_limit, positive=True)
        scope = scope_of(context["scope"])
        async with self.db.connection() as c:
            await self.identity.validate_scope(c, scope)
            await authorize(c, scope)
            invocation = await self._invocation(c, context)
            old = await (
                await c.execute(
                    "SELECT * FROM execution_jobs WHERE invocation_id=%s AND operation_key=%s",
                    (invocation["id"], key),
                )
            ).fetchone()
            if old:
                if old["body_hash"] != body_hash or old["output_limit"] != output_limit:
                    raise PlatformError(409, "idempotency_conflict")
                return {**old, "replay": True}
            if invocation["expires_at"] <= now():
                raise PlatformError(410, "invocation_expired")
            d, b = await self._definition(
                c,
                invocation["model"],
                invocation["funding_mode"],
                bindings,
                scope.product_id,
                scope.billing_account_id,
            )
            plans = await self._plans(c, scope, invocation["funding_mode"], d)
            if output_limit > d["limits"]["max_output_tokens"]:
                raise PlatformError(422, "output_limit_exceeded")
            base = await current_rate(c, "execution", d["id"], d["rate"])
            factor, amount, memberships = await select_price(
                c, scope, invocation["funding_mode"], plans, d["id"],
                lambda value: cost(scale_text(base, value), d["limits"]["max_input_tokens"], output_limit))
            if (
                invocation["operation_count"] >= MAX_OPERATIONS
                or invocation["spent"] + invocation["held"] + amount > MAX_BUDGET
            ):
                raise PlatformError(409, "invocation_budget_exceeded")
            job, attempt = str(uuid4()), str(uuid4())
            reservation = await self.ledger.reserve_in_transaction(
                c,
                scope,
                operation="execution",
                key=job,
                body_hash=body_hash,
                amount=amount,
                rate_version=d["rate"]["version"],
                funding_mode=invocation["funding_mode"],
                authorize_scope=False,
                membership_ids=memberships,
            )
            insert = "INSERT INTO execution_jobs(price,id,invocation_id,operation_key,body_hash,output_limit,scope,billing_account_id,user_id,product_id,model,funding_mode,model_definition,binding_hash,attempt_id,reservation_id,status,amount,lease_until) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'reserved',%s,clock_timestamp()+interval '60 seconds') RETURNING *"
            values = (
                        Jsonb(price_record(scale_text(base, factor), factor)),
                        job,
                        invocation["id"],
                        key,
                        body_hash,
                        output_limit,
                        Jsonb(asdict(scope)),
                        scope.billing_account_id,
                        scope.user_id,
                        scope.product_id,
                        d["id"],
                        invocation["funding_mode"],
                        Jsonb(d),
                        b["binding_hash"],
                        attempt,
                        reservation["id"],
                        amount,
                    )
            if not await catalog_ready(c):
                insert = insert.replace("execution_jobs(price,", "execution_jobs(", 1).replace("VALUES (%s,", "VALUES (", 1)
                values = values[1:]
            row = await (await c.execute(insert, values)).fetchone()
            await c.execute(
                "UPDATE execution_invocations SET operation_count=operation_count+1,held=held+%s WHERE id=%s",
                (amount, invocation["id"]),
            )
            return {**row, "replay": False}

    async def _job(
        self, c, job_id, attempt_id=None, *, live=False, expected_scope=None
    ):
        bounded(job_id)
        row = await (
            await c.execute("SELECT * FROM execution_jobs WHERE id=%s", (job_id,))
        ).fetchone()
        if not row:
            raise PlatformError(404, "execution_not_found")
        scope = scope_of(row["scope"])
        if expected_scope is not None and any(
            getattr(scope, k) != getattr(expected_scope, k)
            for k in ("user_id", "product_id", "tenant_id", "billing_account_id")
        ):
            raise PlatformError(404, "execution_not_found")
        if live:
            await self.identity.validate_scope(c, scope)
        await c.execute(
            "SELECT id FROM billing_accounts WHERE id=%s FOR UPDATE",
            (scope.billing_account_id,),
        )
        await c.execute(
            "SELECT id FROM execution_invocations WHERE id=%s FOR UPDATE",
            (row["invocation_id"],),
        )
        row = await (
            await c.execute(
                "SELECT * FROM execution_jobs WHERE id=%s FOR UPDATE", (job_id,)
            )
        ).fetchone()
        if attempt_id is not None and row["attempt_id"] != attempt_id:
            raise PlatformError(409, "execution_attempt_mismatch")
        reservation = await (
            await c.execute(
                "SELECT * FROM credit_reservations WHERE id=%s FOR UPDATE",
                (row["reservation_id"],),
            )
        ).fetchone()
        return row, scope, reservation

    async def dispatch(self, job_id, attempt_id):
        async with self.db.connection() as c:
            row, scope, _ = await self._job(c, job_id, attempt_id, live=True)
            if row["status"] != "reserved" or row["dispatched_at"] is not None:
                raise PlatformError(409, "execution_already_dispatched")
            if row["lease_until"] <= now():
                raise PlatformError(409, "execution_lease_expired")
            await authorize(c, scope)
            invocation = await (
                await c.execute(
                    "SELECT expires_at FROM execution_invocations WHERE id=%s",
                    (row["invocation_id"],),
                )
            ).fetchone()
            if invocation["expires_at"] <= now():
                raise PlatformError(410, "invocation_expired")
            await self._entitlement(c, scope, row["funding_mode"], row["model_definition"])
            blocked = await (
                await c.execute(
                    "SELECT 1 FROM execution_blocked_bindings WHERE binding_hash=%s",
                    (row["binding_hash"],),
                )
            ).fetchone()
            if blocked:
                raise PlatformError(503, "execution_binding_blocked")
            await c.execute(
                "UPDATE credit_reservations SET status='dispatched',updated_at=clock_timestamp() WHERE id=%s",
                (row["reservation_id"],),
            )
            return await (
                await c.execute(
                    "UPDATE execution_jobs SET status='dispatched',dispatched_at=clock_timestamp(),lease_until=clock_timestamp()+interval '60 seconds',updated_at=clock_timestamp() WHERE id=%s RETURNING *",
                    (job_id,),
                )
            ).fetchone()

    async def heartbeat(self, job_id, attempt_id):
        async with self.db.connection() as c:
            row, _, _ = await self._job(c, job_id, attempt_id)
            if row["status"] != "dispatched":
                return
            await c.execute(
                "UPDATE execution_jobs SET lease_until=clock_timestamp()+interval '60 seconds' WHERE id=%s",
                (job_id,),
            )

    async def _pending(self, c, row, reason):
        # Reason is an internal code, never arbitrary provider text.
        reason = (
            reason
            if isinstance(reason, str)
            and re.fullmatch(r"[a-z][a-z0-9_]{0,127}", reason)
            else "unknown_cost"
        )
        if row["status"] in ("dispatched", "pending_reconciliation"):
            await c.execute(
                "UPDATE credit_reservations SET status='pending_reconciliation',updated_at=clock_timestamp() WHERE id=%s",
                (row["reservation_id"],),
            )
            await c.execute(
                "UPDATE execution_jobs SET status='pending_reconciliation',reason=%s,updated_at=clock_timestamp() WHERE id=%s",
                (reason, row["id"]),
            )
            row = {**row, "status": "pending_reconciliation", "reason": reason}
        elif row["status"] != "settled":
            raise PlatformError(409, "execution_not_dispatched")
        return public(row)

    async def pending(self, job_id, attempt_id, reason):
        async with self.db.connection() as c:
            row, _, _ = await self._job(c, job_id, attempt_id)
            return await self._pending(c, row, reason)

    async def finish(
        self,
        job_id,
        attempt_id,
        *,
        input_tokens,
        output_tokens,
        terminal_status,
        provider_response_id,
        evidence_hash,
        payload,
        content_type,
    ):
        integer(input_tokens)
        integer(output_tokens)
        bounded(provider_response_id)
        bounded(evidence_hash)
        if (
            terminal_status not in ("completed", "incomplete")
            or content_type not in ("application/json", "text/event-stream")
            or not isinstance(payload, bytes)
            or len(payload)
            > (2097152 if content_type == "application/json" else 8388608)
        ):
            raise PlatformError(422, "invalid_execution_receipt")
        error = None
        async with self.db.connection() as c:
            row, scope, reservation = await self._job(c, job_id, attempt_id)
            receipt = dict(
                attempt_id=attempt_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                terminal_status=terminal_status,
                provider_response_id=provider_response_id,
                evidence_hash=evidence_hash,
            )
            evidence = await (await c.execute("SELECT * FROM execution_terminal_evidence WHERE job_id=%s", (job_id,))).fetchone()
            if evidence and (evidence["binding_hash"] != row["binding_hash"]
                             or any(evidence[k] != v for k, v in receipt.items())
                             or sha256(payload).hexdigest() != evidence["evidence_hash"]):
                raise PlatformError(409, "execution_evidence_mismatch")
            row, error = await self._finish_locked(c, row, scope, reservation, receipt,
                                                   payload=payload, content_type=content_type,
                                                   allow_result_backfill=evidence is not None)
        if error:
            raise error
        return public(row)

    async def _finish_locked(self, c, row, scope, reservation, receipt, *, payload=None, content_type=None, allow_result_backfill=False):
        """Settle a trusted receipt; absent payload means no recoverable reply."""
        if row["status"] == "waived":
            raise PlatformError(409, "execution_waived")
        old = await (await c.execute("SELECT * FROM execution_receipts WHERE job_id=%s", (row["id"],))).fetchone()
        if old:
            if any(old[k] != v for k, v in receipt.items()):
                raise PlatformError(409, "idempotency_conflict")
            if allow_result_backfill and payload is not None:
                # A recovery worker may have committed the financial receipt
                # while the original runtime still holds the real response.
                # Never replace or revive an existing/expired cache entry.
                await c.execute("INSERT INTO execution_results(job_id,payload,content_type) VALUES (%s,%s,%s) ON CONFLICT (job_id) DO NOTHING",
                                (row["id"], payload, content_type))
            return row, None
        if row["status"] not in ("dispatched", "pending_reconciliation"):
            raise PlatformError(409, "execution_not_dispatched")
        input_tokens, output_tokens = receipt["input_tokens"], receipt["output_tokens"]
        try:
            actual = cost((row.get("price") or {}).get("rate") or row["model_definition"]["rate"], input_tokens, output_tokens)
        except PlatformError:
            actual = row["amount"] + 1
        if (actual > row["amount"]
                or input_tokens > row["model_definition"]["limits"]["max_input_tokens"]
                or output_tokens > row["output_limit"]):
            await self._pending(c, row, "usage_exceeds_reservation")
            await c.execute("INSERT INTO execution_blocked_bindings(binding_hash,reason) VALUES (%s,'usage_exceeds_reservation') ON CONFLICT DO NOTHING", (row["binding_hash"],))
            return row, PlatformError(409, "settlement_exceeds_reservation")
        await self.ledger._complete(c, scope, reservation, actual, row["attempt_id"], "settled")
        await c.execute(
            "INSERT INTO execution_receipts(job_id,attempt_id,input_tokens,output_tokens,terminal_status,provider_response_id,evidence_hash,actual) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (row["id"], row["attempt_id"], input_tokens, output_tokens, receipt["terminal_status"],
             receipt["provider_response_id"], receipt["evidence_hash"], actual))
        if payload is not None:
            await c.execute("INSERT INTO execution_results(job_id,payload,content_type) VALUES (%s,%s,%s)",
                            (row["id"], payload, content_type))
        await c.execute("UPDATE execution_invocations SET held=held-%s,spent=spent+%s WHERE id=%s",
                        (row["amount"], actual, row["invocation_id"]))
        row = await (await c.execute("UPDATE execution_jobs SET status='settled',actual=%s,updated_at=clock_timestamp() WHERE id=%s RETURNING *", (actual, row["id"]))).fetchone()
        return row, None

    async def finish_from_evidence(self, job_id, attempt_id):
        """Financial-only recovery from committed server evidence. Never I/O."""
        error = None
        async with self.db.connection() as c:
            row, scope, reservation = await self._job(c, job_id, attempt_id)
            if row["status"] == "waived":
                raise PlatformError(409, "execution_waived")
            evidence = await (await c.execute("SELECT * FROM execution_terminal_evidence WHERE job_id=%s", (job_id,))).fetchone()
            if not evidence:
                raise PlatformError(409, "execution_evidence_not_found")
            if evidence["attempt_id"] != attempt_id or evidence["binding_hash"] != row["binding_hash"]:
                raise PlatformError(409, "execution_evidence_mismatch")
            receipt = {k: evidence[k] for k in ("attempt_id", "input_tokens", "output_tokens", "terminal_status", "provider_response_id", "evidence_hash")}
            row, error = await self._finish_locked(c, row, scope, reservation, receipt)
        if error:
            raise error
        return public(row)

    async def recover_evidence(self, *, limit=100):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise PlatformError(422, "invalid_recovery_limit")
        async with self.db.connection() as c:
            candidates = await (await c.execute("""SELECT j.id,j.attempt_id FROM execution_jobs j
                JOIN execution_terminal_evidence e ON e.job_id=j.id AND e.attempt_id=j.attempt_id AND e.binding_hash=j.binding_hash
                WHERE (j.status='pending_reconciliation' OR (j.status='dispatched' AND j.lease_until<=clock_timestamp()))
                AND coalesce(j.reason,'') NOT IN ('usage_exceeds_reservation','settlement_exceeds_reservation')
                ORDER BY e.created_at,j.id LIMIT %s""", (limit,))).fetchall()
        recovered = 0
        for candidate in candidates:
            try:
                result = await self.finish_from_evidence(candidate["id"], candidate["attempt_id"])
                recovered += result["status"] == "settled"
            except PlatformError as error:
                if error.code not in {"settlement_exceeds_reservation", "execution_waived", "execution_evidence_mismatch"}:
                    raise
        return recovered

    async def _release(self, c, row, scope, reservation):
        if row["status"] != "reserved":
            raise PlatformError(409, "unknown_cost_cannot_release")
        await self.ledger._complete(
            c, scope, reservation, 0, row["attempt_id"], "released"
        )
        await c.execute(
            "UPDATE execution_invocations SET held=held-%s WHERE id=%s",
            (row["amount"], row["invocation_id"]),
        )
        return await (
            await c.execute(
                "UPDATE execution_jobs SET status='released',actual=0,updated_at=clock_timestamp() WHERE id=%s RETURNING *",
                (row["id"],),
            )
        ).fetchone()

    async def cancel(self, scope, job_id):
        scope = scope_of(scope)
        async with self.db.connection() as c:
            await self.identity.validate_scope(c, scope)
            row, frozen, reservation = await self._job(c, job_id, expected_scope=scope)
            if row["status"] == "reserved":
                return public(await self._release(c, row, frozen, reservation))
            if row["status"] in ("released", "settled", "waived"):
                return public(row)
            return await self._pending(c, row, "cancelled_after_dispatch")

    async def recover(self):
        async with self.db.connection() as c:
            rows = await (
                await c.execute(
                    "SELECT id FROM execution_jobs WHERE status IN ('reserved','dispatched') AND lease_until<=clock_timestamp() ORDER BY lease_until LIMIT 200"
                )
            ).fetchall()
        counts = {"released": 0, "pending": 0}
        for candidate in rows:
            async with self.db.connection() as c:
                row, scope, reservation = await self._job(c, candidate["id"])
                if row["lease_until"] > now():
                    continue
                if row["status"] == "reserved":
                    await self._release(c, row, scope, reservation)
                    counts["released"] += 1
                elif row["status"] == "dispatched":
                    await self._pending(c, row, "lease_expired")
                    counts["pending"] += 1
        recovered = await self.recover_evidence()
        if recovered:
            counts["evidence_settled"] = recovered
        return counts

    async def list(self, scope):
        scope = scope_of(scope)
        async with self.db.connection() as c:
            await self.identity.validate_scope(c, scope)
            await authorize(c, scope)
            rows = await (
                await c.execute(
                    "SELECT j.*,r.input_tokens,r.output_tokens FROM execution_jobs j LEFT JOIN execution_receipts r ON r.job_id=j.id WHERE j.user_id=%s AND j.product_id=%s AND j.billing_account_id=%s ORDER BY j.created_at DESC,j.id DESC LIMIT 200",
                    (scope.user_id, scope.product_id, scope.billing_account_id),
                )
            ).fetchall()
            return [public(r) for r in rows]

    async def get(self, scope, job_id):
        scope = scope_of(scope)
        async with self.db.connection() as c:
            await self.identity.validate_scope(c, scope)
            await authorize(c, scope)
            row = await (
                await c.execute(
                    "SELECT j.*,r.input_tokens,r.output_tokens FROM execution_jobs j LEFT JOIN execution_receipts r ON r.job_id=j.id WHERE j.id=%s AND j.user_id=%s AND j.product_id=%s AND j.billing_account_id=%s",
                    (job_id, scope.user_id, scope.product_id, scope.billing_account_id),
                )
            ).fetchone()
            if not row:
                raise PlatformError(404, "execution_not_found")
            return public(row)

    async def lookup_operation(self, scope, invocation_id, operation_key):
        """Read historical financial metadata using current business ownership.

        The original session/epoch remains frozen for task authorization; a new
        valid session may only inspect this safe history projection.
        """
        if (not isinstance(invocation_id, str)
                or not re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", invocation_id)
                or not isinstance(operation_key, str)
                or not re.fullmatch(r"[\x21-\x7e]{1,128}", operation_key)):
            raise PlatformError(422, "invalid_identifier")
        scope = scope_of(scope)
        async with self.db.connection() as c:
            await self.identity.validate_scope(c, scope)
            await authorize(c, scope)
            row = await (
                await c.execute(
                    """SELECT j.*,r.input_tokens,r.output_tokens,
                    CASE WHEN j.status<>'settled' OR cache.job_id IS NULL THEN 'not_available'
                         WHEN cache.expires_at<=clock_timestamp() THEN 'expired'
                         WHEN cache.payload IS NOT NULL THEN 'available'
                         ELSE 'not_available' END AS result_state
                    FROM execution_jobs j
                    LEFT JOIN execution_receipts r ON r.job_id=j.id
                    LEFT JOIN execution_results cache ON cache.job_id=j.id
                    WHERE j.invocation_id=%s AND j.operation_key=%s
                    AND j.user_id=%s AND j.product_id=%s AND j.billing_account_id=%s
                    AND j.scope->>'tenant_id'=%s""",
                    (invocation_id, operation_key, scope.user_id, scope.product_id,
                     scope.billing_account_id, scope.tenant_id),
                )
            ).fetchone()
            if not row:
                raise PlatformError(404, "execution_not_found")
            return {**public(row), "result_state": row["result_state"]}

    async def result(self, context, key):
        bounded(key)
        scope = scope_of(context["scope"])
        async with self.db.connection() as c:
            await self.identity.validate_scope(c, scope)
            await authorize(c, scope)
            invocation = await self._invocation(c, context)
            row = await (
                await c.execute(
                    "SELECT j.*,r.payload,r.content_type,r.expires_at result_expires FROM execution_jobs j LEFT JOIN execution_results r ON r.job_id=j.id WHERE j.invocation_id=%s AND j.operation_key=%s",
                    (invocation["id"], key),
                )
            ).fetchone()
            if not row:
                raise PlatformError(404, "execution_not_found")
            if row["status"] == "pending_reconciliation":
                raise PlatformError(409, "pending_reconciliation")
            if row["status"] == "waived":
                raise PlatformError(409, "execution_waived")
            if row["status"] in ("reserved", "dispatched"):
                raise PlatformError(409, "execution_running", True)
            if (
                row["status"] != "settled"
                or row["payload"] is None
                or row["result_expires"] <= now()
            ):
                raise PlatformError(410, "result_expired")
            return {
                **public(row),
                "payload": bytes(row["payload"]),
                "content_type": row["content_type"],
            }

    async def expire_results(self):
        """Explicit retention only: clear expired bytes, retain financial history."""
        async with self.db.connection() as c:
            cursor = await c.execute(
                "UPDATE execution_results SET payload=NULL WHERE expires_at<=clock_timestamp() AND payload IS NOT NULL"
            )
            return cursor.rowcount
