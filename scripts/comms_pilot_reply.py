"""One manually confirmed Pilot A exchange; not production admission logic.

Runtime key is read from the temporary environment, never saved. Exact raw
capture/history stay private. Missing audience/attachments are not defaulted:
this one-off proof additionally requires explicit handset confirmation.
"""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx

EXPECTED_TEXT = "app-tbd TBD pilot 1"
REPLY = "Received your text."


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def private_json(path, data):
    with path.open("w") as file:
        os.chmod(path, 0o600)
        json.dump(data, file, indent=2)


def verify_pair(event, history, conversation):
    message = event["data"]["message"]
    check(event["event"] == "comms.message.received", "Wrong event")
    check(message["channel"] == "imessage" and message["direction"] == "inbound", "Wrong transport")
    check(message["body"] == EXPECTED_TEXT, "Unexpected pilot message")
    check(event["data"]["conversation_id"] == message["conversation_id"], "Envelope route mismatch")
    check(event["data"]["contact"]["phone"] == message["author"]["external_id"], "Signed sender mismatch")
    check(
        message["contact_id"] == message["author"]["contact_id"] == event["data"]["contact"]["id"],
        "Signed contact mismatch",
    )
    for key in ("id", "conversation_id", "contact_id", "line_e164", "channel", "direction", "body", "created_at"):
        check(history.get(key) == message[key], "History mismatch: " + key)
    check(history.get("author") == message["author"], "History sender mismatch")
    for record in (message, history):
        if "audience" in record:
            check(record["audience"] == "direct", "Explicitly non-direct record")
        if "attachments" in record:
            check(record["attachments"] == [], "Attachment present or unclassifiable")
        check(not record.get("unsent_at"), "Message unsent")
    check(conversation["id"] == message["conversation_id"], "Conversation mismatch")
    check(conversation["phone"] == message["author"]["external_id"], "Conversation peer mismatch")
    check(conversation["contact_id"] == message["contact_id"], "Conversation contact mismatch")
    check(conversation["line_e164"] == message["line_e164"], "Conversation line mismatch")
    check(conversation["channel"] == "imessage", "Conversation transport mismatch")
    return message


