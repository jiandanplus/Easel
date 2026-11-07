"""Explicit adapters for the locked Easel New API private voice bridges.

Each call sends at most one POST. Multi-step registration is orchestrated by
VoiceStages; this module never retries a paid operation or falls back to another
producer. Gateway task references are not supplier voice IDs or usage units.
"""
from dataclasses import dataclass,field
from datetime import datetime,timezone
from decimal import Decimal
import base64
import hashlib
import json
import re
import secrets
import time

import httpx

from ...errors import PlatformError
from ...execution_protocol import _unique
from ...media_config import origin
from ..contracts import hash_value
from ..rates import unicode_scalars
from ..voices import OwnedVoiceReference
from .base import MediaAdapter,PreparedRequest
from .openai_images import bounded_response

_TASK=re.compile(r'task_[A-Za-z0-9]{32}\Z',re.ASCII)
_ID=re.compile(r'[A-Za-z0-9_.:-]{1,160}\Z',re.ASCII)
_MIME={'mp3':'audio/mpeg','wav':'audio/wav'}
_SAMPLE={'audio/mpeg':('MP3','.mp3'),'audio/wav':('WAV','.wav'),'audio/mp4':('MP4','.m4a')}
_MINIMAX=frozenset('easel-minimax-'+v for v in ('speech-2.8-hd','speech-2.8-turbo','speech-2.6-hd','speech-2.6-turbo',
    'speech-02-hd','speech-02-turbo','speech-01-hd','speech-01-turbo'))
_DASHSCOPE=frozenset({'cosyvoice-v3.5-plus','cosyvoice-v3.5-flash','cosyvoice-v3-plus','cosyvoice-v3-flash','cosyvoice-v2'})
_FISH=frozenset('easel-fish-'+v for v in ('s2-pro','s2.1-pro','s2.1-pro-free','drama-3'))


@dataclass(frozen=True)
class BridgeVoiceProfile:
    target_models: tuple[str,...] = ()
    presets: tuple[tuple[str,str],...] = field(default=(),repr=False)
    formats: tuple[str,...] = ('mp3','wav')

    def __post_init__(self):
        if (not isinstance(self.target_models,tuple) or len(self.target_models)>32
                or any(not isinstance(v,str) or not _ID.fullmatch(v) for v in self.target_models)
                or len(set(self.target_models))!=len(self.target_models)
                or not isinstance(self.formats,tuple) or not self.formats or any(not isinstance(v,str) or v not in _MIME for v in self.formats)
                or not isinstance(self.presets,tuple) or len(self.presets)>32):raise PlatformError(503,'invalid_voice_profile')
        aliases=set()
        for pair in self.presets:
            if not isinstance(pair,tuple) or len(pair)!=2 or any(not isinstance(v,str) or not _ID.fullmatch(v) for v in pair) or pair[0] in aliases:
                raise PlatformError(503,'invalid_voice_profile')
            aliases.add(pair[0])


@dataclass(frozen=True)
class GatewayTaskReference:
    namespace: str
    task_id: str

    def __post_init__(self):
        if self.namespace not in {'newapi_minimax_voice_v1','newapi_dashscope_voice_v1','newapi_fish_voice_v1'} or not isinstance(self.task_id,str) or not _TASK.fullmatch(self.task_id):
            raise PlatformError(502,'invalid_voice_response')


@dataclass(frozen=True)
class PreparedVoiceRequest(PreparedRequest):
    stage: str
    output_format: str | None = None
    target_model: str | None = None


@dataclass(frozen=True)
class BridgeVoiceResult:
    reference: GatewayTaskReference = field(repr=False)
    state: str
    evidence_hash: str
    facts: dict = field(default_factory=dict,repr=False)
    audio: bytes | None = field(default=None,repr=False)
    mime: str | None = None
    descriptor: dict | None = field(default=None,repr=False)
    quantities: dict[str,int] = field(default_factory=dict)
    reported_facts: dict = field(default_factory=dict,repr=False)


def _encode(value):
    # Decimal is emitted as a JSON numeric literal, never converted to float.
    if isinstance(value,Decimal):
        if not value.is_finite():raise PlatformError(422,'invalid_voice_parameters')
        return format(value,'f').encode()
    if isinstance(value,dict):return b'{'+b','.join(_encode(k)+b':'+_encode(v) for k,v in value.items())+b'}'
    if isinstance(value,list):return b'['+b','.join(_encode(v) for v in value)+b']'
    try:return json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
    except (TypeError,ValueError,UnicodeError):raise PlatformError(422,'invalid_voice_parameters') from None


