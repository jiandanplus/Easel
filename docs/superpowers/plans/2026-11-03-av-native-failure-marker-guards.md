# Native capacity acceptance marker guards

> **For agentic workers:** Follow the existing subagent-driven-development review workflow. Root owns the resource-entry artifact, its pure guard tests and eventual concrete freeze; independent reviewer reads/hashes only.

**Goal:** Bind the new failure marker at both trusted launch acceptance layers before any future native case.

**Architecture:** Preserve both historical templates; create new Root-only copies. Add failure.json to the child publish guard. In the parent factor the existing private-record acceptance checks into records_guard, include failure.json, and call it both before accepted=True and after provisional result.json writing. The latter closes the inspected post-write gap: existing runtime/source/time checks alone do not detect changed private completion/markers. No new successful execution path, daemon call or authority is added.

**Scope:** New files only under outputs/_run/av-native-markers-20261103 plus validation/plan/ledger documentation. All current18 AV source/test files remain hash-identical. Existing loader5b3d.../parentd4ac.../old private cases remain immutable. No UI/config/history/account/wallet change, commit/push/merge or native execution in the source gate. Root alone may later perform separately frozen bounded native work. The approved next-step design was to update/freeze startup scripts before resuming actual validation.

## Task1: Root artifact refusal checks and pure tests

- [x] Freeze historical template hashes and protected18; copy baseline/new candidate script files before mutation.
- [x] Add pure tests extracting the actual loader guard and parent post-result if-accepted branch from AST, supplying synthetic dependencies. No import/execute of full scripts, socket/thread/timer/process/FFI/kernel/network/Docker. Guard positives still work; kill/abort/failure markers, changed done payload/pins refuse. Root post-test tripwires confirm no swallowed native calls. Preserve meaningful RED against old copies.
- [x] Make narrow script change only: child marker condition, parent records_guard extraction with marker plus post-result fresh records recheck. Retain original once intent, auth hashes, selected Python/runtime, original deadlines, environment, process limits, fixed errors and final parent exit requirement. Source-only guard tests are not full parent/native acceptance proof.
- [x] Fresh focused pure pytest, full exact two-script diff/pins/manifest, Root full source review and independent spec+quality review. No broader659 rerun because protected18 unchanged.

After this source gate, fresh readonly environment checks and exact private-stage/runtime/source/recipe/resource freeze remain necessary before one new case. Old unknown cases must not be replayed. Future status records remain rejected-case observations, never capacity proof or retry authority. Full goal remains unmodified.

2026-11-03 source gate CLOSED: Root17purepass and independent source PASS; protected18 unchanged. Concrete new current-revision freeze e5d4e7... separately materialization-reviewed; native case not yet run at this checkpoint.
