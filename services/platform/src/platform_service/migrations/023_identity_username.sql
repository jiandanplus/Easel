-- Product login keeps the signed username claim next to the email, so the private
-- Console can name people. It is display data only and never authorizes anything.
-- Existing rows stay NULL until that person signs in again.
ALTER TABLE external_identities ADD COLUMN username text
    CHECK (username IS NULL OR length(btrim(username)) BETWEEN 1 AND 128);
