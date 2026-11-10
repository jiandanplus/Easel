"""Pure N3 tests. Expected bytes are independently Root-frozen artifacts."""
import base64
import importlib.util
import json
from pathlib import Path
import pytest

BASE = Path(__file__).parent / "fixtures/media_backup_b3/network"
GOLD = BASE / "profile-v1"

def api():
    name = "platform_service.media_backup_b3.network_profile"
    assert importlib.util.find_spec(name) is not None, "N3 pure validator is absent"
    return __import__(name, fromlist=["*"])

def observation():
    m = api()
    from platform_service.media_backup_b3.netlink_contract import parse_dump
    from platform_service.media_backup_b3.netlink_observer import NetworkDump
    f = json.loads((BASE / "linuxkit-6.12.76-same-namespace-v1.public.json").read_bytes())
    def sysfs(side):
        result = []
        for name, v in f[side]["sysfs"].items():
            if v["kind"] == "control":
                result.append(m.ControlEntry(name,v["uid"],v["gid"],v["mode"],"regular"))
            else:
                result.append(m.InterfaceEntry(name,int(v["ifindex"]),int(v["iflink"]),int(v["type"]),int(v["flags"],16),v["operstate"],bytes.fromhex(v["address"].replace(":",""))))
        return tuple(result)
    ds = []
    for d in f["dumps"]:
        args = tuple(d[k] for k in ("request_type","family","sequence","local_portid"))
        packets = tuple(base64.b64decode(p) for p in d["packets"])
        ds.append(NetworkDump(*args,packets,parse_dump(*args,packets)))
    return m.NetworkObservation(f["kernel_release"],"little",m.NamespaceIdentity(**f["before"]["netns"]),m.NamespaceIdentity(**f["after"]["netns"]),sysfs("before"),sysfs("after"),m.ProcViews(**{k:v.encode() for k,v in f["before"]["proc"].items()}),m.ProcViews(**{k:v.encode() for k,v in f["after"]["proc"].items()}),tuple(ds))

def test_complete_root_frozen_fixture():
    m = api()
    result = m.validate_network_profile(observation())
    assert result.canonical_security_json == (GOLD / "fixture.security.canonical.json").read_bytes()
    assert result.telemetry_json == (GOLD / "fixture.telemetry.canonical.json").read_bytes()
    assert result.profile_sha256 == "b803b4094d8254da45b7ca9c4666d2875794d18532fc721c87488bd64c245582"
    assert result.security_sha256 == "92bc74696861b35950e3dc4d910ba648c1965a0c7e8e589e67ffed69b0f7ccb7"
    assert result.raw_observation_sha256 == "3e4a5354304f4fdc67009ceddf0419a80d98f3ef7c2e0bbcfd21f37fcfe756c5"


from dataclasses import replace
import hashlib
import struct
from platform_service.media_backup_b3.contracts import B3Error
from platform_service.media_backup_b3.netlink_contract import NetlinkMessage, parse_attributes, parse_message

POLICY = json.loads((GOLD / "policy.canonical.json").read_bytes())

def rejected(value):
    with pytest.raises(B3Error) as exc:
        api().validate_network_profile(value)
    assert str(exc.value) == "invalid B3 network profile"
    assert exc.value.__cause__ is None
    assert exc.value.__suppress_context__


def pack_attrs(attrs):
    parts = []
    for ident, flags, payload in attrs:
        n = 4 + len(payload)
        parts.append(struct.pack("<HH", n, ident | flags) + payload + bytes((-n) % 4))
    return b"".join(parts)


def edit_attrs(raw, path, action):
    attrs = [(a.id,a.flags,a.payload) for a in parse_attributes(raw)]
    for i,(ident,flags,payload) in enumerate(attrs):
        if ident == path[0]:
            if len(path) > 1:
                attrs[i] = (ident,flags,edit_attrs(payload,path[1:],action))
            else:
                attrs[i:i+1] = action(ident,flags,payload)
            return pack_attrs(attrs)
    raise AssertionError("bad test mutation path")


