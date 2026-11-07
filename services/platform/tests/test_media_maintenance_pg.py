"""Dedicated control schema behavior on the new owned B2 PG database."""
import importlib,sys
from pathlib import Path
from uuid import uuid4
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/"src"))
from b2_owned_pg import b2_pg,b2_m1_pg

def api():
    try:return importlib.import_module("platform_service.media_maintenance.schema"),importlib.import_module("platform_service.media_maintenance.control")
    except ModuleNotFoundError:pytest.fail("B2 dedicated schema and CAS control are missing")
@pytest.mark.asyncio
async def test_schema_installs_only_explicit_control_ledger_and_checksums(b2_pg):
    schema,control=api();profile=schema.SchemaProfile("local_001_020","public",b2_pg.roles["runtime"],b2_pg.roles["controller"],tuple(b2_pg.migrations))
    async with b2_pg.connection("owner") as c:
        first=await schema.install_control_schema(c,profile);await c.commit()
        second=await schema.install_control_schema(c,profile);assert first==second
        versions=await(await c.execute("SELECT version,checksum FROM public.schema_migrations ORDER BY version")).fetchall()
        assert versions==b2_pg.migrations
        assert len(await(await c.execute("SELECT * FROM easel_backup_control.schema_migrations")).fetchall())==1
@pytest.mark.asyncio
async def test_missing_control_schema_is_fail_closed_and_does_not_install(b2_pg):
    schema,control=api()
    async with b2_pg.connection("owner") as c:await c.execute("DROP SCHEMA IF EXISTS easel_backup_control CASCADE");await c.commit()
    client=control.DatabaseControl(b2_pg.factory("runtime"))
    with pytest.raises(control.MaintenanceError):await client.status()
    async with b2_pg.connection("owner") as c:
        assert (await(await c.execute("SELECT count(*) FROM pg_namespace WHERE nspname='easel_backup_control'")).fetchone())[0]==0

async def installed(env):
    schema,control=api();profile=schema.SchemaProfile("local_001_020","public",env.roles["runtime"],env.roles["controller"],tuple(env.migrations))
    async with env.connection("owner") as c:
        await c.execute("DROP SCHEMA IF EXISTS easel_backup_control CASCADE");await c.commit();await schema.install_control_schema(c,profile);await c.commit()
    return control

async def attest_local(control,worker,locks,tmp_path):
    import os
    from platform_service.media_maintenance.filesystem import SourceRoot
    path=tmp_path/('source-'+uuid4().hex);path.mkdir(mode=0o700)
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
    try:source=SourceRoot(fd)
    finally:os.close(fd)
    control.locks=locks;await control.attest_worker(worker,source)
    return source


def local_locks(tmp_path):
    import os
    from platform_service.media_maintenance.filesystem import ControlLock
    root=tmp_path/"control";root.mkdir(mode=0o700);(root/"media.lock").write_bytes(b"");(root/"media.lock").chmod(0o600)
    fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY)
    return fd,ControlLock(fd,"media.lock",owner=(os.getuid(),os.getgid()))

