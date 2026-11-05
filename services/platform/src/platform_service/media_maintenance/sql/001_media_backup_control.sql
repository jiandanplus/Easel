-- Dedicated B2 control schema. Never part of public Database.migrate().
CREATE SCHEMA easel_backup_control;
CREATE TABLE easel_backup_control.schema_migrations(version text PRIMARY KEY,checksum text NOT NULL CHECK(checksum ~ '^[0-9a-f]{64}$'));
CREATE TABLE easel_backup_control.settings(id integer PRIMARY KEY CHECK(id=1),application_schema text NOT NULL,profile text NOT NULL);
CREATE TABLE easel_backup_control.control(
 id integer PRIMARY KEY CHECK(id=1),state text NOT NULL CHECK(state IN ('OPEN','DRAINING','CAPTURING','VERIFYING','RECOVERING','NEEDS_REVIEW')),
 epoch bigint NOT NULL CHECK(epoch>=0),window_id uuid,owner_hash text,binding jsonb,original_state jsonb);
INSERT INTO easel_backup_control.control(id,state,epoch) VALUES(1,'OPEN',0);
CREATE TABLE easel_backup_control.workers(
 worker_id uuid PRIMARY KEY,generation uuid NOT NULL UNIQUE,identity jsonb NOT NULL,
 token_hash text NOT NULL CHECK(token_hash ~ '^[0-9a-f]{64}$'),active boolean NOT NULL DEFAULT true);
CREATE TABLE easel_backup_control.worker_attestations(
 worker_id uuid PRIMARY KEY REFERENCES easel_backup_control.workers(worker_id),generation uuid NOT NULL,
 domain jsonb NOT NULL CHECK(jsonb_typeof(domain)='object'));
CREATE VIEW easel_backup_control.worker_domains AS SELECT w.worker_id,w.generation,w.identity,w.active,a.domain FROM easel_backup_control.workers w LEFT JOIN easel_backup_control.worker_attestations a ON a.worker_id=w.worker_id AND a.generation=w.generation;
CREATE TABLE easel_backup_control.permits(
 token_hash text PRIMARY KEY CHECK(token_hash ~ '^[0-9a-f]{64}$'),epoch bigint NOT NULL,
 kind text NOT NULL CHECK(kind IN ('catalog','policy','mint','upload','job','gc','maintenance')),
 subject_id text NOT NULL,attempt_id text,worker_id uuid NOT NULL,generation uuid NOT NULL,
 upstream_sha256 text,status text NOT NULL DEFAULT 'active' CHECK(status IN ('active','unknown')),
 FOREIGN KEY(worker_id) REFERENCES easel_backup_control.workers(worker_id),
 CHECK((kind IN ('job','gc'))=(attempt_id IS NOT NULL)));
CREATE UNIQUE INDEX permits_exact_job_gc ON easel_backup_control.permits(kind,subject_id) WHERE kind IN ('job','gc');
CREATE TABLE easel_backup_control.audit(
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,at timestamptz NOT NULL DEFAULT clock_timestamp(),
 action text NOT NULL,window_id uuid,epoch bigint NOT NULL,details jsonb NOT NULL);
CREATE VIEW easel_backup_control.runtime_status AS SELECT state,epoch,window_id FROM easel_backup_control.control WHERE id=1;
CREATE VIEW easel_backup_control.worker_status AS SELECT worker_id,generation,identity,active FROM easel_backup_control.workers;
CREATE FUNCTION easel_backup_control.deny_audit_change() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,easel_backup_control AS $$
BEGIN RAISE EXCEPTION 'immutable maintenance audit' USING ERRCODE='23514';END$$;
CREATE UNIQUE INDEX audit_blob_stage_owner ON easel_backup_control.audit((details->>'storage_key')) WHERE action='blob_staged';
CREATE TRIGGER audit_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON easel_backup_control.audit FOR EACH STATEMENT EXECUTE FUNCTION easel_backup_control.deny_audit_change();
CREATE FUNCTION easel_backup_control.generation_digest() RETURNS text LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,easel_backup_control AS $$
 SELECT encode(sha256(convert_to(coalesce(jsonb_agg(jsonb_build_object('worker_id',w.worker_id,'generation',w.generation,'identity',w.identity,'domain',a.domain) ORDER BY w.worker_id)::text,'[]'),'UTF8')),'hex') FROM easel_backup_control.workers w LEFT JOIN easel_backup_control.worker_attestations a ON a.worker_id=w.worker_id AND a.generation=w.generation WHERE w.active
$$;
CREATE FUNCTION easel_backup_control.register_worker(p_identity jsonb,p_token bytea) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,easel_backup_control AS $$
DECLARE s easel_backup_control.control%ROWTYPE; old easel_backup_control.workers%ROWTYPE;
BEGIN
 SELECT * INTO s FROM easel_backup_control.control WHERE id=1 FOR SHARE;
 IF s.state<>'OPEN' OR octet_length(p_token) IS DISTINCT FROM 32 OR jsonb_typeof(p_identity)<>'object'
 OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(p_identity) k) IS DISTINCT FROM ARRAY['boot_id','generation','host_id','process_start','worker_id'] THEN RAISE EXCEPTION 'media maintenance permit required' USING ERRCODE='23514';END IF;
 SELECT * INTO old FROM easel_backup_control.workers WHERE worker_id=(p_identity->>'worker_id')::uuid FOR UPDATE;
 IF FOUND THEN
  IF old.identity<>p_identity OR old.token_hash IS DISTINCT FROM encode(sha256(p_token),'hex') OR NOT old.active THEN RAISE EXCEPTION 'media maintenance permit required' USING ERRCODE='23514';END IF;
  RETURN;
 END IF;
 INSERT INTO easel_backup_control.workers(worker_id,generation,identity,token_hash) VALUES((p_identity->>'worker_id')::uuid,(p_identity->>'generation')::uuid,p_identity,encode(sha256(p_token),'hex'));
 INSERT INTO easel_backup_control.audit(action,epoch,details) VALUES('worker_registered',s.epoch,jsonb_build_object('worker_id',p_identity->>'worker_id','generation',p_identity->>'generation'));
