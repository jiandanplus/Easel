"""Read-only native operation discovery and account-bound recovery contracts."""
import json
import os
import time
from uuid import uuid4

import httpx
import pytest

from easel.platform.central import CentralClient, CentralError


def test_owner_is_stable_business_scope_and_canonical_origin():
    from easel.platform.execution_records import operation_owner
    scope = dict(user_id="user", product_id="easel", tenant_id="tenant", billing_account_id="wallet", session_id="old", authorization_epoch=0)
    owner = operation_owner("https://CENTRAL.test:443/", scope)
    assert owner == operation_owner("https://central.test", {**scope, "session_id": "new", "authorization_epoch": 9})
    assert len(owner) == 64
    for field in ("user_id", "product_id", "tenant_id", "billing_account_id"):
        assert owner != operation_owner("https://central.test", {**scope, field: "another"})
    assert owner != operation_owner("https://other.test", scope)
    for bad in ({}, {**scope, "tenant_id": ""}):
        with pytest.raises(ValueError):
            operation_owner("https://central.test", bad)


def record(root, owner, *, key=None, invocation=None, **changes):
    directory = root / "outputs/_harness/central-execution/operations"
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    directory.parent.chmod(0o700)
    directory.chmod(0o700)
    key = key or str(uuid4())
    row = dict(version=2, operation_key=key, invocation_id=invocation or str(uuid4()),
               body_hash="a"*64, owner_key=owner, model="fixture", status="pending",
               created_at=time.time(), updated_at=time.time())
    row.update(changes)
    path = directory / (key + ".json")
    path.write_text(json.dumps(row))
    path.chmod(0o600)
    return path, row


def native_client(root, handler):
    from tests.test_central_platform_client import auth
    client = CentralClient(root, url="https://central.test", transport=httpx.MockTransport(handler))
    client.save_auth(auth())
    return client


def owned(root):
    from easel.platform.execution_records import operation_owner
    from tests.test_central_platform_client import auth
    return operation_owner("https://central.test", auth()["scope"])


def execution(**changes):
    return dict(id="job", model="fixture", rate_version="v1", status="settled", amount="1000", actual="700",
                funding_mode="test", input_tokens=3, output_tokens=2, created_at="2026-10-22T00:00:00Z",
                updated_at="2026-10-22T00:00:00Z", result_state="expired", **changes)


@pytest.mark.parametrize("code", ["upstream_timeout", "upstream_unavailable", "upstream_error", "invalid_upstream_usage", "request_cancelled"])
def test_pending_operation_retains_only_whitelisted_failure_category(code):
    value = execution()
    value.update(status="pending_reconciliation", actual=None, result_state="not_available",
                 failure_code=code, reason="private-provider-diagnostic")
    parsed = CentralClient._operation_view(value)
    assert parsed["failure_code"] == code and parsed["actual"] is None
    assert "reason" not in parsed


@pytest.mark.parametrize("code", [None, "unknown-private-diagnostic", {"private": True}])
def test_unknown_optional_failure_category_is_not_forwarded(code):
    value = execution()
    value.update(status="pending_reconciliation", actual=None, result_state="not_available", failure_code=code)
    assert "failure_code" not in CentralClient._operation_view(value)


@pytest.mark.parametrize("status", ["reserved", "dispatched", "settled", "released"])
def test_non_pending_operation_drops_stale_timeout_category(status):
    value = execution()
    value.update(status=status, result_state="expired" if status == "settled" else "not_available",
                 failure_code="upstream_timeout")
    assert "failure_code" not in CentralClient._operation_view(value)


def test_pending_operation_accepts_old_server_without_failure_category():
    value = execution()
    value.update(status="pending_reconciliation", actual=None, result_state="not_available")
    parsed = CentralClient._operation_view(value)
    assert parsed["status"] == "pending_reconciliation" and "failure_code" not in parsed


