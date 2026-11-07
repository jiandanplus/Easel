"""Offline behavioral contract: no production credentials, postgres or provider I/O."""
import asyncio
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import os
import stat
import sys
from types import SimpleNamespace

import pytest

SCRIPT=Path(__file__).parents[1]/"scripts/media_restore_window.py"
NONCE="a"*32


def test_window_operator_implementation_exists():
    assert SCRIPT.exists(), "window operator implementation is missing"


def load():
    assert SCRIPT.exists(), "window operator implementation is missing"
    spec=importlib.util.spec_from_file_location("media_restore_window_under_test",SCRIPT)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


class Backend:
    def __init__(self, module, directory):
        self.module=module;self.directory=directory;self.calls=[];self.fail_at=None;self.drift=False
        self.binding={"release":module.RELEASE,"source_sha256":"b"*64,"owner_locator":{"path":"private-owner","keys":["dsn"],"file_sha256":"c"*64}}
        self.value={"models":[{"model_id":name,"enabled":True,"definition_sha256":"d"*64} for name in module.MODELS],
            "counts":dict(module.EXPECTED_COUNTS),"finance":{"credit_lots":{"rows":1,"sha256":"e"*64}},
            "wallet":dict(module.EXPECTED_WALLET),"media_sha256":"f"*64,"files_sha256":"1"*64,"migrations_sha256":"2"*64}
        self.snapshots=0
    def verify(self, locator=None):
        if locator is not None:assert locator==self.binding["owner_locator"]
        return deepcopy(self.binding)
    def snapshot(self):
        self.snapshots+=1
        return deepcopy(self.value)
    async def set_enabled(self, model, enabled, actor):
        assert (self.directory/("window-"+NONCE+"-"+("resume" if enabled else "pause")+"-intent-private.json")).exists(), "intent must be durable before mutation"
        assert model in self.module.MODELS
        assert actor=="media-restore-window-"+NONCE
        self.calls.append((model,enabled,actor))
        if len(self.calls)==self.fail_at:raise RuntimeError("postgres://user:DO-NOT-LEAK@host/db")
        for row in self.value["models"]:
            if row["model_id"]==model:row["enabled"]=enabled
        if self.drift:self.value["finance"]["credit_lots"]["sha256"]="9"*64
    async def close(self):pass


@pytest.fixture
def setup(tmp_path):
    m=load();tmp_path.chmod(0o700);b=Backend(m,tmp_path)
    return m,b,tmp_path


def create(m,b,p):
    receipt=m.make_plan(b,p,NONCE)
    return Path(receipt["plan_path"]),receipt["plan_sha256"],receipt


def run(m,b,path,sha,phase):return asyncio.run(m.transition(b,path,sha,phase))


def test_plan_is_readonly_private_exclusive_and_safe(setup):
    m,b,p=setup;path,sha,receipt=create(m,b,p)
    assert stat.S_IMODE(path.stat().st_mode)==0o600
    assert hashlib.sha256(path.read_bytes()).hexdigest()==sha
    assert b.calls==[]
    text=json.dumps(receipt)
    assert "private-owner" not in text and "credit_lots" not in text and "dsn" not in text
    plan=json.loads(path.read_bytes())
    assert plan["tool_sha256"]==hashlib.sha256(SCRIPT.read_bytes()).hexdigest()
    assert plan["original"]["finance"]==b.value["finance"]
    with pytest.raises(m.Rejected):create(m,b,p)


@pytest.mark.parametrize("nonce",["../escape","a"*31,"A"*32,""])
def test_nonce_rejected(setup,nonce):
    m,b,p=setup
    with pytest.raises(m.Rejected):m.make_plan(b,p,nonce)
    assert list(p.iterdir())==[]


def test_directory_permissions_and_plan_symlink_rejected(setup):
    m,b,p=setup;p.chmod(0o755)
    with pytest.raises(m.Rejected):create(m,b,p)
    p.chmod(0o700);path,sha,_=create(m,b,p)
    target=p/"link";target.symlink_to(path)
    with pytest.raises(m.Rejected):run(m,b,target,sha,"pause")
    path.chmod(0o644)
    with pytest.raises(m.Rejected):run(m,b,path,sha,"pause")
    assert b.calls==[]


@pytest.mark.parametrize("field",["counts","wallet","models"])
def test_plan_rejects_wrong_baseline(setup,field):
    m,b,p=setup
    if field=="models":b.value[field].append({"model_id":"unrelated","enabled":True,"definition_sha256":"d"*64})
    elif field=="counts":b.value[field]["active_jobs"]=1
    else:b.value[field]["held"]=0
    with pytest.raises(m.Rejected):create(m,b,p)
    assert b.calls==[] and list(p.iterdir())==[]


