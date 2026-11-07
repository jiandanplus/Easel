# Central Identity Continuity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement task-by-task with review.

**Goal:** Preserve original user/wallet/admin IDs through a verified same-Logto restore, and prove integrity with an actual isolated PostgreSQL restore.

**Architecture:** A privileged collector compares two real read-only IdP inventories and records expiring owner-only proof. Atomic migration consumes that proof to append unchanged-subject aliases, while normal Console auth resolves canonical and alias safely. Session revocation and real cutover remain separate.

**Tech Stack:** Python3.12, existing psycopg/FastAPI/OIDC/pytest, PostgreSQL16, existing locked wheel build and PostgreSQL dump/restore tools.

## Global Constraints

- Current codex/ui-redesign, no merge/automatic commit/push; no real identities or wallet writes.
- No handwritten mappings/proof JSON accepted as identity truth; same-subject same-Logto restore only.
- Both proof collection and apply require actual schema owner and independent capability; Web roles cannot write proof/migration/alias.
- Preserve original user/tenant/wallet/admin/role/active/epoch and all financial history. Mapping phase keeps sessions; cutover/revocation is separate.
- Existing SQL001–009 unchanged; add010. No production DNS/TLS/service/Logto change or model/payment calls.
- Only exact owned loopback test container and new random schemas/databases/roles; private files0600/dirs0700; no credential output.

### Task 1: Database contracts, service and Console integration

Files: new migrations/010_identity_continuity.sql, identity_migration.py; modify console_auth.py, console_permissions.py, permissions.py; tests test_identity_migration.py and focused Console/role tests.

Interfaces: `MigrationPolicy(environment,source_issuer,target_issuer,tenant_id,client_ids,approved_backup_sha256)`; `VerifiedRestoreEvidence` with policy/backup/inventory/context hashes and tenant user/client ID tuples; `IdentityMigrationService(db,policy).record_proof(evidence,operator)`, `.inspect(proof_id,operator)`, `.apply(proof_id,key,reason,operator)`; owner capabilities identity:collect / identity:migrate. Reference spec for exact semantics; all returned summaries exclude subject/DSN.

- [x] Write RED for proof-only owner access, preserved IDs/wallet/admin states, new issuer reuse, no email merge, empty/mismatched proof, collision/idempotency/concurrency/full rollback, immutability/low-role guards and sessions/pending preservation.
- [x] Implement additive SQL and bounded service; shared canonical/alias resolver and locks for callback/approve, DB cross-table conflict triggers and audit consistency. Do not change source issuer/canonical fields or session lifecycle.
- [x] Run focused tests with private test DSN path, capture RED/GREEN; freeze interfaces for Task2/3 and independent review.

### Task 2: Actual readonly restore evidence collector

Files: new identity_restore.py and tests/test_identity_restore.py.

Consumes Task1 policy/evidence contracts. `collect_restore_evidence(policy,*,source_dsn,target_dsn,source_schema,target_schema,backup_path)` returns `VerifiedRestoreEvidence` only after actual bounded PG readonly queries; no HTTP/model/IdP login. Source/target contextual identity must differ. Verify all tables/schema snapshot digests equal, users/apps and tenant/IDs present, RLS visibility safe and backup SHA equals policy; never emit row data or DSN.

- [x] RED for same target, wrong backup, missing table/column, source/target delta, RLS/privilege gaps, absent users/apps and oversized data.
- [x] Implement strict private config/file parsing and live read-only inventory, policy binding and exact IDP schema checks; collector does not mutate identity databases.
- [x] GREEN focused real PG tests; report snapshot digest and unchanged source/target after reads. Separate actual signed login remains unverified.

### Task 3: Operator entrypoints and actual synthetic recovery drill

Files: identity_migration_cli.py, __main__.py parser/dispatch; tests/test_identity_migration_cli.py; scripts/validation or private owned recovery harness; docs/validation/2026-10-23-central-identity-continuity.md and runbook updates.

- [x] CLI RED: explicit owner DSN mandatory, owner target binding, fixed error/secret masking, no mapping values in argv or ordinary HTTP route; add proof/inspect/apply parser and safe dispatch.
- [x] Run actual pg_dump/pg_restore from one synthetic source schema into new empty test DB; fixture includes settled+pending jobs, membership and enabled/disabled admins plus alias audit. Verify full table/schema/sequence snapshots and low-role guards; failed/partial recovery must not count as pass.
- [x] Full central regression after all SQL/source frozen; rebuild Console/wheel and deployment source verification because010 changes package inventory. Do not overwrite S5-A1 historical artifact.

### Task 4: Independent review and evidence closeout

- [x] Review exact new source/diff, proof trust boundaries, SQL privilege/races, CLI behavior and restore evidence. Fix important findings with covering tests.
- [x] Record separate counts for focused/full tests, synthetic OIDC, actual synthetic restore, new build, and remaining real Logto/production/TLS gates. Preserve old HEAD/main and user custom files; stop only owned synthetic test container if root started it and all test connections are closed, retaining dump/evidence.
