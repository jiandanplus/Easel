# Public target-tool qualification: dependency-closure revision

Status: approved Route A is complete for the scoped public-material qualification path. The frozen offline closure and original runtime-once004 passed independent review; Root recorded scoped acceptance and removed that known-terminal helper. This does not qualify a real restore target or complete B3. Host owner/helper development continues under the existing approved plan. The original failures below remain failures.

## Observed failures and architectural decision

Three independent public-container attempts remain failures, with original records preserved:

1. once001 stopped before main start because the inspection check indexed optional `HostConfig.Init`; the field was absent. The never-started container was normally removed, all227 volume identifiers and source process metadata matched, and its idle owner was released.
2. once002 inspected `created` before the original asynchronous attach/start published `running`. Subsequent read-only observation proved the same original container/attach had started. The public container was normally stopped and removed, all227 volume identifiers/source process metadata matched, and its idle owner was released. A pre-stop orchestration error (failed precheck followed by unconditional stop) remains recorded; subsequent actions used a separate successful gate.
3. once003 passed the bounded same-start wait and executed the pinned sha256sum version/loader commands. The loader returned additional **libz.so.1 and libzstd.so.1** dependencies of the public libcrypto stack. The handwritten closure correctly rejected these unlisted libraries. It produced no compatibility success; original owner/container retention and directed cleanup are handled separately.

The first two are operator-boundary errors. The third exposes the remaining design weakness: direct ELF DT_NEEDED of the four tools is insufficient to define the complete transitive native dependency closure. Extending a hand-maintained list one observed library at a time is not the acceptance strategy.

## Route A: derive the complete frozen closure before another runtime

1. Use the already frozen public PostgreSQL ARM64 OCI archive, selected manifest/config, layer digests and final filesystem layout. No download, PG image modification, tool installation, privileges, database access or real credential access.
2. Offline, derive each tool's ELF interpreter and the complete transitive dependency graph, resolving every image alias to its final canonical file. Preserve edges, aliases, modes, ownership, byte counts and SHA256. Reject missing/ambiguous resolution, unrecognized search semantics or unresolved dependency; do not substitute a guessed path or omit a library.
3. Reuse the already qualified full PostgreSQL native-material inventory where the exact original image/path/hash matches; independently prove that the selected four-tool graph is closed. Synthetic tests cover a transitive dependency, shared dependency, cycle, missing dependency, alias escape and unexpected RPATH/search setting. No native runtime action belongs to this phase.
4. Freeze the complete data manifest and an exact qualification command sequence. Independently review it together with the existing fixed public probe/controller, including optional inspection fields, original asynchronous start/held pipes, caps and original deadline. Pure tests must consume the actual saved successful command outputs and the rejected libz/libzstd case.
5. Only after that review, create one new independent public compatibility target. Validate actual loader output/alias resolution/hash against the complete frozen closure, fixed tool versions, UID999/cap0/NNP/proc permission, natural EOF/terminal exit and endpoint source/volume invariants. Any mismatch remains failure; no automatic retry or list widening.
6. Archive and review the actual result, then normally remove only that known terminal public helper. Current failed attempts are never renamed or promoted to success. Real target binding and full B3 restore remain separate gates.

## Alternative

Defer the public tool compatibility runtime while continuing host transport/caller source development with fail-closed policy pins and synthetic tests. Do not claim actual target qualification or full delivery from those tests.

## Unchanged boundaries

Current branch only; no merge/commit/push. No frontend explanatory text. Keep subscription/custom API and user state. Source PG50a remains original. No production/SSH/payment/model/provider/real account operations. No volume deletion, image change, broad prune, or clock/replay waiver. Source/fake, installed/cold, public compatibility, original target-bound execution and restoration evidence remain distinct.

## Recorded scoped completion

The complete frozen manifest is `outputs/_run/b3-target-tools-public-20261104/closure-actual-offline-once003/MANIFEST.public.json` (SHA256 `0b8607dcfadbc133527904ec6da1996be27a47e1e5a125d084f1a6d47b1f5dde`): four tools, eleven canonical nodes and twenty-nine dependency edges. The public runtime checked all four loader closures, aliases and eleven file hashes, fixed GNU coreutils versions, UID999, zero capabilities, NNP, namespace/proc probes and natural terminal exit within its original window.

Actual saved evidence review: `RUNTIME-ONCE004-ACTUAL-REVIEW.safe.md` (C0/I0/M0). Root acceptance and ordinary cleanup: `runtime-once004/ROOT-ACCEPTANCE-AND-CLEANUP.safe.json` (original session6325, same-session terminal555c8c/exit0, container absent). The accepted scope remains fixed public tool startup closure and the exercised namespace/proc probes only; target-bound, SQL and complete isolated restore remain false. These records describe that historical attempt, not a fresh live observation.