@pytest.mark.asyncio
async def test_real_permit_drains_and_exact_owner_cas_never_auto_opens(b2_pg,tmp_path):
    import os
    from platform_service.media_maintenance.contracts import WorkerIdentity,OperationBinding
    from platform_service.media_maintenance.permits import admit_operation,assert_transaction_permit,close_permit
    control=await installed(b2_pg);fd,locks=local_locks(tmp_path)
    runtime=control.DatabaseControl(b2_pg.factory("runtime"));owner=control.DatabaseControl(b2_pg.factory("controller"),locks=locks)
    worker=WorkerIdentity(str(uuid4()),str(uuid4()),"synthetic-host","synthetic-boot","1:2");await runtime.register_worker(worker)
    source=await attest_local(runtime,worker,locks,tmp_path)
    model="model-"+uuid4().hex;binding=OperationBinding("catalog",model,None,worker,None)
    permit=await admit_operation(runtime,locks,binding)
    identity=dict(host_id=worker.host_id,boot_id=worker.boot_id,process_start=worker.process_start,control_dev=locks.inode[0],control_ino=locks.inode[1],source_dev=os.fstat(fd).st_dev,source_ino=os.fstat(fd).st_ino,generation_digest=await owner.generation_digest(),configuration_state={})
    try:
        window=await owner.begin_window(identity)
        assert permit.epoch==window.epoch==1 and (await runtime.status())["state"]=="DRAINING"
        with pytest.raises(control.MaintenanceError):await admit_operation(runtime,locks,binding)
        with pytest.raises(control.MaintenanceError):await owner.acquire_window_exclusive(window)
        async with b2_pg.connection("runtime") as c:
            async with c.transaction():
                await assert_transaction_permit(c,permit,binding)
                await c.execute("INSERT INTO public.media_models(id,definition,operator_id) VALUES(%s,'{}','synthetic')",(model,))
                await c.execute("INSERT INTO public.media_model_states(model_id,operator_id) VALUES(%s,'synthetic')",(model,))
        await close_permit(runtime,permit,stable_business_digest="a"*64)
        holder=await owner.acquire_window_exclusive(window);assert (await owner.status())["state"]=="CAPTURING"
        await owner.transition(window,expected_state="CAPTURING",next_state="VERIFYING",result={"content":"verified"})
        await owner.transition(window,expected_state="VERIFYING",next_state="RECOVERING",result={"source":"verified"})
        with pytest.raises(control.MaintenanceError):await owner.transition(window,expected_state="RECOVERING",next_state="OPEN",result={})
        assert (await owner.status())["state"]=="RECOVERING"
        await owner.transition(window,expected_state="RECOVERING",next_state="OPEN",result={"source_identity_sha256":"a"*64,"stable_side_effects_sha256":"b"*64,"original_state_sha256":"c"*64})
        assert (await owner.status())["state"]=="OPEN"
    finally:
        for holder in owner._holders.values():holder.close()
        source.close();locks.close();os.close(fd)

@pytest.mark.asyncio
async def test_sql_null_worker_secret_cannot_issue_permit(b2_pg):
    import hashlib,secrets,psycopg
    from dataclasses import asdict
    from psycopg.types.json import Jsonb
    from platform_service.media_maintenance.contracts import WorkerIdentity
    control=await installed(b2_pg);runtime=control.DatabaseControl(b2_pg.factory("runtime"))
    worker=WorkerIdentity(str(uuid4()),str(uuid4()),"synthetic-host","synthetic-boot","1:2");await runtime.register_worker(worker)
    async with b2_pg.connection("runtime") as c:
        with pytest.raises(psycopg.errors.CheckViolation,match="media maintenance permit required"):
            await c.execute("SELECT easel_backup_control.issue_permit(%s,NULL,%s,'catalog','model',NULL,NULL)",(Jsonb(asdict(worker)),hashlib.sha256(secrets.token_bytes(32)).hexdigest()))

@pytest.mark.asyncio
async def test_server_cas_rejects_missing_recovery_digests(b2_pg,tmp_path):
    import os,psycopg,secrets
    from psycopg.types.json import Jsonb
    control=await installed(b2_pg);fd,locks=local_locks(tmp_path);owner=control.DatabaseControl(b2_pg.factory("controller"),locks=locks)
    identity=dict(host_id="synthetic-host",boot_id="synthetic-boot",process_start="1:2",control_dev=locks.inode[0],control_ino=locks.inode[1],source_dev=os.fstat(fd).st_dev,source_ino=os.fstat(fd).st_ino,generation_digest=await owner.generation_digest(),configuration_state={})
    try:
        window=await owner.begin_window(identity);holder=await owner.acquire_window_exclusive(window)
        await owner.transition(window,expected_state="CAPTURING",next_state="VERIFYING",result={})
        await owner.transition(window,expected_state="VERIFYING",next_state="RECOVERING",result={})
        async with b2_pg.connection("controller") as c:
            with pytest.raises(psycopg.errors.CheckViolation):await c.execute("SELECT easel_backup_control.transition(%s,%s,%s,'RECOVERING','OPEN','{}')",(window.owner_nonce,window.window_id,window.epoch))
        assert (await owner.status())["state"]=="RECOVERING"
    finally:
        for holder in owner._holders.values():holder.close()
        locks.close();os.close(fd)

