"""Durable acceptance and exact-turn reads never submit a second model job."""
import asyncio
import json

import pytest

import web.app as web
from easel.service_contracts import TaskContext
from tests.test_agent_harnesses import sandbox  # noqa: F401
from tests.test_conversation_recovery_bridge import events
from easel.routes import chat as chat_routes  # noqa: E402


def fake(monkeypatch):
    calls = []
    async def accept(*args):
        return TaskContext(runtime="codex", model="fixture")
    class Agent:
        async def run(self, request, emit, ask, bind):
            record = web._workbench_state().receipt(request.turn_id)
            assert record["session_id"] == request.session_key
            assert chat_routes._read_job_events(request.turn_id)[0]["event"] == "accepted"
            calls.append(request)
            emit("token", "complete fixture")
    monkeypatch.setattr(web, "_accept_task_context", accept)
    monkeypatch.setattr(web, "_chat_backend", lambda *_: "codex")
    monkeypatch.setattr(web.harnesses, "adapter", lambda *_: Agent())
    return calls


def test_accepted_is_durable_before_model_dispatch_and_replayable(sandbox, monkeypatch):
    calls = fake(monkeypatch)
    response = sandbox.post("/api/chat/stream", json={"sessionId": "owned", "turnId": "turn1", "message": "fixture"})
    assert response.status_code == 200
    accepted = [value for name, value in events(response.text) if name == "accepted"]
    assert len(accepted) == 1
    receipt = accepted[0]
    assert receipt["version"] == 1 and receipt["session_id"] == "owned" and receipt["turn_id"] == "turn1"
    assert len(receipt["request_hash"]) == 64 and len(receipt["receipt_id"]) == 64
    state = sandbox.get("/api/chat/turns/owned/turn1").json()
    assert state["accepted"] is True and state["state"] == "completed"
    assert state["receipt_id"] == receipt["receipt_id"] and state["text"] == "complete fixture"
    assert len(calls) == 1
    replay = sandbox.post("/api/chat/stream", json={"sessionId": "owned", "turnId": "turn1", "message": "fixture"})
    assert replay.status_code == 200 and len(calls) == 1
    assert [value for name, value in events(replay.text) if name == "accepted"] == accepted


def test_same_turn_different_content_or_session_is_rejected(sandbox, monkeypatch):
    calls = fake(monkeypatch)
    payload = {"sessionId": "owned", "turnId": "turn1", "message": "fixture"}
    assert sandbox.post("/api/chat/stream", json=payload).status_code == 200
    for patch in ({"message": "changed"}, {"sessionId": "foreign"}):
        assert sandbox.post("/api/chat/stream", json={**payload, **patch}).status_code == 409
    assert len(calls) == 1


def test_old_turn_can_be_read_after_newer_turn_without_guessing_last(sandbox, monkeypatch):
    calls = fake(monkeypatch)
    for identifier in ("first", "second"):
        assert sandbox.post("/api/chat/stream", json={"sessionId": "owned", "turnId": identifier, "message": identifier}).status_code == 200
    first = sandbox.get("/api/chat/turns/owned/first").json()
    assert first["state"] == "completed" and first["turn_id"] == "first"
    assert sandbox.get("/api/chat/turns/owned/absent").json() == {"version": 1, "session_id": "owned", "turn_id": "absent", "state": "unknown", "accepted": False}
    assert len(calls) == 2


def test_account_source_change_prevents_old_body_and_event_reads(sandbox, monkeypatch):
    calls = fake(monkeypatch)
    contract = web._workbench_contract
    assert sandbox.post("/api/chat/stream", json={"sessionId": "owned", "turnId": "turn1", "message": "fixture"}).status_code == 200
    monkeypatch.setattr(web, "_workbench_contract", lambda *args: {**contract(), "source_scope": "a" * 64})
    for path in ("/api/chat/turns/owned/turn1", "/api/chat/jobs/turn1/stream"):
        response = sandbox.get(path)
        assert response.status_code == 409 and "complete fixture" not in response.text
    assert len(calls) == 1


