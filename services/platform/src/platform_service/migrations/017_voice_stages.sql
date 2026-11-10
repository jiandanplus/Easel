CREATE TABLE media_voice_registration_proofs (
 voice_id text PRIMARY KEY REFERENCES media_voices(id),job_id text NOT NULL UNIQUE REFERENCES media_jobs(id),
 attempt_id text NOT NULL,binding_hash text NOT NULL,provider_scope_hash text NOT NULL,
 voice_boundary_hash text NOT NULL CHECK(voice_boundary_hash ~ '^[a-f0-9]{64}$'),
 protocol text NOT NULL CHECK(protocol IN ('newapi_minimax_voice_v1','newapi_dashscope_voice_v1')),
 target_model text NOT NULL CHECK(length(target_model) BETWEEN 1 AND 160),
 provider_reference text NOT NULL CHECK(provider_reference ~ '^task_[A-Za-z0-9]{32}$'),
 final_step_id text NOT NULL UNIQUE,final_observation_id text NOT NULL UNIQUE,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(job_id,attempt_id,binding_hash,provider_scope_hash)
 REFERENCES media_provider_attempts(job_id,attempt_id,binding_hash,provider_scope_hash),
 UNIQUE(provider_scope_hash,protocol,provider_reference)
);
CREATE TRIGGER media_voice_proof_immutable BEFORE UPDATE OR DELETE ON media_voice_registration_proofs
 FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER media_voice_proof_no_truncate BEFORE TRUNCATE ON media_voice_registration_proofs
 FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();

-- Every voice POST has one durable permit. An existing permit never grants a
-- second POST, including a crash before its first response was observed.
CREATE TABLE media_voice_steps (
 id text PRIMARY KEY,job_id text NOT NULL REFERENCES media_jobs(id),attempt_id text NOT NULL,
 binding_hash text NOT NULL,provider_scope_hash text NOT NULL,
 operation_key text NOT NULL CHECK(length(operation_key) BETWEEN 1 AND 160),
 ordinal integer NOT NULL CHECK(ordinal BETWEEN 0 AND 8),
 protocol text NOT NULL CHECK(protocol IN ('newapi_minimax_voice_v1','newapi_dashscope_voice_v1')),
 stage text NOT NULL CHECK(stage IN ('upload','register','create','verify')),
 wire_hash text NOT NULL CHECK(wire_hash ~ '^[a-f0-9]{64}$'),request_id text NOT NULL UNIQUE CHECK(request_id ~ '^[A-Za-z0-9_.:-]{1,160}$'),
 previous_step_id text REFERENCES media_voice_steps(id),
 refs jsonb NOT NULL CHECK(jsonb_typeof(refs)='object' AND octet_length(refs::text)<=16384),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),UNIQUE(job_id,operation_key),UNIQUE(job_id,ordinal),
 FOREIGN KEY(job_id,attempt_id,binding_hash,provider_scope_hash)
 REFERENCES media_provider_attempts(job_id,attempt_id,binding_hash,provider_scope_hash)
);
CREATE TABLE media_voice_step_observations (
 id text PRIMARY KEY,step_id text NOT NULL REFERENCES media_voice_steps(id),
 operation_key text NOT NULL CHECK(length(operation_key) BETWEEN 1 AND 160),
 observation_seq integer NOT NULL CHECK(observation_seq BETWEEN 0 AND 63),
 state text NOT NULL CHECK(state IN ('headers','completed','pending','rejected','unknown')),
 http_status integer CHECK(http_status BETWEEN 100 AND 599),http_request_id text,
 provider_task_id text CHECK(provider_task_id ~ '^task_[A-Za-z0-9]{32}$'),
 response_hash text CHECK(response_hash ~ '^[a-f0-9]{64}$'),
 facts jsonb NOT NULL CHECK(jsonb_typeof(facts)='object' AND octet_length(facts::text)<=16384),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),UNIQUE(step_id,operation_key),UNIQUE(step_id,observation_seq),
 CHECK(state='unknown' OR (state='headers' AND http_status IS NOT NULL AND response_hash IS NULL AND provider_task_id IS NULL) OR (state<>'headers' AND http_status IS NOT NULL AND response_hash IS NOT NULL AND
    ((state IN ('completed','pending') AND http_status=200 AND provider_task_id IS NOT NULL)
      OR (state='rejected' AND (http_status>=400 OR facts->>'voice_status' IS NOT DISTINCT FROM 'UNDEPLOYED')))))
);

CREATE FUNCTION check_media_voice_history() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE s media_voice_steps%ROWTYPE;j media_jobs%ROWTYPE;p media_voice_steps%ROWTYPE;
 prior media_voice_step_observations%ROWTYPE;first_step media_voice_steps%ROWTYPE;first_result media_voice_step_observations%ROWTYPE;
 expected_refs jsonb;asset_hash text;n bigint;maximum integer;
