"""Workspace tool security fixtures; never invokes provider or server."""
from dataclasses import replace
from pathlib import Path
import os
import pytest
from easel.harnesses.base import RunRequest


async def deny(questions): return {"permission": ["拒绝"]}
async def allow(questions): return {"permission": ["允许本次"]}


def request(tmp_path, mode="workspace-write"):
    return RunRequest("fixture", tmp_path, {"PATH": "/usr/bin:/bin", "OPENAI_API_KEY": "fixture-secret", "CUSTOM_SECRET": "fixture"}, sandbox=mode)


@pytest.mark.asyncio
async def test_workspace_files_and_readonly(tmp_path):
    from easel.harnesses.pi_tools import execute_tool
    req = request(tmp_path)
    assert (await execute_tool(req, "write_file", {"path": "hello.txt", "content": "hello"}, deny))["ok"]
    assert (await execute_tool(req, "read_file", {"path": "hello.txt"}, deny))["text"] == "hello"
    assert "hello.txt" in (await execute_tool(req, "list_files", {"path": "."}, deny))["text"]
    assert not (await execute_tool(replace(req, sandbox="read-only"), "write_file", {"path": "hello.txt", "content": "bad"}, deny))["ok"]
    assert (tmp_path / "hello.txt").read_text() == "hello"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["../outside", "/etc/passwd", ".env", ".env.local", ".git/config", "outputs/_harness/bundled-provider.json", "outputs/_platform/session.json", ".aws/credentials", "outputs/_harness/pi-sessions/x.json"])
async def test_protected_paths(tmp_path, path):
    from easel.harnesses.pi_tools import execute_tool
    for name in ("read_file", "write_file", "list_files"):
        assert not (await execute_tool(request(tmp_path), name, {"path": path, "content": "bad"}, deny))["ok"]


@pytest.mark.asyncio
async def test_symlink_traversal_and_listing_redaction(tmp_path):
    from easel.harnesses.pi_tools import execute_tool
    (tmp_path / ".env").write_text("private")
    (tmp_path / "outputs/_harness").mkdir(parents=True)
    (tmp_path / "secret-link").symlink_to(tmp_path / ".env")
    assert not (await execute_tool(request(tmp_path), "read_file", {"path": "secret-link"}, deny))["ok"]
    listing = await execute_tool(request(tmp_path), "list_files", {"path": "."}, deny)
    assert ".env" not in listing["text"] and "secret-link" not in listing["text"]


@pytest.mark.asyncio
async def test_shell_requires_approval_supported_sandbox_and_clean_env(tmp_path, monkeypatch):
    from easel.harnesses import pi_tools
    asked = []
    async def approval(questions):
        asked.extend(questions)
        return {"permission": ["拒绝"]}
    monkeypatch.setattr(pi_tools, "shell_supported", lambda: True)
    assert not (await pi_tools.execute_tool(request(tmp_path), "run_command", {"command": "echo hi"}, approval))["ok"]
    assert asked
    monkeypatch.setattr(pi_tools, "shell_supported", lambda: False)
    assert not (await pi_tools.execute_tool(request(tmp_path), "run_command", {"command": "echo hi"}, allow))["ok"]
    env = pi_tools.clean_environment()
    assert "OPENAI_API_KEY" not in env and "CUSTOM_SECRET" not in env
    assert env["PATH"] == "/usr/bin:/bin:/usr/sbin:/sbin"
    assert env.get("PYTHONNOUSERSITE") == "1"


@pytest.mark.asyncio
async def test_readonly_shell_denied_before_approval(tmp_path):
    from easel.harnesses.pi_tools import execute_tool
    async def never(_): raise AssertionError("must deny first")
    assert not (await execute_tool(request(tmp_path, "read-only"), "run_command", {"command": "touch x"}, never))["ok"]


@pytest.mark.asyncio
async def test_file_byte_limit_and_nonregular(tmp_path):
    from easel.harnesses.pi_tools import execute_tool
    (tmp_path / "large").write_bytes(b"a" * (256 * 1024 + 1))
    assert not (await execute_tool(request(tmp_path), "read_file", {"path": "large"}, deny))["ok"]
    os.mkfifo(tmp_path / "fifo")
    assert not (await execute_tool(request(tmp_path), "read_file", {"path": "fifo"}, deny))["ok"]


