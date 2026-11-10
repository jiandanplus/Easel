# Independent Console auth / permissions / config review

Status: **reported source findings addressed; focused independent delta GREEN**. The earlier findings and outage checkpoints below are retained as historical evidence. This is a read-only source review, not a deployment or final security acceptance. No core-owned source file was modified. No credentials, actual user session, old wallet or remote service was read or changed.

Reviewed console_auth.py, console_config.py, console_permissions.py, permissions.py, 008 schema, and corresponding public tests. Also followed the Console's reused signed-token verifier in identity/oidc.py.

## P2: missing auth_time is accepted as fresh authentication

ConsoleAuth.callback compares `now - verified.authenticated_at` to a bounded freshness window. However shared `OIDC.exchange` requires iss/sub/aud/exp/iat/nonce, not auth_time, and constructs authenticated_at with `int(claims.get('auth_time', claims['iat']))`. A newly minted token from an old identity-provider session can therefore satisfy the new Console freshness check through recent token issuance time even when actual authentication time is absent. `prompt=login` alone does not make this verifier prove the claimed authentication time.

Observed with the existing signed Provider fixture and httpx.MockTransport, without database or network:

    synthetic_signed_token_auth_time_absent: true
    shared_exchange_accepted: true
    console_age_check_would_pass: true
    network_requests: 0
    database_connections: 0

Suggested narrow fix: Console-specific max_age=0 and a verifier option requiring an explicit numeric signed auth_time, preserving the existing product verifier default for compatibility. Reject missing/invalid type in Console tests; keep old/future freshness tests. Shared verifier ownership expansion must be approved by root. Core was notified.

## P2: sequence privileges escape the intended table isolation

Product `grant_runtime_permissions` now grants SELECT through an explicit table list, correctly excluding Console tables, but still ends with `GRANT USAGE ON ALL SEQUENCES IN SCHEMA`. After migration009, this includes console_audit_events_id_seq. Product role can therefore nextval/read sequence activity despite no Console table access.

Separately Console `assert_safe_console_role` validates table/column privileges but does not inspect sequence privileges. It would not reject accidental UPDATE/setval authority on the audit sequence or USAGE on an unrelated sensitive sequence. Its grant helper itself correctly grants only the audit sequence USAGE.

Suggested fix: product grants only the product sequences actually required (credit_entries and payment_event_audits); Console grants only console_audit_events sequence USAGE, never UPDATE; check inherited/PUBLIC sequence rights during startup and remove stale direct grants during reprovisioning. Add actual PostgreSQL regression once the isolated test service is available. No SQL privilege repro was executed while the service is down.

## Known P1 still pending: product NOINHERIT Console membership

The recursive role query already rejects reachable owner/superuser roles, but product check_grants loops only over current/session roles, not all reachable role names. A NOINHERIT child can hold a Console parent and later SET ROLE even though the child's immediate `has_*_privilege` result is false. Core has already written the regression and explicitly has not observed RED because PostgreSQL stopped. Treat this as open, not passed. The Console role checker itself enumerates reachable roles and checks each, so its corresponding direction is stronger.

## Lock-order review

Write validation acquires shared `console-session:<hash>`, reads the session/admin, acquires shared `console-admin:<id>`, then re-reads under default READ COMMITTED. Logout exclusively locks the session key before revocation. disable_admin exclusively locks the admin key before row mutation. These paths have no observed reverse advisory-lock edge. Catalog calls validate on its outer connection, retaining shared locks until catalog/audit commit; disable/logout therefore serialize with writes without granting Web ACL UPDATE rights.

READ ONLY validation performs SELECT only, which is compatible with the Queries REPEATABLE READ READ ONLY snapshot. A request whose snapshot begins before revocation may finish reading that snapshot; new requests recheck the current session/ACL. This should be described as request-bound validation, not cancellation of already-running read responses.

Callback session insertion can race with disable, but embeds the observed authorization_epoch; subsequent authenticate/validate rejects inactive or changed-epoch ACL. It does not create a usable privilege escalation. The owner-only approval path locks the request and serializes identity approval; it cannot reactivate a disabled account.

