# Current disposition: PASS for S3-A source and controlled fixture scope

The final closure at the end supersedes the earlier findings and intermediate holds. No live supplier, merchant, media/cloud, production, commit or merge is implied. This file preserves the independent review history and closure evidence.

# S3-A whole-delta independent final review

Review disposition: **CHANGES REQUIRED — one newly confirmed P1 test/live isolation defect.** This is the current source-review verdict, before the pending mode-isolation, Node launcher and settings-copy corrections. It is not a merge/release approval. The current branch was verified as `codex/ui-redesign`; no source edits or commits were made by this reviewer.

## Scope and method

Reviewed `/private/tmp/easel-s3-final-brief.md`, the approved S3-A plan and shared interfaces, exact pre-turn delta `/private/tmp/easel-s3-all-review.diff` (56 files), current relevant source, task review reports and correction receipts. Preexisting S1/S2 dirty changes are outside this review except where needed to trace identity/ledger/session behavior. Checked central core and migration/permissions, gateway/config/protocol/runtime, operator commands, private CentralClient, native adapter/proxy/environment/RPC/session bindings, supported media guards, model/backend settings and account execution display.

No broad passed suite was repeated. The only new executable review check was a focused mode-transition proof using an isolated PostgreSQL fixture and HTTPX MockTransport. It uses fake provider secrets and the reserved `provider.invalid` domain, intercepted in memory; no real provider request, preview mutation or credential disclosure occurred.

## P1 — Reject an existing task bearer when its funding mode differs from the current runtime mode

Primary changed source: `/Users/jiandan/Code/GitCode/Easel/services/platform/src/platform_service/execution_runtime.py:123` (authentication immediately followed by validation/admission at lines 124–131 without checking `context['funding_mode']` against `self.settings.mode`). Supporting integration: `/Users/jiandan/Code/GitCode/Easel/services/platform/src/platform_service/execution/core.py:370` evaluates `_definition` against the invocation's frozen mode while using the newly configured runtime's bindings; `_definition` checks provider ID/version but has no current-runtime-mode information.

Trigger: mint a task token while execution is in test mode; restart/reconfigure execution to live mode with the same public model ID and provider ID/version mapped to a live HTTPS endpoint; present the still-valid old token within its five-minute TTL. Configuration permits this mapping. The immutable model remains a test definition, so the live catalog is empty and live readiness correctly reports false. Nevertheless, `/v2/responses` authenticates the old test invocation, admits a new operation using the live binding, dispatches to the live endpoint, and settles test credits. The reverse transition can similarly send a live-funded invocation to a test provider. This breaks the explicit invariant that test/live catalogs and credits never cross and bypasses the live verified-definition readiness boundary.

Confirmed proof: `/private/tmp/test_s3_final_mode_transition.py`. It creates the ordinary isolated execution fixture, mints through a test ExecutionRuntime, then invokes the same real ExecutionService through a live ExecutionRuntime with MockTransport. Assertions confirm:

- Live catalog returns no models and `subscription_execution_enabled` is false.
- The old test bearer still returns HTTP 200 from `live.respond(...)`.
- Exactly one mock provider request targets `https://provider.invalid/responses`.
- The resulting operation is settled with `funding_mode='test'`, and the real test ledger records 700 microcredits spent.

Exact review command (outside default sandbox; temporary fixture schema cleans itself up):

```sh
rtk proxy env PYTHONPATH=/private/tmp:services/platform/tests:services/platform/src EASEL_CENTRAL_TEST_DSN_FILE=/private/tmp/easel-s3-runtime/database.json services/platform/.venv/bin/python -m pytest -p s3_review_dbfixtures /private/tmp/test_s3_final_mode_transition.py -q --tb=short
```

Result: **1 passed in 0.29s**. This proof currently asserts that the defect exists; passing is evidence of the defect, not acceptance.

