# S5-A2 Task 3 owned recovery harness

Status: implemented for review; **actual drill NOT RUN**. Waiting for core010/collector freeze and root's explicit continuation. No Docker, network, database, real identity/provider/payment operation occurred in this subtask checkpoint. Only the two owned new repository files were written, plus this private-stage report.

## Test-first evidence

- Contract first: observed expected assertion RED1 because verify_identity_restore_drill.py did not exist,0.03s.
- Initial pure boundary suite GREEN25/0.03s: default plan gate, exactDSN/ownedcontainer binding, nonce names, scoped dump/restore argv and canonical comparison.
- Added financial/admin/pending/alias semantic fixture requirements: observed RED11failed/29passed0.08s for absent semantic validator.
- Implemented strict semantic assertions: latest pure suite GREEN40/0.03s. No test requires/opens PostgreSQL or Docker; private-file tests only use synthetic temporary paths/data.
- Current two Python files AST and trailing-whitespace checks PASS. Full actual integration remains unverified at this checkpoint.

Command:

    rtk proxy services/platform/.venv/bin/python -m pytest services/platform/tests/test_identity_restore_drill.py -q --tb=short

## Execution and ownership

Default CLI returns executed=false/verification=not_run without reading credentials, inspectingDocker or connectingPG. Mutation requires explicit --execute-owned-drill and a new --output directory. DSN accepts only /private/tmp/easel-console-20261023/test-dsn-private.json, ordinary current-uid0600 boundedfile, exact127.0.0.1:18777 and dedicated easel_s1_test_console_cbabf5af7060 db, no host/service/options indirection.

Preflight inspects only public metadata of exact easel-console-tests-cbabf5af7060: running, fixedpostgres image digest, onlyloopback5432→18777, memory805306368 and data tmpfs536870912. No Config.Env or password inspection. No container create/start/stop/restart/delete exists.

Each execution has a random nonce manifest. Source: one new s1_test_drill_<nonce>schema inside existing dedicated testDB. Targets: one new easel_s1_test_restore_<nonce> and two new easel_s1_test_idp_src/dst_<nonce> databases, all previously absent andcreated on the exacttest connection. Each restoredtarget must be empty; preserve original schema name. pg_dump only selects owned schema; pg_restore exactnewDB, no--clean/no--create, --single-transaction/--exit-on-error/--no-owner/--no-acl. Tools execute as postgres via localpeer inside ownedcontainer, no passwordargv or envread.

## Synthetic data and checks

Seed uses existing wallet/execution fixtures with controlledissuer: membership/lot, one actual synthetic service-settled job2000microcredits and one unknown pendingjob preservingheld; signed MockTransport OIDC creates an admin/session plus a separate disabledviewer. No actualHTTP/provider call. The minimal synthetic Logto users/applications are dumped/restored into differentownedIDP databases with same schema. **Actual collect_restore_evidence** binds the private identity.dump SHA and actual equal inventories; IdentityMigrationService.record_proof/apply creates productalias+twoConsolealiases+immutablemigrationaudit. No handwritten evidence/proofJSON is accepted.

Source and target use real READONLY REPEATABLEREAD snapshots: allordinarytables(to_jsonb rows, boundcount/bytes), orderedcolumns/defaults, constraints/indexes, functions/triggers/views/RLSpolicies and sequence definitions+last_value/is_called. FullcanonicalSHA/count equality before/after dump and targetrestore. Financial predicates check sixbucket conservation, journalzero-sums and walletlot↔journalreconciliation; settled/pending/held, membership,active/disabledadmin and exactsameuseraliascounts. Sourcefullrows remain in memory, output onlycounts/hash/booleans.

Target randomproduct andConsoleLOGIN roles are granted by existinghelpers and connectedasactualroles. Bothstartupguards run; restoredtarget ConsoleAuth validates the preserved synthetic source session and readswallet viaactualConsoleQueries; directidentity_tokens secretcolumn read mustfail. The targetfullsnapshot mustremainunchanged afterthesechecks.

## Artifacts and cleanup

Outputdir0700, files0600/exclusive. Retains central.dump,identity.dump,privateconnections and nonceownedobjectsmanifest plus restore-safe.json(counts/hashes,noneofsubject/token/sessionhash/CSRF/DSN). Failure writes fixed safephaseonly andpreservespartialobjects/artifacts,neverreportsPASS. No stdout/stderr from childtools is printed.

