"""Synthetic raw-secret boundaries; never read the developer's account/profile."""

import json
from contextlib import asynccontextmanager

import pytest
import web.app as web
from easel.routes import outputs as output_routes  # noqa: E402
from easel.harnesses import codex
from easel.platform.session import SessionStore
from tests import test_agent_harnesses as harness_tests
from easel.routes import chat as chat_routes  # noqa: E402

# Reuse the actual Web fixture; it redirects every mutable path to tmp_path.
sandbox = harness_tests.sandbox


def session(root, suffix="first"):
    store = SessionStore(root)
    state = {
        "version": 1,
        "url": "http://127.0.0.1:8790",
        "auth": {
            "access_token": "synthetic-access-" + suffix,
            "refresh_token": "synthetic-refresh-" + suffix,
            "session_id": "synthetic-session",
            "account": {"id": "synthetic-owner"},
            "expires_at": 9999999999,
            "refresh_expires_at": 9999999999,
        },
    }
    with store.locked():
        store.write(state)
    return state


@pytest.mark.parametrize("route", ["output", "media"])
@pytest.mark.parametrize("extension", ["json", "txt", "html", "bin"])
def test_raw_copied_sessions_rejected_by_actual_output_routes(
    sandbox, route, extension
):
    state = session(web.PROJECT_ROOT)
    report = web.OUTPUTS_DIR / "report"
    report.mkdir()
    copy = report / ("copy." + extension)
    copy.write_text(json.dumps(state))
    response = sandbox.get(
        f"/api/{route}/report/{copy.name}", headers={"Range": "bytes=0-3"}
    )
    assert response.status_code == 403
    assert not any(
        token in response.text
        for token in state["auth"].values()
        if isinstance(token, str)
    )


def test_rotated_and_logged_out_raw_values_remain_protected(sandbox):
    old = session(web.PROJECT_ROOT)
    fresh = session(web.PROJECT_ROOT, "rotated")
    with SessionStore(web.PROJECT_ROOT).locked():
        SessionStore(web.PROJECT_ROOT).write({"version": 1, "url": old["url"]})
    report = web.OUTPUTS_DIR / "report"
    report.mkdir()
    for index, state in enumerate((old, fresh)):
        (report / f"raw-{index}.txt").write_text(
            "ordinary diagnostic " + state["auth"]["refresh_token"]
        )
        assert sandbox.get(f"/api/media/report/raw-{index}.txt").status_code == 403
        assert sandbox.get(f"/api/output/report/raw-{index}.txt").status_code == 403


def test_private_alias_still_rejected_and_clean_custom_range_works(sandbox):
    session(web.PROJECT_ROOT)
    report = web.OUTPUTS_DIR / "report"
    report.mkdir()
    (report / "alias.json").symlink_to(SessionStore(web.PROJECT_ROOT).path)
    assert sandbox.get("/api/media/report/alias.json").status_code == 403
    clean = report / "safe.mp4"
    clean.write_bytes(b"0123456789")
    response = sandbox.get("/api/media/report/safe.mp4", headers={"Range": "bytes=2-5"})
    assert response.status_code == 206 and response.content == b"2345"
    assert response.headers["content-range"] == "bytes 2-5/10"


def test_project_metadata_does_not_leak_raw_copy(sandbox):
    state = session(web.PROJECT_ROOT)
    report = web.OUTPUTS_DIR / "report"
    report.mkdir()
    (report / ".easel.json").write_text(
        json.dumps({"title": state["auth"]["access_token"]})
    )
    assert state["auth"]["access_token"] not in sandbox.get("/api/outputs").text


