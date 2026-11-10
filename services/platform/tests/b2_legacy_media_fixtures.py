"""Opt-in legacy tests using owned PostgreSQL and real maintenance capabilities.

This module never installs control in conftest's shared database and never
converts an absent capability into authority on a production service.
"""
from contextlib import asynccontextmanager
import os
import asyncio
import secrets
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import pytest
import pytest_asyncio

from b2_owned_pg import _owned_pg
from b2_owned_media import DB
from platform_service.db import Database
from platform_service.media.core import MediaService
from platform_service.media_maintenance.contracts import require
from platform_service.media_maintenance.filesystem import ControlLock, SourceRoot
from platform_service.media_maintenance.runtime import (
    GuardedDatabase, RuntimeGeneration, current_permit, operation, physical_call,
)
from platform_service.media_maintenance.schema import SchemaProfile, install_control_schema


class LegacyMediaClient:
    """Explicit allowlist adapter; the wrapped service still enforces each guard."""
    _JOB_METHODS = frozenset({
        'dispatch', 'heartbeat', 'record_provider_attempt',
        'record_provider_association', 'record_protocol_facts',
        'record_http_response_reference', 'record_terminal_evidence',
        'pending', 'finish', 'finish_from_evidence', 'register_artifact',
        'register_voice',
    })
    _SCAN_METHODS = frozenset({'prepare_gc', 'claim_gc', 'recover_financial'})

    def __init__(self, service, generation):
        require(type(service) is MediaService and type(generation) is RuntimeGeneration)
        self.raw, self.generation = service, generation
        self.raw.maintenance = generation
        if not hasattr(service, '_legacy_upload_permits'):
            service._legacy_upload_permits = {}
        self._upload_permits = service._legacy_upload_permits

    def __getattr__(self, name):
        original = getattr(self.raw, name)
        if name in self._JOB_METHODS:
            async def job_call(job_id, attempt_id, *args, **kwargs):
                permit = self.job_permit(job_id)
                with operation(permit):
                    return await original(job_id, attempt_id, *args, **kwargs)
            return job_call
        if name in self._SCAN_METHODS:
            async def scan_call(**kwargs):
                async with self.generation.use('maintenance', str(uuid4()), transient=True):
                    return await original(**kwargs)
            return scan_call
        return original

    def job_permit(self, job_id):
        found = [p for p in self.generation.permits
                 if p.binding.kind == 'job' and p.binding.subject_id == job_id]
        require(len(found) == 1)
        return self.generation.owned('job', job_id, found[0].binding.attempt_id)

    def upload_permit(self, storage_key):
        return current_permit(self._upload_permits[storage_key], kinds={'upload'})

    async def publish(self, definition, operator):
        async with self.generation.use('catalog', definition['id'], transient=True):
            return await self.raw.publish(definition, operator)

    async def set_enabled(self, model, enabled, operator):
        async with self.generation.use('catalog', model, transient=True):
            return await self.raw.set_enabled(model, enabled, operator)

    async def mint(self, scope, **values):
        invocation = await self.raw.invocation_identity(scope, values['invocation_key']) or str(uuid4())
        async with self.generation.use('mint', invocation, transient=True):
            return await self.raw.mint(scope, **values)

    async def admit(self, context, **values):
        # Retain the original accepted operation until the legacy assertions end.
        # Only a never-used/replay capability is closed by real SQL stability proof.
        permit = await self.generation.acquire('job', str(uuid4()), str(uuid4()))
        try:
            with operation(permit):
                job = await self.raw.admit(context, **values)
        except BaseException:
            await self.generation.finish_job_if_stable(permit)
            raise
        if job['id'] != permit.binding.subject_id:
            await self.generation.finish_job_if_stable(permit)
        return job

    async def cancel(self, scope, job_id):
        with operation(self.job_permit(job_id)):
            return await self.raw.cancel(scope, job_id)

    async def register_asset(self, context, **metadata):
        permit = self._upload_permits.get(metadata['storage_key'])
        if permit is None or permit._closed:
            permit = await self.generation.acquire('upload', context['invocation_id'])
            self._upload_permits[metadata['storage_key']] = permit
        with operation(permit):
            result = await self.raw.register_asset(context, **metadata)
        await self.complete_uploads(context['invocation_id'])
        return result

    async def complete_uploads(self, invocation_id):
        # Check exact keys owned by each original nonce, then let SQL verify the
        # stable-business proof independently. Unrelated requests never adopt it.
        for permit in tuple(self.generation.permits):
            if permit.binding.kind != 'upload' or permit.binding.subject_id != invocation_id or not permit._valid:
                continue
            keys = [key for key, original in self._upload_permits.items() if original is permit]
            async with self.raw.db.connection() as connection:
                row = await (await connection.execute("SELECT EXISTS(SELECT 1 FROM media_blobs b WHERE storage_key=ANY(%s) AND (state NOT IN ('attached','deleted') OR (state='attached' AND NOT EXISTS(SELECT 1 FROM media_assets a WHERE a.id=b.attached_id AND a.storage_key=b.storage_key)))) pending", (keys,))).fetchone()
            if not row['pending']:
                await self.generation.finish(permit, {'uploaded': invocation_id})

    async def stage_blob(self, context, metadata, *, kind='asset', job_id=None, attempt_id=None):
        if kind == 'asset':
            permit = self._upload_permits.get(metadata['storage_key'])
            if permit is None or permit._closed:
                permit = await self.generation.acquire('upload', context['invocation_id'])
                self._upload_permits[metadata['storage_key']] = permit
            try:
                with operation(permit):
                    return await self.raw.stage_blob(context, metadata, kind=kind,
                                                     job_id=job_id, attempt_id=attempt_id)
            except Exception:
                # A rejected stage with no owned intent must be provably empty;
                # source SQL remains the authority on finishing this capability.
                async with self.raw.db.connection() as connection:
                    row=await(await connection.execute('SELECT 1 FROM media_blobs WHERE storage_key=%s',(metadata['storage_key'],))).fetchone()
                if row is None:
                    await self.generation.finish(permit, {'stage_rejected': metadata['storage_key']})
                raise
        with operation(self.job_permit(job_id)):
            return await self.raw.stage_blob(context, metadata, kind=kind,
                                             job_id=job_id, attempt_id=attempt_id)

    async def _release_storage_budget(self, connection, job_id):
        with operation(self.job_permit(job_id)):
            return await self.raw._release_storage_budget(connection, job_id)


