"""One-attempt media execution over the original shared credit ledger.

Provider I/O and decoding live outside this module. Admission and settlement use
the wallet → invocation → job → reservation → lots lock order used by text.
"""
from ..media_maintenance.runtime import mutation,physical_guard,current_permit,GuardedDatabase,physical_call,operation,optional_permit
from dataclasses import asdict
from datetime import timedelta
from hashlib import sha256
from uuid import uuid4
import re

from psycopg.types.json import Jsonb
from ..contracts import Scope, OperatorIdentity
from ..credits import CreditLedger, authorize, active_plans, bounded, integer
from ..errors import PlatformError
from ..identity import now, opaque
from .contracts import ASYNC_ADAPTERS,validate_definition, validate_request, live_feature_allowed, digest, canonical, identifier, hash_value, MAX_OPERATIONS, MAX_BUDGET
from .rates import cost, quantities as rate_quantities, unicode_scalars
from .storage import StorageLifecycle
from .references import http_reference
from ..internal_features import feature_memberships
from ..pricing import catalog_ready, current_rate, display_multiplier, has_trial, plan_allows, price_record, scale_media, select_price


def scope_of(value):
    return value if isinstance(value, Scope) else Scope(**value)


def same_owner(left, right):
    return all(getattr(left, key)==getattr(right, key) for key in ('user_id','product_id','tenant_id','billing_account_id'))


def operator_id(value):
    if not isinstance(value, OperatorIdentity) or 'media:publish' not in value.capabilities:
        raise PlatformError(403, 'operator_capability_required')
    return value.id


def blob_public(row):
    return {k:(v.isoformat() if hasattr(v,'isoformat') else v) for k,v in row.items()
            if k in {'id','sha256','size','mime','properties','created_at','expires_at','purged_at'}}


def public(row):
    result={key:(str(row[key]) if key in {'amount','actual'} and row[key] is not None else
                 row[key].isoformat() if hasattr(row[key],'isoformat') else row[key])
            for key in ('id','model','capability','execution_state','delivery_state','status','amount','actual','funding_mode','created_at','updated_at')}
    return {**result,'reserved':str(row['amount']),'financial_state':row['status'],'rate_version':row['model_definition']['rate']['version']}