def test_receipt_write_failure_never_dispatches(sandbox, monkeypatch):
    from easel.workbench import WorkbenchState, WorkbenchError
    calls = fake(monkeypatch)
    monkeypatch.setattr(WorkbenchState, "claim", lambda *args, **kwargs: (_ for _ in ()).throw(WorkbenchError("storage unavailable")))
    response = sandbox.post("/api/chat/stream", json={"sessionId": "owned", "turnId": "turn1", "message": "fixture"})
    assert response.status_code == 503 and not calls


@pytest.mark.parametrize("identifier", ["../turn", "x" * 161, "中文"])
def test_invalid_identifiers_reject_before_dispatch(sandbox, monkeypatch, identifier):
    calls = fake(monkeypatch)
    response = sandbox.post("/api/chat/stream", json={"sessionId": "owned", "turnId": identifier, "message": "fixture"})
    assert response.status_code == 400 and not calls


def test_stop_does_not_claim_confirmation_while_supervisor_is_still_live(sandbox, monkeypatch):
    class Process:
        def poll(self): return None
        def terminate(self): pass
        def kill(self): pass
        def wait(self, timeout=0): return None
    process = Process()
    web._workbench_state().claim("owned", "turn1", web._workbench_contract()["source_scope"], "a" * 64)
    web._save_turn("web:owned", "running", "", {"turn_id": "turn1"})
    web._RUNNING_CHAT["owned"] = process
    import itertools
    times = itertools.count(0, 10)
    monkeypatch.setattr(web.time, "monotonic", lambda: next(times))
    result = asyncio.run(chat_routes.api_chat_stop(chat_routes.StopRequest(sessionId="owned", turnId="turn1")))
    assert result["stopped"] is False and result["state"] == "stopping"


@pytest.mark.asyncio
async def test_stop_before_supervisor_start_confirms_exact_queued_turn_and_closes_stream(sandbox, monkeypatch):
    calls = fake(monkeypatch)
    response = await chat_routes.api_chat_stream(web.ChatRequest(sessionId="queued", turnId="queued-turn", message="fixture"))
    result = await chat_routes.api_chat_stop(chat_routes.StopRequest(sessionId="queued", turnId="queued-turn"))
    assert result["stopped"] is True and result["state"] == "stopped"
    async def collect():
        return [item async for item in response.body_iterator]
    captured = await asyncio.wait_for(collect(), 0.5)
    assert any(item["event"] == "accepted" for item in captured) and not calls
    exact = await chat_routes.api_chat_turn("queued", "queued-turn")
    assert json.loads(exact.body)["state"] == "stopped"


@pytest.mark.asyncio
async def test_two_simultaneous_posts_with_same_id_only_dispatch_once(sandbox, monkeypatch):
    calls = fake(monkeypatch)
    original_accept = web._accept_task_context
    async def accept(*args):
        await asyncio.sleep(0.01)
        return await original_accept(*args)
    monkeypatch.setattr(web, "_accept_task_context", accept)
    request = web.ChatRequest(sessionId="owned", turnId="raced", message="fixture")
    first, second = await asyncio.gather(chat_routes.api_chat_stream(request), chat_routes.api_chat_stream(request))
    async def consume(response):
        return [item async for item in response.body_iterator]
    await asyncio.wait_for(asyncio.gather(consume(first), consume(second)), 1)
    assert len(calls) == 1


def test_missing_terminal_snapshot_is_unknown_after_process_exit(sandbox, monkeypatch):
    fake(monkeypatch)
    assert sandbox.post("/api/chat/stream", json={"sessionId": "owned", "turnId": "turn1", "message": "fixture"}).status_code == 200
    web._workbench_state()._path("turn1", "snapshots").unlink()
    state = sandbox.get("/api/chat/turns/owned/turn1").json()
    assert state["accepted"] is True and state["state"] == "unknown"


def test_query_does_not_find_foreign_session_or_private_scope(sandbox, monkeypatch):
    fake(monkeypatch)
    assert sandbox.post("/api/chat/stream", json={"sessionId": "owned", "turnId": "turn1", "message": "fixture"}).status_code == 200
    response = sandbox.get("/api/chat/turns/foreign/turn1")
    assert response.status_code == 409 and "complete fixture" not in response.text
    exact = sandbox.get("/api/chat/turns/owned/turn1").json()
    assert "route" not in exact and "source_scope" not in exact


