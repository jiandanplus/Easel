"""Closed decoded timeline protocol and real subprocess lifecycle; no PG/network."""
import asyncio
from fractions import Fraction
import importlib.util
import os
from pathlib import Path
import sys
import tracemalloc

import pytest
from platform_service.errors import PlatformError


HASH = '0' * 64


def trace(kind='audio', *, rate=16000, tb=None, durations=(2048, 2048, 2048, 1856), channels=1):
    tb = tb or (f'1/{rate}' if kind == 'audio' else '1/10240')
    header = (f'#format: frame checksums\n#version: 2\n#hash: SHA256\n#software: Lavf60.16.100\n'
              f'#tb 0: {tb}\n#media_type 0: {kind}\n#codec_id 0: {"pcm_s16le" if kind == "audio" else "rawvideo"}\n')
    header += (f'#sample_rate 0: {rate}\n#channel_layout_name 0: {"mono" if channels == 1 else "stereo"}\n'
               if kind == 'audio' else '#dimensions 0: 32x32\n#sar 0: 1/1\n')
    header += '#stream#, dts, pts, duration, size, hash\n'
    pts = 0
    for duration in durations:
        header += f'0, {pts}, {pts}, {duration}, {duration*channels*2 if kind == "audio" else 1536}, {HASH}\n'
        pts += duration
    return header.encode()


def parser(kind='audio', maximum=900000, rate=16000, channels=1, time_base=None):
    assert importlib.util.find_spec('platform_service.media.decoded_timeline') is not None, 'missing full decoded timeline measurement'
    from platform_service.media.decoded_timeline import DecodedTimeline
    return DecodedTimeline(kind=kind, maximum_ms=maximum, sample_rate=rate if kind == 'audio' else None,
                           channels=channels if kind == 'audio' else None,
                           dimensions=(32, 32) if kind == 'video' else None,
                           time_base=time_base if kind == 'video' else None)


@pytest.mark.parametrize('chunk', [1, 7, 4096])
@pytest.mark.parametrize('kind,rate,channels,tb,durations,expected', [
    ('audio',16000,1,'1/16000',(2048,2048,2048,1856),500),
    ('audio',16000,1,'1/16000',(47,*([576]*13),465),500),
    ('audio',48000,1,'1/48000',(648,*([960]*24),312),500),
    ('audio',44100,2,'1/44100',(1001,),22),
    ('audio',48000,1,'1/48000',tuple([1024]*24),512),
    ('video',0,0,'1/10240',(1024,3072,3072,1024),800),
    ('video',0,0,'1/10240',(1024,)*5,500),
    ('video',0,0,'1/10240',(1024,),100),
    ('video',0,0,'1/30000',(1001,)*3,100),
])
def test_exact_decoded_samples_and_presentation_intervals(chunk,kind,rate,channels,tb,durations,expected):
    value=parser(kind,rate=rate,channels=channels,time_base=Fraction(tb))
    raw=trace(kind,rate=rate,channels=channels,tb=tb,durations=durations)
    for i in range(0,len(raw),chunk):value.feed(raw[i:i+chunk])
    assert value.finish()==expected


@pytest.mark.parametrize('kind,rate,quantum,total', [('audio',48000,1024,43200000),('video',0,1,27000)])
def test_nine_hundred_seconds_streams_exceed_old_buffer_without_frame_list(kind,rate,quantum,total):
    value=parser(kind,rate=rate,time_base=Fraction(1,30))
    value.feed(trace(kind,rate=rate,tb='1/30' if kind=='video' else None,durations=()))
    tracemalloc.start();baseline=tracemalloc.get_traced_memory()[0]
    pts=0;output_size=0
    while pts<total:
        duration=min(quantum,total-pts)
        row=f'0, {pts}, {pts}, {duration}, {duration*2 if kind=="audio" else 1536}, {HASH}\n'.encode()
        value.feed(row);output_size+=len(row);pts+=duration
    _,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    assert value.finish()==900000 and output_size>131072
    assert peak-baseline<262144


