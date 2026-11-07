-- Provider association and validated terminal usage are immutable server facts.
-- No prompts, credentials, headers or response content are retained here.
CREATE TABLE execution_provider_attempts (
 job_id text PRIMARY KEY REFERENCES execution_jobs(id),
 attempt_id text NOT NULL UNIQUE,
 binding_hash text NOT NULL CHECK(length(binding_hash) BETWEEN 1 AND 256),
 provider_scope_hash text NOT NULL CHECK(provider_scope_hash ~ '^[a-f0-9]{64}$'),
 provider_request_hash text NOT NULL CHECK(provider_request_hash ~ '^[a-f0-9]{64}$'),
 upstream_model text NOT NULL CHECK(length(upstream_model) BETWEEN 1 AND 128),
 request_id text NOT NULL UNIQUE CHECK(length(request_id) BETWEEN 1 AND 256 AND request_id ~ '^[A-Za-z0-9._:-]+$'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE execution_provider_associations (
 job_id text NOT NULL REFERENCES execution_provider_attempts(job_id),
 kind text NOT NULL CHECK(kind IN ('http','response')),
 attempt_id text NOT NULL,
 binding_hash text NOT NULL,
 provider_scope_hash text NOT NULL,
 http_request_id text CHECK(length(http_request_id) BETWEEN 1 AND 256 AND http_request_id ~ '^[A-Za-z0-9._:-]+$'),
 provider_response_id text CHECK(length(provider_response_id) BETWEEN 1 AND 256 AND provider_response_id ~ '^[A-Za-z0-9._:-]+$'),
 http_status integer,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(job_id,kind),
 CHECK((kind='http' AND http_status IS NOT NULL AND http_status BETWEEN 100 AND 599 AND provider_response_id IS NULL)
    OR (kind='response' AND provider_response_id IS NOT NULL AND http_status IS NULL AND http_request_id IS NULL))
);
CREATE UNIQUE INDEX execution_provider_response_unique
 ON execution_provider_associations(provider_scope_hash,provider_response_id) WHERE kind='response';
CREATE TABLE execution_terminal_evidence (
 job_id text PRIMARY KEY REFERENCES execution_provider_attempts(job_id),
 attempt_id text NOT NULL,
 binding_hash text NOT NULL,
 provider_scope_hash text NOT NULL,
 provider_response_id text NOT NULL CHECK(length(provider_response_id) BETWEEN 1 AND 256 AND provider_response_id ~ '^[A-Za-z0-9._:-]+$'),
 input_tokens bigint NOT NULL CHECK(input_tokens>=0),
 output_tokens bigint NOT NULL CHECK(output_tokens>=0),
 terminal_status text NOT NULL CHECK(terminal_status IN ('completed','incomplete')),
 evidence_hash text NOT NULL CHECK(evidence_hash ~ '^[a-f0-9]{64}$'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(provider_scope_hash,provider_response_id)
);
CREATE FUNCTION guard_execution_evidence_binding() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE job execution_jobs%ROWTYPE; attempt execution_provider_attempts%ROWTYPE;
BEGIN
 SELECT * INTO job FROM execution_jobs WHERE id=NEW.job_id;
 IF job.id IS NULL OR job.attempt_id<>NEW.attempt_id OR job.binding_hash<>NEW.binding_hash
    OR job.status IN ('released','waived','settled') THEN
  RAISE EXCEPTION 'execution evidence binding mismatch' USING ERRCODE='23514';
 END IF;
 IF TG_TABLE_NAME='execution_provider_attempts' THEN
  IF job.status<>'reserved' THEN RAISE EXCEPTION 'attempt already dispatched' USING ERRCODE='23514'; END IF;
 ELSE
  SELECT * INTO attempt FROM execution_provider_attempts WHERE job_id=NEW.job_id;
  IF attempt.job_id IS NULL OR attempt.attempt_id<>NEW.attempt_id OR attempt.binding_hash<>NEW.binding_hash OR attempt.provider_scope_hash<>NEW.provider_scope_hash
     OR job.status NOT IN ('dispatched','pending_reconciliation') THEN
   RAISE EXCEPTION 'execution attempt evidence required' USING ERRCODE='23514';
  END IF;
  IF TG_TABLE_NAME='execution_terminal_evidence' THEN
   IF NOT EXISTS(SELECT 1 FROM execution_provider_associations a WHERE a.job_id=NEW.job_id AND a.kind='http' AND a.http_status=200)
      OR NOT EXISTS(SELECT 1 FROM execution_provider_associations a WHERE a.job_id=NEW.job_id AND a.kind='response' AND a.provider_response_id=NEW.provider_response_id) THEN
    RAISE EXCEPTION 'execution terminal association required' USING ERRCODE='23514';
   END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER execution_provider_attempt_binding BEFORE INSERT ON execution_provider_attempts FOR EACH ROW EXECUTE FUNCTION guard_execution_evidence_binding();
CREATE TRIGGER execution_provider_association_binding BEFORE INSERT ON execution_provider_associations FOR EACH ROW EXECUTE FUNCTION guard_execution_evidence_binding();
CREATE TRIGGER execution_terminal_evidence_binding BEFORE INSERT ON execution_terminal_evidence FOR EACH ROW EXECUTE FUNCTION guard_execution_evidence_binding();
CREATE TRIGGER execution_provider_attempts_immutable BEFORE UPDATE OR DELETE ON execution_provider_attempts FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER execution_provider_associations_immutable BEFORE UPDATE OR DELETE ON execution_provider_associations FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER execution_terminal_evidence_immutable BEFORE UPDATE OR DELETE ON execution_terminal_evidence FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER execution_provider_attempts_no_truncate BEFORE TRUNCATE ON execution_provider_attempts FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER execution_provider_associations_no_truncate BEFORE TRUNCATE ON execution_provider_associations FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER execution_terminal_evidence_no_truncate BEFORE TRUNCATE ON execution_terminal_evidence FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();
CREATE FUNCTION check_execution_evidence_resolution() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE key text;
BEGIN
 IF TG_TABLE_NAME='execution_jobs' THEN key=NEW.id;
 ELSE key=NEW.job_id; END IF;
 IF EXISTS(SELECT 1 FROM execution_jobs j JOIN execution_terminal_evidence e ON e.job_id=j.id
           WHERE j.id=key AND j.status='waived') THEN
  RAISE EXCEPTION 'known terminal evidence cannot be waived' USING ERRCODE='23514';
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER execution_job_terminal_evidence_guard AFTER UPDATE ON execution_jobs DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_execution_evidence_resolution();
CREATE CONSTRAINT TRIGGER execution_terminal_resolution_guard AFTER INSERT ON execution_terminal_evidence DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_execution_evidence_resolution();
