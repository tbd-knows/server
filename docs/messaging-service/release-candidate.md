# Messaging v1 — release candidate and one cutover

Status: prepared for final founder review; **not approval to merge or deploy**.
PR #5 stays open. TBD-18 stays In Progress through approval and merge; TBD-20
remains pending live verification. No child PR is needed: this preparation adds
migration/startup rehearsal and one safe signed-ping log to the existing scope.

## Readiness actually checked

- Starting head: `aa48cac4eae717c4ac47b75b9b89f15bcd0e9f61` matched local HEAD
  and PR #5 exactly. Its two backend review findings were already resolved.
- Disposable migration rehearsal now creates fake existing Auth/User/invitation/
  address records under the **released** User schema, snapshots every row plus
  User table grants, schema ACL and RLS policies, applies Messaging, and asserts
  equality. It removes only its own fake rehearsal records afterward. The actual
  release SQL files are used; no production reset or role recreation occurs.
- Real Uvicorn over localhost TCP starts the actual app, verifies database role
  `tbd_user_backend`, owns the delivery advisory lock, completes fake-provider
  history, serves healthy `/healthz`, and shuts down/relinquishes ownership.
  Provider requests are intercepted; no live send/Auth request occurs.
- Full local suite: **146 passed, zero skipped**, one existing upstream warning.
  Ruff/format/whitespace pass. Commands:

```bash
PATH=/opt/homebrew/opt/postgresql@17/bin:$PATH uv run --locked python scripts/run_database_tests.py -q tests/test_database.py::test_release_rehearsal_uvicorn_starts_with_real_role_and_healthy_owned_loop
PATH=/opt/homebrew/opt/postgresql@17/bin:$PATH uv run --locked python scripts/run_database_tests.py -q
uv run --locked ruff check src tests scripts
uv run --locked ruff format --check src tests scripts
git diff --check
```

Read-only production inspection (not a rollout):

| Component | Current observation |
| --- | --- |
| Render | Confirmed TBD workspace; existing `tbd-user-api`, Free, one instance, main, auto-deploy off, `/healthz`; live deploy `dep-dbjrrsvlk1mc73a4jgog`, User release `9d69ab1776af65147fe5237b4f1abb5620a2113d` |
| Public API | Health 200; OpenAPI has no `/webhooks/comms`. One health request took 31.52 seconds; this is existing User Free-host behavior, not a Messaging latency baseline. |
| Supabase | Only migration `20261101103939/user_service`; Messaging schema/table absent. Six Users; backend User grants present; anon/authenticated User schema access denied. |
| Pilot A | Exactly one existing approved joined User matches the private verified phone; saved conversation/phone pair matches signed capture. No contact values published. |
| Comms | Read-only preflight `2026-11-09T03:01:53.547049Z`: Pilot A exact history/route/conversation match; temporary webhook active, received+ping subscribed, secret matches private receiver file; final production webhook count zero. |
| Capture transport | Local receiver responds, but temporary public tunnel is currently unreachable. Active registration is not proof of a working callback. No webhook/tunnel change made during preflight. |
| Pilot B | Joined per founder; intended test phone mismatches the approved phone and is being resolved. No approved B conversation mapping yet. **Hold B setup text.** |

## Complete preparation before approving the final release

1. Resolve Pilot B's phone with the founder privately. Use the approved joined
   phone; do not silently update User Service contacts or allowlist another phone.
   Confirm consent and a one-to-one iMessage thread. After that confirmation and
   with authenticated history access available, give B the shared number from
   **Lines → Live shared line assigned to TBD** and send exactly once:

   ```text
   app-tbd TBD setup B1
   ```

   This is **setup**, not hosted verification; no backend reply is expected.
   Confirm no provider auto reply. Read authenticated history, match sender/line,
   native ID and conversation, and confirm the handset's one-to-one thread.
   Since capture transport is down, use authenticated history rather than claiming
   signed B capture evidence; don't change production registrations in this round.
   If route/history cannot be confirmed, stop and resolve setup before cutover.
