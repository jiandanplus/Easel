# Central Production Preparation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Deliver an offline-verifiable production preparation bundle without changing the existing preview or any user identity, wallet or custom provider.

**Architecture:** Preserve the current independent API and Console and bind both to host loopback under separate systemd users. A strict public manifest drives deterministic templates and source-to-wheel verification; a separate runbook gates Logto relocation, identity migration, database recovery and real deployment.

**Tech Stack:** Python 3.12 stdlib, pytest, existing wheel build, systemd and Nginx deployment templates.

## Global Constraints

- Branch `codex/ui-redesign`; no merge, automatic commit or push.
- No DNS, certificate issuance, service installation/reload/restart, private config read, model call, payment or real database write.
- Existing API, Console and Logto authentication/permissions are not weakened or rewritten.
- Generated report always says offline-only and production_ready=false.
- No real credentials in inputs, outputs, templates, logs, command arguments or reports.
- Preserve source issuer warning; no email identity merge or balance mutation.

---

### Task 1: Offline contract, wheel verification and deployment templates

**Files:**
- Create `services/platform/scripts/prepare_deployment.py`.
- Create `services/platform/tests/test_deployment_preparation.py`.
- Create `services/platform/deploy/production.example.json`.

**Interfaces:**
- CLI: `python services/platform/scripts/prepare_deployment.py --config PUBLIC_JSON --wheel WHEEL --output NEW_DIRECTORY`.
- Contract keys exactly: version, public_url, console_public_url, oidc_issuer, source_oidc_issuer, api_port, console_port, release_dir, config_dir, certificate_dir.
- Output: `deployment-report.json`, `nginx-central.conf`, `easel-central-production.service`, `easel-console-production.service`, `central.env.template`, `console.env.template`.
- Report: version=1, verification="offline_only", production_ready=false, identity_migration_required boolean, wheel_sha256, source file SHA inventory and explicit outstanding checks.

- [x] Write failing tests using synthetic wheel/fixture source roots: valid generated Host/SSE/loopback/nonroot settings; invalid/injected URL/path/port/extra/duplicate JSON keys; wheel source/migration/static mismatch; no overwrite; secret-free fixed errors; issuer mismatch remains blocking.
- [x] Run `rtk proxy services/platform/.venv/bin/python -m pytest services/platform/tests/test_deployment_preparation.py -q`, verify the missing module failure.
- [x] Implement stdlib dataclass validation, bounded JSON/ZIP inspection, deterministic safe templates and exclusive directory/file creation. Use no subprocess/network/database calls. Do not extract the wheel. Public origins use distinct DNS hosts and HTTPS 443 only; source issuer accepts exact HTTPS or literal-loopback HTTP /oidc. Validate wheel Python/SQL/static bytes and file sets against repo source and dist.
- [x] Repeat the command, verify all expected cases pass, then independently review exact output and failure behavior.

### Task 2: Release build and production cutover runbook

**Files:**
- Create `services/platform/deploy/README.md`.
- Update the introduction of `services/platform/README.md` to point to latest actual text/Pi/Console acceptance and the new deployment preparation guide.
- Create `docs/validation/2026-10-23-central-production-preparation.md`.

**Interfaces:**
- Consumes Task 1 CLI and filenames verbatim.
- Produces operator steps for version pinning, offline bundle creation, private env ownership, Logto deployment, identity/pending gating, role checks, actual restore rehearsal, no-consumption smoke and code-only rollback.

- [x] Build Console with the existing build script, build a new platform wheel using locked tooling, and run Task 1 against a synthetic HTTPS deployment config in a private temporary directory. These origins are test fixtures, not claims of public DNS.
- [x] Install the new wheel with locked dependencies into a new owned temporary venv, import and construct API/Console apps using synthetic credentials and no lifespan/network/DB startup. Record exact command and exit status. If dependency installation cannot be completed, report that boundary instead of claiming clean installation.
- [x] Write the runbook with explicit public/private topology and issuer migration gates. Include these deployed checks: canonical Host, anonymous API/Console 401, cross-origin rejection, Logto discovery exact issuer, full static manifest, database role guard, current migration checksums, payments disabled and media/cloud unavailable. These checks must not call providers or create payments.
- [x] Add fresh validation evidence, outstanding deployment inputs, and precise non-executed stages. Review no secrets/placeholders disguised as real deployment values.

### Task 3: Independent review and closeout

**Files:**
- Create `docs/validation/2026-10-23-central-production-preparation-review.md`.

- [x] Review only this batch's files and generated bundle against the spec; verify contracts and no automatic external actions.
- [x] Resolve Important/Critical issues and rerun covering tests; verify all changed-file syntax and whitespace.
- [x] Record source/branch preservation and distinguish offline preparation from real DNS/TLS, identity migration, recovery and production acceptance. Leave the final deployment question tied to actual missing domains/configuration rather than asking the user to reauthorize already approved development.
