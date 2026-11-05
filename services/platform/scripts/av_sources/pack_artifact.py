"""Strict complete-manifest handoff, without native execution or host adoption.

The host supplies its pinned, prepared worker validate_report as `validator`.
A successful parse returns content and transport facts only. Tree bytes are
reconstructed independently; absent commit/blob contents are not reverified.
Raw multiplexed logs must not be persisted by callers.
"""
from dataclasses import dataclass, fields
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import unicodedata


class ArtifactError(ValueError):
    """Fixed safe artifact rejection; no input paths or raw errors."""


def _need(ok):
    if not ok: raise ArtifactError('artifact contract rejected')


@dataclass(frozen=True)
class ArtifactLimits:
    max_manifest_bytes: int = 16777216
    max_frames: int = 4096
    max_depth: int = 4
    max_objects: int = 30002
    max_arrays: int = 2
    max_commas: int = 180010
    max_colons: int = 180010
    max_string_bytes: int = 24576
    max_number_chars: int = 20
    max_members: int = 30000
    max_path_bytes: int = 4096
    max_path_depth: int = 64
    max_tree_bytes: int = 4194304
    max_blob_bytes: int = 16777216
    max_content_bytes: int = 536870912
    max_loaded_bytes: int = 134217728

    def __post_init__(self):
        for field in fields(self):
            value=getattr(self,field.name)
            _need(type(value) is int and 0 < value <= field.default)


@dataclass(frozen=True)
class ManifestArtifact:
    """canonical_bytes stays immutable even if caller edits decoded content.

    transport has stream/frames/payload_bytes/mux_bytes/injected_for_tests;
    none of these fields is a container, provenance or adoption assertion.
    """
    manifest: dict
    canonical_bytes: bytes
    transport: dict


def _hex(value,n):
    return type(value) is str and re.fullmatch('[0-9a-f]{'+str(n)+'}',value) is not None


def _oid(value): return _hex(value,40) and value!='0'*40


def _canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')


def _prescan(raw,lim):
    # Counts and token bounds precede json.loads, bounding graph amplification.
    _need(raw.isascii())
    stack=[];counts={123:0,91:0,44:0,58:0};caps={123:lim.max_objects,91:lim.max_arrays,44:lim.max_commas,58:lim.max_colons}
    i=0
    while i<len(raw):
        c=raw[i]
        if c==34:
            start=i+1;i+=1
            while i<len(raw):
                if raw[i]==34: break
                if raw[i]==92: i+=1
                i+=1
                _need(i-start<=lim.max_string_bytes)
            _need(i<len(raw) and i-start<=lim.max_string_bytes)
        elif c in counts:
            counts[c]+=1;_need(counts[c]<=caps[c])
            if c in (123,91): stack.append(c);_need(len(stack)<=lim.max_depth)
        elif c in (125,93):
            _need(bool(stack) and stack.pop()==(123 if c==125 else 91))
        elif c==45 or 48<=c<=57:
            start=i;i+=1
            while i<len(raw) and raw[i] in b'0123456789.eE+-': i+=1
            _need(i-start<=lim.max_number_chars);i-=1
        i+=1
    _need(not stack)


def _json(raw,lim):
    _prescan(raw,lim)
    def pairs(items):
        result={}
        for k,v in items:
            _need(k not in result);result[k]=v
        return result
    def reject(_): raise ArtifactError('artifact contract rejected')
    obj=json.loads(raw,object_pairs_hook=pairs,parse_constant=reject)
    _need(type(obj) is dict and _canonical(obj)==raw)
    return obj


def _name(name):
    _need(bool(name) and name not in ('.','..') and not any(c in name for c in '/\\:')
          and not any(unicodedata.category(c).startswith('C') for c in name)
          and not name.endswith((' ','.')))
    folded=unicodedata.normalize('NFC',name).casefold();stem=folded.split('.')[0]
    _need(folded not in ('.git','git~1') and stem not in {'con','prn','aux','nul'}
          and re.fullmatch(r'(com|lpt)[1-9]',stem) is None)


