"""Pure synthetic packs; deliberately no application or conftest imports."""
import hashlib
import importlib.util
from pathlib import Path
import struct
import sys
import zlib
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "av_sources"

def module(name):
    path = SCRIPTS / (name + ".py")
    assert path.is_file(), "bounded pack store implementation is missing"
    spec = importlib.util.spec_from_file_location("offline_" + name, path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = result
    spec.loader.exec_module(result)
    return result

def oid(kind, raw):
    return hashlib.sha1(kind.encode() + b" " + str(len(raw)).encode() + b"\0" + raw).hexdigest()

def header(kind, size):
    out = bytearray([(kind << 4) | (size & 15)])
    size >>= 4
    while size:
        out[-1] |= 128
        out.append(size & 127)
        size >>= 7
    return bytes(out)

def entry(kind, raw, ref=b""):
    return header(kind, len(raw)) + ref + zlib.compress(raw)

def pack(entries, version=2, count=None):
    body = b"PACK" + struct.pack(">II", version, len(entries) if count is None else count) + b"".join(entries)
    return body + hashlib.sha1(body).digest()

def source(tmp_path, data):
    tmp_path.chmod(0o700)
    path = tmp_path / "source.pack"
    path.write_bytes(data)
    path.chmod(0o600)
    return path

def load(tmp_path, data, **kwargs):
    m = module("pack_store")
    return m.load_pack(source(tmp_path, data), expected_size=len(data), expected_sha256=hashlib.sha256(data).hexdigest(), **kwargs)

def test_complete_commit_tree(tmp_path):
    blob = b"hello\n"
    tree = b"100644 note.txt\0" + bytes.fromhex(oid("blob", blob))
    commit = (b"tree " + oid("tree", tree).encode() + b"\nauthor A <a@b> 0 +0000\ncommitter A <a@b> 0 +0000\n\nmessage\n")
    store = load(tmp_path, pack([entry(1, commit), entry(2, tree), entry(3, blob)]))
    manifest = module("git_objects").verify_commit_tree(oid("commit", commit), store.read_object)
    assert manifest["entries"][0]["sha256"] == hashlib.sha256(blob).hexdigest()


def varint(n):
    out = bytearray()
    while n >= 128:
        out.append((n & 127) | 128)
        n >>= 7
    return bytes(out + bytes([n]))

def delta(base, target):
    assert 0 < len(target) < 128
    return varint(len(base)) + varint(len(target)) + bytes([len(target)]) + target

def distance(n):
    out = [n & 127]
    while n >> 7:
        n = (n >> 7) - 1
        out.append((n & 127) | 128)
    return bytes(reversed(out))

@pytest.mark.parametrize("version", [2, 3])
@pytest.mark.parametrize("kind,name", [(1,"commit"),(2,"tree"),(3,"blob"),(4,"tag")])
def test_base_types_binary_and_duplicate(tmp_path, version, kind, name):
    raw = b"\0binary\xff"
    store = load(tmp_path, pack([entry(kind,raw),entry(kind,raw),entry(3,b"")],version))
    assert store.read_object(oid(name,raw)) == (name,raw)
    assert store.read_object(oid("blob",b"")) == ("blob",b"")
    assert store.summary["objects_resolved"] == 3
    assert store.summary["unique_objects"] == 2
    assert store.summary["resolved_bytes"] == len(raw)*2
    assert not any(store.summary[k] for k in ("source_identity_verified","build_inputs_adopted","os_resource_limits_verified","dns_ip_egress_verified"))
    with pytest.raises(TypeError): store._objects["x"] = ("blob",b"x")
    summary = store.summary
    summary["objects_resolved"] = 0
    assert store.summary["objects_resolved"] == 3

@pytest.mark.parametrize("kind,name", [(1,"commit"),(2,"tree"),(3,"blob"),(4,"tag")])
def test_forward_ref_and_mixed_ofs_delta(tmp_path, kind, name):
    base, middle, target = b"base", b"middle", b"final"
    first = entry(7,delta(base,middle),bytes.fromhex(oid(name,base)))
    second_offset = 12 + len(first)
    second = entry(6,delta(middle,target),distance(second_offset-12))
    store = load(tmp_path,pack([first,second,entry(kind,base)]))
    assert store.read_object(oid(name,target)) == (name,target)
    assert store.summary["max_delta_depth"] == 2
    assert store.summary["inflated_bytes"] == len(delta(base,middle))+len(delta(middle,target))+len(base)
    assert store.summary["resolved_bytes"] == len(base)+len(middle)+len(target)

def test_ofs_multibyte_and_copy_flag_positions(tmp_path):
    base = bytes(range(256))*257
    first = entry(3,base)
    assert len(first)>128
    # offset 256 via second offset byte, size 256 via second size byte.
    target=base[256:512]
    program=varint(len(base))+varint(256)+bytes([0xA2,1,1])
    store=load(tmp_path,pack([first,entry(6,program,distance(len(first)))]))
    assert store.read_object(oid("blob",target)) == ("blob",target)

def test_copy_zero_means_65536(tmp_path):
    base=b"x"*65536
    first=entry(3,base)
    program=varint(len(base))+varint(len(base))+b"\x80"
    store=load(tmp_path,pack([first,entry(6,program,distance(len(first)))]))
    assert store.summary["resolved_bytes"]==131072

def test_symlink_gitlink_integration(tmp_path):
    blob=b"hello\n"; link=b"note.txt"
    tree=(b"160000 external\0"+b"\xaa"*20+b"120000 link\0"+bytes.fromhex(oid("blob",link))+b"100644 note.txt\0"+bytes.fromhex(oid("blob",blob)))
    commit=b"tree "+oid("tree",tree).encode()+b"\nauthor A <a@b> 0 +0000\ncommitter A <a@b> 0 +0000\n\nmsg"
    store=load(tmp_path,pack([entry(3,blob),entry(3,link),entry(2,tree),entry(1,commit)]))
    manifest=module("git_objects").verify_commit_tree(oid("commit",commit),store.read_object)
    assert manifest["gitlinks"][0]["path"]=="external"
    assert manifest["entries"][0]["kind"]=="symlink"

@pytest.mark.parametrize("body", [b"",b"NOPE"+struct.pack(">II",2,1),b"PACK"+struct.pack(">II",1,1),b"PACK"+struct.pack(">II",2,0),b"PACK"+struct.pack(">II",2,60002)])
def test_bad_container(tmp_path,body):
    m=module("pack_store")
    data=body+hashlib.sha1(body).digest()
    with pytest.raises(m.PackError): load_with(m,tmp_path,data)

def load_with(m,tmp_path,data,**kw):
    return m.load_pack(source(tmp_path,data),expected_size=len(data),expected_sha256=hashlib.sha256(data).hexdigest(),**kw)

@pytest.mark.parametrize("bad", [header(0,0)+zlib.compress(b""),header(5,0)+zlib.compress(b""),b"\xb0"+b"\x80"*10,header(3,1)+zlib.compress(b"ab"),header(3,2)+zlib.compress(b"a"),header(3,0)+b"bad",header(3,2)+zlib.compress(b"ab")[:-1],entry(3,b"x")+zlib.compress(b""),entry(3,b"x")+b"junk"])
def test_invalid_entry(tmp_path,bad):
    m=module("pack_store")
    with pytest.raises(m.PackError): load_with(m,tmp_path,pack([bad]))

@pytest.mark.parametrize("ref",[b"\0",b"\x7f",b"\x01"])
def test_bad_ofs_reference(tmp_path,ref):
    m=module("pack_store")
    with pytest.raises(m.PackError):load_with(m,tmp_path,pack([entry(6,delta(b"a",b"b"),ref)]))

@pytest.mark.parametrize("program", [b"",b"\x80"*11,b"\x01",b"\x02\x01\x01b",b"\x01\x01\0",b"\x01\x02\x02b",b"\x01\x01\x91\x02\x01",b"\x01\x01\x81",b"\x01\x02\x01b",b"\x01\x01\x01bc",b"\x01\x01\x02bc"])
def test_invalid_delta(tmp_path,program):
    m=module("pack_store");first=entry(3,b"a")
    with pytest.raises(m.PackError):load_with(m,tmp_path,pack([first,entry(6,program,distance(len(first)))]))

def test_missing_ref_base(tmp_path):
    m=module("pack_store")
    with pytest.raises(m.PackError):load_with(m,tmp_path,pack([entry(7,delta(b"a",b"b"),b"\x12"*20)]))

@pytest.mark.parametrize("field",["max_pack_bytes","max_inflated_bytes","max_resolved_bytes","max_blob_bytes","max_delta_bytes","max_metadata_bytes","max_objects","max_delta_depth","max_seconds"])
@pytest.mark.parametrize("value",[True,0,-1,1.5,10**10])
def test_invalid_limits(tmp_path,field,value):
    m=module("pack_store")
    with pytest.raises(m.PackError):load_with(m,tmp_path,pack([entry(3,b"x")]),limits=m.PackLimits(**{field:value}))

@pytest.mark.parametrize("field",["max_inflated_bytes","max_resolved_bytes","max_blob_bytes"])
def test_byte_boundaries(tmp_path,field):
    m=module("pack_store");data=pack([entry(3,b"abc")])
    assert load_with(m,tmp_path,data,limits=m.PackLimits(**{field:3})).read_object(oid("blob",b"abc"))[1]==b"abc"
    with pytest.raises(m.PackError):load_with(m,tmp_path,data,limits=m.PackLimits(**{field:2}))

def test_delta_depth_and_accounting_boundaries(tmp_path):
    m=module("pack_store");a=b"a";b=b"bb";c=b"ccc"
    e1=entry(3,a);e2=entry(6,delta(a,b),distance(len(e1)));e3=entry(6,delta(b,c),distance(len(e2)))
    data=pack([e1,e2,e3])
    assert load_with(m,tmp_path,data,limits=m.PackLimits(max_delta_depth=2,max_resolved_bytes=6,max_inflated_bytes=12,max_delta_bytes=6)).summary["max_delta_depth"]==2
    for field,val in [("max_delta_depth",1),("max_resolved_bytes",5),("max_inflated_bytes",11),("max_delta_bytes",5)]:
        with pytest.raises(m.PackError):load_with(m,tmp_path,data,limits=m.PackLimits(**{field:val}))

@pytest.mark.parametrize("value",[None,True,1,"A"*40,"a"*39,"a"*41,"a"*40+"\n"])
def test_read_oid_validation(tmp_path,value):
    m=module("pack_store");store=load_with(m,tmp_path,pack([entry(3,b"x")]))
    with pytest.raises(m.PackError):store.read_object(value)
    with pytest.raises(m.PackError):store.read_object("0"*40)

@pytest.mark.parametrize("clock",[lambda:float("nan"),lambda:float("inf"),lambda:True])
def test_invalid_clock(tmp_path,clock):
    m=module("pack_store")
    with pytest.raises(m.PackError):load_with(m,tmp_path,pack([entry(3,b"x")]),_clock=clock)

def test_clock_expired_decreasing_and_interrupted(tmp_path):
    m=module("pack_store");data=pack([entry(3,b"x")])
    for values in ([0,600],[1,0]):
        it=iter(values)
        with pytest.raises(m.PackError):load_with(m,tmp_path,data,_clock=lambda:next(it))
    def interrupted():raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):load_with(m,tmp_path,data,_clock=interrupted)

