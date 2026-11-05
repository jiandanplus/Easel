--
-- PostgreSQL database dump
--

\restrict easelB2SchemaComparison20261030

-- Dumped from database version 16.15 (Debian 16.15-1.pgdg13+2)
-- Dumped by pg_dump version 16.15 (Debian 16.15-1.pgdg13+2)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Data for Name: billing_accounts; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.billing_accounts (id, tenant_id) FROM stdin;
\.


--
-- Data for Name: billing_offer_withdrawals; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.billing_offer_withdrawals (offer_id, reason, operator_id, created_at) FROM stdin;
\.


--
-- Data for Name: billing_offers; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.billing_offers (id, plan_id, version, definition, created_by, created_at) FROM stdin;
\.


--
-- Data for Name: billing_orders; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.billing_orders (id, user_id, product_id, tenant_id, billing_account_id, offer_id, offer, idempotency_key, body_hash, channel, mode, merchant_id, app_id, amount_minor, currency, status, credit_state, checkout, membership_id, provider_transaction_id, reconciliation_reason, paid_at, expires_at, revision, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: billing_outbox; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.billing_outbox (id, kind, order_id, refund_id, dedupe_key, payload, status, attempts, due_at, lease_token, lease_until, last_error, created_at) FROM stdin;
\.


--
-- Data for Name: billing_refunds; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.billing_refunds (id, order_id, operator_id, idempotency_key, amount_minor, reason, status, provider_refund_id, reconciliation_reason, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: console_acl_events; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.console_acl_events (id, admin_id, request_id, action, role, operator_id, created_at) FROM stdin;
\.


--
-- Data for Name: console_admins; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.console_admins (id, issuer, subject, role, active, authorization_epoch, created_by, created_at) FROM stdin;
\.


--
-- Data for Name: console_audit_events; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.console_audit_events (id, operation_id, actor_id, action, target_type, target_id, reason, key_hash, request_hash, created_at) FROM stdin;
\.


--
-- Data for Name: console_identity_aliases; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.console_identity_aliases (issuer, subject, admin_id, migration_entry_id, created_at) FROM stdin;
\.


--
-- Data for Name: console_login_requests; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.console_login_requests (id, issuer, subject, subject_hash, status, verified_at, expires_at, approved_admin_id) FROM stdin;
\.


--
-- Data for Name: console_login_transactions; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.console_login_transactions (id, state_hash, nonce_hash, cookie_hash, created_at, expires_at, consumed) FROM stdin;
\.


--
-- Data for Name: console_operations; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.console_operations (id, actor_id, key_hash, request_hash, action, target_type, target_id, result, created_at) FROM stdin;
\.


--
-- Data for Name: console_reconciliation_cases; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.console_reconciliation_cases (job_id, state, revision, actor_id, reason, snapshot_hash, last_operation_id, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: console_sessions; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.console_sessions (session_hash, admin_id, csrf_token, authorization_epoch, created_at, expires_at, revoked_at) FROM stdin;
\.


--
-- Data for Name: credit_entries; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.credit_entries (id, journal_id, billing_account_id, bucket, delta) FROM stdin;
\.


--
-- Data for Name: credit_holds; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.credit_holds (reservation_id, lot_id, billing_account_id, amount) FROM stdin;
\.


--
-- Data for Name: credit_journals; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.credit_journals (id, billing_account_id, kind, source, operator_id, created_at, created_xid) FROM stdin;
\.


--
-- Data for Name: credit_lots; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.credit_lots (id, billing_account_id, membership_id, issued, available, held, spent, expired, expires_at, created_at, refunded, frozen, test_only) FROM stdin;
\.


--
-- Data for Name: credit_reservations; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.credit_reservations (id, billing_account_id, tenant_id, user_id, product_id, operation, idempotency_key, body_hash, rate_version, amount, actual, status, completion_key, created_at, updated_at, created_xid, funding_mode) FROM stdin;
\.


--
-- Data for Name: execution_blocked_bindings; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.execution_blocked_bindings (binding_hash, reason, created_at) FROM stdin;
\.


--
-- Data for Name: execution_invocations; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.execution_invocations (id, scope, session_id, user_id, product_id, billing_account_id, invocation_key, model, funding_mode, operation_count, held, spent, created_at, expires_at) FROM stdin;
\.


--
-- Data for Name: execution_jobs; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.execution_jobs (id, invocation_id, operation_key, body_hash, output_limit, scope, billing_account_id, user_id, product_id, model, funding_mode, model_definition, binding_hash, attempt_id, reservation_id, status, amount, actual, reason, lease_until, dispatched_at, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: execution_model_states; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.execution_model_states (model_id, enabled, operator_id, updated_at) FROM stdin;
\.


--
-- Data for Name: execution_models; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.execution_models (id, definition, operator_id, created_at) FROM stdin;
\.


--
-- Data for Name: execution_provider_associations; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.execution_provider_associations (job_id, kind, attempt_id, binding_hash, provider_scope_hash, http_request_id, provider_response_id, http_status, created_at) FROM stdin;
\.


--
-- Data for Name: execution_provider_attempts; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.execution_provider_attempts (job_id, attempt_id, binding_hash, provider_scope_hash, provider_request_hash, upstream_model, request_id, created_at) FROM stdin;
\.


--
-- Data for Name: execution_receipts; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.execution_receipts (job_id, attempt_id, input_tokens, output_tokens, terminal_status, provider_response_id, evidence_hash, actual, created_at) FROM stdin;
\.


--
-- Data for Name: execution_resolution_operations; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.execution_resolution_operations (id, actor_id, operation_key, plan_hash, plan, result, created_at) FROM stdin;
\.


--
-- Data for Name: execution_resolutions; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.execution_resolutions (job_id, operation_id, kind, attempt_id, binding_hash, scope_hash, reservation_id, original_amount, customer_charge, provider_usage_known, actor_id, reason, approval_reference, plan_hash, snapshot_hash, created_at) FROM stdin;
\.


--
-- Data for Name: execution_results; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.execution_results (job_id, payload, content_type, expires_at) FROM stdin;
\.


--
-- Data for Name: execution_task_tokens; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.execution_task_tokens (token_hash, invocation_id, expires_at, created_at) FROM stdin;
\.


--
-- Data for Name: execution_terminal_evidence; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.execution_terminal_evidence (job_id, attempt_id, binding_hash, provider_scope_hash, provider_response_id, input_tokens, output_tokens, terminal_status, evidence_hash, created_at) FROM stdin;
\.


--
-- Data for Name: external_identities; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.external_identities (issuer, subject, user_id, email, email_verified) FROM stdin;
\.


--
-- Data for Name: identity_handoffs; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.identity_handoffs (code_hash, transaction_id, user_id, tenant_id, billing_account_id, issuer, subject, expires_at, consumed) FROM stdin;
\.


--
-- Data for Name: identity_migration_entries; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.identity_migration_entries (id, migration_id, kind, subject, candidate) FROM stdin;
\.


--
-- Data for Name: identity_migrations; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.identity_migrations (id, proof_id, environment, key_hash, request_hash, operator_id, reason, result_sha256, created_at, created_xid) FROM stdin;
\.


--
-- Data for Name: identity_restore_candidates; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.identity_restore_candidates (proof_id, kind, subject, candidate) FROM stdin;
\.


--
-- Data for Name: identity_restore_proofs; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.identity_restore_proofs (id, policy_sha256, evidence, candidates, candidate_sha256, operator_id, created_at, expires_at, created_xid) FROM stdin;
\.


--
-- Data for Name: identity_sessions; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.identity_sessions (id, user_id, tenant_id, billing_account_id, product_id, client_id, authorization_epoch, issuer, subject, created_at, expires_at, revoked_at, source_transaction_id) FROM stdin;
\.


--
-- Data for Name: identity_tokens; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.identity_tokens (token_hash, session_id, kind, expires_at, consumed) FROM stdin;
\.


--
-- Data for Name: identity_transactions; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.identity_transactions (id, product_id, client_id, callback_uri, handoff_challenge, local_state, expires_at, state_hash, cookie_hash, nonce_hash, consumed) FROM stdin;
\.


--
-- Data for Name: internal_feature_extensions; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.internal_feature_extensions (id, grant_id, environment_id, user_id, tenant_id, billing_account_id, product_id, feature, starts_at, expires_at, operator_id, issuance_key, reason, created_at) FROM stdin;
\.


--
-- Data for Name: internal_grants; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.internal_grants (id, environment_id, billing_account_id, user_id, tenant_id, session_id, issuer, source_transaction_id, membership_id, lot_id, operator_id, issuance_key, reason, policy_sha256, source, starts_at, expires_at, created_at, created_xid) FROM stdin;
\.


--
-- Data for Name: media_artifacts; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.media_artifacts (id, job_id, invocation_id, attempt_id, scope, user_id, product_id, tenant_id, billing_account_id, storage_key, sha256, size, mime, properties, created_at, expires_at, purged_at) FROM stdin;
\.


--
-- Data for Name: media_assets; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.media_assets (id, invocation_id, scope, user_id, product_id, tenant_id, billing_account_id, storage_key, sha256, size, mime, properties, created_at, expires_at, purged_at) FROM stdin;
\.


--
-- Data for Name: media_blobs; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.media_blobs (storage_key, kind, scope, user_id, tenant_id, billing_account_id, invocation_id, job_id, attempt_id, sha256, size, mime, properties, state, attached_id, created_at, expires_at, purged_at, deleted_at, gc_token, gc_lease_until) FROM stdin;
\.


--
-- Data for Name: media_blocked_bindings; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.media_blocked_bindings (binding_hash, reason, created_at) FROM stdin;
\.


--
-- Data for Name: media_http_response_references; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.media_http_response_references (job_id, attempt_id, binding_hash, provider_scope_hash, namespace, http_request_id, response_body_sha256, created_at) FROM stdin;
\.


--
-- Data for Name: media_invocations; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.media_invocations (id, scope, session_id, user_id, product_id, tenant_id, billing_account_id, invocation_key, model, capability, funding_mode, operation_count, held, spent, created_at, expires_at) FROM stdin;
\.


--
-- Data for Name: media_jobs; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.media_jobs (id, invocation_id, operation_key, request_hash, request, input_units, scope, user_id, product_id, tenant_id, billing_account_id, model, capability, funding_mode, model_definition, binding_hash, attempt_id, reservation_id, status, execution_state, delivery_state, amount, actual, reason, lease_token, lease_until, dispatched_at, cancel_requested_at, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: media_model_states; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.media_model_states (model_id, enabled, operator_id, updated_at) FROM stdin;
\.


--
-- Data for Name: media_models; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.media_models (id, definition, operator_id, created_at) FROM stdin;
\.


--
-- Data for Name: media_provider_associations; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.media_provider_associations (job_id, kind, attempt_id, binding_hash, provider_scope_hash, http_request_id, http_status, provider_response_id, provider_job_id, created_at) FROM stdin;
\.


--
-- Data for Name: media_provider_attempts; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.media_provider_attempts (job_id, attempt_id, binding_hash, provider_scope_hash, provider_request_hash, upstream_model, request_id, created_at) FROM stdin;
\.


--
-- Data for Name: media_receipts; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.media_receipts (job_id, attempt_id, quantities, basis_evidence, evidence_hash, terminal_status, provider_response_id, actual, created_at, reference_kind, http_reference_namespace, http_reference_value) FROM stdin;
\.


--
-- Data for Name: media_storage_budgets; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.media_storage_budgets (job_id, scope, user_id, tenant_id, billing_account_id, max_bytes, max_files, remaining_bytes, remaining_files, created_at, released_at) FROM stdin;
\.


--
-- Data for Name: media_storage_policy; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.media_storage_policy (id, owner_max_bytes, owner_max_files, global_max_bytes, global_max_files, ttl_seconds) FROM stdin;
fixture_cell_0	fixture_cell_1	fixture_cell_2	fixture_cell_3	fixture_cell_4	fixture_cell_5
\.


--
-- Data for Name: media_task_tokens; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.media_task_tokens (token_hash, invocation_id, expires_at, created_at) FROM stdin;
\.


--
-- Data for Name: media_terminal_evidence; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.media_terminal_evidence (job_id, attempt_id, binding_hash, terminal_status, provider_response_id, quantities, basis_evidence, evidence_hash, created_at, reference_kind, http_reference_namespace, http_reference_value) FROM stdin;
\.


--
-- Data for Name: media_voices; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.media_voices (id, job_id, scope, user_id, product_id, tenant_id, billing_account_id, provider_id, provider_version, provider_voice_id, properties, created_at, expires_at, revoked_at) FROM stdin;
\.


--
-- Data for Name: membership_revocations; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.membership_revocations (membership_id, reason, order_id, created_at) FROM stdin;
\.


--
-- Data for Name: memberships; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.memberships (id, billing_account_id, plan_id, operator_id, issuance_key, starts_at, expires_at, created_at) FROM stdin;
\.


--
-- Data for Name: payment_event_audits; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.payment_event_audits (id, event_id, order_id, outcome, digest, created_at) FROM stdin;
\.


--
-- Data for Name: payment_events; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.payment_events (id, channel, mode, merchant_id, event_id, order_id, fact, created_at) FROM stdin;
\.


--
-- Data for Name: payment_orphan_events; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.payment_orphan_events (id, channel, mode, merchant_id, event_id, order_id, fingerprint, fact, created_at) FROM stdin;
\.


--
-- Data for Name: payment_refund_transactions; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.payment_refund_transactions (channel, mode, merchant_id, provider_refund_id, order_id, refund_id, created_at) FROM stdin;
\.


--
-- Data for Name: payment_transactions; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.payment_transactions (channel, mode, merchant_id, provider_transaction_id, order_id, event_id, created_at) FROM stdin;
\.


--
-- Data for Name: plans; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.plans (id, name, version, definition, created_by, created_at) FROM stdin;
\.


--
-- Data for Name: product_clients; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.product_clients (product_id, client_id, enabled) FROM stdin;
\.


--
-- Data for Name: products; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.products (id, enabled) FROM stdin;
\.


--
-- Data for Name: schema_migrations; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.schema_migrations (version, checksum) FROM stdin;
fixture_cell_0	fixture_cell_1
fixture_cell_0	fixture_cell_1
fixture_cell_0	fixture_cell_1
fixture_cell_0	fixture_cell_1
fixture_cell_0	fixture_cell_1
fixture_cell_0	fixture_cell_1
fixture_cell_0	fixture_cell_1
fixture_cell_0	fixture_cell_1
fixture_cell_0	fixture_cell_1
fixture_cell_0	fixture_cell_1
fixture_cell_0	fixture_cell_1
fixture_cell_0	fixture_cell_1
fixture_cell_0	fixture_cell_1
fixture_cell_0	fixture_cell_1
fixture_cell_0	fixture_cell_1
fixture_cell_0	fixture_cell_1
\.


--
-- Data for Name: tenant_members; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.tenant_members (tenant_id, user_id, role, active) FROM stdin;
\.


--
-- Data for Name: tenants; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.tenants (id, owner_user_id) FROM stdin;
\.


--
-- Data for Name: users; Type: TABLE DATA; Schema: public; Owner: fixture_role
--

COPY public.users (id, authorization_epoch, active, created_at) FROM stdin;
\.


--
-- Name: console_audit_events_id_seq; Type: SEQUENCE SET; Schema: public; Owner: fixture_role
--

SELECT pg_catalog.setval('public.console_audit_events_id_seq', 1, false);


--
-- Name: credit_entries_id_seq; Type: SEQUENCE SET; Schema: public; Owner: fixture_role
--

SELECT pg_catalog.setval('public.credit_entries_id_seq', 1, false);


--
-- Name: payment_event_audits_id_seq; Type: SEQUENCE SET; Schema: public; Owner: fixture_role
--

SELECT pg_catalog.setval('public.payment_event_audits_id_seq', 1, false);


--
-- PostgreSQL database dump complete
--

\unrestrict easelB2SchemaComparison20261030