END$$;
CREATE FUNCTION easel_backup_control.attest_worker(p_identity jsonb,p_token bytea,p_domain jsonb) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,easel_backup_control AS $$
DECLARE s easel_backup_control.control%ROWTYPE;w easel_backup_control.workers%ROWTYPE;old jsonb;k text;
BEGIN
 SELECT * INTO s FROM easel_backup_control.control WHERE id=1 FOR SHARE;
 SELECT * INTO w FROM easel_backup_control.workers WHERE worker_id=(p_identity->>'worker_id')::uuid FOR UPDATE;
 IF NOT FOUND OR NOT w.active OR s.state<>'OPEN' OR w.identity IS DISTINCT FROM p_identity OR octet_length(p_token) IS DISTINCT FROM 32 OR w.token_hash IS DISTINCT FROM encode(sha256(p_token),'hex') OR jsonb_typeof(p_domain) IS DISTINCT FROM 'object' OR (SELECT array_agg(x ORDER BY x) FROM jsonb_object_keys(p_domain) x) IS DISTINCT FROM ARRAY['control_dev','control_fs_sha256','control_ino','control_namespace_sha256','source_dev','source_fs_sha256','source_ino','source_namespace_sha256'] THEN RAISE EXCEPTION 'media maintenance domain rejected' USING ERRCODE='23514';END IF;
 FOREACH k IN ARRAY ARRAY['control_dev','control_ino','source_dev','source_ino'] LOOP
  IF jsonb_typeof(p_domain->k) IS DISTINCT FROM 'number' OR p_domain->>k !~ '^[0-9]+$' OR (p_domain->>k)::numeric>9223372036854775807 THEN RAISE EXCEPTION 'media maintenance domain rejected' USING ERRCODE='23514';END IF;
 END LOOP;
 FOREACH k IN ARRAY ARRAY['control_fs_sha256','control_namespace_sha256','source_fs_sha256','source_namespace_sha256'] LOOP
  IF jsonb_typeof(p_domain->k) IS DISTINCT FROM 'string' OR p_domain->>k !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'media maintenance domain rejected' USING ERRCODE='23514';END IF;
 END LOOP;
 IF p_domain->>'control_namespace_sha256' IS DISTINCT FROM p_domain->>'source_namespace_sha256' OR (p_domain->>'control_dev'=p_domain->>'source_dev' AND p_domain->>'control_ino'=p_domain->>'source_ino') THEN RAISE EXCEPTION 'media maintenance domain rejected' USING ERRCODE='23514';END IF;
 SELECT domain INTO old FROM easel_backup_control.worker_attestations WHERE worker_id=w.worker_id;
 IF FOUND THEN
  IF old IS DISTINCT FROM p_domain THEN RAISE EXCEPTION 'media maintenance domain rejected' USING ERRCODE='23514';END IF;
  RETURN;
 END IF;
 INSERT INTO easel_backup_control.worker_attestations(worker_id,generation,domain)VALUES(w.worker_id,w.generation,p_domain);
 INSERT INTO easel_backup_control.audit(action,epoch,details)VALUES('worker_domain_attested',s.epoch,jsonb_build_object('worker_id',w.worker_id,'generation',w.generation));
END$$;
CREATE FUNCTION easel_backup_control.unregister_worker(p_identity jsonb,p_token bytea) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,easel_backup_control AS $$
DECLARE s easel_backup_control.control%ROWTYPE;w easel_backup_control.workers%ROWTYPE;
BEGIN
 SELECT * INTO s FROM easel_backup_control.control WHERE id=1 FOR SHARE;
 SELECT * INTO w FROM easel_backup_control.workers WHERE worker_id=(p_identity->>'worker_id')::uuid FOR UPDATE;
 IF NOT FOUND OR s.state NOT IN ('OPEN','DRAINING') OR w.identity IS DISTINCT FROM p_identity OR octet_length(p_token) IS DISTINCT FROM 32 OR w.token_hash IS DISTINCT FROM encode(sha256(p_token),'hex') OR EXISTS(SELECT 1 FROM easel_backup_control.permits WHERE worker_id=w.worker_id) THEN RAISE EXCEPTION 'media maintenance permit required' USING ERRCODE='23514';END IF;
 IF NOT w.active THEN RETURN;END IF;
 UPDATE easel_backup_control.workers SET active=false WHERE worker_id=w.worker_id AND generation=w.generation;
 INSERT INTO easel_backup_control.audit(action,epoch,details) VALUES('worker_stopped',s.epoch,jsonb_build_object('worker_id',w.worker_id,'generation',w.generation));
