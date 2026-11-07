> 流式／UI 任务源码审查 PASS；浏览器实测另见主验收记录。

# S3-B Task 1 + Task 3 independent review

Verdict: **PASS for the scoped Task 1/Task 3 code review after fixes** (2026-10-23).

The initial verdict was CHANGES REQUIRED; the original findings and reproduction are retained below for traceability. Both are now closed by independent focused verification. This does not assert Task 2, browser visual, whole-stage, or real-provider acceptance.

Scope: task deltas against `/private/tmp/easel-s3b-before`, current `easel/platform/execution_proxy.py`, the entire current new stream test, frontend recovery component/API integration and seven frontend tests. Read the S3-B plan Global Constraints and Tasks 1/3 and both implementation reports. Branch verified `codex/ui-redesign`. The 246 preexisting dirty paths are outside this review. No source edits, commits, environment changes, preview changes, live provider calls, or broad test reruns were performed.

## Actionable finding

### [P2] Track committed headers for every local JSON/error reply

- File: `/Users/jiandan/Code/GitCode/Easel/easel/platform/execution_proxy.py:187` (reply write/drain), `:281` (404 branch; also 403/429/credential failure), `:327` (fallback decision).
- `_reply()` writes a complete HTTP response before awaiting the newly bounded drain, but only the successful upstream JSON path sets `state["committed"]`. If a rejection reply's drain fails or times out, `_handle()` still sees `committed=False` and appends a second HTTP response. This violates the plan's explicit single-response-after-commit constraint and makes a slow/failed client receive malformed HTTP.
- Bounded no-network reproduction executed successfully with `rtk proxy .venv/bin/python`: instantiate the proxy without entering it; feed a StreamReader `GET /responses HTTP/1.1\r\nHost: 127.0.0.1:1\r\n\r\n`; use a fake writer whose first drain raises TimeoutError and later drain succeeds; call `_handle()`. Observed status lines: `['HTTP/1.1 404 Response', 'HTTP/1.1 400 Response']`. No source or operation file was written, no HTTP client/server was started, and zero handler tasks remained.
- Fix: make all HTTP header writes share the response-commit state and mark it before write/drain; after a committed non-stream response fails, close only. Add parameterized failure tests for the early rejection replies, including first-drain timeout and disconnect. Existing line-471 JSON drain regression covers only the upstream success branch and misses these branches.

## Cross-task integration finding already owned by root/recovery review

The fresh adapter workflow at `/Users/jiandan/Code/GitCode/Easel/easel/harnesses/codex.py:38` passes `outputs/_harness/central-execution` without creating it as private. Proxy `:126` uses `operations.mkdir(parents=True, mode=0o700)` then chmods only operations. Thus a normal 022 umask creates the parent central-execution directory as 0755. The current reader `/Users/jiandan/Code/GitCode/Easel/easel/platform/execution_records.py:96` requires both central-execution and operations to be 0700 and silently returns no rows otherwise. Root independently found this and assigned the recovery reviewer verification/fix ownership. This review confirms the source mismatch but does not duplicate their repro or claim Task 2 acceptance. The integrated status lookup remains blocked until the fresh-writer-to-reader path is covered.

## Areas passing source review

- Incremental native path consumes HTTPX-decoded chunks, validates and yields complete bounded events one at a time, and drains each local HTTP chunk before pulling another upstream chunk. It has no cumulative SSE payload cache, rejects unacceptable response status/type/encoding before success headers, and enforces event/total limits.
- Valid completed/incomplete nested status is required before the successful zero chunk. Error/failed/EOF/malformed/timeout/cancel paths retain local pending and use fixed sanitized error content. Successful upstream JSON headers are already tracked correctly. Transfer cleanup cancels both upstream and disconnect tasks; exit closes the listener/client and clears credentials.
- One `_forward` has one physical POST with one UUID; new tool continuation is a distinct operation. Existing no-retry settings were not changed by this delta. Local transport records do not invent financial settlement.
- Version-2 metadata binds the shared business-owner function, retains creation time, updates transport time, bounds optional execution-header metadata, and contains no prompt or credential. Atomic exclusive 0600 record writes and operations directory 0700 are retained; parent-directory mismatch is noted above.
- Actual-native gate tests at `tests/test_central_execution_stream.py:329` invoke both packaged and Node launcher paths (explicit opt-ins), await emitted app-server token before releasing the terminal fixture gate, assert one initial POST/sending record, then require tool result continuation and two distinct UUIDs/delivered records. These are materially stronger than synthetic HTTP timing alone. Current file includes subsequent raw upstream chunked, JSON-drain, cancellation and invalid-header tests.
- Frontend recovery is an explicit fixed no-store GET carrying only AbortSignal. It neither auto-queries on login/scope change nor offers re-send/POST/tool replay. Render-time scope comparison hides previous owner rows immediately; effect cleanup aborts and late handlers check abort before storing. Logout clears the requested scope and re-login needs a fresh click.
- UI distinguishes local transport, central financial status, and cache availability. Settled plus expired remains settled; missing actual is unknown; not_found/unavailable explicitly cannot establish zero charges or safe resubmission. Existing account scope includes URL/account/full scope; theme classes are reused.

## Evidence and remaining limits

Implementation reports state 71 stream/native/protocol tests passed with both native opt-ins and no skips, 212 frontend tests passed, build and lint passed. This reviewer inspected the relevant assertions but deliberately did not rerun those suites. Only the concrete committed-header repro above was executed independently.

Task 3 code review has no additional actionable finding. Its required desktop/390px browser screenshot acceptance was still pending in the supplied UI report, so this report does not mark full Task 3 visual acceptance complete. Task 2 authorization/ledger/record-reader acceptance and whole-stage/live-provider acceptance are outside this scoped review. Fixture-native timing does not prove a real supplier call, settlement, or live delivery.

## Closure verification

Both blockers are closed in the current proxy source. `_reply` at lines 207-211 marks the shared response state committed before writing; every handler reply passes it. `__aenter__` at lines 123-147 creates/opens and verifies ownership of the two private directories with NOFOLLOW file descriptors, then fchmods both to 0700. Existing ancestor permissions and legacy record content are preserved. The changes do not touch the upstream one-POST/stream terminal/ledger behavior.

Independently executed the new targeted regression file after reviewing all its assertions:

```sh
rtk proxy .venv/bin/python /private/tmp/easel-settings-pytest.py tests/test_central_execution_directory.py -p pytest_asyncio.plugin -o asyncio_default_fixture_loop_scope=function -q --tb=short
```

Result: **16 passed in 0.17s**, no skips. This was an explicitly bounded loopback/temporary-files-only run outside the sandbox, no supplier calls. It covers actual local POST producing an owner-readable v2 record from a fresh workspace under 022 umask; preserving workspace ancestor modes and legacy record bytes/mtime/mode; rejecting unsafe private directory paths; and 404/403/429/credential-502 first-drain TimeoutError/OSError yielding exactly one status line. No new actionable issue found in these fixes. Root retains consolidated regressions and pending browser/live acceptance.
