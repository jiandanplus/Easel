-- Interface settings entered in the console, one page per desktop channel (decision 0015).
-- The console seals supplier keys with a public key; only the central service can open them.
ALTER TABLE console_operations DROP CONSTRAINT console_operations_action_check;
ALTER TABLE console_operations ADD CONSTRAINT console_operations_action_check
 CHECK(action IN ('plan.publish','offer.publish','offer.withdraw','model.publish','model.state','execution.review',
    'allowance.issue','user.status','user.logout','user.note','plan.issue',
    'redemption.create','redemption.disable','admin.approve','admin.disable',
    'model.managed','model.price','model.trial','media.state','provider.config'));
ALTER TABLE console_operations DROP CONSTRAINT console_operations_target_type_check;
ALTER TABLE console_operations ADD CONSTRAINT console_operations_target_type_check
 CHECK(target_type IN ('plan','offer','model','execution','wallet','user','redemption','admin','provider'));

-- Shown in the console so a page can say which address it currently uses. Never a key.
ALTER TABLE provider_catalog ADD COLUMN base_url text;

-- Append only: the newest row for a channel is its current setting.
CREATE TABLE provider_configs (
    id text PRIMARY KEY CHECK(id ~ '^[a-f0-9]{32}$'),
    channel text NOT NULL CHECK(channel IN ('chat','transcribe','speech','image','video','music')),
    base_url text NOT NULL CHECK(length(base_url) BETWEEN 8 AND 512),
    sealed_secret text CHECK(sealed_secret IS NULL OR length(sealed_secret) BETWEEN 1 AND 8192),
    key_hint text CHECK(key_hint IS NULL OR length(key_hint) BETWEEN 1 AND 4),
    options jsonb NOT NULL DEFAULT '{}'::jsonb,
    operator_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK((sealed_secret IS NULL)=(key_hint IS NULL))
);
CREATE INDEX provider_configs_current ON provider_configs(channel,created_at DESC,id DESC);
CREATE TRIGGER provider_configs_immutable BEFORE UPDATE OR DELETE ON provider_configs
FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER provider_configs_no_truncate BEFORE TRUNCATE ON provider_configs
FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();

-- What the central service found when it tried the saved setting.
CREATE TABLE provider_config_status (
    channel text PRIMARY KEY CHECK(channel IN ('chat','transcribe','speech','image','video','music')),
    config_id text NOT NULL,
    state text NOT NULL CHECK(state IN ('ok','auth_failed','unreachable','no_model_list','invalid')),
    models jsonb NOT NULL DEFAULT '[]'::jsonb,
    checked_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
