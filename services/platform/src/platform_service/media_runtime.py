"""Scoped synchronous image jobs over shared credits and persistent evidence."""
import asyncio
import hashlib
import json
import os
from uuid import uuid4

import httpx

from .errors import PlatformError
from .media.artifacts import ArtifactStore, public_artifact
from .media.adapters.openai_images import OpenAIImagesAdapter
from .media_config import IMAGE_CAPABILITIES, ASYNC_CAPABILITIES, AUDIO_CAPABILITIES, VOICE_CAPABILITIES, AV_ADAPTERS, SPEECH_ADAPTERS, audio_profile
from .gateway_catalog import GatewayPreflight, ManagedBindings
from .media.async_store import MediaAsyncStore
from .media.adapters.newapi_async import make_async_adapter
from .media.worker import MediaWorker
from .media_maintenance.runtime import RuntimeGeneration,current_permit,mutation,operation,physical_call,runtime_boundary,runtime_lifecycle
from .media_maintenance.filesystem import ControlLock,SourceRoot
from .media_maintenance.contracts import MaintenanceError


SUPPORTED_METERS = frozenset({("image", "verified_delivery_v1"), ("unicode_scalar", "verified_input_v1"),
                               ("input_token", "provider_usage_v1"), ("output_token", "provider_usage_v1")})


def allowed_meters(provider, capability=None):
    if provider.adapter in VOICE_CAPABILITIES:
        if capability == "voice.enroll": return frozenset({("registration","verified_delivery_v1")})
        meters={("unicode_scalar","verified_input_v1"),("audio_ms","verified_delivery_v1")}
        if provider.adapter == "newapi_minimax_voice_v1": meters.add(("audio_ms","provider_usage_v1"))
        return frozenset(meters)
    if provider.adapter in {'newapi_videos_v1','dashscope_video_v1','ark_video_v1','kling_video_v1','agnes_video_v1','xhs_video_v1'}:return frozenset({('video_ms','verified_delivery_v1')})
    if provider.adapter in {'newapi_suno_v1','newapi_incho_v1','dashscope_music_v1','suno_direct_v1'}:return frozenset({('audio_ms','verified_delivery_v1'),('audio_clip','verified_delivery_v1')})
    if provider.adapter == 'newapi_dashscope_music_v1':return frozenset({('audio_ms','provider_usage_v1'),('audio_ms','verified_delivery_v1')})
    if provider.adapter in AUDIO_CAPABILITIES:
        from .media.adapters.audio import OpenAISpeechAdapter
        if provider.adapter in SPEECH_ADAPTERS: return OpenAISpeechAdapter.supported_meters
        usage = audio_profile(provider).transcription_usage
        return frozenset({('audio_ms','verified_input_v1')} | ({('input_token','provider_usage_v1'),('output_token','provider_usage_v1')} if usage=='tokens' else {('audio_ms','provider_usage_v1')} if usage=='duration' else set()))
    return frozenset({("image", "verified_delivery_v1")}) if provider.response_profile == "newapi_images_v1" else SUPPORTED_METERS


def supported_metering(definition, provider=None):
    meters = definition.get("rate", {}).get("meters")
    allowed = allowed_meters(provider, definition.get("capability")) if provider is not None else SUPPORTED_METERS
    return bool(isinstance(meters, list) and meters and all((meter.get("unit"), meter.get("basis")) in allowed for meter in meters))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


# The desktop refuses a longer list outright, which would take away every media tool.
MAX_PUBLISHED_MODELS = 100