@pytest.mark.asyncio
async def test_supported_shell_enforces_os_boundary(tmp_path):
    from easel.harnesses.pi_tools import execute_tool, shell_supported
    if not shell_supported(): pytest.skip("macOS sandbox-exec only")
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.write_text("fixture-outside")
    (tmp_path / ".ENV.fixture").write_text("fixture-private")
    result = await execute_tool(request(tmp_path), "run_command", {"command": "printf public > public.txt; cat public.txt"}, allow)
    assert result["ok"] and result["text"] == "public"
    for command in ("cat .ENV.fixture", "cat " + str(outside), "printf denied > " + str(outside), "cat outputs/_harness/bundled-provider.json"):
        result = await execute_tool(request(tmp_path), "run_command", {"command": command}, allow)
        assert not result["ok"], (command, result)
    assert outside.read_text() == "fixture-outside"


@pytest.mark.asyncio
async def test_hardlinks_cannot_mutate_or_read_private_inode(tmp_path):
    from easel.harnesses.pi_tools import execute_tool
    (tmp_path / ".env").write_text("fixture-private")
    os.link(tmp_path / ".env", tmp_path / "alias")
    for name in ("read_file", "write_file"):
        assert not (await execute_tool(request(tmp_path), name, {"path":"alias","content":"bad"}, deny))["ok"]
    assert (tmp_path / ".env").read_text() == "fixture-private"


@pytest.mark.asyncio
async def test_shell_trusted_bundled_python_readonly(tmp_path, monkeypatch):
    from easel.harnesses.pi_tools import execute_tool, shell_supported
    if not shell_supported(): pytest.skip("macOS sandbox-exec only")
    resources = tmp_path.parent / (tmp_path.name + "-resources")
    binary = resources / "python/bin/python3"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\nprintf bundled-python")
    binary.chmod(0o755)
    monkeypatch.setenv("EASEL_RESOURCE_ROOT", str(resources))
    result = await execute_tool(request(tmp_path), "run_command", {"command":"python3"}, allow)
    assert result["ok"] and result["text"] == "bundled-python"
    result = await execute_tool(request(tmp_path), "run_command", {"command":"printf changed > " + str(binary)}, allow)
    assert not result["ok"]


@pytest.mark.asyncio
async def test_shell_symlink_and_casefold_private_state(tmp_path):
    from easel.harnesses.pi_tools import execute_tool, shell_supported
    if not shell_supported(): pytest.skip("macOS sandbox-exec only")
    (tmp_path / "outputs/_HARNESS").mkdir(parents=True)
    (tmp_path / "outputs/_HARNESS/fixture").write_text("fixture-private")
    (tmp_path / ".env.fixture").write_text("fixture-env")
    (tmp_path / "alias").symlink_to(tmp_path / ".env.fixture")
    for command in ("cat alias", "cat outputs/_HARNESS/fixture", "cat ../" + tmp_path.name + "/.env.fixture"):
        result = await execute_tool(request(tmp_path), "run_command", {"command":command}, allow)
        assert not result["ok"] and "fixture-private" not in result["text"] and "fixture-env" not in result["text"]


@pytest.mark.asyncio
async def test_shell_rejects_linked_resource_root(tmp_path, monkeypatch):
    from easel.harnesses.pi_tools import execute_tool, shell_supported
    if not shell_supported(): pytest.skip("macOS sandbox-exec only")
    actual = tmp_path.parent / (tmp_path.name + "-actual")
    (actual / "python/bin").mkdir(parents=True)
    (actual / "python/bin/python3").write_text("fixture")
    link = tmp_path.parent / (tmp_path.name + "-link")
    link.symlink_to(actual, target_is_directory=True)
    monkeypatch.setenv("EASEL_RESOURCE_ROOT", str(link / "python/.."))
    result = await execute_tool(request(tmp_path), "run_command", {"command":"echo unsafe"}, allow)
    assert not result["ok"]


