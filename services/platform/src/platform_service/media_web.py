"""Media product-session and short task-token routes; no browser key surface."""
from fastapi import APIRouter, Request
from fastapi.responses import Response
from .errors import PlatformError
from .execution_protocol import parse_json


def task_token(request):
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer ") or not 16 <= len(auth[7:]) <= 4096 or not auth[7:].isascii():
        raise PlatformError(401, "invalid_authentication")
    return auth[7:]


def create_media_router(runtime, require_scope, require_product):
    router = APIRouter()

    @router.get("/v2/products/{product_id}/media-models")
    async def models(request: Request, product_id: str):
        await require_product(product_id)
        return await runtime.catalog(await require_scope(request, product_id))

    @router.post("/v2/media/task-tokens")
    async def mint(request: Request):
        return await runtime.mint(await require_scope(request), parse_json(request.state.bounded_body))

    @router.post("/v2/media/assets")
    async def upload(request: Request):
        token = task_token(request)
        mime = request.headers.get("content-type", "").split(";", 1)[0].lower()
        if mime not in ("image/png", "image/jpeg", "image/webp", "audio/mpeg", "audio/wav", "audio/ogg", "audio/mp4", "video/mp4"):
            raise PlatformError(415, "unsupported_media_asset")
        declared = request.headers.get("content-length")
        if declared is not None and (not declared.isascii() or not declared.isdecimal()):
            raise PlatformError(400, "invalid_media_upload")
        return await runtime.upload_asset(token, request.stream(), mime=mime,
                                          declared_size=int(declared) if declared is not None else None)

    @router.post("/v2/media/jobs")
    async def submit(request: Request):
        return await runtime.submit(task_token(request), request.headers.get("idempotency-key"), parse_json(request.state.bounded_body))

    @router.get("/v2/media/operations")
    async def lookup(request: Request, invocation_id: str, operation_key: str):
        return await runtime.service.lookup_operation(await require_scope(request), invocation_id, operation_key)

    @router.get("/v2/media/jobs/{job_id}")
    async def job(request: Request, job_id: str):
        return await runtime._view(await require_scope(request), job_id)

    @router.post("/v2/media/jobs/{job_id}/cancel")
    async def cancel(request: Request, job_id: str):
        scope = await require_scope(request)
        if request.state.bounded_body and parse_json(request.state.bounded_body):
            raise PlatformError(400, "invalid_request")
        await runtime.cancel(scope, job_id)
        return await runtime._view(scope, job_id)

    @router.get("/v2/media/voices/{voice_id}")
    async def voice(request: Request, voice_id: str):
        scope = await require_scope(request)
        if runtime.voices is None: raise PlatformError(404,"media_voice_unavailable")
        return await runtime.voices.public(scope,voice_id)

    @router.get("/v2/media/artifacts/{artifact_id}")
    async def artifact(request: Request, artifact_id: str):
        return await runtime.artifact(await require_scope(request), artifact_id)

    @router.get("/v2/media/artifacts/{artifact_id}/content")
    async def content(request: Request, artifact_id: str):
        raw, mime, sha = await runtime.artifact(await require_scope(request), artifact_id, content=True)
        suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "audio/wav": ".wav", "audio/mpeg": ".mp3", "audio/ogg": ".ogg", "audio/mp4": ".m4a", "video/mp4": ".mp4", "application/json": ".json", "text/plain": ".txt"}.get(mime, ".bin")
        # The ID is validated by the scoped service before becoming a filename.
        return Response(raw, media_type=mime, headers={"ETag": '"' + sha + '"',
            "Content-Disposition": 'attachment; filename="' + artifact_id + suffix + '"', "X-Content-Type-Options": "nosniff"})

    return router
