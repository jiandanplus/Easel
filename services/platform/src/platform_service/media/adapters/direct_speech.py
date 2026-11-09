"""Suppliers' own text-to-speech APIs. Each one answers differently; all end as owned audio bytes."""
import base64
import hashlib
import io
import json
import re
import wave
from urllib.parse import quote, urlsplit

import httpx

from ...errors import PlatformError
from ..rates import unicode_scalars
from .audio import CLIENT_NAMESPACE, AudioResult, HttpCorrelation, PreparedAudioRequest, _FORMAT_MIME, _OpenAIAudio, _encode, _text
from .newapi_async import bounded


def _bad():
    return PlatformError(502, "invalid_audio_response")


def _json(raw):
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise _bad() from None
    if not isinstance(value, dict):
        raise _bad()
    return value


class DirectSpeech(_OpenAIAudio):
    capability = "audio.speech"
    supported_meters = frozenset({("unicode_scalar", "verified_input_v1"), ("audio_ms", "verified_delivery_v1")})
    formats = ("mp3", "wav")
    max_characters = 4096

    def _auth(self):
        return {"Authorization": "Bearer " + self.provider.secret}

    def _extra_headers(self, prepared):
        return {}

    def _base(self):
        return self.provider.base_url.rstrip("/")

    def prepare(self, request, assets):
        model = self._model(request)
        if assets or request.get("assets", []):
            raise PlatformError(422, "invalid_audio_assets")
        prompt = _text(request.get("prompt"), self.max_characters)
        parameters = request.get("parameters", {})
        if not isinstance(parameters, dict) or set(parameters) - {"voice", "response_format", "speed_percent"}:
            raise PlatformError(422, "invalid_audio_parameters")
        voice, output, speed = parameters.get("voice"), parameters.get("response_format", self.formats[0]), parameters.get("speed_percent", 100)
        if voice not in self.profile.voices or output not in self.formats or type(speed) is not int or not 50 <= speed <= 200:
            raise PlatformError(422, "invalid_audio_parameters")
        return PreparedAudioRequest(self.url(model), _encode(self.body(model, prompt, voice, output, speed)), "application/json", model, output, {"unicode_scalar": unicode_scalars(prompt)})

    async def submit(self, client, prepared, *, request_id, max_bytes, on_headers):
        headers = {**self._auth(), **self._extra_headers(prepared), "Content-Type": prepared.content_type, "Accept": "*/*", "Accept-Encoding": "identity"}
        try:
            async with client.stream("POST", prepared.url, content=prepared.payload, follow_redirects=False, headers=headers) as response:
                await on_headers(http_status=response.status_code, http_request_id=request_id)
                raw = await bounded(response, max_bytes)
            audio = await self.decode(client, raw, prepared, max_bytes)
        except httpx.HTTPError:
            raise PlatformError(502, "audio_upstream_uncertain") from None
        if not audio:
            raise _bad()
        return AudioResult(HttpCorrelation(CLIENT_NAMESPACE, request_id), audio, _FORMAT_MIME[prepared.response_format], {}, {}, None, hashlib.sha256(raw).hexdigest())


class MiniMaxSpeech(DirectSpeech):
    adapter_id = "minimax_speech_v1"

    def url(self, model):
        group = (self.provider.platform_options or {}).get("group_id")
        return self._base() + "/v1/t2a_v2" + ("?GroupId=" + quote(group, safe="") if group else "")

    def body(self, model, prompt, voice, output, speed):
        return {"model": model, "text": prompt, "stream": False, "output_format": "hex",
                "voice_setting": {"voice_id": voice, "speed": speed / 100}, "audio_setting": {"format": output}}

    async def decode(self, client, raw, prepared, max_bytes):
        value = _json(raw)
        status = value.get("base_resp")
        audio = (value.get("data") or {}).get("audio")
        if not isinstance(status, dict) or status.get("status_code") != 0 or not isinstance(audio, str):
            raise _bad()
        try:
            return bytes.fromhex(audio)
        except ValueError:
            raise _bad() from None


class DashScopeSpeech(DirectSpeech):
    adapter_id = "dashscope_speech_v1"
    max_characters = 2000

    def url(self, model):
        return self._base() + "/api/v1/services/audio/tts/SpeechSynthesizer"

    def body(self, model, prompt, voice, output, speed):
        return {"model": model, "input": {"text": prompt, "voice": voice, "format": output, "rate": speed / 100}}

    async def decode(self, client, raw, prepared, max_bytes):
        value = _json(raw)
        audio = (value.get("output") or {}).get("audio")
        location = audio.get("url") if isinstance(audio, dict) else None
        parsed = urlsplit(location) if isinstance(location, str) else None
        if parsed is None or parsed.scheme != "https" or not (parsed.hostname or "").endswith(".aliyuncs.com"):
            raise PlatformError(502, "media_download_origin_denied")
        async with client.stream("GET", location, follow_redirects=False, headers={"Accept": "*/*", "Accept-Encoding": "identity"}) as response:
            return await bounded(response, max_bytes)


class FishSpeech(DirectSpeech):
    adapter_id = "fish_speech_v1"

    def url(self, model):
        return self._base() + "/v1/tts"

    def _extra_headers(self, prepared):
        return {"model": prepared.upstream_model}

    def body(self, model, prompt, voice, output, speed):
        return {"text": prompt, "format": output, "reference_id": voice, "prosody": {"speed": speed / 100}}

    async def decode(self, client, raw, prepared, max_bytes):
        return raw


class GeminiSpeech(DirectSpeech):
    adapter_id = "gemini_speech_v1"
    formats = ("wav",)

    def _auth(self):
        return {"x-goog-api-key": self.provider.secret}

    def url(self, model):
        return self._base() + "/models/" + quote(model.removeprefix("models/"), safe="") + ":generateContent"

    def body(self, model, prompt, voice, output, speed):
        return {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}}

    async def decode(self, client, raw, prepared, max_bytes):
        value = _json(raw)
        try:
            part = next(p["inlineData"] for p in value["candidates"][0]["content"]["parts"] if "inlineData" in p)
            audio, mime = base64.b64decode(part["data"], validate=True), part["mimeType"].lower()
        except (KeyError, IndexError, TypeError, StopIteration, ValueError, AttributeError):
            raise _bad() from None
        if mime.split(";")[0] in ("audio/wav", "audio/x-wav"):
            return audio
        if not mime.startswith("audio/l16") and not mime.startswith("audio/pcm"):
            raise _bad()
        rate = re.search(r"rate=(\d{4,6})", mime)
        target = io.BytesIO()
        with wave.open(target, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(int(rate.group(1)) if rate else 24000)
            output.writeframes(audio)
        return target.getvalue()


DIRECT_SPEECH = {cls.adapter_id: cls for cls in (MiniMaxSpeech, DashScopeSpeech, FishSpeech, GeminiSpeech)}
