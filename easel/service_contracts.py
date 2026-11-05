"""Nonsecret service routing and immutable per-task execution contracts."""
from dataclasses import dataclass, field
from easel.harnesses.base import HarnessError

SERVICE_SOURCES = ("custom", "subscription")
EXECUTION_TARGETS = ("local", "cloud")
from easel.harnesses.capabilities import RUNTIMES, supports_subscription


@dataclass(frozen=True)
class ServiceRoute:
    service_source: str = "custom"
    execution_target: str = "local"

    def __post_init__(self):
        if self.service_source not in SERVICE_SOURCES:
            raise HarnessError("无效的服务来源 service_source")
        if self.execution_target not in EXECUTION_TARGETS:
            raise HarnessError("无效的执行位置 execution_target")

    def require_available(self):
        if self.execution_target != "local":
            raise HarnessError("云端执行尚未开放；请选择本地执行")


def availability():
    return [{"service_source": source, "execution_target": target,
             "available": target == "local",
             "reason": ("中央订阅支持内置 Pi 和 Codex，需平台账号与对应模型授权" if source == "subscription" and target == "local"
                        else "" if target == "local" else "云端执行尚未开放")}
            for source in SERVICE_SOURCES for target in EXECUTION_TARGETS]


@dataclass(frozen=True)
class TaskContext(ServiceRoute):
    runtime: str = "api"
    model: str = ""
    account_id: str = ""
    platform_url: str = ""
    subscription_backend: str = "legacy"
    central_scope: dict = field(default_factory=dict)
    invocation_key: str = ""
    reasoning_effort: str = ""
    reasoning_efforts: tuple[str, ...] = ()
    selection_verified: bool = False
    provider_binding: str = ""

    def __post_init__(self):
        super().__post_init__()
        if self.subscription_backend not in ("legacy", "central"):
            raise HarnessError("无效的订阅后端")
        if self.runtime not in RUNTIMES:
            raise HarnessError("无效的执行引擎 runtime")
        if not isinstance(self.model, str) or len(self.model) > 240 or any(ord(c) < 32 for c in self.model):
            raise HarnessError("无效的任务模型")
        from easel.chat_models import EFFORTS
        if (type(self.selection_verified) is not bool or not isinstance(self.reasoning_efforts, tuple)
                or any(effort not in EFFORTS for effort in self.reasoning_efforts)
                or (self.reasoning_effort and (not self.selection_verified or self.reasoning_effort not in self.reasoning_efforts))):
            raise HarnessError("无效的任务推理强度")

    def metadata(self):
        return {"service_source": self.service_source, "execution_target": self.execution_target,
                "runtime": self.runtime, "model": self.model, "account_id": self.account_id,
                **({"subscription_backend": self.subscription_backend, "platform_url": self.platform_url,
                    "central_scope": dict(self.central_scope)} if self.service_source == "subscription" else {}),
                "model_resolution": ("gateway-agent-default" if self.runtime == "openclaw" else
                                     "explicit" if self.model else "native-default"),
                "chat": True, "tools": self.runtime != "api",
                **({"reasoning_effort": self.reasoning_effort} if self.reasoning_effort else {})}


def task_context(config: dict, *, root=None, selection=None) -> TaskContext:
    """Shared acceptance seam. Subscription authority comes only from server."""
    from pathlib import Path
    from dataclasses import replace
    from easel.chat_models import ModelSelection
    if selection is not None and not isinstance(selection, ModelSelection):
        raise HarnessError("任务模型选择尚未通过目录验证")
    def accepted(context):
        return (replace(context, reasoning_effort=selection.reasoning_effort,
                        reasoning_efforts=selection.reasoning_efforts, selection_verified=True,
                        provider_binding=selection.provider_binding)
                if selection is not None else context)
    runtime = config["runtime"]
    route = ServiceRoute(config["service_source"], config["execution_target"])
    route.require_available()
    if route.service_source == "subscription":
        backend = config.get("subscription_backend", "legacy")
        if not supports_subscription(runtime, backend):
            raise HarnessError("请选择支持此订阅渠道的执行引擎；内置 Pi 支持中央订阅，Codex 保留旧版订阅")
        from easel.platform.client import PlatformClient
        model = selection.model if selection is not None else config.get("subscription_model", "")
        if backend == "central":
            from easel.platform.central import CentralClient
            from uuid import uuid4
            identity = CentralClient(root or Path.cwd()).authorize(model)
            return accepted(TaskContext(route.service_source, route.execution_target, runtime, model,
                               identity["account_id"], identity["platform_url"], backend,
                               identity["scope"], str(uuid4())))
        if backend != "legacy":
            raise HarnessError("无效的订阅后端")
        identity = PlatformClient(root or Path.cwd()).authorize(model)
        return accepted(TaskContext(route.service_source, route.execution_target, runtime, model,
                           identity["account_id"], identity["platform_url"]))
    model = selection.model if selection is not None else config["models"].get(runtime, "")
    if runtime == "codex":
        from easel.harnesses.runtime import codex_model
        model = codex_model(root or Path.cwd(), model)
    elif runtime == "pi" and selection is None:
        from easel.harnesses.provider import pi_model
        model = pi_model(root or Path.cwd(), model)
    return accepted(TaskContext(route.service_source, route.execution_target, runtime, model))