2. Combine confirmed A and B conversation/phone pairs privately. The A mapping is
   `/private/tmp/tbd-comms-capture/pilot-conversations.private.json`; don't overwrite
   it with a guessed B pair. Keep keys, numbers and native IDs outside Git.
3. Confirm the actual Free allowance/reset and remaining budget for all planned
   inbound/outbound messages plus setup/retry reserve. Confirm self-hosted mode and
   automated replies off. Pause tester traffic for the entire cutover.
4. Prepare private runtime/setup keys, the reviewed SQL checksum, intended service,
   project, final endpoint and environment diff for both founders. After setup,
   select/record the proposed fixed UTC activation cutoff and eligible backlog
   privately for review; hold further tester traffic until green light. If an
   activation cutoff was previously established, preserve it. Setup key has
   `comms_webhooks`; runtime has `comms_read`/`comms_send`; setup key never goes on
   Render. Verify the DB URL is restricted-role TLS direct/session mode, not a
   transaction pooler. Preserve existing User/Auth configuration and all data.

## Ordered production cutover — execute only after final approval

Stop at every failed check. This is one planned production deployment, not a
sequence of trial deploys. Current stack is just PR #5. If a child becomes necessary, revise this candidate
and review the entire stack before approving; merge the base before dependents
and deploy only the final merged main commit.

1. **Both founders explicitly approve the final code and private release config.**
   Approval names the final head; prior approval/re-review of aa48cac does not
   approve these preparation changes. Merge the reviewed PR and record the actual
   resulting main SHA. Do not change code afterward without renewed review.
2. **Apply reviewed migration before compatible code.** Verify target project
   `xclftpnjwrmakooaaxik`, current migration history and existing User snapshots.
   The only pending release file must be `20261108173259_messaging_service.sql`.
   Its SHA-256 is
   `9a6e59f3665b5c13ae11f40e2041a0e4ef3fa3eff19e6e1c561552d806d8bbfb`.
   Use secure CLI login/password environment, not password arguments/history:

   ```bash
   supabase migration list --linked
   supabase db push --linked --skip-vault --dry-run
   # Only after confirming that exact reviewed file and target:
   supabase db push --linked --skip-vault
   ```

   Do not use include-all/roles/seed or reset. Recheck migration version, existing
   User data/grants, new Messaging grants/RLS and restricted-role operations.
   If any differ unexpectedly, **stop before deployment**; don't undo data or
   reapply the released User migration.
