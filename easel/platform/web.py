"""Origin-protected local API: platform credentials never leave native bridge."""

import asyncio
import inspect
import re
from fastapi import APIRouter, HTTPException, Request
from easel.harnesses.base import HarnessError
from .client import PlatformClient, PlatformError


def create_router(workspace, guard, close_idle=None, *, transport=None):
    router = APIRouter(prefix="/api/platform")

    def client():
        return PlatformClient(
            workspace() if callable(workspace) else workspace, transport=transport
        )

    async def invoke(method, *args, **kwargs):
        try:
            return await asyncio.to_thread(method, *args, **kwargs)
        except PlatformError as error:
            raise HTTPException(
                error.status, {"code": error.code, "message": str(error)}
            ) from None
        except HarnessError as error:
            raise HTTPException(
                409, {"code": "platform_state_invalid", "message": str(error)}
            ) from None

    async def change(method, *args, **kwargs):
        try:
            guard()
        except HarnessError as error:
            raise HTTPException(
                409, {"code": "tasks_active", "message": str(error)}
            ) from None
        # No await between admission guard and marking the mutation busy. The app guard
        # also sees this counter before task acceptance, closing the refresh/login race.
        router.platform_changes += 1
        try:
            result = await invoke(method, *args, **kwargs)
            if close_idle:
                closed = close_idle()
                if inspect.isawaitable(closed):
                    await closed
            return result
        finally:
            router.platform_changes -= 1

    router.platform_changes = 0

    async def body(request):
        try:
            raw = await request.body()
            if len(raw) > 16384:
                raise ValueError
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError
            return data
        except (ValueError, UnicodeError):
            raise HTTPException(
                400, {"code": "invalid_request", "message": "平台请求格式无效"}
            ) from None

    @router.post("/configure")
    async def configure(request: Request):
        data = await body(request)
        return await change(client().configure, data.get("url"))

    @router.post("/register")
    @router.post("/login")
    async def login(request: Request):
        data = await body(request)
        if not all(isinstance(data.get(k), str) for k in ("email", "password")):
            raise HTTPException(400, "平台请求格式无效")
        return await change(
            client().login,
            data["email"],
            data["password"],
            data.get("device_name", "Easel"),
            register=request.url.path.endswith("/register"),
        )

    @router.get("/status")
    async def status():
        return await invoke(client().status)

    @router.post("/logout")
    async def logout():
        return await change(client().logout)

    @router.get("/devices")
    async def devices():
        return await invoke(client().call, "GET", "/v1/sessions")

    @router.delete("/devices/{session_id}")
    async def revoke(session_id: str):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", session_id):
            raise HTTPException(400, "设备标识无效")
        return await change(client().call, "DELETE", "/v1/sessions/" + session_id)

    @router.get("/plans")
    async def plans():
        return await invoke(client().call, "GET", "/v1/plans", public=True)

    @router.get("/entitlements")
    async def entitlements():
        return await invoke(client().call, "GET", "/v1/entitlements")

    @router.get("/usage")
    async def usage():
        return await invoke(client().call, "GET", "/v1/usage")

    @router.get("/models")
    async def models():
        return await invoke(client().models)

    return router
