"""Alipay face-to-face RSA2 protocol with original response-object verification."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx

from .contracts import (MAX_BODY, BoundedTransport, Checkout, Clock, PaymentOrder, RefundRequest,
    VerifiedPayment, VerifiedRefund, aware, config_identity, digest, fail, identifier,
    minor_to_yuan, parse_json, parse_time, private_key, public_key, rsa_sign, rsa_verify,
    safe_notify, text, yuan_to_minor)

ENDPOINT = "https://openapi.alipay.com/gateway.do"


@dataclass(frozen=True)
class AlipayConfig:
    app_id: str
    merchant_id: str
    merchant_private_key: bytes = field(repr=False)
    alipay_public_key: bytes = field(repr=False)
    notify_url: str
    mode: str = "live"


def _response_bytes(raw: bytes, expected: str) -> tuple[dict, bytes, str]:
    """Decode only for offsets: never serialize the signed response object again."""
    envelope = parse_json(raw)  # Reject duplicate keys everywhere before using any parsed values.
    if expected not in envelope or not isinstance(envelope[expected], dict) or not isinstance(envelope.get("sign"), str):
        fail("payment_invalid_response")
    if any(key.endswith("_response") and key != expected for key in envelope):
        fail("payment_invalid_response")
    source = raw.decode("utf-8")
    decoder = json.JSONDecoder(parse_float=Decimal)
    position = len(source) - len(source.lstrip()) + 1
    try:
        while position < len(source):
            while source[position].isspace():
                position += 1
            if source[position] == "}":
                break
            key, position = decoder.raw_decode(source, position)
            while source[position].isspace():
                position += 1
            if source[position] != ":":
                fail()
            position += 1
            while source[position].isspace():
                position += 1
            begin = position
            _, position = decoder.raw_decode(source, position)
            if key == expected:
                return envelope[expected], source[begin:position].encode("utf-8"), envelope["sign"]
            while source[position].isspace():
                position += 1
            if source[position] == ",":
                position += 1
            elif source[position] == "}":
                break
            else:
                fail()
    except (ValueError, IndexError, RecursionError):
        fail()
    fail()


class AlipayProvider:
    channel = "alipay"

    def __init__(self, config: AlipayConfig, *, transport: httpx.AsyncBaseTransport | None = None,
                 now: Clock | None = None):
        config_identity(config.app_id, config.merchant_id, config.mode)
        safe_notify(config.notify_url)
        self.config = config
        self._private = private_key(config.merchant_private_key)
        self._public = public_key(config.alipay_public_key)
        self._http = BoundedTransport(transport)
        self._now = now or (lambda: datetime.now(timezone.utc))

    async def _request(self, method: str, biz: dict, *, notify: bool = False) -> tuple[dict, bytes]:
        from zoneinfo import ZoneInfo
        params = {"app_id": self.config.app_id, "method": method, "format": "JSON",
                  "charset": "utf-8", "sign_type": "RSA2", "version": "1.0",
                  "timestamp": aware(self._now()).astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
                  "biz_content": json.dumps(biz, ensure_ascii=False, separators=(",", ":"))}
        if notify:
            params["notify_url"] = self.config.notify_url
        canonical = "&".join(f"{key}={value}" for key, value in sorted(params.items())).encode()
        params["sign"] = rsa_sign(self._private, canonical)
        status, _, raw = await self._http.request("POST", ENDPOINT,
            headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
            content=urlencode(params).encode())
        if status != 200:
            fail("payment_provider_rejected", retryable=status >= 500 or status == 429)
        result, signed, signature = _response_bytes(raw, method.replace(".", "_") + "_response")
        rsa_verify(self._public, signed, signature)
        if result.get("code") != "10000":
            fail("payment_provider_rejected", retryable=result.get("code") == "20000")
        self._identity(result, notification=False)
        return result, raw

    def _identity(self, payload: dict, *, notification: bool):
        if notification:
            if payload.get("app_id") != self.config.app_id or payload.get("seller_id") != self.config.merchant_id:
                fail("payment_identity_mismatch")
        elif ("app_id" in payload and payload["app_id"] != self.config.app_id or
              "seller_id" in payload and payload["seller_id"] != self.config.merchant_id):
            fail("payment_identity_mismatch")

    def _payment(self, payload: dict, raw: bytes, *, order: PaymentOrder | None = None,
                 event_id: str | None = None, notification: bool = False) -> VerifiedPayment:
        self._identity(payload, notification=notification)
        order_id = identifier(payload.get("out_trade_no"))
        if order and order.id != order_id:
            fail("payment_order_mismatch")
        state = payload.get("trade_status")
        states = {"WAIT_BUYER_PAY": "pending", "TRADE_SUCCESS": "paid", "TRADE_FINISHED": "paid", "TRADE_CLOSED": "closed"}
        if not isinstance(state, str) or state not in states:
            fail("payment_unknown_state")
        total = yuan_to_minor(payload["total_amount"]) if "total_amount" in payload else None
        transaction = payload.get("trade_no")
        if transaction is not None:
            text(transaction, maximum=64)
        payment_time = payload.get("gmt_payment") if notification else payload.get("send_pay_date")
        occurred = parse_time(payment_time, alipay=True) if payment_time else None
        normalized = states[state]
        # A refund notification exposes accumulated refund_fee, not a reliable single-refund amount.
        if notification and (payload.get("out_biz_no") or payload.get("refund_fee") not in (None, "", "0", "0.0", "0.00")):
            normalized = "refund_observed"
        if normalized == "paid" and (total is None or not transaction or occurred is None):
            fail("payment_incomplete_fact")
        if order and total is not None and total != order.amount_minor:
            fail("payment_amount_mismatch")
        return VerifiedPayment(self.channel, self.config.mode, event_id or "query:" + digest(raw), order_id,
                transaction, normalized, total if total is not None else (order.amount_minor if order else None), "CNY",
                self.config.merchant_id, self.config.app_id, occurred, digest(raw), state)

    async def create(self, order: PaymentOrder) -> Checkout:
        remaining = (aware(order.expires_at) - aware(self._now())).total_seconds()
        if remaining < 60:
            fail("payment_order_expired")
        minutes = min(int(remaining // 60), 120)
        result, _ = await self._request("alipay.trade.precreate", {
            "out_trade_no": order.id, "seller_id": self.config.merchant_id,
            "total_amount": minor_to_yuan(order.amount_minor), "subject": order.description,
            "timeout_express": f"{minutes}m", "qr_code_timeout_express": f"{minutes}m"}, notify=True)
        if result.get("out_trade_no") != order.id:
            fail("payment_order_mismatch")
        qr = text(result.get("qr_code"), maximum=2048)
        try:
            parsed = urlsplit(qr)
            if parsed.scheme != "https" or parsed.hostname != "qr.alipay.com" or parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.fragment:
                fail("payment_invalid_checkout")
        except ValueError:
            fail("payment_invalid_checkout")
        return Checkout(qr, order.expires_at)

    async def query(self, order: PaymentOrder) -> VerifiedPayment:
        result, raw = await self._request("alipay.trade.query", {"out_trade_no": order.id})
        return self._payment(result, raw, order=order)

    async def close(self, order: PaymentOrder) -> VerifiedPayment:
        from ..errors import PlatformError
        try:
            result, _ = await self._request("alipay.trade.close", {"out_trade_no": order.id})
            if result.get("out_trade_no") != order.id:
                fail("payment_order_mismatch")
        except PlatformError as error:
            if error.code not in {"payment_provider_rejected", "payment_transport_uncertain"}:
                raise
        return await self.query(order)

    def _refund(self, payload: dict, raw: bytes, order: PaymentOrder, refund: RefundRequest) -> VerifiedRefund:
        if payload.get("out_trade_no") != order.id or payload.get("out_request_no") != refund.id:
            fail("payment_order_mismatch")
        total = yuan_to_minor(payload.get("total_amount"))
        returned = yuan_to_minor(payload.get("refund_amount"))
        if total != order.amount_minor or returned != refund.amount_minor or returned > total:
            fail("payment_amount_mismatch")
        state = payload.get("refund_status")
        if state not in (None, "REFUND_SUCCESS"):
            fail("payment_unknown_state")
        transaction = text(payload.get("trade_no"), maximum=64)
        occurred = parse_time(payload["gmt_refund_pay"], alipay=True) if payload.get("gmt_refund_pay") else None
        # Refund query may omit success time: authenticated final status still proves success.
        return VerifiedRefund(self.channel, self.config.mode, "refund:" + digest(raw), order.id, transaction,
            "refunded" if state == "REFUND_SUCCESS" else "refund_pending", total, "CNY", self.config.merchant_id,
            self.config.app_id, occurred, digest(raw), state or "UNCONFIRMED", refund.id,
            payload.get("refund_id"), returned)

    async def refund(self, order: PaymentOrder, refund: RefundRequest) -> VerifiedRefund:
        if refund.amount_minor > order.amount_minor:
            fail("payment_invalid_refund")
        result, _ = await self._request("alipay.trade.refund", {
            "out_trade_no": order.id, "out_request_no": refund.id,
            "refund_amount": minor_to_yuan(refund.amount_minor), "refund_reason": refund.reason})
        if result.get("out_trade_no") != order.id:
            fail("payment_order_mismatch")
        if "refund_fee" in result and yuan_to_minor(result["refund_fee"]) != refund.amount_minor:
            fail("payment_amount_mismatch")
        return await self.query_refund(order, refund)

    async def query_refund(self, order: PaymentOrder, refund: RefundRequest) -> VerifiedRefund:
        result, raw = await self._request("alipay.trade.fastpay.refund.query", {
            "out_trade_no": order.id, "out_request_no": refund.id})
        return self._refund(result, raw, order, refund)

    def verify_notification(self, raw: bytes, headers: Mapping[str, str]) -> VerifiedPayment:
        if not isinstance(raw, bytes) or len(raw) > MAX_BODY:
            fail("payment_body_too_large")
        values = {key.lower(): value for key, value in headers.items()}
        content_type = values.get("content-type", "").lower().replace(" ", "")
        if content_type not in {"application/x-www-form-urlencoded", "application/x-www-form-urlencoded;charset=utf-8"}:
            fail("payment_invalid_notification")
        try:
            source = raw.decode("ascii")
            if re.search(r"%(?![0-9a-fA-F]{2})", source):
                fail("payment_invalid_notification")
            pairs = parse_qsl(source, keep_blank_values=True, strict_parsing=True, encoding="utf-8", errors="strict", max_num_fields=100)
            params = {}
            for key, value in pairs:
                if key in params:
                    fail("payment_duplicate_fields")
                if not re.fullmatch(r"[a-zA-Z0-9_]{1,64}", key) or len(value) > 16384:
                    fail("payment_invalid_notification")
                params[key] = value
        except (ValueError, UnicodeError):
            fail("payment_invalid_notification")
        if params.get("sign_type") != "RSA2" or params.get("charset", "utf-8").lower() != "utf-8":
            fail("payment_invalid_signature")
        canonical = "&".join(f"{key}={value}" for key, value in sorted(params.items()) if key not in {"sign", "sign_type"}).encode()
        rsa_verify(self._public, canonical, params.get("sign"))
        if params.get("notify_type") != "trade_status_sync":
            fail("payment_invalid_notification")
        event = text(params.get("notify_id"), maximum=128)
        return self._payment(params, raw, event_id=event, notification=True)
