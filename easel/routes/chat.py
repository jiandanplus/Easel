"""Chat endpoints: submission, streaming, replay, questions, stop/withdraw, attachments and skills.

Moved out of web/app.py. Shared paths and helpers are read from the core app
module (`_core`) at call time; state owned by this domain lives here.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import mimetypes
import os
import re
import stat
import subprocess
import sys
import time
import urllib.request
import uuid
from contextvars import ContextVar
from functools import wraps
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from easel.direct_api import DirectAPIError, history_path, SYSTEM_PROMPT
from easel.gateway_endpoint import chat_completions_url, port_source
from easel.harnesses import store as harness_store
from easel.harnesses.base import HarnessError
from easel.persona import chat_turn_message, persona_prefix
from easel.platform.transport import require_local_http
from easel.routes import outputs as _outputs
from easel.routes.schemas import AttachmentRef, ChatRequest
from easel.service_contracts import ServiceRoute, TaskContext
from easel.timeouts import TIMEOUT_PRODUCE
from easel.workbench import (digest as _workbench_digest, identifier as _workbench_identifier, WorkbenchError)

router = APIRouter()
_core = None

# 问答题桥接的一次性诊断标记：连接失败/旧版本无 question RPC 的告警每进程只打一次，
# 避免每开一个新会话就在后端刷一行同样的错（用户反馈的噪音）。
_QBRIDGE_WARNED: set[str] = set()


# 进程级熔断：一旦确认桥接不可用（旧版本无 question RPC、或 connect 持续失败如
# NOT_PAIRED/scope-upgrade），就彻底停掉桥接，后续每轮直接跳过——不再连接，也就不再
# 每轮在网关上触发新的配对/权限申请。恢复需重启 easel web。
_QBRIDGE_DISABLED = False


def _qbridge_warn_once(key: str, message: str) -> None:
    if key in _QBRIDGE_WARNED:
        return
    _QBRIDGE_WARNED.add(key)
    print(message, file=sys.stderr, flush=True)


# gateway 进程把原始事件流（token/thinking/收尾）写到的**单个共享文件**。
# 关键：`openclaw agent` 只是瘦客户端，没有 --raw-stream 标志——只有常驻 gateway 按它自己
# 的 OPENCLAW_RAW_STREAM/OPENCLAW_RAW_STREAM_PATH 写这个文件（见 scripts/gateway.sh）。
# web 侧 tail 它做流式；默认值必须与 gateway.sh 里 EASEL_RAW_STREAM_PATH 的默认一致。
SHARED_RAW_STREAM = Path(os.environ.get("EASEL_RAW_STREAM_PATH", "/tmp/easel-raw-stream.jsonl"))


_HTTP_READY_CACHE: dict = {"at": -1e9, "ok": False}


_HTTP_READY_TTL = 60.0          # 探针要打一次真端点，别每轮都付这个 RTT


def _gateway_http_ready(force: bool = False) -> bool:
    """探测 gateway 的 /v1/chat/completions 到底挂没挂上。

    **不能探 /v1/models**：那个路径会被 gateway 控制台 SPA 的 catch-all 接走，端点没开也照样
    返回 200（body 是 OpenClaw Control 的 HTML 首页）—— 探了等于没探，只要网关活着就恒为 True。
    这里直接打真端点：openclaw 要求 gateway.http.endpoints.chatCompletions.enabled === true
    才挂这条路由（默认 false），没开就是 404；开了则因为 body 缺 messages 返回 400。用 400 当
    "端点在"的证据，既走完整条路由又不消耗一次模型调用。
    """
    now = time.monotonic()
    if not force and now - _HTTP_READY_CACHE["at"] < _HTTP_READY_TTL:
        return bool(_HTTP_READY_CACHE["ok"])
    ok = False
    try:
        import httpx  # noqa: F401  HTTP 路径全靠它做 SSE；没装就当端点不可用，回退 CLI
        rq = urllib.request.Request(
            chat_completions_url(), data=b"{}",
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(rq, timeout=3):
                ok = False       # 空 body 还给 200 说明这不是我们要的端点，不敢用
        except urllib.error.HTTPError as e:
            ok = e.code == 400   # 400 Missing user message ⇒ 端点在；404 ⇒ 没开
    except Exception:  # noqa: BLE001
        ok = False
    _HTTP_READY_CACHE.update(at=now, ok=ok)
    return ok




def _persona_prefix(persona: str | None) -> str:
    """把画像作为消息前缀内联（复用 easel.persona，与 CLI/skill 同源）。"""
    return persona_prefix(persona)


def _web_output_redactor(emit):
    """One transient boundary before Web queues, persisted events and recovery."""
    from easel.harnesses.secrets import StreamRedactor
    from easel.platform.privacy import sensitive_values
    values = [value.strip().strip('"').strip("'")
              for key, value in _core._read_env().items()
              if _core._SECRETISH_RE.search(key) and len(value.strip()) >= 8]
    return StreamRedactor(values, emit, additional=lambda: sensitive_values(_core.PROJECT_ROOT))


class AttachmentValidationRequest(BaseModel):
    session_id: str = Field(max_length=160)
    attachments: list[AttachmentRef] = Field(default_factory=list, max_length=32)


def _validated_attachment(attachment, session_id):
    """Uploads belong to one session; explicit output references to one source."""
    rel = Path(attachment.path)
    if rel.is_absolute() or ".." in rel.parts or "\\" in attachment.path:
        raise HTTPException(403, "附件不可用")
    normalized = rel.as_posix()
    if normalized.startswith("_inbox/"):
        scope = _core._attachment_scope(session_id)
        if (len(rel.parts) != 4 or rel.parts[1] != scope
                or attachment.id != _core._attachment_id(scope, normalized)):
            raise HTTPException(403, "附件不属于当前会话")
        full = _outputs._safe_output_target(normalized)
        if not full.is_file():
            raise HTTPException(404, "附件不存在")
        return full
    source = _core._workbench_contract()["source_scope"]
    expected = _core._workbench_state().reference_id(normalized, source)
    import hmac
    if not hmac.compare_digest(attachment.id, expected):
        raise HTTPException(403, "附件不可用")
    return _outputs._safe_output_path(normalized)


@router.post("/api/workbench/attachments/validate")
async def api_workbench_attachments(req: AttachmentValidationRequest):
    require_local_http()
    try:
        _workbench_identifier(req.session_id)
    except WorkbenchError:
        raise HTTPException(400, "无效的会话标识") from None
    items = []
    for attachment in req.attachments:
        state = "available"
        try:
            _validated_attachment(attachment, req.session_id)
        except HTTPException as error:
            state = "missing" if error.status_code == 404 else "forbidden"
        items.append({**attachment.model_dump(), "state": state})
    return {"version": 1, "session_id": req.session_id, "attachments": items}


@router.get("/api/artifacts/reference")
async def api_artifact_reference(path: str, session_id: str | None = None):
    require_local_http()
    if len(path) > 2048 or "\\" in path:
        raise HTTPException(400, "无效的文件标识")
    if session_id is not None:
        try:
            _workbench_identifier(session_id)
        except WorkbenchError:
            raise HTTPException(400, "无效的会话标识") from None
    full = _outputs._safe_output_path(path)
    if _outputs._is_protected(full):
        raise HTTPException(403, "文件不可用")
    normalized = full.relative_to(_core.OUTPUTS_DIR.resolve()).as_posix()
    source = _core._workbench_contract()["source_scope"]
    return JSONResponse({"id": _core._workbench_state().reference_id(normalized, source), "path": normalized,
                         "name": full.name, "size": full.stat().st_size,
                         "kind": "text" if full.suffix.lower() in _core.TEXT_EXTS else "file", "state": "available"},
                        headers={"Cache-Control": "no-store"})


@router.get("/api/chat/models")
async def api_chat_models(refresh: bool = False, session_id: str | None = None):
    from easel.chat_models import catalogs
    config = _core._harness_settings()
    try:
        if session_id:
            if len(session_id) > 256 or any(ord(c) < 32 for c in session_id):
                raise HarnessError("无效的会话标识")
            config = {**config, "runtime": _core._chat_backend(session_id)}
            record = harness_store.binding(_core.SESSIONS_DIR, session_id)
            if record and (record["service_source"] != config["service_source"]
                           or record["execution_target"] != config["execution_target"]):
                raise HarnessError("此对话属于其他服务来源或执行位置，请新建对话；原历史保持不变")
            if record and config["service_source"] == "subscription":
                backend = config.get("subscription_backend", "legacy")
                if record.get("subscription_backend", "legacy") != backend:
                    raise HarnessError("此对话属于其他订阅渠道，请新建对话；原历史保持不变")
                if backend == "central":
                    from easel.platform.central import CentralClient
                    client = CentralClient(_core.PROJECT_ROOT)
                    with client.store.locked():
                        state = client._state()
                    auth = state.get("auth") or {}
                    if (record.get("platform_url") != state.get("url")
                            or record.get("central_scope") != auth.get("scope")
                            or record.get("account_id") != auth.get("scope", {}).get("user_id")):
                        raise HarnessError("此对话属于其他订阅账号或平台，请新建对话；原历史保持不变")
                else:
                    from easel.platform.client import PlatformClient
                    client = PlatformClient(_core.PROJECT_ROOT)
                    with client.store.locked():
                        state = client.store.read()
                    if record.get("account_id") != (state.get("auth") or {}).get("account", {}).get("id"):
                        raise HarnessError("此对话属于其他订阅账号，请新建对话；原历史保持不变")
        result = await asyncio.to_thread(catalogs.catalog, _core.PROJECT_ROOT, config, _core.read_settings(_core.ENV_FILE), refresh=refresh)
    except (HarnessError, HTTPException) as error:
        result = {"revision": "", "source_revision": "", "expires_in": 0,
                  "runtime": config["runtime"], "service_source": config["service_source"],
                  "subscription_backend": config.get("subscription_backend", "legacy"),
                  "selected_model": "", "switchable": False, "ready": False, "models": [],
                  "reason": _core._scrub_secrets(str(error.detail if isinstance(error, HTTPException) else error))}
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


def _attachment_context(req: ChatRequest) -> str:
    """Validate attachment ownership and build an Agent-only attachment manifest."""
    if not req.attachments:
        return ""
    if not req.sessionId:
        raise HTTPException(400, "附件必须绑定到会话")

    scope = _core._attachment_scope(req.sessionId)
    rows: list[str] = []
    seen: set[str] = set()
    for attachment in req.attachments:
        full = _validated_attachment(attachment, req.sessionId)
        normalized = full.relative_to(_core.OUTPUTS_DIR.resolve()).as_posix()
        if normalized in seen:
            continue
        seen.add(normalized)
        rows.append(f"- outputs/{normalized}")

    return (
        "〔系统附件清单，仅供本轮执行，不要向用户复述文件上传过程或内部路径〕\n"
        "只允许使用下列当前会话附件；禁止扫描、枚举或猜测 outputs/_inbox 中的其他文件：\n"
        + "\n".join(rows)
        + "\n需要纳入内容项目时，将清单内文件复制到 outputs/<项目>/assets/ 后再使用；"
          "保留 inbox 原件，确保重试仍可复现。"
    )


def _chat_message(req: ChatRequest) -> str:
    context = _attachment_context(req)
    message = req.message.strip()
    if context:
        message = f"{message}\n\n{context}" if message else context
    if not message:
        raise HTTPException(400, "消息不能为空")
    return chat_turn_message(message, req.persona)


def _direct_chat_content(req: ChatRequest) -> tuple[str | list, str]:
    _attachment_context(req)  # Reuse ownership/path validation before reading any attachment.
    message = req.message.strip()
    if not message and not req.attachments:
        raise HTTPException(400, "消息不能为空")
    system = SYSTEM_PROMPT
    if req.persona:
        if not _core._valid_persona_name(req.persona) or not _core.profile_exists(req.persona):
            raise HTTPException(400, "画像不存在或名称无效")
        system += "\n\n当前画像：\n" + _core.load_profile_text(req.persona)
    content = [{"type": "text", "text": message or "请分析这些附件"}]
    for attachment in req.attachments:
        path = _outputs._safe_output_target(attachment.path)
        if path.suffix.lower() in _core.TEXT_EXTS:
            if path.stat().st_size > 65536:
                raise HTTPException(413, "API 直连模式文本附件不能超过 64 KiB")
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeError:
                raise HTTPException(400, "文本附件需要 UTF-8 编码") from None
            content.append({"type": "text", "text": f"附件 {attachment.name}：\n{text}"})
        elif path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
            if path.stat().st_size > 4 * 1024 * 1024:
                raise HTTPException(413, "API 直连模式图片附件不能超过 4 MiB")
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            mime = mimetypes.guess_type(path.name)[0] or "image/png"
            content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}})
        else:
            raise HTTPException(400, "API 直连模式支持文本和图片附件；此附件需要文件处理工具")
    return (content if req.attachments else message), system


def _resolve_transport(sk: str) -> str:
    """决定这一轮走哪条传输层，并保证同一会话**永不中途换边**。

    换边的代价是静默丢光上下文，不是慢一点：CLI 路径用 `--session-id`（uuid5(sk)）把 transcript
    钉死，而 /v1/chat/completions **压根不读 x-openclaw-session-id** —— openclaw 2026.6.11 全量
    实测：该 header 只有 MCP 端点和对上游供应商的出站请求会用，OpenAI 兼容端点只认
    x-openclaw-session-key，transcript 文件名由网关自己挑。于是同一个 web 会话在两条路径下落在
    两份不同的 jsonl 上。实测拿 CLI 去续一个已有两轮 HTTP 历史的会话，agent 回答"无历史"。

    所以判定顺序（都不依赖内存态，web 重启后依然成立）：
      1) uuid5 那份 transcript 已落盘 → 这会话是 CLI 起的，继续 cli；
      2) 有 http 钉子文件 → 继续 http；
      3) 两者都没有 → 新会话，按总开关 + 真探针决定。
    """
    if (_core.OPENCLAW_SESSIONS_DIR / f"{_core._openclaw_session_id(sk)}.jsonl").is_file():
        return "cli"
    try:
        if _core._transport_pin_file(sk).read_text(encoding="utf-8").strip() == "http":
            return "http"
    except OSError:
        pass
    # 3) 新会话：按总开关 + 真探针；探不通不报错，退回 CLI 但要吭一声
    if _core.CHAT_TRANSPORT == "http" and _gateway_http_ready():
        return "http"
    if _core.CHAT_TRANSPORT == "http":
        _warn_http_transport_unavailable()
    return "cli"


_HTTP_FALLBACK_WARNED = False


def _warn_http_transport_unavailable() -> None:
    """HTTP 直连不可用时只喊一次，并把**探的到底是哪个端点**写出来。

    以前这里是静默退回：端口写死探错地方，表现只是「每轮慢 3s」，没人会去查 —— 这次网关
    端口错配就是这么藏住的（面板离线 + 静默退 CLI）。
    """
    global _HTTP_FALLBACK_WARNED
    if _HTTP_FALLBACK_WARNED:
        return
    _HTTP_FALLBACK_WARNED = True
    print(f"[chat-transport] HTTP 直连探不通 {chat_completions_url()}（端口来自 {port_source()}），"
          f"已退回 CLI 传输（每轮多付一次客户端冷启动）。"
          f"查：{_core.PROJECT_ROOT}/scripts/gateway.sh status", file=sys.stderr, flush=True)


def _pin_transport(sk: str, kind: str) -> None:
    """把会话钉在某条传输层上。只需钉 http —— cli 侧由 uuid5 transcript 文件自证。"""
    if kind != "http":
        return
    try:
        _core.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        _core._transport_pin_file(sk).write_text("http", encoding="utf-8")
    except OSError:
        pass


def _job_event_file(turn_id: str) -> Path:
    """Per-turn append-only event log used to resume SSE without restarting the agent."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", turn_id)[:160]
    # Preserve old replay files while avoiding sanitizer collisions for new IDs.
    if ":" in turn_id and not (_core.SESSIONS_DIR / "jobs" / f"{safe}.jsonl").exists():
        safe = hashlib.sha256(turn_id.encode()).hexdigest()
    return _core.SESSIONS_DIR / "jobs" / f"{safe}.jsonl"


