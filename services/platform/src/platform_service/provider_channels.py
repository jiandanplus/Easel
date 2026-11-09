"""The six interface pages the desktop has, shared by the console and the central service."""
import ipaddress
import re
from urllib.parse import urlsplit

from .errors import PlatformError
from .execution_config import ExecutionProvider
from .media_config import MediaProvider, origin

PROVIDER_VERSION = "console-1"

CHANNELS = {
    "chat": {"kind": "execution", "capabilities": ("text",)},
    "transcribe": {"kind": "media", "adapter": "openai_transcriptions_v1", "capabilities": ("audio.transcribe",)},
    "speech": {"kind": "media", "adapter": "openai_speech_v1", "capabilities": ("audio.speech",)},
    "image": {"kind": "media", "adapter": "openai_images_v1", "capabilities": ("image.generate", "image.edit")},
    "video": {"kind": "media", "adapter": "newapi_videos_v1", "capabilities": ("video.generate",)},
    "music": {"kind": "media", "adapter": "newapi_suno_v1", "capabilities": ("music.generate",)},
}
CAPABILITY_CHANNEL = {
    capability: name for name, spec in CHANNELS.items() for capability in spec["capabilities"]
} | {"voice.enroll": "speech", "voice.synthesize": "speech", "image.variation": "image"}


def provider_id(channel):
    return "console-" + channel


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


def clean_key(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 4096 or not value.isascii() or any(not 33 <= ord(c) <= 126 for c in value):
        raise invalid()
    return value


def _origins(values, required):
    if not isinstance(values, list) or len(values) > 8 or len(set(values)) != len(values) or (required and not values):
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


def clean_options(channel, value):
    if not isinstance(value, dict):
        raise invalid()
    if channel == "speech":
        voices = value.get("voices")
        if set(value) != {"voices"} or not isinstance(voices, list) or not 1 <= len(voices) <= 64 or len(set(voices)) != len(voices):
            raise invalid()
        if any(not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", v, re.ASCII) for v in voices):
            raise invalid()
        return {"voices": voices}
    if channel == "image":
        if set(value) != {"mode", "download_origins"} or value["mode"] not in ("gateway", "openai"):
            raise invalid()
        return {"mode": value["mode"], "download_origins": _origins(value["download_origins"], False)}
    if channel == "music":
        prefixes = value.get("download_path_prefixes")
        if set(value) != {"download_origins", "download_path_prefixes"} or not isinstance(prefixes, list) or not 1 <= len(prefixes) <= 16:
            raise invalid()
        if any(not isinstance(p, str) or not re.fullmatch(r"/(?:[A-Za-z0-9_.-]+/)+", p) or len(p) > 512 or {".", ".."} & set(p.split("/")) for p in prefixes):
            raise invalid()
        return {"download_origins": _origins(value["download_origins"], True), "download_path_prefixes": prefixes}
    if value:
        raise invalid()
    return {}


def channel_capabilities(channel, options):
    if channel == "image" and options.get("mode") == "gateway":
        return ("image.generate",)  # The gateway response profile only covers generation.
    return CHANNELS[channel]["capabilities"]


def build_provider(channel, base_url, secret, options, *, preflight=True):
    spec = CHANNELS[channel]
    if spec["kind"] == "execution":
        return ExecutionProvider(version=PROVIDER_VERSION, endpoint=base_url + "/responses", secret=secret, models_url=base_url + "/models",
                                 preflight_models=preflight, managed_models=True)
    extra = {}
    if channel == "speech":
        extra["audio_profile"] = {"voices": list(options["voices"])}
    elif channel == "transcribe":
        extra["audio_profile"] = {}
    elif channel == "image":
        extra["download_origins"] = tuple(options["download_origins"])
        if options["mode"] == "gateway":
            extra["response_profile"] = "newapi_images_v1"
    elif channel == "music":
        extra["download_origins"] = tuple(options["download_origins"])
        extra["download_path_prefixes"] = tuple(options["download_path_prefixes"])
    return MediaProvider(version=PROVIDER_VERSION, base_url=base_url, secret=secret, adapter=spec["adapter"],
                         capabilities=channel_capabilities(channel, options), preflight_models=preflight, managed_models=True, **extra)


def apply_connection(provider, base_url, secret):
    """Point a server-file provider at the address and key saved in the console."""
    import dataclasses
    if isinstance(provider, ExecutionProvider):
        return dataclasses.replace(provider, endpoint=base_url + "/responses", models_url=base_url + "/models", secret=secret)
    return dataclasses.replace(provider, base_url=base_url, secret=secret)


def channel_of(provider):
    """Which page a server-file provider belongs to."""
    if isinstance(provider, ExecutionProvider):
        return "chat"
    return next((CAPABILITY_CHANNEL[c] for c in provider.capabilities if c in CAPABILITY_CHANNEL), None)
