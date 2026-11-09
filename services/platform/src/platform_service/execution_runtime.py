"""One-attempt bounded Responses gateway over the transactional execution core."""

import asyncio
import hashlib
import json
import re
from uuid import uuid4

import anyio
import httpx
from starlette.background import BackgroundTask
from starlette.responses import Response, StreamingResponse

from .errors import PlatformError
from .execution_evidence import correlation_id
from .execution_bridges import BRIDGES
from .gateway_catalog import GatewayPreflight, ManagedBindings
from .execution_protocol import (
    MAX_JSON,
    MAX_RESULT,
    SSEParser,
    encode,
    parse_json,
    terminal_usage,
    validate_request,
    validate_reasoning_effort,
)


def _failure_code(exc):
    if isinstance(exc, PlatformError):
        return exc.code
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "upstream_timeout"
    return "upstream_unavailable"


class ExecutionRuntime:
    def __init__(self, db, identity, settings, *, service=None, transport=None, provider_keys=None):
        if service is None:
            from .execution.core import ExecutionService

            service = ExecutionService(db, identity)
        self.service, self.settings = service, settings
        self.bindings = settings.bindings
        self._managed = None if db is None else ManagedBindings(
            db, "execution", settings, lambda provider: (None, None, ("text",)), keys=provider_keys)
        self._semaphore = asyncio.Semaphore(settings.concurrency)
        self._client = httpx.AsyncClient(
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(settings.timeout_seconds, connect=10),
            limits=httpx.Limits(
                max_connections=settings.concurrency,
                max_keepalive_connections=settings.concurrency,
            ),
        )
        self._preflight = GatewayPreflight(self._client, "model_channel_unavailable")
        if self._managed is not None:
            self._managed.client = self._client

    async def _sync(self):
        if self._managed is not None:
            self.settings, self.bindings = await self._managed.refresh()

    async def catalog(self, scope):
        if self.settings.mode == "disabled":
            return {"models": []}
        await self._sync()
        return await self.service.catalog(
            scope, mode=self.settings.mode, bindings=self.bindings
        )

    async def mint(self, scope, data):
        if self.settings.mode == "disabled":
            raise PlatformError(503, "execution_disabled")
        await self._sync()
        if not isinstance(data, dict) or set(data) != {"model", "invocation_key"}:
            raise PlatformError(400, "invalid_request")
        return await self.service.mint(
            scope,
            model=data["model"],
            invocation_key=data["invocation_key"],
            mode=self.settings.mode,
            bindings=self.bindings,
        )

    async def capabilities(self, product_id):
        await self._sync()
        ready = (
            self.settings.mode != "disabled"
            and bool(self.bindings)
            and await self.service.readiness(
                product_id, mode=self.settings.mode, bindings=self.bindings
            )
        )
        # Provider configuration and verified active definitions must match.
        # User entitlement is independently enforced by catalog/mint/admit.
        return {
            "execution_stage": self.settings.mode,
            "subscription_execution_enabled": ready and self.settings.mode == "live",
            "test_execution_enabled": ready and self.settings.mode == "test",
            "test_subscription_execution_enabled": ready
            and self.settings.mode == "test",
            "media_execution_enabled": False,
            "pending_capabilities": [
                "images",
                "video",
                "music",
                "asr",
                "tts",
                "voice",
                "cloud_execution",
            ],
        }

    async def run_once(self):
        result = await self.service.recover()
        await self.service.expire_results()
        return result

    async def run_forever(self):
        while True:
            try:
                await self._sync()
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # No exception strings: database/provider diagnostics may carry
                # credentials. Recovery is retried; it never dispatches I/O.
                pass
            await asyncio.sleep(self.settings.recovery_seconds)

    async def close(self):
        if self._managed is not None:
            await self._managed.close()
        await self._client.aclose()

    async def _heartbeat(self, job):
        while True:
            await asyncio.sleep(self.settings.heartbeat_seconds)
            await self.service.heartbeat(job["id"], job["attempt_id"])

    async def respond(self, task_token, key, body):
        if self.settings.mode == "disabled":
            raise PlatformError(503, "execution_disabled")
        if not isinstance(key, str) or not re.fullmatch(r"[\x21-\x7e]{1,128}", key):
            raise PlatformError(400, "idempotency_key_required")
        await self._sync()
        context = await self.service.authenticate_task(task_token)
        if context["funding_mode"] != self.settings.mode:
            raise PlatformError(403, "execution_mode_mismatch")
        value, body_hash = validate_request(body, context["model"], default_output=context.get("model_limits", {}).get("max_output_tokens", 4096))
        validate_reasoning_effort(value, context.get("model_reasoning"))
        job = await self.service.admit(
            context,
            key=key,
            body_hash=body_hash,
            output_limit=value["max_output_tokens"],
            bindings=self.bindings,
        )
        if job.get("replay"):
            cached = await self.service.result(context, key)
            return Response(
                cached["payload"],
                media_type=cached["content_type"],
                headers={"X-Execution-ID": job["id"]},
            )

        upstream = None
        beat = None
        acquired = False
        dispatched = False
        settled = False
        cleaned = False

        async def cleanup(reason="upstream_uncertain"):
            nonlocal cleaned
            if cleaned:
                return
            cleaned = True
            with anyio.CancelScope(shield=True):
                if beat is not None:
                    beat.cancel()
                    await asyncio.gather(beat, return_exceptions=True)
                try:
                    if upstream is not None:
                        await upstream.aclose()
                finally:
                    try:
                        if not settled:
                            if dispatched:
                                await self.service.pending(
                                    job["id"], job["attempt_id"], reason
                                )
                            else:
                                await self.service.cancel(context["scope"], job["id"])
                    finally:
                        if acquired:
                            self._semaphore.release()

        async def finish(payload, content_type, terminal):
            nonlocal settled
            a, b, status, response_id = terminal_usage(terminal)
            # Commit trustworthy usage independently before the financial write.
            # No reply bytes are retained in this immutable evidence record.
            await self.service.record_terminal_evidence(
                job["id"], job["attempt_id"], terminal=terminal,
                evidence_hash=hashlib.sha256(payload).hexdigest())
            await self.service.finish(
                job["id"],
                job["attempt_id"],
                input_tokens=a,
                output_tokens=b,
                terminal_status=status,
                provider_response_id=response_id,
                evidence_hash=hashlib.sha256(payload).hexdigest(),
                payload=payload,
                content_type=content_type,
            )
            settled = True

        try:
            ceiling = (
                job["model_definition"]
                .get("limits", {})
                .get("max_input_bytes", 1024 * 1024)
            )
            if len(encode(value)) > ceiling:
                raise PlatformError(413, "request_too_large")
            binding = self.bindings.get(context["model"])
            if not binding or binding["binding_hash"] != job["binding_hash"]:
                raise PlatformError(503, "execution_binding_unavailable")
            provider = self.settings.providers[binding["provider_id"]]
            value["model"] = provider.models[context["model"]]
            if provider.preflight_models:
                await self._preflight.check(
                    provider.models_url or re.sub(r"/v1/.*$", "/v1/models", provider.endpoint), provider.secret, value["model"])
            bridge = BRIDGES.get(provider.protocol)
            payload = encode(bridge.request(value) if bridge else value)
            await self._semaphore.acquire()
            acquired = True
            request_id = str(uuid4())
            await self.service.record_provider_attempt(
                job["id"], job["attempt_id"],
                provider_scope_hash=hashlib.sha256(encode([provider.endpoint, hashlib.sha256(provider.secret.encode()).hexdigest()])).hexdigest(),
                provider_request_hash=hashlib.sha256(payload).hexdigest(),
                upstream_model=value["model"], request_id=request_id)
            await self.service.dispatch(job["id"], job["attempt_id"])
            dispatched = True
            deadline = asyncio.get_running_loop().time() + self.settings.timeout_seconds
            beat = asyncio.create_task(self._heartbeat(job))
            async with asyncio.timeout_at(deadline):
                upstream = await self._client.send(
                    self._client.build_request(
                        "POST",
                        provider.endpoint,
                        content=payload,
                        headers={
                            **(bridge.headers(provider.secret) if bridge else {"Authorization": "Bearer " + provider.secret}),
                            "Content-Type": "application/json",
                            "X-Request-ID": request_id,
                            "Accept-Encoding": "identity",
                            "Accept": "text/event-stream"
                            if value.get("stream")
                            else "application/json",
                        },
                    ),
                    stream=True,
                )
                upstream_request_id = (upstream.headers.get("x-request-id")
                    or upstream.headers.get("request-id")
                    or upstream.headers.get("x-oneapi-request-id"))
                if upstream_request_id is not None:
                    try:
                        correlation_id(upstream_request_id)
                    except PlatformError:
                        # Only an opaque bounded correlation value is eligible;
                        # never persist arbitrary diagnostic/header text.
                        upstream_request_id = None
                await self.service.record_provider_association(
                    job["id"], job["attempt_id"], http_status=upstream.status_code,
                    http_request_id=upstream_request_id)
                if upstream.status_code != 200:
                    raise PlatformError(502, "upstream_error")
                if (
                    upstream.headers.get("content-encoding", "identity").strip().lower()
                    != "identity"
                ):
                    raise PlatformError(502, "invalid_upstream_response")
                content_type = (
                    upstream.headers.get("content-type", "")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                )
                if value.get("stream"):
                    if content_type != "text/event-stream":
                        raise PlatformError(502, "invalid_upstream_response")
                else:
                    if content_type != "application/json":
                        raise PlatformError(502, "invalid_upstream_response")
                    raw = bytearray()
                    async for chunk in upstream.aiter_bytes():
                        if len(raw) + len(chunk) > MAX_JSON:
                            raise PlatformError(502, "upstream_response_too_large")
                        raw.extend(chunk)
                    result = bytes(raw)
                    if bridge:
                        result = encode(bridge.response(parse_json(result, upstream=True), context["model"]))
                    await finish(
                        result, "application/json", parse_json(result, upstream=True)
                    )
                    await cleanup()
                    return Response(
                        result,
                        media_type="application/json",
                        headers={"X-Execution-ID": job["id"]},
                    )

            async def stream():
                parser, captured = SSEParser(), bytearray()
                try:
                    async with asyncio.timeout_at(deadline):
                        source = bridge.stream(upstream.aiter_bytes(), context["model"]) if bridge else upstream.aiter_bytes()
                        async for chunk in source:
                            if len(captured) + len(chunk) > MAX_RESULT:
                                raise PlatformError(502, "upstream_response_too_large")
                            for event, item in parser.feed(chunk):
                                if len(captured) + len(event) > MAX_RESULT:
                                    raise PlatformError(
                                        502, "upstream_response_too_large"
                                    )
                                captured.extend(event)
                                if item is not None:
                                    kind = item["type"]
                                    if kind == "response.created":
                                        created = item.get("response")
                                        identifier = created.get("id") if isinstance(created, dict) else None
                                        if identifier is not None:
                                            correlation_id(identifier)
                                            await self.service.record_provider_association(
                                                job["id"], job["attempt_id"], provider_response_id=identifier)
                                    if kind in {
                                        "response.completed",
                                        "response.incomplete",
                                    }:
                                        terminal = item.get("response")
                                        counts = terminal_usage(terminal)
                                        if kind != "response." + counts[2]:
                                            raise PlatformError(
                                                502, "invalid_upstream_usage"
                                            )
                                        await finish(
                                            bytes(captured),
                                            "text/event-stream",
                                            terminal,
                                        )
                                        await cleanup()
                                        yield event
                                        return
                                    if kind in {"error", "response.failed"}:
                                        raise PlatformError(502, "upstream_error")
                                    if not kind.startswith("response."):
                                        raise PlatformError(
                                            502, "invalid_upstream_response"
                                        )
                                yield event
                        raise PlatformError(502, "invalid_upstream_usage")
                except Exception as exc:
                    code = _failure_code(exc)
                    await cleanup(code)
                    yield (
                        "event: error\ndata: "
                        + json.dumps({"type": "error", "error": {"code": code}})
                        + "\n\n"
                    ).encode()
                finally:
                    await cleanup()

            return StreamingResponse(
                stream(),
                media_type="text/event-stream",
                headers={"X-Execution-ID": job["id"]},
                background=BackgroundTask(cleanup),
            )
        except BaseException as exc:
            code = _failure_code(exc)
            try:
                await cleanup(code)
            except Exception:
                # Durable dispatch marker + lease recovery retain the hold even
                # if the database is temporarily unavailable during cleanup.
                pass
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
            if isinstance(exc, PlatformError):
                raise
            raise PlatformError(502, code) from None
