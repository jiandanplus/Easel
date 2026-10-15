"""Deterministic native Codex payload resolution (manifest schema version 1).

Manifest: schema_version=1, platform="darwin", architecture="arm64",
codex={version, executable (relative POSIX path), sha256},
files={relative POSIX payload path: sha256}. The executable must appear in files.
Every declared file is verified; build tooling must enumerate every payload file.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re

from .base import HarnessError


@dataclass(frozen=True)
class CodexResolution:
    executable: Path | None
    origin: str
    version: str = ""


_HASH_CACHE: dict[tuple, str] = {}


def _payload(root: Path, value: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value or "\0" in value:
        raise ValueError("invalid payload path")
    relative = PurePosixPath(value)
    if relative.is_absolute() or any(part in ("..", ".") for part in value.split("/")):
        raise ValueError("unbounded payload path")
    result = (root / value).resolve(strict=True)
    if not result.is_relative_to(root) or not result.is_file():
        raise ValueError("payload escapes bundle or is not a file")
    return result


def _digest(path: Path) -> str:
    stat = path.stat()
    key = (str(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    if key not in _HASH_CACHE:
        if len(_HASH_CACHE) > 2048:
            _HASH_CACHE.clear()
        with path.open("rb") as stream:
            _HASH_CACHE[key] = hashlib.file_digest(stream, "sha256").hexdigest()
    return _HASH_CACHE[key]


def resolve_codex() -> CodexResolution:
    manifest_value = os.environ.get("EASEL_RUNTIME_MANIFEST")
    if manifest_value is not None:
        try:
            if not manifest_value:
                raise ValueError("empty manifest")
            manifest = Path(manifest_value).expanduser().resolve(strict=True)
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if data["schema_version"] != 1 or isinstance(data["schema_version"], bool):
                raise ValueError("unsupported schema")
            if (data["platform"], data["architecture"]) != ("darwin", "arm64") or (
                    platform.system().lower(), platform.machine().lower()) != ("darwin", "arm64"):
                raise ValueError("incompatible platform or architecture")
            engine = data["codex"]
            if not isinstance(engine["version"], str) or not re.fullmatch(r"[A-Za-z0-9.+_-]{1,80}", engine["version"]):
                raise ValueError("invalid version")
            files = data["files"]
            if not isinstance(files, dict) or not files or files.get(engine["executable"]) != engine["sha256"]:
                raise ValueError("executable digest missing")
            for relative, expected in files.items():
                if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
                    raise ValueError("invalid digest")
                if _digest(_payload(manifest.parent, relative)) != expected:
                    raise ValueError("payload digest mismatch")
            executable = _payload(manifest.parent, engine["executable"])
            if not os.access(executable, os.X_OK) or not executable.stat().st_mode & 0o111:
                raise ValueError("payload is not executable")
            return CodexResolution(executable, "bundled", engine["version"])
        except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError) as error:
            # Do not interpolate untrusted manifest text or credentials into settings responses.
            raise HarnessError("内置 Codex 运行时缺失、不兼容或校验失败；请重新安装适用于 macOS ARM64 的完整试验包") from error
    override = os.environ.get("EASEL_CODEX_EXECUTABLE")
    if override is not None:
        executable = Path(override)
        if not override or not executable.is_absolute() or not executable.is_file() or not os.access(executable, os.X_OK):
            raise HarnessError("EASEL_CODEX_EXECUTABLE 必须指向有效的绝对可执行文件路径")
        return CodexResolution(executable.resolve(), "override")
    return CodexResolution(None, "external")


def codex_environment(env: dict[str, str], cwd: Path, *, private=False) -> dict[str, str]:
    """Shared model/task environment seam; never import an external Codex home."""
    result = dict(env)
    if private or resolve_codex().origin == "bundled":
        home = cwd.resolve() / "outputs" / "_harness" / "codex-home"
        if home.is_symlink() or not home.resolve().is_relative_to(cwd.resolve()):
            raise HarnessError("内置 Codex 私有目录不能指向工作区外部")
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        home.chmod(0o700)
        result["CODEX_HOME"] = str(home)
    return result


def codex_model(cwd: Path, model: str, *, require_configured: bool = True) -> str:
    """Resolve the actual bundled model; external overrides retain native behavior."""
    if resolve_codex().origin != "bundled":
        return model
    from .provider import read_provider, require_provider
    config = require_provider(cwd) if require_configured else read_provider(cwd)
    selected = config.get("model", "") if config.get("api_key") else ""
    if model and model != selected:
        raise HarnessError("内置 Codex 仅使用供应商设置中的模型，请在内置供应商设置中更改")
    return selected


def prepare_codex(request, command: list[str]):
    """Shared command, model and credential preparation for native Codex calls."""
    if request.service_source == "subscription":
        return prepare_subscription(request, command)
    if request.execution_target != "local":
        raise HarnessError("云端执行尚未开放")
    if resolve_codex().origin != "bundled":
        return request, command
    from dataclasses import replace
    from .provider import require_provider
    data = require_provider(request.cwd)
    model = codex_model(request.cwd, request.model)
    env = codex_environment(request.env, request.cwd)
    for key in ("OPENAI_API_KEY", "OPENAI_ACCESS_TOKEN", "CODEX_API_KEY", "EASEL_BUNDLED_API_KEY",
                "OPENAI_BASE_URL", "OPENAI_ORG_ID", "OPENAI_ORGANIZATION", "OPENAI_PROJECT_ID"):
        env.pop(key, None)
    env["EASEL_BUNDLED_API_KEY"] = data["api_key"]
    # JSON strings are also valid TOML basic strings; no key ever enters argv.
    config = ["model_provider=" + json.dumps("easel-custom"),
              "model=" + json.dumps(model),
              'model_providers.easel-custom.name="Easel custom"',
              "model_providers.easel-custom.base_url=" + json.dumps(data["base_url"]),
              'model_providers.easel-custom.env_key="EASEL_BUNDLED_API_KEY"',
              'model_providers.easel-custom.wire_api="responses"',
              'model_providers.easel-custom.requires_openai_auth=false']
    arguments = [part for value in config for part in ("-c", value)]
    return replace(request, env=env, model=model), [command[0], *arguments, *command[1:]]


def prepare_subscription(request, command):
    from dataclasses import replace
    from easel.platform.client import PlatformClient
    if request.execution_target != "local" or not request.account_id or not request.platform_url or not request.model:
        raise HarnessError("订阅任务缺少已接受的账号或模型身份")
    token = PlatformClient(request.cwd).task_credential(request.account_id, request.model, request.platform_url)
    env = codex_environment(request.env, request.cwd, private=True)
    for key in ("OPENAI_API_KEY", "OPENAI_ACCESS_TOKEN", "CODEX_API_KEY", "EASEL_BUNDLED_API_KEY",
                "OPENAI_BASE_URL", "OPENAI_ORG_ID", "OPENAI_ORGANIZATION", "OPENAI_PROJECT_ID"):
        env.pop(key, None)
    for key in list(env):
        if key.startswith("EASEL_PLATFORM_"):env.pop(key, None)
    env["EASEL_SUBSCRIPTION_API_KEY"] = token
    config = ['model_provider="easel-subscription"', "model=" + json.dumps(request.model),
              'model_providers.easel-subscription.name="Easel subscription"',
              "model_providers.easel-subscription.base_url=" + json.dumps(request.platform_url + "/v1"),
              'model_providers.easel-subscription.env_key="EASEL_SUBSCRIPTION_API_KEY"',
              'model_providers.easel-subscription.wire_api="responses"',
              'model_providers.easel-subscription.requires_openai_auth=false',
              'disable_response_storage=true',
              'web_search="disabled"']
    arguments = [part for value in config for part in ("-c", value)]
    return replace(request, env=env), [command[0], *arguments, *command[1:]]
