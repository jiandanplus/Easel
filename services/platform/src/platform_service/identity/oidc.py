"""Bounded OIDC transport and strict signed ID-token verification."""

import hashlib
import hmac
import json
from urllib.parse import urlsplit

import httpx
import jwt

from ..contracts import VerifiedIdentity
from ..errors import PlatformError

MAX_BODY = 262144


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def trusted_url(url, *, allow_loopback_http=False):
    try:
        if (
            not isinstance(url, str)
            or len(url) > 2048
            or "\\" in url
            or any(ord(char) < 33 or ord(char) == 127 for char in url)
        ):
            raise ValueError()
        parsed = urlsplit(url)
        if (
            parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not parsed.hostname
        ):
            raise ValueError()
        if parsed.scheme != "https" and not (
            allow_loopback_http
            and parsed.scheme == "http"
            and parsed.hostname in ("127.0.0.1", "::1")
        ):
            raise ValueError()
        _ = parsed.port
    except (ValueError, TypeError):
        raise PlatformError(503, "identity_not_configured") from None
    return parsed


class OIDC:
    def __init__(self, settings, http_client=None):
        self.settings = settings
        self.client = http_client

    async def request(self, method, url, **kwargs):
        trusted_url(url, allow_loopback_http=self.settings.allow_loopback_http)
        try:
            if self.client is None:
                async with httpx.AsyncClient(
                    timeout=10, follow_redirects=False, trust_env=False
                ) as client:
                    return await self._read(client, method, url, **kwargs)
            return await self._read(self.client, method, url, **kwargs)
        except (httpx.HTTPError, ValueError, TypeError, KeyError):
            raise PlatformError(
                502, "identity_provider_unavailable", retryable=True
            ) from None

    async def _read(self, client, method, url, **kwargs):
        async with client.stream(
            method, url, timeout=10, follow_redirects=False, **kwargs
        ) as response:
            if response.status_code != 200:
                raise PlatformError(
                    502, "identity_provider_unavailable", retryable=True
                )
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_BODY:
                    raise PlatformError(502, "identity_provider_invalid")
            value = json.loads(body)
            if not isinstance(value, dict):
                raise PlatformError(502, "identity_provider_invalid")
            return value

    async def discovery(self):
        issuer = self.settings.issuer
        trusted_url(issuer, allow_loopback_http=self.settings.allow_loopback_http)
        data = await self.request("GET", issuer + "/.well-known/openid-configuration")
        if data.get("issuer") != issuer:
            raise PlatformError(502, "identity_provider_invalid")
        origin = urlsplit(issuer)
        for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            target = trusted_url(
                data.get(field), allow_loopback_http=self.settings.allow_loopback_http
            )
            if (target.scheme, target.netloc) != (origin.scheme, origin.netloc):
                raise PlatformError(502, "identity_provider_invalid")
        return data

    async def exchange(self, product_id, code, verifier, nonce_hash, *, require_auth_time=False):
        config = await self.discovery()
        client = self.settings.oidc_clients[product_id]
        token = await self.request(
            "POST",
            config["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": self.settings.public_url
                + "/oidc/"
                + product_id
                + "/callback",
                "client_id": client.client_id,
                "client_secret": client.client_secret,
            },
        )
        keys = await self.request("GET", config["jwks_uri"])
        try:
            raw = token["id_token"]
            if not isinstance(raw, str) or len(raw) > 32768:
                raise ValueError()
            header = jwt.get_unverified_header(raw)
            # Fixed local allowlist. Never accept arbitrary algorithms from JWT
            # headers or discovery; each choice has its own required key shape.
            algorithm = header.get("alg")
            kid = header.get("kid")
            if (
                algorithm not in ("RS256", "ES384")
                or not isinstance(kid, str)
                or not 1 <= len(kid) <= 255
                or header.get("crit")
            ):
                raise ValueError()
            jwks = keys["keys"]
            if not isinstance(jwks, list):
                raise ValueError()
            matches = [
                item
                for item in jwks
                if isinstance(item, dict) and item.get("kid") == kid
            ]
            if len(matches) != 1:
                raise ValueError()
            jwk = matches[0]
            if (
                jwk.get("alg", algorithm) != algorithm
                or jwk.get("use", "sig") != "sig"
                or ("key_ops" in jwk and jwk["key_ops"] != ["verify"])
            ):
                raise ValueError()
            if algorithm == "RS256":
                if jwk.get("kty") != "RSA" or "crv" in jwk:
                    raise ValueError()
                key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
                if key.key_size < 2048:
                    raise ValueError()
            else:
                if jwk.get("kty") != "EC" or jwk.get("crv") != "P-384":
                    raise ValueError()
                key = jwt.algorithms.ECAlgorithm.from_jwk(json.dumps(jwk))
            claims = jwt.decode(
                raw,
                key,
                algorithms=[algorithm],
                audience=client.client_id,
                issuer=self.settings.issuer,
                options={"require": ["iss", "sub", "aud", "exp", "iat", "nonce"]},
            )
            if (
                isinstance(claims["aud"], list)
                and len(claims["aud"]) > 1
                and claims.get("azp") != client.client_id
            ):
                raise ValueError()
            if "azp" in claims and claims["azp"] != client.client_id:
                raise ValueError()
            if not isinstance(claims["nonce"], str) or not hmac.compare_digest(
                digest(claims["nonce"]), nonce_hash
            ):
                raise ValueError()
            # Console reauthentication must use the signed authentication event,
            # never a freshly issued token timestamp or coerced bool/string claim.
            if require_auth_time and type(claims.get("auth_time")) is not int:
                raise ValueError()
            email = claims.get("email")
            if email is not None and (
                not isinstance(email, str) or not 1 <= len(email) <= 320
            ):
                raise ValueError()
            return VerifiedIdentity(
                issuer=claims["iss"],
                subject=claims["sub"],
                email=email,
                email_verified=claims.get("email_verified") is True,
                authenticated_at=int(claims.get("auth_time", claims["iat"])),
            )
        except (jwt.PyJWTError, ValueError, TypeError, KeyError, AttributeError):
            raise PlatformError(401, "identity_token_invalid") from None
