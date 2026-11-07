-- Console-only command receipts and append-only operator history.
CREATE TABLE console_operations (
    id text PRIMARY KEY,
    actor_id text NOT NULL REFERENCES console_admins(id),
    key_hash text NOT NULL CHECK(key_hash ~ '^[a-f0-9]{64}$'),
    request_hash text NOT NULL CHECK(request_hash ~ '^[a-f0-9]{64}$'),
    action text NOT NULL CHECK(action IN ('plan.publish','offer.publish','offer.withdraw','model.publish','model.state')),
    target_type text NOT NULL CHECK(target_type IN ('plan','offer','model')),
    target_id text NOT NULL CHECK(length(target_id) BETWEEN 1 AND 255),
    result jsonb NOT NULL CHECK(jsonb_typeof(result)='object'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(actor_id,key_hash),
    UNIQUE(id,actor_id)
);
CREATE TABLE console_audit_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    operation_id text NOT NULL UNIQUE,
    actor_id text NOT NULL REFERENCES console_admins(id),
    action text NOT NULL,
    target_type text NOT NULL,
    target_id text NOT NULL,
    reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 512),
    key_hash text NOT NULL CHECK(key_hash ~ '^[a-f0-9]{64}$'),
    request_hash text NOT NULL CHECK(request_hash ~ '^[a-f0-9]{64}$'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY(operation_id,actor_id) REFERENCES console_operations(id,actor_id)
);
CREATE INDEX console_audit_created ON console_audit_events(created_at DESC,id DESC);
CREATE INDEX console_audit_actor ON console_audit_events(actor_id,created_at DESC,id DESC);
CREATE TRIGGER console_operations_immutable BEFORE UPDATE OR DELETE ON console_operations
FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER console_operations_no_truncate BEFORE TRUNCATE ON console_operations
FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER console_audit_immutable BEFORE UPDATE OR DELETE ON console_audit_events
FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER console_audit_no_truncate BEFORE TRUNCATE ON console_audit_events
FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();
-- Require a matching audit even if a caller bypasses the Python command wrapper.
CREATE FUNCTION check_console_operation_audit() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE oid text; n bigint;
BEGIN
    IF TG_TABLE_NAME='console_operations' THEN oid:=NEW.id; ELSE oid:=NEW.operation_id; END IF;
    SELECT count(*) INTO n FROM console_operations o JOIN console_audit_events a
      ON a.operation_id=o.id AND a.actor_id=o.actor_id AND a.action=o.action
      AND a.target_type=o.target_type AND a.target_id=o.target_id
      AND a.key_hash=o.key_hash AND a.request_hash=o.request_hash WHERE o.id=oid;
    IF n<>1 THEN RAISE EXCEPTION 'console audit divergence' USING ERRCODE='23514'; END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER console_operation_audited AFTER INSERT ON console_operations
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_console_operation_audit();
CREATE CONSTRAINT TRIGGER console_audit_bound AFTER INSERT ON console_audit_events
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_console_operation_audit();