Default keeps ownedobjects. Optional --cleanup-owned, onlyafter successfulchecks, drops only in-memorytrackednonceDBs,source schema andfreshroles afterownerchecks/poolsclosed; noFORCE/pg_terminate_backend orbroaderDROP OWNED. Container isneverstopped. A failed cleanupdoesnot count asoverallPASS. Root controlsactualexecutionandeventualcontainerlifecycle.

Planned actualcommand (NOT RUN):

    rtk proxy services/platform/.venv/bin/python services/platform/scripts/verify_identity_restore_drill.py --execute-owned-drill --output NEW_PRIVATE_STAGE_DIRECTORY

Cleanup can beexplicitlyaddedwith --cleanup-owned. Root must firstsendcore010freeze+execution signal.

## Current file hashes

{
  "services/platform/scripts/verify_identity_restore_drill.py": "edc83670b22525a3a814fc909fb869a4315fe4a18993355c5326ea4e3b128747",
  "services/platform/tests/test_identity_restore_drill.py": "4386aee0937a6b097854c1451cd544c7215e475e81d9f8ba48e1590f3a079ca6"
}

## Sequence OWNED BY review fix (still no actual drill)

Root requested sequence ownership semantics in addition to definition/state. Added an independent synthetic-connection test that exercises the actual snapshot function and changes only the owned-by column. Observed RED1/0.03s: sequence hashes incorrectly equal; implemented pg_depend auto/internal dependencies projected as sequence/schema/table/column/type names, never OIDs. Latest full pure suite GREEN41/0.04s; AST/whitespace passed. Actual DB/Docker restore remains NOT RUN pending root instruction.

Frozen hashes supersede prior checkpoint:
{
  "services/platform/scripts/verify_identity_restore_drill.py": "7dbc8560c51ad1085707c947d34e8a1bcf84fdb1a1675d51bdc7a97b7f93c1a0",
  "services/platform/tests/test_identity_restore_drill.py": "06dce5e981df1e9e8d7e919108be2b438f5f2da7225a4ca6d7d93ae380635651"
}

## First authorized actual execution — failed, evidence retained

Executed once with root's explicit authorization after final core/collector freeze:

`rtk proxy services/platform/.venv/bin/python services/platform/scripts/verify_identity_restore_drill.py --execute-owned-drill --cleanup-owned --output /private/tmp/easel-identity-continuity-20261023/actual-restore-v1`

The command exited 1 with safe phase `restore_to_new_empty_database`, code `identity_restore_drill_failed`. This is not a successful restoration. No blind rerun or cleanup was attempted. The failure handler retained the owned nonce objects for diagnosis and did not stop the container. Raw child stderr/exception and private connection material were not printed. Root was informed before any further database action.

Retained private output (all 0600): central.dump 273468 bytes, identity.dump 3555 bytes, connections-private.json, owned-objects.json and restore-failure-safe.json. Failure receipt explicitly says partial_objects_may_remain=true and container_stopped=false. Output directory is private. Actual complete source/target equality, restored low-role guards and cleanup remain unverified for this run.

### Authorized read-only failure diagnosis

Read only the nonce-bound source and new restore databases, projected ownership/schema-presence booleans, public table names/counts and no rows. Both actual current/session users own their databases. Source snapshot succeeds and includes all 10 migrations, normalized proof candidates and applied aliases. Target owned schema is absent and the namespace count is 1. Static SQL inspection identifies the cause: the empty-target check excluded pg_* and public but not PostgreSQL's built-in information_schema. A pristine database therefore fails before pg_restore is invoked. The failure did not demonstrate a dump/restore or core SQL defect. Safe diagnostic receipt: actual-restore-v1/restore-diagnosis-safe.json. No replay/restore/cleanup was attempted during diagnosis; fixing this harness precondition awaits root's authorization.

## v1 harness precondition correction and second actual run

Root authorized the narrow built-in namespace correction. Extracted the old rule into validate_empty_namespaces: expected RED1failed/42passed0.06s (information_schema rejected, unknown schema still rejected); added only information_schema to pg_*/public exclusions: GREEN43passed0.04s. Script frozen SHA d59a12f2f5b4d62c5e7973b1b1c4debbd9c4e0c4924297b3087be35aa4a5b087; tests5c6cea681a5605bf71ab0aca3da50dd44e46bdf5e1cc2f72ebf1d225fe777c63. Fixed-container tmpfs had335777792 bytes available before v2; no capacity change or deletion.

