-- One private blob registry owns staging, quota and recoverable physical deletion.
CREATE TABLE media_storage_policy (
 id integer PRIMARY KEY CHECK(id=1),
 owner_max_bytes bigint NOT NULL DEFAULT 268435456 CHECK(owner_max_bytes BETWEEN 1 AND 1099511627776),
 owner_max_files integer NOT NULL DEFAULT 128 CHECK(owner_max_files BETWEEN 1 AND 1000000),
 global_max_bytes bigint NOT NULL DEFAULT 2147483648 CHECK(global_max_bytes BETWEEN 1 AND 1099511627776),
 global_max_files integer NOT NULL DEFAULT 4096 CHECK(global_max_files BETWEEN 1 AND 1000000),
 ttl_seconds integer NOT NULL DEFAULT 86400 CHECK(ttl_seconds BETWEEN 1 AND 86400)
);
INSERT INTO media_storage_policy(id) VALUES(1);
CREATE TABLE media_storage_budgets (
 job_id text PRIMARY KEY REFERENCES media_jobs(id) DEFERRABLE INITIALLY DEFERRED,
 scope jsonb NOT NULL,user_id text NOT NULL,tenant_id text NOT NULL,billing_account_id text NOT NULL,
 max_bytes bigint NOT NULL CHECK(max_bytes BETWEEN 1 AND 67108864),max_files integer NOT NULL CHECK(max_files BETWEEN 1 AND 32),
 remaining_bytes bigint NOT NULL CHECK(remaining_bytes>=0 AND remaining_bytes<=max_bytes),
 remaining_files integer NOT NULL CHECK(remaining_files>=0 AND remaining_files<=max_files),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),released_at timestamptz
);
CREATE TABLE media_blobs (
 storage_key text PRIMARY KEY CHECK(storage_key ~ '^([a-f0-9]{32}|[a-f0-9]{64})$'),
 kind text NOT NULL CHECK(kind IN ('asset','artifact')),scope jsonb NOT NULL,
 user_id text NOT NULL,tenant_id text NOT NULL,billing_account_id text NOT NULL,
 invocation_id text NOT NULL REFERENCES media_invocations(id),job_id text REFERENCES media_jobs(id),attempt_id text,
 sha256 text NOT NULL CHECK(sha256 ~ '^[a-f0-9]{64}$'),size bigint NOT NULL CHECK(size BETWEEN 1 AND 67108864),mime text NOT NULL,properties jsonb NOT NULL,
 state text NOT NULL CHECK(state IN ('staged','attached','gc_pending','deleted')),
 attached_id text,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),expires_at timestamptz NOT NULL,
 purged_at timestamptz,deleted_at timestamptz,gc_token text,gc_lease_until timestamptz,
 CHECK((kind='asset' AND job_id IS NULL AND attempt_id IS NULL) OR (kind='artifact' AND job_id IS NOT NULL AND attempt_id IS NOT NULL)),
 CHECK(state<>'attached' OR attached_id IS NOT NULL),CHECK((state IN ('gc_pending','deleted'))=(purged_at IS NOT NULL)),
 CHECK((state='deleted')=(deleted_at IS NOT NULL)),
 FOREIGN KEY(billing_account_id,tenant_id) REFERENCES billing_accounts(id,tenant_id)
);
CREATE INDEX media_blobs_owner_idx ON media_blobs(user_id,tenant_id,billing_account_id) WHERE state<>'deleted';
CREATE INDEX media_blobs_gc_idx ON media_blobs(expires_at,storage_key) WHERE state IN ('staged','attached','gc_pending');
CREATE INDEX media_storage_budget_owner_idx ON media_storage_budgets(user_id,tenant_id,billing_account_id) WHERE released_at IS NULL;
INSERT INTO media_blobs(storage_key,kind,scope,user_id,tenant_id,billing_account_id,invocation_id,sha256,size,mime,properties,state,attached_id,expires_at,purged_at)
 SELECT storage_key,'asset',scope,user_id,tenant_id,billing_account_id,invocation_id,sha256,size,mime,properties,
 CASE WHEN purged_at IS NULL THEN 'attached' ELSE 'gc_pending' END,id,expires_at,purged_at FROM media_assets;
INSERT INTO media_blobs(storage_key,kind,scope,user_id,tenant_id,billing_account_id,invocation_id,job_id,attempt_id,sha256,size,mime,properties,state,attached_id,expires_at,purged_at)
 SELECT storage_key,'artifact',scope,user_id,tenant_id,billing_account_id,invocation_id,job_id,attempt_id,sha256,size,mime,properties,
 CASE WHEN purged_at IS NULL THEN 'attached' ELSE 'gc_pending' END,id,expires_at,purged_at FROM media_artifacts;

