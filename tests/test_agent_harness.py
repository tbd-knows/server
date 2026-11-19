"""Synthetic context and authority checks with no provider egress."""

from datetime import datetime, timedelta, timezone
import json
from unittest.mock import Mock
from uuid import uuid4

import pytest

from tbd_server.agent import harness


def turn(text):
    now = datetime.now(timezone.utc)
    return {
        "id": uuid4(),
        "text": text,
        "source_at": now,
        "created_at": now,
        "queue_sequence": 3,
        "user_id": uuid4(),
        "account_ref": "+12125550100",
        "peer_ref": "+12125550101",
        "conversation_id": "example",
        "enrollment_id": uuid4(),
    }


def state(inbound):
    return {
        "brief": None,
        "preferences": {},
        "paused": False,
        "enrolled_at": inbound["source_at"] - timedelta(days=1),
        "watermark": None,
        "reset_source_at": None,
        "reset_sequence": None,
    }


def generated(reply="Here's what fits.", brief="unchanged", edits=(), question=None):
    return json.dumps({"reply": reply, "brief": brief, "edits": list(edits), "question": question})


def test_explicit_preference_writes_are_scoped_and_pause_resets_context():
    inbound = turn("Remember size for shoes for me is 8 wide")
    context = state(inbound)
    assert harness.memory_control(inbound["text"], context, inbound) == "I saved that standing preference."
    assert context["preferences"]["shoes|me|size"]["value"] == "8 wide"
    assert harness.memory_control("My seller said “forget size for shoes for me”", context, inbound) is None
    no_match = harness.memory_control("forget material for hat for me; compare these", context, inbound)
    assert "no saved preference" in no_match and "resend" in no_match
    assert context["reset_sequence"] == inbound["queue_sequence"]
    assert not harness.after_reset(inbound, context)
    assert harness.memory_control("pause memory", context, inbound) == "Preference memory is paused."
    assert "paused" in harness.memory_control("save size for shoes for me is 9", context, inbound)
    assert "8 wide" in harness.memory_control("show my preferences", context, inbound)
    harness.memory_control("forget size for shoes for me", context, inbound)
    assert not context["preferences"]


@pytest.mark.parametrize("source_shift,created_shift,expected", [(5, 5, True), (1, 5, False), (5, -1, False)])
def test_both_times_must_show_reply_before_typed_answer(source_shift, created_shift, expected):
    inbound = turn("9")
    active = state(inbound)
    active["brief"] = {"id": "opening", "fields": {"item": {"value": "shoes"}}, "options": []}
    parent = {
        "text": "Compare shoes",
        "outcome": "model",
        "source_at": inbound["source_at"] - timedelta(minutes=1),
        "queue_sequence": 2,
        "reply": "What size should I use?",
        "question": {"brief_id": "opening", "field": "size", "value": None},
        "sent_observed_at": inbound["source_at"],
    }
    inbound["source_at"] += timedelta(seconds=source_shift)
    inbound["created_at"] += timedelta(seconds=created_shift)
    cur = Mock()
    cur.execute.return_value.fetchall.return_value = [parent]
    prompt, _, answer = harness.context(cur, inbound, active)
    assert bool(prompt) == expected
    assert answer == ({"size": "9"} if expected else None)


def test_intervening_turn_blocks_a_previously_visible_question():
    inbound = turn("yes")
    active = state(inbound)
    active["brief"] = {"id": "opening", "fields": {"item": {"value": "shoes"}}, "options": []}
    parent = {
        "text": "Compare shoes",
        "outcome": "model",
        "source_at": inbound["source_at"] - timedelta(minutes=1),
        "queue_sequence": 1,
        "reply": "Is 8 the right size?",
        "question": {"brief_id": "opening", "field": "size", "value": "8"},
        "sent_observed_at": inbound["source_at"] - timedelta(seconds=5),
    }
    cur = Mock()
    cur.execute.return_value.fetchall.return_value = [{**parent, "outcome": "control", "queue_sequence": 2}, parent]
    assert harness.context(cur, inbound, active)[0] is None


@pytest.mark.parametrize(
    "text",
    [
        "My CVV is 123, remember size for shoes for me is 8",
        "password: fictional-secret",
        "otp 123456",
        "4111 1111 1111 1111",
        "api key=example",
    ],
)
def test_recognized_secrets_block_whole_text(text):
    assert harness.SECRET.search(text)


