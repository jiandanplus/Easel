"""Private workspace configuration for the bundled Responses provider.

The provider file is the only authority for the bundled model. Secret actions are
explicit: preserve at the same endpoint, replace with a new key, or clear.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

from .base import HarnessError


def _text(value, limit, *, empty=False):
    return (isinstance(value, str) and len(value) <= limit and (empty or bool(value))
            and value == value.strip() and not any(ord(c) < 32 or ord(c) == 127 for c in value))


def _validate(base_url, model, key):
    if not _text(base_url, 2048) or not _text(model, 240) or not _text(key, 16384, empty=True):
        raise HarnessError("供应商地址、模型和 Key 必须有效且不含控制字符")
    try:
        url = urlsplit(base_url)
        if (url.scheme not in ("https", "http") or not url.hostname or url.username is not None
                or url.password is not None or url.query or url.fragment or url.port == 0
                or any(c.isspace() for c in base_url) or "\\" in base_url):
            raise ValueError
    except ValueError as error:
        raise HarnessError("供应商地址必须是无凭证、查询或片段的 HTTP(S) URL") from error


def _path(root: Path) -> Path:
    root = root.resolve()
    path = root / "outputs" / "_harness" / "bundled-provider.json"
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise HarnessError("供应商设置必须保存在工作区内的普通文件")
    return path


def read_provider(root: Path) -> dict:
    path = _path(root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or set(data) != {"base_url", "model", "api_key"}:
            raise ValueError
        _validate(data["base_url"], data["model"], data["api_key"])
        return data
    except (OSError, ValueError, TypeError, HarnessError) as error:
        raise HarnessError("内置供应商设置损坏，请检查本地文件；不会覆盖原配置") from error


def provider_status(root: Path) -> dict:
    data = read_provider(root)
    return {"configured": bool(data.get("api_key")), "base_url": data.get("base_url", ""),
            "model": data.get("model", ""), "key_present": bool(data.get("api_key"))}


def require_provider(root: Path) -> dict:
    data = read_provider(root)
    if not data.get("api_key"):
        raise HarnessError("请先在设置 → Agent 底座 → 内置 Codex 供应商填写 API 地址、模型和 Key")
    return data


def save_provider(root: Path, *, base_url: str, model: str, secret_action: str = "preserve", api_key: str = "") -> dict:
    _validate(base_url, model, api_key)
    if secret_action not in ("preserve", "replace", "clear"):
        raise HarnessError("请选择保留、替换或清除 Key")
    current = read_provider(root)
    if secret_action != "replace" and api_key:
        raise HarnessError("填写 Key 时必须选择替换")
    if secret_action == "replace" and not api_key:
        raise HarnessError("替换 Key 时请输入新 Key")
    if secret_action == "preserve" and current.get("api_key") and base_url != current.get("base_url"):
        raise HarnessError("API 地址已更改，请重新输入 Key；不会向新地址转发旧 Key")
    key = api_key if secret_action == "replace" else current.get("api_key", "") if secret_action == "preserve" else ""
    path = _path(root)
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
        temporary = Path(name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump({"base_url": base_url, "model": model, "api_key": key}, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    except OSError as error:
        raise HarnessError("供应商设置保存失败，原配置保持不变") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return provider_status(root)
