# Current central catalog candidate: independent artifact and runner review

Date: 2026-11-03. Initial verdict: **bounded PASS; no blocking P1/P2 finding in the candidate inventory or reviewed runner source**. This verdict permits Root's already-authorized single synthetic installed-wheel run; it does not assert that run has passed.

## Scope and evidence boundary

Reviewed the current 001–020 candidate under `outputs/_run/central-catalog-candidate-20261103/`, the archived baseline wheel under `outputs/_run/b2-wheel-candidate-20261031-q1/current-001-020/`, current public Python/SQL/pyproject inputs, original `test_media_backup_b2_integrated.py` and `b2_owned_pg.py`, the implementation plan, and the preceding narrow catalog review. This reviewer did not access private stages, installed runtime files, DSNs or logs; import product/runner modules; execute tests, build tools or the runner; or perform network, Docker, database or resource operations. Only this review document was edited. No memory was used.

The independent execution here consists of standard-library ZIP, CSV, hashing, text comparison and AST parsing against public artifacts. Runtime/origin receipts are Root-supplied evidence, not independent runtime observation.

## Independently verified artifact

- Candidate SHA-256: `a6d5553286c69b3a62f8ae6d7f67f8a43b615b0cd2c0ef67bce13c45cb59e268`; size **496103 bytes**.
- Baseline SHA-256: `c23cb4cb555e24f96c509abec649fd19fa1e750c2e83b7861d05286eb3a1a6dd`.
- **140** unique relative, traversal-free ZIP member names; no directory, symlink or special-device members; complete CRC check passed. Hatchling's three dist-info members carry permission mode `0644` with no explicit Unix file-type bits, a conventional regular ZIP entry representation; the other entries explicitly carry regular-file mode. The initial checker was adjusted to accept unspecified file-type bits and still reject all special types.
- RECORD has one row for every member. All **139** non-RECORD entries match SHA-256 and byte size; RECORD's own hash and size are blank. Every published inventory row also matches the wheel bytes.
- Exactly **20** migration SQL members are present.
- Full member-set and byte comparison against the baseline found no added or removed member. The complete changed-member list is `platform_service/media_backup_b2/profiles.py`, `platform_service/media_backup_b2/source_facts.py`, and `easel_central_platform-0.1.0.dist-info/RECORD`.
- All **137** members mapped from the declared input manifest match its hashes and sizes. Independently rehashed **131** current public Python/SQL/pyproject inputs and found them equal to the input manifest. Static frontend/license member identity was checked against the supplied input manifest rather than separately reading current frontend/license files.
- The two changed Python members contain precisely the previously reviewed bounded catalog exception conversion and identity-validation ordering adjustment. No digest pins, migrations or other member content changed.
- WHEEL declares `Generator: hatchling 1.32.4`, `Root-Is-Purelib: true` and `Tag: py3-none-any`; METADATA retains Python `>=3.12,<3.13` and the existing dependency requirements. This tag is packaging metadata, not operating-system acceptance evidence.
- Independently hashed `build.lock` to `0a90f6a833961f46e75ea37256f2d4f4973f292585222016d2aa4d3fcc809c8f`, matching the build receipt. Its six builder packages are version-pinned with SHA-256 hashes. Offline isolated PEP517 execution and package acquisition remain Root-attested; this reviewer did not rebuild or inspect private acquisition logs.

## Installed runner source readiness

Reviewed `run_installed_whole.py` as text and parsed its AST without importing or executing it. SHA-256 `92e5f532edfa60b7672333f3c58bad4da6938e57a29d1b2cc96e3506807418a4` matches the supplied preflight receipt.

