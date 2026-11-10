# Independent Console catalog/query review — 2026-10-23

Final status: **PASS after the reviewed permission and financial fixes. No remaining P1/P2 in this review scope.** This is an independent source review supported by separately attributed test evidence, not an independently rerun PostgreSQL test or deployment attestation. No remote, model, private configuration or database action was performed by this reviewer. Earlier findings below are historical and closed by the final section.

## P2: Web role can read more sensitive business columns than its queries need

`services/platform/src/platform_service/console_permissions.py:12–19` includes entire `internal_grants`, `billing_orders`, `billing_refunds`, `execution_jobs` and `execution_receipts` tables in `SELECT_TABLES`; provisioning grants whole-table SELECT at lines 92–93. The current DTO queries correctly omit raw payment/identity material, but the database boundary still permits checkout contents, payment provider identifiers, execution scope/reason and other unnecessary columns. The design's restricted Console role should deny these columns, rather than relying solely on the current projection code. `identity_sessions` also has more allowed columns than its user/product-count query requires.

Root accepted the finding; exact required columns sent to the core agent:

- `billing_orders`: id, product_id, billing_account_id, channel, mode, amount_minor, currency, status, credit_state, revision, created_at, updated_at, paid_at, expires_at, offer.
- `billing_refunds`: id, order_id, amount_minor, status, created_at, updated_at.
- `execution_jobs`: id, model, product_id, billing_account_id, status, amount, actual, funding_mode, created_at, updated_at, model_definition. The retained model definition is the public catalog definition used to project rate version, not a prompt.
- `execution_receipts`: job_id, input_tokens, output_tokens.
- `internal_grants`: id, lot_id, membership_id.
- `identity_sessions`: user_id, product_id.

Closure requires direct low-role SELECT denial for representative forbidden columns plus successful execution of all real Console query methods with the narrowed grants. DTO-only tests do not establish this boundary.

## Reviewed behavior that is consistent with the specification

`console_catalog.py` reuses one outer database connection through `BoundDatabase`, so existing plan/offer/model methods do not independently commit. Business mutation, operation receipt and audit insert share the transaction. Migration 009 makes both receipts and audit immutable and adds deferred matching-audit checks. The existing database context commits/rolls back at the outer connection boundary. Audit-insert failure rollback and concurrent idempotence are represented in the provided PG tests; those tests were read, not executed in this review.

The operation identity is actor plus hashed key, guarded by a transaction advisory lock. Canonical request hashes include action, target, payload and reason; conflicting reuse returns 409. Current ACL/session validation occurs before replay. Real auth holds shared session/admin advisory locks in write transactions, matching logout/disable exclusive locks. Model state updates lock the row and compare its timestamp before writing. No automatic new-key recovery exists here.

Plans/offers reuse immutable version rules. Protected `easel-internal-` plan IDs cannot be published, sold through a new offer or withdrawn through the Console. There are no issue/refund/settle entrypoints. Model publication and enabling require the exact server registry tuple of provider ID, provider version and model ID, plus matching verification reference/test mode. Browser-supplied claims alone cannot turn a fixture into live. Publication starts disabled through the existing execution service; disabling remains possible without a currently allowed registry binding.

`console_service.py` uses parameterized, bounded, stably ordered queries and a repeatable-read/read-only snapshot. It does not expire lots, settle reservations, dispatch models, query payment providers or read execution-result payloads. Projection code explicitly selects safe metadata; provider responses expose only the public registry mapping. Audit result summaries extract only enabled/name/version rather than dumping stored results. Detail collection truncation is signaled. The audit result and metadata may contain operator-written text, which the frontend renders through React escaping.

## Pending final financial delta

The business agent explicitly reports that new spendable/frozen/unposted-expiry SQL is not yet frozen and awaits the owned PostgreSQL test environment. The current file still aggregates old `available` fields; this is recorded as unfinished work, not a second finding against the final patch. Final review must verify `spendable_available` excludes frozen and expired lots at one transaction timestamp, book `available` remains unchanged, frozen/expired diagnostics may overlap, all money values are decimal strings, and reads do not create expiry journals. Frontend already distinguishes the fields and treats absent values as unknown.

Final PASS is pending the narrowed permission delta and financial query delta with their actual PG evidence.

## Financial delta follow-up

The business agent subsequently reported 34 focused PG tests passing, and the new source was independently read. The three new balance fields now use the intended expiry/frozen predicates and `transaction_timestamp()` in overview, list and detail. They are included in decimal-string projection. Audit summaries expose only typed scalar enabled/name/version. These parts pass source review; the PG run is the implementing agent's reported evidence, not an independently rerun suite here.

One remaining P2 was sent to the business agent: `console_service.py:165–166` still computes `test_available` / `live_available` without the expiry predicate. Because the detail UI labels them 测试可用 / 正式可用, expired unposted amounts appear usable even though `spendable_available` correctly excludes them. Add the same `expires_at > transaction_timestamp()` condition and focused assertions for those fields. No money mutation is requested.

## Final closure

The original permission overreach and later test/live expiry omission are closed. Console now grants safe columns for orders, refunds, jobs, receipts, internal grants, identity sessions and credit reservations. Tests directly deny checkout/provider identifiers, scope/reason, receipt attempt data, grant/session identifiers and reservation idempotency/body/completion reads. The real-auth low-role tests invoke overview, all ten lists, user/wallet/order details and all supported catalog mutations; this checks actual SQL compatibility rather than DTO serialization alone.

`test_available` and `live_available` now exclude both frozen and expired lots using the same transaction timestamp. Test/live-mode simulation cases cover active/frozen/expired lots, assert book 300 versus spendable 100 in overview/list/detail, assert 100/0 splits for list/detail, and verify reads create no journals. The live-mode fixture simulates payment events locally; it does not prove external payments.

Evidence attribution: business agent reports **35 passed in 3.29s**, after observing the split omission fail with 200 versus expected 100. Core reports **78 passed in 9.20s**, including signed authentication, narrow privileges and actual low-role query/catalog cases; targeted changes were **26 failures / 1 pass → 27 passes**. Root subsequently reports the complete backend suite **630 passed in 65.82s**, including synthetic signed OIDC provider → real HTTP BFF/auth/catalog/PG integration. This reviewer inspected final source/tests without connecting to PG or duplicating those runs. These tests do not establish real Logto application deployment or a real administrator login.

Reviewed SHA256:

- console_service.py: `d287d554f63ce011bf9a8ba72722ee812645dfc46c4a7ccbdc9443ff3e64c7c3`
- console_catalog.py: `26c79a9e1c2b73a5676f3c6abb0b2352349f20ac66ef472f4b9544b1339b170e`
- console_permissions.py: `fc1350a63bfbece887fadce3ce97d99509d6bb2f0b18d71ec3b2fb6ee9946a08`
- migration009: `f21d6f80fd590b3aee04f11d5579b27cb98d10ac64945ddfa252b354f5da445c`
