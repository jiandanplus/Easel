"""Final serialized tool schemas through actual owned Pi/OpenAI SDK, no network."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess

import httpx
import pytest

from tests.test_central_media_host import Fixture,definition,setup
from tests.test_media_expansion import expanded,UploadFixture


ROOT=Path(__file__).resolve().parents[1]


def models():
    first=definition();first['id']='image-a'
    second=deepcopy(first);second['id']='image-b'
    edit=deepcopy(first);edit.update(id='image-edit',capability='image.edit')
    edit['limits'].update(max_assets=1,input_media={'mime_types':['image/png'],'max_files':1,'max_pixels':1048576})
    variation=deepcopy(edit);variation.update(id='image-variation',capability='image.variation')
    speech=expanded('audio.speech')
    music=expanded('music.generate')
    music['parameters']={'type':'object','properties':{'n':{'type':'integer','minimum':1,'maximum':2},
        'lyric':{'type':'string','maxLength':3000}},'required':[],'additionalProperties':False}
    video=expanded('video.generate')
    video['limits']['output_media'].update(mime_types=['video/mp4'],max_pixels=1048576)
    video['parameters']={'type':'object','properties':{'seconds':{'type':'integer','minimum':1,'maximum':5},
        'size':{'type':'string','enum':['32x32']}},'required':['seconds','size'],'additionalProperties':False}
    transcribe=UploadFixture('audio.transcribe').row
    enroll=UploadFixture('voice.enroll').row
    voice=expanded('voice.synthesize')
    return [first,second,edit,variation,speech,music,video,transcribe,enroll,voice]


def run_sdk(tmp_path,rows,arguments=None,*,tools=True):
    payload=os.environ.get('EASEL_PI_TEST_PAYLOAD')
    if not payload:pytest.skip('owned Pi runtime required for serialized tool schema acceptance')
    directory=Path(payload).resolve();node=directory/'node/bin/node'
    if not node.is_file():node=directory/'node'
    tmp_path.mkdir(exist_ok=True)
    shutil.copy2(ROOT/'runtime/pi/worker.mjs',tmp_path/'worker.mjs')
    (tmp_path/'node_modules').symlink_to(directory/'node_modules',target_is_directory=True)
    configuration={'models':rows,'tools_enabled':tools}
    if arguments is not None:configuration['arguments']=arguments
    path=tmp_path/'configuration.json';path.write_text(json.dumps(configuration))
    result=subprocess.run([str(node),str(Path(__file__).with_name('pi_media_root_schema_fixture.mjs')),
        str(tmp_path),str(path)],capture_output=True,text=True,timeout=20,env={'PATH':'/usr/bin:/bin'})
    assert result.returncode==0,result.stderr
    value=json.loads(result.stdout)
    (tmp_path/'serialized-safe.json').write_text(json.dumps(value,indent=2))
    assert value['real_network_requests']==0
    return value


def create_schema(value):
    return next(row['parameters']for row in value['schemas'][0]if row['name']=='create_media')


def test_actual_two_model_payload_passes_strict_object_root_gateway(tmp_path):
    value=run_sdk(tmp_path,models()[:2])
    assert value['statuses']==[200],value
    schema=create_schema(value)
    assert schema['type']=='object'and len(schema['anyOf'])==2
    assert value['done']and not value['host_requests']and not value['errors']


@pytest.mark.parametrize('count',[0,1,2,10])
def test_zero_single_and_multiple_model_payloads_keep_object_roots_and_variants(tmp_path,count):
    value=run_sdk(tmp_path,models()[:count])
    assert value['statuses']==[200]and value['done'],value
    tools={row['name']:row['parameters']for row in value['schemas'][0]}
    assert all(schema['type']=='object'for schema in tools.values())
    if count==0:assert 'create_media'not in tools;return
    schema=tools['create_media'];variants=schema.get('anyOf',[schema])
    assert len(variants)==count
    assert [branch['properties']['model']['const']for branch in variants]==[row['id']for row in models()[:count]]
    for branch,row in zip(variants,models()[:count]):
        assert branch['type']=='object'and branch['additionalProperties']is False
        parameter=dict(branch['properties']['parameters'])
        optional=parameter.pop('~optional',False)
        assert parameter==row['parameters']
        assert optional==(row['capability'].startswith('image.')or not row['parameters']['required'])
        assert branch['properties']['capability']['const']==row['capability']
        assert 'model'in branch['required']
        if row['capability']!='image.generate':assert 'capability'in branch['required']
        if row['capability']in{'image.edit','image.variation','audio.transcribe','voice.enroll'}:
            assert 'assets'in branch['required']and branch['properties']['assets']['minItems']==1
        if row['capability']=='voice.synthesize':assert 'voice_id'in branch['required']


def test_disabled_tools_do_not_send_media_schema(tmp_path):
    value=run_sdk(tmp_path,models()[:2],tools=False)
    assert value['statuses']==[200]and value['schemas']==[[]]and not value['host_requests']


VALID=[
 {'model':'image-a','prompt':'synthetic','parameters':{'n':1,'size':'1024x1024'}},
 {'model':'image-b','capability':'image.generate','prompt':'synthetic','parameters':{'n':1,'size':'1024x1024'}},
 {'model':'image-edit','capability':'image.edit','prompt':'synthetic','assets':['sample.png'],'parameters':{'n':1,'size':'1024x1024'}},
 {'model':'image-variation','capability':'image.variation','assets':['sample.png'],'parameters':{'n':1,'size':'1024x1024'}},
 {'model':'fixture-audio.speech','capability':'audio.speech','prompt':'synthetic','parameters':{'response_format':'wav'}},
 {'model':'fixture-music.generate','capability':'music.generate','prompt':'synthetic','parameters':{'n':2,'lyric':'合成歌词'}},
 {'model':'fixture-video.generate','capability':'video.generate','prompt':'synthetic','parameters':{'seconds':1,'size':'32x32'}},
 {'model':'fixture-audio.transcribe','capability':'audio.transcribe','assets':['sample.wav']},
 {'model':'fixture-voice.enroll','capability':'voice.enroll','assets':['sample.wav']},
 {'model':'fixture-voice.synthesize','capability':'voice.synthesize','prompt':'synthetic',
  'voice_id':'00000000-0000-4000-8000-000000000001','parameters':{'response_format':'wav'}},
]
INVALID=[
 {'model':'image-a','capability':'image.edit','prompt':'synthetic','assets':['sample.png'],'parameters':{'n':1,'size':'1024x1024'}},
 {'model':'image-a','prompt':'synthetic','parameters':{'n':2,'size':'1024x1024'}},
 {'model':'image-edit','capability':'image.edit','prompt':'synthetic','parameters':{'n':1,'size':'1024x1024'}},
 {'model':'image-variation','capability':'image.variation','assets':[],'parameters':{'n':1,'size':'1024x1024'}},
 {'model':'fixture-audio.speech','capability':'audio.speech','prompt':'synthetic','parameters':{'n':1,'size':'1024x1024'}},
 {'model':'fixture-music.generate','capability':'audio.speech','prompt':'synthetic','parameters':{'n':1}},
 {'model':'image-a','prompt':'synthetic','parameters':{'n':1,'size':'1024x1024'},'api_key':'forbidden'},
 {'model':'foreign','capability':'image.generate','prompt':'synthetic'},
 {'model':'fixture-voice.synthesize','capability':'voice.synthesize','prompt':'synthetic','parameters':{'response_format':'wav'}},
]


@pytest.mark.parametrize('arguments',VALID)
def test_actual_sdk_accepts_each_declared_model_capability_combination(tmp_path,arguments):
    value=run_sdk(tmp_path,models(),arguments)
    assert value['statuses']==[200,200]and value['done'],value
    assert len(value['host_requests'])==1
    assert value['host_requests'][0]['arguments']==arguments


@pytest.mark.parametrize('arguments',INVALID)
def test_actual_sdk_rejects_cross_model_capability_or_parameter_mixing(tmp_path,arguments):
    value=run_sdk(tmp_path,models(),arguments)
    assert value['statuses']==[200,200]and value['done'],value
    assert value['host_requests']==[]


@pytest.mark.parametrize('arguments',INVALID)
def test_actual_host_also_rejects_invalid_combinations_before_any_post(tmp_path,arguments):
    fixture=Fixture();rows=models()
    def handler(request):
        if request.url.path.endswith('/media-models'):
            fixture.calls.append(request);return httpx.Response(200,json={'models':rows})
        return fixture(request)
    _,host,_=setup(tmp_path,handler)
    result=host.execute('create_media',deepcopy(arguments),'synthetic-invalid')
    assert not result['ok']
    assert all(request.method=='GET'for request in fixture.calls)
    assert not list((tmp_path/'outputs').glob('*.png'))
