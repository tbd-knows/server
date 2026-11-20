# Agent v1 backend

Agent enrolls one approved conversation per joined User. Messaging records the inbound and its selected producer in the same transaction. PostgreSQL holds enrollment, the brief and preferences, and ordered turns. The FastAPI lifespan runs two bounded Agent workers. Messaging remains the owner of Comms sends, reply ordering, status reconciliation, and contact cards.

## Configuration

Apply `20261119110533_agent_service.sql` before deploying the compatible backend. The migration adds the private `agent_service` schema and grants access to `tbd_user_backend`. The migration can stay applied throughout rollback.

The deployed program reads the existing Messaging line and conversation allowlist. New Users start in Fixed. The app can run without either Agent environment variable.

- `TBD_AGENT_MODEL` names the TBD-approved lightweight OpenAI model. Use the approved dated revision where OpenAI offers one.
- `TBD_AGENT_OPENAI_API_KEY` is the TBD-owned inference credential.

The adapter uses foreground OpenAI Responses requests with `store: false` and a strict schema. Provider abuse-monitoring retention depends on the API project's data controls. Verify the pilot project's settings before real User traffic. [OpenAI data controls](https://developers.openai.com/api/docs/guides/your-data)

Each turn pins the model, prompt and schema revisions, and resource limits. The initial limits are two possible attempts, a ten-second attempt lease, a 24-second admission deadline, 1,200 output tokens, and a 16,000-byte budget for projected inputs, instructions, and schema. The initial timestamp allowance is two seconds and a brief expires seven days after its last accepted edit. Earlier briefs derive that time from their stored inbound provenance. Tune these with controlled evidence. Drain to Fixed before changing incompatible inference configuration.

Brief constraints require explicit first-party wording such as “My budget is $65” or “I need size 10,” or an eligible answer to a typed question. Listing prices and sizes stay in option entries.

### Supported preference controls

Specify a preference key, item and recipient. The keys are size, fit, condition, brand, and material. The first-person recipient is `me`.

- `Remember size for shoes for me is 8 wide`
- `Correct size for shoes for me to 9 wide`
- `Show my preferences`
- `Forget size for shoes for me` or `Forget all preferences`
- `Pause memory` or `Resume memory`

The app handles these commands locally. Forget and pause clear the brief and advance both context cutoffs. If combined with a shopping request, the reply asks the User to resend the shopping request.

## Cutover and rollback

1. Deploy the compatible revision in Fixed. Verify Join, signed webhook intake, fixed replies and contact cards. Confirm fixed-only webhook and history workers have exited.
2. With the deployed configuration available privately, run `uv run --locked python scripts/agent_mode.py USER_UUID agent --conversation CONVERSATION` for each approved route. Record the deployed revision and model configuration separately from the test results.
3. For a normal drain, use `agent_mode.py USER_UUID fallback`. Fallback stops new inference and settles pending turns in order. Use `agent_mode.py USER_UUID fixed` after backlog drains. The command checks the backlog under the same User lock as intake. Repeat for each enrollment.
4. To revoke or restore a route, follow [Route revocation and restoration](#route-revocation-and-restoration).
5. Before switching to a legacy sender, verify every enrollment is Fixed, all turns are terminal, and all linked outbound messages are in legacy no-POST states. Stop stale compatible workers and wait for in-flight provider requests to end. Keep a guard-compatible build serving until these checks hold.

The following checks report only counts. Expect zeros before switching to a legacy sender.

```sql
SELECT count(*) FROM agent_service.user_state WHERE mode <> 'fixed';
SELECT count(*) FROM agent_service.turns WHERE outcome IS NULL;
SELECT count(*)
FROM messaging.messages m JOIN agent_service.turns t ON t.inbound_id = m.reply_to_message_id
WHERE m.state IN ('pending', 'sending');
```

An unknown submission can stay `uncertain` with no further POST. A known provider ID in `uncertain` or `provider_queued` supports exact-ID reconciliation. Track provider acceptance, observed send, and actual handset receipt separately.

### Route revocation and restoration

1. Remove the exact route from the Messaging allowlist. Verify that all workers using the old configuration have exited.
2. With the route still excluded, run `uv run --locked python scripts/agent_mode.py USER_UUID fallback`. Once the Agent backlog has drained, run `uv run --locked python scripts/agent_mode.py USER_UUID fixed`. A successful Fixed transition confirms that acknowledged Agent work has settled.
3. With the route still excluded, verify that the delivery owner has settled or suppressed every Agent reply that could still POST for that User and route across enrollments. The delivery owner suppresses paused and delayed replies and can reconcile known provider IDs read-only. Keep the route excluded while any possible submission without a provider ID remains unresolved.
4. Restore the allowlist entry after those checks pass. To return the route to Agent, start a fresh enrollment with `uv run --locked python scripts/agent_mode.py USER_UUID agent --conversation CONVERSATION`.

For step 3, set the `psql` variables privately for the target User and exact account, peer, and conversation. Confirm the count is zero before restoring the allowlist entry.

```sql
SELECT count(*) AS unresolved_agent_replies
FROM agent_service.turns t
JOIN messaging.messages m ON m.reply_to_message_id = t.inbound_id
WHERE m.user_id = :'user_id'::uuid
  AND (m.account_ref, m.peer_ref, m.conversation_id) =
      (:'account_ref', :'peer_ref', :'conversation_id')
  AND m.channel = 'imessage'
  AND m.direction = 'outbound'
  AND m.dedupe_key = 'reply:' || t.inbound_id::text
  AND m.provider_message_id IS NULL
  AND m.state NOT IN ('sent', 'failed')
  AND (
    (m.state IN ('pending', 'sending') AND NOT t.delivery_suppressed)
    OR m.state = 'uncertain'
    OR t.submission_possible
    OR t.delivery_grant IS NOT NULL
  );
```

## Verification

Run the existing checks with `initdb`, `pg_ctl`, and `createdb` available on PATH.

```sh
uv run --locked python scripts/run_database_tests.py -q
uv run --locked ruff check .
```

With the approved synthetic test model and credentials in the environment, run the repeatable multi-turn eval directly against the durable runner.

```sh
TBD_RUN_AGENT_EVAL=1 uv run --locked python scripts/run_database_tests.py -q -s \
  -o log_cli=true --log-cli-level=INFO \
  -k 'agent_db_repeated_model_eval or agent_db_paid_brief_scope_transition'
```

The eval outputs recommendation, material question and preference reuse counts, privacy and state errors, attempt timings and known usage. Agent logs correlate queue, context, inference, and commit timings. Missing usage stays null. Reconcile possibly billed dispatches without usage with provider billing before reporting cost per useful reply.

The report includes the exact Git SHA and a row per case and repeat with its outcome, fallback reason, and attempt timings. It scores a useful gift size clarification separately from a saved question target. For the approved GPT-5.4 mini snapshot, the report estimates known spend using standard list rates. Spend per useful reply is calculated only when every attempt has priceable usage, and counts useful recommendations, correct no-fit replies, and useful gift clarifications. Reconcile the estimate with billed usage. [OpenAI model rates](https://developers.openai.com/api/docs/models/gpt-5.4-mini)

The separate `commerce-v1-brief-scope` report uses controlled setup turns and two live model turns. It checks a recommendation from a carried shoes brief, then sends a backpack request after observing a shoes question as sent. The report verifies that the old brief and question cannot apply to the backpack request. These turns have their own usage and timing records.
