-- Messaging v1: history and delivery obligations share one private table.
-- Apply explicitly before deploying compatible code. Never reset User data.
CREATE SCHEMA messaging;
REVOKE ALL ON SCHEMA messaging FROM PUBLIC, anon, authenticated;
CREATE TABLE messaging.messages (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 user_id uuid NOT NULL REFERENCES user_service.users(id),
 channel text NOT NULL CHECK (channel = 'imessage'),
 direction text NOT NULL CHECK (direction IN ('inbound', 'outbound')),
 dedupe_key text NOT NULL CHECK (octet_length(dedupe_key) BETWEEN 1 AND 512),
 account_ref text NOT NULL CHECK (account_ref ~ '^\+[1-9][0-9]{7,14}$'),
 peer_ref text NOT NULL CHECK (peer_ref ~ '^\+[1-9][0-9]{7,14}$'),
 conversation_id text NOT NULL CHECK (octet_length(conversation_id) BETWEEN 1 AND 256),
 text text,
 has_unsupported_content boolean NOT NULL DEFAULT false,
 reply_to_message_id uuid,
 reply_to_direction text GENERATED ALWAYS AS
   (CASE WHEN reply_to_message_id IS NOT NULL THEN 'inbound'::text END) STORED,
 provider_message_id text CHECK (octet_length(provider_message_id) BETWEEN 1 AND 256),
 state text NOT NULL,
 queue_sequence bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
 attempt_id uuid,
 attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
 next_action_at timestamptz NOT NULL DEFAULT now(),
 error_code text CHECK (error_code ~ '^[a-z_]{1,64}$'),
 observed_status text CHECK (observed_status IN ('submitted','sent','delivered','failed','suppressed','unknown')),
 source_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 attempt_at timestamptz,
 first_attempt_at timestamptz,
 accepted_at timestamptz,
 sent_observed_at timestamptz,
 UNIQUE (channel, direction, dedupe_key),
 UNIQUE (channel, direction, provider_message_id),
 UNIQUE (id, user_id, channel, account_ref, peer_ref, conversation_id, direction),
 FOREIGN KEY (reply_to_message_id, user_id, channel, account_ref, peer_ref, conversation_id, reply_to_direction)
   REFERENCES messaging.messages(id, user_id, channel, account_ref, peer_ref, conversation_id, direction),
 CHECK (
   (direction = 'inbound' AND state = 'received' AND source_at IS NOT NULL
    AND provider_message_id IS NOT NULL AND reply_to_message_id IS NULL
    AND attempt_id IS NULL AND attempt_count = 0
    AND (has_unsupported_content OR (text IS NOT NULL AND octet_length(text) > 0))
    AND (text IS NULL OR octet_length(text) <= 65536))
   OR
   (direction = 'outbound' AND state IN ('pending','sending','provider_queued','uncertain','sent','failed')
    AND NOT has_unsupported_content AND text IS NOT NULL AND length(text) BETWEEN 1 AND 5000)
 ),
 CHECK (state NOT IN ('sending','provider_queued','uncertain','sent') OR attempt_id IS NOT NULL),
 CHECK (state NOT IN ('provider_queued','sent') OR provider_message_id IS NOT NULL),
 CHECK (state <> 'sent' OR sent_observed_at IS NOT NULL)
);
CREATE INDEX messages_due_work ON messaging.messages(next_action_at, queue_sequence)
 WHERE direction = 'outbound' AND state NOT IN ('sent','failed');
CREATE INDEX messages_user_history ON messaging.messages(user_id, queue_sequence);
REVOKE ALL ON ALL TABLES IN SCHEMA messaging FROM PUBLIC, anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA messaging FROM PUBLIC, anon, authenticated;
GRANT USAGE ON SCHEMA messaging TO tbd_user_backend;
GRANT SELECT, INSERT ON messaging.messages TO tbd_user_backend;
-- Immutable identity/content/route columns cannot be rewritten by the runtime.
GRANT UPDATE (provider_message_id, state, attempt_id, attempt_count, next_action_at,
 error_code, observed_status, updated_at, attempt_at, first_attempt_at, accepted_at, sent_observed_at)
 ON messaging.messages TO tbd_user_backend;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA messaging TO tbd_user_backend;
ALTER TABLE messaging.messages ENABLE ROW LEVEL SECURITY;
CREATE POLICY backend_messages ON messaging.messages TO tbd_user_backend USING (true) WITH CHECK (true);
ALTER DEFAULT PRIVILEGES IN SCHEMA messaging REVOKE ALL ON TABLES FROM PUBLIC, anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA messaging REVOKE ALL ON SEQUENCES FROM PUBLIC, anon, authenticated;