-- Existing pre-015 jobs retain their full storage promise. Consumed output
-- includes all staged/attached/deleted registry history; GC never recreates it.
INSERT INTO media_storage_budgets(job_id,scope,user_id,tenant_id,billing_account_id,max_bytes,max_files,remaining_bytes,remaining_files,released_at)
 SELECT j.id,j.scope,j.user_id,j.tenant_id,j.billing_account_id,(j.model_definition#>>'{limits,max_artifact_bytes}')::bigint,
 CASE WHEN j.capability LIKE 'image.%' THEN coalesce((j.request#>>'{parameters,n}')::integer,1) ELSE 32 END,
 CASE WHEN e.job_id IS NOT NULL OR j.status='released' THEN 0 ELSE (j.model_definition#>>'{limits,max_artifact_bytes}')::bigint-coalesce(b.bytes,0) END,
 CASE WHEN e.job_id IS NOT NULL OR j.status='released' THEN 0 ELSE (CASE WHEN j.capability LIKE 'image.%' THEN coalesce((j.request#>>'{parameters,n}')::integer,1) ELSE 32 END)-coalesce(b.files,0) END,
 CASE WHEN e.job_id IS NOT NULL OR j.status='released' THEN clock_timestamp() ELSE NULL END
 FROM media_jobs j LEFT JOIN media_terminal_evidence e ON e.job_id=j.id
 LEFT JOIN (SELECT job_id,sum(size)::bigint bytes,count(*)::integer files FROM media_blobs WHERE kind='artifact' GROUP BY job_id) b ON b.job_id=j.id;

CREATE FUNCTION media_storage_pinned(blob media_blobs) RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT EXISTS(SELECT 1 FROM media_jobs j WHERE NOT (j.status='released' AND j.dispatched_at IS NULL AND j.actual=0)
   AND NOT EXISTS(SELECT 1 FROM media_terminal_evidence e WHERE e.job_id=j.id AND e.attempt_id=j.attempt_id)
   AND ((blob.kind='artifact' AND j.id=blob.job_id) OR
    (blob.kind='asset' AND blob.attached_id IS NOT NULL AND j.invocation_id=blob.invocation_id AND j.request->'assets' ? blob.attached_id)))
$$;
CREATE FUNCTION media_storage_quota() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE policy media_storage_policy%ROWTYPE; used_bytes numeric;used_files bigint;owner_bytes numeric;owner_files bigint;added_bytes bigint;added_files integer;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('media-storage-v1',0));
 SELECT * INTO STRICT policy FROM media_storage_policy WHERE id=1;
 SELECT coalesce(sum(bytes),0),coalesce(sum(files),0),coalesce(sum(bytes) FILTER(WHERE user_id=NEW.user_id AND tenant_id=NEW.tenant_id AND billing_account_id=NEW.billing_account_id),0),coalesce(sum(files) FILTER(WHERE user_id=NEW.user_id AND tenant_id=NEW.tenant_id AND billing_account_id=NEW.billing_account_id),0)
 INTO used_bytes,used_files,owner_bytes,owner_files FROM (
  SELECT user_id,tenant_id,billing_account_id,size::numeric bytes,1::bigint files FROM media_blobs WHERE state<>'deleted'
  UNION ALL SELECT user_id,tenant_id,billing_account_id,remaining_bytes::numeric,remaining_files::bigint FROM media_storage_budgets WHERE released_at IS NULL
 ) footprint;
 IF TG_TABLE_NAME='media_blobs' THEN added_bytes=NEW.size;added_files=1;ELSE added_bytes=NEW.remaining_bytes;added_files=NEW.remaining_files;END IF;
 IF used_bytes+added_bytes>policy.global_max_bytes OR used_files+added_files>policy.global_max_files OR owner_bytes+added_bytes>policy.owner_max_bytes OR owner_files+added_files>policy.owner_max_files THEN
  RAISE EXCEPTION 'media storage quota exceeded' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER media_storage_blob_quota BEFORE INSERT ON media_blobs FOR EACH ROW EXECUTE FUNCTION media_storage_quota();
CREATE TRIGGER media_storage_budget_quota BEFORE INSERT ON media_storage_budgets FOR EACH ROW EXECUTE FUNCTION media_storage_quota();

CREATE FUNCTION guard_media_storage_budget() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR (to_jsonb(NEW)-ARRAY['remaining_bytes','remaining_files','released_at']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['remaining_bytes','remaining_files','released_at']) OR OLD.released_at IS NOT NULL OR NEW.remaining_bytes>OLD.remaining_bytes OR NEW.remaining_files>OLD.remaining_files OR (NEW.released_at IS NOT NULL AND (NEW.remaining_bytes<>0 OR NEW.remaining_files<>0)) THEN
  RAISE EXCEPTION 'immutable media storage budget' USING ERRCODE='23514';END IF;
 IF NEW.released_at IS NOT NULL AND NOT EXISTS(SELECT 1 FROM media_jobs j WHERE j.id=NEW.job_id AND
    ((j.status='released' AND j.dispatched_at IS NULL AND j.actual=0) OR EXISTS(SELECT 1 FROM media_terminal_evidence e WHERE e.job_id=j.id AND e.attempt_id=j.attempt_id))) THEN
  RAISE EXCEPTION 'active media storage budget' USING ERRCODE='23514';END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER media_storage_budget_guard BEFORE UPDATE OR DELETE ON media_storage_budgets FOR EACH ROW EXECUTE FUNCTION guard_media_storage_budget();
CREATE FUNCTION guard_media_storage_blob() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR (to_jsonb(NEW)-ARRAY['state','attached_id','expires_at','purged_at','deleted_at','gc_token','gc_lease_until']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['state','attached_id','expires_at','purged_at','deleted_at','gc_token','gc_lease_until']) OR OLD.state='deleted' THEN
  RAISE EXCEPTION 'immutable media storage blob' USING ERRCODE='23514';END IF;
 IF NEW.state=OLD.state AND NEW.attached_id IS NOT DISTINCT FROM OLD.attached_id AND NEW.expires_at=OLD.expires_at AND NEW.purged_at IS NOT DISTINCT FROM OLD.purged_at AND NEW.deleted_at IS NOT DISTINCT FROM OLD.deleted_at THEN RETURN NEW;END IF;
 IF OLD.state='staged' AND NEW.state='attached' AND NEW.attached_id IS NOT NULL AND NEW.purged_at IS NULL THEN RETURN NEW;END IF;
 IF OLD.state IN ('staged','attached') AND NEW.state='gc_pending' AND NEW.attached_id IS NOT DISTINCT FROM OLD.attached_id AND NEW.expires_at=OLD.expires_at AND OLD.expires_at<=clock_timestamp() AND NOT media_storage_pinned(OLD) THEN RETURN NEW;END IF;
 IF OLD.state='gc_pending' AND NEW.state='deleted' AND NEW.attached_id IS NOT DISTINCT FROM OLD.attached_id AND NEW.expires_at=OLD.expires_at AND NEW.purged_at=OLD.purged_at AND NEW.deleted_at IS NOT NULL THEN RETURN NEW;END IF;
 RAISE EXCEPTION 'invalid media storage transition' USING ERRCODE='23514';
END $$;
CREATE TRIGGER media_storage_blob_guard BEFORE UPDATE OR DELETE ON media_blobs FOR EACH ROW EXECUTE FUNCTION guard_media_storage_blob();

-- Deferred checks preserve the deliberate reserve -> stage -> attach transaction
-- ordering while rejecting a committed reduction without matching allocations.
CREATE FUNCTION check_media_storage_integrity() RETURNS trigger LANGUAGE plpgsql AS $$
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
  terminal=(j.status='released' AND j.dispatched_at IS NULL AND j.actual=0) OR EXISTS(SELECT 1 FROM media_terminal_evidence e WHERE e.job_id=job_key AND e.attempt_id=j.attempt_id);
  IF allocated_bytes>budget.max_bytes OR allocated_files>budget.max_files OR
    (budget.released_at IS NULL AND (terminal OR budget.remaining_bytes+allocated_bytes<>budget.max_bytes OR budget.remaining_files+allocated_files<>budget.max_files)) OR
    (budget.released_at IS NOT NULL AND (NOT terminal OR budget.remaining_bytes<>0 OR budget.remaining_files<>0)) THEN
   RAISE EXCEPTION 'media storage budget reconciliation failed' USING ERRCODE='23514';END IF;
 END IF;
 RETURN NULL;
END $$;
DO $$ DECLARE t text;BEGIN
 FOREACH t IN ARRAY ARRAY['media_blobs','media_storage_budgets','media_jobs','media_assets','media_artifacts','media_terminal_evidence'] LOOP
  EXECUTE format('CREATE CONSTRAINT TRIGGER %I AFTER INSERT OR UPDATE ON %I DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_media_storage_integrity()',t||'_storage_reconcile',t);
 END LOOP;
END $$;