BEGIN
 IF TG_TABLE_NAME='media_voice_steps' THEN s=NEW; ELSE SELECT * INTO s FROM media_voice_steps WHERE id=NEW.step_id; END IF;
 SELECT * INTO j FROM media_jobs WHERE id=s.job_id;
 IF j.capability<>'voice.enroll' OR j.dispatched_at IS NULL OR j.status NOT IN ('dispatched','pending_reconciliation')
    OR j.model_definition->>'adapter'<>s.protocol OR j.attempt_id<>s.attempt_id OR j.binding_hash<>s.binding_hash THEN
 RAISE EXCEPTION 'voice stage job mismatch' USING ERRCODE='23514';END IF;
 SELECT count(*),max(ordinal) INTO n,maximum FROM media_voice_steps WHERE job_id=s.job_id;
 IF maximum<>n-1 THEN RAISE EXCEPTION 'voice stage ordinal gap' USING ERRCODE='23514';END IF;
 IF s.ordinal=0 THEN
   SELECT sha256 INTO asset_hash FROM media_assets WHERE id=j.request->'assets'->>0 AND invocation_id=j.invocation_id;
   expected_refs=jsonb_build_object('asset_id',j.request->'assets'->>0,'asset_sha256',asset_hash,'target_model',j.request#>>'{parameters,target_model}');
   IF s.previous_step_id IS NOT NULL OR asset_hash IS NULL OR j.request#>>'{parameters,target_model}' IS NULL OR s.refs<>expected_refs
      OR s.wire_hash<>(SELECT provider_request_hash FROM media_provider_attempts WHERE job_id=s.job_id)
      OR (s.protocol='newapi_minimax_voice_v1' AND s.stage<>'upload')
      OR (s.protocol<>'newapi_minimax_voice_v1' AND s.stage<>'create') THEN
   RAISE EXCEPTION 'invalid initial voice stage' USING ERRCODE='23514';END IF;
 ELSE
   SELECT * INTO p FROM media_voice_steps WHERE id=s.previous_step_id;
   SELECT * INTO prior FROM media_voice_step_observations WHERE step_id=p.id ORDER BY observation_seq DESC LIMIT 1;
   IF p.id IS NULL OR p.job_id<>s.job_id OR p.protocol<>s.protocol OR p.ordinal<>s.ordinal-1 OR prior.state IS DISTINCT FROM 'completed' THEN
   RAISE EXCEPTION 'voice predecessor is unknown' USING ERRCODE='23514';END IF;
   IF s.protocol='newapi_minimax_voice_v1' THEN
     expected_refs=jsonb_build_object('file_task_id',prior.provider_task_id,'target_model',p.refs->>'target_model');
     IF s.ordinal<>1 OR s.stage<>'register' OR p.stage<>'upload' OR s.refs<>expected_refs THEN
     RAISE EXCEPTION 'invalid voice registration dependency' USING ERRCODE='23514';END IF;
   ELSE
     SELECT * INTO first_step FROM media_voice_steps WHERE job_id=s.job_id AND ordinal=0;
     SELECT * INTO first_result FROM media_voice_step_observations WHERE step_id=first_step.id ORDER BY observation_seq DESC LIMIT 1;
     expected_refs=jsonb_build_object('voice_task_id',first_result.provider_task_id,'target_model',first_step.refs->>'target_model');
     IF s.stage<>'verify' OR s.refs<>expected_refs OR first_result.state<>'completed'
        OR (p.stage='verify' AND prior.facts->>'voice_status' IS DISTINCT FROM 'DEPLOYING') THEN
     RAISE EXCEPTION 'invalid voice verification dependency' USING ERRCODE='23514';END IF;
   END IF;
 END IF;
 IF TG_TABLE_NAME='media_voice_step_observations' THEN
   IF NEW.state IN ('completed','pending','rejected') AND NOT EXISTS(SELECT 1 FROM media_voice_step_observations headers WHERE headers.step_id=s.id AND headers.state='headers' AND headers.http_status=NEW.http_status AND headers.http_request_id IS NOT DISTINCT FROM NEW.http_request_id) THEN
   RAISE EXCEPTION 'voice stage headers unverified' USING ERRCODE='23514';END IF;
   SELECT count(*),max(observation_seq) INTO n,maximum FROM media_voice_step_observations WHERE step_id=s.id;
   IF maximum<>n-1 OR EXISTS(SELECT 1 FROM media_voice_step_observations history WHERE history.step_id=s.id
       AND history.observation_seq<NEW.observation_seq AND history.state IN ('completed','rejected')) THEN
   RAISE EXCEPTION 'voice observation is already terminal' USING ERRCODE='23514';END IF;
   IF EXISTS(SELECT 1 FROM media_voice_step_observations history WHERE history.step_id=s.id AND history.provider_task_id IS NOT NULL
       AND NEW.provider_task_id IS NOT NULL AND history.provider_task_id<>NEW.provider_task_id) THEN
   RAISE EXCEPTION 'voice task reference changed' USING ERRCODE='23514';END IF;
   IF NEW.state='completed' AND
      ((s.stage='upload' AND (NEW.facts->>'operation' IS DISTINCT FROM 'upload' OR NEW.facts->>'file_task_id' IS DISTINCT FROM NEW.provider_task_id))
       OR (s.stage='register' AND (NEW.facts->>'operation' IS DISTINCT FROM 'clone' OR NEW.facts->>'voice_task_id' IS DISTINCT FROM NEW.provider_task_id))
       OR (s.stage IN ('create','verify') AND (NEW.facts->>'target_model' IS DISTINCT FROM s.refs->>'target_model'
           OR NEW.facts->>'voice_task_id' IS DISTINCT FROM NEW.provider_task_id
           OR (s.stage='create' AND NEW.facts->>'voice_status' IS DISTINCT FROM 'UNVERIFIED')
           OR (s.stage='verify' AND (NEW.facts->>'voice_status' IS NULL OR NEW.facts->>'voice_status' NOT IN ('DEPLOYING','OK')))))) THEN
   RAISE EXCEPTION 'unverified voice observation' USING ERRCODE='23514';END IF;
 END IF;
 RETURN NULL;
END $$;
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['media_voice_steps','media_voice_step_observations'] LOOP
 EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION deny_immutable_change()',t||'_immutable',t);
 EXECUTE format('CREATE TRIGGER %I BEFORE TRUNCATE ON %I FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change()',t||'_no_truncate',t);
 EXECUTE format('CREATE CONSTRAINT TRIGGER %I AFTER INSERT ON %I DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_media_voice_history()',t||'_binding',t);
 END LOOP;
END $$;




ALTER TABLE media_voice_step_observations ADD CONSTRAINT media_voice_observation_step_identity UNIQUE(id,step_id);
ALTER TABLE media_voice_registration_proofs ADD CONSTRAINT media_voice_proof_observation
 FOREIGN KEY(final_observation_id,final_step_id) REFERENCES media_voice_step_observations(id,step_id);

CREATE FUNCTION check_media_voice_registration() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE proof media_voice_registration_proofs%ROWTYPE;j media_jobs%ROWTYPE;v media_voices%ROWTYPE;
 s media_voice_steps%ROWTYPE;o media_voice_step_observations%ROWTYPE;e media_terminal_evidence%ROWTYPE;
BEGIN
 IF TG_TABLE_NAME='media_voice_registration_proofs' THEN proof=NEW;
 ELSE
   SELECT * INTO j FROM media_jobs WHERE id=NEW.job_id;
   IF j.capability<>'voice.enroll' THEN RETURN NULL;END IF;
   SELECT * INTO proof FROM media_voice_registration_proofs WHERE job_id=NEW.job_id;
   IF NOT FOUND THEN
     IF j.model_definition->>'adapter' IN ('newapi_minimax_voice_v1','newapi_dashscope_voice_v1') THEN
       RAISE EXCEPTION 'owned voice proof required' USING ERRCODE='23514';
     END IF;
     RETURN NULL;
   END IF;
 END IF;
 SELECT * INTO j FROM media_jobs WHERE id=proof.job_id;
 SELECT * INTO v FROM media_voices WHERE id=proof.voice_id;
 SELECT * INTO s FROM media_voice_steps WHERE id=proof.final_step_id;
 SELECT * INTO o FROM media_voice_step_observations WHERE id=proof.final_observation_id;
 IF j.capability<>'voice.enroll' OR v.job_id<>j.id OR v.scope<>j.scope
    OR v.provider_id<>j.model_definition->>'provider_id' OR v.provider_version<>j.model_definition->>'provider_version'
    OR v.provider_voice_id<>proof.provider_reference OR s.job_id<>j.id OR s.attempt_id<>proof.attempt_id
    OR s.binding_hash<>proof.binding_hash OR s.provider_scope_hash<>proof.provider_scope_hash OR s.protocol<>proof.protocol
    OR s.refs->>'target_model'<>proof.target_model OR o.step_id<>s.id OR o.state<>'completed' OR o.provider_task_id<>proof.provider_reference
    OR s.ordinal<>(SELECT max(ordinal) FROM media_voice_steps WHERE job_id=j.id)
    OR NOT ((s.protocol='newapi_minimax_voice_v1' AND s.stage='register') OR
            (s.protocol='newapi_dashscope_voice_v1' AND s.stage='verify' AND o.facts->>'voice_status'='OK')) THEN
 RAISE EXCEPTION 'unverified owned voice registration' USING ERRCODE='23514';END IF;
 SELECT * INTO e FROM media_terminal_evidence WHERE job_id=j.id;
 IF FOUND AND (e.reference_kind<>'object_id' OR e.provider_response_id IS DISTINCT FROM proof.provider_reference
               OR e.evidence_hash<>o.response_hash OR e.terminal_status<>'completed') THEN
 RAISE EXCEPTION 'voice registration terminal mismatch' USING ERRCODE='23514';END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER media_voice_registration_binding AFTER INSERT ON media_voice_registration_proofs
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_media_voice_registration();
CREATE CONSTRAINT TRIGGER media_voice_registration_terminal AFTER INSERT ON media_terminal_evidence
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_media_voice_registration();

CREATE CONSTRAINT TRIGGER media_voice_owned_proof AFTER INSERT ON media_voices
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_media_voice_registration();
