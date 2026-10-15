import asyncio
import json
from pathlib import Path

import pytest
from easel.harnesses.base import HarnessError, RunRequest
from tests.test_portable_runtime import bundle, isolated
from tests.test_agent_harnesses import sandbox


def provider():
    from easel.harnesses import provider
    return provider


def save(root, **kw):
    return provider().save_provider(root, base_url=kw.pop("base_url", "http://127.0.0.1:9000/v1"),
                                    model=kw.pop("model", "easel-fake"), **kw)


def test_store_secret_lifecycle_and_atomic_failure(tmp_path, monkeypatch):
    p = provider()
    assert not p.provider_status(tmp_path)["configured"]
    save(tmp_path, api_key="dummy-private", secret_action="replace")
    path = tmp_path / "outputs/_harness/bundled-provider.json"
    before = path.read_bytes()
    assert path.stat().st_mode & 0o777 == 0o600
    assert p.provider_status(tmp_path) == {"configured": True, "base_url": "http://127.0.0.1:9000/v1", "model": "easel-fake", "key_present": True}
    assert "dummy-private" not in json.dumps(p.provider_status(tmp_path))
    save(tmp_path, secret_action="preserve")
    with pytest.raises(HarnessError): save(tmp_path, base_url="https://other.example/v1", secret_action="preserve")
    assert path.read_bytes() == before
    original = Path.replace
    monkeypatch.setattr(Path, "replace", lambda *a: (_ for _ in ()).throw(OSError("failed")))
    with pytest.raises(HarnessError): save(tmp_path, api_key="different", secret_action="replace")
    assert path.read_bytes() == before
    assert list(path.parent.glob("*.tmp")) == []
    monkeypatch.setattr(Path, "replace", original)
    save(tmp_path, secret_action="clear")
    assert not p.provider_status(tmp_path)["configured"]
    assert not p.provider_status(tmp_path)["key_present"]


@pytest.mark.parametrize("values", [
    {"base_url": "ftp://example.com"}, {"base_url": "https://user:pass@example.com"},
    {"base_url": "https://example.com/#x"}, {"base_url": "https://example.com/?token=x"},
    {"base_url": "https://example.com/\n"}, {"model": "bad\x00model"}, {"model": ""},
    {"model": " model"}, {"api_key": "bad\nkey"}, {"secret_action": "other"},
])
def test_reject_invalid_values_without_writes(tmp_path, values):
    with pytest.raises(HarnessError): save(tmp_path, **{"secret_action": "replace", "api_key": "dummy", **values})
    assert not (tmp_path / "outputs/_harness/bundled-provider.json").exists()


def test_malformed_and_symlink_store_not_overwritten(tmp_path):
    path = tmp_path / "outputs/_harness/bundled-provider.json"
    path.parent.mkdir(parents=True)
    path.write_text("{")
    with pytest.raises(HarnessError): save(tmp_path, secret_action="replace", api_key="dummy")
    assert path.read_text() == "{"
    path.unlink()
    target = tmp_path / "outside"
    target.write_text("{}")
    path.symlink_to(target)
    with pytest.raises(HarnessError): save(tmp_path, secret_action="replace", api_key="dummy")
    assert target.read_text() == "{}"


def test_shared_preparation_and_truthful_model_catalog(tmp_path, monkeypatch):
    from easel.harnesses.runtime import prepare_codex, codex_model
    from easel.harnesses.models import available_models
    bundle(tmp_path, monkeypatch)
    root = tmp_path / "workspace"
    request = RunRequest("hello", root, {"CODEX_HOME": "/outside", "OPENAI_API_KEY": "global", "EASEL_BUNDLED_API_KEY": "old"})
    with pytest.raises(HarnessError, match="设置"): prepare_codex(request, ["fake", "app-server"])
    save(root, secret_action="replace", api_key="dummy-private")
    prepared, command = prepare_codex(request, ["fake", "app-server"])
    assert prepared.model == codex_model(root, "") == "easel-fake"
    assert prepared.env["EASEL_BUNDLED_API_KEY"] == "dummy-private"
    assert "OPENAI_API_KEY" not in prepared.env
    assert request.env["OPENAI_API_KEY"] == "global"
    assert request.env["EASEL_BUNDLED_API_KEY"] == "old"
    assert prepared.env["CODEX_HOME"] == str(root / "outputs/_harness/codex-home")
    assert "dummy-private" not in str(command)
    assert 'requires_openai_auth=false' in str(command)
    assert 'wire_api="responses"' in str(command)
    assert asyncio.run(available_models("codex", request)) == [{"id": "easel-fake", "label": "easel-fake", "default": True}]
    with pytest.raises(HarnessError): codex_model(root, "other")
    monkeypatch.delenv("EASEL_RUNTIME_MANIFEST")
    assert prepare_codex(request, ["external"] ) == (request, ["external"])


