# TBD-14 implementation checkpoint

Updated 2026-11-01. **In Progress, awaiting review.** Backend candidate is locally tested; no hosted deployment or delivery acceptance is claimed.

Review: [draft backend PR #2](https://github.com/tbd-knows/server/pull/2), branch `robiemelaku/tbd-14-user-service`. Both planning merges are present. Client remains unchanged; TBD-15 has not started.

## Implemented candidate

- Strict normalized invitation/seed inputs, separate provisioning/internal credentials, server-resolved own-user scope, and protected profile fields.
- Store-before-send invitations; paginated Auth identity reconciliation after uncertain responses; unconfirmed invitation resend and confirmed-email magic-link recovery without creating another identity. Joined entries send no further onboarding email.
- Supabase-validated confirmed identity and approved server-owned signup details; transactional/idempotent join, including concurrent requests and uncertain committed responses.
- Contact-free business seeding, contact conflicts across seeds and pending invitations, trusted sender lookup, profile revisions, owned addresses, and atomic default changes.
- Address PUT `operation: "save"` creates once; identical retries are no-ops without revision increments, while changed content under the same ID conflicts. `operation: "edit"` requires an existing owned address and current revision, never creates a missing address, and preserves default status. These are tested and documented request details of the approved contract.
- Founder CLI, OpenAPI request/response/error schemas, exact CORS origins, no-store/no-referrer responses, and sanitized errors/results.
- Exact dependencies and generated `uv.lock`; Python 3.12.12 and uv 0.13.6.
- CLI-generated release migration `supabase/migrations/20261101045729_user_service.sql`; private schema, three tables, constraints, one-default index, backend login grants and RLS, no browser grants/policies. The old test-only schema copy was removed; database tests apply the actual release file.
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

1. Planning/founder review of the actual draft PR, migration, and contract; address any feedback. Keep TBD-14 In Progress until review completes.
2. Apply reviewed migration to `tbd-v1` explicitly and verify hosted Auth/database grants, custom-role/pooler connectivity, Render build/deployment, and protected API behavior during TBD-16. Render blueprint is preparation, not hosted proof.
3. Later TBD-15 client verification and TBD-16 full scanner/explicit-click/expired-link recovery and external inbox evidence. No real recipient is authorized yet; no test email was sent.
4. Founder enters the Gmail App Password directly in Supabase and configures templates/Auth URLs before delivery proof. Server/database secrets belong in secret stores, not chat. Vercel verification remains deferred.

No intended scope/architecture deviation. The address operation discriminator makes save retry versus edit intent explicit on the existing endpoint. No new issue breakdown or product surface was added. Review and deployed acceptance remain distinct from local engineering evidence.
