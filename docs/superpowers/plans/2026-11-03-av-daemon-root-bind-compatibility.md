# Exact file bind propagation compatibility

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development for this one narrow fix and independent review. Root alone handles any eventual native case.

**Goal:** Correct the fixed exact-log recipe's deterministic conflict with the current Engine daemon-root bind validation, while keeping the same single readonly file and all execution limits.

**Evidence/design:** Read outputs/_run/av-bind-propagation-20261103/DOCS.safe.md. Current actual Engine29.5.3 GitCommit285b471 primary source validates both prefix directions and rejects explicit rprivate for a bind under /var/lib/docker. Current new case reports CREATE/transport400/cid=null, not the exact error body. The narrow correction is explicit rslave (one-way), not rshared; the own exact regular-file Source/Target, readonly=true, NonRecursive=true/CreateMountpoint=false and all other fields stay. This follows the user's continuing instruction to fix remaining validation blockers; no new filesystem target, write access, socket, resource limit or execution authority is introduced.

## Global constraints

Current branch codex/ui-redesign, no commit/push/merge, no UI/config/history/account/wallet changes. Implementer/reviewer only small pure tests and scoped files/hashes/tempfiles; no real socket/thread/timer/process/FFI/kernel/Docker/network/library lookup/provider/realpack/production/Git/otheragent/delegation. All shell rtk. Root sole native/resource actor. Do not retry old cases, edit old freezes, infer returned error text or claim actual mount success. All source references already fetched by Root; no network needed.

## Task1: exact rslave request and inspection

**Only mutable:** services/platform/scripts/av_sources/capacity_probe.py and services/platform/tests/test_av_capacity_probe.py. Baseline2/MUTABLE2-BEFORE/PROTECTED16 already saved under outputs/_run/av-bind-propagation-20261103. Other16 source/test files stay byte-identical. New evidence/report here only; Root owns global ledgers.

- [x] Read the complete helper recipe/validation/lifecycle and current test rig plus the original profile fake fixture (read-only). Read saved new failure evidence outputs/_run/av-native-markers-20261103/ROOT-EXACT-LOG-r2-FAILURE.safe.json and ROOT-ENGINE-VERSION.safe.json, DOCS.safe.md. No private case access.
- [x] Meaningful RED with valid exact-log config: explicitly require the recipe's sole BindOptions.Propagation to equal rslave, matching inspected fixture; retain readonly, nonrecursive, no-create, exact derived own Source/Target/resource fields. Old rprivate must fail this positive control, not bootstrap pins. Preserve RED.
- [x] Minimal production change: exact-log recipe Propagation=rslave and exact inspected /probe-log Propagation=rslave. Do not accept a mode set/fallback, remove fields, normalize modes, change paths/program/proof/permissions/resources/flags/API/diagnostics/deadlines or foundation recipe. Keep all marker, raw/policy hash, provenance and cleanup behavior.
- [x] Adapt only capacity fixture's synthetic exact-log top-level Mounts propagation to the actual selected specification; protected profile fake helper remains unchanged. Positive explicit recipe assertion prevents fixture adaptation masking a bad request. Add strict wrong modes (rprivate/rshared/slave/private/empty) independently in HostConfig.Mounts and inspected Mounts; all reject. Existing complete exact-log lifecycle must still pass once with no borrowed stop, plus existing None/False/hash and diagnostic cases. Verify foundation create_bytes and recipe hash unchanged by comparing frozen baseline helper to final for synthetic valid foundation config; capture comparison safely, no private proof edit.
- [x] Run exact focused command: rtk proxy services/platform/.venv/bin/python -I -B -m pytest --noconftest services/platform/tests/test_av_capacity_probe.py -q. All other modules unchanged, so no659 repeat unless a concrete new concern arises. Preserve full RED/GREEN and any failure. Native tripwire accounting stays outside normalized catches.
- [x] Freeze final2 copies, full2.diff, MUTABLE2-AFTER, verifiedPROTECTED16, report.md, commands/results, foundation-byte-equivalence evidence and SHA manifest. Stop editing for Root fresh run/hash/diff and independent spec/quality review. No native case from this handoff.

Actual CREATE/START/mount/file measurement still needs separately reviewed/frozen current source case. The identified unsupported field is a source-level compatibility fact; it is not retrospective proof of either old response body and cannot make any failed receipt pass. Full goal remains intact.

2026-11-03 source gate CLOSED: exact2literal change, Rootfresh104pass6.85s/exit0, protected16/full2diff/foundationbytes verified; independent SPEC/QUALITY PASS. Actual rslave mount/measurement not yet run.