@pytest.mark.parametrize('before,after', [
    (b'#version: 2\n',b'#version: 2\n#version: 2\n'),
    (b'#version: 2\n',b'#version: 2\n#unknown: x\n'),
    (b'#hash: SHA256',b'#hash: MD5'),(b'1/16000',b'0/16000'),
    (b'#sample_rate 0: 16000',b'#sample_rate 0: 48000'),
    (b'0, 0, 0, 2048, 4096,',b'1, 0, 0, 2048, 4096,'),
    (b'0, 0, 0, 2048, 4096,',b'0, 0, 0, 0, 4096,'),
    (b'0, 0, 0, 2048, 4096,',b'0, 0, 0, 2048, 4098,'),
    (b'0, 0, 0, 2048, 4096,',b'0, 0, 0, 2e3, 4096,'),
    (b'0, 0, 0, 2048, 4096,',b'0, 0, 0, 9223372036854775808, 4096,'),
    (b'0, 2048, 2048,',b'0, 2049, 2049,'),
    (b'0, 2048, 2048,',b'0, 2047, 2047,'),
    (b'0, 2048, 2048,',b'0, 2047, 2048,'),
    (b'0, 0, 0,',b'0, -1, -1,'),
    (b'#codec_id 0: pcm_s16le',b'#codec_id 0: rawvideo'),
    (b'#media_type 0: audio',b'#media_type 0: video'),
])
def test_closed_trace_rejects_ambiguous_or_inconsistent_data(before,after):
    value=parser()
    with pytest.raises(ValueError):value.feed(trace().replace(before,after));value.finish()


@pytest.mark.parametrize('raw', [b'', trace(durations=()), trace()[:-1], trace()+b'#version: 2\n', trace()+b'\xff\n', b'#'+b'x'*1024])
def test_empty_partial_trailing_and_unbounded_lines_fail(raw):
    value=parser()
    with pytest.raises(ValueError):value.feed(raw);value.finish()


def test_fractional_maximum_checked_before_floor_and_video_timebase_must_match():
    with pytest.raises(ValueError):
        value=parser(maximum=22,rate=44100,channels=2);value.feed(trace(rate=44100,channels=2,durations=(1001,)));value.finish()
    with pytest.raises(ValueError):
        value=parser('video',time_base=Fraction(1,10240));value.feed(trace('video',tb='3/20',durations=(1,2,2,1)));value.finish()
    with pytest.raises(ValueError):
        value=parser(rate=48000);value.feed(trace(rate=48000,durations=(1,)));value.finish()


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['timeout','cancel','nonzero','oversize'])
async def test_stream_executor_reaps_child_on_every_failed_terminal(tmp_path,case):
    from platform_service.media import av_artifacts
    assert hasattr(av_artifacts,'_execute_stream'), 'missing bounded stream execution'
    pidfile=tmp_path/'pid'
    child=tmp_path/'child.py'
    child.write_text('import os,sys,time\nfrom pathlib import Path\n'
                     f'Path({str(pidfile)!r}).write_text(str(os.getpid()))\n'
                     f'sys.stdout.buffer.write({trace()!r});sys.stdout.flush()\n'+
                     ("sys.stdout.write('x'*200000);sys.stdout.flush()\n" if case=='oversize' else '')+
                     ('sys.exit(1)\n' if case=='nonzero' else 'time.sleep(10)\n'))
    task=asyncio.create_task(av_artifacts._execute_stream(sys.executable,['-I',str(child)],parser(),timeout=0.3 if case=='timeout' else 5))
    for _ in range(200):
        if pidfile.exists():break
        await asyncio.sleep(0.005)
    assert pidfile.exists()
    if case=='cancel':
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
    else:
        with pytest.raises(PlatformError) as error:await asyncio.wait_for(task,2)
        assert error.value.code=='invalid_media_artifact'
    with pytest.raises(ProcessLookupError):os.kill(int(pidfile.read_text()),0)


@pytest.mark.asyncio
async def test_stream_success_requires_eof_exit_zero_and_clean_environment(tmp_path,monkeypatch):
    from platform_service.media import av_artifacts
    assert hasattr(av_artifacts,'_execute_stream')
    monkeypatch.setenv('EASEL_TEST_SECRET_SENTINEL','must-not-be-inherited')
    child=tmp_path/'child.py'
    child.write_text('import os,sys,resource\n'
                     "assert 'EASEL_TEST_SECRET_SENTINEL' not in os.environ\n"
                     'assert resource.getrlimit(resource.RLIMIT_CPU)==(30,30)\n'
                     'assert resource.getrlimit(resource.RLIMIT_NOFILE)==(32,32)\n'
                     'assert resource.getrlimit(resource.RLIMIT_FSIZE)==(1048576,1048576)\n'
                     f'sys.stdout.buffer.write({trace()!r})\n')
    assert await av_artifacts._execute_stream(sys.executable,['-I',str(child)],parser())==500


