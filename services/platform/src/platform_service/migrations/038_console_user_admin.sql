-- Console user management, second round (decision 0020): roles, soft deletion, accounts
-- created from the console, balance adjustments and ending a plan early. As in 025, the
-- Console role writes nothing here directly: every change is one SECURITY DEFINER
-- function that re-checks the admin's role, and Runtime and PUBLIC cannot call them.

ALTER TABLE console_operations DROP CONSTRAINT console_operations_action_check;
ALTER TABLE console_operations ADD CONSTRAINT console_operations_action_check
 CHECK(action IN ('plan.publish','offer.publish','offer.withdraw','model.publish','model.state','execution.review',
    'allowance.issue','user.status','user.logout','user.note','plan.issue',
    'redemption.create','redemption.disable','admin.approve','admin.disable',
    'model.managed','model.price','model.trial','media.state','provider.config','model.import',
    'plan.revise','plan.state',
    'user.role','user.delete','user.restore','user.create','user.profile','user.password','credits.adjust','plan.end'));

-- A deleted account keeps every record; it is hidden from the default list and cannot sign in.
ALTER TABLE users ADD COLUMN deleted_at timestamptz;

-- A plan can now also be ended from the console, not only by a refunded order.
ALTER TABLE membership_revocations ALTER COLUMN order_id DROP NOT NULL;
ALTER TABLE membership_revocations ADD COLUMN operator_id text
    CHECK(operator_id IS NULL OR operator_id ~ '^console:[a-f0-9]{32}$');
ALTER TABLE membership_revocations ADD CONSTRAINT membership_revocations_source
    CHECK((order_id IS NULL) <> (operator_id IS NULL));

-- Points added or taken away by an admin. Taking points away moves them from available to
-- expired, so every lot still balances: issued = available + held + spent + expired + refunded.
ALTER TABLE credit_journals DROP CONSTRAINT credit_journals_kind_check;
ALTER TABLE credit_journals ADD CONSTRAINT credit_journals_kind_check
    CHECK(kind IN ('issue','reserve','settle','release','expire','refund','adjust'));

CREATE TABLE console_credit_adjustments (
    id text PRIMARY KEY,
    billing_account_id text NOT NULL REFERENCES billing_accounts(id),
    direction text NOT NULL CHECK(direction IN ('add','deduct','forfeit')),
    amount bigint NOT NULL CHECK(amount BETWEEN 1 AND 1000000000000),
    membership_id text REFERENCES memberships(id),
    journal_id text NOT NULL,
    operator_id text NOT NULL CHECK(operator_id ~ '^console:[a-f0-9]{32}$'),
    reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 256),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK((direction='deduct') = (membership_id IS NULL))
);
CREATE TRIGGER console_credit_adjustments_immutable BEFORE UPDATE OR DELETE ON console_credit_adjustments
FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER console_credit_adjustments_no_truncate BEFORE TRUNCATE ON console_credit_adjustments
FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();

-- Your plans hold up to 10,000 points; the old 100-point ceiling refused every one of them.
ALTER TABLE console_plan_grants DROP CONSTRAINT console_plan_grants_credits_check;
ALTER TABLE console_plan_grants ADD CONSTRAINT console_plan_grants_credits_check CHECK(credits BETWEEN 1 AND 1000000000000);

