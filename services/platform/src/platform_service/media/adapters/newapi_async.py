"""One POST followed only by original-job GETs; no fallback or paid retry."""
from decimal import Decimal
import hashlib
import json
import re

from ...errors import PlatformError
from ...execution_protocol import _unique
from ...media_config import origin
from ..async_contracts import Submission
from ..contracts import identifier


def job_id(value):
    try:return identifier(value)
    except PlatformError:raise PlatformError(502,'invalid_media_response')from None


def encode(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()


def prompt(request):
    value=request.get('prompt')
    if not isinstance(value,str)or not value.strip()or len(value.encode())>65536:
        raise PlatformError(422,'invalid_media_request')
    return value


async def bounded(response,maximum,*,statuses=(200,)):
    if response.status_code not in statuses or response.headers.get('content-encoding','identity').lower()!='identity':
        raise PlatformError(502,'media_upstream_error')
    size=response.headers.get('content-length')
    if size is not None and(not size.isascii()or not size.isdecimal()or int(size)>maximum):
        raise PlatformError(502,'media_response_too_large')
    result=bytearray()
    async for part in response.aiter_bytes():
        if len(result)+len(part)>maximum:raise PlatformError(502,'media_response_too_large')
        result.extend(part)
    if size is not None and len(result)!=int(size):raise PlatformError(502,'invalid_media_response')
    return bytes(result)


class AsyncAdapter:
    capability=None
    def __init__(self,provider,mode):self.provider,self.mode=provider,mode

    def check(self,request,assets,allowed):
        if request.get('capability')!=self.capability or self.capability not in self.provider.capabilities:
            raise PlatformError(503,'media_capability_closed')
        parameters=request.get('parameters',{})
        if not isinstance(parameters,dict)or set(parameters)-allowed:raise PlatformError(422,'invalid_media_parameters')
        try:model=self.provider.models[request['model']]
        except(KeyError,TypeError):raise PlatformError(503,'media_capability_closed')from None
        return model,parameters,prompt(request)

    async def _json(self,client,method,url,*,maximum,prepared=None,request_id=None,on_headers=None):
        headers={'Authorization':'Bearer '+self.provider.secret,'Accept':'application/json','Accept-Encoding':'identity'}
        if prepared is not None:headers['Content-Type']=prepared.content_type
        if request_id is not None:headers['X-Easel-Request-Id']=request_id
        async with client.stream(method,url,headers=headers,content=prepared.payload if prepared else None)as response:
            if on_headers is not None:
                value=response.headers.get('x-oneapi-request-id')or response.headers.get('x-request-id')
                correlation=value if isinstance(value,str)and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,239}',value,re.ASCII)else None
                await on_headers(http_status=response.status_code,**({'http_request_id':correlation}if correlation else{}))
            raw=await bounded(response,maximum,statuses=(200,201,202)if method=='POST'else(200,))
            if response.headers.get('content-type','').split(';',1)[0].lower()!='application/json':
                raise PlatformError(502,'invalid_media_response')
        try:
            value=json.loads(raw,object_pairs_hook=_unique,parse_float=Decimal,
                             parse_constant=lambda _:(_ for _ in()).throw(ValueError()))
            if not isinstance(value,dict)or value.get('error'):raise ValueError
            return value,hashlib.sha256(raw).hexdigest()
        except(ValueError,TypeError,UnicodeError,RecursionError):raise PlatformError(502,'invalid_media_response')from None

    async def submit(self,client,prepared,*,request_id,max_bytes,on_headers,on_job):
        value,sha=await self._json(client,'POST',prepared.url,maximum=max_bytes,prepared=prepared,request_id=request_id,on_headers=on_headers)
        known=self._job_id(value,submit=True)
        await on_job(known)
        return Submission(known,self._submission_observation(value,sha,known))

    async def query(self,client,provider_job_id,*,max_bytes):
        known=job_id(provider_job_id)
        value,sha=await self._json(client,'GET',self.query_url(known),maximum=max_bytes)
        if self._job_id(value,submit=False)!=known:raise PlatformError(502,'media_provider_job_mismatch')
        return self.observe(value,sha,known)

    def _submission_observation(self,value,sha,known):return self.observe(value,sha,known)

    def source_url(self,value):
        try:
            target=origin(value,mode=self.mode,allow_query=True)
            if target not in [origin(o,mode=self.mode,root=True)for o in self.provider.download_origins]:raise ValueError
        except ValueError:raise PlatformError(502,'media_download_origin_denied')from None
        return value

    async def retrieve(self,client,source,*,max_bytes):
        if source.kind=='content':
            prefix=self.provider.base_url.rstrip('/')+'/videos/'
            if not source.location.startswith(prefix):raise PlatformError(502,'media_download_origin_denied')
            suffix=source.location[len(prefix):]
            if not suffix.endswith('/content')or job_id(suffix[:-8])!=suffix[:-8]:raise PlatformError(502,'media_download_origin_denied')
            headers={'Authorization':'Bearer '+self.provider.secret};allowed={'video/mp4'}
        elif source.kind=='url':
            self.source_url(source.location);headers={};allowed={'audio/mpeg','audio/wav','audio/ogg','audio/mp4'}
        else:raise PlatformError(502,'invalid_media_response')
        headers.update({'Accept-Encoding':'identity','Accept':','.join(sorted(allowed))})
        async with client.stream('GET',source.location,headers=headers)as response:
            raw=await bounded(response,max_bytes)
            mime=response.headers.get('content-type','').split(';',1)[0].lower()
            if mime not in allowed or(source.mime is not None and source.mime!=mime):raise PlatformError(502,'invalid_media_response')
            return raw,mime


def make_async_adapter(provider,mode,job=None):
    if provider.adapter in {'newapi_minimax_voice_v1','newapi_dashscope_voice_v1','newapi_fish_voice_v1'}:
        from .voice_async import VoiceRecoveryAdapter
        if job is None:raise PlatformError(503,'media_binding_unavailable')
        return VoiceRecoveryAdapter(provider,mode,job)
    from .newapi_videos import NewAPIVideos
    from .newapi_music import NewAPISuno,NewAPIDashScopeMusic
    if provider.adapter=='newapi_incho_v1':
        from .newapi_incho import NewAPIIncho
        return NewAPIIncho(provider,mode,job)
    kind={'newapi_videos_v1':NewAPIVideos,'newapi_suno_v1':NewAPISuno,'newapi_dashscope_music_v1':NewAPIDashScopeMusic}.get(provider.adapter)
    if kind is None:raise PlatformError(503,'media_capability_closed')
    return kind(provider,mode)
