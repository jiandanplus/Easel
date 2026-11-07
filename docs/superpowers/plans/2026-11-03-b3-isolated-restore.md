# B3-L Complete Isolated Restore Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans task by task. Code-only implementers and independent reviewers do not operate resources. Root alone runs actual tools, PG, application and integrated tests. No commit, push or merge in this authorized scope.

**Goal:** Deliver and verify route A: authenticated B2 bytes restored into new isolated PostgreSQL instances with complete roles, credentials, databases, data, ACLs, sequences, private configuration, media, fresh maintenance control, actual read-only app smoke and unchanged source.

**Architecture:** Add a closed B3 contract and pure comparison module first, then an authenticated preflight, target-bound SQL restore, trusted empty control bootstrap, actual observation and final CAS. Keep historical maintenance evidence separate from new control. B2 capture remains the source format; the existing private-file restore is not database restore.

**Tech Stack:** Existing Python/psycopg3, pinned PostgreSQL16 pg_restore/pg_dump, existing B2 authenticated reader and closure verifier, current wheel's fixed maintenance SQL, existing ASGI app. No new service or public restore UI.

## Global constraints and authority

- Approved design: `docs/superpowers/specs/2026-11-03-b3-isolated-restore-design.md`, SHA256 `4f8c1b733955824fe7fd7289b5be21d4b22ef49319b8d39fe060bb194ec74612`; route A authorization is `outputs/_run/b3-isolated-restore-design-20261103/approval.safe.json`. Its approved state supersedes the spec's stale PROPOSED heading. No repeated approval for ordinary authorized development.
- This document performs no tests, product imports, SQL, process/resource operations, private-material reads, network or deployment. Commands below are future execution instructions assigned to actors.
- Source and each target are separate instances/clusters; central and identity get distinct new target instances, constructed sequentially. Never use a same-cluster new database as B3 acceptance.
- Target: network none, no published ports, no host business-directory mounts, new private named data volume, readonly root, NNP, 1 CPU, 768MiB memory and 768MiB total swap, PID128. Bootstrap uses only CHOWN, SETUID, SETGID, DAC_OVERRIDE, KILL after cap-drop ALL; application worker UID/GID10001 has no capabilities. Resource manifest must freeze these exact names before create. No PGDATA tmpfs substitute.
- Root checks at least 8GiB host free plus actual peak budget; preserve 2GiB running reserve. Freeze image digest, tool hashes, payload, mounts, new-volume identity, complete CID and original deadline. A failed/unknown/late run is observed by its existing handle; no automatic new instance, retry or timeout reset.
- B3 output is `isolated_restore_verified`, `production_ready=false`, `timer_changed=false`, `provider_called=false`. No credentials, passwords, verifier, DSN, SQL, media or account records in safe reports or browser.
- Preserve all original B2/T13/T14/AV19, desktop/signing, production B3, old-ciphertext/private-key, offsite, whole-machine, pressure and multi-node DR obligations. This adds local proof; it does not close those items.
- Candidate `a6d5553286c69b3a62f8ae6d7f67f8a43b615b0cd2c0ef67bce13c45cb59e268` proves recorded B2 capture/file restore only. New B3 code requires a new artifact and complete integrated regression; never attach a6 Linux PASS to changed code.
- Previous full general attempt failed WAL ENOSPC with source PGDATA tmpfs1GiB, exit1. Preserve its stopped state and archived208 safe evidence; owned group was not started. Independent persistent-source fixture work may repair test authority and two capture consumers only; it supplies a source, never a B3 target or new-instance proof.

## Source map and integration facts

All paths below are repository relative to `/Users/jiandan/Code/GitCode/Easel`.

| Existing file | Interface / constraint |
|---|---|
| `services/platform/src/platform_service/media_backup_b2/reader.py` | `read_committed(root_anchor_intent, backup_job_fd, private_pem)` returns owned authenticated reader; `read()` must drain through EOF and `verified` before receipt. `fileno()` deliberately unavailable. |
| `media_backup_b2/source_facts.py` (same package root) | `validate_source_facts(raw)` returns facts, not restore authority. `verify_archived_bundles(bundles,fixed_records,plan)` requires exact three config bundles and actual member records; returns safe digests/profile, **not** facts. Decode central bundle separately after verification. |
| `media_backup_b2/capture.py` | `read_source_catalog(connection,profile)` is `_schema`; `roles_inventory(connection,role_names,administrative_grantors=())`; `role_credential_inventory(connection,role_names)`; `sequence_inventory(connection,schemas=('public',))`; `snapshot_copy_inventory(connection)`; `_raw_rows(connection,profile)`. Credentials already exist in `roles_export` JSON. |
| `media_backup_b2/profiles.py` | `PUBLIC_016`, `LOCAL_020`, `LOGTO_145`; `validate_profile_catalog` pins structure but its projection omits owner/ACL/maintenance triggers. That helper is insufficient as B3 equivalence. |
| `services/platform/src/platform_service/media_maintenance/schema.py` | `install_control_schema(owner_connection,expected_profile)` requires existing public migrations, media tables and credit_lots triggers. Never call it on empty DB or after dump to duplicate triggers. |
| `services/platform/src/platform_service/media_maintenance/sql/001_media_backup_control.sql` | Creates control structures/functions and initial OPEN epoch0; dynamic application references do not install public guards. New installer must execute this pinned SQL and change OPEN to NEEDS_REVIEW **in one unexposed transaction**. |
| `services/platform/src/platform_service/app.py` | `create_app(settings=None,*,database=None,identity=None,payment_runtime=None,execution_runtime=None,media_runtime=None)` lifecycle opens real DB and checks runtime role only with `database=None`; unconditionally calls media.start when present. |
| `services/platform/src/platform_service/media_runtime.py` | `start()` returns early if mode disabled; worker_enabled alone does not prevent generation registration. |
| `services/platform/src/platform_service/db.py` | `Database.open`, `require_runtime_role`, `connection`; app startup does not call migrate. Do not add migration to restore smoke. |
| `services/platform/src/platform_service/permissions.py` | `assert_safe_runtime_role(conn,role=None,check_grants=True)` and `grant_runtime_permissions(conn,role)` define real runtime boundary. Existing owned fixture grants runtime broad table DELETE/TRUNCATE, so it is not automatically an app-safe B3 source. |
| `services/platform/tests/b2_owned_pg.py` | Existing selected source authority creates source DBs/roles and destructive finally cleanup. Do not import/use this fixture as B3 target lifecycle. Coordinate with its independent owner; no changes here. |

