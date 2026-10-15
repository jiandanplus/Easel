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
        if self.service_source != "custom" or self.execution_target != "local":
            raise HarnessError("订阅服务与云端执行尚未接通；当前仅支持 custom/local")


def availability():
    return [{"service_source": source, "execution_target": target,
             "available": source == "custom" and target == "local",
             "reason": "" if source == "custom" and target == "local" else "订阅服务与云端执行尚未接通"}
            for source in SERVICE_SOURCES for target in EXECUTION_TARGETS]


@dataclass(frozen=True)
class TaskContext(ServiceRoute):
    runtime: str = "api"
    model: str = ""

    def __post_init__(self):
        super().__post_init__()
        if self.runtime not in RUNTIMES:
            raise HarnessError("无效的执行引擎 runtime")
        if not isinstance(self.model, str) or len(self.model) > 240 or any(ord(c) < 32 for c in self.model):
            raise HarnessError("无效的任务模型")

    def metadata(self):
        return {"service_source": self.service_source, "execution_target": self.execution_target,
                "runtime": self.runtime, "model": self.model,
                "model_resolution": ("gateway-agent-default" if self.runtime == "openclaw" else
                                     "explicit" if self.model else "native-default"),
                "chat": True, "tools": self.runtime != "api"}
