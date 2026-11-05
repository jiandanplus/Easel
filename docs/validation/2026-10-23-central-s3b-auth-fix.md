> 认证响应修复已通过最终独立复核；本报告保留作者的 RED／GREEN 过程。

# S3-B malformed authentication response correction

Status: implemented and focused verification passed. Independent final reviewer closure remains the root's step. This is a source/fixture correction, not real-provider acceptance.

Scope: only `easel/platform/central.py` and `tests/test_central_execution_recovery.py` in `/Users/jiandan/Code/GitCode/Easel`, current shared `codex/ui-redesign` checkout. No commit, push, reset, environment edit, preview restart, external request or model call.

The final review identified that `_request` parsed JSON before considering HTTP status. Empty, non-JSON, malformed and non-object 401/403/409 responses became 503, allowing recovery lookup to return previously accumulated financial rows. The fix retains HTTP auth/account classification immediately after receiving headers; malformed JSON, invalid error envelopes, body-size rejection, deadline rejection and `httpx.HTTPError` during body reading retain that status with a fixed sanitized `platform_unavailable` error. Error codes are accepted only from a dictionary envelope containing a string in the existing allowlist. Only a complete explicitly accepted invalid-authentication code may remove credentials. Ordinary malformed 404 becomes `not_found`, while malformed 503 remains `unavailable`; neither invents financial state or resubmits work.

TDD evidence:

- Initial new matrix: HTTP 401/403/409 after one valid found row, with empty/plain-text/malformed/null/list/invalid error-envelope bodies; plus malformed 404/503 classification. Before implementation: **19 failed, 1 passed, 19 deselected, 0.32 s**. All 18 authentication cases failed because no error was raised; the malformed 404 was incorrectly unavailable. The first red selection omitted the async plugin and emitted an unknown-mark warning; no async test ran in that selection.
- First correction: three focused client suites **74 passed in 0.49 s**, with pytest-asyncio enabled.
- Root requested the same invariant for oversized and interrupted bodies after headers. Added six cases, all with an already found row. Before extension: **6 failed, 39 deselected, 0.16 s**, each returning partial rows instead of raising.
- Final correction: all three focused suites **80 passed in 0.61 s**, no skips or warnings reported.
- Existing Ruff: **All checks passed** for both owned files.
- `git diff --check -- easel/platform/central.py tests/test_central_execution_recovery.py` passed after the final extension; root performs final global diff/preservation checks. Both files are preexisting untracked work in this checkout, so tracked-only `git diff --stat` does not represent their full delta; compare with the S3-B before snapshot.

Final verification command:

```sh
rtk proxy .venv/bin/python /private/tmp/easel-settings-pytest.py tests/test_central_execution_recovery.py tests/test_central_platform_client.py tests/test_central_billing_client.py -p pytest_asyncio.plugin -o asyncio_default_fixture_loop_scope=function -q --tb=short
rtk proxy ruff check easel/platform/central.py tests/test_central_execution_recovery.py
```

Assertions cover aborting the full recovery read after one found row, preserved status, fixed non-leaking message, unchanged persisted credentials, unchanged local records, two GET-only tuple lookups, and unchanged non-auth lookup semantics. Existing explicit `session_invalid` regression continues to assert credentials are forgotten. No supplier acceptance is implied.
