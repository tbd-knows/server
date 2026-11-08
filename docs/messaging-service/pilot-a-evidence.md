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
- Pilot A reported no automatic reply visible before the controlled send. This
  does not independently prove every provider automation setting is disabled.

## Remaining integration contracts

Actual webhook and history both omit `audience` and `attachments`. Missing fields
are not evidence of direct text. The production adapter still rejects this
shape; a manual one-off handset check is not an automatic admission rule. Obtain
provider-backed semantics or positive direct/content metadata, including media,
group, reaction, edit and unsend evidence, before changing admission.

The provider's unknown outcome, despite handset receipt, also needs a fresh
exact-ID read and provider clarification if it persists. The delivery loop must
keep that route unresolved until terminal evidence; silently treating acceptance
or a manual screenshot as provider-sent would change the approved contract.

Tests exercise these real sanitized shapes: missing classification remains 422;
known-ID unknown remains unresolved and uses GET only. No live API calls occur in
the suite. Keep PR #5 draft and TBD-18 In Progress. Full hosted proof with two
joined testers follows backend review, explicit founder merge approvals and merge.
