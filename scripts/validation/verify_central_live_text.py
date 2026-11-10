#!/usr/bin/env python3
"""Opt-in isolated real-provider acceptance; never reads ambient .env.

Run with the platform project Python. --config is an owner-only 0600 JSON:
owner_dsn, runtime_dsn, execution_config_file, model_definition_file,
verification_ref, tls_tunnel (optional bool). Both DSNs must identify the same
literal-loopback easel_s1_test* database. A new random schema is always created.
The execution file retains https://api.drawbookai.cn/v1/responses; tls_tunnel
routes that one origin through 127.0.0.1:18767 with canonical Host/SNI and TLS
verification. Model definition rates must equal ACCEPTANCE_RATE below. They are
synthetic acceptance units, never a commercial price. --prepare-only makes no
supplier calls. All failures are sanitized; the owned schema remains for audit.
"""

import argparse
import asyncio
from datetime import timedelta
import ipaddress
import json
import os
import socket
import stat
import sys
import time
from uuid import uuid4
from urllib.parse import quote

import httpx
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
import uvicorn

from platform_service.app import create_app
from platform_service.config import Settings
from platform_service.contracts import OperatorIdentity
from platform_service.credits import journal, MAX_CREDITS
from platform_service.db import Database
from platform_service.execution.core import ExecutionService
from platform_service.execution.rates import cost
from platform_service.execution_config import ExecutionSettings
from platform_service.execution_protocol import SSEParser
from platform_service.execution_runtime import ExecutionRuntime
from platform_service.identity import IdentityService
from platform_service.membership import MembershipService
from platform_service.payment_config import PaymentSettings
from platform_service.payment_runtime import PaymentRuntime
from platform_service.permissions import grant_runtime_permissions

REQUIRED_MIGRATIONS = [
    "001_foundation.sql", "002_credits.sql", "003_identity.sql", "004_integrity.sql",
    "005_payments.sql", "006_execution.sql", "007_internal_grants.sql",
    "008_console_auth.sql", "009_console_operations.sql", "010_identity_continuity.sql",
    "011_console_reconciliation.sql",
    "012_execution_writeoffs.sql", "013_execution_evidence.sql", "014_media_execution.sql", "015_media_storage.sql",
    "016_m1_image_enablement.sql", "017_voice_stages.sql", "018_media_async_work.sql", "019_media_voice_runtime.sql", "020_media_incho_runtime.sql",
    "021_media_writeoffs.sql",
    "022_owner_grants.sql",
    "023_identity_username.sql",
    "024_console_owner_allowance.sql",
    "025_console_user_management.sql",
    "026_redemption_codes.sql",
    "027_subscription_catalog.sql",
    "028_provider_configs.sql",
    "029_provider_platforms.sql",
    "030_voice_aliases_and_xhs.sql",
    "031_model_import.sql",
    "032_provider_accounts.sql",
    "033_provider_voices.sql",
    "034_apimart_images.sql",
    "035_model_names_with_spaces.sql",
    "036_plan_states.sql",
    "037_text_price_unit.sql",
    "038_console_user_admin.sql",
]
ACCEPTANCE_CREDITS = 100_000_000
ACCEPTANCE_RATE = {
    "version": "acceptance-noncommercial-v1",
    "input": {"numerator": "1", "denominator": "2"},
    "output": {"numerator": "3", "denominator": "2"},
}
AUDIT = "acceptance_seed_no_payment"
CANONICAL = "https://api.drawbookai.cn/v1/responses"


def private_json(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.getuid()
            or info.st_size > 65536
        ):
            raise ValueError("private_file_required")
        return json.loads(stream.read(65537))


def validate_dsn(value):
    parsed = conninfo_to_dict(value)
    host = parsed.get("host", "")
    try:
        local = ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = False
    if (
        not local
        or parsed.get("hostaddr", host) != host
        or not parsed.get("dbname", "").startswith("easel_s1_test")
        or parsed.get("service")
        or parsed.get("options")
    ):
        raise ValueError("isolated_loopback_validation_database_required")
    return parsed


async def no_payment_rows(db):
    result = {}
    async with db.connection() as c:
        for table in ("billing_orders", "payment_events", "payment_transactions"):
            row = await (
                await c.execute(
                    sql.SQL("SELECT count(*) AS n FROM {}").format(
                        sql.Identifier(table)
                    )
                )
            ).fetchone()
            result[table] = row["n"]
    if any(result.values()):
        raise ValueError("unexpected_payment_rows")
    return result


