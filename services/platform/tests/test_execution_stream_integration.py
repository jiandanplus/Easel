"""Real PostgreSQL + owned HTTP + native proxy; provider is a gated fixture."""

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import socket

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from platform_service.execution_config import ExecutionProvider, ExecutionSettings
from platform_service.execution_runtime import ExecutionRuntime
from platform_service.execution_web import create_execution_router
from test_execution_core import setup


def event(kind, **fields):
    return ("event: " + kind + "\ndata: " + json.dumps({"type": kind, **fields}) + "\n\n").encode()


class GatedProvider(httpx.AsyncByteStream):
    def __init__(self):
        self.release = asyncio.Event()
        self.closed = asyncio.Event()

    async def __aiter__(self):
        yield event("response.output_text.delta", delta="INTEGRATION_EARLY")
        await self.release.wait()
        yield event("response.completed", response={"id": "resp_owned_integration", "status": "completed", "output": [], "usage": {"input_tokens": 3, "output_tokens": 2}})

    async def aclose(self):
        self.closed.set()


@asynccontextmanager
async def chain(db, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3]))
    from easel.platform.execution_proxy import ExecutionProxy

    service, ledger, scope, _ = await setup(db)
    provider, calls = GatedProvider(), []

    async def send(request):
        calls.append(request.method)
        return httpx.Response(200, stream=provider, headers={"Content-Type": "text/event-stream"})

    settings = ExecutionSettings(mode="test", providers={"fixture": ExecutionProvider("1", "http://127.0.0.1:1/responses", "owned-fixture-secret", {"fixture-text": "upstream"})})
    runtime = ExecutionRuntime(db, service.identity, settings, service=service, transport=httpx.MockTransport(send))
    credential = await runtime.mint(scope, {"model": "fixture-text", "invocation_key": "integration-native"})
    app = FastAPI()

    async def require_scope(*_):
        return scope

    async def require_product(*_):
        return None

    @app.middleware("http")
    async def bounded_body(request, call_next):
        request.state.bounded_body = await request.body()
        return await call_next(request)

    app.include_router(create_execution_router(runtime, require_scope, require_product))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    origin = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False, lifespan="off", proxy_headers=False))
    server.install_signal_handlers = lambda: None
    serving = asyncio.create_task(server.serve(sockets=[sock]))
    directory = tmp_path / "outputs" / "_harness" / "central-execution"
    try:
        async with asyncio.timeout(3):
            while not server.started:
                await asyncio.sleep(0.01)
        async with ExecutionProxy(directory, origin, credential["task_token"], "fixture-text", credential["invocation_id"], credential=credential) as proxy:
            yield runtime, service, ledger, scope, provider, calls, proxy, credential, directory
    finally:
        provider.release.set()
        server.should_exit = True
        await asyncio.wait_for(serving, 4)
        await runtime.close()


async def first_text(response):
    chunks = response.aiter_bytes().__aiter__()
    first = await asyncio.wait_for(anext(chunks), 2)
    assert b"INTEGRATION_EARLY" in first
    return chunks


def record(directory):
    records = list((directory / "operations").glob("*.json"))
    assert len(records) == 1
    return json.loads(records[0].read_text())


