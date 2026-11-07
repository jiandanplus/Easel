"""Distinct locked SunoAPI and Fun-Music plugin envelopes; never fallback."""
from decimal import Decimal

from ...errors import PlatformError
from ..async_contracts import ArtifactSource,Observation
from ..rates import seconds_to_milliseconds
from .base import PreparedRequest
from .newapi_async import AsyncAdapter,encode,job_id


class NewAPISuno(AsyncAdapter):
    capability='music.generate'
    def prepare(self,request,assets):
        model,parameters,text=self.check(request,assets,{'lyrics','title','instrumental'})
        if assets or type(parameters.get('instrumental',False))is not bool:raise PlatformError(422,'invalid_media_parameters')
        lyrics=parameters.get('lyrics');title=parameters.get('title')
        if lyrics is not None and(not isinstance(lyrics,str)or not lyrics.strip()or len(lyrics)>65536):raise PlatformError(422,'invalid_media_parameters')
        if title is not None and(not isinstance(title,str)or not 1<=len(title)<=200):raise PlatformError(422,'invalid_media_parameters')
        value={'make_instrumental':parameters.get('instrumental',False)and not bool(lyrics),'mv':model}
        value.update({'prompt':lyrics,'tags':text}if lyrics else{'gpt_description_prompt':text})
        if title is not None:value['title']=title
        return PreparedRequest(self.provider.base_url.rstrip('/')+'/suno/submit/music',encode(value),'application/json',model)

    def envelope(self,value):
        if value.get('code')!='success'or value.get('success')is False:raise PlatformError(502,'invalid_media_response')
        return value.get('data')
    def _job_id(self,value,*,submit):
        data=self.envelope(value)
        return job_id(data if submit else data.get('task_id')if isinstance(data,dict)else None)
    def _submission_observation(self,value,sha,known):return None
    def query_url(self,known):return self.provider.base_url.rstrip('/')+'/suno/fetch/'+job_id(known)

    def observe(self,value,sha,known):
        data=self.envelope(value)
        if not isinstance(data,dict):raise PlatformError(502,'invalid_media_response')
        state={'NOT_START':'queued','SUBMITTED':'queued','QUEUED':'queued','IN_PROGRESS':'running','SUCCESS':'completed','FAILURE':'failed'}.get(data.get('status'))
        if state is None:raise PlatformError(502,'invalid_media_response')
        sources=[]
        if state=='completed':
            songs=data.get('data')
            if isinstance(songs,dict)and isinstance(songs.get('data'),dict):
                nested=songs['data']
                if nested.get('task_id')and'data'in nested:songs=nested['data']
            if isinstance(songs,dict):songs=[songs]
            if not isinstance(songs,list)or not 1<=len(songs)<=32:raise PlatformError(502,'invalid_media_response')
            for song in songs:
                if not isinstance(song,dict):raise PlatformError(502,'invalid_media_response')
                sources.append(ArtifactSource(job_id(song.get('id')),'url',self.source_url(song.get('audio_url'))))
            if len({s.source_id for s in sources})!=len(sources):raise PlatformError(502,'invalid_media_response')
        return Observation(known,state,tuple(sources),{},None,sha)


class NewAPIDashScopeMusic(AsyncAdapter):
    capability='music.generate'
    def prepare(self,request,assets):
        model,parameters,text=self.check(request,assets,{'lyrics','is_instrumental','gender','format','enable_aigc_watermark'})
        if assets or len(text)>2000:raise PlatformError(422,'invalid_media_parameters')
        for key in ['is_instrumental','enable_aigc_watermark']:
            if key in parameters and type(parameters[key])is not bool:raise PlatformError(422,'invalid_media_parameters')
        if 'lyrics'in parameters and(not isinstance(parameters['lyrics'],str)or not 5<=len(parameters['lyrics'])<=2000):raise PlatformError(422,'invalid_media_parameters')
        if 'format'in parameters and parameters['format']not in{'mp3','wav'}:raise PlatformError(422,'invalid_media_parameters')
        if 'gender'in parameters and(model!='fun-music-v1'or parameters['gender']not in{'male','female'}):raise PlatformError(422,'invalid_media_parameters')
        return PreparedRequest(self.provider.base_url.rstrip('/')+'/easel/dashscope/api/v1/services/audio/music/generation',encode({'model':model,'input':{'prompt':text,**parameters}}),'application/json',model)

    def _job_id(self,value,*,submit):
        output=value.get('output')
        return job_id(output.get('task_id')if isinstance(output,dict)else None)
    def query_url(self,known):return self.provider.base_url.rstrip('/')+'/easel/dashscope/api/v1/tasks/'+job_id(known)

    def observe(self,value,sha,known):
        output=value.get('output')
        if not isinstance(output,dict)or value.get('code'):raise PlatformError(502,'invalid_media_response')
        state={'PENDING':'queued','RUNNING':'running','SUCCEEDED':'completed','FAILED':'failed','CANCELED':'cancelled'}.get(output.get('task_status'))
        if state is None:raise PlatformError(502,'invalid_media_response')
        sources=();quantities={};usage_hash=None
        if state=='completed':
            audio=output.get('audio');usage=value.get('usage')
            if output.get('finish_reason')!='stop'or not isinstance(audio,dict)or not isinstance(usage,dict)or type(usage.get('duration'))not in(int,Decimal):
                raise PlatformError(502,'invalid_media_response')
            amount=seconds_to_milliseconds(str(usage['duration']),rounding='ceil')
            if amount<=0:raise PlatformError(502,'invalid_media_usage')
            quantities={'audio_ms':amount};usage_hash=sha
            sources=(ArtifactSource('audio','url',self.source_url(audio.get('url'))),)
        return Observation(known,state,sources,quantities,usage_hash,sha)
