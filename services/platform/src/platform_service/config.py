"""Server configuration; product clients never supply identity credentials."""
from dataclasses import dataclass, field
import ipaddress
import json
import os
from typing import Mapping
from urllib.parse import urlsplit


@dataclass(frozen=True)
class OIDCClient:
    client_id: str
    client_secret: str = field(repr=False)
    force_login: bool = False

    def __post_init__(self):
        if type(self.force_login) is not bool:
            raise ValueError("invalid_oidc_force_login")
        if not all(isinstance(value, str) and 0 < len(value) <= 4096 and not any(ord(c) < 32 for c in value) for value in (self.client_id, self.client_secret)):
            raise ValueError("invalid_oidc_client")


def _url(value, *, local=False):
    if not isinstance(value, str) or len(value) > 2048 or "\\" in value or any(ord(c) < 33 for c in value):
        raise ValueError("invalid_service_url")
    try:
        parsed = urlsplit(value)
        parsed.port
        loopback = bool(parsed.hostname) and ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        loopback = False
        parsed = urlsplit(value)
    try:
        parsed.port
    except ValueError:
        raise ValueError("invalid_service_url") from None
    if (not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment
            or (parsed.scheme != "https" and not (local and loopback and parsed.scheme == "http"))):
        raise ValueError("invalid_service_url")
    return value.rstrip("/")


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    public_url: str
    issuer: str = ""
    oidc_clients: dict[str, OIDCClient] = field(default_factory=dict, repr=False)
    allow_loopback_http: bool = False
    db_schema: str | None = None
    pool_min_size: int = 1
    pool_max_size: int = 5
    access_ttl: int = 900
    refresh_ttl: int = 2592000
    auth_ttl: int = 300
    handoff_ttl: int = 60

    def __post_init__(self):
        if not isinstance(self.database_url, str) or urlsplit(self.database_url).scheme not in ("postgres", "postgresql"):
            raise ValueError("invalid_database_url")
        object.__setattr__(self, "public_url", _url(self.public_url, local=self.allow_loopback_http))
        if urlsplit(self.public_url).path:
            raise ValueError("public_url_requires_origin")
        if self.issuer:
            object.__setattr__(self, "issuer", _url(self.issuer, local=self.allow_loopback_http))
        if not isinstance(self.oidc_clients, dict) or any(not isinstance(k, str) or not k or not isinstance(v, OIDCClient) for k, v in self.oidc_clients.items()):
            raise ValueError("invalid_oidc_clients")
        if not (1 <= self.pool_min_size <= self.pool_max_size <= 20):
            raise ValueError("invalid_pool_size")
        for value in (self.access_ttl, self.refresh_ttl, self.auth_ttl, self.handoff_ttl):
            if type(value) is not int or value < 1:
                raise ValueError("invalid_auth_lifetime")

    @property
    def identity_ready(self):
        return bool(self.issuer and self.oidc_clients)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None):
        env = os.environ if env is None else env
        try:
            clients = json.loads(env.get("CENTRAL_OIDC_CLIENTS", "{}"))
            if not isinstance(clients, dict):
                raise ValueError
            clients = {key: OIDCClient(**value) for key, value in clients.items()}
            return cls(database_url=env["CENTRAL_DATABASE_URL"], public_url=env["CENTRAL_PUBLIC_URL"], issuer=env.get("CENTRAL_OIDC_ISSUER", ""), oidc_clients=clients,
                       allow_loopback_http=env.get("CENTRAL_ALLOW_LOOPBACK_HTTP") == "1", db_schema=env.get("CENTRAL_DB_SCHEMA") or None,
                       pool_min_size=int(env.get("CENTRAL_POOL_MIN", "1")), pool_max_size=int(env.get("CENTRAL_POOL_MAX", "5")))
        except (KeyError, TypeError, ValueError):
            raise ValueError("invalid_central_configuration") from None
