"""Voice cloning straight at the supplier, through the existing audited registration stages.

The stage records keep a fixed `task_...` reference. MiniMax ids fit inside one; a
CosyVoice voice id does not, so the runtime stores it beside the reference.
"""
import base64
import hashlib
import json
import secrets
from urllib.parse import quote

from ...errors import PlatformError
from .newapi_voice import BridgeVoiceResult, DashScopeVoiceAdapter, GatewayTaskReference, MiniMaxVoiceAdapter, _encode, _sample, _task

_SAMPLE_LIMIT = 10 * 1024 * 1024


def _bad():
    return PlatformError(502, "invalid_voice_response")


def minimax_voice_id(reference):
    return "easel" + _task(reference)[5:]


class _Direct:
    def _model(self, request, capability):
        if not isinstance(request, dict) or request.get("capability") != capability or capability not in self.provider.capabilities:
            raise PlatformError(503, "voice_capability_closed")
        model = self.provider.models.get(request.get("model"))
        if not isinstance(model, str):
            raise PlatformError(503, "voice_capability_closed")
        return model

    async def query(self, *args, **kwargs):
        # These suppliers have no task to look up; an unknown POST stays for manual review.
        raise PlatformError(503, "voice_query_unavailable")


class MiniMaxDirectVoice(_Direct, MiniMaxVoiceAdapter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        group = (self.provider.platform_options or {}).get("group_id")
        suffix = "?GroupId=" + quote(group, safe="") if group else ""
        self.paths = {"upload": "/v1/files/upload" + suffix, "register": "/v1/voice_clone" + suffix}

    def prepare_register(self, request, refs):
        model, parameters = self._enrollment(request)
        if not isinstance(refs, dict) or set(refs) != {"file_task_id", "target_model"} or refs["target_model"] != model:
            raise PlatformError(422, "invalid_voice_reference")
        file_task = _task(refs["file_task_id"])
        reference = "task_" + hashlib.sha256(file_task.encode()).hexdigest()[:32]
        body = {"file_id": int(file_task[5:], 16), "voice_id": minimax_voice_id(reference),
                **{key: parameters[key] for key in ("need_noise_reduction", "need_volume_normalization") if key in parameters}}
        return self._prepared("register", _encode(body), "minimax-voice-clone", target_model=model)

    def _parse(self, value, prepared, evidence):
        status = value.get("base_resp")
        if not isinstance(status, dict) or status.get("status_code") != 0:
            raise _bad()
        if prepared.stage == "upload":
            uploaded = value.get("file")
            file_id = uploaded.get("file_id") if isinstance(uploaded, dict) else None
            if type(file_id) is not int or file_id < 0:
                raise _bad()
            task = "task_" + format(file_id, "032x")
            return BridgeVoiceResult(GatewayTaskReference(self.adapter_id, task), "completed", evidence, {"operation": "upload", "file_task_id": task})
        task = "task_" + json.loads(prepared.payload)["voice_id"][5:]
        return BridgeVoiceResult(GatewayTaskReference(self.adapter_id, task), "completed", evidence, {"operation": "clone", "voice_task_id": task})


class DashScopeDirectVoice(_Direct, DashScopeVoiceAdapter):
    verify_interval = 10
    path = "/api/v1/services/audio/tts/customization"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.paths, self.voice_ids = {"create": self.path, "verify": self.path}, {}

    def prepare_upload(self, request, assets):
        model = self._model(request, "voice.enroll")
        if self._parameters(request, {"target_model"}).get("target_model") != model or request.get("voice_id") is not None:
            raise PlatformError(422, "invalid_voice_parameters")
        if not isinstance(assets, list) or len(assets) != 1 or request.get("assets") != [assets[0].get("id")]:
            raise PlatformError(422, "invalid_voice_assets")
        raw = _sample(assets[0], limit=_SAMPLE_LIMIT)
        audio = "data:" + assets[0]["mime"] + ";base64," + base64.b64encode(raw).decode("ascii")
        body = {"model": "voice-enrollment", "input": {"action": "create_voice", "target_model": model, "prefix": "ec" + secrets.token_hex(3), "audio": audio}}
        return self._prepared("create", _encode(body), "voice-enrollment", target_model=model)

    def prepare_verify(self, request, refs):
        model = self._model(request, "voice.enroll")
        if not isinstance(refs, dict) or set(refs) != {"voice_task_id", "target_model"} or refs["target_model"] != model:
            raise PlatformError(422, "invalid_voice_reference")
        body = {"model": "voice-enrollment", "input": {"action": "query_voice", "voice_id": self.voice_ids[_task(refs["voice_task_id"])]}}
        return self._prepared("verify", _encode(body), "voice-enrollment", target_model=model)

    def _parse(self, value, prepared, evidence):
        output = value.get("output")
        if not isinstance(output, dict):
            raise _bad()
        if prepared.stage == "create":
            voice_id = output.get("voice_id")
            if not isinstance(voice_id, str) or not voice_id:
                raise _bad()
            task = "task_" + hashlib.sha256(voice_id.encode()).hexdigest()[:32]
            self.voice_ids[task] = voice_id
            facts = {"operation": "create_voice", "target_model": prepared.target_model, "voice_task_id": task, "voice_status": "UNVERIFIED"}
            return BridgeVoiceResult(GatewayTaskReference(self.adapter_id, task), "completed", evidence, facts, provider_voice_id=voice_id)
        voice_id = json.loads(prepared.payload)["input"]["voice_id"]
        task, status = "task_" + hashlib.sha256(voice_id.encode()).hexdigest()[:32], output.get("status")
        facts = {"operation": "query_voice", "target_model": prepared.target_model, "voice_task_id": task}
        if status in ("OK", "DEPLOYING"):
            return BridgeVoiceResult(GatewayTaskReference(self.adapter_id, task), "completed", evidence, {**facts, "voice_status": status})
        return BridgeVoiceResult(GatewayTaskReference(self.adapter_id, task), "rejected", evidence, {**facts, "voice_status": "UNDEPLOYED"})


DIRECT_VOICE = {"newapi_minimax_voice_v1": MiniMaxDirectVoice, "newapi_dashscope_voice_v1": DashScopeDirectVoice}
