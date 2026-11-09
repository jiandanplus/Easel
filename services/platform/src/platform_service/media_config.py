"""Owner-only explicit image provider bindings; absent configuration is closed."""
from dataclasses import dataclass, field
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit


ASYNC_CAPABILITIES = {"newapi_videos_v1": ("video.generate",), "newapi_suno_v1": ("music.generate",), "newapi_dashscope_music_v1": ("music.generate",), "newapi_incho_v1": ("music.generate",),
                      "dashscope_video_v1": ("video.generate",), "ark_video_v1": ("video.generate",), "kling_video_v1": ("video.generate",), "agnes_video_v1": ("video.generate",), "xhs_video_v1": ("video.generate",),
                      "dashscope_music_v1": ("music.generate",), "suno_direct_v1": ("music.generate",)}

SPEECH_ADAPTERS = ('openai_speech_v1', 'minimax_speech_v1', 'dashscope_speech_v1', 'fish_speech_v1', 'gemini_speech_v1')
AUDIO_CAPABILITIES = {'openai_speech_v1': ('audio.speech',), 'fish_speech_v1': ('audio.speech',), 'gemini_speech_v1': ('audio.speech',),
                      'minimax_speech_v1': ('audio.speech', 'voice.synthesize'), 'dashscope_speech_v1': ('audio.speech', 'voice.synthesize'), 'openai_transcriptions_v1': ('audio.transcribe',)}

VOICE_CAPABILITIES = {'newapi_minimax_voice_v1': ('voice.enroll','voice.synthesize'), 'newapi_dashscope_voice_v1': ('voice.enroll','voice.synthesize'), 'newapi_fish_voice_v1': ('audio.speech',)}
AV_ADAPTERS = {*ASYNC_CAPABILITIES, *AUDIO_CAPABILITIES, *VOICE_CAPABILITIES}

IMAGE_CAPABILITIES = ("image.generate", "image.edit", "image.variation")


def origin(value, *, mode, root=False, allow_query=False):
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 33 for c in value) or "\\" in value:
        raise ValueError("invalid_media_configuration")
    try:
        parsed = urlsplit(value)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        loopback = False
        try: loopback = ipaddress.ip_address(parsed.hostname or "").is_loopback
        except ValueError: pass
        if (not parsed.hostname or parsed.username is not None or parsed.password is not None
                or (parsed.query and not allow_query) or parsed.fragment or (root and parsed.path not in ("", "/"))
                or not (mode == "test" and parsed.scheme == "http" and loopback
                        or mode in ("live", "disabled") and parsed.scheme == "https")):
            raise ValueError
        return parsed.scheme, parsed.hostname.lower(), port
    except (ValueError, TypeError):
        raise ValueError("invalid_media_configuration") from None


@dataclass(frozen=True)
class MediaProvider:
    version: str
    base_url: str = field(repr=False)
    secret: str = field(repr=False)
    models: dict[str, str] = field(default_factory=dict)
    adapter: str = "openai_images_v1"
    protocol_version: str = "1"
    capabilities: tuple[str, ...] = ("image.generate", "image.edit")
    download_origins: tuple[str, ...] = ()
    legacy_variations: bool = False
    response_format: str | None = None
    response_profile: str | None = None
    download_path_prefixes: tuple[str,...] = ()
    audio_profile: dict | None = None
    voice_profile: dict | None = field(default=None, repr=False)
    preflight_models: bool = False
    managed_models: bool = False
    platform_options: dict | None = None
    client_correlation: bool = False


