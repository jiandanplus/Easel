"""Explicit Audio REST contracts; every HTTP request uses a synthetic transport."""
import hashlib
import json
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from platform_service.errors import PlatformError


def provider():
    return SimpleNamespace(base_url='http://127.0.0.1:9101/v1',secret='fixture-only-secret',
                           models={'speech':'configured-tts','transcribe':'configured-asr'},
                           capabilities=('audio.speech','audio.transcribe'),download_origins=())


def speech_request(**parameters):
    return {'model':'speech','capability':'audio.speech','prompt':'A😀e\u0301 中文','assets':[],
            'parameters':{'voice':'alloy','response_format':'wav',**parameters}}


def asset(raw=b'fixture-wav',mime='audio/wav'):
    return {'id':'owned-sample','raw':raw,'sha256':hashlib.sha256(raw).hexdigest(),'size':len(raw),'mime':mime,
            'properties':{'format':'WAV','duration_ms':1001,'channels':1,'sample_rate':24000}}


def transcribe_request(**parameters):
    return {'model':'transcribe','capability':'audio.transcribe','prompt':'专有名词','assets':['owned-sample'],
            'parameters':{'response_format':'json',**parameters}}


def speech_adapter(**profile):
    from platform_service.media.adapters.audio import AudioProfile,OpenAISpeechAdapter
    return OpenAISpeechAdapter(provider(),'test',AudioProfile(voices=('alloy','coral'),**profile))


def transcribe_adapter(**profile):
    from platform_service.media.adapters.audio import AudioProfile,OpenAITranscriptionsAdapter
    return OpenAITranscriptionsAdapter(provider(),'test',AudioProfile(**profile))


async def headers_collector(values,**kwargs):values.append(kwargs)


def test_speech_wire_preserves_scalar_text_and_serializes_exact_speed():
    adapter=speech_adapter(instructions=True)
    request=speech_request(speed_percent=75,instructions='轻声朗读')
    result=adapter.prepare(request,[])
    body=json.loads(result.payload,parse_float=Decimal)
    assert result.url=='http://127.0.0.1:9101/v1/audio/speech'
    assert body=={'model':'configured-tts','input':request['prompt'],'voice':'alloy','response_format':'wav',
                  'stream_format':'audio','speed':Decimal('0.75'),'instructions':'轻声朗读'}
    assert adapter.supported_meters==frozenset({('unicode_scalar','verified_input_v1'),('audio_ms','verified_delivery_v1')})


@pytest.mark.parametrize('patch',[{'voice':'private_voice_id'},{'response_format':'pcm'},{'speed_percent':True},
                                 {'speed_percent':24},{'speed_percent':401},{'stream':True},{'instructions':'ignored'}])
def test_speech_rejects_unpublished_voice_unsupported_stream_pcm_and_parameters(patch):
    with pytest.raises(PlatformError):speech_adapter().prepare(speech_request(**patch),[])


@pytest.mark.asyncio
async def test_speech_binary_has_typed_real_http_correlation_and_no_invented_usage():
    adapter=speech_adapter();prepared=adapter.prepare(speech_request(),[]);calls=[];headers=[]
    async def transport(request):
        calls.append(request)
        assert request.headers['authorization']=='Bearer fixture-only-secret'
        return httpx.Response(200,headers={'content-type':'audio/wav','x-request-id':'req_real_upstream'},content=b'RIFFfixture')
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport),follow_redirects=True) as client:
        result=await adapter.submit(client,prepared,request_id='local-only-id',max_bytes=1024,
                                    on_headers=lambda **v:headers_collector(headers,**v))
    assert len(calls)==1 and result.payload==b'RIFFfixture' and result.mime=='audio/wav'
    assert result.correlation.namespace=='openai_x_request_id_v1' and result.correlation.value=='req_real_upstream'
    assert result.quantities=={} and result.usage_hash is None
    assert result.evidence_hash==hashlib.sha256(b'RIFFfixture').hexdigest()
    assert not hasattr(result,'provider_response_id') and not hasattr(result,'response_id')
    assert headers==[{'http_status':200,'http_request_id':'req_real_upstream'}]


