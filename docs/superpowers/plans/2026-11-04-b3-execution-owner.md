# B3 ATTEST host execution owner implementation plan

> **For agentic workers:** Apply executing-plans task by task after Root assigns implementation. This deliverable is design only; no source implementation, tests, native execution, Git operations or additional capacity request is authorized by this document.

**Goal:** Complete existing Slice3C2 with exact producer-owned target custody, bounded ATTEST execution and a closed caller/supervisor ownership graph.

**Architecture:** One execution owner wraps the existing strict target factory before either target is created. It keeps the actual returned handles and all original host/remote resources; immutable wire records remain data. An independent, fixed observation path establishes the target/worker baseline before the installed worker receives a request.

**Tech stack:** Existing Python standard-library ownership/journaling patterns, existing v3 execution contract and network validator, pinned installed Linux worker. No new generic runner, framework, SQL transport or runtime dependency.

## Scope and fixed constraints

- Preserve `target_factory.py`, `target_lifecycle.py`, the PG image/tools and all eight PG runtime states/records unchanged. Execution uses a separate journal and counters.
- Only `attest_worker`; no SQL, role handover, restore, application smoke, cleanup command or bootstrap exception implementation.
- Preserve the exact original input, consumed stage, authorities, context objects, cancellation event, job inode and original host deadline. No later object, receipt, namespace record or successful cold smoke grants authority.
- At most one worker container/process is active per original job. Execute central then identity sequentially, using a fresh worker for each target. Both targets must have been produced through the owner before attestation begins.
- Worker: exact target full CID network namespace; UID/GID10001, capabilities zero, NNP, read-only root, finite independently pinned CPU/memory/total-swap/PID/tmpfs ceilings, public environment only, no ports, business/source binds, socket mount, PID namespace join or privilege expansion.
- Claims are exclusive private files with file and directory fsync before every possible native entry. Check original deadline after intent fsync and immediately before the constructor. Preserve partial constructor/process/FD/container/remote-exec custody on every BaseException.
- Sticky UNKNOWN never becomes success; recovery observes originals only, without replay, renewed clock, automatic stop/remove or adopting replacement proof.
- N4 v3 source is approved: contract `dacaa79cdbbc7534a2bb4b3582ff5893ce67e749521bb6a7cd8da054aedadb6d`, worker `6d4e5500ef7bc1475b41e3d69d2ce872871b42bb06aa734a51ad3954436e3da5`; fixed profile `b803b4094d8254da45b7ca9c4666d2875794d18532fc721c87488bd64c245582`. Recheck implementation closure before actual work; these are current evidence pins, not production constants.
- Actual full job requires a fresh original input and new target pair; once008 is consumed. Cold once002 proves installed cold execution only, not live full PG target binding or restoration.

## Existing source facts and integration points

`RootTargetFactory.create` retains `_Attempt` before native work and registers job/inode/kind ownership. `_Attempt._create` returns a new `TargetHandle`; neither the factory nor attempt retains that handle's identity. `TargetHandle.verify` verifies its attempt, so a new wrapper around the same attempt is not sufficient proof of original handle custody. The new owner must intercept original production, not accept an already returned handle as authority.

`_Attempt._run` anchors a partially initialized Popen before `__init__`, but its file-based output transport is not the bounded concurrent streaming transport for execution. Do not reuse it for workers or add execution operations to its command table. `TargetHandle.safe_receipt()` invokes verification/native PG operations; it must never be called by a supposedly observation-only execution graph snapshot.

`VerifiedRestoreInput` is defined in `dump_preflight.py`, not `preflight.py`. Its verification binds `_stage`, `_stage_authority`, `_parser_authority`, proofs and facts; stage `verify` checks the original deadline. `_PrivateJobFiles` pins job lineage, Docker binary/config and capacity. `_TargetLedger` is solely PG preparation custody, with CLAIMED/PREPARED records; execution must not append to it.

`b3_target_once.graph_state` currently declares PG READY only for `_state == READY`, `_sequence == 8`, and eight records, then matches each original process with its producer output entry and permitted exit. Preserve this block verbatim. `validate_terminal` currently accepts only zero/false graph fields except parser terminal count; execution success therefore needs an explicit closed schema change, not a catch-all allowance for positive counters.

