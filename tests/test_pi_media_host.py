"""Pi JSONL integration with the native media host and noncommercial HTTP."""
from dataclasses import replace
import json
from pathlib import Path
import os
import subprocess
import sys
import asyncio

import pytest

from tests.test_central_media_host import Fixture, setup, MODEL
from tests.test_pi_adapter import PREFIX, DONE, deny


@pytest.mark.asyncio
async def test_pi_projects_catalog_then_executes_media_in_host_not_worker(tmp_path, monkeypatch):
    from easel.harnesses.pi import PiAdapter
    import easel.platform.media as media
    fixture = Fixture(); _, host, request = setup(tmp_path, fixture)
    monkeypatch.setattr(media, "MediaHost", lambda _: host)
    code = PREFIX + (
        "assert r['media_models'][0]['id']==" + repr(MODEL) + "; "
        "assert '不得通过旧脚本' in r['system_prompt']; "
        "assert 'MEDIA-TASK-TOKEN-SECRET' not in json.dumps(r); "
        "print(json.dumps({'type':'tool_request','id':'create-1','name':'create_media','arguments':{'model':" + repr(MODEL) + ",'prompt':'blue circle'}}),flush=True); "
        "created=json.loads(sys.stdin.readline())['result']; assert created['details']['ok']; "
        "job=created['details']['job']; "
        "print(json.dumps({'type':'tool_request','id':'download-1','name':'download_media_artifact','arguments':{'job_id':job['id'],'artifact_id':job['artifacts'][0]['id'],'path':'outputs/generated.png'}}),flush=True); "
        "saved=json.loads(sys.stdin.readline())['result']; assert saved['details']['ok']; "
        "assert 'SECRET' not in json.dumps(saved); "
    ) + DONE
    request = replace(request, command=(sys.executable, "-u", "-c", code))
    await PiAdapter()._run(request, lambda *_: None, deny, lambda _: None, "deepseek-flash",
                           "http://127.0.0.1:12345", "LOCAL-PROXY-SECRET", 512, 8192)
    assert (tmp_path / "outputs/generated.png").read_bytes() == fixture.raw
    assert len([r for r in fixture.calls if r.method == "POST" and r.url.path == "/v2/media/jobs"]) == 1


@pytest.mark.asyncio
async def test_pi_unknown_media_content_exposes_original_operation_for_read_only_recovery(tmp_path, monkeypatch):
    from easel.harnesses.pi import PiAdapter
    import easel.platform.media as media
    fixture = Fixture(); fixture.timeout = True
    _, host, request = setup(tmp_path, fixture)
    monkeypatch.setattr(media, "MediaHost", lambda _: host)
    code = PREFIX + (
        "print(json.dumps({'type':'tool_request','id':'create-1','name':'create_media','arguments':{'model':" + repr(MODEL) + ",'prompt':'blue circle'}}),flush=True); "
        "created=json.loads(sys.stdin.readline())['result']; "
        "visible=json.loads(created['content'][0]['text']); "
        "assert visible['ok'] is False; assert visible['operation_key']; "
        "assert 'SECRET' not in json.dumps(visible); "
        "print(json.dumps({'type':'tool_request','id':'query-1','name':'get_media_job','arguments':{'operation_key':visible['operation_key']}}),flush=True); "
        "queried=json.loads(sys.stdin.readline())['result']; "
        "visible_query=json.loads(queried['content'][0]['text']); "
        "assert visible_query['job']['id']==" + repr(fixture.job_id) + "; "
        "assert visible_query['job']['actual']=='1000000'; "
    ) + DONE
    request = replace(request, command=(sys.executable, "-u", "-c", code))
    await PiAdapter()._run(request, lambda *_: None, deny, lambda _: None, "deepseek-flash",
                           "http://127.0.0.1:12345", "LOCAL-PROXY-SECRET", 512, 8192)
    assert len([r for r in fixture.calls if r.method == "POST" and r.url.path == "/v2/media/jobs"]) == 1
    assert len([r for r in fixture.calls if r.method == "POST" and r.url.path.endswith("/task-tokens")]) == 1
    assert any(r.method == "GET" and r.url.path == "/v2/media/operations" for r in fixture.calls)


@pytest.mark.parametrize("scenario", ["media", "media-off", "media-invalid-model", "speech", "speech-invalid"])
def test_actual_bundled_sdk_dynamic_media_contract(scenario, tmp_path):
    payload = os.environ.get("EASEL_PI_TEST_PAYLOAD")
    if not payload: pytest.skip("owned Pi runtime required for SDK acceptance")
    directory = Path(payload).resolve()
    node = directory / "node/bin/node"
    if not node.is_file(): node = directory / "node"
    import shutil
    from tests.test_media_expansion import expanded
    from tests.test_central_media_host import definition
    shutil.copy2(Path(__file__).resolve().parents[1] / "runtime/pi/worker.mjs", tmp_path / "worker.mjs")
    (tmp_path / "node_modules").symlink_to(directory / "node_modules", target_is_directory=True)
    row = expanded() if scenario.startswith("speech") else definition()
    result = subprocess.run([str(node), str(Path(__file__).with_name("pi_media_worker_fixture.mjs")),
                             str(tmp_path), scenario, json.dumps(row)], capture_output=True, text=True, timeout=15,
                            env={"PATH": "/usr/bin:/bin"})
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["success"] is True


