> 历史归属／只读查询任务最终审查 PASS；以下保留初审问题和关闭记录。

# S3-B Task 2 independent scoped review

**Disposition: CHANGES REQUIRED** (one confirmed cross-task integration defect).

Scope: Task 2 and its integration with Task 1 durable records, reviewed against `/private/tmp/easel-s3b-before` through `/private/tmp/easel-s3b-recovery-review.diff`. This is not whole-S3-B approval or live-provider acceptance. Reviewed plan global constraints/shared contracts/Task 2, implementation report, all Task 2 production changes and tests, root three integration tests, and the existing CodexAdapter/proxy/session authorization seams. No source, environment, preview or credential changes; no live calls; no broad repeat of passing suites.

## Required fix

### [P1] Make freshly created native record directories satisfy the recovery reader contract

- Changed contract location: `easel/platform/execution_records.py:92-97`, specifically line 96 requiring both `central-execution` and `operations` to be owned mode 0700.
- Producer location: `easel/platform/execution_proxy.py:126-127`; real caller `easel/harnesses/codex.py:38-45` passes an uncreated `outputs/_harness/central-execution` directory. `mkdir(parents=True, mode=0o700)` creates only the leaf `operations` with that mode; its newly created parent inherits normal 0755 under umask 022. `prepare_subscription` does not repair this parent.
- User impact: a normal first central run persists a valid v2 operation, but manual interrupted-status inspection silently returns no records (`([], False)`) because the reader rejects the base directory. This breaks the main feature in fresh workspaces.
- Focused reproduction: constructed the real `ExecutionProxy` with synthetic fixture scope/credential, CodexAdapter's exact fresh path, and umask 022. Called its actual `__aenter__` with only `asyncio.start_server` mocked to avoid sockets; called actual `_record` for a UUID operation; called actual `read_operations`. Observed `base_mode 0o755`, `operations_mode 0o700`, `record_version 2`, `recovery_before ([], False)`. Changing only the disposable base directory to 0700 yielded `recovery_after_private_base_count 1`. Temporary directory was removed automatically; no repository or user records modified.
- Tests currently mask it: `tests/test_central_execution_recovery.py` helper manually chmods the base; root `services/platform/tests/test_execution_stream_integration.py:75-76` explicitly creates it mode 0700 before the proxy. Add a producer-to-reader regression from an absent base directory using the real initialization path. Fix the writer/caller to securely establish the owned private base, retaining strict reader rejection of unsafe/symlink paths.

## Reviewed contracts with no further actionable finding

- `lookup_operation` validates bounded tuples, validates the full current session/authorization epoch through identity and wallet authorization, then filters historical user, product, tenant and billing account independently. A valid fresh same-owner session can read old financial metadata without reviving old task contexts; old scope validation remains active.
- Tuple lookup has no mutation/provider dispatch/result replay. The SQL projection inspects cache existence/expiry/nullness without retrieving payload bytes; the public result excludes original scope, credentials, binding/body hashes and response payload. Expired cache remains settled financial history.
- `recoveries()` verifies `/v2/me` against persisted current full scope/account, hashes only business owner for local discovery, uses URL-encoded GET tuple lookups, and invokes no execution credential mint or model resend. Existing auth refresh may POST only `/v2/auth/refresh`.
- Authentication/account errors abort before returning any accumulated rows; ordinary unavailability/404 remains explicit and contains no fabricated actual cost. Found output is rebuilt through the existing strict public execution projection plus bounded cache state and date validation.
- Reader uses fixed workspace-relative directory descriptors with `O_NOFOLLOW`, owned private final directories and files, UUID filename/tuple matching, v2 ownership, model/hash/date/status validation, regular-file checks before and after open, nonblocking file opens, and independent scan/count/size/time limits. Legacy/foreign/unsafe records are not changed.
- Client bounds per-request I/O and checks the 10-second request budget between decoded chunks and before new tuple lookups; scan/request exhaustion is represented without starting more tuple requests. This is not an independent proof of a hard wall-clock SLA across filesystem lock contention and every transport phase.
- Product bridge uses the fixed current workspace/client and inherited loopback guard; user query parameters do not become local paths/owner authority.
- Root integration test assertions meaningfully connect provisional early text, terminal settlement, replay/status without duplicate charge, interrupted pending holds and settled-but-undelivered behavior. Allowing `httpx.RemoteProtocolError` on intentionally unfinalized chunked delivery is appropriate while requiring no terminal success and exactly one charge. These tests are fixtures over real PostgreSQL/owned HTTP, not live-provider acceptance.

## Evidence boundaries

Author reports native **66 passed, 1 opt-in skip**, PostgreSQL **44 passed**, and task Ruff passed; Task 1 separately reports actual Node + packaged native acceptance. These reported suites were inspected as evidence, not rerun by this review. Root's final three integration test execution was pending at assignment. The reviewer executed only the focused offline directory-layout reproduction above. Closure requires the private-base fix and its fresh-path regression, then review of that narrow delta.


## Narrow closure review — final Task 2 PASS

**Final disposition: PASS for Task 2; the P1 above is closed.** This supersedes the initial CHANGES REQUIRED disposition while preserving the original finding/reproduction as audit history. It is not whole-S3-B or live-provider approval.

The reviewer inspected the current `ExecutionProxy.__aenter__` at lines 123-147, `/private/tmp/easel-s3b-directory-fix.md`, all 16 cases in `tests/test_central_execution_directory.py`, and the updated root PostgreSQL/HTTP integration test. The producer now explicitly creates the private base with mode 0700, opens the base and operations using directory/no-follow descriptors, validates OS ownership before descriptor-based chmod, and closes all descriptors. Existing owned private 0755 directories are corrected without changing workspace ancestor permissions or legacy record bytes/mtime/mode. Unsafe base/operations symlinks, regular files and foreign owners are rejected.

The new fresh-layout regression leaves the base absent under umask 022, passes a complete fixture business scope/credential and UUID invocation, sends a real owned-loopback operation through mocked central transport, then asserts that the real reader discovers exactly one current-owner delivered record. It also asserts unchanged existing ancestor modes. This directly exercises the originally failing producer-to-reader path. Repair and six unsafe-path cases cover the surrounding failure modes.

The root integration fixture no longer precreates the base and the terminal-success integration now calls `read_operations` with the computed owner and asserts the exact produced operation is discoverable. Root reports current three PostgreSQL/HTTP integration cases passed in 1.42 seconds. The fix author reports 106 focused tests passed in 5.06 seconds with both actual native opt-ins and no skips; the independent stream/UI reviewer reports a separate 16-case run passing. These execution results were supplied by their owners; this closure review inspected source/tests/report and did not repeat those suites.

No new actionable Task 2 issue was found in the narrow correction. No source/environment/preview changes or live calls were performed by this reviewer. Only this review report was appended.