def _manifest(m,p,r,lim):
    mode=p['mode']
    if mode!='decode':
        expected={'format':'av-pack-preflight-v1','nonce':p['nonce']} if mode=='preflight' else {'format':'av-pack-control-v1','mode':mode,'nonce':p['nonce']}
        _need(m==expected);return
    _need(set(m)=={'commit_oid','commit_sha256','tree_oid','tree_sha256','entries','gitlinks','counts'})
    _need(_oid(m['commit_oid']) and m['commit_oid']==p['pack']['commit_oid']
          and _oid(m['tree_oid']) and m['tree_oid']==r['tree_oid'])
    _need(all(_hex(m[k],64) and m[k]!='0'*64 for k in ('commit_sha256','tree_sha256')))
    counts=m['counts']
    _need(type(counts) is dict and set(counts)=={'members','content_bytes','loaded_bytes'}
          and all(type(v) is int and v>=0 for v in counts.values()) and counts==r['counts'])
    entries=m['entries'];links=m['gitlinks']
    _need(type(entries) is list and type(links) is list and len(entries)+len(links)<=lim.max_members)
    bypath={};normalized=set();children={'':[]};content=0;trees=0
    raw_facts={}
    def raw_object(oid,kind,size,sha256):
        # An object ID describes one raw object regardless of path/mode. At most
        # max_members entry identities plus the root tree and commit are stored.
        # External gitlinks deliberately supply no loaded raw-object assertion.
        facts=(kind,size,sha256)
        if oid in raw_facts: _need(raw_facts[oid]==facts)
        else:
            _need(len(raw_facts)<lim.max_members+2)
            raw_facts[oid]=facts
    for group,islink in ((entries,False),(links,True)):
        previous=None
        for e in group:
            _need(type(e) is dict and set(e)==({'path','mode','oid'} if islink else {'path','mode','kind','oid','size','sha256'}))
            path=e['path'];_need(type(path) is str)
            encoded=path.encode('utf-8');parts=path.split('/')
            _need(0<len(encoded)<=lim.max_path_bytes and len(parts)<=lim.max_path_depth)
            for name in parts: _name(name)
            _need(previous is None or previous<encoded);previous=encoded
            normal=unicodedata.normalize('NFC',path).casefold()
            _need(normal not in normalized);normalized.add(normal)
            _need(_oid(e['oid']) and type(e['mode']) is str)
            if islink: _need(e['mode']=='160000')
            else:
                kinds={'40000':'tree','100644':'blob','100755':'blob','120000':'symlink'}
                _need(e['mode'] in kinds and e['kind']==kinds[e['mode']] and type(e['size']) is int
                      and 0<=e['size']<=(lim.max_tree_bytes if e['mode']=='40000' else lim.max_blob_bytes)
                      and _hex(e['sha256'],64) and e['sha256']!='0'*64)
                raw_object(e['oid'],'tree' if e['mode']=='40000' else 'blob',e['size'],e['sha256'])
                if e['mode']=='40000': children[path]=[];trees+=e['size']
                else: content+=e['size']
            bypath[path]=e
    for path,e in bypath.items():
        parent,_,name=path.rpartition('/')
        _need(parent in children)
        children[parent].append((name,e))
    root_size=0
    for directory,members in children.items():
        members.sort(key=lambda row:row[0].encode('utf-8')+(b'/' if row[1]['mode']=='40000' else b'\0'))
        size=sum(len(e['mode'])+1+len(name.encode('utf-8'))+1+20 for name,e in members)
        _need(size<=lim.max_tree_bytes)
        raw=b''.join(e['mode'].encode('ascii')+b' '+name.encode('utf-8')+b'\0'+bytes.fromhex(e['oid']) for name,e in members)
        sha1=hashlib.sha1(b'tree '+str(size).encode('ascii')+b'\0'+raw).hexdigest()
        sha256=hashlib.sha256(raw).hexdigest()
        raw_object(sha1,'tree',size,sha256)
        if directory:
            e=bypath[directory];_need((e['size'],e['oid'],e['sha256'])==(size,sha1,sha256))
        else: root_size=size;_need((m['tree_oid'],m['tree_sha256'])==(sha1,sha256))
    _need(counts['members']==len(bypath) and counts['content_bytes']==content<=lim.max_content_bytes)
    commit_size=counts['loaded_bytes']-content-trees-root_size
    _need(0<commit_size<=lim.max_tree_bytes and counts['loaded_bytes']<=lim.max_loaded_bytes)
    raw_object(m['commit_oid'],'commit',commit_size,m['commit_sha256'])
    required=p['pack']['required_entry']
    if required is not None:
        entry=bypath.get(required['path'])
        _need(entry is not None and entry==r['matched_entry'] and entry.get('kind')=='blob'
              and entry['oid']==required['oid'] and entry['size']==required['bytes'])
    else: _need(r['matched_entry'] is None)
    wanted=p['pack']['required_gitlink_oid']
    _need([e for e in links if e['oid']==wanted]==r['matched_gitlinks'])


