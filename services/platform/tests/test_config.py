import pytest

from platform_service.config import OIDCClient, Settings


def test_settings_require_safe_server_urls_and_hide_credentials():
    client = OIDCClient("easel-client", "fixture-oidc-private")
    settings = Settings("postgresql://fixture:private@localhost/easel_s1_test", "https://accounts.example", issuer="https://identity.example/oidc", oidc_clients={"easel": client})
    assert "private" not in repr(settings)
    assert "fixture-oidc-private" not in repr(client)
    assert settings.identity_ready
    with pytest.raises(ValueError):
        Settings("postgresql://localhost/test", "http://accounts.example")
    with pytest.raises(ValueError):
        Settings("postgresql://localhost/test", "https://accounts.example/?key=x")


def test_development_http_requires_explicit_loopback_only():
    with pytest.raises(ValueError):
        Settings("postgresql://localhost/test", "http://127.0.0.1:18000")
    local = Settings("postgresql://localhost/test", "http://127.0.0.1:18000", allow_loopback_http=True)
    assert not local.identity_ready
    with pytest.raises(ValueError):
        Settings("postgresql://localhost/test", "http://192.0.2.1:18000", allow_loopback_http=True)


def test_environment_configuration_never_invents_an_identity_provider():
    settings = Settings.from_env({"CENTRAL_DATABASE_URL": "postgresql://localhost/test", "CENTRAL_PUBLIC_URL": "https://accounts.example"})
    assert not settings.identity_ready
    with pytest.raises(ValueError):
        Settings.from_env({})
    with pytest.raises(ValueError):
        Settings.from_env({"CENTRAL_DATABASE_URL": "postgresql://localhost/test", "CENTRAL_PUBLIC_URL": "https://accounts.example", "CENTRAL_OIDC_CLIENTS": "not-json"})
