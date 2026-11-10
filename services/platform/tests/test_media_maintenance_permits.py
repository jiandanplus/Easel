"""Opaque permit lifetime over actual shared descriptors and threads."""
import asyncio,importlib,os,pickle,sys,threading
from pathlib import Path
from uuid import uuid4
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/"src"))
def api():
    try:return importlib.import_module("platform_service.media_maintenance.permits")
    except ModuleNotFoundError:pytest.fail("B2 actual operation permit lifetime is missing")
class Transport:
    def __init__(self):self.closed=0;self.issued=0
    async def issue_permit(self,binding,token_digest):self.issued+=1;return 3
    async def finish_permit(self,permit,digest):self.closed+=1
    async def verify_permit(self,connection,permit,binding):return None

def setup(tmp_path):
    lib=api();from platform_service.media_maintenance.contracts import WorkerIdentity,OperationBinding
    from platform_service.media_maintenance.filesystem import ControlLock
    root=tmp_path/"root";root.mkdir(mode=0o700);(root/"media.lock").write_bytes(b"");(root/"media.lock").chmod(0o600)
    fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY);locks=ControlLock(fd,"media.lock",owner=(os.getuid(),os.getgid()))
    worker=WorkerIdentity(str(uuid4()),str(uuid4()),"synthetic-host","synthetic-boot","1:2")
    return lib,locks,fd,OperationBinding("job","job-1","attempt-1",worker,None)
@pytest.mark.asyncio
async def test_close_waits_actual_ref_and_never_mints_continuation(tmp_path):
    lib,locks,fd,binding=setup(tmp_path);backend=Transport()
    try:
        permit=await lib.admit_operation(backend,locks,binding);ref=permit.retain_physical(binding)
        with pytest.raises(lib.MaintenanceError):await lib.close_permit(backend,permit,stable_business_digest="a"*64)
        with pytest.raises(lib.MaintenanceError):permit.retain_physical(binding)
        with pytest.raises(lib.MaintenanceError):locks.acquire_exclusive()
        assert backend.closed==0 and backend.issued==1
        ref.close();await lib.close_permit(backend,permit,stable_business_digest="a"*64)
        assert backend.closed==1
        with locks.acquire_exclusive():pass
        with pytest.raises(TypeError):pickle.dumps(permit)
    finally:locks.close();os.close(fd)
@pytest.mark.asyncio
async def test_parent_cancellation_cannot_drop_running_thread_reference(tmp_path):
    lib,locks,fd,binding=setup(tmp_path);backend=Transport();permit=await lib.admit_operation(backend,locks,binding)
    entered=threading.Event();release=threading.Event();exited=threading.Event();ref=permit.retain_physical(binding)
    def actual():
        try:
            with ref:entered.set();release.wait(2)
        finally:exited.set()
    task=asyncio.create_task(asyncio.to_thread(actual))
    try:
        await asyncio.to_thread(entered.wait,1);assert entered.is_set();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        with pytest.raises(lib.MaintenanceError):await lib.close_permit(backend,permit,stable_business_digest="a"*64)
        with pytest.raises(lib.MaintenanceError):locks.acquire_exclusive()
    finally:release.set();await asyncio.to_thread(exited.wait,2)
    await lib.close_permit(backend,permit,stable_business_digest="a"*64)
    with locks.acquire_exclusive():pass
    locks.close();os.close(fd)
@pytest.mark.asyncio
async def test_wrong_attempt_invalidated_and_unstarted_refs_cannot_begin(tmp_path):
    lib,locks,fd,binding=setup(tmp_path);backend=Transport();permit=await lib.admit_operation(backend,locks,binding)
    from dataclasses import replace
    try:
        with pytest.raises(lib.MaintenanceError):permit.retain_physical(replace(binding,attempt_id="wrong"))
        queued=permit.retain_physical(binding);permit.invalidate()
        with pytest.raises(lib.MaintenanceError):queued.start()
        queued.close()
        with pytest.raises(lib.MaintenanceError):await lib.assert_transaction_permit(None,permit,binding)
        assert backend.closed==0
    finally:permit.release_failed_physical();locks.close();os.close(fd)

@pytest.mark.asyncio
async def test_fork_cannot_reuse_parent_opaque_permit(tmp_path):
    lib,locks,fd,binding=setup(tmp_path);backend=Transport();permit=await lib.admit_operation(backend,locks,binding)
    pid=os.fork()
    if pid==0:
        try:
            try:permit.retain_physical(binding)
            except lib.MaintenanceError:os._exit(0)
            os._exit(1)
        except BaseException:os._exit(2)
    try:
        _,status=os.waitpid(pid,0);assert os.waitstatus_to_exitcode(status)==0
        with permit.retain_physical(binding):pass
        await lib.close_permit(backend,permit,stable_business_digest='a'*64)
    finally:locks.close();os.close(fd)



