# S2 final integrated independent review

Date: 2026-10-22. Repository `/Users/jiandan/Code/GitCode/Easel`, branch verified as `codex/ui-redesign`.

## Verdict

**Spec compliance: PASS within the approved local S2 scope. Code quality: PASS. No remaining actionable P1/P2 finding identified in this final integrated review.**

The earlier independently reproduced provider, financial ordering, wallet refresh, refund ordering and order-list QR findings are closed by their focused post-fix reviews and are consistent with the current integrated source. This verdict covers local implementation and the supplied validation evidence. It does not certify real merchant collection/refunds, production operation, capacity, or S3 execution.

## Scope and evidence

Read the approved S2 plan and its shared/global contracts, all four final independent reports (`easel-s2-providers-review.md`, `easel-s2-core-review.md`, `easel-s2-ui-review.md`, `easel-s2-runtime-review.md`), the completed validation document, and current integration source for billing core/public projections, provider contracts, payment web/runtime/configuration/operator CLI, native route boundaries and the React billing/account integration. There is no root `.codegraph` directory, so direct source inspection was used. The pre-existing S1/media/theme changes and all 154 pre-existing dirty files were not treated as this review's change scope.

This final review independently checked source and ran one pure in-process amount-boundary probe. It did not rerun already independently completed suites, touch merchant credentials, use payment production domains, change preview/database/Logto/user state, edit repository implementation, switch branches, or commit. The only new artifact is this private report.

Evidence from the four independent reports remains attributed to those reviewers: provider 80 passed; core 47 passed plus 2 independent original reproductions now passing; final runtime 42 passed (39 repository checks plus 3 private checks); final checkout UI 30 passed. Root validation reports the complete central suite 297 passed, native 133, media 206 and frontend 189, with successful build/lint and wheel packaging. These are overlapping groups and are not added together or represented as rerun by this reviewer.

## Cross-component conclusions

- **List/detail QR contract:** history calls `decorate_order(..., include_qr=False)` and retains checkout target/expiry with a null image; detail derives the bounded image locally. The same-revision UI path only enriches a missing image when target and expiry exactly match and the bounded data-image validator succeeds. It preserves the earlier complete financial projection even if incoming fields contradict it. Lower revisions cannot replace the projection; a metadata-only refresh cannot erase an already loaded image. Financial field changes use newer revisions, backed by order/refund database triggers and wallet-serialized list/detail projection reads. No trust or ordering regression found.
- **Wallet and entitlement refresh:** the accepted projection's financial fingerprint covers status, credit state and refund identity/status. New grant, freeze, failure/release, success/revocation and manual reconciliation observations notify the parent; unchanged/equal/stale observations and image enrichment do not. The parent revision cancels old reads and reloads wallet and entitlements. Identity-keyed billing sessions and aborted requests preserve account/scope boundaries.
- **Authority and recovery:** create accepts only offer/channel/idempotency key and core freezes the operator-published offer. Refresh/close enqueue provider work; neither treats client intent as payment proof. Notify verifies raw bytes before calling core; core rechecks frozen order channel/mode/merchant/app/amount/currency and updates financial state transactionally. Worker provider I/O is outside SQL transactions; lease fencing and error recovery remain intact. No public payment-confirmation, grant, refund or simulated-success API was found.
- **Money, modes and privacy:** public amounts/revisions are strings, provider conversion uses integer/Decimal logic and UI rendering uses BigInt. Disabled/test/live gates are coherent; simulated operator events require test mode and a matching stored test order identity. Native allowlists remain exact, bearer stays native and existing sensitive-value filtering applies. Local operator CLI authorization rests on protected server/database access rather than treating the audit label as a public credential. S3 remains disabled.
- **Refund invariants:** current core matches the independent post-fix findings: an old failed refund replay does not release a newer freeze; delayed issuance inherits pending/manual/succeeded refund protection. Original issuance is retained and success appends compensation/revocation, with wallet locking shared with reservations. No additional cross-component contradiction found.
- **Evidence boundary:** the validation document explicitly identifies simulated loopback HTTP/browser results and excludes real merchant acceptance. Its wallet sequence preserves the original 100 test points, issues two distinct simulated payments once each, and refunds one payment once, yielding issued 300 / available 200 / refunded 100. These preview observations are root evidence, not newly executed by this reviewer.

## Nonblocking contract-hardening note

The maximum accepted offer price is wider in billing core than the runtime/provider contract: `decimal_amount` accepts positive bigint values, runtime parsing permits 12 digits, and provider `minor` caps at 99,999,999,999 cents. A pure in-process probe confirmed core accepts `100000000000` cents and `payment_order` rejects it with `payment_invalid_amount`. Thus an operator-published offer priced at **CNY 1,000,000,000.00 or more** could be displayed and create an order that cannot produce checkout, retaining stable error/retry behavior.

This is a real upper-bound inconsistency, but it requires a trusted operator to publish an implausibly large subscription price. It provides no client-controlled price override, precision loss, incorrect charge, unauthorized credit or private-data exposure. Under the requested concrete P1/P2 review threshold, it is **not a blocker**. A future small hardening change can share one monetary maximum and reject such a definition at publication, with a boundary test; no additional broad suite or production experiment is required to establish this observation.

## Closeout

No unresolved P1/P2 finding, no required further test request, and no implementation change requested by this final review. Preserve the distinction between the verified local S2 implementation and future real merchant/production/S3 acceptance.
