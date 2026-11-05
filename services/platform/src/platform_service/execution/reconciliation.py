"""Server/operator-only, audited decisions to absorb unknown provider costs.

This module does not infer usage, contact providers, retry a model, recover a
reply, or convert unknown supplier charges to zero. Every write-off requires an
explicit operator capability and an exactly reviewed plan.
"""
from datetime import datetime, timezone
import hashlib
import json
import re
import unicodedata
from uuid import uuid4

from psycopg.types.json import Jsonb

from ..contracts import OperatorIdentity
from ..credits import bounded
from ..errors import PlatformError
from ..identity import now

CAPABILITY = 'execution:writeoff'
MAX_BATCH = 32
PLAN_FIELDS = frozenset({'version','kind','actor','reason','approval_reference','items','plan_sha256'})
ITEM_FIELDS = frozenset({'id','attempt_id','binding_hash','scope_hash','reservation_id','amount','snapshot_hash'})
HEX = re.compile(r'^[a-f0-9]{64}$')


def _json(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    raise TypeError('unsupported reconciliation projection')


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False, default=_json).encode()


def plan_digest(value):
    return hashlib.sha256(_canonical({key: item for key, item in value.items()
                                      if key != 'plan_sha256'})).hexdigest()


def _operator(value):
    if not isinstance(value, OperatorIdentity) or CAPABILITY not in value.capabilities:
        raise PlatformError(403, 'operator_capability_required')
    return value.id


def _reason(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 500 or value != value.strip()
            or any(unicodedata.category(c).startswith('C') for c in value)):
        raise PlatformError(422, 'invalid_reconciliation_reason')
    return value


def _key(value):
    if not isinstance(value, str) or re.fullmatch(r'[\x21-\x7e]{1,128}', value) is None:
        raise PlatformError(422, 'invalid_reconciliation_key')
    return value


def _ids(values):
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_BATCH:
        raise PlatformError(422, 'invalid_reconciliation_batch')
    for value in values:
        bounded(value)
    if len(set(values)) != len(values):
        raise PlatformError(422, 'invalid_reconciliation_batch')
    return sorted(values)


