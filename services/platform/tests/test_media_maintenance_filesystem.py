"""Actual local FD/flock and thread lifetime; no Linux claim on Darwin."""
import importlib, os, sys, threading
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/"src"))
def api():
    try:return importlib.import_module("platform_service.media_maintenance.filesystem")
    except ModuleNotFoundError:pytest.fail("B2 same-inode physical lock is missing")
def opened(tmp_path,lib):
    root=tmp_path/"root";root.mkdir(mode=0o700);p=root/"media.lock";p.write_bytes(b"");p.chmod(0o600)
    fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY)
    return root,p,fd,lib.ControlLock(fd,"media.lock",owner=(os.getuid(),os.getgid()))
def test_shared_handles_use_actual_flock_and_last_reference_releases(tmp_path):
    lib=api();root,path,fd,locks=opened(tmp_path,lib)
    try:
        parent=locks.acquire_shared();child=parent.retain();parent.close()
        with pytest.raises(lib.MaintenanceError):locks.acquire_exclusive()
        child.close();exclusive=locks.acquire_exclusive()
        with pytest.raises(lib.MaintenanceError):locks.acquire_shared()
        exclusive.close()
        with locks.acquire_shared():pass
    finally:locks.close();os.close(fd)
def test_thread_reference_survives_parent_close_and_new_reference_is_refused(tmp_path):
    lib=api();root,path,fd,locks=opened(tmp_path,lib);entered=threading.Event();release=threading.Event()
    parent=locks.acquire_shared();child=parent.retain()
    def work():
        with child:entered.set();release.wait(2)
    thread=threading.Thread(target=work);thread.start();assert entered.wait(1);parent.close()
    try:
        with pytest.raises(lib.MaintenanceError):parent.retain()
        with pytest.raises(lib.MaintenanceError):locks.acquire_exclusive()
    finally:release.set();thread.join(2)
    with locks.acquire_exclusive():pass
    locks.close();os.close(fd)
def test_replaced_inode_and_unsafe_root_are_refused(tmp_path):
    lib=api();root,path,fd,locks=opened(tmp_path,lib)
    path.unlink();path.write_bytes(b"");path.chmod(0o600)
    try:
        with pytest.raises(lib.MaintenanceError):locks.acquire_shared()
    finally:locks.close();os.close(fd)
    root.chmod(0o777);fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY)
    try:
        with pytest.raises(lib.MaintenanceError):lib.ControlLock(fd,"media.lock",owner=(os.getuid(),os.getgid()))
    finally:os.close(fd)
def test_unsafe_files_symlink_and_unknown_filesystem_fail_closed(tmp_path):
    lib=api();root,path,fd,locks=opened(tmp_path,lib);locks.close();path.unlink();path.symlink_to("missing")
    try:
        with pytest.raises(lib.MaintenanceError):lib.ControlLock(fd,"media.lock",owner=(os.getuid(),os.getgid()))
    finally:os.close(fd)
    lib=api();root2=tmp_path/"second";root2.mkdir(mode=0o700);(root2/"media.lock").write_bytes(b"");(root2/"media.lock").chmod(0o600);fd=os.open(root2,os.O_RDONLY|os.O_DIRECTORY)
    try:
        with pytest.raises(lib.MaintenanceError):lib.ControlLock(fd,"media.lock",owner=(os.getuid(),os.getgid()),allowed_filesystems={"not-an-actual-filesystem"})
    finally:os.close(fd)

def test_exclusive_running_child_owns_fd_after_parent_closes(tmp_path):
    lib=api();root,path,fd,locks=opened(tmp_path,lib);parent=locks.acquire_exclusive()
    try:
        child=parent.retain();child.start();parent.close()
        with pytest.raises(lib.MaintenanceError):locks.acquire_shared()
        child.close()
        with locks.acquire_shared():pass
        queued=locks.acquire_exclusive();not_started=queued.retain();queued.close()
        with pytest.raises(lib.MaintenanceError):not_started.start()
        not_started.close()
        with locks.acquire_shared():pass
    finally:parent.close();locks.close();os.close(fd)

