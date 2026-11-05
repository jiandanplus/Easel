"""Gemini TTS protocol and audio-container tests; no real provider calls."""
from __future__ import annotations

import argparse
import base64
import io
import sys
import wave
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "shared" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import voice_clone  # noqa: E402


def arguments(**values):
    return argparse.Namespace(model=None, voice_id=None, text="你好，世界。", emotion=None, **values)


def inline(data=b"\x01\x00" * 20, mime="audio/L16;codec=pcm;rate=24000", key="inlineData"):
    return {"candidates": [{"content": {"parts": [{key: {"data": base64.b64encode(data).decode(), "mimeType": mime}}]}}]}


@pytest.fixture()
def capture(monkeypatch):
    for key in ("GEMINI_TTS_API", "GEMINI_BASE_URL", "GEMINI_TTS_MODEL", "GEMINI_TTS_RATE", "GEMINI_VOICE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "fixture-not-a-real-key")
    calls = []
    response = {"value": inline()}

    def request(url, headers, payload, *args, **kwargs):
        calls.append((url, headers, payload))
        return response["value"]

    monkeypatch.setattr(voice_clone, "http_json", request)
    return calls, response


def test_defaults_to_native_generate_content_for_newapi(capture, tmp_path):
    calls, _ = capture
    out = voice_clone.clone_gemini(arguments(), tmp_path / "speech.wav")
    url, headers, payload = calls[0]
    assert url == "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent"
    assert headers == {"x-goog-api-key": "fixture-not-a-real-key"}
    assert payload["contents"][0]["parts"] == [{"text": "你好，世界。"}]
    assert payload["generationConfig"]["responseModalities"] == ["AUDIO"]
    assert payload["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Kore"
    with wave.open(str(out)) as audio:
        assert audio.getframerate() == 24000
        assert audio.getnframes() == 20


def test_keeps_custom_base_as_api_prefix(capture, tmp_path, monkeypatch):
    calls, _ = capture
    monkeypatch.setenv("GEMINI_BASE_URL", "https://gateway.example.test/custom/v1beta/")
    monkeypatch.setenv("GEMINI_TTS_MODEL", "gemini-3.1-flash-tts-preview")
    voice_clone.clone_gemini(arguments(), tmp_path / "speech.wav")
    assert calls[0][0] == "https://gateway.example.test/custom/v1beta/models/gemini-3.1-flash-tts-preview:generateContent"


def test_interactions_remains_explicit_and_does_not_double_wrap_wav(capture, tmp_path, monkeypatch):
    calls, response = capture
    monkeypatch.setenv("GEMINI_TTS_API", "interactions")
    monkeypatch.setenv("GEMINI_BASE_URL", "https://direct.example.test/v1beta")
    wav = voice_clone._pcm_to_wav_bytes(b"\x01\x00" * 10, rate=16000)
    response["value"] = {"output_audio": {"data": base64.b64encode(wav).decode(), "mime_type": "audio/wav"}}
    out = voice_clone.clone_gemini(arguments(), tmp_path / "speech.wav")
    assert calls[0][0] == "https://direct.example.test/v1beta/interactions"
    assert calls[0][2]["response_format"] == {"type": "audio", "mime_type": "audio/wav"}
    assert out.read_bytes() == wav


def test_pcm_uses_returned_mime_rate_before_fallback_env(capture, tmp_path, monkeypatch):
    _, response = capture
    monkeypatch.setenv("GEMINI_TTS_RATE", "24000")
    response["value"] = inline(mime="audio/L16;codec=pcm;rate=16000;channels=1")
    out = voice_clone.clone_gemini(arguments(), tmp_path / "speech.wav")
    with wave.open(str(out)) as audio:
        assert audio.getframerate() == 16000


def test_ignores_non_audio_inline_parts(capture, tmp_path):
    _, response = capture
    response["value"] = inline()
    response["value"]["candidates"][0]["content"]["parts"].insert(0, {"inlineData": {"mimeType": "image/png", "data": "aW1hZ2U="}})
    out = voice_clone.clone_gemini(arguments(), tmp_path / "speech.wav")
    with wave.open(str(out)) as audio:
        assert audio.readframes(100) == b"\x01\x00" * 20


@pytest.mark.parametrize("value", ["not valid base64!", "", "%%%%"])
def test_rejects_invalid_audio_base64(capture, tmp_path, value):
    _, response = capture
    response["value"] = {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "audio/L16;rate=24000", "data": value}}]}}]}
    with pytest.raises(SystemExit):
        voice_clone.clone_gemini(arguments(), tmp_path / "speech.wav")
    assert not (tmp_path / "speech.wav").exists()


