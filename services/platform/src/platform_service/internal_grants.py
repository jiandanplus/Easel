"""Owner-only fixed internal experience allowance. No HTTP issuance surface."""
from dataclasses import dataclass
from datetime import timedelta
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit
from uuid import uuid4

from psycopg.types.json import Jsonb

from .credits import MAX_CREDITS, bounded, journal
from .errors import PlatformError
from .membership import require

PLAN = {
    "id": "easel-internal-experience-v1", "name": "内部体验额度，非付款", "version": "1",
    "included_products": ["easel"], "features": ["text"], "credits": "10000000",
    "days": 7, "concurrency": 1, "test_only": False,
}
_FIELDS = {"schema_version", "enabled", "environment_id", "public_url", "database_host",
           "database_port", "database_name", "db_schema", "issuer", "product_id",
           "credits", "days", "concurrency", "features"}


def _local_origin(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "http" or not ipaddress.ip_address(parsed.hostname).is_loopback
            or parsed.username is not None or parsed.password is not None
            or parsed.path or parsed.query or parsed.fragment or not parsed.port
            or value != parsed.scheme + "://" + parsed.netloc):
        raise ValueError()


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


@dataclass(frozen=True)
class InternalGrantPolicy:
    environment_id: str
    public_url: str
    database_host: str
    database_port: int
    database_name: str
    db_schema: str
    issuer: str
    sha256: str

    @classmethod
    def from_env(cls, settings, env=None):
        env = os.environ if env is None else env
        filename = env.get("CENTRAL_INTERNAL_GRANT_POLICY_FILE")
        if not filename:
            raise PlatformError(503, "internal_grants_disabled")
        try:
            path = Path(filename)
            if not path.is_absolute() or path.resolve(strict=True) != path:
                raise ValueError()
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.geteuid():
                    raise ValueError()
                raw = source.read(16385)
            if len(raw) > 16384:
                raise ValueError()
            data = json.loads(raw, object_pairs_hook=_unique_pairs)
            if (not isinstance(data, dict) or set(data) != _FIELDS
                    or type(data["schema_version"]) is not int or data["schema_version"] != 1
                    or data["enabled"] is not True or data["product_id"] != "easel"
                    or data["credits"] != "10000000" or type(data["days"]) is not int or data["days"] != 7
                    or type(data["concurrency"]) is not int or data["concurrency"] != 1
                    or data["features"] != ["text"]
                    or not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", data["environment_id"])
                    or not ipaddress.ip_address(data["database_host"]).is_loopback
                    or type(data["database_port"]) is not int or not 1 <= data["database_port"] <= 65535
                    or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", data["database_name"])
                    or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", data["db_schema"])):
                raise ValueError()
            _local_origin(data["public_url"])
            if (not settings.allow_loopback_http or data["public_url"] != settings.public_url
                    or data["issuer"] != settings.issuer or not settings.issuer
                    or data["db_schema"] != settings.db_schema or "easel" not in settings.oidc_clients):
                raise ValueError()
            digest = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            return cls(**{key: data[key] for key in cls.__dataclass_fields__ if key != "sha256"}, sha256=digest)
        except (OSError, ValueError, TypeError, KeyError):
            raise PlatformError(503, "invalid_internal_grant_policy") from None


def grant_view(row):
    return {"id": row["id"], "membership_id": row["membership_id"],
            "billing_account_id": row["billing_account_id"], "plan_id": PLAN["id"],
            "credits": "10000000", "starts_at": row["starts_at"].isoformat(),
            "expires_at": row["expires_at"].isoformat(), "test_only": False,
            "funding_source": "internal_experience", "notice": PLAN["name"]}


class InternalGrantService:
    def __init__(self, db, settings, policy):
        self.db, self.settings, self.policy = db, settings, policy

    async def issue(self, operator, *, environment, wallet, session, key, reason):
        require(operator, "internal:issue")
        for value in (wallet, session, key, reason, operator.id):
            bounded(value)
            if any(ord(char) < 32 or ord(char) == 127 for char in value):
                raise PlatformError(422, "invalid_internal_grant_parameter")
        policy = self.policy
        if (not isinstance(policy, InternalGrantPolicy) or environment != policy.environment_id
                or policy.public_url != self.settings.public_url or policy.db_schema != self.settings.db_schema
                or policy.issuer != self.settings.issuer or not self.settings.allow_loopback_http):
            raise PlatformError(403, "internal_environment_mismatch")
        async with self.db.connection() as c:
            owner = await (await c.execute("""SELECT current_user=session_user AND n.nspowner=r.oid AS owner,
                current_database() AS database_name,current_schema() AS schema
                FROM pg_namespace n JOIN pg_roles r ON r.rolname=current_user
                WHERE n.nspname=current_schema()""")).fetchone()
            if not owner or not owner["owner"]:
                raise PlatformError(403, "internal_owner_required")
            if ((owner["database_name"], owner["schema"], c.info.host, c.info.port)
                    != (policy.database_name, policy.db_schema, policy.database_host, policy.database_port)):
                raise PlatformError(403, "internal_environment_mismatch")
            # Source keys are environment-wide, independent of operator identity.
            await c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                            (json.dumps(["internal-grant", environment, key]),))
            target = await (await c.execute("SELECT id FROM billing_accounts WHERE id=%s FOR UPDATE", (wallet,))).fetchone()
            if not target:
                raise PlatformError(403, "internal_session_required")
            # Lock identity and authority rows against a concurrent revocation.
            identity = await (await c.execute("""SELECT s.* FROM identity_sessions s
                JOIN users u ON u.id=s.user_id AND u.active AND u.authorization_epoch=s.authorization_epoch
                JOIN tenant_members m ON m.tenant_id=s.tenant_id AND m.user_id=s.user_id AND m.active
                JOIN billing_accounts b ON b.id=s.billing_account_id AND b.tenant_id=s.tenant_id
                JOIN tenants t ON t.id=s.tenant_id AND t.owner_user_id=s.user_id
                JOIN products p ON p.id=s.product_id AND p.enabled
                JOIN product_clients pc ON pc.product_id=s.product_id AND pc.client_id=s.client_id AND pc.enabled
                JOIN external_identities e ON e.issuer=s.issuer AND e.subject=s.subject AND e.user_id=s.user_id
                JOIN identity_transactions tx ON tx.id=s.source_transaction_id AND tx.product_id=s.product_id
                    AND tx.client_id=s.client_id AND tx.consumed AND tx.state_hash IS NOT NULL
                    AND tx.nonce_hash IS NOT NULL AND tx.cookie_hash IS NOT NULL
                JOIN identity_handoffs h ON h.transaction_id=tx.id AND h.consumed
                    AND (h.user_id,h.tenant_id,h.billing_account_id,h.issuer,h.subject)=
                        (s.user_id,s.tenant_id,s.billing_account_id,s.issuer,s.subject)
                WHERE s.id=%s AND s.billing_account_id=%s AND s.issuer=%s AND s.product_id='easel'
                    AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp()
                    AND EXISTS(SELECT 1 FROM identity_tokens tk WHERE tk.session_id=s.id AND tk.kind='refresh'
                        AND NOT tk.consumed AND tk.expires_at>clock_timestamp())
                FOR SHARE OF s,u,m,t,p,pc,e,tx,h""", (session, wallet, policy.issuer))).fetchone()
            if not identity:
                raise PlatformError(403, "internal_session_required")
            old = await (await c.execute("SELECT * FROM internal_grants WHERE environment_id=%s AND (issuance_key=%s OR billing_account_id=%s)",
                                        (environment,key,wallet))).fetchone()
            if old:
                if (old["billing_account_id"],old["session_id"],old["issuance_key"],old["reason"],old["policy_sha256"]) != (wallet,session,key,reason,policy.sha256):
                    raise PlatformError(409, "idempotency_conflict")
                return grant_view(old)
            await c.execute("INSERT INTO plans(id,name,version,definition,created_by) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                            (PLAN['id'],PLAN['name'],PLAN['version'],Jsonb(PLAN),operator.id))
            plan = await (await c.execute("SELECT definition FROM plans WHERE id=%s",(PLAN['id'],))).fetchone()
            if not plan or plan['definition'] != PLAN:
                raise PlatformError(409,"plan_version_immutable")
            total = await (await c.execute("SELECT coalesce(sum(issued),0) AS n FROM credit_lots WHERE billing_account_id=%s",(wallet,))).fetchone()
            if total['n'] + 10000000 > MAX_CREDITS:
                raise PlatformError(409,"wallet_capacity_exceeded")
            now = (await (await c.execute("SELECT clock_timestamp() AS now")).fetchone())['now']
            end = now + timedelta(days=7)
            mid, lid, gid = str(uuid4()), str(uuid4()), str(uuid4())
            source = "internal-experience:" + hashlib.sha256(json.dumps([environment,key]).encode()).hexdigest()
            await c.execute("INSERT INTO memberships(id,billing_account_id,plan_id,operator_id,issuance_key,starts_at,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                            (mid,wallet,PLAN['id'],operator.id,source,now,end))
            await c.execute("INSERT INTO credit_lots(id,billing_account_id,membership_id,issued,available,expires_at,test_only) VALUES (%s,%s,%s,10000000,10000000,%s,false)",
                            (lid,wallet,mid,end))
            await journal(c,wallet,'issue',mid,{'issuance':-10000000,'available':10000000},operator.id)
            row = await (await c.execute("""INSERT INTO internal_grants(id,environment_id,billing_account_id,user_id,tenant_id,
                session_id,issuer,source_transaction_id,membership_id,lot_id,operator_id,issuance_key,reason,policy_sha256,source,starts_at,expires_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (gid,environment,wallet,identity['user_id'],identity['tenant_id'],session,policy.issuer,
                 identity['source_transaction_id'],mid,lid,operator.id,key,reason,policy.sha256,source,now,end))).fetchone()
            return grant_view(row)
