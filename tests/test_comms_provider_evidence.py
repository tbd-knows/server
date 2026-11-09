"""Real pilot shapes with fake identifiers; never live account requests."""

import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from tbd_server.messaging.adapter import Comms
from tbd_server.user.store import Problem


def evidence():
    return json.loads(Path("docs/messaging-service/fixtures/comms-free-pilot-a.json").read_text())


def test_actual_text_shape_does_not_silently_default_missing_classification():
    fixture = evidence()
    message = fixture["webhook"]["data"]["message"]
    assert message["id"] == fixture["history"]["id"]
    assert message["conversation_id"] == fixture["history"]["conversation_id"] == fixture["conversation"]["id"]
    assert message["body"] == fixture["history"]["body"] == "app-tbd TBD pilot 1"
    for record in (message, fixture["history"]):
        assert "audience" not in record and "attachments" not in record
        with pytest.raises(Problem) as error:
            Comms.normalize(record, record["line_e164"])
        assert error.value.status == 422


def test_actual_unknown_outcome_stays_unresolved_and_known_id_is_read_only():
    fixture = evidence()
    message = fixture["acceptance"]["message"]
    assert fixture["acceptance"]["delivery_unknown"] is True
    job = {
        "id": uuid4(),
        "conversation_id": message["conversation_id"],
        "account_ref": message["line_e164"],
        "text": message["body"],
        "provider_message_id": None,
    }
    accepted = Comms.classify(message, job)
    assert accepted.state == "uncertain" and accepted.provider_id == message["id"]
    job["provider_message_id"] = accepted.provider_id

    def handler(request):
        assert request.method == "GET"
        assert request.url.params["conversation_id"] == message["conversation_id"]
        return httpx.Response(200, json={"messages": fixture["outbound_history"]})

    adapter = Comms("fake-key", transport=httpx.MockTransport(handler))
    try:
        result = adapter.status(job)
        assert result.state == "uncertain" and result.provider_id == message["id"]
    finally:
        adapter.close()


def test_pilot_missing_classification_requires_exact_allowlisted_pair():
    from copy import deepcopy

    fixture = evidence()
    original = fixture["history"]
    allowlist = {original["conversation_id"]: original["author"]["external_id"]}
    for record in (fixture["webhook"]["data"]["message"], original):
        event = Comms.normalize(record, record["line_e164"], pilot_conversations=allowlist)
        assert event.text == original["body"] and not event.unsupported
        assert Comms.normalize(record, record["line_e164"], pilot_conversations={}) is None
        changed = deepcopy(record)
        changed["author"]["external_id"] = "+12125550999"
        assert Comms.normalize(changed, record["line_e164"], pilot_conversations=allowlist) is None
        for field in ("audience", "attachments"):
            invalid = {**record, field: None}
            with pytest.raises(Problem):
                Comms.normalize(invalid, record["line_e164"], pilot_conversations=allowlist)
        with pytest.raises(Problem):
            Comms.normalize({**record, "body": ""}, record["line_e164"], pilot_conversations=allowlist)
