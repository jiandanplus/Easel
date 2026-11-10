"""Exact-turn associations are private, bounded and read-only at recovery."""
import asyncio
from hashlib import sha256
import json
import os
import time
from uuid import uuid4

import httpx
import pytest

from easel.platform.central import CentralClient, CentralError
from easel.platform import execution_records as records
from easel.platform.execution_proxy import ExecutionProxy
from tests.test_central_execution_recovery import execution, native_client, owned, record
from tests.test_central_platform_client import auth


def turn(session="session-1", turn_id="turn:1"):
    assert callable(getattr(records, "operation_turn_key", None)), "exact-turn key helper is required"
    return records.operation_turn_key(session, turn_id)


def test_turn_key_is_canonical_private_tuple_not_concatenation():
    assert turn() == sha256(b'["session-1","turn:1"]').hexdigest()
    assert turn("a:b", "c") != turn("a", "b:c")
    assert turn("a", "b") != turn("b", "a")
    assert len(turn("a" * 160, "Z_.:-09")) == 64


@pytest.mark.parametrize("invalid", [None, 1, True, "", "a" * 161, "_first", "-first", ".first", ":first", "a b", "a/b", "a\\b", "a\n", "中文", "aé"])
def test_turn_key_rejects_invalid_identifier_without_echo(invalid):
    for pair in ((invalid, "safe"), ("safe", invalid)):
        with pytest.raises(ValueError, match="^Invalid operation turn$"):
            turn(*pair)


def test_reader_filters_owner_and_turn_before_fifty_cutoff_and_omits_legacy(tmp_path):
    key = turn()
    wanted = [record(tmp_path, owned(tmp_path), turn_key=key)[1] for _ in range(2)]
    for index in range(55):
        path, _ = record(tmp_path, owned(tmp_path), turn_key=turn("unrelated", str(index)))
        os.utime(path, ns=(9_000_000_000_000_000_000 + index, 9_000_000_000_000_000_000 + index))
    record(tmp_path, "f" * 64, turn_key=key)
    record(tmp_path, owned(tmp_path))
    record(tmp_path, owned(tmp_path), turn_key=key, version=1)
    rows, truncated = records.read_operations(tmp_path, owned(tmp_path), turn_key=key)
    assert {row["operation_key"] for row in rows} == {row["operation_key"] for row in wanted}
    assert not truncated and all("turn_key" not in row and "owner_key" not in row for row in rows)
    assert len(records.read_operations(tmp_path, owned(tmp_path))[0]) == 50


def test_turn_reader_drops_malformed_symlink_and_has_unchanged_scan_bounds(tmp_path, monkeypatch):
    key = turn()
    good, row = record(tmp_path, owned(tmp_path), turn_key=key)
    bad, _ = record(tmp_path, owned(tmp_path), turn_key=key, invocation_id="../bad")
    malformed, _ = record(tmp_path, owned(tmp_path), turn_key={"private": True})
    broken, _ = record(tmp_path, owned(tmp_path), turn_key=key)
    broken.write_text("{")
    link = good.parent / (str(uuid4()) + ".json")
    link.symlink_to(good)
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode) for path in (good, bad, malformed, broken)}
    assert records.read_operations(tmp_path, owned(tmp_path), key)[0][0]["operation_key"] == row["operation_key"]
    assert len(records.read_operations(tmp_path, owned(tmp_path), key)[0]) == 1
    monkeypatch.setattr(records, "MAX_SCAN", 2)
    assert records.read_operations(tmp_path, owned(tmp_path), key)[1]
    monkeypatch.setattr(records, "SCAN_SECONDS", -1)
    assert records.read_operations(tmp_path, owned(tmp_path), key) == ([], True)
    assert before == {path: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode) for path in before}
    assert link.is_symlink()


def test_turn_reader_truncates_matching_records_only(tmp_path):
    key = turn()
    for _ in range(51):
        record(tmp_path, owned(tmp_path), turn_key=key)
    rows, truncated = records.read_operations(tmp_path, owned(tmp_path), key)
    assert len(rows) == 50 and truncated


@pytest.mark.parametrize("pair", [(None, "turn"), ("session", None), ("", ""), ("bad/session", "turn"), ("session", "中文")])
def test_invalid_exact_recovery_is_rejected_before_network_and_writes(tmp_path, pair):
    client = native_client(tmp_path, lambda _: pytest.fail("invalid scope must not query central"))
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    with pytest.raises(ValueError, match="^Invalid operation turn$"):
        client.recoveries(*pair)
    assert before == {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}


def test_exact_recovery_get_only_all_turn_operations_no_private_association_or_file_writes(tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        assert request.method == "GET"
        if request.url.path == "/v2/me":
            return httpx.Response(200, json={"scope": auth()["scope"], "account": auth()["account"]})
        assert request.url.path == "/v2/execution-operations"
        return httpx.Response(200, json=execution())
    client = native_client(tmp_path, handler)
    wanted = [record(tmp_path, owned(tmp_path), turn_key=turn())[1] for _ in range(3)]
    record(tmp_path, owned(tmp_path), turn_key=turn("another-session"))
    record(tmp_path, owned(tmp_path), turn_key=turn(turn_id="another-turn"))
    record(tmp_path, "f" * 64, turn_key=turn())
    record(tmp_path, owned(tmp_path))
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in tmp_path.rglob("*") if path.is_file()}
    result = client.recoveries("session-1", "turn:1")
    assert {row["operation_key"] for row in result["recoveries"]} == {row["operation_key"] for row in wanted}
    assert all(row["lookup_status"] == "found" for row in result["recoveries"])
    assert len(calls) == 4 and not result["truncated"]
    assert all(word not in json.dumps(result) for word in ("turn_key", "owner_key", "session-1", "turn:1", "body_hash"))
    assert before == {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in tmp_path.rglob("*") if path.is_file()}


