# TBD v1 — Agent Service

## TLDR

A joined User texts their personal commerce assistant in iMessage. Agent compares options supplied in chat, carries one shopping brief, and reuses preferences the User has saved. The User can inspect, correct, forget, pause, and resume preference memory from the same thread.

Build inside the existing Python/FastAPI server on Render. Use Supabase Postgres for per-User state and durable turns. Start with a lightweight OpenAI model selected and paid for by TBD. [Approved architecture](architecture.excalidraw).

## Background Information / Context

User Service establishes the canonical User ID at Join. A joined User with an approved direct iMessage route is eligible for Agent enrollment. The pilot enrolls two threads. Each enrolled Entity has its own brief and preferences in the shared Agent runtime.

Messaging already verifies inbound iMessages, deduplicates webhook and history delivery, and sends a linked fixed reply. Agent supplies the reply for enrolled threads after the inbound commits. The platform’s Transactions and Orchestration services build on this Entity-level capability.

## Design and Architecture

### 1. Service boundaries and state

| Service | Responsibility |
| --- | --- |
| User | Owns Join, identity, profile, and addresses. Agent reads only the needed fields for the resolved User. |
| Messaging | Owns `POST /webhooks/comms`, history, approved routes, the reply queue, and Comms delivery. |
| Agent | Owns ordered turns, context, the harness, the active brief, and standing preferences. The existing server lifespan runs a bounded Agent runner. |
| Transactions · Stripe | Owns verified payment state and execution when available. For the Agent pilot, clear requests to order, pay, or verify status get an app-written unavailable response. |

Add two private records in one additive migration. Use the restricted server database role. Reuse the inbound ID, route, sequence, timestamps, and reply link from Messaging.

| Record | Minimum state |
| --- | --- |
| Agent turn | Inbound UUID, enrollment, outcome, inference deadline from admission, attempt token and lease, attempt budget, dispatch grant, pinned execution config, and optional typed question target. |
| Agent User state | Canonical User ID, pilot mode and enrollment, exact route binding, brief, scoped preferences, pause state, source watermark, and context reset cutoffs. |

### 2. Admit and process a turn

1. **Admit.** After existing signature, direct-route, content, and joined-User checks, lock the canonical User row. Read Agent mode and enrollment in a subsequent Read Committed statement. The exact enrolled route in Agent or Fallback commits one pending turn with the inbound before ACK. Other Messaging-approved routes use Fixed. Native-ID replay retains its original producer.
2. **Take the head.** Process the oldest unresolved inbound by Messaging sequence per User. Bound concurrency across Users. Recheck route, enrollment, mode, and freshness. A revoked route closes pending work without a provider call or new reply.
3. **Handle local outcomes.** Process sensitive input, stale text, operator Fallback, clear unsupported transaction requests, and supported preference controls locally. An eligible preference control in Agent can complete after the inference deadline. Expired ordinary turns use a local fallback so the next turn can progress.
4. **Grant inference.** Claim an attempt with a token and lease and debit the persisted attempt budget. After assembling context, use the User lock to recheck current head, token, live DB time, lease, deadline, route, enrollment, and mode. Persist one dispatch grant. Start only after confirmed grant commit and cap call time by the remaining lease and deadline. Disable SDK retries. Release locks and DB connections before inference, and keep model I/O off the request and delivery paths. A grant is never replayed on restart.

### 3. Assemble context and validate a response

| Context | What the harness receives |
| --- | --- |
| Recent turns | Current text and eligible terminal ordinary turns with lower inbound sequence, scoped to the same User, route, and enrollment. Exclude stale, blocked, and memory-control turns and their replies. Keep safe User text after failed inference and exclude fixed and fallback reply text. |
| Active brief | One brief for the item and recipient, with User-established constraints, deadline, and supplied option claims. Material values link to their source inbound. |
| Standing preferences | Relevant typed values for fit and size, condition, brand, and material, scoped to item and recipient. |

Before projecting context or editing state, require `source_at <= created_at + skew_allowance`, source time at or after enrollment and its latest fresh completion, and both source time and sequence after their reset cutoffs. Ask for a restatement when any check fails. Equal source times use admission order for self-contained requests.

