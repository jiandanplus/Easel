"""Explicit host-owner plans and reviewed unknown-cost customer waivers.

No HTTP route exposes these commands. Provider usage remains unknown; a waiver
is a separate audited business decision and never a fabricated model receipt.
"""
import json
import os
from pathlib import Path
import stat
from urllib.parse import urlsplit

from .config import Settings
from .contracts import OperatorIdentity
from .db import Database
from .errors import PlatformError


def read_plan(path):
    try:
        fd = os.open(Path(path), os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        raise ValueError('private_reconciliation_plan_required') from None
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError('private_reconciliation_plan_required')
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError('invalid_reconciliation_plan_file')
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise ValueError('invalid_reconciliation_plan_file') from None
    if not isinstance(value, dict):
        raise ValueError('invalid_reconciliation_plan_file')
    return value


async def operate_reconciliation(args):
    owner = os.environ.get('CENTRAL_MIGRATION_DATABASE_URL', '')
    if not owner:
        raise PlatformError(403, 'reconciliation_owner_required')
    settings = Settings.from_env()
    left, right = urlsplit(owner), urlsplit(settings.database_url)
    if (left.scheme not in {'postgres', 'postgresql'}
            or (left.hostname, left.port, left.path) != (right.hostname, right.port, right.path)):
        raise PlatformError(403, 'reconciliation_owner_target_mismatch')
    db = Database(owner, schema=settings.db_schema, min_size=1, max_size=1)
    await db.open()
    try:
        async with db.connection() as conn:
            row = await (await conn.execute(
                'SELECT current_user=session_user AND current_user=pg_get_userbyid(nspowner) AS owned '
                'FROM pg_namespace WHERE nspname=current_schema()'
            )).fetchone()
            if not row or not row['owned']:
                raise PlatformError(403, 'reconciliation_owner_required')
        if args.command == 'owner-grant':
            from .owner_grants import OwnerGrantService
            return await OwnerGrantService(db).issue(
                OperatorIdentity(args.operator, frozenset({'owner:grant'})), wallet=args.wallet,
                key=args.key, reason=args.reason, approval_reference=args.approval_reference)
        # This capability is deliberately absent from all generic operator and
        # Console roles; only this independently verified host-owner entry uses it.
        operator = OperatorIdentity(args.operator, frozenset({'execution:writeoff'}))
        if args.command.startswith('media-'):
            from .media.reconciliation import MediaReconciliation
            media = MediaReconciliation(db)
            if args.command == 'media-writeoff-plan':
                return await media.plan_waivers(operator, args.jobs, args.reason, args.approval_reference)
            if args.command == 'media-writeoff-apply':
                return await media.apply_waivers(operator, read_plan(args.plan), args.reviewed_plan_hash, args.key)
            if args.command == 'media-writeoff-operation':
                return await media.operation(operator, args.key)
            raise ValueError('invalid_reconciliation_operator_command')
        from .execution.core import ExecutionService
        from .execution.reconciliation import ExecutionReconciliation
        service = ExecutionReconciliation(ExecutionService(db, None))
        if args.command == 'execution-writeoff-plan':
            return await service.plan_waivers(operator, args.jobs, args.reason, args.approval_reference)
        if args.command == 'execution-writeoff-apply':
            return await service.apply_waivers(operator, read_plan(args.plan), args.reviewed_plan_hash, args.key)
        if args.command == 'execution-writeoff-operation':
            return await service.operation(operator, args.key)
        raise ValueError('invalid_reconciliation_operator_command')
    finally:
        await db.close()
