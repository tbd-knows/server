# TBD-14 implementation checkpoint

Updated 2026-11-01. **Technical verification complete; founder review pending.** Robel Bruk and Robel Kebede must both review and explicitly approve merging. The previous implementation merges were premature and have been reverted on both main branches. The technical evidence below is preserved; it does not establish founder review or merge authorization.

Review branches: `review/user-service-founders` in both repositories. The earlier [server PR #2](https://github.com/tbd-knows/server/pull/2) and [client PR #2](https://github.com/tbd-knows/client/pull/2) are historical merged PRs, superseded by fresh founder-review PRs. Earlier agent reviews and test results remain evidence only.

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

**Latest result after R1: 71 passed, zero skipped.** The earlier full 64-test run is a historical checkpoint. Ruff, formatting, and `git diff --check` pass. One upstream Starlette/AnyIO deprecation warning remains; it is not a test failure. An earlier complete run passed 49 tests with zero skips; subsequent constraint, CLI, and access cases expanded the suite. This replaces the initial 16-passed/5-skipped checkpoint.

The runner creates a disposable local PostgreSQL cluster, runs the full suite over TCP, stops it and removes its own artifacts. Application operations run as `tbd_user_backend`, not the database owner. The fixture supplies a minimal local `auth.users` table only for Auth foreign keys; no Supabase project is used/reset.

Evidence includes:

- Individual/business joins, no User before join, unapproved/mismatched identity denial, ignored client signup tampering, concurrent joins, rollback before commit, and retry after a committed response is lost.
- Real database unique/check/foreign-key constraints, concurrent seed/contacts/revisions/default changes, contact conflict handling, actual browser-role SQL denial, and backend denial of Auth-table reads.
- Address save retry/no revision bump, changed-ID payload conflict, missing-address edit denial, stale edit rejection, owned edit/delete/default/replacement, clearing optional fields, default deletion and last-address deletion, atomic rollback of default changes.
- Pinned Supabase SDK **2.33.0** executed with fake HTTP transport: real SDK parsing and request construction for `get_user`, paginated `list_users`, invite/resend, timeout/expired credential handling, and confirmed recovery with `create_user=false`. These tests send no email and do not prove a live Auth server or SMTP configuration.
- A controlled FastAPI + real database + SDK fake-HTTP harness verifies invitations, failed-send reconciliation, join recovery, sender resolution, profile/address operations, and access denial.
- Production-only environment (`uv sync --locked --no-dev`) successfully started real Uvicorn over TCP and passed `/healthz`, OpenAPI, and an unauthorized internal-call check. Development dependencies were then restored with the lockfile.
- Supabase CLI 2.121.0 created the release migration after current help was read. `db advisors` against the disposable database reported **No issues found**. Local CLI SQL connections needed `sslmode=disable` because that throwaway server has no TLS; production instructions require TLS.

## Reconciled acceptance matrix

Both live flows passed the recorded technical checks. Founder review of the implementation and evidence boundary is pending. The earlier acceptance reconciliation required no application change. R1 now changes only unbound invitation handling inside Join; it has not been deployed. Preserve all live records and the migration.

| Approved scenario | Executed result and evidence | Level |
| --- | --- | --- |
| Individual and small-business invitations | PASS: inbox delivery, actual raw-MIME fragment destinations, explicit Join and SQL proof of exactly one User of each type. Approved name/phone/email/type, invitation association and original Auth IDs match. | Live Auth/browser/API/SQL; accepted |
| Link opened/prefetched without button press | PASS: real links show Join, scrub the fragment, and make no verification/User POST before click; SQL showed zero Users before initial joins. | Live browser/SQL; accepted |
| Expired, malformed, or used token | PASS: malformed links fail locally; expired/used Auth errors show resend without User POST in Chromium/WebKit tests. Reopened both consumed real recovery links against production during reconciliation: one Auth request each, 403, clear error, zero Join API requests. | Live consumed-link rejection; controlled expiry and malformed cases |
| Valid session with no approved entry | PASS: confirmed Auth response through installed SDK and actual FastAPI route returns 403; real PostgreSQL proves zero User insert. Wrong Auth ID against an approved entry is also rejected. | Controlled Auth + real SDK/API/database; not a separate live unapproved identity |
| Client changes signup fields | PASS: HTTP Join with tampered name/type/phone/ID returns stored approved data and original User ID. | Accepted real-database HTTP test with controlled Auth |
| Double Join / API timeout / concurrent Join | PASS: browser duplicate-click guard; simultaneous database joins return same ID; HTTP recovery after committed response loss returns same ID/count one. Live individual first-API 503 then same-session Retry: exactly one OTP verification, two API attempts, one User. | Accepted browser/real-database tests; live 503/retry |
| Token consumed, then API fails | PASS: business verification/API failure followed by reload requires resend; confirmed identity receives type=email and joins. Individual retries without reload or another OTP verification. | Live; accepted |
| Failed/uncertain email send | PASS: SDK timeout leaves approved entry, reconciliation finds original Auth identity before resend; actual HTTP/database test preserves entry and later same User. Live successful sends/inbox delivery separately proved. | Controlled failure injection + real database; no deliberate live SMTP outage |
| Duplicate phone/email and repeated business seed | PASS: constraints and contact conflicts/races; hosted seed retry returns same contact-free User. | Accepted real-database and hosted API evidence |
| Invalid internal credential / cross-user address | PASS: invalid credential and wrong scope rejected; foreign read/edit/delete/default-replacement denied. | Accepted real-database and hosted API evidence |
| Two updates with same revision | PASS: one concurrent update wins; stale revision gets 409. | Accepted real-database and hosted API evidence |
| Multiple addresses / default switch / deletion | PASS: save retry/no revision bump, changed-content conflict, edit-existing/current-revision rules, owner locking, atomic defaults, replacement and last-address deletion. | Accepted real-database and hosted API evidence |
| Saved address changed after purchase | PASS by scope/code inspection: this service has no order table/client/write path. Saved-address changes cannot rewrite orders. No payment/order implementation was introduced. | Structural evidence; no fabricated order flow |
| Browser tries Supabase application tables | PASS: hosted browser Data API 406/PGRST106; no browser schema/table grants; backend cannot read Auth tables. | Accepted hosted grants/API and real-database evidence |

### Targeted reconciliation checks

Only the missing direct HTTP admission assertion was added to the existing `test_http_invite_join_retry_failure_recovery_and_profile_contract` test. A confirmed SDK Auth response is supplied before any approval exists; API returns 403 and PostgreSQL count is zero. Its existing remainder also asserts tampering, uncertain email-send recovery and lost committed-response retry. Executed against a fresh disposable PostgreSQL instance:

```bash
PATH=/opt/homebrew/opt/postgresql@17/bin:/opt/homebrew/opt/postgresql@16/bin:$PATH .venv/bin/python scripts/run_database_tests.py -q tests/test_database.py::test_http_invite_join_retry_failure_recovery_and_profile_contract
```

Result: **1 passed, zero skipped**, known upstream deprecation warning. Changed-test Ruff/format and repository whitespace checks pass. This targeted result supplements the accepted **64-passed/no-skips** full backend suite and **38-passed/no-skips** client suite; neither full suite was rerun unnecessarily. The private production browser rejection harness passed for both consumed recovery links. No tokens or contacts were recorded publicly.

### Exact limits and status

No failed application assertion remains in the reconciled matrix. The following are **not independently repeated live**: a fresh verified identity with no approval, a fresh unconsumed token allowed to expire naturally, signup-field tampering using a live session, simultaneous first-time live joins, loss of a successful live commit response, and a real SMTP failure. They have controlled browser/SDK/API/real-database evidence as stated above. Do not describe these as entirely hosted proof. Both authorized live identities are already joined and must be preserved; no account reset, approval deletion, new recipient, live timeout setting change or deliberate SMTP outage is justified to repeat them. This evidence mix is for the founders to review. Preserve these limits; do not infer permission for additional hosted fault injection.

The email **link bodies work**: initial invite type=invite and confirmed recovery type=email target the reviewed canonical /join page. Received subjects remain `You've been invited` and `Your sign-in link` despite founder-saved `Join TBD` subjects. This is a **nonblocking configuration difference**, not a broken-link or joining failure; no new sends/config changes were made to investigate it in this run.

A read-only SQL refresh in the new Codex connection failed with `MCP -32001: Unknown tool({"name":"supabase.execute_sql"})`. Preserve the already accepted final hosted SQL evidence instead of claiming a fresh read succeeded. Restoring that connector routing is required only for further SQL work; it did not block the browser rejection checks or controlled database test. No additional execution/network permission is needed.

## Deployed and published state

- Backend: <https://tbd-user-api.onrender.com>, deployed code **a9045c8d4ef369188581c35d2fe21004aa80590f**. Actual restricted role/TLS/session-pooler proof and health check accepted.
- Client: <https://client-pi-five-28.vercel.app/join>, deployed commit **fea2a07898e0a1289eff23746ef907e6710806a4**, Vercel deployment `dpl_G6Zbyvd9nnLJkT7AAb6PcBfLt7zc`. This is the previous merge deployment; reverting Git does not roll back the live site.
- Supabase migration **20261101103939** is already applied; no reapplication/reset. Public signup disabled; SMTP/inbox delivery verified.
- The review branch now includes R1, which binds a verified identity to an unbound approved invitation inside Join. It is locally tested, not deployed; the live backend and client remain at the SHAs above. No deployment or cloud setting was changed.
- Founder review remains pending. Both main branches have been restored to their pre-implementation trees with forward revert commits; the complete implementation is proposed on `review/user-service-founders`. Render auto-deploy remains off. Vercel's Ignored Build Step is temporarily `exit 0` to preserve the existing live site while Git is reverted. Revisit that temporary setting only after both founders approve the implementation and deployment plan. Default subjects remain a recorded configuration difference.

## PR #3 review fixes — R1 / R2

R1: `Store.join` binds a NULL Auth ID using only the confirmed ID and normalized email provided by `auth.verified_identity`. Binding and User creation/return share the existing transaction and invitation row lock. No nested `bind_auth` transaction, Auth-admin lookup, migration, endpoint or client change was added. Missing approval and different bound identities still fail; uniqueness and rollback remain enforced.

New disposable-database coverage: both User types joining before binding, delayed original binding, two concurrent joins racing delivery binding (one User), identity uniqueness conflict with no partial bind, and binding rollback on failed commit. API/installed-SDK tests pause a delivered invite before its response, join while its binding is NULL, and cover both delayed success and lost Auth response with no founder resend. They retain unconfirmed/mismatched/no-approval denial, approved-details and exactly-one-User assertions.

Commands run from server:

```bash
PATH=/opt/homebrew/opt/postgresql@17/bin:/opt/homebrew/opt/postgresql@16/bin:$PATH .venv/bin/python scripts/run_database_tests.py -q
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
git diff --check
```

Results: **71 passed, zero skipped** against fresh disposable PostgreSQL; Ruff/format/whitespace pass. Known upstream Starlette/AnyIO deprecation warning remains. Existing client browser evidence (38 passed/no skips) is reused because browser behavior is unchanged. Recovery after reload/expired sessions remains founder resend.

R2: current deployment statements consistently identify last verified production client `fea2a07898e0a1289eff23746ef907e6710806a4` / `dpl_G6Zbyvd9nnLJkT7AAb6PcBfLt7zc`. Initial `2c5ebe0fa33d929a85cc74d47807199c162aac7a` / `dpl_4umzXBnL2yQFKQAjksSZaFuJEMJJ` checks are explicitly historical. This uses existing verified evidence; no cloud inspection or mutation was needed.

Both PR #3s remain unmerged on `review/user-service-founders`. TBD-13–16 remain In Progress. The reviewer's remaining source coverage and explicit merge approvals from Robel Bruk and Robel Kebede are pending. Existing live deployments, joined accounts, migration, Render auto-deploy off and Vercel Ignored Build Step `exit 0` are preserved.
