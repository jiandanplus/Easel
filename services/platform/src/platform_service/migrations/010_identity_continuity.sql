-- Owner-only same-Logto continuity proof. Existing identity and financial history is unchanged.
CREATE TABLE identity_restore_proofs (
 id text PRIMARY KEY CHECK(id ~ '^[a-f0-9]{32}$'),
 policy_sha256 text NOT NULL CHECK(policy_sha256 ~ '^[a-f0-9]{64}$'),
 evidence jsonb NOT NULL CHECK(jsonb_typeof(evidence)='object'),
 candidates jsonb NOT NULL CHECK(jsonb_typeof(candidates)='array' AND jsonb_array_length(candidates) BETWEEN 1 AND 10000),
 candidate_sha256 text NOT NULL CHECK(candidate_sha256 ~ '^[a-f0-9]{64}$'),
 operator_id text NOT NULL CHECK(length(btrim(operator_id)) BETWEEN 1 AND 255),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 expires_at timestamptz NOT NULL,
 created_xid bigint NOT NULL DEFAULT txid_current(),
 target_issuer text GENERATED ALWAYS AS (evidence->>'target_issuer') STORED NOT NULL,
 CHECK(expires_at=created_at+interval '1 hour')
);
-- Indexed immutable membership is materialized by proof insertion, never by callers.
CREATE TABLE identity_restore_candidates (
 proof_id text NOT NULL REFERENCES identity_restore_proofs(id),
 kind text NOT NULL CHECK(kind IN ('product','console')),
 subject text NOT NULL CHECK(length(subject) BETWEEN 1 AND 255),
 candidate jsonb NOT NULL CHECK(jsonb_typeof(candidate)='object'),
 PRIMARY KEY(proof_id,kind,subject),
 CHECK(candidate->>'kind'=kind AND candidate->>'subject'=subject)
);
CREATE TABLE identity_migrations (
 id text PRIMARY KEY CHECK(id ~ '^[a-f0-9]{32}$'),
 proof_id text NOT NULL UNIQUE REFERENCES identity_restore_proofs(id),
 environment text NOT NULL,
 key_hash text NOT NULL CHECK(key_hash ~ '^[a-f0-9]{64}$'),
 request_hash text NOT NULL CHECK(request_hash ~ '^[a-f0-9]{64}$'),
 operator_id text NOT NULL CHECK(length(btrim(operator_id)) BETWEEN 1 AND 255),
 reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 1000),
 result_sha256 text NOT NULL CHECK(result_sha256 ~ '^[a-f0-9]{64}$'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 created_xid bigint NOT NULL DEFAULT txid_current(),
 UNIQUE(environment,key_hash)
);
CREATE TABLE identity_migration_entries (
 id text PRIMARY KEY CHECK(id ~ '^[a-f0-9]{32}$'),
 migration_id text NOT NULL REFERENCES identity_migrations(id),
 kind text NOT NULL CHECK(kind IN ('product','console')),
 subject text NOT NULL CHECK(length(subject) BETWEEN 1 AND 255),
 candidate jsonb NOT NULL CHECK(jsonb_typeof(candidate)='object'),
 UNIQUE(migration_id,kind,subject),
 CHECK(candidate->>'kind'=kind AND candidate->>'subject'=subject)
);
CREATE TABLE console_identity_aliases (
 issuer text NOT NULL CHECK(length(issuer) BETWEEN 1 AND 2048),
 subject text NOT NULL CHECK(length(subject) BETWEEN 1 AND 255),
 admin_id text NOT NULL REFERENCES console_admins(id),
 migration_entry_id text NOT NULL UNIQUE REFERENCES identity_migration_entries(id) DEFERRABLE INITIALLY DEFERRED,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(issuer,subject)
);

-- A fixed number of schema/kind-separated advisory stripes bounds shared lock
-- memory. Equal identities always use the same stripe; collisions only serialize.
-- Every function is SECURITY INVOKER and confers no mutation authority.
CREATE FUNCTION identity_lock_stripe(identity_issuer text,identity_subject text) RETURNS integer
 LANGUAGE sql IMMUTABLE STRICT AS $$
 SELECT hashtext(jsonb_build_array(identity_issuer,identity_subject)::text) & 1023
