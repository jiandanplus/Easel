"""Pipeline request gates; real database/capture integration is a separate gate."""

import asyncio
from contextlib import contextmanager
import io
import json
import os
import subprocess
import sys
import threading

import pytest
from cryptography.hazmat.primitives import serialization

from media_backup_b2_fixture import synthetic_keys
from platform_service.media_backup_b2.contracts import B2Error
from platform_service.media_backup_b2 import pipeline


@contextmanager
def structural_flow(tmp_path, monkeypatch, *, blob_bytes=0):
    """Isolate orchestration; real SQL/raw capture/coverage remain separate gates.

    Actual OFD refs, threads, archive, crypto, durable objects and anchors run.
    Only DB transport, source-facts producer and full writer inventory are stubs.
    """
    from types import SimpleNamespace
    from media_backup_fixture import capture
    from media_backup_b2_durable_fixture import roots
    from platform_service.media_backup_b2.capture import OwnedInputs
    from platform_service.media_backup_b2.contracts import CapturedSource
    from platform_service.media_backup_b2 import self_verify
    from platform_service.media_maintenance.control import DatabaseControl
    from platform_service.media_maintenance.filesystem import ControlLock
    from platform_service.media_backup_b2.profiles import PUBLIC_016

    with roots(tmp_path / "roots") as r, capture(tmp_path / "source") as source:
        if blob_bytes:
            # Prebuild a real, non-sparse source with bounded blocks. No input
            # byte string grows with the synthetic media file's total size.
            import hashlib
            from media_backup.contract import registry_digest

            key = "a" * 64
            fd = os.open(
                key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=source.media_fd
            )
            digest, remaining = hashlib.sha256(), blob_bytes
            block = bytes(range(256)) * 256
            try:
                while remaining:
                    raw = block[: min(len(block), remaining)]
                    assert os.write(fd, raw) == len(raw)
                    digest.update(raw)
                    remaining -= len(raw)
                os.fsync(fd)
            finally:
                os.close(fd)
            row = dict(
                key=key,
                state="attached",
                size=blob_bytes,
                sha256=digest.hexdigest(),
                uid=r.owner[0],
                gid=r.owner[1],
                mode=0o600,
                tenant_id="synthetic-tenant",
                product_id="easel",
                operation_id="job:synthetic",
                present=True,
                missing_evidence_sha256=None,
            )
            source.plan["blobs"].append(row)
            source.plan["references"].append(
                {k: row[k] for k in ("key", "tenant_id", "product_id", "operation_id")}
            )
            source.plan["registry_sha256"] = registry_digest(
                source.plan["blobs"], source.plan["references"]
            )
        control_dir = tmp_path / "control"
        control_dir.mkdir(mode=0o700)
        (control_dir / "media.lock").write_bytes(b"")
        (control_dir / "media.lock").chmod(0o600)
        cfd = os.open(control_dir, os.O_RDONLY | os.O_DIRECTORY)
        locks = ControlLock(cfd, "media.lock", owner=r.owner)
        control = DatabaseControl(lambda: None, locks=locks)
        transport = {"state": "OPEN", "binding": None, "trace": []}

        async def rpc(self, name, args=(), **kwargs):
            transport["trace"].append(name)
            if name == "begin_window":
                transport.update(state="DRAINING", binding=dict(args[2].obj))
                return 1
            if name == "transition":
                assert transport["state"] == args[3]
                transport["state"] = args[4]
                transport["result"] = args[5].obj
                if transport.get("lose_after") == args[4]:
                    raise RuntimeError("synthetic lost owner RPC receipt")
                return None
            if name == "verify_window":
                assert transport["state"] == args[3]
                return dict(
                    binding=transport["binding"],
                    state=transport["state"],
                    epoch=1,
                    generation_digest=transport["binding"]["generation_digest"],
                )
            if name == "observe_window":
                return dict(
                    binding=transport["binding"],
                    state=transport["state"],
                    epoch=1,
                    result=transport["result"],
                )
            raise AssertionError(name)

        monkeypatch.setattr(DatabaseControl, "_rpc", rpc)
        inputs = OwnedInputs(
            "not-read",
            "not-read",
            PUBLIC_016,
            (),
            (),
            source.media_fd,
            source.parent_fd,
            None,
            r.owner,
            None,
            {},
            1,
        )

        async def preflight(o, profile, lock, *, host_identity):
            assert o is inputs and lock is locks
            o._preflight = {"structural_test": True}
            return dict(
                zip(("host_id", "boot_id", "process_start"), host_identity),
                control_dev=locks.inode[0],
                control_ino=locks.inode[1],
                source_dev=os.fstat(source.media_fd).st_dev,
                source_ino=os.fstat(source.media_fd).st_ino,
                generation_digest="a" * 64,
                configuration_state={},
            )

        def checkpoint(o):
            assert o is inputs
            o._exclusive.verify(o.binding)
            return {"keepers": 2}

        def post(o, captured):
            checkpoint(o)
            assert captured.window == o.binding
            return dict(
                source_identity_sha256="b" * 64, stable_side_effects_sha256="c" * 64
            )

        async def captured(profile, exclusive, o, runner):
            return CapturedSource(
                source.plan,
                source.media_fd,
                source.fds,
                r.owner,
                source.plan["snapshots"]["source_sha256"],
                exclusive.binding,
            )

        monkeypatch.setattr(pipeline, "preflight_async", preflight)
        monkeypatch.setattr(
            pipeline, "host_identity", lambda: ("unit-host", "unit-boot", "unit-start")
        )
        monkeypatch.setattr(pipeline, "capture_source", captured)
        monkeypatch.setattr(pipeline, "_require_coverage", lambda profile: None)
        monkeypatch.setattr(OwnedInputs, "checkpoint", checkpoint)
        monkeypatch.setattr(OwnedInputs, "verify_after", post)
        monkeypatch.setattr(
            self_verify,
            "_verify_bundles",
            lambda b, f, p: {"source_facts_sha256": p["snapshots"]["source_sha256"]},
        )
        public, private, _ = synthetic_keys()
        with pipeline.RootPolicy.from_operator(r.backup_fd, owner=r.owner) as policy:
            try:
                yield SimpleNamespace(
                    r=r,
                    source=source,
                    inputs=inputs,
                    controller=control,
                    policy=policy,
                    public=public,
                    private=private,
                    transport=transport,
                    profile=PUBLIC_016,
                    runner=pipeline.ChildRunner(
                        ("/not-executed/pg_dump",), ("/not-executed/pg_restore",)
                    ),
                )
            finally:
                inputs.close()
                # This test owns its entire simulated control backend. A real
                # unknown controller retains its holder until exact review.
                for holder in control._holders.values():
                    holder.close()
                locks.close()
                os.close(cfd)


