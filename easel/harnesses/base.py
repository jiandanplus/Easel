"""Internal contract for agent harness integrations."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Protocol


class HarnessError(Exception):
    pass


Emit = Callable[[str, str], None]
Ask = Callable[[list[dict]], Awaitable[dict[str, list[str]]]]
Bind = Callable[[str], None]


@dataclass(frozen=True)
class Descriptor:
    id: str
    label: str
    protocol: str
    install_hint: str


@dataclass(frozen=True)
class RunRequest:
    prompt: str
    cwd: Path
    env: dict[str, str]
    native_id: str = ""
    model: str = ""
    sandbox: str = "workspace-write"
    timeout: int = 1800
    command: tuple[str, ...] = ()
    session_key: str = ""
    codex_auto_approve: bool = False
    service_source: str = "custom"
    execution_target: str = "local"
    account_id: str = ""
    platform_url: str = ""
    subscription_backend: str = "legacy"
    central_scope: dict = field(default_factory=dict)
    invocation_key: str = ""
    reasoning_effort: str = ""
    reasoning_efforts: tuple[str, ...] = ()
    selection_verified: bool = False
    provider_binding: str = ""
    turn_id: str = ""


class Adapter(Protocol):
    descriptor: Descriptor

    async def run(self, request: RunRequest, emit: Emit, ask: Ask, bind: Bind) -> None: ...
