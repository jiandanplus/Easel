-- Redemption codes and web approval of Console operators (decision 0013).
-- An admin creates a batch of single-use codes for one published plan (same limits
-- as 025: at most 100 points and 365 days). Only code hashes are stored. A signed-in
-- product user redeems a code through redeem_code(), which runtime may call; every
-- other entry point is Console-only. Each wallet can redeem one code per batch.
ALTER TABLE console_operations DROP CONSTRAINT console_operations_action_check;
ALTER TABLE console_operations ADD CONSTRAINT console_operations_action_check
 CHECK(action IN ('plan.publish','offer.publish','offer.withdraw','model.publish','model.state','execution.review',
    'allowance.issue','user.status','user.logout','user.note','plan.issue',
    'redemption.create','redemption.disable','admin.approve','admin.disable'));
ALTER TABLE console_operations DROP CONSTRAINT console_operations_target_type_check;
ALTER TABLE console_operations ADD CONSTRAINT console_operations_target_type_check
 CHECK(target_type IN ('plan','offer','model','execution','wallet','user','redemption','admin'));

CREATE TABLE redemption_batches (
    id text PRIMARY KEY CHECK(id ~ '^[a-f0-9]{32}$'),
    plan_id text NOT NULL REFERENCES plans(id),
    name text NOT NULL CHECK(length(btrim(name)) BETWEEN 1 AND 60),
    reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 256),
    created_by text NOT NULL REFERENCES console_admins(id),
    code_count integer NOT NULL CHECK(code_count BETWEEN 1 AND 100),
    credits bigint NOT NULL CHECK(credits BETWEEN 1 AND 100000000),
    expires_at timestamptz NOT NULL,
    disabled_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK(expires_at > created_at AND expires_at <= created_at + interval '365 days')
);
CREATE TABLE redemption_codes (
    code_hash text PRIMARY KEY CHECK(code_hash ~ '^[a-f0-9]{64}$'),
    batch_id text NOT NULL REFERENCES redemption_batches(id),
    redeemed_at timestamptz,
    billing_account_id text REFERENCES billing_accounts(id),
    user_id text REFERENCES users(id),
    membership_id text UNIQUE,
    lot_id text UNIQUE,
    CHECK((redeemed_at IS NULL AND billing_account_id IS NULL AND user_id IS NULL AND membership_id IS NULL AND lot_id IS NULL)
       OR (redeemed_at IS NOT NULL AND billing_account_id IS NOT NULL AND user_id IS NOT NULL AND membership_id IS NOT NULL AND lot_id IS NOT NULL)),
    FOREIGN KEY(membership_id,billing_account_id) REFERENCES memberships(id,billing_account_id),
    FOREIGN KEY(lot_id,billing_account_id) REFERENCES credit_lots(id,billing_account_id)
);
CREATE INDEX redemption_codes_batch ON redemption_codes(batch_id);
CREATE UNIQUE INDEX redemption_once_per_batch ON redemption_codes(batch_id,billing_account_id) WHERE billing_account_id IS NOT NULL;

-- A batch may only be disabled, once; a code may only be redeemed, once.
CREATE FUNCTION guard_redemption_batch() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' OR OLD.disabled_at IS NOT NULL OR NEW.disabled_at IS NULL
        OR (to_jsonb(NEW)-'disabled_at') IS DISTINCT FROM (to_jsonb(OLD)-'disabled_at') THEN
        RAISE EXCEPTION 'redemption batch is immutable' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER redemption_batches_guard BEFORE UPDATE OR DELETE ON redemption_batches
FOR EACH ROW EXECUTE FUNCTION guard_redemption_batch();
CREATE TRIGGER redemption_batches_no_truncate BEFORE TRUNCATE ON redemption_batches
FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();
CREATE FUNCTION guard_redemption_code() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' OR OLD.redeemed_at IS NOT NULL OR NEW.code_hash<>OLD.code_hash OR NEW.batch_id<>OLD.batch_id THEN
        RAISE EXCEPTION 'redemption code is immutable' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER redemption_codes_guard BEFORE UPDATE OR DELETE ON redemption_codes
FOR EACH ROW EXECUTE FUNCTION guard_redemption_code();
CREATE TRIGGER redemption_codes_no_truncate BEFORE TRUNCATE ON redemption_codes
FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();