def run_flow(env):
    from media_backup_b2_fixture import BACKUP_ID, TARGET

    return pipeline.build_backup(
        env.controller,
        env.profile,
        env.inputs,
        env.public,
        env.r.backup_fd,
        env.r.anchor_fd,
        private_pem=env.private,
        backup_id=BACKUP_ID,
        target=TARGET,
        child_runner=env.runner,
        root_policy=env.policy,
    )


def test_synthetic_verification_key_is_bound_before_any_maintenance():
    public, private, recipient = synthetic_keys()
    assert pipeline.recipient_binding(public, private) == recipient


@pytest.mark.parametrize(
    "kind",
    [
        "wrong_private",
        "private_as_public",
        "empty_private",
        "non_bytes",
        "trailing_public",
        "trailing_private",
    ],
)
def test_wrong_or_unbounded_key_inputs_reject_before_maintenance(kind):
    public, private, _ = synthetic_keys()
    if kind == "wrong_private":
        from cryptography.hazmat.primitives.asymmetric import rsa

        private = rsa.generate_private_key(
            public_exponent=65537, key_size=3072
        ).private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    if kind == "private_as_public":
        public = private
    if kind == "empty_private":
        private = b""
    if kind == "non_bytes":
        public = io.BytesIO(public)
    if kind == "trailing_public":
        public += b"TRAILING_SYNTHETIC_DATA"
    if kind == "trailing_private":
        private += b"TRAILING_SYNTHETIC_DATA"
    with pytest.raises(B2Error, match="^invalid_backup$"):
        pipeline.recipient_binding(public, private)


