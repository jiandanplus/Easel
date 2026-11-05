"""Actual installed Node Codex -> local proxy -> production gateway validator."""
import asyncio
import json
import os
import time
from pathlib import Path

import httpx
import pytest


def test_actual_node_payload_validates_and_metadata_stays_local(tmp_path, monkeypatch):
    if os.environ.get("EASEL_TEST_CODEX_NODE") != "1":
        pytest.skip("Set EASEL_TEST_CODEX_NODE=1 for the installed Node Codex fixture")
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "services/platform/src"))
    from platform_service.execution_protocol import validate_request
    from platform_service.errors import PlatformError
    from easel.harnesses.base import RunRequest
    from easel.harnesses.codex import CodexAdapter
    from easel.harnesses.commands import base_command
    from easel.platform.central import CentralClient
    from easel.platform import execution_proxy

    command = base_command("codex") + ["app-server"]
    assert len(command) == 3 and command[1].endswith(".js"), "Acceptance requires the actual Node launcher"
    monkeypatch.setattr(CentralClient, "task_credential", lambda self, account, model, origin, scope, invocation: {
        "task_token": "PRIVATE_OWNED_FIXTURE_TOKEN", "invocation_id": "owned-invocation", "model": model,
        "scope": scope, "funding_mode": "test", "expires_at": time.time() + 300})
    observations, errors, upstream = [], [], []

    async def transport(request):
        body = json.loads(request.content)
        observations.append((request.headers["Idempotency-Key"], isinstance(body.get("client_metadata"), dict)))
        try:
            validated, _ = validate_request(body, "fixture")
        except PlatformError as error:
            errors.append(error.code)
            return httpx.Response(error.status, json={"error": {"code": error.code}})
        assert "client_metadata" not in validated
        upstream.append(validated)
        events = []
        def event(kind, **fields):
            events.append("event: " + kind + "\ndata: " + json.dumps({"type": kind, **fields}) + "\n\n")
        rid = "resp_owned_" + str(len(upstream))
        event("response.created", response={"id": rid, "object": "response", "status": "in_progress", "output": []})
        if len(upstream) == 1:
            item = {"id": "fc_plan", "type": "function_call", "call_id": "call_plan", "name": "update_plan", "arguments": json.dumps({"plan": [{"step": "Fixture tool", "status": "completed"}]}), "status": "completed"}
            event("response.output_item.added", output_index=0, item={**item, "arguments": "", "status": "in_progress"})
            event("response.function_call_arguments.delta", item_id=item["id"], output_index=0, delta=item["arguments"])
            event("response.function_call_arguments.done", item_id=item["id"], output_index=0, arguments=item["arguments"])
        else:
            item = {"id": "msg_owned", "type": "message", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": "OWNED_PROTOCOL_OK", "annotations": []}]}
            event("response.output_item.added", output_index=0, item={**item, "content": [], "status": "in_progress"})
            event("response.output_text.delta", item_id=item["id"], output_index=0, content_index=0, delta="OWNED_PROTOCOL_OK")
        event("response.output_item.done", output_index=0, item=item)
        event("response.completed", response={"id": rid, "object": "response", "status": "completed", "output": [item], "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}})
        return httpx.Response(200, content="".join(events), headers={"Content-Type": "text/event-stream"})

    class Proxy(execution_proxy.ExecutionProxy):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs, transport=httpx.MockTransport(transport))
    monkeypatch.setattr(execution_proxy, "ExecutionProxy", Proxy)
    output = []
    request = RunRequest("Reply briefly.", tmp_path, {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "TMPDIR": str(tmp_path)},
                         model="fixture", sandbox="read-only", command=tuple(command), session_key="owned-test",
                         service_source="subscription", subscription_backend="central", account_id="owned-user",
                         platform_url="http://127.0.0.1:1", invocation_key="owned-turn")
    async def run():
        async def ask(_):
            raise AssertionError("Owned fixture requires no approval")
        await asyncio.wait_for(CodexAdapter().run(request, lambda kind, text: output.append((kind, text)), ask, lambda _: None), 30)
    try:
        asyncio.run(run())
    except Exception:
        pytest.fail(f"Native fixture failed: received={len(observations)}, validated={len(upstream)}, codes={errors}", pytrace=False)
    assert errors == []
    assert len(upstream) == 2 and all(present for _, present in observations)
    assert observations[0][0] != observations[1][0]
    assert any(item.get("type") == "function_call_output" for item in upstream[1]["input"])
    assert any("OWNED_PROTOCOL_OK" in text for _, text in output)
    records = list((tmp_path / "outputs" / "_harness" / "central-execution" / "operations").glob("*.json"))
    assert len(records) == 2
    for path in records:
        assert "PRIVATE_OWNED_FIXTURE_TOKEN" not in path.read_text()