@pytest.mark.asyncio
async def test_cancellation_during_spawn_cannot_leave_late_child(monkeypatch):
    from platform_service.media import av_artifacts
    create=asyncio.create_subprocess_exec
    created=asyncio.Event();release=asyncio.Event();processes=[]
    async def delayed(*args,**kwargs):
        process=await create(*args,**kwargs);processes.append(process);created.set()
        await release.wait()
        return process
    monkeypatch.setattr(av_artifacts.asyncio,'create_subprocess_exec',delayed)
    task=asyncio.create_task(av_artifacts._execute_stream(sys.executable,['-I','-c','import time;time.sleep(10)'],parser()))
    await asyncio.wait_for(created.wait(),2)
    task.cancel();release.set()
    try:
        with pytest.raises(asyncio.CancelledError):await asyncio.wait_for(task,2)
        with pytest.raises(ProcessLookupError):os.kill(processes[0].pid,0)
    finally:
        for process in processes:
            if process.returncode is None:process.kill()
            await process.communicate()


def native_parser(maximum=1000):
    from platform_service.media.decoded_timeline import NativeFrameAudit
    return NativeFrameAudit(streams=[{'index':2,'codec_type':'video','width':32,'height':32,'time_base':'1/10240'},
                                     {'index':5,'codec_type':'audio','channels':1,'sample_rate':'16000'}],
                            maximum_ms=maximum,max_pixels=1024,max_channels=1)


NATIVE_VIDEO=b'frame|media_type=video|stream_index=2|width=32|height=32|pix_fmt=yuv420p|\n'
NATIVE_AUDIO=b'frame|media_type=audio|stream_index=5|sample_fmt=fltp|nb_samples=8000|channels=1|channel_layout=mono\n'


@pytest.mark.parametrize('chunk',[1,7,4096])
def test_native_audit_preserves_original_stream_indices_and_scalar_totals(chunk):
    value=native_parser();raw=NATIVE_VIDEO+NATIVE_AUDIO
    for i in range(0,len(raw),chunk):value.feed(raw[i:i+chunk])
    assert value.finish()=={2:{'frames':1,'samples':0},5:{'frames':1,'samples':8000}}


@pytest.mark.parametrize('bad', [
    NATIVE_VIDEO.replace(b'width=32',b'width=64'),
    NATIVE_VIDEO.replace(b'pix_fmt=yuv420p',b'pix_fmt=yuv444p'),
    NATIVE_AUDIO.replace(b'channels=1',b'channels=2'),
    NATIVE_AUDIO.replace(b'sample_fmt=fltp',b'sample_fmt=s16p'),
    NATIVE_AUDIO.replace(b'channel_layout=mono',b'channel_layout=stereo'),
    NATIVE_AUDIO.replace(b'|channel_layout=mono',b''),
    NATIVE_AUDIO.replace(b'stream_index=5',b'stream_index=2'),
    NATIVE_AUDIO.replace(b'stream_index=5',b'stream_index=1'),
    NATIVE_AUDIO.replace(b'nb_samples=8000',b'nb_samples=0'),
    NATIVE_AUDIO.replace(b'nb_samples=8000',b'nb_samples=9223372036854775808'),
    NATIVE_AUDIO.replace(b'nb_samples=8000',b'nb_samples=1e3'),
    NATIVE_AUDIO.replace(b'nb_samples=8000',b'nb_samples=1|nb_samples=1'),
    NATIVE_AUDIO.replace(b'nb_samples=8000',b'nb_samples=1|unknown=1'),
    NATIVE_AUDIO[:-1],b'unknown\n',b'x'*1025,b'\xff\n',
])
def test_native_audit_rejects_changed_or_unbound_or_partial_frames(bad):
    value=native_parser()
    with pytest.raises(ValueError):
        value.feed(NATIVE_VIDEO+NATIVE_AUDIO);value.feed(bad);value.finish()


def test_native_empty_missing_stream_and_sample_budget_rejected():
    for raw in (b'',NATIVE_VIDEO,NATIVE_VIDEO+NATIVE_AUDIO*3):
        with pytest.raises(ValueError):
            value=native_parser();value.feed(raw);value.finish()


def test_native_unknown_layout_may_be_stably_absent():
    value=native_parser();value.feed(NATIVE_VIDEO+NATIVE_AUDIO.replace(b'|channel_layout=mono',b''))
    assert value.finish()[5]['samples']==8000


@pytest.mark.asyncio
async def test_stream_deadline_includes_spawn_and_late_child_is_reaped(tmp_path,monkeypatch):
    from platform_service.media import av_artifacts
    real_spawn=asyncio.create_subprocess_exec;pidfile=tmp_path/'late-pid'
    async def late(*args,**kwargs):
        process=await real_spawn(*args,**kwargs)
        pidfile.write_text(str(process.pid))
        await asyncio.sleep(0.15)
        return process
    monkeypatch.setattr(asyncio,'create_subprocess_exec',late)
    with pytest.raises(PlatformError):
        await av_artifacts._execute_stream(sys.executable,['-c',f'import sys;sys.stdout.buffer.write({trace()!r})'],parser(),timeout=0.03)
    assert pidfile.exists()
    with pytest.raises(ProcessLookupError):os.kill(int(pidfile.read_text()),0)


