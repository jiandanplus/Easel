# 第三阶段：平台账号、测试套餐、模型网关与用量

日期：2026-10-16。用户已授权继续开发，并明确选择“还没有，新建独立后端”。依据已确认的订阅总体设计和交付路线图，本阶段交付可运行、可验证的独立平台服务与客户端接入。正式收款属于第四阶段，云端工具执行属于第五阶段。

## Global Constraints

- Keep changes on `codex/ui-redesign`; do not merge or push. Preserve the twelve pre-existing appearance files recorded in `.git/sdd/preexisting-ui.json` byte-for-byte and do not stage them.
- Custom/local remains usable without an Easel account. Preserve all custom provider credentials, settings and existing native session histories. Never silently switch service source, account, runtime or execution target.
- Subscription/local initially supports Codex only. Cloud execution and subscription media channels remain unavailable with explicit reasons; no local fallback for cloud requests.
- Run the platform backend as a separate process/package `easel_platform`. It must not import `web.app`, operate on local profiles/workspaces or execute user commands. Do not bundle the platform database, upstream keys or backend package in the desktop artifact.
- Upstream model credentials stay in platform server configuration. Browser responses, local task metadata, argv, logs and downloadable outputs must not expose access tokens, refresh tokens, task bearer tokens, passwords or upstream keys.
- No real upstream inference, login to third-party services, formal payment, public deployment or developer credential reuse. Validation uses temporary HOME/workspaces/databases, loopback dummy providers, fake credentials and isolated ports; do not touch the existing server on 7860.
- Test plans must be explicitly labeled as test entitlements, persisted on the server and granted only by an operator CLI. Registration grants no paid/model entitlement. Do not invent commercial prices or mark a plan paid.
- Server authentication, ownership, entitlement, model permissions, token budget, concurrency and idempotency are authoritative. A frontend selection or local config never grants service access.
- Freeze source/account/model at task acceptance. Block local account/server/source/model changes while tasks are accepted or running. Existing subscription sessions cannot be resumed under a different account.
- Keep each task's tests isolated before invoking code. Do not run real profile skills, engines or external network from unit tests. Native integration proof may launch only its own native Codex against a loopback dummy service.

## Architecture and scope

The existing source/target/runtime contracts remain independent. A new standalone FastAPI service uses a separate SQLite database for this single-instance development stage. It serves identity, device sessions, test plans/grants, model catalog, scoped task credentials, Responses forwarding and usage. SQLite transactions enforce reservations; the service contains no tool executor. A CLI initializes/serves the database and creates/grants test plans. Public deployment, clustered database operation, email delivery/recovery and payment are not claimed.

The client connects to an explicitly configured platform URL, using HTTPS except loopback HTTP for development. Local FastAPI endpoints bridge registration/login/account/subscription operations. The browser never receives platform bearers. The new settings account/subscription panel uses existing theme tokens and components; custom provider settings stay independently preserved.

No existing remote service is available. Alternatives were connecting an existing backend or a hosted identity/subscription service; the user selected a new independent backend. There is no hardcoded production endpoint and no automatic connection to a third party. No endpoint configured means “平台服务未配置”, with custom mode still functional.

## Identity and device sessions

Use email/password registration for the development service. Email is normalized for account identity; email ownership remains unverified and is shown as such. Passwords are 15–128 characters, at most 512 UTF-8 bytes, never trimmed or normalized; reject control characters. Use salted scrypt (N=131072,r=8,p=1,dklen=32,maxmem sufficient), constant-time comparison and generic authentication errors. Bound concurrent hashing and rate-limit registration/login/refresh on the server before expensive work. Store only password digests and SHA256 hashes of cryptographically random 256-bit bearer secrets.

Access sessions expire after 15 minutes, refresh families after 30 days. Refresh rotates both secrets atomically; reuse of an old refresh token revokes the family. Revoke/logout invalidates access, refresh and scoped task credentials. List/revoke device sessions only for their owner. Invalid JSON/validation errors use generic safe errors rather than reflecting password/token fields. A malformed database/configuration is never silently replaced.

Endpoints: POST `/v1/auth/register`, `/v1/auth/login`, `/v1/auth/refresh`, `/v1/auth/logout`; GET `/v1/account`, `/v1/sessions`; DELETE `/v1/sessions/{session_id}`. Registration returns an authenticated session but no grant. Access/refresh secrets are returned only to the native/local bridge making the auth request. Reject unexpected browser Origins at the standalone service; browser interaction goes through the local origin-protected bridge.

## Test plans and grants

Plans are operator-created records with id, name, `test_only=true`, positive token quota, positive concurrency limit, model aliases and duration. No seeded commercial plan or price. Grants have owner, plan/version, start/end, status, quota snapshot and unique operator idempotency key. Replaying the same grant operation cannot extend or duplicate entitlement. No public grant/activate endpoint. Expiry/revocation is checked on each model request and task-token mint.

GET `/v1/plans` returns real catalog records; GET `/v1/entitlements`, `/v1/models`, `/v1/usage` requires access authentication and returns only the caller's data. The catalog distinguishes configured gateway capability from real upstream authentication verification. The UI shows no entitlement, active test grant, expired/revoked grant, used/reserved/remaining tokens and pending reconciliation truthfully. No purchase button can claim an activation.

## Gateway, scoped task credentials and ledger

POST `/v1/task-tokens` creates a random task bearer bound to the authenticated session/account, selected allowed model and active grant, maximum 60 minutes. The bearer can call `/v1/responses` only, not account/admin endpoints; each request rechecks session revocation and grant status. Native Codex receives this bearer through its child environment and uses a private Codex home even in source mode. It receives the platform base URL, never upstream credentials.

