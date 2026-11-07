"""Explicit New API image gateway references; synthetic responses, real PostgreSQL."""
import base64
from dataclasses import replace
import hashlib
import io
import json

import httpx
from PIL import Image
import psycopg
import pytest
from b2_legacy_media_fixtures import legacy_media, legacy_db as db, legacy_dsn as test_dsn
from psycopg.types.json import Jsonb

from platform_service.errors import PlatformError
from platform_service.media_config import MediaProvider, MediaSettings
from platform_service.media.adapters.openai_images import OpenAIImagesAdapter
from platform_service.media_runtime import MediaRuntime
from test_media_contracts import definition
from test_media_credits import setup, admit, artifact

NAMESPACE = 'newapi_x_oneapi_request_id_v1'
GATEWAY_ID = '202610270212247285164708268d9d6hP1gm1CF'
FORWARDED_ID = '016f6b9b-d7ff-4b3f-a10c-24ad3632ba2d'


def provider(**patch):
    values = dict(version='1', base_url='http://127.0.0.1:9999/v1', secret='synthetic-only',
                  models={'fixture-image':'upstream'}, capabilities=('image.generate',),
                  response_profile='newapi_images_v1')
    return MediaProvider(**(values | patch))


def settings(tmp_path, *, source_root=None, **patch):
    return MediaSettings(mode='test', storage_root=source_root if source_root is not None else tmp_path/'media', providers={'fixture':provider(**patch)})


def png(size=(1024,1024)):
    stream=io.BytesIO(); Image.new('RGB',size,(92,115,140)).save(stream,format='PNG'); return stream.getvalue()


def request():
    return {'model':'fixture-image','capability':'image.generate','prompt':'Synthetic controlled image',
            'assets':[],'parameters':{'n':1,'size':'1024x1024'}}


def test_response_profile_is_explicit_and_changes_only_new_binding(tmp_path):
    assert 'response_profile' in MediaProvider.__dataclass_fields__
    legacy=MediaProvider('1','http://127.0.0.1:9999/v1','synthetic-only',{'fixture-image':'upstream'},capabilities=('image.generate',))
    old=MediaSettings(mode='test',storage_root=tmp_path/'old',providers={'fixture':legacy})
    frozen=['test','fixture','1',legacy.base_url,hashlib.sha256(legacy.secret.encode()).hexdigest(),
            'fixture-image','upstream','openai_images_v1','1',['image.generate'],[],False,None]
    assert old.bindings['fixture-image']['binding_hash']==hashlib.sha256(json.dumps(frozen,separators=(',',':')).encode()).hexdigest()
    assert settings(tmp_path).bindings!=old.bindings
    for patch in ({'response_profile':'guessed'}, {'capabilities':('image.edit',)}, {'response_format':'url'}):
        with pytest.raises(ValueError): settings(tmp_path/'invalid',**patch)


@pytest.mark.asyncio
async def test_newapi_profile_persists_gateway_header_namespace_without_fabricating_object_or_usage():
    adapter=OpenAIImagesAdapter(provider(),'test')
    prepared=adapter.prepare(request(),[])
    observed=[]
    value={'created':1793067144,'data':[{'url':'http://127.0.0.1:9998/original.png'}]}
    raw=json.dumps(value,separators=(',',':')).encode()
    def handler(req): return httpx.Response(200,content=raw,headers={
        'content-type':'application/json','x-oneapi-request-id':GATEWAY_ID,'x-request-id':FORWARDED_ID})
    async def headers(**values): observed.append(values)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result=await adapter.submit(client,prepared,request_id='local-attempt',max_bytes=65536,on_headers=headers)
    assert result.response_id is None
    assert result.correlation.namespace==NAMESPACE and result.correlation.value==GATEWAY_ID
    assert result.evidence_hash==hashlib.sha256(raw).hexdigest()
    assert result.quantities=={} and result.usage_hash is None
    assert observed==[{'http_status':200,'http_request_id':GATEWAY_ID}]