Required correction: after task authentication and before new admission/provider I/O, reject a task context whose funding mode differs from the current ExecutionSettings mode. Test both test→live and live→test, asserting no new reservation, job or provider send. Keep internal `finish` based on the frozen dispatched job so already incurred usage can still settle after a runtime mode change or authorization revocation. Native renewal already validates funding-mode continuity and should retain that invariant.

Confidence: high; exercised the actual core, ledger, catalog/readiness and runtime together with a controlled transport, rather than inferring from a mock service.

## Previous findings re-adjudicated in current code

- Catalog column INSERT privilege bypass: closed. `permissions.py:197` uses `has_any_column_privilege` for both execution catalog tables. Current regressions cover column-only overgrants and safe reprovisioning; reviewed correction receipt reports 16 targeted passes and 41 combined permission/core/operator passes.
- Long-tool-turn bearer expiry: closed in current source. `codex.py:31` captures account/model/origin/invocation and a copied scope for the private mint callback. `execution_proxy.py:44` validates renewed invocation/model/funding/scope/expiry; renewal occurs under the operation lock before allocating a new UUID or sending the next operation. Existing uncertain operations are never resent; accepted count remains unchanged by renewal. Current fake-clock, revoked/mismatched renewal and cancellation regressions inspected.
- CLI invocation reused across user turns: closed. `easel/harnesses/__init__.py:84` creates a fresh central invocation UUID for each accepted console turn, preserving the accepted chat identity/native thread. Current two-turn regression inspected.
- Old-account execution rows after scope change: closed. `CentralAccountSettings.tsx:180` passes origin/account/full-scope identity; `CentralExecutionSettings.tsx:20` hides mismatched stored data synchronously and includes that identity in effect invalidation with AbortController. Current same-auth A→B and late A response regressions inspected. Unknown readiness no longer defaults to available.
- Default output exceeding smaller immutable model ceiling: closed. `execution_runtime.py:124` takes default output from authenticated immutable model limits; the lower-ceiling 1024 regression is present.

## Cross-component checks with no additional confirmed P1/P2

- Financial writes use existing CreditLedger transaction helpers. Admission serializes session/wallet/invocation before reservation/lots; dispatch commits its one-attempt marker before HTTP; settlement atomically updates ledger, job, receipt, result and invocation counters. Exact rational input/output amounts are rounded once. Operation counts and cumulative held+spent budget survive token renewal.
- Core replay checks key/body before mutable model configuration. One dispatched job cannot dispatch twice. Unknown dispatched outcomes retain holds; only conclusively undispatched jobs release. Recovery never sends provider requests. Internal settlement uses frozen scope after revocation; over-limit usage stays pending and blocks the binding.
- Migration 006 adds immutable definitions/token/receipt/history protections and deferred financial/counter reconciliation; no delta changes migrations 001–005. Runtime grants cannot publish/enable catalog entries after the corrected INSERT check.
- Gateway bounds requests, JSON, SSE events and retained results; canonical hashing precedes upstream model mapping. JSON/SSE completed/incomplete receipts require integer nonnegative token usage, settlement precedes final success, and failed/missing/invalid terminal usage remains pending. Fixed provider configuration disables env proxies and redirects and does not use ambient OpenAI keys. No public settlement endpoint exists.
- Native central selection is explicit; old unversioned settings/bindings remain legacy. Session binding includes backend/origin/account/full scope, and central tasks cannot silently fall back to custom keys. The local proxy uses an exact literal-loopback route/Host/token, durable per-POST UUID/hash records, one send and disabled native retries. Central RPC is unpooled and closes before proxy teardown. Supported media helper guards precede dotenv/subprocess paths.
- UI keeps custom provider controls separate, labels fixture/test use and pending media/cloud capabilities, and preserves unknown financial values. Current readiness/model fields are validated and secrets are filtered before browser exposure.

## Pending root-discovered fixes and evidence boundaries

Root reported a separate actual-preview launcher defect during this review: a Node-backed external command `[node, codex.js, app-server]` receives inserted `-c` arguments before the JS script, so Node treats a Codex config expression as its module path. It caused startup failure before any central/provider operation. Root assigned a dedicated fix and actual-Node regression. This report does not mark that issue closed; inspect its final source and receipt in the follow-up review.

