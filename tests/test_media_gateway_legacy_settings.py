"""Legacy hand-written New API configuration stays editable until explicit adoption."""
import importlib
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/shared/scripts"))
import media_gateway as gateway


LEGACY = {"IMAGE_API_MODE": "new-api", "IMG_API_KEY": "legacy-image-key", "IMG_BASE_URL": "https://legacy.invalid/v1", "IMG_MODEL": "legacy-model"}


def form(env):
    result = gateway.gateway_settings(env)
    return {key: value for key, value in result.items() if key not in ("keyConfigured", "keyMasked", "configured", "managed")}


def test_settings_distinguish_legacy_route_from_managed_route():
    result = gateway.gateway_settings(LEGACY)
    assert result["modes"]["image"] == "new-api"
    assert result["managed"]["image"] is False
    assert result["configured"]["image"] is False


def test_unrelated_form_save_preserves_legacy_group_without_adopting_it():
    payload = form(LEGACY)
    payload.update(baseUrl="https://gateway.invalid/team", key="business-key")
    payload["modes"]["video"] = "new-api"
    payload["video"]["model"] = "new-video"
    updates = gateway.gateway_updates(payload, LEGACY)
    assert "IMAGE_API_MODE" not in updates
    assert "NEW_API_IMAGE_MODEL" not in updates
    assert gateway.gateway_managed("image", {**LEGACY, **updates}) is False
    assert gateway.gateway_managed("video", {**LEGACY, **updates}) is True


def test_explicit_legacy_adoption_requires_complete_new_configuration():
    payload = form(LEGACY)
    payload["adopt"] = ["image"]
    with pytest.raises(ValueError):
        gateway.gateway_updates(payload, LEGACY)
    payload.update(baseUrl="https://gateway.invalid", key="business-key")
    payload["image"]["model"] = "new-image"
    updates = gateway.gateway_updates(payload, LEGACY)
    assert updates["NEW_API_IMAGE_MODEL"] == "new-image"
    assert gateway.gateway_managed("image", {**LEGACY, **updates})


@pytest.mark.parametrize("module,group", [("ai_image", "image"), ("ai_video", "video"), ("ai_music", "music"), ("voice_clone", "voice")])
def test_script_loader_refreshes_saved_gateway_fields_from_daemon_stale_env(tmp_path, monkeypatch, module, group):
    # Simulates an OpenClaw daemon launching a fresh skill with old inherited env.
    for key in gateway.GATEWAY_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("NEW_API_BASE_URL", "https://stale.invalid")
    monkeypatch.setenv("NEW_API_KEY", "stale-key")
    monkeypatch.setenv(gateway.GATEWAY_SPEC[group]["modeEnv"], "custom" if group != "music" else "legacy")
    monkeypatch.setenv("IMG_API_KEY", "process-vendor-key")
    saved = {"NEW_API_BASE_URL": "https://saved.invalid/team", "NEW_API_KEY": "saved-key", gateway.GATEWAY_SPEC[group]["modeEnv"]: "new-api", gateway.GATEWAY_SPEC[group]["modelEnv"]: "saved-model", "IMG_API_KEY": "disk-vendor-key"}
    if group == "voice":
        saved["NEW_API_VOICE_PROVIDER"] = "openai-compatible"
    env_file = tmp_path / ".env"
    env_file.write_text("\n".join(f"{key}={value}" for key, value in saved.items()))
    importlib.import_module(module).load_env_file(env_file)
    effective = gateway.resolve_media_environment(group, os.environ)
    assert effective["NEW_API_KEY"] == "saved-key"
    assert effective["NEW_API_BASE_URL"] == "https://saved.invalid/team"
    assert gateway.gateway_managed(group, effective)
    assert os.environ["IMG_API_KEY"] == "process-vendor-key"


def test_refresh_removes_stale_key_after_saved_clear():
    result = gateway.refresh_gateway_environment({"NEW_API_KEY": "stale-key", "IMG_API_KEY": "vendor"}, {"NEW_API_VIDEO_PROFILE": "standard", "IMAGE_API_MODE": "custom"})
    assert result["NEW_API_KEY"] == ""
    assert result["IMG_API_KEY"] == "vendor"


def test_registry_loader_refreshes_gateway_only(tmp_path, monkeypatch):
    from model_registry import read_env_file
    path = tmp_path / ".env"
    path.write_text("NEW_API_BASE_URL=https://saved.invalid\nNEW_API_KEY=saved-key\nNEW_API_IMAGE_MODEL=saved-image\nIMAGE_API_MODE=new-api\nIMG_API_KEY=disk-vendor\n")
    monkeypatch.setenv("NEW_API_KEY", "stale-key")
    monkeypatch.setenv("IMG_API_KEY", "process-vendor")
    result = read_env_file(path)
    assert result["NEW_API_KEY"] == "saved-key"
    assert result["IMG_API_KEY"] == "process-vendor"


def test_multivoice_new_cli_task_reads_saved_gateway_despite_daemon_values(tmp_path, monkeypatch):
    import json
    import multivoice
    monkeypatch.chdir(tmp_path)
    for key in gateway.GATEWAY_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("NEW_API_BASE_URL", "https://stale.invalid")
    monkeypatch.setenv("NEW_API_KEY", "stale-key")
    monkeypatch.setenv("NEW_API_VOICE_PROVIDER", "minimax")
    monkeypatch.setenv("VOICE_API_MODE", "custom")
    (tmp_path / ".env").write_text("NEW_API_BASE_URL=https://saved.invalid\nNEW_API_KEY=saved-key\nNEW_API_VOICE_MODEL=saved-model\nNEW_API_VOICE_PROVIDER=openai-compatible\nNEW_API_VOICE_ID=alloy\nVOICE_API_MODE=new-api\n")
    out = tmp_path / "cast.json"
    monkeypatch.setattr(sys, "argv", ["multivoice.py", "cast", "init", "--cast", str(out)])
    assert multivoice.main() == 0
    narrator = json.loads(out.read_text())["cast"][0]
    assert narrator["provider"] == "openai-compatible"
    assert narrator["voice_id"] == "alloy"
    assert os.environ["NEW_API_KEY"] == "saved-key"
