"""Adapt locked voice synthesis to the existing GET-only delivery worker."""
from ...errors import PlatformError
from ..async_contracts import ArtifactSource, Observation
from ..voice_execution import make_voice_adapter
from .newapi_voice import BridgeVoiceResult, GatewayTaskReference


class VoiceRecoveryAdapter:
    def __init__(self,provider,mode,job):
        self.provider=provider
        self.adapter=make_voice_adapter(provider,mode,binding_hash=job['binding_hash'])
        request=job['request'];fmt=request['parameters'].get('response_format','mp3')
        self.prepared=self.adapter._prepared('synthesize',b'',provider.models[job['model']],output_format=fmt)

    def observation(self,result):
        task=result.reference.task_id
        sources=(ArtifactSource('audio','content',task,result.mime),)if result.state=='completed'else()
        return Observation(task,'completed'if result.state=='completed'else'running',sources,result.quantities,
                           result.evidence_hash if result.quantities else None,result.evidence_hash,reported_facts=result.reported_facts)

    async def query(self,client,provider_job_id,*,max_bytes):
        reference=BridgeVoiceResult(GatewayTaskReference(self.provider.adapter,provider_job_id),'pending','0'*64)
        result=await self.adapter.query(client,reference,prepared=self.prepared,max_bytes=min(max_bytes,1048576))
        return self.observation(result)

    async def retrieve(self,client,source,*,max_bytes):
        if source.kind!='content' or source.source_id!='audio':raise PlatformError(422,'invalid_voice_artifact')
        reference=BridgeVoiceResult(GatewayTaskReference(self.provider.adapter,source.location),'pending','0'*64)
        result=await self.adapter.query(client,reference,prepared=self.prepared,max_bytes=1048576)
        if result.mime!=source.mime:raise PlatformError(502,'invalid_voice_artifact')
        return await self.adapter.retrieve(client,result,max_bytes=max_bytes)
