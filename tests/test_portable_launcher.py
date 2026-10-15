import json
from pathlib import Path
import pytest
from easel.portable import seed_workspace, prepare_workspace, verify_inventory


def test_seed_preserves_user_changes_and_updates_untouched(tmp_path):
    resource, workspace = tmp_path/'app', tmp_path/'data'
    (resource/'seed/skills').mkdir(parents=True)
    (resource/'seed/skills/a').write_text('one')
    seed_workspace(resource, workspace)
    (workspace/'skills/a').write_text('user')
    (resource/'seed/skills/a').write_text('two')
    seed_workspace(resource, workspace)
    assert (workspace/'skills/a').read_text() == 'user'
    (resource/'seed/skills/b').write_text('one')
    seed_workspace(resource, workspace)
    (resource/'seed/skills/b').write_text('two')
    seed_workspace(resource, workspace)
    assert (workspace/'skills/b').read_text() == 'two'


def test_overlap_and_foreign_venv_fail_before_seed(tmp_path):
    resource = tmp_path/'app'; resource.mkdir()
    with pytest.raises(ValueError): prepare_workspace(resource, resource/'data')
    workspace = tmp_path/'data'; workspace.mkdir(); (workspace/'.venv').mkdir()
    with pytest.raises(ValueError): prepare_workspace(resource, workspace)
    assert not (workspace/'.easel-portable').exists()


def test_malformed_seed_state_preserved(tmp_path):
    resource, workspace = tmp_path/'app', tmp_path/'data'
    resource.mkdir(); (workspace/'.easel-portable').mkdir(parents=True)
    state = workspace/'.easel-portable/seed.json'; state.write_text('{bad')
    with pytest.raises(ValueError): seed_workspace(resource, workspace)
    assert state.read_text() == '{bad'


def test_inventory_rejects_tamper_and_escape(tmp_path):
    (tmp_path/'file').write_text('bad')
    (tmp_path/'build-inventory.json').write_text(json.dumps({'file':'0'*64}))
    with pytest.raises(ValueError): verify_inventory(tmp_path)
    (tmp_path/'build-inventory.json').write_text(json.dumps({'../outside':'0'*64}))
    with pytest.raises(ValueError): verify_inventory(tmp_path)


def test_builder_uses_resource_allowlist():
    from scripts.packaging.build_macos import selected
    assert selected('skills/shared/scripts/model_registry.py')
    assert selected('easel/portable.py')
    for path in ['profiles/private/BIO.md','.env','outputs/key.json','skills/a/.env','skills/a/.token_cache.json','skills/a/node_modules/a.js','scripts/validation/verify_codex_gateway.py','untracked-credentials.json']:
        assert not selected(path), path


def test_seed_does_not_overwrite_symlinked_user_file(tmp_path):
    resource, workspace=tmp_path/'app',tmp_path/'data'
    (resource/'seed/skills').mkdir(parents=True)
    (resource/'seed/skills/a').write_text('old')
    seed_workspace(resource,workspace)
    profile=workspace/'BIO'; profile.write_text('old')
    (workspace/'skills/a').unlink(); (workspace/'skills/a').symlink_to(profile)
    (resource/'seed/skills/a').write_text('new')
    seed_workspace(resource,workspace)
    assert profile.read_text()=='old'


def test_control_directory_symlink_rejected(tmp_path):
    resource,workspace=tmp_path/'app',tmp_path/'data'
    resource.mkdir(); workspace.mkdir(); outside=tmp_path/'outside'; outside.mkdir()
    (workspace/'.easel-portable').symlink_to(outside)
    with pytest.raises(ValueError): prepare_workspace(resource,workspace)
    assert list(outside.iterdir())==[]


