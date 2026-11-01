# User service founder-review handoff — 2026-11-01

**Technical verification complete; founder implementation review pending.** The approved design remains the scope. Robel Bruk and Robel Kebede must both review and explicitly approve merging. Previous claims that the founders reviewed the implementation or authorized its merge were incorrect.

Both premature PR #2 merges have been reverted on main using forward commits. The complete implementation is restored on `review/user-service-founders` in both repositories for fresh PR review. Do not merge, close the implementation issues, or redeploy on the strength of agent technical acceptance.

Read `AGENTS.md`, `docs/user-service/design.md`, `docs/user-service/setup.md`, and the [reconciled acceptance matrix](implementation-status.md#reconciled-acceptance-matrix). Preserve its distinction between controlled tests and live proof.

## Preserved state

- Backend: <https://tbd-user-api.onrender.com>, deployed SHA `a9045c8d4ef369188581c35d2fe21004aa80590f`. Existing Render service `srv-dbjhuto473hc739t8c9g`, workspace `tea-das0s48u01pc73e7km90`; auto-deploy remains off.
- Client: <https://client-pi-five-28.vercel.app/join>, deployed SHA `fea2a07898e0a1289eff23746ef907e6710806a4`, deployment `dpl_G6Zbyvd9nnLJkT7AAb6PcBfLt7zc`. Existing Vercel project `prj_bjfGLoc1nxSWc9rdnZ7CDX7HLSXX`, team `team_OW5Zi2dAAdSNUopzhMiPbzM5`. Ignored Build Step is temporarily `exit 0` to prevent the Git rollback replacing the live site. Revisit only after both founders approve the implementation and deployment plan.
- Supabase `tbd-v1`, reference `xclftpnjwrmakooaaxik`: migration `20261101103939_user_service` already applied. Do not reapply/reset it or touch `aptknows-auth`. Public signup is disabled; SMTP and correct email fragment links were verified.
- Exactly one individual and one business User were proven against their approved details and original Auth identities. Preserve both accounts. Default email subjects remain a documented configuration difference.
- Recorded suites after R1: 71 backend tests, zero skips; existing 38 Chromium/WebKit browser tests, zero skips, reused because the client is unchanged. R1 changes verified Join to bind a missing Auth ID in its existing transaction; the review fix is not deployed. See implementation-status.md for commands, coverage and R2 documentation reconciliation.

## Review and access

The evidence matrix records successful same-session Retry, reload/resend recovery, consumed-link rejection, ownership, constraints, revisions/defaults, browser database denial and the cases tested with controlled Auth/fault injection rather than independently live.

Keep invitation links, credentials, recipients, raw MIME and private browser artifacts out of Git, chat and public reports. Historical `/private/tmp/tbd16-integration-data/` and `/private/tmp/tbd16-live-browser.mjs` paths belong to the implementer's machine and may no longer exist. Reconstruct only through authorized scoped access if needed; do not recreate identities or resend messages simply to repeat settled proof.

Recheck the exact repository heads and live deployment metadata before reporting them as current. This handoff authorizes review preparation, not another merge or release.