@pytest.mark.asyncio
async def test_shell_internal_python_alias_only(tmp_path, monkeypatch):
    from easel.harnesses.pi_tools import execute_tool, shell_supported
    if not shell_supported(): pytest.skip("macOS sandbox-exec only")
    resources = tmp_path.parent / (tmp_path.name + "-resources-alias")
    binary = resources / "python/bin/python3.12"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\nprintf bundled-alias")
    binary.chmod(0o755)
    alias = binary.with_name("python3")
    alias.symlink_to("python3.12")
    monkeypatch.setenv("EASEL_RESOURCE_ROOT", str(resources))
    result = await execute_tool(request(tmp_path), "run_command", {"command":"python3"}, allow)
    assert result["ok"] and result["text"] == "bundled-alias"
    alias.unlink()
    alias.symlink_to("/bin/echo")
    result = await execute_tool(request(tmp_path), "run_command", {"command":"python3"}, allow)
    assert not result["ok"]


@pytest.mark.asyncio
@pytest.mark.parametrize("private_dir", [".easel-portable", ".EASEL-PORTABLE"])
async def test_portable_controller_is_private_to_all_file_tools(tmp_path, private_dir):
    from easel.harnesses.pi_tools import execute_tool
    directory = tmp_path / private_dir
    directory.mkdir()
    record = directory / "instance.json"
    record.write_text('{"token":"fixture-controller-private"}')
    original = record.read_text()
    req = replace(request(tmp_path), env={})
    for name, arguments in [("read_file", {"path":private_dir + "/instance.json"}),
                            ("list_files", {"path":private_dir}),
                            ("write_file", {"path":private_dir + "/instance.json","content":"changed"})]:
        result = await execute_tool(req, name, arguments, deny)
        assert not result["ok"], (name, result)
        assert "fixture-controller-private" not in result["text"]
    assert record.read_text() == original
    listing = await execute_tool(req, "list_files", {"path":"."}, deny)
    assert private_dir not in listing["text"]
    (tmp_path / "controller-alias").symlink_to(directory, target_is_directory=True)
    assert not (await execute_tool(req, "read_file", {"path":"controller-alias/instance.json"}, deny))["ok"]
    assert not (await execute_tool(req, "read_file", {"path":"x/../" + private_dir + "/instance.json"}, deny))["ok"]
    assert (await execute_tool(req, "write_file", {"path":"public.txt","content":"public"}, deny))["ok"]
    assert (await execute_tool(req, "read_file", {"path":"public.txt"}, deny))["text"] == "public"


@pytest.mark.asyncio
@pytest.mark.parametrize("private_dir", [".easel-portable", ".EASEL-PORTABLE"])
async def test_portable_controller_is_private_to_actual_shell(tmp_path, private_dir):
    from easel.harnesses.pi_tools import execute_tool, shell_supported
    if not shell_supported(): pytest.skip("macOS sandbox-exec only")
    directory = tmp_path / private_dir
    directory.mkdir()
    record = directory / "instance.json"
    record.write_text("fixture-controller-private")
    (tmp_path / "controller-alias").symlink_to(directory, target_is_directory=True)
    commands = ["cat " + private_dir + "/instance.json", "ls " + private_dir,
                "printf changed > " + private_dir + "/instance.json", "cat controller-alias/instance.json",
                "cat ../" + tmp_path.name + "/" + private_dir + "/instance.json"]
    for command in commands:
        result = await execute_tool(request(tmp_path), "run_command", {"command":command}, allow)
        assert not result["ok"], (command,result)
        assert "fixture-controller-private" not in result["text"]
    assert record.read_text() == "fixture-controller-private"


