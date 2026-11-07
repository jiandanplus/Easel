-- Explicit authenticated HTTP references remain distinct from provider objects.
-- The legacy object branch retains its original values and evidence semantics.
ALTER TABLE media_provider_attempts ADD CONSTRAINT media_attempt_reference_identity
 UNIQUE(job_id,attempt_id,binding_hash,provider_scope_hash);
CREATE TABLE media_http_response_references (
 job_id text PRIMARY KEY REFERENCES media_jobs(id), attempt_id text NOT NULL,
 binding_hash text NOT NULL, provider_scope_hash text NOT NULL,
 namespace text NOT NULL,
 http_request_id text NOT NULL,
 CHECK((namespace='openai_x_request_id_v1' AND http_request_id ~ '^req_[A-Za-z0-9_-]{1,192}$')
    OR (namespace='newapi_x_oneapi_request_id_v1' AND http_request_id ~ '^[0-9]{23}[A-Za-z0-9]{16}$')),
 response_body_sha256 text NOT NULL CHECK(response_body_sha256 ~ '^[a-f0-9]{64}$'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(job_id,attempt_id,binding_hash,provider_scope_hash)
 REFERENCES media_provider_attempts(job_id,attempt_id,binding_hash,provider_scope_hash),
 UNIQUE(job_id,attempt_id,binding_hash,namespace,http_request_id,response_body_sha256),
 UNIQUE(provider_scope_hash,namespace,http_request_id)
);
CREATE TRIGGER media_http_response_immutable BEFORE UPDATE OR DELETE ON media_http_response_references
 FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER media_http_response_no_truncate BEFORE TRUNCATE ON media_http_response_references
 FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();

ALTER TABLE media_terminal_evidence
 ALTER COLUMN provider_response_id DROP NOT NULL,
 ADD COLUMN reference_kind text NOT NULL DEFAULT 'object_id',
 ADD COLUMN http_reference_namespace text,
 ADD COLUMN http_reference_value text,
 ADD CONSTRAINT media_terminal_reference_kind CHECK(
    (reference_kind='object_id' AND provider_response_id IS NOT NULL AND http_reference_namespace IS NULL AND http_reference_value IS NULL)
 OR (reference_kind='http_request_id' AND provider_response_id IS NULL AND http_reference_namespace IS NOT NULL AND http_reference_value IS NOT NULL AND
     ((http_reference_namespace='openai_x_request_id_v1' AND http_reference_value ~ '^req_[A-Za-z0-9_-]{1,192}$')
       OR (http_reference_namespace='newapi_x_oneapi_request_id_v1' AND http_reference_value ~ '^[0-9]{23}[A-Za-z0-9]{16}$')))),
 ADD CONSTRAINT media_terminal_http_reference FOREIGN KEY(job_id,attempt_id,binding_hash,http_reference_namespace,http_reference_value,evidence_hash)
 REFERENCES media_http_response_references(job_id,attempt_id,binding_hash,namespace,http_request_id,response_body_sha256)
 DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE media_receipts
 ALTER COLUMN provider_response_id DROP NOT NULL,
 ADD COLUMN reference_kind text NOT NULL DEFAULT 'object_id',
 ADD COLUMN http_reference_namespace text,
 ADD COLUMN http_reference_value text,
 ADD CONSTRAINT media_receipt_reference_kind CHECK(
    (reference_kind='object_id' AND provider_response_id IS NOT NULL AND http_reference_namespace IS NULL AND http_reference_value IS NULL)
 OR (reference_kind='http_request_id' AND provider_response_id IS NULL AND http_reference_namespace IS NOT NULL AND http_reference_value IS NOT NULL AND
     ((http_reference_namespace='openai_x_request_id_v1' AND http_reference_value ~ '^req_[A-Za-z0-9_-]{1,192}$')
       OR (http_reference_namespace='newapi_x_oneapi_request_id_v1' AND http_reference_value ~ '^[0-9]{23}[A-Za-z0-9]{16}$'))));

CREATE FUNCTION check_media_http_reference() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM media_jobs j JOIN media_provider_associations a ON a.job_id=j.id
    WHERE j.id=NEW.job_id AND j.attempt_id=NEW.attempt_id AND j.binding_hash=NEW.binding_hash
      AND j.dispatched_at IS NOT NULL AND a.kind='http' AND a.attempt_id=NEW.attempt_id
      AND a.binding_hash=NEW.binding_hash AND a.provider_scope_hash=NEW.provider_scope_hash
      AND a.http_status=200 AND a.http_request_id=NEW.http_request_id) THEN
 RAISE EXCEPTION 'unverified authenticated media response' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;