CREATE FUNCTION check_redemption() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE n bigint; issued numeric; available numeric; b record;
BEGIN
    IF NEW.redeemed_at IS NULL THEN RETURN NULL; END IF;
    SELECT * INTO b FROM redemption_batches WHERE id=NEW.batch_id;
    IF NOT EXISTS(SELECT 1 FROM memberships m JOIN credit_lots l ON l.membership_id=m.id JOIN plans p ON p.id=m.plan_id
        WHERE m.id=NEW.membership_id AND m.billing_account_id=NEW.billing_account_id AND m.plan_id=b.plan_id
        AND m.operator_id='redemption:'||b.id AND m.issuance_key=NEW.code_hash
        AND l.id=NEW.lot_id AND l.issued=b.credits AND l.expires_at=m.expires_at
        AND l.test_only=(p.definition->>'test_only')::boolean) THEN
        RAISE EXCEPTION 'redemption binding divergence' USING ERRCODE='23514';
    END IF;
    SELECT count(*),coalesce(sum(e.delta) FILTER(WHERE e.bucket='issuance'),0),
        coalesce(sum(e.delta) FILTER(WHERE e.bucket='available'),0)
        INTO n,issued,available FROM credit_journals j JOIN credit_entries e ON e.journal_id=j.id
        WHERE j.billing_account_id=NEW.billing_account_id AND j.kind='issue' AND j.source=NEW.membership_id AND j.operator_id='redemption:'||b.id;
    IF n<>2 OR issued<>-b.credits OR available<>b.credits THEN
        RAISE EXCEPTION 'redemption journal divergence' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER redemption_codes_consistent AFTER UPDATE ON redemption_codes
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_redemption();

CREATE FUNCTION console_create_redemption_batch(p_admin text, p_plan text, p_name text, p_reason text, p_days integer, p_hashes text[])
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_plan jsonb; v_credits bigint; v_days integer; v_id text := replace(gen_random_uuid()::text,'-',''); v_expires timestamptz;
BEGIN
    PERFORM console_require_admin(p_admin, ARRAY['admin']);
    IF p_name IS NULL OR length(btrim(p_name)) NOT BETWEEN 1 AND 60 OR p_reason IS NULL OR length(btrim(p_reason)) NOT BETWEEN 1 AND 256
        OR p_days IS NULL OR p_days NOT BETWEEN 1 AND 365 OR p_hashes IS NULL OR cardinality(p_hashes) NOT BETWEEN 1 AND 100
        OR EXISTS(SELECT 1 FROM unnest(p_hashes) h WHERE h IS NULL OR h !~ '^[a-f0-9]{64}$')
        OR (SELECT count(DISTINCT h) FROM unnest(p_hashes) h) <> cardinality(p_hashes) THEN
        RAISE EXCEPTION 'invalid redemption batch' USING ERRCODE='EA422';
    END IF;
    SELECT definition INTO v_plan FROM plans WHERE id=p_plan;
    IF v_plan IS NULL THEN RAISE EXCEPTION 'plan not found' USING ERRCODE='EA405'; END IF;
    IF p_plan LIKE 'easel-internal-%' OR p_plan LIKE 'easel-owner-allowance-%' THEN
        RAISE EXCEPTION 'plan has its own path' USING ERRCODE='EA406';
    END IF;
    v_credits := (v_plan->>'credits')::bigint; v_days := (v_plan->>'days')::integer;
    IF v_credits IS NULL OR v_credits < 1 OR v_credits > 100000000 OR v_days IS NULL OR v_days < 1 OR v_days > 365 THEN
        RAISE EXCEPTION 'plan above the console limit' USING ERRCODE='EA407';
    END IF;
    v_expires := clock_timestamp() + make_interval(days=>p_days);
    INSERT INTO redemption_batches(id,plan_id,name,reason,created_by,code_count,credits,expires_at)
        VALUES (v_id,p_plan,btrim(p_name),btrim(p_reason),p_admin,cardinality(p_hashes),v_credits,v_expires);
    INSERT INTO redemption_codes(code_hash,batch_id) SELECT h,v_id FROM unnest(p_hashes) h;
    RETURN jsonb_build_object('id',v_id,'plan_id',p_plan,'name',btrim(p_name),'code_count',cardinality(p_hashes),
        'credits',v_credits::text,'expires_at',v_expires);
