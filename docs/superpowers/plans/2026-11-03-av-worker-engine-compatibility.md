# Worker inspect compatibility implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this task and obtain an independent review.

**Goal:** Correct two precisely observed Engine inspect representation differences in the original worker validator while preserving all execution and financial boundaries.

**Architecture:** Keep the fixed worker recipe, resources and lifecycle. Accept only the fixed image descriptor with or without its exact platform field; normalize only the unsupported false/null OomKillDisable representation for a policy hash, while retaining a separate complete raw configuration hash. Update the executor's authenticated profile pin after source finalization.

**Tech Stack:** Existing isolated CPython tests, authenticated profile/executor modules; no dependency or infrastructure change.

## Global constraints

Current checkout codex/ui-redesign. No commit/push/merge or UI/config/account/wallet/history changes. Root alone handles any native resources. Implementer/reviewer may use only small pure synthetic tests, file reads/hashes and temporary test files. No Docker, sockets, real timers/threads/processes/FFI/kernel probes, network, models, pack decode, Git commands, delegation, production or native fixtures. Native tripwires must remain active outside normalized exception catches. Existing capacity task has closed source/pure review, but its exact-log actual result remains unresolved; no retry or native worker execution is included here.

## Task 1: exact representation compatibility and authenticated pin

**Mutable files:** services/platform/scripts/av_sources/pack_profile.py; services/platform/scripts/av_sources/pack_executor.py; services/platform/tests/test_av_pack_profile.py; services/platform/tests/test_av_pack_executor.py.

**Evidence:** outputs/_run/av-worker-compat-20261103. Its PROTECTED14.safe.json binds every other original AV dependency and both current capacity files. Four baseline files and hashes are already captured. Do not alter historical manifests/stages/failed cases or external ledgers.

**Interfaces:** Existing validate_container(info,cid,prepared,recipe,*,phase='observe') signature remains unchanged. Existing configuration_sha256 becomes the precise policy hash, and configuration_raw_sha256 is added to returned facts. Created/final raw observation remains immutable. Existing executor configuration equality and exact terminal equality retain their purpose; do not delete or bypass them. Executor source change must be only the final PROFILE_SHA literal, unless a concrete necessary issue is reported to Root first.

- [x] Read complete current validator and its fixture/tests, executor lifecycle/guardian, capacity R4/R5 diffs and Root actual evidence. Relevant files: outputs/_run/av-capacity-20261103/ROOT-ACTUAL-CONTAINER-DESCRIPTOR.safe.json; ROOT-FOUNDATION-OOM-NULL-DIAGNOSTIC-r2.safe.json; ACTUAL-FAILURE-OOM-REVIEW.safe.md; r5-frozen/r4-to-r5.diff; SOURCE-REVIEW-r5.safe.md. Root original-worker blocker list is ROOT-ORIGINAL-WORKER-COMPATIBILITY-BLOCKERS.safe.json. No new documentation/network lookup is needed.
- [x] Meaningful RED: from valid original worker fixtures, add the exact descriptor platform {architecture:arm64,os:linux,variant:v8}, then false-created/null-started transition. Valid controls must fail specifically under the old implementation. Cover both descriptor forms, rejected extra/wrong/missing/type-confused fields, full descriptor retained in hash, present strict False/None only (reject True/0/1/string/list/dict/missing), same policy/different raw hash, input unchanged. Descriptor change itself remains a configuration change: do not normalize away its platform.
- [x] Minimal profile change only. Request OomKillDisable remains False; accept exactly the two descriptor schemas/values. Compute raw hash on complete original observed Config/HostConfig/Mounts/descriptor mapping before normalization. Only copy HostConfig.OomKillDisable null to false for policy hash. No state/resource/mount/log/output/deadline/fence/provenance loosening.
- [x] Update the executor PROFILE_SHA to the actual final profile hash so authentication remains effective. Existing stages/pins are not upgraded or executed. Add a pure end-to-end executor lifecycle regression with the real updated validator and a fake API changing false to null after START; it must exercise configuration comparisons and final exact facts, reach normal injected receipt once and not stop/retry. If minimal fixture adaptation is necessary, keep normal positive controls and native tripwires.
- [x] Run: rtk proxy services/platform/.venv/bin/python -I -B -m pytest --noconftest services/platform/tests/test_av_pack_profile.py services/platform/tests/test_av_pack_executor.py services/platform/tests/test_av_capacity_probe.py -q. All three are covering tests because the new profile pin is loaded by both controllers. Preserve complete RED/GREEN output. No broader unrelated suite required.
- [x] Freeze four live source/test snapshots, MUTABLE4-AFTER.safe.json, verified PROTECTED14.safe.json, complete baseline-to-final four-file diff, report and SHA256 manifest. Report precise commands/results, pin update, trust boundaries and remaining actual gates. Stop editing for Root's fresh verification and independent review.

Root subsequently reviews full diff/source, verifies all hashes and runs the covering command. Source/pure approval will not close exact-log capacity, original actual worker/guardian controls, retention, real source decode, full toolchain, backup/DR or release gates.

2026-11-03 closure: Root fresh267passed29.71s/exit0, complete diff and live/frozen/protected pins verified; independent SPEC/QUALITY PASS. Source/pure task complete only; no native execution or old-stage upgrade. Validation: docs/validation/2026-11-03-av-worker-engine-compatibility.md.