@pytest.mark.parametrize("change",["hash","self","finance","source","media","migrations"])
def test_pause_revalidates_all_reviewed_facts_before_write(setup,change):
    m,b,p=setup;path,sha,_=create(m,b,p)
    if change=="hash":sha="0"*64
    elif change=="self":
        plan=json.loads(path.read_bytes());plan["tool_sha256"]="0"*64
        path.write_bytes(m.canonical(plan));sha=m.digest(path.read_bytes())
    elif change=="finance":b.value["finance"]["credit_lots"]["sha256"]="0"*64
    elif change=="source":b.binding["source_sha256"]="0"*64
    elif change=="media":b.value["media_sha256"]="0"*64
    else:b.value["migrations_sha256"]="0"*64
    with pytest.raises(m.Rejected):run(m,b,path,sha,"pause")
    assert b.calls==[]


def test_pause_then_resume_only_exact_models_and_repeat_is_readonly(setup):
    m,b,p=setup;path,sha,_=create(m,b,p)
    receipt=run(m,b,path,sha,"pause")
    assert receipt["success"] is True and receipt["financial_unchanged"] is True
    assert len(b.calls)==2 and all(not row["enabled"] for row in b.value["models"])
    files={f.name:f.read_bytes() for f in p.iterdir()}
    repeated=run(m,b,path,sha,"pause")
    assert repeated["repeated"] is True and len(b.calls)==2
    assert files=={f.name:f.read_bytes() for f in p.iterdir()}
    receipt=run(m,b,path,sha,"resume")
    assert receipt["success"] is True and len(b.calls)==4
    assert all(row["enabled"] for row in b.value["models"])
    run(m,b,path,sha,"resume");assert len(b.calls)==4


def test_different_plan_nonces_share_one_availability_window_lock(setup):
    m,b,p=setup;a,asha,_=create(m,b,p)
    second=m.make_plan(b,p,"b"*32);other=Path(second['plan_path']);other_sha=second['plan_sha256']

    async def scenario():
        entered=asyncio.Event();release=asyncio.Event();arrivals=[]

        async def set_enabled(model,enabled,actor):
            nonce=actor.removeprefix('media-restore-window-')
            assert (p/('window-'+nonce+'-'+('resume' if enabled else 'pause')+'-intent-private.json')).is_file()
            if model==m.MODELS[0] and not enabled:
                arrivals.append(actor);entered.set();await release.wait()
            b.calls.append((model,enabled,actor))
            for row in b.value['models']:
                if row['model_id']==model:row['enabled']=enabled

        b.set_enabled=set_enabled
        first=asyncio.create_task(m.transition(b,a,asha,'pause'))
        await asyncio.wait_for(entered.wait(),1)
        competing=asyncio.create_task(m.transition(b,other,other_sha,'pause'))
        await asyncio.sleep(0)
        try:
            assert len(arrivals)==1, 'a second nonce reached domain writes inside the first availability window'
            assert competing.done() and isinstance(competing.exception(),m.Rejected)
            assert not list(p.glob('window-'+('b'*32)+'-pause-*'))
        finally:
            release.set()
            results=await asyncio.gather(first,competing,return_exceptions=True)
        assert results[0]['success'] and isinstance(results[1],m.Rejected)
        with pytest.raises(m.Rejected):await m.transition(b,other,other_sha,'resume')
        assert all(not row['enabled'] for row in b.value['models'])
        assert (await m.transition(b,a,asha,'resume'))['success']
        assert all(row['enabled'] for row in b.value['models'])

    asyncio.run(scenario())


def test_partial_pause_failure_resume_restores_only_changed_model_and_sanitizes(setup):
    m,b,p=setup;path,sha,_=create(m,b,p);b.fail_at=2
    receipt=run(m,b,path,sha,"pause")
    assert receipt["success"] is False
    assert "DO-NOT-LEAK" not in json.dumps(receipt)
    assert all("DO-NOT-LEAK" not in f.read_text() for f in p.iterdir())
    assert [r["enabled"] for r in b.value["models"]]==[False,True]
    b.fail_at=None;receipt=run(m,b,path,sha,"resume")
    assert receipt["success"] is True and len(b.calls)==3
    assert b.calls[-1][0]==m.MODELS[0]


def test_pause_finance_drift_fails_but_resume_recovers_and_records_drift(setup):
    m,b,p=setup;path,sha,_=create(m,b,p);b.drift=True
    result=run(m,b,path,sha,"pause")
    assert result["success"] is False and result["financial_unchanged"] is False
    result=run(m,b,path,sha,"resume")
    assert result["success"] is True and result["financial_unchanged"] is False
    assert all(row["enabled"] for row in b.value["models"])


