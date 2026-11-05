# S1 final independent review

Date: 2026-10-22. Scope: prior foundation/wallet and identity/API findings, additive migration 004 and runtime-role permissions, native central client/listener/session store, and CentralAccountSettings plus its API types. This is an independent read-only review; only this report was authored by the reviewer. Implementation fixes belong to the root/native implementers.

## Review status

All five findings from the two earlier review reports are closed by current source and targeted regression evidence. The new native invalid-session recovery finding and both frontend contract findings are also closed by independent corrected-behavior probes. The subsequently frozen ES384/P-384 compatibility change and safe CLI connection-error handling were independently reviewed and their targeted regressions passed. **No actionable P1/P2 remains in the reviewed S1 paths.** This closes the independent code review; environment-specific acceptance remains a separate evidence category.

## Frontend findings discovered and closed

### P2 — Login failure object is rendered as a React child

Locations at discovery: `web/frontend/src/components/CentralAccountSettings.tsx:152`, `web/frontend/src/lib/api.ts:71`; producer `easel/platform/central_web.py`, local_status/expire/completion.

The bridge returns `login.error={code,message}` after login timeout, task guard rejection or redemption failure. The TypeScript contract declares error as string and the component inserts the complete object directly into a paragraph. This causes an actual React render exception and removes the recovery UI.

Independent reproduction: transpiled the current component with installed TypeScript, supplied the real backend error shape through a minimal hook harness, and rendered with the actual installed `react-dom/server`. Observed:

```text
Objects are not valid as a React child (found: object with keys {code, message}).
```

Required correction: align the API type to the object and render its safe message. A real React rendering regression should use the backend object shape; the existing synthetic tree renderer accepts plain object children and cannot detect this failure.

Closed: the type now declares `{code,message}` and the component renders `.message`. The identical independent real-React probe now renders the Chinese timeout message successfully and contains no object-child leak. The added real-React regression also passes.

### P2 — Device-session UI consumes legacy names absent from the central API

Locations at discovery: `web/frontend/src/components/CentralAccountSettings.tsx:174`, `web/frontend/src/lib/api.ts:90`; producer `services/platform/src/platform_service/identity/__init__.py`, sessions.

Central sessions return `id,product_id,client_id,created_at,expires_at,current`. The bridge forwards that structure. The UI instead reads `device_name,refresh_expires_at,revoked`, so actual central devices always appear unnamed with an unknown refresh deadline even though the server supplied the deadline and client identity. Current frontend fixtures omit these real fields and do not catch the mismatch.

Required correction: use the central contract directly or explicitly transform it once, then test with the exact real session payload. Also align CentralPlan.version to the server's immutable string version while editing the contract; this latter type discrepancy alone does not currently break display.

Closed: device type/renderer use `client_id,expires_at,current`, plan version is a string, and the test fixture uses the central server payload. The specific device-contract regression passes. The account panel also exposes explicit logout/retry for a retained but temporarily unverified local session; its recovery regression passes.

## Corrected findings verified in this review

