# Phase 1 engine compatibility evidence — 2026-10-15

## Decision

Codex **0.146.0 is the provisional built-in engine candidate** for the next phase. Its local app-server and custom-provider/tool protocol have passed the loopback probe; final runtime acceptance and a production subscription gateway have not passed end-to-end validation. Native account recognition and model listing pass, but the upstream marker tasks failed as recorded below. This does not establish that other engines failed or that the cached account is invalid.

The user explicitly chose to complete the other checks and defer real-model verification after HTTP 401 was observed on 2026-10-15. No further upstream model or login attempts are part of this delivery. Native inference, active-shell cancellation, production gateway compatibility and clean-machine packaging remain separate acceptance items.

## Tested installation

- Host: macOS ARM64. Executable found at `/Users/jiandan/.nvm/versions/node/v24.14.0/bin/codex`.
- Resolved npm entry: `/Users/jiandan/.nvm/versions/node/v24.14.0/lib/node_modules/@openai/codex/bin/codex.js`. Package `@openai/codex` and CLI both report `0.146.0`; package metadata declares `Apache-2.0`.
- Native executable under that package: `node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex`; direct `--version` also reports `codex-cli 0.146.0`.
- Sibling platform resources present: `bin/codex-code-mode-host`, `codex-path/rg`, `codex-resources/zsh/bin/zsh`, `codex-package.json`. The installed JavaScript wrapper resolves a platform-specific optional dependency and forwards signals to the native process. A distribution must include the selected platform resources and license notices and validate them in a clean environment; copying the npm wrapper alone is insufficient.
- `opencode` and `openclaw` were absent from PATH. No installs or validation runs were performed for them.

## Live evidence

| Surface | Result | Limit |
| --- | --- | --- |
| `codex login status` | Exit 0; classified ChatGPT login | Raw output and credentials were neither persisted nor printed; this does not prove a successful model request |
| app-server `initialize` / `initialized` | Passed | Local protocol handshake only |
| `account/read` with `refreshToken: false` | Account type `chatgpt`; `requiresOpenaiAuth: true` | No token refresh was requested or tested |
| `model/list` | Returned 5 models | Catalog availability does not prove inference availability |
| Real marker task 1 | Native thread ID bound; failed in 42.45 s | No token, tool activity, permissions request, or marker file |
| Real marker task 2 | Native thread ID bound; `turn/started`; failed in 41.85 s | 10 error notifications classified as stream disconnected/retry; no tool or assistant output |
| Real marker task 3, explicit per-process proxy | Native thread bound; turn started; four stream-disconnection error notifications classified as HTTP 401; turn/completed notification also observed; outer timeout at 90.03 s | No token/tool/marker. The adapter did not resolve completion before timeout despite terminal-looking notifications; exact upstream shape was not captured. No further real tasks. |
| Owned metadata app-server process cleanup | Confirmed process exited when RPC context closed | Active tool cancellation and descendant termination were not exercised |

Task 2 observed notification counts: `remoteControl/status/changed=1`, `mcpServer/startupStatus/updated=29`, `thread/settings/updated=1`, `warning=3`, `thread/status/changed=2`, `turn/started=1`, `item/started=1`, `item/completed=1`, `error=10`. None of the ten error classifications contained `unauthorized`, `401`, or `403`. This does not rule out an authentication/configuration issue hidden behind a connection failure. The adapter returned its generic sanitized HarnessError. Raw upstream payloads, error detail, configured endpoint addresses, model identity, account identity, and credential material were not logged.

The first two runs inherited no HTTP(S) proxy variables. After the parent identified that current network access required an existing loopback HTTP proxy, the third task set HTTP_PROXY/HTTPS_PROXY and their lowercase equivalents only in the child environment, with localhost/loopback NO_PROXY exceptions. No global routing or config changed. This changed the observed failure category to HTTP 401 and did not restore inference. HTTP 401 requires native login/provider investigation; recognized cached login is not successful authenticated inference.

The inherited native configuration initialized configured MCP startup notifications. No MCP tool invocation or command execution was observed. This test used existing native configuration without modifying it; it is not a clean packaged-runtime test.

## Nonbillable custom-provider acceptance probe

`rtk proxy .venv/bin/python scripts/validation/verify_codex_gateway.py` passed on installed Codex 0.146.0. `--help`, `--codex PATH`, and `--scenario tool|error|cancel|all` are supported. The probe creates and removes isolated temporary CODEX_HOME/workspace directories, uses only a dummy token and loopback HTTP server, rejects additional permission requests, asserts expected outcomes, and exits nonzero on failure. It makes no upstream model calls and reads no native auth file.

| Scenario | Observed and asserted result |
| --- | --- |
| Tool/stream | Observed two POST `/v1/responses` requests. Asserts correct dummy Authorization bearer, `stream=true`, actual shell-tool write/read, function_call_output containing the marker, the matching marker file and assistant streamed marker, Easel activity/token events, and owned app-server exit. |
| HTTP 401 | Stub returns dummy 401 invalid_api_key; observed six requests including retries. Asserts HarnessError, no marker and owned process exit. No refresh credential exists, so this does not validate token refresh. |
| Cancel | Cancels only the owned pending turn after a Responses request arrives; asserts scoped `turn/interrupt` acknowledgement, propagated cancellation, no marker and owned process exit. This exercises waiting-model cancellation, not interrupting an active shell child. |

A cancellation cleanup race was exposed by the initial probe: a late interrupted completion could produce `Future exception was never retrieved` after cleanup had already checked a still-pending future. Commit `2a2715b` fixes this by finalizing a pending completion before RPC context exit, while retaining exception retrieval for an already-set future. Deterministic cancellation and reader-failure regressions failed before the fix and passed afterward; the focused cleanup/harness suite passed 106 tests.

