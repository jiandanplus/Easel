# Media Backup B1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Deliver a local, bounded DB/config/media archive closure and private file restore using only synthetic data.

**Architecture:** A strict normalized capture contract defines every expected archive member and binds snapshots, source/schema and registry digests. A canonical raw USTAR codec streams bounded chunks. Filesystem adapters verify trusted FDs and the exact flat media inventory; orchestration validates the full archive into a fresh private stage and returns only limited file-level receipts.

**Tech Stack:** Python standard library; pytest in the existing `.venv`; no added packages, network, database, production CLI or timer.

## Global Constraints

- Human approved route A / B1 on 2026-10-29. Current design SHA `fd210395d8bf870e67b185d8eeb56997f5d13f97bfe1825ad70e6bd64bc438bb`. Frozen R2 technical design was independently reviewed; the current change only clarifies that approval was pending at writing time. That design's historical approval wording is not an implementation receipt.
- Work in `codex/ui-redesign`. No commit, push, merge, production changes, real configuration/credentials/media/wallet reads, paid requests, AV uploads or retries.
- Do not modify existing DR/F1/F2/AV sources or desktop/frontend source. B2/B3 remain separate and incomplete.
- Limits: per file 4 GiB, complete physical archive 32 GiB, 100,000 data members plus one manifest header, manifest 32 MiB, chunk 1 MiB. A caller may tighten but not enlarge these trusted limits.
- USTAR only: canonical 512-byte ordinary headers, manifest first, no PAX/GNU/link/device/directory extensions, no extractall. Physical length includes headers, all padding and mandatory two zero blocks; padding `(-length_after_two_zero_blocks) % 10240`, then EOF checked with at most one extra byte.
- File sources use trusted FDs / private directory FD, ordinary single-link files and exact ownership/mode/size/SHA with before/after metadata checks. Media keys are 32/64 lowercase hex, not tenant directory names. No arbitrary host paths in capture JSON.
- Missing staged is accepted only with an exact trusted normalized plan and evidence digest. Attached must exist; deleted/purged must be absent, preserved as tombstones in registry. Unknown/part/orphan/GC states fail closed. No placeholders, chown from archive values, deletion, quota/expiry/actual/hold mutation.
- Input source consistency and indefinite blocking I/O are caller responsibilities; B1 uses regular files or controlled finite synthetic streams. This code does not authenticate a capture window or encryption boundary. Success labels only `closure_files_verified` and `private_stage_restored_verified`.

## Frozen interfaces

Create package `services/platform/scripts/media_backup/`.

`contract.py` exports `ClosureError(ValueError)`, `Limits` frozen dataclass, `canonical(value)->bytes`, `registry_digest(blobs,references)->str`, `validate_plan(plan, limits=Limits())->dict`, `encode_manifest(plan, limits=Limits())->bytes`, `decode_manifest(raw, limits=Limits())->dict`, `member_records(plan)->list[dict]`, `physical_size(plan, limits=Limits())->int`.

`Limits` fields: `max_file_bytes`, `max_archive_bytes`, `max_members`, `max_manifest_bytes`, `chunk_bytes`, with defaults above. Values must be strict positive integers (not bool) at or below defaults.

Exact normalized plan keys: `format`, `snapshots`, `registry_sha256`, `files`, `blobs`, `references`. Format `easel-media-closure-v1`. `snapshots` keys `central`, `identity` (opaque safe ASCII IDs up to128), `source_sha256`, `schema_sha256` (64hex). File names are exactly these seven required logical members: `db/central.dump`, `db/identity.dump`, `db/central.roles`, `db/identity.roles`, `config/central`, `config/identity`, `config/media`. Files have exact `name,size,sha256,uid,gid,mode`; size allows zero, mode600 or644.

Blobs have exact `key,state,size,sha256,uid,gid,mode,tenant_id,product_id,operation_id,present,missing_evidence_sha256`. Mode600; uid/gid nonnegative 32bit ints; identifiers safe ASCII length1..128; size0..limit; state attached/staged/deleted/purged only; present strict bool. Attached requires present and no missing evidence; staged missing requires64hex evidence and present staged requiresNone; deleted/purged require absent andNone. References have exact `key,tenant_id,product_id,operation_id`, exactly one matching association per blob. Duplicate keys, foreign scope/ref and extra fields reject. Registry digest is SHA256(canonical({blobs: sorted-by-key rows, references: sorted-by-key rows})). The seven files plus present blobs count against max_members; registry metadata also bounded before canonicalization.

