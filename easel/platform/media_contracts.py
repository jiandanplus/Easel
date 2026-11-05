"""Desktop-supported public media contracts. No supplier or credential fields."""
from copy import deepcopy
import re

CAPABILITIES=frozenset({'image.generate','image.edit','image.variation','video.generate','music.generate','audio.transcribe','audio.speech','voice.enroll','voice.synthesize'})
MAX_MEDIA=64*1024*1024
MIMES={'image/png':'PNG','image/jpeg':'JPEG','image/webp':'WEBP','video/mp4':'MP4','audio/mpeg':'MP3','audio/wav':'WAV','audio/ogg':'OGG','audio/mp4':'MP4'}
UNITS={'image':16,'audio_clip':32,'video_ms':900000,'audio_ms':86400000,'unicode_scalar':1048576,'registration':1,'input_token':1048576,'output_token':131072}
BASES={'verified_input_v1','verified_delivery_v1','provider_usage_v1'}
PARAMETERS={
 'image.generate':{'n','size','quality','response_format','background','output_format','output_compression'},
 'image.edit':{'n','size','quality','response_format','background','output_format','output_compression','input_fidelity'},
 'image.variation':{'n','size','response_format'},
 'video.generate':{'seconds','duration','duration_seconds','size','resolution','ratio','aspect_ratio','seed','prompt_extend','watermark'},
 'music.generate':{'lyrics','lyric','n','title','instrumental','is_instrumental','gender','format','enable_aigc_watermark'},
 'audio.speech':{'voice','response_format','speed_percent','instructions','reference_text','reference_text_2'},
 'audio.transcribe':{'language','response_format','prompt'},
 'voice.enroll':{'target_model','need_noise_reduction','need_volume_normalization','name','language'},
 'voice.synthesize':{'response_format','speed_percent','emotion','instructions'},
}


def integer(value, maximum, *, minimum=0):
    if type(value) is not int or not minimum<=value<=maximum:raise ValueError('Invalid media integer')
    return value


def validate_parameter(schema,value):
    kind=schema['type']
    if kind=='integer':integer(value,schema['maximum'],minimum=schema['minimum'])
    elif kind=='boolean':
        if type(value) is not bool:raise ValueError
    elif kind=='string':
        if not isinstance(value,str) or len(value)>schema.get('maxLength',65536):raise ValueError
        value.encode('utf-8')
    else:raise ValueError
    if 'enum' in schema and (value not in schema['enum'] or any(type(value) is not type(v) for v in schema['enum'])):raise ValueError


def parameter_schema(value,capability):
    if (not isinstance(value,dict) or set(value)-{'type','properties','required','additionalProperties'}
        or value.get('type')!='object' or value.get('additionalProperties') is not False
        or not isinstance(value.get('properties'),dict) or set(value['properties'])-PARAMETERS[capability]):raise ValueError
    required=value.get('required',[])
    if not isinstance(required,list) or any(not isinstance(k,str) or k not in value['properties'] for k in required) or len(set(required))!=len(required):raise ValueError
    for key,part in value['properties'].items():
        if not isinstance(part,dict):raise ValueError
        kind=part.get('type');allowed={'type','enum'}|({'maxLength'} if kind=='string' else {'minimum','maximum'} if kind=='integer' else set())
        if kind not in {'string','integer','boolean'} or set(part)-allowed:raise ValueError
        if kind=='string':
            if 'enum' not in part and 'maxLength' not in part:raise ValueError
            if 'maxLength' in part:integer(part['maxLength'],65536,minimum=1)
        if kind=='integer':
            integer(part['minimum'],100000000);integer(part['maximum'],100000000,minimum=part['minimum'])
        if 'enum' in part:
            if not isinstance(part['enum'],list) or not 1<=len(part['enum'])<=64:raise ValueError
            for choice in part['enum']:validate_parameter(part,choice)
    return deepcopy(value)


def parameters(value,schema):
    if not isinstance(value,dict) or set(value)-set(schema['properties']) or not set(schema.get('required',[])).issubset(value):raise ValueError
    for key,item in value.items():validate_parameter(schema['properties'][key],item)
    return deepcopy(value)