def repack(o, slot, messages):
    d = o.dumps[slot]
    packets = tuple(struct.pack("<IHHII",16+len(m.payload),m.type,2,d.sequence,d.local_portid)+m.payload+bytes((-len(m.payload))%4) for m in messages)
    packets += (struct.pack("<IHHIII",20,3,2,d.sequence,d.local_portid,0),)
    nd = replace(d,datagrams=packets,parsedmessages=tuple(messages))
    return replace(o,dumps=o.dumps[:slot]+(nd,)+o.dumps[slot+1:])


def mutate_attr(o,slot,record,path,action):
    messages = list(o.dumps[slot].parsedmessages)
    m = messages[record]
    size = 16 if m.type == 16 else 8 if m.type == 20 else 12
    messages[record] = replace(m,payload=m.payload[:size]+edit_attrs(m.payload[size:],path,action))
    return repack(o,slot,messages)


def descriptor_rows():
    # Read ONLY independently frozen policy data, never product constants.
    f = json.loads((BASE / "linuxkit-6.12.76-same-namespace-v1.public.json").read_bytes())
    for slot,d in enumerate(f["dumps"]):
        from platform_service.media_backup_b3.netlink_contract import parse_dump
        packets=tuple(base64.b64decode(x) for x in d["packets"])
        messages=parse_dump(d["request_type"],d["family"],d["sequence"],d["local_portid"],packets)
        for ordinal,m in enumerate(messages):
            r=parse_message(m)
            if slot == 0:
                name=next(a.payload[:-1].decode() for a in r.attributes if a.id==3)
                spec=next(x for x in POLICY["links"] if x["name"]==name)
            else:
                group="addresses" if slot==1 else "routes" if slot in (2,3) else "rules"
                h={k:v for k,v in vars(r).items() if k!="attributes"}
                if "route_type" in h:h["type"]=h.pop("route_type")
                spec=next(x for x in POLICY[group] if x["header"]==h)
            def walk(xs,path=()):
                for a in xs:
                    p=path+(a["id"],)
                    yield slot,ordinal,p,a
                    if a["rule"]["tag"]=="children":yield from walk(a["rule"]["attributes"],p)
            yield from walk(spec["attributes"])

ROWS=list(descriptor_rows())

@pytest.mark.parametrize("slot,record,path,spec",ROWS)
@pytest.mark.parametrize("mode",["missing","extra","duplicate","flags","netbyteorder","length"])
def test_every_attribute_closed(slot,record,path,spec,mode):
    def edit(i,f,p):
        if mode=="missing":return []
        if mode=="extra":return [(i,f,p),(16383,0,b"")]
        if mode=="duplicate":return [(i,f,p),(i,f,p)]
        if mode=="flags":return [(i,f^32768,p)]
        if mode=="netbyteorder":return [(i,f|16384,p)]
        return [(i,f,p+b"\0")]
    rejected(mutate_attr(observation(),slot,record,path,edit))

STATIC=[]
VARIABLE=[]
for slot,record,path,a in ROWS:
    rule=a["rule"]
    if rule["tag"]=="fixed" and a["length"]:
        # Every full configuration vector element, plus every other fixed leaf.
        offsets=range(0,a["length"],4) if path in ((26,2,1),(26,10,2)) else (0,)
        STATIC.extend((slot,record,path,off) for off in offsets)
    elif rule["tag"]=="words":
        width=rule["bits"]//8
        for i,v in enumerate(rule["values"]):
            (VARIABLE if v is None else STATIC).append((slot,record,path,i*width))

@pytest.mark.parametrize("slot,record,path,offset",STATIC)
def test_every_static_leaf_and_config_word(slot,record,path,offset):
    def edit(i,f,p):
        b=bytearray(p);b[offset]^=1
        return [(i,f,bytes(b))]
    rejected(mutate_attr(observation(),slot,record,path,edit))

