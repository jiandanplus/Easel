"""Owned JSONL fake-worker fixtures, not commercial/provider acceptance."""
import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import pytest
from easel.harnesses.base import HarnessError, RunRequest


async def deny(_): return {"permission": ["拒绝"]}


def configured(tmp_path, monkeypatch, code):
    from easel.harnesses import pi
    monkeypatch.setattr(pi, "require_provider", lambda _: {"base_url": "https://fixture.invalid/v1", "model": "fixture", "api_key": "noncommercial-fixture-secret"})
    return RunRequest("hello", tmp_path, {"OPENAI_API_KEY": "must-not-inherit"}, command=(sys.executable, "-u", "-c", code))


PREFIX = "import sys,json,time,os; r=json.loads(sys.stdin.readline()); "
DONE = "print(json.dumps({'type':'done','session_id':r['session_id'],'messages':[],'success':True}),flush=True)"


@pytest.mark.asyncio
async def test_stream_token_before_terminal_and_private_resume(tmp_path, monkeypatch):
    from easel.harnesses.pi import PiAdapter
    gate = tmp_path / "gate"
    code = PREFIX + "print(json.dumps({'type':'token','text':'early'}),flush=True); " + "\nwhile not os.path.exists(" + repr(str(gate)) + "): time.sleep(.01)\n" + DONE
    req = configured(tmp_path, monkeypatch, code)
    events, ids = [], []
    task = asyncio.create_task(PiAdapter().run(req, lambda *v: events.append(v), deny, ids.append))
    for _ in range(100):
        if events: break
        await asyncio.sleep(.01)
    assert ("token", "early") in events and not task.done()
    gate.touch()
    await task
    assert ids[0].startswith("pi:")
    history = tmp_path / "outputs/_harness/pi-sessions" / (ids[0][3:] + ".json")
    assert history.stat().st_mode & 0o777 == 0o600
    resumed = replace(req, native_id=ids[0], command=(sys.executable, "-u", "-c", PREFIX + DONE))
    await PiAdapter().run(resumed, lambda *_: None, deny, lambda v: ids.append(v))
    assert ids[1] == ids[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["", "print(json.dumps({'type':'error','code':'provider_failed','text':'noncommercial-fixture-secret'}),flush=True)", "print(json.dumps({'type':'done','session_id':r['session_id'],'messages':[],'success':False}),flush=True)"])
async def test_missing_terminal_and_provider_failure_are_errors(tmp_path, monkeypatch, terminal):
    from easel.harnesses.pi import PiAdapter
    req = configured(tmp_path, monkeypatch, PREFIX + terminal)
    with pytest.raises(HarnessError) as error:
        await PiAdapter().run(req, lambda *_: None, deny, lambda _: None)
    assert "noncommercial-fixture-secret" not in str(error.value)


@pytest.mark.asyncio
async def test_tools_round_trip_lf_and_sanitized_env(tmp_path, monkeypatch):
    from easel.harnesses.pi import PiAdapter
    code = PREFIX + "assert not any('KEY' in k or 'TOKEN' in k for k in os.environ); assert r['model']['maxTokens'] == 4096; print(json.dumps({'type':'tool_request','id':'t1','name':'write_file','arguments':{'path':'tool.txt','content':'ok'}}),flush=True); result=json.loads(sys.stdin.readline()); assert result['type']=='tool_result' and result['result']['details']['ok']; " + DONE
    req = configured(tmp_path, monkeypatch, code)
    await PiAdapter().run(req, lambda *_: None, deny, lambda _: None)
    assert (tmp_path / "tool.txt").read_text() == "ok"


@pytest.mark.asyncio
async def test_foreign_and_mismatched_session_never_rebound(tmp_path, monkeypatch):
    from easel.harnesses.pi import PiAdapter
    req = configured(tmp_path, monkeypatch, PREFIX + DONE)
    with pytest.raises(HarnessError):
        await PiAdapter().run(replace(req, native_id="8b47b4b8-b2de-4141-8539-54f14e6980de"), lambda *_: None, deny, lambda _: None)
    req = replace(req, command=(sys.executable, "-u", "-c", PREFIX + "r['session_id']='pi:00000000-0000-4000-8000-000000000000'; " + DONE))
    with pytest.raises(HarnessError): await PiAdapter().run(req, lambda *_: None, deny, lambda _: None)


