"""In-app updates for the managed macOS layout.

Layout: <root>/v<N>/Easel/Easel.app/Contents/Resources, with <root>/current -> v<N>.
A release is described by a manifest signed with an offline Ed25519 key; the
package is accepted only if the signature, size, SHA-256 and the new bundle's
own file inventory all match. Switching stops the running instance through its
authenticated stop endpoint, points `current` at the new version and starts it;
if the new version does not come up, `current` is restored and the old version
is started again.
"""
from __future__ import annotations

import argparse
import base64
import dataclasses
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

from easel import portable

FORMAT = 'easel-update-v1'
DEFAULT_MANIFEST_URL = 'https://github.com/jiandanplus/Easel/releases/latest/download/easel-update.json'
# Offline release key; the private half never ships. Rotate by releasing a build with a new key.
PUBLIC_KEY = '''-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEApRjhbjns4vn4AjKEq41C0vWjiFcwNTr2Yd0diGWWPC0=
-----END PUBLIC KEY-----
'''
MANIFEST_LIMIT = 64 * 1024
PACKAGE_LIMIT = 512 * 1024 * 1024
READY_TIMEOUT = 90
# file:// serves local or LAN distribution; the manifest signature is required either way.
ALLOWED_SCHEMES = ('https://', 'file://')
LAUNCH = 'import sys; sys.path.insert(0, sys.argv.pop(1)); from easel.portable import main; raise SystemExit(main())'
SWITCH = 'import sys; sys.path.insert(0, sys.argv.pop(1)); from easel.updater import switch_main; raise SystemExit(switch_main())'

_NODE_SIGN = ("const c=require('crypto');const i=JSON.parse(require('fs').readFileSync(0,'utf8'));"
              "process.stdout.write(c.sign(null,Buffer.from(i.data,'base64'),i.key).toString('base64'))")
_NODE_VERIFY = ("const c=require('crypto');const i=JSON.parse(require('fs').readFileSync(0,'utf8'));"
                "process.stdout.write(c.verify(null,Buffer.from(i.data,'base64'),i.key,Buffer.from(i.sig,'base64'))?'ok':'no')")


class UpdateError(Exception):
    pass


@dataclasses.dataclass(frozen=True)
class Layout:
    root: Path
    version: int
    resource: Path


def node_path(resource: Path | None = None) -> str | None:
    if resource is not None and (resource/'runtime/pi/node/bin/node').is_file():
        return str(resource/'runtime/pi/node/bin/node')
    return shutil.which('node')


def _node(script: str, payload: dict, node: str) -> str:
    result = subprocess.run([node, '-e', script], input=json.dumps(payload), capture_output=True,
                            text=True, timeout=30, env={'PATH': '/usr/bin:/bin'})
    if result.returncode != 0:
        raise UpdateError('签名工具运行失败')
    return result.stdout


def sign(data: bytes, private_key: str, node: str) -> str:
    return _node(_NODE_SIGN, {'data': base64.b64encode(data).decode(), 'key': private_key}, node)


def verify(data: bytes, signature: str, public_key: str, node: str) -> bool:
    if not re.fullmatch(r'[A-Za-z0-9+/]{86}==', signature.strip()):
        return False
    try:
        return _node(_NODE_VERIFY, {'data': base64.b64encode(data).decode(), 'sig': signature.strip(),
                                    'key': public_key}, node) == 'ok'
    except (UpdateError, OSError, subprocess.SubprocessError):
        return False