def test_waived_operation_keeps_unknown_usage_separate_from_zero_customer_charge():
    value = execution()
    value.update(status="waived", actual=None, input_tokens=None, output_tokens=None,
                 result_state="not_available", customer_charge="0",
                 resolution="platform_absorbed", provider_usage_known=False,
                 failure_code="upstream_error")
    parsed = CentralClient._operation_view(value)
    assert parsed["status"] == "waived" and parsed["actual"] is None
    assert parsed["input_tokens"] is None and parsed["output_tokens"] is None
    assert parsed["customer_charge"] == "0" and parsed["resolution"] == "platform_absorbed"
    assert parsed["provider_usage_known"] is False and parsed["result_state"] == "not_available"
    assert "failure_code" not in parsed


@pytest.mark.parametrize("changes", [
    {"actual": "0"}, {"input_tokens": 0}, {"output_tokens": 2},
    {"customer_charge": "1"}, {"provider_usage_known": True},
    {"resolution": "settled"}, {"result_state": "available"},
])
def test_waived_operation_rejects_invented_usage_cost_or_recoverable_reply(changes):
    value = execution()
    value.update(status="waived", actual=None, input_tokens=None, output_tokens=None,
                 result_state="not_available", customer_charge="0",
                 resolution="platform_absorbed", provider_usage_known=False)
    value.update(changes)
    with pytest.raises(CentralError):
        CentralClient._operation_view(value)


