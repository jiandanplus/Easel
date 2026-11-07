import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from easel import updater

NODE = shutil.which('node')
needs_node = pytest.mark.skipif(NODE is None, reason='Ed25519 verification uses Node crypto')
needs_ditto = pytest.mark.skipif(sys.platform != 'darwin', reason='macOS packages are unpacked with ditto')


def keypair():
    script = ("const c=require('crypto');const k=c.generateKeyPairSync('ed25519');"
              "process.stdout.write(JSON.stringify({pub:k.publicKey.export({type:'spki',format:'pem'}),"
              "priv:k.privateKey.export({type:'pkcs8',format:'pem'})}))")
    keys = json.loads(subprocess.run([NODE, '-e', script], capture_output=True, text=True, check=True).stdout)
    return keys['pub'], keys['priv']


def manifest(version=38, **override):
    value = {'format': updater.FORMAT, 'version': version,
             'url': f'https://example.invalid/Easel-v{version}.zip',
             'sha256': 'a' * 64, 'bytes': 10, 'notes': '修复若干问题', 'minimum_macos': '15.0'}
    value.update(override)
    return json.dumps(value, ensure_ascii=False).encode()


def make_app(base, version, *, provenance_version=True):
    resource = base/'Easel/Easel.app/Contents/Resources'
    (resource/'easel').mkdir(parents=True)
    (resource/'easel/marker.py').write_text(f'VERSION = {version}\n')
    inventory = {'easel/marker.py': hashlib.sha256((resource/'easel/marker.py').read_bytes()).hexdigest()}
    (resource/'build-inventory.json').write_text(json.dumps(inventory))
    provenance = {'engine': 'pi'}
    if provenance_version:
        provenance['version'] = version
    (resource/'build-provenance.json').write_text(json.dumps(provenance))
    return resource


def managed(tmp_path, version=37):
    root = tmp_path/'Applications/Easel'
    resource = make_app(root/f'v{version}', version)
    (root/'current').symlink_to(f'v{version}')
    return root, resource


@needs_node
def test_signature_round_trip_and_tamper_rejected():
    pub, priv = keypair()
    data = manifest()
    signature = updater.sign(data, priv, NODE)
    assert updater.verify(data, signature, pub, NODE)
    assert not updater.verify(data.replace(b'38', b'39'), signature, pub, NODE)
    other_pub, _ = keypair()
    assert not updater.verify(data, signature, other_pub, NODE)
    assert not updater.verify(data, 'not-base64!!', pub, NODE)


@needs_node
@pytest.mark.parametrize('override', [
    {'format': 'other'}, {'version': 0}, {'version': '38'}, {'url': 'http://example.invalid/x.zip'},
    {'sha256': 'xyz'}, {'bytes': 0}, {'notes': 'x' * 5000},
])
def test_manifest_fields_are_validated_after_signature(override):
    pub, priv = keypair()
    data = manifest(**override)
    with pytest.raises(updater.UpdateError):
        updater.parse_manifest(data, updater.sign(data, priv, NODE), public_key=pub, node=NODE)


@needs_node
def test_manifest_with_bad_signature_is_rejected_before_parsing():
    pub, _ = keypair()
    _, other_priv = keypair()
    data = manifest()
    with pytest.raises(updater.UpdateError, match='签名'):
        updater.parse_manifest(data, updater.sign(data, other_priv, NODE), public_key=pub, node=NODE)


def test_managed_layout_requires_current_link_to_running_version(tmp_path):
    root, resource = managed(tmp_path)
    layout = updater.managed_layout(resource)
    assert (layout.root, layout.version) == (root, 37)
    (root/'current').unlink(); (root/'current').symlink_to('v36')
    assert updater.managed_layout(resource) is None
    loose = make_app(tmp_path/'Downloads', 37)
    assert updater.managed_layout(loose) is None


def test_build_version_prefers_provenance_and_falls_back_to_directory(tmp_path):
    assert updater.build_version(make_app(tmp_path/'a/v40', 41)) == 41
    assert updater.build_version(make_app(tmp_path/'b/v37', 37, provenance_version=False)) == 37
    assert updater.build_version(make_app(tmp_path/'c/other', 1, provenance_version=False)) is None


@needs_node
def test_check_reports_only_newer_signed_versions(tmp_path):
    pub, priv = keypair()
    _, resource = managed(tmp_path)
    def serve(version):
        data = manifest(version)
        payloads = {'https://u/m.json': data, 'https://u/m.json.sig': updater.sign(data, priv, NODE).encode()}
        return lambda url, limit: payloads[url]
    assert updater.check(resource, 'https://u/m.json', fetch=serve(37), public_key=pub, node=NODE) is None
    found = updater.check(resource, 'https://u/m.json', fetch=serve(38), public_key=pub, node=NODE)
    assert found['version'] == 38 and found['notes'] == '修复若干问题'


@needs_ditto
def test_install_verifies_hash_and_inventory_before_publishing_version(tmp_path):
    root, resource = managed(tmp_path)
    build = tmp_path/'build'
    make_app(build, 38)
    archive = tmp_path/'Easel-v38.zip'
    subprocess.run(['ditto', '-c', '-k', '--sequesterRsrc', str(build), str(archive)], check=True)
    data = archive.read_bytes()
    good = json.loads(manifest(38, sha256=hashlib.sha256(data).hexdigest(), bytes=len(data)))
    layout = updater.managed_layout(resource)

    with pytest.raises(updater.UpdateError):
        updater.install(layout, {**good, 'sha256': 'b' * 64}, download=lambda url, dest, progress: dest.write_bytes(data))
    assert not (root/'v38').exists()

    installed = updater.install(layout, good, download=lambda url, dest, progress: dest.write_bytes(data))
    assert installed == root/'v38/Easel/Easel.app/Contents/Resources'
    assert updater.build_version(installed) == 38
    assert os.readlink(root/'current') == 'v37'
    assert not list(root.glob('.staging-*')) and not list((root/'downloads').glob('*.part'))


