# TBD v1 — Messaging Service

## TLDR

A tester completes Join, then texts TBD on the Comms by Osis Free shared number. Messaging verifies the sender, stores each new direct iMessage and queues one “Received your text.” reply in the same conversation. Join establishes eligibility and creates no outbound message.

One module, one private message table plus a contact-card ledger, one delivery loop and one Comms adapter run in the existing Python/FastAPI app on Render. [Editable architecture](architecture.excalidraw) · [Linear project](https://linear.app/apt-knows/project/messaging-service-ec91f9125e1b/overview).

## Background Information / Context

[User Service](../user-service/design.md) owns invitations, completed Join, canonical User IDs and approved phone numbers. Messaging owns messages and delivery. Agent will later supply reply content.

The pilot uses a provider-supplied shared line. Comms advertises $0 and 100 messages per day on Free. Testers initiate the conversation using the workspace's routing instructions. A founder shares the number and any required first text privately with joined testers. This is the pilot handoff. [Comms pricing](https://comms.osis.co/pricing) · [Shared line behavior](https://comms.osis.co/compare/blooio).

## Design and Architecture

### 1. Join and channel entry

1. Complete the existing invitation-backed `POST /v1/join`. Existing joined Users can take part immediately. A seeded business alone is ineligible.
2. From their approved phone number, the tester sends the required first iMessage to the shared line. Follow Comms' workspace routing instructions, including a keyword if assigned.
3. The first eligible message Comms passes to TBD follows the same store-and-reply path as every later message. If Comms consumes a routing text, the tester sends an ordinary text after routing. Confirm this behavior during account setup.

### 2. Receive, store and queue

1. Receive `comms.message.received` at `POST /webhooks/comms`. Verify the `X-Osis-Signature` HMAC-SHA256 over the exact body before parsing. Use the signed event type. Use the provider message ID inside `data.message` for message deduplication across webhook and history. The envelope ID identifies a delivery event.
2. The adapter verifies workspace/line, inbound direction, iMessage transport, direct audience, sender phone, conversation ID and original message time. Confirm the exact Free account fields against a signed event and its history record. Comms documents `audience`, `direction`, `channel` and `attachments`; validate how sender, line and timestamp are populated on the shared line. Missing identity or route evidence prevents admission. For this plain-text Free pilot, backend configuration `COMMS_PILOT_CONVERSATIONS` pairs a founder-confirmed one-to-one conversation ID with its approved tester phone (initially Pilot A). Every runtime intake must match this pair and the configured shared line, then resolve to a joined User. If `audience` or `attachments` is absent, that confirmed pair permits only a nonempty exact text body. This is an explicitly approved pilot rule, not a claim that omitted provider fields guarantee direct text. Explicit group, media, non-iMessage, self-echo, unsend or excluded-event evidence overrides the allowlist. Present-but-invalid/null classification remains invalid. The same rule applies to signed webhook intake and authenticated history recovery; no client-supplied allowlist is accepted.
3. Resolve the normalized sender against User Service, require completed Join and reject messages before activation or User creation. Use the cursor-based lookup in the receive transaction. Email Join does not verify phone ownership.
4. Lock the User, store the incoming message and queue its reply in one transaction. Acknowledge after commit. Reuse the stored inbound and reply on identical replays. A changed identity or route for the same provider message ID is a conflict.

| Incoming case | Behavior |
| --- | --- |
| Direct text, emoji, whitespace or URL | Store exact text and queue “Received your text.” |
| Direct media | Ignore in this plain-text pilot; media evidence cannot use the missing-field exception. Download nothing. |
| Repeat delivery or history replay | Reuse records. Distinct messages with identical text each receive a reply. |
| Group, reaction, edit, unsend, self echo, status or non-iMessage | Ignore. Treat a history update to an already stored message as the same message and preserve its stored text. |
| Unknown sender, unjoined seed or unmatched Apple email alias | Ignore without storing its body. |

Automatic URL previews stay text. Skip attachment downloads and storage of attachment URLs or transcripts. Confirm that history cannot turn a reaction or edit into a new eligible message. Keep the current 512 KiB body, 64 KiB UTF-8 text and 256-byte ID bounds. Return 2xx for committed, replayed or intentionally ignored events, 401 for bad signature, 413 for size, 422 for invalid/unclassifiable content, 409 for conflicting immutable replay and 503 for temporary storage failure. Keep the handler bounded and put provider calls in the delivery loop. [Comms webhooks](https://docs.osis.co/guides/webhooks) · [Message records](https://docs.osis.co/messages-api/list-messages).

The runtime allowlist defaults to `{}`, which admits nothing. Populate only confirmed
one-to-one conversation/approved-phone pairs in private server configuration; never
commit real contacts or conversation IDs. Removing a pair stops new intake and
history admission for it, while already committed outbound routes remain unchanged.
The allowlist supplies pilot classification only: completed Join, original-time
boundaries, signature/authenticated history and immutable replay checks still apply.
No per-conversation table or generic permission framework is added.

### 3. Store messages and delivery work

Use the existing draft private `messaging.messages` table for history and outbound work.

| Fields | Purpose |
| --- | --- |
| TBD UUID, User ID, channel, direction, dedupe key, provider message ID | Ownership and replay protection |
| Account/peer phone, provider conversation ID, text, unsupported-content flag | Immutable route and content |
| Inbound reference, state, queue sequence, attempt and due time, safe error code | Reply linkage, delivery order and recovery |
| Source, creation, first attempt, acceptance and sent observation times | Trace each stage |

Keep `inbound:<provider_message_id>` and `reply:<inbound_id>` unique by channel/direction. Bind a reply to the same User, account, peer and conversation as its committed inbound. Keep the existing `receive_message`, `queue_message` and `record_send_result` responsibilities. Edit the unapplied Messaging migration in PR #5 to carry the conversation ID and Comms state mapping. Preserve the released User migration and role boundaries.

### 4. Send and check outcomes

One FastAPI lifespan loop owns sending through the existing session advisory lock. Claim and commit the oldest unresolved outbound on each User/conversation route before calling Comms. Other routes can progress while one is unresolved. Only a terminal send outcome releases the next job on its route.

Send via `POST https://osis.co/api/v1/comms/messages` with a Bearer key. Use the stored `conversation_id`, reply text as `body`, and `channel: "imessage"`. The conversation selects the shared line. Use the outbound row UUID as `Idempotency-Key` and keep the request payload fixed across attempts. [Send API](https://docs.osis.co/messages-api/send-message).

| Evidence | Local action |
| --- | --- |
| Valid acceptance with matching message ID and route | Save provider ID and accepted time. Track submitted work as `provider_queued`. A duplicate response returns the prior result. |
| Verified sent/delivered/read or permanent failure/suppression | Record the corresponding terminal outcome and preserve the reported status. |
| Known ID with unknown, missing or unconfirmed outcome | Keep it unresolved. Read the conversation's outbound history and match the exact ID and route. |
| Timeout or unknown acceptance without ID | Retry the identical request with the same key and backoff within 24 hours of the first attempt. After that, hold as `uncertain` for reconciliation. |
| Rate limit, quota or configuration rejection | Honor retry hints and pace the account. Log quota/auth failures and persist pauses so restart cannot bypass them. |

Keep one bounded request at a time and apply results only to the current attempt. Use the documented `retry_after` value on 429, with 60 seconds when absent. Acceptance, sent observation and delivery are separate evidence. Read status from `GET /api/v1/comms/messages` with the stored conversation and outbound direction. [Idempotency](https://docs.osis.co/guides/idempotency) · [Errors and limits](https://docs.osis.co/guides/errors-and-rate-limits).

### 5. Catch up and observe

Run an inbound history check on start/wake and about once a minute while awake. Query `GET /api/v1/comms/messages` with `since` set to the fixed activation time, `direction=inbound` and `limit=200`. Process returned messages oldest first through the same admission and dedupe path. The list is capped. At the cap, process the returned batch and log possible incomplete coverage. Comms also retries failed webhook deliveries. Verify the actual history window on Free and record the limits of recovery. Keep `MESSAGING_ACTIVATED_AT` fixed across rollouts. [History API](https://docs.osis.co/messages-api/list-messages) · [Webhook retries](https://docs.osis.co/guides/webhooks).

Use structured logs and message rows for receipt, commit, replay, queue age, attempt, acceptance, send observation and failure. Record last successful API/history checks and actual provider pacing. `GET /healthz` reflects process/loop health. Provider failures appear in logs and state. Render Free sleep pauses work until the app wakes.

### 6. Agent handoff

Later, Agent can consume a committed message ID, backend-resolved User ID, text and trusted route. It can use `queue_message` with its own producer key to replace the fixed reply. User Service continues to own profiles and addresses.

## Constraints and Guidelines

- Keep one Python module, message table, delivery loop and Comms adapter in the existing server. Use the current HTTP client, Supabase database and Render service.
- Keep the Messaging schema private, with the existing restricted backend role and explicit grants. Preserve data on routine rollouts.
- Keep bodies, contacts, provider response bodies and secrets out of logs and public GitHub. Commit sanitized fixtures only.
- Pilot settings use the Free shared line, one workspace and iMessage only. Measure actual latency and quota behavior with the pilot.

## Additional Information

### Comms setup and proof

1. Open [Comms pricing](https://comms.osis.co/pricing) and select **Start Free**. Verify the workspace shows Free and an iMessage-capable shared line. Record its exact first-text/keyword instructions. Configure it so TBD is the only reply source. If routing requires a Comms agent object, confirm API traffic works with automated responses off.
2. In [API keys](https://comms.osis.co/settings/api-keys), make a runtime key with `comms_read` and `comms_send`, scoped to the pilot line if available. Use a setup key with `comms_webhooks` for registration. The [quickstart](https://docs.osis.co/quickstart) documents these entry points.
3. Register the controlled HTTPS capture URL using `POST https://osis.co/api/v1/comms/webhooks` with `url` and `events: ["comms.message.received"]`. Include `comms.ping` when using `POST /api/v1/comms/webhooks/{id}/test` for a signed smoke test. Save the returned `webhook.secret` securely. [Webhook setup](https://docs.osis.co/guides/webhooks).
4. Use a consenting tester's iPhone to follow the assigned routing instructions and send a direct test iMessage. Compare its signed event with `GET /api/v1/comms/messages?conversation_id=<id>&direction=inbound`. Capture a small sanitized set for direct text/media, excluded events, replay and status. Verify one explicit iMessage reply through that conversation, with provider auto replies off. Check that Free permits the complete flow and record the actual account limits.
5. After backend review, configure the existing Render service with `COMMS_API_KEY`, `COMMS_WEBHOOK_SECRET`, `COMMS_LINE_E164`, `COMMS_PILOT_CONVERSATIONS` and `MESSAGING_ACTIVATED_AT`. The final webhook must target `/webhooks/comms` and use its own signing secret. Apply the reviewed migration before compatible deployment. Keep runtime keys and test payloads out of Git.

### Build sequence

1. [TBD-18](https://linear.app/apt-knows/issue/TBD-18) — continue [server PR #5](https://github.com/tbd-knows/server/pull/5). Remove the draft Join welcome hook and pilot welcome command. Replace the provider boundary and update configuration, controlled tests, `setup.md` and `implementation-status.md`. Run `uv run --locked python scripts/run_database_tests.py -q` in a normal terminal with local PostgreSQL binaries on PATH. Account validation and fixture capture can proceed alongside the backend checks.
2. [TBD-20](https://linear.app/apt-knows/issue/TBD-20) — after backend review and merge, verify the hosted exchange with joined testers and record real timings. [TBD-17](https://linear.app/apt-knows/issue/TBD-17) tracks both pieces.

## End Goal

A joined tester texts the shared line, sees one reply in the same iMessage conversation, and can keep texting. TBD records each supported message and can trace its reply and catch up after a restart or wake.

| Check | Required evidence |
| --- | --- |
| AC1 — Join eligibility | New and repeated Join preserve the User contract and create zero outbound rows. Existing joined testers qualify. Unjoined senders create no message rows. |
| AC2 — Storage | Exact text, atomic inbound/reply, duplicate webhook/history/concurrent delivery and distinct identical texts. |
| AC3 — Identity/content | Verified signature, workspace/line, direct iMessage classification and trusted conversation. Unknown senders and excluded events produce zero replies. |
| AC4 — Delivery | Same-conversation replies, exact-ID status reads, stable key across retry/restart, uncertainty beyond the key window and independent progress across routes. |
| AC5 — Recovery | Pending and known-ID work resume. Retained inbound history catches up after wake and reports a capped result. |
| AC6 — Observability | Safe stage, queue, quota and error evidence. Pilot timings are measured on the actual path. |
| AC7 — Data safety | Local PostgreSQL transaction/permission checks and User Service regressions pass. |
| AC8 — Hosted pilot | Two joined testers initiate and receive separate same-thread iMessage replies through Comms Free. Confirm routing, one reply per supported message and quiet/wake behavior. |

## Contact card follow-up (TBD-21)

After a conversation's first successful reply, send one saveable TBD contact card
using the existing Free shared line. A private ledger, separate from message
rows, persists one obligation/key per line and conversation. Current approved
pair, canonical joined User/phone and successful reply are required for existing
and future threads. Import Pilot A's verified manual card before enabling sends;
later replies never resend it. The existing delivery loop prioritizes replies
and reserves shared Comms pacing for at most one bounded card request in flight.
Card outcomes stay in the ledger and do not change reply state; known IDs use
exact route/body-aware history, and uncertainty retains the original key.

This is opt-in behind COMMS_CONTACT_CARDS_ENABLED (default false), a reviewed
additive migration and private import. No backdrop, dedicated line, automatic
profile sharing or plan change is included. See [contract, tests and ordered
cutover](contact-cards.md). Production rollout follows review and founder approval.

## Non-Goals

Automatic Join welcome, dedicated/ported numbers, device bridges, provider-generated replies, SMS/RCS fallback, in-app chat, additional channels, Agent execution, attachments processing, memory compaction, extra queue/worker services, dashboards and automatic database resets.

## References

- [Architecture](architecture.excalidraw) · [User Service contract](../user-service/design.md) · [User Service runbook](../user-service/setup.md).
- [Messaging project](https://linear.app/apt-knows/project/messaging-service-ec91f9125e1b/overview) · [Tech Hub](https://app.notion.com/p/3cb726cebd9080be90cdd38c5f2125ad).
- [Comms pricing](https://comms.osis.co/pricing) · [Quickstart](https://docs.osis.co/quickstart) · [Webhooks](https://docs.osis.co/guides/webhooks) · [Send](https://docs.osis.co/messages-api/send-message) · [History](https://docs.osis.co/messages-api/list-messages) · [Idempotency](https://docs.osis.co/guides/idempotency).
