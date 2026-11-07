-- Additive S2: immutable commercial snapshots, verified facts and compensations.
ALTER TABLE plans DROP CONSTRAINT plans_definition_check;
ALTER TABLE plans ADD CONSTRAINT plans_test_mode CHECK ((jsonb_typeof(definition->'test_only')='boolean') IS TRUE);
ALTER TABLE credit_reservations ADD COLUMN funding_mode text NOT NULL DEFAULT 'test' CHECK(funding_mode IN ('test','live'));
CREATE FUNCTION guard_reservation_funding() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.funding_mode IS DISTINCT FROM OLD.funding_mode THEN RAISE EXCEPTION 'immutable reservation funding mode' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;


CREATE TRIGGER reservation_funding_guard BEFORE UPDATE ON credit_reservations FOR EACH ROW EXECUTE FUNCTION guard_reservation_funding();
ALTER TABLE credit_lots ADD COLUMN refunded bigint NOT NULL DEFAULT 0 CHECK(refunded>=0);
ALTER TABLE credit_lots ADD COLUMN frozen boolean NOT NULL DEFAULT false;
ALTER TABLE credit_lots ADD COLUMN test_only boolean NOT NULL DEFAULT true;
CREATE FUNCTION guard_lot_funding() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.test_only IS DISTINCT FROM OLD.test_only THEN RAISE EXCEPTION 'immutable lot funding mode' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER lot_funding_guard BEFORE UPDATE ON credit_lots FOR EACH ROW EXECUTE FUNCTION guard_lot_funding();
ALTER TABLE credit_lots DROP CONSTRAINT credit_lots_check;
ALTER TABLE credit_lots ADD CONSTRAINT lots_conservation CHECK(issued::numeric=available::numeric+held+spent+expired+refunded);
ALTER TABLE credit_journals DROP CONSTRAINT credit_journals_kind_check;
ALTER TABLE credit_journals ADD CONSTRAINT credit_journals_kind_check CHECK(kind IN ('issue','reserve','settle','release','expire','refund'));
ALTER TABLE credit_entries DROP CONSTRAINT credit_entries_bucket_check;
ALTER TABLE credit_entries ADD CONSTRAINT credit_entries_bucket_check CHECK(bucket IN ('issuance','available','held','spent','expired','refunded'));
CREATE OR REPLACE FUNCTION check_wallet_reconciliation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE account text; lots record; entries record;
BEGIN
 IF TG_OP='DELETE' THEN account:=OLD.billing_account_id; ELSE account:=NEW.billing_account_id; END IF;
 PERFORM 1 FROM billing_accounts WHERE id=account FOR UPDATE;
 SELECT coalesce(sum(issued::numeric),0) issued,coalesce(sum(available::numeric),0) available,
 coalesce(sum(held::numeric),0) held,coalesce(sum(spent::numeric),0) spent,
 coalesce(sum(expired::numeric),0) expired,coalesce(sum(refunded::numeric),0) refunded
 INTO lots FROM credit_lots WHERE billing_account_id=account;
 SELECT -coalesce(sum(delta::numeric) FILTER(WHERE bucket='issuance'),0) issued,
 coalesce(sum(delta::numeric) FILTER(WHERE bucket='available'),0) available,
 coalesce(sum(delta::numeric) FILTER(WHERE bucket='held'),0) held,
 coalesce(sum(delta::numeric) FILTER(WHERE bucket='spent'),0) spent,
 coalesce(sum(delta::numeric) FILTER(WHERE bucket='expired'),0) expired,
 coalesce(sum(delta::numeric) FILTER(WHERE bucket='refunded'),0) refunded
 INTO entries FROM credit_entries WHERE billing_account_id=account;
 IF lots IS DISTINCT FROM entries OR lots.issued>9223372036854775807::numeric THEN
 RAISE EXCEPTION 'wallet journal divergence' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;

