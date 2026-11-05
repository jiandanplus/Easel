# B3 Source Reconstruction Data Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the complete private PostgreSQL16 source-observation DATA contract needed by the approved Task4 reconstruction capture, preserving every required source field without granting execution authority.

**Architecture:** This is the first independently reviewable part of the source-capture increment. The neutral B2 module contains immutable records and pure validation only. The subsequent owned producer will populate it at the actual BEFORE/DURING/AFTER capture sites; a later role planner and original execution-owner admission consume that owned provenance. No pure record or digest substitutes for those ownership edges.

**Tech Stack:** Python3.12 standard library, existing B2Error, unittest, fixed PostgreSQL16 catalogue row shapes. No new dependency.

## Global Constraints

- Current branch `codex/ui-redesign`; no Git write, commit, push, merge, UI or product-flow change.
- User objective remains complete deliverable, subscription/custom modes, existing state, and no explanatory frontend copy. This task does not redefine completion of that objective.
- Original Task4 and its approved bootstrap amendment remain binding. No target connection, SQL mutation, new worker operation, native handover or role/database/authentication proof is implemented here.
- Keep FACT_KEYS, seven archive files, B2 exports, ATTEST wire, and all three existing comparison normalizations unchanged.
- Preserve source role validuntil rendering exactly; no timestamp normalization or verifier rehash. Preserve null versus empty arrays and array order.
- No arbitrary C/C, UTF8-only, empty-settings or default-tablespace ceiling in DATA capture. Retain all required PG16 values structurally; native replay compatibility and independent replay authorization remain separate, incomplete work.
- No actual Docker, PG, proc, network, model, production, private authority, archive, wheel, runtime inventory or credential access by implementer/reviewer. Qualified interpreter standard-library use and source/synthetic tests are permitted.
- Every shell command uses `rtk`/`rtk proxy`. Use `/private/tmp/easel-central-catalog-candidate-20261103-r1/runtime/bin/python3 -I -S -B` and explicit source paths for focused tests.

## Task 1: Complete immutable private observation schema

**Files:**

- Create `services/platform/src/platform_service/media_backup_b2/reconstruction_context.py`.
- Create `services/platform/tests/test_media_backup_b2_reconstruction_context.py`.
- Write author evidence only under `outputs/_run/b3-source-context-data-20261105/`: REPORT.safe.md, source.diff and captured RED/GREEN outputs.
- Do not change source-owner/capture/pipeline/caller/execution/worker/factory modules in this task.

**Interfaces:**

```python
class SourcePhase(Enum):
    BEFORE = "before"
    DURING = "during"
    AFTER = "after"
    POSTCHECK = "postcheck"

@dataclass(frozen=True, repr=False)
class SourceReadScope:
    kind: str
    container_id: str
    system_identifier_sha256: str
    database_name: str
    connection_admin: str
    selected_role_names: tuple

def parse_source_sample(
    scope: SourceReadScope,
    phase: SourcePhase,
    identity_row,
    role_rows,
    database_rows,
    setting_rows,
    *,
    keeper_snapshot_token=None,
    keeper_snapshot_text=None,
) -> SourceSample:
    # Complete behavior is specified below; returned records are DATA only.
    ...
```

The ellipsis above is a signature, not an implementation to leave in production. Every production function must be fully implemented and tested. Define frozen, redacted `SourceIdentity`, `RoleState`, `DatabaseState`, `SettingState`, and `SourceSample` with the exact field names/order below. Constructors and the parser validate exact primitive types; manually created records cannot bypass their own shape checks. Arrays become immutable tuples while preserving null/empty/order. Scope and records are copyable DATA, not producer capabilities. No owned-proof, admit/execute/restore, connection, callback, SQL or DSN API exists in this module.

SourceIdentity field names are `database_name, system_identifier, server_version_num, backend_pid, snapshot_text, read_only, isolation, timezone, current_user, session_user, current_user_oid, session_user_oid`. SourceSample field names are `scope, phase, identity, roles, database, settings, keeper_snapshot_token, keeper_snapshot_text`. Row-to-record conversion preserves exact source values, except the explicit typed conversion of server_version_num decimal text to its integer field and list-to-tuple immutable array representation; original source archives are untouched. Phase parser and constructors apply the same checks.

### Exact scope and row shapes

Scope: kind is central/identity; container/hash are lowercase64hex; names use existing B3-compatible ASCII identifier pattern `[A-Za-z_][A-Za-z0-9_]{0,62}`. selected_role_names is a nonempty unique tuple (max1024), includes connection_admin, and comes later from Root source-owner selection. It may exclude the bootstrap name: source OID10 is independently discovered by the fixed source query, not inferred from connection_admin or an archive name.

Identity row (12 fields):

