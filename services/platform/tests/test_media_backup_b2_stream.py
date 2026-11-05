import base64
from dataclasses import replace
import hashlib
import io
import json
import struct
import tracemalloc

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
import nacl.bindings.crypto_secretstream as sodium
import pytest

from media_backup_b2_fixture import BACKUP_ID, TARGET, ShortReader, ShortWriter, small_plan, synthetic_keys
from platform_service.media_backup_b2.contracts import B2Error, ExpectedV2
from platform_service.media_backup_b2.stream import V2Encoder, V2Decoder
from platform_service.media_backup_b2 import wire


def expected(raw, *, kind="central-context"):
    return ExpectedV2(BACKUP_ID, kind, TARGET, synthetic_keys()[2], len(raw),
                      hashlib.sha256(raw).hexdigest() if kind == "central-context" else "b" * 64, "c" * 64)


def receipt(value, raw):
    if value.kind == "central-context":
        return dict(status="context_plan_verified", plaintext_length=len(raw),
                    plaintext_sha256=hashlib.sha256(raw).hexdigest(), plan_sha256=value.plan_sha256,
                    registry_sha256=value.registry_sha256)
    return dict(status="closure_files_verified", file_count=7, blob_count=0, archive_bytes=len(raw),
                archive_sha256=hashlib.sha256(raw).hexdigest(), manifest_sha256=value.plan_sha256,
                registry_sha256=value.registry_sha256)


def sealed(raw, *, value=None, sink=None):
    value = value or expected(raw)
    sink = sink if sink is not None else io.BytesIO()
    encoder = V2Encoder(synthetic_keys()[0], value, sink)
    assert encoder.write(raw) == len(raw)
    result = encoder.finish(receipt(value, raw))
    return sink.getvalue(), value, result


def drain(decoder, size=1048576):
    output = bytearray()
    while True:
        raw = decoder.read(size)
        if raw == b"": return bytes(output)
        output.extend(raw)


def forged(raw, *, value=None, chunks=None, tags=None, final_changes=None, seq=None, extra_final_keys=None,
           aad_mode=None, final_tag=3):
    """Independent low-level writer, no product encoder or AAD helper."""
    value = value or expected(raw)
    key = sodium.crypto_secretstream_xchacha20poly1305_keygen()
    state = sodium.crypto_secretstream_xchacha20poly1305_state()
    stream_header = sodium.crypto_secretstream_xchacha20poly1305_init_push(state, key)
    public = serialization.load_pem_public_key(synthetic_keys()[0])
    wrapped = public.encrypt(key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=b"EASELBS\x02"))
    header = dict(version=2, algorithm=wire.ALGORITHM, backup_id=value.backup_id, kind=value.kind,
                  target=value.target, recipient_sha256=value.recipient_sha256,
                  plaintext_length=value.plaintext_length, chunk_bytes=1048576,
                  wrapped_key=base64.b64encode(wrapped).decode(), stream_header=base64.b64encode(stream_header).decode())
    encode = lambda obj: json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    header_raw = encode(header)
    prefix = b"EASELBS\x02" + struct.pack(">I", len(header_raw)) + header_raw
    digest = hashlib.sha256(prefix).digest()
    chunks = chunks if chunks is not None else [raw[p:p+1048576] for p in range(0, len(raw), 1048576)]
    final = dict(plaintext_length=len(raw), plaintext_sha256=hashlib.sha256(raw).hexdigest(),
                 plan_sha256=value.plan_sha256, registry_sha256=value.registry_sha256, data_records=len(chunks))
    final.update(final_changes or {})
    final.update(extra_final_keys or {})
    result = bytearray(prefix)
    for index, plain in enumerate([*chunks, encode(final)]):
        tag = (tags[index] if tags and index < len(tags) else 0) if index < len(chunks) else final_tag
        number = index if seq is None else seq[index]
        frame = struct.pack(">QI", number, len(plain) + 17)
        aad = b"easel-b2-record-v2\0" + digest + frame
        if aad_mode == "hex": aad = b"easel-b2-record-v2\0" + digest.hex().encode() + frame
        if aad_mode == "json": aad = b"easel-b2-record-v2\0" + digest + encode({"sequence": number, "length": len(plain)+17})
        if aad_mode == "domain": aad = b"easel-wrong-domain\0" + digest + frame
        result.extend(frame + sodium.crypto_secretstream_xchacha20poly1305_push(state, plain, aad, tag))
    return bytes(result), value


