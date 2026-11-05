"""Real local AV fixtures; no provider/network or substituted media evidence."""
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import struct
import sys
import wave
import io
import pytest
from b2_legacy_media_fixtures import legacy_media
from platform_service.errors import PlatformError


@pytest.mark.asyncio
async def test_recorded_ubuntu61_progress_endpoint_is_not_decoded_sample_duration(tmp_path,legacy_media):
    """Replay the independently recorded 6.1 protocol, not a real tool acceptance claim."""
    from platform_service.media.av_artifacts import AVTools, inspect_av
    from test_media_decoded_timeline import trace
    output=io.BytesIO()
    with wave.open(output,'wb') as stream:
        stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(16000)
        stream.writeframes(b'\0'*16000)
    tool=tmp_path/'recorded-protocol'
    tool.write_text(f'#!{sys.executable}\nimport sys,json\n'
                    "if '-version' in sys.argv: print('ffmpeg version 6.1.1-3ubuntu5')\n"
                    "elif '-show_frames' in sys.argv: print('frame|media_type=audio|stream_index=0|sample_fmt=s16|nb_samples=8000|channels=1|channel_layout=mono')\n"
                    "elif '-of' in sys.argv: print(json.dumps({'streams':[{'index':0,'codec_type':'audio','sample_rate':'16000','channels':1,'duration':'0.500000'}],'format':{'duration':'0.500000'}}))\n"
                    f"elif 'framehash' in sys.argv: sys.stdout.buffer.write({trace()!r})\n"
                    "else: print('out_time_us=384000\\nprogress=end')\n")
    tool.chmod(0o700)
    result=await legacy_media.decode(inspect_av,output.getvalue(),kind='audio',max_bytes=100000,max_duration_ms=1000,
                            tools=AVTools(tool,tool,'6.1.1-3ubuntu5'))
    assert result['properties']['duration_ms']==500


def test_shared_av_validator_contract_exists():
    assert importlib.util.find_spec('platform_service.media.av_artifacts') is not None


@pytest.fixture(scope='module')
def files(tmp_path_factory):
    folder=tmp_path_factory.mktemp('av-fixtures')
    ffmpeg=shutil.which('ffmpeg');ffprobe=shutil.which('ffprobe')
    assert ffmpeg and ffprobe,'Real ffmpeg and ffprobe are mandatory for AV validation tests'
    base=[ffmpeg,'-nostdin','-v','error','-y','-threads','1','-filter_threads','1','-filter_complex_threads','1']
    def generate(name,args):
        target=folder/name
        result=subprocess.run([*base,*args,'-threads:v','1','-threads:a','1',str(target)],capture_output=True,timeout=20)
        assert result.returncode==0,result.stderr[:200]
        return target.read_bytes()
    result={'video':generate('plain.mp4',['-f','lavfi','-i','color=c=blue:s=32x32:r=10:d=0.5','-c:v','mpeg4','-an']),
      'av':generate('audio-video.mp4',['-f','lavfi','-i','color=c=blue:s=32x32:r=10:d=0.5','-f','lavfi','-i','sine=frequency=440:duration=0.3','-c:v','mpeg4','-c:a','aac']),
      'wav':generate('tone.wav',['-f','lavfi','-i','sine=frequency=440:duration=0.5','-c:a','pcm_s16le']),
      'mp3':generate('tone.mp3',['-f','lavfi','-i','sine=frequency=440:duration=0.5','-c:a','libmp3lame']),
      'ogg':generate('tone.ogg',['-f','lavfi','-i','sine=frequency=440:duration=0.5','-c:a','libopus'])}
    result['tiny_av']=generate('tiny-audio.mp4',['-f','lavfi','-i','color=c=blue:s=32x32:r=10:d=0.5',
      '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=0.3','-af','atrim=end_sample=1',
      '-c:v','mpeg4','-c:a','pcm_s16le','-movie_timescale','48000'])
    version=subprocess.run([ffmpeg,'-version'],capture_output=True,text=True,timeout=5).stdout.splitlines()[0].split()[2]
    return result,(Path(ffmpeg),Path(ffprobe),version)


async def inspect(legacy_media,raw,tools,**kwargs):
    from platform_service.media.av_artifacts import AVTools,inspect_av
    options=dict(kind='audio',max_bytes=1024*1024,max_duration_ms=1000,max_pixels=1024,max_channels=2,max_sample_rate=48000)
    options.update(kwargs)
    return await legacy_media.decode(inspect_av,raw,tools=AVTools(*tools),**options)


