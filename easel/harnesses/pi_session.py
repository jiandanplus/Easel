"""Pi-only private history. Native identifiers cannot name foreign agent sessions."""
import json
import os
from pathlib import Path
import re
import stat
import uuid
from urllib.parse import urlsplit, urlunsplit
from .base import HarnessError

MAX_HISTORY = 2 * 1024 * 1024
MAX_MESSAGES = 256


def validate_messages(messages):
    if not isinstance(messages, list) or len(messages) > MAX_MESSAGES:
        raise HarnessError("Pi 历史超出上限，请新建对话")
    if len(json.dumps(messages, ensure_ascii=False).encode()) > MAX_HISTORY:
        raise HarnessError("Pi 历史超出上限，请新建对话")
    # The core reconstructs its own system baseline/tools on every invocation.
    # Persist only supported conversation roles, never client-supplied systems.
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in ("user", "assistant", "toolResult"):
            raise HarnessError("Pi 历史格式无效")
        content = message.get("content")
        if not isinstance(content, (str, list)):
            raise HarnessError("Pi 历史格式无效")
    return messages


def canonical_provider(base_url):
    try:
        url = urlsplit(base_url)
        if (url.scheme not in ("http", "https") or not url.hostname or url.username or url.password
                or url.query or url.fragment):
            raise ValueError
        host = url.hostname.lower()
        if ":" in host: host = "[" + host + "]"
        if url.port is not None and (url.scheme, url.port) not in (("http", 80), ("https", 443)):
            host += ":" + str(url.port)
        return urlunsplit((url.scheme.lower(), host, url.path.rstrip("/"), "", ""))
    except (ValueError, TypeError, AttributeError):
        raise HarnessError("Pi 供应商地址无效") from None


def binding(request, model, provider_base_url=""):
    return {"model": model, "service_source": request.service_source,
            "subscription_backend": request.subscription_backend if request.service_source == "subscription" else "",
            "account_id": request.account_id, "platform_url": request.platform_url,
            "central_scope": request.central_scope,
            "provider_base_url": canonical_provider(provider_base_url) if request.service_source == "custom" else ""}


class PiSession:
    def __init__(self, request, model, provider_base_url=""):
        self.binding = binding(request, model, provider_base_url)
        self.id = request.native_id or "pi:" + str(uuid.uuid4())
        if not re.fullmatch(r"pi:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", self.id):
            raise HarnessError("Pi 会话 ID 无效；旧引擎对话不能作为 Pi 历史恢复")
        self.root = request.cwd.resolve()
        self.directory = self.root / "outputs/_harness/pi-sessions"
        self.name = self.id[3:] + ".json"
        self.messages = []
        self._prepare()
        if request.native_id:
            self.messages = self._read()

    def _prepare(self):
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in ("outputs", "_harness", "pi-sessions"):
                try: os.mkdir(part, 0o700, dir_fd=descriptor)
                except FileExistsError: pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            if os.fstat(descriptor).st_uid != os.getuid():
                raise OSError("wrong owner")
            os.fchmod(descriptor, 0o700)
        except OSError:
            raise HarnessError("Pi 私有历史目录不可用") from None
        finally:
            os.close(descriptor)

    def _open_directory(self):
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in ("outputs", "_harness", "pi-sessions"):
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def _read(self):
        directory = self._open_directory()
        try:
            descriptor = os.open(self.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_HISTORY + 16384
                        or info.st_uid != os.getuid() or info.st_mode & 0o077):
                    raise ValueError
                data = json.load(stream)
            previous = data.get("binding")
            # Model choice may vary per turn; provider and identity ownership cannot.
            same_owner = (isinstance(previous, dict) and set(previous) == set(self.binding)
                          and all(previous[key] == value for key, value in self.binding.items() if key != "model"))
            if data.get("schema_version") != 1 or data.get("session_id") != self.id or not same_owner:
                raise ValueError
            return validate_messages(data["messages"])
        except (OSError, ValueError, KeyError, TypeError):
            raise HarnessError("Pi 私有历史缺失或不属于当前服务身份，请新建对话") from None
        finally:
            os.close(directory)

    def save(self, messages):
        messages = validate_messages(messages)
        directory = self._open_directory()
        temporary = self.name + "." + uuid.uuid4().hex + ".tmp"
        try:
            descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump({"schema_version": 1, "session_id": self.id, "binding": self.binding,
                           "messages": messages}, stream, ensure_ascii=False, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.name, src_dir_fd=directory, dst_dir_fd=directory)
        except (OSError, ValueError, TypeError):
            raise HarnessError("Pi 私有历史保存失败") from None
        finally:
            try: os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError: pass
            os.close(directory)