def test_fresh_import_performs_no_child_database_or_socket_connect(tmp_path):
    code = """import json,socket,subprocess,psycopg
def denied(*a,**kw):raise AssertionError('import attempted external work')
socket.socket.connect=denied
subprocess.Popen=denied
psycopg.connect=denied
psycopg.Connection.connect=denied
psycopg.AsyncConnection.connect=denied
from platform_service.media_backup_b2 import pipeline
assert callable(pipeline.build_backup)
print(json.dumps({'imported_without_external_work':True}))
"""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    # Convert development-only relative module roots before leaving checkout.
    env["PYTHONPATH"] = os.pathsep.join(
        os.path.abspath(p) for p in env["PYTHONPATH"].split(os.pathsep)
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr[-1000:]
    assert json.loads(result.stdout) == {"imported_without_external_work": True}


@pytest.mark.asyncio
async def test_structural_flow_writes_real_authenticated_durable_group_and_restores_owner(
    tmp_path, monkeypatch
):
    from media_backup_b2_fixture import BACKUP_ID

    with structural_flow(tmp_path, monkeypatch) as env:
        result = await run_flow(env)
        assert (
            result["status"] == "backup_committed"
            and result["commit_persisted"] is True
        )
        assert result["window_restored"] is True and result["window_state"] == "OPEN"
        assert env.transport["state"] == "OPEN"
        assert (env.r.backup / BACKUP_ID / "COMMITTED.json").is_file()
        assert (env.r.anchor / (BACKUP_ID + ".json")).is_file()
        with env.controller.locks.acquire_shared():
            pass


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["selfverify", "anchor_before", "anchor_receipt"])
async def test_precommit_fault_retains_private_objects_and_closes_admission(
    tmp_path, monkeypatch, fault
):
    from media_backup_b2_fixture import BACKUP_ID

    with structural_flow(tmp_path, monkeypatch) as env:
        original = pipeline.publish_anchor

        def broken(*args, **kwargs):
            if fault == "anchor_receipt":
                original(*args, **kwargs)
            raise OSError("synthetic private detail")

        if fault == "selfverify":
            monkeypatch.setattr(pipeline, "verify_archive_content", broken)
        else:
            monkeypatch.setattr(pipeline, "publish_anchor", broken)
        with pytest.raises(B2Error, match="^invalid_backup$"):
            await run_flow(env)
        assert env.transport["state"] == "NEEDS_REVIEW"
        assert not (env.r.backup / BACKUP_ID / "COMMITTED.json").exists()
        assert (env.r.backup / BACKUP_ID / "archive.easelbs2").exists()
        assert (env.r.backup / BACKUP_ID / "context.easelbs2").exists()
        assert (env.r.anchor / (BACKUP_ID + ".json")).exists() == (
            fault == "anchor_receipt"
        )


@pytest.mark.asyncio
async def test_actual_commit_with_lost_receipt_is_explicitly_uncertain_and_observable(
    tmp_path, monkeypatch
):
    from media_backup_b2_fixture import BACKUP_ID, TARGET

    with structural_flow(tmp_path, monkeypatch) as env:
        original = pipeline.publish_commit_no_clobber

        def lost(*args):
            original(*args)
            raise OSError("synthetic commit receipt loss")

        monkeypatch.setattr(pipeline, "publish_commit_no_clobber", lost)
        result = await run_flow(env)
        assert result["status"] == "backup_commit_uncertain"
        assert result["commit_persisted"] is None and result["window_restored"] is False
        assert env.transport["state"] == "NEEDS_REVIEW"
        fd = os.open(BACKUP_ID, os.O_RDONLY | os.O_DIRECTORY, dir_fd=env.r.backup_fd)
        try:
            with pipeline.trusted_anchor_intent(
                env.r.anchor_fd,
                policy=env.policy,
                backup_id=BACKUP_ID,
                target=TARGET,
                commit_sha256=result["commit_sha256"],
            ) as intent:
                observed = pipeline.observe_backup(intent, fd, env.private)
            assert observed["status"] == "committed_bytes_verified"
            assert observed["publication_durability_confirmed"] is False
            assert observed["maintenance_window_restored"] is False
        finally:
            os.close(fd)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["RECOVERING", "OPEN"])
async def test_persisted_commit_and_window_receipt_are_distinct_and_exact_observation_survives(
    tmp_path, monkeypatch, phase
):
    with structural_flow(tmp_path, monkeypatch) as env:
        env.transport["lose_after"] = phase
        result = await run_flow(env)
        assert result["status"] == "backup_committed_window_uncertain"
        assert result["commit_persisted"] is True and result["window_restored"] is False
        assert result["window_state"] == "unknown"
        (window,) = env.controller.pending_window_ids
        observed, state = await env.controller.observe_window(window)
        assert state == phase and observed.window_id == window
        if phase == "OPEN":
            with env.controller.locks.acquire_shared():
                pass


@pytest.mark.asyncio
async def test_thread_cancellation_after_anchor_never_creates_commit_or_masks_cancel(
    tmp_path, monkeypatch
):
    from media_backup_b2_fixture import BACKUP_ID

    with structural_flow(tmp_path, monkeypatch) as env:
        original = pipeline.publish_anchor

        def cancelled(*args):
            original(*args)
            raise asyncio.CancelledError()

        monkeypatch.setattr(pipeline, "publish_anchor", cancelled)
        with pytest.raises(asyncio.CancelledError):
            await run_flow(env)
        assert env.transport["state"] == "NEEDS_REVIEW"
        assert (env.r.anchor / (BACKUP_ID + ".json")).exists()
        assert not (env.r.backup / BACKUP_ID / "COMMITTED.json").exists()


