# S5-A3 cutover checklist — independent read-only review

Reviewed: `services/platform/deploy/CUTOVER_20261024.md`, SHA256 `f90d2c17141568456335b46ac9e8c3b08f2b01cc4ecc1a660e5a5a351ad505ab`.

Inputs: `server-state-safe.json`, `server-resource-safe.json`, `real-logto-compatibility-safe.json`, `public-endpoints-safe.json`, and the prior identity/collector/deployment review contracts. This review performed no production operation, network request, private configuration read, PostgreSQL connection or source edit.

**Verdict: PASS as a pending cutover planning/checklist artifact. No P1/P2 finding. This is not approval evidence that the listed production prerequisites or cutover actions have already been completed.**

## Evidence and ordering checks

- **Status honesty:** title and line3 explicitly mark the checklist pending and production writes/cutover unexecuted. New release is distinguished from the random Linux installation rehearsal. Section3 explicitly says the real central/identity restore rehearsal remains outstanding. Section7 retains real signed login, wallet continuity, model/tool usage, restarts, monitoring and capacity as separate unchecked gates.
- **Receipts support the limited source facts:** server resource/state receipts support the target host/architecture/Python, persistent central volume, loopback port bindings, 001–009 migrations, existing API/Console listeners and currently unused 19874/19875. The state snapshot reports seven settled jobs, not an eternal absence of pending activity; section4 requires a fresh cutoff query. NULL activity visibility is correctly called insufficient proof of idleness.
- **DNS/TLS are not overclaimed:** public queries found three A records pointing to the target and no AAAA, while normal local login resolution still reached an older address. Public TLS probes did not complete; server-local SNI probes separately reported hostname verification failure. Neither is translated into successful public TLS or service readiness by the manual. Section6 requires fresh validated TLS and exact Host/discovery checks.
- **Callbacks precede the final identity backup:** section3 adds new exact callbacks to the existing applications while preserving old callbacks and secrets, and includes all database-mutating callback preparation in the final backup. Target Logto/admin/login remain off until static source/target comparison, proof and apply complete. Source IdP writers are explicitly frozen. Section6 correctly allows separately recorded target changes only after the static proof phase.
- **Writer fencing is substantive:** section4 binds old processes using environment/PID/UID/start time/command/CWD/parent/manager, accounts for automatic restart, existing DB sessions, late settlements and background writers. It correctly says NOLOGIN alone does not end existing sessions and admission shutdown alone does not stop writes. Section2 blocks entry into the migration window until concrete, reviewed stopping/recovery/fencing commands exist.
- **Old sessions and pending are preserved deliberately:** section4 requires a reviewed session/handoff/login-transaction policy, rather than treating issuer or epoch changes as a complete logout mechanism. Unknown dispatched costs remain pending/held without zero-cost settlement or release. History and audit are preserved. The checklist never copies local Pi session bindings or bearer credentials to bridge origins.
- **One ledger and one lock protocol:** the source central database remains authoritative; restored central databases are rehearsal copies and cannot become spendable balances implicitly. Existing writer isolation is required before running the new 1024-slot identity lock protocol. There is no rolling mixed-lock deployment implied. Migration010 is additive and proof/apply remain owner-only, with no role resurrection, wallet creation or credit replay.
- **Proxy/container boundaries:** existing OpenResty is retained as the only 80/443 owner. The documented mounts match the safe state receipt. The manual does not assume container loopback reaches host loopback; it requires network-mode and minimal-path verification, container-visible certificate paths and permissions, and a config check before a scoped reload. New API sites and existing renewal/default configuration remain protected.
- **Real-login and rollback evidence:** section7 separates user identity/wallet continuity from administrator identity/ACL continuity and from mere successful login. Section8 prohibits old-ledger snapshot rollback after new facts, pending cleanup, identity deletion or blind re-provisioning. Restarting the old service requires schema/lock compatibility and revalidation of exact process/resource ownership.

## Minor precision suggestion (non-blocking)

At line73, `1panel-openresty-*` is a generic pattern, while the resource receipt identifies the actual container as `1panel-openresty`. Prefer that exact observed name, qualified by mandatory pre-execution revalidation, so a future operator does not turn the prose pattern into a command that misses or ambiguously selects a container. The current document contains no such executable wildcard command, so this is not a P1/P2 issue.

## Remaining execution gates

This manual intentionally leaves concrete reviewed fencing commands, exact persistent target Logto resources, real backup/restore chain, owner/default-ACL/role reconstruction, validated TLS, proxy network topology, signed real user/admin login, post-cutover wallet preservation, and operational capacity unexecuted. Those gaps are explicitly gated rather than misrepresented as completed. The 0.607-second single-source inventory receipt is compatibility evidence only, not two-database restoration, a proof/apply receipt, writer fencing or a load test.

## Final single-line clarification re-review

Read line73 again after root's narrow edit. It now names the actually observed `1panel-openresty` and explicitly requires rechecking the exact container identity before execution. The original non-blocking precision suggestion is closed; the original review above is preserved.

Final file binding: `services/platform/deploy/CUTOVER_20261024.md`, SHA256 **`d77092975ea1719d0871d911bef980f84fb51872c4a5e5432ab5f727f566934e`**, independently recomputed from disk. Final verdict remains **PASS for the pending checklist**, with no remaining P1/P2 finding or open P3 wording suggestion from this review. No tests, network or production operations were repeated.
