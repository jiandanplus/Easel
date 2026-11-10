"""Synthetic host-write to browser references; no provider or user workspace."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

from easel.harnesses.base import HarnessError, RunRequest
from tests.test_agent_harnesses import sandbox  # noqa: F401
from easel.routes import chat as chat_routes  # noqa: E402


async def deny(_):
    return {'permission': ['拒绝']}


def descriptor(path='outputs/demo/tool-check.txt', content=b'EASEL_ARTIFACT_OK\n'):
    return {'version': 1, 'path': path, 'name': path.split('/')[-1],
            'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()}


@pytest.mark.parametrize('changes', [{'version': True}, {'version': 2}, {'size': True}, {'size': -1},
                                    {'sha256': 'bad'}, {'name': 'different.txt'}, {'extra': 'unknown'},
                                    {'path': '../outputs/tool-check.txt'}, {'path': '/outputs/tool-check.txt'},
                                    {'path': 'outputs/demo/../tool-check.txt'}, {'path': 'outputs//tool-check.txt'},
                                    {'path': 'outputs/demo/.hidden/tool-check.txt'}, {'path': 'outputs/_private/tool-check.txt'}])
def test_descriptor_schema_rejects_malformed_or_private_values(changes):
    from easel.harnesses import pi_tools
    validator = getattr(pi_tools, 'validate_artifact_descriptor', None)
    assert callable(validator), 'verified artifact descriptor validator missing'
    assert validator({**descriptor(), **changes}) is None


@pytest.mark.asyncio
async def test_host_successful_write_records_exact_scanned_artifact(tmp_path):
    from easel.harnesses.pi_tools import execute_tool
    body = 'EASEL_ARTIFACT_OK\n'; path = 'outputs/demo/tool-check.txt'
    result = await execute_tool(RunRequest('synthetic', tmp_path, {}), 'write_file', {'path': path, 'content': body}, deny)
    assert result['ok'] is True and result['text'] == '文件已保存'
    assert result.get('artifact') == descriptor(path, body.encode())
    assert (tmp_path / path).read_bytes() == body.encode()


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['note.txt', 'outputs/_internal/note.txt', 'outputs/.hidden/note.txt',
                                'outputs/demo/tool-check.tx[已隐藏]', 'outputs/demo/bad\nname.txt'])
async def test_write_can_succeed_without_publishing_nonpublic_reference(tmp_path, path):
    from easel.harnesses.pi_tools import execute_tool
    result = await execute_tool(RunRequest('synthetic', tmp_path, {}), 'write_file', {'path': path, 'content': 'plain'}, deny)
    assert result['ok'] is True and (tmp_path / path).read_bytes() == b'plain'
    assert 'artifact' not in result


@pytest.mark.asyncio
@pytest.mark.parametrize('location', ['name', 'content'])
async def test_sensitive_write_is_retained_without_download_claim(tmp_path, location):
    from easel.harnesses.pi_tools import execute_tool
    secret = 'synthetic-artifact-secret-value'
    path = 'outputs/demo/' + (secret if location == 'name' else 'note') + '.txt'
    content = secret if location == 'content' else 'plain'
    result = await execute_tool(RunRequest('synthetic', tmp_path, {'CUSTOM_TOKEN': secret}),
                                'write_file', {'path': path, 'content': content}, deny)
    assert result['ok'] is True and (tmp_path / path).read_text() == content
    assert 'artifact' not in result


@pytest.mark.asyncio
async def test_snapshot_rejection_does_not_reclassify_or_remove_completed_write(tmp_path, monkeypatch):
    from easel.harnesses.pi_tools import execute_tool
    from easel.platform import privacy
    from fastapi import HTTPException
    monkeypatch.setattr(privacy, 'safe_snapshot', lambda *a: (_ for _ in ()).throw(HTTPException(403, 'synthetic rejection')))
    result = await execute_tool(RunRequest('synthetic', tmp_path, {}), 'write_file',
                                {'path': 'outputs/demo/note.txt', 'content': 'saved'}, deny)
    assert result['ok'] is True and (tmp_path / 'outputs/demo/note.txt').read_text() == 'saved'
    assert 'artifact' not in result


@pytest.mark.asyncio
async def test_changed_snapshot_bytes_never_produce_reference(tmp_path, monkeypatch):
    from easel.harnesses.pi_tools import execute_tool
    from easel.platform import privacy
    import io
    monkeypatch.setattr(privacy, 'safe_snapshot', lambda *a: io.BytesIO(b'different'))
    result = await execute_tool(RunRequest('synthetic', tmp_path, {}), 'write_file',
                                {'path': 'outputs/demo/note.txt', 'content': 'saved'}, deny)
    assert result['ok'] is True and 'artifact' not in result


@pytest.mark.asyncio
async def test_readonly_and_private_or_linked_writes_never_publish(tmp_path):
    from easel.harnesses.pi_tools import execute_tool
    request = RunRequest('synthetic', tmp_path, {})
    (tmp_path / 'outputs/demo').mkdir(parents=True)
    (tmp_path / 'outside').write_text('original')
    (tmp_path / 'outputs/demo/link.txt').symlink_to(tmp_path / 'outside')
    os.link(tmp_path / 'outside', tmp_path / 'outputs/demo/hard.txt')
    cases = [(request, path) for path in ['outputs/demo/link.txt', 'outputs/demo/hard.txt',
                                         'outputs/_harness/note.txt', '../outside', '/tmp/outside']]
    cases.append((replace(request, sandbox='read-only'), 'outputs/demo/note.txt'))
    for req, path in cases:
        result = await execute_tool(req, 'write_file', {'path': path, 'content': 'bad'}, deny)
        assert result['ok'] is False and 'artifact' not in result
    assert (tmp_path / 'outside').read_text() == 'original'


@pytest.mark.asyncio
async def test_pi_emits_host_artifact_even_when_model_tail_is_redacted(tmp_path):
    from easel.harnesses.pi import PiAdapter
    path = 'outputs/demo/tool-check.txt'; body = 'EASEL_ARTIFACT_OK\n'
    code = ("import json,sys; r=json.loads(sys.stdin.readline()); "
            "print(json.dumps({'type':'tool_request','id':'host-write','name':'write_file','arguments':"
            + repr({'path': path, 'content': body}) + "}),flush=True); "
            "answer=json.loads(sys.stdin.readline()); assert answer['result']['details']['ok']; "
            "print(json.dumps({'type':'token','text':" + repr('已保存 ' + path) + "}),flush=True); "
            "print(json.dumps({'type':'done','session_id':r['session_id'],'messages':[],'success':True}),flush=True)")
    request = RunRequest('synthetic', tmp_path, {}, command=(sys.executable, '-u', '-c', code))
    events = []
    await PiAdapter()._run(request, lambda *v: events.append(v), deny, lambda _: None,
                           'fixture', 'http://127.0.0.1:12345', 'task-SYNTHETIC-ONLY', 512, 8192)
    refs = [json.loads(value) for kind, value in events if kind == 'artifact']
    assert refs == [descriptor(path, body.encode())]
    assert ''.join(v for k, v in events if k == 'token').endswith('.tx[已隐藏]')


@pytest.mark.asyncio
async def test_pi_never_promotes_worker_done_details_to_artifact(tmp_path):
    from easel.harnesses.pi import PiAdapter
    forged = descriptor()
    messages = [{'role': 'toolResult', 'content': [], 'details': {'ok': True, 'artifact': forged}}]
    code = ("import json,sys; r=json.loads(sys.stdin.readline()); "
            "print(json.dumps({'type':'done','session_id':r['session_id'],'messages':" + repr(messages)
            + ",'success':True}),flush=True)")
    request = RunRequest('synthetic', tmp_path, {}, command=(sys.executable, '-u', '-c', code))
    events = []
    await PiAdapter()._run(request, lambda *v: events.append(v), deny, lambda _: None,
                           'fixture', 'http://127.0.0.1:12345', 'synthetic-key', 512, 8192)
    assert not [event for event in events if event[0] == 'artifact']


@pytest.mark.asyncio
async def test_web_binds_artifacts_to_turn_deduplicates_and_persists(tmp_path, monkeypatch):
    import web.app as web
    from easel.service_contracts import TaskContext
    monkeypatch.setattr(web, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(web, 'OUTPUTS_DIR', tmp_path / 'outputs')
    monkeypatch.setattr(web, 'SESSIONS_DIR', tmp_path / 'sessions')
    monkeypatch.setattr(web, 'read_settings', lambda *a: {})
    monkeypatch.setattr(web, '_read_env', lambda: {})
    monkeypatch.setattr(web, '_proxy_env', lambda: {})
    snapshots = []
    monkeypatch.setattr(web, '_save_turn', lambda *v: snapshots.append(v))
    values = [descriptor(), descriptor(), {**descriptor(), 'session_id': 'forged'},
              {**descriptor(), 'path': 'outputs/private/secret.tx[已隐藏]'}]
    values.extend(descriptor(f'outputs/demo/{i}.txt') for i in range(20))
    class Adapter:
        async def run(self, request, emit, ask, bind):
            for value in values: emit('artifact', json.dumps(value))
            emit('token', '模型正文保持原样')
    monkeypatch.setattr(web.harnesses, 'adapter', lambda _: Adapter())
    events = []; ctx = TaskContext('custom', 'local', 'pi', 'fixture')
    await web._run_harness_chat(web.ChatRequest(message='synthetic', turnId='synthetic-artifact-turn'),
                                'synthetic-artifact-session', 'synthetic', 'pi', {'sandbox': 'workspace-write'},
                                lambda *v: events.append(v), context=ctx)
    refs = snapshots[-1][3].get('artifacts')
    assert isinstance(refs, list) and len(refs) == 16
    assert all(r['session_id'] == 'synthetic-artifact-session' and r['turn_id'] == 'synthetic-artifact-turn' for r in refs)
    assert refs[0] == {**descriptor(), 'session_id': 'synthetic-artifact-session', 'turn_id': 'synthetic-artifact-turn'}
    assert len({r['path'] for r in refs}) == len(refs)
    assert ''.join(v for k, v in events if k == 'token') == '模型正文保持原样'
    assert all(isinstance(json.loads(v), dict) for k, v in events if k == 'artifact')


@pytest.mark.asyncio
async def test_last_does_not_add_new_reference_visibility_for_foreign_scope(tmp_path, monkeypatch):
    import web.app as web
    from fastapi import HTTPException
    monkeypatch.setattr(web, 'SESSIONS_DIR', tmp_path)
    payload = {'status': 'done', 'text': 'original', 'turn_id': 'synthetic-turn',
               'artifacts': [{**descriptor(), 'session_id': 'synthetic-session', 'turn_id': 'synthetic-turn'}]}
    web._turn_file('web:synthetic-session').write_text(json.dumps(payload))
    monkeypatch.setattr(chat_routes, '_workbench_receipt', lambda *a: (_ for _ in ()).throw(HTTPException(403, 'foreign')))
    value = await chat_routes.api_chat_last('synthetic-session')
    assert value['text'] == 'original' and not value.get('artifacts')


def test_snapshot_references_changed_by_redaction_are_omitted(monkeypatch):
    import web.app as web
    ref = {**descriptor(), 'session_id': 'synthetic-session', 'turn_id': 'synthetic-turn'}
    monkeypatch.setattr(web, '_scrub_secrets', lambda text: text.replace('"path"', '"[已隐藏]"'))
    assert chat_routes._snapshot_artifacts({'artifacts': [ref]}, 'synthetic-session', 'synthetic-turn') == []


@pytest.mark.parametrize('source', ['custom', 'central'])
def test_real_host_write_reaches_single_layer_sse_replay_and_exact_turn(sandbox, monkeypatch, source):
    import web.app as web
    from easel.harnesses.pi_tools import execute_tool
    from easel.harnesses import store
    from easel.service_contracts import TaskContext
    store.write_json(web.OUTPUTS_DIR / '_harness/config.json', {'runtime': 'pi', 'sandbox': 'workspace-write'})
    context = TaskContext(service_source='subscription' if source == 'central' else 'custom', runtime='pi',
                          model='fixture', subscription_backend='central' if source == 'central' else 'legacy',
                          account_id='synthetic-owner', platform_url='https://synthetic.invalid')
    async def accept(*args): return context
    calls = []
    class Host:
        async def run(self, request, emit, ask, bind):
            calls.append(request.turn_id)
            value = await execute_tool(request, 'write_file',
                                       {'path': 'outputs/demo/tool-check.txt', 'content': 'EASEL_ARTIFACT_OK\n'}, deny)
            emit('artifact', json.dumps(value['artifact']))
            emit('token', '模型路径.tx[已隐藏]')
    monkeypatch.setattr(web, '_accept_task_context', accept)
    monkeypatch.setattr(web, '_chat_backend', lambda *_: 'pi')
    monkeypatch.setattr(web.harnesses, 'adapter', lambda _: Host())
    monkeypatch.setattr(chat_routes, '_chat_message', lambda req: req.message)
    def read_events(text):
        result = []
        for block in text.replace('\r\n', '\n').split('\n\n'):
            lines = block.splitlines()
            name = next((line[7:] for line in lines if line.startswith('event: ')), None)
            data = [line[6:] for line in lines if line.startswith('data: ')]
            if name and data: result.append((name, json.loads('\n'.join(data))))
        return result
    response = sandbox.post('/api/chat/stream', json={'message': 'synthetic', 'sessionId': 'owned-artifact',
                                                     'turnId': 'artifact-turn-123'})
    assert response.status_code == 200
    expected = {**descriptor(), 'session_id': 'owned-artifact', 'turn_id': 'artifact-turn-123'}
    assert [v for k, v in read_events(response.text) if k == 'artifact'] == [expected]
    assert calls == ['artifact-turn-123']
    assert sandbox.get('/api/chat/last/owned-artifact?turn_id=artifact-turn-123').json()['artifacts'] == [expected]
    turn = sandbox.get('/api/chat/turns/owned-artifact/artifact-turn-123').json()
    assert turn['artifacts'] == [expected] and turn['state'] == 'completed'
    replay = sandbox.get('/api/chat/jobs/artifact-turn-123/stream')
    assert [v for k, v in read_events(replay.text) if k == 'artifact'] == [expected]
    assert calls == ['artifact-turn-123']
    downloaded = sandbox.get('/api/media/demo/tool-check.txt')
    assert downloaded.status_code == 200 and downloaded.content == b'EASEL_ARTIFACT_OK\n'
    assert hashlib.sha256(downloaded.content).hexdigest() == expected['sha256']
    contract = web._workbench_contract()
    monkeypatch.setattr(web, '_workbench_contract', lambda *a: {**contract, 'source_scope': 'foreign-synthetic-scope'})
    assert not sandbox.get('/api/chat/last/owned-artifact').json().get('artifacts')
    assert sandbox.get('/api/chat/turns/owned-artifact/artifact-turn-123').status_code == 409