@pytest.mark.asyncio
async def test_parent_cancel_does_not_close_live_worker_or_release_capture_barrier(
    tmp_path, monkeypatch
):
    from media_backup_b2_fixture import BACKUP_ID
    from platform_service.media_maintenance.contracts import MaintenanceError

    with structural_flow(tmp_path, monkeypatch) as env:
        entered, release = threading.Event(), threading.Event()
        original = pipeline.write_archive

        def blocked(*args, **kwargs):
            entered.set()
            assert release.wait(5)
            return original(*args, **kwargs)

        monkeypatch.setattr(pipeline, "write_archive", blocked)
        task = asyncio.create_task(run_flow(env))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert env.inputs._active_work == 1
            with pytest.raises(B2Error):
                env.inputs.close()
            with pytest.raises(MaintenanceError):
                env.controller.locks.acquire_shared()
            assert env.transport["state"] == "NEEDS_REVIEW"
        finally:
            release.set()
            for _ in range(200):
                if env.inputs._active_work == 0:
                    break
                await asyncio.sleep(0.01)
            assert env.inputs._active_work == 0
            if not task.done():
                await task
        assert not (env.r.backup / BACKUP_ID / "COMMITTED.json").exists()


@pytest.mark.asyncio
async def test_receipt_never_reports_success_after_cleanup_exceeds_original_deadline(
    tmp_path, monkeypatch
):
    from platform_service.media_backup_b2.capture import OwnedInputs

    with structural_flow(tmp_path, monkeypatch) as env:
        real_clock = pipeline.time.monotonic_ns
        offset = {"ns": 0}
        monkeypatch.setattr(
            pipeline.time, "monotonic_ns", lambda: real_clock() + offset["ns"]
        )
        close = OwnedInputs.close

        def late_close(o):
            close(o)
            if env.transport["state"] == "OPEN":
                offset["ns"] = 200_000_000_000

        monkeypatch.setattr(OwnedInputs, "close", late_close)
        result = await run_flow(env)
        assert result["status"] == "backup_committed_receipt_expired"
        assert result["commit_persisted"] is True and result["window_restored"] is True


@pytest.mark.asyncio
async def test_cleanup_cancellation_cannot_be_masked_as_new_success(
    tmp_path, monkeypatch
):
    from platform_service.media_backup_b2.capture import OwnedInputs

    with structural_flow(tmp_path, monkeypatch) as env:
        original, once = OwnedInputs.close, {"cancel": True}

        def cancelled_close(o):
            if env.transport["state"] == "OPEN" and once["cancel"]:
                once["cancel"] = False
                raise asyncio.CancelledError()
            original(o)

        monkeypatch.setattr(OwnedInputs, "close", cancelled_close)
        with pytest.raises(asyncio.CancelledError):
            await run_flow(env)
        assert env.transport["state"] == "OPEN"


@pytest.mark.asyncio
async def test_private_backup_owner_is_independent_of_declared_media_owner(
    tmp_path, monkeypatch
):
    # Only the private-policy boundary is exercised. Actual media ownership is
    # the subsequent capture preflight's responsibility, not bypassed here.
    with structural_flow(tmp_path, monkeypatch) as env:
        env.inputs.owner = (env.r.owner[0] + 1, env.r.owner[1] + 1)
        reached = []

        async def stop_at_source(*args, **kwargs):
            reached.append("source_preflight")
            raise B2Error()

        monkeypatch.setattr(pipeline, "preflight_async", stop_at_source)
        with pytest.raises(B2Error):
            await run_flow(env)
        assert reached == ["source_preflight"]
        assert env.transport["trace"] == []


