"""Finite AV API and fixed Unix transport; importing/construction opens no resources.

http_module MUST be an authenticated, privately prepared pinned pack_http module.
No ambient import/fallback is provided. This component validates its interface,
not its authenticity. Task2B3 owns recipe semantics, module preparation, same-boot
identity, guardian, persistence, fences and the trusted controller. admit_stdout
is that controller's assertion of pinned semantic validation, not authentication.
Injected factories/clocks are pure-test evidence only, never native wall proof.
"""
from dataclasses import dataclass
from enum import Enum
import json
import math
import re
import socket
import threading
import time

IMAGE = "sha256:3f9c7f75b916e8279e24136d7a57769aba9e193715be52570417698c52d6eed0"
SOCKET = "/Users/jiandan/.docker/run/docker.sock"
PREFIX = "/v1.49"
_SAFE = "invalid AV API operation"
_JSON_MAX = dict(depth=32, openings=8192, string_bytes=131072,
                 number_chars=20, commas=65536, colons=65536)
_PRESETS = {"METADATA_LIMITS": ("metadata", 131072, 163840),
            "STDOUT_LIMITS": ("stdout", 65536, 98304),
            "ARTIFACT_LIMITS": ("artifact", 16809985, 16842752)}
_COMMON = dict(overhead_bytes=32767, header_bytes=16384, header_fields=64,
               line_bytes=8192, status_bytes=1024, chunks=8192,
               chunk_size_digits=16, read_bytes=65536)


class APIError(ValueError):
    """Fixed failure with no peer/dependency exception context."""


def _require(value):
    if not value:
        raise APIError(_SAFE)


class Operation(Enum):
    INFO = "info"
    IMAGE = "image"
    CREATE = "create"
    INSPECT = "inspect"
    START = "start"
    STDOUT = "stdout"
    ARTIFACT = "artifact"


@dataclass(frozen=True)
class FailureDiagnostic:
    """Bounded local observations; no send or response admission proof."""
    operation: str | None
    phase: str
    http_status: int | None
    injected_for_tests: bool

    def __post_init__(self):
        try:
            _require(self.operation is None or type(self.operation) is str and
                     self.operation in tuple(op.value for op in Operation))
            _require(type(self.phase) is str and self.phase in ("request", "transport", "decode", "publish"))
            _require(self.http_status is None or type(self.http_status) is int and 100 <= self.http_status <= 599)
            _require(type(self.injected_for_tests) is bool)
        except BaseException:
            pass
        else:
            return
        raise APIError(_SAFE)


@dataclass(frozen=True)
class Request:
    """Internal request; callers cannot submit arbitrary Request instances."""
    operation: Operation
    method: str
    path: str
    status: int
    content_types: tuple
    limits: object
    body: bytes


@dataclass(frozen=True)
class WireCounters:
    wire_bytes: int
    overhead_bytes: int
    header_bytes: int
    header_fields: int
    chunks: int
    framing: str


@dataclass(frozen=True)
class Result:
    """Decoded metadata or immutable mux bytes; no transport acceptance marker.

    Metadata dictionaries are caller-owned, independent of internal state gates.
    They can contain daemon data; do not persist/log them as safe logging facts.
    Only counters and injected_for_tests are sanitized evidence fields.
    """
    operation: Operation
    value: object
    counters: WireCounters
    injected_for_tests: bool


def _prescan(raw, caps):
    _require(type(raw) is bytes and 0 < len(raw) <= 131072)
    depth = openings = commas = colons = 0
    i = 0
    while i < len(raw):
        c = raw[i]
        if c == 34:
            start = i
            i += 1
            while i < len(raw):
                c = raw[i]
                if c == 34:
                    break
                if c == 92:
                    i += 1  # escaped quote/backslash cannot terminate this token
                i += 1
                _require(i - start + 1 <= caps["string_bytes"])
            _require(i < len(raw) and i - start + 1 <= caps["string_bytes"])
        elif c in (91, 123):
            depth += 1
            openings += 1
            _require(depth <= caps["depth"] and openings <= caps["openings"])
        elif c in (93, 125):
            depth -= 1
            _require(depth >= 0)
        elif c == 44:
            commas += 1
            _require(commas <= caps["commas"])
        elif c == 58:
            colons += 1
            _require(colons <= caps["colons"])
        elif c == 45 or 48 <= c <= 57:
            start = i
            while i + 1 < len(raw) and raw[i + 1] in b"0123456789eE+.-":
                i += 1
            _require(i - start + 1 <= caps["number_chars"])
        i += 1
    _require(depth == 0)