@pytest.mark.asyncio
@pytest.mark.parametrize('header,patch',[
    ({'x-request-id':FORWARDED_ID},{}),
    ({'x-oneapi-request-id':FORWARDED_ID},{}),
    ({'x-oneapi-request-id':GATEWAY_ID},{'id':'made-up-object','created':None}),
])
async def test_gateway_profile_has_no_uuid_bodyid_or_created_fallback(header,patch):
    adapter=OpenAIImagesAdapter(provider(),'test'); observed=[]
    value={'created':1793067144,'data':[{'b64_json':base64.b64encode(png()).decode()}]} | patch
    async def headers(**values):observed.append(values)
    def handler(req):return httpx.Response(200,json=value,headers=header)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PlatformError):await adapter.submit(client,adapter.prepare(request(),[]),request_id='local',max_bytes=65536,on_headers=headers)
    assert all('provider_response_id' not in values for values in observed)


@pytest.mark.asyncio
async def test_real_pg_newapi_url_decode_fixed_delivery_receipt_is_typed_and_replay_never_posts(db,tmp_path,legacy_media):
    service,ledger,scope,*_=await legacy_media.setup(db)
    configured=settings(tmp_path,source_root=legacy_media.source_path,download_origins=('http://127.0.0.1:9998',))
    token=await service.mint(scope,model='fixture-image',capability='image.generate',invocation_key='typed-runtime',mode='test',bindings=configured.bindings)
    calls=[]
    response_raw=json.dumps({'created':1793067144,'data':[{'url':'http://127.0.0.1:9998/image.png'}]},separators=(',',':')).encode()
    def handler(req):
        calls.append(req)
        if req.method=='POST':return httpx.Response(200,content=response_raw,headers={'content-type':'application/json','x-oneapi-request-id':GATEWAY_ID,'x-request-id':FORWARDED_ID})
        assert req.headers.get('authorization') is None
        return httpx.Response(200,content=png(),headers={'content-type':'image/png'})
    runtime=MediaRuntime(db,service.identity,configured,service=service.raw,maintenance=legacy_media.generation,transport=httpx.MockTransport(handler))
    await legacy_media.start_runtime(runtime)
    try:
        result=await runtime.submit(token['task_token'],'one',request())
        assert result['financial_state']=='settled' and result['actual']=='2'
        assert len(result['artifacts'])==1 and result['artifacts'][0]['properties']=={'width':1024,'height':1024,'format':'PNG'}
        assert (await runtime.artifact(scope,result['artifacts'][0]['id'],content=True))[0]==png()
        assert (await runtime.submit(token['task_token'],'one',request()))['id']==result['id']
        assert [r.method for r in calls]==['POST','GET']
        async with db.connection() as c:
            ref=await (await c.execute('SELECT * FROM media_http_response_references WHERE job_id=%s',(result['id'],))).fetchone()
            terminal=await (await c.execute('SELECT * FROM media_terminal_evidence WHERE job_id=%s',(result['id'],))).fetchone()
            receipt=await (await c.execute('SELECT * FROM media_receipts WHERE job_id=%s',(result['id'],))).fetchone()
            assert ref['namespace']==NAMESPACE and ref['http_request_id']==GATEWAY_ID
            assert ref['response_body_sha256']==hashlib.sha256(response_raw).hexdigest()
            for row in (terminal,receipt):
                assert row['provider_response_id'] is None and row['reference_kind']=='http_request_id'
                assert row['http_reference_namespace']==NAMESPACE and row['http_reference_value']==GATEWAY_ID
                assert row['quantities']=={'image':1} and set(row['basis_evidence'])=={'verified_delivery_v1'}
            assert (await (await c.execute("SELECT count(*) n FROM media_provider_associations WHERE job_id=%s AND kind='response'",(result['id'],))).fetchone())['n']==0
        assert (await ledger.summary(scope))['spent']=='2'
    finally:await runtime.close()


@pytest.mark.asyncio
async def test_gateway_no_usage_profile_closes_token_meters_before_mint_dispatch_or_funding(db,tmp_path,legacy_media):
    spec=definition();spec['rate']={'version':'unknown-tokens','meters':[{'unit':'input_token','basis':'provider_usage_v1','numerator':'1','denominator':'1'}]};spec['limits']['max_units']={'input_token':8}
    service,ledger,scope,*_=await legacy_media.setup(db,media_definition=spec)
    calls=[]
    runtime=MediaRuntime(db,service.identity,settings(tmp_path,source_root=legacy_media.source_path),service=service.raw,maintenance=legacy_media.generation,transport=httpx.MockTransport(lambda req:calls.append(req)))
    await legacy_media.start_runtime(runtime)
    try:
        assert (await runtime.capabilities('easel'))['test_media_execution_enabled'] is False
        assert await runtime.catalog(scope)=={'models':[]}
        with pytest.raises(PlatformError):await runtime.mint(scope,{'model':'fixture-image','capability':'image.generate','invocation_key':'closed'})
        assert not calls and (await ledger.summary(scope))['reserved']=='0'
    finally:await runtime.close()


