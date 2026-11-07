# B3 target execution implementation refinement

> Continue the approved B3-L plan in the current codex/ui-redesign checkout. No commit, push or merge. Current source-only work does not imply runtime qualification or restore success.

**Goal:** execute real PostgreSQL transactions and genuine installed-application checks inside the exact isolated target network namespace, preserving original authority, deadlines, private data and failure custody.

**Decision:** one pinned offline Linux worker artifact, fresh operation process per phase and separate fresh runtime-only application process. Retain the strict PostgreSQL factory unchanged. No generic host SQL proxy, arbitrary command transport or source mount.

## Slice 3C1 — protocol and policy

Create `services/platform/src/platform_service/media_backup_b3/execution_contract.py` and its pure unittest module. Support only worker attestation initially. Bind job/input/context/target identities, original clock and finite independent Root resource ceilings; build a closed worker launch manifest; bounded framed request/private response parsing; hashes-only non-success summary. Records are data, never live authority. No IO, Docker, SQL or actual-isolation claims. Requirements are frozen in `/private/tmp/easel-b3-execution-contract-20261104-brief.md`. RED/GREEN and independent source review precede any runtime use.

## Slice 3C2 — owned execution and graph

Add `execution.py`: bind the exact live VerifiedRestoreInput and TargetHandle, original context, job inode and clock. A durable exclusive claim is written and fsynced before each possible spawn. Original process/FD/container custody is retained before native entry. At most one worker at a time. Bounded streaming, original-host deadline and worker-local remaining interval; no comparing host and Linux monotonic timestamps. Constructor interruption, output/IO/commit uncertainty retains UNKNOWN. Recovery observes original identities only and never replays. Extend caller graph counters and disposition predicates explicitly; do not relax factory READY accounting. Cover interruption, replay, late proof, fsync and close-with-UNKNOWN cases with pure transport seams before actual use.

## Slice 3C3 — fixed worker artifact and attestation

Build offline from frozen cached public inputs, current installed wheel and pinned native dependencies. No source import fallback or runtime package installation. Worker joins only the original full target CID network namespace; UID/GID10001, zero capabilities, NNP, read-only root, finite CPU/memory/swap/PID/tmpfs, no ports, business mounts or Docker socket. Independently compare actual target and worker namespace identities and only-loopback routes/interfaces. Validate installed executable/module/resource provenance. Root capacity accounting includes retained targets plus active worker and payload; host free space alone does not qualify Docker VM capacity.

## SQL and application phases

Implement trusted roles/control/restore/observe/smoke functions per original Tasks4–7 through the worker. Real psycopg transaction boundaries; CREATE DATABASE separate utility; pg_restore one archive transaction using fixed tool bytes and pinned dependency closure. Bootstrap secrets never reach the fresh app process. Original configuration is restored immutably; smoke uses explicit disabled providers/workers. Full catalog/raw/COPY/credential/sequence and financial comparison precedes final control CAS. Unknown commits are observed, never resent.

## Unresolved specification edge

PG16 original bootstrap grantor semantics require explicit resolution before role mutation. The approved design forbids changing new bootstrap and requires exact original grantors. Source research indicates a differently named OID10 bootstrap and an ordinary recreated old superuser cannot always preserve these simultaneously. No rename, grantor normalization or catalog write is authorized by this refinement. This does not block protocol development or current target-only qualification. Resolve through a concrete reviewed amendment before affected Task4 cases; retain the full mandatory matrix.

## Acceptance

Each source slice: meaningful RED/GREEN, frozen files, independent spec and quality review. Actual qualification: new original job, fresh hash closure, actual two targets and worker provenance/isolation, known terminal original resources. Full B3 remains open until original Tasks4–9, complete matrix and rebuilt installed artifact pass. No front-end text or existing account/history/config changes.

## 2026-11-04 refinement and verified state

Original v1 parsing slice and its audit-hook correction were approved as pure code. Before worker implementation, refine request/response to v2: complete closed policy data in one request, bounded remaining duration, worker-side decode without a host object, response bound to original request hash, worker namespace only. Host independently observes both namespaces and exact worker image/CID; worker does not echo host identities as observations. Current v2 production0e34baba113538b9749b6cd24ca013cfc6be1591541a1462dee0363aa6c6f1e1/testbda0c4880b99f85a0c8ef69d23ff1dd8a4fe2d5c7690e371815693c72dc29d94: author24PASS, Rootfresh24PASS0.213s, independent spec+qualityPASS. Evidence outputs/_run/b3-execution-v2-20261104. Worker/owner/runtime not yet implemented.

Target lifecycle real positive once008 is archived and reviewed at outputs/_run/b3-target-positive-20261104/once008. It ended Tool37406 exit0; all source cleanup/posthash checks passed. Both PGs and parser subsequently stopped with actualexit0, allvolumes/evidence retained. No reuse of consumed input/clock/oldtargets. A later worker/full restore case must use a fresh original job and current source/artifact closure.

Bootstrap-grantor exception now user-approved; exact proposal/approval at outputs/_run/b3-bootstrap-grantor-amendment-20261104. The original no-modification rule remains outside this scope. No actual handover has occurred. Protocol development remains separate from role mutation.
