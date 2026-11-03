# User service v1 — implementation and verification status

Updated 2026-11-03. **Implemented for the approved v1 scope, merged, deployed, and verified.** The founders reported merging [server PR #3](https://github.com/tbd-knows/server/pull/3) and [client PR #3](https://github.com/tbd-knows/client/pull/3); GitHub confirms both merges. The post-merge production sweep passed. This is completion of the User service contract in [design.md](design.md), with the evidence boundaries below.

## Verified application release

| Component | Production URL / resource | Verified application commit | Deployment at verification |
| --- | --- | --- | --- |
| Python API / Render | <https://tbd-user-api.onrender.com> | `9d69ab1776af65147fe5237b4f1abb5620a2113d` | `dep-dbjrrsvlk1mc73a4jgog`, live |
| Join page / Vercel | <https://client-pi-five-28.vercel.app/join> | `f04707d8b46c201db82bbb724511c02c0c9734c8` | `dpl_2D14St9M8zMVY4Myg4FxThTh6CGU`, READY / production |
| Supabase | `tbd-v1` / `xclftpnjwrmakooaaxik` | Release migration `20261101103939` | Existing migration preserved |

These were both current main heads and serving deployments during verification. Documentation-only closeout commits may follow these application commits. Render tracks main with auto-deploy off. No application source, deployment settings, Auth configuration, or migration was changed by the production sweep.

## Implemented v1 scope

- Founder CLI and provisioning-only API for approved invitations, resend/recovery, and idempotent business seeding without a required contact channel.
- Explicit-click email verification and transactional/idempotent User creation from approved server-owned details. Passive link opening does not join. Uncertain delivery reconciles the original Auth identity; joined invitations do not send another onboarding email.
- R1 is included in the deployed server: Join binds a confirmed identity to an unbound matching invitation inside the existing transaction and row lock. Missing approval, mismatched bindings, uniqueness and rollback protections remain.
- Trusted sender resolution and own-user profile/address APIs; separate provisioning/internal credentials, protected account fields, revisions, retry-safe saves, clearing, and atomic default/replacement rules.
- Private application schema with three tables, constraints/RLS, restricted backend access over TLS, and no browser schema/table grants.
- One Python/FastAPI/uv API on Render and one plain TypeScript/Vite Join page on Vercel, with pinned dependencies, OpenAPI and operational runbooks.

Messaging, Agent, waitlist synchronization, orders/payments, memberships, mobile, a general login/dashboard and a profile UI are outside this v1 service. The tested sender/profile contract is ready for subsequent Messaging/Agent integration.

## Production sweep — 2026-11-03

**Freshly executed: 41 hosted API request cases plus 13 response/state assertions, all 54 passed**, alongside actual email/Auth/browser, read-only hosted SQL, configuration and deployment checks. No failed application assertion was observed.

- One explicitly authorized fresh individual invitation returned 200/sent. The sender copy was inspected and the recipient confirmed inbox receipt. Browser verification used the actual link from the sender copy.
- The real email link reached the canonical Join page, scrubbed its fragment and displayed the button. Before clicking, SQL showed zero application Users for that approval, unconfirmed Auth and an unjoined invitation.
- A single explicit Join displayed “You're in TBD.” Hosted SQL proved exactly one User matching the approved name, normalized phone, email, type, invitation and original Auth identity. The new profile remained revision 0.
- The consumed link showed expired/used guidance on another click. Joined individual/business resend calls returned `already joined`; no extra message to the fresh recipient appeared.
- The live internal API resolved the formatted sender phone to that User. Existing joined business/profile records were read and preserved.
- Live seed retry/conflict, input validation, credential separation, own-user authorization, protected profile fields and stale revisions passed.
- Address save/retry/edit/clear, default switching, replacement/last deletion and foreign ownership cases passed. Mid-run SQL showed exactly one default per address-owning fixture; all synthetic addresses were cleared through the API.
- Final SQL showed one additional User/Auth identity, all previous accounts preserved, no leftover conflict-probe User or saved test addresses, and the same single applied migration. No account reset or approval deletion was used.

Hosted health/OpenAPI/client requests returned 200. Exact production-origin CORS passed; an untrusted origin was rejected. Public signup and unused/anonymous providers are disabled. All application tables rejected browser Data API access (406/PGRST106); grants/RLS and backend Auth-table denial were independently checked. Startup logs verified the restricted database role, TLS and session pooling. The served client contains correct public API/Auth configuration and disables persistent/automatic sessions. No actual server secret was found in its bundle. No error-level Render application logs were returned in the verification window.

Personal contacts, live invitation hashes, account IDs, raw MIME, credentials and private screenshots are deliberately excluded from this public evidence.

## Recorded controlled and earlier live verification

The prior run after R1 recorded **71 backend tests, zero skips**, plus Ruff/format/whitespace checks; existing **38 Chromium/WebKit tests, zero skips** cover the unchanged client behavior. Those suites were not rerun or relabeled as fresh execution during the deployment sweep. The backend suite applies the actual release migration to disposable PostgreSQL and operates as `tbd_user_backend`; SDK tests execute the pinned Supabase client with controlled HTTP transport.

Reproduction commands remain in the [server README](../../README.md) and [client runbook](https://github.com/tbd-knows/client/blob/main/docs/user-service.md). The [immutable pre-closeout record](https://github.com/tbd-knows/server/blob/9d69ab1776af65147fe5237b4f1abb5620a2113d/docs/user-service/implementation-status.md) preserves the detailed commands, binding/rollback/concurrency tests and earlier live business and recovery checks. Its review-pending and old-deployment statements describe that historical checkpoint, not current status.

## Reconciled acceptance matrix

| Approved scenario | Evidence retained | Fresh production sweep |
| --- | --- | --- |
| Individual and small-business invitation/Join | Earlier real inbox, Auth, browser, API and SQL proof of both types | Fresh individual flow passed; existing joined business/profile/resend verified. No second recipient invented. |
| Passive link opening | Earlier browser/network/SQL proof | Actual new link opened; no User, confirmation or joined invitation before the button press. |
| Malformed, expired or used links | Controlled browser malformed/expiry cases and earlier real consumed-link rejection | Invalid-token and consumed actual-link guidance passed; no duplicate User. Natural expiry was not forced. |
| Confirmed session without approval / mismatched identity | Pinned SDK + FastAPI + real database denial and zero User insert | No new unapproved live identity created. |
| Client-supplied account-field tampering | HTTP + real database proof that stored approved details win | Fresh User matched server-approved fields; protected profile-field attempts returned 422. Live Join tampering was not reinjected. |
| Duplicate/concurrent Join and uncertain response | Browser guard, real database concurrency/rollback/idempotency tests; earlier live same-session 503/Retry | One fresh Join and exactly one stored User. Live double-click/race/lost-response injection was not repeated. |
| Auth success followed by API failure / reload recovery | Earlier live individual same-session Retry and business reload/resend/magic-link completion | Existing evidence retained; no forced live outage or account reset. |
| Failed or uncertain email send | Controlled SDK timeout/reconciliation + real database proof; prior live successful sends | Fresh successful inbox receipt confirmed. No deliberate SMTP outage. |
| Contact conflicts and idempotent business seed | Real database constraints/races and earlier hosted checks | Repeated seed returned the original ID/revision; changed details and invitation contact conflicts returned 409. |
| Credentials, context and foreign resources | Controlled and earlier hosted access checks | Invalid/missing credentials and wrong scope denied; cross-user profile/address/default/replacement attempts rejected. |
| Concurrent/stale revisions | Real database one-winner concurrency test | Stale profile/address writes returned 409; correct writes incremented revisions once. |
| Multiple addresses, clearing, defaults and deletion | Real database transaction/rollback tests | Save retries had no duplicate/revision bump; edits/clearing/default/replacement/last deletion passed, with SQL confirmation and cleanup. |
| Address changes do not rewrite orders | Structural source evidence: no order table/client/write path in this service | No fabricated order flow added. |
| Browser application-table access | Restricted role, RLS/grants, direct SQL and hosted Data API denial | All three tables denied; anon/authenticated had no schema/table access. |

## Operational notes and evidence limits

- Observed idle-start responses took about 22.8–32.4 seconds; the client timeout is 15 seconds. A cold first Join may therefore need Retry (an inference from latency and served code). The verified in-memory retry/resend behavior remains; a fresh valid-session cold-start/retry sequence was not forced. No paid hosting change was made.
- Default email subjects remain `You've been invited` / `Your sign-in link`. Actual bodies, canonical fragment links and delivery work; this is the documented nonblocking subject difference.
- Supabase's security advisor reported leaked-password protection disabled. No application-table exposure finding was returned; no Auth setting was changed during this invitation/OTP-focused sweep.
- Automatic approval review blocked deliberate production double-clicking. The successful live Join used one click; duplicate/race cases retain controlled evidence. Unapproved live identities, natural fresh-token expiry, binding races, lost commit responses and SMTP/API outage injection were not newly exercised in production.
- Historical rollback/review and connector-routing blockers are superseded: PR #3s are merged, R1 is deployed, both hosts serve the verified application, and fresh scoped SQL reads succeeded. Preserve existing accounts, secrets, migration and access restrictions; routine documentation updates do not authorize destructive tests.

## Completion

TBD-14 backend, TBD-15 Join page and TBD-16 deployed proof satisfy the approved User service v1 scope; TBD-13 is the completed parent. Record completion with the mixed live/controlled evidence above, rather than claiming every injected failure was reproduced on production or that the subsequent Messaging/Agent product is implemented.
