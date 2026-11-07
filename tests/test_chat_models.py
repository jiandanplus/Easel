"""Owned catalog fixtures; no real provider credentials or model calls."""
import asyncio
from dataclasses import replace
import json
import sys

import httpx
import pytest

from easel.harnesses.base import HarnessError, RunRequest
from easel.harnesses.provider import save_provider


def module():
    from easel import chat_models
    return chat_models


def custom(root):
    save_provider(root, base_url="https://fixture.invalid/v1", model="first",
                  api_key="SYNTHETIC_PRIVATE", secret_action="replace")
    return dict(runtime="pi", models={"pi": "first"}, service_source="custom",
                execution_target="local", subscription_backend="central")


def rows():
    return [{"id": "first"}, {"id": "second", "name": "Second",
            "reasoning": {"efforts": ["low", "high"]}, "api_key": "NEVER_EXPORT"}]


def registry(monkeypatch, clock=None):
    m = module()
    monkeypatch.setattr(m, "provider_models", lambda *_: rows())
    return m.CatalogRegistry(clock=clock or (lambda: 100.0))


def test_catalog_reads_current_provider_without_exposing_keys(tmp_path, monkeypatch):
    config = custom(tmp_path)
    r = registry(monkeypatch)
    result = r.catalog(tmp_path, config, {})
    assert result["ready"] and result["switchable"] and result["selected_model"] == "first"
    assert result["models"][0]["reasoning"] == {"status": "unknown", "efforts": []}
    assert result["models"][1]["reasoning"] == {"status": "supported", "efforts": ["low", "high"]}
    assert "PRIVATE" not in json.dumps(result) and "NEVER_EXPORT" not in json.dumps(result)
    selected = r.select(tmp_path, config, {}, "second", result["revision"], "high")
    assert selected.model == "second" and selected.reasoning_effort == "high"
    assert selected.reasoning_efforts == ("low", "high")


@pytest.mark.parametrize("model,effort", [("absent", ""), ("first", "high"), ("second", "medium")])
def test_invalid_model_or_effort_never_accepted(tmp_path, monkeypatch, model, effort):
    config = custom(tmp_path)
    r = registry(monkeypatch)
    result = r.catalog(tmp_path, config, {})
    with pytest.raises(HarnessError):
        r.select(tmp_path, config, {}, model, result["revision"], effort)


def test_revision_expires_and_cannot_cross_provider_or_root(tmp_path, monkeypatch):
    now = [100.0]
    config = custom(tmp_path)
    r = registry(monkeypatch, lambda: now[0])
    revision = r.catalog(tmp_path, config, {})["revision"]
    now[0] += 121
    with pytest.raises(HarnessError): r.select(tmp_path, config, {}, "first", revision, "")
    now[0] = 100
    other = tmp_path / "other"
    custom(other)
    with pytest.raises(HarnessError): r.select(other, config, {}, "first", revision, "")
    save_provider(tmp_path, base_url="https://fixture.invalid/v1", model="first",
                  api_key="SYNTHETIC_ROTATED", secret_action="replace")
    with pytest.raises(HarnessError): r.select(tmp_path, config, {}, "first", revision, "")


def test_refresh_removes_deleted_model_and_old_revision(tmp_path, monkeypatch):
    config = custom(tmp_path)
    r = registry(monkeypatch)
    first = r.catalog(tmp_path, config, {})
    monkeypatch.setattr(module(), "provider_models", lambda *_: [{"id": "first"}])
    second = r.catalog(tmp_path, config, {}, refresh=True)
    assert second["revision"] != first["revision"]
    with pytest.raises(HarnessError): r.select(tmp_path, config, {}, "second", first["revision"], "")


