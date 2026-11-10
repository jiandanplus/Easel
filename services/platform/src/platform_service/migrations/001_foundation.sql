CREATE TABLE users (
    id text PRIMARY KEY CHECK (length(btrim(id)) BETWEEN 1 AND 255),
    authorization_epoch bigint NOT NULL DEFAULT 0 CHECK (authorization_epoch >= 0),
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE external_identities (
    issuer text NOT NULL CHECK (length(btrim(issuer)) BETWEEN 1 AND 2048),
    subject text NOT NULL CHECK (length(btrim(subject)) BETWEEN 1 AND 255),
    user_id text NOT NULL REFERENCES users(id),
    email text CHECK (email IS NULL OR length(btrim(email)) BETWEEN 1 AND 320),
    email_verified boolean NOT NULL DEFAULT false,
    PRIMARY KEY (issuer, subject)
);
CREATE INDEX external_identities_user_idx ON external_identities(user_id);

CREATE TABLE tenants (
    id text PRIMARY KEY CHECK (length(btrim(id)) BETWEEN 1 AND 255),
    owner_user_id text NOT NULL REFERENCES users(id),
    UNIQUE (id, owner_user_id)
);

CREATE TABLE tenant_members (
    tenant_id text NOT NULL REFERENCES tenants(id),
    user_id text NOT NULL REFERENCES users(id),
    role text NOT NULL CHECK (length(btrim(role)) BETWEEN 1 AND 64),
    active boolean NOT NULL DEFAULT true,
    PRIMARY KEY (tenant_id, user_id)
);

CREATE TABLE billing_accounts (
    id text PRIMARY KEY CHECK (length(btrim(id)) BETWEEN 1 AND 255),
    tenant_id text NOT NULL REFERENCES tenants(id),
    UNIQUE (id, tenant_id)
);

CREATE TABLE products (
    id text PRIMARY KEY CHECK (length(btrim(id)) BETWEEN 1 AND 255),
    enabled boolean NOT NULL DEFAULT true
);

CREATE TABLE product_clients (
    product_id text NOT NULL REFERENCES products(id),
    client_id text NOT NULL CHECK (length(btrim(client_id)) BETWEEN 1 AND 255),
    enabled boolean NOT NULL DEFAULT true,
    PRIMARY KEY (product_id, client_id)
);