def test_recovery_only_current_owner_get_and_safe_projection(tmp_path):
    from tests.test_central_platform_client import auth
    calls = []
    def handler(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.headers["authorization"] == "Bearer ACCESS-CENTRAL-SECRET"
        if request.url.path == "/v2/me":
            return httpx.Response(200, json={"scope": auth()["scope"], "account": auth()["account"]})
        assert request.url.path == "/v2/execution-operations"
        assert dict(request.url.params) == {"invocation_id": row["invocation_id"], "operation_key": row["operation_key"]}
        value = execution()
        value.update(body_hash="hidden", scope={"private": True}, binding_hash="hidden", payload="never-return")
        return httpx.Response(200, json=value)
    client = native_client(tmp_path, handler)
    path, row = record(tmp_path, owned(tmp_path), execution_id="untrusted-hint")
    original = path.read_bytes()
    result = client.recoveries()
    assert result == {"recoveries": [{"operation_key": row["operation_key"], "invocation_id": row["invocation_id"], "model": "fixture", "local_status": "pending", "lookup_status": "found", "execution": execution()}], "truncated": False}
    assert len(calls) == 2 and path.read_bytes() == original
    assert not any(word in json.dumps(result) for word in ("owner_key", "body_hash", "outputs", "hidden", "untrusted"))


def test_reader_drops_foreign_legacy_bad_files_and_keeps_originals(tmp_path):
    from easel.platform.execution_records import read_operations
    owner = owned(tmp_path)
    good, row = record(tmp_path, owner)
    foreign, _ = record(tmp_path, "f"*64)
    legacy, _ = record(tmp_path, owner, version=1)
    malformed, _ = record(tmp_path, owner, invocation_id="../traversal")
    wide, _ = record(tmp_path, owner)
    wide.chmod(0o644)
    huge, _ = record(tmp_path, owner)
    huge.write_bytes(b"x" * 20000)
    broken, _ = record(tmp_path, owner)
    broken.write_text("{")
    link = good.parent / (str(uuid4()) + ".json")
    link.symlink_to(good)
    directory = good.parent / (str(uuid4()) + ".json")
    directory.mkdir()
    before = {p: p.read_bytes() for p in (good, foreign, legacy, malformed, wide, huge, broken)}
    rows, truncated = read_operations(tmp_path, owner)
    assert len(rows) == 1 and rows[0]["operation_key"] == row["operation_key"]
    assert not truncated
    assert {p: p.read_bytes() for p in before} == before and link.is_symlink()


def test_reader_bounded_newest_fifty_and_symlink_parent(tmp_path):
    from easel.platform.execution_records import read_operations
    owner = owned(tmp_path)
    paths = []
    for i in range(55):
        path, _ = record(tmp_path, owner)
        os.utime(path, ns=(1000000000+i, 1000000000+i))
        paths.append(path)
    rows, truncated = read_operations(tmp_path, owner)
    assert len(rows) == 50 and truncated
    assert [r["operation_key"] for r in rows] == [p.stem for p in reversed(paths[5:])]
    path = tmp_path / "symlink-workspace"
    path.mkdir()
    (path / "outputs").symlink_to(tmp_path / "outputs", target_is_directory=True)
    assert read_operations(path, owner) == ([], False)


@pytest.mark.parametrize("status,code,lookup", [(404,"execution_not_found","not_found"), (503,"platform_unavailable","unavailable")])
def test_missing_or_unavailable_never_fabricates_financial_state(tmp_path, status, code, lookup):
    from tests.test_central_platform_client import auth
    def handler(request):
        if request.url.path == "/v2/me":
            return httpx.Response(200, json={"scope": auth()["scope"], "account": auth()["account"]})
        return httpx.Response(status, json={"error": {"code": code}})
    client = native_client(tmp_path, handler)
    path, _ = record(tmp_path, owned(tmp_path))
    before = path.read_bytes()
    row = client.recoveries()["recoveries"][0]
    assert row["lookup_status"] == lookup and "execution" not in row and "actual" not in row
    assert path.read_bytes() == before


def test_auth_failure_after_a_row_aborts_without_stale_rows(tmp_path):
    from tests.test_central_platform_client import auth
    counter = []
    def handler(request):
        if request.url.path == "/v2/me":
            return httpx.Response(200, json={"scope": auth()["scope"], "account": auth()["account"]})
        counter.append(request)
        return httpx.Response(200, json=execution()) if len(counter) == 1 else httpx.Response(401, json={"error": {"code": "session_invalid"}})
    client = native_client(tmp_path, handler)
    for _ in range(2):
        record(tmp_path, owned(tmp_path))
    with pytest.raises(CentralError) as invalid:
        client.recoveries()
    assert invalid.value.status == 401 and "auth" not in client.store.read()


@pytest.mark.parametrize("status", [401, 403, 409])
@pytest.mark.parametrize("content", [b"", b"Unauthorized upstream-private-detail", b'{"error":', b"null", b"[]", b'{"error": "upstream-private-detail"}'])
def test_malformed_auth_failure_after_a_row_aborts_and_preserves_credentials(tmp_path, status, content):
    from tests.test_central_platform_client import auth
    calls = []

    def handler(request):
        assert request.method == "GET"
        if request.url.path == "/v2/me":
            return httpx.Response(200, json={"scope": auth()["scope"], "account": auth()["account"]})
        calls.append(request)
        return httpx.Response(200, json=execution()) if len(calls) == 1 else httpx.Response(status, content=content)

    client = native_client(tmp_path, handler)
    originals = {path: path.read_bytes() for path, _ in (record(tmp_path, owned(tmp_path)) for _ in range(2))}
    saved_auth = client.store.read()["auth"]
    with pytest.raises(CentralError) as invalid:
        client.recoveries()
    assert invalid.value.status == status and invalid.value.code == "platform_unavailable"
    assert str(invalid.value) == str(CentralError())
    assert client.store.read()["auth"] == saved_auth
    assert len(calls) == 2 and {path: path.read_bytes() for path in originals} == originals


@pytest.mark.parametrize("status", [401, 403, 409])
@pytest.mark.parametrize("failure", ["oversize", "interrupted"])
def test_unreadable_auth_error_after_a_row_aborts_and_preserves_credentials(tmp_path, status, failure):
    from tests.test_central_platform_client import auth
    calls = []

    class ErrorBody(httpx.SyncByteStream):
        def __iter__(self):
            if failure == "oversize":
                yield b"x" * (2 * 1024 * 1024 + 1)
            else:
                yield b'{"error":'
                raise httpx.ReadError("upstream-private-detail")

    def handler(request):
        assert request.method == "GET"
        if request.url.path == "/v2/me":
            return httpx.Response(200, json={"scope": auth()["scope"], "account": auth()["account"]})
        calls.append(request)
        return httpx.Response(200, json=execution()) if len(calls) == 1 else httpx.Response(status, stream=ErrorBody())

    client = native_client(tmp_path, handler)
    originals = {path: path.read_bytes() for path, _ in (record(tmp_path, owned(tmp_path)) for _ in range(2))}
    saved_auth = client.store.read()["auth"]
    with pytest.raises(CentralError) as invalid:
        client.recoveries()
    assert invalid.value.status == status and invalid.value.code == "platform_unavailable"
    assert str(invalid.value) == str(CentralError())
    assert client.store.read()["auth"] == saved_auth
    assert len(calls) == 2 and {path: path.read_bytes() for path in originals} == originals


@pytest.mark.parametrize("status,lookup", [(404, "not_found"), (503, "unavailable")])
def test_malformed_non_auth_error_retains_safe_lookup_state(tmp_path, status, lookup):
    from tests.test_central_platform_client import auth

    def handler(request):
        assert request.method == "GET"
        if request.url.path == "/v2/me":
            return httpx.Response(200, json={"scope": auth()["scope"], "account": auth()["account"]})
        return httpx.Response(status, content=b"upstream-private-detail")

    client = native_client(tmp_path, handler)
    record(tmp_path, owned(tmp_path))
    saved_auth = client.store.read()["auth"]
    result = client.recoveries()
    assert result["recoveries"][0]["lookup_status"] == lookup
    assert "execution" not in result["recoveries"][0]
    assert "upstream-private-detail" not in json.dumps(result)
    assert client.store.read()["auth"] == saved_auth


def test_relogin_owner_stable_account_change_hidden_and_invalid_view(tmp_path):
    from tests.test_central_platform_client import auth
    from easel.platform.execution_records import read_operations
    client = native_client(tmp_path, lambda request: None)
    record(tmp_path, owned(tmp_path))
    fresh = auth()
    fresh["scope"]["session_id"] = fresh["session_id"] = "fresh"
    fresh["scope"]["authorization_epoch"] = 2
    from easel.platform.execution_records import operation_owner
    assert len(read_operations(tmp_path, operation_owner("https://central.test", fresh["scope"]))[0]) == 1
    fresh["scope"]["user_id"] = "other"
    assert read_operations(tmp_path, operation_owner("https://central.test", fresh["scope"]))[0] == []
    for changes in ({"result_state": "replayed"}, {"amount": "-1"}, {"created_at": "bad-date"}):
        value = execution()
        value.update(changes)
        with pytest.raises(CentralError):
            client._operation_view(value)


@pytest.mark.asyncio
async def test_recovery_bridge_fixed_route_loopback_and_no_input_override(tmp_path):
    from fastapi import FastAPI
    from easel.platform.central_web import create_router
    from easel.platform.central import CentralClient
    from unittest.mock import patch
    app = FastAPI()
    app.include_router(create_router(tmp_path, lambda: None))
    calls = []
    def recover(client):
        calls.append(client.store.root)
        return {"recoveries": [], "truncated": False}
    with patch.object(CentralClient, "recoveries", recover):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1)), base_url="http://127.0.0.1:8000") as client:
            response = await client.get("/api/platform/central/execution-recoveries?path=/tmp/foreign&owner=other")
            assert response.status_code == 200 and response.json() == {"recoveries": [], "truncated": False}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("198.51.100.1", 1)), base_url="http://127.0.0.1:8000") as client:
            assert (await client.get("/api/platform/central/execution-recoveries")).status_code == 403
    assert calls == [tmp_path]


