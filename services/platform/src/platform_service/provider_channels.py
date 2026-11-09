"""The desktop's six interface pages and the supplier platforms each one can use.

Shared by the console and the central service.
"""
import dataclasses
import ipaddress
import re
from urllib.parse import urlsplit

from .errors import PlatformError
from .execution_config import ExecutionProvider
from .media_config import MediaProvider, origin
from .voice_catalogs import VOICE_ID

PROVIDER_VERSION = "console-1"
GATEWAY = "gateway"

_VOICES = {"key": "voices", "label": "可选音色", "required": True, "list": True, "placeholder": "一行一个或用逗号分隔"}
_VOICES_LISTED = {**_VOICES, "required": False, "lines": True, "placeholder": "可以留空，保存后点“拉取音色”；也可以一行填一个"}
_ORIGINS = {"key": "download_origins", "label": "结果下载域名", "required": False, "list": True, "placeholder": "接口返回下载链接时填写，例如 https://cdn.example.com"}
_ORIGINS_REQUIRED = {**_ORIGINS, "required": True, "placeholder": "例如 https://cdn.example.com"}
_PREFIXES = {"key": "download_path_prefixes", "label": "下载路径前缀", "required": True, "list": True, "placeholder": "例如 /audio/"}

CHANNELS = {
    "chat": {"kind": "execution", "capabilities": ("text",)},
    "transcribe": {"kind": "media", "capabilities": ("audio.transcribe",)},
    "speech": {"kind": "media", "capabilities": ("audio.speech",)},
    "image": {"kind": "media", "capabilities": ("image.generate", "image.edit")},
    "video": {"kind": "media", "capabilities": ("video.generate",)},
    "music": {"kind": "media", "capabilities": ("music.generate",)},
}
CAPABILITY_CHANNEL = {
    capability: name for name, spec in CHANNELS.items() for capability in spec["capabilities"]
} | {"voice.enroll": "speech", "voice.synthesize": "speech", "image.variation": "image"}

