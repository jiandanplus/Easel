import copy
import importlib.util

import pytest

from platform_service.media_backup_b2.contracts import B2Error


def api():
    name = "platform_service.media_backup_b2.maintenance_snapshot"
    assert importlib.util.find_spec(name) is not None, (
        "complete maintenance snapshot boundary is missing"
    )
    return __import__(name, fromlist=["make_snapshot"])


def sample():
    tables = {
        "schema_migrations": [
            {"version": "001_media_backup_control.sql", "checksum": "a" * 64}
        ],
        "settings": [
            {"id": 1, "application_schema": "public", "profile": "public_001_016"}
        ],
        "control": [
            {
                "id": 1,
                "state": "CAPTURING",
                "epoch": 1,
                "window_id": "11111111-1111-4111-8111-111111111111",
                "owner_hash": "b" * 64,
                "binding": {"generation_digest": "c" * 64},
                "original_state": {},
            }
        ],
        "workers": [],
        "worker_attestations": [],
        "permits": [],
        "audit": [
            {
                "id": 1,
                "at": "2026-10-30T00:00:00+00:00",
                "action": "DRAINING",
                "window_id": "11111111-1111-4111-8111-111111111111",
                "epoch": 1,
                "details": {"intent": "closed_new_admissions"},
            },
            {
                "id": 2,
                "at": "2026-10-30T00:00:01+00:00",
                "action": "CAPTURING",
                "window_id": "11111111-1111-4111-8111-111111111111",
                "epoch": 1,
                "details": {"barrier": "exclusive"},
            },
        ],
    }
    columns = {
        "schema_migrations": ["version", "checksum"],
        "settings": ["id", "application_schema", "profile"],
        "control": list(tables["control"][0]),
        "workers": ["worker_id", "generation", "identity", "token_hash", "active"],
        "worker_attestations": ["worker_id", "generation", "domain"],
        "permits": [
            "token_hash",
            "epoch",
            "kind",
            "subject_id",
            "attempt_id",
            "worker_id",
            "generation",
            "upstream_sha256",
            "status",
        ],
        "audit": list(tables["audit"][0]),
    }
    catalog = {
        "columns": [
            ["easel_backup_control", table, "r", "owner", None, i, col]
            for table, cols in columns.items()
            for i, col in enumerate(cols, 1)
        ]
    }
    sequence = {
        "schema": "easel_backup_control",
        "name": "audit_id_seq",
        "owner": "owner",
        "data_type": "bigint",
        "start": 1,
        "increment": 1,
        "max": 2**63 - 1,
        "min": 1,
        "cache": 1,
        "cycle": False,
        "owned_schema": "easel_backup_control",
        "owned_table": "audit",
        "owned_column": "id",
        "dependency": "i",
        "last_value": 2,
        "is_called": True,
    }
    ddl = "\n".join(
        "CREATE TABLE easel_backup_control." + name + " (\n);" for name in tables
    )
    return tables, [sequence], catalog, ddl


def snapshot():
    tables, sequences, catalog, ddl = sample()
    value = api().make_snapshot(
        tables, sequences, snapshot="00000001-1", catalog=catalog, ddl=ddl
    )
    return value, catalog


def verifying(value):
    result = copy.deepcopy(value)
    result["tables"]["control"][0]["state"] = "VERIFYING"
    result["tables"]["audit"].append(
        {
            "id": 3,
            "at": "2026-10-30T00:00:02+00:00",
            "action": "VERIFYING",
            "window_id": result["tables"]["control"][0]["window_id"],
            "epoch": 1,
            "details": {"objects": "durably_reopened"},
        }
    )
    result["sequences"][0]["last_value"] = 3
    return result


def test_complete_snapshot_is_canonical_and_noop_postcondition_is_stable():
    value, catalog = snapshot()
    assert (
        api().validate_snapshot(value, snapshot="00000001-1", catalog=catalog) == value
    )
    assert api().verify_progress(value, copy.deepcopy(value))


def test_only_exact_verifying_audit_tail_and_sequence_step_are_accepted():
    value, _ = snapshot()
    assert api().verify_progress(value, verifying(value))


