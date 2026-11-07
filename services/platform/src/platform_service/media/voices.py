"""Append-only POST permits for explicit, scoped voice-registration chains.

This module performs no network or credit issuance. Only a newly committed
``dispatch=True`` permits one POST; all replays, missing replies and unknown
stages are read-only. The caller must not restart a chain after an unknown POST.
"""
from ..media_maintenance.runtime import mutation,physical_guard,current_permit,GuardedDatabase,physical_call,operation
from dataclasses import asdict,dataclass,field
from datetime import datetime, timedelta
import re
from uuid import uuid4

from psycopg.types.json import Jsonb

from ..credits import authorize, bounded
from ..errors import PlatformError
from ..identity import now
from .contracts import canonical,hash_value
from .core import scope_of

# Fixed protocol budget: create + eight separate verification POSTs.
MAX_VERIFY_POSTS=8
MAX_CHAIN_POSTS=1+MAX_VERIFY_POSTS

PROTOCOLS=frozenset({'newapi_minimax_voice_v1','newapi_dashscope_voice_v1'})
_TASK=re.compile(r'task_[A-Za-z0-9]{32}\Z',re.ASCII)
_SAFE=re.compile(r'[A-Za-z0-9_.:-]{1,160}\Z',re.ASCII)


def _key(value):
    if not isinstance(value,str) or not _SAFE.fullmatch(value):raise PlatformError(422,'invalid_voice_stage')
    return value


def _task(value):
    if not isinstance(value,str) or not _TASK.fullmatch(value):raise PlatformError(422,'invalid_voice_reference')
    return value


