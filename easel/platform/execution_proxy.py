"""Owned per-run loopback Responses endpoint; central bearer stays in this process.

Each accepted HTTP POST is a new operation, even for identical input. Durable
records contain hashes and status only, never prompts or credentials. There are
no automatic delivery retries: an uncertain operation remains recoverable by its
recorded key at the central service, which owns authoritative reconciliation.
"""
from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
import secrets
import time
import uuid

import httpx

from easel.harnesses.base import HarnessError

MAX_BODY = 1024 * 1024
MAX_RESULT = 8 * 1024 * 1024
MAX_EVENT = 256 * 1024
MAX_CENTRAL_ERROR = 8192
SAFE_CENTRAL_FAILURES = frozenset({
    "concurrency_limit", "insufficient_credits", "membership_required",
    "pending_reconciliation", "pending_reconciliation_limit", "execution_entitlement_required", "execution_running", "execution_waived",
})
STREAM_ERROR = b'event: error\ndata: {"type":"error","error":{"code":"central_stream_failed"}}\n\n'


def _json_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate field")
        value[key] = item
    return value


class _SSEParser:
    """Keep at most one complete event; yield before parsing the next event."""

    def __init__(self):
        self.buffer = bytearray()
        self.line_start = 0

    def feed(self, chunk):
        start = 0
        while start < len(chunk):
            end = chunk.find(b"\n", start)
            end = end + 1 if end >= 0 else len(chunk)
            if len(self.buffer) + end - start > MAX_EVENT:
                raise ValueError("event too large")
            self.buffer.extend(chunk[start:end])
            start = end
            if not self.buffer.endswith(b"\n"):
                continue
            blank = self.buffer[self.line_start:] in (b"\n", b"\r\n")
            self.line_start = len(self.buffer)
            if blank:
                raw = bytes(self.buffer)
                self.buffer.clear()
                self.line_start = 0
                lines = raw.decode("utf-8").splitlines()
                names = [line[6:].strip() for line in lines if line.startswith("event:")]
                data = "\n".join(line[5:].removeprefix(" ") for line in lines if line.startswith("data:"))
                if len(names) > 1:
                    raise ValueError("duplicate event name")
                name = names[0] if names else None
                value = None
                if data:
                    value = json.loads(data, object_pairs_hook=_json_object,
                                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                    if not isinstance(value, dict) or not isinstance(value.get("type"), str) or name and name not in ("message", value["type"]):
                        raise ValueError("invalid event")
                elif name and name != "message":
                    raise ValueError("missing data")
                yield raw, value


class ExecutionProxy:
    def __init__(self, directory: Path, origin: str, task_token: str, model: str,
                 invocation_id: str, *, transport=None, credential=None, renew=None, clock=time.time,
                 total_timeout=315.0, read_timeout=310.0, drain_timeout=10.0,
                 session_key="", turn_id=""):
        self._turn_key = None
        if session_key != "" or turn_id != "":
            from .execution_records import operation_turn_key
            self._turn_key = operation_turn_key(session_key, turn_id)
        self.directory = Path(directory) / "operations"
        self.origin, self._task_token = origin, task_token
        self.model, self.invocation_id = model, invocation_id
        self.local_token = secrets.token_urlsafe(48)
        self.transport = transport
        self.tasks = set()
        self.lock = asyncio.Lock()
        self.accepted = 0
        self._renew, self._clock = renew, clock
        self.total_timeout, self.read_timeout, self.drain_timeout = total_timeout, read_timeout, drain_timeout
        self.safe_failure_code = None
        self._record_metadata = {}
        self._scope = dict(credential["scope"]) if credential is not None else None
        self._funding_mode = credential["funding_mode"] if credential is not None else None
        self._owner_key = None
        if self._scope:
            from .execution_records import operation_owner
            self._owner_key = operation_owner(origin, self._scope)
        self._expires_at = None
        if credential is not None:
            self._accept_credential(credential)

    def _accept_credential(self, value):
        from .central import timestamp
        expires_at = timestamp(value.get("expires_at"))
        if (value.get("invocation_id") != self.invocation_id or value.get("model") != self.model
                or value.get("scope") != self._scope or value.get("funding_mode") != self._funding_mode
                or not isinstance(value.get("task_token"), str) or not 16 <= len(value["task_token"]) <= 16384
                or expires_at <= self._clock()):
            raise HarnessError("中央执行凭证续期无效")
        self._task_token, self._expires_at = value["task_token"], expires_at

    async def _renew_before_operation(self):
        # Only before a new operation: never retry an already sent POST.
        if self._expires_at is not None and self._expires_at <= self._clock() + 30:
            if self._renew is None:
                raise HarnessError("中央执行凭证已失效")
            self._accept_credential(await self._renew())

    async def __aenter__(self):
        descriptors = []
        try:
            # Only these two owned directories are private; workspace ancestors
            # retain their existing permissions. Never chmod through a symlink.
            self.directory.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            base = os.open(self.directory.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            descriptors.append(base)
            if os.fstat(base).st_uid != os.getuid():
                raise HarnessError("中央操作私有目录无效")
            try:
                os.mkdir("operations", 0o700, dir_fd=base)
            except FileExistsError:
                pass
            operations = os.open("operations", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=base)
            descriptors.append(operations)
            if os.fstat(operations).st_uid != os.getuid():
                raise HarnessError("中央操作私有目录无效")
            for descriptor in descriptors:
                os.fchmod(descriptor, 0o700)
        except OSError:
            raise HarnessError("中央操作私有目录无效") from None
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)
        # Central execution is bounded at 300s. Leave enough room for its
        # terminal receipt/error to arrive before this proxy closes the stream.
        self.client = httpx.AsyncClient(transport=self.transport, trust_env=False,
                                       follow_redirects=False, timeout=httpx.Timeout(120, connect=10, read=self.read_timeout))
        try:
            self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0, limit=16384)
        except BaseException:
            await self.client.aclose()
            raise
        self.authority = "127.0.0.1:" + str(self.server.sockets[0].getsockname()[1])
        self.base_url = "http://" + self.authority
        return self

    def _record(self, key, body_hash, status):
        value = {"operation_key": key, "body_hash": body_hash,
                 "invocation_id": self.invocation_id, "status": status}
        if self._owner_key is not None:
            now = datetime.fromtimestamp(self._clock(), timezone.utc).isoformat()
            metadata = self._record_metadata.setdefault(key, {"created_at": now})
            value.update(version=2, owner_key=self._owner_key,
                         model=self.model, updated_at=now, **metadata)
            if self._turn_key is not None:
                value["turn_key"] = self._turn_key
        # Exclusive temporary file, fsync content + directory before any send.
        path = self.directory / (key + ".json")
        temporary = self.directory / (key + ".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w") as stream:
                json.dump(value, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)

    def _validate(self, body):
        value = json.loads(body)
        if not isinstance(value, dict) or value.get("model") != self.model:
            raise ValueError
        if any(key in value for key in ("previous_response_id", "conversation", "background", "base_url", "api_key")):
            raise ValueError
        def inspect(item):
            if isinstance(item, list):
                for child in item:
                    inspect(child)
            elif isinstance(item, dict):
                kind = item.get("type", "")
                if isinstance(kind, str) and any(marker in kind.lower() for marker in ("image", "audio", "video", "file", "web_search", "computer")):
                    raise ValueError
                for child in item.values():
                    inspect(child)
        inspect(value)
        for tool in value.get("tools", []):
            if not isinstance(tool, dict) or tool.get("type") not in ("function", "custom", "namespace"):
                raise ValueError
        return value

    async def _reply(self, writer, status, content, content_type="application/json", *, state=None):
        if state is not None:
            state["committed"] = True
        writer.write((f"HTTP/1.1 {status} Response\r\nContent-Type: {content_type}\r\n"
                      f"Content-Length: {len(content)}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n").encode() + content)
        await self._drain(writer)

    async def _drain(self, writer):
        await asyncio.wait_for(writer.drain(), self.drain_timeout)

    async def _chunk(self, writer, data):
        writer.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
        await self._drain(writer)

    async def _remember_failure(self, response, content_type):
        if (not 400 <= response.status_code <= 599 or content_type != "application/json"
                or response.headers.get("content-encoding", "identity").strip().lower() != "identity"):
            return
        raw = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=1024):
            # At the limit there is no room to prove EOF without reading more.
            # Fail closed rather than trusting a possibly truncated JSON prefix.
            if len(raw) + len(chunk) >= MAX_CENTRAL_ERROR:
                return
            raw.extend(chunk)
        try:
            value = json.loads(bytes(raw), object_pairs_hook=_json_object,
                               parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            error = value.get("error") if isinstance(value, dict) else None
            code = error.get("code") if isinstance(error, dict) else None
            if isinstance(code, str) and code in SAFE_CENTRAL_FAILURES:
                self.safe_failure_code = code
        except (ValueError, TypeError, UnicodeError, RecursionError):
            pass

    async def _forward(self, writer, key, digest, body, state):
        self.safe_failure_code = None
        async with self.client.stream("POST", self.origin + "/v2/responses", content=body,
                                      headers={"Authorization": "Bearer " + self._task_token,
                                               "Idempotency-Key": key, "Content-Type": "application/json",
                                               "Accept-Encoding": "identity"}) as response:
            content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
            if response.status_code != 200:
                await self._remember_failure(response, content_type)
                raise ValueError("invalid central response")
            if (content_type not in ("application/json", "text/event-stream")
                    or response.headers.get("content-encoding", "identity").strip().lower() != "identity"):
                raise ValueError("invalid central response")
            execution_id = response.headers.get("x-execution-id", "")
            if self._owner_key is not None and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", execution_id):
                self._record_metadata.setdefault(key, {})["execution_id"] = execution_id
                self._record(key, digest, "sending")
            # HTTPX removes upstream transfer framing. Never forward hop-by-hop headers.
            stream = response.aiter_bytes().__aiter__()
            total = 0
            result = bytearray() if content_type == "application/json" else None
            parser = _SSEParser()
            if result is None:
                state["committed"] = state["streaming"] = True
                writer.write(b"HTTP/1.1 200 Response\r\nContent-Type: text/event-stream\r\n"
                             b"Transfer-Encoding: chunked\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n")
                await self._drain(writer)
            while True:
                try:
                    chunk = await asyncio.wait_for(anext(stream), self.read_timeout)
                except StopAsyncIteration:
                    break
                total += len(chunk)
                if total > MAX_RESULT:
                    raise ValueError("response too large")
                if result is not None:
                    result.extend(chunk)
                    continue
                for raw, value in parser.feed(chunk):
                    kind = value.get("type") if value else None
                    if kind in ("error", "response.failed"):
                        raise ValueError("failed response")
                    terminal = kind in ("response.completed", "response.incomplete")
                    if terminal and (not isinstance(value.get("response"), dict)
                                     or value["response"].get("status") != kind.split(".")[1]):
                        raise ValueError("invalid terminal")
                    await self._chunk(writer, raw)
                    if terminal:
                        writer.write(b"0\r\n\r\n")
                        await self._drain(writer)
                        self._record(key, digest, "delivered")
                        return
            if result is None:
                raise ValueError("missing terminal")
            value = json.loads(bytes(result), object_pairs_hook=_json_object)
            if not isinstance(value, dict) or "error" in value or value.get("status") == "failed":
                raise ValueError("failed response")
            await self._reply(writer, 200, bytes(result), content_type, state=state)
            self._record(key, digest, "delivered")

    async def _transfer(self, reader, writer, key, digest, body, state):
        # Observe native disconnect even while central is paused between events.
        transfer = asyncio.create_task(self._forward(writer, key, digest, body, state))
        disconnected = asyncio.create_task(reader.read(1))
        try:
            async with asyncio.timeout(self.total_timeout):
                done, _ = await asyncio.wait((transfer, disconnected), return_when=asyncio.FIRST_COMPLETED)
                if transfer in done:
                    await transfer
                else:
                    raise ConnectionError("native disconnected")
        finally:
            for task in (transfer, disconnected):
                if not task.done():
                    task.cancel()
            await asyncio.gather(transfer, disconnected, return_exceptions=True)

    async def _handle(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        key = digest = None
        state = {"committed": False}
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
            lines = raw.decode("ascii").split("\r\n")
            if lines[0] != "POST /responses HTTP/1.1":
                await self._reply(writer, 404, b'{"error":{"code":"not_found"}}', state=state)
                return
            headers = {}
            for line in lines[1:]:
                if not line:
                    continue
                name, value = line.split(":", 1)
                name = name.lower()
                if name in headers:
                    raise ValueError
                headers[name] = value.strip()
            if (headers.get("host") != self.authority or headers.get("origin")
                    or not secrets.compare_digest(headers.get("authorization", ""), "Bearer " + self.local_token)):
                await self._reply(writer, 403, b'{"error":{"code":"forbidden"}}', state=state)
                return
            if "transfer-encoding" in headers or not headers.get("content-type", "").startswith("application/json"):
                raise ValueError
            length = int(headers["content-length"])
            if not 0 < length <= MAX_BODY:
                raise ValueError
            body = await asyncio.wait_for(reader.readexactly(length), 15)
            self._validate(body)
            async with self.lock:
                if self.accepted >= 32:
                    await self._reply(writer, 429, b'{"error":{"code":"invocation_limit"}}', state=state)
                    return
                try:
                    await self._renew_before_operation()
                except Exception:
                    await self._reply(writer, 502, b'{"error":{"code":"central_execution_unavailable"}}', state=state)
                    return
                self.accepted += 1
                key, digest = str(uuid.uuid4()), hashlib.sha256(body).hexdigest()
                self._record(key, digest, "sending")
                await self._transfer(reader, writer, key, digest, body, state)
        except asyncio.CancelledError:
            if key:
                self._record(key, digest, "pending")
            raise
        except Exception:
            if key:
                self._record(key, digest, "pending")
            try:
                if state.get("streaming"):
                    await self._chunk(writer, STREAM_ERROR)
                elif not state["committed"]:
                    await self._reply(writer, 502 if key else 400, b'{"error":{"code":"central_request_failed"}}', state=state)
            except (OSError, RuntimeError, TimeoutError):
                pass
        finally:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), self.drain_timeout)
            except (OSError, RuntimeError, TimeoutError):
                pass
            finally:
                self.tasks.discard(task)

    async def __aexit__(self, *args):
        self.server.close()
        for task in list(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)
        await self.server.wait_closed()
        await self.client.aclose()
        self._renew = None
        self._task_token = ""
        self.local_token = ""