def resource(value):
    fields={'mime_types','max_files','max_duration_ms','max_pixels','max_channels','max_sample_rate','require_audio'}
    if not isinstance(value,dict) or set(value)-fields or not {'mime_types','max_files'}.issubset(value):raise ValueError
    mimes=value['mime_types']
    if not isinstance(mimes,list) or not 1<=len(mimes)<=8 or any(m not in MIMES for m in mimes) or len(set(mimes))!=len(mimes):raise ValueError
    av=any(m.startswith(('audio/','video/')) for m in mimes)
    if av and not {'max_duration_ms','max_channels','max_sample_rate','require_audio'}.issubset(value):raise ValueError
    if any(m.startswith(('image/','video/')) for m in mimes) and 'max_pixels' not in value:raise ValueError
    for key,cap in [('max_files',32),('max_duration_ms',900000),('max_pixels',33554432),('max_channels',8),('max_sample_rate',192000)]:
        if key in value:integer(value[key],cap,minimum=8000 if key=='max_sample_rate' else 1)
    if 'require_audio' in value and type(value['require_audio']) is not bool:raise ValueError
    return deepcopy(value)


def public_definition(row):
    capability=row['capability']
    if capability not in CAPABILITIES:raise ValueError
    result={key:row[key] for key in ('id','name','version','capability','test_only','readiness')}
    for key in ('id','version'):
        if not isinstance(result[key],str) or not re.fullmatch(r'[\x21-\x7e]{1,240}',result[key]):raise ValueError
    if not isinstance(row['name'],str) or not 1<=len(row['name'])<=240 or any(ord(c)<32 for c in row['name']):raise ValueError
    if type(row['test_only']) is not bool or row['readiness'] not in {'live','test'} or row['test_only']!=(row['readiness']=='test'):raise ValueError
    rate=row['rate'];meters=rate['meters']
    if not isinstance(meters,list) or not 1<=len(meters)<=8 or not isinstance(rate['version'],str) or not re.fullmatch(r'[\x21-\x7e]{1,240}',rate['version']):raise ValueError
    seen=set()
    for meter in meters:
        if not isinstance(meter,dict) or set(meter)!={'unit','basis','numerator','denominator'} or meter['unit'] not in UNITS or meter['basis'] not in BASES or meter['unit'] in seen:raise ValueError
        seen.add(meter['unit'])
        for key in ('numerator','denominator'):
            if not isinstance(meter[key],str) or not re.fullmatch(r'[0-9]{1,19}',meter[key]) or not 0<int(meter[key])<=2**63-1:raise ValueError
    limits=row['limits']
    if not isinstance(limits,dict) or set(limits)-{'max_input_bytes','max_assets','max_artifact_bytes','max_units','input_media','output_media'}:raise ValueError
    integer(limits['max_input_bytes'],MAX_MEDIA,minimum=1);integer(limits['max_artifact_bytes'],MAX_MEDIA,minimum=1);integer(limits['max_assets'],32)
    if not isinstance(limits['max_units'],dict) or set(limits['max_units'])!=seen:raise ValueError
    for key,amount in limits['max_units'].items():integer(amount,UNITS[key],minimum=1)
    for direction in ('input_media','output_media'):
        if direction in limits:resource(limits[direction])
    incoming=limits.get('input_media');outgoing=limits.get('output_media')
    if limits['max_assets'] and incoming is None and not capability.startswith('image.'):raise ValueError
    if capability in {'video.generate','music.generate','audio.speech','voice.synthesize'}:
        prefix='video/' if capability=='video.generate' else 'audio/'
        if not outgoing or any(not m.startswith(prefix) for m in outgoing['mime_types']):raise ValueError
    if capability in {'audio.transcribe','voice.enroll'} and not incoming:raise ValueError
    if incoming:
        prefixes=('image/',) if capability.startswith('image.') or capability=='video.generate' else ('audio/','video/') if capability=='audio.transcribe' else ('audio/',)
        if any(not m.startswith(prefixes) for m in incoming['mime_types']):raise ValueError
    schema=parameter_schema(row['parameters'],capability)
    if capability.startswith('image.'):
        # Existing single-image safety boundary remains in every image bucket.
        if limits['max_units'].get('image')!=1 or limits['max_artifact_bytes']>20*1024*1024:raise ValueError
        if schema['properties'].get('n')!={'type':'integer','minimum':1,'maximum':1} or schema['properties'].get('size')!={'type':'string','enum':['1024x1024']}:raise ValueError
    public_rate = {'version': rate['version'], 'meters': [
        {key: meter[key] for key in ('unit', 'basis', 'numerator', 'denominator')}
        for meter in meters]}
    result.update(rate=public_rate,limits=deepcopy(limits),parameters=schema)
    return result


def rate_description(row):
    return '; '.join(f"{m['numerator']}/{m['denominator']} microcredits per {m['unit']} ({m['basis']})" for m in row['rate']['meters'])+'; sum exact quantities, round up once; 1000000 microcredits = 1 point'
