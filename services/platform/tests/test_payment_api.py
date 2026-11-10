from dataclasses import replace
import json

import httpx
import pytest

from platform_service.app import create_app
from platform_service.config import Settings
from platform_service.errors import PlatformError
from platform_service.credits import CreditLedger
from platform_service.payment_runtime import payment_order
from test_payment_runtime import setup_runtime


class LocalIdentity:
    def __init__(self, scope):
        self.scope = scope

    async def authenticate(self, token, product_id=None):
        if token == "fixture-valid-token":
            return self.scope
        if token == "fixture-foreign-token":
            return replace(
                self.scope,
                user_id="other",
                tenant_id="foreign",
                billing_account_id="foreign-wallet",
            )
        raise PlatformError(401, "invalid_authentication")


@pytest.mark.asyncio
async def test_disabled_and_live_without_commercial_offer_do_not_advertise_purchase(
    db, tmp_path
):
    from platform_service.payment_config import PaymentSettings
    from platform_service.payment_runtime import PaymentRuntime

    _, _, scope, _ = await setup_runtime(db, tmp_path)
    for mode in ("disabled", "live"):
        config = PaymentSettings(
            mode=mode,
            wechat={"merchant_id": "fixture-merchant", "app_id": "fixture-app"}
            if mode == "live"
            else {},
        )
        runtime = PaymentRuntime(db, config, providers={})
        app = create_app(
            Settings("postgresql://localhost/unused", "https://accounts.example"),
            database=db,
            identity=LocalIdentity(scope),
            payment_runtime=runtime,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://accounts.example"
        ) as client:
            capabilities = (
                await client.get("/v2/capabilities?product_id=easel")
            ).json()
            assert capabilities["billing_stage"] == mode
            assert capabilities["payments_enabled"] is False
            assert capabilities["test_payments_enabled"] is False
            catalog = (await client.get("/v2/billing/catalog?product_id=easel")).json()
            assert catalog["offers"] == []
            assert (
                await client.post(
                    "/v2/orders",
                    json={
                        "offer_id": "runtime-offer",
                        "channel": "wechat",
                        "idempotency_key": "cannot-buy-test",
                    },
                    headers={"Authorization": "Bearer fixture-valid-token"},
                )
            ).status_code >= 400


async def setup_api(db, tmp_path):
    runtime, operator, scope, order = await setup_runtime(db, tmp_path)
    settings = Settings(
        "postgresql://localhost/unused",
        "http://127.0.0.1:18762",
        allow_loopback_http=True,
    )
    app = create_app(
        settings, database=db, identity=LocalIdentity(scope), payment_runtime=runtime
    )
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=settings.public_url,
        headers={"Authorization": "Bearer fixture-valid-token"},
    )
    return client, runtime, scope, order


@pytest.mark.asyncio
async def test_history_metadata_does_not_encode_or_repeat_detail_qr(
    db, tmp_path, monkeypatch
):
    from platform_service import payment_web
    from platform_service.payments import Checkout

    client, runtime, scope, order = await setup_api(db, tmp_path)
    for number in range(4):
        await runtime.billing.create_order(
            scope,
            offer_id="runtime-offer",
            channel="wechat",
            idempotency_key="large-qr-" + str(number),
        )

    async def long_checkout(order):
        return Checkout("x" * 2048, order.expires_at)

    runtime.providers["wechat"].create = long_checkout
    await runtime.run_once()
    encoded = []
    original = payment_web.qr_data_url

    def measured(value):
        encoded.append(len(value))
        return original(value)

    monkeypatch.setattr(payment_web, "qr_data_url", measured)
    async with client:
        history = await client.get("/v2/orders")
        assert history.status_code == 200
        assert encoded == []
        assert len(history.content) < 100000
        assert len(history.json()["orders"]) == 5
        assert all(
            item["checkout"]["qr_data_url"] is None for item in history.json()["orders"]
        )
        detail = await client.get("/v2/orders/" + order["id"])
        assert encoded == [2048]
        assert detail.json()["checkout"]["qr_data_url"].startswith(
            "data:image/svg+xml;base64,"
        )
        assert len(detail.content) < 1048576


@pytest.mark.asyncio
async def test_checkout_catalog_and_qr_are_explicitly_simulated_not_live(db, tmp_path):
    client, runtime, scope, order = await setup_api(db, tmp_path)
    async with client:
        catalog = (await client.get("/v2/billing/catalog?product_id=easel")).json()
        assert catalog["mode"] == "test" and catalog["offers"][0]["amount_minor"] == "1"
        assert "test-wechat-merchant" not in json.dumps(catalog)
        capabilities = (await client.get("/v2/capabilities?product_id=easel")).json()
        assert capabilities["payments_enabled"] is False
        assert capabilities["test_payments_enabled"] is True
        assert capabilities["subscription_execution_enabled"] is False
        await runtime.run_once()
        response = await client.get("/v2/orders/" + order["id"])
        assert response.status_code == 200
        assert response.json()["checkout"]["qr_data_url"].startswith(
            "data:image/svg+xml;base64,"
        )
        assert (await client.get("/v2/orders")).json()["orders"][0]["id"] == order["id"]
        assert (
            await client.get(
                "/v2/orders/" + order["id"],
                headers={"Authorization": "Bearer fixture-foreign-token"},
            )
        ).status_code in {403, 404}
        assert (await CreditLedger(runtime.billing.db).summary(scope))[
            "available"
        ] == "0"


