# Restore window v2: private freeze implementation plan

> **For agentic workers:** Use the approved design and execute this first block with test-driven-development and verification-before-completion. No commit, push, merge or production operation is included.

**Goal:** Implement a root-private, canonical, unapproved F capture contract bound to the existing fixed f50/001–016 source and complete v6 inventory, replacing no controller or DR behavior yet.

**Architecture:** Reuse the SHA-pinned v6 `collect_inventory`, `match_inventories` and canonical bytes as the single complete schema/data/roles/ACL/sequence/money authority. A trusted read-only adapter supplies snapshot lifetime and non-database observations; F captures and rechecks the same complete inventory and raw source sandbox/config/media observations. No raw JSON CLI, production adapter discovery, DSN/environment loading, maintenance or model request is added.

**Tech stack:** Python standard library and existing frozen pure ops modules; pytest synthetic fixtures. Actual PostgreSQL/Linux capture remains a separate gate.

## Global constraints

- F1 owns only `services/platform/scripts/media_restore_freeze.py`, `services/platform/tests/test_media_restore_freeze.py`, and this plan. Approved F2 owns only new `services/platform/scripts/media_restore_capture.py`, new `services/platform/tests/test_media_restore_capture.py`, and this plan; F1 bytes remain fixed.
- Preserve old controller SHA5e100…, DR SHA34df…, wrapper SHAc5ee…, v6 SHA4d3c… and all 123 original guards.
- Production authority remains fixed f50 wheel and exact migrations001–016. Never discover-and-accept a new release/schema.
- Whole v6 table inventory includes financial, execution, media, internal_feature_extensions and remaining schema tables; no second prefix inventory or new ledger.
- Fresh window balances and all pending remain observations, not long-term constants or permission to process any unknown request. F is unapproved with no actions.
- Root0700/0600, exclusive/no-follow/single-link files, complete durable writes and fsync. Safe output uses an explicit allowlist.
- Source PID sandbox raw mount/hash is strict; host surface is separate. No topology delta allowance in this block.
- No SSH, production/config/auth reads, real nonce generation, maintenance, financial writes, model POST, large PG container, or full central/frontend run.

## Block F1 — one independently reviewable deliverable

Files: create the two files above. Owner: `incho_independent_review`.

Interface to freeze:

```python
capture_freeze(adapter, contracts, *, window_id, created_utc, expires_utc) -> dict
save_private_freeze(directory, freeze) -> dict  # private path + digest + safe summary
load_private_freeze(path, expected_sha256, contracts) -> dict
safe_summary(freeze) -> dict
assert_same_source_sandbox(before, after) -> None
```

`contracts` loads the exact approved v6 and mixed source bytes under fixed SHA; its complete inventory is reused without filtering. `adapter` is trusted code, not a user-supplied JSON document; its commands and snapshot provider are the explicit integration boundary. This block does not claim to prove an arbitrary adapter is read-only.

- [x] Write meaningful red tests: fixed authority and migration pins, complete inventory authority, captured balance independence, unchanged full data across fresh snapshots, original media3/2/1, full source sandbox versus separate host surface, canonical JSON rejection, private durability/symlink/hardlink/permissions/exclusive failures, safe nested sentinel omission.
- [x] Run `rtk proxy env PYTHONDONTWRITEBYTECODE=1 services/platform/.venv/bin/python -B -m pytest --noconftest services/platform/tests/test_media_restore_freeze.py -q --tb=short` and save the actual missing-feature failures. Initial module absence caused39 fixture setup errors; the primary complete capture path separately confirmed1 intended assertion failure. Subsequent metadata, coherence and error-sanitization red cases were preserved before fixes.
- [x] Implement only tested API/schema and adapter boundary. No controller/domain writes or production CLI were added.
- [x] Run the same focused test file and only necessary existing pure v6/mixed closure nodes. Historical first candidate:69 new tests and11 existing pure closure tests passed,0 skipped, but independent review subsequently found6 incorrect admissions and requested changes. The old candidate receipts remain unchanged under `/private/tmp/easel-restore-freeze-f1-20261028`; the new revision is recorded below.
- [ ] Hand off frozen source/tests/schema to independent review. Record real PG/Linux capture and W/P/D integration as unverified.

