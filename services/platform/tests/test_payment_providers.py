"""Offline protocol contracts. RSA keys belong only to this generated fixture."""

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from urllib.parse import parse_qs, urlencode

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from platform_service.errors import PlatformError

NOW = datetime(2026, 10, 22, tzinfo=timezone.utc)
ORDER_ID = "a" * 32
REFUND_ID = "b" * 32


def test_payment_contract_is_implemented():
    assert importlib.util.find_spec("platform_service.payments"), (
        "signed payment adapters are missing"
    )


@pytest.fixture
def keys():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return (
        private,
        private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        private.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ),
    )


@pytest.fixture
def order():
    from platform_service.payments import PaymentOrder

    return PaymentOrder(ORDER_ID, 123, "CNY", "Easel 测试", NOW + timedelta(minutes=15))


def sign(key, value):
    return base64.b64encode(
        key.sign(value, padding.PKCS1v15(), hashes.SHA256())
    ).decode()


def wechat(keys, transport=None):
    from platform_service.payments import WechatConfig, WechatProvider

    return WechatProvider(
        WechatConfig(
            app_id="wx-test",
            merchant_id="mch-test",
            merchant_private_key=keys[1],
            mch_serial="merchant-serial",
            verification_keys={"PUB_KEY_ID_test": keys[2]},
            api_v3_key=b"x" * 32,
            notify_url="https://merchant.example/notify",
            refund_notify_url="https://merchant.example/refund",
        ),
        transport=transport,
        now=lambda: NOW,
    )


def wx_response(keys, obj, status=200):
    raw = json.dumps(obj, ensure_ascii=False).encode() if obj is not None else b""
    ts = str(int(NOW.timestamp()))
    return httpx.Response(
        status,
        content=raw,
        headers={
            "Wechatpay-Timestamp": ts,
            "Wechatpay-Nonce": "response-nonce",
            "Wechatpay-Serial": "PUB_KEY_ID_test",
            "Wechatpay-Signature": sign(
                keys[0], ts.encode() + b"\nresponse-nonce\n" + raw + b"\n"
            ),
        },
    )


def wx_notify(keys, payload=None, *, kind="TRANSACTION.SUCCESS", timestamp=None):
    payload = payload or {
        "appid": "wx-test",
        "mchid": "mch-test",
        "out_trade_no": ORDER_ID,
        "transaction_id": "wx-trade",
        "trade_state": "SUCCESS",
        "success_time": NOW.isoformat(),
        "amount": {"total": 123, "currency": "CNY"},
    }
    nonce = "123456789012"
    ciphertext = AESGCM(b"x" * 32).encrypt(
        nonce.encode(), json.dumps(payload).encode(), b"transaction"
    )
    raw = json.dumps(
        {
            "id": "wx-event",
            "create_time": NOW.isoformat(),
            "event_type": kind,
            "resource_type": "encrypt-resource",
            "resource": {
                "algorithm": "AEAD_AES_256_GCM",
                "nonce": nonce,
                "associated_data": "transaction",
                "original_type": "transaction"
                if kind.startswith("TRANSACTION")
                else "refund",
                "ciphertext": base64.b64encode(ciphertext).decode(),
            },
        }
    ).encode()
    ts = str(int((timestamp or NOW).timestamp()))
    return raw, {
        "Wechatpay-Timestamp": ts,
        "Wechatpay-Nonce": "notify-nonce",
        "Wechatpay-Serial": "PUB_KEY_ID_test",
        "Wechatpay-Signature": sign(
            keys[0], ts.encode() + b"\nnotify-nonce\n" + raw + b"\n"
        ),
    }


def alipay(keys, transport=None):
    from platform_service.payments import AlipayConfig, AlipayProvider

    return AlipayProvider(
        AlipayConfig(
            app_id="ali-app",
            merchant_id="ali-seller",
            merchant_private_key=keys[1],
            alipay_public_key=keys[2],
            notify_url="https://merchant.example/alipay",
        ),
        transport=transport,
        now=lambda: NOW,
    )


def ali_response(keys, method, obj, *, raw=None):
    body = raw or json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()
    return httpx.Response(
        200,
        content=b'{"'
        + method.replace(".", "_").encode()
        + b'_response":'
        + body
        + b',"sign":"'
        + sign(keys[0], body).encode()
        + b'"}',
    )


