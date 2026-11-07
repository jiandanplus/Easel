# Workbench provisioning and internal grant independent review

**Final spec verdict: PASS. Safety/code-quality verdict: PASS. No remaining P1/P2 finding established in this source review.** No deployment script, SSH, database mutation, provider/model call, or private credential/config/log read was performed. UI review is separately saved in `docs/validation/2026-10-23-workbench-ui-review.md`.

## Provisioning

Reviewed final `provision_server.py` SHA256 `393cde8ec29367de8fa3c0d632bd72d6fa074dbe140fb76123e4a92e5159ea8d`.

The initial review found that publishing alone left the execution model disabled (migration 006 defaults false). The final helper at lines 249-255 publishes, explicitly enables the exact MODEL with the service's existing execution:publish capability, and checks returned model_id/enabled. setup_database calls that helper. This resolves the readiness omission without expanding operator capability.

The script creates only the fixed new labelled PostgreSQL container/volume and dedicated schema/roles. It refuses an existing unclaimed resource, binds the DB only to 127.0.0.1:18776, checks resource limits/image/mount/labels, retains state for explicit recovery, and does not stop/delete old environments. Owner performs migration/catalog setup; runtime is separately credentialed and checked by require_runtime_role. OIDC uses the normal service with force_login, not synthetic identity creation. Fixed policy/config values match the actual loaders.

New API access is a captured server-side SELECT for the exact enabled business token; the secret stays in 0600 server configuration and does not appear in command arguments or public receipts. The child receives a deliberate environment with runtime DSN and no migration DSN or grant policy. Startup rechecks source/config hashes, owned resources and loopback port. There are no users, sessions, membership, lot, payment or grant writes in provision itself. Actual service health, OIDC bridge/callback operation and provider readiness remain deployment evidence to obtain separately.

## Internal grant

- `internal_grants.py:60-98`: opt-in owner-only policy file, strict exact fields and fixed 10,000,000 microcredits / seven days / text / concurrency one. Policy binds internal loopback origin, issuer, environment and database schema.
- `:113-159`: dedicated internal:issue capability, schema owner/current-session-user check, actual DB host/port/name/schema match, live session/refresh token, active user/epoch/tenant/product/client, and matching consumed OIDC transaction/handoff provenance. Relevant identity rows are locked against revocation. The new nullable source_transaction_id is written by the normal PKCE handoff redemption; old sessions are not backfilled. force_login defaults false for existing clients and adds prompt=login only when explicitly enabled.
- `:134-189`: environment-wide source-key advisory lock plus wallet row lock and uniqueness by environment/key and environment/wallet enforce one grant. Cross-operator exact replay returns the existing grant; changed key/reason/session/wallet conflicts. Capacity bound is checked before creating fixed live membership/lot and balanced issue journal. Audit, money and membership writes are one transaction; no payment is fabricated and test-mode spending still cannot consume this live lot.
- `007_internal_grants.sql`: immutable audit and unique bindings, foreign keys, exact plan/amount/seven-day duration, and deferred consistency checks connect the audit to identity, membership, lot and the two balancing issuance entries. Internal membership creation without its audit fails. Migration is additive; prior migrations and old session meanings are not rewritten.
- `permissions.py`, `__main__.py`: runtime cannot insert/update/delete/truncate internal_grants, including column-level insert detection. The standalone CLI requires explicit migration DSN and creates only the dedicated capability; there is no HTTP issuance surface or arbitrary amount knob. Existing runtime permissions are not expanded to grant issuance.
- Wallet/entitlement source metadata distinguish internal experience from test/live payment provenance. Frontend explicitly marks the allowance non-payment; UI validation is recorded separately.

Read regression sources covering owner/capability/policy boundaries, identity revocation/expiry/provenance, atomic rollback, idempotency and concurrent differing keys, immutable audit/runtime permission failures, no HTTP issuance, test/live separation, expiry and old test-lot preservation. Parent/author reported PostgreSQL regression runs are separate evidence; this reviewer did not rerun them or treat mock identity tests as actual Logto login acceptance.

Reviewed core anchors:

```
d6ab27e7f01d0e9afa86ee31da81d900086689221520070ca5bb00c44a76e860  internal_grants.py
c10b9b6b11931b1025bd9f2552b0e5c1afbef49430f47d2240eb553bc0733e9a  migrations/007_internal_grants.sql
e246511b142786f794754a4790bb2be5b95918009079bd4cbf23113384450ca3  identity/__init__.py
6215ab0c4fb7d5a9a2a3983e4ca67c2090ab3760f96e51a08811eebea2552dad  permissions.py
```

This PASS permits the authorized isolated preparation and later operator issuance workflow; it does not assert an actual grant, successful user login, live chat or payment occurred. Bind the eventual grant to the user's newly verified OIDC session/wallet and retain public-safe issuance/financial receipts. Keep the archived S3B/Pi unknown operations and wallets untouched.

## Follow-up validation helper migration gate — PASS

Compared `scripts/validation/verify_central_live_text.py` byte-for-byte with the previously reviewed stage helper. The sole functional delta defines REQUIRED_MIGRATIONS as the exact ordered 001_foundation through 007_internal_grants filenames, then replaces the old count-equals-six check with exact list equality. Database.migrate returns newly applied migrations in sorted filename order; this runner creates a fresh schema, so exact seven-file equality is the appropriate gate. Missing, unexpected or substituted migration names are not silently accepted. Identity, funding, provider configuration, credentials and dispatch paths in this helper are otherwise identical.

The prepare-only integration test now independently lists those exact seven filenames and retains success, zero provider dispatches and zero payment-row assertions. The author reports the full 473-test run passed; root's independent rerun is separate evidence. No tests, private DSN inspection, network or database action was performed in this appended review. The privately supplied equivalent conninfo-to-URI conversion is not a source-level Settings relaxation and was not inspected as private data.

Reviewed helper SHA256: `a2d6d66ce3a432dbe5f7c05d20b2947b63c355e5bd859ab06f2e30d3b6c8ba40`; corresponding test SHA256: `e134997f895c92c7656aea55daabf24d8163865fa25e146aa1d863cf78d5ce63`.