## Exact public API and retained objects

Create `services/platform/src/platform_service/media_backup_b3/execution.py` with this public surface:

```python
@dataclass(frozen=True, repr=False)
class RootExecutionPolicy:
    worker: WorkerPins
    target_probe: TargetProbePins
    limits: ExecutionLimits

class RootExecutionOwner:
    def __init__(self, factory: RootTargetFactory,
                 verified_input: VerifiedRestoreInput,
                 policy: RootExecutionPolicy): ...
    def create_target(self, kind: str) -> TargetHandle: ...
    def attest(self, kind: str) -> dict: ...  # hashes-only summary DATA
    def snapshot(self) -> dict: ...         # observations, no active commands
    def close(self) -> None: ...            # host custody only, no resource stop
```

`WorkerPins` is a frozen closed record: `image_id, architecture, artifact_sha256, dependency_manifest_sha256, entrypoint_sha256, observer_sha256, pg_restore_sha256, image_inspect_sha256, public_env`. `TargetProbePins` is a frozen closed record: `image_id, image_manifest_sha256, image_config_sha256, tools, dependency_closure_sha256, probe_schema_sha256`; `tools` is an exact ordered tuple of `(role, absolute_path, sha256)` for sha256sum/stat/readlink/cat. `ExecutionLimits` contains exactly the existing contract's `cpu_nanos, memory_bytes, memory_swap_bytes, pids_limit, tmpfs_bytes, envelope_limit, private_result_limit, diagnostic_limit`. Independent baseline/inspect outputs have fixed source caps: 1 MiB each; target probe per response 64 KiB; all stderr 64 KiB. No dynamic selectors, callbacks, argv, SQL, request policy, time budget, handle or returned probe can enter the public methods.

Policy contains no expected dynamic network-security pin: owner derives it only from its own original independent observations. Root checks public policy inputs before launching the original job. Public policy data alone cannot manufacture an owner.

Constructor requires exact types and a fresh factory with empty `_attempts`. Register a strongly held provisional owner by original job `(st_dev, st_ino)` and original factory identity before duplicating FDs or invoking any constructor that can partially acquire resources. Bind exact `verified_input`, `_stage`, `_stage._authority is _stage_authority`, parser authority, original context tuple and each context identity, cancellation event, factory policy/tool objects and canonical hashes. Duplicate and retain original job FD with no-follow named lineage checks. Refuse another owner, an already-used factory, replaced authority/context/event, or changed policy. No clone/pickle/deserialization factory.

Private retained records:

- `_TargetSlot(kind, original_attempt, original_handle, state)` is inserted before calling `factory.create`. Save the returned handle directly in the original slot, check `handle._attempt is factory._attempts[kind]`, and never replace it. If return/assignment is interrupted, owner becomes UNKNOWN; the factory's original attempt is still retained and no handle is reconstructed.
- `_ExecutionAttempt(kind, target_slot, worker_slot, operations, request_bytes, baseline, state, unknown)` is inserted before its first IO. Both target slots must be original returned handles, READY and not closed/unknown. Bind `_attempt` identity and target identity/CID/start metadata as well as exact handle identity on every guard.
- `_NativeSlot(operation, local_process, fds, remote_container, remote_exec, output_offsets, terminal)` is appended before pipe/native allocation. Each FD is stored immediately; constructors are invoked only after their object has been allocated and registered. `remote_exec` is a retained intent for this exact CID, fixed command and ordinal, initially terminal-unobserved; it is not a receipt claiming a remote process ID exists.
- `_WorkerSlot` exists before create, with the source-derived unique name/labels/image/network plan. Set its full CID exactly once from a validated create return. An interrupted create retains the original name/intent with CID unknown; observing a candidate by name may inform disposition but never resumes the execution phase or adopts it as authority.

Use one owner lock in order owner -> original factory lock -> file lock. Disallow overlapping create/attest/close. A failed contender does not poison the winner. Frozen records protect normal API use, not hostile same-process Python mutation; Root retains the existing same-owner-write exclusion assumption.

