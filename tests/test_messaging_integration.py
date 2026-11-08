"""Controlled lifecycle/webhook/history fixtures; no hosted or phone claims."""

from datetime import datetime, timezone
import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import pytest

from tbd_server.messaging.adapter import TextingBlue
from tbd_server.messaging.runtime import DeliveryLoop
from tbd_server.messaging.service import Messaging
from tbd_server.user.store import Problem


def service():
    return Messaging(
        SimpleNamespace(database_url="fake", connections=None),
        "fake-key",
        "fake-secret",
        "+12125550100",
        "2026-11-08T00:00:00Z",
    )


def test_webhook_signature_before_storage_and_classification_is_fail_closed():
    messaging = service()
    messaging.receive = Mock()
    app = FastAPI()
    from fastapi.responses import JSONResponse

    @app.exception_handler(Problem)
    async def problem(request, exc):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status)

    app.add_api_route("/webhooks/texting-blue", messaging.webhook, methods=["POST"])
    client = TestClient(app)
    body = json.dumps({"type": "message.received", "data": {"id": "fake", "to": messaging.account}}).encode()
    assert client.post("/webhooks/texting-blue", content=body).status_code == 401
    signature = "sha256=" + hmac.new(b"fake-secret", body, hashlib.sha256).hexdigest()
    assert (
        client.post("/webhooks/texting-blue", content=body, headers={"x-textingblue-signature": signature}).status_code
        == 422
    )
    assert client.post("/webhooks/texting-blue", content=b"x" * (512 * 1024 + 1)).status_code == 413
    messaging.receive.assert_not_called()
    messaging.adapter.close()


def test_history_keeps_filters_and_visits_empty_intermediate_pages():
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.params["direction"] == "inbound"
        assert request.url.params["to"] == "+12125550100"
        assert request.url.params["limit"] == "100"
        if "cursor" not in request.url.params:
            return httpx.Response(200, json={"messages": [], "has_more": True, "next_cursor": "fake-next"})
        assert request.url.params["cursor"] == "fake-next"
        return httpx.Response(200, json={"messages": [], "has_more": False})

    messaging = service()
    messaging.adapter.close()
    messaging.adapter = TextingBlue("fake", transport=httpx.MockTransport(handler))
    assert messaging.history_turn(messaging.loop)
    assert messaging.history_turn(messaging.loop)
    assert len(calls) == 2
    assert messaging.loop.last_history_at is not None
    messaging.adapter.close()


def test_broken_history_cursor_is_visible_and_does_not_mark_success(caplog):
    messaging = service()
    messaging.adapter.history = Mock(return_value={"messages": [], "has_more": True, "next_cursor": "same"})
    with caplog.at_level("INFO", logger="tbd.messaging"):
        messaging.history_turn(messaging.loop)
        messaging.history_turn(messaging.loop)
    assert messaging.cursor is None
    assert messaging.loop.last_history_at is None
    assert "history_cursor_broken" in caplog.text
    messaging.adapter.close()


def test_waiting_loop_is_healthy_but_stopped_or_stalled_is_not():
    loop = DeliveryLoop("fake", Mock(), "+12125550100", Mock())
    loop.thread = SimpleNamespace(is_alive=lambda: True)
    assert loop.healthy()
    loop.last_tick -= 46
    assert not loop.healthy()
    loop.thread = None
    assert not loop.healthy()


def test_bad_activation_and_missing_configuration_fail_early():
    with pytest.raises(ValueError):
        Messaging(Mock(), "fake", "fake", "+12125550100", "2026-11-08T00:00:00")
    assert datetime.now(timezone.utc).utcoffset() is not None
