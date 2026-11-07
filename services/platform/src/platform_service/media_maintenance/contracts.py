"""Strict immutable internal bindings; never deserialize capabilities from HTTP."""
from dataclasses import dataclass, field
from enum import StrEnum
import re
import uuid

class MaintenanceError(ValueError):
    code = "invalid_backup"
    def __init__(self): super().__init__(self.code)

def require(value):
    if not value: raise MaintenanceError()

def identifier(value):
    require(type(value) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}",value,re.ASCII) is not None)

def digest(value):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}",value,re.ASCII) is not None)

def integer(value, minimum=0): require(type(value) is int and minimum <= value <= 2**63-1)

def uuid4(value):
    try:
        parsed=uuid.UUID(value)
        require(type(value) is str and str(parsed)==value and parsed.version==4 and parsed.variant==uuid.RFC_4122)
    except (ValueError,TypeError,AttributeError): raise MaintenanceError() from None

class ControlState(StrEnum):
    OPEN="OPEN"
    DRAINING="DRAINING"
    CAPTURING="CAPTURING"
    VERIFYING="VERIFYING"
    RECOVERING="RECOVERING"
    NEEDS_REVIEW="NEEDS_REVIEW"

@dataclass(frozen=True)
class WorkerIdentity:
    worker_id: str
    generation: str
    host_id: str
    boot_id: str
    process_start: str
    def __post_init__(self):
        uuid4(self.worker_id);uuid4(self.generation)
        for value in (self.host_id,self.boot_id,self.process_start):identifier(value)

@dataclass(frozen=True)
class OperationBinding:
    kind: str
    subject_id: str
    attempt_id: str | None
    worker: WorkerIdentity
    upstream_identity_sha256: str | None
    def __post_init__(self):
        require(self.kind in {"catalog","policy","mint","upload","job","gc","maintenance"})
        identifier(self.subject_id)
        require(type(self.worker) is WorkerIdentity)
        if self.kind in {"job","gc"}:identifier(self.attempt_id)
        else: require(self.attempt_id is None)
        if self.upstream_identity_sha256 is not None:digest(self.upstream_identity_sha256)

@dataclass(frozen=True)
class WindowBinding:
    window_id: str
    owner_nonce: bytes = field(repr=False)
    epoch: int
    host_id: str
    boot_id: str
    process_start: str
    control_dev: int
    control_ino: int
    source_dev: int
    source_ino: int
    generation_digest: str
    deadline_ns: int
    def __post_init__(self):
        uuid4(self.window_id);require(type(self.owner_nonce) is bytes and len(self.owner_nonce)==32)
        integer(self.epoch)
        for value in (self.host_id,self.boot_id,self.process_start):identifier(value)
        for value in (self.control_dev,self.control_ino,self.source_dev,self.source_ino):integer(value)
        digest(self.generation_digest);integer(self.deadline_ns,1)
