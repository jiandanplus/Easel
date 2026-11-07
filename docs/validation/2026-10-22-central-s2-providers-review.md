# S2 signed providers independent review

Reviewed 2026-10-22. Scope: the supplied `/private/tmp/easel-s2-providers-review.patch`, current `payments/{__init__,contracts,wechat,alipay}.py`, `test_payment_providers.py`, plan Task 1 / Global Constraints / Shared Contracts, implementer report, and protocol research notes. No application/core/UI edits or production merchant requests. Generated temporary RSA material exists only in probe process memory. The reviewer wrote this report and `/private/tmp/easel-s2-review-probes.py` only.

## Current post-fix verdict — 2026-10-22

- **Spec compliance: PASS within the reviewed provider scope.** Both previously reported P2 findings are fixed and independently verified.
- **Code quality: PASS; no remaining actionable P1/P2 finding in this scope.** The fixes are limited to identity validation and transport error normalization, with focused regression coverage. This is not production merchant acceptance.

### Independent post-fix evidence

Re-read current `payments/contracts.py`, `payments/wechat.py`, and the added tests. `_refund` now always calls `_identity`; `merchant_required=notification` permits documented missing synchronous fields while checking every present mchid/appid. Payment paths retain required merchant/app identity checks; refund notifications still require mchid. `httpx.DecodingError` is caught inside the bounded streaming transport and mapped to `payment_transport_uncertain`, retryable=True.

New tests cover both refund/refund-query operations with each contradictory identity, all four valid present/absent identity combinations, refund notification missing mchid, and malformed gzip streaming responses for both channels. Tests continue to use generated fixture RSA keys and injected MockTransport.

Reviewer independently ran:

```
rtk proxy sh -c 'PYTHONPATH=services/platform/src services/platform/.venv/bin/python -m pytest services/platform/tests/test_payment_providers.py -q'
80 passed in 3.75s
```

Reviewer also reran the original exploit shape against the fixed source: it now rejects with `payment_identity_mismatch` (the original pre-fix probe stops on that expected exception). Adapted explicit assertions in `/private/tmp/easel-s2-postfix-review-probes.py` all passed:

```
wx wrong app rejected: payment_identity_mismatch
wechat bad encoding: payment_transport_uncertain retryable: True
alipay bad encoding: payment_transport_uncertain retryable: True
wx wrong AAD rejected: payment_invalid_ciphertext
ali duplicate JSON rejected: payment_duplicate_fields
ali incomplete query: payment_order_mismatch retryable: False
```

The incomplete Alipay query remains a stable error with the previously verified recovery integration, and no new finding is raised. No source changes, credentials, real merchant connections, database/browser checks, or real payment/refund were performed by this reviewer. Initial findings and evidence below are retained as historical review provenance, not unresolved issues.

## Initial review verdicts — superseded by post-fix review below

- **Spec compliance: changes required.** The principal signature, monetary precision, authenticated paid status, refund reconciliation, and bounded transport design comply. Two reproducible edge cases violate explicit wrong-identity rejection / stable-error requirements.
- **Code quality: changes required (2 P2 findings, no P1 found).** Repair the identity condition and error normalization and add focused regression tests. No evidence of unsigned payment issuance, float monetary arithmetic, raw-response reserialization before Alipay signature verification, or real merchant calls was found in this scope.

## Initial findings — both closed by post-fix review

### P2 — Check optional appid even when a synchronous WeChat refund has no mchid

**Location:** `services/platform/src/platform_service/payments/wechat.py:169-172` (identity branch in `_refund`).

The synchronous branch invokes `_identity` only if `mchid` exists. A response with no `mchid` but with a contradictory `appid` therefore bypasses the app comparison at lines 104–105. The returned fact at lines 195–197 replaces that contradiction with the configured app ID and can carry `status=refunded`. Core subsequently receives the expected identity rather than the contradictory authenticated value and cannot detect this through its normal fact identity recheck.

**Reproduction:** the temporary offline probe provides a correctly RSA-signed refund-query response with valid matching order/refund IDs, total/refund 123, SUCCESS, success_time, transaction/refund IDs, no mchid, and `appid="wrong-app"`. `query_refund(...)` returns `refunded` and `app_id="wx-test"`. Output:

```
wx wrong app accepted: refunded wx-test
```

