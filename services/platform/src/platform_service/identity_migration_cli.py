"""Private owner commands for verified identity continuity, never Web handlers."""
import argparse
import asyncio
from contextlib import contextmanager
import json
import logging
import os
import re
import sys
from datetime import datetime

from psycopg import Error as DatabaseError
from psycopg.conninfo import conninfo_to_dict
from psycopg_pool import PoolTimeout

from .config import Settings
from .contracts import OperatorIdentity
from .db import Database
from .errors import PlatformError
from .identity_migration import IdentityMigrationService


COMMANDS = ('identity-restore-proof', 'identity-migration-inspect', 'identity-migration-apply')


@contextmanager
def _private_database_logs():
    # This is a dedicated maintenance process. Driver retry warnings can contain
    # private role/database/certificate identifiers before our fixed exception
    # handler runs. Suppress them for this operation, including pool cleanup.
    loggers = [logging.getLogger(name) for name in ('psycopg', 'psycopg.pool')]
    states = [logger.disabled for logger in loggers]
    try:
        for logger in loggers:
            logger.disabled = True
        yield
    finally:
        for logger, state in zip(loggers, states):
            logger.disabled = state


def require_same_database(owner, runtime):
    """Credentials may differ, but routing and the actual database must not."""
    try:
        left, right = conninfo_to_dict(owner), conninfo_to_dict(runtime)
        for item in (left, right):
            if any(key in item for key in ('service', 'hostaddr', 'options')):
                raise ValueError()
            if not item.get('host') or not item.get('dbname') or ',' in item['host']:
                raise ValueError()
        if any(left.get(key, default) != right.get(key, default)
               for key, default in (('host', ''), ('port', '5432'), ('dbname', ''))):
            raise ValueError()
    except (ValueError, TypeError, DatabaseError):
        raise ValueError('identity_migration_owner_target_mismatch') from None


async def _require_owner(db):
    async with db.connection() as conn:
        row = await (await conn.execute('''SELECT current_user=session_user AND n.nspowner=r.oid AS owned
            FROM pg_namespace n JOIN pg_roles r ON r.rolname=current_user
            WHERE n.nspname=current_schema()''')).fetchone()
        if not row or not row['owned']:
            raise PlatformError(403, 'identity_migration_owner_required')


async def operate_identity_migration(args):
    owner = os.environ.get('CENTRAL_MIGRATION_DATABASE_URL')
    if not owner:
        raise ValueError('identity_migration_owner_required')
    settings = Settings.from_env()
    if not settings.db_schema:
        raise ValueError('identity_migration_schema_required')
    require_same_database(owner, settings.database_url)
    from .identity_restore import read_private_restore_config, collect_restore_evidence
    config = read_private_restore_config(args.config_file)
    capability = 'identity:collect' if args.command == 'identity-restore-proof' else 'identity:migrate'
    operator = OperatorIdentity(args.operator, frozenset({capability}))
    with _private_database_logs():
        db = Database(owner, schema=settings.db_schema, min_size=1, max_size=1)
        try:
            await db.open()
            # Validate actual privilege before opening the identity databases,
            # rather than after the sensitive collector has already run.
            await _require_owner(db)
            service = IdentityMigrationService(db, config.policy)
            if args.command == 'identity-restore-proof':
                evidence = await collect_restore_evidence(config.policy,
                    source_dsn=config.source_dsn, target_dsn=config.target_dsn,
                    source_schema=config.source_schema, target_schema=config.target_schema,
                    backup_path=config.backup_path)
                return await service.record_proof(evidence, operator)
            if args.command == 'identity-migration-inspect':
                return await service.inspect(args.proof_id, operator)
            if args.command == 'identity-migration-apply':
                return await service.apply(args.proof_id, args.key, args.reason, operator)
            raise ValueError('identity_migration_command_invalid')
        finally:
            await db.close()


def add_arguments(parser, command):
    parser.add_argument('--config-file', required=True, help='Protected local restore configuration path')
    parser.add_argument('--operator', required=True, help='Audited maintenance operator')
    if command != 'identity-restore-proof':
        parser.add_argument('--proof-id', required=True)
    if command == 'identity-migration-apply':
        parser.add_argument('--key', required=True)
        parser.add_argument('--reason', required=True)


class PrivateParser(argparse.ArgumentParser):
    def error(self, _message):
        raise ValueError('invalid_identity_migration_arguments')


def _safe_summary(value):
    if not isinstance(value, dict):
        raise ValueError('invalid_identity_migration_result')
    allowed = {'proof_id', 'migration_id', 'product_count', 'console_count', 'candidate_sha256',
               'result_sha256', 'expires_at', 'conflict_count', 'consumed', 'replayed'}
    result = {}
    for key in allowed & value.keys():
        item = value[key]
        if key in {'proof_id', 'migration_id'}:
            valid = isinstance(item, str) and re.fullmatch(r'[a-f0-9]{32}', item)
        elif key.endswith('_sha256'):
            valid = isinstance(item, str) and re.fullmatch(r'[a-f0-9]{64}', item)
        elif key.endswith('_count'):
            valid = type(item) is int and 0 <= item <= 1000000
        elif key in {'consumed', 'replayed'}:
            valid = type(item) is bool
        else:
            valid = isinstance(item, str) and len(item) <= 64
            if valid:
                try: valid = datetime.fromisoformat(item).tzinfo is not None
                except ValueError: valid = False
        if not valid:
            raise ValueError('invalid_identity_migration_result')
        result[key] = item
    if not result:
        raise ValueError('invalid_identity_migration_result')
    return result


def main(argv=None):
    try:
        parser = PrivateParser(description=__doc__)
        sub = parser.add_subparsers(dest='command', required=True)
        for name in COMMANDS:
            add_arguments(sub.add_parser(name), name)
        args = parser.parse_args(argv)
        result = asyncio.run(operate_identity_migration(args))
        print(json.dumps(_safe_summary(result), sort_keys=True))
        return 0
    except (ValueError, TypeError, OSError, PlatformError, DatabaseError, PoolTimeout):
        print('Identity migration command failed; check private configuration, proof and owner target.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
