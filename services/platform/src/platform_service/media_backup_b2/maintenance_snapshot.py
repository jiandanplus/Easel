"""Historical control evidence, never a live-control restoration interface."""

import copy
import hashlib
import re
from uuid import UUID, uuid4

from .bundles import MAX_FACTS_BYTES, canonical_bytes
from .contracts import B2Error, require

TABLES = {
    "schema_migrations": "version",
    "settings": "id",
    "control": "id",
    "workers": "worker_id",
    "worker_attestations": "worker_id",
    "permits": "token_hash",
    "audit": "id",
}
KEYS = {"format", "snapshot", "catalog_sha256", "schema_sql", "tables", "sequences"}
VERIFY_RESULT = {"objects": "durably_reopened"}
SEQUENCE_KEYS = {
    "schema",
    "name",
    "owner",
    "data_type",
    "start",
    "increment",
    "max",
    "min",
    "cache",
    "cycle",
    "owned_schema",
    "owned_table",
    "owned_column",
    "dependency",
    "last_value",
    "is_called",
}


def _uuid(value):
    require(type(value) is str and str(UUID(value)) == value)


def validate_snapshot(value, *, snapshot, catalog):
    try:
        require(type(value) is dict and value.keys() == KEYS)
        require(value["format"] == "easel-maintenance-snapshot-v1")
        require(type(snapshot) is str and snapshot and value["snapshot"] == snapshot)
        require(
            value["catalog_sha256"]
            == hashlib.sha256(canonical_bytes(catalog)).hexdigest()
        )
        require(len(canonical_bytes(value)) <= MAX_FACTS_BYTES)
        ddl = value["schema_sql"]
        require(
            type(ddl) is str
            and 0 < len(ddl.encode("utf-8")) <= 8 * 1024**2
            and "\x00" not in ddl
        )
        names = re.findall(
            r"^CREATE TABLE easel_backup_control\.([a-z_][a-z0-9_]*) \(", ddl, re.M
        )
        require(len(names) == len(TABLES) and set(names) == TABLES.keys())
        tables = value["tables"]
        require(type(tables) is dict and tables.keys() == TABLES.keys())
        columns = {}
        for row in catalog["columns"]:
            if row[0] == "easel_backup_control" and row[2] in {"r", "p", "f", "m"}:
                require(row[2] == "r")
                columns.setdefault(row[1], set()).add(row[6])
        require(columns.keys() == TABLES.keys())
        for name, rows in tables.items():
            require(type(rows) is list and len(rows) <= 100000)
            require(
                all(type(row) is dict and row.keys() == columns[name] for row in rows)
            )
            keys = [row[TABLES[name]] for row in rows]
            require(len(set(keys)) == len(keys) and keys == sorted(keys))
        require(not tables["permits"])
        require(
            len(tables["schema_migrations"]) == 1
            and len(tables["settings"]) == len(tables["control"]) == 1
        )
        migration = tables["schema_migrations"][0]
        require(
            migration["version"] == "001_media_backup_control.sql"
            and re.fullmatch("[a-f0-9]{64}", migration["checksum"])
        )
        settings = tables["settings"][0]
        require(
            type(settings["id"]) is int
            and settings["id"] == 1
            and settings["application_schema"] == "public"
        )
        require(settings["profile"] in {"public_001_016", "local_001_020"})
        control = tables["control"][0]
        require(
            type(control["id"]) is int
            and control["id"] == 1
            and control["state"] in {"CAPTURING", "VERIFYING"}
        )
        require(type(control["epoch"]) is int and control["epoch"] >= 1)
        _uuid(control["window_id"])
        require(
            type(control["owner_hash"]) is str
            and re.fullmatch("[a-f0-9]{64}", control["owner_hash"])
        )
        require(
            type(control["binding"]) is dict and type(control["original_state"]) is dict
        )
        sequences = value["sequences"]
        require(type(sequences) is list and len(sequences) == 1)
        seq = sequences[0]
        require(type(seq) is dict and seq.keys() == SEQUENCE_KEYS)
        require(
            all(
                type(seq[key]) is int
                for key in ("start", "increment", "max", "min", "cache", "last_value")
            )
        )
        require(
            (
                seq["schema"],
                seq["name"],
                seq["owned_schema"],
                seq["owned_table"],
                seq["owned_column"],
                seq["dependency"],
            )
            == (
                "easel_backup_control",
                "audit_id_seq",
                "easel_backup_control",
                "audit",
                "id",
                "i",
            )
        )
        require(
            seq["data_type"] == "bigint"
            and seq["start"] == seq["increment"] == seq["min"] == seq["cache"] == 1
        )
        require(
            seq["max"] == 2**63 - 1
            and seq["cycle"] is False
            and seq["is_called"] is True
        )
        require(type(seq["last_value"]) is int and 1 <= seq["last_value"] <= seq["max"])
        require(type(seq["owner"]) is str and seq["owner"])
        audit = tables["audit"]
        require(audit and audit[-1]["id"] == seq["last_value"])
        for row in audit:
            require(
                type(row["id"]) is int
                and row["id"] > 0
                and type(row["epoch"]) is int
                and row["epoch"] >= 0
            )
            require(type(row["at"]) is str and 0 < len(row["at"]) <= 128)
            require(
                type(row["action"]) is str
                and 0 < len(row["action"]) <= 64
                and type(row["details"]) is dict
            )
            if row["window_id"] is not None:
                _uuid(row["window_id"])
        phases = [
            row
            for row in audit
            if row["window_id"] == control["window_id"]
            and row["epoch"] == control["epoch"]
        ]
        expected_phases = [
            ("DRAINING", {"intent": "closed_new_admissions"}),
            ("CAPTURING", {"barrier": "exclusive"}),
        ]
        if control["state"] == "VERIFYING":
            expected_phases.append(("VERIFYING", VERIFY_RESULT))
        require([(row["action"], row["details"]) for row in phases] == expected_phases)
        require(audit[-1] == phases[-1])
        return value
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        raise B2Error() from None


