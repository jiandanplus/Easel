"""Owner-only, evidence-bound same-subject identity continuity. No network or cutover."""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
import asyncio
import hashlib
import ipaddress
import json
import re
from urllib.parse import urlsplit
from uuid import uuid4

from psycopg import IntegrityError
from psycopg.errors import LockNotAvailable, QueryCanceled
from psycopg.types.json import Jsonb

from .contracts import OperatorIdentity
from .errors import PlatformError

MAX_IDS = 100000
MAX_CANDIDATES = 10000
STATEMENT_TIMEOUT_MS = 30000
LOCK_TIMEOUT_MS = 2000
TRANSACTION_TIMEOUT_SECONDS = 180


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _text(value,limit=255):
    return isinstance(value,str) and 1<=len(value)<=limit and value.strip()==value and not any(ord(c)<32 or ord(c)==127 for c in value)


def _sha(value):
    return isinstance(value,str) and re.fullmatch(r'[a-f0-9]{64}',value) is not None


def _fail(code='identity_migration_invalid',status=400):
    raise PlatformError(status,code)


def _issuer(value,source=False):
    try:
        if not _text(value,2048) or not value.isascii() or any(c in value for c in '\\%?#@'):
            raise ValueError()
        p=urlsplit(value)
        if p.path!='/oidc' or not p.hostname or p.username or p.password or p.query or p.fragment:
            raise ValueError()
        if source and p.scheme=='http':
            if not ipaddress.ip_address(p.hostname).is_loopback or p.port is None:raise ValueError()
        else:
            if p.scheme!='https' or p.port not in (None,443):raise ValueError()
            labels=p.hostname.split('.')
            if len(labels)<2 or labels[-1].isdigit() or re.fullmatch(r'0x[0-9a-f]+',labels[-1],re.I):raise ValueError()
            if not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',part) for part in labels):raise ValueError()
    except (ValueError,TypeError):
        _fail('identity_migration_policy_invalid')


@dataclass(frozen=True)
class MigrationPolicy:
    environment: str
    source_issuer: str
    target_issuer: str
    tenant_id: str
    client_ids: tuple[str,...]
    approved_backup_sha256: str

    def __post_init__(self):
        if (not _text(self.environment) or not re.fullmatch(r'[A-Za-z0-9_-]+',self.environment)
                or not _text(self.tenant_id) or not _sha(self.approved_backup_sha256)
                or not isinstance(self.client_ids,tuple) or not 1<=len(self.client_ids)<=1000
                or not all(_text(c) for c in self.client_ids) or len(set(self.client_ids))!=len(self.client_ids)
                or self.source_issuer==self.target_issuer):
            _fail('identity_migration_policy_invalid')
        _issuer(self.source_issuer,True);_issuer(self.target_issuer)
        object.__setattr__(self,'client_ids',tuple(sorted(self.client_ids)))

    @property
    def sha256(self):
        return _hash(asdict(self))


@dataclass(frozen=True)
class VerifiedRestoreEvidence:
    policy_sha256: str
    backup_sha256: str
    schema_sha256: str
    data_sha256: str
    source_context_sha256: str
    target_context_sha256: str
    tenant_id: str
    user_ids: tuple[str,...] = field(repr=False)
    client_ids: tuple[str,...] = field(repr=False)
    verified_at: datetime


async def _owner(conn,operator,capability):
    if not isinstance(operator,OperatorIdentity) or capability not in operator.capabilities or not _text(operator.id):
        _fail('operator_capability_required',403)
    row=await (await conn.execute("""SELECT current_user=session_user AND n.nspowner=r.oid AS owner
        FROM pg_namespace n JOIN pg_roles r ON r.rolname=current_user WHERE n.nspname=current_schema()""")).fetchone()
    if not row or not row['owner']:_fail('identity_owner_required',403)


def _proof_view(row):
    counts={kind:sum(c['kind']==kind for c in row['candidates']) for kind in ('product','console')}
    return dict(proof_id=row['id'],expires_at=row['expires_at'].isoformat(),product_count=counts['product'],
        console_count=counts['console'],candidate_sha256=row['candidate_sha256'])


