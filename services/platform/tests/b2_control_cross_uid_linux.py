"""Offline Linux permission/OFD component runner; only synthetic owned volume IO.

Run as root in the separately authorized restricted container. This deliberately
does not override the filesystem allowlist or attest a whole source/DB profile.
"""
import json
import os
from pathlib import Path
import signal
import stat
import sys
import traceback

from platform_service.media_maintenance.filesystem import (
    ControlLock, MaintenanceError, SourceRoot, filesystem_identity, open_control_root,
)

APP = 10001
BASE = Path("/component")


def emit(event, **fields):
    print(json.dumps({"event": event, **fields}, sort_keys=True), flush=True)


def metadata(path):
    value = os.stat(path)
    return {"uid": value.st_uid, "gid": value.st_gid,
            "mode": oct(stat.S_IMODE(value.st_mode)), "dev": value.st_dev,
            "ino": value.st_ino}


def rejected(action, kind=MaintenanceError):
    try:
        value = action()
    except kind:
        return
    if hasattr(value, "close"):
        value.close()
    raise AssertionError("unsafe operation accepted")


def fork_app(action):
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        try:
            os.setgroups([])
            os.setgid(APP)
            os.setuid(APP)
            caps = next(line.split()[1] for line in Path("/proc/self/status").read_text().splitlines()
                        if line.startswith("CapEff:"))
            assert os.getuid() == APP and os.getgid() == APP and os.getgroups() == []
            assert int(caps, 16) == 0
            result = action()
            os.write(write_fd, json.dumps({"ok": True, "uid": os.getuid(),
                     "gid": os.getgid(), "groups": os.getgroups(), "CapEff": caps,
                     "result": result}).encode())
            os._exit(0)
        except BaseException as exc:
            os.write(write_fd, json.dumps({"ok": False, "uid": os.getuid(),
                     "error": type(exc).__name__, "trace": traceback.format_exc()}).encode())
            os._exit(1)
    os.close(write_fd)
    with os.fdopen(read_fd, "rb") as stream:
        result = json.loads(stream.read())
    _, status = os.waitpid(pid, 0)
    emit("app_result", **result)
    assert os.waitstatus_to_exitcode(status) == 0 and result["ok"], result
    return result


def make_control(name, mode=0o750, uid=0, lock_mode=0o640):
    root = BASE / name
    root.mkdir(mode=mode)
    os.chmod(root, mode)
    lock = root / "media.lock"
    lock.touch(mode=lock_mode)
    os.chmod(lock, lock_mode)
    os.chown(lock, uid, APP)
    os.chown(root, uid, APP)
    return root, lock


def path_lock(root):
    return ControlLock.from_path(str(root), owner=(0, APP))


