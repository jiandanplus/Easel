"""Native central media tools. Product and task credentials stay in the host."""
from __future__ import annotations

from contextlib import contextmanager
import asyncio
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlencode
import uuid
import warnings

import httpx

from easel.harnesses.base import HarnessError
from .central import CentralClient, CentralError, not_sent_error, timestamp
from .client import PlatformError, validate_url
from .execution_records import operation_owner, operation_turn_key
from .media_records import MediaRecords, MediaRecordError, digest
from .media_contracts import CAPABILITIES, MAX_MEDIA, MIMES, public_definition, parameters as checked_parameters
from .media_files import properties as checked_properties, save as _save_media, freeze as _freeze_media, verify as _verify_media
from .auth_refresh import (access as refresh_access, access_error, ACCESS_FAILURES,
                           guard_present, RefreshUncertain, diagnostic)

MAX_JSON = 2 * 1024 * 1024
MAX_IMAGE = 20 * 1024 * 1024
_UUID = re.compile(r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}")
_HASH = re.compile(r"[a-f0-9]{64}")
_MESSAGES = {
    "media_unavailable": "媒体暂不可用",
    "media_entitlement_required": "当前账号未开通此媒体功能",
    "insufficient_credits": "可用点数不足",
    "concurrency_limit": "已有任务正在执行",
    "pending_reconciliation_limit": "待核算请求已达上限",
    "media_not_found": "未找到该媒体任务",
    "media_artifact_unavailable": "文件暂不可下载",
    "media_records_unavailable": "媒体记录暂不可查询",
    "account_changed": "账号状态已改变",
    "invalid_authentication": "请重新登录",
    "session_invalid": "请重新登录",
    "refresh_replayed": "请重新登录",
    "refresh_uncertain": "登录状态待确认，请重新登录",
}


class MediaError(CentralError):
    def __init__(self, code="media_unavailable", status=503):
        super().__init__(code, status)
        self.args = (_MESSAGES.get(code, "媒体操作未完成"),)


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[\x21-\x7e]{1,240}", value): raise ValueError
    return value


def _text(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 240 or any(ord(c) < 32 for c in value): raise ValueError
    return value


def _uuid(value):
    if not isinstance(value, str) or not _UUID.fullmatch(value): raise ValueError
    return value


def _money(value, *, positive=False):
    if (not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,19}", value)
            or int(value) > 2**63 - 1 or (positive and int(value) == 0)): raise ValueError
    return value


def _artifact(value):
    if not isinstance(value, dict): raise ValueError
    result = {"id": _uuid(value["id"]), "sha256": value["sha256"], "size": value["size"], "mime": value["mime"]}
    if (not isinstance(result["sha256"], str) or not _HASH.fullmatch(result["sha256"])
            or type(result["size"]) is not int or not 0 < result["size"] <= MAX_MEDIA
            or result["mime"] not in {*MIMES, "application/json"}): raise ValueError
    if result["mime"].startswith("image/") and result["size"] > MAX_IMAGE: raise ValueError
    result["properties"] = checked_properties(result["mime"], value["properties"])
    for name in ("created_at", "expires_at", "purged_at"):
        if name not in value:
            if name == "expires_at": raise ValueError
            continue
        item = value.get(name)
        if item is not None: timestamp(item)
        elif name != "purged_at": raise ValueError
        result[name] = item
    if "job_id" in value: result["job_id"] = _uuid(value["job_id"])
    return result


def _voice(value):
    if not isinstance(value, dict) or set(value) != {"id", "name", "language", "expires_at", "state"} or value["state"] != "ready": raise ValueError
    _uuid(value["id"]); timestamp(value["expires_at"])
    for name in ("name", "language"):
        if name == "language" and value[name] is None: continue
        if not isinstance(value[name], str) or len(value[name]) > 240 or any(ord(c)<32 for c in value[name]): raise ValueError
    return dict(value)