class VoiceStages:
    def __init__(self,db,identity,media):
        self.db,self.identity,self.media=media.db,identity,media

    async def _steps(self,c,job_id):
        return await (await c.execute('SELECT * FROM media_voice_steps WHERE job_id=%s ORDER BY ordinal',(job_id,))).fetchall()

    async def _latest(self,c,step_id):
        return await (await c.execute('SELECT * FROM media_voice_step_observations WHERE step_id=%s ORDER BY observation_seq DESC LIMIT 1',(step_id,))).fetchone()

    async def _refs(self,c,job,steps,protocol,stage,previous_step_id):
        target=job['request']['parameters'].get('target_model')
        _key(target)
        if not steps:
            if previous_step_id is not None or stage!=('upload' if protocol=='newapi_minimax_voice_v1' else 'create'):
                raise PlatformError(409,'invalid_voice_stage_order')
            assets=job['request']['assets']
            if len(assets)!=1:raise PlatformError(422,'invalid_voice_assets')
            asset=await (await c.execute('SELECT sha256 FROM media_assets WHERE id=%s AND invocation_id=%s',(assets[0],job['invocation_id']))).fetchone()
            if not asset:raise PlatformError(404,'media_asset_unavailable')
            return {'asset_id':assets[0],'asset_sha256':asset['sha256'],'target_model':target}
        prior=steps[-1]
        last=await self._latest(c,prior['id'])
        if previous_step_id!=prior['id'] or last is None or last['state']!='completed':
            raise PlatformError(409,'voice_stage_unknown')
        if protocol=='newapi_minimax_voice_v1':
            if len(steps)!=1 or prior['stage']!='upload' or stage!='register':raise PlatformError(409,'invalid_voice_stage_order')
            return {'file_task_id':last['provider_task_id'],'target_model':target}
        if stage!='verify' or len(steps)>=MAX_CHAIN_POSTS:raise PlatformError(409,'voice_stage_limit')
        if prior['stage']=='verify' and last['facts'].get('voice_status')!='DEPLOYING':raise PlatformError(409,'voice_stage_already_terminal')
        first=await self._latest(c,steps[0]['id'])
        if first is None or first['state']!='completed':raise PlatformError(409,'voice_stage_unknown')
        return {'voice_task_id':first['provider_task_id'],'target_model':target}

    @mutation("job",subject="job_id",attempt="attempt_id")
    async def begin(self,job_id,attempt_id,*,key,protocol,stage,wire_hash,refs,previous_step_id=None):
        _key(key);hash_value(wire_hash)
        if protocol not in PROTOCOLS or stage not in {'upload','register','create','verify'} or not isinstance(refs,dict) or len(canonical(refs))>16384:
            raise PlatformError(422,'invalid_voice_stage')
        async with self.db.connection() as c:
            job,scope,_=await self.media._job(c,job_id,attempt_id,live=True)
            await authorize(c,scope)
            if job['capability']!='voice.enroll' or job['model_definition']['adapter']!=protocol:raise PlatformError(403,'voice_stage_scope_mismatch')
            old=await (await c.execute('SELECT * FROM media_voice_steps WHERE job_id=%s AND operation_key=%s',(job_id,key))).fetchone()
            if old:
                if any(old[name]!=value for name,value in {'protocol':protocol,'stage':stage,'wire_hash':wire_hash,'refs':refs,'previous_step_id':previous_step_id}.items()):
                    raise PlatformError(409,'idempotency_conflict')
                return {**old,'dispatch':False}
            # An unknown stage has already transitioned the original hold to
            # pending. It never becomes permission for another paid POST.
            if (job['status'] not in {'dispatched','pending_reconciliation'} or job['dispatched_at'] is None
                    or job['cancel_requested_at'] is not None or job['dispatched_at']+timedelta(minutes=5)<=now()):
                raise PlatformError(409,'voice_stage_unknown')
            enabled=await (await c.execute('SELECT enabled FROM media_model_states WHERE model_id=%s',(job['model'],))).fetchone()
            if not enabled['enabled'] or await (await c.execute('SELECT 1 FROM media_blocked_bindings WHERE binding_hash=%s',(job['binding_hash'],))).fetchone():
                raise PlatformError(503,'media_unavailable')
            invocation=await (await c.execute('SELECT expires_at FROM media_invocations WHERE id=%s',(job['invocation_id'],))).fetchone()
            if invocation['expires_at']<=now():raise PlatformError(410,'invocation_expired')
            await self.media._entitlement(c,scope,job['funding_mode'],job['model_definition']['feature'])
            steps=await self._steps(c,job_id)
            if any(step['protocol']!=protocol for step in steps):raise PlatformError(409,'voice_stage_scope_mismatch')
            expected=await self._refs(c,job,steps,protocol,stage,previous_step_id)
            if refs!=expected:raise PlatformError(409,'voice_stage_reference_mismatch')
            attempt=await (await c.execute('SELECT * FROM media_provider_attempts WHERE job_id=%s',(job_id,))).fetchone()
            if not steps and attempt['provider_request_hash']!=wire_hash:raise PlatformError(409,'voice_stage_request_mismatch')
            row=await (await c.execute('INSERT INTO media_voice_steps(id,job_id,attempt_id,binding_hash,provider_scope_hash,operation_key,ordinal,protocol,stage,wire_hash,request_id,previous_step_id,refs) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',
                (str(uuid4()),job_id,attempt_id,job['binding_hash'],attempt['provider_scope_hash'],key,len(steps),protocol,stage,wire_hash,str(uuid4()),previous_step_id,Jsonb(refs)))).fetchone()
            return {**row,'dispatch':True}

    def _facts(self,step,state,facts,provider_task_id):
        if not isinstance(facts,dict) or len(canonical(facts))>16384:raise PlatformError(422,'invalid_voice_observation')
        if state in {'unknown','pending','headers'}:
            if facts:raise PlatformError(422,'invalid_voice_observation')
            return
        if state=='rejected':
            if set(facts)-{'code','voice_status','target_model','voice_task_id','operation'}:raise PlatformError(422,'invalid_voice_observation')
            if 'operation' in facts and (step['stage']!='verify' or facts['operation']!='query_voice'):raise PlatformError(422,'invalid_voice_observation')
            if 'code' in facts:_key(facts['code'])
            if 'voice_status' in facts and facts['voice_status']!='UNDEPLOYED':raise PlatformError(422,'invalid_voice_observation')
            if 'target_model' in facts and facts['target_model']!=step['refs']['target_model']:raise PlatformError(409,'voice_stage_reference_mismatch')
            if 'voice_task_id' in facts and facts['voice_task_id']!=provider_task_id:raise PlatformError(409,'voice_stage_reference_mismatch')
            return
        stage=step['stage']
        if stage in {'upload','register'}:
            field='file_task_id' if stage=='upload' else 'voice_task_id'
            if facts!={'operation':'upload' if stage=='upload' else 'clone',field:provider_task_id}:raise PlatformError(422,'invalid_voice_observation')
        else:
            expected={'operation':'create_voice' if stage=='create' else 'query_voice','target_model':step['refs']['target_model'],
                      'voice_task_id':provider_task_id,'voice_status':facts.get('voice_status')}
            if facts!=expected or facts['voice_status'] not in ({'UNVERIFIED'} if stage=='create' else {'DEPLOYING','OK'}):raise PlatformError(422,'invalid_voice_observation')

    @mutation("job")
    async def observe(self,step_id,*,key,state,http_status=None,http_request_id=None,provider_task_id=None,response_hash=None,facts=None):
        bounded(step_id);_key(key)
        if state not in {'headers','completed','pending','rejected','unknown'}:raise PlatformError(422,'invalid_voice_observation')
        if http_status is not None and (type(http_status) is not int or not 100<=http_status<=599):raise PlatformError(422,'invalid_voice_observation')
        if http_request_id is not None:_key(http_request_id)
        if provider_task_id is not None:_task(provider_task_id)
        if response_hash is not None:hash_value(response_hash)
        facts={} if facts is None else facts
        if (state=='headers' and (http_status is None or provider_task_id is not None or response_hash is not None)
                or state in {'completed','pending'} and (http_status!=200 or provider_task_id is None or response_hash is None)
                or state=='rejected' and (http_status is None or response_hash is None or not (http_status>=400 or isinstance(facts,dict) and facts.get('voice_status')=='UNDEPLOYED'))):
            raise PlatformError(422,'invalid_voice_observation')
        async with self.db.connection() as c:
            step=await (await c.execute('SELECT * FROM media_voice_steps WHERE id=%s',(step_id,))).fetchone()
            if not step:raise PlatformError(404,'voice_stage_not_found')
            job,_,_=await self.media._job(c,step['job_id'],step['attempt_id'])
            values=dict(state=state,http_status=http_status,http_request_id=http_request_id,provider_task_id=provider_task_id,response_hash=response_hash,facts=facts)
            self._facts(step,state,facts,provider_task_id)
            old=await (await c.execute('SELECT * FROM media_voice_step_observations WHERE step_id=%s AND operation_key=%s',(step_id,key))).fetchone()
            if old:
                if any(old[name]!=value for name,value in values.items()):raise PlatformError(409,'idempotency_conflict')
                return old
            if state in {'completed','pending','rejected'}:
                headers=await (await c.execute("SELECT 1 FROM media_voice_step_observations WHERE step_id=%s AND state='headers' AND http_status=%s AND http_request_id IS NOT DISTINCT FROM %s LIMIT 1",(step_id,http_status,http_request_id))).fetchone()
                if not headers:
                    raise PlatformError(409,'voice_stage_headers_unverified')
            latest=await self._latest(c,step_id)
            if latest and (latest['state'] in {'completed','rejected'} or provider_task_id is not None and latest['provider_task_id'] is not None and provider_task_id!=latest['provider_task_id']):
                raise PlatformError(409,'voice_stage_already_terminal')
            sequence=0 if latest is None else latest['observation_seq']+1
            if sequence>=64:raise PlatformError(409,'voice_stage_limit')
            result=await (await c.execute('INSERT INTO media_voice_step_observations(id,step_id,operation_key,observation_seq,state,http_status,http_request_id,provider_task_id,response_hash,facts) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',
                (str(uuid4()),step_id,key,sequence,state,http_status,http_request_id,provider_task_id,response_hash,Jsonb(facts)))).fetchone()
            if state in {'unknown','rejected'}:await self.media._pending(c,job,'voice_stage_'+state)
            return result

    @mutation("job")
    async def record_headers(self,step_id,*,http_status,http_request_id=None):
        return await self.observe(step_id,key='http-headers',state='headers',http_status=http_status,http_request_id=http_request_id)

    async def snapshot(self,scope,job_id):
        scope=scope_of(scope)
        async with self.db.connection() as c:
            await self.identity.validate_scope(c,scope);await authorize(c,scope)
            await self.media._job(c,job_id,expected_scope=scope)
            steps=await self._steps(c,job_id)
            latest=await self._latest(c,steps[-1]['id']) if steps else None
            return {'job_id':job_id,'stage':steps[-1]['stage'] if steps else None,'stage_count':len(steps),
                    'state':latest['state'] if latest and latest['state']!='headers' else 'unknown'}