`create_target(kind)` is the sole producer wrapper. It calls unchanged `factory.create(original_input, kind)` exactly once, retains its actual return, and exposes that same object for existing target receipt reporting. `attest(kind)` takes no handle. It retrieves the retained original handle and rejects a new handle referencing the original attempt, copied receipts and even a structurally equal replacement context. Guard original peers before dispatch. Both attestation slots are single-use including failure.

## Execution journal, guards and clock

Own a separate `_ExecutionLedger`, never `_TargetLedger`. Create files at original job dirfd with `O_CREAT|O_EXCL|O_NOFOLLOW`, mode0600; validate regular type/owner/mode, bounded partial writes, file fsync, directory fsync, inode/hash and original budget. Retain every opened descriptor before any subsequent operation. Maximum record 8192 bytes. Prefixes are exclusively `execution-owner.once`, `execution-{kind}.once`, `execution-{kind}-{ordinal:03d}-{intent|result}.json`; ordinal comes only from the closed internal command sequence.

Closed record fields: `format, kind, ordinal, phase, restore_id, operation_id, input_sha256, context_sha256, target_identity_sha256, target_cid, worker_cid, policy_sha256, job_device, job_inode, started_at_ns, deadline_ns, argv_sha256, request_sha256, previous_sha256, terminal_code, stdout_sha256, stderr_sha256`. Unavailable fields are null only in the explicitly defined intent variant. Final success record additionally binds baseline and validated response hashes. Journal commit is a durability commit, not a database transaction.

State order: `BOUND -> CLAIMED -> TARGET_BASELINED -> WORKER_CREATED -> WORKER_WAITING -> BASELINED -> DISPATCH_CLAIMED -> DRAINED -> VALIDATED -> COMMITTED`. These are execution states only. Every native operation has its own preceding durable intent, including inspect/hash/probe/worker-create/start-attach. UNKNOWN is absorbing from every state. A burned claim cannot be retried even if no child was observed.

Guards are separated deliberately:

1. `_guard_live()` checks exact original live bindings, no UNKNOWN/closed state, paired target status, original event and deadline, immutable pins, FD lineage and execution ledger integrity. Invoke expensive PG `handle.verify()` only at explicit pre/post boundaries while original PG ownership is retained; its native operations stay entirely in factory accounting.
2. `_guard_before_native(slot)` runs after intent fsync, checks all original bindings and time again immediately before constructor. No timing check performed before fsync can substitute for this one.
3. `_snapshot_original()` only polls retained local handles and reads already-retained remote observations under a nonblocking lock. Failed observation or lock acquisition is UNKNOWN; no `safe_receipt`, verification command, discovery, reopening expired input or renewed budget.

Store original host monotonic start/deadline without renewal. Derive integer ns once conservatively (deadline rounded downward; start upward) while preserving original float checks. Immediately BEFORE the original worker start/attach native constructor, retain `main_start_anchor = host_monotonic()` in its original worker slot; after the durable start intent and final live guard, set `acceptance_deadline = min(original_job_deadline, main_start_anchor + 60 seconds)`. This host timestamp is a conservative start bound, never a measured Linux timestamp. Observer launch/input/output, all worker/target rechecks, request commitment/transfer, response drain, validation and success commit use this SAME acceptance deadline. No new deadline is assigned when the helper completes or the request arrives. Original job budget checks continue in addition to this narrower window.

Compute request `remaining_ns` from that acceptance deadline immediately before commitment, conservatively capped at 60,000,000,000 ns even when original host job remainder exceeds 60 seconds. Burn and fsync the exact original request, recheck the SAME acceptance window after fsync and immediately before delivery, and dispatch those original bytes once. Existing installed main keeps its original local 60-second deadline established before reading input; owner does not renew or replace that deadline. The request interval cannot restore time already consumed while main waited for independent baseline work. Host/Linux timestamps are never compared; only remaining durations cross the wire. Main-window expiry before dispatch or during any later phase burns the execution attempt into sticky UNKNOWN, retains original resources and forbids restarting main, launching a replacement worker or replaying a request.