def _task(value):
    if not isinstance(value,str) or not _TASK.fullmatch(value):raise PlatformError(422,'invalid_voice_reference')
    return value


def _integer(value,maximum=9223372036854775807):
    if type(value) is not int or not 0<=value<=maximum:raise PlatformError(502,'invalid_voice_usage')
    return value


def _text(value,maximum):
    unicode_scalars(value)
    if not value.strip() or sum(2 if ord(c)>65535 else 1 for c in value)>maximum:raise PlatformError(422,'invalid_voice_parameters')
    return value


def _sample(value,*,limit,minimum=1,maximum=300000):
    if not isinstance(value,dict) or value.get('mime') not in _SAMPLE:raise PlatformError(422,'invalid_voice_assets')
    raw=value.get('raw');properties=value.get('properties',{})
    if (not isinstance(raw,bytes) or not 0<len(raw)<=limit or value.get('size')!=len(raw)
            or value.get('sha256')!=hashlib.sha256(raw).hexdigest() or not isinstance(properties,dict)
            or properties.get('format')!=_SAMPLE[value['mime']][0]
            or type(properties.get('duration_ms')) is not int or not minimum<=properties['duration_ms']<=maximum
            or type(properties.get('channels')) is not int or not 1<=properties['channels']<=8
            or type(properties.get('sample_rate')) is not int or not 8000<=properties['sample_rate']<=192000):
        raise PlatformError(422,'invalid_voice_assets')
    return raw


