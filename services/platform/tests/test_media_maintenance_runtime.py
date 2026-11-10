"""Controller phase/read-only verification and trusted runtime identity contracts."""
import asyncio,hashlib,importlib,json,os,subprocess,sys
from dataclasses import replace
from pathlib import Path
from uuid import uuid4
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/"src"))
from b2_owned_pg import b2_pg
from test_media_maintenance_pg import installed,local_locks


def identity_api():
    try:return importlib.import_module("platform_service.media_maintenance.identity")
    except ModuleNotFoundError:pytest.fail("OS-derived maintenance identity collector missing")


def test_identity_is_os_derived_hashed_and_process_bound():
    lib=identity_api();first=lib.host_identity();assert first==lib.host_identity()
    assert type(first) is tuple and len(first)==3 and all(type(x) is str and x for x in first)
    assert all(len(x)<128 for x in first)
    worker=lib.worker_identity();assert (worker.host_id,worker.boot_id,worker.process_start)==first
    assert worker.worker_id!=worker.generation
    code="import json;from platform_service.media_maintenance.identity import host_identity;print(json.dumps(host_identity()))"
    run=subprocess.run([sys.executable,"-B","-c",code],env={**os.environ,"PYTHONPATH":str(Path(__file__).parents[1]/"src")},capture_output=True,check=True,timeout=5)
    child=tuple(json.loads(run.stdout));assert child[:2]==first[:2] and child[2]!=first[2]
    assert all("Darwin" in x for x in first) if sys.platform=="darwin" else all("Linux" in x for x in first)


@pytest.mark.asyncio
async def test_phase_budget_starts_at_real_exclusive_not_rpc_receipt(b2_pg,tmp_path):
    control=await installed(b2_pg);fd,locks=local_locks(tmp_path);clock=[1_000_000_000]
    class DelayedReceipt(control.DatabaseControl):
        async def _rpc(self,name,args=(),**kw):
            result=await super()._rpc(name,args,**kw)
            if name=="transition" and args[4]=="CAPTURING":clock[0]+=40_000_000_000
            return result
    owner=DelayedReceipt(b2_pg.factory("controller"),locks=locks,clock_ns=lambda:clock[0])
    identity=dict(host_id="synthetic-host",boot_id="synthetic-boot",process_start="1:2",control_dev=locks.inode[0],control_ino=locks.inode[1],source_dev=os.fstat(fd).st_dev,source_ino=os.fstat(fd).st_ino,generation_digest=await owner.generation_digest(),configuration_state={})
    try:
        binding=await owner.begin_window(identity);clock[0]+=20_000_000_000
        holder=await owner.acquire_window_exclusive(binding)
        assert hasattr(owner,"phase_deadline_ns"),"actual phase deadline accessor missing"
        assert owner.phase_deadline_ns(binding)==141_000_000_000
        clock[0]=141_000_000_001
        with pytest.raises(control.MaintenanceError):owner.phase_deadline_ns(binding)
    finally:
        for holder in owner._holders.values():holder.close()
        locks.close();os.close(fd)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure",[None,"wrongstate","epoch","binding","generation","holder","pid","deadline","unknown"])
