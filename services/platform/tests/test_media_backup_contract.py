"""Synthetic behavioral coverage for the bounded archive capture contract."""
import copy
import hashlib
import importlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def test_contract_capability_exists():
    try:
        module = importlib.import_module("media_backup.contract")
    except ModuleNotFoundError:
        module = None
    assert module is not None, "missing strict media_backup.contract capability"


@pytest.fixture
def api():
    try:
        return importlib.import_module("media_backup.contract")
    except ModuleNotFoundError:
        pytest.skip("strict media_backup.contract not implemented yet")


def plan_for(api, states=("attached", "staged", "deleted", "purged")):
    files = [{"name": name, "size": 0, "sha256": hashlib.sha256(b"").hexdigest(),
              "uid": 0, "gid": 0, "mode": 0o600}
             for name in ("db/central.dump", "db/identity.dump", "db/central.roles",
                          "db/identity.roles", "config/central", "config/identity", "config/media")]
    blobs = []
    refs = []
    for i, state in enumerate(states):
        key = f"{i + 1:032x}"
        present = state == "attached"
        blobs.append({"key": key, "state": state, "size": 3, "sha256": hashlib.sha256(b"abc").hexdigest(),
                      "uid": 0, "gid": 0, "mode": 0o600, "tenant_id": "tenant-1", "product_id": "product-1",
                      "operation_id": "operation-1", "present": present,
                      "missing_evidence_sha256": "e" * 64 if state == "staged" else None})
        refs.append({k: blobs[-1][k] for k in ("key", "tenant_id", "product_id", "operation_id")})
    return {"format": "easel-media-closure-v1", "snapshots": {"central": "central-1", "identity": "identity-1",
            "source_sha256": "a" * 64, "schema_sha256": "b" * 64},
            "registry_sha256": api.registry_digest(blobs, refs), "files": files, "blobs": blobs, "references": refs}


def test_canonical_roundtrip_normalizes_without_mutating(api):
    plan = plan_for(api)
    plan["files"].reverse()
    plan["blobs"].reverse()
    plan["references"].reverse()
    original = copy.deepcopy(plan)
    normalized = api.validate_plan(plan)
    assert plan == original
    assert [x["name"] for x in normalized["files"]] == sorted(x["name"] for x in plan["files"])
    assert [x["key"] for x in normalized["blobs"]] == sorted(x["key"] for x in plan["blobs"])
    raw = api.encode_manifest(plan)
    assert api.decode_manifest(raw) == normalized
    assert raw == json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def test_registry_digest_binds_tombstones_and_scope(api):
    plan = plan_for(api)
    expected = hashlib.sha256(api.canonical({"blobs": plan["blobs"], "references": plan["references"]})).hexdigest()
    assert plan["registry_sha256"] == expected
    changed = copy.deepcopy(plan)
    changed["blobs"][-1]["size"] += 1
    assert api.registry_digest(changed["blobs"], changed["references"]) != expected
    assert api.registry_digest(plan["blobs"][::-1], plan["references"][::-1]) == expected


def test_member_inventory_includes_only_present_blobs(api):
    plan = plan_for(api)
    plan["blobs"][1]["present"] = True
    plan["blobs"][1]["missing_evidence_sha256"] = None
    plan["registry_sha256"] = api.registry_digest(plan["blobs"], plan["references"])
    records = api.member_records(plan)
    assert len(records) == 9
    assert [x["name"] for x in records] == sorted(x["name"] for x in records)
    assert all(set(x) == {"name", "size", "sha256", "uid", "gid", "mode"} for x in records)
    assert [x["name"] for x in records if x["name"].startswith("media/")] == ["media/" + x["key"] for x in plan["blobs"][:2]]


def test_empty_registry_and_64_character_key(api):
    assert len(api.member_records(plan_for(api, ()))) == 7
    plan = plan_for(api, ("attached",))
    plan["blobs"][0]["key"] = plan["references"][0]["key"] = "f" * 64
    plan["registry_sha256"] = api.registry_digest(plan["blobs"], plan["references"])
    assert api.validate_plan(plan)["blobs"][0]["key"] == "f" * 64


@pytest.mark.parametrize("size", [0, 1, 511, 512, 513, 10240, 4 * 1024**3])
def test_physical_size_counts_every_header_and_padding(api, size):
    plan = plan_for(api, ())
    plan["files"][0]["size"] = size
    raw = api.encode_manifest(plan)
    align = lambda n: n + (-n) % 512
    subtotal = 512 + align(len(raw)) + sum(512 + align(x["size"]) for x in plan["files"]) + 1024
    assert api.physical_size(plan) == subtotal + (-subtotal) % 10240


@pytest.mark.parametrize("field,value", [("max_file_bytes", True), ("chunk_bytes", 0), ("max_members", -1),
                                         ("max_archive_bytes", 32 * 1024**3 + 1), ("max_manifest_bytes", 1.0),
                                         ("chunk_bytes", 1024**2 + 1)])
def test_limits_can_only_be_strictly_tightened(api, field, value):
    with pytest.raises(api.ClosureError):
        api.Limits(**{field: value})


@pytest.mark.parametrize("limits", [{"max_file_bytes": 2}, {"max_members": 7}, {"max_manifest_bytes": 10},
                                    {"max_archive_bytes": 10239}, {"max_members": 3}])