def test_actual_adapter_raw_cat_split_rotation_protects_sse_persisted_text_and_copy(
    sandbox, monkeypatch
):
    initial = session(web.PROJECT_ROOT)
    auth = initial["auth"]
    output_dir = web.OUTPUTS_DIR / "report"
    output_dir.mkdir()
    observed = []

    class RPC:
        initialized = {}
        native_id = ""

        async def call(self, method, params, **kwargs):
            if method == "thread/start":
                return {"thread": {"id": "privacy-thread"}}
            if method == "turn/start":
                # Simulate ordinary native cat, followed by refresh and a raw copy.
                raw = SessionStore(web.PROJECT_ROOT).path.read_text()
                fresh = session(web.PROJECT_ROOT, "rotated-in-run")
                (output_dir / "copied.txt").write_text(fresh["auth"]["access_token"])
                for value in (
                    raw,
                    fresh["auth"]["access_token"],
                    fresh["auth"]["refresh_token"],
                ):
                    for part in (value[:11], value[11:23], value[23:]):
                        self.notify(
                            "item/agentMessage/delta",
                            {
                                "threadId": "privacy-thread",
                                "turnId": "privacy-turn",
                                "delta": part,
                            },
                        )
                self.notify(
                    "turn/completed",
                    {
                        "threadId": "privacy-thread",
                        "turnId": "privacy-turn",
                        "turn": {"id": "privacy-turn", "status": "completed"},
                    },
                )
                return {"turn": {"id": "privacy-turn"}}

        async def wait_notification(self, completion):
            await completion

    @asynccontextmanager
    async def connect(request, command, notify, respond):
        observed.append(request)
        rpc = RPC()
        rpc.notify = notify
        yield rpc

    monkeypatch.setattr(codex, "connection", connect)
    monkeypatch.setattr(web.harnesses, "adapter", lambda runtime: codex.CodexAdapter())
    monkeypatch.setattr(
        codex, "prepare_codex", lambda request, command: (request, command)
    )
    response = sandbox.post(
        "/api/chat/stream",
        json={
            "message": "synthetic cat",
            "sessionId": "privacy",
            "turnId": "privacy-job",
        },
    )
    assert response.status_code == 200
    public = (
        response.text
        + sandbox.get("/api/chat/last/privacy").text
        + sandbox.get("/api/chat/jobs/privacy-job/stream").text
    )
    stored = "".join(p.read_text() for p in web.SESSIONS_DIR.rglob("*") if p.is_file())
    for value in (
        auth["access_token"],
        auth["refresh_token"],
        "synthetic-access-rotated-in-run",
        "synthetic-refresh-rotated-in-run",
    ):
        assert value not in public and value not in stored
        assert all(value not in json.dumps(request.env) for request in observed)
    assert "[已隐藏]" in public
    assert sandbox.get("/api/media/report/copied.txt").status_code == 403


