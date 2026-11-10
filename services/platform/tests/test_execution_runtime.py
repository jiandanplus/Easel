import asyncio
import hashlib
import json

import httpx
import pytest

from platform_service.errors import PlatformError


class Service:
    def __init__(self):
        self.jobs = {}
        self.calls = []
        self.receipts = []
        self.provider_attempts = []
        self.provider_associations = []
        self.terminal_evidence = []

    async def record_provider_attempt(self, id, attempt, **value):
        assert self.jobs[id]["status"] == "reserved"
        self.provider_attempts.append(value)

    async def record_provider_association(self, id, attempt, **value):
        assert self.jobs[id]["status"] == "dispatched"
        self.provider_associations.append(value)

    async def record_terminal_evidence(self, id, attempt, **value):
        assert self.jobs[id]["status"] == "dispatched"
        self.terminal_evidence.append(value)

    async def authenticate_task(self, token):
        if token != "private-task":
            raise PlatformError(401, "invalid_authentication")
        return {
            "scope": {},
            "model": "public",
            "invocation_id": "invocation",
            "funding_mode": "test",
        }

    async def admit(self, context, *, key, body_hash, output_limit, bindings):
        if key in self.jobs:
            if self.jobs[key]["body_hash"] != body_hash:
                raise PlatformError(409, "idempotency_conflict")
            return {**self.jobs[key], "replay": True}
        job = {
            "id": key,
            "status": "reserved",
            "attempt_id": "attempt",
            "reservation_id": "reservation",
            "model_definition": {"id": "public"},
            "binding_hash": bindings["public"]["binding_hash"],
            "body_hash": body_hash,
            "replay": False,
        }
        self.jobs[key] = job
        self.calls.append("admit")
        return job

    async def dispatch(self, id, attempt):
        self.calls.append("dispatch")
        self.jobs[id]["status"] = "dispatched"

    async def finish(self, id, attempt, **receipt):
        assert self.jobs[id]["status"] == "dispatched"
        self.calls.append("finish")
        self.receipts.append(receipt)
        self.jobs[id].update(status="settled", **receipt)

    async def pending(self, id, attempt, reason):
        self.calls.append("pending")
        self.jobs[id].update(status="pending", reason=reason)

    async def heartbeat(self, id, attempt):
        self.calls.append("heartbeat")

    async def result(self, context, key):
        job = self.jobs[key]
        if job["status"] != "settled":
            raise PlatformError(409, "pending_reconciliation")
        return job

    async def cancel(self, scope, id):
        self.jobs[id]["status"] = "cancelled"

    async def recover(self):
        self.calls.append("recover")
        return {"pending": 0}

    async def readiness(self, product_id, *, mode, bindings):
        return mode != "disabled" and bool(bindings)

    async def expire_results(self):
        self.calls.append("expire_results")
        return 0


def runtime(handler, **settings):
    from platform_service.execution_config import ExecutionProvider, ExecutionSettings
    from platform_service.execution_runtime import ExecutionRuntime

    service = Service()
    config = ExecutionSettings(
        mode="test",
        providers={
            "fixture": ExecutionProvider(
                "1",
                "http://127.0.0.1:9999/responses",
                "fixture-private-secret",
                {"public": "upstream"},
            )
        },
        **settings,
    )
    return ExecutionRuntime(
        None, None, config, service=service, transport=httpx.MockTransport(handler)
    )


def terminal(status="completed"):
    return {
        "id": "resp_fixture",
        "status": status,
        "usage": {"input_tokens": 3, "output_tokens": 2},
        "output": [],
    }


def request(**patch):
    return {"model": "public", "input": "hello", **patch}


async def consume(response):
    if hasattr(response, "body_iterator"):
        return b"".join([part async for part in response.body_iterator])
    return response.body