@pytest.mark.asyncio
@pytest.mark.parametrize("started",[False,True])
async def test_physical_reference_rejects_fork_before_inherited_rlock(tmp_path,started):
    import select,signal
    lib,locks,fd,binding=setup(tmp_path);backend=Transport();permit=await lib.admit_operation(backend,locks,binding);ref=permit.retain_physical(binding)
    if started:ref.start()
    entered=threading.Event();release=threading.Event()
    def locked():
        with permit._lock:entered.set();release.wait(5)
    thread=threading.Thread(target=locked);thread.start();assert entered.wait(1)
    result_r,result_w=os.pipe();finish_r,finish_w=os.pipe();pid=os.fork()
    if pid==0:
        os.close(result_r);os.close(finish_w)
        try:
            try:ref.start()
            except lib.MaintenanceError:result=b"rejected"
            else:result=b"accepted"
            os.write(result_w,result);os.close(result_w);os.read(finish_r,1);os._exit(0)
        except BaseException:os._exit(2)
    os.close(result_w);os.close(finish_r);killed=False
    try:
        if not select.select([result_r],[],[],2)[0]:
            os.kill(pid,signal.SIGKILL);killed=True
            pytest.fail("fork child blocked before PhysicalRef PID rejection")
        result=os.read(result_r,32)
        release.set();thread.join(2);ref.close();await lib.close_permit(backend,permit,stable_business_digest="a"*64)
        with locks.acquire_exclusive():pass
        assert result==b"rejected"
    finally:
        if not killed:os.write(finish_w,b"x")
        os.close(finish_w);os.close(result_r);_,status=os.waitpid(pid,0)
        release.set();thread.join(2);ref.close()
        if not permit._closed:await lib.close_permit(backend,permit,stable_business_digest="a"*64)
        locks.close();os.close(fd)
        if not killed:assert os.waitstatus_to_exitcode(status)==0



@pytest.mark.parametrize("entry",["physical_count","retain_physical","invalidate","release_failed_physical","assert_transaction_permit","close_permit","observe_permit","ref_start","ref_close"])
def test_all_public_permit_lock_entries_reject_fork_before_inherited_rlock(tmp_path,entry):
    import select,signal
    lib,locks,fd,binding=setup(tmp_path);backend=Transport();permit=asyncio.run(lib.admit_operation(backend,locks,binding));ref=None
    if entry.startswith("ref_"):ref=permit.retain_physical(binding);ref.start()
    if entry=="release_failed_physical":permit.invalidate()
    actions={
        "physical_count":lambda:permit.physical_count,
        "retain_physical":lambda:permit.retain_physical(binding),
        "invalidate":permit.invalidate,
        "release_failed_physical":permit.release_failed_physical,
        "assert_transaction_permit":lambda:asyncio.run(lib.assert_transaction_permit(None,permit,binding)),
        "close_permit":lambda:asyncio.run(lib.close_permit(backend,permit,stable_business_digest="a"*64)),
        "observe_permit":lambda:asyncio.run(lib.observe_permit(backend,permit)),
        "ref_start":lambda:ref.start(),
        "ref_close":lambda:ref.close(),
    }
    entered=threading.Event();release=threading.Event()
    def held():
        with permit._lock:entered.set();release.wait(5)
    thread=threading.Thread(target=held);thread.start();assert entered.wait(1)
    read_fd,write_fd=os.pipe();pid=os.fork()
    if pid==0:
        os.close(read_fd)
        try:
            try:actions[entry]()
            except lib.MaintenanceError:result=b"rejected"
            else:result=b"accepted"
            os.write(write_fd,result);os._exit(0)
        except BaseException:os._exit(2)
    os.close(write_fd);finished=False
    try:
        if select.select([read_fd],[],[],1)[0]:
            result=os.read(read_fd,32);_,status=os.waitpid(pid,0);finished=True
            assert os.waitstatus_to_exitcode(status)==0
        else:result=b"blocked-before-pid-check"
        assert result==b"rejected",f"{entry} cross-PID outcome: {result!r}"
    finally:
        if not finished:os.kill(pid,signal.SIGKILL);os.waitpid(pid,0)
        os.close(read_fd);release.set();thread.join(1);assert not thread.is_alive()
        if ref is not None:ref.close()
        if entry=="release_failed_physical":permit.release_failed_physical()
        else:asyncio.run(lib.close_permit(backend,permit,stable_business_digest="a"*64))
        locks.close();os.close(fd)
