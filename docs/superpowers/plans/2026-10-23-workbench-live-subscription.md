# Workbench Live Subscription Implementation Plan

> **For agentic workers:** Use subagent-driven-development with independent task reviews. User already approved the exact internal grant scope; continue without further routine confirmation.

**Goal:** User-owned Logto login in the current 17868 Pi workbench, server-only provider keys, audited 10-point/7-day internal experience credit and real GLM dialogue/tools.

**Architecture:** Separate loopback server central/PG schema; encrypted SSH forwards reuse the existing issuer without changing it. Private operator-only grants are distinct from payments and old test funding. Existing desktop auth and execution paths remain authoritative.

**Tech Stack:** Existing Python/PostgreSQL/Logto/Pi/React stack, pinned dependencies and current branch.

## Global Constraints

- `codex/ui-redesign`, no commit/push/main merge. Preserve root .env, old services and 001–006 migrations.
- User enters their own identity credentials; no copying old auth or synthetic session. New confidential-client secret and provider Key remain server-side.
- Exact approved internal allowance: 10,000,000 microcredits, 7 days, one grant per environment/wallet, no payment records or public issuance API.
- Default disabled and loopback/internal-only; owner CLI holds new capability, runtime cannot issue.
- Keep actual model execution in live mode; never label a real upstream as a fixture to spend old test points.
- Bound model output/concurrency/transport attempts; unknown reservations remain pending and immutable.

### Task 1: Internal experience grant and accounting metadata

- [x] Add failing real-PG tests for owner/config/environment/session checks, exact policy, atomic balanced issue, concurrency/idempotency/conflict, runtime denial, payment rows unchanged and test/live separation.
- [x] Add `007_internal_grants.sql`, dedicated private-policy loader and grant service, and a narrowly gated `internal-grant` CLI. Preserve migration history. Add only necessary runtime SELECT access and readonly wallet/entitlement source metadata.
- [x] Run focused and related membership/permission/execution tests; independent review before deployment.

### Task 2: Product login and experience presentation

- [x] Add optional validated `force_login` to confidential OIDC client configuration and send standard `prompt=login` when enabled; default old clients unchanged. Verify the actual generated authorize parameters.
- [x] Show internal experience balances and nonpayment notice using server metadata; preserve unknown/error rendering and original sources. Update API types and meaningful frontend regressions.
- [x] Fix any Pi-specific stale CLI installation readiness copy encountered while exposing the actual dialogue view. Build new package only after source review/freeze.

### Task 3: Isolated owned deployment

- [x] Inspect exact existing Docker/service/port ownership and resources, without exposing credentials. Create fresh labelled loopback PG with pinned image and private persistent volume/config.
- [x] Create one independent Logto Traditional Web application through authenticated Management API, preserving existing apps/branding/users; transfer only its required secret to the new server service over SSH, never into source or desktop.
- [x] Upload current public server source, apply all migrations, separate owner/runtime permissions, register Easel client and publish exact live model/rate. Read provider business token only on server.
- [x] Start one owned server service and matching SSH forwards; connect 17868 with public preset and retain workspace/settings. Verify capabilities before asking user to log in.

### Task 4: User login and actual workbench acceptance

- [x] Open hosted sign-in for user entry, then check current scope/identity through sanitized APIs. Never retrieve or fill their password.
- [x] Preview and issue the approved allowance through owner CLI to that exact active session/wallet; repeat same key to verify no duplicate issuance, no payment rows.
- [x] Select central Pi and actual GLM model; test a finite real dialogue and file write/read through the workbench, compare settled receipts to gateway tokens and balance. Leave the working service available.
- [x] Regression/build/browser proof, independent final review, safe operational receipt and source/old-wallet preservation checks; record remaining production boundaries and user-facing test instructions.
