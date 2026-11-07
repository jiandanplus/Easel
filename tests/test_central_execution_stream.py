"""Owned native loopback streaming: timing, framing, bounds and failure state."""
import asyncio
import json
import stat
import time

import httpx
import pytest

from easel.platform.execution_proxy import ExecutionProxy


def event(kind, **fields):
    return ("event: " + kind + "\ndata: " + json.dumps({"type": kind, **fields}, ensure_ascii=False) + "\n\n").encode()


CREATED = event("response.created", response={"id": "resp_fixture", "status": "in_progress", "output": []})
DELTA = event("response.output_text.delta", delta="早期文字")
COMPLETED = event("response.completed", response={"id": "resp_fixture", "status": "completed", "output": [], "usage": {"input_tokens": 1, "output_tokens": 1}})
LOCAL_ERROR = b'"code":"central_stream_failed"'


def test_default_proxy_waits_beyond_central_budget_and_passes_read_timeout(tmp_path):
    """A valid slow central response must not lose its final receipt at 120s."""
    async def run():
        calls, source = [], Stream([CREATED, DELTA, COMPLETED])
        proxy = ExecutionProxy(tmp_path, "https://central.test", "PRIVATE_TASK", "fixture", "inv", transport=transport_for(source, calls))
        assert 300 < proxy.read_timeout < proxy.total_timeout < 330
        async with proxy:
            async with httpx.AsyncClient(trust_env=False) as client:
                response = await client.post(proxy.base_url + "/responses", json={"model": "fixture"}, headers={"Authorization": "Bearer " + proxy.local_token})
                assert response.content == CREATED + DELTA + COMPLETED
            await wait_records(tmp_path, "delivered")
        assert len(calls) == 1 and source.closed
        assert calls[0].extensions["timeout"]["read"] == 310.0
    asyncio.run(run())


class Stream(httpx.AsyncByteStream):
    def __init__(self, chunks, gate=None):
        self.chunks, self.gate, self.closed = chunks, gate, False
        self.pulls = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            self.pulls += 1
            if chunk is None:
                await self.gate.wait()
            else:
                yield chunk

    async def aclose(self):
        self.closed = True


def records(root):
    return [json.loads(path.read_text()) for path in (root / "operations").glob("*.json")]


async def wait_records(root, status):
    async def wait():
        while not records(root) or records(root)[0]["status"] != status:
            await asyncio.sleep(.005)
    await asyncio.wait_for(wait(), 2)


def transport_for(source, calls, **kwargs):
    async def handle(request):
        calls.append(request)
        return httpx.Response(kwargs.get("status", 200), stream=source,
                              headers={"Content-Type": "text/event-stream", **kwargs.get("headers", {})})
    return httpx.MockTransport(handle)


def test_early_text_precedes_terminal_gate_and_delivery(tmp_path):
    async def run():
        gate, calls = asyncio.Event(), []
        source = Stream([CREATED, DELTA, None, COMPLETED], gate)
        async with ExecutionProxy(tmp_path, "https://central.test", "PRIVATE_TASK", "fixture", "inv", transport=transport_for(source, calls)) as proxy:
            async with httpx.AsyncClient(trust_env=False) as client:
                async def receive():
                    async with client.stream("POST", proxy.base_url + "/responses", json={"model": "fixture"}, headers={"Authorization": "Bearer " + proxy.local_token}) as response:
                        assert response.headers.get("transfer-encoding") == "chunked"
                        assert "content-length" not in response.headers
                        result = bytearray()
                        async for chunk in response.aiter_bytes():
                            result.extend(chunk)
                            if DELTA in result and not gate.is_set():
                                assert records(tmp_path)[0]["status"] == "sending"
                                gate.set()
                        return bytes(result)
                assert await asyncio.wait_for(receive(), 1) == CREATED + DELTA + COMPLETED
            await wait_records(tmp_path, "delivered")
        assert len(calls) == 1 and source.closed
    asyncio.run(run())