## Confirmed source-level boundaries

- Exact issuer/subject ACL; no product user/account dependency or email-based promotion.
- Token and CSRF fields are repr-hidden; only token hash is persisted. Eight-hour absolute TTL and current role/active/epoch/session revocation are checked.
- Login transaction is consumed before external exchange and cannot be retried/replayed to mint another session.
- Config requires explicit Console enablement and dedicated Console DSN/client/issuer/origin fields; no owner or product credential fallback.
- Console allowlist excludes product identity_tokens/transactions/handoffs and execution task tokens/results; privileged catalog writes do not require schema owner.
- Bootstrap remains an explicitly capability-gated actual schema-owner command, not Web self-authorization.

This report does not claim PG lock-race verification, final low-role guard PASS or independent production testing. Prior implementation tests and the service outage are recorded separately by core.

## Partial fix checkpoint

Core added `OIDC.exchange(..., require_auth_time=False)` with exact integer validation when opted in. Independently reran the no-database signed-token cases: **7 passed, 24 deselected**, covering absent/null/bool/string/fractional claims and the unchanged product default. This verifies the helper only. At this checkpoint ConsoleAuth.callback had not yet passed `require_auth_time=True` and begin_login had not yet requested max_age=0, pending real-PG integration RED. The original Console finding therefore remains open until wiring and regression are verified. Sequence/NOINHERIT fixes also remain pending PostgreSQL restoration.

## Final independent delta checkpoint

Current source confirms Console begin_login requests max_age=0 and Console callback opts into require_auth_time=True; the strict verifier requires an exact signed integer claim and keeps the product default compatible. The product guard enumerates all recursively reachable roles, including NOINHERIT parents. Product grant helper permits only its two required sequences; both product and Console guards reject extra sequence USAGE and all SELECT/UPDATE sequence authority. Console permits only its audit-sequence USAGE. These address the three findings above; they are not current open issues.

After dedicated test PostgreSQL recovery (new DSN file /private/tmp/easel-console-20261023/test-dsn-private.json), independently executed the focused max_age request, NOINHERIT product-role, product audit-sequence and Console sequence-guard entrypoints: **6 passed in 0.96s**. Earlier signed-token no-database claim regressions were **7 passed, 24 deselected**. The owned business focused suite also exercised actual low-privilege ConsoleAuth + all Queries and five Catalog operations as part of **35 passed in 3.29s**, including denied direct token/result/private-column/internal-grant reads or writes. Core separately reports its broader78-test pass; that is not an independently rerun result here.

No new lock-order issue was found in the final delta. Read-only revocation remains request/snapshot-bound, not cancellation of already-running responses. No production auth, real login, remote service, or model was exercised by this review. Root owns the final complete backend regression and deployment decision. No core-owned file was edited.

## Additional independent source closure — product review agent

Final verdict: **PASS; no remaining P1/P2 in the reviewed fixes.** This additional review read the frozen source and tests only, with no database connection. It confirms the max_age request/strict auth_time wiring, recursively reachable-role checks, narrowed sequence authority and private business-column denial. The preceding independent 6-test execution belongs to the business reviewer; core's 78-test result and root's subsequent full **630 passed in 65.82s** are separately reported evidence, not reruns by this reviewer. The root integration uses a signed synthetic OP with real HTTP BFF and isolated PG; it does not claim a live administrator login.

Reviewed SHA256: console_auth.py `e096caea5b99e05ec69425839a5956aa1d8a456aaa560880d873579bef837f9e`; identity/oidc.py `c0ad548aada5b9aca6f43ab109e3bb5e003a7b1887803eeb98bed3575bd72dd1`; console_permissions.py `fc1350a63bfbece887fadce3ce97d99509d6bb2f0b18d71ec3b2fb6ee9946a08`; permissions.py `b12c1507f1391aa4c5209c0f1f172e2dfef8c275feb09015979f544b90ea6d08`.

Deployment script review and actual environment/administrator acceptance remain separate, unfinished steps at this checkpoint.
