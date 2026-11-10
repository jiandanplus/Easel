"""WeChat Pay APIv3 Native direct merchant protocol; no SDK/global credentials."""
from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import re
import secrets
from urllib.parse import urlencode, urlsplit

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag

from .contracts import (BoundedTransport, Checkout, Clock, PaymentOrder, RefundRequest,
    VerifiedPayment, VerifiedRefund, aware, config_identity, digest, fail, identifier,
    minor, parse_json, parse_time, private_key, public_key, rsa_sign, rsa_verify,
    safe_notify, text)

HOST = "https://api.mch.weixin.qq.com"


@dataclass(frozen=True)
class WechatConfig:
    app_id: str
    merchant_id: str
    merchant_private_key: bytes = field(repr=False)
    mch_serial: str
    verification_keys: Mapping[str, bytes] = field(repr=False)
    api_v3_key: bytes = field(repr=False)
    notify_url: str
    refund_notify_url: str
    mode: str = "live"


class WechatProvider:
    channel = "wechat"

    def __init__(self, config: WechatConfig, *, transport: httpx.AsyncBaseTransport | None = None,
                 now: Clock | None = None):
        config_identity(config.app_id, config.merchant_id, config.mode)
        safe_notify(config.notify_url)
        safe_notify(config.refund_notify_url)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", config.mch_serial) or not isinstance(config.api_v3_key, bytes) or len(config.api_v3_key) != 32 or not config.verification_keys:
            fail("payment_invalid_config")
        self.config = config
        self._private = private_key(config.merchant_private_key)
        self._keys = {}
        self._certificates = {}
        for serial, pem in config.verification_keys.items():
            if not isinstance(serial, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", serial):
                fail("payment_invalid_config")
            self._keys[serial] = public_key(pem)
            if b"BEGIN CERTIFICATE" in pem:
                from cryptography import x509
                certificate = x509.load_pem_x509_certificate(pem)
                if format(certificate.serial_number, "X").lstrip("0") != serial.upper().lstrip("0"):
                    fail("payment_invalid_config")
                self._certificates[serial] = certificate
        self._preferred_key = next((serial for serial in self._keys if serial.startswith("PUB_KEY_ID_")), next(iter(self._keys)))
        self._http = BoundedTransport(transport)
        self._now = now or (lambda: datetime.now(timezone.utc))

    def _verify(self, raw: bytes, headers: Mapping[str, str]):
        values = {key.lower(): value for key, value in headers.items()}
        serial = values.get("wechatpay-serial", "")
        if serial not in self._keys:
            fail("payment_unknown_signing_key")
        ts = values.get("wechatpay-timestamp", "")
        nonce = values.get("wechatpay-nonce", "")
        if not re.fullmatch(r"[0-9]{1,12}", ts) or not isinstance(nonce, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", nonce):
            fail("payment_invalid_signature")
        if abs(aware(self._now()).timestamp() - int(ts)) > 300:
            fail("payment_expired_signature")
        if serial in self._certificates:
            certificate = self._certificates[serial]
            signed_at = datetime.fromtimestamp(int(ts), timezone.utc)
            if not certificate.not_valid_before_utc <= signed_at <= certificate.not_valid_after_utc:
                fail("payment_expired_signing_key")
        message = ts.encode() + b"\n" + nonce.encode() + b"\n" + raw + b"\n"
        rsa_verify(self._keys[serial], message, values.get("wechatpay-signature"))

    async def _request(self, method: str, path: str, body: dict | None = None) -> tuple[dict, bytes]:
        raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode() if body is not None else b""
        ts, nonce = str(int(aware(self._now()).timestamp())), secrets.token_hex(16)
        message = method.encode() + b"\n" + path.encode() + b"\n" + ts.encode() + b"\n" + nonce.encode() + b"\n" + raw + b"\n"
        signature = rsa_sign(self._private, message)
        authorization = f'WECHATPAY2-SHA256-RSA2048 mchid="{self.config.merchant_id}",nonce_str="{nonce}",timestamp="{ts}",serial_no="{self.config.mch_serial}",signature="{signature}"'
        status, headers, response = await self._http.request(method, HOST + path,
                headers={"Authorization": authorization, "Wechatpay-Serial": self._preferred_key, "Accept": "application/json", "Content-Type": "application/json"}, content=raw)
        # Unverified error contents never become business facts or leak to errors.
        if not 200 <= status < 300:
            fail("payment_provider_rejected", retryable=status >= 500 or status == 429)
        self._verify(response, headers)
        return (parse_json(response) if response else {}), response

    def _identity(self, payload: dict, *, payment: bool, merchant_required: bool = True):
        if (merchant_required or "mchid" in payload) and payload.get("mchid") != self.config.merchant_id:
            fail("payment_identity_mismatch")
        if payment and payload.get("appid") != self.config.app_id:
            fail("payment_identity_mismatch")
        if "appid" in payload and payload["appid"] != self.config.app_id:
            fail("payment_identity_mismatch")

    def _payment(self, payload: dict, raw: bytes, *, event_id: str | None = None,
                 order: PaymentOrder | None = None) -> VerifiedPayment:
        self._identity(payload, payment=True)
        order_id = identifier(payload.get("out_trade_no"))
        if order is not None and order_id != order.id:
            fail("payment_order_mismatch")
        state = payload.get("trade_state")
        states = {"SUCCESS": "paid", "NOTPAY": "pending", "USERPAYING": "pending",
                  "CLOSED": "closed", "REVOKED": "closed", "PAYERROR": "closed", "REFUND": "refund_observed"}
        if not isinstance(state, str) or state not in states:
            fail("payment_unknown_state")
        amount = payload.get("amount", {})
        if not isinstance(amount, dict):
            fail()
        total = minor(amount["total"]) if "total" in amount else None
        currency = amount.get("currency")
        if currency is not None and currency != "CNY":
            fail("payment_invalid_currency")
        transaction = payload.get("transaction_id")
        if transaction is not None:
            text(transaction, maximum=64)
        occurred = parse_time(payload["success_time"]) if "success_time" in payload else None
        if state == "SUCCESS" and (total is None or currency != "CNY" or not transaction or occurred is None):
            fail("payment_incomplete_fact")
        if order and (total is not None and total != order.amount_minor or currency is not None and currency != order.currency):
            fail("payment_amount_mismatch")
        return VerifiedPayment(self.channel, self.config.mode, event_id or "query:" + digest(raw), order_id,
                transaction, states[state], total if total is not None else (order.amount_minor if order else None),
                currency or (order.currency if order else None), self.config.merchant_id, self.config.app_id,
                occurred, digest(raw), state)

    async def create(self, order: PaymentOrder) -> Checkout:
        if aware(order.expires_at) <= aware(self._now()):
            fail("payment_order_expired")
        result, _ = await self._request("POST", "/v3/pay/transactions/native", {
            "appid": self.config.app_id, "mchid": self.config.merchant_id,
            "description": order.description, "out_trade_no": order.id,
            "time_expire": aware(order.expires_at).isoformat(), "notify_url": self.config.notify_url,
            "amount": {"total": order.amount_minor, "currency": order.currency}})
        qr = text(result.get("code_url"), maximum=2048)
        parsed = urlsplit(qr)
        if parsed.scheme != "weixin" or parsed.netloc != "wxpay" or parsed.path != "/bizpayurl" or parsed.fragment:
            fail("payment_invalid_checkout")
        return Checkout(qr, order.expires_at)

    async def query(self, order: PaymentOrder) -> VerifiedPayment:
        result, raw = await self._request("GET", "/v3/pay/transactions/out-trade-no/" + order.id + "?" + urlencode({"mchid": self.config.merchant_id}))
        return self._payment(result, raw, order=order)

    async def close(self, order: PaymentOrder) -> VerifiedPayment:
        from ..errors import PlatformError
        try:
            await self._request("POST", "/v3/pay/transactions/out-trade-no/" + order.id + "/close", {"mchid": self.config.merchant_id})
        except PlatformError as error:
            # Already-paid and uncertain closes resolve only through an authenticated query.
            if error.code not in {"payment_provider_rejected", "payment_transport_uncertain"}:
                raise
        return await self.query(order)

    def _refund(self, payload: dict, raw: bytes, *, order: PaymentOrder | None = None,
                refund: RefundRequest | None = None, event_id: str | None = None,
                notification: bool = False) -> VerifiedRefund:
        self._identity(payload, payment=False, merchant_required=notification)
        order_id = identifier(payload.get("out_trade_no"))
        refund_id = identifier(payload.get("out_refund_no"))
        if order and order.id != order_id or refund and refund.id != refund_id:
            fail("payment_order_mismatch")
        state = payload.get("refund_status" if notification else "status")
        statuses = {"SUCCESS": "refunded", "PROCESSING": "refund_pending", "ABNORMAL": "refund_pending", "CLOSED": "refund_failed"}
        if not isinstance(state, str) or state not in statuses:
            fail("payment_unknown_state")
        amount = payload.get("amount")
        if not isinstance(amount, dict):
            fail("payment_incomplete_fact")
        total, returned = minor(amount.get("total")), minor(amount.get("refund"))
        currency = amount.get("currency", "CNY" if notification else None)
        if currency != "CNY" or returned > total:
            fail("payment_invalid_amount")
        if order and total != order.amount_minor or refund and returned != refund.amount_minor:
            fail("payment_amount_mismatch")
        transaction = text(payload.get("transaction_id"), maximum=64)
        provider_refund_id = text(payload.get("refund_id"), maximum=64)
        occurred = parse_time(payload["success_time"]) if "success_time" in payload else None
        if state == "SUCCESS" and occurred is None:
            fail("payment_incomplete_fact")
        return VerifiedRefund(self.channel, self.config.mode, event_id or "refund:" + digest(raw), order_id,
                transaction, statuses[state], total, currency, self.config.merchant_id, self.config.app_id,
                occurred, digest(raw), state, refund_id, provider_refund_id, returned)

    async def refund(self, order: PaymentOrder, refund: RefundRequest) -> VerifiedRefund:
        if refund.amount_minor > order.amount_minor:
            fail("payment_invalid_refund")
        result, raw = await self._request("POST", "/v3/refund/domestic/refunds", {
            "out_trade_no": order.id, "out_refund_no": refund.id, "reason": refund.reason,
            "notify_url": self.config.refund_notify_url,
            "amount": {"refund": refund.amount_minor, "total": order.amount_minor, "currency": order.currency}})
        return self._refund(result, raw, order=order, refund=refund)

    async def query_refund(self, order: PaymentOrder, refund: RefundRequest) -> VerifiedRefund:
        result, raw = await self._request("GET", "/v3/refund/domestic/refunds/" + refund.id)
        return self._refund(result, raw, order=order, refund=refund)

    def verify_notification(self, raw: bytes, headers: Mapping[str, str]) -> VerifiedPayment | VerifiedRefund:
        from .contracts import MAX_BODY
        if not isinstance(raw, bytes) or len(raw) > MAX_BODY:
            fail("payment_body_too_large")
        self._verify(raw, headers)
        envelope = parse_json(raw)
        event_id = text(envelope.get("id"), maximum=128)
        resource = envelope.get("resource")
        event = envelope.get("event_type")
        if envelope.get("resource_type") != "encrypt-resource" or not isinstance(resource, dict) or resource.get("algorithm") != "AEAD_AES_256_GCM":
            fail("payment_invalid_notification")
        try:
            nonce = text(resource.get("nonce"), maximum=16).encode()
            associated = resource.get("associated_data", "")
            if not isinstance(associated, str) or len(associated) > 16:
                fail("payment_invalid_notification")
            encrypted = base64.b64decode(resource["ciphertext"], validate=True)
            payload = parse_json(AESGCM(self.config.api_v3_key).decrypt(nonce, encrypted, associated.encode()))
        except (InvalidTag, ValueError, TypeError, KeyError):
            fail("payment_invalid_ciphertext")
        if event == "TRANSACTION.SUCCESS" and resource.get("original_type") == "transaction" and payload.get("trade_state") == "SUCCESS":
            return self._payment(payload, raw, event_id=event_id)
        refund_events = {"REFUND.SUCCESS": "SUCCESS", "REFUND.CLOSED": "CLOSED", "REFUND.ABNORMAL": "ABNORMAL"}
        if isinstance(event, str) and event in refund_events and resource.get("original_type") == "refund" and payload.get("refund_status") == refund_events[event]:
            return self._refund(payload, raw, event_id=event_id, notification=True)
        fail("payment_invalid_notification")