def ali_notify(keys, **overrides):
    data = {
        "app_id": "ali-app",
        "seller_id": "ali-seller",
        "out_trade_no": ORDER_ID,
        "trade_no": "ali-trade",
        "trade_status": "TRADE_SUCCESS",
        "total_amount": "1.23",
        "notify_id": "ali-event",
        "notify_time": "2026-10-22 08:00:00",
        "gmt_payment": "2026-10-22 08:00:00",
        "sign_type": "RSA2",
        "charset": "utf-8",
        "notify_type": "trade_status_sync",
    }
    data.update(overrides)
    content = "&".join(f"{k}={v}" for k, v in sorted(data.items()) if k != "sign_type")
    data["sign"] = sign(keys[0], content.encode())
    return urlencode(data).encode(), {
        "content-type": "application/x-www-form-urlencoded; charset=utf-8"
    }


def test_wechat_authentic_notification_and_tamper(keys):
    raw, headers = wx_notify(keys)
    fact = wechat(keys).verify_notification(raw, headers)
    assert fact.to_event()["status"] == "paid"
    assert fact.to_event()["amount_minor"] == 123
    assert fact.to_event()["raw_digest"] == hashlib.sha256(raw).hexdigest()
    with pytest.raises(PlatformError):
        wechat(keys).verify_notification(raw + b" ", headers)


@pytest.mark.parametrize(
    "change",
    [
        "serial",
        "signature",
        "timestamp",
        "ciphertext",
        "algorithm",
        "merchant",
        "app",
        "status",
        "boolean_amount",
    ],
)
def test_wechat_rejects_invalid_notification(keys, change):
    payload = {
        "appid": "wx-test",
        "mchid": "mch-test",
        "out_trade_no": ORDER_ID,
        "transaction_id": "wx-trade",
        "trade_state": "SUCCESS",
        "success_time": NOW.isoformat(),
        "amount": {"total": 123, "currency": "CNY"},
    }
    if change == "merchant":
        payload["mchid"] = "wrong"
    if change == "app":
        payload["appid"] = "wrong"
    if change == "status":
        payload["trade_state"] = "NOTPAY"
    if change == "boolean_amount":
        payload["amount"]["total"] = True
    raw, headers = wx_notify(
        keys,
        payload,
        timestamp=NOW - timedelta(minutes=6) if change == "timestamp" else None,
    )
    if change == "serial":
        headers["Wechatpay-Serial"] = "unknown"
    if change == "signature":
        headers["Wechatpay-Signature"] = "WECHATPAY/SIGNTEST/fake"
    if change in ("algorithm", "ciphertext"):
        data = json.loads(raw)
        data["resource"][change] = "wrong"
        raw = json.dumps(data).encode()
        headers["Wechatpay-Signature"] = sign(
            keys[0],
            headers["Wechatpay-Timestamp"].encode() + b"\nnotify-nonce\n" + raw + b"\n",
        )
    with pytest.raises(PlatformError):
        wechat(keys).verify_notification(raw, headers)


