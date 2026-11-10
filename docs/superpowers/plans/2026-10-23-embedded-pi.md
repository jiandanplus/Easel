# Embedded Pi Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a macOS ARM64 Easel application with default Pi, bundled Node/CPython, custom and central subscription modes, verified real tools/model usage/credits.

**Architecture:** An owned Node worker embeds Pi agent-core and pi-ai; Python owns the tool and credential boundaries. Existing central Responses authorization, operation IDs and ledger remain authoritative. Runtime inventories and a current public-source allowlist define the portable app.

**Tech Stack:** Python 3.12, Pi agent-core/pi-ai 1.4.0, official pinned Node >=22.19.0, React/Vite, existing PostgreSQL central service.

## Global Constraints

- Current `codex/ui-redesign`; no main merge, commits, pushes or destructive checkout changes during development.
- Preserve root `.env`, old explicit settings, old sessions, existing wallets and six SQL migrations.
- Pi supports custom Responses and central subscription; legacy subscription remains Codex-compatible.
- SDK maxRetries 0, at most 8 model requests and 16 tools per turn; bounded time/bytes/history, cancel owned children.
- Server business keys stay on server; no credentials in source, browser, argv, logs, artifacts or ordinary shell tool env.
- Pi files are workspace scoped; read-only forbids writes. Shell needs explicit in-product approval and an enforced OS sandbox; unsupported shells fail closed.
- Actual target is macOS ARM64. Package and real-provider proof are separate; no cross-platform, notarization or independent-device claims without evidence.
- Test fixtures are noncommercial and explicitly identified; never mint a fake paid order or zero unknown usage.

Baseline: `/private/tmp/easel-pi-20261023/baseline.json`; progress ledger: `/private/tmp/easel-pi-20261023/progress.md`.

### Task 1: Pi SDK worker, runtime resolver and controlled tools

**Files:** Create `runtime/pi/package.json`, `runtime/pi/package-lock.json`, `runtime/pi/worker.mjs`, `easel/harnesses/pi.py`, `easel/harnesses/pi_runtime.py`, `easel/harnesses/pi_tools.py`, `easel/harnesses/pi_session.py`, and `tests/test_pi_*.py`.

**Interfaces:**

```python
@dataclass(frozen=True)
class PiResolution:
    node: Path
    worker: Path
    origin: str
    version: str

def resolve_pi() -> PiResolution: ...
def pi_command() -> list[str]: ...

class PiAdapter:
    descriptor = Descriptor('pi', 'Easel 内置智能体 · Pi', 'embedded SDK', '随完整安装包内置，无需另装 Agent')
    async def run(self, request: RunRequest, emit, ask, bind) -> None: ...

async def execute_tool(request: RunRequest, name: str, arguments: dict, ask) -> dict: ...
```

Worker protocol: first JSONL `{type:'run', id, session_id, system_prompt, prompt, messages, model:{id,baseUrl,maxTokens,contextWindow}, api_key, tools_enabled}`. Output `{type:'token'|'thinking'|'activity',text}`, `{type:'tool_request',id,name,arguments}`, `{type:'done',session_id,messages,success}` or sanitized `{type:'error',code}`. Parent returns `{type:'tool_result',id,result:{content:[{type:'text',text}],details}}`. EOF/cancel aborts.

- [x] Write tests for missing/wrong-platform/tampered runtime, LF framing and early token before completion, tool request/results, provider error vs resolved promise, resume ID validation, cancellation cleanup, missing terminal, protected paths, read-only writes, shell deny/approval/sandbox and env sanitization.

```python
assert not (await execute_tool(readonly_request, 'write_file', {'path':'x','content':'x'}, deny))['ok']
assert not (await execute_tool(request, 'read_file', {'path':'outputs/_harness/bundled-provider.json'}, deny))['ok']
# A gated fixture emits a token while terminal is withheld; task stays pending.
assert observed_token and not run_task.done()
```

- [x] Run focused tests and confirm expected feature failures before implementation.

Run: `rtk proxy .venv/bin/python -m pytest tests/test_pi_runtime.py tests/test_pi_tools.py tests/test_pi_adapter.py -q --tb=short` (loopback/OS process cases outside sandbox).

- [x] Implement SDK worker with official 1.4.0 Agent and Responses stream, strict model settings, `maxRetries:0`, safe error checks and Python-bridged tools; load no user Pi resources. Implement runtime full hash verification and isolated 0600 session history. For central mode reuse `CentralClient`/`ExecutionProxy`, same scope and invocation, catalog output ceiling.
- [x] Run the focused tests GREEN, inspect clean stderr and self-review. Produce task report and before/after diff package; independent review covers spec and quality before completion.

### Task 2: Default engine and both product modes

**Files:** `easel/harnesses/capabilities.py` (new); modify harness registry/store/models/provider, `easel/service_contracts.py`, `easel/installation.py`, `web/app.py`, `web/frontend/src/components/HarnessSettings.tsx`, `BundledProviderSettings.tsx`, `web/frontend/src/lib/api.ts`; tests in `tests/test_pi_integration.py` and focused existing frontend suites.

