# S3-B Central Streaming and Interrupted Status Implementation Plan

> **For agentic workers:** Use subagent-driven-development for independent tasks and task-scoped reviews, then a complete S3-B delta review. The user approved this scope on 2026-10-23. Preserve existing work; do not commit, push or merge.

**Goal:** A centrally subscribed local Codex session displays incremental model output and can inspect the authoritative state of an interrupted model operation without sending it again; a real existing-provider text model receives separate controlled acceptance.

**Architecture:** Keep the current central one-attempt Responses runtime and shared PostgreSQL ledger. Replace native whole-response buffering with bounded incremental HTTP/1.1 chunked SSE delivery. Resolve durable local invocation/operation tuples through authenticated read-only central lookup; local delivery states never determine billing. Existing provider/New API is selected, and the user authorized meaningful real tests with no monetary cap. The user has now configured channels and a business token, and authorized server-only key use. MiniMax Responses conversion, central protocol and ledger checks have real-provider evidence; complete installed-Node-Codex acceptance remains open.

**Tech Stack:** Existing asynchronous Python, HTTPX, FastAPI/Starlette, PostgreSQL, Codex 0.146.0, React/TypeScript. HTTPX streaming documentation was fetched using Context7 `/encode/httpx`; retain cancellation cleanup and avoid a chunk_size that delays small events.

## Global Constraints

- Stay in `/Users/jiandan/Code/GitCode/Easel` on `codex/ui-redesign`; no commits, pushes, merges, resets, unrelated cleanup, root `.env` replacement or Logto branding changes. Beginning receipt `/private/tmp/easel-s3b-baseline.json` records 246 preexisting dirty files; review snapshot `/private/tmp/easel-s3b-before`.
- The later real-provider continuation begins from 264 existing dirty paths, captured in `/private/tmp/easel-s3b-live-20261023/baseline.json`; preserve that continuation baseline as well as the original S3-B baseline. The 246-path and 264-path counts are different stages, not contradictory current test counts.
- Preserve custom provider interfaces, legacy subscription behavior, all five themes/day/night, current accounts and wallet history. No silent switch to a custom key when central execution fails.
- Keep all six existing SQL migrations immutable. Status lookup is read-only and requires no new schema. Reuse existing transactions and financial state transitions.
- One operation UUID has at most one physical provider POST. Native retries remain disabled. All execution status lookups perform GET only; no automatic resubmission, task-token mint, replayed tool execution, price/usage modification or zero-cost inference. Existing same-scope authentication refresh may renew the product bearer; it cannot dispatch a model operation.
- Current product authorization is always validated. Historical lookup filters user, tenant, product and billing account. A fresh authorized session of the same business owner may inspect history; an old/revoked bearer cannot. Execution renewal remains bound to the original full scope, including session and epoch.
- Existing central terminal-success ordering remains: trustworthy terminal usage is settled before the terminal SSE event is delivered. Earlier text is provisional. Unknown usage, downstream cancellation or upstream loss never fabricates successful settlement.
- SSE single event <=256KiB, total response <=8MiB, request <=1MiB. Incremental delivery has backpressure, finite I/O deadlines and connection cleanup. Once response headers are committed, never write a second HTTP status line.
- Local durable records remain 0600 in owned 0700 directories and contain identifiers, hashes and transport metadata only, never prompts or credentials. Legacy unowned records remain on disk and are not exposed as the current account's recoveries.
- Real provider secrets stay in private server configuration; no secrets in source, browser, child provider environment, ordinary logs or validation artifacts. Never use test points with a live binding or live points with a fixture binding. No live calls before the model, privately configured business credential and isolated live entitlement are ready; calls remain bounded despite the user's no-cap authorization.
- Prefix shell commands with `rtk`, run network/loopback commands outside the sandbox as needed, and use pytest `--tb=short` without locals. Review task deltas against the S3-B snapshot, not all preexisting S1/S2/S3-A work.

## Shared recovery contracts

`easel/platform/execution_records.py` exports:

```python
def operation_owner(origin: str, scope: dict) -> str:
    # SHA256 canonical origin + user_id/product_id/tenant_id/billing_account_id.
    # Excludes current session/authorization epoch only for historical reads.
    ...
```

The proxy writes its existing durable record with additional fields `version:2`, `owner_key`, `model`, `created_at`, `updated_at`, and optional valid `execution_id` from the central response header. `operation_key`, `invocation_id`, `body_hash`, and transport `status` remain. Missing credential/scope in isolated old test seams produces an unowned legacy record, never an owner guessed from ambient state. `delivered` means the native socket drained, not that the user saw it or that billing succeeded.

