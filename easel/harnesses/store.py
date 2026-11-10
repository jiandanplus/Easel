"""Atomic local harness settings and durable session ownership."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from .base import HarnessError

from .capabilities import RUNTIMES as IDS


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError
        return data
    except (OSError, ValueError) as error:
        raise HarnessError("Agent 设置或会话记录损坏，请检查本地文件；不会自动新建会话丢失历史") from error


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def settings(outputs: Path, env: dict[str, str], *, service_source: str | None = None) -> dict:
    """service_source, when given, replaces the saved choice (the automatic subscription rule)."""
    data = read_json(outputs / "_harness" / "config.json")
    from easel.service_contracts import ServiceRoute
    route = ServiceRoute(service_source or data.get("service_source", "custom"), data.get("execution_target", "local"))
    state_dir = Path(env.get("EASEL_OPENCLAW_STATE_DIR") or Path.home() / ".openclaw-easel")
    legacy = "EASEL_CHAT_TRANSPORT" in env or (state_dir / "openclaw.json").is_file()
    default = "openclaw" if legacy else "pi"
    runtime = env.get("EASEL_AGENT_RUNTIME", data.get("runtime", default))
    if not isinstance(runtime, str) or runtime.strip().lower() not in IDS:
        raise HarnessError("未知 Agent 底座，请在设置中选择已支持的底座")
    runtime = runtime.strip().lower()
    if runtime == "openclaw" and env.get("EASEL_CHAT_TRANSPORT", "").strip().lower() == "api":
        runtime = "api"
    models = data.get("models", {})
    if not isinstance(models, dict) or any(
            key not in IDS or not isinstance(value, str) or len(value) > 240
            or any(ord(character) < 32 for character in value)
            for key, value in models.items()):
        raise HarnessError("Agent 模型设置格式无效")
    from .runtime import resolve_codex
    if route.service_source == "custom" and (runtime == "pi" or (
            runtime == "codex" and resolve_codex().origin == "bundled")):
        from .provider import provider_status
        provider = provider_status(outputs.parent)
        models = {**models, runtime: provider["model"] if provider["configured"] else ""}
    backend = "central" if service_source == "subscription" else data.get("subscription_backend", "legacy")
    if backend not in ("legacy", "central"):
        raise HarnessError("无效的订阅后端")
    sandbox = data.get("sandbox", "workspace-write")
    if sandbox not in ("read-only", "workspace-write"):
        raise HarnessError("Agent 权限设置无效，只允许只读或工作区写入")
    auto_approve = data.get("codex_auto_approve", False)
    if not isinstance(auto_approve, bool):
        raise HarnessError("Codex 自动授权设置必须为开关")
    return {"runtime": runtime, "models": models, "sandbox": sandbox,
            "codex_auto_approve": auto_approve,
            "service_source": route.service_source, "execution_target": route.execution_target,
            "subscription_backend": backend,
            "subscription_model": data.get("subscription_model", "")}


def binding_path(sessions: Path, key: str) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return sessions / "harness" / f"{digest}.json"


def binding(sessions: Path, key: str) -> dict:
    record = read_json(binding_path(sessions, key))
    if record and (record.get("runtime") not in IDS
                   or not isinstance(record.get("native_id"), str)):
        raise HarnessError("Agent 会话记录格式无效，不会自动重置历史")
    if record:
        from easel.service_contracts import ServiceRoute
        route = ServiceRoute(record.get("service_source", "custom"), record.get("execution_target", "local"))
        record = {**record, "service_source": route.service_source, "execution_target": route.execution_target}
    return record


def bind(sessions: Path, key: str, runtime: str, native_id: str = "", *,
         service_source: str = "custom", execution_target: str = "local", account_id: str = "",
         subscription_backend: str = "legacy", platform_url: str = "", central_scope: dict | None = None) -> None:
    from easel.service_contracts import ServiceRoute
    route = ServiceRoute(service_source, execution_target)
    route.require_available()
    if runtime not in IDS or not isinstance(native_id, str):
        raise HarnessError("Agent 会话绑定无效")
    current = binding(sessions, key)
    if current and current.get("runtime") != runtime:
        raise HarnessError("此会话属于其他 Agent 底座，请新建对话")
    if current and (current["service_source"] != service_source or current["execution_target"] != execution_target):
        raise HarnessError("此会话属于其他服务来源或执行位置，请新建对话")
    if service_source == "subscription" and (not account_id or (current and current.get("account_id") != account_id)):
        raise HarnessError("此会话属于其他平台账号，请新建对话")
    if subscription_backend not in ("legacy", "central"):
        raise HarnessError("无效的订阅后端")
    if service_source == "subscription" and current:
        if current.get("subscription_backend", "legacy") != subscription_backend or (
                subscription_backend == "central" and (current.get("platform_url") != platform_url
                or current.get("central_scope") != central_scope)):
            raise HarnessError("此会话属于其他订阅后端、平台或账号范围，请新建对话")
    native_id = native_id or current.get("native_id", "")
    if current.get("native_id") and current["native_id"] != native_id:
        raise HarnessError("Agent 返回了不同的原生会话 ID，不会替换已有历史")
    write_json(binding_path(sessions, key), {**current, "runtime": runtime, "native_id": native_id,
               "service_source": service_source, "execution_target": execution_target,
               **({"subscription_backend": subscription_backend} if service_source == "subscription" else {}),
               **({"platform_url": platform_url, "central_scope": central_scope} if subscription_backend == "central" else {}), **({"account_id": account_id} if account_id else {})})
