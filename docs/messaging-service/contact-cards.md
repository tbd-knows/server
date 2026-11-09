# TBD contact card rollout

[TBD-21](https://linear.app/apt-knows/issue/TBD-21). Review candidate only;
no migration, import, rollout enablement, deployment or automated live card send
has been performed for this change. Keep the current Comms Free shared line.

## Verified account proof

Pilot A's manual `POST /api/v1/comms/contact-cards` returned HTTP 200. Saved exact
outbound ID/history matched the approved conversation, configured shared number,
imessage channel and outbound direction; status was sent. The user confirmed one
card arrived and saving/updating it showed the TBD logo in Messages. The response
has `contact_card.name/phone`; its message body is empty with a text/vcard
attachment. History preserves the empty body but omits attachments. This is not
the fixed-text reply shape. Real IDs/phone/key remain private. [Sanitized actual field shapes](fixtures/comms-free-contact-card.json)
are tested separately from handset confirmation.

The approved full artwork is packaged as a 384x384 PNG, 124,280 bytes, SHA-256
`a23a3e07864b03826a5793cfcd09e880963b34706714a584c0d6a0010a702067`.
Requests use the trusted conversation, name TBD, shared line as card phone and
sender, explicit imessage, and photo_base64. No caption, agent, new number,
profile-sharing operation or photo download is needed. Backdrop proof returned
HTTP 501 with actual code `backdrop_unsupported`; no backdrop rollout is included.

## Apply once and protect replies

- A separate private `messaging.contact_cards` ledger has one row per line and
  conversation, tied to a successful reply's immutable User/route. Canonical Join,
  current User phone, server-approved pair and configured line are checked before
  enqueue and claim. No successful reply means no card; Join sends nothing.
- Persist the exact payload digest and stable key before provider I/O. Import A's
  verified provider ID/key as already sent, with zero rollout POST attempts. Never
  reset sent/failed entries on later replies, restart or artwork/code changes.
- The existing session-owned loop checks due reply work first and reserves the
  same 1.1-second API/send pacing slots. It launches at most one bounded card API
  call in a single background executor; it does not wait for that call to send a
  newly due reply. There is no second polling loop, service or infrastructure.
- A transaction lock serializes claims and a persisted 45-second in-flight lease
  exceeds the adapter's 10-second request deadline. After a crash, an expired
  no-ID claim uses the same key/payload; after 24 hours from first attempt it holds
  uncertain for reconciliation, without a replacement key. Known IDs use only
  exact-ID history on the same route and saved card body, including an empty body.
  A 409 idempotency_in_progress retries with backoff and the same key/payload
  within that window; idempotency_key_reused is terminal.
- Completed outcomes are retained across result-commit retries and flushed on
  orderly shutdown. Stale attempt results and terminal regressions are ignored.
  Card state/errors live only in the ledger. Ordinary card rejection or transport
  failure cannot mutate, fail, retry or block a reply's route. Shared-account
  limits still pause provider work: HTTP 401, HTTP 429, or HTTP 402 with the
  documented channel_entitlement_denied quota code. These use explicit
  card_account_* markers and the existing 60/3600/retry-after durations. Other
  402/403 responses (including absent/non-JSON bodies) are card-only; a card read
  restriction retains known-ID uncertainty without blocking replies or history.
- Rollout defaults off. First enablement requires a verified existing-card import
  for the configured line. Missing import/schema/permissions hold only card work,
  with a safe card_rollout_held log; User Service and normal delivery still start.
  A historical import remains valid after its pair is removed; every new card
  claim still requires the current approved pair and canonical joined User. Discovery runs on startup/idle and at most once a
  minute,
  reset immediately when a normal reply becomes sent. Updated approved-pair config
  takes effect on restart; a new eligible thread qualifies after its first sent
  reply. Card failures emit safe card_id/state/error logs without contacts/bodies.

## Exact production order — after founder approval

1. Both founders approve the final PR; merge it and record the actual merged SHA.
   Verify the existing service/project, Free allowance (B card plus normal-reply
   verification needs at least five messages counting both directions), and that
   automatic replies are still off. Keep testers paused. Preserve all existing
   User/Messaging config, activation cutoff and webhook.
2. Keep `COMMS_CONTACT_CARDS_ENABLED=false` (or absent) on Render. Use Dashboard
   **Save only**, never the environment-update connector that deployed during the
   prior pilot. Do not deploy yet.
3. From the reviewed merged checkout, verify only the new migration is pending:

   ```bash
   npx --yes supabase@3.0.0 migration list --remote=xclftpnjwrmakooaaxik
   npx --yes supabase@3.0.0 db push --remote=xclftpnjwrmakooaaxik --skip-vault --dry-run
   # Only after that exact target/pending file is confirmed:
   npx --yes supabase@3.0.0 db push --remote=xclftpnjwrmakooaaxik --skip-vault
   ```

   Expected new file: `20261109223752_contact_cards.sql`. Reviewed SHA-256:
   `c14546c37267b854bef66df791eea899e8bbe64402333134d9b13e164d41517c`. Verify it before execution. Do not reset, rewrite released migrations or include
   roles/seeds. Snapshot existing User/message rows/grants; verify they survive
   and that browser roles cannot access the ledger, while the runtime can.
4. **Import Pilot A before enabling or deploying sends.** Use reviewed code and a
   private env file containing the existing restricted TLS/session database URL,
   COMMS_API_KEY, COMMS_LINE_E164 and confirmed COMMS_PILOT_CONVERSATIONS. It must
   not contain new routes or changed contacts. No real IDs go in migration SQL.

   ```bash
   uv run --locked --env-file /private/path/card-import.env python -m tbd_server.messaging.cards \
     /private/tmp/tbd-comms-contact-card-check/pilot-a-v1.private.json \
     /private/tmp/tbd-comms-contact-card-check/pilot-a-summary.json
   ```

   This command performs only provider GET history, checks the exact saved ID,
   card metadata/body/payload/key/photo and handset confirmations, then imports
   one terminal sent entry. It is repeat-safe for identical proof and refuses a
   conflicting existing attempt. Require imported=true, card_sent=false, A's
   original provider ID/key and attempt_count=0; verify normal rows unchanged.
   If history is missing/capped/uncertain or any check fails, leave rollout off
   and stop. Do not replace A's key or send it another card.
5. Set `COMMS_CONTACT_CARDS_ENABLED=true` with Dashboard **Save only**. Recheck
   the private A import, mappings, artwork digest and pending config. Deploy the
   final merged SHA once. Verify exact commit, runtime role/TLS, loop ownership
   and health. Verify the imported record and absence of card_rollout_held before
   claiming card rollout readiness; healthy API startup alone is not sufficient. No
   second
   deploy is needed just to turn this rollout on.
6. Verify B gets one saveable TBD card/logo, A gets no second card, and each is
   represented by its own ledger entry. B is already eligible from its existing
   successful reply; no new text is required to trigger its card. Then coordinate
   one new normal text in each original thread; confirm exactly one normal reply
   per text and no further cards. Inspect exact provider IDs, attempt counts and
   route matches; compare normal-message counts with the pre-cutover baseline.
7. Record sanitized hosted results in TBD-21. Stop on an unexpected send, route,
   duplicate, unknown result or migration/import/config failure. Inspect the
   existing key/ID, never reset/delete the ledger or invent a new key. If rollout
   must be disabled, stage false and intentionally deploy under the normal
   approval process; a saved environment value alone does not change a running
   process. Disabling preserves ledger state and normal reply data.

## Review validation

The disposable PostgreSQL harness applies the additive migration over released
User/Messaging schemas and preserves User data/grants. Focused tests cover seeded
A, existing/new eligibility, import CLI GET-only behavior, card-only prerequisite
holds/historical-import retention, concurrent
claims, global in-flight lease, restart/key window, exact-ID status and stale
results, permissions, shared-account pause isolation, blocked card I/O with a
normal reply progressing, and result-commit retry without another POST. Provider
requests are controlled fixtures; automated tests never send live cards.

[Send-card API](https://docs.osis.co/messages-api/send-contact-card) differs from
[dedicated-line name/photo sharing](https://docs.osis.co/messages-api/set-contact-card).
Recipient saving and Messages display remain handset evidence, not an API guarantee.

Review regressions also exercise due normal replies/history after card-only 402/403,
actual app startup and B replies with missing import or A removed after import,
and recoverable versus terminal 409 conflicts with unchanged keys/payloads.
See [provider error semantics](https://docs.osis.co/guides/errors-and-rate-limits).