class MediaService(StorageLifecycle):
    def __init__(self, db, identity):
        self.db,self.identity=GuardedDatabase(db),identity;self.ledger=CreditLedger(self.db)

    @mutation("catalog",subject=lambda v:v["definition"]["id"])
    async def publish(self, definition, operator):
        op=operator_id(operator);definition=validate_definition(definition)
        async with self.db.connection() as c:
            await c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('media-model:'+definition['id'],))
            old=await (await c.execute('SELECT definition FROM media_models WHERE id=%s',(definition['id'],))).fetchone()
            if old:
                if old['definition']!=definition:raise PlatformError(409,'media_definition_immutable')
                return definition
            await c.execute('INSERT INTO media_models(id,definition,operator_id) VALUES (%s,%s,%s)',(definition['id'],Jsonb(definition),op))
            await c.execute('INSERT INTO media_model_states(model_id,operator_id) VALUES (%s,%s)',(definition['id'],op))
        return definition

    @mutation("catalog",subject="model")
    async def set_enabled(self, model, enabled, operator):
        op=operator_id(operator);identifier(model)
        if type(enabled) is not bool:raise PlatformError(422,'invalid_enabled')
        async with self.db.connection() as c:
            row=await (await c.execute('UPDATE media_model_states SET enabled=%s,operator_id=%s,updated_at=clock_timestamp() WHERE model_id=%s RETURNING model_id,enabled',(enabled,op,model))).fetchone()
            if not row:raise PlatformError(404,'media_model_not_found')
            return row

    async def _definition(self,c,model,mode,bindings,product,wallet=None):
        row=await (await c.execute('SELECT m.definition,s.enabled FROM media_models m JOIN media_model_states s ON s.model_id=m.id WHERE m.id=%s',(model,))).fetchone()
        binding=bindings.get(model) if isinstance(bindings,dict) else None
        if (mode not in {'test','live'} or not row or not (row['enabled'] or await has_trial(c,'media',model,wallet)) or not isinstance(binding,dict)
                or row['definition']['test_only']!=(mode=='test') or product not in row['definition']['products']):
            raise PlatformError(503,'media_unavailable')
        definition=row['definition']
        if not live_feature_allowed(definition):raise PlatformError(503,'media_unavailable')
        if any(binding.get(k)!=definition[k] for k in ('provider_id','provider_version','adapter','protocol_version')):
            raise PlatformError(503,'media_unavailable')
        hash_value(binding.get('binding_hash'))
        if await (await c.execute('SELECT 1 FROM media_blocked_bindings WHERE binding_hash=%s',(binding['binding_hash'],))).fetchone():
            raise PlatformError(503,'media_binding_blocked')
        return definition,binding

    async def _plans(self,c,scope,mode,definition):
        plans=[p for p in await feature_memberships(c,scope,mode,definition['feature']) if plan_allows(p['definition'],definition['id'])]
        if not plans:raise PlatformError(403,'media_entitlement_required')
        return plans

    async def _entitlement(self,c,scope,mode,definition):
        return max(p['expires_at'] for p in await self._plans(c,scope,mode,definition))

    async def readiness(self,product_id,*,mode,bindings,supported_meters=None,supported_capabilities=None):
        async with self.db.connection() as c:
            for row in await (await c.execute('SELECT id FROM media_models ORDER BY id')).fetchall():
                try:definition,_=await self._definition(c,row['id'],mode,bindings,product_id)
                except PlatformError:continue
                if supported_meters is not None and any((m['unit'],m['basis']) not in supported_meters for m in definition['rate']['meters']):continue
                if supported_capabilities is not None and definition['capability'] not in supported_capabilities.get(row['id'],()):continue
                return True
        return False

    async def catalog(self,scope,*,mode,bindings):
        scope=scope_of(scope);models=[]
        async with self.db.connection() as c:
            await self.identity.validate_scope(c,scope);await authorize(c,scope)
            for row in await (await c.execute('SELECT id FROM media_models ORDER BY id')).fetchall():
                try:
                    d,_=await self._definition(c,row['id'],mode,bindings,scope.product_id,scope.billing_account_id)
                    plans=await self._plans(c,scope,mode,d)
                except PlatformError:continue
                d={**d,'rate':scale_media(await current_rate(c,'media',d['id'],d['rate']),await display_multiplier(c,scope,mode,plans,d['id']))}
                models.append({k:d[k] for k in ('id','name','version','capability','rate','limits','parameters','test_only')}|{'readiness':mode})
        return {'models':models}

    @mutation("mint")
    async def mint(self,scope,*,model,capability,invocation_key,mode,bindings):
        scope=scope_of(scope);identifier(model);bounded(invocation_key)
        async with self.db.connection() as c:
            session=await self.identity.validate_scope(c,scope);await authorize(c,scope)
            old=await (await c.execute('SELECT * FROM media_invocations WHERE session_id=%s AND invocation_key=%s FOR UPDATE',(scope.session_id,invocation_key))).fetchone()
            if old and (old['model'],old['capability'],old['funding_mode'],old['scope'])!=(model,capability,mode,asdict(scope)):
                raise PlatformError(409,'idempotency_conflict')
            d,_=await self._definition(c,model,mode,bindings,scope.product_id)
            if d['capability']!=capability:raise PlatformError(422,'media_capability_mismatch')
            expiry=min(session['expires_at'],await self._entitlement(c,scope,mode,d),now()+timedelta(minutes=30))
            if old:
                if old['expires_at']<=now():raise PlatformError(410,'invocation_expired')
                invocation=old['id'];current_permit(subject=invocation);expiry=min(expiry,old['expires_at'])
            else:
                invocation=current_permit(kinds={'mint'}).binding.subject_id
                await c.execute('INSERT INTO media_invocations(id,scope,session_id,user_id,product_id,tenant_id,billing_account_id,invocation_key,model,capability,funding_mode,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',(invocation,Jsonb(asdict(scope)),scope.session_id,scope.user_id,scope.product_id,scope.tenant_id,scope.billing_account_id,invocation_key,model,capability,mode,expiry))
            token=opaque();token_expiry=min(expiry,now()+timedelta(minutes=5))
            await c.execute('INSERT INTO media_task_tokens(token_hash,invocation_id,expires_at) VALUES (%s,%s,%s)',(sha256(token.encode()).hexdigest(),invocation,token_expiry))
        return dict(task_token=token,expires_at=token_expiry.isoformat(),invocation_id=invocation,model=model,capability=capability,funding_mode=mode,scope=asdict(scope))

    async def authenticate_task(self,token):
        bounded(token)
        async with self.db.connection() as c:
            row=await (await c.execute('SELECT i.*,t.expires_at token_expires,m.definition FROM media_task_tokens t JOIN media_invocations i ON i.id=t.invocation_id JOIN media_models m ON m.id=i.model WHERE t.token_hash=%s',(sha256(token.encode()).hexdigest(),))).fetchone()
            if not row or min(row['expires_at'],row['token_expires'])<=now():raise PlatformError(401,'task_token_invalid')
            scope=scope_of(row['scope']);await self.identity.validate_scope(c,scope)
            return dict(scope=scope,invocation_id=row['id'],model=row['model'],capability=row['capability'],funding_mode=row['funding_mode'],model_limits=row['definition']['limits'])

    async def _invocation(self,c,context):
        scope=scope_of(context['scope'])
        row=await (await c.execute('SELECT * FROM media_invocations WHERE id=%s FOR UPDATE',(context['invocation_id'],))).fetchone()
        if not row or row['scope']!=asdict(scope) or any(row[k]!=context[k] for k in ('model','capability','funding_mode')):
            raise PlatformError(403,'media_scope_mismatch')
        return row

    async def _inputs(self,c,invocation,request,definition,verified_inputs):
        units={'unicode_scalar':unicode_scalars(request['prompt']),'audio_ms':0,'video_ms':0};size=len(canonical(request))
        verified_inputs=verified_inputs or {}
        if not isinstance(verified_inputs,dict) or set(verified_inputs)!=set(request['assets']):raise PlatformError(422,'media_assets_unverified')
        for asset_id in request['assets']:
            asset=await (await c.execute('SELECT * FROM media_assets WHERE id=%s AND invocation_id=%s FOR SHARE',(asset_id,invocation['id']))).fetchone()
            if not asset or asset['purged_at'] or asset['expires_at']<=now():raise PlatformError(404,'media_asset_unavailable')
            expected={key:asset[key] for key in ('sha256','size','mime','properties')}
            if expected!=verified_inputs[asset_id]:raise PlatformError(409,'media_asset_mismatch')
            size+=asset['size']
            for prefix in ('audio','video'):
                if asset['mime'].startswith(prefix+'/'):units[prefix+'_ms']+=integer(asset['properties'].get('duration_ms'),positive=True)
            if definition['capability']=='audio.transcribe' and asset['mime'].startswith('video/'):
                units['audio_ms']+=integer(asset['properties'].get('audio_duration_ms'),positive=True)
        if size>definition['limits']['max_input_bytes']:raise PlatformError(413,'media_input_too_large')
        if request.get('voice_id'):
            voice=await (await c.execute('SELECT * FROM media_voices WHERE id=%s',(request['voice_id'],))).fetchone()
            if not voice or not same_owner(scope_of(voice['scope']),scope_of(invocation['scope'])) or voice['revoked_at'] or voice['expires_at']<=now() or (voice['provider_id'],voice['provider_version'])!=(definition['provider_id'],definition['provider_version']):raise PlatformError(404,'media_voice_unavailable')
        elif definition['capability']=='voice.synthesize':raise PlatformError(422,'media_voice_required')
        for meter in definition['rate']['meters']:
            if meter['basis']=='verified_input_v1' and units[meter['unit']]>definition['limits']['max_units'][meter['unit']]:raise PlatformError(422,'media_input_limit_exceeded')
        return units,{asset_id:verified_inputs[asset_id] for asset_id in request['assets']}

    @mutation("job")
    async def admit(self,context,*,key,request,bindings,verified_inputs=None):
        bounded(key);scope=scope_of(context['scope'])
        async with self.db.connection() as c:
            await self.identity.validate_scope(c,scope);await authorize(c,scope)
            await c.execute('SELECT id FROM billing_accounts WHERE id=%s FOR UPDATE',(scope.billing_account_id,))
            invocation=await self._invocation(c,context)
            definition=(await (await c.execute('SELECT definition FROM media_models WHERE id=%s',(invocation['model'],))).fetchone())['definition']
            request=validate_request(definition,request)
            old=await (await c.execute('SELECT * FROM media_jobs WHERE invocation_id=%s AND operation_key=%s',(invocation['id'],key))).fetchone()
            if old:
                if old['request']!=request:raise PlatformError(409,'idempotency_conflict')
                # Same-key replay is a scoped read; it never dispatches or
                # mutates the already-owned attempt using this new capability.
                return {**old,'financial_state':old['status'],'replay':True}
            if invocation['expires_at']<=now():raise PlatformError(410,'invocation_expired')
            definition,binding=await self._definition(c,invocation['model'],invocation['funding_mode'],bindings,scope.product_id,scope.billing_account_id)
            plans=await self._plans(c,scope,invocation['funding_mode'],definition)
            input_units,input_manifest=await self._inputs(c,invocation,request,definition,verified_inputs)
            request_hash=digest({'request':request,'definition':definition,'binding_hash':binding['binding_hash'],'inputs':input_manifest})
            base=await current_rate(c,'media',definition['id'],definition['rate'])
            factor,amount,memberships=await select_price(c,scope,invocation['funding_mode'],plans,definition['id'],lambda value:cost(scale_media(base,value),definition['limits']['max_units']))
            if invocation['operation_count']>=MAX_OPERATIONS or invocation['held']+invocation['spent']+amount>MAX_BUDGET:raise PlatformError(409,'invocation_budget_exceeded')
            permit=current_permit(kinds={'job'});job,attempt=permit.binding.subject_id,permit.binding.attempt_id
            await self._reserve_storage_budget(c,scope,job,definition,request)
            reservation=await self.ledger.reserve_in_transaction(c,scope,operation='media',key=job,body_hash=request_hash,amount=amount,rate_version=definition['rate']['version'],funding_mode=invocation['funding_mode'],authorize_scope=False,membership_ids=memberships)
            insert="INSERT INTO media_jobs(price,id,invocation_id,operation_key,request_hash,request,input_units,scope,user_id,product_id,tenant_id,billing_account_id,model,capability,funding_mode,model_definition,binding_hash,attempt_id,reservation_id,status,amount,lease_until) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'reserved',%s,clock_timestamp()+interval '60 seconds') RETURNING *"
            values=(Jsonb(price_record(scale_media(base,factor),factor)),job,invocation['id'],key,request_hash,Jsonb(request),Jsonb(input_units),Jsonb(asdict(scope)),scope.user_id,scope.product_id,scope.tenant_id,scope.billing_account_id,definition['id'],definition['capability'],invocation['funding_mode'],Jsonb(definition),binding['binding_hash'],attempt,reservation['id'],amount)
            if not await catalog_ready(c):
                insert,values=insert.replace("media_jobs(price,","media_jobs(",1).replace("VALUES (%s,","VALUES (",1),values[1:]
            row=await (await c.execute(insert,values)).fetchone()
            await c.execute('UPDATE media_invocations SET operation_count=operation_count+1,held=held+%s WHERE id=%s',(amount,invocation['id']))
            return {**row,'financial_state':row['status'],'replay':False}

    async def _job(self,c,job_id,attempt_id=None,*,live=False,expected_scope=None):
        bounded(job_id);row=await (await c.execute('SELECT * FROM media_jobs WHERE id=%s',(job_id,))).fetchone()
        if not row:raise PlatformError(404,'media_not_found')
        if optional_permit() is not None:current_permit(kinds={'job'},subject=row['id'],attempt=row['attempt_id'])
        scope=scope_of(row['scope'])
        if expected_scope is not None and not same_owner(scope,expected_scope):raise PlatformError(404,'media_not_found')
        if live:await self.identity.validate_scope(c,scope)
        await c.execute('SELECT id FROM billing_accounts WHERE id=%s FOR UPDATE',(scope.billing_account_id,))
        await c.execute('SELECT id FROM media_invocations WHERE id=%s FOR UPDATE',(row['invocation_id'],))
        row=await (await c.execute('SELECT * FROM media_jobs WHERE id=%s FOR UPDATE',(job_id,))).fetchone()
        if attempt_id is not None and attempt_id!=row['attempt_id']:raise PlatformError(409,'media_attempt_mismatch')
        reservation=await (await c.execute('SELECT * FROM credit_reservations WHERE id=%s FOR UPDATE',(row['reservation_id'],))).fetchone()
        return row,scope,reservation

    async def _work_fence(self,c,row,claim):
        # Lock ordering is wallet -> invocation -> job/reservation -> work.
        if row['capability']=='voice.enroll' or row['model_definition']['adapter'] not in ASYNC_ADAPTERS:return
        work=await(await c.execute('SELECT * FROM media_work WHERE job_id=%s FOR UPDATE',(row['id'],))).fetchone()
        if (not work or not claim or work['phase']!='settle' or work['lease_token']!=claim.get('lease_token')
            or work['lease_until'] is None or work['lease_until']<=now() or work['deadline']<=now()
            or any(work[k]!=claim.get(k)for k in ('job_id','attempt_id','binding_hash','provider_scope_hash','provider_job_id','adapter'))):
            raise PlatformError(409,'media_work_lease_lost')

    @mutation("job",subject="job_id",attempt="attempt_id")
    async def dispatch(self,job_id,attempt_id):
        async with self.db.connection() as c:
            row,scope,_=await self._job(c,job_id,attempt_id,live=True);await authorize(c,scope)
            if row['status']!='reserved' or row['dispatched_at'] is not None:raise PlatformError(409,'media_already_dispatched')
            if not live_feature_allowed(row['model_definition']):raise PlatformError(503,'media_unavailable')
            inv=await (await c.execute('SELECT expires_at FROM media_invocations WHERE id=%s',(row['invocation_id'],))).fetchone()
            if row['lease_until']<=now() or inv['expires_at']<=now():raise PlatformError(410,'media_lease_expired')
            await self._entitlement(c,scope,row['funding_mode'],row['model_definition'])
            enabled=await (await c.execute('SELECT enabled FROM media_model_states WHERE model_id=%s',(row['model'],))).fetchone()
            if not enabled['enabled'] or await (await c.execute('SELECT 1 FROM media_blocked_bindings WHERE binding_hash=%s',(row['binding_hash'],))).fetchone():raise PlatformError(503,'media_unavailable')
            if not await (await c.execute('SELECT 1 FROM media_provider_attempts WHERE job_id=%s',(job_id,))).fetchone():raise PlatformError(409,'media_attempt_required')
            await c.execute("UPDATE credit_reservations SET status='dispatched',updated_at=clock_timestamp() WHERE id=%s",(row['reservation_id'],))
            return await (await c.execute("UPDATE media_jobs SET status='dispatched',execution_state='running',dispatched_at=clock_timestamp(),lease_until=clock_timestamp()+interval '60 seconds',updated_at=clock_timestamp() WHERE id=%s RETURNING *",(job_id,))).fetchone()

    @mutation("job",subject="job_id",attempt="attempt_id")
    async def heartbeat(self,job_id,attempt_id):
        async with self.db.connection() as c:
            row,_,_=await self._job(c,job_id,attempt_id)
            if row['status']=='dispatched':await c.execute("UPDATE media_jobs SET lease_until=clock_timestamp()+interval '60 seconds' WHERE id=%s",(job_id,))

    @mutation("job",subject="job_id",attempt="attempt_id")
    async def record_provider_attempt(self,job_id,attempt_id,*,provider_request_hash,provider_scope_hash,upstream_model,request_id):
        hash_value(provider_request_hash);hash_value(provider_scope_hash);bounded(upstream_model);bounded(request_id)
        async with self.db.connection() as c:
            row,_,_=await self._job(c,job_id,attempt_id)
            values=dict(attempt_id=attempt_id,binding_hash=row['binding_hash'],provider_request_hash=provider_request_hash,provider_scope_hash=provider_scope_hash,upstream_model=upstream_model,request_id=request_id)
            old=await (await c.execute('SELECT * FROM media_provider_attempts WHERE job_id=%s',(job_id,))).fetchone()
            if old:
                if any(old[k]!=v for k,v in values.items()):raise PlatformError(409,'media_evidence_mismatch')
                return old
            if row['status']!='reserved':raise PlatformError(409,'media_attempt_too_late')
            return await (await c.execute('INSERT INTO media_provider_attempts(job_id,attempt_id,binding_hash,provider_request_hash,provider_scope_hash,upstream_model,request_id) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *',(job_id,attempt_id,row['binding_hash'],provider_request_hash,provider_scope_hash,upstream_model,request_id))).fetchone()

    @mutation("job",subject="job_id",attempt="attempt_id")
    async def record_provider_association(self,job_id,attempt_id,*,http_status=None,http_request_id=None,provider_response_id=None,provider_job_id=None):
        choices=sum(x is not None for x in (http_status,provider_response_id,provider_job_id))
        if choices!=1 or http_request_id is not None and http_status is None:raise PlatformError(422,'invalid_media_evidence')
        if http_status is not None:
            if type(http_status) is not int or not 100<=http_status<=599:raise PlatformError(422,'invalid_media_evidence')
            if http_request_id is not None:bounded(http_request_id)
        for value in (provider_response_id,provider_job_id):
            if value is not None:bounded(value)
        kind='http' if http_status is not None else 'response' if provider_response_id is not None else 'job'
        async with self.db.connection() as c:
            row,_,_=await self._job(c,job_id,attempt_id)
            if row['dispatched_at'] is None:raise PlatformError(409,'media_not_dispatched')
            attempt=await (await c.execute('SELECT * FROM media_provider_attempts WHERE job_id=%s',(job_id,))).fetchone()
            values=dict(attempt_id=attempt_id,binding_hash=row['binding_hash'],provider_scope_hash=attempt['provider_scope_hash'],http_status=http_status,http_request_id=http_request_id,provider_response_id=provider_response_id,provider_job_id=provider_job_id)
            old=await (await c.execute('SELECT * FROM media_provider_associations WHERE job_id=%s AND kind=%s',(job_id,kind))).fetchone()
            if old:
                if any(old[k]!=v for k,v in values.items()):raise PlatformError(409,'media_evidence_mismatch')
                return old
            if kind!='http' and not await (await c.execute("SELECT 1 FROM media_provider_associations WHERE job_id=%s AND kind='http'",(job_id,))).fetchone():raise PlatformError(409,'media_http_evidence_required')
            duplicate=await (await c.execute('SELECT 1 FROM media_provider_associations WHERE provider_scope_hash=%s AND (provider_response_id=%s OR provider_job_id=%s)',(attempt['provider_scope_hash'],provider_response_id,provider_job_id))).fetchone()
            if duplicate:raise PlatformError(409,'media_response_reused')
            return await (await c.execute('INSERT INTO media_provider_associations(job_id,kind,attempt_id,binding_hash,provider_scope_hash,http_status,http_request_id,provider_response_id,provider_job_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',(job_id,kind,attempt_id,row['binding_hash'],attempt['provider_scope_hash'],http_status,http_request_id,provider_response_id,provider_job_id))).fetchone()

    @mutation("job",subject=lambda v:v["job"]["id"],attempt=lambda v:v["job"]["attempt_id"])
    async def _record_protocol_facts(self,c,job,*,response_hash,facts):
        hash_value(response_hash)
        if not isinstance(facts,dict) or len(canonical(facts))>16384:raise PlatformError(422,'invalid_media_usage')
        from .usage_facts import validate_protocol_facts
        validate_protocol_facts(job['model_definition']['adapter'],facts)
        values={'job_id':job['id'],'attempt_id':job['attempt_id'],'binding_hash':job['binding_hash'],
                'namespace':job['model_definition']['adapter'],'response_hash':response_hash,'facts':facts}
        old=await(await c.execute('SELECT * FROM media_protocol_facts WHERE job_id=%s',(job['id'],))).fetchone()
        if old:
            if any(old[k]!=v for k,v in values.items()):raise PlatformError(409,'media_usage_facts_conflict')
            return old
        return await(await c.execute('INSERT INTO media_protocol_facts(job_id,attempt_id,binding_hash,namespace,response_hash,facts) VALUES (%s,%s,%s,%s,%s,%s) RETURNING *',
            (job['id'],job['attempt_id'],job['binding_hash'],values['namespace'],response_hash,Jsonb(facts)))).fetchone()

    @mutation("job",subject="job_id",attempt="attempt_id")
    async def record_protocol_facts(self,job_id,attempt_id,*,response_hash,facts):
        async with self.db.connection()as c:
            job,_,_=await self._job(c,job_id,attempt_id)
            return await self._record_protocol_facts(c,job,response_hash=response_hash,facts=facts)

    @mutation("job",subject="job_id",attempt="attempt_id")
    async def record_http_response_reference(self,job_id,attempt_id,*,namespace,http_request_id,response_body_sha256):
        http_reference(namespace,http_request_id);hash_value(response_body_sha256)
        async with self.db.connection() as c:
            row,_,_=await self._job(c,job_id,attempt_id)
            attempt=await (await c.execute('SELECT * FROM media_provider_attempts WHERE job_id=%s',(job_id,))).fetchone()
            associated=await (await c.execute("SELECT * FROM media_provider_associations WHERE job_id=%s AND kind='http'",(job_id,))).fetchone()
            if (row['dispatched_at'] is None or not attempt or not associated or associated['http_status']!=200
                    or associated['http_request_id']!=http_request_id):
                raise PlatformError(409,'media_response_unverified')
            values=dict(attempt_id=attempt_id,binding_hash=row['binding_hash'],provider_scope_hash=attempt['provider_scope_hash'],
                namespace=namespace,http_request_id=http_request_id,response_body_sha256=response_body_sha256)
            old=await (await c.execute('SELECT * FROM media_http_response_references WHERE job_id=%s',(job_id,))).fetchone()
            if old:
                if any(old[key]!=value for key,value in values.items()):raise PlatformError(409,'media_evidence_mismatch')
                return old
            # Also serialize cross-wallet jobs that share one provider response.
            await c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                (digest(['media-http-reference',attempt['provider_scope_hash'],namespace,http_request_id]),))
            duplicate=await (await c.execute('SELECT 1 FROM media_http_response_references WHERE provider_scope_hash=%s AND namespace=%s AND http_request_id=%s',
                (attempt['provider_scope_hash'],namespace,http_request_id))).fetchone()
            if duplicate:raise PlatformError(409,'media_response_reused')
            return await (await c.execute('INSERT INTO media_http_response_references(job_id,attempt_id,binding_hash,provider_scope_hash,namespace,http_request_id,response_body_sha256) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *',
                (job_id,attempt_id,row['binding_hash'],attempt['provider_scope_hash'],namespace,http_request_id,response_body_sha256))).fetchone()

    async def _verified_quantities(self,c,row,quantities):
        definition=row['model_definition'];values=rate_quantities(definition['rate'],quantities)
        artifacts=await (await c.execute('SELECT * FROM media_artifacts WHERE job_id=%s',(row['id'],))).fetchall()
        for meter in definition['rate']['meters']:
            unit,basis=meter['unit'],meter['basis']
            if basis=='verified_input_v1':expected=row['input_units'][unit]
            elif basis=='verified_delivery_v1':
                if unit=='image':expected=sum(a['mime'].startswith('image/') for a in artifacts)
                elif unit=='audio_clip':expected=sum(a['mime'].startswith('audio/') and a['properties'].get('duration_ms',0)>0 for a in artifacts)
                elif unit in {'audio_ms','video_ms'}:expected=sum(a['properties'].get('duration_ms',0) for a in artifacts if a['mime'].startswith(unit.split('_')[0]+'/'))
                else:expected=(await (await c.execute('SELECT count(*) n FROM media_voices WHERE job_id=%s',(row['id'],))).fetchone())['n']
            else:continue
            if expected!=values[unit]:raise PlatformError(409,'media_quantity_unverified')
        return values

    @mutation("job",subject="job_id",attempt="attempt_id")
    async def record_terminal_evidence(self,job_id,attempt_id,*,terminal_status,provider_response_id,quantities,basis_evidence,evidence_hash,
                                       reference_kind='object_id',http_reference_namespace=None,http_reference_value=None,work_claim=None):
        if terminal_status not in {'completed','failed','cancelled'}:raise PlatformError(422,'invalid_media_evidence')
        hash_value(evidence_hash)
        if reference_kind=='object_id':
            bounded(provider_response_id)
            if http_reference_namespace is not None or http_reference_value is not None:raise PlatformError(422,'invalid_media_reference')
        elif reference_kind=='http_request_id':
            if provider_response_id is not None:raise PlatformError(422,'invalid_media_reference')
            http_reference(http_reference_namespace,http_reference_value)
        else:raise PlatformError(422,'invalid_media_reference')
        async with self.db.connection() as c:
            row,_,_=await self._job(c,job_id,attempt_id)
            await self._work_fence(c,row,work_claim)
            quantities=await self._verified_quantities(c,row,quantities)
            bases={meter['basis'] for meter in row['model_definition']['rate']['meters']}
            if not isinstance(basis_evidence,dict) or set(basis_evidence)!=bases:raise PlatformError(422,'invalid_media_evidence')
            for value in basis_evidence.values():hash_value(value)
            if reference_kind=='object_id':
                verified=await (await c.execute("SELECT 1 FROM media_provider_associations WHERE job_id=%s AND kind='response' AND provider_response_id=%s",(job_id,provider_response_id))).fetchone()
            else:
                verified=await (await c.execute('SELECT 1 FROM media_http_response_references WHERE job_id=%s AND attempt_id=%s AND binding_hash=%s AND namespace=%s AND http_request_id=%s AND response_body_sha256=%s',
                    (job_id,attempt_id,row['binding_hash'],http_reference_namespace,http_reference_value,evidence_hash))).fetchone()
            if row['dispatched_at'] is None or not verified:raise PlatformError(409,'media_response_unverified')
            values=dict(attempt_id=attempt_id,binding_hash=row['binding_hash'],terminal_status=terminal_status,provider_response_id=provider_response_id,quantities=quantities,basis_evidence=basis_evidence,evidence_hash=evidence_hash,
                reference_kind=reference_kind,http_reference_namespace=http_reference_namespace,http_reference_value=http_reference_value)
            old=await (await c.execute('SELECT * FROM media_terminal_evidence WHERE job_id=%s',(job_id,))).fetchone()
            if old:
                if any(old.get(k,'object_id' if k=='reference_kind' else None)!=v for k,v in values.items()):raise PlatformError(409,'media_evidence_mismatch')
                return old
            if reference_kind=='object_id':
                evidence=await (await c.execute('INSERT INTO media_terminal_evidence(job_id,attempt_id,binding_hash,terminal_status,provider_response_id,quantities,basis_evidence,evidence_hash) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',(job_id,attempt_id,row['binding_hash'],terminal_status,provider_response_id,Jsonb(quantities),Jsonb(basis_evidence),evidence_hash))).fetchone()
            else:
                evidence=await (await c.execute('INSERT INTO media_terminal_evidence(job_id,attempt_id,binding_hash,terminal_status,provider_response_id,quantities,basis_evidence,evidence_hash,reference_kind,http_reference_namespace,http_reference_value) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',(job_id,attempt_id,row['binding_hash'],terminal_status,provider_response_id,Jsonb(quantities),Jsonb(basis_evidence),evidence_hash,reference_kind,http_reference_namespace,http_reference_value))).fetchone()
            artifacts=await self._delivery_count(c,job_id)
            await c.execute('UPDATE media_jobs SET execution_state=%s,delivery_state=%s,updated_at=clock_timestamp() WHERE id=%s',(terminal_status,'available' if artifacts else 'unavailable',job_id))
            await self._release_storage_budget(c,job_id)
            return evidence

    @mutation("job",subject=lambda v:v["row"]["id"],attempt=lambda v:v["row"]["attempt_id"])
    async def _pending(self,c,row,reason):
        reason=reason if isinstance(reason,str) and re.fullmatch('[a-z][a-z0-9_]{0,127}',reason) else 'unknown_cost'
        if row['status'] in {'dispatched','pending_reconciliation'}:
            await c.execute("UPDATE credit_reservations SET status='pending_reconciliation',updated_at=clock_timestamp() WHERE id=%s",(row['reservation_id'],))
            row=await (await c.execute("UPDATE media_jobs SET status='pending_reconciliation',execution_state=CASE WHEN execution_state IN ('cancel_requested','completed','failed','cancelled') THEN execution_state ELSE 'unknown' END,reason=%s,updated_at=clock_timestamp() WHERE id=%s RETURNING *",(reason,row['id']))).fetchone()
        elif row['status']!='settled':raise PlatformError(409,'media_not_dispatched')
        return public(row)

    @mutation("job",subject="job_id",attempt="attempt_id")
    async def pending(self,job_id,attempt_id,reason):
        async with self.db.connection() as c:
            row,_,_=await self._job(c,job_id,attempt_id);return await self._pending(c,row,reason)

    @mutation("job",subject="job_id",attempt="attempt_id")
    async def finish(self,job_id,attempt_id,**values):
        await self.record_terminal_evidence(job_id,attempt_id,**values)
        return await self.finish_from_evidence(job_id,attempt_id)

    @mutation("job",subject="job_id",attempt="attempt_id")
    async def finish_from_evidence(self,job_id,attempt_id,*,work_claim=None):
        error=None
        async with self.db.connection() as c:
            row,scope,reservation=await self._job(c,job_id,attempt_id)
            await self._work_fence(c,row,work_claim)
            evidence=await (await c.execute('SELECT * FROM media_terminal_evidence WHERE job_id=%s',(job_id,))).fetchone()
            if not evidence:raise PlatformError(409,'media_terminal_evidence_required')
            if row['status']=='settled':return public(row)
            if row['status'] not in {'dispatched','pending_reconciliation'}:raise PlatformError(409,'media_not_dispatched')
            actual=cost((row.get('price') or {}).get('rate') or row['model_definition']['rate'],evidence['quantities'])
            if actual>row['amount'] or any(n>row['model_definition']['limits']['max_units'][unit] for unit,n in evidence['quantities'].items()):
                result=await self._pending(c,row,'usage_exceeds_reservation')
                await c.execute("INSERT INTO media_blocked_bindings(binding_hash,reason) VALUES (%s,'usage_exceeds_reservation') ON CONFLICT DO NOTHING",(row['binding_hash'],))
                error=PlatformError(409,'settlement_exceeds_reservation')
            else:
                await self.ledger._complete(c,scope,reservation,actual,attempt_id,'settled')
                if evidence.get('reference_kind','object_id')=='object_id':
                    await c.execute('INSERT INTO media_receipts(job_id,attempt_id,quantities,basis_evidence,evidence_hash,terminal_status,provider_response_id,actual) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',(job_id,attempt_id,Jsonb(evidence['quantities']),Jsonb(evidence['basis_evidence']),evidence['evidence_hash'],evidence['terminal_status'],evidence['provider_response_id'],actual))
                else:
                    await c.execute('INSERT INTO media_receipts(job_id,attempt_id,quantities,basis_evidence,evidence_hash,terminal_status,provider_response_id,actual,reference_kind,http_reference_namespace,http_reference_value) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',(job_id,attempt_id,Jsonb(evidence['quantities']),Jsonb(evidence['basis_evidence']),evidence['evidence_hash'],evidence['terminal_status'],evidence['provider_response_id'],actual,evidence['reference_kind'],evidence['http_reference_namespace'],evidence['http_reference_value']))
                await c.execute('UPDATE media_invocations SET held=held-%s,spent=spent+%s WHERE id=%s',(row['amount'],actual,row['invocation_id']))
                artifacts=await self._delivery_count(c,job_id)
                row=await (await c.execute("UPDATE media_jobs SET status='settled',actual=%s,execution_state=%s,delivery_state=%s,updated_at=clock_timestamp() WHERE id=%s RETURNING *",(actual,evidence['terminal_status'],'available' if artifacts else 'unavailable',job_id))).fetchone()
                result=public(row)
        if error:raise error
        return result

    @mutation('job',subject=lambda v:v['row']['id'],attempt=lambda v:v['row']['attempt_id'])
    async def _release(self,c,row,scope,reservation):
        if row['status']!='reserved' or row['dispatched_at'] is not None:raise PlatformError(409,'media_release_forbidden')
        await self.ledger._complete(c,scope,reservation,0,row['attempt_id'],'released')
        await c.execute('UPDATE media_invocations SET held=held-%s WHERE id=%s',(row['amount'],row['invocation_id']))
        released=await (await c.execute("UPDATE media_jobs SET status='released',actual=0,execution_state='cancelled',delivery_state='unavailable',updated_at=clock_timestamp() WHERE id=%s RETURNING *",(row['id'],))).fetchone()
        await self._release_storage_budget(c,row['id'])
        return released

    @mutation("job",subject="job_id",attempt="attempt_id")
    async def cancel(self,scope,job_id):
        scope=scope_of(scope)
        async with self.db.connection() as c:
            await self.identity.validate_scope(c,scope);await authorize(c,scope)
            row,frozen,reservation=await self._job(c,job_id,expected_scope=scope)
            if row['status']=='reserved':return public(await self._release(c,row,frozen,reservation))
            if row['execution_state'] in {'completed','failed','cancelled'}:return public(row)
            if row['status'] in {'dispatched','pending_reconciliation'}:
                if row['execution_state'] not in {'completed','failed','cancelled'}:
                    await c.execute("UPDATE media_jobs SET execution_state='cancel_requested',cancel_requested_at=coalesce(cancel_requested_at,clock_timestamp()) WHERE id=%s",(job_id,))
                    row={**row,'execution_state':'cancel_requested'}
                return await self._pending(c,row,'cancelled_after_dispatch')
            return public(row)

    async def get(self,scope,job_id):
        scope=scope_of(scope)
        async with self.db.connection() as c:
            await self.identity.validate_scope(c,scope);await authorize(c,scope)
            row,_,_=await self._job(c,job_id,expected_scope=scope)
            artifacts=await (await c.execute('SELECT * FROM media_artifacts WHERE job_id=%s ORDER BY created_at,id',(job_id,))).fetchall()
            result={**public(row),'artifacts':[blob_public(a) for a in artifacts]}
            voice=await (await c.execute('SELECT id,properties,expires_at,revoked_at FROM media_voices WHERE job_id=%s',(job_id,))).fetchone()
            if voice:result['voice']={key:(value.isoformat() if hasattr(value,'isoformat') else value) for key,value in voice.items()}
            if row['delivery_state']=='available' and artifacts and all(a['purged_at'] or a['expires_at']<=now() for a in artifacts):result['delivery_state']='expired'
            if voice and (voice['revoked_at'] or voice['expires_at']<=now()):result['delivery_state']='expired'
            return result

    async def _delivery_count(self,c,job_id):
        return (await (await c.execute('SELECT (SELECT count(*) FROM media_artifacts WHERE job_id=%s)+(SELECT count(*) FROM media_voices WHERE job_id=%s) n',(job_id,job_id))).fetchone())['n']

    async def invocation_identity(self,scope,invocation_key):
        scope=scope_of(scope);bounded(invocation_key)
        async with self.db.connection() as c:
            await self.identity.validate_scope(c,scope);await authorize(c,scope)
            row=await(await c.execute('SELECT id FROM media_invocations WHERE session_id=%s AND invocation_key=%s',(scope.session_id,invocation_key))).fetchone()
            return None if row is None else row['id']

    async def lookup_operation(self,scope,invocation_id,operation_key,*,raw=False):
        scope=scope_of(scope);bounded(operation_key)
        async with self.db.connection() as c:
            await self.identity.validate_scope(c,scope);await authorize(c,scope)
            row=await (await c.execute('SELECT * FROM media_jobs WHERE invocation_id=%s AND operation_key=%s',(invocation_id,operation_key))).fetchone()
            if not row or not same_owner(scope_of(row['scope']),scope):raise PlatformError(404,'media_not_found')
            return row if raw else public(row)

    @mutation({"maintenance","job"})
    async def recover_financial(self,*,limit=100):
        if not 1<=integer(limit,positive=True)<=1000:raise PlatformError(422,'invalid_limit')
        scan=current_permit();only=scan.binding.subject_id if scan.binding.kind=='job' else None
        async with self.db.connection() as c:
            rows=await (await c.execute("SELECT j.id,j.attempt_id,e.job_id evidence FROM media_jobs j LEFT JOIN media_terminal_evidence e ON e.job_id=j.id WHERE NOT (e.job_id IS NOT NULL AND j.capability<>'voice.enroll' AND j.model_definition->>'adapter' IN "+repr(ASYNC_ADAPTERS)+") AND j.status IN ('reserved','dispatched','pending_reconciliation') AND (e.job_id IS NOT NULL OR j.lease_until<clock_timestamp()) AND (%s::text IS NULL OR j.id=%s) ORDER BY j.lease_until,j.id LIMIT %s",(only,only,limit))).fetchall()
        result=[]
        for candidate in rows:
            try:
                permit=await self.maintenance.acquire('job',candidate['id'],candidate['attempt_id'])
                with operation(permit):
                    if candidate['evidence']:
                        result.append(await self.finish_from_evidence(candidate['id'],candidate['attempt_id']));continue
                    async with self.db.connection() as c:
                        row,scope,reservation=await self._job(c,candidate['id'],candidate['attempt_id'])
                        if row['lease_until']>now():continue
                        if row['status']=='reserved':result.append(public(await self._release(c,row,scope,reservation)))
                        elif row['status']=='dispatched':
                            if row['capability']!='voice.enroll' and row['model_definition']['adapter'] in ASYNC_ADAPTERS:
                                # A durable original-ID query remains admitted between
                                # successful polls and across a worker restart. Explicit
                                # GET failures already move it to pending atomically.
                                work=await(await c.execute("SELECT 1 FROM media_work WHERE job_id=%s AND phase='query' AND failures=0 AND deadline>clock_timestamp()",(row['id'],))).fetchone()
                                if work:continue
                            result.append(await self._pending(c,row,'lease_expired'))
            except PlatformError:continue
        return result

    def _blob_metadata(self,metadata,kind):
        allowed={'id',kind+'_id','storage_key','sha256','size','mime','properties'}
        if set(metadata)-allowed or not {'storage_key','sha256','size','mime','properties'}.issubset(metadata):raise PlatformError(422,'invalid_media_artifact')
        value=dict(metadata);value['id']=value.pop(kind+'_id',value.get('id',str(uuid4())))
        if 'id' in metadata and kind+'_id' in metadata and metadata['id']!=metadata[kind+'_id']:raise PlatformError(422,'invalid_media_artifact')
        identifier(value['id']);hash_value(value['sha256'])
        if not isinstance(value['storage_key'],str) or not re.fullmatch('(?:[a-f0-9]{32}|[a-f0-9]{64})',value['storage_key']):raise PlatformError(422,'invalid_media_artifact')
        if integer(value['size'],positive=True)>67108864:raise PlatformError(413,'media_artifact_too_large')
        if value['mime'] not in {'image/png','image/jpeg','image/webp','image/gif','video/mp4','video/webm','audio/mpeg','audio/wav','audio/ogg','audio/mp4','text/plain','application/json'}:raise PlatformError(422,'invalid_media_mime')
        properties=value['properties']
        if not isinstance(properties,dict) or set(properties)-{'width','height','duration_ms','audio_duration_ms','format','channels','sample_rate','unicode_scalar'}:raise PlatformError(422,'invalid_media_artifact')
        for key,item in properties.items():
            if key=='format':identifier(item)
            elif integer(item,positive=key!='unicode_scalar')>86400000:raise PlatformError(422,'invalid_media_artifact')
        if value['mime'].startswith('image/') and not {'width','height','format'}.issubset(properties):raise PlatformError(422,'invalid_media_artifact')
        if value['mime'].startswith(('audio/','video/')) and 'duration_ms' not in properties:raise PlatformError(422,'invalid_media_artifact')
        return value

    async def _blob_replay(self,c,table,value,**ownership):
        # Identifiers are server-fixed, never interpolated caller values.
        if table not in {'media_assets','media_artifacts'}:raise ValueError('invalid blob table')
        old=await (await c.execute('SELECT * FROM '+table+' WHERE id=%s',(value['id'],))).fetchone()
        if old:
            if any(old[k]!=v for k,v in {**value,**ownership}.items()):raise PlatformError(409,'media_artifact_conflict')
            return old

    def _artifact_type(self,capability,mime):
        prefix=('image/' if capability.startswith('image.') else 'video/' if capability=='video.generate' else
                'audio/' if capability in {'music.generate','audio.speech','voice.synthesize'} else None)
        if prefix and mime.startswith(prefix):return
        if capability=='audio.transcribe' and mime in {'text/plain','application/json'}:return
        raise PlatformError(422,'media_artifact_type_mismatch')

    @mutation("upload",subject=lambda v:v["context"]["invocation_id"])
    async def register_asset(self,context,**metadata):
        value=self._blob_metadata(metadata,'asset');scope=scope_of(context['scope'])
        async with self.db.connection() as c:
            await self.identity.validate_scope(c,scope);await authorize(c,scope)
            invocation=await self._invocation(c,context)
            if invocation['expires_at']<=now():raise PlatformError(410,'invocation_expired')
            replay=await self._blob_replay(c,'media_assets',value,invocation_id=invocation['id'],scope=asdict(scope))
            if replay:return replay
            definition=(await (await c.execute('SELECT definition FROM media_models WHERE id=%s',(invocation['model'],))).fetchone())['definition']
            existing=await (await c.execute('SELECT coalesce(sum(size),0) size,count(*) n FROM media_assets WHERE invocation_id=%s',(invocation['id'],))).fetchone()
            if existing['n']>=definition['limits']['max_assets'] or existing['size']+value['size']>definition['limits']['max_input_bytes']:raise PlatformError(413,'media_input_too_large')
            await self._stage_storage(c,scope,value,'asset',invocation['id'])
            registered=await (await c.execute("INSERT INTO media_assets(id,invocation_id,scope,user_id,product_id,tenant_id,billing_account_id,storage_key,sha256,size,mime,properties,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,clock_timestamp()+make_interval(secs=>(SELECT ttl_seconds FROM media_storage_policy WHERE id=1))) RETURNING *",(value['id'],invocation['id'],Jsonb(asdict(scope)),scope.user_id,scope.product_id,scope.tenant_id,scope.billing_account_id,value['storage_key'],value['sha256'],value['size'],value['mime'],Jsonb(value['properties'])))).fetchone()
            await self._attach_storage(c,registered,'asset')
            return registered

    async def get_asset(self,context,asset_id):
        scope=scope_of(context['scope']);identifier(asset_id)
        async with self.db.connection() as c:
            await self.identity.validate_scope(c,scope);await authorize(c,scope);invocation=await self._invocation(c,context)
            asset=await (await c.execute('SELECT * FROM media_assets WHERE id=%s AND invocation_id=%s FOR SHARE',(asset_id,invocation['id']))).fetchone()
            if not asset or asset['purged_at'] or asset['expires_at']<=now():raise PlatformError(404,'media_asset_unavailable')
            return asset

    @mutation("job",subject="job_id",attempt="attempt_id")
    async def register_artifact(self,job_id,attempt_id,**metadata):
        value=self._blob_metadata(metadata,'artifact')
        async with self.db.connection() as c:
            row,scope,_=await self._job(c,job_id,attempt_id)
            replay=await self._blob_replay(c,'media_artifacts',value,job_id=job_id,attempt_id=attempt_id,scope=asdict(scope))
            if replay:return replay
            if row['status'] not in {'dispatched','pending_reconciliation'}:raise PlatformError(409,'media_not_dispatched')
            if await (await c.execute('SELECT 1 FROM media_terminal_evidence WHERE job_id=%s',(job_id,))).fetchone():raise PlatformError(409,'media_evidence_frozen')
            self._artifact_type(row['capability'],value['mime'])
            total=await (await c.execute('SELECT coalesce(sum(size),0) size,count(*) n FROM media_artifacts WHERE job_id=%s',(job_id,))).fetchone()
            if total['size']+value['size']>row['model_definition']['limits']['max_artifact_bytes'] or total['n']>=32:raise PlatformError(413,'media_artifact_too_large')
            await self._stage_storage(c,scope,value,'artifact',row['invocation_id'],job_id,attempt_id)
            registered=await (await c.execute("INSERT INTO media_artifacts(id,job_id,invocation_id,attempt_id,scope,user_id,product_id,tenant_id,billing_account_id,storage_key,sha256,size,mime,properties,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,clock_timestamp()+make_interval(secs=>(SELECT ttl_seconds FROM media_storage_policy WHERE id=1))) RETURNING *",(value['id'],job_id,row['invocation_id'],attempt_id,Jsonb(asdict(scope)),scope.user_id,scope.product_id,scope.tenant_id,scope.billing_account_id,value['storage_key'],value['sha256'],value['size'],value['mime'],Jsonb(value['properties'])))).fetchone()
            await self._attach_storage(c,registered,'artifact')
            return registered

    async def get_artifact(self,scope,artifact_id):
        scope=scope_of(scope);identifier(artifact_id)
        async with self.db.connection() as c:
            await self.identity.validate_scope(c,scope);await authorize(c,scope)
            row=await (await c.execute('SELECT * FROM media_artifacts WHERE id=%s',(artifact_id,))).fetchone()
            if not row or not same_owner(scope_of(row['scope']),scope):raise PlatformError(404,'media_artifact_unavailable')
            if row['purged_at'] or row['expires_at']<=now():raise PlatformError(410,'media_artifact_expired')
            return row

    @mutation("job",subject="job_id",attempt="attempt_id")
    async def register_voice(self,job_id,attempt_id,*,provider_voice_id,properties,voice_id=None):
        voice_id=identifier(voice_id or str(uuid4()));bounded(provider_voice_id)
        if (not isinstance(properties,dict) or set(properties)-{'name','language'}
                or any(not isinstance(value,str) or not value or len(value)>160 for value in properties.values())):
            raise PlatformError(422,'invalid_media_voice')
        async with self.db.connection() as c:
            row,scope,_=await self._job(c,job_id,attempt_id)
            if row['capability']!='voice.enroll':raise PlatformError(422,'media_capability_mismatch')
            definition=row['model_definition']
            old=await (await c.execute('SELECT * FROM media_voices WHERE id=%s OR job_id=%s',(voice_id,job_id))).fetchone()
            values=dict(id=voice_id,job_id=job_id,provider_voice_id=provider_voice_id,properties=properties,scope=asdict(scope),provider_id=definition['provider_id'],provider_version=definition['provider_version'])
            if old:
                if any(old[k]!=v for k,v in values.items()):raise PlatformError(409,'media_voice_conflict')
                return old
            if row['status'] not in {'dispatched','pending_reconciliation'}:raise PlatformError(409,'media_not_dispatched')
            if await (await c.execute('SELECT 1 FROM media_terminal_evidence WHERE job_id=%s',(job_id,))).fetchone():raise PlatformError(409,'media_evidence_frozen')
            return await (await c.execute("INSERT INTO media_voices(id,job_id,scope,user_id,product_id,tenant_id,billing_account_id,provider_id,provider_version,provider_voice_id,properties,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,clock_timestamp()+interval '24 hours') RETURNING *",(voice_id,job_id,Jsonb(asdict(scope)),scope.user_id,scope.product_id,scope.tenant_id,scope.billing_account_id,definition['provider_id'],definition['provider_version'],provider_voice_id,Jsonb(properties)))).fetchone()

    async def get_voice(self,scope,voice_id):
        scope=scope_of(scope);identifier(voice_id)
        async with self.db.connection() as c:
            await self.identity.validate_scope(c,scope);await authorize(c,scope)
            row=await (await c.execute('SELECT * FROM media_voices WHERE id=%s',(voice_id,))).fetchone()
            if not row or not same_owner(scope_of(row['scope']),scope):raise PlatformError(404,'media_voice_unavailable')
            if row['revoked_at'] or row['expires_at']<=now():raise PlatformError(410,'media_voice_expired')
            return row
