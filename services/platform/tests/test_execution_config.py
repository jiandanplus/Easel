import json

import pytest


def settings():
    from platform_service.execution_config import ExecutionSettings

    return ExecutionSettings


def config():
    return {
        "mode": "test",
        "providers": {
            "fixture": {
                "version": "1",
                "endpoint": "http://127.0.0.1:9999/v1/responses",
                "secret": "fixture-private-secret",
                "models": {"public": "fixture-upstream"},
            }
        },
    }


def test_disabled_default_and_private_file(tmp_path):
    assert settings().from_env(None, env={}).mode == "disabled"
    path = tmp_path / "private.json"
    path.write_text(json.dumps(config()))
    path.chmod(0o600)
    current = settings().from_env(
        None,
        env={"CENTRAL_EXECUTION_CONFIG_FILE": str(path), "OPENAI_API_KEY": "ignored"},
    )
    assert (
        current.mode == "test"
        and current.providers["fixture"].models["public"] == "fixture-upstream"
    )
    assert "fixture-private-secret" not in repr(current)
    path.chmod(0o644)
    with pytest.raises(ValueError, match="invalid_execution_configuration"):
        settings().from_env(None, env={"CENTRAL_EXECUTION_CONFIG_FILE": str(path)})


@pytest.mark.parametrize(
    "patch",
    [
        {"mode": "live"},
        {"endpoint": "http://localhost:9999/responses"},
        {"endpoint": "http://10.0.0.1/responses"},
        {"endpoint": "https://user:secret@host/responses"},
        {"secret": ""},
        {"endpoint": "http://127.0.0.1/x?key=x"},
        {"secret": "bad\x7fsecret"},
    ],
)
def test_invalid_provider_configuration(tmp_path, patch):
    value = config()
    if "mode" in patch:
        value.update(patch)
    else:
        value["providers"]["fixture"].update(patch)
    path = tmp_path / "private.json"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    with pytest.raises(ValueError):
        settings().from_env(None, env={"CENTRAL_EXECUTION_CONFIG_FILE": str(path)})


def test_invalid_https_port_and_symlink_are_rejected(tmp_path):
    value = config()
    value["mode"] = "live"
    value["providers"]["fixture"]["endpoint"] = "https://provider.example:bad/responses"
    path = tmp_path / "private.json"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    with pytest.raises(ValueError):
        settings().from_env(None, env={"CENTRAL_EXECUTION_CONFIG_FILE": str(path)})
    value = config()
    path.write_text(json.dumps(value))
    linked = tmp_path / "link.json"
    linked.symlink_to(path)
    with pytest.raises(ValueError):
        settings().from_env(None, env={"CENTRAL_EXECUTION_CONFIG_FILE": str(linked)})


@pytest.mark.parametrize("timeout", [1, 90, 120, 121, 300])
def test_execution_timeout_is_bounded_and_does_not_change_provider_binding(tmp_path, timeout):
    assert settings()().timeout_seconds == 90
    path = tmp_path / "private.json"
    value = config()
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    original = settings().from_env(None, env={"CENTRAL_EXECUTION_CONFIG_FILE": str(path)})
    value["timeout_seconds"] = timeout
    path.write_text(json.dumps(value))
    current = settings().from_env(None, env={"CENTRAL_EXECUTION_CONFIG_FILE": str(path)})
    assert current.timeout_seconds == timeout
    assert current.bindings == original.bindings


@pytest.mark.parametrize("timeout", [True, False, 0, -1, 300.0, 301, "300", None])
def test_invalid_execution_timeout_is_rejected(timeout):
    with pytest.raises(ValueError, match="invalid_execution_configuration"):
        settings()(timeout_seconds=timeout)
