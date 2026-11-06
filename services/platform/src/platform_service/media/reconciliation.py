"""Server/operator-only, audited decisions to absorb unknown media provider costs.

The media counterpart of execution.reconciliation (migration 021). It never
contacts a provider, replays a generation, recovers an artifact or turns an
unknown supplier charge into zero usage. Every write-off needs the operator
capability and an exactly reviewed plan; the database re-verifies the batch.
"""
import hashlib
from uuid import uuid4

from psycopg.types.json import Jsonb

from ..credits import CreditLedger
from ..errors import PlatformError
from ..execution.reconciliation import (HEX, ITEM_FIELDS, MAX_BATCH, _canonical, _ids, _key, _operator,
                                        _reason, plan_digest)
from ..identity import now
from .core import scope_of

PLAN_FIELDS = frozenset({'version','kind','subject','actor','reason','approval_reference','items','plan_sha256'})


class MediaReconciliation:
    def __init__(self, db):
        self.db = db
        self.ledger = CreditLedger(db)

    async def _rows(self, conn, job_id, *, lock):
        suffix = ' FOR UPDATE' if lock else ''
        row = await (await conn.execute('SELECT * FROM media_jobs WHERE id=%s', (job_id,))).fetchone()
        if row is None:
            raise PlatformError(404, 'media_not_found')
        if lock:
            # Same order as MediaService._job: wallet, invocation, job, reservation.
            await conn.execute('SELECT id FROM media_invocations WHERE id=%s FOR UPDATE', (row['invocation_id'],))
            row = await (await conn.execute('SELECT * FROM media_jobs WHERE id=%s FOR UPDATE', (job_id,))).fetchone()
        reservation = await (await conn.execute('SELECT * FROM credit_reservations WHERE id=%s' + suffix,
                                                (row['reservation_id'],))).fetchone()
        return row, reservation

    async def _item(self, conn, job_id, *, lock=False):
        row, reservation = await self._rows(conn, job_id, lock=lock)
        if (reservation is None or row['status'] != 'pending_reconciliation'
                or reservation['status'] != 'pending_reconciliation'
                or row['actual'] is not None or reservation['actual'] is not None
                or reservation['completion_key'] is not None or row['dispatched_at'] is None
                or row['lease_until'] > now() or row['amount'] != reservation['amount']):
            raise PlatformError(409, 'media_writeoff_ineligible')
        if await (await conn.execute('SELECT 1 FROM media_receipts WHERE job_id=%s', (job_id,))).fetchone():
            raise PlatformError(409, 'media_receipt_present')
        if await (await conn.execute('SELECT 1 FROM media_terminal_evidence WHERE job_id=%s', (job_id,))).fetchone():
            raise PlatformError(409, 'media_evidence_available')
        work = await (await conn.execute('SELECT phase FROM media_work WHERE job_id=%s', (job_id,))).fetchone()
        if work and work['phase'] != 'manual':
            raise PlatformError(409, 'media_work_active')
        budget = await (await conn.execute('SELECT * FROM media_storage_budgets WHERE job_id=%s', (job_id,))).fetchone()
        scope_hash = (await (await conn.execute("SELECT encode(sha256(convert_to(scope::text,'UTF8')),'hex') value FROM media_jobs WHERE id=%s",
                                               (job_id,))).fetchone())['value']
        snapshot = {'job': dict(row), 'reservation': dict(reservation), 'scope_hash': scope_hash,
                    'storage_budget': dict(budget) if budget else None, 'work_phase': work['phase'] if work else None,
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
        value = {'version': 1, 'kind': 'platform_absorbed', 'subject': 'media', 'actor': actor, 'reason': reason,
                 'approval_reference': approval_reference, 'items': items}
        return {**value, 'plan_sha256': plan_digest(value)}

    def _validate_plan(self, operator, value, reviewed_hash):
        actor = _operator(operator)
        if (not isinstance(value, dict) or set(value) != PLAN_FIELDS
                or type(value['version']) is not int or value['version'] != 1
                or value['kind'] != 'platform_absorbed' or value['subject'] != 'media' or value['actor'] != actor):
            raise PlatformError(422, 'invalid_reconciliation_plan')
        _reason(value['reason']);_reason(value['approval_reference'])
        if not isinstance(value['items'], list) or not 1 <= len(value['items']) <= MAX_BATCH:
            raise PlatformError(422, 'invalid_reconciliation_plan')
        for item in value['items']:
            if not isinstance(item, dict) or set(item) != ITEM_FIELDS:
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
            row = await (await conn.execute('SELECT result FROM media_resolution_operations WHERE actor_id=%s AND operation_key=%s',
                                           (actor, key))).fetchone()
            if row is None:
                raise PlatformError(404, 'reconciliation_operation_not_found')
            return row['result']

    async def apply_waivers(self, operator, plan, reviewed_plan_hash, key):
        actor, ids = self._validate_plan(operator, plan, reviewed_plan_hash)
        _key(key)
        async with self.db.connection() as conn:
            await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                               ('media-writeoff:' + actor + ':' + key,))
            old = await (await conn.execute('SELECT plan_hash,result FROM media_resolution_operations WHERE actor_id=%s AND operation_key=%s',
                                           (actor, key))).fetchone()
            if old:
                if old['plan_hash'] != reviewed_plan_hash:
                    raise PlatformError(409, 'idempotency_conflict')
                return old['result']
            wallets = await (await conn.execute('SELECT DISTINCT billing_account_id FROM media_jobs WHERE id=ANY(%s)', (ids,))).fetchall()
            await conn.execute('SELECT id FROM billing_accounts WHERE id=ANY(%s) ORDER BY id FOR UPDATE',
                               (sorted(row['billing_account_id'] for row in wallets),))
            locked = []
            for item in plan['items']:
                if await self._item(conn, item['id'], lock=True) != item:
                    raise PlatformError(409, 'reconciliation_plan_mismatch')
                locked.append(await self._rows(conn, item['id'], lock=True))
            operation_id = str(uuid4())
            result = {'operation_id': operation_id, 'plan_sha256': reviewed_plan_hash, 'kind': 'platform_absorbed',
                      'items': [{'id': row['id'], 'status': 'waived', 'actual': None, 'customer_charge': '0',
                                 'provider_usage_known': False, 'original_amount': str(row['amount'])}
                                for row, _ in locked],
                      'released_microcredits': str(sum(row['amount'] for row, _ in locked))}
            await conn.execute('INSERT INTO media_resolution_operations(id,actor_id,operation_key,plan_hash,plan,result) VALUES (%s,%s,%s,%s,%s,%s)',
                               (operation_id, actor, key, reviewed_plan_hash, Jsonb(plan), Jsonb(result)))
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('media-storage-v1',0))")
            for item, (row, reservation) in zip(plan['items'], locked, strict=True):
                await conn.execute('''INSERT INTO media_resolutions(job_id,operation_id,kind,attempt_id,binding_hash,
                    scope_hash,reservation_id,original_amount,customer_charge,provider_usage_known,actor_id,reason,
                    approval_reference,plan_hash,snapshot_hash) VALUES (%s,%s,'platform_absorbed',%s,%s,%s,%s,%s,0,false,%s,%s,%s,%s,%s)''',
                    (row['id'],operation_id,row['attempt_id'],row['binding_hash'],item['scope_hash'],row['reservation_id'],
                     row['amount'],actor,plan['reason'],plan['approval_reference'],reviewed_plan_hash,item['snapshot_hash']))
                await self.ledger._complete(conn,scope_of(row['scope']),reservation,0,operation_id,'waived')
                await conn.execute('UPDATE media_invocations SET held=held-%s WHERE id=%s', (row['amount'],row['invocation_id']))
                await conn.execute("UPDATE media_jobs SET status='waived',actual=NULL,reason='platform_absorbed',delivery_state='unavailable',updated_at=clock_timestamp() WHERE id=%s", (row['id'],))
                await conn.execute('UPDATE media_storage_budgets SET remaining_bytes=0,remaining_files=0,released_at=clock_timestamp() WHERE job_id=%s AND released_at IS NULL', (row['id'],))
            return result