def _pairs(rows):
    result = {}
    for key, value in rows:
        _require(key not in result)
        result[key] = value
    return result


def _float(token):
    value = float(token)
    _require(math.isfinite(value))
    return value


def _constant(_):
    raise APIError(_SAFE)


def _json(raw, caps):
    _prescan(raw, caps)
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                       parse_float=_float, parse_constant=_constant)
    _require(type(value) is dict)
    return value


def _http(module):
    _require(callable(module.read_response) and callable(module.ResponseLimits))
    for name, (preset, body, wire) in _PRESETS.items():
        limits = getattr(module, name)
        _require(type(limits) is module.ResponseLimits)
        expected = dict(_COMMON, preset=preset, body_bytes=body, wire_bytes=wire)
        _require(vars(limits) == expected)
        _require(vars(module.ResponseLimits(**vars(limits))) == expected)


class _OwnedCall:
    """One exact socket, published before blocking connect; no reconnect path."""
    def __init__(self, client, end):
        self.client, self.end = client, end
        self.lock = threading.RLock()
        self.owned = None
        self._http_status = None
        self.cancelled = self.cleanup_failed = False

    def _observe_status(self, status):
        _require(self._http_status is None and type(status) is int and 100 <= status <= 599)
        self._http_status = status

    def guard(self):
        _require(not self.cancelled and not self.cleanup_failed and
                 not self.client._poisoned and self.client._clock() < self.end)

    def release(self, cancelled=False):
        # Transfer ownership under lock; shutdown/close outside the lock. Repeated
        # cancellation/finalization can never close a replacement or close twice.
        sock = None
        try:
            with self.lock:
                if cancelled:
                    self.cancelled = True
                sock, self.owned = self.owned, None
        finally:
            # Once transferred, this local handle must be finalized even if the
            # lock's context exit is interrupted. A later cancel sees no owner.
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass  # already disconnected/unconnected is expected
                except BaseException:
                    self.cleanup_failed = True
                try:
                    sock.close()
                except BaseException:
                    self.cleanup_failed = True

    def cancel(self):
        # Timer callbacks may never leak raw dependency errors to stderr.
        try:
            self.release(cancelled=True)
        except BaseException:
            self.cleanup_failed = True
            self.cancelled = True

    def timeout(self, sock):
        self.guard()
        sock.settimeout(min(5.0, self.end - self.client._clock()))
        self.guard()

    def run(self, request):
        timer = None
        response = None
        failed = False
        try:
            with self.lock:
                self.guard()
                self.owned = self.client._socket_factory(socket.AF_UNIX, socket.SOCK_STREAM)
                sock = self.owned
            self.guard()
            remaining = self.end - self.client._clock()
            _require(remaining > 0)
            timer = self.client._timer_factory(remaining, self.cancel)
            timer.daemon = True
            timer.start()
            self.timeout(sock)
            try:
                sock.connect(SOCKET)
            finally:
                self.guard()
            self.timeout(sock)
            # sendall has a total-operation timeout; an exception leaves unknown
            # sent length. The API attempt was consumed and is never retried.
            raw = (request.method + " " + PREFIX + request.path +
                   " HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n" +
                   ("Content-Type: application/json\r\nContent-Length: " +
                    str(len(request.body)) + "\r\n" if request.method == "POST" else "") +
                   "\r\n").encode("ascii") + request.body
            _require(len(raw) <= 132096)
            try:
                sock.sendall(raw)
            finally:
                self.guard()
            def read(n):
                self.timeout(sock)
                try:
                    return sock.recv(n)
                finally:
                    self.guard()
            response = self.client._http.read_response(
                read, guard=self.guard, expected_status=request.status,
                content_types=request.content_types, limits=request.limits,
                on_status=self._observe_status)
            self.guard()
        except BaseException:
            failed = True
        finally:
            # cancel() alone is not a completion proof: join bounded by the SAME
            # original call deadline, then reject if callback is still alive.
            if timer is not None:
                try:
                    timer.cancel()
                except BaseException:
                    self.cleanup_failed = True
            try:
                self.release()
            except BaseException:
                # A lock acquisition/exit interruption must not skip the timer
                # completion checks. call() will cancel any still-owned socket.
                self.cleanup_failed = True
            if timer is not None:
                try:
                    timer.join(timeout=max(0.0, self.end - self.client._clock()))
                except BaseException:
                    self.cleanup_failed = True
                try:
                    if timer.is_alive():
                        self.cleanup_failed = True
                except BaseException:
                    self.cleanup_failed = True
        _require(not failed)
        self.guard()
        return response