@pytest.mark.asyncio
@pytest.mark.parametrize('case,expected', [('vfr',800),('bframes',500),('one-frame',100),('fractional',100)])
async def test_real_video_intervals_include_last_frame_and_preserve_demux_timebase(files,tmp_path,case,expected,legacy_media):
    _,tools=files
    source='color=c=blue:s=32x32:r=30000/1001' if case=='fractional' else 'color=c=blue:s=32x32:r=10:d=0.8' if case=='vfr' else 'color=c=blue:s=32x32:r=10:d=0.5'
    extra={'vfr':['-vf',"select='eq(n,0)+eq(n,1)+eq(n,4)+eq(n,7)'",'-fps_mode','vfr'],
           'bframes':['-bf','2'],'one-frame':['-frames:v','1'],'fractional':['-frames:v','3']}[case]
    path=tmp_path/'exact.mp4'
    result=subprocess.run([str(tools[0]),'-nostdin','-v','error','-filter_threads','1','-f','lavfi','-i',source,
                           '-c:v','mpeg4','-threads:v','1',*extra,'-an',str(path)],capture_output=True,timeout=20)
    assert result.returncode==0,result.stderr[:200]
    info=await inspect(legacy_media,path.read_bytes(),tools,kind='video')
    assert info['properties']['duration_ms']==expected


@pytest.mark.asyncio
async def test_real_aac_uses_all_decoded_samples_not_container_clipped_duration(files,tmp_path,legacy_media):
    _,tools=files;path=tmp_path/'tone.m4a'
    made=subprocess.run([str(tools[0]),'-nostdin','-v','error','-filter_threads','1','-f','lavfi','-i',
                         'sine=frequency=440:sample_rate=48000:duration=0.5','-c:a','aac','-threads:a','1',str(path)],capture_output=True,timeout=20)
    assert made.returncode==0,made.stderr[:200]
    pcm=subprocess.run([str(tools[0]),'-nostdin','-v','error','-threads','1','-i',str(path),'-map','0:a:0',
                        '-c:a','pcm_s16le','-threads:a','1','-f','s16le','pipe:1'],capture_output=True,timeout=20)
    assert pcm.returncode==0 and len(pcm.stdout)%2==0
    samples=len(pcm.stdout)//2
    assert samples>=24000
    info=await inspect(legacy_media,path.read_bytes(),tools,mime='audio/mp4')
    assert info['properties']['duration_ms']==samples*1000//48000
    assert info['sha256']==hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.asyncio
async def test_real_stereo_fractional_milliseconds_are_floored_once_after_exact_limit(files,legacy_media):
    _,tools=files;output=io.BytesIO()
    with wave.open(output,'wb') as stream:
        stream.setnchannels(2);stream.setsampwidth(2);stream.setframerate(44100);stream.writeframes(b'\0'*4004)
    assert (await inspect(legacy_media,output.getvalue(),tools))['properties']['duration_ms']==22
    with pytest.raises(PlatformError):await inspect(legacy_media,output.getvalue(),tools,max_duration_ms=22)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['audio','video'])
async def test_real_nine_hundred_second_media_is_not_truncated_by_trace_buffer(files,tmp_path,kind,legacy_media):
    _,tools=files
    if kind=='audio':
        path=tmp_path/'long.wav'
        with wave.open(str(path),'wb') as stream:
            stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(16000)
            for _ in range(900):stream.writeframes(b'\0'*32000)
    else:
        path=tmp_path/'long.mp4'
        made=subprocess.run([str(tools[0]),'-nostdin','-v','error','-filter_threads','1','-f','lavfi','-i',
                             'color=c=blue:s=32x32:r=30:d=900','-c:v','mpeg4','-threads:v','1','-g','30','-an',str(path)],capture_output=True,timeout=60)
        assert made.returncode==0,made.stderr[:200]
    raw=path.read_bytes()
    info=await inspect(legacy_media,raw,tools,kind=kind,max_bytes=33554432,max_duration_ms=900000)
    assert info['properties']['duration_ms']==900000
    assert info['sha256']==hashlib.sha256(raw).hexdigest() and info['size']==len(raw)