def test_recovery_auth_refresh_once_then_only_execution_gets(tmp_path):
    from tests.test_central_platform_client import auth
    calls = []
    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/v2/auth/refresh":
            assert request.method == "POST"
            value = auth()
            value["access_token"] = "ROTATED-ACCESS-SECRET"
            return httpx.Response(200, json=value)
        assert request.method == "GET" and request.headers["authorization"] == "Bearer ROTATED-ACCESS-SECRET"
        if request.url.path == "/v2/me":
            return httpx.Response(200, json={"scope": auth()["scope"], "account": auth()["account"]})
        return httpx.Response(200, json=execution())
    client = native_client(tmp_path, handler)
    with client.store.locked():
        state = client.store.read()
        state["auth"]["expires_at"] = 0
        client.store.write(state)
    record(tmp_path, owned(tmp_path))
    assert client.recoveries()["recoveries"][0]["lookup_status"] == "found"
    assert calls == [("POST", "/v2/auth/refresh"), ("GET", "/v2/me"), ("GET", "/v2/execution-operations")]


def test_recovery_account_switch_hides_previous_records(tmp_path):
    from tests.test_central_platform_client import auth
    calls = []
    current = auth()
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"scope": current["scope"], "account": current["account"]})
    client = native_client(tmp_path, handler)
    path, _ = record(tmp_path, owned(tmp_path))
    before = path.read_bytes()
    current["scope"]["user_id"] = current["account"]["id"] = "other"
    with client.store.locked():
        state = client.store.read()
        state["auth"] = current
        client.store.write(state)
    assert client.recoveries() == {"recoveries": [], "truncated": False}
    assert calls == ["/v2/me"] and path.read_bytes() == before


