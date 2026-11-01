# TBD-14 implementation checkpoint

Updated 2026-11-01. **TBD-16 In Progress.** Backend and client reviews passed; both PRs remain open/unmerged pending complete service acceptance. Hosted schema/access checks have started; backend is live with runtime connection proof; complete invitation/client/delivery acceptance remains pending.

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

**Latest result: 64 passed, zero skipped.** Ruff, formatting, and `git diff --check` pass. One upstream Starlette/AnyIO deprecation warning remains; it is not a test failure. An earlier complete run passed 49 tests with zero skips; subsequent constraint, CLI, and access cases expanded the suite. This replaces the initial 16-passed/5-skipped checkpoint.

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
2. Reviewed migration is explicitly applied to `tbd-v1`, recorded as `20261101103939`; local filename aligned without SQL changes. Hosted RLS/grants, public Data API denial and rollback-only backend-role SQL assertions pass. Custom-role password/session-pooler connectivity, TLS, Render deployment and protected synthetic API checks now pass. Hosted Auth URLs/templates and complete invite/browser/recovery proof remain pending. See [setup.md](setup.md) for exact evidence and blockers.
3. TBD-15 local browser verification, review, and production deployment passed at `2c5ebe0fa33d929a85cc74d47807199c162aac7a`, serving <https://client-pi-five-28.vercel.app/join>. Backend remains deployed at `a9045c8d4ef369188581c35d2fe21004aa80590f`. Exact origins are configured in the client and API CORS. Accepted local checks were not repeated for documentation-only updates.
4. Gmail inbox delivery is now verified for both privately authorized test aliases, after verifying the connected mailbox identity. Initial and fresh founder-resend messages reached the inbox. However, fresh Invite and Magic Link emails still render Supabase's default subjects and `/auth/v1/verify` links, not the required `/join#token_hash=...` links. The agent did not open these links. Direct Dashboard access redirects to sign-in; connected database tools cannot edit Auth templates. Founder action: save both templates in the intended project's Dashboard using the complete batch in the client documentation, then verify fresh output before any controlled Join.
5. Both approved entries still have zero application Users. The business Auth identity remains unconfirmed. The individual Auth identity was already confirmed before this controlled browser sequence; its origin is not established by the available evidence. Use its recovery email for the individual retry cases, without deleting or resetting either identity. Correct stored types, passive real-link GET, explicit Join, valid-token API failure, same-session retry, lost-response retry, reload and founder-resend recovery remain pending the template correction.

No intended scope/architecture deviation. The address operation discriminator makes save retry versus edit intent explicit on the existing endpoint. No new issue breakdown or product surface was added. Public signup, migration, database connectivity, hosted internal API checks and reviewed deployments remain accepted. Actual inbox delivery now passes; the rendered template contract is the current blocker. Keep TBD-16 In Progress and both PRs unmerged. Personal recipient data, message bodies, invitation links and tokens remain outside the repositories and public evidence. See setup.md for the deployment and acceptance checkpoints.

Template recheck after the founder's second save confirmation: fresh messages at **2026-11-01 12:33:33–12:33:34 UTC** still have default subjects and verification links. Intended-project metadata matches; the business message's raw MIME independently confirms the result. No received link was opened; both application User counts remain zero. Founder was asked to fully reload and reopen both editors to check persisted configuration and report any save error, rather than blindly repeat saves or sends. No application source, deployment, migration, approval or Auth identity was changed.

Later live acceptance supersedes that checkpoint: fresh raw MIME for both authorized aliases contained the correct canonical `/join` fragment links. Business initial Join verified its token, hit the controlled API failure, and reload required founder resend; confirmed-email recovery with `type=email` then joined it. The individual recovery flow used a fresh `type=email` link, verified exactly once, received a controlled first-API 503, and succeeded through Retry in the same browser page with no second OTP verification (`verifies=1`, `calls=2`). Hosted SQL confirms **one business User and one individual User**; both have the approved name, phone, email, type, invitation link, and unchanged original Auth ID. Both received email subjects still use Supabase defaults despite the saved `Join TBD` values. TBD-16 remains In Progress while remaining matrix cases and that configuration difference are reconciled; both PRs remain unmerged.