Seven logical fixed archive files stay `db/central.dump`, `db/identity.dump`, `db/central.roles`, `db/identity.roles`, `config/central`, `config/identity`, `config/media`. Physical media is additionally verified by original closure/registry. Facts shape is exactly existing `FACT_KEYS`; schema is `{migrations,relations,identity}` where identity is `{profile,relations,image_sha256,public_sql}`. Roles are under `facts.roles[kind].before/during/after`; all three must be equal before B3 comparison. There is no standalone invented `schema.tables` or plaintext credential bundle.

## New module boundaries

Create `services/platform/src/platform_service/media_backup_b3/` with `__init__.py` (no effects), `contracts.py` (closed immutable profile/input types), `compare.py` (pure), `preflight.py` (authenticate/stage/read-only tool analysis), `roles.py` (trusted role/database reconstruction), `control.py` (fresh bootstrap/CAS), `restore.py` (ordered SQL sections), `observe.py` (fresh direct observations), `smoke.py` (actual readonly app), `pipeline.py` (single target-bound state machine), `receipt.py` (safe result).

Create corresponding `services/platform/tests/test_media_backup_b3_{contract,compare,preflight,roles,control,restore,observe,smoke,pipeline,integrated,packaging}.py`, plus stdlib-only `services/platform/tests/b3_pure_contract_runner.py`, `services/platform/tests/b3_owned_targets.py` and `services/platform/tests/b3_restore_matrix.py`. Root-only runner belongs in `scripts/verify-b3-isolated-restore.py`. Modify `media_maintenance/schema.py` only to share exact grant construction; keep its existing behavior and SQL bytes unchanged unless a separately explicit versioned contract change is necessary. No edits to the independently owned source-fixture files.

## Task 1: Pure closed contract and exact three-slot normalization

**Files:** Create `media_backup_b3/__init__.py`, `contracts.py`, `compare.py` and `test_media_backup_b3_contract.py`, `test_media_backup_b3_compare.py`, `b3_pure_contract_runner.py` at full package/test prefixes above.

**Interfaces:** `parse_restore_profile(raw: bytes) -> RestoreProfile`; `validate_observation(value: dict, context: ComparisonContext) -> dict`; `normalize_observation(value: dict, context: ComparisonContext) -> dict`; `compare_observations(source: dict,target: dict,source_context: ComparisonContext,target_context: ComparisonContext) -> ComparisonProof`. These modules import only stdlib and their own side-effect-free contracts; no B2 package, DB, app, subprocess or tests fixture imports. Proof contains only equality booleans and hashes. ComparisonContext fixes database name, exact role names, allowed grantors, profile, table/column inventory and maximums independently of observation.

- [x] Write dependency-free runner loading only these files by `importlib.util.spec_from_file_location` with synthetic package namespace. Reject non-stdlib dependencies by AST inspection. RED command (code-only actor, no pytest collection): `rtk proxy python -B services/platform/tests/b3_pure_contract_runner.py`; expect missing B3 contract assertion first, never a DB connection.
- [x] Encode exact key and row-width definitions from the actual queries:

```python
CATALOG_WIDTHS = {
 'columns':17,'constraints':4,'functions':6,'enums':2,'triggers':5,
 'indexes':4,'policies':8,'namespaces':3,'default_acls':4,'database':6,
 'types':6,'relation_options':10,'sequence_definitions':13,'rules':4,
 'operators':5,'collations':9,'casts':7,'extensions':3,'event_triggers':5,
}
ROLE_WIDTHS = {'roles':11,'memberships':7,'ownership':4,'acls':7,'credentials':3}
```

