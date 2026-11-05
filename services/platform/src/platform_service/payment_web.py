"""Product order APIs and exact provider notification endpoints."""

from datetime import datetime, timezone
import json
import logging
import re

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from .checkout_qr import qr_data_url
from .errors import PlatformError

logger = logging.getLogger(__name__)


def _unique(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError
        result[key] = value
    return result


async def _body(request, fields, *, optional=False):
    try:
        raw = request.state.bounded_body
        value = (
            {} if optional and not raw else json.loads(raw, object_pairs_hook=_unique)
        )
        if not isinstance(value, dict) or set(value) != set(fields):
            raise ValueError
        return value
    except (ValueError, TypeError, UnicodeError, AttributeError):
        raise PlatformError(400, "invalid_order_request") from None


def decorate_order(order, *, include_qr=True):
    result = dict(order)
    checkout = result.get("checkout")
    if result.get("status") not in {"creating", "pending"}:
        result["checkout"] = None
    elif isinstance(checkout, dict):
        checkout = dict(checkout)
        try:
            expiry = datetime.fromisoformat(checkout["expires_at"])
            if (
                not include_qr
                or expiry.tzinfo is None
                or expiry <= datetime.now(timezone.utc)
            ):
                checkout["qr_data_url"] = None
            else:
                checkout["qr_data_url"] = qr_data_url(checkout["qr_code"])
        except (PlatformError, ValueError, KeyError, TypeError):
            checkout["qr_data_url"] = None
        result["checkout"] = checkout
    return result


def create_payment_router(runtime, require_scope, require_product):
    router = APIRouter()
    billing = runtime.billing

    @router.get("/v2/billing/catalog")
    async def catalog(product_id: str = "easel"):
        await require_product(product_id)
        return await billing.catalog(product_id)

    @router.post("/v2/orders")
    async def create(request: Request):
        scope = await require_scope(request)
        payload = await _body(request, {"offer_id", "channel", "idempotency_key"})
        return decorate_order(await billing.create_order(scope, **payload))

    @router.get("/v2/orders")
    async def orders(request: Request):
        return {
            "orders": [
                decorate_order(order, include_qr=False)
                for order in await billing.orders(await require_scope(request))
            ]
        }

    def order_id(value):
        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
            raise PlatformError(404, "order_not_found")
        return value

    @router.get("/v2/orders/{id}")
    async def read(request: Request, id: str):
        return decorate_order(
            await billing.get_order(await require_scope(request), order_id(id))
        )

    @router.post("/v2/orders/{id}/refresh")
    async def refresh(request: Request, id: str):
        scope = await require_scope(request)
        await _body(request, set(), optional=True)
        return decorate_order(await billing.request_refresh(scope, order_id(id)))

    @router.post("/v2/orders/{id}/close")
    async def close(request: Request, id: str):
        scope = await require_scope(request)
        await _body(request, set(), optional=True)
        return decorate_order(await billing.request_close(scope, order_id(id)))

    def acknowledge(channel, success, status=200):
        if channel == "alipay":
            return PlainTextResponse(
                "success" if success else "failure", status_code=status
            )
        return JSONResponse(
            {
                "code": "SUCCESS" if success else "FAIL",
                "message": "成功" if success else "payment_notification_rejected",
            },
            status_code=status,
        )

    @router.post("/v2/payments/{channel}/notify")
    async def notify(request: Request, channel: str):
        provider = runtime.providers.get(channel)
        if (
            channel not in {"wechat", "alipay"}
            or provider is None
            or runtime.settings.mode == "disabled"
        ):
            return acknowledge(channel, False, 404)
        try:
            # The outer ASGI boundary has already streamed and bounded these raw
            # bytes. Never parse then serialize a signed provider payload.
            raw = request.state.bounded_body
            fact = provider.verify_notification(raw, request.headers)
            await billing.apply_verified_event(fact.to_event())
        except PlatformError as error:
            return acknowledge(
                channel,
                False,
                503
                if error.status >= 500
                and error.code
                not in {"payment_invalid_signature", "payment_invalid_response"}
                else 400,
            )
        except Exception:
            # A database failure must cause channel retry. No raw credentials,
            # payer data, signed body or exception text is logged or returned.
            logger.warning("Payment notification processing temporarily unavailable")
            return acknowledge(channel, False, 503)
        return acknowledge(channel, True)

    return router
