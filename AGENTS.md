# Implementation rules

- Read the assigned Linear issue and its service design under `docs/` first. For Messaging, also read `docs/user-service/design.md` for the Join and identity contract. Implement TBD v1 only; ask before expanding product scope.
- Keep Python backend logic here. Use the planned FastAPI/Supabase stack; add no extra services, roles framework, ORM, queue, or cache without a concrete requirement.
- Use Markdown under `docs/`. Diagrams must be editable `.excalidraw` files; no Mermaid. Keep the design and implemented API consistent.
- No real waitlist/seed records or secrets in this public repository. Use fake fixtures, environment variables, pinned dependencies, and lockfiles.
- Implement server-side identity and own-user checks. LLM/tool arguments cannot establish identity or override account contacts.
- Verify real database constraints/transactions and meaningful failure paths. Follow the Supabase skill and current CLI help for migrations.
- Keep changes reviewable and follow the issue's acceptance criteria. This handoff does not authorize unrelated platform scaffolding.

- Do not merge implementation PRs until Robel Bruk and Robel Kebede have both reviewed and explicitly approved merging. Agent technical acceptance is not founder approval.