@pytest.mark.parametrize("mode",[0o644,0o400,0o700])
def test_bad_file_modes(tmp_path,mode):
    m=module("pack_store");data=pack([entry(3,b"x")]);p=source(tmp_path,data);p.chmod(mode)
    with pytest.raises(m.PackError):m.load_pack(p,expected_size=len(data),expected_sha256=hashlib.sha256(data).hexdigest())

def test_bad_parent_hardlink_symlink_and_fifo(tmp_path):
    import os
    m=module("pack_store");data=pack([entry(3,b"x")]);p=source(tmp_path,data)
    def attempt(path=p):return m.load_pack(path,expected_size=len(data),expected_sha256=hashlib.sha256(data).hexdigest())
    tmp_path.chmod(0o755)
    with pytest.raises(m.PackError):attempt()
    tmp_path.chmod(0o700)
    os.link(p,tmp_path/"hard")
    with pytest.raises(m.PackError):attempt()
    (tmp_path/"hard").unlink()
    (tmp_path/"sym").symlink_to(p)
    with pytest.raises(m.PackError):attempt(tmp_path/"sym")
    (tmp_path/"ancestor").symlink_to(tmp_path,target_is_directory=True)
    with pytest.raises(m.PackError):attempt(tmp_path/"ancestor"/"source.pack")
    os.mkfifo(tmp_path/"fifo",0o600)
    with pytest.raises(m.PackError):attempt(tmp_path/"fifo")

