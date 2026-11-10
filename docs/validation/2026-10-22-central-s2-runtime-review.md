# S2 Task 3 independent runtime review

Reviewed 2026-10-22 on `codex/ui-redesign`, working tree as present during review. Read-only repository review; only private `/private/tmp` probe/report files were written. No preview state, Logto, real `.env`, containers, commits, or actual merchant calls were changed/performed. Test PostgreSQL used `/private/tmp/easel-s2-runtime/database.json` through existing random-schema fixtures, which clean up their own schemas.

## Result

**PASS after independent fix recheck: no remaining concrete P1/P2 in this review scope.** The originally confirmed order-list QR overflow P2 is fixed and independently retested below. No additional concrete P1/P2 found in the reviewed runtime/configuration/CLI boundaries. Core refund-ordering findings assigned to another reviewer were deliberately not duplicated. This is not full S2 acceptance or real-payment acceptance.

### [Resolved P2] Bound order history without encoding every checkout QR

Location: `services/platform/src/platform_service/payment_web.py:73-79`, specifically line 77 calling `decorate_order` for every order. The decorator at line 51 synchronously creates a full SVG data URL; `billing/service.py:265-280` returns up to 200 orders. Native `easel/platform/central.py:78-79` rejects response bytes beyond 2 MiB.

Reproduced with real PostgreSQL and the actual API, then passed its actual response through native `CentralClient._request` using HTTPX MockTransport:

- Five pending orders, each with a valid 2048-byte provider Checkout payload (within the existing contract and QR tests).
- `GET /v2/orders`: HTTP 200, **2,291,607 bytes**.
- Single-order detail: **458,318 bytes**, accepted-size payload.
- Native order list rejects with **`CentralError(code="platform_unavailable")`**; the UI cannot retrieve/recover order history.
- QR generation for this five-order list took approximately **0.544 seconds** in the async request path on this machine. Repeated list reads regenerate every QR.
- Ordinary simulated QR data URLs are approximately 15,630 characters in the sampled payload; the 200-order limit does not by itself guarantee a response below the native limit even without maximum-length payloads.

Recommended fix: return history metadata without QR images and fetch QR only for the selected detail, with an explicit compatible detail-enrichment contract; or implement bounded pagination/response sizing. Do not simply raise the native safety cap. Current UI rejects equal-revision replacements (`CentralBillingSettings.tsx:27-32`), so removing list QR alone would cause equal-revision detail hydration to be ignored and requires coordinated handling.

Reproduction retained in `/private/tmp/test_easel_s2_runtime_review.py::test_order_list_repro_exceeds_native_bound`. This is a reproduction test asserting the defect exists, not a green regression proving it fixed.

## Verified evidence

Existing root-owned suites: **38 passed in 2.58 seconds**:

```text
services/platform/tests/test_payment_config.py
services/platform/tests/test_payment_testing.py
services/platform/tests/test_checkout_qr.py
services/platform/tests/test_payment_runtime.py
services/platform/tests/test_payment_api.py
services/platform/tests/test_payment_operator.py
```

Command:

```bash
rtk proxy env EASEL_CENTRAL_TEST_DSN_FILE=/private/tmp/easel-s2-runtime/database.json services/platform/.venv/bin/python -m pytest services/platform/tests/test_payment_config.py services/platform/tests/test_payment_testing.py services/platform/tests/test_checkout_qr.py services/platform/tests/test_payment_runtime.py services/platform/tests/test_payment_api.py services/platform/tests/test_payment_operator.py -q
```

Private adversarial probes: **3 passed in 1.31 seconds** (one confirms the above defect; two exercise additional boundaries):

```bash
rtk proxy env PYTHONPATH=services/platform/tests:. EASEL_CENTRAL_TEST_DSN_FILE=/private/tmp/easel-s2-runtime/database.json services/platform/.venv/bin/python -m pytest /private/tmp/test_easel_s2_runtime_review.py -q -s
```

