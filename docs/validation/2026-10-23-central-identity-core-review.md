# S5-A2 Task 1 independent source review

Initial verdict: **Changes requested for one P2 bounded-execution/validation-complexity issue.** No additional blocking owner/proof-trust, canonical-alias privilege escalation, session-revocation or financial mutation defect found in the reviewed source. This is independent read-only review of core-owned code; reviewer owns only a separate recovery harness, not these SQL/service/auth/permission modules. No tests, database/network/Docker operation or source modification occurred in this review. Only this stage report was written. Root reports103focusedPASS; not independently rerun here.

## P2: repeated full-batch deferred row validation and no local timeout

010 attaches check_identity_continuity to every batch, entry and alias INSERT. At deferred commit each invocation counts and loops over all entries, and each inner loop checks membership by scanning proof.candidates. An N-candidate batch therefore schedules roughly O(N) full-batch validations with O(N²) SQL operations and additional JSON-array scanning. The Python service permits MAX_CANDIDATES10000. record_proof/inspect/apply have no explicit LOCAL statement_timeout/lock_timeout, while apply holds the global owner migration advisory lock and per-target identity locks. A legitimate large proof or contention can hold the owner operation and those namespaces for an uncontrolled duration. Small focused fixtures do not establish the allowed upper bound is usable.

This is a static complexity/control-flow finding; no large batch or artificial lock was executed. It does not imply malicious owner protection, which is outside the stated trust model, or that current small preview candidates fail. Request: ensure final complete-batch validation does not repeatedly scan per row, retain database rejection of incomplete/mismatched audit/alias state, and bound all owner operation statement/lock waits. Document or limit accepted batch capacity honestly; no production performance certification is requested. Root assigned the repair to core. Initial report remains Changes requested until the exact repair and regression design are reviewed.

A safe deduplication design must not mark a batch as validated before its deferred final check, then allow later statements in the same transaction to insert invalid entries/aliases that escape revalidation. Nor may a caller forge a session variable or writable marker to skip constraints. Review the actual repair's timing, transaction/savepoint behavior, all mutation trigger paths and final state, not merely the number of validator invocations.

## Owner and evidence trust

MigrationPolicy is explicit, immutable and hashes the exact source/targetissuer/environment/tenant/requiredclients/batchbackup. TargetHTTPS/sourceexplicitliteral-loopback and same-issuer rejection preserve identity boundary; approvedbackup and policyhash cannot be omitted. Evidence requires exact dataclass type, allSHAfields, distinctcontexts, nonemptyboundeduniqueuser/clientIDs, matchingtenant/requiredclients and boundedUTC freshness. record_proof additionally queries central candidates itself and freezes originaluser/wallet/adminstates; subjects must belong to the evidencedtenant inventory. Source owner-wallet ambiguity fails. No caller-supplied user/admin/wallet mapping is accepted.

VerifiedRestoreEvidence is an ordinary Python value, not an unforgeable credential. The stated trust root is the protected ownerCLI+actualcollector and protectedpolicy/source/target/backup configuration. record_proof checks both independent identity:collect capability and current_user=session_user=actualschemaowner. apply/inspect require identity:migrate and the same actualowner. This matches the spec's explicit exclusion of a maliciousschemaowner. Synthetic test evidence values are service-unitfixtures, not actualrestoreproof; Task2 actualcollector and rootCLI must be reviewed separately before claiming their path trusted. No evidence/PII is exposed to ordinaryHTTP/Console list routes.

## Atomic application and immutability

The original code serializes owner apply with one advisory lock, checks environment/key hash/request hash before replay, rejects new-key proofreuse and stale unusedproof, and relocks target identity namespaces before rechecking candidates and target occupation. Same-key replay returns the prior safe receipt without additional write. Product uses the same issuer+newline+subject lock as ordinaryfirstlogin; Console uses lock_console_identity for canonical/alias resolution, application and databaseINSERT triggers. Target occupation, ambiguousidentity and changedsourcecandidate fail the whole transaction.

One transaction inserts batch, exact candidate entries and targetalias/productidentity records. Deferred database consistency requires the complete proofcandidate set and corresponding targetmapping/alias; FK/unique constraints connect alias to its migrationentry and originaladmin. Four new tables denyUPDATE/DELETE/TRUNCATE. Existing001–009 are not altered by010; the newmigration adds canonicalnamespace triggers and owner-only audit/proof objects. The complexity issue above concerns repeated complete checks, not a known bypass of the initial integrity rules.

