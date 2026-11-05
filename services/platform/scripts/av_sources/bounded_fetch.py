"""Quarantine bounded pack candidates from two fixed public Git repositories.

This transport does not adopt build inputs or verify the complete object/tree or
signature chain. Socket timeouts and cooperative deadline checks are not OS wall
time/memory isolation. DNS/IP egress isolation requires integration verification.
"""
from dataclasses import dataclass, fields
from functools import partial
import http.client
import hashlib
import math
import os
import re
import ssl
import stat
import time
import urllib.error
import urllib.request

from av_sources.smart_http import (SourceTransportError, build_fetch_request,
                                   decode_pack_response, parse_advertisement)


@dataclass(frozen=True)
class HttpLimits:
    metadata_bytes: int = 4 * 1024 * 1024
    response_body_bytes: int = 64 * 1024 * 1024
    total_body_bytes: int = 128 * 1024 * 1024
    total_input_bytes: int = 128 * 1024 * 1024
    max_requests: int = 64
    request_seconds: float = 20
    batch_seconds: float = 600


_SOURCES = {
    'libplacebo': ('https://github.com/haasn/libplacebo.git',
                  '275f7dea27924cdd34ab2e6c20e448b6bc4fab65'),
    'vulkan_headers': ('https://github.com/KhronosGroup/Vulkan-Headers.git',
                       '74d8a6cb930c68ef617b202c3ff3c59d919e086b'),
}


def _validate_limits(limits):
    if not isinstance(limits, HttpLimits):
        raise SourceTransportError('invalid HTTP limits')
    ceilings = HttpLimits()
    for field in fields(HttpLimits):
        value = getattr(limits, field.name)
        if field.name.endswith('_seconds'):
            valid = (type(value) in (float, int)
                     and 0 < value <= getattr(ceilings, field.name)
                     and math.isfinite(value))
        else:
            valid = type(value) is int and value > 0
        if not valid or value > getattr(ceilings, field.name):
            raise SourceTransportError('invalid HTTP limit')
    if limits.max_requests < 2:
        raise SourceTransportError('two requests required within budget')


def _new_directory(destination):
    """Create beneath a caller-owned 0700 parent with a single trusted writer.

    Concurrent malicious mutation by the same uid is outside this boundary:
    mkdir has no atomic create-and-open-directory syscall. Check the created
    name, then the opened inode, before use; never chmod an unknown directory.
    """
    try:
        path = os.fspath(destination)
        if not isinstance(path, str) or not path or '\0' in path or '..' in path.split('/'):
            raise SourceTransportError('unsafe destination path')
        absolute = os.path.abspath(path)
        parts = absolute.split('/')[1:]
        if not parts or not parts[-1]:
            raise SourceTransportError('unsafe destination path')
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fd = os.open('/', directory_flags)
        try:
            for component in parts[:-1]:
                next_fd = os.open(component, directory_flags, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            parent = os.fstat(fd)
            if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700:
                raise SourceTransportError('destination parent must be owned and private')
            os.mkdir(parts[-1], mode=0o700, dir_fd=fd)
            created = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
            if (not stat.S_ISDIR(created.st_mode) or created.st_uid != os.geteuid()
                    or stat.S_IMODE(created.st_mode) != 0o700):
                raise SourceTransportError('unsafe created destination directory')
            child_fd = os.open(parts[-1], directory_flags, dir_fd=fd)
            try:
                child = os.fstat(child_fd)
                named = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
                identity = (created.st_dev, created.st_ino)
                if (not stat.S_ISDIR(child.st_mode) or child.st_uid != os.geteuid()
                        or stat.S_IMODE(child.st_mode) != 0o700
                        or (child.st_dev, child.st_ino) != identity
                        or (named.st_dev, named.st_ino) != identity):
                    raise SourceTransportError('unsafe destination directory identity')
            except BaseException:
                os.close(child_fd)
                raise
            return child_fd, absolute
        finally:
            os.close(fd)
    except (OSError, TypeError, ValueError) as error:
        if isinstance(error, SourceTransportError):
            raise
        raise SourceTransportError('destination creation failed') from None


def _new_file(directory_fd, name):
    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory_fd)
        try:
            os.fchmod(fd, 0o600)
            return os.fdopen(fd, 'wb')
        except BaseException:
            os.close(fd)
            raise
    except OSError:
        raise SourceTransportError('candidate file creation failed') from None