@pytest.mark.parametrize('kind', ['video','audio'])
def test_native_nine_hundred_seconds_is_constant_memory(kind):
    from platform_service.media.decoded_timeline import NativeFrameAudit
    stream=({'index':0,'codec_type':'video','width':32,'height':32,'time_base':'1/30'} if kind=='video'
            else {'index':0,'codec_type':'audio','channels':1,'sample_rate':'48000'})
    value=NativeFrameAudit(streams=[stream],maximum_ms=900000,max_pixels=1024,max_channels=1)
    total=27000 if kind=='video' else 43200000
    current=0;size=0
    tracemalloc.start();baseline=tracemalloc.get_traced_memory()[0]
    while current<total:
        count=1 if kind=='video' else min(1024,total-current)
        row=(b'frame|media_type=video|stream_index=0|width=32|height=32|pix_fmt=yuv420p\n' if kind=='video'
             else f'frame|media_type=audio|stream_index=0|sample_fmt=fltp|channels=1|nb_samples={count}\n'.encode())
        value.feed(row);current+=count;size+=len(row)
    _,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    result=value.finish()[0]
    assert result['frames' if kind=='video' else 'samples']==total
    assert size>131072 and peak-baseline<262144


@pytest.mark.parametrize('kind', ['video','audio'])
def test_framehash_must_match_native_frame_or_sample_total(kind):
    from platform_service.media.decoded_timeline import DecodedTimeline
    options=({'dimensions':(32,32),'time_base':Fraction(1,10240),'expected_frames':5} if kind=='video'
             else {'sample_rate':16000,'channels':1,'expected_samples':8001})
    value=DecodedTimeline(kind=kind,maximum_ms=1000,**options)
    with pytest.raises(ValueError):value.feed(trace(kind));value.finish()


@pytest.mark.asyncio
async def test_expired_shared_deadline_never_spawns_next_child(monkeypatch):
    from platform_service.media import av_artifacts
    async def forbidden(*args,**kwargs):pytest.fail('expired budget spawned another child')
    monkeypatch.setattr(av_artifacts,'_spawn_limited',forbidden)
    with pytest.raises(PlatformError):
        await av_artifacts._execute_stream(sys.executable,[],parser(),deadline=asyncio.get_running_loop().time()-1)


@pytest.mark.asyncio
async def test_two_stream_phases_share_remaining_wall_and_reap(tmp_path):
    from platform_service.media import av_artifacts
    deadline=asyncio.get_running_loop().time()+0.25
    first=tmp_path/'first.py'
    first.write_text(f'import time,sys;time.sleep(0.16);sys.stdout.buffer.write({trace()!r})')
    assert await av_artifacts._execute_stream(sys.executable,['-I',str(first)],parser(),deadline=deadline)==500
    pidfile=tmp_path/'second-pid';second=tmp_path/'second.py'
    second.write_text(f'import os,time,sys;from pathlib import Path;Path({str(pidfile)!r}).write_text(str(os.getpid()));time.sleep(0.16);sys.stdout.buffer.write({trace()!r})')
    with pytest.raises(PlatformError):
        await av_artifacts._execute_stream(sys.executable,['-I',str(second)],parser(),deadline=deadline)
    assert pidfile.exists()
    with pytest.raises(ProcessLookupError):os.kill(int(pidfile.read_text()),0)


@pytest.mark.asyncio
@pytest.mark.parametrize('case',['cancel','nonzero','partial','unknown','oversize'])
async def test_native_executor_failed_terminal_reaps_real_child(tmp_path,case):
    from platform_service.media import av_artifacts
    pidfile=tmp_path/'pid';child=tmp_path/'native-child.py'
    raw=NATIVE_VIDEO+NATIVE_AUDIO
    suffix={'cancel':'time.sleep(10)','nonzero':'sys.exit(1)','partial':"sys.stdout.write('frame|')",
            'unknown':"sys.stdout.write('unknown\\n')",'oversize':"sys.stdout.write('x'*200000)"}[case]
    child.write_text(f'import os,sys,time;from pathlib import Path;Path({str(pidfile)!r}).write_text(str(os.getpid()));sys.stdout.buffer.write({raw!r});sys.stdout.flush();'+suffix)
    task=asyncio.create_task(av_artifacts._execute_stream(sys.executable,['-I',str(child)],native_parser(),timeout=2))
    if case=='cancel':
        for _ in range(100):
            if pidfile.exists():break
            await asyncio.sleep(0.005)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
    else:
        with pytest.raises(PlatformError):await task
    assert pidfile.exists()
    with pytest.raises(ProcessLookupError):os.kill(int(pidfile.read_text()),0)
