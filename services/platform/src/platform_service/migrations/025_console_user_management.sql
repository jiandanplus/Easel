-- Console user management (decision 0012): disable or enable an account, sign it out
-- everywhere, keep an operator note, and issue a published plan to a wallet.
-- The Console role still has no ledger or user privileges; each change goes through
-- one SECURITY DEFINER function that re-checks the admin's role. Runtime and PUBLIC
-- cannot call them.
ALTER TABLE console_operations DROP CONSTRAINT console_operations_action_check;
ALTER TABLE console_operations ADD CONSTRAINT console_operations_action_check
 CHECK(action IN ('plan.publish','offer.publish','offer.withdraw','model.publish','model.state','execution.review',
    'allowance.issue','user.status','user.logout','user.note','plan.issue'));
ALTER TABLE console_operations DROP CONSTRAINT console_operations_target_type_check;
ALTER TABLE console_operations ADD CONSTRAINT console_operations_target_type_check
 CHECK(target_type IN ('plan','offer','model','execution','wallet','user'));

-- Operator notes are Console data, not account data: the product never reads them.
CREATE TABLE console_user_notes (
    user_id text PRIMARY KEY REFERENCES users(id),
    note text NOT NULL CHECK(length(note) <= 500),
    tags text[] NOT NULL DEFAULT '{}' CHECK(cardinality(tags) <= 10),
    updated_by text NOT NULL CHECK(updated_by ~ '^[a-f0-9]{32}$'),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE console_plan_grants (
    id text PRIMARY KEY,
    billing_account_id text NOT NULL REFERENCES billing_accounts(id),
    plan_id text NOT NULL REFERENCES plans(id),
    membership_id text NOT NULL UNIQUE,
    lot_id text NOT NULL UNIQUE,
    operator_id text NOT NULL CHECK(operator_id ~ '^console:[a-f0-9]{32}$'),
    issuance_key text NOT NULL UNIQUE CHECK(issuance_key ~ '^console:[a-f0-9]{32}:[a-f0-9]{64}$'),
    reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 256),
    credits bigint NOT NULL CHECK(credits BETWEEN 1 AND 100000000),
    starts_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL CHECK(expires_at > starts_at AND expires_at <= starts_at + interval '365 days'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY(membership_id,billing_account_id) REFERENCES memberships(id,billing_account_id),
    FOREIGN KEY(lot_id,billing_account_id) REFERENCES credit_lots(id,billing_account_id)
);
CREATE TRIGGER console_plan_grants_immutable BEFORE UPDATE OR DELETE ON console_plan_grants
FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER console_plan_grants_no_truncate BEFORE TRUNCATE ON console_plan_grants
FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();

CREATE FUNCTION check_console_plan_grant() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE n bigint; issued numeric; available numeric;
BEGIN
    IF NOT EXISTS(SELECT 1 FROM memberships m JOIN credit_lots l ON l.membership_id=m.id JOIN plans p ON p.id=m.plan_id
        WHERE m.id=NEW.membership_id AND m.billing_account_id=NEW.billing_account_id AND m.plan_id=NEW.plan_id
        AND m.operator_id=NEW.operator_id AND m.issuance_key=NEW.issuance_key
        AND m.starts_at=NEW.starts_at AND m.expires_at=NEW.expires_at
        AND l.id=NEW.lot_id AND l.issued=NEW.credits AND l.expires_at=NEW.expires_at
        AND l.test_only=(p.definition->>'test_only')::boolean AND (p.definition->>'credits')::bigint=NEW.credits) THEN
        RAISE EXCEPTION 'console plan grant binding divergence' USING ERRCODE='23514';
    END IF;
    SELECT count(*),coalesce(sum(e.delta) FILTER(WHERE e.bucket='issuance'),0),
        coalesce(sum(e.delta) FILTER(WHERE e.bucket='available'),0)
        INTO n,issued,available FROM credit_journals j JOIN credit_entries e ON e.journal_id=j.id
        WHERE j.billing_account_id=NEW.billing_account_id AND j.kind='issue' AND j.source=NEW.membership_id AND j.operator_id=NEW.operator_id;
    IF n<>2 OR issued<>-NEW.credits OR available<>NEW.credits THEN
        RAISE EXCEPTION 'console plan grant journal divergence' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER console_plan_grants_consistent AFTER INSERT ON console_plan_grants
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_console_plan_grant();

CREATE FUNCTION console_require_admin(p_admin text, p_roles text[]) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM console_admins WHERE id=p_admin AND active AND role=ANY(p_roles)) THEN
        RAISE EXCEPTION 'console role required' USING ERRCODE='EA403';
    END IF;
END $$;

-- Disabling also bumps the authorization epoch, so every product session ends at once.
CREATE FUNCTION console_set_user_active(p_admin text, p_user text, p_active boolean)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE row record;
BEGIN
    PERFORM console_require_admin(p_admin, ARRAY['admin','operator']);
    UPDATE users SET active=p_active,
        authorization_epoch=authorization_epoch + CASE WHEN p_active THEN 0 ELSE 1 END
        WHERE id=p_user RETURNING id,active,authorization_epoch INTO row;
    IF NOT FOUND THEN RAISE EXCEPTION 'user not found' USING ERRCODE='EA404'; END IF;
    RETURN jsonb_build_object('id',row.id,'active',row.active);
END $$;

CREATE FUNCTION console_force_logout(p_admin text, p_user text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE row record; ended bigint;
BEGIN
    PERFORM console_require_admin(p_admin, ARRAY['admin','operator']);
    UPDATE users SET authorization_epoch=authorization_epoch+1 WHERE id=p_user RETURNING id INTO row;
    IF NOT FOUND THEN RAISE EXCEPTION 'user not found' USING ERRCODE='EA404'; END IF;
    UPDATE identity_sessions SET revoked_at=clock_timestamp()
        WHERE user_id=p_user AND revoked_at IS NULL AND expires_at>clock_timestamp();
    GET DIAGNOSTICS ended = ROW_COUNT;
    RETURN jsonb_build_object('id',row.id,'sessions_ended',ended);
END $$;

-- Only published, ordinary plans: never the internal or owner plans, which have their
-- own audited paths. At most 100 points and 365 days, admin only, and not again while
-- an earlier Console grant of the same plan to the same wallet is still running.
CREATE FUNCTION console_issue_plan(p_admin text, p_wallet text, p_plan text, p_key text, p_reason text)
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
    IF v_credits IS NULL OR v_credits < 1 OR v_credits > 100000000 OR v_days IS NULL OR v_days < 1 OR v_days > 365 THEN
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

DO $$ BEGIN
    EXECUTE format('ALTER FUNCTION console_require_admin(text,text[]) SET search_path = %I, pg_temp', current_schema());
    EXECUTE format('ALTER FUNCTION console_set_user_active(text,text,boolean) SET search_path = %I, pg_temp', current_schema());
    EXECUTE format('ALTER FUNCTION console_force_logout(text,text) SET search_path = %I, pg_temp', current_schema());
    EXECUTE format('ALTER FUNCTION console_issue_plan(text,text,text,text,text) SET search_path = %I, pg_temp', current_schema());
END $$;
REVOKE ALL ON FUNCTION console_require_admin(text,text[]) FROM PUBLIC;
REVOKE ALL ON FUNCTION console_set_user_active(text,text,boolean) FROM PUBLIC;
REVOKE ALL ON FUNCTION console_force_logout(text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION console_issue_plan(text,text,text,text,text) FROM PUBLIC;
