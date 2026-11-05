"""Uncertain central turns cannot be dispatched again through ordinary chat."""

import asyncio

import pytest

import web.app as web
from easel.service_contracts import TaskContext
from easel.harnesses import store
from tests.test_agent_harnesses import sandbox  # noqa: F401


CENTRAL = {"service_source": "subscription", "subscription_backend": "central"}


def fake_execution(monkeypatch):
    accepted, launched = [], []

    async def accept(req, runtime, config, env=None):
        accepted.append(req)
        return TaskContext(runtime="codex", model="test-model")

    class Fake:
        async def run(self, request, emit, ask, bind):
            launched.append(request)
            emit("token", "safe-fixture-result")

    monkeypatch.setattr(web, "_accept_task_context", accept)
    monkeypatch.setattr(web, "_chat_backend", lambda session_id=None: "codex")
    monkeypatch.setattr(web.harnesses, "adapter", lambda runtime: Fake())
    return accepted, launched


@pytest.mark.parametrize("path", ["/api/chat/stream", "/api/chat"])
@pytest.mark.parametrize("extra", [
    {"clean_end": False, "stop_reason": "error"},
    {"clean_end": False, "stop_reason": "stopped"},
    {},
])
def test_uncertain_central_turn_rejected_before_authorize_or_launch(sandbox, monkeypatch, path, extra):
    web._save_turn("web:owned", "done", "original result", {"turn_id": "old", "route": CENTRAL, **extra})
    original = web._turn_file("web:owned").read_bytes()
    accepted, launched = fake_execution(monkeypatch)
    response = sandbox.post(path, json={"sessionId": "owned", "message": "你好", "turnId": "new"})
    assert response.status_code == 409
    assert "中断请求状态" in response.json()["detail"] and "新建对话" in response.json()["detail"]
    assert not accepted and not launched
    assert web._turn_file("web:owned").read_bytes() == original
    assert not web._job_event_file("new").exists()


@pytest.mark.parametrize("path", ["/api/chat/stream", "/api/chat"])
@pytest.mark.parametrize("previous", ["custom_error", "central_completed", "new_session"])
def test_custom_confirmed_and_new_sessions_remain_available(sandbox, monkeypatch, path, previous):
    if previous != "new_session":
        route = CENTRAL if previous == "central_completed" else {"service_source": "custom"}
        web._save_turn("web:allowed", "done", "old", {"route": route, "clean_end": previous == "central_completed"})
    accepted, launched = fake_execution(monkeypatch)
    response = sandbox.post(path, json={"sessionId": "allowed", "message": "new instruction", "turnId": "new"})
    assert response.status_code == 200
    assert len(accepted) == 1 and len(launched) == 1


def test_queued_request_rechecks_previous_turn_under_the_session_lock(sandbox, monkeypatch):
    accepted, launched = fake_execution(monkeypatch)

    async def exercise():
        lock = web._session_lock("queued")
        await lock.acquire()
        events = []
        task = asyncio.create_task(web._run_harness_chat(
            web.ChatRequest(sessionId="queued", message="another request"), "queued", "another request",
            "codex", {"sandbox": "read-only"}, lambda *event: events.append(event),
            context=TaskContext(runtime="codex", model="test-model")))
        await asyncio.sleep(0)
        web._save_turn("web:queued", "done", "original unknown", {"route": CENTRAL, "clean_end": False})
        before = web._turn_file("web:queued").read_bytes()
        lock.release()
        await task
        assert not launched and not accepted
        assert web._turn_file("web:queued").read_bytes() == before
        assert any("中断请求状态" in str(event) for event in events)

    asyncio.run(exercise())


@pytest.mark.parametrize("content", ['null', '[]', '{"status":"done","clean_end":false}'])
def test_unknown_snapshot_of_bound_central_session_never_authorizes(sandbox, monkeypatch, content):
    store.bind(web.SESSIONS_DIR, "bound", "codex", "known-native", service_source="subscription",
               account_id="owned-user", subscription_backend="central", platform_url="https://central.test", central_scope={})
    path = web._turn_file("web:bound")
    path.write_text(content)
    accepted, launched = fake_execution(monkeypatch)
    response = sandbox.post('/api/chat/stream', json={"sessionId": "bound", "message": "next"})
    assert response.status_code == 409 and "中断请求状态" in response.json()["detail"]
    assert not accepted and not launched and path.read_text() == content
