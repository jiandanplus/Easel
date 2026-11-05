"""Locked private New API voice envelopes; synthetic bytes are not live audio."""
from dataclasses import replace
from datetime import datetime,timedelta,timezone
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest

from platform_service.errors import PlatformError

TASK_A='task_'+'a'*32
TASK_B='task_'+'b'*32
MINIMAX='easel-minimax-speech-2.8-hd'


def provider(adapter,model):
    return SimpleNamespace(base_url='http://127.0.0.1:9102',secret='fixture-only-secret',version='1',adapter=adapter,
        models={'public':model},capabilities=('voice.enroll','voice.synthesize','audio.speech'),download_origins=())


def reference(protocol,target):
    from platform_service.media.voices import OwnedVoiceReference
    return OwnedVoiceReference('central-owned',protocol,target,'fixture','1','a'*64,'c'*64,
        datetime.now(timezone.utc)+timedelta(hours=1),TASK_B,{'user_id':'owner','product_id':'easel','tenant_id':'tenant','billing_account_id':'wallet'})


def sample(duration_ms=10000):
    raw=b'protocol-fixture-wav'
    return {'id':'owned-sample','raw':raw,'sha256':hashlib.sha256(raw).hexdigest(),'size':len(raw),'mime':'audio/wav',
        'properties':{'format':'WAV','duration_ms':duration_ms,'channels':1,'sample_rate':24000}}


def request(capability='voice.synthesize',**parameters):
    return {'model':'public','capability':capability,'prompt':'A😀 中文','assets':[],
        'voice_id':'central-owned','parameters':parameters}


def minimax():
    from platform_service.media.adapters.newapi_voice import BridgeVoiceProfile,MiniMaxVoiceAdapter
    return MiniMaxVoiceAdapter(provider('newapi_minimax_voice_v1',MINIMAX),'test',
        BridgeVoiceProfile(target_models=(MINIMAX,)),binding_hash='a'*64,provider_scope_hash='c'*64)


def dashscope():
    from platform_service.media.adapters.newapi_voice import BridgeVoiceProfile,DashScopeVoiceAdapter
    return DashScopeVoiceAdapter(provider('newapi_dashscope_voice_v1','cosyvoice-v2'),'test',
        BridgeVoiceProfile(target_models=('cosyvoice-v2',)),binding_hash='a'*64,provider_scope_hash='c'*64)


def fish():
    from platform_service.media.adapters.newapi_voice import BridgeVoiceProfile,FishVoiceAdapter
    return FishVoiceAdapter(provider('newapi_fish_voice_v1','easel-fish-s2.1-pro'),'test',
        BridgeVoiceProfile(target_models=('easel-fish-s2.1-pro',),presets=(('narrator','admin_preset'),)),
        binding_hash='a'*64,provider_scope_hash='c'*64,clock=lambda:1000)


def test_minimax_upload_has_one_file_and_registration_has_no_hidden_preview():
    adapter=minimax();body=request('voice.enroll',target_model=MINIMAX,need_noise_reduction=True)
    body.pop('voice_id');body['prompt']='';body['assets']=['owned-sample']
    upload=adapter.prepare_upload(body,[sample()])
    assert upload.url.endswith('/easel/minimax/v1/files/upload')
    assert upload.payload.count(b'name="file"')==1 and b'name="purpose"\r\n\r\nvoice_clone' in upload.payload
    assert b'protocol-fixture-wav' in upload.payload and 'preview'.encode() not in upload.payload
    registration=adapter.prepare_register(body,{'file_task_id':TASK_A,'target_model':MINIMAX})
    assert json.loads(registration.payload)=={'file_task_id':TASK_A,'need_noise_reduction':True}
    assert upload.upstream_model=='easel-minimax-upload' and registration.upstream_model=='easel-minimax-clone'
    assert upload.target_model==MINIMAX and registration.target_model==MINIMAX
    for source in [sample(9999),sample(300001),{**sample(),'mime':'audio/ogg'}, {**sample(),'sha256':'0'*64}]:
        with pytest.raises(PlatformError):adapter.prepare_upload(body,[source])


