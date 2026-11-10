# OIDC browser recovery and owned deployment review

2026-10-23, read-only review. **Spec PASS; quality PASS. No P1/P2 finding in this narrow change.** No server/browser actions, authorization-code replay, private configuration reads, grant issuance or model calls were performed by the reviewer.

Frozen inputs:

- `services/platform/src/platform_service/app.py`: `c0e1536d48fd74f6a4154ee04c846708d37af6fbffff84f2a4b0d2bad65adfb5`
- `services/platform/tests/test_oidc_browser_errors.py`: `4c6f0b92106dbd49e5a197431128fed82898cb1e2350478e7aa6718c1d835664`
- `deploy_login_error_fix.py`: `a697c7f3974013e611fd4d7d56fbc5b203821e00f670e75e6a3c1ff06322e235`

## Product source

The new response helper is used by both the registered PlatformError handler and the middleware catch. Only GET requests matching the exact OIDC authorize/callback path shape and explicitly accepting text/html with positive quality receive HTML. Missing Accept, wildcard-only, JSON and text/html;q=0 retain the JSON failure shape/status. Other methods/routes retain their existing behavior.

The HTML is entirely static Chinese recovery guidance. No URL, query value, authorization code, cookie, state, transaction ID, claim, upstream error or exception text is interpolated. There are no scripts, forms, automatic redirects or retry actions. The response itself sets no-store, no-referrer, nosniff and restrictive CSP, including the middleware early-return path. The original error HTTP status is retained.

Authentication lifetime, cookie generation, PKCE, JWT validation, transaction consumption, token exchange, identity provisioning and handoff logic are untouched. The page communicates failure and directs the user to start a new login; it does not imply successful login or retry the old code.

The new tests cover missing cookie/code, expired transaction, wrong state, provider-error query and injected product input, unknown failures with preserved status, JSON negotiation and unrelated routes/methods. Invalid callbacks are guarded against token exchange and checked for unconsumed transactions and absent sessions/handoffs. The implementing agent reported 13/13 GREEN; this reviewer read the tests but did not execute them.

## Deployment wrapper

The script restricts payload paths to the single public `app.py`, validates its old hash, compiles replacement source, verifies the owned environment/config/source hashes and refuses active login transactions. It verifies the owned server's PID start time, command line and listening socket through the existing helper, then validates its parent wrapper UID and exact executable/arguments before signalling that wrapper. The reviewed wrapper forwards termination only to its own child.

It backs up the original public file privately, waits for the owned port to become free, atomically replaces the single file, updates the source digest, rechecks unchanged configuration hashes and starts the same owned wrapper with a clean environment. Runtime configuration is consumed server-side and is not printed. Returned evidence is explicitly constructed from hashes, counts, statuses and the owned wrapper PID. No old ledger, Logto, New API, original preview or provider configuration is modified.

Post-start checks use a synthetic callback without any query or authorization code, require HTML and JSON failure behavior, and compare identity/grant/execution/order counts. This is a login-error-page check, not evidence of successful OIDC login or a model call.

Operational boundaries: perform the bounded restart while the user is not starting another login; the active-transaction check is a preflight snapshot rather than a lock on future browser actions. The deployment script intentionally refuses reuse of its existing backup directory and has no automatic rollback. If interrupted after stop/replacement, inspect owned process/source state and recover from the preserved public backup; do not blindly rerun or touch unrelated services. These limitations do not invalidate the reviewed one-time idle deployment.
