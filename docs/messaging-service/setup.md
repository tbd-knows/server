# Messaging v1 setup (draft, not deployment-ready)

Reuse the existing Supabase project and Render service. Do not deploy until the
provider classification mapping and database verification blockers in
[implementation-status.md](implementation-status.md) are resolved and the code is
reviewed. Both founders must explicitly approve merging. Phone verification is
TBD-20 after merge; automated tests use fake HTTP and a disposable local database.

## Configuration and release order

Keep `DATABASE_URL` on the existing restricted backend role, using TLS and direct
or **session** pooling (port 5432). Transaction pooling is incompatible with the
session advisory lock. Keep one Render process; advisory ownership also prevents
overlapping deployments sending concurrently. No new service or paid plan.

Set the four server-only values in `.env.example`: API key with message send/read
and number-read permissions, webhook secret, dedicated E.164 number, and the
fixed timezone-aware `MESSAGING_ACTIVATED_AT`. Never advance the activation cutoff
on deployment. Store credentials in Render's secure environment, not Git/CLI
arguments. Apply the reviewed `20261108173259_messaging_service.sql` explicitly
**before** compatible deployment; preserve the applied User migration and data.
No migrations run at startup. Startup checks schema and permissions.

Configure the bridge/iPhone using provider instructions. Turn its own autoresponder
off. Register HTTPS `/webhooks/texting-blue` for `message.received`, filtered to
the dedicated number. Store the secret shown at registration. Verify actual
payload/history classification before enabling intake. Published examples alone
are insufficient; current adapter rejects unclassified records with 422.

For explicitly selected existing joined pilot Users:

```bash
uv run --locked python -m tbd_server.messaging.pilot USER_UUID [OTHER_USER_UUID ...]
```

This queues welcomes atomically, prints message UUIDs, and never contacts the
provider. A seed without completed Join is rejected. Repeated selection/Join
returns the original welcome. The loop sends later; queued does not mean sent.

## Tests

Run from `server` with local PostgreSQL binaries on PATH:

```bash
uv run --locked python scripts/run_database_tests.py -q
uv run --locked ruff check src tests scripts
uv run --locked ruff format --check src tests scripts
```

The harness creates/stops its own disposable PostgreSQL database, applies both
release migrations, and uses the restricted role. Never supply the hosted DB as
`TEST_DATABASE_URL`. Socket-restricted agents cannot execute that harness; run in
a normal terminal and report the result. Collection is not passing evidence.

## Operations

Inspect through an authorized SQL session; do not publish rows containing text or
contacts. These queries omit them:

```sql
SELECT state, count(*), min(created_at) AS oldest
FROM messaging.messages WHERE direction='outbound' GROUP BY state;
SELECT id, state, attempt_id, attempt_count, error_code, next_action_at,
       created_at, attempt_at, accepted_at, sent_observed_at
FROM messaging.messages WHERE direction='outbound' AND state NOT IN ('sent','failed')
ORDER BY queue_sequence;
```

Do not turn `uncertain` into `pending` without evidence of non-acceptance. With a
provider ID the loop reads that job; temporary 404s do not trigger another POST.
Without an ID, generic text plus approximate time is not matching evidence.
Quota pauses last one hour, account configuration pauses 30 seconds, rate limits
honor numeric Retry-After (default 30). Persisted due/error fields hold sends for
all Users on the account. Fix credentials/configuration securely; do not overwrite
message routes or replay keys. Only sent/failed releases the route's next job.

`/healthz` fails when the required loop stops/stalls. Provider outages alone do
not fail health. Logs contain safe stage IDs/codes and timing, never message
bodies, contacts or unsanitized HTTP errors. Use SQL above for queue counts.
Render Free sleeping suspends the loop; pending SQL does not wake Render. On wake,
full retained history sweeps recover bridge-captured messages after activation
and User creation. History cursor failures are visible and the sweep retries.

If the provider disables the webhook after failed acknowledgments, correct the
cause, re-enable the existing webhook using provider management tools, and allow
history catch-up. Do not create a second webhook as a recovery shortcut. Account
access is needed to verify the actual re-enablement procedure.

Hosted verification must separately record welcome/reply in the correct iMessage
thread/number, ordinary and burst timings, interleaved Users, quiet/wake behavior,
actual Free-tier caps and device contact. Acceptance and sent observation are
separate timestamps; polling adds observation delay. Neither proves recipient
delivery. Do not claim a hosted latency baseline from fixtures.