@pytest.mark.parametrize("size,hash_",[(True,"a"*64),(0,"a"*64),(-1,"a"*64),(2**27,"a"*64),(1,"A"*64),(1,"a"*63),(1,None),(1,"a"*64+"\n")])
def test_bad_pins(tmp_path,size,hash_):
    m=module("pack_store")
    with pytest.raises(m.PackError):m.load_pack(tmp_path/"absent",expected_size=size,expected_sha256=hash_)

def test_drift_and_checksums(tmp_path,monkeypatch):
    m=module("pack_store");data=pack([entry(3,b"x")]);p=source(tmp_path,data)
    for d,h,n in [(data,"0"*64,len(data)),(data[:-1]+b"0",hashlib.sha256(data[:-1]+b"0").hexdigest(),len(data)),(data,hashlib.sha256(data).hexdigest(),len(data)+1)]:
        p.write_bytes(d)
        with pytest.raises(m.PackError):m.load_pack(p,expected_size=n,expected_sha256=h)
    p.write_bytes(data)
    original=m.os.read;done=False
    def changed(fd,n):
        nonlocal done
        chunk=original(fd,n)
        if not done:
            done=True;p.write_bytes(data)
        return chunk
    monkeypatch.setattr(m.os,"read",changed)
    with pytest.raises(m.PackError):m.load_pack(p,expected_size=len(data),expected_sha256=hashlib.sha256(data).hexdigest())