`ExecutionService.lookup_operation(scope, invocation_id, operation_key)` validates identifiers, current scope, and owner authorization and returns `public(job)` plus `result_state` (`available|expired|not_available`). It filters on all four business owner dimensions and returns 404 for absent or another owner's tuple. It does not return cached payload, task tokens, body hashes, private bindings or original Scope. `settled` is financial state even if `result_state=expired`.

HTTP `GET /v2/execution-operations?invocation_id=...&operation_key=...` authenticates the current product bearer. Tuples generated by native use UUIDs; encode query parameters and validate bounded ASCII keys without path concatenation. Existing model POST and task-token interfaces remain unchanged.

`CentralClient.recoveries()` reads at most the newest 50 valid current-owner records from the fixed workspace's `outputs/_harness/central-execution/operations/` directory, queries each tuple with the current product bearer and returns:

```json
{"recoveries":[{"operation_key":"uuid","invocation_id":"uuid","model":"model-id","local_status":"pending","lookup_status":"found","execution":{"id":"job-id","model":"model-id","status":"settled","amount":"1000","actual":"700","funding_mode":"test","rate_version":"v1","input_tokens":3,"output_tokens":2,"created_at":"ISO","updated_at":"ISO","result_state":"available"}}],"truncated":false}
```

Other `lookup_status` values are `not_found` (not proof that a request cannot still be accepted) and `unavailable` (not a final state). Authentication/account failures stop the read instead of exposing stale rows. Do not query other-owner, malformed, oversized, symlink or unowned records. Current-owner results use existing strict `_execution_view` validation plus bounded result_state. Optional `execution_id` is a hint, not trusted billing or owner authority. No file path, owner hash, body hash or token enters browser output.

`GET /api/platform/central/execution-recoveries` is the loopback product bridge; frontend `centralExecutionRecoveries(signal?)` uses it without bearer/localStorage. Status inspection is explicit/manual and bounded, not an unbounded poll. UI state clears synchronously on `scopeKey` changes and discards late responses.

## Task 1: Incremental native stream and durable operation metadata

**Owner/files:** `easel/platform/execution_proxy.py`, new `tests/test_central_execution_stream.py`; targeted additions to native acceptance tests if necessary. Do not edit central runtime/core or the record-reader module (Task 2 owns that).

- [x] RED: gated `httpx.AsyncByteStream` yields created/text before waiting on a terminal-release Event. A real loopback client must receive text before releasing the gate; current buffer fails. Keep failures bounded with wait_for.
- [x] Implement native SSE parsing/framing with event size and total limits, split UTF8/CRLF support, finite drain/stream timeout, fixed sanitized errors. HTTP/1.1 chunked writes one complete validated event at a time, with a final zero chunk only after a valid terminal. No whole response accumulator for SSE; bounded JSON fallback stays supported.
- [x] Persist version/owner/model/timestamps/header execution_id. Write delivered only after valid completed/incomplete and final drain. Error/failed/EOF/cancellation remain local pending; no retry. The proxy writer owns its `_record`; use Task 2's `operation_owner` function.
- [x] GREEN: early delivery, raw chunk framing, terminal error/EOF, too-large event/total, downstream disconnect, timeout and cancel; verify exactly one provider POST and no credential data in records/errors.
- [x] Run actual packaged and npm/Node Codex against a gated fixture: observe an app-server text delta before fixture terminal release, then complete a tool continuation. Native retries remain zero.
- [x] Save exact red/green and regression evidence to `/private/tmp/easel-s3b-stream-report.md`; request task review, resolve findings. No commit.

## Task 2: Scoped operation lookup and native recovery bridge

**Owner/files:** central `execution/core.py`, `execution_web.py`, `easel/platform/execution_records.py`, `central.py`, `central_web.py`, new targeted central and native recovery tests. Do not edit proxy, frontend, existing migrations or provider config.

- [x] RED: real PostgreSQL settled/pending/released/expired lookup by tuple; cross-user/product/tenant/wallet/session rejection, fresh authorized same-owner session can inspect history, no provider I/O or ledger mutation. Record-reader tests include new-owner switch, legacy/malformed/symlink/oversize/traversal, missing tuple and unavailable service.
- [x] Implement shared contracts, strict safe public view validation, fixed read-only bridge route, bounded 50-row scan and authenticated GET calls. Preserve historical records and transport status; never invent actual=0 on missing receipt.
- [x] GREEN: distinguish settled+expired from pending unknown; 404 not-found gives safe state without resend; authorization failure hides data; local hash never used as central hash/authority. Existing models/executions/API compatibility tests continue passing.
- [x] Record red/green and read-only test assertions in `/private/tmp/easel-s3b-recovery-report.md`; independent task review; no commit.

## Task 3: Product interrupted-status UI

**Owner/files:** frontend API types/helpers and `CentralExecutionSettings.tsx`, tests/frontend/centralExecution.test.mjs or a focused new test. Root implements once Task 2 contracts are fixed.

