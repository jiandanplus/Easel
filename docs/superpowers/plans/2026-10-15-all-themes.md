# B–E Theme Implementation Plan

**Goal:** 五套主题均可在设置中立即切换并持久保存，A 仍是默认。

**Architecture:** Theme preference and before-paint bootstrap share finite-id normalization semantics. Existing page DOM and business callbacks remain stable. Scoped theme styles override semantic tokens and visual arrangement; assets are bundled through Vite.

## Global constraints

- Stay in codex/ui-redesign and preserve previous uncommitted A implementation and design files.
- Exact ids: paper, minimal, forest, pastel, swiss. Default paper; only forest uses dark color-scheme.
- Existing API/credentials/permissions/chat lifecycle remain unchanged. No fake business data or remote runtime font/image requirement.
- Approved visual references are local B–E images; theme changes must reach every existing page and state.
- Every shell command uses rtk. No commit, push or merge requested.

## Tasks

- [x] Extend theme behavior tests for B–E availability, selection, persistence, native color scheme and bootstrap parity (103 frontend tests pass; no claim of a recorded red run).
- [x] Implement five-theme availability/normalization/persistence/bootstrap, correct current/default theme labels and preview assets.
- [x] Delegate scoped B–E style implementation with precise file ownership; implement distinct layouts and complete light/dark surfaces.
- [x] Add local forest and pastel hero assets and theme-reactive workbench image mapping without remounting business pages.
- [x] Run complete frontend tests and build/lint; obtain independent review and resolve responsive/contrast findings.
- [x] Verify four themes via real browser: all main pages, settings, drawer, switching/reload and narrow layout; capture screenshots.
- [x] Restore A as selected default for delivery, complete whitespace checks and save final acceptance evidence.