def test_nonfinite_oversized_integer_clock(tmp_path):
    m=module("pack_store")
    with pytest.raises(m.PackError):load_with(m,tmp_path,pack([entry(3,b"x")]),_clock=lambda:10**1000)


def test_bounded_read_and_decompression_calls(tmp_path,monkeypatch):
    m=module("pack_store");original_read=m.os.read;original_decompress=m.zlib.decompressobj
    reads=[];decompressions=[]
    def read(fd,n):
        assert type(n) is int and 0<n<=65536
        reads.append(n)
        return original_read(fd,n)
    class Decoder:
        def __init__(self):self.inner=original_decompress()
        def __getattr__(self,name):return getattr(self.inner,name)
        def decompress(self,data,max_length):
            assert 0<max_length<=65536
            decompressions.append(max_length)
            return self.inner.decompress(data,max_length)
        def flush(self,*args):pytest.fail("unbounded flush forbidden")
    monkeypatch.setattr(m.os,"read",read)
    monkeypatch.setattr(m.zlib,"decompressobj",Decoder)
    # >64KiB incompressible-ish compressed input, and >64KiB inflated output.
    raw=hashlib.shake_256(b"synthetic").digest(140000)
    store=load_with(m,tmp_path,pack([entry(3,raw)]))
    assert store.read_object(oid("blob",raw))==( "blob",raw)
    assert len(reads)>=3 and len(decompressions)>=3
    reads.clear();decompressions.clear()
    bomb=header(3,1)+zlib.compress(b"x"*100000)
    with pytest.raises(m.PackError):load_with(m,tmp_path,pack([bomb]))
    assert decompressions==[2]


def test_count_and_pack_metadata_boundaries(tmp_path):
    m=module("pack_store");data=pack([entry(4,b"abcd"),entry(3,b"x")])
    assert load_with(m,tmp_path,data,limits=m.PackLimits(max_pack_bytes=len(data),max_objects=2,max_metadata_bytes=4)).summary["objects_resolved"]==2
    for field,val in [("max_pack_bytes",len(data)-1),("max_objects",1),("max_metadata_bytes",3)]:
        with pytest.raises(m.PackError):load_with(m,tmp_path,data,limits=m.PackLimits(**{field:val}))
    for count in (1,3):
        with pytest.raises(m.PackError):load_with(m,tmp_path,pack([entry(3,b"a"),entry(3,b"b")],count=count))