def _job(value, *, artifact_detail=True):
    if not isinstance(value, dict): raise ValueError
    row = {k: _identifier(value[k]) for k in ("model", "rate_version", "created_at", "updated_at")}
    row["id"] = _uuid(value["id"])
    for name in ("created_at", "updated_at"): timestamp(row[name])
    if (value["capability"] not in CAPABILITIES
            or value["status"] not in {"reserved", "dispatched", "pending_reconciliation", "settled", "released", "waived"}
            or value["financial_state"] != value["status"]
            or value["execution_state"] not in {"queued", "running", "completed", "failed", "cancel_requested", "cancelled", "unknown"}
            or value["delivery_state"] not in {"processing", "available", "unavailable", "expired"}
            or value["funding_mode"] not in {"live", "test"}): raise ValueError
    row.update({k: value[k] for k in ("capability", "status", "financial_state", "execution_state", "delivery_state", "funding_mode")})
    row["reserved"] = _money(value["reserved"])
    row["amount"] = _money(value.get("amount", value["reserved"]))
    if row["amount"] != row["reserved"]: raise ValueError
    row["actual"] = None if value.get("actual") is None else _money(value["actual"])
    if row["status"] in {"settled", "released"} and row["actual"] is None: raise ValueError
    # Platform-absorbed (central migration 021): the customer pays 0 and supplier usage stays unknown.
    if row["status"] == "waived" and row["actual"] is not None: raise ValueError
    artifacts = value.get("artifacts", [])
    if not isinstance(artifacts, list) or len(artifacts) > 32: raise ValueError
    row["artifacts"] = [_artifact(item) for item in artifacts]
    prefixes = ("image/",) if row["capability"].startswith("image.") else ("video/",) if row["capability"] == "video.generate" else ("application/json",) if row["capability"] == "audio.transcribe" else ("audio/",)
    if any(not item["mime"].startswith(prefixes) for item in row["artifacts"]) or row["capability"] == "voice.enroll" and artifacts: raise ValueError
    if artifact_detail and row["execution_state"] == "completed" and row["delivery_state"] == "available":
        if row["capability"].startswith("image."):
            if len(row["artifacts"]) != 1 or any(row["artifacts"][0]["properties"].get(key) != 1024 for key in ("width", "height")): raise ValueError
        elif row["capability"] != "voice.enroll" and not row["artifacts"]: raise ValueError
    if value.get("voice") is not None:
        if row["capability"] != "voice.enroll": raise ValueError
        row["voice"] = _voice(value["voice"])
    if artifact_detail and row["capability"] == "voice.enroll" and row["execution_state"] == "completed" and row["delivery_state"] == "available" and "voice" not in row: raise ValueError
    return row


