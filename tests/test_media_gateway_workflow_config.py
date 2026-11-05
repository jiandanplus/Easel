"""Unified settings reach voice workflow selection without mixing supplier credentials."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/shared/scripts"))
import tts
import multivoice


@pytest.fixture
def gateway_voice(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("NEW_API_", "VOICE_", "GEMINI_")):
            monkeypatch.delenv(name, raising=False)
    for name, value in {
        "VOICE_API_MODE": "new-api", "NEW_API_BASE_URL": "https://gateway.invalid/team",
        "NEW_API_KEY": "unit-gateway-business-credential", "NEW_API_VOICE_PROVIDER": "openai-compatible",
        "NEW_API_VOICE_MODEL": "gateway-tts", "NEW_API_VOICE_ID": "alloy",
    }.items():
        monkeypatch.setenv(name, value)


def test_tts_auto_detects_gateway_voice_without_custom_provider(gateway_voice):
    assert tts._closed_provider() == "openai-compatible"


def test_tts_uses_gateway_voice_instead_of_preserved_custom_id(gateway_voice, monkeypatch):
    monkeypatch.setenv("VOICE_PROVIDER", "minimax")
    monkeypatch.setenv("VOICE_NARRATOR_VOICE_ID", "old-private-upstream-voice")
    assert tts._closed_voice_id(None) == "alloy"
    assert os.environ["VOICE_NARRATOR_VOICE_ID"] == "old-private-upstream-voice"


def test_multivoice_recognizes_gateway_as_cloud_voice(gateway_voice):
    before = dict(os.environ)
    assert multivoice._closed_source_configured()
    assert dict(os.environ) == before


def test_cast_init_uses_selected_gateway_voice(gateway_voice, tmp_path):
    out = tmp_path / "cast.json"
    multivoice.cast_init(out)
    import json
    narrator = json.loads(out.read_text())["cast"][0]
    assert narrator["engine"] == "clone"
    assert narrator["provider"] == "openai-compatible"
    assert narrator["voice_id"] == "alloy"


def test_custom_voice_selection_remains_unchanged(gateway_voice, monkeypatch):
    monkeypatch.setenv("VOICE_API_MODE", "custom")
    monkeypatch.setenv("VOICE_PROVIDER", "minimax")
    monkeypatch.setenv("VOICE_NARRATOR_VOICE_ID", "existing-custom-voice")
    assert tts._closed_provider() == "minimax"
    assert tts._closed_voice_id(None) == "existing-custom-voice"


def test_legacy_manual_voice_keeps_narrator_default(gateway_voice, monkeypatch, tmp_path):
    for key in list(os.environ):
        if key.startswith("NEW_API_VOICE_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("VOICE_PROVIDER", "openai-compatible")
    monkeypatch.setenv("VOICE_MODEL", "FunAudioLLM/CosyVoice2-0.5B")
    expected = "FunAudioLLM/CosyVoice2-0.5B:alex"
    assert tts._closed_voice_id(None) == expected
    import json
    out = tmp_path / "legacy-cast.json"
    multivoice.cast_init(out, narrator_provider="openai-compatible")
    assert json.loads(out.read_text())["cast"][0]["voice_id"] == expected
