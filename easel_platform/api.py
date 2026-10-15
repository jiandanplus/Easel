import json
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from .auth import PlatformError
from .store import Store
from .gateway import Gateway, read_body

MAX_BODY = 16384

def create_app(settings, *, transport=None, clock=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.ledger.recover()
        yield

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None,lifespan=lifespan)
    store = Store(settings, clock=clock or time.time)
    app.state.store, app.state.settings, app.state.transport = store, settings, transport

    gateway=Gateway(store,transport)
    app.state.gateway,app.state.ledger=gateway,gateway.ledger

    @app.exception_handler(PlatformError)
    async def platform_error(request, exc):
        return JSONResponse({"error":{"code":exc.code}},status_code=exc.status)

    @app.middleware("http")
    async def reject_origin(request, call_next):
        if request.headers.get("origin"):
            return JSONResponse({"error":{"code":"origin_forbidden"}},status_code=403)
        return await call_next(request)

    async def body(request):
        data=bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data)>MAX_BODY: raise PlatformError(400,"invalid_request")
        try:
            value=json.loads(data)
            if not isinstance(value,dict): raise ValueError()
            return value
        except (ValueError,UnicodeError): raise PlatformError(400,"invalid_request") from None

    def authenticated(request):
        auth=request.headers.get("authorization","")
        if not auth.startswith("Bearer ") or len(auth)>512: raise PlatformError(401,"invalid_authentication")
        return store.authenticate(auth[7:])

    @app.post("/v1/auth/register", status_code=201)
    async def register(request: Request):
        store.rate_limit("register",request.client.host if request.client else "unknown")
        data=await body(request)
        return await run_in_threadpool(store.register, data.get("email"),data.get("password"),data.get("device_name","device"))

    @app.post("/v1/auth/login")
    async def login(request: Request):
        store.rate_limit("login",request.client.host if request.client else "unknown")
        data=await body(request)
        return await run_in_threadpool(store.login, data.get("email"),data.get("password"),data.get("device_name","device"))

    @app.post("/v1/auth/refresh")
    async def refresh(request: Request):
        store.rate_limit("refresh",request.client.host if request.client else "unknown")
        data=await body(request)
        token=data.get("refresh_token")
        if not isinstance(token,str) or len(token)>512 or not token.isascii(): raise PlatformError(400,"invalid_request")
        return store.refresh(token)

    @app.post("/v1/auth/logout")
    async def logout(request: Request):
        user=authenticated(request)
        store.logout(user["account_id"],user["session_id"])
        return {"ok":True}

    @app.get("/v1/account")
    async def account(request: Request):
        user=authenticated(request)
        return {"account":user["account"],"session_id":user["session_id"]}

    @app.get("/v1/sessions")
    async def sessions(request: Request):
        return {"sessions":store.list_sessions(authenticated(request)["account_id"])}

    @app.delete("/v1/sessions/{session_id}")
    async def revoke(session_id: str, request: Request):
        store.revoke_session(authenticated(request)["account_id"],session_id)
        return {"ok":True}

    @app.get("/v1/plans")
    async def plans(): return {"plans":store.plans()}

    @app.get("/v1/entitlements")
    async def entitlements(request: Request):
        return {"entitlements":store.entitlements(authenticated(request)["account_id"])}

    @app.get("/v1/models")
    async def models(request: Request):
        return gateway.models(authenticated(request)["account_id"])

    @app.get("/v1/usage")
    async def usage(request: Request):
        return gateway.ledger.snapshot(authenticated(request)["account_id"])

    @app.post("/v1/task-tokens")
    async def task_tokens(request: Request):
        user=authenticated(request)
        data=await read_body(request,MAX_BODY)
        return gateway.mint(user,data.get("model"))

    @app.post("/v1/responses")
    async def responses(request: Request):
        return await gateway.responses(request)

    return app