END$$;
CREATE FUNCTION easel_backup_control.issue_permit(p_identity jsonb,p_worker_token bytea,p_hash text,p_kind text,p_subject text,p_attempt text,p_upstream text) RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,easel_backup_control AS $$
DECLARE s easel_backup_control.control%ROWTYPE;w easel_backup_control.workers%ROWTYPE;
BEGIN
 SELECT * INTO s FROM easel_backup_control.control WHERE id=1 FOR SHARE;
 SELECT * INTO w FROM easel_backup_control.workers WHERE worker_id=(p_identity->>'worker_id')::uuid FOR SHARE;
 IF s.state<>'OPEN' OR NOT FOUND OR NOT w.active OR w.identity<>p_identity OR w.token_hash IS DISTINCT FROM encode(sha256(p_worker_token),'hex') OR p_hash IS NULL OR p_hash !~ '^[0-9a-f]{64}$'
 OR p_subject IS NULL OR p_subject !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$' OR p_kind IS NULL OR p_kind NOT IN ('catalog','policy','mint','upload','job','gc','maintenance')
 OR (p_kind IN ('job','upload','gc') AND NOT EXISTS(SELECT 1 FROM easel_backup_control.worker_attestations a WHERE a.worker_id=w.worker_id AND a.generation=w.generation))
 OR ((p_kind IN ('job','gc')) IS DISTINCT FROM (p_attempt IS NOT NULL))
 OR (p_attempt IS NOT NULL AND p_attempt !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$') OR (p_upstream IS NOT NULL AND p_upstream !~ '^[0-9a-f]{64}$') THEN RAISE EXCEPTION 'media maintenance permit required' USING ERRCODE='23514';END IF;
 INSERT INTO easel_backup_control.permits(token_hash,epoch,kind,subject_id,attempt_id,worker_id,generation,upstream_sha256)
 VALUES(p_hash,s.epoch+1,p_kind,p_subject,p_attempt,w.worker_id,w.generation,p_upstream);
 RETURN s.epoch+1;
END$$;
CREATE FUNCTION easel_backup_control.validate_permit(p_token bytea,p_epoch bigint,p_kind text,p_subject text,p_attempt text,p_worker uuid,p_generation uuid) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,easel_backup_control AS $$
DECLARE s easel_backup_control.control%ROWTYPE;p easel_backup_control.permits%ROWTYPE;w easel_backup_control.workers%ROWTYPE;
BEGIN
 SELECT * INTO s FROM easel_backup_control.control WHERE id=1 FOR SHARE;
 SELECT * INTO p FROM easel_backup_control.permits WHERE token_hash=encode(sha256(p_token),'hex') FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'media maintenance permit required' USING ERRCODE='23514';END IF;
 SELECT * INTO w FROM easel_backup_control.workers WHERE worker_id=p.worker_id FOR SHARE;
 IF p_epoch IS NULL OR p_kind IS NULL OR p_subject IS NULL OR p_worker IS NULL OR p_generation IS NULL OR octet_length(p_token) IS DISTINCT FROM 32 OR p.status<>'active' OR NOT w.active OR p.worker_id<>p_worker OR p.generation<>p_generation OR w.generation<>p_generation
 OR s.state NOT IN ('OPEN','DRAINING') OR p.epoch<>(s.epoch+CASE WHEN s.state='OPEN' THEN 1 ELSE 0 END)
 OR p.epoch<>p_epoch OR p.kind<>p_kind OR p.subject_id<>p_subject OR p.attempt_id IS DISTINCT FROM p_attempt THEN RAISE EXCEPTION 'media maintenance permit required' USING ERRCODE='23514';END IF;
 PERFORM set_config('easel_backup_control.permit_token',encode(p_token,'hex'),true);
END$$;
CREATE FUNCTION easel_backup_control.assert_stable_business(p_kind text,p_subject text,p_attempt text,p_owner_sha text) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,easel_backup_control AS $$
DECLARE app text;r jsonb;bad boolean;present boolean;
BEGIN
 SELECT application_schema INTO app FROM easel_backup_control.settings WHERE id=1;
 IF p_kind='job' THEN
  EXECUTE format('SELECT to_jsonb(j) FROM %I.media_jobs j WHERE id=$1',app) INTO r USING p_subject;
  IF r IS NULL THEN
   EXECUTE format('SELECT EXISTS(SELECT 1 FROM %I.credit_reservations WHERE operation=''media'' AND idempotency_key=$1) OR EXISTS(SELECT 1 FROM %I.media_storage_budgets WHERE job_id=$1) OR EXISTS(SELECT 1 FROM %I.media_blobs WHERE job_id=$1)',app,app,app) INTO bad USING p_subject;
   IF bad THEN RAISE EXCEPTION 'media maintenance permit required' USING ERRCODE='23514';END IF;
  ELSE
   IF r->>'attempt_id' IS DISTINCT FROM p_attempt OR r->>'status' NOT IN ('settled','cancelled','released') THEN RAISE EXCEPTION 'media maintenance permit required' USING ERRCODE='23514';END IF;
   IF to_regclass(format('%I.media_work',app)) IS NOT NULL THEN
    EXECUTE format('SELECT EXISTS(SELECT 1 FROM %I.media_work WHERE job_id=$1 AND phase<>''done'')',app) INTO bad USING p_subject;
    IF bad THEN RAISE EXCEPTION 'media maintenance permit required' USING ERRCODE='23514';END IF;
   END IF;
  END IF;
 ELSIF p_kind='gc' THEN
  EXECUTE format('SELECT to_jsonb(b) FROM %I.media_blobs b WHERE storage_key=$1',app) INTO r USING p_subject;
  IF r IS NOT NULL AND (r->>'state'<>'deleted' OR r->>'gc_token' IS DISTINCT FROM p_attempt) THEN RAISE EXCEPTION 'media maintenance permit required' USING ERRCODE='23514';END IF;
 ELSIF p_kind='upload' THEN
  EXECUTE format('SELECT EXISTS(SELECT 1 FROM %I.media_blobs b JOIN easel_backup_control.audit a ON a.action=''blob_staged'' AND a.details->>''storage_key''=b.storage_key AND a.details->>''owner_permit_sha256''=$2 WHERE invocation_id=$1 AND kind=''asset'' AND (state NOT IN (''attached'',''deleted'') OR (state=''attached'' AND NOT EXISTS(SELECT 1 FROM %I.media_assets a WHERE a.id=b.attached_id AND a.storage_key=b.storage_key))))',app,app) INTO bad USING p_subject,p_owner_sha;
  IF bad THEN RAISE EXCEPTION 'media maintenance permit required' USING ERRCODE='23514';END IF;
 ELSIF p_kind='mint' THEN
  EXECUTE format('SELECT EXISTS(SELECT 1 FROM %I.media_invocations i WHERE i.id=$1 AND NOT EXISTS(SELECT 1 FROM %I.media_task_tokens t WHERE t.invocation_id=i.id))',app,app) INTO bad USING p_subject;
  IF bad THEN RAISE EXCEPTION 'media maintenance permit required' USING ERRCODE='23514';END IF;
 ELSIF p_kind='catalog' THEN
  EXECUTE format('SELECT EXISTS(SELECT 1 FROM %I.media_models m WHERE m.id=$1 AND NOT EXISTS(SELECT 1 FROM %I.media_model_states s WHERE s.model_id=m.id))',app,app) INTO bad USING p_subject;
  IF bad THEN RAISE EXCEPTION 'media maintenance permit required' USING ERRCODE='23514';END IF;
 END IF;
END$$;
CREATE FUNCTION easel_backup_control.finish_permit(p_token bytea,p_epoch bigint,p_business text) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,easel_backup_control AS $$
DECLARE p easel_backup_control.permits%ROWTYPE;consumed bigint;
BEGIN
 IF octet_length(p_token) IS DISTINCT FROM 32 OR p_epoch IS NULL OR p_epoch<1 OR p_business IS NULL OR p_business !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'media maintenance permit required' USING ERRCODE='23514';END IF;
 SELECT * INTO p FROM easel_backup_control.permits WHERE token_hash=encode(sha256(p_token),'hex') FOR UPDATE;
 IF NOT FOUND OR p.status IS DISTINCT FROM 'active' OR p.epoch IS DISTINCT FROM p_epoch THEN RAISE EXCEPTION 'media maintenance permit required' USING ERRCODE='23514';END IF;
 PERFORM easel_backup_control.assert_stable_business(p.kind,p.subject_id,p.attempt_id,p.token_hash);
 DELETE FROM easel_backup_control.permits WHERE token_hash=p.token_hash AND epoch=p_epoch AND status='active';
 GET DIAGNOSTICS consumed=ROW_COUNT;
 IF consumed IS DISTINCT FROM 1 THEN RAISE EXCEPTION 'media maintenance permit required' USING ERRCODE='23514';END IF;
 INSERT INTO easel_backup_control.audit(action,epoch,details) VALUES('permit_completed',p.epoch,jsonb_build_object('kind',p.kind,'business_sha256',p_business,'permit_sha256',p.token_hash));
END$$;
CREATE FUNCTION easel_backup_control.permit_result(p_token bytea,p_epoch bigint) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,easel_backup_control AS $$
DECLARE hashed text;p easel_backup_control.permits%ROWTYPE;result jsonb;
BEGIN
 IF octet_length(p_token) IS DISTINCT FROM 32 OR p_epoch IS NULL THEN RAISE EXCEPTION 'media maintenance permit required' USING ERRCODE='23514';END IF;
 hashed:=encode(sha256(p_token),'hex');
 SELECT * INTO p FROM easel_backup_control.permits WHERE token_hash=hashed AND epoch=p_epoch;
 IF FOUND THEN RETURN jsonb_build_object('state',p.status);END IF;
 SELECT details INTO result FROM easel_backup_control.audit WHERE action='permit_completed' AND epoch=p_epoch AND details->>'permit_sha256'=hashed ORDER BY id DESC LIMIT 1;
 IF result IS NULL THEN RAISE EXCEPTION 'media maintenance permit required' USING ERRCODE='23514';END IF;
 RETURN jsonb_build_object('state','completed','business_sha256',result->>'business_sha256');
END$$;
CREATE FUNCTION easel_backup_control.mark_unknown(p_token bytea,p_epoch bigint) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,easel_backup_control AS $$
BEGIN
 UPDATE easel_backup_control.permits SET status='unknown' WHERE token_hash=encode(sha256(p_token),'hex') AND epoch=p_epoch;
 IF NOT FOUND THEN RAISE EXCEPTION 'media maintenance permit required' USING ERRCODE='23514';END IF;
END$$;
CREATE FUNCTION easel_backup_control.begin_window(p_nonce bytea,p_window uuid,p_binding jsonb,p_original jsonb) RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,easel_backup_control AS $$
DECLARE s easel_backup_control.control%ROWTYPE;payload jsonb;field text;
BEGIN
 -- Validate every input before the same-owner receipt-recovery early return.
 -- Native UUID values are canonical text here; require RFC4122 version four.
 IF octet_length(p_nonce) IS DISTINCT FROM 32 OR p_window IS NULL OR p_window::text !~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' OR jsonb_typeof(p_binding) IS DISTINCT FROM 'object' OR jsonb_typeof(p_original) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'media maintenance window rejected' USING ERRCODE='23514';END IF;
 IF (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(p_binding) k) IS DISTINCT FROM ARRAY['boot_id','control_dev','control_ino','deadline_ns','generation_digest','host_id','process_start','source_dev','source_ino'] OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(p_original) k) IS DISTINCT FROM ARRAY['boot_id','configuration_state','control_dev','control_ino','generation_digest','host_id','process_start','source_dev','source_ino'] OR jsonb_typeof(p_original->'configuration_state') IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'media maintenance window rejected' USING ERRCODE='23514';END IF;
 FOREACH payload IN ARRAY ARRAY[p_binding,p_original] LOOP
  FOREACH field IN ARRAY ARRAY['host_id','boot_id','process_start'] LOOP
   IF jsonb_typeof(payload->field) IS DISTINCT FROM 'string' OR payload->>field !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$' THEN RAISE EXCEPTION 'media maintenance window rejected' USING ERRCODE='23514';END IF;
  END LOOP;
  FOREACH field IN ARRAY ARRAY['control_dev','control_ino','source_dev','source_ino'] LOOP
   IF jsonb_typeof(payload->field) IS DISTINCT FROM 'number' OR payload->>field !~ '^(0|[1-9][0-9]*)$' THEN RAISE EXCEPTION 'media maintenance window rejected' USING ERRCODE='23514';END IF;
   IF (payload->>field)::numeric>9223372036854775807 THEN RAISE EXCEPTION 'media maintenance window rejected' USING ERRCODE='23514';END IF;
  END LOOP;
  IF jsonb_typeof(payload->'generation_digest') IS DISTINCT FROM 'string' OR payload->>'generation_digest' !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'media maintenance window rejected' USING ERRCODE='23514';END IF;
 END LOOP;
 IF jsonb_typeof(p_binding->'deadline_ns') IS DISTINCT FROM 'number' OR p_binding->>'deadline_ns' !~ '^[1-9][0-9]*$' THEN RAISE EXCEPTION 'media maintenance window rejected' USING ERRCODE='23514';END IF;
 IF (p_binding->>'deadline_ns')::numeric>9223372036854775807 OR (p_original-'configuration_state') IS DISTINCT FROM (p_binding-'deadline_ns') THEN RAISE EXCEPTION 'media maintenance window rejected' USING ERRCODE='23514';END IF;
 SELECT * INTO s FROM easel_backup_control.control WHERE id=1 FOR UPDATE;
 IF NOT FOUND OR p_binding->>'generation_digest' IS DISTINCT FROM easel_backup_control.generation_digest() THEN RAISE EXCEPTION 'media maintenance window rejected' USING ERRCODE='23514';END IF;
 IF s.window_id=p_window AND s.owner_hash=encode(sha256(p_nonce),'hex') THEN
  IF s.binding IS DISTINCT FROM p_binding OR s.original_state IS DISTINCT FROM p_original THEN RAISE EXCEPTION 'media maintenance window rejected' USING ERRCODE='23514';END IF;
  RETURN s.epoch;
 END IF;
 IF s.state IS DISTINCT FROM 'OPEN' THEN RAISE EXCEPTION 'media maintenance window rejected' USING ERRCODE='23514';END IF;
 UPDATE easel_backup_control.control SET state='DRAINING',epoch=epoch+1,window_id=p_window,owner_hash=encode(sha256(p_nonce),'hex'),binding=p_binding,original_state=p_original WHERE id=1 RETURNING * INTO s;
 INSERT INTO easel_backup_control.audit(action,window_id,epoch,details) VALUES('DRAINING',p_window,s.epoch,jsonb_build_object('intent','closed_new_admissions'));
 RETURN s.epoch;
