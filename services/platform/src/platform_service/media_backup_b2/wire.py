"""V2 canonical public envelope syntax, separate from B1 UTF-8 plan encoding."""
import base64
import json
import struct
from .contracts import (B2Error, MAX_ARCHIVE, MAX_CONTEXT, require, validate_hash,
                        validate_id, validate_int, validate_uuid)

MAGIC = b"EASELBS\x02"
ALGORITHM = "RSA-OAEP-SHA256+SECRETSTREAM-XCHACHA20-POLY1305"
CHUNK_BYTES = 1048576
MAX_HEADER, MAX_FINAL, MAX_COMMIT = 8192, 4096, 16384
ABYTES = 17
_HEADER = frozenset(("version", "algorithm", "backup_id", "kind", "target", "recipient_sha256",
                     "plaintext_length", "chunk_bytes", "wrapped_key", "stream_header"))
_FINAL = frozenset(("plaintext_length", "plaintext_sha256", "plan_sha256", "registry_sha256", "data_records"))
_COMMIT = frozenset(("format", "backup_id", "target", "window_id", "epoch", "plan_sha256", "registry_sha256", "objects"))


def _encode(value):
    def check(item):
        if type(item) is str:
            item.encode("ascii")
        elif type(item) is int:
            pass
        elif type(item) is dict:
            for key, child in item.items():
                require(type(key) is str)
                key.encode("ascii")
                check(child)
        else:
            raise B2Error()
    try:
        check(value)
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise B2Error() from None


def _keys(value, expected):
    require(type(value) is dict and value.keys() == expected)


def _pairs(pairs):
    value = {}
    for key, child in pairs:
        require(key not in value)
        value[key] = child
    return value


def _no_number(value):
    raise B2Error()


def _decode(raw, maximum, validate):
    try:
        require(type(raw) is bytes and 1 <= len(raw) <= maximum)
        value = json.loads(raw.decode("ascii"), object_pairs_hook=_pairs,
                           parse_float=_no_number, parse_constant=_no_number)
        validate(value)
        require(_encode(value) == raw)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise B2Error() from None


def decode_b64(value, length=None):
    try:
        require(type(value) is str and len(value) <= 1400)
        raw = base64.b64decode(value, validate=True)
        require(base64.b64encode(raw).decode("ascii") == value)
        if length is not None:
            require(len(raw) == length)
        return raw
    except (ValueError, TypeError, UnicodeError):
        raise B2Error() from None


def _header(value, rsa_bytes=None):
    _keys(value, _HEADER)
    require(type(value["version"]) is int and value["version"] == 2)
    require(type(value["algorithm"]) is str and value["algorithm"] == ALGORITHM)
    validate_uuid(value["backup_id"])
    validate_id(value["target"])
    require(type(value["kind"]) is str and value["kind"] in ("central-archive", "central-context"))
    validate_hash(value["recipient_sha256"])
    validate_int(value["plaintext_length"], MAX_ARCHIVE if value["kind"] == "central-archive" else MAX_CONTEXT, 1)
    require(type(value["chunk_bytes"]) is int and value["chunk_bytes"] == CHUNK_BYTES)
    wrapped = decode_b64(value["wrapped_key"])
    require(384 <= len(wrapped) <= 1024)
    if rsa_bytes is not None:
        validate_int(rsa_bytes, 1024, 384)
        require(len(wrapped) == rsa_bytes)
    decode_b64(value["stream_header"], 24)


def _final(value):
    _keys(value, _FINAL)
    validate_int(value["plaintext_length"], MAX_ARCHIVE, 1)
    for key in ("plaintext_sha256", "plan_sha256", "registry_sha256"):
        validate_hash(value[key])
    validate_int(value["data_records"], MAX_ARCHIVE // CHUNK_BYTES, 1)
    require(value["data_records"] == (value["plaintext_length"] + CHUNK_BYTES - 1) // CHUNK_BYTES)


def _commit(value):
    _keys(value, _COMMIT)
    require(type(value["format"]) is str and value["format"] == "easel-backup-commit-v2")
    validate_uuid(value["backup_id"])
    validate_uuid(value["window_id"])
    validate_id(value["target"])
    validate_int(value["epoch"], 2**63 - 1)
    validate_hash(value["plan_sha256"])
    validate_hash(value["registry_sha256"])
    _keys(value["objects"], {"archive", "context"})
    for key in ("archive", "context"):
        row = value["objects"][key]
        _keys(row, {"name", "bytes", "sha256"})
        require(type(row["name"]) is str and row["name"] == key + ".easelbs2")
        validate_int(row["bytes"], cipher_budget("central-" + key), 1)
        validate_hash(row["sha256"])


def _validated_encode(value, validate, maximum):
    try:
        validate(value)
        raw = _encode(value)
        require(1 <= len(raw) <= maximum)
        return raw
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        raise B2Error() from None


def encode_header(value, *, rsa_bytes=None):
    return _validated_encode(value, lambda item: _header(item, rsa_bytes), MAX_HEADER)


def decode_header(raw, *, rsa_bytes=None):
    return _decode(raw, MAX_HEADER, lambda item: _header(item, rsa_bytes))


def encode_final(value):
    return _validated_encode(value, _final, MAX_FINAL)


def decode_final(raw):
    return _decode(raw, MAX_FINAL, _final)


def encode_commit(value):
    return _validated_encode(value, _commit, MAX_COMMIT)


def decode_commit(raw):
    return _decode(raw, MAX_COMMIT, _commit)


def header_prefix(value):
    raw = encode_header(value)
    return MAGIC + struct.pack(">I", len(raw)) + raw


def record_prefix(sequence, ciphertext_length):
    validate_int(sequence, 2**64 - 1)
    validate_int(ciphertext_length, CHUNK_BYTES + ABYTES, ABYTES + 1)
    return struct.pack(">QI", sequence, ciphertext_length)


def record_aad(prefix_sha256, raw_record_prefix):
    require(type(prefix_sha256) is bytes and len(prefix_sha256) == 32)
    require(type(raw_record_prefix) is bytes and len(raw_record_prefix) == 12)
    return b"easel-b2-record-v2\0" + prefix_sha256 + raw_record_prefix


def cipher_budget(kind):
    require(type(kind) is str and kind in ("central-archive", "central-context"))
    return MAX_ARCHIVE + 2 * 1024**2 + 8192 if kind == "central-archive" else MAX_CONTEXT + 65536
