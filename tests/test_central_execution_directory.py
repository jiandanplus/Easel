"""Fresh private record layout and single-response rejection regressions."""
import asyncio
import json
import os
import stat
import time
import uuid

import httpx
import pytest

from easel.harnesses.base import HarnessError
from easel.platform.execution_proxy import ExecutionProxy
from easel.platform.execution_records import operation_owner, read_operations


def test_fresh_workspace_operation_is_discoverable_for_its_owner(tmp_path):
    outputs = tmp_path / "outputs"
    harness = outputs / "_harness"
    harness.mkdir(parents=True)
    outputs.chmod(0o755)
    harness.chmod(0o775)
    directory = harness / "central-execution"
    scope = {"user_id": "fixture-user", "product_id": "easel", "tenant_id": "fixture-tenant",
             "billing_account_id": "fixture-wallet", "session_id": "fixture-session", "authorization_epoch": 1}
    invocation = str(uuid.uuid4())
    origin = "https://central.test"
    credential = {"task_token": "PRIVATE_FIXTURE_TASK_TOKEN", "invocation_id": invocation,
                  "model": "fixture", "scope": scope, "funding_mode": "test", "expires_at": time.time() + 300}

    async def run():
        calls = []
        async def central(request):
            calls.append(request)
            return httpx.Response(200, json={"status": "completed"})
        async with ExecutionProxy(directory, origin, credential["task_token"], "fixture", invocation,
                                  credential=credential, transport=httpx.MockTransport(central)) as proxy:
            async with httpx.AsyncClient(trust_env=False) as client:
                response = await client.post(proxy.base_url + "/responses", json={"model": "fixture"},
                                             headers={"Authorization": "Bearer " + proxy.local_token})
                assert response.status_code == 200
            async def wait_for_record():
                while proxy.tasks or not any(json.loads(path.read_text())["status"] == "delivered"
                                             for path in proxy.directory.glob("*.json")):
                    await asyncio.sleep(.005)
            await asyncio.wait_for(wait_for_record(), 2)
        rows, truncated = read_operations(tmp_path, operation_owner(origin, scope))
        assert len(rows) == 1
        assert rows[0]["local_status"] == "delivered"
        assert rows[0]["invocation_id"] == invocation
        assert not truncated and len(calls) == 1

    # Reproduce the normal fresh-workspace mkdir mode regardless of test runner umask.
    previous_umask = os.umask(0o022)
    try:
        asyncio.run(run())
    finally:
        os.umask(previous_umask)
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((directory / "operations").stat().st_mode) == 0o700
    assert stat.S_IMODE(outputs.stat().st_mode) == 0o755
    assert stat.S_IMODE(harness.stat().st_mode) == 0o775


def test_existing_owned_private_directories_are_repaired_without_touching_legacy(tmp_path):
    directory = tmp_path / "central-execution"
    operations = directory / "operations"
    operations.mkdir(parents=True)
    directory.chmod(0o755)
    operations.chmod(0o755)
    legacy = operations / "legacy.json"
    legacy.write_text('{"status":"pending"}')
    before = legacy.stat()

    async def run():
        async with ExecutionProxy(directory, "https://central.test", "PRIVATE_FIXTURE_TASK_TOKEN", "fixture", "inv"):
            pass
    asyncio.run(run())
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(operations.stat().st_mode) == 0o700
    assert legacy.read_text() == '{"status":"pending"}'
    assert legacy.stat().st_mtime_ns == before.st_mtime_ns
    assert stat.S_IMODE(legacy.stat().st_mode) == stat.S_IMODE(before.st_mode)


@pytest.mark.parametrize("part", ["base", "operations"])
@pytest.mark.parametrize("kind", ["symlink", "file", "foreign_owner"])
def test_private_directory_rejects_unsafe_existing_path(tmp_path, monkeypatch, part, kind):
    directory = tmp_path / "central-execution"
    path = directory if part == "base" else directory / "operations"
    if part == "operations":
        directory.mkdir(mode=0o700)
    target = tmp_path / "unrelated-target"
    target.mkdir(mode=0o755)
    if kind == "symlink":
        path.symlink_to(target, target_is_directory=True)
    elif kind == "file":
        path.write_text("unrelated file")
    else:
        path.mkdir(mode=0o755)
        actual_fstat = os.fstat
        foreign_inode = path.stat().st_ino
        def fstat(descriptor):
            info = actual_fstat(descriptor)
            if info.st_ino == foreign_inode:
                values = list(info)
                values[4] = os.getuid() + 1
                return os.stat_result(values)
            return info
        monkeypatch.setattr(os, "fstat", fstat)
    async def run():
        with pytest.raises(HarnessError, match="私有目录无效"):
            async with ExecutionProxy(directory, "https://central.test", "PRIVATE_FIXTURE_TASK_TOKEN", "fixture", "inv"):
                pytest.fail("unsafe path must fail before starting the proxy")
    asyncio.run(run())
    assert stat.S_IMODE(target.stat().st_mode) == 0o755
    if kind == "file":
        assert path.read_text() == "unrelated file"
    elif kind == "foreign_owner":
        assert stat.S_IMODE(path.stat().st_mode) == 0o755


@pytest.mark.parametrize("status", [404, 403, 429, 502])
@pytest.mark.parametrize("failure", [TimeoutError, OSError])
def test_rejection_failed_first_drain_never_writes_second_status(tmp_path, status, failure):
    async def run():
        proxy = ExecutionProxy(tmp_path, "https://central.test", "PRIVATE_FIXTURE_TASK_TOKEN", "fixture", "inv")
        proxy.authority = "127.0.0.1:1234"
        if status == 429:
            proxy.accepted = 32
        elif status == 502:
            proxy._expires_at = 0
        method = "GET" if status == 404 else "POST"
        authorization = "invalid" if status == 403 else "Bearer " + proxy.local_token
        body = b'{"model":"fixture"}'
        reader = asyncio.StreamReader()
        reader.feed_data((f"{method} /responses HTTP/1.1\r\nHost: {proxy.authority}\r\n"
                          f"Authorization: {authorization}\r\nContent-Type: application/json\r\n"
                          f"Content-Length: {len(body)}\r\n\r\n").encode() + body)
        class Writer:
            def __init__(self):
                self.output, self.closed = [], False
            def write(self, data):
                self.output.append(data)
            async def drain(self):
                if len(self.output) == 1:
                    raise failure("fixture failed first drain")
            def close(self):
                self.closed = True
            async def wait_closed(self):
                pass
        writer = Writer()
        await proxy._handle(reader, writer)
        wire = b"".join(writer.output)
        assert wire.startswith(f"HTTP/1.1 {status} ".encode())
        assert wire.count(b"HTTP/1.1") == 1
        assert b"event: error" not in wire
        assert writer.closed and not proxy.tasks
    asyncio.run(run())
