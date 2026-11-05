"""Synthetic wire-protocol checks; never contacts an upstream repository."""
import hashlib
import importlib.util
import io
from pathlib import Path
import struct
import subprocess
import sys
import zlib

import pytest


MODULE = Path(__file__).parents[1] / 'scripts/av_sources/smart_http.py'
OID = '1' * 40
OTHER = '2' * 40


def subject():
    assert MODULE.is_file(), 'bounded smart HTTP protocol implementation is missing'
    name = '_easel_av_source_smart_http'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, MODULE)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def packet(body):
    return f'{len(body) + 4:04x}'.encode() + body


def advertisement(oid=OID):
    return (packet(b'# service=git-upload-pack\n') + b'0000'
            + packet((oid + ' HEAD\0side-band-64k ofs-delta shallow agent=git/test\n').encode())
            + packet((oid + ' refs/tags/v-test^{}\n').encode()) + b'0000')


def pack():
    content = b'abc'
    data = b'PACK' + struct.pack('>II', 2, 1) + bytes([0x30 | len(content)]) + zlib.compress(content)
    return data + hashlib.sha1(data).digest()


def response(data=None, progress=b'Counting objects\n'):
    value = pack() if data is None else data
    return (packet(('shallow ' + OID + '\n').encode()) + b'0000' + packet(b'NAK\n')
            + packet(b'\x02' + progress) + packet(b'\x01' + value[:9])
            + packet(b'\x01' + value[9:]) + b'0000')


def decode(m, body, **kwargs):
    sink = io.BytesIO()
    receipt = m.decode_pack_response(io.BytesIO(body), sink, target=OID, **kwargs)
    return sink.getvalue(), receipt


def test_packet_line_and_exact_shallow_request():
    m = subject()
    assert m.pkt_line(b'done\n') == b'0009done\n'
    ad = m.parse_advertisement(advertisement(), target=OID)
    assert ad['target_advertised'] is True
    expected = (packet(('want ' + OID + ' side-band-64k ofs-delta\n').encode())
                + packet(b'deepen 1\n') + b'0000' + packet(b'done\n'))
    assert m.build_fetch_request(OID, ad) == expected
    assert b'filter' not in expected and b'have' not in expected


@pytest.mark.parametrize('bad', [b'', b'0001', b'zzzzabcd', b'0003', b'0008abc',
                                b'0000', advertisement() + b'extra'])
def test_advertisement_rejects_invalid_framing(bad):
    m = subject()
    with pytest.raises(m.SourceTransportError):
        m.parse_advertisement(bad, target=OID)


def test_advertisement_requires_target_capabilities_and_unique_refs():
    m = subject()
    for bad in [advertisement(OTHER), advertisement().replace(b'shallow', b'unknown'),
                advertisement().replace(b'side-band-64k', b'no-band-64kxx')]:
        with pytest.raises(m.SourceTransportError):
            m.parse_advertisement(bad, target=OID)
    duplicate = advertisement()[:-4] + packet((OTHER + ' HEAD\n').encode()) + b'0000'
    with pytest.raises(m.SourceTransportError):
        m.parse_advertisement(duplicate, target=OID)


def test_reflected_unknown_target_cannot_be_used_in_request():
    m = subject()
    ad = m.parse_advertisement(advertisement(), target=OID)
    with pytest.raises(m.SourceTransportError):
        m.build_fetch_request(OTHER, ad)


def test_sideband_response_streams_original_pack_and_checks_container():
    m = subject()
    body = response()
    saved, receipt = decode(m, body)
    assert saved == pack()
    assert receipt['response_body_bytes'] == len(body)
    assert receipt['pack_bytes'] == len(saved)
    assert receipt['pack_sha256'] == hashlib.sha256(saved).hexdigest()
    assert receipt['pack_checksum_sha1_verified'] is True
    assert receipt['objects_declared'] == 1
    assert receipt['objects_verified'] is False
    assert receipt['progress_bytes'] == len(b'Counting objects\n')
    assert receipt['shallow'] == [OID]


def test_root_commit_can_have_empty_shallow_update():
    m = subject()
    body = b'0000' + packet(b'NAK\n') + packet(b'\x01' + pack()) + b'0000'
    saved, receipt = decode(m, body)
    assert saved == pack() and receipt['shallow'] == []


@pytest.mark.parametrize('change', ['checksum', 'signature', 'version', 'zero_objects'])
def test_pack_header_and_checksum_fail_closed(change):
    m = subject()
    value = bytearray(pack())
    if change == 'checksum':
        value[-1] ^= 1
    else:
        if change == 'signature':
            value[:4] = b'FAKE'
        elif change == 'version':
            value[4:8] = struct.pack('>I', 99)
        else:
            value[8:12] = struct.pack('>I', 0)
        value[-20:] = hashlib.sha1(value[:-20]).digest()
    with pytest.raises(m.SourceTransportError):
        decode(m, response(bytes(value)))


@pytest.mark.parametrize('bad', [
    b'0000' + packet(b'ERR private server diagnostic\n'),
    b'0000' + packet(b'ACK ' + OID.encode() + b'\n'),
    b'0000' + packet(b'NAK\n') + packet(b'\x03server fatal') + b'0000',
    b'0000' + packet(b'NAK\n') + packet(b'\x04unknown channel') + b'0000',
    b'0000' + packet(b'NAK\n') + packet(b'\x01') + b'0000',
    response()[:-1], response() + b'extra',
    response().replace(OID.encode(), OTHER.encode(), 1),
    response().replace(b'shallow ', b'unshallow ', 1),
])
def test_response_rejects_protocol_errors_and_truncation(bad):
    m = subject()
    with pytest.raises(m.SourceTransportError) as caught:
        decode(m, bad)
    assert 'private server diagnostic' not in str(caught.value)


