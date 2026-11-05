"""Offline regressions for the independent raw HTTP / inode / cleanup review.

The production opener, handlers and HTTPResponse run unchanged; only TLS
connection establishment is replaced with a memory socket. No network exists.
"""
import http.client
import io
import os
import stat
import urllib.error
import urllib.request

import pytest

from test_av_source_bounded_fetch import bodies, subject


class TrackedStream(io.BytesIO):
    def __init__(self, raw):
        super().__init__(raw)
        self.consumed = 0
        self.requests = []

    def read(self, amount=-1):
        assert amount >= 0
        self.requests.append(amount)
        result = super().read(amount)
        self.consumed += len(result)
        return result

    def readline(self, amount=-1):
        assert amount >= 0
        self.requests.append(amount)
        result = super().readline(amount)
        self.consumed += len(result)
        return result


class MemorySocket:
    def __init__(self, raw):
        self.stream = TrackedStream(raw)
        self.closed = False

    def makefile(self, mode):
        assert mode == 'rb'
        return self.stream

    def sendall(self, data):
        pass

    def close(self):
        self.closed = True


def raw_response(body, kind, *, extra=b'', trailers=b'', extensions=False,
                 status=b'200 OK', transfer=b'chunked'):
    headers = (b'HTTP/1.1 ' + status + b'\r\nContent-Type: application/x-git-upload-pack-'
               + kind + b'\r\nTransfer-Encoding: ' + transfer + b'\r\n' + extra + b'\r\n')
    if extensions:
        chunks = b''.join(b'1;p=' + b'x' * 65000 + b'\r\n' + bytes([x]) + b'\r\n'
                          for x in body[:65])
        rest = body[65:]
        chunks += (f'{len(rest):x}'.encode() + b'\r\n' + rest + b'\r\n') if rest else b''
    else:
        chunks = f'{len(body):x}'.encode() + b'\r\n' + body + b'\r\n'
    return headers + chunks + b'0\r\n' + trailers + b'\r\n'


def production_memory(monkeypatch, raws):
    sockets = []

    def connect(connection):
        assert connection.host == 'github.com'
        sock = MemorySocket(raws[len(sockets)])
        sockets.append(sock)
        connection.sock = sock

    monkeypatch.setattr(http.client.HTTPSConnection, 'connect', connect)
    return sockets


def success_raws(**first_options):
    ad, wire, _ = bodies()
    return [raw_response(ad, b'advertisement', **first_options),
            raw_response(wire, b'result')]


@pytest.mark.parametrize('location', ['headers', 'trailers', 'chunk_extensions'])
def test_production_raw_metadata_rejects_aggregate_over_4mib(tmp_path, monkeypatch, location):
    m = subject()
    padding = (b'X-Padding: ' + b'x' * 65000 + b'\r\n') * 65
    options = {'extra': padding} if location == 'headers' else (
        {'trailers': padding} if location == 'trailers' else {'extensions': True})
    sockets = production_memory(monkeypatch, success_raws(**options))
    with pytest.raises(m.SourceTransportError):
        m.fetch_pack('libplacebo', tmp_path.resolve() / 'new')
    assert len(sockets) == 1
    assert sockets[0].stream.closed


def test_advertisement_and_http_metadata_share_one_cap(tmp_path, monkeypatch):
    m = subject()
    ad = bodies()[0]
    raw = success_raws()
    first_headers = raw[0].split(b'\r\n\r\n')[0] + b'\r\n\r\n'
    production_memory(monkeypatch, raw)
    with pytest.raises(m.SourceTransportError):
        m.fetch_pack('libplacebo', tmp_path.resolve() / 'new',
                     limits=m.HttpLimits(metadata_bytes=len(first_headers) + len(ad) - 1))


class NeverRead(io.BytesIO):
    def read(self, *args):
        raise AssertionError('error body must never be read')