class MediaClient:
    def __init__(self, root, *, transport=None, clock=time.time, url=None, central=None):
        self.central = central or CentralClient(root, transport=transport, clock=clock, url=url)
        self.records = MediaRecords(root, clock=clock)
        self.clock = clock

    def _current(self, snapshot):
        # No credential-file lock spans an external generation/download request.
        with self.central.store.locked():
            state = self.central._state()
            auth = state.get("auth")
            if guard_present(state):raise MediaError("refresh_uncertain",409)
            if (not auth or state["url"] != snapshot["url"] or auth.get("scope") != snapshot["scope"]
                    or auth.get("refresh_expires_at", 0) <= self.clock()):
                raise MediaError("account_changed", 409)
            snapshot["token"] = auth["access_token"]

    @contextmanager
    def publication(self, snapshot):
        """Serialize only the final local link with logout, never network/decoding."""
        with self.central.store.locked():
            state = self.central._state()
            auth = state.get("auth")
            if guard_present(state):raise MediaError("refresh_uncertain",409)
            if (not auth or state["url"] != snapshot["url"] or auth.get("scope") != snapshot["scope"]
                    or auth.get("refresh_expires_at", 0) <= self.clock()):
                raise MediaError("account_changed", 409)
            yield

    def _access(self, state, *, deadline=None, force=False):
        """Keep the original serialized refresh semantics with bounded async I/O."""
        def send(refresh):
            return self._fetch({"url":state["url"],"scope":state["auth"]["scope"],"token":""},
                "POST","/v2/auth/refresh",{"refresh_token":refresh},deadline=deadline)
        try:return refresh_access(self.central,state,send=send,clock=self.clock,force=force)
        except RefreshUncertain:raise MediaError("refresh_uncertain",409)from None

    def _forget(self, snapshot, error):
        with self.central.store.locked():
            state = self.central._state()
            auth = state.get("auth")
            if (auth and state["url"] == snapshot["url"] and auth.get("scope") == snapshot["scope"]
                    and auth.get("access_token") == snapshot["token"]):
                self.central._forget_invalid(state, error)

    def _snapshot(self, *, expected_scope=None, expected_url=None, deadline=None, read_timeout=None):
        with self.central.store.locked():
            state = self.central._state()
            initial=(state.get("auth") or {}).get("access_token")
            token = self._access(state, deadline=deadline)
            scope = dict(state["auth"]["scope"])
            if ((expected_scope is not None and scope != expected_scope)
                    or (expected_url is not None and state["url"] != expected_url)):
                raise MediaError("account_changed", 409)
            snapshot = {"url": state["url"], "scope": scope, "token": token}
        try:
            me = self._get(snapshot, "/v2/me", deadline=deadline,
                           timeout=read_timeout if read_timeout is not None else (2 if deadline is not None else 15),
                           already_renewed=token!=initial)
        except PlatformError as error:
            self._forget(snapshot, error)
            raise
        if me.get("scope") != scope or me.get("account", {}).get("id") != scope["user_id"]:
            raise MediaError("account_changed", 409)
        self._current(snapshot)
        return snapshot

    def _get(self,snapshot,path,*,already_renewed=False,**options):
        deadline=options.get("deadline")
        options["deadline"]=(min(deadline,time.monotonic()+options.get("timeout",15))
                             if deadline is not None else time.monotonic()+options.get("timeout",15))
        self._current(snapshot)
        used=snapshot["token"]
        try:return self._fetch(snapshot,"GET",path,**options)
        except PlatformError as error:
            diagnostic(error.code,error.status,path,snapshot["scope"])
            if error.status!=401 or error.code not in ACCESS_FAILURES or already_renewed:
                raise access_error(error)
        with self.central.store.locked():
            state=self.central._state();auth=state.get("auth")
            if not auth or state["url"]!=snapshot["url"] or auth.get("scope")!=snapshot["scope"]:
                raise MediaError("account_changed",409)
            if guard_present(state):raise MediaError("refresh_uncertain",409)
            token=(self._access(state,deadline=options.get("deadline"),force=True)
                   if auth["access_token"]==used else self._access(state,deadline=options.get("deadline")))
            snapshot["token"]=token
        try:return self._fetch(snapshot,"GET",path,**options)
        except PlatformError as error:
            diagnostic(error.code,error.status,path,snapshot["scope"])
            raise access_error(error)

    def _fetch(self, snapshot, method, path, body=None, *, task_token=None, operation_key=None,
               timeout=15, binary=False, maximum=MAX_JSON, deadline=None, content=None, content_type=None):
        bearer = task_token or snapshot["token"]
        headers = {"Authorization": "Bearer " + bearer} if bearer else {}
        if operation_key: headers["Idempotency-Key"] = operation_key
        if content_type: headers["Content-Type"] = content_type
        headers["Accept-Encoding"] = "identity"
        deadline = min(deadline, time.monotonic() + timeout) if deadline is not None else time.monotonic() + timeout
        remaining = deadline - time.monotonic()
        if remaining <= 0: raise MediaError()
        async def exchange():
            # These synchronous public methods run in the host's owned thread.
            # Async cancellation bounds headers and stalled reads, not just the
            # time between chunks. No retry is attached to this transport.
            async with asyncio.timeout(max(.001, deadline - time.monotonic())):
                async with httpx.AsyncClient(transport=self.central.transport, trust_env=False, follow_redirects=False,
                                  timeout=httpx.Timeout(remaining, connect=min(10, remaining))) as client:
                    async with client.stream(method, validate_url(snapshot["url"]) + path, json=body, content=content, headers=headers) as response:
                        if response.headers.get("content-encoding", "identity").lower() != "identity": raise MediaError()
                        raw = bytearray()
                        async for chunk in response.aiter_bytes():
                            if time.monotonic() >= deadline or len(raw) + len(chunk) > maximum: raise MediaError()
                            raw.extend(chunk)
                        if response.status_code >= 300:
                            try:
                                value = json.loads(raw)
                                code = (value.get("error") or {}).get("code")
                            except (ValueError, TypeError, AttributeError): code = None
                            raise MediaError(code if code in _MESSAGES else "media_unavailable", response.status_code)
                        if binary: return bytes(raw), dict(response.headers)
                        value = json.loads(raw)
                        if not isinstance(value, dict): raise ValueError
                        return self.central._safe(value) if path not in {"/v2/media/task-tokens", "/v2/auth/refresh"} else value
        try:
            return asyncio.run(exchange())
        except PlatformError:
            raise
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise not_sent_error(MediaError()) from None
        except (httpx.HTTPError, ValueError, TypeError, AttributeError, RecursionError, TimeoutError):
            raise MediaError() from None

    def catalog(self, *, snapshot=None):
        snapshot = snapshot or self._snapshot()
        value = self._get(snapshot, "/v2/products/easel/media-models")
        self._current(snapshot)
        try:
            rows = value["models"]
            if not isinstance(rows, list) or len(rows) > 100: raise ValueError
            result = []
            seen = set()
            for row in rows:
                if not isinstance(row, dict): raise ValueError
                try: item = public_definition(row)
                except (KeyError, TypeError, ValueError): continue
                if item["id"] in seen: raise ValueError
                seen.add(item["id"])
                result.append(item)
            return self.central._safe({"models": result})
        except (KeyError, ValueError, TypeError): raise MediaError() from None

    def mint(self, snapshot, record):
        self._current(snapshot)
        value = self._fetch(snapshot, "POST", "/v2/media/task-tokens", {
            "model": record["model"], "capability": record["capability"], "invocation_key": record["invocation_key"]})
        try:
            if (value.get("scope") != snapshot["scope"] or value.get("model") != record["model"]
                    or value.get("capability") != record["capability"] or value.get("funding_mode") not in {"live", "test"}
                    or not isinstance(value.get("task_token"), str) or not 16 <= len(value["task_token"]) <= 4096
                    or timestamp(value.get("expires_at")) <= self.clock()): raise ValueError
            _uuid(value.get("invocation_id"))
        except (ValueError, TypeError): raise MediaError() from None
        from .privacy import remember
        remember(self.central.store.root, [value["task_token"]])
        self._current(snapshot)
        return value

    def upload(self, snapshot, task_token, asset, constraints):
        self._current(snapshot)
        value = self._fetch(snapshot, "POST", "/v2/media/assets", task_token=task_token,
                            content=asset["raw"], content_type=asset["mime"], timeout=45)
        self._current(snapshot)
        result = _artifact(value)
        if any(result[k] != asset[k] for k in ("sha256", "size", "mime")) or timestamp(result["expires_at"]) <= self.clock() or result.get("purged_at") is not None: raise ValueError
        props=result["properties"]
        for actual, maximum in (("duration_ms", "max_duration_ms"), ("audio_duration_ms", "max_duration_ms"), ("channels", "max_channels"), ("sample_rate", "max_sample_rate")):
            if actual in props and maximum in constraints and props[actual] > constraints[maximum]: raise ValueError
        if "width" in props and props["width"] * props["height"] > constraints.get("max_pixels", 33554432): raise ValueError
        if constraints.get("require_audio") and not {"channels", "sample_rate"}.issubset(props): raise ValueError
        _verify_media(asset["raw"], result)
        return result["id"]

    def voice(self, snapshot, voice_id):
        _uuid(voice_id)
        result = _voice(self._get(snapshot, "/v2/media/voices/" + voice_id))
        self._current(snapshot)
        if result["id"] != voice_id or timestamp(result["expires_at"]) <= self.clock(): raise ValueError
        return result

    def submit(self, snapshot, record, task_token, body):
        self._current(snapshot)
        value = self._fetch(snapshot, "POST", "/v2/media/jobs", body, task_token=task_token,
                            operation_key=record["operation_key"], timeout=195)
        self._current(snapshot)
        try: row = _job(value)
        except (KeyError, ValueError, TypeError): raise MediaError() from None
        if row["model"] != record["model"] or row["capability"] != record["capability"]: raise MediaError()
        return row

    def job(self, snapshot, job_id, *, timeout=15, deadline=None):
        _uuid(job_id)
        value = self._get(snapshot, "/v2/media/jobs/" + job_id, timeout=timeout, deadline=deadline)
        self._current(snapshot)
        try: row = _job(value)
        except (KeyError, ValueError, TypeError): raise MediaError() from None
        if row["id"] != job_id: raise MediaError()
        return row

    def lookup(self, snapshot, record, *, timeout=15, deadline=None):
        if record["invocation_id"] is None: return None
        query = urlencode({"invocation_id": record["invocation_id"], "operation_key": record["operation_key"]})
        value = self._get(snapshot, "/v2/media/operations?" + query, timeout=timeout, deadline=deadline)
        self._current(snapshot)
        try:
            row = _job(value, artifact_detail=False)
            if row["model"] != record["model"] or row["capability"] != record["capability"] or (record["job_id"] and record["job_id"] != row["id"]): raise ValueError
        except (KeyError, ValueError, TypeError): raise MediaError() from None
        return self.job(snapshot, row["id"], timeout=timeout, deadline=deadline)

    def recoveries(self, session_id=None, turn_id=None):
        # Identity and every operation/job GET share the same finite read budget.
        read_timeout = 10
        deadline = time.monotonic() + 30
        snapshot = self._snapshot(deadline=deadline, read_timeout=read_timeout)
        turn_key = operation_turn_key(session_id, turn_id) if session_id is not None or turn_id is not None else None
        try:
            records, truncated = self.records.rows(operation_owner(snapshot["url"], snapshot["scope"]), turn_key)
        except (OSError, MediaRecordError):
            raise MediaError("media_records_unavailable", 503) from None
        result = []
        for record in records:
            row = {key: record[key] for key in ("operation_key", "model", "capability", "local_status")}
            row["lookup_status"] = "unavailable" if record["invocation_id"] else "not_sent_or_unknown"
            if record["invocation_id"] and time.monotonic() < deadline:
                try:
                    job = self.lookup(snapshot, record, timeout=min(read_timeout, deadline - time.monotonic()), deadline=deadline)
                    row.update(lookup_status="found", job=job)
                except PlatformError as error:
                    if error.status in {401, 403} or error.code == "account_changed": raise
                    if error.status == 404: row["lookup_status"] = "not_found"
                    if time.monotonic() >= deadline: truncated = True
            elif record["invocation_id"]: truncated = True
            result.append(row)
        self._current(snapshot)
        return self.central._safe({"recoveries": result, "truncated": truncated})


