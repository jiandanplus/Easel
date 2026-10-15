# Platform accounts, test entitlements and Responses gateway Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a separate real account/entitlement/usage service and an Easel subscription/local flow while preserving anonymous custom/local.

**Architecture:** Standalone `easel_platform` FastAPI + transactional SQLite control plane and Responses-only model gateway. Local `easel/platform` bridges private session credentials to platform APIs; accepted immutable task identity reaches Codex. A settings panel exposes actual server state and test plans.

**Tech Stack:** Existing Python/FastAPI/httpx, standard-library SQLite/scrypt/secrets, existing React/TypeScript/CSS tokens. No new production dependency is required.

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

Detailed behavior is normative in `docs/superpowers/specs/2026-10-16-platform-accounts-design.md`; each task reads its named sections, not unrelated task history. No deployment/payment stage is included.

### Task 1: Independent identity, device sessions and test entitlements

**Files:** Create `easel_platform/__init__.py`, `config.py`, `store.py`, `auth.py`, `api.py`, `__main__.py`; create `tests/test_platform_identity.py` and `tests/test_platform_entitlements.py`.

**Interfaces:**
- `Settings(database: Path, upstream_url: str = '', upstream_key: str = '', upstream_model: str = '', public_model: str = '')` (dataclass) validates URL and keeps secrets out of repr.
- `Store(settings: Settings, clock: Callable[[], float] = time.time)` owns schema and transactions; expose register/login/refresh/authenticate/logout, session listing/revocation, plan creation/grant and entitlement queries through explicit methods.
- `create_app(settings: Settings, *, transport=None, clock=None) -> FastAPI` exposes identity/plan/catalog routes. `app.state.store` and `app.state.settings` are seams for Task 2, not a global singleton.
- Authentication response `{access_token, refresh_token, expires_at, refresh_expires_at, account:{id,email,email_verified:false}, session_id}`; authenticated public account/entitlement responses contain no tokens.
- GET `/v1/models` returns `{models:[{id,label}], ready:bool, reason:str}`; task2 adds actual task bearer/gateway dispatch. Before gateway configured, ready=false.
- CLI `python -m easel_platform serve --database PATH --host 127.0.0.1 --port PORT`, `plan create --database PATH --id ID --name NAME --tokens N --concurrency N --days N --model ALIAS`, `grant --database PATH --email EMAIL --plan ID --key IDEMPOTENCY`. All plan grants test-only.

- [ ] Step 1: Write failing behavior tests before implementation. Minimal first assertion:
```python
def test_registration_has_no_entitlement(tmp_path):
    app = create_app(Settings(database=tmp_path / 'platform.sqlite'))
    with TestClient(app) as client:
        registered = client.post('/v1/auth/register', json={'email':'a@example.test','password':'test-password-123456','device_name':'test'})
        assert registered.status_code == 201
        token = registered.json()['access_token']
        result = client.get('/v1/entitlements', headers={'Authorization':f'Bearer {token}'})
        assert result.json()['entitlements'] == []
```
Add independent cases for password hashing/different salts, malformed/oversize secrets without reflection, generic login errors, hashing/rate bounds, clock-driven expiry, refresh rotation/replay revocation, user/device ownership, logout, token digest storage, persisted restart, duplicate grant key, expiry/revocation and no public self-grant.
- [ ] Step 2: Run isolated runner for these two files and record RED. Command: `rtk proxy .venv/bin/python scripts/validation/run_isolated_tests.py --pytest-path /opt/anaconda3/lib/python3.12/site-packages tests/test_platform_identity.py tests/test_platform_entitlements.py -q`. No model/engine/network.
- [ ] Step 3: Implement the defined service/CLI interfaces, using parameterized SQLite transactions and the exact identity/plan rules from spec sections Identity and Test plans. Server body/error handling must not echo secrets. Every route checks ownership.
- [ ] Step 4: Run same tests GREEN; use subprocess mocked CLI parsing/real temporary DB operator operations, not a long-running server or source workspace. Report exact public methods/shapes for following task handoff.
- [ ] Step 5: Commit only task files and write `.git/sdd/phase3-task-1-report.md` with command/output, interfaces, concerns. No push/merge.

