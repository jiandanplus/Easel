-- Owner-approved allowance while payments stay off (decision 0010). Fixed amount,
-- never a payment, no HTTP surface; runtime and Console have no privileges here.
CREATE TABLE owner_grants (
    id text PRIMARY KEY,
    billing_account_id text NOT NULL REFERENCES billing_accounts(id),
    membership_id text NOT NULL UNIQUE,
    lot_id text NOT NULL UNIQUE,
    operator_id text NOT NULL CHECK(length(btrim(operator_id)) BETWEEN 1 AND 256),
    issuance_key text NOT NULL UNIQUE CHECK(length(btrim(issuance_key)) BETWEEN 1 AND 256),
    reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 256),
    approval_reference text NOT NULL CHECK(length(btrim(approval_reference)) BETWEEN 1 AND 256),
    source text NOT NULL UNIQUE CHECK(source ~ '^owner-allowance:[a-f0-9]{64}$'),
    starts_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL CHECK(expires_at=starts_at + interval '365 days'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY(membership_id,billing_account_id) REFERENCES memberships(id,billing_account_id),
    FOREIGN KEY(lot_id,billing_account_id) REFERENCES credit_lots(id,billing_account_id)
);
CREATE TRIGGER owner_grants_immutable BEFORE UPDATE OR DELETE ON owner_grants
FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER owner_grants_no_truncate BEFORE TRUNCATE ON owner_grants
FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();

CREATE FUNCTION check_owner_grant() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE g record; mid text; n bigint; issued numeric; available numeric;
BEGIN
    IF TG_TABLE_NAME='memberships' THEN mid:=NEW.id; ELSE mid:=NEW.membership_id; END IF;
    SELECT * INTO g FROM owner_grants WHERE membership_id=mid;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'missing owner grant audit' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM memberships m JOIN plans p ON p.id=m.plan_id
        JOIN credit_lots l ON l.membership_id=m.id
        WHERE m.id=g.membership_id AND m.billing_account_id=g.billing_account_id
        AND m.plan_id='easel-owner-allowance-v1' AND m.operator_id=g.operator_id AND m.issuance_key=g.source
        AND m.starts_at=g.starts_at AND m.expires_at=g.expires_at
        AND p.definition='{"id":"easel-owner-allowance-v1","name":"所有者额度，非付款","version":"1","included_products":["easel"],"features":["text","image"],"credits":"100000000","days":365,"concurrency":1,"test_only":false}'::jsonb
        AND l.id=g.lot_id AND l.billing_account_id=g.billing_account_id AND NOT l.test_only
        AND l.issued=100000000 AND l.expires_at=g.expires_at) THEN
        RAISE EXCEPTION 'owner grant binding divergence' USING ERRCODE='23514';
    END IF;
    SELECT count(*),coalesce(sum(e.delta) FILTER(WHERE e.bucket='issuance'),0),
        coalesce(sum(e.delta) FILTER(WHERE e.bucket='available'),0)
        INTO n,issued,available FROM credit_journals j JOIN credit_entries e ON e.journal_id=j.id
        WHERE j.billing_account_id=g.billing_account_id AND j.kind='issue' AND j.source=g.membership_id AND j.operator_id=g.operator_id;
    IF n<>2 OR issued<>-100000000 OR available<>100000000 THEN
        RAISE EXCEPTION 'owner issue journal divergence' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER owner_grants_consistent AFTER INSERT ON owner_grants
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_owner_grant();
-- Only this plan fires it: runtime has no access to owner_grants and must keep issuing
-- other memberships, and a forged owner membership from runtime fails here. credit_lots
-- needs no trigger of its own: membership_id is unique and this check covers the lot.
CREATE CONSTRAINT TRIGGER owner_membership_audited AFTER INSERT ON memberships
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.plan_id='easel-owner-allowance-v1')
EXECUTE FUNCTION check_owner_grant();