class APIClient:
    """Single finite case. Never retry/rebind after any API failure.

    Successful create returns decoded metadata before binding, allowing the
    controller to persist Id before interpreting Warnings. bind_created must
    match that exact response Id. Inspect polling is explicit and controller
    bounded; this client contains no polling loop. No recipe/config proof here.
    """
    def __init__(self, nonce, create_body, *, http_module=None,
                 socket_factory=None, timer_factory=None, clock=None, json_caps=None):
        try:
            _require(type(nonce) is str and re.fullmatch("[0-9a-f]{32}", nonce))
            _http(http_module)
            caps = dict(_JSON_MAX)
            if json_caps is not None:
                _require(type(json_caps) is dict)
                for key, value in json_caps.items():
                    _require(key in caps and type(value) is int and 0 < value <= caps[key])
                    caps[key] = value
            value = _json(create_body, caps)
            canonical = json.dumps(value, sort_keys=True, separators=(",", ":"),
                                   ensure_ascii=True, allow_nan=False).encode("ascii")
            _require(canonical == create_body)
            self._nonce, self._body = nonce, create_body
            self._http, self._caps = http_module, caps
            self._injected = any(x is not None for x in
                                 (socket_factory, timer_factory, clock, json_caps))
            self._socket_factory = socket.socket if socket_factory is None else socket_factory
            self._timer_factory = threading.Timer if timer_factory is None else timer_factory
            self._clock = time.monotonic if clock is None else clock
            _require(all(callable(x) for x in (self._socket_factory, self._timer_factory, self._clock)))
            self._lock = threading.RLock()
            self._attempted = set()
            self._success = set()
            self._poisoned = self._busy = False
            self._active = None
            self._last_failure = None
            self._failure_seen = False
            self._cid = self._created_id = self._deadline = None
            self._terminal = self._stdout_admitted = False
        except BaseException:
            pass
        else:
            return
        raise APIError(_SAFE)

    @property
    def injected_for_tests(self):
        return self._injected

    @property
    def attempted(self):
        return frozenset(self._attempted)

    @property
    def last_failure(self):
        return self._last_failure

    def _idle(self):
        _require(not self._poisoned and not self._busy)

    def bind_created(self, cid):
        try:
            with self._lock:
                self._idle()
                _require(Operation.CREATE in self._success and self._cid is None)
                _require(type(cid) is str and re.fullmatch("[0-9a-f]{64}", cid) and
                         cid == self._created_id)
                self._cid = cid
                return
        except BaseException:
            self._poisoned = True
        raise APIError(_SAFE)

    def bind_deadline(self, original_deadline):
        try:
            with self._lock:
                self._idle()
                _require(self._cid is not None and self._deadline is None)
                _require(type(original_deadline) in (int, float) and
                         math.isfinite(original_deadline) and original_deadline > 0)
                self._deadline = original_deadline
                return
        except BaseException:
            self._poisoned = True
        raise APIError(_SAFE)

    def admit_stdout(self):
        """Trusted controller notification after prepared validator admission."""
        try:
            with self._lock:
                self._idle()
                _require(Operation.STDOUT in self._success and not self._stdout_admitted)
                self._stdout_admitted = True
                return
        except BaseException:
            self._poisoned = True
        raise APIError(_SAFE)

    def cancel(self):
        try:
            with self._lock:
                self._poisoned = True
                active = self._active
            if active is not None:
                active.cancel()
                _require(not active.cleanup_failed)
            return
        except BaseException:
            self._poisoned = True
        raise APIError(_SAFE)

    def _request(self, op):
        _require(type(op) is Operation)
        if op is not Operation.INSPECT:
            _require(op not in self._attempted)
        if op is Operation.INFO:
            _require(not self._attempted)
            method, path, status = "GET", "/info", 200
        elif op is Operation.IMAGE:
            _require(Operation.INFO in self._success and Operation.CREATE not in self._attempted)
            method, path, status = "GET", "/images/" + IMAGE + "/json", 200
        elif op is Operation.CREATE:
            _require(Operation.IMAGE in self._success)
            method, path, status = "POST", ("/containers/create?name=run-av-pack-" + self._nonce +
                "&platform=linux%2Farm64%2Fv8"), 201
        else:
            _require(self._cid is not None)
            path = "/containers/" + self._cid
            method, status = "GET", 200
            if op is Operation.INSPECT:
                path += "/json"
            elif op is Operation.START:
                _require(self._deadline is not None)
                method, path, status = "POST", path + "/start", 204
            else:
                _require(Operation.START in self._success and self._terminal)
                if op is Operation.ARTIFACT:
                    _require(self._stdout_admitted and Operation.STDOUT in self._success)
                path += ("/logs?stdout=" + ("1&stderr=0" if op is Operation.STDOUT else "0&stderr=1") +
                         "&timestamps=0&follow=0&details=0&tail=all")
        stream = op in (Operation.STDOUT, Operation.ARTIFACT)
        limits = getattr(self._http, "STDOUT_LIMITS" if op is Operation.STDOUT else
                         "ARTIFACT_LIMITS" if op is Operation.ARTIFACT else "METADATA_LIMITS")
        types = (("application/vnd.docker.raw-stream", "application/vnd.docker.multiplexed-stream")
                 if stream else ("application/json",))
        return Request(op, method, path, status, types, limits,
                       self._body if op is Operation.CREATE else b"")

    def _decode(self, request, response):
        _require(type(response.body) is bytes and len(response.body) <= request.limits.body_bytes)
        _require(type(response.status) is int and response.status == request.status)
        names = ("wire_bytes", "overhead_bytes", "header_bytes", "header_fields", "chunks")
        for name in names:
            value = getattr(response, name)
            _require(type(value) is int and 0 <= value <= getattr(request.limits, name))
        _require(response.wire_bytes == response.overhead_bytes + len(response.body) and
                 response.header_bytes <= response.overhead_bytes and
                 response.framing in ("none", "content-length", "chunked"))
        counters = WireCounters(*(getattr(response, name) for name in names), response.framing)
        op = request.operation
        if op in (Operation.STDOUT, Operation.ARTIFACT):
            value = response.body
        elif op is Operation.START:
            _require(response.body == b"")
            value = None
        else:
            value = _json(response.body, self._caps)
        if op is Operation.CREATE:
            # Store an immutable string, independent of caller mutation.
            self._created_id = value.get("Id") if type(value.get("Id")) is str else None
        if op is Operation.INSPECT:
            _require(value.get("Id") == self._cid)
            state = value.get("State")
            self._terminal = (Operation.START in self._success and type(state) is dict and
                state.get("Status") == "exited" and
                all(state.get(k) is False for k in ("Running", "Paused", "Restarting", "Dead", "OOMKilled")) and
                all(type(state.get(k)) is int and state[k] == 0 for k in ("ExitCode", "Pid")) and
                state.get("Error") == "" and type(value.get("RestartCount")) is int and value["RestartCount"] == 0)
        return Result(op, value, counters, self._injected)

    def call(self, operation):
        active = None
        phase = "request"
        try:
            with self._lock:
                self._idle()
                _http(self._http)
                request = self._request(operation)
                self._attempted.add(operation)  # before deadline, factory or any I/O
                self._busy = True
                now = self._clock()
                _require(type(now) in (int, float) and math.isfinite(now))
                end = min(now + 5.0, self._deadline if self._deadline is not None else math.inf)
                _require(now < end)
                active = _OwnedCall(self, end)
                self._active = active
            phase = "transport"
            response = active.run(request)
            phase = "decode"
            result = self._decode(request, response)
            active.guard()  # parsing/admission must also fit the original deadline
            phase = "publish"
            with self._lock:
                active.guard()
                self._success.add(operation)
                self._busy = False
                self._active = None
            return result
        except BaseException:
            # Snapshot construction is best effort and cannot bypass cleanup.
            try:
                if not self._failure_seen:
                    self._failure_seen = True
                    self._last_failure = FailureDiagnostic(
                        operation.value if type(operation) is Operation else None,
                        phase, active._http_status if active is not None else None,
                        self._injected)
            except BaseException:
                pass
            self._poisoned = True
            self._busy = False
            self._active = None
            if active is not None:
                active.cancel()
        raise APIError(_SAFE)