## Subsequent blocks (not implemented by F1)

- W/P controller: approve exact F, same-window CAS, durable intents, only two model state updates, paused freeze and bounded resume. No wallet literal replacement in the old script.
- D wrapper/DR: chain exact F/W/P/receipt SHA into a new plan, fresh resource/nonce/ciphertext identities, same-snapshot and target full inventory comparisons and source financial postchecks. Keep historical code immutable and map every old guard to equal-or-stronger new behavior.
- Actual Linux/PG and controlled production window: inspect current topology, TTL/GC/idle/resource state, review exact safe plan and execute once within authorization. Original failed DR remains failed until a new complete real success receipt exists.

Detailed design and 17-category test matrix: `/private/tmp/easel-restore-window-independent-design-20261028/design-review.md` (SHAe1e190cd5e1d697383f2b52c6227f14c411a52fd23b9a2d5c0515b2d8ffd0b2a). Current diagnostic balances in that document are not future capture baselines.

## F1 integration limits

The closed factory reads five exact canonical source basenames next to the F module and verifies their fixed SHA before use; future server staging must provide those code bytes in a new root-private bundle. No module path can be supplied through the API. Current tests use a synthetic command sink that executes the real pinned collector's read-only SQL construction without invoking Docker/PostgreSQL. Root UID is substituted only for synthetic private-file permission tests; production default remains0.

The future reviewed adapter must directly read raw lot aggregates and funded, unresolved text reservations in the same read-only snapshot. Do not call `CreditLedger.summary()`, which calls `expire()` and writes. Historical resolved/writeoff jobs remain in the complete inventory. The adapter must reuse the reviewed filesystem capture and verify the binding facts against actual OS/config/installed bytes; this block validates and binds the closed record, not those unexecuted real observations.

Created/expiry timestamps are bound to a UTC interval of at most900 seconds. Reading an F does not assert it is still fresh: the W consumer must check actual current time, boot/source identity and exact live snapshot before authorizing actions. F always remains unapproved/no-actions and does not execute the old paused `mixed_window` guard on an enabled or projected state. Actual paused P validation, W/P/D integration, source topology rehearsal and real restore are unverified. No old failure has been reclassified.

## F1 revision2 — independent contract finding

The first candidate incorrectly accepted missing attempt/budget/valid_invocations, invalid valid_invocations, video capability on the known image job, and an empty model_jobs population. The unchanged independent21 probes were first reproduced as6 failed/15 passed. That failed review and the old3 source hashes/tar are retained; none of its results are transferred to this revision.

- [x] Add complete f50/001–016 synthetic to_jsonb row fixtures, all25 population counters, every required row column/group, extra-key, evidence/attempt/budget/knownmissing/owner/scope/metadata and current-actor rejection probes before fixing. Expanded red run:197 failed/108 passed, including the absent enabled validator.
- [x] Add `validate_enabled_media_state(actual_state, contracts)` with exact actual True flags, closed full population and8 full source row schemas, unchanged original media relationships and additional typed metadata/scope/session/product checks. It neither mutates the input nor calls the paused guard. The old guard independently still rejects true flags.
- [x] Enforce current euid equals ROOT_UID before private-directory/file access; default ROOT_UID stays0. Synthetic file tests substitute their own owner and separately simulate a wrong current actor.
- [x] Run the expanded author file:320 passed/0 skipped,5.69 seconds. Run the original independent probe unchanged:21 passed/0 skipped,0.31 seconds. These are separate runs, not a central-suite total. Re-run only the existing11 pure closure cases separately and verify old5 pins/123 guards before freezing.
- [ ] Independent review of the new3-file SHA, API/schema and narrow diff before W/P/D or any real producer/operational work.

Revision2 evidence: `/private/tmp/easel-restore-freeze-f1-r2-20261028`. The actual SQL/Linux producer, OS/config facts, current-clock admission, controller integration and real DR remain unverified. This revision does not act on any unknown fee or hold.


## F2 — fixed-source read-only producer and actual-clock contract

