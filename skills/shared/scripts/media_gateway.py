"""Pure shared contract for opt-in media gateway settings.

Persistent NEW_API_* values never overwrite the saved custom provider fields.
Execution derives a fresh environment for one capability at a time.
"""
from __future__ import annotations

from collections.abc import Mapping
import re
from urllib.parse import urlsplit


def require_media_available():
    import os
    if os.environ.get("EASEL_SUBSCRIPTION_BACKEND") == "central":
        raise RuntimeError("中央订阅的图片、视频、音乐和语音只能在内置 Pi 中使用。请在设置里把引擎切换为 Pi，或把接口来源改为自定义。")


GATEWAY_SPEC = {
    "image": {"modeEnv": "IMAGE_API_MODE", "customMode": "custom", "modelEnv": "NEW_API_IMAGE_MODEL", "providers": ("openai",)},
    "video": {"modeEnv": "VIDEO_API_MODE", "customMode": "custom", "modelEnv": "NEW_API_VIDEO_MODEL", "providers": ("openai-compatible",)},
    "music": {"modeEnv": "MUSIC_API_MODE", "customMode": "legacy", "modelEnv": "NEW_API_MUSIC_MODEL", "providers": ("dashscope", "suno-compatible")},
    "voice": {"modeEnv": "VOICE_API_MODE", "customMode": "custom", "modelEnv": "NEW_API_VOICE_MODEL", "providers": ("dashscope", "minimax", "fish-audio", "openai-compatible", "gemini")},
}
GATEWAY_ENV_KEYS = (
    "NEW_API_BASE_URL", "NEW_API_KEY", "IMAGE_API_MODE", "VIDEO_API_MODE", "MUSIC_API_MODE", "VOICE_API_MODE",
    "NEW_API_IMAGE_MODEL", "NEW_API_VIDEO_MODEL", "NEW_API_VIDEO_PROFILE", "NEW_API_MUSIC_PROVIDER", "NEW_API_MUSIC_MODEL",
    "NEW_API_VOICE_PROVIDER", "NEW_API_VOICE_MODEL", "NEW_API_VOICE_ID", "GEMINI_TTS_API",
)
VIDEO_PROFILES = ("standard", "veo", "kling", "wan26-t2v", "seedance1")
PRIVATE_VOICE_DEFAULTS = {"dashscope": "cosyvoice-v2", "minimax": "easel-minimax-speech-2.8-hd", "fish-audio": "easel-fish-s2.1-pro"}
_PLACEHOLDER = re.compile(r"replace_me|your[-_]?api[-_]?key|xxx|^\.{3}$|^<.*>$", re.I)


def _value(env, name, default=""):
    return str(env.get(name, "") or "").strip() or default


def gateway_enabled(group, env):
    return _value(env, GATEWAY_SPEC[group]["modeEnv"]).lower() == "new-api"


def gateway_managed(group, env):
    """Old hand-written adapter configurations without shared fields still work."""
    selection = (GATEWAY_SPEC[group]["modelEnv"], *{
        "music": ("NEW_API_MUSIC_PROVIDER",), "voice": ("NEW_API_VOICE_PROVIDER", "NEW_API_VOICE_ID"),
        "video": ("NEW_API_VIDEO_PROFILE",),
    }.get(group, ()))
    return gateway_enabled(group, env) and any(key in env for key in selection)


def refresh_gateway_environment(env, saved):
    """Saved shared settings override stale daemon env without deriving vendor aliases."""
    result = dict(env)
    saved_form = any(key.startswith("NEW_API_") and key not in ("NEW_API_BASE_URL", "NEW_API_KEY") for key in saved)
    if saved_form:
        # Missing selections must stay absent: an explicit empty selection is a
        # managed route, while an absent selection may be an old manual adapter.
        for key in GATEWAY_ENV_KEYS:
            result.pop(key, None)
        result.update(NEW_API_BASE_URL="", NEW_API_KEY="")
    result.update({key: saved[key] for key in GATEWAY_ENV_KEYS if key in saved})
    return result