CREATE OR REPLACE FUNCTION console_issue_plan(p_admin text, p_wallet text, p_plan text, p_key text, p_reason text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE
    v_plan jsonb; v_credits bigint; v_days integer; operator text; issuance text; started timestamptz; ending timestamptz;
    mid text := gen_random_uuid()::text; lid text := gen_random_uuid()::text;
    gid text := gen_random_uuid()::text; jid text := gen_random_uuid()::text;
BEGIN
    PERFORM console_require_admin(p_admin, ARRAY['admin']);
    IF p_key !~ '^[a-f0-9]{64}$' OR p_reason IS NULL OR length(btrim(p_reason)) NOT BETWEEN 1 AND 256 THEN
        RAISE EXCEPTION 'invalid plan grant request' USING ERRCODE='EA422';
    END IF;
    PERFORM 1 FROM billing_accounts WHERE id=p_wallet FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'wallet not found' USING ERRCODE='EA404'; END IF;
    SELECT definition INTO v_plan FROM plans WHERE id=p_plan;
    IF v_plan IS NULL THEN RAISE EXCEPTION 'plan not found' USING ERRCODE='EA405'; END IF;
    IF p_plan LIKE 'easel-internal-%' OR p_plan LIKE 'easel-owner-allowance-%' THEN
        RAISE EXCEPTION 'plan has its own path' USING ERRCODE='EA406';
    END IF;
    v_credits := (v_plan->>'credits')::bigint; v_days := (v_plan->>'days')::integer;
    IF v_credits IS NULL OR v_credits < 1 OR v_credits > 1000000000000 OR v_days IS NULL OR v_days < 1 OR v_days > 365 THEN
        RAISE EXCEPTION 'plan above the console limit' USING ERRCODE='EA407';
    END IF;
    IF EXISTS(SELECT 1 FROM console_plan_grants g WHERE g.billing_account_id=p_wallet AND g.plan_id=p_plan
        AND g.expires_at>clock_timestamp()
        AND NOT EXISTS(SELECT 1 FROM membership_revocations r WHERE r.membership_id=g.membership_id)) THEN
        RAISE EXCEPTION 'plan grant active' USING ERRCODE='EA409';
    END IF;
    IF (SELECT coalesce(sum(issued),0) FROM credit_lots WHERE billing_account_id=p_wallet) + v_credits > 9223372036854775807 THEN
        RAISE EXCEPTION 'wallet capacity exceeded' USING ERRCODE='EA410';
    END IF;
    operator := 'console:' || p_admin;
    issuance := 'console:' || p_admin || ':' || p_key;
    started := clock_timestamp(); ending := started + make_interval(days=>v_days);
    INSERT INTO memberships(id,billing_account_id,plan_id,operator_id,issuance_key,starts_at,expires_at)
        VALUES (mid,p_wallet,p_plan,operator,issuance,started,ending);
    INSERT INTO credit_lots(id,billing_account_id,membership_id,issued,available,expires_at,test_only)
        VALUES (lid,p_wallet,mid,v_credits,v_credits,ending,(v_plan->>'test_only')::boolean);
    INSERT INTO credit_journals(id,billing_account_id,kind,source,operator_id) VALUES (jid,p_wallet,'issue',mid,operator);
    INSERT INTO credit_entries(journal_id,billing_account_id,bucket,delta)
        VALUES (jid,p_wallet,'issuance',-v_credits),(jid,p_wallet,'available',v_credits);
    INSERT INTO console_plan_grants(id,billing_account_id,plan_id,membership_id,lot_id,operator_id,issuance_key,reason,credits,starts_at,expires_at)
        VALUES (gid,p_wallet,p_plan,mid,lid,operator,issuance,btrim(p_reason),v_credits,started,ending);
    -- Same as 024: run the deferred checks now as the owner, then restore the default.
    SET CONSTRAINTS ALL IMMEDIATE;
    SET CONSTRAINTS ALL DEFERRED;
    RETURN jsonb_build_object('id',gid,'membership_id',mid,'billing_account_id',p_wallet,'plan_id',p_plan,
        'credits',v_credits::text,'starts_at',started,'expires_at',ending,'test_only',(v_plan->>'test_only')::boolean);
END $$;

-- The person's sign-in subject: the one under the product issuer when there are several.
CREATE FUNCTION console_subject_of(p_user text, p_product_issuer text) RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT subject FROM external_identities WHERE user_id=p_user
    ORDER BY (issuer=p_product_issuer) DESC, issuer, subject LIMIT 1
$$;

-- 'admin', 'operator' or 'viewer' for an active Console account of this person, else 'user'.
CREATE FUNCTION console_user_role(p_user text, p_console_issuer text) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER AS $$
    SELECT coalesce((SELECT a.role FROM console_admins a
        WHERE a.active AND a.issuer=p_console_issuer
          AND a.subject IN (SELECT e.subject FROM external_identities e WHERE e.user_id=p_user)
        ORDER BY CASE a.role WHEN 'admin' THEN 0 WHEN 'operator' THEN 1 ELSE 2 END LIMIT 1), 'user')
$$;

-- Admin only. You cannot take admin away from yourself or from the last admin.
CREATE FUNCTION console_set_user_role(p_admin text, p_user text, p_role text, p_console_issuer text, p_product_issuer text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_subject text; a record; v_id text := replace(gen_random_uuid()::text,'-','');
BEGIN
    PERFORM console_require_admin(p_admin, ARRAY['admin']);
    IF p_role NOT IN ('admin','user') THEN RAISE EXCEPTION 'role not allowed' USING ERRCODE='EA422'; END IF;
    PERFORM 1 FROM users WHERE id=p_user FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'user not found' USING ERRCODE='EA404'; END IF;
    v_subject := console_subject_of(p_user, p_product_issuer);
    IF v_subject IS NULL THEN RAISE EXCEPTION 'user has no sign-in' USING ERRCODE='EA404'; END IF;
    PERFORM 1 FROM console_admins WHERE active AND role='admin' FOR UPDATE;
    SELECT * INTO a FROM console_admins WHERE issuer=p_console_issuer AND subject=v_subject FOR UPDATE;
    IF p_role='admin' THEN
        IF EXISTS(SELECT 1 FROM users WHERE id=p_user AND (NOT active OR deleted_at IS NOT NULL)) THEN
            RAISE EXCEPTION 'account not active' USING ERRCODE='EA414';
        END IF;
        IF NOT FOUND THEN
            INSERT INTO console_admins(id,issuer,subject,role,created_by) VALUES (v_id,p_console_issuer,v_subject,'admin','console:'||p_admin);
        ELSIF NOT (a.active AND a.role='admin') THEN
            v_id := a.id;
            UPDATE console_admins SET role='admin',active=true,authorization_epoch=authorization_epoch+1 WHERE id=a.id;
        ELSE
            RETURN jsonb_build_object('id',p_user,'role','admin');
        END IF;
        INSERT INTO console_acl_events(id,admin_id,action,role,operator_id)
            VALUES (replace(gen_random_uuid()::text,'-',''),v_id,'approve','admin','console:'||p_admin);
    ELSIF FOUND AND a.active THEN
        IF a.id=p_admin THEN RAISE EXCEPTION 'cannot demote yourself' USING ERRCODE='EA413'; END IF;
        IF a.role='admin' AND (SELECT count(*) FROM console_admins WHERE active AND role='admin')<=1 THEN
            RAISE EXCEPTION 'last admin' USING ERRCODE='EA409';
        END IF;
        UPDATE console_admins SET active=false,authorization_epoch=authorization_epoch+1 WHERE id=a.id;
        INSERT INTO console_acl_events(id,admin_id,action,role,operator_id)
            VALUES (replace(gen_random_uuid()::text,'-',''),a.id,'disable',a.role,'console:'||p_admin);
    END IF;
    RETURN jsonb_build_object('id',p_user,'role',p_role);
END $$;

-- Admin only. The person's Logto id, so the console can edit the sign-in account itself.
CREATE FUNCTION console_user_subject(p_admin text, p_user text, p_product_issuer text) RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER AS $$
BEGIN
    PERFORM console_require_admin(p_admin, ARRAY['admin']);
    RETURN console_subject_of(p_user, p_product_issuer);
END $$;

-- Soft deletion: sign out everywhere, refuse sign-in, hide from the list. Records stay.
CREATE FUNCTION console_set_user_deleted(p_admin text, p_user text, p_deleted boolean, p_console_issuer text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE row record;
BEGIN
    PERFORM console_require_admin(p_admin, ARRAY['admin']);
    PERFORM 1 FROM users WHERE id=p_user FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'user not found' USING ERRCODE='EA404'; END IF;
    IF p_deleted AND console_user_role(p_user, p_console_issuer)<>'user' THEN
        RAISE EXCEPTION 'remove console access first' USING ERRCODE='EA409';
    END IF;
    IF p_deleted THEN
        UPDATE users SET active=false,deleted_at=coalesce(deleted_at,clock_timestamp()),authorization_epoch=authorization_epoch+1
            WHERE id=p_user RETURNING id,active,deleted_at INTO row;
        UPDATE identity_sessions SET revoked_at=clock_timestamp()
            WHERE user_id=p_user AND revoked_at IS NULL AND expires_at>clock_timestamp();
    ELSE
        UPDATE users SET active=true,deleted_at=NULL WHERE id=p_user RETURNING id,active,deleted_at INTO row;
    END IF;
    RETURN jsonb_build_object('id',row.id,'active',row.active,'deleted',row.deleted_at IS NOT NULL);
END $$;

-- An account made in the console exists before its first sign-in, so it can be given a plan
-- right away. The same identity rows the first sign-in would create.
CREATE FUNCTION console_create_user(p_admin text, p_issuer text, p_subject text, p_username text, p_email text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_user text; v_tenant text; v_wallet text; existing text;
BEGIN
    PERFORM console_require_admin(p_admin, ARRAY['admin']);
    IF p_issuer IS NULL OR length(p_issuer) NOT BETWEEN 1 AND 2048 OR p_subject IS NULL OR length(p_subject) NOT BETWEEN 1 AND 255 THEN
        RAISE EXCEPTION 'invalid identity' USING ERRCODE='EA422';
    END IF;
    PERFORM lock_product_identity(p_issuer, p_subject);
    SELECT user_id INTO existing FROM external_identities WHERE issuer=p_issuer AND subject=p_subject;
    IF FOUND THEN RETURN jsonb_build_object('id',existing,'created',false); END IF;
    v_user := replace(gen_random_uuid()::text,'-',''); v_tenant := replace(gen_random_uuid()::text,'-',''); v_wallet := replace(gen_random_uuid()::text,'-','');
    INSERT INTO users(id) VALUES (v_user);
    INSERT INTO external_identities(issuer,subject,user_id,email,email_verified,username) VALUES (p_issuer,p_subject,v_user,p_email,false,p_username);
    INSERT INTO tenants(id,owner_user_id) VALUES (v_tenant,v_user);
    INSERT INTO tenant_members(tenant_id,user_id,role) VALUES (v_tenant,v_user,'owner');
    INSERT INTO billing_accounts(id,tenant_id) VALUES (v_wallet,v_tenant);
    RETURN jsonb_build_object('id',v_user,'created',true);
END $$;

-- Keep the list's cached name and email in step after an edit; the next sign-in refreshes them too.
CREATE FUNCTION console_update_identity(p_admin text, p_user text, p_username text, p_email text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
    PERFORM console_require_admin(p_admin, ARRAY['admin']);
    UPDATE external_identities SET username=p_username,email=p_email,
        email_verified=CASE WHEN email IS DISTINCT FROM p_email THEN false ELSE email_verified END WHERE user_id=p_user;
    IF NOT FOUND THEN RAISE EXCEPTION 'user not found' USING ERRCODE='EA404'; END IF;
    RETURN jsonb_build_object('id',p_user);
END $$;

-- Take points out of live lots, soonest expiry first; frozen and test lots are never touched.
CREATE FUNCTION console_take_points(p_wallet text, p_amount bigint, p_membership text) RETURNS bigint
LANGUAGE plpgsql AS $$
DECLARE l record; left_over bigint := p_amount; part bigint;
BEGIN
    FOR l IN SELECT id,available FROM credit_lots WHERE billing_account_id=p_wallet AND available>0
        AND NOT frozen AND NOT test_only AND expires_at>clock_timestamp()
        AND (p_membership IS NULL OR membership_id=p_membership)
        ORDER BY expires_at,id FOR UPDATE
    LOOP
        EXIT WHEN left_over=0;
        part := least(l.available, left_over);
        UPDATE credit_lots SET available=available-part,expired=expired+part WHERE id=l.id;
        left_over := left_over-part;
    END LOOP;
    RETURN p_amount-left_over;
END $$;

-- Admin only. Adding points creates a lot on the person's current live plan and expires with it,
-- so added points never unlock features or models the plan does not include. Deducting takes
-- points from live lots, soonest expiry first, and refuses to go below zero.
CREATE FUNCTION console_adjust_credits(p_admin text, p_wallet text, p_delta bigint, p_key text, p_reason text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE
    operator text := 'console:' || p_admin; issuance text; m record; taken bigint;
    v_id text := gen_random_uuid()::text; mid text := gen_random_uuid()::text; lid text := gen_random_uuid()::text;
    jid text := gen_random_uuid()::text; v_amount bigint := abs(p_delta); v_expires timestamptz;
BEGIN
    PERFORM console_require_admin(p_admin, ARRAY['admin']);
    IF p_key !~ '^[a-f0-9]{64}$' OR p_reason IS NULL OR length(btrim(p_reason)) NOT BETWEEN 1 AND 256
        OR p_delta IS NULL OR p_delta=0 OR v_amount>1000000000000 THEN
        RAISE EXCEPTION 'invalid adjustment' USING ERRCODE='EA422';
    END IF;
    PERFORM 1 FROM billing_accounts WHERE id=p_wallet FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'wallet not found' USING ERRCODE='EA404'; END IF;
    issuance := operator || ':' || p_key;
    IF p_delta>0 THEN
        SELECT ms.plan_id,ms.expires_at INTO m FROM memberships ms JOIN plans p ON p.id=ms.plan_id
            WHERE ms.billing_account_id=p_wallet AND ms.starts_at<=clock_timestamp() AND ms.expires_at>clock_timestamp()
              AND NOT (p.definition->>'test_only')::boolean
              AND NOT EXISTS(SELECT 1 FROM membership_revocations r WHERE r.membership_id=ms.id)
            ORDER BY ms.expires_at DESC, ms.id LIMIT 1;
        IF NOT FOUND THEN RAISE EXCEPTION 'no live plan' USING ERRCODE='EA412'; END IF;
        v_expires := m.expires_at;
        IF (SELECT coalesce(sum(issued),0) FROM credit_lots WHERE billing_account_id=p_wallet) + v_amount > 9223372036854775807 THEN
            RAISE EXCEPTION 'wallet capacity exceeded' USING ERRCODE='EA410';
        END IF;
        INSERT INTO memberships(id,billing_account_id,plan_id,operator_id,issuance_key,starts_at,expires_at)
            VALUES (mid,p_wallet,m.plan_id,operator,issuance,clock_timestamp(),m.expires_at);
        INSERT INTO credit_lots(id,billing_account_id,membership_id,issued,available,expires_at,test_only)
            VALUES (lid,p_wallet,mid,v_amount,v_amount,m.expires_at,false);
        INSERT INTO credit_journals(id,billing_account_id,kind,source,operator_id) VALUES (jid,p_wallet,'issue',mid,operator);
        INSERT INTO credit_entries(journal_id,billing_account_id,bucket,delta)
            VALUES (jid,p_wallet,'issuance',-v_amount),(jid,p_wallet,'available',v_amount);
        INSERT INTO console_credit_adjustments(id,billing_account_id,direction,amount,membership_id,journal_id,operator_id,reason)
            VALUES (v_id,p_wallet,'add',v_amount,mid,jid,operator,btrim(p_reason));
    ELSE
        IF (SELECT coalesce(sum(available),0) FROM credit_lots WHERE billing_account_id=p_wallet AND available>0
            AND NOT frozen AND NOT test_only AND expires_at>clock_timestamp()) < v_amount THEN
            RAISE EXCEPTION 'not enough points' USING ERRCODE='EA411';
        END IF;
        taken := console_take_points(p_wallet, v_amount, NULL);
        INSERT INTO credit_journals(id,billing_account_id,kind,source,operator_id) VALUES (jid,p_wallet,'adjust',v_id,operator);
        INSERT INTO credit_entries(journal_id,billing_account_id,bucket,delta)
            VALUES (jid,p_wallet,'available',-taken),(jid,p_wallet,'expired',taken);
        INSERT INTO console_credit_adjustments(id,billing_account_id,direction,amount,journal_id,operator_id,reason)
            VALUES (v_id,p_wallet,'deduct',taken,jid,operator,btrim(p_reason));
    END IF;
    SET CONSTRAINTS ALL IMMEDIATE;
    SET CONSTRAINTS ALL DEFERRED;
    RETURN jsonb_build_object('id',v_id,'billing_account_id',p_wallet,'delta',p_delta::text,
        'expires_at',v_expires);
END $$;

-- Admin only. Ends one plan now: no more access from it, and its unspent points are forfeited.
-- Refused while a call is still holding points from it, so nothing in flight is cut off.
CREATE FUNCTION console_end_membership(p_admin text, p_wallet text, p_membership text, p_reason text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE operator text := 'console:' || p_admin; l record; taken bigint := 0; has_lot boolean;
    v_id text := gen_random_uuid()::text; jid text := gen_random_uuid()::text;
BEGIN
    PERFORM console_require_admin(p_admin, ARRAY['admin']);
    IF p_reason IS NULL OR length(btrim(p_reason)) NOT BETWEEN 1 AND 256 THEN
        RAISE EXCEPTION 'invalid request' USING ERRCODE='EA422';
    END IF;
    PERFORM 1 FROM billing_accounts WHERE id=p_wallet FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'wallet not found' USING ERRCODE='EA404'; END IF;
    PERFORM 1 FROM memberships WHERE id=p_membership AND billing_account_id=p_wallet
        AND expires_at>clock_timestamp() AND NOT EXISTS(SELECT 1 FROM membership_revocations r WHERE r.membership_id=p_membership);
    IF NOT FOUND THEN RAISE EXCEPTION 'membership not active' USING ERRCODE='EA405'; END IF;
    SELECT * INTO l FROM credit_lots WHERE membership_id=p_membership FOR UPDATE;
    has_lot := FOUND;
    IF has_lot AND l.held>0 THEN RAISE EXCEPTION 'calls in flight' USING ERRCODE='EA409'; END IF;
    INSERT INTO membership_revocations(membership_id,reason,operator_id) VALUES (p_membership,btrim(p_reason),operator);
    IF has_lot AND l.available>0 THEN
        taken := l.available;
        UPDATE credit_lots SET available=0,expired=expired+taken WHERE id=l.id;
        INSERT INTO credit_journals(id,billing_account_id,kind,source,operator_id) VALUES (jid,p_wallet,'adjust',v_id,operator);
        INSERT INTO credit_entries(journal_id,billing_account_id,bucket,delta)
            VALUES (jid,p_wallet,'available',-taken),(jid,p_wallet,'expired',taken);
        INSERT INTO console_credit_adjustments(id,billing_account_id,direction,amount,membership_id,journal_id,operator_id,reason)
            VALUES (v_id,p_wallet,'forfeit',taken,p_membership,jid,operator,btrim(p_reason));
    END IF;
    SET CONSTRAINTS ALL IMMEDIATE;
    SET CONSTRAINTS ALL DEFERRED;
    RETURN jsonb_build_object('membership_id',p_membership,'billing_account_id',p_wallet,'forfeited',taken::text);
END $$;

DO $$ BEGIN
    EXECUTE format('ALTER FUNCTION console_issue_plan(text,text,text,text,text) SET search_path = %I, pg_temp', current_schema());
    EXECUTE format('ALTER FUNCTION console_subject_of(text,text) SET search_path = %I, pg_temp', current_schema());
    EXECUTE format('ALTER FUNCTION console_user_role(text,text) SET search_path = %I, pg_temp', current_schema());
    EXECUTE format('ALTER FUNCTION console_set_user_role(text,text,text,text,text) SET search_path = %I, pg_temp', current_schema());
    EXECUTE format('ALTER FUNCTION console_user_subject(text,text,text) SET search_path = %I, pg_temp', current_schema());
    EXECUTE format('ALTER FUNCTION console_set_user_deleted(text,text,boolean,text) SET search_path = %I, pg_temp', current_schema());
    EXECUTE format('ALTER FUNCTION console_create_user(text,text,text,text,text) SET search_path = %I, pg_temp', current_schema());
    EXECUTE format('ALTER FUNCTION console_update_identity(text,text,text,text) SET search_path = %I, pg_temp', current_schema());
    EXECUTE format('ALTER FUNCTION console_take_points(text,bigint,text) SET search_path = %I, pg_temp', current_schema());
    EXECUTE format('ALTER FUNCTION console_adjust_credits(text,text,bigint,text,text) SET search_path = %I, pg_temp', current_schema());
    EXECUTE format('ALTER FUNCTION console_end_membership(text,text,text,text) SET search_path = %I, pg_temp', current_schema());
END $$;
REVOKE ALL ON FUNCTION console_subject_of(text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION console_user_role(text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION console_set_user_role(text,text,text,text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION console_user_subject(text,text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION console_set_user_deleted(text,text,boolean,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION console_create_user(text,text,text,text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION console_update_identity(text,text,text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION console_take_points(text,bigint,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION console_adjust_credits(text,text,bigint,text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION console_end_membership(text,text,text,text) FROM PUBLIC;
