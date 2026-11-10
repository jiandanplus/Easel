"""Explicit independent Console configuration. No product/owner credential fallback."""
from dataclasses import dataclass, field
import os
import re

from .config import OIDCClient, Settings


@dataclass(frozen=True)
class ConsoleSettings:
    database_url: str = field(repr=False)
    public_url: str
    issuer: str
    client_id: str
    client_secret: str = field(repr=False)
    db_schema: str
    allow_loopback_http: bool = False
    pool_min_size: int = 1
    pool_max_size: int = 2
    session_ttl: int = 28800
    auth_ttl: int = 300
    provider_public_key: str | None = field(default=None, repr=False)
    # Optional: editing sign-in accounts (Logto Management API, machine-to-machine app).
    logto_endpoint: str | None = None
    logto_client_id: str | None = None
    logto_client_secret: str | None = field(default=None, repr=False)
    # The issuer products sign in with; accounts made in the console are linked under it.
    product_issuer: str | None = None

    def __post_init__(self):
        if (not isinstance(self.db_schema, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", self.db_schema)
                or type(self.allow_loopback_http) is not bool or not self.issuer
                or self.session_ttl != 28800 or type(self.session_ttl) is not int
                or self.auth_ttl != 300 or type(self.auth_ttl) is not int
                or type(self.pool_min_size) is not int or type(self.pool_max_size) is not int):
            raise ValueError("invalid_console_configuration")
        validated = self.oidc_settings()
        object.__setattr__(self, "public_url", validated.public_url)
        object.__setattr__(self, "issuer", validated.issuer)

    def oidc_settings(self):
        return Settings(database_url=self.database_url, public_url=self.public_url,
            issuer=self.issuer, oidc_clients={"console": OIDCClient(self.client_id,self.client_secret,force_login=True)},
            db_schema=self.db_schema, allow_loopback_http=self.allow_loopback_http,
            pool_min_size=self.pool_min_size,pool_max_size=self.pool_max_size,auth_ttl=self.auth_ttl)

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        if env.get("CENTRAL_CONSOLE_ENABLED") != "1":
            raise ValueError("console_disabled")
        try:
            return cls(database_url=env["CENTRAL_CONSOLE_DATABASE_URL"],public_url=env["CENTRAL_CONSOLE_PUBLIC_URL"],
                issuer=env["CENTRAL_CONSOLE_OIDC_ISSUER"],client_id=env["CENTRAL_CONSOLE_OIDC_CLIENT_ID"],
                client_secret=env["CENTRAL_CONSOLE_OIDC_CLIENT_SECRET"],db_schema=env["CENTRAL_CONSOLE_DB_SCHEMA"],
                allow_loopback_http=env.get("CENTRAL_CONSOLE_ALLOW_LOOPBACK_HTTP")=="1",
                provider_public_key=env.get("CENTRAL_CONSOLE_PROVIDER_PUBLIC_KEY") or None,
                logto_endpoint=env.get("CENTRAL_CONSOLE_LOGTO_ENDPOINT") or None,
                logto_client_id=env.get("CENTRAL_CONSOLE_LOGTO_M2M_ID") or None,
                logto_client_secret=env.get("CENTRAL_CONSOLE_LOGTO_M2M_SECRET") or None,
                product_issuer=env.get("CENTRAL_CONSOLE_PRODUCT_ISSUER") or None)
        except (KeyError,TypeError,ValueError):
            raise ValueError("invalid_console_configuration") from None
