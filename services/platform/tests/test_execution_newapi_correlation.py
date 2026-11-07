"""Opaque New API HTTP correlation survives errors without a paid retry."""
import json

import httpx
import pytest

from platform_service.errors import PlatformError
from platform_service.execution_config import ExecutionProvider,ExecutionSettings
from platform_service.execution_runtime import ExecutionRuntime
from test_execution_core import setup


async def build(db,headers,*,status=400,stream=False):
    service,ledger,scope,_=await setup(db)
    calls=[]
    terminal={'id':'response-synthetic','status':'completed',
              'usage':{'input_tokens':3,'output_tokens':2},'output':[]}
    def handler(request):
        calls.append(request)
        if status!=200:return httpx.Response(status,
            json={'error':{'message':'SYNTHETIC_ERROR_BODY_DO_NOT_STORE'}},headers=headers)
        if stream:
            body=('event: response.completed\ndata: '+json.dumps({'type':'response.completed','response':terminal})+'\n\n').encode()
            return httpx.Response(200,content=body,headers={'content-type':'text/event-stream',**headers})
        return httpx.Response(200,json=terminal,headers=headers)
    settings=ExecutionSettings(mode='test',providers={'fixture':ExecutionProvider('1',
        'http://127.0.0.1:19991/v1/responses','SYNTHETIC_PROVIDER_KEY_DO_NOT_STORE',
        {'fixture-text':'synthetic-upstream'})})
    runtime=ExecutionRuntime(db,service.identity,settings,service=service,transport=httpx.MockTransport(handler))
    token=(await runtime.mint(scope,{'model':'fixture-text','invocation_key':'newapi-correlation'}))['task_token']
    body={'model':'fixture-text','input':'SYNTHETIC_REQUEST_BODY_DO_NOT_STORE','stream':stream}
    return runtime,ledger,scope,token,body,calls


async def association(db):
    async with db.connection()as c:
        return await(await c.execute("SELECT * FROM execution_provider_associations WHERE kind='http'")).fetchone()


@pytest.mark.asyncio
async def test_http400_newapi_opaque_header_is_saved_before_error_without_body_or_retry(db):
    opaque='20261028T_SYNTHETIC-NewAPI:opaque.1'
    runtime,ledger,scope,token,body,calls=await build(db,{
        'X-Oneapi-Request-Id':opaque,'X-Private-Debug':'SYNTHETIC_ARBITRARY_HEADER_DO_NOT_STORE'})
    try:
        with pytest.raises(PlatformError)as error:await runtime.respond(token,'same-operation',body)
        assert error.value.code=='upstream_error'
        row=await association(db)
        assert row['http_status']==400 and row['http_request_id']==opaque
        assert set(row)=={'job_id','kind','attempt_id','binding_hash','provider_scope_hash',
                         'http_request_id','provider_response_id','http_status','created_at'}
        serialized=json.dumps(row,default=str)
        assert 'DO_NOT_STORE'not in serialized
        async with db.connection()as c:
            job=await(await c.execute('SELECT status,actual,amount FROM execution_jobs')).fetchone()
            assert job['status']=='pending_reconciliation'and job['actual']is None
            attempt=await(await c.execute('SELECT * FROM execution_provider_attempts')).fetchone()
            assert 'DO_NOT_STORE'not in json.dumps(attempt,default=str)
            for table in ['execution_receipts','execution_terminal_evidence','execution_results']:
                assert(await(await c.execute('SELECT count(*) n FROM '+table)).fetchone())['n']==0
        summary=await ledger.summary(scope)
        assert summary['spent']=='0'and int(summary['reserved'])==job['amount']>0
        with pytest.raises(PlatformError):await runtime.respond(token,'same-operation',body)
        await runtime.run_once();await runtime.run_once()
        assert len(calls)==1
        assert(await association(db))==row
    finally:await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('headers,expected',[
    ({'x-request-id':'original-first','request-id':'original-second','x-oneapi-request-id':'new-third'},'original-first'),
    ({'request-id':'original-second','x-oneapi-request-id':'new-third'},'original-second'),
    ({'x-request-id':'','request-id':'original-second','x-oneapi-request-id':'new-third'},'original-second'),
    ({'x-request-id':'','request-id':'','x-oneapi-request-id':'new-third'},'new-third'),
    ({'x-oneapi-request-id':'x'*256},'x'*256),
    ({'x-request-id':'invalid first value','request-id':'original-second','x-oneapi-request-id':'new-third'},None),
    ({'request-id':'invalid second value','x-oneapi-request-id':'new-third'},None),
    ({},None)])
async def test_original_correlation_header_priority_is_preserved(db,headers,expected):
    runtime,_,_,token,body,calls=await build(db,headers)
    try:
        with pytest.raises(PlatformError):await runtime.respond(token,'priority',body)
        row=await association(db)
        assert row['http_status']==400 and row['http_request_id']==expected
        assert len(calls)==1
    finally:await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('value',['','x'*257,'Bearer SYNTHETIC_SECRET',
    'https://private.invalid/?key=synthetic','request=id','one,two','with space','line\nbreak'])
async def test_invalid_newapi_correlation_is_not_persisted(db,value):
    runtime,_,_,token,body,calls=await build(db,{'X-Oneapi-Request-Id':value})
    try:
        with pytest.raises(PlatformError):await runtime.respond(token,'invalid-header',body)
        row=await association(db)
        assert row['http_status']==400 and row['http_request_id']is None
        assert len(calls)==1
    finally:await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stream',[False,True])
async def test_newapi_correlation_does_not_change_200_usage_stream_or_replay(db,stream):
    runtime,ledger,scope,token,body,calls=await build(db,{'X-Oneapi-Request-Id':'newapi-success'},status=200,stream=stream)
    async def consume(response):
        if hasattr(response,'body_iterator'):return b''.join([part async for part in response.body_iterator])
        return response.body
    try:
        first=await consume(await runtime.respond(token,'success',body))
        replay=await consume(await runtime.respond(token,'success',body))
        assert first==replay and len(calls)==1
        assert(await association(db))['http_request_id']=='newapi-success'
        assert(await ledger.summary(scope))['spent']=='700'
        async with db.connection()as c:
            assert(await(await c.execute('SELECT count(*) n FROM execution_receipts')).fetchone())['n']==1
    finally:await runtime.close()
