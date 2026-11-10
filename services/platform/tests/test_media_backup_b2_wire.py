import base64
import hashlib
import json
import struct
import pytest

from media_backup_b2_fixture import header, final, commit, small_plan
from platform_service.media_backup_b2.contracts import B2Error
from platform_service.media_backup_b2 import wire


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def test_exact_codecs_and_binary_aad():
    for value, encode, decode in [(header(), wire.encode_header, wire.decode_header),
                                  (final(), wire.encode_final, wire.decode_final),
                                  (commit(), wire.encode_commit, wire.decode_commit)]:
        raw = encode(value)
        assert raw == canonical(value) and decode(raw) == value
    prefix = wire.header_prefix(header())
    assert prefix == b"EASELBS\x02" + struct.pack(">I", len(canonical(header()))) + canonical(header())
    record = wire.record_prefix(7, 1048593)
    assert record == struct.pack(">QI", 7, 1048593)
    digest = hashlib.sha256(prefix).digest()
    assert wire.record_aad(digest, record) == b"easel-b2-record-v2\0" + digest + record


@pytest.mark.parametrize("field,value", [
    ("version", True), ("version", 2.0), ("version", 1), ("algorithm", "fallback"),
    ("backup_id", "12345678-1234-1234-9234-123456789abc"), ("target", "/root/key"),
    ("target", "隐私"), ("kind", "central"), ("recipient_sha256", "A" * 64),
    ("plaintext_length", 0), ("plaintext_length", True), ("plaintext_length", 32 * 1024**2 + 1),
    ("chunk_bytes", 1024), ("chunk_bytes", True),
    ("wrapped_key", "AAAA"), ("stream_header", base64.b64encode(b"h" * 23).decode()),
    ("stream_header", base64.b64encode(b"h" * 24).decode() + "\n"),
])
def test_header_rejects_wrong_schema_values(field, value):
    changed = header()
    changed[field] = value
    with pytest.raises(B2Error):
        wire.decode_header(canonical(changed))


@pytest.mark.parametrize("kind", ["header", "final", "commit"])
@pytest.mark.parametrize("fault", ["duplicate", "extra", "missing", "whitespace", "nan", "nonascii", "oversized"])
def test_strict_json_parsers_reject_noncanonical_bytes(kind, fault):
    value = {"header": header, "final": final, "commit": commit}[kind]()
    decode = getattr(wire, "decode_" + kind)
    if fault == "duplicate":
        key = next(iter(value))
        raw = canonical(value)[:-1] + b"," + canonical({key: value[key]})[1:]
    elif fault == "extra":
        raw = canonical({**value, "host_path": "/private/secret"})
    elif fault == "missing":
        value.pop(next(iter(value)))
        raw = canonical(value)
    elif fault == "whitespace":
        raw = b" " + canonical(value)
    elif fault == "nan":
        raw = canonical(value).replace(b":2", b":NaN", 1) if kind == "header" else b'{"x":NaN}'
    elif fault == "nonascii":
        raw = b'{"private":"\xff"}'
    else:
        raw = b" " * (16385 if kind == "commit" else 8193 if kind == "header" else 4097)
    with pytest.raises(B2Error, match="^invalid_backup$"):
        decode(raw)


@pytest.mark.parametrize("field,value", [("epoch", True), ("epoch", -1), ("window_id", "x"),
                                          ("plan_sha256", "x" * 64), ("format", "old")])
def test_commit_rejects_untrusted_context(field, value):
    changed = commit()
    changed[field] = value
    with pytest.raises(B2Error):
        wire.decode_commit(canonical(changed))


@pytest.mark.parametrize("fault", ["filename", "bytes_bool", "oversize", "object_extra", "kind_extra"])
def test_commit_never_accepts_arbitrary_object_names_or_caps(fault):
    value = commit()
    if fault == "filename": value["objects"]["archive"]["name"] = "../../root"
    if fault == "bytes_bool": value["objects"]["context"]["bytes"] = True
    if fault == "oversize": value["objects"]["context"]["bytes"] = 32 * 1024**2 + 65537
    if fault == "object_extra": value["objects"]["archive"]["permissions"] = 0o777
    if fault == "kind_extra": value["objects"]["secret"] = value["objects"]["archive"]
    with pytest.raises(B2Error): wire.decode_commit(canonical(value))


def test_small_actual_b1_context_has_no_chunk_minimum_or_padding():
    plan, raw = small_plan()
    assert 1 <= len(raw) < 1048576
    value = header()
    value["plaintext_length"] = len(raw)
    assert wire.decode_header(wire.encode_header(value))["plaintext_length"] == len(raw)


def test_binary_prefix_integer_bounds_and_explicit_rsa_size():
    for seq, size in [(True, 18), (-1, 18), (2**64, 18), (0, True), (0, 0), (0, 1048594)]:
        with pytest.raises(B2Error): wire.record_prefix(seq, size)
    with pytest.raises(B2Error): wire.decode_header(canonical(header()), rsa_bytes=512)
    assert wire.decode_header(canonical(header()), rsa_bytes=384) == header()


@pytest.mark.parametrize("kind,maximum", [("central-context", 32 * 1024**2), ("central-archive", 32 * 1024**3)])
def test_exact_declared_upper_budgets_do_not_allocate_plaintext(kind, maximum):
    value = header()
    value.update(kind=kind, plaintext_length=maximum)
    assert wire.decode_header(wire.encode_header(value))["plaintext_length"] == maximum
    value["plaintext_length"] += 1
    with pytest.raises(B2Error): wire.encode_header(value)
    assert wire.cipher_budget(kind) == maximum + (65536 if kind == "central-context" else 2 * 1024**2 + 8192)


@pytest.mark.parametrize("field,value", [("plaintext_length", True), ("plaintext_length", 0),
    ("data_records", True), ("data_records", 0), ("data_records", 2),
    ("plaintext_sha256", "A" * 64), ("plan_sha256", None), ("registry_sha256", "short")])
def test_final_has_strict_lengths_hashes_and_exact_record_count(field, value):
    changed = final()
    changed[field] = value
    with pytest.raises(B2Error): wire.decode_final(canonical(changed))


def test_standard_base64_reencoding_rejects_nonzero_padding_bits():
    value = header()
    canonical_key = base64.b64encode(b"w" * 385).decode()
    assert canonical_key.endswith("==")
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    index = alphabet.index(canonical_key[-3])
    value["wrapped_key"] = canonical_key[:-3] + alphabet[index + 1] + "=="
    assert base64.b64decode(value["wrapped_key"], validate=True) == b"w" * 385
    with pytest.raises(B2Error): wire.decode_header(canonical(value))
