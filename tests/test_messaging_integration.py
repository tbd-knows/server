"""Controlled Comms HTTP/lifecycle tests. No account or handset evidence."""

import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import httpx

from tbd_server.messaging.adapter import Comms
from tbd_server.messaging.runtime import DeliveryLoop
from tbd_server.messaging.service import Messaging
from tbd_server.user.store import Problem
from test_messaging_adapter import inbound


def service():
    return Messaging(
        SimpleNamespace(database_url="fake", connections=None),
        "fake-key",
        "fake-secret",
        "+12125550100",
        "2026-11-08T00:00:00Z",
        pilot_conversations={"conversation_fake": "+12125550101"},
    )


def test_webhook_signed_native_message_only_then_storage():
    messaging = service()
    messaging.receive = Mock()
    app = FastAPI()

    @app.exception_handler(Problem)
    async def problem(request, exc):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status)

    app.add_api_route("/webhooks/comms", messaging.webhook, methods=["POST"])
    with TestClient(app) as client:
        raw = json.dumps({"event": "comms.message.received", "data": {"message": inbound()}}).encode()
        assert client.post("/webhooks/comms", content=raw).status_code == 401
        sig = "sha256=" + hmac.new(b"fake-secret", raw, hashlib.sha256).hexdigest()
        assert client.post("/webhooks/comms", content=raw, headers={"x-osis-signature": sig}).status_code == 200
        assert messaging.receive.call_args.args[0].native_id == "msg_fake"
        assert client.post("/webhooks/comms", content=b"x" * (512 * 1024 + 1)).status_code == 413
    messaging.adapter.close()


def test_history_activation_cap_and_oldest_first(caplog):
    records = [inbound(id="new"), inbound(id="old")]

    def handler(request):
        assert request.url.params["since"] == "2026-11-08T00:00:00+00:00"
        assert request.url.params["direction"] == "inbound"
        assert request.url.params["limit"] == "200"
        return httpx.Response(200, json={"messages": records})

    messaging = service()
    messaging.adapter.close()
    messaging.adapter = Comms("fake", transport=httpx.MockTransport(handler))
    messaging.receive = Mock()
    messaging.history_turn(messaging.loop)
    assert [c.args[0].native_id for c in messaging.receive.call_args_list] == ["old", "new"]
    assert all(c.args[0].history for c in messaging.receive.call_args_list)
    messaging.next_sweep = 0
    records[:] = [inbound(id=str(n)) for n in range(200)]
    with caplog.at_level("INFO", logger="tbd.messaging"):
        messaging.history_turn(messaging.loop)
    assert "history_coverage_capped" in caplog.text
    messaging.adapter.close()


def test_waiting_loop_healthy_stopped_or_stalled_unhealthy():
    loop = DeliveryLoop("fake", Mock(), "+12125550100", Mock())
    loop.thread = SimpleNamespace(is_alive=lambda: True)
    assert loop.healthy()
    loop.last_tick -= 46
    assert not loop.healthy()
    loop.thread = None
    assert not loop.healthy()