`roles` rows are `[name,super,inherit,createrole,createdb,login,replication,connlimit,validuntil,bypassrls,config]`; `memberships` are `[role,member,admin,grantor,inherit,set,xmin]`; credentials `[name,verifier_or_null,xmin]`; ownership `[schema,name,owner,acl_text_or_null]`; ACL `[schema,name,kind,grantor,grantee,privilege,is_grantable]`. Do not confuse sequence-definition min/max positions with `sequence_inventory` dictionary order (`max` before `min` there).

- [x] Define closed observation `{schema,roles,rows,copy,sequences}` with schema the 19-key catalog, roles above, raw rows exact trusted profile tables/columns, COPY keys exact `public.<table>`, and sequences exact existing 16 dictionary fields (`schema,name,owner,data_type,start,increment,max,min,cache,cycle,owned_schema,owned_table,owned_column,dependency,last_value,is_called`). Catalog enums are `[type_name,list_of_labels]`; namespace is absent there. Column attnum/name/type can be null for relation without columns; do not replace legitimate null with empty string.
- [x] Reject duplicate JSON keys, NaN/Infinity/floats, bool-as-int, missing/extra keys, malformed nested values, duplicate logical rows and unsorted query rows. Limit input to 8MiB, 100000 rows/table, 1000 COPY tables, 512 columns/table, identifiers to ASCII `[A-Za-z_][A-Za-z0-9_]{0,62}`, verifier <=1024 and explicit SCRAM/allowed MD5/null forms, xmin decimal string, recursion depth<=32. Explicit per-column types and nullable fields follow pinned SQL above; GUC arrays are null or unique strings with independently allowed keys, never arbitrary executable input. Source/target role sets, ownership/grantor references and profile pins checked before normalization.
- [x] Copy before modifying; normalization changes only:

```python
normalized = copy.deepcopy(validated)
normalized['schema']['database'][0][0] = '<trusted-database>'
for row in normalized['roles']['memberships']:
    row[6] = '<membership-xmin>'
for row in normalized['roles']['credentials']:
    row[2] = '<credential-xmin>'
```

Before replacement require exactly one database row and name equal to that side's trusted context. Credentials and memberships must already have valid exact-width/typed rows. No global OID/id/xmin deletion, string replacement, sorting away malformed order, tolerant SQL equality or permissions projection. Original input must compare byte-equal after call.
- [x] Tests use copied complete synthetic valid observation, vary just these three positions and prove equality; then mutate database owner/ACL/connlimit, membership role/member/grantor/admin/inherit/set, credential name/verifier and prove rejection. Mutate every non-exempt catalog position including RLS, function search_path/body/owner, trigger enabled, default ACL and sequence dependency. Test extra nested fields, invalid width, floats/bools/duplicates, bad bounds, untrusted DB names, cross-table row omission, input nonmutation and no normalization outside the three paths.
- [x] GREEN same runner, independent review of exact shapes and closed limits. Deliver pure contract only. Full PG/control filtering and archive orchestration are later tasks; Task1 does not wait for source-PG fixture setup.

**Acceptance:** three approved differences only; all adjacent changes fail; no runtime effects. No partial B3 completion claim.

## Task 2: Authenticate complete archive and preflight actual dump before SQL

**Files:** Create `media_backup_b3/preflight.py`, `test_media_backup_b3_preflight.py`.

**Interfaces:** `preflight_restore(intent,backup_job_fd,private_pem,profile,parser_runner,stage_parent_fd) -> VerifiedRestoreInput`. This owned nonserializable object holds verified staged FDs, source facts, normalized roles, trusted profile, plan/source/registry/object hashes, tool/preflight evidence and deadline; no caller-created dict is accepted as authority. `VerifiedRestoreInput.close()` closes FDs, retains failure materials according to job policy. `parser_runner.run(tool,args,input_fd,output_fd,maximum,deadline)` has no target connection and no network.

- [ ] RED tests instrument target factory with a hard failure: wrong anchor/key, truncation, appended bytes, wrong recipient/commit/object SHA, changed seven members, bundle cross-binding and source facts tampering must never call target factory or any SQL.
- [ ] Compose existing `read_committed`, existing archive closure validation and `verify_archived_bundles`. Drain actual ciphertext to authenticated FINAL/EOF; then re-open only verified immutable staged handles. Verify fixed records and entire media registry. Decode central bundle after cross-binding to obtain facts; parse both roles JSON with exact envelope `{format:'easel-roles-v1',inventory:...}` and require bytes/digests agree with before/during/after. No new role-capture format.
- [ ] Execute pinned `pg_restore --list`, `--schema-only --file=- --restrict-key=easelB2SchemaComparison20261030`, and `--data-only --file=-` against actual dump FD in no-network parser. Parse TOC into closed allowed categories and exact object set derived from trusted profile plus authenticated catalog. Reject extra schema/object/DB creation/unrecognized commands; do not silently filter TOC. Schema bytes must match bound size/SHA and original DDL comparison. COPY via existing `parse_dump_copy_inventory` must exactly match `snapshot_projections[kind]`; sequence output must agree with before/dump/after and catalog definition/ownership/dependency.
- [ ] Exercise extra function/extension/event trigger/foreign table, missing COPY column/row, changed sequence value/is_called and dump DDL hash. Positive fixture includes original maintenance triggers and must not strip them. Limits preserve 8MiB schema/facts, 16MiB per bundle and 32MiB aggregate; dump/media streaming uses frozen archive limits, never unlimited subprocess capture.
- [ ] Root runs `rtk proxy .venv/bin/python -B -m pytest --noconftest services/platform/tests/test_media_backup_b3_preflight.py -q` only after test source inspected for resource effects. Expected all cases pass, SQL-call count zero for every reject. Code-only actors use extracted pure parsers only.