@pytest.mark.asyncio
async def test_real_display_matrix_rotation_preserves_encoded_dimensions(files,tmp_path,legacy_media):
    _,tools=files;source=tmp_path/'encoded.mp4';rotated=tmp_path/'rotated.mp4'
    made=subprocess.run([str(tools[0]),'-nostdin','-v','error','-filter_threads','1','-f','lavfi','-i',
                         'color=c=blue:s=32x16:r=10:d=0.5','-c:v','mpeg4','-threads:v','1','-an',str(source)],capture_output=True,timeout=20)
    assert made.returncode==0,made.stderr[:200]
    copied=subprocess.run([str(tools[0]),'-nostdin','-v','error','-display_rotation','90','-i',str(source),
                           '-c','copy',str(rotated)],capture_output=True,timeout=20)
    assert copied.returncode==0,copied.stderr[:200]
    probed=subprocess.run([str(tools[1]),'-v','error','-show_entries','stream_side_data=rotation','-of','json',str(rotated)],capture_output=True,timeout=20)
    import json
    assert abs(json.loads(probed.stdout)['streams'][0]['side_data_list'][0]['rotation'])==90
    raw=rotated.read_bytes();info=await inspect(legacy_media,raw,tools,kind='video')
    assert info['properties']['width']==32 and info['properties']['height']==16
    assert info['properties']['duration_ms']==500 and info['sha256']==hashlib.sha256(raw).hexdigest()


@pytest.mark.asyncio
@pytest.mark.parametrize('name,mime,fmt',[('wav','audio/wav','WAV'),('mp3','audio/mpeg','MP3'),('ogg','audio/ogg','OGG')])
async def test_real_audio_is_fully_decoded_with_exact_bytes_and_actual_audio_duration(files,name,mime,fmt,legacy_media):
    data,tools=files
    result=await inspect(legacy_media,data[name],tools,mime=mime)
    assert result['sha256']==hashlib.sha256(data[name]).hexdigest() and result['size']==len(data[name])
    assert result['mime']==mime and result['properties']['format']==fmt
    assert 490<=result['properties']['duration_ms']<=550
    assert result['properties']['channels']==1 and 8000<=result['properties']['sample_rate']<=48000


@pytest.mark.asyncio
async def test_real_video_separates_shorter_audio_track_duration_from_video_duration(files,legacy_media):
    data,tools=files
    result=await inspect(legacy_media,data['av'],tools,kind='video',mime='video/mp4',require_audio=True)
    props=result['properties']
    assert props['width']==props['height']==32 and 490<=props['duration_ms']<=550
    assert 250<=props['audio_duration_ms']<=400 and props['audio_duration_ms']<props['duration_ms']
    with pytest.raises(PlatformError):await inspect(legacy_media,data['video'],tools,kind='video',require_audio=True)
    assert 'audio_duration_ms' not in (await inspect(legacy_media,data['video'],tools,kind='video'))['properties']


@pytest.mark.asyncio
@pytest.mark.parametrize('options',[{'max_bytes':10},{'max_duration_ms':100},{'kind':'video','max_pixels':10},{'mime':'application/json'}])
async def test_real_media_resource_limits_and_mime_are_enforced(files,options,legacy_media):
    data,tools=files
    with pytest.raises(PlatformError):await inspect(legacy_media,data['video'] if options.get('kind')=='video' else data['wav'],tools,**options)


