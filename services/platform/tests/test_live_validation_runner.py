"""Safety and real PostgreSQL accounting checks for the opt-in live runner."""

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def runner():
    path = (
        Path(__file__).resolve().parents[3]
        / "scripts/validation/verify_central_live_text.py"
    )
    spec = importlib.util.spec_from_file_location("live_validation_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://u:p@example.com/easel_s1_test",
        "postgresql://u:p@localhost/easel_s1_test",
        "postgresql://u:p@127.0.0.1/production",
        "host=127.0.0.1 hostaddr=8.8.8.8 dbname=easel_s1_test",
    ],
)
def test_rejects_nonisolated_database(runner, dsn):
    with pytest.raises(ValueError):
        runner.validate_dsn(dsn)


@pytest.mark.asyncio
async def test_seed_balanced_live_acceptance_credit_without_payment(db, runner):
    from platform_service.config import Settings
    from platform_service.identity import IdentityService
    from platform_service.credits import CreditLedger

    settings = Settings(
        "postgresql://unused@127.0.0.1/easel_s1_test",
        "http://127.0.0.1:19999",
        allow_loopback_http=True,
    )
    identity = IdentityService(db, settings)
    token = await runner.seed_acceptance(db, identity, "text")
    scope = await identity.authenticate(token["access_token"], product_id="easel")
    assert (await identity.me(scope))["account"]["id"] == "acceptance-user"
    summary = await CreditLedger(db).summary(scope)
    assert summary["issued"] == summary["available"] == str(runner.ACCEPTANCE_CREDITS)
    assert summary["test_only"] is False
    assert await runner.no_payment_rows(db) == {
        "billing_orders": 0,
        "payment_events": 0,
        "payment_transactions": 0,
    }
    async with db.connection() as c:
        row = await (
            await c.execute("SELECT sum(delta) AS total FROM credit_entries")
        ).fetchone()
        assert row["total"] == 0
        row = await (
            await c.execute("SELECT operator_id,issuance_key FROM memberships")
        ).fetchone()
        assert row["operator_id"] == row["issuance_key"] == "acceptance_seed_no_payment"
        assert (
            await (await c.execute("SELECT test_only FROM credit_lots")).fetchone()
        )["test_only"] is False
    with pytest.raises(Exception):
        await runner.seed_acceptance(db, identity, "text")
    assert (await CreditLedger(db).summary(scope))["issued"] == str(
        runner.ACCEPTANCE_CREDITS
    )


@pytest.mark.asyncio
async def test_fixed_tls_transport_counts_and_rejects_fifth_call(runner):
    import httpx

    seen = []

    async def capture(request):
        seen.append(
            (
                str(request.url),
                request.headers["host"],
                request.extensions.get("sni_hostname"),
            )
        )
        return httpx.Response(200, json={})

    transport = runner.CountedTransport(True, httpx.MockTransport(capture))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ValueError):
            await client.post("https://unapproved.invalid/v1/responses")
        assert transport.posts == 0
        for _ in range(4):
            await client.post(runner.CANONICAL)
        with pytest.raises(ValueError):
            await client.post(runner.CANONICAL)
    assert transport.posts == 4
    assert (
        seen
        == [
            (
                "https://127.0.0.1:18767/v1/responses",
                "api.drawbookai.cn",
                "api.drawbookai.cn",
            )
        ]
        * 4
    )


