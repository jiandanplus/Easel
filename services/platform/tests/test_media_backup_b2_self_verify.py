"""T11 structural/authentication seam; facts validation is independently required.

The temporary validator below only isolates streaming/content behavior. It is
never production authorization or evidence of actual snapshot/facts capture.
"""

import copy
import hashlib
import io

import pytest

from media_backup.closure import write_archive
from media_backup.contract import encode_manifest, physical_size
from media_backup_fixture import capture
from media_backup_b2_fixture import BACKUP_ID, TARGET, synthetic_keys
from platform_service.media_backup_b2.contracts import B2Error, ExpectedV2
from platform_service.media_backup_b2.stream import V2Decoder, V2Encoder
from platform_service.media_backup_b2 import self_verify


def encrypt_archive(plan, raw):
    public, private, recipient = synthetic_keys()
    plan_sha = hashlib.sha256(encode_manifest(plan)).hexdigest()
    expected = ExpectedV2(
        BACKUP_ID,
        "central-archive",
        TARGET,
        recipient,
        len(raw),
        plan_sha,
        plan["registry_sha256"],
    )
    out = io.BytesIO()
    encoder = V2Encoder(public, expected, out)
    encoder.write(raw)
    receipt = encoder.finish(
        dict(
            status="closure_files_verified",
            file_count=7 + sum(b["present"] for b in plan["blobs"]),
            blob_count=sum(b["present"] for b in plan["blobs"]),
            archive_bytes=len(raw),
            archive_sha256=hashlib.sha256(raw).hexdigest(),
            manifest_sha256=plan_sha,
            registry_sha256=plan["registry_sha256"],
        )
    )
    return private, expected, out.getvalue(), receipt


def archive_for(source):
    output = io.BytesIO()
    receipt = write_archive(
        source.plan, source.media_fd, source.fds, output, owner=source.owner
    )
    return output.getvalue(), receipt


@pytest.fixture
def structural_validator(monkeypatch):
    seen = []

    def validate(bundles, records, plan):
        seen.append((dict(bundles), copy.deepcopy(records), copy.deepcopy(plan)))
        return {"source_facts_sha256": plan["snapshots"]["source_sha256"]}

    monkeypatch.setattr(self_verify, "_verify_bundles", validate)
    return seen


def test_stream_verifies_every_member_then_auth_eof_and_fixed_facts_validator(
    tmp_path, structural_validator
):
    with capture(
        tmp_path / "capture", [(b"blob", "attached", True), (b"old", "deleted", False)]
    ) as source:
        raw, original = archive_for(source)
        private, expected, cipher, final = encrypt_archive(source.plan, raw)
        decoder = V2Decoder(private, io.BytesIO(cipher), expected)
        checks = []
        result = self_verify.verify_archive_content(
            decoder, source.plan, check=lambda: checks.append(1)
        )
        assert result == {
            "status": "archive_content_verified",
            "file_count": 8,
            "blob_count": 1,
            "archive_bytes": physical_size(source.plan),
            "archive_sha256": original["archive_sha256"],
            "plan_sha256": final["plan_sha256"],
            "registry_sha256": source.plan["registry_sha256"],
            "source_facts_sha256": source.plan["snapshots"]["source_sha256"],
        }
        assert decoder.verified and len(checks) > 10
        bundles, records, plan = structural_validator[0]
        assert bundles == {
            name: ("synthetic:" + name).encode()
            for name in ("config/central", "config/identity", "config/media")
        }
        assert records == source.plan["files"] and plan == source.plan
        assert len(structural_validator) == 1


@pytest.mark.parametrize("fault", ["member", "header", "manifest", "padding", "footer"])
def test_authenticated_but_corrupt_content_never_reaches_facts(
    tmp_path, structural_validator, fault
):
    with capture(tmp_path / "capture") as source:
        raw, _ = archive_for(source)
        changed = bytearray(raw)
        if fault == "member":
            changed[raw.index(b"synthetic:db/central.dump")] ^= 1
        if fault == "header":
            changed[0] ^= 1
        if fault == "manifest":
            changed[raw.index(b"easel-media-closure-v1")] ^= 1
        if fault == "padding":
            pos = raw.index(b"synthetic:db/central.dump") + len(
                b"synthetic:db/central.dump"
            )
            changed[pos] = 1
        if fault == "footer":
            changed[-1] = 1
        private, expected, cipher, _ = encrypt_archive(source.plan, bytes(changed))
        decoder = V2Decoder(private, io.BytesIO(cipher), expected)
        with pytest.raises(B2Error, match="^invalid_backup$"):
            self_verify.verify_archive_content(decoder, source.plan)
        assert not structural_validator