@pytest.mark.parametrize('code', [401, 500])
def test_real_http_error_closes_fp_before_public_error(tmp_path, code):
    m = subject()
    fp = NeverRead(b'private upstream body')

    def open_error(request, *, timeout):
        raise urllib.error.HTTPError(request.full_url, code, 'private diagnostic', {}, fp)

    with pytest.raises(m.SourceTransportError, match='source request failed') as caught:
        m.fetch_pack('libplacebo', tmp_path.resolve() / 'new', _open=open_error)
    assert caught.value is not None
    assert fp.closed


@pytest.mark.parametrize('code,location', [(301, 'https://other.invalid/'),
                                         (302, 'https://other.invalid/'),
                                         (303, None), (307, 'file:///private'),
                                         (308, 'https://other.invalid/')])
def test_actual_redirect_handler_closes_without_reading(tmp_path, code, location):
    m = subject()
    fp = NeverRead(b'private redirect body')

    def open_redirect(request, *, timeout):
        handler = m._NoRedirect()
        headers = {} if location is None else {'location': location}
        return getattr(handler, 'http_error_' + str(code))(request, fp, code, 'found', headers)

    with pytest.raises(m.SourceTransportError) as caught:
        m.fetch_pack('libplacebo', tmp_path.resolve() / 'new', _open=open_redirect)
    assert caught.value is not None
    assert fp.closed


