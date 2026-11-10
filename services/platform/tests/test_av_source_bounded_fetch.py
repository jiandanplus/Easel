"""HTTP transport tests use injected byte streams, never live providers."""
import hashlib
import importlib.util
import io
from pathlib import Path
import struct
import sys
import zlib

import pytest


MODULE = Path(__file__).parents[1] / 'scripts/av_sources/bounded_fetch.py'
LIBPLACEBO = '275f7dea27924cdd34ab2e6c20e448b6bc4fab65'
BASE = 'https://github.com/haasn/libplacebo.git'


def subject():
    assert MODULE.is_file(), 'bounded fixed-source downloader is missing'
    scripts = str(MODULE.parents[1])
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    name = '_easel_av_bounded_fetch'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, MODULE)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def packet(body):
    return f'{len(body) + 4:04x}'.encode() + body


def bodies():
    ad = (packet(b'# service=git-upload-pack\n') + b'0000'
          + packet((LIBPLACEBO + ' HEAD\0side-band-64k ofs-delta shallow\n').encode()) + b'0000')
    content = b'abc'
    pack = b'PACK' + struct.pack('>II', 2, 1) + b'3' + zlib.compress(content)
    pack += hashlib.sha1(pack).digest()
    wire = packet(('shallow ' + LIBPLACEBO + '\n').encode()) + b'0000' + packet(b'NAK\n')
    wire += packet(b'\x01' + pack) + b'0000'
    return ad, wire, pack


class Response(io.BytesIO):
    def __init__(self, body, url, kind, *, status=200, encoding=None, declared=None):
        super().__init__(body)
        self.status = status
        self.url = url
        self.headers = {'Content-Type': kind}
        if encoding:
            self.headers['Content-Encoding'] = encoding
        if declared is not None:
            self.headers['Content-Length'] = str(declared)
        self.read_calls = 0

    def read(self, n=-1):
        assert n >= 0, 'transport must never ask for an unbounded read'
        self.read_calls += 1
        return super().read(n)

    def geturl(self):
        return self.url


def opener(ad_override=None, pack_override=None, response_options=None):
    ad, wire, _ = bodies()
    responses, calls = [], []

    def call(request, *, timeout):
        calls.append((request, timeout))
        first = len(calls) == 1
        body = (ad if ad_override is None else ad_override) if first else (wire if pack_override is None else pack_override)
        kind = 'application/x-git-upload-pack-' + ('advertisement' if first else 'result')
        options = response_options or {}
        result = Response(body, request.full_url, kind, **options)
        responses.append(result)
        return result

    return call, calls, responses


def test_fixed_source_fetch_keeps_pack_candidate_and_has_no_credentials(tmp_path):
    m = subject()
    open_response, calls, _ = opener()
    destination = tmp_path.resolve() / 'new-fetch'
    result = m.fetch_pack('libplacebo', destination, _open=open_response)
    assert len(calls) == 2
    assert [r.full_url for r, _ in calls] == [BASE + '/info/refs?service=git-upload-pack', BASE + '/git-upload-pack']
    assert [r.get_method() for r, _ in calls] == ['GET', 'POST']
    for request, timeout in calls:
        assert timeout <= 20
        headers = {k.lower(): v for k, v in request.header_items()}
        assert not {'authorization', 'cookie', 'proxy-authorization'} & set(headers)
        assert headers['accept-encoding'] == 'identity'
    ad, wire, pack = bodies()
    assert (destination / 'advertisement.bin').read_bytes() == ad
    assert (destination / 'source.pack').read_bytes() == pack
    assert result['response_body_bytes'] == len(ad) + len(wire)
    assert result['request_count'] == 2
    assert result['transport_injected_for_tests'] is True
    assert result['source_identity_verified'] is False
    assert result['build_inputs_adopted'] is False
    assert result['os_resource_limits_verified'] is False
    assert result['pack']['objects_verified'] is False
    assert destination.stat().st_mode & 0o777 == 0o700
    assert (destination / 'source.pack').stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('source', ['unknown', BASE, 'http://127.0.0.1', '../libplacebo'])