@dataclass(frozen=True)
class OwnedVoiceReference:
    id: str
    protocol: str
    target_model: str
    provider_id: str
    provider_version: str
    binding_hash: str
    provider_scope_hash: str
    expires_at: datetime
    provider_reference: str = field(repr=False)
    owner_scope: dict = field(repr=False)
    voice_boundary_hash: str | None = field(default=None,repr=False)


class OwnedVoices:
    """Resolve private bridge resources only through completed owned stages."""
    def __init__(self,db,identity,media):
        self.db,self.identity,self.media=media.db,identity,media

    async def _row(self,c,voice_id=None,job_id=None):
        return await (await c.execute('SELECT v.*,p.protocol,p.target_model,p.binding_hash,p.provider_scope_hash,p.provider_reference,p.final_step_id,p.final_observation_id,p.voice_boundary_hash FROM media_voice_registration_proofs p JOIN media_voices v ON v.id=p.voice_id WHERE (%s::text IS NOT NULL AND p.voice_id=%s) OR (%s::text IS NOT NULL AND p.job_id=%s)',(voice_id,voice_id,job_id,job_id))).fetchone()

    def _reference(self,row):
        return OwnedVoiceReference(row['id'],row['protocol'],row['target_model'],row['provider_id'],row['provider_version'],
            row['binding_hash'],row['provider_scope_hash'],row['expires_at'],row['provider_reference'],row['scope'],row.get('voice_boundary_hash'))

    @mutation("job",subject="job_id",attempt="attempt_id")
    async def register_ready(self,job_id,attempt_id,*,name='',language=None,voice_boundary_hash=None):
        if voice_boundary_hash is not None:hash_value(voice_boundary_hash)
        if not isinstance(name,str) or len(name)>160 or any(ord(c)<32 for c in name):raise PlatformError(422,'invalid_media_voice')
        if language is not None and (not isinstance(language,str) or not re.fullmatch(r'[a-z]{2,3}(?:-[A-Za-z]{2,8})?',language,re.ASCII)):
            raise PlatformError(422,'invalid_media_voice')
        properties={**({'name':name} if name else {}),**({'language':language} if language else {})}
        async with self.db.connection() as c:
            job,scope,_=await self.media._job(c,job_id,attempt_id)
            old=await self._row(c,job_id=job_id)
            if old:
                if old['properties']!=properties or voice_boundary_hash is not None and old['voice_boundary_hash']!=voice_boundary_hash:raise PlatformError(409,'idempotency_conflict')
                return self._reference(old)
            if job['capability']!='voice.enroll' or job['status'] not in {'dispatched','pending_reconciliation'}:raise PlatformError(409,'voice_not_ready')
            step=await (await c.execute('SELECT * FROM media_voice_steps WHERE job_id=%s ORDER BY ordinal DESC LIMIT 1',(job_id,))).fetchone()
            if not step or step['protocol'] not in PROTOCOLS:raise PlatformError(409,'voice_not_ready')
            result=await (await c.execute('SELECT * FROM media_voice_step_observations WHERE step_id=%s ORDER BY observation_seq DESC LIMIT 1',(step['id'],))).fetchone()
            if (not result or result['state']!='completed' or
                not (step['protocol']=='newapi_minimax_voice_v1' and step['stage']=='register'
                     or step['protocol']=='newapi_dashscope_voice_v1' and step['stage']=='verify' and result['facts'].get('voice_status')=='OK')):
                raise PlatformError(409,'voice_not_ready')
            reference=_task(result['provider_task_id']);target=_key(step['refs']['target_model'])
            duplicate=await (await c.execute('SELECT 1 FROM media_voice_registration_proofs WHERE provider_scope_hash=%s AND protocol=%s AND provider_reference=%s',(step['provider_scope_hash'],step['protocol'],reference))).fetchone()
            if duplicate:raise PlatformError(409,'voice_reference_reused')
            definition=job['model_definition'];voice_id=str(uuid4())
            voice=await (await c.execute("INSERT INTO media_voices(id,job_id,scope,user_id,product_id,tenant_id,billing_account_id,provider_id,provider_version,provider_voice_id,properties,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,clock_timestamp()+interval '24 hours') RETURNING *",(voice_id,job_id,Jsonb(asdict(scope)),scope.user_id,scope.product_id,scope.tenant_id,scope.billing_account_id,definition['provider_id'],definition['provider_version'],reference,Jsonb(properties)))).fetchone()
            await c.execute('INSERT INTO media_voice_registration_proofs(voice_id,job_id,attempt_id,binding_hash,provider_scope_hash,protocol,target_model,provider_reference,final_step_id,final_observation_id,voice_boundary_hash) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (voice_id,job_id,attempt_id,step['binding_hash'],step['provider_scope_hash'],step['protocol'],target,reference,step['id'],result['id'],voice_boundary_hash or step['binding_hash']))
            return self._reference({**voice,**{k:step[k] for k in ('protocol','binding_hash','provider_scope_hash')},'target_model':target,'provider_reference':reference,'voice_boundary_hash':voice_boundary_hash or step['binding_hash']})

    async def _authorized(self,c,scope,voice_id):
        scope=scope_of(scope);bounded(voice_id)
        await self.identity.validate_scope(c,scope);await authorize(c,scope)
        row=await self._row(c,voice_id=voice_id)
        if not row or any(row['scope'].get(key)!=getattr(scope,key) for key in ('user_id','product_id','tenant_id','billing_account_id')):
            raise PlatformError(404,'media_voice_unavailable')
        if row['revoked_at'] or row['expires_at']<=now():raise PlatformError(410,'media_voice_expired')
        return row

    async def resolve(self,scope,voice_id,*,protocol,target_model,binding_hash,provider_scope_hash,voice_boundary_hash=None,provider_id=None,provider_version=None):
        hash_value(binding_hash);hash_value(provider_scope_hash);_key(target_model)
        async with self.db.connection() as c:
            row=await self._authorized(c,scope,voice_id)
            expected={'protocol':protocol,'target_model':target_model,'provider_scope_hash':provider_scope_hash}
            if voice_boundary_hash is None:expected['binding_hash']=binding_hash
            else:
                hash_value(voice_boundary_hash)
                expected.update(voice_boundary_hash=voice_boundary_hash,provider_id=provider_id,provider_version=provider_version)
            if any(row[key]!=value for key,value in expected.items()):
                raise PlatformError(403,'voice_binding_mismatch')
            return self._reference(row)

    async def public(self,scope,voice_id):
        async with self.db.connection() as c:
            row=await self._authorized(c,scope,voice_id)
            return {'id':row['id'],'name':row['properties'].get('name',''),'language':row['properties'].get('language'),
                    'expires_at':row['expires_at'].isoformat(),'state':'ready'}