def test_source_revision_stable_on_refresh_and_expiry_counts_down(tmp_path, monkeypatch):
    now = [100.0]
    config = custom(tmp_path)
    r = registry(monkeypatch, lambda: now[0])
    first = r.catalog(tmp_path, config, {})
    assert first["expires_in"] == 120 and first["source_revision"]
    now[0] += 60
    cached = r.catalog(tmp_path, config, {})
    assert cached["expires_in"] == 60 and cached["revision"] == first["revision"]
    again = r.catalog(tmp_path, config, {}, refresh=True)
    assert again["source_revision"] == first["source_revision"] and again["revision"] != first["revision"]
    monkeypatch.setattr(module(), "provider_models", lambda *_: [{"id": "first"}])
    changed = r.catalog(tmp_path, config, {}, refresh=True)
    assert changed["source_revision"] != first["source_revision"]


def test_provider_catalog_enforces_same_endpoint_bounds_and_whitelist():
    calls = []
    def serve(req):
        calls.append(req)
        return httpx.Response(200, json={"data": rows()})
    result = module().provider_models({"base_url": "https://fixture.invalid/v1/", "api_key": "SYNTHETIC_PRIVATE"},
                                      transport=httpx.MockTransport(serve))
    assert calls[0].url == "https://fixture.invalid/v1/models"
    assert calls[0].headers["authorization"] == "Bearer SYNTHETIC_PRIVATE"
    assert result[1]["reasoning"]["efforts"] == ["low", "high"]
    assert "api_key" not in result[1]


@pytest.mark.parametrize("response", [httpx.Response(302, headers={"location": "https://other.invalid/models"}),
    httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1)),
    httpx.Response(200, json={"data": "invalid"}),
    httpx.Response(401, json={"error": "SYNTHETIC_PRIVATE"})])
def test_provider_catalog_failure_is_bounded_and_safe(response):
    count = []
    def serve(req): count.append(req); return response
    with pytest.raises(HarnessError) as caught:
        module().provider_models({"base_url": "https://fixture.invalid/v1", "api_key": "SYNTHETIC_PRIVATE"},
                                  transport=httpx.MockTransport(serve))
    assert len(count) == 1 and "SYNTHETIC_PRIVATE" not in str(caught.value)


def test_capabilities_are_explicit_not_guessed_from_names():
    normalize = module().normalize_reasoning
    assert normalize({"id": "gpt-future-thinking"}) == {"status": "unknown", "efforts": []}
    assert normalize({"reasoning": {"efforts": []}}) == {"status": "unsupported", "efforts": []}
    assert normalize({"supported_reasoning_efforts": ["high", "low", "invalid", "high"]}) == {"status": "supported", "efforts": ["low", "high"]}
    assert normalize({"reasoning": {"status": "unknown", "efforts": []}}) == {"status": "unknown", "efforts": []}


def test_unsupported_engine_keeps_original_send_path(tmp_path):
    r = module().CatalogRegistry()
    result = r.catalog(tmp_path, {"runtime": "openclaw", "models": {}, "service_source": "custom", "execution_target": "local"}, {})
    assert result["ready"] and not result["switchable"] and result["reason"]


def test_pi_session_model_change_keeps_history_but_not_foreign_identity(tmp_path):
    from easel.harnesses.pi_session import PiSession
    req = RunRequest("hi", tmp_path, {}, model="first")
    old = PiSession(req, "first", "https://fixture.invalid/v1")
    messages = [{"role": "user", "content": "preserve"}]
    old.save(messages)
    changed = PiSession(replace(req, native_id=old.id), "second", "https://fixture.invalid/v1")
    assert changed.messages == messages
    with pytest.raises(HarnessError): PiSession(replace(req, native_id=old.id), "second", "https://other.invalid/v1")


@pytest.mark.asyncio
async def test_pi_verified_selection_reaches_worker(tmp_path, monkeypatch):
    from easel.harnesses.pi import PiAdapter
    monkeypatch.setattr("easel.harnesses.pi.require_provider", lambda _: {"base_url": "https://fixture.invalid/v1", "model": "first", "api_key": "fixture-secret"})
    code = "import sys,json; r=json.loads(sys.stdin.readline()); assert r['model']['id']=='second'; assert r['model']['reasoningEfforts']==['low','high']; assert r['reasoning_effort']=='high'; print(json.dumps({'type':'done','session_id':r['session_id'],'success':True,'messages':[]}))"
    req = RunRequest("hi", tmp_path, {}, model="second", selection_verified=True,
                     reasoning_effort="high", reasoning_efforts=("low", "high"),
                     command=(sys.executable, "-u", "-c", code))
    await PiAdapter().run(req, lambda *_: None, lambda _: None, lambda _: None)
    with pytest.raises(HarnessError):
        await PiAdapter().run(replace(req, selection_verified=False), lambda *_: None, lambda _: None, lambda _: None)


