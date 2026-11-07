import builtins
import hashlib
import io
import json
import tarfile
import types

import pytest

from media_backup_b2_fixture import BACKUP_ID, synthetic_keys
from platform_service.media_backup_b2.contracts import B2Error
from platform_service.media_backup_b2 import legacy


def frozen_helper():
    raw = legacy._resource_bytes("sealed_transfer.py")
    assert hashlib.sha256(raw).hexdigest() == "7dd81fbc97d60c782fe2d2c43d81e37fc0b4ea926504e66309a43abc144ad7d6"
    module = types.ModuleType("synthetic_legacy_fixture")
    exec(compile(raw, "synthetic-fixed-legacy", "exec"), module.__dict__)
    return module


def old_payload():
    entries = {"databases/central.dump": b"PGDMP synthetic fixture", "roles/central.sql": b"-- synthetic role"}
    manifest = dict(format="synthetic-legacy", files=[dict(name=name, length=len(raw),
                    sha256=hashlib.sha256(raw).hexdigest()) for name, raw in sorted(entries.items())])
    entries["manifest.json"] = json.dumps(manifest, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as target:
        for name, raw in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            target.addfile(info, io.BytesIO(raw))
    return output.getvalue(), manifest


def legacy_cipher():
    payload, manifest = old_payload()
    envelope = frozen_helper().seal(synthetic_keys()[0], payload,
        dict(transfer_id=BACKUP_ID, kind="central", target="ser362540173134",
             expected_sha256=hashlib.sha256(payload).hexdigest(), expected_length=len(payload)))
    return envelope, manifest


def test_fixed_legacy_v1_roundtrip_runs_only_unseal_then_frozen_payload_verification():
    envelope, manifest = legacy_cipher()
    assert legacy.verify_legacy(synthetic_keys()[1], envelope, BACKUP_ID, "central", "ser362540173134", manifest) is None
    assert legacy.format_for_magic(b"EASELST\x01") == "v1"
    assert legacy.format_for_magic(b"EASELBS\x02") == "v2"


@pytest.mark.parametrize("fault", ["id", "kind", "target", "auth", "truncated", "trailing", "manifest", "v2", "unknown", "oversize"])
def test_legacy_wrong_context_auth_inner_or_size_has_no_fallback(fault):
    envelope, manifest = legacy_cipher()
    transfer, kind, target = BACKUP_ID, "central", "ser362540173134"
    if fault == "id": transfer = "22345678-1234-4234-9234-123456789abc"
    if fault == "kind": kind = "logto"
    if fault == "target": target = "another"
    if fault == "auth": envelope = envelope[:-1] + bytes([envelope[-1] ^ 1])
    if fault == "truncated": envelope = envelope[:-1]
    if fault == "trailing": envelope += b"\0"
    if fault == "manifest": manifest = {**manifest, "unexpected": True}
    if fault == "v2": envelope = b"EASELBS\x02" + envelope[8:]
    if fault == "unknown": envelope = b"UNKNOWN!" + envelope[8:]
    if fault == "oversize": envelope = b"EASELST\x01" + b"x" * (33 * 1024**2)
    with pytest.raises(B2Error, match="^invalid_backup$"):
        legacy.verify_legacy(synthetic_keys()[1], envelope, transfer, kind, target, manifest)


def test_changed_fixed_resource_is_rejected_before_execution(monkeypatch):
    envelope, manifest = legacy_cipher()
    monkeypatch.setattr(builtins, "_b2_fixture_executed", False, raising=False)
    monkeypatch.setattr(legacy, "_resource_bytes", lambda name:
        b"import builtins;builtins._b2_fixture_executed=True\n")
    with pytest.raises(B2Error):
        legacy.verify_legacy(synthetic_keys()[1], envelope, BACKUP_ID, "central", "ser362540173134", manifest)
    assert builtins._b2_fixture_executed is False


def test_magic_dispatch_requires_exact_eight_bytes():
    for raw in (b"", b"EASELST\x02", b"EASELBS\x01", b"EASELST\x01x", bytearray(b"EASELST\x01")):
        with pytest.raises(B2Error): legacy.format_for_magic(raw)


def test_v2_auth_failure_cannot_enter_legacy_adapter(monkeypatch):
    from platform_service.media_backup_b2.stream import V2Decoder
    from platform_service.media_backup_b2.contracts import ExpectedV2
    envelope, _ = legacy_cipher()
    monkeypatch.setattr(legacy, "_resource_bytes", lambda name: pytest.fail("v2 reader attempted legacy fallback"))
    expected = ExpectedV2(BACKUP_ID, "central-context", "synthetic", synthetic_keys()[2], 1, "b" * 64, "c" * 64)
    with pytest.raises(B2Error): V2Decoder(synthetic_keys()[1], io.BytesIO(envelope), expected)
