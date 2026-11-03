# TBD v1 — User service

## TLDR

Build one Python API on Render and one TypeScript join page on Vercel. Supabase provides Auth and Postgres; Gmail SMTP is the initial Auth email delivery target, using the founder's existing address.

A founder submits an approved waitlist entry. TBD emails a Join TBD link. Opening it shows one button; pressing Join verifies the email and creates the User from the stored entry. Individuals and small businesses use the same flow. Founders can seed other businesses directly.

The service also owns saved profile details and addresses. Messaging and Agent are subsequent implementations; this service provides their tested integration contract.

Status: **implemented for the approved v1 scope, merged, deployed and verified on 2026-11-03**. See [implementation and acceptance evidence](implementation-status.md), [the drawing](architecture.excalidraw), and the [production runbook](setup.md). Messaging and Agent remain subsequent implementations.

## Background Information / Context

TBD v1 coordinates buying-led physical-goods transactions. A User represents an individual or business. Entity means the eventual bundle of User, Messaging, and Agent services. A User record alone does not create an agent or prove business participation.

Confirmed decisions:

- Waitlist fields: name, phone, email, and individual/business type. Admission is controlled by TBD.
- Medium and large businesses are seeded by founders; no business-size field is needed.
- Control of the admitted phone is sufficient for later iMessage access. One phone maps to one account.
- Collect additional details only when needed. Clear conversational intent permits saving reusable information.
- Support multiple saved addresses and one default delivery address. Each purchase owns its selected destination.
- Use a fresh Supabase project and an explicit Join button. This email join flow supersedes creating the account before a welcome text.

## Design and Architecture

### 1. Components and ownership

| Component | Responsibility |
| --- | --- |
| Python / FastAPI API | Invitations, joining, seeding, account lookup, profile/address operations, access checks |
| TypeScript / Vite page | One join interaction and success/error states; no general login or profile application |
| Supabase Auth | Email invitation tokens, verification, short-lived authentication for joining |
| Supabase Postgres | Approved entries, joined/seeded Users, saved addresses |
| Gmail SMTP | Deliver Supabase Auth emails using the founder's Gmail address and Google App Password |

Use Supabase's Python SDK for Auth and Psycopg for transactional SQL. No ORM, queue, separate auth service, or file-storage bucket. Keep Auth administrative clients separate from clients that validate user sessions; never mutate a shared privileged client's session.

### 2. Invite and join

1. Founder runs `invite` with a JSON file containing `name`, `phone`, `email`, and `type`. The command calls the API using a provisioning credential. There is no waitlist integration or admin UI in this phase.
2. Validate and store the approved entry before sending email. Repeating the same entry reuses it; different details for the same entry return a conflict rather than silently overwrite it.
3. Call `supabase.auth.admin.invite_user_by_email` and retain its Auth ID on the entry. Supabase may create an Auth identity now, but no application User exists yet. If its response is lost, reconcile the approved email through the Auth admin API before retrying; never manufacture an Auth ID.
4. Configure the Invite email template to link to `JOIN_SITE_URL/join#token_hash={{ .TokenHash }}&type=invite`. The Magic Link template uses the same page with `type=email` for recovery/resend after email verification.
5. GET `/join` and page initialization must neither verify the token nor create a User. The page reads the fragment into memory, removes it from the visible URL, and displays Join TBD. Do not fetch private account details before verification.
6. On the button press, call Supabase `verifyOtp` with the hash and allowlisted type (`invite` or `email`). Keep the returned session in memory only: disable automatic URL detection, persistent session storage, and automatic refresh.
7. Send the access token to `POST /v1/join`. The API validates it with Supabase Auth `get_user`; it requires a confirmed email and matches it to the server-owned approved entry. Ignore client-supplied name, phone, type, email, or user ID. If delivery preceded storing the Auth ID, bind the confirmed identity to the matching unbound approved invitation within the Join transaction and row lock; reject a different existing binding.
8. In one database transaction, lock the entry, create the User, and mark the entry with its User ID. A retry with the same verified identity returns that same User. Return success only after commit.
9. Show “You're in TBD.” Do not claim that messaging, an agent, or purchasing is available yet. Clear the in-memory session after success; do not create a dashboard.