class _PrivateVoice(MediaAdapter):
    supported_meters=frozenset({('unicode_scalar','verified_input_v1'),('audio_ms','verified_delivery_v1')})
    paths: dict
    targets: frozenset

    def __init__(self,provider,mode,profile,*,binding_hash,provider_scope_hash,clock=time.time,voice_boundary_hash=None):
        if not isinstance(profile,BridgeVoiceProfile) or getattr(provider,'adapter',None)!=self.adapter_id:raise PlatformError(503,'invalid_voice_profile')
        try:origin(provider.base_url,mode=mode)
        except (ValueError,AttributeError):raise PlatformError(503,'invalid_voice_profile') from None
        if (not isinstance(provider.secret,str) or not 1<=len(provider.secret)<=4096 or not provider.secret.isascii()
                or any(not 33<=ord(c)<=126 for c in provider.secret)):
            raise PlatformError(503,'invalid_voice_profile')
        hash_value(binding_hash);hash_value(provider_scope_hash)
        self.provider,self.mode,self.profile=provider,mode,profile
        self.binding_hash,self.provider_scope_hash,self.clock=binding_hash,provider_scope_hash,clock
        self.voice_boundary_hash=voice_boundary_hash
        self.base=provider.base_url.rstrip('/')

    def _model(self,request,capability):
        if (not isinstance(request,dict) or request.get('capability')!=capability or capability not in self.provider.capabilities
                or not isinstance(request.get('model'),str)):
            raise PlatformError(503,'voice_capability_closed')
        model=self.provider.models.get(request['model'])
        if model not in self.targets or model not in self.profile.target_models:raise PlatformError(503,'voice_capability_closed')
        return model

    def _owned(self,request,model,voice):
        if (not isinstance(voice,OwnedVoiceReference) or request.get('voice_id')!=voice.id or voice.protocol!=self.adapter_id
                or voice.target_model!=model or voice.provider_version!=self.provider.version
                or (voice.binding_hash!=self.binding_hash if self.voice_boundary_hash is None else voice.voice_boundary_hash!=self.voice_boundary_hash) or voice.provider_scope_hash!=self.provider_scope_hash
                or voice.expires_at<=datetime.now(timezone.utc)):
            raise PlatformError(403,'voice_binding_mismatch')
        return _task(voice.provider_reference)

    def _prepared(self,stage,payload,model,*,content_type='application/json',output_format=None,target_model=None):
        return PreparedVoiceRequest(self.base+self.paths[stage],payload,content_type,model,stage,output_format,target_model or model)

    def _parameters(self,request,allowed):
        value=request.get('parameters',{})
        if not isinstance(value,dict) or set(value)-allowed:raise PlatformError(422,'invalid_voice_parameters')
        return value

    def _speed(self,parameters):
        value=parameters.get('speed_percent',100)
        if type(value) is not int or not 50<=value<=200:raise PlatformError(422,'invalid_voice_parameters')
        return Decimal(value)/100

    async def _json(self,client,method,url,*,payload=None,content_type=None,request_id=None,max_bytes=1048576,on_headers=None,allowed_status=(200,)):
        if type(max_bytes) is not int or not 1<=max_bytes<=2*1024*1024:raise PlatformError(422,'invalid_voice_limit')
        headers={'Authorization':'Bearer '+self.provider.secret,'Accept':'application/json','Accept-Encoding':'identity'}
        if content_type:headers['Content-Type']=content_type
        if request_id is not None:
            if not isinstance(request_id,str) or not _ID.fullmatch(request_id):raise PlatformError(422,'invalid_voice_request')
            headers['X-Client-Request-Id']=request_id
        try:
            async with client.stream(method,url,content=payload,headers=headers,follow_redirects=False) as response:
                header_id=response.headers.get('x-request-id')
                if on_headers:await on_headers(http_status=response.status_code,**({'http_request_id':header_id} if isinstance(header_id,str) and _ID.fullmatch(header_id) else {}))
                if (response.status_code not in allowed_status or response.headers.get('content-encoding','identity').lower()!='identity'
                        or response.headers.get('content-type','').split(';',1)[0].lower()!='application/json'):
                    raise PlatformError(502,'voice_upstream_error')
                declared=response.headers.get('content-length')
                maximum=min(max_bytes,1048576)
                if declared is not None and (not declared.isascii() or not declared.isdecimal() or int(declared)>maximum):raise PlatformError(502,'voice_response_too_large')
                data=bytearray()
                async for chunk in response.aiter_bytes():
                    if len(data)+len(chunk)>maximum:raise PlatformError(502,'voice_response_too_large')
                    data.extend(chunk)
                if declared is not None and len(data)!=int(declared):raise PlatformError(502,'invalid_voice_response')
            raw=bytes(data)
            value=json.loads(raw,object_pairs_hook=_unique,parse_float=Decimal,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
            if not isinstance(value,dict) or value.get('error') or value.get('code') or value.get('success') is False:raise ValueError
            return value,hashlib.sha256(raw).hexdigest()
        except httpx.HTTPError:raise PlatformError(502,'voice_upstream_uncertain') from None
        except (ValueError,TypeError,UnicodeError,RecursionError):raise PlatformError(502,'invalid_voice_response') from None

    async def submit(self,client,prepared,*,request_id,max_bytes,on_headers,on_task=None):
        if not isinstance(prepared,PreparedVoiceRequest) or prepared.stage not in self.paths or prepared.url!=self.base+self.paths[prepared.stage]:
            raise PlatformError(422,'invalid_voice_request')
        value,evidence=await self._json(client,'POST',prepared.url,payload=prepared.payload,content_type=prepared.content_type,
            request_id=request_id,max_bytes=max_bytes,on_headers=on_headers,allowed_status=(200,202) if self.adapter_id=='newapi_fish_voice_v1' else (200,))
        if self.adapter_id=='newapi_dashscope_voice_v1':
            output=value.get('output')
            task=output.get('task_id') if isinstance(output,dict) else None
        else:task=value.get('task_id')
        if on_task is not None and isinstance(task,str) and _TASK.fullmatch(task):await on_task(task,evidence)
        try:return self._parse(value,prepared,evidence)
        except PlatformError as error:
            if error.status==422:raise PlatformError(502,'invalid_voice_response') from None
            raise

    async def query(self,client,result,*,prepared,max_bytes,on_headers=None):
        if not isinstance(result,BridgeVoiceResult) or result.reference.namespace!=self.adapter_id:raise PlatformError(422,'invalid_voice_reference')
        task=_task(result.reference.task_id)
        value,evidence=await self._json(client,'GET',self.base+self.query_path+task,max_bytes=max_bytes,on_headers=on_headers)
        observed=self._parse(value,prepared,evidence)
        if observed.reference.task_id!=task:raise PlatformError(502,'voice_reference_changed')
        return observed

    async def retrieve(self,client,result,*,max_bytes):
        if (not isinstance(result,BridgeVoiceResult) or result.reference.namespace!=self.adapter_id or result.state!='completed'
                or type(max_bytes) is not int or not 1<=max_bytes<=64*1024*1024):raise PlatformError(422,'invalid_voice_artifact')
        if result.audio is not None:
            if not 0<len(result.audio)<=max_bytes:raise PlatformError(502,'voice_response_too_large')
            return result.audio,result.mime
        task=_task(result.reference.task_id);path='/v1/tasks/'+task+'/artifacts/audio/content'
        if not isinstance(result.descriptor,dict) or result.descriptor.get('path')!=path:raise PlatformError(422,'invalid_voice_artifact')
        try:
            async with client.stream('GET',self.base+path,headers={'Authorization':'Bearer '+self.provider.secret,
                    'Accept':result.mime,'Accept-Encoding':'identity'},follow_redirects=False) as response:
                raw=await bounded_response(response,max_bytes)
                mime=response.headers.get('content-type','').split(';',1)[0].strip().lower()
            if mime!=result.mime or result.descriptor.get('size') is not None and len(raw)!=result.descriptor['size']:
                raise PlatformError(502,'invalid_voice_artifact')
            return raw,mime
        except httpx.HTTPError:raise PlatformError(502,'voice_download_unavailable') from None


class MiniMaxVoiceAdapter(_PrivateVoice):
    adapter_id='newapi_minimax_voice_v1'
    targets=_MINIMAX
    paths={'upload':'/easel/minimax/v1/files/upload','register':'/easel/minimax/v1/voice_clone','synthesize':'/easel/minimax/v1/t2a_v2'}
    query_path='/easel/minimax/v1/tasks/'
    supported_meters=_PrivateVoice.supported_meters|frozenset({('audio_ms','provider_usage_v1')})

    def _enrollment(self,request):
        model=self._model(request,'voice.enroll')
        params=self._parameters(request,{'target_model','need_noise_reduction','need_volume_normalization'})
        if params.get('target_model')!=model or request.get('voice_id') is not None or request.get('prompt','').strip():raise PlatformError(422,'invalid_voice_parameters')
        for key in ('need_noise_reduction','need_volume_normalization'):
            if key in params and type(params[key]) is not bool:raise PlatformError(422,'invalid_voice_parameters')
        return model,params

    def prepare_upload(self,request,assets):
        model,_=self._enrollment(request)
        if not isinstance(assets,list) or len(assets)!=1 or request.get('assets')!=[assets[0].get('id')]:raise PlatformError(422,'invalid_voice_assets')
        sample=assets[0];raw=_sample(sample,limit=20000000,minimum=10000)
        boundary='easel-voice-'+secrets.token_hex(16)
        payload=(f'--{boundary}\r\nContent-Disposition: form-data; name="purpose"\r\n\r\nvoice_clone\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="input{_SAMPLE[sample["mime"]][1]}"\r\nContent-Type: {sample["mime"]}\r\n\r\n').encode()+raw+f'\r\n--{boundary}--\r\n'.encode()
        return self._prepared('upload',payload,'easel-minimax-upload',content_type='multipart/form-data; boundary='+boundary,target_model=model)

    def prepare_register(self,request,refs):
        model,params=self._enrollment(request)
        if not isinstance(refs,dict) or set(refs)!={'file_task_id','target_model'} or refs['target_model']!=model:raise PlatformError(422,'invalid_voice_reference')
        body={'file_task_id':_task(refs['file_task_id']),**{k:params[k] for k in ('need_noise_reduction','need_volume_normalization') if k in params}}
        return self._prepared('register',_encode(body),'easel-minimax-clone',target_model=model)

    def prepare(self,request,assets,*,owned_voice=None):
        model=self._model(request,'voice.synthesize');reference=self._owned(request,model,owned_voice)
        if assets or request.get('assets'):raise PlatformError(422,'invalid_voice_assets')
        params=self._parameters(request,{'speed_percent','emotion','response_format'})
        if params.get('response_format','mp3')!='mp3':raise PlatformError(422,'invalid_voice_parameters')
        body={'model':model,'voice_task_id':reference,'text':_text(request.get('prompt'),500),'speed':self._speed(params),'stream':False}
        if 'emotion' in params:
            if params['emotion'] not in {'happy','sad','angry','fearful','disgusted','surprised'}:raise PlatformError(422,'invalid_voice_parameters')
            body['emotion']=params['emotion']
        return self._prepared('synthesize',_encode(body),model,output_format='mp3')

    def _parse(self,value,prepared,evidence):
        task=_task(value.get('task_id'))
        if value.get('status')!='SUCCESS' or any(k in value for k in ('voice_id','file_id')):raise PlatformError(502,'invalid_voice_response')
        reference=GatewayTaskReference(self.adapter_id,task)
        if prepared.stage in {'upload','register'}:
            field='file_task_id' if prepared.stage=='upload' else 'voice_task_id'
            if value.get(field)!=task:raise PlatformError(502,'invalid_voice_response')
            return BridgeVoiceResult(reference,'completed',evidence,{'operation':'upload' if field=='file_task_id' else 'clone',field:task})
        data,extra=value.get('data'),value.get('extra_info')
        if not isinstance(data,dict) or data.get('status')!=2 or not isinstance(extra,dict) or extra.get('audio_format')!='mp3':raise PlatformError(502,'invalid_voice_response')
        facts={'usage_characters':_integer(extra.get('usage_characters'),100000)}
        audio=data.get('audio')
        if not isinstance(audio,str) or not audio or len(audio)>900000 or len(audio)%2 or not re.fullmatch('[0-9a-fA-F]+',audio):raise PlatformError(502,'invalid_voice_response')
        quantities={}
        if 'audio_length' in extra:
            length=extra['audio_length']
            if type(length) not in {int,Decimal} or isinstance(length,Decimal) and not length.is_finite() or length<0 or length>900000:
                raise PlatformError(502,'invalid_voice_usage')
            milliseconds=int(Decimal(length).to_integral_value(rounding='ROUND_CEILING'))
            quantities['audio_ms']=milliseconds;facts['audio_length_ms']=str(length)
        return BridgeVoiceResult(reference,'completed',evidence,audio=bytes.fromhex(audio),mime='audio/mpeg',quantities=quantities,reported_facts=facts)


class DashScopeVoiceAdapter(_PrivateVoice):
    adapter_id='newapi_dashscope_voice_v1'
    targets=_DASHSCOPE
    paths={'verify':'/easel/dashscope/voice/verify','synthesize':'/easel/dashscope/voice/synthesize'}
    query_path='/easel/dashscope/voice/tasks/'

    def prepare_enroll(self,request,assets):
        # The locked v1 bridge requires a public URL. Private samples and the
        # newer Data URI API cannot be silently substituted into this contract.
        raise PlatformError(503,'voice_sample_publication_unavailable')

    def prepare_verify(self,request,refs):
        model=self._model(request,'voice.enroll')
        if (self._parameters(request,{'target_model'}).get('target_model')!=model or not isinstance(refs,dict)
                or set(refs)!={'voice_task_id','target_model'} or refs['target_model']!=model):raise PlatformError(422,'invalid_voice_reference')
        return self._prepared('verify',_encode({'model':'voice-enrollment','input':{'action':'query_voice','voice_task_id':_task(refs['voice_task_id'])}}),'voice-enrollment',target_model=model)

    def prepare(self,request,assets,*,owned_voice=None):
        model=self._model(request,'voice.synthesize');reference=self._owned(request,model,owned_voice)
        if assets or request.get('assets'):raise PlatformError(422,'invalid_voice_assets')
        params=self._parameters(request,{'response_format','speed_percent','instruction'})
        fmt=params.get('response_format','mp3')
        if fmt not in self.profile.formats:raise PlatformError(422,'invalid_voice_parameters')
        fields={'voice_task_id':reference,'text':_text(request.get('prompt'),100000),'format':fmt,'rate':self._speed(params)}
        if 'instruction' in params:fields['instruction']=_text(params['instruction'],2000)
        return self._prepared('synthesize',_encode({'model':model,'input':fields}),model,output_format=fmt)

    def _parse(self,value,prepared,evidence):
        output=value.get('output')
        if not isinstance(output,dict) or output.get('task_status')!='SUCCEEDED':raise PlatformError(502,'invalid_voice_response')
        task=_task(output.get('task_id'));reference=GatewayTaskReference(self.adapter_id,task);usage=value.get('usage')
        if prepared.stage=='verify':
            if (output.get('voice_task_id')!=task or output.get('target_model')!=prepared.target_model or output.get('voice_status') not in {'DEPLOYING','OK','UNDEPLOYED'}
                    or not isinstance(usage,dict) or usage.get('count')!=1 or type(usage['count']) is not int):raise PlatformError(502,'invalid_voice_response')
            facts={'operation':'query_voice','voice_task_id':task,'target_model':prepared.target_model,'voice_status':output['voice_status']}
            return BridgeVoiceResult(reference,'rejected' if output['voice_status']=='UNDEPLOYED' else 'completed',evidence,facts)
        if (output.get('finish_reason')!='stop' or not isinstance(output.get('audio'),dict)
                or not isinstance(output['audio'].get('url'),str) or not isinstance(usage,dict) or _integer(usage.get('characters'))<=0):
            raise PlatformError(502,'invalid_voice_response')
        # The locked plugin exposes this protected artifact route. Never follow
        # the provider-supplied URL or send gateway credentials to its CDN.
        return BridgeVoiceResult(reference,'completed',evidence,mime=_MIME[prepared.output_format],
            descriptor={'path':'/v1/tasks/'+task+'/artifacts/audio/content'},reported_facts={'characters':usage['characters']})


class FishVoiceAdapter(_PrivateVoice):
    adapter_id='newapi_fish_voice_v1'
    targets=_FISH
    paths={'synthesize':'/easel/fish/v1/tts'}
    query_path='/easel/fish/v1/tasks/'

    def prepare(self,request,assets):
        model=self._model(request,'audio.speech')
        if request.get('voice_id') is not None:raise PlatformError(422,'invalid_voice_reference')
        params=self._parameters(request,{'voice','response_format','speed_percent','reference_text','reference_text_2'})
        fmt=params.get('response_format','mp3')
        if fmt not in self.profile.formats:raise PlatformError(422,'invalid_voice_parameters')
        body={'model':model,'text':_text(request.get('prompt'),2000),'format':fmt,'speed':self._speed(params)}
        preset=params.get('voice')
        if assets:
            if preset is not None or not isinstance(assets,list) or not 1<=len(assets)<=2 or request.get('assets')!=[a.get('id') for a in assets]:raise PlatformError(422,'invalid_voice_assets')
            body['references']=[]
            for index,sample in enumerate(assets):
                raw=_sample(sample,limit=1048576)
                text=_text(params.get('reference_text' if index==0 else 'reference_text_2'),2000)
                body['references'].append({'audio':base64.b64encode(raw).decode('ascii'),'text':text})
            if len(assets)==1 and 'reference_text_2' in params:raise PlatformError(422,'invalid_voice_parameters')
        else:
            presets=dict(self.profile.presets)
            if not isinstance(preset,str) or preset not in presets or any(k in params for k in ('reference_text','reference_text_2')) or request.get('assets'):raise PlatformError(422,'invalid_voice_reference')
            body['reference_id']=presets[preset]
        return self._prepared('synthesize',_encode(body),model,output_format=fmt)

    def _parse(self,value,prepared,evidence):
        task=_task(value.get('task_id'));reference=GatewayTaskReference(self.adapter_id,task);status=value.get('status')
        if status in {'NOT_START','SUBMITTED','QUEUED','IN_PROGRESS'}:return BridgeVoiceResult(reference,'pending',evidence)
        if status!='SUCCESS':raise PlatformError(502,'invalid_voice_response')
        path='/v1/tasks/'+task+'/artifacts/audio/content';mime=_MIME[prepared.output_format]
        if (value.get('content_url') not in {path,self.base+path} or value.get('mime_type')!=mime
                or type(value.get('size')) is not int or not 0<value['size']<=8388608
                or type(value.get('expires_at')) is not int):raise PlatformError(502,'invalid_voice_response')
        if value.get('artifact_expired') is True or value['expires_at']<=self.clock():raise PlatformError(410,'voice_artifact_expired')
        return BridgeVoiceResult(reference,'completed',evidence,mime=mime,descriptor={'path':path,'size':value['size'],'expires_at':value['expires_at']})
