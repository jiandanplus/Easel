# Current-source capture evidence: independent bounded consistency review

Date: 2026-11-03. Verdict: **bounded pass; no actionable P1/P2 contradiction in the reviewed claims and public evidence**.

This is an independent review of public source bytes, test assertions, safe execution attestations and their consistency. It is **not an independent rerun or observation of PostgreSQL, Docker, private archives, private logs or restored content**. The actual execution outcomes below are Root attestations. This review does not replace a strict native acceptance protocol or expand backup/restore acceptance.

## Reviewed material and permitted work

Read the 13 `*.safe.json` files in `outputs/_run/backup-catalog-capture-20261103/` (12 records plus their manifest), the prior catalog validation `RESULT.safe.json`, both catalog validation/review documents and `2026-11-03-backup-capture-current-source.md`. Inspected the current capture-PG test, integrated test, owned-PG helper, relevant profile/source-facts validation and pipeline/capture call paths. Independently read and SHA-256 hashed every one of the 136 public Python/SQL members named by the whole intent, plus the once-intent and prior-validation source members.

No product import, test run, network, Docker, database operation or resource launch was performed by this reviewer. No `/private/tmp` contents, DSNs, keys or private log/backup contents were read. Only this review document was written. Source paths to private fixtures were inspected as public code, not followed.

## Result and intent correspondence

| Public intent / result | Root-reported outcome | Static correspondence |
| --- | --- | --- |
| `intent.safe.json` / `result.safe.json` | once001: attested_capture, exit 1, 1 failed in 1.49s | Matching exact case selector; failure-frame log digest equals result log digest. Selected metadata records UID501/GID0, against required UID501/GID20. The reported stop at ConfigInputs precedes capture in the current test. This failed attempt remains recorded. |
| `intent-r2.safe.json` / `result-r2.safe.json` | once002: attested_capture, exit 0, 1 passed in 4.47s | Matching exact case selector, source digests and bounded scope; new stage, actual-owner intent [501,20], prior failure explicitly retained. |
| `intent-r3.safe.json` / `result-r3.safe.json` | once003: facts then logto, exit 0, 2 passed in 7.84s | Matching ordered two-case selectors, source digests and synthetic public/Logto scope. |
| `intent-whole.safe.json` / `result-whole.safe.json` | whole001: integrated file, exit 0, 6 passed in 50.03s | Matching file selector and six-case count; source count 136 and scope agree. Original six tuples in current source agree with the delivery report. |

All records report `timed_out=false`; their outer elapsed times exceed their pytest summary times by small plausible process overhead and remain below the intent's 180-second bound. Later attempts explicitly report `old_handle_reused=false`. These are consistent attestations, not independent timing or execution measurements.

The pairing is semantic and documentary: the result records do not embed their intent SHA-256, stage/run identifier, full command, individual passing node IDs or a signed runtime provenance chain. A private-log hash without reading that log cannot establish its execution content. Consequently this reviewer can establish matching selectors, recorded scope and current source identity, but cannot independently prove that a specific runtime process used the recorded intent or that all runtime source bytes stayed unchanged. Those historical execution/unchanged-source assertions remain Root's attestations. The safe manifest protects the reviewed set against accidental byte mismatch; it does not create independent runtime provenance. Under the explicitly bounded attestation model this is a stated evidence limit, not a new P1/P2 blocker.

## Source assertions support the bounded claim

- `validate_profile_catalog` retains the pinned digest equality and translates the specified malformed-data exceptions to `B2Error`; both public and Logto identity branches in source-facts validate catalogs before deriving table names. Capture calls the catalog validator and archived-bundle verification; the valid paths reviewed do not substitute these validators.
- The selected `attested_capture` case registers and attests a current worker, retains a historical unregistered worker, captures real tool output, checks seven fixed file descriptors, the PGDMP marker, post-capture stability and two snapshot keepers. It inspects CAPTURING maintenance history and rejects a rewritten empty worker registry. The `facts` case revalidates captured bundles and rejects an altered dump digest. The `logto` case checks the identity profile, eight enums and post-capture verification. This supports the report's component claims if the reported original tests ran successfully; it is not a claim that all other fault parameters were rerun.
- The integrated source contains exactly `(16,false,public)`, `(20,false,public)`, `(16,true,public)`, `(20,true,public)`, `(16,false,logto)`, `(16,true,logto)`. It is not an exhaustive Cartesian product; 20-migration plus Logto is not among these six.
- The integrated source calls the original `build_backup` with OwnedInputs, ChildRunner and RootPolicy; asserts committed publication, restored OPEN maintenance state, zero permits, exact trailing five audit actions and no child/unknown references. A fresh Python subprocess reads via trusted anchor and commit, authenticates and verifies the archive, then restores files into a private directory. It checks CAPTURING history, source projection keys, `cross_database_atomic=false`, seven fixed files, blob count/presence and the nonempty media SHA.
- The nonempty media path uses the actual service/runtime and ledger with synthetic fixtures, but the HTTP response is explicitly `httpx.MockTransport` with generated image bytes. It proves neither a live supplier request nor real financial activity.
- `ChildRunner` uses the exact fixed full container ID in the tests. Capture invokes pg_restore to render schema/data to stdout (`--file=-`); this is **not import into a replacement database**. The fresh subprocess restores private files only and never starts a restored application.
- `_owned_pg` limits authority to the fixed loopback synthetic base, generates random database/role names and performs exact generated-name cleanup in `finally`. Selected public postchecks attest major16, zero base public relations, zero active owned-test sessions and the same running/noOOM/restart0 container and loopback63623 binding. These postchecks do not enumerate all historical databases/roles or independently prove cleanup of every resource. The delivery report states that limit accurately.

