# TBD-18 — Comms Free revision checkpoint

Updated 2026-11-08. **In Progress; draft PR #5 remains open.** Governing design:
`6b2c67e0eaccea7bc7be55827a4dfe92c36c6e93` (docs PR #6, now merged on main).
The implementation branch includes current main `3370855` via a normal merge.
Work is in the original `server` checkout on `messaging-v1-implementer`.

## Implemented and tested locally

- Removed the draft Join welcome hook and pilot command. User Store matches main.
  New/repeated/concurrent Join creates zero Messaging rows.
- One private table retains immutable User/line/peer/conversation/channel routes,
  native/producer dedupe, reply foreign keys, queue state and first-attempt time.
  Only the **unapplied** Messaging migration changed; the released User migration
  is unchanged. No production migration or reset occurred.
- `/webhooks/comms` verifies raw-body `X-Osis-Signature`, reads the signed event and
  nested `data.message`, and admits through the canonical joined User transaction.
  The provisional documented mapping requires inbound/direct/iMessage, line,
  `author.external_id`, conversation, original time and attachments classification.
  It has **not** been validated against a real Free-account event/history pair.
- Atomic inbound plus reply, exact text, conflicting route rejection and replay
  reuse. History updates preserve original stored text. No attachment URLs,
  downloads or transcripts are stored. Unknown/seeded/old messages are ignored.
- Comms Bearer adapter sends the stored conversation/body with explicit iMessage
  and outbound UUID as `Idempotency-Key`. Retry payload/key stays fixed. First
  attempt anchors the conservative 24-hour window; after it, no-ID work is held
  uncertain. Known-ID status reads match exact IDs/routes in outbound history.
- One session-owned lifecycle loop, per-conversation ordering, independent routes,
  persisted pauses, restart recovery and result-save retry. Requests have a total
  ten-second deadline. History uses fixed activation since, inbound direction and
  limit 200, oldest first, reporting possible incomplete coverage at the cap.
- Safe correlated logs/queue queries, loop health, config examples and runbook.
  Pacing is 1.1 seconds between completed API calls; actual Free behavior is pending.

## Executed verification

```bash
PATH=/opt/homebrew/opt/postgresql@17/bin:$PATH uv run --locked python scripts/run_database_tests.py -q
uv run --locked ruff check src tests scripts
uv run --locked ruff format --check src tests scripts
git diff --check
```

**120 passed, zero skipped**, disposable local PostgreSQL over TCP and restricted
backend SQL role; one existing Starlette/AnyIO deprecation warning. The full suite
includes User regression coverage and controlled Comms HTTP fixtures. The initial
actual run found an indeterminate SQL parameter type; an explicit cast fixed it.
The previous sandbox/DNS blockers are resolved; they are historical, not current.
No automated test uses the live Comms account or hosted database.

Coverage includes schema/browser denial, immutable columns, inbound state/FK
checks, concurrent Join/admission/replay, transaction rollback, exact text,
unknown/unjoined seeds and activation/User-creation boundaries, history edit
preservation, distinct identical messages, stable retry UUID/first-attempt time,
expiry uncertainty, exact-ID states, stale results, route ordering, independent
Users, advisory ownership, and HTTP intake-to-SQL-to-fake-provider acceptance.
Fixtures are **synthetic documentation examples**, not sanitized real evidence.

## Early Free-account proof — pending access

A temporary signature-only capture receiver and HTTPS tunnel are prepared. It
returns 503 until its Comms secret is installed and stores verified raw evidence
privately outside Git with mode 0600. No webhook has been registered, no key has
been used, and no live reply has been sent. It is not a production deployment.
Setup/capture scripts are documented in [setup.md](setup.md); runtime never gets
the separate setup key. Registration includes received and ping events.

The user has been asked to select **Start Free**, confirm Free/shared line and the
exact first-text/keyword instructions, confirm auto replies off, provide only a
private env-file path for the separate scoped keys, and identify a consenting
joined tester privately. Those responses/access are still pending.

Before claiming provider compatibility, capture a signed direct iMessage event
and its matching inbound history record, confirm line/sender/time/audience and
content mapping, send one stable-key reply in that conversation, read its outcome
and obtain same-thread iMessage confirmation. Confirm how routing texts are
consumed and whether an agent object is needed with automated replies disabled.
Record actual Free allowance/reset, retention/history cap and any limitations.
Publish only sanitized fixtures; raw contacts, signatures, bodies and credentials
stay private. No current phone, routing or Free API capability claim is made.

## Acceptance status

| AC | Evidence / remaining |
| --- | --- |
| AC1 eligibility | Local PostgreSQL: Join sends nothing; joined senders eligible; unknown/seeded senders ignored |
| AC2 storage | Local transaction/concurrency/HTTP proof; real webhook/history fixture validation pending |
| AC3 identity/content | Signature/strict documented mapping tests pass; actual shared-line/media/excluded-event mapping pending |
| AC4 delivery | Controlled stable-key/retry/window/status/order proof; real same-conversation reply and account limits pending |
| AC5 recovery | Local ownership/history/retry tests; actual Free history window and retained-message/wake proof pending |
| AC6 observability | Local safe logs and state checks; actual provider baseline belongs to early proof/TBD-20 |
| AC7 safety | Full isolated PostgreSQL and User regressions pass; no hosted data changed |

PR #5 stays draft pending provider evidence and any corrections it reveals.
TBD-18 and parent remain open. Full hosted proof with two joined testers is TBD-20
**after** backend review, explicit founder merge approvals and merge. No reviewed
backend deployment, production schema change or hosted verification occurred.

## Pilot A capture — 2026-11-08 continuation

The receiver saved a signature-verified `comms.ping` and then a
signature-verified `comms.message.received`. Pilot A reports one blue iMessage
and no automatic reply visible at screenshot time. The captured original message
time is `2026-11-08T22:38:34.756Z`, with envelope `sentAt` 78 ms later. The full
controlled routing text was retained. The signed record includes line, peer,
contact, conversation, direction/channel and original time; **audience and
attachments are both absent**. The user's history observation reports the same
omissions; agent-side API comparison still awaits the runtime-key command.

This is a concrete mapping limitation. Missing fields are not proof of direct
text. Shared-line inability to create outbound groups does not prove inbound
records can never be groups. Production normalization remains strict. Before a
general admission rule changes, obtain a provider contract or positive direct/
content metadata, with media and excluded-event fixtures and design review.

A one-off manual proof script now requires explicit handset confirmation of a
one-to-one text-only thread, reads matching inbound history and conversation
metadata, checks peer/contact/line/conversation identities, saves a stable UUID
before any send, and sends exactly one explicit iMessage reply. Retrying preserves
the key; once an ID is known it reads history rather than posting again. This
manual proof is separate from automatic production admission and cannot establish
its missing classification contract. Its six focused synthetic tests pass.
Actual history comparison, sanitized pair and reply/handset outcome remain pending
execution of that hidden runtime-key command. No live reply is claimed yet.

## Pilot A completed one controlled exchange

See [actual provider evidence](pilot-a-evidence.md) and the
[sanitized webhook/history pair plus conversation and reply records](fixtures/comms-free-pilot-a.json).
The private comparisons passed, one stable-key manual explicit-iMessage reply was
accepted, and Pilot A confirmed exactly one arrival in the original thread.
Provider acceptance and captured exact-ID outbound history still reported unknown
with `delivery_unknown: true`; preserve that distinction from handset evidence.

Both incoming records omit audience/attachments. The production adapter remains
strict, so this does not prove automatic backend admission works with the actual
Free shape. Provider classification semantics/metadata and terminal delivery
state behavior remain material integration questions. PR #5 stays draft;
TBD-18 remains In Progress. No production migration, deployment or merge occurred.

## Exact-ID history read resolves the unknown reply outcome

The fresh HTTP 200 history result at `2026-11-08T23:05:38.437356Z` matches the
saved outbound ID and route and reports raw status `read`, with provider
`read_at: 2026-11-08T22:56:05.218Z`. The earlier helper labeled this unrecognized
because its allowlist omitted read; the provider field was present. Original
unknown acceptance/history remain in the evidence fixture beside the later read.

Per the approved correction, verified `read` maps to existing local `sent`, with
`observed_status=read`; only the unapplied Messaging migration's status constraint
is extended. No new local state or production migration is added. A focused real
PostgreSQL + fake HTTP regression verifies unknown holds the next conversation
job, a different ID's read does not release it, and exact-ID read records terminal
success/observation and releases the next job. Stale unknown cannot regress it.
Automatic intake still rejects the missing audience/attachments shape pending
provider clarification. No live API/send was needed for this code correction.

Verification for this correction: full disposable PostgreSQL harness **133 passed,
zero skipped**; Ruff, formatting and whitespace checks pass. One existing upstream
Starlette/AnyIO deprecation warning remains. No live send or hosted schema change.
