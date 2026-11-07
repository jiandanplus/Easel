from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest

from platform_service.app import create_app
from platform_service.config import Settings
from platform_service.contracts import Scope
from platform_service.errors import PlatformError


class Database:
    @asynccontextmanager
    async def connection(self): yield self
    async def execute(self, *args): return self
    async def fetchone(self): return {"id": "easel"}


class Identity:
    async def authenticate(self, token, product_id=None):
        if token != "product-private-token": raise PlatformError(401, "invalid_authentication")
        return Scope("user", "easel", "tenant", "wallet", "session", 0)


class Media:
    def __init__(self): self.calls = []
    async def catalog(self, scope): self.calls.append("catalog"); return {"models": []}
    async def mint(self, scope, data): self.calls.append("mint"); return {"task_token": "short-media-private"}
    async def submit(self, token, key, body): self.calls.append(("submit", token, key)); return {"id": "job", "financial_state": "settled"}
    async def upload_asset(self, token, chunks, **kwargs):
        self.calls.append(("upload", token)); raw = b"".join([part async for part in chunks]); return {"id": "asset", "size": len(raw)}
    async def artifact(self, scope, identifier, content=False):
        return (b"verified", "image/png", "a" * 64) if content else {"id": identifier, "mime": "image/png"}


def app():
    media = Media()
    value = create_app(Settings("postgresql://localhost/unused", "https://accounts.example"), database=Database(), identity=Identity(), media_runtime=media)
    return value, media


@pytest.mark.asyncio
async def test_media_public_routes_require_product_or_task_credentials_and_never_accept_browser_origins():
    value, media = app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=value), base_url="https://accounts.example") as client:
        assert (await client.get("/v2/products/easel/media-models")).status_code == 401
        assert (await client.get("/v2/products/easel/media-models", headers={"Authorization": "Bearer product-private-token"})).json() == {"models": []}
        assert (await client.post("/v2/media/jobs", json={}, headers={"Authorization": "Bearer short-media-private", "Idempotency-Key": "one", "Origin": "https://accounts.example"})).status_code == 403
        assert (await client.post("/v2/media/jobs", json={})).status_code == 401
        response = await client.post("/v2/media/jobs", json={"model": "public"}, headers={"Authorization": "Bearer short-media-private", "Idempotency-Key": "one"})
        assert response.status_code == 200 and ("submit", "short-media-private", "one") in media.calls


@pytest.mark.asyncio
async def test_asset_binary_stream_is_separate_from_bounded_json_and_response_is_private():
    value, media = app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=value), base_url="https://accounts.example") as client:
        raw = b"x" * 70000
        response = await client.post("/v2/media/assets", content=raw, headers={"Authorization": "Bearer short-media-private", "Content-Type": "image/png"})
        assert response.status_code == 200 and response.json()["size"] == len(raw)
        assert response.headers["cache-control"] == "no-store"
        large_json = await client.post("/v2/media/jobs", content=b"x" * 70000, headers={"Authorization": "Bearer short-media-private"})
        assert large_json.status_code == 413
        artifact = await client.get("/v2/media/artifacts/image-one/content", headers={"Authorization": "Bearer product-private-token"})
        assert artifact.content == b"verified" and artifact.headers["content-type"] == "image/png"
        assert "attachment" in artifact.headers["content-disposition"] and artifact.headers["etag"] == '"' + "a" * 64 + '"'