END$$;
CREATE FUNCTION easel_backup_control.observe_window(p_nonce bytea,p_window uuid) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,easel_backup_control AS $$
DECLARE s easel_backup_control.control%ROWTYPE;
BEGIN
 IF octet_length(p_nonce) IS DISTINCT FROM 32 OR p_window IS NULL OR p_window::text !~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' THEN RAISE EXCEPTION 'media maintenance window rejected' USING ERRCODE='23514';END IF;
 SELECT * INTO s FROM easel_backup_control.control WHERE id=1;
 IF NOT FOUND OR s.window_id IS DISTINCT FROM p_window OR s.owner_hash IS DISTINCT FROM encode(sha256(p_nonce),'hex') THEN RAISE EXCEPTION 'media maintenance window rejected' USING ERRCODE='23514';END IF;
 RETURN jsonb_build_object('state',s.state,'epoch',s.epoch,'binding',s.binding,'result',(SELECT details FROM easel_backup_control.audit WHERE window_id=s.window_id AND epoch=s.epoch AND action=s.state ORDER BY id DESC LIMIT 1));
END$$;
CREATE FUNCTION easel_backup_control.verify_window(p_nonce bytea,p_window uuid,p_epoch bigint,p_expected text) RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,easel_backup_control AS $$
DECLARE s easel_backup_control.control%ROWTYPE;generation text;
BEGIN
 SELECT * INTO s FROM easel_backup_control.control WHERE id=1;
 generation:=easel_backup_control.generation_digest();
 IF octet_length(p_nonce) IS DISTINCT FROM 32 OR p_window IS NULL OR p_epoch IS NULL OR p_expected IS NULL OR p_expected NOT IN ('DRAINING','CAPTURING','VERIFYING','RECOVERING') OR s.window_id IS DISTINCT FROM p_window OR s.owner_hash IS DISTINCT FROM encode(sha256(p_nonce),'hex') OR s.epoch IS DISTINCT FROM p_epoch OR s.state IS DISTINCT FROM p_expected OR s.binding->>'generation_digest' IS DISTINCT FROM generation THEN RAISE EXCEPTION 'media maintenance window rejected' USING ERRCODE='23514';END IF;
 RETURN jsonb_build_object('binding',s.binding,'state',s.state,'epoch',s.epoch,'generation_digest',generation);
