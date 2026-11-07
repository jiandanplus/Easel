"""Independent, cookie-authenticated management BFF; never a product API bypass."""
from contextlib import asynccontextmanager
import hmac
import json
from pathlib import Path
import re
import secrets
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from .errors import PlatformError


SESSION_COOKIE = "easel_console_session"
LOGIN_COOKIE = "easel_console_login"
CALLBACK = "/oidc/console/callback"
PAGES = {"", "overview", "stats", "redemptions", "admins", "users", "products", "plans", "wallets", "orders", "offers", "models", "providers", "executions", "reconciliation", "audits"}
CSP = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"


def create_console_app(settings=None, *, database=None, auth=None, queries=None, catalog=None, reconciliation=None, static_dir=None, provider_ids=None, payments_mode="disabled"):
    if settings is None:
        from .console_config import ConsoleSettings
        settings = ConsoleSettings.from_env()
    from .db import Database
    db = database or Database(settings.database_url, min_size=settings.pool_min_size, max_size=settings.pool_max_size, schema=settings.db_schema)
    if auth is None:
        from .console_auth import ConsoleAuth
        auth = ConsoleAuth(db, settings)
    if provider_ids is None:
        from .console_registry import provider_registry
        provider_ids = provider_registry()
    if queries is None:
        from .console_service import ConsoleQueries
        queries = ConsoleQueries(db, auth, provider_ids=provider_ids, payments_mode=payments_mode)
    if catalog is None:
        from .console_catalog import ConsoleCatalog
        catalog = ConsoleCatalog(db, auth, provider_ids=provider_ids)
    if reconciliation is None:
        from .console_reconciliation import ConsoleReconciliation
        reconciliation = ConsoleReconciliation(db, auth)
    root = Path(static_dir) if static_dir is not None else Path(__file__).with_name("console_static")
    root = root.resolve()
    expected_host = urlsplit(settings.public_url).netloc.lower()
    secure = urlsplit(settings.public_url).scheme == "https"

    @asynccontextmanager
    async def lifespan(app):
        if database is None:
            from .console_permissions import assert_safe_console_role
            await db.open()
            try:
                async with db.connection() as conn:
                    await assert_safe_console_role(conn)
                yield
            finally:
                await db.close()
        else:
            yield

    app = FastAPI(title="Central operations console", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.database, app.state.auth, app.state.queries, app.state.catalog = db, auth, queries, catalog
    app.state.reconciliation = reconciliation

    def failure(status, code, *, retryable=False):
        return JSONResponse({"error": {"code": code, "retryable": retryable, "correlation_id": secrets.token_hex(8)}}, status_code=status)

    @app.exception_handler(PlatformError)
    async def service_error(request, error):
        if request.url.path == CALLBACK:
            return HTMLResponse("<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\"><title>管理登录未完成</title><h1>管理登录未完成</h1><p>登录窗口可能已经过期。请回到后台重新登录，关闭旧登录页面。</p><a href=\"/\">返回中央运营后台</a></html>", status_code=error.status)
        return failure(error.status, error.code, retryable=error.retryable)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        return failure(422, "invalid_console_request")

    @app.middleware("http")
    async def boundary(request, call_next):
        if request.headers.get("host", "").lower() != expected_host:
            response = failure(400, "invalid_console_host")
        elif request.url.path.startswith("/api/") and (
            (request.headers.get("origin") not in (None, settings.public_url))
            or request.headers.get("sec-fetch-site") == "cross-site"
        ):
            response = failure(403, "console_origin_forbidden")
        else:
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                parts, total = [], 0
                async for part in request.stream():
                    total += len(part)
                    if total > 65536:
                        response = failure(413, "request_too_large")
                        break
                    parts.append(part)
                else:
                    request.state.console_body = b"".join(parts)
                    response = None
            else:
                response = None
            if response is None:
                try:
                    response = await call_next(request)
                except PlatformError as error:
                    response = failure(error.status, error.code, retryable=error.retryable)
                except Exception:
                    response = failure(500, "console_request_failed")
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = CSP
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response

    async def principal(request, permission="console:read", *, write=False):
        actor = await auth.authenticate(request.cookies.get(SESSION_COOKIE, ""))
        if permission not in actor.permissions:
            raise PlatformError(403, "console_permission_required")
        if write:
            token = request.headers.get("x-console-csrf", "")
            if request.headers.get("origin") != settings.public_url or not 1 <= len(token) <= 256 or not hmac.compare_digest(token, actor.csrf_token):
                raise PlatformError(403, "console_csrf_invalid")
        return actor

    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError()
            value[key] = item
        return value

    async def command(request, keys):
        if request.headers.get("content-type", "").partition(";")[0].strip().lower() != "application/json":
            raise PlatformError(415, "console_json_required")
        try:
            value = json.loads(request.state.console_body, object_pairs_hook=pairs)
            if not isinstance(value, dict) or set(value) != set(keys):
                raise ValueError()
        except (ValueError, TypeError, UnicodeError):
            raise PlatformError(400, "invalid_console_request") from None
        reason = value.get("reason")
        key = request.headers.get("idempotency-key", "")
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 512 or any(ord(c) < 32 for c in reason) or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", key):
            raise PlatformError(403, "console_command_proof_required")
        return value, key

    @app.get("/health")
    async def health():
        async with db.connection() as conn:
            await conn.execute("SELECT 1")
        return {"ok": True, "service": "central-console", "assets_ready": (root / "index.html").is_file()}

    @app.get("/auth/login")
    async def login():
        result = await auth.begin_login()
        response = RedirectResponse(result["url"], status_code=302)
        response.set_cookie(LOGIN_COOKIE, result["cookie"], max_age=settings.auth_ttl, path=CALLBACK, httponly=True, secure=secure, samesite="lax")
        return response

    @app.get(CALLBACK)
    async def callback(request: Request, code: str = "", state: str = ""):
        result = await auth.callback(code, state, request.cookies.get(LOGIN_COOKIE, ""))
        if result["status"] == "authenticated":
            response = RedirectResponse("/", status_code=302)
            response.set_cookie(SESSION_COOKIE, result["session_token"], max_age=settings.session_ttl, path="/", httponly=True, secure=secure, samesite="lax")
        elif result["status"] == "pending" and re.fullmatch(r"[A-Za-z0-9_-]{16,128}", result.get("request_id", "")):
            response = HTMLResponse("<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\"><title>等待管理员授权</title><h1>账号验证成功，等待管理员授权</h1><p>普通注册不会自动获得管理权限。请把下方申请编号交给运营者完成授权；无需提供密码或验证码。</p><p>申请编号：<code>" + result["request_id"] + "</code></p><p>授权后请重新登录。</p><a href=\"/\">返回中央运营后台</a></html>", status_code=403)
            response.delete_cookie(SESSION_COOKIE, path="/")
        else:
            raise PlatformError(400, "console_login_invalid")
        response.delete_cookie(LOGIN_COOKIE, path=CALLBACK)
        return response

    @app.post("/auth/logout")
    async def logout(request: Request):
        await principal(request, write=True)
        await auth.logout(request.cookies.get(SESSION_COOKIE, ""))
        response = JSONResponse({"ok": True})
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @app.get("/api/session")
    async def session(request: Request):
        actor = await principal(request)
        return {"admin": {"id": actor.id, "role": actor.role, "permissions": sorted(actor.permissions)}, "csrf_token": actor.csrf_token}

    @app.get("/api/overview")
    async def overview(request: Request):
        return await queries.overview(await principal(request))

    @app.get("/api/reconciliation-cases")
    async def reconciliation_list(request: Request, limit: int = 25, offset: int = 0, q: str = "", financial_status: str = "pending", review_state: str = "all", product_id: str | None = None, funding_mode: str = "all"):
        actor = await principal(request)
        keys = [key for key, _ in request.query_params.multi_items()]
        if len(keys) != len(set(keys)) or set(keys) - {"limit", "offset", "q", "financial_status", "review_state", "product_id", "funding_mode"}:
            raise PlatformError(422, "invalid_console_query")
        return await reconciliation.list(actor, limit=limit, offset=offset, q=q, financial_status=financial_status, review_state=review_state, product_id=product_id, funding_mode=funding_mode)

    @app.get("/api/reconciliation-cases/{job_id}")
    async def reconciliation_detail(request: Request, job_id: str):
        return await reconciliation.detail(await principal(request), job_id)

    @app.get("/api/reconciliation-cases/{job_id}/review-operations")
    async def reconciliation_operation(request: Request, job_id: str):
        return await reconciliation.operation(await principal(request), job_id, request.headers.get("idempotency-key", ""))

    @app.post("/api/reconciliation-cases/{job_id}/reviews")
    async def reconciliation_review(request: Request, job_id: str):
        actor = await principal(request, "execution:review", write=True)
        value, key = await command(request, {"state", "reason", "expected_revision", "snapshot_hash"})
        return await reconciliation.review(actor, job_id, value, key)

    def listing(name):
        async def endpoint(request: Request, limit: int = 25, offset: int = 0, q: str = ""):
            return await getattr(queries, name)(await principal(request), limit=limit, offset=offset, q=q)
        endpoint.__name__ = "console_list_" + name
        return endpoint

    @app.get("/api/users")
    async def users_list(request: Request, limit: int = 25, offset: int = 0, q: str = "", status: str = "", sort: str = "created"):
        return await queries.users(await principal(request), limit=limit, offset=offset, q=q, status=status, sort=sort)

    @app.get("/api/users/{item_id}/activity")
    async def user_activity(request: Request, item_id: str, days: int = 30):
        return await queries.user_activity(await principal(request), item_id, days)

    @app.get("/api/users/{item_id}/usage")
    async def user_usage(request: Request, item_id: str, limit: int = 25, offset: int = 0, model: str = "", status: str = ""):
        return await queries.user_usage(await principal(request), item_id, limit=limit, offset=offset, model=model, status=status)

    @app.get("/api/users/{item_id}/ledger")
    async def user_ledger(request: Request, item_id: str, limit: int = 25, offset: int = 0):
        return await queries.user_ledger(await principal(request), item_id, limit=limit, offset=offset)

    @app.get("/api/users/{item_id}/sessions")
    async def user_sessions(request: Request, item_id: str):
        return await queries.user_sessions(await principal(request), item_id)

    @app.get("/api/users/{item_id}/audits")
    async def user_audits(request: Request, item_id: str):
        return await queries.user_audits(await principal(request), item_id)

    @app.get("/api/redemptions")
    async def redemptions_list(request: Request, limit: int = 25, offset: int = 0, q: str = ""):
        return await queries.redemptions(await principal(request), limit=limit, offset=offset, q=q)

    @app.get("/api/redemptions/{item_id}")
    async def redemption_detail(request: Request, item_id: str):
        return await queries.redemption(await principal(request), item_id)

    @app.post("/api/redemptions")
    async def redemption_create(request: Request):
        actor = await principal(request, "redemption:manage", write=True)
        value, key = await command(request, {"plan_id", "name", "count", "days", "reason"})
        return await catalog.create_redemptions(actor, value["plan_id"], value["name"], value["count"], value["days"], value["reason"], key)

    @app.post("/api/redemptions/{item_id}/disable")
    async def redemption_disable(request: Request, item_id: str):
        actor = await principal(request, "redemption:manage", write=True)
        value, key = await command(request, {"reason"})
        return await catalog.disable_redemptions(actor, item_id, value["reason"], key)

    @app.get("/api/admins")
    async def admins(request: Request):
        return await queries.admins(await principal(request, "admins:manage"))

    @app.post("/api/admin-requests/{item_id}/approve")
    async def admin_approve(request: Request, item_id: str):
        actor = await principal(request, "admins:manage", write=True)
        value, key = await command(request, {"role", "reason"})
        return await catalog.approve_admin(actor, item_id, value["role"], value["reason"], key)

    @app.post("/api/admins/{item_id}/disable")
    async def admin_disable(request: Request, item_id: str):
        actor = await principal(request, "admins:manage", write=True)
        value, key = await command(request, {"reason"})
        return await catalog.disable_admin(actor, item_id, value["reason"], key)

    @app.get("/api/stats")
    async def stats(request: Request, days: int = 30):
        return await queries.stats(await principal(request), days)

    for name in ("products", "plans", "wallets", "orders", "offers", "models", "providers", "executions", "audits"):
        app.add_api_route("/api/" + name, listing(name), methods=["GET"])

    def detail(name):
        async def endpoint(request: Request, item_id: str):
            return await getattr(queries, name)(await principal(request), item_id)
        endpoint.__name__ = "console_detail_" + name
        return endpoint

    for route, method in (("users", "user"), ("wallets", "wallet"), ("orders", "order")):
        app.add_api_route("/api/" + route + "/{item_id}", detail(method), methods=["GET"])

    def publish(name, permission):
        async def endpoint(request: Request):
            actor = await principal(request, permission, write=True)
            value, key = await command(request, {"definition", "reason"})
            return await getattr(catalog, "publish_" + name)(actor, value["definition"], value["reason"], key)
        endpoint.__name__ = "console_publish_" + name
        return endpoint

    for route, method, permission in (("plans", "plan", "plans:publish"), ("offers", "offer", "billing:publish"), ("models", "model", "execution:publish")):
        app.add_api_route("/api/" + route, publish(method, permission), methods=["POST"])

    @app.post("/api/offers/{item_id}/withdraw")
    async def withdraw(request: Request, item_id: str):
        actor = await principal(request, "billing:publish", write=True)
        value, key = await command(request, {"reason"})
        return await catalog.withdraw_offer(actor, item_id, value["reason"], key)

    @app.post("/api/wallets/{item_id}/allowance")
    async def allowance(request: Request, item_id: str):
        actor = await principal(request, "owner:allowance", write=True)
        value, key = await command(request, {"reason"})
        return await catalog.issue_allowance(actor, item_id, value["reason"], key)

    @app.post("/api/wallets/{item_id}/plans")
    async def issue_plan(request: Request, item_id: str):
        actor = await principal(request, "plans:issue", write=True)
        value, key = await command(request, {"plan_id", "reason"})
        return await catalog.issue_plan(actor, item_id, value["plan_id"], value["reason"], key)

    @app.post("/api/users/{item_id}/status")
    async def user_status(request: Request, item_id: str):
        actor = await principal(request, "users:manage", write=True)
        value, key = await command(request, {"active", "reason"})
        return await catalog.set_user_active(actor, item_id, value["active"], value["reason"], key)

    @app.post("/api/users/{item_id}/logout")
    async def user_logout(request: Request, item_id: str):
        actor = await principal(request, "users:manage", write=True)
        value, key = await command(request, {"reason"})
        return await catalog.force_logout(actor, item_id, value["reason"], key)

    @app.post("/api/users/{item_id}/note")
    async def user_note(request: Request, item_id: str):
        actor = await principal(request, "users:manage", write=True)
        value, key = await command(request, {"note", "tags", "reason"})
        return await catalog.set_user_note(actor, item_id, value["note"], value["tags"], value["reason"], key)

    @app.post("/api/models/{item_id}/state")
    async def model_state(request: Request, item_id: str):
        actor = await principal(request, "execution:publish", write=True)
        value, key = await command(request, {"enabled", "reason", "expected_updated_at"})
        if type(value["enabled"]) is not bool or not isinstance(value["expected_updated_at"], str):
            raise PlatformError(400, "invalid_console_request")
        return await catalog.set_model_state(actor, item_id, value["enabled"], value["reason"], key, value["expected_updated_at"])

    @app.get("/assets/{name}")
    async def asset(name: str):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
            raise PlatformError(404, "console_asset_not_found")
        path = (root / "assets" / name).resolve()
        if not path.is_relative_to(root / "assets") or not path.is_file():
            raise PlatformError(404, "console_asset_not_found")
        return FileResponse(path, headers={"Cache-Control": "public, max-age=31536000, immutable"})

    @app.get("/{page:path}")
    async def index(page: str):
        if page not in PAGES:
            raise PlatformError(404, "console_route_not_found")
        entry = root / "index.html"
        if not entry.is_file() or not entry.resolve().is_relative_to(root):
            return HTMLResponse("<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\"><title>中央运营后台</title><h1>管理端资源尚未构建</h1><p>请由运营者完成管理端构建后重试。</p></html>", status_code=503)
        return FileResponse(entry, media_type="text/html", headers={"Cache-Control": "no-store"})

    return app