An earlier Agent reply enters context only when its parent is eligible and `sent_observed_at < min(created_at, source_at - skew_allowance)` for the current inbound. For an implicit brief edit, inspect the immediately preceding Agent turn before filtering. Its exact linked reply must pass that check, match the current brief, and contain one clear question rendered from a typed target. Use the opening inbound UUID as the brief ID and store it with the target. A bare “yes” can confirm one precise field and value. An open question needs a value. Otherwise ask for the field and value.

Apply explicit current constraints first, then same-recipient brief constraints, then compatible preferences. Ask when an unknown gift size matters. Keep one-off limits on the brief and honor a clear switch or close. A seller price is an option claim. A material edit needs evidence in the current User text or a valid typed answer to the eligible question. Clarify unsupported edits and keep the existing brief. Use source IDs for provenance without reloading excluded text.

**Preference controls.** Recognize a small set of clear first-party save, correct, inspect, forget, pause, and resume requests before inference. Quoted instructions, bare assent, and model output confer no write authority. A scoped forget deletes the preference if present, clears the brief, and advances both context cutoffs even when the value appeared only in chat. Pause uses the same reset and suspends preference use and writes while keeping inspection and deletion available. Confirm only the committed outcome. If a clear forget or pause is mixed with shopping, apply the control and ask the User to resend the shopping request. Messaging owns transcript retention.

**Sensitive input.** Clearly identified payment credentials or authentication secrets block the whole turn from model input, brief or preference edits, and later context. Reply locally without quoting the input. Use this gate before any proposed preference write.

**Harness.** Use one bounded structured generation for ordinary work, with a thin adapter returning reply text, proposed brief edits, and an optional follow-up target. Bound brief options, standing preference keys, and the entire prompt. Trim optional material first and ask the User to narrow the request when required context exceeds the budget. Use supplied facts, name unknowns, and keep action confirmations app-owned. Never request card details in chat. Validate the complete result and the Messaging reply contract of nonempty, database-safe text up to 5,000 characters. Refusal, invalid output, unavailable config, or exhausted transient retries yields a local fallback with zero proposed edits.

### 4. Commit and deliver

Under the User lock, recheck current ownership, enrollment, route, and mode. For inference, check the lease and deadline against live DB time after acquiring the lock. A granted request can still reach the provider after a transition. The current lock and mode checks fence its result. Commit validated state, `reply:<inbound_id>`, and the terminal outcome together. After an uncertain commit, reread the turn. Fresh completions advance the enrollment watermark. Stale and invalid-clock turns leave it alone.

Messaging recognizes Agent replies by the durable turn linked to the inbound parent. Before each POST, check current approval of the exact route and joined User, delivery attempt ownership, and the existing idempotency window. Retain the outbound UUID and key on authorized retries.

A route revocation takes effect after workers with the old routing config exit. Suppress affected Agent replies through the existing delivery owner before reapproval, including recovered claims and future-due or paused work. Preserve account-wide backoff and late provider evidence. Use a durable no-POST state that bypasses retry normalization.

| Send evidence | Revoked reply handling |
| --- | --- |
| No possible submission | Close in the existing `failed` state and record the reason. |
| Possible submission with no provider ID | Keep `uncertain` with zero further POSTs. The blocked route stays out of the pilot while unresolved. |
| Exact provider ID | Retain the outcome and reconcile unresolved status read-only by exact ID. |

### 5. Enroll, disable, and recover

Default to Fixed. Use a private operator command with the existing backend configuration to enforce `Fixed → Agent → Fallback → Fixed` under the User lock. Fallback fences in-flight model results, resolves turns locally, and reports memory controls as unavailable. Enter Fixed only after a locked terminal-backlog check.

Enable after the fixed-only webhook and history workers have exited. Re-enroll with a fresh brief, recent context, and watermark. Keep the same User’s approved preferences, pause state, and privacy cutoffs. Retire and suppress the old route before enrolling a replacement conversation.

Join and Messaging must start with Agent credentials absent. If Agent stalls, recover on a guard-compatible build and drain. An old sender can run only after intake is Fixed, stale attempts have stopped, and all Agent-linked outbound rows are in legacy no-POST states.

## Constraints and Guidelines