END$$;
CREATE FUNCTION easel_backup_control.transition(p_nonce bytea,p_window uuid,p_epoch bigint,p_expected text,p_next text,p_result jsonb) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,easel_backup_control AS $$
DECLARE s easel_backup_control.control%ROWTYPE;
BEGIN
 -- Reject SQL NULL/invalid input before any idempotent success path. SQL
 -- three-valued logic must never turn an unknown expected state into a CAS.
 IF octet_length(p_nonce) IS DISTINCT FROM 32 OR p_window IS NULL OR p_epoch IS NULL OR p_epoch<1 OR p_expected IS NULL OR p_next IS NULL OR jsonb_typeof(p_result) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'media maintenance window rejected' USING ERRCODE='23514';END IF;
 IF p_expected NOT IN ('DRAINING','CAPTURING','VERIFYING','RECOVERING','NEEDS_REVIEW') OR p_next NOT IN ('CAPTURING','VERIFYING','RECOVERING','OPEN','NEEDS_REVIEW') OR NOT ((p_expected='DRAINING' AND p_next='CAPTURING') OR (p_expected='CAPTURING' AND p_next='VERIFYING') OR (p_expected='VERIFYING' AND p_next='RECOVERING') OR (p_expected='RECOVERING' AND p_next='OPEN') OR (p_expected<>'OPEN' AND p_next='NEEDS_REVIEW')) THEN RAISE EXCEPTION 'media maintenance window rejected' USING ERRCODE='23514';END IF;
 -- Canonical proof types must be checked before an idempotent success path.
 -- ->> alone also turns JSON numbers into text that can match the hex regex.
 IF p_next='OPEN' AND (
  (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(p_result) k) IS DISTINCT FROM ARRAY['original_state_sha256','source_identity_sha256','stable_side_effects_sha256']
  OR jsonb_typeof(p_result->'source_identity_sha256') IS DISTINCT FROM 'string'
  OR jsonb_typeof(p_result->'stable_side_effects_sha256') IS DISTINCT FROM 'string'
  OR jsonb_typeof(p_result->'original_state_sha256') IS DISTINCT FROM 'string'
  OR p_result->>'source_identity_sha256' IS NULL OR p_result->>'stable_side_effects_sha256' IS NULL OR p_result->>'original_state_sha256' IS NULL
  OR p_result->>'source_identity_sha256' !~ '^[0-9a-f]{64}$' OR p_result->>'stable_side_effects_sha256' !~ '^[0-9a-f]{64}$' OR p_result->>'original_state_sha256' !~ '^[0-9a-f]{64}$'
 ) THEN RAISE EXCEPTION 'media maintenance window rejected' USING ERRCODE='23514';END IF;
 SELECT * INTO s FROM easel_backup_control.control WHERE id=1 FOR UPDATE;
 IF NOT FOUND OR s.window_id IS DISTINCT FROM p_window OR s.owner_hash IS DISTINCT FROM encode(sha256(p_nonce),'hex') OR s.epoch IS DISTINCT FROM p_epoch THEN RAISE EXCEPTION 'media maintenance window rejected' USING ERRCODE='23514';END IF;
 IF s.state=p_next AND EXISTS(SELECT 1 FROM easel_backup_control.audit WHERE window_id=p_window AND epoch=p_epoch AND action=p_next AND details=p_result) THEN RETURN;END IF;
 IF s.state IS DISTINCT FROM p_expected THEN RAISE EXCEPTION 'media maintenance window rejected' USING ERRCODE='23514';END IF;
 IF p_next<>'NEEDS_REVIEW' AND (EXISTS(SELECT 1 FROM easel_backup_control.permits) OR EXISTS(SELECT 1 FROM easel_backup_control.workers w LEFT JOIN easel_backup_control.worker_attestations a ON a.worker_id=w.worker_id AND a.generation=w.generation WHERE w.active AND a.worker_id IS NULL) OR s.binding->>'generation_digest'<>easel_backup_control.generation_digest()) THEN RAISE EXCEPTION 'media maintenance window rejected' USING ERRCODE='23514';END IF;
 UPDATE easel_backup_control.control SET state=p_next WHERE id=1;
 INSERT INTO easel_backup_control.audit(action,window_id,epoch,details) VALUES(p_next,p_window,p_epoch,p_result);
