"""Opaque IDs may start with dashes: CLI wrappers must use --option=value."""
import pytest

from platform_service import __main__ as cli


def test_internal_grant_equal_arguments_preserve_opaque_ids_without_database(monkeypatch, capsys):
    captured=[]
    async def operate(args):
        captured.append(vars(args))
        return {"parsed":True}
    monkeypatch.setattr(cli,"operate",operate)
    assert cli.main([
        "internal-grant",
        "--environment=wb-synthetic-environment",
        "--wallet=--WALLET-CANARY",
        "--session=-SESSION-CANARY",
        "--key=initial-synthetic-allowance",
        "--reason=User approved fixed synthetic regression",
        "--operator=synthetic-operator",
    ])==0
    assert captured==[{
        "command":"internal-grant",
        "environment":"wb-synthetic-environment",
        "wallet":"--WALLET-CANARY",
        "session":"-SESSION-CANARY",
        "key":"initial-synthetic-allowance",
        "reason":"User approved fixed synthetic regression",
        "operator":"synthetic-operator",
    }]
    assert capsys.readouterr().out.strip()=='{"parsed": true}'


@pytest.mark.parametrize("field,value",[("session","-SESSION-CANARY"),("wallet","--WALLET-CANARY")])
def test_separate_dash_value_stops_before_operation(monkeypatch,capsys,field,value):
    async def forbidden(args):
        raise AssertionError("invalid CLI parsing must never reach database operation")
    monkeypatch.setattr(cli,"operate",forbidden)
    arguments=["internal-grant","--environment=wb-synthetic-environment","--key=synthetic-key",
        "--reason=synthetic regression","--operator=synthetic-operator"]
    arguments += ["--wallet=wallet"] if field=="session" else ["--session=session"]
    with pytest.raises(SystemExit) as error:
        cli.main([*arguments,"--"+field,value])
    assert error.value.code==2
    assert "expected one argument" in capsys.readouterr().err
