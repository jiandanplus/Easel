"""A new central turn carries a replayable, credential-free recovery reference."""
import asyncio
import json
import time

import pytest

import web.app as web
from easel.harnesses.base import HarnessError, RunRequest
from easel.service_contracts import TaskContext
from tests.test_agent_harnesses import sandbox  # noqa: F401


def events(text):
    output = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        name = next((x[7:] for x in block.splitlines() if x.startswith("event: ")), None)
        values = [x[6:] for x in block.splitlines() if x.startswith("data: ")]
        if name and values:
            output.append((name, json.loads("\n".join(values))))
    return output


@pytest.mark.parametrize("source", ["custom", "central"])
@pytest.mark.parametrize("outcome", ["success", "error", "stopped"])
def test_accepted_reference_is_exact_replayable_and_survives_terminal_outcomes(sandbox, monkeypatch, source, outcome):
    launched = []
    central = source == "central"
    context = TaskContext(
        service_source="subscription" if central else "custom", runtime="pi", model="fixture",
        subscription_backend="central" if central else "legacy", account_id="PRIVATE_ACCOUNT",
        platform_url="https://central.test", central_scope={"user_id": "PRIVATE_SCOPE"})

    async def accept(*args):
        return context

    class Fake:
        async def run(self, request, emit, ask, bind):
            launched.append(request)
            if outcome == "error":
                raise HarnessError("fixture unavailable")
            if outcome == "stopped":
                raise asyncio.CancelledError
            emit("token", "fixture completed")

    monkeypatch.setattr(web, "_accept_task_context", accept)
    monkeypatch.setattr(web, "_chat_backend", lambda *_: "pi")
    monkeypatch.setattr(web.harnesses, "adapter", lambda *_: Fake())
    response = sandbox.post("/api/chat/stream", json={"message": "test", "sessionId": "owned", "turnId": "turn-123"})
    assert response.status_code == 200
    captured = [value for name, value in events(response.text) if name == "central_recovery"]
    assert captured == ([{"version": 1, "turn_id": "turn-123"}] if central else [])
    assert len(launched) == 1 and launched[0].turn_id == "turn-123"
    assert launched[0].session_key == "owned"
    assert not any("PRIVATE" in json.dumps(value) for value in captured)
    snapshot = json.loads(web._turn_file("web:owned").read_text())
    assert snapshot.get("central_recovery") == ({"version": 1, "turn_id": "turn-123"} if central else None)
    replay = sandbox.get("/api/chat/jobs/turn-123/stream")
    assert [value for name, value in events(replay.text) if name == "central_recovery"] == captured
    assert len(launched) == 1


@pytest.mark.parametrize("bad", ["../wrong", "含中文", "a" * 161])
def test_bad_central_turn_identifier_never_launches(sandbox, monkeypatch, bad):
    launched = []

    async def accept(*args):
        return TaskContext(service_source="subscription", runtime="pi", model="fixture", subscription_backend="central")

    class Fake:
        async def run(self, *args):
            launched.append(args)

    monkeypatch.setattr(web, "_accept_task_context", accept)
    monkeypatch.setattr(web, "_chat_backend", lambda *_: "pi")
    monkeypatch.setattr(web.harnesses, "adapter", lambda *_: Fake())
    response = sandbox.post("/api/chat/stream", json={"message": "test", "sessionId": "owned", "turnId": bad})
    assert response.status_code == 400 and not launched
    assert bad not in response.text


def test_old_non_web_request_has_no_turn_association(tmp_path):
    assert RunRequest("fixture", tmp_path, {}).turn_id == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime", ["pi", "codex"])
async def test_native_adapter_passes_exact_web_turn_to_owned_proxy(tmp_path, monkeypatch, runtime):
    from easel.harnesses.pi import PiAdapter
    from easel.harnesses.codex import CodexAdapter
    import easel.platform.central
    import easel.platform.execution_proxy
    import easel.harnesses.runtime
    captured = []
    scope = dict(user_id="user", tenant_id="tenant", billing_account_id="wallet", product_id="easel", session_id="session", authorization_epoch=0)

    class Client:
        def __init__(self, *_):
            pass
        def models(self):
            return {"models": [{"id": "fixture", "limits": {"max_output_tokens": 512, "max_input_tokens": 2048}}]}
        def task_credential(self, *args):
            return dict(task_token="fixture-private", invocation_id="invocation", model="fixture", scope=scope, funding_mode="live", expires_at=time.time() + 600)

    class Proxy:
        def __init__(self, *args, **kwargs):
            captured.append(kwargs)
            self.base_url, self.local_token = "http://127.0.0.1:12345", "local-private"
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass

    async def worker(*args, **kwargs):
        return None

    monkeypatch.setattr(easel.platform.central, "CentralClient", Client)
    monkeypatch.setattr(easel.platform.execution_proxy, "ExecutionProxy", Proxy)
    monkeypatch.setattr(easel.harnesses.runtime, "prepare_subscription", lambda request, command, **kwargs: (request, command))
    adapter = PiAdapter() if runtime == "pi" else CodexAdapter()
    monkeypatch.setattr(type(adapter), "_run", worker)
    request = RunRequest("fixture", tmp_path, {}, model="fixture", session_key="owned", turn_id="turn-123",
                         service_source="subscription", subscription_backend="central", account_id="user",
                         platform_url="https://central.test", central_scope=scope, invocation_key="invocation")
    await adapter.run(request, lambda *_: None, lambda *_: None, lambda *_: None)
    assert len(captured) == 1
    assert captured[0]["session_key"] == "owned" and captured[0]["turn_id"] == "turn-123"