END$$;
CREATE FUNCTION easel_backup_control.assert_current(p_kind text,p_subject text,p_attempt text) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,easel_backup_control AS $$
DECLARE raw text;p easel_backup_control.permits%ROWTYPE;
BEGIN
 raw:=current_setting('easel_backup_control.permit_token',true);
 IF raw IS NULL OR raw !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'media maintenance permit required' USING ERRCODE='23514';END IF;
 SELECT * INTO p FROM easel_backup_control.permits WHERE token_hash=encode(sha256(decode(raw,'hex')),'hex');
 IF NOT FOUND OR p.kind<>p_kind OR p.subject_id<>p_subject OR (p_attempt IS NOT NULL AND p.attempt_id IS DISTINCT FROM p_attempt) THEN RAISE EXCEPTION 'media maintenance permit required' USING ERRCODE='23514';END IF;
 PERFORM easel_backup_control.validate_permit(decode(raw,'hex'),p.epoch,p.kind,p.subject_id,p.attempt_id,p.worker_id,p.generation);
END$$;
-- A GC capability never outranks a still-live/unknown writer of that blob.
CREATE FUNCTION easel_backup_control.gc_writer_idle(p_key text) RETURNS boolean LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,easel_backup_control AS $$
DECLARE app text;b jsonb;
BEGIN
 SELECT application_schema INTO app FROM easel_backup_control.settings WHERE id=1;
 EXECUTE format('SELECT to_jsonb(b) FROM %I.media_blobs b WHERE storage_key=$1',app) INTO b USING p_key;
 IF b IS NULL THEN RETURN false;END IF;
 IF EXISTS(SELECT 1 FROM easel_backup_control.audit a WHERE a.action='blob_staged' AND a.details->>'storage_key'=p_key) THEN
  RETURN NOT EXISTS(SELECT 1 FROM easel_backup_control.permits p JOIN easel_backup_control.audit a ON a.action='blob_staged' AND a.details->>'storage_key'=p_key AND a.details->>'owner_permit_sha256'=p.token_hash WHERE p.status IN ('active','unknown'));
 END IF;
 RETURN NOT EXISTS(SELECT 1 FROM easel_backup_control.permits p WHERE p.status IN ('active','unknown') AND ((p.kind='upload' AND p.subject_id=b->>'invocation_id') OR (p.kind='job' AND p.subject_id=b->>'job_id')));
END$$;
CREATE FUNCTION easel_backup_control.blob_writer_owned(p_token bytea,p_epoch bigint,p_key text) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,easel_backup_control AS $$
DECLARE p easel_backup_control.permits%ROWTYPE;origin jsonb;
BEGIN
 IF octet_length(p_token) IS DISTINCT FROM 32 OR p_epoch IS NULL OR p_key IS NULL THEN RETURN false;END IF;
 SELECT * INTO p FROM easel_backup_control.permits WHERE token_hash=encode(sha256(p_token),'hex') AND epoch=p_epoch;
 IF NOT FOUND OR p.kind NOT IN ('job','upload') THEN RETURN false;END IF;
 PERFORM easel_backup_control.validate_permit(p_token,p.epoch,p.kind,p.subject_id,p.attempt_id,p.worker_id,p.generation);
 SELECT details INTO origin FROM easel_backup_control.audit WHERE action='blob_staged' AND details->>'storage_key'=p_key;
 RETURN origin IS NOT NULL AND origin->>'owner_permit_sha256'=p.token_hash AND origin->>'kind'=p.kind AND origin->>'subject_id'=p.subject_id AND (origin->>'attempt_id') IS NOT DISTINCT FROM p.attempt_id;