Canonical JSON uses UTF-8, sorted keys, compact separators, no NaN/floats; decoder rejects duplicate fields and noncanonical bytes. Normalized lists are sorted by name/key; manifest binds the supplied digests but does not prove DB execution. `member_records` returns the seven file records plus present blobs mapped to `media/<key>`, sorted by member name. Manifest name is `manifest.json`.

`ustar.py` exports `TarError(ValueError)`, `encode_header(name,size)->bytes`, `decode_header(raw)->tuple[str,int]`, `TarWriter(sink,total_size,max_bytes,chunk_bytes)`, `TarReader(source,max_bytes,chunk_bytes)`.

`TarWriter.add(name,size,chunks)->str` streams bytes-like chunks, bounds every output operation, verifies exact size and returns SHA256; `finish()->int` writes only canonical terminators/padding, checks total_size. `TarReader.header()->tuple[str,int]` requires no pending body; `body(size,sink)->str` consumes exact pending body and zero512padding, returnsSHA; `finish(expected_total)->int` consumes exactly two zero blocks and computed record padding then EOF1. Short reads/writes loop with progress, invalidNone/zero/oversized chunks reject. Reader/writer never read/write unbounded sizes. Canonical header has name<=100 ASCII safe relative components, size octal11+NUL, mode600, uid/gid/mtime0, type0, magic `ustar\0`, version00, empty prefix/link/uname/gname/dev fields, correct unsigned checksum. Decoder requires byte-for-byte canonical reencoding, rejecting hidden extension fields before body reads.

`files.py` exports `FileError(ValueError)`, `Sources(plan,media_fd,file_fds,owner,chunk_bytes)` context manager with `chunks(record)->Iterator[bytes]`, `verify()->None`. It borrows FDs (does not close caller FDs), validates exact logical FD keys and exact media names, ownership/modes, directory0700, regular/singlelink, expected size/SHA and metadata stability. Blob opens use openat NOFOLLOW/NONBLOCK; fixed DB/config FDs use bounded pread from offset0. All captured file identities are checked again at final verify; source inventory/directory signatures must be unchanged. No full file buffering.

`closure.py` exports `write_archive(plan,media_fd,file_fds,sink,*,owner,limits=Limits())->dict` and `restore_archive(source,parent_fd,name,expected_plan,*,limits=Limits())->dict`. Write validates plan, constructs manifest, streams every expected member through Sources/TarWriter, verifies source before final trailer; no success on drift/error. Restore requires parent FD owned by current effective uid/gid,0700; a fresh safe ASCII basename, mkdirat exclusive; opens new stage by FD, creates fixed `db`, `config`, `media` subdirectories0700 and ordinary files0600 exclusive NOFOLLOW, copies verified bytes, validates manifest equals supplied trusted expected_plan, exact order/member sizes/digests, trailer/EOF and source regularFD stability. It never follows supplied paths or writes original uid/gid. Stage and files fsync before receipt, stage identity/references rechecked. Failure leaves owned isolated partial stage, never success.

Receipts contain only status, counts, byte totals, archive SHA if computed, manifestSHA/registrySHA (no IDs/keys/paths/content). No production/DB/source-consistency/financial success flags.

## Task 1: strict normalized contract

Files: create `media_backup/__init__.py`, `media_backup/contract.py`, `services/platform/tests/test_media_backup_contract.py`.

- [x] Write behavioral tests for accepted canonical plans; digest/scope/state/duplicate/bool/limit/extra-field/canonical-JSON failures and physical-size formula. First run must show missing capability or a real behavior failure before implementation.
- [x] Implement exported contract exactly; canonical size uses `512 + align512(len(manifest)) + sum(512+align512(row['size'])) + 1024`, rounded up to10240. Reject when physical or manifest limit exceeded.
- [x] Run `rtk proxy .venv/bin/python -B -m pytest --noconftest services/platform/tests/test_media_backup_contract.py -q`, preserve RED/GREEN logs and input hashes in `/private/tmp`.
- [x] Root review strict contract/API and task reviewer spec/quality; fix actual findings before integration. No commit.

