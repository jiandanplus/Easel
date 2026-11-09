"""Explicit synchronous Audio REST protocols, without provider or local fallbacks.

Binary speech has a real HTTP correlation, not a fabricated provider object ID.
Decoding/storage and finance are separate callers; receiving bytes alone never
certifies a playable artifact or a provider usage quantity.
"""
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import re
import secrets

import httpx

from ...errors import PlatformError
from ...execution_protocol import _unique
from ...media_config import origin
from ..rates import unicode_scalars, seconds_to_milliseconds
from ..references import http_reference
from .base import MediaAdapter, PreparedRequest
from .openai_images import bounded_response
from ...voice_catalogs import VOICE_ID

CLIENT_NAMESPACE = 'easel_client_request_v1'
_HTTP_ID = re.compile(r'req_[A-Za-z0-9_-]{1,192}\Z', re.ASCII)
_INTERNAL_ID = re.compile(r'[A-Za-z0-9_.:-]{1,128}\Z', re.ASCII)
_FORMAT_MIME = {'mp3':'audio/mpeg','wav':'audio/wav'}
_INPUTS = {'audio/mpeg':('MP3','.mp3'),'audio/wav':('WAV','.wav'),
           'audio/ogg':('OGG','.ogg'),'audio/mp4':('MP4','.m4a'),'video/mp4':('MP4','.mp4')}
_MAX_INTEGER = 9223372036854775807


@dataclass(frozen=True)
class HttpCorrelation:
    namespace: str
    value: str

    def __post_init__(self):
        try:http_reference(self.namespace,self.value)
        except PlatformError:raise PlatformError(502,'audio_response_uncorrelated') from None


@dataclass(frozen=True)
class AudioProfile:
    voices: tuple[str,...] = ()
    instructions: bool = False
    speech_formats: tuple[str,...] = ('mp3','wav')
    transcription_formats: tuple[str,...] = ('json','verbose_json')
    transcription_usage: str = 'none'
    transcription_video: bool = False
    max_input_bytes: int = 25*1024*1024

    def __post_init__(self):
        if (not isinstance(self.voices,tuple) or len(self.voices)>64
                or any(not isinstance(v,str) or not re.fullmatch(VOICE_ID,v,re.ASCII) for v in self.voices)
                or len(set(self.voices))!=len(self.voices)
                or type(self.instructions) is not bool or type(self.transcription_video) is not bool
                or self.transcription_usage not in {'none','tokens','duration'}
                or type(self.max_input_bytes) is not int or not 1<=self.max_input_bytes<=25*1024*1024):
            raise PlatformError(503,'invalid_audio_profile')
        for values,allowed in ((self.speech_formats,set(_FORMAT_MIME)),(self.transcription_formats,{'json','verbose_json'})):
            if not isinstance(values,tuple) or not values or any(not isinstance(v,str) or v not in allowed for v in values) or len(set(values))!=len(values):
                raise PlatformError(503,'invalid_audio_profile')


@dataclass(frozen=True)
class PreparedAudioRequest(PreparedRequest):
    response_format: str
    input_quantities: dict[str,int]


@dataclass(frozen=True)
class AudioResult:
    correlation: HttpCorrelation
    payload: bytes
    mime: str
    properties: dict
    quantities: dict[str,int]
    usage_hash: str | None
    evidence_hash: str
    usage_evidence: dict | None = None


def _encode(value):
    try:return json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')
    except (TypeError,ValueError,UnicodeError):raise PlatformError(422,'invalid_audio_request') from None


def _integer(value):
    if type(value) is not int or not 0<=value<=_MAX_INTEGER:raise PlatformError(502,'invalid_audio_usage')
    return value


def _text(value,limit,*,empty=False):
    count=unicode_scalars(value)
    if count>limit or (not empty and not value.strip()):raise PlatformError(422,'invalid_audio_request')
    return value


