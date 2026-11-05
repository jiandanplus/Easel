"""Fixed producer contracts; synthetic HTTP only, never a real generation."""
import importlib
import json
from types import SimpleNamespace

import httpx
import pytest
from platform_service.errors import PlatformError


def adapter(name):
    module=importlib.import_module('platform_service.media.adapters.newapi_async')
    provider=SimpleNamespace(base_url='http://127.0.0.1:19991/v1' if name=='newapi_videos_v1' else 'http://127.0.0.1:19991',
        secret='synthetic-only',models={'public':'upstream-explicit'},adapter=name,
        capabilities=('video.generate',) if name=='newapi_videos_v1' else ('music.generate',),
        download_origins=('http://127.0.0.1:19992',))
    return module.make_async_adapter(provider,'test')


def request(capability='video.generate',**parameters):
    return {'model':'public','capability':capability,'prompt':'synthetic brief','assets':[],
            'parameters':parameters}


def test_video_prepares_only_declared_standard_fields_and_owned_multipart():
    current=adapter('newapi_videos_v1');body=request(seconds=4,size='1280x720')
    value=current.prepare(body,[])
    assert value.url=='http://127.0.0.1:19991/v1/videos'
    assert json.loads(value.payload)=={'model':'upstream-explicit','prompt':'synthetic brief','seconds':'4','size':'1280x720'}
    owned={'id':'owned','mime':'image/png','raw':b'owned-decoded-image','properties':{'width':1,'height':1}}
    value=current.prepare(body,[owned])
    assert value.content_type.startswith('multipart/form-data; boundary=')
    assert b'name="input_reference"' in value.payload and b'owned-decoded-image' in value.payload


@pytest.mark.parametrize('patch',[{'duration':4},{'seconds':True},{'input_reference':'http://evil.invalid/a'},
    {'audio':False},{'seconds':0},{'seconds':901},{'size':'720p'}])
def test_video_rejects_unknown_or_unverified_mapping_without_guessing(patch):
    current=adapter('newapi_videos_v1')
    with pytest.raises(PlatformError):current.prepare(request(**{'seconds':4,'size':'1280x720',**patch}),[])


