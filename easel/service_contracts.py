"""Nonsecret service routing and immutable per-task execution contracts."""
from dataclasses import dataclass
from easel.harnesses.base import HarnessError

SERVICE_SOURCES = ("custom", "subscription")
EXECUTION_TARGETS = ("local", "cloud")
RUNTIMES = ("api", "openclaw", "opencode", "codex", "acp")


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
             "reason": ("订阅服务仅支持本地 Codex，需平台账号与可用测试授权" if source == "subscription" and target == "local"
                        else "" if target == "local" else "云端执行尚未开放")}
            for source in SERVICE_SOURCES for target in EXECUTION_TARGETS]


@dataclass(frozen=True)
class TaskContext(ServiceRoute):
    runtime: str = "api"
    model: str = ""
    account_id: str = ""
    platform_url: str = ""

    def __post_init__(self):
        super().__post_init__()
        if self.runtime not in RUNTIMES:
            raise HarnessError("无效的执行引擎 runtime")
        if not isinstance(self.model, str) or len(self.model) > 240 or any(ord(c) < 32 for c in self.model):
            raise HarnessError("无效的任务模型")

    def metadata(self):
        return {"service_source": self.service_source, "execution_target": self.execution_target,
                "runtime": self.runtime, "model": self.model, "account_id": self.account_id,
                "model_resolution": ("gateway-agent-default" if self.runtime == "openclaw" else
                                     "explicit" if self.model else "native-default"),
                "chat": True, "tools": self.runtime != "api"}


def task_context(config: dict, *, root=None) -> TaskContext:
    """Shared acceptance seam. Subscription authority comes only from server."""
    from pathlib import Path
    runtime = config["runtime"]
    route = ServiceRoute(config["service_source"], config["execution_target"])
    route.require_available()
    if route.service_source == "subscription":
        if runtime != "codex":
            raise HarnessError("订阅服务目前仅支持本地 Codex")
        from easel.platform.client import PlatformClient
        model = config.get("subscription_model", "")
        identity = PlatformClient(root or Path.cwd()).authorize(model)
        return TaskContext(route.service_source, route.execution_target, runtime, model,
                           identity["account_id"], identity["platform_url"])
    model = config["models"].get(runtime, "")
    if runtime == "codex":
        from easel.harnesses.runtime import codex_model
        model = codex_model(root or Path.cwd(), model)
    return TaskContext(route.service_source, route.execution_target, runtime, model)