1. database_name, 2. system_identifier decimal text, 3. server_version_num decimal text, 4. backend_pid, 5. pg_current_snapshot text, 6. transaction_read_only, 7. transaction_isolation, 8. timezone, 9. current_user, 10. session_user, 11. current_user_oid, 12. session_user_oid.

Require database/scope equality, SHA256 of exact system-identifier UTF8 equals scope pin, PG16 version160000..169999, positive backend PID, read_only=`on`, isolation=`repeatable read`, exact current/session name=connection_admin and matching positive OIDs. System identifier is positive uint64 decimal; snapshot is bounded PG snapshot `xmin:xmax:xiplist`, with xmin<=xmax, unique in-range xip IDs and numeric bounds validated. Do not impose a sorted-xip requirement or normalize the original snapshot text. Do not assume the connection principal is OID10. Timezone is bounded exact nonempty source text.

Role row (14 fields), mapped to RoleState in this order:

`oid, name, superuser, inherit, createrole, createdb, login, replication, connlimit, validuntil, bypassrls, config, verifier, auth_xmin`.

Booleans are actual bool. OIDs are positive uint32. connlimit is int -1..INT32_MAX. validuntil is null or exact bounded text (including legitimate infinity rendering). config is null or bounded ordered tuple of `key=value` strings; preserve values, reject repeated keys case-insensitively, and do not filter unknown syntactically valid GUC keys in DATA capture. verifier is null, exact canonical `md5`+32lowerhex, or a complete SCRAM-SHA-256 verifier with positive INT32 iterations (preserving exact decimal spelling, including leading zeros), canonical base64 nonempty salt and exactly32-byte stored/server keys. Reject malformed/noncanonical base64, empty verifier, extra separators, booleans masquerading as integers and leaked offending values in exceptions. Do not calculate a new verifier. This DATA capture contract does not change the separate existing B3 archive validator's spelling restrictions.

Require unique role names/OIDs; exactly one observed role has OID10. Returned names equal selected_role_names union that independently observed bootstrap name, with no missing or extra role. The selected connection principal must match its observed row and the identity OIDs. An OID10 row may be grantor-only and absent from selected/exported business roles. This permits READ evidence only; it does not authorize any role replay or handover.

Database row (exactly one, 19 fields), mapped to DatabaseState:

`oid, name, owner_oid, owner_name, encoding_id, encoding_name, collate, ctype, provider, icu_locale, icu_rules, recorded_version, actual_version, acl, connlimit, istemplate, allowconn, tablespace_name, xmin`.

Require exact current database name; owner name/OID agree with one captured role. Preserve encoding ID/name without imposing UTF8-only. Provider is c/i. libc requires null ICU locale/rules; ICU requires nonempty locale and null-or-text rules (empty rules is distinct from null). Version values are independently null-or-text and retained even if unequal; replay compatibility is not established here. Preserve exact ACL text/null, connlimit -1..INT32_MAX (invalid -2 rejects), exact bool flags, tablespace identifier and xmin. Non-C libc locales, ICU, named tablespaces and nonempty settings are not discarded or rejected solely to simplify this prerequisite.

Setting row (5 fields), mapped to SettingState:

`database_oid, role_oid, role_name, config, xmin`.

OID0 means global. role_oid0 requires role_name null; positive role OID requires a valid name. Retain all rows for the exact source database, including otherwise unselected roles. Global rows may refer only to selected/captured role OIDs or0. Reject other database OIDs, duplicate `(database_oid, role_oid)`, inconsistent name/OID pairs and malformed configs. Global selected-role settings must exactly agree with corresponding RoleState.config, preserving null/empty/order. A role with no global row has config null. Unknown current-database role settings remain DATA for the future authorization planner; do not silently filter them.

Phase DURING requires both exact bounded exported snapshot token and keeper snapshot text; parsed identity snapshot equals keeper text. Other phases require both arguments null. Token shape follows PostgreSQL exported snapshot spelling, not a caller SQL value. Parser validates DATA correlation only; actual import and original keeper custody belong to the subsequent producer.

### Bounds, privacy and stable comparison

- Source sample canonical private serialization <=8MiB. Validate/stream count before assembling an unbounded serialization; fixed rows/counts/field lengths must bound work and allocation.
- Max roles1024, settings4096, config entries256 per array; verifier1024bytes; config item8192bytes; ICU rules65536bytes; other source texts1024bytes except identifier63bytes, hash64bytes and xmin10decimal digits. Use exact UTF8 byte limits and reject NUL. Reject invalid Unicode without including input in the error.
- Role/global/db xmin decimal0..UINT32_MAX; preserve exact source decimal spelling. Preserve row order from fixed source queries, and every config-array order; no hidden sorting/normalization.
- `SourceSample.to_private_value()` returns a detached JSON-primitive tree containing every exact field, explicit null, phase and snapshot evidence. It is intentionally private data.
- `SourceSample.stable_private_value()` contains scope + database/roles/settings + static identity fields including system/version/principal/timezone; it excludes only phase, backend PID, transaction snapshot and exported-snapshot telemetry. This is a versioned companion-specific projection, not a new business-comparison normalization.
- `SourceSample.safe_summary()` returns only fixed format/kind/phase, full/stable SHA256, counts and `data_only=True`, `native_verified=False`. It cannot contain identifiers, raw source rows, GUC values, verifiers, ACLs or passwords. All records and scopes have redacted repr/str. All invalid-input errors are existing B2Error with fixed `invalid_backup` text and no offending values.
- Import is standard-library-only/inert. No B3/scripts/psycopg import, FS, socket, process, clock acquisition, driver, object registry or target access.