The fixed helper anchors `helper_origin = local_monotonic()` BEFORE reading any frame byte. Its initial input/read-and-work cap is fixed at 15 seconds (within the N2 maximum 60 seconds): `initial_deadline = helper_origin + 15 seconds`. Read the bounded framed request under that initial deadline, accepting exact positive integer `remaining_ns <= 60,000,000,000`. After decode set `helper_deadline = min(initial_deadline, helper_origin + remaining_ns / 1e9)`, never `now + remaining`. Every helper input continuation, N2 reader call, proc observation and output write uses this same helper deadline, and every N2 call receives remaining time <= 60 seconds. Reject if input consumed all of it. Host sends a remaining duration additionally capped by its original acceptance window and continues draining/enforcing that host window independently. The helper response/EOF/remote terminal observation must also fit it. This gives separate bounded local clocks without pretending they share an epoch.

Use nonblocking stdin/stdout/stderr and one bounded selector pump. `envelope_limit` and `private_result_limit` are PAYLOAD byte caps, unchanged from v3. Parse the fixed 4-byte length header first; reject zero or over-limit declared payload before payload allocation. The request frame must contain exactly `4 + declared_request_payload_size` bytes and be <= `envelope_limit + 4`. Response stdout must contain exactly one frame of `4 + declared_response_payload_size` bytes and be <= `private_result_limit + 4`; reject ANY trailing byte even when below the total byte ceiling. Reject a truncated header or body at EOF. Stderr remains an unframed raw byte stream capped at `diagnostic_limit`, with no extra 4-byte allowance; all other unframed probe/inspect caps remain unchanged. The helper request payload is capped at 64 KiB (total frame <= 64 KiB + 4), and its observation response payload at 1 MiB (total frame <= 1 MiB + 4), with the same exact-one-frame, nonzero declared size, preallocation bounds, truncation and no-trailing-byte rules. Read both streams concurrently in <=64 KiB chunks; partial write offsets survive EINTR/EAGAIN within this same operation. On premature EOF, trailing bytes, zero/broken write, overflow, malformed frame, timeout, cancellation or commit/close ambiguity: sticky UNKNOWN. Never resubmit an entire frame after uncertain progress. Close stdin once after exact delivery, retain its close result; success requires exact full response, stdout/stderr EOF, producer-validated local exit0, known terminal worker, no active original execs, valid original bindings and durable final result. No stream file can grow unbounded outside these caps.

## Independent target and worker baseline sequence

The following is one closed sequence, per target; all probes share the original job budget and per-operation durable claims:

1. Verify the exact retained target handle through unchanged factory; save original full CID/image/start/PID and expected inspect projection. Target observations are Linux VM facts. Reject target restart/PID/start drift rather than rebinding.
2. Execute a fixed target probe under original target CID as existing PG UID/GID999:999, with no extra capability or environment beyond fixed public C locale. Use individually fixed commands (no shell and no `-c`): pinned `stat -L` for `/proc/1/ns/net`, pinned `readlink /proc/1/ns/net`, pinned `cat /proc/1/stat`, pinned `readlink /proc/1/exe`. Fixed `/proc/self/ns/net` observation must agree with target PID1 net namespace; `/proc/self` denotes the probe itself. Capture before/after PID1 start/stat/ns to reject drift; validate exact ASCII format and bounded sizes. Full target CID inspect surrounds probes. No arbitrary filesystem paths or target Python assumption.
3. Tool qualification gate: inspect the already approved public PG image's selected OCI filesystem/metadata offline, identify exact real stat/readlink/cat/sha256sum paths, binary versions, symlink targets, loader and dependency hashes. `target_factory` currently uses `/usr/bin/cat` and `/usr/bin/sha256sum`; their existing use does not qualify stat/readlink or invent their hashes. If this fixed tool set is absent/unusable at UID999 or proc access is denied, this design cannot qualify the full target; retain the gate, do not install/copy tools, change PG image, add privileges or substitute a configurable runner. Root public-material qualification supplies actual pins; none are fabricated here.
4. Create worker from independently pinned immutable image and closed manifest, network `container:<original target CID>`. Validate actual full created inspect defaults/public image environment, no VOLUME/binds/ports/sockets, resource ceilings and start identity. Start the installed main via original attached stdin, leaving it waiting with zero request bytes delivered. Anchor original attach Popen/FDs and remote worker slot before start constructor. Retain all output concurrently while waiting.
5. Run a separate installed fixed observer in this original worker CID. Proposed helper `execution_observer.py`, fixed invocation `/opt/easel/bin/python -I -B -m platform_service.media_backup_b3.execution_observer`. No program text, modes, paths or selectors. Its only input is a closed framed object `{format: "easel.b3.execution-baseline-request.v1", remaining_ns: positive_int}` with remaining_ns <= 60,000,000,000. Before reading its frame it fixes the local origin and 15-second initial cap described above; decoded duration can only shorten that original deadline. The host observer request and all its IO use the already-fixed original main acceptance window. Output contains bounded complete raw N4 observation plus own/PID1 namespace, PID1 start, PID1 exe identity and observed UID/GID/capabilities/NNP. It reports observations only; no expected security hash is supplied. It must establish its own namespace == original worker PID1 namespace and original installed main identity.
6. Host reparses raw observer output through the sealed installed wheel's N3/N4 pure code, not a parsed digest supplied by the observer. Compare observer/PID1/worker namespace with independently probed original target namespace in the SAME Linux VM. Derive expected security/profile pins only now. Recheck original target CID/PID/start and worker CID/PID/start, original image and namespace immediately before committing the ATTEST request. Target kernel namespace cannot come solely from Docker NetworkMode, worker self-report, synthetic fixture or public cold smoke.
7. Build `ExecutionBinding`/`ExecutionPolicy` internally using the originals and derived baseline; request stays v3. Worker before/after raw observations must independently validate against this baseline and target namespace. Validate response with `validate_attestation(policy, original_request_bytes, response_bytes)` and derive existing summary only after all original terminal observations and journal durability succeed. Worker must exit naturally; no automatic stop/remove.