CREATE CONSTRAINT TRIGGER media_http_reference_binding AFTER INSERT ON media_http_response_references
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_media_http_reference();

CREATE OR REPLACE FUNCTION check_media_reconciliation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE inv text; current_inv media_invocations%ROWTYPE; expected_count bigint; expected_held numeric; expected_spent numeric;
BEGIN
 IF TG_TABLE_NAME='media_invocations' THEN inv=NEW.id;
 ELSIF TG_TABLE_NAME IN ('media_receipts','media_terminal_evidence','media_provider_attempts','media_provider_associations','media_artifacts','media_voices','media_http_response_references') THEN SELECT invocation_id INTO inv FROM media_jobs WHERE id=NEW.job_id;
 ELSIF TG_TABLE_NAME='media_assets' THEN inv=NEW.invocation_id;
 ELSIF TG_TABLE_NAME='credit_reservations' THEN
   SELECT invocation_id INTO inv FROM media_jobs WHERE reservation_id=NEW.id;
   IF inv IS NULL AND NEW.operation='media' THEN RAISE EXCEPTION 'orphan media reservation' USING ERRCODE='23514'; END IF;
 ELSE inv=NEW.invocation_id; END IF;
 IF inv IS NULL THEN RETURN NULL; END IF;
 SELECT * INTO current_inv FROM media_invocations WHERE id=inv;
 IF current_inv.scope->>'user_id' IS DISTINCT FROM current_inv.user_id OR current_inv.scope->>'product_id' IS DISTINCT FROM current_inv.product_id
    OR current_inv.scope->>'tenant_id' IS DISTINCT FROM current_inv.tenant_id OR current_inv.scope->>'billing_account_id' IS DISTINCT FROM current_inv.billing_account_id
    OR current_inv.scope->>'session_id' IS DISTINCT FROM current_inv.session_id
    OR NOT EXISTS(SELECT 1 FROM identity_sessions s WHERE s.id=current_inv.session_id AND s.user_id=current_inv.user_id AND s.product_id=current_inv.product_id
       AND s.tenant_id=current_inv.tenant_id AND s.billing_account_id=current_inv.billing_account_id AND s.authorization_epoch::text=current_inv.scope->>'authorization_epoch')
    OR NOT EXISTS(SELECT 1 FROM media_models m WHERE m.id=current_inv.model AND m.definition->>'capability'=current_inv.capability) THEN
 RAISE EXCEPTION 'media invocation scope mismatch' USING ERRCODE='23514'; END IF;
 SELECT count(*),coalesce(sum(amount) FILTER(WHERE status IN ('reserved','dispatched','pending_reconciliation')),0),coalesce(sum(actual) FILTER(WHERE status='settled'),0)
 INTO expected_count,expected_held,expected_spent FROM media_jobs WHERE invocation_id=inv;
 IF (current_inv.operation_count,current_inv.held::numeric,current_inv.spent::numeric) IS DISTINCT FROM (expected_count,expected_held,expected_spent) THEN
 RAISE EXCEPTION 'media invocation reconciliation failed' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM media_jobs j JOIN credit_reservations r ON r.id=j.reservation_id WHERE j.invocation_id=inv AND
    (j.status<>r.status OR j.amount<>r.amount OR j.actual IS DISTINCT FROM r.actual OR j.billing_account_id<>r.billing_account_id
     OR j.user_id<>r.user_id OR j.product_id<>r.product_id OR j.tenant_id<>r.tenant_id OR j.funding_mode<>r.funding_mode
     OR j.scope<>current_inv.scope OR j.model<>current_inv.model OR j.capability<>current_inv.capability
     OR j.user_id<>current_inv.user_id OR j.product_id<>current_inv.product_id OR j.tenant_id<>current_inv.tenant_id OR j.billing_account_id<>current_inv.billing_account_id
     OR j.funding_mode<>current_inv.funding_mode OR j.model_definition<>(SELECT definition FROM media_models WHERE id=j.model)
     OR r.operation<>'media' OR r.idempotency_key<>j.id OR r.body_hash<>j.request_hash OR r.rate_version<>(j.model_definition->'rate'->>'version'))) THEN
 RAISE EXCEPTION 'media reservation reconciliation failed' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM media_jobs j WHERE j.invocation_id=inv AND
    ((j.status='reserved' AND (j.dispatched_at IS NOT NULL OR j.execution_state<>'queued' OR j.actual IS NOT NULL))
     OR (j.status IN ('dispatched','pending_reconciliation','settled') AND (j.dispatched_at IS NULL OR NOT EXISTS(SELECT 1 FROM media_provider_attempts a WHERE a.job_id=j.id)))
     OR (j.status='released' AND (j.dispatched_at IS NOT NULL OR j.actual<>0 OR j.execution_state<>'cancelled')))) THEN
 RAISE EXCEPTION 'media execution reconciliation failed' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM media_jobs j LEFT JOIN media_receipts r ON r.job_id=j.id LEFT JOIN media_terminal_evidence e ON e.job_id=j.id WHERE j.invocation_id=inv AND
    ((j.status='settled' AND (r.job_id IS NULL OR e.job_id IS NULL OR r.actual<>j.actual OR r.attempt_id<>j.attempt_id
      OR (r.quantities,r.basis_evidence,r.evidence_hash,r.terminal_status,r.provider_response_id,r.reference_kind,r.http_reference_namespace,r.http_reference_value) IS DISTINCT FROM (e.quantities,e.basis_evidence,e.evidence_hash,e.terminal_status,e.provider_response_id,e.reference_kind,e.http_reference_namespace,e.http_reference_value)))
     OR (j.status<>'settled' AND r.job_id IS NOT NULL))) THEN
 RAISE EXCEPTION 'media receipt reconciliation failed' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM media_provider_attempts a JOIN media_jobs j ON j.id=a.job_id WHERE j.invocation_id=inv AND (a.attempt_id<>j.attempt_id OR a.binding_hash<>j.binding_hash))
 OR EXISTS(SELECT 1 FROM media_provider_associations a JOIN media_provider_attempts p ON p.job_id=a.job_id JOIN media_jobs j ON j.id=a.job_id WHERE j.invocation_id=inv AND
    (a.attempt_id<>p.attempt_id OR a.binding_hash<>p.binding_hash OR a.provider_scope_hash<>p.provider_scope_hash))
 OR EXISTS(SELECT 1 FROM media_terminal_evidence e JOIN media_jobs j ON j.id=e.job_id WHERE j.invocation_id=inv AND
    (e.attempt_id<>j.attempt_id OR e.binding_hash<>j.binding_hash OR e.terminal_status<>j.execution_state
     OR NOT EXISTS(SELECT 1 FROM media_provider_associations a WHERE a.job_id=j.id AND a.kind='http')
     OR (e.reference_kind='object_id' AND NOT EXISTS(SELECT 1 FROM media_provider_associations a WHERE a.job_id=j.id AND a.kind='response' AND a.provider_response_id=e.provider_response_id))
     OR (e.reference_kind='http_request_id' AND NOT EXISTS(SELECT 1 FROM media_http_response_references h WHERE h.job_id=j.id AND h.attempt_id=e.attempt_id AND h.binding_hash=e.binding_hash AND h.namespace=e.http_reference_namespace AND h.http_request_id=e.http_reference_value AND h.response_body_sha256=e.evidence_hash)))) THEN
 RAISE EXCEPTION 'media evidence binding mismatch' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM media_assets a WHERE a.invocation_id=inv AND (a.scope<>current_inv.scope OR a.user_id<>current_inv.user_id OR a.product_id<>current_inv.product_id OR a.tenant_id<>current_inv.tenant_id OR a.billing_account_id<>current_inv.billing_account_id))
 OR EXISTS(SELECT 1 FROM media_artifacts a JOIN media_jobs j ON j.id=a.job_id WHERE j.invocation_id=inv AND
    (a.invocation_id<>j.invocation_id OR a.attempt_id<>j.attempt_id OR a.scope<>j.scope OR a.user_id<>j.user_id OR a.product_id<>j.product_id OR a.tenant_id<>j.tenant_id OR a.billing_account_id<>j.billing_account_id))
 OR EXISTS(SELECT 1 FROM media_voices a JOIN media_jobs j ON j.id=a.job_id WHERE j.invocation_id=inv AND
    (a.scope<>j.scope OR a.user_id<>j.user_id OR a.product_id<>j.product_id OR a.tenant_id<>j.tenant_id OR a.billing_account_id<>j.billing_account_id)) THEN
 RAISE EXCEPTION 'media content scope mismatch' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;