@pytest.mark.asyncio
async def test_provider_response_namespace_is_independent_of_model_alias():
    from platform_service.execution_config import ExecutionProvider, ExecutionSettings
    from platform_service.execution_runtime import ExecutionRuntime
    hashes=[]
    for upstream,secret in [("upstream-a","same-key"),("upstream-b","same-key"),("upstream-a","other-key")]:
        config=ExecutionSettings(mode="test",providers={"fixture":ExecutionProvider("1","http://127.0.0.1:9999/responses",secret,{"public":upstream})})
        service=Service()
        current=ExecutionRuntime(None,None,config,service=service,transport=httpx.MockTransport(lambda req:httpx.Response(200,json=terminal())))
        try:
            await current.respond("private-task","namespace",request())
            hashes.append(service.provider_attempts[0]["provider_scope_hash"])
        finally:await current.close()
    assert hashes[0]==hashes[1] and hashes[0]!=hashes[2]


@pytest.mark.asyncio
async def test_default_output_follows_immutable_model_limit():
    received = []
    async def handler(req):
        received.append(json.loads(req.content)["max_output_tokens"])
        return httpx.Response(200, json=terminal())
    gateway = runtime(handler)
    authenticate = gateway.service.authenticate_task
    async def with_limits(token):
        return {**await authenticate(token), "model_limits": {"max_output_tokens": 1024}}
    gateway.service.authenticate_task = with_limits
    try:
        await gateway.respond("private-task", "smaller-default", request())
        assert received == [1024]
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_json_settles_before_return_and_replay_never_resends():
    calls = []

    async def handler(req):
        calls.append(req)
        assert json.loads(req.content)["model"] == "upstream"
        assert json.loads(req.content)["max_output_tokens"] == 4096
        return httpx.Response(
            200, json=terminal(), headers={"content-type": "application/json"}
        )

    current = runtime(handler)
    try:
        first = await current.respond("private-task", "op", request())
        second = await current.respond("private-task", "op", request())
        assert first.body == second.body and len(calls) == 1
        assert current.service.calls == ["admit", "dispatch", "finish"]
        receipt = current.service.receipts[0]
        assert (receipt["input_tokens"], receipt["output_tokens"]) == (3, 2)
        assert receipt["evidence_hash"] == hashlib.sha256(first.body).hexdigest()
        with pytest.raises(PlatformError):
            await current.respond("private-task", "op", request(input="different"))
    finally:
        await current.close()


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "incomplete"])
async def test_stream_split_utf8_terminal_is_settled_before_final_emit(status):
    delta = (
        "event: response.output_text.delta\r\ndata: "
        + json.dumps(
            {"type": "response.output_text.delta", "delta": "你好"}, ensure_ascii=False
        )
        + "\r\n\r\n"
    ).encode()
    final = (
        "event: response."
        + status
        + "\r\ndata: "
        + json.dumps({"type": "response." + status, "response": terminal(status)})
        + "\r\n\r\n"
    ).encode()
    stream = Chunks([bytes([byte]) for byte in delta + final])
    current = runtime(
        lambda req: httpx.Response(
            200, stream=stream, headers={"content-type": "text/event-stream"}
        )
    )
    try:
        response = await current.respond("private-task", "stream", request(stream=True))
        seen = bytearray()
        async for part in response.body_iterator:
            if b'"response":' in part:
                assert current.service.jobs["stream"]["status"] == "settled"
            seen.extend(part)
        assert bytes(seen) == delta + final and stream.closed
        assert await consume(
            await current.respond("private-task", "stream", request(stream=True))
        ) == bytes(seen)
    finally:
        await current.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario", ["eof", "failed", "malformed", "oversized", "http", "usage", "gzip"]
)
async def test_dispatched_failures_stay_pending_and_never_resend(scenario):
    calls = []

    def handler(req):
        calls.append(req)
        if scenario == "http":
            return httpx.Response(500, text="fixture-private-secret")
        if scenario == "gzip":
            return httpx.Response(
                200, content=b"x", headers={"content-encoding": "gzip"}
            )
        if scenario == "usage":
            return httpx.Response(
                200,
                json={
                    **terminal(),
                    "usage": {"input_tokens": True, "output_tokens": 0},
                },
            )
        body = {
            "eof": b'data: {"type":"response.created"}\n\n',
            "failed": b'data: {"type":"response.failed"}\n\n',
            "malformed": b"data: not-json\n\n",
            "oversized": b"x" * (256 * 1024 + 1),
        }[scenario]
        return httpx.Response(
            200, stream=Chunks([body]), headers={"content-type": "text/event-stream"}
        )

    current = runtime(handler)
    streaming = scenario not in {"http", "usage", "gzip"}
    try:
        try:
            response = await current.respond(
                "private-task", "bad", request(stream=streaming)
            )
            body = await consume(response)
            assert b"event: error" in body and b"fixture-private-secret" not in body
        except PlatformError as exc:
            assert "fixture-private-secret" not in str(exc)
        assert current.service.jobs["bad"]["status"] == "pending"
        with pytest.raises(PlatformError):
            await current.respond("private-task", "bad", request(stream=streaming))
        assert len(calls) == 1
    finally:
        await current.close()