@pytest.mark.parametrize("tail", [b"", b"data: {bad}\n\n", b"data: \xff\n\n", b'event: wrong\ndata: {"type":"response.completed"}\n\n', b'data: {"type":"a","type":"b"}\n\n', event("error", error={"message": "PRIVATE_UPSTREAM"}), event("response.failed", response={"status": "failed"}), event("response.completed", response={"status": "failed"}), event("response.incomplete", response={})])
def test_stream_failure_after_delta_stays_pending_and_sanitized(tmp_path, tail):
    async def run():
        calls, source = [], Stream([CREATED, DELTA, tail])
        async with ExecutionProxy(tmp_path, "https://central.test", "PRIVATE_TASK", "fixture", "inv", transport=transport_for(source, calls)) as proxy:
            reader, writer = await asyncio.open_connection("127.0.0.1", int(proxy.authority.split(":")[1]))
            body = b'{"model":"fixture"}'
            writer.write((f"POST /responses HTTP/1.1\r\nHost: {proxy.authority}\r\nAuthorization: Bearer {proxy.local_token}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n").encode() + body)
            await writer.drain()
            wire = await asyncio.wait_for(reader.read(), 2)
            writer.close()
            await writer.wait_closed()
            await wait_records(tmp_path, "pending")
            assert wire.count(b"HTTP/1.1") == 1
            assert DELTA in wire and LOCAL_ERROR in wire and b"PRIVATE" not in wire
            assert not wire.endswith(b"0\r\n\r\n")
        assert source.closed and len(calls) == 1
    asyncio.run(run())


def test_split_utf8_crlf_comments_and_raw_chunk_framing(tmp_path):
    async def run():
        payloads = [b": heartbeat\r\n\r\n", CREATED.replace(b"\n", b"\r\n"), DELTA.replace(b"\n", b"\r\n"), COMPLETED.replace(b"\n", b"\r\n")]
        source, calls = Stream([bytes([b]) for b in b"".join(payloads)]), []
        async with ExecutionProxy(tmp_path, "https://central.test", "PRIVATE_TASK", "fixture", "inv", transport=transport_for(source, calls)) as proxy:
            reader, writer = await asyncio.open_connection("127.0.0.1", int(proxy.authority.split(":")[1]))
            body = b'{"model":"fixture"}'
            writer.write((f"POST /responses HTTP/1.1\r\nHost: {proxy.authority}\r\nAuthorization: Bearer {proxy.local_token}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n").encode() + body)
            await writer.drain()
            head = await reader.readuntil(b"\r\n\r\n")
            assert b"Transfer-Encoding: chunked" in head and b"Content-Length" not in head
            chunks = []
            while True:
                size = int((await reader.readline()).strip(), 16)
                if not size:
                    assert await reader.readexactly(2) == b"\r\n"
                    break
                chunks.append(await reader.readexactly(size))
                assert await reader.readexactly(2) == b"\r\n"
            assert chunks == payloads
            assert await reader.read() == b""
            writer.close()
            await writer.wait_closed()
            await wait_records(tmp_path, "delivered")
        assert len(calls) == 1 and source.closed
    asyncio.run(run())


@pytest.mark.parametrize("status,headers", [(201, {}), (204, {}), (302, {}), (401, {}), (503, {}), (200, {"Content-Type": "text/plain"}), (200, {"Content-Encoding": "gzip"})])
def test_rejects_unexpected_status_type_or_encoding_before_headers(tmp_path, status, headers):
    async def run():
        calls, source = [], Stream([b"PRIVATE_UPSTREAM"])
        async with ExecutionProxy(tmp_path, "https://central.test", "PRIVATE_TASK", "fixture", "inv", transport=transport_for(source, calls, status=status, headers=headers)) as proxy:
            async with httpx.AsyncClient(trust_env=False) as client:
                response = await client.post(proxy.base_url + "/responses", json={"model": "fixture"}, headers={"Authorization": "Bearer " + proxy.local_token})
                assert response.status_code == 502 and "PRIVATE" not in response.text
            await wait_records(tmp_path, "pending")
        assert len(calls) == 1 and source.closed
    asyncio.run(run())


