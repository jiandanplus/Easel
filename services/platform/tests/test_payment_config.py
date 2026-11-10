import json

import pytest

from platform_service.config import Settings
from platform_service.errors import PlatformError


def settings(*, local=False):
    return Settings(
        "postgresql://localhost/unused",
        "http://127.0.0.1:18762" if local else "https://accounts.example",
        allow_loopback_http=local,
    )


def private_file(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    path.chmod(0o600)
    return path


def test_payments_are_disabled_without_reading_credentials(tmp_path):
    from platform_service.payment_config import PaymentSettings

    config = PaymentSettings.from_env(
        settings(), {"CENTRAL_PAYMENTS_CONFIG_FILE": str(tmp_path / "absent")}
    )
    assert config.mode == "disabled" and config.channels == ()


def test_test_payments_require_explicit_loopback_and_private_key(tmp_path):
    from platform_service.payment_config import PaymentSettings

    key = private_file(tmp_path, "test.key", b"x" * 32)
    path = private_file(
        tmp_path,
        "payments.json",
        json.dumps(
            {"test_key_file": str(key), "test_state_file": str(tmp_path / "state.json")}
        ).encode(),
    )
    env = {"CENTRAL_PAYMENTS_MODE": "test", "CENTRAL_PAYMENTS_CONFIG_FILE": str(path)}
    with pytest.raises(PlatformError):
        PaymentSettings.from_env(settings(), env)
    config = PaymentSettings.from_env(settings(local=True), env)
    assert config.mode == "test"
    assert {item["id"] for item in config.channels} == {"wechat", "alipay"}
    assert all(item["enabled"] for item in config.channels)
    assert config.test_key == b"x" * 32
    assert "xxxxxxxx" not in repr(config)


@pytest.mark.parametrize(
    "kind", ["world_readable", "symlink", "unknown_mode", "missing_file", "short_key"]
)
def test_invalid_payment_configuration_fails_without_sensitive_diagnostics(
    tmp_path, kind
):
    from platform_service.payment_config import PaymentSettings

    key = private_file(
        tmp_path,
        "private-key-must-not-echo",
        b"short" if kind == "short_key" else b"x" * 32,
    )
    path = private_file(
        tmp_path,
        "configuration.json",
        json.dumps(
            {"test_key_file": str(key), "test_state_file": str(tmp_path / "state.json")}
        ).encode(),
    )
    env = {
        "CENTRAL_PAYMENTS_MODE": "unknown" if kind == "unknown_mode" else "test",
        "CENTRAL_PAYMENTS_CONFIG_FILE": str(path),
    }
    if kind == "world_readable":
        key.chmod(0o644)
    if kind == "symlink":
        linked = tmp_path / "linked"
        linked.symlink_to(path)
        env["CENTRAL_PAYMENTS_CONFIG_FILE"] = str(linked)
    if kind == "missing_file":
        env["CENTRAL_PAYMENTS_CONFIG_FILE"] = str(tmp_path / "missing")
    with pytest.raises(PlatformError) as caught:
        PaymentSettings.from_env(settings(local=True), env)
    assert "private-key-must-not-echo" not in str(caught.value)
    assert str(tmp_path) not in str(caught.value)


def test_live_credentials_use_server_files_and_fixed_notification_origins(tmp_path):
    from platform_service.payment_config import PaymentSettings

    private = private_file(tmp_path, "merchant.pem", b"private-fixture-material")
    public = private_file(tmp_path, "verification.pem", b"public-fixture-material")
    api_key = private_file(tmp_path, "api.key", b"a" * 32)
    body = {
        "wechat": {
            "merchant_id": "merchant",
            "app_id": "app",
            "mch_serial": "123ABC",
            "merchant_private_key_file": str(private),
            "verification_key_files": {"PUB_KEY_ID_FIXTURE": str(public)},
            "api_v3_key_file": str(api_key),
        }
    }
    path = private_file(tmp_path, "live.json", json.dumps(body).encode())
    config = PaymentSettings.from_env(
        settings(),
        {"CENTRAL_PAYMENTS_MODE": "live", "CENTRAL_PAYMENTS_CONFIG_FILE": str(path)},
    )
    assert (
        config.wechat["notify_url"]
        == "https://accounts.example/v2/payments/wechat/notify"
    )
    assert (
        config.wechat["refund_notify_url"]
        == "https://accounts.example/v2/payments/wechat/notify"
    )
    assert config.wechat["merchant_private_key"] == b"private-fixture-material"
    assert config.channels[0]["merchant_id"] == "merchant"
    assert "private-fixture-material" not in repr(config)
    with pytest.raises(PlatformError):
        PaymentSettings.from_env(
            settings(local=True),
            {
                "CENTRAL_PAYMENTS_MODE": "live",
                "CENTRAL_PAYMENTS_CONFIG_FILE": str(path),
            },
        )


def test_live_configuration_rejects_arbitrary_provider_endpoint(tmp_path):
    from platform_service.payment_config import PaymentSettings

    path = private_file(
        tmp_path,
        "unsafe.json",
        json.dumps({"wechat": {"base_url": "https://untrusted.example"}}).encode(),
    )
    with pytest.raises(PlatformError):
        PaymentSettings.from_env(
            settings(),
            {
                "CENTRAL_PAYMENTS_MODE": "live",
                "CENTRAL_PAYMENTS_CONFIG_FILE": str(path),
            },
        )
