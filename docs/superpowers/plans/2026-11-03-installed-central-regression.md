# Current Installed Central Candidate Full Regression

> **For agentic workers:** Use subagent-driven-development for pure partition implementation/review. Root alone collects or executes pytest, reads any private test DSN, operates PG/Docker, starts processes or performs actual artifact checks. No commit/push/merge.

**Goal:** Execute the complete current central test-file set against the already installed a6d555 candidate, preserving special fixture isolation and the user-approved replacement of the historical mixed 200ms cancellation criterion.

**Architecture:** Keep all product/tests/fixtures unchanged. Determine a conservative transitive local-module dependency graph from ASTs. Test files reaching b2_owned_pg run in a dedicated pytest process, because this explicitly isolated helper clears PG*/EASEL* environment and has session-scoped fixtures; remaining files run in another fresh process. Both groups together must cover the entire fixed test-file inventory without duplicates or omissions. No group may be represented as the other group's execution.

**Tech Stack:** Current immutable installed wheel a6d5553286c69b3a62f8ae6d7f67f8a43b615b0cd2c0ef67bce13c45cb59e268, Python3.12, pytest/pytest-asyncio, existing explicitly owned synthetic local PostgreSQL and reviewed public tool inputs.

## Global Constraints

- Branch codex/ui-redesign; no frontend text, original17868 state, product code, SQL, packaging wheel, fixture authority/grants, credentials or global package changes.
- Full original delivery scope remains. This plan is a regression boundary, not B3/full T13/T14/AV19/production/desktop/signing or release acceptance.
- Preserve the historical old slow function and bytes. User-approved replacement mapping is outputs/_run/b2-causal-linux-preparation-20261102/FUNCTIONAL-ACCEPTANCE-MAPPING.safe.json. The old function is explicitly recorded as deselected from revised functional acceptance, never relabelled PASS; both new named cancellation tests remain selected.
- No production DSN/discovery/fallback, provider calls, payment, remote SSH, or inherited environment. Root verifies exact owned loopback authority before any test. No enabling real-restore flags or extra model capabilities.
- Pure implementation/review must not import product/tests/payload modules, collect pytest, connect/network/DB/Docker, run subprocesses, execute harness main/prepare, or read private dirs/configs/credentials. Parsing source bytes with stdlib AST and synthetic dictionaries is allowed.
- Do not edit b2_owned_pg.py or relax its environment-clearing boundary. The issue is inappropriate mixed-process orchestration, not a proven product defect.
- No old-case retry or repeat of original200ms request. Unknown/failed/late actual outcomes stop that batch and preserve original handles/logs.

## Task 1: Pure deterministic file partition and review

**Create:** outputs/_run/central-installed-regression-20261103/codeonly/suite_partition.py, test_suite_partition.py, IMPLEMENTATION.md, manifest.safe.json and review.diff. Keep all tests outside any frozen runtime payload.

**Interface:** `partition_sources(sources: dict[str, bytes]) -> dict[str, list[str]]`, returning exactly `general` and `owned_pg`, each lexically sorted `.py` basenames.

Input comprises every top-level regular Python source in services/platform/tests, keyed by its exact basename. Reject non-dict/non-bytes, path-bearing/duplicate-normalized/invalid basenames, missing b2_owned_pg.py, malformed AST, and no tests. Test files are exactly test_*.py. Use AST Import and ImportFrom nodes (including nested imports), connecting each present local top-level module. Also conservatively connect a string constant when it is exactly an existing local module name, to cover literal dynamic imports. Relative local imports and `from . import name` must either resolve unambiguously or reject; no execution/import resolution. An explicit unresolved b2_* or test_* module import must reject rather than silently become external. Standard-library/product/third-party imports are external graph leaves and never loaded.

Reachability is iterative/cycle-safe. Include every test exactly once: a test reaching b2_owned_pg goes to owned_pg, all others to general. Assert disjoint groups and exact union of all test basenames. Empty one group is permitted for synthetic tests; actual Root adoption separately requires both groups and fixes all184currenttestfiles. Source order must not affect the result. Do not special-case filenames or delete tests to obtain partition success.

`validate_partition(sources, value) -> dict[str, list[str]]` must require the exact two keys, actual lists of unique basename strings, sorted order and exact equality to a freshly computed conservative partition; reject bool/int/extra/missing/duplicate/cross-group/wrong-dependency variants. Returning a dictionary of hardcoded success flags is insufficient.

- [x] First add meaningful synthetic unittest RED cases against an absent implementation: direct owned import, transitive helper import, nested import, literal dynamic import, cycle/diamond graph, disconnected normal tests, stable order, malformed/unsafe inputs, unresolved local fixture imports, exact group validation. Use real ASTs of synthetic source strings; no module-import fakes or network.
- [x] Record actual RED output. Implement the smallest stdlib-only pure algorithm, then observe full GREEN. No resource-dependent RED.
- [x] Test independence: changing a dependency from owned to normal changes the actual returned group; missing a transitive owned helper fails closed rather than appearing general.
- [x] Freeze source/test hashes and exact RED/GREEN count/output; generate real diff from the initial empty implementation state. No git writes required.
- [x] Independent source/spec/quality review; reviewer does not repeat unchanged tests. Root reviews full source/results and recomputes actual184file partition without importing tests/product.

## Task 2: Root actual preparation, full-file collection and execution

Only after Task1 review, Root freezes the complete selected source/test/helper/script/Easel-contract/Pi public inputs, installed139records and reviewed runtime/tools. Preserve original sources; use outside-checkout execution and -I/-B. Product module origins in each actual pytest process must resolve to the installed candidate. Code inspection tests may read exact frozen workspace source bytes, separately disclosed; that is not product import provenance.

- [x] Inspect actual partition dependency/fixture relationships and reject any group mixing the environment-clearing fixture with unresolved default conftest DB authority.
- [x] Verify selected exact owned synthetic PG endpoint and running service identity read-only. Ensure protected source/public relation state before execution. Never print DSN or role secrets.
- [ ] Freeze fresh once-only run paths, exact groups, input hashes, environment allowlist, private logs, per-group wall deadlines and artifact references. Actualcollection/execution is Root only. A group failure stops later groups and preserves all evidence; no retry/reset.
- [ ] Validate actual collected file set equals its frozen group; all original current test files are represented once across the two groups. Only explicitly mapped old mixed200ms node may be deselected. Require the two new cancellation nodes present. Preserve all explicit skips as missing gates, with node/reason records, not passes.
- [ ] Run each group once with real unchanged tests, installed-module-origin checks and before/after input/installed hashes. Pi SDK and actual-wheel/backport artifacts must either be explicitly verified/provided or be recorded as real skips; no made-up completion or broad acceptance from a summary line.
- [ ] Root reviews any actual failure before proposing a product/test change; preserve failed sample. Successful full group counts stay separate, with exact union/coverage mapping and no aggregate claim of one pytest session.

## Task 3: Bounded audit and handoff

- [ ] Independent actual-safe evidence review of precise installed origin, source, collection, group/skip/deselect, original deadlines and terminal/hash references; no private rows/keys/log exports.
- [ ] Update full execution ledger and candidate regression report with all failures and unverified gates. Broader delivery goal remains active unless every original requirement is independently proved.