@pytest.mark.asyncio
async def test_cancellation_closes_stream_and_preserves_hold():
    started = asyncio.Event()

    class Blocking(Chunks):
        async def __aiter__(self):
            started.set()
            await asyncio.Event().wait()
            yield b""

    stream = Blocking([])
    current = runtime(
        lambda req: httpx.Response(
            200, stream=stream, headers={"content-type": "text/event-stream"}
        )
    )
    try:
        response = await current.respond("private-task", "cancel", request(stream=True))
        task = asyncio.create_task(consume(response))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stream.closed and current.service.jobs["cancel"]["status"] == "pending"
        assert current._semaphore._value == current.settings.concurrency
    finally:
        await current.close()


@pytest.mark.asyncio
async def test_worker_recovery_and_disabled_no_outbound():
    from platform_service.execution_config import ExecutionSettings
    from platform_service.execution_runtime import ExecutionRuntime

    current = ExecutionRuntime(None, None, ExecutionSettings(), service=Service())
    try:
        assert (await current.capabilities("easel"))[
            "subscription_execution_enabled"
        ] is False
        assert (await current.capabilities("easel"))["test_execution_enabled"] is False
        with pytest.raises(PlatformError, match="execution_disabled"):
            await current.respond("private-task", "op", request())
        assert await current.run_once() == {"pending": 0}
        assert "expire_results" in current.service.calls
        assert await current.catalog({}) == {"models": []}
    finally:
        await current.close()


@pytest.mark.asyncio
async def test_configured_transport_alone_does_not_advertise_execution():
    current = runtime(lambda req: (_ for _ in ()).throw(AssertionError("no outbound")))

    async def no_models(*args, **kwargs):
        return False

    current.service.readiness = no_models
    try:
        assert (await current.capabilities("easel"))["test_execution_enabled"] is False
    finally:
        await current.close()


@pytest.mark.asyncio
async def test_frozen_model_input_bytes_ceiling_is_checked_before_dispatch():
    current = runtime(lambda req: (_ for _ in ()).throw(AssertionError("no outbound")))
    original = current.service.admit

    async def small(*args, **kwargs):
        job = await original(*args, **kwargs)
        return {**job, "model_definition": {"limits": {"max_input_bytes": 8}}}

    current.service.admit = small
    try:
        with pytest.raises(PlatformError, match="request_too_large"):
            await current.respond("private-task", "op", request())
        assert "dispatch" not in current.service.calls
        assert current.service.jobs["op"]["status"] == "cancelled"
    finally:
        await current.close()


@pytest.mark.asyncio
async def test_json_response_limit_and_redirect_are_pending():
    for response in (
        httpx.Response(302, headers={"location": "http://127.0.0.1:1/evil"}),
        httpx.Response(
            200,
            content=b"x" * (2 * 1024 * 1024 + 1),
            headers={"content-type": "application/json"},
        ),
    ):
        calls = []

        def handler(req):
            calls.append(req)
            return response

        current = runtime(handler)
        try:
            with pytest.raises(PlatformError):
                await current.respond("private-task", "op", request())
            assert len(calls) == 1 and current.service.jobs["op"]["status"] == "pending"
        finally:
            await current.close()


