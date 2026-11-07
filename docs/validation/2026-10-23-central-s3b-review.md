> 整项已实现源码最终审查 PASS；真实供应商验收待配置。以下保留初审问题和最终关闭记录。

# Whole-S3-B independent source review

Initial verdict: **CHANGES REQUIRED** — one additional P2 remains in the implemented source. This verdict concerns the exact S3-B source delta, not the full historical branch or completion of real-provider acceptance.

Review scope: all 15 files in `/private/tmp/easel-s3b-final-review.diff` against the S3-B before snapshot, the plan global/shared contracts, implementation reports, both task-review closure reports, and existing call paths through CodexAdapter, native configuration, central execution runtime, identity authorization and wallet authorization. Branch checked as `codex/ui-redesign`. No source/environment/preview/commit changes, no secrets printed, no live provider calls, and no broad test reruns. Only this report was written. A quick memory check supplied evidence-boundary context only; all source conclusions below use current files.

## Required finding

### [P2] Preserve authentication failure status when the recovery response body is malformed

Location: `easel/platform/central.py`, `_request` at lines 83-106 and `recoveries` at lines 389-403.

`_request` parses and validates the response JSON before processing `response.status_code`. A 401, 403 or 409 response with plain-text/HTML, empty body, or malformed JSON is therefore converted by the generic parsing exception branch into default `CentralError()` (503). During a tuple lookup, `recoveries()` treats that as an ordinary unavailable row and returns any financial rows already accumulated. This contradicts the explicit shared contract that authentication/account failures stop the read without exposing partial rows. Valid JSON auth errors are handled correctly, but the HTTP-level authentication failure is lost on common gateway/error-page responses.

Independent bounded offline reproduction used the existing native recovery fixture helpers and `httpx.MockTransport` in a disposable temporary workspace: `/v2/me` returned the valid current fixture identity; first tuple GET returned a valid execution; second tuple GET returned HTTP 401 with body `Unauthorized`. Actual output was `{'returned_partial_rows': ['found', 'unavailable'], 'operation_get_count': 2}`. Expected is an authentication failure with no returned recovery collection. The initial repro import lacked pytest on the venv path; the successful rerun used the existing approved system-site-packages path from the supplied pytest wrapper. No sockets, supplier calls, or repository records were involved.

Requested correction: preserve 401/403/409 failure classification independently of response JSON parsing/shape, while retaining sanitized errors and the existing rule that credentials are only forgotten for explicit invalid-authentication codes. Add parameterized empty/non-JSON/malformed-body failures after one successful row and assert the complete read raises. Ordinary malformed 503 responses should remain unavailable, and ordinary 404 must remain not-found without zero-cost inference.

## Prior findings closed

- Fresh workspace private-base mismatch: current proxy explicitly creates/opens the owned base and operations directories with NOFOLLOW directory descriptors and fchmods both to 0700. Ancestors and old records remain unchanged. The fresh-layout regression and current PostgreSQL/HTTP integration start with an absent base and discover the produced v2 record through the real reader. This closes the prior P1.
- Second HTTP status after an early JSON/error reply drain failure: `_reply` marks the shared response state committed before writing, and every handler reply passes that state. All four rejection branches have TimeoutError/OSError regression coverage. This closes the prior P2.

## Contracts verified in source and test assertions

- Current product authentication is required by the tuple GET route. `lookup_operation` validates the full current session/epoch through identity and wallet authorization, then independently filters historical user, product, billing account and tenant. A fresh currently authorized same-owner login may inspect old history; original revoked task contexts are not revived. The projection excludes scope, payload, hashes, private binding and task credentials. It performs no execution mutation or provider I/O. PostgreSQL snapshots in tests cover all relevant job, receipt, reservation and ledger tables before/after reads.
- Local owner hashing includes canonical origin and four business dimensions, excluding session/epoch only for historical discovery. The reader uses descriptor-relative NOFOLLOW traversal, owner/mode/regular-file checks, UUID filename matching, bounded fields, record bytes, scan count, row count and scan time. It ignores legacy/foreign/unsafe records without changing them. Fifty is the maximum returned records; when scan/time limits truncate discovery, newest ordering applies to the candidates actually inspected.
- Recovery requests verify current `/v2/me`, hold the private session lock, then issue URL-encoded tuple GETs only. The only possible POST is the existing same-scope product-auth refresh, not execution or task mint. Per-request timeouts and aggregate deadline checks stop further tuple requests. These are bounded read controls, not proof of a strict ten-second wall-clock SLA across lock acquisition or all blocking transport phases. Correctly parsed current-auth errors abort the batch; the malformed-body gap above remains.
- Native streaming has one central POST per new UUID and no replay branch. Existing native request/stream retries are explicitly zero; custom and legacy branches are retained. The central runtime has one physical provider send per admitted attempt, and cached same-operation replay bypasses provider I/O.
- Native SSE is incremental, UTF8/CRLF-aware and independently bounded to 256 KiB per event and 8 MiB total. Each validated event drains before the next upstream pull. Terminal completed/incomplete status is checked before successful final chunking. Error/EOF/timeout/cancellation/failed drain retain pending and fixed sanitized diagnostics; response commitment prevents a second status line. Owned transfer/watch tasks and network contexts are cleaned up.
- Central runtime settles trusted terminal usage transactionally before yielding the terminal SSE event. Unknown usage/upstream loss/downstream cancellation keep holds. Successful central settlement can coexist with local pending delivery; the integration test specifically asserts one 700-microcredit charge and zero remaining reservation after terminal delivery fails. The early gate test proves provisional text while financial state is dispatched, then settlement at terminal.
- The actual packaged and Node native tests assert an app-server token before releasing the terminal fixture gate and a subsequent tool-output continuation with a distinct operation UUID. Their source assertions establish relevant native coverage; they remain fixture evidence.
- UI lookup is manual, uncached GET with AbortSignal, no auto lookup on login, no resend/tool execution action and no browser bearer/localStorage. Render-time scope comparison hides old rows immediately, effect cleanup aborts old work and handlers discard aborted late responses. Logout/relogin requires another manual request. Local delivery, central financial state, unknown actual amount and result-cache expiry remain distinct; missing/unavailable never implies zero charges.

