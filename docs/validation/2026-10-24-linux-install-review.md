# Linux package verifier v2 independent review

Verdict: **PASS for the scoped v1 → v2 timeout cleanup change.** No P1/P2 issue found in this static delta review. This is not a claim that the running Linux installation, production deployment, or real identity/provider acceptance has completed.

## Reviewed inputs and binding

- `verify_linux_package_v2.py` SHA256: `9dd3f1244b66964833be40a7cefc611ee8d784c72438e687924736403ceb3f1a`.
- Baseline `verify_linux_package.py` SHA256: `47772eb953e7e023d8dce90a9eab8daa241b2f4432e4362c965f648eee6b0323`.
- `test_verify_linux_package_v2.py` SHA256: `87d3a6a90dfe664e2acc21bf4958c59f495d1e2be160120766984cd90c875af5`.
- Read the unified delta, complete v2 script, focused tests and author review. Root reports a fresh local run of 25 passing tests in 0.03 seconds; this reviewer inspected those tests and did not repeat them.

## Findings checked

1. **Signal scope is the process group created by this invocation.** `run_owned` (lines 93–121) requires a fresh-root-shaped path, a log directly under that root, and an argument within that root. Both call sites are fixed commands for the fresh virtual environment. `Popen(..., start_new_session=True)` creates the session/group whose numeric ID is the returned child PID. The timeout handler rejects PID ≤ 1 and signals only that PID as a process-group ID. There is no process-name matching, PID-file lookup, global kill, or discovery of old service processes. The existing exact host checks and exclusive fresh directory creation remain intact.
2. **A parent exiting during TERM handling does not skip child cleanup.** After SIGTERM, the bounded parent wait may succeed, but the code still attempts SIGKILL against the same group. `ProcessLookupError` means that attempt found no such group; it is not treated as successful installation. This addresses the observed pip parent/child timeout seam. The focused regression simulates the parent returning after the initial timeout and verifies both group signals.
3. **Timeout cannot become success.** The original `TimeoutExpired` is re-raised after cleanup, including when the subsequent parent wait returns zero. Nonzero normal exit is rejected. The outer error path emits `success: false` and `production_ready: false`; successful probe verification and receipt publication cannot be reached through this timeout path. Cleanup waits are bounded to 5 seconds each. The increased installation/SSH time limits are finite and accommodate the nested operation budgets.
4. **The public-only installation boundary is preserved.** The fixed public wheel/requirements/probe upload set, content and hash validation, isolated environment, strict SSH host checking, canonical package index, new target directory, and sanitized error output remain unchanged. No provider credential, identity configuration, old database state, or raw installation log is added to the receipt.
5. **The synthetic probe remains bounded to construction checks.** Its socket audit hook, synthetic inputs, disabled execution/payment runtimes, installed payload hash checks and migration inventory are unchanged. It does not enter application lifespan or perform database, provider, payment, or real login acceptance. The probe still has its own finite timeout; this patch does not claim to turn it into a general subprocess supervisor.

## Limits of the conclusion

The receipt records signal attempts and whether the direct parent was reaped; it correctly does not assert that every possible descendant is dead. Process groups cover descendants that remain in the created group, not arbitrary descendants that deliberately establish a new session. Numeric process-group signalling is not a cgroup-level lifetime guarantee. The reviewed fixed pip/venv commands do not introduce a daemonization or process-discovery path. Host disruption or external termination of the SSH wrapper is also outside this local timeout-handler guarantee.

No remote command, network request, installation, process signal, service action, private configuration read, or test execution was performed for this review. The actual v2 run and its eventual safe receipt must be assessed separately by root.


## 根代理运行复核补充

独立阅读 v1→v2 差异后，根代理 fresh 运行 `test_verify_linux_package_v2.py`，25 passed in 0.03s；随后执行 v2 的默认本地计划，核对新 wheel SHA、带哈希依赖和当前 56 个 payload。该计划声明 not_run，不发起 SSH。实际 --execute 另有独立目标回执，不能由纯测试／审查推导成功。首次 v1 实际安装因 600 秒超时失败；未用放松依赖 hash、私有配置隔离或禁 socket 探针来绕过。