def test_minimax_synthesis_accepts_only_owned_exact_binding_and_maps_integer_speed():
    adapter=minimax();owned=reference('newapi_minimax_voice_v1',MINIMAX)
    prepared=adapter.prepare(request(speed_percent=125,emotion='happy'),[],owned_voice=owned)
    assert prepared.url.endswith('/easel/minimax/v1/t2a_v2')
    assert json.loads(prepared.payload)=={'model':MINIMAX,'voice_task_id':TASK_B,'text':'A😀 中文','speed':1.25,'emotion':'happy','stream':False}
    for changed in [None,replace(owned,binding_hash='e'*64),replace(owned,provider_scope_hash='d'*64),
                    replace(owned,target_model='other'),replace(owned,protocol='newapi_dashscope_voice_v1'),
                    replace(owned,expires_at=datetime.now(timezone.utc)-timedelta(seconds=1))]:
        with pytest.raises(PlatformError):adapter.prepare(request(),[],owned_voice=changed)
    with pytest.raises(PlatformError):adapter.prepare(request(voice_task_id=TASK_A),[],owned_voice=owned)


async def submit_result(adapter,prepared,value,status=200):
    calls=[]
    async def transport(req):calls.append(req);return httpx.Response(status,headers={'content-type':'application/json','x-request-id':'req_bridge'},content=json.dumps(value).encode())
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport),follow_redirects=True) as client:
        result=await adapter.submit(client,prepared,request_id='durable-stage-id',max_bytes=1048576,on_headers=lambda **v:noop())
    assert len(calls)==1
    return result


async def noop():pass


@pytest.mark.asyncio
async def test_minimax_hex_completion_preserves_reported_facts_without_character_unit_guess():
    adapter=minimax();prepared=adapter.prepare(request(),[],owned_voice=reference('newapi_minimax_voice_v1',MINIMAX))
    result=await submit_result(adapter,prepared,{'task_id':TASK_A,'status':'SUCCESS','data':{'status':2,'audio':'494433'},'extra_info':{'audio_format':'mp3','usage_characters':6,'audio_length':1000}})
    assert result.reference.task_id==TASK_A and result.reference.namespace=='newapi_minimax_voice_v1'
    assert result.audio==b'ID3' and result.mime=='audio/mpeg'
    assert result.reported_facts['usage_characters']==6 and result.quantities=={'audio_ms':1000}
    assert 'unicode_scalar' not in result.quantities and 'input_token' not in result.quantities
    assert ('unicode_scalar','provider_usage_v1') not in adapter.supported_meters
    for patch in [{'task_id':'supplier-private-voice'},{'data':{'status':1,'audio':'494433'}},
                  {'data':{'status':2,'audio':'xyz'}},{'extra_info':{}},{'extra_info':{'usage_characters':6}},{'status':'FAILED'}]:
        with pytest.raises(PlatformError):
            await submit_result(adapter,prepared,{**{'task_id':TASK_A,'status':'SUCCESS','data':{'status':2,'audio':'494433'},'extra_info':{'audio_format':'mp3','usage_characters':6}},**patch})


@pytest.mark.asyncio
async def test_minimax_accepts_actual_locked_presenter_fixture_without_supplier_ids():
    from pathlib import Path
    root=Path(__file__).resolve().parents[3]
    fixture=json.loads((root/'integrations/newapi/plugins/easel-minimax-voice/1.0.0/fixtures.json').read_text())
    value=next(row['expected'] for row in fixture['cases'] if row.get('member')=='created' and row['name']=='public speech result is JSON hex')
    adapter=minimax();prepared=adapter.prepare(request(),[],owned_voice=reference('newapi_minimax_voice_v1',MINIMAX))
    result=await submit_result(adapter,prepared,value)
    assert result.audio==b'ID3' and result.quantities=={'audio_ms':1200}
    assert result.reported_facts['usage_characters']==5


def test_dashscope_legacy_url_enrollment_stays_closed_and_synthesis_uses_owned_review():
    adapter=dashscope();body=request('voice.enroll',target_model='cosyvoice-v2',url='https://private.invalid/sample.wav')
    with pytest.raises(PlatformError) as error:adapter.prepare_enroll(body,[sample()])
    assert error.value.code=='voice_sample_publication_unavailable'
    prepared=adapter.prepare(request(response_format='wav',speed_percent=90,instruction='轻声朗读'),[],owned_voice=reference('newapi_dashscope_voice_v1','cosyvoice-v2'))
    assert prepared.url.endswith('/easel/dashscope/voice/synthesize')
    assert json.loads(prepared.payload)=={'model':'cosyvoice-v2','input':{'voice_task_id':TASK_B,'text':'A😀 中文','format':'wav','rate':0.9,'instruction':'轻声朗读'}}