# Speech engines come from each supplier's documentation: only their voices can be listed through an API.
PLATFORMS = {
    "chat": {
        GATEWAY: {"label": "OpenAI Responses / New API", "url": "", "protocol": "responses", "probe": "bearer", "hint": "地址要支持 OpenAI Responses 接口（/responses）。"},
        "openai": {"label": "OpenAI 兼容（DeepSeek、中转站等）", "url": "https://api.deepseek.com/v1", "protocol": "chat", "probe": "bearer", "hint": "调用 /chat/completions，由中央服务转换成统一格式。"},
        "anthropic": {"label": "Anthropic", "url": "https://api.anthropic.com/v1", "protocol": "anthropic", "probe": "anthropic", "hint": "调用 /messages，由中央服务转换成统一格式。"},
        "relay": {"label": "中转站（Anthropic 协议）", "url": "", "protocol": "anthropic", "probe": "anthropic", "hint": "调用 /messages。"},
        "custom": {"label": "自定义（OpenAI 兼容）", "url": "", "protocol": "chat", "probe": "bearer", "hint": "需要第二家 OpenAI 兼容接口时用这一项。"},
    },
    "transcribe": {
        GATEWAY: {"label": "OpenAI 兼容 / New API", "url": "", "adapter": "openai_transcriptions_v1", "probe": "bearer", "hint": "调用 /audio/transcriptions。"},
        "siliconflow": {"label": "硅基流动", "url": "https://api.siliconflow.cn/v1", "adapter": "openai_transcriptions_v1", "probe": "bearer", "hint": "常用模型 FunAudioLLM/SenseVoiceSmall。"},
    },
    "speech": {
        GATEWAY: {"label": "OpenAI 兼容 / New API", "url": "", "adapter": "openai_speech_v1", "probe": "bearer", "fields": [_VOICES], "hint": "调用 /audio/speech。"},
        "dashscope": {"label": "阿里 CosyVoice", "url": "https://dashscope.aliyuncs.com", "adapter": "dashscope_speech_v1", "clone_adapter": "newapi_dashscope_voice_v1", "probe": None, "capabilities": ("audio.speech", "voice.synthesize"), "voice_list": "dashscope", "known_models": ("cosyvoice-v3.5-plus", "cosyvoice-v3.5-flash", "cosyvoice-v3-plus", "cosyvoice-v3-flash", "cosyvoice-v2"), "fields": [_VOICES_LISTED], "hint": "音色要和模型版本匹配，例如 cosyvoice-v2 用 longxiaochun_v2。支持声音复刻。"},
        "minimax": {"label": "MiniMax", "url": "https://api.minimax.chat", "adapter": "minimax_speech_v1", "clone_adapter": "newapi_minimax_voice_v1", "probe": None, "capabilities": ("audio.speech", "voice.synthesize"), "voice_list": "minimax", "known_models": ("speech-2.8-hd", "speech-2.8-turbo", "speech-2.6-hd", "speech-2.6-turbo", "speech-02-hd", "speech-02-turbo"), "fields": [_VOICES_LISTED, {"key": "group_id", "label": "Group ID", "required": False, "list": False, "placeholder": "账号需要时填写"}], "hint": "调用 /v1/t2a_v2。支持声音复刻。"},
        "fish": {"label": "Fish Audio", "url": "https://api.fish.audio", "adapter": "fish_speech_v1", "probe": None, "voice_list": "fish", "voice_search": True, "known_models": ("s2.1-pro", "s2.1-pro-free", "s2-pro", "drama-3"), "fields": [{**_VOICES_LISTED, "label": "声音模型 ID"}], "hint": "调用 /v1/tts。可以直接上传参考音频。"},
        "gemini": {"label": "Google Gemini TTS", "url": "https://generativelanguage.googleapis.com/v1beta", "adapter": "gemini_speech_v1", "probe": "gemini", "known_models": ("gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts"), "fields": [{**_VOICES, "placeholder": "例如 Kore"}], "hint": "只输出 WAV。"},
    },
    "image": {
        GATEWAY: {"label": "New API 网关（只生图）", "url": "", "adapter": "openai_images_v1", "probe": "bearer", "capabilities": ("image.generate",), "response_profile": "newapi_images_v1", "fields": [_ORIGINS], "hint": "调用 /images/generations。"},
        "openai": {"label": "OpenAI 兼容 / apimart / 小红书 MaaS（生图和改图）", "url": "https://api.openai.com/v1", "adapter": "openai_images_v1", "probe": "bearer",
                   "fields": [_ORIGINS, {"key": "auth_header", "label": "鉴权头名", "required": False, "list": False, "placeholder": "默认 Authorization，小红书 MaaS 填 api-key"},
                              {"key": "api_version", "label": "api-version", "required": False, "list": False, "placeholder": "Azure 等需要时填写"}], "hint": "调用 /images/generations 和 /images/edits。"},
    },
    "video": {
        GATEWAY: {"label": "OpenAI 兼容 / New API", "url": "", "adapter": "newapi_videos_v1", "probe": "bearer", "hint": "调用 /videos。"},
        "dashscope": {"label": "阿里通义万相 Wan", "url": "https://dashscope.aliyuncs.com/api/v1", "adapter": "dashscope_video_v1", "probe": None, "fields": [_ORIGINS], "hint": "结果在阿里云存储上，一般不用填下载域名。"},
        "ark": {"label": "火山引擎 Seedance", "url": "https://ark.cn-beijing.volces.com/api/v3", "adapter": "ark_video_v1", "probe": "bearer", "fields": [_ORIGINS], "hint": "结果在火山存储上，一般不用填下载域名。"},
        "kling": {"label": "快手可灵", "url": "https://api.klingai.com", "adapter": "kling_video_v1", "probe": None, "key_label": "Access Key", "second_key": "Secret Key", "fields": [_ORIGINS], "hint": "时长只支持 5 秒或 10 秒。"},
        "xhs": {"label": "小红书 MaaS（happyhorse）", "url": "https://maas.devops.xiaohongshu.com/openai/openai/qwen/v1", "adapter": "xhs_video_v1", "probe": None, "key_label": "MaaS api-key", "fields": [_ORIGINS], "hint": "这是内网地址，只有服务器能访问小红书内网时才能用。"},
        "agnes": {"label": "Agnes Video", "url": "https://apihub.agnes-ai.com/v1", "adapter": "agnes_video_v1", "probe": None, "fields": [_ORIGINS, {"key": "poll_url", "label": "轮询地址", "required": False, "list": False, "placeholder": "默认 https://apihub.agnes-ai.com/agnesapi"}], "hint": "时长支持 4 到 12 秒。"},
    },
    "music": {
        GATEWAY: {"label": "New API 网关（Suno）", "url": "", "adapter": "newapi_suno_v1", "probe": "bearer", "fields": [_ORIGINS_REQUIRED, _PREFIXES], "hint": "调用 /suno/submit/music。"},
        "dashscope": {"label": "阿里 DashScope 音乐", "url": "https://dashscope.aliyuncs.com/api/v1", "adapter": "dashscope_music_v1", "probe": None, "fields": [_ORIGINS], "hint": "结果在阿里云存储上，一般不用填下载域名。"},
        "suno": {"label": "Suno 类第三方 API", "url": "", "adapter": "suno_direct_v1", "probe": None, "fields": [_ORIGINS], "hint": "调用 /generate 和 /feed/{id}。结果不在 suno 域名上时要填下载域名。"},
    },
}


