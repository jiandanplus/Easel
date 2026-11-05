# Persistent Owned PostgreSQL Regression Fixture

> **For agentic workers:** Use subagent-driven-development for code-only authority/fixture adaptation and independent review. Root alone creates/starts actual resources, reads private fixture credentials, initializes synthetic identity data or runs pytest. No commit/push/merge.

**Goal:** Replace the failed 1GiB-tmpfs synthetic test PG authority with a fresh, persistent, explicitly selected local fixture so the complete installed-candidate regression can finish without changing business assertions.

**Architecture:** Preserve stopped container7bed3d6afc12db82f89cadf062ab5d07315b1ff793962234bd6ea4d342f32f0d and all old DSN/config/failed-run artifacts. A new private descriptor selects exactly one newly owned local PG instance. A stdlib-only reader validates that descriptor and caches the immutable selection before the existing owned fixture clears PG*/EASEL* environment. The two actual capture tests use that exact selected container and synthetic identity fixture, instead of hardcoded recovered-instance paths. No discovery or production fallback.

**Tech Stack:** Existing cached official PostgreSQL16 image, named local data volume, loopback-only host port, existing psycopg3/pytest and a6 installed candidate. No dependency/model/payment/production changes.

## Evidence and constraints

- Original general run is failed, not partial success promoted to completion: 1973 call passes plus1teardownerror; distinct1972complete passes/1error/2653notrun from4626nodes,146files visited. Original owned group did not start.
- Root observed WAL ENOSPC and then source container exited1/noOOM/restart0 with PGDATA tmpfs size1073741824. df unavailable after exit. Do not claim a measured utilization, recovered tmpfs data or a verified post-run base state. Preserve originals, never automatically restart that handle.
- Archive208safe files/SHA886430b887e9e96e0a3cf40611a0edd1154702f034ccfbc9235044d54247bc24 contains all196oldtest source bytes and exact old operator; no private SQL/credentials/raw log exported.
- Only test infrastructure changes. Preserve every original test assertion, parameter, SQL, grant policy, role generation, loopback restriction, deadline, cancellation rule, source catalog pin, product/wheel/frontend and old200ms historical result. New full run uses a new batch/once path.
- B2 local/isolation authorization only. No real account/Logto data, production configuration, model/payment, remote server, timer, B3 acceptance or deployment.

## Task1: Explicit private authority and minimal consumer adaptation

**Create:** services/platform/tests/b2_test_authority.py; pure tests/evidence under outputs/_run/owned-pg-disk-fixture-20261103/codeonly/.

**Modify only:** services/platform/tests/b2_owned_pg.py, test_media_backup_b2_capture_pg.py, test_media_backup_b2_integrated.py. No other central tests/product files.

Descriptor selection comes only from explicitly supplied `EASEL_B2_TEST_AUTHORITY_FILE`, read lazily before environment clearing. No default to the old dead endpoint, ambient PG service, production DSN or directory scan. Missing selection fails safely, not skips.

Exact descriptor keys: `format`, `nonce`, `container_id`, `image`, `host`, `port`, `database`, `user`, `password`. Require format=easel-b2-owned-test-pg-v1; lowercase32hex nonce; lowercase64hex fullcontainerID; image exactly postgres@sha256:9e2cf6287f25c3151f4c9421ccce2b17f3e60061c111a29165e0d2cdd9ff0a97; host exactly127.0.0.1; actualint port1024..65535 (no bool); database exactly easel_s1_test_b2_+nonce; user exactlypostgres; password32..128ASCII URL-safe alphanumeric/underscore/hyphen. No arbitrary DSN/query/options fields.

The selected file must be absolute and named authority.private.json inside directory easel-b2-owned-pg-<nonce>. Parent and file must be owned by current OS user, mode0700/0600, no symlink ancestors/final symlink, regular singlelink file, bounded<=16384bytes, valid UTF8 JSON with duplicate keys rejected. Open/check actual file descriptors without following symlinks; reject path traversal, unsafe type/mode/UID/nlink/oversize/schema; do not print the payload/password in errors/repr. Return a frozen authority value with password repr=False and an opaque safe repr. Test directories may live under the codeonly synthetic-test scratch; do not hardcode a test-only bypass for path/owner/mode checks.

Expose `selected_authority()` with process-local lazy immutable caching, so a later _owned_pg call still uses the same exact descriptor after the existing environment clearing. Cache must not turn missing/invalid initial input into a partial value; no private reads at module import. Use psycopg.make_conninfo in b2_owned_pg to form the same exact loopback/hostaddr/connect_timeout5/sslmodedisable/application_name settings from validated components, never parsing an arbitrary supplied DSN. Preserve all creation/grant/migration/finalization SQL and role naming, and add the selected containerID to OwnedPG for the actual pg_dump/pg_restore argv.