END$$;
-- Automatic budget blocking is part of the original job transaction, not a
-- blanket policy privilege. Recompute its frozen evidence using exact integer
-- rational arithmetic, with the same one-round-up rule as media.rates.cost.
CREATE FUNCTION easel_backup_control.job_can_block_binding(p_subject text,p_attempt text,p_binding text) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,easel_backup_control AS $$
DECLARE app text;j jsonb;e jsonb;meter jsonb;unit text;q numeric;maximum numeric;n numeric;d numeric;total_n numeric:=0;total_d numeric:=1;over_units boolean:=false;
BEGIN
 SELECT application_schema INTO app FROM easel_backup_control.settings WHERE id=1;
 EXECUTE format('SELECT to_jsonb(j),to_jsonb(e) FROM %I.media_jobs j JOIN %I.media_terminal_evidence e ON e.job_id=j.id AND e.attempt_id=j.attempt_id WHERE j.id=$1 AND j.attempt_id=$2',app,app) INTO j,e USING p_subject,p_attempt;
 IF j IS NULL OR e IS NULL OR j->>'binding_hash' IS DISTINCT FROM p_binding OR j->>'status' IS DISTINCT FROM 'pending_reconciliation' OR j->>'reason' IS DISTINCT FROM 'usage_exceeds_reservation' OR jsonb_typeof(j->'model_definition'->'rate'->'meters') IS DISTINCT FROM 'array' OR jsonb_typeof(e->'quantities') IS DISTINCT FROM 'object' THEN RETURN false;END IF;
 IF jsonb_array_length(j->'model_definition'->'rate'->'meters') NOT BETWEEN 1 AND 8 OR (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(e->'quantities') key) IS DISTINCT FROM (SELECT array_agg(value->>'unit' ORDER BY value->>'unit') FROM jsonb_array_elements(j->'model_definition'->'rate'->'meters')) THEN RETURN false;END IF;
 FOR meter IN SELECT value FROM jsonb_array_elements(j->'model_definition'->'rate'->'meters') LOOP
  unit:=meter->>'unit';
  IF unit IS NULL OR unit NOT IN ('audio_clip','image','video_ms','audio_ms','unicode_scalar','registration','input_token','output_token') OR jsonb_typeof(e->'quantities'->unit) IS DISTINCT FROM 'number' OR e->'quantities'->>unit !~ '^[0-9]{1,19}$' OR jsonb_typeof(j->'model_definition'->'limits'->'max_units'->unit) IS DISTINCT FROM 'number' OR j->'model_definition'->'limits'->'max_units'->>unit !~ '^[0-9]{1,19}$' OR jsonb_typeof(meter->'numerator') IS DISTINCT FROM 'string' OR meter->>'numerator' !~ '^[0-9]{1,19}$' OR jsonb_typeof(meter->'denominator') IS DISTINCT FROM 'string' OR meter->>'denominator' !~ '^[0-9]{1,19}$' THEN RETURN false;END IF;
  q:=(e->'quantities'->>unit)::numeric;maximum:=(j->'model_definition'->'limits'->'max_units'->>unit)::numeric;n:=(meter->>'numerator')::numeric;d:=(meter->>'denominator')::numeric;
  IF greatest(q,maximum,n,d)>9223372036854775807 OR d<1 THEN RETURN false;END IF;
  over_units:=over_units OR q>maximum;total_n:=total_n*d+q*n*total_d;total_d:=total_d*d;
 END LOOP;
 RETURN over_units OR div(total_n+total_d-1,total_d)>(j->>'amount')::numeric;
