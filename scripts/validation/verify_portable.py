"""Local-only packaged HTTP/SSE proof; never calls a real provider."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer


def assert_sse_success(stream, marker):
    events = []
    for block in stream.replace('\r\n', '\n').split('\n\n'):
        event, data = None, []
        for line in block.splitlines():
            if line.startswith('event:'): event = line[6:].strip()
            elif line.startswith('data:'): data.append(line[5:].strip())
        if event: events.append((event, json.loads('\n'.join(data))))
    assert not any(event == 'error' for event, _ in events), events
    assert sum(event == 'done' for event, _ in events) == 1, events
    assert events[-1][0] == 'done', events
    assert marker in ''.join(data for event, data in events if event == 'token' and isinstance(data, str)), events
    return {event: sum(name == event for name, _ in events) for event, _ in events}


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('app',type=Path); args = parser.parse_args()
    app = args.app.resolve()
    root = Path(tempfile.mkdtemp(prefix='easel-portable-proof-'))
    moved = root/'移动 Trial.app'; app.rename(moved)
    workspace, home = root/'工作区', root/'home'; home.mkdir()
    launcher = moved/'Contents/MacOS/Easel'
    env = {'HOME':str(home),'PATH':'/usr/bin:/bin','TMPDIR':str(root),'HTTP_PROXY':'http://127.0.0.1:9','HTTPS_PROXY':'http://127.0.0.1:9','ALL_PROXY':'http://127.0.0.1:9','NO_PROXY':'127.0.0.1,localhost','PYTHONDONTWRITEBYTECODE':'1'}
    spec = importlib.util.spec_from_file_location('dummy',Path(__file__).with_name('verify_codex_gateway.py')); dummy = importlib.util.module_from_spec(spec); spec.loader.exec_module(dummy)
    dummy.TOOL_COMMAND = ".venv/bin/python -c \"from pathlib import Path; p=Path('marker.txt'); p.write_text('"+dummy.MARKER+"'); print(p.read_text())\""
    gateway = ThreadingHTTPServer(('127.0.0.1',0),dummy.Handler)
    threading.Thread(target=gateway.serve_forever,daemon=True).start()
    proc = None
    def launch():
        return subprocess.Popen([str(launcher),'--workspace',str(workspace),'--port','0','--no-browser'],env=env)
    def wait_ready():
        state = workspace/'.easel-portable/instance.json'
        for _ in range(400):
            if proc.poll() is not None: raise RuntimeError((home/'Library/Logs/Easel/launcher.log').read_text())
            if state.exists():
                data=json.loads(state.read_text())
                try:
                    request('/_easel/identity',headers={'X-Easel-Token':data['token']},state=data)
                    return data
                except OSError: pass
            time.sleep(.1)
        raise RuntimeError('startup timed out')
    def request(path, data=None, headers=None, state=None):
        state = state or json.loads((workspace/'.easel-portable/instance.json').read_text())
        req=urllib.request.Request(f'http://127.0.0.1:{state["port"]}'+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json',**(headers or {})})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=120) as response: return response.read().decode()
    report = {'root':str(root)}
    try:
        proc=launch(); state=wait_ready()
        report['http_index']= '<html' in request('/').lower()
        assert report['http_index'], 'HTTP index missing HTML'
        report['static']=bool(urllib.request.build_opener(urllib.request.ProxyHandler({})).open(f'http://127.0.0.1:{state["port"]}/static/easel-icon-transparent.png').read())
        assert report['static'], 'Static asset missing'
        provider={'base_url':f'http://127.0.0.1:{gateway.server_port}/v1','model':'easel-loopback-dummy','api_key':dummy.TOKEN,'secret_action':'replace'}
        saved=request('/api/settings/harness/provider',provider)
        assert dummy.TOKEN not in saved
        request('/api/settings/harness',{'runtime':'codex','sandbox':'workspace-write','codex_auto_approve':True,'service_source':'custom','execution_target':'local'})
        sse=request('/api/chat/stream',{'message':'Execute the supplied harmless local Python marker command.','sessionId':'portable-proof'})
        (root/'stream.txt').write_text(sse)
        assert (workspace/'marker.txt').read_text()==dummy.MARKER, sse
        assert dummy.stats['tool_result_seen'] and dummy.stats['tool_output_marker'] and dummy.stats['bearer_correct'],dummy.stats
        report['sse_events'] = assert_sse_success(sse, dummy.MARKER)
        dummy.stats['marker_verified'] = True
        assert dummy.TOKEN not in sse
        report['native_python_sse']=True; report['dummy_stats']=dummy.stats
        preserved=workspace/'profiles/user.txt'; preserved.write_text('preserve')
        stopped=subprocess.run([str(launcher),'--workspace',str(workspace),'--stop'],env=env,timeout=30)
        assert stopped.returncode==0; proc.wait(timeout=30)
        proc=launch(); wait_ready(); assert preserved.read_text()=='preserve'
        report['restart_preservation']=True
        subprocess.run([str(launcher),'--workspace',str(workspace),'--stop'],env=env,check=True,timeout=30); proc.wait(timeout=30)
        print(json.dumps(report,ensure_ascii=False,indent=2)); (root/'report.json').write_text(json.dumps(report,indent=2))
    finally:
        gateway.shutdown()
        if proc and proc.poll() is None: proc.terminate(); proc.wait(timeout=15)
        moved.rename(app)

if __name__=='__main__': main()
