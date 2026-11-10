-- Subscription media shares the original wallet; units never masquerade as text tokens.
CREATE TABLE media_models (
 id text PRIMARY KEY, definition jsonb NOT NULL, operator_id text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE media_model_states (
 model_id text PRIMARY KEY REFERENCES media_models(id), enabled boolean NOT NULL DEFAULT false,
 operator_id text NOT NULL, updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE media_blocked_bindings (
 binding_hash text PRIMARY KEY CHECK(binding_hash ~ '^[a-f0-9]{64}$'), reason text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE media_invocations (
 id text PRIMARY KEY, scope jsonb NOT NULL, session_id text NOT NULL REFERENCES identity_sessions(id),
 user_id text NOT NULL REFERENCES users(id), product_id text NOT NULL REFERENCES products(id),
 tenant_id text NOT NULL, billing_account_id text NOT NULL, invocation_key text NOT NULL,
 model text NOT NULL REFERENCES media_models(id), capability text NOT NULL,
 funding_mode text NOT NULL CHECK(funding_mode IN ('test','live')),
 operation_count integer NOT NULL DEFAULT 0 CHECK(operation_count BETWEEN 0 AND 32),
 held bigint NOT NULL DEFAULT 0 CHECK(held>=0), spent bigint NOT NULL DEFAULT 0 CHECK(spent>=0),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), expires_at timestamptz NOT NULL,
 UNIQUE(session_id,invocation_key), FOREIGN KEY(billing_account_id,tenant_id) REFERENCES billing_accounts(id,tenant_id),
 CHECK(held::numeric+spent<=100000000)
);
CREATE TABLE media_task_tokens (
 token_hash text PRIMARY KEY CHECK(token_hash ~ '^[a-f0-9]{64}$'),
 invocation_id text NOT NULL REFERENCES media_invocations(id), expires_at timestamptz NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE media_jobs (
 id text PRIMARY KEY, invocation_id text NOT NULL REFERENCES media_invocations(id),
 operation_key text NOT NULL, request_hash text NOT NULL CHECK(request_hash ~ '^[a-f0-9]{64}$'),
 request jsonb NOT NULL, input_units jsonb NOT NULL, scope jsonb NOT NULL,
 user_id text NOT NULL REFERENCES users(id), product_id text NOT NULL REFERENCES products(id), tenant_id text NOT NULL,
 billing_account_id text NOT NULL, model text NOT NULL REFERENCES media_models(id), capability text NOT NULL,
 funding_mode text NOT NULL CHECK(funding_mode IN ('test','live')), model_definition jsonb NOT NULL,
 binding_hash text NOT NULL CHECK(binding_hash ~ '^[a-f0-9]{64}$'), attempt_id text NOT NULL UNIQUE,
 reservation_id text NOT NULL UNIQUE REFERENCES credit_reservations(id),
 status text NOT NULL CHECK(status IN ('reserved','dispatched','pending_reconciliation','settled','released')),
 execution_state text NOT NULL DEFAULT 'queued' CHECK(execution_state IN ('queued','running','completed','failed','cancel_requested','cancelled','unknown')),
 delivery_state text NOT NULL DEFAULT 'processing' CHECK(delivery_state IN ('processing','available','unavailable','expired')),
 amount bigint NOT NULL CHECK(amount>0), actual bigint CHECK(actual>=0 AND actual<=amount), reason text,
 lease_token text, lease_until timestamptz NOT NULL, dispatched_at timestamptz, cancel_requested_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(billing_account_id,tenant_id) REFERENCES billing_accounts(id,tenant_id), UNIQUE(invocation_id,operation_key)
);
CREATE INDEX media_jobs_work_idx ON media_jobs(lease_until,id) WHERE status IN ('reserved','dispatched','pending_reconciliation');
CREATE TABLE media_provider_attempts (
 job_id text PRIMARY KEY REFERENCES media_jobs(id), attempt_id text NOT NULL UNIQUE,
 binding_hash text NOT NULL, provider_scope_hash text NOT NULL CHECK(provider_scope_hash ~ '^[a-f0-9]{64}$'),
 provider_request_hash text NOT NULL CHECK(provider_request_hash ~ '^[a-f0-9]{64}$'), upstream_model text NOT NULL,
 request_id text NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE media_provider_associations (
 job_id text NOT NULL REFERENCES media_provider_attempts(job_id), kind text NOT NULL CHECK(kind IN ('http','response','job')),
 attempt_id text NOT NULL, binding_hash text NOT NULL, provider_scope_hash text NOT NULL,
 http_request_id text, http_status integer CHECK(http_status BETWEEN 100 AND 599),
 provider_response_id text, provider_job_id text, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(job_id,kind),
 CHECK((kind='http' AND http_status IS NOT NULL AND provider_response_id IS NULL AND provider_job_id IS NULL)
    OR (kind='response' AND http_status IS NULL AND http_request_id IS NULL AND provider_response_id IS NOT NULL AND provider_job_id IS NULL)
    OR (kind='job' AND http_status IS NULL AND http_request_id IS NULL AND provider_response_id IS NULL AND provider_job_id IS NOT NULL))
);
CREATE UNIQUE INDEX media_provider_response_unique ON media_provider_associations(provider_scope_hash,provider_response_id) WHERE kind='response';
CREATE UNIQUE INDEX media_provider_job_unique ON media_provider_associations(provider_scope_hash,provider_job_id) WHERE kind='job';
CREATE TABLE media_terminal_evidence (
 job_id text PRIMARY KEY REFERENCES media_jobs(id), attempt_id text NOT NULL, binding_hash text NOT NULL,
 terminal_status text NOT NULL CHECK(terminal_status IN ('completed','failed','cancelled')),
 provider_response_id text NOT NULL, quantities jsonb NOT NULL CHECK(jsonb_typeof(quantities)='object'),
 basis_evidence jsonb NOT NULL CHECK(jsonb_typeof(basis_evidence)='object'),
 evidence_hash text NOT NULL CHECK(evidence_hash ~ '^[a-f0-9]{64}$'), created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE media_receipts (
 job_id text PRIMARY KEY REFERENCES media_terminal_evidence(job_id), attempt_id text NOT NULL,
 quantities jsonb NOT NULL, basis_evidence jsonb NOT NULL, evidence_hash text NOT NULL,
 terminal_status text NOT NULL, provider_response_id text NOT NULL, actual bigint NOT NULL CHECK(actual>=0),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE media_assets (
 id text PRIMARY KEY, invocation_id text NOT NULL REFERENCES media_invocations(id), scope jsonb NOT NULL,
 user_id text NOT NULL REFERENCES users(id), product_id text NOT NULL REFERENCES products(id), tenant_id text NOT NULL,
 billing_account_id text NOT NULL, storage_key text NOT NULL UNIQUE CHECK(storage_key ~ '^([a-f0-9]{32}|[a-f0-9]{64})$'),
 sha256 text NOT NULL CHECK(sha256 ~ '^[a-f0-9]{64}$'), size bigint NOT NULL CHECK(size>0 AND size<=67108864),
 mime text NOT NULL, properties jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 expires_at timestamptz NOT NULL, purged_at timestamptz,
 FOREIGN KEY(billing_account_id,tenant_id) REFERENCES billing_accounts(id,tenant_id)
);
CREATE TABLE media_artifacts (
 id text PRIMARY KEY, job_id text NOT NULL REFERENCES media_jobs(id), invocation_id text NOT NULL REFERENCES media_invocations(id),
 attempt_id text NOT NULL, scope jsonb NOT NULL, user_id text NOT NULL REFERENCES users(id), product_id text NOT NULL REFERENCES products(id),
 tenant_id text NOT NULL, billing_account_id text NOT NULL,
 storage_key text NOT NULL UNIQUE CHECK(storage_key ~ '^([a-f0-9]{32}|[a-f0-9]{64})$'),
 sha256 text NOT NULL CHECK(sha256 ~ '^[a-f0-9]{64}$'), size bigint NOT NULL CHECK(size>0 AND size<=67108864), mime text NOT NULL,
 properties jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(), expires_at timestamptz NOT NULL, purged_at timestamptz,
 FOREIGN KEY(billing_account_id,tenant_id) REFERENCES billing_accounts(id,tenant_id)
);
CREATE TABLE media_voices (
 id text PRIMARY KEY, job_id text NOT NULL UNIQUE REFERENCES media_jobs(id), scope jsonb NOT NULL,
 user_id text NOT NULL REFERENCES users(id), product_id text NOT NULL REFERENCES products(id), tenant_id text NOT NULL,
 billing_account_id text NOT NULL, provider_id text NOT NULL, provider_version text NOT NULL,
 provider_voice_id text NOT NULL, properties jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 expires_at timestamptz NOT NULL, revoked_at timestamptz,
 FOREIGN KEY(billing_account_id,tenant_id) REFERENCES billing_accounts(id,tenant_id)
);

CREATE FUNCTION guard_media_invocation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR (to_jsonb(NEW)-ARRAY['operation_count','held','spent']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['operation_count','held','spent']) THEN
 RAISE EXCEPTION 'immutable media invocation' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER media_invocation_guard BEFORE UPDATE OR DELETE ON media_invocations FOR EACH ROW EXECUTE FUNCTION guard_media_invocation();
CREATE FUNCTION guard_media_job() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'immutable media job' USING ERRCODE='23514'; END IF;
 IF (to_jsonb(NEW)-ARRAY['status','actual','execution_state','delivery_state','reason','lease_token','lease_until','dispatched_at','cancel_requested_at','updated_at']) IS DISTINCT FROM
    (to_jsonb(OLD)-ARRAY['status','actual','execution_state','delivery_state','reason','lease_token','lease_until','dispatched_at','cancel_requested_at','updated_at'])
    OR (OLD.dispatched_at IS NOT NULL AND NEW.dispatched_at IS DISTINCT FROM OLD.dispatched_at)
    OR (OLD.status IN ('settled','released') AND (NEW.status,NEW.actual,NEW.execution_state,NEW.reason) IS DISTINCT FROM (OLD.status,OLD.actual,OLD.execution_state,OLD.reason)) THEN
 RAISE EXCEPTION 'immutable media binding' USING ERRCODE='23514'; END IF;
 IF NEW.status<>OLD.status AND NOT ((OLD.status='reserved' AND NEW.status IN ('dispatched','released')) OR
    (OLD.status='dispatched' AND NEW.status IN ('pending_reconciliation','settled')) OR
    (OLD.status='pending_reconciliation' AND NEW.status='settled')) THEN RAISE EXCEPTION 'invalid media transition' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER media_job_guard BEFORE UPDATE OR DELETE ON media_jobs FOR EACH ROW EXECUTE FUNCTION guard_media_job();

CREATE FUNCTION guard_media_blob() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR (to_jsonb(NEW)-'purged_at') IS DISTINCT FROM (to_jsonb(OLD)-'purged_at') OR OLD.purged_at IS NOT NULL OR NEW.purged_at IS NULL OR OLD.expires_at>clock_timestamp() THEN
 RAISE EXCEPTION 'immutable media content' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='media_assets' AND EXISTS(SELECT 1 FROM media_jobs j WHERE j.invocation_id=OLD.invocation_id
    AND j.request->'assets' ? OLD.id AND j.execution_state IN ('queued','running','cancel_requested','unknown')) THEN
 RAISE EXCEPTION 'media input pinned by active job' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER media_asset_guard BEFORE UPDATE OR DELETE ON media_assets FOR EACH ROW EXECUTE FUNCTION guard_media_blob();
CREATE TRIGGER media_artifact_guard BEFORE UPDATE OR DELETE ON media_artifacts FOR EACH ROW EXECUTE FUNCTION guard_media_blob();

CREATE FUNCTION check_media_reconciliation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE inv text; current_inv media_invocations%ROWTYPE; expected_count bigint; expected_held numeric; expected_spent numeric;
BEGIN
 IF TG_TABLE_NAME='media_invocations' THEN inv=NEW.id;
 ELSIF TG_TABLE_NAME IN ('media_receipts','media_terminal_evidence','media_provider_attempts','media_provider_associations','media_artifacts','media_voices') THEN SELECT invocation_id INTO inv FROM media_jobs WHERE id=NEW.job_id;
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
      OR (r.quantities,r.basis_evidence,r.evidence_hash,r.terminal_status,r.provider_response_id) IS DISTINCT FROM (e.quantities,e.basis_evidence,e.evidence_hash,e.terminal_status,e.provider_response_id)))
     OR (j.status<>'settled' AND r.job_id IS NOT NULL))) THEN
 RAISE EXCEPTION 'media receipt reconciliation failed' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM media_provider_attempts a JOIN media_jobs j ON j.id=a.job_id WHERE j.invocation_id=inv AND (a.attempt_id<>j.attempt_id OR a.binding_hash<>j.binding_hash))
 OR EXISTS(SELECT 1 FROM media_provider_associations a JOIN media_provider_attempts p ON p.job_id=a.job_id JOIN media_jobs j ON j.id=a.job_id WHERE j.invocation_id=inv AND
    (a.attempt_id<>p.attempt_id OR a.binding_hash<>p.binding_hash OR a.provider_scope_hash<>p.provider_scope_hash))
 OR EXISTS(SELECT 1 FROM media_terminal_evidence e JOIN media_jobs j ON j.id=e.job_id WHERE j.invocation_id=inv AND
    (e.attempt_id<>j.attempt_id OR e.binding_hash<>j.binding_hash OR e.terminal_status<>j.execution_state
     OR NOT EXISTS(SELECT 1 FROM media_provider_associations a WHERE a.job_id=j.id AND a.kind='http')
     OR NOT EXISTS(SELECT 1 FROM media_provider_associations a WHERE a.job_id=j.id AND a.kind='response' AND a.provider_response_id=e.provider_response_id))) THEN
 RAISE EXCEPTION 'media evidence binding mismatch' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM media_assets a WHERE a.invocation_id=inv AND (a.scope<>current_inv.scope OR a.user_id<>current_inv.user_id OR a.product_id<>current_inv.product_id OR a.tenant_id<>current_inv.tenant_id OR a.billing_account_id<>current_inv.billing_account_id))
 OR EXISTS(SELECT 1 FROM media_artifacts a JOIN media_jobs j ON j.id=a.job_id WHERE j.invocation_id=inv AND
    (a.invocation_id<>j.invocation_id OR a.attempt_id<>j.attempt_id OR a.scope<>j.scope OR a.user_id<>j.user_id OR a.product_id<>j.product_id OR a.tenant_id<>j.tenant_id OR a.billing_account_id<>j.billing_account_id))
 OR EXISTS(SELECT 1 FROM media_voices a JOIN media_jobs j ON j.id=a.job_id WHERE j.invocation_id=inv AND
    (a.scope<>j.scope OR a.user_id<>j.user_id OR a.product_id<>j.product_id OR a.tenant_id<>j.tenant_id OR a.billing_account_id<>j.billing_account_id)) THEN
 RAISE EXCEPTION 'media content scope mismatch' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['media_models','media_blocked_bindings','media_task_tokens','media_provider_attempts','media_provider_associations','media_terminal_evidence','media_receipts','media_voices'] LOOP
 EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION deny_immutable_change()',t||'_immutable',t);
 END LOOP;
 FOREACH t IN ARRAY ARRAY['media_models','media_model_states','media_blocked_bindings','media_invocations','media_task_tokens','media_jobs','media_provider_attempts','media_provider_associations','media_terminal_evidence','media_receipts','media_assets','media_artifacts','media_voices'] LOOP
 EXECUTE format('CREATE TRIGGER %I BEFORE TRUNCATE ON %I FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change()',t||'_no_truncate',t);
 END LOOP;
 FOREACH t IN ARRAY ARRAY['media_invocations','media_jobs','media_assets','media_artifacts','media_voices','media_provider_attempts','media_provider_associations','media_terminal_evidence','media_receipts','credit_reservations'] LOOP
 EXECUTE format('CREATE CONSTRAINT TRIGGER %I AFTER INSERT OR UPDATE ON %I DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_media_reconciliation()',t||'_media_reconcile',t);
 END LOOP;
END $$;
