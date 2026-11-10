"""Bounded original media bytes, workspace freezing, and no-replace publication."""
from hashlib import sha256
import io
import json
import os
import stat
import uuid
import warnings
import wave
from .media_contracts import MIMES, MAX_MEDIA, integer

SUFFIXES={'image/png':'.png','image/jpeg':'.jpg','image/webp':'.webp','video/mp4':'.mp4','audio/mpeg':'.mp3','audio/wav':'.wav','audio/ogg':'.ogg','audio/mp4':'.m4a','application/json':'.json'}


def properties(mime,value):
    if not isinstance(value,dict):raise ValueError
    if mime=='application/json':
        if set(value)!={'unicode_scalar'}:raise ValueError
        integer(value['unicode_scalar'],1048576);return dict(value)
    if mime not in MIMES or value.get('format')!=MIMES[mime]:raise ValueError
    if mime.startswith('image/'):
        if set(value)!={'width','height','format'}:raise ValueError
        for key in ('width','height'):integer(value[key],16384,minimum=1)
        if value['width']*value['height']>33554432:raise ValueError
    else:
        required={'format','duration_ms'}|({'width','height'} if mime.startswith('video/') else {'channels','sample_rate'})
        if not required.issubset(value) or set(value)-{'format','duration_ms','audio_duration_ms','channels','sample_rate','width','height'}:raise ValueError
        integer(value['duration_ms'],900000,minimum=1)
        for key,maximum in [('audio_duration_ms',900000),('width',16384),('height',16384),('channels',8),('sample_rate',192000)]:
            if key in value:integer(value[key],maximum,minimum=8000 if key=='sample_rate' else 1)
        if ('channels' in value)!=('sample_rate' in value):raise ValueError
        if 'width' in value and value['width']*value['height']>33554432:raise ValueError
    return dict(value)


def sniff(raw):
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):return 'image/png'
    if raw.startswith(b'\xff\xd8\xff'):return 'image/jpeg'
    if raw[:4]==b'RIFF' and raw[8:12]==b'WEBP':return 'image/webp'
    if raw[:4]==b'RIFF' and raw[8:12]==b'WAVE':return 'audio/wav'
    if raw.startswith(b'OggS'):return 'audio/ogg'
    if raw.startswith(b'ID3') or len(raw)>2 and raw[0]==255 and raw[1]&0xe0==0xe0:return 'audio/mpeg'
    if len(raw)>=16 and raw[4:8]==b'ftyp':return 'video/mp4'
    raise ValueError('Unsupported media bytes')


def verify(raw,metadata):
    mime=metadata['mime'];props=properties(mime,metadata['properties'])
    if not 0<len(raw)<=MAX_MEDIA or len(raw)!=metadata['size'] or sha256(raw).hexdigest()!=metadata['sha256']:raise ValueError
    if mime=='application/json':
        def unique(pairs):
            result={}
            for key,value in pairs:
                if key in result:raise ValueError
                result[key]=value
            return result
        value=json.loads(raw.decode('utf-8'),object_pairs_hook=unique,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
        if not isinstance(value,dict) or set(value)!={'text'} or not isinstance(value['text'],str) or len(value['text'])!=props['unicode_scalar']:raise ValueError
        value['text'].encode('utf-8');return
    actual=sniff(raw)
    if actual!=mime and not (actual=='video/mp4' and mime=='audio/mp4'):raise ValueError
    if mime.startswith('image/'):
        from PIL import Image
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error',Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as image:
                    if image.format!=props['format'] or image.size!=(props['width'],props['height']) or getattr(image,'n_frames',1)!=1:raise ValueError
                    image.verify()
                with Image.open(io.BytesIO(raw)) as image:image.load()
        except (Image.DecompressionBombError,Image.DecompressionBombWarning,SyntaxError):raise ValueError from None
    elif mime=='audio/wav':
        try:
            if int.from_bytes(raw[4:8],'little')+8!=len(raw):raise ValueError
            with wave.open(io.BytesIO(raw),'rb') as wav:
                if wav.getnchannels()!=props['channels'] or wav.getframerate()!=props['sample_rate']:raise ValueError
                frames=wav.getnframes();decoded=wav.readframes(frames)
                if len(decoded)!=frames*wav.getnchannels()*wav.getsampwidth() or abs((frames*1000+wav.getframerate()-1)//wav.getframerate()-props['duration_ms'])>1:raise ValueError
        except (wave.Error,EOFError):raise ValueError from None
    elif mime in {'video/mp4','audio/mp4'}:
        # Match the original central-verified ISO BMFF container, without transcode.
        offset=0;boxes=set()
        while offset<len(raw):
            if offset+8>len(raw):raise ValueError
            size=int.from_bytes(raw[offset:offset+4],'big');kind=raw[offset+4:offset+8];header=8
            if size==1:
                if offset+16>len(raw):raise ValueError
                size=int.from_bytes(raw[offset+8:offset+16],'big');header=16
            if size==0:size=len(raw)-offset
            if size<header or offset+size>len(raw):raise ValueError
            boxes.add(kind);offset+=size
        if not {b'ftyp',b'moov',b'mdat'}.issubset(boxes):raise ValueError
    elif mime=='audio/ogg':
        if len(raw)<27 or raw[4]!=0:raise ValueError
    elif mime=='audio/mpeg':
        if len(raw)<16:raise ValueError


def freeze(root,path,*,maximum,allowed):
    from easel.harnesses.pi_tools import _parts,_directory
    parts=_parts(path)
    if not parts or any(part.lower() in {"runtime", "account", "accounts"} for part in parts):raise ValueError
    with _directory(root,parts[:-1]) as directory:
        fd=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
        with os.fdopen(fd,'rb') as stream:
            before=os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or not 0<before.st_size<=min(maximum,MAX_MEDIA):raise ValueError
            raw=stream.read(min(maximum,MAX_MEDIA)+1);after=os.fstat(stream.fileno())
            current=os.stat(parts[-1],dir_fd=directory,follow_symlinks=False)
            keys=('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')
            if any(getattr(before,k)!=getattr(after,k) or getattr(before,k)!=getattr(current,k) for k in keys) or len(raw)!=before.st_size:raise ValueError
    mime=sniff(raw)
    if mime=='video/mp4' and parts[-1].lower().endswith('.m4a') and 'audio/mp4' in allowed:mime='audio/mp4'
    if mime not in allowed:raise ValueError
    # Input bytes stay frozen in memory until central inspection has confirmed them.
    return {'raw':raw,'size':len(raw),'sha256':sha256(raw).hexdigest(),'mime':mime}


def save(root,path,raw,metadata,*,publish_guard):
    from easel.harnesses.pi_tools import _parts,_directory
    parts=_parts(path)
    if len(parts)<2 or parts[0]!='outputs' or not parts[-1].lower().endswith(SUFFIXES[metadata['mime']]):raise ValueError
    verify(raw,metadata)
    with _directory(root,parts[:-1],create=True) as directory:
        temporary='.easel-media-'+str(uuid.uuid4())+'.part'
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
        try:
            with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
            with publish_guard():
                os.link(temporary,parts[-1],src_dir_fd=directory,dst_dir_fd=directory,follow_symlinks=False);os.fsync(directory)
        finally:os.unlink(temporary,dir_fd=directory)
    return '/'.join(parts)