@dataclass(frozen=True)
class MediaSettings:
    mode: str = "disabled"
    providers: dict[str, MediaProvider] = field(default_factory=dict, repr=False)
    storage_root: Path | None = field(default=None, repr=False)
    control_root: Path = field(default=Path('/var/lib/easel-central-control'),repr=False)
    # "local": in-process media permits, no B2 control schema (default, see docs/decisions/0006).
    # "control": the B2 database control and cross-process write barrier.
    maintenance: str = "local"
    concurrency: int = 1
    timeout_seconds: int = 180
    heartbeat_seconds: int = 10
    recovery_seconds: int = 15
    worker_enabled: bool = True
    max_asset_bytes: int = 20 * 1024 * 1024
    max_response_bytes: int = 32 * 1024 * 1024
    max_pixels: int = 16 * 1024 * 1024
    av_tools: object | None = field(default=None, repr=False)
    download_concurrency: int = 1
    worker_wall_seconds: int = 120

    def __post_init__(self):
        if not isinstance(self.control_root,Path) or not self.control_root.is_absolute() or '..' in self.control_root.parts:
            raise ValueError("invalid_media_configuration")
        if self.mode not in ("disabled", "test", "live") or not isinstance(self.providers, dict):
            raise ValueError("invalid_media_configuration")
        if self.maintenance not in ("local", "control"):
            raise ValueError("invalid_media_configuration")
        if type(self.worker_enabled) is not bool:
            raise ValueError("invalid_media_configuration")
        for value, maximum in ((self.concurrency, 4), (self.download_concurrency, 2), (self.worker_wall_seconds, 300), (self.timeout_seconds, 600), (self.heartbeat_seconds, 30),
                               (self.recovery_seconds, 60), (self.max_asset_bytes, 64 * 1024 * 1024),
                               (self.max_response_bytes, 128 * 1024 * 1024), (self.max_pixels, 32 * 1024 * 1024)):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError("invalid_media_configuration")
        if self.mode != "disabled" and (not self.providers or self.storage_root is None):
            raise ValueError("invalid_media_configuration")
        if self.storage_root is not None:
            path = Path(self.storage_root)
            if not path.is_absolute() or path.absolute() != path.resolve():
                raise ValueError("invalid_media_configuration")
            object.__setattr__(self, "storage_root", path)
        if self.av_tools is not None:
            from .media.av_artifacts import AVTools
            if not isinstance(self.av_tools, AVTools): raise ValueError("invalid_media_configuration")
            try: self.av_tools.paths()
            except Exception: raise ValueError("invalid_media_configuration") from None
        models = set()
        for name, provider in self.providers.items():
            if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name)
                    or not isinstance(provider, MediaProvider) or provider.adapter not in ("openai_images_v1", *ASYNC_CAPABILITIES, *AUDIO_CAPABILITIES, *VOICE_CAPABILITIES)
                    or provider.protocol_version != "1" or type(provider.legacy_variations) is not bool
                    or not isinstance(provider.version, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", provider.version)
                    or not isinstance(provider.secret, str) or not 1 <= len(provider.secret) <= 4096
                    or not provider.secret.isascii() or any(not 33 <= ord(c) <= 126 for c in provider.secret)
                    or provider.response_format not in (None, "b64_json", "url")
                    or provider.response_profile not in (None, "newapi_images_v1")):
                raise ValueError("invalid_media_configuration")
            if provider.adapter in AUDIO_CAPABILITIES:
                try:
                    profile=audio_profile(provider)
                    if provider.adapter in SPEECH_ADAPTERS and not profile.voices:raise ValueError
                except Exception: raise ValueError("invalid_media_configuration") from None
                if provider.response_profile is not None or provider.response_format is not None or provider.legacy_variations:
                    raise ValueError("invalid_media_configuration")
            elif provider.audio_profile is not None:
                raise ValueError("invalid_media_configuration")
            if provider.adapter in VOICE_CAPABILITIES:
                try:
                    profile=voice_profile(provider)
                    from .media.adapters.newapi_voice import MiniMaxVoiceAdapter,DashScopeVoiceAdapter,FishVoiceAdapter
                    targets={'newapi_minimax_voice_v1':MiniMaxVoiceAdapter.targets,'newapi_dashscope_voice_v1':DashScopeVoiceAdapter.targets,'newapi_fish_voice_v1':FishVoiceAdapter.targets}[provider.adapter]
                    if not provider.client_correlation and (not profile.target_models or not set(profile.target_models)<=targets or any(value not in profile.target_models for value in provider.models.values())):raise ValueError
                except Exception: raise ValueError("invalid_media_configuration") from None
                if provider.response_profile is not None or provider.response_format is not None or provider.legacy_variations:
                    raise ValueError("invalid_media_configuration")
            elif provider.voice_profile is not None: raise ValueError("invalid_media_configuration")
            origin(provider.base_url, mode=self.mode)
            if (not isinstance(provider.capabilities, (tuple, list)) or not provider.capabilities
                    or any(cap not in (IMAGE_CAPABILITIES if provider.adapter == "openai_images_v1" else {**ASYNC_CAPABILITIES, **AUDIO_CAPABILITIES, **VOICE_CAPABILITIES}[provider.adapter]) for cap in provider.capabilities)
                    or ("image.variation" in provider.capabilities and not provider.legacy_variations)):
                raise ValueError("invalid_media_configuration")
            if provider.adapter in ASYNC_CAPABILITIES and (provider.response_profile is not None or provider.response_format is not None or provider.legacy_variations):
                raise ValueError("invalid_media_configuration")
            if provider.response_profile == "newapi_images_v1" and (tuple(provider.capabilities) != ("image.generate",)
                    or provider.response_format is not None):
                raise ValueError("invalid_media_configuration")
            if not isinstance(provider.download_origins, (tuple, list)) or len(provider.download_origins) > 8:
                raise ValueError("invalid_media_configuration")
            for url in provider.download_origins: origin(url, mode=self.mode, root=True)
            if not isinstance(provider.download_path_prefixes,(tuple,list)) or len(provider.download_path_prefixes)>16:
                raise ValueError('invalid_media_configuration')
            for prefix in provider.download_path_prefixes:
                if (not isinstance(prefix,str)or not 2<=len(prefix)<=512 or not prefix.startswith('/')or not prefix.endswith('/')
                    or any(not (c.isascii()and(c.isalnum()or c in '/_-.'))for c in prefix)or '//'in prefix
                    or any(part in {'.','..'}for part in prefix.split('/'))):raise ValueError('invalid_media_configuration')
            if not isinstance(provider.models, dict) or (not provider.models and not provider.managed_models):
                raise ValueError("invalid_media_configuration")
            if provider.adapter=='newapi_incho_v1':
                origin(provider.base_url,mode=self.mode,root=True)
                if any(not isinstance(model,str)or model not in {'v3.5','v4.0'}for model in provider.models.values()):
                    raise ValueError('invalid_media_configuration')
            for public, upstream in provider.models.items():
                if (not isinstance(public, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", public) or public in models
                        or not isinstance(upstream, str) or not 1 <= len(upstream) <= 128
                        or any(ord(c) < 33 or ord(c) == 127 for c in upstream)):
                    raise ValueError("invalid_media_configuration")
                models.add(public)

    @property
    def bindings(self):
        result = {}
        for name, provider in self.providers.items():
            for model, upstream in provider.models.items():
                frozen = [self.mode, name, provider.version, provider.base_url, hashlib.sha256(provider.secret.encode()).hexdigest(),
                          model, upstream, provider.adapter, provider.protocol_version, list(provider.capabilities),
                          list(provider.download_origins), provider.legacy_variations, provider.response_format]
                # A missing profile retains every legacy binding byte. Explicit
                # gateway interpretation becomes part of the immutable binding.
                if provider.response_profile is not None:
                    frozen.append(provider.response_profile)
                if provider.platform_options:
                    frozen.append({"platform_options": provider.platform_options})
                if provider.adapter in AUDIO_CAPABILITIES:
                    frozen.append({"audio_profile": provider.audio_profile})
                if provider.adapter in VOICE_CAPABILITIES:
                    frozen.append({"voice_profile": provider.voice_profile})
                if provider.adapter in AV_ADAPTERS:
                    frozen.append({"av_tools": None if self.av_tools is None else [str(self.av_tools.ffmpeg),str(self.av_tools.ffprobe),self.av_tools.version],"download_path_prefixes":list(provider.download_path_prefixes)})
                result[model] = {"provider_id": name, "provider_version": provider.version, "adapter": provider.adapter,
                                 "protocol_version": provider.protocol_version,
                                 "binding_hash": hashlib.sha256(json.dumps(frozen, separators=(",", ":")).encode()).hexdigest()}
        return result

    @classmethod
    def from_env(cls, central_settings, *, env=None):
        env = os.environ if env is None else env
        path = env.get("CENTRAL_MEDIA_CONFIG_FILE")
        if not path: return cls()
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid() or info.st_size > 65536:
                    raise ValueError
                from .execution_protocol import _unique
                value = json.loads(stream.read(65537), object_pairs_hook=_unique)
            if not isinstance(value, dict): raise ValueError
            if value.get("av_tools") is not None:
                from .media.av_artifacts import AVTools
                value["av_tools"] = AVTools(**value["av_tools"])
            value["providers"] = {name: MediaProvider(**provider) for name, provider in value.get("providers", {}).items()}
            return cls(**value)
        except (OSError, ValueError, TypeError, AttributeError, RecursionError):
            raise ValueError("invalid_media_configuration") from None


def audio_profile(provider):
    from .media.adapters.audio import AudioProfile
    if not isinstance(provider.audio_profile, dict): raise ValueError("invalid_media_configuration")
    value = dict(provider.audio_profile)
    for key in ('voices', 'speech_formats', 'transcription_formats'):
        if key in value:
            if not isinstance(value[key], (tuple, list)): raise ValueError("invalid_media_configuration")
            value[key] = tuple(value[key])
    return AudioProfile(**value)


def voice_profile(provider):
    from .media.adapters.newapi_voice import BridgeVoiceProfile
    if not isinstance(provider.voice_profile, dict): raise ValueError("invalid_media_configuration")
    value = dict(provider.voice_profile)
    for key in ('target_models', 'formats', 'presets'):
        if key in value:
            if not isinstance(value[key], (tuple, list)): raise ValueError("invalid_media_configuration")
            value[key] = tuple(tuple(v) if isinstance(v, (tuple,list)) else v for v in value[key])
    return BridgeVoiceProfile(**value)


def voice_boundary(settings, name, provider, target_model):
    # Shared permission across public enrollment/synthesis model IDs. All private
    # connection, protocol, capability and profile fields remain in the boundary.
    from .media.contracts import digest
    return digest([settings.mode,name,provider.version,provider.base_url,hashlib.sha256(provider.secret.encode()).hexdigest(),
        provider.adapter,provider.protocol_version,list(provider.capabilities),provider.voice_profile,target_model,
        list(provider.download_origins),list(provider.download_path_prefixes)])