@pytest.mark.asyncio
async def test_dashscope_download_uses_protected_same_origin_artifact_not_supplier_url():
    adapter=dashscope();prepared=adapter.prepare(request(response_format='wav'),[],owned_voice=reference('newapi_dashscope_voice_v1','cosyvoice-v2'))
    value={'output':{'task_id':TASK_A,'task_status':'SUCCEEDED','finish_reason':'stop','audio':{'url':'https://untrusted.example.invalid/steal'}},'usage':{'characters':6}}
    result=await submit_result(adapter,prepared,value)
    assert result.descriptor['path']=='/v1/tasks/'+TASK_A+'/artifacts/audio/content'
    assert 'untrusted' not in str(result.descriptor) and result.quantities=={}
    calls=[]
    async def transport(req):
        calls.append(req)
        assert req.url.host=='127.0.0.1' and req.headers['authorization']=='Bearer fixture-only-secret'
        return httpx.Response(200,headers={'content-type':'audio/wav'},content=b'fixture-wav')
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport),follow_redirects=True) as client:
        data,mime=await adapter.retrieve(client,result,max_bytes=1024)
    assert len(calls)==1 and data==b'fixture-wav' and mime=='audio/wav'


def test_fish_inline_reference_is_validated_and_admin_preset_is_not_arbitrary_voice_id():
    adapter=fish();body=request('audio.speech',reference_text='样本转写',response_format='wav')
    body.pop('voice_id');body['assets']=['owned-sample']
    prepared=adapter.prepare(body,[sample()])
    fields=json.loads(prepared.payload)
    assert fields['model']=='easel-fish-s2.1-pro' and fields['references'][0]['text']=='样本转写'
    assert 'reference_id' not in fields
    preset=request('audio.speech',voice='narrator');preset.pop('voice_id')
    assert json.loads(adapter.prepare(preset,[]).payload)['reference_id']=='admin_preset'
    for patch in [{'voice':'raw-provider-id'},{'reference_id':'admin_preset'},{'response_format':'pcm'}]:
        bad={**preset,'parameters':{**preset['parameters'],**patch}}
        with pytest.raises(PlatformError):adapter.prepare(bad,[])


@pytest.mark.asyncio
async def test_fish_pending_queries_original_task_and_expired_artifact_never_resubmits():
    adapter=fish();body=request('audio.speech',voice='narrator');body.pop('voice_id');prepared=adapter.prepare(body,[])
    pending=await submit_result(adapter,prepared,{'task_id':TASK_A,'status':'IN_PROGRESS'})
    assert pending.state=='pending'
    calls=[]
    async def transport(req):
        calls.append(req);assert req.method=='GET' and req.url.path.endswith('/easel/fish/v1/tasks/'+TASK_A)
        return httpx.Response(200,headers={'content-type':'application/json'},json={'task_id':TASK_A,'status':'SUCCESS',
            'content_url':'/v1/tasks/'+TASK_A+'/artifacts/audio/content','mime_type':'audio/mpeg','size':8,'expires_at':1600})
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        done=await adapter.query(client,pending,prepared=prepared,max_bytes=1048576)
    assert len(calls)==1 and done.state=='completed' and done.quantities=={}
    with pytest.raises(PlatformError):
        await submit_result(adapter,prepared,{'task_id':TASK_A,'status':'SUCCESS','content_url':'/v1/tasks/'+TASK_A+'/artifacts/audio/content',
                                             'mime_type':'audio/mpeg','size':8,'expires_at':999})


@pytest.mark.asyncio
async def test_private_voice_post_redirect_timeout_and_foreign_prepared_url_never_retry():
    adapter=minimax();prepared=adapter.prepare(request(),[],owned_voice=reference('newapi_minimax_voice_v1',MINIMAX))
    for kind in ['redirect','timeout','foreign']:
        calls=[]
        async def transport(req):
            calls.append(req)
            if kind=='timeout':raise httpx.ReadTimeout('secret-upstream-message')
            return httpx.Response(307,headers={'location':'https://other.example.invalid/steal'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport),follow_redirects=True) as client:
            with pytest.raises(PlatformError) as error:
                await adapter.submit(client,replace(prepared,url='https://other.example.invalid/steal') if kind=='foreign' else prepared,
                                     request_id='durable-stage',max_bytes=1048576,on_headers=lambda **v:noop())
        assert len(calls)==(0 if kind=='foreign' else 1) and 'secret-upstream-message' not in str(error.value)
