import json
import httpx
import pytest
from fastapi import FastAPI
from easel.harnesses.base import HarnessError
from easel.platform.central import CentralClient
from easel.platform.central_web import create_router
from tests.test_central_platform_client import auth

ORDER = "B" * 32

@pytest.mark.asyncio
async def test_billing_routes_body_origin_guard_and_privacy(tmp_path):
    seen, active = [], [False]
    def handle(r):
        seen.append(r)
        return httpx.Response(200, json={"id": ORDER, "status": "creating", "amount_minor": "1", "credit_state": "pending"})
    transport = httpx.MockTransport(handle)
    native = CentralClient(tmp_path, transport=transport, url="http://127.0.0.1:8791")
    native.save_auth(auth())
    def guard():
        if active[0]: raise HarnessError("tasks active")
    router = create_router(tmp_path, guard, transport=transport, url="http://127.0.0.1:8791")
    app = FastAPI(); app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1",123)), base_url="http://local") as c:
        base = "/api/platform/central"
        assert (await c.get(base+"/billing/catalog")).status_code == 200
        assert str(seen[-1].url).endswith("/v2/billing/catalog?product_id=easel")
        body = {"offer_id":"fixture", "channel":"wechat", "idempotency_key":"stable"}
        created = await c.post(base+"/orders", json=body)
        assert created.status_code == 200, created.text
        assert json.loads(seen[-1].content) == body
        assert "SECRET" not in created.text
        for path in ["/orders", "/orders/"+ORDER]: assert (await c.get(base+path)).status_code == 200
        for action in ["refresh", "close"]: assert (await c.post(base+"/orders/"+ORDER+"/"+action)).status_code == 200
        before = len(seen)
        for extra in [{"wallet":"wrong"}, {"amount_minor":"1"}, {"credits":"100"}, {"url":"https://evil.test"}]:
            assert (await c.post(base+"/orders", json={**body, **extra})).status_code == 400
        assert (await c.post(base+"/orders/"+ORDER+"/refresh", json={"paid":True})).status_code == 400
        assert (await c.post(base+"/orders", json=body, headers={"Origin":"https://evil.test"})).status_code == 403
        assert len(seen) == before
        active[0] = True
        assert (await c.post(base+"/orders", json=body)).status_code == 409
        assert len(seen) == before
        for suffix in ["paid", "confirm", "refund"]: assert (await c.post(base+"/orders/"+ORDER+"/"+suffix)).status_code == 404
    await router.shutdown()