### Task 2: Task-scoped Responses gateway and transactional token ledger

**Files:** Create `easel_platform/gateway.py`, `ledger.py`; modify `api.py`, `store.py`, `__main__.py`; create `tests/test_platform_gateway.py`, `tests/test_platform_ledger.py`.

**Interfaces:** Consume Settings/Store/create_app from Task1. Produce POST `/v1/task-tokens` with `{model}` -> `{token,expires_at,account_id,model,base_url_path:'/v1'}` (secret only to bridge); POST `/v1/responses`; GET `/v1/usage` -> nonsecret totals/entries with used/reserved/remaining and pending states. Operator `reconcile --database PATH --request-id ID --input-tokens N --output-tokens N --key KEY`.

- [ ] Step 1: Write RED tests using `httpx.MockTransport`/temporary SQLite/fakeclock. Assert no upstream on no auth/no grant/model mismatch/expired/revoked session/quota/concurrency excess, task token rejects account API, request and stream bounds, no redirect/ambient proxy use. Test streamed UTF-8/chunk splits, JSON completion usage, missing usage, cancellation, timeout, replay, duplicate-body conflict, concurrent reserve/settle, process restart recovery and operator reconciliation.
```python
def test_budget_reservation_is_atomic(ledger):
    first = ledger.reserve(owner='a', grant='g', key='same', body_hash='h', tokens=100)
    with pytest.raises(Conflict):
        ledger.reserve(owner='a', grant='g', key='same', body_hash='h', tokens=100)
    assert ledger.snapshot('a')['reserved_tokens'] == 100
```
Choose concrete constructor/method arguments matching Task1 and report them; `Conflict` must be a safe platform error class, not a placeholder.
- [ ] Step 2: Run `rtk proxy .venv/bin/python scripts/validation/run_isolated_tests.py --pytest-path /opt/anaconda3/lib/python3.12/site-packages tests/test_platform_gateway.py tests/test_platform_ledger.py -q`; record RED.
- [ ] Step 3: Implement the spec Gateway/ledger section. Reserve serialized byte upper bound + output bound, settle upstream measured tokens exactly once, retain dispatched uncertain reservations as pending and close upstream before releasing concurrency. No raw stream or prompt stored, no total key forwarded to clients, no successful final event without valid accounting. Use explicit gateway error shape and status mapping. Recheck session/grant each call.
- [ ] Step 4: Run Task1+Task2 tests GREEN. Verify SQLite multi-connection concurrency and reconciliation idempotency with real DB transactions, no real network. Document exact usage/task-token response shapes and gateway registration seam.
- [ ] Step 5: Commit scoped files and `.git/sdd/phase3-task-2-report.md` evidence. No client or UI changes in this task.

### Task 3: Private local account bridge and subscription/native execution

**Files:** Create `easel/platform/__init__.py`, `session.py`, `client.py`, `web.py`; modify `easel/service_contracts.py`, `easel/harnesses/{base,store,runtime,__init__,models}.py`, `web/app.py`, CLI entrypoints only as needed; create `tests/test_platform_client.py`, `tests/test_subscription_execution.py`.

**Interfaces:** Consume platform API shapes from reports1/2. Local router factory receives workspace and a callback rejecting account/source changes while accepted/running work exists; routes live under `/api/platform`. Browser responses return only sanitized status/catalog/grants/usage. Add source/account identity to TaskContext/RunRequest with compatible defaults and propagate accepted identity, never a secret, through task metadata/bindings.

