"""Trusted operator commands; product clients cannot publish or issue points."""

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from psycopg import Error as DatabaseError
from psycopg_pool import PoolTimeout

from .config import Settings
from .contracts import OperatorIdentity
from .db import Database
from .errors import PlatformError
from .membership import MembershipService


def _definition(path):
    with Path(path).open("rb") as source:
        raw = source.read(16385)
    if len(raw) > 16384:
        raise ValueError("definition_too_large")
    return json.loads(raw)


async def _test_payment(db, runtime, args, operator):
    """Trusted local fixture, never reachable from product HTTP or live mode."""
    if runtime.settings.mode != "test":
        raise PlatformError(503, "test_payments_not_configured")
    from .payment_runtime import payment_order
    from .payments import RefundRequest

    if (
        not re.fullmatch(r"[a-f0-9]{32}", args.order)
        or not isinstance(args.key, str)
        or not 1 <= len(args.key) <= 128
    ):
        raise PlatformError(422, "invalid_test_payment_event")
    async with db.connection() as conn:
        order = await (
            await conn.execute(
                "SELECT * FROM billing_orders WHERE id=%s", (args.order,)
            )
        ).fetchone()
        if not order:
            raise PlatformError(404, "order_not_found")
        context = runtime.contexts.get(order["channel"], {})
        if order["mode"] != "test" or any(
            order[field] != context.get(field) for field in ("merchant_id", "app_id")
        ):
            raise PlatformError(409, "test_event_conflict")
        refund = None
        if args.refund:
            row = await (
                await conn.execute(
                    "SELECT * FROM billing_refunds WHERE id=%s AND order_id=%s",
                    (args.refund, order["id"]),
                )
            ).fetchone()
            if not row:
                raise PlatformError(404, "refund_not_found")
            refund = RefundRequest(row["id"], row["amount_minor"], row["reason"])
    prepared = payment_order(
        {
            "order_id": order["id"],
            "amount_minor": str(order["amount_minor"]),
            "currency": order["currency"],
            "description": order["offer"]["name"][:100],
            "expires_at": order["expires_at"].isoformat(),
            "provider_transaction_id": order["provider_transaction_id"],
        }
    )
    event_id = (
        "operator-"
        + hashlib.sha256(json.dumps([operator.id, args.key]).encode()).hexdigest()
    )
    provider = runtime.providers[order["channel"]]
    raw, headers = provider.record(
        prepared, status=args.status, event_id=event_id, refund=refund
    )
    event = provider.verify_notification(raw, headers).to_event()
    event["raw_status"] = "SIMULATED_OPERATOR:" + operator.id
    return await runtime.billing.apply_verified_event(event)