def decode_manifest_logs(raw: bytes, *, profile, report, _allow_injected=False,
                         limits=ArtifactLimits(), validator=None):
    """Return ManifestArtifact after strict stream2, report and full tree checks.

    validator must be the host's pinned prepared worker.validate_report. The
    host owns ensuring that callable's code identity; there is no ambient import.
    HTTP wire counters, sameCID/terminal/deadline fences remain the host's job.
    """
    try:
        _need(type(limits) is ArtifactLimits and type(raw) is bytes and callable(validator))
        _need(type(_allow_injected) is bool and type(report) is dict
              and type(report.get('injected_for_tests')) is bool
              and (not report['injected_for_tests'] or _allow_injected is True))
        validator(report,profile,_allow_injected=_allow_injected)
        meta=report['manifest'];_need(meta['bytes']<=limits.max_manifest_bytes)
        _need(len(raw)<=limits.max_manifest_bytes+1+limits.max_frames*8)
        parts=[];offset=0;payload=0;frames=0
        while offset<len(raw):
            _need(len(raw)-offset>=8)
            header=raw[offset:offset+8];offset+=8;frames+=1
            _need(frames<=limits.max_frames and header[:4]==b'\x02\0\0\0')
            size=int.from_bytes(header[4:],'big')
            _need(size<=len(raw)-offset and payload+size<=limits.max_manifest_bytes+1
                  and payload+size<=meta['bytes']+1)
            parts.append(raw[offset:offset+size]);offset+=size;payload+=size
        _need(frames>0 and payload==meta['bytes']+1)
        joined=b''.join(parts);_need(joined[-1:]==b'\n');content=joined[:-1]
        _need(hashlib.sha256(content).hexdigest()==meta['sha256'])
        manifest=_json(content,limits);_manifest(manifest,profile,report,limits)
        return ManifestArtifact(manifest,content,dict(stream=2,frames=frames,payload_bytes=payload,mux_bytes=len(raw),injected_for_tests=report['injected_for_tests']))
    except Exception:
        raise ArtifactError('artifact contract rejected') from None


def _identity(s):
    return (s.st_dev,s.st_ino,s.st_mode,s.st_size,s.st_uid,s.st_gid,s.st_nlink,s.st_mtime_ns,s.st_ctime_ns)


def _stable(s): return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink)


def _parent(path):
    text=os.fspath(path)
    _need(type(text) is str and text.startswith('/') and '\0' not in text
          and all(x not in ('','.','..') for x in text.split('/')[1:]))
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for part in text.split('/')[1:]:
            nxt=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=nxt
        s=os.fstat(fd);_need(stat.S_IMODE(s.st_mode)==0o700 and s.st_uid==os.geteuid())
        return fd
    except BaseException: os.close(fd);raise


def store_manifest(raw: bytes, *, expected_size: int, expected_sha256: str, destination: Path):
    """Exclusive private fixed-name write. A DTO is not final job adoption.

    Failure may leave an exclusive partial file quarantined; never overwrites or
    returns a success path/marker. The caller brackets this with host fences.
    """
    parent=None;fd=None
    try:
        _need(type(raw) is bytes and type(expected_size) is int and 0<expected_size<=16777216
              and len(raw)==expected_size and _hex(expected_sha256,64)
              and hashlib.sha256(raw).hexdigest()==expected_sha256)
        destination=Path(destination)
        _need(destination.is_absolute() and destination.name=='manifest.json')
        parent=_parent(destination.parent);parent_before=os.fstat(parent)
        fd=os.open('manifest.json',os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=parent)
        before=os.fstat(fd)
        _need(stat.S_ISREG(before.st_mode) and stat.S_IMODE(before.st_mode)==0o600
              and before.st_uid==os.geteuid() and before.st_nlink==1)
        for start in range(0,len(raw),65536):
            view=memoryview(raw)[start:start+65536]
            while view:
                n=os.write(fd,view);_need(type(n) is int and 0<n<=len(view));view=view[n:]
        os.fsync(fd);os.fsync(parent)
        after=os.fstat(fd);_need(_stable(before)==_stable(after) and after.st_size==len(raw))
        os.lseek(fd,0,os.SEEK_SET);digest=hashlib.sha256();total=0
        while True:
            chunk=os.read(fd,min(65536,len(raw)+1-total))
            if not chunk: break
            total+=len(chunk);_need(total<=len(raw));digest.update(chunk)
        _need(total==len(raw) and digest.hexdigest()==expected_sha256 and _identity(os.fstat(fd))==_identity(after)
              and _identity(os.stat('manifest.json',dir_fd=parent,follow_symlinks=False))==_identity(after))
        check=_parent(destination.parent)
        try: _need(_stable(os.fstat(check))[:5]==_stable(parent_before)[:5]==_stable(os.fstat(parent))[:5])
        finally: os.close(check)
        return {'bytes':total,'sha256':expected_sha256,'identity':_identity(after)}
    except (OSError,ValueError,TypeError,OverflowError):
        raise ArtifactError('private artifact write rejected') from None
    finally:
        close_failed=False
        for owned in (fd,parent):
            if owned is not None:
                try: os.close(owned)
                except BaseException: close_failed=True
        if close_failed: raise ArtifactError('private artifact write rejected') from None