def test_event_replay_rejects_nonregular_and_oversized_logs(sandbox, tmp_path):
    oversized = chat_routes._job_event_file("oversized")
    oversized.parent.mkdir(parents=True)
    with oversized.open("wb") as stream:
        stream.truncate(16 * 1024 * 1024 + 1)
    assert chat_routes._read_job_events("oversized") == []
    outside = tmp_path / "outside.jsonl"
    outside.write_text(json.dumps({"id": 1, "event": "token", "data": "private"}) + "\n")
    chat_routes._job_event_file("alias").symlink_to(outside)
    assert chat_routes._read_job_events("alias") == []


@pytest.mark.parametrize("outcome,expected", [("success", "completed"), ("error", "failed"), ("stop", "stopped")])
def test_done_projects_only_durable_execution_termination(sandbox, monkeypatch, outcome, expected):
    fake(monkeypatch)
    class Agent:
        async def run(self, request, emit, ask, bind):
            if outcome == "error":
                from easel.harnesses.base import HarnessError
                raise HarnessError("synthetic unavailable")
            if outcome == "stop":
                web._STOPPED_CHAT.add(request.session_key)
                raise asyncio.CancelledError
            emit("token", "fixture result")
    monkeypatch.setattr(web.harnesses, "adapter", lambda *_: Agent())
    response = sandbox.post("/api/chat/stream", json={"sessionId": "owned", "turnId": "turn1", "message": "fixture"})
    terminal = [data for kind, data in events(response.text) if kind == "done"][-1]
    assert terminal["version"] == 1 and terminal["turn_id"] == "turn1" and terminal["session_id"] == "owned"
    assert terminal["sessionKey"] == "owned" and terminal["state"] == expected
    assert terminal["clean_end"] is (outcome == "success")
    assert terminal["stop_reason"] == ("completed" if outcome == "success" else "stopped" if outcome == "stop" else "error")
    replay = sandbox.get("/api/chat/jobs/turn1/stream")
    assert [data for kind, data in events(replay.text) if kind == "done"][-1] == terminal


def test_done_does_not_manufacture_completion_if_durable_snapshot_fails(sandbox, monkeypatch):
    from easel.workbench import WorkbenchState
    fake(monkeypatch)
    monkeypatch.setattr(WorkbenchState, "save_snapshot", lambda *args: None)
    response = sandbox.post("/api/chat/stream", json={"sessionId": "owned", "turnId": "turn1", "message": "fixture"})
    terminal = [data for kind, data in events(response.text) if kind == "done"][-1]
    assert terminal["state"] == "unknown" and terminal["clean_end"] is False


def test_preacceptance_rejection_is_exact_and_creates_no_task_record(sandbox, monkeypatch):
    calls = fake(monkeypatch)
    async def reject(*args):
        raise web.HTTPException(409, "模型目录已过期")
    monkeypatch.setattr(web, "_accept_task_context", reject)
    payload = {"sessionId": "owned", "turnId": "turn1", "message": "保留的草稿"}
    response = sandbox.post("/api/chat/stream", json=payload)
    assert response.status_code == 409 and not calls
    rejection = response.json()["rejection"]
    expected = web._workbench_digest(web.ChatRequest(**payload).model_dump())
    assert rejection == {"version": 1, "session_id": "owned", "turn_id": "turn1", "request_hash": expected, "not_dispatched": True}
    decision = web._workbench_state().receipt("turn1")
    assert decision["decision"] == "rejected"
    assert not chat_routes._job_event_file("turn1").exists()
    assert not web._turn_file("web:owned").exists()