def make_snapshot(tables, sequences, *, snapshot, catalog, ddl):
    value = {
        "format": "easel-maintenance-snapshot-v1",
        "snapshot": snapshot,
        "catalog_sha256": hashlib.sha256(canonical_bytes(catalog)).hexdigest(),
        "schema_sql": ddl,
        "tables": copy.deepcopy(tables),
        "sequences": copy.deepcopy(sequences),
    }
    return validate_snapshot(value, snapshot=snapshot, catalog=catalog)


def validate_worker_binding(value, worker):
    """An active projection cannot substitute for the complete historical rows."""
    try:
        tables = value["tables"]
        control = tables["control"][0]
        require(control["window_id"] == worker["window_id"])
        require(control["binding"]["generation_digest"] == worker["generation_digest"])
        history = {row["worker_id"]: row for row in tables["workers"]}
        require(len(history) == len(tables["workers"]))
        attestations = {row["worker_id"]: row for row in tables["worker_attestations"]}
        require(
            len(attestations) == len(tables["worker_attestations"])
            and attestations.keys() <= history.keys()
        )
        registry = []
        for key, row in sorted(history.items()):
            _uuid(key)
            _uuid(row["generation"])
            require(
                type(row["active"]) is bool
                and type(row["token_hash"]) is str
                and re.fullmatch("[a-f0-9]{64}", row["token_hash"])
            )
            identity = row["identity"]
            require(
                type(identity) is dict
                and identity.keys()
                == {"worker_id", "generation", "host_id", "boot_id", "process_start"}
            )
            require(
                identity["worker_id"] == key
                and identity["generation"] == row["generation"]
            )
            require(all(type(v) is str and v for v in identity.values()))
            attestation = attestations.get(key)
            if attestation is not None:
                require(
                    attestation["generation"] == row["generation"]
                    and type(attestation["domain"]) is dict
                )
            if row["active"]:
                require(attestation is not None)
                registry.append(
                    {
                        "worker_id": key,
                        "generation": row["generation"],
                        "identity": identity,
                        "active": True,
                        "domain": attestation["domain"],
                    }
                )
        require(registry == worker["registry"])
        return {"historical_workers": len(history), "active_workers": len(registry)}
    except (KeyError, TypeError, ValueError, AttributeError):
        raise B2Error() from None