def _close_response(response):
    if response is not None:
        try:
            response.close()
        except Exception:
            pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _close_response(fp)
        raise SourceTransportError('source redirect forbidden')

    def http_error_302(self, req, fp, code, msg, headers):
        # Reject before urllib's location parsing or its read-and-close path.
        # Missing/invalid Location and unsupported schemes also own a stream.
        _close_response(fp)
        raise SourceTransportError('source redirect forbidden')

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


class _RawHTTPInput:
    """Meter decrypted HTTP reads *before* HTTPResponse parses/discards them.

    Initial status/headers and chunk size/extensions/separators/trailers are
    metadata. Advertisement payload is also metadata. Each byte belongs to
    exactly one input class and is charged to total_input_bytes exactly once.
    This counts HTTP bytes delivered by the socket's file reader, not TLS/TCP
    records or a socket buffer's possible read-ahead.
    """
    def __init__(self, stream, budget, deadline, advertisement):
        self.stream = stream
        self.budget = budget
        self.deadline = deadline
        self.advertisement = advertisement
        self.metadata = True

    def _read(self, method, amount):
        self.budget.check(self.deadline)
        if type(amount) is not int or amount < 0:
            raise SourceTransportError('unbounded raw HTTP read forbidden')
        if amount == 0:
            return b''
        is_metadata = self.metadata or self.advertisement
        remaining = self.budget.limits.total_input_bytes - self.budget.input_used
        if is_metadata:
            remaining = min(remaining, self.budget.limits.metadata_bytes - self.budget.metadata_used)
        if remaining <= 0:
            raise SourceTransportError('source HTTP input or metadata budget exhausted')
        data = getattr(self.stream, method)(min(amount, remaining))
        self.budget.check(self.deadline)
        if not isinstance(data, bytes) or len(data) > min(amount, remaining):
            raise SourceTransportError('invalid raw HTTP stream')
        self.budget.charge_input(len(data), metadata=is_metadata)
        return data

    def read(self, amount):
        return self._read('read', amount)

    def readline(self, amount):
        return self._read('readline', amount)

    def close(self):
        self.stream.close()


class _HTTPResponse(http.client.HTTPResponse):
    def __init__(self, *args, budget, deadline, advertisement, **kwargs):
        super().__init__(*args, **kwargs)
        self._av_raw_budget = budget
        self._av_input = _RawHTTPInput(self.fp, budget, deadline, advertisement)
        self.fp = self._av_input

    def begin(self):
        try:
            super().begin()
            transfer = _header(self.headers, 'Transfer-Encoding')
            if transfer is not None:
                if (transfer.lower().strip() != 'chunked'
                        or _header(self.headers, 'Content-Length') is not None):
                    raise SourceTransportError('unsupported source transfer encoding')
            self._av_input.metadata = False
        except BaseException:
            self.close()
            raise

    def _get_chunk_left(self):
        # The stdlib reads chunk separator CRLF, size/extensions and trailers
        # here, before _read_chunked reads actual payload through the same fp.
        self._av_input.metadata = True
        try:
            return super()._get_chunk_left()
        finally:
            self._av_input.metadata = False


class _HTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, budget, **kwargs):
        super().__init__(**kwargs)
        self.budget = budget

    def https_open(self, req):
        def connection(host, **kwargs):
            result = http.client.HTTPSConnection(host, **kwargs)
            result.response_class = partial(
                _HTTPResponse, budget=self.budget, deadline=req._av_deadline,
                advertisement=req._av_advertisement)
            return result
        return self.do_open(connection, req, context=self._context)


def _production_open(budget):
    # A fresh opener never inherits the global opener, proxy environment,
    # cookie jar or authentication handlers. TLS uses system trust validation.
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoRedirect(),
        _HTTPSHandler(budget, context=ssl.create_default_context()))
    opener.addheaders = []
    return opener.open