def test_reader_scan_entry_time_and_os_owner_bounds(tmp_path, monkeypatch):
    import easel.platform.execution_records as records
    owner = owned(tmp_path)
    paths = [record(tmp_path, owner)[0] for _ in range(4)]
    before = {p: p.read_bytes() for p in paths}
    monkeypatch.setattr(records, "MAX_SCAN", 2)
    rows, truncated = records.read_operations(tmp_path, owner)
    assert len(rows) <= 2 and truncated
    monkeypatch.setattr(records, "SCAN_SECONDS", -1)
    assert records.read_operations(tmp_path, owner) == ([], True)
    monkeypatch.setattr(records, "SCAN_SECONDS", 1)
    real_uid = os.getuid()
    monkeypatch.setattr(records.os, "getuid", lambda: real_uid + 1)
    assert records.read_operations(tmp_path, owner) == ([], False)
    assert {p: p.read_bytes() for p in paths} == before


def test_lookup_total_deadline_stops_new_requests(tmp_path, monkeypatch):
    import easel.platform.central as central
    from tests.test_central_platform_client import auth
    clock = [0.0]
    calls = []
    client = native_client(tmp_path, lambda r: None)
    for _ in range(3):
        record(tmp_path, owned(tmp_path))
    monkeypatch.setattr(central.time, "monotonic", lambda: clock[0])
    def request(state, method, path, body=None, **kwargs):
        calls.append(path)
        if path == "/v2/me":
            return {"scope": auth()["scope"], "account": auth()["account"]}
        clock[0] = 31
        return execution()
    monkeypatch.setattr(client, "_request", request)
    result = client.recoveries()
    assert result["truncated"] and len(calls) == 2
    assert [r["lookup_status"] for r in result["recoveries"]] == ["found", "unavailable", "unavailable"]


