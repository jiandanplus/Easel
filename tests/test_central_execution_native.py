"""Central native isolation, binding and operation identity contracts."""
import asyncio
import hashlib
import json
import stat

import httpx
import pytest

from easel.harnesses.base import HarnessError, RunRequest


def test_missing_backend_is_legacy_and_binding_cannot_rebind(tmp_path):
    from easel.harnesses import store
    config = store.settings(tmp_path / "outputs", {})
    assert config["subscription_backend"] == "legacy"
    scope = {"user_id": "u", "billing_account_id": "w"}
    store.bind(tmp_path, "chat", "codex", service_source="subscription", account_id="u",
               subscription_backend="central", platform_url="https://central.test", central_scope=scope)
    with pytest.raises(HarnessError):
        store.bind(tmp_path, "chat", "codex", service_source="subscription", account_id="u")
    with pytest.raises(HarnessError):
        store.bind(tmp_path, "chat", "codex", service_source="subscription", account_id="u",
                   subscription_backend="central", platform_url="https://central.test", central_scope={**scope, "billing_account_id": "other"})


def test_proxy_distinct_operations_durable_and_secret_free(tmp_path):
    from easel.platform.execution_proxy import ExecutionProxy
    calls = []
    async def handler(request):
        key = request.headers["Idempotency-Key"]
        record = json.loads((tmp_path / "operations" / (key + ".json")).read_text())
        assert record["body_hash"] == hashlib.sha256(request.content).hexdigest()
        assert record["status"] == "sending"
        calls.append((key, request.headers["Authorization"]))
        return httpx.Response(200, json={"id": key})
    async def run():
        async with ExecutionProxy(tmp_path, "https://central.test", "PRIVATE-CENTRAL", "fixture", "invocation", transport=httpx.MockTransport(handler)) as proxy:
            async with httpx.AsyncClient(trust_env=False) as client:
                for _ in range(2):
                    response = await client.post(proxy.base_url + "/responses", json={"model": "fixture", "input": "hello"}, headers={"Authorization": "Bearer " + proxy.local_token})
                    assert response.status_code == 200
                response = await client.post(proxy.base_url + "/responses", json={"model": "fixture"})
                assert response.status_code == 403
                response = await client.post(proxy.base_url + "/responses", json={"model": "other"}, headers={"Authorization": "Bearer " + proxy.local_token})
                assert response.status_code == 400
    asyncio.run(run())
    assert len(calls) == 2 and calls[0][0] != calls[1][0]
    assert all(token == "Bearer PRIVATE-CENTRAL" for _, token in calls)
    for file in (tmp_path / "operations").glob("*.json"):
        assert "PRIVATE-CENTRAL" not in file.read_text() and "hello" not in file.read_text()
        assert stat.S_IMODE(file.stat().st_mode) == 0o600


def test_central_prepare_needs_proxy_and_strips_provider_credentials(tmp_path):
    from easel.harnesses.runtime import prepare_subscription
    request = RunRequest("hi", tmp_path, {"PATH": "/bin", "NEW_API_KEY": "custom", "ANTHROPIC_API_KEY": "custom", "HOME": str(tmp_path)}, model="fixture", service_source="subscription", account_id="u", platform_url="https://central.test", subscription_backend="central")
    with pytest.raises(HarnessError):
        prepare_subscription(request, ["codex", "app-server"])
    prepared, command = prepare_subscription(request, ["codex", "app-server"], proxy_url="http://127.0.0.1:12345", proxy_token="LOCAL-ONLY")
    assert "NEW_API_KEY" not in prepared.env and "ANTHROPIC_API_KEY" not in prepared.env
    assert prepared.env["EASEL_SUBSCRIPTION_API_KEY"] == "LOCAL-ONLY"
    assert prepared.env["EASEL_SUBSCRIPTION_BACKEND"] == "central"
    assert "LOCAL-ONLY" not in str(command)
    assert any("request_max_retries=0" in arg for arg in command)
    assert any("stream_max_retries=0" in arg for arg in command)


def test_media_gate_precedes_dotenv_and_subprocess(tmp_path, monkeypatch):
    import subprocess
    import sys
    from pathlib import Path
    scripts = Path(__file__).resolve().parents[1] / "skills" / "shared" / "scripts"
    for name in ("ai_image", "ai_video", "ai_music", "voice_clone", "tts", "multivoice", "asr"):
        result = subprocess.run([sys.executable, str(scripts / (name + ".py")), "--help"],
                                env={"PATH": "/usr/bin:/bin", "EASEL_SUBSCRIPTION_BACKEND": "central"},
                                capture_output=True, text=True)
        assert result.returncode != 0
        assert "只能在内置 Pi 中使用" in result.stderr