The observer is a required small installed helper, not a dynamic runner. Adding it changes the installed artifact closure and requires a rebuilt pinned artifact plus a new bounded public cold qualification before full-job use. Current approved image is evidence for the previous closure only.

Remote-exec custody is explicit: retain exact original CID, command/ordinal, parent local process and before/after inspect alongside raw results. Validated command completion with original local exit0 and stable original container with no active execs is required for successful completion; CLI exit alone is insufficient. If daemon exec IDs are observed, freeze them in the slot and inspect those originals only. If an interrupted constructor leaves exec identity unavailable, keep it unobserved/UNKNOWN; never invent an ID or infer completion from a replacement process. This conservative path can remain quarantined until external disposition confirms original remote terminal status.

## Closed graph integration and disposition

Add exactly these fields to `graph_state` (bool or nonnegative exact int, never bool-as-int):

| Field | Type | Normal ATTEST terminal condition |
|---|---|---|
| execution_owner_count | int | 1 |
| execution_target_count | int | 2 |
| execution_phase_claims | int | 2 |
| execution_committed_count | int | 2 |
| execution_local_terminal_count | int | validated count > 0 |
| execution_local_live_or_unobserved | int | 0 |
| execution_remote_live_or_unobserved | int | 0 |
| execution_active_work | int | 0 |
| execution_unknown | bool | false |
| execution_observation_error | bool | false |
| execution_disposition_retained | bool | false |
| execution_retained_owner_count | int | 0 |

The last two fields describe a disposition outcome only: `execution_disposition_retained=True` requires exactly one original owner retained (`execution_retained_owner_count=1`) with `execution_unknown=True`. They are false/zero for ordinary success, including a successfully closed owner, and for an UNKNOWN owner before any matching disposition. They do not mean its remote operations completed or its FDs closed. The retained owner stays in a strong process registry and is included in graph snapshots even after the caller leaves quarantine. `execution_remote_live_or_unobserved` preserves the actual retained remote observation and may remain positive; disposition does not rewrite it. No ATTEST success schema accepts the RETAINED combination.

An absent owner has all zeros/false for the old target-only path. A present but mismatched/partial owner yields observation error and unknown, never an absent-owner default. Snapshot counts are derived from original slot lists; verify unique process identity, exact producer transcript matching, original terminal code/output hashes, durable committed attempt count and no duplicated slots. `unknown` ORs execution unknown/error/live/active, plus incomplete claimed execution at the final boundary. During earlier legitimate graph snapshots a BOUND producer with no requested execution is idle; normal final validation separately demands two commits. Graph never calls native inspection or verifies targets.

