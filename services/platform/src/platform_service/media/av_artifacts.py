"""Explicit local formats, forbidden external references and full AV decode.

No supplied path/URL is opened. Only a private copy of the owned bytes is passed
through fixed demuxers, with MOV drefs disabled and no playlist/network formats.
"""
from ..media_maintenance.runtime import mutation,physical_guard,current_permit,GuardedDatabase,physical_call,operation
import asyncio
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys
import tempfile
from ..errors import PlatformError
from .rates import seconds_to_milliseconds
from .decoded_timeline import DecodedTimeline, NativeFrameAudit, time_base


_DECODE_WALL_SECONDS = 60


@dataclass(frozen=True)
class AVTools:
    ffmpeg: Path
    ffprobe: Path
    version: str

    def paths(self):
        result=[]
        for value in (self.ffmpeg,self.ffprobe):
            path=Path(value)
            if not path.is_absolute():raise PlatformError(503,'media_decoder_unavailable')
            try:
                resolved=path.resolve(strict=True);info=resolved.stat()
                if (not stat.S_ISREG(info.st_mode) or info.st_mode&0o022 or not info.st_mode&0o111
                    or info.st_uid not in (0,os.getuid())):raise ValueError
            except (OSError,ValueError):raise PlatformError(503,'media_decoder_unavailable') from None
            result.append(str(resolved))
        if not isinstance(self.version,str) or not re.fullmatch(r'[A-Za-z0-9_.+-]{1,80}',self.version):
            raise PlatformError(503,'media_decoder_unavailable')
        return tuple(result)