async def operate(args):
    if args.command in {'execution-writeoff-plan', 'execution-writeoff-apply', 'execution-writeoff-operation',
                        'media-writeoff-plan', 'media-writeoff-apply', 'media-writeoff-operation', 'owner-grant'}:
        from .reconciliation_cli import operate_reconciliation
        return await operate_reconciliation(args)
    if args.command in {'identity-restore-proof', 'identity-migration-inspect', 'identity-migration-apply'}:
        from .identity_migration_cli import operate_identity_migration
        return await operate_identity_migration(args)
    if args.command in {"grant-console", "console-requests", "console-approve", "console-disable"}:
        from .console_cli import operate_console
        return await operate_console(args)
    settings = Settings.from_env()
    internal_policy = None
    if args.command == "internal-grant":
        from .internal_grants import InternalGrantPolicy

        internal_policy = InternalGrantPolicy.from_env(settings)
        if not os.environ.get("CENTRAL_MIGRATION_DATABASE_URL"):
            raise PlatformError(403, "internal_owner_required")
    administrative = args.command in {
        "migrate",
        "internal-grant",
        "grant-runtime",
        "register-product",
        "accounts",
        "model-publish",
        "model-enable",
        "model-disable",
    }
    dsn = (
        os.environ.get("CENTRAL_MIGRATION_DATABASE_URL", settings.database_url)
        if administrative
        else settings.database_url
    )
    db = Database(dsn, schema=settings.db_schema)
    await db.open()
    try:
        if args.command == "internal-grant":
            from .internal_grants import InternalGrantService

            # Deliberately separate from broad existing operator capabilities.
            operator = OperatorIdentity(args.operator, frozenset({"internal:issue"}))
            return await InternalGrantService(db, settings, internal_policy).issue(
                operator, environment=args.environment, wallet=args.wallet,
                session=args.session, key=args.key, reason=args.reason,
            )
        if args.command == "migrate":
            return {"applied": await db.migrate()}
        if args.command == "grant-runtime":
            from .permissions import grant_runtime_permissions

            async with db.connection() as conn:
                await grant_runtime_permissions(conn, args.role)
            return {"role": args.role, "granted": True}
        if args.command == "register-product":
            if not all(
                re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value)
                for value in (args.product, args.client)
            ):
                raise ValueError("invalid_product_registration")
            async with db.connection() as conn:
                await conn.execute(
                    "INSERT INTO products(id) VALUES(%s) ON CONFLICT DO NOTHING",
                    (args.product,),
                )
                await conn.execute(
                    "INSERT INTO product_clients(product_id,client_id) VALUES(%s,%s) ON CONFLICT DO NOTHING",
                    (args.product, args.client),
                )
            return {"product_id": args.product, "client_id": args.client}
        if args.command == "accounts":
            async with db.connection() as conn:
                rows = await (
                    await conn.execute(
                        "SELECT u.id AS user_id,e.email,t.id AS tenant_id,b.id AS billing_account_id FROM users u JOIN external_identities e ON e.user_id=u.id JOIN tenants t ON t.owner_user_id=u.id JOIN billing_accounts b ON b.tenant_id=t.id ORDER BY u.created_at LIMIT 100"
                    )
                ).fetchall()
            return {"accounts": rows}
        if args.command == "payments-work":
            from .payment_config import PaymentSettings
            from .payment_runtime import PaymentRuntime

            config = PaymentSettings.from_env(settings)
            if config.mode != "disabled":
                await db.require_runtime_role()
            return await PaymentRuntime(db, config).run_once(limit=args.limit)
        if args.command == "execution-work":
            from .execution.core import ExecutionService
            from .identity import IdentityService

            await db.require_runtime_role()
            execution = ExecutionService(db, IdentityService(db, settings))
            return {**await execution.recover(), "expired_results": await execution.expire_results()}
        operator = OperatorIdentity(
            args.operator,
            frozenset(
                {"plans:publish", "test:issue", "billing:publish", "billing:refund", "execution:publish"}
            ),
        )
        service = MembershipService(db)
        if args.command in {"model-publish", "model-enable", "model-disable"}:
            from .execution.core import ExecutionService
            from .identity import IdentityService

            execution = ExecutionService(db, IdentityService(db, settings))
            if args.command == "model-publish":
                return await execution.publish(_definition(args.file), operator)
            return await execution.set_enabled(args.model, args.command == "model-enable", operator)
        if args.command == "publish-plan":
            return await service.publish_plan(operator, _definition(args.file))
        if args.command == "grant-test":
            return await service.issue_test(operator, args.wallet, args.plan, args.key)
        if args.command in {
            "offer-publish",
            "offer-withdraw",
            "refund-request",
            "refund-query",
            "test-payment",
        }:
            from .payment_config import PaymentSettings
            from .payment_runtime import PaymentRuntime

            runtime = PaymentRuntime(db, PaymentSettings.from_env(settings))
            if args.command == "offer-publish":
                return await runtime.billing.publish_offer(
                    operator, _definition(args.file)
                )
            if args.command == "offer-withdraw":
                return await runtime.billing.withdraw_offer(
                    operator, args.offer, args.reason
                )
            if args.command == "refund-request":
                return await runtime.billing.request_refund(
                    operator, args.order, idempotency_key=args.key
                )
            if args.command == "refund-query":
                return await runtime.billing.get_refund(operator, args.id)
            return await _test_payment(db, runtime, args, operator)
        raise ValueError("invalid_operator_command")
    finally:
        await db.close()


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in {'identity-restore-proof', 'identity-migration-inspect', 'identity-migration-apply'}:
        # Dedicated parsing keeps private arguments out of argparse error text.
        from .identity_migration_cli import main as migration_main
        return migration_main(arguments)
    parser = argparse.ArgumentParser(
        description="Independent central accounts, plans, orders and shared points service"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ('identity-restore-proof', 'identity-migration-inspect', 'identity-migration-apply'):
        sub.add_parser(name, help='Owner-only identity continuity; use this command --help for private options')
    sub.add_parser("migrate")
    permissions = sub.add_parser("grant-runtime")
    permissions.add_argument("--role", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=18762)
    console = sub.add_parser("serve-console", help="Independent admin BFF, never loads operator credentials")
    console.add_argument("--host", default="127.0.0.1")
    console.add_argument("--port", type=int, default=18875)
    console.add_argument("--static-dir")
    console_grant = sub.add_parser("grant-console", help="Owner-only least-privilege Console database grants")
    console_grant.add_argument("--role", required=True)
    sub.add_parser("console-requests", help="Owner-only verified admin access requests")
    console_approve = sub.add_parser("console-approve", help="Owner-only approval of a verified request")
    console_approve.add_argument("--request", required=True)
    console_approve.add_argument("--role", choices=("admin", "operator", "viewer"), required=True)
    console_approve.add_argument("--operator", required=True)
    console_disable = sub.add_parser("console-disable", help="Owner-only admin revocation")
    console_disable.add_argument("--admin", required=True)
    console_disable.add_argument("--operator", required=True)
    register = sub.add_parser("register-product")
    register.add_argument("--product", required=True)
    register.add_argument("--client", required=True)
    publish = sub.add_parser("publish-plan")
    publish.add_argument("--file", required=True)
    grant = sub.add_parser("grant-test")
    for field in ("wallet", "plan", "key"):
        grant.add_argument("--" + field, required=True)
    internal = sub.add_parser("internal-grant", help="Owner-only fixed internal experience; never a payment")
    for field in ("environment", "wallet", "session", "key", "reason"):
        internal.add_argument("--" + field, required=True)
    sub.add_parser("accounts")
    model_publish = sub.add_parser("model-publish")
    model_publish.add_argument("--file", required=True)
    model_enable = sub.add_parser("model-enable")
    model_enable.add_argument("--model", required=True)
    model_disable = sub.add_parser("model-disable")
    model_disable.add_argument("--model", required=True)
    sub.add_parser("execution-work")
    writeoff_plan = sub.add_parser('execution-writeoff-plan', help='Owner-only read-only customer waiver plan; never changes money')
    writeoff_plan.add_argument('--job', dest='jobs', action='append', required=True)
    writeoff_plan.add_argument('--reason', required=True)
    writeoff_plan.add_argument('--approval-reference', required=True)
    writeoff_apply = sub.add_parser('execution-writeoff-apply', help='Owner-only approved waiver; exact reviewed plan and idempotency key required')
    writeoff_apply.add_argument('--plan', required=True)
    writeoff_apply.add_argument('--reviewed-plan-hash', required=True)
    writeoff_apply.add_argument('--key', required=True)
    writeoff_operation = sub.add_parser('execution-writeoff-operation', help='Owner-only lookup of original waiver operation; no retries or writes')
    writeoff_operation.add_argument('--key', required=True)
    media_plan = sub.add_parser('media-writeoff-plan', help='Owner-only read-only media customer waiver plan; never changes money')
    media_plan.add_argument('--job', dest='jobs', action='append', required=True)
    media_plan.add_argument('--reason', required=True)
    media_plan.add_argument('--approval-reference', required=True)
    media_apply = sub.add_parser('media-writeoff-apply', help='Owner-only approved media waiver; exact reviewed plan and idempotency key required')
    media_apply.add_argument('--plan', required=True)
    media_apply.add_argument('--reviewed-plan-hash', required=True)
    media_apply.add_argument('--key', required=True)
    media_operation = sub.add_parser('media-writeoff-operation', help='Owner-only lookup of original media waiver operation; no retries or writes')
    media_operation.add_argument('--key', required=True)
    owner_grant = sub.add_parser('owner-grant', help='Owner-only fixed allowance while payments are off; never a payment')
    for field in ('wallet', 'key', 'reason', 'approval-reference'):
        owner_grant.add_argument('--' + field, required=True)
    for child in (writeoff_plan, writeoff_apply, writeoff_operation, media_plan, media_apply, media_operation, owner_grant):
        child.add_argument('--operator', required=True, help='Audited host-owner operator identity')
    offer = sub.add_parser("offer-publish")
    offer.add_argument("--file", required=True)
    withdraw = sub.add_parser("offer-withdraw")
    withdraw.add_argument("--offer", required=True)
    withdraw.add_argument("--reason", required=True)
    work = sub.add_parser("payments-work")
    work.add_argument("--limit", type=int, default=20)
    refund = sub.add_parser("refund-request")
    refund.add_argument("--order", required=True)
    refund.add_argument("--key", required=True)
    query = sub.add_parser("refund-query")
    query.add_argument("--id", required=True)
    simulate = sub.add_parser(
        "test-payment", help="Loopback test mode only; never moves real money"
    )
    simulate.add_argument("--order", required=True)
    simulate.add_argument(
        "--status",
        choices=("paid", "closed", "refunded", "refund_failed"),
        required=True,
    )
    simulate.add_argument("--key", required=True)
    simulate.add_argument("--refund")
    for child in (publish, grant, internal, offer, withdraw, refund, query, simulate, model_publish, model_enable, model_disable):
        child.add_argument(
            "--operator", required=True, help="Audited local operator identity"
        )
    args = parser.parse_args(argv)
    try:
        if args.command == "serve-console":
            from .console_cli import serve_console
            serve_console(args)
        elif args.command == "serve":
            import uvicorn
            from .app import create_app

            uvicorn.run(
                create_app(),
                host=args.host,
                port=args.port,
                proxy_headers=False,
                access_log=False,
            )
        else:
            print(
                json.dumps(asyncio.run(operate(args)), ensure_ascii=False, default=str)
            )
        return 0
    except (ValueError, OSError, PlatformError, DatabaseError, PoolTimeout) as error:
        # Do not echo file contents, connection strings or provider responses. A
        # PlatformError code is a validated fixed token, so the operator may see it.
        code = f" ({error.code})" if isinstance(error, PlatformError) else ""
        print(
            f"Central command failed{code}; check server configuration and operator parameters.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