def attach_owned_companion(env,monkeypatch,*,reject=False):
    """Native SQL is synthetic; real pair + pipeline + publication execute."""
    from types import SimpleNamespace as NS
    from test_media_backup_b2_reconstruction_capture import fixtures,Native,Keeper
    from platform_service.media_backup_b2 import reconstruction_capture as c
    from platform_service.media_backup_b2.capture import run_owned_io
    from platform_service.media_backup_b2.contracts import CapturedSource
    import dataclasses,time
    from b3_source_owner import SourceOwner
    from psycopg.conninfo import make_conninfo
    @dataclasses.dataclass(frozen=True)
    class Context:
        kind:str
    cancel=threading.Event();now=time.monotonic_ns();contexts=(Context('central'),Context('identity'));producers=[]
    for kind,context in zip(('central','identity'),contexts):
        parts=fixtures();scope=dataclasses.replace(parts[0],kind=kind)
        owner=SourceOwner.__new__(SourceOwner)
        owner.name=scope.database_name;owner.container_id=scope.container_id
        owner.phase='READY';owner.cleanup_attempted=False;owner.active=0;owner.unknown=False
        owner.cancellation=cancel;owner.check_budget=lambda:None;owner.attempts=[]
        owner.roles=dict(owner='owner',runtime='runtime',controller='controller')
        owner.binding=dict(started=(now-1)/1e9,deadline=(now+120_000_000_000)/1e9)
        owner.base='host=127.0.0.1 hostaddr=127.0.0.1 port=55432 dbname=source_db user=operator password=synthetic'
        owner.make_conninfo=make_conninfo;owner.records=[];owner.record=owner.records.append
        # Use the same float-to-ns pins SourceOwner journals and later verifies.
        start=int(owner.binding['started']*1e9);deadline=int(owner.binding['deadline']*1e9)
        producer=owner.begin_reconstruction(scope,original_read_context=scope,original_comparison=context,
            started_at_ns=start,deadline_ns=deadline,cancellation=cancel)
        producers.append(producer)
    pair=c.OwnedSourceCapture._for_inputs(env.inputs,original_contexts=contexts,producers=tuple(producers))
    env.inputs.bind_reconstruction(pair)
    monkeypatch.setattr(c,'_connect_native',lambda *a:Native(fixtures()))
    monkeypatch.setattr(c,'_literal_command',lambda shape,value:shape.format(repr(value)))
    async def captured(profile,exclusive,o,runner):
        def fixed_sites():
            pair._bind_window(exclusive)
            keepers={kind:Keeper() for kind in ('central','identity')}
            for kind,keeper in keepers.items():
                pair._before(kind,keeper)
                o._keepers[kind]=keeper
                o._keeper_snapshots[kind]='00000003-0000001B-1'
                o._keeper_stamps[kind]=(123,'10:20:11,15')
            for kind,keeper in keepers.items():
                if reject and kind=='central':
                    parts=fixtures();parts[1][0]='wrong_database'
                    monkeypatch.setattr(c,'_connect_native',lambda *a:Native(parts))
                pair._during(kind,keeper)
                observer=Keeper()
                try:pair._after(kind,observer)
                finally:
                    observer.close();pair._after_observer_closed(kind,observer,None)
            actual=CapturedSource(env.source.plan,env.source.media_fd,env.source.fds,env.r.owner,
                env.source.plan['snapshots']['source_sha256'],exclusive.binding)
            o._capture=actual;pair._bind_captured(actual);return actual
        return await run_owned_io(o,fixed_sites)
    monkeypatch.setattr(pipeline,'capture_source',captured)
    # Keeper close is an acknowledged local fake, without real PG/native calls.
    monkeypatch.setattr(Keeper,'close',lambda self:None,raising=False)
    return pair,cancel


@pytest.mark.asyncio
async def test_owned_pair_real_pipeline_retains_duplicates_after_original_job_closes(tmp_path,monkeypatch):
    from platform_service.media_backup_b2 import reconstruction_capture as c
    with structural_flow(tmp_path,monkeypatch) as env:
        pair,root_cancel=attach_owned_companion(env,monkeypatch)
        seen=[];original=c.OwnedSourceCapture._record_open
        def opened(self,binding,controller):
            assert self._job._closed
            assert all(os.fstat(slot['fd']) for slot in self._slots)
            seen.append(self._stage)
            return original(self,binding,controller)
        monkeypatch.setattr(c.OwnedSourceCapture,'_record_open',opened)
        result=await run_flow(env)
        assert result['status']=='backup_committed'
        assert pair._terminal_state=='TERMINAL_SUCCESS'
        assert seen==['COMMITTED']
        assert root_cancel is not pair._state['cancelled']
        assert not root_cancel.is_set() and not pair._state['cancelled'].is_set()
        assert pair._authenticated['original_archive'] is not None
        assert pair.snapshot()['publication_fds']==2
        for p in pair._producers:
            assert p._owner.active==1
            p._owner.finish_reconstruction(p)
            assert p._owner.active==0
            with pytest.raises(RuntimeError):p._owner.finish_reconstruction(p)
        pair._verify_publication()
        pair.close_publication()
        assert pair.snapshot()['publication_fds']==0
        with pytest.raises(B2Error):pair.close_publication()


