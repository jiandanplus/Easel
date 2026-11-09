-- Voices a supplier reported for a connection, shown in the console for the admin to pick from (decision 0017).
ALTER TABLE provider_config_status ADD COLUMN voices jsonb NOT NULL DEFAULT '[]'::jsonb;
