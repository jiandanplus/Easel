"""In-process media maintenance control (media setting maintenance="local", the default).

Operation permits, physical references and write/GC fences keep their exact
types and checks; only their backing state lives in this process instead of
the B2 `easel_backup_control` schema. What this drops is the cross-process
write barrier B2 uses to capture a database/media snapshot at one instant, so
it assumes a single central API process owns the media root. See
docs/decisions/0006-media-maintenance-optional.md.
"""
import os
from pathlib import Path

from .contracts import MaintenanceError, OperationBinding, digest, require


class LocalLease:
    def __init__(self):
        self._closed = False

    def retain(self):
        return LocalLease()

    def close(self):
        self._closed = True


class LocalLocks:
    """Stand-in for ControlLock: one process, so no shared lock file."""

    def verify(self):
        return None

    def acquire_shared(self):
        return LocalLease()

    def close(self):
        return None


class LocalSource:
    """Stand-in for SourceRoot: a path or descriptor must be the media root directory itself."""

    def __init__(self, root):
        self.root = Path(root)
        self._identity = self._stat_path(self.root)

    @staticmethod
    def _stat_path(path):
        fd = None
        try:
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            info = os.fstat(fd)
            return (info.st_dev, info.st_ino)
        except (OSError, TypeError, ValueError):
            raise MaintenanceError() from None
        finally:
            if fd is not None:
                os.close(fd)

    def verify(self):
        require(self._stat_path(self.root) == self._identity)

    def verify_path(self, path):
        require(self._stat_path(path) == self._identity)

    def verify_fd(self, fd):
        info = os.fstat(fd)
        require((info.st_dev, info.st_ino) == self._identity)

    def close(self):
        return None


class LocalControl:
    def __init__(self, connection_factory, source):
        require(type(source) is LocalSource)
        self.connection_factory = connection_factory
        self._source = source
        self._pid = os.getpid()
        self._epoch = 0
        self._states = {}
        self._permits = {}
        self._workers = set()

    def __repr__(self):
        return "<LocalControl>"

    def _local(self):
        require(os.getpid() == self._pid)

    async def status(self):
        self._local()
        return {"state": "OPEN", "epoch": self._epoch, "window_id": None}

    async def register_worker(self, identity):
        self._local()
        self._workers.add((identity.worker_id, identity.generation))

    async def attest_worker(self, identity, source):
        self._local()
        require((identity.worker_id, identity.generation) in self._workers and source is self._source)
        source.verify()

    async def unregister_worker(self, identity):
        self._local()
        require(not any(p.binding.worker == identity and (not p._closed or p.physical_count)
                        for p in self._permits.values()))
        self._workers.discard((identity.worker_id, identity.generation))

    def _verify_physical_domain(self):
        self._local()
        self._source.verify()

    def _track_permit(self, permit):
        self._permits[permit.token_digest] = permit

    def _forget_permit(self, permit):
        self._permits.pop(permit.token_digest, None)

    async def issue_permit(self, binding, token_digest):
        self._local()
        require(type(binding) is OperationBinding and token_digest not in self._states)
        digest(token_digest)
        require((binding.worker.worker_id, binding.worker.generation) in self._workers)
        self._epoch += 1
        self._states[token_digest] = {"state": "active", "epoch": self._epoch, "binding": binding}
        return self._epoch

    def _active(self, permit, binding=None):
        state = self._states.get(permit.token_digest)
        require(state is not None and state["state"] == "active" and state["epoch"] == permit.epoch
                and state["binding"] == (binding or permit.binding))
        return state

    async def verify_permit(self, connection, permit, binding):
        self._local()
        require(connection is not None and binding == permit.binding)
        self._active(permit, binding)

    async def finish_permit(self, permit, business_digest):
        self._local()
        digest(business_digest)
        self._active(permit).update(state="completed", business_sha256=business_digest)

    async def observe_permit(self, permit):
        self._local()
        state = self._states.get(permit.token_digest)
        require(state is not None and state["epoch"] == permit.epoch)
        result = {"state": state["state"]}
        if state["state"] == "completed":
            result["business_sha256"] = state["business_sha256"]
        return result

    async def mark_unknown(self, permit):
        self._local()
        permit.invalidate()  # as DatabaseControl: an unknown permit is never used again locally
        state = self._states.get(permit.token_digest)
        require(state is not None and state["epoch"] == permit.epoch)
        if state["state"] == "active":
            state["state"] = "unknown"

    async def blob_writer_owned(self, connection, permit, storage_key):
        """Ownership is the live, valid permit itself; the blob row was already matched in SQL."""
        self._local()
        require(type(storage_key) is str and storage_key)
        self._active(permit)
        return True

    def gc_idle_sql(self, column):
        require(column in {"storage_key", "b.storage_key"})
        return "TRUE"

    async def gc_writer_idle(self, connection, storage_key):
        """GC targets gc_pending blobs only; no writer can be staging one in this process."""
        self._local()
        require(type(storage_key) is str and storage_key)
        return True
