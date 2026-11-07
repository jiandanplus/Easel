"""Owned native login callback and loopback-only central account API."""

import asyncio
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import inspect
import json
import re
import secrets
import threading
import time
from urllib.parse import parse_qs, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request

from easel.harnesses.base import HarnessError
from .central import CentralClient
from .client import PlatformError
from .web import require_local_request


class _Listener:
    """Only this instance owns its socket; handler logs never include query codes."""

    def __init__(self, loop, complete):
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                status = 400
                try:
                    parsed = urlsplit(self.path)
                    if len(self.path) > 4096 or parsed.path != "/platform-auth/callback":
                        raise ValueError
                    query = parse_qs(parsed.query, strict_parsing=True, max_num_fields=4)
                    if set(query) != {"state", "code"} or any(len(v) != 1 for v in query.values()):
                        raise ValueError
                    # All state checks and mutations run on the owning ASGI loop.
                    future = asyncio.run_coroutine_threadsafe(complete(owner, query["code"][0], query["state"][0]), loop)
                    status = future.result(timeout=25)
                except Exception:
                    status = 400
                content = ("登录已完成，可以关闭此页面。" if status == 200 else "登录未完成，请返回优创重新登录。").encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                try:
                    self.wfile.write(content)
                except OSError:
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.server.timeout = 1
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
        self.uri = f"http://127.0.0.1:{self.server.server_port}/platform-auth/callback"
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


STATUS_BUDGET_SECONDS = 15


