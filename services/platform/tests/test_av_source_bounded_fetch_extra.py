"""Offline security boundaries for the fixed-source transport."""
from dataclasses import FrozenInstanceError
import ssl
import urllib.request

import pytest

from test_av_source_bounded_fetch import BASE, Response, bodies, opener, subject


def test_limits_are_frozen_and_invalid_limits_never_open(tmp_path):
    m = subject()
    limits = m.HttpLimits()
    with pytest.raises(FrozenInstanceError):
        limits.max_requests = 100
    for values in [{'metadata_bytes': 0}, {'response_body_bytes': True},
                   {'request_seconds': float('nan')}, {'batch_seconds': -1}]:
        open_response, calls, _ = opener()
        with pytest.raises(m.SourceTransportError):
            m.fetch_pack('libplacebo', tmp_path / 'invalid',
                         limits=m.HttpLimits(**values), _open=open_response)
        assert calls == []


def test_parent_symlink_and_raw_dotdot_never_open(tmp_path):
    m = subject()
    parent = tmp_path.resolve()
    actual = parent / 'actual'
    actual.mkdir(mode=0o700)
    alias = parent / 'alias'
    alias.symlink_to(actual, target_is_directory=True)
    for target in [alias / 'child', str(actual) + '/../child']:
        open_response, calls, _ = opener()
        with pytest.raises(m.SourceTransportError):
            m.fetch_pack('libplacebo', target, _open=open_response)
        assert calls == []
    assert list(actual.iterdir()) == []


@pytest.mark.parametrize('kind,encoding', [('text/plain', None),
                                         ('application/x-git-upload-pack-advertisement', 'br')])
def test_wrong_media_or_encoding_is_rejected_before_body(tmp_path, kind, encoding):
    m = subject()
    responses = []

    def open_response(request, *, timeout):
        response = Response(bodies()[0], request.full_url, kind, encoding=encoding)
        responses.append(response)
        return response

    with pytest.raises(m.SourceTransportError):
        m.fetch_pack('libplacebo', tmp_path.resolve() / 'new', _open=open_response)
    assert len(responses) == 1 and responses[0].read_calls == 0


def test_total_budget_counts_advertisement_and_pack(tmp_path):
    m = subject()
    ad, wire, _ = bodies()
    open_response, calls, _ = opener()
    with pytest.raises(m.SourceTransportError):
        m.fetch_pack('libplacebo', tmp_path.resolve() / 'new',
                     limits=m.HttpLimits(total_body_bytes=len(ad) + len(wire) - 1),
                     _open=open_response)
    assert len(calls) == 2


def test_response_closes_after_early_validation_failure(tmp_path):
    m = subject()
    open_response, _, responses = opener(response_options={'status': 401})
    with pytest.raises(m.SourceTransportError):
        m.fetch_pack('libplacebo', tmp_path.resolve() / 'new', _open=open_response)
    assert responses[0].closed


def test_request_deadline_checked_after_read(tmp_path, monkeypatch):
    m = subject()
    clock = [0.0]
    monkeypatch.setattr(m.time, 'monotonic', lambda: clock[0])
    calls = []

    class SlowResponse(Response):
        def read(self, n=-1):
            clock[0] += 21
            return super().read(n)

    def open_response(request, *, timeout):
        calls.append(request)
        return SlowResponse(bodies()[0], request.full_url,
                            'application/x-git-upload-pack-advertisement')

    with pytest.raises(m.SourceTransportError):
        m.fetch_pack('libplacebo', tmp_path.resolve() / 'new', _open=open_response)
    assert len(calls) == 1


def test_batch_deadline_checked_between_requests(tmp_path, monkeypatch):
    m = subject()
    clock = [0.0]
    monkeypatch.setattr(m.time, 'monotonic', lambda: clock[0])
    calls = []

    class SlowCloseResponse(Response):
        def close(self):
            clock[0] += 10
            super().close()

    def open_response(request, *, timeout):
        calls.append(request)
        return SlowCloseResponse(bodies()[0], request.full_url,
                                 'application/x-git-upload-pack-advertisement')

    with pytest.raises(m.SourceTransportError):
        m.fetch_pack('libplacebo', tmp_path.resolve() / 'new',
                     limits=m.HttpLimits(batch_seconds=5), _open=open_response)
    assert len(calls) == 1


def test_production_opener_has_tls_and_no_proxy_auth_cookie_or_redirect(tmp_path, monkeypatch):
    m = subject()
    calls = []
    monkeypatch.setenv('HTTPS_PROXY', 'http://user:secret@127.0.0.1:9')

    def offline_open(director, request, data=None, timeout=None):
        calls.append(request)
        for handler in director.handlers:
            assert not isinstance(handler, (urllib.request.HTTPBasicAuthHandler,
                                            urllib.request.ProxyBasicAuthHandler,
                                            urllib.request.HTTPCookieProcessor))
            if isinstance(handler, urllib.request.ProxyHandler):
                assert handler.proxies == {}
            if isinstance(handler, urllib.request.HTTPSHandler):
                assert handler._context.verify_mode == ssl.CERT_REQUIRED
                assert handler._context.check_hostname
            if isinstance(handler, urllib.request.HTTPRedirectHandler):
                with pytest.raises(Exception):
                    handler.redirect_request(request, None, 302, 'redirect', {},
                                             'https://other.invalid/')
        first = len(calls) == 1
        return Response(bodies()[0 if first else 1], request.full_url,
                        'application/x-git-upload-pack-' + ('advertisement' if first else 'result'))

    monkeypatch.setattr(urllib.request.OpenerDirector, 'open', offline_open)
    result = m.fetch_pack('libplacebo', tmp_path.resolve() / 'new')
    assert len(calls) == 2 and result['transport_injected_for_tests'] is False


def test_second_fixed_source_has_its_own_url_and_pin(tmp_path):
    m = subject()
    target = '74d8a6cb930c68ef617b202c3ff3c59d919e086b'
    ad, wire, pack = bodies()
    ad = ad.replace(b'275f7dea27924cdd34ab2e6c20e448b6bc4fab65', target.encode())
    wire = wire.replace(b'275f7dea27924cdd34ab2e6c20e448b6bc4fab65', target.encode())
    calls = []

    def open_response(request, *, timeout):
        calls.append(request)
        assert request.full_url.startswith('https://github.com/KhronosGroup/Vulkan-Headers.git/')
        first = len(calls) == 1
        if not first:
            assert target.encode() in request.data
        return Response(ad if first else wire, request.full_url,
                        'application/x-git-upload-pack-' + ('advertisement' if first else 'result'))

    result = m.fetch_pack('vulkan_headers', tmp_path.resolve() / 'new', _open=open_response)
    assert len(calls) == 2 and result['commit_oid'] == target
    assert (tmp_path / 'new/source.pack').read_bytes() == pack


def test_large_integer_seconds_rejected_as_transport_error(tmp_path):
    m = subject()
    open_response, calls, _ = opener()
    with pytest.raises(m.SourceTransportError):
        m.fetch_pack('libplacebo', tmp_path.resolve() / 'new',
                     limits=m.HttpLimits(request_seconds=10**1000), _open=open_response)
    assert calls == []
