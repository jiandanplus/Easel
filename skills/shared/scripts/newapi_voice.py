"""Explicit New API private voice adapters; standard library, no POST retries.

Provider credentials and bridge secrets stay on the gateway. All IDs used as
voice dependencies are public owner-bound task IDs, never supplier voice IDs.
"""
from __future__ import annotations

import base64
import json
import math
import os
from pathlib import Path
import re
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


PROVIDERS = frozenset({"minimax", "dashscope", "fish-audio"})
PUBLIC_ID = re.compile(r"task_[A-Za-z0-9]{32}\Z")
MAX_AUDIO = 8 << 20
MAX_JSON = 2 << 20
DASH_MODELS = frozenset({"cosyvoice-v3.5-plus", "cosyvoice-v3.5-flash", "cosyvoice-v3-plus", "cosyvoice-v3-flash", "cosyvoice-v2"})
MINIMAX_MODELS = frozenset("easel-minimax-" + model for model in (
    "speech-2.8-hd", "speech-2.8-turbo", "speech-2.6-hd", "speech-2.6-turbo",
    "speech-02-hd", "speech-02-turbo", "speech-01-hd", "speech-01-turbo"))
FISH_MODELS = frozenset("easel-fish-" + model for model in ("s2-pro", "s2.1-pro", "s2.1-pro-free", "drama-3"))
MIME = {"mp3": "audio/mpeg", "wav": "audio/wav", "pcm": "application/octet-stream", "opus": "audio/opus"}