class LegacyAsyncClient:
    _SCANS = frozenset({'claim', 'expire', 'recover_associations'})
    _CLAIMS = frozenset({'heartbeat', 'defer', 'observe', 'stage_source',
                         'attach_source', 'prepare_settlement', 'done', 'sources',
                         'job', 'observation'})

    def __init__(self, queue, media):
        self.raw, self.media = queue, media
        self.service = media.raw

    def __getattr__(self, name):
        original = getattr(self.raw, name)
        if name in self._SCANS:
            async def scan(**kwargs):
                async with self.media.generation.use('maintenance', str(uuid4()), transient=True):
                    return await original(**kwargs)
            return scan
        if name in self._CLAIMS:
            async def continuation(claim, *args, **kwargs):
                permit = self.media.job_permit(claim['job_id'])
                with operation(permit):
                    return await original(claim, *args, **kwargs)
            return continuation
        if name in {'enqueue', 'seed'}:
            async def job(job_id, *args, **kwargs):
                with operation(self.media.job_permit(job_id)):
                    return await original(job_id, *args, **kwargs)
            return job
        return original


class LegacyVoiceStages:
    def __init__(self, stages, media):
        self.raw, self.media = stages, media

    def __getattr__(self, name):
        original = getattr(self.raw, name)
        if name in {'begin', 'register_ready'}:
            async def job(job_id, *args, **kwargs):
                with operation(self.media.job_permit(job_id)):
                    return await original(job_id, *args, **kwargs)
            return job
        if name in {'observe', 'record_headers'}:
            async def step(step_id, *args, **kwargs):
                async with self.raw.db.connection() as connection:
                    row = await (await connection.execute(
                        'SELECT job_id FROM media_voice_steps WHERE id=%s', (step_id,))).fetchone()
                require(row is not None)
                with operation(self.media.job_permit(row['job_id'])):
                    return await original(step_id, *args, **kwargs)
            return step
        return original


