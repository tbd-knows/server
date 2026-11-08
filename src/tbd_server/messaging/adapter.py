"""Comms by Osis boundary. Wire mappings require Free-account fixture validation."""

import asyncio
from dataclasses import dataclass
from datetime import datetime
import hashlib
import hmac
import json
import math
import re

import httpx

from tbd_server.user.store import Problem
from .core import ReceivedMessage, utf8_size


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
    if not hmac.compare_digest(signature.lower().encode(), expected.encode()):
        raise Problem(401, "Invalid webhook signature")
    try:
        event = json.loads(body)
    except (ValueError, UnicodeError, RecursionError):
        raise Problem(422, "Invalid webhook JSON") from None
    if not isinstance(event, dict) or not isinstance(event.get("event"), str):
        raise Problem(422, "Invalid webhook event")
    return event


def retry_after(response):
    try:
        value = response.json().get("retry_after", 60)
        if not isinstance(value, bool) and 0 < float(value) and math.isfinite(float(value)):
            return float(value)
    except (ValueError, TypeError, AttributeError):
        pass
    return 60.0


class Comms:
    def __init__(self, api_key, *, transport=None):
        self.production = transport is None
        self.client = httpx.Client(
            base_url="https://osis.co/api/v1/comms/",
            headers={"Authorization": "Bearer " + api_key},
            timeout=10,
            follow_redirects=False,
            transport=transport or httpx.HTTPTransport(retries=0),
        )

    def request(self, method, path, **kwargs):
        if not self.production:
            return self.client.request(method, path, **kwargs)

        async def bounded():
            async with asyncio.timeout(10):
                async with httpx.AsyncClient(
                    base_url=str(self.client.base_url),
                    headers=self.client.headers,
                    timeout=10,
                    follow_redirects=False,
                    transport=httpx.AsyncHTTPTransport(retries=0),
                ) as client:
                    return await client.request(method, path, **kwargs)

        try:
            return asyncio.run(bounded())
        except TimeoutError:
            raise httpx.ReadTimeout("Provider deadline") from None

    def close(self):
        self.client.close()

    @staticmethod
    def normalize(data, account, *, history=False, pilot_conversations=None):
        if not isinstance(data, dict):
            raise Problem(422, "Invalid message record")
        for key in ("direction", "channel", "line_e164"):
            if not isinstance(data.get(key), str):
                raise Problem(422, "Missing message classification")
        if (data["direction"], data["channel"], data["line_e164"]) != ("inbound", "imessage", account):
            return None
        if data.get("unsent_at"):
            return None
        # A reaction collection on an original message is not a new reaction.
        # Explicit event records, if returned in history, must never become texts.
        if "event" in data and data["event"] != "comms.message.received":
            return None
        if "audience" in data:
            if not isinstance(data["audience"], str):
                raise Problem(422, "Invalid audience")
            if data["audience"] != "direct":
                return None
        author = data.get("author")
        if not isinstance(author, dict) or not isinstance(author.get("external_id"), str):
            raise Problem(422, "Sender identity missing")
        peer = author["external_id"]
        if not re.fullmatch(r"\+[1-9][0-9]{7,14}", peer) or peer == account:
            return None
        attachments = data.get("attachments")
        if "attachments" in data and not isinstance(attachments, list):
            raise Problem(422, "Invalid attachment classification")
        if pilot_conversations is not None:
            conversation = data.get("conversation_id")
            if not isinstance(conversation, str) or pilot_conversations.get(conversation) != peer:
                return None
            # Pilot approval covers one-to-one plain text only. Positive media or
            # excluded-event evidence always overrides the missing-field exception.
            if attachments:
                return None
            if not isinstance(data.get("body"), str) or not data["body"]:
                raise Problem(422, "Pilot requires nonempty text")
        elif "audience" not in data or "attachments" not in data:
            raise Problem(422, "Missing message classification")
        # No downloads, URLs, transcripts or display names enter the core.
        try:
            source = datetime.fromisoformat(data["created_at"].replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError, AttributeError):
            raise Problem(422, "Original message time missing") from None
        return ReceivedMessage(
            data.get("id"),
            account,
            peer,
            data.get("body"),
            bool(attachments),
            source,
            data.get("conversation_id"),
            history=history,
        )

    @staticmethod
    def classify(data, job):
        if not isinstance(data, dict):
            return SendResult("uncertain", job.get("provider_message_id"), "invalid_response")
        native_id = data.get("id")
        try:
            valid_id = isinstance(native_id, str) and 0 < utf8_size(native_id) <= 256
        except Problem:
            valid_id = False
        if (
            not valid_id
            or data.get("conversation_id") != job["conversation_id"]
            or data.get("line_e164") != job["account_ref"]
            or data.get("direction") != "outbound"
            or data.get("channel") != "imessage"
            or data.get("body") != job["text"]
            or (job.get("provider_message_id") and native_id != job["provider_message_id"])
        ):
            return SendResult("uncertain", job.get("provider_message_id"), "identity_mismatch")
        status = data.get("status")
        if status in ("sent", "delivered", "read"):
            return SendResult("sent", native_id, observed_status=status)
        if status in ("failed", "suppressed"):
            return SendResult("failed", native_id, "provider_failed", observed_status=status)
        if status == "submitted":
            return SendResult("provider_queued", native_id, observed_status=status)
        return SendResult("uncertain", native_id, "unknown_status", observed_status="unknown")

    def send(self, job):
        try:
            response = self.request(
                "POST",
                "messages",
                headers={"Idempotency-Key": str(job["id"])},
                json={"conversation_id": job["conversation_id"], "body": job["text"], "channel": "imessage"},
            )
        except httpx.HTTPError:
            return SendResult("pending", error_code="transport_unknown")
        if response.status_code == 429:
            return SendResult("pending", error_code="rate_limited", retry_after=retry_after(response))
        try:
            data = response.json()
        except ValueError:
            return SendResult("pending", error_code="invalid_response")
        if response.status_code in (200, 202):
            return self.classify(data.get("message") if isinstance(data, dict) else None, job)
        if response.status_code == 402:
            return SendResult("pending", error_code="quota", retry_after=3600)
        if response.status_code in (401, 403):
            return SendResult("pending", error_code="account_configuration", retry_after=60)
        code = data.get("code") or data.get("error") if isinstance(data, dict) else None
        if code in ("line_mismatch", "conversation_line_mismatch", "line_channel_unsupported"):
            return SendResult("pending", error_code="account_configuration", retry_after=60)
        if response.status_code >= 500 or code in ("idempotency_in_progress", "reply_target_unavailable"):
            return SendResult("pending", error_code="provider_retry")
        return SendResult("failed", error_code="request_rejected")

    def history(self, *, since=None, conversation=None, direction="inbound"):
        params = {"direction": direction, "limit": 200}
        if since is not None:
            params["since"] = since
        if conversation is not None:
            params["conversation_id"] = conversation
        response = self.request("GET", "messages", params=params)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get("messages"), list):
            raise ValueError("Invalid history")
        return data["messages"]

    def status(self, job):
        try:
            records = self.history(conversation=job["conversation_id"], direction="outbound")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403, 429):
                return SendResult(
                    "uncertain",
                    job["provider_message_id"],
                    "rate_limited" if exc.response.status_code == 429 else "account_configuration",
                    retry_after(exc.response) if exc.response.status_code == 429 else 60,
                )
            return SendResult("uncertain", job["provider_message_id"], "read_unavailable")
        except (httpx.HTTPError, ValueError):
            return SendResult("uncertain", job["provider_message_id"], "read_unavailable")
        for record in records:
            if isinstance(record, dict) and record.get("id") == job["provider_message_id"]:
                return self.classify(record, job)
        return SendResult("uncertain", job["provider_message_id"], "history_missing")
