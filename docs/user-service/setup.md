# User service setup

Updated 2026-11-01. **Technical verification complete; founder review pending.** Both previous implementation merges were reverted at the founder's request. Review the fresh PRs from `review/user-service-founders` in each repository. Robel Bruk and Robel Kebede must both review and explicitly approve merging. [Current evidence](implementation-status.md) distinguishes local tests from deployment proof.

| Component | State / next action |
| --- | --- |
| GitHub | Both premature implementation merges reverted on main. Fresh implementation PRs await Robel Bruk and Robel Kebede review; no merge authorization. |
| Supabase | Use existing `tbd-v1`, reference `xclftpnjwrmakooaaxik`. Reviewed migration applied at version `20261101103939`. Private schema/RLS/grants and browser Data API denial verified. Public signup is verified disabled (`disable_signup=true`, email still enabled). SMTP inbox delivery and correct fragment links pass; both authorized accounts have joined with original Auth identities. Default subjects are a nonblocking difference. Never change old `aptknows-auth`. |
| Render | Verified TBD workspace. Created free Python service `srv-dbjhuto473hc739t8c9g` from the existing backend branch with auto-deploy off. Live at `https://tbd-user-api.onrender.com`, commit `a9045c8d4ef369188581c35d2fe21004aa80590f`. Actual startup SQL confirms backend role, TLS and session pooling. Inspect workspace `tea-das0s48u01pc73e7km90` before provisioning/reusing one service. |
| Vercel | Existing `client` project serves <https://client-pi-five-28.vercel.app/join> from merge commit `fea2a07898e0a1289eff23746ef907e6710806a4`, deployment `dpl_G6Zbyvd9nnLJkT7AAb6PcBfLt7zc`. Ignored Build Step is temporarily `exit 0` so Git rollback preserves the current live site. Revisit after both founders approve review and deployment. No replacement project created. |
| Email | Actual inbox delivery verified for both authorized test aliases, including fresh resends. Correct fragment destinations and both join/recovery flows verified. Only the subject text differs from the configured expectation. Never enter the App Password into chat, Git, or the browser application. |

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

The blueprint points to `main`; deploy the reviewed merged implementation, or explicitly select the implementation branch for an authorized review deployment. Apply the migration first, then set all required environment values and deploy compatible code. `/healthz` checks process startup only; verify a protected API request and database behavior separately. Verified API URL: `https://tbd-user-api.onrender.com`. Use this API origin in the client deployment.

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


## Current deployed acceptance

Both authorized real test accounts are joined: exactly one correctly typed application User each, matching approved details and original Auth identities. Preserve those records. Raw MIME confirms correct direct page fragments; business reload/resend recovery and individual same-session Retry with one OTP verification are accepted. Both consumed recovery links were subsequently rejected by production Auth with 403 and no Join API calls.

The [reconciled acceptance matrix](implementation-status.md#reconciled-acceptance-matrix) maps every design row to its exact local, controlled, or live evidence. It also lists the exact failures not separately repeated live. The latest local backend suite passes 71 tests, zero skips, after R1; the prior 64-test backend checkpoint is historical. Existing 38 client browser tests, zero skips, are reused because client behavior is unchanged. One strengthened real-database HTTP integration test passed during the earlier reconciliation. R1 is a later backend behavior fix, tested locally and not deployed.

Backend URL: <https://tbd-user-api.onrender.com>, deployed SHA `a9045c8d4ef369188581c35d2fe21004aa80590f`, Render deploy `dep-dbji8de7bikc73fhmeh0`. Runtime SQL proved `current_user=tbd_user_backend`, libpq TLS active and session-pooler endpoint/session port. Client URL: <https://client-pi-five-28.vercel.app/join>, last verified production SHA `fea2a07898e0a1289eff23746ef907e6710806a4`, Vercel deployment `dpl_G6Zbyvd9nnLJkT7AAb6PcBfLt7zc`. The earlier deployment `dpl_4umzXBnL2yQFKQAjksSZaFuJEMJJ` at `2c5ebe0fa33d929a85cc74d47807199c162aac7a` is a **historical verification checkpoint**; its browser evidence remains valid, but it is not the current deployment. Auto-deploy remains off for Render; documentation changes do not redeploy accepted code.

Hosted evidence preserved: migration 20261101103939, private schema RLS/backend-only grants, no browser Data API access (406/PGRST106), backend Auth-table denial, rollback-only constraint checks, restricted password/TLS/session-pooler connectivity, protected hosted API fixture harness for seeding, resolution, revisions, ownership, address/default operations and CORS. Hosted synthetic addresses were removed; two contact-free synthetic seed Users remain. No live reset or migration reapplication occurred.

Received email subjects still use Supabase defaults despite saved `Join TBD` subjects; this is a nonblocking configuration difference. Actual bodies/fragment destinations and inbox delivery are verified. Do not repeat SMTP credential entry or template saves without new evidence.

Previous bundled Render tool-routing errors and Vercel project 404s were resolved through direct MCP/CLI access; no replacement projects were created. The new account's attempted Supabase read-only SQL refresh returned MCP -32001 Unknown tool for supabase.execute_sql; accepted prior SQL evidence remains valid, but further SQL work requires restoring that connection. This is not a new application failure.

Technical checks passed with the documented evidence limits and email-subject difference. Founder implementation review remains pending; the previous claim of founder merge authorization was incorrect. Both main branches are reverted and new review PRs contain the complete implementation. Preserve secrets, CORS, URLs, build/start commands, Render manual deploys, existing accounts and the applied migration. No new fault injection or account reset is authorized by this handoff.