In `actual` initialize `execution_owner=None` beside factory/handles before the try. Keep `factory=make_factory(...)` as the real factory so existing graph accounting continues. Allocate/anchor the owner before its constructor; replace direct creation with `execution_owner.create_target(kind)`. After both target receipts, call central then identity `attest`. Keep `finalobj`, stage, factory and handles live through both commits and graph snapshots. Add separate hash-only `execution` result; existing target receipts keep `kernel_network_isolation_proven=False` because their own producer has not gained that proof. New execution result may explicitly state `target_bound=True`, `attestation_complete=True`, `SQL_restore=False`, `isolated_restore_verified=False` after the owner meets all gates.

Update `cleanup_known` with two explicit branches before creating its resource list:

- Ordinary known success: prepend `EXECUTION_CLOSE` before FINAL/source/FD release. Record the original owner and each host FD close result in the original cleanup attempt. Only a complete successful close may mark `EXECUTION_CLOSE` TERMINAL; the owner remains anchored for audit. No remote stop/remove is performed. Any close exception flips sticky UNKNOWN, raises, and stops cleanup so remaining input/source resources stay held. Existing cleanup_failed remains absorbing; disposition does not retry an already failed close sequence.
- Explicit validated disposition of an UNKNOWN owner: require the matching original-owner-bound disposition and `disposition_work_terminal` conditions below before any unrelated cleanup. Anchor the original owner, its FD objects and immutable audit references in the strong retained registry, then record `graph['execution_disposition_state'] = 'RETAINED'` and the validated document hash/binding as DATA. Append a separate disposition audit row with `phase='EXECUTION_RETAIN', state='RETAINED', resource=<original owner>`; this is never a cleanup TERMINAL row. Set the two RETAINED snapshot fields to true/one. Omit this owner from the cleanup resource list ONLY in this branch: do not call its `close()`, do not release its owned FDs, do not clear UNKNOWN and do not fabricate a terminal process/exec result. Proceed with the existing explicitly authorized unrelated synthetic cleanup order, preserving owner-owned FD exclusions if caller lists alias them. The owner remains strongly anchored after quarantine ends; all actual uncertainty stays in the graph. A retention-record/registry uncertainty stops unrelated cleanup and stays quarantined.

Ordinary UNKNOWN without the validated binding returns `cleanup_refused_live_work=True` before any close or unrelated resource release; a wrong/missing binding cannot select RETAINED. RETAINED itself grants no cleanup authority and is not a substitute for validating the original document on entry. Normal success never takes this branch. An unrelated cleanup failure after RETAINED still follows existing absorbing cleanup_failed behavior and keeps all remaining resources.

Update `disposition_work_terminal`: additionally reject execution active work, local live/unobserved and observation error. Existing external `remote_terminal_confirmed` remains the explicit Root disposition contract; it is authorization for disposition of the exact graph, not an observed daemon completion result. Extend its closed document with `execution_owner_sha256` when an execution owner exists (required there, forbidden for old target-only shape); match the exact original in-memory journal head and retain the matched document hash. No document creates an owner or changes its remote observation. Only this matching disposition plus original-local terminal observations and zero active work selects RETAINED and permits authorized unrelated synthetic cleanup. A live/unobserved local process or failed snapshot blocks the branch regardless of the document. Remote UNKNOWN remains sticky and cannot qualify success, but the matched external disposition can authorize the unrelated cleanup while that UNKNOWN owner remains retained.

Update `quarantine_unknown`: preserve original counters/owner in `_QUARANTINED`, validate the document and local-terminal predicate, then select the RETAINED branch explicitly rather than calling UNKNOWN owner.close. Preserve `execution_unknown=True`, the actual remote-live/unobserved counter and `remote_status_unknown=True` whenever those resources remain unobserved; do not apply the existing unconditional `remote_status_unknown=False` update to this retained-execution path. Set `root_disposition_applied=True` and preserve the disposition hash and RETAINED state separately. `synthetic_cleanup_complete=True`, if reached, describes only the authorized unrelated synthetic cleanup, while result remains success=False/status=FAILED and execution disposition state is RETAINED. After that cleanup finishes, remove this caller graph from the active quarantine loop but keep the original owner/FD/audit registry anchor. Observation never calls `attest`, creates probes, renews time or clears UNKNOWN. Wrong bindings, original local liveness or observation errors keep the original loop and release nothing.

