# S2 Easel checkout / native bridge independent review — post-fix

Date: 2026-10-22. This post-fix section supersedes the initial NEEDS CHANGES verdict retained below for traceability. Review remained read-only for implementation, merchant connections, preview/configuration, Logto and user state.

## Current verdicts

- **Spec compliance: PASS for the reviewed Task 4 UI/native scope.** Both original P2 findings are resolved in the current source. No remaining actionable P1/P2 found.
- **Code quality: PASS for the reviewed change.** Financial ordering is explicit and projection-wide, callbacks deduplicate observed financial transitions, and long QR payloads remain bounded.

This is code/contract and deterministic-test acceptance, not browser/visual, live merchant, native-runtime or production acceptance.

## Resolution evidence

1. **Original P2 financial ordering — RESOLVED.** `CentralBillingSettings.tsx:24-42` parses canonical decimal revisions with BigInt, accepts only a strictly newer complete snapshot when both revisions are present, and rejects stale/equal complete snapshots. It no longer merges refund fields from different authoritative versions. A newer refunded→reconciliation_required/manual_review fact is accepted; an older paid/granted or older pending/failed/manual refund projection cannot overwrite the accepted state. The fallback for missing revisions is conservative and permits newly observed review after refund; it is not relied upon as a total ordering guarantee. `api.ts:110-112` exposes the required revision string. I also inspected core public string serialization and order/refund triggers that advance the revision; runtime/PG execution of that core change remains root-owned.

2. **Original P2 stale wallet/entitlements — RESOLVED.** `CentralBillingSettings.tsx:44-48,88-96` compares accepted status/credit/refund identity/refund status and invokes `onFinancialChange` only for a newly observed financial change. Grant, refund freeze, failed refund/unfreeze, retry with a different refund id, success/revocation and manual review are covered. Stale/equal snapshots, unchanged polls/lists and revision-only changes do not notify. `CentralAccountSettings.tsx:179` wires the callback to the existing revision refresh that fetches wallet/entitlements. Callback refs are not request-effect dependencies, so normal parent rerenders do not restart child reads. The keyed-session lifetime and abort checks still prevent old-account callbacks.

3. **Long QR compatibility — ACCEPTED.** `CentralBillingSettings.tsx:53` raises the data URL limit to 1,000,000 while retaining the SVG/base64 image allowlist. The rendering regression confirms a 450,000-character Base64 payload is passed to an img and over-limit payloads are hidden; external URLs are still rejected. The test exercises URL length/rendering, not QR scan validity. Root owns worst-case actual encoder and browser/merchant validation.

## Independent current-run verification

Command: `rtk proxy node --test tests/frontend/centralBilling.test.mjs`

**24 passed / 0 failed / 0 skipped**, executed independently during this post-fix review. This includes both original findings, BigInt revision precision, stale/equal/refund-state ordering, financial callback deduplication, parent wallet/entitlement reload with no loop, old-account cancellation, bounded polling, disabled/live gates, exact API body, and long/external QR constraints.

The implementation report records 183/183 full frontend tests and successful build/lint; those complete-suite/build/lint results are author evidence, not re-executed by this reviewer. No source change occurred during review, so no repeated full suite was necessary. Native boundaries remain as inspected in the initial review; the fix scope only changes frontend files/tests.

---

# Historical initial review (superseded)

# S2 Easel checkout / native bridge independent review

Date: 2026-10-22. Read-only review of Task 4 plus shared/global contracts and integrated current files. No implementation edit, commit, merge, configuration/session/Logto change, or merchant connection.

## Verdicts

- **Spec compliance: NEEDS CHANGES.** Purchase authority, canonical money, modes, exact bridge routes and identity cancellation conform on inspection; financial state merging can hide a genuine reconciliation change, and accepted refund facts do not refresh wallet/entitlements.
- **Code quality: NEEDS CHANGES.** Two actionable P2 findings below. No demonstrated P1 in this scope. The existing reported 172 frontend / 43 native passes were not rerun or independently re-certified. Three targeted probes reproduced the findings using the existing deterministic React-hook harness against the actual component source.

## Findings

### P2 — Preserve financial state ordering without hiding later reconciliation

Location: `web/frontend/src/components/CentralBillingSettings.tsx:26-29` (mergeOrder).