def test_provider_api_guard_masking_and_model_consistency(sandbox, tmp_path, monkeypatch):
    import web.app as web
    bundle(tmp_path, monkeypatch)
    root = web.PROJECT_ROOT
    assert sandbox.get("/api/settings/harness/provider").status_code == 200
    payload = {"base_url": "http://127.0.0.1:9000/v1", "model": "easel-fake", "api_key": "dummy-private", "secret_action": "replace"}
    monkeypatch.setattr(web, "_HARNESS_TASKS", {"active"})
    assert sandbox.post("/api/settings/harness/provider", json=payload).status_code == 409
    web._HARNESS_TASKS.clear()
    response = sandbox.post("/api/settings/harness/provider", json=payload)
    assert response.status_code == 200, response.text
    assert "dummy-private" not in response.text
    assert sandbox.get("/api/settings/harness").json()["models"]["codex"] == "easel-fake"
    config = web._harness_settings()
    assert web._task_context(web.ChatRequest(message="hello"), "codex", config).model == "easel-fake"
    assert sandbox.post("/api/settings/harness", json={"runtime":"codex", "model":"other"}).status_code == 400
    assert sandbox.post("/api/settings/harness", json={"runtime":"codex", "model":""}).status_code == 200
    assert sandbox.post("/api/settings/harness/provider", json={**payload,"api_key":"", "secret_action":"clear"}).json()["configured"] is False
    assert sandbox.get("/api/settings/harness").json()["models"]["codex"] == ""
    assert sandbox.post("/api/settings/harness/models/codex").status_code == 409


def test_provider_invalid_payload_never_reflects_secret(sandbox, tmp_path, monkeypatch):
    bundle(tmp_path, monkeypatch)
    for payload in ({"base_url":"http://127.0.0.1", "model":"m", "api_key": "dummy-secret" * 2000},
                    {"base_url":"http://127.0.0.1", "model":"m", "api_key": {"dummy-secret":"bad"}}):
        response = sandbox.post("/api/settings/harness/provider", json=payload)
        assert response.status_code == 400
        assert "dummy-secret" not in response.text


def test_console_context_records_configured_model(tmp_path, monkeypatch):
    import easel.harnesses as harnesses
    bundle(tmp_path, monkeypatch)
    root = tmp_path / "workspace"
    save(root, secret_action="replace", api_key="dummy-private")
    seen = []
    class Capture:
        async def run(self, request, *args):
            seen.append(request)
    monkeypatch.setattr(harnesses, "adapter", lambda runtime: Capture())
    config = {"runtime":"codex", "models":{}, "sandbox":"read-only", "codex_auto_approve":False,
              "service_source":"custom", "execution_target":"local"}
    asyncio.run(harnesses.console_turn("hello", root, {}, config=config))
    assert seen[0].model == "easel-fake"


def test_provider_get_ignores_global_codex_credentials(tmp_path, monkeypatch):
    from easel.harnesses.runtime import prepare_codex
    bundle(tmp_path, monkeypatch)
    root = tmp_path / "workspace"
    save(root, secret_action="replace", api_key="dummy-private")
    home = tmp_path / "home/.codex"
    home.mkdir(parents=True)
    (home / "auth.json").write_text('{"OPENAI_API_KEY":"global-secret"}')
    original = Path.read_text
    def guarded(path, *args, **kwargs):
        assert not path.is_relative_to(home), "global native credentials must never be read"
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", guarded)
    request = RunRequest("hello", root, {"CODEX_HOME":str(home), "OPENAI_API_KEY":"global-secret"})
    effective, command = prepare_codex(request, ["fake", "app-server"])
    assert effective.env["CODEX_HOME"] != str(home)
    assert "global-secret" not in str(effective.env)
    assert "global-secret" not in str(command)


@pytest.mark.parametrize("state,installed", [("empty", True), ("configured", True), ("cleared", True), ("configured", False)])
def test_bundled_setup_reports_local_provider_readiness(sandbox, tmp_path, monkeypatch, state, installed):
    import web.app as web
    bundle(tmp_path, monkeypatch)
    monkeypatch.setattr(web.harnesses, "describe", lambda runtime: {"origin": "bundled", "installed": installed})
    if state != "empty":
        save(web.PROJECT_ROOT, secret_action="replace", api_key="dummy-readiness-key")
    if state == "cleared":
        save(web.PROJECT_ROOT, secret_action="clear")
    monkeypatch.setattr(web, "_harness_settings", lambda: {"runtime": "codex"})
    monkeypatch.setattr(web, "_direct_api_enabled", lambda: False)
    monkeypatch.setattr(web, "check_gateway", lambda: pytest.fail("bundled readiness must stay local"))
    expected = state == "configured" and installed
    setup = sandbox.get("/api/settings/bootstrap").json()
    assert setup["modelConfigured"] is expected
    assert setup["envKeyOk"] is (state == "configured")
    assert setup["routeOk"] is expected
    assert setup["gatewayUp"] is expected
    assert setup["authVerified"] is False
    assert "供应商" in setup["routeDetail"] and "设置" in setup["routeDetail"]
    assert "原生登录" not in setup["routeDetail"]
    assert "dummy-readiness-key" not in json.dumps(setup)
    assert sandbox.get("/api/status").json()["gateway"] is expected


@pytest.mark.parametrize("runtime,origin", [("codex", "external"), ("codex", "override"), ("opencode", "external"), ("acp", "external")])
@pytest.mark.parametrize("installed", [True, False])
def test_external_native_setup_keeps_installation_contract(sandbox, monkeypatch, runtime, origin, installed):
    import web.app as web
    monkeypatch.setattr(web, "_harness_settings", lambda: {"runtime": runtime})
    monkeypatch.setattr(web, "_direct_api_enabled", lambda: False)
    monkeypatch.setattr(web.harnesses, "describe", lambda selected: {"installed": installed, "origin": origin})
    monkeypatch.setattr(provider(), "provider_status", lambda root: pytest.fail("external runtime must not read bundled settings"))
    setup = sandbox.get("/api/settings/bootstrap").json()
    assert all(setup[key] is installed for key in ("modelConfigured", "envKeyOk", "routeOk", "gatewayUp"))
    assert "原生登录" in setup["routeDetail"]
    assert setup["authVerified"] is False
    assert sandbox.get("/api/status").json()["gateway"] is installed