- For both notify channels: 65,537-byte bodies reject with 413, hostile Origin rejects with 403, invalid signatures reject with 400 and correct channel-shaped failure body.
- Unexpected provider error returns Alipay `failure` with 503; private exception text is not returned.
- App lifespan starts its own worker, cancels/awaits it at shutdown, leaves an unrelated task alive, and leaves an injected database usable.
- Existing signed callback tests include duplicate HMAC delivery and actual TCP loopback, and issue exactly once.
- Existing scope tests reject foreign-wallet reads and client price/wallet/payment claims; public confirm/refund endpoints absent.
- Existing runtime timeout test leaves `create_checkout` pending with stable error and no grant; core `finish_outbox` additionally schedules query recovery on create/close/refund errors.
- Existing CLI tests exercise publish/withdraw, simulated paid/refunded replay, wallet freeze/revocation, disabled simulation rejection, runtime owner rejection, and safe error output.

## Configuration, lifecycle and documentation assessment

- `PaymentSettings.from_env` defaults disabled before reading merchant files (lines 93-97); test requires explicit loopback development setting (106-107); live requires HTTPS and rejects loopback development settings (108-111). Test config accepts only test key/state fields (130-145), excluding merchant key configuration.
- Server credential file reader rejects final symlinks, nonregular files, oversized content and group/world permissions; secret dataclass fields do not appear in repr. Exact live config schemas prevent arbitrary gateway URLs.
- `__main__._test_payment` rejects all modes except test before producing a simulated fact and checks the stored order channel/mode/merchant/app context. The command remains trusted local operator functionality, not an HTTP purchase confirmation.
- App uses the actual payment mode in capabilities, and advertises purchases only with applicable channels/offers. S3 remains false.
- Worker awaits provider I/O before entering `finish_outbox`; runtime validates frozen mode/merchant/app before calls, preserves retryable uncertainty, and suppresses sensitive exception text. Lifecycle cancels only its owned task.
- `PAYMENTS.md` clearly describes test-only 0.01 yuan, unverified real merchant acceptance, operator-only refunds, no automatic billing, retained financial history and S3 off. Its operational sequence includes migration/runtime permissions and protected credential files. No new concrete truth-claim mismatch found in the runtime documentation scope.

## Evidence limits

No live merchant credentials or public payment domains were used. Provider protocol details were independently reviewed elsewhere; this pass did not repeat that provider audit. Root/frontend integration and the pending core refund fixes require their own regression checks. The order-list defect has now been fixed and independently retested as recorded below; this scoped runtime review is closed.


## Independent fix recheck — PASS

Root changed `decorate_order(order, include_qr=True)` and uses `include_qr=False` only for history. The history retains checkout target/expiry with `qr_data_url: null`, while individual order detail still derives the QR. The native 2 MiB response cap remains unchanged.

Repeated the original actual-PG / actual-API / native-client reproduction with the same five valid 2048-byte Checkout payloads:

- History now **13,867 bytes**, previously 2,291,607 bytes.
- Single-order detail remains **458,318 bytes**, with its valid SVG data URL.
- Native `CentralClient._request` accepts the complete five-order history.
- History handler elapsed approximately **0.008 seconds**, previously 0.544 seconds.
- Root regression additionally measures encoder calls: **zero** for history and **one** for a selected detail.

Independently inspected the UI merge change. At an equal revision it copies only `qr_data_url` into the previous checkout, only when that previous image is missing and both `qr_code` and `expires_at` match. The shared validator checks the 1,000,000-character bound before the SVG Base64 allowlist. All previous financial fields stay intact even if the incoming detail supplies contradictory payment/refund fields. Metadata refresh cannot erase an existing QR; lower revisions remain rejected. The API type explicitly permits null QR images.

Current independently run checks:

- Runtime/config/testing/QR/API/operator suites plus private regression and boundary/lifecycle probes: **42 passed in 3.90 seconds** (39 repository tests + 3 private probes).
- `rtk proxy node --test tests/frontend/centralBilling.test.mjs`: **30 passed, 0 failed, 0 skipped**. This includes restored-list same-revision detail hydration, metadata preservation, contradictory financial data, oversized/external QR, changed checkout target and changed expiry.

Private green regression: `/private/tmp/test_easel_s2_runtime_recheck.py`. Original defect reproduction is preserved separately in `/private/tmp/test_easel_s2_runtime_review.py` as before-fix evidence.

No remaining concrete P1/P2 found in this runtime review scope. This PASS does not substitute for the separate core refund review, full end-to-end browser acceptance, or real merchant validation. All tests still use owned PostgreSQL fixtures and simulated providers; no preview configuration or merchant state was changed.
