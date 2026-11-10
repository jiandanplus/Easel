"""Fixed, bounded Console projections. Every page uses a real read-only snapshot."""
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from .errors import PlatformError

MONEY = {'issued','available','held','spent','expired','refunded','amount','actual','amount_minor',
         'revision','test_available','live_available','experience_available','delta',
         'spendable_available','frozen_available','unposted_expired_available',
         'experience_issued','experience_held','experience_spent'}
from .model_templates import public_templates
from .provider_channels import CAPABILITY_CHANNEL, CHANNELS, provider_id, public_platforms

SNAPSHOT_NOTE = '只读余额快照；账面剩余包含尚未过账的过期额度。冻结与未过账过期可能重叠，不可相加。'


LIVE = 'x.deleted_at IS NULL AND '
USER_FILTERS = {
    '': 'x.deleted_at IS NULL',
    'funded': LIVE + 'x.spendable_available>0',
    'empty': LIVE + 'x.spendable_available=0',
    'expiring': LIVE + "x.expires_at>transaction_timestamp() AND x.expires_at<=transaction_timestamp()+interval '7 days'",
    'disabled': LIVE + 'NOT x.active',
    'admins': LIVE + "x.role<>'user'",
    'deleted': 'x.deleted_at IS NOT NULL',
}
# The plan a person is on now: the live membership that runs longest, ended ones excluded.
CURRENT_PLAN = """FROM memberships m JOIN plans p ON p.id=m.plan_id JOIN billing_accounts b ON b.id=m.billing_account_id
    JOIN tenants t ON t.id=b.tenant_id WHERE t.owner_user_id=u.id AND m.starts_at<=transaction_timestamp()
    AND m.expires_at>transaction_timestamp() AND NOT EXISTS(SELECT 1 FROM membership_revocations r WHERE r.membership_id=m.id)
    AND NOT EXISTS(SELECT 1 FROM console_credit_adjustments a WHERE a.membership_id=m.id)
    ORDER BY m.expires_at DESC,m.id LIMIT 1"""
USER_SORTS = {
    'created': 'x.created_at DESC,x.id DESC',
    'balance': 'x.spendable_available DESC,x.id DESC',
    'spent': 'x.spent DESC,x.id DESC',
    'last_used': 'x.last_used_at DESC NULLS LAST,x.id DESC',
}
USAGE_STATUSES = {'', 'settled', 'released', 'reserved', 'dispatched', 'pending_reconciliation'}
STAT_DAYS = {7, 30, 90}
# Calendar days in China time; live points only, so test funding never inflates usage.
DAILY_SQL = """SELECT d.day::date AS day,
    (SELECT count(*) FROM users u WHERE (u.created_at AT TIME ZONE 'Asia/Shanghai')::date=d.day::date) AS new_users,
    count(r.id) AS calls,
    count(DISTINCT r.billing_account_id) AS active_wallets,
    coalesce(sum(r.actual) FILTER(WHERE r.status='settled'),0) AS spent
    FROM generate_series((transaction_timestamp() AT TIME ZONE 'Asia/Shanghai')::date-(%s-1),
        (transaction_timestamp() AT TIME ZONE 'Asia/Shanghai')::date, interval '1 day') AS d(day)
    LEFT JOIN credit_reservations r ON (r.created_at AT TIME ZONE 'Asia/Shanghai')::date=d.day::date
        AND r.funding_mode='live' AND r.created_at>=transaction_timestamp()-make_interval(days=>%s+1) AND {where}
    GROUP BY d.day ORDER BY d.day"""
MODEL_SQL = """SELECT coalesce(ej.model,mj.model,r.operation) AS model,count(*) AS calls,
    coalesce(sum(r.actual) FILTER(WHERE r.status='settled'),0) AS spent
    FROM credit_reservations r LEFT JOIN execution_jobs ej ON ej.reservation_id=r.id LEFT JOIN media_jobs mj ON mj.reservation_id=r.id
    WHERE r.created_at>=transaction_timestamp()-make_interval(days=>%s) AND {where} r.funding_mode='live'
    GROUP BY 1 ORDER BY spent DESC,calls DESC,model LIMIT 10"""


def provider_registry(values):
    """Only public server-supplied IDs/mappings; never endpoint or credentials."""
    result = {}
    if not isinstance(values, (list, tuple, set, frozenset)) or len(values) > 100:
        raise ValueError('invalid_console_provider_registry')
    for item in values:
        if isinstance(item, str) and item and len(item) <= 128:
            result[(item, '', '')] = {}
            continue
        if (not isinstance(item, dict) or set(item) != {'id','version','models'}
                or not isinstance(item['models'], dict) or len(item['models']) > 200):
            raise ValueError('invalid_console_provider_registry')
        for value in (item['id'], item['version']):
            if not isinstance(value, str) or not value or len(value) > 128 or any(ord(c) < 33 for c in value):
                raise ValueError('invalid_console_provider_registry')
        for model, metadata in item['models'].items():
            if (not isinstance(model, str) or not model or len(model) > 128
                    or not isinstance(metadata, dict)
                    or set(metadata) != {'upstream_model','verification','verification_ref'}
                    or metadata['verification'] not in {'live','fixture'}
                    or any(not isinstance(value, str) or not value or len(value) > 255
                           or any(ord(c) < 32 for c in value) for value in metadata.values())):
                raise ValueError('invalid_console_provider_registry')
            key = (item['id'], item['version'], model)
            if key in result:
                raise ValueError('duplicate_console_provider_registry')
            result[key] = dict(metadata)
    return result


