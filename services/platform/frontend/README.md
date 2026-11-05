# Central operations console frontend

Independent React/TypeScript/Vite client for the Console BFF. This directory is not part of the Easel desktop frontend. The site and assets use `/`; requests use same-origin `/api/...` and `/auth/logout`. Authentication starts at `/auth/login`.

## Local checks

```sh
npm ci --ignore-scripts
npm test
npm run build
python ../scripts/build_console.py --verify-existing
```

The final command verifies the source/output set and writes `dist/console-assets.json`. Do not add other receipts or private configuration to `dist`. Deployment must serve the generated assets with the independent Console application; Vite is not an authentication proxy.

## Behavior and security contract

- The BFF session cookie is sent with `credentials: same-origin`. CSRF stays in client memory. There is no bearer-token or local-storage authentication path.
- A 401 shows the login screen. A 403 clears protected client views. Registration without ACL approval is handled by the server's pending-request page; the frontend cannot approve an account.
- Read requests are abortable and protected against stale results. No fixtures or fallback rows are shipped. Empty, unknown, loading and failed data remain distinguishable.
- Publishing requires the corresponding server-returned permission. A submission captures its exact payload and idempotency key once; a manual network retry reuses both. A conflict never automatically creates a new operation. Session changes invalidate captured operations.
- Financial strings are formatted with BigInt, without floating-point conversion. `spendable_available` is 可用余额; `available` is 账本可用. Missing values remain unknown. Frozen and expired diagnostics may overlap and are never added together.
- All table/detail text uses React escaping and a detail-field allowlist. There are no secret/provider-key, credit-grant, refund, manual-settlement or administrator-permission forms.
- CSS is external and compatible with the Console CSP without `unsafe-inline` or external font dependencies.

## Verification record — 2026-10-23

Behavior tests were introduced before implementation: the initial 12 cases failed for missing behavior, then passed. Submission/App tests added three failures before their implementation. Financial semantics/protected-offer tests and reason validation each produced focused failures before the fixes. Final run: **20 passed, 0 failed, 0 skipped**. `tsc -b && vite build` passed. The tests use actual client functions and React server rendering; they do not establish browser interaction, live OIDC, real catalog writes, database permission enforcement or provider acceptance.

Recommended browser acceptance: desktop and mobile login layout; real OIDC pending and approved login; viewer read-only navigation; real empty/error states; search/pagination/detail keyboard focus; operator publish/withdraw/model-state actions in the designated test scope; refresh-conflict handling and same-key retry; sign-out clearing protected views. Any synthetic test data must be explicitly identified by the test environment, never presented as production data.

Later real-browser acceptance exposed an overview-to-list transition bug. Two new tests reproduced the stale ready payload and SSR render exception before the fix (20 passed / 2 failed). Navigation now clears stale data and cancels its request synchronously; DataPage rejects incompatible list payloads. The actual App handler test also covers repeated-current-page recovery. Final repair run: **22 passed / 0 failed**, TypeScript/Vite and unified asset verification passed. A root-owned real-browser retest remains separate from these local checks.
