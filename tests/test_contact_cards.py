"""Controlled card boundary: no automated test may contact the live account."""

import base64
import json

import httpx
import pytest

from tbd_server.messaging.adapter import Comms, SendResult
from tbd_server.messaging.cards import identity, payload, PHOTO_SHA

ACCOUNT = "+12125550100"


def card_job(**changes):
    digest, key = identity(ACCOUNT, "conversation_fake_card")
    return {
        "account_ref": ACCOUNT,
        "conversation_id": "conversation_fake_card",
        "payload_sha256": digest,
        "idempotency_key": key,
        "provider_message_id": None,
        "response_body": None,
        **changes,
    }


def card_message(job, **changes):
    return {
        "id": "fake-card-native",
        "conversation_id": job["conversation_id"],
        "line_e164": ACCOUNT,
        "direction": "outbound",
        "channel": "imessage",
        "body": "",
        "status": "sent",
        **changes,
    }


def test_card_payload_and_empty_body_history_use_verified_shape():
    import hashlib

    job = card_job()
    requests = []

    def provider(request):
        requests.append(request)
        if request.method == "POST":
            assert request.url.path.endswith("/contact-cards")
            assert request.headers["Idempotency-Key"] == job["idempotency_key"]
            data = json.loads(request.content)
            assert data == payload(ACCOUNT, job["conversation_id"])
            assert data["name"] == "TBD" and data["phone"] == ACCOUNT and data["channel"] == "imessage"
            png = base64.b64decode(data["photo_base64"].split(",", 1)[1])
            assert len(png) < 200000 and hashlib.sha256(png).hexdigest() == PHOTO_SHA
            return httpx.Response(
                200,
                json={
                    "message": card_message(job, status="unknown", attachments=[{"content_type": "text/vcard"}]),
                    "contact_card": {"name": "TBD", "phone": ACCOUNT},
                },
            )
        assert request.url.params["conversation_id"] == job["conversation_id"]
        return httpx.Response(
            200, json={"messages": [card_message(job, id="wrong-id", status="read"), card_message(job, status="read")]}
        )

    adapter = Comms("fake", transport=httpx.MockTransport(provider))
    result = adapter.send_card(job)
    assert result.state == "uncertain" and result.provider_id == "fake-card-native" and result.response_body == ""
    observed = adapter.card_status({**job, "provider_message_id": result.provider_id, "response_body": ""})
    assert observed.state == "sent" and observed.observed_status == "read" and observed.response_body == ""
    assert len([r for r in requests if r.method == "POST"]) == 1
    adapter.close()


@pytest.mark.parametrize(
    "code,delay,error",
    [
        (401, 60, "account_configuration"),
        (403, 60, "account_configuration"),
        (402, 3600, "quota"),
        (429, 60, "rate_limited"),
    ],
)
@pytest.mark.parametrize("body", [b"", b"<html>unavailable</html>"])
def test_card_account_pauses_before_json(code, delay, error, body):
    adapter = Comms("fake", transport=httpx.MockTransport(lambda _: httpx.Response(code, content=body)))
    result = adapter.send_card(card_job())
    assert result == SendResult("pending", error_code=error, retry_after=delay)
    adapter.close()


@pytest.mark.parametrize(
    "change",
    [
        {"conversation_id": "wrong"},
        {"line_e164": "+12125550199"},
        {"channel": "sms"},
        {"direction": "inbound"},
        {"body": "wrong"},
    ],
)
def test_card_known_id_status_requires_route_and_saved_body(change):
    job = card_job(provider_message_id="fake-card-native", response_body="")
    adapter = Comms(
        "fake",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"messages": [card_message(job, status="read", **change)]})
        ),
    )
    assert adapter.card_status(job).state == "uncertain"
    adapter.close()


def test_card_failures_and_payload_changes_do_not_send_alternate_payload():
    calls = []
    adapter = Comms(
        "fake", transport=httpx.MockTransport(lambda req: calls.append(req) or httpx.Response(400, json={}))
    )
    assert adapter.send_card(card_job(payload_sha256="0" * 64)).error_code == "card_payload_changed"
    assert calls == []
    assert adapter.send_card(card_job()).state == "failed"
    adapter.close()


def test_real_sanitized_card_fixture_acceptance_and_history():
    from pathlib import Path

    fixture = json.loads(Path("docs/messaging-service/fixtures/comms-free-contact-card.json").read_text())
    assert fixture["acceptance"]["message"]["body"] == fixture["history"]["body"] == ""
    assert fixture["acceptance"]["message"]["attachments"][0]["content_type"] == "text/vcard"
    assert "attachments" not in fixture["history"]

    def provider(request):
        if request.method == "POST":
            return httpx.Response(fixture["http_status"], json=fixture["acceptance"])
        return httpx.Response(200, json={"messages": [fixture["history"]]})

    adapter = Comms("fake", transport=httpx.MockTransport(provider))
    try:
        job = card_job()
        accepted = adapter.send_card(job)
        assert accepted.state == "sent" and accepted.response_body == ""
        result = adapter.card_status(
            {**job, "provider_message_id": accepted.provider_id, "response_body": accepted.response_body}
        )
        assert result.state == "sent" and result.provider_id == accepted.provider_id
    finally:
        adapter.close()
