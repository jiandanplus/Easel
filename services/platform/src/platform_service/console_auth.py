"""Independent OIDC BFF sessions and owner-reviewed Console administrator ACL."""
from dataclasses import dataclass, field
from datetime import timedelta
import base64
import hashlib
import hmac
import json
import re
import secrets
from urllib.parse import urlencode
from uuid import uuid4

from .contracts import OperatorIdentity
from .errors import PlatformError
from .identity.oidc import OIDC, digest

OPERATOR_PERMISSIONS = frozenset({"console:read","plans:publish","billing:publish","execution:publish","execution:review","users:manage"})
# Issuing points is admin-only (decisions 0011, 0012).
PERMISSIONS = OPERATOR_PERMISSIONS | {"owner:allowance","plans:issue"}
ROLES = {"admin": PERMISSIONS, "operator": OPERATOR_PERMISSIONS, "viewer": frozenset({"console:read"})}


@dataclass(frozen=True)
class AdminPrincipal:
    id: str
    role: str
    permissions: frozenset[str]
    csrf_token: str = field(repr=False)
    session_hash: str = field(repr=False)


def _opaque(value):
    return isinstance(value,str) and re.fullmatch(r"[A-Za-z0-9_-]{43}",value) is not None


def _id(value):
    if not isinstance(value,str) or not re.fullmatch(r"[a-f0-9]{32}",value):
        raise PlatformError(400,"console_invalid_request")
    return value


async def _owner(conn, operator):
    if not isinstance(operator,OperatorIdentity) or "console:bootstrap" not in operator.capabilities:
        raise PlatformError(403,"operator_capability_required")
    row=await (await conn.execute("""SELECT current_user=session_user AND n.nspowner=r.oid AS owner
        FROM pg_namespace n JOIN pg_roles r ON r.rolname=current_user WHERE n.nspname=current_schema()""")).fetchone()
    if not row or not row['owner']:
        raise PlatformError(403,"console_owner_required")


async def resolve_console_identity(conn,issuer,subject,*,for_update=False):
    """Resolve one exact canonical/alias namespace; ambiguity never grants ACL."""
    await conn.execute('SELECT lock_console_identity(%s,%s)',(issuer,subject))
    matches=await (await conn.execute('SELECT id FROM console_admins WHERE issuer=%s AND subject=%s UNION ALL SELECT admin_id id FROM console_identity_aliases WHERE issuer=%s AND subject=%s',
        (issuer,subject,issuer,subject))).fetchall()
    if len(matches)>1:raise PlatformError(409,'console_acl_conflict')
    if not matches:return None
    query='SELECT * FROM console_admins WHERE id=%s'+(' FOR UPDATE' if for_update else '')
    return await (await conn.execute(query,(matches[0]['id'],))).fetchone()


