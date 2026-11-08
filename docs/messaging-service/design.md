# TBD v1 — Messaging Service

## TLDR

Build Messaging inside the existing Python/FastAPI server. Reuse Supabase Postgres and Render. One private message table holds history and outbound work; one delivery loop connects to Texting Blue Free and the spare-number iPhone.

When an approved User completes Join, queue one welcome. Store each new direct iMessage and queue one “Received your text.” reply in the same thread. Keep enough state and logs to see what was received, queued, accepted and sent.

The high-level architecture is approved. This implementation design is a review draft. [Editable architecture](architecture.excalidraw) · [Linear project](https://linear.app/apt-knows/project/messaging-service-ec91f9125e1b/overview).

## Background Information / Context

[User Service](../user-service/design.md) already owns invitations, completed Join, canonical User IDs, approved phone numbers and profiles. Messaging owns transport, message records and delivery. Agent will later provide response content through Messaging.

An eligible User has completed an invitation-backed Join. A seeded business alone is not eligible. Email Join does not separately verify phone ownership; the authenticated channel event establishes the incoming sender. Match that sender to the stored approved phone.

The first rollout uses existing test accounts. Texting Blue Free and Render Free are accepted, including quotas and hosting sleep. Account capabilities, real event payloads and phone behavior are checked during implementation. The dedicated number is TBD's sender identity; automatic contact-name branding is not assumed.

## Design and Architecture

### 1. Components and interfaces

| Part | Responsibility |
| --- | --- |
| User Service | Admission, identity, approved contact and Join transaction |
| Messaging core | Welcome, inbound storage, duplicate handling and outbound queueing |
| Delivery loop | Send pending text, check outcomes and recover retained inbound history |
| Texting Blue adapter | Verify/translate incoming events; call provider send/read APIs |
| Agent, later | Produce replies from committed messages; Messaging still owns delivery |

Add `src/tbd_server/messaging/` to the existing application. Keep provider fields inside the adapter. Shared operations are small Python functions, not separate network services:

| Operation | Contract |
| --- | --- |
| `ensure_welcome(cur, user)` | Insert once or return the original welcome unchanged |
| `receive_message(event)` | Resolve the User and commit inbound plus fixed reply together |
| `queue_message(cur, user, route, text, dedupe_key, reply_to=None)` | Insert or reuse an identical outbound command in the caller's transaction |
| `record_send_result(cur, message_id, attempt_id, result)` | Apply evidence only to the current attempt/provider job |

HTTP changes are limited to:

- Existing `POST /v1/join`: queue the welcome before commit, including the already-joined branch; preserve its response contract.
- New `POST /webhooks/texting-blue`: receive signed `message.received` events. Outgoing status is read through the provider API.
- Existing `GET /healthz`: report process/required-loop health. A provider failure alone does not make the process unhealthy.

### 2. Join and first message

1. User Service verifies the existing invitation and identity.
2. In its Join transaction, lock the User and call `ensure_welcome`. Commit User, joined invitation and welcome obligation together. Do not call the provider here.
3. The delivery loop sends: **“Welcome to TBD. You can reply to this number.”**

Repeated Join returns the same welcome. Changing welcome copy does not resend it. Add one backend command accepting explicitly selected existing joined User IDs for pilot setup; reuse the same operation. First eligible inbound receipt also ensures a missing welcome before its reply, so pilot setup and incoming texts can overlap safely.

### 3. Receive, store and respond

1. Verify HMAC-SHA256 over the raw body using `x-textingblue-signature`, then parse the signed event type. Require the destination to match the configured TBD number.
2. Normalize the adapter event: channel, sender account, original message ID, peer, exact text, unsupported-content flag and original timestamp. Use nested `data.id`, not the webhook envelope ID; history must map to the same identity.
3. Check for a replay before resolving a new User. For a new event, resolve and lock the canonical User, require completed Join and apply the activation/User-creation time boundaries.
4. In one transaction, ensure welcome, insert the inbound row and queue its reply. Commit before returning success. Delivery happens afterward.

| Incoming case | Behavior |
| --- | --- |
| Direct text, emoji, whitespace, line breaks or URL | Preserve exactly; reply “Received your text.” |
| Duplicate native message ID | Reuse records; no second response |
| Two separate messages with identical text | Store and respond to both |
| Recognized direct media, with or without caption | Store identity/classification and caption; reply “Please send text for now.”; download nothing |
| Automatic URL preview | Treat as text |
| Group, reaction, edit, self echo or status event | Ignore |
| Unknown sender, unjoined seed or unlinked Apple email alias | Ignore without storing its body or replying |

Verify actual webhook/history metadata during integration; absent fields are not proof of a direct text event. Turn off the bridge's own autoresponder.

Return `200 {"received":true}` for a committed message, identical replay or intentional ignore; `401` for bad signature, `422` for invalid data, `413` for size limits, `409` for conflicting immutable replay, and `503` for storage failure or unknown commit outcome. Retrying an unknown commit must remain duplicate-safe.

Bound raw bodies to 512 KiB, inbound text to 64 KiB UTF-8 and native IDs to 256 bytes. Never truncate or trim. Text-only content must be nonempty; unsupported media may have null text. First-seen inbound needs a valid original timestamp, not a substituted receipt time. Use a four-second handler budget against the provider's five-second acknowledgment deadline; bound database waits accordingly.

### 4. Storage and atomicity

Use one private `messaging.messages` table. Each inbound and outbound has its own TBD UUID; the outbound row is also its delivery job. Inbound state is `received`; outbound states are defined below.

| Fields | Purpose |
| --- | --- |
| `id`, `user_id`, `channel`, `direction`, `dedupe_key` | Identity and replay protection |
| `account_ref`, `peer_ref`, `text`, `has_unsupported_content` | Trusted route snapshot and exact content |
| `reply_to_message_id`, `provider_message_id` | Link replies and provider records |
| `state`, queue sequence, attempt ID/count, `next_action_at`, safe error code | Ordering and resumable work |
| Created/updated, original-source, attempt, accepted and sent-observed timestamps | History and stage timing |

Use `inbound:<native_id>`, `welcome:<user_id>` and `reply:<inbound_id>` keys, unique by channel/direction. Enforce native-ID uniqueness, valid direction/state/content combinations and foreign keys. Start with duplicate/native-ID, due-work and User-history indexes.

Serialize message creation under the User row lock, then allocate queue order. Preserve Join's invitation-before-User lock order. Use cursor-based User lookup: existing `Store.resolve()` opens another transaction. Schema-qualify Messaging SQL and handle duplicate conflicts deliberately instead of passing them through the generic identity-conflict handler.

Replays cannot change User, channel, route, text or reply relationship. A reply must match its inbound User/channel/route. Multiple future Agent messages may reference one inbound; their producer keys differ. `ensure_welcome` returns an existing welcome before applying current copy.

### 5. Send, track and resume

One loop runs with FastAPI lifespan without blocking request handling. Use a session advisory lock on one dedicated session-mode database connection to prevent overlapping deployments from running two senders. Reuse it for short transactions; stop new work on ownership loss. A loop waiting for ownership is healthy.

Claim the oldest unresolved outbound on each User/account/channel route, save `sending` and the attempt ID, then commit before HTTP. Only `sent` or terminal `failed` releases that route's next message; other Users can progress independently. No database transaction stays open during a provider call.

The adapter uses `POST /v1/messages/send` with `from`, `to`, `content` and `x-api-key`. Keep redirects and automatic POST retries disabled; use a bounded ten-second request deadline.

| Evidence | State and next action |
| --- | --- |
| Local obligation | `pending`; claim when due |
| Send attempt started | `sending`; retain attempt identity |
| Valid 202 with provider ID | `provider_queued`; save ID and poll `GET /v1/messages/:id` |
| Verified sent/delivered status | `sent`; record observation time, preserving what was actually reported |
| Definite permanent rejection/provider failure | `failed`; report the reason |
| Send outcome unknown | `uncertain`; no blind replacement POST |

Validate result identity/route against the current job. Stale results cannot regress terminal state. If an acceptance is known in memory but saving it fails, retry recording that result. On restart, an unresolved `sending` row without a provider ID becomes uncertain. Local uniqueness cannot guarantee exactly-once external sending.

Known-ID uncertain rows continue paced GET checks, including after temporary 404s. They can recover to queued/sent/failed without another POST. Without an ID, release uncertainty only with evidence of non-acceptance or a demonstrably matching provider job. Repeated generic text and approximate time alone are insufficient.

Use one shared API budget and simple due times. Initial settings: 13 seconds between completed send attempts, including on startup/handoff; 2.1 seconds between completed API requests; status checks every 15 seconds; complete history sweeps and number-status reads every minute while awake. Interleave due categories, one history page per turn. These are tunable operational defaults, not product latency targets.

Honor `Retry-After` on 429, using 30 seconds if absent/invalid. Persist an account-wide one-hour send pause on quota rejection, then allow one probe. Account auth/config failures pause affected work for 30 seconds before a probe. Other confirmed transient non-acceptance uses backoff capped at 30 seconds; unknown POST outcomes stay uncertain. Known jobs are read, not recreated. Derive account pauses from persisted failure/due fields so restarts and other Users cannot bypass them. Continue reads during send-only pauses when permitted.

For catch-up, page through provider history with `direction=inbound`, `to=TBD` and `limit=100`, preserving filters until `has_more=false`. Feed records into the same receive operation. Continue past known records and empty intermediate pages; report malformed records while processing the rest. A missing/repeated cursor ends that sweep visibly and retries on the next sweep.

Set `MESSAGING_ACTIVATED_AT` once. Ignore original message times before activation or User creation; never advance this cutoff on deployment. Full sweeps are sufficient for this small pilot. Recovery covers provider-retained messages, not messages the bridge never captured.

### 6. Observe the real path

Use structured application logs and message rows. Correlate receipt, commit, duplicate, claim, acceptance, send observation, retry and failure with request/message/attempt IDs. Record stage duration and queue age; expose pending, provider-queued, uncertain and failed work plus last successful API call, history sweep and device contact.

Measure receipt-to-commit, queue time, provider acceptance and sent-observed time. Keep provider timestamps separate; status polling adds observation delay. Neither API acceptance nor phone contact proves recipient delivery. After integration, measure ordinary exchanges, a small burst, two Users interleaving and quiet/wake behavior. Set improvement priorities from that baseline.

### 7. Agent handoff

Later, Agent consumes a committed message ID, backend-resolved User ID, text and trusted route. It returns response content and a stable producer key through `queue_message`; multiple updates can reference one inbound. Replace the placeholder when Agent is integrated, and add durable Agent-work ownership then. No LLM call belongs inside a webhook request or SQL transaction. User Service retains profile authority; Agent/Transactions own task interpretation and financial decisions.

## Constraints and Guidelines

- **HARD YAGNI:** keep one module, table, loop and adapter. Reuse the current FastAPI, Psycopg and HTTP client stack.
- **Private data:** extend the existing backend role with schema/table/sequence grants and RLS policies. Keep Messaging outside the Data API and inaccessible to browser roles. Parameterize SQL.
- **Trusted identity:** backend configuration and canonical User records select routes. Message text and future LLM arguments cannot select a User or destination.
- **Safe logs:** omit message bodies, raw contacts, credentials, signatures and unsanitized provider errors. Redact HTTP-client URLs containing phone query parameters. Public GitHub contains only fake fixtures and secret-free templates.
- **Accepted pilot limits:** retain Texting Blue Free and Render Free. Sleep delays work until the app wakes; a pending row cannot wake it. Confirm actual account caps during implementation and pace accordingly.
- **Preserve data:** add a new migration before compatible code. Routine deploys retain Users, messages and pending jobs. Any later disposable-test cleanup is a separate, explicit operation.

## Additional Information

### Repository and setup

Work in `tbd-knows/server`: Messaging module, existing app/Join integration, `supabase/migrations/`, tests and `docs/messaging-service/`. The existing TypeScript Join page and Vercel flow need no new UI for this scope. Reuse the current Supabase project and Render service.

Backend configuration adds `TEXTING_BLUE_API_KEY`, `TEXTING_BLUE_WEBHOOK_SECRET`, `TEXTING_BLUE_PHONE_NUMBER` and `MESSAGING_ACTIVATED_AT`. Runtime permissions cover sending/reading messages and reading number status; webhook management belongs to setup. Use the provider's iPhone setup; TBD builds no device automation.

During implementation, add a short `setup.md` with actual configuration, pilot-welcome command, deployment order and operational queries. Verify schema/role readiness at startup. Document queue inspection, quota/auth pauses, uncertain sends and webhook re-enablement using existing tools.

### Build sequence

[TBD-17](https://linear.app/apt-knows/issue/TBD-17) tracks completion:

1. [TBD-18 — backend](https://linear.app/apt-knows/issue/TBD-18): implement storage, Join, intake, adapter, delivery, telemetry and meaningful tests together.
2. [TBD-20 — hosted proof](https://linear.app/apt-knows/issue/TBD-20): connect the account/phone, resolve actual integration findings, verify both journeys and record the baseline/runbook.

Capture real fixtures as soon as account access is available; final hosted proof follows the integrated backend. These are substantial deliverables, not endpoint-level tickets. Follow repository review rules; planning approval does not claim implementation completion.

## End Goal

A completed Join produces one welcome from the TBD number. Each supported new direct iMessage is stored and receives one generic reply in the same thread. The team can trace that exchange and identify delayed or failed work. The core is ready for Agent to become its reply source.

| Check | Required evidence |
| --- | --- |
| AC1 — Join | New/repeated/interrupted Join and selected pilot setup produce one welcome; copy changes do not resend |
| AC2 — Storage | Exact text; distinct identical texts; webhook/history/concurrent replay; atomic inbound/reply; conflicting reuse rejected |
| AC3 — Identity/content | Bad signatures rejected; unknown/seeded senders and excluded events ignored; unsupported direct content handled; no cross-User route |
| AC4 — Delivery | Queued differs from sent; known-ID read recovery; stale/mismatched results rejected; no blind ambiguous resend; route order with other-User progress |
| AC5 — Recovery | Pending/known jobs resume after restart/wake; full history traversal and activation filters; broken cursors visible; bounded intake and single-loop ownership |
| AC6 — Observability | Correlated stage/queue/error evidence; small burst and quota/rate handling; measured baseline |
| AC7 — Data safety | Restricted-role/transaction tests; private logs; routine deployment preserves records; User Service regressions pass |
| AC8 — Actual channel | Observe welcome and stored-text-to-reply on the phones, from the correct number over iMessage in the same thread; bridge autoresponder off |

Use isolated PostgreSQL and controlled HTTP fixtures for automated tests; never send through the live account from that suite. Hosted handset checks are separate evidence. Complete the parent only when both children and AC1–AC8 have evidence.

## Non-Goals

Agent execution, memory compaction/summarization, retention automation, purchasing/payments, in-app chat, WhatsApp/Telegram, mobile apps, cross-channel identity linking, groups/media processing, read receipts, typing indicators, dashboards, Redis/brokers, separate worker deployments, a generic plugin framework, automatic database resets, or speculative scale/P99 targets.

## References

- [Approved Excalidraw](architecture.excalidraw); [User Service contract](../user-service/design.md) and [deployed setup](../user-service/setup.md).
- [Messaging project](https://linear.app/apt-knows/project/messaging-service-ec91f9125e1b/overview); [Tech Hub](https://app.notion.com/p/3cb726cebd9080be90cdd38c5f2125ad); [TBD v1 product scope](https://app.notion.com/p/3fa726cebd9080d2a096deed0fc4d440).
- Texting Blue: [messages](https://docs.texting.blue/api/messages/), [webhooks](https://docs.texting.blue/api/webhooks/), [limits](https://docs.texting.blue/api/rate-limits/), [errors](https://docs.texting.blue/api/errors/) and [device setup](https://docs.texting.blue/api/how-it-works/). Published contracts require account/device validation during implementation.
- [Supabase connection modes](https://supabase.com/docs/guides/database/connecting-to-postgres); [Render Free lifecycle](https://render.com/docs/free). Preserve session-mode worker ownership and the accepted sleeping-host behavior.
