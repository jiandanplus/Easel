"""Console catalog commands: one connection, current ACL, immutable audit."""
from contextlib import asynccontextmanager
from datetime import datetime
import hashlib
import json
from uuid import uuid4
from psycopg.types.json import Jsonb

from .contracts import OperatorIdentity
from .errors import PlatformError
from .membership import MembershipService
from .billing.service import BillingService
from .execution.core import ExecutionService, validate_definition
from .console_service import provider_registry


class BoundDatabase:
    """Existing business methods commit only with the outer console transaction."""
    def __init__(self, connection):
        self.connection_object = connection

    @asynccontextmanager
    async def connection(self):
        yield self.connection_object


def text(value, maximum):
    if (not isinstance(value, str) or not value.strip() or value != value.strip()
            or len(value) > maximum or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise PlatformError(422, 'invalid_console_command')
    return value


def protected(value):
    if isinstance(value, str) and value.startswith('easel-internal-'):
        raise PlatformError(403, 'console_internal_plan_protected')


class ConsoleCatalog:
    def __init__(self, db, auth, *, provider_ids=()):
        self.db, self.auth = db, auth
        self.providers = provider_registry(provider_ids)

    def _verified_model(self, definition):
        d = validate_definition(definition)
        approved = self.providers.get((d['provider_id'], d['provider_version'], d['id']))
        if (not approved or approved.get('verification') not in {'live', 'fixture'}
                or d['verification'] != approved['verification']
                or d['verification_ref'] != approved.get('verification_ref')
                or d['test_only'] != (approved['verification'] == 'fixture')):
            raise PlatformError(409, 'console_provider_not_preverified')
        return d

    async def _command(self, principal, action, target_type, target_id, payload, reason, key, permissions):
        reason, key = text(reason, 512), text(key, 128)
        target_id = text(target_id, 255)
        try:
            canonical = json.dumps({'action': action, 'target_type': target_type, 'target_id': target_id,
                                    'payload': payload, 'reason': reason}, sort_keys=True, separators=(',', ':'), allow_nan=False)
        except (TypeError, ValueError):
            raise PlatformError(422, 'invalid_console_command') from None
        if len(canonical.encode()) > 16384:
            raise PlatformError(422, 'invalid_console_command')
        request_hash = hashlib.sha256(canonical.encode()).hexdigest()
        key_hash = hashlib.sha256(key.encode()).hexdigest()
        async with self.db.connection() as conn:
            await conn.execute("SET LOCAL statement_timeout='10s'")
            await conn.execute("SET LOCAL lock_timeout='2s'")
            current = principal
            for permission in permissions:
                current = await self.auth.validate(conn, current, permission)
            actor = current.id
            await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                               ('console-command:' + actor + ':' + key_hash,))
            old = await (await conn.execute('SELECT request_hash,result FROM console_operations WHERE actor_id=%s AND key_hash=%s', (actor, key_hash))).fetchone()
            if old:
                if old['request_hash'] != request_hash:
                    raise PlatformError(409, 'console_idempotency_conflict')
                return old['result']
            operator = OperatorIdentity(actor, frozenset(permissions))
            bound = BoundDatabase(conn)
            if action == 'plan.publish':
                result = await MembershipService(bound).publish_plan(operator, payload)
            elif action == 'offer.publish':
                result = await BillingService(bound).publish_offer(operator, payload)
            elif action == 'offer.withdraw':
                row = await (await conn.execute('SELECT plan_id FROM billing_offers WHERE id=%s', (target_id,))).fetchone()
                if row:
                    protected(row['plan_id'])
                result = await BillingService(bound).withdraw_offer(operator, target_id, reason)
            elif action == 'model.publish':
                definition = self._verified_model(payload)
                result = await ExecutionService(bound, None).publish(definition, operator)
                state = await (await conn.execute('SELECT enabled,updated_at FROM execution_model_states WHERE model_id=%s', (target_id,))).fetchone()
                result = {**result, 'enabled': state['enabled'], 'updated_at': state['updated_at'].isoformat()}
            elif action == 'model.state':
                row = await (await conn.execute('SELECT m.definition,s.updated_at FROM execution_models m JOIN execution_model_states s ON s.model_id=m.id WHERE m.id=%s FOR UPDATE OF s', (target_id,))).fetchone()
                if not row:
                    raise PlatformError(404, 'model_not_found')
                if row['updated_at'] != datetime.fromisoformat(payload['expected_updated_at']):
                    raise PlatformError(409, 'console_model_state_conflict')
                if payload['enabled']:
                    self._verified_model(row['definition'])
                result = await ExecutionService(bound, None).set_enabled(target_id, payload['enabled'], operator)
                state = await (await conn.execute('SELECT updated_at FROM execution_model_states WHERE model_id=%s', (target_id,))).fetchone()
                result = {**result, 'id': target_id, 'updated_at': state['updated_at'].isoformat()}
            else:
                raise PlatformError(422, 'invalid_console_command')
            operation_id = uuid4().hex
            await conn.execute('INSERT INTO console_operations(id,actor_id,key_hash,request_hash,action,target_type,target_id,result) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                               (operation_id, actor, key_hash, request_hash, action, target_type, target_id, Jsonb(result)))
            await conn.execute('INSERT INTO console_audit_events(operation_id,actor_id,action,target_type,target_id,reason,key_hash,request_hash) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                               (operation_id, actor, action, target_type, target_id, reason, key_hash, request_hash))
            return result

    async def publish_plan(self, principal, definition, reason, key):
        if not isinstance(definition, dict):
            raise PlatformError(422, 'invalid_console_command')
        protected(definition.get('id'))
        permissions = ('plans:publish', 'billing:publish') if definition.get('test_only') is False else ('plans:publish',)
        return await self._command(principal, 'plan.publish', 'plan', definition.get('id'), definition, reason, key, permissions)

    async def publish_offer(self, principal, definition, reason, key):
        if not isinstance(definition, dict):
            raise PlatformError(422, 'invalid_console_command')
        protected(definition.get('plan_id'))
        return await self._command(principal, 'offer.publish', 'offer', definition.get('id'), definition, reason, key, ('billing:publish',))

    async def withdraw_offer(self, principal, id, reason, key):
        return await self._command(principal, 'offer.withdraw', 'offer', id, {}, reason, key, ('billing:publish',))

    async def publish_model(self, principal, definition, reason, key):
        if not isinstance(definition, dict):
            raise PlatformError(422, 'invalid_console_command')
        return await self._command(principal, 'model.publish', 'model', definition.get('id'), definition, reason, key, ('execution:publish',))

    async def set_model_state(self, principal, id, enabled, reason, key, expected_updated_at):
        try:
            if type(enabled) is not bool or not isinstance(expected_updated_at, str) or len(expected_updated_at) > 128:
                raise ValueError()
            parsed = datetime.fromisoformat(expected_updated_at)
            if parsed.tzinfo is None:
                raise ValueError()
        except (TypeError, ValueError):
            raise PlatformError(422, 'invalid_console_command') from None
        return await self._command(principal, 'model.state', 'model', id,
                                   {'enabled': enabled, 'expected_updated_at': expected_updated_at},
                                   reason, key, ('execution:publish',))
