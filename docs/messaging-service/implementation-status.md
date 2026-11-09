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

**146 passed, zero skipped**, disposable local PostgreSQL over TCP and the actual
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

## Focused review corrections

Messaging startup configures only `tbd.messaging` with an INFO stderr handler and
JSON-message formatting, without propagation or root/provider logger changes.
A subprocess loads the real app through the documented Uvicorn logging settings
(fake configuration, database verification stubbed, no live I/O). Reinitializing
the logger still emits exactly one sanitized event on stderr:

```json
{"event": "logging_smoke", "request_id": "fake-request", "state": "pending"}
```

Comms sends now classify HTTP 401/402/403 before JSON parsing, preserving the
existing 60-second configuration and 3,600-second quota pauses even for empty or
non-JSON bodies. Existing 429 parsing/fallback behavior is unchanged. Focused
empty/HTML tests cover all four status codes. The full disposable PostgreSQL suite
passes **144 tests, zero skipped**; Ruff/format/whitespace pass. One existing
upstream warning remains. Both fixes are ready for focused re-review in PR #5;
TBD-18 stays In Progress. No live provider call, production migration/deployment
or merge occurred for these corrections.

## Final release-candidate preparation

The initial PR/local head matched reviewed aa48cac. Read-only Render/Supabase/
Comms preflight and private Pilot A checks are recorded in
[release-candidate.md](release-candidate.md), together with the exact approved
migration/config/one-deploy/final-ping stop gates and hosted A2/B1/burst/wake pass.
Production still runs the old User release; Messaging is unapplied and undeployed.
Pilot A is approved/joined and mapped privately. Pilot B's approved joined User
reference, one-to-one setup text and authenticated conversation are now verified;
the two distinct pairs are combined privately (0600). No B reply was sent.

The shared PostgreSQL harness now rehearses migration over existing fake User,
Auth, invitation/address data and compares rows, grants/schema ACL and policies.
A real local Uvicorn process proves the restricted role, exclusive delivery-loop
ownership, fake history, healthy HTTP and graceful shutdown/lock release. Added
one small rollout log, `webhook_ping_verified`, only after successful HMAC ping
verification, with a focused no-message-work/bad-signature regression. No new
service/table/adapter/loop or child PR. Final full suite: **146 passed, zero
skipped**; lint/format/whitespace pass, one existing upstream warning. Targeted
rehearsal: **1 passed, zero skipped**. No production mutation or live send.

PR #5 stays open for one founder review of this final diff/private configuration;
prior aa48cac review is not approval of the new candidate. TBD-18 remains In
Progress through approval/merge; TBD-20 remains pending actual hosted verification.
This round stops at the reviewable candidate, not cutover. Close TBD-17 only after
both children and their linked evidence are complete.

Pilot B readiness update: the `app-tbd TBD setup B1` record at
`2026-11-09T03:19:05.543Z` matched the privately approved sender/shared line and
independent conversation in the runtime-key read at `03:22:55.311711Z`. One-to-one
handset confirmation is recorded; founder observed delivery and no automatic
reply. A+B mapping has two entries with A unchanged, saved privately mode 0600.
This documentation-only update changes no runtime/test/migration code from the
146-passing aa55e6d candidate. Hold further tester texts until approved cutover;
PR #5 remains open and no deployment, provider send or registration change occurs.
