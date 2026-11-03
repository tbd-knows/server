# User service v1 — integration handoff

Updated 2026-11-03. **The approved User service v1 is implemented, merged, deployed and verified.** [Implementation status](implementation-status.md) is the canonical evidence record; [setup.md](setup.md) records hosting and operations. The previous review/rollback handoff is historical in Git.

## Integration contract

- A founder can invite an approved individual/small business, resend using the original Auth identity, or seed a business without inventing a contact channel. User creation from an invitation requires explicit Join.
- The future Messaging backend resolves a trusted sender with `POST /internal/users/resolve`, then supplies the resulting User ID in `X-TBD-User-ID` outside LLM-controlled arguments. Profile/address paths must match that context and use the internal credential.
- Name correction is the supported profile patch. Identity, type and contacts remain protected. Address saves require reusable-address intent; gift/task-only destinations remain task data. Use current revisions and reread on 409 or uncertain mutations.
- Address save retries preserve the original ID/revision. Edits can clear optional fields. Default changes and replacement deletion are atomic; foreign addresses are not exposed.

Messaging transport, Agent runtime/memory, orders/payments, memberships, mobile and a general dashboard/login are subsequent work. Do not introduce placeholder services or a generic role/token framework for this handoff.

## Release and evidence

Both implementation PR #3s are merged; deployed server includes R1's transactional binding of an unbound approved invitation. A fresh hosted individual flow and 54 API/state checks passed on 2026-11-03. Earlier business Join/recovery, 71 backend tests and 38 browser tests remain evidence at their stated layers. Do not relabel earlier controlled race/failure cases as new hosted injections.

## Operational boundaries

Keep Render manual deployment, the applied migration, joined accounts, exact origins and backend-only secrets. A cold first Join may need Retry because idle-start latency can exceed the client's 15-second timeout; reload/expiry uses founder resend. Default email subjects remain a nonblocking difference.

Use [AGENTS.md](../../AGENTS.md), the [approved design](design.md), and current live deployment metadata before subsequent changes. Do not reset accounts, reapply the migration, send mail to unapproved recipients, or publish real contacts, invitation links, credentials or private browser artifacts.