@pytest.mark.parametrize("value", [{"skills/a": None}, {"junk": "bad"}, {"../escape": "0"*64}, {"/absolute": "0"*64}, {"skills/a\x00": "0"*64}, {"skills/a\\b": "0"*64}])
def test_invalid_seed_schema_preserves_bytes(tmp_path, value):
    resource, workspace = tmp_path/'app', tmp_path/'data'
    (resource/'seed/skills').mkdir(parents=True)
    (resource/'seed/skills/a').write_text('new')
    state = workspace/'.easel-portable/seed.json'; state.parent.mkdir(parents=True)
    original = json.dumps(value); state.write_text(original)
    with pytest.raises(ValueError): seed_workspace(resource, workspace)
    assert state.read_text() == original
    assert not (workspace/'skills/a').exists()


def test_seed_interrupted_copy_preserves_destination(tmp_path, monkeypatch):
    import easel.portable as portable
    resource, workspace = tmp_path/'app', tmp_path/'data'
    (resource/'seed/skills').mkdir(parents=True)
    source = resource/'seed/skills/a'; source.write_text('old')
    seed_workspace(resource, workspace); source.write_text('new')
    def broken(src, dst):
        Path(dst).write_text('partial'); raise OSError('disk full')
    monkeypatch.setattr(portable.shutil, 'copy2', broken)
    with pytest.raises(OSError): seed_workspace(resource, workspace)
    assert (workspace/'skills/a').read_text() == 'old'


def test_seed_recovers_after_metadata_save_failure(tmp_path, monkeypatch):
    import easel.portable as portable
    resource, workspace = tmp_path/'app', tmp_path/'data'
    (resource/'seed/skills').mkdir(parents=True)
    source = resource/'seed/skills/a'; source.write_text('old')
    seed_workspace(resource, workspace); source.write_text('new')
    original = portable.atomic_json
    monkeypatch.setattr(portable, 'atomic_json', lambda *a: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError): seed_workspace(resource, workspace)
    monkeypatch.setattr(portable, 'atomic_json', original)
    seed_workspace(resource, workspace); source.write_text('newer')
    seed_workspace(resource, workspace)
    assert (workspace/'skills/a').read_text() == 'newer'


@pytest.mark.parametrize('name', ['seed.json', 'venv.json', 'instance.json', 'launcher.lock'])
@pytest.mark.parametrize('dangling', [False, True])
def test_individual_control_symlinks_rejected(tmp_path, name, dangling):
    from easel.portable import safe_state
    workspace = tmp_path/'data'; state = workspace/'.easel-portable'; state.mkdir(parents=True)
    outside = tmp_path/'foreign'
    if not dangling: outside.write_text('foreign')
    (state/name).symlink_to(outside)
    with pytest.raises(ValueError): safe_state(workspace)
    assert not outside.exists() if dangling else outside.read_text() == 'foreign'


def test_builder_rejects_extra_and_mismatched_payload(tmp_path):
    from scripts.packaging.build_macos import verify_payload
    payload = tmp_path/'payload'; payload.mkdir(); (payload/'a').write_text('ok')
    from easel.portable import digest
    records = {'a': digest(payload/'a')}
    verify_payload(payload, records)
    (payload/'extra.pth').write_text('unsafe')
    with pytest.raises(ValueError): verify_payload(payload, records)


def test_builder_metadata_uses_tracked_authority(tmp_path):
    from scripts.packaging.build_macos import verify_metadata
    authoritative, prepared = tmp_path/'tracked', tmp_path/'prepared'
    authoritative.mkdir(); prepared.mkdir()
    (authoritative/'pins').write_text('pinned'); (prepared/'pins').write_text('different')
    with pytest.raises(ValueError): verify_metadata(prepared, authoritative, ['pins'])


@pytest.mark.parametrize('suffix', ['', 'event: error\ndata: "failed"\n\n', 'event: done\ndata: {}\n\nevent: done\ndata: {}\n\n'])
def test_sse_proof_rejects_incomplete_or_error(suffix):
    from scripts.validation.verify_portable import assert_sse_success
    stream = 'event: token\ndata: "MARKER"\n\n' + suffix
    with pytest.raises(AssertionError): assert_sse_success(stream, 'MARKER')


def test_sse_proof_requires_marker_and_successful_done():
    from scripts.validation.verify_portable import assert_sse_success
    stream = 'event: token\ndata: "MARKER"\n\nevent: done\ndata: {}\n\n'
    assert assert_sse_success(stream, 'MARKER') == {'token': 1, 'done': 1}
    with pytest.raises(AssertionError): assert_sse_success(stream, 'OTHER')