def projected(row):
    return {key: str(value) if (key in MONEY or isinstance(value, Decimal)) and value is not None
            else value.isoformat() if isinstance(value, datetime) else value
            for key, value in row.items()}


def page_args(limit, offset, q):
    if (type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int
            or not 0 <= offset <= 100000 or not isinstance(q, str) or len(q) > 128
            or any(ord(char) < 32 for char in q)):
        raise PlatformError(422, 'invalid_console_query')


def identifier(value):
    if not isinstance(value, str) or not value or len(value) > 255 or any(ord(c) < 32 for c in value):
        raise PlatformError(422, 'invalid_console_query')
    return value


def offer_snapshot(definition):
    allowed=('id','plan_id','version','name','credits','days','concurrency','included_products',
             'features','amount_minor','currency','test_only','period')
    result={key:definition[key] for key in allowed if key in definition}
    terms=definition.get('terms',{})
    result['terms']={key:terms[key] for key in ('refund_policy','description') if key in terms}
    return result


def directory_of(settings):
    from .logto_admin import from_settings
    return from_settings(settings)


class ConsoleQueries:
    def __init__(self, db, auth, *, provider_ids=(), payments_mode='disabled', sealing_ready=False):
        if payments_mode not in {'disabled','test','live'}:
            raise ValueError('invalid_console_payments_mode')
        self.db, self.auth, self.payments_mode = db, auth, payments_mode
        self.registry = provider_registry(provider_ids)
        self.sealing_ready = sealing_ready
        self._directory = None
        self._directory_set = False

    @property
    def directory(self):
        """The Logto Management API client, or None when the console has no credentials for it."""
        return self._directory if self._directory_set else directory_of(getattr(self.auth, 'settings', None))

    @property
    def console_issuer(self):
        return getattr(getattr(self.auth, 'settings', None), 'issuer', '') or ''

    @property
    def product_issuer(self):
        return getattr(getattr(self.auth, 'settings', None), 'product_issuer', None) or self.console_issuer

    @directory.setter
    def directory(self, value):
        self._directory, self._directory_set = value, True

    @asynccontextmanager
    async def _snapshot(self, principal):
        async with self.db.connection() as conn:
            await conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            await conn.execute("SET LOCAL statement_timeout='5s'")
            await conn.execute("SET LOCAL lock_timeout='1s'")
            await self.auth.validate(conn, principal, 'console:read')
            yield conn

    async def _list(self, principal, statement, params, limit, offset, q, convert=projected):
        page_args(limit, offset, q)
        async with self._snapshot(principal) as conn:
            rows = await (await conn.execute(statement + ' LIMIT %s OFFSET %s', (*params, limit + 1, offset))).fetchall()
            return {'items': [convert(row) for row in rows[:limit]], 'limit': limit,
                    'offset': offset, 'has_more': len(rows) > limit}

    async def overview(self, principal):
        async with self._snapshot(principal) as conn:
            result = {}
            for key, table in (('users','users'),('products','products'),('orders','billing_orders'),
                               ('executions','execution_jobs'),('wallet_count','billing_accounts')):
                result[key] = (await (await conn.execute('SELECT count(*) AS n FROM ' + table)).fetchone())['n']
            result['pending_reconciliation'] = (await (await conn.execute("SELECT count(*) AS n FROM execution_jobs WHERE status='pending_reconciliation'")).fetchone())['n']
            today = await (await conn.execute("""SELECT count(*) AS calls,
                coalesce(sum(actual) FILTER(WHERE status='settled'),0) AS spent,
                count(*) FILTER(WHERE status IN ('pending_reconciliation','waived')) AS problems
                FROM credit_reservations WHERE funding_mode='live' AND created_at>=
                date_trunc('day',transaction_timestamp() AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai'""")).fetchone()
            result['today'] = projected(today)
            result['expiring_soon'] = (await (await conn.execute("""SELECT count(*) AS n FROM (
                SELECT m.billing_account_id,max(m.expires_at) AS last FROM memberships m JOIN plans p ON p.id=m.plan_id
                WHERE NOT (p.definition->>'test_only')::boolean GROUP BY m.billing_account_id) x
                WHERE x.last>transaction_timestamp() AND x.last<=transaction_timestamp()+interval '7 days'""")).fetchone())['n']
            rows = await (await conn.execute("""SELECT CASE WHEN l.test_only THEN 'test' ELSE 'live' END AS funding_mode,
                sum(l.issued) AS issued,sum(l.available) AS available,sum(l.held) AS held,sum(l.spent) AS spent,
                sum(l.expired) AS expired,sum(l.refunded) AS refunded,
                coalesce(sum(l.available) FILTER(WHERE NOT l.frozen AND l.expires_at>transaction_timestamp()),0) AS spendable_available,
                coalesce(sum(l.available) FILTER(WHERE l.frozen),0) AS frozen_available,
                coalesce(sum(l.available) FILTER(WHERE l.expires_at<=transaction_timestamp()),0) AS unposted_expired_available,
                coalesce(sum(l.issued) FILTER(WHERE g.id IS NOT NULL),0) AS experience_issued,
                coalesce(sum(l.available) FILTER(WHERE g.id IS NOT NULL AND NOT l.frozen AND l.expires_at>transaction_timestamp()),0) AS experience_available,
                coalesce(sum(l.held) FILTER(WHERE g.id IS NOT NULL),0) AS experience_held,
                coalesce(sum(l.spent) FILTER(WHERE g.id IS NOT NULL),0) AS experience_spent
                FROM credit_lots l LEFT JOIN internal_grants g ON g.lot_id=l.id GROUP BY l.test_only ORDER BY l.test_only DESC""")).fetchall()
            result['wallets'] = [projected(row) for row in rows]
            result['payments_mode'] = self.payments_mode
            result['as_of'] = (await (await conn.execute('SELECT transaction_timestamp() AS now')).fetchone())['now'].isoformat()
            result['snapshot_note'] = SNAPSHOT_NOTE
            return result

    async def users(self, principal, limit=25, offset=0, q='', status='', sort='created'):
        if status not in USER_FILTERS or sort not in USER_SORTS:
            raise PlatformError(422, 'console_query_invalid')
        owned = """FROM credit_reservations r JOIN billing_accounts b ON b.id=r.billing_account_id
                JOIN tenants t ON t.id=b.tenant_id WHERE t.owner_user_id=u.id AND r.funding_mode='live'"""
        return await self._list(principal, """SELECT * FROM (SELECT u.id,u.active,u.created_at,u.deleted_at,
            console_user_role(u.id,%s) AS role,
            (SELECT p.name """ + CURRENT_PLAN + """) AS plan_name,
            (SELECT m.expires_at """ + CURRENT_PLAN + """) AS plan_expires_at,
            (SELECT count(*) FROM tenants t WHERE t.owner_user_id=u.id) AS tenant_count,
            (SELECT count(*) FROM billing_accounts b JOIN tenants t ON t.id=b.tenant_id WHERE t.owner_user_id=u.id) AS wallet_count,
            (SELECT coalesce(sum(l.available) FILTER(WHERE NOT l.frozen AND l.expires_at>transaction_timestamp()),0)
                FROM credit_lots l JOIN billing_accounts b ON b.id=l.billing_account_id JOIN tenants t ON t.id=b.tenant_id
                WHERE t.owner_user_id=u.id AND NOT l.test_only) AS spendable_available,
            (SELECT max(m.expires_at) FROM memberships m JOIN billing_accounts b ON b.id=m.billing_account_id
                JOIN tenants t ON t.id=b.tenant_id WHERE t.owner_user_id=u.id) AS expires_at,
            (SELECT coalesce(sum(r.actual) FILTER(WHERE r.status='settled'),0) """ + owned + """) AS spent,
            (SELECT count(*) """ + owned + """) AS calls,
            (SELECT max(r.created_at) """ + owned + """) AS last_used_at,
            (SELECT min(e.username) FROM external_identities e WHERE e.user_id=u.id) AS username,
            (SELECT min(e.email) FROM external_identities e WHERE e.user_id=u.id) AS email,
            (SELECT n.tags FROM console_user_notes n WHERE n.user_id=u.id) AS tags
            FROM users u WHERE %s='' OR strpos(lower(u.id),lower(%s))>0 OR EXISTS(SELECT 1 FROM external_identities e
                WHERE e.user_id=u.id AND (strpos(lower(coalesce(e.username,'')),lower(%s))>0 OR strpos(lower(coalesce(e.email,'')),lower(%s))>0))
            OR EXISTS(SELECT 1 FROM console_user_notes n WHERE n.user_id=u.id AND (strpos(lower(n.note),lower(%s))>0 OR lower(%s)=ANY(SELECT lower(t) FROM unnest(n.tags) t)))
            ) x WHERE """ + USER_FILTERS[status] + " ORDER BY " + USER_SORTS[sort], (self.console_issuer,q,q,q,q,q,q),limit,offset,q)

    @staticmethod
    async def _owned_wallets(conn, id):
        identifier(id)
        if not await (await conn.execute('SELECT 1 FROM users WHERE id=%s', (id,))).fetchone():
            raise PlatformError(404, 'console_user_not_found')
        rows = await (await conn.execute('SELECT b.id FROM billing_accounts b JOIN tenants t ON t.id=b.tenant_id WHERE t.owner_user_id=%s ORDER BY b.id', (id,))).fetchall()
        return [row['id'] for row in rows]

    async def user_activity(self, principal, id, days=30):
        if days not in STAT_DAYS:
            raise PlatformError(422, 'console_query_invalid')
        async with self._snapshot(principal) as conn:
            wallets = await self._owned_wallets(conn, id)
            daily = await (await conn.execute(DAILY_SQL.format(where="r.billing_account_id=ANY(%s)"), (days, days, wallets))).fetchall()
            models = await (await conn.execute(MODEL_SQL.format(where="r.billing_account_id=ANY(%s) AND"), (days, wallets))).fetchall()
            return {'days': days, 'daily': [projected(r) for r in daily], 'models': [projected(r) for r in models]}

    async def user_usage(self, principal, id, limit=25, offset=0, model='', status=''):
        page_args(limit, offset, model)
        if status not in USAGE_STATUSES:
            raise PlatformError(422, 'console_query_invalid')
        async with self._snapshot(principal) as conn:
            wallets = await self._owned_wallets(conn, id)
            rows = await (await conn.execute("""SELECT r.id,r.created_at,r.operation,r.product_id,r.status,r.amount,r.actual,r.funding_mode,
                coalesce(ej.model,mj.model) AS model
                FROM credit_reservations r LEFT JOIN execution_jobs ej ON ej.reservation_id=r.id LEFT JOIN media_jobs mj ON mj.reservation_id=r.id
                WHERE r.billing_account_id=ANY(%s) AND (%s='' OR coalesce(ej.model,mj.model,'')=%s) AND (%s='' OR r.status=%s)
                ORDER BY r.created_at DESC,r.id DESC LIMIT %s OFFSET %s""", (wallets, model, model, status, status, limit + 1, offset))).fetchall()
            return {'items': [projected(r) for r in rows[:limit]], 'limit': limit, 'offset': offset, 'has_more': len(rows) > limit}

    async def user_ledger(self, principal, id, limit=25, offset=0):
        page_args(limit, offset, '')
        async with self._snapshot(principal) as conn:
            wallets = await self._owned_wallets(conn, id)
            rows = await (await conn.execute("""SELECT j.id,j.kind,j.created_at,j.billing_account_id,
                (SELECT m.plan_id FROM memberships m WHERE j.kind='issue' AND m.id=j.source) AS plan_id,
                coalesce(sum(e.delta) FILTER(WHERE e.bucket='available'),0) AS available,
                coalesce(sum(e.delta) FILTER(WHERE e.bucket='held'),0) AS held,
                coalesce(sum(e.delta) FILTER(WHERE e.bucket='spent'),0) AS spent,
                coalesce(sum(e.delta) FILTER(WHERE e.bucket='expired'),0) AS expired
                FROM credit_journals j JOIN credit_entries e ON e.journal_id=j.id
                WHERE j.billing_account_id=ANY(%s) GROUP BY j.id ORDER BY j.created_at DESC,j.id DESC LIMIT %s OFFSET %s""",
                (wallets, limit + 1, offset))).fetchall()
            return {'items': [projected(r) for r in rows[:limit]], 'limit': limit, 'offset': offset, 'has_more': len(rows) > limit}

    async def user_sessions(self, principal, id):
        async with self._snapshot(principal) as conn:
            await self._owned_wallets(conn, id)
            rows = await (await conn.execute("""SELECT product_id,created_at,expires_at,revoked_at,
                revoked_at IS NULL AND expires_at>transaction_timestamp() AS active
                FROM identity_sessions WHERE user_id=%s ORDER BY created_at DESC LIMIT 51""", (id,))).fetchall()
            return {'items': [projected(r) for r in rows[:50]], 'has_more': len(rows) > 50}

    async def user_audits(self, principal, id):
        async with self._snapshot(principal) as conn:
            wallets = await self._owned_wallets(conn, id)
            rows = await (await conn.execute(self._audit_sql() + " WHERE a.target_id=ANY(%s) ORDER BY a.created_at DESC,a.id DESC LIMIT 51",
                                             ([id, *wallets],))).fetchall()
            return {'items': [self._audit(r) for r in rows[:50]], 'has_more': len(rows) > 50}

    async def redemptions(self, principal, limit=25, offset=0, q=''):
        return await self._list(principal, """SELECT b.id,b.name,b.plan_id,b.credits,b.code_count,b.expires_at,b.disabled_at,b.created_at,
            (p.definition->>'name') AS plan_name,(p.definition->>'days')::integer AS plan_days,
            (SELECT count(*) FROM redemption_codes c WHERE c.batch_id=b.id AND c.redeemed_at IS NOT NULL) AS redeemed,
            CASE WHEN b.disabled_at IS NOT NULL THEN 'disabled' WHEN b.expires_at<=transaction_timestamp() THEN 'expired' ELSE 'active' END AS state
            FROM redemption_batches b JOIN plans p ON p.id=b.plan_id
            WHERE %s='' OR strpos(lower(b.name||' '||b.plan_id||' '||b.id),lower(%s))>0 ORDER BY b.created_at DESC,b.id DESC""", (q, q), limit, offset, q)

    async def redemption(self, principal, id):
        identifier(id)
        async with self._snapshot(principal) as conn:
            batch = await (await conn.execute("""SELECT b.id,b.name,b.reason,b.plan_id,b.credits,b.code_count,b.expires_at,b.disabled_at,b.created_at,
                (p.definition->>'name') AS plan_name FROM redemption_batches b JOIN plans p ON p.id=b.plan_id WHERE b.id=%s""", (id,))).fetchone()
            if not batch:
                raise PlatformError(404, 'console_redemption_not_found')
            codes = await (await conn.execute("""SELECT left(c.code_hash,8) AS code_ref,c.redeemed_at,c.user_id,
                (SELECT min(e.username) FROM external_identities e WHERE e.user_id=c.user_id) AS username,
                (SELECT min(e.email) FROM external_identities e WHERE e.user_id=c.user_id) AS email
                FROM redemption_codes c WHERE c.batch_id=%s ORDER BY c.redeemed_at DESC NULLS LAST,c.code_hash LIMIT 100""", (id,))).fetchall()
            return {**projected(batch), 'codes': [projected(r) for r in codes]}

    async def admins(self, principal):
        async with self._snapshot(principal) as conn:
            admins = await (await conn.execute("""SELECT a.id,a.role,a.active,a.created_at,
                (SELECT max(s.created_at) FROM console_sessions s WHERE s.admin_id=a.id) AS last_login_at
                FROM console_admins a ORDER BY a.active DESC,a.created_at""")).fetchall()
            requests = await (await conn.execute("""SELECT id,subject_hash,verified_at,expires_at FROM console_login_requests
                WHERE status='pending' AND expires_at>transaction_timestamp() ORDER BY verified_at DESC LIMIT 50""")).fetchall()
            return {'admins': [projected(r) for r in admins], 'requests': [projected(r) for r in requests], 'self': principal.id}

    async def stats(self, principal, days=30):
        if days not in STAT_DAYS:
            raise PlatformError(422, 'console_query_invalid')
        async with self._snapshot(principal) as conn:
            daily = await (await conn.execute(DAILY_SQL.format(where="true"), (days, days))).fetchall()
            models = await (await conn.execute(MODEL_SQL.format(where=""), (days,))).fetchall()
            totals = await (await conn.execute("""SELECT
                (SELECT count(*) FROM users) AS users,
                (SELECT count(*) FROM users WHERE created_at>=transaction_timestamp()-make_interval(days=>%s)) AS new_users,
                (SELECT count(DISTINCT billing_account_id) FROM credit_reservations WHERE funding_mode='live'
                    AND created_at>=transaction_timestamp()-make_interval(days=>%s)) AS active_wallets""", (days, days))).fetchone()
            return {'days': days, 'totals': projected(totals), 'daily': [projected(r) for r in daily],
                    'models': [projected(r) for r in models], 'as_of': (await (await conn.execute('SELECT transaction_timestamp() AS now')).fetchone())['now'].isoformat()}

    async def user(self, principal, id):
        identifier(id)
        async with self._snapshot(principal) as conn:
            row = await (await conn.execute('SELECT id,active,created_at,deleted_at,console_user_role(id,%s) AS role FROM users WHERE id=%s',
                                            (self.console_issuer, id))).fetchone()
            if not row:
                raise PlatformError(404,'console_user_not_found')
            result = projected(row)
            result['plans'] = [projected(r) for r in await (await conn.execute("""SELECT m.id,m.billing_account_id AS wallet_id,
                m.plan_id,p.name AS plan_name,m.starts_at,m.expires_at,(p.definition->>'test_only')::boolean AS test_only,
                l.issued,l.available,l.spent,l.held,r.membership_id IS NOT NULL AS ended,
                m.expires_at<=transaction_timestamp() AS expired,
                CASE WHEN EXISTS(SELECT 1 FROM console_credit_adjustments a WHERE a.membership_id=m.id AND a.direction='add') THEN 'points'
                     WHEN m.operator_id LIKE 'console:%%' THEN 'console' WHEN m.operator_id LIKE 'billing:%%' THEN 'purchase'
                     WHEN m.operator_id LIKE 'redemption:%%' THEN 'redemption' ELSE 'other' END AS source
                FROM memberships m JOIN plans p ON p.id=m.plan_id JOIN billing_accounts b ON b.id=m.billing_account_id
                JOIN tenants t ON t.id=b.tenant_id LEFT JOIN credit_lots l ON l.membership_id=m.id
                LEFT JOIN membership_revocations r ON r.membership_id=m.id
                WHERE t.owner_user_id=%s ORDER BY (r.membership_id IS NULL AND m.expires_at>transaction_timestamp()) DESC,
                m.expires_at DESC,m.id LIMIT 50""", (id,))).fetchall()]
            if 'admins:manage' in principal.permissions and self.directory is not None:
                subject = (await (await conn.execute('SELECT console_user_subject(%s,%s,%s) AS subject',
                    (principal.id, id, self.product_issuer))).fetchone())['subject']
            else:
                subject = None
            note = await (await conn.execute('SELECT note,tags,updated_at FROM console_user_notes WHERE user_id=%s', (id,))).fetchone()
            result['note'] = projected(note) if note else None
            result['identities'] = [projected(r) for r in await (await conn.execute('SELECT username,email,email_verified FROM external_identities WHERE user_id=%s ORDER BY username,email LIMIT 10',(id,))).fetchall()]
            result['tenants'] = [projected(r) for r in await (await conn.execute('SELECT tenant_id AS id,role,active FROM tenant_members WHERE user_id=%s ORDER BY tenant_id LIMIT 101',(id,))).fetchall()]
            result['wallets'] = [projected(r) for r in await (await conn.execute('SELECT b.id,b.tenant_id FROM billing_accounts b JOIN tenant_members m ON m.tenant_id=b.tenant_id WHERE m.user_id=%s ORDER BY b.id LIMIT 101',(id,))).fetchall()]
            result['products'] = [projected(r) for r in await (await conn.execute('SELECT product_id,count(*) AS session_count FROM identity_sessions WHERE user_id=%s GROUP BY product_id ORDER BY product_id LIMIT 101',(id,))).fetchall()]
            result['has_more'] = any(len(result[name])>100 for name in ('tenants','wallets','products'))
            for name in ('tenants','wallets','products'):
                result[name] = result[name][:100]
        result['profile'] = None
        if subject:
            from .logto_admin import profile
            try:
                result['profile'] = profile(await self.directory.get_user(subject))
            except PlatformError as error:
                result['profile_error'] = error.code
        result['directory'] = self.directory is not None
        return result

    async def products(self, principal, limit=25, offset=0, q=''):
        return await self._list(principal,"""SELECT p.id,p.enabled,
            (SELECT count(*) FROM product_clients c WHERE c.product_id=p.id) AS client_count
            FROM products p WHERE %s='' OR strpos(lower(p.id),lower(%s))>0 ORDER BY p.id""",(q,q),limit,offset,q)

    @staticmethod
    def _plan(row):
        allowed = ('id','name','version','included_products','features','credits','days','concurrency','test_only')
        return {**{key:row['definition'][key] for key in allowed},
                'models':row['definition'].get('models'),'pricing':row['definition'].get('pricing'),
                'created_by':row['created_by'],'created_at':row['created_at'].isoformat(),
                'protected':row['definition']['id'].startswith('easel-internal-'),
                'system':row['definition']['id'].startswith('easel-'),
                'retired':bool(row.get('retired')),'replaced_by':row.get('replaced_by')}

    async def plans(self, principal, limit=25, offset=0, q=''):
        return await self._list(principal,"""SELECT p.definition,p.created_by,p.created_at,s.retired,s.replaced_by FROM plans p
            LEFT JOIN LATERAL (SELECT retired,replaced_by FROM plan_states WHERE plan_id=p.id ORDER BY created_at DESC,id DESC LIMIT 1) s ON true
            WHERE %s='' OR strpos(lower(p.id||' '||p.name),lower(%s))>0 ORDER BY coalesce(s.retired,false),p.created_at DESC,p.id DESC""",(q,q),limit,offset,q,self._plan)

    @staticmethod
    def _wallet_sql():
        return """SELECT b.id,b.tenant_id,transaction_timestamp() AS as_of,coalesce(sum(l.issued),0) AS issued,
            coalesce(sum(l.available),0) AS available,coalesce(sum(l.held),0) AS held,
            coalesce(sum(l.spent),0) AS spent,coalesce(sum(l.expired),0) AS expired,
            coalesce(sum(l.refunded),0) AS refunded,
            coalesce(sum(l.available) FILTER(WHERE NOT l.frozen AND l.expires_at>transaction_timestamp()),0) AS spendable_available,
            coalesce(sum(l.available) FILTER(WHERE l.frozen),0) AS frozen_available,
            coalesce(sum(l.available) FILTER(WHERE l.expires_at<=transaction_timestamp()),0) AS unposted_expired_available,
            coalesce(sum(l.available) FILTER(WHERE l.test_only AND NOT l.frozen AND l.expires_at>transaction_timestamp()),0) AS test_available,
            coalesce(sum(l.available) FILTER(WHERE NOT l.test_only AND NOT l.frozen AND l.expires_at>transaction_timestamp()),0) AS live_available,
            coalesce(sum(l.available) FILTER(WHERE g.id IS NOT NULL AND NOT l.frozen AND l.expires_at>transaction_timestamp()),0) AS experience_available,
            (SELECT count(*) FROM credit_reservations r WHERE r.billing_account_id=b.id AND r.status='pending_reconciliation') AS pending_reconciliation
            FROM billing_accounts b LEFT JOIN credit_lots l ON l.billing_account_id=b.id
            LEFT JOIN internal_grants g ON g.lot_id=l.id"""

    async def wallets(self, principal, limit=25, offset=0, q=''):
        return await self._list(principal,self._wallet_sql()+" WHERE %s='' OR strpos(lower(b.id),lower(%s))>0 GROUP BY b.id,b.tenant_id ORDER BY b.id",(q,q),limit,offset,q)

    async def wallet(self, principal, id):
        identifier(id)
        async with self._snapshot(principal) as conn:
            row = await (await conn.execute(self._wallet_sql()+' WHERE b.id=%s GROUP BY b.id,b.tenant_id',(id,))).fetchone()
            if not row:
                raise PlatformError(404,'console_wallet_not_found')
            result=projected(row)
            result['snapshot_note']=SNAPSHOT_NOTE
            queries={
                'memberships':"SELECT m.id,m.plan_id,m.starts_at,m.expires_at,(p.definition->>'test_only')::boolean AS test_only,CASE WHEN g.id IS NOT NULL THEN 'internal_experience' WHEN (p.definition->>'test_only')::boolean THEN 'test' ELSE 'live' END AS funding_source FROM memberships m JOIN plans p ON p.id=m.plan_id LEFT JOIN internal_grants g ON g.membership_id=m.id WHERE m.billing_account_id=%s ORDER BY m.created_at DESC,m.id DESC LIMIT 101",
                'lots':"SELECT l.id,l.membership_id,l.issued,l.available,l.held,l.spent,l.expired,l.refunded,l.frozen,l.test_only,l.expires_at,CASE WHEN g.id IS NOT NULL THEN 'internal_experience' WHEN l.test_only THEN 'test' ELSE 'live' END AS funding_source FROM credit_lots l LEFT JOIN internal_grants g ON g.lot_id=l.id WHERE l.billing_account_id=%s ORDER BY l.created_at DESC,l.id DESC LIMIT 101",
                'reservations':"SELECT id,product_id,operation,rate_version,amount,actual,status,funding_mode,created_at,updated_at FROM credit_reservations WHERE billing_account_id=%s ORDER BY created_at DESC,id DESC LIMIT 101",
                'journals':"SELECT id,kind,source,operator_id,created_at FROM credit_journals WHERE billing_account_id=%s ORDER BY created_at DESC,id DESC LIMIT 101"}
            truncated={}
            for name,query in queries.items():
                rows=await (await conn.execute(query,(id,))).fetchall()
                truncated[name]=len(rows)>100
                result[name]=[projected(r) for r in rows[:100]]
            # One bounded query, not a per-journal unbounded history scan.
            journal_ids=[r['id'] for r in result['journals']]
            entries=await (await conn.execute('SELECT journal_id,bucket,delta FROM credit_entries WHERE billing_account_id=%s AND journal_id=ANY(%s) ORDER BY id LIMIT 1001',(id,journal_ids))).fetchall()
            for journal in result['journals']:
                journal['entries']=[{'bucket':r['bucket'],'delta':str(r['delta'])} for r in entries if r['journal_id']==journal['id']]
            result['has_more']={**truncated,'entries':len(entries)>1000}
            return result

    @staticmethod
    def _order_fields():
        return 'id,product_id,billing_account_id,channel,mode,amount_minor,currency,status,credit_state,revision,created_at,updated_at,paid_at,expires_at'

    async def orders(self, principal, limit=25, offset=0, q=''):
        return await self._list(principal,'SELECT '+self._order_fields()+" FROM billing_orders WHERE %s='' OR strpos(lower(id||' '||status||' '||billing_account_id),lower(%s))>0 ORDER BY created_at DESC,id DESC",(q,q),limit,offset,q)

    async def order(self, principal, id):
        identifier(id)
        async with self._snapshot(principal) as conn:
            row=await (await conn.execute('SELECT '+self._order_fields()+',offer FROM billing_orders WHERE id=%s',(id,))).fetchone()
            if not row:
                raise PlatformError(404,'console_order_not_found')
            definition=row.pop('offer')
            result=projected(row)
            result['offer']=offer_snapshot(definition)
            refunds=await (await conn.execute('SELECT id,order_id,amount_minor,status,created_at,updated_at FROM billing_refunds WHERE order_id=%s ORDER BY created_at DESC,id DESC LIMIT 101',(id,))).fetchall()
            result['refunds']=[projected(r) for r in refunds[:100]]
            result['has_more']=len(refunds)>100
            return result

    @staticmethod
    def _offer(row):
        return {**offer_snapshot(row['definition']),
                **projected({key:row[key] for key in ('created_by','created_at','withdrawn','reason')}),
                'protected':row['definition']['plan_id'].startswith('easel-internal-')}

    async def offers(self, principal, limit=25, offset=0, q=''):
        return await self._list(principal,"""SELECT o.definition,o.created_by,o.created_at,w.offer_id IS NOT NULL AS withdrawn,w.reason
            FROM billing_offers o LEFT JOIN billing_offer_withdrawals w ON w.offer_id=o.id
            WHERE %s='' OR strpos(lower(o.id||' '||o.plan_id),lower(%s))>0 ORDER BY o.created_at DESC,o.id DESC""",(q,q),limit,offset,q,self._offer)

    @staticmethod
    def _model(row):
        definition=row['definition']
        allowed=('id','name','version','capability','feature','products','test_only','provider_id','provider_version','verification','verification_ref')
        rate=definition['rate']
        return {**{key:definition[key] for key in allowed},
                'rate':{'version':rate['version'],**{part:{key:rate[part][key] for key in ('numerator','denominator')} for part in ('input','output')}},
                'limits':{key:definition['limits'][key] for key in ('max_input_bytes','max_input_tokens','max_output_tokens')},
                **projected({key:row[key] for key in ('enabled','updated_at','operator_id')})}

    async def models(self, principal, limit=25, offset=0, q=''):
        return await self._list(principal,"""SELECT m.definition,s.enabled,s.updated_at,s.operator_id
            FROM execution_models m JOIN execution_model_states s ON s.model_id=m.id
            WHERE %s='' OR strpos(lower(m.id||' '||(m.definition->>'name')),lower(%s))>0 ORDER BY m.created_at DESC,m.id DESC""",(q,q),limit,offset,q,self._model)

    async def model_catalog(self, principal):
        async with self._snapshot(principal) as conn:
            rows = await (await conn.execute("""
              WITH models AS (
                SELECT 'execution' AS kind,m.id,m.definition,s.enabled,s.updated_at FROM execution_models m JOIN execution_model_states s ON s.model_id=m.id
                UNION ALL SELECT 'media',m.id,m.definition,s.enabled,s.updated_at FROM media_models m JOIN media_model_states s ON s.model_id=m.id)
              SELECT x.kind,x.id,x.definition,x.enabled,x.updated_at,b.upstream_model,
                (SELECT p.rate FROM model_prices p WHERE p.kind=x.kind AND p.model_id=x.id AND p.effective_at<=transaction_timestamp() ORDER BY p.effective_at DESC,p.created_at DESC LIMIT 1) AS price,
                (SELECT count(*) FROM model_trial_access t WHERE t.kind=x.kind AND t.model_id=x.id AND t.active) AS trial_wallets,
                CASE WHEN x.kind='execution' THEN (SELECT count(*) FROM execution_jobs j WHERE j.model=x.id AND j.status='settled') ELSE (SELECT count(*) FROM media_jobs j WHERE j.model=x.id AND j.status='settled') END AS settled_calls
              FROM models x LEFT JOIN managed_model_bindings b ON b.kind=x.kind AND b.model_id=x.id ORDER BY x.kind,x.id""")).fetchall()
            providers = await (await conn.execute('SELECT kind,provider_id,provider_version,adapter,capabilities,updated_at FROM provider_catalog ORDER BY kind,provider_id')).fetchall()
            items = [{'kind':r['kind'],'id':r['id'],'name':r['definition']['name'],'capability':r['definition']['capability'],'feature':r['definition']['feature'],
                      'provider_id':r['definition']['provider_id'],'enabled':r['enabled'],'updated_at':r['updated_at'].isoformat(),'managed':r['upstream_model'] is not None,
                      'upstream_model':r['upstream_model'],'rate':r['price'] or r['definition']['rate'],'custom_price':r['price'] is not None,
                      'trial_wallets':r['trial_wallets'],'settled_calls':r['settled_calls']} for r in rows]
            return {'items':items,'providers':[projected(r) for r in providers],'templates':public_templates()}

    async def provider_configs(self, principal):
        async with self._snapshot(principal) as conn:
            configs = await (await conn.execute("""SELECT DISTINCT ON (channel,platform,account) id,channel,platform,account,name,base_url,key_hint,options,created_at,
                min(created_at) OVER (PARTITION BY channel,platform,account) AS first_at
                FROM provider_configs ORDER BY channel,platform,account,created_at DESC,id DESC""")).fetchall()
            status = await (await conn.execute('SELECT channel,platform,account,config_id,state,models,voices,checked_at FROM provider_config_status')).fetchall()
            catalog = await (await conn.execute("SELECT kind,provider_id,base_url,capabilities FROM provider_catalog WHERE provider_id NOT LIKE 'console-%'")).fetchall()
        states = {(r['channel'], r['platform'], r['account']): r for r in status}
        def server_address(name):
            spec = CHANNELS[name]
            for row in catalog:
                if row['kind'] == spec['kind'] and row['base_url'] and (spec['kind'] == 'execution' or any(CAPABILITY_CHANNEL.get(c) == name for c in row['capabilities'])):
                    return row['base_url']
            return None
        def report(key, config_id):
            state = states.get(key)
            if state is None or state['config_id'] != config_id:
                return None
            return {'state': state['state'], 'models': state['models'], 'voices': state['voices'], 'checked_at': state['checked_at'].isoformat()}
        channels = []
        for name in CHANNELS:
            # A row without a key is a connection that was removed.
            rows = sorted((r for r in configs if r['channel'] == name and r['key_hint']), key=lambda r: (r['first_at'], r['id']))
            connections = [{'id': r['id'], 'platform': r['platform'], 'account': r['account'], 'name': r['name'], 'provider_id': provider_id(name, r['platform'], r['account']),
                            'base_url': r['base_url'], 'key_hint': r['key_hint'], 'options': r['options'], 'updated_at': r['created_at'].isoformat(),
                            'status': report((name, r['platform'], r['account']), r['id'])} for r in rows]
            address = server_address(name)
            overridden = any(r['platform'] == 'gateway' and not r['account'] for r in rows)
            channels.append({'channel': name, 'server_base_url': address, 'platforms': public_platforms(name), 'connections': connections,
                             'server': {'base_url': address, 'status': report((name, 'gateway', ''), 'server-file')} if address and not overridden else None})
        return {'channels': channels, 'sealing_ready': self.sealing_ready}

    async def providers(self, principal, limit=25, offset=0, q=''):
        page_args(limit,offset,q)
        async with self._snapshot(principal):
            items={}
            for (id,version,model),metadata in sorted(self.registry.items()):
                if q.lower() not in (id+' '+version).lower():
                    continue
                item=items.setdefault((id,version),{'id':id,'version':version,'configured':True,'readiness':'configured','models':[]})
                if model:
                    item['models'].append({'id':model,**metadata})
            rows=list(items.values())
            return {'items':rows[offset:offset+limit],'limit':limit,'offset':offset,'has_more':len(rows)>offset+limit}

    async def executions(self, principal, limit=25, offset=0, q=''):
        return await self._list(principal,"""SELECT j.id,j.model,j.product_id,j.billing_account_id,j.status,j.amount,j.actual,
            j.funding_mode,j.created_at,j.updated_at,r.input_tokens,r.output_tokens,j.model_definition->'rate'->>'version' AS rate_version
            FROM execution_jobs j LEFT JOIN execution_receipts r ON r.job_id=j.id
            WHERE %s='' OR strpos(lower(j.id||' '||j.model||' '||j.status),lower(%s))>0 ORDER BY j.created_at DESC,j.id DESC""",(q,q),limit,offset,q)

    @staticmethod
    def _audit(row):
        result=projected({key:row[key] for key in ('id','operation_id','actor_id','action','target_type','target_id','reason','key_hash','request_hash','created_at')})
        result['result_summary']={key:row['result_'+key] for key in ('enabled','name','version') if row['result_'+key] is not None}
        return result

    @staticmethod
    def _audit_sql():
        return """SELECT a.id,a.operation_id,a.actor_id,a.action,a.target_type,a.target_id,a.reason,a.key_hash,a.request_hash,a.created_at,
            CASE WHEN jsonb_typeof(o.result->'enabled')='boolean' THEN (o.result->>'enabled')::boolean END AS result_enabled,
            CASE WHEN jsonb_typeof(o.result->'name')='string' THEN o.result->>'name' END AS result_name,
            CASE WHEN jsonb_typeof(o.result->'version')='string' THEN o.result->>'version' END AS result_version
            FROM console_audit_events a JOIN console_operations o ON o.id=a.operation_id"""

    async def audits(self, principal, limit=25, offset=0, q=''):
        return await self._list(principal, self._audit_sql() + """
            WHERE %s='' OR strpos(lower(a.actor_id||' '||a.action||' '||a.target_id),lower(%s))>0 ORDER BY a.created_at DESC,a.id DESC""",(q,q),limit,offset,q,self._audit)