def _save_image(root, path, raw, metadata, *, publish_guard):
    from easel.harnesses.pi_tools import _parts, _directory
    from PIL import Image
    parts = _parts(path)
    suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[metadata["mime"]]
    if (len(parts) < 2 or parts[0] != "outputs" or not parts[-1].lower().endswith(suffix)
            or len(raw) != metadata["size"] or sha256(raw).hexdigest() != metadata["sha256"]
            or any(metadata["properties"][key] != 1024 for key in ("width", "height"))): raise ValueError
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                if (image.format != metadata["properties"]["format"] or image.size != (
                        metadata["properties"]["width"], metadata["properties"]["height"])
                        or getattr(image, "n_frames", 1) != 1): raise ValueError
                image.verify()
            with Image.open(io.BytesIO(raw)) as image: image.load()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, SyntaxError):
        raise ValueError("Invalid decoded image") from None
    with _directory(root, parts[:-1], create=True) as directory:
        temporary = ".easel-media-" + str(uuid.uuid4()) + ".part"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            # link() is an atomic no-replace publication, including a raced target.
            with publish_guard():
                os.link(temporary, parts[-1], src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
                os.fsync(directory)
        finally:
            os.unlink(temporary, dir_fd=directory)
    return "/".join(parts)


class MediaHost:
    def __init__(self, request, *, client=None):
        self.request = request
        self.client = client or MediaClient(request.cwd)
        self.records = self.client.records
        self._models = None
        self._stopped = threading.Event()
        self._publish_lock = threading.Lock()

    def stop(self):
        with self._publish_lock:
            self._stopped.set()

    def _active(self):
        if self._stopped.is_set(): raise MediaError("media_unavailable", 409)

    def _accepted(self):
        self._active()
        self._context()
        request = self.request
        return self.client._snapshot(expected_scope=dict(request.central_scope), expected_url=request.platform_url)

    def _context(self):
        request = self.request
        if (request.service_source != "subscription" or request.subscription_backend != "central"
                or request.sandbox != "workspace-write" or not request.session_key or not request.turn_id
                or not request.invocation_key or not isinstance(request.central_scope, dict)
                or set(request.central_scope) != {"user_id", "product_id", "tenant_id", "billing_account_id", "session_id", "authorization_epoch"}
                or type(request.central_scope.get("authorization_epoch")) is not int
                or request.central_scope["authorization_epoch"] < 0
                or not isinstance(request.central_scope.get("session_id"), str)
                or not 1 <= len(request.central_scope["session_id"]) <= 160
                or any(ord(c) < 32 for c in request.central_scope["session_id"])
                or request.account_id != request.central_scope.get("user_id")):
            raise MediaError("media_unavailable", 403)
        operation_owner(request.platform_url, request.central_scope)
        operation_turn_key(request.session_key, request.turn_id)

    @contextmanager
    def _publication(self, snapshot):
        with self._publish_lock:
            self._active()
            with self.client.publication(snapshot):
                yield

    def models(self):
        try:
            snapshot = self._accepted()
            self._models = self.client.catalog(snapshot=snapshot)["models"]
            return self._models
        except (HarnessError, OSError, ValueError):
            self._models = []
            return []

    def _result(self, job, *, snapshot, **extra):
        with self._publication(snapshot):
            safe = {"job": job, **extra}
            return {"ok": True, "text": json.dumps(safe, ensure_ascii=False), **safe}

    def _owned(self, snapshot, job_id):
        _uuid(job_id)
        owner = operation_owner(snapshot["url"], snapshot["scope"])
        record = self.records.owned(owner, job_id=job_id)
        job = self.client.job(snapshot, job_id)
        if job["model"] != record["model"] or job["capability"] != record["capability"]: raise MediaError()
        return record, job

    def execute(self, name, arguments, tool_id):
        record = None
        try:
            if not isinstance(arguments, dict): raise ValueError
            self._active()
            self._context()
            if name == "create_media":
                if set(arguments) - {"model", "capability", "prompt", "parameters", "assets", "voice_id"} or "model" not in arguments: raise ValueError
                prompt = arguments.get("prompt", "")
                if not isinstance(prompt, str) or len(prompt.encode()) > 65536: raise ValueError
                snapshot = self._accepted()
                models = self.client.catalog(snapshot=snapshot)["models"]
                model = next((item for item in models if item["id"] == arguments["model"]), None)
                if model is None: raise ValueError
                capability = arguments.get("capability", "image.generate")
                if capability != model["capability"]: raise ValueError
                if capability not in {"audio.transcribe", "voice.enroll", "image.variation"} and not prompt.strip(): raise ValueError
                default = {"n": 1, "size": "1024x1024"} if capability.startswith("image.") else {}
                parameters = checked_parameters(arguments.get("parameters", default), model["parameters"])
                paths = arguments.get("assets", [])
                if not isinstance(paths, list) or any(not isinstance(p, str) for p in paths) or len(set(paths)) != len(paths) or len(paths) > model["limits"]["max_assets"]: raise ValueError
                if capability in {"image.edit", "image.variation", "audio.transcribe", "voice.enroll"} and not paths: raise ValueError
                incoming = model["limits"].get("input_media", {"mime_types": ["image/png", "image/jpeg", "image/webp"], "max_files": model["limits"]["max_assets"]})
                if len(paths) > incoming["max_files"]: raise ValueError
                assets = [_freeze_media(self.request.cwd, p, maximum=model["limits"]["max_input_bytes"], allowed=incoming["mime_types"]) for p in paths]
                if sum(a["size"] for a in assets) > min(MAX_MEDIA, model["limits"]["max_input_bytes"]): raise ValueError
                body = {"model": model["id"], "capability": capability, "prompt": prompt, "assets": [], "parameters": parameters}
                if "voice_id" in arguments:
                    if capability not in {"voice.synthesize", "audio.speech"}: raise ValueError
                    body["voice_id"] = self.client.voice(snapshot, arguments["voice_id"])["id"]
                if capability == "voice.synthesize" and "voice_id" not in body: raise ValueError
                if len(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()) > model["limits"]["max_input_bytes"]: raise ValueError
                owner = operation_owner(snapshot["url"], snapshot["scope"])
                record, fresh = self.records.claim(owner=owner, scope=snapshot["scope"], session_id=self.request.session_key,
                    turn_id=self.request.turn_id, tool_id=tool_id, model=model["id"], capability=capability, body_hash=digest(body if not assets else {"body":body,"assets":[{k:a[k] for k in ("sha256","size","mime")} for a in assets]}))
                if not fresh:
                    job = self.client.lookup(snapshot, record)
                    if job is None: return {"ok": False, "text": "原媒体请求状态待确认", "local_status": record["local_status"], "operation_key": record["operation_key"]}
                    record = self.records.update(record, local_status="known", job_id=job["id"])
                    return self._result(job, snapshot=snapshot, operation_key=record["operation_key"])
                record = self.records.update(record, local_status="mint_sending")
                self._active()
                try: credential = self.client.mint(snapshot, record)
                except BaseException:
                    record = self.records.update(record, local_status="mint_unknown")
                    raise
                record = self.records.update(record, local_status="minted", invocation_id=credential["invocation_id"])
                self._active()
                if assets:
                    record = self.records.update(record, local_status="upload_sending")
                    try:
                        for asset in assets:
                            self._active()
                            body["assets"].append(self.client.upload(snapshot, credential["task_token"], asset, incoming))
                        record = self.records.update(record, local_status="uploaded")
                    except BaseException:
                        record = self.records.update(record, local_status="upload_unknown")
                        raise
                self._active()
                record = self.records.update(record, local_status="job_sending")
                try: job = self.client.submit(snapshot, record, credential["task_token"], body)
                except BaseException:
                    record = self.records.update(record, local_status="unknown")
                    raise
                record = self.records.update(record, local_status="known", job_id=job["id"])
                self._active()
                return self._result(job, snapshot=snapshot, operation_key=record["operation_key"])
            if name not in {"get_media_job", "cancel_media_job", "download_media_artifact"}: raise ValueError
            snapshot = self.client._snapshot()
            if operation_owner(snapshot["url"], snapshot["scope"]) != operation_owner(self.request.platform_url, self.request.central_scope):
                raise MediaError("account_changed", 409)
            if name == "get_media_job":
                if set(arguments) == {"operation_key"}:
                    record = self.records.owned(operation_owner(snapshot["url"], snapshot["scope"]), operation_key=_uuid(arguments["operation_key"]))
                    job = self.client.lookup(snapshot, record)
                    if job is None: return {"ok": False, "text": "原媒体请求状态待确认", "local_status": record["local_status"]}
                    self.records.update(record, local_status="known", job_id=job["id"])
                elif set(arguments) == {"job_id"}: record, job = self._owned(snapshot, arguments["job_id"])
                else: raise ValueError
                return self._result(job, snapshot=snapshot)
            if name == "cancel_media_job":
                if set(arguments) != {"job_id"}: raise ValueError
                record, _ = self._owned(snapshot, arguments["job_id"])
                snapshot = self._accepted()
                self._active()
                value = self.client._fetch(snapshot, "POST", "/v2/media/jobs/" + arguments["job_id"] + "/cancel", {})
                self.client._current(snapshot)
                job = _job(value)
                if job["id"] != arguments["job_id"] or job["model"] != record["model"] or job["capability"] != record["capability"]: raise ValueError
                return self._result(job, snapshot=snapshot)
            if set(arguments) != {"job_id", "artifact_id", "path"}: raise ValueError
            from easel.harnesses.pi_tools import _parts
            parts = _parts(arguments["path"])
            if len(parts) < 2 or parts[0] != "outputs": raise ValueError
            record, job = self._owned(snapshot, arguments["job_id"])
            artifact_id = _uuid(arguments["artifact_id"])
            metadata = next((item for item in job["artifacts"] if item["id"] == artifact_id), None)
            if (metadata is None or metadata.get("purged_at") is not None
                    or timestamp(metadata["expires_at"]) <= self.client.clock()
                    or metadata.get("job_id", job["id"]) != job["id"]
                    or (job["capability"].startswith("image.") and any(metadata["properties"].get(key) != 1024 for key in ("width", "height")))): raise ValueError
            current = _artifact(self.client._get(snapshot, "/v2/media/artifacts/" + artifact_id))
            if (any(current[k] != metadata[k] for k in ("id", "sha256", "size", "mime", "properties", "expires_at"))
                    or current.get("job_id", job["id"]) != job["id"] or current.get("purged_at") is not None): raise ValueError
            raw, headers = self.client._get(snapshot, "/v2/media/artifacts/" + artifact_id + "/content",
                binary=True, maximum=metadata["size"], timeout=45)
            self._active()
            self.client._current(snapshot)
            if (headers.get("content-type", "").split(";", 1)[0] != metadata["mime"]
                    or headers.get("etag") != '"' + metadata["sha256"] + '"'
                    or headers.get("content-encoding", "identity").lower() != "identity"
                    or "content-length" in headers and headers["content-length"] != str(metadata["size"])): raise ValueError
            saver = _save_image if job["capability"].startswith("image.") else _save_media
            path = saver(self.request.cwd, arguments["path"], raw, metadata,
                               publish_guard=lambda: self._publication(snapshot))
            self.records.update(record, local_status="downloaded", path=path)
            with self._publication(snapshot):
                return {"ok": True, "text": "文件已保存：" + path, "path": path, "artifact": metadata, "job": job}
        except (HarnessError, OSError, ValueError, TypeError, KeyError, RecursionError) as error:
            messages = {
                "Media record capacity reached": "本机生图记录已满",
                "Media record lookup incomplete": "媒体记录暂不可查询",
            }
            message = messages.get(str(error), "媒体操作未完成，请查询原任务") if isinstance(error, MediaRecordError) else "媒体操作未完成，请查询原任务"
            result = {"ok": False, "text": message}
            if record is not None:
                result.update(operation_key=record["operation_key"], local_status=(
                    "unknown" if record["local_status"] == "job_sending" else record["local_status"]))
            return result