async def seed_acceptance(db, identity, feature):
    """Owned-schema synthetic identity, genuine token authentication, no OIDC claim.

    Refuses an already populated schema. Never updates an existing wallet. Credit
    issuance, lot and balanced journal commit together under deferred triggers.
    """
    async with db.connection() as c:
        for table in ("users", "memberships", "credit_lots", "credit_journals"):
            row = await (
                await c.execute(
                    sql.SQL("SELECT count(*) AS n FROM {}").format(
                        sql.Identifier(table)
                    )
                )
            ).fetchone()
            if row["n"]:
                raise ValueError("fresh_schema_required")
        await c.execute("INSERT INTO users(id) VALUES ('acceptance-user')")
        await c.execute(
            "INSERT INTO tenants(id,owner_user_id) VALUES ('acceptance-tenant','acceptance-user')"
        )
        await c.execute(
            "INSERT INTO tenant_members(tenant_id,user_id,role) VALUES ('acceptance-tenant','acceptance-user','owner')"
        )
        await c.execute(
            "INSERT INTO billing_accounts(id,tenant_id) VALUES ('acceptance-wallet','acceptance-tenant')"
        )
        await c.execute("INSERT INTO products(id) VALUES ('easel')")
        await c.execute(
            "INSERT INTO product_clients(product_id,client_id) VALUES ('easel','acceptance-desktop')"
        )
        await c.execute(
            "INSERT INTO external_identities(issuer,subject,user_id) VALUES ('synthetic-acceptance','synthetic-acceptance','acceptance-user')"
        )
        row = await (
            await c.execute(
                "INSERT INTO identity_sessions(id,user_id,tenant_id,billing_account_id,product_id,client_id,authorization_epoch,issuer,subject,expires_at) VALUES ('acceptance-session','acceptance-user','acceptance-tenant','acceptance-wallet','easel','acceptance-desktop',0,'synthetic-acceptance','synthetic-acceptance',clock_timestamp()+interval '1 hour') RETURNING *"
            )
        ).fetchone()
        tokens = await identity._mint(c, row)
    operator = OperatorIdentity(AUDIT, frozenset({"plans:publish", "billing:publish"}))
    plan = dict(
        id="acceptance-noncommercial-plan",
        name="Synthetic noncommercial acceptance credit",
        version="1",
        included_products=["easel"],
        features=[feature],
        credits=str(ACCEPTANCE_CREDITS),
        days=1,
        concurrency=1,
        test_only=False,
    )
    await MembershipService(db).publish_plan(operator, plan)
    async with db.connection() as c:
        await c.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (json.dumps(["validation-issuance", AUDIT, AUDIT]),),
        )
        await c.execute(
            "SELECT id FROM billing_accounts WHERE id='acceptance-wallet' FOR UPDATE"
        )
        old = await (
            await c.execute(
                "SELECT 1 FROM memberships WHERE operator_id=%s AND issuance_key=%s",
                (AUDIT, AUDIT),
            )
        ).fetchone()
        total = await (
            await c.execute(
                "SELECT coalesce(sum(issued),0) AS n FROM credit_lots WHERE billing_account_id='acceptance-wallet'"
            )
        ).fetchone()
        if old or total["n"] + ACCEPTANCE_CREDITS > MAX_CREDITS:
            raise ValueError("issuance_conflict")
        now = (await (await c.execute("SELECT clock_timestamp() AS now")).fetchone())[
            "now"
        ]
        end, mid = now + timedelta(hours=1), str(uuid4())
        await c.execute(
            "INSERT INTO memberships(id,billing_account_id,plan_id,operator_id,issuance_key,starts_at,expires_at) VALUES (%s,'acceptance-wallet',%s,%s,%s,%s,%s)",
            (mid, plan["id"], AUDIT, AUDIT, now, end),
        )
        await c.execute(
            "INSERT INTO credit_lots(id,billing_account_id,membership_id,issued,available,expires_at,test_only) VALUES (%s,'acceptance-wallet',%s,%s,%s,%s,false)",
            (str(uuid4()), mid, ACCEPTANCE_CREDITS, ACCEPTANCE_CREDITS, end),
        )
        await journal(
            c,
            "acceptance-wallet",
            "issue",
            mid,
            {"issuance": -ACCEPTANCE_CREDITS, "available": ACCEPTANCE_CREDITS},
            AUDIT,
        )
    await no_payment_rows(db)
    await identity.authenticate(tokens["access_token"], product_id="easel")
    return tokens