def test_public_zip_inspects_compressed_session_copy_but_preserves_clean_archive(
    sandbox,
):
    import zipfile

    state = session(web.PROJECT_ROOT)
    report = web.OUTPUTS_DIR / "report"
    report.mkdir()
    for name, value, expected in [
        ("diagnostic.zip", json.dumps(state), 403),
        ("safe.zip", "harmless report", 200),
    ]:
        with zipfile.ZipFile(
            report / name, "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            archive.writestr("account-copy.json", value)
        assert sandbox.get("/api/media/report/" + name).status_code == expected
    assert (
        sandbox.get("/api/artifacts/archive?path=report/diagnostic.zip").status_code
        == 403
    )


def test_raw_secret_in_public_filename_is_not_exposed_in_tree_or_archive(sandbox):
    state = session(web.PROJECT_ROOT)
    report = web.OUTPUTS_DIR / "report"
    report.mkdir()
    token = state["auth"]["access_token"]
    (report / (token + ".txt")).write_text("harmless bytes")
    assert token not in sandbox.get("/api/outputs").text
    assert sandbox.get("/api/media/report/" + token + ".txt").status_code == 403


@pytest.mark.asyncio
async def test_adapter_protocol_error_redacts_account_values(tmp_path, monkeypatch):
    from easel.harnesses.base import RunRequest, HarnessError

    state = session(tmp_path)

    @asynccontextmanager
    async def fail(*args, **kwargs):
        raise HarnessError("protocol " + state["auth"]["access_token"])
        yield

    monkeypatch.setattr(codex, "connection", fail)
    monkeypatch.setattr(
        codex, "prepare_codex", lambda request, command: (request, command)
    )
    with pytest.raises(HarnessError) as error:
        await codex.CodexAdapter().run(
            RunRequest("synthetic", tmp_path, {}, command=("synthetic",)),
            lambda *args: None,
            None,
            lambda value: None,
        )
    assert state["auth"]["access_token"] not in str(error.value)


@pytest.mark.asyncio
async def test_acp_raw_split_deltas_and_permission_question_are_scrubbed(
    tmp_path, monkeypatch
):
    from easel.harnesses import acp
    from easel.harnesses.base import RunRequest

    state = session(tmp_path)
    token = state["auth"]["refresh_token"]
    output, questions = [], []

    class RPC:
        initialized = {"protocolVersion": 1}
        native_id = ""

        async def call(self, method, params, **kwargs):
            if method == "session/new":
                return {"sessionId": "acp-session"}
            if method == "session/prompt":
                await self.respond(
                    "session/request_permission",
                    {
                        "sessionId": "acp-session",
                        "toolCall": {"title": token},
                        "options": [
                            {"kind": "allow_once", "name": "allow", "optionId": "once"}
                        ],
                    },
                )
                for part in (token[:10], token[10:]):
                    self.notify(
                        "session/update",
                        {
                            "sessionId": "acp-session",
                            "update": {
                                "sessionUpdate": "agent_message_chunk",
                                "content": {"type": "text", "text": part},
                            },
                        },
                    )
                return {"stopReason": "end_turn"}

    @asynccontextmanager
    async def connect(request, command, notify, respond, **kwargs):
        rpc = RPC()
        rpc.notify = notify
        rpc.respond = respond
        yield rpc

    async def ask(value):
        questions.extend(value)
        return {}

    monkeypatch.setattr(acp, "connection", connect)
    await acp.ACPAdapter().run(
        RunRequest("synthetic", tmp_path, {}, command=("synthetic",)),
        lambda kind, text: output.append(text),
        ask,
        lambda value: None,
    )
    assert token not in "".join(output) and token not in json.dumps(questions)
    assert "[已隐藏]" in "".join(output)


def test_scan_handles_chunk_boundary_and_retains_scoped_child_bearer(sandbox):
    from easel.platform.privacy import remember

    session(web.PROJECT_ROOT)
    remember(web.PROJECT_ROOT, ["synthetic-task-bearer-boundary"])
    report = web.OUTPUTS_DIR / "report"
    report.mkdir()
    (report / "split.bin").write_bytes(
        b"x" * (65536 - 9) + b"synthetic-task-bearer-boundary" + b"tail"
    )
    assert (
        sandbox.get(
            "/api/media/report/split.bin", headers={"Range": "bytes=0-4"}
        ).status_code
        == 403
    )


def test_scanned_snapshot_serves_same_bytes_after_source_changes_and_closes(
    sandbox, monkeypatch
):
    from easel.platform import privacy

    state = session(web.PROJECT_ROOT)
    report = web.OUTPUTS_DIR / "report"
    report.mkdir()
    target = report / "race.txt"
    clean = b"clean-" * 200000  # force private on-disk spool, keep memory bounded
    target.write_bytes(clean)
    original = privacy.safe_snapshot
    snapshots = []

    def replace_after_scan(path, root):
        snapshot = original(path, root)
        snapshots.append(snapshot)
        import os
        import stat

        assert stat.S_IMODE(os.fstat(snapshot.fileno()).st_mode) == 0o600
        target.write_text(state["auth"]["refresh_token"])
        return snapshot

    monkeypatch.setattr(privacy, "safe_snapshot", replace_after_scan)
    response = sandbox.get(
        "/api/media/report/race.txt", headers={"Range": "bytes=0-10"}
    )
    assert response.content == clean[:11]
    assert snapshots[0].closed
    assert sandbox.get("/api/media/report/race.txt").status_code == 403


def test_safe_snapshot_recognizes_ordinary_auth_json_without_registry(
    tmp_path, monkeypatch
):
    from easel.platform import privacy
    from fastapi import HTTPException

    outputs = tmp_path / "outputs"
    outputs.mkdir()
    target = outputs / "old-session.json"
    target.write_text(json.dumps({"access_token": "A" * 43, "refresh_token": "B" * 43}))
    monkeypatch.setattr(privacy, "sensitive_values", lambda root: ())
    with pytest.raises(HTTPException) as error:
        privacy.safe_snapshot(target, outputs)
    assert error.value.status_code == 403
    target.write_text('{"access_token": "[已隐藏]", "refresh_token": "example"}')
    with privacy.safe_snapshot(target, outputs) as snapshot:
        assert snapshot.read() == target.read_bytes()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_snapshot_closes_on_send_failure_or_cancellation(cancel):
    import asyncio
    import io
    from easel.platform.privacy import snapshot_response

    snapshot = io.BytesIO(b"safe bytes")
    response = snapshot_response(snapshot, "test.txt")

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        if cancel:
            raise asyncio.CancelledError
        raise RuntimeError("synthetic send failure")

    with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
    assert snapshot.closed


def test_sensitive_context_saturation_fails_closed_without_eviction(
    tmp_path, monkeypatch
):
    from easel.platform import privacy
    from easel.harnesses.base import HarnessError

    monkeypatch.setattr(privacy, "_MAX_VALUES", 2)
    privacy.remember(tmp_path, ["synthetic-old-access", "synthetic-old-refresh"])
    with pytest.raises(HarnessError):
        privacy.remember(tmp_path, ["synthetic-rotated"])
    with pytest.raises(HarnessError):
        privacy.sensitive_values(tmp_path)


def test_anonymous_custom_context_needs_no_account_http_or_fcntl(tmp_path, monkeypatch):
    from easel.platform import privacy, session as session_module
    from easel.platform.client import PlatformClient

    monkeypatch.setattr(session_module, "fcntl", None)
    monkeypatch.setattr(
        PlatformClient,
        "__init__",
        lambda *args, **kwargs: pytest.fail("unexpected platform client"),
    )
    assert privacy.sensitive_values(tmp_path) == ()
    assert not (tmp_path / "outputs").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["media", "output"])
