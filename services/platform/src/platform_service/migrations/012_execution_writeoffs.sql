-- Unknown supplier costs are not zero usage. A write-off is a separate,
-- audited customer no-charge resolution and never a synthetic receipt.
ALTER TABLE credit_reservations DROP CONSTRAINT credit_reservations_status_check;
ALTER TABLE credit_reservations ADD CONSTRAINT credit_reservations_status_check
 CHECK(status IN ('reserved','dispatched','pending_reconciliation','settled','released','waived'));
ALTER TABLE execution_jobs DROP CONSTRAINT execution_jobs_status_check;
ALTER TABLE execution_jobs ADD CONSTRAINT execution_jobs_status_check
 CHECK(status IN ('reserved','dispatched','pending_reconciliation','settled','released','waived'));
ALTER TABLE execution_jobs ADD CONSTRAINT waived_execution_actual
 CHECK(status<>'waived' OR actual IS NULL);
ALTER TABLE credit_reservations DROP CONSTRAINT terminal_reservation_fields;
ALTER TABLE credit_reservations ADD CONSTRAINT terminal_reservation_fields CHECK (
 (status IN ('reserved','dispatched','pending_reconciliation') AND actual IS NULL AND completion_key IS NULL)
 OR (status='settled' AND actual IS NOT NULL AND completion_key IS NOT NULL AND length(btrim(completion_key)) BETWEEN 1 AND 256)
 OR (status='released' AND actual IS NOT DISTINCT FROM 0 AND completion_key IS NOT NULL AND length(btrim(completion_key)) BETWEEN 1 AND 256)
 OR (status='waived' AND actual IS NULL AND completion_key IS NOT NULL AND length(btrim(completion_key)) BETWEEN 1 AND 256)
);