def test_verified_selection_does_not_modify_global_configuration(tmp_path, monkeypatch):
    from easel.service_contracts import task_context
    config = custom(tmp_path)
    before = (tmp_path / "outputs/_harness/bundled-provider.json").read_bytes()
    r = registry(monkeypatch)
    catalog = r.catalog(tmp_path, config, {})
    selected = r.select(tmp_path, config, {}, "second", catalog["revision"], "high")
    context = task_context(config, root=tmp_path, selection=selected)
    assert context.model == "second" and context.reasoning_effort == "high" and context.selection_verified
    assert (tmp_path / "outputs/_harness/bundled-provider.json").read_bytes() == before
    with pytest.raises(HarnessError): task_context(config, root=tmp_path, selection={"model": "second"})


def test_web_catalog_and_turn_selection_are_server_owned(tmp_path, monkeypatch):
    import web.app as web
    from fastapi.testclient import TestClient
    config = custom(tmp_path)
    env = tmp_path / ".env"
    env.write_text("")
    monkeypatch.setattr(web, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(web, "ENV_FILE", env)
    monkeypatch.setattr(web, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(web, "_harness_settings", lambda: config)
    r = registry(monkeypatch)
    monkeypatch.setattr(module(), "catalogs", r)
    with TestClient(web.app, base_url="http://127.0.0.1:7860", client=("127.0.0.1", 10000)) as client:
        response = client.get("/api/chat/models")
        assert response.status_code == 200
        revision = response.json()["revision"]
        req = web.ChatRequest(message="hello", model="second", catalog_revision=revision, reasoning_effort="high")
        context = web._task_context(req, "pi", config, {})
        assert context.model == "second" and context.reasoning_effort == "high"
        for patch in ({"model": "second"}, {"catalog_revision": revision}, {"reasoning_effort": "high"},
                      {"model": "second", "catalog_revision": "forged"}):
            with pytest.raises(web.HTTPException):
                web._task_context(web.ChatRequest(message="hello", **patch), "pi", config, {})


def test_web_catalog_rejects_old_conversation_source_without_reading_models(tmp_path, monkeypatch):
    import web.app as web
    from easel.harnesses import store
    from fastapi.testclient import TestClient
    config = custom(tmp_path)
    env = tmp_path / ".env"
    env.write_text("")
    monkeypatch.setattr(web, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(web, "ENV_FILE", env)
    monkeypatch.setattr(web, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(web, "_harness_settings", lambda: config)
    monkeypatch.setattr(web, "_chat_backend", lambda _: "pi")
    store.write_json(store.binding_path(web.SESSIONS_DIR, "old"), {"runtime": "pi", "native_id": "old", "service_source": "subscription", "execution_target": "local", "account_id": "other"})
    def forbidden(*_): raise AssertionError("must not query a different source")
    monkeypatch.setattr(module(), "provider_models", forbidden)
    with TestClient(web.app, base_url="http://127.0.0.1:7860", client=("127.0.0.1", 10000)) as client:
        response = client.get("/api/chat/models?session_id=old")
        assert response.status_code == 200
        assert not response.json()["ready"] and "服务来源" in response.json()["reason"]


def test_refresh_failure_revokes_previous_catalog(tmp_path, monkeypatch):
    config = custom(tmp_path)
    r = registry(monkeypatch)
    revision = r.catalog(tmp_path, config, {})["revision"]
    def fail(*_): raise HarnessError("unavailable")
    monkeypatch.setattr(module(), "provider_models", fail)
    with pytest.raises(HarnessError): r.catalog(tmp_path, config, {}, refresh=True)
    with pytest.raises(HarnessError): r.select(tmp_path, config, {}, "first", revision)


def test_central_revision_is_bound_to_account_scope_and_credentials(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    state = {"url": "https://central.invalid", "auth": {"scope": {"user_id": "first", "product_id": "easel"}, "access_token": "SYNTHETIC_CENTRAL"}}
    fake = SimpleNamespace(store=SimpleNamespace(locked=lambda: nullcontext()), _state=lambda: state,
                           models=lambda: {"models": rows()})
    monkeypatch.setattr("easel.platform.central.CentralClient", lambda _: fake)
    config = dict(runtime="pi", service_source="subscription", execution_target="local", subscription_backend="central", subscription_model="first")
    r = module().CatalogRegistry()
    result = r.catalog(tmp_path, config, {})
    assert r.select(tmp_path, config, {}, "second", result["revision"], "high").model == "second"
    state["auth"]["scope"]["user_id"] = "second"
    with pytest.raises(HarnessError): r.select(tmp_path, config, {}, "second", result["revision"], "high")


def test_catalog_read_rejects_provider_change_during_discovery(tmp_path, monkeypatch):
    config = custom(tmp_path)
    r = registry(monkeypatch)
    def load(*_):
        save_provider(tmp_path, base_url="https://other.invalid/v1", model="first", api_key="OTHER_SYNTHETIC", secret_action="replace")
        return rows()
    monkeypatch.setattr(module(), "provider_models", load)
    with pytest.raises(HarnessError): r.catalog(tmp_path, config, {})


@pytest.mark.parametrize("field", ["scope", "session_id", "tokens"])
def test_central_discovery_checks_stable_identity_but_allows_same_session_refresh(tmp_path, monkeypatch, field):
    from contextlib import nullcontext
    from types import SimpleNamespace
    state = {"url": "https://central.invalid", "auth": {"scope": {"user_id": "first", "product_id": "easel"}, "session_id": "same", "access_token": "SYNTHETIC_ACCESS", "refresh_token": "SYNTHETIC_REFRESH", "expires_at": 10, "refresh_expires_at": 20}}
    def load():
        if field == "scope": state["auth"]["scope"]["user_id"] = "other"
        elif field == "session_id": state["auth"]["session_id"] = "other"
        else: state["auth"].update(access_token="ROTATED_ACCESS", refresh_token="ROTATED_REFRESH", expires_at=30, refresh_expires_at=40)
        return {"models": rows()}
    fake = SimpleNamespace(store=SimpleNamespace(locked=lambda: nullcontext()), _state=lambda: state, models=load)
    monkeypatch.setattr("easel.platform.central.CentralClient", lambda _: fake)
    config = dict(runtime="pi", service_source="subscription", execution_target="local", subscription_backend="central", subscription_model="first")
    r = module().CatalogRegistry()
    if field == "tokens":
        result = r.catalog(tmp_path, config, {})
        assert r.select(tmp_path, config, {}, "second", result["revision"], "high")
    else:
        with pytest.raises(HarnessError): r.catalog(tmp_path, config, {})


def test_central_token_refresh_preserves_source_and_each_unexpired_lease(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    state = {"url": "https://central.invalid", "auth": {"scope": {"user_id": "first", "product_id": "easel"}, "session_id": "same", "access_token": "SYNTHETIC_ACCESS", "refresh_token": "SYNTHETIC_REFRESH", "expires_at": 10, "refresh_expires_at": 20}}
    count = [0]
    def load():
        count[0] += 1
        if count[0] == 2:
            state["auth"].update(access_token="ROTATED_ACCESS", refresh_token="ROTATED_REFRESH", expires_at=30, refresh_expires_at=40)
        return {"models": rows()}
    fake = SimpleNamespace(store=SimpleNamespace(locked=lambda: nullcontext()), _state=lambda: state, models=load)
    monkeypatch.setattr("easel.platform.central.CentralClient", lambda _: fake)
    config = dict(runtime="pi", service_source="subscription", execution_target="local", subscription_backend="central", subscription_model="first")
    r = module().CatalogRegistry()
    first = r.catalog(tmp_path, config, {})
    second = r.catalog(tmp_path, config, {}, refresh=True)
    assert second["revision"] != first["revision"]
    assert second["source_revision"] == first["source_revision"]
    assert r.select(tmp_path, config, {}, "second", first["revision"], "high")
    assert r.select(tmp_path, config, {}, "second", second["revision"], "high")


def test_provider_key_rotation_changes_source_revision(tmp_path, monkeypatch):
    config = custom(tmp_path)
    r = registry(monkeypatch)
    first = r.catalog(tmp_path, config, {})
    save_provider(tmp_path, base_url="https://fixture.invalid/v1", model="first", api_key="ROTATED_SYNTHETIC", secret_action="replace")
    second = r.catalog(tmp_path, config, {}, refresh=True)
    assert second["source_revision"] != first["source_revision"]


def test_external_codex_native_credential_change_revokes_choice(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from easel.harnesses import models, runtime
    native = tmp_path / "native"
    native.mkdir()
    (native / "auth.json").write_text('{"token":"SYNTHETIC_FIRST"}')
    monkeypatch.setattr(runtime, "resolve_codex", lambda: SimpleNamespace(origin="external"))
    async def load(*_): return rows()
    monkeypatch.setattr(models, "available_models", load)
    config = dict(runtime="codex", service_source="custom", execution_target="local", models={"codex": "first"})
    env = {"CODEX_HOME": str(native)}
    r = module().CatalogRegistry()
    result = r.catalog(tmp_path, config, env)
    assert result["switchable"] and result["models"][1]["reasoning"]["status"] == "unknown"
    assert r.select(tmp_path, config, env, "second", result["revision"])
    (native / "auth.json").write_text('{"token":"SYNTHETIC_SECOND"}')
    with pytest.raises(HarnessError): r.select(tmp_path, config, env, "second", result["revision"])


@pytest.mark.asyncio
async def test_pi_accepted_selection_cannot_be_redirected_after_acceptance(tmp_path, monkeypatch):
    from easel.harnesses.pi import PiAdapter
    config = custom(tmp_path)
    r = registry(monkeypatch)
    result = r.catalog(tmp_path, config, {})
    selected = r.select(tmp_path, config, {}, "second", result["revision"])
    provider = {"base_url": "https://other.invalid/v1", "model": "first", "api_key": "ROTATED_SYNTHETIC"}
    monkeypatch.setattr("easel.harnesses.pi.require_provider", lambda _: provider)
    req = RunRequest("hello", tmp_path, {}, model=selected.model, selection_verified=True,
                     provider_binding=selected.provider_binding, command=("/must/not/spawn",))
    with pytest.raises(HarnessError) as caught:
        await PiAdapter().run(req, lambda *_: None, lambda _: None, lambda _: None)
    assert "供应商" in str(caught.value)


@pytest.mark.asyncio
async def test_chat_completion_only_sends_verified_explicit_reasoning(monkeypatch):
    from easel import direct_api
    original = httpx.AsyncClient
    bodies = []
    def serve(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
    monkeypatch.setattr(direct_api.httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(serve), **kw))
    settings = {"EASEL_DIRECT_API_BASE_URL": "https://fixture.invalid/v1", "EASEL_DIRECT_API_MODEL": "second", "EASEL_DIRECT_API_KEY": "fixture"}
    assert [item async for item in direct_api.stream_chat(settings, "hi", reasoning_effort="high", reasoning_efforts=("low", "high"))]
    assert bodies[0]["reasoning_effort"] == "high"
    assert [item async for item in direct_api.stream_chat(settings, "hi")]
    assert "reasoning_effort" not in bodies[1]
    with pytest.raises(direct_api.DirectAPIError):
        assert [item async for item in direct_api.stream_chat(settings, "hi", reasoning_effort="medium", reasoning_efforts=("high",))]
    assert len(bodies) == 2