def test_control_parent_entry_replacement_is_detected(tmp_path):
    lib=api();anchor=tmp_path/"anchor";anchor.mkdir(mode=0o700);control=anchor/"control";control.mkdir(mode=0o700)
    (control/"media.lock").write_bytes(b"");(control/"media.lock").chmod(0o600);fd=os.open(anchor,os.O_RDONLY|os.O_DIRECTORY)
    locks=lib.ControlLock.from_anchor(fd,"control",owner=(os.getuid(),os.getgid()))
    try:
        with locks.acquire_shared():pass
        control.rename(anchor/"replaced");control.mkdir(mode=0o700);(control/"media.lock").write_bytes(b"");(control/"media.lock").chmod(0o600)
        with pytest.raises(lib.MaintenanceError):locks.verify()
        with pytest.raises(lib.MaintenanceError):locks.acquire_exclusive()
    finally:locks.close();os.close(fd)

def test_actual_separate_process_is_blocked_until_last_shared_ref(tmp_path):
    import subprocess
    lib=api();root,path,fd,locks=opened(tmp_path,lib);share=locks.acquire_shared();child=share.retain();share.close()
    code="import os,fcntl,sys;f=os.open(sys.argv[1],os.O_RDONLY);\ntry: fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)\nexcept BlockingIOError: sys.exit(17)\nelse: sys.exit(0)"
    try:
        first=subprocess.run([sys.executable,"-B","-c",code,str(path)],capture_output=True,timeout=5)
        assert first.returncode==17
        child.close();second=subprocess.run([sys.executable,"-B","-c",code,str(path)],capture_output=True,timeout=5)
        assert second.returncode==0
    finally:child.close();locks.close();os.close(fd)



def _fork_waiting(action):
    import select,signal
    result_r,result_w=os.pipe();finish_r,finish_w=os.pipe();pid=os.fork()
    if pid==0:
        os.close(result_r);os.close(finish_w)
        try:
            try:action()
            except api().MaintenanceError:result=b"rejected"
            else:result=b"accepted"
            os.write(result_w,result);os.close(result_w);os.read(finish_r,1);os._exit(0)
        except BaseException:os._exit(2)
    os.close(result_w);os.close(finish_r)
    if not select.select([result_r],[],[],2)[0]:
        os.kill(pid,signal.SIGKILL);os.waitpid(pid,0);os.close(result_r);os.close(finish_w)
        pytest.fail("fork child blocked before PID rejection")
    return pid,result_r,finish_w


def _finish_fork(pid,result_r,finish_w):
    try:os.write(finish_w,b"x")
    finally:
        os.close(finish_w);os.close(result_r)
        _,status=os.waitpid(pid,0);assert os.waitstatus_to_exitcode(status)==0


@pytest.mark.parametrize("started",[False,True])
def test_exclusive_reference_rejects_fork_before_inherited_rlock(tmp_path,started):
    lib=api();root,path,fd,locks=opened(tmp_path,lib);holder=locks.acquire_exclusive();ref=holder.retain()
    if started:ref.start()
    entered=threading.Event();release=threading.Event()
    def locked():
        with holder._lock:entered.set();release.wait(5)
    thread=threading.Thread(target=locked);thread.start();assert entered.wait(1)
    try:
        pid,result_r,finish_w=_fork_waiting(ref.start)
        try:assert os.read(result_r,32)==b"rejected"
        finally:_finish_fork(pid,result_r,finish_w)
    finally:
        release.set();thread.join(2);ref.close();holder.close();locks.close();os.close(fd)


@pytest.mark.parametrize("started_ref",[None,False,True])
def test_idle_fork_child_closes_all_inherited_exclusive_fds_without_unlocking_parent(tmp_path,started_ref):
    import fcntl
    lib=api();root,path,fd,locks=opened(tmp_path,lib);holder=locks.acquire_exclusive();ref=None
    if started_ref is not None:
        ref=holder.retain()
        if started_ref:ref.start()
    pid,result_r,finish_w=_fork_waiting(lambda:None)
    try:
        assert os.read(result_r,32)==b"accepted"
        # Child cleanup must never LOCK_UN the OFD that the parent still owns.
        with pytest.raises(lib.MaintenanceError):locks.acquire_shared()
        holder.close()
        if started_ref:
            with pytest.raises(lib.MaintenanceError):locks.acquire_shared()
            ref.close()
        # Child remains alive: its copied holder/dup FDs must all be closed.
        probe=os.open(path,os.O_RDONLY)
        try:
            try:fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:pytest.fail("idle fork child retained exclusive original/dup FD")
        finally:os.close(probe)
    finally:
        _finish_fork(pid,result_r,finish_w)
        if ref is not None:ref.close()
        holder.close();locks.close();os.close(fd)