async def test_wechat_http_request_signature_and_query(keys, order):
    async def handler(request):
        assert request.url.host == "api.mch.weixin.qq.com"
        assert request.headers["Wechatpay-Serial"] == "PUB_KEY_ID_test"
        auth = dict(
            part.strip().split("=", 1)
            for part in request.headers["authorization"].split(" ", 1)[1].split(",")
        )
        auth = {k: v.strip('"') for k, v in auth.items()}
        message = (
            request.method.encode()
            + b"\n"
            + request.url.raw_path
            + b"\n"
            + auth["timestamp"].encode()
            + b"\n"
            + auth["nonce_str"].encode()
            + b"\n"
            + request.content
            + b"\n"
        )
        keys[0].public_key().verify(
            base64.b64decode(auth["signature"]),
            message,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        if request.method == "POST":
            data = json.loads(request.content)
            assert data["out_trade_no"] == ORDER_ID and data["amount"] == {
                "total": 123,
                "currency": "CNY",
            }
            return wx_response(keys, {"code_url": "weixin://wxpay/bizpayurl?pr=test"})
        assert request.url.params["mchid"] == "mch-test"
        return wx_response(
            keys,
            {
                "appid": "wx-test",
                "mchid": "mch-test",
                "out_trade_no": ORDER_ID,
                "trade_state": "SUCCESS",
                "transaction_id": "wx-trade",
                "success_time": NOW.isoformat(),
                "amount": {"total": 123, "currency": "CNY"},
            },
        )

    provider = wechat(keys, httpx.MockTransport(handler))
    assert (await provider.create(order)).qr_code.startswith("weixin:")
    assert (await provider.query(order)).to_event()["status"] == "paid"


async def test_wechat_close_then_pending_query_has_no_fabricated_transaction(
    keys, order
):
    paths = []

    async def handler(request):
        paths.append(request.url.path)
        if request.url.path.endswith("/close"):
            return wx_response(keys, None, 204)
        return wx_response(
            keys,
            {
                "appid": "wx-test",
                "mchid": "mch-test",
                "out_trade_no": ORDER_ID,
                "trade_state": "CLOSED",
            },
        )

    fact = await wechat(keys, httpx.MockTransport(handler)).close(order)
    assert paths[0].endswith("/close") and len(paths) == 2
    assert (
        fact.to_event()["status"] == "closed"
        and fact.to_event()["provider_transaction_id"] is None
    )


@pytest.mark.parametrize(
    "state,expected",
    [("NOTPAY", "pending"), ("REFUND", "refund_observed"), ("new-state", None)],
)
async def test_wechat_state_mapping_fails_closed(keys, order, state, expected):
    async def handler(request):
        return wx_response(
            keys,
            {
                "appid": "wx-test",
                "mchid": "mch-test",
                "out_trade_no": ORDER_ID,
                "trade_state": state,
            },
        )

    provider = wechat(keys, httpx.MockTransport(handler))
    if expected is None:
        with pytest.raises(PlatformError):
            await provider.query(order)
    else:
        assert (await provider.query(order)).to_event()["status"] == expected


async def test_wechat_refund_and_query(keys, order):
    from platform_service.payments import RefundRequest

    refund = RefundRequest(REFUND_ID, 123, "unused")

    async def handler(request):
        if request.method == "POST":
            data = json.loads(request.content)
            assert data["out_refund_no"] == REFUND_ID and data["amount"] == {
                "refund": 123,
                "total": 123,
                "currency": "CNY",
            }
        return wx_response(
            keys,
            {
                "out_trade_no": ORDER_ID,
                "out_refund_no": REFUND_ID,
                "transaction_id": "wx-trade",
                "refund_id": "wx-refund",
                "status": "SUCCESS",
                "success_time": NOW.isoformat(),
                "amount": {"total": 123, "refund": 123, "currency": "CNY"},
            },
        )

    provider = wechat(keys, httpx.MockTransport(handler))
    assert (await provider.refund(order, refund)).to_event()["status"] == "refunded"
    assert (await provider.query_refund(order, refund)).to_event()[
        "refund_amount_minor"
    ] == 123


@pytest.mark.parametrize("method", ["refund", "query_refund"])
@pytest.mark.parametrize(
    "identity", [{"appid": "wrong-app"}, {"mchid": "wrong-merchant"}]
)
async def test_wechat_refund_rejects_each_present_wrong_identity(
    keys, order, method, identity
):
    from platform_service.payments import RefundRequest

    async def handler(request):
        return wx_response(
            keys,
            {
                **identity,
                "out_trade_no": ORDER_ID,
                "out_refund_no": REFUND_ID,
                "transaction_id": "wx-trade",
                "refund_id": "wx-refund",
                "status": "SUCCESS",
                "success_time": NOW.isoformat(),
                "amount": {"total": 123, "refund": 123, "currency": "CNY"},
            },
        )

    provider = wechat(keys, httpx.MockTransport(handler))
    with pytest.raises(PlatformError) as caught:
        await getattr(provider, method)(order, RefundRequest(REFUND_ID, 123))
    assert caught.value.code == "payment_identity_mismatch"


@pytest.mark.parametrize("method", ["refund", "query_refund"])
@pytest.mark.parametrize(
    "identity",
    [
        {},
        {"appid": "wx-test"},
        {"mchid": "mch-test"},
        {"appid": "wx-test", "mchid": "mch-test"},
    ],
)
async def test_wechat_refund_allows_documented_missing_identity_fields(
    keys, order, method, identity
):
    from platform_service.payments import RefundRequest

    async def handler(request):
        return wx_response(
            keys,
            {
                **identity,
                "out_trade_no": ORDER_ID,
                "out_refund_no": REFUND_ID,
                "transaction_id": "wx-trade",
                "refund_id": "wx-refund",
                "status": "SUCCESS",
                "success_time": NOW.isoformat(),
                "amount": {"total": 123, "refund": 123, "currency": "CNY"},
            },
        )

    event = (
        await getattr(wechat(keys, httpx.MockTransport(handler)), method)(
            order, RefundRequest(REFUND_ID, 123)
        )
    ).to_event()
    assert event["status"] == "refunded"
    assert event["app_id"] == "wx-test" and event["merchant_id"] == "mch-test"


def test_wechat_refund_notification_still_requires_merchant(keys):
    payload = {
        "appid": "wx-test",
        "out_trade_no": ORDER_ID,
        "out_refund_no": REFUND_ID,
        "transaction_id": "wx-trade",
        "refund_id": "wx-refund",
        "refund_status": "SUCCESS",
        "success_time": NOW.isoformat(),
        "amount": {"total": 123, "refund": 123},
    }
    raw, headers = wx_notify(keys, payload, kind="REFUND.SUCCESS")
    with pytest.raises(PlatformError) as caught:
        wechat(keys).verify_notification(raw, headers)
    assert caught.value.code == "payment_identity_mismatch"


def test_alipay_notification_canonical_values_and_duplicates(keys):
    raw, headers = ali_notify(keys, subject="中文 & += %")
    assert alipay(keys).verify_notification(raw, headers).to_event()["status"] == "paid"
    with pytest.raises(PlatformError):
        alipay(keys).verify_notification(raw + b"&total_amount=1.23", headers)
    with pytest.raises(PlatformError):
        alipay(keys).verify_notification(raw.replace(b"1.23", b"1.24"), headers)


@pytest.mark.parametrize(
    "changes",
    [
        {"app_id": "wrong"},
        {"seller_id": "wrong"},
        {"total_amount": "NaN"},
        {"total_amount": "1.234"},
        {"total_amount": "1e2"},
        {"trade_status": "UNKNOWN"},
        {"sign_type": "RSA"},
    ],
)
def test_alipay_invalid_facts_rejected(keys, changes):
    raw, headers = ali_notify(keys, **changes)
    with pytest.raises(PlatformError):
        alipay(keys).verify_notification(raw, headers)


async def test_alipay_response_original_json_bytes_and_sign_request(keys, order):
    async def handler(request):
        values = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        canonical = "&".join(
            f"{k}={v}" for k, v in sorted(values.items()) if k != "sign"
        )
        keys[0].public_key().verify(
            base64.b64decode(values["sign"]),
            canonical.encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        assert json.loads(values["biz_content"])["out_trade_no"] == ORDER_ID
        if values["method"] == "alipay.trade.precreate":
            assert json.loads(values["biz_content"])["total_amount"] == "1.23"
            original = json.dumps(
                {
                    "code": "10000",
                    "out_trade_no": ORDER_ID,
                    "qr_code": "https://qr.alipay.com/test",
                    "extra": {"sign": '中文 escaped " }'},
                },
                ensure_ascii=False,
                indent=2,
            ).encode()
            return ali_response(keys, values["method"], {}, raw=original)
        return ali_response(
            keys,
            values["method"],
            {
                "code": "10000",
                "out_trade_no": ORDER_ID,
                "trade_no": "ali-trade",
                "trade_status": "TRADE_SUCCESS",
                "total_amount": "1.23",
                "send_pay_date": "2026-10-22 08:00:00",
            },
        )

    provider = alipay(keys, httpx.MockTransport(handler))
    assert (await provider.create(order)).qr_code == "https://qr.alipay.com/test"
    assert (await provider.query(order)).to_event()["status"] == "paid"


async def test_alipay_refund_query_requires_final_refund_status(keys, order):
    from platform_service.payments import RefundRequest

    refund = RefundRequest(REFUND_ID, 123, "unused")

    async def handler(request):
        values = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        data = json.loads(values["biz_content"])
        assert data["out_request_no"] == REFUND_ID
        return ali_response(
            keys,
            values["method"],
            {
                "code": "10000",
                "out_trade_no": ORDER_ID,
                "trade_no": "ali-trade",
                "out_request_no": REFUND_ID,
                "total_amount": "1.23",
                "refund_amount": "1.23",
            },
        )

    provider = alipay(keys, httpx.MockTransport(handler))
    assert (await provider.query_refund(order, refund)).to_event()[
        "status"
    ] == "refund_pending"


@pytest.mark.parametrize("which", ["wechat", "alipay"])
async def test_provider_refuses_unsigned_and_redirected_response(keys, order, which):
    factory = wechat if which == "wechat" else alipay
    for status in (200, 302):

        async def handler(request):
            return httpx.Response(
                status,
                content=b'{"code":"10000"}',
                headers={"location": "https://evil.example/"},
            )

        with pytest.raises(PlatformError):
            await factory(keys, httpx.MockTransport(handler)).create(order)


@pytest.mark.parametrize("which", ["wechat", "alipay"])
async def test_provider_rejects_oversized_body(keys, order, which):
    async def handler(request):
        return httpx.Response(200, content=b"x" * 1_048_577)

    with pytest.raises(PlatformError):
        await (wechat if which == "wechat" else alipay)(
            keys, httpx.MockTransport(handler)
        ).create(order)


@pytest.mark.parametrize("which", ["wechat", "alipay"])
def test_provider_config_identity_is_header_safe(keys, which):
    from dataclasses import replace
    from platform_service.payments import WechatProvider, AlipayProvider

    provider = (wechat if which == "wechat" else alipay)(keys)
    provider_class = WechatProvider if which == "wechat" else AlipayProvider
    with pytest.raises(PlatformError):
        provider_class(
            replace(provider.config, merchant_id='mch-test",signature="injected')
        )


async def test_alipay_malformed_qr_port_is_stable_error(keys, order):
    async def handler(request):
        return ali_response(
            keys,
            "alipay.trade.precreate",
            {
                "code": "10000",
                "out_trade_no": ORDER_ID,
                "qr_code": "https://qr.alipay.com:invalid/test",
            },
        )

    with pytest.raises(PlatformError):
        await alipay(keys, httpx.MockTransport(handler)).create(order)


@pytest.mark.parametrize("which", ["wechat", "alipay"])
async def test_invalid_structured_status_fails_closed(keys, order, which):
    async def handler(request):
        if which == "wechat":
            return wx_response(
                keys,
                {
                    "appid": "wx-test",
                    "mchid": "mch-test",
                    "out_trade_no": ORDER_ID,
                    "trade_state": [],
                },
            )
        return ali_response(
            keys,
            "alipay.trade.query",
            {"code": "10000", "out_trade_no": ORDER_ID, "trade_status": []},
        )

    with pytest.raises(PlatformError):
        await (wechat if which == "wechat" else alipay)(
            keys, httpx.MockTransport(handler)
        ).query(order)


def test_wechat_refund_notification_has_no_appid_currency_and_uses_trusted_context(
    keys,
):
    payload = {
        "mchid": "mch-test",
        "out_trade_no": ORDER_ID,
        "out_refund_no": REFUND_ID,
        "transaction_id": "wx-trade",
        "refund_id": "wx-refund",
        "refund_status": "SUCCESS",
        "success_time": NOW.isoformat(),
        "amount": {"total": 123, "refund": 123},
    }
    raw, headers = wx_notify(keys, payload, kind="REFUND.SUCCESS")
    event = wechat(keys).verify_notification(raw, headers).to_event()
    assert (
        event["status"] == "refunded"
        and event["app_id"] == "wx-test"
        and event["currency"] == "CNY"
    )
    assert event["refund_amount_minor"] == 123


def test_alipay_cumulative_refund_notification_only_requests_reconciliation(keys):
    raw, headers = ali_notify(keys, out_biz_no=REFUND_ID, refund_fee="1.23")
    event = alipay(keys).verify_notification(raw, headers).to_event()
    assert event["status"] == "refund_observed" and "refund_amount_minor" not in event


async def test_alipay_refund_queries_final_status_and_close_queries_payment(
    keys, order
):
    from platform_service.payments import RefundRequest

    methods = []

    async def handler(request):
        values = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        method = values["method"]
        methods.append(method)
        result = {"code": "10000", "out_trade_no": ORDER_ID, "trade_no": "ali-trade"}
        if method == "alipay.trade.refund":
            result["refund_fee"] = "1.23"
        if method == "alipay.trade.fastpay.refund.query":
            result.update(
                out_request_no=REFUND_ID,
                total_amount="1.23",
                refund_amount="1.23",
                refund_status="REFUND_SUCCESS",
                gmt_refund_pay="2026-10-22 08:00:00",
            )
        if method == "alipay.trade.query":
            result.update(
                trade_status="TRADE_SUCCESS",
                total_amount="1.23",
                send_pay_date="2026-10-22 08:00:00",
            )
        return ali_response(keys, method, result)

    provider = alipay(keys, httpx.MockTransport(handler))
    assert (await provider.refund(order, RefundRequest(REFUND_ID, 123))).to_event()[
        "status"
    ] == "refunded"
    assert (await provider.close(order)).to_event()["status"] == "paid"
    assert methods == [
        "alipay.trade.refund",
        "alipay.trade.fastpay.refund.query",
        "alipay.trade.close",
        "alipay.trade.query",
    ]


@pytest.mark.parametrize("which", ["wechat", "alipay"])
async def test_query_cannot_accept_wrong_amount(keys, order, which):
    async def handler(request):
        if which == "wechat":
            return wx_response(
                keys,
                {
                    "appid": "wx-test",
                    "mchid": "mch-test",
                    "out_trade_no": ORDER_ID,
                    "trade_state": "SUCCESS",
                    "transaction_id": "wx-trade",
                    "success_time": NOW.isoformat(),
                    "amount": {"total": 122, "currency": "CNY"},
                },
            )
        return ali_response(
            keys,
            "alipay.trade.query",
            {
                "code": "10000",
                "out_trade_no": ORDER_ID,
                "trade_status": "TRADE_SUCCESS",
                "trade_no": "ali-trade",
                "send_pay_date": "2026-10-22 08:00:00",
                "total_amount": "1.22",
            },
        )

    with pytest.raises(PlatformError):
        await (wechat if which == "wechat" else alipay)(
            keys, httpx.MockTransport(handler)
        ).query(order)


@pytest.mark.parametrize("which", ["wechat", "alipay"])
async def test_network_timeout_is_uncertain_retryable(keys, order, which):
    async def handler(request):
        raise httpx.ReadTimeout("no upstream details")

    with pytest.raises(PlatformError) as caught:
        await (wechat if which == "wechat" else alipay)(
            keys, httpx.MockTransport(handler)
        ).create(order)
    assert caught.value.code == "payment_transport_uncertain" and caught.value.retryable


@pytest.mark.parametrize("which", ["wechat", "alipay"])
async def test_malformed_compressed_response_is_uncertain_retryable(keys, order, which):
    class BadCompressed(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"invalid gzip body"

    async def handler(request):
        return httpx.Response(
            200, headers={"Content-Encoding": "gzip"}, stream=BadCompressed()
        )

    with pytest.raises(PlatformError) as caught:
        await (wechat if which == "wechat" else alipay)(
            keys, httpx.MockTransport(handler)
        ).create(order)
    assert caught.value.code == "payment_transport_uncertain" and caught.value.retryable
    assert str(caught.value) == "payment_transport_uncertain"


@pytest.mark.parametrize("which", ["wechat", "alipay"])
def test_notification_body_limit(keys, which):
    with pytest.raises(PlatformError) as caught:
        (wechat if which == "wechat" else alipay)(keys).verify_notification(
            b"x" * 1_048_577, {}
        )
    assert caught.value.code == "payment_body_too_large"


@pytest.mark.parametrize("certificate_state", ["valid", "expired", "wrong_serial"])
def test_wechat_certificate_serial_and_validity(keys, certificate_state):
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from dataclasses import replace
    from platform_service.payments import WechatProvider

    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test only")])
    end = (
        NOW - timedelta(days=1)
        if certificate_state == "expired"
        else NOW + timedelta(days=1)
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(keys[0].public_key())
        .serial_number(0x1A)
        .not_valid_before(NOW - timedelta(days=2))
        .not_valid_after(end)
        .sign(keys[0], hashes.SHA256())
    )
    serial = "1B" if certificate_state == "wrong_serial" else "1A"
    config = replace(
        wechat(keys).config,
        verification_keys={serial: cert.public_bytes(serialization.Encoding.PEM)},
    )
    raw, headers = wx_notify(keys)
    headers["Wechatpay-Serial"] = serial
    if certificate_state == "valid":
        assert (
            WechatProvider(config, now=lambda: NOW)
            .verify_notification(raw, headers)
            .to_event()["status"]
            == "paid"
        )
    else:
        with pytest.raises(PlatformError):
            WechatProvider(config, now=lambda: NOW).verify_notification(raw, headers)


@pytest.mark.parametrize("which", ["wechat", "alipay"])
async def test_signed_response_tamper_fails(keys, order, which):
    async def handler(request):
        if which == "wechat":
            response = wx_response(
                keys, {"code_url": "weixin://wxpay/bizpayurl?pr=test"}
            )
        else:
            response = ali_response(
                keys,
                "alipay.trade.precreate",
                {
                    "code": "10000",
                    "out_trade_no": ORDER_ID,
                    "qr_code": "https://qr.alipay.com/test",
                },
            )
        return httpx.Response(
            200,
            content=response.content.replace(b"test", b"evil"),
            headers=response.headers,
        )

    with pytest.raises(PlatformError):
        await (wechat if which == "wechat" else alipay)(
            keys, httpx.MockTransport(handler)
        ).create(order)


@pytest.mark.parametrize("which", ["wechat", "alipay"])
async def test_close_timeout_recovers_verified_late_payment(keys, order, which):
    async def handler(request):
        is_close = (
            request.url.path.endswith("/close")
            if which == "wechat"
            else parse_qs(request.content.decode())["method"][0] == "alipay.trade.close"
        )
        if is_close:
            raise httpx.ReadTimeout("uncertain close")
        if which == "wechat":
            return wx_response(
                keys,
                {
                    "appid": "wx-test",
                    "mchid": "mch-test",
                    "out_trade_no": ORDER_ID,
                    "trade_state": "SUCCESS",
                    "transaction_id": "wx-trade",
                    "success_time": NOW.isoformat(),
                    "amount": {"total": 123, "currency": "CNY"},
                },
            )
        return ali_response(
            keys,
            "alipay.trade.query",
            {
                "code": "10000",
                "out_trade_no": ORDER_ID,
                "trade_status": "TRADE_SUCCESS",
                "trade_no": "ali-trade",
                "send_pay_date": "2026-10-22 08:00:00",
                "total_amount": "1.23",
            },
        )

    assert (
        await (wechat if which == "wechat" else alipay)(
            keys, httpx.MockTransport(handler)
        ).close(order)
    ).to_event()["status"] == "paid"


@pytest.mark.parametrize("amount", [True, 1.23, 0, -1])
def test_payment_order_rejects_non_integer_or_nonpositive_amount(amount):
    from platform_service.payments import PaymentOrder

    with pytest.raises(PlatformError):
        PaymentOrder(ORDER_ID, amount, "CNY", "test", NOW + timedelta(minutes=5))


@pytest.mark.parametrize(
    "raw",
    [
        b"sign=x&sign_type=RSA2&a=%XY",
        b"sign=x&sign_type=RSA2&a=%ff",
        b"sign=x&sign_type=RSA2&a=hello&",
        b"sign=x&sign_type=RSA2&sign=x",
    ],
)
def test_alipay_malformed_form_rejected(keys, raw):
    with pytest.raises(PlatformError):
        alipay(keys).verify_notification(
            raw, {"content-type": "application/x-www-form-urlencoded"}
        )


def test_wechat_unknown_structured_event_type_rejected(keys):
    raw, headers = wx_notify(keys)
    data = json.loads(raw)
    data["event_type"] = []
    raw = json.dumps(data).encode()
    headers["Wechatpay-Signature"] = sign(
        keys[0],
        headers["Wechatpay-Timestamp"].encode() + b"\nnotify-nonce\n" + raw + b"\n",
    )
    with pytest.raises(PlatformError):
        wechat(keys).verify_notification(raw, headers)


@pytest.mark.parametrize("which", ["wechat", "alipay"])
async def test_response_body_is_bounded_while_streaming(keys, order, which):
    consumed = []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for i in range(5):
                consumed.append(i)
                yield b"x" * 524288

    async def handler(request):
        return httpx.Response(200, stream=Stream())

    with pytest.raises(PlatformError) as caught:
        await (wechat if which == "wechat" else alipay)(
            keys, httpx.MockTransport(handler)
        ).create(order)
    assert caught.value.code == "payment_body_too_large" and len(consumed) == 3


@pytest.mark.parametrize("which", ["wechat", "alipay"])
def test_provider_config_repr_does_not_expose_key_material(keys, which):
    config = (wechat if which == "wechat" else alipay)(keys).config
    assert "PRIVATE KEY" not in repr(config)
    assert "PUBLIC KEY" not in repr(config)
    assert "xxxxxxxxxxxxxxxx" not in repr(config)