Disable public signups and other unused Auth providers. Possessing an Auth session alone is insufficient: a matching approved entry is required. Auth metadata is never the source of account type, phone, or admission authority.

Invitation recovery stays manual. The founder's `resend` command reuses the approved entry: resend an invite to an unconfirmed identity; for an already confirmed identity whose application join failed, send a magic link with `should_create_user=False`. Configure both templates. A joined User returns “already joined” without another onboarding email. Never delete/recreate an identity to retry.

If verification succeeds but the API fails, the page keeps the session in memory and offers Retry. If the page is closed, reloaded, or the session expires, the founder can resend. An expired/used link gets a clear error, not a new account. SMTP acceptance is “sent,” not proof of inbox delivery; uncertain sends are reported for manual retry.

### 3. Seed a business

`seed-business` accepts a stable `seed_key`, name, and optional phone/email. It calls a provisioning-only endpoint. Type is always `business`; repeating an identical seed key returns the same User. Changed data or conflicting contacts return a conflict.

No email, Auth identity, login, membership, agent, or business consent is created as a side effect. No contact channel is required. Giving that business interactive access later is separate work.

### 4. Data model

Use three tables in a non-exposed `user_service` schema. Browser `anon`/`authenticated` roles have no table or schema access. The backend's database credential stays on Render. API authorization remains necessary because the backend can access multiple Users.

| Table | Fields and invariants |
| --- | --- |
| `users` | UUID `id`; nullable unique `auth_user_id` referencing Supabase Auth; `name`; `type` constrained to individual/business; nullable unique normalized `phone`; nullable unique normalized `email`; nullable unique `seed_key`; integer `revision` initially 0; `created_at` |
| `invitations` | UUID `id`; unique normalized `email`; `name`; required normalized `phone`; `type`; nullable `auth_user_id`; nullable `joined_user_id` referencing Users; `created_at` |
| `addresses` | UUID `id`; `user_id` FK; optional `label`; `recipient`; `line1`; optional `line2`; `city`; `region`; `postal_code`; `country_code` constrained to US; `is_default` |

Invite-backed Users require name, phone, email, type, and Auth ID. Seeded businesses may omit contacts and Auth ID. Email is unique for invite-backed identity; do not merge individual and business accounts sharing contacts. Normalize email by trimming and case-folding; do not rewrite provider-specific aliases. Normalize US phones to E.164. Email joining does not verify phone ownership; the future Messaging integration establishes the trusted sender.

Do not add a generic profile JSON blob. Addresses are the first additional profile feature. Add other fields when a concrete v1 flow requires them.

### 5. Profile and address behavior

- Read profile returns account details, saved addresses, and revision. The Agent needs name/type as well as additional details.
- Profile patch supports display name only, from an explicit correction. It rejects changes to ID, type, Auth ID, phone, and email. Contact recovery remains a founder-operated process, not an agent action.
- Address operations: list/read, save, edit, remove, and set default. The first saved address becomes default. Further saves preserve the existing default unless explicitly changed.
- Enforce at most one default with a partial unique index. Address mutations lock the owning User row; switching defaults is one transaction.
- Removing the default with other addresses remaining requires a replacement address ID in the same operation. Removing the last address leaves no default.
- Each mutation takes the current `revision`, validates it under the row lock, and increments it once. A stale revision returns 409 for reread/clarification. Do not blindly replay a conflicting user instruction.
- A save accepts a caller-generated address ID. The PUT body uses `operation: "save"` (default) for idempotent creation and `operation: "edit"` for an explicit revision-checked edit. Repeating that ID with the same content returns the existing address; a different payload conflicts. After an uncertain edit/delete, reread before deciding whether another write is needed.
- Saving requires clear intent to retain/reuse. Gift or purchase-only addresses remain task data. Saving is not permission to disclose to a seller or delivery provider.
- Clearing removes the saved value; future Agent work must not silently restore it from conversation history. This service does not delete or rewrite orders or Agent memory.

### 6. API and access contract

