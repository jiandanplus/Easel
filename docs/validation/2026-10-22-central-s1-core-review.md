# S1 foundation and wallet independent review

Date: 2026-10-22. Scope: Task 1 and Task 2 of `docs/superpowers/plans/2026-10-22-central-accounts-plans-wallet.md`, checked against the revised 2026-10-22 subscription design.

Reviewed: contracts, errors, Database, membership, credits, migrations 001/002, independent package configuration and focused tests. Identity, HTTP app and desktop UI are excluded because they are still moving.

Evidence: source inspection only. The implementer's reported 17 foundation and 22 wallet tests were not rerun. No database writes, credentials, real environment configuration, production or test-server configuration changes were made. SQL below is a required regression probe, not a claim that this reviewer executed it.

## Findings requiring correction

### P1 — Runtime privileges and TRUNCATE bypass immutable history

Locations: `services/platform/src/platform_service/migrations/002_credits.sql:91-95`; `services/platform/src/platform_service/db.py:17-27,46-71`.

All immutable-history guards are row UPDATE/DELETE triggers. They do not handle TRUNCATE. The same Database DSN supplies migration and runtime connections, and the reviewed implementation provides no least-privilege runtime-role boundary. When that role owns the migrated tables, ordinary SQL can erase ledger history without firing either immutable triggers or deferred row reconciliation.

Minimal probe in an owned disposable schema after issuing credits:

```sql
BEGIN;
TRUNCATE credit_entries;
COMMIT;
```

This statement has no dependent-table obstacle and no TRUNCATE guard; counters remain while immutable entries disappear. A broader `TRUNCATE plans CASCADE` also illustrates why dependent FKs are not an immutable-history defense.

Required correction: separate migration owner from the non-owner, non-superuser runtime DML role; runtime must not have TRUNCATE, ALTER/DROP or trigger-disabling authority. Add explicit BEFORE TRUNCATE guards for immutable history. REVOKE on an owner is not sufficient protection against owner-level DDL. Add a real PostgreSQL runtime-role acceptance test proving that normal issuance/reservation/settlement works and destructive history operations are rejected.

### P2 — Reservations and holds can diverge while journal reconciliation succeeds

Locations: `services/platform/src/platform_service/migrations/002_credits.sql:39-67,118-143`; consumers `services/platform/src/platform_service/credits.py:307-310,338-364`.

The deferred reconciliation compares wallet-level lot totals against journal totals. It does not observe reservation or hold writes and does not connect reservation state to held balances. After a legitimate reservation of 80, the following corruption can commit without changing either reconciled side:

```sql
UPDATE credit_reservations
SET status='settled', actual=0, completion_key='corrupt'
WHERE id=:reservation_id;
```

The wallet still reports 80 held, while usage reports a completed zero-cost reservation; a repeated settle with `actual=0, key='corrupt'` returns the row as successfully settled without repairing the frozen balance. An extra hold inserted for another lot after initial reservation creation also commits and makes subsequent completion fail, because the service consumes all holds while its journal uses the original reservation amount.

Required correction: commit-time consistency between reservation amount and sum of its holds; between each lot's held counter and holds belonging to active reservations; and between terminal state, actual/completion fields and its terminal journal. Preserve immutable reservation bindings/budget and terminal state, and disallow extending committed hold allocation or enforce an equivalent invariant. Add targeted PostgreSQL corruption tests and verify the failed corruption rolls back so the legitimate reservation remains completable.

## Reviewed paths without another confirmed defect

- Scope value validation is immutable and bounded; authorization checks tenant/account membership, user epoch and enabled product. Reservation completion binds wallet, tenant, user and product.
- All service wallet mutation paths take the same billing-account row lock. Cross-product reservations therefore contend on one wallet rather than separate product budgets.
- Test issuance writes membership, lot and balanced journal in one transaction and rejects rebinding the same operator issuance key to another wallet or plan.
- Reservations allocate lots in a stable expiry/id order. Over-budget settlement commits pending state while retaining the hold. Unknown dispatched work cannot be released as zero cost.
- Expiry, release and settlement update original lots and balanced entries in one transaction; idempotent successful completion returns the existing result.
- Migration bootstrap/application share a transaction-scoped advisory lock; stored SHA256 values reject modified or missing applied files. Controlled schema names are validated and quoted.
- Package configuration names a separate Python package and dependency lock. Existing import-isolation test checks the active installed environment; this review did not independently build and inspect a wheel.

The findings are database-integrity gaps. No claim is made that a current public HTTP route exposes arbitrary SQL or directly permits these corruptions.
