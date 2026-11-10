"""Independent expected context; crypto receipts do not prove capture or restore."""
from dataclasses import dataclass
import re
from typing import TYPE_CHECKING
import uuid

if TYPE_CHECKING:
    from ..media_maintenance.contracts import WindowBinding

MAX_ARCHIVE = 32 * 1024**3
MAX_CONTEXT = 32 * 1024**2


def require(value):
    if not value:
        raise B2Error()


def validate_hash(value):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value, re.ASCII) is not None)
    return value


def validate_id(value):
    require(type(value) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value, re.ASCII) is not None)
    return value


def validate_uuid(value):
    try:
        require(type(value) is str)
        parsed = uuid.UUID(value)
        require(parsed.version == 4 and parsed.variant == uuid.RFC_4122 and str(parsed) == value)
        return value
    except (ValueError, TypeError, AttributeError):
        raise B2Error() from None


def validate_int(value, maximum, minimum=0):
    require(type(value) is int and minimum <= value <= maximum)
    return value


class B2Error(ValueError):
    code = "invalid_backup"

    def __init__(self, *args, code="invalid_backup"):
        super().__init__(self.code)


@dataclass(frozen=True)
class ExpectedV2:
    backup_id: str
    kind: str
    target: str
    recipient_sha256: str
    plaintext_length: int
    plan_sha256: str
    registry_sha256: str

    def __post_init__(self):
        validate_uuid(self.backup_id)
        require(type(self.kind) is str and self.kind in ("central-archive", "central-context"))
        validate_id(self.target)
        for value in (self.recipient_sha256, self.plan_sha256, self.registry_sha256):
            validate_hash(value)
        validate_int(self.plaintext_length, MAX_ARCHIVE if self.kind == "central-archive" else MAX_CONTEXT, 1)


@dataclass(frozen=True, repr=False)
class CapturedSource:
    plan: dict
    media_fd: int
    fixed_fds: dict[str, int]
    owner: tuple[int, int]
    source_facts_sha256: str
    window: "WindowBinding"

    def __post_init__(self):
        require(type(self.plan) is dict and type(self.fixed_fds) is dict)
        require(self.fixed_fds.keys() == {"db/central.dump", "db/identity.dump", "db/central.roles",
            "db/identity.roles", "config/central", "config/identity", "config/media"})
        require(type(self.owner) is tuple and len(self.owner) == 2)
        for value in self.owner:
            validate_int(value, 2**32 - 1)
        validate_int(self.media_fd, 2**31 - 1)
        for name, fd in self.fixed_fds.items():
            require(type(name) is str)
            validate_int(fd, 2**31 - 1)
        validate_hash(self.source_facts_sha256)
        require(self.window is not None)


@dataclass(frozen=True)
class CommitRef:
    backup_id: str
    target: str
    commit_sha256: str
    plan_sha256: str
    registry_sha256: str

    def __post_init__(self):
        validate_uuid(self.backup_id)
        validate_id(self.target)
        for value in (self.commit_sha256, self.plan_sha256, self.registry_sha256):
            validate_hash(value)