MANAGED_PRIVATE_PATHS = ["cookies.json", "COOKIES.JSON", "auth.json", "credentials.json", "storage_state.json",
    "skills/builtin/skill-wechat-publisher/wechat-publisher.yaml",
    "skills/builtin/skill-wechat-publisher/wechat-publisher.yaml.tmp",
    "skills/builtin/skill-wechat-publisher/scripts/.token_cache.json",
    "skills/builtin/skill-wechat-publisher/scripts/.TOKEN_CACHE_web.json",
    "outputs/_sessions/harness/binding.json", "outputs/_SESSIONS/history.jsonl",
    "outputs/_login/status.json", "outputs/_publish/verification.json", "outputs/_debug/history.log"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", MANAGED_PRIVATE_PATHS)
async def test_managed_credentials_and_state_file_tools_denied(tmp_path, path):
    from easel.harnesses.pi_tools import execute_tool
    file = tmp_path / path
    file.parent.mkdir(parents=True,exist_ok=True)
    file.write_text("noncommercial-private-fixture")
    for name in ("read_file","write_file","list_files"):
        result = await execute_tool(replace(request(tmp_path),env={}),name,{"path":path,"content":"changed"},deny)
        assert not result["ok"],(name,path,result)
    assert file.read_text() == "noncommercial-private-fixture"


@pytest.mark.asyncio
async def test_managed_credentials_and_state_actual_shell_denied(tmp_path):
    from easel.harnesses.pi_tools import execute_tool, shell_supported
    if not shell_supported(): pytest.skip("macOS sandbox-exec only")
    for path in MANAGED_PRIVATE_PATHS:
        file = tmp_path / path
        file.parent.mkdir(parents=True,exist_ok=True)
        file.write_text("noncommercial-private-fixture")
        for command in ("cat " + path, "printf changed > " + path):
            result = await execute_tool(request(tmp_path),"run_command",{"command":command},allow)
            assert not result["ok"],(command,result)
            assert "noncommercial-private-fixture" not in result["text"]
        assert file.read_text() == "noncommercial-private-fixture"


@pytest.mark.asyncio
@pytest.mark.parametrize("unicode_paths", [False, True])
async def test_actual_packaged_python_in_relocated_nested_sandbox(tmp_path,monkeypatch,unicode_paths):
    import json
    import shlex
    import shutil
    from easel.harnesses.pi_tools import execute_tool,shell_supported
    if not shell_supported(): pytest.skip("macOS sandbox-exec only")
    value = os.environ.get("EASEL_PI_TEST_RESOURCES")
    if not value: pytest.skip("set EASEL_PI_TEST_RESOURCES to the actual built app Resources")
    source = Path(value).resolve(strict=True)
    if not (source / "python/bin/python3").is_file(): pytest.fail("actual packaged Python missing")
    resources = tmp_path / ("中文 空间" if unicode_paths else "ascii-resources") / "Easel.app/Contents/Resources"
    resources.mkdir(parents=True)
    shutil.copytree(source / "python",resources / "python",symlinks=True,
                    ignore=shutil.ignore_patterns("__pycache__","*.pyc"))
    workspace = tmp_path / ("深层/独立工作区" if unicode_paths else "nested/workspace")
    workspace.mkdir(parents=True)
    outside = workspace.parent / "outside.txt"
    outside.write_text("noncommercial-outside-fixture")
    (workspace / ".env.fixture").write_text("noncommercial-private-fixture")
    (resources / "resource-write-sentinel.txt").write_text("unchanged")
    monkeypatch.setenv("EASEL_RESOURCE_ROOT",str(resources))
    req = replace(request(workspace),env={})
    code = 'import json,sys;print(json.dumps({"executable":sys.executable,"marker":"EASEL_ACTUAL_PACKAGED_PYTHON_OK"}))'
    result = await execute_tool(req,"run_command",{"command":"python3 -I -B -c " + shlex.quote(code)},allow)
    assert result["ok"],result
    payload = json.loads(result["text"])
    assert payload["marker"] == "EASEL_ACTUAL_PACKAGED_PYTHON_OK"
    assert Path(payload["executable"]).resolve().is_relative_to(resources / "python")
    for command in ("cat .env.fixture","cat " + shlex.quote(str(outside)),
                    "printf changed > " + shlex.quote(str(outside)),
                    "printf changed > " + shlex.quote(str(resources / "resource-write-sentinel.txt")),
                    "ls " + shlex.quote(str(workspace.parent))):
        denied = await execute_tool(req,"run_command",{"command":command},allow)
        assert not denied["ok"],(command,denied)
    assert outside.read_text() == "noncommercial-outside-fixture"
    assert (resources / "resource-write-sentinel.txt").read_text() == "unchanged"
