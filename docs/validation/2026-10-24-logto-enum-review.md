# S5-A3 native enum collector — independent review

Date: 2026-10-24 (Asia/Shanghai).

**Specification verdict: PASS. Code-quality/security verdict: PASS for this scoped enum extension. No blocking or actionable P1/P2 finding identified.**

Reviewed inputs: the frozen enum diff/report, `docs/superpowers/specs/2026-10-24-logto-enum-compatibility.md`, current collector source and enum tests, and the public safe single-source compatibility receipt. This was static independent review: no PostgreSQL rerun, server connection, real IdP access or source edit.

## Exact source binding

- `services/platform/src/platform_service/identity_restore.py`: `ba35a40f67173f64a2279e90f2db7cdeb19859f71d4fe40c5f268104d42c272e`
- `services/platform/tests/test_identity_restore.py`: `befecf59cf7e31ac4583485e2322c73d98753142f15837be499e35b9115b077c`

Hashes were independently read from the current files and match the submitted report and the collector hash in `real-logto-compatibility-safe.json`.

## Specification and implementation checks

- `_enum_inventory` selects every native enum in the controlled schema, independently of whether any column uses it. Separate definitions inventory therefore retains empty and unused enums; the labels inventory records all labels ordered by type identity and relative enum order.
- The enum fingerprint contains schema/name, named owner, ACL, and the PostgreSQL-generated array's schema/name/owner/ACL. It joins the array through `enum.typarray`, verifies native base/array category and `array.typelem = enum.oid`, and requires the same selected schema. These OIDs are used only as catalog references, never included in the returned structure.
- Numeric `enumsortorder` is used only to order labels. The emitted ordered labels retain semantic order without treating reassigned float values or label/type OIDs as portable identity. This correctly permits logical reconstruction of the same enum order.
- The schema allowlist still rejects domains, ranges/multiranges and custom base types. Standalone composites are also rejected by the existing relation-kind gate. Table row-type automatic arrays are tolerated as catalog scaffolding, but composite-typed table columns are not enabled.
- Column admission requires either the preexisting pg_catalog type rule, an enum in the selected schema, or its matching same-schema generated enum array. External same-name enums and their arrays remain unsupported. No cast-to-text bypass was introduced.
- Enum definition query fields have explicit unique aliases. The label query fields (`nspname`, `typname`, `enumlabel`) are distinct, and the new column-admission boolean introduces only one expression field. Thus the existing `_rows` row-to-JSON/dict-values transport does not collapse any new selected field through duplicate keys. This is important because `_rows` itself would still require care for future queries with repeated names.
- Enum/label reads reuse the bounded catalog reader and execute inside the existing repeatable-read read-only transaction. No type creation, catalog writes, function execution or privilege elevation was added to production collection.
- Backup/config protection, policy validation, source/target context distinction, full table hashing, tenant candidate sets, required clients, RLS/full-SELECT visibility and timeout/size limits remain intact. The diff does not alter owner-only proof creation, freshness, proof consumption, target identity matching or session/financial behavior.

## Test evidence assessed

The submitted final focused run is **60 passed in 8.86 s** (existing 42 plus 18 enum cases); this reviewer inspected the tests and did not rerun or add their counts to another suite.

The cases use real synthetic PostgreSQL schemas and assert differing actual type OIDs for equivalent inventories; they compare ADD VALUE ordering with logical reconstruction using different sort-number assignments. Other cases cover unused/empty definitions, missing/extra/type-name/label/order changes, namespace and array element differences, enum owner/ACL differences, unsupported domains/ranges/composites, and foreign enum arrays.

The generated-array ACL case correctly tests PostgreSQL's refusal of independent array GRANT, followed by the observable element ACL difference. It does not fabricate an impossible catalog state. The array fingerprint includes owner/ACL regardless of that server restriction.

## Actual-source evidence and remaining boundaries

The root-provided `real-logto-compatibility-safe.json` is bound to the reviewed collector SHA and reports a successful **single-source read-only inventory**, five tenant users, four applications, both required clients present, and elapsed 0.607 seconds. It explicitly records no exported rows, no proof creation, no restored-target comparison, no identity mapping and `production_ready=false`.

This supports the limited reported compatibility observation for that source snapshot. It is not an independently rerun source observation, a two-database restore proof, proof of a backup's operational provenance, new HTTPS OIDC authentication, actual production migration, writer fencing or a production load test. The original trusted-operator/DB-owner, unsupported-type, cross-schema dependency and broader role/permission limitations remain. A new build/proof must use this schema-hash format; the previous S5-A2 artifact should not be relabeled as containing enum support.