@pytest.mark.asyncio
async def test_prepare_only_real_http_auth_and_zero_provider_calls(test_dsn, runner):
    import secrets
    from uuid import uuid4
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    role = "s3b_prepare_" + uuid4().hex
    password = secrets.token_urlsafe(24)
    async with await psycopg.AsyncConnection.connect(test_dsn, autocommit=True) as c:
        await c.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password)
            )
        )
    receipt = None
    try:
        receipt = await runner.run(
            {
                "owner_dsn": test_dsn,
                "runtime_dsn": make_conninfo(test_dsn, user=role, password=password),
            },
            prepare_only=True,
        )
        assert receipt["success"] is True, receipt
        assert receipt["prepared_only"] is True
        assert receipt["provider_posts"] == 0
        assert receipt["migrations"] == [
            "001_foundation.sql", "002_credits.sql", "003_identity.sql", "004_integrity.sql",
            "005_payments.sql", "006_execution.sql", "007_internal_grants.sql",
            "008_console_auth.sql", "009_console_operations.sql", "010_identity_continuity.sql",
            "011_console_reconciliation.sql",
            "012_execution_writeoffs.sql", "013_execution_evidence.sql", "014_media_execution.sql", "015_media_storage.sql",
            "016_m1_image_enablement.sql", "017_voice_stages.sql", "018_media_async_work.sql", "019_media_voice_runtime.sql", "020_media_incho_runtime.sql",
            "021_media_writeoffs.sql",
        ]
        assert receipt["no_payment_rows"] == {
            "billing_orders": 0,
            "payment_events": 0,
            "payment_transactions": 0,
        }
        assert receipt["financial_snapshot"]["counts"]["credit_journals"] == 1
    finally:
        async with await psycopg.AsyncConnection.connect(
            test_dsn, autocommit=True
        ) as c:
            if receipt:
                await c.execute(
                    sql.SQL("DROP SCHEMA {} CASCADE").format(
                        sql.Identifier(receipt["schema"])
                    )
                )
            await c.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            await c.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


@pytest.mark.asyncio
async def test_four_operation_protocol_fixture_ledger_replay_and_interrupt(
    test_dsn, runner, tmp_path, monkeypatch
):
    """Synthetic upstream only: live-mode ledger mechanics are not live evidence."""
    import asyncio
    import json
    import secrets
    from uuid import uuid4
    import httpx
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from test_execution_core import definition

    model = definition()
    model.update(
        test_only=False,
        verification="live",
        verification_ref="synthetic-protocol-fixture-only",
        rate=runner.ACCEPTANCE_RATE,
    )
    model["limits"]["max_output_tokens"] = 256
    paths = {}
    execution = {
        "mode": "live",
        "providers": {
            "fixture": {
                "version": "1",
                "endpoint": runner.CANONICAL,
                "secret": "synthetic-not-a-real-provider-key",
                "models": {model["id"]: "synthetic-upstream"},
            }
        },
        "concurrency": 1,
        "timeout_seconds": 90,
        "worker_enabled": False,
    }
    for name, value in (("model", model), ("execution", execution)):
        path = tmp_path / (name + ".json")
        path.write_text(json.dumps(value))
        path.chmod(0o600)
        paths[name] = str(path)
    calls = []

    class Stream(httpx.AsyncByteStream):
        def __init__(self, response, interrupt):
            self.response, self.interrupt = response, interrupt

        async def __aiter__(self):
            yield b'data: {"type":"response.output_text.delta","delta":"synthetic"}\n\n'
            await asyncio.sleep(0.05 if not self.interrupt else 2)
            yield (
                "data: "
                + json.dumps({"type": "response.completed", "response": self.response})
                + "\n\n"
            ).encode()

    async def upstream(request):
        body = json.loads(request.content)
        calls.append(body)
        output = (
            [
                {
                    "type": "function_call",
                    "id": "fc_synthetic",
                    "call_id": "call_synthetic",
                    "name": "acceptance_echo",
                    "arguments": '{"marker":"S3B"}',
                }
            ]
            if len(calls) == 2
            else [
                {"type": "reasoning", "content": None, "summary": []},
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": "ACCEPTED synthetic marker"}
                    ],
                }
            ]
        )
        response = {
            "id": "resp_synthetic_" + str(len(calls)),
            "status": "completed",
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "output": output,
        }
        if body.get("stream"):
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=Stream(response, len(calls) == 4),
            )
        return httpx.Response(200, json=response)

    original_init = runner.CountedTransport.__init__

    def fixture_init(self, tunnel=False, inner=None):
        original_init(self, tunnel, httpx.MockTransport(upstream))

    monkeypatch.setattr(runner.CountedTransport, "__init__", fixture_init)
    role = "s3b_protocol_" + uuid4().hex
    password = "synthetic-" + secrets.token_urlsafe(24)
    async with await psycopg.AsyncConnection.connect(test_dsn, autocommit=True) as c:
        await c.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password)
            )
        )
    receipt = None
    try:
        receipt = await runner.run(
            {
                "owner_dsn": test_dsn,
                "runtime_dsn": make_conninfo(test_dsn, user=role, password=password),
                "execution_config_file": paths["execution"],
                "model_definition_file": paths["model"],
                "verification_ref": model["verification_ref"],
            }
        )
        assert receipt["success"] is True, receipt
        assert len(calls) == receipt["provider_posts"] == 4
        assert len(receipt["operations"]) == 4
        assert (
            receipt["replay_status_readonly"]["before"]
            == receipt["replay_status_readonly"]["after"]
        )
        assert (
            receipt["operations"][-1]["execution"]["status"] == "pending_reconciliation"
        )
        assert int(receipt["financial_snapshot"]["lots"][0]["held"]) > 0
    finally:
        async with await psycopg.AsyncConnection.connect(
            test_dsn, autocommit=True
        ) as c:
            if receipt:
                await c.execute(
                    sql.SQL("DROP SCHEMA {} CASCADE").format(
                        sql.Identifier(receipt["schema"])
                    )
                )
            await c.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            await c.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