$$;
CREATE FUNCTION lock_identity_stripe(identity_kind text,stripe integer) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
 IF identity_kind NOT IN ('product','console') OR stripe NOT BETWEEN 0 AND 1023 THEN
   RAISE EXCEPTION 'invalid identity lock' USING ERRCODE='23514';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtext('easel-identity:' || current_schema() || ':' || identity_kind),stripe);
END $$;
CREATE FUNCTION lock_console_identity(identity_issuer text,identity_subject text) RETURNS void LANGUAGE sql AS $$
 SELECT lock_identity_stripe('console',identity_lock_stripe(identity_issuer,identity_subject))
$$;
CREATE FUNCTION lock_product_identity(identity_issuer text,identity_subject text) RETURNS void LANGUAGE sql AS $$
 SELECT lock_identity_stripe('product',identity_lock_stripe(identity_issuer,identity_subject))
$$;
CREATE FUNCTION check_console_identity_namespace() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 PERFORM lock_console_identity(NEW.issuer,NEW.subject);
 IF TG_TABLE_NAME='console_admins' THEN
   IF EXISTS(SELECT 1 FROM console_identity_aliases WHERE issuer=NEW.issuer AND subject=NEW.subject) THEN
     RAISE EXCEPTION 'console identity conflict' USING ERRCODE='23505';
   END IF;
 ELSE
   IF EXISTS(SELECT 1 FROM console_admins WHERE issuer=NEW.issuer AND subject=NEW.subject) THEN
     RAISE EXCEPTION 'console identity conflict' USING ERRCODE='23505';
   END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER console_canonical_namespace BEFORE INSERT OR UPDATE OF issuer,subject ON console_admins
 FOR EACH ROW EXECUTE FUNCTION check_console_identity_namespace();
CREATE TRIGGER console_alias_namespace BEFORE INSERT ON console_identity_aliases
 FOR EACH ROW EXECUTE FUNCTION check_console_identity_namespace();

-- These SECURITY INVOKER triggers confer no write authority. The normalizer is
-- the sole non-malicious insertion path: direct INSERT has trigger depth one.
-- No TEMP object or caller-controlled GUC can mark validation as complete.
CREATE FUNCTION guard_restore_candidate_insert() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF pg_trigger_depth()<>2 OR NOT EXISTS(
   SELECT 1 FROM identity_restore_proofs WHERE id=NEW.proof_id AND created_xid=txid_current()) THEN
   RAISE EXCEPTION 'restore candidates are sealed' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER identity_candidate_member BEFORE INSERT ON identity_restore_candidates
 FOR EACH ROW EXECUTE FUNCTION guard_restore_candidate_insert();
CREATE FUNCTION materialize_restore_candidates() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 INSERT INTO identity_restore_candidates(proof_id,kind,subject,candidate)
 SELECT NEW.id,value->>'kind',value->>'subject',value FROM jsonb_array_elements(NEW.candidates);
 RETURN NULL;
END $$;
CREATE TRIGGER identity_proof_candidates AFTER INSERT ON identity_restore_proofs
 FOR EACH ROW EXECUTE FUNCTION materialize_restore_candidates();

-- Constant-size indexed membership checks prevent appends even if a caller has
-- already forced the deferred batch check with SET CONSTRAINTS ALL IMMEDIATE.
CREATE FUNCTION guard_identity_entry_insert() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM identity_migrations m JOIN identity_restore_candidates c ON c.proof_id=m.proof_id
     WHERE m.id=NEW.migration_id AND m.created_xid=txid_current()
       AND c.kind=NEW.kind AND c.subject=NEW.subject AND c.candidate=NEW.candidate) THEN
   RAISE EXCEPTION 'identity migration entry is not a proof member' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER identity_entry_member BEFORE INSERT ON identity_migration_entries
 FOR EACH ROW EXECUTE FUNCTION guard_identity_entry_insert();