def test_unknown_source_never_opens_network_or_creates_destination(tmp_path, source):
    m = subject()
    open_response, calls, _ = opener()
    destination = tmp_path.resolve() / 'new-fetch'
    with pytest.raises(m.SourceTransportError):
        m.fetch_pack(source, destination, _open=open_response)
    assert calls == [] and not destination.exists()


@pytest.mark.parametrize('options', [{'status': 302}, {'status': 401}, {'encoding': 'gzip'},
                                    {'declared': 10**9}, {'declared': -1}, {'declared': 'bad'}])
def test_bad_response_fails_without_retry_and_no_verified_receipt(tmp_path, options):
    m = subject()
    open_response, calls, responses = opener(response_options=options)
    with pytest.raises(m.SourceTransportError):
        m.fetch_pack('libplacebo', tmp_path.resolve() / 'new-fetch', _open=open_response)
    assert len(calls) == 1
    if options.get('declared') == 10**9:
        assert responses[0].read_calls == 0


def test_content_length_mismatch_is_not_accepted(tmp_path):
    m = subject()
    open_response, calls, _ = opener(response_options={'declared': 1})
    with pytest.raises(m.SourceTransportError):
        m.fetch_pack('libplacebo', tmp_path.resolve() / 'new-fetch', _open=open_response)
    assert len(calls) == 1


def test_redirect_cannot_be_followed_by_an_injected_transport(tmp_path):
    m = subject()
    normal, calls, _ = opener()

    def redirect(request, *, timeout):
        result = normal(request, timeout=timeout)
        result.url = 'https://other.invalid/private'
        return result

    with pytest.raises(m.SourceTransportError):
        m.fetch_pack('libplacebo', tmp_path.resolve() / 'new-fetch', _open=redirect)
    assert len(calls) == 1


@pytest.mark.parametrize('key,value', [('metadata_bytes', 8), ('response_body_bytes', 8),
                                      ('total_body_bytes', 8), ('max_requests', 1)])
def test_transport_budgets_are_not_posthoc_only(tmp_path, key, value):
    m = subject()
    open_response, calls, _ = opener()
    limits = m.HttpLimits(**{key: value})
    with pytest.raises(m.SourceTransportError):
        m.fetch_pack('libplacebo', tmp_path.resolve() / 'new-fetch', limits=limits, _open=open_response)
    assert len(calls) <= 1


def test_truncated_pack_preserves_partial_candidate_without_resubmission(tmp_path):
    m = subject()
    open_response, calls, _ = opener(pack_override=b'0000' + packet(b'NAK\n') + b'0010\x01PACK')
    destination = tmp_path.resolve() / 'new-fetch'
    with pytest.raises(m.SourceTransportError):
        m.fetch_pack('libplacebo', destination, _open=open_response)
    assert len(calls) == 2 and destination.is_dir()
    assert (destination / 'advertisement.bin').exists()


def test_existing_or_symlink_destination_and_untrusted_parent_are_rejected(tmp_path):
    m = subject()
    parent = tmp_path.resolve()
    existing = parent / 'existing'
    existing.mkdir(mode=0o700)
    sentinel = existing / 'keep'
    sentinel.write_bytes(b'keep')
    link = parent / 'link'
    link.symlink_to(existing, target_is_directory=True)
    unsafe = parent / 'unsafe'
    unsafe.mkdir(mode=0o755)
    for destination in [existing, link, unsafe / 'child']:
        open_response, calls, _ = opener()
        with pytest.raises(m.SourceTransportError):
            m.fetch_pack('libplacebo', destination, _open=open_response)
        assert calls == []
    assert sentinel.read_bytes() == b'keep'


def test_timeout_is_sanitized_and_never_retried(tmp_path):
    m = subject()
    calls = []

    def fail(request, *, timeout):
        calls.append(request)
        raise TimeoutError('sensitive upstream diagnostic')

    with pytest.raises(m.SourceTransportError) as error:
        m.fetch_pack('libplacebo', tmp_path.resolve() / 'new-fetch', _open=fail)
    assert len(calls) == 1 and 'sensitive' not in str(error.value)