@pytest.mark.asyncio
async def test_cancellation_reaps_owned_child(tmp_path, monkeypatch):
    from easel.harnesses.pi import PiAdapter
    pidfile = tmp_path / "pid"
    code = PREFIX + "open(" + repr(str(pidfile)) + ",'w').write(str(os.getpid())); time.sleep(60)"
    req = configured(tmp_path, monkeypatch, code)
    task = asyncio.create_task(PiAdapter().run(req, lambda *_: None, deny, lambda _: None))
    for _ in range(100):
        if pidfile.exists(): break
        await asyncio.sleep(.01)
    assert pidfile.exists()
    pid = int(pidfile.read_text())
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    with pytest.raises(ProcessLookupError): os.kill(pid, 0)


@pytest.mark.asyncio
async def test_legacy_subscription_rejected_before_credentials(tmp_path, monkeypatch):
    from easel.harnesses.pi import PiAdapter
    req = configured(tmp_path, monkeypatch, PREFIX + DONE)
    with pytest.raises(HarnessError):
        await PiAdapter().run(replace(req, service_source="subscription", subscription_backend="legacy"), lambda *_: None, deny, lambda _: None)


@pytest.mark.asyncio
async def test_central_catalog_ceiling_scope_and_proxy_url(tmp_path, monkeypatch):
    from easel.harnesses import pi
    import easel.platform.central
    import easel.platform.execution_proxy
    order, observed = [], {}
    class Client:
        def __init__(self, _): pass
        def models(self):
            order.append("catalog")
            return {"models": [{"id":"fixture", "limits":{"max_output_tokens":512,"max_input_tokens":2048}}]}
        def task_credential(self, *args):
            order.append("mint")
            observed["mint"] = args
            return {"task_token":"noncommercial-fixture-token", "invocation_id":"fixture-invocation"}
    class Proxy:
        def __init__(self, *args, **kwargs):
            observed["proxy"] = args
            self.base_url, self.local_token = "http://127.0.0.1:12345", "local-fixture-token"
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
    async def worker(self, request, emit, ask, bind, *args): observed["worker"] = args
    monkeypatch.setattr(easel.platform.central, "CentralClient", Client)
    monkeypatch.setattr(easel.platform.execution_proxy, "ExecutionProxy", Proxy)
    monkeypatch.setattr(pi.PiAdapter, "_run", worker)
    scope = {"user_id":"fixture-account"}
    req = RunRequest("fixture",tmp_path,{},model="fixture",service_source="subscription",subscription_backend="central",
                     account_id="fixture-account",platform_url="https://fixture.invalid",central_scope=scope,invocation_key="fixture-key")
    await pi.PiAdapter().run(req, lambda *_: None, deny, lambda _: None)
    assert order == ["catalog", "mint"]
    assert observed["mint"] == ("fixture-account","fixture","https://fixture.invalid",scope,"fixture-key")
    assert observed["worker"] == ("fixture","http://127.0.0.1:12345","local-fixture-token",512,6656)


@pytest.mark.asyncio
async def test_session_private_directory_symlink_rejected(tmp_path, monkeypatch):
    from easel.harnesses.pi import PiAdapter
    req = configured(tmp_path, monkeypatch, PREFIX + DONE)
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "outputs").symlink_to(outside)
    with pytest.raises(HarnessError): await PiAdapter().run(req, lambda *_: None, deny, lambda _: None)
    assert not list(outside.iterdir())


@pytest.mark.asyncio
async def test_claimed_success_with_assistant_error_is_failure(tmp_path, monkeypatch):
    from easel.harnesses.pi import PiAdapter
    code = PREFIX + "print(json.dumps({'type':'done','session_id':r['session_id'],'messages':[{'role':'assistant','content':[],'stopReason':'error'}],'success':True}),flush=True)"
    req = configured(tmp_path, monkeypatch, code)
    with pytest.raises(HarnessError): await PiAdapter().run(req, lambda *_: None, deny, lambda _: None)