**Acceptance:** all archive-derived content authenticated and pinned before target create or archive SQL; input failure cannot leave a runnable target.

## Task 3: Root-owned new-instance authority and bounded lifecycle

**Files:** Create `b3_owned_targets.py`, `scripts/verify-b3-isolated-restore.py`, `test_media_backup_b3_pipeline.py` (authority unit section).

**Interfaces:** `TargetIdentity` freezes restore_id, kind, full CID, cluster system identifier, image, volume/mount digest, bootstrap identifier, limits and deadline; `TargetHandle.verify()` rereads exact identity; `RootTargetFactory.create(verified_input,kind) -> TargetHandle` is Root-only. No exported DSN in repr/report. `TargetHandle.connection(role)` is only private in-container socket/loopback connection. New bootstrap differs from every restored role and source admin.

- [ ] Unit RED with fake inspect results: reject reused/existing-started CID, same source system identifier, shared central/identity cluster, mutable mounts, host paths/ports, network bridge, extra cap, missing swap/PID limit, deadline extension, volume reuse, wrong image and resource drift.
- [ ] Implement frozen manifest builder (no start in builder), then exact inspect/create/start phases. Root validates complete payload and resource manifest under original T13 controls before each single start. At target start capture system identifier and exact CID; bind both to job ledger. No discovery by name prefixes or source credential fallback.
- [ ] Store private ledger root-owned mode0600, directory0700, exclusive-create, monotonic original deadline; atomically preserve each transition. Enforce one start per target; late completion can only observe original handle. Resource stop on deadline is targeted and recorded, never silently delete volume/logs.
- [ ] Root's executable entry accepts `--manifest-fd`, `--authority-fd`, `--report-fd`; descriptors are preopened private files, not DSNs/passwords in argv. `--prepare` produces payload/manifest only; `--execute` consumes sealed manifest and performs actual single run. Flags do not let archive choose images/caps/paths.
- [ ] Run pure authority suite; independent review before Root prepares Linux payload. Freeze exact cached image and five capabilities CHOWN, SETUID, SETGID, DAC_OVERRIDE, KILL from original authorized bootstrap; no moving tag or inferred broader authorization.

**Acceptance:** isolated targets independent from source persistent fixture; no real creation by code actors; empty-state identity observed before any restoration.

## Task 4: Trusted role, credential and database reconstruction

**Files:** Create `media_backup_b3/roles.py`, `test_media_backup_b3_roles.py`.

**Interfaces:** `restore_roles(target: TargetHandle,verified: VerifiedRestoreInput,kind: str) -> RoleCreationProof`; `create_database(target,verified,kind) -> DatabaseCreationProof`; `enable_original_logins(target,verified,kind) -> AuthenticationProof`. All helpers require verified authority and target identity at entry/exit.

- [ ] RED compile tests for unknown role, permission over ceiling, bootstrap collision, missing/duplicate credential, malformed SCRAM/MD5, unauthorized grantor, unsupported GUC/member option. Fail before any mutation on validation errors.
- [ ] Build all roles initially NOLOGIN using trusted Identifier for names and Literal/parameters for values. PostgreSQL utility commands may not support bind placeholders for every value; use psycopg SQL composables with sensitive values strictly inside private protocol, never formatted public log. Restore super/inherit/createrole/createdb/replication/bypassrls only to independently approved exact attributes, connection limit, validuntil, role config, verifier/null. Do not manufacture a credential for null or skip unsupported fields.
- [ ] Restore memberships with actual admin/inherit/set and original grantor. If source administrative grantor is outside exported role set, independent profile must supply its exact required attributes/credentials and authorize recreating that named role in new cluster; otherwise reject. Never replace grantor with new bootstrap and normalize it away. Restore dependencies in a deterministic authorized grant order; cyclic/unsatisfied grant authority fails closed.
- [ ] Obtain encoding, locale/collation provider/version and DB/role database settings from separately trusted source-context captured by Root for this run: existing `_schema.database` contains only six fields and cannot establish those missing values. Bind this context hash in profile/ledger. Unsupported/absent source context means no complete restore, not a guessed default. Keep this context outside archive authority; do not expand seven fixed files silently.
- [ ] CREATE DATABASE using profile exact target name/owner/template0 and trusted create attributes, then connection limit/datallowconn/template flag, role-in-database settings and database ACL. Preserve owner and grants. Prefer exact same DB names on different instances: `roles.acls` database row also contains db name and the approved comparator does not normalize that field. Different target name with conflicting ACL facts must fail, not add a fourth exception.
- [ ] After content restored, apply original LOGIN flags, leaving original NOLOGIN unchanged. Authenticate from private in-container loopback under HBA requiring password: correct synthetic password succeeds, wrong password fails, null-password and NOLOGIN deny; verify protocol did not use trust/peer. Query actual pg_roles/pg_authid/pg_auth_members and retain private comparison, publish booleans only.
- [ ] Root runs `test_media_backup_b3_roles.py` on real new targets: owner/runtime/controller plus Logto role memberships; denied role escalation, bad grants, exact grantor options and password controls. No mock-only PASS.