class _OpenAIAudio(MediaAdapter):
    capability: str

    def __init__(self,provider,mode,profile):
        if not isinstance(profile,AudioProfile):raise PlatformError(503,'invalid_audio_profile')
        if getattr(provider,'adapter',self.adapter_id)!=self.adapter_id:raise PlatformError(503,'invalid_audio_profile')
        try:origin(provider.base_url,mode=mode)
        except (ValueError,AttributeError):raise PlatformError(503,'invalid_audio_profile') from None
        if (not isinstance(provider.secret,str) or not 1<=len(provider.secret)<=4096
                or not provider.secret.isascii() or any(not 33<=ord(c)<=126 for c in provider.secret)):
            raise PlatformError(503,'invalid_audio_profile')
        self.provider,self.mode,self.profile=provider,mode,profile

    def _model(self,request):
        if not isinstance(request,dict) or request.get('capability')!=self.capability or self.capability not in self.provider.capabilities:
            raise PlatformError(503,'audio_capability_closed')
        if not isinstance(request.get('model'),str):raise PlatformError(422,'invalid_audio_request')
        model=self.provider.models.get(request['model'])
        if not isinstance(model,str) or not 1<=len(model)<=128 or any(ord(c)<33 or ord(c)==127 for c in model):
            raise PlatformError(503,'audio_capability_closed')
        if request.get('voice_id') is not None:raise PlatformError(422,'owned_voice_reference_required')
        return model

    async def _submit(self,client,prepared,*,request_id,max_bytes,on_headers,accept,json_type=False):
        if not isinstance(prepared,PreparedAudioRequest) or prepared.url!=self.provider.base_url.rstrip('/')+self.path:
            raise PlatformError(422,'invalid_audio_request')
        if not isinstance(request_id,str) or not _INTERNAL_ID.fullmatch(request_id):raise PlatformError(422,'invalid_audio_request')
        if type(max_bytes) is not int or not 1<=max_bytes<=128*1024*1024:raise PlatformError(422,'invalid_audio_limit')
        try:
            async with client.stream('POST',prepared.url,content=prepared.payload,follow_redirects=False,
                                     headers={'Authorization':'Bearer '+self.provider.secret,'Content-Type':prepared.content_type,
                                              'Accept':accept,'Accept-Encoding':'identity','X-Client-Request-Id':request_id}) as response:
                raw_id=response.headers.get('x-request-id')
                correlation=HttpCorrelation('openai_x_request_id_v1',raw_id) if isinstance(raw_id,str) and _HTTP_ID.fullmatch(raw_id) else None
                # Only OpenAI itself issues req_ ids; others are tied to the id sent with the call.
                if correlation is None and getattr(self.provider,'client_correlation',False):correlation=HttpCorrelation(CLIENT_NAMESPACE,request_id)
                await on_headers(http_status=response.status_code,**({'http_request_id':correlation.value} if correlation else {}))
                raw=await bounded_response(response,max_bytes,json_type=json_type)
                mime=response.headers.get('content-type','').split(';',1)[0].strip().lower()
            if correlation is None:raise PlatformError(502,'audio_response_uncorrelated')
            if not raw:raise PlatformError(502,'invalid_audio_response')
            return raw,mime,correlation
        except httpx.HTTPError:
            # A failed POST may already have consumed provider resources.
            raise PlatformError(502,'audio_upstream_uncertain') from None


class OpenAISpeechAdapter(_OpenAIAudio):
    capability='audio.speech'
    adapter_id='openai_speech_v1'
    path='/audio/speech'
    supported_meters=frozenset({('unicode_scalar','verified_input_v1'),('audio_ms','verified_delivery_v1')})

    def prepare(self,request,assets):
        model=self._model(request)
        if assets or request.get('assets',[]):raise PlatformError(422,'invalid_audio_assets')
        prompt=_text(request.get('prompt'),4096)
        parameters=request.get('parameters',{})
        if not isinstance(parameters,dict) or set(parameters)-{'voice','response_format','speed_percent','instructions'}:
            raise PlatformError(422,'invalid_audio_parameters')
        voice=parameters.get('voice')
        output=parameters.get('response_format','mp3')
        if voice not in self.profile.voices or output not in self.profile.speech_formats:raise PlatformError(422,'invalid_audio_parameters')
        value={'model':model,'input':prompt,'voice':voice,'response_format':output}
        if not getattr(self.provider,'client_correlation',False):value['stream_format']='audio'
        if 'instructions' in parameters:
            if not self.profile.instructions:raise PlatformError(422,'invalid_audio_parameters')
            value['instructions']=_text(parameters['instructions'],4096)
        speed=parameters.get('speed_percent',100)
        if type(speed) is not int or not 25<=speed<=400:raise PlatformError(422,'invalid_audio_parameters')
        # JSON numeric wire value from bounded integer arithmetic, never float.
        raw=_encode(value)[:-1]+b',"speed":'+format(Decimal(speed)/100,'f').encode()+b'}'
        return PreparedAudioRequest(self.provider.base_url.rstrip('/')+'/audio/speech',raw,'application/json',model,
                                    output,{'unicode_scalar':unicode_scalars(prompt)})

    async def submit(self,client,prepared,*,request_id,max_bytes,on_headers):
        raw,mime,correlation=await self._submit(client,prepared,request_id=request_id,max_bytes=max_bytes,on_headers=on_headers,
                                              accept=_FORMAT_MIME[prepared.response_format])
        expected=_FORMAT_MIME[prepared.response_format]
        lenient=getattr(self.provider,'client_correlation',False) and mime in {'audio/mp3','audio/x-wav','audio/wave','application/octet-stream'}
        if mime!=expected and not lenient:raise PlatformError(502,'invalid_audio_response')
        return AudioResult(correlation,raw,expected,{}, {},None,hashlib.sha256(raw).hexdigest())


