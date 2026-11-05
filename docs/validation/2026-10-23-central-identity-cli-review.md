# S5-A2 independent CLI review

Scope: read-only source review of `identity_migration_cli.py`, `__main__.py` migration routing, 25 CLI tests, and the actual `Database`/installed pool call seam. No PostgreSQL tests, private configuration, network or mutations performed by this review. Collector implementation and its separate 42-test focused result are not counted as independent CLI acceptance.

Final verdict after the narrow fix: **PASS for the reviewed private CLI contract**. The initial P2/P3 findings below are retained as history and closed by the final re-review. No remaining blocking finding in this scope. Functional authorization and data-boundary design match the stated plan.

## P2 — Pool diagnostic logging bypasses the fixed CLI failure output

`services/platform/src/platform_service/identity_migration_cli.py:61–62` constructs and opens the real `Database`, which uses `AsyncConnectionPool`. The installed `psycopg_pool/pool_async.py:758` logs `logger.warning("error connecting in %r: %s", self.name, ex)` during failed connection/retry, before the exception reaches the CLI's fixed stderr handler at `identity_migration_cli.py:137–139`.

Consequently the CLI can emit raw libpq diagnostics containing private routing, database/role names or certificate-file paths, despite the promised fixed error contract. Existing tests mock `operate_identity_migration` to raise; they do not exercise pool logger output. This is not a claim that a real credential was leaked in this task.

Requested fix: suppress or replace pool diagnostics with fixed text in a tightly scoped private CLI operation, restore logging state afterward, and add a pure synthetic canary logger regression proving no raw exception output escapes through logging. Do not modify general Web-service logging globally.

## P3 — Pool open failure bypasses cleanup

`identity_migration_cli.py:62` awaits `db.open()` before entering its `try/finally` at line63. A failed/timeout opening pool bypasses `db.close()`. The tested owner-rejection path does close the pool, but it begins after a successful open.

Requested narrow fix: move open inside the `try` and test failed open still invokes close. The command-level asyncio runner eventually tears down its loop, so this is not an observed persistent production connection leak; explicit cleanup avoids outstanding worker resources for direct async callers and documents the intended lifecycle.

## Checks without further findings

- Explicit `CENTRAL_MIGRATION_DATABASE_URL` is mandatory before private restore config is read. Runtime/owner host, port and database routing are compared; service/hostaddr/options indirection is rejected. This is configuration binding to a trusted direct database endpoint, not an independent runtime-connection attestation against a malicious routing proxy.
- Actual current_user=session_user and schema-owner verification runs before either IdP connection opens. Core service repeats owner/capability checks before financial-independent identity mutation. No runtime Web identity is promoted through the CLI.
- Exact private restore config parser is reused. CLI accepts a config-file path, proof ID, key, reason and operator; no subject/email/arbitrary mapping parameters. Dedicated command routing occurs before the generic main parser for supported command-first invocation.
- `inspect` and `apply` only use config.policy and the central database; collector is called only for restore-proof. Reading config does not read backup or open IdP connections.
- `_safe_summary` filters fields and validates IDs/digests/counts/bools/timestamps before printing. Unknown fields are omitted. No full candidate IDs, DSNs or raw restored rows are printed by this projection.
- Idempotency/proof consumption are delegated to core rather than inferred from a successful process exit or a user-authored proof file.
- Current CLI test evidence was provided by root (25 focused / 65 with drill boundary); this reviewer did not rerun or merge those counts with collector tests.

Remaining release boundaries: private config/backup and real restore provenance remain operator-trusted; new HTTPS signed login and real migration are not validated by these source tests. Ordinary `--help` usage is not sensitive input; unsupported malformed command ordering outside the dedicated command-first route was not represented as a new supported private interface.

## Final narrow re-review — findings closed

Re-read the final CLI and new regression. `_private_database_logs` disables the two actual installed driver logger names (`psycopg` and `psycopg.pool`) for Database construction/open, owner check, operation and close, then restores their prior disabled states in `finally`. This is appropriate for the dedicated single-operation maintenance process; it is not introduced into a concurrent Web process.

Pool open is now inside the try/finally, so a failed open still invokes close under the same diagnostic privacy boundary. The new pure test emits a private canary through both actual logger objects during a fake failed open, checks no canary was logged, verifies close, and verifies logger states restored. Root reports RED1/25 then GREEN26 CLI (66 including the separate 40 drill-boundary tests). This re-review inspected that regression without rerunning PG or connecting externally.

Final reviewed SHA256:

- `identity_migration_cli.py`: `5207c8e9b6f002cf64deaed951c358fcae53fccef50c84a64361206a70c9d740`
- `__main__.py`: `cddfbadf100a3873f9c6fcd2bfe2da8f4b1544d8938cdf6fd472f525510de448`
- `test_identity_migration_cli.py`: `085e3d13382b92e72144b8ffd815430133d5966ab3de8de79dbf159b20606d35`

This PASS covers code/contract review only, not real issuer migration, real administrator login, restored production ownership, or financial cutover.

## Documentation consistency review

Read-only review of `services/platform/IDENTITY_MIGRATION.md` and `services/platform/deploy/README.md` against frozen collector `2801c7fe...`. No source/document edits or database operations by this review.

Verdict: **PASS with small wording clarifications recommended**, no newly found blocking release-boundary claim. The documents correctly require actual schema-owner authority, protected operator credentials, pg_control_system EXECUTE and full SELECT/RLS visibility, same-version/schema trusted restore, actual Logto compatibility verification, separately observed signed login, old-session handling, and explicit writer fencing. They do not claim a production capacity test or that preserving sessions establishes a cutover barrier. The `--no-owner --no-acl` examples already say that ownership/permissions must be separately restored.

Specific suggested clarifications sent to root:

- `IDENTITY_MIGRATION.md:23`: say absolute paths, no symlink in any path component, single-link regular file (`nlink=1`, no hardlinks), current effective user ownership and exact 0600. These reflect the actual file opener, beyond simply saying the final file is not a symlink.
- `IDENTITY_MIGRATION.md:19`: narrow the broad object owner/ACL statement to the relation/function owner and ACL representations actually fingerprinted. Schema ownership/default privileges and complete role memberships still require the separate restore checklist; the collector does not fingerprint every cluster authorization fact.
- `IDENTITY_MIGRATION.md:21`: if describing the 180-second bound, call it the PostgreSQL collection phase deadline, not production load/throughput evidence or a whole-process timing guarantee. Backup hashing precedes that async deadline and has a separate file-size cap.
- `IDENTITY_MIGRATION.md:57`: proof is valid for one hour after recording in the central database, not precisely from the start of source collection.
- `deploy/README.md:96`: explicitly run collector only after restoring the recorded owner/ACL state. The dump/restore examples may use PGSERVICEFILE/PGPASSFILE, while collector's exact private DSNs intentionally reject service/passfile indirection; these are separate tool contracts.

The current descriptions explicitly reject unsupported real Logto custom types and other unhandled objects rather than promising universal compatibility. Actual production IdP structure remains unverified. No live DNS/TLS claims were independently rechecked during this documentation-only review.