@pytest.mark.asyncio
async def test_unknown_gc_permit_cannot_expire_or_be_reassigned(b2_pg,tmp_path):
    import os
    from platform_service.media_maintenance.contracts import WorkerIdentity,OperationBinding
    from platform_service.media_maintenance.permits import admit_operation
    control=await installed(b2_pg);fd,locks=local_locks(tmp_path);runtime=control.DatabaseControl(b2_pg.factory("runtime"))
    worker=WorkerIdentity(str(uuid4()),str(uuid4()),"synthetic-host","synthetic-boot","1:2");await runtime.register_worker(worker)
    source=await attest_local(runtime,worker,locks,tmp_path)
    first=await admit_operation(runtime,locks,OperationBinding("gc","a"*64,"claim-1",worker,None))
    try:
        await runtime.mark_unknown(first);first.release_failed_physical()
        with pytest.raises(control.MaintenanceError):await admit_operation(runtime,locks,OperationBinding("gc","a"*64,"claim-2",worker,None))
        async with b2_pg.connection("owner") as c:
            row=await(await c.execute("SELECT status,count(*) OVER() FROM easel_backup_control.permits")).fetchone();assert row==("unknown",1)
    finally:source.close();locks.close();os.close(fd)

@pytest.mark.asyncio
async def test_lost_begin_receipt_retains_exact_intent_and_queries_original(b2_pg,tmp_path):
    import os
    control=await installed(b2_pg);fd,locks=local_locks(tmp_path)
    class LoseOnce(control.DatabaseControl):
        async def _rpc(self,name,args=(),**kw):
            result=await super()._rpc(name,args,**kw)
            if name=="begin_window":raise TimeoutError("synthetic lost receipt")
            return result
    owner=LoseOnce(b2_pg.factory("controller"),locks=locks)
    identity=dict(host_id="synthetic-host",boot_id="synthetic-boot",process_start="1:2",control_dev=locks.inode[0],control_ino=locks.inode[1],source_dev=os.fstat(fd).st_dev,source_ino=os.fstat(fd).st_ino,generation_digest=await owner.generation_digest(),configuration_state={})
    try:
        with pytest.raises(TimeoutError):await owner.begin_window(identity)
        assert len(owner.pending_window_ids)==1
        original=next(iter(owner.pending_window_ids))
        with pytest.raises(control.MaintenanceError):await owner.begin_window(identity)
        binding,status=await owner.observe_window(original)
        assert binding.window_id==original and status=="DRAINING" and binding.epoch==1
        async with b2_pg.connection("owner") as c:
            assert (await(await c.execute("SELECT count(*) FROM easel_backup_control.audit WHERE action='DRAINING'")).fetchone())[0]==1
    finally:locks.close();os.close(fd)

@pytest.mark.asyncio
async def test_expired_owner_can_close_to_review_but_never_open(b2_pg,tmp_path):
    import os
    control=await installed(b2_pg);fd,locks=local_locks(tmp_path);now=[1000]
    owner=control.DatabaseControl(b2_pg.factory("controller"),locks=locks,clock_ns=lambda:now[0])
    identity=dict(host_id="synthetic-host",boot_id="synthetic-boot",process_start="1:2",control_dev=locks.inode[0],control_ino=locks.inode[1],source_dev=os.fstat(fd).st_dev,source_ino=os.fstat(fd).st_ino,generation_digest=await owner.generation_digest(),configuration_state={})
    try:
        binding=await owner.begin_window(identity);now[0]=binding.deadline_ns+1
        with pytest.raises(control.MaintenanceError):await owner.acquire_window_exclusive(binding)
        await owner.transition(binding,expected_state="DRAINING",next_state="NEEDS_REVIEW",result={"reason":"deadline"})
        assert (await owner.status())["state"]=="NEEDS_REVIEW"
    finally:locks.close();os.close(fd)

@pytest.mark.asyncio
async def test_m1_profile_never_installs_017_020_and_has_exact_guard_coverage(b2_m1_pg):
    schema,control=api();env=b2_m1_pg
    profile=schema.SchemaProfile("public_001_016","public",env.roles["runtime"],env.roles["controller"],tuple(env.migrations))
    async with env.connection("owner") as c:
        await schema.install_control_schema(c,profile);await c.commit()
        assert (await(await c.execute("SELECT to_regclass('public.media_work')")).fetchone())[0] is None
        versions=await(await c.execute("SELECT version FROM public.schema_migrations ORDER BY version")).fetchall()
        assert len(versions)==16 and versions[-1][0].startswith('016_')
        actual={row[0] for row in await(await c.execute("SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid WHERE tgname='a_b2_rows' AND c.relname LIKE 'media_%'")).fetchall()}
        assert actual==set(schema.profile_tables(profile.name))

