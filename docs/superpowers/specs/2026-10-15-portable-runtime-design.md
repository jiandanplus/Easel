# 第二阶段：macOS ARM64 内置运行时试验包

本阶段在 `codex/ui-redesign` 实现自定义服务、本地执行的可移动试验包。订阅认证、计费、云端执行继续使用第一阶段能力边界，不开放空实现。

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

## Architecture

`EASEL_WORKSPACE_ROOT` and `EASEL_RESOURCE_ROOT` provide explicit data/resource roots; existing source defaults and fixture monkeypatch points remain compatible. Backend code and web assets stay in app resources. Small skills/scripts/agents seed into the managed workspace using content hashes so scripts with relative state remain writable without touching the app. A `.venv` link exposes the private interpreter to existing skill instructions, with ownership checks.

A versioned runtime manifest identifies platform, architecture, Codex version, bounded relative payload paths and hashes. Command resolution uses an explicit external override in source mode, otherwise manifest-selected bundled executable, otherwise existing PATH lookup. Settings show origin and version. One shared provider preparation path supplies both inference and model discovery.

Bundled Codex uses an isolated workspace `CODEX_HOME` and an Easel-managed custom Responses provider. A private atomic JSON store contains base URL, model and key; GET returns only masked state. Settings expose this provider when the engine origin is bundled. Users can keep using existing external-engine authentication in source mode. Changing provider settings obeys the existing active-task guard. No automatic external fallback or global authentication import.

The launcher prebinds a loopback socket, creates managed workspace state, starts Uvicorn and opens the browser after startup. It records only its own instance and supports an authenticated stop operation. Concurrent launches reuse the existing live managed instance or fail clearly. A local stop helper is supplied. Logs and a useful startup error are available for Finder launches.

Build inputs are pinned and recorded, with license notices and SHA256 output. Core trial includes web UI, custom Responses configuration, native agent streaming, file/shell tools, and ordinary Python scripts. Media models, browsers, publishing integrations and OS tools are outside the trial guarantee and must be listed explicitly. The archive is unsigned; signing/notarization and independent-device acceptance remain later release work.

## Validation

Use temporary HOME and data root, sanitized PATH, and a relocated app path containing spaces/Chinese text. Validate actual packaged HTTP/static/settings/SSE flow with a loopback Responses stub driving native Codex and packaged Python to write/read a marker. Verify stop, restart, source/update preservation, secret masking, corrupt payload failure and resource privacy. Run relevant source/frontend regressions. Never infer real upstream or clean-device success from these tests.