async def test_verify_window_is_exact_read_only_and_never_clears_unknown(b2_pg,tmp_path,failure):
    from psycopg.types.json import Jsonb
    control=await installed(b2_pg);fd,locks=local_locks(tmp_path);clock=[1_000_000_000]
    owner=control.DatabaseControl(b2_pg.factory("controller"),locks=locks,clock_ns=lambda:clock[0])
    identity=dict(host_id="synthetic-host",boot_id="synthetic-boot",process_start="1:2",control_dev=locks.inode[0],control_ino=locks.inode[1],source_dev=os.fstat(fd).st_dev,source_ino=os.fstat(fd).st_ino,generation_digest=await owner.generation_digest(),configuration_state={})
    try:
        binding=await owner.begin_window(identity)
        assert hasattr(owner,"verify_window"),"exact read-only controller verification missing"
        assert await owner.verify_window(binding,expected_state="DRAINING") is None
        holder=await owner.acquire_window_exclusive(binding);supplied=binding;expected="CAPTURING"
        if failure=="wrongstate":expected="VERIFYING"
        elif failure=="epoch":supplied=replace(binding,epoch=binding.epoch+1)
        elif failure=="binding":supplied=replace(binding,source_ino=binding.source_ino+1)
        elif failure=="generation":
            async with b2_pg.connection("owner") as c:
                await c.execute("INSERT INTO easel_backup_control.workers(worker_id,generation,identity,token_hash) VALUES(%s,%s,%s,%s)",(str(uuid4()),str(uuid4()),Jsonb({"synthetic":"new generation"}),"a"*64));await c.commit()
        elif failure=="holder":holder.close()
        elif failure=="pid":owner._pid=-1
        elif failure=="deadline":clock[0]+=121_000_000_000
        elif failure=="unknown":owner._windows[binding.window_id]["unknown"]=True
        async with b2_pg.connection("owner") as c:
            before=(await(await c.execute("SELECT count(*) FROM easel_backup_control.audit")).fetchone())[0]
        if failure is None:
            assert await owner.verify_window(supplied,expected_state=expected) is None
            assert owner.pending_window_ids==frozenset()
        else:
            with pytest.raises(control.MaintenanceError):await owner.verify_window(supplied,expected_state=expected)
            assert owner.pending_window_ids=={binding.window_id}
            with pytest.raises(control.MaintenanceError):await owner.verify_window(binding,expected_state="CAPTURING")
        async with b2_pg.connection("owner") as c:
            assert (await(await c.execute("SELECT state FROM easel_backup_control.control")).fetchone())[0]=="CAPTURING"
            assert (await(await c.execute("SELECT count(*) FROM easel_backup_control.audit")).fetchone())[0]==before
    finally:
        for holder in owner._holders.values():holder.close()
        locks.close();os.close(fd)



@pytest.mark.asyncio
@pytest.mark.parametrize('failure',[None,'expired','unknown','wrongbinding','after_exclusive'])
async def test_drain_deadline_is_original_readonly_and_never_rebased(b2_pg,tmp_path,failure):
    control=await installed(b2_pg);fd,locks=local_locks(tmp_path);clock=[1_000_000_000]
    owner=control.DatabaseControl(b2_pg.factory('controller'),locks=locks,clock_ns=lambda:clock[0])
    original=dict(host_id='synthetic-host',boot_id='synthetic-boot',process_start='1:2',control_dev=locks.inode[0],control_ino=locks.inode[1],source_dev=0,source_ino=0,generation_digest=await owner.generation_digest(),configuration_state={})
    try:
        binding=await owner.begin_window(original);supplied=binding
        assert hasattr(owner,'drain_deadline_ns'),'actual original drain deadline accessor missing'
        if failure=='expired':clock[0]+=30_000_000_001
        elif failure=='unknown':owner._windows[binding.window_id]['unknown']=True
        elif failure=='wrongbinding':supplied=replace(binding,epoch=binding.epoch+1)
        elif failure=='after_exclusive':await owner.acquire_window_exclusive(binding)
        async with b2_pg.connection('owner') as c:before=(await(await c.execute('SELECT count(*) FROM easel_backup_control.audit')).fetchone())[0]
        if failure is None:
            assert owner.drain_deadline_ns(supplied)==31_000_000_000
            clock[0]+=10_000_000_000;assert owner.drain_deadline_ns(supplied)==31_000_000_000
        else:
            with pytest.raises(control.MaintenanceError):owner.drain_deadline_ns(supplied)
        async with b2_pg.connection('owner') as c:assert(await(await c.execute('SELECT count(*) FROM easel_backup_control.audit')).fetchone())[0]==before
    finally:
        for holder in owner._holders.values():holder.close()
        locks.close();os.close(fd)
