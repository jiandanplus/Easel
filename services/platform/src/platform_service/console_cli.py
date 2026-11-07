"""Separated server/operator entrypoints for the central management console."""
import os
from urllib.parse import urlsplit

from .db import Database
from .contracts import OperatorIdentity
from .errors import PlatformError


def serve_console(args):
    # Failing before settings construction also avoids accidentally carrying an
    # operator secret into a long-running web process.
    if any(os.environ.get(name) for name in ("CENTRAL_CONSOLE_MIGRATION_DATABASE_URL", "CENTRAL_MIGRATION_DATABASE_URL")):
        raise ValueError("console_web_owner_environment_forbidden")
    if args.host not in {"127.0.0.1", "::1"} or type(args.port) is not int or not 1 <= args.port <= 65535:
        raise ValueError("console_loopback_binding_required")
    from .console_app import create_console_app
    from .console_config import ConsoleSettings
    import uvicorn

    settings = ConsoleSettings.from_env()
    mode = os.environ.get("CENTRAL_CONSOLE_PAYMENTS_MODE", "disabled")
    if mode not in {"disabled", "test", "live"}:
        raise ValueError("invalid_console_payment_mode")
    uvicorn.run(create_console_app(settings, static_dir=args.static_dir, payments_mode=mode),
                host=args.host, port=args.port, proxy_headers=False, access_log=False)


async def operate_console(args):
    owner = os.environ.get("CENTRAL_CONSOLE_MIGRATION_DATABASE_URL", "")
    if not owner:
        raise ValueError("console_owner_configuration_required")
    from .console_auth import ConsoleAuth
    from .console_config import ConsoleSettings
    from .console_permissions import grant_console_permissions

    settings = ConsoleSettings.from_env()
    left, right = urlsplit(owner), urlsplit(settings.database_url)
    if (left.scheme, left.hostname, left.port, left.path) != (right.scheme, right.hostname, right.port, right.path):
        raise ValueError("console_owner_target_mismatch")
    db = Database(owner, schema=settings.db_schema, min_size=1, max_size=1)
    await db.open()
    try:
        async with db.connection() as conn:
            row = await (await conn.execute("SELECT current_user=pg_get_userbyid(nspowner) AND current_user=session_user AS owned FROM pg_namespace WHERE nspname=current_schema()")).fetchone()
            if not row or not row["owned"]:
                raise PlatformError(403, "console_owner_required")
        if args.command == "grant-console":
            async with db.connection() as conn:
                await grant_console_permissions(conn, args.role)
            return {"role": args.role, "granted": True}
        auth = ConsoleAuth(db, settings)
        operator = OperatorIdentity(getattr(args, "operator", "console-owner"), frozenset({"console:bootstrap"}))
        if args.command == "console-requests":
            return await auth.list_requests(operator)
        if args.command == "console-approve":
            return await auth.approve_request(args.request, args.role, operator)
        if args.command == "console-disable":
            return await auth.disable_admin(args.admin, operator)
        raise ValueError("invalid_console_operator_command")
    finally:
        await db.close()
