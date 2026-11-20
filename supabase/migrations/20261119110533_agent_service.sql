CREATE SCHEMA agent_service;
REVOKE ALL ON SCHEMA agent_service FROM PUBLIC, anon, authenticated;
GRANT USAGE ON SCHEMA agent_service TO tbd_user_backend;

CREATE TABLE agent_service.user_state (
 user_id uuid PRIMARY KEY REFERENCES user_service.users(id),
 mode text NOT NULL CHECK (mode IN ('fixed','agent','fallback')),
 enrollment_id uuid NOT NULL,
 enrolled_at timestamptz NOT NULL,
 account_ref text NOT NULL,
 peer_ref text NOT NULL,
 conversation_id text NOT NULL,
 brief jsonb,
 preferences jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(preferences) = 'object'),
 paused boolean NOT NULL DEFAULT false,
 watermark timestamptz,
 reset_source_at timestamptz,
 reset_sequence bigint
);

CREATE TABLE agent_service.turns (
 inbound_id uuid PRIMARY KEY REFERENCES messaging.messages(id),
 enrollment_id uuid NOT NULL,
 outcome text CHECK (outcome IN ('model','fallback','stale','blocked','control','transaction','revoked')),
 completed_at timestamptz,
 inference_deadline timestamptz NOT NULL,
 execution_config jsonb NOT NULL,
 attempt_id uuid,
 lease_until timestamptz,
 attempts_left integer NOT NULL CHECK (attempts_left BETWEEN 0 AND 2),
 granted_at timestamptz,
 question jsonb,
 delivery_suppressed boolean NOT NULL DEFAULT false,
 delivery_grant uuid,
 submission_possible boolean NOT NULL DEFAULT false,
 CHECK ((outcome IS NULL) = (completed_at IS NULL)),
 CHECK ((attempt_id IS NULL) = (lease_until IS NULL)),
 CHECK (granted_at IS NULL OR attempt_id IS NOT NULL)
);
CREATE INDEX agent_pending_turns ON agent_service.turns(inbound_id) WHERE outcome IS NULL;

REVOKE ALL ON ALL TABLES IN SCHEMA agent_service FROM PUBLIC, anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA agent_service REVOKE ALL ON TABLES FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA agent_service TO tbd_user_backend;
ALTER TABLE agent_service.user_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_service.turns ENABLE ROW LEVEL SECURITY;
CREATE POLICY backend_user_state ON agent_service.user_state TO tbd_user_backend USING (true) WITH CHECK (true);
CREATE POLICY backend_turns ON agent_service.turns TO tbd_user_backend USING (true) WITH CHECK (true);
