"""The management web server never inherits operator database credentials."""
import argparse
import pytest


def test_console_cli_routes_are_separate_from_product_settings(monkeypatch, capsys):
    import platform_service.__main__ as command
    async def operation(args):
        assert args.command == "console-requests"
        return {"requests": []}
    monkeypatch.setattr(command, "operate", operation)
    assert command.main(["console-requests"]) == 0
    assert '"requests": []' in capsys.readouterr().out


def test_console_server_refuses_owner_environment_before_start(monkeypatch, capsys):
    from platform_service.console_cli import serve_console
    monkeypatch.setenv("CENTRAL_CONSOLE_MIGRATION_DATABASE_URL", "postgresql://owner:secret-proof@127.0.0.1/test")
    with pytest.raises(ValueError, match="console_web_owner_environment_forbidden"):
        serve_console(argparse.Namespace(host="127.0.0.1", port=18875, static_dir=None))
    assert "secret-proof" not in capsys.readouterr().out


def test_console_server_only_binds_loopback(monkeypatch):
    from platform_service.console_cli import serve_console
    for name in ("CENTRAL_CONSOLE_MIGRATION_DATABASE_URL", "CENTRAL_MIGRATION_DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError, match="console_loopback_binding_required"):
        serve_console(argparse.Namespace(host="0.0.0.0", port=18875, static_dir=None))


def test_console_cli_approval_arguments_are_not_implicit_admin_identity(monkeypatch, capsys):
    import platform_service.__main__ as command
    async def operation(args):
        assert args.command == "console-approve" and args.request == "verified-request-proof"
        assert args.role == "viewer" and args.operator == "operator-proof"
        return {"approved": True}
    monkeypatch.setattr(command, "operate", operation)
    assert command.main(["console-approve", "--request=verified-request-proof", "--role", "viewer", "--operator", "operator-proof"]) == 0


@pytest.mark.asyncio
async def test_console_operator_needs_explicit_owner_dsn(monkeypatch):
    from platform_service.console_cli import operate_console
    monkeypatch.delenv("CENTRAL_CONSOLE_MIGRATION_DATABASE_URL", raising=False)
    with pytest.raises(Exception, match="console_owner_configuration_required"):
        await operate_console(argparse.Namespace(command="console-requests"))