@pytest.mark.parametrize(
    "destination",
    [
        "existing",
        "public_parent",
        "symlink_parent",
        "symlink_leaf",
        "missing_parent",
        "unwritable",
    ],
)
def test_main_rejects_receipt_before_config_or_run(
    runner, tmp_path, monkeypatch, capsys, destination
):
    parent = tmp_path / "private"
    parent.mkdir(mode=0o700)
    receipt = parent / "receipt.json"
    if destination == "existing":
        receipt.write_text("original")
    elif destination == "public_parent":
        parent.chmod(0o755)
    elif destination == "symlink_parent":
        alias = tmp_path / "alias"
        alias.symlink_to(parent, target_is_directory=True)
        receipt = alias / "receipt.json"
    elif destination == "symlink_leaf":
        target = parent / "original"
        target.write_text("original")
        receipt.symlink_to(target)
    elif destination == "missing_parent":
        receipt = tmp_path / "missing" / "receipt.json"
    elif destination == "unwritable":
        parent.chmod(0o500)
    calls = []
    monkeypatch.setattr(runner, "private_json", lambda path: calls.append("config"))

    async def fake_run(*args):
        calls.append("run")
        return {"success": True, "schema": "safe-schema", "provider_posts": 0}

    monkeypatch.setattr(runner, "run", fake_run)
    monkeypatch.setattr(
        "sys.argv", ["runner", "--config", "unused", "--receipt", str(receipt)]
    )
    assert runner.main() == 1
    assert calls == []
    assert (
        capsys.readouterr().err
        == "Live validation stopped safely; no secret diagnostics emitted.\n"
    )
    if destination == "existing":
        assert receipt.read_text() == "original"


@pytest.mark.parametrize("success", [True, False])
def test_main_reserves_private_receipt_and_keeps_run_metadata(
    runner, tmp_path, monkeypatch, capsys, success
):
    import json
    import os
    import stat

    parent = tmp_path / "private"
    parent.mkdir(mode=0o700)
    receipt = parent / "receipt.json"
    result = {
        "success": success,
        "schema": "safe-schema",
        "provider_posts": 3,
        "stage": "complete" if success else "live_probes",
    }
    monkeypatch.setattr(runner, "private_json", lambda path: {})

    async def fake_run(*args):
        assert receipt.exists()
        info = receipt.stat()
        assert stat.S_ISREG(info.st_mode)
        assert stat.S_IMODE(info.st_mode) == 0o600
        assert info.st_uid == os.getuid()
        return result

    monkeypatch.setattr(runner, "run", fake_run)
    monkeypatch.setattr(
        "sys.argv", ["runner", "--config", "unused", "--receipt", str(receipt)]
    )
    assert runner.main() == (0 if success else 1)
    assert json.loads(receipt.read_text()) == result
    assert json.loads(capsys.readouterr().out)["schema"] == "safe-schema"
