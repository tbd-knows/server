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
reactions/edits as new texts. The captured Pilot A records omit audience and attachments. The approved plain-text
pilot uses a server-owned conversation/phone allowlist as described below; this
does not establish general provider omission semantics. Test fixtures distinguish
synthetic cases from the actual sanitized Pilot A records.

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

Migration `20261108173259_messaging_service.sql` was applied in production on
2026-11-09 before compatible deployment. Do not reapply it. For another target,
apply the reviewed migration before compatible code after inspecting its history. The applied User migration is unchanged. Do not
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
with a new key based on generic text and approximate time. Verified sent/delivered/read all use local `sent` with the exact observed
provider status preserved. Terminal failure or success releases that conversation route; other routes progress alone.

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

Startup configures only `tbd.messaging` at INFO with structured messages on stderr;
root/provider logging is not enabled by this configuration. Logs omit bodies,
contacts and provider errors/URLs. Receipt/commit/replay,
message/attempt IDs, queue age, API duration and state/error observations correlate
stages. Acceptance is not sent or delivery. Render Free sleep pauses processing;
pending rows do not wake it. Measure actual ordinary/burst/interleaved/quiet-wake
latency during TBD-20; polling adds observation delay.

### One-off Pilot A reply proof

`scripts/comms_pilot_reply.py` is a manual capture-proof tool, not the runtime
adapter. It requires `COMMS_PILOT_DIRECT_TEXT_CONFIRMED=YES` only after the tester
confirms the thread contains just the tester and shared line and the message had
no attachment. Use a hidden `COMMS_API_KEY` prompt in a temporary shell. The
script reads and compares the signed capture, exact inbound history and matching
conversation record before sending. It writes only private proof files under
`/private/tmp/tbd-comms-capture`, including the stable reply key and a sanitized
pair. Do not replace its key or payload if an outcome is unknown. Once it knows
the provider message ID, another execution only checks outbound history.

Absent audience/attachments remain absent in the sanitized evidence. Handset
attestation for this one controlled exchange is not an automatic classification
rule by itself. The approved server-owned pair rule below separately authorizes
the bounded plain-text pilot; it does not establish a provider-wide default.

## Confirmed-conversation plain-text pilot

Set `COMMS_PILOT_CONVERSATIONS` to a JSON object pairing each confirmed one-to-one
conversation ID with the tester's approved E.164 phone. Example with fake data:

```dotenv
COMMS_PILOT_CONVERSATIONS='{"conversation_pilot_a":"+12125550101"}'
```

The real Pilot A pair is prepared privately at
`/private/tmp/tbd-comms-capture/pilot-conversations.private.json`. Copy that JSON
into the secure runtime variable during the reviewed rollout, never into Git or
public issue comments. No key is stored in this mapping. Empty/missing config
admits no conversations. Add another pair only after its tester phone, completed
Join and one-to-one thread are confirmed. Real hosting configuration is TBD-20.

Both signed webhook intake and authenticated inbound-history recovery enforce the
same configured pair and shared line. They still resolve/lock the canonical
joined User and enforce activation/User-creation times. A missing audience or
attachments field permits only nonempty text under this explicit pilot rule;
null/invalid fields fail validation. Explicit shared/group, media, self/outbound,
non-iMessage, unsend or excluded-event evidence overrides the pair. Media is
ignored in this text-only pilot. Reactions attached to an original message do not
become separate inbound messages; history replay preserves already stored text.
The pilot relies on confirmed threads and plain-text testers, not a provider-wide
promise about omitted fields. Do not broaden it silently after provider changes.

The real sanitized Pilot A fixture is exercised through signed HTTP intake,
restricted-role PostgreSQL storage and authenticated fake-provider history:
exact text + one queued reply, replay with no duplicate, and zero extra rows for
wrong joined sender/unapproved conversation/line or explicit exclusions. No live
account is contacted by these tests. Marking PR #5 ready for backend review does
not authorize merge, production migration or deployment; both founders' explicit
merge approval remains required and hosted proof remains TBD-20.

## Final release preparation and cutover

Use [release-candidate.md](release-candidate.md) for the rehearsal evidence,
read-only production snapshot, private tester readiness, exact approval → merge →
migration → Save-only config → one deploy → final signed-ping gates, capture
retirement and the hosted A2/B1/interleaved/quiet-wake checklist. The earlier A1
and setup routing messages are not hosted verification evidence. Do not begin
production changes from these instructions until both founders approve the final
code/private configuration and the backend is merged.


## Verified hosted pilot and ongoing operation

[2026-11-09 evidence](hosted-pilot-evidence.md) records the final deployed commit,
migration safety, A+B routes, measured timings and quiet/wake recovery. Keep the
approved activation `2026-11-09T03:30:00Z` fixed; do not advance it to discard
backlog. Reconcile unexpected eligible backlog explicitly before deployment.

- Keep the final received+ping registration and its own secret matched to runtime.
  The temporary capture registration was retired and local receiver/tunnel stopped.
  The setup key is not a runtime variable. Keep private evidence secure; temporary
  staging under /private/tmp is not durable credential storage. Runtime secrets
  remain in Render; preserve the private evidence/config reference securely for
  operations before local temporary files expire. Never publish contacts or keys.
- To stage variables without deployment, use Render Dashboard **Save only** and
  inspect deploy history. The connected environment-update tool triggered a deploy
  during this pilot despite auto-deploy being off; do not use it as Save-only.
  For a reviewed code release, verify schema compatibility and all required values
  before one intentional deploy, then verify the exact merged SHA and loop health.
- Supabase CLI 3.0.0 used an explicit target because this checkout was unlinked:
  `npx --yes supabase@3.0.0 db push --remote=xclftpnjwrmakooaaxik --skip-vault --dry-run`.
  Read installed help, confirm exactly reviewed pending migrations and target, then
  apply with the same flags minus --dry-run only when authorized. Do not reset or
  include roles/seeds. Verify the real client TLS session, not pg_stat_ssl behind
  a pooler; the connector's inability to SET ROLE does not diagnose runtime access.
- On Free sleep, do not health-poll while measuring idle. Use control-plane logs
  to confirm shutdown and restricted-session lock release. In this pass the inbound
  webhook woke the service, its first delivery timed out, startup history recovered
  A5, and webhook retry replayed it safely. No manual wake was needed. If a future
  retained message does not wake the service, record one explicit wake request and
  inspect the same native ID/job afterward; don't ask for a replacement text/send.
- A provider delivered/read status without delivered_at does not establish a
  delivery timestamp. Keep source-to-acceptance/status-observation times separate.
  Known-ID uncertainty is GET reconciliation, never a replacement send/key.
- Free 100/day was reported and at least 20 available confirmed before this pass.
  Allocation16 used/4 reserve is bookkeeping, not a live allowance reading.
  Check actual availability before more tests; reset time and inbound accounting
  remain unverified. Keep both testers paused after the completed pass.


## Contact card rollout (pending review)

Keep COMMS_CONTACT_CARDS_ENABLED=false until the reviewed ledger migration and
private Pilot A import are verified. [contact-cards.md](contact-cards.md) gives the
exact migration/import/enable/deploy sequence and post-deploy B/A checks. This
follow-up has not been deployed; it does not change the existing reply settings.