Root approved the narrow design `/private/tmp/easel-restore-freeze-f2-design-20261028.md` (SHA256 `8df18b9e3f9f109397a588fbb715ed926fbca6ecb4626bb183fcce61d1aa7ab0`). This adds the production source implementation, without executing it in this development block. F1-r2 contract-only independent approval and its original evidence remain separate. F1 source/test, all five pinned old scripts and the original123 guards must remain byte-for-byte unchanged.

Owned files: `media_restore_capture.py`, `test_media_restore_capture.py`, and this plan. No new dependency, general adapter loader, JSON/DSN/role/clock CLI, source maintenance command, controller action or balance literal is introduced.

Production APIs:

```python
capture_server(*, window_id, expected_producer_sha256) -> dict
validate_capture_freshness(freeze_path, freeze_sha256, receipt_path,
                           receipt_sha256, *, expected_producer_sha256) -> dict
```

Both construct their own fixed source broker, OS/config fact reader and clock after a root/Linux/fixed-host/isolated-interpreter/source-SHA gate. `window_id` is supplied by a future reviewed controller; this module never creates a real nonce. Capture emits only `captured_unapproved`, F plus a root-private producer receipt, and `approved=False/actions=[]`. Freshness emits only `fresh_capture_unapproved`; it is neither a same-window financial CAS nor permission for W/P/D.

- [x] Use the pinned core's complete inventory collector, exact read-only transaction envelope and fixed schemas/query templates. The producer keeper exports a real `REPEATABLE READ READ ONLY` snapshot. All full core collector passes, raw lot sums, all-domain unresolved graph and actual enabled media observation use that same registered token per pass. Unknown role, source, token, SQL suffix, writable CTE, command and caller transport keyword reject before dispatch. No `CreditLedger.summary/expire/complete` call is used.
- [x] Bind fixed Unix Docker socket and root-owned CLI, explicit empty private `--config`, scrubbed environment with no HOME override or Docker/XDG variables, inspected full source container IDs/image/start, fixed bootstrap database identities and selected application process database configuration. Central API and console use their actual distinct `CENTRAL_` and `CENTRAL_CONSOLE_` configuration keys. Secrets remain in memory; only reviewed configuration hashes/metadata enter F.
- [x] Read installed f50 wheel/source and exact001–016 migrations, original11 configuration files, actual source PID/start/command/executable/systemd invocation, source sandbox raw mount and separate host raw mount, live blob registry/owned bytes and known missing relationship through fixed source paths. No allowed topology delta is added. Existing source filesystem/OS implementations are reused, not a caller-supplied JSON record.
- [x] Project every raw lot and all unresolved reservation/job associations without expiry/funding/user filters. Preserve legitimate historical waived operations and immutable old holds. Every current/historical reservation retains its entire original hold allocation sum. Hold account must agree with reservation and lot; strict boolean immutable lot funding must match original reservation funding in pending, settled, released and waived history. Current mutable frozen/expiry flags do not reject a legitimate historical allocation. An independent unallocated test/frozen/expired lot is retained in whole-wallet totals.
- [x] Bind UTC, monotonic, boot and qualified PostgreSQL clock samples. Maximum offset/jump5 seconds, capture900 seconds, strict existing blob/artifact horizon greater than1800 seconds. All actual expiries are retained; no expiry extension, wallet change or historical state modification is performed to pass. A fresh actual clock/binding check is required for receipt validation.
- [x] Stream subprocess stdout and stderr under hard bounds before allocation can exceed the accepted stream budget; timeout/cancellation/overflow kill and reap only the process group created for that invocation. Actual local synthetic processes verify both over-limit streams, simultaneous input/output, timeout, cancellation and pipe closure. Docker/systemd/PG are not invoked in these tests. Keeper success rolls back, and errors terminate only its own local session; no source container stop/kill command exists.
- [x] Publish F plus closed producer receipt with root-private exclusive/no-follow/fsync/hash-readback persistence. A partial F or receipt failure never returns completion; the existing window cannot be overwritten. Sanitized rejection never prints private command output or exception contents.
- [x] Preserve red evidence before fixes: initial missing producer; financial/clock/receipt7 counterexamples; enabled graph/media mismatch; bound/config7 failures; historical hold12 failures; closed transport/environment5 failures; missing/altered immutable historical hold2 failures. These are historical diagnostic runs, not a summed test total. Final focused author run is100 passed/0 skipped; necessary F1/core closure is recorded in the separate frozen receipt.
- [ ] Independent review of frozen F2 source, API/query/receipt schema and focused evidence.
- [ ] Actual owned PostgreSQL RR/read-only/SQLSTATE25006, Linux/root/socket/OS/config/process gates and actual production capture. Synthetic seams do not certify these gates; no real producer has run in this block.