- [x] RED: manual “查询中断请求” fetch, current-account isolation, cleared late reads, found/pending/not-found/unavailable/expired distinctions, no POST/retry action or automatic lookup on login.
- [x] Add account-scoped read section with local transport progress and authoritative central status/points; explain text can be provisional and local delivery is not a financial state. Do not display local operation tuples as another account's data. Maintain theme classes/responsive cards; no provider setup internals in ordinary product flow.
- [x] GREEN: frontend suite/build/lint; desktop and CSS390px browser screenshots with current fixture results. No source/env setting switch outside private preview.
- [x] Task review and current-owner late-response checks; no commit.

## Task 4: Real-provider controlled acceptance and consolidated verification

**Owner:** root. Use existing-provider/New API private configuration and select an available compatible text model from authoritative channel evidence. The user explicitly authorized testing with no spending limit; still use only meaningful bounded acceptance calls. A live config must be HTTPS and a compatible Responses route; business model credential is distinct from the New API administrator login. Do not infer full compatibility from a model list.

- [x] Prepare and validate server-only private configuration using an owned isolated validation schema/runtime/workspace and immutable `central-minimax-m27-live-v1` binding. Preserve fixture preview/test points and root `.env`. Seed only a clearly labeled synthetic noncommercial live entitlement/lot/issue journal; this does not prove paid membership, commercial pricing or OIDC login.
- [x] Execute four bounded real central-provider operations: plain JSON, forced function SSE, tool continuation SSE, and downstream interruption after text. All four have trustworthy usage and settlement receipts; same-key replay/status GET preserves the physical provider POST count and ledger. This is central protocol/ledger acceptance, not installed-native acceptance.
- [ ] Complete exact provider-request correlation where supported, post-change Chat success verification, and a full installed-engine real text/tool path. The first installed Node Codex request remains `pending_reconciliation` with unknown usage and a held amount; do not release, zero-settle or automatically resubmit it. User is now considering a lighter agent engine, so captured full native-body external retesting remains unexecuted pending the engine-route decision and any required data authorization.
- [x] Run full PostgreSQL platform tests, native/custom/legacy/CLI/portable, media custom regressions, frontend tests/build/lint, existing Ruff, wheel and source/migration checks. Back up preview DBs before any needed change, restart only owned preview services; preserve Logto and unrelated ports.
- [x] Complete per-task and whole-S3-B independent review, fix actionable findings, record current exact results and fixture/live evidence separately. If supplier/model/credential/budget access is still missing, finish all independent development and explicitly leave live acceptance pending rather than claim S3-B complete.
- [x] Recheck baseline branch/head/main/env and all prior dirty paths; document operator and user steps, screenshot the result, leave current branch unmerged and uncommitted.

## Progress

- Context and approved scope checked; two read-only audits identified the native buffering/delivery-state defect and missing tuple lookup. Existing-provider/New API selected; user authorized real testing with no monetary cap. Resolve the available model and private service credential without exposing secrets.
- Tasks 1–3 implemented and verified, including actual packaged/Node early-token gates and desktop/390px fixture-browser acceptance. Parent directory and double-HTTP-response findings are independently closed. Full central 417, media/custom 243, frontend 212 and final native 461 cases passed; build/lint and 30-Python/6-SQL wheel byte checks passed. Whole-source review found a malformed-auth-body classification issue; it was fixed with 26 added cases and independently closed. Final implemented-source review PASS; the earlier source findings remain documented.
- User initially selected self-configuration of the New API supplier channel and business token; production inventory was empty at that earlier inspection. The later readiness confirmation, five-model matrix and real-provider results below supersede that historical pending status. Task 4 remains partially complete because installed-engine real acceptance is still open. Software/fixture evidence remains in `docs/validation/2026-10-23-central-s3b.md`; the current external evidence is recorded in `docs/validation/2026-10-23-new-api-live-discovery.md`.

### Task 4 live acceptance continuation

The user subsequently confirmed supplier setup and saved the `easel` business token. Initial production metadata showed two enabled Type 60 channels and five advertised models: `deepseek-v4.1-flash`, `minimax-m2.7`, `kimi-k3`, `glm-5.3`, and `grok-4.7`. The public pricing catalog advertised Responses support, but the ten-request first matrix had no successful Responses result. Chat JSON succeeded for GLM (21/76 input/output tokens), Grok (222/179), and MiniMax (47/92); DeepSeek timed out and Kimi returned HTTP 500. These are configured model identifiers, not verified official model provenance.