class ConsoleAuth:
    def __init__(self,db,settings,http_client=None):
        self.db,self.settings=db,settings
        self.oidc=OIDC(settings.oidc_settings(),http_client)

    async def begin_login(self):
        config=await self.oidc.discovery()
        state,nonce,cookie=(secrets.token_urlsafe(32) for _ in range(3))
        async with self.db.connection() as c:
            row=await (await c.execute("SELECT clock_timestamp() AS now")).fetchone()
            end=row['now']+timedelta(seconds=self.settings.auth_ttl)
            await c.execute("INSERT INTO console_login_transactions(id,state_hash,nonce_hash,cookie_hash,expires_at) VALUES (%s,%s,%s,%s,%s)",
                (uuid4().hex,digest(state),digest(nonce),digest(cookie),end))
        params={"client_id":self.settings.client_id,"redirect_uri":self.settings.public_url+'/oidc/console/callback',
            "response_type":"code","scope":"openid profile email","state":state,"nonce":nonce,
            "code_challenge":base64.urlsafe_b64encode(hashlib.sha256(cookie.encode()).digest()).rstrip(b'=').decode(),
            "code_challenge_method":"S256","prompt":"login","max_age":"0"}
        return {"url":config['authorization_endpoint']+'?'+urlencode(params),"cookie":cookie,"expires_at":end.isoformat()}

    async def callback(self,code,state,cookie):
        if (not isinstance(code,str) or not 1<=len(code)<=4096 or not _opaque(state) or not _opaque(cookie)):
            raise PlatformError(400,"console_login_invalid")
        async with self.db.connection() as c:
            tx=await (await c.execute("SELECT *,expires_at>clock_timestamp() AS valid FROM console_login_transactions WHERE state_hash=%s FOR UPDATE",(digest(state),))).fetchone()
            if not tx or not tx['valid'] or tx['consumed'] or not hmac.compare_digest(tx['cookie_hash'],digest(cookie)):
                raise PlatformError(400,"console_login_invalid")
            await c.execute("UPDATE console_login_transactions SET consumed=true WHERE id=%s",(tx['id'],))
        verified=await self.oidc.exchange('console',code,cookie,tx['nonce_hash'],require_auth_time=True)
        async with self.db.connection() as c:
            now=(await (await c.execute("SELECT clock_timestamp() AS now")).fetchone())['now']
            age=now.timestamp()-verified.authenticated_at
            if age>self.settings.auth_ttl+60 or age < -60:
                raise PlatformError(401,"console_authentication_stale")
            admin=await resolve_console_identity(c,verified.issuer,verified.subject)
            if not admin or not admin['active']:
                request_id=uuid4().hex
                subject_hash=digest(json.dumps([verified.issuer,verified.subject],separators=(',',':')))
                await c.execute("INSERT INTO console_login_requests(id,issuer,subject,subject_hash,expires_at) VALUES (%s,%s,%s,%s,%s)",
                    (request_id,verified.issuer,verified.subject,subject_hash,now+timedelta(days=1)))
                return {"status":"pending","request_id":request_id}
            session_token,csrf_token=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
            expires=now+timedelta(seconds=self.settings.session_ttl)
            await c.execute("INSERT INTO console_sessions(session_hash,admin_id,csrf_token,authorization_epoch,created_at,expires_at) VALUES (%s,%s,%s,%s,%s,%s)",
                (digest(session_token),admin['id'],csrf_token,admin['authorization_epoch'],now,expires))
            return {"status":"authenticated","session_token":session_token,"csrf_token":csrf_token,"expires_at":expires.isoformat()}

    async def _session(self,c,session_hash):
        readonly=(await (await c.execute('SHOW transaction_read_only')).fetchone())['transaction_read_only']=='on'
        query="""SELECT a.id,a.role,s.csrf_token,s.session_hash FROM console_sessions s JOIN console_admins a ON a.id=s.admin_id
            WHERE s.session_hash=%s AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp()
            AND a.active AND a.authorization_epoch=s.authorization_epoch"""
        if not readonly:
            await c.execute('SELECT pg_advisory_xact_lock_shared(hashtextextended(%s,0))',('console-session:'+session_hash,))
        row=await (await c.execute(query,(session_hash,))).fetchone()
        if row and not readonly:
            await c.execute('SELECT pg_advisory_xact_lock_shared(hashtextextended(%s,0))',('console-admin:'+row['id'],))
            row=await (await c.execute(query,(session_hash,))).fetchone()
        if not row:raise PlatformError(401,"console_session_invalid")
        return AdminPrincipal(row['id'],row['role'],ROLES[row['role']],row['csrf_token'],row['session_hash'])

    async def authenticate(self,cookie):
        if not _opaque(cookie):raise PlatformError(401,"console_session_invalid")
        async with self.db.connection() as c:return await self._session(c,digest(cookie))

    async def validate(self,conn,principal,permission):
        if not isinstance(principal,AdminPrincipal):raise PlatformError(401,"console_session_invalid")
        current=await self._session(conn,principal.session_hash)
        if current.id!=principal.id or not hmac.compare_digest(current.csrf_token,principal.csrf_token):
            raise PlatformError(401,"console_session_invalid")
        if permission not in current.permissions:raise PlatformError(403,"console_permission_denied")
        return current

    async def logout(self,cookie):
        if not _opaque(cookie):raise PlatformError(401,"console_session_invalid")
        async with self.db.connection() as c:
            await c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('console-session:'+digest(cookie),))
            await c.execute('UPDATE console_sessions SET revoked_at=clock_timestamp() WHERE session_hash=%s AND revoked_at IS NULL',(digest(cookie),))

    async def list_requests(self,operator):
        async with self.db.connection() as c:
            await _owner(c,operator)
            rows=await (await c.execute("SELECT id,status,subject_hash,verified_at,expires_at FROM console_login_requests WHERE status='pending' AND expires_at>clock_timestamp() ORDER BY verified_at DESC,id LIMIT 100")).fetchall()
            return [{k:v.isoformat() if hasattr(v,'isoformat') else v for k,v in row.items()} for row in rows]

    async def approve_request(self,request_id,role,operator):
        _id(request_id)
        if role not in ROLES:raise PlatformError(400,"console_invalid_role")
        async with self.db.connection() as c:
            await _owner(c,operator)
            request=await (await c.execute('SELECT *,expires_at>clock_timestamp() AS valid FROM console_login_requests WHERE id=%s FOR UPDATE',(request_id,))).fetchone()
            if not request or not request['valid'] or request['issuer']!=self.settings.issuer:
                raise PlatformError(400,"console_request_invalid")
            admin=await resolve_console_identity(c,request['issuer'],request['subject'],for_update=True)
            if admin:
                if not admin['active'] or admin['role']!=role:raise PlatformError(409,'console_acl_conflict')
            else:
                admin=await (await c.execute('INSERT INTO console_admins(id,issuer,subject,role,created_by) VALUES (%s,%s,%s,%s,%s) RETURNING *',
                    (uuid4().hex,request['issuer'],request['subject'],role,operator.id))).fetchone()
            if request['status']=='pending':
                await c.execute("UPDATE console_login_requests SET status='approved',approved_admin_id=%s WHERE id=%s",(admin['id'],request_id))
                await c.execute("INSERT INTO console_acl_events(id,admin_id,request_id,action,role,operator_id) VALUES (%s,%s,%s,'approve',%s,%s)",(uuid4().hex,admin['id'],request_id,role,operator.id))
            elif request['approved_admin_id']!=admin['id']:raise PlatformError(409,'console_acl_conflict')
            return {'id':admin['id'],'request_id':request_id,'role':admin['role'],'active':admin['active'],'subject_hash':request['subject_hash']}

    async def disable_admin(self,admin_id,operator):
        _id(admin_id)
        async with self.db.connection() as c:
            await _owner(c,operator)
            await c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('console-admin:'+admin_id,))
            admin=await (await c.execute('SELECT * FROM console_admins WHERE id=%s FOR UPDATE',(admin_id,))).fetchone()
            if not admin:raise PlatformError(404,'console_admin_not_found')
            if admin['active']:
                await c.execute('UPDATE console_admins SET active=false,authorization_epoch=authorization_epoch+1 WHERE id=%s',(admin_id,))
                await c.execute("INSERT INTO console_acl_events(id,admin_id,action,role,operator_id) VALUES (%s,%s,'disable',%s,%s)",(uuid4().hex,admin_id,admin['role'],operator.id))
            return {'id':admin_id,'active':False}