def test_mkdir_open_replacement_is_not_chmodded_or_written(tmp_path, monkeypatch):
    m = subject()
    parent = tmp_path.resolve()
    old = parent / 'old'
    old.mkdir(mode=0o755)
    old.chmod(0o755)
    (old / 'sentinel').write_bytes(b'keep')
    old_identity = old.stat().st_ino
    real_mkdir = os.mkdir

    def swapped_mkdir(path, mode=0o777, *, dir_fd=None):
        real_mkdir(path, mode, dir_fd=dir_fd)
        if path == 'new' and dir_fd is not None:
            os.rename('new', 'created-aside', src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            os.rename('old', 'new', src_dir_fd=dir_fd, dst_dir_fd=dir_fd)

    monkeypatch.setattr(m.os, 'mkdir', swapped_mkdir)
    with pytest.raises(m.SourceTransportError):
        fd, _ = m._new_directory(parent / 'new')
        os.close(fd)
    assert (parent / 'new').stat().st_ino == old_identity
    assert stat.S_IMODE((parent / 'new').stat().st_mode) == 0o755
    assert [p.name for p in (parent / 'new').iterdir()] == ['sentinel']


def test_stat_open_inode_replacement_rejected_without_chmod(tmp_path, monkeypatch):
    m = subject()
    parent = tmp_path.resolve()
    old = parent / 'old'
    old.mkdir(mode=0o700)
    (old / 'sentinel').write_bytes(b'keep')
    real_open = os.open

    def swapped_open(path, flags, *args, **kwargs):
        if path == 'new' and 'dir_fd' in kwargs:
            fd = kwargs['dir_fd']
            os.rename('new', 'created-aside', src_dir_fd=fd, dst_dir_fd=fd)
            os.rename('old', 'new', src_dir_fd=fd, dst_dir_fd=fd)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(m.os, 'open', swapped_open)
    with pytest.raises(m.SourceTransportError):
        fd, _ = m._new_directory(parent / 'new')
        os.close(fd)
    assert [p.name for p in (parent / 'new').iterdir()] == ['sentinel']


def test_success_raw_accounting_does_not_double_charge_advertisement(tmp_path, monkeypatch):
    m = subject()
    raws = success_raws(extra=b'X-Test: yes\r\n', trailers=b'X-End: done\r\n')
    sockets = production_memory(monkeypatch, raws)
    result = m.fetch_pack('libplacebo', tmp_path.resolve() / 'new',
                          limits=m.HttpLimits(total_input_bytes=sum(map(len, raws)) + 1))
    ad, wire, pack = bodies()
    assert len(sockets) == 2
    assert result['total_input_bytes'] == sum(map(len, raws))
    assert result['response_body_bytes'] == len(ad) + len(wire)
    assert result['metadata_bytes'] == sum(map(len, raws)) - len(wire)
    assert result['http_raw_input_metered'] is True
    assert result['transport_injected_for_tests'] is False
    assert all(sock.stream.closed for sock in sockets)
    assert (tmp_path / 'new' / 'source.pack').read_bytes() == pack
    assert not any(result[key] for key in ('source_identity_verified', 'build_inputs_adopted',
                                           'os_resource_limits_verified'))


@pytest.mark.parametrize('limits', [{'metadata_bytes': 8}, {'total_input_bytes': 8}])
def test_status_read_is_capped_before_parsing(tmp_path, monkeypatch, limits):
    m = subject()
    sockets = production_memory(monkeypatch, success_raws())
    with pytest.raises(m.SourceTransportError) as caught:
        m.fetch_pack('libplacebo', tmp_path.resolve() / 'new', limits=m.HttpLimits(**limits))
    assert caught.value is not None
    assert len(sockets) == 1
    assert sockets[0].stream.consumed <= 8
    assert max(sockets[0].stream.requests) <= 8
    assert sockets[0].stream.closed


def test_total_input_header_budget_works_before_opener_returns(tmp_path, monkeypatch):
    m = subject()
    raw = success_raws(extra=b'X-Padding: ' + b'x' * 1000 + b'\r\n')
    sockets = production_memory(monkeypatch, raw)
    with pytest.raises(m.SourceTransportError):
        m.fetch_pack('libplacebo', tmp_path.resolve() / 'new',
                     limits=m.HttpLimits(total_input_bytes=160))
    assert len(sockets) == 1
    assert sockets[0].stream.consumed <= 160
    assert sockets[0].stream.closed
    assert (tmp_path / 'new' / 'advertisement.bin').read_bytes() == b''


def test_metadata_is_cumulative_across_both_http_responses(tmp_path, monkeypatch):
    m = subject()
    raws = success_raws()
    second_headers = raws[1].split(b'\r\n\r\n')[0] + b'\r\n\r\n'
    sockets = production_memory(monkeypatch, raws)
    cap = len(raws[0]) + len(second_headers) - 1
    with pytest.raises(m.SourceTransportError):
        m.fetch_pack('libplacebo', tmp_path.resolve() / 'new',
                     limits=m.HttpLimits(metadata_bytes=cap))
    assert len(sockets) == 2
    assert sum(sock.stream.consumed for sock in sockets) <= cap
    assert sockets[1].stream.closed


@pytest.mark.parametrize('transfer,extra', [(b'gzip', b''), (b'gzip, chunked', b''),
                                          (b'chunked', b'Content-Length: 5\r\n'),
                                          (b'chunked', b'Transfer-Encoding: chunked\r\n')])
def test_unsupported_or_ambiguous_transfer_encoding_rejected_before_payload(tmp_path, monkeypatch,
                                                                          transfer, extra):
    m = subject()
    raws = success_raws(transfer=transfer, extra=extra)
    sockets = production_memory(monkeypatch, raws)
    with pytest.raises(m.SourceTransportError):
        m.fetch_pack('libplacebo', tmp_path.resolve() / 'new')
    assert len(sockets) == 1
    assert sockets[0].stream.consumed == len(raws[0].split(b'\r\n\r\n')[0]) + 4
    assert sockets[0].stream.closed


@pytest.mark.parametrize('status', [b'401 denied', b'500 failure', b'302 found'])
def test_production_urllib_http_error_or_redirect_closes_before_body(tmp_path, monkeypatch, status):
    m = subject()
    raws = success_raws(status=status, extra=b'Location: https://other.invalid/\r\n')
    sockets = production_memory(monkeypatch, raws)
    with pytest.raises(m.SourceTransportError) as caught:
        m.fetch_pack('libplacebo', tmp_path.resolve() / 'new')
    assert caught.value is not None
    assert len(sockets) == 1
    assert sockets[0].stream.closed
    assert sockets[0].stream.consumed == len(raws[0].split(b'\r\n\r\n')[0]) + 4