F2 evidence lives under `/private/tmp/easel-restore-capture-f2-20261028`. Future server execution additionally needs explicit staging of exactly reviewed root-private source bytes, the expected producer hash, a genuinely fresh controller window identity, current legal source state and enough unchanged TTL/resources. Dynamic financial data remain observations. If any real observation is expired, inconsistent, unknown or outside fixed authority, the producer rejects rather than altering the source or accepting an old baseline. W approval, paused P, same-window intent/action control, D encrypted backup/restore targets/registry lifecycle and a real successful disaster-recovery receipt remain unimplemented by F2. The earlier failed DR remains failed.


## F2 revision2 — four independent P2 findings

The first frozen F2 candidate remains retained under `/private/tmp/easel-restore-capture-f2-20261028`, with independent REQUEST CHANGES under `/private/tmp/easel-restore-capture-f2-independent-20261028`. Root authorized only the same producer/test/plan files for this revision. Before changes, the original unmodified blocking subset reproduced6 failed/5 passed/2 deselected; the two generic terminal-history expectations were explicitly withdrawn as blockers and remain excluded, not silently enforced.

- [x] Literal `127.0.0.1` and `::1` URLs now require the corresponding inspected published HostIp. `localhost` conservatively rejects; no DNS/hosts-file inference or source configuration change is introduced.
- [x] Production remains Linux-only. Standard `os.waitid(...WNOWAIT)` observes an owned child without reaping its identity. Runner and keeper never poll/wait before failure signalling. Failed/cancelled/over-limit invocations send one SIGKILL only to that invocation's still-owned group, then wait/reap the leader. Normal observed exit0 with output EOF may reap/close directly; it does not prove all arbitrary background descendants disappeared.
- [x] After a failure group's leader is reaped, only bounded existence checks (`killpg(pgid,0)`) remain: only ESRCH proves the group disappeared, while0/EPERM wait to a2-second deadline and then reject. No nonzero signal is repeated after reap; potential numeric reuse can only cause conservative rejection. The leader wait has its existing10-second bound, so failure cleanup has an explicit additional maximum12-second leader/group window beyond the transport deadline. Pipes close in finally, including signal error, observation error, timeout and already-reaped repeat paths. This does not claim direct reaping of grandchildren adopted by init.
- [x] The original immediate exited-leader probe exposed a further transition race during a parallel closure run:10 passed/1 failed/2 deselected. That failed run is retained as `original-independent-green.*` in the r2 evidence directory despite its earlier filename. SDK-only local experiments found a short-lived same-birth process record after SIGKILL, while SID/FD observations were already ESRCH;10/100/1000ms observations disappeared. These non-atomic observations neither establish a still-running sleep nor justify calling the record a zombie. The new bounded disappearance predicate, rather than a fixed sleep or repeated green-only run, closes that transition. Original r1 genuinely omitted group signalling; its finding is not reclassified.
- [x] Keep each first-anchor clock/boot/skew/TTL check and add each adjacent sample's system UTC, DB UTC and monotonic non-decrease check during capture and private receipt validation. Existing5-second,900-second and strict1800-second bounds remain unchanged.
- [x] Add an internal `final_clock()` that reads expiry and PG clock in the same restricted read-only snapshot, finishes keeper/source/fact closing I/O, then samples actual local UTC/monotonic/boot. Capture calls it after F+receipt fsync/readback; freshness calls it after prior file/config/database I/O. Final admission is pure calculation against the same original window and previous sample. It appends no final sample to the already-written receipt list, writes no extra receipt and performs no subsequent source I/O before returning. A late failure may leave an exclusive private orphan, never completion/freshness or extended expiry.
- [x] macOS synthetic process tests use a test-only C dylib compiled by the installed clang/SDK using real `siginfo_t` and WNOWAIT macros, loaded in the same parent and injected into the test seam. Production has no ctypes/Darwin branch or reaping fallback. The test bridge is not Linux operational evidence. A missing toolchain/compiler error fails explicitly; no test is silently skipped. Compilation uses only its own temporary directory, without changing HOME.
- [x] Author focused:132 passed/0 skipped. Original unmodified blocking subset after the bounded fix:11 passed/2 explicitly deselected. Separate unchanged F1/core closure results are recorded in the new frozen receipt. Original financial projection semantics remain unchanged, with two explicit accepted generic settled/released history regression cases.
- [ ] Independent review of the new revision and frozen source/API/query/private-receipt hashes.

