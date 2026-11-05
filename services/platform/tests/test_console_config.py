import pytest


def env_config():
    return {"CENTRAL_CONSOLE_ENABLED":"1","CENTRAL_CONSOLE_DATABASE_URL":"postgresql://console:CONFIG-SECRET@127.0.0.1/easel_s1_test_console",
        "CENTRAL_CONSOLE_PUBLIC_URL":"http://127.0.0.1:18875","CENTRAL_CONSOLE_OIDC_ISSUER":"http://127.0.0.1:18763/oidc",
        "CENTRAL_CONSOLE_OIDC_CLIENT_ID":"console-client","CENTRAL_CONSOLE_OIDC_CLIENT_SECRET":"OIDC-CONFIG-SECRET",
        "CENTRAL_CONSOLE_DB_SCHEMA":"console_test","CENTRAL_CONSOLE_ALLOW_LOOPBACK_HTTP":"1"}


def test_console_config_explicit_enable_and_no_product_fallback():
    from platform_service.console_config import ConsoleSettings
    with pytest.raises(ValueError):ConsoleSettings.from_env({})
    with pytest.raises(ValueError):ConsoleSettings.from_env({"CENTRAL_CONSOLE_ENABLED":"1","CENTRAL_DATABASE_URL":"postgresql://unused@localhost/db"})
    settings=ConsoleSettings.from_env(env_config())
    assert settings.session_ttl==28800 and settings.auth_ttl==300
    assert settings.public_url=='http://127.0.0.1:18875'
    assert 'CONFIG-SECRET' not in repr(settings)
    assert settings.oidc_settings().oidc_clients['console'].force_login is True


@pytest.mark.parametrize('field,value',[
    ('CENTRAL_CONSOLE_ENABLED','true'),('CENTRAL_CONSOLE_ALLOW_LOOPBACK_HTTP','0'),
    ('CENTRAL_CONSOLE_PUBLIC_URL','http://public.example'),('CENTRAL_CONSOLE_PUBLIC_URL','http://127.0.0.1:18875/admin'),
    ('CENTRAL_CONSOLE_PUBLIC_URL','https://user:pass@example.com'),('CENTRAL_CONSOLE_DB_SCHEMA','unsafe;schema'),
    ('CENTRAL_CONSOLE_OIDC_CLIENT_SECRET',''),('CENTRAL_CONSOLE_OIDC_ISSUER',''),
])
def test_console_config_rejects_unsafe_or_incomplete_settings(field,value):
    from platform_service.console_config import ConsoleSettings
    with pytest.raises(ValueError):ConsoleSettings.from_env({**env_config(),field:value})