async def dispatched(db,legacy_media,key='typed',gateway_id=GATEWAY_ID):
    service,ledger,scope,ctx,*_=await legacy_media.setup(db)
    assert callable(getattr(service,'record_http_response_reference',None))
    job=await admit(service,ctx,key)
    await service.record_provider_attempt(job['id'],job['attempt_id'],provider_request_hash='b'*64,provider_scope_hash='c'*64,upstream_model='fixture',request_id='local-'+job['id'])
    await service.dispatch(job['id'],job['attempt_id'])
    await service.record_provider_association(job['id'],job['attempt_id'],http_status=200,http_request_id=gateway_id)
    return service,ledger,scope,ctx,job


@pytest.mark.asyncio
async def test_typed_reference_is_immutable_and_rejects_wrong_namespace_header_attempt_binding_body(db,legacy_media):
    service,ledger,scope,ctx,job=await dispatched(db,legacy_media)
    original=await service.record_http_response_reference(job['id'],job['attempt_id'],namespace=NAMESPACE,http_request_id=GATEWAY_ID,response_body_sha256='d'*64)
    assert (await service.record_http_response_reference(job['id'],job['attempt_id'],namespace=NAMESPACE,http_request_id=GATEWAY_ID,response_body_sha256='d'*64))==original
    for patch in [{'namespace':'guessed'},{'http_request_id':FORWARDED_ID},{'http_request_id':'202610270212247285164708268d9d6hP1gm1CE'},{'response_body_sha256':'e'*64}]:
        with pytest.raises(PlatformError):await service.record_http_response_reference(job['id'],job['attempt_id'],**({'namespace':NAMESPACE,'http_request_id':GATEWAY_ID,'response_body_sha256':'d'*64}|patch))
    for column,value in [('namespace','guessed'),('http_request_id',FORWARDED_ID),('response_body_sha256','e'*64)]:
        with pytest.raises(psycopg.Error)as error:
            async with legacy_media.sql(service.job_permit(job['id'])) as c:await c.execute('UPDATE media_http_response_references SET '+column+'=%s WHERE job_id=%s',(value,job['id']))
        assert 'media maintenance permit required' not in str(error.value)
    await artifact(service,job)
    arguments=dict(terminal_status='completed',provider_response_id=None,quantities={'image':1},basis_evidence={'verified_delivery_v1':'e'*64},evidence_hash='d'*64,reference_kind='http_request_id',http_reference_namespace=NAMESPACE,http_reference_value=GATEWAY_ID)
    for patch in [{'provider_response_id':'fake-object'},{'reference_kind':'object_id'},{'evidence_hash':'f'*64},{'http_reference_value':'202610270212247285164708268d9d6hP1gm1CE'}]:
        with pytest.raises(PlatformError):await service.record_terminal_evidence(job['id'],job['attempt_id'],**(arguments|patch))
    await service.record_terminal_evidence(job['id'],job['attempt_id'],**arguments)
    assert (await service.finish_from_evidence(job['id'],job['attempt_id']))['actual']=='2'
    async with db.connection() as c:
        receipt=await (await c.execute('SELECT * FROM media_receipts WHERE job_id=%s',(job['id'],))).fetchone()
        assert receipt['provider_response_id'] is None and receipt['http_reference_namespace']==NAMESPACE