class CountedTransport(httpx.AsyncBaseTransport):
    """Count physical provider dispatches; fixed route, verified TLS, zero retries."""

    def __init__(self, tunnel=False, inner=None):
        self.inner = inner or httpx.AsyncHTTPTransport(
            verify=True, retries=0, trust_env=False
        )
        self.tunnel, self.posts = tunnel, 0

    async def handle_async_request(self, request):
        if str(request.url) != CANONICAL or request.method != "POST" or self.posts >= 4:
            raise ValueError("provider_route_or_call_bound")
        self.posts += 1
        if self.tunnel:
            request.url = request.url.copy_with(host="127.0.0.1", port=18767)
            request.headers["Host"] = "api.drawbookai.cn"
            request.extensions["sni_hostname"] = "api.drawbookai.cn"
        return await self.inner.handle_async_request(request)

    async def aclose(self):
        await self.inner.aclose()


async def financial_snapshot(db):
    async with db.connection() as c:
        counts = {}
        for table in (
            "execution_jobs",
            "execution_receipts",
            "credit_reservations",
            "credit_journals",
            "credit_entries",
        ):
            row = await (
                await c.execute(
                    sql.SQL("SELECT count(*) AS n FROM {}").format(
                        sql.Identifier(table)
                    )
                )
            ).fetchone()
            counts[table] = row["n"]
        lots = await (
            await c.execute(
                "SELECT issued,available,held,spent FROM credit_lots ORDER BY id"
            )
        ).fetchall()
    return {"counts": counts, "lots": lots}