async def test_cancel_during_scan_closes_worker_snapshot(tmp_path, monkeypatch, route):
    import asyncio
    import io
    import threading
    from starlette.requests import Request
    from easel.platform import privacy

    outputs = tmp_path / "outputs"
    outputs.mkdir()
    (outputs / "clean.txt").write_text("safe")
    monkeypatch.setattr(web, "OUTPUTS_DIR", outputs)
    entered, release, returned = threading.Event(), threading.Event(), threading.Event()
    snapshot = io.BytesIO(b"safe")

    def slow_scan(*args):
        entered.set()
        assert release.wait(3)
        returned.set()
        return snapshot

    monkeypatch.setattr(privacy, "safe_snapshot", slow_scan)
    task = asyncio.create_task(
        output_routes.api_media("clean.txt", Request({"type": "http", "headers": []}))
        if route == "media" else output_routes.api_output("clean.txt")
    )
    assert await asyncio.to_thread(entered.wait, 2)
    # The scanner remains blocked while actual stop/status handlers can progress.
    stopped = await asyncio.wait_for(chat_routes.api_chat_stop(chat_routes.StopRequest(sessionId="scan-status")), 0.5)
    assert stopped is not None
    last = await asyncio.wait_for(chat_routes.api_chat_last("scan-status"), 0.5)
    assert last["status"] == "none"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    assert await asyncio.to_thread(returned.wait, 2)
    for _ in range(20):
        if snapshot.closed:
            break
        await asyncio.sleep(0.01)
    assert snapshot.closed


def test_zip_inspection_bounds_decompression_and_rejects_nested_archives(
    tmp_path, monkeypatch
):
    import zipfile
    from fastapi import HTTPException
    from easel.platform import privacy

    outputs = tmp_path / "outputs"
    outputs.mkdir()
    for name, entries in [
        ("many.zip", [(f"{i}.txt", b"safe") for i in range(1001)]),
        ("nested.zip", [("inner.zip", b"PK\x03\x04synthetic nested bytes")]),
    ]:
        target = outputs / name
        with zipfile.ZipFile(target, "w") as archive:
            for member, value in entries:
                archive.writestr(member, value)
        with pytest.raises(HTTPException) as error:
            privacy.safe_snapshot(target, outputs)
        assert error.value.status_code == 403


