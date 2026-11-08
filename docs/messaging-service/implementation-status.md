# TBD-18 — Comms Free backend review checkpoint

Updated 2026-11-08. PR #5 proceeds to **backend review** under the founder-approved
plain-text conversation allowlist rule in [design.md](design.md). TBD-18 remains
In Progress for review/corrections; no merge or deployment is authorized here.
Work stays in the original `server` checkout on `messaging-v1-implementer`, including
current main `3370855`. Both founders must explicitly approve merging.

## Final pilot behavior

Join establishes eligibility and creates zero Messaging rows. One private message
table, one adapter and one session-owned lifespan delivery loop run in the existing
FastAPI service. The released User migration and User Store contract are unchanged.
Only the unapplied Messaging migration is part of the proposed release.

`COMMS_PILOT_CONVERSATIONS` is a validated server-only JSON mapping of confirmed
one-to-one conversation IDs to approved E.164 tester phones, initially Pilot A.
Missing/empty config admits nothing. The private Pilot A mapping is prepared at
`/private/tmp/tbd-comms-capture/pilot-conversations.private.json`, not deployed.
No real conversation/phone pair or credentials are committed.

Signed `/webhooks/comms` intake and authenticated inbound-history recovery share
the exact same adapter gate. Matching conversation + approved phone + configured
shared line may omit audience/attachments for nonempty text. Present-null/invalid
classification fails validation; explicit group/media, excluded event, unsend,
self/outbound or non-iMessage evidence overrides the pilot approval. Media is
ignored in this text-only pilot. Canonical joined-User resolution/locking, source
activation/User-creation boundaries, exact text, immutable replay checks and
atomic inbound + one fixed queued reply still apply. The allowlist is an approved
pilot restriction, not a provider-wide claim about omitted fields.

Outbound requests use the stored conversation/body, explicit iMessage channel and
outbound UUID idempotency key. Known IDs are read by exact-ID conversation history;
unknown no-ID retries preserve the key/payload within the conservative 24-hour
window, then hold uncertain. Verified sent/delivered/read maps to existing local
sent while retaining the observed provider status. Queued/unknown do not release
the route. Due/error fields persist account pauses. History uses fixed activation,
limit 200 and oldest-first handling, logging possible incomplete coverage at cap.
Safe stage/queue logs and loop health expose delays/failures without contacts/bodies.

## Executed verification

```bash
PATH=/opt/homebrew/opt/postgresql@17/bin:$PATH uv run --locked python scripts/run_database_tests.py -q
uv run --locked ruff check src tests scripts
uv run --locked ruff format --check src tests scripts
git diff --check
```

**135 passed, zero skipped**, disposable local PostgreSQL over TCP and the actual
restricted backend role, plus controlled HTTP. One existing Starlette/AnyIO
warning. Automated tests never contact the live provider or hosted database.

The actual sanitized Pilot A fixture is exercised through signature-verified HTTP
and real SQL: exact inbound text and one pending fixed reply; duplicate webhook
and authenticated history replay create no extra rows. A wrong **joined** sender,
unapproved conversation, wrong line and explicit group/media/excluded evidence
produce no additional replies through either path. Tests also cover empty
allowlists, empty text and invalid/null classification. Existing constraint,
permission, transaction, concurrency, ownership, retry/window/status/order and
User Service regressions pass. Exact-ID unknown-to-read releases the next job;
wrong-ID read and stale unknown cannot release/regress terminal work.

## Actual provider evidence and its limits

[Sanitized actual records](fixtures/comms-free-pilot-a.json) and
[Pilot A evidence](pilot-a-evidence.md) record a verified ping/inbound, matching
history/conversation, retained full `app-tbd`-prefixed first text, one manual
stable-key explicit-iMessage reply and exactly one same-thread handset arrival.
Original HTTP 202 unknown acceptance/history is preserved beside the later exact-ID
read result at `23:05:38.437356Z` (provider read_at `22:56:05.218Z`). No replacement
send was needed. Real contacts, native identifiers, signatures and keys stay private.

This early account proof and the controlled backend tests are separate evidence.
The new allowlisted backend has not been deployed. General provider omission
semantics, media/group handling and retention limits are not claimed; the approved
plain-text pilot is deliberately bounded to confirmed conversation/phone pairs.
Actual two-tester hosted exchange, timing/burst/quiet-wake checks, final secure
configuration and migration-before-deploy verification remain TBD-20 **after**
backend review, both founders' explicit merge approvals and merge. No production
migration, hosting configuration, live provider send or account reset occurred in
this allowlist implementation.