CREATE TABLE execution_resolution_operations (
 id text PRIMARY KEY,
 actor_id text NOT NULL,
 operation_key text NOT NULL CHECK(operation_key ~ '^[!-~]{1,128}$'),
 plan_hash text NOT NULL CHECK(plan_hash ~ '^[a-f0-9]{64}$'),
 plan jsonb NOT NULL CHECK(jsonb_typeof(plan)='object'),
 result jsonb NOT NULL CHECK(jsonb_typeof(result)='object'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(actor_id,operation_key)
);
CREATE TABLE execution_resolutions (
 job_id text PRIMARY KEY REFERENCES execution_jobs(id),
 operation_id text NOT NULL REFERENCES execution_resolution_operations(id) DEFERRABLE INITIALLY DEFERRED,
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
CREATE INDEX execution_resolutions_operation ON execution_resolutions(operation_id,job_id);
CREATE TRIGGER execution_resolutions_immutable BEFORE UPDATE OR DELETE ON execution_resolutions FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER execution_resolutions_no_truncate BEFORE TRUNCATE ON execution_resolutions FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER execution_resolution_operations_immutable BEFORE UPDATE OR DELETE ON execution_resolution_operations FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER execution_resolution_operations_no_truncate BEFORE TRUNCATE ON execution_resolution_operations FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();

CREATE FUNCTION deny_initial_waived_state() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.status='waived' THEN RAISE EXCEPTION 'write-off requires an existing pending record' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER execution_no_initial_waiver BEFORE INSERT ON execution_jobs
 FOR EACH ROW EXECUTE FUNCTION deny_initial_waived_state();
CREATE TRIGGER reservation_no_initial_waiver BEFORE INSERT ON credit_reservations
 FOR EACH ROW EXECUTE FUNCTION deny_initial_waived_state();

CREATE OR REPLACE FUNCTION guard_reservation_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'immutable reservation history' USING ERRCODE='23514'; END IF;
 IF (NEW.id,NEW.billing_account_id,NEW.tenant_id,NEW.user_id,NEW.product_id,NEW.operation,
     NEW.idempotency_key,NEW.body_hash,NEW.rate_version,NEW.amount,NEW.created_at,NEW.created_xid)
    IS DISTINCT FROM
    (OLD.id,OLD.billing_account_id,OLD.tenant_id,OLD.user_id,OLD.product_id,OLD.operation,
     OLD.idempotency_key,OLD.body_hash,OLD.rate_version,OLD.amount,OLD.created_at,OLD.created_xid)
    OR OLD.status IN ('settled','released','waived') THEN
  RAISE EXCEPTION 'immutable reservation binding or completion' USING ERRCODE='23514';
 END IF;
 IF NEW.status<>OLD.status AND NOT (
  (OLD.status='reserved' AND NEW.status IN ('dispatched','released')) OR
  (OLD.status='dispatched' AND NEW.status IN ('pending_reconciliation','settled')) OR
  (OLD.status='pending_reconciliation' AND NEW.status IN ('settled','waived'))
 ) THEN RAISE EXCEPTION 'invalid reservation transition' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION guard_execution_job() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'immutable execution history' USING ERRCODE='23514'; END IF;
 IF (to_jsonb(NEW)-ARRAY['status','actual','reason','lease_until','dispatched_at','updated_at']) IS DISTINCT FROM
    (to_jsonb(OLD)-ARRAY['status','actual','reason','lease_until','dispatched_at','updated_at'])
    OR (OLD.dispatched_at IS NOT NULL AND NEW.dispatched_at IS DISTINCT FROM OLD.dispatched_at)
    OR (OLD.status IN ('settled','released','waived') AND NEW IS DISTINCT FROM OLD) THEN
  RAISE EXCEPTION 'immutable execution binding' USING ERRCODE='23514';
 END IF;
 IF NEW.status<>OLD.status AND NOT (
  (OLD.status='reserved' AND NEW.status IN ('dispatched','released')) OR
  (OLD.status='dispatched' AND NEW.status IN ('pending_reconciliation','settled')) OR
  (OLD.status='pending_reconciliation' AND NEW.status IN ('settled','waived'))
 ) THEN RAISE EXCEPTION 'invalid execution transition' USING ERRCODE='23514'; END IF;
 IF NEW.status='waived' AND OLD.status<>'waived' AND
    (OLD.actual IS NOT NULL OR OLD.lease_until>clock_timestamp() OR OLD.dispatched_at IS NULL) THEN
  RAISE EXCEPTION 'execution not eligible for write-off' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;

-- Every monetary comparison uses IS DISTINCT FROM: NULL supplier actual must
-- not bypass the explicit customer charge of zero for a waived reservation.
CREATE OR REPLACE FUNCTION check_reservation_reconciliation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE account text; reservation record; held_total numeric; journal_count bigint;
        amount_held numeric; amount_spent numeric; amount_returned numeric; amount_available numeric;
        customer_actual bigint;
BEGIN
 IF TG_OP='DELETE' THEN account:=OLD.billing_account_id; ELSE account:=NEW.billing_account_id; END IF;
 PERFORM 1 FROM billing_accounts WHERE id=account FOR UPDATE;
 FOR reservation IN SELECT * FROM credit_reservations WHERE billing_account_id=account LOOP
  SELECT coalesce(sum(amount::numeric),0) INTO held_total FROM credit_holds WHERE reservation_id=reservation.id;
  IF held_total IS DISTINCT FROM reservation.amount::numeric THEN RAISE EXCEPTION 'reservation allocation divergence' USING ERRCODE='23514'; END IF;
  SELECT count(*) INTO journal_count FROM credit_journals WHERE billing_account_id=account AND kind='reserve' AND source=reservation.id;
  SELECT coalesce(sum(e.delta::numeric) FILTER(WHERE e.bucket='held'),0),
         coalesce(sum(e.delta::numeric) FILTER(WHERE e.bucket='available'),0)
  INTO amount_held,amount_available FROM credit_entries e JOIN credit_journals j ON j.id=e.journal_id
  WHERE j.billing_account_id=account AND j.kind='reserve' AND j.source=reservation.id;
  IF journal_count<>1 OR amount_held IS DISTINCT FROM reservation.amount::numeric
     OR amount_available IS DISTINCT FROM -reservation.amount::numeric THEN
   RAISE EXCEPTION 'reservation journal divergence' USING ERRCODE='23514';
  END IF;
  SELECT count(*) INTO journal_count FROM credit_journals WHERE billing_account_id=account AND kind IN ('settle','release') AND source=reservation.id;
  IF reservation.status IN ('reserved','dispatched','pending_reconciliation') THEN
   IF journal_count<>0 THEN RAISE EXCEPTION 'active reservation has terminal journal' USING ERRCODE='23514'; END IF;
  ELSE
   IF journal_count<>1 OR NOT EXISTS (SELECT 1 FROM credit_journals WHERE billing_account_id=account AND source=reservation.id
      AND kind=CASE WHEN reservation.status='settled' THEN 'settle' ELSE 'release' END) THEN
    RAISE EXCEPTION 'missing terminal journal' USING ERRCODE='23514';
   END IF;
   customer_actual:=CASE WHEN reservation.status='waived' THEN 0 ELSE reservation.actual END;
   SELECT coalesce(sum(e.delta::numeric) FILTER(WHERE e.bucket='held'),0),
          coalesce(sum(e.delta::numeric) FILTER(WHERE e.bucket='spent'),0),
          coalesce(sum(e.delta::numeric) FILTER(WHERE e.bucket IN ('available','expired')),0)
   INTO amount_held,amount_spent,amount_returned FROM credit_entries e JOIN credit_journals j ON j.id=e.journal_id
   WHERE j.billing_account_id=account AND j.kind IN ('settle','release') AND j.source=reservation.id;
   IF customer_actual IS NULL OR amount_held IS DISTINCT FROM -reservation.amount::numeric
      OR amount_spent IS DISTINCT FROM customer_actual::numeric
      OR amount_returned IS DISTINCT FROM reservation.amount::numeric-customer_actual THEN
    RAISE EXCEPTION 'terminal journal amount divergence' USING ERRCODE='23514';
   END IF;
  END IF;
 END LOOP;
 IF EXISTS (SELECT 1 FROM credit_lots l WHERE l.billing_account_id=account AND l.held<>(
  SELECT coalesce(sum(h.amount::numeric),0) FROM credit_holds h JOIN credit_reservations r ON r.id=h.reservation_id
  WHERE h.lot_id=l.id AND r.status IN ('reserved','dispatched','pending_reconciliation')
 )) THEN RAISE EXCEPTION 'lot active holds divergence' USING ERRCODE='23514'; END IF;
 IF EXISTS (SELECT 1 FROM credit_journals j WHERE j.billing_account_id=account AND j.kind IN ('reserve','settle','release')
  AND NOT EXISTS(SELECT 1 FROM credit_reservations r WHERE r.id=j.source AND r.billing_account_id=account)) THEN
  RAISE EXCEPTION 'orphan reservation journal' USING ERRCODE='23514';
 END IF;
 RETURN NULL;
END $$;

-- This verifier is reached only by new write-off/audit rows, never by ordinary
-- runtime settlement or a scan of a wallet's older waived reservations. It is
-- SECURITY INVOKER; neither runtime nor Console receives financial audit INSERT.
CREATE FUNCTION verify_execution_resolution_operation(target text) RETURNS void LANGUAGE plpgsql AS $$
DECLARE operation execution_resolution_operations%ROWTYPE;
BEGIN
 SELECT * INTO operation FROM execution_resolution_operations WHERE id=target;
 IF NOT FOUND THEN RAISE EXCEPTION 'write-off operation missing' USING ERRCODE='23514'; END IF;
 IF operation.plan->>'version' IS DISTINCT FROM '1' OR operation.plan->>'kind' IS DISTINCT FROM 'platform_absorbed'
    OR operation.plan->>'actor' IS DISTINCT FROM operation.actor_id
    OR operation.plan->>'plan_sha256' IS DISTINCT FROM operation.plan_hash
    OR jsonb_typeof(operation.plan->'items') IS DISTINCT FROM 'array'
    OR jsonb_typeof(operation.result->'items') IS DISTINCT FROM 'array'
    OR operation.result->>'operation_id' IS DISTINCT FROM operation.id
    OR operation.result->>'plan_sha256' IS DISTINCT FROM operation.plan_hash
    OR operation.result->>'kind' IS DISTINCT FROM 'platform_absorbed' THEN
  RAISE EXCEPTION 'invalid write-off operation' USING ERRCODE='23514';
 END IF;
 IF jsonb_array_length(operation.plan->'items') NOT BETWEEN 1 AND 32
    OR jsonb_array_length(operation.result->'items')<>jsonb_array_length(operation.plan->'items')
    OR (SELECT count(DISTINCT item->>'id') FROM jsonb_array_elements(operation.result->'items') item)<>jsonb_array_length(operation.plan->'items')
    OR (SELECT count(*) FROM execution_resolutions WHERE operation_id=target)<>jsonb_array_length(operation.plan->'items') THEN
  RAISE EXCEPTION 'incomplete write-off batch' USING ERRCODE='23514';
 END IF;
 IF EXISTS (
  SELECT 1 FROM jsonb_array_elements(operation.plan->'items') item
  LEFT JOIN execution_resolutions r ON r.job_id=item->>'id' AND r.operation_id=target
  LEFT JOIN execution_jobs j ON j.id=r.job_id
  LEFT JOIN credit_reservations reservation ON reservation.id=r.reservation_id
  WHERE r.job_id IS NULL OR r.kind<>'platform_absorbed' OR r.customer_charge<>0 OR r.provider_usage_known
   OR r.actor_id IS DISTINCT FROM operation.actor_id OR r.plan_hash IS DISTINCT FROM operation.plan_hash
   OR r.reason IS DISTINCT FROM operation.plan->>'reason'
   OR r.approval_reference IS DISTINCT FROM operation.plan->>'approval_reference'
   OR r.attempt_id IS DISTINCT FROM item->>'attempt_id' OR r.binding_hash IS DISTINCT FROM item->>'binding_hash'
   OR r.scope_hash IS DISTINCT FROM item->>'scope_hash' OR r.reservation_id IS DISTINCT FROM item->>'reservation_id'
   OR r.original_amount::text IS DISTINCT FROM item->>'amount' OR r.snapshot_hash IS DISTINCT FROM item->>'snapshot_hash'
   OR j.status IS DISTINCT FROM 'waived' OR reservation.status IS DISTINCT FROM 'waived'
   OR j.actual IS NOT NULL OR reservation.actual IS NOT NULL
   OR j.reason IS DISTINCT FROM 'platform_absorbed' OR j.dispatched_at IS NULL
   OR j.lease_until>operation.created_at OR j.dispatched_at>operation.created_at
   OR j.attempt_id IS DISTINCT FROM r.attempt_id OR j.binding_hash IS DISTINCT FROM r.binding_hash
   OR encode(sha256(convert_to(j.scope::text,'UTF8')),'hex') IS DISTINCT FROM r.scope_hash
   OR j.reservation_id IS DISTINCT FROM r.reservation_id OR j.amount IS DISTINCT FROM r.original_amount
   OR reservation.amount IS DISTINCT FROM r.original_amount OR reservation.completion_key IS DISTINCT FROM target
   OR EXISTS(SELECT 1 FROM execution_receipts receipt WHERE receipt.job_id=r.job_id)
 ) THEN RAISE EXCEPTION 'write-off evidence divergence' USING ERRCODE='23514'; END IF;
 IF EXISTS (
  SELECT 1 FROM jsonb_array_elements(operation.result->'items') item
  LEFT JOIN execution_resolutions r ON r.job_id=item->>'id' AND r.operation_id=target
  WHERE r.job_id IS NULL OR item->>'status' IS DISTINCT FROM 'waived'
   OR item->'actual' IS DISTINCT FROM 'null'::jsonb
   OR item->>'customer_charge' IS DISTINCT FROM '0'
   OR item->'provider_usage_known' IS DISTINCT FROM 'false'::jsonb
   OR item->>'original_amount' IS DISTINCT FROM r.original_amount::text
 ) OR operation.result->>'released_microcredits' IS DISTINCT FROM
      (SELECT sum(original_amount)::text FROM execution_resolutions WHERE operation_id=target) THEN
  RAISE EXCEPTION 'write-off result divergence' USING ERRCODE='23514';
 END IF;
END $$;

CREATE FUNCTION check_execution_resolution_audit() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_TABLE_NAME='execution_resolution_operations' THEN
  PERFORM verify_execution_resolution_operation(NEW.id);
 ELSE
  PERFORM verify_execution_resolution_operation(NEW.operation_id);
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER execution_resolution_operation_audited AFTER INSERT ON execution_resolution_operations
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_execution_resolution_audit();
CREATE CONSTRAINT TRIGGER execution_resolution_audited AFTER INSERT ON execution_resolutions
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_execution_resolution_audit();

CREATE FUNCTION check_waived_execution_audit() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE operation text;
BEGIN
 IF TG_TABLE_NAME='execution_jobs' THEN
  SELECT operation_id INTO operation FROM execution_resolutions WHERE job_id=NEW.id;
 ELSE
  SELECT operation_id INTO operation FROM execution_resolutions WHERE reservation_id=NEW.id;
 END IF;
 IF operation IS NULL THEN RAISE EXCEPTION 'unaudited write-off forbidden' USING ERRCODE='23514'; END IF;
 PERFORM verify_execution_resolution_operation(operation);
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER execution_waiver_audited AFTER UPDATE ON execution_jobs
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN(NEW.status='waived') EXECUTE FUNCTION check_waived_execution_audit();
CREATE CONSTRAINT TRIGGER reservation_waiver_audited AFTER UPDATE ON credit_reservations
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN(NEW.status='waived') EXECUTE FUNCTION check_waived_execution_audit();
