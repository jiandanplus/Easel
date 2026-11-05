from types import SimpleNamespace

import httpx
import pytest
from starlette.responses import JSONResponse

from platform_service.app import create_app
from platform_service.config import Settings
from platform_service.errors import PlatformError


class Identity:
    async def authenticate(self, token, product_id=None):
        if token != "product-private-token":
            raise PlatformError(401, "invalid_authentication")
        return SimpleNamespace(product_id=product_id or "easel")


class Runtime:
    settings = SimpleNamespace(mode="test", worker_enabled=False)

    def __init__(self):
        self.service = self
        self.received = []

    async def catalog(self, scope):
        return {"models": [{"id": "fixture", "test_only": True}]}

    async def mint(self, scope, body):
        return {"task_token": "private-minted", "model": body["model"]}

    async def respond(self, token, key, body):
        self.received.append((token, key, body))
        if token != "task-private-token":
            raise PlatformError(401, "invalid_authentication")
        return JSONResponse({"id": "fixture-response"})

    async def list(self, scope):
        return [{"id": "job", "status": "pending_reconciliation"}]

    async def get(self, scope, id):
        return {"id": id}

    async def cancel(self, scope, id):
        return {"id": id, "status": "cancelled"}

    def capabilities(self, product):
        return {"subscription_execution_enabled": False, "test_execution_enabled": True}

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_routes_use_distinct_bearers_body_limits_and_no_public_settlement(db):
    runtime = Runtime()
    async with db.connection() as conn:
        await conn.execute("INSERT INTO products(id) VALUES('easel')")
    app = create_app(
        Settings("postgresql://localhost/unused", "https://accounts.example"),
        database=db,
        identity=Identity(),
        execution_runtime=runtime,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://accounts.example"
    ) as client:
        headers = {"Authorization": "Bearer product-private-token"}
        assert (await client.get("/v2/products/easel/models", headers=headers)).json()[
            "models"
        ][0]["test_only"]
        assert (
            await client.post(
                "/v2/task-tokens",
                headers=headers,
                json={"model": "fixture", "invocation_key": "one"},
            )
        ).json()["task_token"] == "private-minted"
        assert (await client.get("/v2/executions", headers=headers)).json()[
            "executions"
        ][0]["status"] == "pending_reconciliation"
        assert (await client.get("/v2/executions/job", headers=headers)).json()[
            "id"
        ] == "job"
        assert (
            await client.post("/v2/executions/job/cancel", headers=headers, json={})
        ).json()["status"] == "cancelled"
        assert (await client.get("/v2/capabilities")).json()[
            "test_execution_enabled"
        ] is True
        task = {"Authorization": "Bearer task-private-token", "Idempotency-Key": "op"}
        assert (
            await client.post(
                "/v2/responses", headers=task, json={"input": "x" * 70000}
            )
        ).status_code == 200
        assert (
            await client.post(
                "/v2/responses", headers=task, content=b"x" * (1024 * 1024 + 1)
            )
        ).status_code == 413
        assert (
            await client.post("/v2/task-tokens", headers=headers, content=b"x" * 70000)
        ).status_code == 413
        assert (
            await client.post("/v2/responses", headers=headers, json={})
        ).status_code == 401
        assert (await client.get("/v2/executions", headers=task)).status_code == 401
        assert (
            await client.post("/v2/executions/job/finish", headers=headers, json={})
        ).status_code == 404