@pytest.mark.parametrize("timeout_kind", ["read", "total"])
def test_idle_or_total_timeout_closes_upstream_and_retains_pending(tmp_path, timeout_kind):
    async def run():
        gate, calls = asyncio.Event(), []
        source = Stream([CREATED, DELTA, None, COMPLETED], gate)
        options = {timeout_kind + "_timeout": .08}
        async with ExecutionProxy(tmp_path, "https://central.test", "PRIVATE_TASK", "fixture", "inv", transport=transport_for(source, calls), **options) as proxy:
            async with httpx.AsyncClient(trust_env=False) as client:
                with pytest.raises(httpx.RemoteProtocolError):
                    await client.post(proxy.base_url + "/responses", json={"model": "fixture"}, headers={"Authorization": "Bearer " + proxy.local_token})
            await wait_records(tmp_path, "pending")
        assert source.closed and len(calls) == 1
    asyncio.run(run())


def test_downstream_disconnect_cancels_paused_upstream(tmp_path):
    async def run():
        gate, calls = asyncio.Event(), []
        source = Stream([CREATED, DELTA, None, COMPLETED], gate)
        async with ExecutionProxy(tmp_path, "https://central.test", "PRIVATE_TASK", "fixture", "inv", transport=transport_for(source, calls)) as proxy:
            async with httpx.AsyncClient(trust_env=False) as client:
                async with client.stream("POST", proxy.base_url + "/responses", json={"model": "fixture"}, headers={"Authorization": "Bearer " + proxy.local_token}) as response:
                    async for chunk in response.aiter_bytes():
                        if DELTA == chunk:
                            break
            await wait_records(tmp_path, "pending")
        assert source.closed and len(calls) == 1 and not gate.is_set()
    asyncio.run(run())


def test_owned_metadata_preserves_timestamps_and_execution_header(tmp_path):
    from easel.platform.execution_records import operation_owner
    scope = {"user_id": "user", "product_id": "easel", "tenant_id": "tenant", "billing_account_id": "wallet", "session_id": "session", "authorization_epoch": 1}
    credential = {"task_token": "PRIVATE_TASK_TOKEN", "invocation_id": "inv", "model": "fixture", "scope": scope, "funding_mode": "test", "expires_at": time.time() + 300}
    async def run():
        calls, source = [], Stream([CREATED + DELTA + COMPLETED])
        async with ExecutionProxy(tmp_path, "https://central.test", credential["task_token"], "fixture", "inv", credential=credential, transport=transport_for(source, calls, headers={"X-Execution-ID": "exec_fixture"})) as proxy:
            async with httpx.AsyncClient(trust_env=False) as client:
                response = await client.post(proxy.base_url + "/responses", json={"model": "fixture"}, headers={"Authorization": "Bearer " + proxy.local_token})
                assert response.status_code == 200
            await wait_records(tmp_path, "delivered")
        record = records(tmp_path)[0]
        assert record["version"] == 2 and record["owner_key"] == operation_owner("https://central.test", scope)
        assert record["model"] == "fixture" and record["execution_id"] == "exec_fixture"
        assert record["created_at"] <= record["updated_at"]
        assert "PRIVATE" not in json.dumps(record) and "session" not in json.dumps(record)
        path = next((tmp_path / "operations").glob("*.json"))
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    asyncio.run(run())