## Console identity concurrency and ordinary privileges

Canonical and alias paths share an exact identity namespace lock. Resolver UNION ALL rejects more thanone match; no canonical-first/alias-first privilege preference. Ordinaryapprove on an alias returns the originaladmin only if role and active match, and cannot create anotheradmin, change role or reactivate disabledACL. Alias has no independentrole/active/epoch. Existing sessions still resolve adminID/role/active/epoch live, so disabling/revoking originalACL controls all identities.

Function is SECURITY INVOKER and adds no owner authority. Console has only the three alias SELECT columns needed for exact resolution; migrationentry link and proof/audit fields remain unavailable. It has no new-table write. Product gains no Consolealias or proof/audit access. Both guards inspect actual/inherited/NOINHERIT-reachable roles and column privileges; productexplicit continuitytable check closes accidentalSELECT/INSERT/UPDATE/REFERENCES. Granthelpers do not addowner capabilities or broadsequence rights. Actual low-role signedaliaslogin is covered by the authored regression source.

## Old sessions and financial preservation

apply only appends external_identity targetrow, migrationaudit and consolealias. It does not write existinguser/tenant/wallet/membership/lots/journal/entries/reservations/jobs/session/role/active/epoch. Sourcecanonicalidentity and foreignkeys remain. Oldaccess/session semantics intentionally stay unchanged; this mapping operation is not drain/fencing or cutover/global logout. Alias points at existingadmin so subsequentactive/epoch changes propagate rather than being copied. Tests inspect full existingtables, nonemptysettled2000+pendingheld anddisabledACL, then preserve oldproduct/Consoleauthentication. No model or payment request is part of this service.

## Test source inspected and limits

Authored tests cover policy/evidencebounds, capability/ownerdenial, candidatefreeze, wrongsubject/tenant/client/backup/context, staleevidence/proof, ambiguouswallet, exactID reuse through signedsyntheticnewissuerlogin, sameemaildifferent-subject nonmerge, ordinaryapprovealias withoutupgrade, target/sourceconflicts, samekeyconcurrency, audit/aliasimmutability, actualwebrole denial, unexpectedcolumn grants, canonicalINSERT-vs-aliasapply race and failure-after-first-mapping rollback. These are meaningful coverage. They are not actualsource/targetLogtoinventorycomparison, pg_dump/pg_restore or productionlogin evidence; those belong to Task2/harness/realcutover respectively. Concurrencytest coverage is representative, not exhaustive scheduler or large-scaleperformance proof.

## Initial reviewed bytes

- services/platform/src/platform_service/migrations/010_identity_continuity.sql: `bed746b6f808aea067c72e9d2d1e12de828ac0777dc4998ede5a7bb1a89aaa74`
- services/platform/src/platform_service/identity_migration.py: `82e1c666a4b755654c261a1bf0bf8de8d744e068d16f04b95876164f5feff3de`
- services/platform/src/platform_service/console_auth.py: `0f94709974f59fa45b4fc48eb80f2f35a71fcdce97d261182e903dccbbce95d0`
- services/platform/src/platform_service/console_permissions.py: `b2f69eb945e2ff18ea9027aa1a24bee922c9cf2751c973b912623a75f3ced5ac`
- services/platform/src/platform_service/permissions.py: `48082028f57008f9258e94a0781149c1a24774705437e08d6cfefc42cf166f63`
- services/platform/tests/test_identity_migration.py: `cebf02ed0c8b7806199e52a6789b8ef63a5fdb722222aeaab2dfb4b38a514aef`

## Final repair review — frozen source

**Final independent source verdict: PASS for the reviewed Task1 scope; original P2 closed.** No remaining important source/specification mismatch found in the final delta. This closes the source review, not production deployment, real-Logto recovery or a fresh full-suite result. This reviewer did not run the core tests. The separately owned synthetic restore harness has not yet executed at this review checkpoint.

The repair adds a fifth owner-only immutable table, identity_restore_candidates. Proof insertion synchronously materializes its exact JSON members in a single AFTER INSERT INSERT SELECT. Normalized inserts require trigger depth two plus a proof created in the current transaction; ordinary direct inserts, including after SET CONSTRAINTS ALL IMMEDIATE, fail. The normalizer is SECURITY INVOKER, has no writable TEMP/GUC bypass marker, and invalid/duplicate normalized members fail atomically. All five tables remain immutable. This is correct within the stated non-malicious schema-owner trust boundary; an owner who can replace triggers is not a protected adversary.

