# TBD-14 implementation checkpoint

2026-11-01. In progress; not deployable or accepted yet.

Branch: `robiemelaku/tbd-14-user-service`. Both planning PR #1 merges are present on local main (server `c9bffa4`, client `b28c2dc`). Both initial working trees were clean. Client remains untouched; TBD-15 follows backend completion.

Implemented candidate code:

- Strict normalized invitation/seed inputs and protected profile fields.
- Transactional join, seed retry, sender lookup, profile revision checks, saved-address ownership and default operations.
- FastAPI routes with separate provisioning/internal credentials and resolved own-user header checks.
- Founder file/stdin CLI and separate Supabase Auth admin/session clients; email reconciliation and manual recovery paths.
- Candidate private schema in `tests/schema.sql`. This is **not** a release migration. Do not apply it to the live project.
- Exact direct dependencies in `pyproject.toml`; no lockfile generated yet.

Checks actually run using installed Python 3.12 packages:

```
/opt/homebrew/opt/python@3.12/bin/python3.12 -m pytest -q
# 16 passed, 5 skipped (real database suite needs TEST_DATABASE_URL)
/opt/homebrew/opt/python@3.12/bin/python3.12 -m ruff check src tests
# All checks passed
```

These checks establish validation and API authorization behavior only. The Supabase SDK is absent locally; the Auth adapter has not been executed. No email sent, cloud resource changed, migration applied, deployment attempted, or end-to-end flow verified.

Environment blockers observed:

- Shell DNS fails for github.com, PyPI and npm registry; dependency/tool installation and Git remote operations fail.
- `uv` is unavailable. Cached Supabase CLI exists under npm's npx cache but aborts attempting to write its telemetry file outside the writable sandbox. Its Go helper does not expose `migration new`.
- `initdb` fails with `shmget: Operation not permitted`; local PostgreSQL socket access is also denied. Do not replace required database proof with mocks or use the live project for destructive testing.
- Linear issue reads/status updates work. Agent context endpoint returns “Tool get_agent_context not found.”

Next work, in order:

1. Restore shell network/package access and permission for disposable PostgreSQL IPC/socket access and CLI configuration; install uv/Supabase CLI. Resolve dependencies and commit `uv.lock`.
2. Verify actual pinned SDK signatures and test invitation failure/reconciliation/confirmed resend. Expand join/identity/API-failure tests and response models/OpenAPI.
3. Generate the migration via `supabase migration new` after checking current help; include backend role grants, private schema restrictions and defense-in-depth RLS. Run disposable SQL suite and full failure matrix. Audit contact conflicts across pending invitations and seeded users.
4. Review address caller-ID retry versus edit semantics against the design; extend concurrent revision/default tests and direct constraint tests.
5. Add Render configuration and exact run/CLI/setup instructions. Update diagram if the implemented contract changes. Open draft backend PR and report evidence on TBD-14.
6. Proceed to TBD-15, then TBD-16; keep issues open until their criteria pass.

Founder deployment actions later: enter Google App Password directly in Supabase, authorize a real test recipient, provide backend database/server credentials through secret stores. Never request or publish secrets in chat. Inspect existing resources before changes; preserve the old Auth project.
