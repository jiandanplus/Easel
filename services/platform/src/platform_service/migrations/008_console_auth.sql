-- Independent Console BFF identity. Ordinary product users have no implicit ACL.
CREATE TABLE console_admins (
 id text PRIMARY KEY CHECK(id ~ '^[a-f0-9]{32}$'),
 issuer text NOT NULL CHECK(length(issuer) BETWEEN 1 AND 2048),
 subject text NOT NULL CHECK(length(subject) BETWEEN 1 AND 255),
 role text NOT NULL CHECK(role IN ('admin','operator','viewer')),
 active boolean NOT NULL DEFAULT true,
 authorization_epoch bigint NOT NULL DEFAULT 0 CHECK(authorization_epoch>=0),
 created_by text NOT NULL CHECK(length(btrim(created_by)) BETWEEN 1 AND 255),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(issuer,subject)
);
CREATE TABLE console_login_transactions (
 id text PRIMARY KEY CHECK(id ~ '^[a-f0-9]{32}$'),
 state_hash text NOT NULL UNIQUE CHECK(state_hash ~ '^[a-f0-9]{64}$'),
 nonce_hash text NOT NULL CHECK(nonce_hash ~ '^[a-f0-9]{64}$'),
 cookie_hash text NOT NULL CHECK(cookie_hash ~ '^[a-f0-9]{64}$'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 expires_at timestamptz NOT NULL,
 consumed boolean NOT NULL DEFAULT false
);
CREATE TABLE console_login_requests (
 id text PRIMARY KEY CHECK(id ~ '^[a-f0-9]{32}$'),
 issuer text NOT NULL,
 subject text NOT NULL,
 subject_hash text NOT NULL CHECK(subject_hash ~ '^[a-f0-9]{64}$'),
 status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved')),
 verified_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 expires_at timestamptz NOT NULL,
 approved_admin_id text REFERENCES console_admins(id),
 CHECK((status='pending' AND approved_admin_id IS NULL) OR (status='approved' AND approved_admin_id IS NOT NULL))
);
CREATE TABLE console_sessions (
 session_hash text PRIMARY KEY CHECK(session_hash ~ '^[a-f0-9]{64}$'),
 admin_id text NOT NULL REFERENCES console_admins(id),
 csrf_token text NOT NULL CHECK(length(csrf_token)=43),
 authorization_epoch bigint NOT NULL CHECK(authorization_epoch>=0),
 created_at timestamptz NOT NULL,
 expires_at timestamptz NOT NULL,
 revoked_at timestamptz
);
CREATE INDEX console_sessions_admin ON console_sessions(admin_id);
CREATE TABLE console_acl_events (
 id text PRIMARY KEY,
 admin_id text NOT NULL REFERENCES console_admins(id),
 request_id text REFERENCES console_login_requests(id),
 action text NOT NULL CHECK(action IN ('approve','disable')),
 role text NOT NULL CHECK(role IN ('admin','operator','viewer')),
 operator_id text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER console_acl_events_immutable BEFORE UPDATE OR DELETE ON console_acl_events FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER console_acl_events_no_truncate BEFORE TRUNCATE ON console_acl_events FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();
