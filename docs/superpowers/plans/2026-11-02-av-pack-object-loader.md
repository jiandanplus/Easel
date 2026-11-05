# AV bounded pack object loader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this task, then independent specification and quality review. User authorization is continued current-branch development; do not commit/push/merge.

**Goal:** Fill the missing bounded offline pack-to-read_object adapter in the already approved AV acquisition Task3 and prove integration with the existing commit/tree verifier using synthetic complete packs.

**Architecture:** One focused stdlib module validates a pinned private pack file, incrementally inflates entries within limits, resolves in-pack OFS/REF deltas and exposes immutable raw Git objects to the unchanged verifier. It does not fetch, extract, execute or adopt source, or authenticate tags/signatures. Existing public candidates remain untouched until a separately frozen hard-isolated real-input execution.

**Tech Stack:** Python3.12 stdlib hashlib/struct/zlib/os/stat/time; existing git_objects.py read_object protocol; pytest pure synthetic fixtures. Official protocol notes in outputs/_run/av-pack-loader-20261102/DOCS.safe.md.

## Global Constraints

- Same codex/ui-redesign branch. Preserve original frontend/UI, login/history/config, subscription/custom modes. No new explanatory UI copy. No commit/push/merge, network, Git CLI, subprocess, Docker, DB, SSH, real credentials, model, media generation or production actions.
- Only new pack_store.py and test_av_pack_store.py plus named report/logs owned by implementer. Existing six original/helper modules and all existing tests remain byte-identical.
- No real acquired pack decode or even reads of /private/tmp/easel-av-public-batch-20261102-r1 in this task. All fixtures synthetic small owned temp files. No import/execution of application modules or conftest. Root owns any later actual executor/resources.
- Default ceilings, callers may only tighten with positive exact integers (bool rejected): pack 64MiB, inflated entry data aggregate 128MiB, materialized resolved objects aggregate128MiB, individual blob/delta-program16MiB, commit/tree/tag4MiB, object count60001, delta depth64, cooperative600seconds. Pack limits are distinct from original verifier's per-path loaded128MiB/content512MiB/member30000 budget; do not sum overlapping counters or claim byte caps prove AS512MiB/CPU30s/hard wall/network isolation.
- Paths: source is an owned regular0600 single-link file; immediate parent owned0700; reject symlink in all traversed components, special files, existing stat/hash drift; no writes or materialization. Single trusted writer, not malicious same-UID protection. Read requests bounded at <=65536, never unbounded read/decompress. Check size before allocation, actual bytes/SHA256 and trailer SHA1, inode/size/mtime/ctime before/after parsing and named inode. Expected pin inputs strict positive size<=pack cap and 64 canonical lowercase hex.
- Accept PACK versions2/3, nonempty declared count<=60001, exact count and exact container consumption before20-byte trailer; no trailing junk. Type1commit/2tree/3blob/4tag, OFS_DELTA6, REF_DELTA7; rejectreserved0/5, truncated/overflow sizes/references/zlib, no extra concatenated stream. Every inflate max_length must be positive and bounded by remaining object/aggregate budget plus one detection byte; never max_length=0 or unlimited flush. Require eof and exact declared uncompressed size.
- OFS_DELTA uses negative distance from its own entry-header offset, must reference an actual prior entry boundary; zero/underflow/inside-entry offsets fail. REF_DELTA resolves any full in-pack OID including forward references; missing thin bases, cycles/unresolvable references fail. No external object fallback. Resolve with bounded depth/work and no unbounded recursion/repeated inflate.
- Delta source-size/target-size little-endian 7-bit varints must be bounded; exact source size, target size budget before output allocation. Copy flags retain byte positions, zero copy length means65536; copy extent within base, insert extent within delta, exact target output and complete instruction consumption, opcode0 rejected. Count every inflated program and resolved object allocation in corresponding counters; no partial successful store on failure.
- Recompute SHA1 of canonical type SP decimal_length NUL raw content for every resolved object; detect sameOID/differenttype-or-content collision, permit identical repeated objects with correct per-entry accounting. Object identity here means content integrity only. Do not claim signed provenance or rewrite existing acquisition receipts/objects_verified flags.
- Use one finite nondecreasing cooperative clock, check before/through parsing/resolution and before returning store; no reset/renewal. Clearly mark source_identity_verified/build_inputs_adopted/os_resource_limits_verified/dns_ip_egress_verified false; preserve exception/cancellation without returning success.

### Task 1: Bounded pack store and synthetic verifier integration