@pytest.mark.asyncio
async def test_subscription_legacy_image_script_cannot_bypass_host_network_even_if_command_approved(tmp_path):
    from easel.harnesses.pi_tools import execute_tool, shell_supported
    if not shell_supported(): pytest.skip("actual macOS sandbox-exec required")
    from easel.harnesses.base import RunRequest
    accepted = []
    async def receive(reader, writer):
        accepted.append(True); writer.close(); await writer.wait_closed()
    server = await asyncio.start_server(receive, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    (tmp_path / "legacy_image.sh").write_text(
        'test -z "$NEW_API_KEY" || exit 90\n/usr/bin/curl --max-time 1 --silent --fail http://127.0.0.1:' + str(port) + '/fixture\n')
    async def approve(_): return {"permission": ["允许本次"]}
    request = RunRequest("synthetic legacy script bypass", tmp_path, {"NEW_API_KEY": "SYNTHETIC-DO-NOT-INHERIT"},
                         service_source="subscription", subscription_backend="central")
    try:
        result = await execute_tool(request, "run_command", {"command": "/bin/sh legacy_image.sh"}, approve)
        assert result["ok"] is False and accepted == []
        assert "SYNTHETIC-DO-NOT-INHERIT" not in json.dumps(result)
    finally:
        server.close(); await server.wait_closed()


@pytest.mark.asyncio
async def test_pi_receives_complete_multimeter_definition_not_flat_image_price(tmp_path,monkeypatch):
    from tests.test_media_expansion import MultimediaFixture
    from easel.harnesses.pi import PiAdapter
    import easel.platform.media as media
    fixture=MultimediaFixture();_,host,request=setup(tmp_path,fixture)
    monkeypatch.setattr(media,'MediaHost',lambda _:host)
    code=PREFIX+(
        "m=r['media_models'][0]; assert m['capability']=='audio.speech'; "
        "assert len(m['rate']['meters'])==2; assert m['rate']['meters'][1]['denominator']=='3'; "
        "assert m['parameters']['properties']['response_format']['enum']==['wav']; assert 'cost' not in m; "
        "assert 'SECRET' not in json.dumps(m); "
    )+DONE
    request=replace(request,command=(sys.executable,'-u','-c',code))
    await PiAdapter()._run(request,lambda *_:None,deny,lambda _:None,'deepseek-flash','http://127.0.0.1:12345','LOCAL-PROXY-SECRET',512,8192)


@pytest.mark.parametrize('capability',['image.edit','image.variation','video.generate','music.generate','audio.transcribe','voice.enroll','voice.synthesize'])
def test_actual_sdk_each_expanded_capability_schema(tmp_path,capability):
    import shutil
    from uuid import uuid4
    from tests.test_media_expansion import expanded,UploadFixture
    from tests.test_central_media_host import definition
    payload=os.environ.get('EASEL_PI_TEST_PAYLOAD')
    if not payload:pytest.skip('owned Pi runtime required for SDK acceptance')
    directory=Path(payload).resolve();node=directory/'node/bin/node'
    if not node.is_file():node=directory/'node'
    shutil.copy2(Path(__file__).resolve().parents[1]/'runtime/pi/worker.mjs',tmp_path/'worker.mjs')
    (tmp_path/'node_modules').symlink_to(directory/'node_modules',target_is_directory=True)
    row=expanded(capability);args={'model':row['id'],'capability':capability,'prompt':'fixture','parameters':{'response_format':'wav'}}
    if capability in {'audio.transcribe','voice.enroll'}:
        row=UploadFixture(capability).row;args={'model':row['id'],'capability':capability,'assets':['sample.wav']}
    elif capability.startswith('image.'):
        row=definition();row.update(id='fixture-'+capability,capability=capability);row['limits']['max_assets']=1
        args={'model':row['id'],'capability':capability,'prompt':'fixture','assets':['sample.png']}
    elif capability=='video.generate':
        row['limits']['output_media'].update(mime_types=['video/mp4'],max_pixels=1048576)
        row['parameters']={'type':'object','properties':{'seconds':{'type':'integer','minimum':1,'maximum':5},'size':{'type':'string','enum':['1024x1024']}},'required':['seconds','size'],'additionalProperties':False}
        args['parameters']={'seconds':5,'size':'1024x1024'}
    elif capability=='music.generate':
        row['parameters']={'type':'object','properties':{'format':{'type':'string','enum':['wav']}},'required':['format'],'additionalProperties':False}
        args['parameters']={'format':'wav'}
    elif capability=='voice.synthesize':args['voice_id']=str(uuid4())
    result=subprocess.run([str(node),str(Path(__file__).with_name('pi_media_worker_fixture.mjs')),str(tmp_path),'media',json.dumps(row),json.dumps(args)],capture_output=True,text=True,timeout=15,env={'PATH':'/usr/bin:/bin'})
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['calls']==1


@pytest.mark.asyncio
async def test_pi_stdin_never_contains_unpublished_rate_fields(tmp_path,monkeypatch):
    from tests.test_media_expansion import MultimediaFixture
    from easel.harnesses.pi import PiAdapter
    import easel.platform.media as media
    fixture=MultimediaFixture()
    fixture.row['rate'].update(provider_id='synthetic-private-provider',owner={'user_id':'synthetic-private-owner'})
    _,host,request=setup(tmp_path,fixture)
    monkeypatch.setattr(media,'MediaHost',lambda _:host)
    code=PREFIX+(
        "assert 'synthetic-private' not in json.dumps(r); "
        "rate=r['media_models'][0]['rate']; assert set(rate)=={'version','meters'}; "
        "assert all(set(m)=={'unit','basis','numerator','denominator'} for m in rate['meters']); "
    )+DONE
    request=replace(request,command=(sys.executable,'-u','-c',code))
    await PiAdapter()._run(request,lambda *_:None,deny,lambda _:None,'deepseek-flash','http://127.0.0.1:12345','LOCAL-PROXY-SECRET',512,8192)
