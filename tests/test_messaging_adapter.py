"""Synthetic fixtures derived from Comms documentation, not actual account proof."""

from datetime import datetime, timezone
import hashlib
import hmac
import json
from uuid import uuid4

import httpx
import pytest

from tbd_server.messaging.adapter import Comms, signed_event
from tbd_server.user.store import Problem

ACCOUNT, PEER = "+12125550100", "+12125550101"


def inbound(**changes):
    return {
        "id": "msg_fake",
        "conversation_id": "conversation_fake",
        "direction": "inbound",
        "channel": "imessage",
        "audience": "direct",
        "line_e164": ACCOUNT,
        "author": {"external_id": PEER, "contact_id": "contact_fake"},
        "body": " \n🙂 https://example.test\n ",
        "attachments": [],
        "created_at": "2026-11-08T23:00:00Z",
        **changes,
    }


def job():
    return {
        "id": uuid4(),
        "conversation_id": "conversation_fake",
        "account_ref": ACCOUNT,
        "text": "Received your text.",
        "provider_message_id": None,
    }


def outbound(j, **changes):
    return {
        "id": "msg_reply_fake",
        "conversation_id": j["conversation_id"],
        "line_e164": ACCOUNT,
        "direction": "outbound",
        "channel": "imessage",
        "body": j["text"],
        "status": "submitted",
        **changes,
    }


def test_signed_event_uses_signed_event_field_and_native_id():
    raw = json.dumps({"id": "envelope", "event": "comms.message.received", "data": {"message": inbound()}}).encode()
    sig = "sha256=" + hmac.new(b"fake-secret", raw, hashlib.sha256).hexdigest()
    assert signed_event(raw, sig, "fake-secret")["data"]["message"]["id"] == "msg_fake"
    with pytest.raises(Problem) as error:
        signed_event(raw + b" ", sig, "fake-secret")
    assert error.value.status == 401


@pytest.mark.parametrize(
    "change",
    [
        {"direction": "outbound"},
        {"channel": "sms"},
        {"audience": "shared"},
        {"line_e164": "+12125550999"},
        {"author": {"external_id": "alias@example.test"}},
        {"unsent_at": "2026-11-08T23:01:00Z"},
    ],
)
def test_excluded_content(change):
    assert Comms.normalize(inbound(**change), ACCOUNT) is None


def test_strict_mapping_preserves_exact_text_and_media_caption():
    event = Comms.normalize(inbound(), ACCOUNT)
    assert event.text == inbound()["body"]
    assert event.peer == PEER and event.conversation_id == "conversation_fake"
    assert event.source_at == datetime(2026, 11, 8, 23, tzinfo=timezone.utc)
    assert Comms.normalize(inbound(body=None, attachments=[{"mediaType": "image"}]), ACCOUNT).unsupported
    for key in ("audience", "author", "attachments", "created_at", "conversation_id"):
        record = inbound()
        del record[key]
        with pytest.raises(Problem):
            Comms.normalize(record, ACCOUNT)


@pytest.mark.parametrize(
    "status,state",
    [
        ("submitted", "provider_queued"),
        ("sent", "sent"),
        ("delivered", "sent"),
        ("failed", "failed"),
        ("suppressed", "failed"),
        ("unknown", "uncertain"),
    ],
)
def test_response_states(status, state):
    j = job()
    assert Comms.classify(outbound(j, status=status), j).state == state
    assert Comms.classify(outbound(j, conversation_id="wrong"), j).state == "uncertain"


def test_send_duplicate_and_retry_keep_key_payload_and_channel():
    j = job()
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer fake-key"
        assert request.headers["idempotency-key"] == str(j["id"])
        assert json.loads(request.content) == {
            "conversation_id": j["conversation_id"],
            "body": j["text"],
            "channel": "imessage",
        }
        if len(calls) == 1:
            raise httpx.ReadTimeout("fake timeout")
        return httpx.Response(200, json={"duplicate": True, "message": outbound(j)})

    adapter = Comms("fake-key", transport=httpx.MockTransport(handler))
    assert adapter.send(j).state == "pending"
    assert adapter.send(j).state == "provider_queued"
    assert calls[0].content == calls[1].content
    adapter.close()


@pytest.mark.parametrize(
    "code,data,state,delay",
    [
        (429, {"retry_after": 17}, "pending", 17),
        (429, {}, "pending", 60),
        (402, {}, "pending", 3600),
        (401, {}, "pending", 60),
        (409, {"code": "idempotency_in_progress"}, "pending", None),
        (503, {"code": "line_channel_unavailable"}, "pending", None),
        (400, {}, "failed", None),
    ],
)
def test_provider_rejections(code, data, state, delay):
    adapter = Comms("fake", transport=httpx.MockTransport(lambda _: httpx.Response(code, json=data)))
    result = adapter.send(job())
    assert (result.state, result.retry_after) == (state, delay)
    adapter.close()


def test_empty_429_preserves_default_account_pause():
    adapter = Comms("fake", transport=httpx.MockTransport(lambda _: httpx.Response(429)))
    try:
        result = adapter.send(job())
        assert (result.state, result.error_code, result.retry_after) == ("pending", "rate_limited", 60)
    finally:
        adapter.close()


def test_status_matches_exact_id_in_conversation_no_post():
    j = job()
    j["provider_message_id"] = "msg_reply_fake"

    def handler(request):
        assert request.method == "GET"
        assert request.url.params["conversation_id"] == j["conversation_id"]
        assert request.url.params["direction"] == "outbound"
        return httpx.Response(
            200, json={"messages": [outbound(j, id="other", status="sent"), outbound(j, status="sent")]}
        )

    adapter = Comms("fake", transport=httpx.MockTransport(handler))
    assert adapter.status(j).state == "sent"
    j["provider_message_id"] = "missing"
    assert adapter.status(j).state == "uncertain"
    adapter.close()
