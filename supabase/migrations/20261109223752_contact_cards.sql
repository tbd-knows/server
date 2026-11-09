-- Separate obligations: card failures must never rewrite a normal reply.
CREATE TABLE messaging.contact_cards (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 account_ref text NOT NULL CHECK (account_ref ~ '^\+[1-9][0-9]{7,14}$'),
 conversation_id text NOT NULL CHECK (octet_length(conversation_id) BETWEEN 1 AND 256),
 user_id uuid NOT NULL REFERENCES user_service.users(id),
 peer_ref text NOT NULL CHECK (peer_ref ~ '^\+[1-9][0-9]{7,14}$'),
 channel text NOT NULL DEFAULT 'imessage' CHECK (channel='imessage'),
 reply_direction text GENERATED ALWAYS AS ('outbound'::text) STORED,
 successful_reply_id uuid NOT NULL,
 idempotency_key text NOT NULL UNIQUE CHECK (length(idempotency_key) BETWEEN 1 AND 200),
 payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
 state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','sending','uncertain','sent','failed')),
 provider_message_id text CHECK (octet_length(provider_message_id) BETWEEN 1 AND 256),
 response_body text CHECK (octet_length(response_body)<=65536),
 attempt_id uuid,
 attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count>=0),
 first_attempt_at timestamptz,
 next_action_at timestamptz NOT NULL DEFAULT now(),
 error_code text CHECK (error_code ~ '^[a-z_]{1,64}$'),
 observed_status text CHECK (observed_status IN ('submitted','sent','delivered','read','failed','suppressed','unknown')),
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE (account_ref,conversation_id),
 UNIQUE (account_ref,provider_message_id),
 FOREIGN KEY (successful_reply_id,user_id,channel,account_ref,peer_ref,conversation_id,reply_direction)
 REFERENCES messaging.messages(id,user_id,channel,account_ref,peer_ref,conversation_id,direction),
 CHECK (state <> 'sent' OR (provider_message_id IS NOT NULL AND response_body IS NOT NULL)),
 CHECK (provider_message_id IS NULL OR response_body IS NOT NULL),
 CHECK (state <> 'sending' OR attempt_id IS NOT NULL),
 CHECK (attempt_count=0 OR first_attempt_at IS NOT NULL)
);
REVOKE ALL ON messaging.contact_cards FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT ON messaging.contact_cards TO tbd_user_backend;
GRANT UPDATE (state,provider_message_id,response_body,attempt_id,attempt_count,first_attempt_at,
 next_action_at,error_code,observed_status,updated_at) ON messaging.contact_cards TO tbd_user_backend;
ALTER TABLE messaging.contact_cards ENABLE ROW LEVEL SECURITY;
CREATE POLICY backend_contact_cards ON messaging.contact_cards TO tbd_user_backend USING (true) WITH CHECK (true);
