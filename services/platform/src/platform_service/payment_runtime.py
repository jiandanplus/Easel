"""Durable payment dispatch. All provider I/O happens outside SQL transactions."""

import asyncio
from datetime import datetime
import logging
import re
from uuid import uuid4

from .billing import BillingService
from .errors import PlatformError
from .payments import (
    PaymentOrder,
    RefundRequest,
    WechatConfig,
    WechatProvider,
    AlipayConfig,
    AlipayProvider,
)

logger = logging.getLogger(__name__)


def payment_order(payload):
    try:
        value = payload["amount_minor"]
        if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,11}", value):
            raise ValueError
        return PaymentOrder(
            id=payload["order_id"],
            amount_minor=int(value),
            currency=payload["currency"],
            description=payload["description"],
            expires_at=datetime.fromisoformat(payload["expires_at"]),
            provider_transaction_id=payload.get("provider_transaction_id"),
        )
    except (ValueError, KeyError, TypeError):
        raise PlatformError(502, "payment_job_invalid") from None


def build_providers(config):
    try:
        if config.mode == "test":
            from .payment_testing import TestPaymentProvider

            return {
                channel["id"]: TestPaymentProvider(channel["id"], config)
                for channel in config.channels
            }
        result = {}
        if config.wechat:
            result["wechat"] = WechatProvider(WechatConfig(**config.wechat))
        if config.alipay:
            result["alipay"] = AlipayProvider(AlipayConfig(**config.alipay))
        return result
    except (PlatformError, ValueError, KeyError, TypeError):
        raise PlatformError(503, "payment_configuration_invalid") from None


class PaymentRuntime:
    def __init__(self, db, settings, *, providers=None):
        self.settings = settings
        self.contexts = {channel["id"]: channel for channel in settings.channels}
        self.providers = build_providers(settings) if providers is None else providers
        self.billing = BillingService(
            db, mode=settings.mode, channels=settings.channels
        )
        self.worker_id = "payment-" + uuid4().hex

    async def _process(self, job):
        kind, payload = job["kind"], job["payload"]
        if kind in {"order_paid", "order_refunded"}:
            # Durable business events are retained in SQL. Email/push delivery is
            # a separate integration; no external message is sent here.
            return {"status": "delivered"}
        context = self.contexts.get(payload.get("channel"))
        provider = self.providers.get(payload.get("channel"))
        if (
            context is None
            or provider is None
            or payload.get("mode") != self.settings.mode
            or any(
                payload.get(field) != context[field]
                for field in ("merchant_id", "app_id")
            )
        ):
            raise PlatformError(503, "payment_channel_unavailable")
        order = payment_order(payload)
        if kind == "create_checkout":
            checkout = await provider.create(order)
            return {
                "status": "pending",
                "qr_code": checkout.qr_code,
                "expires_at": checkout.expires_at.isoformat(),
            }
        if kind in {"query_order", "close_order"}:
            fact = await (
                provider.query(order)
                if kind == "query_order"
                else provider.close(order)
            )
            return {"status": "observed", "event": fact.to_event()}
        if kind in {"request_refund", "query_refund"}:
            amount = payload["refund_amount_minor"]
            if not isinstance(amount, str) or not re.fullmatch(
                r"[1-9][0-9]{0,11}", amount
            ):
                raise PlatformError(502, "payment_job_invalid")
            refund = RefundRequest(
                payload["refund_id"], int(amount), payload.get("reason", "")[:80]
            )
            fact = await (
                provider.refund(order, refund)
                if kind == "request_refund"
                else provider.query_refund(order, refund)
            )
            return {"status": "observed", "event": fact.to_event()}
        raise PlatformError(502, "payment_job_invalid")

    async def run_once(self, *, limit=20):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise PlatformError(422, "invalid_worker_limit")
        stats = {"claimed": 0, "completed": 0, "retried": 0, "stale": 0}
        if self.settings.mode == "disabled":
            return stats
        for _ in range(limit):
            jobs = await self.billing.claim_outbox(
                worker_id=self.worker_id, limit=1, lease_seconds=120
            )
            if not jobs:
                break
            job = jobs[0]
            stats["claimed"] += 1
            try:
                try:
                    result = await self._process(job)
                except (PlatformError, KeyError, ValueError, TypeError) as error:
                    code = (
                        error.code
                        if isinstance(error, PlatformError)
                        else "payment_job_invalid"
                    )
                    code = (
                        code
                        if re.fullmatch(r"[a-z0-9_]{1,80}", code)
                        else "payment_provider_unavailable"
                    )
                    delay = min(300, 15 * 2 ** min(job.get("attempts", 1), 5))
                    await self.billing.finish_outbox(
                        job["id"], job["lease_token"], error=code, retry_seconds=delay
                    )
                    stats["retried"] += 1
                    continue
                await self.billing.finish_outbox(
                    job["id"], job["lease_token"], result=result
                )
                stats["completed"] += 1
            except PlatformError as error:
                if error.code == "outbox_lease_lost":
                    stats["stale"] += 1
                else:
                    # Provider facts rejected by the core never become payment
                    # success; retain the leased job for controlled recovery.
                    logger.warning(
                        "Payment result rejected (%s)",
                        error.code
                        if re.fullmatch(r"[a-z0-9_]{1,80}", error.code)
                        else "invalid_result",
                    )
                    stats["retried"] += 1
        return stats

    async def run_forever(self):
        while True:
            try:
                await self.run_once(limit=10)
            except asyncio.CancelledError:
                raise
            except Exception:
                # SQL/transport exceptions may contain DSNs or signed payloads.
                # Never interpolate the original exception into application logs.
                logger.warning("Payment worker temporarily unavailable")
            await asyncio.sleep(1)