class ExecutionReconciliation:
    def __init__(self, service):
        self.service = service
        self.db = service.db

    async def _item(self, conn, job_id, *, locked=None):
        if locked is None:
            row = await (await conn.execute('SELECT * FROM execution_jobs WHERE id=%s', (job_id,))).fetchone()
            if row is None:
                raise PlatformError(404, 'execution_not_found')
            reservation = await (await conn.execute('SELECT * FROM credit_reservations WHERE id=%s',
                                                     (row['reservation_id'],))).fetchone()
        else:
            row, _, reservation = locked
        if (reservation is None or row['status'] != 'pending_reconciliation'
                or reservation['status'] != 'pending_reconciliation'
                or row['actual'] is not None or reservation['actual'] is not None
                or reservation['completion_key'] is not None or row['dispatched_at'] is None
                or row['lease_until'] > now() or row['amount'] != reservation['amount']):
            raise PlatformError(409, 'execution_writeoff_ineligible')
        receipt = await (await conn.execute('SELECT 1 FROM execution_receipts WHERE job_id=%s', (job_id,))).fetchone()
        if receipt:
            raise PlatformError(409, 'execution_receipt_present')
        available = (await (await conn.execute("SELECT to_regclass('execution_terminal_evidence') IS NOT NULL AS present")).fetchone())['present']
        if available and await (await conn.execute('SELECT 1 FROM execution_terminal_evidence WHERE job_id=%s', (job_id,))).fetchone():
            raise PlatformError(409, 'execution_evidence_available')
        scope_hash = (await (await conn.execute("SELECT encode(sha256(convert_to(scope::text,'UTF8')),'hex') value FROM execution_jobs WHERE id=%s",
                                               (job_id,))).fetchone())['value']
        snapshot = {'job': dict(row), 'reservation': dict(reservation), 'scope_hash': scope_hash,
                    'receipt_present': False, 'terminal_evidence_present': False}
        return {'id': row['id'], 'attempt_id': row['attempt_id'], 'binding_hash': row['binding_hash'],
                'scope_hash': scope_hash, 'reservation_id': row['reservation_id'], 'amount': str(row['amount']),
                'snapshot_hash': hashlib.sha256(_canonical(snapshot)).hexdigest()}

    async def plan_waivers(self, operator, job_ids, reason, approval_reference):
        actor = _operator(operator)
        ids = _ids(job_ids)
        reason, approval_reference = _reason(reason), _reason(approval_reference)
        async with self.db.connection() as conn:
            await conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            items = [await self._item(conn, job_id) for job_id in ids]
        value = {'version': 1, 'kind': 'platform_absorbed', 'actor': actor, 'reason': reason,
                 'approval_reference': approval_reference, 'items': items}
        return {**value, 'plan_sha256': plan_digest(value)}

    def _validate_plan(self, operator, value, reviewed_hash):
        actor = _operator(operator)
        if (not isinstance(value, dict) or set(value) != PLAN_FIELDS
                or type(value['version']) is not int or value['version'] != 1
                or value['kind'] != 'platform_absorbed' or value['actor'] != actor):
            raise PlatformError(422, 'invalid_reconciliation_plan')
        _reason(value['reason']);_reason(value['approval_reference'])
        if not isinstance(value['items'], list) or not 1 <= len(value['items']) <= MAX_BATCH:
            raise PlatformError(422, 'invalid_reconciliation_plan')
        for item in value['items']:
            if not isinstance(item, dict) or set(item) != ITEM_FIELDS:
                raise PlatformError(422, 'invalid_reconciliation_plan')
            for field in ['id','attempt_id','binding_hash','reservation_id']:
                bounded(item[field])
            if any(not isinstance(item[field], str) or HEX.fullmatch(item[field]) is None
                   for field in ['scope_hash','snapshot_hash']):
                raise PlatformError(422, 'invalid_reconciliation_plan')
            if (not isinstance(item['amount'], str) or re.fullmatch(r'[1-9][0-9]{0,18}',item['amount']) is None
                    or int(item['amount']) > 9223372036854775807):
                raise PlatformError(422, 'invalid_reconciliation_plan')
        ids = _ids([item['id'] for item in value['items']])
        if ids != [item['id'] for item in value['items']]:
            raise PlatformError(422, 'invalid_reconciliation_plan')
        if (not isinstance(reviewed_hash, str) or HEX.fullmatch(reviewed_hash) is None
                or value['plan_sha256'] != reviewed_hash or plan_digest(value) != reviewed_hash):
            raise PlatformError(409, 'reconciliation_plan_mismatch')
        return actor, ids

    async def operation(self, operator, key):
        actor = _operator(operator);_key(key)
        async with self.db.connection() as conn:
            await conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            row = await (await conn.execute('SELECT result FROM execution_resolution_operations WHERE actor_id=%s AND operation_key=%s',
                                           (actor, key))).fetchone()
            if row is None:
                raise PlatformError(404, 'reconciliation_operation_not_found')
            return row['result']

    async def apply_waivers(self, operator, plan, reviewed_plan_hash, key):
        actor, ids = self._validate_plan(operator, plan, reviewed_plan_hash)
        _key(key)
        async with self.db.connection() as conn:
            await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                               ('execution-writeoff:' + actor + ':' + key,))
            old = await (await conn.execute('SELECT plan_hash,result FROM execution_resolution_operations WHERE actor_id=%s AND operation_key=%s',
                                           (actor, key))).fetchone()
            if old:
                if old['plan_hash'] != reviewed_plan_hash:
                    raise PlatformError(409, 'idempotency_conflict')
                return old['result']
            # Lock every wallet in deterministic order before the per-attempt
            # canonical locks, so multi-wallet batches cannot invert lock order.
            wallets = await (await conn.execute('SELECT DISTINCT billing_account_id FROM execution_jobs WHERE id=ANY(%s)', (ids,))).fetchall()
            await conn.execute('SELECT id FROM billing_accounts WHERE id=ANY(%s) ORDER BY id FOR UPDATE',
                               (sorted(row['billing_account_id'] for row in wallets),))
            locked = []
            for item in plan['items']:
                job = await self.service._job(conn,item['id'],item['attempt_id'])
                if await self._item(conn,item['id'],locked=job) != item:
                    raise PlatformError(409,'reconciliation_plan_mismatch')
                locked.append(job)
            operation_id = str(uuid4())
            result = {'operation_id': operation_id, 'plan_sha256': reviewed_plan_hash, 'kind': 'platform_absorbed',
                      'items': [{'id': row['id'], 'status': 'waived', 'actual': None, 'customer_charge': '0',
                                 'provider_usage_known': False, 'original_amount': str(row['amount'])}
                                for row, _, _ in locked],
                      'released_microcredits': str(sum(row['amount'] for row, _, _ in locked))}
            await conn.execute('INSERT INTO execution_resolution_operations(id,actor_id,operation_key,plan_hash,plan,result) VALUES (%s,%s,%s,%s,%s,%s)',
                               (operation_id, actor, key, reviewed_plan_hash, Jsonb(plan), Jsonb(result)))
            for item, (row, scope, reservation) in zip(plan['items'], locked, strict=True):
                await conn.execute('''INSERT INTO execution_resolutions(job_id,operation_id,kind,attempt_id,binding_hash,
                    scope_hash,reservation_id,original_amount,customer_charge,provider_usage_known,actor_id,reason,
                    approval_reference,plan_hash,snapshot_hash) VALUES (%s,%s,'platform_absorbed',%s,%s,%s,%s,%s,0,false,%s,%s,%s,%s,%s)''',
                    (row['id'],operation_id,row['attempt_id'],row['binding_hash'],item['scope_hash'],row['reservation_id'],
                     row['amount'],actor,plan['reason'],plan['approval_reference'],reviewed_plan_hash,item['snapshot_hash']))
                await self.service.ledger._complete(conn,scope,reservation,0,operation_id,'waived')
                await conn.execute('UPDATE execution_invocations SET held=held-%s WHERE id=%s', (row['amount'],row['invocation_id']))
                await conn.execute("UPDATE execution_jobs SET status='waived',actual=NULL,reason='platform_absorbed',updated_at=clock_timestamp() WHERE id=%s", (row['id'],))
            return result
