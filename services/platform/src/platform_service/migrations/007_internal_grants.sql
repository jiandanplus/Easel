-- Existing sessions remain usable, but only newly proven OIDC sessions can receive
-- the owner-operated, fixed internal allowance. Never backfill identity provenance.
ALTER TABLE identity_sessions ADD COLUMN source_transaction_id text REFERENCES identity_transactions(id);
CREATE UNIQUE INDEX identity_sessions_source_transaction ON identity_sessions(source_transaction_id) WHERE source_transaction_id IS NOT NULL;
CREATE TABLE internal_grants (
    id text PRIMARY KEY,
    environment_id text NOT NULL CHECK(environment_id ~ '^[A-Za-z0-9_-]{8,80}$'),
    billing_account_id text NOT NULL,
    user_id text NOT NULL REFERENCES users(id),
    tenant_id text NOT NULL,
    session_id text NOT NULL REFERENCES identity_sessions(id),
    issuer text NOT NULL,
    source_transaction_id text NOT NULL REFERENCES identity_transactions(id),
    membership_id text NOT NULL UNIQUE,
    lot_id text NOT NULL UNIQUE,
    operator_id text NOT NULL CHECK(length(btrim(operator_id)) BETWEEN 1 AND 256),
    issuance_key text NOT NULL CHECK(length(btrim(issuance_key)) BETWEEN 1 AND 256),
    reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 256),
    policy_sha256 text NOT NULL CHECK(policy_sha256 ~ '^[a-f0-9]{64}$'),
    source text NOT NULL UNIQUE CHECK(source ~ '^internal-experience:[a-f0-9]{64}$'),
    starts_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL CHECK(expires_at=starts_at + interval '7 days'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    created_xid xid8 NOT NULL DEFAULT pg_current_xact_id(),
    UNIQUE(environment_id,billing_account_id),
    UNIQUE(environment_id,issuance_key),
    FOREIGN KEY(billing_account_id,tenant_id) REFERENCES billing_accounts(id,tenant_id),
    FOREIGN KEY(tenant_id,user_id) REFERENCES tenant_members(tenant_id,user_id),
    FOREIGN KEY(membership_id,billing_account_id) REFERENCES memberships(id,billing_account_id),
    FOREIGN KEY(lot_id,billing_account_id) REFERENCES credit_lots(id,billing_account_id)
);
CREATE TRIGGER internal_grants_immutable BEFORE UPDATE OR DELETE ON internal_grants
FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER internal_grants_no_truncate BEFORE TRUNCATE ON internal_grants
FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();

CREATE FUNCTION check_internal_grant() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE g record; mid text; n bigint; issued numeric; available numeric;
BEGIN
    IF TG_TABLE_NAME='memberships' THEN
        IF NEW.plan_id <> 'easel-internal-experience-v1' THEN RETURN NULL; END IF;
        mid:=NEW.id;
    ELSIF TG_TABLE_NAME='credit_lots' THEN mid:=NEW.membership_id;
    ELSE mid:=NEW.membership_id;
    END IF;
    SELECT * INTO g FROM internal_grants WHERE membership_id=mid;
    IF NOT FOUND THEN
        IF EXISTS(SELECT 1 FROM memberships WHERE id=mid AND plan_id='easel-internal-experience-v1') THEN
            RAISE EXCEPTION 'missing internal grant audit' USING ERRCODE='23514';
        END IF;
        RETURN NULL;
    END IF;
    IF NOT EXISTS(SELECT 1 FROM memberships m JOIN plans p ON p.id=m.plan_id
        JOIN credit_lots l ON l.membership_id=m.id
        JOIN identity_sessions s ON s.id=g.session_id
        JOIN identity_transactions tx ON tx.id=s.source_transaction_id
        JOIN identity_handoffs h ON h.transaction_id=tx.id
        WHERE m.id=g.membership_id AND m.billing_account_id=g.billing_account_id
        AND m.plan_id='easel-internal-experience-v1' AND m.operator_id=g.operator_id AND m.issuance_key=g.source
        AND m.starts_at=g.starts_at AND m.expires_at=g.expires_at
        AND p.definition='{"id":"easel-internal-experience-v1","name":"内部体验额度，非付款","version":"1","included_products":["easel"],"features":["text"],"credits":"10000000","days":7,"concurrency":1,"test_only":false}'::jsonb
        AND l.id=g.lot_id AND l.billing_account_id=g.billing_account_id AND NOT l.test_only
        AND l.issued=10000000 AND l.expires_at=g.expires_at
        AND (s.user_id,s.tenant_id,s.billing_account_id,s.issuer,s.source_transaction_id)=
            (g.user_id,g.tenant_id,g.billing_account_id,g.issuer,g.source_transaction_id)
        AND s.product_id='easel' AND tx.product_id=s.product_id AND tx.client_id=s.client_id
        AND tx.consumed AND h.consumed
        AND (h.user_id,h.tenant_id,h.billing_account_id,h.issuer,h.subject)=
            (s.user_id,s.tenant_id,s.billing_account_id,s.issuer,s.subject)) THEN
        RAISE EXCEPTION 'internal grant binding divergence' USING ERRCODE='23514';
    END IF;
    SELECT count(*),coalesce(sum(e.delta) FILTER(WHERE e.bucket='issuance'),0),
        coalesce(sum(e.delta) FILTER(WHERE e.bucket='available'),0)
        INTO n,issued,available FROM credit_journals j JOIN credit_entries e ON e.journal_id=j.id
        WHERE j.billing_account_id=g.billing_account_id AND j.kind='issue' AND j.source=g.membership_id AND j.operator_id=g.operator_id;
    IF n<>2 OR issued<>-10000000 OR available<>10000000 THEN
        RAISE EXCEPTION 'internal issue journal divergence' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER internal_grants_consistent AFTER INSERT ON internal_grants
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_internal_grant();
CREATE CONSTRAINT TRIGGER internal_membership_audited AFTER INSERT ON memberships
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_internal_grant();
CREATE CONSTRAINT TRIGGER internal_lot_consistent AFTER INSERT ON credit_lots
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_internal_grant();