def verify_progress(before, current):
    """Only the exact same owner's single fixed VERIFYING CAS may extend history."""
    try:
        require(
            type(before) is type(current) is dict
            and before.keys() == current.keys() == KEYS
        )
        for name in KEYS - {"tables", "sequences"}:
            require(before[name] == current[name])
        old, new = before["tables"], current["tables"]
        require(old.keys() == new.keys() == TABLES.keys())
        for name in TABLES.keys() - {"control", "audit"}:
            require(old[name] == new[name])
        require(not old["permits"] and not new["permits"])
        require(len(old["control"]) == len(new["control"]) == 1)
        original, actual = old["control"][0], new["control"][0]
        require(
            original["state"] == "CAPTURING"
            and actual["state"] in {"CAPTURING", "VERIFYING"}
        )
        require(
            {k: v for k, v in original.items() if k != "state"}
            == {k: v for k, v in actual.items() if k != "state"}
        )
        prefix, observed = old["audit"], new["audit"]
        require(
            type(prefix) is type(observed) is list and observed[: len(prefix)] == prefix
        )
        tail = observed[len(prefix) :]
        require(type(before["sequences"]) is list and len(before["sequences"]) == 1)
        expected_sequences = copy.deepcopy(before["sequences"])
        if actual["state"] == "VERIFYING":
            require(len(tail) == 1)
            row = tail[0]
            require(
                row.keys() == {"id", "at", "action", "window_id", "epoch", "details"}
            )
            require(
                row["action"] == "VERIFYING"
                and row["window_id"] == original["window_id"]
                and row["epoch"] == original["epoch"]
            )
            require(
                row["details"] == VERIFY_RESULT
                and type(row["at"]) is str
                and 0 < len(row["at"]) <= 128
            )
            expected_sequences[0]["last_value"] += 1
            require(row["id"] == expected_sequences[0]["last_value"])
        else:
            require(not tail)
        require(current["sequences"] == expected_sequences)
        return {"audit_prefix_rows": len(prefix), "allowed_cas_tail_rows": len(tail)}
    except (KeyError, TypeError, ValueError, AttributeError):
        raise B2Error() from None


def read_control_rows(connection):
    """Fixed tables, actual named cursors and a total bounded raw-row budget."""
    actual = connection.execute(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='easel_backup_control' AND c.relkind IN ('r','p','f','m') ORDER BY 1"
    ).fetchall()
    require({row[0] for row in actual} == TABLES.keys())
    result, total = {}, 0
    for table, key in TABLES.items():
        rows = []
        # Names come only from the fixed source constants, never the archive.
        statement = (
            "SELECT pg_column_size(to_jsonb(t)),CASE WHEN pg_column_size(to_jsonb(t))<=%s "
            'THEN to_jsonb(t) ELSE NULL END FROM easel_backup_control."'
            + table
            + '" t ORDER BY "'
            + key
            + '"'
        )
        with connection.cursor(name="b2control_" + uuid4().hex) as cursor:
            cursor.itersize = 1
            cursor.execute(statement, (MAX_FACTS_BYTES,))
            for size, row in cursor:
                require(
                    type(size) is int
                    and 0 < size <= MAX_FACTS_BYTES
                    and type(row) is dict
                )
                total += len(canonical_bytes(row))
                require(total <= MAX_FACTS_BYTES and len(rows) < 100000)
                rows.append(row)
        result[table] = rows
    return result
