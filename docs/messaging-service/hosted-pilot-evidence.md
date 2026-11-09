# Messaging v1 hosted pilot — 2026-11-09

Production: merged `8377754a3755a1c063c77bcb3a4fa3e33f361718`; Render deploy
`dep-dbovjnui0phs73eutvm0`, live at 16:51:48.440415 UTC. One existing Free
web service/process, restricted `tbd_user_backend`, TLS/session pooling and
exclusive loop ownership verified. Local candidate: 146 disposable PostgreSQL
and controlled HTTP tests passed, zero skips; these are separate from live evidence.

## Release and recovery

Applied only reviewed migration `20261108173259_messaging_service.sql` after
explicit-project dry-run. Existing six Users, four invitations, all grants,
schema ACL and policies matched before/after. Messaging browser access denied;
runtime read/write permissions and immutable-text restriction verified.

Two approved joined testers have privately confirmed one-to-one conversation/
phone pairs and shared-line configuration. Temporary capture hook retired;
final received+ping webhook uses its own secret. Fixed activation remains
2026-11-09T03:30:00Z. Two predeploy A2 texts after this cutoff were explicitly
accepted as catch-up: each produced one stored inbound, one linked outbound,
one send attempt, and exactly two replies in A's original thread. These are
recovery evidence, not fresh hosted texts.

The initial manual deploy failed before serving due to missing COMMS_API_KEY;
the old User release stayed live. During final configuration the Render connector
unexpectedly deployed as part of its environment merge despite auto-deploy off;
Save-only was not honored. No second deploy was triggered by the implementer.
The resulting deployment is the intended SHA. All five Messaging values were
supplied together and startup role/health checks passed. This deviation is
recorded; future Save-only changes require a supported interface with verified
semantics.

Valid provider ping: delivered HTTP 200 on attempt 1, application
webhook_ping_verified at 16:55:54.278444910Z; invalid signature HTTP 401.
Message counts remained two inbound/two outbound after both checks.

## Fresh A and B, then interleaved burst

Each marker matched its exact original inbound and saved outbound provider ID,
line, approved sender, conversation and original text. Each has one linked
outbound and one send attempt. Handset confirmations: A/B single tests each one
reply in its own thread; burst two new replies in each original thread. Exact
handset arrival timestamps were not measured.

| Marker | Source UTC | Backend acceptance UTC | First terminal status observation UTC | Source to acceptance / observation (s) | Later provider snapshot |
| --- | --- | --- | --- | --- | --- |
| A2 live | 16:58:20.549 | 16:58:23.587193 | 16:58:39.450788 | 3.038193 / 18.901788 | read |
| B1 | 17:07:59.290 | 17:08:02.455090 | 17:08:18.306636 | 3.165090 / 19.016636 | delivered |
| A3 | 17:14:11.285 | 17:14:16.896350 | 17:14:16.896350 | 5.611350 / 5.611350 | read |
| B2 | 17:14:11.372 | 17:14:13.639301 | 17:14:13.639301 | 2.267301 / 2.267301 | delivered |
| A4 | 17:14:17.329 | 17:14:20.006249 | 17:14:20.006249 | 2.677249 / 2.677249 | read |
| B3 | 17:14:23.241 | 17:14:25.340290 | 17:14:25.340290 | 2.099290 / 2.099290 | delivered |

A2 live initially observed read; B1 delivered; all burst jobs observed sent at
acceptance. Later snapshots are recorded separately; backend terminal states are
not reclassified as first observations of read/delivered. Comms supplied no
sent_at/delivered_at fields for these replies, so acceptance-to-delivery latency
is unavailable. Supplied read_at: A2 live 16:58:23.395Z, A3 17:14:16.784Z,
A4 17:14:19.912Z. Read timestamps precede local acceptance observations slightly;
do not infer negative latency or substitute read for delivery. Other read times
are unavailable. A's approximate 18-second handset report is separate evidence.

Burst ordering is preserved within each conversation; B progressed while A was
pending. Render logs correlate one claim, one provider send request and applied
terminal result per marker. No queue/timing configuration changed.

## Join

The normal page clears sessions after success. One explicitly authorized sign-in
email to existing Pilot B (new-user creation disabled) supplied a fresh session.
User clicked normal Join and confirmed You're in TBD. Counts remained six Users,
eight inbound/eight outbound; complete User and invitation record digests matched
the pre-Join snapshot, including unchanged Pilot B. Zero message work from repeat
Join. No fresh-user Join is claimed and no identity was recreated.

