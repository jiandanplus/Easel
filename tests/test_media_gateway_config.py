"""Pure unified gateway settings and CLI selection contracts; no paid requests."""
from __future__ import annotations

import importlib
import http.server
import io
from pathlib import Path
import sys
import threading
import urllib.error
import urllib.request
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/shared/scripts"))


def gateway():
    return importlib.import_module("media_gateway")


def settings_payload(**overrides):
    return {
        "baseUrl": "https://gateway.test/team/ai", "key": "business-key",
        "modes": {name: "new-api" for name in ("image", "video", "music", "voice")},
        "image": {"model": "image-alias"},
        "video": {"model": "video-alias", "profile": "standard"},
        "music": {"provider": "dashscope", "model": "fun-music-v1"},
        "voice": {"provider": "gemini", "model": "gemini-tts-alias", "voiceId": "Kore"},
        "geminiTtsApi": "interactions", **overrides,
    }


def unified_env(**overrides):
    return {**gateway().gateway_updates(settings_payload(), {}), **overrides}


def test_custom_and_manual_preexisting_newapi_are_preserved():
    g = gateway()
    env = {"DASHSCOPE_API_KEY": "custom-key", "MUSIC_API_MODE": "legacy"}
    assert g.resolve_media_environment("music", env) == env
    manual = {"VIDEO_API_MODE": "new-api", "VIDEO_BASE_URL": "https://old.test/v1", "VIDEO_MODEL": "old"}
    assert g.resolve_media_environment("video", manual) == manual


def test_settings_roundtrip_is_nested_and_never_returns_secret():
    g = gateway()
    env = unified_env()
    view = g.gateway_settings(env)
    assert view["keyConfigured"] is True
    assert "business-key" not in repr(view)
    assert view["keyMasked"] and view["configured"]
    assert view["modes"] == settings_payload()["modes"]
    assert view["voice"] == settings_payload()["voice"]
    assert view["geminiTtsApi"] == "interactions"
    assert set(env) <= set(g.GATEWAY_ENV_KEYS)


def test_empty_key_keeps_and_address_change_requires_fresh_key():
    g = gateway()
    env = unified_env()
    assert g.gateway_updates(settings_payload(key=""), env)["NEW_API_KEY"] == "business-key"
    with pytest.raises(ValueError, match="密钥|key"):
        g.gateway_updates(settings_payload(key="", baseUrl="https://other.test"), env)
    assert g.gateway_updates(settings_payload(key="replacement", baseUrl="https://other.test"), env)["NEW_API_KEY"] == "replacement"


def test_clear_key_only_after_all_modes_custom():
    g = gateway()
    with pytest.raises(ValueError):
        g.gateway_updates(settings_payload(clearKey=True, key=""), unified_env())
    update = g.gateway_updates(settings_payload(clearKey=True, key="", modes={name: "custom" for name in ("image", "video", "music", "voice")}), unified_env())
    assert update["NEW_API_KEY"] == ""
    assert update["MUSIC_API_MODE"] == "legacy"


@pytest.mark.parametrize("changes", [
    {"NEW_API_BASE_URL": ""}, {"NEW_API_KEY": ""},
    {"NEW_API_IMAGE_MODEL": ""}, {"NEW_API_VIDEO_MODEL": ""},
    {"NEW_API_VOICE_MODEL": ""}, {"NEW_API_MUSIC_PROVIDER": "bad"},
    {"NEW_API_VIDEO_PROFILE": "bad"}, {"IMAGE_API_MODE": "auto"},
    {"NEW_API_BASE_URL": "https://user:secret@gateway.test"},
    {"NEW_API_BASE_URL": "https://gateway.test?key=secret"},
    {"NEW_API_BASE_URL": "http://remote.test"}, {"NEW_API_KEY": "bad\nsecret"},
])
def test_direct_env_validation_rejects_invalid_configuration(changes):
    with pytest.raises(ValueError):
        gateway().validate_gateway_env(unified_env(**changes))


