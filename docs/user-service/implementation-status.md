# TBD-14 implementation checkpoint

Updated 2026-11-01. **TBD-16 In Progress.** Backend and client reviews passed; both PRs remain open/unmerged pending complete service acceptance. Hosted schema/access checks have started; no healthy hosted application or delivery acceptance is claimed. The first Render build succeeded but startup requires the database environment variable.

Review: [backend PR #2](https://github.com/tbd-knows/server/pull/2), branch `robiemelaku/tbd-14-user-service`. Both planning merges are present. Backend review passed at `2bb8c5103d30a48199f506770672bda8957d5e6e`; client review passed at `2c5ebe0fa33d929a85cc74d47807199c162aac7a` ([client PR #2](https://github.com/tbd-knows/client/pull/2)). Integration uses these same branches.

## Implemented candidate

- Strict normalized invitation/seed inputs, separate provisioning/internal credentials, server-resolved own-user scope, and protected profile fields.
- Store-before-send invitations; paginated Auth identity reconciliation after uncertain responses; unconfirmed invitation resend and confirmed-email magic-link recovery without creating another identity. Joined entries send no further onboarding email.
- Supabase-validated confirmed identity and approved server-owned signup details; transactional/idempotent join, including concurrent requests and uncertain committed responses.
- Contact-free business seeding, contact conflicts across seeds and pending invitations, trusted sender lookup, profile revisions, owned addresses, and atomic default changes.
- Address PUT `operation: "save"` creates once; identical retries are no-ops without revision increments, while changed content under the same ID conflicts. `operation: "edit"` requires an existing owned address and current revision, never creates a missing address, and preserves default status. These are tested and documented request details of the approved contract.
- Founder CLI, OpenAPI request/response/error schemas, exact CORS origins, no-store/no-referrer responses, and sanitized errors/results.
- Exact dependencies and generated `uv.lock`; Python 3.12.12 and uv 0.13.6.
- CLI-generated release migration `supabase/migrations/20261101103939_user_service.sql`; private schema, three tables, constraints, one-default index, backend login grants and RLS, no browser grants/policies. The old test-only schema copy was removed; database tests apply the actual release file.
- Render preparation in `render.yaml`, secret-free env template, runnable README and setup instructions. No automatic migration or paid resource was introduced.

## Verification actually run

Run from `server`, using uv 0.13.6 (`/private/tmp/tbd-tooling/bin/uv` in this session):

```bash
uv sync --locked
uv run --locked ruff check src tests scripts
uv run --locked ruff format --check src tests scripts
uv run --locked python scripts/run_database_tests.py -q
```

**Latest result: 62 passed, zero skipped.** Ruff, formatting, and `git diff --check` pass. One upstream Starlette/AnyIO deprecation warning remains; it is not a test failure. An earlier complete run passed 49 tests with zero skips; subsequent constraint, CLI, and access cases expanded the suite. This replaces the initial 16-passed/5-skipped checkpoint.

The runner creates a disposable local PostgreSQL cluster, runs the full suite over TCP, stops it and removes its own artifacts. Application operations run as `tbd_user_backend`, not the database owner. The fixture supplies a minimal local `auth.users` table only for Auth foreign keys; no Supabase project is used/reset.

Evidence includes:

- Individual/business joins, no User before join, unapproved/mismatched identity denial, ignored client signup tampering, concurrent joins, rollback before commit, and retry after a committed response is lost.
- Real database unique/check/foreign-key constraints, concurrent seed/contacts/revisions/default changes, contact conflict handling, actual browser-role SQL denial, and backend denial of Auth-table reads.
- Address save retry/no revision bump, changed-ID payload conflict, missing-address edit denial, stale edit rejection, owned edit/delete/default/replacement, clearing optional fields, default deletion and last-address deletion, atomic rollback of default changes.
- Pinned Supabase SDK **2.33.0** executed with fake HTTP transport: real SDK parsing and request construction for `get_user`, paginated `list_users`, invite/resend, timeout/expired credential handling, and confirmed recovery with `create_user=false`. These tests send no email and do not prove a live Auth server or SMTP configuration.
- A controlled FastAPI + real database + SDK fake-HTTP harness verifies invitations, failed-send reconciliation, join recovery, sender resolution, profile/address operations, and access denial.
- Production-only environment (`uv sync --locked --no-dev`) successfully started real Uvicorn over TCP and passed `/healthz`, OpenAPI, and an unauthorized internal-call check. Development dependencies were then restored with the lockfile.
- Supabase CLI 2.121.0 created the release migration after current help was read. `db advisors` against the disposable database reported **No issues found**. Local CLI SQL connections needed `sslmode=disable` because that throwaway server has no TLS; production instructions require TLS.

## Environment and remaining acceptance

The earlier environment failures are resolved in this session. Git push/PR creation, PyPI/npm access, CLI configuration writes, and disposable PostgreSQL shared memory/TCP all work. **No further execution/network permission is currently needed.**

Historical failures were `git push`/package installs with DNS resolution errors, Supabase CLI telemetry writes with EPERM, and `initdb` with `shmget: Operation not permitted`. These are no longer current blockers; no service was redesigned around them.

Remaining acceptance:

1. Both implementation reviews passed with no requested changes. Hold both PRs unmerged until the complete User service passes acceptance. TBD-16 is now In Progress; no new branch or PR is used for integration.
2. Reviewed migration is explicitly applied to `tbd-v1`, recorded as `20261101103939`; local filename aligned without SQL changes. Hosted RLS/grants, public Data API denial and rollback-only backend-role SQL assertions pass. Custom-role password/pooler connectivity, hosted Auth configuration, Render deployment and protected API proof remain pending. See [setup.md](setup.md) for exact evidence and blockers.
3. TBD-15 local browser verification and review passed. TBD-16 still requires full deployed scanner/explicit-click/expired-link recovery and external inbox evidence. The founder has privately authorized individual/business recipients; no test email was sent.
4. Founder reports the Gmail App Password saved directly in Supabase. Templates/Auth URLs and inbox delivery remain unverified. Server/database secrets belong in secret stores, not chat. Vercel verification remains deferred.

No intended scope/architecture deviation. The address operation discriminator makes save retry versus edit intent explicit on the existing endpoint. No new issue breakdown or product surface was added. Deployed acceptance remains distinct from the accepted local engineering evidence. Current cloud blockers: Render direct MCP access is working and the exact TBD workspace is verified; first build succeeded but startup fails with missing DATABASE_URL, expected Vercel client project not visible, public signup now verified disabled, server credentials/SMTP/templates not configured or verified, and no live delivery proof yet (recipient authorization is now provided privately). No email sent or application live. Render service srv-dbjhuto473hc739t8c9g is created on the existing branch; generated API credentials are set. Founder must provide DATABASE_URL/SUPABASE_SECRET_KEY directly in Render and set /healthz. See setup.md for the failed deploy ID and exact attempted SHA.
