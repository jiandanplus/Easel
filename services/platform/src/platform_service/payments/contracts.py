"""Bounded payment protocol values. This module never stores raw payment payloads."""
from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import re
from urllib.parse import urlsplit

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from ..errors import PlatformError

MAX_BODY = 1_048_576
Clock = Callable[[], datetime]


def fail(code: str = "payment_invalid_response", *, retryable: bool = False):
    raise PlatformError(502, code, retryable=retryable)


def identifier(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{6,32}", value):
        fail("payment_invalid_identifier")
    return value


def text(value: object, *, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or any(ord(c) < 32 for c in value):
        fail()
    return value


def minor(value: object) -> int:
    if type(value) is not int or not 0 < value <= 99_999_999_999:
        fail("payment_invalid_amount")
    return value


def yuan_to_minor(value: object) -> int:
    if isinstance(value, Decimal):
        value = str(value)
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9][0-9]{0,8})(?:\.[0-9]{1,2})?", value):
        fail("payment_invalid_amount")
    whole, _, decimal = value.partition(".")
    return minor(int(whole) * 100 + int(decimal.ljust(2, "0") or "0"))


def minor_to_yuan(value: int) -> str:
    minor(value)
    return f"{value // 100}.{value % 100:02d}"


def aware(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        fail("payment_invalid_time")
    return value.astimezone(timezone.utc)


def parse_time(value: object, *, alipay: bool = False) -> datetime:
    try:
        if not isinstance(value, str):
            fail("payment_invalid_time")
        if alipay:
            from zoneinfo import ZoneInfo
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(timezone.utc)
        return aware(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except (ValueError, TypeError):
        fail("payment_invalid_time")


def _unique(pairs):
    output = {}
    for key, value in pairs:
        if key in output:
            fail("payment_duplicate_fields")
        output[key] = value
    return output


def parse_json(raw: bytes) -> dict:
    if not isinstance(raw, bytes) or len(raw) > MAX_BODY:
        fail("payment_body_too_large")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique, parse_float=Decimal,
                           parse_constant=lambda _: fail("payment_invalid_response"))
        if not isinstance(value, dict):
            fail()
        return value
    except (UnicodeError, ValueError, TypeError, RecursionError):
        fail()


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def private_key(pem: bytes) -> rsa.RSAPrivateKey:
    try:
        key = serialization.load_pem_private_key(pem, password=None)
        if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
            fail("payment_invalid_config")
        return key
    except (ValueError, TypeError):
        fail("payment_invalid_config")


def public_key(pem: bytes) -> rsa.RSAPublicKey:
    try:
        if b"BEGIN CERTIFICATE" in pem:
            from cryptography import x509
            key = x509.load_pem_x509_certificate(pem).public_key()
        else:
            key = serialization.load_pem_public_key(pem)
        if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 2048:
            fail("payment_invalid_config")
        return key
    except (ValueError, TypeError):
        fail("payment_invalid_config")


def rsa_sign(key: rsa.RSAPrivateKey, raw: bytes) -> str:
    return base64.b64encode(key.sign(raw, padding.PKCS1v15(), hashes.SHA256())).decode("ascii")


def rsa_verify(key: rsa.RSAPublicKey, raw: bytes, signature: object):
    try:
        if not isinstance(signature, str) or len(signature) > 2048:
            fail("payment_invalid_signature")
        decoded = base64.b64decode(signature, validate=True)
        key.verify(decoded, raw, padding.PKCS1v15(), hashes.SHA256())
    except (ValueError, TypeError, InvalidSignature):
        fail("payment_invalid_signature")


def safe_notify(value: str):
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or len(value) > 255:
        fail("payment_invalid_config")


def config_identity(app: str, merchant: str, mode: str):
    if not isinstance(app, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", app) or not isinstance(merchant, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", merchant):
        fail("payment_invalid_config")
    if mode not in {"live", "test"}:
        fail("payment_invalid_config")


@dataclass(frozen=True)
class PaymentOrder:
    id: str
    amount_minor: int
    currency: str
    description: str
    expires_at: datetime
    provider_transaction_id: str | None = None

    def __post_init__(self):
        identifier(self.id)
        minor(self.amount_minor)
        if self.currency != "CNY":
            fail("payment_invalid_currency")
        text(self.description, maximum=127)
        aware(self.expires_at)
        if self.provider_transaction_id is not None:
            text(self.provider_transaction_id, maximum=64)


@dataclass(frozen=True)
class RefundRequest:
    id: str
    amount_minor: int
    reason: str = ""

    def __post_init__(self):
        identifier(self.id)
        minor(self.amount_minor)
        if not isinstance(self.reason, str) or len(self.reason) > 80 or any(ord(c) < 32 for c in self.reason):
            fail("payment_invalid_refund")


@dataclass(frozen=True)
class Checkout:
    qr_code: str
    expires_at: datetime


@dataclass(frozen=True)
class VerifiedPayment:
    channel: str
    mode: str
    event_id: str
    order_id: str
    provider_transaction_id: str | None
    status: str
    amount_minor: int | None
    currency: str | None
    merchant_id: str
    app_id: str
    occurred_at: datetime | None
    raw_digest: str
    raw_status: str = ""

    def to_event(self) -> dict:
        event = asdict(self)
        event["occurred_at"] = aware(self.occurred_at).isoformat() if self.occurred_at else None
        return event


@dataclass(frozen=True)
class VerifiedRefund(VerifiedPayment):
    refund_id: str = ""
    provider_refund_id: str | None = None
    refund_amount_minor: int = 0


class BoundedTransport:
    """Each request owns a client; injected transports are for offline protocol tests."""
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self.transport = transport

    async def request(self, method: str, url: str, *, headers: Mapping[str, str], content: bytes) -> tuple[int, Mapping[str, str], bytes]:
        try:
            async with httpx.AsyncClient(transport=self.transport, trust_env=False, follow_redirects=False,
                    timeout=httpx.Timeout(10.0, connect=5.0, pool=5.0), limits=httpx.Limits(max_connections=10, max_keepalive_connections=0)) as client:
                async with client.stream(method, url, headers=headers, content=content) as response:
                    if 300 <= response.status_code < 400:
                        fail("payment_redirect_rejected")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > MAX_BODY:
                            fail("payment_body_too_large")
                        body.extend(chunk)
                    return response.status_code, response.headers, bytes(body)
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError, httpx.DecodingError):
            fail("payment_transport_uncertain", retryable=True)
