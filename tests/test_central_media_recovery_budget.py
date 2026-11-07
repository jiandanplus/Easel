"""Virtual-time GET-only recovery probes; no sleep, sockets or provider calls."""
import httpx
import pytest

from easel.platform.client import PlatformError
from tests.test_central_media_host import Fixture, setup, MODEL
from tests.test_central_platform_client import auth


def recovery_client(tmp_path, monkeypatch, *, delay=5.5, count=1):
    import easel.platform.media as media
    clock = [0.0]
    calls = []
    fixture = Fixture()

    def handler(request):
        assert request.method == "GET", "recovery must not submit or mint a task"
        allowance = request.extensions["timeout"]["read"]
        calls.append((request.url.path, allowance, clock[0]))
        clock[0] += min(delay, allowance)
        if allowance < delay:
            raise httpx.ReadTimeout("synthetic virtual slow GET", request=request)
        return fixture(request)

    client, _, _ = setup(tmp_path, handler)
    rows = [{"operation_key": f"operation-{index}", "model": MODEL, "capability": "image.generate",
             "local_status": "created", "invocation_id": fixture.invocation, "job_id": fixture.job_id}
            for index in range(count)]
    monkeypatch.setattr(client.records, "rows", lambda *args: (rows, False))
    monkeypatch.setattr(media.time, "monotonic", lambda: clock[0])
    return client, clock, calls


def test_media_recovery_accepts_5_5s_identity_and_gets_in_shared_budget(tmp_path, monkeypatch):
    client, clock, calls = recovery_client(tmp_path, monkeypatch)
    saved = client.central.store.read()["auth"]
    result = client.recoveries("session-one", "turn-one")
    assert result["truncated"] is False
    assert result["recoveries"][0]["lookup_status"] == "found"
    assert clock[0] == 16.5 and len(calls) == 3
    assert all(allowance == 10 for _, allowance, _ in calls)
    assert client.central.store.read()["auth"] == saved


def test_media_recovery_uses_remaining_budget_and_never_starts_after_30s(tmp_path, monkeypatch):
    client, clock, calls = recovery_client(tmp_path, monkeypatch, delay=9, count=3)
    result = client.recoveries("session-one", "turn-one")
    assert clock[0] == 30
    assert [allowance for _, allowance, _ in calls] == [10, 10, 10, 3]
    assert [start for _, _, start in calls] == [0, 9, 18, 27]
    assert result["truncated"] is True
    assert [row["lookup_status"] for row in result["recoveries"]] == ["found", "unavailable", "unavailable"]


def test_media_recovery_slow_identity_still_rejects_changed_account(tmp_path, monkeypatch):
    client, clock, calls = recovery_client(tmp_path, monkeypatch, count=0)
    fetch = client._fetch
    def foreign(snapshot, method, path, *args, **kwargs):
        value = fetch(snapshot, method, path, *args, **kwargs)
        return {**value, "account": {**auth()["account"], "id": "foreign"}} if path == "/v2/me" else value
    monkeypatch.setattr(client, "_fetch", foreign)
    with pytest.raises(PlatformError) as error:
        client.recoveries("session-one", "turn-one")
    assert error.value.code == "account_changed"
    assert clock[0] == 5.5 and len(calls) == 1
