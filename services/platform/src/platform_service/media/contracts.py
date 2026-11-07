"""Strict public media request and immutable definition contracts.

These values grant no provider access. A published definition is closed until an
operator separately enables it and the private runtime binding matches.
"""
import copy
import hashlib
import json
import re
from ..credits import bounded, integer
from ..errors import PlatformError
from .rates import normalize, cost, unicode_scalars

CAPABILITIES=frozenset({'image.generate','image.edit','image.variation','video.generate','music.generate','audio.transcribe','audio.speech','voice.enroll','voice.synthesize'})
UNIT_LIMITS={'audio_clip':32,'image':16,'video_ms':900000,'audio_ms':86400000,'unicode_scalar':1048576,'registration':1,'input_token':1048576,'output_token':131072}
MAX_OPERATIONS=32
MAX_BUDGET=100000000


def canonical(value):
    try:return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')
    except (TypeError,ValueError,UnicodeError):raise PlatformError(422,'invalid_media_request') from None


def digest(value):return hashlib.sha256(canonical(value)).hexdigest()


def hash_value(value):
    if not isinstance(value,str) or not re.fullmatch('[a-f0-9]{64}',value):
        raise PlatformError(422,'invalid_media_evidence')
    return value


def identifier(value):
    if not isinstance(value,str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}',value,re.ASCII):
        raise PlatformError(422,'invalid_media_identifier')
    return value


def parameter_schema(value):
    if (not isinstance(value,dict) or set(value)-{'type','properties','required','additionalProperties'}
            or value.get('type')!='object' or value.get('additionalProperties') is not False
            or not isinstance(value.get('properties'),dict) or len(value['properties'])>32):
        raise PlatformError(422,'invalid_media_parameters')
    properties=value['properties'];required=value.get('required',[])
    if not isinstance(required,list) or any(not isinstance(key,str) or key not in properties for key in required) or len(set(required))!=len(required):
        raise PlatformError(422,'invalid_media_parameters')
    for key,part in properties.items():
        identifier(key)
        if not isinstance(part,dict) or part.get('type') not in {'string','integer','boolean'}:
            raise PlatformError(422,'invalid_media_parameters')
        kind=part['type'];allowed={'type','enum'}|({'maxLength'} if kind=='string' else {'minimum','maximum'} if kind=='integer' else set())
        if set(part)-allowed:raise PlatformError(422,'invalid_media_parameters')
        if kind=='string':
            if 'maxLength' not in part and 'enum' not in part:raise PlatformError(422,'invalid_media_parameters')
            if 'maxLength' in part and integer(part['maxLength'],positive=True)>65536:raise PlatformError(422,'invalid_media_parameters')
        elif kind=='integer':
            if not {'minimum','maximum'}.issubset(part) or integer(part['minimum'])>integer(part['maximum']) or part['maximum']>100000000:
                raise PlatformError(422,'invalid_media_parameters')
        if 'enum' in part:
            choices=part['enum']
            if not isinstance(choices,list) or not 1<=len(choices)<=64 or len({canonical(item) for item in choices})!=len(choices):
                raise PlatformError(422,'invalid_media_parameters')
            for choice in choices:
                validate_parameter(part,choice)
    return copy.deepcopy(value)


def validate_parameter(part,value):
    kind=part['type']
    if kind=='boolean':valid=type(value) is bool
    elif kind=='integer':valid=type(value) is int and part['minimum']<=value<=part['maximum']
    else:valid=isinstance(value,str) and unicode_scalars(value)<=part.get('maxLength',65536)
    if not valid or 'enum' in part and value not in part['enum']:
        raise PlatformError(422,'invalid_media_parameters')


RESOURCE_MIMES=frozenset({'image/png','image/jpeg','image/webp','video/mp4','audio/mpeg','audio/wav','audio/ogg','audio/mp4'})


def media_resource(value):
    fields={'mime_types','max_files','max_duration_ms','max_pixels','max_channels','max_sample_rate','require_audio'}
    if not isinstance(value,dict) or set(value)-fields or not {'mime_types','max_files'}.issubset(value):
        raise PlatformError(422,'invalid_media_resource')
    mimes=value['mime_types']
    if (not isinstance(mimes,list) or not 1<=len(mimes)<=8 or any(not isinstance(m,str) or m not in RESOURCE_MIMES for m in mimes)
        or len(set(mimes))!=len(mimes)):
        raise PlatformError(422,'invalid_media_resource')
    video=any(m.startswith('video/') for m in mimes);audio=any(m.startswith('audio/') for m in mimes)
    visual=video or any(m.startswith('image/') for m in mimes)
    required={'max_files'}|({'max_duration_ms','max_channels','max_sample_rate','require_audio'} if video or audio else set())|({'max_pixels'} if visual else set())
    if not required.issubset(value):raise PlatformError(422,'invalid_media_resource')
    for key,cap in [('max_files',32),('max_duration_ms',900000),('max_pixels',33554432),('max_channels',8),('max_sample_rate',192000)]:
        if key in value and integer(value[key],positive=True)>cap:raise PlatformError(422,'invalid_media_resource')
    if 'max_sample_rate' in value and value['max_sample_rate']<8000:raise PlatformError(422,'invalid_media_resource')
    if 'require_audio' in value and type(value['require_audio']) is not bool:raise PlatformError(422,'invalid_media_resource')
    if not (audio or video) and value.get('require_audio'):raise PlatformError(422,'invalid_media_resource')
    return copy.deepcopy(value)


