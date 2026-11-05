"""The browser never gets to declare arbitrary provider keys or live bindings."""
import json
import pytest


def parse(value):
    from platform_service.console_registry import provider_registry
    return provider_registry({"CENTRAL_CONSOLE_PROVIDER_REGISTRY": json.dumps(value)})


def binding():
    return {"id": "gateway", "version": "1", "models": {"model-v1": {"upstream_model": "glm-5.3", "verification": "live", "verification_ref": "live-proof"}}}


def test_console_registry_defaults_empty_and_keeps_preverified_binding():
    assert parse([]) == ()
    assert parse([binding()]) == (binding(),)


@pytest.mark.parametrize("change", ["key", "url", "unknown", "model-secret", "duplicate", "verification", "control"])
def test_console_registry_rejects_credentials_unknowns_and_unsafe_values(change):
    value = binding()
    if change in {"key", "url", "unknown"}:
        value[change] = "never-export-this"
    elif change == "model-secret":
        value["models"]["model-v1"]["api_key"] = "never-export-this"
    elif change == "verification":
        value["models"]["model-v1"]["verification"] = "user-claimed-success"
    elif change == "control":
        value["models"]["model-v1"]["upstream_model"] = "line\nfeed"
    values = [value, value] if change == "duplicate" else [value]
    with pytest.raises(ValueError, match="invalid_console_provider_registry"):
        parse(values)
