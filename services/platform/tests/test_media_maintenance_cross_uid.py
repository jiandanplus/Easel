"""Permission policy regressions; actual cross-UID proof is the Linux runner."""
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from platform_service.media_maintenance.filesystem import ControlLock, MaintenanceError, SourceRoot


@pytest.mark.parametrize("mode", [0o750, 0o755, 0o770])
def test_nonroot_owned_control_and_source_remain_private(tmp_path, mode):
    if os.getuid() == 0:
        pytest.skip("actual nonroot owner matrix is covered by Linux runner")
    root = tmp_path / "control"
    root.mkdir(mode=mode)
    root.chmod(mode)
    lock = root / "media.lock"
    lock.touch(mode=0o640)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(MaintenanceError):
            ControlLock(fd, "media.lock", owner=(os.getuid(), os.getgid()))
        with pytest.raises(MaintenanceError):
            SourceRoot(fd, owner=(os.getuid(), os.getgid()))
    finally:
        os.close(fd)


def test_same_uid_private_control_still_works(tmp_path):
    root = tmp_path / "control"
    root.mkdir(mode=0o700)
    lock = root / "media.lock"
    lock.touch(mode=0o600)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    locks = ControlLock(fd, "media.lock", owner=(os.getuid(), os.getgid()))
    try:
        with locks.acquire_shared():
            with pytest.raises(MaintenanceError):
                locks.acquire_exclusive()
        with locks.acquire_exclusive():
            pass
    finally:
        locks.close()
        os.close(fd)
