# B3 full target-pair verification deduplication plan

> **For agentic workers:** Apply subagent-driven-development, independent source review and Root fresh verification; no Git writes.

**Goal:** Preserve complete original-target/peer verification at every existing ATTEST boundary while removing duplicate full pair passes that exhaust main60.

**Architecture:** Exact TargetHandle.verify calls original _Attempt._verify then original RootTargetFactory._pair. With both original targets READY, one selected handle validates its own inspect/tool/PID/wrong-password/metadata and the peer's entire equivalent checks, followed by verify_distinct_pair. RootExecutionOwner currently loops over both original handles at each of three boundaries, duplicating this full paired check. Replace each loop with one verify on the retained original selected target handle. Existing owner guards continue checking exact original handle/attempt/registry/context/FD identity for both targets. No caching and no skipped underlying target or peer checks.

**Tech stack:** Existing Python stdlib/source-only fake-transport unittest fixtures.

## Evidence and scope

Root current cost sample uses the exact41,043,648-byte public Docker CLI inside existing synthetic prerequisite/factory fixtures, with actual subprocess/network forbidden. Fresh fake pair creation59.763s; current two-handle verify loop42.327s validates metadata twice per target; single original handle21.031s validates metadata once per target. Saved ROOT-public-cli-pair-cost-once002.safe.json/jsonl in b3-policy-validation-dedup-20261105, originalsession97606 terminal34029d0. No actual target/60s qualification inferred. Initial fixed120s diagnostic failed without an original cause observation; kept FAILED, not rewritten.

## Global constraints

- Current branch codex/ui-redesign, no commits/push/merge, no UI edits; preserve subscription/custom modes.
- Root alone performs actual Docker/native/proc/network/DB/model/credential/artifact work. Author/reviewer only repo source and existing pure fake transport fixtures, every shell rtk/rtk proxy.
- Preserve original job900 and permanent main60 through final durable commit, no renewal/guard removal/cache/timeout increase.
- No changes to target_factory.py/lifecycle.py/TargetHandle API, _pair implementation, PG image/tools/commands/eight READY records, worker/observer/wire contract, dispatch protocol, native table or journal schema.
- Both original targets must still be READY/owned; _guard_live still checks both exact original handles, attempts, source/context/input/policy/cancellation/job/FD bindings before native/dispatch/commit.
- One original selected handle's verify must actually execute full peer checks; do not call only _metadata or accept receipts, discover/adopt a handle, suppress a rejection or use a fake proof.
- Prior core source acceptance c9ebdfac...118 tests and operator3047 pin-binding acceptance remain scoped history; previous FAILED once010 remains unchanged. Actual target/owner/SQL/fullB3 readiness remains unproven.

## Task 1: Minimal owner integration and behavioral RED/GREEN

Modify only services/platform/src/platform_service/media_backup_b3/execution.py and tests/test_media_backup_b3_execution.py; report/diff under b3-paired-verification-dedup-20261105. Before bytes and hashes are frozen.

- [ ] Inspect real TargetHandle.verify/_Attempt._verify/_pair and owner _begin_attestation/_check_original_bindings. Confirm one selected original verification checks both original peers in READY pair, all metadata/auth/tool/PID/inspect and distinctness, including selected identity case.
- [ ] RED with real existing factory and fake private transport: successfully attest central and identity on separate fresh owners, bound metadata output counters before attest. Each of the three existing verification boundaries must perform one complete pair pass, so each target metadata count increases3 rather than6. Do not mock verify/_pair or bypass validators.
- [ ] Add focused peer-drift rejection coverage using the real original factory path/fake response: at least nonselected peer metadata drift must block request dispatch/COMMITTED. Cover selected central and identity and retain UNKNOWN/no-replay semantics. Avoid redundant broad matrix or weakening old tests. Existing pre/post original binding/clock cases remain.
- [ ] GREEN production change only three loops to a single a.target_slot.original_handle.verify() each, with a concise comment explaining factory's complete peer verification. Prefer inline minimal calls, no new abstraction/API.
- [ ] Run the full original execution-owner pure module once. Document RED count failures, existing and new cases, exact exit/time/SHA and full two-file diff; freeze and stop.
- [ ] Independent spec/quality review of complete before/after plus real factory relationship. Root fresh full owner and existing caller/supervisor composition regression. Do not rerun unchanged69 lifecycle/factory after no changes to them.

## Task 2: Root actual delivery closure

Root rechecks public-CLI cost as needed, merges exact source acceptance chain, rebuilds/freeze current worker package/image and literal bindings, then a new original input/job/target pair. Original windows and full proof/source-preservation/terminal gates must pass. If cost remains high, preserve evidence and continue diagnosis; no replay/renewal/claim substitution. Full original Tasks4–9/bootstrap/SQL/content/control/application/finance/media/matrix/AV/macOS scope remains pending after qualification.
