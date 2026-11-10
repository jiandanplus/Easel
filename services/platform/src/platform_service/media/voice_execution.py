"""Explicit POST stage runner. Recovery of uncertain stages is GET-only."""
from ..media_maintenance.runtime import mutation,physical_guard,current_permit,GuardedDatabase,physical_call,operation
import asyncio
from contextlib import suppress, asynccontextmanager
import hashlib
from uuid import uuid4
from ..errors import PlatformError
from ..media_config import voice_profile, voice_boundary
from .adapters.newapi_voice import MiniMaxVoiceAdapter, DashScopeVoiceAdapter, FishVoiceAdapter, BridgeVoiceResult, GatewayTaskReference
from .contracts import digest
from .voices import VoiceStages, OwnedVoices
from . import voices as voice_clock
from datetime import timedelta


def provider_scope(provider):
    return digest([provider.base_url,hashlib.sha256(provider.secret.encode()).hexdigest()])


def make_voice_adapter(provider,mode,*,binding_hash,voice_boundary_hash=None):
    from .adapters.direct_voice import DIRECT_VOICE
    cls=DIRECT_VOICE.get(provider.adapter) if provider.client_correlation else {'newapi_minimax_voice_v1':MiniMaxVoiceAdapter,'newapi_dashscope_voice_v1':DashScopeVoiceAdapter,'newapi_fish_voice_v1':FishVoiceAdapter}.get(provider.adapter)
    if cls is None:raise PlatformError(503,'media_capability_closed')
    return cls(provider,mode,voice_profile(provider),binding_hash=binding_hash,provider_scope_hash=provider_scope(provider),voice_boundary_hash=voice_boundary_hash)


async def owned(runtime,scope,request,provider):
    binding=runtime.bindings[request['model']]
    boundary=voice_boundary(runtime.settings,binding['provider_id'],provider,provider.models[request['model']])
    result=await runtime.voices.resolve(scope,request['voice_id'],protocol=provider.adapter,target_model=provider.models[request['model']],
        binding_hash=binding['binding_hash'],provider_scope_hash=provider_scope(provider),voice_boundary_hash=boundary,
        provider_id=binding['provider_id'],provider_version=provider.version)
    return result,boundary


async def owned_direct(runtime,scope,request,provider):
    """The supplier's id for a voice this account registered through the sibling clone entry."""
    from .adapters.direct_voice import minimax_voice_id
    name=runtime.bindings[request['model']]['provider_id']+'-clone'
    clone=runtime.settings.providers.get(name)
    if clone is None:raise PlatformError(503,'media_capability_closed')
    target=provider.models[request['model']];scope_hash=provider_scope(clone)
    voice=await runtime.voices.resolve(scope,request['voice_id'],protocol=clone.adapter,target_model=target,binding_hash=runtime.bindings[request['model']]['binding_hash'],
        provider_scope_hash=scope_hash,voice_boundary_hash=voice_boundary(runtime.settings,name,clone,target),provider_id=name,provider_version=clone.version)
    if clone.adapter=='newapi_minimax_voice_v1':return minimax_voice_id(voice.provider_reference)
    async with runtime.service.db.connection() as c:
        row=await(await c.execute('SELECT provider_voice_id FROM provider_voice_aliases WHERE provider_scope_hash=%s AND reference=%s',(scope_hash,voice.provider_reference))).fetchone()
    if row is None:raise PlatformError(503,'voice_binding_mismatch')
    return row['provider_voice_id']


