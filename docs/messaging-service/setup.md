# Messaging v1 — Comms Free setup

Join establishes eligibility and sends nothing. A joined tester initiates on the
Free shared line using the workspace's exact first-text/keyword instructions.
TBD is the only reply source: disable Comms automated replies, including any
routing agent's replies. Do not assume an agent object is required or that the
routing text reaches our webhook; confirm the actual Free workspace behavior.

## Account proof before release

Select **Start Free** at <https://comms.osis.co/pricing>. Confirm Free, shared-line
iMessage capability, first-text routing, and the actual allowance/history window.
In <https://comms.osis.co/settings/api-keys>, create a runtime key with `comms_read`
and `comms_send`, scoped to the pilot line if possible. Use a **separate setup
key** with `comms_webhooks`; never install it in the server runtime.

Keep both keys and the shared number in a private local env file outside Git.
The temporary capture tools use `COMMS_SETUP_KEY`, `COMMS_CAPTURE_DIR` and
`COMMS_CAPTURE_SECRET_FILE`. The directory must be private and outside Git.
`scripts/comms_capture.py` accepts only signature-verified events and stores exact
bytes with mode 0600, without logging contacts/bodies. Before registration it
returns 503. It sends no messages and is not a production deployment.

With the controlled HTTPS capture receiver running:

```bash
uv run --env-file /private/path/comms.env python scripts/comms_setup.py register HTTPS_CAPTURE_URL/webhooks/comms
uv run --env-file /private/path/comms.env python scripts/comms_setup.py test HTTPS_CAPTURE_URL/webhooks/comms
```

Registration subscribes to `comms.message.received` and `comms.ping`. Store its
returned secret privately. After the signed ping, coordinate one consenting
joined tester privately: follow the workspace's exact routing instructions, then
send one ordinary iMessage if routing consumes the initial text. Read the same
message using the runtime key with `conversation_id` and `direction=inbound`.
Compare native `data.message.id`, line, author phone, audience, channel,
conversation, attachments and original time. Confirm history updates cannot admit
reactions/edits as new texts. The current adapter uses **documented fields**, not
yet confirmed against the actual account. Treat fixtures in tests as synthetic.

Only after identity/conversation verification, send one explicit iMessage reply
using that conversation and a saved UUID idempotency key. Record its exact-ID
outbound history and have the tester confirm the same thread/number. Do not log
or commit real contacts, message bodies, signatures, keys or raw responses.
Publish only sanitized fixtures with the observed field shapes preserved. Clean
up the temporary registration/tunnel after capture; do not confuse it with the
final runtime webhook or leave it as the production endpoint.

## Runtime and release

Configure server-only `COMMS_API_KEY`, `COMMS_WEBHOOK_SECRET`, `COMMS_LINE_E164`
and timezone-aware `MESSAGING_ACTIVATED_AT` as in `.env.example`. Set activation
once and preserve it. The line and scoped runtime key establish the intended
workspace/line; verified inbound records must match that line and direct
`imessage` classification. Sender identity comes from the provider record and
canonical joined User lookup, never message text or Agent arguments.

Apply the reviewed **unapplied** migration `20261108173259_messaging_service.sql`
before compatible deployment. The applied User migration is unchanged. Do not
reset databases. Use the existing restricted backend role, TLS and direct or
session-pooler connection; session advisory ownership is incompatible with
transaction pooling. Keep Render auto-deploy off and use one existing process.
Configure the final HTTPS `/webhooks/comms` registration and its own secret.
Startup verifies schema/role readiness. `/healthz` checks the required loop;
provider outages appear in logs/state without alone failing process health.

Both founders must explicitly approve merging. Full hosted proof is TBD-20 after
backend review/merge. No automatic welcome or pilot welcome command remains.

## Tests and operations

```bash
PATH=/opt/homebrew/opt/postgresql@17/bin:$PATH uv run --locked python scripts/run_database_tests.py -q
uv run --locked ruff check src tests scripts
uv run --locked ruff format --check src tests scripts
```

The harness applies release migrations to a fresh disposable local cluster and
runs under restricted SQL credentials. It never sends real messages. Preserve
User regression tests. Use a new local test cluster, never hosted credentials.

Inspect safe delivery fields in an authorized SQL session:

```sql
SELECT state,count(*),min(created_at) AS oldest
FROM messaging.messages WHERE direction='outbound' GROUP BY state;
SELECT id,state,attempt_id,attempt_count,error_code,next_action_at,first_attempt_at,
       accepted_at,sent_observed_at FROM messaging.messages
WHERE direction='outbound' AND state NOT IN ('sent','failed') ORDER BY queue_sequence;
```

The outbound UUID is the stable `Idempotency-Key`; retries keep conversation,
body and explicit channel fixed. Unknown acceptance without ID retries with that
key only within 24 hours of first attempt; after that it stays uncertain for
reconciliation. Known IDs are read through outbound conversation history and
matched exactly; missing/unknown status never releases the route. Do not requeue
with a new key based on generic text and approximate time. Terminal failure or
sent observation releases that conversation route; other routes progress alone.

Shared API pacing is 1.1 seconds between completed requests. This is a conservative
operational setting below the documented request budget, not a latency target.
429 honors JSON `retry_after`, default 60 seconds; quota pauses sends one hour;
auth/config pauses work 60 seconds. Pauses persist in message due/error fields.
The actual Free entitlement/reset behavior still needs account confirmation.

History checks run on start/wake and about once a minute while awake, with fixed
activation `since`, inbound direction and limit 200. Returned records are handled
oldest first. At 200, logs report possibly incomplete coverage; there is no
invented pagination. Recovery cannot cover messages outside provider retention
or older messages hidden by the cap. Re-enable a disabled webhook after fixing
its cause; provider retries and history complement one another.

Logs omit bodies, contacts and provider errors/URLs. Receipt/commit/replay,
message/attempt IDs, queue age, API duration and state/error observations correlate
stages. Acceptance is not sent or delivery. Render Free sleep pauses processing;
pending rows do not wake it. Measure actual ordinary/burst/interleaved/quiet-wake
latency during TBD-20; polling adds observation delay.
