"""Server-owned provider facts, committed independently from financial settlement.

This module accepts no request/response body, credentials or HTTP header mapping.
All writes take the execution service's canonical wallet/invocation/job locks.
There is no public import endpoint and no client-supplied usage authority.
"""
import re

from psycopg.errors import UniqueViolation

from .errors import PlatformError
from .execution_protocol import terminal_usage
from .model_names import upstream_model_ok


def correlation_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,256}", value):
        raise PlatformError(422, "invalid_execution_evidence")
    return value


def validate_evidence_hash(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise PlatformError(422, "invalid_execution_evidence")
    return value


def same(existing, expected):
    if any(existing[key] != value for key, value in expected.items()):
        raise PlatformError(409, "idempotency_conflict")
    return dict(existing)


def mutable_attempt(job):
    if job["status"] == "waived":
        raise PlatformError(409, "execution_waived")
    if job["status"] not in ("dispatched", "pending_reconciliation"):
        raise PlatformError(409, "execution_not_dispatched")


class ExecutionEvidence:
    def __init__(self, db, lock_job):
        self.db, self.lock_job = db, lock_job

    async def start(self, job_id, attempt_id, *, provider_request_hash, upstream_model, request_id, provider_scope_hash):
        validate_evidence_hash(provider_request_hash); validate_evidence_hash(provider_scope_hash); correlation_id(request_id)
        if not upstream_model_ok(upstream_model):
            raise PlatformError(422, "invalid_execution_evidence")
        try:
            async with self.db.connection() as conn:
                job, _, _ = await self.lock_job(conn, job_id, attempt_id)
                values = dict(job_id=job_id, attempt_id=attempt_id, binding_hash=job["binding_hash"],
                              provider_scope_hash=provider_scope_hash,
                              provider_request_hash=provider_request_hash, upstream_model=upstream_model, request_id=request_id)
                old = await (await conn.execute("SELECT * FROM execution_provider_attempts WHERE job_id=%s", (job_id,))).fetchone()
                if old:
                    return same(old, values)
                if job["status"] != "reserved":
                    raise PlatformError(409, "execution_already_dispatched")
                return dict(await (await conn.execute(
                    """INSERT INTO execution_provider_attempts(job_id,attempt_id,binding_hash,provider_scope_hash,provider_request_hash,upstream_model,request_id)
                    VALUES (%(job_id)s,%(attempt_id)s,%(binding_hash)s,%(provider_scope_hash)s,%(provider_request_hash)s,%(upstream_model)s,%(request_id)s) RETURNING *""", values)).fetchone())
        except UniqueViolation:
            raise PlatformError(409, "execution_correlation_conflict") from None

    async def association(self, job_id, attempt_id, *, http_request_id=None, provider_response_id=None, http_status=None):
        if provider_response_id is not None:
            correlation_id(provider_response_id)
            if http_request_id is not None or http_status is not None:
                raise PlatformError(422, "invalid_execution_evidence")
        elif type(http_status) is not int or not 100 <= http_status <= 599:
            raise PlatformError(422, "invalid_execution_evidence")
        if http_request_id is not None:
            correlation_id(http_request_id)
        try:
            async with self.db.connection() as conn:
                job, _, _ = await self.lock_job(conn, job_id, attempt_id)
                return await self._association(conn, job, http_request_id=http_request_id,
                                               provider_response_id=provider_response_id, http_status=http_status)
        except UniqueViolation:
            raise PlatformError(409, "execution_response_reused") from None

    async def _association(self, conn, job, *, http_request_id=None, provider_response_id=None, http_status=None):
        attempt = await (await conn.execute("SELECT * FROM execution_provider_attempts WHERE job_id=%s", (job["id"],))).fetchone()
        if not attempt:
            raise PlatformError(409, "execution_attempt_evidence_required")
        values = dict(job_id=job["id"], kind="response" if provider_response_id is not None else "http",
                      attempt_id=job["attempt_id"], binding_hash=job["binding_hash"], provider_scope_hash=attempt["provider_scope_hash"], http_request_id=http_request_id,
                      provider_response_id=provider_response_id, http_status=http_status)
        old = await (await conn.execute("SELECT * FROM execution_provider_associations WHERE job_id=%s AND kind=%s", (job["id"], values["kind"]))).fetchone()
        if old:
            return same(old, values)
        mutable_attempt(job)
        if provider_response_id is not None:
            reused = await (await conn.execute("SELECT job_id FROM execution_provider_associations WHERE kind='response' AND provider_scope_hash=%s AND provider_response_id=%s", (attempt["provider_scope_hash"], provider_response_id))).fetchone()
            if reused:
                raise PlatformError(409, "execution_response_reused")
        return dict(await (await conn.execute(
            """INSERT INTO execution_provider_associations(job_id,kind,attempt_id,binding_hash,provider_scope_hash,http_request_id,provider_response_id,http_status)
            VALUES (%(job_id)s,%(kind)s,%(attempt_id)s,%(binding_hash)s,%(provider_scope_hash)s,%(http_request_id)s,%(provider_response_id)s,%(http_status)s) RETURNING *""", values)).fetchone())

    async def terminal(self, job_id, attempt_id, *, terminal, evidence_hash):
        input_tokens, output_tokens, status, response_id = terminal_usage(terminal)
        correlation_id(response_id)
        validate_evidence_hash(evidence_hash)
        try:
            async with self.db.connection() as conn:
                job, _, _ = await self.lock_job(conn, job_id, attempt_id)
                attempt = await (await conn.execute("SELECT * FROM execution_provider_attempts WHERE job_id=%s", (job_id,))).fetchone()
                if not attempt:
                    raise PlatformError(409, "execution_attempt_evidence_required")
                values = dict(job_id=job_id, attempt_id=attempt_id, binding_hash=job["binding_hash"],
                              provider_scope_hash=attempt["provider_scope_hash"],
                              provider_response_id=response_id, input_tokens=input_tokens, output_tokens=output_tokens,
                              terminal_status=status, evidence_hash=evidence_hash)
                old = await (await conn.execute("SELECT * FROM execution_terminal_evidence WHERE job_id=%s", (job_id,))).fetchone()
                if old:
                    return same(old, values)
                mutable_attempt(job)
                if not await (await conn.execute("SELECT 1 FROM execution_provider_associations WHERE job_id=%s AND kind='http' AND http_status=200", (job_id,))).fetchone():
                    raise PlatformError(409, "execution_http_evidence_required")
                await self._association(conn, job, provider_response_id=response_id)
                return dict(await (await conn.execute(
                    """INSERT INTO execution_terminal_evidence(job_id,attempt_id,binding_hash,provider_scope_hash,provider_response_id,input_tokens,output_tokens,terminal_status,evidence_hash)
                    VALUES (%(job_id)s,%(attempt_id)s,%(binding_hash)s,%(provider_scope_hash)s,%(provider_response_id)s,%(input_tokens)s,%(output_tokens)s,%(terminal_status)s,%(evidence_hash)s) RETURNING *""", values)).fetchone())
        except UniqueViolation:
            raise PlatformError(409, "execution_response_reused") from None