def test_trusted_tighter_caps_are_enforced(api, limits):
    with pytest.raises(api.ClosureError):
        api.validate_plan(plan_for(api), api.Limits(**limits))


@pytest.mark.parametrize("path,value", [
    (("format",), "other"), (("registry_sha256",), "0" * 64),
    (("snapshots", "central"), "../secret"), (("snapshots", "identity"), "x" * 129),
    (("snapshots", "source_sha256"), "A" * 64), (("snapshots", "schema_sha256"), 42),
    (("files", 0, "size"), True), (("files", 0, "size"), -1), (("files", 0, "size"), 4 * 1024**3 + 1),
    (("files", 0, "mode"), 0o666), (("files", 0, "uid"), True), (("files", 0, "gid"), 2**32),
    (("files", 0, "name"), "/secret"), (("files", 0, "sha256"), "x" * 64),
    (("blobs", 0, "state"), "gc_pending"), (("blobs", 0, "present"), False),
    (("blobs", 0, "present"), 1), (("blobs", 0, "missing_evidence_sha256"), "d" * 64),
    (("blobs", 1, "missing_evidence_sha256"), None), (("blobs", 2, "present"), True),
    (("blobs", 3, "missing_evidence_sha256"), "d" * 64), (("blobs", 0, "key"), "a" * 33),
    (("blobs", 0, "key"), "A" * 32), (("blobs", 0, "tenant_id"), ""),
    (("blobs", 0, "mode"), 0o644), (("references", 0, "operation_id"), "foreign"),
])
def test_invalid_schema_and_binding_are_sanitized(api, path, value):
    plan = plan_for(api)
    target = plan
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = value
    with pytest.raises(api.ClosureError) as caught:
        api.validate_plan(plan)
    assert str(caught.value) == "invalid_closure"
    assert caught.value.code == "invalid_closure"


@pytest.mark.parametrize("area", ["plan", "snapshots", "file", "blob", "reference"])
def test_extra_fields_are_rejected(api, area):
    plan = plan_for(api)
    target = {"plan": plan, "snapshots": plan["snapshots"], "file": plan["files"][0],
              "blob": plan["blobs"][0], "reference": plan["references"][0]}[area]
    target["unexpected"] = "/private/synthetic-secret"
    with pytest.raises(api.ClosureError):
        api.validate_plan(plan)


@pytest.mark.parametrize("area", ["files", "blobs", "references"])
def test_duplicate_records_are_rejected(api, area):
    plan = plan_for(api)
    plan[area].append(copy.deepcopy(plan[area][0]))
    with pytest.raises(api.ClosureError):
        api.validate_plan(plan)


@pytest.mark.parametrize("area", ["files", "blobs", "references"])
def test_incomplete_inventory_is_rejected(api, area):
    plan = plan_for(api)
    plan[area].pop()
    with pytest.raises(api.ClosureError):
        api.validate_plan(plan)


def test_foreign_reference_and_scope_cannot_be_redigested_to_acceptance(api):
    plan = plan_for(api)
    plan["references"][0]["tenant_id"] = "foreign"
    with pytest.raises(api.ClosureError):
        api.registry_digest(plan["blobs"], plan["references"])


@pytest.mark.parametrize("raw", [b"{} ", b'{"a":1,"a":1}', b'{"a":NaN}', b'{"a":1.5}', b"[]", b"\xff"])
def test_noncanonical_or_invalid_manifest_bytes_are_rejected(api, raw):
    with pytest.raises(api.ClosureError):
        api.decode_manifest(raw)


def test_canonical_json_but_non_normalized_list_order_is_rejected(api):
    plan = plan_for(api)
    plan["files"].reverse()
    with pytest.raises(api.ClosureError):
        api.decode_manifest(api.canonical(plan))


@pytest.mark.parametrize("value", [1.0, float("nan"), {1: "a"}, {"x": object()}, (1, 2), {"x": "\ud800"}])
def test_canonical_rejects_non_json_and_float_values(api, value):
    with pytest.raises(api.ClosureError):
        api.canonical(value)


def test_exact_cap_equality_and_immutable_limits(api):
    plan = plan_for(api, ())
    raw = api.encode_manifest(plan)
    physical = api.physical_size(plan)
    assert api.validate_plan(plan, api.Limits(max_manifest_bytes=len(raw), max_archive_bytes=physical))
    with pytest.raises(AttributeError):
        api.Limits().chunk_bytes = 1


def test_registry_metadata_budget_precedes_full_canonicalization(api, monkeypatch):
    plan = plan_for(api)
    original = api.canonical

    def bounded_canonical(value):
        if type(value) is dict and set(value) == {"blobs", "references"}:
            raise AssertionError("oversized registry reached full canonicalization")
        return original(value)

    monkeypatch.setattr(api, "canonical", bounded_canonical)
    with pytest.raises(api.ClosureError):
        api.validate_plan(plan, api.Limits(max_manifest_bytes=512))


def test_absent_rows_also_count_toward_registry_metadata_cap(api):
    plan = plan_for(api, ("deleted",) * 8)
    with pytest.raises(api.ClosureError):
        api.validate_plan(plan, api.Limits(max_members=7))