**Acceptance:** exact role names and permissions restored in new cluster; bootstrap separate; database attributes proven, credentials neither leaked nor inferred missing.

## Task 5: Empty database control bootstrap, grants and atomic final CAS

**Files:** Create `media_backup_b3/control.py`, `test_media_backup_b3_control.py`; modify `services/platform/src/platform_service/media_maintenance/schema.py` to extract reusable `grant_control_access(owner_connection,expected_profile)` with unchanged exact function lists and grants.

**Interfaces:** `bootstrap_restore_control(owner_connection,profile,job_identity) -> FreshControlProof`; `verify_fresh_control(owner_connection,proof) -> FreshControlProof`; `complete_restore_control(owner_connection,fresh_proof,all_proofs) -> OpenProof`. Proof binds initial row, trusted SQL checksum, owner/grants, restore_id and target CID/system identifier.

- [ ] RED real empty-DB test proves existing installer cannot be used; test new helper on empty DB with no public migrations/media tables. In one transaction acquire fixed advisory lock, assert control schema absent, execute current pinned `_SQL`, insert settings/schema_migrations and UPDATE only new `(id=1,state=OPEN,epoch=0,window_id NULL,owner_hash NULL,binding NULL,original_state NULL)` to NEEDS_REVIEW. Require rowcount1 and zero workers/attestations/permits/audit. Commit only then. Rollback injection at every statement leaves no visible OPEN.
- [ ] Execute trusted SQL as restored owner (bootstrap SET ROLE under bound connection) so new control ownership is correct; restore-only target remains inaccessible. Shared grant helper grants schema/view SELECT and exact existing runtime/controller function lists; no broad ALL FUNCTIONS grant. Public triggers come from dump later, not this helper.
- [ ] Identity gets fresh control only if its authenticated schema/profile includes that approved control contract; fixed Logto dump normally has public only. Do not insert central media control objects into Logto and claim equality. Record per-database expected control presence explicitly in trusted profile.
- [ ] Historical snapshot stays immutable evidence; never import its control/worker/permit/audit rows. Compare new control structures independently to fixed SQL/profile grants; source history separately retains its original checksum/bindings. Source worker token/generation cannot authenticate in new target.
- [ ] Implement CAS after all required proofs. Lock control row and relevant control tables against concurrent insert; assert exact original fresh row epoch0/NEEDS_REVIEW and empty workers/attestations/permits/audit, unchanged role/grant/schema/target identities, original deadline live, source postcheck complete. In one transaction insert audit `restore_verified` (new sequence), then UPDATE exact row to OPEN, epoch1, null window/owner/binding/original_state; require rowcount1. Audit includes only restore_id and proof hashes. If any check fails rollback; never open in finally or reuse `transition(old_window)`.
- [ ] Test stale epoch, injected worker/permit, unexpected audit, wrong control owner, schema checksum mismatch, replayed completion, cancellation, crash before/after commit observation. Test runtime cannot call completion; controller normal transition does not acquire restore completion power. Delayed observation checks original handle and exact audit, no replay.

**Acceptance:** never expose intermediate OPEN; source historical control never becomes live authority; successful local finalization has one precise CAS/audit.

## Task 6: Transactional public restore, configuration and full media

**Files:** Create `media_backup_b3/restore.py`, `test_media_backup_b3_restore.py`.

**Interfaces:** `restore_content(target,verified,kind,fresh_control) -> RestoreExecutionProof`; `restore_private_files(verified,target_mapping) -> FilesProof`. target_mapping is immutable trusted UID/GID/path map of directory FDs, not archive paths.

- [ ] RED real PG cases: nonempty target, wrong owner, changed verified FD, missing control function, unknown TOC, restore error and mid-section crash remain NEEDS_REVIEW and no success.
- [ ] Apply verified pre-data, data, post-data in original PG order with fixed pg_restore `--exit-on-error --single-transaction`, preserving owner and ACL (no no-owner/no-acl). Prefer one full archive transaction where accepted; if section control is needed, execute all sections inside one controlled transaction via private generated SQL FD with ON_ERROR_STOP and pinned psql, record exact boundary. Do not promise a transaction across CREATE DATABASE or both separate instances.
- [ ] Original guards are post-data triggers, so data loads before guard creation without disabling guards. Bootstrap control exists before trigger function resolution. Never `session_replication_role=replica`, DISABLE TRIGGER or omit guards. Enforce public trigger exact definitions and deferred lot constraints after restore. Only frozen R2 exact constraint fix, matched to input/output pins, is permitted; arbitrary pg_get_constraintdef differences fail.
- [ ] Restore sequences including last_value/is_called, definition, owner and dependency; verify after post-data. Restore database ACL/settings explicitly from Task4 context because public-schema pg_dump is not a full CREATE DATABASE backup.
- [ ] Reuse closure extraction into exclusive private directory and verify every physical file by FD/hash/size/uid/gid/mode under trusted mapping. Compare all original config bytes before separately constructing smoke-only disabled settings; keep originals immutable. Reject links, traversal, path collisions, unexpected file/missing/deleted resurrection, size/owner drift. No automatic GC/refund/settlement.
- [ ] Root executes restore suite against actual PG and disk; failure retains private stage/target/logs, source fingerprint unchanged.

