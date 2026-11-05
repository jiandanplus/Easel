# Console final independent evidence review

Verdict: **No remaining blocking defect found within the reviewed local-test and deployed-loopback-preview evidence. Real administrator acceptance remains incomplete and must not be claimed.** This is a read-only review of existing public safe receipts and the validation document, not a new remote/process/SQL/browser probe. No source, service, login transaction, credentials or business data was modified. No PostgreSQL/network/model call was made.

## Actual evidence reviewed

- local-validation-safe.json: full backend630 passed/0failed in65.82s against dedicated real isolated PostgreSQL with signed synthetic OIDC; frontend20 passed/0failed and production build. real_admin_tested=false. Wheel195985bytes SHA4e5a7594579e86d0e1a1ef698034d85e3e0ceff606c89660fd308ad0a1cdbb09 contains exact Python,9migrations and4static files including the frontend receipt. This proves local/test/package boundaries, not real-user admin acceptance.
- server-prepare-v2-safe.json: owned environment console-20261023-cbabf5af7060 prepared; pre-migration schema backup257061bytes SHA b4a06ce4f0fe59f66c19753b8ffe45bf6c88a0dae27d3009e27320a161d48661; business snapshot unchanged, zero real administrator grants and zero model requests during preparation. Existing product business counts are explicitly present. Backup creation/checksum is not a restoration rehearsal.
- server-start-safe.json: owned Console running on loopback18875 with nonroot=true. This receipt establishes the observed startup result; this review did not independently inspect a live UID/process.
- tunnel-safe.json: local18875 forwards to server loopback18875; health reports central-console with assets_ready=true; anonymous session/users status401. Existing reverse tunnel reused, provider_key_transferred=false. Health+anonymous rejection do not prove authenticated authorization or business writes.
- server-final-safe.json: same source manifest SHA fe67f78928dec4ed5f72176be27c8bba2ce4b4a19c7c4d15bb17a87e748d97b6 and same backup as prepare; migrations001–009 present; owned_console_running=true; administrator_count=0, pending_requests=0 at that snapshot; existing business wrapper and NewAPI still running; provider_key_read_by_console=false. Product payments/test payments remain disabled, existing live text subscription execution remains enabled; media/cloud capabilities remain pending.

The phase='prepared' value in the final status receipt describes provisioning state and is accompanied by owned_console_running=true; it is consistent with the separate startup receipt. It should not be summarized as a stopped server. Conversely port_free=false is expected for the owned running18875 listener, not evidence of an unrelated collision.

## Real browser boundary and remaining work

Root reports that the browser actually displayed Console18875 login and reached the independent Logto application's registration page. This is root's browser observation, not an independently repeated observation by this reviewer. The user's own registration remains pending. No administrator was granted and no real authenticated Console catalog operation occurred. The pending_requests=0 snapshot is not a claim that a later in-progress browser login transaction is absent.

The remaining authorized acceptance sequence requires the user to finish their own account registration/login, a verified pending-access request, an explicit owner-side approval for the exact issuer/subject, then a fresh login as that approved administrator and actual read/write acceptance as separately authorized. No user password, grant, browser continuation or business write was attempted here. Synthetic signed-OIDC tests cover that flow locally but cannot replace this real-user sequence.

No existing product account is promoted automatically. Internal experience grants, refunds, payment confirmations, force reconciliation and provider/payment secret upload remain outside the Web surface. Production domain/TLS, real merchant collection, media/cloud integrations, load/performance acceptance, HA and backup restoration are not complete claims. Loopback/SSH-preview deployment does not imply a durable public production release.

## Documentation reconciliation

Read docs/validation/2026-10-23-central-console.md. Its630/20 counts, wheel hashes, source identity, isolated Logto application preparation and explicit real-admin limits agree with the reviewed receipts. Its deployment paragraph still said that deployment/process isolation/browser results would be added; root had already stated that section was being updated. **Complete that documentation update before final handoff**, using the actual nonroot startup/loopback health/anonymous401/migrations9/administrator0 evidence above and retaining the pending real-admin boundary. This is a documentation closeout item, not a discovered deployment blocker. Do not mark real administrator or write acceptance complete merely because health and login navigation passed.

## Receipt integrity anchors

The following SHA values identify the exact local receipt bytes reviewed; no receipt was regenerated:

- local-validation-safe.json: `49c710f6a0e85e90597b74b21b22c03413ac673a891e168bf3b4beebac2c79bc`
- server-prepare-v2-safe.json: `b66d1fe3ecbf039614c113c0fdbe0451cfa4d348f5696f2ade5d9ff934e69282`
- server-start-safe.json: `05ffdfeb781e2f95eeec55721af31affe9f688dff7bc838e41e2a8655a54d8bd`
- tunnel-safe.json: `a2b7cc6554f72b255911328b2dbdc5538342636e58eb8aebe6d170336df6dff7`
- server-final-safe.json: `c25f1cc5174631d28ba3552e84f4ef06a427c02e5c8dccffae1855780386f0b6`

All JSON cross-checks above passed locally. Evidence is point-in-time and receipt-based; no assertion of continuous availability, external model usage or live authorization beyond those receipts is made.


Root closeout: validation report deployment section and public consolidated receipt were updated after this independent review. Actual administrator registration/approval and authenticated browser acceptance remain pending; no real catalog mutation was performed.
