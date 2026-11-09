"""Console catalog commands: one connection, current ACL, immutable audit."""
from contextlib import asynccontextmanager
from datetime import datetime
import hashlib
import json
import re
from uuid import uuid4
import psycopg
from psycopg.types.json import Jsonb

from .contracts import OperatorIdentity
from .errors import PlatformError
from .membership import MembershipService
from .billing.service import BillingService
from .execution.core import ExecutionService, validate_definition
from .console_service import provider_registry
from .redemptions import code_hash, new_code
from .execution.rates import cost as cost_text, normalize as normalize_text_rate
from .media.contracts import MAX_BUDGET as MEDIA_BUDGET, validate_definition as validate_media_definition
from .media.rates import cost as cost_media, normalize as normalize_media_rate
from .model_templates import APIMART_SIZES, MEDIA, OPENAI_IMAGE_SIZES, TEXT, build_rate
from .provider_channels import CAPABILITY_CHANNEL, CHANNELS, PLATFORMS, clean_base_url, clean_key, clean_name, clean_options, provider_id as channel_provider_id, seal_scope, spec_of
from .provider_secrets import seal, valid_public_key


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


ALLOWANCE_ERRORS = {
    'EA403': (403, 'console_permission_denied'),
    'EA404': (404, 'billing_account_not_found'),
    'EA409': (409, 'owner_grant_active'),
    'EA410': (409, 'wallet_capacity_exceeded'),
    'EA422': (422, 'invalid_console_command'),
}


PLAN_ERRORS = {
    **ALLOWANCE_ERRORS,
    'EA405': (404, 'plan_not_found'),
    'EA406': (403, 'console_internal_plan_protected'),
    'EA407': (409, 'plan_above_console_limit'),
    'EA409': (409, 'plan_grant_active'),
}
ADMIN_ERRORS = {'EA403': (403, 'console_permission_denied'), 'EA404': (404, 'console_request_invalid'),
                'EA409': (409, 'console_acl_conflict')}
USER_ERRORS = {'EA403': (403, 'console_permission_denied'), 'EA404': (404, 'console_user_not_found')}


async def guarded(conn, statement, params, errors):
    """Call one Console SECURITY DEFINER function and map its own error codes."""
    try:
        async with conn.transaction():
            row = await (await conn.execute(statement, params)).fetchone()
    except psycopg.Error as error:
        failure = errors.get(getattr(error, 'sqlstate', None))
        if failure is None:
            raise
        raise PlatformError(*failure) from None
    return row['result']


def tags(values):
    if (not isinstance(values, list) or len(values) > 10
            or any(not isinstance(v, str) or not v.strip() or v != v.strip() or len(v) > 20
                   or any(ord(c) < 32 or ord(c) == 127 or c == ',' for c in v) for v in values)
            or len(set(values)) != len(values)):
        raise PlatformError(422, 'invalid_console_command')
    return values


