CREATE TABLE identity_transactions (
 id text PRIMARY KEY,
 product_id text NOT NULL,
 client_id text NOT NULL,
 callback_uri text NOT NULL,
 handoff_challenge text NOT NULL,
 local_state text NOT NULL,
 expires_at timestamptz NOT NULL,
 state_hash text UNIQUE,
 cookie_hash text,
 nonce_hash text,
 consumed boolean NOT NULL DEFAULT false,
 FOREIGN KEY(product_id,client_id) REFERENCES product_clients(product_id,client_id)
);
CREATE TABLE identity_handoffs (
 code_hash text PRIMARY KEY,
 transaction_id text NOT NULL UNIQUE REFERENCES identity_transactions(id),
 user_id text NOT NULL REFERENCES users(id),
 tenant_id text NOT NULL,
 billing_account_id text NOT NULL,
 issuer text NOT NULL,
 subject text NOT NULL,
 expires_at timestamptz NOT NULL,
 consumed boolean NOT NULL DEFAULT false,
 FOREIGN KEY(tenant_id,user_id) REFERENCES tenant_members(tenant_id,user_id),
 FOREIGN KEY(billing_account_id,tenant_id) REFERENCES billing_accounts(id,tenant_id),
 FOREIGN KEY(issuer,subject) REFERENCES external_identities(issuer,subject)
);
CREATE TABLE identity_sessions (
 id text PRIMARY KEY,
 user_id text NOT NULL REFERENCES users(id),
 tenant_id text NOT NULL,
 billing_account_id text NOT NULL,
 product_id text NOT NULL,
 client_id text NOT NULL,
 authorization_epoch bigint NOT NULL CHECK(authorization_epoch >= 0),
 issuer text NOT NULL,
 subject text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 expires_at timestamptz NOT NULL,
 revoked_at timestamptz,
 FOREIGN KEY(product_id,client_id) REFERENCES product_clients(product_id,client_id),
 FOREIGN KEY(tenant_id,user_id) REFERENCES tenant_members(tenant_id,user_id),
 FOREIGN KEY(billing_account_id,tenant_id) REFERENCES billing_accounts(id,tenant_id),
 FOREIGN KEY(issuer,subject) REFERENCES external_identities(issuer,subject)
);
CREATE TABLE identity_tokens (
 token_hash text PRIMARY KEY,
 session_id text NOT NULL REFERENCES identity_sessions(id),
 kind text NOT NULL CHECK(kind IN ('access','refresh')),
 expires_at timestamptz NOT NULL,
 consumed boolean NOT NULL DEFAULT false
);
CREATE INDEX identity_tokens_session_idx ON identity_tokens(session_id);
CREATE INDEX identity_sessions_user_idx ON identity_sessions(user_id);