- The runner has fixed fresh-stage paths and no candidate fallback or alternate DSN. Candidate wheel and all installed distributed member hashes must match before running. All copied test harness hashes are also checked first.
- Exclusive creation of the fixed run directory and intent/result files prevents normal re-execution of the same invocation. GID 20, private directory mode and a 2 GiB free-space minimum are checked before launch. The test log and same-process origin proof use exclusive mode-0600 creation.
- The subprocess uses the new venv interpreter with `-I -B`, a minimal explicit environment and an external harness working directory. Before `pytest.main`, it imports the product package, DB module, both changed modules and `media_backup.contract`, requiring each origin beneath the new candidate site-packages path. That proof is produced inside the actual pytest process.
- The original integrated module still defines all six 016/020, empty/nonempty and public/Logto combinations. Original assertions and its fresh-reader subprocess timeout remain intact. The helper pins the existing synthetic PG authority and creates unique helper-owned databases/roles, with cleanup in `finally`. No production authority selection or discovery fallback was introduced by the runner.
- The runner preserves `--maxfail=1`, requires the exact `6 passed` summary with no skipped marker, zero exit status, no outer timeout, terminal observation before the original 180-second deadline, isolated-process origin proof and unchanged distributed member hashes. There is no automatic retry and no weak nonzero-exit success path.
- The safe intent records candidate, runner and test hashes, argv, environment and the original deadline. The result links the exact intent bytes by SHA-256 and retains private log hash/size. Shared locked dependencies and incomplete full delivery are explicitly disclosed.

## Nonblocking limits and failure handling

The outer deadline terminates the subprocess process group with SIGTERM/SIGKILL. This is not independent proof that Docker-exec server-side children or every temporary database would be cleaned after an abrupt kill. Ordinary test completion follows the existing helper's cleanup; do not extend that to timeout cleanup guarantees.

Preflight assertion failures, missing origin data, excessive logs, filesystem errors or failures while terminating a child can stop the runner before it writes a result receipt. These fail closed and must remain failures or incomplete evidence. A missing result is not permission to rerun the fixed invocation. The log-size check occurs after collection and is not a live output cap.

The supplied `installed-origin.safe.json` reports new-prefix origins, 139 checked distributed members and twenty migrations. `installed-preflight.safe.json` inventories installer/venv extras. `installed-pure.safe.json` reports Root's **80 passed in 1.09 s** from the external harness. These receipts are consistent with the reviewed artifact and runner design, but their runtime facts are Root-attested. A fresh venv reusing locked dependency files is not a clean-machine dependency-install test.

## Remaining gates

The integrated runtime result was pending the initial review; its later bounded consistency audit follows below. No production/B3, database/ACL restore, complete Linux/T13/T14/AV19, signing/notary, desktop, merge or full-release acceptance claim follows from this document.

## Actual installed run: subsequent public-receipt audit

Final verdict: **bounded PASS for artifact/runner review and consistency of Root's actual installed synthetic run receipts**. No P1/P2 blocker was identified. Root reports its actual tool process terminated with exit code 0. This reviewer read only the newly published `installed-whole.intent.safe.json` and `installed-whole.result.safe.json`, rehashed the existing public runner, original test and wheel, and executed standard-library consistency assertions. Private stages and logs remained unread; no runtime was independently rerun.

- Independently rehashed the exact intent bytes to `e45b83dba2fdd1507180f60e1507a515660a5c6da5161f2b4f174e3913d51d19`, matching the result link. Intent runner SHA matches the reviewed source, original test SHA `eea8f11b6609e7f70b82e8e9bffdae2ee7828ec2ccc9c54632a0a23d3578357d` matches current public test bytes, and both receipts name the independently verified candidate SHA.
- Intent argv preserves the reviewed new venv interpreter, `-I -B`, external harness, exact six-case module and fixed private basetemp. Intent deadline is exactly 180 seconds after its monotonic start; run identifiers match between receipts.
- Result records **6 passed in 49.34 s**, **49.618 s** outer elapsed, `exit_code: 0`, `timed_out: false`, and observation before the original deadline. All success checks in the reviewed runner are satisfied by the result fields.
- Same-pytest-process proof records `isolated: true` and all five inspected product/module origins under the new candidate site-packages prefix. The result records all 139 distributed candidate hashes unchanged after the run. These are actual Root-generated runtime receipts, independently checked for consistency; they are not independent filesystem observation by this reviewer.
- The private 99-byte log is represented only by size and SHA-256 `84087ddc5dce56aa96d5849d7bdd1db337a2a3c330278900c3b8211d1ec4448e`. Its contents were not read or independently authenticated against the private file.

The supported scope remains installed current 001–020 wheel, existing shared locked dependencies, original six synthetic PG/public-Logto/media combinations, MockTransport for provider requests, and private file restore. Fresh database/ACL restore, production/B3, complete Linux acceptance, clean-machine dependency installation and full release remain unproven by this run. An integrated pass does not close those gates or retroactively alter frozen baseline evidence.
