# TBD server

Python 3.12/FastAPI User service for TBD v1. [Approved design](docs/user-service/design.md), [editable architecture](docs/user-service/architecture.excalidraw), [setup/deployment](docs/user-service/setup.md), and [verification status](docs/user-service/implementation-status.md).

## Install and run

Install Python 3.12 and uv 0.13.6, then run these commands from this repository:

```bash
uv sync --locked
cp .env.example .env
# Fill server-only values in .env from your secret store.
uv run --locked --env-file .env uvicorn tbd_server.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

Apply the reviewed SQL migration explicitly first; startup and requests never migrate a database. `DATABASE_URL` must authenticate as `tbd_user_backend`, using the connection details supplied by Supabase and TLS in production. Do not use the database owner credential for the API. Both API credentials must be distinct random secrets. Public browser keys are insufficient for any internal route.

Startup checks the actual database role and TLS transport before serving, emitting only sanitized connection evidence. `GET /healthz` is a side-effect-free process check. `/docs` and `/openapi.json` document request/response schemas and bearer authentication. The service uses at most four concurrent database connections per process, with bounded wait/connect/query timeouts. Run one Uvicorn worker initially.

## Tests

```bash
uv run --locked ruff check src tests scripts
uv run --locked ruff format --check src tests scripts
uv run --locked python scripts/run_database_tests.py -q
```

The last command requires local PostgreSQL binaries (`initdb`, `pg_ctl`, `createdb`) on PATH. It creates its own temporary cluster, connects over localhost TCP, runs **all tests**, then stops and removes only its own cluster. No Docker, cloud credentials, live database, or email delivery is involved. Tests apply the actual release migration, provide a minimal disposable `auth.users` table for its foreign keys, and exercise application SQL under the restricted backend login. Auth tests exercise the pinned Supabase SDK through fake HTTP responses, not a live Auth server.

`uv run --locked pytest -q` without `TEST_DATABASE_URL` runs fast tests and **skips database tests**. For an externally started disposable instance, set `TEST_DATABASE_URL` to a new empty local database named `tbd_user_test_*` and run pytest. The fixture refuses nonlocal hosts, and fails if the schema already exists; it never resets it. Use a fresh cluster because the migration also creates the cluster-wide backend role.

## Founder commands

Export `TBD_API_URL` and `PROVISIONING_TOKEN` from your secret store. Use file/stdin inputs; do not place real records in tracked files or command arguments. The CLI prints only HTTP status, ID, and outcome.

```bash
uv run --locked tbd-user invite /secure/path/approved.private.json
uv run --locked tbd-user resend /secure/path/resend.private.json
uv run --locked tbd-user seed-business /secure/path/seed.private.json
# Any command accepts - instead of a filename for stdin.
```

Fake input shapes:

```json
{"name":"Example Person","phone":"+12125550100","email":"person@example.test","type":"individual"}
```

```json
{"id":"11111111-1111-4111-8111-111111111111"}
```

```json
{"seed_key":"example-shop","name":"Example Shop"}
```

Invitation success reports `sent`, meaning SMTP/API acceptance, not inbox proof. An uncertain send returns HTTP 503 and the invitation ID when available. Keep that ID and use `resend`; if no ID was received, repeat the identical approved entry to reconcile. An existing joined entry returns `already joined`. The approved entry commits before Auth is called; failures never delete it or recreate an identity. Seeds do not call Auth or send email.

## Internal profile contract

The trusted Messaging backend (implemented later) calls `/internal/users/resolve` with its own bearer credential and a normalized sender phone. It then supplies the returned UUID as `X-TBD-User-ID`, outside LLM-controlled tool inputs. Every profile/address path must match that context. Provisioning and browser credentials cannot call these routes.

Read `/internal/users/{id}/profile` for account details, revision, and the complete saved-address list; select an address by ID from this own-user list. Patch display name with `{"revision":0,"name":"Corrected Name"}`. Protected account fields are rejected. A stale mutation returns 409 and requires rereading and clarifying intent.

PUT `/internal/users/{id}/addresses/{caller_generated_uuid}` takes a full address plus revision and `operation` (`save` by default, or `edit`). Example:

```json
{"revision":0,"operation":"save","recipient":"Example Person","line1":"1 Example St","city":"Example","region":"NY","postal_code":"10001","country_code":"US","label":null,"line2":null}
```

`save` creates once; repeating the same ID/content is a no-op returning the current profile, even after a lost response. Different content under a saved ID conflicts. `edit` requires an existing own address and the current revision. It replaces supported fields; null/omitted optional fields clear saved label/line2. Reread after uncertain edits/deletes rather than blindly replaying them. Saving requires explicit reusable-address intent; task-only/gift destinations stay outside this service.

PUT `/internal/users/{id}/default-address` takes `{"revision":1,"address_id":"…"}`. DELETE an address with JSON `{"revision":2,"replacement_address_id":"…"}`; replacement is required when deleting the default with others remaining, forbidden for a nondefault deletion, and omitted when deleting the last address. Every successful mutation increments revision once, except an identical save retry. Ownership checks and all default/revision changes occur in one transaction. Saved-address operations have no order-writing path.

The join client belongs in the separate [client repository](https://github.com/tbd-knows/client). This backend does not implement Messaging, Agent, purchasing, or general login.