@pytest.mark.asyncio
async def test_lost_capture_receipt_requires_observation_and_never_reacquires_as_success(b2_pg,tmp_path):
    import os
    control=await installed(b2_pg);fd,locks=local_locks(tmp_path)
    class LoseCapture(control.DatabaseControl):
        async def _rpc(self,name,args=(),**kw):
            result=await super()._rpc(name,args,**kw)
            if name=='transition' and args[4]=='CAPTURING':raise TimeoutError('synthetic capture receipt loss')
            return result
    owner=LoseCapture(b2_pg.factory('controller'),locks=locks)
    identity=dict(host_id='synthetic-host',boot_id='synthetic-boot',process_start='1:2',control_dev=locks.inode[0],control_ino=locks.inode[1],source_dev=os.fstat(fd).st_dev,source_ino=os.fstat(fd).st_ino,generation_digest=await owner.generation_digest(),configuration_state={})
    try:
        binding=await owner.begin_window(identity)
        with pytest.raises(TimeoutError):await owner.acquire_window_exclusive(binding)
        assert owner.pending_window_ids=={binding.window_id}
        observed,state=await owner.observe_window(binding.window_id);assert state=='CAPTURING'
        with pytest.raises(control.MaintenanceError):await owner.acquire_window_exclusive(observed)
        await owner.transition(observed,expected_state='CAPTURING',next_state='NEEDS_REVIEW',result={'reason':'receipt_unknown'})
        assert (await owner.status())['state']=='NEEDS_REVIEW'
    finally:locks.close();os.close(fd)

@pytest.mark.asyncio
async def test_database_xid_epoch_comparison_uses_wrapped_xid32(b2_pg):
    await installed(b2_pg)
    async with b2_pg.connection('owner') as c:
        for xid8,low in [('4294967301','5'),('8589934609','17')]:
            row=await(await c.execute("SELECT easel_backup_control.xid_low32(%s::xid8)::text",(xid8,))).fetchone()
            assert row[0]==low

@pytest.mark.asyncio
async def test_lost_permit_close_receipt_observes_exact_durable_result(b2_pg,tmp_path):
    import os
    from platform_service.media_maintenance.contracts import WorkerIdentity,OperationBinding
    from platform_service.media_maintenance.permits import admit_operation,assert_transaction_permit,close_permit,observe_permit
    control=await installed(b2_pg);fd,locks=local_locks(tmp_path)
    class LoseFinish(control.DatabaseControl):
        async def _rpc(self,name,args=(),**kw):
            result=await super()._rpc(name,args,**kw)
            if name=='finish_permit':raise TimeoutError('synthetic finish receipt loss')
            return result
    runtime=LoseFinish(b2_pg.factory('runtime'));worker=WorkerIdentity(str(uuid4()),str(uuid4()),'synthetic-host','synthetic-boot','1:2');await runtime.register_worker(worker)
    model='observed-'+uuid4().hex;binding=OperationBinding('catalog',model,None,worker,None);permit=await admit_operation(runtime,locks,binding)
    try:
        async with b2_pg.connection('runtime') as c:
            async with c.transaction():
                await assert_transaction_permit(c,permit,binding)
                await c.execute("INSERT INTO media_models(id,definition,operator_id) VALUES(%s,'{}','synthetic')",(model,))
                await c.execute("INSERT INTO media_model_states(model_id,operator_id) VALUES(%s,'synthetic')",(model,))
        with pytest.raises(TimeoutError):await close_permit(runtime,permit,stable_business_digest='a'*64)
        with pytest.raises(control.MaintenanceError):locks.acquire_exclusive()
        result=await observe_permit(runtime,permit);assert result=={'state':'completed','business_sha256':'a'*64}
        with locks.acquire_exclusive():pass
        assert await observe_permit(runtime,permit)==result
        async with b2_pg.connection('owner') as c:
            assert (await(await c.execute("SELECT count(*) FROM easel_backup_control.audit WHERE action='permit_completed'")).fetchone())[0]==1
    finally:locks.close();os.close(fd)

