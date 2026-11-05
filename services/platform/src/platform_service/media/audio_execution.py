"""Standard audio execution using owned decoded bytes and typed HTTP evidence."""
from ..media_maintenance.runtime import mutation,physical_guard,current_permit,GuardedDatabase,physical_call,operation
import hashlib
import secrets
from ..errors import PlatformError
from ..media_config import audio_profile
from .adapters.audio import OpenAISpeechAdapter, OpenAITranscriptionsAdapter
from .av_artifacts import inspect_av
from .contracts import digest


def make_audio_adapter(provider, mode):
    cls = {'openai_speech_v1': OpenAISpeechAdapter, 'openai_transcriptions_v1': OpenAITranscriptionsAdapter}.get(provider.adapter)
    if cls is None: raise PlatformError(503, 'media_capability_closed')
    return cls(provider, mode, audio_profile(provider))


async def inspect(runtime, raw, mime, spec, maximum):
    if not spec or mime not in spec['mime_types']: raise PlatformError(422, 'media_mime_denied')
    return await inspect_av(raw, kind='video' if mime.startswith('video/') else 'audio', mime=mime,
        max_bytes=maximum, max_duration_ms=spec['max_duration_ms'],
        max_pixels=min(spec.get('max_pixels',runtime.settings.max_pixels),runtime.settings.max_pixels),
        require_audio=spec['require_audio'], max_channels=spec['max_channels'],
        max_sample_rate=spec['max_sample_rate'], tools=runtime.settings.av_tools)


@mutation("upload",subject=lambda v:v["context"]["invocation_id"])
async def store_input(runtime, context, raw, mime):
    await runtime._model(context['scope'], context['model'])
    limits=context['model_limits']
    info=await inspect(runtime,raw,mime,limits.get('input_media'),min(limits['max_input_bytes'],runtime.settings.max_asset_bytes))
    key=secrets.token_hex(32)
    await runtime.service.stage_blob(context,{'storage_key':key,**info})
    from ..media_maintenance.runtime import authorize_write
    fence=await authorize_write(current_permit(),{'storage_key':key,**info})
    return runtime.store._put_verified(raw,info,storage_key=key,fence=fence)


@mutation("job",subject=lambda v:v["job"]["id"],attempt=lambda v:v["job"]["attempt_id"])
async def finish_audio(runtime, context, job, response, prepared):
    service=runtime.service
    await service.record_http_response_reference(job['id'],job['attempt_id'], namespace=response.correlation.namespace,
        http_request_id=response.correlation.value,response_body_sha256=response.evidence_hash)
    if response.usage_evidence is not None:
        await service.record_protocol_facts(job['id'],job['attempt_id'],response_hash=response.evidence_hash,facts=response.usage_evidence)
    maximum=min(job['model_definition']['limits']['max_artifact_bytes'],runtime.settings.max_asset_bytes)
    if job['capability']=='audio.transcribe':
        if not 0<len(response.payload)<=maximum:raise PlatformError(502,'media_response_too_large')
        info={'sha256':hashlib.sha256(response.payload).hexdigest(),'size':len(response.payload),
              'mime':response.mime,'properties':response.properties}
    else:
        info=await inspect(runtime,response.payload,response.mime,job['model_definition']['limits'].get('output_media'),maximum)
    key=secrets.token_hex(32)
    await service.stage_blob(context,{'storage_key':key,**info},kind='artifact',job_id=job['id'],attempt_id=job['attempt_id'])
    from ..media_maintenance.runtime import authorize_write
    fence=await authorize_write(current_permit(),{'storage_key':key,**info})
    stored=runtime.store._put_verified(response.payload,info,storage_key=key,fence=fence)
    artifact=await service.register_artifact(job['id'],job['attempt_id'],**stored)
    quantities,bases={},{}
    for meter in job['model_definition']['rate']['meters']:
        unit,basis=meter['unit'],meter['basis']
        if basis=='verified_input_v1':
            quantities[unit]=prepared.input_quantities[unit]
            bases[basis]=digest({'request_hash':job['request_hash'],'input_units':prepared.input_quantities})
        elif basis=='verified_delivery_v1':
            if unit!='audio_ms':raise PlatformError(502,'media_metering_unsupported')
            quantities[unit]=info['properties']['duration_ms']
            bases[basis]=digest({k:artifact[k]for k in ('id','sha256','size','mime','properties')})
        elif basis=='provider_usage_v1':
            if response.usage_hash is None or unit not in response.quantities:raise PlatformError(502,'invalid_media_usage')
            quantities[unit]=response.quantities[unit];bases[basis]=response.usage_hash
        else:raise PlatformError(502,'media_metering_unsupported')
    await service.record_terminal_evidence(job['id'],job['attempt_id'],terminal_status='completed',
        provider_response_id=None,reference_kind='http_request_id',http_reference_namespace=response.correlation.namespace,
        http_reference_value=response.correlation.value,quantities=quantities,basis_evidence=bases,evidence_hash=response.evidence_hash)