## Evidence and readiness limits

Parent reports central 417 passed, media 243 passed, frontend 212 passed with build/lint passing, current three PostgreSQL streaming integrations passed after the directory correction, and both native opt-ins passed in the 106-case focused run. Task reviewers additionally report an independent 16-case directory/header-failure closure run. This reviewer inspected assertions and reports without rerunning these broad passing suites. Root broad-native and wheel results were still in progress at assignment.

Desktop and 390px actual-browser acceptance was pending at assignment and is not established by this source review. The root must incorporate its actual current evidence separately.

Real-model acceptance remains **PENDING external provider configuration**, not a completed user objective. Parent reports authoritative existing New API database has zero channels, tokens, models and abilities; the user selected that existing New API and authorized bounded meaningful testing without a monetary cap, but has not confirmed provider/token configuration ready. This reviewer made no live check or model call. Required remaining acceptance is a real compatible HTTPS Responses binding and privately configured business token, correct live account/entitlement and verified model/rate publication, then bounded real text/tool/stream/interrupt/status evidence correlated with provider usage and the ledger. Fixture/test points must remain separate from that live configuration. Software review cannot substitute for this evidence.

Confidence: high in the inspected source call-path conclusions and the reproduced P2; moderate in overall delivery readiness because browser/package regressions and all real-provider acceptance are separate evidence stages. Final source approval requires closure of the P2 above.


## Narrow authentication correction closure — final implemented-source PASS

**Final verdict: PASS for the implemented whole-S3-B source delta.** This supersedes the initial CHANGES REQUIRED source verdict above; the original P2 and its reproduction are retained as audit history. No unresolved actionable source finding remains from this review. This is not approval to represent real-provider acceptance or the entire user objective as complete.

The reviewer inspected the current `_request` implementation and all newly added regression assertions, plus `/private/tmp/easel-s3b-auth-fix.md`. HTTP 401/403/409 is now captured immediately after headers, before decoding/validating the body. Malformed JSON, empty/non-object bodies, invalid error envelopes, over-limit bodies, aggregate-deadline rejection and HTTPX body-read failures retain the auth/account status with a sanitized generic code. This makes `recoveries` abort without returning previously collected financial rows. Explicit allowlisted invalid-auth codes still follow existing credential invalidation; malformed responses retain recoverable credentials. Malformed 404 retains not-found and malformed 503 retains unavailable without invented financial state. No model POST, task mint or retry behavior was added.

Independent narrow verification executed only the new relevant regression matrix:

```sh
rtk proxy .venv/bin/python /private/tmp/easel-settings-pytest.py tests/test_central_execution_recovery.py -k 'malformed_auth_failure_after_a_row or unreadable_auth_error_after_a_row or malformed_non_auth_error' -p pytest_asyncio.plugin -o asyncio_default_fixture_loop_scope=function -q --tb=short
```

Result: **26 passed, 19 deselected in 0.17 seconds**. These are offline MockTransport and temporary-file tests; no sockets or provider requests. The assertions require a successful first row, failure on the second auth-error lookup, preserved HTTP status and fixed error text, unchanged credentials/local records, and GET-only tuple calls. The earlier independently reproduced P2 is closed. No source, environment, preview or commit modifications were made by this reviewer; only this report was appended.

Root subsequently supplied further delivery evidence: an actual 1200px desktop and CSS390 browser fixture run with no overflow and screenshots under `docs/validation/assets/central-s3b`; actual Node Codex early text while the fixture terminal gate was closed, followed by two settled fixture operations at 14000 microcredits each; repeated status GET/UI reads with no extra provider POST/charge; and wheel byte comparison for 30 Python files and six SQL migrations (reported wheel SHA256 `605f2d023150676d76b4f44c39d715710e9738177c6643aaa5bd474aecb5dac0`). These are parent-supplied results, not independently rerun browser/package acceptance in this narrow review. Root final broad-native verification was still running at closure assignment and must be recorded by root when complete.

Real-provider readiness remains pending the user's existing New API provider/token/model configuration and confirmation. No live model call has been performed. The source PASS and fixture/browser/package evidence do not establish that external acceptance. Confidence is high for the reviewed implemented-source contracts and closure; live-provider completion remains unproven until the separate authorized acceptance runs.
