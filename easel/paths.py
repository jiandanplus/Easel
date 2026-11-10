"""Explicit writable workspace and immutable application resource roots."""
import os
from pathlib import Path

_SOURCE_ROOT = Path(__file__).resolve().parents[1]


def workspace_root() -> Path:
    return Path(os.environ.get("EASEL_WORKSPACE_ROOT") or _SOURCE_ROOT).expanduser().resolve()


def resource_root() -> Path:
    return Path(os.environ.get("EASEL_RESOURCE_ROOT") or _SOURCE_ROOT).expanduser().resolve()