def _read_job_events(turn_id: str, after: int = 0) -> list[dict]:
    path = _job_event_file(turn_id)
    events = []
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 16 * 1024 * 1024:
                return []
            raw = stream.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            return []
        for index, line in enumerate(raw.splitlines()):
            if index >= 100000:
                return []
            event = json.loads(line)
            if (not isinstance(event, dict) or type(event.get("id")) is not int
                    or event["id"] <= 0 or not isinstance(event.get("event"), str)):
                return []
            if event["id"] > after:
                events.append(event)
    except (OSError, ValueError, UnicodeError):
        return []
    return events


def _raw_event_for_run(line: str, expected_run_id: str | None) -> dict | None:
    """Parse one OpenClaw raw event and reject events from other runs.

    The gateway multiplexes every run into one shared raw-stream file, and its
    events carry `runId` (not `sessionId`). A turn latches onto its own runId —
    the first event seen after the turn starts — and must ignore any event with
    a different runId. `expected_run_id=None` means not-yet-latched → accept, so
    the caller can latch from `event['runId']`.
    """
    line = line.strip()
    if not line:
        return None
    try:
        event = json.loads(line)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(event, dict):
        return None
    if expected_run_id is not None:
        rid = event.get("runId")
        if rid is not None and rid != expected_run_id:
            return None
    return event


_WORKBENCH_TURNS: dict = {}


def _workbench_turn_live(turn_id):
    task = _WORKBENCH_TURNS.get((str(_core.SESSIONS_DIR), turn_id))
    return task is not None and not task.done()


def _workbench_receipt(session_id, turn_id):
    try:
        return _core._workbench_state().owned(session_id, turn_id, _core._workbench_contract()["source_scope"])
    except WorkbenchError as error:
        raise HTTPException(error.status, str(error)) from None


def _workbench_completion(session_id, turn_id):
    """Only a durable exact-turn snapshot can confirm execution termination."""
    result = {"version": 1, "session_id": session_id, "turn_id": turn_id,
              "state": "unknown", "clean_end": False, "stop_reason": "unknown"}
    try:
        snapshot = _core._workbench_state().snapshot(turn_id)
    except WorkbenchError:
        snapshot = None
    if snapshot is not None and snapshot.get("status") == "done":
        clean = snapshot.get("clean_end") is True
        reason = snapshot.get("stop_reason", "unknown")
        result.update(clean_end=clean, stop_reason=reason,
                      state="completed" if clean else "stopped" if reason in ("stopped", "user_stopped") else "failed")
    return result


def _snapshot_artifacts(snapshot, session_id, turn_id):
    """Return only additive exact-turn references, never infer them from text."""
    from easel.harnesses.pi_tools import validate_artifact_descriptor
    values = snapshot.get("artifacts")
    if not isinstance(values, list):
        return []
    found = {}
    fields = {"version", "path", "name", "size", "sha256", "session_id", "turn_id"}
    for value in values[:16]:
        if (type(value) is not dict or set(value) != fields
                or value.get("session_id") != session_id or value.get("turn_id") != turn_id):
            continue
        serialized = json.dumps(value, ensure_ascii=False)
        if _core._scrub_secrets(serialized) != serialized:
            continue
        base = {key: value[key] for key in fields - {"session_id", "turn_id"}}
        if validate_artifact_descriptor(base) is None:
            continue
        found[value["path"]] = dict(value)
    return list(found.values())