@pytest.mark.asyncio
async def test_video_persists_original_job_before_parsing_later_status_and_never_reposts():
    current=adapter('newapi_videos_v1');calls=[];saved=[];headers=[]
    async def handler(value):
        calls.append(value.method)
        return httpx.Response(200,json={'id':'video_fixture','status':'future-state'},headers={'x-request-id':'req_fixture'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PlatformError):
            await current.submit(client,current.prepare(request(seconds=4,size='1280x720'),[]),
                request_id='local-only',max_bytes=4096,on_headers=lambda **v: append(headers,v),on_job=lambda v: append(saved,v))
    assert saved==['video_fixture'] and headers[0]['http_status']==200 and calls==['POST']


async def append(items,value):items.append(value)


@pytest.mark.asyncio
async def test_video_uses_original_get_and_fixed_authenticated_content_not_guessed_url():
    current=adapter('newapi_videos_v1');calls=[]
    async def handler(value):
        calls.append((value.method,str(value.url),value.headers.get('authorization')))
        if value.url.path.endswith('/content'):return httpx.Response(200,content=b'synthetic-mp4',headers={'content-type':'video/mp4'})
        return httpx.Response(200,json={'id':'video_fixture','status':'completed','seconds':'99'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        observed=await current.query(client,'video_fixture',max_bytes=4096)
        assert observed.state=='completed' and observed.quantities=={} and observed.usage_hash is None
        assert len(observed.sources)==1 and observed.sources[0].kind=='content'
        raw,mime=await current.retrieve(client,observed.sources[0],max_bytes=100)
    assert raw==b'synthetic-mp4' and mime=='video/mp4'
    assert calls==[('GET','http://127.0.0.1:19991/v1/videos/video_fixture','Bearer synthetic-only'),
                   ('GET','http://127.0.0.1:19991/v1/videos/video_fixture/content','Bearer synthetic-only')]


@pytest.mark.asyncio
@pytest.mark.parametrize('bad',['wrong_id','redirect','compressed','duplicate','oversized'])
async def test_video_query_rejects_identity_or_response_ambiguity(bad):
    current=adapter('newapi_videos_v1')
    async def handler(value):
        if bad=='redirect':return httpx.Response(302,headers={'location':'http://127.0.0.1:19992/private'})
        if bad=='compressed':return httpx.Response(200,headers={'content-encoding':'gzip','content-type':'application/json'})
        if bad=='duplicate':return httpx.Response(200,content=b'{"id":"video_fixture","id":"other","status":"completed"}',headers={'content-type':'application/json'})
        if bad=='oversized':return httpx.Response(200,json={'id':'video_fixture','status':'completed','padding':'x'*5000})
        return httpx.Response(200,json={'id':'other','status':'completed'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler),follow_redirects=False) as client:
        with pytest.raises(PlatformError):await current.query(client,'video_fixture',max_bytes=1024)


def test_music_profiles_have_distinct_explicit_request_contracts():
    suno=adapter('newapi_suno_v1').prepare(request('music.generate',lyrics='synthetic lyrics',title='test',instrumental=False),[])
    value=json.loads(suno.payload)
    assert suno.url.endswith('/suno/submit/music') and value=={'prompt':'synthetic lyrics','tags':'synthetic brief','title':'test','make_instrumental':False,'mv':'upstream-explicit'}
    dash=adapter('newapi_dashscope_music_v1').prepare(request('music.generate',is_instrumental=True,format='wav'),[])
    assert dash.url.endswith('/easel/dashscope/api/v1/services/audio/music/generation')
    assert json.loads(dash.payload)=={'model':'upstream-explicit','input':{'prompt':'synthetic brief','is_instrumental':True,'format':'wav'}}
    for name in ['newapi_suno_v1','newapi_dashscope_music_v1']:
        with pytest.raises(PlatformError):adapter(name).prepare(request('music.generate',duration=30),[])


@pytest.mark.asyncio
async def test_suno_submit_is_string_id_then_exact_get_audio_sources_without_fake_clip_count():
    current=adapter('newapi_suno_v1');calls=[];saved=[]
    async def handler(value):
        calls.append((value.method,value.url.path,value.headers.get('authorization')))
        if value.method=='POST':return httpx.Response(200,json={'code':'success','data':'task_fixture'})
        if value.url.path=='/suno/fetch/task_fixture':return httpx.Response(200,json={'code':'success','data':{'task_id':'task_fixture','status':'SUCCESS','data':[{'id':'clip_one','audio_url':'http://127.0.0.1:19992/one.mp3','image_url':'http://evil.invalid/cover'}]}})
        return httpx.Response(200,content=b'synthetic-audio',headers={'content-type':'audio/mpeg'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        submitted=await current.submit(client,current.prepare(request('music.generate'),[]),request_id='local-only',max_bytes=4096,on_headers=lambda **v: append([],v),on_job=lambda v: append(saved,v))
        assert submitted.provider_job_id=='task_fixture' and submitted.observation is None
        observed=await current.query(client,submitted.provider_job_id,max_bytes=4096)
        assert observed.quantities=={} and observed.usage_hash is None and len(observed.sources)==1
        assert observed.sources[0].source_id=='clip_one'
        await current.retrieve(client,observed.sources[0],max_bytes=100)
    assert saved==['task_fixture'] and calls[-1]==('GET','/one.mp3',None)


@pytest.mark.asyncio
async def test_dashscope_known_completion_preserves_actual_usage_duration_and_does_not_infer_requested_duration():
    current=adapter('newapi_dashscope_music_v1');saved=[]
    async def handler(value):
        return httpx.Response(200,content=b'{"output":{"task_id":"task_fixture","task_status":"SUCCEEDED","finish_reason":"stop","audio":{"url":"http://127.0.0.1:19992/audio.wav"}},"usage":{"duration":1.0000000000000000000000000001}}',headers={'content-type':'application/json'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        submitted=await current.submit(client,current.prepare(request('music.generate'),[]),request_id='local-only',max_bytes=4096,on_headers=lambda **v: append([],v),on_job=lambda v: append(saved,v))
    assert saved==['task_fixture'] and submitted.observation.quantities=={'audio_ms':1001}
    assert submitted.observation.usage_hash and submitted.observation.response_hash