| Interface | Caller / behavior |
| --- | --- |
| `POST /internal/invitations` | Provisioning credential; store approved entry and request invitation delivery |
| `POST /internal/invitations/{id}/send` | Provisioning credential; retry delivery using the recovery rules above |
| `POST /internal/users/seed-business` | Provisioning credential; idempotent seed operation |
| `POST /v1/join` | Verified Supabase bearer token; join from approved server-owned data |
| `POST /internal/users/resolve` | Internal credential; trusted sender phone in body resolves an existing User; never creates one |
| `GET /internal/users/{id}/profile` | Internal credential plus backend-resolved User context; own profile |
| `PATCH /internal/users/{id}/profile` | Same context; display-name correction and revision |
| `PUT /internal/users/{id}/addresses/{address_id}` | Same context; create/update validated address and revision |
| `DELETE /internal/users/{id}/addresses/{address_id}` | Same context; revision and replacement default when required |
| `PUT /internal/users/{id}/default-address` | Same context; existing own address ID and revision |
| `GET /healthz` | Fast, side-effect-free process/readiness check for Render |

Use separate provisioning and internal-service bearer secrets. Provisioning may create accounts; ordinary profile calls cannot. Browser Supabase tokens are accepted only at `/v1/join`, not internal routes.

The internal credential is intentionally privileged across accounts. The future Messaging/Agent backend resolves the User and supplies `X-TBD-User-ID` outside the LLM tool arguments; the tool supplies only supported profile/address changes. Check path ID against that context, and accept the header only with the internal credential. The resolve endpoint establishes context and does not require a preexisting User ID. A claimed ID without an authenticated internal caller proves nothing. Do not add a per-agent token issuer or implement placeholder Messaging/Agent services now. Test these calls with a controlled internal harness.

Return 401 for missing/invalid credentials, 403 for an unauthorized operation, 404 for unknown own resources/senders, 409 for identity/revision conflicts, and 422 for invalid fields. Infrastructure failures are retryable errors, never success. An unknown address ID belonging to another User behaves as not found. Document these request/response schemas in FastAPI OpenAPI during implementation.

## Constraints and Guidelines

- Scope: Users, invitations, saved profile data, and addresses. Spending approvals, disclosure approval, payments, order snapshots, and memory remain outside this service.
- Keep the page free of analytics, third-party scripts, token logging, and private data before verification. Use no-referrer/no-store behavior and remove the URL fragment before network calls. Disable email link tracking in the sending provider.
- CORS allowlists the local join origin and deployed production origin. Do not allow every preview origin to access the live project; previews use local/mocked Auth unless explicitly configured.
- Keep Supabase secrets, SQL credentials, and internal tokens out of browser builds and Git. Browser configuration contains only API URL, Supabase URL, and publishable key.
- Use parameterized SQL and a backend-only database role with access to this service's schema. Do not expose application tables through the Supabase Data API or add public RLS policies for convenience. The backend role has schema-scoped grants and RLS policies; browser roles have no policies or grants.
- Use SQL migrations committed under `supabase/migrations/`; create/generate them using the Supabase CLI workflow. Apply them explicitly before deploying compatible application code. No migration-on-request or destructive production reset.
- Pin dependencies and commit lockfiles. Test real database transactions/constraints as well as API behavior. Avoid mock-only integration proof.
- Both repositories are public. Commit only code, fake fixtures, documentation, and secret-free templates. No real waitlist records, addresses, tokens, or email screenshots.

## Additional Information

### Repository and deployment layout

| Repository | Layout / deployment |
| --- | --- |
| `tbd-knows/server` | Root Python project and lockfile; `src/tbd_server/user/`; tests; `supabase/`; `docs/user-service/`; one Render web service |
| `tbd-knows/client` | `apps/web/` contains one Vite TypeScript package and lockfile; client instructions link to the canonical server design; one Vercel project |

Use Python 3.12 with `uv`; use npm and a current supported Node LTS for the web package. Implementation pins exact package versions. No mobile folders, Python workspace, JS monorepo tooling, Docker, or generated shared SDK yet.

Render starts Uvicorn on `0.0.0.0:$PORT`; no persistent disk. Use Supabase's dashboard-supplied session-pooler URL when IPv4 connectivity requires it; do not guess the pooler hostname. Keep the connection pool small. Vercel root is `apps/web`, framework Vite, output `dist`.

Founder commands `invite`, `resend`, and `seed-business` read JSON from a file/stdin, call the API, and report IDs/outcomes without tokens or private payloads. They use environment credentials; no founder web console. Implementation documents the actual commands and deployment settings in the READMEs.