@pytest.mark.asyncio
async def test_database_cannot_reuse_gateway_reference_or_attach_wrong_attempt_scope_and_header(db,legacy_media):
    service,ledger,scope,ctx,first=await dispatched(db,legacy_media)
    await service.record_http_response_reference(first['id'],first['attempt_id'],namespace=NAMESPACE,http_request_id=GATEWAY_ID,response_body_sha256='d'*64)
    second=await admit(service,ctx,'second')
    await service.record_provider_attempt(second['id'],second['attempt_id'],provider_request_hash='e'*64,provider_scope_hash='c'*64,upstream_model='fixture',request_id='local-second')
    await service.dispatch(second['id'],second['attempt_id'])
    await service.record_provider_association(second['id'],second['attempt_id'],http_status=200,http_request_id=GATEWAY_ID)
    values=dict(job_id=second['id'],attempt_id=second['attempt_id'],binding_hash=second['binding_hash'],provider_scope_hash='c'*64,namespace=NAMESPACE,http_request_id=GATEWAY_ID,response_body_sha256='d'*64)
    for patch in [{},{'attempt_id':'other'},{'binding_hash':'f'*64},{'provider_scope_hash':'f'*64},{'http_request_id':'202610270212247285164708268d9d6hP1gm1CE'}]:
        with pytest.raises(psycopg.Error)as error:
            async with legacy_media.sql(service.job_permit(second['id'])) as c:
                await c.execute('INSERT INTO media_http_response_references(job_id,attempt_id,binding_hash,provider_scope_hash,namespace,http_request_id,response_body_sha256) VALUES (%(job_id)s,%(attempt_id)s,%(binding_hash)s,%(provider_scope_hash)s,%(namespace)s,%(http_request_id)s,%(response_body_sha256)s)',values|patch)
        if 'attempt_id' not in patch:
            assert 'media maintenance permit required' not in str(error.value)
    assert (await ledger.summary(scope))['reserved']=='12'


@pytest.mark.asyncio
async def test_database_terminal_typed_tuple_and_body_hash_cannot_be_forged(db,legacy_media):
    service,ledger,scope,ctx,job=await dispatched(db,legacy_media)
    await service.record_http_response_reference(job['id'],job['attempt_id'],namespace=NAMESPACE,http_request_id=GATEWAY_ID,response_body_sha256='d'*64)
    await artifact(service,job)
    values=dict(job_id=job['id'],attempt_id=job['attempt_id'],binding_hash=job['binding_hash'],
        terminal_status='completed',provider_response_id=None,quantities=Jsonb({'image':1}),
        basis_evidence=Jsonb({'verified_delivery_v1':'e'*64}),evidence_hash='d'*64,
        reference_kind='http_request_id',http_reference_namespace=NAMESPACE,http_reference_value=GATEWAY_ID)
    for patch in [{'evidence_hash':'f'*64},{'provider_response_id':'fabricated-object'},
                  {'http_reference_namespace':'openai_x_request_id_v1'},
                  {'http_reference_value':'202610270212247285164708268d9d6hP1gm1CE'}]:
        with pytest.raises(psycopg.Error) as caught:
            async with legacy_media.sql(service.job_permit(job['id'])) as c:
                await c.execute('INSERT INTO media_terminal_evidence(job_id,attempt_id,binding_hash,terminal_status,provider_response_id,quantities,basis_evidence,evidence_hash,reference_kind,http_reference_namespace,http_reference_value) VALUES (%(job_id)s,%(attempt_id)s,%(binding_hash)s,%(terminal_status)s,%(provider_response_id)s,%(quantities)s,%(basis_evidence)s,%(evidence_hash)s,%(reference_kind)s,%(http_reference_namespace)s,%(http_reference_value)s)',values|patch)
                await c.execute("UPDATE media_jobs SET execution_state='completed' WHERE id=%s",(job['id'],))
                await service._release_storage_budget(c,job['id'])
        assert caught.value.sqlstate in {'23503','23514'}
        assert 'media maintenance permit required' not in str(caught.value)
    await service.finish(job['id'],job['attempt_id'],terminal_status='completed',provider_response_id=None,
        quantities={'image':1},basis_evidence={'verified_delivery_v1':'e'*64},evidence_hash='d'*64,
        reference_kind='http_request_id',http_reference_namespace=NAMESPACE,http_reference_value=GATEWAY_ID)
    assert (await ledger.summary(scope))['spent']=='2'


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['download_timeout','decode_failure','wrong_header'])
async def test_gateway_unknown_or_invalid_delivery_preserves_hold_and_replay_never_generates_again(db,tmp_path,failure,legacy_media):
    service,ledger,scope,*_=await legacy_media.setup(db)
    configured=settings(tmp_path,source_root=legacy_media.source_path,download_origins=('http://127.0.0.1:9998',))
    token=await service.mint(scope,model='fixture-image',capability='image.generate',invocation_key='unknown',mode='test',bindings=configured.bindings)
    calls=[]
    def handler(req):
        calls.append(req)
        if req.method=='POST':return httpx.Response(200,json={'created':1793067144,'data':[{'url':'http://127.0.0.1:9998/image.png'}]},headers={'x-oneapi-request-id':FORWARDED_ID if failure=='wrong_header' else GATEWAY_ID})
        if failure=='download_timeout':raise httpx.ReadTimeout('synthetic-only',request=req)
        return httpx.Response(200,content=b'not-an-image',headers={'content-type':'image/png'})
    runtime=MediaRuntime(db,service.identity,configured,service=service.raw,maintenance=legacy_media.generation,transport=httpx.MockTransport(handler))
    await legacy_media.start_runtime(runtime)
    try:
        with pytest.raises(PlatformError):await runtime.submit(token['task_token'],'one',request())
        old=await runtime.submit(token['task_token'],'one',request())
        assert old['financial_state']=='pending_reconciliation' and old['actual'] is None
        assert sum(r.method=='POST' for r in calls)==1
        assert (await ledger.summary(scope))['reserved']=='6' and (await ledger.summary(scope))['spent']=='0'
        async with db.connection() as c:
            assert (await (await c.execute('SELECT count(*) n FROM media_terminal_evidence')).fetchone())['n']==0
            assert (await (await c.execute('SELECT count(*) n FROM media_receipts')).fetchone())['n']==0
    finally:await runtime.close()


