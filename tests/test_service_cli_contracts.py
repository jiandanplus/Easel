import pytest
from types import SimpleNamespace
from unittest.mock import Mock
from easel import cli
from easel.commands import skill, ping
from easel.harnesses import store

@pytest.mark.parametrize("module,command", [(cli, cli.cmd_chat), (skill, skill.cmd_skill), (ping, ping.cmd_ping)])
@pytest.mark.parametrize("runtime", ["api", "openclaw", "codex"])
@pytest.mark.parametrize("source,target", [("subscription", "local"), ("custom", "cloud"), ("subscription", "cloud")])
def test_actual_cli_preflights_before_side_effects(tmp_path, monkeypatch, capsys, module, command, runtime, source, target):
    monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path, raising=False)
    monkeypatch.setattr("easel.paths.workspace_root", lambda: tmp_path)
    config = {"runtime": runtime, "models": {}, "service_source": source, "execution_target": target}
    monkeypatch.setattr(store, "settings", lambda *a: config)
    monkeypatch.setattr(cli.harnesses, "settings", lambda *a: config)
    monkeypatch.setattr(module, "read_settings", lambda: {})
    reject = Mock(side_effect=AssertionError("side effect before route preflight"))
    for owner, names in [(cli, ["_list_personas", "_build_chat_cmd", "stream_chat"]),
                         (skill, ["_find_skill", "_check_profile_exists", "_resolve_input", "_run_via_openclaw"]),
                         (ping, ["stream_chat", "openclaw_base_cmd"])]:
        for name in names:
            monkeypatch.setattr(owner, name, reject)
    monkeypatch.setattr("builtins.input", reject)
    monkeypatch.setattr(cli.subprocess, "run", reject)
    monkeypatch.setattr(ping.urllib.request, "urlopen", reject)
    assert command(SimpleNamespace(name="x", input="x", profile="x")) == 1
    assert "Agent 服务不可用" in capsys.readouterr().out
    assert not reject.called
