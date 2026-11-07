"""Explicit resource limits are separate from commercial metering units."""
import copy
import pytest
from platform_service.errors import PlatformError
from test_media_contracts import definition


def audio_resource():
    return {"mime_types":["audio/mpeg","audio/wav"],"max_files":2,"max_duration_ms":120000,
            "max_channels":2,"max_sample_rate":48000,"require_audio":True}


def video_resource():
    return {"mime_types":["video/mp4"],"max_files":1,"max_duration_ms":30000,"max_pixels":8294400,
            "max_channels":2,"max_sample_rate":48000,"require_audio":False}


def test_old_four_key_image_definition_is_preserved_without_normalization_or_hash_changes():
    from platform_service.media.contracts import validate_definition,digest
    old=definition();before=copy.deepcopy(old);expected=digest(old)
    assert validate_definition(old)==before and digest(validate_definition(old))==expected
    assert set(old['limits'])=={'max_input_bytes','max_assets','max_artifact_bytes','max_units'}


@pytest.mark.parametrize("direction,spec",[("input_media",audio_resource()),("output_media",video_resource())])
def test_immutable_definition_accepts_explicit_bounded_media_resource_object(direction,spec):
    from platform_service.media.contracts import validate_definition
    value=definition();value['limits'][direction]=spec
    assert validate_definition(value)==value


@pytest.mark.parametrize("patch",[
    {'max_duration_ms':True},{'max_duration_ms':0},{'max_duration_ms':900001},
    {'max_pixels':33554433},{'max_channels':9},{'max_sample_rate':192001},
    {'max_files':33},{'require_audio':'true'},{'mime_types':['application/x-mpegURL']},
    {'mime_types':['video/mp4','video/mp4']},{'ffmpeg_options':'-protocol_whitelist ALL'}])
def test_resource_schema_rejects_unsafe_types_limits_mimes_or_arbitrary_decoder_options(patch):
    from platform_service.media.contracts import validate_definition
    value=definition();value['limits']['output_media']={**video_resource(),**patch}
    with pytest.raises(PlatformError):validate_definition(value)


@pytest.mark.parametrize("capability,needs_input,needs_output",[
    ('video.generate',True,True),('music.generate',False,True),('audio.speech',False,True),
    ('audio.transcribe',True,False),('voice.enroll',True,False),('voice.synthesize',False,True)])
def test_av_readiness_requires_corresponding_binary_resource_direction(capability,needs_input,needs_output):
    from platform_service.media import contracts
    ready=getattr(contracts,'media_resources_ready',None)
    assert callable(ready), 'AV readiness must explicitly validate resource limits'
    value=definition(capability)
    if not needs_input:value['limits']['max_assets']=0
    assert ready(value) is False
    if needs_input:value['limits']['input_media']=audio_resource() if capability!='video.generate' else {'mime_types':['image/png'],'max_files':1,'max_pixels':16777216}
    if needs_output:value['limits']['output_media']=video_resource() if capability=='video.generate' else audio_resource()
    assert ready(value) is True


def test_audio_clip_rate_is_actual_verified_delivery_and_never_tokens_or_predicted_count():
    from platform_service.media.rates import normalize,cost
    meter={'unit':'audio_clip','basis':'verified_delivery_v1','numerator':'3','denominator':'2'}
    rate=normalize({'version':'fixture-audio-clip','meters':[meter]})
    assert cost(rate,{'audio_clip':1})==2 and cost(rate,{'audio_clip':2})==3
    for basis in ['provider_usage_v1','verified_input_v1']:
        with pytest.raises(PlatformError):normalize({'version':'fixture','meters':[{**meter,'basis':basis}]})