The unconditional refunded guard returns the old order for **every** later non-refunded state. However billing core explicitly permits a later `reconciliation_required` fact after another provider transaction or other anomaly (`services/platform/src/platform_service/billing/service.py:600-610`, `_mark_review` at 650-663). Once a user has viewed a refunded order, pressing query after that genuine server change continues to display “已退款” and hides the manual-accounting status until component remount. Conversely, reconciliation is only protected against creating/pending/closed; a stale paid+granted response from an overlapping list/query can replace `reconciliation_required`+`manual_review` and display “已确认到账” again. Current reads and mutations can overlap, so this requires no invalid server payload.

Fix with an explicit allowed-transition/ordering policy (ideally a server revision) that accepts refunded→reconciliation_required and rejects stale paid over reconciliation_required. Protect refund substates as appropriate instead of treating refunded as absolutely final. Add both transition/race regressions.

Evidence: targeted probe 1 starts with a real-shaped refunded order and returns reconciliation_required from refresh; displayed text remains refunded with no 待人工核算. Probe 3 starts manual_review and accepts the older paid/granted projection; displayed text becomes 已确认到账 and grant callback fires. These probes demonstrate the merge behavior; probe 3 supplies a stale-shaped response directly rather than reproducing transport latency.

### P2 — Refresh wallet and entitlements when an observed refund changes balances

Location: `web/frontend/src/components/CentralBillingSettings.tsx:77-79`; integration `web/frontend/src/components/CentralAccountSettings.tsx:179`.

Only the first paid+granted observation invokes the parent refresh. If a user has already viewed a paid order, an operator can request/finish its refund while this screen remains open; clicking 查询支付状态 or 刷新目录和订单 accepts the new pending refund / refunded / manual_review order but never refreshes the parent wallet or entitlements. The screen can therefore show “已退款” next to still-available points and “权益生效中”, or say points are frozen while the wallet retains the old spendable amount. The backend actually freezes lots at refund request and revokes memberships/reclaims credits at success (`billing/service.py:755-769`, 949-979). Only the separate top-level 刷新账号状态 action repairs those stale values, and the order UI does not direct the user to do so.

Use a deduplicated financial-state-change callback (grant, refund/freeze/refund failure, revocation/manual review) to refresh wallet/entitlements whenever an accepted projection indicates those state changes. Do not refresh on every unchanged poll.

Evidence: targeted probe 2 loads paid+granted (callback count 1), refreshes to refunded+revoked, confirms 已退款 renders, and confirms callback count remains 1.

## Verified by inspection

- Order creation sends only offer_id/channel/idempotency_key; no client-side price, wallet, credit grant, confirmation or refund route.
- Native allowlist requires exact 32-alphanumeric order IDs and allowed verbs; create fields/channels/identifiers constrained. Existing S3 deny remains. Private bearer stays native; response filtering blocks token/key/signature fields and known sensitive values. No v1/custom implementation edited by this review.
- Native router requires loopback peer and billing writes compare Origin; task guard retained. Secret response/error mapping uses fixed messages.
- CNY and credit rendering uses canonical decimal string checks and BigInt; purchase requires positive CNY amount, correct mode/test_only relation and supported refund terms.
- Failed same-offer/channel creates retain their idempotency key; success clears it. Different offer/channel requests use distinct map entries with crypto.randomUUID. No additional intent-key finding was established.
- Keyed billing sessions include URL/auth/scope/session/epoch; request cleanup aborts old identity, and delayed old actions check mounted activity. Polling is serialized and bounded; paid+granted blocks old pending/closed.
- QR uses bounded SVG data-image URL in img, no arbitrary URL or innerHTML; expiry has a separate timer. HTTP SVG generation is root-owned and outside this review's implementation responsibility.
- disabled/test/live labels and test-only purchase labels are distinct. No live collection, QR merchant acceptance, browser visual acceptance, or real account switching was performed here.

## Reproduction artifact

`/private/tmp/easel-s2-ui-review-probes.mjs`

Command: `rtk proxy node --test /private/tmp/easel-s2-ui-review-probes.mjs`

Result: 3 passed / 0 failed. They assert the observed buggy behavior to establish reproducibility, so “passed” is not a claim that the implementation satisfies the desired behavior. Only tmp files were written.