class _Budget:
    def __init__(self, limits):
        self.limits = limits
        self.deadline = time.monotonic() + limits.batch_seconds
        self.used = 0  # decoded HTTP body bytes, including Git framing
        self.input_used = 0  # decrypted raw HTTP bytes, counted once
        self.metadata_used = 0  # HTTP framing/headers + advertisement payload
        self.requests = 0

    def charge_input(self, amount, *, metadata):
        if (self.input_used + amount > self.limits.total_input_bytes
                or (metadata and self.metadata_used + amount > self.limits.metadata_bytes)):
            raise SourceTransportError('source HTTP input or metadata budget exceeded')
        self.input_used += amount
        if metadata:
            self.metadata_used += amount

    def check(self, deadline=None):
        now = time.monotonic()
        if now >= self.deadline or (deadline is not None and now >= deadline):
            raise SourceTransportError('source request deadline exceeded')


def _header(headers, name):
    # HTTPMessage preserves duplicate fields; reject ambiguity before reading.
    if hasattr(headers, 'get_all'):
        values = headers.get_all(name, [])
    else:
        values = [v for k, v in headers.items() if k.lower() == name.lower()]
    if len(values) > 1 or any(not isinstance(value, str) for value in values):
        raise SourceTransportError('ambiguous source response header')
    return values[0] if values else None


class _Body:
    def __init__(self, response, request, budget, deadline, limit, mime):
        self.response = response
        self.budget = budget
        self.deadline = deadline
        self.limit = limit
        self.used = 0
        self.advertisement = request._av_advertisement
        self.raw_metered = getattr(response, '_av_raw_budget', None) is budget
        budget.check(deadline)
        if response.status != 200 or response.geturl() != request.full_url:
            raise SourceTransportError('source status or final URL rejected')
        kind = _header(response.headers, 'Content-Type')
        encoding = _header(response.headers, 'Content-Encoding')
        if kind is None or kind.lower().strip() != mime or encoding not in (None, 'identity'):
            raise SourceTransportError('source media type or encoding rejected')
        declared = _header(response.headers, 'Content-Length')
        self.declared = None
        if declared is not None:
            if re.fullmatch('[0-9]+', declared) is None or len(declared) > 20:
                raise SourceTransportError('invalid source content length')
            self.declared = int(declared)
            if self.declared >= min(limit, budget.limits.total_body_bytes - budget.used):
                raise SourceTransportError('source declared body exceeds budget')

    def read(self, size):
        if type(size) is not int or size < 0:
            raise SourceTransportError('unbounded source read forbidden')
        self.budget.check(self.deadline)
        if size == 0:
            return b''
        remaining = min(self.limit - self.used,
                        self.budget.limits.total_body_bytes - self.budget.used,
                        self.budget.limits.total_input_bytes - self.budget.input_used)
        if self.advertisement:
            remaining = min(remaining, self.budget.limits.metadata_bytes - self.budget.metadata_used)
        if remaining <= 0:
            raise SourceTransportError('source body budget exhausted before EOF')
        amount = min(size, remaining, 64 * 1024)
        try:
            data = self.response.read(amount)
        except (OSError, ValueError):
            raise SourceTransportError('source body read failed') from None
        self.budget.check(self.deadline)
        if not isinstance(data, bytes) or len(data) > amount:
            raise SourceTransportError('invalid source body stream')
        self.used += len(data)
        self.budget.used += len(data)
        if not self.raw_metered:
            # Body-only test seam has no HTTP parser/header bytes. It cannot
            # establish production HTTP accounting, but payload still counts.
            self.budget.charge_input(len(data), metadata=self.advertisement)
        if self.declared is not None:
            if self.used > self.declared or (not data and self.used != self.declared):
                raise SourceTransportError('source content length mismatch')
        return data