@pytest.mark.asyncio
async def test_gateway_wrong_decoded_dimensions_keep_unknown_hold_budget_and_never_register_or_repost(db,tmp_path,legacy_media):
    service,ledger,scope,*_=await legacy_media.setup(db)
    configured=settings(tmp_path,source_root=legacy_media.source_path)
    token=await service.mint(scope,model='fixture-image',capability='image.generate',invocation_key='wrong-size',mode='test',bindings=configured.bindings)
    calls=[]
    def handler(req):
        calls.append(req)
        return httpx.Response(200,json={'created':1793067144,'data':[{'b64_json':base64.b64encode(png((2,2))).decode()}]},headers={'x-oneapi-request-id':GATEWAY_ID})
    runtime=MediaRuntime(db,service.identity,configured,service=service.raw,maintenance=legacy_media.generation,transport=httpx.MockTransport(handler))
    await legacy_media.start_runtime(runtime)
    try:
        with pytest.raises(PlatformError):await runtime.submit(token['task_token'],'one',request())
        old=await runtime.submit(token['task_token'],'one',request())
        assert old['financial_state']=='pending_reconciliation' and old['actual'] is None and old['artifacts']==[]
        assert (await ledger.summary(scope))['spent']=='0' and (await ledger.summary(scope))['reserved']=='6'
        assert len(calls)==1
        async with db.connection() as c:
            assert (await (await c.execute('SELECT count(*) n FROM media_artifacts')).fetchone())['n']==0
            assert (await (await c.execute('SELECT count(*) n FROM media_blobs')).fetchone())['n']==0
            assert (await (await c.execute('SELECT count(*) n FROM media_terminal_evidence')).fetchone())['n']==0
            assert (await (await c.execute('SELECT count(*) n FROM media_receipts')).fetchone())['n']==0
            budget=await (await c.execute('SELECT * FROM media_storage_budgets WHERE job_id=%s',(old['id'],))).fetchone()
            assert budget['released_at'] is None and budget['remaining_bytes']==budget['max_bytes'] and budget['remaining_files']==budget['max_files']
    finally:await runtime.close()


@pytest.mark.parametrize('parameters',[{'n':2,'size':'1024x1024'},{'n':1,'size':'1536x1024'},{'n':1},
                                     {'n':1,'size':'1024x1024','quality':'low'}])
def test_explicit_gateway_profile_does_not_expand_unverified_request_quantity_size_or_quality(parameters):
    adapter=OpenAIImagesAdapter(provider(),'test')
    with pytest.raises(PlatformError):adapter.prepare(request()|{'parameters':parameters},[])
