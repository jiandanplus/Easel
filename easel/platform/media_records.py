"""Owner-only exact-turn media records. No prompts, credentials, or raw URLs."""
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid

try:
    import fcntl
except ImportError:
    fcntl = None

from .execution_records import operation_turn_key
from .media_contracts import CAPABILITIES

MAX_RECORD = 16384
MAX_RECORDS = 1000
MAX_SCAN = 4096
MAX_ROWS = 50
_HEX = re.compile(r"[a-f0-9]{64}")
_UUID = re.compile(r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}")
_STATES = frozenset({"prepared", "mint_sending", "mint_unknown", "minted", "upload_sending", "upload_unknown", "uploaded", "job_sending", "unknown", "known", "downloaded"})


def _filename(value):
    suffix = "" if value["capability"].startswith("image.") else "-" + value["capability"]
    return "turn-" + value["turn_key"] + suffix + ".json"


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class MediaRecordError(ValueError):
    pass


def _validate(value):
    keys = {"version", "owner_key", "turn_key", "tool_key", "invocation_key", "operation_key", "body_hash",
            "scope_hash", "model", "capability", "local_status", "invocation_id", "job_id", "created_at", "updated_at"}
    if (not isinstance(value, dict) or set(value) - (keys | {"path"}) or not keys.issubset(value)
            or type(value["version"]) is not int or value["version"] != 1
            or any(not isinstance(value[k], str) or not _HEX.fullmatch(value[k])
                   for k in ("owner_key", "turn_key", "tool_key", "body_hash", "scope_hash"))
            or not isinstance(value["operation_key"], str) or not _UUID.fullmatch(value["operation_key"])
            or value["invocation_key"] != "media-" + value["operation_key"]
            or not isinstance(value["model"], str) or not re.fullmatch(r"[\x21-\x7e]{1,240}", value["model"])
            or value["capability"] not in CAPABILITIES or value["local_status"] not in _STATES
            or any(value[k] is not None and (not isinstance(value[k], str) or not _UUID.fullmatch(value[k]))
                   for k in ("invocation_id", "job_id"))
            or any(type(value[k]) not in (int, float) or not 0 <= value[k] <= 253402300799
                   for k in ("created_at", "updated_at"))
            or ("path" in value and (not isinstance(value["path"], str) or len(value["path"]) > 4096))):
        raise MediaRecordError("Invalid media record")
    return value