Expose `identity_fixture()` from b2_owned_pg returning the same `(private_config, roles)` shape consumers already use. Read only fixed siblings identity-dsn.private.json and identity-roles.safe.json within the selected private authority directory, with the same regular/singlelink/owner/mode/size/no-follow/duplicate-key checks. private_config must be exactly {dsn: str}; roles exactly {bootstrap: str, tenant_roles: list[str]}, valid distinct SQL identifier strings<=63characters. Parse identity DSN using existing psycopg.conninfo_to_dict and require same literal host/hostaddr, selectedport, database exactly easel_s1_test_logto_+nonce, user=roles.bootstrap, no indirect service or other host routing/options. Require actual password nonempty; secret values never in diagnostics. Root later supplies synthetic fixtures only; this format check is not proof of actual resource custody or restored data.

Both capture consumers replace the fixed recovered Logto paths with identity_fixture(), and fixed container7bed strings with central/env.container_id. Preserve their whole assertions, schema profiles, roles, administrative grantor logic, tool/env allowlists and actual capture functions. No product/read-policy/source-facts substitution.

- [x] Synthetic TDD RED first for missing explicit authority, exact valid schema, wrong/missing/extra/type/nonce/host/port/db/user/image/password fields, duplicate JSON, real synthetic FS symlink/hardlink/mode/oversize/parent ownership checks and no import I/O.
- [x] Meaningful tests for cache surviving cleared EASEL environment without reselecting another descriptor; invalid input not cached. Use fresh module instances for cases. Reader/validator must execute actual code on synthetic bytes/files; no product/real PG/test modules loaded.
- [x] Pure consumer source AST checks ensure only authority paths/container references changed, all old business assertions/SQL/grants/limits remain and both capture paths use the selected authority. Tests may parse original source snapshots but not import or execute these fixture/product/capture modules.
- [x] For identity validation use actual pure conninfo parser only if needed; no DB connection. If testing b2_owned_pg helper logic, AST extract with fake globals rather than import platform_service or execute fixture/pytestmain. Capture RED/GREEN, exact original/final sources/diff/hashes, limitations.
- [x] Independent source/spec/quality review and Root actual source adoption before resources. No actual Docker/SQL/credential read, subprocess, collection, full pytest or fixturemain by implementers/reviewers.


### 2026-11-03 independently reviewed administrator clarification

The original `user=postgres` restriction above is superseded only by this closed additional case: a conservative ASCII SQL identifier whose UTF8 SHA256 equals `416797dcb35c2f7ff89f33f006a75bfce7b1663055504b25f38464b4fdb30c0c`. Root separately verified that the retired synthetic source administrator has exactly this hash and that it matches the fixed public Logto145 external grantor. Preserve its exact logical name with fresh credentials in the new source; do not rewrite membership grantors to postgres. The reader still accepts exact postgres and rejects every other user. Source re-review PASS is `admin-clarification/REVIEW.safe.md`, SHA `c78a292ad5afffecc2e05bdc96a10010fd7856dc4aed5dbfc0d2c064353e67b7`; reader SHA `213e32019ae693ab22bd823e1fd44adbf2fab0d7d5389f1038e2de9eb6feffb3`. Seven new and all original fifteen synthetic tests passed. Actual service/membership/full regression remain unverified.

## Task2: Root fresh persistent synthetic service

- [ ] Check actual current physical host/VM headroom, pinned cached image, oldterminalstate and no active oldgroup. Document image/platform/resource/port/volume/secret initialization and finitewall/once handles before action. Never pull/stop/delete unrelated services or volumes.
- [ ] Create one new labelled nameddata volume and one new labelled local PG container with loopback-only published port, bounded memory/PIDs/logs. PGDATA must be the namedvolume, not tmpfs. Preserve normal fsync/full_page_writes/synchronous_commit; do not trade durability for a pass. Do not claim a soft WAL target is a hard disk quota. Keep>=2GiB physical headroom during tests.
- [ ] Generate synthetic credentials only in new user-owned0700dir named from nonce, mode0600 files; no password argv/chat/publicartifact. Verify actual container/image/label/loopback-port/mount/role/database against descriptor, and actual SQL identity before tests. New base empty; original retiredsource untouched.
- [ ] Reinitialize an exact accepted synthetic Logto145 baseline from verified public/synthetic source material. Inspect its provenance before reading any archive; do not substitute a real account dump. Bind same newauthority port/nonceDB and preserve original catalog/role checks. This is test-fixture initialization, not B3 or production recovery acceptance.
- [ ] Root verifies both central/identity fixtures and all installed139records/sources; independently reviews new metadata/materialization. Only then a new full batch with newsource inventory and exact same184testfiles, oldmixednode deselected/new2included. No retry of old failed handle.

## Task3: Resume exact installed full regression

- [ ] Preserve completed partition and reviewed operator controls; update only explicit newauthority/input/batch paths and changed test-source inventory. Recompute full file groups; no dropped tests or weakened checks.
- [ ] General then owned groups each once, original failure intact. Preserve exact collection/perphase/origins/skip/deselect/source/deadline and protected-base/service postchecks. Any unknown/failure stops new batch.
- [ ] Independent bounded actual-safe audit and exact candidate handoff. Explicitly retain skipped/unavailable f50/restore/native/production/B3/AV19/signing/cleanMac/other delivery gates; never call full goal achieved from a partial regression.
