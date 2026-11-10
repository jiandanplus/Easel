"""Bounded, read-only discovery of private native execution operation records."""

from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import stat
import time
from datetime import datetime
from urllib.parse import urlsplit


def operation_turn_key(session_id: str, turn_id: str) -> str:
    """Private exact-turn association; never infer one for older records."""
    if any(not isinstance(value, str)
           or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}", value)
           for value in (session_id, turn_id)):
        raise ValueError("Invalid operation turn")
    return sha256(json.dumps([session_id, turn_id], ensure_ascii=True,
                             separators=(",", ":")).encode()).hexdigest()


def operation_owner(origin: str, scope: dict) -> str:
    """Business owner only; never use this digest to authorize execution renewal."""
    fields = ("user_id", "product_id", "tenant_id", "billing_account_id")
    if not isinstance(scope, dict) or any(
        not isinstance(scope.get(key), str) or not 1 <= len(scope[key]) <= 160
        or any(ord(char) < 32 for char in scope[key]) for key in fields
    ):
        raise ValueError("Invalid operation owner")
    try:
        parsed = urlsplit(origin)
        if (parsed.scheme not in ("https", "http") or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ("", "/")):
            raise ValueError
        host = parsed.hostname.lower()
        if ":" in host:
            host = "[" + host + "]"
        port = parsed.port
        default = 443 if parsed.scheme == "https" else 80
        canonical = parsed.scheme + "://" + host + (":" + str(port) if port is not None and port != default else "")
    except (TypeError, ValueError, AttributeError):
        raise ValueError("Invalid operation owner") from None
    return sha256(json.dumps([canonical, *[scope[key] for key in fields]],
                             ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


MAX_SCAN = 1000
MAX_RECORD_BYTES = 16384
MAX_RECORDS = 50
SCAN_SECONDS = 0.25
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


def _valid_time(value):
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.tzinfo is not None and math.isfinite(parsed.timestamp())
        except (ValueError, OverflowError, OSError):
            return False
    return type(value) in (int, float) and 0 <= value <= 253402300799 and math.isfinite(value)


def _record(value, owner, filename, turn_key=None):
    if (not isinstance(value, dict) or type(value.get("version")) is not int
            or value["version"] != 2 or value.get("owner_key") != owner
            or (turn_key is not None and value.get("turn_key") != turn_key)
            or not isinstance(value.get("operation_key"), str)
            or not re.fullmatch(_UUID, value["operation_key"])
            or filename != value["operation_key"] + ".json"
            or not isinstance(value.get("invocation_id"), str)
            or not re.fullmatch(_UUID, value["invocation_id"])
            or not isinstance(value.get("body_hash"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", value["body_hash"])
            or not isinstance(value.get("model"), str)
            or not re.fullmatch(r"[\x21-\x7e]{1,240}", value["model"])
            or value.get("status") not in ("sending", "pending", "delivered")
            or not _valid_time(value.get("created_at"))
            or not _valid_time(value.get("updated_at"))):
        return None
    return {"operation_key": value["operation_key"], "invocation_id": value["invocation_id"],
            "model": value["model"], "local_status": value["status"]}


def read_operations(root, owner, turn_key=None):
    """Read at most 50 newest owned matching records without writing metadata.

    All path components are opened relative to an already-open directory with
    NOFOLLOW; a symlink swap cannot redirect reads outside the fixed workspace.
    Scanning, file size, file count and wall time are independently bounded.
    """
    descriptors = []
    deadline = time.monotonic() + SCAN_SECONDS
    truncated = False
    rows = []
    try:
        descriptor = os.open(Path(root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(descriptor)
        for part in ("outputs", "_harness", "central-execution", "operations"):
            descriptor = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            descriptors.append(descriptor)
            info = os.fstat(descriptor)
            if info.st_uid != os.getuid() or (part in ("central-execution", "operations") and stat.S_IMODE(info.st_mode) != 0o700):
                return [], False
        candidates = []
        with os.scandir(descriptor) as entries:
            for index, entry in enumerate(entries):
                if index >= MAX_SCAN or time.monotonic() >= deadline:
                    truncated = True
                    break
                if not re.fullmatch(_UUID + r"\.json", entry.name):
                    continue
                try:
                    info = entry.stat(follow_symlinks=False)
                    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > MAX_RECORD_BYTES):
                        continue
                    candidates.append((info.st_mtime_ns, entry.name))
                except OSError:
                    continue
        for _, name in sorted(candidates, reverse=True):
            if time.monotonic() >= deadline:
                truncated = True
                break
            try:
                handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
                with os.fdopen(handle, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > MAX_RECORD_BYTES):
                        continue
                    raw = stream.read(MAX_RECORD_BYTES + 1)
                if len(raw) > MAX_RECORD_BYTES:
                    continue
                row = _record(json.loads(raw), owner, name, turn_key)
                if row is not None:
                    if len(rows) == MAX_RECORDS:
                        truncated = True
                        break
                    rows.append(row)
            except (OSError, ValueError, TypeError, RecursionError):
                continue
    except OSError:
        return [], False
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
    return rows, truncated