def test_rejection_never_declares_an_existing_or_claimed_id_safe_to_resubmit(sandbox, monkeypatch):
    fake(monkeypatch)
    payload = {"sessionId": "owned", "turnId": "turn1", "message": "original"}
    assert sandbox.post("/api/chat/stream", json=payload).status_code == 200
    different = sandbox.post("/api/chat/stream", json={**payload, "message": "different"})
    assert different.status_code == 409 and "rejection" not in different.json()
    from easel.workbench import WorkbenchState, WorkbenchError
    monkeypatch.setattr(WorkbenchState, "claim", lambda *args, **kwargs: (_ for _ in ()).throw(WorkbenchError("cannot prove dispatch", 409)))
    failed_claim = sandbox.post("/api/chat/stream", json={**payload, "turnId": "other"})
    assert failed_claim.status_code == 409 and "rejection" not in failed_claim.json()


def test_rejected_turn_is_a_permanent_fence_and_is_readable_exactly(sandbox, monkeypatch):
    calls = fake(monkeypatch)
    scope = web._workbench_contract()["source_scope"]
    payload = {"sessionId": "owned", "turnId": "turn1", "message": "fixture"}
    request_hash = web._workbench_digest(web.ChatRequest(**payload).model_dump())
    proof = web._workbench_state().reject("owned", "turn1", scope, request_hash)
    assert proof["not_dispatched"] is True
    response = sandbox.post("/api/chat/stream", json=payload)
    assert response.status_code == 409 and response.json()["rejection"] == proof and not calls
    state = sandbox.get("/api/chat/turns/owned/turn1").json()
    assert state["state"] == "rejected" and state["accepted"] is False
    assert state["not_dispatched"] is True and state["request_hash"] == request_hash
    changed = sandbox.post("/api/chat/stream", json={**payload, "message": "different"})
    assert changed.status_code == 409 and "rejection" not in changed.json()


@pytest.mark.asyncio
async def test_rejection_wins_during_preflight_and_fences_the_racing_accept(sandbox, monkeypatch):
    calls = fake(monkeypatch)
    entered, proceed = asyncio.Event(), asyncio.Event()
    original = web._accept_task_context
    async def accept(*args):
        entered.set()
        await proceed.wait()
        return await original(*args)
    monkeypatch.setattr(web, "_accept_task_context", accept)
    request = web.ChatRequest(sessionId="owned", turnId="raced", message="fixture")
    task = asyncio.create_task(chat_routes.api_chat_stream(request))
    await entered.wait()
    proof = web._workbench_state().reject("owned", "raced", web._workbench_contract()["source_scope"], web._workbench_digest(request.model_dump()))
    proceed.set()
    response = await task
    assert response.status_code == 409 and json.loads(response.body)["rejection"] == proof
    assert not calls and not chat_routes._job_event_file("raced").exists()


