# A 纸感编辑室 Implementation Plan

> **For agentic workers:** Use subagent-driven-development for the isolated theme preference task, then integrate shell/styles and review the complete change.

**Goal:** A 为默认主题，提供设置入口与完整 A 版真实应用预览。

**Architecture:** Theme preference module and appearance settings are independent of business APIs. Shared semantic CSS tokens/style layer restyle existing pages. Sidebar and workbench receive layout updates without changing chat/publishing backend behavior.

**Tech Stack:** Existing React 19, TypeScript 6, Vite 8, CSS, Node test runner.

## Global Constraints

- Work in current codex/ui-redesign checkout, preserving untracked design assets.
- Implement only A; B–E disabled and labelled 待设计.
- Default A even with previous light/dark preferences. No external runtime dependency for fonts or images.
- Keep real data and business controls; do not seed fake accounts, outputs or scheduled posts.
- All shell commands use rtk prefix. Do not read or expose .env credentials.

### Task 1: Theme preference and settings

- [x] Write failing Node tests for preference initialization, migration, invalid values, unavailable storage, persistence and disabled theme protection.
- [x] Implement web/frontend/src/lib/theme.ts and components/ThemeSettings.tsx.
- [x] Integrate a new theme settings section in SettingsPanel.tsx. Remove independent light/dark toggle path. Apply A before paint in index.html.
- [x] Run targeted tests and build. Record exact commands/results in outputs/_run/theme-settings-report.md.

### Task 2: Shared A shell and styling

- [x] Read A reference pages. Add paper.css with semantic tokens, readable typography, component surfaces, sidebar, tool pages, settings and responsive behavior.
- [x] Update Sidebar.tsx to grouped full navigation, active settings state and compact history shown only for chat.
- [x] Update App.tsx with a shared topbar, portrait chooser, theme shortcut and main-area settings. Preserve chat lifecycle.
- [x] Update DashboardPage.tsx layout and functional creation entry. Add local landscape image asset and genuine empty states.

### Task 3: Verify and deliver

- [x] Run Node frontend tests, build, lint and diff whitespace check.
- [x] Open actual local app, exercise navigation, settings appearance, model panes and reload. Review desktop and narrow viewport screenshots.
- [x] Independent review of feature correctness, scope and accessibility; fix material issues.
- [x] Save verification screenshots, update plan checkboxes and report URL/file evidence. Do not push or merge.

## Acceptance evidence

2026-10-15: 96 Node frontend tests pass; build and lint exit 0; git diff --check passes. Existing lint escape warning and >500 kB bundle advisory retained. Actual browser navigation covered all 11 main pages, six model channels, theme/environment/more, onboarding and skill drawer. Library search filters real current-folder nodes. Settings round trip retained an unsent composer draft; repeated appearance shortcut returned to theme panel; narrow viewport history opens, Escape closes and restores focus. Screenshots and QA report: outputs/_run/paper-ui/. Work remains on codex/ui-redesign for user review, no push or merge.
