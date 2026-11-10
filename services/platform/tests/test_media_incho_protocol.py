"""Locked Incho native protocol: no Suno inference or implicit model downgrade."""
import json

import httpx
import pytest

from platform_service.errors import PlatformError
from platform_service.media.adapters.newapi_async import make_async_adapter
from platform_service.media_config import MediaProvider, MediaSettings
from platform_service.media_runtime import allowed_meters


def provider(*, model='v3.5', base='http://127.0.0.1:19991', origins=('http://127.0.0.1:19992',), paths=('/audio/',)):
    return MediaProvider('1', base, 'synthetic-only', {'incho-fixture': model},
        adapter='newapi_incho_v1', capabilities=('music.generate',),
        download_origins=origins, download_path_prefixes=paths)


def adapter(*, model='v3.5', n=None, **kwargs):
    job=None if n is None else {'request': request(n=n)}
    return make_async_adapter(provider(model=model, **kwargs), 'test', job)


def request(**parameters):
    return {'model':'incho-fixture','capability':'music.generate',
            'prompt':'  合成器与钢琴。  ','assets':[],'parameters':parameters}


def envelope(choices, *, state='SUCCESS', task='gateway-task'):
    return {'code':'success','data':{'task_id':task,'status':state,
            'data':{'id':'vendor-task','task_type':'normal','choices':choices}}}


def song(identifier='song-one', status='done', url='http://127.0.0.1:19992/audio/one.mp3'):
    return {'id':identifier,'status':status,'audio_url':url,'duration':999999,
            'lyric':'private lyric','image_url':'https://evil.invalid/cover.png'}


def test_incho_native_prepare_is_explicit_vendor_model_and_count():
    prepared=adapter().prepare(request(lyric='  合成歌词  ',n=1),[])
    assert prepared.url=='http://127.0.0.1:19991/incho/submit/music'
    assert prepared.upstream_model=='v3.5'
    assert prepared.content_type=='application/json'
    assert json.loads(prepared.payload)=={'model':'v3.5','task_type':'normal','n':1,
                                         'prompt':'合成器与钢琴。','lyric':'合成歌词'}
    assert json.loads(adapter(model='v4.0').prepare(request(),[]).payload)['n']==2


@pytest.mark.parametrize('params', [{'n':True},{'n':'1'},{'n':0},{'n':3},
    {'duration':30},{'task_type':'extend'},{'model':'v4.5'},{'lyric':4},
    {'lyric':'x'*3001},{'lyric':'\ud800'}])
def test_incho_rejects_unverified_or_out_of_contract_parameters(params):
    with pytest.raises(PlatformError):adapter().prepare(request(**params),[])


def test_incho_prompt_is_unicode_scalar_bounded_and_assets_are_rejected():
    value=request();value['prompt']='🎹'*1000
    assert json.loads(adapter().prepare(value,[]).payload)['prompt']==value['prompt']
    for prompt in ['🎹'*1001,'\ud800','   ']:
        value['prompt']=prompt
        with pytest.raises(PlatformError):adapter().prepare(value,[])
    with pytest.raises(PlatformError):adapter().prepare(request(),[{'raw':b'no-reference-mode'}])


@pytest.mark.parametrize('model', ['v4.5','incho_music','arbitrary','V3.5',None,['v3.5'],{'model':'v3.5'}])
def test_incho_unknown_vendor_model_stays_closed_without_downgrade(model):
    with pytest.raises((ValueError,PlatformError)):
        MediaSettings(mode='test',storage_root='/private/tmp/incho-fixture-never-written',
                      providers={'fixture':provider(model=model)})


def test_incho_requires_root_provider_url_and_supports_only_verified_audio_meters():
    with pytest.raises((ValueError,PlatformError)):
        MediaSettings(mode='test',storage_root='/private/tmp/incho-fixture-never-written',
                      providers={'fixture':provider(base='http://127.0.0.1:19991/v1')})
    assert allowed_meters(provider())==frozenset({('audio_ms','verified_delivery_v1'),
                                               ('audio_clip','verified_delivery_v1')})


def test_desktop_accepts_only_declared_typed_incho_music_parameters():
    from easel.platform.media_contracts import parameter_schema,parameters
    schema={'type':'object','properties':{'n':{'type':'integer','minimum':1,'maximum':2},
        'lyric':{'type':'string','maxLength':3000}},'required':[],'additionalProperties':False}
    published=parameter_schema(schema,'music.generate')
    assert parameters({'n':2,'lyric':'合成歌词'},published)=={'n':2,'lyric':'合成歌词'}
    for invalid in [{'n':0},{'n':3},{'n':True},{'n':'1'},{'lyric':4},
                    {'lyric':'x'*3001},{'lyric':'\ud800'},{'model':'v4.5'}]:
        with pytest.raises((ValueError,UnicodeError)):parameters(invalid,published)


