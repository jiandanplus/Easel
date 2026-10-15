import dataclasses
import pytest
from easel import service_contracts as contracts
from easel.harnesses import store
from easel.harnesses.base import HarnessError


@pytest.mark.parametrize("source", ["custom", "subscription"])
@pytest.mark.parametrize("target", ["local", "cloud"])
def test_routes_model_choices_but_only_custom_local_executes(source, target):
    route = contracts.ServiceRoute(source, target)
    if target == "local":
        route.require_available()
    else:
        with pytest.raises(HarnessError):
            route.require_available()


def test_context_is_immutable_and_nonsecret():
    context = contracts.TaskContext("custom", "local", "codex", "model")
    with pytest.raises(dataclasses.FrozenInstanceError):
        context.model = "changed"
    assert context.metadata() == {"service_source": "custom", "execution_target": "local", "runtime": "codex", "model": "model", "account_id": "", "model_resolution": "explicit", "chat": True, "tools": True}
    with pytest.raises(HarnessError):
        contracts.TaskContext("custom", "local", "bad", "model")


def test_legacy_settings_are_read_without_rewrite(tmp_path):
    path = tmp_path / "_harness" / "config.json"
    store.write_json(path, {"runtime": "codex", "models": {"codex": "m"}})
    before = path.read_bytes()
    value = store.settings(tmp_path, {})
    assert value["service_source"] == "custom"
    assert value["execution_target"] == "local"
    assert path.read_bytes() == before


@pytest.mark.parametrize("field,value", [("service_source", "bad"), ("execution_target", None)])
def test_invalid_persisted_route_fails_closed(tmp_path, field, value):
    store.write_json(tmp_path / "_harness" / "config.json", {field: value})
    with pytest.raises(HarnessError):
        store.settings(tmp_path, {})


def test_legacy_binding_preserves_metadata_and_native_id(tmp_path):
    path = store.binding_path(tmp_path, "s")
    store.write_json(path, {"runtime": "codex", "native_id": "native", "extra": {"keep": True}})
    assert store.binding(tmp_path, "s")["service_source"] == "custom"
    store.bind(tmp_path, "s", "codex")
    assert store.binding(tmp_path, "s")["native_id"] == "native"
    assert store.binding(tmp_path, "s")["extra"] == {"keep": True}
    before = path.read_bytes()
    with pytest.raises(HarnessError):
        store.bind(tmp_path, "s", "codex", service_source="subscription")
    assert path.read_bytes() == before