**Acceptance:** actual complete database and file restoration, not merely pg_restore exit0; all errors closed and source preserved.

## Task 7: Full fresh observations and actual read-only application smoke

**Files:** Create `media_backup_b3/observe.py`, `smoke.py`, `test_media_backup_b3_observe.py`, `test_media_backup_b3_smoke.py`; modify `services/platform/src/platform_service/permissions.py` and corresponding permission regression tests for explicit PUBLIC_016 profile support.

**Interfaces:** `observe_target(target,verified,kind) -> dict` returns Task1 shape; `verify_target(target,verified,kind,fresh_control) -> ComparisonProof`; `run_readonly_app_smoke(target,verified,synthetic_credentials) -> SmokeProof`.

- [ ] Query new target directly with same 19 catalogs, raw rows, COPY, sequence and role/credential inventory as source. Source facts contain central raw rows but identity equality also needs exact actual COPY for every identity table. Do not invent missing identity raw facts; trusted source read-only observation may independently supply them bound to same capture, otherwise COPY is complete row-content proof for identity. Keep row ordering/digests identical.
- [ ] Partition control-specific catalog/ownership/ACL rows by explicit category schema positions, including casts' both endpoint schemas and functions. Unknown cross-control dependency rejects. Validate every excluded source row against authenticated maintenance history and every target row against current fixed control contract; no blind drop. Public guard triggers and every other catalog row remain strict. `profile_catalog_digest` supplements, never replaces Task1 equality.
- [ ] Compare complete finance/account/member/order/admin/audit rows, exact actual=NULL, held/spent/reserved lineage, tenant/product scope, missing/deleted state, and full media/config. Add single-field mutations for each class, not balance-only checks.
- [ ] Build B3 synthetic **source** with exact `grant_runtime_permissions` and required migrations before capture. Existing broad-grant b2 fixture cannot satisfy real runtime-role check. Actual `grant_runtime_permissions` iterates SELECT_TABLES/INSERT_TABLES/UPDATE_COLUMNS unconditionally, including media_protocol_facts/media_work/voice tables absent in PUBLIC_016. Add explicit pinned-profile argument to granting/checking: validate exact migrations and table inventory first, select only that profile's present tables/columns from the same immutable policy, and reject unknown/missing expected tables. Keep default LOCAL_020 behavior and privilege ceilings. Propagate explicit profile from B3 synthetic source setup and app runtime validation without granting broader permissions. Do not bypass `check_grants`, inject fake DB or migrate 016 to020. Commit grants then call `assert_safe_runtime_role` on owner-selected runtime and again from actual app connection. Follow public `outputs/_run/central-catalog-linux-candidate-20261103/r2-author/minimum_role.py:enforce_minimum_runtime` for ordering. Review changed permission helper and run both profiles' regression.
- [ ] Start actual installed app `create_app(settings)` with `database=None` so real pool/open/runtime-role check executes. Supply target-only postgres URL (Settings requires postgres/postgresql URL, not arbitrary conninfo), loopback public_url/TrustedHost match, pool bounds and default_transaction_read_only=on on smoke connections without changing archived role GUCs. Use actual ASGI lifespan and HTTP transport inside same no-network process; no host port needed.
- [ ] Use actual disabled PaymentRuntime/ExecutionRuntime/MediaRuntime via clean explicit configuration: payments disabled+worker false; execution/media config files independently generated mode disabled+worker false+empty providers, mode0600/app UID. `CENTRAL_PAYMENTS_MODE=disabled`, `CENTRAL_PAYMENTS_WORKER=0`, `CENTRAL_EXECUTION_CONFIG_FILE`, `CENTRAL_MEDIA_CONFIG_FILE` are actual readers. No fictional CENTRAL_MEDIA_MODE variable. Restore original private configs separately; do not launch them. media.start is still called but disabled returns before generation registration.
- [ ] GET `/health` must query real DB and return ok; authenticate previously seeded unexpired synthetic access token from captured identity_tokens/session and GET `/v2/me`, `/v2/wallet`, `/v2/usage`, entitlements and sessions. No OIDC authorize/callback, refresh or provider. Use genuine IdentityService, no fake identity injection. Invalid token and cross-product/tenant negative controls reject. Before/after raw table/COPY/control/sequence observations equal; SQL read-only rejection is a second guard if endpoint unexpectedly writes. Package missing later tables or permission failure is a real blocker to fix, not skip.
- [ ] Root proves new worker registration only after CAS in a separate synthetic activation check; old token/generation rejects. Positive writes limited to fresh synthetic transaction/permit, rollback business mutations and record intentional control audit/generation differences. Keep pre-CAS equivalence evidence distinct.

