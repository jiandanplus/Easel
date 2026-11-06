-- Unknown supplier costs are not zero usage. A media write-off is the same
-- audited customer no-charge resolution as 012 for text execution: an operator
-- decision recorded beside the job, never a synthetic receipt or replay.
ALTER TABLE media_jobs DROP CONSTRAINT media_jobs_status_check;
ALTER TABLE media_jobs ADD CONSTRAINT media_jobs_status_check
 CHECK(status IN ('reserved','dispatched','pending_reconciliation','settled','released','waived'));
ALTER TABLE media_jobs ADD CONSTRAINT waived_media_actual CHECK(status<>'waived' OR actual IS NULL);
CREATE TRIGGER media_no_initial_waiver BEFORE INSERT ON media_jobs
 FOR EACH ROW EXECUTE FUNCTION deny_initial_waived_state();

CREATE TABLE media_resolution_operations (
 id text PRIMARY KEY,
 actor_id text NOT NULL,
 operation_key text NOT NULL CHECK(operation_key ~ '^[!-~]{1,128}$'),
 plan_hash text NOT NULL CHECK(plan_hash ~ '^[a-f0-9]{64}$'),
 plan jsonb NOT NULL CHECK(jsonb_typeof(plan)='object'),
 result jsonb NOT NULL CHECK(jsonb_typeof(result)='object'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(actor_id,operation_key)
);
CREATE TABLE media_resolutions (
 job_id text PRIMARY KEY REFERENCES media_jobs(id),
 operation_id text NOT NULL REFERENCES media_resolution_operations(id) DEFERRABLE INITIALLY DEFERRED,
 kind text NOT NULL CHECK(kind='platform_absorbed'),
 attempt_id text NOT NULL UNIQUE,
 binding_hash text NOT NULL,
 scope_hash text NOT NULL CHECK(scope_hash ~ '^[a-f0-9]{64}$'),
 reservation_id text NOT NULL UNIQUE REFERENCES credit_reservations(id),
 original_amount bigint NOT NULL CHECK(original_amount>0),
 customer_charge bigint NOT NULL CHECK(customer_charge=0),
 provider_usage_known boolean NOT NULL CHECK(provider_usage_known=false),
 actor_id text NOT NULL,
 reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 500 AND reason=btrim(reason) AND reason !~ '[[:cntrl:]]'),
 approval_reference text NOT NULL CHECK(length(btrim(approval_reference)) BETWEEN 1 AND 500 AND approval_reference=btrim(approval_reference) AND approval_reference !~ '[[:cntrl:]]'),
 plan_hash text NOT NULL CHECK(plan_hash ~ '^[a-f0-9]{64}$'),
 snapshot_hash text NOT NULL CHECK(snapshot_hash ~ '^[a-f0-9]{64}$'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX media_resolutions_operation ON media_resolutions(operation_id,job_id);
CREATE TRIGGER media_resolutions_immutable BEFORE UPDATE OR DELETE ON media_resolutions FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER media_resolutions_no_truncate BEFORE TRUNCATE ON media_resolutions FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER media_resolution_operations_immutable BEFORE UPDATE OR DELETE ON media_resolution_operations FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER media_resolution_operations_no_truncate BEFORE TRUNCATE ON media_resolution_operations FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();

-- Reached only by new write-off rows (SECURITY INVOKER); runtime and Console
-- receive no privilege on either resolution table.
CREATE FUNCTION verify_media_resolution_operation(target text) RETURNS void LANGUAGE plpgsql AS $$
DECLARE operation media_resolution_operations%ROWTYPE;
BEGIN
 SELECT * INTO operation FROM media_resolution_operations WHERE id=target;
 IF NOT FOUND THEN RAISE EXCEPTION 'media write-off operation missing' USING ERRCODE='23514'; END IF;
 IF operation.plan->>'version' IS DISTINCT FROM '1' OR operation.plan->>'kind' IS DISTINCT FROM 'platform_absorbed'
    OR operation.plan->>'subject' IS DISTINCT FROM 'media'
    OR operation.plan->>'actor' IS DISTINCT FROM operation.actor_id
    OR operation.plan->>'plan_sha256' IS DISTINCT FROM operation.plan_hash
    OR jsonb_typeof(operation.plan->'items') IS DISTINCT FROM 'array'
    OR jsonb_typeof(operation.result->'items') IS DISTINCT FROM 'array'
    OR operation.result->>'operation_id' IS DISTINCT FROM operation.id
    OR operation.result->>'plan_sha256' IS DISTINCT FROM operation.plan_hash
    OR operation.result->>'kind' IS DISTINCT FROM 'platform_absorbed' THEN
  RAISE EXCEPTION 'invalid media write-off operation' USING ERRCODE='23514';
 END IF;
 IF jsonb_array_length(operation.plan->'items') NOT BETWEEN 1 AND 32
    OR jsonb_array_length(operation.result->'items')<>jsonb_array_length(operation.plan->'items')
    OR (SELECT count(DISTINCT item->>'id') FROM jsonb_array_elements(operation.result->'items') item)<>jsonb_array_length(operation.plan->'items')
    OR (SELECT count(*) FROM media_resolutions WHERE operation_id=target)<>jsonb_array_length(operation.plan->'items') THEN
  RAISE EXCEPTION 'incomplete media write-off batch' USING ERRCODE='23514';
 END IF;
 IF EXISTS (
  SELECT 1 FROM jsonb_array_elements(operation.plan->'items') item
  LEFT JOIN media_resolutions r ON r.job_id=item->>'id' AND r.operation_id=target
  LEFT JOIN media_jobs j ON j.id=r.job_id
  LEFT JOIN credit_reservations reservation ON reservation.id=r.reservation_id
  LEFT JOIN media_storage_budgets budget ON budget.job_id=r.job_id
  WHERE r.job_id IS NULL OR r.kind<>'platform_absorbed' OR r.customer_charge<>0 OR r.provider_usage_known
   OR r.actor_id IS DISTINCT FROM operation.actor_id OR r.plan_hash IS DISTINCT FROM operation.plan_hash
   OR r.reason IS DISTINCT FROM operation.plan->>'reason'
   OR r.approval_reference IS DISTINCT FROM operation.plan->>'approval_reference'
   OR r.attempt_id IS DISTINCT FROM item->>'attempt_id' OR r.binding_hash IS DISTINCT FROM item->>'binding_hash'
   OR r.scope_hash IS DISTINCT FROM item->>'scope_hash' OR r.reservation_id IS DISTINCT FROM item->>'reservation_id'
   OR r.original_amount::text IS DISTINCT FROM item->>'amount' OR r.snapshot_hash IS DISTINCT FROM item->>'snapshot_hash'
   OR j.status IS DISTINCT FROM 'waived' OR reservation.status IS DISTINCT FROM 'waived'
   OR j.actual IS NOT NULL OR reservation.actual IS NOT NULL
   OR j.reason IS DISTINCT FROM 'platform_absorbed' OR j.delivery_state IS DISTINCT FROM 'unavailable' OR j.dispatched_at IS NULL
   OR j.lease_until>operation.created_at OR j.dispatched_at>operation.created_at
   OR j.attempt_id IS DISTINCT FROM r.attempt_id OR j.binding_hash IS DISTINCT FROM r.binding_hash
   OR encode(sha256(convert_to(j.scope::text,'UTF8')),'hex') IS DISTINCT FROM r.scope_hash
   OR j.reservation_id IS DISTINCT FROM r.reservation_id OR j.amount IS DISTINCT FROM r.original_amount
   OR reservation.amount IS DISTINCT FROM r.original_amount OR reservation.completion_key IS DISTINCT FROM target
   OR budget.released_at IS NULL
   OR EXISTS(SELECT 1 FROM media_receipts receipt WHERE receipt.job_id=r.job_id)
   OR EXISTS(SELECT 1 FROM media_terminal_evidence evidence WHERE evidence.job_id=r.job_id)
   OR EXISTS(SELECT 1 FROM media_work work WHERE work.job_id=r.job_id AND work.phase<>'manual')
 ) THEN RAISE EXCEPTION 'media write-off evidence divergence' USING ERRCODE='23514'; END IF;
 IF EXISTS (
  SELECT 1 FROM jsonb_array_elements(operation.result->'items') item
  LEFT JOIN media_resolutions r ON r.job_id=item->>'id' AND r.operation_id=target
  WHERE r.job_id IS NULL OR item->>'status' IS DISTINCT FROM 'waived'
   OR item->'actual' IS DISTINCT FROM 'null'::jsonb
   OR item->>'customer_charge' IS DISTINCT FROM '0'
   OR item->'provider_usage_known' IS DISTINCT FROM 'false'::jsonb
   OR item->>'original_amount' IS DISTINCT FROM r.original_amount::text
 ) OR operation.result->>'released_microcredits' IS DISTINCT FROM
      (SELECT sum(original_amount)::text FROM media_resolutions WHERE operation_id=target) THEN
  RAISE EXCEPTION 'media write-off result divergence' USING ERRCODE='23514';
 END IF;
END $$;

CREATE FUNCTION check_media_resolution_audit() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_TABLE_NAME='media_resolution_operations' THEN
  PERFORM verify_media_resolution_operation(NEW.id);
 ELSE
  PERFORM verify_media_resolution_operation(NEW.operation_id);
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER media_resolution_operation_audited AFTER INSERT ON media_resolution_operations
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_media_resolution_audit();
CREATE CONSTRAINT TRIGGER media_resolution_audited AFTER INSERT ON media_resolutions
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_media_resolution_audit();

-- A waived reservation is audited by whichever resolution owns it. 012 only
-- knew text execution and would reject every media write-off.
CREATE OR REPLACE FUNCTION check_waived_execution_audit() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE operation text;
BEGIN
 IF TG_TABLE_NAME='execution_jobs' THEN
  SELECT operation_id INTO operation FROM execution_resolutions WHERE job_id=NEW.id;
 ELSIF TG_TABLE_NAME='media_jobs' THEN
  SELECT operation_id INTO operation FROM media_resolutions WHERE job_id=NEW.id;
  IF operation IS NULL THEN RAISE EXCEPTION 'unaudited write-off forbidden' USING ERRCODE='23514'; END IF;
  PERFORM verify_media_resolution_operation(operation);
  RETURN NULL;
 ELSE
  SELECT operation_id INTO operation FROM execution_resolutions WHERE reservation_id=NEW.id;
  IF operation IS NULL THEN
   SELECT operation_id INTO operation FROM media_resolutions WHERE reservation_id=NEW.id;
   IF operation IS NULL THEN RAISE EXCEPTION 'unaudited write-off forbidden' USING ERRCODE='23514'; END IF;
   PERFORM verify_media_resolution_operation(operation);
   RETURN NULL;
  END IF;
 END IF;
 IF operation IS NULL THEN RAISE EXCEPTION 'unaudited write-off forbidden' USING ERRCODE='23514'; END IF;
 PERFORM verify_execution_resolution_operation(operation);
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER media_waiver_audited AFTER UPDATE ON media_jobs
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN(NEW.status='waived') EXECUTE FUNCTION check_waived_execution_audit();

-- A waived job leaves the active ledger like a settled one: it may no longer move,
-- holds nothing, and keeps the dispatch evidence it already had.
CREATE OR REPLACE FUNCTION guard_media_job() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'immutable media job' USING ERRCODE='23514'; END IF;
 IF (to_jsonb(NEW)-ARRAY['status','actual','execution_state','delivery_state','reason','lease_token','lease_until','dispatched_at','cancel_requested_at','updated_at']) IS DISTINCT FROM
    (to_jsonb(OLD)-ARRAY['status','actual','execution_state','delivery_state','reason','lease_token','lease_until','dispatched_at','cancel_requested_at','updated_at'])
    OR (OLD.dispatched_at IS NOT NULL AND NEW.dispatched_at IS DISTINCT FROM OLD.dispatched_at)
    OR (OLD.status IN ('settled','released') AND (NEW.status,NEW.actual,NEW.execution_state,NEW.reason) IS DISTINCT FROM (OLD.status,OLD.actual,OLD.execution_state,OLD.reason))
    OR (OLD.status='waived' AND NEW IS DISTINCT FROM OLD) THEN
 RAISE EXCEPTION 'immutable media binding' USING ERRCODE='23514'; END IF;
 IF NEW.status<>OLD.status AND NOT ((OLD.status='reserved' AND NEW.status IN ('dispatched','released')) OR
    (OLD.status='dispatched' AND NEW.status IN ('pending_reconciliation','settled')) OR
    (OLD.status='pending_reconciliation' AND NEW.status IN ('settled','waived'))) THEN RAISE EXCEPTION 'invalid media transition' USING ERRCODE='23514'; END IF;
 IF NEW.status='waived' AND OLD.status<>'waived' AND
    (OLD.actual IS NOT NULL OR OLD.lease_until>clock_timestamp() OR OLD.dispatched_at IS NULL
     OR NEW.execution_state IS DISTINCT FROM OLD.execution_state) THEN
  RAISE EXCEPTION 'media job not eligible for write-off' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;

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
     OR (j.status IN ('dispatched','pending_reconciliation','settled','waived') AND (j.dispatched_at IS NULL OR NOT EXISTS(SELECT 1 FROM media_provider_attempts a WHERE a.job_id=j.id)))
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

-- Storage treats a waived job as terminal: its unused output budget is returned
-- and its staged blobs are no longer pinned against expiry GC.
CREATE OR REPLACE FUNCTION media_storage_pinned(blob media_blobs) RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT EXISTS(SELECT 1 FROM media_jobs j WHERE NOT (j.status='released' AND j.dispatched_at IS NULL AND j.actual=0) AND j.status<>'waived'
   AND NOT EXISTS(SELECT 1 FROM media_terminal_evidence e WHERE e.job_id=j.id AND e.attempt_id=j.attempt_id)
   AND ((blob.kind='artifact' AND j.id=blob.job_id) OR
    (blob.kind='asset' AND blob.attached_id IS NOT NULL AND j.invocation_id=blob.invocation_id AND j.request->'assets' ? blob.attached_id)))
$$;

CREATE OR REPLACE FUNCTION guard_media_storage_budget() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR (to_jsonb(NEW)-ARRAY['remaining_bytes','remaining_files','released_at']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['remaining_bytes','remaining_files','released_at']) OR OLD.released_at IS NOT NULL OR NEW.remaining_bytes>OLD.remaining_bytes OR NEW.remaining_files>OLD.remaining_files OR (NEW.released_at IS NOT NULL AND (NEW.remaining_bytes<>0 OR NEW.remaining_files<>0)) THEN
  RAISE EXCEPTION 'immutable media storage budget' USING ERRCODE='23514';END IF;
 IF NEW.released_at IS NOT NULL AND NOT EXISTS(SELECT 1 FROM media_jobs j WHERE j.id=NEW.job_id AND
    ((j.status='released' AND j.dispatched_at IS NULL AND j.actual=0) OR j.status='waived' OR EXISTS(SELECT 1 FROM media_terminal_evidence e WHERE e.job_id=j.id AND e.attempt_id=j.attempt_id))) THEN
  RAISE EXCEPTION 'active media storage budget' USING ERRCODE='23514';END IF;
 RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION check_media_storage_integrity() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE blob media_blobs%ROWTYPE;inv media_invocations%ROWTYPE;j media_jobs%ROWTYPE;
 budget media_storage_budgets%ROWTYPE;a media_assets%ROWTYPE;r media_artifacts%ROWTYPE;
 blob_key text;job_key text;allocated_bytes numeric;allocated_files bigint;terminal boolean;expected_files integer;
BEGIN
 IF TG_TABLE_NAME IN ('media_blobs','media_assets','media_artifacts') THEN blob_key=NEW.storage_key;
 ELSIF TG_TABLE_NAME='media_jobs' THEN job_key=NEW.id;
 ELSE job_key=NEW.job_id;END IF;
 IF blob_key IS NOT NULL THEN
  SELECT * INTO blob FROM media_blobs WHERE storage_key=blob_key;
  IF NOT FOUND THEN RAISE EXCEPTION 'media storage registry missing' USING ERRCODE='23514';END IF;
  SELECT * INTO inv FROM media_invocations WHERE id=blob.invocation_id;
  IF NOT FOUND OR blob.scope<>inv.scope OR (blob.user_id,blob.tenant_id,blob.billing_account_id) IS DISTINCT FROM (inv.user_id,inv.tenant_id,inv.billing_account_id) THEN
   RAISE EXCEPTION 'media storage scope mismatch' USING ERRCODE='23514';END IF;
  IF blob.kind='artifact' THEN
   job_key=blob.job_id;SELECT * INTO j FROM media_jobs WHERE id=job_key;
   IF NOT FOUND OR (blob.invocation_id,blob.attempt_id,blob.scope,blob.user_id,blob.tenant_id,blob.billing_account_id) IS DISTINCT FROM
     (j.invocation_id,j.attempt_id,j.scope,j.user_id,j.tenant_id,j.billing_account_id) THEN
    RAISE EXCEPTION 'media storage attempt mismatch' USING ERRCODE='23514';END IF;
  END IF;
  IF blob.state='staged' AND blob.attached_id IS NOT NULL THEN RAISE EXCEPTION 'media staged attachment mismatch' USING ERRCODE='23514';END IF;
  IF blob.attached_id IS NOT NULL THEN
   IF blob.kind='asset' THEN
    SELECT * INTO a FROM media_assets WHERE id=blob.attached_id;
    IF NOT FOUND OR (blob.storage_key,blob.sha256,blob.size,blob.mime,blob.properties,blob.scope,blob.invocation_id,blob.expires_at) IS DISTINCT FROM
      (a.storage_key,a.sha256,a.size,a.mime,a.properties,a.scope,a.invocation_id,a.expires_at) OR (blob.purged_at IS NULL)<>(a.purged_at IS NULL) THEN
     RAISE EXCEPTION 'media storage attachment mismatch' USING ERRCODE='23514';END IF;
   ELSE
    SELECT * INTO r FROM media_artifacts WHERE id=blob.attached_id;
    IF NOT FOUND OR (blob.storage_key,blob.sha256,blob.size,blob.mime,blob.properties,blob.scope,blob.invocation_id,blob.job_id,blob.attempt_id,blob.expires_at) IS DISTINCT FROM
      (r.storage_key,r.sha256,r.size,r.mime,r.properties,r.scope,r.invocation_id,r.job_id,r.attempt_id,r.expires_at) OR (blob.purged_at IS NULL)<>(r.purged_at IS NULL) THEN
     RAISE EXCEPTION 'media storage attachment mismatch' USING ERRCODE='23514';END IF;
   END IF;
  ELSIF (blob.kind='asset' AND EXISTS(SELECT 1 FROM media_assets WHERE storage_key=blob.storage_key)) OR
        (blob.kind='artifact' AND EXISTS(SELECT 1 FROM media_artifacts WHERE storage_key=blob.storage_key)) THEN
   RAISE EXCEPTION 'media storage attachment missing' USING ERRCODE='23514';
  END IF;
 END IF;
 IF job_key IS NOT NULL THEN
  SELECT * INTO j FROM media_jobs WHERE id=job_key;
  SELECT * INTO budget FROM media_storage_budgets WHERE job_id=job_key;
  IF NOT FOUND OR budget.scope<>j.scope OR (budget.user_id,budget.tenant_id,budget.billing_account_id) IS DISTINCT FROM
    (j.user_id,j.tenant_id,j.billing_account_id) OR budget.max_bytes<>(j.model_definition#>>'{limits,max_artifact_bytes}')::bigint THEN
   RAISE EXCEPTION 'media storage budget binding mismatch' USING ERRCODE='23514';END IF;
  expected_files=CASE WHEN j.capability LIKE 'image.%' THEN coalesce((j.request#>>'{parameters,n}')::integer,1) ELSE 32 END;
  IF budget.max_files<>expected_files THEN RAISE EXCEPTION 'media storage budget count mismatch' USING ERRCODE='23514';END IF;
  SELECT coalesce(sum(size),0),count(*) INTO allocated_bytes,allocated_files FROM media_blobs WHERE job_id=job_key AND kind='artifact';
  terminal=(j.status='released' AND j.dispatched_at IS NULL AND j.actual=0) OR j.status='waived' OR EXISTS(SELECT 1 FROM media_terminal_evidence e WHERE e.job_id=job_key AND e.attempt_id=j.attempt_id);
  IF allocated_bytes>budget.max_bytes OR allocated_files>budget.max_files OR
    (budget.released_at IS NULL AND (terminal OR budget.remaining_bytes+allocated_bytes<>budget.max_bytes OR budget.remaining_files+allocated_files<>budget.max_files)) OR
    (budget.released_at IS NOT NULL AND (NOT terminal OR budget.remaining_bytes<>0 OR budget.remaining_files<>0)) THEN
   RAISE EXCEPTION 'media storage budget reconciliation failed' USING ERRCODE='23514';END IF;
 END IF;
 RETURN NULL;
END $$;