async def run_probes(client, db, model, transport, receipt):
    def checked(response):
        if response.status_code != 200:
            raise ValueError("acceptance_http_failure_" + str(response.status_code))
        return response.json()

    checked(await client.get("/v2/me"))
    catalog = checked(await client.get("/v2/products/easel/models"))
    if model not in {item["id"] for item in catalog["models"]}:
        raise ValueError("model_not_in_authorized_catalog")

    async def mint():
        return checked(
            await client.post(
                "/v2/task-tokens", json={"model": model, "invocation_key": str(uuid4())}
            )
        )

    async def operation(task, body, label, interrupt=False):
        key, started = str(uuid4()), time.monotonic()
        headers = {
            "Authorization": "Bearer " + task["task_token"],
            "Idempotency-Key": key,
        }
        record = {
            "label": label,
            "operation_key": key,
            "invocation_id": task["invocation_id"],
            "first_text_seconds": None,
            "terminal_seconds": None,
        }
        receipt["operations"].append(record)
        terminal = None
        if body.get("stream"):
            parser = SSEParser()
            async with client.stream(
                "POST", "/v2/responses", headers=headers, json=body
            ) as response:
                if response.status_code != 200:
                    raise ValueError(
                        "acceptance_stream_http_failure_" + str(response.status_code)
                    )
                record["job_id"] = response.headers.get("x-execution-id")
                async for chunk in response.aiter_bytes():
                    for _, item in parser.feed(chunk):
                        if item is None:
                            continue
                        kind = item["type"]
                        if (
                            kind == "response.output_text.delta"
                            and item.get("delta")
                            and record["first_text_seconds"] is None
                        ):
                            record["first_text_seconds"] = time.monotonic() - started
                        if kind in ("response.completed", "response.incomplete"):
                            terminal = item["response"]
                            record["terminal_seconds"] = time.monotonic() - started
                            # Terminal delivery must follow committed settlement.
                            state = checked(
                                await client.get(
                                    "/v2/execution-operations",
                                    params={
                                        "invocation_id": task["invocation_id"],
                                        "operation_key": key,
                                    },
                                )
                            )
                            if state["status"] != "settled":
                                raise ValueError("terminal_before_settlement")
                        if kind in ("error", "response.failed"):
                            raise ValueError("acceptance_upstream_stream_failure")
                    if interrupt and record["first_text_seconds"] is not None:
                        break
            if not interrupt and terminal is None:
                raise ValueError("missing_terminal")
        else:
            response = await client.post("/v2/responses", headers=headers, json=body)
            terminal = checked(response)
            record["job_id"] = response.headers.get("x-execution-id")
            record["terminal_seconds"] = time.monotonic() - started
        if interrupt:
            # Bounded cleanup window only; read-only status never dispatches.
            await asyncio.sleep(0.2)
        state = checked(
            await client.get(
                "/v2/execution-operations",
                params={"invocation_id": task["invocation_id"], "operation_key": key},
            )
        )
        record["execution"] = state
        if terminal:
            record["provider_response_id"] = terminal.get("id")
            record["usage"] = {
                "input_tokens": state["input_tokens"],
                "output_tokens": state["output_tokens"],
            }
            expected = cost(
                ACCEPTANCE_RATE, state["input_tokens"], state["output_tokens"]
            )
            record["expected_acceptance_cost"] = str(expected)
            if state["status"] != "settled" or state["actual"] != str(expected):
                raise ValueError("accounting_mismatch")
            if label in ("plain-json", "tool-continuation-sse"):
                texts = [
                    part.get("text", "")
                    for output in terminal.get("output", [])
                    if output.get("type") == "message"
                    for part in output.get("content", [])
                    if part.get("type") == "output_text"
                ]
                text = "".join(texts).strip()
                record["nonempty_output_text"] = bool(text)
                if not text or (label == "plain-json" and "ACCEPTED" not in text):
                    raise ValueError("bounded_operation_has_no_expected_text")
        return terminal, key, headers

    plain_task = await mint()
    plain_body = dict(
        model=model,
        input="Return the single word ACCEPTED.",
        max_output_tokens=128,
        stream=False,
    )
    await operation(plain_task, plain_body, "plain-json")
    # Replay identical tuple/body; check every ledger count and lot plus I/O count.
    first_key = receipt["operations"][0]["operation_key"]
    before, posts = await financial_snapshot(db), transport.posts
    checked(
        await client.post(
            "/v2/responses",
            headers={
                "Authorization": "Bearer " + plain_task["task_token"],
                "Idempotency-Key": first_key,
            },
            json=plain_body,
        )
    )
    checked(
        await client.get(
            "/v2/execution-operations",
            params={
                "invocation_id": plain_task["invocation_id"],
                "operation_key": first_key,
            },
        )
    )
    after = await financial_snapshot(db)
    if before != after or posts != transport.posts:
        raise ValueError("replay_or_lookup_mutated_ledger")
    receipt["replay_status_readonly"] = {
        "before": before,
        "after": after,
        "provider_posts_before": posts,
        "provider_posts_after": transport.posts,
    }
    task = await mint()
    tools = [
        {
            "type": "function",
            "name": "acceptance_echo",
            "description": "Deterministic acceptance marker",
            "parameters": {
                "type": "object",
                "properties": {"marker": {"type": "string", "enum": ["S3B"]}},
                "required": ["marker"],
                "additionalProperties": False,
            },
            "strict": True,
        }
    ]
    initial = [
        {
            "role": "user",
            "content": "Call acceptance_echo with marker S3B, then summarize its result in one sentence.",
        }
    ]
    terminal, _, _ = await operation(
        task,
        dict(
            model=model,
            input=initial,
            tools=tools,
            tool_choice={"type": "function", "name": "acceptance_echo"},
            max_output_tokens=256,
            stream=True,
        ),
        "forced-tool-sse",
    )
    calls = [
        item
        for item in terminal.get("output", [])
        if item.get("type") == "function_call"
    ]
    if (
        len(calls) != 1
        or calls[0].get("name") != "acceptance_echo"
        or json.loads(calls[0]["arguments"]) != {"marker": "S3B"}
    ):
        raise ValueError("unexpected_tool_call")
    continuation = (
        initial
        + terminal["output"]
        + [
            {
                "type": "function_call_output",
                "call_id": calls[0]["call_id"],
                "output": '{"marker":"S3B","verified":true}',
            }
        ]
    )
    await operation(
        task,
        dict(
            model=model,
            input=continuation,
            tools=tools,
            tool_choice="none",
            max_output_tokens=256,
            stream=True,
        ),
        "tool-continuation-sse",
    )
    task = await mint()
    await operation(
        task,
        dict(
            model=model,
            input="Write a numbered list of 80 brief synthetic acceptance markers, one per line.",
            max_output_tokens=256,
            stream=True,
        ),
        "interrupted-after-text",
        interrupt=True,
    )
    record = receipt["operations"][-1]
    if record["first_text_seconds"] is None:
        raise ValueError("interrupt_has_no_early_text")
    interrupted = record["execution"]
    if interrupted["status"] not in ("settled", "pending_reconciliation"):
        raise ValueError("interrupt_not_authoritative_terminal_or_unknown")
    if interrupted["status"] == "pending_reconciliation":
        if interrupted["actual"] is not None or int(interrupted["amount"]) <= 0:
            raise ValueError("unknown_interrupt_missing_hold")
        async with db.connection() as c:
            row = await (
                await c.execute(
                    "SELECT r.status,r.actual,r.amount FROM execution_jobs j JOIN credit_reservations r ON r.id=j.reservation_id WHERE j.id=%s",
                    (interrupted["id"],),
                )
            ).fetchone()
        if (
            row["status"] != "pending_reconciliation"
            or row["actual"] is not None
            or row["amount"] != int(interrupted["amount"])
        ):
            raise ValueError("unknown_interrupt_reservation_mismatch")
    if transport.posts != 4:
        raise ValueError("expected_four_physical_provider_posts")
    for item in receipt["operations"]:
        if item["label"] == "tool-continuation-sse" and not (
            item["first_text_seconds"] is not None
            and item["first_text_seconds"] < item["terminal_seconds"]
        ):
            raise ValueError("incremental_text_not_observed")