END$$;
CREATE FUNCTION easel_backup_control.guard_media_mutation() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,easel_backup_control AS $$
DECLARE v jsonb;app text;subject text;attempt text;kind text:='job';r jsonb;reservation text;raw bytea;owner_sha text;op_epoch bigint;
BEGIN
 IF TG_OP='TRUNCATE' THEN RAISE EXCEPTION 'media maintenance permit required' USING ERRCODE='23514';END IF;
 v:=CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
 SELECT application_schema INTO app FROM easel_backup_control.settings WHERE id=1;
 IF TG_TABLE_NAME='credit_reservations' THEN
  IF v->>'operation'<>'media' AND (TG_OP<>'UPDATE' OR to_jsonb(OLD)->>'operation'<>'media') THEN RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;END IF;
  subject:=v->>'idempotency_key';
 ELSIF TG_TABLE_NAME IN ('credit_holds','credit_journals','credit_entries') THEN
  IF TG_TABLE_NAME='credit_holds' THEN reservation:=v->>'reservation_id';
  ELSIF TG_TABLE_NAME='credit_journals' THEN reservation:=v->>'source';
  ELSE EXECUTE format('SELECT source FROM %I.credit_journals WHERE id=$1',app) INTO reservation USING v->>'journal_id';END IF;
  EXECUTE format('SELECT to_jsonb(r) FROM %I.credit_reservations r WHERE id=$1',app) INTO r USING reservation;
  IF r IS NULL OR r->>'operation'<>'media' THEN RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;END IF;
  subject:=r->>'idempotency_key';
 ELSIF TG_TABLE_NAME IN ('media_models','media_model_states') THEN kind:='catalog';subject:=coalesce(v->>'model_id',v->>'id');
 ELSIF TG_TABLE_NAME='media_blocked_bindings' THEN
  kind:='policy';subject:=v->>'binding_hash';
  SELECT jsonb_build_object('kind',p.kind,'subject_id',p.subject_id,'attempt_id',p.attempt_id) INTO r FROM easel_backup_control.permits p WHERE p.token_hash=encode(sha256(decode(coalesce(current_setting('easel_backup_control.permit_token',true),''),'hex')),'hex');
  IF TG_OP='INSERT' AND v->>'reason'='usage_exceeds_reservation' AND r->>'kind'='job' THEN
   kind:='job';subject:=r->>'subject_id';attempt:=r->>'attempt_id';
   IF NOT easel_backup_control.job_can_block_binding(subject,attempt,v->>'binding_hash') THEN RAISE EXCEPTION 'media maintenance permit required' USING ERRCODE='23514';END IF;
  END IF;
 ELSIF TG_TABLE_NAME='media_storage_policy' THEN kind:='policy';subject:='global';
 ELSIF TG_TABLE_NAME='media_invocations' AND TG_OP='UPDATE' THEN
  SELECT p.subject_id INTO subject FROM easel_backup_control.permits p WHERE p.kind='job' AND p.token_hash=encode(sha256(decode(coalesce(current_setting('easel_backup_control.permit_token',true),''),'hex')),'hex');
  EXECUTE format('SELECT attempt_id FROM %I.media_jobs WHERE id=$1 AND invocation_id=$2',app) INTO attempt USING subject,v->>'id';
  IF attempt IS NULL THEN RAISE EXCEPTION 'media maintenance permit required' USING ERRCODE='23514';END IF;
 ELSIF TG_TABLE_NAME IN ('media_invocations','media_task_tokens') THEN kind:='mint';subject:=coalesce(v->>'invocation_id',v->>'id');
 ELSIF TG_TABLE_NAME IN ('media_assets','media_artifacts') AND TG_OP='UPDATE' AND v->>'purged_at' IS DISTINCT FROM to_jsonb(OLD)->>'purged_at' THEN kind:='gc';subject:=v->>'storage_key';
 ELSIF TG_TABLE_NAME='media_blobs' AND v->>'state' IN ('gc_pending','deleted') THEN kind:='gc';subject:=v->>'storage_key';attempt:=v->>'gc_token';
 ELSIF TG_TABLE_NAME='media_assets' OR (TG_TABLE_NAME='media_blobs' AND v->>'kind'='asset') THEN kind:='upload';subject:=v->>'invocation_id';
 ELSIF TG_TABLE_NAME='media_voice_step_observations' THEN
  EXECUTE format('SELECT job_id,attempt_id FROM %I.media_voice_steps WHERE id=$1',app) INTO subject,attempt USING v->>'step_id';
 ELSE subject:=coalesce(v->>'job_id',CASE WHEN TG_TABLE_NAME='media_jobs' THEN v->>'id' END);attempt:=v->>'attempt_id';END IF;
 IF subject IS NULL THEN RAISE EXCEPTION 'media maintenance permit required' USING ERRCODE='23514';END IF;
 IF kind='job' AND attempt IS NULL THEN EXECUTE format('SELECT attempt_id FROM %I.media_jobs WHERE id=$1',app) INTO attempt USING subject;END IF;
 IF kind='gc' AND easel_backup_control.gc_writer_idle(subject) IS NOT TRUE THEN RAISE EXCEPTION 'media maintenance permit required' USING ERRCODE='23514';END IF;
 PERFORM easel_backup_control.assert_current(kind,subject,attempt);
 IF TG_TABLE_NAME='media_blobs' AND TG_OP='INSERT' AND kind IN ('job','upload') THEN
  raw:=decode(current_setting('easel_backup_control.permit_token'),'hex');owner_sha:=encode(sha256(raw),'hex');
  SELECT epoch INTO op_epoch FROM easel_backup_control.permits WHERE token_hash=owner_sha;
  IF EXISTS(SELECT 1 FROM easel_backup_control.audit WHERE action='blob_staged' AND details->>'storage_key'=v->>'storage_key') THEN
   IF easel_backup_control.blob_writer_owned(raw,op_epoch,v->>'storage_key') IS NOT TRUE THEN RAISE EXCEPTION 'media maintenance permit required' USING ERRCODE='23514';END IF;
  ELSE
   INSERT INTO easel_backup_control.audit(action,epoch,details)VALUES('blob_staged',op_epoch,jsonb_build_object('storage_key',v->>'storage_key','owner_permit_sha256',owner_sha,'kind',kind,'subject_id',subject,'attempt_id',attempt));
  END IF;
 ELSIF TG_TABLE_NAME IN ('media_blobs','media_assets','media_artifacts') AND kind IN ('job','upload') AND TG_OP<>'DELETE' THEN
  raw:=decode(current_setting('easel_backup_control.permit_token'),'hex');
  SELECT epoch INTO op_epoch FROM easel_backup_control.permits WHERE token_hash=encode(sha256(raw),'hex');
  IF easel_backup_control.blob_writer_owned(raw,op_epoch,v->>'storage_key') IS NOT TRUE THEN RAISE EXCEPTION 'media maintenance permit required' USING ERRCODE='23514';END IF;
 END IF;
 RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
END$$;
-- PostgreSQL's explicit xid8 -> xid conversion intentionally takes low 32 bits.
-- Compare typed xids, never bigint8 text directly to the 32-bit xmin counter.
CREATE FUNCTION easel_backup_control.xid_low32(value xid8) RETURNS xid LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog AS $$ SELECT value::xid $$;
-- Shared lots may change for text. Recheck only media reservations actually
-- changed in this transaction, preserving the pre-existing deferred ledger graph.
CREATE FUNCTION easel_backup_control.guard_media_lot_commit() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,easel_backup_control AS $$
DECLARE app text;lot text;r record;
BEGIN
 lot:=CASE WHEN TG_OP='DELETE' THEN OLD.id ELSE NEW.id END;
 SELECT application_schema INTO app FROM easel_backup_control.settings WHERE id=1;
 FOR r IN EXECUTE format('SELECT DISTINCT r.idempotency_key AS job_id,j.attempt_id FROM %I.credit_holds h JOIN %I.credit_reservations r ON r.id=h.reservation_id LEFT JOIN %I.media_jobs j ON j.id=r.idempotency_key WHERE h.lot_id=$1 AND r.operation=''media'' AND r.xmin=easel_backup_control.xid_low32(pg_current_xact_id())',app,app,app) USING lot LOOP
  IF r.attempt_id IS NULL THEN RAISE EXCEPTION 'media maintenance permit required' USING ERRCODE='23514';END IF;
  PERFORM easel_backup_control.assert_current('job',r.job_id,r.attempt_id);
 END LOOP;
 RETURN NULL;
END$$;
REVOKE ALL ON SCHEMA easel_backup_control FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA easel_backup_control FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA easel_backup_control FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA easel_backup_control FROM PUBLIC;