def test_actual_small_b1_context_roundtrips_without_padding_and_explicit_final():
    plan, raw = small_plan()
    value = replace(expected(raw), registry_sha256=plan["registry_sha256"])
    sink = ShortWriter(13)
    cipher, _, result = sealed(raw, value=value, sink=sink)
    assert cipher.startswith(b"EASELBS\x02")
    assert result["plaintext_length"] == len(raw) < 1048576
    assert result["plaintext_sha256"] == hashlib.sha256(raw).hexdigest()
    source = ShortReader(cipher, 7)
    decoder = V2Decoder(synthetic_keys()[1], source, value)
    with pytest.raises(io.UnsupportedOperation): decoder.fileno()
    assert drain(decoder, 31) == raw
    assert decoder.read(1) == b""
    assert not source.closed and not sink.closed
    assert max(source.requests) <= 1048576 + 17


def test_independent_sodium_writer_and_reader_interoperate():
    raw = b"synthetic context"
    cipher, value = forged(raw)
    assert drain(V2Decoder(synthetic_keys()[1], io.BytesIO(cipher), value)) == raw
    written, value, _ = sealed(raw)
    hlen = struct.unpack(">I", written[8:12])[0]
    header = json.loads(written[12:12+hlen])
    private = serialization.load_pem_private_key(synthetic_keys()[1], None)
    key = private.decrypt(base64.b64decode(header["wrapped_key"]), padding.OAEP(
        mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=b"EASELBS\x02"))
    state = sodium.crypto_secretstream_xchacha20poly1305_state()
    sodium.crypto_secretstream_xchacha20poly1305_init_pull(state, base64.b64decode(header["stream_header"]), key)
    digest = hashlib.sha256(written[:12+hlen]).digest()
    offset, records = 12+hlen, []
    while offset < len(written):
        prefix = written[offset:offset+12]
        _, size = struct.unpack(">QI", prefix)
        offset += 12
        plain, tag = sodium.crypto_secretstream_xchacha20poly1305_pull(state, written[offset:offset+size],
            b"easel-b2-record-v2\0" + digest + prefix)
        offset += size
        records.append((plain, tag))
    assert records[0] == (raw, 0)
    assert records[1][1] == 3 and json.loads(records[1][0])["plaintext_sha256"] == hashlib.sha256(raw).hexdigest()


def test_fixed_synthetic_known_answer_vector_and_authenticated_corruption():
    from media_backup_b2_fixture import (KNOWN_V2_PRIVATE_PEM, KNOWN_V2_CIPHERTEXT_B64,
        KNOWN_V2_CIPHERTEXT_SHA256, KNOWN_V2_RECIPIENT_SHA256, KNOWN_V2_PLAINTEXT)
    cipher = base64.b64decode(KNOWN_V2_CIPHERTEXT_B64, validate=True)
    assert hashlib.sha256(cipher).hexdigest() == KNOWN_V2_CIPHERTEXT_SHA256
    value = ExpectedV2(BACKUP_ID, "central-context", TARGET, KNOWN_V2_RECIPIENT_SHA256,
        len(KNOWN_V2_PLAINTEXT), hashlib.sha256(KNOWN_V2_PLAINTEXT).hexdigest(), "c" * 64)
    decoder = V2Decoder(KNOWN_V2_PRIVATE_PEM, io.BytesIO(cipher), value)
    assert drain(decoder) == KNOWN_V2_PLAINTEXT and decoder.verified
    corrupted = cipher[:-1] + bytes([cipher[-1] ^ 1])
    with pytest.raises(B2Error): drain(V2Decoder(KNOWN_V2_PRIVATE_PEM, io.BytesIO(corrupted), value))


@pytest.mark.parametrize("size", [1, 1048575, 1048576, 1048577, 2097152])
def test_chunk_boundaries_and_one_block_buffer(size):
    raw = b"q" * size
    sink = io.BytesIO()
    value = expected(raw)
    encoder = V2Encoder(synthetic_keys()[0], value, sink)
    for pos in range(0, len(raw), 13001):
        block = raw[pos:pos+13001]
        assert encoder.write(memoryview(block)) == len(block)
        assert encoder.buffered_plaintext <= 1048576
    encoder.finish(receipt(value, raw))
    decoder = V2Decoder(synthetic_keys()[1], io.BytesIO(sink.getvalue()), value)
    assert drain(decoder, 17321) == raw
    assert decoder.buffered_plaintext <= 1048576