CREATE TABLE billing_offers (
 id text PRIMARY KEY, plan_id text NOT NULL REFERENCES plans(id), version text NOT NULL,
 definition jsonb NOT NULL, created_by text NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(plan_id,version)
);
CREATE TABLE billing_offer_withdrawals (
 offer_id text PRIMARY KEY REFERENCES billing_offers(id), reason text NOT NULL, operator_id text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE billing_orders (
 id text PRIMARY KEY CHECK(id ~ '^[a-zA-Z0-9]{32}$'),
 user_id text NOT NULL REFERENCES users(id), product_id text NOT NULL REFERENCES products(id),
 tenant_id text NOT NULL, billing_account_id text NOT NULL,
 offer_id text NOT NULL REFERENCES billing_offers(id), offer jsonb NOT NULL,
 idempotency_key text NOT NULL, body_hash text NOT NULL,
 channel text NOT NULL CHECK(channel IN ('wechat','alipay')), mode text NOT NULL CHECK(mode IN ('test','live')),
 merchant_id text NOT NULL, app_id text NOT NULL,
 amount_minor bigint NOT NULL CHECK(amount_minor>0), currency text NOT NULL CHECK(currency='CNY'),
 status text NOT NULL DEFAULT 'creating' CHECK(status IN ('creating','pending','paid','closed','reconciliation_required','refunded')),
 credit_state text NOT NULL DEFAULT 'not_granted' CHECK(credit_state IN ('not_granted','granted','revoked','manual_review')),
 checkout jsonb, membership_id text UNIQUE, provider_transaction_id text,
 reconciliation_reason text, paid_at timestamptz, expires_at timestamptz NOT NULL,
 revision bigint NOT NULL DEFAULT 0 CHECK(revision>=0),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(user_id,product_id,tenant_id,billing_account_id,idempotency_key),
 UNIQUE(id,billing_account_id),
 FOREIGN KEY(billing_account_id,tenant_id) REFERENCES billing_accounts(id,tenant_id),
 FOREIGN KEY(membership_id,billing_account_id) REFERENCES memberships(id,billing_account_id),
 CHECK((membership_id IS NULL AND paid_at IS NULL) OR (membership_id IS NOT NULL AND paid_at IS NOT NULL)),
 CHECK(status NOT IN ('paid','refunded') OR membership_id IS NOT NULL),
 CHECK(credit_state NOT IN ('granted','revoked') OR membership_id IS NOT NULL)
);
CREATE TABLE payment_events (
 id text PRIMARY KEY, channel text NOT NULL, mode text NOT NULL, merchant_id text NOT NULL,
 event_id text NOT NULL, order_id text NOT NULL REFERENCES billing_orders(id),
 fact jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(channel,mode,merchant_id,event_id)
);
CREATE TABLE payment_orphan_events (
 id text PRIMARY KEY, channel text NOT NULL, mode text NOT NULL, merchant_id text NOT NULL,
 event_id text NOT NULL, order_id text NOT NULL, fingerprint text NOT NULL, fact jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(channel,mode,merchant_id,event_id,fingerprint)
);
CREATE TABLE payment_event_audits (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 event_id text NOT NULL REFERENCES payment_events(id), order_id text NOT NULL REFERENCES billing_orders(id),
 outcome text NOT NULL, digest text NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE payment_transactions (
 channel text NOT NULL, mode text NOT NULL, merchant_id text NOT NULL, provider_transaction_id text NOT NULL,
 order_id text NOT NULL REFERENCES billing_orders(id), event_id text NOT NULL REFERENCES payment_events(id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(channel,mode,merchant_id,provider_transaction_id)
);
CREATE TABLE billing_refunds (
 id text PRIMARY KEY, order_id text NOT NULL REFERENCES billing_orders(id),
 operator_id text NOT NULL, idempotency_key text NOT NULL,
 amount_minor bigint NOT NULL CHECK(amount_minor>0), reason text NOT NULL,
 status text NOT NULL CHECK(status IN ('pending','succeeded','failed','manual_reconciliation')),
 provider_refund_id text, reconciliation_reason text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(operator_id,idempotency_key)
);
-- Historical failures/successes/reviews coexist; only one outbound request may be pending.
CREATE UNIQUE INDEX one_pending_refund ON billing_refunds(order_id) WHERE status='pending';
CREATE TABLE payment_refund_transactions (
 channel text NOT NULL, mode text NOT NULL, merchant_id text NOT NULL, provider_refund_id text NOT NULL,
 order_id text NOT NULL REFERENCES billing_orders(id), refund_id text NOT NULL REFERENCES billing_refunds(id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(channel,mode,merchant_id,provider_refund_id)
);
CREATE TABLE membership_revocations (
 membership_id text PRIMARY KEY REFERENCES memberships(id), reason text NOT NULL,
 order_id text NOT NULL REFERENCES billing_orders(id), created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE billing_outbox (
 id text PRIMARY KEY, kind text NOT NULL CHECK(kind IN ('create_checkout','query_order','close_order','request_refund','query_refund','order_paid','order_refunded')),
 order_id text NOT NULL REFERENCES billing_orders(id), refund_id text REFERENCES billing_refunds(id),
 dedupe_key text NOT NULL UNIQUE, payload jsonb NOT NULL,
 status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','leased','done')),
 attempts integer NOT NULL DEFAULT 0 CHECK(attempts>=0),
 due_at timestamptz NOT NULL DEFAULT clock_timestamp(), lease_token text, lease_until timestamptz,
 last_error text, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK((status='leased' AND lease_token IS NOT NULL AND lease_until IS NOT NULL) OR status<>'leased')
);
CREATE INDEX billing_outbox_due ON billing_outbox(status,due_at,lease_until);
CREATE INDEX billing_orders_owner ON billing_orders(user_id,product_id,tenant_id,billing_account_id,created_at);

CREATE FUNCTION guard_billing_workflow() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE allowed text[];
BEGIN
 IF TG_OP IN ('DELETE','TRUNCATE') THEN RAISE EXCEPTION 'immutable billing history' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='billing_orders' THEN
 NEW.revision:=OLD.revision+1;
 allowed:=ARRAY['status','credit_state','checkout','membership_id','provider_transaction_id','reconciliation_reason','paid_at','updated_at','revision'];
 IF OLD.membership_id IS NOT NULL AND (NEW.membership_id,NEW.paid_at,NEW.provider_transaction_id) IS DISTINCT FROM (OLD.membership_id,OLD.paid_at,OLD.provider_transaction_id) THEN
 RAISE EXCEPTION 'immutable payment grant' USING ERRCODE='23514'; END IF;
 IF OLD.membership_id IS NOT NULL AND NEW.status IN ('creating','pending','closed') THEN
 RAISE EXCEPTION 'paid order cannot regress' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='billing_refunds' THEN
 allowed:=ARRAY['status','provider_refund_id','reconciliation_reason','updated_at'];
 IF OLD.status='succeeded' AND NEW.status<>'succeeded' THEN RAISE EXCEPTION 'terminal refund' USING ERRCODE='23514'; END IF;
 ELSE allowed:=ARRAY['status','attempts','due_at','lease_token','lease_until','last_error']; END IF;
 IF (to_jsonb(NEW)-allowed) IS DISTINCT FROM (to_jsonb(OLD)-allowed) THEN RAISE EXCEPTION 'immutable billing binding' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['billing_offers','billing_offer_withdrawals','payment_events','payment_orphan_events','payment_event_audits','payment_transactions','payment_refund_transactions','membership_revocations'] LOOP
 EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION deny_immutable_change()',t);
 EXECUTE format('CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON %I FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change()',t);
 END LOOP;
 FOREACH t IN ARRAY ARRAY['billing_orders','billing_refunds','billing_outbox'] LOOP
 EXECUTE format('CREATE TRIGGER workflow_guard BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION guard_billing_workflow()',t);
 EXECUTE format('CREATE TRIGGER workflow_truncate BEFORE TRUNCATE ON %I FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change()',t);
 END LOOP;
END $$;

-- At commit, workflow states must be backed by the immutable grant/refund ledger.
CREATE FUNCTION check_billing_reconciliation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE target text; o record; l record; r record; n bigint; refunded_delta numeric;
BEGIN
 IF TG_TABLE_NAME='billing_orders' THEN target:=NEW.id; ELSE target:=NEW.order_id; END IF;
 SELECT * INTO o FROM billing_orders WHERE id=target;
 PERFORM 1 FROM billing_accounts WHERE id=o.billing_account_id FOR UPDATE;
 IF o.membership_id IS NOT NULL THEN
   IF NOT EXISTS(SELECT 1 FROM memberships m WHERE m.id=o.membership_id
      AND m.billing_account_id=o.billing_account_id AND m.plan_id=o.offer->>'plan_id'
      AND m.operator_id='billing:verified' AND m.issuance_key=o.id) THEN
     RAISE EXCEPTION 'order membership divergence' USING ERRCODE='23514';
   END IF;
   SELECT * INTO l FROM credit_lots WHERE membership_id=o.membership_id;
   IF NOT FOUND OR l.billing_account_id<>o.billing_account_id OR l.issued<>(o.offer->>'credits')::bigint
      OR l.test_only<>(o.offer->>'test_only')::boolean THEN
     RAISE EXCEPTION 'order credit lot divergence' USING ERRCODE='23514';
   END IF;
   IF NOT EXISTS(SELECT 1 FROM credit_journals WHERE billing_account_id=o.billing_account_id
      AND kind='issue' AND source=o.membership_id) OR NOT EXISTS(SELECT 1 FROM payment_transactions
      WHERE channel=o.channel AND mode=o.mode AND merchant_id=o.merchant_id
      AND provider_transaction_id=o.provider_transaction_id AND order_id=o.id) THEN
     RAISE EXCEPTION 'order payment ledger missing' USING ERRCODE='23514';
   END IF;
 END IF;
 SELECT count(*) INTO n FROM billing_refunds WHERE order_id=target AND status='succeeded';
 IF n>1 THEN RAISE EXCEPTION 'multiple successful refunds require review' USING ERRCODE='23514'; END IF;
 IF o.status='refunded' AND n<>1 THEN RAISE EXCEPTION 'refunded order missing confirmation' USING ERRCODE='23514'; END IF;
 FOR r IN SELECT * FROM billing_refunds WHERE order_id=target AND status='succeeded' LOOP
   SELECT coalesce(sum(e.delta::numeric) FILTER(WHERE e.bucket='refunded'),0) INTO refunded_delta
     FROM credit_entries e JOIN credit_journals j ON j.id=e.journal_id
     WHERE j.billing_account_id=o.billing_account_id AND j.kind='refund' AND j.source=r.id;
   IF o.membership_id IS NULL OR r.amount_minor<>o.amount_minor OR refunded_delta<>(o.offer->>'credits')::numeric
      OR NOT EXISTS(SELECT 1 FROM membership_revocations WHERE membership_id=o.membership_id)
      OR l.refunded<>l.issued OR l.available<>0 OR l.held<>0 OR l.spent<>0 THEN
     RAISE EXCEPTION 'refund compensation missing' USING ERRCODE='23514';
   END IF;
 END LOOP;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER billing_orders_reconcile AFTER INSERT OR UPDATE ON billing_orders
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_billing_reconciliation();
CREATE CONSTRAINT TRIGGER billing_refunds_reconcile AFTER INSERT OR UPDATE ON billing_refunds
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_billing_reconciliation();

-- Refund-only changes are part of the public order projection as well.
CREATE FUNCTION touch_refund_order() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 UPDATE billing_orders SET updated_at=clock_timestamp() WHERE id=NEW.order_id;
 RETURN NEW;
END $$;
CREATE TRIGGER refund_order_revision AFTER INSERT OR UPDATE ON billing_refunds
FOR EACH ROW EXECUTE FUNCTION touch_refund_order();