class LegacyMediaEnvironment:
    def __init__(self, env, database, runtime_db, generation, locks, source):
        self.env, self.db, self.runtime_db = env, database, runtime_db
        self.generation, self.locks, self.source = generation, locks, source
        self.created_roles = set()
        self.runtimes = []

    async def setup(self, database, **values):
        require(database is self.db)
        from test_media_credits import setup
        result = await setup(database, maintenance=self.generation, **values)
        service, *remaining = result
        return (self.client(service), *remaining)

    def client(self, service):
        return LegacyMediaClient(service, self.generation)

    async def restart_generation(self, database):
        require(not self.generation._started and not self.generation.permits)
        self.generation = RuntimeGeneration(database, self.locks, source=self.source)
        await self.generation.start()
        return self.generation

    async def start_runtime(self, runtime):
        require(runtime.maintenance is self.generation)
        self.source.verify_path(runtime.settings.storage_root)
        await runtime.start()
        self.runtimes.append(runtime)
        return runtime

    def retain_runtime(self, runtime):
        # Same live worker/generation; this is facade recreation, not process loss.
        require(runtime.maintenance is self.generation)
        if runtime not in self.runtimes:
            self.runtimes.append(runtime)

    def queue(self, media):
        from platform_service.media.async_store import MediaAsyncStore
        return LegacyAsyncClient(MediaAsyncStore(media.raw), media)

    def stages(self, media):
        from platform_service.media.voices import VoiceStages
        return LegacyVoiceStages(VoiceStages(media.db, media.identity, media.raw), media)

    def voices(self, media):
        from platform_service.media.voices import OwnedVoices
        return LegacyVoiceStages(OwnedVoices(media.db, media.identity, media.raw), media)

    async def decode(self, function, *args, **options):
        if not hasattr(self, '_decoder_context'):
            _, _, _, self._decoder_context, *_ = await self.setup(self.db)
        invocation = self._decoder_context['invocation_id']
        async with self.generation.use('upload', invocation, transient=True) as permit:
            return await physical_call(function, *args, permit=permit, **options)

    async def due_work(self):
        async with self.db.connection() as connection:
            rows = await (await connection.execute('SELECT job_id,attempt_id FROM media_work')).fetchall()
        for row in rows:
            permit = self.generation.owned('job', row['job_id'], row['attempt_id'])
            async with self.sql(permit) as connection:
                await connection.execute("UPDATE media_work SET due_at=clock_timestamp()-interval '1 second' WHERE job_id=%s", (row['job_id'],))

    async def run_worker(self, worker):
        async with self.generation.use('maintenance', str(uuid4()), transient=True):
            return await worker.run_once()

    @property
    def source_path(self):
        return self._source_path

    @asynccontextmanager
    async def sql(self, permit):
        # SQL fixture mutations are individually scoped, never autouse-authorized.
        with operation(permit):
            async with GuardedDatabase(self.db).connection() as connection:
                yield connection

    @asynccontextmanager
    async def policy(self, subject='global'):
        async with self.generation.use('policy', subject, transient=True) as permit:
            async with self.sql(permit) as connection:
                yield connection

    async def put_image(self, media, context, store, raw, *, job=None, **options):
        from platform_service.media.artifacts import inspect_image
        from platform_service.media_maintenance.runtime import authorize_write
        info = inspect_image(raw, max_bytes=min(options.pop('max_bytes', store.max_bytes), store.max_bytes),
                             max_pixels=store.max_pixels, mime=options.get('mime'))
        metadata = {'storage_key': secrets.token_hex(32), **info}
        if job is not None:
            permit = media.job_permit(job['id'])
            with operation(permit):
                await media.raw.stage_blob(context, metadata, kind='artifact', job_id=job['id'], attempt_id=job['attempt_id'])
                fence = await authorize_write(permit, metadata)
                return await physical_call(store.put_image, raw, fence=fence, permit=permit, **options)
        permit = await self.generation.acquire('upload', context['invocation_id'])
        media._upload_permits[metadata['storage_key']] = permit
        with operation(permit):
            await media.raw.stage_blob(context, metadata)
            fence = await authorize_write(permit, metadata)
            result = await physical_call(store.put_image, raw, fence=fence, permit=permit, **options)
            asset = await media.raw.register_asset(context, **result)
        await media.complete_uploads(context['invocation_id'])
        return {**result, 'asset_id': asset['id']}

    async def put_image_for_rejection(self, media, context, store, raw, *, reference_raw, **options):
        """Reserve a legal intent independently, then enter the real strict store.

        The reference is a valid fixture image: the same input for limit/mime
        cases, or an independent valid image for corrupt-content cases. Never
        inspect the tested input with the strict rule that its store must apply.
        """
        from platform_service.media.artifacts import inspect_image
        from platform_service.media_maintenance.runtime import authorize_write
        info = inspect_image(reference_raw, max_bytes=1024 * 1024, max_pixels=1024 * 1024)
        metadata = {'storage_key': secrets.token_hex(32), **info}
        permit = await self.generation.acquire('upload', context['invocation_id'])
        media._upload_permits[metadata['storage_key']] = permit
        try:
            with operation(permit):
                await media.raw.stage_blob(context, metadata)
                fence = await authorize_write(permit, metadata)
                return await physical_call(store.put_image, raw, fence=fence, permit=permit, **options)
        finally:
            # No attachment or successful settlement is inferred from a rejected
            # write. Retain this exact original intent as durably unknown.
            await self.generation.unknown(permit)

    async def gc_claim(self, media, metadata):
        await asyncio.sleep(1.05)
        await media.prepare_gc()
        rows = await media.claim_gc()
        claim = next(row for row in rows if row['storage_key'] == metadata['storage_key'])
        return claim, self.generation.owned('gc', claim['storage_key'], claim['gc_token'])

    async def delete_owned(self, media, store, claim, permit):
        return await media.raw.gc_delete_owned(store, claim, permit=permit)

    async def physical(self, function, *args, permit, **kwargs):
        return await physical_call(function, *args, permit=permit, **kwargs)

    async def create_runtime_role(self, role, password=None):
        # Exact fresh role only; the owned DB owner intentionally has no CREATEROLE.
        async with await psycopg.AsyncConnection.connect(self.env.base, autocommit=True) as admin:
            if password is None:
                await admin.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
            else:
                await admin.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(
                    sql.Identifier(role), sql.Literal(password)))
        self.created_roles.add(role)
        from platform_service.permissions import grant_runtime_permissions
        async with self.db.connection() as connection:
            await grant_runtime_permissions(connection, role)
            await connection.execute(sql.SQL('GRANT USAGE ON SCHEMA easel_backup_control TO {}').format(sql.Identifier(role)))
            for signature in (
                'register_worker(jsonb,bytea)', 'attest_worker(jsonb,bytea,jsonb)',
                'unregister_worker(jsonb,bytea)', 'issue_permit(jsonb,bytea,text,text,text,text,text)',
                'validate_permit(bytea,bigint,text,text,text,uuid,uuid)',
                'finish_permit(bytea,bigint,text)', 'permit_result(bytea,bigint)', 'mark_unknown(bytea,bigint)',
                'gc_writer_idle(text)', 'blob_writer_owned(bytea,bigint,text)',
            ):
                await connection.execute(sql.SQL('GRANT EXECUTE ON FUNCTION easel_backup_control.' + signature + ' TO {}').format(sql.Identifier(role)))

    async def drop_runtime_role(self, role):
        require(role in self.created_roles)
        target = make_conninfo(self.env.base, dbname=self.env.name)
        async with await psycopg.AsyncConnection.connect(target, autocommit=True) as admin:
            await admin.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
            await admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
        self.created_roles.remove(role)