def test_direct_env_address_change_cannot_reuse_stored_key():
    g = gateway()
    env = unified_env()
    with pytest.raises(ValueError):
        g.validate_gateway_env({**env, "NEW_API_BASE_URL": "https://other.test"}, previous=env)


@pytest.mark.parametrize("suffix", ["", "/v1"])
def test_image_and_video_routes_preserve_root_prefix(suffix):
    g = gateway()
    env = unified_env(NEW_API_BASE_URL="https://gateway.test/proxy" + suffix)
    for group, prefix in (("image", "IMG"), ("video", "VIDEO")):
        effective = g.resolve_media_environment(group, env)
        expected = "https://gateway.test/proxy" + suffix + "/v1"
        # An explicitly entered /v1 is part of the root; never strip it.
        assert effective[prefix + "_BASE_URL"] == expected
        assert effective[prefix + "_API_KEY"] == "business-key"
        assert effective[prefix + "_MODEL"] == group + "-alias"
    assert "IMG_BASE_URL" not in env


def test_scoped_music_and_voice_never_rewrite_other_group_credentials():
    g = gateway()
    env = unified_env(NEW_API_VOICE_PROVIDER="dashscope", NEW_API_VOICE_MODEL="cosyvoice-v2", DASHSCOPE_API_KEY="original", VOICE_NARRATOR_VOICE_ID="old-voice")
    music = g.resolve_media_environment("music", env)
    voice = g.resolve_media_environment("voice", env)
    assert music["DASHSCOPE_MUSIC_MODEL"] == "fun-music-v1"
    assert "DASHSCOPE_TTS_MODEL" not in music
    assert voice["DASHSCOPE_TTS_MODEL"] == "cosyvoice-v2"
    assert "DASHSCOPE_MUSIC_MODEL" not in voice
    assert env["DASHSCOPE_API_KEY"] == "original"
    assert music["VOICE_NARRATOR_VOICE_ID"] == "old-voice"
    assert voice["VOICE_NARRATOR_VOICE_ID"] == "Kore"


@pytest.mark.parametrize("provider,model,basekey,keykey,modelkey", [
    ("openai-compatible", "tts-alias", "VOICE_BASE_URL", "VOICE_API_KEY", "VOICE_MODEL"),
    ("gemini", "gemini-alias", "GEMINI_BASE_URL", "GEMINI_API_KEY", "GEMINI_TTS_MODEL"),
    ("minimax", "", "MINIMAX_BASE_URL", "MINIMAX_API_KEY", "MINIMAX_MODEL"),
    ("fish-audio", "", "FISH_BASE_URL", "FISH_API_KEY", "FISH_MODEL"),
])
def test_voice_routes_and_known_private_defaults(provider, model, basekey, keykey, modelkey):
    effective = gateway().resolve_media_environment("voice", unified_env(NEW_API_VOICE_PROVIDER=provider, NEW_API_VOICE_MODEL=model, NEW_API_VOICE_ID="", VOICE_NARRATOR_VOICE_ID="SiliconFlow:alex"))
    assert effective[keykey] == "business-key"
    assert effective[modelkey]
    assert effective["VOICE_NARRATOR_VOICE_ID"] != "SiliconFlow:alex"
    assert effective[basekey] == "https://gateway.test/team/ai" + ({"openai-compatible": "/v1", "gemini": "/v1beta"}.get(provider, ""))
    if provider == "gemini":
        assert effective["GEMINI_TTS_API"] == "generate-content"


def test_registry_lists_only_current_gateway_provider_without_old_credentials():
    from model_registry import configured_providers
    env = unified_env()
    assert [p["id"] for p in configured_providers("voice", env)] == ["gemini"]
    assert [p["id"] for p in configured_providers("video", env)] == ["openai-compatible"]


@pytest.mark.parametrize("module,group,selected", [("ai_video", "video", "openai-compatible"), ("ai_music", "music", "dashscope"), ("voice_clone", "voice", "gemini")])
def test_imported_provider_helpers_honor_gateway_and_reject_conflicting_cli(module, group, selected):
    script = importlib.import_module(module)
    with patch.dict("os.environ", unified_env(), clear=True):
        assert script.resolve_provider(None) == selected
        with pytest.raises(SystemExit):
            script.resolve_provider("ark" if group == "video" else "minimax" if group == "voice" else "suno-compatible")