Revision2 evidence: `/private/tmp/easel-restore-capture-f2-r2-20261028`. The two public function signatures and private receipt field schema remain unchanged. Actual Linux waitid, process-group lifecycle, PG exported snapshots/SQLSTATE25006, source/root/config/OS capture and W/P/D operations remain unverified. No production fact factory, SSH, real database, user auth, nonce, provider request or financial operation ran. The old failed real DR remains failed.


## F2 revision3 — final TTL upper bound and cleanup re-entry contract

The revision2 candidate and its independent report remain immutable. The new original TTL probe was reproduced1 failed/9 deselected before source changes, and two added conservative-bound cases reproduced2 failures before the pure fix. Final TTL now requires every asset expiry to exceed `max(final OS UTC, last observed DB UTC) + 5 seconds` by strictly more than1800 seconds. This is an explicit derived completion upper bound, not a newly observed database time. Existing first/adjacent5-second checks,900-second window and strict1800-second threshold stay in force; no extra source I/O, field, expiry change or receipt mutation is added.

Root clarified the earlier whole-lifetime-single-SIGKILL wording. The actual safety invariant is that **each nonzero signal requires a fresh WNOWAIT proof of the same still-unreaped owned child/group**. After KeyboardInterrupt leaves that identity unreaped, another owned cleanup attempt may repeat SIGKILL. Once reaped, ownership lost, or cleanup budget exhausted, it must never send another nonzero signal. The original private B assertion forbidding a second signal for the entire lifetime remains an unchanged nonblocking failed expectation; it was not altered to make an all-probe green claim.

A single private Popen attribute `_easel_cleanup_deadline` is fixed at the first cleanup monotonic time plus12 seconds, before observation/blocking/signal. Re-entry never extends it. Each leader wait is limited by both10 seconds and the remaining total budget; group disappearance is limited by both2 seconds and that same remaining budget. At exhaustion only nonblocking reap/close is attempted and fixed rejection is returned, without claiming unknown processes were cleaned. No global state, public parameter, private F/receipt field or new production platform branch is introduced.

- [x] Original TTL1 RED, added upper-bound2 RED, pure TTL3 GREEN.
- [x] Interrupted owned cleanup re-entry and exhausted shared-budget2 RED, then GREEN; already-reaped/ownership-lost/no-resignal controls remain covered.
- [x] Focused author136 passed/0 skipped. Original r1 blocking subset11 passed/2 excluded terminal-history expectations. New r2 blocking set9 passed/1 excluded B. Original B is separately retained as1 failed/9 deselected under `nonblocking-original-B-retained.*`, explicitly not a blocker under root's clarified invariant.
- [ ] New independent review of revision3, required before real source capture.

Revision3 evidence is `/private/tmp/easel-restore-capture-f2-r3-20261028`. F1/old5/123 guards, financial projector AST, public API signatures and receipt field schema remain unchanged. Actual Linux waitid/cleanup, PostgreSQL/root/source-fact factory, production capture and W/P/D remain unverified. The public FFmpeg source research is separate; none of its material changes this producer's source authority or proves a live restore.