def test_clean_multipart_and_invalid_ranges_close_snapshot(sandbox, monkeypatch):
    from easel.platform import privacy

    report = web.OUTPUTS_DIR / "report"
    report.mkdir(parents=True)
    (report / "safe.txt").write_text("0123456789")
    snapshots = []
    original = privacy.safe_snapshot

    def capture(*args):
        snapshot = original(*args)
        snapshots.append(snapshot)
        return snapshot

    monkeypatch.setattr(privacy, "safe_snapshot", capture)
    response = sandbox.get(
        "/api/media/report/safe.txt", headers={"Range": "bytes=0-1,8-9"}
    )
    assert response.status_code == 206
    assert b"bytes 0-1/10" in response.content and b"bytes 8-9/10" in response.content
    assert len(response.content) == int(response.headers["content-length"])
    assert snapshots[-1].closed
    response = sandbox.get(
        "/api/media/report/safe.txt", headers={"Range": "bytes=99-100"}
    )
    assert response.status_code == 416 and snapshots[-1].closed


def test_portable_plain_file_snapshot_and_range_remain_available(sandbox, monkeypatch):
    from easel.platform import privacy

    outputs = web.OUTPUTS_DIR
    outputs.mkdir(exist_ok=True)
    target = outputs / "plain.txt"
    target.write_bytes(b"plain custom output")
    monkeypatch.setattr(privacy, "_directory_open_supported", lambda: False)
    with privacy.safe_snapshot(target, outputs) as snapshot:
        assert snapshot.read() == b"plain custom output"
    response = sandbox.get("/api/media/plain.txt", headers={"Range": "bytes=0-4"})
    assert response.status_code == 206 and response.content == b"plain"


