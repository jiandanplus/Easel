# Workbench Pi dialogue, tools and credit evidence review

2026-10-23. **PASS within the evidence boundaries below.** No blocking inconsistency was found in the two supplied safe receipts. This review read local safe evidence and independently recomputed its arithmetic and marker hash; it did not access remote services, private identities/configuration/session contents, or perform another model or financial operation.

## Frozen evidence

| File | SHA-256 |
| --- | --- |
| `workbench-usage-safe.json` | `2579cfe0401bc8719215b87feeec3889936f6a8b0793844312906a8fb1eee082` |
| `workbench-tool-safe.json` | `52ceb19e453787e8ac1f6d8eaaca65885f91d8d6e5bebfa7bc4570e564fe829b` |

The usage evidence covers the new isolated workbench environment from the grant start at 2026-10-23 11:23:47 UTC through the read-only observation at 11:31:48 UTC. It is not an audit of the previously archived S3B/Pi environments.

## Central usage and wallet

All four jobs are `settled`, have `completed` terminal receipts, and pass the recorded scope, rate, reservation and receipt consistency checks. They belong to two invocations, containing one and three jobs respectively. The frozen model is `easel-glm53-internal-v1`; the previously reviewed auditor checks rate version `internal-glm53-20261023-v1`, with input 1/2 and output 3/2 microcredits per token.

The reviewer independently recalculated each job as `ceil((input_tokens + 3 * output_tokens) / 2)`:

| Job in chronological order | Input tokens | Output tokens | Actual microcredits |
| --- | ---: | ---: | ---: |
| 1 | 627 | 166 | 563 |
| 2 | 699 | 585 | 1227 |
| 3 | 744 | 24 | 408 |
| 4 | 780 | 45 | 458 |
| Total | 2850 | 820 | **2656** |

Per-job rounding matters: summing the unrounded token costs across all jobs would yield 2655, which is not the implemented per-job settlement rule.

The wallet moves from 10,000,000 issued/available microcredits and zero spent/held to:

- issued: **10,000,000**;
- available: **9,997,344**;
- spent: **2656**;
- held, expired and refunded: **0**.

Thus `10,000,000 = 9,997,344 + 2656`. The immutable entry total is zero, and the audit reports wallet buckets equal the immutable entries, reservation sets equal job reservations, receipts equal the settled job set, and invocation counters equal the job totals. No nonsettled or pending-reconciliation hold remains in this evidence window. The receipt explicitly does not zero unknown usage.

There is exactly **one internal gift audit**. Billing orders, payment events and payment transactions are all **zero**. The supported description is an internal experience allowance followed by actual central settlement, not a purchase or successful payment. At one million microcredits per displayed point, the spend is 0.002656 points and the remaining balance is 9.997344 points. The fixed seven-day term was checked in the earlier grant implementation review; these two receipts do not independently expose and verify an expiry timestamp.

## Tool evidence

The supplied safe tool proof identifies persisted Pi SDK toolCall/toolResult messages and the actual workspace file as its source. It reports exactly one successful paired `write_file` and one successful paired `read_file`, both for `outputs/workbench-pi-proof-e6467e17.txt`. Both content checks, the successful final marker and the file hash agree:

`26e33aa25db8c2ccedc3a5c8a3b246bd4915efa8bd854ca432906755373cd04b`.

The reviewer independently recomputed this SHA-256 from the synthetic final marker `EASEL_WORKBENCH_PI_OK` and verified its 21-byte length. The receipt reports exact marker bytes with no trailing newline. This supports the controlled file write/read and successful final-marker outcome; it does not establish shell execution or any other unlisted tool. The reviewer did not reopen private raw SDK sessions, so event/file extraction is evidence supplied by the collector, not a second raw-session inspection.

## New API correlation and claim limits

Four gateway consumption rows for GLM 5.3/channel 4 have token pairs matching the four central receipts. However, the audited raw response identifiers do not equal the gateway identifier fields under the reviewed exact-matching rule. The evidence therefore correctly records **`exact_matches=0` and `exact_one_to_one_proven=false`**. Keep that limitation in the final report; token matches alone are candidate correlation, not proved request identity. Do not silently strip or normalize response-ID prefixes and upgrade this claim without separately reviewing that mapping.

The gateway query covers consumption records in the selected window and is not an exhaustive count of every possible provider transport attempt. New API quota figures are not central microcredit charges. Central verified terminal usage and the immutable central ledger are the settlement authority here.