### Build sequence

1. Backend: migrations, API, founder commands, profile/address contract, meaningful tests, and Render configuration.
2. Client: join page, Supabase templates/SMTP setup instructions, and Vercel configuration. Target the backend contract rather than duplicate its logic.
3. Integration: configure Gmail SMTP and Auth settings, deploy both repos, and exercise the matrix below. Record evidence on the parent issue.

Work: [TBD-13 parent](https://linear.app/apt-knows/issue/TBD-13), then [TBD-14 backend](https://linear.app/apt-knows/issue/TBD-14), [TBD-15 join page](https://linear.app/apt-knows/issue/TBD-15), and [TBD-16 deployed proof](https://linear.app/apt-knows/issue/TBD-16). Each child is one substantial deliverable; do not split into endpoint-level tickets. Both planning PRs are merged; implementation follows the approved contract.

### Required verification

| Scenario | Expected result |
| --- | --- |
| Individual and small-business invitations | Both create correct Users only after Join |
| Link opened/prefetched without button press | No token consumption or User creation |
| Expired, malformed, or used token | Clear error; no new User |
| Valid session with no approved entry | Rejected; no User |
| Client changes signup fields | Ignored/rejected; stored approved data wins |
| Double Join / API timeout / concurrent Join | One User; safe retry with verified session |
| Token consumed, then API fails | Session retry works; founder resend recovers after reload |
| Failed/uncertain email send | Approved entry remains; retry does not duplicate accounts |
| Duplicate phone/email and repeated business seed | Defined conflict or same original User, never merge |
| Invalid internal credential / cross-user address | Rejected without exposing another profile |
| Two updates with the same revision | One wins; stale change returns 409 |
| Multiple addresses / default switch / deletion | One default, valid ownership, atomic mutations |
| Saved address changed after purchase | No order changes; this service has no order-writing path |
| Browser tries Supabase application tables | Denied; no browser table grants |

## End Goal

A founder can invite a permitted external email using stored waitlist details. The recipient opens the deployed page, presses Join, and becomes exactly one User. The same flow supports small businesses. A founder can seed a business without inventing a contact channel. Profile and address operations pass their own-user, conflict, and clearing tests. The tested sender-resolution contract is ready for Messaging.

Completion requires deployed end-to-end evidence, not only green unit tests. SMTP credentials and working Render/Vercel deployments are launch prerequisites; current setup gaps are in [setup.md](setup.md). Vercel verification is deferred until needed and does not block planning.

## Non-Goals

Waitlist collection/sync, Messaging transport, Agent runtime/memory, business memberships, buyer/seller roles, public signup, passwords/OAuth/MFA, general web login/dashboard, phone OTP, automated account recovery, order/payment/Stripe integration, disclosure-policy engine, arbitrary profile metadata, file uploads, queues, caches, mobile apps, or platform-wide scaffolding.

## References

- [Approved service drawing](architecture.excalidraw); [Linear User Service project](https://linear.app/apt-knows/project/user-service-89260eb3e4ce/overview).
- [Product scope](https://app.notion.com/p/3fa726cebd9080d2a096deed0fc4d440); [Tech Hub](https://app.notion.com/p/3cb726cebd9080be90cdd38c5f2125ad). This design incorporates the founders' later terminology and email-join decisions.
- [Supabase invitations](https://supabase.com/docs/reference/python/auth-admin-inviteuserbyemail), [email templates and prefetching](https://supabase.com/docs/guides/auth/auth-email-templates), [passwordless recovery](https://supabase.com/docs/guides/auth/auth-email-passwordless), [SMTP](https://supabase.com/docs/guides/auth/auth-smtp), [Google App Passwords](https://support.google.com/accounts/answer/185833), [Gmail SMTP settings](https://support.google.com/mail/answer/7104828), [database connections](https://supabase.com/docs/guides/database/connecting-to-postgres).
- [Render deploys](https://render.com/docs/deploys), [health checks](https://render.com/docs/health-checks), [FastAPI deployment](https://fastapi.tiangolo.com/deployment/manually/).
- Platform documentation checked 2026-10-31, including Supabase's current changelog. Custom SMTP is required for the custom join templates on a new free project.
