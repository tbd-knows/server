# User service setup

Updated 2026-11-03. **User service v1 is implemented, merged, deployed and verified.** [Implementation status and acceptance evidence](implementation-status.md) is the completion record; [design.md](design.md) defines the supported scope.

## Verified production configuration

| Component | Verified state |
| --- | --- |
| GitHub | Server PR #3 and client PR #3 are merged. The verified application release is server `9d69ab1776af65147fe5237b4f1abb5620a2113d` and client `f04707d8b46c201db82bbb724511c02c0c9734c8`; later documentation commits do not change that application source. |
| Supabase | Existing `tbd-v1` / `xclftpnjwrmakooaaxik`; migration `20261101103939` already applied. Private schema/RLS/backend grants and browser denial verified. Public signup/unused providers disabled; SMTP delivery and invitation/recovery links verified. Preserve accounts and the migration; do not modify old `aptknows-auth`. |
| Render | Existing service `srv-dbjhuto473hc739t8c9g` in TBD workspace `tea-das0s48u01pc73e7km90`; <https://tbd-user-api.onrender.com>. Tracks main with auto-deploy off. Verified release deployment `dep-dbjrrsvlk1mc73a4jgog`; startup verified restricted role/TLS/session pooling. |
| Vercel | Existing `client` project `prj_bjfGLoc1nxSWc9rdnZ7CDX7HLSXX` in team `team_OW5Zi2dAAdSNUopzhMiPbzM5`; <https://client-pi-five-28.vercel.app/join>. Root `apps/web`, Node 22/Vite, npm ci/build, dist. Verified application deployment `dpl_2D14St9M8zMVY4Myg4FxThTh6CGU`; the temporary rollback build pause was removed before this release. |
| Email | Gmail SMTP sends the correct direct `/join` fragment links. Prior individual/business recovery and fresh individual inbox receipt/Join are verified. Default subject text remains a nonblocking difference. Keep SMTP credentials out of chat, Git and browser builds. |

Deployment IDs/SHAs identify the application release verified on 2026-11-03; documentation-only commits or equivalent builds may follow. Inspect live metadata before future operational changes.

## Configuration

| Location | Values |
| --- | --- |
| Render only | `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `PROVISIONING_TOKEN`, `INTERNAL_SERVICE_TOKEN`, `JOIN_SITE_URL`, `ALLOWED_WEB_ORIGINS` |
| Browser/Vercel | `VITE_API_URL`, `VITE_SUPABASE_URL`, `VITE_SUPABASE_PUBLISHABLE_KEY` (all public) |
| Founder CLI | `TBD_API_URL`, `PROVISIONING_TOKEN`; JSON records outside Git |
| Supabase Dashboard | SMTP credentials, disabled public signups/unused providers, production Site URL and exact redirect allowlist, Invite and Magic Link templates |

`ALLOWED_WEB_ORIGINS` is a comma-separated exact allowlist for the intended local origin and `https://client-pi-five-28.vercel.app`. Do not allow every preview deployment. Generate independent random bearer secrets into secret stores. No real secret values belong in `.env.example`.

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

## Render deployment

`render.yaml` documents Python 3.12.12, free web compute, repository root, manual deployments, no disk, and `/healthz`. Reuse the existing service above. Paid plan or deployment-policy changes require a separate operational decision.

Build command:

```bash
pip install uv==0.13.6 && uv sync --locked --no-dev
```

Start command:

```bash
.venv/bin/uvicorn tbd_server.main:app --host 0.0.0.0 --port $PORT --workers 1 --no-access-log
```

The service and blueprint track `main`; keep Render auto-deploy off. For a future release, review compatibility and any pending migration, configure required values, then explicitly deploy the intended commit. The existing release migration must not be reapplied. `/healthz` checks process startup only; verify a protected API request and database behavior separately. Verified API URL: `https://tbd-user-api.onrender.com`. Use this API origin in the client deployment.

## Gmail SMTP and join templates (TBD-16)

The founder enters a Google App Password **directly in Supabase** using an eligible Google account with 2-Step Verification. Use `smtp.gmail.com`, port 587/STARTTLS, sender name `TBD`, and the founder's selected Gmail address as sender/username. Never use the normal Google password or publish the personal address. No Resend or custom sending domain is required.

Disable public signup and unused Auth providers. Set the final Site URL and only the intended production/local redirects. Configure these template links, replacing `JOIN_SITE_URL` with the actual join origin:

```html
<!-- Invite template -->
<a href="JOIN_SITE_URL/join#token_hash={{ .TokenHash }}&amp;type=invite">Join TBD</a>
<!-- Magic Link template for confirmed-identity recovery -->
<a href="JOIN_SITE_URL/join#token_hash={{ .TokenHash }}&amp;type=email">Join TBD</a>
```

Disable link tracking. A link GET must not verify a token. After explicit verification, the browser sends only its bearer token to `POST /v1/join`; any supplied signup details are ignored. The backend validates confirmed identity and approved data and returns a committed User response. The page retains its verified session in memory on API failure; after reload/expiry the founder resends. The TBD-15 page is merged on main and deployed at the production URL above.

Only an explicitly authorized recipient may receive a live test message. Keep recipient inputs and invitation links outside Git. The completed TBD-16 evidence covers delivery, both User types, recovery, failure cases and browser table denial, with exact live/controlled boundaries in [implementation-status.md](implementation-status.md). SMTP acceptance alone is not inbox proof.

Primary references checked for implementation: [Supabase Python invitations](https://supabase.com/docs/reference/python/auth-admin-inviteuserbyemail), [list users](https://supabase.com/docs/reference/python/auth-admin-listusers), [database roles](https://supabase.com/docs/guides/database/postgres/roles), [Render FastAPI](https://render.com/docs/deploy-fastapi), [Render blueprint](https://render.com/docs/blueprint-spec). The current Supabase changelog was checked; its upcoming HTTP TLS cipher removal requires no special code with the supported Python/OpenSSL stack.


## Acceptance and operations

The [completion record](implementation-status.md) contains the verified release, 54 fresh hosted API/state checks, individual email/Auth/Join/SQL proof, retained earlier business/recovery evidence, and recorded 71 backend / 38 browser tests. Fresh SQL reads succeeded; all existing accounts and the migration were preserved. Test addresses were cleared.

Idle-start API responses were observed at about 22.8–32.4 seconds against the client's 15-second timeout, so a first Join may require Retry. Same-session recovery remains in memory; reload or session expiry requires founder resend. Do not reset a joined identity to repeat an onboarding test.

Default email subjects and the Auth leaked-password-protection advisory remain documented operational notes. Neither prompted credential re-entry, template changes, new infrastructure or a paid-plan change during verification.
