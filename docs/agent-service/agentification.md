# TBD / Agent Service v1 Part 2 — Agentification

Design review draft. [Editable Excalidraw](agentification.excalidraw) · [Linear review](https://linear.app/apt-knows/document/agent-service-v1-part-2-agentification-18ac4cd986e2)

## TLDR

On an enrolled iMessage thread, TBD helps an Entity shape a buying goal, compare supplied options, and make a grounded decision. It picks up eligible work on a return visit and reuses details the Entity clearly authorizes it to keep.

Use the existing FastAPI app on Render and private Supabase state. TBD owns the behavior, context, and authority. A replaceable model adapter supplies inference.

## Background Information / Context

Part 1 provides verified intake, ordered Agent turns, and linked same-thread delivery. Part 1's admission, freshness, dispatch, commit, and delivery checks apply with the Part 2 behavior below. The broader TBD product vision includes sourcing, purchase, and fulfillment. This slice ships the assistant's judgment and continuity for supplied options. [TBD v1 product experience](https://app.notion.com/p/3fa726cebd9080d2a096deed0fc4d440)

| Service | Owns |
| --- | --- |
| User | Completed Join, the canonical User ID, profile, and addresses. A joined User with an approved direct route can be enrolled. |
| Messaging | Signed input and history, route verification, inbound deduplication, reply queue, and delivery evidence. |
| Agent | TBD's behavior, ordered turns, context, one shopping brief, and Entity-authorized memory. |
| Transactions and Orchestration | Verified purchase and payment state and cross-Entity work when those capabilities are available. The Part 2 turn handles unsupported requests locally. |

## Design and Architecture

### 1. The experience

One versioned TBD policy supplies identity, soul, and operating limits. Serve the Entity's interests. Recommend a choice when facts support it, explain material tradeoffs and uncertainty, and say when waiting or choosing none makes sense. Ask one focused question when the answer would change the decision. Speak plainly and briefly in iMessage.

A greeting on its own gets a reply only. A clear shopping request can start work even when it begins with a greeting. Account setup comes from Join and enrollment. Use an app-owned reply for a direct identity or capability question on an eligible turn, including during an inference outage. Ask for comparable details if a bare link lacks them. Messaging handles unsupported inputs.

A clear request to source live listings, contact a seller, buy, pay, or check verified status gets an app-owned capability reply and the next supported step, including when the request also asks for a comparison. Ground material option details in supplied evidence. Reject model proposals that claim unsupported acts or verification. The app reports confirmed state from its own records. A budget or choice guides the brief. Transactions owns future execution and its authorization.

### 2. Context and the active brief

| Context | Contents |
| --- | --- |
| Short term | Current text and eligible earlier turns for the same User, route, and enrollment, with speaker and source preserved. |
| Medium term | One brief for the item and recipient, with User-established constraints and supplied options. Keep source IDs for material values. |
| Long term | Authorized standing preferences. Use the existing keys for size, fit, condition, brand, and material. |

Use current explicit constraints first, then a matching brief, then compatible memory. Apply scope, source, freshness, and privacy cutoffs across the whole prompt. Exclude memory-control and account-profile lookup turns and their replies, including inspections while paused. Keep the Part 1 eligibility checks for User text and observed replies. Trim optional context before asking for a narrower request.

A side conversation or recommendation leaves the brief available for an eligible return. Expire it from its last accepted edit. On a fresh, eligible Agent turn, an explicit close settles locally. A clear switch retires the old brief and question at terminal commit, including a reply-only result or inference fallback. An eligible typed answer can establish the new recipient. A replacement brief requires current evidence. Clarify ambiguous scope. Use same-scope task history from the eligible brief's opening turn onward and omit task history whose scope cannot be established. A vague reference without an eligible brief needs fresh details.

A short answer binds to one typed field from the immediately preceding Agent turn, checked before filtering. Require its exact linked question, observed sent before the inbound under the existing skew check, and a matching brief and scope. A bare yes confirms an exact proposed value for that field. An open question requires the value. Without a writable brief, ask for the item, recipient, and missing detail together.

### 3. Memory controls

A durable write requires a clear first-party request from the enrolled Entity for a supported field, item, recipient, and value where applicable. Clarify quoted, retracted, conflicting, or ambiguous controls as needed with zero memory, brief, or cutoff effects.

Use the same canonical scope for selection, inspection, and controls. Resolve known equivalents such as "me" and "myself." Treat agreeing legacy aliases as one value. Withhold conflicting aliases from reuse until the Entity clearly replaces or forgets them. A scoped replacement or forget covers all known equivalents in the same commit.

| Request | Effect |
| --- | --- |
| Change a constraint for this purchase | Update the matching brief using existing evidence rules. Exclude conflicting older task details. |
| Save a standing preference | Record the first approved value for future work. Confirm a save with a reply only when the scope resolves to one unambiguous value equal to the request. The current brief continues after a first or same-value save. |
| Change a standing preference | Replace the scoped value and apply the context reset when a save changes an existing value or resolves conflicting aliases. |
| Repair stale recall | Apply the reset on an explicit, scoped repair even when the saved value already matches or the obsolete value appeared only in chat. Write a new value with clear authorization for future use. A disputed saved value requires an explicit replacement or forget. |

The reset clears the brief and advances both cutoffs to the accepted inbound's source time and queue sequence. Prior text and its linked reply must follow both cutoffs to enter context, even if the reply is observed later. A question tied to the cleared brief loses write authority. An inbound at or before the time cutoff needs restatement even with a later queue sequence. Other authorized preferences stay eligible in scope. Commit the change, source, reset, outcome, and linked reply together. Replay reuses the outcome. Messaging owns transcript retention and ordering of already queued replies.

A forget resets context even with no saved match. Pause resets context and suspends standing-memory use and writes. While paused, allow inspection, forget, and reset-only repair. A repair requiring a durable write asks the Entity to resume before any change. Resume restores eligible standing preferences and keeps the cutoffs.

Render explicit memory inspections locally from committed state. Only an explicit question about the name on the TBD account or profile uses the resolved User's display name in an app reply attributed to User Service. Ordinary name recall uses eligible chat. Attribute other recall to eligible chat or the brief. User Service owns profile edits. Forgetting a name clears Agent recall of it.

Eligible controls settle locally in Agent mode, including with inference unavailable or past its deadline. Operator Fallback reports controls unavailable. Confirm the committed effect and ask for what is needed to resume a comparison interrupted by a reset. If a reset request also includes shopping, settle the control first and ask the Entity to resend the shopping request.

### 4. From message to reply

1. **Admit.** Messaging verifies the sender and exact direct route. For an eligible enrollment, commit one pending Agent turn with its inbound before ACK. Replay keeps the original producer and outcome. Other Messaging-approved routes use Fixed.
2. **Take the ordered head.** Recheck User, enrollment, route, mode, and freshness using the Part 1 watermark. Apply the sensitive-input gate before local controls or inference. Close revoked turns without a new reply. A clear authorized local control or brief close takes precedence over a capability-only reply. If the turn also asks for an unsupported action, report the committed change and the capability limit in one linked reply.
3. **Request inference.** Pin behavior, context policy, response contract, adapter, model revision, settings, and limits per turn. The adapter takes TBD instructions, authorized context as data, and the structured output contract. It returns a proposal or failure, with known usage. Call once per committed dispatch grant within the remaining budget, with database locks released and SDK retries disabled.
4. **Commit and deliver.** Validate reply, proposed edits, and any typed question in the app. A brief change needs current User evidence or a bound typed answer. Use the existing ownership, lease, deadline, and config checks at grant and commit. Commit outcome, permitted state, and one linked reply together. Invalid or unavailable inference on an eligible turn yields an app fallback and zero model edits. Resolve an uncertain commit from the stored outcome. Messaging owns ordered sending, the route check before each POST, the same outbound key on authorized retry, and reconciliation.

## Constraints and Guidelines

- Use the existing entry points, private state, and runner. Resolve identity and authority in the backend and read own profile without a nested User lock. Keep identified secrets out of inference, edits, and later context.
- TBD alone selects the model and pays inference costs. Use stateless provider requests with response storage disabled and verify the chosen provider project's data controls before real traffic. A controlled substitute can prove the adapter contract. [OpenAI data controls](https://developers.openai.com/api/docs/guides/your-data)
- Keep logs to metadata and use synthetic fixtures and redacted evidence. Tune budgets, brief expiry, deadlines, and performance targets from measured runs.

### Cutover and rollback

For incompatible changes, drain through Fallback to Fixed and deploy in Fixed. Verify Join and Messaging health and wait for old Agent, webhook, history, and delivery workers to exit before re-enrolling. Keep the current private tables. Re-enrollment starts fresh brief and recent context, retaining eligible same-User preferences, pause state, and cutoffs.

Use the exact-route delivery checks for every re-enrollment, including the same route. Keep revoked routes excluded through the Fixed drain and delivery settlement or suppression. An unresolved possible submission without a provider ID blocks restoration. A rollback can serve Agent only if it can read stored state and enforce Part 2 privacy and authority. Before restoring a legacy sender, verify Fixed, stopped old workers and in-flight calls, and zero replies the legacy sender could POST. [Backend runbook](https://github.com/tbd-knows/server/blob/6039d68c3cb290968d633783b32f1eb34eea40cc/docs/agent-service/backend.md#cutover-and-rollback)

## Additional Information

### Evaluation and pilot

Freeze a small versioned set of journeys, held-out phrasings, expected outcomes, repeat counts, and a founder-calibrated rubric. Record the reference revision, starting with shipped Part 1 at `6039d68`, and use the same approved model and settings for comparison. Agree usefulness, fallback, latency, and spend targets from that baseline before tuning and candidate acceptance. Keep the held-out set for candidate verification.

| Check | Evidence |
| --- | --- |
| Everyday use | Exercise a natural intro, a greeting with a shopping request, grounded comparison or no-fit, and a material question with an observed reply before the next inbound. Score meaning, sourcing, and judgment per journey. Count unexpected fallback as a miss on ordinary turns. Report designed local and failure outcomes separately. |
| Context and authority | In disposable Postgres with controlled inference, assert provider input, committed state, and linked replies. Cover switch with reply-only or fallback, close, expiry, unseen questions, paused inspection, name recall after forget and after explicit profile lookup, unsupported asks and claims, sensitive input, isolation, replay, and revocation. Require zero privacy, authority, or state failures. |
| Standing-memory corrections | Seed the obsolete value in memory, the brief, chat, and a sent reply. Verify the local commit, both cutoffs, and later model input. Cover equivalent scopes and a save matching only one of two conflicting aliases. Exercise first and same-value saves, task-only edits, explicit repair, retracted and mixed requests, late arrival and replay. Check unrelated preferences in scope. |
| Quality, latency, and spend | Record revisions, model, sample counts and results by journey, useful-reply and fallback rates, and queue, context, inference, commit, and delivery timings. Reconcile usage for every potentially billed dispatch before passing the complete cost target. Report known spend separately when usage is missing. |
| Hosted use | After rollout approval, both founders use approved threads naturally and return later. Record deployed revision and config, warm turns, cold first ingress, and recovery of already-acknowledged work. Record acceptance and observed send separately from handset receipt on both routes. After an inference deadline, recovered work settles once with an ordinary fallback or eligible local control. Note any extra turn to resume after a standing correction. Reuse recorded Part 1 evidence or complete outstanding TBD-24 checks in the same authorized pilot. Verify candidate-specific recovery and rollback. [TBD-24](https://linear.app/apt-knows/issue/TBD-24) |

Record the accepted Markdown and canvas at one repo revision in the design review issue. Use that revision for the build and evaluation. [Design review](https://linear.app/apt-knows/issue/TBD-26) · [Harness build](https://linear.app/apt-knows/issue/TBD-27) · [Evals and pilot](https://linear.app/apt-knows/issue/TBD-28)

## End Goal

Both founders can meet TBD naturally, carry a shopping decision across turns, return to relevant work, and see an authorized preference reused, corrected, and forgotten. The candidate meets the agreed quality and performance bar with the deterministic checks passing.

## Non-Goals

Live catalog search, seller contact, checkout, payment or status execution, fulfillment, cross-Entity orchestration, proactive outreach, multiple concurrent briefs, new channels, separate workers, vector storage, and a second live provider for launch.

## References

- [Agent Service v1 design](https://github.com/tbd-knows/server/blob/6039d68c3cb290968d633783b32f1eb34eea40cc/docs/agent-service/design.md) · [Backend runbook](https://github.com/tbd-knows/server/blob/6039d68c3cb290968d633783b32f1eb34eea40cc/docs/agent-service/backend.md)
- [User Service](https://github.com/tbd-knows/server/blob/6039d68c3cb290968d633783b32f1eb34eea40cc/docs/user-service/design.md) · [Messaging Service](https://github.com/tbd-knows/server/blob/6039d68c3cb290968d633783b32f1eb34eea40cc/docs/messaging-service/design.md)
- [TBD Product Experience](https://app.notion.com/p/3fa726cebd9080d2a096deed0fc4d440) · [TBD principles](https://app.notion.com/p/3e3726cebd9081dfb051eeb563b53b10) · [Agentification](https://linear.app/apt-knows/issue/TBD-25)