def test_resolved_metadata_and_target_preallocation_cap(tmp_path):
    m=module("pack_store");base=b"a";first=entry(4,base)
    data=pack([first,entry(6,delta(base,b"long"),distance(len(first)))])
    with pytest.raises(m.PackError):load_with(m,tmp_path,data,limits=m.PackLimits(max_metadata_bytes=3))
    data=pack([entry(3,b"a"),entry(7,b"\x01"+varint(2**40),bytes.fromhex(oid("blob",base)))])
    with pytest.raises(m.PackError):load_with(m,tmp_path,data)


def test_truncated_references_nonboundary_ofs_and_overflow(tmp_path):
    m=module("pack_store");first=entry(3,b"a")
    for bad in [entry(6,delta(b"a",b"b"),distance(len(first)-1)),header(6,1)+b"\x80"*11,header(7,1)+b"short",b"\xb0"+b"\xff"*8+b"\x7f"]:
        with pytest.raises(m.PackError):load_with(m,tmp_path,pack([first,bad]))


@pytest.mark.parametrize("selection", [(0,), (1,), (0, 1)])
def test_delta_self_unresolvable_refs(tmp_path, selection):
    m = module("pack_store")
    # Preserve both original self-cycles and the original combined case.
    fixtures = []
    for base, target in [(b"a", b"b"), (b"b", b"a")]:
        program = delta(base, target)
        encoded = entry(7, program, bytes.fromhex(oid("blob", target)))
        ref_start = len(header(7, len(program)))
        assert encoded[ref_start:ref_start + 20].hex() == oid("blob", target)
        assert zlib.decompress(encoded[ref_start + 20:]) == program
        fixtures.append(encoded)
    with pytest.raises(m.PackError, match="missing, thin, cyclic or unresolvable delta bases"):
        load_with(m, tmp_path, pack([fixtures[i] for i in selection]))


def test_delta_mutual_ref_cycle(tmp_path):
    m = module("pack_store")
    # Output b"b" requires output b"a"; output b"a" requires output b"b".
    a_program = delta(b"a", b"b")
    b_program = delta(b"b", b"a")
    a = entry(7, a_program, bytes.fromhex(oid("blob", b"a")))
    b = entry(7, b_program, bytes.fromhex(oid("blob", b"b")))
    a_ref_start = len(header(7, len(a_program)))
    b_ref_start = len(header(7, len(b_program)))
    assert oid("blob", b"a") != oid("blob", b"b")
    assert a[a_ref_start:a_ref_start + 20].hex() == oid("blob", b"a")
    assert b[b_ref_start:b_ref_start + 20].hex() == oid("blob", b"b")
    assert zlib.decompress(a[a_ref_start + 20:]) == a_program
    assert zlib.decompress(b[b_ref_start + 20:]) == b_program
    with pytest.raises(m.PackError, match="missing, thin, cyclic or unresolvable delta bases"):
        load_with(m, tmp_path, pack([a, b]))


def test_delta_mixed_ref_ofs_cycle(tmp_path):
    m = module("pack_store")
    # First output b"b" waits for b"a"; second produces b"a" from first.
    first_program = delta(b"a", b"b")
    second_program = delta(b"b", b"a")
    first = entry(7, first_program, bytes.fromhex(oid("blob", b"a")))
    encoded_distance = distance(len(first))
    second = entry(6, second_program, encoded_distance)
    first_ref_start = len(header(7, len(first_program)))
    second_ref_start = len(header(6, len(second_program)))
    assert first[first_ref_start:first_ref_start + 20].hex() == oid("blob", b"a")
    assert zlib.decompress(first[first_ref_start + 20:]) == first_program
    # Tiny fixture has a single-byte distance; prove its raw reference reaches
    # the first header at pack offset 12 from the second header's own offset.
    assert len(encoded_distance) == 1
    assert second[second_ref_start] == len(first)
    assert 12 + len(first) - second[second_ref_start] == 12
    assert zlib.decompress(second[second_ref_start + 1:]) == second_program
    assert oid("blob", b"a") != oid("blob", b"b")
    with pytest.raises(m.PackError, match="missing, thin, cyclic or unresolvable delta bases"):
        load_with(m, tmp_path, pack([first, second]))