Executed fresh actual-restore-v2 once with --cleanup-owned. It failed at actual_restored_low_privilege_checks. Control flow had already completed actual pg_restore and source/target whole-table/schema/sequence equality and semantic comparisons. This partial progress is not full success; successful cleanup was not reached. v1 and v2 objects/archives remain.

Root authorized scoped read-only v2 diagnosis: both fresh nonce roles exist, have LOGIN and no superuser/create-role/create-database/bypass-RLS flags; explicit-role guard diagnostics pass; synthetic valid session count is1. This is diagnostic SET ROLE/explicit-role evidence, not actual-login proof. A later readonly query diagnostic stopped at configuration construction with ValueError before querying as the Console role. No raw row/session hash/credential was output.

Pure local synthetic reproduction (no private file/DB/network) confirms the precise harness defect: replace(ConsoleSettings, database_url=make_conninfo(...)) passes a keyword DSN into Settings' postgres/postgresql-URL validation and raises fixed invalid_database_url. ConsoleAuth already receives the actual low Database separately; keeping the original synthetic unused URL avoids copying real connection credentials into the settings object. No core guard relaxation is proposed. Await root's fix/fresh-run authorization.

## Final authorized v3 execution — PASS

Root authorized the proven configuration fix: ConsoleAuth(low, seed['settings']), removing the unused replace import. The real check_low_roles handler under pure injected adapters reproduced RED1failed/43passed0.13s at invalid_database_url; after the two-line correction GREEN44passed0.08s. The regression verifies the same original synthetic settings object is passed and the actual low Database is injected with the fresh Console role DSN. No guard or business check changed. Final script SHA256 `2dffdb67c51aacb5aca0fa58037eea9c3942952c80c7909e442a7f12834eb296`; final tests `c31690af2b095f81e2fdc958914467f5c1173caafaaa2973396633479457778e`. AST/trailing-whitespace checks pass for both owned files.

Before v3, exact-container tmpfs had306262016 bytes free of536870912; no capacity changes/deletions. Ran once:

`rtk proxy services/platform/.venv/bin/python services/platform/scripts/verify_identity_restore_drill.py --execute-owned-drill --cleanup-owned --output /private/tmp/easel-identity-continuity-20261023/actual-restore-v3`

Exit0, success=true, verification=actual_synthetic_restore, verified_at2026-10-23T15:50:05.414167+00:00. Safe receipt: `/private/tmp/easel-identity-continuity-20261023/actual-restore-v3/restore-safe.json`.

Actual synthetic IdP source/target distinct databases, same schema: pg_dump/pg_restore then the real frozen collector. The collector evidence was consumed by the real owner migration service, mapping1product and2Console identities. Actual central archive restored into a new empty database with the unchanged random schema name. All51table row inventories/data hashes, structural definitions and sequence definition/state/OWNED BY semantic-name hashes match source and target; source recheck around dump and post-low-role target recheck match too. schema_migrations has10rows, normalized candidates3, migrationentries3, proof1, batch1, consolealiases2.

Semantic verification passes: settledjob1(cost2000), pendingjob1 with positive held and no fabricated actual, membership1, financial six-bucket/lot/entry conservation and journal balance, original product user mapping, active admin and disabled viewer states. Actual fresh LOGIN product and Console low-role connections pass their startup guards; restored Console session authenticates to original admin, wallet read returns preserved membership, private token-column SELECT is denied. These are actual v3 checks, distinct from the explicit-role diagnosis on failed v2.

Successful v3 cleanup removed only its three nonce databases, source nonce schema and two fresh roles after closing connections and verifying owners. Container was not stopped. v1/v2 failed stages and their objects remain preserved for diagnosis; no claim they were cleaned. v3 retains dump/privateconnection/ownedmanifest/safereceipt artifacts despite database cleanup. All v3 files are0600 and stage directory0700. Model calls0/payment calls0. No real identity, wallet, prior preview, production database or source001–009 was mutated by this harness. This is synthetic isolated recovery evidence only; production_ready=false.

Final archive SHA256: central `f43a4ee2c5dee357046fc48f3af11608dcfd528ab1830364aa1bd00ebb7d521e` (273463bytes); synthetic IdP `79704cc971c4dcb6ff3527fc20ed9a6219eb73c6b96873704f3fdc3699a3e0b9`. PG slot released to root for the independent fresh full suite including10,000-candidate cases.
