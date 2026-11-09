# Messaging v1 — implementation status

Updated 2026-11-09. Backend [PR #5](https://github.com/tbd-knows/server/pull/5)
is merged at `8377754a3755a1c063c77bcb3a4fa3e33f361718` and deployed on the
existing Render service, deploy `dep-dbovjnui0phs73eutvm0`. Both founders' final
release/cutoff approvals were explicitly confirmed by the user before cutover.

The reviewed Messaging migration is applied. Production User data/grants survived;
runtime role, TLS/session pooling, advisory ownership, health and final signed
ping passed. Confirmed A+B conversation/phone pairs and all credentials remain in
private configuration. Activation is fixed at `2026-11-09T03:30:00Z`.

## Implemented contract

Join sends nothing. Verified webhook and authenticated history use the same
approved conversation/phone/shared-line admission rule and canonical joined-User
lookup. Missing audience/attachments permits only nonempty plain text for those
confirmed pairs; explicit excluded evidence overrides approval. This remains a
bounded pilot rule, not a provider-wide omission guarantee.

One private table atomically stores inbound text and one queued reply. One
session-owned delivery loop sends explicit iMessage replies in the stored
conversation using the outbound UUID as its stable key. Known IDs use exact-ID
history; uncertainty, retry windows, account pauses and per-conversation order
remain as designed. Sent/delivered/read map to local sent while preserving the
observed provider status. History is capped at 200 and uses the fixed activation.
No Join welcome, Agent execution, or extra service/table/loop was added. Timing
settings stayed unchanged throughout the hosted pass.

## Verification

Runtime/test candidate `aa55e6d94679940fa091125b764ea87e4fd384ae`: **146 passed,
zero skipped**, using disposable local PostgreSQL and controlled HTTP. Ruff,
format and whitespace checks passed; one existing upstream Starlette/AnyIO warning.
The later pre-merge documentation commit changed no runtime/test/migration code.
Automated tests did not contact live Comms or use the hosted database.

Coverage includes constraints, atomicity, identity/permissions, immutable routing,
concurrency/replay, stable-key retries/window limits, exact-ID unknown-to-read
reconciliation, history cap, restricted-role Uvicorn startup/loop ownership and
User Service regressions. The populated released-User-schema migration rehearsal
preserves existing rows, grants/schema ACL and policies.

The two backend review fixes are included: only tbd.messaging gets INFO stderr
configuration, and Comms HTTP 401/402/403/429 classification occurs before JSON
parsing, including empty/non-JSON response tests.

## Hosted evidence and operations

[Hosted pilot evidence](hosted-pilot-evidence.md) records real A/B replies, four
interleaved messages, zero outgoing rows from repeat Join, and actual spin-down/
startup recovery. Each eligible text has one inbound, one linked outbound and one
send attempt. Final totals: six Users, nine inbound/nine outbound, no pending
jobs. Both tester threads and exact-one reply counts are handset-confirmed.

Provider sent_at/delivered_at fields were unavailable; backend status-observation
times are reported separately from handset observations. Free allowance reset
and inbound accounting, longer-term history retention, and general omitted-field
semantics remain documented pilot limits. No latency target or tuning was added.

[setup.md](setup.md) contains ongoing operations. The
[release checklist](release-candidate.md) is an archived pre-cutover plan; actual
procedural deviations and results are in the hosted evidence. The earlier
[Pilot A account proof](pilot-a-evidence.md) remains separate from deployed proof.
This evidence update changes documentation only; it requires no redeployment.