@pytest.mark.asyncio
async def test_text_is_early_finance_waits_terminal_and_status_never_resends(db, tmp_path, monkeypatch):
    async with chain(db, tmp_path, monkeypatch) as (runtime, service, ledger, scope, provider, calls, proxy, credential, directory):
        body = {"model": "fixture-text", "input": "owned integration", "stream": True}
        async with httpx.AsyncClient(trust_env=False) as client:
            async with client.stream("POST", proxy.base_url + "/responses", json=body, headers={"Authorization": "Bearer " + proxy.local_token}) as response:
                chunks = await first_text(response)
                operation = record(directory)
                before = await service.lookup_operation(scope, credential["invocation_id"], operation["operation_key"])
                assert before["status"] == "dispatched" and before["actual"] is None
                assert (await ledger.summary(scope))["spent"] == "0"
                provider.release.set()
                tail = b"".join([part async for part in chunks])
                assert b"response.completed" in tail
            operation = record(directory)
            assert operation["status"] == "delivered"
            from easel.platform.execution_records import operation_owner, read_operations
            discovered, truncated = read_operations(tmp_path, operation_owner(proxy.origin, credential["scope"]))
            assert not truncated and len(discovered) == 1
            assert discovered[0]["operation_key"] == operation["operation_key"]
            finished = await service.lookup_operation(scope, credential["invocation_id"], operation["operation_key"])
            assert finished["status"] == "settled" and finished["actual"] == "700"
            assert finished["result_state"] == "available"
            for _ in range(2):
                checked = await client.get(proxy.origin + "/v2/execution-operations", params={"invocation_id": credential["invocation_id"], "operation_key": operation["operation_key"]})
                assert checked.status_code == 200 and checked.json()["actual"] == "700"
            replay = await runtime.respond(credential["task_token"], operation["operation_key"], body)
            assert b"response.completed" in replay.body
            assert calls == ["POST"]
            summary = await ledger.summary(scope)
            assert summary["spent"] == "700" and summary["reserved"] == "0"


@pytest.mark.asyncio
async def test_drop_after_text_retains_unknown_hold_and_can_query_without_resend(db, tmp_path, monkeypatch):
    async with chain(db, tmp_path, monkeypatch) as (_, service, ledger, scope, provider, calls, proxy, credential, directory):
        async with httpx.AsyncClient(trust_env=False) as client:
            async with client.stream("POST", proxy.base_url + "/responses", json={"model": "fixture-text", "input": "owned drop", "stream": True}, headers={"Authorization": "Bearer " + proxy.local_token}) as response:
                await first_text(response)
            async with asyncio.timeout(3):
                while True:
                    operation = record(directory)
                    current = await service.lookup_operation(scope, credential["invocation_id"], operation["operation_key"])
                    if current["status"] == "pending_reconciliation" and operation["status"] == "pending":
                        break
                    await asyncio.sleep(0.01)
            assert current["actual"] is None and current["result_state"] == "not_available"
            summary = await ledger.summary(scope)
            assert summary["spent"] == "0" and int(summary["reserved"]) > 0
            assert calls == ["POST"] and provider.closed.is_set()


@pytest.mark.asyncio
async def test_settled_but_local_terminal_delivery_failed_stays_charged_once(db, tmp_path, monkeypatch):
    async with chain(db, tmp_path, monkeypatch) as (_, service, ledger, scope, provider, calls, proxy, credential, directory):
        original = proxy._chunk

        async def fail_terminal(writer, data):
            if b"response.completed" in data:
                raise ConnectionResetError("owned downstream failure")
            await original(writer, data)

        monkeypatch.setattr(proxy, "_chunk", fail_terminal)
        async with httpx.AsyncClient(trust_env=False) as client:
            async with client.stream("POST", proxy.base_url + "/responses", json={"model": "fixture-text", "input": "owned drain", "stream": True}, headers={"Authorization": "Bearer " + proxy.local_token}) as response:
                chunks = await first_text(response)
                provider.release.set()
                parts, closed = [], False
                try:
                    async for part in chunks:
                        parts.append(part)
                except httpx.RemoteProtocolError:
                    closed = True
                tail = b"".join(parts)
                assert closed or b"central_stream_failed" in tail
                assert b"response.completed" not in tail
            operation = record(directory)
            assert operation["status"] == "pending"
            for _ in range(2):
                current = await service.lookup_operation(scope, credential["invocation_id"], operation["operation_key"])
                assert current["status"] == "settled" and current["actual"] == "700"
            summary = await ledger.summary(scope)
            assert summary["spent"] == "700" and summary["reserved"] == "0" and calls == ["POST"]
