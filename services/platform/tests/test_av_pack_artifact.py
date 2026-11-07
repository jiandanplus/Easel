"""Pure synthetic producer/consumer and private artifact tests."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]/'scripts'/'av_sources'


def artifact():
    path=ROOT/'pack_artifact.py'
    assert path.exists(), 'complete manifest consumer missing'
    spec=importlib.util.spec_from_file_location('artifact_test',path)
    mod=importlib.util.module_from_spec(spec);sys.modules[spec.name]=mod;spec.loader.exec_module(mod)
    return mod


def test_consumer_available():
    assert callable(artifact().decode_manifest_logs)


def helpers():
    spec=importlib.util.spec_from_file_location('artifact_worker_fixtures', Path(__file__).with_name('test_av_pack_worker.py'))
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def fixture(tmp_path,mode='decode'):
    h=helpers();w,p,code,inputs,work=h.stage(tmp_path)
    p['mode']=mode
    if mode!='decode': p['pack']=None
    if mode.startswith('control-'):
        r=w._base_report(p,w.synthetic_runtime(p),True)
        r['manifest']=w.write_manifest(work,{'format':'av-pack-control-v1','mode':mode,'nonce':p['nonce']})
    else:
        r=w.execute_decode(p,code_dir=code,inputs_dir=inputs,work_dir=work,_runtime=w.synthetic_runtime(p))
    return artifact(),w,p,r,(work/'manifest.json').read_bytes()


def mux(payload): return b'\x02\0\0\0'+struct.pack('>I',len(payload))+payload


def consume(a,w,p,r,raw,**kw):
    validator=kw.pop('validator_override',w.validate_report)
    return a.decode_manifest_logs(raw,profile=p,report=r,_allow_injected=True,validator=validator,**kw)


def repin(w,r,raw):
    r=copy.deepcopy(r);r['manifest']['bytes']=len(raw);r['manifest']['sha256']=hashlib.sha256(raw).hexdigest();return r


@pytest.mark.parametrize('mode',['decode','preflight','control-as','control-cpu','control-wall'])
def test_producer_consumer_writer(tmp_path,mode):
    a,w,p,r,raw=fixture(tmp_path,mode)
    class Sink:
        def __init__(self): self.data=bytearray()
        def write(self,fd,data): assert fd==2;self.data.extend(data[:7]);return min(7,len(data))
    sink=Sink();w.publish_manifest(sink,p,r,work_dir=tmp_path/'work')
    result=consume(a,w,p,r,mux(bytes(sink.data)))
    assert result.manifest==json.loads(raw) and result.canonical_bytes==raw
    assert result.transport=={'stream':2,'frames':1,'payload_bytes':len(raw)+1,'mux_bytes':len(raw)+9,'injected_for_tests':True}
    with pytest.raises(a.ArtifactError): a.decode_manifest_logs(mux(raw+b'\n'),profile=p,report=r,validator=w.validate_report)
    target=tmp_path/'destination';target.mkdir(mode=0o700)
    dto=a.store_manifest(result.canonical_bytes,expected_size=len(raw),expected_sha256=hashlib.sha256(raw).hexdigest(),destination=target/'manifest.json')
    assert (target/'manifest.json').read_bytes()==raw and dto['bytes']==len(raw)
    assert set(dto)=={'bytes','sha256','identity'} and 'path' not in dto
    result.manifest.clear();assert result.canonical_bytes==raw


@pytest.mark.parametrize('fault',['stream0','stream1','stream3','reserved','header','payload','tail','noLF','extraLF','hash','empty','length'])
def test_mux_and_pin_rejections(tmp_path,fault):
    a,w,p,r,raw=fixture(tmp_path);data=mux(raw+b'\n')
    if fault.startswith('stream'): data=bytes([int(fault[-1])])+data[1:]
    if fault=='reserved': data=data[:1]+b'X'+data[2:]
    if fault=='header': data=data[:7]
    if fault=='payload': data=data[:-1]
    if fault=='tail': data+=b'X'
    if fault=='noLF': data=mux(raw)
    if fault=='extraLF': data=mux(raw+b'\n\n')
    if fault=='hash': data=mux(raw[:-1]+b' '+b'\n')
    if fault=='empty': data=b''
    if fault=='length': data=data[:4]+b'\xff'*4+data[8:]
    with pytest.raises(a.ArtifactError): consume(a,w,p,r,data)


@pytest.mark.parametrize('fault',['unknown','countsbool','countmembers','content','loaded','commit','tree','sha','entrysha','entrymode','entrysize','kind','path','reservedname','surrogate','missingentry','gitlinkmode','gitlinkoid','unsorted','duplicate','ancestor','leafancestor','matched','linkmatch'])
def test_semantics_with_recomputed_pin(tmp_path,fault):
    a,w,p,r,raw=fixture(tmp_path);m=json.loads(raw)
    if fault=='unknown': m['unknown']=1
    if fault=='countsbool': m['counts']['members']=True;r['counts']['members']=True
    if fault=='countmembers': m['counts']['members']+=1;r['counts']=m['counts']
    if fault=='content': m['counts']['content_bytes']+=1;r['counts']=m['counts']
    if fault=='loaded': m['counts']['loaded_bytes']=m['counts']['content_bytes'];r['counts']=m['counts']
    if fault=='commit': m['commit_oid']='c'*40
    if fault=='tree': m['tree_sha256']='c'*64
    if fault=='sha': m['commit_sha256']='C'*64
    if fault=='entrysha': m['entries'][0]['sha256']='c'*64
    if fault=='entrymode': m['entries'][0]['mode']='100755'
    if fault=='entrysize': m['entries'][0]['size']=True
    if fault=='kind': m['entries'][0]['kind']='symlink'
    if fault=='path': m['entries'][0]['path']='../x'
    if fault=='reservedname': m['entries'][0]['path']='CON.txt'
    if fault=='surrogate': m['entries'][0]['path']='\ud800'
    if fault=='missingentry': m['entries']=[]
    if fault=='gitlinkmode': m['gitlinks'][0]['mode']='100644'
    if fault=='gitlinkoid': m['gitlinks'][0]['oid']='c'*40
    if fault=='unsorted': m['entries'].append(dict(m['entries'][0],path='a'))
    if fault=='duplicate': m['gitlinks'][0]['path']='FILE.TXT'
    if fault=='ancestor': m['entries'][0]['path']='dir/file.txt'
    if fault=='leafancestor': m['gitlinks'][0]['path']='file.txt/sub'
    if fault=='matched': r['matched_entry']=dict(r['matched_entry'],sha256='c'*64)
    if fault=='linkmatch': r['matched_gitlinks'][0]['path']='wrong'
    raw=w.canonical(m);r=repin(w,r,raw)
    with pytest.raises(a.ArtifactError): consume(a,w,p,r,mux(raw+b'\n'))


@pytest.mark.parametrize('raw',[b'{"x":1,"x":1}',b'{"x":NaN}',b'{"x":Infinity}',b'{} ',b'\xef\xbb\xbf{}',b'{"a":[[[[[]]]]]}',b'{"a":123456789012345678901}',b'{"a":"'+b'x'*24577+b'"}',b'{"x":"\xc3\xa9"}'])
def test_canonical_grammar_rejects_before_adoption(tmp_path,raw):
    a,w,p,r,_=fixture(tmp_path);r=repin(w,r,raw)
    with pytest.raises(a.ArtifactError): consume(a,w,p,r,mux(raw+b'\n'))


@pytest.mark.parametrize('key',['max_manifest_bytes','max_frames','max_depth','max_objects','max_arrays','max_commas','max_colons','max_string_bytes','max_number_chars','max_members','max_path_bytes','max_path_depth','max_tree_bytes','max_blob_bytes','max_content_bytes','max_loaded_bytes'])
def test_limits_cannot_be_raised_or_bool(tmp_path,key):
    a=artifact();base=a.ArtifactLimits()
    for bad in (True,0,-1,getattr(base,key)+1):
        with pytest.raises(a.ArtifactError): a.ArtifactLimits(**{key:bad})


def test_tiny_caps_fragmentation_and_empty_frames(tmp_path):
    a,w,p,r,raw=fixture(tmp_path,'preflight')
    data=mux(b'')+mux(raw[:3])+mux(raw[3:]+b'\n')
    assert consume(a,w,p,r,data,limits=a.ArtifactLimits(max_frames=3,max_manifest_bytes=len(raw))).canonical_bytes==raw
    for limits in [a.ArtifactLimits(max_frames=2),a.ArtifactLimits(max_manifest_bytes=len(raw)-1),a.ArtifactLimits(max_string_bytes=31),a.ArtifactLimits(max_objects=1,max_colons=1)]:
        with pytest.raises(a.ArtifactError): consume(a,w,p,r,data,limits=limits)


@pytest.mark.parametrize('fault',['existing','badname','relative','parentmode','symlinkparent','symlinkfile','hardlink','wrongsize','wronghash','zero','invalid','partial','write','fsync','dirfsync','rehash','identity'])
def test_private_writer_fails_closed(tmp_path,monkeypatch,fault):
    a=artifact();parent=tmp_path/'private';parent.mkdir(mode=0o700);dest=parent/'manifest.json';raw=b'{"synthetic":true}'
    size=len(raw);sha=hashlib.sha256(raw).hexdigest()
    if fault=='existing': dest.write_bytes(b'preserve')
    if fault=='badname': dest=parent/'other.json'
    if fault=='relative': dest=Path('manifest.json')
    if fault=='parentmode': parent.chmod(0o755)
    if fault=='symlinkparent':
        alias=tmp_path/'alias';alias.symlink_to(parent,target_is_directory=True);dest=alias/'manifest.json'
    if fault=='symlinkfile': dest.symlink_to(parent/'absent')
    if fault=='hardlink':
        original=parent/'original';original.write_bytes(b'preserve');os.link(original,dest)
    if fault=='wrongsize': size+=1
    if fault=='wronghash': sha='a'*64
    write=a.os.write;fsync=a.os.fsync;read=a.os.read;fstat=a.os.fstat;calls=[0]
    def badwrite(fd,data):
        calls[0]+=1
        if fault=='zero': return 0
        if fault=='invalid': return len(data)+1
        if fault=='partial' and calls[0]==1: return write(fd,data[:1])
        raise OSError('private')
    def badsync(fd):
        if fault=='fsync' or a.stat.S_ISDIR(fstat(fd).st_mode): raise OSError('private')
        fsync(fd)
    if fault in ('zero','invalid','partial','write'): monkeypatch.setattr(a.os,'write',badwrite)
    if fault in ('fsync','dirfsync'): monkeypatch.setattr(a.os,'fsync',badsync)
    if fault=='rehash': monkeypatch.setattr(a.os,'read',lambda fd,n:b'X' if n else b'')
    if fault=='identity':
        def changed(fd):
            info=fstat(fd)
            if a.stat.S_ISREG(info.st_mode):
                calls[0]+=1
                if calls[0]>1:
                    values=list(info);values[3]=2;return os.stat_result(values)
            return info
        monkeypatch.setattr(a.os,'fstat',changed)
    with pytest.raises(a.ArtifactError): a.store_manifest(raw,expected_size=size,expected_sha256=sha,destination=dest)
    if fault in ('existing','hardlink'): assert dest.read_bytes()==b'preserve'


@pytest.mark.parametrize('validator',[None,False,1,'ambient'])
def test_explicit_validator_required_before_json(tmp_path,monkeypatch,validator):
    a,w,p,r,raw=fixture(tmp_path)
    monkeypatch.setattr(a.json,'loads',lambda *args,**kw:pytest.fail('missing validator reached JSON'))
    with pytest.raises(a.ArtifactError): a.decode_manifest_logs(mux(raw+b'\n'),profile=p,report=r,validator=validator,_allow_injected=True)


def test_validator_rejection_and_independent_injection_guard(tmp_path,monkeypatch):
    a,w,p,r,raw=fixture(tmp_path)
    def rejected(*args,**kw): raise ValueError('private validation error')
    with pytest.raises(a.ArtifactError,match='^artifact contract rejected$'): consume(a,w,p,r,mux(raw+b'\n'),validator_override=rejected)


@pytest.mark.parametrize('kind',['depth','objects','arrays','commas','colons','string','number','ascii'])
def test_grammar_limits_before_json_graph(tmp_path,monkeypatch,kind):
    a,w,p,r,_=fixture(tmp_path)
    cases={'depth':(b'[[[[[0]]]]]',{}),'objects':(b'[{},{}]',{'max_objects':1}),
           'arrays':(b'[[],[]]',{}),'commas':(b'[0,0,0]',{'max_commas':1}),
           'colons':(b'{"a":0,"b":0}',{'max_colons':1}),
           'string':(b'{"a":"abcd"}',{'max_string_bytes':3}),
           'number':(b'{"a":123}',{'max_number_chars':2}),'ascii':(b'{"a":"\xc3\xa9"}',{})}
    raw,kw=cases[kind];r=repin(w,r,raw)
    monkeypatch.setattr(a.json,'loads',lambda *args,**kw:pytest.fail('prescan budget reached JSON graph'))
    with pytest.raises(a.ArtifactError): consume(a,w,p,r,mux(raw+b'\n'),limits=a.ArtifactLimits(**kw))


def test_injection_guard_does_not_trust_callback(tmp_path):
    a,w,p,r,raw=fixture(tmp_path)
    for allow in (False,1,'yes',None):
        with pytest.raises(a.ArtifactError): a.decode_manifest_logs(mux(raw+b'\n'),profile=p,report=r,validator=lambda *a,**k:None,_allow_injected=allow)


@pytest.mark.parametrize('field',['commit_sha256','tree_sha256'])
def test_null_hash_rejected(tmp_path,field):
    a,w,p,r,raw=fixture(tmp_path);m=json.loads(raw);m[field]='0'*64;raw=w.canonical(m);r=repin(w,r,raw)
    with pytest.raises(a.ArtifactError): consume(a,w,p,r,mux(raw+b'\n'))


@pytest.mark.parametrize('mode',['preflight','control-as','control-cpu','control-wall'])
@pytest.mark.parametrize('change',['nonce','mode','unknown','format'])
def test_small_mode_exact_full_object(tmp_path,mode,change):
    a,w,p,r,raw=fixture(tmp_path,mode);m=json.loads(raw)
    m[change]={'nonce':'b'*32,'mode':'decode','unknown':1,'format':'wrong'}[change]
    raw=w.canonical(m);r=repin(w,r,raw)
    with pytest.raises(a.ArtifactError): consume(a,w,p,r,mux(raw+b'\n'))


def nested_fixture(tmp_path, *, share_symlink=False, external_reuses_blob=False):
    import zlib
    h=helpers();w,p,code,inputs,work=h.stage(tmp_path)
    blob=b'hi';link=blob if share_symlink else b'../file';oid=h.oid
    external_oid=oid('blob',blob) if external_reuses_blob else 'b'*40
    subtree=b'120000 alias\0'+bytes.fromhex(oid('blob',link))+b'100755 run\0'+bytes.fromhex(oid('blob',blob))+b'160000 sub\0'+bytes.fromhex(external_oid)
    tree=b'100644 d.c\0'+bytes.fromhex(oid('blob',blob))+b'40000 d\0'+bytes.fromhex(oid('tree',subtree))+b'160000 sub\0'+bytes.fromhex(external_oid)
    commit=(f'tree {oid("tree",tree)}\nauthor A <a@b> 1 +0000\ncommitter A <a@b> 1 +0000\n\nsynthetic\n').encode()
    objects=[(1,commit),(2,tree),(2,subtree),(3,blob)]+([] if share_symlink else [(3,link)])
    packed=bytearray(struct.pack('>4sII',b'PACK',2,len(objects)))
    for kind,raw in objects:
        size=len(raw);packed.append((kind<<4)|(size&15)|(128 if size>>4 else 0));size>>=4
        while size:
            packed.append((size&127)|(128 if size>>7 else 0));size>>=7
        packed.extend(zlib.compress(raw))
    packed.extend(hashlib.sha1(packed).digest());(inputs/'source.pack').write_bytes(packed)
    p['pack'].update(bytes=len(packed),sha256=hashlib.sha256(packed).hexdigest(),commit_oid=oid('commit',commit),expected_tree_oid=oid('tree',tree),required_entry=None,required_gitlink_oid=external_oid)
    r=w.execute_decode(p,code_dir=code,inputs_dir=inputs,work_dir=work,_runtime=w.synthetic_runtime(p))
    return artifact(),w,p,r,(work/'manifest.json').read_bytes()


def test_genuine_nested_tree_git_order_and_occurrence_accounting(tmp_path):
    a,w,p,r,raw=nested_fixture(tmp_path)
    result=consume(a,w,p,r,mux(raw+b'\n'))
    assert [e['path'] for e in result.manifest['entries']]==['d','d.c','d/alias','d/run']
    assert result.manifest['counts']['content_bytes']==11


@pytest.mark.parametrize('fault',['dirsha','dirsize','diroid','missingparent','leafparent','sort','nfc','crosscollision','loadedbool','loadextra','members','pathdepth','pathbytes','contentcap','treecap','blobcap','loadedcap','linkorder','linkomit'])
def test_nested_semantics_and_tightened_limits(tmp_path,fault):
    a,w,p,r,raw=nested_fixture(tmp_path);m=json.loads(raw);limits=a.ArtifactLimits()
    if fault=='dirsha': m['entries'][0]['sha256']='c'*64
    if fault=='dirsize': m['entries'][0]['size']+=1
    if fault=='diroid': m['entries'][0]['oid']='c'*40
    if fault=='missingparent': del m['entries'][0]
    if fault=='leafparent': m['entries'][0].update(mode='100644',kind='blob')
    if fault=='sort': m['entries'].reverse()
    if fault=='nfc':
        m['entries'][0]['path']='é';m['gitlinks'][0]['path']='e\u0301'
    if fault=='crosscollision': m['gitlinks'][0]['path']='D'
    if fault=='loadedbool': m['counts']['loaded_bytes']=True;r['counts']=m['counts']
    if fault=='loadextra': m['counts']['loaded_bytes']+=4194304;r['counts']=m['counts']
    if fault=='members': limits=a.ArtifactLimits(max_members=4)
    if fault=='pathdepth': limits=a.ArtifactLimits(max_path_depth=1)
    if fault=='pathbytes': limits=a.ArtifactLimits(max_path_bytes=6)
    if fault=='contentcap': limits=a.ArtifactLimits(max_content_bytes=10)
    if fault=='treecap': limits=a.ArtifactLimits(max_tree_bytes=1)
    if fault=='blobcap': limits=a.ArtifactLimits(max_blob_bytes=1)
    if fault=='loadedcap': limits=a.ArtifactLimits(max_loaded_bytes=1)
    if fault=='linkorder': r['matched_gitlinks'].reverse()
    if fault=='linkomit': r['matched_gitlinks']=r['matched_gitlinks'][:1]
    raw=w.canonical(m);r=repin(w,r,raw)
    with pytest.raises(a.ArtifactError): consume(a,w,p,r,mux(raw+b'\n'),limits=limits)


@pytest.mark.parametrize('fault',['parentowner','fileowner','filemode','multilink','fileclose','parentclose'])
def test_private_writer_metadata_and_close_errors(tmp_path,monkeypatch,fault):
    a=artifact();parent=tmp_path/'private';parent.mkdir(mode=0o700);raw=b'{}'
    fstat=a.os.fstat;close=a.os.close
    class Info:
        def __init__(self,s): self.original=s
        def __getattr__(self,k): return getattr(self.original,k)
    def changed(fd):
        info=fstat(fd);result=Info(info)
        if a.stat.S_ISDIR(info.st_mode) and fault=='parentowner': result.st_uid=info.st_uid+1
        if a.stat.S_ISREG(info.st_mode):
            if fault=='fileowner': result.st_uid=info.st_uid+1
            if fault=='filemode': result.st_mode=(info.st_mode&~0o777)|0o644
            if fault=='multilink': result.st_nlink=2
        return result
    def badclose(fd):
        info=fstat(fd);close(fd)
        if (fault=='fileclose' and a.stat.S_ISREG(info.st_mode)) or (fault=='parentclose' and a.stat.S_ISDIR(info.st_mode)):
            raise OSError('private close error')
    monkeypatch.setattr(a.os,'fstat',changed)
    if fault in ('fileclose','parentclose'): monkeypatch.setattr(a.os,'close',badclose)
    with pytest.raises(a.ArtifactError,match='^private artifact write rejected$'):
        a.store_manifest(raw,expected_size=2,expected_sha256=hashlib.sha256(raw).hexdigest(),destination=parent/'manifest.json')


def test_no_raw_validator_error(tmp_path):
    a,w,p,r,raw=fixture(tmp_path)
    def broken(*args,**kw): raise RuntimeError('private native details')
    with pytest.raises(a.ArtifactError,match='^artifact contract rejected$'):
        consume(a,w,p,r,mux(raw+b'\n'),validator_override=broken)


@pytest.mark.parametrize('value',[0,-1,True,1.0,'1',999999])
def test_publisher_rejects_invalid_write_counts(tmp_path,value):
    a,w,p,r,raw=fixture(tmp_path,'preflight')
    class Sink:
        def write(self,fd,data): return value
    with pytest.raises(w.WorkerError): w.publish_manifest(Sink(),p,r,work_dir=tmp_path/'work')


def test_publisher_size_pin_and_chunk_bound(tmp_path):
    a,w,p,r,raw=fixture(tmp_path,'preflight')
    class Sink:
        def __init__(self): self.data=bytearray()
        def write(self,fd,data):
            assert fd==2 and 0<len(data)<=65536
            self.data.extend(data);return len(data)
    bad=copy.deepcopy(r);bad['manifest']['bytes']+=1
    sink=Sink()
    with pytest.raises(w.WorkerError): w.publish_manifest(sink,p,bad,work_dir=tmp_path/'work')
    assert not sink.data
    # Modest genuine canonical file spans the fixed write chunk boundary.
    w.os.unlink(tmp_path/'work'/'manifest.json')
    r['manifest']=w.write_manifest(tmp_path/'work',{'synthetic':'x'*66000})
    w.publish_manifest(sink,p,r,work_dir=tmp_path/'work')
    assert sink.data==(tmp_path/'work'/'manifest.json').read_bytes()+b'\n'


def test_writer_budget_before_open_and_successful_short_writes(tmp_path,monkeypatch):
    a=artifact();parent=tmp_path/'private';parent.mkdir(mode=0o700);raw=b'{}';sha=hashlib.sha256(raw).hexdigest()
    with pytest.raises(a.ArtifactError): a.store_manifest(raw,expected_size=16777217,expected_sha256=sha,destination=parent/'manifest.json')
    assert not (parent/'manifest.json').exists()
    original=a.os.write
    monkeypatch.setattr(a.os,'write',lambda fd,data:original(fd,data[:1]))
    result=a.store_manifest(raw,expected_size=2,expected_sha256=sha,destination=parent/'manifest.json')
    assert result['bytes']==2 and (parent/'manifest.json').read_bytes()==raw


@pytest.mark.parametrize('fault',['sha256','offsetting-sizes'])
def test_repeated_blob_oid_requires_identical_raw_facts(tmp_path,fault):
    a,w,p,r,raw=nested_fixture(tmp_path);m=json.loads(raw)
    first=next(e for e in m['entries'] if e['path']=='d.c')
    second=next(e for e in m['entries'] if e['path']=='d/run')
    assert first['oid']==second['oid'] and (first['size'],second['size'])==(2,2)
    if fault=='sha256': second['sha256']='c'*64
    else: first['size']=1;second['size']=3
    raw=w.canonical(m);r=repin(w,r,raw)
    # The shared report validator admits this semantic fault's outer binding.
    w.validate_report(r,p,_allow_injected=True)
    with pytest.raises(a.ArtifactError): consume(a,w,p,r,mux(raw+b'\n'))


def test_repeated_blob_oid_can_cross_file_executable_and_symlink_modes(tmp_path):
    a,w,p,r,raw=nested_fixture(tmp_path,share_symlink=True)
    result=consume(a,w,p,r,mux(raw+b'\n'))
    leaves=[e for e in result.manifest['entries'] if e['kind']!='tree']
    assert {e['mode'] for e in leaves}=={'100644','100755','120000'}
    assert len({(e['oid'],e['size'],e['sha256']) for e in leaves})==1
    assert result.manifest['counts']['content_bytes']==6


@pytest.mark.parametrize('source',['root-tree','leaf-blob'])
def test_commit_oid_cannot_conflict_with_loaded_raw_object_type(tmp_path,source):
    a,w,p,r,raw=nested_fixture(tmp_path);m=json.loads(raw)
    conflicting=m['tree_oid'] if source=='root-tree' else next(e['oid'] for e in m['entries'] if e['kind']=='blob')
    m['commit_oid']=conflicting
    p=copy.deepcopy(p);r=copy.deepcopy(r)
    p['pack']['commit_oid']=conflicting;r['pack']['commit_oid']=conflicting
    raw=w.canonical(m);r=repin(w,r,raw)
    w.validate_report(r,p,_allow_injected=True)
    with pytest.raises(a.ArtifactError): consume(a,w,p,r,mux(raw+b'\n'))


def test_external_gitlink_oid_is_not_a_loaded_raw_fact(tmp_path):
    a,w,p,r,raw=nested_fixture(tmp_path,external_reuses_blob=True)
    # External gitlinks are references only: this verifier deliberately never
    # loads or asserts their referenced raw type, even for a locally known OID.
    m=json.loads(raw)
    external=[e for e in m['gitlinks']]
    assert len(external)==2 and len({e['oid'] for e in external})==1
    assert external[0]['oid']==next(e['oid'] for e in m['entries'] if e['kind']=='blob')
    result=consume(a,w,p,r,mux(raw+b'\n'))
    assert result.manifest['gitlinks']==external