The older validation result's `actual_pg_or_full_capture_verified=false` is explicitly for the earlier malformed-catalog-only run. The later capture report adds new, bounded Root execution attestations without rewriting that earlier record; these statements are not contradictory.

## Independent byte checks

All 136 whole-intent source SHA-256 values matched current public bytes. All five members in each of the three once intents matched current bytes; their four shared members agree with the whole intent. The remaining once member, `test_media_backup_b2_capture_pg.py`, is intentionally outside the whole integrated-file inventory and was checked separately. The four prior validation source members also matched their recorded SHA-256 values; the two malformed-input test files are not claimed as part of the 136-member whole run.

The safe manifest contains exactly the other 12 safe JSON files in that directory, with no missing/extra member. All 12 byte counts and SHA-256 values independently matched. Reference digests:

| Public file | SHA-256 |
| --- | --- |
| `manifest.safe.json` | `9369ae496ecb36207735cc096ee0ce51dca324cbee3d3b06f9aea78fc7857987` |
| `intent.safe.json` | `62edc3ec98a65bafd8a37e955f1079e32e10037ab9f720867535a6245b32e825` |
| `result.safe.json` | `05f4f024c57aa4098847d41a5c771f7157fa34e3c9f07e7fb6bc7014e13452b1` |
| `intent-r2.safe.json` | `9bbdc507b391e0deaf76a879b65a0e45402829123666d4fb64d5162ba6ee8b6a` |
| `result-r2.safe.json` | `ad8c9cf0087c72f26289c97d939fdcea90403a1cc48e39d563bc471ab542a5ea` |
| `intent-r3.safe.json` | `32e2aac925839cbfca80a874d7a3224c7e1c333c20163e435508b909be5c2d20` |
| `result-r3.safe.json` | `15dcde8548060dd8cd7b7b4cd049f19d4d0b9125d49abd089eedb8bfc3863da0` |
| `intent-whole.safe.json` | `3eb2ca4bf2c636f71da57a7c5c821930f5beeea7ed37206d8b207bf6e51623c3` |
| `result-whole.safe.json` | `ace38fdd20d0c0c033a6fcb03750b6e9051d9b0e90cf0370c20577f6351a5dac` |
| `profiles.py` | `11e867a15427136c441562e764f6048a3f0da1df4a54676cca7305f11c059fa3` |
| `source_facts.py` | `f1391a856fe3988902ad84cac0f2467717f5e09384937e34c8cdbd1f001ee4a1` |
| `capture.py` | `8281992395e1584b30e852c789ebe052ffe4d443c76241b911e38ee2d054d2bb` |
| `test_media_backup_b2_capture_pg.py` | `8a8819ff79874d18576200039b3f787c38ed2958c4c24ff3a5d4f82cc7830a82` |
| `b2_owned_pg.py` | `301854d470bf68c829f9a5365d805088e2d14f58fd477101977f98bc7dc69f43` |

The complete 136-member source inventory remains in `intent-whole.safe.json`; this review independently recomputed every entry rather than inferring equality from the result's `source_unchanged` boolean.

## Acceptance limits

The justified outcome is a consistent, current-source-bound public record of Root-attested successful synthetic PG16 capture and six local capture/encryption/committed-publication/fresh-process private-file-restore cases on macOS. This reviewer independently verifies the public evidence/source consistency only. There is no independent runtime observation, replacement-database restore, restored application startup, production data or credits, live provider, installed-wheel/clean-machine acceptance, full Linux/root source factory, full B2/B3/T13/T14, audio/video coverage or production DR closure. No broader gate is closed by this review.
