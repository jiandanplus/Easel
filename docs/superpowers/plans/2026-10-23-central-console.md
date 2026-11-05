# Central Operations Console Implementation Plan

> **For agentic workers:** Use subagent-driven-development, independent tests and review. The user approved continued development and chose a separate administrator account to register later. No further routine implementation approval is needed.

**Goal:** A usable independent central management website with explicit admin identity, real business data, auditable catalog operations and preserved user/custom API paths.

**Architecture:** Separate ASGI Console origin and low-privilege database role; independent Logto client and opaque-cookie BFF; reuse central services within one transaction for mutations. Separate React/Vite assets, never shipped in Easel.

**Tech Stack:** Existing Python/PostgreSQL/httpx/JWT and pinned React/TypeScript/Vite stack. No new identity or UI framework.

## Global Constraints

- Stay codex/ui-redesign, no commit/push/main merge; preserve root.env, migrations001–007, old wallets/services and custom providers.
- Never give Web owner DSN, internal issuance rights or provider/payment secret material. Never weaken product runtime guards.
- Fixed console callback /oidc/console/callback; exact-origin + synchronizer CSRF; empty ACL by default; only CLI approves verified requests.
- Read-only pages do not expire credits, call suppliers, reconcile orders or consume models.
- First version exposes catalog publication/state only. No Web credit issuance, refunds, manual paid status, credential upload or admin self-grant.

### Task 1: Admin identity and database boundary

Files: console_config.py, console_auth.py, console_permissions.py, migrations/008_console_auth.sql, permissions.py; tests/test_console_auth.py, test_console_permissions.py, test_console_config.py.

- [x] RED tests: ordinary bearer/noACL denied, exactissuer+subject ACL, signedOIDC and fixedcallback, one-time5mintransaction, sessionhash/cookieexpiry/revocation, CSRF, no selfgrant; real PG grant assertions and product role no Console table SELECT.
- [x] Implement ConsoleSettings, ConsoleAuth and AdminPrincipal contract. begin_login()->url/cookie; callback()->authenticated session or verified pending request; authenticate(cookie)->principal; validate(conn,principal,permission)->current authorized principal; logout(cookie).
- [x] Implement owner-only approval CLI service and non-owner Console grant/assertion. No Web role mutation; no owner credential loading.
- [x] Run focused tests and independent security review; update original runtime-permission regressions.

### Task 2: Management queries and atomic catalog operations

Files: console_service.py, console_catalog.py, migrations/009_console_operations.sql; tests/test_console_queries.py and test_console_catalog.py.

- [x] RED real-PG tests for fixed field projections, all-user access only after admin validation, bounded pagination, safe filters, zero mutations from read pages, no credential/body/config dumps.
- [x] Implement overview/users/products/plans/wallets/orders/offers/models/providers/executions/audits projections using bound SQL, string microcredits, typed status and genuine counts.
- [x] RED atomic rollback/concurrent idempotence/version-conflict/role-revoked tests. Implement publish_plan/offer/withdraw/model/state with explicit permission, reason, key and same-transaction immutable audit.
- [x] Keep internal issuance, live refund and manual settlement inaccessible; test these absent routes/privileges. Independent business/finance review.

### Task 3: Independent management frontend

Files: services/platform/frontend package/lock/config/src/tests and build receipt.

- [x] RED behavior tests for fetch credentials+CSRF+idempotency, 401/403/409, exact integer formatting and restricted actions.
- [x] Implement overview, users/products, plans/wallets, orders/payments, models/providers, execution/audit pages, detail drawers and validated publish dialogs. Canonical /api endpoints, no demo data, secrets or user-state imports.
- [ ] Build with pinned existing stack; asset manifest hashes; accessibility, responsive layout, errors/empty/permissions and disabled payment states. Independent UI review.

### Task 4: App integration, local and owned preview acceptance

Files: console_app.py, __main__.py CLI integration, admin asset packaging, integration tests, docs/platform/CONSOLE.md, validation report/receipts.

- [x] RED integration tests for precise routes, body limits, CSRF/Origin/bearer isolation, CSP, no-store, static path traversal, permission boundaries and disabled-by-default startup.
- [x] Integrate auth, services and static assets with dedicated serve-console command. Existing serve and /v2 browser boundary unchanged; no owner in Web.
- [x] Context7 current docs, frontend build, all relevant backend regressions and independent review. Check migrations exact set without changing old files.
- [x] Read-only preflight before isolated server deployment; new low-rights role and independent Logto app, private config over SSH; no production restart or model call. Verify source/config/role/port ownership and health.
- [x] Open actual Console login page for user's separate account; then approve only their verified request when identified. Until user completes, report pending real administrator acceptance accurately.
- [x] Record actual browser/data/catalog/audit evidence separately from fixtures, preserve old wallet and custom-provider checks, leave useful Console available.

Current handoff: server preview and browser entry verified; the user completed separate administrator registration, and exact verified owner approval plus its single ACL audit were checked. Fresh administrator login and all 11 desktop page reads passed after repairing the overview-to-list stale-data crash. Real catalog writes, mobile viewport and commercial payment acceptance remain separately unverified. Detailed evidence: docs/validation/2026-10-23-central-console.md.
