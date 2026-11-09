"""Suppliers' own video and music task APIs, called without a gateway in between."""
import base64
import hashlib
import hmac
import json
import time
from urllib.parse import quote, urlsplit

from ...errors import PlatformError
from ...media_config import origin
from ...model_templates import APIMART_SIZES
from ..async_contracts import ArtifactSource, Observation
from .base import PreparedRequest
from .newapi_async import AsyncAdapter, NewAPIDashScopeMusicBase, bounded, encode, job_id

_RATIOS = {"1280x720": ("16:9", "720P"), "720x1280": ("9:16", "720P"), "1024x1024": ("1:1", "720P")}
_GENERIC_MIMES = {"", "application/octet-stream", "binary/octet-stream"}


def _bad():
    return PlatformError(502, "invalid_media_response")


class DirectAsync(AsyncAdapter):
    """Downloads come from the supplier's own storage hosts, without the API key."""
    suffixes = ()
    default_mime = "video/mp4"
    output_mimes = frozenset({"video/mp4"})

    def source_url(self, value):
        try:
            target = origin(value, mode=self.mode, allow_query=True)
            allowed = target in [origin(o, mode=self.mode, root=True) for o in self.provider.download_origins]
            host = urlsplit(value).hostname or ""
            if not allowed and not (target[0] == "https" and any(host.endswith(suffix) for suffix in self.suffixes)):
                raise ValueError
        except ValueError:
            raise PlatformError(502, "media_download_origin_denied") from None
        return value

    async def retrieve(self, client, source, *, max_bytes):
        if source.kind != "url":
            raise _bad()
        self.source_url(source.location)
        async with client.stream("GET", source.location, headers={"Accept-Encoding": "identity", "Accept": "*/*"}) as response:
            raw = await bounded(response, max_bytes)
            mime = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        # Object stores often omit the type; the decoder still checks the real bytes.
        mime = self.default_mime if mime in _GENERIC_MIMES else mime
        if mime not in self.output_mimes:
            raise _bad()
        return raw, mime

    def url_source(self, value, source_id="output"):
        if not isinstance(value, str):
            raise _bad()
        return ArtifactSource(source_id, "url", self.source_url(value))

    def _base(self):
        return self.provider.base_url.rstrip("/")


class DirectVideo(DirectAsync):
    capability = "video.generate"

    def video(self, request, assets):
        model, parameters, text = self.check(request, assets, {"seconds", "size"})
        seconds, size = parameters.get("seconds"), parameters.get("size")
        if len(assets) > 1 or type(seconds) is not int or not 1 <= seconds <= 900 or size not in _RATIOS:
            raise PlatformError(422, "invalid_media_parameters")
        return model, text, seconds, size, *_RATIOS[size]

    @staticmethod
    def _image(assets):
        """Base64 of the one starting frame and its data URI, or (None, None)."""
        if not assets:
            return None, None
        item = assets[0]
        if item.get("mime") not in ("image/png", "image/jpeg", "image/webp") or not isinstance(item.get("raw"), bytes) or not 0 < len(item["raw"]) <= 10 * 1024 * 1024:
            raise PlatformError(422, "invalid_media_assets")
        encoded = base64.b64encode(item["raw"]).decode("ascii")
        return encoded, "data:" + item["mime"] + ";base64," + encoded


class DashScopeVideo(DirectVideo):
    suffixes = (".aliyuncs.com",)
    submit_headers = {"X-DashScope-Async": "enable"}

    def prepare(self, request, assets):
        model, text, seconds, size, ratio, resolution = self.video(request, assets)
        # wan2.7 replaced size with resolution and ratio.
        parameters = {"duration": seconds, **({"resolution": resolution, "ratio": ratio} if model.startswith("wan2.7") else {"size": size.replace("x", "*")})}
        _, frame = self._image(assets)
        block = {"prompt": text, **({} if not frame else {"media": [{"type": "first_frame", "url": frame}]} if model.startswith("wan2.7") else {"img_url": frame})}
        return PreparedRequest(self._base() + "/services/aigc/video-generation/video-synthesis",
                               encode({"model": model, "input": block, "parameters": parameters}), "application/json", model)

    def _job_id(self, value, *, submit):
        output = value.get("output")
        return job_id(output.get("task_id") if isinstance(output, dict) else None)

    def query_url(self, known):
        return self._base() + "/tasks/" + job_id(known)

    def observe(self, value, sha, known):
        output = value.get("output")
        if not isinstance(output, dict):
            raise _bad()
        state = {"PENDING": "queued", "RUNNING": "running", "SUCCEEDED": "completed", "FAILED": "failed", "CANCELED": "cancelled"}.get(output.get("task_status"))
        if state is None:
            raise _bad()
        return Observation(known, state, (self.url_source(output.get("video_url")),) if state == "completed" else (), {}, None, sha)


