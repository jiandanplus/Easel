"""Immutable identity and authorization values shared by central modules."""
from dataclasses import dataclass


def _identifier(value: object, field: str, maximum: int = 255) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\x00" in value:
        raise ValueError(f"invalid {field}")


def _nonnegative_integer(value: object, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 9223372036854775807:
        raise ValueError(f"invalid {field}")


@dataclass(frozen=True)
class Scope:
    user_id: str
    product_id: str
    tenant_id: str
    billing_account_id: str
    session_id: str
    authorization_epoch: int

    def __post_init__(self):
        for field in ("user_id", "product_id", "tenant_id", "billing_account_id", "session_id"):
            _identifier(getattr(self, field), field)
        _nonnegative_integer(self.authorization_epoch, "authorization_epoch")


@dataclass(frozen=True)
class VerifiedIdentity:
    issuer: str
    subject: str
    email: str | None
    email_verified: bool
    authenticated_at: int

    def __post_init__(self):
        _identifier(self.issuer, "issuer", 2048)
        _identifier(self.subject, "subject")
        if self.email is not None:
            _identifier(self.email, "email", 320)
        if not isinstance(self.email_verified, bool):
            raise ValueError("invalid email_verified")
        _nonnegative_integer(self.authenticated_at, "authenticated_at")


@dataclass(frozen=True)
class OperatorIdentity:
    id: str
    capabilities: frozenset[str]

    def __post_init__(self):
        _identifier(self.id, "id")
        if not isinstance(self.capabilities, frozenset):
            raise ValueError("capabilities must be immutable")
        for capability in self.capabilities:
            _identifier(capability, "capability")
