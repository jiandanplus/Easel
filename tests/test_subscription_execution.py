import pytest
from easel.harnesses.base import HarnessError
from easel.service_contracts import task_context


def test_subscription_rejects_other_runtime_before_dispatch(tmp_path):
    config = dict(
        service_source="subscription",
        execution_target="local",
        runtime="openclaw",
        models={},
    )
    with pytest.raises(HarnessError):
        task_context(config, root=tmp_path)


def test_custom_anonymous_never_platform(tmp_path, monkeypatch):
    from easel.platform.client import PlatformClient

    monkeypatch.setattr(
        PlatformClient,
        "authorize",
        lambda *a, **k: pytest.fail("custom called platform"),
    )
    ctx = task_context(
        dict(
            service_source="custom",
            execution_target="local",
            runtime="codex",
            models={"codex": "custom-model"},
        ),
        root=tmp_path,
    )
    assert ctx.model == "custom-model" and ctx.account_id == ""


def test_subscription_native_uses_scoped_env_private_home(tmp_path, monkeypatch):
    from easel.harnesses.runtime import prepare_codex
    from easel.harnesses.base import RunRequest
    from easel.platform.client import PlatformClient

    monkeypatch.setattr(
        PlatformClient, "task_credential", lambda self, a, m, u: "task-SECRET"
    )
    request = RunRequest(
        "hello",
        tmp_path,
        {"OPENAI_API_KEY": "custom-SECRET", "CODEX_HOME": "/wrong"},
        model="alias",
        service_source="subscription",
        account_id="owner",
        platform_url="http://localhost:8790",
    )
    prepared, argv = prepare_codex(request, ["codex", "app-server"])
    assert prepared.env["EASEL_SUBSCRIPTION_API_KEY"] == "task-SECRET"
    assert "OPENAI_API_KEY" not in prepared.env
    assert prepared.env["CODEX_HOME"].startswith(str(tmp_path / "outputs/_harness"))
    assert "SECRET" not in " ".join(argv)
    assert any("responses" in x for x in argv)
    assert any("http://localhost:8790/v1" in x for x in argv)


def test_subscription_binding_account_preserved(tmp_path):
    from easel.harnesses.store import bind, binding

    bind(
        tmp_path,
        "key",
        "codex",
        "native",
        service_source="subscription",
        account_id="owner",
    )
    with pytest.raises(HarnessError):
        bind(
            tmp_path, "key", "codex", service_source="subscription", account_id="other"
        )
    assert binding(tmp_path, "key")["account_id"] == "owner"