The server has one explicitly configured upstream Responses base URL/key/model and a public model alias for this first slice. No client-supplied upstream URL, redirect forwarding or credential pass-through. Use a dedicated HTTP client with ambient proxies and redirects disabled. Upstream HTTPS is required except loopback dummy URLs. Only advertise model execution as ready when server configuration is present; real authentication remains unverified until separately tested.

For each model request, validate a bounded JSON body (maximum 1 MiB), selected alias and stream mode. Ensure an output bound (default/max 4096 tokens for this trial). Conservatively reserve UTF-8 serialized request bytes plus max_output_tokens against the grant's token quota before forwarding; this is an admission upper bound, not displayed as actual consumption. Rule version is `token-v1`; actual ledger consumption is upstream input_tokens+output_tokens, without monetary prices. Record input/output separately. Reject quota/concurrency excess before any upstream call. Handle UTF-8/SSE chunk boundaries correctly and bound accounting-parser memory.

SQLite `BEGIN IMMEDIATE` transactions and unique owner/idempotency-key records ensure concurrent calls and retries cannot double-reserve/settle or exceed admitted budget. Same key/different body is a conflict; an in-flight/completed key is not forwarded twice and returns an explicit conflict/status rather than replaying a billable call. Request bodies and stream text are not persisted; store their hash plus nonsecret routing and accounting data. Requests without a key receive a unique server id.

Successful Responses JSON or final streamed response usage settles exactly once and releases the unused reservation. Failure before dispatch releases reservation; once dispatch may have occurred, cancellation, disconnect, timeout, missing/invalid usage or restart uncertainty preserves the reservation as `pending_reconciliation`, releases the active concurrency slot only after upstream cleanup, and never fabricates zero cost. Operator CLI can reconcile by explicit request id and measured usage idempotently. Restart marks unresolved in-flight records pending without replaying upstream calls. Never forward a successful terminal response with unverifiable accounting as a clean billed completion. Sanitize upstream errors; close upstream streams on cancellation. Upstream keys and raw upstream error bodies do not enter client error messages.

## Client integration and task ownership

`easel/platform/` owns URL validation, private atomic session storage and the HTTP bridge. Store local platform URL/refresh/access state under `outputs/_harness/platform-session.json` (0600, parent0700, bounded non-symlink path). Existing generic private-output blocking covers this directory. Browser status exposes only user/session ids, email, expiry and nonsecret capability data. Refresh is serialized locally, atomic, and persists newly rotated tokens before other operations; network failure preserves prior state, invalid authentication fails clearly. Changing platform URL cannot forward an old bearer; require explicit sign-out/clear first.

Extend immutable TaskContext and RunRequest with nonsecret source/account identity as needed, retaining existing positional compatibility. Resolve subscription model/account from the authenticated server at acceptance, validate Codex/local and compare session ownership before reading attachments or launching tools. Propagate accepted values into native preparation; reject account changes rather than re-reading a new account into a queued task. Bind subscription sessions to their account and preserve existing custom bindings. Model/provider/runtime changes and logout/server/account changes use the existing accepted/running-task guard and close idle native sessions.

The native resolver keeps custom provider behavior unchanged. For subscription it obtains a scoped task bearer and applies the same Responses provider flags/private-home boundary on both bundled and explicitly installed Codex. Model listing uses the platform catalog and does not call a globally authenticated engine. Other runtimes remain available for custom mode and reject subscription mode before side effects. CLI chat/skill/ping must enforce the same contract as Web; unavailable or expired subscriptions cannot fall through to API/OpenClaw or custom credentials.

Add local `/api/platform/...` routes in a focused router/module, with a shared active-task callback, preserving local Origin guard. Settings gains “账号与订阅”, showing server configuration, registration/login, current account, device sessions, test plans/grants, usage and logout. Harness settings gains explicit custom/subscription selection and server-provided subscription model selection; cloud remains disabled. New components use current CSS tokens, clear loading/error states, accessible form labels and password fields. Never store authentication data in browser localStorage.

## Validation and delivery

Use temporary SQLite and fake clocks for expiry, session rotation/replay, revocation, ownership, grants, idempotency, concurrent budget use, upstream errors, streamed/JSON usage, cancellations and restart recovery. Test the actual local HTTP auth bridge and accepted task paths without native processes in unit tests. Preserve custom config hashes and anonymous custom/local behavior.

A dedicated loopback integration script starts its own platform service plus dummy Responses server and temporary local app, registers test accounts, operator-grants a labeled test plan, logs in through the local bridge, lists authorized models, runs actual native Codex through the gateway, verifies tool file output/SSE, and checks real ledger settlement. Also verify no-grant/expired/revoked/quota/foreign-owner failures, refresh and no fallback. Deny outside networking and use no developer credentials. Browser QA checks the new panel with disposable synthetic accounts; do not submit real passwords or payments.

Run source/frontend regressions, lint/build and final independent code review. Retain current branch and local commits. No new desktop archive is promised by this stage; previous phase's unsigned trial remains a historical artifact until explicitly rebuilt/validated for these new capabilities. Deliver source, runnable service/CLI instructions, new UI and local sanitized proof; leave real upstream, public hosting, email verification/recovery, production abuse controls/operations and commercial subscriptions explicitly outstanding.

## Documentation sources

- FastAPI StreamingResponse cancellation: https://fastapi.tiangolo.com/advanced/custom-response/ (Context7 read 2026-10-16).
- Password hashing: https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html (read 2026-10-16).
- Session lifecycle: https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html (read 2026-10-16).
