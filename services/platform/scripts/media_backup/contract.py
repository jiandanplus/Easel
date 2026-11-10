"""Strict, normalized input contract for synthetic/local media archive closure.

The manifest binds caller-supplied snapshots and hashes; it does not authenticate
capture-window consistency or execute/restore a database.
"""
from dataclasses import dataclass, fields
import hashlib
import json
import re


class ClosureError(ValueError):
    """A fixed, sanitized failure category with no capture data attached."""

    code = "invalid_closure"

    def __init__(self, *args):
        super().__init__(self.code)


_DEFAULTS = {
    "max_file_bytes": 4 * 1024**3,
    "max_archive_bytes": 32 * 1024**3,
    "max_members": 100_000,
    "max_manifest_bytes": 32 * 1024**2,
    "chunk_bytes": 1024**2,
}


@dataclass(frozen=True)
class Limits:
    max_file_bytes: int = _DEFAULTS["max_file_bytes"]
    max_archive_bytes: int = _DEFAULTS["max_archive_bytes"]
    max_members: int = _DEFAULTS["max_members"]
    max_manifest_bytes: int = _DEFAULTS["max_manifest_bytes"]
    chunk_bytes: int = _DEFAULTS["chunk_bytes"]

    def __post_init__(self):
        for name, maximum in _DEFAULTS.items():
            value = getattr(self, name)
            if type(value) is not int or not 0 < value <= maximum:
                raise ClosureError()


_FORMAT = "easel-media-closure-v1"
_FILES = frozenset(("db/central.dump", "db/identity.dump", "db/central.roles",
                    "db/identity.roles", "config/central", "config/identity", "config/media"))
_FILE_FIELDS = frozenset(("name", "size", "sha256", "uid", "gid", "mode"))
_REF_FIELDS = frozenset(("key", "tenant_id", "product_id", "operation_id"))
_BLOB_FIELDS = frozenset(("key", "state", "size", "sha256", "uid", "gid", "mode",
                          "tenant_id", "product_id", "operation_id", "present", "missing_evidence_sha256"))
_PLAN_FIELDS = frozenset(("format", "snapshots", "registry_sha256", "files", "blobs", "references"))
_SNAPSHOT_FIELDS = frozenset(("central", "identity", "source_sha256", "schema_sha256"))
_HEX = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_KEY = re.compile(r"(?:[0-9a-f]{32}|[0-9a-f]{64})\Z", re.ASCII)
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z", re.ASCII)


def _require(condition):
    if not condition:
        raise ClosureError()


def _limits(limits):
    _require(type(limits) is Limits)
    # Recheck even a frozen dataclass supplied through an unusual deserializer.
    Limits(**{field.name: getattr(limits, field.name) for field in fields(Limits)})


def _keys(value, expected):
    _require(type(value) is dict and value.keys() == expected)


def _hash(value):
    _require(type(value) is str and _HEX.fullmatch(value) is not None)


def _identifier(value):
    _require(type(value) is str and _ID.fullmatch(value) is not None)


def _integer(value, maximum):
    _require(type(value) is int and 0 <= value <= maximum)


def _canonical_value(value):
    if value is None or type(value) in (bool, str, int):
        return
    if type(value) is list:
        for item in value:
            _canonical_value(item)
        return
    if type(value) is dict:
        for key, item in value.items():
            _require(type(key) is str)
            _canonical_value(item)
        return
    raise ClosureError()


def canonical(value) -> bytes:
    """Encode JSON without floats, arbitrary objects, ambiguous keys or spacing."""
    try:
        _canonical_value(value)
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise ClosureError() from None


def _file_metadata(row, limits, modes):
    _integer(row["size"], limits.max_file_bytes)
    _hash(row["sha256"])
    _integer(row["uid"], 2**32 - 1)
    _integer(row["gid"], 2**32 - 1)
    _require(type(row["mode"]) is int and row["mode"] in modes)


