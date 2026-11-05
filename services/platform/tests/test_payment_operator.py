import argparse
import json

import pytest

from platform_service.__main__ import main, operate
from platform_service.credits import CreditLedger
from platform_service.errors import PlatformError
from test_payment_runtime import setup_runtime


def configure(monkeypatch, db, test_dsn, tmp_path, runtime):
    monkeypatch.setenv("CENTRAL_DATABASE_URL", test_dsn)
    monkeypatch.setenv("CENTRAL_DB_SCHEMA", db.schema)
    monkeypatch.setenv("CENTRAL_PUBLIC_URL", "http://127.0.0.1:18762")
    monkeypatch.setenv("CENTRAL_ALLOW_LOOPBACK_HTTP", "1")
    monkeypatch.setenv("CENTRAL_PAYMENTS_MODE", "test")
    monkeypatch.delenv("CENTRAL_MIGRATION_DATABASE_URL", raising=False)
    key = tmp_path / "test.key"
    key.write_bytes(runtime.settings.test_key)
    key.chmod(0o600)
    config = tmp_path / "payment.json"
    config.write_text(
        json.dumps(
            {
                "test_key_file": str(key),
                "test_state_file": str(runtime.settings.test_state_file),
            }
        )
    )
    config.chmod(0o600)
    monkeypatch.setenv("CENTRAL_PAYMENTS_CONFIG_FILE", str(config))


def command(name, **values):
    return argparse.Namespace(command=name, operator="fixture-operator", **values)


@pytest.mark.asyncio
async def test_operator_publishes_and_withdraws_immutable_offer(
    db, test_dsn, tmp_path, monkeypatch
):
    runtime, _, _, order = await setup_runtime(db, tmp_path)
    configure(monkeypatch, db, test_dsn, tmp_path, runtime)
    offer = {
        "id": "operator-offer",
        "plan_id": order["offer"]["plan_id"],
        "version": "2",
        "amount_minor": "2",
        "currency": "CNY",
        "test_only": True,
        "terms": {"refund_policy": "unused_full_only", "description": "模拟测试价格"},
    }
    file = tmp_path / "offer.json"
    file.write_text(json.dumps(offer))
    args = command("offer-publish", file=str(file))
    assert (await operate(args))["amount_minor"] == "2"
    assert await operate(args) == await operate(args)
    assert (
        await operate(
            command("offer-withdraw", offer="operator-offer", reason="test completed")
        )
    )["withdrawn"]
    assert "operator-offer" not in [
        item["id"] for item in (await runtime.billing.catalog("easel"))["offers"]
    ]


@pytest.mark.asyncio
async def test_operator_simulation_payment_refund_and_replay_are_audited(
    db, test_dsn, tmp_path, monkeypatch
):
    runtime, operator, scope, order = await setup_runtime(db, tmp_path)
    configure(monkeypatch, db, test_dsn, tmp_path, runtime)
    await runtime.run_once()
    paid = command(
        "test-payment", order=order["id"], status="paid", key="cli-paid", refund=None
    )
    assert (await operate(paid))["credit_state"] == "granted"
    await operate(paid)
    assert (await CreditLedger(db).summary(scope))["issued"] == "100000000"
    request = command("refund-request", order=order["id"], key="cli-refund")
    refund = await operate(request)
    assert refund == await operate(request)
    assert (await CreditLedger(db).summary(scope))["frozen"] == "100000000"
    assert (await operate(command("refund-query", id=refund["id"])))[
        "status"
    ] == "pending"
    completed = command(
        "test-payment",
        order=order["id"],
        status="refunded",
        key="cli-refunded",
        refund=refund["id"],
    )
    assert (await operate(completed))["status"] == "refunded"
    await operate(completed)
    assert (await CreditLedger(db).summary(scope))["refunded"] == "100000000"
    assert (await runtime.billing.get_refund(operator, refund["id"]))[
        "status"
    ] == "succeeded"


@pytest.mark.asyncio
async def test_simulation_fails_closed_when_disabled_and_does_not_issue(
    db, test_dsn, tmp_path, monkeypatch
):
    runtime, _, scope, order = await setup_runtime(db, tmp_path)
    configure(monkeypatch, db, test_dsn, tmp_path, runtime)
    monkeypatch.setenv("CENTRAL_PAYMENTS_MODE", "disabled")
    with pytest.raises(PlatformError) as failure:
        await operate(
            command(
                "test-payment",
                order=order["id"],
                status="paid",
                key="never",
                refund=None,
            )
        )
    assert failure.value.code == "test_payments_not_configured"
    assert (await CreditLedger(db).summary(scope))["issued"] == "0"


@pytest.mark.asyncio
async def test_worker_command_has_no_operator_and_rejects_database_owner(
    db, test_dsn, tmp_path, monkeypatch
):
    runtime, _, _, _ = await setup_runtime(db, tmp_path)
    configure(monkeypatch, db, test_dsn, tmp_path, runtime)
    with pytest.raises(PlatformError) as failure:
        await operate(argparse.Namespace(command="payments-work", limit=1))
    assert failure.value.code == "unsafe_runtime_role"
    monkeypatch.setenv("CENTRAL_PAYMENTS_MODE", "disabled")
    assert (await operate(argparse.Namespace(command="payments-work", limit=1)))[
        "claimed"
    ] == 0


@pytest.mark.parametrize(
    "argv",
    [
        ["offer-publish", "--file", "/no-file", "--operator", "op"],
        ["payments-work", "--limit", "1"],
        [
            "test-payment",
            "--order",
            "0" * 32,
            "--status",
            "paid",
            "--key",
            "key",
            "--operator",
            "op",
        ],
        ["refund-query", "--id", "0" * 32, "--operator", "op"],
    ],
)
def test_new_operator_commands_have_stable_safe_errors(monkeypatch, capsys, argv):
    async def unavailable(args):
        raise PlatformError(503, "private-material-never-print")

    import platform_service.__main__ as module

    monkeypatch.setattr(module, "operate", unavailable)
    assert main(argv) == 1
    output = capsys.readouterr()
    assert "private-material" not in output.out + output.err
