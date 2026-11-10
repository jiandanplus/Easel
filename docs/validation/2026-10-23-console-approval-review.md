# Exact Console administrator approval path — read-only review

Verdict: **PASS for the current exact verified-request owner-CLI approval path; no blocking source defect found.** No grant, SQL connection, service/network/browser action, credential read or source edit occurred. This review does not independently verify the real user's registration or pending request; root owns that actual browser/server correlation.

## Current guards

ConsoleAuth.callback creates a request only after a one-use state/cookie/PKCE/nonce-bound OIDC exchange with strict signed auth_time and freshness. The stored issuer/subject are obtained from the verified identity, not an email or user-supplied grant field. console_login_requests.verified_at is server-generated. The approval method does not re-exchange the OIDC token: it approves the stored verified request.

approve_request accepts only a32hex request ID and one of admin/operator/viewer. On the same database connection it calls _owner: OperatorIdentity must contain console:bootstrap, current_user must equal session_user and be the actual owner of current_schema. It SELECTs the exact request ID FOR UPDATE, requires the row exists, its expires_at exceeds clock_timestamp at validation and issuer exactly equals configured Console issuer. A subject-hash advisory lock serializes approvals for that identity, then issuer+subject selects the existing ACL row FOR UPDATE. Only that precise pair is inserted when absent. Existing disabled or different-role ACL gives409; approval cannot reactivate or change another role. An already-approved request is an idempotent success only for its existing matching ACL, without another approve event.

The request status update, exact admin ACL insert and one immutable console_acl_events action=approve row commit in the same transaction. UNIQUE(issuer,subject) and the request status/approved_admin_id consistency constraint support that binding. approve_request returns only admin/request IDs, role, active and subject_hash; it does not create an administrator session.

operate_console requires explicit CENTRAL_CONSOLE_MIGRATION_DATABASE_URL, compares owner/runtime scheme+host+port+database path, opens the configured explicit schema using the owner DSN, and separately checks actual schema owner/current_user=session_user before dispatch. It constructs a bootstrap-only OperatorIdentity for owner-side console-requests/approve/disable. The Web serve path rejects owner DSN variables and has no approve route. This is an owner-CLI authority gate, not an automatically promoted ordinary product login.

## Minimum post-approval acceptance

1. Save a safe owner-side approval receipt containing only the exact request ID or its hash, returned admin ID, role=admin, active=true and subject_hash. Match that subject_hash to the already correlated pending request. Do not export raw subject, credentials, auth code, cookies or session/CSRF tokens.
2. Read only the target request/ACL/event projection: request status=approved and approved_admin_id equals returned ID; exact matched ACL active with role=admin; exactly one console_acl_events approve event for that request/admin/role and the intended operator_id. This event is in console_acl_events, not the business /api/audits list. Confirm other ACL rows unchanged and no extra approvals. Aggregate count alone is insufficient proof of identity matching.
3. Have the user start a fresh ordinary Console login from the homepage. Approval itself creates no session and the pending callback code must not be replayed. After the new verified login, /api/session should return the same approved admin ID, role=admin and exactly console:read/plans:publish/billing:publish/execution:publish. Record only those safe fields; never persist the returned CSRF token or session cookie in evidence.
4. Make the minimal actual authenticated read: /api/overview and one representative bounded list/detail (for example models and wallet) succeed; UI identity/financial labels render. Record status codes, row counts and safe IDs/hashes rather than raw tables. Anonymous API access should remain401. Reads must not create grants/payments/executions or catalog operations/audits; compare only scoped counts or preserved safe snapshots, not unrelated users' sessions.
5. Real business-write acceptance requires a distinct actual catalog operation with a bounded fixture and preservation evidence; it is unnecessary for the minimal approval/login/read checks above. Existing live-model toggles or saleable-plan publication are outside this read-only review. Approval+read success may be called real administrator login/read acceptance, not real catalog-write acceptance. Logout/revocation acceptance likewise remains a distinct deliberate step if performed.

Relevant existing public tests cover capability denial, unknown ID/role, disabled ACL non-reactivation, replay idempotence, expired request denial, wrong issuer/subject and fresh signed identity. They were source-inspected only in this pass; no tests rerun or service queried.

## Source anchors

- services/platform/src/platform_service/console_auth.py: `e096caea5b99e05ec69425839a5956aa1d8a456aaa560880d873579bef837f9e`
- services/platform/src/platform_service/console_cli.py: `5eed610ade26db26c82293fe56adcb5a9cfe1e3bba79dc476d42a7069826b22d`
- services/platform/src/platform_service/migrations/008_console_auth.sql: `cd8997b72407e760a87fbd0b78cc43a676014d3dc820b3c9c1c86b3a0e652195`