@pytest.mark.asyncio
async def test_tool_limit_and_unterminated_jsonline_fail(tmp_path, monkeypatch):
    from easel.harnesses.pi import PiAdapter
    code = PREFIX + "sys.stdout.write(json.dumps({'type':'token','text':'no LF'}));sys.stdout.flush()"
    req = configured(tmp_path, monkeypatch, code)
    with pytest.raises(HarnessError): await PiAdapter().run(req, lambda *_: None, deny, lambda _: None)
    code = PREFIX + "\nfor n in range(17):\n print(json.dumps({'type':'tool_request','id':str(n),'name':'list_files','arguments':{'path':'.'}}),flush=True)\n if n < 16: sys.stdin.readline()\n" + DONE
    with pytest.raises(HarnessError): await PiAdapter().run(replace(req,command=(sys.executable,"-u","-c",code)),lambda *_: None,deny,lambda _: None)


@pytest.mark.parametrize("scenario", ["tool", "error", "abort", "budget", "tool-budget", "invalid-tool-budget"])
def test_official_sdk_offline_worker_contract(scenario):
    import subprocess
    payload = os.environ.get("EASEL_PI_TEST_PAYLOAD")
    if not payload:
        pytest.skip("set EASEL_PI_TEST_PAYLOAD to prepared exact1.4 runtime for offline SDK acceptance")
    directory = Path(payload).resolve()
    nodes = [directory / "node/bin/node", directory / "node"]
    node = next((str(p) for p in nodes if p.is_file()), None)
    if node is None: pytest.fail("prepared runtime must include owned Node")
    result = subprocess.run([node, str(Path(__file__).with_name("pi_worker_fixture.mjs")),str(directory),scenario],
                            capture_output=True,text=True,timeout=15,env={"PATH":"/usr/bin:/bin"})
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["success"] is True


@pytest.mark.asyncio
async def test_custom_session_never_forwards_history_to_changed_provider(tmp_path, monkeypatch):
    from easel.harnesses import pi
    from easel.harnesses.pi import PiAdapter
    history_messages = [{"role":"user","content":"noncommercial private conversation"}]
    code = PREFIX + "print(json.dumps({'type':'done','session_id':r['session_id'],'messages':" + repr(history_messages) + ",'success':True}),flush=True)"
    req = configured(tmp_path,monkeypatch,code)
    ids = []
    await PiAdapter().run(req,lambda *_:None,deny,ids.append)
    file = tmp_path / "outputs/_harness/pi-sessions" / (ids[0][3:] + ".json")
    before = file.read_bytes()
    spawned = tmp_path / "provider-b-spawned"
    dangerous_code = "open(" + repr(str(spawned)) + ",'w').write('started');" + PREFIX + DONE
    monkeypatch.setattr(pi,"require_provider",lambda _:{"model":"fixture","base_url":"https://other-fixture.invalid/v1","api_key":"rotated-noncommercial-fixture"})
    with pytest.raises(HarnessError):
        await PiAdapter().run(replace(req,native_id=ids[0],command=(sys.executable,"-u","-c",dangerous_code)),lambda *_:None,deny,lambda _:None)
    assert not spawned.exists(), "changed provider must be rejected before worker/model POST"
    assert file.read_bytes() == before


@pytest.mark.asyncio
async def test_custom_session_same_canonical_provider_allows_key_rotation(tmp_path,monkeypatch):
    from easel.harnesses import pi
    from easel.harnesses.pi import PiAdapter
    req = configured(tmp_path,monkeypatch,PREFIX + DONE)
    monkeypatch.setattr(pi,"require_provider",lambda _:{"model":"fixture","base_url":"https://FIXTURE.invalid:443/v1/","api_key":"first-noncommercial-fixture"})
    ids=[]
    await PiAdapter().run(req,lambda *_:None,deny,ids.append)
    monkeypatch.setattr(pi,"require_provider",lambda _:{"model":"fixture","base_url":"https://fixture.invalid/v1","api_key":"second-noncommercial-fixture"})
    await PiAdapter().run(replace(req,native_id=ids[0]),lambda *_:None,deny,ids.append)
    assert len(ids)==2 and ids[0]==ids[1]
