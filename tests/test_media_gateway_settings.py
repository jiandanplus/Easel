"""Unified media settings API: local configuration only, never provider traffic."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "web"))
import app as web


CUSTOM = "# preserved\nIMG_API_KEY=custom-image-secret\nIMG_BASE_URL=https://custom.example/images\nIMG_MODEL=custom-image\nDASHSCOPE_API_KEY=custom-dashscope-secret\n"


def payload():
    return {
        "baseUrl": "https://gateway.example/proxy", "key": "gateway-business-secret",
        "modes": {"image": "new-api", "video": "new-api", "music": "new-api", "voice": "new-api"},
        "image": {"model": "gpt-image-1"},
        "video": {"model": "sora-2", "profile": "standard"},
        "music": {"provider": "suno-compatible", "model": "chirp-v4"},
        "voice": {"provider": "openai-compatible", "model": "tts-1", "voiceId": "alloy"},
        "geminiTtsApi": "generate-content",
    }


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(CUSTOM)
    monkeypatch.setattr(web, "ENV_FILE", env)
    monkeypatch.setattr(web, "_direct_api_enabled", lambda: False)
    monkeypatch.setattr(web, "_harness_settings", lambda: {"runtime": "openclaw"})
    local = "http://127.0.0.1:7860"
    # Do not run application lifespan or touch existing project output directories.
    client = TestClient(web.app, base_url=local, client=("127.0.0.1", 51234), headers={"Origin": local})
    client.env_file = env
    yield client
    client.close()


def save(client, body=None):
    response = client.put("/api/settings/media-gateway", json=body or payload())
    assert response.status_code == 200, response.text
    return response.json()


def test_get_defaults_is_masked_and_roundtrip_preserves_custom(sandbox):
    response = sandbox.get("/api/settings/media-gateway")
    assert response.status_code == 200
    assert response.json()["modes"] == dict.fromkeys(("image", "video", "music", "voice"), "custom")
    body = save(sandbox)
    assert body["ok"] is True and body["configured"] == dict.fromkeys(("image", "video", "music", "voice"), True)
    assert body["keyConfigured"] is True
    assert "gateway-business-secret" not in str(body)
    assert "key" not in body
    assert sandbox.env_file.read_text().startswith(CUSTOM)
    assert "IMG_MODEL=custom-image" in sandbox.env_file.read_text()
    assert body == {**sandbox.get("/api/settings/media-gateway").json(), "ok": True}


def test_blank_key_retains_and_clear_requires_all_custom(sandbox):
    save(sandbox)
    body = payload()
    body["key"] = ""
    save(sandbox, body)
    before = sandbox.env_file.read_bytes()
    body["clearKey"] = True
    assert sandbox.put("/api/settings/media-gateway", json=body).status_code == 400
    assert sandbox.env_file.read_bytes() == before
    body["modes"] = dict.fromkeys(body["modes"], "custom")
    result = save(sandbox, body)
    assert result["keyConfigured"] is False
    assert "gateway-business-secret" not in sandbox.env_file.read_text()
    assert sandbox.env_file.read_text().startswith(CUSTOM)


@pytest.mark.parametrize("change", ["url", "model", "mode", "injection"])
def test_invalid_put_is_atomic_and_safe(sandbox, change):
    save(sandbox)
    body = payload()
    body["key"] = ""
    if change == "url":
        body["baseUrl"] = "https://replacement.example/proxy"
    elif change == "model":
        body["image"]["model"] = ""
    elif change == "mode":
        body["modes"]["image"] = "oops"
    else:
        body["key"] = "unsafe-secret\nTOKEN=another-secret"
    before = sandbox.env_file.read_bytes()
    response = sandbox.put("/api/settings/media-gateway", json=body)
    assert response.status_code == 400, response.text
    assert "unsafe-secret" not in response.text and "another-secret" not in response.text
    assert sandbox.env_file.read_bytes() == before


def test_generic_env_cannot_bypass_gateway_validation(sandbox):
    before = sandbox.env_file.read_bytes()
    response = sandbox.post("/api/env", json={"updates": {"IMAGE_API_MODE": "new-api"}})
    assert response.status_code == 400
    assert sandbox.env_file.read_bytes() == before
    save(sandbox)
    before = sandbox.env_file.read_bytes()
    for updates in ({"NEW_API_BASE_URL": "https://replacement.example"}, {"NEW_API_KEY": ""}, {"NEW_API_VIDEO_MODEL": ""}):
        response = sandbox.post("/api/env", json={"updates": updates})
        assert response.status_code == 400
        assert sandbox.env_file.read_bytes() == before


@pytest.mark.parametrize("field", ["baseUrl", "key", "model"])
def test_enabling_gateway_requires_complete_configuration(sandbox, field):
    body = payload()
    if field == "model":
        body["image"]["model"] = ""
    else:
        body[field] = ""
    before = sandbox.env_file.read_bytes()
    response = sandbox.put("/api/settings/media-gateway", json=body)
    assert response.status_code == 400
    assert sandbox.env_file.read_bytes() == before


def test_reentered_key_allows_address_change_and_switchback_restores_custom(sandbox):
    save(sandbox)
    body = payload()
    body["baseUrl"] = "https://replacement.example/prefix"
    assert save(sandbox, body)["baseUrl"] == body["baseUrl"]
    body["modes"] = dict.fromkeys(body["modes"], "custom")
    body["key"] = ""
    save(sandbox, body)
    rows = web._model_channels()["channels"]["image"]["rows"]
    assert len(rows) == 1 and rows[0]["slot"] == "openai"
    assert rows[0]["baseUrl"] == "https://custom.example/images"
    assert rows[0]["model"] == "custom-image"
    assert sandbox.env_file.read_text().startswith(CUSTOM)


def test_invalid_json_never_echoes_submitted_secrets(sandbox):
    response = sandbox.put("/api/settings/media-gateway", content='{"key":"malformed-secret"', headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    assert "malformed-secret" not in response.text


def test_legacy_manual_adapter_keeps_original_readiness(sandbox):
    sandbox.env_file.write_text(CUSTOM + "IMAGE_API_MODE=new-api\n")
    assert web._skill_api_configured("ai-image-gen") is True
    rows = web._model_channels()["channels"]["image"]["rows"]
    assert len(rows) == 1 and rows[0]["slot"] == "openai"


def test_task_env_preserves_process_only_manual_adapter(sandbox):
    source = {"VIDEO_API_MODE": "new-api", "NEW_API_BASE_URL": "https://manual.example", "NEW_API_KEY": "manual-key", "VIDEO_MODEL": "old-model"}
    assert web._fresh_media_gateway_env(source) == source


def test_task_env_clears_stale_process_key_after_explicit_clear(sandbox):
    save(sandbox)
    body = payload()
    body.update(key="", clearKey=True, modes=dict.fromkeys(body["modes"], "custom"))
    save(sandbox, body)
    result = web._fresh_media_gateway_env({"NEW_API_KEY": "stale-process-key", "IMG_API_KEY": "vendor-key"})
    assert result["NEW_API_KEY"] == ""
    assert result["IMAGE_API_MODE"] == "custom"
    assert result["IMG_API_KEY"] == "vendor-key"


def test_api_preserves_unchanged_legacy_group_when_other_group_is_saved(sandbox):
    sandbox.env_file.write_text(CUSTOM + "IMAGE_API_MODE=new-api\n")
    previous = sandbox.get("/api/settings/media-gateway").json()
    assert previous["managed"]["image"] is False
    body = {key: value for key, value in previous.items() if key not in ("keyConfigured", "keyMasked", "configured", "managed")}
    body.update(baseUrl="https://gateway.example/team", key="business-key")
    body["modes"]["video"] = "new-api"
    body["video"]["model"] = "new-video"
    result = save(sandbox, body)
    assert result["managed"]["image"] is False and result["managed"]["video"] is True
    assert "NEW_API_IMAGE_MODEL" not in sandbox.env_file.read_text()
    assert "IMAGE_API_MODE=new-api" in sandbox.env_file.read_text()
    assert "IMG_API_KEY=custom-image-secret" in sandbox.env_file.read_text()


def test_gateway_rows_and_skill_status_use_effective_route(sandbox):
    save(sandbox)
    assert web._skill_api_configured("ai-image-gen") is True
    channels = web._model_channels()["channels"]
    for channel in ("image", "video", "music", "speech"):
        rows = channels[channel]["rows"]
        gateway = next(row for row in rows if row["slot"] == "new-api")
        assert gateway["role"] == "主" and gateway["result"] == "已配置"
        assert gateway["baseUrl"] == payload()["baseUrl"]
        assert all(row["role"] == "保留" for row in rows if row["slot"] != "new-api")
    sandbox.env_file.write_text(sandbox.env_file.read_text().replace("NEW_API_IMAGE_MODEL=gpt-image-1", "NEW_API_IMAGE_MODEL="))
    assert web._skill_api_configured("ai-image-gen") is False  # custom credentials cannot hide invalid active route


def test_media_rows_use_fresh_file_even_with_direct_chat_process_env(sandbox, monkeypatch):
    save(sandbox)
    monkeypatch.setattr(web, "_direct_api_enabled", lambda: True)
    monkeypatch.setattr(web, "read_settings", lambda *a: {"IMAGE_API_MODE": "custom", "NEW_API_IMAGE_MODEL": "stale-model", "NEW_API_KEY": "stale-key"})
    row = web._model_channels()["channels"]["image"]["rows"][0]
    assert row["slot"] == "new-api" and row["model"] == "gpt-image-1"


def test_old_model_save_cannot_write_synthetic_gateway_slot(sandbox):
    save(sandbox)
    before = sandbox.env_file.read_bytes()
    response = sandbox.post("/api/settings/models/save", json={"channel": "image", "rows": [{"slot": "new-api", "model": "wrong", "key": "accidental-secret"}]})
    assert response.status_code == 400
    assert sandbox.env_file.read_bytes() == before
    # Original provider settings remain independently editable.
    response = sandbox.post("/api/settings/models/save", json={"channel": "image", "rows": [{"slot": "openai", "model": "custom-new"}]})
    assert response.status_code == 200, response.text
    assert "NEW_API_IMAGE_MODEL=gpt-image-1" in sandbox.env_file.read_text()
    assert "IMG_MODEL=custom-new" in sandbox.env_file.read_text()


def test_old_media_selftest_does_not_probe_custom_credentials_in_gateway_mode(sandbox, monkeypatch):
    save(sandbox)
    monkeypatch.setattr(web.urllib.request, "build_opener", lambda *a: pytest.fail("must not create provider probe"))
    response = sandbox.post("/api/settings/models/selftest", json={"channel": "image"})
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_new_run_gets_fresh_gateway_fields_without_alias_overlay(sandbox, monkeypatch, tmp_path):
    from easel.service_contracts import TaskContext
    save(sandbox)
    monkeypatch.setattr(web, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(web, "SESSIONS_DIR", tmp_path / "outputs/_sessions")
    monkeypatch.setattr(web, "_save_turn", lambda *a: None)
    stale = {"NEW_API_KEY": "stale-key", "NEW_API_IMAGE_MODEL": "stale-model", "IMG_API_KEY": "original-vendor-key"}
    monkeypatch.setattr(web, "read_settings", lambda *a: stale.copy())
    monkeypatch.setattr(web, "_proxy_env", lambda: stale.copy())
    seen = []
    class Adapter:
        async def run(self, request, emit, ask, bind):
            seen.append(request)
            emit("token", "ok")
    monkeypatch.setattr(web.harnesses, "adapter", lambda runtime: Adapter())
    await web._run_harness_chat(web.ChatRequest(message="hello"), "media-fresh-env", "hello", "codex", {"sandbox": "read-only"}, lambda *a: None, context=TaskContext("custom", "local", "codex", "native"))
    assert seen[0].env["NEW_API_KEY"] == "gateway-business-secret"
    assert seen[0].env["NEW_API_IMAGE_MODEL"] == "gpt-image-1"
    assert seen[0].env["IMG_API_KEY"] == "original-vendor-key"
