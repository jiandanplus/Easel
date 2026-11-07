"""Private binary-to-task bridge for New API; no public deployment side effects."""
from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import time
from typing import Callable, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
import httpx
import msgpack
from pydantic import BaseModel, ConfigDict, Field, ValidationError

FISH_TTS_URL = "https://api.fish.audio/v1/tts"
MODELS = frozenset({"s2-pro", "s2.1-pro", "s2.1-pro-free", "drama-3"})
TASK_PATTERN = re.compile(r"^task_[A-Za-z0-9]{32}$")
ARTIFACT_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SECRET_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
MIME_TYPES = {"mp3": "audio/mpeg", "wav": "audio/wav", "pcm": "application/octet-stream", "opus": "audio/opus"}
ACCEPTED_TYPES = {
    "mp3": {"audio/mpeg", "audio/mp3"},
    "wav": {"audio/wav", "audio/x-wav", "audio/wave"},
    "pcm": {"audio/pcm", "application/octet-stream"},
    "opus": {"audio/opus", "audio/ogg", "application/ogg"},
}


@dataclass(frozen=True)
class Config:
    storage_dir: Path
    internal_secrets: tuple[str, ...]
    fish_api_key: str
    allowed_reference_ids: frozenset[str] = frozenset()
    max_concurrency: int = 2
    timeout_seconds: float = 90
    artifact_ttl_seconds: int = 600
    max_audio_bytes: int = 8 * 1024 * 1024
    max_storage_bytes: int = 64 * 1024 * 1024
    max_request_bytes: int = 4 * 1024 * 1024
    max_sample_bytes: int = 1024 * 1024
    max_receipts: int = 10000

    def __post_init__(self):
        if not self.internal_secrets or any(not SECRET_PATTERN.fullmatch(value) for value in self.internal_secrets):
            raise ValueError("configure strong random internal secrets before starting")
        if len(set(self.internal_secrets)) != len(self.internal_secrets):
            raise ValueError("internal secrets must be unique")
        if not self.fish_api_key.strip() or "\n" in self.fish_api_key or "\r" in self.fish_api_key:
            raise ValueError("configure the supplier API key before starting")
        for name in ["max_concurrency", "timeout_seconds", "artifact_ttl_seconds", "max_audio_bytes", "max_storage_bytes", "max_request_bytes", "max_sample_bytes", "max_receipts"]:
            if getattr(self, name) <= 0:
                raise ValueError("bridge limits must be positive")
        if self.max_audio_bytes > self.max_storage_bytes or self.max_concurrency > 16:
            raise ValueError("invalid bounded resource configuration")
        if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) for value in self.allowed_reference_ids):
            raise ValueError("invalid administrator reference ID allowlist")

    @classmethod
    def from_env(cls):
        try:
            values = json.loads(os.environ.get("FISH_BRIDGE_INTERNAL_SECRETS", "[]"))
            allowed = json.loads(os.environ.get("FISH_BRIDGE_ALLOWED_REFERENCE_IDS", "[]"))
            if not isinstance(values, list) or not isinstance(allowed, list):
                raise ValueError("secret and reference configuration must be arrays")
            return cls(
                storage_dir=Path(os.environ.get("FISH_BRIDGE_STORAGE_DIR", "./state")),
                internal_secrets=tuple(values),
                fish_api_key=os.environ.get("FISH_API_KEY", ""),
                allowed_reference_ids=frozenset(allowed),
            )
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid private bridge configuration") from exc