async def _spawn_limited(executable,args):
    code=("import os,resource,sys;resource.setrlimit(resource.RLIMIT_CPU,(30,30));"
          "resource.setrlimit(resource.RLIMIT_NOFILE,(32,32));"
          "resource.setrlimit(resource.RLIMIT_FSIZE,(1048576,1048576));"
          "resource.setrlimit(resource.RLIMIT_AS,(536870912,536870912)) if sys.platform=='linux' else None;"
          "os.execve(sys.argv[1],sys.argv[1:],{'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'})")
    pending=asyncio.create_task(asyncio.create_subprocess_exec(sys.executable,'-I','-c',code,executable,*args,
        stdin=asyncio.subprocess.DEVNULL,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,
        env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','PYTHONNOUSERSITE':'1'}))
    try:return await asyncio.shield(pending)
    except asyncio.CancelledError:
        # Cancellation may arrive after the OS child exists but before the
        # Process handle reaches this coroutine. Retain ownership until reaped.
        while not pending.done():
            try:await asyncio.shield(pending)
            except asyncio.CancelledError:pass
        try:process=pending.result()
        except (OSError,asyncio.CancelledError):pass
        else:await _reap(process)
        raise


async def _reap(process):
    if process.returncode is None:
        try:process.kill()
        except ProcessLookupError:pass
    async def drain():
        # A paused stdout pipe can keep asyncio.wait() pending even after kill.
        # Drain the finite buffered bytes without retaining failed output.
        while await process.stdout.read(4096):pass
        await process.wait()
    cleanup=asyncio.create_task(drain())
    cancelled=False
    while not cleanup.done():
        try:await asyncio.shield(cleanup)
        except asyncio.CancelledError:cancelled=True
    cleanup.result()
    if cancelled:raise asyncio.CancelledError


async def _execute(executable,args,*,timeout=30,maximum=131072):
    process=None
    async def read():
        output=bytearray()
        while True:
            chunk=await process.stdout.read(4096)
            if not chunk:return bytes(output)
            if len(output)+len(chunk)>maximum:raise ValueError
            output.extend(chunk)
    try:
        process=await _spawn_limited(executable,args)
        async with asyncio.timeout(timeout):
            output=await read();await process.wait()
        if process.returncode!=0:raise ValueError
        return output
    except (OSError,ValueError,TimeoutError):raise PlatformError(422,'invalid_media_artifact') from None
    finally:
        if process is not None:await _reap(process)


async def _execute_stream(executable,args,timeline,*,timeout=60,deadline=None):
    process=None
    if deadline is None:deadline=asyncio.get_running_loop().time()+timeout
    try:
        if asyncio.get_running_loop().time()>=deadline:raise TimeoutError
        async with asyncio.timeout_at(deadline):
            process=await _spawn_limited(executable,args)
            while True:
                chunk=await process.stdout.read(4096)
                if not chunk:break
                timeline.feed(chunk)
            await process.wait()
            if process.returncode!=0 or asyncio.get_running_loop().time()>=deadline:raise ValueError
            return timeline.finish()
    except (OSError,ValueError,TimeoutError):raise PlatformError(422,'invalid_media_artifact') from None
    finally:
        if process is not None:await _reap(process)


def _mp4_references(raw):
    count=0
    containers={b'moov',b'trak',b'mdia',b'minf',b'dinf',b'edts',b'udta',b'meta',b'mvex',b'moof',b'traf'}
    def walk(start,end,depth):
        nonlocal count
        if depth>12:raise ValueError
        offset=start
        while offset<end:
            count+=1
            if count>8192 or offset+8>end:raise ValueError
            size,kind=struct.unpack_from('>I4s',raw,offset);header=8
            if size==1:
                if offset+16>end:raise ValueError
                size=struct.unpack_from('>Q',raw,offset+8)[0];header=16
            if size==0:size=end-offset
            if size<header or offset+size>end:raise ValueError
            body=offset+header;limit=offset+size
            if kind in {b'rmra',b'rmda',b'rdrf'}:raise PlatformError(422,'media_external_reference_forbidden')
            if kind==b'dref':
                if limit-body<8 or struct.unpack_from('>I',raw,body+4)[0]!=1:raise PlatformError(422,'media_external_reference_forbidden')
                entry=body+8
                if entry+12>limit:raise ValueError
                esize,etype=struct.unpack_from('>I4s',raw,entry)
                if (etype!=b'url ' or esize<12 or entry+esize!=limit or raw[entry+8:entry+12]!=b'\x00\x00\x00\x01'
                    or raw[entry+12:limit] not in (b'',b'\x00')):
                    raise PlatformError(422,'media_external_reference_forbidden')
            elif kind in containers:walk(body+(4 if kind==b'meta' else 0),limit,depth+1)
            offset=limit
    walk(0,len(raw),0)


def _mp4_declared_durations(raw):
    """Read original track declarations before the demuxer clamps them.

    Only a single ordinary-speed, nonempty edit is supported. More complicated
    timelines cannot establish decoded audio length with this profile.
    """
    count=0
    def boxes(start,end):
        nonlocal count
        result=[]
        while start<end:
            count+=1
            if count>8192 or start+8>end:raise ValueError
            size,kind=struct.unpack_from('>I4s',raw,start);header=8
            if size==1:
                if start+16>end:raise ValueError
                size=struct.unpack_from('>Q',raw,start+8)[0];header=16
            if size==0:size=end-start
            if size<header or start+size>end:raise ValueError
            result.append((kind,start+header,start+size));start+=size
        return result
    def only(items,kind):
        matches=[(a,b)for name,a,b in items if name==kind]
        if len(matches)!=1:raise ValueError
        return matches[0]
    def timing(a,b):
        if b-a<4 or raw[a] not in (0,1) or raw[a+1:a+4]!=b'\x00'*3:raise ValueError
        offset=12 if raw[a]==0 else 20;size=4 if raw[a]==0 else 8
        if a+offset+4+size>b:raise ValueError
        scale=struct.unpack_from('>I',raw,a+offset)[0]
        duration=struct.unpack_from('>I' if size==4 else '>Q',raw,a+offset+4)[0]
        if scale==0 or duration==(1<<(size*8))-1:raise ValueError
        return scale,duration
    moov=boxes(*only(boxes(0,len(raw)),b'moov'))
    movie_scale,_=timing(*only(moov,b'mvhd'));result={}
    for name,a,b in moov:
        if name!=b'trak':continue
        track=boxes(a,b);mdia=boxes(*only(track,b'mdia'))
        hdlr,end=only(mdia,b'hdlr')
        if end-hdlr<12:raise ValueError
        handler=raw[hdlr+8:hdlr+12]
        kind={b'vide':'video',b'soun':'audio'}.get(handler)
        if kind is None or kind in result:raise ValueError
        scale,duration=timing(*only(mdia,b'mdhd'))
        durations=[Fraction(duration,scale)]
        edits=[(x,y)for name,x,y in track if name==b'edts']
        if len(edits)>1:raise ValueError
        if edits:
            start,end=only(boxes(*edits[0]),b'elst')
            if end-start<8 or raw[start] not in (0,1) or raw[start+1:start+4]!=b'\x00'*3:raise ValueError
            version=raw[start];entry=12 if version==0 else 20
            if struct.unpack_from('>I',raw,start+4)[0]!=1 or end-start!=8+entry:raise ValueError
            length,media_time,rate=struct.unpack_from('>IiI' if version==0 else '>QqI',raw,start+8)
            if media_time<0 or media_time>=duration or rate!=65536:raise ValueError
            durations=[Fraction(duration-media_time,scale),Fraction(length,movie_scale)]
        if any(value<=0 for value in durations):raise ValueError
        result[kind]=durations
    if not result:raise ValueError
    return result


def _format(raw,kind):
    if len(raw)>=16 and raw[4:8]==b'ftyp':
        brand=raw[8:12]
        if brand not in {b'isom',b'iso2',b'iso3',b'iso4',b'iso5',b'iso6',b'mp41',b'mp42',b'avc1',b'M4V ',b'M4A ',b'dash'}:raise ValueError
        _mp4_references(raw)
        return 'mov','video/mp4' if kind=='video' else 'audio/mp4','MP4'
    if kind=='audio':
        if raw[:4]==b'RIFF' and raw[8:12]==b'WAVE':
            if len(raw)<12 or struct.unpack_from('<I',raw,4)[0]+8!=len(raw):raise ValueError
            return 'wav','audio/wav','WAV'
        if raw[:4]==b'OggS':return 'ogg','audio/ogg','OGG'
        if raw[:3]==b'ID3' or len(raw)>1 and raw[0]==255 and raw[1]&224==224:return 'mp3','audio/mpeg','MP3'
    raise ValueError


def _positive_int(value,maximum):
    if type(value) is not int or not 1<=value<=maximum:raise ValueError
    return value


def _duration(value,maximum):
    if not isinstance(value,str):return None
    try:
        ceiling=seconds_to_milliseconds(value,rounding='ceil')
        if not 0<ceiling<=maximum:raise ValueError
        return seconds_to_milliseconds(value,rounding='floor')
    except PlatformError:raise ValueError from None


async def _decode(ffmpeg,path,demuxer,stream,*,maximum,sample_rate=None,channels=None,dimensions=None,stream_time_base=None,audit=None,deadline=None):
    args=['-nostdin','-hide_banner','-v','error','-xerror','-err_detect','explode','-max_error_rate','0',
          '-threads','1','-filter_threads','1','-filter_complex_threads','1','-reinit_filter','0',
          '-protocol_whitelist','file,pipe','-format_whitelist','mov,mp3,wav,ogg','-f',demuxer]
    if demuxer=='mov':args+=['-enable_drefs','0','-use_absolute_path','0']
    if stream=='0:v:0':args+=['-noautorotate']
    args+=['-i',str(path),'-map',stream,'-sn','-dn','-threads','1']
    if stream=='0:a:0':
        timeline=DecodedTimeline(kind='audio',maximum_ms=maximum,sample_rate=sample_rate,channels=channels,
                                 expected_samples=audit['samples'])
        args+=['-c:a','pcm_s16le']
    elif stream=='0:v:0':
        timeline=DecodedTimeline(kind='video',maximum_ms=maximum,dimensions=dimensions,time_base=stream_time_base,
                                 expected_frames=audit['frames'])
        args+=['-c:v','rawvideo','-fps_mode','passthrough','-enc_time_base','demux']
    else:raise ValueError
    args+=['-f','framehash','-hash','sha256','pipe:1']
    return await _execute_stream(ffmpeg,args,timeline,deadline=deadline)


@physical_guard
async def inspect_av(raw,*,kind,mime=None,max_bytes,max_duration_ms,max_pixels=33554432,
                     require_audio=False,max_channels=2,max_sample_rate=48000,tools):
    try:
        if not isinstance(raw,bytes) or kind not in {'audio','video'} or type(require_audio) is not bool:raise ValueError
        _positive_int(max_bytes,67108864);_positive_int(max_duration_ms,900000)
        _positive_int(max_pixels,33554432);_positive_int(max_channels,8);_positive_int(max_sample_rate,192000)
        if not 0<len(raw)<=max_bytes:raise ValueError
        demuxer,actual_mime,fmt=_format(raw,kind)
        original_durations=_mp4_declared_durations(raw) if demuxer=='mov' else {}
        if mime not in (None,actual_mime):raise ValueError
        if not isinstance(tools,AVTools):raise PlatformError(503,'media_decoder_unavailable')
        ffmpeg,ffprobe=tools.paths()
        for executable in (ffmpeg,ffprobe):
            version=await _execute(executable,['-version'],timeout=5,maximum=8192)
            if not re.match(rb'ff(?:mpeg|probe) version '+re.escape(tools.version.encode())+rb'(?:\s|$)',version):
                raise PlatformError(503,'media_decoder_unavailable')
        with tempfile.TemporaryDirectory(prefix='easel-av-') as directory:
            folder=Path(directory);folder.chmod(0o700);path=folder/'owned-input.bin'
            descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(descriptor,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
            args=['-v','error','-threads','1','-protocol_whitelist','file,pipe','-format_whitelist','mov,mp3,wav,ogg','-f',demuxer]
            if demuxer=='mov':args+=['-enable_drefs','0','-use_absolute_path','0']
            args+=['-show_entries','stream=index,codec_type,codec_name,width,height,channels,sample_rate,duration,duration_ts,time_base,nb_frames:format=format_name,duration,size','-of','json',str(path)]
            value=json.loads(await _execute(ffprobe,args,timeout=10,maximum=32768))
            streams=value.get('streams')
            if not isinstance(streams,list) or not 1<=len(streams)<=2:raise ValueError
            video=[s for s in streams if s.get('codec_type')=='video'];audio=[s for s in streams if s.get('codec_type')=='audio']
            if len(video)+len(audio)!=len(streams) or len(video)>1 or len(audio)>1:raise ValueError
            if original_durations and set(original_durations)!={s['codec_type'] for s in streams}:raise ValueError
            if kind=='video' and len(video)!=1 or kind=='audio' and (video or len(audio)!=1) or require_audio and not audio:raise ValueError
            properties={'format':fmt}
            for stream in streams:
                _duration(stream.get('duration'),max_duration_ms)
            _duration(value.get('format',{}).get('duration'),max_duration_ms)
            if video:
                width=_positive_int(video[0].get('width'),16384);height=_positive_int(video[0].get('height'),16384)
                if width*height>max_pixels:raise ValueError
                properties.update(width=width,height=height)
            if audio:
                channels=_positive_int(audio[0].get('channels'),max_channels)
                sample=audio[0].get('sample_rate')
                if not isinstance(sample,str) or not sample.isdecimal():raise ValueError
                sample=_positive_int(int(sample),max_sample_rate)
                if sample<8000:raise ValueError
                properties.update(channels=channels,sample_rate=sample)
            # One native decode covers both original stream indices. Output
            # conversion must never conceal native geometry or audio changes.
            # Each child retains CPU30; AV uses three full-decode children
            # (nominal CPU limits sum to 90), all sharing this wall deadline.
            deadline=asyncio.get_running_loop().time()+_DECODE_WALL_SECONDS
            audit_parser=NativeFrameAudit(streams=streams,maximum_ms=max_duration_ms,
                                          max_pixels=max_pixels,max_channels=max_channels)
            native_args=args[:args.index('-show_entries')]+['-show_frames','-show_entries',
                'frame=media_type,stream_index,width,height,pix_fmt,sample_fmt,channels,channel_layout,nb_samples:frame_side_data=',
                '-of','compact=p=1:nk=0',str(path)]
            audited=await _execute_stream(ffprobe,native_args,audit_parser,deadline=deadline)
            if video:
                properties['duration_ms']=await _decode(ffmpeg,path,demuxer,'0:v:0',maximum=max_duration_ms,
                    dimensions=(width,height),stream_time_base=time_base(video[0].get('time_base')),
                    audit=audited[video[0]['index']],deadline=deadline)
            if audio:
                duration=await _decode(ffmpeg,path,demuxer,'0:a:0',maximum=max_duration_ms,sample_rate=sample,channels=channels,
                    audit=audited[audio[0]['index']],deadline=deadline)
                properties['audio_duration_ms' if video else 'duration_ms']=duration
            # Each selected stream must agree with its own full decode. The
            # video timeline cannot conceal an audio track missing tail samples.
            for selected,measured in [(video[0],properties['duration_ms'])] if video else []:
                declared=_duration(selected.get('duration') or value.get('format',{}).get('duration'),max_duration_ms)
                if declared is not None and abs(declared-measured)>100:raise ValueError
            if audio:
                measured=properties['audio_duration_ms' if video else 'duration_ms']
                declared=_duration(audio[0].get('duration') or (None if video else value.get('format',{}).get('duration')),max_duration_ms)
                if declared is not None and abs(declared-measured)>100:raise ValueError
            # STTS and edit-list processing can shorten ffprobe's duration.
            # Retain the original per-track declarations as an independent gate.
            for track,durations in original_durations.items():
                measured=properties['audio_duration_ms' if track=='audio' and video else 'duration_ms']
                if any(value*1000>max_duration_ms or abs(value*1000-measured)>100 for value in durations):raise ValueError
            if any(properties[key]<=0 for key in ('duration_ms',)):raise ValueError
            return {'sha256':hashlib.sha256(raw).hexdigest(),'size':len(raw),'mime':actual_mime,'properties':properties}
    except (OSError,ValueError,TypeError,KeyError,UnicodeError,RecursionError):
        raise PlatformError(422,'invalid_media_artifact') from None