- Keep the backend in `tbd-knows/server`. Follow [repository rules](../../AGENTS.md) and preserve the existing Join and Messaging regressions.
- Derive identity in the backend. Use the minimum own-profile projection. Read it within the owning transaction or after release to avoid a nested User lock.
- Pin model revision where available, prompt, schema, and limits per turn. Use stateless calls with response storage disabled and verify endpoint data controls. Drain to Fixed for incompatible config changes. Future TBD-selected models, including hosted open-weight candidates, use the same adapter and eval cases.
- Use metadata-only logs and synthetic fixtures. Keep credentials, raw chat, real identities, and saved values out of logs and public evidence.

## Additional Information

### Build sequence

1. **Backend.** Add storage and transactional intake, then the ordered runner and delivery guard. Add context, controls, and the harness. Exercise the complete path with disposable Postgres, controlled providers, and repeatable pilot-model evals. Run `uv run --locked python scripts/run_database_tests.py -q` and `uv run --locked ruff check .`.
2. **Hosted pilot.** Follow the repository’s review and approval rules. Deploy the compatible build in Fixed and verify existing health. Verify old-worker exit before enrolling two approved threads. Record warm response, cold first ingress, recovery of already-ACKed work, and Fallback and rollback evidence. On Render Free, a later inbound request wakes the app and lets pending work resume.

### Telemetry and evals

Correlate inbound, attempt, config, terminal outcome, and outbound IDs. Track Agent progress independently of Messaging health. Measure queue, context, inference, commit, and delivery time, including cold wake. The shipped Messaging cold-wake baseline was 34.27 seconds from source to acceptance and observed send. Track known usage across retries and failed turns, and flag possibly billed attempts with missing usage. Report API spend per useful reply.

Replay the same versioned multi-turn cases through every candidate model. Hard-fail privacy, authority, and durable state errors. Grade grounded comparisons, a recommendation when facts suffice, a material question when needed, honest no-fit, and preference reuse. Count fallback as a miss on nominal turns. Tune context budgets, skew, deadlines, brief expiry, and numeric acceptance thresholds with measured runs before declaring the hosted pilot a pass.

## End Goal

Two enrolled Users can text a useful personal commerce assistant, carry a brief across turns, and reuse or change a standing preference. TBD can trace each eligible inbound to one linked reply, measure its quality and cost, and safely disable Agent.

| Check | Required evidence |
| --- | --- |
| Identity and replay | A second approved route stays Fixed. Duplicate inbound and lock-wait races preserve one outcome. Unjoined and seed-only identities create no Agent work. |
| Context and controls | Show a resumed brief, gift exception, saved preference reuse, no-match forget after deadline, and isolation across Users. Assert provider payloads for future turns, unseen questions, late input, and blocked credentials. |
| Reliability | Cover hung inference, stale grant, uncertain commit, revocation during shared backoff, and guarded rollback. Join, webhook intake, delivery, and contact card regressions pass. |
| Hosted | Confirm same-thread handset receipt on both routes. Track provider acceptance and observed send separately. For already-ACKed work recovered after its inference deadline, an ordinary turn settles once with fallback and an eligible control still applies once. |

## Non-Goals

Cross-Entity orchestration, proactive work, catalog search, checkout, payment execution, and fulfillment. Additional channels, client UI, per-Entity compute or keys, arbitrary tool execution, and separate worker deployments or a queue broker.

## References

- [Agent Service project](https://linear.app/apt-knows/project/agent-service-e8946bced971/overview) · [TBD v1 Product Experience](https://app.notion.com/p/3fa726cebd9080d2a096deed0fc4d440) · [Tech Hub](https://app.notion.com/p/3cb726cebd9080be90cdd38c5f2125ad)
- [User Service design](../user-service/design.md) · [Messaging design](../messaging-service/design.md) · [Hosted Messaging evidence](../messaging-service/hosted-pilot-evidence.md#quietwake--passed)
- [Postgres transaction isolation](https://www.postgresql.org/docs/current/transaction-iso.html) · [Render deploy sequence](https://render.com/docs/deploys#zero-downtime-deploys) · [Render Free wake](https://render.com/docs/free#spinning-down-on-idle)
- [OpenAI SDK retries](https://github.com/openai/openai-python#retries) · [OpenAI data controls](https://developers.openai.com/api/docs/guides/your-data)
