"""Central identity broker. Opaque credentials are persisted only as SHA-256 hashes."""

import base64
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import re
import secrets
from urllib.parse import urlencode, urlsplit

from ..contracts import Scope
from ..errors import PlatformError
from .oidc import OIDC, digest


def now():
    return datetime.now(timezone.utc)


def opaque():
    return secrets.token_urlsafe(32)


def challenge(value):
    return (
        base64.urlsafe_b64encode(hashlib.sha256(value.encode()).digest())
        .rstrip(b"=")
        .decode()
    )


def bounded(value, limit=255):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise PlatformError(400, "invalid_request")
    return value


def callback_uri(value):
    bounded(value, 2048)
    if "\\" in value or any(ord(char) < 33 or ord(char) == 127 for char in value):
        raise PlatformError(400, "invalid_callback_uri")
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in ("127.0.0.1", "::1")
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path != "/platform-auth/callback"
            or not parsed.port
        ):
            raise ValueError()
        expected = "[::1]" if parsed.hostname == "::1" else "127.0.0.1"
        if parsed.netloc != expected + ":" + str(parsed.port):
            raise ValueError()
    except ValueError:
        raise PlatformError(400, "invalid_callback_uri") from None
    return value


class IdentityService:
    def __init__(self, db, settings, *, http_client=None):
        self.db = db
        self.settings = settings
        self.oidc = OIDC(settings, http_client)

    async def _client(self, conn, product_id, client_id):
        row = await (
            await conn.execute(
                "SELECT p.id FROM products p JOIN product_clients c ON c.product_id=p.id WHERE p.id=%s AND c.client_id=%s AND p.enabled AND c.enabled",
                (product_id, client_id),
            )
        ).fetchone()
        if not row:
            raise PlatformError(403, "product_client_disabled")
        if product_id not in self.settings.oidc_clients or not self.settings.issuer:
            raise PlatformError(503, "identity_not_configured")

    async def begin_login(
        self, product_id, client_id, callback_uri, handoff_challenge, local_state
    ):
        bounded(product_id)
        bounded(client_id)
        bounded(local_state, 256)
        callback = globals()["callback_uri"](callback_uri)
        if not isinstance(handoff_challenge, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{43}", handoff_challenge
        ):
            raise PlatformError(400, "invalid_pkce")
        transaction = opaque()
        expires = now() + timedelta(seconds=self.settings.auth_ttl)
        async with self.db.connection() as conn:
            await self._client(conn, product_id, client_id)
            await conn.execute(
                "INSERT INTO identity_transactions(id,product_id,client_id,callback_uri,handoff_challenge,local_state,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    transaction,
                    product_id,
                    client_id,
                    callback,
                    handoff_challenge,
                    local_state,
                    expires,
                ),
            )
        return {
            "transaction_id": transaction,
            "authorize_url": self.settings.public_url
            + "/oidc/"
            + product_id
            + "/authorize?"
            + urlencode({"transaction_id": transaction}),
            "expires_at": expires.isoformat(),
        }

    async def authorize(self, transaction_id, *, expected_product=None):
        bounded(transaction_id)
        config = await self.oidc.discovery()
        state, nonce, cookie = opaque(), opaque(), opaque()
        async with self.db.connection() as conn:
            row = await (
                await conn.execute(
                    "SELECT * FROM identity_transactions WHERE id=%s FOR UPDATE",
                    (transaction_id,),
                )
            ).fetchone()
            if (
                not row
                or (
                    expected_product is not None
                    and row["product_id"] != expected_product
                )
                or row["expires_at"] <= now()
                or row["consumed"]
                or row["state_hash"]
            ):
                raise PlatformError(400, "login_transaction_invalid")
            await self._client(conn, row["product_id"], row["client_id"])
            await conn.execute(
                "UPDATE identity_transactions SET state_hash=%s,nonce_hash=%s,cookie_hash=%s WHERE id=%s",
                (digest(state), digest(nonce), digest(cookie), transaction_id),
            )
        client = self.settings.oidc_clients[row["product_id"]]
        params = {
            "client_id": client.client_id,
            "redirect_uri": self.settings.public_url
            + "/oidc/"
            + row["product_id"]
            + "/callback",
            "response_type": "code",
            "scope": "openid profile email",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge(cookie),
            "code_challenge_method": "S256",
        }
        if getattr(client, "force_login", False):
            params["prompt"] = "login"
        return {
            "url": config["authorization_endpoint"] + "?" + urlencode(params),
            "cookie": cookie,
            "product_id": row["product_id"],
        }

    async def callback(self, product_id, code, state, cookie):
        bounded(product_id)
        bounded(code, 4096)
        bounded(state)
        bounded(cookie)
        async with self.db.connection() as conn:
            tx = await (
                await conn.execute(
                    "SELECT * FROM identity_transactions WHERE state_hash=%s FOR UPDATE",
                    (digest(state),),
                )
            ).fetchone()
            if (
                not tx
                or tx["product_id"] != product_id
                or tx["consumed"]
                or tx["expires_at"] <= now()
                or not hmac.compare_digest(tx["cookie_hash"], digest(cookie))
            ):
                raise PlatformError(400, "login_transaction_invalid")
            await self._client(conn, product_id, tx["client_id"])
            await conn.execute(
                "UPDATE identity_transactions SET consumed=true WHERE id=%s",
                (tx["id"],),
            )
        verified = await self.oidc.exchange(product_id, code, cookie, tx["nonce_hash"])
        handoff = opaque()
        async with self.db.connection() as conn:
            # Shares bounded schema/product identity stripes with continuity migration.
            await conn.execute(
                "SELECT lock_product_identity(%s,%s)",
                (verified.issuer, verified.subject),
            )
            ext = await (
                await conn.execute(
                    "SELECT user_id FROM external_identities WHERE issuer=%s AND subject=%s",
                    (verified.issuer, verified.subject),
                )
            ).fetchone()
            if ext:
                user_id = ext["user_id"]
                await conn.execute(
                    "UPDATE external_identities SET email=%s,email_verified=%s WHERE issuer=%s AND subject=%s",
                    (
                        verified.email,
                        verified.email_verified,
                        verified.issuer,
                        verified.subject,
                    ),
                )
                account = await (
                    await conn.execute(
                        "SELECT t.id tenant_id,b.id billing_account_id FROM tenants t JOIN billing_accounts b ON b.tenant_id=t.id WHERE t.owner_user_id=%s ORDER BY t.id LIMIT 1",
                        (user_id,),
                    )
                ).fetchone()
                if not account:
                    raise PlatformError(403, "account_unavailable")
                tenant_id, billing_id = (
                    account["tenant_id"],
                    account["billing_account_id"],
                )
            else:
                user_id, tenant_id, billing_id = opaque(), opaque(), opaque()
                await conn.execute("INSERT INTO users(id) VALUES (%s)", (user_id,))
                await conn.execute(
                    "INSERT INTO external_identities(issuer,subject,user_id,email,email_verified) VALUES (%s,%s,%s,%s,%s)",
                    (
                        verified.issuer,
                        verified.subject,
                        user_id,
                        verified.email,
                        verified.email_verified,
                    ),
                )
                await conn.execute(
                    "INSERT INTO tenants(id,owner_user_id) VALUES (%s,%s)",
                    (tenant_id, user_id),
                )
                await conn.execute(
                    "INSERT INTO tenant_members(tenant_id,user_id,role) VALUES (%s,%s,'owner')",
                    (tenant_id, user_id),
                )
                await conn.execute(
                    "INSERT INTO billing_accounts(id,tenant_id) VALUES (%s,%s)",
                    (billing_id, tenant_id),
                )
            await conn.execute(
                "INSERT INTO identity_handoffs(code_hash,transaction_id,user_id,tenant_id,billing_account_id,issuer,subject,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    digest(handoff),
                    tx["id"],
                    user_id,
                    tenant_id,
                    billing_id,
                    verified.issuer,
                    verified.subject,
                    now() + timedelta(seconds=self.settings.handoff_ttl),
                ),
            )
        return {
            "redirect_uri": tx["callback_uri"]
            + "?"
            + urlencode({"code": handoff, "state": tx["local_state"]})
        }

    async def redeem(self, payload):
        if not isinstance(payload, dict):
            raise PlatformError(400, "invalid_request")
        for field in (
            "code",
            "code_verifier",
            "product_id",
            "client_id",
            "callback_uri",
            "state",
        ):
            bounded(payload.get(field), 2048)
        verifier = payload["code_verifier"]
        if not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier):
            raise PlatformError(400, "invalid_pkce")
        async with self.db.connection() as conn:
            row = await (
                await conn.execute(
                    "SELECT h.*, t.product_id,t.client_id,t.callback_uri,t.handoff_challenge,t.local_state FROM identity_handoffs h JOIN identity_transactions t ON t.id=h.transaction_id WHERE h.code_hash=%s FOR UPDATE OF h",
                    (digest(payload["code"]),),
                )
            ).fetchone()
            if (
                not row
                or row["consumed"]
                or row["expires_at"] <= now()
                or any(
                    row[key] != payload[key]
                    for key in ("product_id", "client_id", "callback_uri")
                )
                or row["local_state"] != payload["state"]
                or not hmac.compare_digest(
                    row["handoff_challenge"], challenge(verifier)
                )
            ):
                raise PlatformError(400, "handoff_invalid")
            await self._client(conn, row["product_id"], row["client_id"])
            user = await (
                await conn.execute(
                    "SELECT u.authorization_epoch FROM users u JOIN tenant_members m ON m.user_id=u.id WHERE u.id=%s AND m.tenant_id=%s AND u.active AND m.active",
                    (row["user_id"], row["tenant_id"]),
                )
            ).fetchone()
            if not user:
                raise PlatformError(403, "account_unavailable")
            row.update(
                id=opaque(),
                authorization_epoch=user["authorization_epoch"],
                expires_at=now() + timedelta(seconds=self.settings.refresh_ttl),
            )
            await conn.execute(
                "INSERT INTO identity_sessions(id,user_id,tenant_id,billing_account_id,product_id,client_id,authorization_epoch,issuer,subject,expires_at,source_transaction_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                tuple(
                    row[key]
                    for key in (
                        "id",
                        "user_id",
                        "tenant_id",
                        "billing_account_id",
                        "product_id",
                        "client_id",
                        "authorization_epoch",
                        "issuer",
                        "subject",
                        "expires_at",
                        "transaction_id",
                    )
                ),
            )
            await conn.execute(
                "UPDATE identity_handoffs SET consumed=true WHERE code_hash=%s",
                (row["code_hash"],),
            )
            return await self._mint(conn, row)

    def _scope(self, row):
        return Scope(
            user_id=row["user_id"],
            product_id=row["product_id"],
            tenant_id=row["tenant_id"],
            billing_account_id=row["billing_account_id"],
            session_id=row["id"],
            authorization_epoch=row["authorization_epoch"],
        )

    async def _mint(self, conn, row):
        access, refresh = opaque(), opaque()
        expires = min(
            now() + timedelta(seconds=self.settings.access_ttl), row["expires_at"]
        )
        for value, kind, deadline in (
            (access, "access", expires),
            (refresh, "refresh", row["expires_at"]),
        ):
            await conn.execute(
                "INSERT INTO identity_tokens(token_hash,session_id,kind,expires_at) VALUES (%s,%s,%s,%s)",
                (digest(value), row["id"], kind, deadline),
            )
        account = await self._account(conn, row)
        return dict(
            access_token=access,
            refresh_token=refresh,
            session_id=row["id"],
            expires_at=expires.isoformat(),
            refresh_expires_at=row["expires_at"].isoformat(),
            scope=asdict(self._scope(row)),
            account=account,
        )

    async def _valid_session(self, conn, session_id):
        row = await (
            await conn.execute(
                "SELECT s.*,u.active user_active,u.authorization_epoch current_epoch,m.active member_active,p.enabled product_enabled,c.enabled client_enabled FROM identity_sessions s JOIN users u ON u.id=s.user_id JOIN tenant_members m ON m.user_id=s.user_id AND m.tenant_id=s.tenant_id JOIN billing_accounts b ON b.id=s.billing_account_id AND b.tenant_id=s.tenant_id JOIN products p ON p.id=s.product_id JOIN product_clients c ON c.product_id=s.product_id AND c.client_id=s.client_id WHERE s.id=%s",
                (session_id,),
            )
        ).fetchone()
        if (
            not row
            or row["revoked_at"]
            or row["expires_at"] <= now()
            or row["current_epoch"] != row["authorization_epoch"]
            or not all(
                row[key]
                for key in (
                    "user_active",
                    "member_active",
                    "product_enabled",
                    "client_enabled",
                )
            )
        ):
            raise PlatformError(401, "session_invalid")
        return row

    async def authenticate(self, access_token, product_id=None):
        bounded(access_token, 256)
        async with self.db.connection() as conn:
            token = await (
                await conn.execute(
                    "SELECT * FROM identity_tokens WHERE token_hash=%s AND kind='access'",
                    (digest(access_token),),
                )
            ).fetchone()
            if not token or token["consumed"] or token["expires_at"] <= now():
                raise PlatformError(401, "session_invalid")
            row = await self._valid_session(conn, token["session_id"])
            if product_id is not None and row["product_id"] != product_id:
                raise PlatformError(403, "product_scope_mismatch")
            return self._scope(row)

    async def refresh(self, refresh_token):
        bounded(refresh_token, 256)
        replay = False
        result = None
        async with self.db.connection() as conn:
            token = await (
                await conn.execute(
                    "SELECT * FROM identity_tokens WHERE token_hash=%s AND kind='refresh'",
                    (digest(refresh_token),),
                )
            ).fetchone()
            if not token:
                raise PlatformError(401, "session_invalid")
            # Session lock also serializes refresh versus explicit revocation.
            await conn.execute(
                "SELECT id FROM identity_sessions WHERE id=%s FOR UPDATE",
                (token["session_id"],),
            )
            token = await (
                await conn.execute(
                    "SELECT * FROM identity_tokens WHERE token_hash=%s FOR UPDATE",
                    (digest(refresh_token),),
                )
            ).fetchone()
            if token["consumed"]:
                await conn.execute(
                    "UPDATE identity_sessions SET revoked_at=now() WHERE id=%s",
                    (token["session_id"],),
                )
                replay = True
            else:
                if token["expires_at"] <= now():
                    raise PlatformError(401, "session_invalid")
                row = await self._valid_session(conn, token["session_id"])
                await conn.execute(
                    "UPDATE identity_tokens SET consumed=true WHERE session_id=%s AND NOT consumed",
                    (row["id"],),
                )
                result = await self._mint(conn, row)
        if replay:
            raise PlatformError(401, "refresh_replayed")
        return result

    async def logout(self, access_token):
        scope = await self.authenticate(access_token)
        await self.revoke(scope, scope.session_id)

    async def _account(self, conn, row):
        external = await (
            await conn.execute(
                "SELECT email,email_verified FROM external_identities WHERE issuer=%s AND subject=%s AND user_id=%s",
                (row["issuer"], row["subject"], row["user_id"]),
            )
        ).fetchone()
        if not external:
            raise PlatformError(401, "session_invalid")
        return {"id": row["user_id"], **external}

    async def _scope_row(self, conn, scope):
        row = await self._valid_session(conn, scope.session_id)
        if self._scope(row) != scope:
            raise PlatformError(403, "scope_mismatch")
        return row

    async def validate_scope(self, conn, scope, *, lock=True):
        """Validate all frozen claims under the session lock used by revocation."""
        if lock:
            await conn.execute(
                "SELECT id FROM identity_sessions WHERE id=%s FOR UPDATE",
                (scope.session_id,),
            )
        return await self._scope_row(conn, scope)

    async def me(self, scope):
        async with self.db.connection() as conn:
            row = await self._scope_row(conn, scope)
            return {"account": await self._account(conn, row), "scope": asdict(scope)}

    async def sessions(self, scope):
        async with self.db.connection() as conn:
            await self._scope_row(conn, scope)
            rows = await (
                await conn.execute(
                    "SELECT id,product_id,client_id,created_at,expires_at FROM identity_sessions WHERE user_id=%s AND revoked_at IS NULL AND expires_at>now() ORDER BY created_at",
                    (scope.user_id,),
                )
            ).fetchall()
            return [
                {
                    **row,
                    "created_at": row["created_at"].isoformat(),
                    "expires_at": row["expires_at"].isoformat(),
                    "current": row["id"] == scope.session_id,
                }
                for row in rows
            ]

    async def revoke(self, scope, session_id):
        bounded(session_id)
        async with self.db.connection() as conn:
            await self._scope_row(conn, scope)
            row = await (
                await conn.execute(
                    "UPDATE identity_sessions SET revoked_at=now() WHERE id=%s AND user_id=%s RETURNING id",
                    (session_id, scope.user_id),
                )
            ).fetchone()
            if not row:
                raise PlatformError(404, "session_not_found")