- [ ] Step 1: RED tests for session file0600/atomic preservation/URL change/no bearer leakage/private-output block; serial refresh/replay/network failure; real local router register/login/status/logout/device revoke; active-task guard on account/server changes; custom anonymous unchanged. Web/CLI preflight must reject missing/expired/no-grant/cloud/non-Codex subscription before file/process effects. Accepted account/model stable, cross-account native session rejected, custom credentials byte-preserved, no fallback.
```python
def test_subscription_rejects_other_runtime_before_dispatch(config, rejecting_adapter):
    config.update(service_source='subscription', execution_target='local', runtime='openclaw')
    with pytest.raises(HarnessError):
        task_context(config)
    rejecting_adapter.assert_not_called()
```
Also inspect actual cmd_chat/cmd_skill/cmd_ping and web task paths with process/network dispatch mocks, not just helper tests.
- [ ] Step 2: Run these new test files RED using isolated runner. Task auth HTTP uses MockTransport and synthetic sessions, never developer HOME or real platform.
- [ ] Step 3: Implement private local bridge, endpoint validation and refresh. Extend existing availability/model/session/native seams coherently. Subscription Codex requests obtain a scoped bearer matching accepted account/model and use private home/Responses flags in source and bundled modes; custom paths remain unchanged. Model list comes from platform. All account/source changes close idle native sessions after guard. Keep focused router logic out of large web/app.py.
- [ ] Step 4: GREEN new tests plus `tests/test_service_contracts.py`, `tests/test_service_cli_contracts.py`, `tests/test_bundled_provider.py`, `tests/test_portable_runtime.py`, relevant native/session tests. Report exact local routes/response shapes to Task4.
- [ ] Step 5: Commit scoped files, write `.git/sdd/phase3-task-3-report.md` with propagated identity/compatibility evidence. No real engine/upstream launch in tests.

### Task 4: Account/subscription settings UI and isolated full-flow acceptance

**Files:** Create `web/frontend/src/components/PlatformAccountSettings.tsx`, focused styles and API functions; modify `SettingsPanel.tsx`, `HarnessSettings.tsx`, `web/frontend/src/lib/api.ts`; add meaningful frontend tests; create `scripts/validation/verify_platform.py`, `docs/validation/2026-10-16-platform-accounts.md`, `docs/platform/README.md`. Update roadmap delivery evidence. Do not edit the twelve protected appearance files.

- [ ] Step 1: RED tests for account section navigation, accessible registration/login forms, password fields not persisted, server-driven test plans/no-price/no-grant state, error/expired states, usage/reserved/pending display, custom/subscription selection preserving custom provider panel and disabled cloud. Use existing frontend Node test conventions; avoid tests merely asserting decorative source strings.
- [ ] Step 2: Run targeted tests RED, implement account panel and service selectors with existing theme variables. Clear secret fields after submission, no localStorage tokens, preserve backend error/loading states. No activation/purchase UI claiming a real paid subscription.
- [ ] Step 3: Run `rtk proxy node --test tests/frontend/*.test.mjs`, frontend lint/build. Run full isolated source suite once final feature code is stable. Preserve baseline warnings distinctly.
- [ ] Step 4: Implement and execute owned loopback proof: temporary platform DB/HOME/workspace, dummy upstream fake key, real account registration/local bridge login, operator test grant, server model listing, accepted subscription task through real native Codex, actual shell/Python marker file/SSE and measured ledger. Use Origin headers, deny outside networking, verify usage/no-grant/quota/refresh/revocation/cross-account rejection and no custom fallback. Never use real user profiles or the existing7860server. Stop only owned processes.
- [ ] Step 5: Browser QA the new account panel against a disposable local test app with synthetic account; screenshot deliverable after changes, clear ownership lifecycle. Update docs with exact source/test/proof versions and remaining real-upstream/payment/cloud/deployment boundaries. No new .app/ZIP claimed current without a separately performed rebuild/probe.
- [ ] Step 6: Commit only scoped UI/tests/probe/docs. Write `.git/sdd/phase3-task-4-report.md`; parent performs final broad branch review and fixes any Critical/Important findings, confirms branch/main/protected hashes. No merge/push.
