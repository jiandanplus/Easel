"""Bounded, locked, atomic private session persistence."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import tempfile

try:
    import fcntl
except ImportError:  # Keep anonymous/custom desktop imports usable on Windows.
    fcntl = None
from easel.harnesses.base import HarnessError


class SessionStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.directory = self.root / "outputs" / "_harness"
        self.path = self.directory / "platform-session.json"

    def check(self):
        for path in (self.root / "outputs", self.directory, self.path):
            if path.is_symlink():
                raise HarnessError("平台账号文件不能使用符号链接")
        if not self.directory.resolve().is_relative_to(self.root):
            raise HarnessError("平台账号目录无效")

    @contextmanager
    def locked(self):
        if fcntl is None:
            raise HarnessError(
                "当前系统暂不支持平台账号安全文件锁；自定义服务可继续使用"
            )
        self.check()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        lock = self.directory / "platform-session.lock"
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "a") as stream:
                fcntl.flock(stream, fcntl.LOCK_EX)
                self.check()
                yield
        except OSError as error:
            raise HarnessError("无法安全读取平台账号文件") from error

    def read(self):
        self.check()
        if not self.path.exists():
            return {}
        try:
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError
                raw = stream.read(65537)
            if len(raw) > 65536:
                raise ValueError
            value = json.loads(raw)
            if (
                not isinstance(value, dict)
                or value.get("version") != 1
                or not isinstance(value.get("url"), str)
            ):
                raise ValueError
            if "auth" in value:
                auth = value["auth"]
                if not isinstance(auth, dict) or not all(
                    isinstance(auth.get(k), str) and auth[k]
                    for k in ("access_token", "refresh_token", "session_id")
                ):
                    raise ValueError
                if not isinstance(auth.get("account"), dict) or not isinstance(
                    auth["account"].get("id"), str
                ):
                    raise ValueError
                if not all(
                    isinstance(auth.get(k), (int, float))
                    and not isinstance(auth[k], bool)
                    for k in ("expires_at", "refresh_expires_at")
                ):
                    raise ValueError
            return value
        except (OSError, ValueError, TypeError) as error:
            raise HarnessError(
                "平台账号文件损坏，请检查本地文件；不会覆盖原始数据"
            ) from error

    def write(self, data):
        self.check()
        descriptor, name = tempfile.mkstemp(
            dir=self.directory, prefix="platform-session-", suffix=".tmp"
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump(data, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            self.check()
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)