class MediaRecords:
    def __init__(self, root, *, clock=time.time):
        self.root, self.clock = Path(root).resolve(), clock

    @contextmanager
    def directory(self, *, create=False):
        descriptors = []
        try:
            fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            descriptors.append(fd)
            for part in ("outputs", "_harness", "central-media", "operations"):
                if create:
                    try: os.mkdir(part, 0o755 if part == "outputs" else 0o700, dir_fd=fd)
                    except FileExistsError: pass
                fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                descriptors.append(fd)
                info = os.fstat(fd)
                if info.st_uid != os.getuid() or (part != "outputs" and stat.S_IMODE(info.st_mode) != 0o700):
                    raise MediaRecordError("Invalid media record directory")
            yield fd
        finally:
            for fd in reversed(descriptors): os.close(fd)

    @contextmanager
    def locked(self):
        if fcntl is None: raise MediaRecordError("Media file locking unavailable")
        with self.directory(create=True) as fd:
            lock = os.open(".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=fd)
            try:
                info = os.fstat(lock)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                        or stat.S_IMODE(info.st_mode) != 0o600):
                    raise MediaRecordError("Invalid media lock")
                fcntl.flock(lock, fcntl.LOCK_EX)
                yield fd
            finally:
                os.close(lock)

    def _read(self, fd, name):
        try:
            source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        except FileNotFoundError:
            return None
        with os.fdopen(source, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > MAX_RECORD):
                raise MediaRecordError("Invalid media record")
            raw = stream.read(MAX_RECORD + 1)
        if len(raw) > MAX_RECORD: raise MediaRecordError("Invalid media record")
        try:
            value = json.loads(raw)
            legacy = isinstance(value, dict) and "capability" not in value
            if legacy: value = {**value, "capability": "image.generate"}
            value = _validate(value)
        except (ValueError, TypeError, RecursionError): raise MediaRecordError("Invalid media record") from None
        if name != _filename(value): raise MediaRecordError("Invalid media record")
        if legacy: value["_legacy_without_capability"] = True
        return value

    def _write(self, fd, value, *, first=False):
        raw = json.dumps(_validate(value), sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
        if len(raw) > MAX_RECORD: raise MediaRecordError("Invalid media record")
        final = _filename(value)
        temporary = "." + str(uuid.uuid4()) + ".tmp"
        name = final if first else temporary
        target = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        try:
            with os.fdopen(target, "wb") as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            if not first: os.replace(temporary, final, src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            if not first:
                try: os.unlink(temporary, dir_fd=fd)
                except FileNotFoundError: pass

    def claim(self, *, owner, scope, session_id, turn_id, tool_id, model, body_hash, capability="image.generate"):
        if capability not in CAPABILITIES: raise MediaRecordError("Invalid media capability")
        if not isinstance(tool_id, str) or not 1 <= len(tool_id) <= 240 or any(ord(c) < 32 for c in tool_id):
            raise MediaRecordError("Invalid tool identity")
        turn_key = operation_turn_key(session_id, turn_id)
        tool_key = digest(tool_id)
        identity = [owner, turn_key, tool_key] + ([] if capability.startswith("image.") else [capability])
        key = str(uuid.uuid5(uuid.NAMESPACE_URL, digest(identity)))
        value = dict(version=1, owner_key=owner, turn_key=turn_key, tool_key=tool_key,
                     invocation_key="media-" + key, operation_key=key, body_hash=body_hash, scope_hash=digest(scope),
                     model=model, capability=capability, local_status="prepared", invocation_id=None, job_id=None,
                     created_at=self.clock(), updated_at=self.clock())
        with self.locked() as fd:
            previous = self._read(fd, _filename(value))
            if previous:
                if any(previous[k] != value[k] for k in ("owner_key", "operation_key", "body_hash", "scope_hash", "model", "capability")):
                    raise MediaRecordError("This turn already has a media operation")
                return previous, False
            # Keep every admitted paid operation inside the complete lookup
            # bound. Pagination only controls display, never ownership proof.
            if len(self._names(fd)) >= MAX_RECORDS:
                raise MediaRecordError("Media record capacity reached")
            self._write(fd, value, first=True)
            return value, True

    def update(self, record, **changes):
        if set(changes) - {"local_status", "invocation_id", "job_id", "path"}: raise MediaRecordError("Invalid update")
        with self.locked() as fd:
            previous = self._read(fd, _filename(record))
            if not previous or any(previous[k] != record[k] for k in ("owner_key", "operation_key", "body_hash", "scope_hash")):
                raise MediaRecordError("Media record changed")
            for name in ("invocation_id", "job_id"):
                if previous[name] is not None and name in changes and previous[name] != changes[name]:
                    raise MediaRecordError("Media association changed")
            # Old records without a capability are read-only compatibility data.
            if previous.get("_legacy_without_capability"): return previous
            value = {**previous, **changes, "updated_at": self.clock()}
            self._write(fd, value)
            return value

    def _names(self, fd):
        names = []
        deadline = time.monotonic() + 2
        with os.scandir(fd) as entries:
            for index, entry in enumerate(entries):
                if index >= MAX_SCAN or time.monotonic() >= deadline:
                    raise MediaRecordError("Media record lookup incomplete")
                if re.fullmatch(r"turn-[a-f0-9]{64}(?:-[a-z]+\.[a-z]+)?\.json", entry.name):
                    names.append(entry.name)
        return names

    def rows(self, owner, turn_key=None):
        rows, truncated = [], False
        deadline = time.monotonic() + 2
        try:
            with self.directory() as fd:
                if turn_key is not None:
                    if not isinstance(turn_key, str) or not _HEX.fullmatch(turn_key):
                        raise MediaRecordError("Invalid media turn")
                    values = [self._read(fd, _filename({"turn_key":turn_key,"capability":cap}))
                              for cap in ["image.generate", *sorted(CAPABILITIES - {"image.generate","image.edit","image.variation"})]]
                    return [v for v in values if v and v["owner_key"] == owner], False
                candidates = self._names(fd)
                for name in candidates:
                    if time.monotonic() >= deadline:
                        truncated = True; break
                    try: value = self._read(fd, name)
                    except (OSError, MediaRecordError): continue
                    if value and value["owner_key"] == owner and (turn_key is None or value["turn_key"] == turn_key):
                        rows.append(value)
                rows.sort(key=lambda row: row["updated_at"], reverse=True)
                if len(rows) > MAX_ROWS: truncated = True
        except FileNotFoundError:
            pass
        return rows[:MAX_ROWS], truncated

    def owned(self, owner, *, operation_key=None, job_id=None):
        if ((operation_key is None) == (job_id is None)
                or not isinstance(operation_key or job_id, str) or not _UUID.fullmatch(operation_key or job_id)):
            raise MediaRecordError("Invalid media lookup")
        matches = []
        deadline = time.monotonic() + 2
        try:
            with self.directory() as fd:
                for name in self._names(fd):
                    if time.monotonic() >= deadline:
                        raise MediaRecordError("Media record lookup incomplete")
                    row = self._read(fd, name)
                    if row and row["owner_key"] == owner and (
                            (operation_key is not None and row["operation_key"] == operation_key)
                            or (job_id is not None and row["job_id"] == job_id)):
                        matches.append(row)
        except FileNotFoundError:
            pass
        if len(matches) != 1: raise MediaRecordError("Owned media operation not found")
        return matches[0]
