"""Server-owned, short-lived model choices. Catalogs never contain credentials."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import stat
import threading
import time

import httpx

from easel.harnesses.base import HarnessError

EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max")
MAX_CATALOG = 2 * 1024 * 1024
CATALOG_TTL = 120
_BINDING_SECRET = secrets.token_bytes(32)


def provider_binding(provider):
    raw = json.dumps(provider, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(_BINDING_SECRET, raw, hashlib.sha256).hexdigest()


def native_config_binding(env):
    """Hash native authority privately; no native credential enters the catalog."""
    home = Path(env.get("CODEX_HOME") or Path(env.get("HOME") or Path.home()) / ".codex")
    result = {"directory": str(home.resolve()), "files": {}}
    for name in ("auth.json", "config.toml"):
        path = home / name
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            result["files"][name] = None
            continue
        except OSError:
            raise HarnessError("无法确认原生模型账号配置，请检查原生配置后重试") from None
        try:
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CATALOG:
                    raise HarnessError("原生模型账号配置超出安全读取范围")
                raw = stream.read(MAX_CATALOG + 1)
                if len(raw) > MAX_CATALOG:
                    raise HarnessError("原生模型账号配置超出安全读取范围")
                result["files"][name] = hashlib.sha256(raw).hexdigest()
        except OSError:
            raise HarnessError("无法确认原生模型账号配置，请检查原生配置后重试") from None
    return result


def text(value, limit=240):
    return (isinstance(value, str) and 0 < len(value) <= limit and value == value.strip()
            and not any(ord(c) < 32 or ord(c) == 127 for c in value))


def normalize_reasoning(row):
    """Only explicit interface metadata enables controls; model names are inert."""
    raw = row.get("reasoning")
    values = raw.get("efforts") if isinstance(raw, dict) else row.get("supported_reasoning_efforts")
    if isinstance(raw, dict) and raw.get("status") == "unknown":
        return {"status": "unknown", "efforts": []}
    if raw is False or (isinstance(raw, dict) and raw.get("status") == "unsupported"):
        return {"status": "unsupported", "efforts": []}
    if not isinstance(values, list):
        return {"status": "unknown", "efforts": []}
    efforts = [value for value in EFFORTS if value in values]
    return {"status": "supported" if efforts else "unsupported", "efforts": efforts}


def clean_models(rows, selected=""):
    if not isinstance(rows, list) or len(rows) > 1000:
        raise HarnessError("模型目录格式无效，请刷新后重试")
    result = {}
    for row in rows:
        if not isinstance(row, dict) or not text(row.get("id")):
            continue
        identifier = row["id"]
        label = row.get("label", row.get("name", identifier))
        item = {"id": identifier, "label": label if text(label) else identifier,
                "default": identifier == selected or row.get("default") is True,
                "reasoning": normalize_reasoning(row)}
        # Duplicate public IDs with conflicting capabilities are ambiguous.
        if identifier in result and result[identifier] != item:
            raise HarnessError("模型目录包含冲突项，请联系接口提供方")
        result[identifier] = item
    return list(result.values())


def provider_models(provider, *, transport=None):
    """GET only the configured provider's own /models; never follow redirects."""
    try:
        deadline = time.monotonic() + 15
        headers = {"Authorization": "Bearer " + provider["api_key"]} if provider.get("api_key") else {}
        with httpx.Client(transport=transport, trust_env=False, follow_redirects=False,
                          timeout=httpx.Timeout(15, connect=5)) as client:
            with client.stream("GET", provider["base_url"].rstrip("/") + "/models", headers=headers) as response:
                if response.status_code != 200:
                    raise ValueError
                raw = bytearray()
                for chunk in response.iter_bytes():
                    if time.monotonic() >= deadline or len(raw) + len(chunk) > MAX_CATALOG:
                        raise ValueError
                    raw.extend(chunk)
                value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError
        return clean_models(value.get("data", value.get("models")))
    except (httpx.HTTPError, ValueError, TypeError, KeyError, RecursionError):
        raise HarnessError("无法读取当前接口的模型目录，请检查接口连接后刷新") from None