class ReferenceAudio(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    audio: str = Field(min_length=1, max_length=1400000)
    text: str = Field(min_length=1, max_length=2000)


class SpeechRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    model: Literal["s2-pro", "s2.1-pro", "s2.1-pro-free", "drama-3"]
    text: str = Field(min_length=1, max_length=2000)
    format: Literal["mp3", "wav", "pcm", "opus"] = "mp3"
    reference_id: str | None = Field(default=None, min_length=1, max_length=128)
    references: list[ReferenceAudio] | None = Field(default=None, min_length=1, max_length=2)
    speed: float = Field(default=1.0, ge=0.5, le=2)


class Store:
    """Durable no-replay receipts; expired audio leaves an idempotency tombstone."""

    def __init__(self, config: Config, clock: Callable[[], float]):
        self.config, self.clock = config, clock
        root = config.storage_dir
        if root.is_symlink():
            raise ValueError("private store must not be a symlink")
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = root.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError("private store must be owned by this user and have mode 0700")
        self.root = root
        self.lock_fd = os.open(root / "bridge.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self.lock_fd)
            raise ValueError("private store supports one process only") from None
        db_path = root / "receipts.sqlite3"
        if db_path.is_symlink():
            raise ValueError("receipt database must not be a symlink")
        fd = os.open(db_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        if stat.S_IMODE(db_path.stat().st_mode) != 0o600:
            raise ValueError("receipt database must have mode 0600")
        self.db = sqlite3.connect(db_path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS receipts (owner TEXT NOT NULL, task_id TEXT NOT NULL, input_hash TEXT NOT NULL, state TEXT NOT NULL, metadata TEXT NOT NULL, artifact_id TEXT UNIQUE, expires_at REAL, size INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(owner, task_id))")
        self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS receipt_public_task ON receipts(task_id)")
        self.cleanup()
        retained = {row[0] for row in self.db.execute("SELECT artifact_id FROM receipts WHERE state='completed'")}
        for path in root.iterdir():
            if re.fullmatch(r"[0-9a-f]{64}\.(part|audio)", path.name) and (path.suffix == ".part" or path.stem not in retained):
                path.unlink(missing_ok=True)

    def close(self):
        self.db.close()
        os.close(self.lock_fd)

    def cleanup(self):
        rows = self.db.execute("SELECT owner, task_id, metadata, artifact_id FROM receipts WHERE state='completed' AND expires_at<=?", (self.clock(),)).fetchall()
        for row in rows:
            artifact_id = row["artifact_id"]
            if artifact_id and ARTIFACT_PATTERN.fullmatch(artifact_id):
                (self.root / (artifact_id + ".audio")).unlink(missing_ok=True)
            metadata = json.loads(row["metadata"])
            metadata["state"] = "expired"
            self.db.execute("UPDATE receipts SET state='expired', metadata=?, size=0 WHERE owner=? AND task_id=?", (json.dumps(metadata), row["owner"], row["task_id"]))

    def lookup(self, owner, task_id):
        self.cleanup()
        return self.db.execute("SELECT * FROM receipts WHERE owner=? AND task_id=?", (owner, task_id)).fetchone()

    def begin(self, owner, task_id, input_hash):
        self.cleanup()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            current = self.db.execute("SELECT * FROM receipts WHERE task_id=?", (task_id,)).fetchone()
            if current:
                if current["owner"] != owner:
                    raise HTTPException(409, "idempotency_owner_conflict")
                if current["input_hash"] != input_hash:
                    raise HTTPException(409, "idempotency_input_conflict")
                self.db.execute("COMMIT")
                return current
            if self.db.execute("SELECT count(*) FROM receipts").fetchone()[0] >= self.config.max_receipts:
                raise HTTPException(503, "receipt_capacity_reached")
            metadata = json.dumps({"state": "pending", "operation_id": task_id})
            self.db.execute("INSERT INTO receipts(owner,task_id,input_hash,state,metadata) VALUES(?,?,?,'pending',?)", (owner, task_id, input_hash, metadata))
            self.db.execute("COMMIT")
            return None
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def complete(self, owner, task_id, metadata):
        self.db.execute("UPDATE receipts SET state='completed', metadata=?, artifact_id=?, expires_at=?, size=? WHERE owner=? AND task_id=? AND state='pending'", (json.dumps(metadata), metadata["artifact_id"], metadata["expires_at"], metadata["size"], owner, task_id))

    def used_bytes(self):
        self.cleanup()
        return self.db.execute("SELECT coalesce(sum(size),0) FROM receipts").fetchone()[0]

    def artifact(self, owner, artifact_id):
        self.cleanup()
        return self.db.execute("SELECT * FROM receipts WHERE owner=? AND artifact_id=? AND state='completed'", (owner, artifact_id)).fetchone()


def create_app(config: Config | None = None, *, transport=None, clock=time.time):
    settings = config or Config.from_env()
    store = Store(settings, clock)
    active = 0
    reserved = 0
    locks = {}

    @asynccontextmanager
    async def lifespan(app):
        timeout = httpx.Timeout(settings.timeout_seconds, connect=10)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False, transport=transport) as client:
            app.state.supplier = client
            try:
                yield
            finally:
                store.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    def authenticate(request):
        header = request.headers.get("Authorization", "")
        supplied = header[7:] if header.startswith("Bearer ") else ""
        matched = None
        for secret in settings.internal_secrets:
            if hmac.compare_digest(supplied.encode(), secret.encode()):
                matched = secret
        if matched is None:
            raise HTTPException(401, "internal_auth_required")
        return hashlib.sha256(matched.encode()).hexdigest()

    def task_id_from_header(request):
        task_id = request.headers.get("Idempotency-Key", "")
        if not TASK_PATTERN.fullmatch(task_id):
            raise HTTPException(422, "public_task_id_required")
        return task_id

    async def decode(request):
        if request.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            raise HTTPException(415, "json_required")
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > settings.max_request_bytes:
                raise HTTPException(413, "request_too_large")
        try:
            value = SpeechRequest.model_validate(json.loads(raw))
        except (ValueError, ValidationError, TypeError):
            raise HTTPException(422, "invalid_speech_request") from None
        if not value.text.strip() or bool(value.reference_id) == bool(value.references):
            raise HTTPException(422, "choose_one_reference_source")
        if value.reference_id and value.reference_id not in settings.allowed_reference_ids:
            raise HTTPException(403, "reference_not_allowed")
        payload = {"text": value.text, "format": value.format, "prosody": {"speed": value.speed}}
        if value.reference_id:
            payload["reference_id"] = value.reference_id
        else:
            references = []
            for item in value.references:
                try:
                    sample = base64.b64decode(item.audio, validate=True)
                except ValueError:
                    raise HTTPException(422, "invalid_reference_audio") from None
                if not sample or len(sample) > settings.max_sample_bytes:
                    raise HTTPException(422, "reference_audio_too_large")
                references.append({"audio": sample, "text": item.text})
            payload["references"] = references
        canonical = json.dumps(value.model_dump(exclude_none=True), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return value, payload, hashlib.sha256(canonical.encode()).hexdigest()

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        return JSONResponse({"error": str(error.detail)}, status_code=error.status_code, headers={"Cache-Control": "no-store"})

    @app.get("/healthz")
    async def health():
        return {"ok": True}

    @app.post("/v1/tts")
    async def synthesize(request: Request):
        nonlocal active, reserved
        owner = authenticate(request)
        task_id = task_id_from_header(request)
        if active >= settings.max_concurrency:
            raise HTTPException(429, "bridge_busy")
        active += 1
        lock_key = owner + ":" + task_id
        entry = locks.setdefault(lock_key, {"lock": asyncio.Lock(), "users": 0})
        entry["users"] += 1
        try:
            value, payload, input_hash = await decode(request)
            async with entry["lock"]:
                existing = store.lookup(owner, task_id)
                if not existing and store.used_bytes() + reserved + settings.max_audio_bytes > settings.max_storage_bytes:
                    raise HTTPException(503, "audio_capacity_reached")
                existing = store.begin(owner, task_id, input_hash)
                if existing:
                    metadata = json.loads(existing["metadata"])
                    return JSONResponse(metadata, status_code=202 if metadata["state"] == "pending" else 200, headers={"Cache-Control": "no-store"})
                reserved += settings.max_audio_bytes
                opaque_id = secrets.token_hex(32)
                temporary = store.root / (opaque_id + ".part")
                destination = store.root / (opaque_id + ".audio")
                file = None
                completed = False
                try:
                    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                    file = os.fdopen(fd, "wb")
                    packed = bool(value.references)
                    upstream_headers = {"Authorization": "Bearer " + settings.fish_api_key, "model": value.model, "Content-Type": "application/msgpack" if packed else "application/json"}
                    content = msgpack.packb(payload, use_bin_type=True) if packed else json.dumps(payload).encode()
                    size = 0
                    async with asyncio.timeout(settings.timeout_seconds):
                        async with app.state.supplier.stream("POST", FISH_TTS_URL, headers=upstream_headers, content=content) as response:
                            media_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
                            if response.status_code != 200 or media_type not in ACCEPTED_TYPES[value.format]:
                                raise ValueError("supplier_response_unconfirmed")
                            async for chunk in response.aiter_bytes():
                                size += len(chunk)
                                if size > settings.max_audio_bytes:
                                    raise ValueError("supplier_audio_exceeds_limit")
                                file.write(chunk)
                    if not size:
                        raise ValueError("empty_supplier_audio")
                    file.flush()
                    os.fsync(file.fileno())
                    file.close()
                    file = None
                    os.replace(temporary, destination)
                    directory = os.open(store.root, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
                    metadata = {"state": "completed", "operation_id": task_id, "artifact_id": opaque_id, "mime_type": MIME_TYPES[value.format], "format": value.format, "size": size, "expires_at": int(clock() + settings.artifact_ttl_seconds), "request_count": 1, "input_characters": len(value.text)}
                    store.complete(owner, task_id, metadata)
                    completed = True
                    return JSONResponse(metadata, headers={"Cache-Control": "no-store"})
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # The provider may have consumed quota. Keep the durable
                    # pending receipt and never issue this POST again.
                    return JSONResponse({"state": "pending", "operation_id": task_id}, status_code=202, headers={"Cache-Control": "no-store"})
                finally:
                    if file is not None:
                        file.close()
                    temporary.unlink(missing_ok=True)
                    if not completed:
                        destination.unlink(missing_ok=True)
                    reserved -= settings.max_audio_bytes
        finally:
            active -= 1
            entry["users"] -= 1
            if entry["users"] == 0:
                locks.pop(lock_key, None)

    @app.get("/v1/operations/{task_id}")
    async def operation(task_id: str, request: Request):
        owner = authenticate(request)
        if not TASK_PATTERN.fullmatch(task_id):
            raise HTTPException(404, "operation_not_found")
        receipt = store.lookup(owner, task_id)
        if receipt is None:
            raise HTTPException(404, "operation_not_found")
        return JSONResponse(json.loads(receipt["metadata"]), headers={"Cache-Control": "no-store"})

    @app.api_route("/v1/artifacts/{artifact_id}", methods=["GET", "HEAD"])
    async def artifact(artifact_id: str, request: Request):
        owner = authenticate(request)
        if not ARTIFACT_PATTERN.fullmatch(artifact_id):
            raise HTTPException(404, "artifact_not_found")
        receipt = store.artifact(owner, artifact_id)
        if receipt is None:
            raise HTTPException(404, "artifact_not_found")
        path = store.root / (artifact_id + ".audio")
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            file = os.fdopen(fd, "rb")
            info = os.fstat(file.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size != receipt["size"]:
                file.close()
                raise HTTPException(404, "artifact_not_found")
        except OSError:
            raise HTTPException(404, "artifact_not_found") from None
        size, start, end = info.st_size, 0, info.st_size - 1
        status_code = 200
        requested_range = request.headers.get("Range")
        if requested_range:
            match = re.fullmatch(r"bytes=([0-9]*)-([0-9]*)", requested_range)
            if not match or len(requested_range) > 128 or (not match[1] and not match[2]):
                file.close()
                return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
            if match[1]:
                start = int(match[1])
                end = min(int(match[2]), end) if match[2] else end
            else:
                start = max(0, size - int(match[2]))
            if start > end or start >= size:
                file.close()
                return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
            status_code = 206
        metadata = json.loads(receipt["metadata"])
        response_headers = {"Content-Length": str(end - start + 1), "Accept-Ranges": "bytes", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Content-Disposition": 'attachment; filename="audio.' + metadata["format"] + '"'}
        if status_code == 206:
            response_headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        if request.method == "HEAD":
            file.close()
            return Response(status_code=status_code, media_type=metadata["mime_type"], headers=response_headers)

        def chunks():
            remaining = end - start + 1
            try:
                file.seek(start)
                while remaining:
                    chunk = file.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk
            finally:
                file.close()

        return StreamingResponse(chunks(), status_code=status_code, media_type=metadata["mime_type"], headers=response_headers)

    return app