@pytest.mark.parametrize("slot,record,path,offset",VARIABLE)
def test_every_variable_word_is_telemetry(slot,record,path,offset):
    o=observation()
    def edit(i,f,p):
        b=bytearray(p);b[offset]^=1
        return [(i,f,bytes(b))]
    baseline=api().validate_network_profile(repack(o,slot,o.dumps[slot].parsedmessages))
    result=api().validate_network_profile(mutate_attr(o,slot,record,path,edit))
    assert result.canonical_security_json==(GOLD/"fixture.security.canonical.json").read_bytes()
    assert result.raw_observation_sha256!=baseline.raw_observation_sha256
    before=json.loads(baseline.telemetry_json)
    after=json.loads(result.telemetry_json)
    assert after["origin"]==before["origin"]  # Identical packetization, not a transport-only difference.
    def selected(telemetry):
        entries=[e for e in telemetry["netlink"] if (e["slot"],e["record"],tuple(e["path"]))==(slot,record,path)]
        assert len(entries)==1
        return entries[0]
    prior,current=selected(before),selected(after)
    spec=next(a for s,r,p,a in ROWS if (s,r,p)==(slot,record,path))
    bits=spec["rule"]["bits"]
    width=bits//8
    attrs=parse_message(o.dumps[slot].parsedmessages[record]).attributes
    for depth,ident in enumerate(path):
        payload=next(a.payload for a in attrs if a.id==ident)
        if depth+1<len(path):attrs=parse_attributes(payload)
    original=[int.from_bytes(payload[i:i+width],"little") for i in range(0,len(payload),width)]
    expected=original.copy()
    assert offset%width==0
    expected[offset//width]^=1
    assert prior["bits"]==current["bits"]==bits
    assert prior["values"]==original
    assert current["values"]==expected
    assert current["values"][offset//width]==(original[offset//width]^1)


def test_compiled_policy_exact_correspondence():
    from platform_service.media_backup_b3 import _network_policy as p
    assert p.POLICY_BYTES==(GOLD/"policy.canonical.json").read_bytes()
    assert len(p.POLICY_BYTES)==81806
    assert hashlib.sha256(p.POLICY_BYTES).hexdigest()=="a87d2bbd5bb8afb554f827fc8609ca02a5ca5b5c1817cb09045e0c35bf230d36"
    def compiled(xs):
        out=[]
        for x in xs:
            r=x["rule"];tag=r["tag"]
            v=bytes.fromhex(r["hex"]) if tag=="fixed" else compiled(r["attributes"]) if tag=="children" else (r["bits"],tuple(r["values"])) if tag=="words" else None
            out.append((x["id"],x["flags"],x["length"],tag,v))
        return tuple(out)
    for key in ("links","addresses","routes","rules"):
        expected=[]
        for r in POLICY[key]:
            row=(tuple(r["header"].items()),compiled(r["attributes"]))
            if key=="links":row=(r["name"],tuple(r["index"].items()),tuple(r["sysfs"].items()))+row
            expected.append(row)
        assert getattr(p,key.upper())==tuple(expected)
    assert p.proc_security()==POLICY["proc_security"]


@pytest.mark.parametrize("side",["proc_before","proc_after"])
@pytest.mark.parametrize("row",range(3))
@pytest.mark.parametrize("field",[0,1,2,3,4,5,7,8,9])
def test_proc_route6_each_static_column(side,row,field):
    o=observation();p=getattr(o,side);lines=p.route6.decode().splitlines();tokens=lines[row].split()
    tokens[field]="evil" if field==9 else ("1" if tokens[field][-1]!="1" else "2").rjust(len(tokens[field]),"0")
    lines[row]=" ".join(tokens)
    rejected(replace(o,**{side:replace(p,route6=("\n".join(lines)+"\n").encode())}))

@pytest.mark.parametrize("side",["proc_before","proc_after"])
@pytest.mark.parametrize("row",range(3))
def test_reference_count_only_changes_raw(side,row):
    o=observation();p=getattr(o,side);lines=p.route6.decode().splitlines();tokens=lines[row].split();tokens[6]="ffffffff";lines[row]=" ".join(tokens)
    result=api().validate_network_profile(replace(o,**{side:replace(p,route6=("\n".join(lines)+"\n").encode())}))
    assert result.canonical_security_json==(GOLD/"fixture.security.canonical.json").read_bytes()
    assert result.telemetry_json!=(GOLD/"fixture.telemetry.canonical.json").read_bytes()

@pytest.mark.parametrize("side",["proc_before","proc_after"])
@pytest.mark.parametrize("field",["dev","addresses6","route4","route6","fib_trie"])
@pytest.mark.parametrize("bad",[b"\r",b"\v",b"\f",b"\0",b"\x7f",b"\x80",b"evil\n",b"\n",b" "*65537])
def test_proc_lexical_closure(side,field,bad):
    o=observation();p=getattr(o,side)
    rejected(replace(o,**{side:replace(p,**{field:getattr(p,field)+bad})}))

@pytest.mark.parametrize("row",range(18))
def test_each_trie_indent_rejects(row):
    o=observation();lines=o.proc_before.fib_trie.splitlines();lines[row]=b" "+lines[row]
    rejected(replace(o,proc_before=replace(o.proc_before,fib_trie=b"\n".join(lines)+b"\n")))

@pytest.mark.parametrize("count",[1,3])
def test_sentinel_multiplicity(count):
    o=observation();lines=o.proc_before.route6.splitlines()
    rejected(replace(o,proc_before=replace(o.proc_before,route6=b"\n".join([lines[0]]*count+[lines[1]])+b"\n")))

@pytest.mark.parametrize("slot",range(6))
@pytest.mark.parametrize("mode",["seq","pid","unfinished","cached_type","cached_payload","bool","extra","missing"])
def test_dump_and_cached_closure(slot,mode):
    o=observation();d=o.dumps[slot]
    if mode=="seq":d=replace(d,sequence=d.sequence+1)
    elif mode=="pid":d=replace(d,local_portid=d.local_portid+1)
    elif mode=="unfinished":d=replace(d,datagrams=d.datagrams[:-1])
    elif mode=="cached_type":d=replace(d,parsedmessages=(replace(d.parsedmessages[0],type=True),)+d.parsedmessages[1:])
    elif mode=="cached_payload":d=replace(d,parsedmessages=(replace(d.parsedmessages[0],payload=b"SENSITIVE"),)+d.parsedmessages[1:])
    elif mode=="bool":d=replace(d,local_portid=True)
    elif mode=="extra":return rejected(repack(o,slot,d.parsedmessages+(d.parsedmessages[0],)))
    else:return rejected(repack(o,slot,d.parsedmessages[:-1]))
    rejected(replace(o,dumps=o.dumps[:slot]+(d,)+o.dumps[slot+1:]))

@pytest.mark.parametrize("slot,record,path,spec",[row for row in ROWS if row[3]["rule"]["tag"]=="permanent_identity"])
@pytest.mark.parametrize("offset,mask,valid",[(0,1,False),(0,2,False),(6,1,False),(15,1,False),(1,1,True),(0,4,True)])
def test_permanent_identity(slot,record,path,spec,offset,mask,valid):
    def edit(i,f,p):
        b=bytearray(p);b[offset]^=mask
        return [(i,f,bytes(b))]
    changed=mutate_attr(observation(),slot,record,path,edit)
    if not valid:rejected(changed)
    else:
        result=api().validate_network_profile(changed)
        assert result.security_sha256!="92bc74696861b35950e3dc4d910ba648c1965a0c7e8e589e67ffed69b0f7ccb7"


def test_presentation_order_and_input_nonmutation():
    o=observation();saved=repr(vars(o));changed=o
    for slot,d in enumerate(o.dumps):
        ms=[]
        for m in reversed(d.parsedmessages):
            size=16 if m.type==16 else 8 if m.type==20 else 12
            def reverse(raw):
                return pack_attrs([(a.id,a.flags,a.payload) for a in reversed(parse_attributes(raw))])
            ms.append(replace(m,payload=m.payload[:size]+reverse(m.payload[size:])))
        changed=repack(changed,slot,ms)
    result=api().validate_network_profile(changed)
    assert result.canonical_security_json==(GOLD/"fixture.security.canonical.json").read_bytes()
    assert result.raw_observation_sha256!="3e4a5354304f4fdc67009ceddf0419a80d98f3ef7c2e0bbcfd21f37fcfe756c5"
    assert repr(vars(o))==saved
    assert "linuxkit" not in repr(result) and "canonical_security" not in repr(result)


@pytest.mark.parametrize("field,value",[("kernel_release","6.12.77-linuxkit"),("byteorder","big"),("dumps",[]),("sysfs_before",[]),("namespace_before",None),("proc_before",None)])
def test_outer_types_and_supported_environment(field,value):
    rejected(replace(observation(),**{field:value}))

@pytest.mark.parametrize("name",["device","inode"])
@pytest.mark.parametrize("value",[True,False,0,-1,2**64,"1",None])
def test_namespace_exact_unsigned_type(name,value):
    o=observation()
    rejected(replace(o,namespace_before=replace(o.namespace_before,**{name:value})))


def test_namespace_static_drift():
    o=observation();rejected(replace(o,namespace_after=replace(o.namespace_after,inode=o.namespace_after.inode+1)))

@pytest.mark.parametrize("side",["sysfs_before","sysfs_after"])
@pytest.mark.parametrize("field,value",[("uid",True),("uid",1),("gid",1),("mode",0o600),("file_type","symlink"),("file_type","directory"),("name","SENSITIVE")])
def test_root_control_closure(side,field,value):
    o=observation();es=getattr(o,side);i=next(i for i,e in enumerate(es) if type(e) is api().ControlEntry)
    rejected(replace(o,**{side:es[:i]+(replace(es[i],**{field:value}),)+es[i+1:]}))

@pytest.mark.parametrize("field,value",[("index",True),("index",0),("index",2**32),("iflink",1),("arphrd",999),("flags",129),("operstate","up"),("address_bytes",b"x"),("address_bytes",bytes(65537)),("name","eth0"),("name","SENSITIVE/"),("name","\0")])
def test_sysfs_interface_closure(field,value):
    o=observation();es=o.sysfs_after;i=next(i for i,e in enumerate(es) if e.name=="tunl0")
    rejected(replace(o,sysfs_after=es[:i]+(replace(es[i],**{field:value}),)+es[i+1:]))

@pytest.mark.parametrize("mode",["missing","duplicate","extra"])
def test_sysfs_inventory(mode):
    o=observation();es=o.sysfs_before
    es=es[:-1] if mode=="missing" else es+(es[0],) if mode=="extra" else es[:-1]+(es[0],)
    rejected(replace(o,sysfs_before=es))

@pytest.mark.parametrize("field",["dev","addresses6","route4","route6","fib_trie"])
@pytest.mark.parametrize("mode",["bytes_subclass","str","bytearray","no_final_lf"])
def test_proc_exact_bytes(field,mode):
    class Bytes(bytes):pass
    o=observation();p=o.proc_before;v=getattr(p,field)
    v=Bytes(v) if mode=="bytes_subclass" else v.decode() if mode=="str" else bytearray(v) if mode=="bytearray" else v[:-1]
    rejected(replace(o,proc_before=replace(p,**{field:v})))


def test_recursive_record_subclasses_rejected():
    m=api();o=observation()
    for cls,path,value in [(m.NetworkObservation,None,o),(m.NamespaceIdentity,"namespace_before",o.namespace_before),(m.ProcViews,"proc_before",o.proc_before)]:
        sub=type("Forged",(cls,),{})
        forged=sub(**vars(value))
        rejected(forged if path is None else replace(o,**{path:forged}))
    for e in o.sysfs_before:
        sub=type("Forged",(type(e),),{})
        i=o.sysfs_before.index(e)
        rejected(replace(o,sysfs_before=o.sysfs_before[:i]+(sub(**vars(e)),)+o.sysfs_before[i+1:]))
    d=o.dumps[0];sub=type("Forged",(type(d),),{})
    rejected(replace(o,dumps=(sub(**vars(d)),)+o.dumps[1:]))
    cached=d.parsedmessages[0];sub=type("Forged",(type(cached),),{})
    rejected(replace(o,dumps=(replace(d,parsedmessages=(sub(**vars(cached)),)+d.parsedmessages[1:]),)+o.dumps[1:]))

@pytest.mark.parametrize("slot",range(6))
def test_each_header_field_static(slot):
    o=observation();d=o.dumps[slot]
    # Alter every fixed header byte, keeping all other records and DONE complete.
    for ordinal,m in enumerate(d.parsedmessages):
        size=16 if slot==0 else 8 if slot==1 else 12
        for offset in range(size):
            if slot==0 and offset in (4,5,6,7):continue # indices have explicit relational tests
            b=bytearray(m.payload);b[offset]^=1
            messages=list(d.parsedmessages);messages[ordinal]=replace(m,payload=bytes(b))
            rejected(repack(o,slot,messages))


def test_dynamic_fallback_index_is_not_pinned():
    o=observation();d=o.dumps[0];ms=list(d.parsedmessages);r=parse_message(ms[1]);old=r.index
    b=bytearray(ms[1].payload);struct.pack_into("<i",b,4,12345);ms[1]=replace(ms[1],payload=bytes(b))
    changed=repack(o,0,ms)
    for side in ("sysfs_before","sysfs_after"):
        changed=replace(changed,**{side:tuple(replace(e,index=12345) if type(e) is api().InterfaceEntry and e.index==old else e for e in getattr(changed,side))})
    result=api().validate_network_profile(changed)
    assert result.security_sha256!="92bc74696861b35950e3dc4d910ba648c1965a0c7e8e589e67ffed69b0f7ccb7"


def test_import_and_call_no_external_io(monkeypatch):
    import builtins,io,os,socket,subprocess,time,types,sys
    m=api();source=Path(m.__file__).read_text();o=observation()
    def forbidden(*a,**kw):raise AssertionError("external operation attempted")
    with monkeypatch.context() as mp:
        for owner,name in [(builtins,"open"),(io,"open"),(os,"open"),(socket,"socket"),(subprocess,"Popen"),(time,"time"),(time,"monotonic"),(time,"monotonic_ns")]:mp.setattr(owner,name,forbidden)
        fresh=types.ModuleType(m.__name__+"_pure_test");fresh.__package__=m.__package__;mp.setitem(sys.modules,fresh.__name__,fresh)
        exec(compile(source,"<N3 already-read source>","exec"),fresh.__dict__)
        result=m.validate_network_profile(o)
        assert result.security_sha256=="92bc74696861b35950e3dc4d910ba648c1965a0c7e8e589e67ffed69b0f7ccb7"



def test_complete_raw_preimage_metadata_and_sysfs_golden(monkeypatch):
    m=api();o=observation();original=m.hashlib.sha256;captured=[]
    class Digest:
        def __init__(self, initial):self.chunks=[initial];self.inner=original(initial);captured.append(self)
        def update(self,value):self.chunks.append(value);self.inner.update(value)
        def hexdigest(self):return self.inner.hexdigest()
    monkeypatch.setattr(m.hashlib,"sha256",Digest)
    m.validate_network_profile(o)
    raw=next(b"".join(x.chunks) for x in captured if x.chunks[0]==b"B3-N3-RAW-v1\0")
    assert raw==(GOLD/"fixture.raw.framed.bin").read_bytes()
    offset=len(b"B3-N3-RAW-v1\0");frames=[]
    while offset<len(raw):
        length=int.from_bytes(raw[offset:offset+8],"little");offset+=8
        frames.append(raw[offset:offset+length]);offset+=length
    assert frames[0]==(GOLD/"fixture.metadata.canonical.json").read_bytes()
    assert frames[-2]==(GOLD/"fixture.sysfs-before.canonical.json").read_bytes()
    assert frames[-1]==(GOLD/"fixture.sysfs-after.canonical.json").read_bytes()

@pytest.mark.parametrize("column",range(16))
@pytest.mark.parametrize("bad",["00","01","-1","+1","18446744073709551616","1"*100])
def test_dev_numeric_closure(column,bad):
    o=observation();lines=o.proc_before.dev.decode().splitlines();name,tail=lines[2].split(":");values=tail.split();values[column]=bad;lines[2]=name+":"+" ".join(values)
    rejected(replace(o,proc_before=replace(o.proc_before,dev=("\n".join(lines)+"\n").encode())))

@pytest.mark.parametrize("field",["addresses6","route6"])
@pytest.mark.parametrize("bad",["A","g","+","-","0x"])
def test_hex_lexical_closure(field,bad):
    o=observation();p=o.proc_before;raw=getattr(p,field)
    rejected(replace(o,proc_before=replace(p,**{field:bad.encode()+raw[len(bad):]})))

@pytest.mark.parametrize("field",["dev","addresses6","route4","route6","fib_trie"])
def test_proc_extra_token_and_missing_row(field):
    o=observation();p=o.proc_before;raw=getattr(p,field)
    rejected(replace(o,proc_before=replace(p,**{field:raw[:-1]+b" extra\n"})))
    lines=raw.splitlines(keepends=True)
    rejected(replace(o,proc_before=replace(p,**{field:b"".join(lines[:-1])})))


def test_exact_output_dataclasses_frozen_and_repr_disabled():
    m=api();o=observation();r=m.validate_network_profile(o)
    from dataclasses import FrozenInstanceError
    for obj in [o,o.namespace_before,o.proc_before,*o.sysfs_before,r]:
        assert type(obj).__dataclass_params__.frozen
        assert type(obj).__dataclass_params__.repr is False
        with pytest.raises(FrozenInstanceError):setattr(obj,next(iter(vars(obj))),None)
    assert r.observed_interfaces==tuple(x["name"] for x in POLICY["links"])
    assert r.active_interfaces==("lo",)

@pytest.mark.parametrize("slot",range(6))
@pytest.mark.parametrize("mode",["packet_subclass","packet_oversize","packet_count","cache_tuple","cache_payload_subclass","bool_sequence","bool_family"])
def test_transport_strict_types_bounds(slot,mode):
    class Bytes(bytes):pass
    o=observation();d=o.dumps[slot]
    if mode=="packet_subclass":d=replace(d,datagrams=(Bytes(d.datagrams[0]),)+d.datagrams[1:])
    elif mode=="packet_oversize":d=replace(d,datagrams=(bytes(65537),)+d.datagrams[1:])
    elif mode=="packet_count":d=replace(d,datagrams=(b"x",)*257)
    elif mode=="cache_tuple":d=replace(d,parsedmessages=list(d.parsedmessages))
    elif mode=="cache_payload_subclass":d=replace(d,parsedmessages=(replace(d.parsedmessages[0],payload=Bytes(d.parsedmessages[0].payload)),)+d.parsedmessages[1:])
    elif mode=="bool_sequence":d=replace(d,sequence=True)
    else:d=replace(d,family=False)
    rejected(replace(o,dumps=o.dumps[:slot]+(d,)+o.dumps[slot+1:]))


def test_all_nested_attribute_order_canonicalizes():
    o=observation();changed=o
    for slot,d in enumerate(o.dumps):
        messages=[]
        for record,m in enumerate(d.parsedmessages):
            children={path for s,r,path,a in ROWS if s==slot and r==record and a["rule"]["tag"]=="children"}
            def reorder(raw,path=()):
                attrs=[]
                for a in reversed(parse_attributes(raw)):
                    p=path+(a.id,)
                    attrs.append((a.id,a.flags,reorder(a.payload,p) if p in children else a.payload))
                return pack_attrs(attrs)
            size=16 if m.type==16 else 8 if m.type==20 else 12
            messages.append(replace(m,payload=m.payload[:size]+reorder(m.payload[size:])))
        changed=repack(changed,slot,messages)
    result=api().validate_network_profile(changed)
    assert result.canonical_security_json==(GOLD/"fixture.security.canonical.json").read_bytes()


@pytest.mark.parametrize("slot,ident",[(0,10),(0,19),(0,28),(0,37),(0,38),(0,53),(2,5),(2,9),(2,18),(2,21),(2,30),(3,5),(3,9),(4,10),(4,11),(4,19),(4,20),(4,23),(4,24)])
def test_explicit_external_relations_gateway_multipath_rule_selectors(slot,ident):
    o=observation();ms=list(o.dumps[slot].parsedmessages);m=ms[0];size=16 if slot==0 else 12
    attrs=[(a.id,a.flags,a.payload) for a in parse_attributes(m.payload[size:])]
    assert ident not in {a[0] for a in attrs}
    attrs.append((ident,0,bytes(4)))
    ms[0]=replace(m,payload=m.payload[:size]+pack_attrs(attrs))
    rejected(repack(o,slot,ms))


def test_proc_and_sysfs_source_order_preserved_outside_security():
    o=observation();p=o.proc_before;dev=p.dev.splitlines(keepends=True)
    p=replace(p,dev=b"".join(dev[:2]+list(reversed(dev[2:]))),route6=b"".join(reversed(p.route6.splitlines(keepends=True))))
    changed=replace(o,proc_before=p,sysfs_before=tuple(reversed(o.sysfs_before)))
    r=api().validate_network_profile(changed)
    assert r.canonical_security_json==(GOLD/"fixture.security.canonical.json").read_bytes()
    assert json.loads(r.telemetry_json)["proc_before"]["dev"][0]["name"]=="ip6gre0"
    assert r.raw_observation_sha256!="3e4a5354304f4fdc67009ceddf0419a80d98f3ef7c2e0bbcfd21f37fcfe756c5"
