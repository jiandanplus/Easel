"""Verify a complete, owned Pi SDK + Node payload; never consult PATH."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re

from easel.paths import resource_root
from .base import HarnessError


@dataclass(frozen=True)
class PiResolution:
    node: Path
    worker: Path
    origin: str
    version: str


_HASH_CACHE = {}


def _digest(path):
    info = path.stat()
    key = (str(path), info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    if key not in _HASH_CACHE:
        if len(_HASH_CACHE) > 20000:
            _HASH_CACHE.clear()
        with path.open("rb") as stream:
            _HASH_CACHE[key] = hashlib.file_digest(stream, "sha256").hexdigest()
    return _HASH_CACHE[key]


def _payload(root, name):
    if (not isinstance(name, str) or not name or "\\" in name or "\0" in name
            or PurePosixPath(name).is_absolute() or any(p in ("", ".", "..") for p in name.split("/"))):
        raise ValueError("invalid path")
    value = root
    for part in name.split("/"):
        value = value / part
        if value.is_symlink():
            raise ValueError("symlink")
    if not value.is_file() or not value.resolve().is_relative_to(root):
        raise ValueError("not a payload file")
    return value


def resolve_pi():
    try:
        explicit = os.environ.get("EASEL_PI_RUNTIME_MANIFEST")
        if explicit is not None and not explicit:
            raise ValueError("empty manifest")
        manifest = Path(explicit) if explicit is not None else resource_root() / "runtime/pi/runtime-manifest.json"
        if not manifest.is_absolute() or manifest.is_symlink():
            raise ValueError("invalid manifest path")
        manifest = manifest.resolve(strict=True)
        root = manifest.parent
        if manifest.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("manifest too large")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if type(data["schema_version"]) is not int or data["schema_version"] != 2:
            raise ValueError("unsupported schema")
        if ((data["platform"], data["architecture"]) != ("darwin", "arm64")
                or (platform.system().lower(), platform.machine().lower()) != ("darwin", "arm64")):
            raise ValueError("unsupported platform")
        node, pi, files = data["node"], data["pi"], data["files"]
        if pi["version"] != "1.4.0" or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", node["version"]):
            raise ValueError("invalid version")
        if tuple(map(int, node["version"].split("."))) < (22, 19, 0):
            raise ValueError("unsupported Node")
        if not isinstance(files, dict) or not 2 <= len(files) <= 50000:
            raise ValueError("invalid inventory")
        actual = set()
        for directory, dirs, names in os.walk(root, followlinks=False):
            for name in dirs:
                if (Path(directory) / name).is_symlink():
                    raise ValueError("linked directory")
            for name in names:
                path = Path(directory) / name
                if path == manifest:
                    continue
                if path.is_symlink() or not path.is_file():
                    raise ValueError("nonregular payload")
                actual.add(path.relative_to(root).as_posix())
        if actual != set(files):
            raise ValueError("incomplete inventory")
        for name, expected in files.items():
            if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise ValueError("invalid digest")
            if _digest(_payload(root, name)) != expected:
                raise ValueError("digest mismatch")
        executable, worker = _payload(root, node["executable"]), _payload(root, pi["worker"])
        if files.get(node["executable"]) != node["sha256"] or files.get(pi["worker"]) != pi["sha256"]:
            raise ValueError("missing entrypoint digest")
        if not os.access(executable, os.X_OK) or not executable.stat().st_mode & 0o111:
            raise ValueError("Node is not executable")
        return PiResolution(executable, worker, "bundled", pi["version"])
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError):
        raise HarnessError("内置 Pi 运行时缺失、不兼容或校验失败；请重新安装适用于 macOS ARM64 的完整安装包") from None


def pi_command():
    result = resolve_pi()
    return [str(result.node), str(result.worker)]