def test_real_native_fixture_tool_cycle_and_retry_zero(tmp_path, monkeypatch):
    """Opt-in real bundled 0.146 binary, isolated loopback fake central only."""
    import os
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path
    from easel.harnesses.codex import CodexAdapter
    from easel.platform.central import CentralClient
    binary = os.environ.get("EASEL_TEST_CODEX_BINARY", "")
    if not binary:
        pytest.skip("Set EASEL_TEST_CODEX_BINARY to run real native acceptance")
    assert Path(binary).is_file()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.server.calls.append((self.headers["Idempotency-Key"], body))
            if self.server.mode == "http_error":
                self.send_response(503)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")
                return
            events = []
            def event(kind, **fields):
                events.append("event: " + kind + "\ndata: " + json.dumps({"type": kind, **fields}) + "\n\n")
            rid = "resp_fixture_" + str(len(self.server.calls))
            event("response.created", response={"id": rid, "object": "response", "status": "in_progress", "output": []})
            if self.server.mode != "stream_drop":
                if len(self.server.calls) == 1:
                    item = {"id": "fc_plan", "type": "function_call", "call_id": "call_plan", "name": "update_plan", "arguments": json.dumps({"plan": [{"step": "Fixture tool", "status": "completed"}]}), "status": "completed"}
                    event("response.output_item.added", output_index=0, item={**item, "arguments": "", "status": "in_progress"})
                    event("response.function_call_arguments.delta", item_id=item["id"], output_index=0, delta=item["arguments"])
                    event("response.function_call_arguments.done", item_id=item["id"], output_index=0, arguments=item["arguments"])
                else:
                    item = {"id": "msg_fixture", "type": "message", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": "FIXTURE_OK", "annotations": []}]}
                    event("response.output_item.added", output_index=0, item={**item, "content": [], "status": "in_progress"})
                    event("response.output_text.delta", item_id=item["id"], output_index=0, content_index=0, delta="FIXTURE_OK")
                event("response.output_item.done", output_index=0, item=item)
                event("response.completed", response={"id": rid, "object": "response", "status": "completed", "output": [item], "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}})
            output = "".join(events).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(output)))
            self.end_headers()
            self.wfile.write(output)
    monkeypatch.setattr(CentralClient, "task_credential", lambda *args: {"task_token": "PRIVATE_FIXTURE_CENTRAL_TOKEN", "invocation_id": "inv_fixture", "model": args[2], "scope": args[4], "funding_mode": "test", "expires_at": __import__("time").time() + 300})
    for mode in ("tool", "http_error", "stream_drop"):
        root = tmp_path / mode
        root.mkdir()
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.mode, server.calls = mode, []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        output = []
        request = RunRequest("Reply briefly.", root, {"PATH": "/usr/bin:/bin", "HOME": str(root), "TMPDIR": str(root)},
                             model="fixture", sandbox="read-only", command=(binary, "app-server"),
                             session_key="owned-test", service_source="subscription", subscription_backend="central",
                             account_id="user", platform_url=f"http://127.0.0.1:{server.server_port}", invocation_key="inv-key")
        async def run():
            async def ask(_):
                raise AssertionError("Fixture must not ask for permission")
            await asyncio.wait_for(CodexAdapter().run(request, lambda kind, text: output.append((kind, text)), ask, lambda value: None), 30)
            from easel.harnesses.rpc import _POOLS
            assert not _POOLS.get(asyncio.get_running_loop())
        try:
            if mode == "tool":
                asyncio.run(run())
                assert len(server.calls) == 2
                assert server.calls[0][0] != server.calls[1][0]
                assert any(item.get("type") == "function_call_output" for item in server.calls[1][1]["input"])
                assert any("FIXTURE_OK" in text for kind, text in output)
            else:
                with pytest.raises(HarnessError):
                    asyncio.run(run())
                assert len(server.calls) == 1
            for path in root.rglob("*"):
                if path.is_file() and path.stat().st_size < 8 * 1024 * 1024:
                    assert b"PRIVATE_FIXTURE_CENTRAL_TOKEN" not in path.read_bytes()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)


