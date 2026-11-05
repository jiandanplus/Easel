CREATE TABLE plans (
    id text PRIMARY KEY,
    name text NOT NULL,
    version text NOT NULL,
    definition jsonb NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(name, version),
    CHECK (definition->>'test_only' = 'true')
);
CREATE TABLE memberships (
    id text PRIMARY KEY,
    billing_account_id text NOT NULL REFERENCES billing_accounts(id),
    plan_id text NOT NULL REFERENCES plans(id),
    operator_id text NOT NULL,
    issuance_key text NOT NULL,
    starts_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(operator_id, issuance_key),
    UNIQUE(id, billing_account_id),
    CHECK (expires_at > starts_at)
);
CREATE TABLE credit_lots (
    id text PRIMARY KEY,
    billing_account_id text NOT NULL REFERENCES billing_accounts(id),
    membership_id text NOT NULL UNIQUE,
    issued bigint NOT NULL CHECK (issued > 0),
    available bigint NOT NULL CHECK (available >= 0),
    held bigint NOT NULL DEFAULT 0 CHECK (held >= 0),
    spent bigint NOT NULL DEFAULT 0 CHECK (spent >= 0),
    expired bigint NOT NULL DEFAULT 0 CHECK (expired >= 0),
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(id, billing_account_id),
    FOREIGN KEY(membership_id,billing_account_id) REFERENCES memberships(id,billing_account_id),
    CHECK (issued::numeric = available::numeric + held + spent + expired)
);
CREATE TABLE credit_reservations (
    id text PRIMARY KEY,
    billing_account_id text NOT NULL,
    tenant_id text NOT NULL,
    user_id text NOT NULL REFERENCES users(id),
    product_id text NOT NULL REFERENCES products(id),
    operation text NOT NULL,
    idempotency_key text NOT NULL,
    body_hash text NOT NULL,
    rate_version text NOT NULL,
    amount bigint NOT NULL CHECK(amount > 0),
    actual bigint CHECK(actual >= 0 AND actual <= amount),
    status text NOT NULL CHECK(status IN ('reserved','dispatched','pending_reconciliation','settled','released')),
    completion_key text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(billing_account_id,product_id,operation,idempotency_key),
    UNIQUE(id,billing_account_id),
    FOREIGN KEY(billing_account_id,tenant_id) REFERENCES billing_accounts(id,tenant_id)
);
CREATE TABLE credit_holds (
    reservation_id text NOT NULL,
    lot_id text NOT NULL,
    billing_account_id text NOT NULL,
    amount bigint NOT NULL CHECK(amount > 0),
    PRIMARY KEY(reservation_id,lot_id),
    FOREIGN KEY(reservation_id,billing_account_id) REFERENCES credit_reservations(id,billing_account_id),
    FOREIGN KEY(lot_id,billing_account_id) REFERENCES credit_lots(id,billing_account_id)
);
CREATE TABLE credit_journals (
    id text PRIMARY KEY,
    billing_account_id text NOT NULL REFERENCES billing_accounts(id),
    kind text NOT NULL CHECK(kind IN ('issue','reserve','settle','release','expire')),
    source text NOT NULL,
    operator_id text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    created_xid xid8 NOT NULL DEFAULT pg_current_xact_id(),
    UNIQUE(id,billing_account_id),
    UNIQUE(billing_account_id,kind,source)
);
CREATE TABLE credit_entries (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    journal_id text NOT NULL,
    billing_account_id text NOT NULL,
    bucket text NOT NULL CHECK(bucket IN ('issuance','available','held','spent','expired')),
    delta bigint NOT NULL CHECK(delta <> 0),
    FOREIGN KEY(journal_id,billing_account_id) REFERENCES credit_journals(id,billing_account_id)
);
CREATE INDEX credit_lots_wallet_expiry ON credit_lots(billing_account_id,expires_at,id);
CREATE INDEX credit_reservations_wallet_status ON credit_reservations(billing_account_id,status);
CREATE FUNCTION deny_immutable_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'immutable ledger or plan history' USING ERRCODE='23514'; END $$;
CREATE TRIGGER plans_immutable BEFORE UPDATE OR DELETE ON plans FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER memberships_immutable BEFORE UPDATE OR DELETE ON memberships FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER journal_immutable BEFORE UPDATE OR DELETE ON credit_journals FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER entries_immutable BEFORE UPDATE OR DELETE ON credit_entries FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER holds_immutable BEFORE UPDATE OR DELETE ON credit_holds FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE FUNCTION guard_entry_append() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM credit_journals WHERE id=NEW.journal_id AND created_xid=pg_current_xact_id()) THEN
        RAISE EXCEPTION 'closed journal' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER entry_insert_guard BEFORE INSERT ON credit_entries FOR EACH ROW EXECUTE FUNCTION guard_entry_append();
CREATE FUNCTION check_balanced_journal() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE target text; total numeric; n bigint;
BEGIN
    IF TG_TABLE_NAME='credit_journals' THEN target := NEW.id; ELSE target := NEW.journal_id; END IF;
    SELECT sum(delta::numeric),count(*) INTO total,n FROM credit_entries WHERE journal_id=target;
    IF n < 2 OR total IS DISTINCT FROM 0::numeric THEN
        RAISE EXCEPTION 'unbalanced journal' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER journal_balanced AFTER INSERT ON credit_journals DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_balanced_journal();
CREATE CONSTRAINT TRIGGER entries_balanced AFTER INSERT ON credit_entries DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_balanced_journal();
-- Counter rows are a materialized view of the immutable journal. Check both sides
-- at commit, after an atomic operation has written all lots and all entries.
CREATE FUNCTION check_wallet_reconciliation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE account text; lots record; entries record;
BEGIN
    IF TG_OP='DELETE' THEN account:=OLD.billing_account_id; ELSE account:=NEW.billing_account_id; END IF;
    PERFORM 1 FROM billing_accounts WHERE id=account FOR UPDATE;
    SELECT coalesce(sum(issued::numeric),0) AS issued,
           coalesce(sum(available::numeric),0) AS available,
           coalesce(sum(held::numeric),0) AS held,
           coalesce(sum(spent::numeric),0) AS spent,
           coalesce(sum(expired::numeric),0) AS expired
    INTO lots FROM credit_lots WHERE billing_account_id=account;
    SELECT -coalesce(sum(delta::numeric) FILTER(WHERE bucket='issuance'),0) AS issued,
           coalesce(sum(delta::numeric) FILTER(WHERE bucket='available'),0) AS available,
           coalesce(sum(delta::numeric) FILTER(WHERE bucket='held'),0) AS held,
           coalesce(sum(delta::numeric) FILTER(WHERE bucket='spent'),0) AS spent,
           coalesce(sum(delta::numeric) FILTER(WHERE bucket='expired'),0) AS expired
    INTO entries FROM credit_entries WHERE billing_account_id=account;
    IF lots IS DISTINCT FROM entries OR lots.issued>9223372036854775807::numeric THEN
        RAISE EXCEPTION 'wallet journal divergence' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER lots_reconcile AFTER INSERT OR UPDATE OR DELETE ON credit_lots
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_wallet_reconciliation();
CREATE CONSTRAINT TRIGGER entries_reconcile AFTER INSERT ON credit_entries
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_wallet_reconciliation();
