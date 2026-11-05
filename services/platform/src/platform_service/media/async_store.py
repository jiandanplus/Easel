"""Durable GET work, immutable terminal observations and fenced owned delivery.

Claiming a lease locks only work. Writes acquire the existing wallet/job locks
first, then validate that same lease; no network or decoder runs under DB locks.
"""
from ..media_maintenance.runtime import mutation,physical_guard,current_permit,GuardedDatabase,physical_call,operation,mutation_context
from contextlib import asynccontextmanager
from dataclasses import asdict
import re
import secrets
from uuid import uuid4

from psycopg.types.json import Jsonb

from ..errors import PlatformError
from ..media_maintenance.contracts import MaintenanceError
from ..identity import now
from .async_contracts import Observation
from .contracts import hash_value,identifier


ASYNC_ADAPTERS=('newapi_videos_v1','newapi_suno_v1','newapi_dashscope_music_v1','newapi_incho_v1','newapi_minimax_voice_v1','newapi_dashscope_voice_v1','newapi_fish_voice_v1')


class MediaAsyncStore:
    def __init__(self,service):self.service,self.db=service,service.db

    @mutation("job",subject="job_id",attempt="attempt_id")
    async def enqueue(self,job_id,attempt_id):
        async with self.db.connection()as c:
            job,_,_=await self.service._job(c,job_id,attempt_id)
            association=await(await c.execute("SELECT * FROM media_provider_associations WHERE job_id=%s AND kind='job'",(job_id,))).fetchone()
            if not association or job['dispatched_at']is None or job['model_definition']['adapter']not in ASYNC_ADAPTERS:
                raise PlatformError(409,'media_provider_job_required')
            old=await(await c.execute('SELECT * FROM media_work WHERE job_id=%s',(job_id,))).fetchone()
            if old:return old
            if job['status']not in{'dispatched','pending_reconciliation'}:raise PlatformError(409,'media_not_dispatched')
            return await(await c.execute("INSERT INTO media_work(job_id,attempt_id,binding_hash,provider_scope_hash,provider_job_id,adapter,deadline) VALUES (%s,%s,%s,%s,%s,%s,%s+interval '24 hours') RETURNING *",
                (job_id,attempt_id,job['binding_hash'],association['provider_scope_hash'],association['provider_job_id'],job['model_definition']['adapter'],job['dispatched_at']))).fetchone()

    @mutation({"maintenance","job"})
    async def recover_associations(self,*,limit=100):
        scan=current_permit();only=scan.binding.subject_id if scan.binding.kind=='job' else None
        async with self.db.connection()as c:
            rows=await(await c.execute("SELECT j.id,j.attempt_id FROM media_jobs j JOIN media_provider_associations a ON a.job_id=j.id AND a.kind='job' LEFT JOIN media_work w ON w.job_id=j.id WHERE w.job_id IS NULL AND j.status IN ('dispatched','pending_reconciliation') AND j.model_definition->>'adapter'=ANY(%s) AND (%s::text IS NULL OR j.id=%s) ORDER BY j.created_at,j.id LIMIT %s",(list(ASYNC_ADAPTERS),only,only,limit))).fetchall()
        count=0
        for row in rows:
            try:permit=await self.service.maintenance.acquire('job',row['id'],row['attempt_id'])
            except MaintenanceError:continue
            await self.enqueue(row['id'],row['attempt_id'],permit=permit);count+=1
        return count

    @mutation({"maintenance","job"})
    async def claim(self,*,limit=1):
        if type(limit)is not int or not 1<=limit<=4:raise PlatformError(422,'invalid_limit')
        scan=current_permit();only=scan.binding.subject_id if scan.binding.kind=='job' else None
        async with self.db.connection()as c:
            rows=await(await c.execute("SELECT w.job_id,w.attempt_id FROM media_work w WHERE phase IN ('query','download','settle') AND deadline>clock_timestamp() AND due_at<=clock_timestamp() AND (lease_until IS NULL OR lease_until<=clock_timestamp()) AND polls<10000 AND (%s::text IS NULL OR job_id=%s) ORDER BY due_at,job_id LIMIT %s",(only,only,limit))).fetchall()
        result=[]
        for candidate in rows:
            # Admission/SH flock is obtained before any business/work row lock.
            try:permit=await self.service.maintenance.acquire('job',candidate['job_id'],candidate['attempt_id'])
            except MaintenanceError:continue
            with operation(permit):
                async with self.db.connection()as c:
                    owned=await(await c.execute("SELECT * FROM media_work WHERE job_id=%s AND attempt_id=%s AND phase IN ('query','download','settle') AND deadline>clock_timestamp() AND due_at<=clock_timestamp() AND (lease_until IS NULL OR lease_until<=clock_timestamp()) AND polls<10000 FOR UPDATE SKIP LOCKED",(candidate['job_id'],candidate['attempt_id']))).fetchone()
                    if owned is None:continue
                    result.append(await(await c.execute("UPDATE media_work SET lease_token=%s,lease_until=least(deadline,clock_timestamp()+interval '120 seconds'),polls=polls+1,updated_at=clock_timestamp() WHERE job_id=%s RETURNING *",(str(uuid4()),candidate['job_id']))).fetchone())
        return result

    @mutation("job",subject="job_id")
    async def seed(self,job_id,observation):
        # Best effort immediate terminal snapshot of a synchronous submit result.
        # A competing GET worker may already own this exact ID; it will recover
        # the same host result. This never grants another submission.
        async with self.db.connection()as c:
            claim=await(await c.execute("UPDATE media_work SET lease_token=%s,lease_until=least(deadline,clock_timestamp()+interval '120 seconds'),polls=polls+1 WHERE job_id=%s AND phase='query' AND lease_token IS NULL RETURNING *",(str(uuid4()),job_id))).fetchone()
        if claim is None:return
        try:
            await self.observe(claim,observation)
        finally:
            async with self.db.connection()as c:
                await c.execute('UPDATE media_work SET lease_token=NULL,lease_until=NULL,due_at=clock_timestamp() WHERE job_id=%s AND lease_token=%s',(job_id,claim['lease_token']))

    @mutation("job",subject=lambda v:v["claim"]["job_id"],attempt=lambda v:v["claim"]["attempt_id"])
    async def heartbeat(self,claim):
        # Activity must retain execution admission while an original query is
        # running. Never take work first and then wait on wallet/job locks.
        async with self.owned(claim)as(c,job,scope,work):
            if work['phase']not in {'query','download','settle'}or work['deadline']<=now():raise PlatformError(409,'media_work_lease_lost')
            await c.execute("UPDATE media_work SET lease_until=least(deadline,clock_timestamp()+interval '120 seconds') WHERE job_id=%s",(job['id'],))
            if job['status']=='dispatched':
                await c.execute("UPDATE media_jobs SET lease_until=least(%s,clock_timestamp()+interval '60 seconds') WHERE id=%s",(work['deadline'],job['id']))

    @mutation_context('job',subject=lambda v:v['claim']['job_id'],attempt=lambda v:v['claim']['attempt_id'])
    @asynccontextmanager
    async def owned(self,claim,*,allow_expired=False):
        permit=current_permit(kinds={'job'})
        current_permit(permit,subject=claim['job_id'],attempt=claim['attempt_id'])
        async with self.db.connection()as c:
            job,scope,_=await self.service._job(c,claim['job_id'],claim['attempt_id'])
            work=await(await c.execute('SELECT * FROM media_work WHERE job_id=%s FOR UPDATE',(job['id'],))).fetchone()
            if(not work or work['lease_token']!=claim.get('lease_token')or work['lease_until']is None or (work['lease_until']<=now()and not(allow_expired and work['deadline']<=now()))
               or any(work[k]!=claim.get(k)for k in('attempt_id','binding_hash','provider_scope_hash','provider_job_id','adapter'))):
                raise PlatformError(409,'media_work_lease_lost')
            yield c,job,scope,work

    async def job(self,claim):
        async with self.owned(claim)as(c,job,scope,work):return job

    @mutation("job",subject=lambda v:v["claim"]["job_id"],attempt=lambda v:v["claim"]["attempt_id"])
    async def defer(self,claim,reason,*,phase=None,delay=15):
        reason=reason if isinstance(reason,str)and re.fullmatch(r'[a-z][a-z0-9_]{0,127}',reason)else'media_query_uncertain'
        if phase not in(None,'query','download','settle','manual')or type(delay)is not int or not 1<=delay<=300:raise PlatformError(422,'invalid_media_work')
        async with self.owned(claim,allow_expired=reason=='media_recovery_window_expired')as(c,job,scope,work):
            target='manual'if work['deadline']<=now()or work['failures']>=9999 else phase or work['phase']
            await self.service._pending(c,job,reason)
            await c.execute("UPDATE media_work SET phase=%s,failures=failures+1,last_safe_code=%s,due_at=clock_timestamp()+make_interval(secs=>%s),lease_token=NULL,lease_until=NULL,updated_at=clock_timestamp() WHERE job_id=%s",(target,reason,delay,job['id']))

    @mutation("job",subject=lambda v:v["claim"]["job_id"],attempt=lambda v:v["claim"]["attempt_id"])
    async def observe(self,claim,value):
        if not isinstance(value,Observation)or value.provider_job_id!=claim['provider_job_id']:raise PlatformError(409,'media_observation_mismatch')
        hash_value(value.response_hash)
        if value.usage_hash is not None:hash_value(value.usage_hash)
        if value.state not in{'queued','running','completed','failed','cancelled'}:raise PlatformError(422,'invalid_media_observation')
        if(not isinstance(value.quantities,dict)or any(not isinstance(k,str)or type(v)is not int or not 0<=v<=9223372036854775807 for k,v in value.quantities.items())or len(value.sources)>32):raise PlatformError(422,'invalid_media_observation')
        manifest=[]
        for source in value.sources:
            identifier(source.source_id)
            if(source.kind not in{'content','url'}or not isinstance(source.location,str)or not 1<=len(source.location)<=2048):raise PlatformError(422,'invalid_media_observation')
            manifest.append({'source_id':source.source_id,'kind':source.kind,'mime':source.mime})
        manifest.sort(key=lambda s:s['source_id'])
        if len({s['source_id']for s in manifest})!=len(manifest)or(value.state=='completed'and not manifest):raise PlatformError(422,'invalid_media_observation')
        async with self.owned(claim)as(c,job,scope,work):
            old=await(await c.execute('SELECT * FROM media_observations WHERE job_id=%s',(job['id'],))).fetchone()
            if old:
                if any(old[k]!=v for k,v in{'provider_job_id':value.provider_job_id,'state':value.state,'quantities':value.quantities,'manifest':manifest}.items()):raise PlatformError(409,'media_observation_mismatch')
            elif value.state in{'queued','running'}:
                if job['status']=='dispatched':
                    await c.execute("UPDATE media_jobs SET lease_until=least(%s,clock_timestamp()+interval '60 seconds') WHERE id=%s",(work['deadline'],job['id']))
                await c.execute("UPDATE media_work SET due_at=clock_timestamp()+interval '15 seconds',lease_token=NULL,lease_until=NULL,updated_at=clock_timestamp() WHERE job_id=%s",(job['id'],))
                return None
            else:
                if value.reported_facts:
                    await self.service._record_protocol_facts(c,job,response_hash=value.response_hash,facts=value.reported_facts)
                old=await(await c.execute('INSERT INTO media_observations(job_id,attempt_id,binding_hash,provider_job_id,state,quantities,usage_hash,response_hash,manifest)VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)RETURNING *',
                    (job['id'],job['attempt_id'],job['binding_hash'],value.provider_job_id,value.state,Jsonb(value.quantities),value.usage_hash,value.response_hash,Jsonb(manifest)))).fetchone()
                for source in value.sources:
                    await c.execute('INSERT INTO media_sources(job_id,source_id,kind,location,mime,storage_key,artifact_key)VALUES(%s,%s,%s,%s,%s,%s,%s)',(job['id'],source.source_id,source.kind,source.location,source.mime,secrets.token_hex(32),str(uuid4())))
                await c.execute('UPDATE media_jobs SET execution_state=%s,delivery_state=%s WHERE id=%s',(value.state,'processing'if manifest else'unavailable',job['id']))
                await self.service._pending(c,{**job,'execution_state':value.state},'media_delivery_pending')
            for source in value.sources:
                await c.execute('UPDATE media_sources SET location=%s,updated_at=clock_timestamp()WHERE job_id=%s AND source_id=%s',(source.location,job['id'],source.source_id))
            await c.execute('UPDATE media_work SET phase=%s,last_safe_code=NULL,updated_at=clock_timestamp()WHERE job_id=%s',('download'if manifest else'manual',job['id']))
            return old

    async def sources(self,claim):
        async with self.owned(claim)as(c,job,scope,work):
            return await(await c.execute('SELECT * FROM media_sources WHERE job_id=%s ORDER BY source_id',(job['id'],))).fetchall()

    @mutation("job",subject=lambda v:v["claim"]["job_id"],attempt=lambda v:v["claim"]["attempt_id"])
    async def stage_source(self,claim,source_id,metadata):
        async with self.owned(claim)as(c,job,scope,work):
            source=await(await c.execute('SELECT * FROM media_sources WHERE job_id=%s AND source_id=%s FOR UPDATE',(job['id'],source_id))).fetchone()
            if not source:raise PlatformError(404,'media_source_unavailable')
            value=self.service._blob_metadata({**metadata,'id':source['artifact_key'],'storage_key':source['storage_key']},'artifact')
            if source['metadata']is not None and source['metadata']!=value:raise PlatformError(409,'media_source_changed')
            self.service._artifact_type(job['capability'],value['mime'])
            if work['phase']!='download':raise PlatformError(409,'media_delivery_not_pending')
            await self.service._stage_storage(c,scope,value,'artifact',job['invocation_id'],job['id'],job['attempt_id'])
            await c.execute('UPDATE media_sources SET metadata=%s,updated_at=clock_timestamp() WHERE job_id=%s AND source_id=%s',(Jsonb(value),job['id'],source_id))
            return value

    @mutation("job",subject=lambda v:v["claim"]["job_id"],attempt=lambda v:v["claim"]["attempt_id"])
    async def attach_source(self,claim,source_id):
        async with self.owned(claim)as(c,job,scope,work):
            source=await(await c.execute('SELECT * FROM media_sources WHERE job_id=%s AND source_id=%s FOR UPDATE',(job['id'],source_id))).fetchone()
            if not source or source['metadata']is None:raise PlatformError(409,'media_source_not_staged')
            value=source['metadata']
            replay=await self.service._blob_replay(c,'media_artifacts',value,job_id=job['id'],attempt_id=job['attempt_id'],scope=asdict(scope))
            if replay:return replay
            if work['phase']!='download'or job['status']not in{'dispatched','pending_reconciliation'}:raise PlatformError(409,'media_delivery_not_pending')
            row=await(await c.execute("INSERT INTO media_artifacts(id,job_id,invocation_id,attempt_id,scope,user_id,product_id,tenant_id,billing_account_id,storage_key,sha256,size,mime,properties,expires_at)VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,clock_timestamp()+make_interval(secs=>(SELECT ttl_seconds FROM media_storage_policy WHERE id=1)))RETURNING *",(value['id'],job['id'],job['invocation_id'],job['attempt_id'],Jsonb(asdict(scope)),scope.user_id,scope.product_id,scope.tenant_id,scope.billing_account_id,value['storage_key'],value['sha256'],value['size'],value['mime'],Jsonb(value['properties'])))).fetchone()
            await self.service._attach_storage(c,row,'artifact')
            await c.execute('UPDATE media_sources SET artifact_id=%s,updated_at=clock_timestamp()WHERE job_id=%s AND source_id=%s',(row['id'],job['id'],source_id))
            return row

    @mutation({"maintenance","job"})
    async def expire(self,*,limit=100):
        # Never hold work while waiting on the wallet. Scan IDs then fence each
        # write in the shared funds lock order; expired active leases also stop.
        scan=current_permit();only=scan.binding.subject_id if scan.binding.kind=='job' else None
        async with self.db.connection()as c:
            rows=await(await c.execute("SELECT job_id,attempt_id FROM media_work WHERE phase IN ('query','download','settle') AND (deadline<=%s OR polls>=10000) AND (%s::text IS NULL OR job_id=%s) ORDER BY due_at,job_id LIMIT %s",(now(),only,only,limit))).fetchall()
        count=0
        for row in rows:
            try:permit=await self.service.maintenance.acquire('job',row['job_id'],row['attempt_id'])
            except MaintenanceError:continue
            with operation(permit):
                async with self.db.connection()as c:
                    job,_,_=await self.service._job(c,row['job_id'],row['attempt_id'])
                    work=await(await c.execute('SELECT * FROM media_work WHERE job_id=%s FOR UPDATE',(job['id'],))).fetchone()
                    if work['phase'] not in {'query','download','settle'} or (work['deadline']>now() and work['polls']<10000):continue
                    if job['status']=='settled':
                        await c.execute("UPDATE media_work SET phase='done',lease_token=NULL,lease_until=NULL,updated_at=clock_timestamp() WHERE job_id=%s",(job['id'],));count+=1;continue
                    await self.service._pending(c,job,'media_recovery_window_expired')
                    await c.execute("UPDATE media_work SET phase='manual',lease_token=NULL,lease_until=NULL,last_safe_code='media_recovery_window_expired',updated_at=clock_timestamp() WHERE job_id=%s",(job['id'],));count+=1
        return count

    async def observation(self,claim):
        async with self.owned(claim)as(c,job,scope,work):
            return await(await c.execute('SELECT * FROM media_observations WHERE job_id=%s',(job['id'],))).fetchone()

    @mutation("job",subject=lambda v:v["claim"]["job_id"],attempt=lambda v:v["claim"]["attempt_id"])
    async def prepare_settlement(self,claim):
        async with self.owned(claim)as(c,job,scope,work):
            observation=await(await c.execute('SELECT * FROM media_observations WHERE job_id=%s',(job['id'],))).fetchone()
            missing=await(await c.execute('SELECT 1 FROM media_sources WHERE job_id=%s AND artifact_id IS NULL',(job['id'],))).fetchone()
            if not observation or observation['state']!='completed' or missing:raise PlatformError(409,'media_delivery_not_ready')
            await c.execute("UPDATE media_work SET phase='settle',updated_at=clock_timestamp() WHERE job_id=%s",(job['id'],))
            return observation

    @mutation("job",subject=lambda v:v["claim"]["job_id"],attempt=lambda v:v["claim"]["attempt_id"])
    async def done(self,claim):
        async with self.owned(claim)as(c,job,scope,work):
            if job['status']!='settled':raise PlatformError(409,'media_settlement_pending')
            await c.execute("UPDATE media_work SET phase='done',lease_token=NULL,lease_until=NULL,last_safe_code=NULL,updated_at=clock_timestamp() WHERE job_id=%s",(job['id'],))