@pytest.mark.asyncio
async def test_order_body_price_and_browser_payment_confirmation_are_rejected(
    db, tmp_path
):
    client, _, _, order = await setup_api(db, tmp_path)
    async with client:
        body = {
            "offer_id": "runtime-offer",
            "channel": "wechat",
            "idempotency_key": "new",
        }
        for extra in (
            {"amount_minor": "1"},
            {"wallet": "foreign-wallet"},
            {"paid": True},
        ):
            result = await client.post("/v2/orders", json={**body, **extra})
            assert result.status_code == 400
        assert (
            await client.post(
                "/v2/orders", json=body, headers={"Origin": "https://hostile.example"}
            )
        ).status_code == 403
        assert (
            await client.post("/v2/orders/" + order["id"] + "/confirm", json={})
        ).status_code == 404
        assert (
            await client.post("/v2/orders/" + order["id"] + "/refund", json={})
        ).status_code == 404


@pytest.mark.asyncio
async def test_asgi_signed_callback_credits_once_and_tamper_does_not(db, tmp_path):
    client, runtime, scope, order = await setup_api(db, tmp_path)
    async with client:
        await runtime.run_once()
        provider = runtime.providers["wechat"]
        porder = payment_order(
            {
                "order_id": order["id"],
                "amount_minor": "1",
                "currency": "CNY",
                "description": "Fixture",
                "expires_at": order["expires_at"],
            }
        )
        raw, headers = provider.record(
            porder, status="paid", event_id="http-signed-event"
        )
        failed = await client.post(
            "/v2/payments/wechat/notify", content=raw + b" ", headers=headers
        )
        assert failed.status_code >= 400
        assert (await CreditLedger(runtime.billing.db).summary(scope))["issued"] == "0"

        assert (
            await client.post(
                "/v2/payments/wechat/notify", content=raw, headers=headers
            )
        ).status_code == 200
        assert (
            await client.post(
                "/v2/payments/wechat/notify", content=raw, headers=headers
            )
        ).status_code == 200
        assert (await CreditLedger(runtime.billing.db).summary(scope))[
            "issued"
        ] == "100000000"
        current = (await client.get("/v2/orders/" + order["id"])).json()
        assert current["status"] == "paid" and current["credit_state"] == "granted"
        for forbidden in (
            "access_token",
            "refresh_token",
            "api_v3_key",
            "signature",
            "merchant_private_key",
        ):
            assert forbidden not in json.dumps(current)


@pytest.mark.asyncio
async def test_real_loopback_http_callback_and_query_recovery(db, tmp_path):
    import asyncio
    import socket
    import time
    import uvicorn

    runtime, _, scope, order = await setup_runtime(db, tmp_path)
    with socket.socket() as owned_socket:
        owned_socket.bind(("127.0.0.1", 0))
        owned_socket.listen()
        origin = "http://127.0.0.1:" + str(owned_socket.getsockname()[1])
        settings = Settings(
            "postgresql://localhost/unused", origin, allow_loopback_http=True
        )
        app = create_app(
            settings,
            database=db,
            identity=LocalIdentity(scope),
            payment_runtime=runtime,
        )
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                lifespan="off",
                access_log=False,
                log_level="error",
                proxy_headers=False,
            )
        )
        running = asyncio.create_task(server.serve(sockets=[owned_socket]))
        try:
            deadline = time.monotonic() + 5
            while not server.started:
                assert not running.done() and time.monotonic() < deadline
                await asyncio.sleep(0.01)
            async with httpx.AsyncClient(
                base_url=origin,
                trust_env=False,
                headers={"Authorization": "Bearer fixture-valid-token"},
            ) as client:
                await runtime.run_once()
                porder = payment_order(
                    {
                        "order_id": order["id"],
                        "amount_minor": "1",
                        "currency": "CNY",
                        "description": "HTTP fixture",
                        "expires_at": order["expires_at"],
                    }
                )
                raw, headers = runtime.providers["wechat"].record(
                    porder, status="paid", event_id="tcp-signed-fixture"
                )
                assert (
                    await client.post(
                        "/v2/payments/wechat/notify",
                        content=raw + b" ",
                        headers=headers,
                    )
                ).status_code == 400
                for _ in range(2):
                    assert (
                        await client.post(
                            "/v2/payments/wechat/notify", content=raw, headers=headers
                        )
                    ).status_code == 200
                current = (await client.get("/v2/orders/" + order["id"])).json()
                assert (
                    current["credit_state"] == "granted"
                    and current["revision"].isdigit()
                )
                # Replayed, durable provider query facts do not grant a second lot.
                await client.post("/v2/orders/" + order["id"] + "/refresh")
                await runtime.run_once()
                assert (await CreditLedger(db).summary(scope))["issued"] == "100000000"
        finally:
            server.should_exit = True
            await asyncio.wait_for(running, timeout=5)


@pytest.mark.asyncio
async def test_refresh_and_close_queue_facts_instead_of_claiming_payment(db, tmp_path):
    client, runtime, scope, order = await setup_api(db, tmp_path)
    async with client:
        refresh = await client.post("/v2/orders/" + order["id"] + "/refresh")
        assert (
            refresh.status_code == 200
            and refresh.json()["credit_state"] == "not_granted"
        )
        await client.post("/v2/orders/" + order["id"] + "/close")
        await runtime.run_once()
        assert (await client.get("/v2/orders/" + order["id"])).json()[
            "status"
        ] == "closed"
        assert (await CreditLedger(runtime.billing.db).summary(scope))["issued"] == "0"