def sanitized_pair(event, history):
    message = event["data"]["message"]
    replacements = {
        message["id"]: "msg_pilot_a_inbound",
        message["conversation_id"]: "conversation_pilot_a",
        message["contact_id"]: "contact_pilot_a",
        message["author"]["external_id"]: "+12125550101",
        message["line_e164"]: "+12125550100",
        event["id"]: "event_pilot_a_received",
    }
    if message.get("line_id"):
        replacements[message["line_id"]] = "line_shared_pilot"
    safe_strings = {"event", "object", "status", "channel", "direction", "audience", "created_at", "sentAt"}

    def clean(value, key=""):
        if isinstance(value, dict):
            return {k: clean(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [clean(v) for v in value]
        if isinstance(value, str):
            if value in replacements:
                return replacements[value]
            if key in safe_strings or (key == "body" and value == EXPECTED_TEXT):
                return value
            return "[redacted]"
        return value

    return {
        "evidence": "actual signature-verified capture and API history; identifiers sanitized",
        "classification_limit": "audience/attachments absent; one-off handset confirmation required; not a production default",
        "webhook": clean(event),
        "history": clean(history),
    }


def main():
    if os.environ.get("COMMS_PILOT_DIRECT_TEXT_CONFIRMED") != "YES":
        raise ValueError("Handset confirmation required before this one-off send")
    root = Path("/private/tmp/tbd-comms-capture")
    capture = root / "4baad90a-d44b-4730-9592-eac48f5975f9.private.json"
    receipts = []
    for line in (root / "receiver.log").read_text().splitlines():
        try:
            receipts.append(json.loads(line))
        except ValueError:
            pass
    check(
        any(
            (
                r.get("capture") == "signature_verified"
                and r.get("capture_id") == capture.stem
                and (r.get("event") == "comms.message.received")
                for r in receipts
            )
        ),
        "Matching verification receipt missing",
    )
    event = json.loads(capture.read_text())
    message = event["data"]["message"]
    with httpx.Client(
        base_url="https://osis.co/api/v1/comms/",
        timeout=10,
        follow_redirects=False,
        headers={"Authorization": "Bearer " + os.environ["COMMS_API_KEY"]},
    ) as client:
        response = client.get(
            "messages", params={"conversation_id": message["conversation_id"], "direction": "inbound", "limit": 200}
        )
        response.raise_for_status()
        records = response.json()["messages"]
        matches = [r for r in records if r.get("id") == message["id"]]
        check(len(matches) == 1, "Exact inbound history record missing or duplicated")
        response = client.get("conversations", params={"line_e164": message["line_e164"], "limit": 200})
        response.raise_for_status()
        conversations = [c for c in response.json()["conversations"] if c.get("id") == message["conversation_id"]]
        check(len(conversations) == 1, "Exact conversation not found")
        verify_pair(event, matches[0], conversations[0])
        private_json(root / "pilot-a-history.private.json", matches[0])
        private_json(root / "pilot-a-conversation.private.json", conversations[0])
        private_json(root / "pilot-a-sanitized-pair.json", sanitized_pair(event, matches[0]))
        state_path = root / "pilot-a-reply.private.json"
        if state_path.exists():
            state = json.loads(state_path.read_text())
        else:
            state = {
                "idempotency_key": str(uuid4()),
                "first_attempt_at": datetime.now(timezone.utc).isoformat(),
                "payload": {"conversation_id": message["conversation_id"], "body": REPLY, "channel": "imessage"},
            }
            private_json(state_path, state)  # Saved before any POST; never generate a replacement key.
        check(
            state["payload"] == {"conversation_id": message["conversation_id"], "body": REPLY, "channel": "imessage"},
            "Saved reply conflicts",
        )
        if not state.get("provider_message_id"):
            check(
                datetime.now(timezone.utc) - datetime.fromisoformat(state["first_attempt_at"]) < timedelta(hours=24),
                "Retry key window expired; do not resend",
            )
            response = client.post(
                "messages", headers={"Idempotency-Key": state["idempotency_key"]}, json=state["payload"]
            )
            # Preserve every response privately before deciding whether acceptance is known.
            state["response_status"] = response.status_code
            try:
                result = response.json()
                private_json(root / "pilot-a-send-response.private.json", result)
            except ValueError:
                private_json(state_path, state)
                raise ValueError("Send outcome unknown; retry only this saved key") from None
            private_json(state_path, state)
            response.raise_for_status()
            sent = result["message"]
            check(
                sent["conversation_id"] == message["conversation_id"] and sent["channel"] == "imessage",
                "Acceptance route unconfirmed",
            )
            check(
                sent["direction"] == "outbound"
                and sent["body"] == REPLY
                and (sent["line_e164"] == message["line_e164"]),
                "Acceptance identity unconfirmed",
            )
            state["provider_message_id"] = sent["id"]
            private_json(state_path, state)
            print(
                "Verified inbound/history/conversation match. Reply accepted; recipient delivery still requires confirmation."
            )
        response = client.get(
            "messages", params={"conversation_id": message["conversation_id"], "direction": "outbound", "limit": 200}
        )
        response.raise_for_status()
        exact = [r for r in response.json()["messages"] if r.get("id") == state["provider_message_id"]]
        private_json(root / "pilot-a-outbound-history.private.json", exact)
        if len(exact) == 1:
            observed = exact[0]
            check(
                (
                    observed.get("conversation_id"),
                    observed.get("channel"),
                    observed.get("direction"),
                    observed.get("line_e164"),
                    observed.get("body"),
                )
                == (message["conversation_id"], "imessage", "outbound", message["line_e164"], REPLY),
                "Outbound history route mismatch",
            )
        status = exact[0].get("status") if len(exact) == 1 else "not_yet_observed"
        print(
            "Exact reply status: "
            + (
                status
                if status in ("submitted", "sent", "delivered", "failed", "suppressed", "unknown", "not_yet_observed")
                else "unrecognized"
            )
        )
        print(
            "Sanitized pair saved privately. Ask Pilot A whether exactly one reply arrived in the original blue-iMessage thread."
        )


if __name__ == "__main__":
    try:
        main()
    except (httpx.HTTPError, AssertionError, ValueError, KeyError, OSError):
        raise SystemExit(
            "Proof stopped: a check or API request failed. No new key will be generated; inspect private evidence before retrying."
        ) from None