def _normalize_registry(blobs, references, limits):
    _require(type(blobs) is list and type(references) is list)
    # Bound even absent/tombstoned rows before canonical JSON allocation.
    _require(len(blobs) <= limits.max_members and len(references) <= limits.max_members)
    _require(len(blobs) == len(references))
    # Count the exact compact registry length one bounded row at a time.
    # This rejects oversized metadata before allocating the full JSON string.
    metadata_bytes = len(b'{"blobs":[],"references":[]}')
    metadata_bytes += max(0, len(blobs) - 1) + max(0, len(references) - 1)
    _require(metadata_bytes <= limits.max_manifest_bytes)
    by_key = {}
    for row in blobs:
        _keys(row, _BLOB_FIELDS)
        key = row["key"]
        _require(type(key) is str and _KEY.fullmatch(key) is not None and key not in by_key)
        _file_metadata(row, limits, (0o600,))
        for name in ("tenant_id", "product_id", "operation_id"):
            _identifier(row[name])
        _require(type(row["state"]) is str and row["state"] in ("attached", "staged", "deleted", "purged"))
        _require(type(row["present"]) is bool)
        evidence = row["missing_evidence_sha256"]
        if row["state"] == "attached":
            _require(row["present"] and evidence is None)
        elif row["state"] == "staged" and not row["present"]:
            _hash(evidence)
        elif row["state"] == "staged":
            _require(evidence is None)
        else:
            _require(not row["present"] and evidence is None)
        metadata_bytes += len(canonical(row))
        _require(metadata_bytes <= limits.max_manifest_bytes)
        by_key[key] = dict(row)
    refs_by_key = {}
    for row in references:
        _keys(row, _REF_FIELDS)
        key = row["key"]
        _require(type(key) is str and key in by_key and key not in refs_by_key)
        for name in ("tenant_id", "product_id", "operation_id"):
            _identifier(row[name])
            _require(row[name] == by_key[key][name])
        metadata_bytes += len(canonical(row))
        _require(metadata_bytes <= limits.max_manifest_bytes)
        refs_by_key[key] = dict(row)
    _require(by_key.keys() == refs_by_key.keys())
    return ([by_key[key] for key in sorted(by_key)], [refs_by_key[key] for key in sorted(refs_by_key)])


def _registry_sha(blobs, references):
    return hashlib.sha256(canonical({"blobs": blobs, "references": references})).hexdigest()


def registry_digest(blobs, references) -> str:
    """Hash the complete normalized registry, including missing rows/tombstones."""
    normalized = _normalize_registry(blobs, references, Limits())
    return _registry_sha(*normalized)


def _records(plan):
    rows = [dict(row) for row in plan["files"]]
    for blob in plan["blobs"]:
        if blob["present"]:
            rows.append({"name": "media/" + blob["key"], **{field: blob[field] for field in ("size", "sha256", "uid", "gid", "mode")}})
    return sorted(rows, key=lambda row: row["name"])


def _physical(plan, manifest_size):
    align = lambda value: value + (-value) % 512
    size = 512 + align(manifest_size) + 1024
    size += sum(512 + align(row["size"]) for row in _records(plan))
    return size + (-size) % 10240


def validate_plan(plan, limits=Limits()) -> dict:
    """Validate a closed capture inventory and return independent sorted rows."""
    _limits(limits)
    _keys(plan, _PLAN_FIELDS)
    _require(type(plan["format"]) is str and plan["format"] == _FORMAT)
    snapshots = plan["snapshots"]
    _keys(snapshots, _SNAPSHOT_FIELDS)
    for name in ("central", "identity"):
        _identifier(snapshots[name])
    for name in ("source_sha256", "schema_sha256"):
        _hash(snapshots[name])
    _hash(plan["registry_sha256"])
    files = plan["files"]
    _require(type(files) is list and len(files) == len(_FILES))
    files_by_name = {}
    for row in files:
        _keys(row, _FILE_FIELDS)
        name = row["name"]
        _require(type(name) is str and name in _FILES and name not in files_by_name)
        _file_metadata(row, limits, (0o600, 0o644))
        files_by_name[name] = dict(row)
    _require(files_by_name.keys() == _FILES)
    blobs, references = _normalize_registry(plan["blobs"], plan["references"], limits)
    _require(plan["registry_sha256"] == _registry_sha(blobs, references))
    _require(len(files) + sum(row["present"] for row in blobs) <= limits.max_members)
    normalized = {"format": _FORMAT, "snapshots": dict(snapshots), "registry_sha256": plan["registry_sha256"],
                  "files": [files_by_name[name] for name in sorted(files_by_name)], "blobs": blobs, "references": references}
    manifest_size = len(canonical(normalized))
    _require(manifest_size <= limits.max_manifest_bytes)
    _require(_physical(normalized, manifest_size) <= limits.max_archive_bytes)
    return normalized


def encode_manifest(plan, limits=Limits()) -> bytes:
    return canonical(validate_plan(plan, limits))


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        _require(key not in value)
        value[key] = item
    return value


def _no_float(value):
    raise ClosureError()


def decode_manifest(raw, limits=Limits()) -> dict:
    _limits(limits)
    _require(type(raw) is bytes and len(raw) <= limits.max_manifest_bytes)
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                           parse_float=_no_float, parse_constant=_no_float)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ClosureError() from None
    normalized = validate_plan(value, limits)
    _require(canonical(normalized) == raw)
    return normalized


def member_records(plan) -> list[dict]:
    return _records(validate_plan(plan))


def physical_size(plan, limits=Limits()) -> int:
    normalized = validate_plan(plan, limits)
    return _physical(normalized, len(canonical(normalized)))