@pytest_asyncio.fixture
async def legacy_media(tmp_path):
    owned = _owned_pg(20)
    env = next(owned)
    database = Database(env.dsn('owner'), schema='public')
    source = locks = generation = None
    directory_fd = None
    environment = None
    try:
        await database.open()
        profile = SchemaProfile('local_001_020', 'public', env.roles['runtime'],
                                env.roles['controller'], tuple(env.migrations))
        async with env.connection('owner') as connection:
            await install_control_schema(connection, profile)
            await connection.commit()
        control_path = tmp_path / 'legacy-control'
        control_path.mkdir(mode=0o700)
        lock_path = control_path / 'media.lock'
        lock_path.write_bytes(b'')
        lock_path.chmod(0o600)
        directory_fd = os.open(control_path, os.O_RDONLY | os.O_DIRECTORY)
        locks = ControlLock(directory_fd, 'media.lock', owner=(os.getuid(), os.getgid()))
        source_path = tmp_path / 'legacy-media'
        source_path.mkdir(mode=0o700)
        anchor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            source = SourceRoot.from_anchor(anchor, source_path.name)
        finally:
            os.close(anchor)
        runtime_db = DB(env, 'runtime')
        generation = RuntimeGeneration(runtime_db, locks, source=source)
        await generation.start()
        environment = LegacyMediaEnvironment(env, database, runtime_db, generation, locks, source)
        environment._source_path = source_path
        yield environment
    finally:
        if environment is not None:
            for runtime in reversed(environment.runtimes):
                await runtime.close()
        if generation is not None:
            for permit in generation.permits:
                if not permit._closed:
                    try:
                        await generation.unknown(permit)
                    except Exception:
                        pass
                    if not permit.physical_count:
                        permit.release_failed_physical()
        await database.close()
        if environment is not None:
            for role in tuple(environment.created_roles):
                await environment.drop_runtime_role(role)
        if source is not None:
            source.close()
        if locks is not None:
            locks.close()
        if directory_fd is not None:
            os.close(directory_fd)
        try:
            next(owned)
        except StopIteration:
            pass