def test_status_reports_actual_state_without_mutations_and_resume_requires_pause_intent(setup):
    m,b,p=setup;path,sha,_=create(m,b,p)
    with pytest.raises(m.Rejected):run(m,b,path,sha,"resume")
    status=run(m,b,path,sha,"status")
    assert status["state"]=="planned" and b.calls==[]
    b.fail_at=1;run(m,b,path,sha,"pause")
    count=len(b.calls);status=run(m,b,path,sha,"pause")
    assert status["success"] is False and status["repeated"] is True and len(b.calls)==count
    assert status["models_enabled"]==2


def test_unfinished_intent_is_unknown_and_never_replayed(setup):
    m,b,p=setup;path,sha,_=create(m,b,p)
    intent=p/("window-"+NONCE+"-pause-intent-private.json")
    m.write_private(intent,{"phase":"pause","plan_sha256":sha,"tool_sha256":m.tool_sha(),"nonce":NONCE})
    result=run(m,b,path,sha,"pause")
    assert result["state"]=="pause_incomplete" and result["success"] is False and b.calls==[]


def test_write_private_fsyncs_file_and_directory_and_never_overwrites(setup,monkeypatch):
    m,b,p=setup;real=m.os.fsync;calls=[]
    monkeypatch.setattr(m.os,"fsync",lambda fd:(calls.append(stat.S_ISDIR(os.fstat(fd).st_mode)),real(fd))[1])
    m.write_private(p/"intent",{"safe":True})
    assert calls==[False,True]
    with pytest.raises(m.Rejected):m.write_private(p/"intent",{"safe":False})


def test_production_adapter_uses_deployed_domain_api_and_owner_connection_only(setup,monkeypatch):
    m,b,p=setup;calls=[]
    class DB:
        def __init__(self,dsn,*,schema):calls.append(("Database",dsn,schema))
        async def open(self):calls.append(("open",))
        async def close(self):calls.append(("close",))
    class Service:
        def __init__(self,db,identity):calls.append(("MediaService",))
        async def set_enabled(self,model,enabled,actor):calls.append(("set_enabled",model,enabled,actor))
    class Actor:
        def __init__(self,id,capabilities):self.id=id;self.capabilities=capabilities
    for name,module in {"platform_service.db":SimpleNamespace(Database=DB),"platform_service.media.core":SimpleNamespace(MediaService=Service),
        "platform_service.contracts":SimpleNamespace(OperatorIdentity=Actor),"platform_service.identity":SimpleNamespace(IdentityService=lambda db,key:None)}.items():
        monkeypatch.setitem(sys.modules,name,module)
    adapter=m.ProductionBackend();adapter.dsn="OWNER-DSN";adapter.schema="owned-schema"
    async def apply():
        await adapter.set_enabled(m.MODELS[0],False,"media-restore-window-"+NONCE);await adapter.close()
    asyncio.run(apply())
    assert calls[0]==("Database","OWNER-DSN","owned-schema")
    entry=next(row for row in calls if row[0]=="set_enabled")
    assert entry[2] is False and entry[3].id=="media-restore-window-"+NONCE
    assert entry[3].capabilities==frozenset({"media:publish"})
    assert calls[-1]==("close",)


def test_real_blob_boundary_rejects_extra_file_symlink_and_wrong_digest(setup,monkeypatch):
    m,b,p=setup;storage=p/"storage";storage.mkdir(mode=0o700)
    raw=b"verified-local-blob";key="3"*32;blob=storage/key;blob.write_bytes(raw);blob.chmod(0o600)
    owner=SimpleNamespace(pw_uid=os.geteuid(),pw_gid=os.getegid())
    monkeypatch.setattr(m,"STORAGE",storage);monkeypatch.setattr(m.pwd,"getpwnam",lambda _:owner)
    rows=[{"storage_key":key,"size":len(raw),"sha256":hashlib.sha256(raw).hexdigest(),"state":"attached"},
        {"storage_key":"4"*32,"size":10,"sha256":"5"*64,"state":"staged"}]
    adapter=m.ProductionBackend();files,missing=adapter.files_snapshot(rows)
    assert len(files)==1 and missing==1
    assert adapter.files_snapshot(rows)==(files,missing)
    extra=storage/"unexpected";extra.write_bytes(b"x")
    with pytest.raises(m.Rejected):adapter.files_snapshot(rows)
    # This is test fixture manipulation only; no cleanup logic exists in the operator.
    extra.rename(p/"outside-extra")
    rows[0]["sha256"]="0"*64
    with pytest.raises(m.Rejected):adapter.files_snapshot(rows)
    rows[0]["sha256"]=hashlib.sha256(raw).hexdigest()
    blob.rename(p/"outside-blob");blob.symlink_to(p/"outside-blob")
    with pytest.raises(OSError):adapter.files_snapshot(rows)