**Acceptance:** actual installed application reads restored target with providers/workers off, zero source/target business mutations, no claim of SSO/live provider acceptance.

## Task 8: Orchestration, crash retention and safe receipt

**Files:** Create `media_backup_b3/pipeline.py`, `receipt.py`; extend `test_media_backup_b3_pipeline.py`.

**Interfaces:** `run_isolated_restore(intent,backup_job_fd,private_pem,profile,root_factory,source_observer,job_ledger) -> SafeRestoreReceipt`. Owned handles only; no untrusted dict success shortcut.

- [ ] State machine is `NEW -> AUTHENTICATED -> PREFLIGHTED -> TARGET_BOUND -> ROLES_CREATED -> CONTROL_CLOSED -> CONTENT_RESTORED -> LOGIN_CHECKED -> COMPARED -> APP_READONLY_VERIFIED -> SOURCE_PRESERVED -> OPEN_COMMITTED -> FRESH_OBSERVED`. Every failure terminal is retained NEEDS_REVIEW/FAILED/UNKNOWN; no finally OPEN or new run.
- [ ] Keep central and identity per-target proofs; open central only when both complete, original source files/DB/roles/control and timer unchanged, app checks passed and target final reread still fresh. A central success cannot mask identity failure. Obtain source postcheck using original owned source authority, read-only, no restoring archived source control.
- [ ] Safe receipt exact closed fields: format, status, restore_id, source/profile/artifact/object/plan hashes, per-target identity hashes, tool/image hashes, comparison booleans, authentication booleans, control-before/after hashes, deadline outcome, source_preserved, production_ready/timer_changed/provider_called. No exception repr or private value fallback. Unknown state never serializes success.
- [ ] Inject crash/cancel at every transition, target identity drift, source mutation, late finish, ledger fsync failure and receipt write failure. Observe original exact handle; no new resources. Test actual kill in Root integration at restore and before/after CAS boundaries in dedicated targets; retain private evidence.

**Acceptance:** only fully observed terminal proof can emit success; failure attribution and original deadline survive interruptions.

## Task 9: Root actual matrix, rebuilt artifact and complete regression

**Files:** Create `b3_restore_matrix.py`, `test_media_backup_b3_integrated.py`, `test_media_backup_b3_packaging.py`; extend root runner; add safe evidence under a new B3 run directory chosen by Root, not a6's results.

- [ ] Freeze fresh artifact/wheel hashes, source revision/file hashes, tests, trusted SQL, PG tools, manifests and matrix before unique execution. Package new modules and trusted resources, install outside source, prove imports resolve inside installed artifact. Re-run existing B2 suite and source fixture groups for this candidate; no reuse of old Linux status.
- [ ] Source matrix: central PUBLIC_016 and LOCAL_020 each crossed with public identity016, public identity020 and pinned Logto1.45.0 (six combinations). Each combination covers empty and nonempty physical media, settled+actualNULL/unknown finance, missing/deleted history and all raw business tables. Every target pair fresh; do not overwrite/reuse prior case's cluster. Source synthetic passwords and data are generated/held by Root independently of candidate outputs.
- [ ] Root executes actual roles/password/grantor, PG rejection and full positive restore; actor matrix includes new bootstrap, restored owner, runtime, controller, original/source admin negative, restored identity/Logto tenant roles, unknown role, old worker credentials and new worker. Test runtime/controller cannot alter guard, control or immutable ledger; cross-tenant reads/mutations denied under actual role/RLS.
- [ ] Negative matrix must include wrong anchor/key; truncation/extra object; forbidden role privilege; missing verifier; owner/ACL/member options/grantor; sequence value/is_called/dependency; RLS/function body/search_path; missing resurrection; tenant scope; amount/actual NULL mutation; old control replay; wrong instance; resource/mount drift; timeout; restore crash; duplicate completion. Archive/preflight rejects prove no SQL; SQL/role rejection cases use actual PG, not only mocks. No successful receipt; source and timer unchanged for each.
- [ ] Run commands through Root-owned runner with inherited FD authority:

```text
rtk proxy python -B scripts/verify-b3-isolated-restore.py --prepare --manifest-fd 3 --authority-fd 4 --report-fd 5
rtk proxy python -B scripts/verify-b3-isolated-restore.py --execute --manifest-fd 3 --authority-fd 4 --report-fd 5
```

