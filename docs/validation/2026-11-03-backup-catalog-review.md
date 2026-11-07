# Backup catalog validation: independent narrow review

Date: 2026-11-03. Verdict: **bounded pass; no P1/P2 finding in the reviewed change**.

## Scope

Reviewed the current `validate_profile_catalog` in `services/platform/src/platform_service/media_backup_b2/profiles.py:70-78`, the identity catalog check ordering in `source_facts.py:303-311`, and the new malformed-catalog tests in `test_media_backup_b2_profiles.py:20-35` and `test_media_backup_b2_source_facts.py:6-24`. Read `contracts.py` and relevant `bundles.py` JSON/canonicalization contracts for context. No product file was edited. No UI, resource, network, Docker, PostgreSQL, deployment, or restore operation was exercised. Unrelated dirty files were not reviewed.

The narrow git diff returned no content. The parent subsequently supplied two public source members extracted from an archived wheel as a comparison baseline under `outputs/_run/backup-catalog-validation-20261103/`. This reviewer independently hashed and compared those members with the current source. The only source differences are the bounded exception translation and the two moved identity checks; the digest algorithm, constants, and other source text are unchanged relative to those supplied baseline members. Extraction/provenance of the original wheel was performed by the parent, not independently repeated by this reviewer.

## Findings and reasoning

No actionable P1/P2 finding was identified.

- `profiles.py:72-75` still accepts only the known exact profile objects and requires the calculated catalog digest to equal the profile's fixed `CATALOG_PINS` entry. The new exception handler returns no success value on failure and has no fallback to an unknown profile/catalog. Pin mismatch remains a `B2Error`.
- `profiles.py:74-77` confines translation to the digest/pin check and enumerates `KeyError`, `IndexError`, `TypeError`, `ValueError`, and `AttributeError`. These cover malformed row indexing, missing fields, invalid container operations, and malformed canonicalization inputs. It raises `B2Error()` with `from None`; the public error string is the fixed `invalid_backup`. This is not an `Exception` or `BaseException` catch. Operational `OSError`, cancellation exceptions, `KeyboardInterrupt`, `SystemExit`, and `MemoryError` are not included. This conclusion comes from the explicit exception classes, not injected runtime fault tests.
- `source_facts.py:305,310` now invokes the catalog validator before either identity branch reads `r[1]` or `r[2]`. Empty and short column rows are rejected at that boundary instead of leaking `IndexError` through the outer source-facts handler, which does not catch `IndexError`.
- Moving this check preserves valid-catalog behavior by inspection: it is a pure digest/equality check with no mutation; the same profile, catalog, table derivation, image/public-SQL constraints, and inventory checks remain in the successful path. The changed order rejects malformed input earlier. This is a static behavioral assessment, not a successful live-catalog or restored-database demonstration.
- The new tests exercise 14 malformed row variants across the digest's projected catalog fields, plus 6 short-row cases spanning public and Logto identity profiles. They assert the external `B2Error` boundary; the profile test also checks that a marker is absent from the error string. The fixtures deliberately need not describe a valid complete archive because the regression concerns rejection before subsequent archive validation.

## Independently executed verification

```text
rtk proxy env PYTHONPATH=services/platform/src:services/platform/scripts services/platform/.venv/bin/python -B -m pytest -q services/platform/tests/test_media_backup_b2_profiles.py services/platform/tests/test_media_backup_b2_source_facts.py
61 passed in 0.07s
```

This reviewer did not rerun the pre-fix RED state, larger suites, positive pinned-catalog fixtures, operational fault injection, or any live infrastructure check. Parent-reported RED/other-suite results are outside this independent execution claim. These two passing test files do not establish full backup/restore delivery or production readiness.

## Reviewed file SHA-256

| File | SHA-256 |
| --- | --- |
| `services/platform/src/platform_service/media_backup_b2/profiles.py` | `11e867a15427136c441562e764f6048a3f0da1df4a54676cca7305f11c059fa3` |
| `services/platform/src/platform_service/media_backup_b2/source_facts.py` | `f1391a856fe3988902ad84cac0f2467717f5e09384937e34c8cdbd1f001ee4a1` |
| `services/platform/tests/test_media_backup_b2_profiles.py` | `1d58673091eac40be7d4be8bc42183cbcf7290861ca737885d8fe6baaf56e554` |
| `services/platform/tests/test_media_backup_b2_source_facts.py` | `78d9fa796b7244280f3192bdecf293f6f7c1d4de67fa466d1aaed2e662fe7faa` |
| `outputs/_run/backup-catalog-validation-20261103/baseline-profiles.py` | `1bfe494abee5165e244064f7e2d63c030140a704f44db15bd5cb9050b85a96c6` |
| `outputs/_run/backup-catalog-validation-20261103/baseline-source_facts.py` | `e3d7fea5e65e1c2bddd536c1fc2f8cabd6b68e3e216cefbc9fcb7c7d5d759718` |
