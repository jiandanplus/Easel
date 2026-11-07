# S2 independent billing core review — approved after fixes

Reviewed 2026-10-22 in `/Users/jiandan/Code/GitCode/Easel`, current `codex/ui-redesign` working tree. Read-only repository review; only this report and the independent regression file were written under `/private/tmp`. No merchant HTTP, configuration, Logto, preview schema, migration deployment, commit or branch changes.

## Current verdict after independent re-review

**Approved within the reviewed core scope; both P1 findings below are resolved. No remaining actionable P1/P2 finding identified in this re-review.** Core follows the approved S2 architecture: server-frozen offers, Scope authorization, atomic verified payment grant and immutable journal, wallet serialization, outbox fencing, test/live separation and append-only refunds. The prior core review block on preview acceptance is cleared; deployment authorization, backups and other component reviews remain the root task's responsibility.

Re-read the complete fix batch in `billing/service.py`, new refund-ordering tests and both PostgreSQL projection-serialization tests. No migration change was needed for these fixes.

- Repeated failure of an already failed refund is now audited as `refund_failure_replay` without unfreezing or mutating the earlier refund. Unfreezing on a first verified failure additionally requires no unresolved/succeeded refund, no membership revocation and no unresolved order accounting state.
- Delayed payment issuance inserts its lot with `frozen=true` whenever an already recorded refund is pending, manual reconciliation or succeeded. Payment issuance and freeze become visible atomically under the canonical wallet lock. Verified refund success compensates once; verified failure can release only when the other accounting protections permit it.
- New coverage includes newer pending/manual/succeeded refund states, actual reservation denial while frozen, refund pending before payment with both final outcomes, resolutions before payment, late successes/failures and unresolved order accounting.
- The suggested order-list revision/refund mixed projection concern is **not a defect**: `orders()` and `get_order()` acquire the canonical wallet lock through `authorize()`, while payment/refund mutations acquire the same lock. Reviewed the new tests: the reader pauses after the order SELECT but before the refund SELECT; `pg_blocking_pids` confirms the writer is blocked, then assertions verify the old consistent projection followed by the newer consistent projection. Both list/detail variants pass.

Independent post-fix verification, on the same authorized isolated loopback PostgreSQL fixture:

1. Original untouched `/private/tmp/easel_s2_review_regressions.py`: **2 passed in 0.34s**.
2. All three repository core suites (`test_billing.py`, `test_billing_refunds.py`, `test_billing_integrity.py`): **47 passed in 6.35s**, no skips.

These are current independently run results. The fixer's broader 296-test result is not represented as independently rerun here. No merchant calls, preview schema changes, Logto operations or repository writes were performed by this reviewer.

## Original findings and red evidence (resolved)

The following records the original defects and reproductions; its line references describe the pre-fix source.

## P1 — Old failed refund releases funds frozen by a newer refund

Location: `services/platform/src/platform_service/billing/service.py:931-947`, specifically the `refund_failed` branch and unconditional `UPDATE credit_lots SET frozen=false`.

Reproduction on real PostgreSQL:

1. Pay an order and issue 100 credits.
2. Request refund A; apply its verified `refund_failed` event. A becomes failed and funds correctly unfreeze.
3. Request refund B; B is pending and the same lot freezes.
4. Deliver another independently identified verified failure event for A (e.g. a delayed query result or distinct provider delivery).
5. B remains `pending`, but wallet summary becomes `available=100, frozen=0`.

The handler allows failed-to-failed processing and clears the shared lot freeze without checking other unresolved refunds. This admits consumption during B's pending external refund, defeating the unused-full-only guarantee. Preserve freeze while any other pending/manual refund or accounting reason remains, and treat already-terminal failure observations idempotently. Check that the regression also attempts reserve so the enforcement boundary is explicit.

## P1 — Refund-pending observed before payment permits an unfrozen grant

Locations: `services/platform/src/platform_service/billing/service.py:920-928` (`refund_pending` when no lot exists), and `:666-715` (`_grant`, which does not inspect existing pending refunds).

Reproduction on real PostgreSQL:

1. Create an order.
2. Apply an external verified `refund_pending` event before the delayed paid event. A pending refund is stored; no lot yet exists to freeze.
3. Apply the valid paid event for the same order/trade.
4. The new lot has `available=100, frozen=0` despite the persisted pending refund.

This is a permitted out-of-order provider fact sequence and leaves a spend window while funds may be returning to the payer. Preserve the unresolved refund state across payment issuance: either hold issuance for reconciliation or issue into an explicitly frozen/revoked state consistent with the approved accounting model. Add coverage for pending → paid → success/failure, including reservation denial while pending.

## Independent verification

Command (secret DSN content was never printed):

`rtk proxy env EASEL_CENTRAL_TEST_DSN_FILE=/private/tmp/easel-s2-runtime/database.json uv run --project services/platform pytest services/platform/tests/test_billing.py services/platform/tests/test_billing_refunds.py services/platform/tests/test_billing_integrity.py -q`

Result: **37 passed in 4.69s**, no skips. Run outside the sandbox against the explicitly authorized loopback test database; standard fixtures create/drop their own random schemas and the low-privilege test owns its temporary role.

Independent regression file: `/private/tmp/easel_s2_review_regressions.py`.

`rtk proxy env EASEL_CENTRAL_TEST_DSN_FILE=/private/tmp/easel-s2-runtime/database.json uv run --project services/platform pytest /private/tmp/easel_s2_review_regressions.py -q --tb=short`

Result: **2 failed in 0.33s**, both with observed `available='100'`, `frozen='0'` where pending-refund protection requires no spendable credits.

## Scope and limits

Read approved spec and S2 plan, implementation report, complete billing core, additive 005 migration, billing/refund/integrity tests, permissions, relevant credit authorization/membership/expiry/reservation paths, and root payment runtime. Verified scope locks, financial transaction boundaries, immutable history and refund compensation checks in source; baseline tests include real runtime-role full payment/refund and concurrent refund/reserve.

No additional concrete finding is asserted. Full provider cryptography, root config/web/CLI and frontend are outside this prioritized core review; root has separate reviews. The root-reported broader suite is not relabeled as independently rerun here. No library/API documentation lookup was necessary for business-logic code review.
