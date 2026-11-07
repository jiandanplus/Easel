"""Scoped public execution routes; settlement remains an internal trusted call."""

from fastapi import APIRouter, Request

from .errors import PlatformError
from .execution_protocol import parse_json


def create_execution_router(runtime, require_scope, require_product):
    router = APIRouter()

    @router.get("/v2/products/{product_id}/models")
    async def models(request: Request, product_id: str):
        await require_product(product_id)
        return await runtime.catalog(await require_scope(request, product_id))

    @router.post("/v2/task-tokens")
    async def mint(request: Request):
        return await runtime.mint(
            await require_scope(request), parse_json(request.state.bounded_body)
        )

    @router.post("/v2/responses")
    async def responses(request: Request):
        auth = request.headers.get("authorization", "")
        if (
            not auth.startswith("Bearer ")
            or not 16 <= len(auth[7:]) <= 4096
            or not auth[7:].isascii()
        ):
            raise PlatformError(401, "invalid_authentication")
        return await runtime.respond(
            auth[7:],
            request.headers.get("idempotency-key"),
            parse_json(request.state.bounded_body),
        )

    @router.get("/v2/executions")
    async def executions(request: Request):
        return {"executions": await runtime.service.list(await require_scope(request))}

    @router.get("/v2/execution-operations")
    async def operation(request: Request, invocation_id: str, operation_key: str):
        return await runtime.service.lookup_operation(
            await require_scope(request), invocation_id, operation_key
        )

    @router.get("/v2/executions/{id}")
    async def execution(request: Request, id: str):
        return await runtime.service.get(await require_scope(request), id)

    @router.post("/v2/executions/{id}/cancel")
    async def cancel(request: Request, id: str):
        scope = await require_scope(request)
        if request.state.bounded_body and parse_json(request.state.bounded_body):
            raise PlatformError(400, "invalid_request")
        return await runtime.service.cancel(scope, id)

    return router
