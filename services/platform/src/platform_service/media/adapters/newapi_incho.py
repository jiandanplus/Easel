"""Incho 1.0.2 native music tasks with explicit vendor versions and owned GETs."""
from urllib.parse import urlsplit

from ...errors import PlatformError
from ...media_config import origin
from ..async_contracts import ArtifactSource, Observation
from ..rates import unicode_scalars
from .base import PreparedRequest
from .newapi_async import AsyncAdapter, encode, job_id


class NewAPIIncho(AsyncAdapter):
    capability='music.generate'
    models=frozenset({'v3.5','v4.0'})

    def __init__(self,provider,mode,job=None):
        super().__init__(provider,mode)
        self.requested_n=2 if job is None else job['request'].get('parameters',{}).get('n',2)
        self.bound_request=job is not None
        if type(self.requested_n)is not int or self.requested_n not in (1,2):
            raise PlatformError(503,'media_binding_unavailable')

    def prepare(self,request,assets):
        unicode_scalars(request.get('prompt'))
        model,parameters,text=self.check(request,assets,{'lyric','n'})
        try:origin(self.provider.base_url,mode=self.mode,root=True)
        except ValueError:raise PlatformError(503,'media_binding_unavailable')from None
        n=parameters.get('n',2);text=text.strip();lyric=parameters.get('lyric')
        if (assets or not isinstance(model,str)or model not in self.models or type(n)is not int or n not in(1,2)
            or self.bound_request and n!=self.requested_n or unicode_scalars(text)>1000):
            raise PlatformError(422,'invalid_media_parameters')
        if lyric is not None:
            unicode_scalars(lyric);lyric=lyric.strip()
            if unicode_scalars(lyric)>3000:raise PlatformError(422,'invalid_media_parameters')
        body={'model':model,'task_type':'normal','n':n,'prompt':text}
        if lyric:body['lyric']=lyric
        return PreparedRequest(self.provider.base_url.rstrip('/')+'/incho/submit/music',
                               encode(body),'application/json',model)

    def envelope(self,value):
        if value.get('code')!='success'or value.get('success')is False:
            raise PlatformError(502,'invalid_media_response')
        return value.get('data')

    def _job_id(self,value,*,submit):
        data=self.envelope(value)
        return job_id(data if submit else data.get('task_id')if isinstance(data,dict)else None)

    def _submission_observation(self,value,sha,known):return None

    def query_url(self,known):
        return self.provider.base_url.rstrip('/')+'/incho/fetch/'+job_id(known)

    def source_url(self,value):
        target=super().source_url(value)
        path=urlsplit(target).path
        if ('%'in path or '//'in path or any(part in{'.','..'}for part in path.split('/'))
            or not any(path.startswith(prefix)for prefix in self.provider.download_path_prefixes)):
            raise PlatformError(502,'media_download_path_denied')
        return target

    def observe(self,value,sha,known):
        data=self.envelope(value)
        if not isinstance(data,dict):raise PlatformError(502,'invalid_media_response')
        if not isinstance(data.get('status'),str):raise PlatformError(502,'invalid_media_response')
        state={'NOT_START':'queued','SUBMITTED':'queued','QUEUED':'queued',
               'IN_PROGRESS':'running','SUCCESS':'completed','FAILURE':'failed'}.get(data.get('status'))
        if state is None:raise PlatformError(502,'invalid_media_response')
        task=data.get('data')
        choices=task.get('choices')if isinstance(task,dict)else None
        # Host data can still contain the initial submit metadata before its
        # first provider poll. It supplies no delivery or zero-cost evidence.
        if choices is None and state=='queued':choices=[]
        if not isinstance(choices,list)or len(choices)>self.requested_n:
            raise PlatformError(502,'invalid_media_response')
        states=[];ids=set();sources=[]
        for song in choices:
            if (not isinstance(song,dict)or not isinstance(song.get('status'),str)
                or song.get('status')not in{'pending','cancelled','running','stream','done','fail'}):
                raise PlatformError(502,'invalid_media_response')
            status=song['status'];states.append(status)
            if song.get('id')is not None:
                identifier=job_id(song['id'])
                if identifier in ids:raise PlatformError(502,'invalid_media_response')
                ids.add(identifier)
            if status=='done':
                identifier=job_id(song.get('id'))
                sources.append(ArtifactSource(identifier,'url',self.source_url(song.get('audio_url'))))
        expected=('queued'if not states or 'pending'in states and not any(s in{'running','stream'}for s in states)
                  else'running'if any(s in{'running','stream'}for s in states)
                  else'completed'if'done'in states else'failed')
        if state!=expected:raise PlatformError(502,'invalid_media_response')
        # While any song is nonterminal, no URLs or supplier declarations can
        # become owned artifacts or billed quantities.
        return Observation(known,state,tuple(sources)if state=='completed'else(),{},None,sha)
