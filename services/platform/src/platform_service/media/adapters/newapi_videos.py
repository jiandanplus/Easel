"""Fixed standard New API /v1/videos contract; no model-name detection."""
import re
import secrets

from ...errors import PlatformError
from ..async_contracts import ArtifactSource,Observation
from .base import PreparedRequest
from .newapi_async import AsyncAdapter,encode,job_id


class NewAPIVideos(AsyncAdapter):
    capability='video.generate'

    def prepare(self,request,assets):
        model,parameters,text=self.check(request,assets,{'seconds','size'})
        seconds,size=parameters.get('seconds'),parameters.get('size')
        if(type(seconds)is not int or not 1<=seconds<=900 or not isinstance(size,str)
           or not re.fullmatch(r'[1-9][0-9]{1,4}x[1-9][0-9]{1,4}',size)or len(assets)>1):
            raise PlatformError(422,'invalid_media_parameters')
        fields={'model':model,'prompt':text,'seconds':str(seconds),'size':size}
        url=self.provider.base_url.rstrip('/')+'/videos'
        if not assets:return PreparedRequest(url,encode(fields),'application/json',model)
        item=assets[0];ext={'image/png':'png','image/jpeg':'jpeg','image/webp':'webp'}.get(item.get('mime'))
        if ext is None or not isinstance(item.get('raw'),bytes)or not 0<len(item['raw'])<=10*1024*1024:
            raise PlatformError(422,'invalid_media_assets')
        boundary='easel_video_'+secrets.token_hex(16);parts=[]
        for key,value in fields.items():parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="input_reference"; filename="reference.{ext}"\r\nContent-Type: {item["mime"]}\r\n\r\n'.encode()+item['raw']+b'\r\n')
        parts.append(f'--{boundary}--\r\n'.encode())
        return PreparedRequest(url,b''.join(parts),'multipart/form-data; boundary='+boundary,model)

    def _job_id(self,value,*,submit):return job_id(value.get('id'))
    def query_url(self,known):return self.provider.base_url.rstrip('/')+'/videos/'+job_id(known)

    def observe(self,value,sha,known):
        state={'queued':'queued','in_progress':'running','completed':'completed','failed':'failed'}.get(value.get('status'))
        if state is None:raise PlatformError(502,'invalid_media_response')
        sources=(ArtifactSource('content','content',self.query_url(known)+'/content','video/mp4'),)if state=='completed'else()
        # seconds is requested/nominal metadata, never provider usage.
        return Observation(known,state,sources,{},None,sha)
