"""Bounded original-ID GET recovery. Generation submission never lives here."""
from ..media_maintenance.runtime import mutation,physical_guard,current_permit,GuardedDatabase,physical_call,operation
import asyncio
import os
import hashlib
import stat
from urllib.parse import urlsplit
from contextlib import suppress
from .adapters.newapi_async import make_async_adapter
from .async_contracts import ArtifactSource
from .av_artifacts import inspect_av
from .contracts import digest,media_resources_ready
from ..errors import PlatformError
from ..identity import now


def check_download_path(provider,location):
    path=urlsplit(location).path
    if ('%'in path or '//'in path or any(part in {'.','..'}for part in path.split('/'))
        or not any(path.startswith(prefix)for prefix in provider.download_path_prefixes)):
        raise PlatformError(502,'media_download_path_denied')


class MediaWorker:
    def __init__(self,queue,store,client,settings,provider):
        self.queue,self.service,self.store=queue,queue.service,store
        self.client,self.settings,self.provider=client,settings,provider

    @physical_guard
    def _write_source(self,raw,metadata,*,fence=None):
        from ..media_maintenance.permits import WriteFence
        from ..media_maintenance.contracts import require
        require(type(fence) is WriteFence);fence.verify(metadata)
        require(type(raw) is bytes and len(raw)==metadata['size'] and hashlib.sha256(raw).hexdigest()==metadata['sha256'])
        self.store.check_capacity(len(raw))
        key=metadata['storage_key'];temporary=key+'.part'
        with self.store._directory(create=True)as directory:
            try:
                descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
            except FileExistsError:
                self._promote_partial(metadata,fence=fence)
                return
            try:
                with os.fdopen(descriptor,'wb')as stream:
                    owned=os.fstat(stream.fileno());stream.write(raw);stream.flush();os.fsync(stream.fileno())
                try:os.link(temporary,key,src_dir_fd=directory,dst_dir_fd=directory,follow_symlinks=False)
                except FileExistsError:self.store.read(metadata)
                os.fsync(directory)
                # Only this writer's successfully linked partial can be removed.
                current=os.stat(temporary,dir_fd=directory,follow_symlinks=False)
                if(current.st_dev,current.st_ino)!=(owned.st_dev,owned.st_ino):raise PlatformError(503,'media_storage_partial_pending')
                os.unlink(temporary,dir_fd=directory);os.fsync(directory)
            except OSError:raise PlatformError(503,'media_storage_partial_pending')from None

    @physical_guard
    def _promote_partial(self,metadata,*,fence=None):
        from ..media_maintenance.permits import WriteFence
        from ..media_maintenance.contracts import require
        require(type(fence) is WriteFence);fence.verify(metadata)
        key=metadata['storage_key'];temporary=key+'.part'
        with self.store._directory()as directory:
            try:
                descriptor=os.open(temporary,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
                with os.fdopen(descriptor,'rb')as stream:
                    owned=os.fstat(stream.fileno())
                    if (not stat.S_ISREG(owned.st_mode)or owned.st_uid!=os.getuid()or stat.S_IMODE(owned.st_mode)!=0o600 or owned.st_size!=metadata['size']):raise ValueError
                    raw=stream.read(metadata['size']+1)
                    if len(raw)!=metadata['size']or hashlib.sha256(raw).hexdigest()!=metadata['sha256']:raise ValueError
                    os.fsync(stream.fileno())
                try:os.link(temporary,key,src_dir_fd=directory,dst_dir_fd=directory,follow_symlinks=False)
                except FileExistsError:self.store.read(metadata)
                os.fsync(directory)
                current=os.stat(temporary,dir_fd=directory,follow_symlinks=False)
                if(current.st_dev,current.st_ino)!=(owned.st_dev,owned.st_ino):raise ValueError
                os.unlink(temporary,dir_fd=directory);os.fsync(directory)
            except(OSError,ValueError):raise PlatformError(503,'media_storage_partial_pending')from None

    @mutation({"maintenance","job"})
    async def run_once(self):
        await self.queue.recover_associations()
        await self.queue.expire()
        claims=await self.queue.claim(limit=self.settings.download_concurrency)
        await asyncio.gather(*(self.execute(claim,permit=self.service.maintenance.owned('job',claim['job_id'],claim['attempt_id']))for claim in claims))
        return len(claims)

    @mutation('job',subject=lambda v:v['claim']['job_id'],attempt=lambda v:v['claim']['attempt_id'])
    async def _heartbeat(self,claim):
        while True:
            await asyncio.sleep(10)
            await self.queue.heartbeat(claim)

    @mutation("job",subject=lambda v:v["claim"]["job_id"],attempt=lambda v:v["claim"]["attempt_id"])
    async def execute(self,claim):
        remaining=(claim['deadline']-now()).total_seconds()
        ceiling=getattr(self.settings,'worker_wall_seconds',120)
        try:
            async with asyncio.timeout(min(ceiling,max(0,remaining))):
                await self._execute_claim(claim)
        except TimeoutError:
            expired=claim['deadline']<=now()
            with suppress(Exception):
                await self.queue.defer(claim,'media_recovery_window_expired'if expired else'media_work_wall_timeout',
                    phase='manual'if expired else None)

    @mutation("job",subject=lambda v:v["claim"]["job_id"],attempt=lambda v:v["claim"]["attempt_id"])
    async def _execute_claim(self,claim):
        beat=asyncio.create_task(self._heartbeat(claim))
        phase=claim['phase']
        try:
            job=await self.queue.job(claim)
            provider=await self.provider(job)
            adapter=make_async_adapter(provider,self.settings.mode,job)
            if not media_resources_ready(job['model_definition']):raise PlatformError(503,'media_resource_policy_required')
            spec=job['model_definition']['limits']['output_media']
            observation=await self.queue.observation(claim)
            if phase=='query':
                value=await adapter.query(self.client,claim['provider_job_id'],max_bytes=self.settings.max_response_bytes)
                observation=await self.queue.observe(claim,value)
                if observation is None:return
            if observation['state']!='completed':
                # Failure/cancellation alone is no evidence of zero supplier cost.
                await self.queue.defer(claim,'media_cost_unverified',phase='manual');return
            sources=await self.queue.sources(claim)
            if not 1<=len(sources)<=spec['max_files']:raise PlatformError(502,'media_output_limit')
            ceiling=min(job['model_definition']['limits']['max_artifact_bytes'],self.settings.max_asset_bytes)
            total=0;artifacts=[]
            for source in sources:
                if source['artifact_id'] is None:
                    phase='download'
                    stored=source['metadata']
                    if stored is not None:
                        try:self.store.read(stored)
                        except PlatformError:
                            partial=self.store.root/(stored['storage_key']+'.part')
                            if partial.exists()or partial.is_symlink():
                                await physical_call(self._promote_partial,stored)
                                self.store.read(stored)
                            else:stored=None
                    if stored is None:
                        descriptor=ArtifactSource(source['source_id'],source['kind'],source['location'],source['mime'])
                        if descriptor.kind=='url' and provider.download_path_prefixes:check_download_path(provider,descriptor.location)
                        raw,mime=await adapter.retrieve(self.client,descriptor,max_bytes=ceiling)
                        if mime not in spec['mime_types']:raise PlatformError(502,'media_output_mime_denied')
                        info=await physical_call(inspect_av,raw,kind='video'if job['capability']=='video.generate'else'audio',mime=mime,
                            max_bytes=ceiling,max_duration_ms=spec['max_duration_ms'],max_pixels=min(spec.get('max_pixels',self.settings.max_pixels),self.settings.max_pixels),
                            require_audio=spec['require_audio'],max_channels=spec['max_channels'],max_sample_rate=spec['max_sample_rate'],tools=self.settings.av_tools)
                        if total+info['size']>ceiling:raise PlatformError(502,'media_output_limit')
                        stored=await self.queue.stage_source(claim,source['source_id'],info)
                        # Stable source storage identity survives DB/file commit loss.
                        # Complete owned files are verified above; partials are never
                        # silently discarded or regenerated.
                        await physical_call(self._write_source,raw,stored)
                    if total+stored['size']>ceiling:raise PlatformError(502,'media_output_limit')
                    artifact=await self.queue.attach_source(claim,source['source_id'])
                else:
                    artifact=source['metadata']
                total+=artifact['size'];artifacts.append(artifact)
                if total>ceiling:raise PlatformError(502,'media_output_limit')
            observation=await self.queue.prepare_settlement(claim);phase='settle'
            await self.service.record_provider_association(job['id'],job['attempt_id'],provider_response_id=claim['provider_job_id'])
            measured={'video_ms':sum(a['properties']['duration_ms'] for a in artifacts if a['mime'].startswith('video/')),
                      'audio_ms':sum(a['properties']['duration_ms'] for a in artifacts if a['mime'].startswith('audio/')),
                      'audio_clip':sum(a['mime'].startswith('audio/')for a in artifacts)}
            quantities={};bases={}
            for meter in job['model_definition']['rate']['meters']:
                unit,basis=meter['unit'],meter['basis']
                if basis=='verified_delivery_v1':
                    if unit not in measured:raise PlatformError(502,'media_metering_unsupported')
                    quantities[unit]=measured[unit]
                    bases[basis]=digest([{k:a[k]for k in ('id','sha256','size','mime','properties')}for a in artifacts])
                elif basis=='provider_usage_v1':
                    if observation['usage_hash'] is None or unit not in observation['quantities']:raise PlatformError(502,'media_usage_unverified')
                    quantities[unit]=observation['quantities'][unit];bases[basis]=observation['usage_hash']
                elif basis=='verified_input_v1':
                    if unit!='unicode_scalar':raise PlatformError(502,'media_metering_unsupported')
                    quantities[unit]=job['input_units'][unit];bases[basis]=digest({'request_hash':job['request_hash'],'input_units':job['input_units']})
                else:raise PlatformError(502,'media_metering_unsupported')
            await self.service.record_terminal_evidence(job['id'],job['attempt_id'],terminal_status=observation['state'],
                provider_response_id=claim['provider_job_id'],quantities=quantities,basis_evidence=bases,evidence_hash=observation['response_hash'],work_claim=claim)
            await self.service.finish_from_evidence(job['id'],job['attempt_id'],work_claim=claim)
            await self.queue.done(claim)
        except asyncio.CancelledError:raise
        except Exception as error:
            with suppress(Exception):
                await self.queue.defer(claim,getattr(error,'code','media_recovery_uncertain'),phase='query'if phase=='download'else phase)
        finally:
            beat.cancel();await asyncio.gather(beat,return_exceptions=True)
            await self.service.maintenance.finish_job_if_stable(current_permit(kinds={'job'}))