**Files:**
- Create services/platform/scripts/av_sources/pack_store.py
- Create services/platform/tests/test_av_pack_store.py
- Write outputs/_run/av-pack-loader-20261102/TASK1-REPORT.safe.md plus RED/GREEN safe raw logs

**Interfaces:**
- Consumes unchanged git_objects.verify_commit_tree(commit_oid, read_object, limits=Limits()), where read_object returns (type:str, raw:bytes).
- Produces PackLimits dataclass, PackError(ValueError), load_pack(path, *, expected_size, expected_sha256, limits=PackLimits(), _clock=None) -> PackStore. PackStore.read_object(oid) returns exactly that tuple, rejects malformed/missing OID; report/summary contains only size/hash/counts/depth and explicit unverified flags, no raw source contents. Return objects immutable, no mutable backing index exposed. _clock only explicit offline seam, report labels it.

- [x] Read original full git_objects.py, bounded_fetch.py/smart_http.py pack container semantics, and DOCS.safe.md before code. Record protected six module hashes and existing verifier test hash.
- [x] Write an initial failing behavioral test with explicit missing-module assertion, not collection import failure. Build real compressed synthetic pack bytes without Git CLI. Example integration:
```python
def oid(kind, raw):
    return hashlib.sha1(kind.encode()+b' '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
# blob=b'hello\n'; canonical tree=b'100644 note.txt\0'+bytes.fromhex(oid('blob',blob))
# commit has tree header, author/committer and full message; pack all3 zlib entries.
store = load_pack(path, expected_size=len(pack), expected_sha256=hashlib.sha256(pack).hexdigest())
manifest = verify_commit_tree(commit_oid, store.read_object)
assert manifest['entries'][0]['sha256'] == hashlib.sha256(b'hello\n').hexdigest()
```
- [x] Run exact command below and retain complete raw RED; confirm missing functionality is cause. Implement the minimal real parser and store to pass this path, then meaningful RED/GREEN for delta and rejection families.
- [x] Test versions2/3, empty blob, all base types, OFS multi-byte distance, REF forward base, mixed delta chain, duplicate identical object, type inheritance, zero-size-copy65536, noncontiguous copy flag positions, tag/blob/tree bytes preserved. Synthetic complete commit tree includes symlink and gitlink; old verifier records but never loads gitlink.
- [x] Reject wrong pins/trailer/magic/version/count/extra bytes, header overflow/reserved types, truncated zlib/invalid codec stream/size mismatch/bomb before large output, OFS zero/underflow/nonboundary, REF missing/thin/cycle, malformed/truncated delta varints/opcodes/copy/insert, source/target mismatch, exact per-object/aggregate/depth boundary and one-over. Recompute outer checksum/pin when testing inner corruption so cases exercise intended parser.
- [x] Test hostile source file metadata/symlink/parent/ancestor/hardlink/FIFO without blocking, observed source mutation, bounded read/decompress calls, strict limits/count/pin/OID types, decreasing/nonfinite/expired clock, interrupted parse. Use tiny budgets, no resource-heavy synthetic files.
- [x] Run `rtk proxy services/platform/.venv/bin/python -I -B -m pytest --noconftest services/platform/tests/test_av_pack_store.py services/platform/tests/test_av_source_git_objects.py -q` (pure only); save GREEN exact output/exit, source/test hashes, protected hashes before/after, API/report fields, limitations. No success claims for actual public objects/Linux/signatures/fullAV19.
- [x] Root full source/test read, fresh focused execution, then a fresh independent specification+quality reviewer with full new-file diff, plan and report. Fix batched actionable findings with targeted RED/GREEN then re-review. Record completed component in durable checkpoint.

## Subsequent actual gate

After this component's review, Root must separately freeze a specific hard-isolated offline executor, exact staged code/input pins and resource scope before decoding retained real candidates. It must bind actual libplacebo full tree/gitlink to the paired Vulkan-Headers commit and expectedtree, then signed annotatedtag tocommit; no private deployment or existing pending remote payload scope is expanded. This plan does not close full AV19, original76-token FFmpeg9.0.2 capabilities, B2/B3/T13/T14/DR or final desktop release.

## Task1 已执行结果

源码30fa4928e766dfc4137f0b367d5d134c1523a06bc7f7eed4827fbc77d15ca3a6，最终测试78a3fe8d452d6f1f9e4382f5fe4457c043b986c993013aad27d67fed957bd9cc。Root204passed/0.45s；一处测试fixture图错误经真实RED修正，历史199记录保留。独立规格/质量复审PASS，无未关闭发现。见docs/validation/2026-11-02-av-pack-object-loader.md。仅Task1组件闭合，Subsequent actual gate仍未实现/运行，完整目标保持原范围。
