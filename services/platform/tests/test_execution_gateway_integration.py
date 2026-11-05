"""Real PostgreSQL + owned literal-loopback HTTP fixture. No provider keys."""

import asyncio
from contextlib import asynccontextmanager
import json

import pytest

from platform_service.errors import PlatformError
from platform_service.execution_config import ExecutionProvider, ExecutionSettings
from platform_service.execution_runtime import ExecutionRuntime
from test_execution_core import setup


@asynccontextmanager
async def provider():
    calls = []

    async def handle(reader, writer):
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            headers = dict(
                line.split(b":", 1) for line in head.split(b"\r\n")[1:] if b":" in line
            )
            raw = await reader.readexactly(int(headers[b"Content-Length"].strip()))
            value = json.loads(raw)
            calls.append(value)
            assert headers[b"Authorization"].strip() == b"Bearer dummy-local-fixture"
            if value["input"] == "http-fail":
                writer.write(
                    b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                )
                await writer.drain()
                return
            terminal = {
                "id": "response-fixture-"+str(len(calls)),
                "status": "completed",
                "usage": {"input_tokens": 3, "output_tokens": 2},
                "output": [],
            }
            if value.get("stream"):
                payload = (
                    "event: response.completed\r\ndata: "
                    + json.dumps({"type": "response.completed", "response": terminal})
                    + "\r\n\r\n"
                ).encode()
                content_type = b"text/event-stream"
            else:
                payload = json.dumps(terminal).encode()
                content_type = b"application/json"
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: "
                + content_type
                + b"\r\nContent-Length: "
                + str(len(payload)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + payload
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        yield (
            "http://127.0.0.1:"
            + str(server.sockets[0].getsockname()[1])
            + "/v1/responses",
            calls,
        )
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_real_http_postgres_json_sse_charge_replay_and_pending(db):
    service, ledger, scope, _ = await setup(db)
    async with provider() as (endpoint, calls):
        current = ExecutionRuntime(
            db,
            service.identity,
            ExecutionSettings(
                mode="test",
                providers={
                    "fixture": ExecutionProvider(
                        "1",
                        endpoint,
                        "dummy-local-fixture",
                        {"fixture-text": "local-upstream"},
                    )
                },
            ),
            service=service,
        )
        try:
            assert (await current.capabilities("easel"))[
                "test_execution_enabled"
            ] is True
            assert (await current.capabilities("second"))[
                "test_execution_enabled"
            ] is False
            minted = await current.mint(
                scope, {"model": "fixture-text", "invocation_key": "runtime-run"}
            )
            token = minted["task_token"]
            body = {
                "model": "fixture-text",
                "input": "hello",
                "tools": [
                    {"type": "custom", "name": "edit"},
                    {
                        "type": "namespace",
                        "name": "local",
                        "tools": [
                            {
                                "type": "function",
                                "name": "run",
                                "parameters": {"type": "object"},
                            }
                        ],
                    },
                ],
            }
            first = await current.respond(token, "sync", body)
            replay = await current.respond(token, "sync", body)
            assert first.body == replay.body and len(calls) == 1
            assert (await ledger.summary(scope))["spent"] == "700"
            stream_body = {**body, "stream": True}
            response = await current.respond(token, "stream", stream_body)
            raw = b"".join([chunk async for chunk in response.body_iterator])
            assert b"response.completed" in raw
            assert (await ledger.summary(scope))["spent"] == "1400"
            assert len(calls) == 2
            with pytest.raises(PlatformError):
                await current.respond(
                    token, "bad", {"model": "fixture-text", "input": "http-fail"}
                )
            with pytest.raises(PlatformError, match="pending_reconciliation"):
                await current.respond(
                    token, "bad", {"model": "fixture-text", "input": "http-fail"}
                )
            assert len(calls) == 3
            assert (await ledger.summary(scope))["reserved"] == "7372800"
            assert len(await service.list(scope)) == 3
            async with db.connection() as conn:
                rows = await (
                    await conn.execute("SELECT body_hash FROM execution_jobs")
                ).fetchall()
            assert all(
                len(row["body_hash"]) == 64 and "hello" not in row["body_hash"]
                for row in rows
            )
        finally:
            await current.close()