@pytest.mark.asyncio
async def test_incho_submit_associates_once_then_only_original_get_with_two_audio_sources():
    current=adapter(n=2);seen=[];saved=[]
    async def append(value):saved.append(value)
    async def headers(**value):pass
    def handler(req):
        seen.append((req.method,req.url.path,req.headers.get('authorization')))
        if req.method=='POST':return httpx.Response(200,json={'code':'success','data':'gateway-task'})
        if req.url.path=='/incho/fetch/gateway-task':
            return httpx.Response(200,json=envelope([song(),song('song-two',url='http://127.0.0.1:19992/audio/two.mp3')]))
        return httpx.Response(200,content=b'controlled-audio',headers={'content-type':'audio/mpeg'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler),follow_redirects=False)as client:
        submitted=await current.submit(client,current.prepare(request(n=2),[]),request_id='synthetic',
            max_bytes=8192,on_headers=headers,on_job=append)
        assert submitted.observation is None
        observed=await current.query(client,submitted.provider_job_id,max_bytes=8192)
        assert observed.state=='completed' and len(observed.sources)==2
        assert observed.quantities=={} and observed.usage_hash is None
        assert {source.source_id for source in observed.sources}=={'song-one','song-two'}
        for source in observed.sources:await current.retrieve(client,source,max_bytes=100)
    assert saved==['gateway-task']
    assert seen==[('POST','/incho/submit/music','Bearer synthetic-only'),
        ('GET','/incho/fetch/gateway-task','Bearer synthetic-only'),
        ('GET','/audio/one.mp3',None),('GET','/audio/two.mp3',None)]


def test_incho_partial_completion_only_delivers_done_tracks_not_failed_urls():
    observed=adapter(n=2).observe(envelope([song(),song('failed','fail')]),'a'*64,'gateway-task')
    assert observed.state=='completed' and len(observed.sources)==1
    assert observed.sources[0].source_id=='song-one'


@pytest.mark.parametrize('choices,state,expected', [
    ([], 'QUEUED','queued'),([song(status='pending')],'QUEUED','queued'),
    ([song(status='running')],'IN_PROGRESS','running'),
    ([song(status='stream')],'IN_PROGRESS','running'),
    ([song(status='fail')],'FAILURE','failed'),
    ([song(status='cancelled')],'FAILURE','failed')])
def test_incho_noncompleted_states_have_no_sources_or_inferred_cost(choices,state,expected):
    result=adapter().observe(envelope(choices,state=state),'a'*64,'gateway-task')
    assert result.state==expected and not result.sources and result.quantities=={}


@pytest.mark.parametrize('choices,state', [
    ([song(status='pending')],'SUCCESS'),([song(status='done')],'IN_PROGRESS'),
    ([song(status='unknown')],'IN_PROGRESS'),([song(url='')],'SUCCESS'),
    ([song(),song()],'SUCCESS'),([song(),song('two'),song('three')],'SUCCESS'),
    ([song(),song('two')],'SUCCESS')])
def test_incho_ambiguous_or_over_count_result_is_rejected(choices,state):
    with pytest.raises(PlatformError):adapter(n=1).observe(envelope(choices,state=state),'a'*64,'gateway-task')


@pytest.mark.parametrize('url', ['http://127.0.0.1:19993/audio/a.mp3',
    'http://127.0.0.1:19992/private/a.mp3','http://127.0.0.1:19992/audio/%2e%2e/a.mp3',
    'http://127.0.0.1:19992/audio/../private.mp3','http://user:pass@127.0.0.1:19992/audio/a.mp3'])
def test_incho_download_sources_need_exact_origin_and_path(url):
    with pytest.raises(PlatformError):adapter().observe(envelope([song(url=url)]),'a'*64,'gateway-task')


@pytest.mark.asyncio
async def test_incho_fetch_requires_original_public_id():
    current=adapter()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(200,
            json=envelope([song()],task='other-task'))))as client:
        with pytest.raises(PlatformError)as error:await current.query(client,'gateway-task',max_bytes=8192)
    assert error.value.code=='media_provider_job_mismatch'


@pytest.mark.parametrize('value', [{},[],True,None])
def test_incho_untyped_task_or_song_status_is_a_safe_protocol_error(value):
    for result in [envelope([song()],state=value),envelope([song(status=value)])]:
        with pytest.raises(PlatformError):adapter().observe(result,'a'*64,'gateway-task')