3. **Prepare final signed webhook and exact private config, without deploying.**
   Record/disable the temporary capture registration through supported Comms
   management controls; leave raw evidence and its signing secret private/intact.
   No guessed update/delete API. Confirm no active temporary receiver will consume
   future tester traffic. List before creating, and reuse/reconcile an existing
   exact final URL rather than blindly creating duplicates.

   Final URL: `https://tbd-user-api.onrender.com/webhooks/comms`.
   Subscribe to `comms.message.received` and `comms.ping`. Use the setup key only
   here. Use a separate private directory (for example
   `/private/tmp/tbd-comms-production`) for the returned final registration/secret;
   do not overwrite `/private/tmp/tbd-comms-capture/webhook-secret`.
   The old User app may return 404 on this new path until deployment; testers stay
   paused and no ping is requested yet. Provider retries/history cover that brief
   transition; don't call this final endpoint verified before the deploy.

   Set Render's five Messaging values using **Save only**, not either deploy
   option: `COMMS_API_KEY`, `COMMS_WEBHOOK_SECRET` (the final registration's secret),
   `COMMS_LINE_E164`, `COMMS_PILOT_CONVERSATIONS` (confirmed A+B pairs), and
   `MESSAGING_ACTIVATED_AT`. Copy raw allowlist JSON into Render without shell quotes; keep auto-deploy off.
   If activation has never been
   established, install the founder-reviewed UTC cutoff selected **after all setup messages/A1/B routing**
   and before deployment/green light. If it is already established, preserve it
   and review the eligible backlog; don't silently advance it. Preserve this fixed
   value on later releases. Unexpected backlog or an uncertain secret/route means
   **stop before deployment**.
4. **Deploy the final merged SHA once** on the existing Render service. Record the
   deploy ID; wait for that specific deploy to become live. Confirm Render's
   deployed commit equals the recorded merged SHA, startup's restricted role/TLS
   evidence, real advisory ownership, history/queue logs and healthy `/healthz`.
   Old public health alone is not proof of the new commit. If deployment fails,
   Render can retain the old instance: stop, inspect logs and return findings;
   don't repeatedly deploy, reset data or claim the pass completed.
5. **Verify the final webhook before tester green light.** Request one signed
   `comms.ping` with the setup key against the final webhook ID. Require provider
   HTTP 200 and the new app's `webhook_ping_verified` event. This new log contains
   only the event name/request UUID and is emitted only after HMAC validation.
   An unsigned/bad-signature request must return 401. Require zero message rows or
   outbound jobs from the ping. A failed/missing ping or role/loop check means
   **hold all tester texts**, reconcile configuration privately, and don't bypass
   HMAC or perform another send/deploy as a trial fix.
6. Confirm the temporary registration is inactive/deleted and then stop its local
   receiver/tunnel. Retain private evidence until the rollout is reconciled.
   Do not leave both registrations active or publish any signing secret.

## One hosted verification pass after all gates above

Use the existing A thread and B's independently confirmed setup thread. No extra
one-off script sends replies: the deployed loop is the only reply source. A1 was
sent before Messaging deployment and is **not hosted evidence**. Setup/old history
must not produce a surprise catch-up reply under the reviewed activation cutoff.

| Check | Tester action | Expected result |
| --- | --- | --- |
| Fresh A | A sends `TBD hosted A2` once in existing thread | One inbound + one linked outbound, exactly one `Received your text.` in that original blue-iMessage thread |
| Independent B | B sends `TBD hosted B1` once in B's setup thread | Same invariant; B route/phone/conversation stays separate from A |
| Join | Existing session repeats Join; use a newly approved consenting Join only if available/authorized | Same User; zero outbound rows from Join. Never delete/recreate an existing identity to force a fresh Join. Missing fresh-Join access is reported, not fabricated. |
| Small interleaved burst | A: `TBD hosted A3`; B: `TBD hosted B2`; A: `TBD hosted A4`; B: `TBD hosted B3` | One pair/reply per distinct text, per-conversation order and independent progress, no cross-thread reply |
| Quiet/wake | After confirmed Free-host sleep (record actual quiet interval), A sends `TBD hosted A5` once; make one explicit public wake request if needed | Fixed activation preserved; retained pending/inbound work resumes with no duplicate. A pending row/text may not itself wake Render; measure the actual wake path. |

For each marker, retain a private evidence row: tester label and handset send/
arrival observation; provider native inbound ID and original timestamp; local
inbound UUID/text; exactly one outbound UUID linked by reply_to; conversation/line
match; created/attempt/accepted/sent-observed timestamps; observed provider status
and exact-ID history. Pair the SQL evidence with the handset's exact-one-reply
confirmation. Compute queue/API/observed intervals; keep provider clocks and poll
observation delay separate. Don't claim read receipt availability or a latency
budget from acceptance alone. Publish sanitized labels/IDs and observed timings,
not contacts, bodies beyond the controlled test markers, raw responses or keys.

Use existing protected SQL/log tools for row counts and health; do not add a
schema, dashboard or separate worker. Wrong-route/duplicate/missing reply or
unexpected auto reply is a stop condition: pause tests and inspect the saved job
and exact provider ID. Known-ID unknown is GET reconciliation, never a new key.
If provider routing drops a marker, don't ask for repeated sends until its cause
is understood. Record failures honestly rather than restarting the entire pass.

After approvals + merge, complete TBD-18 with review/test/migration links. Complete
TBD-20 only when the hosted evidence and limitations are recorded. Close TBD-17
only after both children are complete and AC1–AC8 evidence is linked. This round
stops at the release candidate; no production mutation/deployment is performed.