The final parent run passed all three loopback scenarios with `unhandled_asyncio_errors=0` and owned-process exit. The probe temporarily installs a loop exception handler that counts errors without retaining or printing request payloads; any recorded error fails the probe. It drops the task reference, runs garbage collection and gives the loop a cleanup tick before restoring the original handler. Thus expected interrupted-completion errors are not silently treated as a pass. The earlier real upstream terminal-event timeout remains unresolved; this local cleanup fix is not evidence that the upstream timeout is fixed.

The loopback test establishes installed-engine protocol mechanics, custom env-key bearer support without native auth, a real local tool cycle, streaming, fail-closed errors, and prompt-scoped waiting-model cancellation. Exact request counts are observations, not version-independent assertions. It does not establish production gateway identity, payment/entitlements, account refresh, hosted routing, usage accounting, context limits, or billing compatibility. The stub emits usage fields but the probe does not verify their mapping into an Easel billing ledger.

Isolation here means a temporary CODEX_HOME/workspace and an owned child process with a loopback model endpoint; it does not assert that the native process cannot access all other host files or that no telemetry/network behavior exists outside the observed model requests. A packaged runtime still requires explicit capability and host-isolation validation.

## Exact marker scope and reproduction

The ignored local evidence and runnable probes are under `outputs/_run/phase1-engine-validation/`:

- `real_smoke.py`, `real-smoke-result.json`: first task.
- `real_smoke_observed.py`, `real-smoke-second-result.json`: second task with sanitized protocol method counts and boolean error categories.
- `real_smoke_proxy.py`, `real-smoke-proxy-result.json`: third task with explicitly authorized per-process proxy settings.
- `metadata_probe.py`, `metadata-result.json`: nonbillable protocol/account/catalog probe.
- `marker-*` directories: isolated workspaces; none of the three upstream tasks produced the expected marker.

From the repository root, the exact commands used were:

```sh
rtk proxy .venv/bin/python outputs/_run/phase1-engine-validation/real_smoke.py
rtk proxy .venv/bin/python outputs/_run/phase1-engine-validation/real_smoke_observed.py
rtk proxy .venv/bin/python outputs/_run/phase1-engine-validation/real_smoke_proxy.py
rtk proxy .venv/bin/python outputs/_run/phase1-engine-validation/metadata_probe.py
```

The first three commands each initiate one real model task and can consume usage. Do not replay them automatically. Scripts are local ignored evidence rather than distributed production files. All three use the exact PATH-resolved Codex command override plus `app-server`, existing environment/native auth, `sandbox=workspace-write`, and a 90-second `asyncio.wait_for` boundary. Each creates a separate temporary directory beneath the allowed evidence directory. Prompt: write only `marker.txt` with `EASEL_ENGINE_SMOKE_20261015` plus newline using a shell tool, read it using a shell tool, return the marker, and inspect no other files/configuration/credentials/network. All additional permission requests are rejected. No session pool key is used, so each RPC context owns and closes only its own process group.

The scripts intentionally do not read or copy auth/config files. Native Codex can update its own session history as part of normal operation. No global configuration, repository branch, UI file, or unrelated process was changed.

## Adapter inspection and remaining acceptance work

Reviewed `easel/harnesses/{codex,rpc,base,workspace,commands,models}.py` and the subscription/execution design spec. The validation initially added only probes and evidence; its cancellation finding then led to the separately tested production cleanup fix in commit `2a2715b`.

- Protocol: existing Codex adapter initializes app-server, starts/resumes a native thread, starts a turn, buffers early notifications, and scopes events to native thread/turn IDs. Actual upstream start succeeded; completion did not. Loopback stub turn completion passed.
- Authentication: credentials remain native. Current recognized ChatGPT account is separate from future Easel task-scoped bearer credentials. Authentication refresh and expiry behavior remain untested.
- Tools/streaming: code maps command/file/activity events and assistant/reasoning deltas, and the loopback stub exercised actual shell write/read plus streamed final marker. Native upstream tasks did not reach those steps.
- Permissions: code uses on-request approvals, restricts accepted sandbox modes, and provides a workspaceWrite policy with writableRoots limited to request.cwd and networkAccess false. No real task requested approval, so live allow/deny behavior was not exercised. This evidence does not establish host-level isolation.
- Cancellation: code catches adapter cancellation and sends `turn/interrupt` with thread/turn IDs before RPC process-group cleanup; no global Codex kill is used. Owned metadata process exit and loopback waiting-model turn interrupt/process exit were observed. Completion-future cleanup is fixed and covered as described above. Active shell-tool descendant interruption remains untested.
- Workspace: the real task root was temporary. Shared developer instructions fall back to Easel bundled workspace documents while interpolating the temporary root. No project scripts or profiles were used.
- Packaging: only the installed macOS ARM64 candidate was inspected. No app bundle, Windows/Linux binary, code signing, upgrade, or development-tool-free machine was tested.
- Subscription/custom gateway: no real Easel gateway exists in this evidence. Responses protocol, task-scoped bearer token, expired token/refresh, error mapping, tool-result cycle, streaming response and usage events, context limits, and cancellation must be validated against the actual gateway. The nonbillable loopback probe passed dummy bearer forwarding, Responses tool-result exchange, streamed assistant output, 401 rejection and waiting-model cancellation. It did not validate hosted identity, refreshed/task-scoped credentials, usage accounting or context limits; hosted gateway acceptance is still required.

Deferred acceptance, at the user's direction: inspect native login/provider authorization through the normal native login flow, resolve the HTTP 401 and investigate the adapter terminal-event timeout without weakening permissions, then repeat one owned marker task and one owned active-shell-cancellation case. Independently validate the future gateway and the pinned packaged runtime before making either capability available to users. This report does not authorize unattended real-model retries.