class ConsoleCatalog:
    def __init__(self, db, auth, *, provider_ids=(), public_key=None):
        self.db, self.auth = db, auth
        self.public_key = public_key if public_key and valid_public_key(public_key) else None
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

    async def _command(self, principal, action, target_type, target_id, payload, reason, key, permissions, secret=None, sealed=None):
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
                for model in payload.get('models') or ():
                    if not await (await conn.execute('SELECT 1 FROM execution_models WHERE id=%s UNION ALL SELECT 1 FROM media_models WHERE id=%s', (model, model))).fetchone():
                        raise PlatformError(422, 'console_plan_model_unknown')
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
                    managed = await (await conn.execute("SELECT 1 FROM managed_model_bindings WHERE kind='execution' AND model_id=%s", (target_id,))).fetchone()
                    if not managed:
                        self._verified_model(row['definition'])
                result = await ExecutionService(bound, None).set_enabled(target_id, payload['enabled'], operator)
                state = await (await conn.execute('SELECT updated_at FROM execution_model_states WHERE model_id=%s', (target_id,))).fetchone()
                result = {**result, 'id': target_id, 'updated_at': state['updated_at'].isoformat()}
            elif action == 'allowance.issue':
                result = await guarded(conn, 'SELECT console_issue_owner_allowance(%s,%s,%s,%s) AS result',
                                       (actor, target_id, key_hash, reason), ALLOWANCE_ERRORS)
            elif action == 'plan.issue':
                result = await guarded(conn, 'SELECT console_issue_plan(%s,%s,%s,%s,%s) AS result',
                                       (actor, target_id, payload['plan_id'], key_hash, reason), PLAN_ERRORS)
            elif action == 'user.status':
                result = await guarded(conn, 'SELECT console_set_user_active(%s,%s,%s) AS result',
                                       (actor, target_id, payload['active']), USER_ERRORS)
            elif action == 'user.logout':
                result = await guarded(conn, 'SELECT console_force_logout(%s,%s) AS result', (actor, target_id), USER_ERRORS)
            elif action == 'redemption.create':
                result = await guarded(conn, 'SELECT console_create_redemption_batch(%s,%s,%s,%s,%s,%s) AS result',
                    (actor, payload['plan_id'], payload['name'], reason, payload['days'], [code_hash(c) for c in secret]), PLAN_ERRORS)
            elif action == 'redemption.disable':
                result = await guarded(conn, 'SELECT console_disable_redemption_batch(%s,%s) AS result', (actor, target_id), USER_ERRORS)
            elif action == 'admin.approve':
                result = await guarded(conn, 'SELECT console_approve_request(%s,%s,%s,%s) AS result',
                    (actor, target_id, payload['role'], self.auth.settings.issuer), ADMIN_ERRORS)
            elif action == 'admin.disable':
                result = await guarded(conn, 'SELECT console_disable_admin(%s,%s) AS result', (actor, target_id), ADMIN_ERRORS)
            elif action == 'model.managed':
                result = await self._create_managed(conn, actor, target_id, payload)
            elif action == 'model.price':
                result = await self._set_price(conn, actor, target_id, payload, reason)
            elif action == 'model.import':
                result = await self._import_models(conn, actor, operator, bound, payload)
            elif action == 'model.trial':
                result = await self._set_trial(conn, actor, target_id, payload)
            elif action == 'media.state':
                result = await self._set_media_state(conn, actor, target_id, payload)
            elif action == 'provider.config':
                result = await self._save_provider(conn, actor, target_id, payload, sealed)
            elif action == 'user.note':
                if not await (await conn.execute('SELECT 1 FROM users WHERE id=%s', (target_id,))).fetchone():
                    raise PlatformError(404, 'console_user_not_found')
                row = await (await conn.execute("""INSERT INTO console_user_notes(user_id,note,tags,updated_by)
                    VALUES (%s,%s,%s,%s) ON CONFLICT (user_id) DO UPDATE SET note=EXCLUDED.note,tags=EXCLUDED.tags,
                    updated_by=EXCLUDED.updated_by,updated_at=clock_timestamp() RETURNING note,tags,updated_at""",
                    (target_id, payload['note'], payload['tags'], actor))).fetchone()
                result = {'id': target_id, 'note': row['note'], 'tags': row['tags'], 'updated_at': row['updated_at'].isoformat()}
            else:
                raise PlatformError(422, 'invalid_console_command')
            operation_id = uuid4().hex
            await conn.execute('INSERT INTO console_operations(id,actor_id,key_hash,request_hash,action,target_type,target_id,result) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                               (operation_id, actor, key_hash, request_hash, action, target_type, target_id, Jsonb(result)))
            await conn.execute('INSERT INTO console_audit_events(operation_id,actor_id,action,target_type,target_id,reason,key_hash,request_hash) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                               (operation_id, actor, action, target_type, target_id, reason, key_hash, request_hash))
            # Plain redemption codes are returned once and never stored, not even in the replay record.
            return {**result, 'codes': secret} if secret is not None else result

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

    async def _provider(self, conn, kind, provider_id):
        row = await (await conn.execute('SELECT * FROM provider_catalog WHERE kind=%s AND provider_id=%s', (kind, provider_id))).fetchone()
        if not row:
            raise PlatformError(404, 'console_provider_unknown')
        return row

    async def _create_managed(self, conn, actor, model, payload):
        capability = payload['capability']
        version = 'console-' + uuid4().hex[:12]
        rate = build_rate(capability, version, payload['prices'])
        common = {'id': model, 'version': version, 'name': payload['name'], 'capability': capability, 'products': ['easel'],
                  'test_only': False, 'verification': 'live', 'verification_ref': 'console-managed:' + actor, 'rate': rate}
        if capability == 'text':
            kind = 'execution'
            provider = await self._provider(conn, kind, payload['provider_id'])
            definition = validate_definition({**common, 'feature': 'text', 'provider_id': provider['provider_id'],
                                              'provider_version': provider['provider_version'], 'limits': dict(TEXT['limits'])})
            await conn.execute('INSERT INTO execution_models(id,definition,operator_id) VALUES (%s,%s,%s)', (model, Jsonb(definition), actor))
            await conn.execute('INSERT INTO execution_model_states(model_id,operator_id) VALUES (%s,%s)', (model, actor))
        else:
            template = MEDIA.get(capability)
            if template is None:
                raise PlatformError(422, 'invalid_console_command')
            kind = 'media'
            provider = await self._provider(conn, kind, payload['provider_id'])
            if capability not in provider['capabilities']:
                raise PlatformError(422, 'console_provider_capability')
            limits, parameters = template['limits'], template['parameters']
            if capability == 'voice.enroll':  # Must name the same model that will speak with this voice.
                parameters = {**parameters, 'properties': {**parameters['properties'], 'target_model': {'type': 'string', 'enum': [payload['upstream_model']]}}}
            if provider['adapter'] == 'fish_speech_v1' and capability == 'audio.speech':  # Fish takes a reference recording in the request.
                limits = {**limits, 'max_input_bytes': 10485760, 'max_assets': 1, 'input_media': {'mime_types': ['audio/mpeg', 'audio/wav', 'audio/mp4'], 'max_files': 1, 'max_duration_ms': 300000, 'max_channels': 2, 'max_sample_rate': 48000, 'require_audio': True}}
                parameters = {**parameters, 'required': ['response_format'], 'properties': {**parameters['properties'], 'reference_text': {'type': 'string', 'maxLength': 2000}}}
            if provider['adapter'] == 'apimart_image_v1':  # A task whose result is fetched and checked like video.
                limits = {**limits, 'output_media': {'mime_types': ['image/png', 'image/jpeg', 'image/webp'], 'max_files': 1, 'max_pixels': 16777216}}
                parameters = {**parameters, 'properties': {**parameters['properties'], 'size': {'type': 'string', 'enum': list(APIMART_SIZES)}}}
            if capability in ('image.generate', 'image.edit') and provider['provider_id'].startswith('console-image-openai'):
                parameters = {**parameters, 'properties': {**parameters['properties'], 'size': {'type': 'string', 'enum': OPENAI_IMAGE_SIZES}}}
            if provider['adapter'] == 'gemini_speech_v1':  # Gemini only returns raw audio, delivered as WAV.
                limits = {**limits, 'output_media': {**limits['output_media'], 'mime_types': ['audio/wav']}}
                parameters = {**parameters, 'properties': {**parameters['properties'], 'response_format': {'type': 'string', 'enum': ['wav']}}}
            definition = validate_media_definition({**common, 'feature': template['feature'], 'provider_id': provider['provider_id'],
                'provider_version': provider['provider_version'], 'adapter': provider['adapter'], 'protocol_version': provider['protocol_version'],
                'limits': {**limits, 'max_units': {template['unit']: template['max_units']}}, 'parameters': parameters})
            await conn.execute('INSERT INTO media_models(id,definition,operator_id) VALUES (%s,%s,%s)', (model, Jsonb(definition), actor))
            await conn.execute('INSERT INTO media_model_states(model_id,operator_id) VALUES (%s,%s)', (model, actor))
        await conn.execute('INSERT INTO managed_model_bindings(kind,model_id,provider_id,upstream_model,operator_id) VALUES (%s,%s,%s,%s,%s)',
                           (kind, model, payload['provider_id'], payload['upstream_model'], actor))
        return {'id': model, 'kind': kind, 'enabled': False}

    @staticmethod
    async def _model_taken(conn, model):
        return bool(await (await conn.execute('SELECT 1 FROM execution_models WHERE id=%s UNION ALL SELECT 1 FROM media_models WHERE id=%s', (model, model))).fetchone())

    async def _connection_label(self, conn, capability, provider):
        """What the admin called this connection, for telling two same-named models apart."""
        channel = CAPABILITY_CHANNEL.get(capability)
        for row in await self._current_connections(conn, channel) if channel else ():
            if provider.removesuffix('-clone') == channel_provider_id(channel, row['platform'], row['account']):
                return row['name'] or PLATFORMS[channel][row['platform']]['label']
        return provider

    async def _import_models(self, conn, actor, operator, bound, payload):
        kind = 'execution' if payload['capability'] == 'text' else 'media'
        added, skipped = [], []
        for upstream in payload['upstream_models']:
            if await (await conn.execute('SELECT 1 FROM managed_model_bindings WHERE kind=%s AND provider_id=%s AND upstream_model=%s', (kind, payload['provider_id'], upstream))).fetchone():
                skipped.append(upstream)
                continue
            model = re.sub(r'[^A-Za-z0-9_.:-]', '-', upstream).lstrip('-_.:')[:128]
            if not model:
                raise PlatformError(422, 'invalid_console_command')
            name = upstream
            if await self._model_taken(conn, model):
                name = (upstream + '（' + await self._connection_label(conn, payload['capability'], payload['provider_id']) + '）')[:128]
                model = (re.sub(r'[^A-Za-z0-9_.:-]', '-', payload['provider_id']) + ':' + model)[:128]
                if await self._model_taken(conn, model):
                    skipped.append(upstream)
                    continue
            await self._create_managed(conn, actor, model, {'name': name, 'capability': payload['capability'], 'provider_id': payload['provider_id'], 'upstream_model': upstream, 'prices': payload['prices']})
            if payload['enabled'] and kind == 'execution':
                await ExecutionService(bound, None).set_enabled(model, True, operator)
            elif payload['enabled']:
                await self._set_media_state(conn, actor, model, {'enabled': True})
            added.append(model)
        return {'added': added, 'skipped': skipped, 'enabled': payload['enabled']}

    async def _model(self, conn, kind, model):
        table = 'execution_models' if kind == 'execution' else 'media_models'
        row = await (await conn.execute('SELECT definition FROM ' + table + ' WHERE id=%s', (model,))).fetchone()
        if not row:
            raise PlatformError(404, 'model_not_found')
        return row['definition']

    async def _set_price(self, conn, actor, model, payload, reason):
        definition = await self._model(conn, payload['kind'], model)
        rate = build_rate(definition['capability'], 'price-' + uuid4().hex[:12], payload['prices'])
        if payload['kind'] == 'execution':
            rate = normalize_text_rate(rate)
            cost_text(rate, definition['limits']['max_input_tokens'], definition['limits']['max_output_tokens'])
        else:
            rate = normalize_media_rate(rate)
            if {m['unit'] for m in rate['meters']} != {m['unit'] for m in definition['rate']['meters']}:
                raise PlatformError(422, 'invalid_console_command')
            if not 0 < cost_media(rate, definition['limits']['max_units']) <= MEDIA_BUDGET:
                raise PlatformError(422, 'invalid_console_command')
        await conn.execute('INSERT INTO model_prices(id,kind,model_id,rate,reason,operator_id) VALUES (%s,%s,%s,%s,%s,%s)',
                           (uuid4().hex, payload['kind'], model, Jsonb(rate), reason, actor))
        return {'id': model, 'kind': payload['kind'], 'rate': rate}

    async def _set_trial(self, conn, actor, model, payload):
        await self._model(conn, payload['kind'], model)
        needle = payload['user']
        rows = await (await conn.execute("""SELECT DISTINCT b.id FROM billing_accounts b JOIN tenants t ON t.id=b.tenant_id
            LEFT JOIN external_identities e ON e.user_id=t.owner_user_id
            WHERE t.owner_user_id=%s OR b.id=%s OR lower(e.username)=lower(%s) OR lower(e.email)=lower(%s) LIMIT 2""", (needle, needle, needle, needle))).fetchall()
        if len(rows) != 1:
            raise PlatformError(404, 'console_trial_user_not_found')
        wallet = rows[0]['id']
        await conn.execute("""INSERT INTO model_trial_access(kind,model_id,billing_account_id,operator_id,active) VALUES (%s,%s,%s,%s,%s)
            ON CONFLICT (kind,model_id,billing_account_id) DO UPDATE SET active=EXCLUDED.active,operator_id=EXCLUDED.operator_id""",
            (payload['kind'], model, wallet, actor, payload['active']))
        return {'id': model, 'wallet': wallet, 'active': payload['active']}

    async def _set_media_state(self, conn, actor, model, payload):
        row = await (await conn.execute('UPDATE media_model_states SET enabled=%s,operator_id=%s,updated_at=clock_timestamp() WHERE model_id=%s RETURNING enabled,updated_at',
                                        (payload['enabled'], actor, model))).fetchone()
        if not row:
            raise PlatformError(404, 'model_not_found')
        return {'id': model, 'enabled': row['enabled'], 'updated_at': row['updated_at'].isoformat()}

    @staticmethod
    async def _running_calls(conn, channel):
        spec = CHANNELS[channel]
        table, models = ('execution_jobs', 'execution_models') if spec['kind'] == 'execution' else ('media_jobs', 'media_models')
        capabilities = [capability for capability, name in CAPABILITY_CHANNEL.items() if name == channel]
        row = await (await conn.execute('SELECT count(*) AS total FROM ' + table + ' j JOIN ' + models + " m ON m.id=j.model WHERE j.status IN ('reserved','dispatched') AND m.definition->>'capability'=ANY(%s)", (capabilities,))).fetchone()
        return row['total']

    async def _models_in_use(self, conn, channel, platform, account):
        kind = CHANNELS[channel]['kind']
        states = 'execution_model_states' if kind == 'execution' else 'media_model_states'
        own = channel_provider_id(channel, platform, account)
        row = await (await conn.execute('SELECT count(*) AS total FROM managed_model_bindings b JOIN ' + states + ' s ON s.model_id=b.model_id WHERE b.kind=%s AND b.provider_id=ANY(%s) AND s.enabled',
                                        (kind, [own, own + '-clone']))).fetchone()
        return row['total']

    @staticmethod
    async def _current_connections(conn, channel):
        return await (await conn.execute(
            """SELECT DISTINCT ON (platform,account) platform,account,name,sealed_secret FROM provider_configs WHERE channel=%s
            ORDER BY platform,account,created_at DESC,id DESC""", (channel,))).fetchall()

    async def _save_provider(self, conn, actor, target, payload, sealed):
        channel, platform, account = payload['channel'], payload['platform'], payload['account']
        # One lock for the page: names have to stay unique across its connections.
        await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('provider-config:' + channel,))
        previous = await (await conn.execute('SELECT base_url,sealed_secret,key_hint,name FROM provider_configs WHERE channel=%s AND platform=%s AND account=%s ORDER BY created_at DESC,id DESC LIMIT 1', (channel, platform, account))).fetchone()
        had_key = bool(previous and previous['sealed_secret'])
        if payload['creating'] and previous:
            raise PlatformError(409, 'console_idempotency_conflict')
        if account and not payload['creating'] and not had_key:
            raise PlatformError(404, 'console_provider_not_found')
        if payload['clear']:
            secret, hint = None, None
            if had_key and await self._models_in_use(conn, channel, platform, account):
                raise PlatformError(409, 'console_provider_in_use')
        elif sealed:
            secret, hint = sealed
        elif had_key and previous['base_url'] == payload['base_url']:
            secret, hint = previous['sealed_secret'], previous['key_hint']
        else:
            # A sealed key is bound to its address, so moving it needs the key again.
            raise PlatformError(409, 'console_provider_key_required')
        name = payload['name'] or (previous['name'] if previous else '')
        if name and secret is not None and any(
                row['sealed_secret'] is not None and row['name'].lower() == name.lower() and (row['platform'], row['account']) != (platform, account)
                for row in await self._current_connections(conn, channel)):
            raise PlatformError(409, 'console_provider_name_taken')
        connection_changed = not had_key or secret != previous['sealed_secret'] or previous['base_url'] != payload['base_url']
        if had_key and connection_changed and await self._running_calls(conn, channel):
            raise PlatformError(409, 'console_provider_busy')
        config_id = uuid4().hex
        await conn.execute('INSERT INTO provider_configs(id,channel,platform,account,name,base_url,sealed_secret,key_hint,options,operator_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                           (config_id, channel, platform, account, name, payload['base_url'], secret, hint, Jsonb(payload['options']), actor))
        return {'channel': channel, 'platform': platform, 'account': account, 'provider_id': channel_provider_id(channel, platform, account), 'id': config_id, 'configured': secret is not None}

    async def save_provider_config(self, principal, channel, platform, base_url, key, clear, options, reason, op_key, *, account='', name=''):
        """account None adds a new connection; '' is the platform's original one."""
        if channel not in CHANNELS or platform not in PLATFORMS[channel] or type(clear) is not bool or (clear and key is not None):
            raise PlatformError(422, 'console_provider_invalid')
        creating, name = account is None, clean_name(name)
        if creating:
            if clear or not name:
                raise PlatformError(422, 'console_provider_invalid')
            # Derived from the request key, so a retried submission lands on the same connection.
            account = hashlib.sha256(str(op_key).encode()).hexdigest()[:8]
        if not isinstance(account, str) or not re.fullmatch(r'(?:[a-f0-9]{8})?', account):
            raise PlatformError(422, 'console_provider_invalid')
        base_url = clean_base_url(base_url)
        payload = {'channel': channel, 'platform': platform, 'account': account, 'creating': creating, 'name': name, 'base_url': base_url, 'options': clean_options(channel, platform, options), 'clear': clear, 'key_fingerprint': None}
        sealed = None
        if key is not None:
            key = clean_key(key, parts=2 if spec_of(channel, platform).get('second_key') else 1)
            if self.public_key is None:
                raise PlatformError(503, 'console_provider_key_unconfigured')
            # Retries must match without keeping the key: the sealed text differs every time.
            payload['key_fingerprint'] = hashlib.sha256(key.encode()).hexdigest()
            sealed = (seal(self.public_key, seal_scope(channel, platform, account), base_url, key), key[-4:])
        return await self._command(principal, 'provider.config', 'provider', channel + '/' + platform + ('/' + account if account else ''), payload, reason, op_key, ('providers:manage',), sealed=sealed)

    async def create_managed_model(self, principal, model, name, capability, provider_id, upstream_model, prices, reason, key):
        payload = {'name': text(name, 128), 'capability': text(capability, 64), 'provider_id': text(provider_id, 64),
                   'upstream_model': text(upstream_model, 128), 'prices': prices}
        if not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,128}', model or ''):
            raise PlatformError(422, 'invalid_console_command')
        return await self._command(principal, 'model.managed', 'model', model, payload, reason, key, ('execution:publish',))

    async def import_models(self, principal, capability, provider_id, upstream_models, prices, enabled, reason, key):
        if (not isinstance(upstream_models, list) or not 1 <= len(upstream_models) <= 100 or len(set(map(str, upstream_models))) != len(upstream_models)
                or type(enabled) is not bool):
            raise PlatformError(422, 'invalid_console_command')
        payload = {'capability': text(capability, 64), 'provider_id': text(provider_id, 64), 'upstream_models': [text(name, 128) for name in upstream_models], 'prices': prices, 'enabled': enabled}
        if any(any(ord(c) < 33 for c in name) for name in upstream_models):
            raise PlatformError(422, 'invalid_console_command')
        return await self._command(principal, 'model.import', 'model', 'import:' + provider_id, payload, reason, key, ('execution:publish',))

    async def set_model_price(self, principal, kind, model, prices, reason, key):
        if kind not in ('execution', 'media'):
            raise PlatformError(422, 'invalid_console_command')
        return await self._command(principal, 'model.price', 'model', model, {'kind': kind, 'prices': prices}, reason, key, ('execution:publish',))

    async def set_model_trial(self, principal, kind, model, user, active, reason, key):
        if kind not in ('execution', 'media') or type(active) is not bool:
            raise PlatformError(422, 'invalid_console_command')
        return await self._command(principal, 'model.trial', 'model', model, {'kind': kind, 'user': text(user, 320), 'active': active}, reason, key, ('execution:publish',))

    async def set_media_state(self, principal, model, enabled, reason, key):
        if type(enabled) is not bool:
            raise PlatformError(422, 'invalid_console_command')
        return await self._command(principal, 'media.state', 'model', model, {'enabled': enabled}, reason, key, ('execution:publish',))

    async def issue_plan(self, principal, wallet, plan_id, reason, key):
        if isinstance(reason, str) and len(reason) > 256:
            raise PlatformError(422, 'invalid_console_command')
        return await self._command(principal, 'plan.issue', 'wallet', wallet, {'plan_id': text(plan_id, 255)},
                                   reason, key, ('plans:issue',))

    async def set_user_active(self, principal, user, active, reason, key):
        if type(active) is not bool:
            raise PlatformError(422, 'invalid_console_command')
        return await self._command(principal, 'user.status', 'user', user, {'active': active}, reason, key, ('users:manage',))

    async def force_logout(self, principal, user, reason, key):
        return await self._command(principal, 'user.logout', 'user', user, {}, reason, key, ('users:manage',))

    async def set_user_note(self, principal, user, note, tag_list, reason, key):
        if not isinstance(note, str) or len(note) > 500 or any(ord(c) < 32 and c not in '\n' or ord(c) == 127 for c in note):
            raise PlatformError(422, 'invalid_console_command')
        return await self._command(principal, 'user.note', 'user', user, {'note': note, 'tags': tags(tag_list)},
                                   reason, key, ('users:manage',))

    async def create_redemptions(self, principal, plan_id, name, count, days, reason, key):
        if (type(count) is not int or not 1 <= count <= 100 or type(days) is not int or not 1 <= days <= 365
                or (isinstance(reason, str) and len(reason) > 256)):
            raise PlatformError(422, 'invalid_console_command')
        payload = {'plan_id': text(plan_id, 255), 'name': text(name, 60), 'count': count, 'days': days}
        return await self._command(principal, 'redemption.create', 'redemption', 'new', payload, reason, key,
                                   ('redemption:manage',), secret=[new_code() for _ in range(count)])

    async def disable_redemptions(self, principal, batch, reason, key):
        return await self._command(principal, 'redemption.disable', 'redemption', batch, {}, reason, key, ('redemption:manage',))

    async def approve_admin(self, principal, request_id, role, reason, key):
        if role not in ('operator', 'viewer'):
            raise PlatformError(422, 'invalid_console_command')
        return await self._command(principal, 'admin.approve', 'admin', request_id, {'role': role}, reason, key, ('admins:manage',))

    async def disable_admin(self, principal, admin_id, reason, key):
        return await self._command(principal, 'admin.disable', 'admin', admin_id, {}, reason, key, ('admins:manage',))

    async def issue_allowance(self, principal, wallet, reason, key):
        if isinstance(reason, str) and len(reason) > 256:
            raise PlatformError(422, 'invalid_console_command')
        return await self._command(principal, 'allowance.issue', 'wallet', wallet, {}, reason, key, ('owner:allowance',))

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