@router.get("/api/chat/turns/{session_id}/{turn_id}")
async def api_chat_turn(session_id: str, turn_id: str):
    require_local_http()
    record = _workbench_receipt(session_id, turn_id)
    result = {"version": 1, "session_id": session_id, "turn_id": turn_id, "state": "unknown", "accepted": record is not None}
    if record is None:
        return JSONResponse(result, headers={"Cache-Control": "no-store"})
    result.update(_core._workbench_state().public_receipt(record))
    if record["decision"] == "rejected":
        result.update(state="rejected", accepted=False, not_dispatched=True)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})
    try:
        snapshot = _core._workbench_state().snapshot(turn_id)
    except WorkbenchError:
        snapshot = None
    if snapshot is not None:
        for field in ("text", "clean_end", "stop_reason", "central_recovery"):
            if field in snapshot:
                result[field] = snapshot[field]
        if "artifacts" in snapshot:
            result["artifacts"] = _snapshot_artifacts(snapshot, session_id, turn_id)
        if snapshot.get("status") == "done":
            result["state"] = ("completed" if snapshot.get("clean_end") is True else
                               "stopped" if snapshot.get("stop_reason") in ("stopped", "user_stopped") else "failed")
        elif _workbench_turn_live(turn_id):
            result["state"] = "running"
    elif _workbench_turn_live(turn_id):
        result["state"] = "accepted"
    batch = _read_job_events(turn_id)
    result["last_event_id"] = max((int(event.get("id", 0)) for event in batch), default=0)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


class WithdrawRequest(BaseModel):
    model_config = {"extra": "forbid"}
    request_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


@router.post("/api/chat/turns/{session_id}/{turn_id}/withdraw")
async def api_chat_withdraw(session_id: str, turn_id: str, body: WithdrawRequest, request: Request):
    """An explicit local withdrawal fences an undecided submission permanently."""
    require_local_http()
    origin = request.headers.get("origin")
    expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
    if origin not in (None, expected) or request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "请求来源不允许")
    try:
        _workbench_identifier(session_id)
        _workbench_identifier(turn_id)
        ledger = _core._workbench_state()
        scope = _core._workbench_contract()["source_scope"]
        record = ledger.owned(session_id, turn_id, scope)
        if record is None and _job_event_file(turn_id).exists():
            # An old event log cannot be converted to a safe rejection merely
            # because it predates versioned durable acceptance decisions.
            record = ledger.owned(session_id, turn_id, scope)
            if record is None:
                raise WorkbenchError("原任务状态待确认", 409)
        if record is not None and record["request_hash"] != body.request_hash:
            raise WorkbenchError("任务标识已用于其他内容", 409)
        # A racing accept keeps its original receipt; a winning rejection is
        # permanent. Neither branch changes money or dispatches another model.
        ledger.reject(session_id, turn_id, scope, body.request_hash)
    except WorkbenchError as error:
        raise HTTPException(error.status, str(error)) from None
    return await api_chat_turn(session_id, turn_id)


async def _run_direct_chat(req: ChatRequest, sk: str, content, system: str, emit, *,
                           context: TaskContext | None = None, settings: dict | None = None) -> str:
    """Run one API turn with the same stop/recovery/session locking as Gateway chat."""
    if sk in _core._SYNC_SESSIONS and _core._SYNC_SESSIONS[sk][1] is not context:
        raise HTTPException(409, "这个会话上一条还在运行，请稍后再试")
    pk = f"web:{sk}"
    turn_id = req.turnId or uuid.uuid4().hex
    full_text = []
    clean_end = False
    reason = "error"
    error_message = ""
    lock = _core._session_lock(sk)
    xlock = _core._CrossProcLock(sk)
    proc = _core._GatewayHttpProc()
    acquired = False
    claimed = False
    locked = False
    settings = dict(settings if settings is not None else _core.read_settings(_core.ENV_FILE))
    context = context or await _core._accept_task_context(req, "api", _core._harness_settings(), settings)
    settings["EASEL_DIRECT_API_MODEL"] = context.model
    task = asyncio.current_task()
    _core._DIRECT_CHAT_TASKS.add(task)
    if lock.locked():
        emit("activity", "⏳ 这个会话上一条还在跑，排队等它结束…")
    try:
        await lock.acquire()
        locked = True
        deadline = time.monotonic() + min(_core.TIMEOUT_CHAT, 300)
        while True:
            acquired = xlock.acquire(0)
            if acquired:
                break
            xlock.release()
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.2)
        if not acquired:
            raise DirectAPIError("这个会话正在另一个窗口运行，请稍候再试")
        # Existing OpenClaw conversations have different histories. Require a new
        # session rather than silently discard their context when switching modes.
        if (_core._transport_pin_file(sk).is_file()
                or (_core.OPENCLAW_SESSIONS_DIR / f"{_core._openclaw_session_id(sk)}.jsonl").is_file()
                or (_core.OPENCLAW_SESSIONS_DIR / f"{_core._openclaw_session_id(pk)}.jsonl").is_file()):
            raise DirectAPIError("此会话属于 OpenClaw，请新建会话使用 API 直连")
        _core._bind_task(sk, context)
        _core._save_turn(pk, "running", "", {"turn_id": turn_id, "route": context.metadata()})
        claimed = True
        proc._task = asyncio.current_task()
        _core._RUNNING_CHAT[sk] = proc
        emit("activity", "🧠 正在通过 API 思考…")

        async def consume():
            nonlocal clean_end, reason
            async for kind, text in _core.stream_chat(
                    settings, content, system=system,
                    history_file=history_path(_core.SESSIONS_DIR, sk), timeout=_core.TIMEOUT_CHAT,
                    **({"reasoning_effort": context.reasoning_effort, "reasoning_efforts": context.reasoning_efforts}
                       if context.reasoning_effort else {})):
                if kind == "finish":
                    clean_end, reason = True, text
                else:
                    if kind == "token":
                        full_text.append(text)
                    emit(kind, text)

        await asyncio.wait_for(consume(), timeout=_core.TIMEOUT_CHAT)
    except asyncio.CancelledError:
        reason = "user_stopped" if sk in _core._STOPPED_CHAT else "cancelled"
        if reason != "user_stopped":
            raise
    except (DirectAPIError, HarnessError, asyncio.TimeoutError) as error:
        error_message = str(error) or "API 请求超时"
        emit("error", error_message)
    except OSError:
        error_message = "会话历史保存失败，请检查项目 outputs 目录权限"
        emit("error", error_message)
    finally:
        proc.finish()
        snapshot = "".join(full_text) + (f"\n\n❌ {error_message}" if error_message else "")
        try:
            if claimed:
                _core._save_turn(pk, "done", snapshot, {
                    "turn_id": turn_id, "clean_end": clean_end, "stop_reason": reason, "route": context.metadata(),
                })
        finally:
            if _core._RUNNING_CHAT.get(sk) is proc:
                _core._RUNNING_CHAT.pop(sk, None)
            if claimed:
                _core._STOPPED_CHAT.discard(sk)
            xlock.release()
            if locked:
                lock.release()
            _core._DIRECT_CHAT_TASKS.discard(task)
    return "".join(full_text)


@router.get("/api/chat/last/{session_id}")
async def api_chat_last(session_id: str, turn_id: str | None = None):
    """取某会话最近一轮的完整结果（SSE 断线后前端据此取回，避免丢结果）。"""
    f = _core._turn_file(f"web:{session_id}")
    if not f.is_file():
        return {"status": "none", "text": ""}
    try:
        payload = json.loads(f.read_text(encoding="utf-8"))
        if turn_id and payload.get("turn_id") != turn_id:
            return {"status": "stale", "text": "", "turn_id": payload.get("turn_id")}
        if "artifacts" in payload:
            try:
                owned = _workbench_receipt(session_id, payload.get("turn_id"))
            except (HTTPException, TypeError, ValueError):
                owned = None
            payload["artifacts"] = (_snapshot_artifacts(payload, session_id, payload.get("turn_id"))
                                    if owned is not None else [])
        return payload
    except Exception:
        return {"status": "none", "text": ""}


@router.get("/api/chat/jobs/{turn_id}/stream")
async def api_chat_job_stream(turn_id: str, after: int = 0):
    """Replay missed events, then tail this turn until its terminal event arrives."""
    # A stale browser-side pendingTurnId must fail promptly instead of receiving
    # heartbeats forever. The frontend can then recover from the final snapshot.
    require_local_http()
    try:
        _workbench_identifier(turn_id)
        receipt = _core._workbench_state().receipt(turn_id)
    except WorkbenchError as error:
        raise HTTPException(error.status, str(error)) from None
    if receipt is not None:
        _workbench_receipt(receipt["session_id"], turn_id)
        if receipt["decision"] == "rejected":
            raise HTTPException(409, "本次请求未提交")
    if not _job_event_file(turn_id).is_file():
        raise HTTPException(404, "对话任务记录不存在或已失效")

    async def events():
        cursor = max(0, after)
        idle_since = time.monotonic()
        while True:
            if receipt is not None:
                try:
                    _workbench_receipt(receipt["session_id"], turn_id)
                except HTTPException:
                    yield {"event": "task_state", "data": json.dumps({"version": 1, "turn_id": turn_id, "state": "unknown"})}
                    return
            batch = _read_job_events(turn_id, cursor)
            if batch:
                idle_since = time.monotonic()
                for event in batch:
                    cursor = int(event["id"])
                    raw = event.get("data")
                    # job 日志里 question 的 data 已是序列化 JSON 字符串（实时流
                    # forward() 对 question 原样透传），这里若再 json.dumps 会双层
                    # 转义 → 前端 JSON.parse 拿到字符串而非对象 → ask_user 选项
                    # 卡片渲染成空壳。其余事件存原文/dict，与实时流的 dumps 规则
                    # 一致。因此仅 question 按实时流规则原样透传。
                    if event["event"] in ("question", "artifact") and isinstance(raw, str):
                        data_out = raw
                    else:
                        data_out = json.dumps(raw, ensure_ascii=False)
                    yield {
                        "id": str(cursor),
                        "event": event["event"],
                        "data": data_out,
                    }
                    if event["event"] == "done" or (event["event"] == "error" and receipt is None):
                        return
            else:
                if receipt is not None and not _workbench_turn_live(turn_id):
                    yield {"event": "task_state", "data": json.dumps({"version": 1, "turn_id": turn_id, "state": "unknown"})}
                    return
                # Keep proxy connections active; reconnecting remains safe if it still drops.
                if time.monotonic() - idle_since >= 15:
                    yield {"event": "ping", "data": "{}"}
                    idle_since = time.monotonic()
                await asyncio.sleep(0.25)

    return EventSourceResponse(events(), headers={
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
        "Content-Encoding": "identity",
    })