class VoiceError(Exception):
    """Only fixed, non-sensitive local messages may cross the HTTP boundary."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise VoiceError("New API 语音请求拒绝重定向；未转发凭据或重新提交。")


def enabled(fail) -> bool:
    mode = os.environ.get("VOICE_API_MODE", "").strip() or "custom"
    if mode not in ("custom", "new-api"):
        fail("VOICE_API_MODE 仅支持 custom 或 new-api，不按域名猜测协议。")
    return mode == "new-api"


def _id(value):
    if not isinstance(value, str) or not PUBLIC_ID.fullmatch(value):
        raise VoiceError("New API 语音必须使用 task_ 开头的公共任务 ID，不能传供应商原始音色 ID。")
    return value


def _origin(url):
    try:
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username
                or parsed.password or parsed.fragment):
            raise ValueError
        return parsed.scheme, parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == "https" else 80)
    except (ValueError, TypeError):
        raise VoiceError("New API 语音 URL 格式不合法。") from None


def _text(value, limit, label):
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-16-le")) // 2 > limit:
        raise VoiceError(f"{label}必须包含 1 到 {limit} 个 UTF-16 字符单位。")
    return value


def _speed(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or not 0.5 <= value <= 2:
        raise VoiceError("New API 语音 speed 必须为 0.5 到 2 之间的有限数值。")
    return value


def _sample(path, limit):
    try:
        file = Path(path)
        if not file.is_file() or not 0 < file.stat().st_size <= limit:
            raise VoiceError("语音参考样本为空或超过对应插件的文件大小限制。")
        with file.open("rb") as stream:
            data = stream.read(limit + 1)
        if not 0 < len(data) <= limit:
            raise VoiceError("语音参考样本为空或超过对应插件的文件大小限制。")
        return file, data
    except (OSError, TypeError):
        raise VoiceError("无法读取语音参考样本文件。") from None


class Client:
    def __init__(self, base_url, key):
        self.base = base_url.rstrip("/")
        self.origin = _origin(self.base)
        parsed = urllib.parse.urlsplit(self.base)
        if parsed.query or (parsed.scheme != "https" and parsed.hostname not in ("127.0.0.1", "localhost", "::1")):
            raise VoiceError("NEW_API_BASE_URL 必须为 HTTPS 网关根地址或本地回环开发地址，可保留路径前缀。")
        self.key = key
        self.recovery_id = None
        if not isinstance(key, str) or not key or any(character in key for character in "\r\n"):
            raise VoiceError("NEW_API_KEY 不能为空且不能包含换行。")
        try:
            self.timeout = float(os.environ.get("NEW_API_VOICE_TIMEOUT", "180"))
            self.interval = float(os.environ.get("NEW_API_VOICE_POLL_INTERVAL", "2"))
        except ValueError:
            raise VoiceError("New API 语音超时或轮询间隔配置不合法。") from None
        if not 1 <= self.timeout <= 600 or not 0.1 <= self.interval <= 30:
            raise VoiceError("NEW_API_VOICE_TIMEOUT 必须为 1..600 秒，轮询间隔必须为 0.1..30 秒。")
        self.opener = urllib.request.build_opener(NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()))

    def endpoint(self, path):
        return self.base + "/" + path.lstrip("/")

    def _read(self, url, *, method="GET", payload=None, body=None, content_type=None,
              authorize=True, timeout=None, limit=MAX_JSON):
        headers = {"User-Agent": "Easel-NewAPI-Voice/1"}
        if authorize:
            if _origin(url) != self.origin:
                raise VoiceError("拒绝向其他 origin 发送网关语音凭据。")
            headers["Authorization"] = "Bearer " + self.key
        else:
            _origin(url)  # External downloads still reject userinfo/invalid URLs.
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            content_type = "application/json"
        if content_type:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout if timeout is None else timeout) as response:
                raw = response.read(limit + 1)
                mime = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        except VoiceError:
            raise
        except urllib.error.HTTPError as error:
            error.close()
            raise VoiceError(f"New API 语音 HTTP {error.code}；未回退协议或重新提交。") from None
        except Exception:
            raise VoiceError("New API 语音连接失败；请求结果可能未知，未重新提交收费操作。") from None
        if len(raw) > limit:
            raise VoiceError("New API 语音响应超过大小限制。")
        return raw, mime

    def json(self, path, *, payload=None, body=None, content_type=None, timeout=None):
        raw, _ = self._read(self.endpoint(path), method="POST" if payload is not None or body is not None else "GET",
            payload=payload, body=body, content_type=content_type, timeout=timeout)
        try:
            result = json.loads(raw)
        except (json.JSONDecodeError, UnicodeError):
            raise VoiceError("New API 语音未返回有效 JSON；未重新提交。") from None
        if not isinstance(result, dict) or "error" in result or result.get("code") or result.get("success") is False:
            raise VoiceError("New API 语音返回业务错误；未回退或重新提交。")
        return result

    def download(self, url, out, *, expected_size=None, expected_mime=None):
        raw, mime = self._read(url, authorize=_origin(url) == self.origin, limit=MAX_AUDIO)
        if not raw or (expected_size is not None and len(raw) != expected_size):
            raise VoiceError("New API 语音音频为空或与完成元数据中的大小不一致。")
        if expected_mime is not None and mime != expected_mime:
            raise VoiceError("New API 语音下载 MIME 与完成元数据不一致。")
        if mime and not (mime.startswith("audio/") or mime == "application/octet-stream"):
            raise VoiceError("New API 语音下载不是可验证的音频内容。")
        return self.save(raw, out)

    @staticmethod
    def save(raw, out):
        out.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".voice-", dir=out.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
            os.replace(temporary, out)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return out

    def minimax_enroll(self, a):
        resume = getattr(a, "resume_id", None)
        if resume:
            uploaded = self.json("/easel/minimax/v1/tasks/" + _id(resume))
        else:
            file, raw = _sample(getattr(a, "sample", None), 20 << 20)
            if file.suffix.lower() not in (".mp3", ".m4a", ".wav") or any(c in file.name for c in '\\/\r\n"'):
                raise VoiceError("MiniMax 样本必须为安全文件名的 mp3、m4a 或 wav。")
            boundary = "EaselVoice" + uuid.uuid4().hex
            body = (f'--{boundary}\r\nContent-Disposition: form-data; name="purpose"\r\n\r\nvoice_clone\r\n'
                    f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{file.name}"\r\n'
                    'Content-Type: application/octet-stream\r\n\r\n').encode() + raw + f"\r\n--{boundary}--\r\n".encode()
            uploaded = self.json("/easel/minimax/v1/files/upload", body=body, content_type="multipart/form-data; boundary=" + boundary)
        file_id = self._minimax_complete(uploaded, "file_task_id")
        if resume and file_id != resume:
            raise VoiceError("MiniMax 恢复查询返回的上传公共 ID 不一致。")
        self.recovery_id = file_id
        print("已上传 file_task_id=" + file_id + "；请保留此 ID，后续登记失败可用 --resume-id 继续。", file=sys.stderr)
        cloned = self.json("/easel/minimax/v1/voice_clone", payload={"file_task_id": file_id})
        return self._minimax_complete(cloned, "voice_task_id")

    @staticmethod
    def _minimax_complete(result, reference=None):
        if result.get("status") != "SUCCESS":
            raise VoiceError("MiniMax 私有操作未完成；不会重做上传、登记或合成。")
        task_id = _id(result.get("task_id"))
        if reference is not None and result.get(reference) != task_id:
            raise VoiceError("MiniMax 归属任务公共 ID 不完整或不一致。")
        return task_id

    def minimax_clone(self, a, out, emotion):
        voice_id = _id(getattr(a, "voice_id", None))
        if out.suffix.lower() not in ("", ".mp3"):
            raise VoiceError("MiniMax 私有短文本插件只返回 MP3，输出文件必须使用 .mp3。")
        model = a.model or os.environ.get("MINIMAX_MODEL", "").strip() or "easel-minimax-speech-2.8-hd"
        if model not in MINIMAX_MODELS:
            raise VoiceError("MiniMax 网关模式需要已声明的 easel-minimax-speech-* 模型。")
        payload = {"model": model, "voice_task_id": voice_id,
                   "text": _text(a.text, 500, "MiniMax 文本"), "speed": _speed(a.speed)}
        if emotion and emotion != "neutral":
            if emotion not in {"happy", "sad", "angry", "fearful", "disgusted", "surprised"}:
                raise VoiceError("MiniMax 情感值不受支持。")
            payload["emotion"] = emotion
        result = self.json("/easel/minimax/v1/t2a_v2", payload=payload)
        self._minimax_complete(result)
        data, extra = result.get("data"), result.get("extra_info")
        if (not isinstance(data, dict) or data.get("status") != 2 or not isinstance(extra, dict)
                or extra.get("audio_format") != "mp3" or type(extra.get("usage_characters")) is not int
                or extra["usage_characters"] < 0):
            raise VoiceError("MiniMax 未返回完整的非流式 MP3 完成结果。")
        audio = data.get("audio")
        if not isinstance(audio, str) or not audio or len(audio) > 900000 or len(audio) % 2 or not re.fullmatch(r"[0-9a-fA-F]+", audio):
            raise VoiceError("MiniMax 完成响应缺少合法、有界的 hex 音频。")
        return self.save(bytes.fromhex(audio), out)

    @staticmethod
    def _dash_output(result):
        output = result.get("output")
        if not isinstance(output, dict) or output.get("task_status") != "SUCCEEDED":
            raise VoiceError("DashScope 私有语音操作未返回完成态。")
        _id(output.get("task_id"))
        return output

    def dashscope_enroll(self, a):
        selected = a.model or os.environ.get("DASHSCOPE_TTS_MODEL", "").strip()
        resume = getattr(a, "resume_id", None)
        if resume:
            created = self._dash_output(self.json("/easel/dashscope/voice/tasks/" + _id(resume)))
            model = created.get("target_model")
            if selected and selected != model:
                raise VoiceError("DashScope 恢复时模型必须与已有登记 target_model 完全一致。")
        else:
            model = selected or "cosyvoice-v2"
            if model not in DASH_MODELS:
                raise VoiceError("DashScope target_model 不在当前私有插件声明中。")
            sample_url = getattr(a, "sample_url", None)
            _origin(sample_url)
            prefix = getattr(a, "name", None) or ("pc" + uuid.uuid4().hex[:6])
            if not isinstance(prefix, str) or not re.fullmatch(r"[A-Za-z0-9]{1,10}", prefix):
                raise VoiceError("DashScope --name 前缀必须为 1 到 10 位字母或数字。")
            created = self._dash_output(self.json("/easel/dashscope/voice/enroll", payload={"model": "voice-enrollment", "input": {
                "action": "create_voice", "target_model": model, "prefix": prefix, "url": sample_url}}))
        source = _id(created.get("voice_task_id"))
        if (source != created["task_id"] or created.get("target_model") != model or model not in DASH_MODELS
                or created.get("voice_status") != "UNVERIFIED" or (resume and source != resume)):
            raise VoiceError("DashScope 登记公共 ID、create_voice 阶段或 target_model 不一致。")
        self.recovery_id = source
        print("已登记待审核 voice_task_id=" + source + "；此 ID 尚不是最终可合成音色，可用 --resume-id 继续审核。", file=sys.stderr)
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise VoiceError("DashScope 审核查询超时；未重新创建音色，请保留原登记公共任务 ID。")
            output = self._dash_output(self.json("/easel/dashscope/voice/verify", payload={"model": "voice-enrollment",
                "input": {"action": "query_voice", "voice_task_id": source}}, timeout=min(self.timeout, remaining)))
            if output.get("target_model") != model or output.get("voice_task_id") != output["task_id"]:
                raise VoiceError("DashScope 审核响应 target_model 或公共 ID 不一致。")
            if output.get("voice_status") == "OK":
                return _id(output["voice_task_id"])
            if output.get("voice_status") != "DEPLOYING":
                raise VoiceError("DashScope 音色审核未就绪或被拒绝；未重新创建音色。")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise VoiceError("DashScope 审核查询超时；未重新创建音色，请保留原登记公共任务 ID。")
            time.sleep(min(self.interval, remaining))

    def dashscope_clone(self, a, out, instruction):
        voice_id = _id(getattr(a, "voice_id", None))
        reviewed = self._dash_output(self.json("/easel/dashscope/voice/tasks/" + voice_id))
        model = reviewed.get("target_model")
        if (reviewed.get("voice_task_id") != voice_id or reviewed.get("task_id") != voice_id
                or reviewed.get("voice_status") != "OK" or model not in DASH_MODELS):
            raise VoiceError("DashScope 合成需要已审核 OK 的公共验证任务 ID。")
        selected = a.model or os.environ.get("DASHSCOPE_TTS_MODEL", "").strip() or model
        if selected != model:
            raise VoiceError("DashScope 合成模型必须与登记/审核 target_model 完全一致。")
        format = out.suffix.lstrip(".").lower() or "mp3"
        if format not in MIME:
            raise VoiceError("DashScope 私有语音输出格式不受支持。")
        inputs = {"text": _text(a.text, 100000, "DashScope 文本"), "voice_task_id": voice_id,
                  "format": format, "rate": _speed(a.speed)}
        if instruction:
            inputs["instruction"] = instruction
        output = self._dash_output(self.json("/easel/dashscope/voice/synthesize", payload={"model": model, "input": inputs}))
        audio = output.get("audio")
        if output.get("finish_reason") != "stop" or not isinstance(audio, dict) or not isinstance(audio.get("url"), str):
            raise VoiceError("DashScope 未返回可验证的同步完成音频。")
        return self.download(audio["url"], out)

    def fish_clone(self, a, out):
        model = a.model or os.environ.get("FISH_MODEL", "").strip() or "easel-fish-s2.1-pro"
        format = out.suffix.lstrip(".").lower() or "mp3"
        if model not in FISH_MODELS or format not in MIME:
            raise VoiceError("Fish 网关模型或输出格式不受当前私有插件支持。")
        payload = {"model": model, "text": _text(a.text, 2000, "Fish 文本"), "format": format, "speed": _speed(a.speed)}
        reference, sample = getattr(a, "voice_id", None), getattr(a, "sample", None)
        if bool(reference) == bool(sample):
            raise VoiceError("Fish 必须在管理员预置 --voice-id 与 --sample 参考音中选择一种。")
        if reference:
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", reference):
                raise VoiceError("Fish 管理员预置 reference_id 格式不合法。")
            payload["reference_id"] = reference
        else:
            _, raw = _sample(sample, 1 << 20)
            payload["references"] = [{"audio": base64.b64encode(raw).decode(),
                                      "text": _text(getattr(a, "sample_text", None), 2000, "Fish 参考音转写")}]
        result = self.json("/easel/fish/v1/tts", payload=payload)
        task_id = _id(result.get("task_id"))
        deadline = time.monotonic() + self.timeout
        while result.get("status") != "SUCCESS":
            if result.get("status") not in ("NOT_START", "SUBMITTED", "QUEUED", "IN_PROGRESS"):
                raise VoiceError("Fish 任务失败或状态未知；未重新提交收费请求。")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise VoiceError("Fish 查询超时；原请求结果可能未知，未重新合成。")
            time.sleep(min(self.interval, remaining))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise VoiceError("Fish 查询超时；原请求结果可能未知，未重新合成。")
            result = self.json("/easel/fish/v1/tasks/" + task_id, timeout=min(self.timeout, remaining))
            if result.get("task_id") != task_id:
                raise VoiceError("Fish 查询返回了不一致的公共任务 ID。")
        canonical = "/v1/tasks/" + task_id + "/artifacts/audio/content"
        if (result.get("artifact_expired") is True or type(result.get("expires_at")) is not int
                or result["expires_at"] <= time.time()):
            raise VoiceError("Fish 音频 artifact 已过期或无法验证有效期；未重新合成。")
        if (result.get("content_url") not in (canonical, self.endpoint(canonical))
                or result.get("mime_type") != MIME[format] or type(result.get("size")) is not int
                or not 0 < result["size"] <= MAX_AUDIO):
            raise VoiceError("Fish 完成结果的 artifact 路径、MIME 或大小不合法。")
        return self.download(self.endpoint(canonical), out, expected_size=result["size"], expected_mime=MIME[format])


def enroll(provider, a, *, require_env, fail):
    client = None
    try:
        client = Client(require_env("NEW_API_BASE_URL"), require_env("NEW_API_KEY"))
        if provider == "minimax":
            return client.minimax_enroll(a)
        if provider == "dashscope":
            return client.dashscope_enroll(a)
        raise VoiceError("Fish 网关模式直接 clone：提供参考音与转写或管理员允许的预置 reference_id。")
    except VoiceError as error:
        recovery = (f" 已保留公共恢复 ID：{client.recovery_id}；继续命令：enroll --provider {provider} --resume-id {client.recovery_id}。"
                    if client is not None and client.recovery_id else "")
        fail(str(error) + recovery)


def clone(provider, a, out, *, require_env, fail, emotion=""):
    try:
        client = Client(require_env("NEW_API_BASE_URL"), require_env("NEW_API_KEY"))
        if provider == "minimax":
            return client.minimax_clone(a, out, emotion)
        if provider == "dashscope":
            return client.dashscope_clone(a, out, emotion)
        if provider == "fish-audio":
            return client.fish_clone(a, out)
        raise VoiceError("该 provider 不使用私有 New API 语音包装。")
    except VoiceError as error:
        fail(str(error))
