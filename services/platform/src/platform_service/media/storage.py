"""Database-owned storage reservations and recoverable exact-key deletion."""
from ..media_maintenance.runtime import mutation,physical_guard,current_permit,GuardedDatabase,physical_call,operation
from dataclasses import asdict
import time
from uuid import uuid4
from psycopg.types.json import Jsonb
from ..contracts import Scope
from ..credits import authorize, integer
from ..errors import PlatformError
from ..identity import now
from .contracts import identifier
from ..media_maintenance.contracts import MaintenanceError,require
from ..media_maintenance.permits import _mint_gc_fence,assert_transaction_permit


def scope_of(value):
    return value if isinstance(value, Scope) else Scope(**value)


class StorageLifecycle:
    async def _storage_policy(self, c):
        return await (await c.execute("SELECT * FROM media_storage_policy WHERE id=1")).fetchone()

    async def _storage_quota(self, c, scope, size, files):
        await c.execute("SELECT pg_advisory_xact_lock(hashtextextended('media-storage-v1',0))")
        policy = await self._storage_policy(c)
        usage = await (await c.execute("""SELECT coalesce(sum(bytes),0) bytes,coalesce(sum(files),0) files,
          coalesce(sum(bytes) FILTER(WHERE user_id=%s AND tenant_id=%s AND billing_account_id=%s),0) owner_bytes,
          coalesce(sum(files) FILTER(WHERE user_id=%s AND tenant_id=%s AND billing_account_id=%s),0) owner_files
          FROM (SELECT user_id,tenant_id,billing_account_id,size::numeric bytes,1::bigint files FROM media_blobs WHERE state<>'deleted'
          UNION ALL SELECT user_id,tenant_id,billing_account_id,remaining_bytes::numeric,remaining_files::bigint FROM media_storage_budgets WHERE released_at IS NULL) footprint""",
          (scope.user_id,scope.tenant_id,scope.billing_account_id,scope.user_id,scope.tenant_id,scope.billing_account_id))).fetchone()
        if (usage['bytes']+size>policy['global_max_bytes'] or usage['files']+files>policy['global_max_files']
            or usage['owner_bytes']+size>policy['owner_max_bytes'] or usage['owner_files']+files>policy['owner_max_files']):
            raise PlatformError(413,'media_storage_quota_exceeded')
        return policy

    @mutation("job",subject="job_id")
    async def _reserve_storage_budget(self, c, scope, job_id, definition, request):
        size=definition['limits']['max_artifact_bytes']
        files=request['parameters'].get('n',1) if definition['capability'].startswith('image.') else 32
        if type(files) is not int or not 1<=files<=32:raise PlatformError(422,'invalid_media_parameters')
        await self._storage_quota(c,scope,size,files)
        await c.execute("INSERT INTO media_storage_budgets(job_id,scope,user_id,tenant_id,billing_account_id,max_bytes,max_files,remaining_bytes,remaining_files) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (job_id,Jsonb(asdict(scope)),scope.user_id,scope.tenant_id,scope.billing_account_id,size,files,size,files))

    @mutation("job",subject="job_id")
    async def _release_storage_budget(self, c, job_id):
        await c.execute("SELECT pg_advisory_xact_lock(hashtextextended('media-storage-v1',0))")
        await c.execute("UPDATE media_storage_budgets SET remaining_bytes=0,remaining_files=0,released_at=clock_timestamp() WHERE job_id=%s AND released_at IS NULL",(job_id,))

    @mutation({"job","upload"})
    async def _stage_storage(self,c,scope,metadata,kind,invocation_id,job_id=None,attempt_id=None):
        if kind=='asset':current_permit(kinds={'upload'},subject=invocation_id)
        else:current_permit(kinds={'job'},subject=job_id,attempt=attempt_id)
        await c.execute("SELECT pg_advisory_xact_lock(hashtextextended('media-storage-v1',0))")
        values={key:metadata[key] for key in ('storage_key','sha256','size','mime','properties')}
        owned={**values,'kind':kind,'scope':asdict(scope),'invocation_id':invocation_id,'job_id':job_id,'attempt_id':attempt_id}
        old=await (await c.execute('SELECT * FROM media_blobs WHERE storage_key=%s FOR UPDATE',(metadata['storage_key'],))).fetchone()
        if old:
            if any(old[k]!=v for k,v in owned.items()) or old['state'] not in {'staged','attached'}:
                raise PlatformError(409,'media_storage_conflict')
            permit=current_permit()
            require(await permit._control.blob_writer_owned(c,permit,metadata['storage_key']) is True)
            return old
        budget=None
        if job_id:
            budget=await (await c.execute('SELECT * FROM media_storage_budgets WHERE job_id=%s FOR UPDATE',(job_id,))).fetchone()
        if budget:
            if budget['released_at'] or budget['remaining_bytes']<values['size'] or budget['remaining_files']<1:
                raise PlatformError(413,'media_storage_budget_exceeded')
            await c.execute('UPDATE media_storage_budgets SET remaining_bytes=remaining_bytes-%s,remaining_files=remaining_files-1 WHERE job_id=%s',(values['size'],job_id))
            policy=await self._storage_policy(c)
        else:policy=await self._storage_quota(c,scope,values['size'],1)
        return await (await c.execute("INSERT INTO media_blobs(storage_key,kind,scope,user_id,tenant_id,billing_account_id,invocation_id,job_id,attempt_id,sha256,size,mime,properties,state,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'staged',clock_timestamp()+make_interval(secs=>%s)) RETURNING *",
            (values['storage_key'],kind,Jsonb(asdict(scope)),scope.user_id,scope.tenant_id,scope.billing_account_id,invocation_id,job_id,attempt_id,values['sha256'],values['size'],values['mime'],Jsonb(values['properties']),policy['ttl_seconds']))).fetchone()

    @mutation({"job","upload"})
    async def stage_blob(self,context,metadata,*,kind='asset',job_id=None,attempt_id=None):
        if kind not in {'asset','artifact'}:raise PlatformError(422,'invalid_media_artifact')
        value=self._blob_metadata(metadata,kind);scope=scope_of(context['scope'])
        async with self.db.connection() as c:
            if kind=='asset':
                await self.identity.validate_scope(c,scope);await authorize(c,scope)
                invocation=await self._invocation(c,context)
                if invocation['expires_at']<=now():raise PlatformError(410,'invocation_expired')
                if job_id is not None or attempt_id is not None:raise PlatformError(422,'invalid_media_artifact')
                invocation_id=invocation['id']
            else:
                row,_,_=await self._job(c,job_id,attempt_id,expected_scope=scope)
                if row['status'] not in {'dispatched','pending_reconciliation'}:raise PlatformError(409,'media_not_dispatched')
                if await (await c.execute('SELECT 1 FROM media_terminal_evidence WHERE job_id=%s',(job_id,))).fetchone():raise PlatformError(409,'media_evidence_frozen')
                # Already dispatched outputs belong to the frozen accepted job;
                # logout/revocation blocks new work, not evidence or settlement.
                if (row['invocation_id']!=context['invocation_id'] or row['scope']!=asdict(scope)
                    or any(context.get(key)!=row[key] for key in ('model','capability','funding_mode'))):
                    raise PlatformError(403,'media_scope_mismatch')
                invocation_id=row['invocation_id']
            return await self._stage_storage(c,scope,value,kind,invocation_id,job_id,attempt_id)

    @mutation({"job","upload"})
    async def _attach_storage(self,c,row,kind):
        scope=scope_of(row['scope'])
        staged=await self._stage_storage(c,scope,row,kind,row['invocation_id'],row.get('job_id'),row.get('attempt_id'))
        if staged['state']=='attached':
            if staged['attached_id']!=row['id']:raise PlatformError(409,'media_storage_conflict')
            return
        await c.execute("UPDATE media_blobs SET state='attached',attached_id=%s,expires_at=%s WHERE storage_key=%s",(row['id'],row['expires_at'],row['storage_key']))

    @mutation({'maintenance','gc'})
    async def prepare_gc(self,*,limit=100):
        if not 1<=integer(limit,positive=True)<=1000:raise PlatformError(422,'invalid_limit')
        scan=current_permit()
        async with self.db.connection() as c:
            rows=await(await c.execute("SELECT * FROM media_blobs b WHERE state IN ('staged','attached') AND expires_at<=clock_timestamp() AND NOT media_storage_pinned(b) AND "+self.maintenance.control.gc_idle_sql('storage_key')+" AND (%s::text IS NULL OR storage_key=%s) ORDER BY expires_at,storage_key LIMIT %s",(scan.binding.subject_id if scan.binding.kind=='gc' else None,scan.binding.subject_id,limit))).fetchall()
        prepared=[]
        for candidate in rows:
            try:
                known=[p for p in self.maintenance.permits if p.binding.kind=='gc' and p.binding.subject_id==candidate['storage_key']]
                permit=scan if scan.binding.kind=='gc' else known[0] if known else await self.maintenance.acquire('gc',candidate['storage_key'],str(uuid4()))
                current_permit(permit,kinds={'gc'},subject=candidate['storage_key'])
            except MaintenanceError:continue
            with operation(permit):
                async with self.db.connection() as c:
                    await c.execute("SELECT pg_advisory_xact_lock(hashtextextended('media-storage-v1',0))")
                    row=await(await c.execute("SELECT * FROM media_blobs b WHERE storage_key=%s AND state IN ('staged','attached') AND expires_at<=clock_timestamp() AND NOT media_storage_pinned(b) AND "+self.maintenance.control.gc_idle_sql('storage_key')+" FOR UPDATE SKIP LOCKED",(candidate['storage_key'],))).fetchone()
                    if row is None:continue
                    if row['attached_id']:
                        table='media_assets' if row['kind']=='asset' else 'media_artifacts'
                        await c.execute('SELECT id FROM '+table+' WHERE id=%s FOR UPDATE',(row['attached_id'],))
                        pinned=await(await c.execute('SELECT media_storage_pinned(b) pinned FROM media_blobs b WHERE storage_key=%s',(row['storage_key'],))).fetchone()
                        if pinned['pinned']:continue
                        await c.execute('UPDATE '+table+' SET purged_at=clock_timestamp() WHERE id=%s AND purged_at IS NULL',(row['attached_id'],))
                    await c.execute("UPDATE media_blobs SET state='gc_pending',purged_at=clock_timestamp(),gc_token=%s,gc_lease_until=NULL WHERE storage_key=%s",(permit.binding.attempt_id,row['storage_key']))
                    prepared.append(row['storage_key'])
        return prepared

    @mutation({'maintenance','gc'})
    async def claim_gc(self,*,limit=100):
        if not 1<=integer(limit,positive=True)<=1000:raise PlatformError(422,'invalid_limit')
        scan=current_permit()
        async with self.db.connection() as c:
            rows=await(await c.execute("SELECT * FROM media_blobs WHERE state='gc_pending' AND "+self.maintenance.control.gc_idle_sql('storage_key')+" AND (gc_lease_until IS NULL OR gc_lease_until<=clock_timestamp()) AND (%s::text IS NULL OR storage_key=%s) ORDER BY expires_at,storage_key LIMIT %s",(scan.binding.subject_id if scan.binding.kind=='gc' else None,scan.binding.subject_id,limit))).fetchall()
        claimed=[]
        for candidate in rows:
            known=[p for p in self.maintenance.permits if p.binding.kind=='gc' and p.binding.subject_id==candidate['storage_key']]
            try:
                permit=known[0] if known else await self.maintenance.acquire('gc',candidate['storage_key'],str(uuid4()))
                current_permit(permit,kinds={'gc'},subject=candidate['storage_key'])
            except MaintenanceError:continue
            if permit.physical_count or permit.token_digest in self.maintenance._gc_claimed:continue
            # Save intent before receipt. A lease timeout never mints/reassigns
            # this active/unknown capability, including after DB session loss.
            self.maintenance._gc_claimed.add(permit.token_digest)
            with operation(permit):
                async with self.db.connection() as c:
                    await c.execute("SELECT pg_advisory_xact_lock(hashtextextended('media-storage-v1',0))")
                    row=await(await c.execute("SELECT * FROM media_blobs WHERE storage_key=%s AND state='gc_pending' AND "+self.maintenance.control.gc_idle_sql('storage_key')+" AND (gc_lease_until IS NULL OR gc_lease_until<=clock_timestamp()) FOR UPDATE SKIP LOCKED",(candidate['storage_key'],))).fetchone()
                    if row is None:continue
                    claimed.append(await(await c.execute("UPDATE media_blobs SET gc_token=%s,gc_lease_until=clock_timestamp()+interval '60 seconds' WHERE storage_key=%s RETURNING *",(permit.binding.attempt_id,row['storage_key']))).fetchone())
        for row in claimed:
            permit=self.maintenance.owned('gc',row['storage_key'],row['gc_token'])
            self.maintenance._gc_rows[permit.token_digest]=dict(row)
        return claimed

    @mutation('gc',subject=lambda v:v['claim']['storage_key'],attempt=lambda v:v['claim']['gc_token'])
    async def gc_delete_owned(self,store,claim):
        permit=current_permit(kinds={'gc'},subject=claim['storage_key'],attempt=claim['gc_token']);fence=None;started=False
        try:
            async with self.db.connection() as c:
                await c.execute("SELECT pg_advisory_xact_lock(hashtextextended('media-storage-v1',0))")
                row=await(await c.execute('SELECT * FROM media_blobs WHERE storage_key=%s FOR UPDATE',(claim['storage_key'],))).fetchone()
                require(row is not None and row['state']=='gc_pending' and row['gc_token']==claim['gc_token'])
                keys=('storage_key','kind','scope','invocation_id','job_id','attempt_id','sha256','size','mime','properties','attached_id','expires_at','purged_at')
                require(all(row[k]==claim[k] for k in keys))
                require(await permit._control.gc_writer_idle(c,claim['storage_key']) is True)
                # Fresh DB clock under row ownership, immediately before issuing
                # the physical fence. Conservatively count query latency too.
                before=time.monotonic_ns()
                lease=await(await c.execute("SELECT extract(epoch FROM(gc_lease_until-clock_timestamp())) remaining FROM media_blobs WHERE storage_key=%s AND state='gc_pending' AND gc_token=%s",(claim['storage_key'],claim['gc_token']))).fetchone()
                require(lease is not None and lease['remaining'] is not None and lease['remaining']>0)
                await assert_transaction_permit(c,permit,permit.binding)
                fence=_mint_gc_fence(c,permit,row,before+int(lease['remaining']*1_000_000_000))
                started=True
                await physical_call(store.delete_exact,row,fence=fence,permit=permit,connection=c)
                # Keep the exact same advisory+row ownership through finish.
                result=await(await c.execute("UPDATE media_blobs SET state='deleted',deleted_at=clock_timestamp() WHERE storage_key=%s AND state='gc_pending' AND gc_token=%s RETURNING *",(row['storage_key'],row['gc_token']))).fetchone()
                require(result is not None)
            return result
        except BaseException:
            if started:
                try:await self.maintenance.unknown(permit)
                except Exception:permit.invalidate()
            raise
        finally:
            if fence is not None:fence.close()

    @mutation('gc',subject='storage_key',attempt='gc_token')
    async def finish_gc(self,storage_key,gc_token):
        # A public after-the-fact finish can only observe already deleted state.
        # It cannot convert an unverified/unlinked claim into a success.
        async with self.db.connection() as c:
            row=await(await c.execute('SELECT * FROM media_blobs WHERE storage_key=%s',(storage_key,))).fetchone()
            require(row is not None and row['state']=='deleted' and row['gc_token']==gc_token)
            return row

    async def read_artifact(self,scope,artifact_id,reader):
        scope=scope_of(scope);identifier(artifact_id)
        async with self.db.connection() as c:
            await self.identity.validate_scope(c,scope);await authorize(c,scope)
            row=await (await c.execute('SELECT * FROM media_artifacts WHERE id=%s FOR SHARE',(artifact_id,))).fetchone()
            if not row or any(row[key]!=getattr(scope,key) for key in ('user_id','product_id','tenant_id','billing_account_id')):raise PlatformError(404,'media_artifact_unavailable')
            if row['purged_at'] or row['expires_at']<=now():raise PlatformError(410,'media_artifact_expired')
            return reader(row)