@pytest.mark.asyncio
async def test_owned_validation_rejection_records_known_failed_after_finally(tmp_path,monkeypatch):
    with structural_flow(tmp_path,monkeypatch) as env:
        pair,root_cancel=attach_owned_companion(env,monkeypatch,reject=True)
        with pytest.raises(B2Error):await run_flow(env)
        assert pair._state['cancelled'].is_set() and not root_cancel.is_set()
        assert pair._terminal_state=='FAILED_TERMINAL'
        assert pair._producers[0]._rejected and not pair._producers[0]._unknown
        assert not pair.snapshot()['retained_refs']
        assert not env.inputs._active_work and not env.inputs._keepers
        assert env.transport['state']=='NEEDS_REVIEW'
        for p in pair._producers:
            p._owner.finish_known_failed_reconstruction(p)
            assert p._owner.active==0 and not p._owner.unknown
            with pytest.raises(RuntimeError):p._owner.finish_reconstruction(p)
        pair.close_publication()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['root_cancel','work_unknown','live_worker','pending_control','cleanup','expired','mutated_plan'])
async def test_owned_final_seal_refuses_late_uncertainty(tmp_path,monkeypatch,fault):
    from platform_service.media_backup_b2 import reconstruction_capture as c
    with structural_flow(tmp_path,monkeypatch) as env:
        pair,root_cancel=attach_owned_companion(env,monkeypatch)
        original=c.OwnedSourceCapture._finish_pipeline
        def finish(self,**kw):
            if fault=='root_cancel':root_cancel.set()
            if fault=='work_unknown':env.inputs._work_unknown=True
            if fault=='live_worker':env.inputs._active_work=1
            if fault=='pending_control':self._controller._windows['synthetic']={'unknown':True}
            if fault=='cleanup':kw['cleanup_known']=False
            if fault=='expired':self._producers[0]._deadline=0
            if fault=='mutated_plan':self._captured.plan['registry_sha256']='0'*64
            return original(self,**kw)
        monkeypatch.setattr(c.OwnedSourceCapture,'_finish_pipeline',finish)
        result=await run_flow(env)
        assert result['status']=='backup_committed_cleanup_uncertain'
        assert pair._terminal_state=='UNKNOWN'
        for p in pair._producers:
            with pytest.raises((RuntimeError,B2Error)):p._owner.finish_reconstruction(p)
            assert p._owner.active==1
        # Bounded synthetic teardown of deliberately inserted local flags.
        env.inputs._active_work=0;env.inputs._work_unknown=False
        self_pending=env.controller._windows
        self_pending.pop('synthetic',None)
        for slot in pair._slots:os.close(slot['fd']);slot['state']='CLOSED'


@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['reused_fd','named_substitution','object_stamp','commit_stamp'])
async def test_owned_publication_checks_held_fds_names_and_stamps(tmp_path,monkeypatch,fault):
    with structural_flow(tmp_path,monkeypatch) as env:
        pair,_=attach_owned_companion(env,monkeypatch);await run_flow(env)
        if fault=='reused_fd':
            fd=pair._slots[0]['fd'];os.close(fd)
            replacement=os.open(env.r.anchor,os.O_RDONLY|os.O_DIRECTORY)
            if replacement!=fd:os.dup2(replacement,fd);os.close(replacement)
        elif fault=='named_substitution':
            path=env.r.backup/pair._backup_id
            path.rename(path.with_name('moved'));path.mkdir(mode=0o700)
        else:
            name='archive.easelbs2' if fault=='object_stamp' else 'COMMITTED.json'
            fd=os.open(name,os.O_WRONLY,dir_fd=pair._slots[0]['fd'])
            try:os.write(fd,b'changed')
            finally:os.close(fd)
        with pytest.raises(B2Error):pair._verify_publication()
        for slot in pair._slots:os.close(slot['fd']);slot['state']='CLOSED'


@pytest.mark.asyncio
async def test_owned_publication_close_ambiguity_is_retained_never_retried(tmp_path,monkeypatch):
    from unittest.mock import patch
    from platform_service.media_backup_b2 import reconstruction_capture as c
    with structural_flow(tmp_path,monkeypatch) as env:
        pair,_=attach_owned_companion(env,monkeypatch);await run_flow(env)
        with patch.object(c.os,'close',side_effect=OSError('synthetic ambiguous close')) as close:
            with pytest.raises(B2Error):pair.close_publication()
            with pytest.raises(B2Error):pair.close_publication()
            assert close.call_count==1
        assert pair._slots[0]['state']=='UNKNOWN' and pair.snapshot()['unknown']
        for slot in pair._slots:os.close(slot['fd']);slot['state']='CLOSED'