Each entry now checks indexed exact proof membership and current-transaction batch ownership. Each alias checks its exact console entry, subject, original admin and proof target issuer, also in the batch transaction. The deferred complete-batch validator runs once per batch, compares normalized proof members with entries through a FULL JOIN and checks all target mappings through set joins. Early IMMEDIATE cannot create an empty successful batch because count/set checks fail; after a complete successful check an added entry must either duplicate an existing unique member or fail membership. An extra alias must have the same exact linked entry/subject/admin/issuer and conflicts with unique entry/identity constraints. Late committed-batch appends fail created_xid. The repair therefore preserves final completeness while removing the original repeated full-batch row-trigger scans.

record_proof, inspect and apply now share a transaction context with LOCAL 30-second statement timeout, 2-second lock timeout and 180-second total async deadline. Timeout, lock wait and statement cancellation become a fixed safe 503 and roll back. These limits bound this owner operation; they are not production throughput certification. Candidate collection uses two set queries: product identities joined to aggregate owner wallets and console admins. Missing or multiple owner-wallet matches still fail; total candidates remain capped at 10,000. Conflict checks and application remain linear sequences of parameterized exact lookups/inserts within the overall deadline.

The actual 10,000-console fixture exposed shared-lock memory exhaustion before the final repair. Final lock functions instead use 1,024 deterministic issuer+subject stripes per schema and identity kind. Ordinary product first-login now calls lock_product_identity; Console login/approval/canonical and alias triggers call lock_console_identity. Migration obtains distinct target stripes ordered by kind and stripe before conflict checks. Equal identities share a lock; hash collisions serialize unrelated identities without merging their exact row keys. Bounded lock count addresses the observed memory failure. The old exact-hash product protocol and new stripe protocol must not coexist among writers during cutover; stopping/draining old writers remains a runbook prerequisite. This review found no reverse multi-stripe acquisition path in ordinary first login.

Regression source covers one deferred validator invocation, normalized-member direct append denial, early-IMMEDIATE proof and batch cases, committed-batch append denial, timeout values/rollback/local-setting scope, genuine statement and lock timeout, bounded candidate queries and 10,000-product/console cases. Existing low-role guards explicitly deny normalized candidates/proofs/audits and retain only necessary Console alias SELECT columns. Product and Console runtime do not gain owner migration authority.

### Reported execution evidence and unresolved test boundary

Core reports the final reduced related selection passed **164 tests, 2 scale cases deselected, 23.80 seconds**. The two scale cases on final source were separately reported passing; the stripe-path three-case run took 30.61 seconds. Earlier pre-stripe Console scale OOM is retained in the implementation report, not hidden as a successful first attempt.

Core also reports an initial broader 166-case run had **161 passed, 5 failed in 40.43 seconds**: four product one-second expiration boundaries and one Console integer host/DB clock boundary did not raise as expected. The reason is **unproven**. No fixture/TTL change was made; a single later six-case diagnostic passed and later measured clock offset does not establish the cause at failure time. This review does not call those failures repaired or certify the full suite green. Root will run the fresh complete suite, including both scale cases, separately. Those observed boundary failures remain a validation limitation pending that result, not a newly established core identity-migration defect.

### Final reviewed bytes

- services/platform/src/platform_service/identity_migration.py: `c08aadea54953216dabd2670d56932597ea832e0d5850ac8f4e1814fed772b00`
- services/platform/src/platform_service/migrations/010_identity_continuity.sql: `b71dcfa0861e4b8324bcf9ece66119efdb695d70d9094e590c2f6312e95da6e3`
- services/platform/src/platform_service/identity/__init__.py: `e9c5b2e551d034cd91b717b88874c0b736cefbe625413d9b7291fc28a2fd00d1`
- services/platform/src/platform_service/console_auth.py: `0f94709974f59fa45b4fc48eb80f2f35a71fcdce97d261182e903dccbbce95d0`
- services/platform/src/platform_service/permissions.py: `96f586c316c016d1652d7095be54c397d93fd9ebdfa6841b1fd9d3103ad16ac6`
- services/platform/src/platform_service/console_permissions.py: `b2f69eb945e2ff18ea9027aa1a24bee922c9cf2751c973b912623a75f3ced5ac`
- services/platform/tests/test_identity_migration.py: `30644ad4b690d124d05a1cb73984ea7273d5f584790df944edc0e00633d9671d`