The supported final statement is: the supplied evidence shows four completed central model jobs across two invocations, a successful controlled Pi write/read task with final marker, exactly 2656 microcredits settled from a single internal experience gift, 9,997,344 microcredits available and no held amount at observation, with no payment rows. The separate plain-text task's exact answer and the user's login interaction are not independently inspected in these two receipts. This is internal workbench acceptance, not proof of commercial payment, production deployment, or clean-machine packaging acceptance. The audit receipt's `model_requests=0` means the auditor made no new model request; it does not negate the four historical jobs.

## Existing grant review status

`issue-experience-review.md` already includes the leading-hyphen CLI fix and PASS for wrapper SHA-256 `edf7551514545efe9e2b1e0a94be8ddccf81a8fd4a467ffcfdb811d9860dc369`. Its inverse-transform check proves that only wallet/session argv encoding changed from the prior approved wrapper. The current actual audit supplies the later evidence of one issued grant; the earlier source-readiness review was correctly not labelled as issuance evidence.

No credentials or complete user/session/tenant/wallet scope identifiers are reproduced in this review report.

## Supplemental account, actual expiry and browser evidence

**PASS.** This follow-up expands the initial two-receipt review above. It supersedes the earlier limitations concerning absence of an actual grant expiry and absence of a visible plain-text result. Other limits, especially the unproven exact New API request-ID correlation, remain unchanged.

Additional locally inspected evidence:

| File | SHA-256 |
| --- | --- |
| `approved-grant-safe.json` | `20e6e96bf9d3be44ca01d7de621b36443113a998be95781582adb42153389940` |
| `new-account-safe.json` | `5d35dc126423be61d597aefaefc81502740d4f5ba3ce6e6a0ace5964a717dd15` |
| `2026-10-23-workbench-text-success.png` | `d82779c2a43a4a331e069d1a2b2829336196add3bb5d59bc930211264e816eb1` |
| `2026-10-23-workbench-tool-success.png` | `8d6e4511db9778d3d32c54038f1a7b49d110d48e0e7eb526f7afade46af8df0f` |
| `2026-10-23-workbench-wallet-success.png` | `8a04fefcfb9284258ed4dff841e2b7d4eedf42f62ada84f9bf6e71fb17124da7` |

The account safe receipt confirms that the current session matches the newly registered account, a normal OIDC source is present, and the wallet target was verified. It declares that credentials were not exported. This reviewer inspected those safe assertions without opening identity records or raw session data; no username or full identity is reproduced here. Together with the root's reported live login observation, this supports describing the target as the user's newly registered, normally authenticated account rather than a synthetic seeded identity.

The actual grant safe receipt records `funding_source=internal_experience`, 10,000,000 microcredits, one audited grant, successful same-key replay without additional credit, and unchanged zero payment rows. Its wallet hash matches the usage audit. Its recorded interval is **2026-10-23T11:23:47.552184+00:00 to 2026-10-30T11:23:47.552184+00:00**. Independent datetime subtraction yields exactly **604800 seconds / seven days**. The actual validity term is therefore verified from the supplied receipt, not merely inferred from the fixed policy.

The root reported reopening the actual browser sessions. The reviewer independently viewed the three saved screenshots:

- The text screenshot visibly shows the synthetic no-tool request and the exact answer **`EASEL_TEXT_READY`**. It provides visual confirmation of the plain-text result, in addition to the settled model receipts.
- The tool screenshot visibly shows `write_file` and `read_file` in the execution area and the exact final answer **`EASEL_WORKBENCH_PI_OK`**, consistent with the previously reviewed tool-event/file proof. The screenshot alone does not replace that underlying tool proof.
- The wallet screenshot visibly shows **9.997344 available**, **0.002656 spent**, **10 issued**, and **0 reserved / pending reconciliation**. Expired, frozen, refunded and test-available values are also zero. These displayed points agree with the microcredit audit and its one-million-to-one unit conversion.

These additions support the complete internal acceptance statement: a newly registered normal OIDC account received one seven-day internal experience allowance; the workbench displayed the successful text marker and controlled Pi write/read result; four completed model jobs settled 2656 microcredits; the wallet displayed the matching remaining balance with no outstanding hold. The allowance remains **non-payment**, and the gateway correlation remains **not exactly proved** under the reviewed identifier rule. No additional live request, model call, remote access or financial mutation was performed for this supplementary review.