@pytest.mark.asyncio
async def test_owned_dup_failure_retains_allocation_and_first_duplicate(tmp_path,monkeypatch):
    from unittest.mock import patch
    from platform_service.media_backup_b2 import reconstruction_capture as c
    with structural_flow(tmp_path,monkeypatch) as env:
        pair,_=attach_owned_companion(env,monkeypatch)
        original=c.OwnedSourceCapture._retain_publication_job;dup=os.dup
        def retain(self,job):
            calls=[]
            def allocate(fd):
                calls.append(fd)
                if len(calls)==2:raise OSError('synthetic dup ambiguous')
                return dup(fd)
            with patch.object(c.os,'dup',side_effect=allocate):return original(self,job)
        monkeypatch.setattr(c.OwnedSourceCapture,'_retain_publication_job',retain)
        with pytest.raises(B2Error):await run_flow(env)
        assert pair._terminal_state=='UNKNOWN' and pair._custody_unknown
        assert [slot['state'] for slot in pair._slots]==['HELD','UNKNOWN']
        assert pair._slots[1]['fd'] is None
        with pytest.raises(B2Error):pair.close_publication()
        os.close(pair._slots[0]['fd']);pair._slots[0]['state']='CLOSED'


@pytest.mark.asyncio
async def test_owned_failure_finish_journal_ambiguity_preserves_legacy_active_unknown(tmp_path,monkeypatch):
    with structural_flow(tmp_path,monkeypatch) as env:
        pair,_=attach_owned_companion(env,monkeypatch,reject=True)
        with pytest.raises(B2Error):await run_flow(env)
        p=pair._producers[0];owner=p._owner
        monkeypatch.setattr(owner,'record',lambda *a:(_ for _ in ()).throw(OSError('synthetic journal ambiguity')))
        with pytest.raises(OSError):owner.finish_known_failed_reconstruction(p)
        assert owner.unknown and owner.active==1 and owner._reconstruction_attempt['state']=='UNKNOWN'
        with pytest.raises(RuntimeError):owner.finish_known_failed_reconstruction(p)
        pair.close_publication()


@pytest.mark.asyncio
async def test_owned_publication_rejects_equal_caller_copies_at_first_recording(tmp_path,monkeypatch):
    import copy,dataclasses
    from platform_service.media_backup_b2 import reconstruction_capture as c
    with structural_flow(tmp_path,monkeypatch) as env:
        pair,_=attach_owned_companion(env,monkeypatch)
        authenticated=c.OwnedSourceCapture._record_authenticated_objects
        committed=c.OwnedSourceCapture._record_committed
        def record_objects(self,receipts,decoders,archive,reopened):
            with pytest.raises(B2Error):authenticated(self,copy.deepcopy(receipts),decoders,archive,reopened)
            with pytest.raises(B2Error):authenticated(self,receipts,copy.deepcopy(decoders),archive,reopened)
            return authenticated(self,receipts,decoders,archive,reopened)
        def record_commit(self,reference,commit,intent):
            with pytest.raises(B2Error):committed(self,dataclasses.replace(reference),commit,intent)
            return committed(self,reference,commit,intent)
        monkeypatch.setattr(c.OwnedSourceCapture,'_record_authenticated_objects',record_objects)
        monkeypatch.setattr(c.OwnedSourceCapture,'_record_committed',record_commit)
        result=await run_flow(env)
        assert result['status']=='backup_committed'
        pair.close_publication()


@pytest.mark.asyncio
async def test_phase_expiry_does_not_allow_source_owner_success(tmp_path, monkeypatch):
    from b3_source_owner import SourceOwner
    from platform_service.media_backup_b2.capture import OwnedInputs
    begin = SourceOwner.begin_reconstruction
    def original_longer_job(owner, scope, **kwargs):
        # Model the already-supported Root900 / capture120 distinction before
        # minting a producer; do not mutate an existing producer's budget.
        owner.binding['deadline'] += 780
        kwargs['deadline_ns'] = int(owner.binding['deadline'] * 1e9)
        return begin(owner, scope, **kwargs)
    monkeypatch.setattr(SourceOwner, 'begin_reconstruction', original_longer_job)
    with structural_flow(tmp_path, monkeypatch) as env:
        real_clock = pipeline.time.monotonic_ns
        offset = {'ns': 0}
        monkeypatch.setattr(pipeline.time, 'monotonic_ns', lambda: real_clock() + offset['ns'])
        pair, _ = attach_owned_companion(env, monkeypatch)
        close = OwnedInputs.close
        def late_close(inputs):
            close(inputs)
            if env.transport['state'] == 'OPEN':
                offset['ns'] = 200_000_000_000
        monkeypatch.setattr(OwnedInputs, 'close', late_close)
        try:
            result = await run_flow(env)
            assert result['status'] == 'backup_committed_receipt_expired'
            assert pair._terminal_state == 'EXPIRED'
            before = [p._owner.active for p in pair._producers]
            for p in pair._producers:
                with pytest.raises((RuntimeError, B2Error)):
                    p._owner.finish_reconstruction(p)
            assert [p._owner.active for p in pair._producers] == before
        finally:
            pair.close_publication()


