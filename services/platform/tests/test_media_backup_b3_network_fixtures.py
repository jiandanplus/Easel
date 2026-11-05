"""Unit-test fixture baseline ONLY; never live qualification or host provenance."""
import base64
import json
from pathlib import Path
BASE = Path(__file__).parent / "fixtures/media_backup_b3/network"
GOLD = BASE / "profile-v1"
PROFILE = "b803b4094d8254da45b7ca9c4666d2875794d18532fc721c87488bd64c245582"
SECURITY = "92bc74696861b35950e3dc4d910ba648c1965a0c7e8e589e67ffed69b0f7ccb7"
RAW = "3e4a5354304f4fdc67009ceddf0419a80d98f3ef7c2e0bbcfd21f37fcfe756c5"
def observation():
    from platform_service.media_backup_b3 import network_profile as m
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


def wire_fixture():
    f=json.loads((BASE / "linuxkit-6.12.76-same-namespace-v1.public.json").read_bytes())
    def sysfs(side):
        rows=[]
        for name,v in f[side]["sysfs"].items():
            if v["kind"]=="control": rows.append(dict(tag="control",name=name,uid=v["uid"],gid=v["gid"],mode=v["mode"],file_type="regular"))
            else: rows.append(dict(tag="interface",name=name,index=int(v["ifindex"]),iflink=int(v["iflink"]),arphrd=int(v["type"]),flags=int(v["flags"],16),operstate=v["operstate"],address_hex=v["address"].replace(":","")))
        return rows
    return dict(kernel_release=f["kernel_release"],byteorder="little",namespace_before=f["before"]["netns"],namespace_after=f["after"]["netns"],sysfs_before=sysfs("before"),sysfs_after=sysfs("after"),proc_before={k:v.encode().hex() for k,v in f["before"]["proc"].items()},proc_after={k:v.encode().hex() for k,v in f["after"]["proc"].items()},dumps=[dict(**{k:d[k] for k in ("request_type","family","sequence","local_portid")},datagrams=[base64.b64decode(p).hex() for p in d["packets"]]) for d in f["dumps"]])

def network_evidence():
    snapshot=json.loads((GOLD / "fixture.security.canonical.json").read_bytes())
    return dict(network_before=wire_fixture(),network_after=wire_fixture(),network_profile_sha256=PROFILE,network_security_sha256=SECURITY,network_security_snapshot=snapshot,observed_interfaces=[x["name"] for x in snapshot["links"]],active_interfaces=["lo"],sysfs_controls=snapshot["sysfs_controls"],network_before_raw_sha256=RAW,network_after_raw_sha256=RAW)

def varied_observation(kind="dev"):
    """Independent permissible /proc counter variation; permanent state unchanged."""
    from dataclasses import replace
    o=observation()
    if kind=="route6":
        rows=o.proc_after.route6.decode().splitlines()
        tokens=rows[0].split();tokens[6]="00000007"
        rows[0]=" ".join(tokens)
        return replace(o,proc_after=replace(o.proc_after,route6=("\n".join(rows)+"\n").encode()))
    lines=o.proc_after.dev.decode().splitlines()
    name,values=lines[2].split(":")
    tokens=values.split();tokens[0]="17" if tokens[0]!="17" else "18"
    lines[2]=name+": "+" ".join(tokens)
    return replace(o,proc_after=replace(o.proc_after,dev=("\n".join(lines)+"\n").encode()))

def inert_key_probe(value, key):
    """A str-subclass key with an inert equality counter; no native callbacks."""
    calls=[]
    class Key(str):
        __hash__=str.__hash__
        def __eq__(self,other):
            calls.append(1)
            return str.__eq__(self,other)
    changed=dict(value)
    item=changed.pop(key)
    changed[Key(key)]=item
    calls.clear()
    return changed,calls