@pytest.mark.parametrize("changes", [{"backup_id": "22345678-1234-4234-9234-123456789abc"},
    {"kind": "central-archive"}, {"target": "another"}, {"recipient_sha256": "d" * 64},
    {"plaintext_length": 20}, {"plan_sha256": "d" * 64}, {"registry_sha256": "d" * 64}])
def test_independently_expected_context_mismatch_never_falls_back(changes):
    cipher, value, _ = sealed(b"synthetic context")
    with pytest.raises(B2Error):
        drain(V2Decoder(synthetic_keys()[1], io.BytesIO(cipher), replace(value, **changes)))


@pytest.mark.parametrize("tag", [1, 2, 3])
def test_data_push_rekey_or_premature_final_tag_is_rejected(tag):
    cipher, value = forged(b"synthetic", tags=[tag])
    with pytest.raises(B2Error): drain(V2Decoder(synthetic_keys()[1], io.BytesIO(cipher), value))


@pytest.mark.parametrize("mode", ["hex", "json", "domain"])
def test_authenticated_wrong_aad_representation_is_rejected(mode):
    cipher, value = forged(b"synthetic context", aad_mode=mode)
    with pytest.raises(B2Error): drain(V2Decoder(synthetic_keys()[1], io.BytesIO(cipher), value))


@pytest.mark.parametrize("tag", [0, 1, 2])
def test_final_record_must_have_final_tag_even_when_json_authenticates(tag):
    cipher, value = forged(b"synthetic context", final_tag=tag)
    with pytest.raises(B2Error): drain(V2Decoder(synthetic_keys()[1], io.BytesIO(cipher), value))


@pytest.mark.parametrize("fault", ["short_data", "zero_data", "duplicate", "skip", "bad_hash", "bad_count", "bool_count", "extra_final"])
def test_authenticated_but_invalid_frames_or_final_are_rejected(fault):
    raw = b"z" * (1048576 + 5)
    kwargs = {}
    if fault == "short_data": kwargs["chunks"] = [raw[:3], raw[3:]]
    if fault == "zero_data": kwargs["chunks"] = [b"", raw]
    if fault == "duplicate": kwargs["seq"] = [0, 0, 2]
    if fault == "skip": kwargs["seq"] = [0, 2, 3]
    if fault == "bad_hash": kwargs["final_changes"] = {"plaintext_sha256": "a" * 64}
    if fault == "bad_count": kwargs["final_changes"] = {"data_records": 3}
    if fault == "bool_count": kwargs["final_changes"] = {"data_records": True}
    if fault == "extra_final": kwargs["extra_final_keys"] = {"host_path": "/private/never"}
    cipher, value = forged(raw, **kwargs)
    with pytest.raises(B2Error): drain(V2Decoder(synthetic_keys()[1], io.BytesIO(cipher), value))


@pytest.mark.parametrize("fault", ["header", "seq", "length", "cipher", "final", "append_zero", "append_record"])
def test_tampering_aad_or_trailing_ciphertext_never_yields_eof(fault):
    cipher, value, _ = sealed(b"synthetic context")
    hlen = struct.unpack(">I", cipher[8:12])[0]
    start = 12 + hlen
    changed = bytearray(cipher)
    offsets = {"header": 16, "seq": start+7, "length": start+11, "cipher": start+12, "final": len(changed)-1}
    if fault in offsets: changed[offsets[fault]] ^= 1
    elif fault == "append_zero": changed.extend(b"\0")
    else: changed.extend(cipher[start:])
    with pytest.raises(B2Error): drain(V2Decoder(synthetic_keys()[1], io.BytesIO(bytes(changed)), value))


def test_every_structural_truncation_and_missing_final_is_failure_not_plain_eof():
    cipher, value, _ = sealed(b"synthetic context")
    hlen = struct.unpack(">I", cipher[8:12])[0]
    start = 12 + hlen
    size = struct.unpack(">I", cipher[start+8:start+12])[0]
    for length in (0, 7, 8, 11, 12, start-1, start, start+11, start+12, start+12+size, len(cipher)-1):
        with pytest.raises(B2Error): drain(V2Decoder(synthetic_keys()[1], io.BytesIO(cipher[:length]), value))


def test_bounded_lengths_checked_before_reading_or_allocating_body():
    raw = b"x"
    value = expected(raw)
    source = ShortReader(b"EASELBS\x02" + struct.pack(">I", 8193), 10000)
    with pytest.raises(B2Error): V2Decoder(synthetic_keys()[1], source, value)
    assert max(source.requests) <= 12
    cipher, _, _ = sealed(raw)
    hlen = struct.unpack(">I", cipher[8:12])[0]
    offset = 12 + hlen
    source = ShortReader(cipher[:offset] + struct.pack(">QI", 0, 2**32-1), 10000)
    with pytest.raises(B2Error): drain(V2Decoder(synthetic_keys()[1], source, value))
    assert max(source.requests) <= 8192


