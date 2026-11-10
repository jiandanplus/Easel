"""New desktop installs can sign in without entering the central API address."""
import pytest

from easel.harnesses.base import HarnessError
from easel.harnesses.store import read_json, write_json
from easel.installation import initialize_runtime
from easel.platform.central import CentralClient


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    for key in ("EASEL_PLATFORM_URL", "EASEL_AGENT_RUNTIME", "EASEL_CHAT_TRANSPORT", "EASEL_OPENCLAW_STATE_DIR"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("EASEL_PORTABLE", "1")


def test_new_portable_account_entry_has_public_endpoint_without_writing_credentials(tmp_path):
    client = CentralClient(tmp_path)
    status = client.status(verify=False)
    assert status["url"] == "https://accounts.drawbookai.cn"
    assert status["configured"] is True and status["authenticated"] is False
    assert not client.store.path.exists()


def test_saved_private_central_endpoint_remains_authoritative(tmp_path):
    client = CentralClient(tmp_path)
    with client.store.locked():
        client.store.write({"version": 2, "url": "https://private.invalid"})
    before = client.store.path.read_bytes()
    assert client.status(verify=False)["url"] == "https://private.invalid"
    assert client.store.path.read_bytes() == before


@pytest.mark.parametrize("value", ["", "https://configured.invalid"])
def test_explicit_endpoint_overrides_product_fallback(tmp_path, monkeypatch, value):
    monkeypatch.setenv("EASEL_PLATFORM_URL", value)
    assert CentralClient(tmp_path).status(verify=False)["url"] == value


def test_source_checkout_and_explicit_constructor_keep_configurable_entry(tmp_path, monkeypatch):
    monkeypatch.delenv("EASEL_PORTABLE")
    assert CentralClient(tmp_path).status(verify=False)["configured"] is False
    monkeypatch.setenv("EASEL_PORTABLE", "1")
    assert CentralClient(tmp_path, url="").status(verify=False)["configured"] is False


def test_existing_malformed_account_state_never_falls_back_or_changes(tmp_path):
    client = CentralClient(tmp_path)
    client.store.directory.mkdir(parents=True)
    client.store.path.write_text("{broken")
    with pytest.raises(HarnessError):
        client.status(verify=False)
    assert client.store.path.read_text() == "{broken"


def test_fresh_desktop_workspace_uses_subscription_without_a_supplier_key(tmp_path):
    assert initialize_runtime(tmp_path, "pi", subscription_default=True) == "pi"
    settings = read_json(tmp_path / "outputs/_harness/config.json")
    assert settings["service_source"] == "subscription"
    assert settings["subscription_backend"] == "central"
    assert settings["execution_target"] == "local"
    assert not (tmp_path / "outputs/_harness/bundled-provider.json").exists()


def test_existing_runtime_and_custom_config_are_never_replaced(tmp_path):
    path = tmp_path / "outputs/_harness/config.json"
    write_json(path, {"runtime": "pi", "service_source": "custom", "models": {"pi": "own-model"}})
    before = path.read_bytes()
    assert initialize_runtime(tmp_path, "pi", subscription_default=True) == "pi"
    assert path.read_bytes() == before


def test_default_cli_initialization_retains_original_custom_channel(tmp_path):
    assert initialize_runtime(tmp_path, "pi") == "pi"
    assert read_json(tmp_path / "outputs/_harness/config.json").get("service_source", "custom") == "custom"
