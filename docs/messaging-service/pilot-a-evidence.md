# Pilot A — actual Comms Free exchange

2026-11-08. Early provider proof for TBD-18; not the deployed backend proof or
TBD-20 completion. [Sanitized actual records](fixtures/comms-free-pilot-a.json).
Real contacts, native IDs, keys, signatures and raw evidence stay private.

- Workspace reported by founder: Free, Live shared line assigned to TBD,
  Self-hosted, routing label `app-tbd`.
- Signed `comms.ping` and `comms.message.received` were saved by the verifying
  capture receiver. The inbound source time is `22:38:34.756Z`; its envelope time
  is 78 ms later. This difference is not measured webhook or reply latency.
- Pilot A sent `app-tbd TBD pilot 1` once as a blue iMessage. The full prefix and
  text appeared in both signed message and exact-ID inbound history. This first
  text reached TBD; no separate routing text was consumed in this observed case.
- Private comparison passed for native ID, original text/time, sender/contact,
  shared line and conversation. The conversation read matched the peer/contact,
  line and iMessage channel. Pilot A supplied the additional one-to-one/text-only
  handset confirmation required for this controlled proof.
- One manual API send used that conversation, explicit `channel: imessage`, fixed
  reply `Received your text.` and a UUID saved before POST. HTTP 202 acceptance
  returned a message ID and **delivery_unknown: true**, status **unknown**.
  The captured exact-ID outbound history also said **unknown**.
- Pilot A subsequently confirmed exactly one reply arrived in the original
  iMessage thread. This is handset evidence; it does not change the provider's
  recorded status to sent/delivered. No replacement POST or new key is justified.
- A later read-only history request at `23:05:38.437356Z` returned HTTP 200,
  matching the same outbound ID and route with raw status **read** and provider
  `read_at: 22:56:05.218Z`. The original acceptance and first unknown history
  snapshot are retained unchanged alongside this later record. The helper's
  initial `unrecognized` label was an incomplete allowlist, not the provider value.
  Verified `read` now maps to local `sent`, preserving observed status `read`.
- Pilot A reported no automatic reply visible before the controlled send. This
  does not independently prove every provider automation setting is disabled.

## Remaining integration contracts

Actual webhook and history both omit `audience` and `attachments`. The approved
plain-text pilot now pairs the confirmed conversation with its approved joined
phone in private server configuration. With matching shared line and nonempty
text, this pair supplies the pilot classification needed for webhook and history
admission. Explicit excluded evidence still wins. This does not establish a
general provider default; real media/group semantics remain outside this limited
plain-text pilot. The earlier manual exchange remains separate from hosted backend
proof; isolated integration tests now exercise this exact sanitized shape through
the backend's allowlisted path.

The later exact-ID `read` result resolves the earlier unknown send outcome.
Unknown acceptance alone still does not release a conversation; verified terminal
history does. No replacement send was made or needed.

Tests exercise these real sanitized shapes: missing classification without a matching configured pair is rejected;
known-ID unknown remains unresolved; exact-ID read becomes local sent while
preserving observed read and releases the next queued job. Status reads use GET only. No live API calls occur in
the suite. PR #5 proceeds to backend review; TBD-18 stays In Progress until review/integration. Full hosted proof with two
joined testers follows backend review, explicit founder merge approvals and merge.
