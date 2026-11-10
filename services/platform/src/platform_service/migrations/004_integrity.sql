-- History cannot be removed by TRUNCATE (which bypasses row triggers).
CREATE TRIGGER plans_no_truncate BEFORE TRUNCATE ON plans FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER memberships_no_truncate BEFORE TRUNCATE ON memberships FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER journals_no_truncate BEFORE TRUNCATE ON credit_journals FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER entries_no_truncate BEFORE TRUNCATE ON credit_entries FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER holds_no_truncate BEFORE TRUNCATE ON credit_holds FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER lots_no_truncate BEFORE TRUNCATE ON credit_lots FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER reservations_no_truncate BEFORE TRUNCATE ON credit_reservations FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();

ALTER TABLE credit_reservations ADD COLUMN created_xid xid8 NOT NULL DEFAULT pg_current_xact_id();
ALTER TABLE credit_reservations ADD CONSTRAINT terminal_reservation_fields CHECK (
    (status IN ('reserved','dispatched','pending_reconciliation') AND actual IS NULL AND completion_key IS NULL)
    OR (status='settled' AND actual IS NOT NULL AND completion_key IS NOT NULL AND length(btrim(completion_key)) BETWEEN 1 AND 256)
    OR (status='released' AND actual=0 AND completion_key IS NOT NULL AND length(btrim(completion_key)) BETWEEN 1 AND 256)
);
CREATE FUNCTION guard_reservation_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'immutable reservation history' USING ERRCODE='23514'; END IF;
    IF (NEW.id,NEW.billing_account_id,NEW.tenant_id,NEW.user_id,NEW.product_id,NEW.operation,
        NEW.idempotency_key,NEW.body_hash,NEW.rate_version,NEW.amount,NEW.created_at,NEW.created_xid)
       IS DISTINCT FROM
       (OLD.id,OLD.billing_account_id,OLD.tenant_id,OLD.user_id,OLD.product_id,OLD.operation,
        OLD.idempotency_key,OLD.body_hash,OLD.rate_version,OLD.amount,OLD.created_at,OLD.created_xid)
       OR OLD.status IN ('settled','released') THEN
        RAISE EXCEPTION 'immutable reservation binding or completion' USING ERRCODE='23514';
    END IF;
    IF NEW.status<>OLD.status AND NOT (
        (OLD.status='reserved' AND NEW.status IN ('dispatched','released')) OR
        (OLD.status='dispatched' AND NEW.status IN ('pending_reconciliation','settled')) OR
        (OLD.status='pending_reconciliation' AND NEW.status='settled')
    ) THEN RAISE EXCEPTION 'invalid reservation transition' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER reservation_guard BEFORE UPDATE OR DELETE ON credit_reservations
FOR EACH ROW EXECUTE FUNCTION guard_reservation_mutation();
CREATE FUNCTION guard_hold_append() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM credit_reservations WHERE id=NEW.reservation_id
        AND created_xid=pg_current_xact_id() AND status='reserved') THEN
        RAISE EXCEPTION 'closed hold allocation' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER hold_append_guard BEFORE INSERT ON credit_holds FOR EACH ROW EXECUTE FUNCTION guard_hold_append();

CREATE FUNCTION check_reservation_reconciliation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE account text; reservation record; held_total numeric; journal_count bigint;
        amount_held numeric; amount_spent numeric; amount_returned numeric; amount_available numeric;
