-- Console admins may issue the fixed owner allowance from the web (decision 0011).
-- The Console role still has no ledger privileges: it may only call this function,
-- which re-checks the admin, issues exactly the 022 plan and leaves the 022
-- consistency triggers to verify the rows. Runtime and PUBLIC cannot call it.
ALTER TABLE console_operations DROP CONSTRAINT console_operations_action_check;
ALTER TABLE console_operations ADD CONSTRAINT console_operations_action_check
 CHECK(action IN ('plan.publish','offer.publish','offer.withdraw','model.publish','model.state','execution.review','allowance.issue'));
ALTER TABLE console_operations DROP CONSTRAINT console_operations_target_type_check;
ALTER TABLE console_operations ADD CONSTRAINT console_operations_target_type_check
 CHECK(target_type IN ('plan','offer','model','execution','wallet'));

CREATE FUNCTION console_issue_owner_allowance(p_admin text, p_wallet text, p_key text, p_reason text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE
    plan jsonb := '{"id":"easel-owner-allowance-v1","name":"所有者额度，非付款","version":"1","included_products":["easel"],"features":["text","image"],"credits":"100000000","days":365,"concurrency":1,"test_only":false}'::jsonb;
    operator text; issuance text; src text; started timestamptz; ending timestamptz;
    mid text := gen_random_uuid()::text; lid text := gen_random_uuid()::text;
    gid text := gen_random_uuid()::text; jid text := gen_random_uuid()::text;
BEGIN
    IF NOT EXISTS(SELECT 1 FROM console_admins WHERE id=p_admin AND active AND role='admin') THEN
        RAISE EXCEPTION 'console admin required' USING ERRCODE='EA403';
    END IF;
    IF p_key !~ '^[a-f0-9]{64}$' OR p_reason IS NULL OR length(btrim(p_reason)) NOT BETWEEN 1 AND 256 THEN
        RAISE EXCEPTION 'invalid allowance request' USING ERRCODE='EA422';
    END IF;
    PERFORM 1 FROM billing_accounts WHERE id=p_wallet FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'wallet not found' USING ERRCODE='EA404'; END IF;
    IF EXISTS(SELECT 1 FROM owner_grants g WHERE g.billing_account_id=p_wallet AND g.expires_at>clock_timestamp()
        AND NOT EXISTS(SELECT 1 FROM membership_revocations r WHERE r.membership_id=g.membership_id)) THEN
        RAISE EXCEPTION 'owner allowance active' USING ERRCODE='EA409';
    END IF;
    IF (SELECT coalesce(sum(issued),0) FROM credit_lots WHERE billing_account_id=p_wallet) + 100000000 > 9223372036854775807 THEN
        RAISE EXCEPTION 'wallet capacity exceeded' USING ERRCODE='EA410';
    END IF;
    operator := 'console:' || p_admin;
    issuance := 'console:' || p_admin || ':' || p_key;
    src := 'owner-allowance:' || encode(sha256(convert_to(issuance, 'UTF8')), 'hex');
    INSERT INTO plans(id,name,version,definition,created_by) VALUES (plan->>'id',plan->>'name',plan->>'version',plan,operator)
        ON CONFLICT DO NOTHING;
    IF (SELECT definition FROM plans WHERE id=plan->>'id') IS DISTINCT FROM plan THEN
        RAISE EXCEPTION 'owner allowance plan diverged' USING ERRCODE='23514';
    END IF;
    started := clock_timestamp(); ending := started + interval '365 days';
    INSERT INTO memberships(id,billing_account_id,plan_id,operator_id,issuance_key,starts_at,expires_at)
        VALUES (mid,p_wallet,plan->>'id',operator,src,started,ending);
    INSERT INTO credit_lots(id,billing_account_id,membership_id,issued,available,expires_at,test_only)
        VALUES (lid,p_wallet,mid,100000000,100000000,ending,false);
    INSERT INTO credit_journals(id,billing_account_id,kind,source,operator_id) VALUES (jid,p_wallet,'issue',mid,operator);
    INSERT INTO credit_entries(journal_id,billing_account_id,bucket,delta)
        VALUES (jid,p_wallet,'issuance',-100000000),(jid,p_wallet,'available',100000000);
    INSERT INTO owner_grants(id,billing_account_id,membership_id,lot_id,operator_id,issuance_key,reason,approval_reference,source,starts_at,expires_at)
        VALUES (gid,p_wallet,mid,lid,operator,issuance,btrim(p_reason),'console-web',src,started,ending);
    -- Run the deferred ledger and audit checks now, as the owner: at commit they
    -- would run as the Console role, which may not read those tables. Then restore
    -- the default, so the Console's own audit rows are still checked at commit
    -- (every deferrable constraint in this schema is INITIALLY DEFERRED).
    SET CONSTRAINTS ALL IMMEDIATE;
    SET CONSTRAINTS ALL DEFERRED;
    RETURN jsonb_build_object('id',gid,'membership_id',mid,'billing_account_id',p_wallet,'plan_id',plan->>'id',
        'credits','100000000','features',plan->'features','starts_at',started,'expires_at',ending,
        'test_only',false,'funding_source','owner_allowance','notice',plan->>'name');
END $$;
-- A SECURITY DEFINER function must not search pg_temp first or any schema but its own.
DO $$ BEGIN
    EXECUTE format('ALTER FUNCTION console_issue_owner_allowance(text,text,text,text) SET search_path = %I, pg_temp', current_schema());
END $$;

REVOKE ALL ON FUNCTION console_issue_owner_allowance(text,text,text,text) FROM PUBLIC;