class ArkVideo(DirectVideo):
    suffixes = (".volces.com", ".volccdn.com")

    def prepare(self, request, assets):
        model, text, seconds, _, ratio, _ = self.video(request, assets)
        _, frame = self._image(assets)
        content = [{"type": "text", "text": text}, *([{"type": "image_url", "image_url": {"url": frame}}] if frame else [])]
        return PreparedRequest(self._base() + "/contents/generations/tasks",
                               encode({"model": model, "content": content, "ratio": ratio, "duration": seconds}), "application/json", model)

    def _job_id(self, value, *, submit):
        return job_id(value.get("id"))

    def _submission_observation(self, value, sha, known):
        return None

    def query_url(self, known):
        return self._base() + "/contents/generations/tasks/" + job_id(known)

    def observe(self, value, sha, known):
        state = {"queued": "queued", "running": "running", "succeeded": "completed", "failed": "failed", "cancelled": "cancelled", "expired": "failed"}.get(value.get("status"))
        if state is None:
            raise _bad()
        content = value.get("content")
        return Observation(known, state, (self.url_source(content.get("video_url") if isinstance(content, dict) else None),) if state == "completed" else (), {}, None, sha)


class KlingVideo(DirectVideo):
    suffixes = (".klingai.com", ".kuaishou.com", ".kwai.net", ".kwaicdn.com")

    def _auth(self):
        def part(data):
            return base64.urlsafe_b64encode(data).rstrip(b"=").decode()
        access, secret = self.provider.secret.split("|", 1)
        now = int(time.time())
        head = part(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
        body = part(json.dumps({"iss": access, "exp": now + 1800, "nbf": now - 5}, separators=(",", ":")).encode())
        signature = hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
        return {"Authorization": "Bearer " + f"{head}.{body}.{part(signature)}"}

    def prepare(self, request, assets):
        model, text, seconds, _, ratio, _ = self.video(request, assets)
        if seconds not in (5, 10):
            raise PlatformError(422, "invalid_media_parameters")
        encoded, _ = self._image(assets)
        value = {"model_name": model, "prompt": text, "duration": str(seconds), "aspect_ratio": ratio, **({"image": encoded} if encoded else {})}
        return PreparedRequest(self._base() + "/v1/videos/" + ("image2video" if encoded else "text2video"), encode(value), "application/json", model)

    def _job_id(self, value, *, submit):
        data = value.get("data")
        return job_id(data.get("task_id") if isinstance(data, dict) else None)

    def _submission_observation(self, value, sha, known):
        return None

    def query_url(self, known):
        kind = "image2video" if self.job and (self.job.get("request") or {}).get("assets") else "text2video"
        return self._base() + "/v1/videos/" + kind + "/" + job_id(known)

    def observe(self, value, sha, known):
        data = value.get("data")
        if value.get("code") != 0 or not isinstance(data, dict):
            raise _bad()
        state = {"submitted": "queued", "processing": "running", "succeed": "completed", "failed": "failed"}.get(data.get("task_status"))
        if state is None:
            raise _bad()
        sources = ()
        if state == "completed":
            videos = (data.get("task_result") or {}).get("videos")
            if not isinstance(videos, list) or not videos or not isinstance(videos[0], dict):
                raise _bad()
            sources = (self.url_source(videos[0].get("url")),)
        return Observation(known, state, sources, {}, None, sha)


class AgnesVideo(DirectVideo):
    suffixes = (".agnes-ai.com",)

    def prepare(self, request, assets):
        model, text, seconds, _, ratio, _ = self.video(request, assets)
        if not 4 <= seconds <= 12:
            raise PlatformError(422, "invalid_media_parameters")
        _, frame = self._image(assets)
        value = {"model": model, "prompt": text, "seconds": str(seconds), "size": "720P", "aspect_ratio": ratio, "mode": "keyframe" if frame else "text", **({"first_frame": frame} if frame else {})}
        return PreparedRequest(self._base() + "/videos", encode(value), "application/json", model)

    def _job_id(self, value, *, submit):
        return job_id(value.get("video_id") or value.get("id") or value.get("task_id"))

    def _submission_observation(self, value, sha, known):
        return None

    def query_url(self, known):
        options = self.provider.platform_options or {}
        base = options.get("poll_url") or "https://apihub.agnes-ai.com/agnesapi"
        upstream = self.provider.models[self.job["model"]]
        return base + "?video_id=" + quote(job_id(known), safe="") + "&model_name=" + quote(upstream, safe="")

    async def query(self, client, provider_job_id, *, max_bytes):
        known = job_id(provider_job_id)
        value, sha = await self._json(client, "GET", self.query_url(known), maximum=max_bytes)
        return self.observe(value, sha, known)

    def observe(self, value, sha, known):
        state = {"queued": "queued", "pending": "queued", "processing": "running", "in_progress": "running", "running": "running",
                 "completed": "completed", "succeeded": "completed", "failed": "failed", "error": "failed"}.get(value.get("status"))
        if state is None:
            raise _bad()
        return Observation(known, state, (self.url_source(value.get("url")),) if state == "completed" else (), {}, None, sha)


class XhsVideo(DashScopeVideo):
    """Xiaohongshu's internal MaaS: DashScope-style tasks, an api-key header, and a body on GET."""
    suffixes = (".xiaohongshu.com",)
    submit_headers = {}

    def _auth(self):
        return {"api-key": self.provider.secret}

    def prepare(self, request, assets):
        model, text, seconds, _, ratio, resolution = self.video(request, assets)
        _, frame = self._image(assets)
        block = {"prompt": text, **({"media": [{"type": "first_frame", "url": frame}]} if frame else {})}
        return PreparedRequest(self._base() + "/services/aigc/video-generation/video-synthesis",
                               encode({"model": model, "input": block, "parameters": {"resolution": resolution, "ratio": ratio, "duration": seconds}}), "application/json", model)

    async def query(self, client, provider_job_id, *, max_bytes):
        known = job_id(provider_job_id)
        body = PreparedRequest(self.query_url(known), encode({"model": self.provider.models[self.job["model"]]}), "application/json", "")
        value, sha = await self._json(client, "GET", body.url, maximum=max_bytes, prepared=body)
        return self.observe(value, sha, known)


class DirectMusic(DirectAsync):
    capability = "music.generate"
    default_mime = "audio/mpeg"
    output_mimes = frozenset({"audio/mpeg", "audio/wav"})

    def music(self, request, assets):
        model, parameters, text = self.check(request, assets, {"lyrics", "title", "instrumental"})
        lyrics, instrumental = parameters.get("lyrics"), parameters.get("instrumental", False)
        if assets or type(instrumental) is not bool or (lyrics is not None and (not isinstance(lyrics, str) or not lyrics.strip())):
            raise PlatformError(422, "invalid_media_parameters")
        return model, text, lyrics, parameters.get("title"), instrumental and not lyrics


class DashScopeMusic(DirectMusic, NewAPIDashScopeMusicBase):
    suffixes = (".aliyuncs.com",)
    submit_headers = {"X-DashScope-Async": "enable"}

    def prepare(self, request, assets):
        model, text, lyrics, _, instrumental = self.music(request, assets)
        if len(text) > 2000:
            raise PlatformError(422, "invalid_media_parameters")
        block = {"prompt": text, "is_instrumental": instrumental, **({"lyrics": lyrics} if lyrics else {})}
        return PreparedRequest(self._base() + "/services/audio/music/generation", encode({"model": model, "input": block}), "application/json", model)

    def query_url(self, known):
        return self._base() + "/tasks/" + job_id(known)


def _first(value, names):
    """The first of these fields, also looking inside the data envelope such APIs add."""
    if isinstance(value, dict):
        for name in names:
            if isinstance(value.get(name), (str, int)) and not isinstance(value.get(name), bool):
                return str(value[name])
        return _first(value.get("data"), names)
    if isinstance(value, list) and value:
        return _first(value[0], names)
    return None


class SunoDirect(DirectMusic):
    suffixes = (".suno.ai", ".suno.com")

    def prepare(self, request, assets):
        model, text, lyrics, title, instrumental = self.music(request, assets)
        value = {"model": model, "prompt": text, "make_instrumental": instrumental, **({"lyrics": lyrics} if lyrics else {}), **({"title": title} if title else {})}
        return PreparedRequest(self._base() + "/generate", encode(value), "application/json", model)

    def _job_id(self, value, *, submit):
        return job_id(_first(value, ("task_id", "id", "request_id")))

    def _submission_observation(self, value, sha, known):
        return None

    def query_url(self, known):
        return self._base() + "/feed/" + job_id(known)

    async def query(self, client, provider_job_id, *, max_bytes):
        known = job_id(provider_job_id)
        value, sha = await self._json(client, "GET", self.query_url(known), maximum=max_bytes)
        return self.observe(value, sha, known)

    def observe(self, value, sha, known):
        status = (_first(value, ("status", "task_status", "state")) or "").lower()
        if status in ("complete", "completed", "succeeded", "success"):
            url = _first(value, ("audio_url", "url"))
            return Observation(known, "completed", (self.url_source(url),), {}, None, sha)
        if status in ("failed", "error"):
            return Observation(known, "failed", (), {}, None, sha)
        if not status:
            raise _bad()
        return Observation(known, "running", (), {}, None, sha)


class ApimartImage(DirectAsync):
    """apimart answers image requests with a task; there is no synchronous mode."""
    suffixes = (".apimart.ai",)
    default_mime = "image/png"
    output_mimes = frozenset({"image/png", "image/jpeg", "image/webp"})

    def prepare(self, request, assets):
        self.capability = request.get("capability") if request.get("capability") in ("image.generate", "image.edit") else None
        model, parameters, text = self.check(request, assets, {"n", "size"})
        if parameters.get("n", 1) != 1 or parameters.get("size") not in APIMART_SIZES:
            raise PlatformError(422, "invalid_media_parameters")
        if (not assets) == (self.capability == "image.edit") or len(assets) > 1:
            raise PlatformError(422, "invalid_media_assets")
        references = []
        for item in assets:
            if item.get("mime") not in self.output_mimes or not isinstance(item.get("raw"), bytes) or not 0 < len(item["raw"]) <= 20 * 1024 * 1024:
                raise PlatformError(422, "invalid_media_assets")
            references.append("data:" + item["mime"] + ";base64," + base64.b64encode(item["raw"]).decode("ascii"))
        value = {"model": model, "prompt": text, "n": 1, "size": APIMART_SIZES[parameters["size"]], "resolution": "1k", **({"image_urls": references} if references else {})}
        return PreparedRequest(self._base() + "/images/generations", encode(value), "application/json", model)

    def _job_id(self, value, *, submit):
        rows = value.get("data")
        return job_id(rows[0].get("task_id") if isinstance(rows, list) and rows and isinstance(rows[0], dict) else None)

    def _submission_observation(self, value, sha, known):
        return None

    def query_url(self, known):
        return self._base() + "/tasks/" + job_id(known)

    async def query(self, client, provider_job_id, *, max_bytes):
        known = job_id(provider_job_id)
        value, sha = await self._json(client, "GET", self.query_url(known), maximum=max_bytes)
        return self.observe(value, sha, known)

    def observe(self, value, sha, known):
        data = value.get("data")
        if value.get("code") not in (None, 200) or not isinstance(data, dict):
            raise _bad()
        state = {"pending": "queued", "submitted": "queued", "processing": "running", "in_progress": "running",
                 "completed": "completed", "failed": "failed", "cancelled": "cancelled"}.get(data.get("status"))
        if state is None:
            raise _bad()
        sources = ()
        if state == "completed":
            result = data.get("result")
            images = result.get("images") if isinstance(result, dict) else None
            first = images[0].get("url") if isinstance(images, list) and images and isinstance(images[0], dict) else None
            sources = (self.url_source(first[0] if isinstance(first, list) and first else first),)
        return Observation(known, state, sources, {}, None, sha)


DIRECT_ASYNC = {"apimart_image_v1": ApimartImage, "dashscope_video_v1": DashScopeVideo, "ark_video_v1": ArkVideo, "kling_video_v1": KlingVideo, "agnes_video_v1": AgnesVideo, "xhs_video_v1": XhsVideo,
                "dashscope_music_v1": DashScopeMusic, "suno_direct_v1": SunoDirect}
