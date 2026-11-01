-- User service private schema. Apply explicitly as the migration owner before deployment.
CREATE SCHEMA user_service;
REVOKE ALL ON SCHEMA user_service FROM PUBLIC;
CREATE TABLE user_service.users (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 auth_user_id uuid UNIQUE REFERENCES auth.users(id),
 name text NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 200),
 type text NOT NULL CHECK (type IN ('individual','business')),
 phone text UNIQUE CHECK (phone ~ '^\+1[2-9][0-9]{2}[2-9][0-9]{6}$'),
 email text UNIQUE CHECK (email = lower(trim(email)) AND email ~ '^[^[:space:]@]+@[^[:space:]@]+[.][^[:space:]@]+$' AND length(email) <= 254),
 seed_key text UNIQUE CHECK (length(trim(seed_key)) BETWEEN 1 AND 200),
 revision integer NOT NULL DEFAULT 0 CHECK (revision >= 0),
 created_at timestamptz NOT NULL DEFAULT now(),
 CHECK ((seed_key IS NOT NULL AND type='business' AND auth_user_id IS NULL)
     OR (seed_key IS NULL AND auth_user_id IS NOT NULL AND phone IS NOT NULL AND email IS NOT NULL))
);
CREATE TABLE user_service.invitations (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 email text NOT NULL UNIQUE CHECK (email=lower(trim(email)) AND email ~ '^[^[:space:]@]+@[^[:space:]@]+[.][^[:space:]@]+$' AND length(email) <= 254),
 phone text NOT NULL UNIQUE CHECK (phone ~ '^\+1[2-9][0-9]{2}[2-9][0-9]{6}$'),
 name text NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 200),
 type text NOT NULL CHECK (type IN ('individual','business')),
 auth_user_id uuid UNIQUE REFERENCES auth.users(id),
 joined_user_id uuid UNIQUE REFERENCES user_service.users(id),
 created_at timestamptz NOT NULL DEFAULT now(),
 CHECK (joined_user_id IS NULL OR auth_user_id IS NOT NULL)
);
CREATE TABLE user_service.addresses (
 id uuid PRIMARY KEY,
 user_id uuid NOT NULL REFERENCES user_service.users(id),
 label text CHECK (length(trim(label)) BETWEEN 1 AND 200),
 recipient text NOT NULL CHECK (length(trim(recipient)) BETWEEN 1 AND 200),
 line1 text NOT NULL CHECK (length(trim(line1)) BETWEEN 1 AND 200),
 line2 text CHECK (length(trim(line2)) BETWEEN 1 AND 200),
 city text NOT NULL CHECK (length(trim(city)) BETWEEN 1 AND 200),
 region text NOT NULL CHECK (region ~ '^[A-Z]{2}$'),
 postal_code text NOT NULL CHECK (postal_code ~ '^[0-9]{5}(-[0-9]{4})?$'),
 country_code text NOT NULL CHECK (country_code='US'),
 is_default boolean NOT NULL
);
CREATE INDEX addresses_user_id ON user_service.addresses(user_id);
CREATE UNIQUE INDEX one_default_per_user ON user_service.addresses(user_id) WHERE is_default;
REVOKE ALL ON ALL TABLES IN SCHEMA user_service FROM PUBLIC, anon, authenticated;
REVOKE ALL ON SCHEMA user_service FROM anon, authenticated;

-- The login password is set out of band. This role cannot administer Auth or DDL.
CREATE ROLE tbd_user_backend LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT USAGE ON SCHEMA user_service TO tbd_user_backend;
GRANT SELECT, INSERT, UPDATE ON user_service.users, user_service.invitations TO tbd_user_backend;
GRANT SELECT, INSERT, UPDATE, DELETE ON user_service.addresses TO tbd_user_backend;
ALTER TABLE user_service.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_service.invitations ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_service.addresses ENABLE ROW LEVEL SECURITY;
CREATE POLICY backend_users ON user_service.users TO tbd_user_backend USING (true) WITH CHECK (true);
CREATE POLICY backend_invitations ON user_service.invitations TO tbd_user_backend USING (true) WITH CHECK (true);
CREATE POLICY backend_addresses ON user_service.addresses TO tbd_user_backend USING (true) WITH CHECK (true);
ALTER DEFAULT PRIVILEGES IN SCHEMA user_service REVOKE ALL ON TABLES FROM PUBLIC, anon, authenticated;