@pytest.mark.parametrize(
    "fault", ["no_final", "tamper_final", "cipher_trailing", "cipher_io"]
)
def test_full_plain_archive_requires_final_and_cipher_eof(
    tmp_path, structural_validator, fault
):
    with capture(tmp_path / "capture") as source:
        raw, _ = archive_for(source)
        private, expected, cipher, _ = encrypt_archive(source.plan, raw)
        if fault == "no_final":
            cipher = cipher[:-1]
        if fault == "tamper_final":
            cipher = cipher[:-1] + bytes([cipher[-1] ^ 1])
        if fault == "cipher_trailing":
            cipher += b"x"

        class EofError(io.BytesIO):
            def read(self, n):
                if self.tell() == len(cipher):
                    raise OSError("synthetic private detail")
                return super().read(n)

        stream = EofError(cipher) if fault == "cipher_io" else io.BytesIO(cipher)
        decoder = V2Decoder(private, stream, expected)
        with pytest.raises(B2Error, match="^invalid_backup$"):
            self_verify.verify_archive_content(decoder, source.plan)
        assert not decoder.verified and not structural_validator


def test_guard_expiry_after_authenticated_eof_prevents_receipt(
    tmp_path, structural_validator
):
    with capture(tmp_path / "capture") as source:
        raw, _ = archive_for(source)
        private, expected, cipher, _ = encrypt_archive(source.plan, raw)
        decoder = V2Decoder(private, io.BytesIO(cipher), expected)

        def check():
            if decoder.verified:
                raise TimeoutError("synthetic deadline")

        with pytest.raises(B2Error, match="^invalid_backup$"):
            self_verify.verify_archive_content(decoder, source.plan, check=check)
        assert decoder.verified and not structural_validator


def test_cancellation_from_guard_is_not_sanitized_into_success(
    tmp_path, structural_validator
):
    import asyncio

    with capture(tmp_path / "capture") as source:
        raw, _ = archive_for(source)
        private, expected, cipher, _ = encrypt_archive(source.plan, raw)
        decoder = V2Decoder(private, io.BytesIO(cipher), expected)

        def check():
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            self_verify.verify_archive_content(decoder, source.plan, check=check)
        assert not decoder.verified and not structural_validator


def test_raw_plain_stream_cannot_supply_crypto_verification(
    tmp_path, structural_validator
):
    with capture(tmp_path / "capture") as source:
        raw, _ = archive_for(source)
        with pytest.raises(B2Error):
            self_verify.verify_archive_content(io.BytesIO(raw), source.plan)
        assert not structural_validator


def test_bundle_validation_failure_and_wrong_fact_digest_never_accept(
    tmp_path, monkeypatch
):
    with capture(tmp_path / "capture") as source:
        raw, _ = archive_for(source)
        private, expected, cipher, _ = encrypt_archive(source.plan, raw)
        for validate in (
            lambda *args: {"source_facts_sha256": "f" * 64},
            lambda *args: (_ for _ in ()).throw(ValueError("private config")),
        ):
            monkeypatch.setattr(self_verify, "_verify_bundles", validate)
            with pytest.raises(B2Error, match="^invalid_backup$"):
                self_verify.verify_archive_content(
                    V2Decoder(private, io.BytesIO(cipher), expected), source.plan
                )


def test_declared_bundle_limit_checked_before_body_collection(
    tmp_path, monkeypatch, structural_validator
):
    with capture(tmp_path / "capture") as source:
        raw, _ = archive_for(source)
        private, expected, cipher, _ = encrypt_archive(source.plan, raw)
        monkeypatch.setattr(self_verify, "MAX_BUNDLES_BYTES", 1)
        with pytest.raises(B2Error):
            self_verify.verify_archive_content(
                V2Decoder(private, io.BytesIO(cipher), expected), source.plan
            )
        assert not structural_validator
