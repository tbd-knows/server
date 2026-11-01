# TBD server

Python backend services for TBD v1. The first implementation is the User service.

- [User service design](docs/user-service/design.md)
- [Architecture drawing](docs/user-service/architecture.excalidraw)
- [Setup and remaining prerequisites](docs/user-service/setup.md)
- [Implementation work](https://linear.app/apt-knows/project/user-service-89260eb3e4ce/overview)

This repository currently contains the planning handoff. Runtime commands, dependencies, migrations, tests, and Render configuration are added by the implementation issues.

Keep server-side logic here; the TypeScript join page belongs in [client](https://github.com/tbd-knows/client).

TBD-14 implementation is now in progress. See the [implementation checkpoint](docs/user-service/implementation-status.md) for tested behavior, environment blockers, and remaining work. No deployment is ready yet.

With dependencies installed, local checks are `python -m pytest -q` and `python -m ruff check src tests`. Real database tests require a new local database named `tbd_user_test_*` through `TEST_DATABASE_URL`; they refuse nonlocal hosts and do not reset an existing schema. The standard `uv sync --locked` workflow remains pending lockfile generation.
