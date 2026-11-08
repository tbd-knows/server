"""Capture setup recovery uses fake HTTP only, never a real account."""

import json
import sys

import httpx
import pytest

from scripts import comms_setup


def test_setup_reuses_registered_webhook_and_recovers_secret(tmp_path, monkeypatch):
    url = "https://capture.example.test/webhooks/comms"
    monkeypatch.setenv("COMMS_CAPTURE_DIR", str(tmp_path))
    monkeypatch.setenv("COMMS_SETUP_KEY", "fake-setup-key")
    monkeypatch.setattr(sys, "argv", ["comms_setup", "register", url])
    calls = []

    def handler(request):
        calls.append(request.method)
        assert request.headers["authorization"] == "Bearer fake-setup-key"
        return httpx.Response(
            200, json={"webhooks": [{"id": "fake-hook", "url": url, "active": True, "secret": "fake-secret"}]}
        )

    client = httpx.Client
    monkeypatch.setattr(comms_setup.httpx, "Client", lambda **kw: client(**kw, transport=httpx.MockTransport(handler)))
    comms_setup.main()
    assert calls == ["GET"]
    assert (tmp_path / "webhook-secret").read_text() == "fake-secret"
    assert (tmp_path / "webhook-secret").stat().st_mode & 0o777 == 0o600


def test_setup_refuses_replacement_while_old_webhook_is_active(tmp_path, monkeypatch):
    previous = {"id": "fake-old", "url": "https://old.example.test/webhooks/comms"}
    (tmp_path / "registration.private.json").write_text(json.dumps(previous))
    (tmp_path / "webhook-secret").write_text("preserve-fake-secret")
    monkeypatch.setenv("COMMS_CAPTURE_DIR", str(tmp_path))
    monkeypatch.setenv("COMMS_SETUP_KEY", "fake-setup-key")
    monkeypatch.setattr(sys, "argv", ["comms_setup", "register", "https://new.example.test/webhooks/comms"])

    def handler(request):
        assert request.method == "GET"
        return httpx.Response(200, json={"webhooks": [{**previous, "active": True}]})

    client = httpx.Client
    monkeypatch.setattr(comms_setup.httpx, "Client", lambda **kw: client(**kw, transport=httpx.MockTransport(handler)))
    with pytest.raises(SystemExit, match="Disable the old"):
        comms_setup.main()
    assert (tmp_path / "webhook-secret").read_text() == "preserve-fake-secret"