@pytest.mark.asyncio
async def test_stream_heartbeat_and_total_timeout_preserve_hold():
    class Slow(Chunks):
        async def __aiter__(self):
            await asyncio.sleep(1.1)
            yield b'data: {"type":"response.created"}\n\n'
            await asyncio.Event().wait()

    source = Slow([])
    current = runtime(
        lambda req: httpx.Response(
            200, stream=source, headers={"content-type": "text/event-stream"}
        ),
        heartbeat_seconds=1,
        timeout_seconds=2,
    )
    try:
        response = await current.respond(
            "private-task", "timeout", request(stream=True)
        )
        body = await consume(response)
        assert "heartbeat" in current.service.calls
        assert b"event: error" in body and source.closed
        assert current.service.jobs["timeout"]["status"] == "pending"
    finally:
        await current.close()


@pytest.mark.asyncio
async def test_invalid_auth_or_operation_key_never_dispatches():
    current = runtime(lambda req: (_ for _ in ()).throw(AssertionError("no outbound")))
    try:
        for token, key in (
            ("product-token", "op"),
            ("private-task", None),
            ("private-task", "bad key"),
        ):
            with pytest.raises(PlatformError):
                await current.respond(token, key, request())
        assert current.service.calls == []
    finally:
        await current.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["deadline", "connect", "read", "write", "pool"])
async def test_header_timeouts_have_safe_reason_and_never_resend(monkeypatch, failure):
    requests = []
    if failure == "deadline":
        original_timeout_at = asyncio.timeout_at
        monkeypatch.setattr(asyncio, "timeout_at", lambda deadline: original_timeout_at(
            min(deadline, asyncio.get_running_loop().time() + .02)))

    async def handler(req):
        requests.append(req)
        if failure == "deadline":
            await asyncio.Event().wait()
        error = {"connect": httpx.ConnectTimeout, "read": httpx.ReadTimeout,
                 "write": httpx.WriteTimeout, "pool": httpx.PoolTimeout}[failure]
        raise error("fixture-private-secret", request=req)

    current = runtime(handler)
    try:
        with pytest.raises(PlatformError) as caught:
            await current.respond("private-task", "slow-headers", request(stream=True))
        assert caught.value.code == "upstream_timeout"
        assert "fixture-private-secret" not in str(caught.value)
        assert current.service.jobs["slow-headers"]["reason"] == "upstream_timeout"
        assert current.service.jobs["slow-headers"]["status"] == "pending"
        assert not current.service.receipts
        assert current._semaphore._value == current.settings.concurrency
        with pytest.raises(PlatformError, match="pending_reconciliation"):
            await current.respond("private-task", "slow-headers", request(stream=True))
        assert len(requests) == 1
        assert current.service.calls.count("dispatch") == 1
    finally:
        await current.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["deadline", "read"])
async def test_stream_timeouts_have_safe_reason_close_source_and_never_resend(monkeypatch, failure):
    requests = []
    if failure == "deadline":
        original_timeout_at = asyncio.timeout_at
        monkeypatch.setattr(asyncio, "timeout_at", lambda deadline: original_timeout_at(
            min(deadline, asyncio.get_running_loop().time() + .02)))

    class Slow(Chunks):
        async def __aiter__(self):
            yield b'data: {"type":"response.created"}\n\n'
            if failure == "deadline":
                await asyncio.Event().wait()
            raise httpx.ReadTimeout("fixture-private-secret")

    source = Slow([])
    def handler(req):
        requests.append(req)
        return httpx.Response(200, stream=source, headers={"content-type": "text/event-stream"})

    current = runtime(handler)
    try:
        response = await current.respond("private-task", "slow-stream", request(stream=True))
        body = await consume(response)
        assert b'"code": "upstream_timeout"' in body
        assert b"fixture-private-secret" not in body
        assert current.service.jobs["slow-stream"]["reason"] == "upstream_timeout"
        assert current.service.jobs["slow-stream"]["status"] == "pending"
        assert source.closed and not current.service.receipts
        assert current._semaphore._value == current.settings.concurrency
        with pytest.raises(PlatformError, match="pending_reconciliation"):
            await current.respond("private-task", "slow-stream", request(stream=True))
        assert len(requests) == 1
        assert current.service.calls.count("dispatch") == 1
    finally:
        await current.close()