- Immutable-history TRUNCATE and runtime-owner gap: migration 004 adds BEFORE TRUNCATE guards; permissions grant only needed DML to non-owner runtime roles; service lifespan calls require_runtime_role. Temporary-role tests verify permitted operations, rejection of TRUNCATE/DDL/history UPDATE/DELETE, and owner-role membership rejection.
- Reservation/hold divergence: migration 004 freezes reservation bindings and terminal rows, closes hold allocation after the creation transaction, validates active lot holds and reservation/terminal journal amounts at commit. Corruption probes reject the previous reproductions and legitimate settlement/release remains possible afterward.
- accounts CLI missing operator: accounts is handled before constructing OperatorIdentity; its dedicated real-PostgreSQL regression passes.
- API retryability: PlatformError.retryable now reaches both exception conversion paths; true/false contract probes pass.
- Wrong-product authorize: expected_product is checked inside the locked identity transaction before proof hashes are written; the route-level incorrect-product-then-correct-product regression passes.
- Native revoked/expired session dead end: discovered that the inherited v1 error mapper hid session_invalid/refresh_replayed, logout refused to clear it, and begin refused the retained auth. The native implementer added central-specific safe error mapping and clears credentials only on explicit terminal 401 errors or local refresh expiry, preserving outage state. Independent before/after temporary-directory reproduction confirms the corrected status keeps the safe session_invalid code, removes private auth, allows logout, and accepts a new login start. No real credentials were used.
- CLI connection-error boundary: the root added handling for Psycopg database errors and pool timeouts. Two targeted tests confirm that a synthetic exception containing a secret marker produces only the stable CLI message. Local operator commands remain trusted local administrative actions; the network service still enforces its non-owner runtime-role startup check.
- ES384/P-384 compatibility: the verifier's local allowlist is exactly RS256/ES384. Matching kid must be nonempty and unique; RSA requires the RSA key shape, no EC curve and at least 2048 bits; ES384 requires EC/P-384. Present JWK alg/use/key_ops must match the expected algorithm, signature use and verify-only operation. Each decode receives only the already validated algorithm. Existing issuer/audience/azp/nonce checks remain in place. The 13-case added regression covers successful signed P-384 authentication and rejection of wrong key type/curve/algorithm/use/operations/kid, duplicate kid, wrong signature/nonce/issuer/audience and an unallowed algorithm.

## Independent execution evidence

Owned loopback PostgreSQL DSN was supplied by the approved private DSN-file environment variable; its contents were not printed. Tests create disposable schemas and temporary roles through their existing fixtures.

```text
pytest test_database_integrity.py test_runtime_permissions.py test_api.py --tb=short -q
22 passed in 2.14s

pytest test_operator.py --tb=short -q
3 passed in 0.20s

node --test --test-name-pattern='central devices|retained|structured login' tests/frontend/centralAccount.test.mjs
3 passed, 0 failed

pytest test_oidc.py test_operator.py -k 'es384_logto_signature_boundaries or cli_connection_errors_are_safe' --tb=short -q
15 passed, 22 deselected in 1.23s
```

The native before/after probe used only httpx.MockTransport, synthetic credentials and TemporaryDirectory. Corrected output:

```text
status_authenticated: False safe_error: session_invalid
private_auth_remaining: False
logout_authenticated: False
login_restart_succeeded: True

structured_login_error_rendered: true
object_child_leak: false
```

Existing full identity/wallet/native test claims were not independently rerun just to duplicate their evidence. The native implementer separately reports 108 native/legacy/privacy tests passed, and the identity implementer reports 62 identity/OIDC tests passed after ES384 support; those are implementer evidence rather than independent full-suite runs by this reviewer. Independent targeted execution above totals 40 central-service tests and 3 frontend tests, plus the two before/after minimal probes.

## Cross-layer observations

- Central and legacy session filenames/locks are distinct. Atomic private persistence retains mode 0600 under a mode 0700 directory; symlink checks and existing raw-secret output filtering also include the central store.
- Browser APIs receive account/scope metadata and an authorization URL, not access/refresh credentials. Callback handoff proof stays in the native listener. Client calls are allowlisted and S3 authorization methods explicitly reject execution.
- Listener state and mutation checks execute on the owning ASGI loop. Cancellation/expiry/workspace mismatch are rechecked before persistence, and cancellation is ordered with the disk commit through the persistence lock. Rejected redeemed sessions get best-effort server revocation.
- Native refresh occurs under the private session file lock, avoiding concurrent refresh-token reuse across native client instances. Terminal authentication failures are distinguished from network/provider unavailability after the fix.
- Unknown wallet values remain unknown; integer string formatting preserves microcredits. Payment and execution remain clearly unavailable in the UI.

This review does not claim production Logto, payment, supplier execution or production deployment acceptance. No real .env, existing private session, production database, server configuration, branch or commit was modified.