async def run(config, prepare_only=False):
    a, b = validate_dsn(config["owner_dsn"]), validate_dsn(config["runtime_dsn"])
    if any(
        a.get(k) != b.get(k) for k in ("host", "hostaddr", "port", "dbname")
    ) or a.get("user") == b.get("user"):
        raise ValueError("distinct_roles_same_validation_database_required")
    model = None
    execution = ExecutionSettings()
    if not prepare_only or config.get("execution_config_file"):
        model = private_json(config["model_definition_file"])
        if (
            model.get("test_only") is not False
            or model.get("verification_ref") != config["verification_ref"]
            or not config["verification_ref"]
            or model.get("rate") != ACCEPTANCE_RATE
            or model.get("limits", {}).get("max_output_tokens") != 256
            or not 1 <= model.get("limits", {}).get("max_input_tokens", 0) <= 65536
        ):
            raise ValueError("verified_bounded_acceptance_model_required")
        execution = ExecutionSettings.from_env(
            None, env={"CENTRAL_EXECUTION_CONFIG_FILE": config["execution_config_file"]}
        )
        if (
            execution.mode != "live"
            or execution.worker_enabled
            or execution.concurrency != 1
            or execution.timeout_seconds not in (90, 120)
            or set(execution.bindings) != {model["id"]}
            or any(p.endpoint != CANONICAL for p in execution.providers.values())
        ):
            raise ValueError("fixed_live_execution_configuration_required")
    schema = "s3b_live_acceptance_" + uuid4().hex
    receipt = {
        "schema": schema,
        "auth": "synthetic owned session; real IdentityService token authentication; no OIDC acceptance",
        "funding": "synthetic noncommercial live acceptance credit; no payment",
        "rate": ACCEPTANCE_RATE,
        "operations": [],
        "provider_posts": 0,
        "prepared_only": prepare_only,
    }
    async with await psycopg.AsyncConnection.connect(
        config["owner_dsn"], autocommit=True
    ) as c:
        await c.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    owner = Database(config["owner_dsn"], schema=schema)
    runtime_db = Database(config["runtime_dsn"], schema=schema)
    server = server_task = transport = None
    sock = None
    try:
        receipt["stage"] = "migrations"
        await owner.open()
        receipt["migrations"] = await owner.migrate()
        if receipt["migrations"] != REQUIRED_MIGRATIONS:
            raise ValueError("reviewed_fresh_migrations_required")
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(128)
        port = sock.getsockname()[1]
        settings_host = "[" + b["host"] + "]" if ":" in b["host"] else b["host"]
        settings_dsn = (
            "postgresql://"
            + quote(b["user"], safe="")
            + ":"
            + quote(b.get("password", ""), safe="")
            + "@"
            + settings_host
            + ":"
            + b.get("port", "5432")
            + "/"
            + quote(b["dbname"], safe="")
        )
        settings = Settings(
            settings_dsn,
            "http://127.0.0.1:" + str(port),
            allow_loopback_http=True,
            db_schema=schema,
        )
        identity = IdentityService(owner, settings)
        receipt["stage"] = "identity_and_acceptance_credit"
        tokens = await seed_acceptance(
            owner, identity, model["feature"] if model else "text"
        )
        if model:
            service = ExecutionService(owner, identity)
            operator = OperatorIdentity(AUDIT, frozenset({"execution:publish"}))
            await service.publish(model, operator)
            await service.set_enabled(model["id"], True, operator)
        async with owner.connection() as c:
            receipt["stage"] = "runtime_permissions"
            await grant_runtime_permissions(c, b["user"])
        receipt["stage"] = "runtime_open"
        await runtime_db.open()
        await runtime_db.require_runtime_role()
        transport = CountedTransport(config.get("tls_tunnel", False))
        identity = IdentityService(runtime_db, settings)
        runtime = ExecutionRuntime(runtime_db, identity, execution, transport=transport)
        app = create_app(
            settings,
            database=runtime_db,
            identity=identity,
            execution_runtime=runtime,
            payment_runtime=PaymentRuntime(runtime_db, PaymentSettings()),
        )
        receipt["stage"] = "owned_http_start"
        server = uvicorn.Server(
            uvicorn.Config(app, log_level="critical", access_log=False, lifespan="on")
        )
        server_task = asyncio.create_task(server.serve(sockets=[sock]))
        async with asyncio.timeout(10):
            while not server.started:
                if server_task.done():
                    raise ValueError("owned_server_failed")
                await asyncio.sleep(0.02)
        async with httpx.AsyncClient(
            base_url=settings.public_url,
            headers={"Authorization": "Bearer " + tokens["access_token"]},
            timeout=130,
            trust_env=False,
        ) as client:
            receipt["stage"] = "http_prepare" if prepare_only else "live_probes"
            if prepare_only:
                if (await client.get("/v2/me")).status_code != 200 or (
                    await client.get("/v2/products/easel/models")
                ).status_code != 200:
                    raise ValueError("prepare_auth_failed")
            else:
                await run_probes(client, owner, model["id"], transport, receipt)
        receipt["no_payment_rows"] = await no_payment_rows(owner)
        async with owner.connection() as c:
            receipt["settlement_receipts"] = await (
                await c.execute(
                    "SELECT job_id,attempt_id,input_tokens,output_tokens,terminal_status,provider_response_id,actual,created_at FROM execution_receipts ORDER BY created_at"
                )
            ).fetchall()
        for settled in receipt["settlement_receipts"]:
            settled["expected_acceptance_cost"] = cost(
                ACCEPTANCE_RATE, settled["input_tokens"], settled["output_tokens"]
            )
            if settled["actual"] != settled["expected_acceptance_cost"]:
                raise ValueError("settlement_receipt_cost_mismatch")
        receipt["financial_snapshot"] = await financial_snapshot(owner)
        receipt["success"] = True
        receipt["stage"] = "complete"
    except Exception:
        receipt["success"] = False
        receipt["failure"] = (
            "validation_stopped; inspect private owned schema; no automatic retry"
        )
    finally:
        if transport:
            receipt["provider_posts"] = transport.posts
        if server:
            server.should_exit = True
        if server_task:
            try:
                await asyncio.wait_for(server_task, 10)
            except (TimeoutError, asyncio.CancelledError):
                server_task.cancel()
                await asyncio.gather(server_task, return_exceptions=True)
        if sock:
            sock.close()
        if transport:
            await transport.aclose()
        await runtime_db.close()
        await owner.close()
    return receipt


def reserve_receipt(path):
    # Pin the immediate parent before checking it or creating the leaf. Never
    # resolve a final parent symlink or reopen the destination by pathname.
    parent, leaf = os.path.split(os.path.abspath(path))
    directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(directory)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError("private_receipt_directory_required")
        fd = os.open(
            leaf,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
            ):
                raise ValueError("private_receipt_required")
            return os.fdopen(fd, "w")
        except BaseException:
            os.close(fd)
            raise
    finally:
        os.close(directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    try:
        with reserve_receipt(args.receipt) as out:
            result = asyncio.run(run(private_json(args.config), args.prepare_only))
            json.dump(result, out, indent=2, default=str)
            out.write("\n")
        print(
            json.dumps(
                {
                    "success": result["success"],
                    "schema": result["schema"],
                    "provider_posts": result["provider_posts"],
                    "prepared_only": args.prepare_only,
                }
            )
        )
        return 0 if result["success"] else 1
    except Exception:
        print(
            "Live validation stopped safely; no secret diagnostics emitted.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