@pytest.mark.parametrize("fault", [0, True, None, -1, "io_error"])
def test_bad_cipher_sink_counts_or_errors_are_sanitized(fault):
    class Sink:
        def write(self, raw):
            if fault == "io_error": raise OSError("PRIVATE_KEY_DETAIL")
            return fault
    with pytest.raises(B2Error, match="^invalid_backup$"):
        V2Encoder(synthetic_keys()[0], expected(b"x"), Sink())


@pytest.mark.parametrize("result", [None, True, b"", "PRIVATE", b"x" * 9000])
def test_bad_cipher_source_output_is_sanitized(result):
    class Source:
        def read(self, size): return result
    with pytest.raises(B2Error, match="^invalid_backup$"):
        V2Decoder(synthetic_keys()[1], Source(), expected(b"x"))


def test_close_does_not_finish_and_bad_receipt_poisoning_prevents_late_success():
    raw = b"synthetic"
    value = expected(raw)
    sink = io.BytesIO()
    encoder = V2Encoder(synthetic_keys()[0], value, sink)
    encoder.write(raw)
    before = sink.getvalue()
    encoder.close()
    assert sink.getvalue() == before
    with pytest.raises(B2Error): encoder.finish(receipt(value, raw))
    encoder = V2Encoder(synthetic_keys()[0], value, io.BytesIO())
    encoder.write(raw)
    invalid = receipt(value, raw)
    invalid["plan_sha256"] = "e" * 64
    with pytest.raises(B2Error): encoder.finish(invalid)
    with pytest.raises(B2Error): encoder.finish(receipt(value, raw))


def test_rsa_key_bounds_and_recipient_wrapping_are_enforced():
    value = expected(b"x")
    for key in (rsa.generate_private_key(public_exponent=65537, key_size=2048),
                rsa.generate_private_key(public_exponent=3, key_size=3072),
                ec.generate_private_key(ec.SECP256R1())):
        public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        with pytest.raises(B2Error): V2Encoder(public, value, io.BytesIO())
    with pytest.raises(B2Error): V2Encoder(synthetic_keys()[0] + b"PRIVATE_TRAILING", value, io.BytesIO())
    cipher1, _, _ = sealed(b"x")
    cipher2, _, _ = sealed(b"x")
    assert cipher1 != cipher2
    with pytest.raises(B2Error): V2Decoder(synthetic_keys()[1] + b"TRAIL", io.BytesIO(cipher1), value)


def test_allowed_rsa_3080_modulus_length_is_checked_against_private_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=3080)
    assert key.key_size == 3080
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    der = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    raw = b"synthetic 385-byte RSA modulus"
    value = replace(expected(raw), recipient_sha256=hashlib.sha256(der).hexdigest())
    sink = io.BytesIO()
    encoder = V2Encoder(public, value, sink)
    encoder.write(raw)
    encoder.finish(receipt(value, raw))
    assert drain(V2Decoder(private, io.BytesIO(sink.getvalue()), value)) == raw


def test_actual_disk_roundtrip_above_32mib_has_bounded_python_buffers(tmp_path, record_property):
    length = 32 * 1048576 + 123
    value = ExpectedV2(BACKUP_ID, "central-archive", TARGET, synthetic_keys()[2], length, "b" * 64, "c" * 64)
    path = tmp_path / "synthetic.easelbs2"
    block, digest = b"p" * 1048576, hashlib.sha256()
    tracemalloc.start()
    try:
        with path.open("wb", buffering=0) as sink:
            encoder = V2Encoder(synthetic_keys()[0], value, sink)
            remaining = length
            while remaining:
                piece = block[:min(remaining, len(block))]
                assert encoder.write(piece) == len(piece)
                digest.update(piece)
                remaining -= len(piece)
                assert encoder.buffered_plaintext <= 1048576
            encoder.finish(dict(status="closure_files_verified", file_count=7, blob_count=0,
                archive_bytes=length, archive_sha256=digest.hexdigest(), manifest_sha256=value.plan_sha256,
                registry_sha256=value.registry_sha256))
        assert path.stat().st_size > 32 * 1048576
        actual, count = hashlib.sha256(), 0
        with path.open("rb", buffering=0) as source:
            decoder = V2Decoder(synthetic_keys()[1], source, value)
            while True:
                piece = decoder.read(1048576)
                if not piece: break
                actual.update(piece)
                count += len(piece)
                assert decoder.buffered_plaintext <= 1048576
        _, peak = tracemalloc.get_traced_memory()
        record_property("plaintext_bytes", length)
        record_property("ciphertext_bytes", path.stat().st_size)
        record_property("python_peak_bytes", peak)
        assert count == length and actual.hexdigest() == digest.hexdigest()
        assert peak < 16 * 1048576, f"whole-archive buffering detected: {peak}"
    finally:
        tracemalloc.stop()


