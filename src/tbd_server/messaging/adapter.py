"""Texting Blue wire boundary. Never log provider payloads or request URLs."""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import hmac
import json
from urllib.parse import quote

import httpx

from tbd_server.user.store import Problem


@dataclass(frozen=True)
class SendResult:
    state: str
    provider_id: str | None = None
    error_code: str | None = None
    retry_after: float | None = None
    observed_status: str | None = None


def signed_event(body: bytes, signature: str, secret: str) -> dict:
    if len(body) > 512 * 1024:
        raise Problem(413, "Webhook body too large")
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature.encode(), expected.encode()):
        raise Problem(401, "Invalid webhook signature")
    try:
        event = json.loads(body)
    except (ValueError, UnicodeError, RecursionError):
        raise Problem(422, "Invalid webhook JSON") from None
    if not isinstance(event, dict) or not isinstance(event.get("type"), str):
        raise Problem(422, "Invalid webhook event")
    return event


def retry_after(response: httpx.Response) -> float:
    # Provider documents seconds. Bound invalid/non-finite values to the default.
    try:
        value = float(response.headers.get("retry-after", "30"))
        if 0 < value < float("inf"):
            return value
    except ValueError:
        try:
            until = parsedate_to_datetime(response.headers.get("retry-after", ""))
            value = (until - datetime.now(timezone.utc)).total_seconds()
            if value > 0:
                return value
        except (TypeError, ValueError, OverflowError):
            pass
    return 30.0


class TextingBlue:
    def __init__(self, api_key: str, *, transport=None):
        self.production = transport is None
        self.client = httpx.Client(
            base_url="https://api.texting.blue",
            headers={"x-api-key": api_key},
            timeout=10,
            follow_redirects=False,
            transport=transport if transport is not None else httpx.HTTPTransport(retries=0),
        )

    def request(self, method, path, **kwargs):
        if not self.production:
            return self.client.request(method, path, **kwargs)

        async def bounded_request():
            # A total deadline is required: HTTPX phase timeouts alone do not
            # bound a slow multi-phase response to ten seconds.
            async with asyncio.timeout(10):
                async with httpx.AsyncClient(
                    base_url="https://api.texting.blue",
                    headers=self.client.headers,
                    timeout=10,
                    follow_redirects=False,
                    transport=httpx.AsyncHTTPTransport(retries=0),
                ) as client:
                    return await client.request(method, path, **kwargs)

        try:
            return asyncio.run(bounded_request())
        except TimeoutError:
            raise httpx.ReadTimeout("Provider request deadline") from None

    def close(self):
        self.client.close()

    @staticmethod
    def classify(response, account, peer, text, *, known_id=None):
        # Only explicit non-acceptance permits another POST. 5xx/redirects are ambiguous.
        if response.status_code == 429:
            return SendResult("pending", known_id, "rate_limited", retry_after(response))
        if response.status_code == 402:
            return SendResult("pending", known_id, "quota", 3600)
        if response.status_code in (401, 403):
            return SendResult("pending", known_id, "account_configuration", 30)
        if response.status_code in (400, 422) and known_id is None:
            return SendResult("failed", error_code="invalid_request")
        expected_status = 200 if known_id else 202
        if response.status_code != expected_status:
            return SendResult("uncertain", known_id, "unknown_outcome")
        try:
            data = response.json()
        except ValueError:
            return SendResult("uncertain", known_id, "invalid_response")
        if not isinstance(data, dict):
            return SendResult("uncertain", known_id, "invalid_response")
        native_id = data.get("id")
        if (
            not isinstance(native_id, str)
            or not 0 < len(native_id.encode()) <= 256
            or (known_id is not None and native_id != known_id)
            or data.get("from") != account
            or data.get("to") != peer
            or data.get("content") != text
        ):
            return SendResult("uncertain", known_id, "identity_mismatch")
        status = data.get("status")
        if status in ("sent", "delivered") and known_id:
            return SendResult("sent", native_id, observed_status=status)
        if status == "failed" and known_id:
            return SendResult("failed", native_id, "provider_failed", observed_status=status)
        if status in ("queued", "polling"):
            return SendResult("provider_queued", native_id, observed_status=status)
        return SendResult("uncertain", native_id, "unknown_status")

    def send(self, account, peer, text):
        try:
            response = self.request("POST", "/v1/messages/send", json={"from": account, "to": peer, "content": text})
        except httpx.HTTPError:
            return SendResult("uncertain", error_code="transport_unknown")
        return self.classify(response, account, peer, text)

    def status(self, native_id, account, peer, text):
        try:
            response = self.request("GET", "/v1/messages/" + quote(native_id, safe=""))
        except httpx.HTTPError:
            return SendResult("uncertain", native_id, "read_unavailable")
        result = self.classify(response, account, peer, text, known_id=native_id)
        # A failed read never turns a previously accepted job into a new send.
        if result.state == "pending":
            return SendResult("uncertain", native_id, result.error_code, result.retry_after)
        return result

    def history(self, account, cursor=None):
        params = {"direction": "inbound", "to": account, "limit": 100}
        if cursor is not None:
            params["cursor"] = cursor
        response = self.request("GET", "/v1/messages", params=params)
        response.raise_for_status()
        page = response.json()
        if (
            not isinstance(page, dict)
            or not isinstance(page.get("messages"), list)
            or not isinstance(page.get("has_more"), bool)
        ):
            raise ValueError("Invalid history page")
        return page

    @staticmethod
    def normalize(data, account):
        """Fail closed until real fixtures establish direct-content metadata.

        Published payloads do not establish group/reaction/edit classification.
        No invented provider fields or missing-field defaults are accepted here.
        """
        if not isinstance(data, dict):
            raise Problem(422, "Invalid message record")
        if data.get("to") != account:
            return None
        # This is an explicit integration blocker, not silent message loss.
        raise Problem(422, "Provider direct-message classification not yet verified")

    def number_status(self, account):
        response = self.request("GET", "/v1/numbers")
        response.raise_for_status()
        data = response.json()
        for number in data["numbers"]:
            if number.get("phone_number") == account:
                return number
        raise ValueError("Configured number missing")