@pytest.mark.asyncio
@pytest.mark.parametrize('name',['video','wav','mp3','ogg'])
async def test_truncated_or_corrupt_real_media_never_counts_as_delivery(files,name,legacy_media):
    data,tools=files
    raw=data[name]
    with pytest.raises(PlatformError):await inspect(legacy_media,raw[:len(raw)//2],tools,kind='video' if name=='video' else 'audio')


def external_dref(raw):
    marker=raw.find(b'url ')
    assert marker>4 and raw[marker+4:marker+8]==b'\x00\x00\x00\x01'
    result=bytearray(raw);result[marker+7]=0
    return bytes(result)


@pytest.mark.asyncio
async def test_external_mov_dref_and_local_playlist_are_rejected_before_tools_can_open_paths(files,tmp_path,legacy_media):
    from platform_service.media.av_artifacts import AVTools,inspect_av
    data,_=files
    unavailable=AVTools(tmp_path/'not-a-tool',tmp_path/'not-a-probe','unavailable')
    with pytest.raises(PlatformError) as error:
        await legacy_media.decode(inspect_av,external_dref(data['video']),kind='video',max_bytes=1048576,max_duration_ms=1000,max_pixels=1024,tools=unavailable)
    assert error.value.code=='media_external_reference_forbidden'
    sentinel=tmp_path/'private';sentinel.write_bytes(b'PRIVATE_SENTINEL_DO_NOT_OPEN')
    playlist=('ffconcat version 1.0\nfile '+str(sentinel)+'\n').encode()
    with pytest.raises(PlatformError) as error:
        await legacy_media.decode(inspect_av,playlist,kind='video',max_bytes=1048576,max_duration_ms=1000,max_pixels=1024,tools=unavailable)
    assert error.value.code=='invalid_media_artifact' and sentinel.read_bytes()==b'PRIVATE_SENTINEL_DO_NOT_OPEN'


@pytest.mark.asyncio
async def test_validator_accepts_bytes_only_and_requires_explicit_verified_tool_version(files,tmp_path,legacy_media):
    from platform_service.media.av_artifacts import AVTools,inspect_av
    data,tools=files
    link=tmp_path/'link';link.symlink_to(tmp_path/'private')
    for raw in [link,str(link),'https://not-allowed.example/file.mp4']:
        with pytest.raises(PlatformError):await legacy_media.decode(inspect_av,raw,kind='video',max_bytes=1048576,max_duration_ms=1000,max_pixels=1024,tools=AVTools(*tools))
    with pytest.raises(PlatformError):await inspect(legacy_media,data['wav'],(tools[0],tools[1],'unexpected-version'))


@pytest.mark.asyncio
async def test_quicktime_brand_is_not_silently_reported_as_mp4(files,legacy_media):
    data,tools=files
    raw=bytearray(data['video']);raw[8:12]=b'qt  '
    with pytest.raises(PlatformError):await inspect(legacy_media,bytes(raw),tools,kind='video',mime='video/mp4')



def extend_declared_audio_track(raw,*,media_header=True,edit=True):
    result=bytearray(raw)
    def boxes(start,end):
        while start<end:
            size,kind=struct.unpack_from('>I4s',result,start)
            assert size>=8 and start+size<=end
            yield kind,start+8,start+size
            start+=size
    def child(start,end,kind):
        return next((a,b) for name,a,b in boxes(start,end) if name==kind)
    moov=child(0,len(result),b'moov');mvhd=child(*moov,b'mvhd')
    movie_scale=struct.unpack_from('>I',result,mvhd[0]+12)[0]
    for kind,a,b in boxes(*moov):
        if kind!=b'trak':continue
        mdia=child(a,b,b'mdia');hdlr=child(*mdia,b'hdlr')
        if result[hdlr[0]+8:hdlr[0]+12]!=b'soun':continue
        mdhd=child(*mdia,b'mdhd');assert result[mdhd[0]]==0
        scale=struct.unpack_from('>I',result,mdhd[0]+12)[0]
        duration=struct.unpack_from('>I',result,mdhd[0]+16)[0]
        if media_header:struct.pack_into('>I',result,mdhd[0]+16,duration+scale//5)
        edts=child(a,b,b'edts');elst=child(*edts,b'elst');assert result[elst[0]]==0
        length=struct.unpack_from('>I',result,elst[0]+8)[0]
        if edit:struct.pack_into('>I',result,elst[0]+8,length+movie_scale//5)
        return bytes(result)
    raise AssertionError('Fixture must contain an audio track')


@pytest.mark.asyncio
@pytest.mark.parametrize('media_header,edit',[(True,True),(True,False),(False,True)])
async def test_audio_track_declared_duration_cannot_hide_missing_tail_samples_under_valid_video(files,media_header,edit,legacy_media):
    data,tools=files
    corrupted=extend_declared_audio_track(data['av'],media_header=media_header,edit=edit)
    with pytest.raises(PlatformError):await inspect(legacy_media,corrupted,tools,kind='video',require_audio=True)


def timing_version_one(raw):
    # Fixture mdat is before moov, so extending metadata does not move samples.
    assert raw.find(b'mdat')<raw.find(b'moov')
    def rewrite(start,end):
        result=bytearray()
        while start<end:
            size,kind=struct.unpack_from('>I4s',raw,start)
            assert size>=8 and start+size<=end
            body=raw[start+8:start+size]
            if kind in {b'moov',b'trak',b'mdia',b'edts'}:body=rewrite(start+8,start+size)
            elif kind in {b'mvhd',b'mdhd'}:
                assert body[0]==0
                created,modified,scale,duration=struct.unpack_from('>IIII',body,4)
                body=b'\x01'+body[1:4]+struct.pack('>QQIQ',created,modified,scale,duration)+body[20:]
            elif kind==b'elst':
                assert body[0]==0 and struct.unpack_from('>I',body,4)[0]==1
                length,media_time,rate=struct.unpack_from('>IiI',body,8)
                body=b'\x01'+body[1:8]+struct.pack('>QqI',length,media_time,rate)
            result.extend(struct.pack('>I4s',len(body)+8,kind)+body);start+=size
        return bytes(result)
    return rewrite(0,len(raw))


@pytest.mark.asyncio
async def test_64bit_track_timing_keeps_actual_audio_length_and_rejects_missing_tail(files,legacy_media):
    data,tools=files
    valid=timing_version_one(data['av'])
    result=await inspect(legacy_media,valid,tools,kind='video',require_audio=True)
    assert result['sha256']==hashlib.sha256(valid).hexdigest()
    assert 250<=result['properties']['audio_duration_ms']<=400
    assert result['properties']['audio_duration_ms']<result['properties']['duration_ms']
    with pytest.raises(PlatformError):
        await inspect(legacy_media,timing_version_one(extend_declared_audio_track(data['av'])),tools,kind='video',require_audio=True)


@pytest.mark.asyncio
async def test_present_audio_track_cannot_be_delivered_as_zero_milliseconds(files,legacy_media):
    data,tools=files
    with pytest.raises(PlatformError):await inspect(legacy_media,data['tiny_av'],tools,kind='video',require_audio=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('box,offset,fmt,value',[
    (b'mdhd',0,'>B',2),(b'mdhd',12,'>I',0),(b'mdhd',16,'>I',0xFFFFFFFF),
    (b'elst',4,'>I',2),(b'elst',12,'>i',-1),(b'elst',16,'>I',0),
])
async def test_unknown_or_unsupported_track_timing_is_closed_before_external_tools(files,tmp_path,box,offset,fmt,value,legacy_media):
    from platform_service.media.av_artifacts import AVTools,inspect_av
    data,_=files
    raw=bytearray(data['av']);marker=raw.rfind(box);assert marker>0
    struct.pack_into(fmt,raw,marker+4+offset,value)
    unavailable=AVTools(tmp_path/'not-a-tool',tmp_path/'not-a-probe','unavailable')
    with pytest.raises(PlatformError) as error:
        await legacy_media.decode(inspect_av,bytes(raw),kind='video',max_bytes=1048576,max_duration_ms=1000,max_pixels=1024,tools=unavailable)
    assert error.value.code=='invalid_media_artifact'


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['video', 'audio'])
async def test_native_parameter_changes_cannot_be_hidden_by_output_conversion(files,tmp_path,kind,legacy_media):
    _,tools=files
    def make(name,args):
        target=tmp_path/name
        result=subprocess.run([str(tools[0]),'-nostdin','-v','error','-threads','1','-filter_threads','1',
                               *args,'-threads:v','1','-threads:a','1',str(target)],capture_output=True,timeout=10)
        assert result.returncode==0,result.stderr[:200]
        return target.read_bytes()
    if kind=='video':
        parts=[make(f'{size}.h264',['-f','lavfi','-i',f'color=c=blue:s={size}x{size}:r=10:d=0.3',
                                  '-c:v','libx264','-x264-params','keyint=1','-f','h264']) for size in (32,64)]
        joined=tmp_path/'joined.h264';joined.write_bytes(b''.join(parts))
        raw=make('dynamic.mp4',['-r','10','-i',str(joined),'-c:v','copy'])
    else:
        parts=[make(f'{channels}.mp3',['-f','lavfi','-i','sine=frequency=440:sample_rate=16000:duration=0.02',
                                    '-ac',str(channels),'-c:a','libmp3lame','-write_xing','0','-id3v2_version','0']) for channels in (1,2)]
        raw=parts[0]+parts[1]+parts[0]
    with pytest.raises(PlatformError):
        await inspect(legacy_media,raw,tools,kind=kind,max_channels=1,max_sample_rate=16000)


@pytest.mark.asyncio
@pytest.mark.parametrize('case',['same-area-geometry','pixel-format'])
async def test_real_native_video_changes_rejected_even_within_pixel_budget(files,tmp_path,case,legacy_media):
    import json
    _,tools=files;parts=[]
    specifications=[('32x16','yuv420p'),('16x32','yuv420p')] if case=='same-area-geometry' else [('32x32','yuv420p'),('32x32','yuv444p')]
    for i,(size,pixel) in enumerate(specifications):
        path=tmp_path/f'{i}.h264'
        made=subprocess.run([str(tools[0]),'-nostdin','-v','error','-filter_threads','1','-f','lavfi','-i',
            f'color=c=blue:s={size}:r=10:d=0.3','-pix_fmt',pixel,'-c:v','libx264','-threads:v','1',
            '-x264-params','keyint=1','-f','h264',str(path)],capture_output=True,timeout=10)
        assert made.returncode==0,made.stderr[:200]
        parts.append(path.read_bytes())
    source=tmp_path/'joined.h264';source.write_bytes(b''.join(parts));target=tmp_path/'changed.mp4'
    made=subprocess.run([str(tools[0]),'-nostdin','-v','error','-r','10','-i',str(source),'-c:v','copy',str(target)],capture_output=True,timeout=10)
    assert made.returncode==0,made.stderr[:200]
    native=subprocess.run([str(tools[1]),'-v','error','-threads','1','-show_frames','-show_entries',
                          'frame=width,height,pix_fmt','-of','json',str(target)],capture_output=True,timeout=10)
    assert native.returncode==0
    signatures={(v['width'],v['height'],v['pix_fmt']) for v in json.loads(native.stdout)['frames']}
    assert len(signatures)==2 and all(w*h<=1024 for w,h,_ in signatures)
    with pytest.raises(PlatformError):await inspect(legacy_media,target.read_bytes(),tools,kind='video')


@pytest.mark.asyncio
@pytest.mark.parametrize('rates',[(16000,48000,16000),(48000,16000,48000),(16000,48000,8000,48000,16000)])
async def test_short_and_compensating_rate_changes_fail_at_direct_input_gate(files,tmp_path,monkeypatch,rates,legacy_media):
    from platform_service.media import av_artifacts
    _,tools=files;parts={}
    for rate in set(rates):
        path=tmp_path/f'{rate}.mp3'
        made=subprocess.run([str(tools[0]),'-nostdin','-v','error','-filter_threads','1','-f','lavfi','-i',
            f'sine=frequency=440:sample_rate={rate}:duration=0.001','-ac','1','-c:a','libmp3lame','-threads:a','1',
            '-write_xing','0','-id3v2_version','0',str(path)],capture_output=True,timeout=10)
        assert made.returncode==0,made.stderr[:200];parts[rate]=path.read_bytes()
    raw=b''.join(parts[rate] for rate in rates)
    real=av_artifacts._execute_stream;direct=[]
    async def observe(executable,args,timeline,**kwargs):
        if '-reinit_filter' in args:
            assert args[args.index('-reinit_filter')+1]=='0'
            checked=subprocess.run([executable,*args],capture_output=True,timeout=10)
            direct.append(checked.returncode)
            assert checked.returncode!=0
            assert b'Changing audio frame properties' in checked.stderr
        return await real(executable,args,timeline,**kwargs)
    monkeypatch.setattr(av_artifacts,'_execute_stream',observe)
    with pytest.raises(PlatformError):await inspect(legacy_media,raw,tools)
    # Native audit passed; the direct decoder gate failed before the final
    # duration-declaration comparison. A tolerance rejection cannot pass this.
    assert len(direct)==1


@pytest.mark.asyncio
async def test_av_native_audit_covers_both_tracks_once_and_three_children_share_deadline(files,monkeypatch,legacy_media):
    from platform_service.media import av_artifacts
    raw,tools=files;real=av_artifacts._execute_stream;observed=[]
    async def observe(executable,args,timeline,**kwargs):
        observed.append((tuple(args),kwargs['deadline']))
        return await real(executable,args,timeline,**kwargs)
    monkeypatch.setattr(av_artifacts,'_execute_stream',observe)
    info=await inspect(legacy_media,raw['av'],tools,kind='video')
    assert info['properties']['duration_ms']==500
    assert len(observed)==3 and len({deadline for _,deadline in observed})==1
    assert '-show_frames' in observed[0][0] and '-select_streams' not in observed[0][0]
    assert observed[1][0][observed[1][0].index('-map')+1]=='0:v:0'
    assert observed[2][0][observed[2][0].index('-map')+1]=='0:a:0'
