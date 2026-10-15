"""Relocatable, loopback-only launcher. No host runtime discovery or installation."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=path.name+'.', suffix='.new')
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream, ensure_ascii=False, indent=2)
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def safe_state(workspace):
    state = workspace/'.easel-portable'
    if state.is_symlink() or not state.resolve().is_relative_to(workspace.resolve()):
        raise ValueError('工作区控制目录不能是符号链接')
    return state


def bounded(root, relative):
    path = root / relative
    if Path(relative).is_absolute() or '..' in Path(relative).parts or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('资源路径越界')
    return path


def verify_inventory(resource):
    for relative, expected in json.loads((resource/'build-inventory.json').read_text()).items():
        path = bounded(resource, relative)
        if not path.is_file() or digest(path) != expected:
            raise ValueError('程序或运行时校验失败：' + relative)


def seed_workspace(resource, workspace):
    state = safe_state(workspace)/'seed.json'
    try:
        previous = json.loads(state.read_text()) if state.exists() else {}
        if not isinstance(previous, dict): raise ValueError('invalid seed state')
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError('工作区种子记录损坏，请保留原文件并检查') from error
    current = {}
    seed = resource/'seed'
    for source in sorted(seed.rglob('*')):
        if not source.is_file(): continue
        relative = source.relative_to(seed).as_posix()
        destination = bounded(workspace, relative)
        if any(p.is_symlink() for p in [destination, *destination.parents] if p != workspace and p.is_relative_to(workspace)):
            continue
        checksum = digest(source)
        if not destination.exists() or (destination.is_file() and digest(destination) == previous.get(relative)):
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            current[relative] = checksum
        else:
            # Keep old baseline: a user edit must stay a user edit on later updates.
            if relative in previous: current[relative] = previous[relative]
    atomic_json(state, current)


def prepare_workspace(resource, workspace):
    resource, workspace = resource.resolve(), workspace.resolve()
    safe_state(workspace)
    if workspace.is_relative_to(resource) or resource.is_relative_to(workspace):
        raise ValueError('工作区必须位于应用资源之外，且不能包含应用资源')
    link = workspace/'.venv'
    ownership = workspace/'.easel-portable/venv.json'
    target = resource/'python'
    if link.exists() or link.is_symlink():
        try: owned = json.loads(ownership.read_text())['target']
        except (OSError, ValueError, KeyError): owned = None
        if not link.is_symlink() or str(link.readlink()) != owned:
            raise ValueError('已有 .venv 不属于 Easel；请选择独立工作区')
    workspace.mkdir(parents=True, exist_ok=True)
    seed_workspace(resource, workspace)
    if link.is_symlink(): link.unlink()
    link.symlink_to(target, target_is_directory=True)
    atomic_json(ownership, {'target':str(target)})
    for name in ('profiles', 'outputs', 'outputs/_harness/codex-home'):
        (workspace/name).mkdir(parents=True, exist_ok=True)


def instance_request(state, action, workspace):
    port = state['port']; token = state['token']
    if type(port) is not int or not 1 <= port <= 65535 or not isinstance(token, str) or len(token) != 64:
        raise ValueError('无效实例记录')
    request = urllib.request.Request(f'http://127.0.0.1:{port}/_easel/{action}', headers={'X-Easel-Token':token}, method='POST' if action == 'stop' else 'GET')
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=2) as response:
        result = json.load(response)
    if result.get('workspace') != str(workspace) or result.get('token') != token: raise ValueError('实例身份校验失败')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', type=Path, default=Path.home()/'Library/Application Support/Easel/workspace')
    parser.add_argument('--port', type=int, default=17860)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--stop', action='store_true')
    args = parser.parse_args(argv)
    resource = Path(__file__).resolve().parents[1]
    workspace = args.workspace.expanduser().resolve()
    state_path = safe_state(workspace)/'instance.json'
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text())
            instance_request(state, 'identity', workspace)
            if args.stop: instance_request(state, 'stop', workspace)
            if not args.stop and not args.no_browser: webbrowser.open(f'http://127.0.0.1:{state["port"]}')
            return 0
        except (OSError, ValueError, KeyError):
            if args.stop: raise ValueError('无法认证自有运行实例；未终止任何进程')
    elif args.stop:
        raise ValueError('没有自有运行实例记录')
    verify_inventory(resource)
    if workspace.is_relative_to(resource) or resource.is_relative_to(workspace):
        raise ValueError('工作区与资源重叠')
    (workspace/'.easel-portable').mkdir(parents=True, exist_ok=True)
    # Lock is per workspace, survives stale process state without PID-based killing.
    import fcntl
    lock = (workspace/'.easel-portable/launcher.lock').open('a')
    try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError: raise ValueError('此工作区已有正在启动或运行的 Easel 实例')
    prepare_workspace(resource, workspace)
    native = resource/'native'
    os.environ.update(EASEL_WORKSPACE_ROOT=str(workspace), EASEL_ROOT=str(workspace), EASEL_RESOURCE_ROOT=str(resource), EASEL_RUNTIME_MANIFEST=str(resource/'runtime-manifest.json'), EASEL_PORTABLE='1', CODEX_HOME=str(workspace/'outputs/_harness/codex-home'), PYTHONDONTWRITEBYTECODE='1', PATH=os.pathsep.join([str(resource/'python/bin'),str(native/'codex-path'),str(native/'bin'),'/usr/bin','/bin','/usr/sbin','/sbin']))
    os.chdir(workspace)
    from easel.installation import initialize_runtime
    initialize_runtime(workspace, 'codex')
    from web.app import app
    import uvicorn
    from fastapi import Request, HTTPException
    globals()['Request'] = Request
    token = secrets.token_hex(32)
    server = None
    @app.get('/_easel/identity')
    async def identity(request: Request):
        if not secrets.compare_digest(request.headers.get('X-Easel-Token',''), token): raise HTTPException(403)
        return {'token':token,'workspace':str(workspace)}
    @app.post('/_easel/stop')
    async def stop(request: Request):
        if not secrets.compare_digest(request.headers.get('X-Easel-Token',''), token): raise HTTPException(403)
        server.should_exit = True
        return {'token':token,'workspace':str(workspace)}
    sock = socket.socket()
    try: sock.bind(('127.0.0.1', args.port))
    except OSError as error: raise ValueError('端口已占用；未终止其他进程。请关闭自有实例或使用 --port') from error
    sock.listen(128)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='info'))
    atomic_json(state_path, {'port':port,'token':token})
    def ready():
        while not server.started and not server.should_exit: time.sleep(.05)
        if server.started:
            print(f'EASEL_READY http://127.0.0.1:{port}', flush=True)
            if not args.no_browser: webbrowser.open(f'http://127.0.0.1:{port}')
    threading.Thread(target=ready, daemon=True).start()
    try: server.run(sockets=[sock])
    finally:
        sock.close()
        if state_path.exists() and json.loads(state_path.read_text()).get('token') == token: state_path.unlink()
    return 0


if __name__ == '__main__':
    try: raise SystemExit(main())
    except Exception as error:
        print(f'Easel 启动失败：{error}', file=sys.stderr)
        raise SystemExit(1)