@pytest.mark.asyncio
async def test_publication_close_refuses_reused_descriptor(tmp_path, monkeypatch):
    with structural_flow(tmp_path, monkeypatch) as env:
        pair, _ = attach_owned_companion(env, monkeypatch)
        await run_flow(env)
        fd = pair._slots[0]['fd']
        os.close(fd)
        replacement = os.open(env.r.anchor, os.O_RDONLY | os.O_DIRECTORY)
        if replacement != fd:
            os.dup2(replacement, fd)
            os.close(replacement)
        try:
            with pytest.raises(B2Error):
                pair.close_publication()
            assert os.fstat(fd), 'foreign replacement descriptor must remain open'
            assert pair.snapshot()['unknown']
            assert pair.snapshot()['publication_fds'] > 0
            with pytest.raises(B2Error):pair.close_publication()
        finally:
            # Only this synthetic fixture's descriptors; no native/source work.
            for slot in pair._slots:
                try:
                    os.close(slot['fd'])
                except OSError:
                    pass
                slot['state'] = 'CLOSED'


@pytest.mark.asyncio
async def test_owner_finish_rechecks_original_capture_deadline(tmp_path, monkeypatch):
    from b3_source_owner import SourceOwner
    begin = SourceOwner.begin_reconstruction
    def longer(owner, scope, **kwargs):
        owner.binding['deadline'] += 780
        kwargs['deadline_ns'] = int(owner.binding['deadline'] * 1e9)
        return begin(owner, scope, **kwargs)
    monkeypatch.setattr(SourceOwner, 'begin_reconstruction', longer)
    with structural_flow(tmp_path, monkeypatch) as env:
        pair, _ = attach_owned_companion(env, monkeypatch)
        await run_flow(env)
        assert pair._terminal_state == 'TERMINAL_SUCCESS'
        now = pipeline.time.monotonic_ns()
        monkeypatch.setattr(pipeline.time, 'monotonic_ns', lambda: now + 200_000_000_000)
        before = [p._owner.active for p in pair._producers]
        try:
            for p in pair._producers:
                with pytest.raises((RuntimeError, B2Error)):p._owner.finish_reconstruction(p)
            assert [p._owner.active for p in pair._producers] == before
        finally:pair.close_publication()


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["commit", "unlinked_directory", "mode"])
async def test_publication_release_owned_fd_despite_content_drift(tmp_path, monkeypatch, drift):
    with structural_flow(tmp_path, monkeypatch) as env:
        pair, _ = attach_owned_companion(env, monkeypatch)
        await run_flow(env)
        if drift == 'mode':os.fchmod(pair._slots[0]['fd'],0o750)
        elif drift == 'commit':os.unlink('COMMITTED.json', dir_fd=pair._slots[0]['fd'])
        else:
            for name in os.listdir(pair._slots[0]['fd']):os.unlink(name,dir_fd=pair._slots[0]['fd'])
            os.rmdir(pair._backup_id,dir_fd=pair._slots[1]['fd'])
        pair.close_publication()
        assert pair.snapshot()['publication_fds'] == 0


@pytest.mark.asyncio
async def test_publication_close_refuses_missing_descriptor_without_retry(tmp_path, monkeypatch):
    with structural_flow(tmp_path, monkeypatch) as env:
        pair, _ = attach_owned_companion(env, monkeypatch)
        await run_flow(env)
        fd = pair._slots[0]['fd']
        os.close(fd)
        try:
            with pytest.raises(B2Error):pair.close_publication()
            assert pair._slots[0]['state'] == 'UNKNOWN'
            assert pair.snapshot()['unknown']
            assert pair.snapshot()['publication_fds'] == 2
            with pytest.raises(B2Error):pair.close_publication()
        finally:
            os.close(pair._slots[1]['fd'])