@pytest.mark.asyncio
async def test_generation_stop_requires_all_permits_closed_and_never_resurrects(b2_pg,tmp_path):
    import os
    from platform_service.media_maintenance.contracts import WorkerIdentity,OperationBinding
    from platform_service.media_maintenance.permits import admit_operation,close_permit
    control=await installed(b2_pg);fd,locks=local_locks(tmp_path);runtime=control.DatabaseControl(b2_pg.factory('runtime'))
    worker=WorkerIdentity(str(uuid4()),str(uuid4()),'synthetic-host','synthetic-boot','1:2');await runtime.register_worker(worker)
    binding=OperationBinding('catalog','no-write-'+uuid4().hex,None,worker,None);permit=await admit_operation(runtime,locks,binding)
    try:
        with pytest.raises(control.MaintenanceError):await runtime.unregister_worker(worker)
        await close_permit(runtime,permit,stable_business_digest='a'*64)
        await runtime.unregister_worker(worker);await runtime.unregister_worker(worker)
        async with b2_pg.connection('runtime') as c:
            assert (await(await c.execute('SELECT active FROM easel_backup_control.worker_status WHERE worker_id=%s',(worker.worker_id,))).fetchone())[0] is False
        with pytest.raises(control.MaintenanceError):await runtime.register_worker(worker)
    finally:locks.close();os.close(fd)


@pytest.mark.asyncio
async def test_transition_rejects_null_expected_before_any_idempotent_return(b2_pg):
    import psycopg
    from psycopg.types.json import Jsonb
    control=await installed(b2_pg);owner=control.DatabaseControl(b2_pg.factory("controller"))
    identity=dict(host_id="synthetic-host",boot_id="synthetic-boot",process_start="1:2",control_dev=0,control_ino=0,source_dev=0,source_ino=0,generation_digest=await owner.generation_digest(),configuration_state={})
    window=await owner.begin_window(identity)
    recovery={"source_identity_sha256":"a"*64,"stable_side_effects_sha256":"b"*64,"original_state_sha256":"c"*64}
    invalid=[
        (window.owner_nonce,window.window_id,window.epoch,None,"OPEN",recovery),
        (window.owner_nonce,window.window_id,window.epoch,None,"DRAINING",{}),
        (window.owner_nonce,window.window_id,None,"DRAINING","CAPTURING",{}),
        (None,window.window_id,window.epoch,"DRAINING","CAPTURING",{}),
        (window.owner_nonce,None,window.epoch,"DRAINING","CAPTURING",{}),
        (window.owner_nonce,window.window_id,window.epoch,"DRAINING",None,{}),
        (window.owner_nonce,window.window_id,window.epoch,"invalid","NEEDS_REVIEW",{}),
        (window.owner_nonce,window.window_id,window.epoch,"OPEN","DRAINING",{}),
        (window.owner_nonce,window.window_id,window.epoch,"DRAINING","CAPTURING",None),
        (window.owner_nonce,window.window_id,window.epoch,"DRAINING","CAPTURING",[]),
    ]
    for nonce,window_id,epoch,expected,next_state,result in invalid:
        async with b2_pg.connection("controller") as c:
            with pytest.raises(psycopg.errors.CheckViolation):
                await c.execute("SELECT easel_backup_control.transition(%s,%s,%s,%s,%s,%s)",(nonce,window_id,epoch,expected,next_state,None if result is None else Jsonb(result)))
        assert (await owner.status())["state"]=="DRAINING"
    async with b2_pg.connection("owner") as c:
        assert (await(await c.execute("SELECT array_agg(action ORDER BY id) FROM easel_backup_control.audit")).fetchone())[0]==["DRAINING"]