def spec_of(channel, platform):
    try:
        return PLATFORMS[channel][platform]
    except KeyError:
        raise invalid() from None


def provider_id(channel, platform=GATEWAY, account=""):
    """The first connection of a platform keeps its short id; further ones add their account."""
    base = "console-" + channel if platform == GATEWAY and not account else "console-" + channel + "-" + platform
    return base + "-" + account if account else base


def seal_scope(channel, platform=GATEWAY, account=""):
    """What a key is sealed for. A gateway key keeps the page name alone."""
    base = channel if platform == GATEWAY and not account else channel + "/" + platform
    return base + "/" + account if account else base


def public_platforms(channel):
    result = []
    for platform, spec in PLATFORMS[channel].items():
        result.append({"id": platform, "label": spec["label"], "default_url": spec["url"], "hint": spec["hint"],
                       "key_label": spec.get("key_label", "API Key"), "second_key": spec.get("second_key"),
                       "fields": spec.get("fields", []), "lists_models": spec["probe"] is not None or "known_models" in spec, "known_models": list(spec.get("known_models", ())),
                       "lists_voices": "voice_list" in spec, "voice_search": bool(spec.get("voice_search"))})
    return result


def invalid():
    return PlatformError(422, "console_provider_invalid")


def clean_base_url(value):
    if not isinstance(value, str) or not 8 <= len(value) <= 512 or any(ord(c) < 33 or ord(c) == 127 for c in value) or "\\" in value:
        raise invalid()
    value = value.rstrip("/")
    try:
        parsed = urlsplit(value)
        parsed.port
        address = None
        try:
            address = ipaddress.ip_address(parsed.hostname or "")
        except ValueError:
            pass
    except ValueError:
        raise invalid() from None
    local_test = parsed.scheme == "http" and address is not None and address.is_loopback
    if (not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment
            or not (parsed.scheme == "https" or local_test)
            # Keep admin-typed addresses off internal services such as cloud metadata.
            or (parsed.scheme == "https" and address is not None and not address.is_global)):
        raise invalid()
    return value


def clean_key(value, *, parts=1):
    if not isinstance(value, str) or not 1 <= len(value) <= 4096 or not value.isascii() or any(not 33 <= ord(c) <= 126 for c in value):
        raise invalid()
    if len(value.split("|")) != parts or not all(value.split("|")):
        raise invalid()
    return value


def _origins(values):
    if not isinstance(values, list) or len(values) > 8 or len(set(values)) != len(values):
        raise invalid()
    for value in values:
        try:
            origin(value, mode="live", root=True)
        except ValueError:
            try:
                origin(value, mode="test", root=True)
            except ValueError:
                raise invalid() from None
    return values


def _voices(values):
    if (not isinstance(values, list) or len(values) > 64 or len(set(values)) != len(values)
            or any(not isinstance(v, str) or not re.fullmatch(VOICE_ID, v, re.ASCII) for v in values)):
        raise invalid()
    return values


def _prefixes(values):
    if (not isinstance(values, list) or len(values) > 16
            or any(not isinstance(p, str) or not re.fullmatch(r"/(?:[A-Za-z0-9_.-]+/)+", p) or len(p) > 512 or {".", ".."} & set(p.split("/")) for p in values)):
        raise invalid()
    return values