## Quiet/wake — passed

After the hosted Join check, neither tester sent traffic and the implementer made
no app HTTP/health requests. Control-plane logs show the old process shutting
down at 17:39:20.475050Z and finishing at 17:39:20.577422Z. The advisory lock
was released; counts remained six Users/eight inbound/eight outbound. Monitoring
started at 17:25:54.860727Z; the exact last inbound HTTP time is unavailable, so
this is not a measurement of a full 15-minute idle interval. One transient Render
log-API 503 recovered; it was not treated as app shutdown evidence.

A sent `TBD hosted A5` once at provider source time 17:47:52.258Z, after
8m31.681s of confirmed process downtime. The first received-webhook attempt timed
out; no manual wake request or repeated tester send was made. A new instance
started Uvicorn at 17:48:11.780713Z and completed startup at 17:48:22.039958Z,
29.782s after the inbound source time. Its role/TLS/session-pooler verification
passed and it reacquired the loop lock. Startup history replayed the eight old
inbounds without duplicating rows, then recovered A5 through the same admission
path. The fixed activation cutoff and timing configuration were unchanged.

| A5 stage | UTC | Seconds since inbound source |
| --- | --- | ---: |
| Inbound source | 17:47:52.258000 | 0 |
| Stored inbound and queued linked reply | 17:48:23.530053 | 31.272053 |
| First send attempt | 17:48:25.070354 | 32.812354 |
| Acceptance and observed sent | 17:48:26.526617 | 34.268617 |

Exactly one inbound/linked outbound, one send attempt; later exact-ID Comms
history reported read with read_at 17:48:31.835Z. sent_at and delivered_at are
unavailable; exact handset arrival time and actual delivery latency remain
unavailable. A confirms exactly one new reply in the original thread. The
received-webhook retry delivered HTTP 200 on attempt two and replayed the already
recovered message without another reply. Final counts: six Users, nine inbound,
nine outbound, no nonterminal work; restricted loop ownership reacquired. Wake
and catch-up succeeded without manual intervention. These observations do not
guarantee that every future provider retry will wake a Free instance promptly.
A post-wake health request at 17:52:44.137523Z returned 200/ok; it was made
after recovery and was not used to wake the app.

## Acceptance evidence

| Check | Evidence |
| --- | --- |
| AC1 | Local new/repeated Join and unjoined-sender cases; hosted repeat Join preserved six Users and created zero Messaging rows. |
| AC2 | Local atomicity/constraint/concurrency tests; hosted exact native-ID/text linkage, burst and startup history/webhook replay with no extra rows. |
| AC3 | Local wrong sender/line/conversation/excluded cases; actual signed ping 200, bad signature 401; privately confirmed A+B joined routes. |
| AC4 | Local stable-key/retry/status-window/order cases; hosted one attempt per reply, exact-ID history, per-thread order and independent B progress. |
| AC5 | Local pending/known-ID restart and history-cap cases; actual spin-down, lock release, startup history catch-up and duplicate-safe webhook retry. |
| AC6 | Render claim/provider/result/queue/history logs, SQL times and exact provider statuses above; unavailable fields stated explicitly. |
| AC7 | 146 passing disposable tests, populated User migration rehearsal and production before/after data/grant checks. |
| AC8 | Two approved joined phones, separate same-thread replies, interleaved burst, hosted Join and actual quiet/wake with handset confirmations. |

## Pilot limits

Founder reports Free 100 messages/day and verified at least 20 available before
cutover. Allocation: 2 catch-up sends + 14 fresh-test messages + 4 reserve. The planned pass consumed 16 messages from that allocation
(two catch-up sends and seven fresh inbound/reply exchanges), leaving four allocated reserve messages;
this is not a fresh provider allowance reading. Reset time and whether inbound texts count remain unverified. History list is capped at 200; observed reads were below cap. Actual
longer-term retention and provider-wide omitted classification semantics are
unproven. Pilot admission relies on server-owned confirmed pair/plain-text rule.
Raw native IDs, contacts, keys and records remain private outside Git.

Tracking: [TBD-18](https://linear.app/apt-knows/issue/TBD-18), [TBD-20](https://linear.app/apt-knows/issue/TBD-20), [TBD-17](https://linear.app/apt-knows/issue/TBD-17). Backend: [PR #5](https://github.com/tbd-knows/server/pull/5). Operational instructions: [setup.md](setup.md).