BEGIN
    IF TG_OP='DELETE' THEN account:=OLD.billing_account_id; ELSE account:=NEW.billing_account_id; END IF;
    PERFORM 1 FROM billing_accounts WHERE id=account FOR UPDATE;
    FOR reservation IN SELECT * FROM credit_reservations WHERE billing_account_id=account LOOP
        SELECT coalesce(sum(amount::numeric),0) INTO held_total FROM credit_holds WHERE reservation_id=reservation.id;
        IF held_total<>reservation.amount THEN
            RAISE EXCEPTION 'reservation allocation divergence' USING ERRCODE='23514';
        END IF;
        SELECT count(*) INTO journal_count FROM credit_journals
            WHERE billing_account_id=account AND kind='reserve' AND source=reservation.id;
        SELECT coalesce(sum(e.delta::numeric) FILTER(WHERE e.bucket='held'),0),
               coalesce(sum(e.delta::numeric) FILTER(WHERE e.bucket='available'),0)
        INTO amount_held,amount_available FROM credit_entries e JOIN credit_journals j ON j.id=e.journal_id
        WHERE j.billing_account_id=account AND j.kind='reserve' AND j.source=reservation.id;
        IF journal_count<>1 OR amount_held<>reservation.amount OR amount_available<>-reservation.amount THEN
            RAISE EXCEPTION 'reservation journal divergence' USING ERRCODE='23514';
        END IF;
        SELECT count(*) INTO journal_count FROM credit_journals
            WHERE billing_account_id=account AND kind IN ('settle','release') AND source=reservation.id;
        IF reservation.status IN ('reserved','dispatched','pending_reconciliation') THEN
            IF journal_count<>0 THEN RAISE EXCEPTION 'active reservation has terminal journal' USING ERRCODE='23514'; END IF;
        ELSE
            IF journal_count<>1 OR NOT EXISTS (SELECT 1 FROM credit_journals
                WHERE billing_account_id=account AND source=reservation.id
                AND kind=CASE WHEN reservation.status='settled' THEN 'settle' ELSE 'release' END) THEN
                RAISE EXCEPTION 'missing terminal journal' USING ERRCODE='23514';
            END IF;
            SELECT coalesce(sum(e.delta::numeric) FILTER(WHERE e.bucket='held'),0),
                   coalesce(sum(e.delta::numeric) FILTER(WHERE e.bucket='spent'),0),
                   coalesce(sum(e.delta::numeric) FILTER(WHERE e.bucket IN ('available','expired')),0)
            INTO amount_held,amount_spent,amount_returned FROM credit_entries e JOIN credit_journals j ON j.id=e.journal_id
            WHERE j.billing_account_id=account AND j.kind IN ('settle','release') AND j.source=reservation.id;
            IF amount_held<>-reservation.amount OR amount_spent<>reservation.actual
               OR amount_returned<>reservation.amount-reservation.actual THEN
                RAISE EXCEPTION 'terminal journal amount divergence' USING ERRCODE='23514';
            END IF;
        END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM credit_lots l WHERE l.billing_account_id=account AND l.held<>(
        SELECT coalesce(sum(h.amount::numeric),0) FROM credit_holds h JOIN credit_reservations r ON r.id=h.reservation_id
        WHERE h.lot_id=l.id AND r.status IN ('reserved','dispatched','pending_reconciliation')
    )) THEN RAISE EXCEPTION 'lot active holds divergence' USING ERRCODE='23514'; END IF;
    IF EXISTS (SELECT 1 FROM credit_journals j WHERE j.billing_account_id=account AND j.kind IN ('reserve','settle','release')
        AND NOT EXISTS (SELECT 1 FROM credit_reservations r WHERE r.id=j.source AND r.billing_account_id=account)) THEN
        RAISE EXCEPTION 'orphan reservation journal' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER reservations_reconcile AFTER INSERT OR UPDATE OR DELETE ON credit_reservations
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_reservation_reconciliation();
CREATE CONSTRAINT TRIGGER holds_reconcile AFTER INSERT OR UPDATE OR DELETE ON credit_holds
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_reservation_reconciliation();
CREATE CONSTRAINT TRIGGER lots_holds_reconcile AFTER INSERT OR UPDATE OR DELETE ON credit_lots
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_reservation_reconciliation();
CREATE CONSTRAINT TRIGGER journal_reservations_reconcile AFTER INSERT ON credit_journals
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_reservation_reconciliation();
CREATE CONSTRAINT TRIGGER entry_reservations_reconcile AFTER INSERT ON credit_entries
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_reservation_reconciliation();