def _group(value):
    if not isinstance(value, str) or (value and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value, re.ASCII)):
        raise invalid()
    return value


def _poll(value):
    if not isinstance(value, str):
        raise invalid()
    return clean_base_url(value) if value else value


def _header(value):
    if not isinstance(value, str) or (value and not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,63}", value, re.ASCII)):
        raise invalid()
    return value


def _version(value):
    if not isinstance(value, str) or (value and not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", value, re.ASCII)):
        raise invalid()
    return value


_CLEAN = {"voices": _voices, "download_origins": _origins, "download_path_prefixes": _prefixes, "group_id": _group, "poll_url": _poll, "auth_header": _header, "api_version": _version}


def clean_name(value):
    if not isinstance(value, str) or len(value.strip()) > 64 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise invalid()
    return value.strip()


def clean_options(channel, platform, value):
    spec, value = spec_of(channel, platform), dict(value) if isinstance(value, dict) else None
    fields = spec.get("fields", [])
    query = value.pop("voice_query", "") if value is not None and spec.get("voice_search") else ""
    if value is None or set(value) != {field["key"] for field in fields}:
        raise invalid()
    if not isinstance(query, str) or len(query.strip()) > 64 or any(ord(c) < 32 or ord(c) == 127 for c in query):
        raise invalid()
    result = {"voice_query": query.strip()} if query.strip() else {}
    for field in fields:
        item = _CLEAN[field["key"]](value[field["key"]])
        if field["required"] and not item:
            raise invalid()
        result[field["key"]] = item
    return result


def build_provider(channel, platform, base_url, secret, options, *, preflight=True):
    spec = spec_of(channel, platform)
    preflight = preflight and spec["probe"] == "bearer" and not options.get("auth_header")
    if CHANNELS[channel]["kind"] == "execution":
        path = {"responses": "/responses", "chat": "/chat/completions", "anthropic": "/messages"}[spec["protocol"]]
        return ExecutionProvider(version=PROVIDER_VERSION, endpoint=base_url + path, secret=secret, models_url=base_url + "/models",
                                 preflight_models=preflight, managed_models=True, protocol=spec["protocol"])
    extra = {"capabilities": spec.get("capabilities", CHANNELS[channel]["capabilities"])}
    if channel in ("speech", "transcribe"):
        extra["audio_profile"] = {"voices": list(options["voices"])} if channel == "speech" else {}
    if "download_origins" in options:
        extra["download_origins"] = tuple(options["download_origins"])
    if "download_path_prefixes" in options:
        extra["download_path_prefixes"] = tuple(options["download_path_prefixes"])
    if spec.get("response_profile"):
        extra["response_profile"] = spec["response_profile"]
    private = {key: options[key] for key in ("group_id", "poll_url", "auth_header", "api_version") if options.get(key)}
    if private:
        extra["platform_options"] = private
    return MediaProvider(version=PROVIDER_VERSION, base_url=base_url, secret=secret, adapter=spec["adapter"], preflight_models=preflight,
                         managed_models=True, client_correlation=True, **extra)


def clone_provider_id(channel, platform, account=""):
    return provider_id(channel, platform, account) + "-clone"


def build_clone_provider(channel, platform, base_url, secret, options):
    """Voice registration runs through the audited stages, under its own provider entry."""
    spec = spec_of(channel, platform)
    private = {key: options[key] for key in ("group_id",) if options.get(key)}
    return MediaProvider(version=PROVIDER_VERSION, base_url=base_url, secret=secret, adapter=spec["clone_adapter"], capabilities=("voice.enroll",), voice_profile={},
                         managed_models=True, client_correlation=True, **({"platform_options": private} if private else {}))


def apply_connection(provider, base_url, secret):
    """Point a server-file provider at the address and key saved in the console."""
    if isinstance(provider, ExecutionProvider):
        return dataclasses.replace(provider, endpoint=base_url + "/responses", models_url=base_url + "/models", secret=secret)
    return dataclasses.replace(provider, base_url=base_url, secret=secret)


def channel_of(provider):
    """Which page a server-file provider belongs to."""
    if isinstance(provider, ExecutionProvider):
        return "chat"
    return next((CAPABILITY_CHANNEL[c] for c in provider.capabilities if c in CAPABILITY_CHANNEL), None)
