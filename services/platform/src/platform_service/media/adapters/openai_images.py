"""One explicit nonstreaming Images request, with no paid delivery retries."""
import base64
import hashlib
import json
import re
import secrets

from ...errors import PlatformError
from ...execution_protocol import _unique
from ...media_config import origin
from .base import HttpCorrelation, ImageResult, MediaAdapter, PreparedRequest
from ..references import http_reference

PARAMETERS = frozenset({"n", "size", "quality", "response_format", "output_format", "background", "input_fidelity", "output_compression", "moderation", "mask_asset"})
PATHS = {"image.generate": "/images/generations", "image.edit": "/images/edits", "image.variation": "/images/variations"}


def correlation(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,239}", value, re.ASCII) else None


def encode(value): return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


async def bounded_response(response, maximum, *, json_type=False):
    if response.status_code != 200 or response.headers.get("content-encoding", "identity").lower() != "identity":
        raise PlatformError(502, "media_upstream_error")
    if json_type and response.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
        raise PlatformError(502, "invalid_media_response")
    declared = response.headers.get("content-length")
    if declared is not None and (not declared.isascii() or not declared.isdecimal() or int(declared) > maximum):
        raise PlatformError(502, "media_response_too_large")
    raw = bytearray()
    async for chunk in response.aiter_bytes():
        if len(raw) + len(chunk) > maximum: raise PlatformError(502, "media_response_too_large")
        raw.extend(chunk)
    if declared is not None and len(raw) != int(declared): raise PlatformError(502, "invalid_media_response")
    return bytes(raw)