def _request(open_response, request, budget, limit, mime, consume):
    budget.check()
    if budget.requests >= budget.limits.max_requests:
        raise SourceTransportError('source request count exceeded')
    deadline = min(budget.deadline, time.monotonic() + budget.limits.request_seconds)
    timeout = deadline - time.monotonic()
    if timeout <= 0:
        raise SourceTransportError('source request deadline exceeded')
    budget.requests += 1
    response = None
    request._av_deadline = deadline
    request._av_advertisement = request.get_method() == 'GET'
    try:
        response = open_response(request, timeout=timeout)
        body = _Body(response, request, budget, deadline, limit, mime)
        value = consume(body)
        budget.check(deadline)
        return value
    except SourceTransportError:
        raise
    except urllib.error.HTTPError as error:
        # urllib raises before open_response returns; this branch owns fp.
        # Closing the HTTPError also closes its fp, without reading the body.
        _close_response(error)
        raise SourceTransportError('source request failed') from None
    except Exception:
        # Network diagnostics can contain private proxy/auth/host information.
        raise SourceTransportError('source request failed') from None
    finally:
        _close_response(response)


def fetch_pack(source_id, destination, limits=HttpLimits(), _open=None):
    """Fetch one quarantine candidate without retries or repository execution.

    ``_open`` is an offline test seam returning a bounded byte stream response.
    Its use is recorded in the receipt and is not production egress evidence.
    Existing destinations are rejected; failed partial candidates are retained.
    The caller supplies an owned 0700 parent with no malicious same-uid writer.
    HTTP metadata (status/headers/transfer framing plus advertisement payload)
    shares a 4MiB batch cap. response_body_bytes retains decoded HTTP payload
    semantics; total_input_bytes additionally counts raw HTTP metadata once.
    Neither counter measures TLS records, TCP bytes or socket read-ahead.
    """
    if not isinstance(source_id, str) or source_id not in _SOURCES:
        raise SourceTransportError('unknown fixed public source')
    _validate_limits(limits)
    base, target = _SOURCES[source_id]
    budget = _Budget(limits)
    directory_fd, absolute = _new_directory(destination)
    try:
        open_response = _open if _open is not None else _production_open(budget)
        common = {'Accept-Encoding': 'identity', 'User-Agent': 'Easel-AV-source-quarantine/1'}
        advertisement_request = urllib.request.Request(
            base + '/info/refs?service=git-upload-pack',
            headers={**common, 'Accept': 'application/x-git-upload-pack-advertisement'}, method='GET')
        with _new_file(directory_fd, 'advertisement.bin') as sink:
            def receive_advertisement(body):
                chunks = []
                while True:
                    chunk = body.read(64 * 1024)
                    if not chunk:
                        break
                    if sink.write(chunk) != len(chunk):
                        raise SourceTransportError('short advertisement sink write')
                    chunks.append(chunk)
                return b''.join(chunks)
            advertisement = _request(
                open_response, advertisement_request, budget,
                min(limits.metadata_bytes, limits.response_body_bytes),
                'application/x-git-upload-pack-advertisement', receive_advertisement)
        parsed = parse_advertisement(advertisement, target=target, max_bytes=limits.metadata_bytes)
        pack_request = urllib.request.Request(
            base + '/git-upload-pack', data=build_fetch_request(target, parsed),
            headers={**common, 'Content-Type': 'application/x-git-upload-pack-request',
                     'Accept': 'application/x-git-upload-pack-result'}, method='POST')
        with _new_file(directory_fd, 'source.pack') as sink:
            pack = _request(
                open_response, pack_request, budget, limits.response_body_bytes,
                'application/x-git-upload-pack-result',
                lambda body: decode_pack_response(
                    body, sink, target=target, max_response_bytes=limits.response_body_bytes,
                    max_pack_bytes=limits.response_body_bytes))
        budget.check()
        return {'source_id': source_id, 'repository_url': base, 'commit_oid': target,
                'destination': absolute, 'request_count': budget.requests,
                'response_body_bytes': budget.used,
                'total_input_bytes': budget.input_used,
                'metadata_bytes': budget.metadata_used,
                'http_raw_input_metered': _open is None,
                'advertisement_sha256': hashlib.sha256(advertisement).hexdigest(),
                'pack': pack, 'transport_injected_for_tests': _open is not None,
                'source_identity_verified': False, 'build_inputs_adopted': False,
                'os_resource_limits_verified': False}
    except SourceTransportError:
        raise
    except Exception:
        raise SourceTransportError('source candidate acquisition failed') from None
    finally:
        os.close(directory_fd)