class MediaRuntime:
    def __init__(self, db, identity, settings, *, service=None, transport=None, maintenance=None, control_lock=None, source_root=None, provider_keys=None):
        if service is None:
            from .media.core import MediaService
            service = MediaService(db, identity)
        if maintenance is not None and type(maintenance) is not RuntimeGeneration:raise MaintenanceError()
        if control_lock is not None and type(control_lock) is not ControlLock:raise MaintenanceError()
        if source_root is not None and type(source_root) is not SourceRoot:raise MaintenanceError()
        self.maintenance,self._control_lock,self._source_root=maintenance,control_lock,source_root
        self.service, self.settings, self.bindings = service, settings, settings.bindings
        self._managed = None if db is None else ManagedBindings(db, 'media', settings, lambda provider: (provider.adapter, provider.protocol_version, provider.capabilities), keys=provider_keys)
        self.store = ArtifactStore(settings.storage_root, max_bytes=settings.max_asset_bytes, max_pixels=settings.max_pixels) if settings.storage_root is not None else None
        self._semaphore, self._uploads = asyncio.Semaphore(settings.concurrency), asyncio.Semaphore(1)
        self.tasks = set()
        # Video and music interfaces can arrive from the console after start.
        self._console_interfaces = provider_keys is not None
        self.async_queue = MediaAsyncStore(service) if self._console_interfaces or any(p.adapter in {*ASYNC_CAPABILITIES, *VOICE_CAPABILITIES} for p in settings.providers.values()) else None
        from .media.voices import OwnedVoices
        from .media.voice_execution import VoiceExecution
        has_voice=self._console_interfaces or any(p.adapter in VOICE_CAPABILITIES for p in settings.providers.values())
        self.voices = OwnedVoices(service.db, service.identity, service) if has_voice else None
        self.voice_execution = VoiceExecution(self) if has_voice else None
        self._client = httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False,
            timeout=httpx.Timeout(settings.timeout_seconds, connect=10),
            limits=httpx.Limits(max_connections=settings.concurrency + 1, max_keepalive_connections=settings.concurrency + 1))
        self._get_client = httpx.AsyncClient(transport=transport,trust_env=False,follow_redirects=False,timeout=httpx.Timeout(90,connect=10),limits=httpx.Limits(max_connections=settings.download_concurrency,max_keepalive_connections=settings.download_concurrency))
        self._preflight = GatewayPreflight(self._get_client, 'media_channel_unavailable')
        if self._managed is not None:self._managed.client=self._get_client
        self.worker = MediaWorker(self.async_queue,self.store,self._get_client,settings,self._frozen_async_provider) if self.async_queue is not None else None

    @runtime_lifecycle('start')
    async def start(self):
        if self.settings.mode=='disabled':return
        try:
            if self.maintenance is None and self.settings.maintenance=='local':
                if self.settings.storage_root is None:raise MaintenanceError()
                self.maintenance=RuntimeGeneration.local(self.service.db,storage_root=self.settings.storage_root)
            elif self.maintenance is None:
                self._control_lock=self._control_lock or ControlLock.from_path(str(self.settings.control_root),owner=(0,os.getegid()))
                self._source_root=self._source_root or SourceRoot.from_path(str(self.settings.storage_root))
                self.maintenance=RuntimeGeneration(self.service.db,self._control_lock,source=self._source_root)
            self.maintenance.source.verify_path(self.settings.storage_root)
            if not self.maintenance._started:await self.maintenance.start()
            self.service.maintenance=self.maintenance
        except MaintenanceError:
            # The ordinary text API/read surface stays available. Every media
            # writer below still requires a successfully registered generation.
            return
        has_audio=self._console_interfaces or any(p.adapter in AUDIO_CAPABILITIES for p in self.settings.providers.values())
        if self.settings.mode=='disabled' or self.async_queue is None and not has_audio:return
        # Runtime never migrates. The owner installs 018 for async media and
        # 017/019 for voice/audio, with the exact runtime DML grants.
        import psycopg
        try:
            async with self.service.db.connection()as c:
                tables=('media_work','media_observations','media_sources') if self.async_queue is not None else ()
                if has_audio:tables+=('media_protocol_facts',)
                if self.voice_execution is not None:tables+=('media_voice_steps','media_voice_step_observations','media_voice_registration_proofs','media_protocol_facts')
                for table in tables:
                    await c.execute('SELECT * FROM '+table+' LIMIT 0')
                if self.voice_execution is not None:
                    constraint=await (await c.execute("SELECT pg_get_constraintdef(oid) definition FROM pg_constraint WHERE conrelid='media_work'::regclass AND conname='media_work_adapter_check'")).fetchone()
                    if not constraint or any(name not in constraint['definition'] for name in VOICE_CAPABILITIES):raise PlatformError(503,'media_voice_migration_required')
                if any(p.adapter=='newapi_incho_v1'for p in self.settings.providers.values()):
                    constraint=await (await c.execute("SELECT pg_get_constraintdef(oid) definition FROM pg_constraint WHERE conrelid='media_work'::regclass AND conname='media_work_adapter_check'")).fetchone()
                    if not constraint or 'newapi_incho_v1'not in constraint['definition']:
                        raise PlatformError(503,'media_incho_migration_required')
        except psycopg.Error:raise PlatformError(503,'media_async_migration_required')from None

    async def _av_ready(self):
        if self.settings.av_tools is None or not self.settings.worker_enabled:return False
        from .media.av_artifacts import _execute
        import re
        try:
            for executable in self.settings.av_tools.paths():
                value=await _execute(executable,['-version'],timeout=5,maximum=8192)
                if not re.match(rb'ff(?:mpeg|probe) version '+re.escape(self.settings.av_tools.version.encode())+rb'(?:\s|$)',value):return False
            return True
        except PlatformError:return False

    async def _frozen_async_provider(self,job):
        provider=self._provider(job['model'],job['capability'])
        binding=self.bindings.get(job['model'])
        if not binding or binding['binding_hash']!=job['binding_hash'] or job['funding_mode']!=self.settings.mode:
            raise PlatformError(503,'media_binding_unavailable')
        if not await self._av_ready():raise PlatformError(503,'media_decoder_unavailable')
        return provider

    def _enabled(self):
        if self.settings.mode == "disabled" or self.store is None:
            raise PlatformError(503, "media_execution_disabled")

    def _provider(self, model, capability):
        if not isinstance(model, str) or not isinstance(capability, str):
            raise PlatformError(422, "invalid_media_request")
        binding = self.bindings.get(model)
        provider = self.settings.providers.get(binding["provider_id"]) if binding else None
        if binding and self.settings.bindings.get(model) != binding:
            raise PlatformError(503,"media_binding_unavailable")
        if (provider is None or capability not in provider.capabilities
                or provider.adapter in AV_ADAPTERS and (self.settings.av_tools is None or not self.settings.worker_enabled)
                or provider.adapter in {'newapi_suno_v1','newapi_dashscope_music_v1','newapi_incho_v1'} and (not provider.download_origins or not provider.download_path_prefixes)):
            raise PlatformError(503, "media_capability_closed")
        if provider.adapter=='newapi_dashscope_voice_v1' and capability=='voice.enroll' and not provider.client_correlation:
            raise PlatformError(503,'voice_sample_publication_unavailable')
        return provider

    async def _sync(self):
        if self._managed is not None:
            self.settings, self.bindings = await self._managed.refresh()

    async def catalog(self, scope):
        if self.settings.mode == "disabled": return {"models": []}
        await self._sync()
        value = await self.service.catalog(scope, mode=self.settings.mode, bindings=self.bindings)
        rows = []
        av_ready = await self._av_ready() if any(p.adapter in AV_ADAPTERS for p in self.settings.providers.values()) else False
        for row in value.get("models", []):
            try: provider = self._provider(row["id"], row["capability"])
            except (PlatformError, KeyError): continue
            if provider.adapter in AV_ADAPTERS and not av_ready:continue
            if supported_metering(row, provider): rows.append(row)
        return {"models": rows[:MAX_PUBLISHED_MODELS]}

    async def _model(self, scope, model):
        rows = (await self.service.catalog(scope, mode=self.settings.mode, bindings=self.bindings)).get("models", [])
        row = next((row for row in rows if row.get("id") == model), None)
        if row is None: raise PlatformError(503, "media_capability_closed")
        if self._provider(row["id"],row["capability"]).adapter in AV_ADAPTERS and not await self._av_ready():raise PlatformError(503,"media_decoder_unavailable")
        if not supported_metering(row, self._provider(row["id"], row["capability"])): raise PlatformError(503, "media_metering_unsupported")
        return row

    async def capabilities(self, product_id):
        await self._sync()
        groups = {}
        av_ready=await self._av_ready() if any(p.adapter in AV_ADAPTERS for p in self.settings.providers.values()) else False
        for model,binding in self.bindings.items():
            candidates=self.settings.providers[binding['provider_id']].capabilities
            provider=None
            for capability in candidates:
                try: provider=self._provider(model,capability);break
                except (PlatformError,KeyError):continue
            if provider is None:continue
            if provider.adapter in AV_ADAPTERS and not av_ready:continue
            for capability in provider.capabilities:
                if provider.adapter=='newapi_dashscope_voice_v1' and capability=='voice.enroll' and not provider.client_correlation:continue
                meters = allowed_meters(provider, capability)
                group = groups.setdefault(meters, {"bindings": {}, "capabilities": {}})
                group["bindings"][model] = binding
                group["capabilities"].setdefault(model, []).append(capability)
        ready = False
        if self.settings.mode != "disabled":
            for meters, group in groups.items():
                if await self.service.readiness(product_id, mode=self.settings.mode, bindings=group["bindings"],
                        supported_meters=meters, supported_capabilities=group["capabilities"]):
                    ready = True
                    break
        return {"media_execution_enabled": bool(ready and self.settings.mode == "live"),
                "test_media_execution_enabled": bool(ready and self.settings.mode == "test"),
                "media_execution_stage": self.settings.mode}

    def _maintenance(self):
        if self.maintenance is None or not self.maintenance._started or self.maintenance._closing:
            raise PlatformError(503,'media_maintenance_unavailable')
        return self.maintenance

    async def _finish_job(self,permit):
        return await self.maintenance.finish_job_if_stable(permit)

    @runtime_boundary
    async def cancel(self,scope,job_id):
        maintenance=self._maintenance()
        async with self.service.db.connection() as c:
            await self.service.identity.validate_scope(c,scope)
            row=await(await c.execute('SELECT id,attempt_id,scope FROM media_jobs WHERE id=%s',(job_id,))).fetchone()
            from .media.core import same_owner,scope_of
            if row is None or not same_owner(scope_of(row['scope']),scope):raise PlatformError(404,'media_not_found')
        permit=await maintenance.acquire('job',job_id,row['attempt_id'])
        with operation(permit):
            result=await self.service.cancel(scope,job_id)
            await self._finish_job(permit);return result

    @runtime_boundary
    async def mint(self, scope, body):
        self._enabled()
        if not isinstance(body, dict) or set(body) != {"model", "capability", "invocation_key"}:
            raise PlatformError(400, "invalid_request")
        self._provider(body["model"], body["capability"])
        await self._model(scope, body["model"])
        maintenance=self._maintenance()
        invocation=await self.service.invocation_identity(scope,body['invocation_key']) or str(uuid4())
        async with maintenance.use('mint',invocation,transient=True):
            return await self.service.mint(scope, **body, mode=self.settings.mode, bindings=self.bindings)

    async def _context(self, token):
        self._enabled()
        await self._sync()
        value = await self.service.authenticate_task(token)
        if value["funding_mode"] != self.settings.mode: raise PlatformError(403, "media_mode_mismatch")
        return value

    @runtime_boundary
    async def upload_asset(self, token, chunks, *, mime, declared_size=None):
        context = await self._context(token)
        maximum = min(self.settings.max_asset_bytes, context.get("model_limits", {}).get("max_input_bytes", self.settings.max_asset_bytes))
        if declared_size is not None and (type(declared_size) is not int or not 0 < declared_size <= maximum):
            raise PlatformError(413, "media_input_too_large")
        async with self._maintenance().use("upload",context["invocation_id"],transient=True):
            async with self._uploads:
                await physical_call(self.store.check_capacity,maximum)
                raw = bytearray()
                try:
                    async with asyncio.timeout(min(30, self.settings.timeout_seconds)):
                        async for chunk in chunks:
                            if len(raw) + len(chunk) > maximum: raise PlatformError(413, "media_input_too_large")
                            raw.extend(chunk)
                except TimeoutError:
                    raise PlatformError(408, "media_upload_timeout") from None
                if declared_size is not None and len(raw) != declared_size: raise PlatformError(400, "invalid_media_upload")
                if context['capability'] in ('audio.transcribe', 'voice.enroll', 'audio.speech', 'voice.synthesize'):
                    from .media.audio_execution import store_input
                    metadata = await physical_call(store_input,self, context, bytes(raw), mime)
                else:
                    metadata = await physical_call(self.store.put_image_async,bytes(raw), mime=mime,
                        before_write=lambda value:self.service.stage_blob(context,value))
                try:
                    registered = await self.service.register_asset(context, **metadata)
                except PlatformError as error:
                    if 400 <= error.status < 500: await physical_call(self.store.discard,metadata)
                    raise
                return public_artifact(registered)

    async def _view(self, scope, job_id):
        value = await self.service.get(scope, job_id)
        allowed = {"id", "model", "capability", "status", "financial_state", "execution_state", "delivery_state", "amount", "reserved", "actual", "rate_version", "funding_mode", "created_at", "updated_at", "failure_code"}
        result = {key: item for key, item in value.items() if key in allowed}
        result["artifacts"] = [public_artifact(row) for row in value.get("artifacts", [])]
        if value.get('voice') and self.voices is not None:
            try: result['voice'] = await self.voices.public(scope,value['voice']['id'])
            except PlatformError as error:
                if error.status not in (404,410):raise
        return result

    @runtime_boundary
    async def submit(self, task_token, key, body):
        context = await self._context(task_token)
        if not isinstance(body, dict): raise PlatformError(400, "invalid_media_request")
        # Query an already accepted operation before touching mutable inputs or
        # capacity. Core rechecks the immutable request; this path never dispatches.
        try:
            original=await self.service.lookup_operation(context["scope"], context["invocation_id"], key,raw=True)
        except PlatformError as error:
            if error.status != 404: raise
        else:
            from .media.contracts import validate_request
            if validate_request(original['model_definition'],body)!=original['request']:raise PlatformError(409,'idempotency_conflict')
            return await self._view(context["scope"], original["id"])
        self._provider(body.get("model"), body.get("capability"))
        await self._model(context["scope"], body["model"])
        async with self._maintenance().use("job",str(uuid4()),str(uuid4())):
            await physical_call(self.store.check_capacity,context.get("model_limits", {}).get("max_artifact_bytes", self.settings.max_asset_bytes))
            assets = body.get("assets", [])
            if not isinstance(assets, list) or len(assets) > 32 or any(not isinstance(item, str) for item in assets):
                raise PlatformError(422, "invalid_media_assets")
            metadata = {identifier: await self.service.get_asset(context, identifier) for identifier in assets}
            provider = self._provider(body['model'],body['capability'])
            if body['capability']=='voice.synthesize':
                from .media.voice_execution import owned, owned_direct
                await (owned_direct if provider.adapter in AUDIO_CAPABILITIES else owned)(self,context['scope'],body,provider)
            verified = {identifier: {field: row[field] for field in ("sha256", "size", "mime", "properties")} for identifier, row in metadata.items()}
            job = await self.service.admit(context, key=key, request=body, bindings=self.bindings, verified_inputs=verified)
            if job.get("replay"): return await self._view(context["scope"], job["id"])
            task = asyncio.create_task(self._execute(context, job, metadata))
            self.tasks.add(task)
            def done(future):
                self.tasks.discard(future)
                if not future.cancelled(): future.exception()
            task.add_done_callback(done)
            return await asyncio.shield(task)

    @mutation('job',subject=lambda v:v['job']['id'],attempt=lambda v:v['job']['attempt_id'])
    async def _heartbeat(self, job):
        while True:
            await asyncio.sleep(self.settings.heartbeat_seconds)
            await self.service.heartbeat(job["id"], job["attempt_id"])

    @mutation("job",subject=lambda v:v["job"]["id"],attempt=lambda v:v["job"]["attempt_id"])
    async def _execute(self, context, job, metadata):
        dispatched, evidence, beat = False, False, None
        try:
            async with self._semaphore:
                async with asyncio.timeout(self.settings.timeout_seconds):
                    request = job["request"]
                    provider = self._provider(request["model"], request["capability"])
                    if not supported_metering(job["model_definition"], provider): raise PlatformError(503, "media_metering_unsupported")
                    if self.bindings[request["model"]]["binding_hash"] != job["binding_hash"]:
                        raise PlatformError(503, "media_binding_unavailable")
                    assets = [{**row, "id": identifier, "raw": self.store.read(row)} for identifier, row in metadata.items()]
                    is_voice = provider.adapter in VOICE_CAPABILITIES
                    is_async=provider.adapter in ASYNC_CAPABILITIES
                    is_audio = provider.adapter in AUDIO_CAPABILITIES
                    from .media.audio_execution import make_audio_adapter, finish_audio
                    adapter = make_audio_adapter(provider,self.settings.mode) if is_audio else make_async_adapter(provider,self.settings.mode,job) if is_async else OpenAIImagesAdapter(provider, self.settings.mode)
                    if is_voice:
                        from .media.voice_execution import make_voice_adapter, owned
                        reference,boundary = await owned(self,context['scope'],request,provider) if request['capability']=='voice.synthesize' else (None,None)
                        adapter=make_voice_adapter(provider,self.settings.mode,binding_hash=job['binding_hash'],voice_boundary_hash=boundary)
                        if request['capability']=='voice.enroll':prepared=adapter.prepare_upload(request,assets)
                        elif reference is not None:prepared=adapter.prepare(request,assets,owned_voice=reference)
                        else:prepared=adapter.prepare(request,assets)
                    elif is_audio and request['capability']=='voice.synthesize':
                        from .media.voice_execution import owned_direct
                        prepared=adapter.prepare(request,assets,owned_voice=await owned_direct(self,context['scope'],request,provider))
                    else: prepared = adapter.prepare(request, assets)
                    await physical_call(self.store.check_capacity,job["model_definition"]["limits"]["max_artifact_bytes"])
                    if len(prepared.payload) > 64 * 1024 * 1024: raise PlatformError(413, "media_input_too_large")
                    if provider.preflight_models:
                        await self._preflight.check(provider.base_url.rstrip('/')+'/models', provider.secret, prepared.upstream_model)
                    request_id = str(uuid4())
                    await self.service.record_provider_attempt(job["id"], job["attempt_id"],
                        provider_request_hash=hashlib.sha256(prepared.payload).hexdigest(),
                        provider_scope_hash=digest([provider.base_url, hashlib.sha256(provider.secret.encode()).hexdigest()]),
                        upstream_model=prepared.upstream_model, request_id=request_id)
                    await self.service.dispatch(job["id"], job["attempt_id"])
                    dispatched = True
                    beat = asyncio.create_task(self._heartbeat(job))
                    async def headers(**values):
                        await self.service.record_provider_association(job["id"], job["attempt_id"], **values)
                    if is_voice:
                        if request['capability']=='voice.enroll':
                            await self.voice_execution.enroll(context,job,adapter,prepared,assets)
                        else:
                            from .media.adapters.voice_async import VoiceRecoveryAdapter
                            async def associated_voice(task, sha):
                                await self.service.record_provider_association(job['id'],job['attempt_id'],provider_job_id=task)
                            result=await adapter.submit(self._client,prepared,request_id=request_id,max_bytes=1048576,on_headers=headers,on_task=associated_voice)
                            await self.async_queue.enqueue(job['id'],job['attempt_id'])
                            if result.state=='completed':
                                await self.async_queue.seed(job['id'],VoiceRecoveryAdapter(provider,self.settings.mode,job).observation(result))
                        return await self._view(context['scope'],job['id'])
                    if is_async:
                        async def associated(known):
                            await self.service.record_provider_association(job['id'],job['attempt_id'],provider_job_id=known)
                        submission=await adapter.submit(self._client,prepared,request_id=request_id,max_bytes=self.settings.max_response_bytes,on_headers=headers,on_job=associated)
                        await self.async_queue.enqueue(job['id'],job['attempt_id'])
                        if submission.observation is not None and submission.observation.state in {"completed","failed","cancelled"}:
                            await self.async_queue.seed(job['id'],submission.observation)
                        return await self._view(context['scope'],job['id'])
                    response = await adapter.submit(self._client, prepared, request_id=request_id,
                                                   max_bytes=self.settings.max_response_bytes, on_headers=headers)
                    if is_audio:
                        await physical_call(finish_audio,self, context, job, response, prepared)
                        evidence = True
                        await self.service.finish_from_evidence(job['id'], job['attempt_id'])
                        return await self._view(context['scope'], job['id'])
                    terminal_reference = {"provider_response_id": response.response_id}
                    if response.correlation is not None:
                        await self.service.record_http_response_reference(job["id"], job["attempt_id"],
                            namespace=response.correlation.namespace, http_request_id=response.correlation.value,
                            response_body_sha256=response.evidence_hash)
                        terminal_reference.update(reference_kind="http_request_id",
                            http_reference_namespace=response.correlation.namespace,
                            http_reference_value=response.correlation.value)
                    else:
                        await self.service.record_provider_association(job["id"], job["attempt_id"], provider_response_id=response.response_id)
                    if len(response.images) > request.get("parameters", {}).get("n", 1):
                        raise PlatformError(502, "invalid_media_response")
                    artifacts = []
                    ceiling = job["model_definition"]["limits"]["max_artifact_bytes"]
                    total = 0
                    async def stage_artifact(value):
                        if provider.response_profile == "newapi_images_v1":
                            # Only complete decoder properties can certify the
                            # requested delivery. Reject before any blob stage,
                            # file write, registration, terminal, or settlement.
                            expected = tuple(int(part) for part in request["parameters"]["size"].split("x"))
                            actual = tuple(value["properties"].get(key) for key in ("width", "height"))
                            if actual != expected:
                                raise PlatformError(502, "media_delivery_dimensions_mismatch")
                        return await self.service.stage_blob(context,value,kind="artifact",job_id=job["id"],attempt_id=job["attempt_id"])
                    for source in response.images:
                        raw, mime = await adapter.retrieve(self._client, source, max_bytes=min(ceiling, self.settings.max_asset_bytes))
                        total += len(raw)
                        if total > ceiling: raise PlatformError(502, "media_response_too_large")
                        stored = await physical_call(self.store.put_image_async,raw, mime=mime, max_bytes=ceiling,
                            before_write=stage_artifact)
                        try:
                            artifacts.append(await self.service.register_artifact(job["id"], job["attempt_id"], **stored))
                        except PlatformError as error:
                            if 400 <= error.status < 500: await physical_call(self.store.discard,stored)
                            raise
                    measured = {"image": len(artifacts), **response.quantities,
                                "unicode_scalar": len(request.get("prompt", ""))}
                    quantities, bases = {}, {}
                    for meter in job["model_definition"]["rate"]["meters"]:
                        unit, basis = meter["unit"], meter["basis"]
                        if unit not in measured: raise PlatformError(502, "invalid_media_usage")
                        quantities[unit] = measured[unit]
                        if basis == "provider_usage_v1":
                            if response.usage_hash is None: raise PlatformError(502, "invalid_media_usage")
                            bases[basis] = response.usage_hash
                        elif basis == "verified_delivery_v1":
                            bases[basis] = digest([public_artifact(row) for row in artifacts])
                        else:
                            bases[basis] = digest({"request_hash": job["request_hash"], "inputs": verified_input_hashes(metadata)})
                    await self.service.record_terminal_evidence(job["id"], job["attempt_id"], terminal_status="completed",
                        **terminal_reference, quantities=quantities, basis_evidence=bases, evidence_hash=response.evidence_hash)
                    evidence = True
                    await self.service.finish_from_evidence(job["id"], job["attempt_id"])
                    return await self._view(context["scope"], job["id"])
        except BaseException as exc:
            try:
                if dispatched:
                    await asyncio.shield(self.service.pending(job["id"], job["attempt_id"], "settlement_pending" if evidence else "upstream_uncertain"))
                else:
                    await asyncio.shield(self.service.cancel(context["scope"], job["id"]))
            except Exception: pass
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)): raise
            if evidence:
                # Content may already be available even if finance needs a later
                # recovery. No second generation is attempted.
                return await self._view(context["scope"], job["id"])
            raise PlatformError(502, "media_execution_failed") from None
        finally:
            if beat is not None:
                beat.cancel(); await asyncio.gather(beat, return_exceptions=True)
            permit=current_permit(kinds={'job'},subject=job['id'],attempt=job['attempt_id'])
            await self._finish_job(permit)

    async def artifact(self, scope, artifact_id, *, content=False):
        self._enabled()
        if content:
            return await self.service.read_artifact(scope,artifact_id,lambda row:(self.store.read(row),row["mime"],row["sha256"]))
        return public_artifact(await self.service.get_artifact(scope,artifact_id))

    async def _continue_once(self,permit):
        # This path only possesses original in-memory operations; it cannot
        # recreate a digest/token or claim a new object while DRAINING.
        from .media_maintenance.runtime import validate
        await validate(permit)
        with operation(permit):
            if permit.binding.kind=='job':
                if self.worker is not None:await self.worker.run_once(permit=permit)
                if self.voice_execution is not None:await self.voice_execution.recover(permit=permit)
                await self.service.recover_financial(permit=permit)
                await self.maintenance.finish_job_if_stable(permit)
            elif permit.binding.kind=='gc':
                claim=self.maintenance._gc_rows.get(permit.token_digest)
                if claim is not None:
                    await self.service.gc_delete_owned(self.store,claim,permit=permit)
                    await self.maintenance.finish(permit,{'deleted':claim['storage_key']})

    @runtime_boundary
    async def run_once(self):
        await self._sync()
        maintenance=self._maintenance();state=await maintenance.control.status()
        if state['state']=='DRAINING':
            continued=0
            for permit in maintenance.permits:
                if permit.binding.kind not in {'job','gc'} or not permit._valid or permit._closing:continue
                await self._continue_once(permit);continued+=1
            return {'continued':continued,'financial':[],'gc':0,'async':0}
        if state['state']!='OPEN':raise PlatformError(503,'media_maintenance_unavailable')
        async with self._maintenance().use("maintenance","run-"+str(uuid4()),transient=True):
            async_count=await self.worker.run_once() if self.store is not None and any(p.adapter in {*ASYNC_CAPABILITIES, *VOICE_CAPABILITIES} for p in self.settings.providers.values()) else 0
            if any(p.adapter in VOICE_CAPABILITIES for p in self.settings.providers.values()):await self.voice_execution.recover()
            financial=await self.service.recover_financial()
            if self.store is None:return {"financial":financial,"gc":0}
            await self.service.prepare_gc()
            completed=0
            for row in await self.service.claim_gc():
                try:
                    permit=self.maintenance.owned('gc',row['storage_key'],row['gc_token'])
                    await self.service.gc_delete_owned(self.store,row,permit=permit)
                    await self.maintenance.finish(permit,{'deleted':row['storage_key']})
                    completed+=1
                except (PlatformError,OSError):continue
            return {"financial":financial,"gc":completed,"async":async_count}

    @runtime_boundary
    async def run_forever(self):
        self._maintenance()
        while True:
            try: await self.run_once()
            except asyncio.CancelledError: raise
            except Exception: pass
            await asyncio.sleep(self.settings.recovery_seconds)

    @runtime_lifecycle('close')
    async def close(self):
        tasks = list(self.tasks)
        for task in tasks: task.cancel()
        if tasks: await asyncio.gather(*tasks, return_exceptions=True)
        if self._managed is not None: await self._managed.close()
        await self._client.aclose()
        await self._get_client.aclose()
        if self.maintenance is not None and self.maintenance._started:
            for permit in self.maintenance.permits:
                if not permit._closed:
                    try:await self.maintenance.unknown(permit)
                    except MaintenanceError:pass
                    if not permit.physical_count:permit.release_failed_physical()
            if not self.maintenance.permits:await self.maintenance.close()
        if self._control_lock is not None:self._control_lock.close()


def verified_input_hashes(metadata):
    return {key: value["sha256"] for key, value in sorted(metadata.items())}