@needs_ditto
def test_install_rejects_package_whose_inventory_does_not_match(tmp_path):
    root, resource = managed(tmp_path)
    build = tmp_path/'build'
    tampered = make_app(build, 38)
    (tampered/'easel/marker.py').write_text('VERSION = "tampered"\n')
    archive = tmp_path/'bad.zip'
    subprocess.run(['ditto', '-c', '-k', str(build), str(archive)], check=True)
    data = archive.read_bytes()
    bad = json.loads(manifest(38, sha256=hashlib.sha256(data).hexdigest(), bytes=len(data)))
    with pytest.raises(updater.UpdateError):
        updater.install(updater.managed_layout(resource), bad, download=lambda url, dest, progress: dest.write_bytes(data))
    assert not (root/'v38').exists()


class FakeInstances:
    """Records the switch's stop/start calls; `healthy` decides whether a version comes up."""
    def __init__(self, healthy):
        self.healthy = healthy
        self.calls = []

    def stop(self, workspace):
        self.calls.append(('stop',))

    def start(self, resource, workspace, port):
        version = updater.build_version(resource)
        self.calls.append(('start', version))
        return version

    def ready(self, handle, workspace, timeout):
        return self.healthy.get(handle, False)

    def abandon(self, handle):
        self.calls.append(('abandon', handle))


def test_switch_flips_current_only_after_new_version_is_ready(tmp_path):
    root, resource = managed(tmp_path)
    make_app(root/'v38', 38)
    instances = FakeInstances({38: True})
    result = updater.switch(root, 37, 38, tmp_path/'workspace', 17868, instances=instances)
    assert result['status'] == 'updated'
    assert os.readlink(root/'current') == 'v38'
    assert instances.calls == [('stop',), ('start', 38)]
    assert json.loads((root/'update-result.json').read_text())['status'] == 'updated'


def test_switch_rolls_back_when_new_version_fails_to_start(tmp_path):
    root, resource = managed(tmp_path)
    make_app(root/'v38', 38)
    instances = FakeInstances({38: False, 37: True})
    result = updater.switch(root, 37, 38, tmp_path/'workspace', 17868, instances=instances)
    assert result['status'] == 'rolled_back'
    assert os.readlink(root/'current') == 'v37'
    assert instances.calls == [('stop',), ('start', 38), ('abandon', 38), ('start', 37)]


@needs_node
def test_embedded_public_key_is_a_valid_ed25519_key():
    _, priv = keypair()
    data = manifest()
    # A valid key parses and rejects a foreign signature; a broken key would fail to parse.
    assert updater.verify(data, updater.sign(data, priv, NODE), updater.PUBLIC_KEY, NODE) is False
    script = "const c=require('crypto');process.stdout.write(c.createPublicKey(require('fs').readFileSync(0,'utf8')).asymmetricKeyType)"
    kind = subprocess.run([NODE, '-e', script], input=updater.PUBLIC_KEY, capture_output=True, text=True, check=True).stdout
    assert kind == 'ed25519'


def test_switch_refuses_unknown_target(tmp_path):
    root, _ = managed(tmp_path)
    with pytest.raises(updater.UpdateError):
        updater.switch(root, 37, 39, tmp_path/'workspace', 17868, instances=FakeInstances({}))
    assert os.readlink(root/'current') == 'v37'


@needs_node
def test_publish_tool_signs_a_manifest_clients_accept_and_refuses_foreign_key(tmp_path):
    from scripts.packaging import publish_update
    pub, priv = keypair()
    key = tmp_path/'key.pem'; key.write_text(priv)
    artifact = tmp_path/'artifact.json'
    artifact.write_text(json.dumps({'version': 38, 'sha256': 'c' * 64, 'archive_bytes': 123, 'minimum_macos': '15.0'}))
    path = publish_update.publish(artifact, 'https://example.invalid/v38.zip', '修复', key, tmp_path/'out', node=NODE, public_key=pub)
    data = path.read_bytes()
    signature = (tmp_path/'out/easel-update.json.sig').read_text()
    assert updater.parse_manifest(data, signature, public_key=pub, node=NODE)['version'] == 38
    other_pub, _ = keypair()
    with pytest.raises(updater.UpdateError):
        publish_update.publish(artifact, 'https://example.invalid/v38.zip', '', key, tmp_path/'out2', node=NODE, public_key=other_pub)
    artifact.write_text(json.dumps({'sha256': 'c' * 64, 'archive_bytes': 123}))
    with pytest.raises(ValueError, match='version'):
        publish_update.publish(artifact, 'https://example.invalid/v38.zip', '', key, tmp_path/'out3', node=NODE, public_key=pub)


@needs_node
def test_local_file_source_is_allowed_but_plain_http_is_not(tmp_path):
    pub, priv = keypair()
    data = manifest(url='file:///Users/x/Easel-v38.zip')
    assert updater.parse_manifest(data, updater.sign(data, priv, NODE), public_key=pub, node=NODE)['version'] == 38
    with pytest.raises(updater.UpdateError):
        updater.fetch('http://example.invalid/m.json', 10)
    local = tmp_path/'m.json'; local.write_bytes(b'{}')
    assert updater.fetch(local.as_uri(), 10) == b'{}'
