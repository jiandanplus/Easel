# Current Central Catalog Fix Candidate Implementation Plan

> **For agentic workers:** Use executing-plans for Root-only build/install/runtime operations and requesting-code-review for the resulting candidate. No production operation or merge.

**Goal:** Package the tested catalog-validation fix into a standard current 001–020 central wheel, verify all wheel contents, and exercise the installed artifact outside the checkout.

**Architecture:** Copy only the declared public build inputs into a fresh private stage. Pin the existing Hatchling1.32.4 backend through a hash-bearing build dependency lock, then use standard isolated PEP517. A fresh install prefix takes the candidate code while reusing the existing locked T12 production dependencies; report this distinction explicitly.

**Tech Stack:** Python3.12.7, uv0.11.16, Hatchling1.32.4, existing locked runtime/test dependencies, synthetic PostgreSQL16.

## Global Constraints

- Branch codex/ui-redesign; no commit/push/merge, no current workbench switch, no UI copy changes.
- No production DSN/key/user/wallet/provider/payment/timer/service action.
- Old wheel and validation material remain unchanged. New candidate directory only; no frozen016 repack in this task.
- Build input allowlist: services/platform/pyproject.toml; src/platform_service regular .py/.sql files and declared licenses; scripts/media_backup regular .py files; declared licenses; frontend/dist regular static files. No .venv/.env/output/test/private files in the wheel.
- Resolve only public build requirements from official PyPI using a fresh private cache and cleared package-manager environment; no source upload or interpreter download. Build offline after the dependency acquisition step.
- All wheel member names must be relative, unique, traversal-free and regular; CRC and every RECORD digest/size must match. Product .py/.sql, media_backup resources and declared force-includes must equal the copied source bytes.
- Installed validation must load platform_service and media_backup from the new prefix, never checkout/staged product source. Reusing locked dependency files is not a clean-machine/dependency-install claim.
- The synthetic PG fixture stays bound to original full CID7bed… and loopback63623. Each integration case uses new helper-owned databases/roles and a GID20 private directory; no Docker restart/create/delete.
- Fresh database/ACL restore, B3 production, full Linux/T13/T14/AV19, signing/notary remain separate requirements.

## Task 1: Build and inventory the candidate

**Files:** Create only `outputs/_run/central-catalog-candidate-20261103/` safe build input/inventory records and candidate wheel. Private staging uses a new directory under /private/tmp.

- [x] Materialize exact allowlisted regular input files with source SHA before/after; stage mode0700, GID20. Do not recursively copy the project or follow symlinks.
- [x] Write `build.in` as `hatchling==1.32.4`; resolve a complete hash-bearing dependency lock with uv pip compile. Use official index and a private cache; retain public dependency names/versions/hashes.
- [x] Build with local `uv build --wheel --force-pep517 --offline --no-python-downloads --no-sources --build-constraints <lock> --require-hashes --out-dir <new-output> <source-stage>`. Exact local CLI help is authoritative for uv0.11.16.
- [x] Validate ZIP member closure, CRC, RECORD and input identity. Compare with prior c23cb4… wheel; require an explicit complete member-diff list, not an assumed two-file delta.
- [x] Preserve candidate wheel hash/size and input inventory; do not label production-compatible.

## Task 2: Install and verify origins

- [x] Create a new Python3.12 venv; install the candidate offline with no dependencies. Add only the existing locked T12 dependency site-packages through a .pth; no source directory.
- [x] In isolated `-I -B` Python, verify module origins, all installed candidate RECORD members and twenty actual migration files. Reject any product origin in checkout or build source.
- [x] Copy only public test Python modules into a separate harness outside checkout. Run profiles/source_facts/bundles/self_verify with explicit asyncio configuration and test tools; first failure stops and private logs remain0600.
- [x] Run the original six integrated combinations from that external harness with new GID20 basetemp and exact private synthetic fixture. Preserve original timeouts and test assertions. Record source/test identity and before/after installed file hashes.

## Task 3: Review and handoff

- [x] Independent candidate review checks wheel inventory/RECORD, exact source differences, installed origins and bounded runtime records. No independent runtime claim from a manifest alone.
- [x] Publish a candidate report with a clickable artifact, SHA256, commands and remaining production/desktop gates; update total execution ledger without closing them.
- [x] Keep current workbench and all existing account/history/config data intact.
