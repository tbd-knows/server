# User service setup

Checked 2026-10-31. These are setup facts, not implementation completion.

| Tool | Verified state | Remaining implementation setup |
| --- | --- | --- |
| GitHub | Both `tbd-knows/server` and `tbd-knows/client` accessible with write permissions; no application code | Merge planning PRs, then implement the linked Linear work |
| Supabase | Created fresh `tbd-v1` in the supplied TBD organization; project `xclftpnjwrmakooaaxik`, region `us-east-1`, healthy; no application tables | Store backend secrets securely, apply migrations, disable public signups, configure Auth URLs and templates |
| Linear | User Service project `P-TBD-2`, TBD team; no prior project issues | Use the parent and three implementation issues linked in the planning PR |
| Notion | Tech Hub readable; design index created beneath it | Keep links to the canonical GitHub design rather than maintaining a second specification |
| Render | Supplied TBD workspace accessible; no services returned | Create one Python web service from the implemented server repo; set secrets, port, and `/healthz`; verify live deployment |
| Vercel | Supplied team readable; no matching `client` project returned | Project creation returned 403. A team member with creation permission must create/import `tbd-knows/client`, root `apps/web`, framework Vite, output `dist` |
| Email | Resend selected for Supabase SMTP; official integration verified | Create/use a Resend account, verify a TBD-owned sending domain with its DNS owner, and configure SMTP credentials in Supabase; no emails sent in this phase |

The old inactive `aptknows-auth` Supabase project was left unchanged. Fresh-project creation was quoted at $0/month; that quote does not cover future hosting or email usage.

## Configuration ownership

| Location | Required configuration |
| --- | --- |
| Render only | `DATABASE_URL` (dashboard-supplied connection string), `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `PROVISIONING_TOKEN`, `INTERNAL_SERVICE_TOKEN`, `JOIN_SITE_URL`, `ALLOWED_WEB_ORIGINS` |
| Vercel/browser | `VITE_API_URL`, `VITE_SUPABASE_URL`, `VITE_SUPABASE_PUBLISHABLE_KEY`; all are public values |
| Supabase Dashboard | Disable public signups and unused providers; set production Site URL; allow only intended local/production redirects; configure Resend SMTP and both Invite/Magic Link templates |
| Founder's local environment | API URL and provisioning token for the founder CLI; never commit input files containing real waitlist/seed data |

SMTP passwords and the Supabase secret key must never be included in the client repo or client environment. Generate tokens into secret stores, not terminal transcripts. No secret values were collected or committed in this phase.

## Complete setup during implementation

1. Build and test backend locally with disposable data. Create migrations through the Supabase CLI. Use the current CLI's `--help`; do not invent flags or migration names.
2. Deploy the API in the supplied Render workspace. Apply reviewed migrations explicitly, configure secrets, and verify `/healthz` and an authenticated API call.
3. Resolve the Vercel 403, link the client repo, and deploy the join page. Set the actual production URL in both the API's CORS settings and Supabase Auth settings.
4. Configure the verified sending domain and SMTP before customizing templates or testing arbitrary external recipients. Supabase's default mailer is not the release path.
5. Set Invite and Magic Link templates exactly as specified in [design.md](design.md). Disable link tracking. Test scanner GET, actual Join, resend, and a consumed-token/API-failure recovery.
6. Complete the deployed acceptance matrix and attach sanitized outcomes and commit IDs to Linear. Do not call the service ready while delivery, hosting, or access checks remain unverified.

The Linear agent-context tool was advertised but returned “Tool get_agent_context not found.” No shared field notes were available through it; the design uses this conversation, inspected repositories, and primary product/platform sources.
