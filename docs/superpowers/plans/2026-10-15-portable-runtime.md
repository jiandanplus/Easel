# Portable runtime implementation plan

Read alongside `docs/superpowers/specs/2026-10-15-portable-runtime-design.md`. Execute on current UI branch with test-first changes and task reviews.

## Global Constraints

- Keep changes on `codex/ui-redesign`; do not merge, push, or modify the twelve pre-existing appearance files recorded in `.git/sdd/preexisting-ui.json`.
- No upstream inference, login, or developer credential reuse. All runtime integration uses a loopback dummy model server and temporary HOME/workspaces.
- Ship private relocatable CPython, pinned core Python dependencies, a native Codex executable and its resources, and a prebuilt frontend. No developer venv, global Node, Python or engine installation is required for the declared core trial.
- Separate immutable program resources from writable workspace data. Default packaged workspace is `~/Library/Application Support/Easel/workspace`; explicit override is available for testing. Source checkout defaults remain unchanged.
- Never bundle profiles, outputs, .env, credentials, token caches, browser sessions, .git, node_modules, or unrelated generated artifacts. Resource selection is an allowlist from tracked project resources, with credential/cache exclusions.
- Preserve existing and user-modified workspace seed files, settings, profiles, and outputs during restart/update. Never silently overwrite malformed configuration or an unrelated `.venv`.
- Bundled runtime resolution is deterministic and fails clearly for incompatible, missing or corrupt payloads. Do not silently switch to a globally installed engine when a bundled manifest is present.
- Bind the packaged server to loopback, isolate bundled Codex home, mask provider secrets in responses, and never pass API keys on the command line.
- Claims distinguish local sanitized-environment verification, independent clean-device verification, and real upstream verification. The latter two remain outstanding until actually performed.

### Task 1: Workspace roots and bundled engine resolution

Create `easel/paths.py` with `workspace_root()` honoring `EASEL_WORKSPACE_ROOT` and `resource_root()` honoring `EASEL_RESOURCE_ROOT`, each defaulting to source checkout root. Convert backend/CLI roots in web/app.py, easel/cli.py, direct_api.py, persona.py, commands/{doctor,gateway,skill,ping}.py and ACP discovery. Preserve module PROJECT_ROOT monkeypatch points. Resource fallback in harness workspace instructions uses resource root. Do not rewrite shared skill scripts; task 3 copies these.

Create a runtime resolver module with versioned JSON manifest via `EASEL_RUNTIME_MANIFEST`. Define/document exact schema shared with task 3: schema version, platform (`darwin`), architecture (`arm64`), codex version, relative executable path and sha256; support hashes of all platform payload files. Resolve all payload paths below manifest.parent with no absolute/traversal/symlink escape, verify declared platform/arch/executable permission and hashes with a stats-based cache. Missing/incompatible/corrupt manifest or payload raises HarnessError without PATH fallback. Source mode may explicitly override via absolute `EASEL_CODEX_EXECUTABLE`; invalid override fails, no fallback. Keep existing PATH/npm Windows behavior when no bundle/override. `base_command`, actual Codex adapter and model discovery already share base_command; preserve that relationship. `describe` includes origin/version and actionable resolution error without leaking secrets; preserve mock compatibility as reasonable. Packaged Codex must use workspace-isolated CODEX_HOME in actual model and task subprocesses (shared helper/env seam for task 2). Tests use temp manifest/fake executable and deny real network/process engines.

Test first: source defaults, every explicit workspace path, resource fallback, external/PATH behavior, explicit override validation, compatible bundle, corrupt/missing/incompatible/traversal manifests, shared model/task resolution and private home. Run covering tests plus existing runtime/harness/path/settings tests as needed. Commit only assigned files; report RED/GREEN commands, exact results and interfaces in `.git/sdd/phase2-task-1-report.md`.

### Task 2: Bundled custom provider and settings

Implement a private atomic workspace provider store with base URL, model, API key. Validate http(s) URL, model and controls; accept explicit loopback HTTP for local gateway tests. Store 0600, never log/return raw key or put it in argv. GET returns configured state, base URL/model and key-present flag only; support preserve/replace/clear secret semantics deliberately. Shared Codex command/env preparation supplies isolated CODEX_HOME and custom Responses provider to actual tasks and model discovery; requires_openai_auth=false and key via dedicated environment variable. Bundled unconfigured execution gives clear configuration instruction and never borrows global native auth. Native external behavior stays compatible. Configured model is the truthful bundled model choice and TaskContext uses it consistently; no false global native catalog. Explicit unsupported model override must fail clearly or be resolved consistently.

Add GET/POST provider API and existing active-task mutation guard. Add origin/version API typing, a provider UI in new `BundledProviderSettings.tsx` rendered from `HarnessSettings.tsx` for bundled origin; api.ts can change. Do not edit the twelve pre-existing appearance files. Show base URL, model, masked password input, save result/errors and bundled origin/version. Existing source/subscription capability gating stays truthful.

Test first: temp roots, masking/permissions/atomic failure, invalid values, secret lifecycle, model/task command-env consistency, no global credential reads, task guard, external compatibility. No real provider traffic. Run backend covering tests, frontend tests/lint/build. Commit assigned changes and report to `.git/sdd/phase2-task-2-report.md`.

### Task 3: Portable launcher, build and local artifact validation

Implement managed seed/update/bootstrap and loopback launcher with own-instance stop, default managed workspace, explicit test overrides, safe `.venv` link ownership, browser-after-ready and actionable logs/errors. App resources stay immutable; source entry points remain supported. Copies of skills, scripts and agents use tracked allowlist with mutable credential/cache exclusions. Updates replace only unchanged prior seeded files. No user-data deletion or malformed config replacement. Isolate Codex home.

Create macOS ARM64 build tool with pinned standalone CPython and pinned core dependencies, copied native Codex version/platform resources, prebuilt frontend, licenses and provenance manifest. No Node or uv needed on target. Use ignored output staging, avoid copying source venv. Disk budget currently ~1.8GiB: one stage, no large redundant copies. Build archive with executable bits/symlinks preserved and validate contents. Record dirty appearance input hashes without staging those files. Include clear trial support matrix and startup/stop/data location documentation.

Add offline tests and packaged validation script using loopback dummy Responses and actual native engine/packaged Python. Validate relocated path, temp HOME/data, sanitized PATH, HTTP/static/settings/SSE, marker tool/Python execution, masking, own stop/restart, data preservation and bounded payload integrity/privacy. Record exact commands, hashes, sizes and limitations in `docs/validation/2026-10-15-portable-runtime.md`. Run source/frontend regressions after final code changes and obtain task plus broad final review. Commit code/tests/docs only; leave artifact local and no push/merge. Report `.git/sdd/phase2-task-3-report.md`.
