-- Several connections per platform on a page, each with its own name and key (decision 0017).
-- The empty account is the connection a platform already had; its provider id does not change.
ALTER TABLE provider_configs ADD COLUMN account text NOT NULL DEFAULT '' CHECK(account ~ '^([a-f0-9]{8})?$');
ALTER TABLE provider_configs ADD COLUMN name text NOT NULL DEFAULT '' CHECK(length(name)<=64);
DROP INDEX provider_configs_current;
CREATE INDEX provider_configs_current ON provider_configs(channel,platform,account,created_at DESC,id DESC);

ALTER TABLE provider_config_status ADD COLUMN account text NOT NULL DEFAULT '' CHECK(account ~ '^([a-f0-9]{8})?$');
ALTER TABLE provider_config_status DROP CONSTRAINT provider_config_status_pkey;
ALTER TABLE provider_config_status ADD PRIMARY KEY(channel,platform,account);