### Step 1: Write meaningful RED tests first

- [ ] Add independent synthetic row fixtures, not actual-resource fixtures. Include connection admin `operator` with non10 OID and bootstrap `postgres` OID10 absent from selected names, plus owner/runtime/controller and pg_database_owner.
- [ ] Test valid full libc/non-C and ICU/rules/version/tablespace/settings preservation; no C/C/empty-settings shortcut.
- [ ] Test all null/empty/order distinctions, exact validuntil, NULL/MD5/SCRAM verifiers, frozen detachment and redaction.
- [ ] Test wrong source/database/principal/snapshot, missing or duplicate selected role/OID10, owner mismatch, all row-shape/type/limit failures, malformed credentials, settings references/duplicates/global mismatch, oversize aggregate, Unicode/NUL errors.
- [ ] Test phases: correct DURING keeper correlation, missing/wrong token/text, and non-DURING token refusal. Stable projection agrees for equivalent BEFORE/DURING/AFTER values but changes for adjacent protected fields, xmin, config order, NULL/empty, ICU rules and settings.
- [ ] Test unknown current-DB settings retained, other-DB/global-unselected settings rejected, and source validuntil unchanged.
- [ ] Use real schema code in tests. An initial absent-module/API assertion must fail explicitly; do not report import/setup errors as the intended RED. Private standard-library import/audit fixtures may assert zero IO without executing actual providers.

Run focused new file with qualified Python -I -S -B. Save exact RED command/output before production implementation. Tests must fail because this contract is missing, not because a live fixture/runtime is absent.

### Step 2: Implement the complete DATA contract

- [ ] Implement all exact records, primitive/cross-row validation, bounded serialization, private/stable values and safe summary. No optional feature beyond this specification.
- [ ] Reuse existing B2Error. Do not import B3 validator code into B2; this neutral capture contract precedes the B3 authority boundary.
- [ ] Reject constructor bypasses/mutable nested inputs, preserve original inputs and keep errors/reprs sanitized.

### Step 3: GREEN and self-review

Run the full focused module (no existing-resource or unrelated broad suite):

```sh
rtk proxy /private/tmp/easel-central-catalog-candidate-20261103-r1/runtime/bin/python3 -I -S -B services/platform/tests/test_media_backup_b2_reconstruction_context.py
```

- [ ] All meaningful tests pass; report exact count/seconds/exit status and RED/GREEN outputs.
- [ ] Review full diff against absent before files, freeze hashes and stop editing.
- [ ] Write REPORT.safe.md and source.diff. State explicitly that source capture/ownership, role planning, original-owner admission, native replay, SQL/full B3 and desktop delivery remain incomplete.
- [ ] Root independently runs fresh focused suite and reviews the complete diff; independent task review follows. No Git commit.

## Relationship to remaining work

This DATA contract is not the complete source-capture increment. Next work under the already approved Task4 design implements the owned native producer, exact fixed BEFORE/DURING/AFTER B2 sites, real publication/job/receipt provenance, SourceOwner lifetime and graph integration. Then implement the pure role/database plan and one-shot original-owner admission, followed by the full approved three-commit helper/handover/helper-removal protocol and actual PostgreSQL cases. All original Tasks4–9 and required PUBLIC/LOCAL/Logto matrix remain in scope.

Reference decisions: `outputs/_run/b3-task4-source-context-design-20261105.safe.md`, especially its superseding final section; `docs/superpowers/specs/2026-11-04-b3-bootstrap-grantor-amendment.md` and `docs/superpowers/plans/2026-11-04-b3-bootstrap-execution-notes.md`.

Official PG16 field references checked through Context7 on2026-11-05: [pg_database](https://www.postgresql.org/docs/16/catalog-pg-database.html), [pg_db_role_setting](https://www.postgresql.org/docs/16/catalog-pg-db-role-setting.html), [pg_authid](https://www.postgresql.org/docs/16/catalog-pg-authid.html), [pg_auth_members](https://www.postgresql.org/docs/16/catalog-pg-auth-members.html). These document fields; they do not substitute for later actual PG tests.
