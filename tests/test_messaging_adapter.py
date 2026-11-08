"""Controlled provider HTTP only: these tests cannot reach a live account."""

import hashlib
import hmac

import httpx
import pytest

from tbd_server.messaging.adapter import TextingBlue, signed_event
from tbd_server.user.store import Problem

ACCOUNT, PEER, TEXT = "+12125550100", "+12125550101", "Received your text."


def test_raw_signature_and_nested_identity():
    body = b'{"type":"message.received","id":"envelope","data":{"id":"native"}}'
    signature = "sha256=" + hmac.new(b"fake-secret", body, hashlib.sha256).hexdigest()
    assert signed_event(body, signature, "fake-secret")["data"]["id"] == "native"
    with pytest.raises(Problem) as error:
        signed_event(body + b" ", signature, "fake-secret")
    assert error.value.status == 401
    with pytest.raises(Problem) as error:
        signed_event(b"x" * (512 * 1024 + 1), signature, "fake-secret")
    assert error.value.status == 413


@pytest.mark.parametrize(
    "code,state,delay",
    [
        (202, "provider_queued", None),
        (302, "uncertain", None),
        (500, "uncertain", None),
        (429, "pending", 30),
        (402, "pending", 3600),
        (401, "pending", 30),
        (403, "pending", 30),
        (422, "failed", None),
    ],
)
def test_send_outcomes(code, state, delay):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            code,
            json={"id": "job-1", "status": "queued", "from": ACCOUNT, "to": PEER, "content": TEXT},
            headers={"Location": "https://example.test"},
        )

    adapter = TextingBlue("fake-key", transport=httpx.MockTransport(handler))
    try:
        result = adapter.send(ACCOUNT, PEER, TEXT)
        assert (result.state, result.retry_after) == (state, delay)
        assert len(requests) == 1
        assert requests[0].headers["x-api-key"] == "fake-key"
    finally:
        adapter.close()


def test_timeout_is_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("fake timeout")

    adapter = TextingBlue("fake", transport=httpx.MockTransport(handler))
    assert adapter.send(ACCOUNT, PEER, TEXT).state == "uncertain"
    assert len(calls) == 1
    adapter.close()


@pytest.mark.parametrize(
    "status,state",
    [
        ("queued", "provider_queued"),
        ("sent", "sent"),
        ("delivered", "sent"),
        ("failed", "failed"),
        ("unknown", "uncertain"),
    ],
)
def test_known_job_reads(status, state):
    def handler(request):
        assert request.method == "GET"
        return httpx.Response(200, json={"id": "job-1", "status": status, "from": ACCOUNT, "to": PEER, "content": TEXT})

    adapter = TextingBlue("fake", transport=httpx.MockTransport(handler))
    assert adapter.status("job-1", ACCOUNT, PEER, TEXT).state == state
    assert adapter.status("different-job", ACCOUNT, PEER, TEXT).state == "uncertain"
    adapter.close()


def test_missing_known_job_never_becomes_sendable():
    adapter = TextingBlue("fake", transport=httpx.MockTransport(lambda _: httpx.Response(404)))
    result = adapter.status("job-1", ACCOUNT, PEER, TEXT)
    assert (result.state, result.provider_id) == ("uncertain", "job-1")
    adapter.close()


def test_total_deadline_cancels_without_retry(monkeypatch):
    import asyncio
    from tbd_server.messaging import adapter as module

    calls = []
    timeout = asyncio.timeout
    monkeypatch.setattr(module.asyncio, "timeout", lambda seconds: timeout(0.001))

    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def request(self, *args, **kwargs):
            calls.append(args)
            await asyncio.sleep(1)

    monkeypatch.setattr(module.httpx, "AsyncClient", FakeClient)
    adapter = TextingBlue("fake")
    try:
        result = adapter.send(ACCOUNT, PEER, TEXT)
        assert result.state == "uncertain"
        assert result.error_code == "transport_unknown"
        assert len(calls) == 1
    finally:
        adapter.close()
