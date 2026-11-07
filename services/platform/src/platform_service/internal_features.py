"""Owner-operated, non-financial extensions of an existing internal grant."""
from uuid import uuid4
from .contracts import OperatorIdentity
from .credits import active_plans, bounded
from .errors import PlatformError


async def feature_memberships(c, scope, funding_mode, feature, *, memberships=None):
    plans = await active_plans(c, scope, funding_mode) if memberships is None else memberships
    selected = [p for p in plans if feature in p['definition']['features']]
    if feature != 'image' or funding_mode == 'test':
        return selected
    extended = await (await c.execute("""SELECT g.membership_id FROM internal_feature_extensions e
        JOIN internal_grants g ON g.id=e.grant_id
        JOIN tenants t ON t.id=e.tenant_id AND t.owner_user_id=e.user_id
        WHERE e.user_id=%s AND e.tenant_id=%s AND e.billing_account_id=%s
          AND e.product_id=%s AND e.feature='image'
          AND e.starts_at<=clock_timestamp() AND e.expires_at>clock_timestamp()""",
        (scope.user_id,scope.tenant_id,scope.billing_account_id,scope.product_id))).fetchall()
    identifiers = {row['membership_id'] for row in extended}
    return [p for p in plans if p in selected or (not p['definition']['test_only'] and p['id'] in identifiers)]


async def entitled_features(c, scope, memberships):
    features = {f for p in memberships for f in p['definition']['features']}
    if await feature_memberships(c,scope,None,'image',memberships=memberships):
        features.add('image')
    return sorted(features)


def view(row):
    return {key:(value.isoformat() if hasattr(value,'isoformat') else value) for key,value in row.items()}


class InternalFeatureService:
    """No HTTP route and no runtime INSERT privilege. Never issues credit."""
    def __init__(self, db):
        self.db = db

    async def extend(self, operator, *, environment, grant_id, product, feature, key, reason):
        if not isinstance(operator,OperatorIdentity) or 'internal:extend_features' not in operator.capabilities:
            raise PlatformError(403,'operator_capability_required')
        for value in (environment,grant_id,product,feature,key,reason,operator.id):
            bounded(value)
            if any(ord(char)<32 or ord(char)==127 for char in value):
                raise PlatformError(422,'invalid_internal_feature_parameter')
        if product!='easel' or feature!='image':
            raise PlatformError(403,'internal_feature_scope_denied')
        async with self.db.connection() as c:
            owner=await (await c.execute("""SELECT current_user=session_user AND n.nspowner=r.oid AS owner
                FROM pg_namespace n JOIN pg_roles r ON r.rolname=current_user
                WHERE n.nspname=current_schema()""")).fetchone()
            if not owner or not owner['owner']:
                raise PlatformError(403,'internal_owner_required')
            await c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                ('internal-feature:'+environment+':'+key,))
            grant=await (await c.execute('SELECT * FROM internal_grants WHERE id=%s AND environment_id=%s',(grant_id,environment))).fetchone()
            if not grant:raise PlatformError(403,'internal_grant_required')
            await c.execute('SELECT id FROM billing_accounts WHERE id=%s FOR UPDATE',(grant['billing_account_id'],))
            old=await (await c.execute("""SELECT * FROM internal_feature_extensions WHERE environment_id=%s
                AND (issuance_key=%s OR (grant_id=%s AND product_id=%s AND feature=%s))""",
                (environment,key,grant_id,product,feature))).fetchall()
            if old:
                if len(old)!=1 or (old[0]['grant_id'],old[0]['product_id'],old[0]['feature'],old[0]['issuance_key'],old[0]['reason'])!=(grant_id,product,feature,key,reason):
                    raise PlatformError(409,'idempotency_conflict')
                return view(old[0])
            current=await (await c.execute("""SELECT g.id FROM internal_grants g
                JOIN users u ON u.id=g.user_id AND u.active
                JOIN tenants t ON t.id=g.tenant_id AND t.owner_user_id=g.user_id
                JOIN tenant_members tm ON tm.user_id=g.user_id AND tm.tenant_id=g.tenant_id AND tm.active
                JOIN products p ON p.id=%s AND p.enabled
                JOIN memberships m ON m.id=g.membership_id AND m.billing_account_id=g.billing_account_id
                WHERE g.id=%s AND g.starts_at<=clock_timestamp() AND g.expires_at>clock_timestamp()
                  AND NOT EXISTS(SELECT 1 FROM membership_revocations r WHERE r.membership_id=m.id)
                FOR SHARE OF u,t,tm,p,m""",(product,grant_id))).fetchone()
            if not current:raise PlatformError(403,'internal_grant_inactive')
            row=await (await c.execute("""INSERT INTO internal_feature_extensions(
                id,grant_id,environment_id,user_id,tenant_id,billing_account_id,product_id,feature,
                starts_at,expires_at,operator_id,issuance_key,reason)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (str(uuid4()),grant_id,environment,grant['user_id'],grant['tenant_id'],grant['billing_account_id'],product,feature,
                 grant['starts_at'],grant['expires_at'],operator.id,key,reason))).fetchone()
            return view(row)