@pytest.fixture
def legacy_db(legacy_media):
    return legacy_media.db


@pytest.fixture
def legacy_dsn(legacy_media):
    return legacy_media.env.dsn('owner')


@pytest_asyncio.fixture
async def legacy_bare_db():
    owned = _owned_pg(0)
    env = next(owned)
    database = Database(env.dsn('owner'), schema='public')
    try:
        await database.open()
        yield database
    finally:
        await database.close()
        try:
            next(owned)
        except StopIteration:
            pass


class Legacy014Fixture:
    """Owner SQL bootstrap before control exists; never a guarded runtime bypass."""
    def __init__(self, env, database):
        self.env, self.db = env, database

    async def bootstrap(self, *, terminal):
        import copy
        from dataclasses import asdict
        from psycopg.types.json import Jsonb
        from test_membership import seed
        from test_media_credits import REQUEST
        from test_media_contracts import definition
        from platform_service.media.contracts import digest, validate_definition, cost
        from platform_service.credits import CreditLedger
        membership,operator,original,scope=await seed(self.db,'300000000')
        await membership.publish_plan(operator,{**original,'id':'media-plan','name':'Media fixture','features':['text','media']})
        await membership.issue_test(operator,'wallet','media-plan','media-grant')
        ledger=CreditLedger(self.db)
        model=validate_definition(definition());request=copy.deepcopy(REQUEST)
        invocation,job_id,attempt_id=str(uuid4()),str(uuid4()),str(uuid4())
        request_hash=digest({'request':request,'definition':model,'binding_hash':'a'*64,'inputs':{}})
        amount=cost(model['rate'],model['limits']['max_units'])
        async with self.db.connection()as c:
            assert(await(await c.execute("SELECT to_regnamespace('easel_backup_control') value")).fetchone())['value']is None
            await c.execute("INSERT INTO product_clients(product_id,client_id) VALUES ('easel','desktop')")
            await c.execute("INSERT INTO external_identities(issuer,subject,user_id) VALUES ('fixture','subject','u')")
            await c.execute("INSERT INTO identity_sessions(id,user_id,tenant_id,billing_account_id,product_id,client_id,authorization_epoch,issuer,subject,expires_at) VALUES ('session','u','t','wallet','easel','desktop',0,'fixture','subject',clock_timestamp()+interval '1 hour')")
            await c.execute('INSERT INTO media_models(id,definition,operator_id) VALUES (%s,%s,%s)',(model['id'],Jsonb(model),'media-operator'))
            await c.execute('INSERT INTO media_model_states(model_id,enabled,operator_id) VALUES (%s,true,%s)',(model['id'],'media-operator'))
            await c.execute("INSERT INTO media_invocations(id,scope,session_id,user_id,product_id,tenant_id,billing_account_id,invocation_key,model,capability,funding_mode,expires_at) VALUES (%s,%s,'session','u','easel','t','wallet','legacy-014',%s,'image.generate','test',clock_timestamp()+interval '30 minutes')",(invocation,Jsonb(asdict(scope)),model['id']))
            reservation=await ledger.reserve_in_transaction(c,scope,operation='media',key=job_id,body_hash=request_hash,amount=amount,rate_version=model['rate']['version'],funding_mode='test',authorize_scope=False)
            job=await(await c.execute("INSERT INTO media_jobs(id,invocation_id,operation_key,request_hash,request,input_units,scope,user_id,product_id,tenant_id,billing_account_id,model,capability,funding_mode,model_definition,binding_hash,attempt_id,reservation_id,status,amount,lease_until) VALUES (%s,%s,'operation',%s,%s,'{}',%s,'u','easel','t','wallet',%s,'image.generate','test',%s,%s,%s,%s,'reserved',%s,clock_timestamp()+interval '1 minute') RETURNING *",(job_id,invocation,request_hash,Jsonb(request),Jsonb(asdict(scope)),model['id'],Jsonb(model),'a'*64,attempt_id,reservation['id'],amount))).fetchone()
            await c.execute('UPDATE media_invocations SET operation_count=1,held=%s WHERE id=%s',(amount,invocation))
            await c.execute('INSERT INTO media_provider_attempts(job_id,attempt_id,binding_hash,provider_scope_hash,provider_request_hash,upstream_model,request_id) VALUES (%s,%s,%s,%s,%s,%s,%s)',(job_id,attempt_id,'a'*64,'c'*64,'b'*64,'fixture-upstream','request-'+job_id))
            await c.execute("UPDATE credit_reservations SET status='dispatched' WHERE id=%s",(reservation['id'],))
            await c.execute("UPDATE media_jobs SET status='dispatched',execution_state='running',dispatched_at=clock_timestamp() WHERE id=%s",(job_id,))
            await c.execute("INSERT INTO media_provider_associations(job_id,kind,attempt_id,binding_hash,provider_scope_hash,http_status) VALUES (%s,'http',%s,%s,%s,200)",(job_id,attempt_id,'a'*64,'c'*64))
            await c.execute("INSERT INTO media_provider_associations(job_id,kind,attempt_id,binding_hash,provider_scope_hash,provider_response_id) VALUES (%s,'response',%s,%s,%s,%s)",(job_id,attempt_id,'a'*64,'c'*64,'response-'+job_id))
            await c.execute("INSERT INTO media_artifacts(id,job_id,invocation_id,attempt_id,scope,user_id,product_id,tenant_id,billing_account_id,storage_key,sha256,size,mime,properties,expires_at) SELECT 'legacy-artifact',id,invocation_id,attempt_id,scope,user_id,product_id,tenant_id,billing_account_id,repeat('a',64),repeat('b',64),10,'image/png','{\"width\":1,\"height\":1,\"format\":\"PNG\"}',clock_timestamp()+interval '24 hours' FROM media_jobs WHERE id=%s",(job_id,))
            if terminal:
                quantities={'image':1};basis={'verified_delivery_v1':'d'*64};actual=cost(model['rate'],quantities)
                await c.execute("INSERT INTO media_terminal_evidence(job_id,attempt_id,binding_hash,terminal_status,provider_response_id,quantities,basis_evidence,evidence_hash) VALUES (%s,%s,%s,'completed',%s,%s,%s,%s)",(job_id,attempt_id,'a'*64,'response-'+job_id,Jsonb(quantities),Jsonb(basis),'e'*64))
                reservation_row=await(await c.execute('SELECT * FROM credit_reservations WHERE id=%s',(reservation['id'],))).fetchone()
                await ledger._complete(c,scope,reservation_row,actual,attempt_id,'settled')
                await c.execute("INSERT INTO media_receipts(job_id,attempt_id,quantities,basis_evidence,evidence_hash,terminal_status,provider_response_id,actual) VALUES (%s,%s,%s,%s,%s,'completed',%s,%s)",(job_id,attempt_id,Jsonb(quantities),Jsonb(basis),'e'*64,'response-'+job_id,actual))
                await c.execute('UPDATE media_invocations SET held=0,spent=%s WHERE id=%s',(actual,invocation))
                await c.execute("UPDATE media_jobs SET status='settled',actual=%s,execution_state='completed',delivery_state='available' WHERE id=%s",(actual,job_id))
            else:
                await c.execute("UPDATE credit_reservations SET status='pending_reconciliation' WHERE id=%s",(reservation['id'],))
                await c.execute("UPDATE media_jobs SET status='pending_reconciliation',execution_state='unknown',reason='legacy_unknown' WHERE id=%s",(job_id,))
        return ledger,scope,job

    async def reject_control_on_actual014(self):
        import hashlib
        from platform_service.db import MIGRATIONS_DIR
        from platform_service.media_maintenance.contracts import MaintenanceError
        migrations=tuple((p.name,hashlib.sha256(p.read_bytes()).hexdigest())for p in sorted(MIGRATIONS_DIR.glob('*.sql'))[:16])
        expected=SchemaProfile('public_001_016','public',self.env.roles['runtime'],self.env.roles['controller'],migrations)
        with pytest.raises(MaintenanceError,match='invalid_backup'):
            async with self.env.connection('owner')as connection:
                await install_control_schema(connection,expected)
        async with self.db.connection()as c:
            assert(await(await c.execute("SELECT to_regnamespace('easel_backup_control') value")).fetchone())['value']is None
            assert(await(await c.execute('SELECT count(*) n FROM schema_migrations')).fetchone())['n']==14


@pytest_asyncio.fixture
async def legacy_014():
    owned=_owned_pg(14)
    env=next(owned)
    database=Database(env.dsn('owner'),schema='public')
    try:
        await database.open()
        yield Legacy014Fixture(env,database)
    finally:
        await database.close()
        try:next(owned)
        except StopIteration:pass