**Interfaces:**

```python
def supports_subscription(runtime: str, backend: str) -> bool:
    return runtime == 'codex' or (runtime == 'pi' and backend == 'central')

# describe() adds subscription_backends ['central'] for Pi;
# Codex keeps ['central','legacy']; others [].
```

- [x] Add failing tests: fresh workspace default Pi; explicit config/env and old OpenClaw resolution retained; central Pi settings accepted, legacy Pi rejected; Pi model list uses central catalog; custom provider status never returns key; busy tasks block account/provider changes; old Codex session runtime cannot change.

```python
assert settings(tmp_path/'outputs', {})['runtime'] == 'pi'
assert supports_subscription('pi','central')
assert not supports_subscription('pi','legacy')
```

- [x] Implement capability-based gates consistently in backend acceptance/readiness and frontend filtering/save state. Reuse existing private provider file without moving keys. Show Pi as built-in; retain external engine advanced choices and explain source/permissions in product language. New default never overwrites explicit existing selections.
- [x] Run Python integration and relevant old contract/provider/native suites plus frontend tests/build. Update assertions only where intended behavior changes. Independent task review uses delta from this turn baseline.

### Task 3: Deterministic runtime preparation and macOS app

**Files:** Create `scripts/packaging/prepare_pi_runtime.py`, `scripts/packaging/build_pi_macos.py`, `packaging/pi/README.md`, public lock/provenance and licenses; modify `.gitignore`, `easel/portable.py`, `web/frontend/portable-assets.ts`; add `tests/test_pi_packaging.py` and update portable tests.

**Interfaces:**

```bash
rtk proxy .venv/bin/python scripts/packaging/prepare_pi_runtime.py --output /ABS/OWNED/PI/PAYLOAD
rtk proxy .venv/bin/python scripts/packaging/build_pi_macos.py --inputs /ABS/PREPARED/PYTHON --pi-root /ABS/OWNED/PI/PAYLOAD --output /ABS/NEW/DELIVERY
```

Manifest: schema_version 2, engine `pi`, platform and architecture, `pi.version`, `node.version`, relative worker/node paths and exact `files` map. Source worker and npm lock must match authority; Node archive digest must match official SHASUMS. Package includes licenses, runtime/source/build inventories; no `node_modules` import from arbitrary source trees.

- [x] Write failing tests for pinned runtime inputs, no implicit download at application launch, missing SDK/Node rejection, untracked central/Pi source inclusion, stale frontend receipt rejection, secret/source exclusion, Pi default and old configuration preservation.
- [x] Prepare pinned public runtime inputs with npm lifecycle disabled; verify authoritative CPython payload/wheels without claiming unavailable archive proof. Build from current audited public files and frontend receipt; include Node/Pi/Python, launcher and stop action.
- [x] Build actual fresh unsigned `Easel.app` and archive. Launch in temporary HOME, `PATH=/usr/bin:/bin`, no external Agent config, with owned workspace/port. Verify private Node/Python executable paths and bundled real tools, stop only own instance. Record artifact SHA and installed-runtime proof separately from signing/device coverage. Independent review.

### Task 4: Real model, tools and credits acceptance

**Files:** Extend/create controlled validation scripts under `scripts/validation`, add `docs/validation/2026-10-23-embedded-pi*` receipts/review and operational guide.

- [x] Use server-only business credential access already authorized. Create fresh isolated schema/membership/noncommercial live credit with existing reviewed runner seed path; preserve old pending operation and production state. Bind a verified model and finite acceptance token ceiling.
- [x] Through actual packaged Pi run a synthetic task: write/read a marker via a real local tool, return final marker, observe text/tool events, and compare each central receipt to New API request ID and token usage. No forced or fabricated assistant/tool responses in this live path.
- [x] Verify custom mode with actual Pi against a server-side provider proxy so business key remains server-only; custom call does not debit central wallet. Disable automatic retries in the Easel-controlled SDK, client and fixture transport; gateway/provider internal retry behavior is not asserted.
- [x] Query/replay original central operation identity, confirm no new provider dispatch or debit. Cancel a separate finite operation if needed, preserve unknown usage, verify status recovery. Zero payment rows and exact noncommercial rates must be recorded.
- [x] Fresh appropriate regression suites, frontend build/browser visual check, exact-source review, preservation of baseline269 paths outside explicit changes, `.env`/HEAD/main/six migrations unchanged. Archive audit evidence and clean only owned temporary processes/credentials. Final report links runnable package and precise remaining constraints; no merge.

## Verification closeout

Completed on 2026-10-23 with final v3 packaged runtime, GLM real text/tools, noncommercial credit ledger/replay/cancellation proof, frontend/browser checks and protected-state comparison. See `docs/validation/2026-10-23-embedded-pi.md` and its safe receipts for exact evidence and exclusions. Server cleanup ran only after the user explicitly approved its precise scope; all uncertain reservations remain held. Production OIDC/payment rollout, independent-device acceptance, signing/notarization, cross-platform packaging and optional media tool runtime delivery remain future work.