def test_public_catalog_and_execution_views_reject_malformed_money_and_scope_fields():
    from copy import deepcopy
    from easel.platform.central import CentralClient, CentralError
    model = {"id": "fixture", "name": "Fixture", "version": "1", "capability": "text", "test_only": True, "readiness": "test",
             "rate": {"version": "1", "input": {"numerator": "100", "denominator": "1"}, "output": {"numerator": "200", "denominator": "1"}},
             "limits": {"max_input_bytes": 1048576, "max_input_tokens": 65536, "max_output_tokens": 4096}}
    assert CentralClient._execution_view("/v2/products/easel/models", {"models": [model]}) == {"models": [model]}
    for invalid in (0, "0", "-1", "1.5", "9" * 20):
        changed = deepcopy(model)
        changed["rate"]["input"]["denominator"] = invalid
        with pytest.raises(CentralError):
            CentralClient._execution_view("/v2/products/easel/models", {"models": [changed]})
    job = {"id": "job", "model": "fixture", "rate_version": "1", "created_at": "2026-10-22T00:00:00Z", "updated_at": "2026-10-22T00:00:00Z", "status": "pending_reconciliation", "funding_mode": "test", "amount": "100", "actual": None,
           "provider_url": "https://private.invalid", "scope": {"user_id": "other"}}
    safe = CentralClient._execution_view("/v2/executions", {"executions": [job]})["executions"][0]
    assert "scope" not in safe and "provider_url" not in safe and safe["actual"] is None
    with pytest.raises(CentralError):
        CentralClient._execution_view("/v2/executions", {"executions": [{**job, "amount": 100}]})


def test_central_mint_is_private_and_scope_checked(tmp_path):
    import time
    from easel.platform.central import CentralClient
    scope = {"user_id": "user", "product_id": "easel", "tenant_id": "tenant", "billing_account_id": "wallet", "session_id": "session", "authorization_epoch": 0}
    calls = []
    def handler(request):
        calls.append(request.url.path)
        assert request.url.path == "/v2/task-tokens"
        return httpx.Response(200, json={"task_token": "PRIVATE_TASK_TOKEN", "invocation_id": "inv", "model": "fixture", "scope": scope, "funding_mode": "test", "base_url_path": "/v2", "expires_at": time.time() + 100})
    client = CentralClient(tmp_path, url="https://central.test", transport=httpx.MockTransport(handler))
    client.save_auth({"access_token": "PRIVATE_ACCESS_TOKEN", "refresh_token": "PRIVATE_REFRESH_TOKEN", "session_id": "session", "expires_at": time.time() + 600, "refresh_expires_at": time.time() + 1200,
                      "scope": scope, "account": {"id": "user", "email": "user@example.test", "email_verified": True}})
    credential = client.task_credential("user", "fixture", "https://central.test", scope, "invocation")
    assert credential["task_token"] == "PRIVATE_TASK_TOKEN"
    assert "PRIVATE_TASK_TOKEN" not in client.store.path.read_text()
    with pytest.raises(HarnessError):
        client.task_credential("user", "fixture", "https://central.test", {**scope, "billing_account_id": "other"}, "invocation")
    with pytest.raises(HarnessError):
        client.call("POST", "/v2/task-tokens", {"model": "fixture", "invocation_key": "invocation"})
    assert calls == ["/v2/task-tokens"]


def test_proxy_cancellation_keeps_pending_key_and_closes_owned_listener(tmp_path):
    from easel.platform.execution_proxy import ExecutionProxy
    async def run():
        accepted = asyncio.Event()
        async def handler(request):
            accepted.set()
            await asyncio.Event().wait()
        proxy = ExecutionProxy(tmp_path, "https://central.test", "PRIVATE-TASK", "fixture", "inv", transport=httpx.MockTransport(handler))
        await proxy.__aenter__()
        async with httpx.AsyncClient(trust_env=False) as client:
            task = asyncio.create_task(client.post(proxy.base_url + "/responses", json={"model": "fixture", "input": "hi"}, headers={"Authorization": "Bearer " + proxy.local_token}))
            await asyncio.wait_for(accepted.wait(), 3)
            records = list((tmp_path / "operations").glob("*.json"))
            assert len(records) == 1
            key = json.loads(records[0].read_text())["operation_key"]
            await asyncio.wait_for(proxy.__aexit__(), 3)
            result = json.loads(records[0].read_text())
            assert result["status"] == "pending" and result["operation_key"] == key
            assert not proxy.server.is_serving() and not proxy.tasks
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())


