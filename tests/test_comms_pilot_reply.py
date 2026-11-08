"""One-off proof checks use synthetic records and no live account."""

from copy import deepcopy

import pytest

from scripts.comms_pilot_reply import EXPECTED_TEXT, main, sanitized_pair, verify_pair


def records():
    message = {
        "id": "msg_fake",
        "conversation_id": "conversation_fake",
        "contact_id": "contact_fake",
        "line_e164": "+12125550100",
        "line_id": "line_fake",
        "channel": "imessage",
        "direction": "inbound",
        "body": EXPECTED_TEXT,
        "created_at": "2026-11-08T22:38:34.756Z",
        "author": {"external_id": "+12125550101", "contact_id": "contact_fake"},
    }
    event = {
        "id": "event_fake",
        "event": "comms.message.received",
        "data": {
            "message": message,
            "contact": {"id": "contact_fake", "phone": "+12125550101"},
            "conversation_id": "conversation_fake",
        },
    }
    conversation = {
        "id": "conversation_fake",
        "phone": "+12125550101",
        "contact_id": "contact_fake",
        "channel": "imessage",
        "line_e164": "+12125550100",
    }
    return event, deepcopy(message), conversation


def test_manual_proof_can_compare_omitted_fields_without_inventing_them():
    event, history, conversation = records()
    assert verify_pair(event, history, conversation)["id"] == "msg_fake"
    pair = sanitized_pair(event, history)
    assert "audience" not in pair["history"] and "attachments" not in pair["history"]
    assert "one-off handset confirmation" in pair["classification_limit"]
    assert pair["webhook"]["data"]["message"]["id"] == pair["history"]["id"]


@pytest.mark.parametrize("change", ["peer", "conversation", "group", "attachment"])
def test_manual_proof_rejects_mismatches_and_explicit_excluded_content(change):
    event, history, conversation = records()
    if change == "peer":
        conversation["phone"] = "+12125550999"
    if change == "conversation":
        history["conversation_id"] = "other"
    if change == "group":
        history["audience"] = "shared"
    if change == "attachment":
        history["attachments"] = [{"mediaType": "image"}]
    with pytest.raises(AssertionError):
        verify_pair(event, history, conversation)


def test_handset_confirmation_required_before_any_api_call(monkeypatch):
    monkeypatch.delenv("COMMS_PILOT_DIRECT_TEXT_CONFIRMED", raising=False)
    with pytest.raises(ValueError, match="Handset confirmation"):
        main()