- Preserve the original test preview, its existing wallet, Logto and execution binding. Use a new owned validation schema/runtime/workspace for live-provider acceptance because execution mode is global per process and shared-schema workers process both financial modes.
- The validation schema may seed a clearly labeled synthetic noncommercial live entitlement, lot and balanced issue journal in one transaction. It must contain zero billing orders, payment events or payment transactions; no fake paid order, no new product live-grant capability, no alteration of commercial issuance policy, no conversion of existing test credits. This proves real supplier transport and ledger mechanics using acceptance credit, not paid membership or commercial-price acceptance.
- Use the actual identity authentication/service boundary, one-attempt model dispatch, bounded output and existing HTTPS credential controls. Correlate response usage with central settlement and New API request logs; repeat status/replay without a new physical provider request. Keep interrupted unknown operations held unless trustworthy accounting evidence exists.
- Automatic approval rejected copying a business token from the production database into local temporary configuration because the prior handoff kept the key on the server. That operation did not execute. The user then explicitly authorized **server-only read/use, with no key leaving the server**. Real requests and private provider configuration obey that authorization; the local-copy path stays disabled. Separate temporary synthetic product-session credentials used by the client are not New API business keys.
- Official `v1.0.0-rc.45` source confirms built-in Advanced Custom Type 58 Responses-to-Chat conversion; Type 60 remains pass-through. Add only channel 3 `Easel S3B MiniMax Responses v1`, scoped to `minimax-m2.7`, with explicit Responses-converter and Chat-`none` routes. Original channels 1/2 were not edited. The channel/key copy and ability insertion occurred server-side atomically after a private database backup; normal cache reload picked it up without a New API restart. This does affect normal default-group matching for that one model. Source references and rollback boundary are in the live discovery report.
- Actual converted MiniMax SSE returned HTTP 200 `completed`, marker and 47/98 input/output tokens; first text 16.4836s, terminal 16.4840s. These observations prove text precedes terminal in this sample, not good long-answer latency. Custom-tool input buffering and unsupported stateful Responses fields remain converter limitations; full native compatibility cannot be inferred from ordinary function support.
- The owned runner issued exactly four provider POSTs with no retries. Plain JSON used 45/128 tokens and settled 215 synthetic microcredits; forced function SSE 218/60 settled 199; same-invocation tool continuation 280/256 settled 524; downstream interruption after text 54/256 settled 411. Three terminals were truthful `incomplete` at the finite output limit, one `completed`; all four had trustworthy accounting. The interrupted client did not see terminal, while the central service had already obtained and settled it. Do not claim that sample proves an unknown-upstream interruption was settled successfully.
- Four-operation total is **1,349 synthetic microcredits**, at acceptance-only input `1/2` and output `3/2` per token with one final rounding. The snapshot then had issued 100000000, available 99998651, spent 1349 and held 0. Billing orders, payment events and payment transactions were all zero. IdentityService mint/auth and real HTTP were exercised with a synthetic owned session; there was no new OIDC or paid-subscription acceptance. Same-key replay and GET left provider dispatch `1 → 1`, all row counts and balances unchanged. New API safe logs 18–21 match central usage by channel/model/path/token counts, but their exported request-id field is null, so exact request-id correlation is not claimed.
- The actual installed Node Codex first run issued one central POST, failed after 9.1884s and produced neither the plan tool nor final marker. Its operation `de56a642-a63f-421c-a7a1-8e63350a1663`, job `f343005c-146a-46ab-ba84-ea5bc67c294d`, is found by read-only recovery as `pending_reconciliation`, `actual: null`, usage unknown, result not available and **33152 held**. Preserve that audit and hold; the earlier held-0 snapshot is not the later wallet state. A channel-3 Chat regression separately returned HTTP 503 overload and has not yet passed after the addition.
- Safe synthetic developer/system-role controls both returned HTTP 200 `completed` with the ordinary function, at 195/47 and 186/51 tokens. Consecutive-user and simplified-namespace controls also returned 200. A long synthetic-input control returned 500 with fixed token/quota error keywords; these do not establish a context-length root cause or prove supplier quota exhaustion. Do not apply blind role rewrites or remove tool/history data as a guessed fix.
- Automatic approval rejected raw production-log export despite regex masking, and separately rejected full captured native-body external comparison before execution. No bypass occurred: diagnostic exports use fixed server-side categories and manual synthetic bodies. The user then asked whether OpenClaw/Pi or another lighter open-source agent could replace Codex; full native-body retesting is not authorized by that question, and complete installed-engine acceptance remains open while that direction is assessed.
- Added reusable controlled runner and tests; both independently reviewed receipt-file findings are closed. This continuation's runner result is **16 passed**, related Ruff passed. Previous full central/native/media/frontend counts above belong to the earlier software/fixture stage, not a fresh rerun here. Final continuation preservation and owned-runtime cleanup still need an explicit receipt; do not claim them from the earlier phase's checked items.