def test_portable_opener_rejects_links_and_reparse(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from easel.platform import privacy

    outputs = tmp_path / "outputs"
    outputs.mkdir()
    target = outputs / "plain.txt"
    target.write_text("safe")
    link = outputs / "link.txt"
    link.symlink_to(target)
    monkeypatch.setattr(privacy, "_directory_open_supported", lambda: False)
    with pytest.raises(HTTPException):
        privacy.safe_snapshot(link, outputs)
    original = privacy._portable_stat

    def reparse(path):
        if path == target:
            info = target.stat()
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return original(path)

    monkeypatch.setattr(privacy, "_portable_stat", reparse)
    with pytest.raises(HTTPException):
        privacy.safe_snapshot(target, outputs)


def test_portable_opener_rejects_replacement_during_open(tmp_path, monkeypatch):
    import os
    from fastapi import HTTPException
    from easel.platform import privacy

    outputs = tmp_path / "outputs"
    outputs.mkdir()
    target = outputs / "plain.txt"
    target.write_text("before")
    replacement = outputs / "replacement.txt"
    replacement.write_text("replacement")
    monkeypatch.setattr(privacy, "_directory_open_supported", lambda: False)
    original = os.open

    def changed(path, *args, **kwargs):
        if path == target:
            replacement.replace(target)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(privacy.os, "open", changed)
    with pytest.raises(HTTPException):
        privacy.safe_snapshot(target, outputs)


def test_private_snapshot_spool_cannot_use_public_output_temp_directory(
    tmp_path, monkeypatch
):
    from fastapi import HTTPException
    from easel.platform import privacy

    outputs = tmp_path / "outputs"
    outputs.mkdir()
    target = outputs / "safe.txt"
    target.write_text("clean")
    spool_directory = outputs / "public-temp"
    spool_directory.mkdir()
    monkeypatch.setattr(privacy.tempfile, "tempdir", str(spool_directory))
    with pytest.raises(HTTPException) as error:
        privacy.safe_snapshot(target, outputs)
    assert error.value.status_code == 403


@pytest.fixture
def openclaw_web(sandbox, monkeypatch):
    from easel.harnesses import store
    store.write_json(web.OUTPUTS_DIR / "_harness" / "config.json", {"runtime": "openclaw"})
    monkeypatch.setattr(chat_routes, "DEBUG_DIR", web.OUTPUTS_DIR / "_debug")
    monkeypatch.setattr(chat_routes, "SHARED_RAW_STREAM", web.PROJECT_ROOT / "raw.jsonl")
    monkeypatch.setattr(web, "_heal_openclaw_session", lambda *a: None)
    monkeypatch.setattr(web, "openclaw_base_cmd", lambda: ["synthetic-openclaw"])
    monkeypatch.setattr(chat_routes, "_resolve_transport", lambda *a: "cli")
    monkeypatch.setattr(web, "GatewayClient", None)
    return sandbox


@pytest.mark.parametrize("ending", ["complete", "stopped", "error"])
def test_openclaw_actual_web_split_stream_and_recovery(openclaw_web, monkeypatch, ending):
    import io
    state = session(web.PROJECT_ROOT)
    secret = state["auth"]["refresh_token"]
    seen = []

    class Process:
        def __init__(self, cmd, **kwargs):
            seen.append((cmd, kwargs["env"]))
            raw = []
            # Ordinary cat output and interleaved thinking, split at sensitive prefixes.
            for kind, value in [("text", SessionStore(web.PROJECT_ROOT).path.read_text()),
                                ("text", "normal-before " + secret + " normal-after"),
                                ("thinking", "thought " + secret + " clear")]:
                for part in (value[:18], value[18:25], value[25:]):
                    raw.append({"runId": "test-run", "event": "assistant_" + kind + "_stream",
                                "evtType": kind + "_delta", "delta": part})
            if ending == "complete":
                raw.append({"runId": "test-run", "event": "assistant_message_end"})
            elif ending == "stopped":
                web._STOPPED_CHAT.add("oc-private")
                raw.append({"runId": "test-run", "event": "assistant_text_stream",
                            "evtType": "text_delta", "delta": secret[:12]})
            chat_routes.SHARED_RAW_STREAM.write_text("".join(json.dumps(v) + "\n" for v in raw))
            self.stdout = io.StringIO("diagnostic " + secret + "\n")
        def poll(self):
            return 1 if ending == "error" else 0

    monkeypatch.setattr(web.subprocess, "Popen", Process)
    response = openclaw_web.post("/api/chat/stream", json={
        "message": "cat account diagnostic", "sessionId": "oc-private", "turnId": "oc-job"})
    assert response.status_code == 200
    public = response.text + openclaw_web.get("/api/chat/last/oc-private").text
    public += openclaw_web.get("/api/chat/jobs/oc-job/stream").text
    stored = "".join(p.read_text() for root in (web.SESSIONS_DIR, chat_routes.DEBUG_DIR)
                     for p in root.rglob("*") if p.is_file())
    for value in (secret, state["auth"]["access_token"]):
        assert value not in public and value not in stored
    assert "normal-before " in response.text and " normal-after" in response.text
    assert "thought " in response.text and " clear" in response.text
    assert "[已隐藏]" in public
    assert all(secret not in json.dumps(value) for value in seen)
    assert response.text.index("[已隐藏]") < response.text.index("event: done")
    last = openclaw_web.get("/api/chat/last/oc-private").json()
    assert "normal-before " in last["text"] and " normal-after" in last["text"]
    assert not web._RUNNING_CHAT


@pytest.mark.parametrize("mode", ["stdout", "stderr", "exception"])
def test_openclaw_sync_output_and_error_are_private(openclaw_web, monkeypatch, mode):
    from types import SimpleNamespace
    state = session(web.PROJECT_ROOT)
    secret = state["auth"]["access_token"]
    def run(cmd, **kwargs):
        assert secret not in json.dumps([cmd, kwargs["env"]])
        if mode == "exception":
            raise OSError("safe-prefix " + secret + " safe-suffix")
        return SimpleNamespace(returncode=int(mode == "stderr"),
                               stdout="safe-prefix " + secret + " safe-suffix" if mode == "stdout" else "",
                               stderr="safe-prefix " + secret + " safe-suffix")
    monkeypatch.setattr(web.subprocess, "run", run)
    response = openclaw_web.post("/api/chat", json={"message": "hello", "sessionId": "oc-sync"})
    public = response.text + openclaw_web.get("/api/chat/last/oc-sync").text
    assert secret not in public
    assert "safe-prefix" in public and "safe-suffix" in public
    assert "[已隐藏]" in public


@pytest.mark.parametrize("rc", [0, 1])
def test_openclaw_stdout_fallback_redacted_before_web_persistence(openclaw_web, monkeypatch, rc):
    import io
    secret = session(web.PROJECT_ROOT)["auth"]["refresh_token"]
    class Process:
        stdout = io.StringIO("ordinary fallback " + secret + " suffix\n")
        def poll(self):
            return rc
    monkeypatch.setattr(web.subprocess, "Popen", lambda *a, **k: Process())
    response = openclaw_web.post("/api/chat/stream", json={
        "message": "hello", "sessionId": "fallback", "turnId": "fallback-job"})
    public = response.text + openclaw_web.get("/api/chat/last/fallback").text
    public += openclaw_web.get("/api/chat/jobs/fallback-job/stream").text
    assert secret not in public
    assert "ordinary fallback" in public and "suffix" in public and "[已隐藏]" in public


@pytest.mark.asyncio
async def test_openclaw_cancel_flushes_pending_before_recovery(openclaw_web, monkeypatch):
    import io
    secret = session(web.PROJECT_ROOT)["auth"]["access_token"]
    class Process:
        stdout = io.StringIO("")
        stopped = False
        def __init__(self):
            chat_routes.SHARED_RAW_STREAM.write_text(json.dumps({
                "runId": "cancel-run", "event": "assistant_text_stream",
                "evtType": "text_delta", "delta": "safe cancellation " + secret[:12]}) + "\n")
        def poll(self):
            return 0 if self.stopped else None
        def terminate(self):
            self.stopped = True
        def wait(self, **kwargs):
            self.stopped = True
    monkeypatch.setattr(web.subprocess, "Popen", lambda *a, **k: Process())
    response = await chat_routes.api_chat_stream(web.ChatRequest(message="hello", sessionId="cancel", turnId="cancel-job"))
    events = []
    async for event in response.body_iterator:
        events.append(event)
        if event.get("event") == "token" and "safe cancellation" in event.get("data", ""):
            tasks = list(web._BG_TASKS)
            assert tasks
            tasks[0].cancel()
    last = await chat_routes.api_chat_last("cancel")
    public = json.dumps(events, ensure_ascii=False) + json.dumps(last, ensure_ascii=False)
    assert secret[:12] not in public and "[已隐藏]" in public
    assert last["text"] == "safe cancellation [已隐藏]"
    assert events[-1]["event"] == "done"
    assert not web._RUNNING_CHAT


@pytest.mark.parametrize("failure", [False, True])
def test_openclaw_http_stream_uses_same_private_sink(openclaw_web, monkeypatch, failure):
    import httpx
    secret = session(web.PROJECT_ROOT, "x" * 240)["auth"]["refresh_token"]
    original = httpx.AsyncClient
    async def provider(request):
        assert secret not in request.content.decode()
        if failure:
            return httpx.Response(503, text="safe error " + secret)
        lines = []
        for field in ("content", "reasoning_content"):
            for value in ("safe " + secret[:10], secret[10:] + " suffix"):
                lines.append("data: " + json.dumps({"choices": [{"delta": {field: value}}]}) + "\n\n")
        return httpx.Response(200, text="".join(lines) + "data: [DONE]\n\n")
    monkeypatch.setattr(chat_routes, "_resolve_transport", lambda *a: "http")
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(provider), **kwargs))
    response = openclaw_web.post("/api/chat/stream", json={
        "message": "hello", "sessionId": "http-private", "turnId": "http-job"})
    public = response.text + openclaw_web.get("/api/chat/last/http-private").text
    public += openclaw_web.get("/api/chat/jobs/http-job/stream").text
    assert secret not in public and "[已隐藏]" in public
    assert "safe" in public
    assert "event: done" in response.text
