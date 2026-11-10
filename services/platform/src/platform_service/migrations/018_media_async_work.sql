-- Original-job GET recovery only. No row authorizes a second generation POST.
ALTER TABLE media_provider_associations ADD CONSTRAINT media_async_job_association_identity
 UNIQUE(job_id,attempt_id,binding_hash,provider_scope_hash,provider_job_id);
CREATE TABLE media_work (
 job_id text PRIMARY KEY REFERENCES media_jobs(id),attempt_id text NOT NULL,binding_hash text NOT NULL,
 provider_scope_hash text NOT NULL,provider_job_id text NOT NULL,adapter text NOT NULL
 CHECK(adapter IN ('newapi_videos_v1','newapi_suno_v1','newapi_dashscope_music_v1')),
 phase text NOT NULL DEFAULT 'query' CHECK(phase IN ('query','download','settle','done','manual')),
 due_at timestamptz NOT NULL DEFAULT clock_timestamp(),deadline timestamptz NOT NULL,
 lease_token text,lease_until timestamptz,polls integer NOT NULL DEFAULT 0 CHECK(polls>=0 AND polls<=10000),
 failures integer NOT NULL DEFAULT 0 CHECK(failures>=0 AND failures<=10000),last_safe_code text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(job_id,attempt_id,binding_hash,provider_job_id),
 FOREIGN KEY(job_id,attempt_id,binding_hash,provider_scope_hash,provider_job_id)
 REFERENCES media_provider_associations(job_id,attempt_id,binding_hash,provider_scope_hash,provider_job_id),
 CHECK((lease_token IS NULL)=(lease_until IS NULL)),
 CHECK(last_safe_code IS NULL OR last_safe_code ~ '^[a-z][a-z0-9_]{0,127}$')
);
CREATE INDEX media_work_due ON media_work(due_at,job_id) WHERE phase IN ('query','download','settle');
CREATE TABLE media_observations (
 job_id text PRIMARY KEY REFERENCES media_work(job_id),attempt_id text NOT NULL,binding_hash text NOT NULL,
 provider_job_id text NOT NULL,state text NOT NULL CHECK(state IN ('completed','failed','cancelled')),
 quantities jsonb NOT NULL CHECK(jsonb_typeof(quantities)='object'),usage_hash text CHECK(usage_hash ~ '^[a-f0-9]{64}$'),
 response_hash text NOT NULL CHECK(response_hash ~ '^[a-f0-9]{64}$'),
 manifest jsonb NOT NULL CHECK(jsonb_typeof(manifest)='array' AND jsonb_array_length(manifest)<=32),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(job_id,attempt_id,binding_hash,provider_job_id)REFERENCES media_work(job_id,attempt_id,binding_hash,provider_job_id)
);
CREATE TABLE media_sources (
 job_id text NOT NULL REFERENCES media_observations(job_id),source_id text NOT NULL
 CHECK(length(source_id)>=1 AND length(source_id)<=160 AND source_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]*$'),
 kind text NOT NULL CHECK(kind IN ('content','url')),location text NOT NULL CHECK(length(location)>=1 AND length(location)<=2048),mime text,
 storage_key text NOT NULL UNIQUE CHECK(storage_key ~ '^[a-f0-9]{64}$'),artifact_key text NOT NULL UNIQUE,
 metadata jsonb CHECK(metadata IS NULL OR jsonb_typeof(metadata)='object'),artifact_id text UNIQUE REFERENCES media_artifacts(id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(job_id,source_id),CHECK(artifact_id IS NULL OR artifact_id=artifact_key)
);
CREATE FUNCTION guard_media_work() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR (to_jsonb(NEW)-ARRAY['phase','due_at','lease_token','lease_until','polls','failures','last_safe_code','updated_at']) IS DISTINCT FROM
 (to_jsonb(OLD)-ARRAY['phase','due_at','lease_token','lease_until','polls','failures','last_safe_code','updated_at']) OR
 (OLD.phase='done' AND NEW.phase<>'done') OR NEW.polls<OLD.polls OR NEW.failures<OLD.failures THEN
 RAISE EXCEPTION 'immutable async media binding' USING ERRCODE='23514';END IF;RETURN NEW;
END $$;
CREATE FUNCTION guard_media_source() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR (to_jsonb(NEW)-ARRAY['location','metadata','artifact_id','updated_at']) IS DISTINCT FROM
 (to_jsonb(OLD)-ARRAY['location','metadata','artifact_id','updated_at']) OR
 (OLD.metadata IS NOT NULL AND NEW.metadata IS DISTINCT FROM OLD.metadata) OR
 (OLD.artifact_id IS NOT NULL AND NEW.artifact_id IS DISTINCT FROM OLD.artifact_id) THEN
 RAISE EXCEPTION 'immutable async media source' USING ERRCODE='23514';END IF;RETURN NEW;
END $$;
CREATE TRIGGER media_work_guard BEFORE UPDATE OR DELETE ON media_work FOR EACH ROW EXECUTE FUNCTION guard_media_work();
CREATE TRIGGER media_source_guard BEFORE UPDATE OR DELETE ON media_sources FOR EACH ROW EXECUTE FUNCTION guard_media_source();
CREATE TRIGGER media_observation_immutable BEFORE UPDATE OR DELETE ON media_observations FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
DO $$DECLARE t text;BEGIN
 FOREACH t IN ARRAY ARRAY['media_work','media_observations','media_sources'] LOOP
 EXECUTE format('CREATE TRIGGER %I BEFORE TRUNCATE ON %I FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change()',t||'_no_truncate',t);
 END LOOP;
END $$;
CREATE FUNCTION check_media_async_integrity() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE key text;w media_work%ROWTYPE;j media_jobs%ROWTYPE;o media_observations%ROWTYPE;
BEGIN
 key=NEW.job_id;SELECT * INTO w FROM media_work WHERE job_id=key;
 IF NOT FOUND THEN RETURN NULL;END IF;
 SELECT * INTO j FROM media_jobs WHERE id=key;
 IF j.dispatched_at IS NULL OR (w.attempt_id,w.binding_hash,w.adapter)IS DISTINCT FROM
 (j.attempt_id,j.binding_hash,j.model_definition->>'adapter') OR NOT EXISTS(
 SELECT 1 FROM media_provider_associations a WHERE a.job_id=key AND a.kind='job' AND
 (a.attempt_id,a.binding_hash,a.provider_scope_hash,a.provider_job_id)=(w.attempt_id,w.binding_hash,w.provider_scope_hash,w.provider_job_id)) THEN
 RAISE EXCEPTION 'async media association mismatch' USING ERRCODE='23514';END IF;
 SELECT * INTO o FROM media_observations WHERE job_id=key;
 IF FOUND AND ((o.attempt_id,o.binding_hash,o.provider_job_id)IS DISTINCT FROM(w.attempt_id,w.binding_hash,w.provider_job_id) OR
 o.manifest IS DISTINCT FROM coalesce((SELECT jsonb_agg(jsonb_build_object('source_id',s.source_id,'kind',s.kind,'mime',s.mime)ORDER BY s.source_id)FROM media_sources s WHERE s.job_id=key),'[]'::jsonb)) THEN
 RAISE EXCEPTION 'async media manifest mismatch' USING ERRCODE='23514';END IF;
 IF EXISTS(SELECT 1 FROM media_sources s LEFT JOIN media_artifacts a ON a.id=s.artifact_id WHERE s.job_id=key AND s.artifact_id IS NOT NULL AND
 (a.job_id IS DISTINCT FROM key OR a.attempt_id IS DISTINCT FROM w.attempt_id OR a.storage_key IS DISTINCT FROM s.storage_key OR
 s.metadata IS NULL OR (a.sha256,a.size,a.mime,a.properties)IS DISTINCT FROM(s.metadata->>'sha256',(s.metadata->>'size')::bigint,s.metadata->>'mime',s.metadata->'properties'))) THEN
 RAISE EXCEPTION 'async media artifact mismatch' USING ERRCODE='23514';END IF;
 IF EXISTS(SELECT 1 FROM media_terminal_evidence e WHERE e.job_id=key)AND
 (w.phase NOT IN('settle','done','manual')OR o.job_id IS NULL OR EXISTS(SELECT 1 FROM media_sources s WHERE s.job_id=key AND s.artifact_id IS NULL))THEN
 RAISE EXCEPTION 'async media delivery not frozen' USING ERRCODE='23514';END IF;
 IF EXISTS(SELECT 1 FROM media_terminal_evidence e WHERE e.job_id=key AND
 (e.terminal_status IS DISTINCT FROM o.state OR e.evidence_hash IS DISTINCT FROM o.response_hash OR
 e.reference_kind<>'object_id' OR e.provider_response_id IS DISTINCT FROM w.provider_job_id OR
 EXISTS(SELECT 1 FROM jsonb_array_elements(j.model_definition->'rate'->'meters') m
 WHERE m->>'basis'='provider_usage_v1' AND (e.quantities->(m->>'unit') IS DISTINCT FROM o.quantities->(m->>'unit')
 OR e.basis_evidence->>'provider_usage_v1' IS DISTINCT FROM o.usage_hash)))) THEN
 RAISE EXCEPTION 'async media terminal observation mismatch' USING ERRCODE='23514';END IF;
 IF w.phase='done' AND (j.status<>'settled' OR NOT EXISTS(SELECT 1 FROM media_receipts r WHERE r.job_id=key)) THEN
 RAISE EXCEPTION 'async media settlement not complete' USING ERRCODE='23514';END IF;
 IF w.phase='settle' AND (o.job_id IS NULL OR o.state<>'completed' OR EXISTS(SELECT 1 FROM media_sources s WHERE s.job_id=key AND s.artifact_id IS NULL)) THEN
 RAISE EXCEPTION 'async media delivery not frozen' USING ERRCODE='23514';END IF;
 RETURN NULL;
END $$;
DO $$DECLARE t text;BEGIN
 FOREACH t IN ARRAY ARRAY['media_work','media_observations','media_sources','media_artifacts','media_terminal_evidence'] LOOP
 EXECUTE format('CREATE CONSTRAINT TRIGGER %I AFTER INSERT OR UPDATE ON %I DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_media_async_integrity()',t||'_async_reconcile',t);
 END LOOP;
END $$;