Root also assigned correction of generic settings copy that still tells an explicitly central-subscription user to fill in an API key. This is a pending UI wording correction, not evidence of key fallback or an actual financial side effect.

The whole-stage test counts supplied by root (current PostgreSQL 385, native 318, media 243, frontend 200) and earlier worker receipts are existing execution evidence, not newly rerun by this reviewer. Root owns ongoing desktop/mobile preview and native integration acceptance. No live supplier, real payment, media generation, cloud execution or production readiness is established here.

## Minor limitations / truthful handoff requirements

- The native proxy buffers central SSE until completion. Current short native fixture cycles do not establish all long-stream/idle-timeout UX behavior; keep this disclosed and avoid claiming incremental native token streaming.
- Durable local operation records preserve UUID/hash/status, but there is no implemented native automatic same-key result-recovery client. Do not describe this as automatic retry/recovery. Central pending reconciliation is deliberately retained rather than fabricated as zero cost.
- Native filesystem access remains the existing same-user boundary; environment/output credential filtering is not an OS-level filesystem sandbox proving that secrets can never be read from user-owned files.
- The runtime implementation receipt's older hard-4096 wording was superseded by the current immutable per-model default fix; keep final handoff documentation consistent with current source.

Final closure requires the mode-transition correction and review of the pending Node launcher/settings copy corrections, followed by root's remaining focused preview acceptance. No other confirmed P1/P2 was found in this review.

## Follow-up review — mode, Node launcher and settings copy

The P1 funding-mode finding above is **closed in current source**. `execution_runtime.py` now compares the authenticated task context's `funding_mode` with `self.settings.mode` immediately after authentication and raises `403 execution_mode_mismatch` before request admission, reservations or provider I/O. Internal `ExecutionService.finish` is unchanged and still settles frozen dispatched jobs independently of the current public admission mode.

Inspected the complete new `services/platform/tests/test_execution_mode_isolation.py`: its real-PostgreSQL cases cover test→live and live→test token rejection; test/live→disabled rejection; and all four transitions retaining trusted settlement after session revocation. Rejected operations assert no provider calls, jobs, reservations or receipts and unchanged ledger plus invocation count. Settlement tests assert the original funding mode and no change to the opposite mode's available balance. Root reports red **2 failed / 6 passed**, green **8 passed**, and combined central target **80 passed**. These are reviewed root execution results; no unnecessary reviewer rerun was performed.

The actual Node subscription launcher defect is also **closed in current source**. `prepare_subscription` appends Codex configuration flags after the complete launcher/script/subcommand sequence (`[*command, *arguments]`). Both central and legacy paths share this correction, while custom preparation retains its previous contracts. Inspected `tests/test_codex_launcher_arguments.py` and `/private/tmp/easel-s3-node-launcher-fix.md`: direct/Node launcher and central/legacy combinations, executed Node/direct fixtures, secrets absent from argv, unchanged command input, and custom regressions are covered. The report records **113 passed, no skips** and an actual installed Node Codex `initialize` response with id/result and exit 0. Initialization alone is not model-call acceptance; root's current preview has progressed past this startup failure.

The central/legacy settings-copy issue is **closed in current source**. `HarnessSettings` notifies its parent only after a successful read/save; `SettingsPanel` derives setup instructions from that persisted state. Central points to account/subscription and shared credits, legacy points to the advanced old test account and independent token quota, and missing backend stays legacy. Unsaved previews and failed saves do not change the parent instructions; custom save restores provider-key setup copy. Inspected the callback/component regressions and `/private/tmp/easel-s3-copy-fix.md`, which records **205 frontend passes**, successful build/lint and only preexisting warnings.

**Whole-stage verdict remains pending actual native protocol acceptance.** Root reports that actual Node Codex now starts but its request is rejected by the strict central gateway before admission (zero jobs/upstream calls and unchanged wallet). A dedicated protocol investigation is in progress. This follow-up does not classify that rejection as resolved or grant whole-stage PASS; review its eventual exact normalization/allowlist changes and acceptance evidence first.