class IdentityMigrationService:
    def __init__(self,db,policy):
        if not isinstance(policy,MigrationPolicy):_fail('identity_migration_policy_invalid')
        self.db,self.policy=db,policy

    @asynccontextmanager
    async def _transaction(self,*,readonly=False):
        try:
            async with asyncio.timeout(TRANSACTION_TIMEOUT_SECONDS):
                async with self.db.connection() as c:
                    if readonly:await c.execute('SET TRANSACTION READ ONLY')
                    await c.execute("SELECT set_config('statement_timeout',%s,true)",(str(STATEMENT_TIMEOUT_MS),))
                    await c.execute("SELECT set_config('lock_timeout',%s,true)",(str(LOCK_TIMEOUT_MS),))
                    yield c
        except (TimeoutError,LockNotAvailable,QueryCanceled):
            raise PlatformError(503,'identity_migration_timeout',retryable=True) from None

    def _evidence(self,evidence):
        if not isinstance(evidence,VerifiedRestoreEvidence):_fail('identity_restore_evidence_invalid')
        if (not all(_sha(getattr(evidence,name)) for name in ('policy_sha256','backup_sha256','schema_sha256',
                'data_sha256','source_context_sha256','target_context_sha256'))
                or evidence.policy_sha256!=self.policy.sha256 or evidence.backup_sha256!=self.policy.approved_backup_sha256
                or evidence.tenant_id!=self.policy.tenant_id or evidence.source_context_sha256==evidence.target_context_sha256):
            _fail('identity_restore_evidence_invalid')
        for ids in (evidence.user_ids,evidence.client_ids):
            if (not isinstance(ids,tuple) or not 1<=len(ids)<=MAX_IDS
                    or not all(_text(item) for item in ids) or len(set(ids))!=len(ids)):
                _fail('identity_restore_evidence_invalid')
        if not set(self.policy.client_ids)<=set(evidence.client_ids):_fail('identity_restore_evidence_invalid')
        at=evidence.verified_at
        if not isinstance(at,datetime) or at.tzinfo is None or at.utcoffset()!=timedelta(0):_fail('identity_restore_evidence_invalid')
        age=(datetime.now(timezone.utc)-at).total_seconds()
        if not -60<=age<=300:_fail('identity_restore_evidence_expired')
        data=asdict(evidence)
        data.update(user_ids=sorted(evidence.user_ids),client_ids=sorted(evidence.client_ids),verified_at=at.isoformat(),
            source_issuer=self.policy.source_issuer,target_issuer=self.policy.target_issuer)
        return data

    async def _candidates(self,c):
        products=await (await c.execute("""WITH source AS MATERIALIZED (
            SELECT e.subject,e.user_id,e.email,e.email_verified,u.active,u.authorization_epoch
            FROM external_identities e JOIN users u ON u.id=e.user_id WHERE e.issuer=%s ORDER BY e.subject LIMIT 10001
        ), owners AS (
            SELECT t.owner_user_id,min(t.id) tenant_id,min(b.id) billing_account_id,count(*) owner_count
            FROM tenants t JOIN billing_accounts b ON b.tenant_id=t.id
            WHERE t.owner_user_id IN (SELECT user_id FROM source) GROUP BY t.owner_user_id
        ) SELECT s.*,o.tenant_id,o.billing_account_id,o.owner_count FROM source s
          LEFT JOIN owners o ON o.owner_user_id=s.user_id ORDER BY s.subject""",
            (self.policy.source_issuer,))).fetchall()
        admins=await (await c.execute('SELECT id,subject,role,active,authorization_epoch FROM console_admins WHERE issuer=%s ORDER BY subject LIMIT 10001',
            (self.policy.source_issuer,))).fetchall()
        if not 1<=len(products)+len(admins)<=MAX_CANDIDATES:_fail('identity_candidates_invalid',409)
        result=[]
        for row in products:
            if row.pop('owner_count')!=1:_fail('identity_owner_wallet_ambiguous',409)
            result.append(dict(kind='product',**row))
        result.extend(dict(kind='console',admin_id=row['id'],**{k:v for k,v in row.items() if k!='id'}) for row in admins)
        return result

    async def record_proof(self,evidence,operator):
        async with self._transaction() as c:
            await _owner(c,operator,'identity:collect')
            data=self._evidence(evidence)
            candidates=await self._candidates(c)
            subjects=set(evidence.user_ids)
            if any(row['subject'] not in subjects for row in candidates):_fail('identity_restore_subject_missing',409)
            now=(await (await c.execute('SELECT clock_timestamp() now')).fetchone())['now']
            row=await (await c.execute('INSERT INTO identity_restore_proofs(id,policy_sha256,evidence,candidates,candidate_sha256,operator_id,created_at,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',
                (uuid4().hex,self.policy.sha256,Jsonb(data),Jsonb(candidates),_hash(candidates),operator.id,now,now+timedelta(hours=1)))).fetchone()
            return _proof_view(row)

    async def _proof(self,c,proof_id):
        if not isinstance(proof_id,str) or not re.fullmatch(r'[a-f0-9]{32}',proof_id):_fail()
        row=await (await c.execute('SELECT *,expires_at>clock_timestamp() valid FROM identity_restore_proofs WHERE id=%s',(proof_id,))).fetchone()
        if not row or row['policy_sha256']!=self.policy.sha256 or not row['valid']:_fail('identity_restore_proof_invalid',409)
        if _hash(row['candidates'])!=row['candidate_sha256']:_fail('identity_restore_proof_invalid',409)
        return row

    async def _conflicts(self,c,proof):
        try:current=await self._candidates(c)
        except PlatformError:return 1
        count=int(current!=proof['candidates'])
        for row in proof['candidates']:
            if row['kind']=='product':
                found=await (await c.execute('SELECT user_id FROM external_identities WHERE issuer=%s AND subject=%s',
                    (self.policy.target_issuer,row['subject']))).fetchone()
            else:
                found=await (await c.execute('SELECT id FROM console_admins WHERE issuer=%s AND subject=%s UNION ALL SELECT admin_id id FROM console_identity_aliases WHERE issuer=%s AND subject=%s',
                    (self.policy.target_issuer,row['subject'],self.policy.target_issuer,row['subject']))).fetchone()
            count+=int(found is not None)
        return count

    async def inspect(self,proof_id,operator):
        async with self._transaction(readonly=True) as c:
            await _owner(c,operator,'identity:migrate')
            proof=await self._proof(c,proof_id)
            consumed=await (await c.execute('SELECT id FROM identity_migrations WHERE proof_id=%s',(proof_id,))).fetchone()
            return dict(**_proof_view(proof),conflict_count=await self._conflicts(c,proof),consumed=bool(consumed))

    async def apply(self,proof_id,key,reason,operator):
        if not _text(key,255) or not _text(reason,1000):_fail()
        request_hash=_hash(dict(policy_sha256=self.policy.sha256,proof_id=proof_id,key=key,reason=reason))
        try:
            async with self._transaction() as c:
                await _owner(c,operator,'identity:migrate')
                # One short owner operation serializes proof consumption and key replay.
                await c.execute("SELECT pg_advisory_xact_lock(hashtextextended('identity-migration-owner',0))")
                old=await (await c.execute('SELECT * FROM identity_migrations WHERE environment=%s AND key_hash=%s',
                    (self.policy.environment,_hash(key)))).fetchone()
                if old:
                    if old['request_hash']!=request_hash:_fail('identity_migration_key_conflict',409)
                    proof=await (await c.execute('SELECT * FROM identity_restore_proofs WHERE id=%s',(old['proof_id'],))).fetchone()
                    return self._receipt(old,proof,True)
                proof=await self._proof(c,proof_id)
                if await (await c.execute('SELECT id FROM identity_migrations WHERE proof_id=%s',(proof_id,))).fetchone():
                    _fail('identity_restore_proof_consumed',409)
                # Lock bounded distinct stripes in a common order, shared with login.
                stripes=await (await c.execute("SELECT DISTINCT value->>'kind' AS kind,identity_lock_stripe(%s,value->>'subject') AS stripe FROM jsonb_array_elements(%s::jsonb) ORDER BY kind,stripe",
                    (self.policy.target_issuer,Jsonb(proof['candidates'])))).fetchall()
                for row in stripes:await c.execute('SELECT lock_identity_stripe(%s,%s)',(row['kind'],row['stripe']))
                if await self._conflicts(c,proof):_fail('identity_migration_conflict',409)
                mid=uuid4().hex
                result_sha256=_hash(dict(proof_id=proof_id,candidate_sha256=proof['candidate_sha256'],policy_sha256=self.policy.sha256))
                batch=await (await c.execute('INSERT INTO identity_migrations(id,proof_id,environment,key_hash,request_hash,operator_id,reason,result_sha256) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',
                    (mid,proof_id,self.policy.environment,_hash(key),request_hash,operator.id,reason,result_sha256))).fetchone()
                for row in proof['candidates']:
                    entry_id=uuid4().hex
                    await c.execute('INSERT INTO identity_migration_entries(id,migration_id,kind,subject,candidate) VALUES (%s,%s,%s,%s,%s)',
                        (entry_id,mid,row['kind'],row['subject'],Jsonb(row)))
                    if row['kind']=='product':
                        await c.execute('INSERT INTO external_identities(issuer,subject,user_id,email,email_verified) VALUES (%s,%s,%s,%s,%s)',
                            (self.policy.target_issuer,row['subject'],row['user_id'],row['email'],row['email_verified']))
                    else:
                        await c.execute('INSERT INTO console_identity_aliases(issuer,subject,admin_id,migration_entry_id) VALUES (%s,%s,%s,%s)',
                            (self.policy.target_issuer,row['subject'],row['admin_id'],entry_id))
                return self._receipt(batch,proof,False)
        except IntegrityError:
            _fail('identity_migration_conflict',409)

    @staticmethod
    def _receipt(batch,proof,replayed):
        view=_proof_view(proof)
        return dict(migration_id=batch['id'],proof_id=proof['id'],product_count=view['product_count'],
            console_count=view['console_count'],result_sha256=batch['result_sha256'],replayed=replayed)
