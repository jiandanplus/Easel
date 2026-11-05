# Independent final real-administrator read-only acceptance

Verdict: **PASS for the deployed preview's real administrator login and scoped read-only acceptance. No remaining blocker found in this scope.** This is not real catalog-write acceptance, production publication or a claim that all operational capabilities were exercised.

## Independently executed server and anonymous checks

The before snapshot was actually queried at 2026-10-23T13:28:30.583031+00:00; the after snapshot was actually queried at 2026-10-23T13:40:14.681037+00:00. Both use the same exact target administrator/request in the same isolated workbench schema. The owner connection is used only inside the server process, with default_transaction_read_only enabled and an explicit REPEATABLE READ READ ONLY transaction; statement/lock/connect timeouts are bounded. No operator configuration, session hash, CSRF, cookie, issuer subject or email was exported.

The after query confirms active role=admin, the exact request approved for that ACL, issuer+subject matching, exactly one matching immutable approval event, and one valid non-revoked epoch-matched target session. Existing hotfix SSH ControlPath was first checked alive; actual read used ProxyCommand=false, prohibiting direct fallback. No tunnel was stopped or restarted and no new master was required. An actual independent local GET /api/session with no Cookie/authentication header returned401.

The before canonical public source SHA fe67f78928dec4ed5f72176be27c8bba2ce4b4a19c7c4d15bb17a87e748d97b6 changed exactly to approved after SHA6d9aab3014d76f07fd13fd5f252714839df83f73fe5226983f8c1fa902c786a6. These values follow the reviewed provisioner's canonical sorted-JSON manifest calculation, not the raw manifest-file byte hash.

| Scope table | Before | After | Delta |
|---|---:|---:|---:|
| credit_journals | 15 | 15 | 0 |
| credit_entries | 37 | 37 | 0 |
| credit_lots | 1 | 1 | 0 |
| billing_orders | 0 | 0 | 0 |
| payment_transactions | 0 | 0 | 0 |
| execution_jobs | 7 | 7 | 0 |
| console_operations | 0 | 0 | 0 |
| console_audit_events | 0 | 0 | 0 |

These are aggregate count-preservation checks for the requested scope, not a row-by-row content digest or proof about unrelated concurrent users. All eight counts remained identical. No business write, grant, logout or model call was performed by this reviewer. The zero console_operations/console_audit_events values correctly show that opening the model dialog was not catalog submission; ACL approval is independently recorded in console_acl_events.

## Browser and hotfix receipts reviewed

Root's actual browser receipt shows the real administrator visible, overview ready plus all10 lists ready, including authentic zero-row orders/offers/audits. User/wallet/model/provider details worked; the wallet displayed internal experience source. Model-publication dialog was opened and closed without submit. Empty search/restore, repeated-current-page navigation and list→overview all passed. The historical navigation exception count remains1; new browser errors after repair are0. This browser work was performed by root; this reviewer checked the safe receipt and consistency, not independently replayed browser actions.

The original overview→users navigation bug was real and is not erased from evidence. The independently source-reviewed narrow fix clears stale data before changing page, cancels prior requests, forces same-page refresh and adds the list shape guard. Root's hotfix receipt confirms one new hashed JS asset, old assets retained, atomically updated index/asset receipt/public manifest, same owned PID, unchanged runtime/operator files, no restart and no database calls. Python/SQL and backend runtime were unchanged. Local frontend22-pass/build evidence belongs to the fix author/root; earlier full backend630-pass evidence remains relevant to unchanged backend, while the original wheel's old static bytes are not represented as the hotfixed frontend artifact.

## Accurate completion boundary

Real本人registration→exact owner approval→fresh real login→authenticated read-only UI is now supported by combined browser evidence and independent server session/ACL checks. This closes the earlier pending-real-admin/read boundary. No real catalog mutation was submitted. Publication/withdraw/state-change audit and rollback behavior remain covered by the local synthetic/real-PostgreSQL tests; they were not exercised against the live catalog during this acceptance. Logout/ACL-disable after this acceptance was not performed, preserving the user's active session.

Real merchant collection/refunds, model execution through Console, experience-grant Web operations, media/cloud execution, production domain/TLS, HA/load testing and backup restoration are not newly accepted claims. The service remains the isolated loopback/SSH preview. Mobile viewport acceptance has not been performed. Root noted a P3 cosmetic issue: model details show an extra empty product-session heading; record it as a non-blocking presentation issue without claiming it fixed. The final validation document should describe this scope, the preserved historical bug and its repair, and the difference between pre-hotfix wheel and installed static hotfix.

## Collector correctness notes

The first before collector attempt compared the manifest's raw byte hash to the provisioner's canonical-JSON hash and correctly refused; its hashing convention was corrected before the successful before receipt. The first after wrapper completed a read-only remote query but failed locally due to an HTTP variable shadowing the module; it wrote no receipt and caused no server mutation. After fixing that local variable, the exact read-only query and anonymous GET were rerun and the final after receipt succeeded. Neither collector failure is counted as a product test failure or a successful acceptance run.

## Exact reviewed receipt bytes

- admin-login-server-safe.json: `dd021440e3d788556d74e4623457c380b641592531b79e6036468f4f84e2ac22`
- admin-login-server-after-safe.json: `1d8feec6554b47b03a12ffe393fb0c709637ce67bcc12e6886ba73dadd7cf1f5`
- navigation-hotfix-safe.json: `ddcfd87685d2b8937a112aae8d76663c3f64a465832a0a0f4846f0af09f11514`
- admin-browser-safe.json: `3abc88094d6ffb5bc609360e0698d9578f1dcf222e650807401fbc54b6df6375`

## Final handoff

Root owns the actual11-page/4-detail/browser GET plus form-open/close proof. This reviewer owns the actually executed server before/after8-table read-only comparison and anonymous GET401. Root will close only the newly owned hotfix master; the two pre-existing tunnels remain. No further remote check was performed by this reviewer after the successful after receipt. Directory writes, real payment and mobile viewport remain unaccepted.
