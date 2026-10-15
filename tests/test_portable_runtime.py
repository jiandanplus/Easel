"""Portable roots and deterministic engine selection; no engine is executed."""
import hashlib
import importlib
import json
import os
from pathlib import Path

import pytest
from easel.harnesses.base import HarnessError

@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for key in ("EASEL_RUNTIME_MANIFEST", "EASEL_CODEX_EXECUTABLE", "EASEL_RESOURCE_ROOT", "EASEL_WORKSPACE_ROOT", "CODEX_HOME"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PATH", str(tmp_path / "empty-path"))


def bundle(tmp_path, monkeypatch):
    from easel.harnesses import runtime
    monkeypatch.setattr(runtime.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(runtime.platform, "machine", lambda: "arm64")
    executable = tmp_path / "bin" / "codex"
    executable.parent.mkdir()
    executable.write_bytes(b"fake executable")
    executable.chmod(0o755)
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    data = {"schema_version": 1, "platform": "darwin", "architecture": "arm64",
            "codex": {"version": "0.114.0", "executable": "bin/codex", "sha256": digest},
            "files": {"bin/codex": digest}}
    manifest = tmp_path / "runtime.json"
    manifest.write_text(json.dumps(data))
    monkeypatch.setenv("EASEL_RUNTIME_MANIFEST", str(manifest))
    return manifest, executable, data


def test_roots_default_and_explicit(monkeypatch, tmp_path):
    from easel.paths import workspace_root, resource_root
    assert workspace_root() == Path(__file__).resolve().parents[1]
    assert resource_root() == workspace_root()
    monkeypatch.setenv("EASEL_WORKSPACE_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("EASEL_RESOURCE_ROOT", str(tmp_path / "resources"))
    assert workspace_root() == tmp_path / "data"
    assert resource_root() == tmp_path / "resources"


def test_web_bootstrap_expands_quoted_tilde_roots(tmp_path, monkeypatch):
    import ast
    from easel.paths import workspace_root, resource_root

    home = tmp_path / "temporary-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("EASEL_WORKSPACE_ROOT", "~/portable-workspace")
    monkeypatch.setenv("EASEL_RESOURCE_ROOT", "~/portable-resources")
    # Execute only the bootstrap root assignments, avoiding app imports/startup.
    app_path = Path(__file__).resolve().parents[1] / "web/app.py"
    tree = ast.parse(app_path.read_text())
    roots = ast.Module(body=[node for node in tree.body
                            if isinstance(node, ast.Assign)
                            and any(isinstance(target, ast.Name)
                                    and target.id in {"PROJECT_ROOT", "RESOURCE_ROOT"}
                                    for target in node.targets)], type_ignores=[])
    namespace = {"Path": Path, "os": os, "__file__": str(app_path)}
    exec(compile(roots, str(app_path), "exec"), namespace)
    assert namespace["PROJECT_ROOT"] == workspace_root() == home / "portable-workspace"
    assert namespace["RESOURCE_ROOT"] == resource_root() == home / "portable-resources"


def test_resource_instructions_fallback(tmp_path, monkeypatch):
    from easel.harnesses.workspace import workspace_documents
    folder = tmp_path / "resources" / "agents" / "workspace"
    folder.mkdir(parents=True)
    (folder / "AGENTS.md").write_text("resource instruction")
    monkeypatch.setenv("EASEL_RESOURCE_ROOT", str(tmp_path / "resources"))
    assert workspace_documents(tmp_path / "data") == [folder / "AGENTS.md"]


def test_bundle_command_and_private_environment(tmp_path, monkeypatch):
    from easel.harnesses.commands import base_command
    from easel.harnesses.runtime import codex_environment, resolve_codex
    manifest, executable, data = bundle(tmp_path, monkeypatch)
    assert base_command("codex") == [str(executable)]
    assert resolve_codex().origin == "bundled"
    assert resolve_codex().version == "0.114.0"
    original = {"CODEX_HOME": "/never/read", "OTHER": "kept"}
    env = codex_environment(original, tmp_path / "workspace")
    assert env["CODEX_HOME"] == str(tmp_path / "workspace/outputs/_harness/codex-home")
    assert Path(env["CODEX_HOME"]).stat().st_mode & 0o777 == 0o700
    assert original["CODEX_HOME"] == "/never/read"


@pytest.mark.parametrize("failure", ["missing", "json", "version", "platform", "arch", "hash", "payload", "permission", "traversal", "absolute", "symlink", "extra_hash"])
def test_invalid_bundle_never_falls_back(tmp_path, monkeypatch, failure):
    from easel.harnesses.commands import base_command
    manifest, executable, data = bundle(tmp_path, monkeypatch)
    if failure == "missing": manifest.unlink()
    elif failure == "json": manifest.write_text("{")
    elif failure == "version": data["schema_version"] = 99
    elif failure == "platform": data["platform"] = "linux"
    elif failure == "arch": data["architecture"] = "x86_64"
    elif failure == "hash": executable.write_bytes(b"corrupt")
    elif failure == "payload": executable.unlink()
    elif failure == "permission": executable.chmod(0o644)
    elif failure in ("traversal", "absolute"):
        data["codex"]["executable"] = "../codex" if failure == "traversal" else str(executable)
    elif failure == "symlink":
        outside = tmp_path.parent / (tmp_path.name + "-outside")
        outside.write_bytes(executable.read_bytes())
        executable.unlink()
        executable.symlink_to(outside)
    elif failure == "extra_hash":
        data["files"]["resources/missing"] = "0" * 64
    if failure not in ("missing", "json"): manifest.write_text(json.dumps(data))
    with pytest.raises(HarnessError): base_command("codex")


def test_override_and_source_external(tmp_path, monkeypatch):
    from easel.harnesses.commands import base_command
    from easel.harnesses.runtime import codex_environment
    monkeypatch.setattr("easel.harnesses.commands.shutil.which", lambda name: "/external/" + name)
    assert base_command("codex") == ["/external/codex"]
    assert codex_environment({"CODEX_HOME": "existing"}, tmp_path) == {"CODEX_HOME": "existing"}
    for value in ("codex", str(tmp_path / "missing")):
        monkeypatch.setenv("EASEL_CODEX_EXECUTABLE", value)
        with pytest.raises(HarnessError): base_command("codex")
    executable = tmp_path / "codex"
    executable.write_text("fake")
    executable.chmod(0o755)
    monkeypatch.setenv("EASEL_CODEX_EXECUTABLE", str(executable))
    assert base_command("codex") == [str(executable)]


def test_all_workspace_modules_honor_root(tmp_path, monkeypatch):
    monkeypatch.setenv("EASEL_WORKSPACE_ROOT", str(tmp_path))
    for name in ("easel.cli", "easel.direct_api", "easel.persona", "easel.commands.doctor", "easel.commands.gateway", "easel.commands.skill"):
        module = importlib.import_module(name)
        previous = dict(vars(module))
        importlib.reload(module)
        try: assert module.PROJECT_ROOT == tmp_path, name
        finally:
            vars(module).clear()
            vars(module).update(previous)


def test_web_import_uses_resource_shared_modules(tmp_path):
    # Verify the immutable Python import path separately from writable skills.
    source = (Path(__file__).resolve().parents[1] / "web/app.py").read_text()
    assert 'SHARED_SCRIPTS = RESOURCE_ROOT / "skills" / "shared" / "scripts"' in source


def test_web_launch_uses_immutable_script(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import easel.cli as cli
    resource = tmp_path / "resource"
    (resource / "web").mkdir(parents=True)
    (resource / "web/app.py").write_text("# no execution")
    monkeypatch.setenv("EASEL_RESOURCE_ROOT", str(resource))
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path / "data")
    calls = []
    monkeypatch.setattr(cli.subprocess, "run", lambda command, **kw: (calls.append((command, kw)) or SimpleNamespace(returncode=0)))
    assert cli.cmd_web(SimpleNamespace(port=7860)) == 0
    assert calls[0][0][1] == str(resource / "web/app.py")
    assert calls[0][1]["cwd"] == str(tmp_path / "data")


def test_private_home_rejects_escape_before_creating_outside(tmp_path, monkeypatch):
    from easel.harnesses.runtime import codex_environment
    bundle(tmp_path, monkeypatch)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "outputs").symlink_to(outside, target_is_directory=True)
    with pytest.raises(HarnessError): codex_environment({}, workspace)
    assert list(outside.iterdir()) == []


def test_describe_bundle_failure_has_origin_without_secret(tmp_path, monkeypatch):
    from easel.harnesses import describe
    monkeypatch.setenv("EASEL_RUNTIME_MANIFEST", str(tmp_path / "secret-key"))
    result = describe("codex")
    assert result["origin"] == "bundled"
    assert result["version"] == ""
    assert not result["installed"]
    assert "secret-key" not in result["resolution_error"]


def test_verified_payload_is_rechecked_after_mutation(tmp_path, monkeypatch):
    from easel.harnesses.commands import base_command
    manifest, executable, data = bundle(tmp_path, monkeypatch)
    assert base_command("codex") == [str(executable)]
    executable.write_bytes(b"tampered data!!")
    with pytest.raises(HarnessError): base_command("codex")


@pytest.mark.parametrize("route", ["task", "models"])
def test_model_and_task_use_same_bundle_and_private_home(tmp_path, monkeypatch, route):
    import asyncio
    from contextlib import asynccontextmanager
    from easel.harnesses.base import RunRequest
    from easel.harnesses import codex, models
    manifest, executable, data = bundle(tmp_path, monkeypatch)
    observed = []

    class StopProbe(Exception): pass

    @asynccontextmanager
    async def capture(request, command, *args):
        observed.append((request, command))
        raise StopProbe
        yield

    request = RunRequest("never executes", tmp_path / "workspace", {"CODEX_HOME": "outside"})
    async def run():
        with pytest.raises(StopProbe):
            if route == "task":
                monkeypatch.setattr(codex, "connection", capture)
                await codex.CodexAdapter().run(request, None, None, None)
            else:
                monkeypatch.setattr(models, "RPC", capture)
                await models.available_models("codex", request)
    asyncio.run(run())
    effective, command = observed[0]
    assert command == [str(executable), "app-server"]
    assert effective.env["CODEX_HOME"] == str(request.cwd / "outputs/_harness/codex-home")
    assert request.env["CODEX_HOME"] == "outside"