This does not establish a real-world forgery: the payload is deliberately signed using a generated fixture key. It establishes failure to reject an explicitly contradictory authenticated identity, as required by the frozen contract. The missing-field fallback is legitimate for documented refund payloads; present mismatching fields must still be rejected.

**Fix expectation:** independently validate every present appid/mchid in synchronous refund responses, while allowing documented absent identity fields. Keep notification mchid mandatory. Add a signed regression with wrong appid and omitted mchid, alongside valid omitted-field response coverage.

### P2 — Normalize malformed compressed response errors at the transport boundary

**Location:** `services/platform/src/platform_service/payments/contracts.py:237-243` (stream decoding and exception handler).

`response.aiter_bytes()` performs HTTP content decoding. A malformed gzip response raises `httpx.DecodingError`, which is not one of the handled TimeoutException / NetworkError / RemoteProtocolError classes. It escapes both providers as a raw HTTPX exception instead of the stable PlatformError promised by Task 1 and the shared error boundary. This also bypasses the existing uncertain-transport code path for the request, whose outcome can be unknown after sending a create/refund operation.

**Reproduction:** HTTPX MockTransport returns status 200, `Content-Encoding: gzip`, and an AsyncByteStream yielding `b"invalid gzip body"`. Both provider `create` calls escape with DecodingError:

```
wechat bad encoding: DecodingError stable: False
alipay bad encoding: DecodingError stable: False
```

**Fix expectation:** normalize decode/other intended HTTP transport exceptions to an appropriate stable PlatformError, preserving uncertain-result/recovery semantics for already sent mutations. Do not echo decoder messages or upstream payload. Add a streaming malformed-encoding regression for each provider or the shared transport.

## Verification and positive checks

- Reviewed the exact raw-byte WeChat response/notification signature construction, exact serial key lookup, certificate serial and signing-time validity checks, AESGCM decryption, event/resource/state agreement, and total versus payer_total semantics.
- Reviewed Alipay sorted decoded form canonicalization, duplicate decoded key rejection, exact expected raw JSON object extraction, nested duplicate JSON rejection, and Decimal/integer amount conversion. No parse-then-reserialize signature path found.
- Reviewed paid mappings and required paid amount/transaction/time, unknown-state rejection, close-then-authenticated-query, and refund observations. Cumulative Alipay refund notices produce `refund_observed` without per-refund compensation amounts; refund initiation follows with authenticated query, and code 10000 alone does not mark refunded.
- Reviewed fixed HTTPS endpoints, default TLS verification, no redirects, trust_env=False, explicit timeout and streaming size bound. Config repr excludes private/public key material and APIv3 key. Provider code contains no payload/signature logging.
- Independently ran the temporary targeted probe through `services/platform/.venv/bin/python` with `PYTHONPATH=services/platform/src`. It reproduced the two findings. Additional probes confirmed changed AES-GCM AAD fails with `payment_invalid_ciphertext` and duplicate Alipay response JSON fields fail with `payment_duplicate_fields`.
- Did **not** repeat the implementer-reported 65-test provider suite. The 65 passed figure remains implementer evidence rather than a new reviewer test result. No database, core, browser, merchant-config, real payment, or refund acceptance is claimed.

## Official-document cross-check

Ran Context7 outside the sandbox: library resolution for Alipay, then one focused docs query against `/websites/opendocs_alipay` about `alipay.trade.fastpay.refund.query`. No quota error. Current official results corroborate that `refund_status=REFUND_SUCCESS` proves success, while missing refund_status can mean unreceived/failed/not-yet-processed; code 10000 is query success only. `gmt_refund_pay` is an optional query option, so not requiring that time for final refund facts is justified. Sources: https://opendocs.alipay.com/open/02f3vb and https://opendocs.alipay.com/mini/07wx9g.

The same official schema makes refund-query business fields conditionally optional. An independent signed code-10000-only probe yields `payment_order_mismatch` with retryable=False. At the parent reviewer's explicit request, I performed a narrow read-only integration check: `payment_runtime.py:93-98` routes every PlatformError to error completion regardless of retryable; `billing/service.py:1071-1091` retains retry and enqueues a query after a mutation error; its error path does not unfreeze refunds. Therefore this currently produces a stable error followed by recovery, not success or a definitive refund failure. **No third finding is raised.** A more descriptive incomplete/uncertain error would improve diagnostics but is not necessary to prevent premature compensation or release under the current integration. No extra API call or sleep is recommended inside the adapter.