@pytest.mark.parametrize("extra", [0, 1])
def test_event_exact_byte_limit_and_one_over(tmp_path, extra):
    from easel.platform.execution_proxy import MAX_EVENT
    padding = MAX_EVENT - len(event("response.output_text.delta", delta=""))
    sized = event("response.output_text.delta", delta="x" * (padding + extra))
    assert len(sized) == MAX_EVENT + extra
    async def run():
        calls, source = [], Stream([sized, COMPLETED])
        async with ExecutionProxy(tmp_path, "https://central.test", "PRIVATE_TASK", "fixture", "inv", transport=transport_for(source, calls)) as proxy:
            async with httpx.AsyncClient(trust_env=False) as client:
                if extra:
                    with pytest.raises(httpx.RemoteProtocolError):
                        await client.post(proxy.base_url + "/responses", json={"model": "fixture"}, headers={"Authorization": "Bearer " + proxy.local_token})
                else:
                    response = await client.post(proxy.base_url + "/responses", json={"model": "fixture"}, headers={"Authorization": "Bearer " + proxy.local_token})
                    assert response.content == sized + COMPLETED
            await wait_records(tmp_path, "pending" if extra else "delivered")
        assert len(calls) == 1 and source.closed
    asyncio.run(run())


@pytest.mark.parametrize("extra,giant", [(0, False), (1, False), (1, True)])
def test_total_exact_byte_limit_and_one_over(tmp_path, extra, giant):
    from easel.platform.execution_proxy import MAX_EVENT, MAX_RESULT
    # Heartbeats are complete SSE events and obey the independent event limit.
    remaining = MAX_RESULT - len(COMPLETED)
    heartbeat = b":" + b"x" * (MAX_EVENT - 3) + b"\n\n"
    chunks = [heartbeat] * (remaining // MAX_EVENT)
    rest = remaining % MAX_EVENT
    chunks += [b":" + b"x" * (rest - 3 + extra) + b"\n\n", COMPLETED]
    if giant:
        chunks = [b"".join(chunks)]
    async def run():
        calls, source = [], Stream(chunks)
        async with ExecutionProxy(tmp_path, "https://central.test", "PRIVATE_TASK", "fixture", "inv", transport=transport_for(source, calls)) as proxy:
            async with httpx.AsyncClient(trust_env=False) as client:
                if extra:
                    with pytest.raises(httpx.RemoteProtocolError):
                        await client.post(proxy.base_url + "/responses", json={"model": "fixture"}, headers={"Authorization": "Bearer " + proxy.local_token})
                else:
                    response = await client.post(proxy.base_url + "/responses", json={"model": "fixture"}, headers={"Authorization": "Bearer " + proxy.local_token})
                    assert len(response.content) == MAX_RESULT
            await wait_records(tmp_path, "pending" if extra else "delivered")
        assert len(calls) == 1 and source.closed
    asyncio.run(run())


def test_partial_event_limit_never_grows_parser_buffer_past_bound():
    from easel.platform.execution_proxy import MAX_EVENT, _SSEParser
    parser = _SSEParser()
    assert list(parser.feed(b"data: " + b"x" * (MAX_EVENT - 6))) == []
    assert len(parser.buffer) == MAX_EVENT
    with pytest.raises(ValueError):
        list(parser.feed(b"x" * (8 * 1024 * 1024)))
    assert len(parser.buffer) == MAX_EVENT


@pytest.mark.parametrize("blocked_drain", ["event", "terminal", "json"])
def test_backpressure_and_failed_final_drain_never_mark_delivered(tmp_path, blocked_drain):
    async def run():
        calls, drain_entered, release = [], asyncio.Event(), asyncio.Event()
        source = Stream([CREATED, DELTA, COMPLETED])
        if blocked_drain == "json":
            async def handle(request):
                calls.append(request)
                return httpx.Response(200, json={"ok": True})
            transport = httpx.MockTransport(handle)
        else:
            transport = transport_for(source, calls)
        class Writer:
            def __init__(self):
                self.writes = []
            def write(self, data):
                self.writes.append(data)
            async def drain(self):
                latest = self.writes[-1]
                blocked = ((blocked_drain == "event" and CREATED in latest)
                           or (blocked_drain == "terminal" and latest == b"0\r\n\r\n")
                           or blocked_drain == "json")
                if blocked:
                    drain_entered.set()
                    await release.wait()
        async with ExecutionProxy(tmp_path, "https://central.test", "PRIVATE_TASK", "fixture", "inv", transport=transport, drain_timeout=.08) as proxy:
            proxy._record("operation", "digest", "sending")
            writer = Writer()
            task = asyncio.create_task(proxy._forward(writer, "operation", "digest", b'{"model":"fixture"}', {"committed": False}))
            await asyncio.wait_for(drain_entered.wait(), 1)
            assert records(tmp_path)[0]["status"] == "sending"
            if blocked_drain == "event":
                assert source.pulls == 1  # No next read while native's first event drain blocks.
            with pytest.raises(TimeoutError):
                await task
            assert records(tmp_path)[0]["status"] != "delivered"
        assert len(calls) == 1 and (source.closed or blocked_drain == "json")
    asyncio.run(run())


def test_exit_cancels_after_delta_without_final_chunk(tmp_path):
    async def run():
        gate, calls = asyncio.Event(), []
        source = Stream([CREATED, DELTA, None, COMPLETED], gate)
        proxy = ExecutionProxy(tmp_path, "https://central.test", "PRIVATE_TASK", "fixture", "inv", transport=transport_for(source, calls))
        await proxy.__aenter__()
        async with httpx.AsyncClient(trust_env=False) as client:
            async with client.stream("POST", proxy.base_url + "/responses", json={"model": "fixture"}, headers={"Authorization": "Bearer " + proxy.local_token}) as response:
                iterator = response.aiter_bytes()
                assert await anext(iterator) == CREATED
                assert await anext(iterator) == DELTA
                await asyncio.wait_for(proxy.__aexit__(), 2)
                with pytest.raises(httpx.RemoteProtocolError):
                    await anext(iterator)
        assert records(tmp_path)[0]["status"] == "pending"
        assert source.closed and len(calls) == 1 and not proxy.tasks
    asyncio.run(run())


@pytest.mark.parametrize("terminal", ["response.completed", "response.incomplete"])
def test_valid_terminal_kind_delivers_and_ignores_unread_tail(tmp_path, terminal):
    async def run():
        calls = []
        last = event(terminal, response={"status": terminal.split(".")[1]})
        source = Stream([DELTA + last, b"invalid unread post-terminal"])
        async with ExecutionProxy(tmp_path, "https://central.test", "PRIVATE_TASK", "fixture", "inv", transport=transport_for(source, calls)) as proxy:
            async with httpx.AsyncClient(trust_env=False) as client:
                response = await client.post(proxy.base_url + "/responses", json={"model": "fixture"}, headers={"Authorization": "Bearer " + proxy.local_token})
                assert response.content == DELTA + last
            await wait_records(tmp_path, "delivered")
        assert len(calls) == 1 and source.closed and source.pulls == 1
        assert calls[0].headers["Accept-Encoding"] == "identity"
        assert "owner_key" not in records(tmp_path)[0]  # Preserve unowned test/legacy seam.
    asyncio.run(run())


@pytest.mark.parametrize("launcher", ["bundled", "node"])
def test_actual_native_first_token_before_terminal_then_tool_continuation(tmp_path, monkeypatch, launcher):
    import os
    import uuid
    from pathlib import Path
    from easel.harnesses.base import RunRequest
    from easel.harnesses.codex import CodexAdapter
    from easel.harnesses.commands import base_command
    from easel.platform.central import CentralClient
    from easel.platform import execution_proxy
    if launcher == "bundled":
        binary = os.environ.get("EASEL_TEST_CODEX_BINARY", "")
        if not binary:
            pytest.skip("Set EASEL_TEST_CODEX_BINARY for real packaged native acceptance")
        assert Path(binary).is_file()
        command = [binary, "app-server"]
    else:
        if os.environ.get("EASEL_TEST_CODEX_NODE") != "1":
            pytest.skip("Set EASEL_TEST_CODEX_NODE=1 for installed Node native acceptance")
        command = base_command("codex") + ["app-server"]
        assert len(command) == 3 and command[1].endswith(".js")
    scope = {"user_id": "owned-user", "product_id": "easel", "tenant_id": "owned-tenant", "billing_account_id": "owned-wallet", "session_id": "owned-session", "authorization_epoch": 0}
    invocation = str(uuid.uuid4())
    monkeypatch.setattr(CentralClient, "task_credential", lambda self, account, model, origin, scope, key: {
        "task_token": "PRIVATE_NATIVE_GATED_TOKEN", "invocation_id": invocation, "model": model,
        "scope": scope, "funding_mode": "test", "expires_at": time.time() + 300})
    async def run():
        gate, first_token = asyncio.Event(), asyncio.Event()
        calls, sources, output = [], [], []
        async def handle(request):
            body = json.loads(request.content)
            calls.append((request.headers["Idempotency-Key"], body))
            rid = "resp_gated_" + str(len(calls))
            created = event("response.created", response={"id": rid, "object": "response", "status": "in_progress", "output": []})
            text = "NATIVE_EARLY_OK" if len(calls) == 1 else "NATIVE_FINAL_OK"
            message = {"id": "msg_" + rid, "type": "message", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": text, "annotations": []}]}
            early = [created,
                     event("response.output_item.added", output_index=0, item={**message, "content": [], "status": "in_progress"}),
                     event("response.content_part.added", item_id=message["id"], output_index=0, content_index=0, part={"type": "output_text", "text": "", "annotations": []}),
                     event("response.output_text.delta", item_id=message["id"], output_index=0, content_index=0, delta=text)]
            tail = [event("response.output_text.done", item_id=message["id"], output_index=0, content_index=0, text=text),
                    event("response.content_part.done", item_id=message["id"], output_index=0, content_index=0, part=message["content"][0]),
                    event("response.output_item.done", output_index=0, item=message)]
            items = [message]
            if len(calls) == 1:
                tool = {"id": "fc_gated", "type": "function_call", "call_id": "call_gated", "name": "update_plan", "arguments": json.dumps({"plan": [{"step": "Gated fixture tool", "status": "completed"}]}), "status": "completed"}
                items.append(tool)
                tail += [event("response.output_item.added", output_index=1, item={**tool, "arguments": "", "status": "in_progress"}),
                         event("response.function_call_arguments.delta", item_id=tool["id"], output_index=1, delta=tool["arguments"]),
                         event("response.function_call_arguments.done", item_id=tool["id"], output_index=1, arguments=tool["arguments"]),
                         event("response.output_item.done", output_index=1, item=tool)]
            tail += [event("response.completed", response={"id": rid, "object": "response", "status": "completed", "output": items, "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}})]
            source = Stream(early + ([None] if len(calls) == 1 else []) + tail, gate)
            sources.append(source)
            return httpx.Response(200, stream=source, headers={"Content-Type": "text/event-stream", "X-Execution-ID": "exec_" + rid})
        class Proxy(execution_proxy.ExecutionProxy):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs, transport=httpx.MockTransport(handle))
        monkeypatch.setattr(execution_proxy, "ExecutionProxy", Proxy)
        request = RunRequest("Reply briefly.", tmp_path, {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "TMPDIR": str(tmp_path)},
                             model="fixture", sandbox="read-only", command=tuple(command), session_key="gated-native",
                             service_source="subscription", subscription_backend="central", account_id="owned-user",
                             platform_url="http://127.0.0.1:1", central_scope=scope, invocation_key="gated-turn")
        def emit(kind, text):
            output.append((kind, text))
            if kind == "token" and "NATIVE_EARLY_OK" in text:
                first_token.set()
        async def ask(_):
            raise AssertionError("Fixture must not ask for permission")
        task = asyncio.create_task(CodexAdapter().run(request, emit, ask, lambda _: None))
        try:
            await asyncio.wait_for(first_token.wait(), 12)
            assert not gate.is_set() and len(calls) == 1
            operation_root = tmp_path / "outputs" / "_harness" / "central-execution"
            assert records(operation_root)[0]["status"] == "sending"
            gate.set()
            await asyncio.wait_for(task, 20)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert len(calls) == 2 and calls[0][0] != calls[1][0]
        assert any(item.get("type") == "function_call_output" for item in calls[1][1]["input"])
        assert any("NATIVE_FINAL_OK" in text for _, text in output)
        assert all(source.closed for source in sources)
        assert all(row["status"] == "delivered" and row["version"] == 2 for row in records(operation_root))
        for path in (operation_root / "operations").glob("*.json"):
            assert "PRIVATE" not in path.read_text() and "NATIVE_EARLY_OK" not in path.read_text()
    asyncio.run(run())


@pytest.mark.parametrize("separator", [b"\n\r\n", b"\r\n\n"])
def test_mixed_sse_line_endings_are_blank_lines(separator):
    from easel.platform.execution_proxy import _SSEParser
    raw = b'data: {"type":"response.output_text.delta","delta":"ok"}' + separator
    parser = _SSEParser()
    assert list(parser.feed(raw)) == [(raw, {"type": "response.output_text.delta", "delta": "ok"})]


def test_real_upstream_chunked_is_decoded_before_local_event_chunks(tmp_path):
    async def run():
        gate, central_calls = asyncio.Event(), []
        async def central(reader, writer):
            try:
                headers = await reader.readuntil(b"\r\n\r\n")
                central_calls.append(headers)
                length = int(next(line.split(b":", 1)[1] for line in headers.split(b"\r\n") if line.lower().startswith(b"content-length:")))
                await reader.readexactly(length)
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n")
                for content in [CREATED, DELTA]:
                    # Upstream HTTP frames intentionally do not align with SSE frames.
                    for offset in range(0, len(content), 7):
                        piece = content[offset:offset + 7]
                        writer.write(f"{len(piece):x}\r\n".encode() + piece + b"\r\n")
                    await writer.drain()
                await asyncio.wait_for(gate.wait(), 2)
                writer.write(f"{len(COMPLETED):x}\r\n".encode() + COMPLETED + b"\r\n0\r\n\r\n")
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
        server = await asyncio.start_server(central, "127.0.0.1", 0)
        origin = "http://127.0.0.1:" + str(server.sockets[0].getsockname()[1])
        try:
            async with ExecutionProxy(tmp_path, origin, "PRIVATE_TASK", "fixture", "inv") as proxy:
                async with httpx.AsyncClient(trust_env=False) as client:
                    async with client.stream("POST", proxy.base_url + "/responses", json={"model": "fixture"}, headers={"Authorization": "Bearer " + proxy.local_token}) as response:
                        assert response.headers["transfer-encoding"] == "chunked"
                        chunks = []
                        async for chunk in response.aiter_bytes():
                            chunks.append(chunk)
                            if chunk == DELTA:
                                assert not gate.is_set()
                                gate.set()
                        assert b"".join(chunks) == CREATED + DELTA + COMPLETED
                await wait_records(tmp_path, "delivered")
        finally:
            server.close()
            await server.wait_closed()
        assert len(central_calls) == 1
    asyncio.run(run())


def test_json_failed_drain_closes_without_sse_or_second_status(tmp_path):
    async def run():
        async def handle(_):
            return httpx.Response(200, json={"ok": True})
        class Writer:
            def __init__(self):
                self.output = []
            def write(self, data):
                self.output.append(data)
            async def drain(self):
                raise OSError("client closed")
            def close(self):
                pass
            async def wait_closed(self):
                pass
        async with ExecutionProxy(tmp_path, "https://central.test", "PRIVATE_TASK", "fixture", "inv", transport=httpx.MockTransport(handle)) as proxy:
            reader = asyncio.StreamReader()
            body = b'{"model":"fixture"}'
            reader.feed_data((f"POST /responses HTTP/1.1\r\nHost: {proxy.authority}\r\nAuthorization: Bearer {proxy.local_token}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n").encode() + body)
            writer = Writer()
            await proxy._handle(reader, writer)
            assert len(writer.output) == 1
            assert b"event: error" not in b"".join(writer.output)
            assert records(tmp_path)[0]["status"] == "pending"
    asyncio.run(run())


@pytest.mark.parametrize("phase", ["event_timeout", "final_timeout", "final_cancel"])
def test_handler_failed_or_cancelled_drain_persists_pending_and_closes_source(tmp_path, phase):
    async def run():
        entered = asyncio.Event()
        calls, source = [], Stream([CREATED, DELTA, COMPLETED])
        class Writer:
            def __init__(self):
                self.writes, self.closed = [], False
            def write(self, data):
                self.writes.append(data)
            async def drain(self):
                block = ((phase == "event_timeout" and CREATED in self.writes[-1])
                         or (phase.startswith("final") and self.writes[-1] == b"0\r\n\r\n"))
                if block:
                    entered.set()
                    await asyncio.Event().wait()
            def close(self):
                self.closed = True
            async def wait_closed(self):
                pass
        async with ExecutionProxy(tmp_path, "https://central.test", "PRIVATE_TASK", "fixture", "inv", transport=transport_for(source, calls), drain_timeout=.08) as proxy:
            reader = asyncio.StreamReader()
            body = b'{"model":"fixture"}'
            reader.feed_data((f"POST /responses HTTP/1.1\r\nHost: {proxy.authority}\r\nAuthorization: Bearer {proxy.local_token}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n").encode() + body)
            writer = Writer()
            task = asyncio.create_task(proxy._handle(reader, writer))
            await asyncio.wait_for(entered.wait(), 1)
            assert records(tmp_path)[0]["status"] == "sending"
            if phase == "final_cancel":
                task.cancel()
            result = await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 2)
            if phase == "final_cancel":
                assert isinstance(result[0], asyncio.CancelledError)
            assert records(tmp_path)[0]["status"] == "pending"
            assert writer.closed and source.closed and len(calls) == 1
            assert b"".join(writer.writes).count(b"HTTP/1.1") == 1
            assert not proxy.tasks
    asyncio.run(run())


@pytest.mark.parametrize("execution_id", ["bad/path", "bad header", "x" * 129])
def test_invalid_execution_header_is_ignored_not_persisted(tmp_path, execution_id):
    scope = {"user_id": "user", "product_id": "easel", "tenant_id": "tenant", "billing_account_id": "wallet", "session_id": "session", "authorization_epoch": 1}
    credential = {"task_token": "PRIVATE_TASK_TOKEN", "invocation_id": "inv", "model": "fixture", "scope": scope, "funding_mode": "test", "expires_at": time.time() + 300}
    async def run():
        calls, source = [], Stream([COMPLETED])
        async with ExecutionProxy(tmp_path, "https://central.test", credential["task_token"], "fixture", "inv", credential=credential, transport=transport_for(source, calls, headers={"X-Execution-ID": execution_id})) as proxy:
            async with httpx.AsyncClient(trust_env=False) as client:
                response = await client.post(proxy.base_url + "/responses", json={"model": "fixture"}, headers={"Authorization": "Bearer " + proxy.local_token})
                assert response.content == COMPLETED
            await wait_records(tmp_path, "delivered")
        assert "execution_id" not in records(tmp_path)[0]
        assert len(calls) == 1
    asyncio.run(run())