def test_all_input_and_output_limits_apply_while_reading():
    m = subject()
    with pytest.raises(m.SourceTransportError):
        decode(m, response(), max_response_bytes=len(response()) - 1)
    with pytest.raises(m.SourceTransportError):
        decode(m, response(), max_pack_bytes=len(pack()) - 1)
    with pytest.raises(m.SourceTransportError):
        decode(m, response(), max_packets=2)
    with pytest.raises(m.SourceTransportError):
        decode(m, response(), max_objects=0)
    with pytest.raises(m.SourceTransportError):
        m.parse_advertisement(advertisement(), target=OID, max_bytes=16)


def test_declared_oversize_packet_does_not_read_its_body():
    m = subject()
    source = io.BytesIO(b'0000' + packet(b'NAK\n') + b'fff0' + b'X' * 100)
    with pytest.raises(m.SourceTransportError):
        m.decode_pack_response(source, io.BytesIO(), target=OID, max_response_bytes=64)
    assert source.tell() == 16


@pytest.mark.parametrize('oid', ['A' * 40, 'x' * 40, '1' * 39, '../x', OID + '\n'])
def test_target_oid_is_canonical_and_cannot_inject_protocol(oid):
    m = subject()
    with pytest.raises(m.SourceTransportError):
        m.parse_advertisement(advertisement(), target=oid)


@pytest.mark.parametrize('with_parent', [False, True])
def test_real_git_stateless_rpc_pack_and_complete_tree(tmp_path, with_parent):
    """Actual local upload-pack/index-pack, no remote or user Git config."""
    m = subject()
    empty = tmp_path / 'empty-template'
    empty.mkdir(mode=0o700)
    source = tmp_path / 'source.git'
    target_repo = tmp_path / 'received.git'
    env = {
        'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C',
        'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_SYSTEM': '/dev/null',
        'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_TERMINAL_PROMPT': '0',
        'GIT_ATTR_NOSYSTEM': '1', 'GIT_NO_REPLACE_OBJECTS': '1',
        'GIT_AUTHOR_NAME': 'AV Source Fixture',
        'GIT_AUTHOR_EMAIL': 'fixture@invalid.invalid',
        'GIT_COMMITTER_NAME': 'AV Source Fixture',
        'GIT_COMMITTER_EMAIL': 'fixture@invalid.invalid',
        'GIT_AUTHOR_DATE': '2026-10-28T12:00:00+0800',
        'GIT_COMMITTER_DATE': '2026-10-28T03:00:00-0100',
    }

    def git(args, data=None):
        completed = subprocess.run(['/usr/bin/git', *args], input=data,
                                   capture_output=True, env=env, timeout=10)
        assert completed.returncode == 0, completed.stderr.decode('utf8', 'replace')
        assert len(completed.stdout) < 1024 * 1024
        return completed.stdout

    for path in [source, target_repo]:
        git(['init', '--bare', '--initial-branch=main', '--template=' + str(empty), str(path)])
    content = b'AV_SOURCE_SYNTHETIC\n'
    blob = git(['--git-dir=' + str(source), 'hash-object', '-w', '--stdin'], content).strip()
    tree = git(['--git-dir=' + str(source), 'mktree'], b'100644 blob ' + blob + b'\tmarker.txt\n').strip()
    commit_args = ['--git-dir=' + str(source), 'commit-tree', tree.decode()]
    parent = git(commit_args, b'parent synthetic commit\n').strip()
    target = git(commit_args + ['-p', parent.decode()], b'target synthetic commit\n').strip() if with_parent else parent
    oid = target.decode()
    git(['--git-dir=' + str(source), 'update-ref', 'refs/heads/main', oid])
    refs = git(['upload-pack', '--stateless-rpc', '--advertise-refs', str(source)])
    ad = m.parse_advertisement(packet(b'# service=git-upload-pack\n') + b'0000' + refs, target=oid)
    wire = git(['upload-pack', '--stateless-rpc', str(source)], m.build_fetch_request(oid, ad))
    sink = io.BytesIO()
    receipt = m.decode_pack_response(io.BytesIO(wire), sink, target=oid)
    assert receipt['objects_declared'] == 3
    # Native upload-pack marks the requested depth-1 tip shallow even when
    # that synthetic tip is already a root commit.
    assert receipt['shallow'] == [oid]
    if receipt['shallow']:
        (target_repo / 'shallow').write_text(oid + '\n')
    git(['--git-dir=' + str(target_repo), 'index-pack', '--stdin', '--strict', '--threads=1'], sink.getvalue())

    object_path = MODULE.with_name('git_objects.py')
    spec = importlib.util.spec_from_file_location('_av_native_git_objects', object_path)
    objects = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = objects
    spec.loader.exec_module(objects)

    def load_object(value):
        prefix = ['--git-dir=' + str(target_repo), 'cat-file']
        kind = git(prefix + ['-t', value]).decode().strip()
        size = int(git(prefix + ['-s', value]))
        assert size < 1024 * 1024
        body = git(prefix + [kind, value])
        assert len(body) == size
        return kind, body

    verified = objects.verify_commit_tree(oid, load_object)
    assert verified['commit_oid'] == oid
    assert verified['tree_oid'] == tree.decode()
    assert verified['counts']['content_bytes'] == len(content)
    assert verified['entries'] == [{
        'path': 'marker.txt', 'mode': '100644', 'kind': 'blob',
        'oid': blob.decode(), 'size': len(content),
        'sha256': hashlib.sha256(content).hexdigest(),
    }]
