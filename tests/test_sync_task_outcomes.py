import asyncio
import subprocess
import threading
from unittest.mock import Mock
import pytest
from web import app as web
from easel.service_contracts import TaskContext
from easel.routes import chat as chat_routes  # noqa: E402

@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(web, "TURNS_DIR", tmp_path / "turns", raising=False)
    monkeypatch.setattr(web, "_turn_file", lambda key: tmp_path / (key.replace(":", "_") + ".json"))
    monkeypatch.setattr(web, "_oc_state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(web, "_direct_api_enabled", lambda: False)
    monkeypatch.setattr(web, "_heal_openclaw_session", Mock())
    monkeypatch.setattr(web, "openclaw_base_cmd", lambda: ["never-real-engine"])
    monkeypatch.setattr(web, "_openclaw_session_id", lambda _: "native")
    monkeypatch.setattr(web, "_agent_thinking_level", lambda: "off")
    monkeypatch.setattr(web, "_proxy_env", lambda: {})
    monkeypatch.setattr(web, "_read_env", lambda: {"API_KEY": "sk-private-123456789012345678901234567890"})
    lock = Mock()
    lock.acquire.return_value = True
    monkeypatch.setattr(web, "_CrossProcLock", lambda _: lock)
    return lock

@pytest.mark.parametrize("case,reason", [("rc", "error"), ("stdout_rc", "error"), ("timeout", "timeout"), ("exception", "error"), ("lock", "session_lock_timeout")])
def test_real_executor_failure_is_structured_and_callback_false(isolated, monkeypatch, case, reason):
    process = Mock(return_value=SimpleResult(1, "partial" if case == "stdout_rc" else "", "Authorization: Bearer sk-private-123456789012345678901234567890"))
    if case == "timeout": process.side_effect = subprocess.TimeoutExpired("fake", 1)
    if case == "exception": process.side_effect = RuntimeError("private failure")
    if case == "lock": isolated.acquire.return_value = False
    monkeypatch.setattr(web.subprocess, "run", process)
    callback = Mock()
    async def exercise():
        result = await web._submit_sync_task("test", 1, TaskContext(runtime="openclaw"), session_id="failed", on_complete=callback)
        last = await chat_routes.api_chat_last("failed")
        assert last["clean_end"] is False
        assert last["stop_reason"] == reason
        assert isinstance(result, str)
        assert "sk-private" not in result
        callback.assert_called_once_with(False, result)
        if case == "lock": process.assert_not_called()
    asyncio.run(exercise())

class SimpleResult:
    def __init__(self, returncode, stdout, stderr):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_sync_ownership_survives_request_cancel_and_rejects_duplicate(isolated, monkeypatch):
    started, finish = threading.Event(), threading.Event()
    def worker(*args):
        started.set()
        assert finish.wait(3)
        return "finished"
    monkeypatch.setattr(web, "run_agent_sync", worker)
    context = TaskContext(runtime="openclaw")
    async def exercise():
        web._bind_task("owned", context)
        task = web._submit_sync_task("test", 1, context, session_id="owned")
        async def request(): return await asyncio.shield(task)
        waiter = asyncio.create_task(request())
        try:
            with pytest.raises(web.HTTPException): await chat_routes.api_delete_session("owned")
            with pytest.raises(web.HTTPException): web._submit_sync_task("duplicate", 1, context, session_id="owned")
            assert await asyncio.to_thread(started.wait, 1)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError): await waiter
            with pytest.raises(web.HTTPException): await chat_routes.api_delete_session("owned")
            assert (await chat_routes.api_chat_stop(chat_routes.StopRequest(sessionId="owned")))["stopped"] is False
        finally: finish.set()
        await task
        assert (await chat_routes.api_delete_session("owned"))["deleted"]
        await asyncio.sleep(0)
        assert not web._turn_file("web:owned").exists()
    asyncio.run(exercise())


def test_sync_submission_rejects_native_lock_before_metadata(isolated, monkeypatch):
    bind = Mock()
    monkeypatch.setattr(web, "_bind_task", bind)
    async def exercise():
        lock = web._session_lock("native-busy")
        await lock.acquire()
        try:
            with pytest.raises(web.HTTPException):
                web._submit_sync_task("test", 1, TaskContext(runtime="openclaw"), session_id="native-busy")
            bind.assert_not_called()
        finally: lock.release()
    asyncio.run(exercise())


def test_native_cannot_overtake_accepted_sync_task(isolated, monkeypatch):
    async def exercise():
        owner = TaskContext(runtime="openclaw")
        task = web._submit_sync_task("test", 1, owner, session_id="queued")
        try:
            with pytest.raises(web.HTTPException):
                await web._run_harness_chat(web.ChatRequest(message="test"), "queued", "test", "codex", {}, Mock(), context=TaskContext(runtime="codex"))
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
            await asyncio.sleep(0)
        assert "queued" not in web._SYNC_SESSIONS
    asyncio.run(exercise())


@pytest.mark.parametrize("mode,reason,clean", [("success", "completed", True), ("stopped", "stopped", False), ("timeout", "timeout", False), ("error", "error", False)])
def test_native_sync_outcome_preserves_inner_completion(isolated, monkeypatch, mode, reason, clean):
    config = {"runtime": "codex", "models": {}, "sandbox": "workspace-write", "codex_auto_approve": False}
    monkeypatch.setattr(web, "_harness_settings", lambda: config)
    monkeypatch.setattr(web, "read_settings", lambda *a: {})
    monkeypatch.setattr(web, "_HARNESS_LOOP", None)
    async def run(request, emit, ask, bind):
        emit("token", "partial")
        if mode == "stopped":
            web._STOPPED_CHAT.add("native")
            raise asyncio.CancelledError()
        if mode == "timeout": raise asyncio.TimeoutError()
        if mode == "error": raise web.HarnessError("mock failure")
    monkeypatch.setattr(web.harnesses, "adapter", lambda _: type("Adapter", (), {"run": staticmethod(run)})())
    callback = Mock()
    async def exercise():
        task = web._submit_sync_task("test", 1, TaskContext(runtime="codex"), session_id="native", on_complete=callback)
        if mode in ("timeout", "error"):
            with pytest.raises(web.HTTPException): await task
        else:
            assert await task == "partial"
        last = await chat_routes.api_chat_last("native")
        assert last["stop_reason"] == reason
        assert last["clean_end"] is clean
        assert callback.call_args.args[0] is clean
        assert not web._SYNC_SESSIONS
    asyncio.run(exercise())


@pytest.mark.parametrize("stdout,stderr,expected", [("success", "", "success"), ("", "", "（无输出）"), ("", "diagnostic", "（无输出）\n\ndiagnostic")])
def test_openclaw_public_success_strings_remain_compatible(isolated, monkeypatch, stdout, stderr, expected):
    monkeypatch.setattr(web.subprocess, "run", Mock(return_value=SimpleResult(0, stdout, stderr)))
    assert web.run_agent_sync("test", context=TaskContext(runtime="openclaw")) == expected
