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
        except (TimeoutError, httpx.TransportError):
            return Generation(retryable=True)
        if response.status_code in (408, 409, 429) or response.status_code >= 500:
            return Generation(retryable=True)
        if response.status_code != 200:
            return Generation()
        try:
            data = response.json()
            usage = data.get("usage")
            counts = (
                {
                    key: usage[key]
                    for key in ("input_tokens", "output_tokens", "total_tokens")
                    if type(usage.get(key)) is int and usage[key] >= 0
                }
                if isinstance(usage, dict)
                else None
            )
            if counts is not None and isinstance(usage.get("input_tokens_details"), dict):
                cached = usage["input_tokens_details"].get("cached_tokens")
                if type(cached) is int and cached >= 0:
                    counts["cached_tokens"] = cached
            if data.get("status") != "completed":
                return Generation(usage=counts or None)
            parts = [part for item in data["output"] if item["type"] == "message" for part in item["content"]]
            output = parts[0]["text"] if len(parts) == 1 and parts[0]["type"] == "output_text" else None
            return Generation(output, counts or None)
        except (ValueError, TypeError, KeyError, IndexError, AttributeError):
            return Generation()