@pytest.mark.asyncio
@pytest.mark.parametrize('response_headers,body',[
    ({'content-type':'audio/wav'},b'RIFFfixture'),
    ({'content-type':'audio/wav','x-request-id':'req_ok','content-length':'99'},b'RIFFfixture'),
    ({'content-type':'audio/wav','x-request-id':'req_ok','content-encoding':'gzip'},b'RIFFfixture'),
    ({'content-type':'application/json','x-request-id':'req_ok'},b'{"audio":"fake"}')])
async def test_speech_ambiguous_or_truncated_response_is_not_trusted(response_headers,body):
    adapter=speech_adapter();prepared=adapter.prepare(speech_request(),[]);calls=[]
    async def transport(request):calls.append(request);return httpx.Response(200,headers=response_headers,content=body)
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        with pytest.raises(PlatformError):await adapter.submit(client,prepared,request_id='req_local',max_bytes=1024,on_headers=lambda **v:headers_collector([],**v))
    assert len(calls)==1


@pytest.mark.asyncio
async def test_speech_redirect_and_timeout_never_post_again_or_leak_body():
    adapter=speech_adapter();prepared=adapter.prepare(speech_request(),[])
    for kind in ['redirect','timeout','error']:
        calls=[]
        async def transport(request):
            calls.append(request)
            if kind=='timeout':raise httpx.ReadTimeout('fixture-secret-not-for-errors')
            if kind=='redirect':return httpx.Response(307,headers={'location':'https://other.example.invalid/upload'})
            return httpx.Response(401,headers={'content-type':'application/json'},content=b'{"error":{"message":"fixture-secret-not-for-errors"}}')
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport),follow_redirects=True) as client:
            with pytest.raises(PlatformError) as error:
                await adapter.submit(client,prepared,request_id='local',max_bytes=1024,on_headers=lambda **v:headers_collector([],**v))
        assert len(calls)==1 and 'fixture-secret-not-for-errors' not in str(error.value)


def test_transcription_safe_multipart_preserves_context_and_verified_file_bytes():
    adapter=transcribe_adapter(transcription_usage='tokens')
    result=adapter.prepare(transcribe_request(language='zh'),[asset()])
    assert result.url=='http://127.0.0.1:9101/v1/audio/transcriptions'
    assert result.content_type.startswith('multipart/form-data; boundary=')
    assert b'name="file"; filename="input.wav"' in result.payload
    assert b'fixture-wav' in result.payload and '专有名词'.encode() in result.payload
    assert b'name="model"\r\n\r\nconfigured-asr' in result.payload
    assert b'name="language"\r\n\r\nzh' in result.payload
    assert ('input_token','provider_usage_v1') in adapter.supported_meters
    assert ('audio_ms','provider_usage_v1') not in adapter.supported_meters


def test_transcription_rejects_unverified_files_paths_speaker_objects_and_unknown_formats():
    adapter=transcribe_adapter()
    for request,assets in [(transcribe_request(),[]),(transcribe_request(),[asset(),asset()]),
                           (transcribe_request(),[{**asset(),'sha256':'0'*64}]),
                           (transcribe_request(),[{**asset(),'properties':{}}]),
                           (transcribe_request(response_format='diarized_json'),[asset()]),
                           (transcribe_request(file='/etc/private'),[asset()]),
                           (transcribe_request(known_speaker_references=['data:audio/wav;base64,AAAA']),[asset()])]:
        with pytest.raises(PlatformError):adapter.prepare(request,assets)


async def transcription_result(raw,*,usage='tokens',response_format='json'):
    adapter=transcribe_adapter(transcription_usage=usage)
    prepared=adapter.prepare(transcribe_request(response_format=response_format),[asset()])
    async def transport(request):return httpx.Response(200,headers={'content-type':'application/json','x-request-id':'req_transcript'},content=raw)
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        return await adapter.submit(client,prepared,request_id='local',max_bytes=4096,on_headers=lambda **v:headers_collector([],**v))


@pytest.mark.asyncio
async def test_transcription_tokens_are_exact_and_public_output_preserves_only_text():
    raw=b'{"text":"A\\ud83d\\ude00e\\u0301","private_url":"https://private.invalid","usage":{"type":"tokens","input_tokens":14,"output_tokens":45,"total_tokens":59}}'
    result=await transcription_result(raw)
    assert result.quantities=={'input_token':14,'output_token':45}
    assert json.loads(result.payload)=={'text':'A😀e\u0301'}
    assert result.mime=='application/json' and result.properties=={'unicode_scalar':4}
    assert result.evidence_hash==hashlib.sha256(raw).hexdigest() and result.usage_hash