Descriptors are opened by Root after exact manifest review. Runner executes installed tests by explicit frozen selection, not uncontrolled discovery; writes safe summary and private logs separately. Full `services/platform/tests` regression occurs only under selected persistent source authority with disk budget and fixed original deadline, in addition to B3 fresh-target matrix. Preserve original B2/T13/T14 selections and cancellation/large resource tests; failed or unrun suites reported individually.
- [ ] Report by case: actual CID/volume/system-identity binding, source capture fingerprints, ciphertext validation, execution exit, complete comparison, login controls, smoke, fresh control CAS, source postcheck, original deadline terminal and current artifact provenance. Independent source review then evidence review; safe manifest alone is not runtime proof.
- [ ] After complete archive/review, only targeted already-authorized successful-resource cleanup; do not remove volumes, failure logs or history backups. Production follow-up remains a separate concrete deployment/real-key/timer plan requiring actual deployment approval at that stage, not a development blocker.

**Acceptance:** current candidate complete B3-L matrix and existing regression, independently reviewed; clear unresolved production/desktop/AV/DR requirements retained.

## Self-review and handoff

Tasks1–2 cover closed trust/preflight; Tasks3–4 separate instances and complete role/database metadata; Task5 fresh control; Task6 SQL/media/config; Task7 full semantic facts and actual app; Task8 failure/source/receipt; Task9 complete actual matrix/current artifact. No current runtime PASS claimed. Every task ends with independent source review plus its own bounded RED/GREEN; Root executes all product/pytest/PG/resource tests. No commit task is implied because current scope says no commit/push/merge. Task1 is ready independently of source fixture work. A source fixture repair and B3-target restore remain distinct workstreams.

## Reference source hashes (planning-time bytes, recheck before implementing)

| Source | SHA256 |
|---|---|
| `docs/superpowers/specs/2026-11-03-b3-isolated-restore-design.md` | `4f8c1b733955824fe7fd7289b5be21d4b22ef49319b8d39fe060bb194ec74612` |
| `outputs/_run/b3-isolated-restore-design-20261103/approval.safe.json` | `76f9b23404f25f25897ec9384b90abb63e042094c8d0b79bc35201e2cd36de8b` |
| `services/platform/src/platform_service/media_backup_b2/capture.py` | `8281992395e1584b30e852c789ebe052ffe4d443c76241b911e38ee2d054d2bb` |
| `services/platform/src/platform_service/media_backup_b2/source_facts.py` | `f1391a856fe3988902ad84cac0f2467717f5e09384937e34c8cdbd1f001ee4a1` |
| `services/platform/src/platform_service/media_backup_b2/profiles.py` | `11e867a15427136c441562e764f6048a3f0da1df4a54676cca7305f11c059fa3` |
| `services/platform/src/platform_service/media_backup_b2/reader.py` | `f2390571649d572c61889883ebf397c93aa387b7f07ebd7fdf55246c661d862a` |
| `services/platform/src/platform_service/media_backup_b2/bundles.py` | `4b7dde30565eb0a8f598e1f04ae586c353322b9a526278f5ce3a8d2a8a063e5e` |
| `services/platform/src/platform_service/media_maintenance/schema.py` | `5fd066f421b48414d3e80fbbaced241801fcc3ba6bbf45d234729eed91b8a99b` |
| `services/platform/src/platform_service/media_maintenance/sql/001_media_backup_control.sql` | `6ce3762c4f63b81a659738faf7189ce86323cbb7961dd8abfb27708074638bba` |
| `services/platform/src/platform_service/app.py` | `393f91962d5064054b2aea6beda1ce790bbbb9c810567be92456060373550afd` |
| `services/platform/src/platform_service/config.py` | `222d983c15141109a2350d340ade1b29767985c72bf7e058bbc3a7b25ac5e5ca` |
| `services/platform/src/platform_service/db.py` | `0e36967df9835b2ca8a4992fee3bc7e36c2e6813fbf08676aee65e3e684bf025` |
| `services/platform/src/platform_service/permissions.py` | `6d3d9242954042b333053ca713465e1bb278e934717ad4f8d436a4c34d15c8a0` |
| `services/platform/src/platform_service/media_runtime.py` | `462ee44f7259e81a55df310ab8edcd155c5a310605998cdf14bc9f6a7ee439cc` |
| `services/platform/src/platform_service/media_config.py` | `b656b82677736b93ebddc88f8a692506799527356f64177b6847a2cd9f3c737c` |
| `services/platform/src/platform_service/execution_config.py` | `9da6b77c1b6d2256fe29abca29c537a2dffcfad20a1124ed5b764d05209a94ce` |
| `services/platform/src/platform_service/payment_config.py` | `2b5bb726e65b648e447acdf39686fe2d92b108fc278515d526246d59fa07ee25` |
| `services/platform/src/platform_service/identity/__init__.py` | `e9c5b2e551d034cd91b717b88874c0b736cefbe625413d9b7291fc28a2fd00d1` |
| `docs/superpowers/plans/2026-10-30-media-backup-b2.md` | `3373f7b0267cea0a47ee7275d3c72599345b77fd768a9b08e8a78a0ba4c470d3` |
| `docs/superpowers/plans/2026-11-03-owned-pg-disk-fixture.md` | `bc55bef0994ec337df49cb8ad0d263a9131b420c9658791a87c962d9e6ef71fd` |
| `outputs/_run/central-catalog-linux-candidate-20261103/r2-author/minimum_role.py` | `4609279e962cbe5180a84d6c6aba61906a46c6044e76df2de6299798468590db` |