@dataclass(frozen=True)
class ModelSelection:
    model: str
    reasoning_effort: str = ""
    reasoning_efforts: tuple[str, ...] = ()
    provider_binding: str = ""


class CatalogRegistry:
    """Bounded process-private leases, bound to source/identity/credential changes."""
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self._secret = secrets.token_bytes(32)
        self._entries = {}
        self._leases = {}
        self._lock = threading.RLock()

    def _snapshot(self, root, config, env):
        root = Path(root).resolve()
        runtime = config["runtime"]
        source = config.get("service_source", "custom")
        backend = config.get("subscription_backend", "legacy")
        identity = {"root": str(root), "runtime": runtime, "service_source": source,
                    "subscription_backend": backend, "execution_target": config.get("execution_target", "local")}
        selected = config.get("subscription_model", "") if source == "subscription" else config.get("models", {}).get(runtime, "")
        identity["selected_model"] = selected
        ready, switchable, reason, loader = True, False, "此执行引擎沿用设置中的默认模型，暂不支持在对话中切换", lambda: []
        credential = ""
        if config.get("execution_target", "local") != "local":
            ready, reason = False, "云端执行尚未开放"
        elif source == "subscription":
            from easel.harnesses.capabilities import supports_subscription
            if not supports_subscription(runtime, backend):
                ready, reason = False, "当前执行引擎不支持此订阅渠道"
            else:
                if backend == "central":
                    from easel.platform.central import CentralClient
                    client = CentralClient(root)
                    with client.store.locked():
                        state = client._state()
                else:
                    from easel.platform.client import PlatformClient
                    client = PlatformClient(root)
                    with client.store.locked():
                        state = client.store.read()
                identity["platform"] = {"url": state.get("url"), "auth": state.get("auth"),
                                        "client_id": getattr(client, "client_id", "")}
                if not state.get("auth"):
                    ready, reason = False, "请先登录订阅账号，再加载可用模型"
                else:
                    switchable, reason = True, ""
                    loader = lambda: client.models().get("models", [])
        elif runtime == "pi":
            from easel.harnesses.provider import require_provider
            provider = require_provider(root)
            identity["provider"] = provider
            credential = provider_binding(provider)
            selected = provider["model"]
            identity["selected_model"] = selected
            switchable, reason = True, ""
            loader = lambda: provider_models(provider)
        elif runtime == "api":
            from easel.direct_api import api_config
            base, selected, key = api_config(env)
            provider = {"base_url": base, "model": selected, "api_key": key}
            identity["provider"] = provider
            credential = provider_binding(provider)
            identity["selected_model"] = selected
            switchable, reason = True, ""
            loader = lambda: provider_models(provider)
        elif runtime == "codex":
            from easel.harnesses.runtime import resolve_codex
            if resolve_codex().origin != "bundled":
                # Native config and auth remain native. Only model discovery is used.
                from easel.harnesses.base import RunRequest
                from easel.harnesses.models import available_models
                identity["native_env"] = env
                identity["native_authority"] = native_config_binding(env)
                request = RunRequest("", root, dict(env))
                loader = lambda: asyncio.run(available_models(runtime, request))
                switchable, reason = True, ""
        material = json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        authority = hmac.new(self._secret, material, hashlib.sha256).hexdigest()
        stable_identity = json.loads(material)
        platform = stable_identity.get("platform")
        if isinstance(platform, dict) and isinstance(platform.get("auth"), dict):
            # A catalog read may refresh this same authenticated session. Only
            # token values/deadlines may rotate; scope, account and session stay bound.
            for field in ("access_token", "refresh_token", "expires_at", "refresh_expires_at"):
                platform["auth"].pop(field, None)
        stable = hmac.new(self._secret, json.dumps(stable_identity, sort_keys=True).encode(), hashlib.sha256).hexdigest()
        public = {"runtime": runtime, "service_source": source, "subscription_backend": backend,
                  "selected_model": selected, "switchable": switchable, "ready": ready, "reason": reason}
        return authority, public, loader, credential, stable

    def catalog(self, root, config, env, *, refresh=False):
        with self._lock:
            authority, public, loader, credential, stable = self._snapshot(root, config, env)
            now = self.clock()
            self._entries = {key: value for key, value in self._entries.items() if value["expires"] > now}
            self._leases = {key: value for key, value in self._leases.items() if value["expires"] > now}
            cached = self._entries.get(authority)
            if cached and not refresh:
                result = json.loads(json.dumps(cached["public"]))
                result["expires_in"] = max(0, int(cached["expires"] - now))
                return result
            # A failed explicit refresh must not keep a previously usable lease.
            self._entries.pop(authority, None)
            try:
                rows = clean_models(loader(), public["selected_model"]) if public["ready"] and public["switchable"] else []
            except BaseException:
                self._leases = {key: value for key, value in self._leases.items() if value["stable"] != stable}
                raise
            if public["runtime"] not in ("pi", "api"):
                # These adapters support model choice but do not carry a verified
                # reasoning value to their provider protocol yet.
                for row in rows:
                    row["reasoning"] = {"status": "unknown", "efforts": []}
            # A successful read can refresh central access credentials. Bind the post-read state.
            after, final, _, final_credential, after_stable = self._snapshot(root, config, env)
            if final != public or stable != after_stable or credential != final_credential:
                self._leases = {key: value for key, value in self._leases.items() if value["stable"] != stable}
                raise HarnessError("接口或账号已改变，请重新加载模型目录")
            revision = secrets.token_urlsafe(32)
            # UI source identity survives token renewal within the same central
            # session. Leases remain bound to the stable authority and provider.
            source_revision = hmac.new(self._secret, (after_stable + json.dumps(rows, sort_keys=True)).encode(), hashlib.sha256).hexdigest()
            result = {**public, "revision": revision, "source_revision": source_revision,
                      "expires_in": CATALOG_TTL, "models": rows}
            if public["switchable"] and not rows:
                result.update(ready=False, reason="当前渠道没有可用模型，请检查账号权益或接口配置")
            self._entries.pop(authority, None)
            if len(self._entries) >= 64:
                self._entries.pop(next(iter(self._entries)))
            # Multiple composers and normal credential renewal share one stable
            # authority. Keep each issued lease until its own deadline, except
            # when a refresh discovers changed capabilities or unavailable state.
            self._leases = {key: value for key, value in self._leases.items()
                            if value["stable"] != after_stable or (result["ready"] and value["public"]["models"] == rows)}
            entry = {"expires": self.clock() + CATALOG_TTL, "public": result,
                     "provider_binding": credential, "stable": after_stable}
            self._entries[after] = entry
            if len(self._leases) >= 64:
                self._leases.pop(next(iter(self._leases)))
            self._leases[revision] = entry
            return json.loads(json.dumps(result))

    def select(self, root, config, env, model, revision, effort=""):
        if not text(model) or not text(revision, 160) or not isinstance(effort, str):
            raise HarnessError("模型选择无效，请刷新模型目录后重试")
        with self._lock:
            _, _, _, credential, stable = self._snapshot(root, config, env)
            entry = self._leases.get(revision)
            if (not entry or entry["expires"] <= self.clock()
                    or not hmac.compare_digest(entry["stable"], stable)
                    or entry["provider_binding"] != credential):
                raise HarnessError("模型目录已过期或接口账号已改变，请刷新模型列表后重试")
            value = entry["public"]
            rows = [row for row in value["models"] if row["id"] == model]
            if not value["ready"] or not value["switchable"] or len(rows) != 1:
                raise HarnessError("所选模型当前不可用，请刷新模型目录")
            reasoning = rows[0]["reasoning"]
            if effort and (reasoning["status"] != "supported" or effort not in reasoning["efforts"]):
                raise HarnessError("当前模型接口不支持所选推理强度，请重新选择")
            return ModelSelection(model, effort, tuple(reasoning["efforts"]), entry["provider_binding"])


catalogs = CatalogRegistry()