class OpenAIImagesAdapter(MediaAdapter):
    def __init__(self, provider, mode):
        self.provider, self.mode = provider, mode

    def _auth(self):
        header = (self.provider.platform_options or {}).get("auth_header")
        return {header: self.provider.secret} if header else {"Authorization": "Bearer " + self.provider.secret}

    def prepare(self, request, assets):
        cap = request.get("capability")
        if cap not in self.provider.capabilities or cap not in PATHS:
            raise PlatformError(503, "media_capability_closed")
        params = dict(request.get("parameters", {}))
        if set(params) - PARAMETERS or type(params.get("n", 1)) is not int or not 1 <= params.get("n", 1) <= 4:
            raise PlatformError(422, "invalid_media_parameters")
        if self.provider.response_profile == "newapi_images_v1" and params != {"n": 1, "size": "1024x1024"}:
            raise PlatformError(422, "unverified_media_parameters")
        mask_id = params.pop("mask_asset", None)
        if self.provider.response_format is not None:
            if params.get("response_format", self.provider.response_format) != self.provider.response_format:
                raise PlatformError(422, "invalid_media_parameters")
            params["response_format"] = self.provider.response_format
        model = self.provider.models[request["model"]]
        fields = {"model": model, **params}
        prompt = request.get("prompt", "")
        if cap != "image.variation": fields["prompt"] = prompt
        options = self.provider.platform_options or {}
        url = self.provider.base_url.rstrip("/") + PATHS[cap] + ("?api-version=" + options["api_version"] if options.get("api_version") else "")
        if cap == "image.generate":
            if assets or mask_id or not prompt.strip(): raise PlatformError(422, "invalid_media_assets")
            return PreparedRequest(url, encode(fields), "application/json", model)
        if cap == "image.variation":
            if (not self.provider.legacy_variations or len(assets) != 1 or prompt.strip() or mask_id
                    or set(params)-{"n","size","response_format"}
                    or params.get("size","1024x1024") not in {"256x256","512x512","1024x1024"}):
                raise PlatformError(422, "invalid_media_variation")
            image=assets[0]
            if (image["mime"]!="image/png" or len(image["raw"])>=4*1024*1024
                    or image["properties"]["width"]!=image["properties"]["height"]):
                raise PlatformError(422, "invalid_media_variation")
        elif not assets or len(assets) > 4 or not prompt.strip():
            raise PlatformError(422, "invalid_media_assets")
        mask = next((asset for asset in assets if asset["id"] == mask_id), None) if mask_id else None
        if mask_id and mask is None: raise PlatformError(422, "invalid_media_assets")
        images = [asset for asset in assets if asset is not mask]
        if not images: raise PlatformError(422, "invalid_media_assets")
        if mask and (mask["mime"] != "image/png" or any(mask["properties"].get(k) != images[0]["properties"].get(k) for k in ("width", "height"))):
            raise PlatformError(422, "invalid_media_mask")
        boundary = "easel-media-" + secrets.token_hex(16)
        parts = []
        for name, value in fields.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode() + str(value).encode() + b"\r\n")
        for index, asset in enumerate([*images, *([mask] if mask else [])]):
            name = "mask" if asset is mask else "image[]" if len(images) > 1 else "image"
            suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[asset["mime"]]
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="input-{index}{suffix}"\r\nContent-Type: {asset["mime"]}\r\n\r\n'.encode() + asset["raw"] + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        return PreparedRequest(url, b"".join(parts), "multipart/form-data; boundary=" + boundary, model)

    async def submit(self, client, prepared, *, request_id, max_bytes, on_headers):
        async with client.stream("POST", prepared.url, content=prepared.payload,
                                 headers={**self._auth(),
                                          "Content-Type": prepared.content_type, "Accept": "application/json",
                                          "Accept-Encoding": "identity", "X-Request-ID": request_id}) as response:
            gateway = self.provider.response_profile == "newapi_images_v1"
            http_id = correlation(response.headers.get("x-oneapi-request-id") if gateway else
                                  response.headers.get("x-request-id") or response.headers.get("request-id"))
            reference = None
            if gateway and http_id is not None:
                try:
                    http_reference("newapi_x_oneapi_request_id_v1", http_id)
                    reference = HttpCorrelation("newapi_x_oneapi_request_id_v1", http_id)
                except PlatformError:
                    http_id = None
            await on_headers(http_status=response.status_code, **({"http_request_id": http_id} if http_id else {}))
            raw = await bounded_response(response, max_bytes, json_type=True)
        try:
            value = json.loads(raw, object_pairs_hook=_unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(value, dict) or value.get("error") or not isinstance(value.get("data"), list) or not 1 <= len(value["data"]) <= 4:
                raise ValueError
            if gateway:
                if reference is None or type(value.get("created")) is not int or value["created"] <= 0:
                    raise ValueError
                response_id = None
            else:
                response_id = correlation(value.get("id")) or http_id or (request_id if self.provider.client_correlation else None)
                if response_id is None: raise ValueError
            images = []
            for row in value["data"]:
                if not isinstance(row, dict) or bool(row.get("url")) == bool(row.get("b64_json")):
                    raise ValueError
                images.append({"url": row["url"]} if row.get("url") else {"b64_json": row["b64_json"]})
            usage = value.get("usage")
            quantities, usage_hash = {}, None
            if usage is not None:
                if not isinstance(usage, dict): raise ValueError
                for public, key in (("input_token", "input_tokens"), ("output_token", "output_tokens")):
                    count = usage.get(key)
                    if type(count) is not int or not 0 <= count <= 9223372036854775807: raise ValueError
                    quantities[public] = count
                if "total_tokens" in usage and (type(usage["total_tokens"]) is not int or usage["total_tokens"] != sum(quantities.values())):
                    raise ValueError
                usage_hash = hashlib.sha256(encode(usage)).hexdigest()
            return ImageResult(response_id, images, quantities, usage_hash, hashlib.sha256(raw).hexdigest(), reference)
        except (ValueError, TypeError, UnicodeError, KeyError):
            raise PlatformError(502, "invalid_media_response") from None

    async def retrieve(self, client, source, *, max_bytes):
        if "b64_json" in source:
            value = source["b64_json"]
            try:
                if not isinstance(value, str) or len(value) > ((max_bytes + 2) // 3) * 4:
                    raise ValueError
                raw = base64.b64decode(value, validate=True)
                if not 0 < len(raw) <= max_bytes: raise ValueError
                return raw, None
            except (ValueError, TypeError):
                raise PlatformError(502, "invalid_media_response") from None
        url = source.get("url")
        try:
            target = origin(url, mode=self.mode, allow_query=True)
            allowed = [origin(value, mode=self.mode, root=True) for value in self.provider.download_origins]
            if target not in allowed: raise ValueError
        except ValueError:
            raise PlatformError(502, "media_download_origin_denied") from None
        # No provider Authorization is forwarded to artifact/CDN URLs.
        async with client.stream("GET", url, headers={"Accept-Encoding": "identity", "Accept": "image/png,image/jpeg,image/webp"}) as response:
            raw = await bounded_response(response, max_bytes)
            mime = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if mime not in ("image/png", "image/jpeg", "image/webp"):
                raise PlatformError(502, "invalid_media_response")
            return raw, mime