END $$;

CREATE FUNCTION console_disable_redemption_batch(p_admin text, p_batch text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE row record;
BEGIN
    PERFORM console_require_admin(p_admin, ARRAY['admin']);
    SELECT * INTO row FROM redemption_batches WHERE id=p_batch FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'batch not found' USING ERRCODE='EA404'; END IF;
    IF row.disabled_at IS NULL THEN
        UPDATE redemption_batches SET disabled_at=clock_timestamp() WHERE id=p_batch RETURNING * INTO row;
    END IF;
    RETURN jsonb_build_object('id',row.id,'disabled_at',row.disabled_at);
END $$;

-- Called by the product runtime for the signed-in user's own wallet.
CREATE FUNCTION redeem_code(p_user text, p_wallet text, p_hash text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE c record; b record; v_plan jsonb; v_days integer; started timestamptz; ending timestamptz;
    mid text := gen_random_uuid()::text; lid text := gen_random_uuid()::text; jid text := gen_random_uuid()::text;
BEGIN
    IF p_hash IS NULL OR p_hash !~ '^[a-f0-9]{64}$' THEN RAISE EXCEPTION 'invalid code' USING ERRCODE='EB404'; END IF;
    PERFORM 1 FROM billing_accounts WHERE id=p_wallet FOR UPDATE;
    IF NOT FOUND OR NOT EXISTS(SELECT 1 FROM users WHERE id=p_user AND active) THEN
        RAISE EXCEPTION 'account unavailable' USING ERRCODE='EB403';
    END IF;
    SELECT * INTO c FROM redemption_codes WHERE code_hash=p_hash FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'code not found' USING ERRCODE='EB404'; END IF;
    SELECT * INTO b FROM redemption_batches WHERE id=c.batch_id;
    SELECT definition INTO v_plan FROM plans WHERE id=b.plan_id;
    IF c.redeemed_at IS NOT NULL THEN
        IF c.billing_account_id=p_wallet THEN
            RETURN jsonb_build_object('plan_id',b.plan_id,'name',v_plan->>'name','credits',b.credits::text,
                'expires_at',(SELECT expires_at FROM memberships WHERE id=c.membership_id),'redeemed_at',c.redeemed_at,'replayed',true);
        END IF;
        RAISE EXCEPTION 'code already used' USING ERRCODE='EB409';
    END IF;
    IF b.disabled_at IS NOT NULL THEN RAISE EXCEPTION 'batch disabled' USING ERRCODE='EB410'; END IF;
    IF b.expires_at <= clock_timestamp() THEN RAISE EXCEPTION 'code expired' USING ERRCODE='EB411'; END IF;
    IF EXISTS(SELECT 1 FROM redemption_codes WHERE batch_id=c.batch_id AND billing_account_id=p_wallet) THEN
        RAISE EXCEPTION 'batch already used by this wallet' USING ERRCODE='EB412';
    END IF;
    IF (SELECT coalesce(sum(issued),0) FROM credit_lots WHERE billing_account_id=p_wallet) + b.credits > 9223372036854775807 THEN
        RAISE EXCEPTION 'wallet capacity exceeded' USING ERRCODE='EB413';
    END IF;
    v_days := (v_plan->>'days')::integer;
    started := clock_timestamp(); ending := started + make_interval(days=>v_days);
    INSERT INTO memberships(id,billing_account_id,plan_id,operator_id,issuance_key,starts_at,expires_at)
        VALUES (mid,p_wallet,b.plan_id,'redemption:'||b.id,p_hash,started,ending);
    INSERT INTO credit_lots(id,billing_account_id,membership_id,issued,available,expires_at,test_only)
        VALUES (lid,p_wallet,mid,b.credits,b.credits,ending,(v_plan->>'test_only')::boolean);
    INSERT INTO credit_journals(id,billing_account_id,kind,source,operator_id) VALUES (jid,p_wallet,'issue',mid,'redemption:'||b.id);
    INSERT INTO credit_entries(journal_id,billing_account_id,bucket,delta)
        VALUES (jid,p_wallet,'issuance',-b.credits),(jid,p_wallet,'available',b.credits);
    UPDATE redemption_codes SET redeemed_at=started,billing_account_id=p_wallet,user_id=p_user,membership_id=mid,lot_id=lid
        WHERE code_hash=p_hash;
    -- Same as 024: run the deferred checks now as the owner, then restore the default.
    SET CONSTRAINTS ALL IMMEDIATE;
    SET CONSTRAINTS ALL DEFERRED;
    RETURN jsonb_build_object('plan_id',b.plan_id,'name',v_plan->>'name','credits',b.credits::text,
        'expires_at',ending,'redeemed_at',started,'replayed',false);
END $$;

-- Web approval may add operators and viewers only; admins stay a host decision.
CREATE FUNCTION console_approve_request(p_admin text, p_request text, p_role text, p_issuer text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE r record; v_id text := replace(gen_random_uuid()::text,'-','');
BEGIN
    PERFORM console_require_admin(p_admin, ARRAY['admin']);
    IF p_role NOT IN ('operator','viewer') THEN RAISE EXCEPTION 'role not allowed on the web' USING ERRCODE='EA403'; END IF;
    SELECT * INTO r FROM console_login_requests WHERE id=p_request FOR UPDATE;
    IF NOT FOUND OR r.status<>'pending' OR r.expires_at<=clock_timestamp() OR r.issuer<>p_issuer THEN
        RAISE EXCEPTION 'request invalid' USING ERRCODE='EA404';
    END IF;
    IF EXISTS(SELECT 1 FROM console_admins WHERE issuer=r.issuer AND subject=r.subject)
        OR EXISTS(SELECT 1 FROM console_identity_aliases WHERE issuer=r.issuer AND subject=r.subject) THEN
        RAISE EXCEPTION 'identity already has access' USING ERRCODE='EA409';
    END IF;
    INSERT INTO console_admins(id,issuer,subject,role,created_by) VALUES (v_id,r.issuer,r.subject,p_role,'console:'||p_admin);
    UPDATE console_login_requests SET status='approved',approved_admin_id=v_id WHERE id=p_request;
    INSERT INTO console_acl_events(id,admin_id,request_id,action,role,operator_id)
        VALUES (replace(gen_random_uuid()::text,'-',''),v_id,p_request,'approve',p_role,'console:'||p_admin);
    RETURN jsonb_build_object('id',v_id,'request_id',p_request,'role',p_role,'active',true);
END $$;

CREATE FUNCTION console_disable_admin(p_admin text, p_target text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE t record;
BEGIN
    PERFORM console_require_admin(p_admin, ARRAY['admin']);
    SELECT * INTO t FROM console_admins WHERE id=p_target FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'admin not found' USING ERRCODE='EA404'; END IF;
    IF t.id=p_admin OR t.role='admin' THEN RAISE EXCEPTION 'cannot disable this account on the web' USING ERRCODE='EA403'; END IF;
    IF t.active THEN
        UPDATE console_admins SET active=false,authorization_epoch=authorization_epoch+1 WHERE id=p_target;
        INSERT INTO console_acl_events(id,admin_id,action,role,operator_id)
            VALUES (replace(gen_random_uuid()::text,'-',''),p_target,'disable',t.role,'console:'||p_admin);
    END IF;
    RETURN jsonb_build_object('id',t.id,'active',false);
END $$;

DO $$ BEGIN
    EXECUTE format('ALTER FUNCTION console_create_redemption_batch(text,text,text,text,integer,text[]) SET search_path = %I, pg_temp', current_schema());
    EXECUTE format('ALTER FUNCTION console_disable_redemption_batch(text,text) SET search_path = %I, pg_temp', current_schema());
    EXECUTE format('ALTER FUNCTION redeem_code(text,text,text) SET search_path = %I, pg_temp', current_schema());
    EXECUTE format('ALTER FUNCTION console_approve_request(text,text,text,text) SET search_path = %I, pg_temp', current_schema());
    EXECUTE format('ALTER FUNCTION console_disable_admin(text,text) SET search_path = %I, pg_temp', current_schema());
END $$;
REVOKE ALL ON FUNCTION console_create_redemption_batch(text,text,text,text,integer,text[]) FROM PUBLIC;
REVOKE ALL ON FUNCTION console_disable_redemption_batch(text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION redeem_code(text,text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION console_approve_request(text,text,text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION console_disable_admin(text,text) FROM PUBLIC;