@pytest.mark.asyncio
@pytest.mark.parametrize("guard",["none","live_child","identity_drift"])
async def test_lost_open_receipt_observes_exact_result_and_releases_exclusive(b2_pg,tmp_path,guard):
    import os
    control=await installed(b2_pg);fd,locks=local_locks(tmp_path)
    class LoseOpen(control.DatabaseControl):
        async def _rpc(self,name,args=(),**kw):
            value=await super()._rpc(name,args,**kw)
            if name=="transition" and args[4]=="OPEN":raise TimeoutError("synthetic OPEN receipt loss")
            return value
    owner=LoseOpen(b2_pg.factory("controller"),locks=locks)
    identity=dict(host_id="synthetic-host",boot_id="synthetic-boot",process_start="1:2",control_dev=locks.inode[0],control_ino=locks.inode[1],source_dev=os.fstat(fd).st_dev,source_ino=os.fstat(fd).st_ino,generation_digest=await owner.generation_digest(),configuration_state={})
    child=None
    try:
        window=await owner.begin_window(identity);holder=await owner.acquire_window_exclusive(window)
        await owner.transition(window,expected_state="CAPTURING",next_state="VERIFYING",result={})
        await owner.transition(window,expected_state="VERIFYING",next_state="RECOVERING",result={})
        recovery={"source_identity_sha256":"a"*64,"stable_side_effects_sha256":"b"*64,"original_state_sha256":"c"*64}
        if guard=="live_child":
            child=holder.retain();child.start()
            with pytest.raises(control.MaintenanceError):await owner.transition(window,expected_state="RECOVERING",next_state="OPEN",result=recovery)
            assert (await owner.status())["state"]=="RECOVERING" and not owner.pending_window_ids
            with pytest.raises(control.MaintenanceError):locks.acquire_shared()
            child.close();child=None
        with pytest.raises(TimeoutError):await owner.transition(window,expected_state="RECOVERING",next_state="OPEN",result=recovery)
        assert (await owner.status())["state"]=="OPEN" and owner.pending_window_ids=={window.window_id}
        if guard=="live_child":
            with pytest.raises(control.MaintenanceError):holder.retain()
        if guard=="identity_drift":
            path=tmp_path/"control"/"media.lock";path.rename(path.with_name("original.lock"));path.write_bytes(b"");path.chmod(0o600)
            with pytest.raises(control.MaintenanceError):await owner.observe_window(window.window_id)
            assert owner.pending_window_ids=={window.window_id} and window.window_id in owner._holders
        else:
            observed,state=await owner.observe_window(window.window_id)
            assert observed==window and state=="OPEN" and not owner.pending_window_ids
            with locks.acquire_shared():pass
            assert window.window_id not in owner._holders
            assert await owner.observe_window(window.window_id)==(window,"OPEN")
        async with b2_pg.connection("owner") as c:
            actions=(await(await c.execute("SELECT array_agg(action ORDER BY id) FROM easel_backup_control.audit")).fetchone())[0]
            assert actions==["DRAINING","CAPTURING","VERIFYING","RECOVERING","OPEN"]
    finally:
        if child is not None:child.close()
        for retained in owner._holders.values():retained.close()
        locks.close();os.close(fd)



@pytest.mark.asyncio
async def test_open_rpc_seals_exclusive_before_async_receipt_window(b2_pg,tmp_path):
    import asyncio,os
    control=await installed(b2_pg);fd,locks=local_locks(tmp_path);entered=asyncio.Event();release=asyncio.Event()
    class PauseOpen(control.DatabaseControl):
        async def _rpc(self,name,args=(),**kw):
            if name=="transition" and args[4]=="OPEN":entered.set();await release.wait()
            return await super()._rpc(name,args,**kw)
    owner=PauseOpen(b2_pg.factory("controller"),locks=locks);child=None;task=None
    identity=dict(host_id="synthetic-host",boot_id="synthetic-boot",process_start="1:2",control_dev=locks.inode[0],control_ino=locks.inode[1],source_dev=os.fstat(fd).st_dev,source_ino=os.fstat(fd).st_ino,generation_digest=await owner.generation_digest(),configuration_state={})
    try:
        window=await owner.begin_window(identity);holder=await owner.acquire_window_exclusive(window)
        await owner.transition(window,expected_state="CAPTURING",next_state="VERIFYING",result={})
        await owner.transition(window,expected_state="VERIFYING",next_state="RECOVERING",result={})
        recovery={"source_identity_sha256":"a"*64,"stable_side_effects_sha256":"b"*64,"original_state_sha256":"c"*64}
        task=asyncio.create_task(owner.transition(window,expected_state="RECOVERING",next_state="OPEN",result=recovery))
        await asyncio.wait_for(entered.wait(),2)
        with pytest.raises(control.MaintenanceError):child=holder.retain();child.start()
        release.set();await task;task=None
        assert (await owner.status())["state"]=="OPEN" and not owner.pending_window_ids
        with locks.acquire_shared():pass
    finally:
        if child is not None:child.close()
        release.set()
        if task is not None:
            try:await task
            except control.MaintenanceError:pass
        for retained in owner._holders.values():retained.close()
        locks.close();os.close(fd)
