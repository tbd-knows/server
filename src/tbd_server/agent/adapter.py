"""One stateless OpenAI request per persisted dispatch grant."""

import asyncio
from dataclasses import dataclass

import httpx

from .harness import INSTRUCTIONS, Result


@dataclass(frozen=True)
class Generation:
    output: str | None = None
    usage: dict | None = None
    retryable: bool = False
    reason: str | None = None


class OpenAI:
    def __init__(self, api_key):
        self.api_key = api_key

    async def __call__(self, model, prompt, max_output, timeout):
        try:
            async with asyncio.timeout(timeout):
                async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(retries=0), trust_env=False) as client:
                    response = await client.post(
                        "https://api.openai.com/v1/responses",
                        headers={"Authorization": "Bearer " + self.api_key},
                        json={
                            "model": model,
                            "instructions": INSTRUCTIONS,
                            "input": prompt,
                            "store": False,
                            "max_output_tokens": max_output,
                            "text": {
                                "format": {
                                    "type": "json_schema",
                                    "name": "commerce_reply",
                                    "strict": True,
                                    "schema": Result.model_json_schema(),
                                }
                            },
                        },
                    )
        except TimeoutError:
            return Generation(retryable=True, reason="provider_timeout")
        except httpx.TransportError:
            return Generation(retryable=True, reason="provider_transport")
        if response.status_code in (408, 409, 429) or response.status_code >= 500:
            return Generation(retryable=True, reason="provider_retryable_http")
        if response.status_code != 200:
            return Generation(reason="provider_rejected_http")
        try:
            data = response.json()
        except (ValueError, UnicodeError, RecursionError):
            return Generation(reason="provider_unreadable")
        if not isinstance(data, dict):
            return Generation(reason="provider_unreadable")

        usage = data.get("usage")
        counts = (
            {
                key: usage[key]
                for key in ("input_tokens", "output_tokens", "total_tokens")
                if type(usage.get(key)) is int and usage[key] >= 0
            }
            if isinstance(usage, dict)
            else {}
        )
        if isinstance(usage, dict) and isinstance(usage.get("input_tokens_details"), dict):
            cached = usage["input_tokens_details"].get("cached_tokens")
            if type(cached) is int and cached >= 0:
                counts["cached_tokens"] = cached
        counts = counts or None
        status = data.get("status")
        if status == "incomplete":
            details = data.get("incomplete_details")
            detail = details.get("reason") if isinstance(details, dict) else None
            known = {"max_output_tokens", "content_filter"}
            reason = (
                f"provider_incomplete_{detail}"
                if isinstance(detail, str) and detail in known
                else "provider_incomplete"
            )
            return Generation(usage=counts, reason=reason)
        if status not in ("completed", "failed", "cancelled", "queued", "in_progress"):
            return Generation(usage=counts, reason="provider_unreadable")
        if status != "completed":
            return Generation(usage=counts, reason="provider_not_completed")

        try:
            parts = [part for item in data["output"] if item["type"] == "message" for part in item["content"]]
            if any(part["type"] == "refusal" for part in parts):
                return Generation(usage=counts, reason="provider_refusal")
            if len(parts) == 1 and parts[0]["type"] == "output_text":
                output = parts[0]["text"]
                if isinstance(output, str) and output.strip():
                    return Generation(output=output, usage=counts)
        except (TypeError, KeyError):
            pass
        return Generation(usage=counts, reason="provider_unreadable")