def test_real_b1_empty_logical_media_archive_and_private_stage_authentication_seam(tmp_path):
    from media_backup_fixture import capture
    from media_backup.contract import physical_size, encode_manifest
    from media_backup.closure import write_archive, restore_archive
    from media_backup.contract import ClosureError
    with capture(tmp_path / "capture") as source:
        raw_plan = encode_manifest(source.plan)
        value = ExpectedV2(BACKUP_ID, "central-archive", TARGET, synthetic_keys()[2],
            physical_size(source.plan), hashlib.sha256(raw_plan).hexdigest(), source.plan["registry_sha256"])
        sink = io.BytesIO()
        encoder = V2Encoder(synthetic_keys()[0], value, sink)
        b1 = write_archive(source.plan, source.media_fd, source.fds, encoder, owner=source.owner)
        final = encoder.finish(b1)
        assert final["plaintext_sha256"] == b1["archive_sha256"]
        decoder = V2Decoder(synthetic_keys()[1], io.BytesIO(sink.getvalue()), value)
        result = restore_archive(decoder, source.parent_fd, "verified", source.plan)
        assert result["status"] == "private_stage_restored_verified" and decoder.verified
        assert decoder.receipt == final
        invalid = sink.getvalue()[:-1]
        decoder = V2Decoder(synthetic_keys()[1], io.BytesIO(invalid), value)
        with pytest.raises(ClosureError): restore_archive(decoder, source.parent_fd, "failed", source.plan)
        assert not decoder.verified
        assert (source.parent / "failed").is_dir()
        assert ((source.parent / "failed").stat().st_mode & 0o777) == 0o700


def test_final_and_cipher_eof_are_required_before_empty_plaintext_or_receipt():
    raw = b"small synthetic context"
    cipher, value, final = sealed(raw)
    decoder = V2Decoder(synthetic_keys()[1], io.BytesIO(cipher), value)
    assert decoder.read(100) == raw
    assert not decoder.verified
    with pytest.raises(B2Error): _ = decoder.receipt
    assert decoder.read(1) == b"" and decoder.verified and decoder.receipt == final
    for size in (0, -1, True):
        decoder = V2Decoder(synthetic_keys()[1], io.BytesIO(cipher), value)
        with pytest.raises(B2Error): decoder.read(size)
        with pytest.raises(B2Error): decoder.read(1)


@pytest.mark.parametrize("field,bad", [("status", "closure_files_verified"), ("plaintext_length", True),
    ("plaintext_sha256", "a" * 64), ("registry_sha256", "a" * 64), ("extra", "PRIVATE")])
def test_explicit_context_receipt_does_not_accept_archive_aliases_or_self_report(field, bad):
    raw = b"x"
    value = expected(raw)
    encoder = V2Encoder(synthetic_keys()[0], value, io.BytesIO())
    encoder.write(raw)
    claimed = receipt(value, raw)
    claimed[field] = bad
    with pytest.raises(B2Error): encoder.finish(claimed)
    with pytest.raises(B2Error): encoder.finish(receipt(value, raw))


def test_cancelled_cipher_write_poisoning_never_adds_final_or_allows_late_finish():
    import asyncio
    class CancelledSink(io.BytesIO):
        cancel = False
        def write(self, raw):
            if self.cancel: raise asyncio.CancelledError()
            return super().write(raw)
    raw = b"z" * 1048576
    sink = CancelledSink()
    value = expected(raw)
    encoder = V2Encoder(synthetic_keys()[0], value, sink)
    before = sink.getvalue()
    sink.cancel = True
    with pytest.raises(asyncio.CancelledError): encoder.write(raw)
    assert encoder.buffered_plaintext == 0 and sink.getvalue() == before
    sink.cancel = False
    with pytest.raises(B2Error): encoder.finish(receipt(value, raw))