@pytest.mark.parametrize("which", ["chat", "skill", "ping"])
def test_actual_cli_propagates_accepted_identity(tmp_path, monkeypatch, which):
    from types import SimpleNamespace
    from easel import cli, harnesses
    from easel.commands import skill, ping
    from easel.platform.client import PlatformClient

    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(skill, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("easel.paths.workspace_root", lambda: tmp_path)
    config = dict(
        service_source="subscription",
        execution_target="local",
        runtime="codex",
        models={"codex": "custom"},
        subscription_model="alias",
        sandbox="read-only",
        codex_auto_approve=False,
    )
    monkeypatch.setattr(harnesses, "settings", lambda *a: config)
    monkeypatch.setattr(
        PlatformClient,
        "authorize",
        lambda self, m: {
            "account_id": "owner",
            "platform_url": "http://localhost:8790",
        },
    )
    for module in (cli, skill, ping):
        monkeypatch.setattr(module, "read_settings", lambda: {})
    monkeypatch.setattr(cli, "_list_personas", lambda: [])
    monkeypatch.setattr(skill, "_find_skill", lambda name: "synthetic")
    monkeypatch.setattr(skill, "_resolve_input", lambda value: "synthetic prompt")
    monkeypatch.setattr(cli, "_proxy_env", lambda: {})
    inputs = iter(["hello", "/exit"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    seen = []

    async def console(*args, **kwargs):
        context = kwargs["context"]
        seen.append(context)
        assert context.model == "alias" and context.account_id == "owner"
        return "ok", "native"

    monkeypatch.setattr(harnesses, "console_turn", console)
    command = {"chat": cli.cmd_chat, "skill": skill.cmd_skill, "ping": ping.cmd_ping}[
        which
    ]
    assert command(SimpleNamespace(name="synthetic", input="x", profile=None)) == 0
    assert seen


@pytest.mark.asyncio
async def test_console_uses_context_without_reauthorization(tmp_path, monkeypatch):
    from easel import harnesses
    from easel.service_contracts import TaskContext
    from easel.platform.client import PlatformClient

    config = dict(
        runtime="codex",
        service_source="subscription",
        execution_target="local",
        models={"codex": "wrong"},
        sandbox="read-only",
        codex_auto_approve=False,
    )
    monkeypatch.setattr(
        PlatformClient,
        "authorize",
        lambda *a: pytest.fail("accepted context was re-resolved"),
    )
    seen = []

    class Adapter:
        async def run(self, request, emit, ask, bind):
            seen.append(request)

    monkeypatch.setattr(harnesses, "adapter", lambda runtime: Adapter())
    await harnesses.console_turn(
        "synthetic",
        tmp_path,
        {},
        config=config,
        context=TaskContext(
            "subscription", "local", "codex", "alias", "owner", "http://localhost:8790"
        ),
    )
    assert seen[0].account_id == "owner" and seen[0].model == "alias"


@pytest.mark.asyncio
async def test_web_actual_preflight_rejects_before_attachments(tmp_path, monkeypatch):
    import web.app as web
    from fastapi import HTTPException

    monkeypatch.setattr(web, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(web, "_chat_backend", lambda sid: "codex")
    monkeypatch.setattr(
        web,
        "_harness_settings",
        lambda: dict(
            runtime="codex",
            service_source="subscription",
            execution_target="local",
            models={},
            subscription_model="alias",
        ),
    )
    monkeypatch.setattr(web, "read_settings", lambda *a: {})
    monkeypatch.setattr(
        web,
        "_chat_message",
        lambda *a: pytest.fail("read attachment/profile before auth"),
    )
    with pytest.raises(HTTPException):
        await web.api_chat(web.ChatRequest(message="synthetic"))


@pytest.mark.asyncio
async def test_web_native_request_carries_frozen_identity(tmp_path, monkeypatch):
    import web.app as web
    from easel.service_contracts import TaskContext
    from easel.harnesses import store

    monkeypatch.setattr(web, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(web, "SESSIONS_DIR", tmp_path / "outputs/_sessions")
    monkeypatch.setattr(web, "read_settings", lambda *a: {})
    monkeypatch.setattr(web, "_proxy_env", lambda: {})
    monkeypatch.setattr(web, "_save_turn", lambda *a: None)
    seen = []

    class Adapter:
        async def run(self, request, emit, ask, bind):
            seen.append(request)
            bind("native")
            emit("token", "ok")

    monkeypatch.setattr(web.harnesses, "adapter", lambda runtime: Adapter())
    config = dict(sandbox="read-only", codex_auto_approve=False)
    ctx = TaskContext(
        "subscription", "local", "codex", "alias", "owner", "http://localhost:8790"
    )
    await web._run_harness_chat(
        web.ChatRequest(message="synthetic"),
        "synthetic-task",
        "synthetic",
        "codex",
        config,
        lambda *a: None,
        context=ctx,
    )
    assert seen[0].account_id == "owner" and seen[0].model == "alias"
    assert store.binding(web.SESSIONS_DIR, "synthetic-task")["account_id"] == "owner"


def test_stream_redactor_catches_chunked_task_bearer():
    from easel.harnesses.secrets import StreamRedactor

    output = []
    redact = StreamRedactor(
        ["task-SECRET"], lambda kind, text: output.append((kind, text))
    )
    redact.emit("token", "before task-")
    redact.emit("token", "SEC")
    redact.emit("token", "RET after")
    redact.emit("thinking", "task-SE")
    redact.flush()
    combined = "".join(text for _, text in output)
    assert "task-" not in combined and "SECRET" not in combined
    assert "before [已隐藏] after" in combined


@pytest.mark.asyncio
async def test_web_blocks_login_while_preflight_and_acceptance_while_login(
    tmp_path, monkeypatch
):
    import asyncio
    import threading
    import web.app as web
    from fastapi import HTTPException
    from easel.platform.client import PlatformClient

    monkeypatch.setattr(web, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(web, "SESSIONS_DIR", tmp_path / "outputs/_sessions")
    entered = threading.Event()
    release = threading.Event()

    def authorize(self, model):
        entered.set()
        assert release.wait(3)
        return {"account_id": "owner", "platform_url": "http://localhost:8790"}

    monkeypatch.setattr(PlatformClient, "authorize", authorize)
    config = dict(
        runtime="codex",
        service_source="subscription",
        execution_target="local",
        models={},
        subscription_model="alias",
    )
    task = asyncio.create_task(
        web._accept_task_context(web.ChatRequest(message="synthetic"), "codex", config)
    )
    await asyncio.to_thread(entered.wait, 2)
    with pytest.raises(HarnessError):
        web._platform_change_guard()
    release.set()
    ctx = await task
    assert ctx.account_id == "owner"
    web._platform_router.platform_changes += 1
    try:
        with pytest.raises(HTTPException):
            await web._accept_task_context(
                web.ChatRequest(message="synthetic"), "codex", config
            )
    finally:
        web._platform_router.platform_changes -= 1


@pytest.mark.asyncio
async def test_subscription_model_save_preserves_custom_selection(
    tmp_path, monkeypatch
):
    import web.app as web
    from easel.harnesses import store

    monkeypatch.setattr(web, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(web, "OUTPUTS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(web, "read_settings", lambda *a: {})
    monkeypatch.setattr(web.harnesses, "describe", lambda runtime: {"installed": True})
    config = dict(
        runtime="codex",
        service_source="custom",
        execution_target="local",
        models={"codex": "custom-original"},
        sandbox="read-only",
        codex_auto_approve=False,
    )
    store.write_json(tmp_path / "outputs/_harness/config.json", config)
    # Bundled settings can display a resolved model distinct from persisted selection.
    monkeypatch.setattr(
        web,
        "_harness_settings",
        lambda: {**config, "models": {"codex": "custom-displayed"}},
    )
    await web.api_harness_settings_save(
        web.HarnessSettingsRequest(
            runtime="codex", service_source="subscription", model="alias"
        )
    )
    saved = store.read_json(tmp_path / "outputs/_harness/config.json")
    assert (
        saved["models"]["codex"] == "custom-original"
        and saved["subscription_model"] == "alias"
    )

    # Omitted model must survive source-only saves in both directions.
    for source in ("custom", "subscription", "custom"):
        await web.api_harness_settings_save(
            web.HarnessSettingsRequest(runtime="codex", service_source=source)
        )
        saved = store.read_json(tmp_path / "outputs/_harness/config.json")
        assert saved["models"]["codex"] == "custom-original"
        assert saved["subscription_model"] == "alias"
    await web.api_harness_settings_save(
        web.HarnessSettingsRequest(runtime="codex", service_source="custom", model="")
    )
    assert (
        store.read_json(tmp_path / "outputs/_harness/config.json")["models"]["codex"]
        == ""
    )


@pytest.mark.asyncio
async def test_actual_codex_adapter_scrubs_chunked_tokens_errors_and_questions(
    tmp_path, monkeypatch
):
    from contextlib import asynccontextmanager
    from easel.harnesses import codex
    from easel.harnesses.base import RunRequest
    from easel.platform.client import PlatformClient

    monkeypatch.setattr(PlatformClient, "task_credential", lambda *a: "task-SECRET")
    output = []
    questions = []

    class RPC:
        initialized = {}
        native_id = ""

        async def call(self, method, params, **kwargs):
            if method == "thread/start":
                return {"thread": {"id": "thread"}}
            if method == "turn/start":
                for text in ("before task-", "SEC", "RET after"):
                    self.notify(
                        "item/agentMessage/delta",
                        {"threadId": "thread", "turnId": "turn", "delta": text},
                    )
                self.notify(
                    "item/reasoning/textDelta",
                    {"threadId": "thread", "turnId": "turn", "delta": "task-SECRET"},
                )
                self.notify(
                    "turn/completed",
                    {
                        "threadId": "thread",
                        "turnId": "turn",
                        "turn": {
                            "id": "turn",
                            "status": "failed",
                            "error": {"message": "task-SECRET"},
                        },
                    },
                )
                return {"turn": {"id": "turn"}}

        async def wait_notification(self, completion):
            await completion

    @asynccontextmanager
    async def connect(request, command, notify, respond):
        rpc = RPC()
        rpc.notify = notify
        await respond(
            "item/tool/requestUserInput",
            {
                "threadId": "",
                "questions": [{"id": "x", "question": "echo task-SECRET"}],
            },
        )
        yield rpc

    monkeypatch.setattr(codex, "connection", connect)

    async def ask(items):
        questions.extend(items)
        return {}

    request = RunRequest(
        "synthetic",
        tmp_path,
        {},
        model="alias",
        command=("synthetic",),
        service_source="subscription",
        account_id="owner",
        platform_url="http://localhost:8790",
    )
    with pytest.raises(HarnessError) as exc:
        await codex.CodexAdapter().run(
            request,
            lambda kind, text: output.append((kind, text)),
            ask,
            lambda value: None,
        )
    assert "task-SECRET" not in str(exc.value)
    assert "SECRET" not in str(output) + str(questions)
    assert "before [已隐藏] after" in "".join(text for _, text in output)


@pytest.mark.asyncio
@pytest.mark.parametrize("source_override", [False, True])
@pytest.mark.parametrize(
    "peer", ["198.51.100.23", "127.0.0.1", "::1", "::ffff:127.0.0.1"]
)
async def test_http_subscription_requires_loopback(
    tmp_path, monkeypatch, source_override, peer
):
    import httpx
    import web.app as web
    from easel.platform.client import PlatformClient

    config = dict(
        runtime="codex",
        service_source="custom" if source_override else "subscription",
        execution_target="local",
        models={},
        subscription_model="alias",
    )
    monkeypatch.setattr(web, "_harness_settings", lambda: config)
    monkeypatch.setattr(web, "_chat_backend", lambda *a: "codex")
    monkeypatch.setattr(web, "read_settings", lambda *a: {})
    calls = []

    def authorize(self, model):
        calls.append("authorize")
        return {
            "account_id": "synthetic-owner",
            "platform_url": "http://localhost:8790",
        }

    async def dispatch(*a, **k):
        assert k["context"].account_id == "synthetic-owner"
        calls.append("dispatch")
        return "synthetic response"

    monkeypatch.setattr(PlatformClient, "authorize", authorize)
    monkeypatch.setattr(web, "_run_harness_chat", dispatch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=web.app, client=(peer, 1234)),
        base_url="http://localhost:7860",
    ) as client:
        body = (
            {"message": "synthetic", "service_source": "subscription"}
            if source_override
            else {"message": "synthetic"}
        )
        response = await client.post(
            "/api/chat",
            json=body,
            headers={"Origin": "http://localhost:7860", "X-Forwarded-For": "127.0.0.1"},
        )
    if peer == "198.51.100.23":
        assert response.status_code == 403, response.text
        assert calls == []
    else:
        assert response.status_code == 200, response.text
        assert calls == ["authorize", "dispatch"]


@pytest.mark.asyncio
async def test_custom_remote_http_preserves_existing_access(monkeypatch):
    import httpx
    import web.app as web
    from easel.platform.client import PlatformClient

    config = dict(
        runtime="codex",
        service_source="custom",
        execution_target="local",
        models={"codex": "custom"},
    )
    monkeypatch.setattr(web, "_harness_settings", lambda: config)
    monkeypatch.setattr(web, "_chat_backend", lambda *a: "codex")
    monkeypatch.setattr(web, "read_settings", lambda *a: {})
    monkeypatch.setattr(
        PlatformClient, "authorize", lambda *a: pytest.fail("custom authorization")
    )

    async def dispatch(*a, **k):
        assert k["context"].service_source == "custom"
        return "synthetic custom response"

    monkeypatch.setattr(web, "_run_harness_chat", dispatch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=web.app, client=("198.51.100.23", 1234)),
        base_url="http://localhost:7860",
    ) as client:
        response = await client.post(
            "/api/chat",
            json={"message": "synthetic"},
            headers={"Origin": "http://localhost:7860"},
        )
    assert response.status_code == 200, response.text


def test_owned_servers_disable_forwarded_peer_rewriting():
    import ast
    from pathlib import Path

    for filename, target in (("web/app.py", "run"), ("easel/portable.py", "Config")):
        tree = ast.parse(Path(filename).read_text())
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "uvicorn"
            and node.func.attr == target
        ]
        assert len(calls) == 1
        assert any(
            keyword.arg == "proxy_headers"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is False
            for keyword in calls[0].keywords
        )