def parse_manifest(data: bytes, signature: str, *, public_key: str = PUBLIC_KEY, node: str | None = None) -> dict:
    node = node or node_path()
    if node is None or not verify(data, signature, public_key, node):
        raise UpdateError('更新清单签名无效')
    try:
        value = json.loads(data)
    except ValueError:
        raise UpdateError('更新清单格式错误') from None
    if (not isinstance(value, dict) or value.get('format') != FORMAT
            or type(value.get('version')) is not int or value['version'] <= 0
            or not isinstance(value.get('url'), str) or not value['url'].startswith(ALLOWED_SCHEMES)
            or not isinstance(value.get('sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', value['sha256'])
            or type(value.get('bytes')) is not int or not 0 < value['bytes'] <= PACKAGE_LIMIT
            or not isinstance(value.get('notes', ''), str) or len(value.get('notes', '')) > 4000):
        raise UpdateError('更新清单字段无效')
    return value


def build_version(resource: Path) -> int | None:
    try:
        version = json.loads((resource/'build-provenance.json').read_text(encoding='utf-8')).get('version')
        if type(version) is int and version > 0:
            return version
    except (OSError, ValueError):
        pass
    # Builds before v38 carry no version field; the managed directory name records it.
    if not (resource/'build-inventory.json').is_file():
        return None
    match = re.fullmatch(r'v(\d+)', resource.parents[3].name) if len(resource.parents) > 3 else None
    return int(match[1]) if match else None


def managed_layout(resource: Path) -> Layout | None:
    resource = Path(resource)
    if len(resource.parents) < 5 or resource.parts[-4:] != ('Easel', 'Easel.app', 'Contents', 'Resources'):
        return None
    version_dir, root = resource.parents[3], resource.parents[4]
    version = build_version(resource)
    current = root/'current'
    if (version is None or version_dir.name != f'v{version}' or not current.is_symlink()
            or os.readlink(current) != version_dir.name):
        return None
    return Layout(root, version, resource)


def fetch(url: str, limit: int) -> bytes:
    if not url.startswith(ALLOWED_SCHEMES):
        raise UpdateError('更新地址必须使用 HTTPS')
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            data = response.read(limit + 1)
    except OSError:
        raise UpdateError('无法连接更新服务') from None
    if len(data) > limit:
        raise UpdateError('更新清单过大')
    return data


def check(resource: Path, url: str = DEFAULT_MANIFEST_URL, *, fetch=fetch,
          public_key: str = PUBLIC_KEY, node: str | None = None) -> dict | None:
    current = build_version(resource)
    data = fetch(url, MANIFEST_LIMIT)
    signature = fetch(url + '.sig', 1024).decode('ascii', 'replace')
    manifest = parse_manifest(data, signature, public_key=public_key, node=node or node_path(resource))
    return manifest if current is not None and manifest['version'] > current else None


def download(url: str, dest: Path, progress) -> None:
    if not url.startswith(ALLOWED_SCHEMES):
        raise UpdateError('下载地址必须使用 HTTPS')
    received = 0
    try:
        with urllib.request.urlopen(url, timeout=60) as response, dest.open('wb') as stream:
            while chunk := response.read(1024 * 1024):
                received += len(chunk)
                if received > PACKAGE_LIMIT:
                    raise UpdateError('更新包过大')
                stream.write(chunk)
                progress(received)
    except OSError:
        raise UpdateError('下载更新失败') from None


def install(layout: Layout, manifest: dict, *, download=download, progress=lambda received: None) -> Path:
    version = manifest['version']
    target = layout.root/f'v{version}'
    resource = target/'Easel/Easel.app/Contents/Resources'
    if target.exists():
        try:
            portable.verify_inventory(resource)
        except (OSError, ValueError):
            raise UpdateError('已存在同版本目录但校验失败') from None
        return resource
    downloads = layout.root/'downloads'
    downloads.mkdir(exist_ok=True)
    part = downloads/f'Easel-v{version}.zip.part'
    staging = layout.root/f'.staging-{version}-{uuid.uuid4().hex}'
    try:
        download(manifest['url'], part, progress)
        if part.stat().st_size != manifest['bytes'] or portable.digest(part) != manifest['sha256']:
            raise UpdateError('更新包校验失败')
        subprocess.run(['/usr/bin/ditto', '-x', '-k', str(part), str(staging)], check=True,
                       capture_output=True, timeout=600)
        staged = staging/'Easel/Easel.app/Contents/Resources'
        try:
            portable.verify_inventory(staged)
        except (OSError, ValueError):
            raise UpdateError('更新包内容校验失败') from None
        if build_version(staged) != version:
            raise UpdateError('更新包版本不符')
        os.rename(staging, target)
        return resource
    except subprocess.SubprocessError:
        raise UpdateError('解压更新包失败') from None
    finally:
        part.unlink(missing_ok=True)
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def set_current(root: Path, version: int) -> None:
    temporary = root/f'.current-{uuid.uuid4().hex}'
    temporary.symlink_to(f'v{version}')
    os.replace(temporary, root/'current')


class Instances:
    """Real launcher control; tests substitute a recorder with the same methods."""

    def __init__(self, log: Path):
        self.log = log

    def stop(self, workspace: Path) -> None:
        state_path = workspace/'.easel-portable/instance.json'
        state = json.loads(state_path.read_text())
        portable.instance_request(state, 'stop', workspace)
        deadline = time.monotonic() + 30
        while state_path.exists() and time.monotonic() < deadline:
            time.sleep(0.2)
        if state_path.exists():
            raise UpdateError('原实例未能停止')

    def start(self, resource: Path, workspace: Path, port: int):
        self.log.parent.mkdir(parents=True, exist_ok=True)
        with self.log.open('ab') as log:
            return subprocess.Popen(
                [str(resource/'python/bin/python3'), '-I', '-B', '-c', LAUNCH, str(resource),
                 '--workspace', str(workspace), '--port', str(port), '--no-browser'],
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)

    def ready(self, handle, workspace: Path, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if handle.poll() is not None:
                return False
            try:
                state = json.loads((workspace/'.easel-portable/instance.json').read_text())
                portable.instance_request(state, 'identity', workspace)
                return True
            except (OSError, ValueError, KeyError):
                time.sleep(0.5)
        return False

    def abandon(self, handle) -> None:
        handle.terminate()
        try:
            handle.wait(10)
        except subprocess.TimeoutExpired:
            handle.kill()


def switch(root: Path, old: int, new: int, workspace: Path, port: int, *, instances, timeout: float = READY_TIMEOUT) -> dict:
    new_resource = root/f'v{new}/Easel/Easel.app/Contents/Resources'
    old_resource = root/f'v{old}/Easel/Easel.app/Contents/Resources'
    if build_version(new_resource) != new or build_version(old_resource) != old:
        raise UpdateError('目标版本未安装')
    instances.stop(workspace)
    set_current(root, new)
    handle = instances.start(new_resource, workspace, port)
    if instances.ready(handle, workspace, timeout):
        result = {'status': 'updated', 'from': old, 'to': new}
    else:
        instances.abandon(handle)
        set_current(root, old)
        instances.start(old_resource, workspace, port)
        result = {'status': 'rolled_back', 'from': old, 'to': new}
    portable.atomic_json(root/'update-result.json', {**result, 'at': int(time.time())})
    return result


def start_switch(layout: Layout, new: int, workspace: Path, port: int):
    """Hand the switch to a detached process from the new version; it outlives this server."""
    resource = layout.root/f'v{new}/Easel/Easel.app/Contents/Resources'
    return subprocess.Popen(
        [str(resource/'python/bin/python3'), '-I', '-B', '-c', SWITCH, str(resource),
         '--root', str(layout.root), '--from', str(layout.version), '--to', str(new),
         '--workspace', str(workspace), '--port', str(port)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def switch_main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--from', dest='old', type=int, required=True)
    parser.add_argument('--to', dest='new', type=int, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args(argv)
    time.sleep(1)  # Let the HTTP response that started the switch reach the browser.
    log = Path.home()/f'Library/Logs/Easel/workbench-{args.port}.log'
    result = switch(args.root, args.old, args.new, args.workspace, args.port, instances=Instances(log))
    return 0 if result['status'] == 'updated' else 1
