CREATE TABLE execution_models (
 id text PRIMARY KEY,
 definition jsonb NOT NULL,
 operator_id text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE execution_model_states (
 model_id text PRIMARY KEY REFERENCES execution_models(id),
 enabled boolean NOT NULL DEFAULT false,
 operator_id text NOT NULL,
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE execution_blocked_bindings (
 binding_hash text PRIMARY KEY,
 reason text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE execution_invocations (
 id text PRIMARY KEY,
 scope jsonb NOT NULL,
 session_id text NOT NULL REFERENCES identity_sessions(id),
 user_id text NOT NULL REFERENCES users(id),
 product_id text NOT NULL REFERENCES products(id),
 billing_account_id text NOT NULL REFERENCES billing_accounts(id),
 invocation_key text NOT NULL,
 model text NOT NULL REFERENCES execution_models(id),
 funding_mode text NOT NULL CHECK(funding_mode IN ('test','live')),
 operation_count integer NOT NULL DEFAULT 0 CHECK(operation_count BETWEEN 0 AND 32),
 held bigint NOT NULL DEFAULT 0 CHECK(held>=0),
 spent bigint NOT NULL DEFAULT 0 CHECK(spent>=0),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 expires_at timestamptz NOT NULL,
 CHECK(held::numeric+spent::numeric<=100000000),
 UNIQUE(session_id,invocation_key)
);
CREATE TABLE execution_task_tokens (
 token_hash text PRIMARY KEY,
 invocation_id text NOT NULL REFERENCES execution_invocations(id),
 expires_at timestamptz NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE execution_jobs (
 id text PRIMARY KEY,
 invocation_id text NOT NULL REFERENCES execution_invocations(id),
 operation_key text NOT NULL,
 body_hash text NOT NULL,
 output_limit integer NOT NULL CHECK(output_limit>0),
 scope jsonb NOT NULL,
 billing_account_id text NOT NULL REFERENCES billing_accounts(id),
 user_id text NOT NULL REFERENCES users(id),
 product_id text NOT NULL REFERENCES products(id),
 model text NOT NULL REFERENCES execution_models(id),
 funding_mode text NOT NULL CHECK(funding_mode IN ('test','live')),
 model_definition jsonb NOT NULL,
 binding_hash text NOT NULL,
 attempt_id text NOT NULL UNIQUE,
 reservation_id text NOT NULL UNIQUE REFERENCES credit_reservations(id),
 status text NOT NULL CHECK(status IN ('reserved','dispatched','pending_reconciliation','settled','released')),
 amount bigint NOT NULL CHECK(amount>0),
 actual bigint CHECK(actual>=0 AND actual<=amount),
 reason text,
 lease_until timestamptz NOT NULL,
 dispatched_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(invocation_id,operation_key)
);
CREATE TABLE execution_receipts (
 job_id text PRIMARY KEY REFERENCES execution_jobs(id),
 attempt_id text NOT NULL,
 input_tokens bigint NOT NULL CHECK(input_tokens>=0),
 output_tokens bigint NOT NULL CHECK(output_tokens>=0),
 terminal_status text NOT NULL CHECK(terminal_status IN ('completed','incomplete')),
 provider_response_id text NOT NULL,
 evidence_hash text NOT NULL,
 actual bigint NOT NULL CHECK(actual>=0),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE execution_results (
 job_id text PRIMARY KEY REFERENCES execution_receipts(job_id),
 payload bytea CHECK(octet_length(payload)<=8388608),
 content_type text NOT NULL CHECK(content_type IN ('application/json','text/event-stream')),
 expires_at timestamptz NOT NULL DEFAULT (clock_timestamp()+interval '24 hours')
);
CREATE INDEX execution_jobs_lease_idx ON execution_jobs(lease_until) WHERE status IN ('reserved','dispatched');
CREATE TRIGGER execution_models_immutable BEFORE UPDATE OR DELETE ON execution_models FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER execution_receipts_immutable BEFORE UPDATE OR DELETE ON execution_receipts FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER execution_tokens_immutable BEFORE UPDATE OR DELETE ON execution_task_tokens FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER execution_blocks_immutable BEFORE UPDATE OR DELETE ON execution_blocked_bindings FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE FUNCTION guard_execution_job() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'immutable execution history' USING ERRCODE='23514'; END IF;
 IF (to_jsonb(NEW)-ARRAY['status','actual','reason','lease_until','dispatched_at','updated_at']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['status','actual','reason','lease_until','dispatched_at','updated_at']) OR (OLD.dispatched_at IS NOT NULL AND NEW.dispatched_at IS DISTINCT FROM OLD.dispatched_at) OR (OLD.status IN ('settled','released') AND NEW IS DISTINCT FROM OLD) THEN
 RAISE EXCEPTION 'immutable execution binding' USING ERRCODE='23514'; END IF;
 IF NEW.status<>OLD.status AND NOT ((OLD.status='reserved' AND NEW.status IN ('dispatched','released')) OR (OLD.status='dispatched' AND NEW.status IN ('pending_reconciliation','settled')) OR (OLD.status='pending_reconciliation' AND NEW.status='settled')) THEN RAISE EXCEPTION 'invalid execution transition' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER execution_job_guard BEFORE UPDATE OR DELETE ON execution_jobs FOR EACH ROW EXECUTE FUNCTION guard_execution_job();
CREATE FUNCTION guard_execution_result() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR NEW.job_id<>OLD.job_id OR NEW.content_type<>OLD.content_type OR NEW.expires_at<>OLD.expires_at OR NEW.payload IS NOT NULL OR OLD.expires_at>clock_timestamp() THEN RAISE EXCEPTION 'immutable execution result' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER execution_result_guard BEFORE UPDATE OR DELETE ON execution_results FOR EACH ROW EXECUTE FUNCTION guard_execution_result();

CREATE FUNCTION guard_execution_invocation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'immutable invocation history' USING ERRCODE='23514'; END IF;
 IF (to_jsonb(NEW)-ARRAY['operation_count','held','spent']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['operation_count','held','spent']) THEN RAISE EXCEPTION 'immutable invocation binding' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER execution_invocation_guard BEFORE UPDATE OR DELETE ON execution_invocations FOR EACH ROW EXECUTE FUNCTION guard_execution_invocation();
CREATE FUNCTION check_execution_reconciliation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE inv text; expected_count bigint; expected_held numeric; expected_spent numeric; current_inv execution_invocations%ROWTYPE;
BEGIN
 IF TG_TABLE_NAME='execution_invocations' THEN inv=NEW.id;
 ELSIF TG_TABLE_NAME='execution_receipts' THEN SELECT invocation_id INTO inv FROM execution_jobs WHERE id=NEW.job_id;
 ELSIF TG_TABLE_NAME='credit_reservations' THEN SELECT invocation_id INTO inv FROM execution_jobs WHERE reservation_id=NEW.id;
 ELSE inv=NEW.invocation_id; END IF;
 IF inv IS NULL THEN RETURN NULL; END IF;
 SELECT * INTO current_inv FROM execution_invocations WHERE id=inv;
 SELECT count(*),coalesce(sum(amount) FILTER(WHERE status IN ('reserved','dispatched','pending_reconciliation')),0),coalesce(sum(actual) FILTER(WHERE status='settled'),0)
 INTO expected_count,expected_held,expected_spent FROM execution_jobs WHERE invocation_id=inv;
 IF current_inv.operation_count<>expected_count OR current_inv.held<>expected_held OR current_inv.spent<>expected_spent THEN RAISE EXCEPTION 'execution invocation reconciliation failed' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM execution_jobs j JOIN credit_reservations r ON r.id=j.reservation_id WHERE j.invocation_id=inv AND (j.status<>r.status OR j.amount<>r.amount OR j.actual IS DISTINCT FROM r.actual OR j.billing_account_id<>r.billing_account_id OR j.user_id<>r.user_id OR j.product_id<>r.product_id OR j.funding_mode<>r.funding_mode OR j.scope<>current_inv.scope OR j.model<>current_inv.model)) THEN RAISE EXCEPTION 'execution reservation reconciliation failed' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM execution_jobs j LEFT JOIN execution_receipts r ON r.job_id=j.id WHERE j.invocation_id=inv AND ( (j.status='settled' AND (r.job_id IS NULL OR r.actual<>j.actual OR r.attempt_id<>j.attempt_id)) OR (j.status<>'settled' AND r.job_id IS NOT NULL))) THEN RAISE EXCEPTION 'execution receipt reconciliation failed' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER execution_invocation_reconcile AFTER INSERT OR UPDATE ON execution_invocations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_execution_reconciliation();
CREATE CONSTRAINT TRIGGER execution_job_reconcile AFTER INSERT OR UPDATE ON execution_jobs DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_execution_reconciliation();
CREATE TRIGGER execution_models_no_truncate BEFORE TRUNCATE ON execution_models FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER execution_model_states_no_truncate BEFORE TRUNCATE ON execution_model_states FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER execution_blocked_bindings_no_truncate BEFORE TRUNCATE ON execution_blocked_bindings FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER execution_invocations_no_truncate BEFORE TRUNCATE ON execution_invocations FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER execution_task_tokens_no_truncate BEFORE TRUNCATE ON execution_task_tokens FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER execution_jobs_no_truncate BEFORE TRUNCATE ON execution_jobs FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER execution_receipts_no_truncate BEFORE TRUNCATE ON execution_receipts FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER execution_results_no_truncate BEFORE TRUNCATE ON execution_results FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();

CREATE CONSTRAINT TRIGGER execution_receipt_reconcile AFTER INSERT ON execution_receipts DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_execution_reconciliation();
CREATE CONSTRAINT TRIGGER execution_reservation_reconcile AFTER INSERT OR UPDATE ON credit_reservations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_execution_reconciliation();