def test_video_model_helper_reads_unified_model_without_mutating_environment():
    import ai_video
    with patch.dict("os.environ", unified_env(), clear=True):
        assert ai_video.resolve_model("openai-compatible") == "video-alias"


def test_preexisting_private_voice_allows_explicit_provider_without_new_selection():
    import voice_clone
    env = {"VOICE_API_MODE": "new-api", "NEW_API_BASE_URL": "https://old.test", "NEW_API_KEY": "old-key"}
    with patch.dict("os.environ", env, clear=True):
        assert voice_clone.resolve_provider("minimax") == "minimax"
        assert gateway().resolve_media_environment("voice", env) == env


def test_configured_is_per_capability_and_incomplete_group_does_not_block_another():
    g = gateway()
    env = unified_env(NEW_API_VIDEO_MODEL="")
    assert g.gateway_settings(env)["configured"] == {"image": True, "video": False, "music": True, "voice": True}
    assert g.resolve_media_environment("image", env)["IMG_MODEL"] == "image-alias"


@pytest.mark.parametrize("group,env", [
    ("image", {"IMAGE_API_MODE": "new-api"}),
    ("video", {"VIDEO_API_MODE": "new-api", "VIDEO_PROVIDER": "openai-compatible", "VIDEO_BASE_URL": "https://old-video.test/v1", "VIDEO_API_KEY": "old-video", "VIDEO_MODEL": "old-model"}),
    ("music", {"MUSIC_API_MODE": "new-api", "MUSIC_PROVIDER": "suno-compatible", "MUSIC_BASE_URL": "https://old-music.test", "MUSIC_API_KEY": "old-music"}),
])
def test_old_handwritten_newapi_routes_ignore_other_capability_shared_root(group, env):
    env.update(NEW_API_BASE_URL="https://voice-gateway.test", NEW_API_KEY="shared-key")
    assert gateway().resolve_media_environment(group, env) == env


@pytest.mark.parametrize("group,changes", [
    ("image", {"NEW_API_VIDEO_PROFILE": "invalid", "GEMINI_TTS_API": "invalid"}),
    ("voice", {"NEW_API_VIDEO_PROFILE": "invalid"}),
    ("music", {"GEMINI_TTS_API": "invalid"}),
])
def test_invalid_unrelated_group_fields_do_not_prevent_scoped_resolution(group, changes):
    assert gateway().resolve_media_environment(group, unified_env(**changes))


def test_unified_image_does_not_guess_gateway_protocol_from_vendor_hostname():
    import ai_image
    with patch.dict("os.environ", unified_env(NEW_API_BASE_URL="https://api.apimart.ai/team"), clear=True):
        assert ai_image.detect_mode("https://api.apimart.ai/team/v1", None) == "sync"
        assert ai_image.detect_provider("https://api.agnes-ai.cn/team/v1") == "openai"
        with pytest.raises(SystemExit):
            ai_image.detect_mode("https://gateway.test/v1", "async")


def test_unified_image_config_check_describes_active_generation_and_edit_route(capsys):
    import ai_image
    with patch.dict("os.environ", unified_env(), clear=True):
        ai_image.cmd_check(None)
    output = capsys.readouterr().out
    assert "image-alias" in output
    assert "text2img" in output and "img2img" in output
    assert "仅 variations" not in output
    assert "business-key" not in output


def test_voice_cli_custom_mode_preserves_existing_argument_default():
    import argparse
    import voice_clone
    seen = []
    args = argparse.Namespace(cmd="clone", env_file=None, voice_id=None, func=lambda a: seen.append(a.voice_id) or 0)
    parser = Mock()
    parser.parse_args.return_value = args
    with patch.dict("os.environ", {"VOICE_API_MODE": "custom", "VOICE_NARRATOR_VOICE_ID": "saved-custom-narrator"}, clear=True), \
            patch.object(voice_clone, "build_parser", return_value=parser), \
            patch.object(voice_clone, "find_default_env_file", return_value=None), \
            patch.object(voice_clone, "load_env_file"):
        assert voice_clone.main() == 0
    assert seen == [None]