def test_ref_base_produced_by_forward_ref_delta(tmp_path):
    m = module("pack_store")
    base, middle, target = b"base", b"middle", b"target"
    first_program = delta(middle, target)
    second_program = delta(base, middle)
    first = entry(7, first_program, bytes.fromhex(oid("blob", middle)))
    second = entry(7, second_program, bytes.fromhex(oid("blob", base)))
    first_ref_start = len(header(7, len(first_program)))
    second_ref_start = len(header(7, len(second_program)))
    assert first[first_ref_start:first_ref_start + 20].hex() == oid("blob", middle)
    assert second[second_ref_start:second_ref_start + 20].hex() == oid("blob", base)
    assert zlib.decompress(first[first_ref_start + 20:]) == first_program
    assert zlib.decompress(second[second_ref_start + 20:]) == second_program
    store = load_with(m, tmp_path, pack([first, second, entry(3, base)]))
    assert store.read_object(oid("blob", middle)) == ("blob", middle)
    assert store.read_object(oid("blob", target)) == ("blob", target)
    assert store.summary["objects_resolved"] == 3
    assert store.summary["max_delta_depth"] == 2


def test_clock_checked_after_work_and_parse_interruption(tmp_path,monkeypatch):
    m=module("pack_store");data=pack([entry(3,b"x")]);ticks=[]
    def clock():ticks.append(0);return 0
    load_with(m,tmp_path,data,_clock=clock)
    count=len(ticks);calls=0
    def last_expiry():
        nonlocal calls
        calls+=1
        return 600 if calls==count else 0
    with pytest.raises(m.PackError):load_with(m,tmp_path,data,_clock=last_expiry)
    def cancelled(fd,n):raise KeyboardInterrupt
    monkeypatch.setattr(m.os,"read",cancelled)
    with pytest.raises(KeyboardInterrupt):load_with(m,tmp_path,data)


def test_fake_ownership_and_named_inode_replacement(tmp_path,monkeypatch):
    import types
    m=module("pack_store");data=pack([entry(3,b"x")]);original=m.os.geteuid
    monkeypatch.setattr(m.os,"geteuid",lambda:original()+1)
    with pytest.raises(m.PackError):load_with(m,tmp_path,data)
    monkeypatch.setattr(m.os,"geteuid",original)
    p=source(tmp_path,data);read=m.os.read;changed=False
    def replace(fd,n):
        nonlocal changed
        chunk=read(fd,n)
        if not changed:
            changed=True
            replacement=tmp_path/"replacement"
            replacement.write_bytes(data);replacement.chmod(0o600);replacement.replace(p)
        return chunk
    monkeypatch.setattr(m.os,"read",replace)
    with pytest.raises(m.PackError):m.load_pack(p,expected_size=len(data),expected_sha256=hashlib.sha256(data).hexdigest())


def test_collision_detection_pure_digest_seam(tmp_path,monkeypatch):
    m=module("pack_store");original=m.hashlib.sha1
    class Digest:
        def __init__(self,data=b""):self.inner=original(data)
        def update(self,data):self.inner.update(data)
        def digest(self):return self.inner.digest()
        def hexdigest(self):return "a"*40
    data=pack([entry(3,b"one"),entry(4,b"two")])
    monkeypatch.setattr(m.hashlib,"sha1",Digest)
    with pytest.raises(m.PackError,match="collision"):load_with(m,tmp_path,data)


def test_source_mutation_during_resolution_is_rejected(tmp_path,monkeypatch):
    m=module("pack_store");first=entry(3,b"a")
    data=pack([first,entry(6,delta(b"a",b"b"),distance(len(first)))])
    path=source(tmp_path,data);original=m._delta
    def changed(*args):
        value=original(*args)
        path.write_bytes(data)
        return value
    monkeypatch.setattr(m,"_delta",changed)
    with pytest.raises(m.PackError):m.load_pack(path,expected_size=len(data),expected_sha256=hashlib.sha256(data).hexdigest())
