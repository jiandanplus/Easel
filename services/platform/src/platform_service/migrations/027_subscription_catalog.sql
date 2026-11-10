-- Unified model catalog and plan-level model access (decision 0014).
-- Secrets, endpoints and adapters stay in server files; these tables only say which
-- configured provider serves a model, what it currently costs, and who may try it.
ALTER TABLE console_operations DROP CONSTRAINT console_operations_action_check;
ALTER TABLE console_operations ADD CONSTRAINT console_operations_action_check
 CHECK(action IN ('plan.publish','offer.publish','offer.withdraw','model.publish','model.state','execution.review',
    'allowance.issue','user.status','user.logout','user.note','plan.issue',
    'redemption.create','redemption.disable','admin.approve','admin.disable',
    'model.managed','model.price','model.trial','media.state'));

-- Written by the central service at start: what it is configured to reach, no secrets.
CREATE TABLE provider_catalog (
    kind text NOT NULL CHECK(kind IN ('execution','media')),
    provider_id text NOT NULL CHECK(length(btrim(provider_id)) BETWEEN 1 AND 64),
    provider_version text NOT NULL CHECK(length(btrim(provider_version)) BETWEEN 1 AND 128),
    adapter text,
    protocol_version text,
    capabilities jsonb NOT NULL DEFAULT '[]'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(kind,provider_id)
);

CREATE TABLE managed_model_bindings (
    kind text NOT NULL CHECK(kind IN ('execution','media')),
    model_id text NOT NULL CHECK(model_id ~ '^[A-Za-z0-9_.:/-]{1,128}$'),
    provider_id text NOT NULL,
    upstream_model text NOT NULL CHECK(length(upstream_model) BETWEEN 1 AND 128 AND upstream_model !~ '[[:space:][:cntrl:]]'),
    operator_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(kind,model_id)
);
CREATE TRIGGER managed_model_bindings_immutable BEFORE UPDATE OR DELETE ON managed_model_bindings
FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();

-- Append only. The newest row whose effective_at has passed is the model's price.
CREATE TABLE model_prices (
    id text PRIMARY KEY CHECK(id ~ '^[a-f0-9]{32}$'),
    kind text NOT NULL CHECK(kind IN ('execution','media')),
    model_id text NOT NULL,
    rate jsonb NOT NULL,
    reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 512),
    operator_id text NOT NULL,
    effective_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX model_prices_current ON model_prices(kind,model_id,effective_at DESC,created_at DESC);
CREATE TRIGGER model_prices_immutable BEFORE UPDATE OR DELETE ON model_prices
FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER model_prices_no_truncate BEFORE TRUNCATE ON model_prices
FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();

-- A disabled model is visible only to these wallets, so it can be tried before release.
CREATE TABLE model_trial_access (
    kind text NOT NULL CHECK(kind IN ('execution','media')),
    model_id text NOT NULL,
    billing_account_id text NOT NULL REFERENCES billing_accounts(id),
    operator_id text NOT NULL,
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(kind,model_id,billing_account_id)
);

-- The exact price a job was admitted at. NULL on jobs admitted before this migration.
ALTER TABLE execution_jobs ADD COLUMN price jsonb;
ALTER TABLE media_jobs ADD COLUMN price jsonb;