def media_resources_ready(definition):
    capability=definition.get('capability');limits=definition.get('limits',{})
    incoming=limits.get('input_media');outgoing=limits.get('output_media')
    try:
        if incoming is not None:media_resource(incoming)
        if outgoing is not None:media_resource(outgoing)
    except PlatformError:return False
    if capability in {'video.generate','music.generate','audio.speech','voice.synthesize'}:
        if outgoing is None:return False
        prefix='video/' if capability=='video.generate' else 'audio/'
        if any(not mime.startswith(prefix) for mime in outgoing['mime_types']):return False
    if capability in {'audio.transcribe','voice.enroll'} or (capability in {'video.generate','music.generate','audio.speech','voice.synthesize'} and limits.get('max_assets',0)>0):
        if incoming is None:return False
        allowed=('image/',) if capability=='video.generate' else ('audio/','video/') if capability=='audio.transcribe' else ('audio/',)
        if any(not mime.startswith(allowed) for mime in incoming['mime_types']):return False
        if capability=='audio.transcribe' and any(m.startswith('video/') for m in incoming['mime_types']) and incoming.get('require_audio') is not True:return False
    if capability in {'image.generate','image.edit','image.variation'}:
        if any(any(not mime.startswith('image/') for mime in spec['mime_types']) for spec in (incoming,outgoing) if spec is not None):return False
    return True

def live_feature_allowed(value):
    return value.get('test_only') is True or not str(value.get('capability','')).startswith('image.') or value.get('feature')=='image'


def validate_definition(value):
    fields={'id','version','name','capability','feature','products','test_only','provider_id','provider_version','adapter','protocol_version','verification','verification_ref','rate','limits','parameters'}
    if not isinstance(value,dict) or set(value)!=fields:raise PlatformError(422,'invalid_media_definition')
    result=copy.deepcopy(value)
    for key in fields-{'products','test_only','rate','limits','parameters'}:bounded(result[key])
    identifier(result['id']);identifier(result['adapter'])
    if (result['capability'] not in CAPABILITIES or result['protocol_version']!='1' or type(result['test_only']) is not bool
            or result['verification']!=('fixture' if result['test_only'] else 'live') or not live_feature_allowed(result)):
        raise PlatformError(422,'invalid_media_definition')
    products=result['products']
    if not isinstance(products,list) or not 1<=len(products)<=64 or any(not isinstance(p,str) for p in products) or len(set(products))!=len(products):raise PlatformError(422,'invalid_media_definition')
    for product in products:identifier(product)
    result['rate']=normalize(result['rate']);limits=result['limits']
    if not isinstance(limits,dict) or set(limits)-{'max_input_bytes','max_assets','max_artifact_bytes','max_units','input_media','output_media'} or not {'max_input_bytes','max_assets','max_artifact_bytes','max_units'}.issubset(limits):
        raise PlatformError(422,'invalid_media_limits')
    for name,cap in [('max_input_bytes',67108864),('max_assets',32),('max_artifact_bytes',67108864)]:
        if integer(limits[name],positive=name!='max_assets')>cap:raise PlatformError(422,'invalid_media_limits')
    for direction in ('input_media','output_media'):
        if direction in limits:media_resource(limits[direction])
    units={part['unit'] for part in result['rate']['meters']}
    if not isinstance(limits['max_units'],dict) or set(limits['max_units'])!=units:raise PlatformError(422,'invalid_media_limits')
    for unit,amount in limits['max_units'].items():
        if integer(amount,positive=True)>UNIT_LIMITS[unit]:raise PlatformError(422,'invalid_media_limits')
    budget=cost(result['rate'],limits['max_units'])
    if not 0<budget<=MAX_BUDGET:raise PlatformError(422,'invalid_media_limits')
    result['parameters']=parameter_schema(result['parameters'])
    return result


def validate_request(definition,value):
    if not isinstance(value,dict) or set(value)-{'model','capability','prompt','assets','voice_id','parameters'}:
        raise PlatformError(422,'invalid_media_request')
    if value.get('model')!=definition['id'] or value.get('capability')!=definition['capability']:
        raise PlatformError(422,'media_capability_mismatch')
    prompt=value.get('prompt','')
    unicode_scalars(prompt)
    assets=value.get('assets',[])
    if not isinstance(assets,list) or len(assets)>definition['limits']['max_assets'] or any(not isinstance(a,str) for a in assets) or len(set(assets))!=len(assets):raise PlatformError(422,'invalid_media_assets')
    for asset in assets:identifier(asset)
    voice=value.get('voice_id')
    if voice is not None:identifier(voice)
    if definition['capability'] in {'image.edit','image.variation','audio.transcribe','voice.enroll'} and not assets:raise PlatformError(422,'media_asset_required')
    if definition['capability'] not in {'audio.transcribe','voice.enroll','image.variation'} and not prompt.strip():raise PlatformError(422,'media_prompt_required')
    params=value.get('parameters',{})
    if not isinstance(params,dict):raise PlatformError(422,'invalid_media_parameters')
    schema=definition['parameters']
    if set(params)-set(schema['properties']) or not set(schema.get('required',[])).issubset(params):raise PlatformError(422,'invalid_media_parameters')
    for key,item in params.items():validate_parameter(schema['properties'][key],item)
    result={'model':value['model'],'capability':value['capability'],'prompt':prompt,'assets':list(assets),'parameters':copy.deepcopy(params)}
    if voice is not None:result['voice_id']=voice
    if len(canonical(result))>definition['limits']['max_input_bytes']:raise PlatformError(413,'media_input_too_large')
    return result