def create_router(workspace, guard, close_idle=None, *, transport=None, url=None, login_timeout=300):
    router = APIRouter(prefix="/api/platform/central", dependencies=[Depends(require_local_request)])
    router.platform_changes = 0
    pending = None
    login_error = None
    callbacks = set()
    # A cancel racing the final disk commit is ordered before or after that
    # commit. It cannot report cancellation and then allow a late private write.
    persistence = asyncio.Lock()

    def root():
        return workspace() if callable(workspace) else workspace

    def client():
        return CentralClient(root(), transport=transport, url=url)

    def checked_guard():
        try:
            guard()
        except HarnessError as error:
            raise HTTPException(409, {"code": "tasks_active", "message": str(error)}) from None

    async def invoke(method, *args, **kwargs):
        try:
            return await asyncio.to_thread(method, *args, **kwargs)
        except PlatformError as error:
            raise HTTPException(error.status, {"code": error.code, "message": str(error)}) from None
        except HarnessError as error:
            raise HTTPException(409, {"code": "platform_state_invalid", "message": str(error)}) from None

    async def changed(method, *args, **kwargs):
        checked_guard()
        router.platform_changes += 1
        try:
            return await invoke(method, *args, **kwargs)
        finally:
            router.platform_changes -= 1

    async def _close_current(error=None):
        nonlocal pending, login_error
        current, pending = pending, None
        login_error = error
        if current:
            current["cancelled"] = True
            timer = current.get("timer")
            if timer and timer is not asyncio.current_task():
                timer.cancel()
            await asyncio.to_thread(current["listener"].close)

    async def close_current(error=None):
        async with persistence:
            await _close_current(error)

    async def completion(listener, code, state):
        nonlocal pending, login_error
        current = pending
        if not current or current["listener"] is not listener or current.get("consumed"):
            return 409
        if not secrets.compare_digest(state, current["state"]) or not 1 <= len(code) <= 4096:
            return 400
        current["consumed"] = True
        task = asyncio.current_task()
        callbacks.add(task)
        auth, committed = None, False
        try:
            checked_guard()
            router.platform_changes += 1
            try:
                if current["expires_at"] <= time.time():
                    raise HTTPException(409, {"code": "login_expired", "message": "登录已超时，请重试"})
                auth = await invoke(current["client"].redeem, code, current["verifier"], listener.uri, state)
                async with persistence:
                    checked_guard()
                    if pending is not current or current["cancelled"] or root() != current["root"]:
                        raise HTTPException(409, {"code": "login_cancelled", "message": "登录已取消，请重试"})
                    if current["expires_at"] <= time.time():
                        raise HTTPException(409, {"code": "login_expired", "message": "登录已超时，请重试"})
                    await invoke(current["client"].save_auth, auth)
                    committed = True
                if close_idle:
                    result = close_idle()
                    if inspect.isawaitable(result):
                        await result
            finally:
                router.platform_changes -= 1
            return 200
        except HTTPException as error:
            detail = error.detail
            login_error = detail if isinstance(detail, dict) else {"code": "login_failed", "message": "登录未完成，请重试"}
            return error.status_code
        finally:
            if auth and not committed:
                try:
                    await invoke(current["client"].discard_auth, auth)
                except HTTPException:
                    pass  # No local token is persisted if remote cleanup is unavailable.
            if pending is current:
                await close_current(login_error)
            callbacks.discard(task)

    async def expire(current):
        await asyncio.sleep(max(0, current["expires_at"] - time.time()))
        if pending is current:
            await close_current({"code": "login_expired", "message": "登录已超时，请重试"})

    async def local_status(*, verify=True):
        deadline = time.monotonic() + STATUS_BUDGET_SECONDS
        native = CentralClient(root(), transport=transport, url=url, absolute_deadline=deadline)
        try:
            # This bounds executor admission as well as awaiting the result.
            # A cancelled to_thread worker still owns its internal deadline;
            # expiry prevents queued work from touching auth or starting HTTP.
            async with asyncio.timeout(max(.001, deadline - time.monotonic())):
                result = await invoke(native.status, verify=verify)
                result["login"] = {"pending": bool(pending)}
                if pending:
                    result["login"]["expires_at"] = pending["expires_at"]
                if login_error:
                    result["login"]["error"] = login_error
                if result["configured"]:
                    try:
                        result["capabilities"] = await invoke(native.call, "GET", "/v2/capabilities?product_id=easel", public=True)
                    except HTTPException as error:
                        if not result.get("error"):
                            result["error"] = error.detail
                return result
        except TimeoutError:
            raise HTTPException(503, {"code": "platform_unavailable", "message": "中央平台暂不可用，请稍后重试"}) from None

    @router.get("/status")
    async def status():
        return await local_status()

    @router.post("/auth/start")
    async def start():
        nonlocal pending, login_error
        checked_guard()
        if router.platform_changes:
            raise HTTPException(409, {"code": "account_changing", "message": "账号正在修改，请稍后重试"})
        router.platform_changes += 1
        try:
            await close_current()
            native = client()
            current = {"state": secrets.token_urlsafe(32), "verifier": secrets.token_urlsafe(48),
                       "cancelled": False, "client": native, "root": root(), "expires_at": time.time() + login_timeout}
            current["listener"] = _Listener(asyncio.get_running_loop(), completion)
            pending = current
            challenge = base64.urlsafe_b64encode(hashlib.sha256(current["verifier"].encode()).digest()).rstrip(b"=").decode()
            value = await invoke(native.begin, current["listener"].uri, challenge, current["state"])
            if pending is not current:
                raise HTTPException(409, {"code": "login_cancelled", "message": "登录已取消"})
            current["expires_at"] = min(current["expires_at"], value["expires_at"])
            current["timer"] = asyncio.create_task(expire(current))
            return {"authorization_url": value["authorize_url"], "expires_at": current["expires_at"]}
        except BaseException:
            await close_current()
            raise
        finally:
            router.platform_changes -= 1

    @router.post("/auth/cancel")
    async def cancel():
        await close_current()
        return await local_status(verify=False)

    @router.post("/logout")
    async def logout():
        checked_guard()
        await close_current()
        await changed(client().logout)
        return await local_status(verify=False)

    @router.post("/configure")
    async def configure(request: Request):
        try:
            raw = await request.body()
            if len(raw) > 4096:
                raise ValueError
            data = await request.json()
            if not isinstance(data, dict) or set(data) != {"url"}:
                raise ValueError
        except (ValueError, UnicodeError):
            raise HTTPException(400, {"code": "invalid_request", "message": "中央平台配置无效"}) from None
        await changed(client().configure, data["url"])
        return await local_status(verify=False)

    def add_read(name, target, public=False):
        async def read():
            return await invoke(client().call, "GET", target, public=public)
        router.add_api_route("/" + name, read, methods=["GET"])

    for name, target, public in (("plans", "/v2/plans?product_id=easel", True),
                                 ("billing/catalog", "/v2/billing/catalog?product_id=easel", True),
                                 ("orders", "/v2/orders", False),
                                 ("models", "/v2/products/easel/models", False),
                                 ("executions", "/v2/executions", False),
                                 ("wallet", "/v2/wallet", False),
                                 ("entitlements", "/v2/products/easel/entitlements", False),
                                 ("usage", "/v2/usage", False), ("devices", "/v2/sessions", False)):
        add_read(name, target, public)

    @router.get("/execution-recoveries")
    async def execution_recoveries():
        return await invoke(client().recoveries)

    def media_client():
        from .media import MediaClient
        return MediaClient(root(), transport=transport, url=url)

    @router.get("/media-models")
    async def media_models():
        return await invoke(media_client().catalog)

    @router.get("/media-recoveries")
    async def media_recoveries():
        return await invoke(media_client().recoveries)

    @router.get("/media-recoveries/turn")
    async def turn_media_recoveries(request: Request):
        from .execution_records import operation_turn_key
        try:
            session_ids = request.query_params.getlist("session_id")
            turn_ids = request.query_params.getlist("turn_id")
            if (set(request.query_params) != {"session_id", "turn_id"}
                    or len(session_ids) != 1 or len(turn_ids) != 1):
                raise ValueError
            operation_turn_key(session_ids[0], turn_ids[0])
        except ValueError:
            raise HTTPException(400, {"code": "invalid_request", "message": "请求轮次标识无效"}) from None
        return await invoke(media_client().recoveries, session_id=session_ids[0], turn_id=turn_ids[0])

    @router.get("/execution-recoveries/turn")
    async def turn_execution_recoveries(request: Request):
        from .execution_records import operation_turn_key
        try:
            session_ids = request.query_params.getlist("session_id")
            turn_ids = request.query_params.getlist("turn_id")
            if len(session_ids) != 1 or len(turn_ids) != 1:
                raise ValueError
            operation_turn_key(session_ids[0], turn_ids[0])
        except ValueError:
            raise HTTPException(400, {"code": "invalid_request", "message": "请求轮次标识无效"}) from None
        return await invoke(client().recoveries, session_id=session_ids[0], turn_id=turn_ids[0])

    async def billing_body(request, *, create=False):
        origin = request.headers.get("origin")
        # Browser writes must come from this local UI; CLI loopback requests have no Origin.
        if origin and origin != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "请求来源无效")
        try:
            raw = await request.body()
            if len(raw) > 4096:
                raise ValueError
            data = await request.json() if raw else None
            if create:
                if not isinstance(data, dict) or set(data) != {"offer_id", "channel", "idempotency_key"}:
                    raise ValueError
            elif data not in (None, {}):
                raise ValueError
            return data if create else None
        except (ValueError, UnicodeError):
            raise HTTPException(400, {"code": "invalid_request", "message": "购买请求格式无效"}) from None

    def order_path(order_id):
        if not re.fullmatch(r"[A-Za-z0-9]{32}", order_id):
            raise HTTPException(400, "订单标识无效")
        return "/v2/orders/" + order_id

    @router.post("/orders")
    async def create_order(request: Request):
        data = await billing_body(request, create=True)
        return await changed(client().call, "POST", "/v2/orders", data)

    @router.post("/redemptions")
    async def redeem(request: Request):
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "请求来源无效")
        try:
            raw = await request.body()
            if len(raw) > 512:
                raise ValueError
            data = json.loads(raw)
            if not isinstance(data, dict) or set(data) != {"code"} or not isinstance(data["code"], str):
                raise ValueError
        except (ValueError, UnicodeError):
            raise HTTPException(400, {"code": "invalid_request", "message": "兑换码格式无效"}) from None
        return await changed(client().call, "POST", "/v2/redemptions", {"code": data["code"].strip()})

    @router.get("/orders/{order_id}")
    async def get_order(order_id: str):
        return await invoke(client().call, "GET", order_path(order_id))

    @router.post("/orders/{order_id}/refresh")
    async def refresh_order(order_id: str, request: Request):
        await billing_body(request)
        return await changed(client().call, "POST", order_path(order_id) + "/refresh")

    @router.post("/orders/{order_id}/close")
    async def close_order(order_id: str, request: Request):
        await billing_body(request)
        return await changed(client().call, "POST", order_path(order_id) + "/close")

    @router.delete("/devices/{session_id}")
    async def revoke(session_id: str):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", session_id):
            raise HTTPException(400, "设备标识无效")
        return await changed(client().call, "DELETE", "/v2/sessions/" + session_id)

    async def shutdown():
        await close_current()
        for task in list(callbacks):
            task.cancel()
        if callbacks:
            await asyncio.gather(*list(callbacks), return_exceptions=True)

    router.shutdown = shutdown
    return router
