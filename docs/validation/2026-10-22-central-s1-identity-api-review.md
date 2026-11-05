# S1 identity and central HTTP/CLI independent review

Date: 2026-10-22. Read-only review of Task 3 and root-owned central `app.py`, `config.py`, `__main__.py` against the S1 implementation plan. Native bridge and UI are intentionally excluded while still under implementation.

Evidence: current source and focused tests inspected. The reported 49 identity tests were not rerun; no database writes, secrets, configuration changes or server changes were made. The root's concurrent runtime-role lifespan changes were observed and are not reported as missing.

## P2 — accounts CLI reads an argument its parser never creates

Location at review: `services/platform/src/platform_service/__main__.py:38`, accounts branch at 47 and parser at 73.

`operate()` constructs `OperatorIdentity(args.operator, ...)` before reaching `accounts`. Only publish-plan and grant-test parsers define operator. Consequently `python -m platform_service accounts` raises AttributeError after a successful database open instead of listing the wallet IDs needed for a test grant; the CLI error handler does not catch AttributeError either.

Move the accounts branch before operator construction, or construct operator only inside commands requiring it. Add an accounts CLI regression test with an injected database/configuration and confirm structured output and clean close.

## P2 — HTTP error conversion discards the explicit retryability contract

Location at review: `services/platform/src/platform_service/app.py:42-47,65-66`.

The failure helper derives retryable exclusively from status 503, ignoring `PlatformError.retryable`. The OIDC transport explicitly raises `PlatformError(502, 'identity_provider_unavailable', retryable=True)` for transient network/provider failures. Clients receive retryable=false, losing the recovery contract. Conversely a non-retryable 503 becomes retryable=true.

Pass the exception's explicit retryable value to the response helper at both error conversion sites. Add an HTTP test for 502/retryable=true and 503/retryable=false. Fixed boundary errors should specify their intended retryability directly.

## P2 — Wrong-product authorize request consumes valid transaction initialization

Location at review: `services/platform/src/platform_service/app.py:128-132`; `services/platform/src/platform_service/identity/__init__.py`, authorize method.

The route validates its product only after `IdentityService.authorize()` locks the transaction and commits its state, nonce and cookie hashes. A valid Easel transaction sent to an enabled second product's authorize route returns product_mismatch, but the transaction is now initialized without any cookie having reached the browser. Retrying the original correct Easel authorize URL fails login_transaction_invalid because state_hash is already populated.

Validate the expected route product inside the transaction before setting its proof hashes, for example through an optional expected-product argument. Add a route-level regression sequence: begin Easel transaction; request second-product authorize URL with its transaction ID and expect rejection; request correct Easel authorize URL and expect a valid redirect and correlation cookie. This is a transaction-state correctness problem, not a demonstrated cross-product authentication bypass.

## Checked boundaries without an additional confirmed defect

- Callback URI accepts only exact 127.0.0.1/[::1] HTTP origins with explicit port and `/platform-auth/callback`, with userinfo/query/fragment rejected.
- OIDC state, nonce and cookie proofs are hashed; OIDC PKCE is separate from local handoff PKCE. Handoff is bound to product/client/callback/local state and one-time redemption is row-locked.
- Discovery validates exact issuer and endpoint origin; transport blocks redirects, bounds response bytes and uses explicit timeouts.
- ID tokens require RS256, unique matching RSA kid, signature, issuer, audience, time claims and nonce. Multi-audience azp is checked. Email does not merge issuer/sub identities.
- Product/client, user, membership, epoch and session revocation are checked by session authentication. Refresh and revocation share the session-row serialization point; consumed refresh replay revokes the device session.
- Ordinary business endpoints require device bearer authentication; cross-origin browser writes are rejected and no public wallet/operator write route is exposed.
- Login start explicitly returns 503 when identity is unconfigured. Capabilities declare test billing and both payment/execution flags false.
- OIDC credentials and database configuration are excluded from dataclass repr. Server invocation disables access logging and proxy header trust.

These observations are source evidence, not production Logto acceptance. The three findings were sent to the root for correction; this report records the reviewed state before those corrections.