def test_atomic_accept_and_reject_share_one_immutable_decision(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from easel.workbench import WorkbenchState
    ledger = WorkbenchState(tmp_path / "sessions", tmp_path)
    scope = ledger.contract({}, "build")["source_scope"]
    for number in range(12):
        turn = f"race-{number}"
        with ThreadPoolExecutor(max_workers=2) as pool:
            accepting = pool.submit(ledger.claim, "owned", turn, scope, "a" * 64)
            rejecting = pool.submit(ledger.reject, "owned", turn, scope, "a" * 64)
            accepted, created = accepting.result()
            proof = rejecting.result()
        final = ledger.receipt(turn)
        if proof is not None:
            assert final["decision"] == "rejected" and accepted["decision"] == "rejected" and created is False
        else:
            assert final["decision"] == "accepted" and accepted["decision"] == "accepted" and created is True


def test_explicit_withdraw_resolves_unknown_to_a_permanent_exact_rejection(sandbox, monkeypatch):
    calls = fake(monkeypatch)
    payload = {"sessionId": "owned", "turnId": "not-received", "message": "fixture"}
    body_hash = web._workbench_digest(web.ChatRequest(**payload).model_dump())
    assert sandbox.get("/api/chat/turns/owned/not-received").json()["state"] == "unknown"
    withdrawn = sandbox.post("/api/chat/turns/owned/not-received/withdraw", json={"request_hash": body_hash})
    assert withdrawn.status_code == 200
    value = withdrawn.json()
    assert value["state"] == "rejected" and value["accepted"] is False and value["not_dispatched"] is True
    assert value["request_hash"] == body_hash
    repeated = sandbox.post("/api/chat/turns/owned/not-received/withdraw", json={"request_hash": body_hash})
    assert repeated.json() == value
    later = sandbox.post("/api/chat/stream", json=payload)
    assert later.status_code == 409 and later.json()["rejection"]["not_dispatched"] is True and not calls


def test_withdraw_never_rewrites_accepted_turn_or_another_content(sandbox, monkeypatch):
    calls = fake(monkeypatch)
    payload = {"sessionId": "owned", "turnId": "turn1", "message": "fixture"}
    body_hash = web._workbench_digest(web.ChatRequest(**payload).model_dump())
    assert sandbox.post("/api/chat/stream", json=payload).status_code == 200
    original = web._workbench_state()._path("turn1").read_bytes()
    state = sandbox.post("/api/chat/turns/owned/turn1/withdraw", json={"request_hash": body_hash}).json()
    assert state["accepted"] is True and state["state"] == "completed" and "not_dispatched" not in state
    assert web._workbench_state()._path("turn1").read_bytes() == original and len(calls) == 1
    for path, digest in (("owned/turn1", "b" * 64), ("foreign/turn1", body_hash)):
        assert sandbox.post(f"/api/chat/turns/{path}/withdraw", json={"request_hash": digest}).status_code == 409


def test_withdraw_requires_same_origin_and_cannot_hide_old_unversioned_log(sandbox):
    endpoint = "/api/chat/turns/owned/turn1/withdraw"
    wrong_origin = sandbox.post(endpoint, json={"request_hash": "a" * 64}, headers={"Origin": "http://localhost:7860"})
    assert wrong_origin.status_code == 403
    event = chat_routes._job_event_file("turn1")
    event.parent.mkdir(parents=True, exist_ok=True)
    event.write_text(json.dumps({"id": 1, "event": "token", "data": "old"}) + "\n")
    assert sandbox.post(endpoint, json={"request_hash": "a" * 64}).status_code == 409
    assert web._workbench_state().receipt("turn1") is None


@pytest.mark.asyncio
async def test_http_withdraw_racing_preflight_prevents_dispatch(sandbox, monkeypatch):
    calls = fake(monkeypatch)
    entered, proceed = asyncio.Event(), asyncio.Event()
    original = web._accept_task_context
    async def accept(*args):
        entered.set()
        await proceed.wait()
        return await original(*args)
    monkeypatch.setattr(web, "_accept_task_context", accept)
    request = web.ChatRequest(sessionId="owned", turnId="withdraw-race", message="fixture")
    task = asyncio.create_task(chat_routes.api_chat_stream(request))
    await entered.wait()
    body_hash = web._workbench_digest(request.model_dump())
    response = sandbox.post("/api/chat/turns/owned/withdraw-race/withdraw", json={"request_hash": body_hash})
    assert response.status_code == 200 and response.json()["state"] == "rejected"
    proceed.set()
    original_response = await task
    assert original_response.status_code == 409 and not calls


def test_withdraw_rechecks_source_and_real_loopback_peer(sandbox, monkeypatch):
    from fastapi.testclient import TestClient
    proof = sandbox.post("/api/chat/turns/owned/turn1/withdraw", json={"request_hash": "a" * 64})
    assert proof.status_code == 200
    original_contract = web._workbench_contract
    monkeypatch.setattr(web, "_workbench_contract", lambda *args: {**original_contract(), "source_scope": "b" * 64})
    assert sandbox.post("/api/chat/turns/owned/turn1/withdraw", json={"request_hash": "a" * 64}).status_code == 409
    with TestClient(web.app, base_url="http://127.0.0.1:7860", headers={"Origin": "http://127.0.0.1:7860"}, client=("198.51.100.1", 50000)) as remote:
        response = remote.post("/api/chat/turns/owned/remote/withdraw", json={"request_hash": "a" * 64})
        assert response.status_code == 403
    assert web._workbench_state().receipt("remote") is None
