-- A published plan is never edited or deleted (members hold it). Retiring one stops new
-- issuance, new redemption batches and new offers; the newest row is the plan's state.
CREATE TABLE plan_states (
    id text PRIMARY KEY CHECK(id ~ '^[a-f0-9]{32}$'),
    plan_id text NOT NULL REFERENCES plans(id),
    retired boolean NOT NULL,
    replaced_by text REFERENCES plans(id),
    operator_id text NOT NULL CHECK(length(operator_id) BETWEEN 1 AND 255),
    reason text NOT NULL CHECK(length(reason) BETWEEN 1 AND 512),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK(replaced_by IS NULL OR (retired AND replaced_by <> plan_id))
);
CREATE INDEX plan_states_latest ON plan_states(plan_id, created_at DESC, id DESC);
CREATE TRIGGER plan_states_immutable BEFORE UPDATE OR DELETE ON plan_states FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER plan_states_no_truncate BEFORE TRUNCATE ON plan_states FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();

ALTER TABLE console_operations DROP CONSTRAINT console_operations_action_check;
ALTER TABLE console_operations ADD CONSTRAINT console_operations_action_check
 CHECK(action IN ('plan.publish','offer.publish','offer.withdraw','model.publish','model.state','execution.review',
    'allowance.issue','user.status','user.logout','user.note','plan.issue',
    'redemption.create','redemption.disable','admin.approve','admin.disable',
    'model.managed','model.price','model.trial','media.state','provider.config','model.import',
    'plan.revise','plan.state'));
