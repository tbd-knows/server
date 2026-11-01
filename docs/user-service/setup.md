# User service setup

Updated 2026-11-01. Backend and client reviews passed. [Server PR #2](https://github.com/tbd-knows/server/pull/2) and [client PR #2](https://github.com/tbd-knows/client/pull/2) remain open/unmerged until full service acceptance. TBD-16 is In Progress. [Current evidence](implementation-status.md) distinguishes local tests from deployment proof.

| Component | State / next action |
| --- | --- |
| GitHub | Both implementation reviews passed; use the existing server/client branches for integration, then merge both only after acceptance. |
| Supabase | Use existing `tbd-v1`, reference `xclftpnjwrmakooaaxik`. Reviewed migration applied at version `20261101103939`. Private schema/RLS/grants and browser Data API denial verified. Public signup is still enabled and must be disabled. Founder reports Gmail SMTP saved directly; delivery/template/URL configuration remains unverified. Never change old `aptknows-auth`. |
| Render | Deployment prepared in root `render.yaml`; no service created or deployed. Render plugin access is pending. Inspect workspace `tea-das0s48u01pc73e7km90` before provisioning/reusing one service. |
| Vercel | Existing imported `client` project in `robel-kebedes-projects`. Connected account sees the supplied team but `client` returns 404 and is absent from its project list. Founder confirmed the existing Dashboard URL and production domain; connector access still returns 404. A Vercel team-consent link was requested; no replacement project created. |
| Email | Founder reports Gmail SMTP saved directly; actual delivery remains unverified. Never enter the App Password into chat, Git, or the browser application. |

## Configuration

| Location | Values |
| --- | --- |
| Render only | `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `PROVISIONING_TOKEN`, `INTERNAL_SERVICE_TOKEN`, `JOIN_SITE_URL`, `ALLOWED_WEB_ORIGINS` |
| Browser/Vercel, later | `VITE_API_URL`, `VITE_SUPABASE_URL`, `VITE_SUPABASE_PUBLISHABLE_KEY` (all public) |
| Founder CLI | `TBD_API_URL`, `PROVISIONING_TOKEN`; JSON records outside Git |
| Supabase Dashboard | SMTP credentials, disabled public signups/unused providers, production Site URL and exact redirect allowlist, Invite and Magic Link templates |

`ALLOWED_WEB_ORIGINS` is a comma-separated exact allowlist, initially `http://localhost:5173` plus the production join origin when known. Do not allow every preview deployment. Generate independent random bearer secrets into secret stores. No real secret values belong in `.env.example`.

## Migration workflow

The release migration is `supabase/migrations/20261101103939_user_service.sql`. Its SQL was created with Supabase CLI 2.121.0 using `supabase migration new user_service` after reading current help, initially under timestamp `20261101045729`. On 2026-11-01 the reviewed SQL was explicitly applied through the connected Supabase migration tool, which recorded version `20261101103939`. The local filename was aligned to that recorded version without changing the SQL. Do not reapply it. Disposable tests apply this exact file. It creates only the three application tables, private-schema grants/RLS policies, and a backend database login with no password. Browser roles have neither schema nor table access. Only `tbd_user_backend` has application DML policies; it cannot read Auth tables or administer schema/roles.

The migration is now applied. Before any later migration, inspect the live project/schema/migration history. The commands below document the workflow for future reviewed changes; the current migration must show as already applied. Authenticate the CLI locally; keep database credentials in its secure prompt/secret environment, not command history. Check current CLI help before execution:

```bash
supabase link --help
supabase migration list --help
supabase db push --help
supabase db advisors --help
supabase link --project-ref xclftpnjwrmakooaaxik
supabase migration list --linked
supabase db push --linked --dry-run
# After reviewing the target and pending migration:
supabase db push --linked
supabase db advisors --linked --type all --fail-on error
```

Do not reset the live database. Do not apply the migration on API startup or requests. Do not edit an applied migration to repair production; add a new reviewed migration instead.

As the database owner, set the backend role's password out of band. In an interactive `psql` session using a secure owner connection, `\password tbd_user_backend` prompts without putting it in SQL history. Use Supabase's supplied direct or **session-pooler** host/port and custom-role username format, never a guessed host. For session pooling, the username is `tbd_user_backend.<project-ref>`; use the role's password, percent-encoded in a connection URI. Set TLS (`sslmode=require` at minimum) and keep the resulting `DATABASE_URL` only in Render's secret environment. Confirm the deployed connection's `current_user` is `tbd_user_backend` before acceptance. The API does not need direct Auth schema privileges; identity validation uses the Auth SDK.

## Render preparation

`render.yaml` specifies Python 3.12.12, free web compute, repository root, manual deployments, no disk, and `/healthz`. It is preparation, not proof of service creation or a hosted build. Inspect the supplied workspace and existing services first. Do not provision a paid plan to work around failures.

Build command:

```bash
pip install uv==0.13.6 && uv sync --locked --no-dev
```

Start command:

```bash
.venv/bin/uvicorn tbd_server.main:app --host 0.0.0.0 --port $PORT --workers 1 --no-access-log
```

The blueprint points to `main`; deploy the reviewed merged implementation, or explicitly select the implementation branch for an authorized review deployment. Apply the migration first, then set all required environment values and deploy compatible code. `/healthz` checks process startup only; verify a protected API request and database behavior separately. Production URL is not known yet. Use the actual Render URL in the later client configuration.

## Gmail SMTP and join templates (TBD-16)

The founder enters a Google App Password **directly in Supabase** using an eligible Google account with 2-Step Verification. Use `smtp.gmail.com`, port 587/STARTTLS, sender name `TBD`, and the founder's selected Gmail address as sender/username. Never use the normal Google password or publish the personal address. No Resend or custom sending domain is required.

Disable public signup and unused Auth providers. Set the final Site URL and only the intended production/local redirects. Configure these template links, replacing `JOIN_SITE_URL` with the actual join origin:

```html
<!-- Invite template -->
<a href="JOIN_SITE_URL/join#token_hash={{ .TokenHash }}&amp;type=invite">Join TBD</a>
<!-- Magic Link template for confirmed-identity recovery -->
<a href="JOIN_SITE_URL/join#token_hash={{ .TokenHash }}&amp;type=email">Join TBD</a>
```

Disable link tracking. A link GET must not verify a token. After explicit verification, the browser sends only its bearer token to `POST /v1/join`; any supplied signup details are ignored. The backend validates confirmed identity and approved data and returns a committed User response. The page retains its verified session in memory on API failure; after reload/expiry the founder resends. The reviewed TBD-15 page is implemented on its existing client branch.

Only an explicitly authorized recipient may receive a live test message. The founder has explicitly authorized one individual and one business test recipient, recorded privately outside Git. Use distinct synthetic test phones. TBD-16 records deployed URLs/SHAs, inbox delivery, both User types, recovery, failures, and browser table denial. Local tests and SMTP acceptance do not establish delivery or full-service completion.

Primary references checked for implementation: [Supabase Python invitations](https://supabase.com/docs/reference/python/auth-admin-inviteuserbyemail), [list users](https://supabase.com/docs/reference/python/auth-admin-listusers), [database roles](https://supabase.com/docs/guides/database/postgres/roles), [Render FastAPI](https://render.com/docs/deploy-fastapi), [Render blueprint](https://render.com/docs/blueprint-spec). The current Supabase changelog was checked; its upcoming HTTP TLS cipher removal requires no special code with the supported Python/OpenSSL stack. Live configuration remains unverified.


## TBD-16 hosted evidence and current blockers

Checked 2026-11-01 against `tbd-v1` only. No old project, unrelated Vercel project, or paid resource was changed.

- Existing application schema/migration history were empty before application. Hosted history now contains `20261101103939 / user_service`.
- All three tables have RLS enabled; `anon` and `authenticated` lack schema usage and table SELECT grants. Only the restricted backend role has application policies/grants. The role has no superuser, database/role creation, replication, RLS-bypass or Auth-schema access.
- Browser publishable-key HTTP probes for all three tables with `Accept-Profile: user_service` return `406 / PGRST106` (schema not exposed).
- Hosted rollback-only fake-data SQL passed under `tbd_user_backend`: seed DML, partial unique default constraint, default switch, invalid seed-type rejection and Auth-table read denial. No fixture rows remain. The migration owner's temporary ability to SET ROLE was enabled inside this transaction only and was verified reverted after rollback. This tests SQL privileges/constraints, **not** password/pooler connectivity or deployed API behavior.
- Security advisor: no findings. Performance advisor: one informational unused `addresses_user_id` index on the newly empty table; retain it for the approved address lookup.
- Public Auth settings return `disable_signup=false`, with email the only enabled provider. Disable public signup in the Dashboard before acceptance. No invite or magic-link email has been sent.
- Render access must be connected before workspace inspection/service creation; the founder reports SMTP entered and has privately authorized the recipient pair. Final Auth URLs/templates, server secret configuration, and inbox delivery verification remain required.
- Vercel `client` is not visible in the connected account/team. Founder confirmed `https://vercel.com/robel-kebedes-projects/client` and `https://client-pi-five-28.vercel.app`. The connector still cannot access it; resolve team consent before configuring/deploying it. The local CLI is not logged in; it was not used to create/link a replacement.

No deployed API/join URL exists from this implementation yet. The full hosted API/email/browser acceptance matrix remains pending. Local accepted checks were not rerun merely because cloud access is pending.


### Access retry after founder configuration update

Both reviewed application heads remain accepted. The migration and completed database evidence were preserved, not rerun.

- `get_project(idOrName="client", teamId="team_OW5Zi2dAAdSNUopzhMiPbzM5")` still returns **404 / not_found / Project not found**. The domain lookup returns the same error. `authorize_team_access` returned **requires_user_action**, with a single-use ten-minute consent link. Founder action: open the link from the session, approve the existing connection's team access, then confirm completion. A new link is needed if it expires. Do not recreate the project.
- Render plugin discovery reports **installed=false**, and no Render operations are available in this session. Founder action: install/connect Render and grant access to the supplied workspace. The agent cannot complete that account login on the founder's behalf.
- Local `supabase whoami --output json` timed out after 12 seconds without a usable identity result. The installed CLI's Auth configuration workflow needs a local login. Founder action: authenticate the CLI directly in a terminal, or toggle public signup off directly in the intended project's Dashboard. No credential should be pasted into chat. A minimal temporary `auth.enable_signup=false` file is prepared outside Git; current CLI help confirms undeclared properties are left unchanged. Review `config diff` before pushing after authentication.
- Public `/auth/v1/settings` still returned `disable_signup=false`, email enabled. SMTP saving is founder-reported; the public settings endpoint cannot establish SMTP delivery.
- The supplied production `/join` URL returns **404 / NOT_FOUND** before deployment. The founder reports its current production source as main `705c5dd`; this session has not verified a deployment SHA through Vercel metadata.

No live invitation has been sent. Authorized records are stored only in private temporary input files outside both repositories; addresses and SMTP credentials are excluded from public evidence. No new API or reviewed-branch client deployment exists yet. Credentialed pooler connectivity, templates/redirects/CORS, both real join types and the hosted recovery/access matrix remain pending hosting/Auth access.
