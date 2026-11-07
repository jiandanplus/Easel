"""Codex config must not split a runtime launcher from its script."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from easel.harnesses.base import RunRequest
from easel.harnesses.runtime import prepare_codex, prepare_subscription


@pytest.fixture(autouse=True)
def external_runtime(monkeypatch):
    monkeypatch.delenv("EASEL_RUNTIME_MANIFEST", raising=False)
    monkeypatch.delenv("EASEL_CODEX_EXECUTABLE", raising=False)


def subscription_request(root, backend):
    return RunRequest(
        "initialization only", root, {"PATH": os.environ.get("PATH", ""), "HOME": str(root)},
        model="fixture", service_source="subscription", subscription_backend=backend,
        account_id="owned-fixture", platform_url="https://central.example.test",
    )


@pytest.mark.parametrize("backend", ["central", "legacy"])
@pytest.mark.parametrize("prefix", [
    ["/owned/codex", "app-server"],
    ["/owned/node", "/owned/codex.js", "app-server"],
])
def test_subscription_config_follows_complete_launcher(tmp_path, monkeypatch, backend, prefix):
    from easel.platform.client import PlatformClient

    monkeypatch.setattr(PlatformClient, "task_credential", lambda *args: "legacy-local-dummy")
    original = list(prefix)
    prepared, command = prepare_subscription(
        subscription_request(tmp_path, backend), prefix,
        proxy_url="http://127.0.0.1:12345", proxy_token="central-local-dummy",
    )
    assert command[:len(prefix)] == prefix
    assert prefix == original
    assert command[len(prefix)] == "-c"
    assert 'model_provider="easel-subscription"' in command
    assert "local-dummy" not in " ".join(command)
    token = "central-local-dummy" if backend == "central" else "legacy-local-dummy"
    assert prepared.env["EASEL_SUBSCRIPTION_API_KEY"] == token
    base_url = "http://127.0.0.1:12345" if backend == "central" else "https://central.example.test/v1"
    assert "model_providers.easel-subscription.base_url=" + json.dumps(base_url) in command


@pytest.mark.parametrize("launcher", ["direct", "node"])
def test_prepared_subscription_launcher_executes_initialization(tmp_path, launcher):
    """Execute a stdio fixture to catch Node interpreting -c as its own option."""
    if launcher == "node":
        node = shutil.which("node")
        if not node:
            pytest.skip("Node launcher regression requires Node.js")
        script = tmp_path / "codex.js"
        script.write_text('''
const readline = require('node:readline');
if (process.argv[2] !== 'app-server' || process.argv[3] !== '-c') process.exit(2);
const lines = readline.createInterface({input: process.stdin});
lines.on('line', line => {
  const request = JSON.parse(line);
  console.log(JSON.stringify({id: request.id, result: {fixture: 'initialized'}}));
});
''')
        prefix = [node, str(script), "app-server"]
    else:
        script = tmp_path / "codex"
        script.write_text(f"#!{sys.executable}\n" + '''
import json
import sys
assert sys.argv[1:3] == ['app-server', '-c']
for line in sys.stdin:
    request = json.loads(line)
    print(json.dumps({'id': request['id'], 'result': {'fixture': 'initialized'}}))
''')
        script.chmod(0o700)
        prefix = [str(script), "app-server"]
    prepared, command = prepare_subscription(
        subscription_request(tmp_path, "central"), prefix,
        proxy_url="http://127.0.0.1:1", proxy_token="fixture-dummy",
    )
    result = subprocess.run(
        command, input=json.dumps({"id": 1, "method": "initialize"}) + "\n",
        env=prepared.env, cwd=tmp_path, text=True, capture_output=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"id": 1, "result": {"fixture": "initialized"}}


def test_external_custom_launcher_is_unchanged(tmp_path):
    request = RunRequest("fixture", tmp_path, {}, model="custom-model")
    command = ["/owned/node", "/owned/codex.js", "app-server"]
    assert prepare_codex(request, command) == (request, command)


def test_bundled_custom_native_configuration_remains_supported(tmp_path, monkeypatch):
    from easel.harnesses import runtime
    from easel.harnesses.provider import save_provider

    monkeypatch.setattr(runtime, "resolve_codex", lambda: runtime.CodexResolution(Path("/owned/codex"), "bundled"))
    save_provider(tmp_path, base_url="http://127.0.0.1:12345/v1", model="fixture",
                  secret_action="replace", api_key="bundled-local-dummy")
    request = RunRequest("initialization only", tmp_path, {}, model="fixture")
    prepared, command = prepare_codex(request, ["/owned/codex", "app-server"])
    assert command[0] == "/owned/codex" and command[-1] == "app-server"
    assert 'model_provider="easel-custom"' in command
    assert prepared.env["EASEL_BUNDLED_API_KEY"] == "bundled-local-dummy"
    assert "bundled-local-dummy" not in " ".join(command)
