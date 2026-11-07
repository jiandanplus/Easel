# B3 policy validation duplication implementation plan

> **For agentic workers:** Apply subagent-driven-development task by task; Root alone performs actual Docker/native/source-authority work. No Git writes.

**Goal:** Reduce verified duplicate whole-policy validation cost while preserving every existing validation, native command, identity/FD/capacity guard and original clock.

**Architecture:** RootFactoryPolicy._projection already calls its complete __post_init__. _PrivateJobFiles._guard and RootTargetFactory._check_policy currently call both explicitly, so one logical guard validates the same immutable structure twice. Remove only those two explicit redundant calls; keep _projection validation and every subsequent check unchanged. Do not cache policy/validation/hash results across guards.

**Tech stack:** Existing Python stdlib and pure unittest fixtures; no dependencies or API changes.

## Global constraints

- Current branch codex/ui-redesign; no commit/push/merge or UI edits. Preserve subscription/custom modes.
- No actual native/Docker/proc/network/DB/model/provider/credential/archive/wheel/runtime work by source implementer or reviewer. Pure existing fake-transport fixtures only.
- No changes to _projection, _read, _guard_time, wall/monotonic clocks, original main60/job900, parser/worker/observer protocol, PG commands/image/tools/8 records, RootTargetFactory public API, file/hash checks, resource limits or ownership model.
- Each guard still performs full typed policy validation on every call via existing _projection; runtime-tool validation remains. No digest/validation caching or deferred checking.
- Existing failed once010, original terminal137, RETAINED history and current cold/component proofs stay unchanged. This source optimization alone does not establish actual target/owner/SQL/full B3 or desktop readiness.

## Task 1: Minimal deduplication and cost/invalid-shape regression

Files: modify only target_lifecycle.py, target_factory.py and their two corresponding test modules. Write report and exact unified delta under outputs/_run/b3-policy-validation-dedup-20261105. Before bytes and four-file SHA inventory are frozen there.

- [ ] RED: Add one meaningful regression per existing fixture. Instrument the real RootFactoryPolicy.__post_init__ (delegate to original, not fake success), count only the exact policy object. Two consecutive ordinary guard checks must perform two whole-policy validations total rather than four. This deterministically bounds validation amplification instead of flaky wall time.
- [ ] In the same coverage, after valid guards change _specs from tuple to same-content list: its canonical JSON projection would collide, but real typed validation must still reject before native entry. Restore original tuple in finally for ordinary fixture cleanup. This excludes cached-validation or digest-only shortcuts.
- [ ] Demonstrate exact new tests fail on duplicate validation count before production edit, not import/fixture errors.
- [ ] GREEN: _PrivateJobFiles._guard removes only explicit p.__post_init__ before digest(p._projection()); RootTargetFactory._check_policy removes only p.__post_init__ while keeping t.__post_init__. No unrelated refactor.
- [ ] Run both full focused pure lifecycle/factory unittest modules once. Record exact tests, wall times, exit codes, hashes and test/source diff; freeze and stop editing.
- [ ] Independent source/spec/quality review; Root reads full report/diff and runs fresh appropriate regressions, including execution owner because it consumes the original factory policy.

## Task 2: Root cost and actual qualification gate

- [ ] Repeat the existing exact public-CLI synthetic prerequisite profile once on changed source; compare validator calls and elapsed cost with original 1274 guards/2548 policy validations/0.603s profile. This is cost evidence only.
- [ ] Rebuild/freeze the actual host/worker artifact closure as required by changed source. Independent review before any new native original task.
- [ ] Use a fresh original input/job/target pair; never reuse once010 handles, claims or clocks. Verify original main60 through commit and original job900, both target bindings, source preservation and terminal disposition.
- [ ] If still insufficient, retain the new failure and profile remaining cost. No deadline expansion, removed checks, hidden retries or narrower success claim.

Full Tasks4–9, SQL/bootstrap/content/control/application/finance/media/matrix/AV/macOS remain in original scope and follow qualification; this plan does not replace them.

## Task2 concrete Root operator pin binding

After source acceptance c9ebdfac..., Root found the existing fixed APPROVED_API map still bound the two prior production hashes. Root97f324 preserved exact before bytes, demonstrated real dependency_hashes rejects only the outdated fixed pins, then replaced exactly those two literal hash values. All seven fixed API pins remain mandatory, the other five remain unchanged, and actual Root rehash of453 repository plus3777 public runtime files passes; no source authority or native task is used. This mechanical operator binding is additional to the four-file source patch, with independent review and caller/supervisor pure regression before actual launch. It does not alter command/state/clock/authority behavior or broaden the map. The caller is not part of the worker wheel source set.