_SCOPE = {"user_id": "user", "product_id": "easel", "tenant_id": "tenant",
          "billing_account_id": "wallet", "session_id": "session", "authorization_epoch": 0}


def test_console_turns_allocate_distinct_invocations_keep_frozen_chat(tmp_path, monkeypatch):
    from easel.harnesses import console_turn
    from easel.service_contracts import TaskContext
    from easel import harnesses
    context = TaskContext("subscription", "local", "codex", "fixture", "user",
                          "https://central.test", "central", dict(_SCOPE), "accepted-chat-key")
    requests = []
    class Adapter:
        async def run(self, request, emit, ask, bind):
            requests.append(request)
            bind("native-chat")
    monkeypatch.setattr(harnesses, "adapter", lambda _: Adapter())
    async def run():
        config = {"runtime": "codex", "sandbox": "read-only", "codex_auto_approve": False}
        _, native = await console_turn("one", tmp_path, {}, config=config, context=context)
        await console_turn("two", tmp_path, {}, native, config=config, context=context)
    asyncio.run(run())
    assert requests[0].invocation_key != requests[1].invocation_key
    assert all(r.invocation_key and r.central_scope == _SCOPE and r.account_id == "user"
               and r.platform_url == context.platform_url for r in requests)
    assert requests[1].native_id == "native-chat"
    assert context.invocation_key == "accepted-chat-key"


@pytest.mark.parametrize("changed", [None, "invocation_id", "model", "funding_mode", *_SCOPE, "revoked", "expiry"])
def test_proxy_private_renewal_after_long_tool_pause(tmp_path, changed):
    from datetime import datetime, timezone
    from easel.platform.execution_proxy import ExecutionProxy
    now = [1800000000.0]
    calls, renewals = [], []
    initial = {"task_token": "PRIVATE_INITIAL_TOKEN", "expires_at": now[0] + 300,
               "invocation_id": "inv", "model": "fixture", "scope": dict(_SCOPE), "funding_mode": "test"}
    async def renew():
        renewals.append(True)
        if changed == "revoked":
            raise HarnessError("revoked PRIVATE_INITIAL_TOKEN")
        value = {**initial, "scope": dict(_SCOPE), "task_token": "PRIVATE_RENEWED_TOKEN",
                 "expires_at": datetime.fromtimestamp(now[0] + 300, timezone.utc).isoformat()}
        if changed in _SCOPE:
            value["scope"][changed] = "changed"
        elif changed == "expiry":
            value["expires_at"] = now[0] - 1
        elif changed:
            value[changed] = "changed"
        return value
    async def handler(request):
        calls.append((request.headers["Idempotency-Key"], request.headers["Authorization"]))
        return httpx.Response(200, json={"ok": True})
    async def run():
        proxy = ExecutionProxy(tmp_path, "https://central.test", initial["task_token"], "fixture", "inv",
                               credential=initial, renew=renew, clock=lambda: now[0], transport=httpx.MockTransport(handler))
        async with proxy:
            async with httpx.AsyncClient(trust_env=False) as client:
                async def post():
                    return await client.post(proxy.base_url + "/responses", json={"model": "fixture", "input": "tool result"}, headers={"Authorization": "Bearer " + proxy.local_token})
                assert (await post()).status_code == 200
                assert not renewals
                now[0] += 301  # tool/approval pause exceeds task TTL, not invocation TTL
                result = await post()
                assert result.status_code == (200 if changed is None else 502)
                assert "PRIVATE" not in result.text
                assert proxy.accepted == (2 if changed is None else 1)
                if changed is None:
                    for _ in range(30):
                        assert (await post()).status_code == 200
                    assert (await post()).status_code == 429
                    assert proxy.accepted == 32  # renewal never resets operation cap
        assert proxy._task_token == "" and not proxy.tasks
    asyncio.run(run())
    assert len(renewals) == 1
    assert len(calls) == (32 if changed is None else 1)
    if changed is None:
        assert calls[0][0] != calls[1][0]
        assert calls[1][1] == "Bearer PRIVATE_RENEWED_TOKEN"
    for path in (tmp_path / "operations").glob("*.json"):
        assert "PRIVATE" not in path.read_text()
        assert json.loads(path.read_text())["invocation_id"] == "inv"