_CHAT_ACCEPTANCE_PHASE = ContextVar("chat_acceptance_phase", default=None)


def _with_submission_rejection(handler):
    @wraps(handler)
    async def wrapped(req):
        phase = {"claim_started": False, "scope": None}
        token = _CHAT_ACCEPTANCE_PHASE.set(phase)
        try:
            return await handler(req)
        except HTTPException as error:
            # A precise rejected request is different from a network error or
            # absent GET result. A claim attempt can never get this assertion.
            rejection = None
            if (400 <= error.status_code < 500 and not phase["claim_started"] and phase["scope"]
                    and req.sessionId and req.turnId):
                try:
                    require_local_http()
                    _workbench_identifier(req.sessionId)
                    _workbench_identifier(req.turnId)
                    # A rejection permanently wins or loses the same atomic
                    # decision as claim. Absence alone proves nothing.
                    if not _job_event_file(req.turnId).exists():
                        rejection = _core._workbench_state().reject(req.sessionId, req.turnId, phase["scope"],
                                                              _workbench_digest(req.model_dump()))
                except (WorkbenchError, HTTPException):
                    pass
            if rejection is not None:
                return JSONResponse({"detail": error.detail, "rejection": rejection}, status_code=error.status_code,
                                    headers={**(error.headers or {}), "Cache-Control": "no-store"})
            raise
        finally:
            _CHAT_ACCEPTANCE_PHASE.reset(token)
    return wrapped