def main():
    signal.alarm(110)
    assert sys.platform == "linux" and os.getuid() == 0
    fd = os.open(BASE, os.O_RDONLY | os.O_DIRECTORY)
    try:
        identity = filesystem_identity(fd)
    finally:
        os.close(fd)
    assert identity[1] in {"ext4", "xfs", "btrfs"}, identity
    emit("environment", filesystem=identity, uid=os.getuid(), gid=os.getgid(),
         CapEff=next(line.split()[1] for line in Path("/proc/self/status").read_text().splitlines()
                     if line.startswith("CapEff:")), base=metadata(BASE))

    root, lock = make_control("control-" + sys.argv[1])
    app_root = BASE / ("app-" + sys.argv[1])
    app_root.mkdir(mode=0o700)
    os.chown(app_root, APP, APP)
    emit("control_metadata", directory=metadata(root), lock=metadata(lock))

    def usable():
        # Real kernel permission is independently established before API policy.
        probe = os.open(lock, os.O_RDONLY | os.O_NOFOLLOW)
        os.close(probe)
        emit("app_kernel_readonly_open", uid=os.getuid(), success=True)
        locks = path_lock(root)
        try:
            with locks.acquire_shared():
                locks.verify()
            opened = open_control_root(str(root), owner=(0, APP))
            os.close(opened)
            replacement = app_root / "replacement"
            replacement.touch(mode=0o600)
            for action in (
                lambda: os.open(lock, os.O_WRONLY),
                lambda: os.chmod(lock, 0o600),
                lambda: os.chmod(root, 0o700),
                lambda: os.unlink(lock),
                lambda: os.rename(lock, root / "moved"),
                lambda: os.rename(root, BASE / "renamed"),
                lambda: os.mkdir(root / "replace"),
                lambda: os.replace(replacement, lock),
                lambda: os.unlink(BASE / "chain-marker"),
            ):
                rejected(action, PermissionError)
            return {"readonly_shared": True, "denied_mutations": 9, "inode": locks.inode}
        finally:
            locks.close()

    (BASE / "chain-marker").touch(mode=0o644)
    fork_app(usable)

    controller = path_lock(root)
    # Distinct process and real last shared reference, with deterministic IPC.
    status_r, status_w = os.pipe()
    release_r, release_w = os.pipe()

    def share():
        locks = path_lock(root)
        parent = locks.acquire_shared()
        child = parent.retain()
        parent.close()
        os.write(status_w, b"H")
        assert os.read(release_r, 1) == b"R"
        child.close()
        os.write(status_w, b"D")
        assert os.read(release_r, 1) == b"X"
        locks.close()
        return {"last_actual_shared_reference_released": True}

    pid = os.fork()
    if pid == 0:
        os.close(status_r)
        os.close(release_w)
        try:
            fork_app(share)
            os._exit(0)
        except BaseException:
            traceback.print_exc()
            os._exit(1)
    os.close(status_w)
    os.close(release_r)
    assert os.read(status_r, 1) == b"H"
    rejected(controller.acquire_exclusive)
    emit("root_exclusive_blocked_by_app_last_ref", success=True)
    os.write(release_w, b"R")
    assert os.read(status_r, 1) == b"D"
    with controller.acquire_exclusive():
        emit("root_exclusive_after_app_last_ref", success=True)
        def blocked_shared():
            locks = path_lock(root)
            try:
                rejected(locks.acquire_shared)
                return {"shared_blocked_by_root_exclusive": True}
            finally:
                locks.close()
        fork_app(blocked_shared)
    os.write(release_w, b"X")
    os.close(release_w)
    os.close(status_r)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0

    # Recheck the immutable bound metadata and path entries after construction.
    for mode in (0o770, 0o755, 0o700):
        os.chmod(root, mode)
        rejected(controller.verify)
        os.chmod(root, 0o750)
        controller.verify()
    os.chmod(lock, 0o600)
    rejected(controller.verify)
    os.chmod(lock, 0o640)
    controller.verify()
    os.chown(lock, APP, APP)
    rejected(controller.verify)
    os.chown(lock, 0, APP)
    controller.verify()
    os.chown(root, APP, APP)
    rejected(controller.verify)
    os.chown(root, 0, APP)
    controller.verify()
    os.chown(root, 0, APP + 1)
    rejected(controller.verify)
    os.chown(root, 0, APP)
    controller.verify()
    os.chmod(BASE, 0o775)
    rejected(controller.verify)
    os.chmod(BASE, 0o755)
    controller.verify()
    old = root / "original.lock"
    lock.rename(old)
    lock.touch(mode=0o640)
    os.chmod(lock, 0o640)
    os.chown(lock, 0, APP)
    rejected(controller.verify)
    lock.unlink()
    old.rename(lock)
    controller.verify()
    moved = BASE / ("moved-" + sys.argv[1])
    root.rename(moved)
    root.mkdir(mode=0o750)
    os.chown(root, 0, APP)
    rejected(controller.verify)
    root.rmdir()
    moved.rename(root)
    controller.verify()
    controller.close()

    for suffix, mode, uid, lock_mode in (
        ("writable", 0o770, 0, 0o640), ("public", 0o755, 0, 0o640),
        ("nonroot", 0o750, APP, 0o640), ("write-lock", 0o750, 0, 0o660),
    ):
        invalid, _ = make_control(suffix + "-" + sys.argv[1], mode, uid, lock_mode)
        rejected(lambda: path_lock(invalid))
        if uid == APP:
            def nonroot_shared_denied():
                rejected(lambda: ControlLock.from_path(str(invalid), owner=(APP, APP)))
                return {"nonroot_owner_0750_same_owner_rejected": True}
            fork_app(nonroot_shared_denied)

    private, _ = make_control("private-" + sys.argv[1], 0o700)
    def private_denied():
        rejected(lambda: path_lock(private))
        return {"root_0700_denied": True}
    fork_app(private_denied)
    private_locks = path_lock(private)
    with private_locks.acquire_shared():
        pass
    private_locks.close()
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        rejected(lambda: SourceRoot(fd, owner=(0, APP)))
    finally:
        os.close(fd)
    emit("negative_matrix", source_0750_rejected=True, unsafe_roots_rejected=3,
         unsafe_lock_rejected=True, inode_owner_mode_chain_drift_rejected=True)
    emit("PASS", scope="Linux permission and OFD component only")


if __name__ == "__main__":
    main()
