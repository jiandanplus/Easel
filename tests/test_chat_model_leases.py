"""A successful same-source refresh cannot revoke another valid composer lease."""
import pytest
from easel.chat_models import CatalogRegistry
from easel.harnesses.base import HarnessError


def registry(monkeypatch):
    state = {"token": "one", "source": "owner", "time": 0, "models": [{"id": "pro"}], "fail": False}
    value = CatalogRegistry(clock=lambda: state["time"])
    def load():
        if state["fail"]:
            raise HarnessError("unavailable")
        return state["models"]
    def snapshot(*args):
        return state["source"] + ":" + state["token"], {"runtime": "pi", "service_source": "subscription", "subscription_backend": "central",
            "selected_model": "pro", "switchable": True, "ready": True, "reason": ""}, load, "", state["source"]
    monkeypatch.setattr(value, "_snapshot", snapshot)
    return value, state


def test_same_source_refresh_keeps_previously_issued_unexpired_lease(monkeypatch):
    value, state = registry(monkeypatch)
    first = value.catalog("root", {}, {})
    second = value.catalog("root", {}, {}, refresh=True)
    assert first["revision"] != second["revision"]
    assert value.select("root", {}, {}, "pro", first["revision"]).model == "pro"


def test_normal_central_token_rotation_keeps_same_owner_lease(monkeypatch):
    value, state = registry(monkeypatch)
    first = value.catalog("root", {}, {})
    state["token"] = "rotated"
    assert value.select("root", {}, {}, "pro", first["revision"]).model == "pro"


def test_source_change_never_reuses_a_lease(monkeypatch):
    value, state = registry(monkeypatch)
    first = value.catalog("root", {}, {})
    state["source"] = "another-owner"
    with pytest.raises(HarnessError):
        value.select("root", {}, {}, "pro", first["revision"])


def test_explicit_failed_refresh_and_capability_change_invalidate_old_leases(monkeypatch):
    for failure in (False, True):
        value, state = registry(monkeypatch)
        first = value.catalog("root", {}, {})
        state["models"] = [{"id": "other"}]
        state["fail"] = failure
        if failure:
            with pytest.raises(HarnessError): value.catalog("root", {}, {}, refresh=True)
        else:
            value.catalog("root", {}, {}, refresh=True)
        with pytest.raises(HarnessError): value.select("root", {}, {}, "pro", first["revision"])


def test_refresh_never_extends_an_old_lease_deadline(monkeypatch):
    value, state = registry(monkeypatch)
    first = value.catalog("root", {}, {})
    state["time"] = 100
    second = value.catalog("root", {}, {}, refresh=True)
    state["time"] = 121
    with pytest.raises(HarnessError): value.select("root", {}, {}, "pro", first["revision"])
    assert value.select("root", {}, {}, "pro", second["revision"]).model == "pro"


@pytest.mark.parametrize("field", ["session_id", "auth_epoch"])
def test_real_central_snapshot_rejects_a_changed_session_or_auth_epoch(tmp_path, monkeypatch, field):
    from contextlib import nullcontext
    from types import SimpleNamespace
    state = {"url": "https://central.invalid", "auth": {
        "scope": {"user_id": "owner", "product_id": "easel"},
        "session_id": "first", "auth_epoch": 1, "access_token": "SYNTHETIC"}}
    client = SimpleNamespace(store=SimpleNamespace(locked=lambda: nullcontext()),
                             _state=lambda: state, models=lambda: {"models": [{"id": "pro"}]})
    monkeypatch.setattr("easel.platform.central.CentralClient", lambda _: client)
    config = dict(runtime="pi", service_source="subscription", subscription_backend="central",
                  execution_target="local", subscription_model="pro")
    value = CatalogRegistry()
    first = value.catalog(tmp_path, config, {})
    state["auth"][field] = "second" if field == "session_id" else 2
    with pytest.raises(HarnessError):
        value.select(tmp_path, config, {}, "pro", first["revision"])


def test_catalog_leases_are_bounded_without_extending_retained_deadlines(monkeypatch):
    value, state = registry(monkeypatch)
    revisions = [value.catalog("root", {}, {}, refresh=True)["revision"] for _ in range(65)]
    with pytest.raises(HarnessError):
        value.select("root", {}, {}, "pro", revisions[0])
    for revision in revisions[1:]:
        assert value.select("root", {}, {}, "pro", revision).model == "pro"
    state["time"] = 121
    for revision in revisions[1:]:
        with pytest.raises(HarnessError):
            value.select("root", {}, {}, "pro", revision)
