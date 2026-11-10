"""Independent root-private recovery selector; package JSON cannot grant trust.

Operator policy/FDs are explicit inputs. Root UID/GID zero is the default;
synthetic owner overrides are local-test policy, not host-root evidence.
Publication is a durable binding, not whole capture/self-verify authorization.
"""
import hashlib

from .contracts import B2Error, CommitRef, require, validate_hash, validate_id, validate_int, validate_uuid
from . import wire
from .durable import _Directory, _File, _identity, _publish_record
import os

_TOKEN = object()
_FIELDS = {"format", "backup_id", "target", "commit_sha256", "plan_sha256", "registry_sha256"}
_FORMAT = "easel-backup-anchor-v2"


class RootPolicy:
    __slots__ = ("_token", "_backup", "_closed", "_issuer")

    def __init__(self, token, directory):
        require(token is _TOKEN and type(directory) is _Directory)
        self._token, self._backup, self._closed = token, directory, False
        self._issuer = object()

    @classmethod
    def from_operator(cls, backup_root_fd, *, owner=(0, 0)):
        directory = None
        try:
            require(type(owner) is tuple and len(owner) == 2)
            for value in owner:
                validate_int(value, 2**32 - 1)
            directory = _Directory(backup_root_fd, owner)
            return cls(_TOKEN, directory)
        except BaseException as error:
            if directory is not None: directory.close()
            if not isinstance(error, Exception): raise
            raise B2Error() from None

    @property
    def owner(self):
        return self._backup.owner

    def _check(self):
        require(self._token is _TOKEN and not self._closed)
        self._backup.verify()

    def _clone(self):
        self._check()
        clone = RootPolicy(_TOKEN, self._backup.clone())
        clone._issuer = self._issuer
        return clone

    def close(self):
        if not self._closed:
            self._backup.close()
            self._closed = True

    def __repr__(self): return "<RootPolicy>"
    def __reduce_ex__(self, protocol): raise TypeError("opaque root policy")
    def __enter__(self): return self
    def __exit__(self, *args): self.close()


def _validate_anchor(value):
    require(type(value) is dict and value.keys() == _FIELDS and value["format"] == _FORMAT)
    validate_uuid(value["backup_id"])
    validate_id(value["target"])
    for key in ("commit_sha256", "plan_sha256", "registry_sha256"):
        validate_hash(value[key])


def _record(reference):
    require(type(reference) is CommitRef)
    value = dict(format=_FORMAT, backup_id=reference.backup_id, target=reference.target,
                 commit_sha256=reference.commit_sha256, plan_sha256=reference.plan_sha256,
                 registry_sha256=reference.registry_sha256)
    _validate_anchor(value)
    return wire._encode(value)


class RootAnchorIntent:
    __slots__ = ("_token", "_directory", "_policy", "_backup_id", "_target", "_commit_sha256", "_closed", "_durable")

    def __init__(self, token, directory, policy, backup_id, target, commit_sha256):
        require(token is _TOKEN and type(directory) is _Directory and type(policy) is RootPolicy)
        self._token, self._directory, self._policy = token, directory, policy
        self._backup_id, self._target, self._commit_sha256 = backup_id, target, commit_sha256
        self._closed = self._durable = False

    @property
    def backup_id(self): return self._backup_id

    @property
    def target(self): return self._target

    @property
    def commit_sha256(self): return self._commit_sha256

    def _check(self):
        require(self._token is _TOKEN and not self._closed)
        self._policy._check()
        self._directory.verify()
        backup, anchor = self._policy._backup, self._directory
        require(backup.identity[:2] not in anchor.lineage and anchor.identity[:2] not in backup.lineage)

    def _require_durable(self):
        self._check()
        require(self._durable is True)

    def _validate_commit(self, raw):
        self._check()
        value = wire.decode_commit(raw)
        require(value["backup_id"] == self.backup_id and value["target"] == self.target
                and hashlib.sha256(raw).hexdigest() == self.commit_sha256)
        return CommitRef(self.backup_id, self.target, self.commit_sha256,
                         value["plan_sha256"], value["registry_sha256"])

    def _read_anchor(self):
        self._check()
        with _File(self._directory, self.backup_id + ".json", wire.MAX_COMMIT) as source:
            raw = source.read_all(wire.MAX_COMMIT)
            value = wire._decode(raw, wire.MAX_COMMIT, _validate_anchor)
            require(value["backup_id"] == self.backup_id and value["target"] == self.target
                    and value["commit_sha256"] == self.commit_sha256)
            source.verify()
        return CommitRef(value["backup_id"], value["target"], value["commit_sha256"],
                         value["plan_sha256"], value["registry_sha256"])

    def observe(self):
        """Read the exact original selector; never retry naming or create a nonce."""
        try:
            return self._read_anchor()
        except Exception:
            raise B2Error() from None

    def _verify_for_job(self, job):
        self._check()
        require(job.backup_id == self.backup_id and job.owner == self._policy.owner
                and job._root.identity == self._policy._backup.identity
                and job._directory.parent_identity == self._policy._backup.identity)

    def _open_job(self, job_fd):
        directory = None
        try:
            self._check()
            directory = _Directory(job_fd, self._policy.owner)
            require(directory.name == self.backup_id
                    and directory.parent_identity == self._policy._backup.identity)
            return directory
        except BaseException:
            if directory is not None: directory.close()
            raise

    def _clone(self):
        self._check()
        directory, policy = None, None
        try:
            directory, policy = self._directory.clone(), self._policy._clone()
            clone = RootAnchorIntent(_TOKEN, directory, policy, self.backup_id, self.target, self.commit_sha256)
            clone._durable = self._durable
            clone._check()
            return clone
        except BaseException:
            if directory is not None: directory.close()
            if policy is not None: policy.close()
            raise

    def close(self):
        if not self._closed:
            self._directory.close()
            self._policy.close()
            self._closed = True

    def __repr__(self): return "<RootAnchorIntent>"
    def __reduce_ex__(self, protocol): raise TypeError("opaque root restore intent")
    def __enter__(self): return self
    def __exit__(self, *args): self.close()


def trusted_anchor_intent(anchor_fd, *, policy, backup_id, target, commit_sha256):
    directory = captured_policy = None
    try:
        require(type(policy) is RootPolicy)
        policy._check()
        validate_uuid(backup_id)
        validate_id(target)
        validate_hash(commit_sha256)
        directory = _Directory(anchor_fd, policy.owner)
        captured_policy = policy._clone()
        value = RootAnchorIntent(_TOKEN, directory, captured_policy, backup_id, target, commit_sha256)
        value._check()
        return value
    except BaseException as error:
        if directory is not None: directory.close()
        if captured_policy is not None: captured_policy.close()
        if not isinstance(error, Exception): raise
        raise B2Error() from None


def publish_anchor(trusted_anchor_root_fd, intent, commit_bytes):
    try:
        require(type(intent) is RootAnchorIntent)
        intent._check()
        require(not intent._durable and _identity(os.fstat(trusted_anchor_root_fd)) == intent._directory.identity)
        reference = intent._validate_commit(commit_bytes)
        _publish_record(intent._directory, intent.backup_id + ".json", _record(reference), wire.MAX_COMMIT)
        require(intent._read_anchor() == reference)
        intent._durable = True
        return reference
    except Exception:
        raise B2Error() from None