def test_full_prompt_budget_and_optional_context_trimming():
    inbound = turn("Compare these shoes")
    cur = Mock()
    cur.execute.return_value.fetchall.return_value = [
        {
            "text": "Z" * 20000,
            "outcome": "fallback",
            "source_at": inbound["source_at"] - timedelta(seconds=5),
            "queue_sequence": 1,
            "sent_observed_at": None,
            "reply": "Exclude me",
        }
    ]
    prompt, _, _ = harness.context(cur, inbound, state(inbound))
    assert json.loads(prompt)["recent"] == []
    inbound["text"] *= harness.PROMPT_BYTES
    assert harness.context(cur, inbound, state(inbound))[0] is None


def test_scoped_preferences_and_gift_exception():
    inbound = turn("Compare these shoes for my sister")
    active = state(inbound)
    active["brief"] = {"id": "opening", "fields": {"item": {"value": "shoes"}}, "options": []}
    active["watermark"] = inbound["source_at"] - timedelta(minutes=2)
    active["preferences"] = {
        "shoes|me|size": {"value": "8"},
        "shoes|sister|material": {"value": "cotton"},
        "hats|sister|size": {"value": "small"},
    }
    cur = Mock()
    cur.execute.return_value.fetchall.return_value = []
    inbound["text"] += " and tell me what fits"
    prompt, _, _ = harness.context(cur, inbound, active)
    assert json.loads(prompt)["brief"] is None
    assert json.loads(prompt)["preferences"] == [
        {"item": "shoes", "recipient": "sister", "key": "material", "value": "cotton"},
    ]
    active["brief"]["fields"]["recipient"] = {"value": "my sister"}
    prompt, _, _ = harness.context(cur, inbound, active)
    assert json.loads(prompt)["brief"]["id"] == "opening"
    inbound["text"] = "Which shoes would be warmer?"
    prompt, _, _ = harness.context(cur, inbound, active)
    assert [p["recipient"] for p in json.loads(prompt)["preferences"]] == ["sister"]


def test_model_edits_require_current_evidence_and_distinguish_option_price():
    inbound = turn("Compare shoes. Seller A asks $80. My budget is $65.")
    raw = generated(
        brief="update",
        edits=[
            {"field": "item", "value": "shoes"},
            {"field": "option", "value": "Seller A asks $80"},
            {"field": "budget", "value": "$65"},
        ],
        question={"field": "size", "value": None},
    )
    reply, brief, question = harness.validate(raw, inbound, None, None)
    assert "What size" in reply
    assert brief["fields"]["budget"] == {"value": "$65", "source": str(inbound["id"])}
    assert question["brief_id"] == brief["id"]
    for invalid in (
        raw.replace('"$65"', '"$80"'),
        raw.replace("Here's what fits.", "I saved that preference."),
        raw.replace("Here's what fits.", "What is your budget?"),
        raw.replace('"shoes"', '"boots"'),
    ):
        with pytest.raises(harness.INVALID_RESULT):
            harness.validate(invalid, inbound, None, None)

    follow_up = turn("9")
    raw = generated(brief="update", edits=[{"field": "size", "value": "9"}])
    with pytest.raises(harness.INVALID_RESULT):
        harness.validate(raw, follow_up, brief, None)
    _, updated, _ = harness.validate(raw, follow_up, brief, {"size": "9"})
    assert updated["fields"]["size"] == {"value": "9", "source": str(follow_up["id"])}


@pytest.mark.parametrize(
    "status,output_status,retryable,usage",
    [
        (200, "completed", False, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}),
        (200, "incomplete", False, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}),
        (429, None, True, None),
    ],
)
def test_thin_adapter_stateless_request_and_usage(monkeypatch, status, output_status, retryable, usage):
    import asyncio

    import httpx

    from tbd_server.agent import adapter

    def handler(request):
        assert request.url.path == "/v1/responses"
        data = json.loads(request.content)
        assert set(data) == {"model", "instructions", "input", "store", "max_output_tokens", "text"}
        assert data["model"] == "synthetic-model" and data["store"] is False
        assert data["text"]["format"]["strict"] is True
        return httpx.Response(
            status,
            json={
                "status": output_status,
                "usage": usage,
                "output": [{"type": "message", "content": [{"type": "output_text", "text": generated()}]}],
            },
        )

    client = httpx.AsyncClient

    def controlled(**kwargs):
        assert kwargs["trust_env"] is False
        return client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(adapter.httpx, "AsyncClient", controlled)
    result = asyncio.run(adapter.OpenAI("synthetic")("synthetic-model", "synthetic message", 100, 1))
    assert result.retryable is retryable and result.usage == usage
    assert bool(result.output) == (output_status == "completed")