@router.post("/api/chat/stream")
@_with_submission_rejection
async def api_chat_stream(req: ChatRequest):
    """SSE 真流式对话。

    `openclaw agent` CLI 会把整段模型输出缓冲到结束才打印（stdout 无增量）。真正跑模型的是
    常驻 gateway，它按自己的 OPENCLAW_RAW_STREAM/OPENCLAW_RAW_STREAM_PATH 把「模型原始流」逐
    token 写到**单个共享 jsonl**（SHARED_RAW_STREAM，见 scripts/gateway.sh）。后端在本轮开始时
    记下该文件尾偏移、实时 tail 之后追加的行，用 runId 闩锁隔离本轮，把 assistant_text_stream 的
    token delta 转成 SSE `token`、thinking delta 转成 `thinking`。stdout 仅留作错误/兜底。
    """
    # Reject unavailable/invalid routes before creating even private acceptance
    # state, retaining the existing no-side-effects rejection contract.
    phase = _CHAT_ACCEPTANCE_PHASE.get()
    configured = _core._harness_settings()
    try:
        ServiceRoute(req.service_source if "service_source" in req.model_fields_set else configured["service_source"],
                     req.execution_target if "execution_target" in req.model_fields_set else configured["execution_target"]).require_available()
        if ((req.service_source is not None and req.service_source != configured["service_source"])
                or (req.execution_target is not None and req.execution_target != configured["execution_target"])):
            raise HarnessError("当前服务已变化，请重新选择")
    except HarnessError as error:
        raise HTTPException(409, str(error)) from None
    accepted_session = req.sessionId or f"web-{int(time.time() * 1000)}"
    accepted_turn = req.turnId or uuid.uuid4().hex
    try:
        _workbench_identifier(accepted_session)
        _workbench_identifier(accepted_turn)
    except WorkbenchError:
        raise HTTPException(400, "无效的任务标识") from None
    phase["scope"] = _core._workbench_contract(configured)["source_scope"]
    ledger = _core._workbench_state()
    request_hash = _workbench_digest({**req.model_dump(), "sessionId": accepted_session, "turnId": accepted_turn})
    try:
        previous_receipt = ledger.receipt(accepted_turn)
        if previous_receipt is not None:
            ledger.owned(accepted_session, accepted_turn, phase["scope"])
            if previous_receipt["request_hash"] != request_hash:
                raise WorkbenchError("任务标识已用于其他内容", 409)
            if previous_receipt["decision"] == "rejected":
                return JSONResponse({"detail": "本次请求未提交", "rejection": ledger.public_rejection(previous_receipt)},
                                    status_code=409, headers={"Cache-Control": "no-store"})
            return await api_chat_job_stream(accepted_turn)
    except WorkbenchError as error:
        raise HTTPException(error.status, str(error)) from None
    if _job_event_file(accepted_turn).exists():
        raise HTTPException(409, "任务标识已有记录")
    _core._guard_uncertain_central_turn(req.sessionId)
    # 每轮末尾追加「先查技能库」提醒，抗长对话指令衰减（对用户不可见）
    runtime = _core._chat_backend(req.sessionId)
    config = _core._harness_settings()
    task_settings = dict(_core.read_settings(_core.ENV_FILE))
    context = await _core._accept_task_context(req, runtime, config, task_settings)
    central_recovery = (context.service_source == "subscription"
                        and context.subscription_backend == "central")
    if central_recovery:
        from easel.platform.execution_records import operation_turn_key
        try:
            operation_turn_key(accepted_session, accepted_turn)
        except ValueError:
            raise HTTPException(400, "本次对话标识无效，请新建对话。") from None
    direct = runtime == "api"
    alternate = runtime not in ("api", "openclaw")
    if direct:
        content, system = _direct_chat_content(req)
        message = ""
    else:
        if req.sessionId and history_path(_core.SESSIONS_DIR, req.sessionId).is_file():
            raise HTTPException(409, "此会话属于 API 直连，请新建会话使用 OpenClaw")
        message = _chat_message(req)

    # Acceptance is durable before any agent task is created. A concurrent or
    # disconnected duplicate only reads this same receipt and original log.
    initial_scope = _core._workbench_contract(config)["source_scope"]
    if initial_scope != phase["scope"]:
        raise HTTPException(409, "当前账号或服务已变化")
    try:
        phase["claim_started"] = True
        receipt, created = ledger.claim(accepted_session, accepted_turn, initial_scope, request_hash)
        if not created:
            if receipt["decision"] == "rejected":
                return JSONResponse({"detail": "本次请求未提交", "rejection": ledger.public_rejection(receipt)},
                                    status_code=409, headers={"Cache-Control": "no-store"})
            return await api_chat_job_stream(accepted_turn)
        event_path = _job_event_file(accepted_turn)
        ledger._directory(event_path.parent)
        accepted_event = {"id": 1, "event": "accepted", "data": ledger.public_receipt(receipt)}
        with event_path.open("x", encoding="utf-8") as stream:
            os.chmod(event_path, 0o600)
            stream.write(json.dumps(accepted_event, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except (WorkbenchError, OSError) as error:
        raise HTTPException(error.status if isinstance(error, WorkbenchError) else 503, "任务记录暂不可用") from None

    # supervisor（跑 openclaw run）与 forward（转发 SSE 给浏览器）之间的事件通道。
    # 关键：run 跑在独立后台任务里，客户端断开只结束 forward，不取消 supervisor →
    # openclaw 照常跑到底、结果落盘，前端断线后 /api/chat/last 取回。
    loop = asyncio.get_event_loop()
    client_q: asyncio.Queue = asyncio.Queue()
    CLIENT_DONE = object()
    client_q.put_nowait({"t": "accepted", "text": ledger.public_receipt(receipt), "id": 1})

    async def supervisor():
        sk = accepted_session
        pk = f"web:{sk}"                 # 落盘 key（与 /api/chat/last 一致）
        turn_id = accepted_turn
        event_seq = 1
        full_text: list[str] = []        # 累积完整回答，供断线取回
        timed_out = False                # 只有真·超时才 terminate 进程；断线绝不杀

        # Claim this turn before waiting for locks, so recovery cannot return the previous turn.
        if not direct and not alternate:
            _core._bind_task(sk, context)
            _core._save_turn(pk, "running", "", {"turn_id": turn_id, "route": context.metadata()})

        event_path = _job_event_file(turn_id)

        def publish(kind, text=None, **extra):
            nonlocal event_seq
            if kind == "token" and not direct and not alternate:
                full_text.append(text)
            event_seq += 1
            data = ({"sessionKey": extra.get("sessionKey"), **_workbench_completion(sk, turn_id)} if kind == "done" else text)
            if kind == "done":
                extra["completion"] = data
            event = {"id": event_seq, "event": kind, "data": data}
            try:
                with event_path.open("a", encoding="utf-8") as ef:
                    ef.write(json.dumps(event, ensure_ascii=False) + "\n")
                    ef.flush()
            except OSError:
                pass
            client_q.put_nowait({"t": kind, "text": text, "id": event_seq, **extra})

        redactor = _web_output_redactor(lambda kind, text: publish(kind, text))

        def to_client(kind, text=None, **extra):
            if kind in ("done", "error"):
                redactor.flush()
            if kind == "done":
                publish(kind, **redactor.value(extra))
            else:
                redactor.emit(kind, text)

        # 秒级反馈：发出即亮「已收到」，不等 agent 冷启动（首个 SSE 事件，随流回放必达）
        to_client("activity", "⏳ 已收到…" if direct else "⏳ 已收到，正在唤醒 agent…")
        if central_recovery:
            # A safe reference to this accepted turn, never a financial result
            # or an authorization grant. Old clients ignore the additive event.
            publish("central_recovery", {"version": 1, "turn_id": turn_id})

        if direct:
            try:
                await _run_direct_chat(
                    req.model_copy(update={"turnId": turn_id}), sk, content, system, to_client, context=context, settings=task_settings)
            finally:
                to_client("done", sessionKey=sk)
                client_q.put_nowait(CLIENT_DONE)
            return

        if alternate:
            try:
                await _core._run_harness_chat(req.model_copy(update={"turnId": turn_id}),
                                        sk, message, runtime, config, to_client, context=context)
            finally:
                to_client("done", sessionKey=sk)
                client_q.put_nowait(CLIENT_DONE)
            return

        async def _run_gateway_turn(hproc):
            """HTTP 直连常驻网关跑一轮（OpenAI 兼容端点 /v1/chat/completions，原生 SSE）。

            与 CLI 路径的差异：agent 在常驻 gateway 进程里直接跑，无每轮进程冷启动；
            正文 token 经 SSE 直接进 q（与 CLI 路径共享同一消费出口）；
            收尾由主循环统一负责（proc.poll() 兼容面）。
            """
            body = {
                "model": context.model,
                "stream": True,
                "messages": [{"role": "user", "content": message}],
            }
            # session-id 必须跟 CLI 路径钉死同一个（见 _openclaw_session_id）：只带 session-key
            # 的话网关会自己另起一个 transcript —— 跨天空闲后丢历史，且万一本轮回退 CLI，
            # 两条路径会写进不同的会话文件，对话历史直接劈叉。
            headers = {"x-openclaw-session-key": f"agent:main:{sk}",
                       "x-openclaw-session-id": _core._openclaw_session_id(sk)}
            tool_noted = False
            saw_done = False
            got_text = False
            try:
                import httpx as _httpx
                timeout = _httpx.Timeout(_core.TIMEOUT_CHAT + 60, connect=10)
                async with _httpx.AsyncClient(timeout=timeout) as client:
                    async with client.stream(
                            "POST", chat_completions_url(),
                            json=body, headers=headers) as resp:
                        if resp.status_code != 200:
                            raw = _core._scrub_secrets((await resp.aread()).decode("utf-8", "replace"))[:200]
                            to_client("error", f"❌ 对话失败（HTTP {resp.status_code}）：{raw[:160]}")
                            return
                        async for line in resp.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            payload = line[5:].lstrip()   # SSE 允许 `data:{…}`（冒号后无空格）
                            if payload == "[DONE]":
                                saw_done = True
                                break
                            try:
                                d = json.loads(payload)
                            except ValueError:
                                continue
                            if isinstance(d.get("error"), dict):   # 200 里夹错误对象：不能当正常流吞掉
                                to_client("error", f"❌ 网关返回错误：{_core._scrub_secrets(str(d['error']))[:160]}")
                                return
                            delta = (d.get("choices") or [{}])[0].get("delta") or {}
                            # 思考流的两个可能来源，先到先得（`sse_thinking` 闩锁，防两路都来时重复）：
                            # ① 这里的 reasoning 增量 —— openclaw 2026.6.11 的 chat/completions
                            #    实现里 reasoning/thinking 出现 0 次，**不会**给；留着是给别的网关/
                            #    以后的版本用。② 常驻 gateway 写的共享 raw 流（见下面 _tail），
                            #    它按自己的 env 写，跟这一轮是谁触发的无关，所以 HTTP 模式照样能读到。
                            rc = delta.get("reasoning_content") or delta.get("reasoning")
                            if rc:
                                run_info["sse_thinking"] = True
                                run_info["thinking_chars"] += len(rc)
                                q.put_nowait({"t": "thinking", "text": rc})
                            c = delta.get("content")
                            if c:
                                got_text = True
                                q.put_nowait({"t": "token", "text": c})
                            if delta.get("tool_calls") and not tool_noted:
                                tool_noted = True
                                to_client("activity", "🔧 正在执行操作…")
                # 流正常结束却既没正文也没 [DONE]：多半是端点没真开或中途断了。
                # 不报错的话这一轮会静默落一条空回答，还会被 /api/chat/last 原样取回。
                if not got_text and not saw_done:
                    to_client("error", "❌ 网关流异常结束：没有收到任何内容（检查 "
                                       "gateway.http.endpoints.chatCompletions 是否开启，"
                                       "或设 EASEL_CHAT_TRANSPORT=cli 回退）")
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                to_client("error", f"❌ 网关连接失败：{_core._scrub_secrets(str(e))[:140]}")
            finally:
                # 先把 SENTINEL 排进 q（FIFO 保证它排在本轮所有 token 之后），再标记完成：
                # 主循环读到它时，前面的 token 必然已全部消费过。
                q.put_nowait(SENTINEL)
                hproc.finish()

        _core._heal_openclaw_session(sk)       # 清洗历史里无签名 thinking 块，防回放失效
        # 原始事件流由常驻 gateway 写到共享文件（见 SHARED_RAW_STREAM / scripts/gateway.sh），
        # 不是 agent 客户端写的。本轮开始时记下文件当前尾偏移：只读此偏移之后追加的行，
        # 再用首个新事件的 runId 闩锁本轮，隔离其它并发会话的事件。
        try:
            raw_start_offset = SHARED_RAW_STREAM.stat().st_size
        except OSError:
            raw_start_offset = 0

        cmd = _core.openclaw_base_cmd() + [
            "--profile", _core.OPENCLAW_PROFILE, "agent", "--agent", "main",
            "--session-key", f"agent:main:{sk}", "--session-id", _core._openclaw_session_id(sk),
            "--thinking", _core._agent_thinking_level(),
            "--timeout", str(_core.TIMEOUT_CHAT), "--message", message,
        ]
        env = _core._proxy_env()
        # 注意：不要在客户端 env 上设 OPENCLAW_RAW_STREAM*——`agent` 客户端不写 raw 流，
        # 设了也没用；raw 流开关在 gateway 侧（scripts/gateway.sh）。
        # 告诉 skill：本部署的 ask_user 选项卡片是否可用。卡片依赖 gateway 的
        # question.* RPC（仅 2026.9.x 有），2026.6.11 上桥接不可用 → skill 改用
        # 「文字问答跨轮等待」拿短信验证码，而不是空等卡片超时。
        _cards_ok = _core.question_bridge_supported is not None and _core.question_bridge_supported()
        env["EASEL_ASKUSER_CARDS"] = "1" if _cards_ok else "0"

        # 会话级串行：同一会话若已有请求在跑，先提示排队，等它结束再开
        # （否则两个 openclaw 进程并发写同一 session 文件 → 崩溃 rc=1 / 会话串味）。
        # 双层锁：asyncio 锁管同 web 进程内并发；flock 跨进程锁管两个标签/gateway/cron 撞同一会话。
        lock = _core._session_lock(sk)
        xlock = _core._CrossProcLock(sk)
        if lock.locked():
            to_client("activity", "⏳ 这个会话上一条还在跑，排队等它结束再开始…")
        await lock.acquire()
        # flock 可能阻塞（等另一进程/标签跑完），放线程池避免卡住事件循环
        got = await loop.run_in_executor(None, xlock.acquire, min(_core.TIMEOUT_CHAT, 300))
        if not got:
            lock.release()
            _core._save_turn(pk, "done", "这个会话正在另一个窗口运行，请稍候再试。", {
                "turn_id": turn_id, "route": context.metadata(), "clean_end": False, "stop_reason": "session_lock_timeout",
            })
            to_client("activity", "⏳ 这个会话正在另一个窗口运行，请稍候再试")
            to_client("done", sessionKey=sk)
            client_q.put_nowait(CLIENT_DONE)
            return

        # _resolve_transport 里既有 stat 又有阻塞 urllib 探针（最多 3s），必须丢线程：
        # 直接在协程里调会把整个事件循环——连同其它会话正在推的 SSE——一起卡住。
        is_http = (await asyncio.to_thread(_resolve_transport, sk)) == "http"
        if is_http:
            # HTTP 直连常驻网关：无进程冷启动（agent 在 gateway 进程里跑）
            proc = _core._GatewayHttpProc()
        else:
            try:
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    cwd=str(_core.PROJECT_ROOT), text=True, bufsize=1, env=env,
                )
            except BaseException:
                lock.release()
                xlock.release()
                _core._save_turn(pk, "done", "❌ 启动失败，请重试", {
                    "turn_id": turn_id, "route": context.metadata(), "clean_end": False, "stop_reason": "spawn_failed",
                })
                to_client("error", "❌ 启动失败，请重试")
                to_client("done", sessionKey=sk)
                client_q.put_nowait(CLIENT_DONE)
                return
        _core._RUNNING_CHAT[sk] = proc         # 注册运行中进程（HTTP 模式为伪进程），供 /api/chat/stop
        # 经 gateway 后客户端 stdout 没有 model-fetch 标记（那是独立跑 agent 才有），先立刻
        # 给一个「正在思考」活动指示，随后 token 从共享 raw stream 流进来接管显示。
        to_client("activity", "🧠 正在思考…")

        q = asyncio.Queue()
        SENTINEL = object()
        stdout_lines = []
        run_info: dict = {"stop_reason": None, "last_ev": None, "saw_message_end": False,
                          "run_id": None,
                          "fetch_count": 0, "token_chars": 0, "thinking_chars": 0,
                          "delegated": False, "ignored_foreign_events": 0}

        def _drain_stdout():
            try:
                for line in proc.stdout:
                    stdout_lines.append(line)
                    c = re.sub(r"\x1b\[[0-9;]*m", "", line)
                    if "model-fetch] start" in c:
                        run_info["fetch_count"] += 1
                        fc = run_info["fetch_count"]
                        _emit("activity", "🧠 正在思考…" if fc == 1 else f"🔧 调用工具后继续推理（第 {fc} 步）…")
                    elif "[agent]" in c and "delegat" in c.lower():
                        run_info["delegated"] = True
                        _emit("activity", "🛠️ 制作中…")
                    m = re.search(r"ended with stopReason=(\S+)", c)
                    if m:
                        run_info["stop_reason"] = m.group(1)
            except Exception:
                pass

        def _emit(kind: str, text: str):
            loop.call_soon_threadsafe(q.put_nowait, {"t": kind, "text": text})

        # 必须等 q / run_info / _emit 都就位后再起这一轮：_run_gateway_turn 闭包引用它们，
        # 早一步 create_task 就只能靠「中间没有 await」来侥幸，改一行同步代码就会崩。
        if is_http:
            proc._task = loop.create_task(_run_gateway_turn(proc))

        # ---- ask_user 问答题桥接：轮询 gateway 的 pending question，推给前端渲染 ----
        # 背景：OpenClaw 的 ask_user 注册到 gateway 进程内，Easel 前端不消费 question RPC → 选项不可见。
        # 这里在 agent 运行期间每 2s 轮询一次，把新出现的 pending question 以 SSE `question` 事件推送，
        # 前端渲染选项卡片；用户点击后经 /api/chat/question/answer 调 question.resolve 完成回答。
        if _core.GatewayClient is not None and not _QBRIDGE_DISABLED:
            def _question_poll():
                global _QBRIDGE_DISABLED
                if _QBRIDGE_DISABLED:
                    return
                # 版本能力门：旧版本 OpenClaw（<2026.9.x）没有 question.* RPC，连都不连——
                # 否则每轮 connect 都在网关上触发新的 scope-upgrade 配对申请。只提示一次，走文字问答。
                if _core.question_bridge_supported is not None and not _core.question_bridge_supported():
                    _QBRIDGE_DISABLED = True
                    _qbridge_warn_once(
                        "unsupported",
                        "[question-bridge] 当前 OpenClaw 版本无 question RPC（需 2026.9.x+），"
                        "已跳过 ask_user 选项卡片桥接，改用文字问答。")
                    return
                client = None
                pushed: set[str] = set()
                try:
                    client = _core.GatewayClient()
                    client.connect()
                except Exception as e:
                    # connect 失败（如 NOT_PAIRED/scope-upgrade，或网关不可达）：熔断整个桥接，
                    # 不再每轮重试——否则每轮都会在网关上堆一个新的配对/权限申请。每进程只告警一次。
                    _QBRIDGE_DISABLED = True
                    _qbridge_warn_once(
                        "connect",
                        f"[question-bridge] connect gateway failed，已停用桥接（本进程），"
                        f"ask_user 改用文字问答: {e}")
                    return
                try:
                    while proc.poll() is None:
                        try:
                            items = client.list_questions(
                                session_key=f"agent:main:{sk}", status="pending")
                        except _core.GatewayUnsupportedError as e:
                            # 连上了但没有 question RPC（版本判断漏网时的兜底）：熔断，安静退出。
                            _QBRIDGE_DISABLED = True
                            _qbridge_warn_once(
                                "unsupported",
                                f"[question-bridge] 当前 OpenClaw 版本无 question RPC，"
                                f"已停用 ask_user 选项卡片桥接（需 2026.9.x+）: {e}")
                            return
                        except Exception:
                            time.sleep(2)
                            continue
                        for it in items:
                            qid = it.get("id")
                            if qid and qid not in pushed:
                                pushed.add(qid)
                                _emit("question", json.dumps({
                                    "id": qid,
                                    "questions": it.get("questions", []),
                                    "expiresAtMs": it.get("expiresAtMs"),
                                }, ensure_ascii=False))
                        time.sleep(2)
                finally:
                    try:
                        if client is not None:
                            client.close()
                    except Exception:
                        pass
            loop.run_in_executor(None, _question_poll)

        def _handle(line: str):
            o = _raw_event_for_run(line, run_info["run_id"])
            if o is None:
                # 属其它并发 run 的事件（或无法解析）：绝不混入本轮可见流/收尾诊断，仅计数。
                try:
                    parsed = json.loads(line)
                    rid = parsed.get("runId") if isinstance(parsed, dict) else None
                    if rid is not None and run_info["run_id"] is not None and rid != run_info["run_id"]:
                        run_info["ignored_foreign_events"] += 1
                except Exception:
                    pass
                return
            # 首个带 runId 的事件闩锁本轮 run（之后 _raw_event_for_run 只放行这个 run）。
            if run_info["run_id"] is None:
                rid = o.get("runId")
                if rid is None:
                    return          # 还没拿到 runId，等下一条带 runId 的事件再闩锁
                run_info["run_id"] = rid
            ev, et, delta = o.get("event"), o.get("evtType"), o.get("delta") or ""
            # 记录最后一个 raw 事件：正常收尾 last_ev == assistant_message_end；
            # 若停在 text_delta/thinking_delta 说明输出或思考流被中断、没正常收尾（本次排查关键信号）。
            if ev:
                run_info["last_ev"] = ev
            if ev == "assistant_message_end":
                run_info["saw_message_end"] = True
            if not delta:
                return
            if ev == "assistant_text_stream" and et == "text_delta":
                if is_http:
                    # HTTP 模式正文以 SSE 为准（那条才是本请求自己的响应流）。这里再发一遍
                    # 就是同一段内容进两次队列 —— 前端会看到每个字重复。
                    return
                run_info["token_chars"] += len(delta)
                run_info["text_tail"] = (run_info.get("text_tail", "") + delta)[-160:]
                _emit("token", delta)
                return
            if ev == "assistant_thinking_stream" and et == "thinking_delta":
                if run_info.get("sse_thinking"):
                    return      # SSE 已经在供思考流了，别叠第二份
                run_info["thinking_chars"] += len(delta)
                _emit("thinking", delta)
                return

        def _tail():
            try:
                f = None
                # gateway 刚起或本轮还没产生事件时文件可能暂不存在：轮询等它出现（进程先退出则收尾）。
                while f is None:
                    try:
                        f = open(SHARED_RAW_STREAM, "r", encoding="utf-8")
                    except OSError:
                        if proc.poll() is not None:
                            return
                        time.sleep(0.04)
                with f:
                    f.seek(raw_start_offset)   # 只读本轮开始后追加的行，跳过历史轮次
                    buf = ""
                    while True:
                        chunk = f.readline()
                        if chunk == "":
                            if proc.poll() is not None:
                                buf += f.read()
                                for ln in buf.split("\n"):
                                    _handle(ln)
                                break
                            time.sleep(0.04)
                            continue
                        buf += chunk
                        while "\n" in buf:
                            ln, buf = buf.split("\n", 1)
                            _handle(ln)
            except Exception:
                pass
            finally:
                loop.call_soon_threadsafe(q.put_nowait, SENTINEL)

        stdout_fut = None
        if is_http:
            # 正文走 SSE，但思考流**不走**：openclaw 2026.6.11 的 chat/completions 实现里
            # reasoning/thinking 一次都没出现，不回传任何思考增量。思考只存在于常驻 gateway
            # 写的那份共享 raw 流里（它按 gateway 自己的 env 写，与谁触发无关）。所以这条路径
            # 照样 tail 它——_handle 里 text_delta 在 is_http 下直接丢弃，只取 thinking_delta，
            # 正文不会进两次。不 tail 的话「💭 思考过程」在 HTTP 模式下永远是空的。
            loop.run_in_executor(None, _tail)
        else:
            stdout_fut = loop.run_in_executor(None, _drain_stdout)
            loop.run_in_executor(None, _tail)

        deadline = time.monotonic() + _core.TIMEOUT_CHAT + 30
        emitted = False
        # 两条路径都靠「队列里读到 SENTINEL」收尾。HTTP 模式若改用带外标志（一开始就置 True），
        # 最后一批还排在 q 里没被消费的 token 会随 poll() 转为已完成而被直接丢掉——答案尾巴被截。
        tail_finished = False
        try:
            while True:
                # A raw-stream reader failure must not be mistaken for model
                # completion. Keep the session lock until the process exits.
                if tail_finished and proc.poll() is not None:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    to_client("error", "⏱️ 请求超时")
                    break
                try:
                    item = await asyncio.wait_for(q.get(), timeout=min(10, remaining))
                except asyncio.TimeoutError:
                    continue
                if item is SENTINEL:
                    tail_finished = True
                    if proc.poll() is not None:
                        break
                    continue
                if item["t"] == "token":
                    emitted = True
                    to_client("token", item["text"])
                elif item["t"] == "thinking":
                    to_client("thinking", item["text"])
                elif item["t"] == "activity":
                    to_client("activity", item["text"])
                elif item["t"] == "question":
                    to_client("question", item["text"])
            rc = proc.poll()
            # 等 stdout 读完（stopReason 行在进程收尾时才打印，避免 _tail 先发 SENTINEL 时漏读）
            if stdout_fut is not None:
                try:
                    await asyncio.wait_for(stdout_fut, timeout=2)
                except Exception:
                    pass
            sr = run_info.get("stop_reason")
            if not emitted:
                clean = _core.clean_agent_output("".join(stdout_lines))
                if clean:
                    emitted = True
                    to_client("token", clean)
                elif rc not in (0, None):
                    err = _core._scrub_secrets(_core.clean_agent_output("".join(stdout_lines)))[:200]
                    to_client("error", f"❌ 执行失败（退出码 {rc}）{' — ' + err if err else ''}")
            # 收尾检测：即使已吐了内容，只要不是「正常收尾」就显式告知——
            # 否则被截断（触顶）/被杀（负载）/流被中断，都会被当成「清晰地答完了」，
            # 用户看到的就是「答一半突然停、也不说做完」（本 bug 根因）。
            # 正常收尾的唯一标志：raw 流最后一个事件是 assistant_message_end。
            # 用户显式「停止」不是异常中断 → 不报「被中断」告警（前端已就地标注「已停止」）。
            if (emitted or run_info["thinking_chars"]) and sk not in _core._STOPPED_CHAT:
                note = None
                if sr and sr in ("max_tokens", "length", "model_length"):
                    note = (f"\n\n---\n⚠️ 上面这条**被截断**了（stopReason={sr}，单条回复触顶）。"
                            f"回我「继续」我接着写完，或让我把任务拆小一点。")
                elif rc not in (0, None):
                    note = (f"\n\n---\n⚠️ 生成**被中断**（退出码 {rc}，多半是超时或系统负载过高把进程杀了），"
                            f"不是正常收尾。可以让我重试。")
                elif sr == "tool_use":
                    note = ("\n\n---\n⚠️ 我刚做完这一步、**正要执行下一步操作时中断了**"
                            "（本轮以工具调用结尾却没能继续，前端把它当成答完了）。回我「继续」我接着做。")
                elif run_info.get("last_ev") not in (None, "assistant_message_end"):
                    note = ("\n\n---\n⚠️ 这条**可能没写完**——模型的输出/思考流被中断、没有正常收尾"
                            "（多为网络或模型代理把长回复的流掐断了）。回我「继续」，或重试。")
                elif run_info.get("text_tail", "").rstrip()[-1:] in ("：", ":"):
                    # 正常收尾但正文停在冒号 = 模型"我要做X："后没接着做（多为要接工具/下一步却断了）。
                    # 用户实测「所有莫名停止都停在冒号」——这一条兜住这个模式。
                    note = ("\n\n---\n⚠️ 我似乎停在了冒号处、没接着把后面的内容/操作做出来。"
                            "回我「继续」我补上。")
                if note:
                    to_client("token", note)
        finally:
            # 只有真的在 HTTP 上跑出了内容，才把这个会话钉到 http 上。钉早了（比如选路时就钉）
            # 会把一个其实没跑成的会话锁死在 http，之后每轮都往一条不通的路上撞；而钉住之后
            # 就绝不能再换回 cli —— 网关那份 transcript 我们按名字找不回来，换边即丢历史。
            if is_http and emitted:
                _pin_transport(sk, "http")
            user_stopped = sk in _core._STOPPED_CHAT
            _core._STOPPED_CHAT.discard(sk)
            # Reaching finally while the child is alive means timeout, explicit
            # stop, cancellation, or an internal stream failure. Never release
            # the session locks while such a process can still write history.
            if proc.poll() is None:
                try:
                    proc.terminate()
                except OSError:
                    pass
                try:
                    await asyncio.to_thread(proc.wait, timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        proc.kill()
                        await asyncio.to_thread(proc.wait, timeout=2)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
            redactor.flush()  # also before cancellation recovery persistence
            # 诊断日志：每次对话流收尾都记一行，供事后定位「莫名停下」到底是哪种情况。
            try:
                DEBUG_DIR.mkdir(parents=True, exist_ok=True)
                tail = _core._scrub_secrets(_core.clean_agent_output("".join(stdout_lines)))[-800:]
                with (DEBUG_DIR / "chat-stream.jsonl").open("a", encoding="utf-8") as lf:
                    lf.write(_core._scrub_secrets(json.dumps({
                        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "session": sk,
                        "rc": proc.poll(),
                        "stop_reason": run_info["stop_reason"],
                        "last_ev": run_info["last_ev"],
                        "clean_end": run_info["last_ev"] == "assistant_message_end",
                        "saw_message_end": run_info["saw_message_end"],
                        "fetch_count": run_info["fetch_count"],
                        "token_chars": run_info["token_chars"],
                        "thinking_chars": run_info["thinking_chars"],
                        "delegated": run_info["delegated"],
                        "ignored_foreign_events": run_info["ignored_foreign_events"],
                        "text_tail": "".join(full_text)[-160:],
                        "stdout_tail": tail,
                    }, ensure_ascii=False)) + "\n")
            except Exception:
                pass
            # 落盘完整结果：后端跑完整轮不依赖客户端连接，断线后前端用 /api/chat/last 取回
            _core._save_turn(pk, "done", "".join(full_text), {
                "turn_id": turn_id, "route": context.metadata(),
                "clean_end": run_info.get("last_ev") == "assistant_message_end",
                "stop_reason": "user_stopped" if user_stopped else run_info.get("stop_reason"),
            })
            xlock.release()
            lock.release()
            _core._RUNNING_CHAT.pop(sk, None)
            to_client("done", sessionKey=sk)
            client_q.put_nowait(CLIENT_DONE)

    # 把 run 跑在独立后台任务里（持强引用防 GC）——客户端断开不取消它。
    task = asyncio.create_task(supervisor())
    _core._BG_TASKS.add(task)
    _WORKBENCH_TURNS[(str(_core.SESSIONS_DIR), accepted_turn)] = task

    def _bg_done(t):
        _core._BG_TASKS.discard(t)
        _WORKBENCH_TURNS.pop((str(_core.SESSIONS_DIR), accepted_turn), None)
        try:
            exc = t.exception()   # 取出异常避免「never retrieved」告警
        except (Exception, asyncio.CancelledError):
            exc = None
        if t.cancelled():
            client_q.put_nowait(CLIENT_DONE)
        if exc is not None:
            # supervisor 意外崩溃：解锁 forward，别让它空等
            try:
                client_q.put_nowait(CLIENT_DONE)
            except Exception:
                pass
    task.add_done_callback(_bg_done)

    async def forward():
        """纯转发：从 client_q 取事件 yield 给浏览器。

        客户端断开（关标签/代理掐断）只会结束本生成器，supervisor 任务不受影响，
        继续把 openclaw run 跑完并落盘 → 前端断线后 /api/chat/last 取回完整结果。
        """
        idle_since = time.monotonic()
        while True:
            try:
                item = await asyncio.wait_for(client_q.get(), timeout=10)
            except asyncio.TimeoutError:
                # 长时间无输出（等模型长回复 / 制作类长任务）→ 发心跳，让用户知道没卡死。
                # 用独立的 `heartbeat` 事件，不走 `activity`：否则会覆盖掉真实的
                # 「🧠 正在思考…/🛠️ 制作中…」状态与思考流（防呆消息把思考设计顶掉的根因）。
                # 发出后重置 idle_since → 心跳按 30s 一次的节奏，不再每 10s 重复刷屏。
                if time.monotonic() - idle_since >= 30:
                    idle_since = time.monotonic()
                    yield {"event": "heartbeat", "data": json.dumps(
                        "仍在处理中，未卡住…（复杂或制作类任务会花点时间）", ensure_ascii=False)}
                continue
            if item is CLIENT_DONE:
                break
            idle_since = time.monotonic()
            t = item["t"]
            if t == "token":
                yield {"id": str(item["id"]), "event": "token", "data": json.dumps(item["text"], ensure_ascii=False)}
            elif t == "thinking":
                yield {"id": str(item["id"]), "event": "thinking", "data": json.dumps(item["text"], ensure_ascii=False)}
            elif t == "activity":
                yield {"id": str(item["id"]), "event": "activity", "data": json.dumps(item["text"], ensure_ascii=False)}
            elif t == "artifact":
                yield {"id": str(item["id"]), "event": "artifact", "data": item["text"]}
            elif t == "central_recovery":
                yield {"id": str(item["id"]), "event": "central_recovery", "data": json.dumps(item["text"], ensure_ascii=False)}
            elif t == "accepted":
                yield {"id": str(item["id"]), "event": "accepted", "data": json.dumps(item["text"], ensure_ascii=False)}
            elif t == "question":
                yield {"id": str(item["id"]), "event": "question", "data": item["text"]}
            elif t == "error":
                yield {"id": str(item["id"]), "event": "error", "data": json.dumps(item["text"], ensure_ascii=False)}
            elif t == "done":
                yield {"id": str(item["id"]), "event": "done", "data": json.dumps(item.get("completion", {"sessionKey": item.get("sessionKey")}), ensure_ascii=False)}

    return EventSourceResponse(forward(), headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Content-Encoding": "identity"})


class QuestionAnswerRequest(BaseModel):
    sessionId: str | None = None
    questionId: str
    answers: dict  # {questionId: [optionValues]}
    resolvedBy: str | None = None


@router.post("/api/chat/question/answer")
async def api_question_answer(req: QuestionAnswerRequest):
    """前端点击 ask_user 选项后调用：转发 gateway question.resolve，让等待的 agent 拿到答案。"""
    if req.questionId.startswith("harness_"):
        record = _core._HARNESS_QUESTIONS.get(req.questionId)
        if record is None:
            return {"ok": False, "error": "权限或问答请求已过期，请重试"}
        required = {question["questionId"] for question in record["questions"]}
        if set(req.answers or {}) != required:
            raise HTTPException(400, "回答与当前问题不匹配")
        if record["future"].done():
            return {"ok": False, "error": "请求已回答"}
        record["future"].set_result(req.answers)
        return {"ok": True}
    if _core._direct_api_enabled():
        raise HTTPException(409, "API 直连模式使用文字问答，请新建对话")
    if _core._harness_settings()["runtime"] != "openclaw":
        raise HTTPException(409, "此问题属于旧 OpenClaw 会话，请使用当前底座的新对话")
    if _core.GatewayClient is None:
        return {"ok": False, "error": "gateway question bridge unavailable"}
    client = _core.GatewayClient()
    try:
        client.connect()
        result = client.resolve(req.questionId, req.answers or {}, req.resolvedBy)
        return {"ok": True, "result": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        client.close()


class QuestionStatusRequest(BaseModel):
    questionIds: list[str]


@router.post("/api/chat/question/status")
async def api_question_status(req: QuestionStatusRequest):
    """批量查 question 状态（重放旧事件时过滤已解决的题）。"""
    if req.questionIds and all(identifier.startswith("harness_") for identifier in req.questionIds):
        return {"ok": True, "questions": {
            identifier: {"status": _core._HARNESS_QUESTIONS.get(identifier, {}).get("status", "not_found")}
            for identifier in req.questionIds}}
    if _core._direct_api_enabled():
        return {"ok": True, "questions": {qid: {"status": "not_found"} for qid in req.questionIds}}
    if _core._harness_settings()["runtime"] != "openclaw":
        return {"ok": False, "questions": {}}
    if _core.GatewayClient is None:
        return {"ok": False, "questions": {}}
    client = _core.GatewayClient()
    try:
        client.connect()
        out = {}
        for qid in req.questionIds:
            try:
                q = client.get_question(qid)
                # QUESTION_NOT_FOUND 抛异常捕获后报 not_found；正常返回则按 status
                out[qid] = {"status": q.get("status") if q else "not_found"}
            except _core.GatewayQuestionError as e:
                # gateway 明确错：问题已清理/不存在 = 已答或已过期，一律 not_found
                out[qid] = {"status": "not_found"}
            except Exception:
                out[qid] = {"status": "unknown"}   # 网络/连接异常：保持 unknown（前端保留显示，宁不缺题）
        return {"ok": True, "questions": out}
    except Exception as e:
        return {"ok": False, "error": str(e), "questions": {}}
    finally:
        client.close()


class StopRequest(BaseModel):
    sessionId: str | None = None
    turnId: str | None = None


@router.post("/api/chat/stop")
async def api_chat_stop(req: StopRequest):
    """用户显式停止当前会话正在跑的对话 agent：终止进程 → supervisor 收尾释放会话锁 →
    下一句立刻能发（不再卡「上一条还在跑」）。仅此显式入口会杀进程；客户端断线不经此路径。"""
    sk = (req.sessionId or "").strip()
    target = None
    if req.turnId:
        require_local_http()
        target = _workbench_receipt(sk, req.turnId)
        if target is None:
            return {"stopped": False, "state": "unknown", "turn_id": req.turnId}
        task = _WORKBENCH_TURNS.get((str(_core.SESSIONS_DIR), req.turnId))
        current = {}
        try:
            current = json.loads(_core._turn_file(f"web:{sk}").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        if current.get("turn_id") != req.turnId:
            # Accepted but still queued: cancel that supervisor, never the
            # different turn currently holding this session's process lock.
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                payload = {"turn_id": req.turnId, "status": "done", "text": "", "clean_end": False, "stop_reason": "stopped"}
                _core._workbench_state().save_snapshot(sk, req.turnId, payload)
                batch = _read_job_events(req.turnId)
                if not any(event.get("event") in ("done", "error") for event in batch):
                    with _job_event_file(req.turnId).open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps({"id": max((int(event.get("id", 0)) for event in batch), default=0) + 1,
                                                 "event": "done", "data": {"sessionKey": sk, **_workbench_completion(sk, req.turnId)}}) + "\n")
                        stream.flush()
                        os.fsync(stream.fileno())
                return {"stopped": True, "state": "stopped", "turn_id": req.turnId}
            response = await api_chat_turn(sk, req.turnId)
            state = json.loads(response.body)["state"]
            return {"stopped": state == "stopped", "state": state, "turn_id": req.turnId}
    proc = _core._RUNNING_CHAT.get(sk) if sk else None
    if proc is not None and proc.poll() is None:
        _core._STOPPED_CHAT.add(sk)          # 标记为用户停止，供 supervisor 正常收尾（不报「被中断」）
        try:
            proc.terminate()
        except OSError:
            pass
        try:
            await asyncio.to_thread(proc.wait, timeout=3)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
        # supervisor removes the running marker only after persisting the final
        # snapshot and releasing both session locks.
        deadline = time.monotonic() + 5
        while _core._RUNNING_CHAT.get(sk) is proc and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        if _core._RUNNING_CHAT.get(sk) is proc:
            return {"stopped": False, "state": "stopping", **({"turn_id": req.turnId} if req.turnId else {})}
        if target is not None:
            response = await api_chat_turn(sk, req.turnId)
            state = json.loads(response.body)["state"]
            return {"stopped": state == "stopped", "state": state, "turn_id": req.turnId}
        return {"stopped": True, "state": "stopped"}
    if target is not None:
        response = await api_chat_turn(sk, req.turnId)
        state = json.loads(response.body)["state"]
        return {"stopped": state == "stopped", "state": state, "turn_id": req.turnId}
    return {"stopped": False, "state": "unknown"}


@router.post("/api/chat")
async def api_chat(req: ChatRequest):
    """非流式对话（备选）。"""
    _core._guard_uncertain_central_turn(req.sessionId)
    runtime = _core._chat_backend(req.sessionId)
    config = _core._harness_settings()
    task_settings = dict(_core.read_settings(_core.ENV_FILE))
    context = await _core._accept_task_context(req, runtime, config, task_settings)
    if runtime not in ("api", "openclaw"):
        message = _chat_message(req)
        sk = req.sessionId or uuid.uuid4().hex
        errors = []
        def emit(kind, text):
            if kind == "error":
                errors.append(text)
        result = await _core._run_harness_chat(req, sk, message, runtime, config, emit,
                                         interactive=False, context=context)
        if errors:
            raise HTTPException(502, errors[0])
        return {"response": result, "sessionKey": sk}
    if runtime == "api":
        content, system = _direct_chat_content(req)
        errors = []
        def emit(kind, text):
            if kind == "error":
                errors.append(text)
        sk = req.sessionId or uuid.uuid4().hex
        result = await _run_direct_chat(req, sk, content, system, emit, context=context, settings=task_settings)
        if errors:
            raise HTTPException(502, errors[0])
        return {"response": result, "sessionKey": sk}
    if req.sessionId and history_path(_core.SESSIONS_DIR, req.sessionId).is_file():
        raise HTTPException(409, "此会话属于 API 直连，请新建会话使用 OpenClaw")
    # 每轮末尾追加「先查技能库」提醒，抗长对话指令衰减（对用户不可见）
    message = _chat_message(req)
    # chat 可能中途触发制作层长任务 → 用 TIMEOUT_CHAT，与流式 /api/chat/stream 一致（勿用 300s）
    sk = req.sessionId or uuid.uuid4().hex
    task = _core._submit_sync_task(message, _core.TIMEOUT_CHAT, context, session_id=sk, turn_id=req.turnId)
    result = await asyncio.shield(task)
    return {"response": result}


class SkillRequest(BaseModel):
    skill: str
    input: str
    persona: str | None = None


@router.post("/api/skill")
async def api_skill(req: SkillRequest):
    config = _core._harness_settings()
    context = await _core._accept_task_context(ChatRequest(message=req.input), config["runtime"], config)
    if context.runtime == "api":
        raise HTTPException(409, "API 直连模式支持聊天；SKILL 执行需要具备本机工具的 Agent 运行时")
    skill_full = _core.find_skill(req.skill)
    if skill_full is None:
        raise HTTPException(404, f"SKILL '{req.skill}' 不存在")
    message = f"{_persona_prefix(req.persona)}请执行 /{skill_full}，内容如下：\n\n{req.input}"
    # 统一给足超时：制作类 SKILL（生视频/多镜合成）可能跑很久，取安全上界
    timeout = TIMEOUT_PRODUCE
    result = await asyncio.shield(_core._submit_sync_task(message, timeout, context))
    return {"response": result}


@router.delete("/api/session/{session_key}")
async def api_delete_session(session_key: str):
    """删除 OpenClaw 本地的 session 记录。"""
    if session_key in _core._SYNC_SESSIONS or session_key in _core._RUNNING_CHAT or _core._session_lock(session_key).locked():
        raise HTTPException(409, "请先停止当前对话再删除")
    record = harness_store.binding(_core.SESSIONS_DIR, session_key)
    if record and record.get("runtime") not in ("api", "openclaw"):
        from easel.harnesses.rpc import close_sessions
        await close_sessions(session_key)
        harness_store.binding_path(_core.SESSIONS_DIR, session_key).unlink(missing_ok=True)
        _core._turn_file(f"web:{session_key}").unlink(missing_ok=True)
        return {"deleted": True, "nativeHistoryDeleted": False}

    def missing_history(reason):
        # A failed first turn can own Easel state before native history exists.
        if record:
            harness_store.binding_path(_core.SESSIONS_DIR, session_key).unlink(missing_ok=True)
            _core._turn_file(f"web:{session_key}").unlink(missing_ok=True)
            return {"deleted": True, "nativeHistoryDeleted": False}
        return {"deleted": False, "reason": reason}

    direct_history = history_path(_core.SESSIONS_DIR, session_key)
    if direct_history.is_file():
        if session_key in _core._RUNNING_CHAT:
            raise HTTPException(409, "请先停止当前对话再删除")
        direct_history.unlink()
        harness_store.binding_path(_core.SESSIONS_DIR, session_key).unlink(missing_ok=True)
        _core._turn_file(f"web:{session_key}").unlink(missing_ok=True)
        return {"deleted": True}
    if _core._direct_api_enabled():
        return missing_history("session not found")
    sessions_file = _core._oc_state_dir() / 'agents' / 'main' / 'sessions' / 'sessions.json'
    if not sessions_file.is_file():
        return missing_history("sessions file not found")
    data = json.loads(sessions_file.read_text(encoding="utf-8"))
    full_key = f'agent:main:{session_key}' if not session_key.startswith('agent:') else session_key
    for key in (full_key, session_key):
        if key in data:
            del data[key]
            sessions_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            harness_store.binding_path(_core.SESSIONS_DIR, session_key).unlink(missing_ok=True)
            _core._turn_file(f"web:{session_key}").unlink(missing_ok=True)
            return {'deleted': True}
    return missing_history("session not found")


def create_router(core):
    """Bind the core app module and return this domain's routes."""
    global _core, DEBUG_DIR
    _core = core
    DEBUG_DIR = _core.OUTPUTS_DIR / "_debug"   # 诊断日志（对话流收尾情况等），_ 前缀不进内容库
    return router