@pytest.mark.parametrize("route", ["image-generation", "image-edit", "voice-openai", "voice-gemini"])
def test_gateway_http_errors_never_echo_business_key(route, capsys, tmp_path):
    import argparse
    import ai_image
    import voice_clone
    env = unified_env(NEW_API_VOICE_PROVIDER="openai-compatible", NEW_API_VOICE_MODEL="tts-alias")
    env = gateway().resolve_media_environment("voice", gateway().resolve_media_environment("image", env))
    error = urllib.error.HTTPError("https://gateway.test", 401, "Unauthorized", {}, io.BytesIO(b'{"error":"invalid token business-key"}'))
    image = tmp_path / "source.png"
    image.write_bytes(b"png-fixture")
    args = argparse.Namespace(image=str(image), mask=None, n=1, size="1024x1024", quality=None,
                              model=None, voice_id="alloy", text="hello", speed=1, emotion=None)
    with patch.dict("os.environ", env, clear=True), pytest.raises(SystemExit):
        if route == "image-generation":
            with patch.object(ai_image, "_open", side_effect=error):
                ai_image.http_post("https://gateway.test/v1/images/generations", "business-key", {})
        elif route == "image-edit":
            with patch.object(ai_image, "_open", side_effect=error), patch.object(ai_image, "_check_image", return_value=(image, "image/png")):
                ai_image._post_edits_multipart("https://gateway.test/v1", "business-key", "image-alias", "hello", args)
        elif route == "voice-openai":
            with patch.object(voice_clone, "_open", side_effect=error):
                voice_clone.clone_openai(args, tmp_path / "voice.mp3")
        else:
            with patch.object(voice_clone, "_open", side_effect=error):
                voice_clone.http_json("https://gateway.test/v1beta/models/gemini:generateContent", {}, {})
    output = capsys.readouterr()
    assert "business-key" not in output.err + output.out
    assert "401" in output.err


def test_custom_image_error_keeps_existing_supplier_diagnostics(capsys):
    import ai_image
    error = urllib.error.HTTPError("https://custom.test", 401, "Unauthorized", {}, io.BytesIO(b'{"error":"supplier detail"}'))
    with patch.dict("os.environ", {"IMAGE_API_MODE": "custom"}, clear=True), patch.object(ai_image, "_open", side_effect=error), pytest.raises(SystemExit):
        ai_image.http_post("https://custom.test/images/generations", "custom-key", {})
    assert "supplier detail" in capsys.readouterr().err


def test_gateway_image_success_status_error_payload_does_not_echo_key(capsys):
    import ai_image
    with patch.dict("os.environ", unified_env(), clear=True), pytest.raises(SystemExit):
        ai_image.save_sync_data({"error": {"message": "invalid token business-key"}}, "unused", "png")
    assert "business-key" not in capsys.readouterr().err


@pytest.mark.parametrize("group", ["image", "voice"])
def test_unified_core_requests_reject_redirect_before_forwarding_business_key(group):
    calls = []
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            calls.append(self.path)
            if self.path == "/source":
                self.send_response(302)
                self.send_header("Location", "/target")
            else:
                self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")
        do_POST = do_GET
        def log_message(self, *args):
            pass
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/source"
    try:
        with patch.dict("os.environ", unified_env(), clear=True):
            if group == "image":
                import ai_image
                with pytest.raises(urllib.error.HTTPError):
                    ai_image._open(urllib.request.Request(url, headers={"Authorization": "Bearer business-key"}), 5)
            else:
                import voice_clone
                with pytest.raises(SystemExit):
                    voice_clone.http_json(url, {"Authorization": "Bearer business-key"}, {})
        assert calls == ["/source"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
