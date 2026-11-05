from dataclasses import FrozenInstanceError
import pytest

from media_backup_b2_fixture import BACKUP_ID, TARGET
from platform_service.media_backup_b2.contracts import B2Error, ExpectedV2, CommitRef, CapturedSource


def expected(**changes):
    values = dict(backup_id=BACKUP_ID, kind="central-context", target=TARGET,
                  recipient_sha256="a" * 64, plaintext_length=1,
                  plan_sha256="b" * 64, registry_sha256="c" * 64)
    values.update(changes)
    return ExpectedV2(**values)


def test_small_context_and_immutable_bindings():
    value = expected()
    assert value.plaintext_length == 1
    with pytest.raises(FrozenInstanceError):
        value.plaintext_length = 2
    assert CommitRef(BACKUP_ID, TARGET, "a" * 64, "b" * 64, "c" * 64).backup_id == BACKUP_ID


@pytest.mark.parametrize("changes", [
    {"backup_id": BACKUP_ID.upper()}, {"backup_id": "12345678-1234-1234-9234-123456789abc"},
    {"kind": "central"}, {"target": "../host"}, {"target": "机密"}, {"target": ""},
    {"recipient_sha256": "A" * 64}, {"plan_sha256": "x" * 64},
    {"registry_sha256": None}, {"plaintext_length": True}, {"plaintext_length": 1.0},
    {"plaintext_length": 0}, {"plaintext_length": 32 * 1024**2 + 1},
    {"kind": "central-archive", "plaintext_length": 32 * 1024**3 + 1},
])
def test_expected_rejects_ambiguous_or_out_of_budget_context(changes):
    with pytest.raises(B2Error, match="^invalid_backup$"):
        expected(**changes)


def test_error_never_carries_private_context():
    assert str(B2Error("PRIVATE SECRET")) == "invalid_backup"
    assert B2Error().code == "invalid_backup"


def test_captured_source_requires_the_seven_fixed_borrowed_fds_without_secret_repr():
    names = ("db/central.dump", "db/identity.dump", "db/central.roles", "db/identity.roles",
             "config/central", "config/identity", "config/media")
    fds = dict.fromkeys(names, 3)
    value = CapturedSource({"PRIVATE_CONTEXT": "not a capture proof"}, 3, fds, (0, 0), "a" * 64, object())
    assert "PRIVATE_CONTEXT" not in repr(value)
    for invalid in ({}, {**fds, "/private/key": 4}, {**fds, "config/media": True}):
        with pytest.raises(B2Error):
            CapturedSource({}, 3, invalid, (0, 0), "a" * 64, object())