@pytest.mark.parametrize("cancel_during_renewal", [False, True])
def test_proxy_renewal_never_resends_uncertain_operation_and_cleans_cancel(tmp_path, cancel_during_renewal):
    from easel.platform.execution_proxy import ExecutionProxy
    now, calls = [1800000000.0], []
    credential = {"task_token": "PRIVATE_INITIAL_TOKEN", "expires_at": now[0] + 300,
                  "invocation_id": "inv", "model": "fixture", "scope": dict(_SCOPE), "funding_mode": "test"}
    async def run():
        renewing, cancelled = asyncio.Event(), asyncio.Event()
        async def renew():
            renewing.set()
            if cancel_during_renewal:
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            return {**credential, "task_token": "PRIVATE_RENEWED_TOKEN", "expires_at": now[0] + 300}
        async def handler(request):
            calls.append(request.headers["Idempotency-Key"])
            raise httpx.ReadTimeout("uncertain PRIVATE_INITIAL_TOKEN")
        proxy = ExecutionProxy(tmp_path, "https://central.test", credential["task_token"], "fixture", "inv",
                               credential=credential, renew=renew, clock=lambda: now[0], transport=httpx.MockTransport(handler))
        await proxy.__aenter__()
        async with httpx.AsyncClient(trust_env=False) as client:
            async def post():
                return await client.post(proxy.base_url + "/responses", json={"model": "fixture"}, headers={"Authorization": "Bearer " + proxy.local_token})
            assert (await post()).status_code == 502
            first = list((tmp_path / "operations").glob("*.json"))[0]
            assert json.loads(first.read_text())["status"] == "pending"
            now[0] += 301
            next_post = asyncio.create_task(post())
            await asyncio.wait_for(renewing.wait(), 3)
            if not cancel_during_renewal:
                assert (await next_post).status_code == 502
            await asyncio.wait_for(proxy.__aexit__(), 3)
            await asyncio.gather(next_post, return_exceptions=True)
            assert not proxy.tasks and not proxy.server.is_serving()
            assert proxy._task_token == "" and proxy.local_token == "" and proxy._renew is None
            assert json.loads(first.read_text())["operation_key"] == calls[0]
            assert json.loads(first.read_text())["status"] == "pending"
            if cancel_during_renewal:
                assert cancelled.is_set()
    asyncio.run(run())
    assert len(calls) == (1 if cancel_during_renewal else 2)
    assert len(set(calls)) == len(calls)
    records = list((tmp_path / "operations").glob("*.json"))
    assert len(records) == len(calls)  # cancelled mint allocates no operation
    assert all("PRIVATE" not in path.read_text() for path in records)


def test_adapter_renewal_captures_original_account_origin_scope_and_turn_key(tmp_path, monkeypatch):
    from easel.harnesses.codex import CodexAdapter
    from easel.platform.central import CentralClient
    from easel.platform import execution_proxy
    calls, proxies = [], []
    def mint(self, account, model, origin, scope, invocation_key):
        calls.append((account, model, origin, scope, invocation_key))
        return {"task_token": "PRIVATE_ADAPTER_TOKEN", "invocation_id": "inv", "model": model,
                "scope": dict(_SCOPE), "funding_mode": "test", "expires_at": __import__("time").time() + 300}
    monkeypatch.setattr(CentralClient, "task_credential", mint)
    class Proxy(execution_proxy.ExecutionProxy):
        async def __aenter__(self):
            self.base_url = "http://127.0.0.1:12345"
            proxies.append(self)
            return self
        async def __aexit__(self, *args):
            pass
    monkeypatch.setattr(execution_proxy, "ExecutionProxy", Proxy)
    request = RunRequest("hi", tmp_path, {}, model="fixture", command=("fixture-codex",),
                         service_source="subscription", subscription_backend="central", account_id="user",
                         platform_url="https://central.test", central_scope=dict(_SCOPE), invocation_key="one-turn")
    async def native(self, prepared, emit, ask, bind, prepared_command=None):
        assert "PRIVATE_ADAPTER_TOKEN" not in str(prepared.env) + str(prepared_command)
        request.central_scope["billing_account_id"] = "changed-after-acceptance"
        await proxies[0]._renew()
    monkeypatch.setattr(CodexAdapter, "_run", native)
    asyncio.run(CodexAdapter().run(request, lambda *args: None, lambda *args: None, lambda *args: None))
    assert calls == [("user", "fixture", "https://central.test", _SCOPE, "one-turn")] * 2
    assert all(b"PRIVATE_ADAPTER_TOKEN" not in p.read_bytes() for p in tmp_path.rglob("*") if p.is_file())