def test_seed_existing_directory_is_preserved(tmp_path):
    resource, workspace = tmp_path/'app', tmp_path/'data'
    (resource/'seed/skills').mkdir(parents=True)
    (resource/'seed/skills/a').write_text('seed')
    (workspace/'skills/a').mkdir(parents=True)
    seed_workspace(resource, workspace)
    assert (workspace/'skills/a').is_dir()


def test_builder_licenses_select_tracked_authority_only(tmp_path, monkeypatch):
    from scripts.packaging import build_macos as builder
    root = tmp_path/'repo'; authority = root/'packaging/macos/licenses'
    authority.mkdir(parents=True)
    (authority/'notice').write_text('authoritative')
    (authority/'untracked-secret').write_text('secret')
    prepared = root/'outputs/licenses'; prepared.mkdir(parents=True)
    (prepared/'notice').write_text('mutated')
    (prepared/'extra.pth').write_text('malicious')
    (prepared/'linked-secret').symlink_to(authority/'untracked-secret')
    monkeypatch.setattr(builder, 'ROOT', root)
    target = tmp_path/'bundled'
    builder.copy_tracked_licenses(target, ['packaging/macos/licenses/notice'])
    assert {p.name for p in target.iterdir()} == {'notice'}
    assert (target/'notice').read_text() == 'authoritative'


@pytest.mark.parametrize('kind', ['file-link', 'directory-link', 'directory'])
def test_builder_rejects_nonregular_tracked_license(tmp_path, monkeypatch, kind):
    from scripts.packaging import build_macos as builder
    root = tmp_path/'repo'; authority = root/'packaging/macos/licenses'
    authority.mkdir(parents=True)
    outside = tmp_path/'outside'; outside.mkdir(); (outside/'notice').write_text('secret')
    if kind == 'file-link': (authority/'notice').symlink_to(outside/'notice')
    elif kind == 'directory-link':
        authority.rmdir(); authority.symlink_to(outside, target_is_directory=True)
    else: (authority/'notice').mkdir()
    monkeypatch.setattr(builder, 'ROOT', root)
    with pytest.raises(ValueError):
        builder.copy_tracked_licenses(tmp_path/'bundled', ['packaging/macos/licenses/notice'])


