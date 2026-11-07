"""Public errors carry stable codes, never upstream response content."""
import re


class PlatformError(Exception):
    def __init__(self, status: int, code: str, retryable: bool = False):
        if isinstance(status, bool) or not isinstance(status, int) or not 400 <= status <= 599:
            raise ValueError("invalid error status")
        if not isinstance(code, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,127}", code):
            raise ValueError("invalid error code")
        if not isinstance(retryable, bool):
            raise ValueError("invalid retryable flag")
        self.status = status
        self.code = code
        self.retryable = retryable
        super().__init__(code)