Update `execute_fd` result status to a distinct closed ATTEST result (`target_worker_attestation_verified`) only for requested execution with two commits. Preserve old target-only status and closed version separately; do not silently reinterpret historical reports. `validate_terminal` uses explicit schemas for these two statuses, validates all new counter types/conditions and the two ordered summaries and common original bindings. Positive committed/terminal counts are allowed only by these rules, never by broad nonnegative acceptance. Update `workflow` to pass the closed independently pinned execution policy in the source-pinned configuration, retain original external-launch release sequence and produce an attestation-specific safe qualification result with restoration flags false. Update `validate_config`, `validate_spec` and `dependency_hashes` closure for new policy/helper modules; no generic optional field bag.

## Bounded implementation tasks and pure verification

All implementation tasks require meaningful failing regression, minimal source changes, focused green tests, frozen file hashes and independent specification/quality review. No commit/push/merge. Root assigns implementation separately.

### Task 1: producer ownership and separate durable ledger

Create `execution.py` and `tests/test_media_backup_b3_execution.py`. Implement the public types above, owner/slot identity guards and claim/append primitives. Keep factory source unchanged. Private native seam names `_popen`, `_monotonic`, `_open`, `_fsync`, `_selector` are module-local primitives patchable only by tests; public API cannot receive a transport callback.

- [ ] Write failing identity cases: fake factory returns one exact handle; owner retains it; a fresh handle on same attempt, replaced stage/authority/context, alias job FD competing owner, used factory and replay all fail before execution native entry.
- [ ] Write fsync-boundary case: original clock advances past deadline during file/directory fsync; native constructor count remains zero and burned claim remains retained.
- [ ] Implement identity registry and separate execution journal, including partial-write/constructor interruption anchoring and contender-does-not-poison-winner behavior.
- [ ] Run focused pure tests and review source; no fake result is reported as live authority.

### Task 2: fixed observation and bounded transport

Extend `execution.py`; create `execution_observer.py` and `tests/test_media_backup_b3_execution_observer.py`. Implement closed operation table and exact sequence above; target tool policy is fail-closed until actual public-material pins exist. Observer only uses fixed installed N4 reader and fixed proc identity reads.

- [ ] Fail tests for target/worker namespace mismatch, target restart, changed original CID, worker PID1 start/exe drift, self-report-derived baseline, source/fixture result adoption and request dispatch before baseline.
- [ ] Implement independent raw observation validation and compare all original identities before dispatch; no target Python invocation or configurable command runner.
- [ ] Fail then implement bounded concurrent pump with partial writes, EINTR/EAGAIN, stderr flooding, oversize frame header, trailing output, EOF, nonzero exit, interruption inside Popen init and expiry after durable dispatch intent.
- [ ] Assert a partially initialized Popen is reachable from original owner before fake constructor raises, all allocated FDs remain tracked, and UNKNOWN blocks another worker or request.
- [ ] Assert final journal fsync failure and close ambiguity cannot emit a successful summary, even after valid response bytes.
- [ ] Add focused framing boundary cases for request/response and the fixed helper: an otherwise valid payload exactly at its payload cap is transport-accepted with its 4-byte header; declared cap+1 is rejected before payload allocation; truncated 4-byte header, truncated body and ANY trailing byte fail. Diagnostic and other unframed caps receive no framing allowance.
- [ ] Add fake-clock cases: original job has 600 seconds remaining but N2/request intervals never exceed 60 seconds; helper spends 4 seconds reading a request with remaining=6 seconds and finishes under origin+6 rather than read-complete+6; helper input consuming its full 15-second cap fails before N2; decoded remaining consumed by input also fails before observation.
- [ ] Add fake main-window cases: host anchor is retained before the start constructor; constructor/baseline consumes 59 seconds and dispatch gets at most 1 second; baseline or dispatch fsync reaches anchor+60 and no request is written; job deadline earlier than anchor+60 wins. A subsequent clock read/helper completion never moves the original acceptance deadline, and expiry keeps the attempt burned UNKNOWN with no restart/replacement/replay.

### Task 3: caller/supervisor graph and lifecycle

