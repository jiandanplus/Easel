# Approved experience issuance wrapper review

Reviewed read-only on 2026-10-23. Source SHA-256: `09ee4a8a347fe0d3f727e2e0fa4d68eaacf244eba01729d90ec43dcf7336852a`.

Verdict: **P2 recovery fix required before execution**. No P1 authorization, amount, or credential-boundary issue identified. This review did not execute the wrapper, connect to a service/database, inspect private target/configuration files, issue credits, or call a model.

## Finding

**P2 — successful issuance cannot recover its receipt after a partial write or lost response** (`issue_approved_experience.py:72–74`, with local persistence at line 80). The script commits the fixed grant before creating the final server receipt with `O_EXCL` and directly writing JSON. A partial write leaves the destination present; a complete server write followed by a lost SSH response or failed local receipt write has the same retry consequence. Re-running the same wrapper safely replays the grant, but then always raises `FileExistsError` instead of returning recoverable evidence. This is not duplicate issuance: the underlying service correctly rejects/replays the fixed key. It is a financial-operation recovery/reporting defect.

Use a private same-directory temporary file, complete and sync its contents, then atomically publish. On an existing final receipt, validate its regular-file/ownership/mode/no-follow properties and validate its target and immutable grant against the current database/CLI result before returning it. Preserve the original key and grant; never issue a replacement allowance to repair a receipt. Partial final files should have an explicit safe recovery path if supported.

## Confirmed boundaries

- The local target producer reads the normal workbench status endpoint, requires the exact central URL and authenticated scope, and stores only opaque scope IDs. The issuance wrapper consumes the private target with `O_NOFOLLOW`, owner and mode checks; the remote process validates its exact five-field shape and product.
- The script uses a fixed remote root, exact schema, environment, operator, reason and issuance key. Wallet/session are taken from the saved normal-login target, then checked against the server identity session and its source transaction. They are not interpolated into shell commands or SQL.
- The CLI revalidates the private fixed policy, explicit owner DSN, actual database host/port/name/schema, schema ownership, and active real OIDC provenance. Its dedicated capability is `internal:issue`; no HTTP issuance or runtime-role permission is added by this wrapper.
- The amount and duration have no script parameter: the reviewed service fixes 10,000,000 microcredits and seven days. The wrapper checks the returned amount, funding source, non-test flag and wallet. It invokes the exact same CLI twice and compares results and snapshots, requires one audit record, balanced entries and zero unchanged payment rows.
- Owner configuration and credentials remain on the server. Child stdout/stderr are captured; only the explicitly constructed sanitized receipt is returned. No old ledger, existing preview configuration or provider credential is read by this review.

The strict snapshot-equality assertion assumes no simultaneous wallet activity during the two replay checks. If the user starts a conversation concurrently, failure of that assertion must be treated as inconclusive receipt validation, not proof that the grant failed and not permission to change the key. Current scope is issuance readiness only; there has been no execution or successful grant claim here.

## Frozen recovery fix re-review

Current source SHA-256: `fe28376bccbde20476ce5f581fa8f42ce9fe182aa5b83aa937ed3a3a55b99b06`.

**PASS — the above P2 is closed for this revised source.** No remaining P1/P2 findings in this narrow wrapper review.

- A new server receipt is completely written to an exclusively created 0600 temporary file, flushed and fsynced, then published through a non-overwriting hard link. The directory is fsynced. Failure before publication leaves no partial final JSON; a retry retains the same financial idempotency key. An existing final encountered during publication must match the complete result.
- The existing-receipt branch runs before either invocation of `issue()`. It reads the final file with no-follow/regular-file/owner/mode checks, requires the identical saved target and environment, and checks the actual immutable audit row for exact wallet, session, key, user, tenant, operator and reason. Its grant view must match the saved grant. It also checks one grant, 10,000,000 issued microcredits, balanced entries and zero payment rows. It then returns the original proof plus a separately labelled current wallet without issuing anything.
- This allows recovery after a lost SSH response or local persistence failure even if legitimate later usage has changed available/held/spent. Local safe receipts are likewise written privately to a complete flushed/fsynced temporary file and atomically replaced; an existing receipt must match the wallet/environment and private-file checks.
- Fixed amount/duration, real-login target binding, owner-only service validation and server credential boundary remain intact. The no-concurrent-wallet-activity boundary for the initial two replay checks remains as documented above; a comparison failure never authorizes changing the key.

This is a source-only re-review. No script, network, service, grant or model operation was executed by the reviewer, and no private configuration or saved target was read. Syntax checks were reported by the implementing agent, not rerun here. PASS establishes readiness of this source under the approved scope, not a claim that the user has logged in or credits have been issued.

## Opaque-ID CLI encoding fix re-review

Current source SHA-256: `edf7551514545efe9e2b1e0a94be8ddccf81a8fd4a467ffcfdb811d9860dc369`.

**PASS.** The wrapper now passes `--wallet=<opaque value>` and `--session=<opaque value>` as single argv entries. This preserves valid opaque IDs beginning with a hyphen as option values instead of letting argparse interpret them as flags. No shell interpolation is introduced: the CLI is still invoked with a subprocess argument list.

The reviewer independently reversed exactly these two encodings in memory and obtained the prior reviewed source SHA-256 `fe28376bccbde20476ce5f581fa8f42ce9fe182aa5b83aa937ed3a3a55b99b06`. This verifies that the fixed environment, target, original idempotency key, amount/duration authority, audit checks and receipt recovery are otherwise byte-for-byte unchanged.

The new `services/platform/tests/test_internal_grant_cli_arguments.py` exercises actual `cli.main` with a mocked operation and no database: the positive case preserves leading-hyphen wallet/session values, and two negative cases prove the old separated form exits with code 2 before the operation runs. The implementing agent reported 3 passed in 0.03 seconds; this reviewer read but did not rerun those tests. No wrapper/server/grant/model operation or private target read occurred during this re-review. The root reported that the preceding failed invocation never entered the operation and left all financial counts and balances zero; that runtime evidence was not independently accessed here.

There is no remaining review blocker to continuing the already authorized fixed allowance with the same target and same key. This conclusion is execution readiness, not a claim that issuance succeeded.