@pytest.mark.parametrize("failure_at", ["identity", "operation"])
def test_exact_recovery_current_account_failure_discards_whole_batch(tmp_path, failure_at):
    calls = []
    def handler(request):
        assert request.method == "GET"
        calls.append(request.url.path)
        if request.url.path == "/v2/me":
            current = auth()
            if failure_at == "identity":
                current["account"]["id"] = "other"
            return httpx.Response(200, json={"scope": current["scope"], "account": current["account"]})
        if len(calls) == 3:
            return httpx.Response(409, json={"error": {"code": "account_changed"}})
        return httpx.Response(200, json=execution())
    client = native_client(tmp_path, handler)
    originals = {path: path.read_bytes() for path, _ in (record(tmp_path, owned(tmp_path), turn_key=turn()) for _ in range(2))}
    with pytest.raises(Exception) as invalid:
        client.recoveries("session-1", "turn:1")
    assert invalid.value.status == 409
    assert invalid.value.code == ("account_changed" if failure_at == "identity" else "platform_unavailable")
    assert len(calls) == (1 if failure_at == "identity" else 3)
    assert originals == {path: path.read_bytes() for path in originals}


@pytest.mark.asyncio
async def test_turn_route_validates_sanitized_400_and_loopback(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from easel.platform.central_web import create_router
    app = FastAPI()
    app.include_router(create_router(tmp_path, lambda: None))
    calls = []
    def recover(client, session_id=None, turn_id=None):
        calls.append((client.store.root, session_id, turn_id))
        return {"recoveries": [], "truncated": False}
    monkeypatch.setattr(CentralClient, "recoveries", recover)
    route = "/api/platform/central/execution-recoveries/turn"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1)), base_url="http://127.0.0.1:8000") as client:
        response = await client.get(route, params={"session_id": "session-1", "turn_id": "turn:1", "owner": "forged"})
        assert response.status_code == 200 and response.json() == {"recoveries": [], "truncated": False}
        for params in ({}, {"session_id": "safe"}, {"session_id": "safe", "turn_id": "secret/invalid"}, {"session_id": "safe", "turn_id": ""}, [("session_id", "a"), ("session_id", "b"), ("turn_id", "c")]):
            response = await client.get(route, params=params)
            assert response.status_code == 400
            assert response.json() == {"detail": {"code": "invalid_request", "message": "请求轮次标识无效"}}
        assert (await client.post(route, json={})).status_code == 405
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("198.51.100.1", 1)), base_url="http://127.0.0.1:8000") as client:
        assert (await client.get(route, params={"session_id": "s", "turn_id": "t"})).status_code == 403
    assert calls == [(tmp_path, "session-1", "turn:1")]


def test_proxy_records_exact_turn_before_send_and_never_raw_identifiers(tmp_path):
    directory = tmp_path / "outputs/_harness/central-execution"
    invocation = str(uuid4())
    credential = dict(task_token="PRIVATE_FIXTURE_TASK_TOKEN", invocation_id=invocation, model="fixture", scope=auth()["scope"], funding_mode="test", expires_at=time.time() + 300)
    async def run():
        observed = []
        async def central(request):
            path = directory / "operations" / (request.headers["Idempotency-Key"] + ".json")
            value = json.loads(path.read_text())
            assert value["turn_key"] == turn() and value["status"] == "sending"
            assert value["version"] == 2 and value["owner_key"] == owned(tmp_path)
            assert "session-1" not in path.read_text() and "turn:1" not in path.read_text()
            observed.append(value["operation_key"])
            return httpx.Response(200, json={"status": "completed"})
        async with ExecutionProxy(directory, "https://central.test", credential["task_token"], "fixture", invocation, credential=credential,
                                  session_key="session-1", turn_id="turn:1", transport=httpx.MockTransport(central)) as proxy:
            async with httpx.AsyncClient(trust_env=False) as client:
                for _ in range(2):
                    response = await client.post(proxy.base_url + "/responses", json={"model": "fixture"}, headers={"Authorization": "Bearer " + proxy.local_token})
                    assert response.status_code == 200
            while proxy.tasks:
                await asyncio.gather(*proxy.tasks)
        assert len(set(observed)) == 2
    asyncio.run(run())
    assert len(records.read_operations(tmp_path, owned(tmp_path), turn())[0]) == 2


def test_proxy_legacy_omits_turn_key_and_partial_association_is_rejected(tmp_path):
    proxy = ExecutionProxy(tmp_path, "https://central.test", "PRIVATE_FIXTURE_TASK_TOKEN", "fixture", "inv")
    proxy.directory.mkdir()
    proxy._record("legacy", "a" * 64, "sending")
    assert "turn_key" not in json.loads((proxy.directory / "legacy.json").read_text())
    for fields in ({"session_key": "session"}, {"turn_id": "turn"}, {"session_key": "session", "turn_id": "bad/turn"}):
        with pytest.raises(ValueError, match="^Invalid operation turn$"):
            ExecutionProxy(tmp_path, "https://central.test", "PRIVATE_FIXTURE_TASK_TOKEN", "fixture", "inv", **fields)