Modify only `services/platform/scripts/b3_target_once.py` functions `actual`, `graph_state`, `cleanup_known`, `disposition_work_terminal`, `disposition_permits`, `quarantine_unknown`, `validate_config`, `dependency_hashes`, `execute_fd`; retain `make_factory` result as strict original factory. Modify `services/platform/scripts/b3_target_supervisor.py` functions `validate_spec`, `validate_terminal`, `workflow`. Tests: existing `test_b3_target_once_pure.py`, `test_b3_target_supervisor_pure.py` plus execution tests.

- [ ] Fail tests for zero-owner forgery in ATTEST final outcome, missing/extra graph field, bool-as-counter, unmatched local process, hidden live remote, two concurrent workers, incomplete pair and success with sticky UNKNOWN.
- [ ] Implement exact status/schema split and producer wrapper integration; keep original PG READY eight-state/eight-record predicate unchanged.
- [ ] Fail then implement cleanup refusal while worker active/unknown, source/input retention on close failure, original-owner-bound disposition, and observation-only quarantine after deadline.
- [ ] Add one complete pure integration path: UNKNOWN owner enters quarantine with original local handles terminal, no active work or observation error; matching original-owner-bound explicit Root disposition selects RETAINED; assert owner.close is never called, original owner/FD/audit references stay anchored, execution_unknown and any remote uncertainty remain, RETAINED fields are true/one, authorized unrelated cleanup occurs once in original order, and overall success stays false. No disposition document writes a remote terminal result.
- [ ] Exercise that same path with absent/wrong owner binding and with a live original local process: neither selects RETAINED nor releases any unrelated resource. Ordinary successful path must call/record owner.close before remaining cleanup with RETAINED false/zero. Close failure must keep UNKNOWN and stop unrelated cleanup; matching disposition cannot reset cleanup_failed or retry that failed close.
- [ ] Preserve existing target-only outcome validation; new ATTEST acceptance requires exactly two original target slots and two committed attempts, no active/live/error counters and ordered hash-only summaries bound to original input/contexts/deadline.

### Task 4: source acceptance package

- [ ] Run focused pure tests using the checkout's qualified interpreter with `PYTHONPATH=src:scripts`, from `services/platform`; use `python -m unittest discover -s tests -p 'test_media_backup_b3_execution*.py'`, then each existing caller/supervisor pure module. Prefix shell with rtk. No pytest/plugin auto-loading or native runtime tests is needed for this slice.
- [ ] Regression-check existing execution contract/worker pure modules and factory/lifecycle pure modules once; use explicit existing filenames after confirming them, not all-application discovery.
- [ ] Verify source diff is confined to assigned files, factory READY source unchanged, no forbidden command/secret/source mount paths and no production claim. Record exact test results/source hashes, independent spec review and quality review. Mocks prove source behavior only.

## Actual gates, separate from source completion

Root's current evidence: image `81e6305be6381a0d12c0d7a7bc38f2d0fb2b922af306691aafdcba36045b16b4`, wheel `a40b8b3b97a5dae0573746b1a45ef146cee87e6afe9e16ae6f9c351649582dfa`, build manifest `bb25c5c65fd347d54ae77fb475e370d7b01a736f14c6a190850a82c61c256a2a`, 5592 files/472708149 bytes; cold once002 request1817/response148240 bytes. These identify approved prior evidence; do not hardcode them into production execution logic.

Before actual full-owner qualification Root must independently qualify unchanged PG stat/readlink/cat/hash binaries and their closure, rebuild/qualify the worker with the fixed observer, pin installed host reparsing closure and full image defaults, and obtain the existing fixed full-job capacity of 12.88 GiB. Capacity remains pending and does not block source work. No new capacity question, Docker action or tool installation belongs to this design task.

Then use a new original job/input/target pair and observe both original targets plus sequential workers under the original deadline. All original resources must have known terminal disposition. Full Tasks4–9 SQL/application/financial/media/matrix remain open. Public cold success, unit mocks, this plan and host-only free-space measurement are not those proofs.

No actual contradiction with the approved binding plan was found: original handle producer ownership is supplied by the fixed wrapper, and missing target-probe pins are an explicit qualification gate, not a reason to change the PG factory or broaden authority.
