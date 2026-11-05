"""Central public API. Business balances are never writable by product clients."""
from contextlib import asynccontextmanager
import asyncio
import json
import re
import secrets
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import Settings
from .credits import CreditLedger
from .db import Database
from .errors import PlatformError
from .execution_config import ExecutionSettings
from .execution_runtime import ExecutionRuntime
from .execution_web import create_execution_router
from .identity import IdentityService
from .membership import MembershipService
from .payment_config import PaymentSettings
from .payment_runtime import PaymentRuntime
from .payment_web import create_payment_router
from .media_config import MediaSettings
from .media_runtime import MediaRuntime
from .media_web import create_media_router


def create_app(settings: Settings | None = None, *, database=None, identity=None, payment_runtime=None, execution_runtime=None, media_runtime=None):
    settings = settings or Settings.from_env()
    db = database or Database(settings.database_url, min_size=settings.pool_min_size, max_size=settings.pool_max_size, schema=settings.db_schema)
    accounts = identity or IdentityService(db, settings)
    memberships, ledger = MembershipService(db), CreditLedger(db)
    payments = payment_runtime or PaymentRuntime(db, PaymentSettings.from_env(settings))
    executions = execution_runtime or ExecutionRuntime(db, accounts, ExecutionSettings.from_env(settings))
    media = media_runtime or MediaRuntime(db, accounts, MediaSettings.from_env(settings))

    @asynccontextmanager
    async def lifespan(app):
        payment_task = None
        execution_task = None
        media_task = None
        if database is None:
            await db.open()
        try:
            if database is None:
                await db.require_runtime_role()
            if payments.settings.mode != "disabled" and payments.settings.worker_enabled:
                payment_task = asyncio.create_task(payments.run_forever())
            if executions.settings.mode != "disabled" and executions.settings.worker_enabled:
                execution_task = asyncio.create_task(executions.run_forever())
            if getattr(media,"start",None) is not None:
                await media.start()
            if media.settings.mode != "disabled" and media.settings.worker_enabled:
                media_task = asyncio.create_task(media.run_forever())
            yield
        finally:
            if payment_task is not None:
                payment_task.cancel()
                await asyncio.gather(payment_task, return_exceptions=True)
            if execution_task is not None:
                execution_task.cancel()
                await asyncio.gather(execution_task, return_exceptions=True)
            if media_task is not None:
                media_task.cancel()
                await asyncio.gather(media_task, return_exceptions=True)
            await media.close()
            await executions.close()
            if database is None:
                await db.close()

    app = FastAPI(title="Central accounts and points", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.database, app.state.identity = db, accounts
    app.state.payment_runtime = payments
    app.state.execution_runtime = executions
    app.state.media_runtime = media
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[urlsplit(settings.public_url).hostname])

    def failure(status, code, *, retryable=False):
        return JSONResponse({"error": {"code": code, "retryable": retryable, "correlation_id": secrets.token_hex(8)}}, status_code=status)

    from .media_maintenance.contracts import MaintenanceError
    @app.exception_handler(MaintenanceError)
    async def media_maintenance_failure(request, error):
        return failure(503,'media_maintenance_unavailable',retryable=False)

    def protocol_failure(request, error):
        # Only explicit HTML navigation to these two GET routes gets a recovery
        # page. Its content is static: never reflect codes, claims, URLs or errors.
        html_navigation = False
        if request.method == "GET" and re.fullmatch(r"/oidc/[^/]+/(?:authorize|callback)", request.url.path):
            for item in request.headers.get("accept", "").split(","):
                media, *parameters = item.split(";")
                if media.strip().lower() != "text/html":
                    continue
                quality = next((parameter.partition("=")[2].strip() for parameter in parameters
                                if parameter.partition("=")[0].strip().lower() == "q"), "1")
                try:
                    html_navigation = 0 < float(quality) <= 1
                except ValueError:
                    html_navigation = False
                if html_navigation:
                    break
        if html_navigation:
            return HTMLResponse(
                "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
                "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
                "<title>登录未完成 · Easel</title></head><body><main>"
                "<h1>登录未完成</h1><p>登录已过期，或无法确认本次登录上下文。</p>"
                "<p>请回到 Easel 工作台，点击左下角账号入口，取消尚未完成的登录后重新登录。</p>"
                "<p>请关闭此页面，不要刷新或重复提交旧登录链接。已完成的注册仍然有效。</p>"
                "</main></body></html>",
                status_code=error.status,
                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                         "X-Content-Type-Options": "nosniff",
                         "Content-Security-Policy": "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"},
            )
        return failure(error.status, error.code, retryable=error.retryable)

    @app.exception_handler(PlatformError)
    async def platform_failure(request, error):
        return protocol_failure(request, error)

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        # Native/local bridges make business requests without Origin. The only
        # browser protocol endpoints are fixed OIDC redirects; no permissive CORS.
        if request.headers.get("origin") and not request.url.path.startswith("/oidc/"):
            return failure(403, "browser_api_forbidden")
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path != "/v2/media/assets":
            chunks, size = [], 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > (1024 * 1024 if request.url.path == "/v2/responses" else 65536):
                    return failure(413, "request_too_large")
                chunks.append(chunk)
            request.state.bounded_body = b"".join(chunks)
        try:
            response = await call_next(request)
        except PlatformError as error:
            return protocol_failure(request, error)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    async def body(request):
        try:
            result = json.loads(request.state.bounded_body)
            if not isinstance(result, dict):
                raise ValueError
            return result
        except (ValueError, UnicodeError):
            raise PlatformError(400, "invalid_request") from None

    async def scope(request, product=None):
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer ") or not 16 <= len(auth[7:]) <= 4096:
            raise PlatformError(401, "invalid_authentication")
        return await accounts.authenticate(auth[7:], product_id=product)

    async def product(value):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
            raise PlatformError(400, "invalid_product")
        async with db.connection() as conn:
            found = await (await conn.execute("SELECT 1 FROM products WHERE id=%s AND enabled", (value,))).fetchone()
        if not found:
            raise PlatformError(404, "product_not_found")
        return value

    @app.get("/health")
    async def health():
        async with db.connection() as conn:
            await conn.execute("SELECT 1")
        return {"ok": True, "stage": "accounts_plans_wallet"}

    @app.get("/v2/capabilities")
    async def capabilities(product_id: str = "easel"):
        await product(product_id)
        catalog = await payments.billing.catalog(product_id)
        ready = bool(catalog["channels"] and catalog["offers"])
        execution_capabilities = executions.capabilities(product_id)
        if asyncio.iscoroutine(execution_capabilities):
            execution_capabilities = await execution_capabilities
        media_capabilities = await media.capabilities(product_id)
        return {"product_id": product_id, "auth_mode": "logto", "ledger_mode": "central_credit", "billing_stage": payments.settings.mode, "payments_enabled": payments.settings.mode == "live" and ready,
                "test_payments_enabled": payments.settings.mode == "test" and ready,
                **execution_capabilities, **media_capabilities, "identity_ready": settings.identity_ready and product_id in settings.oidc_clients}

    @app.get("/v2/plans")
    async def plans(product_id: str = "easel"):
        await product(product_id)
        return {"plans": await memberships.plans(product_id)}

    @app.post("/v2/auth/transactions")
    async def begin(request: Request):
        if not settings.identity_ready:
            raise PlatformError(503, "identity_not_configured")
        data = await body(request)
        try:
            return await accounts.begin_login(**{key: data[key] for key in ("product_id", "client_id", "callback_uri", "handoff_challenge", "local_state")})
        except (KeyError, TypeError, ValueError):
            raise PlatformError(400, "invalid_request") from None

    @app.get("/oidc/{product_id}/authorize")
    async def authorize(product_id: str, transaction_id: str):
        await product(product_id)
        if not settings.identity_ready:
            raise PlatformError(503, "identity_not_configured")
        result = await accounts.authorize(transaction_id, expected_product=product_id)
        # authorize URL must belong to the route product before browser navigation.
        result_product = result.get("product_id", product_id)
        if result_product != product_id:
            raise PlatformError(400, "product_mismatch")
        response = RedirectResponse(result["url"], status_code=302)
        response.set_cookie("central_oidc_" + product_id, result["cookie"], max_age=settings.auth_ttl, httponly=True,
                            secure=urlsplit(settings.public_url).scheme == "https", samesite="lax", path=f"/oidc/{product_id}/callback")
        return response

    @app.get("/oidc/{product_id}/callback")
    async def callback(request: Request, product_id: str, code: str = "", state: str = ""):
        await product(product_id)
        result = await accounts.callback(product_id, code, state, request.cookies.get("central_oidc_" + product_id, ""))
        response = RedirectResponse(result["redirect_uri"], status_code=302)
        response.delete_cookie("central_oidc_" + product_id, path=f"/oidc/{product_id}/callback")
        return response

    @app.post("/v2/auth/redeem")
    async def redeem(request: Request):
        return await accounts.redeem(await body(request))

    @app.post("/v2/auth/refresh")
    async def refresh(request: Request):
        token = (await body(request)).get("refresh_token")
        if not isinstance(token, str) or not 16 <= len(token) <= 4096:
            raise PlatformError(400, "invalid_request")
        return await accounts.refresh(token)

    @app.post("/v2/auth/logout")
    async def logout(request: Request):
        await scope(request)
        await accounts.logout(request.headers["authorization"][7:])
        return {"ok": True}

    @app.get("/v2/me")
    async def me(request: Request):
        return await accounts.me(await scope(request))

    @app.get("/v2/products/{product_id}/entitlements")
    async def entitlements(request: Request, product_id: str):
        return await memberships.entitlements(await scope(request, product_id))

    @app.get("/v2/wallet")
    async def wallet(request: Request):
        return await ledger.summary(await scope(request))

    @app.get("/v2/usage")
    async def usage(request: Request):
        return {"usage": await ledger.usage(await scope(request))}

    @app.get("/v2/sessions")
    async def sessions(request: Request):
        return {"sessions": await accounts.sessions(await scope(request))}

    @app.delete("/v2/sessions/{session_id}")
    async def revoke(request: Request, session_id: str):
        await accounts.revoke(await scope(request), session_id)
        return {"ok": True}

    app.include_router(create_payment_router(payments, scope, product))
    app.include_router(create_execution_router(executions, scope, product))
    app.include_router(create_media_router(media, scope, product))
    return app