## Task 2: streaming canonical raw USTAR

Files: create `media_backup/ustar.py`, `services/platform/tests/test_media_backup_ustar.py`. Independent of contract.py; root maps TarError to ClosureError.

- [x] Test stream roundtrip, forced short IO, exact chunk sizes, negative/oversized sizes, malformed checksums/type/link/PAX/prefix, all padding and EOF cases. Test injected malformed headers cannot trigger body consumption.
- [x] Implement canonical header encode/decode and reader/writer state machine. `padding=(-size)%512`; footer length `(-[bytes_so_far+1024])%10240`. Every raw header and byte counts toward max_bytes; strict integer limits.
- [x] Run `rtk proxy .venv/bin/python -B -m pytest --noconftest services/platform/tests/test_media_backup_ustar.py -q`, preserve RED/GREEN. Root task review spec/quality. No existing archive code reuse that relaxes B1 contract.

## Task 3: trusted source file adapter and orchestration

Files: create `media_backup/files.py`, `media_backup/closure.py`, `services/platform/tests/media_backup_fixture.py`, `services/platform/tests/test_media_backup_files.py`, `services/platform/tests/test_media_backup_closure.py`.

- [x] Fixture creates only tmp_path private media/files with synthetic dumps/config and normalized registry. Test stale size/SHA, missing/extra/part/symlink/hardlink/FIFO, wrong owner/mode, replaced descriptor/path and captured source mutation. Preserve RED before new adapter/closure implementation.
- [x] Stream with `os.pread(fd,min(chunk_bytes,size-offset),offset)`, never whole-archive bytes. Signature includes dev/ino/size/mtime_ns/ctime_ns/uid/gid/mode/nlink. Borrowed FDs are not closed; owned opens always finally-close. Verify source directory and every source again after consumption.
- [x] Roundtrip zero/one/many blobs, present/missing staged and deleted tombstones. Require expected plan; corrupt manifest/member/digest/order/footer/trailing data fails and preserves partial private stage. Parent symlink/path substitution and nonfresh target reject; no source financial or identity data exists in fixture.
- [x] Run new local four-file joint suite. Task review and fix relevant failures, then integration review against the approved design.

## Task 4: actual larger-file resources, regression and delivery

Files: add `services/platform/tests/test_media_backup_resources.py`, `docs/platform/media-backup-b1.md`, validation report and safe JSON under `docs/validation/`, evidence under ignored `outputs/_run/media-backup-b1-20261029/`.

- [x] Write and run actual synthetic >32MiB single-file and aggregate archive/restore tests using sparse input but actual archive/output writes, bounded chunk instrumentation and tracemalloc incremental memory check. Do not equate sparse disk allocation or fake streams with full resource evidence; check disk headroom before running. Limits do not promise current host4GiB/32GiB feasibility.
- [x] Run all new B1 tests plus existing pure mixed-file baseline and related source primitive tests once; only broaden after an unresolved failure. Rehash desktop904/60 and existing frozen central inputs; no package rebuild.
- [x] Independent final scope/spec/security review of exact new files; preserve findings and fixes, then root final joint run and hash manifest. Capture statuses without upgrading archive checks into authenticated backup, fresh DB restore or production deployment.
- [x] Document local import-only API, trusted input and finite IO requirements, failure residue, exact caps and B2/B3 boundaries. Save fixed-source tests/reports/safe evidence, verify archive closed-set, update complete delivery ledger with actual evidence.

## Pre-flight review / execution choice

The plan was checked against the approved design: required streaming, fixed namespace, manifests and database binding, tombstones/known-missing, strict source FDs, raw USTAR budgets/EOF, fresh private restore, drift and large-file evidence all map to the tasks above. B2/B3 and AV are deliberately not implemented by this plan. No conflicting requirement found. User already requested development, so execute now with independent Task1/Task2 workers and root owning Task3/integration; no additional execution-choice permission.