CREATE CONSTRAINT TRIGGER media_http_reference_reconciliation AFTER INSERT ON media_http_response_references
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_media_reconciliation();

-- Non-financial, append-only image access on precisely the original grant.
CREATE TABLE internal_feature_extensions (
 id text PRIMARY KEY, grant_id text NOT NULL REFERENCES internal_grants(id),
 environment_id text NOT NULL, user_id text NOT NULL REFERENCES users(id), tenant_id text NOT NULL,
 billing_account_id text NOT NULL, product_id text NOT NULL CHECK(product_id='easel'),
 feature text NOT NULL CHECK(feature='image'), starts_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
 operator_id text NOT NULL CHECK(length(btrim(operator_id))>=1 AND length(btrim(operator_id))<=256),
 issuance_key text NOT NULL CHECK(length(btrim(issuance_key))>=1 AND length(btrim(issuance_key))<=256),
 reason text NOT NULL CHECK(length(btrim(reason))>=1 AND length(btrim(reason))<=256),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(environment_id,issuance_key), UNIQUE(grant_id,product_id,feature),
 FOREIGN KEY(billing_account_id,tenant_id) REFERENCES billing_accounts(id,tenant_id),
 FOREIGN KEY(tenant_id,user_id) REFERENCES tenant_members(tenant_id,user_id)
);
CREATE TRIGGER internal_feature_extensions_immutable BEFORE UPDATE OR DELETE ON internal_feature_extensions
 FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER internal_feature_extensions_no_truncate BEFORE TRUNCATE ON internal_feature_extensions
 FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();