def test_production_snapshot_only_reads_and_hashes_financial_and_media_rows(setup,monkeypatch):
    m,b,p=setup;statements=[];calls=[]
    migrations=p/"migrations";migrations.mkdir()
    expected=[]
    for i in range(1,17):
        path=migrations/(str(i).zfill(3)+"_test.sql");path.write_text("-- local test migration "+str(i))
        expected.append({"version":path.name,"checksum":m.digest(path.read_bytes())})
    class Result:
        def __init__(self,value):self.value=value
        def fetchone(self):return self.value
        def fetchall(self):return self.value
        def __iter__(self):return iter(self.value)
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def execute(self,query):
            text=query if isinstance(query,str) else query.as_string();statements.append(text)
            assert text.split()[0]=="SELECT", "production snapshot must be read-only"
            if "LEFT JOIN media_model_states" in text:
                return Result([{"model_id":name,"enabled":True,"definition":{"id":name}} for name in m.MODELS])
            if "tablename FROM pg_tables" in text:return Result([{"tablename":name} for name in ["media_invocations","media_jobs","media_models","media_storage_budgets"]])
            if "to_jsonb(t)::text" in text:return Result([{"body":"{\"local\": true}"}])
            if "FROM credit_lots" in text:return Result(dict(m.EXPECTED_WALLET))
            if "count(*) FROM media_jobs" in text:return Result({k:v for k,v in m.EXPECTED_COUNTS.items() if k!="missing_staged"})
            if "storage_key,sha256,size,state" in text:return Result([])
            if "schema_migrations" in text:return Result(expected)
            raise AssertionError(text)
    def connect(dsn,schema,*,readonly=True):
        calls.append((dsn,schema,readonly));return Connection()
    adapter=m.ProductionBackend();adapter.dsn="fake-owner";adapter.schema="fake-schema"
    adapter.prep=SimpleNamespace(connect_owner=connect,financial_snapshot=lambda c:{"credit_lots":{"rows":1,"sha256":"c"*64}})
    adapter.files_snapshot=lambda rows:({"local":True},1)
    monkeypatch.setitem(sys.modules,"platform_service.db",SimpleNamespace(MIGRATIONS_DIR=migrations))
    snapshot=adapter.snapshot()
    assert calls==[("fake-owner","fake-schema",True)]
    assert snapshot["counts"]==m.EXPECTED_COUNTS and snapshot["wallet"]==m.EXPECTED_WALLET
    assert set(snapshot["finance"])=={"credit_lots","media_invocations","media_jobs","media_storage_budgets"}
    assert all(not any(word in q.upper() for word in ("UPDATE ","DELETE ","INSERT ","ALTER ")) for q in statements)


def test_nonblocking_plan_lock_prevents_overlapping_mutations(setup):
    m,b,p=setup;path,sha,_=create(m,b,p)
    with m.operation_lock(path):
        with pytest.raises(m.Rejected):run(m,b,path,sha,"pause")
    assert b.calls==[]


def test_resume_restores_availability_even_if_counts_or_wallet_drift(setup):
    m,b,p=setup;path,sha,_=create(m,b,p);run(m,b,path,sha,"pause")
    b.value["wallet"]["available"]-=17;b.value["wallet"]["spent"]+=17
    b.value["counts"]["active_jobs"]=1
    b.value["finance"]["credit_lots"]["sha256"]="8"*64
    result=run(m,b,path,sha,"resume")
    assert result["success"] is True and result["financial_unchanged"] is False
    assert all(row["enabled"] for row in b.value["models"])


def test_forged_operation_receipt_does_not_trigger_write(setup):
    m,b,p=setup;path,sha,_=create(m,b,p)
    m.write_private(p/("window-"+NONCE+"-pause-intent-private.json"),
        {"phase":"pause","nonce":NONCE,"tool_sha256":m.tool_sha(),"plan_sha256":"0"*64})
    with pytest.raises(m.Rejected):run(m,b,path,sha,"resume")
    assert b.calls==[]


def test_help_is_available_without_touching_production_state():
    import subprocess
    result=subprocess.run([sys.executable,"-B",str(SCRIPT),"--help"],capture_output=True,text=True,check=False)
    assert result.returncode==0 and "{plan,pause,resume,status}" in result.stdout
    assert result.stderr==""


def test_repeated_pause_does_not_claim_current_window_success_after_drift(setup):
    m,b,p=setup;path,sha,_=create(m,b,p)
    assert run(m,b,path,sha,"pause")["success"] is True
    b.value["counts"]["active_jobs"]=1
    repeated=run(m,b,path,sha,"pause")
    assert repeated["success"] is False and repeated["repeated"] is True
    assert len(b.calls)==2