def _provider(group, env):
    return _value(env, "NEW_API_" + group.upper() + "_PROVIDER", GATEWAY_SPEC[group]["providers"][0])


def _model(group, env):
    model = _value(env, GATEWAY_SPEC[group]["modelEnv"])
    if not model and group == "voice":
        model = PRIVATE_VOICE_DEFAULTS.get(_provider(group, env), "")
    if not model and group == "music" and _provider(group, env) == "dashscope":
        model = "fun-music-v1"
    return model


def _validate_url(value):
    try:
        parsed = urlsplit(value)
        parsed.port
        if (parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or any(ord(char) < 33 for char in value)
                or (parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"))):
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("NEW_API_BASE_URL 必须为无凭据、查询参数的 HTTPS 网关根地址（本地回环可使用 HTTP）。") from None


def _validate_fields(env):
    for group, spec in GATEWAY_SPEC.items():
        mode = _value(env, spec["modeEnv"], spec["customMode"])
        allowed = ("legacy", "new-api") if group == "music" else (("custom", "legacy", "new-api") if group == "video" else ("custom", "new-api"))
        if mode not in allowed:
            raise ValueError(spec["modeEnv"] + " 模式不受支持。")
        if group in ("music", "voice") and _provider(group, env) not in spec["providers"]:
            raise ValueError("NEW_API_" + group.upper() + "_PROVIDER 不受支持。")
    if _value(env, "NEW_API_VIDEO_PROFILE", "standard") not in VIDEO_PROFILES:
        raise ValueError("NEW_API_VIDEO_PROFILE 不受支持。")
    if _value(env, "GEMINI_TTS_API", "generate-content") not in ("generate-content", "interactions"):
        raise ValueError("GEMINI_TTS_API 仅支持 generate-content 或 interactions。")
    for name in GATEWAY_ENV_KEYS:
        raw = str(env.get(name, "") or "")
        if any(ord(char) < 32 or ord(char) == 127 for char in raw):
            raise ValueError(name + " 不能包含控制字符。")
    base = _value(env, "NEW_API_BASE_URL")
    if base:
        _validate_url(base)


def gateway_readiness(group, env):
    enabled = gateway_enabled(group, env)
    missing = []
    if enabled:
        for key in ("NEW_API_BASE_URL", "NEW_API_KEY"):
            if not _value(env, key) or _PLACEHOLDER.search(_value(env, key)):
                missing.append(key)
        if group in ("image", "video") or (group == "voice" and _provider(group, env) in ("openai-compatible", "gemini")):
            if not _model(group, env):
                missing.append(GATEWAY_SPEC[group]["modelEnv"])
        try:
            _validate_group(group, env)
        except ValueError:
            if not missing:
                missing.append("gateway_configuration")
    return {"enabled": enabled, "configured": enabled and not missing, "missing": missing,
            "provider": _provider(group, env), "model": _model(group, env)}


def _validate_group(group, env):
    # Resolve only this capability: an incomplete unrelated capability cannot
    # prevent using a complete route or overwrite shared vendor variables.
    own_keys = {"NEW_API_BASE_URL", "NEW_API_KEY", GATEWAY_SPEC[group]["modeEnv"]}
    own_keys.update(key for key in GATEWAY_ENV_KEYS if key.startswith("NEW_API_" + group.upper() + "_"))
    # Unified Gemini always uses Generate Content. The direct-provider
    # GEMINI_TTS_API value remains saved independently and cannot block it.
    subset = {key: value for key, value in env.items() if key in own_keys}
    _validate_fields(subset)
    for key in ("NEW_API_BASE_URL", "NEW_API_KEY"):
        if not _value(env, key) or _PLACEHOLDER.search(_value(env, key)):
            raise ValueError("启用统一网关需要配置 " + key + "。")
    if (group in ("image", "video") or (group == "voice" and _provider(group, env) in ("openai-compatible", "gemini"))) and not _model(group, env):
        raise ValueError("启用该能力需要显式配置 " + GATEWAY_SPEC[group]["modelEnv"] + "。")


def validate_gateway_env(env, previous=None):
    _validate_fields(env)
    for group in GATEWAY_SPEC:
        preserved_legacy = (previous is not None and gateway_enabled(group, previous)
                            and not gateway_managed(group, previous) and not gateway_managed(group, env))
        if gateway_enabled(group, env) and not preserved_legacy:
            _validate_group(group, env)
    if previous:
        oldbase = _value(previous, "NEW_API_BASE_URL").rstrip("/")
        newbase = _value(env, "NEW_API_BASE_URL").rstrip("/")
        oldkey = _value(previous, "NEW_API_KEY")
        newkey = _value(env, "NEW_API_KEY")
        if oldbase and oldkey and oldbase != newbase and newkey and newkey == oldkey:
            raise ValueError("更换网关地址后必须重新填写新的业务密钥 key。")


def gateway_settings(env):
    key = _value(env, "NEW_API_KEY")
    modes = {group: "new-api" if gateway_enabled(group, env) else "custom" for group in GATEWAY_SPEC}
    configured = {group: gateway_managed(group, env) and gateway_readiness(group, env)["configured"] for group in GATEWAY_SPEC}
    return {
        "baseUrl": _value(env, "NEW_API_BASE_URL"), "keyConfigured": bool(key), "keyMasked": "********" if key else "",
        "modes": modes, "image": {"model": _value(env, "NEW_API_IMAGE_MODEL")},
        "video": {"model": _value(env, "NEW_API_VIDEO_MODEL"), "profile": _value(env, "NEW_API_VIDEO_PROFILE", "standard")},
        "music": {"provider": _provider("music", env), "model": _value(env, "NEW_API_MUSIC_MODEL")},
        "voice": {"provider": _provider("voice", env), "model": _value(env, "NEW_API_VOICE_MODEL"), "voiceId": _value(env, "NEW_API_VOICE_ID")},
        "geminiTtsApi": _value(env, "GEMINI_TTS_API", "generate-content"), "configured": configured,
        "managed": {group: gateway_managed(group, env) for group in GATEWAY_SPEC},
    }


def gateway_updates(payload, current):
    if not isinstance(payload, Mapping):
        raise ValueError("统一媒体网关设置必须为对象。")
    def text(obj, key):
        if not isinstance(obj, Mapping) or key not in obj or not isinstance(obj[key], str):
            raise ValueError("统一媒体网关设置缺少有效字段 " + key + "。")
        value = obj[key]
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError(key + " 不能包含控制字符。")
        return value.strip()
    adopt = payload.get("adopt", [])
    if not isinstance(adopt, list) or any(not isinstance(group, str) or group not in GATEWAY_SPEC for group in adopt):
        raise ValueError("adopt 必须为有效能力名称列表。")
    stored = gateway_settings(current)
    update = {"NEW_API_BASE_URL": text(payload, "baseUrl").rstrip("/")}
    modes = payload.get("modes")
    for group, spec in GATEWAY_SPEC.items():
        mode = text(modes, group)
        if mode not in ("custom", "new-api"):
            raise ValueError("能力模式仅支持 custom 或 new-api。")
        update[spec["modeEnv"]] = spec["customMode"] if mode == "custom" else mode
        update[spec["modelEnv"]] = text(payload.get(group), "model")
    update["NEW_API_VIDEO_PROFILE"] = text(payload.get("video"), "profile")
    update["NEW_API_MUSIC_PROVIDER"] = text(payload.get("music"), "provider")
    update["NEW_API_VOICE_PROVIDER"] = text(payload.get("voice"), "provider")
    update["NEW_API_VOICE_ID"] = text(payload.get("voice"), "voiceId")
    update["GEMINI_TTS_API"] = text(payload, "geminiTtsApi")
    for group, spec in GATEWAY_SPEC.items():
        if (group not in adopt and gateway_enabled(group, current) and not gateway_managed(group, current)
                and text(modes, group) == "new-api"
                and all(text(payload.get(group), name) == value for name, value in stored[group].items())):
            # Saving another capability does not silently adopt a pre-form adapter.
            for key in tuple(update):
                if key == spec["modeEnv"] or key.startswith("NEW_API_" + group.upper() + "_"):
                    update.pop(key)
    if "clearKey" in payload and not isinstance(payload["clearKey"], bool):
        raise ValueError("clearKey 必须为布尔值。")
    entered_key = text(payload, "key") if "key" in payload else ""
    if payload.get("clearKey"):
        if entered_key or any(text(modes, group) == "new-api" for group in GATEWAY_SPEC):
            raise ValueError("清除密钥前必须将全部能力切回自定义，且不能同时填写密钥。")
        update["NEW_API_KEY"] = ""
    else:
        update["NEW_API_KEY"] = entered_key or _value(current, "NEW_API_KEY")
    # The dedicated form can prove re-entry even when a user re-enters the
    # same valid business key; generic env writes have no such signal.
    validate_gateway_env({**current, **update}, previous={**current, "NEW_API_KEY": ""} if entered_key else current)
    return update


def resolve_media_environment(group, env):
    """Return a new environment; never overlay other capabilities globally."""
    effective = dict(env)
    if not gateway_managed(group, env):
        return effective
    _validate_group(group, env)
    root, key, model = _value(env, "NEW_API_BASE_URL").rstrip("/"), _value(env, "NEW_API_KEY"), _model(group, env)
    provider = _provider(group, env)
    if group == "image":
        effective.update(IMG_BASE_URL=root + "/v1", IMG_API_KEY=key, IMG_MODEL=model,
                         IMG_API_KEY_HEADER="", IMG_API_VERSION="", IMG_NO_PROXY="")
    elif group == "video":
        effective.update(VIDEO_PROVIDER="openai-compatible", VIDEO_BASE_URL=root + "/v1", VIDEO_API_KEY=key,
                         VIDEO_MODEL=model, VIDEO_NEWAPI_PROFILE=_value(env, "NEW_API_VIDEO_PROFILE", "standard"))
    elif group == "music":
        effective["MUSIC_PROVIDER"] = provider
        prefix = "DASHSCOPE" if provider == "dashscope" else "MUSIC"
        effective[prefix + "_BASE_URL"] = root
        effective[prefix + "_API_KEY"] = key
        effective["DASHSCOPE_MUSIC_MODEL" if provider == "dashscope" else "MUSIC_MODEL"] = model
    elif group == "voice":
        voice = _value(env, "NEW_API_VOICE_ID") or {"openai-compatible": "alloy", "gemini": "Kore"}.get(provider, "")
        effective.update(VOICE_PROVIDER=provider, VOICE_NARRATOR_VOICE_ID=voice)
        prefix = {"dashscope": "DASHSCOPE", "minimax": "MINIMAX", "fish-audio": "FISH", "openai-compatible": "VOICE", "gemini": "GEMINI"}[provider]
        effective[prefix + "_API_KEY"] = key
        effective[prefix + "_BASE_URL"] = root + {"openai-compatible": "/v1", "gemini": "/v1beta"}.get(provider, "")
        effective[{"dashscope": "DASHSCOPE_TTS_MODEL", "minimax": "MINIMAX_MODEL", "fish-audio": "FISH_MODEL", "openai-compatible": "VOICE_MODEL", "gemini": "GEMINI_TTS_MODEL"}[provider]] = model
        if provider == "gemini":
            effective.update(GEMINI_TTS_API="generate-content", GEMINI_VOICE=voice)
        if provider == "openai-compatible":
            effective["VOICE_INSTRUCT_MODE"] = "field"
    return effective