CREATE FUNCTION check_internal_feature_extension() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM internal_grants g JOIN memberships m ON m.id=g.membership_id
     JOIN credit_lots l ON l.id=g.lot_id JOIN tenants t ON t.id=g.tenant_id AND t.owner_user_id=g.user_id
     WHERE g.id=NEW.grant_id AND g.environment_id=NEW.environment_id
       AND (g.user_id,g.tenant_id,g.billing_account_id,g.starts_at,g.expires_at)
           IS NOT DISTINCT FROM (NEW.user_id,NEW.tenant_id,NEW.billing_account_id,NEW.starts_at,NEW.expires_at)
       AND m.billing_account_id=g.billing_account_id AND m.plan_id='easel-internal-experience-v1'
       AND (m.starts_at,m.expires_at) IS NOT DISTINCT FROM (g.starts_at,g.expires_at)
       AND l.membership_id=m.id AND l.billing_account_id=g.billing_account_id AND NOT l.test_only
       AND g.starts_at<=clock_timestamp() AND g.expires_at>clock_timestamp()
       AND NOT EXISTS(SELECT 1 FROM membership_revocations r WHERE r.membership_id=m.id)) THEN
 RAISE EXCEPTION 'internal feature grant binding divergence' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER internal_feature_extension_binding AFTER INSERT ON internal_feature_extensions
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_internal_feature_extension();
