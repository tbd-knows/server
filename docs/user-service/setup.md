# User service setup

Updated 2026-11-01. The backend is implemented and locally tested on [draft PR #2](https://github.com/tbd-knows/server/pull/2); it remains under review. [Current evidence](implementation-status.md) distinguishes local tests from deployment proof.

| Component | State / next action |
| --- | --- |
| GitHub | Both planning PRs merged; backend implementation is a draft PR. Client implementation has not started. |
| Supabase | Use existing `tbd-v1`, reference `xclftpnjwrmakooaaxik`. This implementation has not changed its schema or Auth settings. Inspect live resources before applying anything. Never change old `aptknows-auth`. |
| Render | Deployment prepared in root `render.yaml`; no service created or deployed by this implementation. Inspect workspace `tea-das0s48u01pc73e7km90` before provisioning/reusing one service. |
| Vercel | Existing imported `client` project in `robel-kebedes-projects`. Deferred until TBD-15/TBD-16; no backend dependency. |
| Email | Gmail SMTP entry and delivery verification remain founder actions. Never enter the App Password into chat, Git, or the browser application. |

## Configuration

| Location | Values |
| --- | --- |
| Render only | `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `PROVISIONING_TOKEN`, `INTERNAL_SERVICE_TOKEN`, `JOIN_SITE_URL`, `ALLOWED_WEB_ORIGINS` |
| Browser/Vercel, later | `VITE_API_URL`, `VITE_SUPABASE_URL`, `VITE_SUPABASE_PUBLISHABLE_KEY` (all public) |
| Founder CLI | `TBD_API_URL`, `PROVISIONING_TOKEN`; JSON records outside Git |
| Supabase Dashboard | SMTP credentials, disabled public signups/unused providers, production Site URL and exact redirect allowlist, Invite and Magic Link templates |

`ALLOWED_WEB_ORIGINS` is a comma-separated exact allowlist, initially `http://localhost:5173` plus the production join origin when known. Do not allow every preview deployment. Generate independent random bearer secrets into secret stores. No real secret values belong in `.env.example`.

## Migration workflow

The release migration is `supabase/migrations/20261101045729_user_service.sql`. It was created with Supabase CLI 2.121.0 using `supabase migration new user_service` after reading current help. Disposable tests apply this exact file. It creates only the three application tables, private-schema grants/RLS policies, and a backend database login with no password. Browser roles have neither schema nor table access. Only `tbd_user_backend` has application DML policies; it cannot read Auth tables or administer schema/roles.

Before deployment, review the file and inspect the live project/schema/migration history. Authenticate the CLI locally; keep database credentials in its secure prompt/secret environment, not command history. Check current CLI help before execution:

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

Disable link tracking. A link GET must not verify a token. After explicit verification, the browser sends only its bearer token to `POST /v1/join`; any supplied signup details are ignored. The backend validates confirmed identity and approved data and returns a committed User response. The page retains its verified session in memory on API failure; after reload/expiry the founder resends. This page is subsequent TBD-15 work.

Only an explicitly authorized recipient may receive a live test message. No recipient is authorized yet. TBD-16 records deployed URLs/SHAs, inbox delivery, both User types, recovery, failures, and browser table denial. Local tests and SMTP acceptance do not establish delivery or full-service completion.

Primary references checked for implementation: [Supabase Python invitations](https://supabase.com/docs/reference/python/auth-admin-inviteuserbyemail), [list users](https://supabase.com/docs/reference/python/auth-admin-listusers), [database roles](https://supabase.com/docs/guides/database/postgres/roles), [Render FastAPI](https://render.com/docs/deploy-fastapi), [Render blueprint](https://render.com/docs/blueprint-spec). The current Supabase changelog was checked; its upcoming HTTP TLS cipher removal requires no special code with the supported Python/OpenSSL stack. Live configuration remains unverified.