@pytest.mark.asyncio
@pytest.mark.parametrize('usage',[b'null',b'{"type":"tokens","input_tokens":true,"output_tokens":1,"total_tokens":2}',
                                 b'{"type":"tokens","input_tokens":1,"output_tokens":2,"total_tokens":4}',
                                 b'{"type":"tokens","input_tokens":1.2,"output_tokens":2,"total_tokens":3}',
                                 b'{"type":"tokens","input_tokens":1,"output_tokens":-2,"total_tokens":-1}',
                                 b'{"type":"duration","seconds":1}',
                                 b'{"type":"tokens","input_tokens":1,"input_tokens":2,"output_tokens":1,"total_tokens":3}'])
async def test_transcription_missing_malformed_or_wrong_profile_usage_cannot_be_used_as_zero(usage):
    with pytest.raises(PlatformError):await transcription_result(b'{"text":"ok","usage":'+usage+b'}')


@pytest.mark.asyncio
async def test_transcription_duration_profile_uses_decimal_usage_not_display_duration():
    result=await transcription_result(b'{"text":"ok","duration":999,"usage":{"type":"duration","seconds":1.0001}}',usage='duration',response_format='verbose_json')
    assert result.quantities=={'audio_ms':1001}
    fixed=await transcription_result(b'{"text":"","duration":999}',usage='none',response_format='verbose_json')
    assert fixed.quantities=={} and fixed.usage_hash is None and json.loads(fixed.payload)=={'text':''}


@pytest.mark.asyncio
async def test_transcription_non_json_error_or_duplicate_text_is_rejected():
    for raw in [b'not-json',b'{"text":"first","text":"second"}',b'{"text":null}',b'{"error":{"message":"private"}}',b'{"text":"\\ud800"}']:
        with pytest.raises(PlatformError):await transcription_result(raw,usage='none')


def test_video_asr_uses_verified_audio_stream_duration_not_container_length():
    adapter=transcribe_adapter(transcription_video=True)
    sample=asset(mime='video/mp4')
    sample['properties']={'format':'MP4','duration_ms':30000,'audio_duration_ms':1250,'channels':1,'sample_rate':24000,'width':320,'height':240}
    prepared=adapter.prepare(transcribe_request(),[sample])
    assert prepared.input_quantities=={'audio_ms':1250}
    del sample['properties']['audio_duration_ms']
    with pytest.raises(PlatformError):adapter.prepare(transcribe_request(),[sample])


@pytest.mark.asyncio
@pytest.mark.parametrize('http_id',['req_ok,req_other','../../req_fake','local-only-id','req_含中文','req_space value'])
async def test_binary_correlation_requires_exact_declared_http_namespace(http_id):
    adapter=speech_adapter();prepared=adapter.prepare(speech_request(),[])
    async def transport(request):return httpx.Response(200,headers=[(b'content-type',b'audio/wav'),(b'x-request-id',http_id.encode('utf-8'))],content=b'fixture')
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        with pytest.raises(PlatformError):await adapter.submit(client,prepared,request_id='local',max_bytes=1024,on_headers=lambda **v:headers_collector([],**v))


@pytest.mark.asyncio
async def test_audio_prepared_url_cannot_forward_secret_to_another_origin_or_path():
    from dataclasses import replace
    adapter=speech_adapter();prepared=adapter.prepare(speech_request(),[]);calls=[]
    async def transport(request):
        calls.append(request)
        return httpx.Response(200,headers={'content-type':'audio/wav','x-request-id':'req_fixture'},content=b'fixture')
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        for url in ['https://other.example.invalid/audio/speech','http://127.0.0.1:9101/private']:
            with pytest.raises(PlatformError):await adapter.submit(client,replace(prepared,url=url),request_id='local',max_bytes=1024,on_headers=lambda **v:headers_collector([],**v))
    assert calls==[]


def test_audio_binding_and_public_model_types_are_explicit_before_preparing():
    from platform_service.media.adapters.audio import AudioProfile,OpenAISpeechAdapter
    wrong=provider();wrong.adapter='openai_images_v1'
    with pytest.raises(PlatformError):OpenAISpeechAdapter(wrong,'test',AudioProfile(voices=('alloy',)))
    for model in [{},[],None]:
        request=speech_request();request['model']=model
        with pytest.raises(PlatformError):speech_adapter().prepare(request,[])