def test_reads_interactions_rest_audio_steps(capture, tmp_path, monkeypatch):
    _, response = capture
    monkeypatch.setenv("GEMINI_TTS_API", "interactions")
    wav = voice_clone._pcm_to_wav_bytes(b"\x00\x00" * 10)
    response["value"] = {"steps": [{"type": "model_output", "content": [{"type": "audio", "mime_type": "audio/wav", "data": base64.b64encode(wav).decode()}]}]}
    assert voice_clone.clone_gemini(arguments(), tmp_path / "speech.wav").read_bytes() == wav


def test_v38_style_is_metadata_not_spoken_prefix(capture, tmp_path, monkeypatch):
    calls, response = capture
    monkeypatch.setenv("GEMINI_TTS_MODEL", "gemini-3.8-flash-tts")
    wav = voice_clone._pcm_to_wav_bytes(b"\x00\x00" * 10)
    response["value"] = inline(wav, mime="audio/wav")
    args = arguments()
    args.emotion = "温柔"
    voice_clone.clone_gemini(args, tmp_path / "speech.wav")
    payload = calls[0][2]
    part = payload["contents"][0]["parts"][0]
    assert part["text"] == "你好，世界。"
    assert "温柔" in part["speech_metadata"]["style"]
    assert payload["generationConfig"]["speechConfig"]["voiceConfig"] == {"voice": "Kore"}


def test_unknown_mode_fails_before_network(capture, tmp_path, monkeypatch):
    calls, _ = capture
    monkeypatch.setenv("GEMINI_TTS_API", "unknown")
    with pytest.raises(SystemExit):
        voice_clone.clone_gemini(arguments(), tmp_path / "speech.wav")
    assert calls == []


def test_encoded_mp3_is_saved_as_mp3_without_pcm_wrapping(capture, tmp_path):
    _, response = capture
    mp3 = b"ID3" + b"\x00" * 40
    response["value"] = inline(mp3, mime="audio/mpeg")
    assert voice_clone.clone_gemini(arguments(), tmp_path / "speech.mp3").read_bytes() == mp3


@pytest.mark.parametrize("data,mime", [
    (b"\x00", "audio/L16;rate=24000"),
    (b"\x00\x00", "audio/L16;rate=invalid"),
    (b"\x00\x00", "audio/L16;rate=24000;channels=7"),
    (b"not-a-wave-file", "audio/wav"),
    (b"unidentified", "audio/unknown"),
])
def test_rejects_malformed_audio_containers(capture, tmp_path, data, mime):
    _, response = capture
    response["value"] = inline(data, mime)
    with pytest.raises(SystemExit):
        voice_clone.clone_gemini(arguments(), tmp_path / "speech.wav")
    assert not (tmp_path / "speech.wav").exists()


def test_pcm_fallback_uses_env_when_mime_has_no_rate(capture, tmp_path, monkeypatch):
    _, response = capture
    monkeypatch.setenv("GEMINI_TTS_RATE", "22050")
    response["value"] = inline(mime="audio/L16;codec=pcm")
    out = voice_clone.clone_gemini(arguments(), tmp_path / "speech.wav")
    with wave.open(str(out)) as audio:
        assert audio.getframerate() == 22050


def test_wav_header_wins_over_stale_pcm_mime(capture, tmp_path):
    _, response = capture
    wav = voice_clone._pcm_to_wav_bytes(b"\x00\x00" * 10, rate=16000)
    response["value"] = inline(wav, mime="audio/L16;rate=24000")
    assert voice_clone.clone_gemini(arguments(), tmp_path / "speech.wav").read_bytes() == wav


def test_no_ffmpeg_returns_existing_valid_source_format(capture, tmp_path, monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda _: None)
    out = voice_clone.clone_gemini(arguments(), tmp_path / "speech.mp3")
    assert out == tmp_path / "speech.wav"
    with wave.open(io.BytesIO(out.read_bytes())) as audio:
        assert audio.getnframes() == 20