@pytest.mark.parametrize(
    "fault",
    [
        "sequence_gap",
        "sequence_only",
        "unaccounted_tail",
        "wrong_action",
        "wrong_details",
        "wrong_window",
        "wrong_epoch",
        "prefix",
        "owner",
        "binding",
        "settings",
        "inactive_worker",
        "unknown_permit",
        "extra_table",
        "schema",
        "snapshot",
    ],
)
def test_unknown_control_change_never_becomes_a_legal_cas_tail(fault):
    value, _ = snapshot()
    changed = verifying(value)
    if fault == "sequence_gap":
        changed["sequences"][0]["last_value"] += 1
    elif fault == "sequence_only":
        changed["tables"]["audit"].pop()
    elif fault == "unaccounted_tail":
        changed["tables"]["audit"].append(copy.deepcopy(changed["tables"]["audit"][-1]))
    elif fault == "wrong_action":
        changed["tables"]["audit"][-1]["action"] = "RECOVERING"
    elif fault == "wrong_details":
        changed["tables"]["audit"][-1]["details"] = {"objects": "unknown"}
    elif fault == "wrong_window":
        changed["tables"]["audit"][-1]["window_id"] = (
            "22222222-2222-4222-8222-222222222222"
        )
    elif fault == "wrong_epoch":
        changed["tables"]["audit"][-1]["epoch"] = 2
    elif fault == "prefix":
        changed["tables"]["audit"][0]["details"] = {}
    elif fault == "owner":
        changed["tables"]["control"][0]["owner_hash"] = "d" * 64
    elif fault == "binding":
        changed["tables"]["control"][0]["binding"] = {}
    elif fault == "settings":
        changed["tables"]["settings"][0]["profile"] = "local_001_020"
    elif fault == "inactive_worker":
        changed["tables"]["workers"].append({"active": False})
    elif fault == "unknown_permit":
        changed["tables"]["permits"].append({"status": "unknown"})
    elif fault == "extra_table":
        changed["tables"]["unrecognised"] = []
    elif fault == "schema":
        changed["catalog_sha256"] = "e" * 64
    elif fault == "snapshot":
        changed["snapshot"] = "00000002-2"
    with pytest.raises(B2Error):
        api().verify_progress(value, changed)


@pytest.mark.parametrize(
    "fault",
    [
        "missing_table",
        "extra_column",
        "omitted_column",
        "unknown_sequence",
        "ddl_omission",
        "catalog_hash",
        "boolean_sequence_definition",
        "initial_sequence_gap",
        "initial_unexplained_audit",
        "duplicate_capture_phase",
    ],
)
def test_snapshot_never_silently_omits_raw_control_data(fault):
    value, catalog = snapshot()
    if fault == "missing_table":
        value["tables"].pop("worker_attestations")
    elif fault == "extra_column":
        value["tables"]["settings"][0]["unknown"] = True
    elif fault == "omitted_column":
        value["tables"]["control"][0].pop("original_state")
    elif fault == "unknown_sequence":
        value["sequences"].append(copy.deepcopy(value["sequences"][0]))
    elif fault == "ddl_omission":
        value["schema_sql"] = value["schema_sql"].replace(
            "CREATE TABLE easel_backup_control.workers ", "UNKNOWN "
        )
    elif fault == "catalog_hash":
        value["catalog_sha256"] = "f" * 64
    elif fault == "boolean_sequence_definition":
        value["sequences"][0]["increment"] = True
    elif fault == "initial_sequence_gap":
        value["sequences"][0]["last_value"] += 1
    elif fault in {"initial_unexplained_audit", "duplicate_capture_phase"}:
        row = copy.deepcopy(value["tables"]["audit"][-1])
        row["id"] += 1
        if fault == "initial_unexplained_audit":
            row["action"] = "unexpected"
        value["tables"]["audit"].append(row)
        value["sequences"][0]["last_value"] += 1
    with pytest.raises(B2Error):
        api().validate_snapshot(value, snapshot="00000001-1", catalog=catalog)


def test_unknown_foreign_control_relation_is_not_silently_omitted():
    tables, sequences, catalog, ddl = sample()
    catalog["columns"].append(
        ["easel_backup_control", "unknown", "f", "owner", None, 1, "id"]
    )
    with pytest.raises(B2Error):
        api().make_snapshot(
            tables, sequences, snapshot="00000001-1", catalog=catalog, ddl=ddl
        )


@pytest.mark.parametrize("missing", [False, True])
def test_worker_registry_is_derived_from_complete_raw_history(missing):
    value, _ = snapshot()
    worker_id = "22222222-2222-4222-8222-222222222222"
    generation = "33333333-3333-4333-8333-333333333333"
    identity = {
        "worker_id": worker_id,
        "generation": generation,
        "host_id": "host",
        "boot_id": "boot",
        "process_start": "start",
    }
    value["tables"]["workers"] = [
        {
            "worker_id": worker_id,
            "generation": generation,
            "identity": identity,
            "token_hash": "d" * 64,
            "active": True,
        }
    ]
    domain = {"control_ino": 1}
    value["tables"]["worker_attestations"] = [
        {"worker_id": worker_id, "generation": generation, "domain": domain}
    ]
    expected = {
        "worker_id": worker_id,
        "generation": generation,
        "identity": identity,
        "active": True,
        "domain": domain,
    }
    worker = {
        "window_id": value["tables"]["control"][0]["window_id"],
        "generation_digest": "c" * 64,
        "registry": [] if missing else [expected],
    }
    assert hasattr(api(), "validate_worker_binding"), (
        "raw worker history binding is missing"
    )
    if missing:
        with pytest.raises(B2Error):
            api().validate_worker_binding(value, worker)
    else:
        assert api().validate_worker_binding(value, worker)