@pytest.mark.parametrize('requested', [17860, 43210, 0])
def test_launcher_publishes_actual_port_before_web_import(tmp_path, monkeypatch, requested):
    import builtins
    import os
    import socket
    import easel.portable as portable
    workspace = tmp_path/'workspace'
    monkeypatch.setattr(os, 'environ', os.environ.copy())
    monkeypatch.setattr(portable, 'verify_inventory', lambda *a: None)
    monkeypatch.setattr(portable, 'prepare_workspace', lambda *a: None)
    monkeypatch.setattr(os, 'chdir', lambda *a: None)
    import easel.installation
    monkeypatch.setattr(easel.installation, 'initialize_runtime', lambda *a: None)
    real_import = builtins.__import__
    sockets = []
    class FakeSocket:
        def __init__(self): self.closed = False; sockets.append(self)
        def setsockopt(self, level, option, value):
            assert (level, option, value) == (socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.reuse = True
        def bind(self, address):
            assert getattr(self, 'reuse', False), 'SO_REUSEADDR required before bind'
            self.port = address[1] or 45678
        def getsockname(self): return ('127.0.0.1', self.port)
        def listen(self, *a): pass
        def close(self): self.closed = True
    monkeypatch.setattr(socket, 'socket', FakeSocket)
    def intercept(name, *args, **kwargs):
        if name == 'web.app':
            assert sockets and os.environ.get('EASEL_PORT') == str(requested or 45678)
            raise RuntimeError('mock web startup failure')
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', intercept)
    with pytest.raises(RuntimeError, match='mock web startup failure'):
        portable.main(['--workspace', str(workspace), '--port', str(requested), '--no-browser'])
    assert sockets[0].closed


@pytest.mark.parametrize('extra', ['extra.js', '.env', 'assets/unrelated-12345678.js'])
def test_frontend_inventory_rejects_unlisted_files(tmp_path, extra):
    from scripts.packaging.build_macos import copy_frontend
    from easel.portable import digest
    dist = tmp_path/'dist'; dist.mkdir(); (dist/'index.html').write_text('built')
    (dist/'portable-assets.json').write_text(json.dumps({'schema_version': 1, 'files': {'index.html': digest(dist/'index.html')}}))
    path = dist/extra; path.parent.mkdir(exist_ok=True); path.write_text('dummy secret')
    with pytest.raises(ValueError): copy_frontend(dist, tmp_path/'target')
    assert not (tmp_path/'target').exists()


@pytest.mark.parametrize('kind', ['file', 'directory', 'dist', 'ancestor'])
def test_frontend_inventory_rejects_all_symlink_inputs(tmp_path, kind):
    from scripts.packaging.build_macos import copy_frontend
    from easel.portable import digest
    actual = tmp_path/'actual'; actual.mkdir(); (actual/'index.html').write_text('built')
    records = {'index.html': digest(actual/'index.html')}
    if kind == 'file':
        (actual/'alias.txt').symlink_to(actual/'index.html'); records['alias.txt'] = records['index.html']
    if kind == 'directory':
        (actual/'assets').symlink_to(tmp_path, target_is_directory=True)
    (actual/'portable-assets.json').write_text(json.dumps({'schema_version': 1, 'files': records}))
    dist = actual
    if kind == 'dist':
        dist = tmp_path/'dist'; dist.symlink_to(actual, target_is_directory=True)
    if kind == 'ancestor':
        alias = tmp_path/'alias'; alias.symlink_to(tmp_path, target_is_directory=True); dist = alias/'actual'
    with pytest.raises(ValueError): copy_frontend(dist, tmp_path/'target')
    assert not (tmp_path/'target').exists()


def test_frontend_inventory_copies_exact_hashes_and_rejects_tamper(tmp_path):
    from scripts.packaging.build_macos import copy_frontend
    from easel.portable import digest
    dist = tmp_path/'dist'; dist.mkdir(); (dist/'index.html').write_text('built')
    (dist/'portable-assets.json').write_text(json.dumps({'schema_version': 1, 'files': {'index.html': digest(dist/'index.html')}}))
    copy_frontend(dist, tmp_path/'target')
    assert [p.name for p in (tmp_path/'target').iterdir()] == ['index.html']
    (dist/'index.html').write_text('modified')
    with pytest.raises(ValueError): copy_frontend(dist, tmp_path/'other')


@pytest.mark.parametrize('port', [17860, 43210, 45678])
def test_real_web_guard_uses_launch_port_and_rejects_foreign(port):
    import os
    import subprocess
    import sys
    code = """
import asyncio
from starlette.requests import Request
from starlette.responses import Response
from web.app import local_write_guard
async def downstream(request): return Response(status_code=204)
async def check():
    for origin, expected in [('http://127.0.0.1:PORT', 204), ('http://localhost:PORT', 204), ('https://foreign.invalid', 403)]:
        for path in ['/api/settings/harness/provider', '/api/settings/harness', '/api/chat/stream']:
            req = Request({'type':'http', 'method':'POST', 'path':path, 'headers':[(b'origin', origin.encode())], 'server':('127.0.0.1', PORT), 'client':('127.0.0.1', 1), 'scheme':'http', 'query_string':b''})
            assert (await local_write_guard(req, downstream)).status_code == expected
asyncio.run(check())
""".replace('PORT', str(port))
    subprocess.run([sys.executable, '-c', code], env={**os.environ, 'EASEL_PORT':str(port)}, check=True, capture_output=True)


@pytest.mark.parametrize('private', ['.env', 'assets/credential.json', 'assets/token_cache.js'])
def test_frontend_receipt_cannot_authorize_private_names(tmp_path, private):
    from scripts.packaging.build_macos import copy_frontend
    from easel.portable import digest
    dist = tmp_path/'dist'; dist.mkdir(); (dist/'index.html').write_text('built')
    path = dist/private; path.parent.mkdir(exist_ok=True); path.write_text('dummy')
    records = {p: digest(dist/p) for p in ['index.html', private]}
    (dist/'portable-assets.json').write_text(json.dumps({'schema_version':1, 'files':records}))
    with pytest.raises(ValueError): copy_frontend(dist, tmp_path/'target')
    assert not (tmp_path/'target').exists()


def test_frontend_receipt_must_match_current_source(tmp_path):
    from scripts.packaging.build_macos import copy_frontend
    from easel.portable import digest
    dist = tmp_path/'dist'; dist.mkdir(); (dist/'index.html').write_text('built')
    (dist/'portable-assets.json').write_text(json.dumps({'schema_version':1, 'files':{'index.html':digest(dist/'index.html')}, 'source':{'input':'old'}}))
    with pytest.raises(ValueError, match='stale'):
        copy_frontend(dist, tmp_path/'target', source_records={'input':'new'})


@pytest.mark.parametrize('stop', [False, True])
def test_launcher_reuses_only_authenticated_existing_instance(tmp_path, monkeypatch, stop):
    import easel.portable as portable
    workspace = tmp_path/'workspace'; state = workspace/'.easel-portable/instance.json'
    state.parent.mkdir(parents=True); state.write_text(json.dumps({'port':43210, 'token':'x'*64}))
    calls = []
    monkeypatch.setattr(portable, 'instance_request', lambda data, action, root: calls.append((action, root)))
    monkeypatch.setattr(portable.socket, 'socket', lambda: pytest.fail('reuse must not bind another socket'))
    assert portable.main(['--workspace',str(workspace),'--no-browser'] + (['--stop'] if stop else [])) == 0
    assert calls == [('identity', workspace)] + ([('stop', workspace)] if stop else [])


@pytest.mark.parametrize('occupied', [False, True])
def test_launcher_real_socket_restart_and_foreign_listener(tmp_path, monkeypatch, occupied):
    import builtins
    import os
    import socket
    from types import SimpleNamespace
    import easel.portable as portable
    import easel.installation
    monkeypatch.setattr(os, 'environ', os.environ.copy())
    monkeypatch.setattr(os, 'chdir', lambda *a: None)
    monkeypatch.setattr(portable, 'verify_inventory', lambda *a: None)
    monkeypatch.setattr(portable, 'prepare_workspace', lambda *a: None)
    monkeypatch.setattr(easel.installation, 'initialize_runtime', lambda *a: None)
    class App:
        def get(self, *a): return lambda fn: fn
        def post(self, *a): return lambda fn: fn
    class Server:
        def __init__(self, config): self.started = False; self.should_exit = True
        def run(self, sockets):
            listener = sockets[0]
            with socket.create_connection(listener.getsockname()) as client:
                peer, _ = listener.accept()
                peer.sendall(b'ok'); peer.close()  # active server close enters TIME_WAIT
                assert client.recv(2) == b'ok'
                assert client.recv(1) == b''
    real_import = builtins.__import__
    def intercept(name, *args, **kwargs):
        if name == 'web.app': return SimpleNamespace(app=App())
        if name == 'uvicorn': return SimpleNamespace(Server=Server, Config=lambda *a, **k: None)
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', intercept)
    workspace = tmp_path/'workspace'
    with socket.socket() as foreign:
        foreign.bind(('127.0.0.1', 0)); port = foreign.getsockname()[1]
        if occupied:
            foreign.listen(1)
            with pytest.raises(ValueError, match='端口已占用'):
                portable.main(['--workspace',str(workspace),'--port',str(port),'--no-browser'])
            assert foreign.getsockname()[1] == port
            with socket.create_connection(('127.0.0.1',port)) as client:
                peer, _ = foreign.accept(); peer.close()
            return
    for _ in range(2):
        assert portable.main(['--workspace',str(workspace),'--port',str(port),'--no-browser']) == 0
