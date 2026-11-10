"""Closed v3 raw DATA only; no live observation."""
import copy
import importlib.util
import dataclasses
import pytest
from test_media_backup_b3_network_fixtures import observation, wire_fixture, PROFILE, SECURITY, RAW
from platform_service.media_backup_b3.contracts import B3Error

def api():
    assert importlib.util.find_spec("platform_service.media_backup_b3.network_wire") is not None, "complete raw wire adapter required"
    from platform_service.media_backup_b3 import network_wire
    return network_wire

def test_wire_adapter_required():
    m=api(); o=observation(); wire=wire_fixture()
    assert m.encode_network_observation(o)==wire
    assert m.decode_network_observation(wire)==o
    from platform_service.media_backup_b3.network_profile import validate_network_profile
    r=validate_network_profile(m.decode_network_observation(wire))
    assert (r.profile_sha256,r.security_sha256,r.raw_observation_sha256)==(PROFILE,SECURITY,RAW)

def changes(w):
    yield {**w,"extra":1}
    for k in w:
        v=copy.deepcopy(w);del v[k];yield v
    for side in ("namespace_before","namespace_after"):
        for key in ("device","inode"):
            v=copy.deepcopy(w);v[side][key]=True;yield v
    for side in ("proc_before","proc_after"):
        for key in w[side]:
            for val in ("A0","a","00 ","0x00",True,"00"*65537):
                v=copy.deepcopy(w);v[side][key]=val;yield v
    for side in ("sysfs_before","sysfs_after"):
        for i,row in enumerate(w[side]):
            for key in row:
                v=copy.deepcopy(w);v[side][i][key]=True;yield v
            v=copy.deepcopy(w);v[side][i]["extra"]=1;yield v
        v=copy.deepcopy(w);v[side]*=2;yield v
    for i,d in enumerate(w["dumps"]):
        for key in d:
            v=copy.deepcopy(w);v["dumps"][i][key]=True;yield v
        for val in (["A0"],["0"],["00"*65537],[],["00"]*257):
            v=copy.deepcopy(w);v["dumps"][i]["datagrams"]=val;yield v
        v=copy.deepcopy(w);v["dumps"][i]["parsedmessages"]=[];yield v
    v=copy.deepcopy(w);v["dumps"]*=2;yield v

def test_closed_types_hex_bounds_and_semantics():
    m=api()
    for bad in changes(wire_fixture()):
        with pytest.raises(B3Error) as ex: m.decode_network_observation(bad)
        assert str(ex.value)=="invalid B3 network wire"
        assert ex.value.__cause__ is None and ex.value.__suppress_context__

def test_encoding_revalidates_forged_cache():
    m=api();o=observation()
    bad=dataclasses.replace(o,dumps=(dataclasses.replace(o.dumps[0],parsedmessages=()),)+o.dumps[1:])
    with pytest.raises(B3Error):m.encode_network_observation(bad)

def test_raw_source_order_preserved_and_preflight_before_record_construction(monkeypatch):
    m=api();w=wire_fixture()
    for side in ("sysfs_before","sysfs_after"):w[side].reverse()
    assert m.encode_network_observation(m.decode_network_observation(w))==w
    # A late invalid field must fail before even the first record construction.
    w["dumps"][-1]["datagrams"]=["00"*65537]
    from unittest import mock
    constructor=mock.Mock(side_effect=AssertionError("record allocated before full preflight"))
    monkeypatch.setattr(m.profile,"NamespaceIdentity",constructor)
    with pytest.raises(B3Error,match="invalid B3 network wire"):m.decode_network_observation(w)
    constructor.assert_not_called()


def test_aggregate_limit_before_records_or_n1_parsing(monkeypatch):
    from unittest import mock
    m=api();w=wire_fixture()
    w["dumps"][0]["datagrams"]=["00"*65536]*129
    parser=mock.Mock(side_effect=AssertionError("N1 reached before aggregate cap"))
    monkeypatch.setattr(m,"parse_dump",parser)
    with pytest.raises(B3Error):m.decode_network_observation(w)
    parser.assert_not_called()

@pytest.mark.parametrize("kind",["dev","route6"])
def test_independently_varied_permitted_counters_preserve_raw_bytes(kind):
    from test_media_backup_b3_network_fixtures import varied_observation
    from platform_service.media_backup_b3.network_profile import validate_network_profile
    m=api();o=varied_observation(kind);decoded=m.decode_network_observation(m.encode_network_observation(o))
    assert decoded==o
    result=validate_network_profile(decoded)
    assert result.security_sha256==SECURITY
    assert result.raw_observation_sha256!=RAW

@pytest.mark.parametrize("location",["root","sysfs_tag"])
def test_i1_nonbuiltin_keys_reject_without_equality_dispatch(location):
    from test_media_backup_b3_network_fixtures import inert_key_probe
    w=wire_fixture()
    if location=="root":w,calls=inert_key_probe(w,"kernel_release")
    else:w["sysfs_before"][0],calls=inert_key_probe(w["sysfs_before"][0],"tag")
    with pytest.raises(B3Error,match="invalid B3 network wire"):api().decode_network_observation(w)
    assert calls==[], "non-built-in key equality dispatched before rejection"
