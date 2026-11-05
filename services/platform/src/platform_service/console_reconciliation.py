"""Console progress metadata over fixed safe financial projections; no money writes."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import json
import re
import unicodedata
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from .console_service import page_args
from .errors import PlatformError


STATES = frozenset({'investigating', 'awaiting_evidence', 'disputed'})
REVIEW_FIELDS = frozenset({'state', 'reason', 'expected_revision', 'snapshot_hash'})
JOB_FIELDS = ('id', 'model', 'product_id', 'billing_account_id', 'status', 'amount', 'actual',
              'funding_mode', 'created_at', 'updated_at', 'rate_version', 'review_state', 'review_revision')
JOB_SQL = """SELECT j.id,j.model,j.product_id,j.billing_account_id,j.status,j.amount,j.actual,
    j.funding_mode,j.created_at,j.updated_at,j.model_definition->'rate'->>'version' AS rate_version,
    j.model_definition->'rate'->'input'->>'numerator' AS rate_input_numerator,
    j.model_definition->'rate'->'input'->>'denominator' AS rate_input_denominator,
    j.model_definition->'rate'->'output'->>'numerator' AS rate_output_numerator,
    j.model_definition->'rate'->'output'->>'denominator' AS rate_output_denominator,
    r.job_id IS NOT NULL AS usage_present,r.input_tokens,r.output_tokens,
    coalesce(c.state,'unreviewed') AS review_state,coalesce(c.revision,0) AS review_revision
    FROM execution_jobs j LEFT JOIN execution_receipts r ON r.job_id=j.id
    LEFT JOIN console_reconciliation_cases c ON c.job_id=j.id"""
OPERATION_SQL = """SELECT id,actor_id,target_id,action,target_type,request_hash,
    result->'review'->>'state' AS state,
    CASE WHEN action='execution.review' AND target_type='execution'
        THEN (result->'review'->>'revision')::bigint END AS revision,
    result->'review'->>'actor_id' AS review_actor_id,result->'review'->>'reason' AS reason,
    result->'review'->>'snapshot_hash' AS snapshot_hash,result->'review'->>'updated_at' AS updated_at
    FROM console_operations"""


def _timestamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec='microseconds')


def _job_id(value):
    try:
        if not isinstance(value, str) or len(value) > 36:
            raise ValueError()
        return str(UUID(value))
    except (TypeError, ValueError, AttributeError):
        raise PlatformError(422, 'invalid_console_reconciliation_request') from None


def _key(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,128}', value):
        raise PlatformError(422, 'invalid_console_reconciliation_request')
    return hashlib.sha256(value.encode()).hexdigest()


def _body(value):
    if (not isinstance(value, dict) or set(value) != REVIEW_FIELDS
            or not isinstance(value.get('state'), str) or value['state'] not in STATES):
        raise PlatformError(422, 'invalid_console_reconciliation_request')
    reason, revision, snapshot = value['reason'], value['expected_revision'], value['snapshot_hash']
    if (not isinstance(reason, str) or not 1 <= len(reason) <= 500 or not reason.strip() or reason != reason.strip()
            or any(unicodedata.category(c) == 'Cc' for c in reason)
            or type(revision) is not int or not 0 <= revision < 2**63 - 1
            or not isinstance(snapshot, str) or not re.fullmatch(r'[a-f0-9]{64}', snapshot)):
        raise PlatformError(422, 'invalid_console_reconciliation_request')
    return value


def _job(row, *, detail=False):
    result = {name: str(row[name]) if name in {'amount', 'actual'} and row[name] is not None
              else _timestamp(row[name]) if isinstance(row[name], datetime) else row[name]
              for name in JOB_FIELDS}
    if detail:
        result['rate'] = _rate(row)
    if row['status'] == 'waived':
        if row['actual'] is not None or row['usage_present']:
            raise PlatformError(503, 'invalid_console_reconciliation_state')
        result.update(customer_charge='0', resolution='platform_absorbed', provider_usage_known=False)
    return result


def _rate(row):
    return {'version': row['rate_version'], **{part: {key: row['rate_' + part + '_' + key]
            for key in ('numerator', 'denominator')} for part in ('input', 'output')}}


def _usage(row):
    present = row['usage_present']
    complete = row['input_tokens'] is not None and row['output_tokens'] is not None
    diagnostic = ('settled_usage_present' if complete else 'settled_usage_incomplete') if row['status'] == 'settled' and present else 'usage_without_settlement' if present else 'waived_usage_unknown' if row['status'] == 'waived' else 'central_usage_missing'
    return {'present': present, 'input_tokens': str(row['input_tokens']) if row['input_tokens'] is not None else None,
            'output_tokens': str(row['output_tokens']) if row['output_tokens'] is not None else None,
            'diagnostic': diagnostic}


def _snapshot_hash(row):
    financial = _job(row)
    for name in ('created_at', 'review_state', 'review_revision', 'rate_version'):
        financial.pop(name)
    financial['rate'] = _rate(row)
    financial['usage'] = {key: value for key, value in _usage(row).items() if key != 'diagnostic'}
    canonical = json.dumps({'version': 1, 'job': financial}, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _review(row):
    return {name: _timestamp(row[name]) if isinstance(row[name], datetime) else row[name]
            for name in ('state', 'revision', 'actor_id', 'reason', 'snapshot_hash', 'updated_at')}


def _operation(row):
    return {'operation_id': row['id'], 'job_id': row['target_id'], 'review': {
        'state': row['state'], 'revision': row['revision'], 'actor_id': row['review_actor_id'],
        'reason': row['reason'], 'snapshot_hash': row['snapshot_hash'], 'updated_at': row['updated_at']}}


class ConsoleReconciliation:
    def __init__(self, db, auth):
        self.db, self.auth = db, auth

    @asynccontextmanager
    async def _snapshot(self, principal):
        async with self.db.connection() as conn:
            await conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            await conn.execute("SET LOCAL statement_timeout='5s'")
            await conn.execute("SET LOCAL lock_timeout='1s'")
            await self.auth.validate(conn, principal, 'console:read')
            yield conn

    async def _read_job(self, conn, job_id):
        row = await (await conn.execute(JOB_SQL + ' WHERE j.id=%s', (job_id,))).fetchone()
        if row is None:
            raise PlatformError(404, 'console_reconciliation_not_found')
        return row

    async def list(self, principal, *, limit=25, offset=0, q='', financial_status='pending', review_state='all', product_id=None, funding_mode='all'):
        page_args(limit, offset, q)
        if (not isinstance(financial_status, str) or financial_status not in {'pending', 'finished', 'all'}
                or not isinstance(review_state, str) or review_state not in STATES | {'all', 'unreviewed'}
                or not isinstance(funding_mode, str) or funding_mode not in {'all', 'test', 'live'}
                or product_id is not None and (not isinstance(product_id, str) or not 1 <= len(product_id) <= 255 or any(unicodedata.category(c) == 'Cc' for c in product_id))):
            raise PlatformError(422, 'invalid_console_query')
        finished = "(j.status='waived' OR (j.status IN ('settled','released') AND c.job_id IS NOT NULL))"
        predicate = "(j.status='pending_reconciliation' OR " + finished + ")" if financial_status == 'all' else "j.status='pending_reconciliation'" if financial_status == 'pending' else finished
        statement = JOB_SQL + ' WHERE (' + predicate + ") AND (%s='all' OR coalesce(c.state,'unreviewed')=%s) AND (%s::text IS NULL OR j.product_id=%s) AND (%s='all' OR j.funding_mode=%s) AND (%s='' OR strpos(lower(j.id||' '||j.model||' '||j.billing_account_id||' '||j.product_id),lower(%s))>0) ORDER BY j.created_at DESC,j.id DESC LIMIT %s OFFSET %s"
        async with self._snapshot(principal) as conn:
            rows = await (await conn.execute(statement, (review_state, review_state, product_id, product_id, funding_mode, funding_mode, q, q, limit + 1, offset))).fetchall()
            return {'items': [_job(row) for row in rows[:limit]], 'limit': limit, 'offset': offset, 'has_more': len(rows) > limit}

    async def detail(self, principal, job_id):
        job_id = _job_id(job_id)
        async with self._snapshot(principal) as conn:
            row = await self._read_job(conn, job_id)
            review = await (await conn.execute('SELECT state,revision,actor_id,reason,snapshot_hash,updated_at FROM console_reconciliation_cases WHERE job_id=%s', (job_id,))).fetchone()
            history = await (await conn.execute("""SELECT o.id,o.actor_id,a.reason,o.created_at,
                o.result->'review'->>'state' AS state,(o.result->'review'->>'revision')::bigint AS revision,
                o.result->'review'->>'snapshot_hash' AS snapshot_hash FROM console_operations o
                JOIN console_audit_events a ON a.operation_id=o.id
                WHERE o.action='execution.review' AND o.target_type='execution' AND o.target_id=%s
                ORDER BY (o.result->'review'->>'revision')::bigint DESC,o.id DESC LIMIT 51""", (job_id,))).fetchall()
            observed = (await (await conn.execute('SELECT clock_timestamp() AS now')).fetchone())['now']
            return {'job': _job(row, detail=True), 'review': _review(review) if review else None,
                    'observed_at': _timestamp(observed), 'snapshot_hash': _snapshot_hash(row), 'usage': _usage(row),
                    'history': {'items': [{key: _timestamp(value) if isinstance(value, datetime) else value for key, value in item.items()} for item in history[:50]], 'has_more': len(history) > 50}}

    async def operation(self, principal, job_id, key):
        job_id, key_hash = _job_id(job_id), _key(key)
        async with self._snapshot(principal) as conn:
            row = await (await conn.execute(OPERATION_SQL + " WHERE actor_id=%s AND key_hash=%s AND action='execution.review' AND target_type='execution' AND target_id=%s", (principal.id, key_hash, job_id))).fetchone()
            return {'found': row is not None, 'result': _operation(row) if row else None}

    async def review(self, principal, job_id, body, key):
        job_id, value, key_hash = _job_id(job_id), _body(body), _key(key)
        canonical = json.dumps({'action': 'execution.review', 'target_type': 'execution', 'target_id': job_id, 'body': value}, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
        request_hash = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
        async with self.db.connection() as conn:
            await conn.execute("SET LOCAL statement_timeout='10s'")
            await conn.execute("SET LOCAL lock_timeout='2s'")
            actor = (await self.auth.validate(conn, principal, 'execution:review')).id
            await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('console-command:' + actor + ':' + key_hash,))
            old = await (await conn.execute(OPERATION_SQL + ' WHERE actor_id=%s AND key_hash=%s', (actor, key_hash))).fetchone()
            if old:
                if old['request_hash'] != request_hash or old['action'] != 'execution.review' or old['target_type'] != 'execution' or old['target_id'] != job_id:
                    raise PlatformError(409, 'console_idempotency_conflict')
                return _operation(old)
            # This lock covers absent cases as well. It never locks a financial row.
            await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('console-reconciliation:' + job_id,))
            existing = await (await conn.execute('SELECT revision FROM console_reconciliation_cases WHERE job_id=%s FOR UPDATE', (job_id,))).fetchone()
            revision = existing['revision'] if existing else 0
            if revision != value['expected_revision']:
                raise PlatformError(409, 'console_review_revision_conflict')
            row = await self._read_job(conn, job_id)
            if row['status'] != 'pending_reconciliation' or _snapshot_hash(row) != value['snapshot_hash']:
                raise PlatformError(409, 'console_review_snapshot_conflict')
            operation_id = uuid4().hex
            fields = (value['state'], revision + 1, actor, value['reason'], value['snapshot_hash'], operation_id)
            if existing:
                saved = await (await conn.execute("""UPDATE console_reconciliation_cases SET
                    state=%s,revision=%s,actor_id=%s,reason=%s,snapshot_hash=%s,last_operation_id=%s,updated_at=clock_timestamp()
                    WHERE job_id=%s RETURNING state,revision,actor_id,reason,snapshot_hash,updated_at""", (*fields, job_id))).fetchone()
            else:
                saved = await (await conn.execute("""INSERT INTO console_reconciliation_cases(state,revision,actor_id,reason,snapshot_hash,last_operation_id,job_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING state,revision,actor_id,reason,snapshot_hash,updated_at""", (*fields, job_id))).fetchone()
            result = {'operation_id': operation_id, 'job_id': job_id, 'review': _review(saved)}
            await conn.execute("INSERT INTO console_operations(id,actor_id,key_hash,request_hash,action,target_type,target_id,result) VALUES (%s,%s,%s,%s,'execution.review','execution',%s,%s)", (operation_id, actor, key_hash, request_hash, job_id, Jsonb(result)))
            await conn.execute("INSERT INTO console_audit_events(operation_id,actor_id,action,target_type,target_id,reason,key_hash,request_hash) VALUES (%s,%s,'execution.review','execution',%s,%s,%s,%s)", (operation_id, actor, job_id, value['reason'], key_hash, request_hash))
            return result