def test_recovery_accepts_bounded_slow_reads_without_resubmission(tmp_path, monkeypatch):
    import easel.platform.central as central
    from tests.test_central_platform_client import auth
    clock = [0.0]
    calls = []

    def handler(request):
        assert request.method == "GET"
        calls.append((request.url.path, dict(request.url.params)))
        # MockTransport does not enforce socket timeouts. Model a 5.5-second
        # response in virtual time and let the real _request deadline gate run.
        clock[0] += 5.5
        if request.url.path == "/v2/me":
            return httpx.Response(200, json={"scope": auth()["scope"], "account": auth()["account"]})
        return httpx.Response(200, json=execution())

    client = native_client(tmp_path, handler)
    records = [record(tmp_path, owned(tmp_path)) for _ in range(2)]
    originals = {path: path.read_bytes() for path, _ in records}
    saved_auth = client.store.read()["auth"]
    monkeypatch.setattr(central.time, "monotonic", lambda: clock[0])

    result = client.recoveries()

    assert result["truncated"] is False
    assert [row["lookup_status"] for row in result["recoveries"]] == ["found", "found"]
    assert clock[0] == 16.5
    assert calls[0] == ("/v2/me", {}) and len(calls) == 3
    assert {query["operation_key"] for _, query in calls[1:]} == {row["operation_key"] for _, row in records}
    assert client.store.read()["auth"] == saved_auth
    assert {path: path.read_bytes() for path in originals} == originals


def test_slow_recovery_reads_share_one_budget_and_do_not_start_after_expiry(tmp_path, monkeypatch):
    import easel.platform.central as central
    from tests.test_central_platform_client import auth
    clock = [0.0]
    calls = []

    def handler(request):
        assert request.method == "GET"
        timeout = request.extensions["timeout"]["read"]
        calls.append((request.url.path, timeout))
        # Enforce the supplied per-read allowance in the virtual transport;
        # neither this test nor the implementation sleeps or retries a read.
        clock[0] += min(9.0, timeout)
        if timeout < 9:
            raise httpx.ReadTimeout("synthetic slow read", request=request)
        if request.url.path == "/v2/me":
            return httpx.Response(200, json={"scope": auth()["scope"], "account": auth()["account"]})
        return httpx.Response(200, json=execution())

    client = native_client(tmp_path, handler)
    records = [record(tmp_path, owned(tmp_path)) for _ in range(4)]
    originals = {path: path.read_bytes() for path, _ in records}
    saved_auth = client.store.read()["auth"]
    monkeypatch.setattr(central.time, "monotonic", lambda: clock[0])

    result = client.recoveries()

    assert result["truncated"] is True and clock[0] == 30
    assert [row["lookup_status"] for row in result["recoveries"]] == ["found", "found", "unavailable", "unavailable"]
    assert calls == [("/v2/me", 10.0), ("/v2/execution-operations", 10.0),
                     ("/v2/execution-operations", 10.0), ("/v2/execution-operations", 3.0)]
    assert client.store.read()["auth"] == saved_auth
    assert {path: path.read_bytes() for path in originals} == originals


@pytest.mark.parametrize("status,code", [(401,"session_invalid"), (403,"scope_denied"), (409,"account_unavailable"), (409,"account_changed")])
def test_current_auth_or_account_failure_never_exposes_records(tmp_path, status, code):
    client = native_client(tmp_path, lambda r: httpx.Response(status, json={"error": {"code": code}}))
    path, _ = record(tmp_path, owned(tmp_path))
    before = path.read_bytes()
    with pytest.raises(Exception) as invalid:
        client.recoveries()
    assert invalid.value.status == status and path.read_bytes() == before


def test_empty_records_still_requires_authenticated_current_scope(tmp_path):
    from easel.platform.client import PlatformError
    client = CentralClient(tmp_path, url="https://central.test", transport=httpx.MockTransport(lambda r: pytest.fail("No credentials must not query central")))
    with pytest.raises(PlatformError) as invalid:
        client.recoveries()
    assert invalid.value.status == 401


def test_deeply_nested_and_huge_numeric_records_are_dropped(tmp_path):
    from easel.platform.execution_records import read_operations
    owner = owned(tmp_path)
    path, _ = record(tmp_path, owner)
    path.write_text("[" * 2000 + "0" + "]" * 2000)
    assert read_operations(tmp_path, owner) == ([], False)
    path, _ = record(tmp_path, owner, created_at=10**1000)
    assert read_operations(tmp_path, owner) == ([], False)