CREATE FUNCTION guard_identity_alias_insert() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM identity_migration_entries e
     JOIN identity_migrations m ON m.id=e.migration_id
     JOIN identity_restore_proofs p ON p.id=m.proof_id
     WHERE e.id=NEW.migration_entry_id AND m.created_xid=txid_current()
       AND e.kind='console' AND e.subject=NEW.subject AND p.target_issuer=NEW.issuer
       AND e.candidate->>'admin_id'=NEW.admin_id) THEN
   RAISE EXCEPTION 'identity alias is not an audit member' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER identity_alias_member BEFORE INSERT ON console_identity_aliases
 FOR EACH ROW EXECUTE FUNCTION guard_identity_alias_insert();

-- Exactly one deferred validation per batch. Indexed set joins replace the old
-- per-entry trigger/full-array loops; normal work is O(N log N), not quadratic.
CREATE FUNCTION check_identity_continuity() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE proof identity_restore_proofs%ROWTYPE;
BEGIN
 SELECT * INTO proof FROM identity_restore_proofs WHERE id=NEW.proof_id;
 IF NEW.created_xid<>txid_current() OR proof.id IS NULL OR
    (SELECT count(*) FROM identity_migration_entries WHERE migration_id=NEW.id)<>jsonb_array_length(proof.candidates) THEN
   RAISE EXCEPTION 'identity migration audit incomplete' USING ERRCODE='23514';
 END IF;
 IF EXISTS(
   WITH expected AS (SELECT kind,subject,candidate FROM identity_restore_candidates WHERE proof_id=proof.id),
        actual AS (SELECT kind,subject,candidate FROM identity_migration_entries WHERE migration_id=NEW.id)
   SELECT 1 FROM expected p FULL JOIN actual e ON (p.kind,p.subject)=(e.kind,e.subject)
   WHERE p.kind IS NULL OR e.kind IS NULL OR p.candidate IS DISTINCT FROM e.candidate
 ) THEN
   RAISE EXCEPTION 'identity migration candidate conflict' USING ERRCODE='23514';
 END IF;
 IF EXISTS(SELECT 1 FROM identity_migration_entries e
   LEFT JOIN external_identities x ON e.kind='product' AND x.issuer=proof.target_issuer
       AND x.subject=e.subject AND x.user_id=e.candidate->>'user_id'
   LEFT JOIN console_identity_aliases a ON e.kind='console' AND a.issuer=proof.target_issuer
       AND a.subject=e.subject AND a.admin_id=e.candidate->>'admin_id' AND a.migration_entry_id=e.id
   WHERE e.migration_id=NEW.id AND ((e.kind='product' AND x.user_id IS NULL) OR (e.kind='console' AND a.admin_id IS NULL))
 ) THEN
   RAISE EXCEPTION 'identity migration target missing' USING ERRCODE='23514';
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER identity_migration_complete AFTER INSERT ON identity_migrations
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_identity_continuity();

CREATE TRIGGER identity_candidates_immutable BEFORE UPDATE OR DELETE ON identity_restore_candidates FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER identity_candidates_no_truncate BEFORE TRUNCATE ON identity_restore_candidates FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER identity_proofs_immutable BEFORE UPDATE OR DELETE ON identity_restore_proofs FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER identity_proofs_no_truncate BEFORE TRUNCATE ON identity_restore_proofs FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER identity_migrations_immutable BEFORE UPDATE OR DELETE ON identity_migrations FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER identity_migrations_no_truncate BEFORE TRUNCATE ON identity_migrations FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER identity_entries_immutable BEFORE UPDATE OR DELETE ON identity_migration_entries FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER identity_entries_no_truncate BEFORE TRUNCATE ON identity_migration_entries FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER console_aliases_immutable BEFORE UPDATE OR DELETE ON console_identity_aliases FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER console_aliases_no_truncate BEFORE TRUNCATE ON console_identity_aliases FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();
