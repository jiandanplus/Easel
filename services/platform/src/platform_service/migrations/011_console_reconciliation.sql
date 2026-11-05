-- Console review metadata never changes an execution's financial state.
ALTER TABLE console_operations DROP CONSTRAINT console_operations_action_check;
ALTER TABLE console_operations ADD CONSTRAINT console_operations_action_check
 CHECK(action IN ('plan.publish','offer.publish','offer.withdraw','model.publish','model.state','execution.review'));
ALTER TABLE console_operations DROP CONSTRAINT console_operations_target_type_check;
ALTER TABLE console_operations ADD CONSTRAINT console_operations_target_type_check
 CHECK(target_type IN ('plan','offer','model','execution'));

CREATE TABLE console_reconciliation_cases (
 job_id text PRIMARY KEY REFERENCES execution_jobs(id),
 state text NOT NULL CHECK(state IN ('investigating','awaiting_evidence','disputed')),
 revision bigint NOT NULL CHECK(revision>0),
 actor_id text NOT NULL REFERENCES console_admins(id),
 reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 500 AND reason=btrim(reason) AND reason !~ '[[:cntrl:]]'),
 snapshot_hash text NOT NULL CHECK(snapshot_hash ~ '^[a-f0-9]{64}$'),
 last_operation_id text NOT NULL UNIQUE REFERENCES console_operations(id) DEFERRABLE INITIALLY DEFERRED,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX console_review_updated ON console_reconciliation_cases(updated_at DESC,job_id);
CREATE INDEX console_review_state ON console_reconciliation_cases(state,updated_at DESC,job_id);
CREATE INDEX console_review_history ON console_operations(target_id,created_at DESC,id DESC)
 WHERE action='execution.review' AND target_type='execution';
CREATE INDEX console_pending_queue ON execution_jobs(created_at DESC,id DESC)
 WHERE status='pending_reconciliation';

CREATE FUNCTION guard_console_review_revision() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='INSERT' THEN
  IF NEW.revision<>1 THEN RAISE EXCEPTION 'review must start at revision one' USING ERRCODE='23514'; END IF;
 ELSIF NEW.job_id IS DISTINCT FROM OLD.job_id OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.revision<>OLD.revision+1 OR NEW.last_operation_id=OLD.last_operation_id
       OR NEW.updated_at<OLD.updated_at THEN
  RAISE EXCEPTION 'invalid review revision' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER console_review_revision BEFORE INSERT OR UPDATE ON console_reconciliation_cases
 FOR EACH ROW EXECUTE FUNCTION guard_console_review_revision();
CREATE TRIGGER console_review_no_delete BEFORE DELETE ON console_reconciliation_cases
 FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER console_review_no_truncate BEFORE TRUNCATE ON console_reconciliation_cases
 FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();

-- Case metadata, its immutable result and the matching audit must commit together.
-- These checks read review metadata only. The FK may take normal key-share locks;
-- it does not authorize or modify a financial state.
CREATE FUNCTION check_console_review_case() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS (
  SELECT 1 FROM console_operations o JOIN console_audit_events a ON a.operation_id=o.id
  WHERE o.id=NEW.last_operation_id AND o.action='execution.review' AND o.target_type='execution'
   AND o.target_id=NEW.job_id AND o.actor_id=NEW.actor_id
   AND a.actor_id=NEW.actor_id AND a.reason=NEW.reason
   AND o.result->>'operation_id'=o.id AND o.result->>'job_id'=NEW.job_id
   AND o.result->'review'->>'state'=NEW.state
   AND o.result->'review'->>'revision'=NEW.revision::text
   AND o.result->'review'->>'actor_id'=NEW.actor_id
   AND o.result->'review'->>'reason'=NEW.reason
   AND o.result->'review'->>'snapshot_hash'=NEW.snapshot_hash
   AND (o.result->'review'->>'updated_at')::timestamptz=NEW.updated_at
 ) THEN RAISE EXCEPTION 'review audit divergence' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER console_review_case_audited AFTER INSERT OR UPDATE ON console_reconciliation_cases
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_console_review_case();

CREATE FUNCTION check_console_review_operation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.action='execution.review' AND NOT EXISTS (
  SELECT 1 FROM console_reconciliation_cases c
  WHERE c.job_id=NEW.target_id AND c.last_operation_id=NEW.id
 ) THEN RAISE EXCEPTION 'review operation has no case' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER console_review_operation_bound AFTER INSERT ON console_operations
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_console_review_operation();