class OpenAITranscriptionsAdapter(_OpenAIAudio):
    capability='audio.transcribe'
    adapter_id='openai_transcriptions_v1'
    path='/audio/transcriptions'

    @property
    def supported_meters(self):
        values={('audio_ms','verified_input_v1')}
        if self.profile.transcription_usage=='tokens':values|={('input_token','provider_usage_v1'),('output_token','provider_usage_v1')}
        elif self.profile.transcription_usage=='duration':values.add(('audio_ms','provider_usage_v1'))
        return frozenset(values)

    def prepare(self,request,assets):
        model=self._model(request)
        if (not isinstance(assets,list) or len(assets)!=1 or request.get('assets')!=[assets[0].get('id')]):
            raise PlatformError(422,'invalid_audio_assets')
        sample=assets[0];mime=sample.get('mime');raw=sample.get('raw');properties=sample.get('properties',{})
        if (mime not in _INPUTS or mime=='video/mp4' and not self.profile.transcription_video
                or not isinstance(raw,bytes) or not 0<len(raw)<=self.profile.max_input_bytes
                or sample.get('size')!=len(raw) or sample.get('sha256')!=hashlib.sha256(raw).hexdigest()
                or not isinstance(properties,dict) or properties.get('format')!=_INPUTS[mime][0]):
            raise PlatformError(422,'invalid_audio_assets')
        duration=properties.get('audio_duration_ms' if mime=='video/mp4' else 'duration_ms')
        if (type(duration) is not int or not 0<duration<=86400000
                or type(properties.get('channels')) is not int or not 1<=properties['channels']<=32
                or type(properties.get('sample_rate')) is not int or not 1000<=properties['sample_rate']<=384000):
            raise PlatformError(422,'invalid_audio_assets')
        parameters=request.get('parameters',{})
        if not isinstance(parameters,dict) or set(parameters)-{'response_format','language'}:raise PlatformError(422,'invalid_audio_parameters')
        output=parameters.get('response_format','json')
        if output not in self.profile.transcription_formats:raise PlatformError(422,'invalid_audio_parameters')
        fields={'model':model,'response_format':output}
        if 'language' in parameters:
            language=parameters['language']
            if not isinstance(language,str) or not re.fullmatch('[a-z]{2}',language,re.ASCII):raise PlatformError(422,'invalid_audio_parameters')
            fields['language']=language
        prompt=_text(request.get('prompt',''),4096,empty=True)
        if prompt:fields['prompt']=prompt
        boundary='easel-audio-'+secrets.token_hex(16);parts=[]
        for key,value in fields.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n'.encode()+value.encode()+b'\r\n')
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="input{_INPUTS[mime][1]}"\r\nContent-Type: {mime}\r\n\r\n'.encode()+raw+b'\r\n')
        parts.append(f'--{boundary}--\r\n'.encode())
        return PreparedAudioRequest(self.provider.base_url.rstrip('/')+'/audio/transcriptions',b''.join(parts),
                                    'multipart/form-data; boundary='+boundary,model,output,{'audio_ms':duration})

    def _usage(self,value,raw):
        profile=self.profile.transcription_usage
        if profile=='none':return {},None,None
        usage=value.get('usage')
        if not isinstance(usage,dict) or usage.get('type')!=profile:raise PlatformError(502,'invalid_audio_usage')
        if profile=='tokens':
            amounts={'input_token':_integer(usage.get('input_tokens')),'output_token':_integer(usage.get('output_tokens'))}
            if _integer(usage.get('total_tokens'))!=sum(amounts.values()):raise PlatformError(502,'invalid_audio_usage')
            evidence={'type':'tokens','input_tokens':amounts['input_token'],'output_tokens':amounts['output_token'],'total_tokens':sum(amounts.values())}
        else:
            seconds=usage.get('seconds')
            if type(seconds) not in {int,Decimal} or isinstance(seconds,Decimal) and not seconds.is_finite():
                raise PlatformError(502,'invalid_audio_usage')
            try:amount=seconds_to_milliseconds(str(seconds),rounding='ceil')
            except (PlatformError,ValueError,ArithmeticError):raise PlatformError(502,'invalid_audio_usage') from None
            amounts={'audio_ms':amount};evidence={'type':'duration','seconds':str(seconds),'rounding':'ceil_milliseconds_v1'}
        return amounts,hashlib.sha256(raw).hexdigest(),evidence

    async def submit(self,client,prepared,*,request_id,max_bytes,on_headers):
        raw,_,correlation=await self._submit(client,prepared,request_id=request_id,max_bytes=max_bytes,on_headers=on_headers,
                                           accept='application/json',json_type=True)
        try:
            value=json.loads(raw,object_pairs_hook=_unique,parse_float=Decimal,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
            if not isinstance(value,dict) or value.get('error') or not isinstance(value.get('text'),str):raise ValueError
            text=value['text'];count=unicode_scalars(text)
            quantities,usage_hash,evidence=self._usage(value,raw)
            return AudioResult(correlation,_encode({'text':text}),'application/json',{'unicode_scalar':count},
                               quantities,usage_hash,hashlib.sha256(raw).hexdigest(),evidence)
        except (ValueError,TypeError,UnicodeError,RecursionError):
            raise PlatformError(502,'invalid_audio_response') from None