## Final source-review closure — actual native protocol correction

**Final independent S3-A code-review verdict: PASS. No unresolved actionable P1/P2 remains in the reviewed current source.** This supersedes the initial changes-required verdict and the intermediate source-review hold above. It does not claim completion of root's pending actual browser/preview acceptance, production deployment, live supplier/payment verification or a merge.

Re-reviewed current `execution_protocol.py`, the protocol regression additions, the new `test_execution_native_metadata.py`, the actual installed Node `test_central_native_protocol.py`, and `/private/tmp/easel-s3-actual-protocol-fix.md`. The actual native request rejection was caused by top-level `client_metadata`. The correction accepts only six observed field names (`session_id`, `turn_id`, `thread_id`, `x-codex-installation-id`, `x-codex-turn-metadata`, `x-codex-window-id`), requires string values of at most 4096 UTF-8 bytes each, and rejects unknown fields and malformed shapes. The complete request is still size-checked and canonically hashed with metadata present; metadata is removed only from the returned provider request. It cannot modify routing, frozen scope or funding mode. Existing media/hosted-tool/root-key restrictions remain present.

The actual Node regression exercises the installed launcher through the real adapter, owned loopback proxy and production validator with a fixture gateway transport, completing a two-request local tool cycle. It asserts distinct operation keys, actual metadata on both requests, metadata absent from forwarded bodies, function-call output on continuation and final text. During review I identified that its new secret-record assertion initially globbed the wrong directory and therefore checked zero files; the author corrected it to `central-execution/operations/*.json` and now asserts exactly two durable records before checking token absence. This test-only issue is closed.

The separate real-PostgreSQL/owned-HTTP test confirms untrusted client session metadata does not replace task-token scope, metadata is removed from upstream request, identical replay performs one call/charge, changed metadata under the same key conflicts, and the ledger spends exactly 700 fixture microcredits once. The final receipt reports **53 protocol/gateway/runtime passes**, **24 native passes with one packaged-binary-only opt-in skip** (the actual installed Node case did run), and Ruff clean. I inspected this evidence and the test assertions rather than repeating already successful suites.

All findings encountered by this whole-delta review and the root's subsequent actual-preview debugging now have inspected source corrections and relevant focused regression evidence: mode isolation, Node launcher argument order, native metadata boundary, settings wording, plus the earlier permission/renewal/invocation/UI-scope/output-limit fixes. No production source was edited by this reviewer; only temporary proof/report artifacts were created. Root should retain the stated fixture/live distinction and complete its current desktop/mobile preview execution checks before reporting operational acceptance to the user.

### Root integrated preview evidence received after code-review closure

Root subsequently reported successful actual browser execution through the installed npm/Node Codex → owned local proxy → central service with real PostgreSQL → isolated fixture provider. The accepted turn used `update_plan`, then sent `function_call_output`, and produced exactly two provider POSTs and two settled jobs. Each receipt contained 100 input / 20 output tokens and charged 14,000 microcredits; the preview wallet moved from 200,000,000 to 199,972,000 microcredits, with 28,000 spent and zero reserved. The UI displayed the fixture warning and final reply. These exact totals are arithmetically consistent with the published fixture rate (100/input and 200/output).

This is root-reported integrated browser/preview evidence, distinct from the reviewer's isolated mode proof and inspected worker tests. It closes the previously reported pre-admission native-protocol acceptance hold for the controlled fixture chain. Root's final expanded PostgreSQL/native/frontend reruns were still running when this update was written; do not report those as completed from this document.

**Final whole-S3-A independent review disposition: PASS for the reviewed source and controlled fixture execution scope.** No unresolved review blocker remains. Final consolidated test receipts, any remaining desktop/mobile checklist items and the user handoff remain root-owned. Live supplier/merchant, media/cloud and production acceptance are still explicitly outside this PASS. No merge, push or commit is authorized or performed by this review.
