"""Private durable acceptance receipts and opaque desktop workspace scopes.

Receipts prove acceptance, never successful inference, delivery or settlement.
An absent receipt is unknown, not permission to repeat an uncertain submission.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import time


class WorkbenchError(Exception):
    def __init__(self, message="任务记录暂不可用", status=503):
        super().__init__(message)
        self.status = status


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}", value, re.ASCII):
        raise WorkbenchError("无效的任务标识", 400)
    return value


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def _sync_directory(path):
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class WorkbenchState:
    def __init__(self, sessions, workspace):
        self.workspace = Path(workspace).resolve()
        self.sessions = Path(sessions)
        self.directory = self.sessions / "workbench"

    def _directory(self, path):
        try:
            if not path.absolute().is_relative_to(self.workspace):
                raise WorkbenchError()
            relative = path.absolute().relative_to(self.workspace)
            current = self.workspace
            for name in relative.parts:
                current = current / name
                if current.is_symlink():
                    raise WorkbenchError()
                existed = current.is_dir()
                current.mkdir(mode=0o700, exist_ok=True)
                if not current.is_dir():
                    raise WorkbenchError()
                if not existed:
                    _sync_directory(current.parent)
            return path
        except OSError:
            raise WorkbenchError() from None

    def _read(self, path, limit=65536):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return None
        except OSError:
            raise WorkbenchError() from None
        try:
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                    raise WorkbenchError()
                value = stream.read(limit + 1)
                if len(value) > limit:
                    raise WorkbenchError()
                return value
        except OSError:
            raise WorkbenchError() from None

    def _key(self):
        self._directory(self.directory)
        path = self.directory / "scope.key"
        key = self._read(path, 64)
        if key is None:
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(secrets.token_hex(32).encode("ascii"))
                    stream.flush()
                    os.fsync(stream.fileno())
                _sync_directory(path.parent)
            except FileExistsError:
                pass
            except OSError:
                raise WorkbenchError() from None
            key = self._read(path, 64)
        if key is None or not re.fullmatch(b"[0-9a-f]{64}", key):
            raise WorkbenchError()
        return key

    def contract(self, source, build):
        key = self._key()
        token = lambda value: hmac.new(key, value.encode(), hashlib.sha256).hexdigest()
        return {"version": 1, "build_id": build if re.fullmatch(r"[0-9a-f]{64}", build) else digest(build),
                "workspace_scope": token("workspace\0" + str(self.workspace)),
                "source_scope": token("source\0" + digest(source))}

    def reference_id(self, path, scope):
        return hmac.new(self._key(), ("output\0" + scope + "\0" + path).encode(), hashlib.sha256).hexdigest()

    def _path(self, turn, kind="receipts", *, create=True):
        identifier(turn)
        directory = self.directory / kind
        if create:
            self._directory(directory)
        else:
            if not directory.absolute().is_relative_to(self.workspace):
                raise WorkbenchError()
            current = self.workspace
            for name in directory.absolute().relative_to(self.workspace).parts:
                current = current / name
                if current.is_symlink():
                    raise WorkbenchError()
        return directory / (hashlib.sha256(turn.encode()).hexdigest() + ".json")

    def receipt(self, turn):
        identifier(turn)
        candidate = self.directory / "receipts" / (hashlib.sha256(turn.encode()).hexdigest() + ".json")
        if not candidate.exists() and not candidate.is_symlink():
            return None
        raw = self._read(self._path(turn, create=False), 8192)
        if raw is None:
            return None
        try:
            value = json.loads(raw)
            if (not isinstance(value, dict) or value.get("version") != 1
                    or identifier(value.get("turn_id")) != turn
                    or not all(isinstance(value.get(field), str) for field in
                               ("session_id", "request_hash", "source_scope", "receipt_id"))):
                raise ValueError
            identifier(value["session_id"])
            for field in ("request_hash", "source_scope", "receipt_id"):
                if not re.fullmatch(r"[0-9a-f]{64}", value[field]):
                    raise ValueError
            if value.get("decision", "accepted") not in ("accepted", "rejected"):
                raise ValueError
            value.setdefault("decision", "accepted")
            return value
        except (ValueError, TypeError):
            raise WorkbenchError() from None

    def owned(self, session, turn, scope):
        identifier(session)
        record = self.receipt(turn)
        if record is not None and (record["session_id"] != session
                or not hmac.compare_digest(record["source_scope"], scope)):
            raise WorkbenchError("任务所属账号或服务已变化", 409)
        return record

    def _decide(self, session, turn, scope, request_hash, decision):
        identifier(session); identifier(turn)
        if (decision not in ("accepted", "rejected")
                or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                       for value in (scope, request_hash))):
            raise WorkbenchError("无效的任务摘要", 400)
        existing = self.owned(session, turn, scope)
        if existing is not None:
            if not hmac.compare_digest(existing["request_hash"], request_hash):
                raise WorkbenchError("任务标识已用于其他内容", 409)
            return existing, False
        value = {"version": 1, "session_id": session, "turn_id": turn, "source_scope": scope,
                 "request_hash": request_hash, "receipt_id": secrets.token_hex(32),
                 "decision": decision, "decided_at": int(time.time())}
        if decision == "accepted":
            value["accepted_at"] = value["decided_at"]
        path = self._path(turn)
        temporary = None
        try:
            fd, name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
            temporary = Path(name)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump(value, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            # Publish a complete immutable inode only if the decision slot is
            # absent. Both acceptance and rejection race on this same slot.
            os.link(temporary, path, follow_symlinks=False)
            _sync_directory(path.parent)
        except FileExistsError:
            # A concurrent claimant won. Never let the losing caller dispatch.
            existing = self.owned(session, turn, scope)
            if existing is None:
                raise WorkbenchError()
            if not hmac.compare_digest(existing["request_hash"], request_hash):
                raise WorkbenchError("任务标识已用于其他内容", 409)
            return existing, False
        except OSError:
            raise WorkbenchError() from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return value, True

    def claim(self, session, turn, scope, request_hash):
        return self._decide(session, turn, scope, request_hash, "accepted")

    def reject(self, session, turn, scope, request_hash):
        record, _ = self._decide(session, turn, scope, request_hash, "rejected")
        return self.public_rejection(record) if record["decision"] == "rejected" else None

    @staticmethod
    def public_rejection(record):
        return {**{field: record[field] for field in ("version", "session_id", "turn_id", "request_hash")},
                "not_dispatched": True}

    @staticmethod
    def public_receipt(record):
        return {field: record[field] for field in ("version", "session_id", "turn_id", "request_hash", "receipt_id")}

    def save_snapshot(self, session, turn, payload):
        record = self.receipt(turn)
        if record is None or record["session_id"] != session or record["decision"] != "accepted":
            return
        path = self._path(turn, "snapshots")
        temporary = None
        try:
            fd, name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
            temporary = Path(name)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump(payload, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            _sync_directory(path.parent)
        except OSError:
            raise WorkbenchError() from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def snapshot(self, turn):
        raw = self._read(self._path(turn, "snapshots", create=False), 16 * 1024 * 1024)
        if raw is None:
            return None
        try:
            value = json.loads(raw)
            if not isinstance(value, dict) or value.get("turn_id") != turn:
                raise ValueError
            return value
        except (ValueError, TypeError):
            raise WorkbenchError() from None