class VoiceExecution:
    def __init__(self,runtime):
        self.runtime=runtime
        self.stages=VoiceStages(runtime.service.db,runtime.service.identity,runtime.service)

    @asynccontextmanager
    async def _budget(self,job,*,lifetime,wall):
        # Re-read the original dispatch time even after a restart. The monotonic
        # timeout covers streaming and database awaits, not only socket idleness.
        async with self.runtime.service.db.connection() as c:
            row=await(await c.execute('SELECT dispatched_at FROM media_jobs WHERE id=%s AND attempt_id=%s',
                (job['id'],job['attempt_id']))).fetchone()
        if not row or row['dispatched_at'] is None:raise PlatformError(409,'voice_stage_unknown')
        remaining=(row['dispatched_at']+lifetime-voice_clock.now()).total_seconds()
        if remaining<=0:raise PlatformError(409,'voice_stage_unknown')
        async with asyncio.timeout(min(remaining,wall)):
            yield

    def post_chain_budget(self,job):
        # New POST permits (including POST-based verify) expire after 5 minutes.
        return self._budget(job,lifetime=timedelta(minutes=5),wall=90)

    def recovery_budget(self,job):
        # Original-task GET and already-proved completion retain the original
        # 24-hour recovery window. This never authorizes a new POST permit.
        return self._budget(job,lifetime=timedelta(hours=24),
            wall=min(self.runtime.settings.worker_wall_seconds,90))

    @mutation("job",subject=lambda v:v["job"]["id"],attempt=lambda v:v["job"]["attempt_id"])
    async def stage(self,job,adapter,prepared,refs,previous=None,key=None):
        permit=await self.stages.begin(job['id'],job['attempt_id'],key=key or prepared.stage,protocol=adapter.adapter_id,
            stage=prepared.stage,wire_hash=hashlib.sha256(prepared.payload).hexdigest(),refs=refs,previous_step_id=previous)
        if not permit['dispatch']:raise PlatformError(409,'voice_stage_unknown')
        headers={}
        async def received(**value):
            headers.update(value);await self.stages.record_headers(permit['id'],**value)
        async def task_known(task,sha):
            await self.stages.observe(permit['id'],key='task-known',state='pending',**headers,provider_task_id=task,response_hash=sha)
        try:
            async with self.post_chain_budget(job):
                result=await adapter.submit(self.runtime._client,prepared,request_id=permit['request_id'],max_bytes=1048576,on_headers=received,on_task=task_known)
                if getattr(result,'provider_voice_id',None):
                    async with self.runtime.service.db.connection() as c:
                        await c.execute('INSERT INTO provider_voice_aliases(provider_scope_hash,reference,provider_voice_id) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',
                            (provider_scope(adapter.provider),result.reference.task_id,result.provider_voice_id))
                await self.stages.observe(permit['id'],key='post-result',state=result.state,**headers,
                    provider_task_id=result.reference.task_id,response_hash=result.evidence_hash,facts=result.facts)
                return permit,result,headers
        except BaseException:
            with suppress(Exception):await self.stages.observe(permit['id'],key='post-unknown',state='unknown')
            raise

    @mutation("job",subject=lambda v:v["job"]["id"],attempt=lambda v:v["job"]["attempt_id"])
    async def enroll(self,context,job,adapter,prepared,assets):
        request=job['request'];target=prepared.target_model
        first,result,_=await self.stage(job,adapter,prepared,{'asset_id':assets[0]['id'],'asset_sha256':assets[0]['sha256'],'target_model':target})
        if adapter.adapter_id=='newapi_dashscope_voice_v1' and adapter.provider.client_correlation:
            await self.finish(job,await self.verify(job,adapter),_);return
        if adapter.adapter_id!='newapi_minimax_voice_v1':raise PlatformError(503,'voice_sample_publication_unavailable')
        registration=adapter.prepare_register(request,{'file_task_id':result.reference.task_id,'target_model':target})
        _,result,headers=await self.stage(job,adapter,registration,{'file_task_id':result.reference.task_id,'target_model':target},first['id'])
        await self.finish(job,result,headers)

    @mutation("job",subject=lambda v:v["job"]["id"],attempt=lambda v:v["job"]["attempt_id"])
    async def verify(self,job,adapter):
        async with self.post_chain_budget(job):
            return await self._bounded_verify(job,adapter)

    @mutation("job",subject=lambda v:v["job"]["id"],attempt=lambda v:v["job"]["attempt_id"])
    async def _bounded_verify(self,job,adapter):
        """At most eight distinct query_voice POSTs under the original hold.

        This stage-only entry needs an already proved create stage. The v1
        public enrollment path remains closed without a safe sample producer.
        """
        if adapter.adapter_id!='newapi_dashscope_voice_v1':raise PlatformError(422,'invalid_voice_stage')
        while True:
            async with self.runtime.service.db.connection()as c:
                steps=await self.stages._steps(c,job['id'])
                if not steps or steps[0]['stage']!='create':raise PlatformError(409,'invalid_voice_stage_order')
                latest=await self.stages._latest(c,steps[-1]['id'])
                first=await self.stages._latest(c,steps[0]['id'])
            if latest is None or latest['state']!='completed':raise PlatformError(409,'voice_stage_unknown')
            if steps[-1]['stage']=='verify'and latest['facts'].get('voice_status')=='OK':
                return BridgeVoiceResult(GatewayTaskReference(adapter.adapter_id,latest['provider_task_id']),
                    'completed',latest['response_hash'],latest['facts'])
            if len(steps)>=9:raise PlatformError(409,'voice_stage_limit')
            refs={'voice_task_id':first['provider_task_id'],'target_model':steps[0]['refs']['target_model']}
            await asyncio.sleep(getattr(adapter,'verify_interval',0))
            prepared=adapter.prepare_verify(job['request'],refs)
            await self.stage(job,adapter,prepared,refs,steps[-1]['id'],key='verify-'+str(len(steps)))

    @mutation("job",subject=lambda v:v["job"]["id"],attempt=lambda v:v["job"]["attempt_id"])
    async def finish(self,job,result,headers):
        async with self.recovery_budget(job):
            return await self._bounded_finish(job,result,headers)

    @mutation("job",subject=lambda v:v["job"]["id"],attempt=lambda v:v["job"]["attempt_id"])
    async def _bounded_finish(self,job,result,headers):
        runtime=self.runtime;provider=runtime._provider(job['model'],job['capability'])
        binding=runtime.bindings[job['model']]
        if binding['binding_hash']!=job['binding_hash']:raise PlatformError(503,'media_binding_unavailable')
        boundary=voice_boundary(runtime.settings,binding['provider_id'],provider,provider.models[job['model']])
        voice=await runtime.voices.register_ready(job['id'],job['attempt_id'],voice_boundary_hash=boundary)
        await runtime.service.record_provider_association(job['id'],job['attempt_id'],**headers)
        await runtime.service.record_provider_association(job['id'],job['attempt_id'],provider_response_id=result.reference.task_id)
        await runtime.service.record_terminal_evidence(job['id'],job['attempt_id'],terminal_status='completed',
            provider_response_id=result.reference.task_id,quantities={'registration':1},
            basis_evidence={'verified_delivery_v1':digest({'voice_id':voice.id,'evidence':result.evidence_hash})},evidence_hash=result.evidence_hash)
        await runtime.service.finish_from_evidence(job['id'],job['attempt_id'])

    @mutation({"maintenance","job"})
    async def recover(self):
        # This dedicated stage runner is separate from the M2 GET worker. It may
        # create a NEW stage only after the previous committed result is known;
        # no existing POST permit is ever reused, even after a process restart.
        runtime=self.runtime;scan=current_permit();only=scan.binding.subject_id if scan.binding.kind=='job' else None
        async with runtime.service.db.connection()as c:
            rows=await(await c.execute("SELECT j.*,s.id step_id,s.stage,s.refs FROM media_jobs j JOIN media_voice_steps s ON s.job_id=j.id WHERE j.status IN ('dispatched','pending_reconciliation') AND j.dispatched_at>clock_timestamp()-interval '24 hours' AND s.ordinal=(SELECT max(ordinal) FROM media_voice_steps WHERE job_id=j.id) AND EXISTS(SELECT 1 FROM media_voice_step_observations o WHERE o.step_id=s.id AND o.provider_task_id IS NOT NULL) AND (%s::text IS NULL OR j.id=%s) ORDER BY j.created_at LIMIT 10",(only,only))).fetchall()
        count=0
        for job in rows:
            try:
                permit=await runtime.maintenance.acquire('job',job['id'],job['attempt_id'])
                with operation(permit):
                    async with asyncio.timeout(min(runtime.settings.worker_wall_seconds,90)):
                        await self._recover_job(job)
                    await runtime.maintenance.finish_job_if_stable(permit)
                count+=1
            except asyncio.CancelledError:raise
            except Exception:continue
        return count

    @mutation("job",subject=lambda v:v["job"]["id"],attempt=lambda v:v["job"]["attempt_id"])
    async def _recover_job(self,job):
        async with self.recovery_budget(job):
            return await self._bounded__recover_job(job)

    @mutation("job",subject=lambda v:v["job"]["id"],attempt=lambda v:v["job"]["attempt_id"])
    async def _bounded__recover_job(self,job):
        runtime=self.runtime
        provider=await runtime._frozen_async_provider(job)
        async with runtime.service.db.connection()as c:
            records=await(await c.execute('SELECT * FROM media_voice_step_observations WHERE step_id=%s ORDER BY observation_seq',(job['step_id'],))).fetchall()
        if not records or records[-1]['state']=='rejected':return
        adapter=make_voice_adapter(provider,runtime.settings.mode,binding_hash=job['binding_hash'])
        latest=records[-1]
        if latest['state']=='completed':
            result=BridgeVoiceResult(GatewayTaskReference(provider.adapter,latest['provider_task_id']),'completed',latest['response_hash'],latest['facts'])
            fields={'http_status':latest['http_status'],'http_request_id':latest['http_request_id']}
        else:
            known=next((v for v in records if v['provider_task_id']),None)
            # Each GET has a durable attempt; leave room for headers and result.
            if known is None or len(records)>60:return
            operation='get-'+str(uuid4())
            await self.stages.observe(job['step_id'],key=operation,state='unknown')
            fields={}
            async def headers(**values):
                fields.update(values)
                await self.stages.observe(job['step_id'],key=operation+'-headers',state='headers',**values)
            prepared=adapter._prepared(job['stage'],b'',provider.models[job['model']],target_model=job['refs']['target_model'])
            reference=BridgeVoiceResult(GatewayTaskReference(provider.adapter,known['provider_task_id']),'pending',known['response_hash'])
            result=await adapter.query(runtime._get_client,reference,prepared=prepared,max_bytes=1048576,on_headers=headers)
            await self.stages.observe(job['step_id'],key=operation+'-result',state=result.state,**fields,
                provider_task_id=result.reference.task_id,response_hash=result.evidence_hash,facts=result.facts)
        if result.state!='completed':return
        if job['stage']=='register':
            await self.finish(job,result,fields)
        elif job['stage']=='upload':
            refs={'file_task_id':result.reference.task_id,'target_model':job['refs']['target_model']}
            prepared=adapter.prepare_register(job['request'],refs)
            _,result,fields=await self.stage(job,adapter,prepared,refs,job['step_id'])
            await self.finish(job,result,fields)
