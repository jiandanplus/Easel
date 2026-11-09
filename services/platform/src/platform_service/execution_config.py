"""Private fixed-provider execution configuration; disabled absent explicit file."""

from dataclasses import dataclass, field
import hashlib
import ipaddress
import json
import os
import re
import stat
from urllib.parse import urlsplit

from .execution_protocol import _unique


@dataclass(frozen=True)
class ExecutionProvider:
    version: str
    endpoint: str = field(repr=False)
    secret: str = field(repr=False)
    models: dict[str, str] = field(default_factory=dict)
    preflight_models: bool = False
    managed_models: bool = False
    models_url: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class ExecutionSettings:
    mode: str = "disabled"
    providers: dict[str, ExecutionProvider] = field(default_factory=dict, repr=False)
    concurrency: int = 4
    timeout_seconds: int = 90
    heartbeat_seconds: int = 10
    recovery_seconds: int = 15
    worker_enabled: bool = True

    def __post_init__(self):
        if self.mode not in {"disabled", "test", "live"} or not isinstance(
            self.providers, dict
        ):
            raise ValueError("invalid_execution_configuration")
        if type(self.worker_enabled) is not bool:
            raise ValueError("invalid_execution_configuration")
        for item, maximum in (
            (self.concurrency, 16),
            (self.timeout_seconds, 300),
            (self.heartbeat_seconds, 30),
            (self.recovery_seconds, 60),
        ):
            if type(item) is not int or not 1 <= item <= maximum:
                raise ValueError("invalid_execution_configuration")
        if self.mode != "disabled" and not self.providers:
            raise ValueError("invalid_execution_configuration")
        models = set()
        for name, provider in self.providers.items():
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) or not isinstance(
                provider, ExecutionProvider
            ):
                raise ValueError("invalid_execution_configuration")
            if any(
                not isinstance(value, str)
                or not value
                or len(value) > 4096
                or any(ord(c) < 32 for c in value)
                for value in (provider.secret, provider.version)
            ):
                raise ValueError("invalid_execution_configuration")
            if not provider.secret.isascii() or any(
                not 33 <= ord(c) <= 126 for c in provider.secret
            ):
                raise ValueError("invalid_execution_configuration")
            parsed = urlsplit(provider.endpoint)
            try:
                parsed.port
            except ValueError:
                raise ValueError("invalid_execution_configuration") from None
            try:
                local = (
                    bool(parsed.hostname)
                    and ipaddress.ip_address(parsed.hostname).is_loopback
                )
            except ValueError:
                local = False
            if (
                not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
                or "\\" in provider.endpoint
                or any(ord(c) < 33 for c in provider.endpoint)
                or not (
                    (self.mode == "test" and parsed.scheme == "http" and local)
                    or (self.mode == "live" and parsed.scheme == "https")
                )
            ):
                raise ValueError("invalid_execution_configuration")
            if not isinstance(provider.models, dict) or (not provider.models and not provider.managed_models):
                raise ValueError("invalid_execution_configuration")
            for public, upstream in provider.models.items():
                if (
                    not isinstance(public, str)
                    or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", public)
                    or public in models
                    or not isinstance(upstream, str)
                    or not 1 <= len(upstream) <= 128
                    or any(ord(c) < 33 for c in upstream)
                ):
                    raise ValueError("invalid_execution_configuration")
                models.add(public)

    @property
    def bindings(self):
        result = {}
        for name, provider in self.providers.items():
            for public, upstream in provider.models.items():
                frozen = json.dumps(
                    [
                        self.mode,
                        name,
                        provider.version,
                        provider.endpoint,
                        hashlib.sha256(provider.secret.encode()).hexdigest(),
                        public,
                        upstream,
                    ],
                    separators=(",", ":"),
                )
                result[public] = {
                    "provider_id": name,
                    "provider_version": provider.version,
                    "binding_hash": hashlib.sha256(frozen.encode()).hexdigest(),
                }
        return result

    @classmethod
    def from_env(cls, central_settings, *, env=None):
        env = os.environ if env is None else env
        path = env.get("CENTRAL_EXECUTION_CONFIG_FILE")
        if not path:
            return cls()
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as source:
                info = os.fstat(source.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_uid != os.getuid()
                    or info.st_size > 65536
                ):
                    raise ValueError
                value = json.loads(source.read(65537), object_pairs_hook=_unique)
            if not isinstance(value, dict):
                raise ValueError
            value["providers"] = {
                key: ExecutionProvider(**provider)
                for key, provider in value.get("providers", {}).items()
            }
            return cls(**value)
        except (OSError, ValueError, TypeError, AttributeError, RecursionError):
            raise ValueError("invalid_execution_configuration") from None
